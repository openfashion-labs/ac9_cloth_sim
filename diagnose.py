"""Diagnose overlays: colors over the cloth that answer "is something wrong here?" (Fable review
docs/reviews/REVIEW_fable_diagnostic_overlays_2026-11-02.md, user 2026-11-02).

Stretch: each triangle colored by how far the cloth is stretched from its pattern -- the larger principal stretch
of the triangle's 2D (pattern mm) -> 3D map. 1.05 or less is not drawn (the placement rule, DECISIONS
cloth-sim-placement-no-stretch-rule-2026-10-08), clear to yellow up to 1.20, yellow to red up to 1.50, deep red above;
squashed cloth (the smaller stretch 0.95 or less) blue. One scale for every piece (no piece kinds judged): a band
widened evenly shows one color all over, a bad stretch a gradient inside the piece. The same on the placed pieces,
the dressed garment and Live, read from the meshes alone (the placed pieces' layout points, the garment's UV =
pattern mm / 1000). A line at the top left gives each piece's median / largest.

Problems: what the add-on found wrong, red on the cloth itself (until 2026-11-02 a color attribute or the object's
color, seen only in Solid shading with that color mode, so never seen). Each finding is recorded on the object it is
on -- a placed piece or the dressed garment -- by mark() as {kind: {verts, note}} in the property PROP: verts the
mesh's vertex indices (their triangles red), or None for the whole piece (lightly red). On by default under the
Overlays switch (Fable review b-1)."""

import bpy
import blf
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import overlay
from . import piece_look as PL
from . import placed_view as PLV
from . import store
from . import pattern_view as PV
from . import view_visibility as VV
from .i18n import tx

LIFT_MM = PL.LIFT_MM + 1.0      # over the piece colors (both lifted towards the eye, drawn with the depth test)
OK, WARN, BAD = 1.05, 1.20, 1.50
SQUASH = 0.95, 0.80
YELLOW = np.array((1.0, 0.85, 0.1), np.float32)
RED = np.array((0.95, 0.1, 0.08), np.float32)
BLUE = np.array((0.2, 0.45, 1.0), np.float32)
PROP = 'ac9_problems'
KINDS = {                                 # kind: the legend's words
    'left': "left after fitting",         # Part to Seam / Band on Edge / settle: inside the body or through cloth
    'placement': "placed",                # Place: a band stretched to clear the body, a piece through another
    'fold': "fold stretches it",          # a fold too sharp for the piece's length here
    'hand': "moved by hand",              # Move by Hand: vertices added / deleted, inside the body, stretched
    'anomaly': "the run stopped here",    # the solver's anomaly (its hardest pulling stitches)
    'unsettled': "still moving at the end",   # moved 2 mm or more in the last 50 frames of the last stage
    'inside': "inside the body",          # From Mesh: the imported garment's vertices inside the body
    'start': "did not start as shown",    # a run: vertices that started over 0.1 mm from the shape on screen
}
PROBLEM = np.array((1.0, 0.12, 0.08), np.float32)

_handles = []
_d = {'key': None}          # per piece: triangles' world corners (m), stretches; per shape
_b = {'key': None}          # the lifted batch data: per shape and view


def stretches(F, T, W):
    """(larger, smaller) principal stretch per triangle of a piece: F layout mm (n, 2), T (m, 3), W world (n, 3) in
    the same unit as F. The eigenvalues of the 2x2 metric (Ds Dm^-1)^T (Ds Dm^-1), closed form."""
    F, W = np.asarray(F, float), np.asarray(W, float)
    T = np.asarray(T, np.int64)
    u = F[T[:, 1]] - F[T[:, 0]]
    v = F[T[:, 2]] - F[T[:, 0]]
    U = W[T[:, 1]] - W[T[:, 0]]
    V = W[T[:, 2]] - W[T[:, 0]]
    det = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    ok = np.abs(det) > 1e-9
    det = np.where(ok, det, 1.0)
    # Dm^-1 = [[v1, -v0], [-u1, u0]] / det; the deformation's columns a, b (3D)
    a = (U * v[:, 1:2] - V * u[:, 1:2]) / det[:, None]
    b = (V * u[:, 0:1] - U * v[:, 0:1]) / det[:, None]
    p, q, r = (a * a).sum(1), (a * b).sum(1), (b * b).sum(1)
    m, d = (p + r) / 2, np.sqrt(np.maximum(((p - r) / 2) ** 2 + q * q, 0.0))
    l1, l2 = np.sqrt(np.maximum(m + d, 0.0)), np.sqrt(np.maximum(m - d, 0.0))
    l1[~ok], l2[~ok] = 1.0, 1.0
    return l1, l2


