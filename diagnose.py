"""Diagnose overlays: colors over the cloth that answer "is something wrong here?" (Fable review
docs/reviews/REVIEW_fable_diagnostic_overlays_2026-11-02.md, user 2026-11-02).

Stretch: each triangle colored by how far the cloth is stretched from its pattern -- the larger principal stretch
of the triangle's 2D (pattern mm) -> 3D map. 1.05 or less is not drawn (the placement rule, DECISIONS
cloth-sim-placement-no-stretch-rule-2026-10-08), clear to yellow up to 1.20, yellow to red up to 1.50, deep red above;
squashed cloth (the smaller stretch 0.95 or less) blue. One scale for every piece (no piece kinds judged): a band
widened evenly shows one color all over, a bad stretch a gradient inside the piece. The same on the placed pieces,
the dressed garment and Live, read from the meshes alone (the placed pieces' layout points, the garment's UV =
pattern mm / 1000). A line at the top left gives each piece's median / largest.

Face Side: the cloth's face blue, its back red (user 2026-11-17, Fable review
docs/reviews/REVIEW_fable_face_side_flip_2026-11-17.md). The face is the side the pattern is seen from in 2D: the
triangles' winding, the same side as Blender's normals. Blender's own Face Orientation shows nothing useful here: the
thickness's Solidify turns its inner shell outward, so both sides read as the face, and it paints the body too. Drawn
from the meshes before the thickness, culled by winding: one pass for the triangles facing the eye, one for the rest.

Problems: draw only the validated current-shape snapshot from diagnosis.py: confirmed intersections and intrusion
red, sewing-unverified crossings yellow, with markers in the cloth and pattern view. The object properties written
by mark() below are legacy operation/run records; they are not the current-shape overlay's source of truth."""

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
    'hand': "moved by hand",              # moved by hand: vertices added / deleted, inside the body, stretched
    'anomaly': "the run stopped here",    # the solver's anomaly (its hardest pulling stitches)
    'unsettled': "still moving at the end",   # moved 2 mm or more in the last 50 frames of the last stage
    'inside': "inside the body",          # From Mesh: the imported garment's vertices inside the body
    'start': "did not start as shown",    # a run: vertices that started over 0.1 mm from the shape on screen
    'layer': "Layer has no effect",       # a run: a piece whose Layer reaches none or few of its vertices (seams)
    'check': "found by Diagnose",        # Diagnose (place_check): inside the body or through cloth, as shown
}
PROBLEM = np.array((1.0, 0.12, 0.08), np.float32)
FACE = (0.15, 0.4, 1.0, 0.45)       # the cloth's face (blue) and back (red), as Blender's Face Orientation
BACK = (0.95, 0.12, 0.08, 0.5)

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
    """{piece: (layout mm, triangles, world m, turned)} as the piece colors show them: the dressed garment's pieces
    where its result is seen, the placed objects elsewhere (piece_look._data3d decides the same way). And a shape key.
    turned: the triangles wind against the piece's face (a pair's mirrored half reads its source's triangles, and
    the Mirror modifier turns its faces over)."""
    from . import dress as DR
    from . import display_state as DS
    if not VV.allows_world(context, spec):
        return {}, None
    ob = DR.garment(spec)
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
                    out[n] = (flat, p['tri'], co, False)
        sh = {n: v for n, v in sh.items() if n in sap and n in visible}
    else:
        sh = {n: v for n, v in sh.items() if n in visible}
    for n, e in sh.items():
        if e[2] is not None and len(e[1]):
            out[n] = (e[0], np.asarray(e[1]), np.asarray(e[2], float), len(e[3]) > 2 and e[3][2] == 'twin')
    key.append(tuple((n, hash(f.tobytes()), hash(t.tobytes())) for n, (f, t, _w, _r) in sorted(out.items())))
    return out, tuple(map(str, key))


def _garment_flat(ob):
    """The garment's pattern points (mm) per vertex, from its UV (pattern mm / 1000, dress.garment_object)."""
    me = ob.data
    uv = me.uv_layers.get('UVMap')
    if uv is None:
        return None
    # UV coordinates can change without changing the vertex/loop counts.
    lv = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get('vertex_index', lv)
    u = np.empty(len(me.loops) * 2, np.float32)
    uv.data.foreach_get('uv', u)
    f = np.zeros((len(me.vertices), 2))
    f[lv] = u.reshape(-1, 2) * 1000.0
    return f


