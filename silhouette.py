# SPDX-License-Identifier: GPL-3.0-or-later
"""Three flat, metric reference shadows; no objects, images or physics are created.

Opaque offscreen masks union triangles before applying opacity ONCE. Evaluated
geometry and masks are retained across pan/zoom and position changes. Source
epochs are shared with the existing overlay's geometry/undo/load watcher.
"""
import time
import traceback
import bpy
import gpu
import numpy as np
from mathutils import Matrix, Vector
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
from . import pattern_view as PV, overlay as OV, view_visibility as VV
from .core import silhouette as C
from .core import overlay_policy as OP
from .i18n import CTX, tx

_handles = []
_cache = {}
_shader = None
_active = None
stats = {'builds': 0, 'mask_builds': 0, 'draw_ms': 0.0, 'build_ms': 0.0, 'mask_build_ms': 0.0, 'error': None}


def sources(context):
    """Registered sources, independent of local collections in the flat view."""
    from . import dress
    p = context.scene.ac9_cloth_sim
    body = p.body
    if body is not None and (body.type != 'MESH' or context.scene.objects.get(body.name) != body):
        body = None
    others = tuple(ob for ob in dress.collider_objects(context) if ob != body)
    return body, others


def pattern_view(context):
    space = context.space_data
    layout = PV.layout_empty(context)
    if (not space or space.type != 'VIEW_3D' or layout is None
            or context.scene.objects.get(layout.name) != layout):
        return False
    rv = space.region_3d
    if not rv or rv.view_perspective != 'ORTHO' or not VV.allows_pattern(context):
        return False
    normal = PV.layout_matrix(context).inverted_safe().transposed().to_3x3() @ Vector((0, 0, 1))
    eye = rv.view_rotation @ Vector((0, 0, 1))
    return normal.normalized().dot(eye) > 0.999


