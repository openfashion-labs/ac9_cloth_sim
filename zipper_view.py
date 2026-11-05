# SPDX-License-Identifier: GPL-3.0-or-later
"""The zippers drawn on the dressed garment (user 2026-10-15, DECISIONS cloth-sim-fasteners-alpha-2026-10-15: the
teeth a texture, to be swapped for a 3D shape later).

Each side of a zipper is a strip WIDTH_MM wide along its edges, inside its piece: the object AC9_Zippers_<garment>,
whose vertices carry their layout point as ac9_uv. The Geometry Nodes group AC9_ZipperPlace puts each vertex on the
garment at that point (Sample UV Surface on the garment's UV map, the layout mm / 1000, as the buttons are), lifted
LIFT_M along the cloth's blurred normal turned away from the body -- every vertex pinned to its own cloth point, so
the strip follows the drape frame by frame and never jumps to another layer. The strip's texture: the tape, and the
teeth on its outer half (the edge's side), one every TOOTH_MM, the two sides offset by half a tooth so that they
interlock where the zipper is closed. Open or closed is the cloth's: the strips go where their edges go.
"""
import os

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from . import button_view as BV
from .core import zippers as ZP

PLACE = "AC9_ZipperPlace"
MAT = "AC9_Zipper"
IMG = "AC9_ZipperTeeth"
WIDTH_MM = 6.0                # the tape and teeth seen beside the edge
EDGE_IN_MM = 0.5              # the strip's outer line just inside the piece (Sample UV Surface at the very edge misses)
STEP_MM = 2.0
TOOTH_MM = 3.5
LIFT_M = 0.0008
GROUP_V = 2


def points_name(garment_name):
    return f"AC9_Zippers_{garment_name}"


def place_group():
    ng = bpy.data.node_groups.get(PLACE)
    if ng is not None and ng.get('ac9_v') == GROUP_V:
        return ng
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(PLACE, 'GeometryNodeTree')
    ng['ac9_v'] = GROUP_V
    n_, l_ = BV._n, BV._l
    BV._sock(ng, "Geometry", 'NodeSocketGeometry')
    BV._sock(ng, "Garment", 'NodeSocketObject')
    BV._sock(ng, "Lift", 'NodeSocketFloat', default=LIFT_M)
    BV._sock(ng, "Geometry", 'NodeSocketGeometry', 'OUTPUT')
    gi = n_(ng, 'NodeGroupInput', -1000, 0)
    go = n_(ng, 'NodeGroupOutput', 900, 0)
    oi = n_(ng, 'GeometryNodeObjectInfo', -800, 200, transform_space='RELATIVE')
    l_(ng, gi.outputs['Garment'], oi.inputs['Object'])
    cloth = BV._cloth_only(ng, oi.outputs['Geometry'], -600, 500)
    uvmap = n_(ng, 'GeometryNodeInputNamedAttribute', -800, 0, data_type='FLOAT_VECTOR')
    uvmap.inputs['Name'].default_value = 'UVMap'
    suv = n_(ng, 'GeometryNodeInputNamedAttribute', -800, -150, data_type='FLOAT_VECTOR')
    suv.inputs['Name'].default_value = 'ac9_uv'
    pos = n_(ng, 'GeometryNodeInputPosition', -800, -300)
    nin = n_(ng, 'GeometryNodeInputNormal', -800, -450)
    blur = n_(ng, 'GeometryNodeBlurAttribute', -600, -450, data_type='FLOAT_VECTOR')
    l_(ng, nin.outputs[0], blur.inputs[0])
    blur.inputs['Iterations'].default_value = BV.BLUR_ITER

    def sample(val, y):
        s = n_(ng, 'GeometryNodeSampleUVSurface', -350, y, data_type='FLOAT_VECTOR')
        l_(ng, cloth, s.inputs['Mesh'])
        l_(ng, val, s.inputs['Value'])
        l_(ng, uvmap.outputs[0], s.inputs['UV Map'])
        l_(ng, suv.outputs[0], s.inputs['Sample UV'])
        return s.outputs['Value']
    P = sample(pos.outputs[0], 100)
    N = sample(blur.outputs[0], -300)
    out = n_(ng, 'GeometryNodeInputNamedAttribute', -350, -550, data_type='FLOAT_VECTOR')
    out.inputs['Name'].default_value = 'ac9_out'
    dot = n_(ng, 'ShaderNodeVectorMath', -100, -400, operation='DOT_PRODUCT')
    l_(ng, N, dot.inputs[0])
    l_(ng, out.outputs[0], dot.inputs[1])
    nn = n_(ng, 'ShaderNodeVectorMath', 0, -300, operation='NORMALIZE')
    l_(ng, N, nn.inputs[0])
    sg = BV._math(ng, 'SIGN', dot.outputs['Value'], None, 0, -500)
    lift = BV._math(ng, 'MULTIPLY', sg, None, 150, -500)
    l_(ng, gi.outputs['Lift'], lift.node.inputs[1])
    off = n_(ng, 'ShaderNodeVectorMath', 300, -300, operation='SCALE')
    l_(ng, nn.outputs[0], off.inputs[0])
    l_(ng, lift, off.inputs['Scale'])
    at = n_(ng, 'ShaderNodeVectorMath', 450, -100, operation='ADD')
    l_(ng, P, at.inputs[0])
    l_(ng, off.outputs[0], at.inputs[1])
    sp = n_(ng, 'GeometryNodeSetPosition', 650, 0)
    l_(ng, gi.outputs['Geometry'], sp.inputs['Geometry'])
    l_(ng, at.outputs[0], sp.inputs['Position'])
    l_(ng, sp.outputs[0], go.inputs[0])
    return ng