def colors(l1, l2):
    """rgba per triangle (alpha 0: not drawn)."""
    c = np.zeros((len(l1), 4), np.float32)
    t = np.clip((l1 - OK) / (WARN - OK), 0.0, 1.0)
    s = np.clip((l1 - WARN) / (BAD - WARN), 0.0, 1.0)
    rgb = YELLOW[None] * (1 - s[:, None]) + RED[None] * s[:, None]
    c[:, :3] = rgb
    c[:, 3] = np.where(l1 > OK, 0.25 + 0.3 * t + 0.15 * s, 0.0)
    sq = (l1 <= OK) & (l2 < SQUASH[0])
    k = np.clip((SQUASH[0] - l2) / (SQUASH[0] - SQUASH[1]), 0.0, 1.0)
    c[sq, :3] = BLUE
    c[sq, 3] = 0.25 + 0.4 * k[sq]
    return c


def _pieces(context, spec, outlines):
    """{piece: (layout mm, triangles, world m)} as the piece colors show them: the dressed garment's pieces where its
    result is seen, the placed objects elsewhere (piece_look._data3d decides the same way). And a shape key."""
    from . import dress as DR
    from . import display_state as DS
    if not VV.allows_world(context, spec):
        return {}, None
    ob = bpy.data.objects.get(DR.garment_name(spec))
    display = DS.snapshot(spec, ob)
    visible = VV.world_names(context, spec, display=display)
    if not visible:
        return {}, None
    job = DS.job(ob)
    if job is None and DS.result_names(ob):
        job = 'blend:' + ob.name
    result_visible = any(n in visible and v['source'] == 'result' for n, v in display.items())
    sh, sig = PLV.shown(context, spec, outlines, include_off=True)
    out, key = {}, [sig, visible]
    if job is not None and result_visible:
        sap = {n for n, v in display.items() if v['source'] == 'placed'}
        pieces = PL._garment_topo(ob, job)
        co, ckey = PL._garment_co(context, ob, job)
        flat = _garment_flat(ob)
        key.append(ckey)
        if flat is not None:
            for n, p in pieces.items():
                if n in visible and n not in sap and display.get(n, {}).get('source') == 'result':
                    out[n] = (flat, p['tri'], co)
        sh = {n: v for n, v in sh.items() if n in sap and n in visible}
    else:
        sh = {n: v for n, v in sh.items() if n in visible}
    for n, e in sh.items():
        if e[2] is not None and len(e[1]):
            out[n] = (e[0], np.asarray(e[1]), np.asarray(e[2], float))
    return out, tuple(map(str, key))


_gflat = {'key': None}


def _garment_flat(ob):
    """The garment's pattern points (mm) per vertex, from its UV (pattern mm / 1000, dress.garment_object)."""
    me = ob.data
    uv = me.uv_layers.get('UVMap')
    if uv is None:
        return None
    key = (ob.name, len(me.vertices), len(me.loops), me.session_uid if hasattr(me, 'session_uid') else id(me))
    if _gflat['key'] != key:
        lv = np.empty(len(me.loops), np.int64)
        me.loops.foreach_get('vertex_index', lv)
        u = np.empty(len(me.loops) * 2, np.float32)
        uv.data.foreach_get('uv', u)
        f = np.zeros((len(me.vertices), 2))
        f[lv] = u.reshape(-1, 2) * 1000.0
        _gflat.update(key=key, f=f)
    return _gflat['f']


def _data(context, spec, outlines):
    got, key = _pieces(context, spec, outlines)
    if _d['key'] != key:
        per = {}
        for n, (F, T, W) in got.items():
            l1, l2 = stretches(F, T, np.asarray(W, float) * 1000.0)
            per[n] = (np.asarray(W, float)[T.ravel()], l1, l2)
        _d.update(key=key, per=per)
    return _d


