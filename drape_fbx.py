"""Read a 3D drape FBX for core/bom_drape.py: import it, copy its meshes out as arrays, and remove everything the
import made (objects, meshes, materials, images, armatures, actions, collections) so the scene is as it was."""
import os

import bpy
import numpy as np

_KINDS = ('objects', 'meshes', 'materials', 'images', 'armatures', 'actions', 'collections', 'textures',
          'node_groups', 'cameras', 'lights', 'curves')


def find_beside(bom_path):
    """The one FBX in the BOM's folder, or '' (none, or several: which one is not known)."""
    folder = os.path.dirname(bom_path)
    try:
        found = [f for f in os.listdir(folder) if f.lower().endswith('.fbx')]
    except OSError:
        return ''
    return os.path.join(folder, found[0]) if len(found) == 1 else ''


def read_meshes(path):
    """[{'co' (V, 3) m, 'tri' (T, 3), 'uv' (T, 3, 2)}] of every mesh in the FBX with a UV map (world space)."""
    if not os.path.isfile(path):
        raise ValueError(path)
    before = {k: set(getattr(bpy.data, k)) for k in _KINDS}
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    selected = [ob for ob in view_layer.objects if ob.select_get()]
    out = []
    try:
        bpy.ops.import_scene.fbx(filepath=path)
        for ob in set(bpy.data.objects) - before['objects']:
            if ob.type != 'MESH' or not ob.data.uv_layers:
                continue
            me = ob.data
            me.calc_loop_triangles()
            n_t, n_v = len(me.loop_triangles), len(me.vertices)
            if not n_t:
                continue
            co = np.empty(n_v * 3, np.float32)
            me.vertices.foreach_get('co', co)
            M = np.array(ob.matrix_world, float)
            co = co.reshape(-1, 3).astype(float) @ M[:3, :3].T + M[:3, 3]
            tv = np.empty(n_t * 3, np.int32)
            me.loop_triangles.foreach_get('vertices', tv)
            tl = np.empty(n_t * 3, np.int32)
            me.loop_triangles.foreach_get('loops', tl)
            uv = np.empty(len(me.loops) * 2, np.float32)
            me.uv_layers.active.data.foreach_get('uv', uv)
            uv = uv.reshape(-1, 2).astype(float)
            out.append({'co': co, 'tri': tv.reshape(-1, 3), 'uv': uv[tl].reshape(-1, 3, 2)})
    finally:
        for k in _KINDS:
            coll = getattr(bpy.data, k)
            for idb in list(set(coll) - before[k]):
                try:
                    coll.remove(idb)
                except (ReferenceError, RuntimeError):
                    pass
        for ob in view_layer.objects:
            ob.select_set(ob in selected)
        if active is not None and active.name in view_layer.objects:
            view_layer.objects.active = active
    return out
