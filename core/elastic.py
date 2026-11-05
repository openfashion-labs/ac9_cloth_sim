# SPDX-License-Identifier: GPL-3.0-or-later
"""Elastic and shrinkage (S3 ゴム・縮み, 2026-10-13; no bpy): the garment spec's spec['elastic'] turned into the
solver's cfg 'elastic' (solver/newton_vbd.py), one entry per stage.

    "elastic": [{"piece": name, "ratio": float,          # rest length / pattern length along the direction (< 1)
                 "line": internal id | "side": SIDE,      # absent: the whole piece (a waistband, a cuff)
                 "w": mm,                                 # a line's band: w mm each side (default 10)
                 "dir": [dx, dy],                         # absent: along the line, or the piece's long axis
                 "strength": float,                       # the shrunk cloth's stiffness x the fabric's (default 0.3)
                 "enabled": bool}]

A piece's twin (core/spec.twins) takes the same elastic, mirrored in the layout (a twin's line and edge ids need not
match: the reference's do not). The rest length is
shortened along the direction only (across it the cloth keeps its length), composed on top of the easing (cfg ease,
the easing chat's) and anything else that set the rest shape.

When: in the stage where a piece first comes in (not carried from the stage before), it is shortened over RAMP
frames from the pattern's length; in the stages after, it starts shortened. The numbers that chose this are in
2609_Apparel_CAD/docs/testing/RESULT_elastic_2026-10-13.md.
"""
import numpy as np

from . import spec as S

RAMP = 30          # frames over which a piece coming in shortens (see the module docstring)
W_DEFAULT = 10.0   # mm each side of a line
# the elastic's stiffness x the fabric's when the spec gives none: as stiff as the fabric, a waistband held open by the
# body tore its own seam open (pants, ratio 0.85) and at 0.70 pushed 83 vertices into the body; at 0.3 / 0.1 neither
# (seams 900-905 / 908, 0 inside). A free band (a cuff) reaches its ratio at any of them (docs/testing/RESULT_elastic_2026-10-13.md)
STRENGTH = 0.3


def _mirror_map(spec, src, twin, tol=1.0):
    """x -> c - x taking piece src onto its mirrored twin in the layout (core/draft.mirror_copy, and the BOM's twins),
    or None when the mirrored outline does not lie on the twin's (p95 > tol mm): then the twin is left as it is."""
    A = S.Outline(S.piece_index(spec)[src]).B
    B = S.Outline(S.piece_index(spec)[twin]).B
    c = A[:, 0].min() + B[:, 0].max()
    M = np.stack([c - A[:, 0], A[:, 1]], 1)
    d = np.sqrt(((M[:, None, :] - B[None]) ** 2).sum(-1)).min(1)
    return c if float(np.percentile(d, 95)) <= tol else None


def _entry(el, piece, line=None, mirror=None):
    """One solver entry; line: the flat polyline of the source piece, mirror: x -> mirror - x onto this piece."""
    e = {'piece': piece, 'ratio': float(el['ratio'])}
    if line is not None:
        P = np.array(line, float)
        if mirror is not None:
            P[:, 0] = mirror - P[:, 0]
        e['line'] = np.round(P, 3).tolist()
        e['w'] = float(el.get('w', W_DEFAULT))
    if el.get('dir') is not None:
        d = np.asarray(el['dir'], float)
        if mirror is not None:
            d = d * np.array([-1.0, 1.0])            # the twin is the piece mirrored in x
        e['dir'] = d.tolist()
    e['ke_scale'] = float(el.get('strength', STRENGTH))
    return e


def solver_entries(spec, names, notes=None):
    """[entry] for the pieces in `names` (start / frames are added per stage by for_stage). An elastic left out
    (its line gone, or a twin that is not the piece's mirror image) is said in `notes` (a list, appended to)."""
    notes = [] if notes is None else notes
    own = {el['piece'] for el in spec.get('elastic', []) if _whole(el) and el.get('enabled', True)}
    tw = {}
    for a, b in S.twins(spec):
        tw[a], tw[b] = b, a
    out = []
    for el in spec.get('elastic', []):
        if not el.get('enabled', True) or float(el.get('ratio', 1.0)) == 1.0:
            continue
        line = None
        try:
            if el.get('line') is not None:
                line = S.side_polyline(spec, {'piece': el['piece'], 'line': el['line'], 'from': 0.0, 'to': 1.0})
            elif el.get('side') is not None:
                line = S.side_polyline(spec, dict(el['side'], piece=el['piece']))
        except (KeyError, StopIteration):
            # the line is gone (a pattern edit): nothing to shorten, said so (ARCHITECTURE: what the system changes
            # by itself is logged)
            notes.append(f"elastic on {el['piece']} left out: its line "
                         f"{el.get('line') or (el.get('side') or {}).get('from')} is not on the piece")
            continue
        if el['piece'] in names:
            out.append(_entry(el, el['piece'], line))
        t = tw.get(el['piece'])
        if t in own and _whole(el):
            t = None                                # the twin has its own setting
        if t in names:
            c = _mirror_map(spec, el['piece'], t)
            if c is not None:
                out.append(_entry(el, t, line, c))
            else:
                notes.append(f"elastic on {el['piece']} not given to its twin {t}: the twin is not its mirror image "
                             f"in the layout (set the twin's own)")
    return out


def carried_pieces(cfg, k, names, shaped=()):
    """The pieces that start stage k with the shape they had before (so already shortened): in a Dress the ones the
    stage takes from the stage before (cfg init_from_pieces); in a Continue / Live the ones of the result it goes on
    from (`shaped`) in its first stage and every piece after."""
    if k > 0:
        return set(cfg.get('init_from_pieces') or names) if cfg.get('init_from') else set()
    return set(shaped)


def _whole(el):
    return el.get('line') is None and el.get('side') is None


def piece_ratio(spec, name):
    """The whole-piece elastic ratio of piece `name` (its own, else its twin's), None when it has none (the panel)."""
    tw = {}
    for a, b in S.twins(spec):
        tw[a], tw[b] = b, a
    for n in (name, tw.get(name)):
        for el in spec.get('elastic', []):
            if el.get('piece') == n and n is not None and _whole(el) and el.get('enabled', True) \
                    and float(el.get('ratio', 1.0)) != 1.0:
                return float(el['ratio'])
    return None


def for_stage(entries, cfg, carried, ramp=RAMP):
    """The entries for one stage's config: a piece in `carried` starts shortened, a piece coming in shortens over
    `ramp` frames from its pattern length. Pieces not in the stage are left out."""
    active = cfg.get('active_pieces')
    out = []
    for e in entries:
        if active is not None and e['piece'] not in active:
            continue
        out.append(dict(e, start=1, frames=0 if e['piece'] in carried else int(ramp)))
    return out