def _draw_view():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    stretch, probs = overlay.shows(props, 'show_stretch'), overlay.shows(props, 'show_problems')
    if not (stretch or probs):
        return
    spec, outlines = store.get(context)
    if not spec or PV.layout_empty(context) is None:
        return
    if stretch:
        _draw_stretch(context, spec, outlines)
    if probs:
        _draw_problems(context, spec, context.region_data)


def _draw_stretch(context, spec, outlines):
    d = _data(context, spec, outlines)
    if not d['per']:
        return
    rv3d = context.region_data
    key = (d['key'], np.array(rv3d.view_matrix).tobytes(), rv3d.is_perspective)
    if _b['key'] != key:
        P, C = [], []
        for n, (Wc, l1, l2) in d['per'].items():
            c = colors(l1, l2)
            on = np.repeat(c[:, 3] > 0, 3)
            if on.any():
                P.append(Wc[on])
                C.append(np.repeat(c, 3, axis=0)[on])
        if P:
            Pv = _lift(np.vstack(P), rv3d)
            _b.update(key=key, batch=None, P=np.ascontiguousarray(Pv, np.float32),
                      C=np.ascontiguousarray(np.vstack(C), np.float32))
        else:
            _b.update(key=key, batch=False)
    if _b.get('batch') is False:
        return
    su = gpu.shader.from_builtin('SMOOTH_COLOR')
    if _b.get('batch') is None:
        _b['batch'] = batch_for_shader(su, 'TRIS', {'pos': _b['P'], 'color': _b['C']})
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    su.bind()
    _b['batch'].draw(su)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def _lift(P, rv3d):
    Vi = np.array(rv3d.view_matrix.inverted())
    if rv3d.is_perspective:
        dv = Vi[:3, 3][None] - P
        dv /= np.maximum(np.linalg.norm(dv, axis=1), 1e-9)[:, None]
    else:
        dv = np.broadcast_to(Vi[:3, 2], P.shape)
    return P + dv * (LIFT_MM * PV.MM)


def summary(per, selected=()):
    """[(piece, median, largest)] of the larger stretch: the selected pieces, else the 3 most stretched."""
    rows = [(n, float(np.median(l1)), float(l1.max())) for n, (_, l1, _l2) in per.items() if len(l1)]
    sel = [r for r in rows if r[0] in selected]
    if sel:
        return sorted(sel, key=lambda r: -r[2])
    return sorted(rows, key=lambda r: -r[2])[:3]


# ------------------------------------------------------------------ problems


def mark(ob, kind, verts=None, note=""):
    """Record a problem of `kind` on `ob`: verts (its vertex indices) red, or None for the whole piece."""
    import json
    d = problems(ob)
    d[kind] = {'verts': None if verts is None else sorted({int(v) for v in verts}), 'note': str(note)}
    ob[PROP] = json.dumps(d)


def clear(ob, *kinds):
    """Drop the problems of these kinds from `ob` (all when none named)."""
    import json
    d = problems(ob)
    for k in (kinds or list(d)):
        d.pop(k, None)
    if d:
        ob[PROP] = json.dumps(d)
    elif PROP in ob:
        del ob[PROP]


def problems(ob):
    import json
    try:
        return json.loads(ob.get(PROP, '{}')) if ob is not None else {}
    except (ValueError, TypeError, ReferenceError):
        return {}


_pb = {'key': None}


def _problem_objects(spec):
    """[(object, piece or None, {kind: record})] of the shown objects with problems: placed pieces and the garment."""
    from . import dress as DR
    out = []
    for ob in {o.name: o for o in PLV.objects(spec).values()}.values():
        try:
            if PROP in ob and ob.visible_get():
                out.append((ob, ob.get(PLV.PROP), problems(ob)))
        except ReferenceError:
            continue
    g = bpy.data.objects.get(DR.garment_name(spec))
    if g is not None and PROP in g and g.visible_get():
        out.append((g, None, problems(g)))
    return out