def material():
    """The tape with the teeth on its outer half: an image (u across the strip, 0 inside .. 1 at the edge; v along it,
    one tooth per 1)."""
    img = bpy.data.images.get(IMG)
    if img is None:
        W = H = 64
        u = (np.arange(W) + 0.5) / W
        v = (np.arange(H) + 0.5) / H
        U, V = np.meshgrid(u, v)
        tape = np.array([0.05, 0.05, 0.055, 1.0])
        tooth = np.array([0.62, 0.62, 0.64, 1.0])
        shade = np.array([0.30, 0.30, 0.32, 1.0])
        px = np.tile(tape, (H, W, 1))
        teeth = (U > 0.5) & (np.abs(V - 0.5) < 0.32)
        px[teeth] = tooth
        px[teeth & ((U > 0.93) | (np.abs(V - 0.5) > 0.26))] = shade
        img = bpy.data.images.new(IMG, W, H, alpha=True)
        img.pixels.foreach_set(px.astype(np.float32).ravel())
        img.pack()
        img.use_fake_user = True
    m = bpy.data.materials.get(MAT)
    if m is None:
        m = bpy.data.materials.new(MAT)
        m.use_nodes = True
        nt = m.node_tree
        bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
        tx = nt.nodes.new('ShaderNodeTexImage')
        tx.image = img
        tx.interpolation = 'Closest'
        at = nt.nodes.new('ShaderNodeAttribute')
        at.attribute_name = 'ac9_zip_tex'
        nt.links.new(at.outputs['Vector'], tx.inputs['Vector'])
        nt.links.new(tx.outputs['Color'], bsdf.inputs['Base Color'])
        bsdf.inputs['Metallic'].default_value = 0.3
        bsdf.inputs['Roughness'].default_value = 0.45
        m.diffuse_color = (0.15, 0.15, 0.16, 1.0)
    return m


def _inset_chain(P, poly, d):
    """Polyline P moved d mm into the closed outline poly."""
    area = 0.5 * np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1])
    t = np.gradient(P, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    n_in = np.stack([-t[:, 1], t[:, 0]], 1) * (1.0 if area > 0 else -1.0)
    return P + n_in * d


def strips(spec, z):
    """[(piece, outer (n, 2) mm, inner (n, 2) mm, v (n,) teeth, side)] for zipper z: each side bottom to top, the
    v of side b offset by half a tooth."""
    from .core import spec as S
    out = []
    for k, sd in enumerate(('a', 'b')):
        piece = z[sd]['piece']
        pc = S.piece_index(spec)[piece]
        poly = np.vstack([np.asarray(e['pts'], float)[:-1] for e in pc['edges']])
        # inset in the outline's own order (the left of a CCW outline is inside), then bottom first: turning the
        # polyline round first turned its inside out (one side's strip lay outside its piece, nothing sampled there)
        P = ZP._chain(spec, piece, z[sd]['edges'])
        cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
        s = np.linspace(EDGE_IN_MM, cum[-1] - EDGE_IN_MM, max(2, int(cum[-1] / STEP_MM) + 1))     # the ends inside too
        Q = np.stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])], 1)
        Qo, Qi = _inset_chain(Q, poly, EDGE_IN_MM), _inset_chain(Q, poly, WIDTH_MM)
        if Q[0, 1] > Q[-1, 1]:
            Qo, Qi = Qo[::-1], Qi[::-1]
        out.append((piece, Qo, Qi, s / TOOTH_MM + 0.5 * k, sd))
    return out


