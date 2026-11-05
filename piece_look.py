# SPDX-License-Identifier: GPL-3.0-or-later
"""Each piece's state and the selected pieces, seen on the flat pattern and on the pieces in 3D (placed, or the
garment once it has a result). User 2026-10-14: the selected piece is to be found as the selected seam is (white
rim), and the four states told apart at a glance (Freeze has its own color; a piece turned off
is drawn dashed).

    selected     a white rim (seen through the body and the cloth in 3D, faintly) and a light white tint (in 3D a
                 see-through orange one: the cloth's shading stays)
    Active       as before (nothing added)
    Freeze       a whitish blue tint and a solid light blue rim
    Off Pattern  a dashed light gray rim (the cloth goes through it, its seams still pull); in 3D a light gray tint
    Off All      a dashed dark gray rim (out of the simulation); in 3D a dark tint

Drawn before the seam overlay (overlay.py), so the seams lie on top. In 3D the lines and the tint are moved
LIFT_MM towards the eye and depth tested, as the 3D seams are (a tint on the cloth itself loses the depth test,
KNOWLEDGE reference_post_view_tint_depth)."""
import json
import os

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import overlay
from . import pattern_view as PV
from . import placed_view as PLV
from . import store
from . import view_visibility as VV
from .core import spec as S

SEL = (1.0, 1.0, 1.0, 1.0)
SEL_FILL = (1.0, 1.0, 1.0, 0.16)
# in 3D the gray cloth under a light white tint is hardly told from the rest: a see-through warm tint, as Blender's
# selected objects, that leaves the cloth's light and shade (user 2026-10-24)
SEL_FILL_3D = (1.0, 0.55, 0.15, 0.30)
FREEZE = (0.70, 0.85, 1.0, 1.0)
FREEZE_FILL = (0.80, 0.91, 1.0, 0.45)
OFF_PATTERN = (0.85, 0.85, 0.85, 0.95)
OFF_ALL = (0.40, 0.40, 0.40, 0.90)
# in 3D the rims of the two Off states lie mostly under the seam lines: a tint says it (the flat pattern shows them
# as wire already)
OFF_PATTERN_FILL = (0.92, 0.92, 0.92, 0.22)
OFF_ALL_FILL = (0.10, 0.10, 0.10, 0.45)
DASH_MM, GAP_MM = 8.0, 6.0
LIFT_MM = 5.0

_handles = []
_flat = {'key': None}       # the flat pattern's batches data
_flat_views = {}
_topo = {'key': None}       # the garment's faces and rims per piece (per job and mesh)
_gco = {'key': None}        # the garment's world positions at the frame shown
_b3 = {'key': None}         # the 3D batches data (per positions, view, states and selection)
_g3 = {'key': None}        # the garment's tints and rims before the lift (per shape, states, selection)


def _selected(context):
    """The selected pieces: flat pattern pieces and placed objects (a mirrored pair's object: both its pieces)."""
    out = set()
    from . import piece_move as PM
    owners = set(PV.piece_objects().values()) | set(PLV.objects().values()) | set(PM.handles())
    for ob in owners:
        if context.scene.objects.get(ob.name) != ob:
            continue
        if not ob.select_get(view_layer=context.view_layer):
            continue
        for k in (PV.PIECE_PROP, PLV.PROP, PLV.TWIN_PROP):
            if ob.get(k):
                out.add(ob[k])
    return frozenset(out)


def fabric_colors(spec):
    """{fabric id: rgba} for the garment's fabrics in list order (Overlays > Fabric Colors, the Lists' Fabrics
    swatches). Hues spaced by the golden angle from another start than the seams' (overlay._palette), less
    saturated."""
    import colorsys
    out = {}
    for i, f in enumerate(spec.get('fabrics', [])):
        r, g, b = colorsys.hsv_to_rgb((0.08 + i * 0.61803398875) % 1.0, 0.55, 0.9)
        out[f['id']] = (r, g, b, 1.0)
    return out