_tick = {'n': 0}
_pc = {}                    # per 3D view: (key, time, pieces, shape key)
RECHECK_S = 0.5


@bpy.app.handlers.persistent
def _changed(_scene, _depsgraph):
    _tick['n'] += 1
    from . import diagnosis
    diagnosis.dirty(_scene, _depsgraph)


def _pieces_now(context, spec, outlines):
    """_pieces, read again only when something changed: reading every piece's evaluated mesh took 14 ms a redraw on
    a T-shirt's placed pieces (2026-11-17), and the viewport redraws on every mouse move. Any depsgraph update, a
    frame or the stored spec changing reads them again, and at least every RECHECK_S whatever (a change that reaches
    no depsgraph). Kept per 3D view: what a view shows depends on the view (view_visibility)."""
    import time
    area = context.area.as_pointer() if context.area else 0
    from . import diagnosis
    snapshot = diagnosis.display_pieces(context, spec)
    if snapshot is not None:
        got, skey = snapshot
        _pc[area] = (None, time.monotonic(), got, skey)
        return got, skey
    key = (_tick['n'], context.scene.frame_current, PLV.store_key(), id(spec))
    c = _pc.get(area)
    now = time.monotonic()
    if (c is not None and c[0] == key and now - c[1] < RECHECK_S
            and _changed in bpy.app.handlers.depsgraph_update_post):
        return c[2], c[3]
    got, skey = _pieces(context, spec, outlines)
    _pc[area] = (key, now, got, skey)
    return got, skey


def _data(context, spec, outlines):
    got, key = _pieces_now(context, spec, outlines)
    area = context.area.as_pointer() if context.area else 0
    view = _d.setdefault('views', {}).get(area)
    if view is None or view['key'] != key:
        per = {}
        for n, (F, T, W, _turned) in got.items():
            l1, l2 = stretches(F, T, np.asarray(W, float) * 1000.0)
            per[n] = (np.asarray(W, float)[T.ravel()], l1, l2)
        view = {'key': key, 'per': per}
        _d['views'][area] = view
    _d.update(view)
    return _d


def view_stretch(context):
    area = context.area.as_pointer() if context.area else 0
    return _d.get('views', {}).get(area, {}).get('per', {})


def _draw_view():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    from . import diagnosis
    mode = diagnosis.mode(props) if props is not None and props.show_overlays else 'NONE'
    stretch, probs, side = mode == 'STRETCH', mode == 'PROBLEMS', mode == 'SIDE'
    if not (stretch or probs or side):
        return
    spec, outlines = store.get(context)
    if not spec or PV.layout_empty(context) is None:
        return
    if side:
        _draw_face_side(context, spec, outlines)
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


_fb = {'key': None}         # the face side's lifted triangles: per shape and view


def _draw_face_side(context, spec, outlines):
    got, key = _pieces_now(context, spec, outlines)
    if not got:
        return
    rv3d = context.region_data
    k = (key, np.array(rv3d.view_matrix).tobytes(), rv3d.is_perspective)
    if _fb['key'] != k:
        P = []
        for n, (_F, T, W, turned) in got.items():
            T = np.asarray(T, np.int64)
            if len(T):
                P.append(np.asarray(W, float)[(T[:, [0, 2, 1]] if turned else T).ravel()])
        if not P:
            _fb.update(key=k, batch=False)
            return
        _fb.update(key=k, batch=None, P=np.ascontiguousarray(_lift(np.vstack(P), rv3d), np.float32))
    if _fb.get('batch') is False:
        return
    su = gpu.shader.from_builtin('UNIFORM_COLOR')
    if _fb.get('batch') is None:
        _fb['batch'] = batch_for_shader(su, 'TRIS', {'pos': _fb['P']})
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    su.bind()
    for cull, color in (('BACK', FACE), ('FRONT', BACK)):     # counter-clockwise on screen: the face seen
        gpu.state.face_culling_set(cull)
        su.uniform_float('color', color)
        _fb['batch'].draw(su)
    gpu.state.face_culling_set('NONE')
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