def sync(context, spec, g, job=None, X_mm=None):
    """Make the zippers' strip object of garment `g` match the spec (remove it when there are none)."""
    pname = points_name(g.name) if g is not None else None
    old = bpy.data.objects.get(pname) if pname else None
    if g is None or not spec.get('zippers') or not job or not os.path.exists(os.path.join(job, 'scene.idx.npz')):
        if old is not None:
            me = old.data
            bpy.data.objects.remove(old)
            if me.users == 0:
                bpy.data.meshes.remove(me)
        return None
    import json
    idx = np.load(os.path.join(job, 'scene.idx.npz'))
    names = [str(n) for n in json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))['names']]
    if X_mm is None:
        from . import dress as DR
        X_mm = DR.job_last_frame(job) * 1000.0
    bvh = None
    av_p = os.path.join(job, 'avatar.npz')
    if os.path.exists(av_p):
        av = np.load(av_p)
        bvh = BVHTree.FromPolygons(av['body_co'].tolist(), av['body_tri'].tolist(), all_triangles=True)
    flat, F, fp = idx['flat'], idx['F'], idx['fp']

    def out_at(piece, pt):
        t, w = BV._locate(flat, F, fp, names.index(piece), pt)
        a, b, c = X_mm[F[t]]
        n = np.cross(b - a, c - a)
        n = n / max(np.linalg.norm(n), 1e-12)
        p = w @ X_mm[F[t]]
        if bvh is None:
            return n
        hit = bvh.find_nearest((p * 0.001).tolist())
        if hit[0] is None:
            return n
        o = p * 0.001 - np.array(hit[0])
        return o / max(np.linalg.norm(o), 1e-12)

    verts, faces, uvs, outs, tex = [], [], [], [], []
    for z in spec['zippers']:
        if z['a']['piece'] not in names or z['b']['piece'] not in names:
            continue
        for piece, Po, Pi, v, _sd in strips(spec, z):
            n = len(Po)
            base = len(verts)
            for k in range(n):
                for q, u in ((Pi[k], 0.0), (Po[k], 1.0)):
                    verts.append((0.0, 0.0, 0.0))
                    uvs.append((q[0] / 1000.0, q[1] / 1000.0, 0.0))
                    outs.append(out_at(piece, q))
            for k in range(n - 1):
                a = base + 2 * k
                faces.append((a, a + 1, a + 3, a + 2))
                tex.extend([(0.0, v[k]), (1.0, v[k]), (1.0, v[k + 1]), (0.0, v[k + 1])])
    if not faces:
        return None
    me = bpy.data.meshes.new(pname)
    me.from_pydata(verts, [], faces)
    me.update()
    a_ = me.attributes.new('ac9_uv', 'FLOAT_VECTOR', 'POINT')
    a_.data.foreach_set('vector', np.asarray(uvs, np.float32).ravel())
    o_ = me.attributes.new('ac9_out', 'FLOAT_VECTOR', 'POINT')
    o_.data.foreach_set('vector', np.asarray(outs, np.float32).ravel())
    t_ = me.attributes.new('ac9_zip_tex', 'FLOAT2', 'CORNER')
    t_.data.foreach_set('vector', np.asarray(tex, np.float32).ravel())
    me.materials.append(material())
    if old is None:
        old = bpy.data.objects.new(pname, me)
        context.scene.collection.objects.link(old)
    else:
        m_old = old.data
        old.data = me
        if m_old.users == 0:
            bpy.data.meshes.remove(m_old)
    old.matrix_world = g.matrix_world
    old['ac9_zippers_of'] = g.name
    from . import view_setup
    view_setup.ensure_accessory(context, old, g, spec)
    view_setup.refresh(context)
    mod = old.modifiers.get("AC9 Zippers") or old.modifiers.new("AC9 Zippers", 'NODES')
    ng = place_group()
    mod.node_group = ng
    for item in ng.interface.items_tree:
        if getattr(item, 'in_out', '') == 'INPUT' and item.name == 'Garment':
            BV._set_input(mod, item.identifier, g)
    old.hide_select = True
    return old
