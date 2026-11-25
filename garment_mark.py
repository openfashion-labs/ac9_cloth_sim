# SPDX-License-Identifier: GPL-3.0-or-later
"""The point clicked on the dressed garment shown on its pattern too (user 2026-10-14): a click on the garment
with an AC9 tool leaves a dot there and the same point on the flat pattern. The dot stays on the cloth
(it follows the frames) until the next click on the garment or a blank click clears it.

The pattern point comes from the garment's UV map: dress.garment_object writes each vertex's layout point (mm / 1000)
as its UV, so the point is the clicked triangle's UVs weighted as the click is on the triangle.

The placed pieces before a Dress too (user 2026-10-15: the seams are chosen before dressing): their layout points are
the vertex attribute placed_view.FLAT_ATTR; on a pair's Mirror half the source's points mirrored into the twin's
outline (placed_view.mirror_flat, as the seams drawn on it).
"""
import bpy
import json
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import barycentric_transform, closest_point_on_tri
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d

from . import pattern_view as PV
from . import placed_view as PLV
from . import view_visibility as VV

C_DOT = (1.0, 0.2, 0.15, 1.0)
C_RIM = (1.0, 1.0, 1.0, 1.0)
_mark = {}                   # 'garment' (the object's name: a garment or a placed piece), 'verts' (3 vertex indices),
                             # 'w' (3 weights), 'uv' (m, layout)
_handles = []


def clear(context=None):
    _mark.clear()
    if context is not None:
        _redraw(context)


def mark(context, g, mx, my):
    """Mark the point of garment `g` (or a placed piece's object) under region (mx, my). Returns the pattern point (mm)
    or None."""
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or g is None:
        return None
    return mark_ray(context, g, region_2d_to_origin_3d(region, rv3d, (mx, my)),
                    region_2d_to_vector_3d(region, rv3d, (mx, my)))