FABRIC_ALPHA = 0.40


def _fabric_fills(context, spec):
    """{piece: rgba} the fabric tint of each piece, {} when Fabric Colors is off."""
    props = context.scene.ac9_cloth_sim
    if not props.show_fabric_colors:
        return {}
    from .core import fabric as FA
    cols = fabric_colors(spec)
    out = {}
    for pc in spec['pieces']:
        f = FA.resolved(spec, pc['name'])
        c = cols.get(f['id']) if f else None
        if c is not None:
            out[pc['name']] = (c[0], c[1], c[2], FABRIC_ALPHA)
    return out


def _states(spec):
    return {pc['name']: S.piece_state(pc) for pc in spec['pieces']}


def _pairs(P, closed=True):
    """Segment end points (2k x 3) of a polyline."""
    P = np.asarray(P, float)
    if closed:
        P = np.vstack([P, P[:1]])
    return np.stack([P[:-1], P[1:]], 1).reshape(-1, 3)


def _dashes(P, closed=True):
    """Dashed segments (2k x 3) along a polyline (world m): DASH_MM on, GAP_MM off, round the corners."""
    P = np.asarray(P, float)
    if closed:
        P = np.vstack([P, P[:1]])
    if len(P) < 2:
        return np.zeros((0, 3))
    cum = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    L, d, per = cum[-1], DASH_MM * PV.MM, (DASH_MM + GAP_MM) * PV.MM
    out = []
    s = 0.0
    while s < L:
        e = min(s + d, L)
        t = np.r_[s, cum[(cum > s) & (cum < e)], e]
        Q = np.stack([np.interp(t, cum, P[:, k]) for k in range(3)], 1)
        out.append(np.stack([Q[:-1], Q[1:]], 1).reshape(-1, 3))
        s += per
    return np.vstack(out) if out else np.zeros((0, 3))


def _nonzero(L):
    """Segments (2k x 3) without the zero-length ones: a wide line of zero length is drawn as a long spike across
    the view by the polyline shader (an outline that repeats its first point at the end did)."""
    L = np.asarray(L, float).reshape(-1, 2, 3)
    return L[np.linalg.norm(L[:, 1] - L[:, 0], axis=1) > 1e-7].reshape(-1, 3)


def _rim(name, state, sel, P, closed=True, in3d=False):
    """[(points, color, kind)] of one piece's rim: kind 'sel' (the selection), 'state'."""
    out = _rim_raw(name, state, sel, P, closed, in3d)
    return [(_nonzero(L), c, k) for L, c, k in out]


def _rim_raw(name, state, sel, P, closed=True, in3d=False):
    out = []
    if name in sel:
        out.append(((_dashes if in3d and state in ('off_pattern', 'off_all') else _pairs)(P, closed), SEL, 'sel'))
    if state == 'freeze':
        out.append((_pairs(P, closed), FREEZE, 'state'))
    elif state == 'off_pattern':
        out.append((_dashes(P, closed), OFF_PATTERN, 'state'))
    elif state == 'off_all':
        out.append((_dashes(P, closed), OFF_ALL, 'state'))
    return out


def _fill_color(name, state, sel, in3d=False, fab=None):
    """fab: {piece: rgba} the fabric tints (Fabric Colors on): over the selection's, under Freeze / Off's."""
    if state == 'freeze':
        return FREEZE_FILL
    if in3d and state == 'off_pattern':
        return None
    if in3d and state == 'off_all':
        return None
    if fab and name in fab and state == 'active':      # an Off piece keeps its look (dashed rim, no tint)
        return fab[name]
    if name in sel:
        return SEL_FILL_3D if in3d else SEL_FILL
    return None


