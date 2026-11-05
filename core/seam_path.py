# SPDX-License-Identifier: GPL-3.0-or-later
"""Seams pulled round the body (TEST, 2026-10-14; no bpy): a seam's two sides drawn together round a body part's axis
instead of straight through it, in the sewing stages (solver cfg seam_paths, solver/newton_vbd.py).

    seam["path"] = {"side": SIDE,              # which side of the body the seam goes round (below), "auto" the default
                    "guide": guide name}       # absent: the guide side a's piece is placed on (an arm, the torso)

SIDE (user 2026-10-14, DECISIONS cloth-sim-seam-path-side-2026-10-14 -- named by the side of the body, not + / - or
"in front / behind", which read differently for a piece sewn to itself and to another): 'auto' (the side away from
the seam's own cloth: the open side of a wrapped sleeve), 'under', 'top', 'front', 'back', 'left', 'right' (the
avatar's own left / right). A seam without "path" is pulled straight as before.

The recipe holds some seams open for a later stage (a sleeve's underarm until the last stage, cfg stitch_delay); a
seam pulled round is closed where its pieces first come in, so those holds are dropped for it (with a straight
pull, closing the underarm early drew it through the arm: the user's raglan sleeve, 2026-10-14).
"""
import numpy as np

SIDES = ('auto', 'under', 'top', 'front', 'back', 'left', 'right')
# world directions (avatar +z up, its front -y, its left +x: core/body_axes._DIRS)
DIRS = {'under': (0, 0, -1), 'top': (0, 0, 1), 'front': (0, -1, 0), 'back': (0, 1, 0), 'left': (1, 0, 0),
        'right': (-1, 0, 0)}


def guide_of(spec, seam):
    """The guide (name, dict) a seam goes round: its path's own, else the one side a's piece is placed on, else side
    b's; (None, None) when none."""
    gs = spec.get('guides') or {}
    want = (seam.get('path') or {}).get('guide')
    if want in gs:
        return want, gs[want]
    for k in ('a', 'b'):
        g = (spec.get('placement') or {}).get(seam[k]['piece'], {}).get('guide')
        if g in gs:
            return g, gs[g]
    return None, None


def path_seams(spec, names):
    """{seam id: (guide dict, side)} of the enabled seams with a path between pieces in names."""
    out = {}
    for sm in spec.get('seams', []):
        if not sm.get('path') or not sm.get('enabled', True):
            continue
        if sm['a']['piece'] not in names or sm['b']['piece'] not in names:
            continue
        _n, g = guide_of(spec, sm)
        if g is None:
            continue
        side = sm['path'].get('side', 'auto')
        out[sm['id']] = (g, side if side in SIDES else 'auto')
    return out


def write_rows(path, spec, names, E_st, E_seam, tacks=None, offs=None):
    """The solver's seam_paths file for the stitches of the seams with a path; the seam ids written (empty: no file).
    tacks ((n, 2) garment vertex indices, spec tacks) between the same two pieces as a seam pulled round go round
    with it: held straight, the raglan sleeve's underarm tacks drew it through the arm (2026-10-14)."""
    ps = path_seams(spec, names)
    if not ps or E_seam is None:
        return []
    sid = np.asarray(E_seam).astype(str)
    E_st = np.asarray(E_st)
    if tacks is not None and len(tacks) and offs is not None:
        pof = lambda v: names[int(np.searchsorted(np.asarray(offs), v, side='right') - 1)]
        tk = np.asarray(tacks)
        tp = [frozenset((pof(a), pof(b))) for a, b in tk]
        smap = {sm['id']: frozenset((sm['a']['piece'], sm['b']['piece'])) for sm in spec.get('seams', [])}
        extra, ex_id = [], []
        for s_id in ps:
            m = [i for i, p_ in enumerate(tp) if p_ == smap.get(s_id)]
            extra += [tk[i] for i in m]
            ex_id += [s_id] * len(m)
            tp = [None if i in m else p_ for i, p_ in enumerate(tp)]
        if extra:
            E_st = np.vstack([E_st, np.asarray(extra)])
            sid = np.r_[sid, np.asarray(ex_id)]
    rows = {k: [] for k in ('pairs', 'o', 'u', 'rg', 'side', 'group')}
    done = []
    for gi, (s_id, (g, side)) in enumerate(ps.items()):
        m = sid == s_id
        if not m.any():
            continue
        R = np.asarray(g['R'], float)
        n = int(m.sum())
        rows['pairs'].append(E_st[m])
        rows['o'].append(np.repeat(np.asarray(g['o'], float)[None] / 1000.0, n, 0))
        rows['u'].append(np.repeat(R[2][None], n, 0))
        rows['rg'].append(np.full(n, float(g['r']) / 1000.0))
        rows['side'].append(np.repeat(np.asarray(DIRS.get(side, (0, 0, 0)), float)[None], n, 0))
        rows['group'].append(np.full(n, gi))
        done.append(s_id)
    if not done:
        return []
    np.savez(path, **{k: np.concatenate(v) for k, v in rows.items()})
    return done


