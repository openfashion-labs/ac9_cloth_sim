# SPDX-License-Identifier: GPL-3.0-or-later
"""Hand the dressed garment to AC9 Cloth Retopo as its Guide.

What Cloth Retopo needs of a Guide (read from its code, 2026-09-28): a triangulated mesh with its transform
applied, the pattern islands not welded, a ShapeKey '<uv>_Flattened' holding (u, v, 0) per vertex, and seam
pairs that coincide in 3D -- it finds which boundary edges are sewn by grouping them on their 3D positions rounded
to 0.1 mm. So the Guide here is the last frame with every stitched pair moved onto its midpoint (a Newton drape
leaves them up to a few mm apart), UV = the flat pattern at real scale in meters (the flat-to-real ratio Cloth
Retopo measures is then 1), Basis = the drape, 'UVMap_Flattened' = the flat pattern.
"""
import json
import os
from .i18n import rpt

import bpy
import numpy as np

from . import dress as D
from . import store

GUIDE_PREFIX = "AC9_Guide_"
FLAT_SK = "UVMap_Flattened"


def last_frame(job_dir, job):
    """The last frame written (a job stopped on an anomaly has no frames for its later stages)."""
    X = D.job_last_frame(job_dir)                 # final.npy once its frames were dropped
    if X is None:
        raise ValueError("The simulation job has no frames")
    return X


def weld_stitches(X, E):
    """Move every stitched vertex onto the mean of the vertices it is sewn to (connected groups: a corner where
    three pieces meet goes to one point)."""
    parent = np.arange(len(X))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in E:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in range(len(X))])
    Y = X.copy()
    touched = np.unique(E.ravel())
    for r in np.unique(roots[touched]):
        g = np.nonzero(roots == r)[0]
        Y[g] = X[g].mean(0)
    return Y, float(np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1).max())


def _boundary_adjacency(F):
    ed = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1)
    u, c = np.unique(ed, axis=0, return_counts=True)
    adj = {}
    for a, b in u[c == 1]:
        adj.setdefault(int(a), []).append(int(b))
        adj.setdefault(int(b), []).append(int(a))
    return adj


def _between(adj, a, b, limit=6):
    """The outline vertices strictly between a and b (the short way round, at most limit steps), from a."""
    if b in adj.get(a, []):
        return []
    prev, q = {a: None}, [a]
    for _ in range(limit):
        nq = []
        for x in q:
            for y in adj.get(x, []):
                if y not in prev:
                    prev[y] = x
                    nq.append(y)
        q = nq
        if b in prev:
            path, y = [], prev[b]
            while y is not None and y != a:
                path.append(y)
                y = prev[y]
            return path[::-1]
    return None


def weld_between(Y, E, seam, F):
    """The outline vertices between two stitches are not sewn (the outline is filled at <= h between seam samples
    a little more than h apart): pair them in order with the other side's and weld them too; where the two sides
    differ in count, lay them on the straight piece between the two welded stitches. Without this, 136 of the
    jacket-with-collar Guide's 1322 outline edges met their other side (Cloth Retopo pairs seam edges on positions
    rounded to 0.1 mm), with it 880; vertices move at most 0.5 mm (2026-10-06)."""
    Y = Y.copy()
    adj = _boundary_adjacency(F)
    for sid in np.unique(seam):
        m = np.nonzero(seam == sid)[0]
        A, B = E[m, 0], E[m, 1]
        for k in range(len(m) - 1):
            pa = _between(adj, int(A[k]), int(A[k + 1]))
            pb = _between(adj, int(B[k]), int(B[k + 1]))
            if pa is None or pb is None:
                continue
            if len(pa) == len(pb):
                for va, vb in zip(pa, pb):
                    Y[[va, vb]] = (Y[va] + Y[vb]) / 2
            else:
                for p, (s0, s1) in ((pa, (A[k], A[k + 1])), (pb, (B[k], B[k + 1]))):
                    for i, v in enumerate(p):
                        t = (i + 1) / (len(p) + 1)
                        Y[v] = Y[s0] * (1 - t) + Y[s1] * t
    return Y


THICK_ATTR = 'ac9_thickness_mm'                  # per face: its fabric's thickness in mm (0: none)