def mark_ray(context, g, o, d):
    """mark() along the world ray o + t d (the view ray of a click). Either side of the cloth: the ray cast keeps
    back faces, and the thickness's copied faces read the layout points of the faces they were copied from."""
    if g is None:
        return None
    dg = context.evaluated_depsgraph_get()
    ev = g.evaluated_get(dg)
    Mi = ev.matrix_world.inverted()
    lo = Mi @ o
    ld = (Mi.to_3x3() @ d).normalized()
    ok, loc, _n, fi = ev.ray_cast(lo, ld)
    me = ev.data
    placed = PLV.PROP in g
    uvl = None if placed else me.uv_layers.active
    if not ok or (uvl is None and not placed) or not (0 <= fi < len(me.polygons)):
        return None
    poly = me.polygons[fi]
    ls = list(poly.loop_indices)
    vs = [me.loops[k].vertex_index for k in ls]
    best = None
    for k in range(1, len(vs) - 1):                    # a fan: the triangle the click is closest to (tris as a rule)
        tri = (0, k, k + 1)
        P = [me.vertices[vs[t]].co for t in tri]
        dd = (closest_point_on_tri(loc, *P) - loc).length
        if best is None or dd < best[0]:
            best = (dd, tri, P)
    _, tri, P = best
    w = barycentric_transform(loc, *P, Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    w = np.clip(np.array(w[:]), 0.0, None)
    w = w / max(w.sum(), 1e-12)
    if placed:
        uv = _placed_uv(context, g, dg, [vs[t] for t in tri], w)
        if uv is None:
            return None
    else:
        uv = w @ np.array([uvl.data[ls[t]].uv[:] for t in tri])
    piece = None
    if placed:
        piece = PLV.placed_face_piece(context, g, fi)
    else:
        attribute = me.attributes.get('ac9_piece')
        try:
            names = json.loads(g.get('ac9_piece_names', '[]'))
        except (TypeError, ValueError):
            names = []
        if attribute is not None and fi < len(attribute.data):
            index = attribute.data[fi].value
            piece = names[index] if 0 <= index < len(names) else None
    _mark.update(garment=g.name, piece=piece, verts=tuple(vs[t] for t in tri), w=tuple(w.tolist()),
                 uv=tuple(np.asarray(uv).tolist()), nsign=1.0)       # the face's own loops: its own normal
    for a in context.screen.areas if context.screen else ():
        if a.type == 'VIEW_3D':
            a.tag_redraw()
    return tuple(v * 1000.0 for v in _mark['uv'])


GARMENT_PREFIX = "AC9_Garment_"     # dress.GARMENT_PREFIX (not imported: dress imports the solver side)


def _in_tri(P, A, B, C):
    """Barycentric weights (k, 3) of the 2D point P in each triangle (A, B, C: (k, 2)); rows outside: a negative."""
    v0, v1, v2 = B - A, C - A, P[None] - A
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-18
    den = np.where(ok, den, 1.0)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    W = np.stack([1.0 - v - w, v, w], 1)
    W[~ok] = -1.0
    return W


EDGE_MM = 3.0      # a click on the outline (an edge or a corner picked) lands just outside the cloth's triangles


def _pick(W, P, A, B, C, scale=1.0):
    """(triangle, 3 weights) of the 2D point P among the triangles A, B, C (W: its weights in each, _in_tri): the
    first holding it (a little slack for its edges); else the nearest point of the triangle the point is least
    outside of, when within EDGE_MM (A, B, C in mm times scale); else None."""
    hit = np.flatnonzero((W >= -1e-6).all(1))
    if len(hit):
        j = int(hit[0])
        w = np.clip(W[j], 0.0, None)
        return j, w / max(w.sum(), 1e-12)
    if not len(W):
        return None
    j = int(W.min(1).argmax())
    T = [Vector((float(X[j][0]), float(X[j][1]), 0.0)) for X in (A, B, C)]
    q = closest_point_on_tri(Vector((float(P[0]), float(P[1]), 0.0)), *T)
    if (q.to_2d() - Vector((float(P[0]), float(P[1])))).length / scale > EDGE_MM:
        return None
    w = np.clip(_in_tri(np.array(q.to_2d()), *(np.asarray(X[j:j + 1], float) for X in (A, B, C)))[0], 0.0, None)
    return j, w / max(w.sum(), 1e-12)


def _garment_hit(context, g, piece, uv):
    """(3 vertex indices, 3 weights) of the layout point uv (m) on piece of the dressed garment g, or None. Its UVs
    are the layout points the garment was dressed with (dress.garment_object), read on the evaluated mesh as a click
    on it is (the thickness's Solidify and a Reset's Mask change the vertices; the UVs and ac9_piece go with them)."""
    me = g.evaluated_get(context.evaluated_depsgraph_get()).data
    uvl = me.uv_layers.active
    attribute = me.attributes.get('ac9_piece')
    try:
        names = json.loads(g.get('ac9_piece_names', '[]'))
    except (TypeError, ValueError):
        names = []
    if uvl is None or attribute is None or piece not in names or not len(me.polygons):
        return None
    k = names.index(piece)
    fp = np.empty(len(me.polygons), np.int32)
    attribute.data.foreach_get('value', fp)
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    if not nt:
        return None
    poly = np.empty(nt, np.int32)
    me.loop_triangles.foreach_get('polygon_index', poly)
    loops = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get('loops', loops)
    verts = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get('vertices', verts)
    keep = np.flatnonzero(fp[poly] == k)
    if not len(keep):
        return None
    U = np.empty(len(me.loops) * 2, np.float32)
    uvl.data.foreach_get('uv', U)
    U = U.reshape(-1, 2).astype(np.float64)
    L, V = loops.reshape(-1, 3)[keep], verts.reshape(-1, 3)[keep]
    P, A, B, C = np.asarray(uv, float), U[L[:, 0]], U[L[:, 1]], U[L[:, 2]]
    r = _pick(_in_tri(P, A, B, C), P, A, B, C, scale=0.001)
    if r is None:
        return None
    return tuple(int(v) for v in V[r[0]]), r[1]


def _placed_hit(context, ob, piece, uv):
    """(3 vertex indices, 3 weights) of the layout point uv (m) on piece of the placed object ob, or None. On the
    Mirror's half the own triangles shifted by n (the Mirror appends the mirrored copy in the same order), their
    layout points the source's mirrored into the twin's outline (as _placed_uv reads them back)."""
    from . import store
    c = PLV._read(context, ob, context.evaluated_depsgraph_get())
    if c is None or not len(c['T']):
        return None
    F, T, n = c['F'], c['T'], c['n']
    if piece == ob.get(PLV.PROP):
        shift = 0
    elif c['twin'] and piece == ob.get(PLV.TWIN_PROP):
        outlines = store.get(context)[1]
        src, tw = ob.get(PLV.PROP), ob.get(PLV.TWIN_PROP)
        if outlines is None or src not in outlines or tw not in outlines:
            return None
        F = PLV.mirror_flat(F, outlines[src].B, outlines[tw].B, outlines[src].plane)
        shift = n
    else:
        return None
    p = np.asarray(uv, float) * 1000.0
    A, B, C = F[T[:, 0]], F[T[:, 1]], F[T[:, 2]]
    r = _pick(_in_tri(p, A, B, C), p, A, B, C)
    if r is None:
        return None
    # the Mirror's half reverses each face's loops: its own triangle's order there makes the normal point inward
    return tuple(int(v) + shift for v in T[r[0]]), r[1], (-1.0 if shift else 1.0)


def mark_flat(context, piece, p_mm):
    """Mark the layout point p_mm (mm) of the flat piece and its point on the cloth: the dressed garment holding the
    piece if one is shown, else its placed object (user 2026-11-17: which point of the 3D a point of a symmetric
    pattern is). Returns True when the cloth point was found; else the old dot goes (none wrong left on the cloth)."""
    if piece is None or p_mm is None:
        return False
    uv = np.asarray(p_mm, float) / 1000.0
    # shown in the scene, not in the view clicked: the flat pattern's view (the Work Screen's right) does not show
    # the cloth, the 3D view does; each view's draw keeps the dot to what it shows (visible_points)
    cands = [o for o in context.scene.objects
             if o.type == 'MESH' and o.name.startswith(GARMENT_PREFIX) and _shown(context, o)]
    ob = PLV.objects().get(piece)
    if ob is not None and _shown(context, ob):
        cands.append(ob)
    for o in cands:
        try:
            hit = _placed_hit(context, o, piece, uv) if PLV.PROP in o else _garment_hit(context, o, piece, uv)
        except Exception:
            hit = None
        if hit is not None:
            _mark.clear()
            _mark.update(garment=o.name, piece=piece, verts=hit[0], w=tuple(float(x) for x in hit[1]),
                         uv=tuple(uv.tolist()), nsign=hit[2] if len(hit) > 2 else 1.0)
            _redraw(context)
            return True
    _mark.clear()
    _redraw(context)
    return False


def _shown(context, ob):
    """ob is shown in some 3D view of the screen (the Work Screen shows the cloth in its left view only, by the view's
    own collections: the view layer alone hides it)."""
    screen = context.screen
    for a in screen.areas if screen else ():
        if a.type != 'VIEW_3D':
            continue
        region = next((r for r in a.regions if r.type == 'WINDOW'), None)
        try:
            with context.temp_override(area=a, region=region, space_data=a.spaces.active):
                if VV.object_decoration_allowed(bpy.context, ob):
                    return True
        except Exception:
            continue
    return False


def _redraw(context):
    for a in context.screen.areas if context.screen else ():
        if a.type == 'VIEW_3D':
            a.tag_redraw()


def _placed_uv(context, ob, dg, verts, w):
    """The layout point (m) of a placed piece's point: its own half's FLAT_ATTR weighted; on the Mirror's half (the
    evaluated mesh holds the own vertices first, then their mirrored copy) the same, mirrored into the twin's outline.
    A vertex of the thickness (the Solidify appends a copy of every vertex: the face seen from the back, or of a
    piece turned over) reads the vertex it was copied from, as placed_view.placed_face_piece does."""
    from . import store
    c = PLV._read(context, ob, dg)
    if c is None:
        return None
    core = PLV.core_count(ob, len(ob.evaluated_get(dg).data.vertices))
    verts = [v % core for v in verts]
    n = c['n']
    if max(verts) >= 2 * n or (max(verts) >= n and not c['twin']):
        return None
    half = {v >= n for v in verts}
    if len(half) != 1:                        # a face across the mirror plane (merge is off: none, kept for safety)
        return None
    p = w @ c['F'][[v % n for v in verts]]
    if True in half:
        outlines = store.get(context)[1]
        src, tw = ob.get(PLV.PROP), ob.get(PLV.TWIN_PROP)
        if outlines is None or src not in outlines or tw not in outlines:
            return None
        p = PLV.mirror_flat(p[None], outlines[src].B, outlines[tw].B, outlines[src].plane)[0]
    return np.asarray(p) / 1000.0


def points(context):
    """(3D point on the cloth now, its pattern point) in world coordinates, or None."""
    if not _mark:
        return None
    g = bpy.data.objects.get(_mark['garment'])
    if g is None or g.type != 'MESH':
        return None
    me = g.evaluated_get(context.evaluated_depsgraph_get()).data
    if max(_mark['verts']) >= len(me.vertices):
        return None
    p = sum((me.vertices[i].co * w for i, w in zip(_mark['verts'], _mark['w'])), Vector())
    u, v = _mark['uv']
    return g.matrix_world @ p, PV.layout_matrix(context) @ Vector((u, v, 0.0))


def visible_points(context):
    """Keep the shared correspondence; project only owners belonging here."""
    pts = points(context)
    if pts is None:
        return ()
    out = []
    if VV.object_decoration_allowed(context, bpy.data.objects.get(_mark.get('garment', ''))):
        out.append(pts[0])
    layout = PV.layout_empty(context)
    piece = _mark.get('piece')
    flat_visible = (VV.object_decoration_allowed(context, PV.piece_objects().get(piece)) if piece is not None else
                    VV.object_decoration_allowed(context, layout) and VV.allows_pattern(context))
    if flat_visible:
        out.append(pts[1])
    return tuple(out)


VISIBLE, BACK, HIDDEN = 'VISIBLE', 'BACK', 'HIDDEN'
END_M = 0.0002      # the ray stops this short of the dot (its own face is not something in front of it)
C_FADED = (1.0, 0.2, 0.15, 0.35)
RING_PX = 7.0
_state_cache = {}
_updates = [0]


def _cloth_normal(context):
    """(3D point, unit world normal of the dot's triangle) or None. The Mirror's half read through its own
    triangles (marked from the pattern) faces the other way: 'nsign' turns it back."""
    if not _mark:
        return None
    g = bpy.data.objects.get(_mark['garment'])
    if g is None or g.type != 'MESH':
        return None
    me = g.evaluated_get(context.evaluated_depsgraph_get()).data
    if max(_mark['verts']) >= len(me.vertices):
        return None
    M = g.matrix_world
    P = [M @ me.vertices[i].co for i in _mark['verts']]
    p = sum((q * w for q, w in zip(P, _mark['w'])), Vector())
    n = (P[1] - P[0]).cross(P[2] - P[0])
    if n.length < 1e-15:
        return p, None
    return p, n.normalized() * _mark.get('nsign', 1.0)


def _own_face(context, ob, face_index):
    """Is the hit face of the dot's own cloth the dot's own place: its triangle's face, the thickness's copy of it
    (or the rim), or a face touching it (the ray grazing an edge)? Decided by the mesh, not by distance: the two
    layers of a lapel lie a few mm apart, and a distance rule took the front one for the cloth's own thickness
    (user 2026-11-20: a dot behind the lapel drawn as seen, flickering from place to place)."""
    me = ob.evaluated_get(context.evaluated_depsgraph_get()).data
    if not (0 <= face_index < len(me.polygons)):
        return False
    core = max(1, PLV.core_count(ob, len(me.vertices)))      # the thickness's copies: the vertex they came from
    mine = {v % core for v in _mark['verts']}
    return any(v % core in mine for v in me.polygons[face_index].vertices)


def cloth_state(context, o, d):
    """How the dot on the cloth is seen along the view ray o + t d (user 2026-11-20: a dot on the back of the cloth
    or behind another piece shown as such): HIDDEN when something else is in front of it (another piece, the body,
    another part of the same cloth: any face but the dot's own place, _own_face); else BACK when its face looks
    away from the view; else VISIBLE. Wireframe and bounds-only objects do not hide it."""
    pn = _cloth_normal(context)
    if pn is None:
        return VISIBLE
    p, n = pn
    from . import toolbar as TB
    d = Vector(d).normalized()
    o = Vector(o)
    far = (p - o).dot(d)
    t = 0.0
    for _ in range(16):
        if far - t <= END_M:
            break
        try:
            ok, loc, _nrm, idx, ob, _m = TB.viewport_ray_cast(context, o + d * t, d, distance=far - t - END_M)
        except Exception:
            ok = False
        if not ok or ob is None:
            break
        ob = ob.original
        own = ob.name == _mark['garment'] and _own_face(context, ob, idx)
        if not own and ob.display_type not in {'WIRE', 'BOUNDS'}:
            return HIDDEN
        t = (loc - o).dot(d) + 1e-5
    if n is not None and n.dot(d) > 0.0:
        return BACK
    return VISIBLE


def _view_state(context):
    """cloth_state of the dot seen in this 3D view, kept while the view and the dot stay (one ray a change)."""
    region, rv3d = context.region, context.region_data
    pn = _cloth_normal(context) if region is not None and rv3d is not None else None
    if pn is None:
        return VISIBLE, None
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    p = pn[0]
    xy = location_3d_to_region_2d(region, rv3d, p)
    if xy is None:
        return VISIBLE, None
    # the scene's update count: hiding or moving what stands in front changes the answer with the dot unmoved
    key = (np.array(rv3d.perspective_matrix).tobytes(), tuple(round(c, 7) for c in p), region.width, region.height,
           _updates[0])
    hit = _state_cache.get(region.as_pointer())
    if hit is not None and hit[0] == key:
        return hit[1], xy
    s = cloth_state(context, region_2d_to_origin_3d(region, rv3d, xy), region_2d_to_vector_3d(region, rv3d, xy))
    _state_cache[region.as_pointer()] = (key, s)
    if len(_state_cache) > 32:
        _state_cache.clear()
    return s, xy


def _draw_view():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if not _mark or props is None or not getattr(props, 'show_overlays', True):
        return
    pts = visible_points(context)
    if not pts:
        return
    cloth = points(context)
    if cloth is not None and _view_state(context)[0] != VISIBLE:
        pts = tuple(q for q in pts if (q - cloth[0]).length > 1e-9)       # the cloth's dot drawn as a ring

        if not pts:
            return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    sh.bind()
    b = batch_for_shader(sh, 'POINTS', {'pos': [tuple(p) for p in pts]})
    for size, col in ((16.0, C_RIM), (11.0, C_DOT)):     # a white rim: seen on the gray cloth and the colored lines
        gpu.state.point_size_set(size)
        sh.uniform_float('color', col)
        b.draw(sh)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set('NONE')


def _ring(xy, r, n=3):
    """A square outline around xy (half side r), the same shape as the dot (a square point): its corners and n - 1
    points on each side between them, closed (the dashes take every other piece)."""
    C = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    out = []
    for i in range(4):
        (ax, ay), (bx, by) = C[i], C[(i + 1) % 4]
        out += [(xy[0] + r * (ax + (bx - ax) * k / n), xy[1] + r * (ay + (by - ay) * k / n), 0.0) for k in range(n)]
    return out + out[:1]


def _draw_pixel():
    """The cloth's dot not seen from here, on the screen, square as the dot is: on the back of the cloth a hollow
    square (white rim), behind something a faded dot in a dashed square."""
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if not _mark or props is None or not getattr(props, 'show_overlays', True):
        return
    if not VV.object_decoration_allowed(context, bpy.data.objects.get(_mark.get('garment', ''))):
        return
    state, xy = _view_state(context)
    if state == VISIBLE or xy is None:
        return
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    line = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    line.bind()
    line.uniform_float('viewportSize', (region.width, region.height))
    ring = _ring(xy, RING_PX)
    if state == BACK:
        for width, col in ((5.0, C_RIM), (3.0, C_DOT)):
            line.uniform_float('lineWidth', width)
            line.uniform_float('color', col)
            batch_for_shader(line, 'LINE_STRIP', {'pos': ring}).draw(line)
    else:
        dot = gpu.shader.from_builtin('UNIFORM_COLOR')
        dot.bind()
        gpu.state.point_size_set(11.0)
        dot.uniform_float('color', C_FADED)
        batch_for_shader(dot, 'POINTS', {'pos': [(xy[0], xy[1], 0.0)]}).draw(dot)
        gpu.state.point_size_set(1.0)
        line.bind()
        line.uniform_float('viewportSize', (region.width, region.height))
        dashes = [q for k in range(0, len(ring) - 1, 2) for q in (ring[k], ring[k + 1])]
        for width, col in ((4.0, (1.0, 1.0, 1.0, 0.6)), (2.0, C_DOT)):
            line.uniform_float('lineWidth', width)
            line.uniform_float('color', col)
            batch_for_shader(line, 'LINES', {'pos': dashes}).draw(line)
    gpu.state.blend_set('NONE')


@bpy.app.handlers.persistent
def _loaded(*_a):
    _mark.clear()
    _state_cache.clear()


@bpy.app.handlers.persistent
def _updated(*_a):
    _updates[0] += 1


def register():
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_pixel, (), 'WINDOW', 'POST_PIXEL'))
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)
    if _updated not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_updated)


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    for hs, name in ((bpy.app.handlers.load_post, '_loaded'), (bpy.app.handlers.depsgraph_update_post, '_updated')):
        for h in list(hs):
            if getattr(h, '__name__', '') == name and getattr(h, '__module__', '') == __name__:
                hs.remove(h)
    _mark.clear()
    _state_cache.clear()