def _problem_tris(context, ob, recs, garment):
    """(world corners (3k, 3), rgba (3k, 4), shape sample) of `ob`'s problems, or None."""
    from . import dress as DR
    me = ob.data
    if not len(me.vertices):
        return None
    if garment:
        co = DR.garment_co(context, ob).astype(np.float64)      # the frame shown, the solver's vertices
    else:
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
    if len(co) != len(me.vertices):
        return None
    Mw = np.array(ob.matrix_world)
    co = co @ Mw[:3, :3].T + Mw[:3, 3]
    me.calc_loop_triangles()
    T = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get('vertices', T)
    T = T.reshape(-1, 3)
    alpha = np.zeros(len(T), np.float32)
    for rec in recs.values():
        v = rec.get('verts')
        if v is None:
            alpha = np.maximum(alpha, 0.22)
        else:
            on = np.zeros(len(co), bool)
            v = np.asarray(v, np.int64)
            on[v[(v >= 0) & (v < len(co))]] = True
            alpha = np.maximum(alpha, np.where(on[T].any(1), 0.6, 0.0).astype(np.float32))
    keep = alpha > 0
    if not keep.any():
        return None
    P = co[T[keep].ravel()]
    C = np.repeat(np.c_[np.broadcast_to(PROBLEM, (int(keep.sum()), 3)), alpha[keep]], 3, axis=0)
    return P, C.astype(np.float32), (ob.name, ob.get(PROP), co[::max(1, len(co) // 16)].round(5).tobytes())


def _draw_problems(context, spec, rv3d):
    from . import dress as DR
    gname = DR.garment_name(spec)
    got = [r for r in (_problem_tris(context, ob, recs, ob.name == gname) for ob, _p, recs in _problem_objects(spec))
           if r is not None]
    if not got:
        return
    su = gpu.shader.from_builtin('SMOOTH_COLOR')
    key = (tuple(r[2] for r in got), np.array(rv3d.view_matrix).tobytes(), rv3d.is_perspective)
    if _pb['key'] != key:
        P = _lift(np.vstack([r[0] for r in got]), rv3d)
        _pb.update(key=key, batch=batch_for_shader(su, 'TRIS', {
            'pos': np.ascontiguousarray(P, np.float32),
            'color': np.ascontiguousarray(np.vstack([r[1] for r in got]), np.float32)}))
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    su.bind()
    _pb['batch'].draw(su)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def problem_lines(spec):
    """The legend's lines: one per object with problems and its kinds (4 at most)."""
    lines = []
    for ob, piece, recs in _problem_objects(spec):
        what = ', '.join(tx(KINDS.get(k, k)) for k in recs)
        lines.append(tx("Red on {0}: {1}", piece or tx("the garment"), what))
    return lines[:4]


def _draw_px():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    stretch = overlay.shows(props, 'show_stretch') and _d.get('per')
    probs = overlay.shows(props, 'show_problems')
    if not (stretch or probs):
        return
    spec, _o = store.get(context)
    if not spec or PV.layout_empty(context) is None:
        return
    lines = []                         # (text, rgb)
    if stretch:
        lines.append((tx("Stretch: up to {0} ok, {1} yellow, {2} red, blue squashed", OK, WARN, BAD), (1.0, 1.0, 1.0)))
        for n, m, x in summary(_d['per'], PL._selected(context)):
            c = colors(np.array([x]), np.array([1.0]))[0]
            lines.append((tx("{0}: {1:.2f} median, {2:.2f} max", n, m, x),
                          tuple(c[:3]) if x > OK else (0.85, 0.95, 0.85)))
    if probs:
        lines += [(t, (1.0, 0.45, 0.4)) for t in problem_lines(spec)]
    region = context.region
    fid = 0
    blf.size(fid, 13)
    for i, (txt, rgb) in enumerate(lines):
        x, y = 70, region.height - 120 - i * 18
        blf.color(fid, 0.0, 0.0, 0.0, 0.9)
        blf.position(fid, x + 1, y - 1, 0)
        blf.draw(fid, txt)
        blf.color(fid, *rgb, 1.0)
        blf.position(fid, x, y, 0)
        blf.draw(fid, txt)


def invalidate():
    for c in (_d, _b, _gflat, _pb):
        c['key'] = None


def register():
    _handles.append((bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'), 'WINDOW'))
    _handles.append((bpy.types.SpaceView3D.draw_handler_add(_draw_px, (), 'WINDOW', 'POST_PIXEL'), 'WINDOW'))


def unregister():
    for h, r in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, r)
        except Exception:
            pass
    _handles.clear()
    invalidate()