def mark(ob, kind, verts=None, note="", weights=None, detail="", extra=None):
    """Record a problem of `kind` on `ob`: verts (its vertex indices) red, or None for the whole piece.
    weights: 0..1 per vertex in verts' order, the red shaded by it (strong at 1); none, all at full strength.
    detail: shown after the kind in the legend (which pieces, how far). extra: more keys kept in the record (Diagnose's
    'spots' and counts)."""
    import json
    d = problems(ob)
    if verts is None:
        rec = {'verts': None}
    elif weights is None:
        rec = {'verts': sorted({int(v) for v in verts})}
    else:
        w_of = {}
        for v, w in zip(verts, weights):
            w_of[int(v)] = max(w_of.get(int(v), 0.0), float(w))
        vs = sorted(w_of)
        rec = {'verts': vs, 'w': [round(w_of[v], 2) for v in vs]}     # rounded: kept in the .blend and undo
    rec['note'] = str(note)
    if detail:
        rec['detail'] = str(detail)
    if extra:
        rec.update(extra)
    d[kind] = rec
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
    g = DR.garment(spec)
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
    alpha = np.zeros(len(T), np.float32)          # per triangle: the kinds without weights, as before
    va = np.zeros(len(co), np.float32)            # per vertex: the kinds with weights (shaded by how far)
    for rec in recs.values():
        v = rec.get('verts')
        if v is None:
            alpha = np.maximum(alpha, 0.22)
            continue
        v = np.asarray(v, np.int64)
        ok = (v >= 0) & (v < len(co))
        w = rec.get('w')
        if w is not None and len(w) == len(v):
            # 0.08 faint at the least, 0.65 at the most: the strong red is where it is far off
            np.maximum.at(va, v[ok], (0.08 + 0.57 * np.asarray(w, np.float32)[ok]).astype(np.float32))
        else:
            on = np.zeros(len(co), bool)
            on[v[ok]] = True
            alpha = np.maximum(alpha, np.where(on[T].any(1), 0.6, 0.0).astype(np.float32))
    ac = np.maximum(alpha[:, None], va[T])        # per corner: SMOOTH_COLOR blends it across the triangle
    keep = ac.max(1) > 0
    if not keep.any():
        return None
    P = co[T[keep].ravel()]
    C = np.c_[np.broadcast_to(PROBLEM, (int(keep.sum()) * 3, 3)), ac[keep].ravel()]
    return P, C.astype(np.float32), (ob.name, ob.get(PROP), co[::max(1, len(co) // 16)].round(5).tobytes())


def _draw_problems(context, spec, rv3d):
    from . import diagnosis
    # Respect local-view membership in the cloth viewport; pattern spots are drawn separately.
    if not VV.allows_world(context, spec):
        return
    su = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    su.bind()
    for candidates, color in ((False, (*PROBLEM, 0.6)), (True, (1.0, .65, .05, .5))):
        P = diagnosis.problem_geometry(context, candidates=candidates)
        if P is not None:
            batch = batch_for_shader(su, 'TRIS', {'pos': np.ascontiguousarray(_lift(P, rv3d), np.float32)})
            su.uniform_float('color', color)
            batch.draw(su)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def problem_lines(spec):
    """The legend's lines: one per object with problems and its kinds (4 at most)."""
    lines = []
    for ob, piece, recs in _problem_objects(spec):
        what = ', '.join(tx(KINDS.get(k, k)) + (f" ({r['detail']})" if r.get('detail') else "")
                         + (" (" + tx("inside the body {0}, through cloth {1}", r['n_in'], r['n_x']) + ")"
                            if 'n_in' in r else "")
                         for k, r in recs.items())
        lines.append(tx("Red on {0}: {1}", piece or tx("the garment"), what))
    return lines[:4]


SPOT_PX = 11.0          # the spot's square, half side (the click dot's ring is 7)
SEEN_MM = 6.0           # a hit on the spot's own object this near it is the spot's own cloth (its thickness)
C_SPOT = (1.0, 0.2, 0.15, 1.0)
_seen_cache = {}


def spot_points(context, spec):
    """[(object, piece, 3D world point or None, layout world point, inside, through)] of Diagnose's spots. The 3D
    point is the spot's vertex as the object shows it now (its evaluated mesh: a pair's Mirror half, a follower's
    constraint), not a point kept in the object's space: a pair linked again on a click rewrites the object's matrix
    and vertices together, and a kept point moved off the cloth (user 2026-11-23)."""
    from . import diagnosis
    return diagnosis.spot_points(context, spec)


def _legacy_spot_points(context, spec):
    from mathutils import Vector
    out = []
    L = PV.layout_matrix(context)
    dg = context.evaluated_depsgraph_get()
    # every placed object, shown here or not: the pattern's view shows the pieces' flat objects, not the placed ones
    # (_problem_objects keeps those shown, and the flat spots were never drawn)
    for ob in {o.name: o for o in PLV.objects(spec).values()}.values():
        try:
            recs = problems(ob) if PROP in ob else {}
            sps = (recs.get('check') or {}).get('spots') or ()
            if not sps:
                continue
            ev = ob.evaluated_get(dg)
            vs, n = ev.data.vertices, len(ob.data.vertices)
            M = ev.matrix_world
        except ReferenceError:
            continue
        for s in sps:
            try:
                i = int(s['v']) + (n if s.get('half') else 0)
                w = M @ vs[i].co if 0 <= i < len(vs) else None
                u, v = s['f']
            except (KeyError, TypeError, ValueError):
                continue
            out.append((ob, s.get('piece'), w, L @ Vector((u, v, 0.0)), int(s.get('in', 0)), int(s.get('x', 0))))
    return out


def spot_label(n_in, n_x):
    parts = []
    if n_in:
        parts.append(tx("Intrusion {0} vertices", n_in))
    if n_x:
        parts.append(tx("Cloth crossing {0} faces", n_x))
    return ", ".join(parts)


MERGE_MM = 20.0     # spots closer than this (in 3D, or on the pattern) are one mark: a pair's halves at the middle


def merge_spots(spots):
    """[[point (m), n_in, n_x, key]] with those closer than MERGE_MM made one, the counts added. Measured where the
    spots are, not on the screen: the count beside a mark does not change with the view (user 2026-11-23: it did,
    merged by pixels), and the mark stays at the largest spot's point whatever order they come in (key breaks ties)."""
    out = []
    for p, a, b, k in sorted(spots, key=lambda s: (-(s[1] + s[2]), s[3])):
        for m in out:
            if (m[0] - p).length < MERGE_MM * 0.001:
                m[1], m[2] = m[1] + a, m[2] + b
                break
        else:
            out.append([p, a, b, k])
    return out


def _seen(context, ob, p):
    """Is the point p of ob's cloth seen in this view: nothing in front of it but its own cloth near it (inside the
    body the body is in front: not seen). One ray per spot, kept while the view and the scene stay."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_origin_3d, region_2d_to_vector_3d
    from . import garment_mark as GM
    from . import toolbar as TB
    region, rv3d = context.region, context.region_data
    xy = location_3d_to_region_2d(region, rv3d, p)
    if xy is None:
        return None, False
    key = (region.as_pointer(), np.array(rv3d.perspective_matrix).tobytes(), region.width, region.height,
           GM._updates[0], ob.name, tuple(round(c, 6) for c in p))
    if key in _seen_cache:
        return xy, _seen_cache[key]
    o = region_2d_to_origin_3d(region, rv3d, xy)
    d = region_2d_to_vector_3d(region, rv3d, xy).normalized()
    far = (p - o).dot(d)
    t, seen = 0.0, True
    for _ in range(16):
        if far - t <= GM.END_M:
            break
        try:
            ok, loc, _n, _i, hob, _m = TB.viewport_ray_cast(context, o + d * t, d, distance=far - t - GM.END_M)
        except Exception:
            ok = False
        if not ok or hob is None:
            break
        hob = hob.original
        own = hob.name == ob.name and (loc - p).length < SEEN_MM * 0.001
        if not own and hob.display_type not in {'WIRE', 'BOUNDS'}:
            seen = False
            break
        t = (loc - o).dot(d) + 1e-5
    if len(_seen_cache) > 256:
        _seen_cache.clear()
    _seen_cache[key] = seen
    return xy, seen


def _draw_spots(context, spec, *, candidates=False):
    """Diagnose's spots in this view (user 2026-11-23: the red was said, not where): on the cloth a red square with a
    count beside it, drawn over everything -- seen as a solid square, behind the body or other cloth a faded dot in a
    dashed square (the click dot's looks) -- and the same spot on the flat pattern. Returns the labels drawn."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from . import garment_mark as GM
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return []
    pieces = PV.piece_objects()
    on_cloth, on_pattern, obs = [], [], {}
    from . import diagnosis
    for ob, piece, w3, wf, n_in, n_x in diagnosis.spot_points(context, spec, candidates=candidates):
        key = (piece or "", tuple(round(c, 5) for c in wf))
        # the cloth's spot in a view showing the placed object; the pattern's only in a view not showing it (the
        # Work Screen's right): a selected piece's flat object is allowed in the 3D view too, and its spot showed at
        # the pattern's place at the 3D view's edge (user 2026-11-23)
        if w3 is not None and VV.object_decoration_allowed(context, ob):
            on_cloth.append([w3, n_in, n_x, key])
            obs[key] = ob
        elif piece is not None and VV.object_decoration_allowed(context, pieces.get(piece)):
            on_pattern.append([wf, n_in, n_x, key])
    marks = []                                # (xy, seen, label)
    for p, a, b, key in merge_spots(on_cloth):
        xy, seen = _seen(context, obs[key], p)
        if xy is not None:
            marks.append((xy, seen, tx("Crossing candidate {0} faces", b) if candidates else spot_label(a, b)))
    for p, a, b, _key in merge_spots(on_pattern):
        xy = location_3d_to_region_2d(region, rv3d, p)
        if xy is not None:
            marks.append((xy, True, tx("Crossing candidate {0} faces", b) if candidates else spot_label(a, b)))
    if not marks:
        return []
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    line = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    dot = gpu.shader.from_builtin('UNIFORM_COLOR')
    for xy, seen, _label in marks:
        ring = GM._ring(xy, SPOT_PX, 4)
        if seen:
            segs, kind = ring, 'LINE_STRIP'
        else:
            dot.bind()
            gpu.state.point_size_set(11.0)
            dot.uniform_float('color', (1., .65, .05, .35) if candidates else GM.C_FADED)
            batch_for_shader(dot, 'POINTS', {'pos': [(xy[0], xy[1], 0.0)]}).draw(dot)
            gpu.state.point_size_set(1.0)
            segs, kind = [q for k in range(0, len(ring) - 1, 2) for q in (ring[k], ring[k + 1])], 'LINES'
        line.bind()
        line.uniform_float('viewportSize', (region.width, region.height))
        for width, col in ((5.0, (1.0, 1.0, 1.0, 0.85)), (3.0, (1., .65, .05, 1.) if candidates else C_SPOT)):
            line.uniform_float('lineWidth', width)
            line.uniform_float('color', col)
            batch_for_shader(line, kind, {'pos': segs}).draw(line)
    gpu.state.blend_set('NONE')
    from . import text_band
    blf.size(0, 12)
    text_band.draw(0, [(x, y, label, (1., .8, .3, 1.) if candidates else (1.0, 0.55, 0.5, 1.0) if seen else (1.0, 0.75, 0.7, 0.8))
                       for (x, y), (_xy, seen, label) in zip(label_places(marks, lambda t: blf.dimensions(0, t)), marks)
                       if label])
    return [m[2] for m in marks]


def label_places(marks, size):
    """Each mark's label's baseline-left: right of its square, else left, below, above -- the first place clear of
    the squares and of the labels placed before (two pieces' spots side by side on the pattern overlapped)."""
    boxes = [(xy[0] - SPOT_PX, xy[1] - SPOT_PX, xy[0] + SPOT_PX, xy[1] + SPOT_PX) for xy, _s, _l in marks]
    out = []
    for xy, _seen, label in marks:
        w, h = size(label) if label else (0.0, 0.0)
        g = SPOT_PX + 4
        cands = [(xy[0] + g, xy[1] - 4), (xy[0] - g - w, xy[1] - 4),
                 (xy[0] - w / 2, xy[1] - g - h), (xy[0] - w / 2, xy[1] + g)]
        place = cands[0]
        for x, y in cands:
            b = (x - 2, y - 3, x + w + 2, y + h + 3)
            if not any(b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3] for o in boxes):
                place = (x, y)
                break
        out.append(place)
        boxes.append((place[0] - 2, place[1] - 3, place[0] + w + 2, place[1] + h + 3))
    return out


def _draw_px():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    from . import diagnosis
    mode = diagnosis.mode(props) if props is not None and props.show_overlays else 'NONE'
    per = view_stretch(context)
    stretch = mode == 'STRETCH' and bool(per)
    probs = mode == 'PROBLEMS'
    # the legend only in a view that colored something (not the pattern's view)
    area = context.area.as_pointer() if context.area else 0
    side = mode == 'SIDE' and bool((_pc.get(area) or (None, 0, {}))[2])
    from . import dress as D
    prep = D.run_preparing()
    if not (stretch or probs or prep or side):
        return
    spec, _o = store.get(context)
    if not prep and (not spec or PV.layout_empty(context) is None):
        return
    if probs and spec and PV.layout_empty(context) is not None:
        _draw_spots(context, spec)
        _draw_spots(context, spec, candidates=True)
    lines = []                         # (text, rgb)
    # a run that has not made its first frame yet: said where the person looks (user 2026-11-13: "準備中が一切出なかった")
    if prep:
        lines.append((tx("Preparing the simulation ({0:.0f} s elapsed)", prep[0]), (0.55, 0.8, 1.0)))
        if D.run_kernels_built():
            # building the compute programs: once on a PC, and once after each update of the add-on
            lines.append((tx("First run on this PC: building the compute programs for its GPU."), (0.85, 0.85, 0.85)))
            lines.append((tx("This can take a few minutes, once. Later runs start quickly."), (0.85, 0.85, 0.85)))
        # an ordinary start (say after editing the pattern) is not a first run: no more lines (user 2026-11-13)
        if not spec or PV.layout_empty(context) is None:
            stretch = probs = side = False
    if side:
        lines.append((tx("Face Side: blue the face, red the back"), (1.0, 1.0, 1.0)))
    if stretch:
        lines.append((tx("Stretch: up to {0} ok, {1} yellow, {2} red, blue squashed", OK, WARN, BAD), (1.0, 1.0, 1.0)))
        for n, m, x in summary(per, PL._selected(context)):
            c = colors(np.array([x]), np.array([1.0]))[0]
            lines.append((tx("{0}: {1:.2f} median, {2:.2f} max", n, m, x),
                          tuple(c[:3]) if x > OK else (0.85, 0.95, 0.85)))
    if probs:
        lines += [(t, (1.0, 0.45, 0.4)) for t in diagnosis.problem_lines(context)]
    fid = 0
    blf.size(fid, 13)
    # clear of the toolbar whatever its width (it was x = 70, under a toolbar with two columns or names; 2026-11-12)
    from . import ui as U
    x0, y0 = U.view_text_origin(context, 120)
    from . import text_band
    text_band.draw(fid, [(x0, y0 - i * 20, txt, (*rgb, 1.0)) for i, (txt, rgb) in enumerate(lines)])


def invalidate():
    for c in (_d, _b, _fb):
        c['key'] = None
    _d.pop('views', None)
    _pc.clear()


def register():
    if _changed not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_changed)
    _handles.append((bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'), 'WINDOW'))
    _handles.append((bpy.types.SpaceView3D.draw_handler_add(_draw_px, (), 'WINDOW', 'POST_PIXEL'), 'WINDOW'))


def unregister():
    for h, r in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, r)
        except Exception:
            pass
    _handles.clear()
    for h in [h for h in bpy.app.handlers.depsgraph_update_post if getattr(h, '__name__', '') == '_changed'
              and getattr(h, '__module__', '') == __name__]:
        bpy.app.handlers.depsgraph_update_post.remove(h)
    invalidate()