def drop_holds(cfg, spec, ids):
    """The stage config without the recipe's holds (stitch_delay) on the seams pulled round: one by its seam id, or a
    pair of pieces when every enabled seam between them is pulled round."""
    if not ids or not cfg.get('stitch_delay'):
        return
    ids = set(ids)
    by_pair = {}
    for sm in spec.get('seams', []):
        if sm.get('enabled', True):
            by_pair.setdefault(frozenset((sm['a']['piece'], sm['b']['piece'])), []).append(sm['id'])
    keep = []
    for g in cfg['stitch_delay']:
        if g.get('seam') is not None:
            if str(g['seam']) in ids:
                continue
        elif g.get('pieces'):
            all_ = by_pair.get(frozenset(g['pieces']), [])
            if all_ and all(x in ids for x in all_):
                continue
        keep.append(g)
    if keep:
        cfg['stitch_delay'] = keep
    else:
        cfg.pop('stitch_delay', None)


def arc_points(g, A, B, side_dir=None, n=12, r_min=None):
    """Display (and its tests): n+1 points (mm) on the arc from A to B about guide g's axis -- through side_dir's
    side, or, side_dir None, the shorter way. Each row of A, B is one pair; returns (k, n+1, 3)."""
    R = np.asarray(g['R'], float)
    o, u = np.asarray(g['o'], float), R[2]
    A, B = np.atleast_2d(np.asarray(A, float)), np.atleast_2d(np.asarray(B, float))
    da, db = A - o, B - o
    ha, hb = da @ u, db @ u
    pa, pb = da - ha[:, None] * u, db - hb[:, None] * u
    ra, rb = np.linalg.norm(pa, axis=1), np.linalg.norm(pb, axis=1)
    e1 = pa / np.maximum(ra, 1e-9)[:, None]
    e2 = np.cross(u, e1)
    th = np.mod(np.arctan2((pb * e2).sum(1), (pb * e1).sum(1)), 2 * np.pi)
    if side_dir is None:
        dth = np.where(th <= np.pi, th, th - 2 * np.pi)
    else:
        sd = np.asarray(side_dir, float)
        ts = np.mod(np.arctan2(e2 @ sd, e1 @ sd), 2 * np.pi)
        dth = np.where(ts < th, th, th - 2 * np.pi)
    t = np.linspace(0.0, 1.0, n + 1)[None]
    r = ra[:, None] + (rb - ra)[:, None] * t
    if r_min is not None:
        r = np.maximum(r, r_min)
    an = dth[:, None] * t
    h = ha[:, None] + (hb - ha)[:, None] * t
    return (o[None, None] + h[..., None] * u + r[..., None] * (np.cos(an)[..., None] * e1[:, None]
                                                              + np.sin(an)[..., None] * e2[:, None]))
