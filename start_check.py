# SPDX-License-Identifier: GPL-3.0-or-later
"""Start check: the shapes on screen read when a run is pressed (before it writes into the garment or takes the
placed objects away), and each piece's start compared with them after prepare (core/start_check.py)."""
import json
import os

import numpy as np
from . import result_reference as RR

from .core import start_check as CS


def read_screen(context, spec, g, mode, sources=None):
    """{piece: (source, shape, note)} as core.start_check.compare reads it. mode: the run's ('START' starts every
    piece from its placement, so the garment shown is not its start). sources: a dict filled with what
    core.start_plan needs of each placed piece ({'moved', 'vertex_count', 'made_vertex_count',
    'reset'};
    a mirrored half has its leading object's)."""
    from . import display_state as DS
    from . import placed_view as PLV
    from . import dress as DR
    snap = DS.snapshot(spec, g)
    obs = PLV.objects(spec)
    reset = set(DR.reset_pieces(g)) if g is not None else set()
    out, gar = {}, None
    for n, v in snap.items():
        if not v['solid']:
            continue
        src = v['source']
        if src == 'placed':
            ob = obs.get(n)
            hs = PLV.hand_shape(ob) if ob is not None else None
            if sources is not None and ob is not None and hs is not None:
                sources[n] = {'moved': PLV.is_moved(ob), 'vertex_count': len(hs[0]),
                              'made_vertex_count': ob.get(PLV.NV_PROP), 'reset': n in reset}
            if hs is None:
                out[n] = ('placed', None, "its placed object has no layout")
            elif ob.get(PLV.PROP) == n:
                out[n] = ('placed', hs, "")
            else:                                   # the half a pair's Mirror modifier shows
                out[n] = _mirrored_half(spec, n, ob.get(PLV.PROP), hs)
        elif src == 'result':
            if mode == 'START':
                out[n] = ('rule', None, "started again from the placement")
                continue
            if gar is None:
                gar = _garment(context, g, DR)
            if isinstance(gar, str):
                out[n] = ('result', None, gar)
            elif n in gar[0]:
                a, b = gar[0][n]
                T = gar[2][(gar[2] >= a).all(1) & (gar[2] < b).all(1)] - a
                out[n] = ('result', (gar[1][a:b], T, gar[3][a:b]), "")
            else:
                out[n] = ('result', None, "not found in the garment's mesh")
        elif src == 'proxy':
            # a run without a result to go on from (a Phase Sim from the start, a Live / Local before any result)
            # makes a moved piece's placed object again from its pose (dress.sync_placed); a run going on from a
            # result has no such step, and a hand move not on screen is lost
            pose = spec.get('placement', {}).get(n, {}).get('pose') or {}
            moved = any(abs(float(x)) > 1e-6 for k in ('loc', 'rot') for x in pose.get(k, ()))
            on_result = mode != 'START' and g is not None and bool(DR.job_of(g))
            out[n] = ('rule', None, "", "hand move" if moved and on_result else "")
    return out


def _mirrored_half(spec, n, source, hs):
    """The shape on screen of the half a pair's Mirror modifier shows, in its own terms: its source's layout points
    mirrored into its outline (as a mirrored twin's mesh is laid out, placed_view.mirror_flat), the triangles turned
    to keep their winding, the world positions mirrored in the pair's plane. With its own layout points it is put on
    the run's mesh as any placed piece is (core.shape_source.carry); with its source's, the reader could only take it
    vertex by vertex, and a source made on an older mesh of the same vertex count started the half torn."""
    from .core import spec as S_
    from . import placed_view as PLV
    F_, T_, W_ = hs
    pl_ = S_.pair_plane(spec, n)
    W = S_.mirror_points(W_, pl_, S_.mirror_center(spec, pl_, n))
    pcs = {p['name']: p for p in spec.get('pieces', []) if p['name'] in (n, source)}
    if set(pcs) != {n, source}:
        return ('mirrored', None, "its pair's pattern is missing")
    F = PLV.mirror_flat(F_, S_.Outline(pcs[source]).B, S_.Outline(pcs[n]).B, pl_)
    T = np.asarray(T_, np.int64)[:, [0, 2, 1]] if len(T_) else np.asarray(T_, np.int64)
    return ('mirrored', (F, T, W), "")


def _garment(context, g, DR):
    """(ranges, flat mm, triangles, shown mm) of the dressed garment, or a note why it cannot be read."""
    rng = DR._piece_ranges(g)
    if not rng:
        return "the garment's mesh does not fit its job"
    flat = np.load(os.path.join(DR.job_of(g), RR.INDEX))['flat']
    me = g.data
    me.calc_loop_triangles()
    T = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get('vertices', T)
    return rng, np.asarray(flat, float), T.reshape(-1, 3), DR.shown_shape(context, g)


def check(screen, info, flat, Xinit):
    """Compare, write <job>/start_check.json, return (result, summary line, lines per piece off)."""
    from . import dress as DR
    offs = np.load(os.path.join(info['dir'], RR.INDEX))['offs']
    res = CS.compare(screen, info['names'], flat, offs, Xinit, DR._bary_map_extend)
    head, off = CS.lines(res)
    try:
        json.dump({'tol_mm': CS.TOL_MM, 'pieces': res}, open(os.path.join(info['dir'], 'start_check.json'), 'w',
                                                              encoding='utf-8'), ensure_ascii=False, indent=1)
    except OSError:
        pass
    return res, head, off