def build_guide(context, garment):
    job_dir = garment.get('ac9_job')
    if not job_dir or not os.path.exists(os.path.join(job_dir, 'job.json')):
        raise ValueError("The garment has no finished simulation job")
    job = json.load(open(os.path.join(job_dir, 'job.json'), encoding='utf-8'))
    idx = np.load(os.path.join(job_dir, 'scene.idx.npz'))
    X = last_frame(job_dir, job)
    shown = D.shown_shape(context, garment) / 1000.0       # the frame on screen, or the hand-fixed shape
    if len(shown) == len(X):
        X = shown
    E = idx['E_st']
    Y, gap_before = weld_stitches(X, E)
    if 'E_seam' in idx.files:
        Y = weld_between(Y, E, idx['E_seam'], idx['F'].astype(np.int64))
    flat_m = idx['flat'] / 1000.0
    uv = flat_m - flat_m.min(0)
    name = GUIDE_PREFIX + garment.name[len(D.GARMENT_PREFIX):]
    old = bpy.data.objects.get(name)
    if old is not None:
        me_old = old.data
        bpy.data.objects.remove(old)
        if me_old.users == 0:
            bpy.data.meshes.remove(me_old)
    me = bpy.data.meshes.new(name)
    me.from_pydata(Y.tolist(), [], idx['F'].tolist())
    me.update()
    uvl = me.uv_layers.new(name='UVMap')
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', lv)
    uvl.data.foreach_set('uv', uv[lv].astype(np.float32).ravel())
    a = me.attributes.new('ac9_piece', 'INT', 'FACE')
    a.data.foreach_set('value', idx['fp'].astype(np.int32))
    ob = bpy.data.objects.new(name, me)
    context.scene.collection.objects.link(ob)
    ob.shape_key_add(name='Basis', from_mix=False)
    sk = ob.shape_key_add(name=FLAT_SK, from_mix=False)
    sk.data.foreach_set('co', np.c_[uv, np.zeros(len(uv))].astype(np.float32).ravel())
    ob['ac9_piece_names'] = json.dumps(job['names'], ensure_ascii=False)
    ob['ac9_job'] = job_dir
    # the pieces' looks, and each face's fabric thickness as a value only (the Guide stays thin, user 2026-10-21:
    # Cloth Retopo works on a thin cloth; reading the value to give the result its thickness is Cloth Retopo's, not
    # made yet). The UV keeps its 0-based place (Cloth Retopo's flat space): a texture lies shifted from the garment's
    from . import look_apply as LA
    from .core import fabric as FA
    spec = store.get(context)[0]
    if spec is not None:
        LA.apply_garment(ob, spec)
        th = np.array([FA.thickness(FA.resolved(spec, n)) or 0.0 for n in job['names']], np.float32)
        if th.any():
            ta = me.attributes.new(THICK_ATTR, 'FLOAT', 'FACE')
            ta.data.foreach_set('value', th[idx['fp'].astype(np.int64)])
    top = getattr(context.scene, 'ac9_cloth_retopo', None)
    linked = False
    if top is not None:
        try:
            top.guide_obj = ob
            top.guide_flat_shapekey = FLAT_SK
            linked = True
        except Exception:
            linked = False
    return ob, {'stitch_gap_before_mm': round(gap_before * 1000.0, 3), 'linked_to_retopo': linked,
                'verts': len(Y), 'tris': int(len(idx['F']))}


def _garment(context):
    from . import store
    spec, _ = store.get(context)
    return bpy.data.objects.get(D.garment_name(spec)) if spec else None


class AC9_OT_SimExportRetopo(bpy.types.Operator):
    """Make an AC9 Cloth Retopo Guide from the dressed garment (seams closed exactly, flat ShapeKey added, set as the Guide if Cloth Retopo is on)"""
    bl_idname = "ac9_cloth_sim.export_retopo"
    bl_label = "To AC9 Cloth Retopo"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = _garment(context)
        return ob is not None and bool(ob.get('ac9_job'))

    def execute(self, context):
        try:
            ob, info = build_guide(context, _garment(context))
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt("{0}: {1} verts; seams closed (were up to {2:.1f} mm apart)", ob.name, info['verts'], info['stitch_gap_before_mm'])
                    + (rpt("; set as the Cloth Retopo Guide") if info['linked_to_retopo'] else ""))
        return {'FINISHED'}


CLASSES = (AC9_OT_SimExportRetopo,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
