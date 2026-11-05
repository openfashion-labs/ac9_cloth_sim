# SPDX-License-Identifier: GPL-3.0-or-later
"""The point clicked on the dressed garment shown on its pattern too (user 2026-10-14): a click on the garment
with an AC9 tool leaves a dot there and the same point on the flat pattern. The dot stays on the cloth
(it follows the frames) until the next click on the garment.

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


def clear():
    _mark.clear()


def mark(context, g, mx, my):
    """Mark the point of garment `g` (or a placed piece's object) under region (mx, my). Returns the pattern point (mm)
    or None."""
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None or g is None:
        return None
    dg = context.evaluated_depsgraph_get()
    ev = g.evaluated_get(dg)
    Mi = ev.matrix_world.inverted()
    o = region_2d_to_origin_3d(region, rv3d, (mx, my))
    d = region_2d_to_vector_3d(region, rv3d, (mx, my))
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
                 uv=tuple(np.asarray(uv).tolist()))
    for a in context.screen.areas if context.screen else ():
        if a.type == 'VIEW_3D':
            a.tag_redraw()
    return tuple(v * 1000.0 for v in _mark['uv'])


def _placed_uv(context, ob, dg, verts, w):
    """The layout point (m) of a placed piece's point: its own half's FLAT_ATTR weighted; on the Mirror's half (the
    evaluated mesh holds the own vertices first, then their mirrored copy) the same, mirrored into the twin's outline."""
    from . import store
    c = PLV._read(context, ob, dg)
    if c is None:
        return None
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
        p = PLV.mirror_flat(p[None], outlines[src].B, outlines[tw].B)[0]
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


def _draw_view():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if not _mark or props is None or not getattr(props, 'show_overlays', True):
        return
    pts = visible_points(context)
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


@bpy.app.handlers.persistent
def _loaded(*_a):
    _mark.clear()


def register():
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    for h in list(bpy.app.handlers.load_post):
        if getattr(h, '__name__', '') == '_loaded' and getattr(h, '__module__', '') == __name__:
            bpy.app.handlers.load_post.remove(h)
    _mark.clear()