def _read(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        v = np.empty((len(mesh.vertices), 3), np.float64)
        mesh.vertices.foreach_get('co', v.ravel())
        mesh.calc_loop_triangles()
        t = np.empty((len(mesh.loop_triangles), 3), np.int32)
        mesh.loop_triangles.foreach_get('vertices', t.ravel())
        m = np.asarray(evaluated.matrix_world, dtype=float)
        v = v @ m[:3, :3].T + m[:3, 3]
        if not np.isfinite(v).all():
            raise ValueError('Non-finite reference vertices: ' + obj.name)
        return v, t
    finally:
        evaluated.to_mesh_clear()


def snapshot(context):
    """Geometry-only cache; accessible headlessly for numerical verification."""
    body, others = sources(context)
    objects = ((body,) if body else ()) + others
    key = (context.scene.session_uid, context.view_layer.as_pointer(), context.scene.frame_current, OV._generation[0],
           tuple((ob.session_uid, ob.data.session_uid, OV.epoch(ob), OV.epoch(ob.data),
                  tuple(x for row in ob.matrix_world for x in row)) for ob in objects))
    if OV.watching() and _cache.get('key') == key:
        return _cache
    start = time.perf_counter()
    _free_masks()
    _cache.clear()
    dg = context.evaluated_depsgraph_get()
    meshes = {ob.session_uid: _read(ob, dg) for ob in objects}
    valid = [v for v, t in meshes.values() if len(v) and len(t)]
    if not valid:
        _cache.update(key=key, meshes=meshes, objects=objects, body=body, empty=True, masks={})
        return _cache
    allv = np.vstack(valid)
    ref = meshes[body.session_uid][0] if body is not None and len(meshes[body.session_uid][0]) else allv
    origin = C.frame(ref)
    columns = C.arrangement(allv, origin)
    height = float(np.ptp(ref[:, 2]))
    # Default placement aligns the torso with the middle of the flat pattern.
    inv = PV.layout_matrix(context).inverted_safe()
    corners = [inv @ (ob.matrix_world @ Vector(c))
               for ob in set(PV.piece_objects().values()) for c in ob.bound_box
               if context.scene.objects.get(ob.name) == ob]
    centre = (np.asarray(corners).min(0) + np.asarray(corners).max(0)) / 2 if corners else (0, 0, 0)
    default = (float(centre[0]), float(centre[1]) - height * 0.55)
    _cache.update(key=key, meshes=meshes, objects=objects, body=body, empty=False,
                  origin=origin, columns=columns, default=default, masks={})
    stats['builds'] += 1
    stats['build_ms'] = (time.perf_counter() - start) * 1000
    return _cache


def enabled_ids(context, data):
    p = context.scene.ac9_cloth_sim
    return tuple(ob.session_uid for ob in data['objects']
                 if (ob == data['body'] and p.show_silhouette_avatar)
                 or (ob != data['body'] and p.show_silhouette_collisions))


def _free_masks():
    for masks in _cache.get('masks', {}).values():
        for offscreen, _bounds in masks.values():
            try:
                offscreen.free()
            except (ReferenceError, RuntimeError):
                pass


def masks(context, data):
    ids = enabled_ids(context, data)
    if ids in data['masks']:
        return data['masks'][ids]
    out = {}
    if not ids:
        return out
    vertices, triangles, base = [], [], 0
    for uid in ids:
        v, t = data['meshes'][uid]
        vertices.append(v)
        triangles.append(t + base)
        base += len(v)
    v, t = np.vstack(vertices), np.vstack(triangles)
    if not len(t):
        return out
    start = time.perf_counter()
    blend, depth, write = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
    viewport = gpu.state.viewport_get()
    try:
        for direction in C.DIRECTIONS:
            p = C.project(v, data['origin'], direction)
            lo, hi = p.min(0), p.max(0)
            size = np.maximum(hi - lo, 0.001)
            # 2px padding prevents clipped edges; resolution never changes metric bounds.
            pad = size / 512
            lo, hi = lo - pad, hi + pad
            size = hi - lo
            wh = np.maximum(8, np.round(1024 * size / size.max())).astype(int)
            off = gpu.types.GPUOffScreen(int(wh[0]), int(wh[1]))
            out[direction] = (off, (lo, hi))
            q = (p - lo) / size * 2 - 1
            with off.bind():
                gpu.state.viewport_set(0, 0, int(wh[0]), int(wh[1]))
                gpu.state.active_framebuffer_get().clear(color=(0, 0, 0, 0))
                gpu.state.blend_set('NONE')
                gpu.state.depth_test_set('NONE')
                gpu.state.depth_mask_set(False)
                with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.matrix.load_identity()
                    gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
                    # GPU attr_fill consumes float32 buffers; float64 was read as
                    # unrelated coordinates (a full rectangle on 5.2.1).
                    batch = batch_for_shader(shader, 'TRIS', {'pos': np.column_stack((q, np.zeros(len(q)))).astype(np.float32)}, indices=t)
                    shader.bind()
                    shader.uniform_float('color', (1, 1, 1, 1))
                    batch.draw(shader)
        data['masks'][ids] = out
        stats['mask_builds'] += 1
        stats['mask_build_ms'] = (time.perf_counter() - start) * 1000
        return out
    except Exception:
        for off, _bounds in out.values():
            off.free()
        raise
    finally:
        gpu.state.viewport_set(*viewport)
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(write)


def _image_shader():
    global _shader
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo('ac9_silhouette_uv')
        interface.smooth('VEC2', 'uv')
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', 'mvp')
        info.sampler(0, 'FLOAT_2D', 'mask')
        info.vertex_in(0, 'VEC3', 'position')
        info.vertex_in(1, 'VEC2', 'texcoord')
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source('void main(){uv=texcoord;gl_Position=mvp*vec4(position,1.0);}')
        info.fragment_source('void main(){fragColor=vec4(0.45,0.45,0.45,0.18*texture(mask,uv).r);}')
        _shader = gpu.shader.create_from_info(info)
    return _shader


def draw():
    context = bpy.context
    p = getattr(context.scene, 'ac9_cloth_sim', None)
    if (not p or not p.show_overlays or not (p.show_silhouette_avatar or p.show_silhouette_collisions)
            or not pattern_view(context)):
        return
    start = time.perf_counter()
    blend, depth, write = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
    try:
        data = snapshot(context)
        if data['empty']:
            return
        shadow = masks(context, data)
        pos = p.silhouette_position if p.silhouette_positioned else data['default']
        if _active is not None and _active['area'] == context.area.as_pointer() and _active['pos'] is not None:
            pos = _active['pos']
        m = PV.layout_matrix(context)
        shader = _image_shader()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(False)
        for direction, (off, bounds) in shadow.items():
            world = [m @ Vector(v) for v in C.quad(bounds, pos, data['columns'][direction])]
            batch = batch_for_shader(shader, 'TRIS', {'position': world,
                                     'texcoord': ((0, 0), (1, 0), (1, 1), (0, 1))}, indices=((0, 1, 2), (0, 2, 3)))
            shader.bind()
            shader.uniform_float('mvp', context.region_data.perspective_matrix)
            shader.uniform_sampler('mask', off.texture_color)
            batch.draw(shader)
        stats['error'] = None
    except Exception as exc:
        if stats['error'] != str(exc):
            traceback.print_exc()
        stats['error'] = str(exc)
    finally:
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(write)
        stats['draw_ms'] = (time.perf_counter() - start) * 1000


def hit_position(context, region, xy):
    rv = context.space_data.region_3d
    m = PV.layout_matrix(context)
    ray = region_2d_to_vector_3d(region, rv, xy)
    eye = region_2d_to_origin_3d(region, rv, xy)
    normal = m.inverted_safe().transposed().to_3x3() @ Vector((0, 0, 1))
    denom = ray.dot(normal)
    if abs(denom) < 1e-10:
        return None
    point = eye + ray * ((m.translation - eye).dot(normal) / denom)
    local = m.inverted_safe() @ point
    return (float(local.x), float(local.y))


class AC9_OT_SilhouettePosition(bpy.types.Operator):
    bl_idname = 'ac9_cloth_sim.silhouette_position'
    bl_label = OP.label('silhouette_position')
    bl_description = OP.note('silhouette_position')
    bl_translation_context = CTX
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        p = getattr(context.scene, 'ac9_cloth_sim', None)
        return bool(_active is None and p and p.show_overlays and
                    (p.show_silhouette_avatar or p.show_silhouette_collisions) and pattern_view(context)
                    and any(sources(context)))

    def invoke(self, context, event):
        global _active
        region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        if region is None:
            return {'CANCELLED'}
        self._area = context.area
        self._window = context.window
        self._scene = context.scene
        self._region = region
        _active = {'area': context.area.as_pointer(), 'pos': None,
                   'window': context.window, 'owner_area': context.area}
        context.window.cursor_modal_set('CROSSHAIR')
        context.area.header_text_set(tx('Click to place silhouettes; Esc cancels'))
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _finish(self):
        global _active
        _active = None
        try:
            self._window.cursor_modal_restore()
            self._area.header_text_set(None)
            self._area.tag_redraw()
        except ReferenceError:
            pass

    def modal(self, context, event):
        if (_active is None or context.scene != self._scene or context.area != self._area
                or not pattern_view(context)):
            self._finish()
            return {'CANCELLED'}
        if event.type in {'ESC', 'RIGHTMOUSE', 'WINDOW_DEACTIVATE'}:
            self._finish()
            return {'CANCELLED'}
        r = self._region
        xy = event.mouse_x - r.x, event.mouse_y - r.y
        if not (0 <= xy[0] < r.width and 0 <= xy[1] < r.height):
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' or (event.type == 'LEFTMOUSE' and event.value == 'PRESS'):
            pos = hit_position(context, r, xy)
            if pos is not None:
                _active['pos'] = pos
                self._area.tag_redraw()
                if event.type == 'LEFTMOUSE':
                    p = self._scene.ac9_cloth_sim
                    p.silhouette_position = pos
                    p.silhouette_positioned = True
                    self._finish()
                    return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._finish()


def register():
    bpy.utils.register_class(AC9_OT_SilhouettePosition)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW'))


def unregister():
    global _shader, _active
    if _active is not None and _active.get('window') is not None:
        try:
            _active['window'].cursor_modal_restore()
            _active['owner_area'].header_text_set(None)
        except ReferenceError:
            pass
    for h in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _handles.clear()
    _free_masks()
    _cache.clear()
    _shader = None
    _active = None
    bpy.utils.unregister_class(AC9_OT_SilhouettePosition)