def _flat_data(context, spec, outlines):
    """The flat pattern's tints and rims (world m), cached per spec, layout, states and selection."""
    global _flat
    visible = VV.pattern_names(context, spec)
    if visible not in _flat_views:
        if len(_flat_views) >= 16:
            _flat_views.clear()
        _flat_views[visible] = {'key': None}
    _flat = _flat_views[visible]
    M = np.array(PV.layout_matrix(context))
    st, sel = _states(spec), _selected(context)
    fab = _fabric_fills(context, spec)
    key = (store._cache['key'], M.tobytes(), tuple(sorted(st.items())), sel, bool(fab), visible)
    if _flat['key'] == key:
        return _flat
    tri, tcol, lines = [], [], []
    for pc in spec['pieces']:
        n = pc['name']
        if n not in visible:
            continue
        ol = outlines.get(n)
        if ol is None:
            continue
        B = np.asarray(ol.B, float) * PV.MM
        W = (np.c_[B, np.zeros(len(B)), np.ones(len(B))] @ M.T)[:, :3]
        c = _fill_color(n, st[n], sel, fab=fab)
        if c is not None and len(W) >= 3:
            for t in tessellate_polygon([[Vector(p) for p in W]]):
                tri += [W[i] for i in t]
                tcol += [c] * 3
        lines += _rim(n, st[n], sel, W)
    _flat.update(key=key, tri=np.asarray(tri, np.float32).reshape(-1, 3), tcol=tcol, lines=lines)
    return _flat


_ptint = {}         # piece -> (shape key, triangle corners): a placed piece's tint, made again when it moved
_prim = {}          # piece -> (key, rims): a placed piece's rims made again only when it moved or its look changed


def _placed_lines(spec, outlines, st, sel, shown=None, only=None):
    """Before a result: the rims of the placed pieces -- on a piece's placed object as it is now (moved, rotated,
    edited; shown = placed_view.shown), else on its guide (overlay.wrapped_outline). Kept per piece while its shape,
    state and selection stay (a drag redraws every frame)."""
    from . import overlay as OV
    shown = shown or {}
    lines = []
    sk = store._cache['key']
    for pc in spec['pieces']:
        n = pc['name']
        if (st[n] == 'active' and n not in sel) or (only is not None and n not in only):
            continue
        key = (sk, st[n], n in sel, shown[n][4] if n in shown else None)
        hit = _prim.get(n)
        if hit is None or hit[0] != key:
            try:
                if n in shown:
                    P = PLV.to_world(shown[n], outlines[n].B)
                    P = None if P is None else np.vstack([P, P[:1]])
                else:
                    P = OV.wrapped_outline(spec, outlines, n)
            except Exception:
                P = None
            hit = _prim[n] = (key, [] if P is None else _rim(n, st[n], sel, P[:-1], in3d=True))
        lines += hit[1]
    return lines


def _loops(E):
    """Boundary edges (k x 2) chained into vertex loops / open chains."""
    adj = {}
    for a, b in E.tolist():
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, out = set(), []
    for a, b in E.tolist():
        if (a, b) in seen or (b, a) in seen:
            continue
        chain, prev, cur = [a], a, b
        seen.add((a, b))
        while cur != a:
            chain.append(cur)
            nxt = [v for v in adj[cur] if v != prev and (cur, v) not in seen and (v, cur) not in seen]
            if not nxt:
                break
            seen.add((cur, nxt[0]))
            prev, cur = cur, nxt[0]
        out.append((np.asarray(chain), cur == a))
    return out


def _garment_topo(ob, job):
    """Per piece name: its triangles (t x 3 vertex ids) and its rim loops, from the face attribute ac9_piece."""
    me = ob.data
    key = (job, ob.name, len(me.vertices), len(me.polygons))
    if _topo['key'] == key:
        return _topo['pieces']
    pieces = {}
    names = json.loads(ob.get('ac9_piece_names', '[]'))
    pa = me.attributes.get('ac9_piece')
    if names and pa is not None:
        me.calc_loop_triangles()
        tri = np.empty(len(me.loop_triangles) * 3, np.int64)
        me.loop_triangles.foreach_get('vertices', tri)
        tri = tri.reshape(-1, 3)
        poly = np.empty(len(me.loop_triangles), np.int64)
        me.loop_triangles.foreach_get('polygon_index', poly)
        fpc = np.empty(len(me.polygons), np.int64)
        pa.data.foreach_get('value', fpc)
        tp = fpc[poly]
        for i, n in enumerate(names):
            T = tri[tp == i]
            if not len(T):
                continue
            E = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
            u, c = np.unique(E, axis=0, return_counts=True)
            pieces[n] = {'tri': T, 'loops': _loops(u[c == 1])}
    _topo.update(key=key, pieces=pieces)
    return pieces


