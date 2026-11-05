# SPDX-License-Identifier: GPL-3.0-or-later
"""From Mesh: a garment modeled in 3D, cut with UV seams, becomes the pattern (core/mesh_to_spec.py).

The pieces are not placed round the body: the modeled mesh itself is written as the last result (a job without
simulation, as draft_tool.carry_to_3d writes one), so Simulate goes on from the modeled shape -- Continue meshes the
pattern at the Mesh Size and carries the shape onto it (design review 2026-10-09, model3d/DESIGN_mesh_to_pattern §8).
Every piece still gets a placement on the nearest guide: prepare() wants one, and Dress from the start uses it.
Cloth that starts inside the body is measured, logged and painted red, not refused (§8.3).
"""
import json
import os
import time
from .i18n import rpt, tx

import bpy
import numpy as np

from . import pattern_view as PV
from . import placement as PL
from . import store
from .core import body_axes as BA
from .core import mesh_to_spec as M2S
from .core import spec as S


def _mesh_arrays(context, ob):
    """World positions (mm), triangles and their corner UVs of the object as shown (its modifiers applied)."""
    dg = context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        if not me.uv_layers.active:
            raise M2S.MeshError("the mesh has no UV map: mark seams and unwrap it first")
        me.calc_loop_triangles()
        n = len(me.vertices)
        co = np.empty(n * 3, np.float64)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        M = np.array(ob.matrix_world)
        co = (np.c_[co, np.ones(n)] @ M.T)[:, :3] * 1000.0
        nt = len(me.loop_triangles)
        tri = np.empty(nt * 3, np.int64)
        me.loop_triangles.foreach_get('vertices', tri)
        lp = np.empty(nt * 3, np.int64)
        me.loop_triangles.foreach_get('loops', lp)
        uv = np.empty(len(me.loops) * 2, np.float64)
        me.uv_layers.active.data.foreach_get('uv', uv)
        uv = uv.reshape(-1, 2)[lp].reshape(nt, 3, 2)
        return co, tri.reshape(-1, 3), uv
    finally:
        ev.to_mesh_clear()


def _inside_body(context, body, X):
    """Indices of garment points (mm) inside the body."""
    from . import dress as DR
    co, tri = PL.body_arrays(context, body)
    bvh = DR.body_bvh(co, tri)
    out = []
    for i, x in enumerate(X * 0.001):
        hit = bvh.find_nearest(x.tolist())
        if hit[0] is not None and np.dot(x - np.array(hit[0]), np.array(hit[1])) < 0.0:
            out.append(i)
    return out


def write_job(context, spec, job):
    """The modeled garment as a finished job (pattern.npz, scene.idx.npz, s1/frames.npy, ac9_spec.json, job.json)
    and the garment object showing it. Returns the job folder."""
    from . import dress as DR
    d = DR.job_dir(context)
    os.makedirs(os.path.join(d, 's1'), exist_ok=True)
    names = job['names']
    pat = dict(job['V'], names=np.array(names), nb=job['nb'])
    np.savez(os.path.join(d, 'pattern.npz'), **pat)
    np.savez(os.path.join(d, 'scene.idx.npz'), offs=job['offs'], F=job['F'], fp=job['fp'], flat=job['flat'],
             Xinit=job['X'])
    np.save(os.path.join(d, 's1', 'frames.npy'), (job['X'][None] * 0.001).astype(np.float32))
    open(os.path.join(d, 'ac9_spec.json'), 'w', encoding='utf-8').write(S.dumps(spec))
    json.dump({'kind': 'from mesh', 'source': spec['source'].get('object', ''), 'names': names,
               'stages': ['from mesh'], 'verts': int(len(job['flat'])), 'tris': int(len(job['F']))},
              open(os.path.join(d, 'job.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    ob = DR.garment_object(context, DR.garment_name(spec), job['flat'], job['F'], job['fp'], job['X'], names)
    ob['ac9_job'] = d
    DR.refresh_thickness(context, ob, spec)
    return d, ob


class AC9_OT_SimFromMesh(bpy.types.Operator):
    """Make the pattern from the active mesh: each UV island is a piece, edges shared by two islands are seams. The garment starts as modeled (Phase Sim settles it). Model it single-layered and open, as shown (modifiers applied); pick the body first"""
    bl_idname = "ac9_cloth_sim.from_mesh"
    bl_label = "From Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        body = context.scene.ac9_cloth_sim.body
        return ob is not None and ob.type == 'MESH' and body is not None and ob != body

    def execute(self, context):
        from . import dress as DR
        t0 = time.time()
        ob = context.active_object
        props = context.scene.ac9_cloth_sim
        try:
            co, tri, uv = _mesh_arrays(context, ob)
            bco, btri = PL.body_arrays(context, props.body)
            gd, m = BA.guides(BA.Body(bco, btri))
            guides = BA.to_json(gd)
            spec, job, rep = M2S.convert(ob.name, co, tri, uv, guides)
        except M2S.MeshError as exc:
            self.report({'ERROR'}, rpt("From Mesh: {0}", exc))
            return {'CANCELLED'}
        except Exception as exc:
            self.report({'ERROR'}, rpt("From Mesh failed: {0}", exc))
            return {'CANCELLED'}
        # the guides as Detect Guides writes them (placement.AC9_OT_SimDetectGuides)
        spec['guides'] = guides
        spec['body'] = {'object': props.body.name, 'z_crotch': m['z_crotch'], 'z_armpit': m['z_armpit'],
                        'z_neck': m['z_neck'], 'torso_half_width_armpit': m['torso_half_width_armpit']}
        store.new_text(context, spec['name'])
        store.put(context, spec)
        spec, outlines = store.get(context)
        PV.rebuild(context, spec, outlines)
        d, g = write_job(context, spec, job)
        inside = _inside_body(context, props.body, job['X'])
        if inside:
            from . import diagnose as DG        # red in the Overlays' Problems (a color attribute until 2026-11-02)
            DG.mark(g, 'inside', inside, f"{len(inside)} vertices inside the body")
        ob.hide_set(True)
        secs = round(time.time() - t0, 2)
        for line in rep:
            DR.log(context, d, 'from_mesh', line)
        msg = (tx("From Mesh: {0} pieces, {1} seams in {2} s; ", len(spec['pieces']), len(spec['seams']), secs)
               + (tx("{0} points start inside the body (red)", len(inside)) if inside else tx("none inside the body"))
               + tx(". Phase Sim to settle it"))
        DR.log(context, d, 'from_mesh', msg, inside=len(inside), seconds=secs)
        props.dress_status = msg
        self.report({'WARNING'} if inside else {'INFO'}, msg)
        return {'FINISHED'}


CLASSES = (AC9_OT_SimFromMesh,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