def _garment_co(context, ob, job):
    """World positions of the garment's vertices at the frame shown (evaluated: the result's frames)."""
    # pieces Reset and hidden on the frozen garment (a Mask): its own vertices, indexed as the pieces are
    # (dress.garment_co: the solver's vertices whatever modifiers a person put on the garment)
    from . import dress as DR
    co = DR.garment_co(context, ob)
    n = len(co)
    sample = co[:: max(1, n // 16)].round(6).tobytes()
    key = (job, n, context.scene.frame_current, np.array(ob.matrix_world).tobytes(), sample)
    if _gco['key'] != key:
        Mw = np.array(ob.matrix_world)
        _gco.update(key=key, co=co.astype(np.float64) @ Mw[:3, :3].T + Mw[:3, 3])
    return _gco['co'], _gco['key']


def _lift(P, rv3d):
    P = np.asarray(P, float)
    Vi = np.array(rv3d.view_matrix.inverted())
    if rv3d.is_perspective:
        d = Vi[:3, 3][None] - P
        d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    else:
        d = np.broadcast_to(Vi[:3, 2], P.shape)
    return P + d * (LIFT_MM * PV.MM)


def _data3d(context, spec, outlines, rv3d):
    """The 3D tints and rims: on the garment's result when there is one, else on the placed pieces. With a result,
    a piece whose placed object is seen (Reset, or folded / moved again) is drawn on that object and a piece the
    garment does not have on its guide; the rest stay on the garment (user 2026-10-22: one placed piece in sight
    put every selected piece's rim on its cylinder, off the dressed shirt)."""
    if not VV.allows_world(context, spec):
        return None
    from . import dress as DR
    from . import display_state as DS
    st, sel = _states(spec), _selected(context)
    fab = _fabric_fills(context, spec)
    ob = bpy.data.objects.get(DR.garment_name(spec))
    display = DS.snapshot(spec, ob)
    visible = VV.world_names(context, spec, display=display)
    if not visible:
        return None
    job = DS.job(ob)
    if job is None and DS.result_names(ob):
        job = 'blend:' + ob.name        # the job folder lost: the tints are read from the garment's mesh all the same
    result_visible = any(n in visible and v['source'] == 'result' for n, v in display.items())
    view = (np.array(rv3d.view_matrix).tobytes(), rv3d.is_perspective)
    if job is None or not result_visible:
        sh, sig = PLV.shown(context, spec, outlines, include_off=True)
        sh = {n: v for n, v in sh.items() if n in visible}
        key = ('placed', store._cache['key'], tuple(sorted(st.items())), sel, view, sig, bool(fab), visible)
        if _b3['key'] != key:
            tri, tcol, lines = _placed_data(spec, outlines, st, sel, sh, fab, rv3d, only=visible)
            T = _lift(np.vstack(tri), rv3d).astype(np.float32) if tri else np.zeros((0, 3), np.float32)
            tcol = np.vstack(tcol) if tcol else []
            _b3.update(key=key, tri=T, tcol=tcol, lines=lines)
        return _b3
    pieces = _garment_topo(ob, job)
    co, ckey = _garment_co(context, ob, job)
    sap = frozenset(n for n, v in display.items() if v['source'] == 'placed')
    sh, sig = PLV.shown(context, spec, outlines, include_off=True) if sap else ({}, None)
    sh = {n: v for n, v in sh.items() if n in sap and n in visible}
    off_g = {n for n, v in display.items() if v['source'] == 'proxy' and n in visible}
    gkey = ('garment', ckey, tuple(sorted(st.items())), sel, store._cache['key'] if fab else None, sig,
            store._cache['key'] if sh else None, visible)
    if _g3['key'] != gkey:
        # the garment's tints and rims as they lie, made again only when the shape, states or selection change;
        # lifted towards the eye per view below (an orbit with a dressed piece picked rebuilt its 56 k tint corners
        # every frame, 158 ms, user 2026-10-22 "heavy after picking a dressed piece")
        tri, tcol, lines = [], [], []
        if result_visible:
            for n, p in pieces.items():
                if n in sh or n not in visible:           # placed elsewhere or excluded in this viewport
                    continue
                s = st.get(n, 'off_all')                 # a piece gone from the spec since: as off
                c = _fill_color(n, s, sel, in3d=True, fab=fab)
                if c is not None:
                    tri.append(co[p['tri'].ravel()])
                    tcol.append(np.broadcast_to(np.asarray(c, np.float32), (p['tri'].size, 4)))
                if s != 'active' or n in sel:
                    for loop, closed in p['loops']:
                        lines += _rim(n, s, sel, co[loop], closed, in3d=True)
        _g3.update(key=gkey, tri=np.vstack(tri) if tri else np.zeros((0, 3)),
                   tcol=np.vstack(tcol).astype(np.float32) if tcol else np.zeros((0, 4), np.float32),
                   lines=[(P, c, k) for P, c, k in lines if len(P)])
    key = (gkey, view)
    if _b3['key'] != key:
        T = _lift(_g3['tri'], rv3d).astype(np.float32) if len(_g3['tri']) else np.zeros((0, 3), np.float32)
        tcol = _g3['tcol']
        lines = [(_lift(P, rv3d).astype(np.float32), c, k) for P, c, k in _g3['lines']]
        if sh or off_g:
            t2, c2, l2 = _placed_data(spec, outlines, st, sel, sh, fab, rv3d, only=set(sh) | off_g)
            lines += l2
            if t2:
                T = np.vstack([T] + [_lift(np.vstack(t2), rv3d).astype(np.float32)])
                tcol = np.vstack([tcol] + [np.vstack(c2)]).astype(np.float32)
        _b3.update(key=key, tri=T, tcol=tcol, lines=lines)
    return _b3


def _placed_data(spec, outlines, st, sel, sh, fab, rv3d, only=None):
    """(triangle corner lists, their colors (k, 4) lists, lifted rims) of the placed pieces (only: these)."""
    lines = [(_lift(P, rv3d).astype(np.float32), c, k)
             for P, c, k in _placed_lines(spec, outlines, st, sel, sh, only=only)]
    # the tints on the placed objects too, as on the result (user 2026-10-14: Freeze / Off were seen on the
    # flat pattern only -- the placed pieces had the rims alone, the Off ones under the seam lines)
    tri, tcol = [], []
    for n, e in sh.items():
        c = _fill_color(n, st.get(n, 'off_all'), sel, in3d=True, fab=fab)
        if c is None or e[2] is None or not len(e[1]):
            continue
        hit = _ptint.get(n)                 # made again only for the piece that moved
        if hit is None or hit[0] != e[4]:
            hit = _ptint[n] = (e[4], np.asarray(e[2], np.float32)[np.asarray(e[1]).ravel()])
        tri.append(hit[1])
        tcol.append(np.broadcast_to(np.asarray(c, np.float32), (len(hit[1]), 4)))
    return tri, tcol, lines


def _tris(sh, d):
    """The tint batch of a data dict (_flat / _b3), made once per its key. The arrays are handed over contiguous:
    batch_for_shader reads a numpy array's memory as it lies, and the garment's colors (C_CONTIGUOUS False) came out
    as opaque white whatever their alpha (user 2026-10-24 "the selected piece is all white, no depth"; measured in
    the GUI: alpha 0.02 to 0.5 gave the same white, a list or a contiguous copy the tint)."""
    if d.get('tb_key') != d['key']:
        d['tb'] = batch_for_shader(sh, 'TRIS', {'pos': np.ascontiguousarray(d['tri'], np.float32),
                                                'color': np.ascontiguousarray(d['tcol'], np.float32)})
        d['tb_key'] = d['key']
    return d['tb']


_lb = {}          # line batches per (lines, kind, alpha, view): rebuilt only when one of them changes


def _draw_lines(sh, lines, kind, width, rv3d, alpha=1.0):
    """The `kind` rims of `lines` as one batch. In perspective, a segment with an end behind the eye is left out:
    the polyline shader spreads it across the whole view (the flat pattern lies on the floor, partly behind a
    camera standing in front of the avatar)."""
    key = (id(lines), kind, alpha, np.array(rv3d.perspective_matrix).tobytes())
    b = _lb.get(key)
    if b is None:
        pts, cols = [], []
        for P, c, k in lines:
            if k == kind and len(P):
                pts.append(P)
                cols.append(np.tile((c[0], c[1], c[2], c[3] * alpha), (len(P), 1)))
        if pts:
            P, C = np.vstack(pts).astype(np.float32), np.vstack(cols).astype(np.float32)
            if rv3d.is_perspective:
                w = (np.c_[P, np.ones(len(P))] @ np.array(rv3d.perspective_matrix).T)[:, 3]
                ok = np.repeat((w[0::2] > 1e-3) & (w[1::2] > 1e-3), 2)
                P, C = P[ok], C[ok]
            b = batch_for_shader(sh, 'LINES', {'pos': P, 'color': C}) if len(P) else False
        else:
            b = False
        if len(_lb) > 64:
            _lb.clear()
        _lb[key] = b
    if b:
        sh.uniform_float('lineWidth', width)
        b.draw(sh)


def _draw_view():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if not overlay.shows(props, 'show_piece_colors'):
        return
    spec, outlines = store.get(context)
    if not spec or PV.layout_empty(context) is None:
        return
    region, rv3d = context.region, context.region_data
    vs = (region.width, region.height)
    gpu.state.blend_set('ALPHA')
    flat = _flat_data(context, spec, outlines)
    d3 = _data3d(context, spec, outlines, rv3d)
    su = gpu.shader.from_builtin('SMOOTH_COLOR')
    sh = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    # the flat pattern: on the floor beside the avatar, drawn over the piece objects
    gpu.state.depth_test_set('NONE')
    if len(flat['tri']):
        su.bind()
        _tris(su, flat).draw(su)
    sh.bind()
    sh.uniform_float('viewportSize', vs)
    # wider than the seams drawn over them (3 px), so a rim shows on both sides of a seam
    _draw_lines(sh, flat['lines'], 'sel', 10.0, rv3d)
    _draw_lines(sh, flat['lines'], 'state', 6.0, rv3d)
    if d3 is not None:
        gpu.state.depth_test_set('LESS_EQUAL')
        if len(d3['tri']):
            su.bind()
            _tris(su, d3).draw(su)
        sh.bind()
        sh.uniform_float('viewportSize', vs)
        _draw_lines(sh, d3['lines'], 'state', 5.0, rv3d)
        _draw_lines(sh, d3['lines'], 'sel', 6.0, rv3d)
        # the selected piece's rim also faintly where the body or the cloth hides it (found from any side)
        gpu.state.depth_test_set('NONE')
        _draw_lines(sh, d3['lines'], 'sel', 2.0, rv3d, alpha=0.2)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def invalidate():
    _flat_views.clear()
    for c in (_flat, _topo, _gco, _b3, _g3):
        c['key'] = None
        c.pop('tb_key', None)
    _lb.clear()
    _prim.clear()
    _ptint.clear()


def register():
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    invalidate()
