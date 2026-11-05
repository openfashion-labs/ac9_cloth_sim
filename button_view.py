# SPDX-License-Identifier: GPL-3.0-or-later
"""The buttons drawn on the dressed garment (user 2026-10-15, DECISIONS cloth-sim-fasteners-alpha-2026-10-15).

Each button is a point of the object AC9_Buttons_<garment> with its layout point as the attribute ac9_uv; the
Geometry Nodes group AC9_ButtonPlace finds that point on the garment by the garment's UV map (the layout, mm / 1000,
dress.garment_object) with Sample UV Surface -- so the button follows the drape cache frame by frame -- and puts the
style's shape there, turned to the cloth's normal and the piece's layout x. A fastened button is drawn at its hole
(it comes through it, on top of the hole's piece); an unfastened one on its own piece, with the buttonhole drawn as
a slit where the hole is.

On the flat pattern (Pattern View) the same shapes stand on each button's own point and every hole is drawn as its
slit, fastened or not: the object AC9_Buttons_Pattern, a child of the layout Empty (sync_flat).

The shapes: one object per style in the collection AC9_ButtonStyles (kept with a fake user, not in the scene), each
the group AC9_ButtonShape with the style's values (core/button_shape.py) -- the values a Button Creator will edit.
"""
import json
import math
import os

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from .core import button_shape as BS

COLL = "AC9_ButtonStyles"
SHAPE = "AC9_ButtonShape"
PLACE = "AC9_ButtonPlace"
FLAT = "AC9_ButtonFlat"
FLAT_OBJ = "AC9_Buttons_Pattern"
HOLE_OBJ = "AC9_BtnStyle_zz_hole"
MAT = "AC9_Button"
MAT_HOLE = "AC9_Buttonhole"
LIFT_M = 0.0006
BLUR_ITER = 12                # the cloth normal's blur (neighbor steps, ~6 mm mesh)
OUT_LEAN = 0.5                # the body's outside mixed into it
NEAR_M = 0.004                # an outer layer this near its own cloth takes the button (a fold); further out
                              # it is another part of the garment passing in front: the button stays put
GROUP_V = 7                  # the node groups' version: a blend holding an older one gets it rebuilt


def points_name(garment_name):
    return f"AC9_Buttons_{garment_name}"


# ---- node helpers -------------------------------------------------------------------------------------------
def _n(ng, t, x=0, y=0, **props):
    n = ng.nodes.new(t)
    n.location = (x, y)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def _set_input(mod, ident, v):
    """A Geometry Nodes modifier's input value: Blender 5.2 keeps them in mod.properties.inputs (no ID properties
    on the modifier), older versions as mod[identifier]."""
    props = getattr(mod, 'properties', None)
    if props is not None and hasattr(props, 'inputs'):
        getattr(props.inputs, ident).value = v
    else:
        mod[ident] = v


def _l(ng, a, b):
    ng.links.new(a, b)


def _sock(ng, name, kind, io='INPUT', default=None, lo=None):
    s = ng.interface.new_socket(name, in_out=io, socket_type=kind)
    if default is not None:
        s.default_value = default
    if lo is not None:
        s.min_value = lo
    return s


def _math(ng, op, a, b=None, x=0, y=0):
    m = _n(ng, 'ShaderNodeMath', x, y, operation=op)
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            m.inputs[i].default_value = v
        else:
            _l(ng, v, m.inputs[i])
    return m.outputs[0]


def _cloth_only(ng, geo, x=0, y=0):
    """The garment's geometry without the copy its drawn thickness adds (dress.show_thickness marks it in a vertex
    group; the rim goes with it): the copy carries the cloth's UV again, and Sample UV Surface on the doubled UV put
    every button at the origin (user 2026-10-23). Not the rim group: it holds the cloth's own edge vertices too, and
    the narrow placket the buttons sit on lost every face. Without a thickness the group is not there and nothing is
    removed."""
    a = _n(ng, 'GeometryNodeInputNamedAttribute', x - 400, y - 150, data_type='FLOAT')
    a.inputs['Name'].default_value = "AC9_ThicknessShell"
    d = _n(ng, 'GeometryNodeDeleteGeometry', x, y, domain='POINT')
    _l(ng, geo, d.inputs['Geometry'])
    _l(ng, _math(ng, 'GREATER_THAN', a.outputs['Attribute'], 0.5, x - 150, y - 200), d.inputs['Selection'])
    return d.outputs['Geometry']


def _xyz(ng, x_=0.0, y_=0.0, z_=0.0, x=0, y=0):
    c = _n(ng, 'ShaderNodeCombineXYZ', x, y)
    for i, v in enumerate((x_, y_, z_)):
        if isinstance(v, (int, float)):
            c.inputs[i].default_value = v
        else:
            _l(ng, v, c.inputs[i])
    return c.outputs[0]


def _xform(ng, geo, tr=None, rot=None, sc=None, x=0, y=0):
    t = _n(ng, 'GeometryNodeTransform', x, y)
    _l(ng, geo, t.inputs['Geometry'])
    if tr is not None:
        _l(ng, tr, t.inputs['Translation']) if not isinstance(tr, tuple) else setattr(t.inputs['Translation'], 'default_value', tr)
    if rot is not None:
        t.inputs['Rotation'].default_value = rot
    if sc is not None:
        _l(ng, sc, t.inputs['Scale']) if not isinstance(sc, tuple) else setattr(t.inputs['Scale'], 'default_value', sc)
    return t.outputs[0]


def _switch(ng, cond, geo_true, x=0, y=0):
    s = _n(ng, 'GeometryNodeSwitch', x, y, input_type='GEOMETRY')
    _l(ng, cond, s.inputs['Switch'])
    _l(ng, geo_true, s.inputs['True'])
    return s.outputs[0]


def _torus(ng, R, r, x=0, y=0):
    """A torus round z: ring radius R, tube radius r (sockets or numbers)."""
    c = _n(ng, 'GeometryNodeCurvePrimitiveCircle', x, y)
    c.inputs['Resolution'].default_value = 48
    p = _n(ng, 'GeometryNodeCurvePrimitiveCircle', x, y - 150)
    p.inputs['Resolution'].default_value = 12
    for node, v in ((c, R), (p, r)):
        if isinstance(v, (int, float)):
            node.inputs['Radius'].default_value = v
        else:
            _l(ng, v, node.inputs['Radius'])
    m = _n(ng, 'GeometryNodeCurveToMesh', x + 200, y)
    _l(ng, c.outputs['Curve'], m.inputs['Curve'])
    _l(ng, p.outputs['Curve'], m.inputs['Profile Curve'])
    return m.outputs['Mesh']


def _smooth(ng, geo, x=0, y=0):
    s = _n(ng, 'GeometryNodeSetShadeSmooth', x, y)
    _l(ng, geo, s.inputs['Mesh'])
    return s.outputs[0]


def shape_group():
    """AC9_ButtonShape: a button from its values (mm in, meters out). Cylinder body, holes cut through it, a rim on its
    edge, a dome on top, a shank loop under it."""
    ng = bpy.data.node_groups.get(SHAPE)
    if ng is not None and ng.get('ac9_v') == GROUP_V:
        return ng
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(SHAPE, 'GeometryNodeTree')
    ng['ac9_v'] = GROUP_V
    _sock(ng, "Geometry", 'NodeSocketGeometry', 'OUTPUT')
    for name, kind, d in (("Diameter", 'NodeSocketFloat', 11.5), ("Thickness", 'NodeSocketFloat', 1.8),
                          ("Holes", 'NodeSocketInt', 4), ("Hole Diameter", 'NodeSocketFloat', 1.5),
                          ("Hole Spacing", 'NodeSocketFloat', 3.6), ("Rim", 'NodeSocketFloat', 1.2),
                          ("Dome", 'NodeSocketFloat', 0.0), ("Shank", 'NodeSocketBool', False)):
        _sock(ng, name, kind, default=d, lo=0 if kind != 'NodeSocketBool' else None)
    _sock(ng, "Material", 'NodeSocketMaterial')
    gi = _n(ng, 'NodeGroupInput', -1400, 0)
    go = _n(ng, 'NodeGroupOutput', 1400, 0)
    I = gi.outputs
    r = _math(ng, 'MULTIPLY', I['Diameter'], 0.5, -1200, 300)
    t = I['Thickness']
    # body
    cyl = _n(ng, 'GeometryNodeMeshCylinder', -1000, 400, fill_type='NGON')
    cyl.inputs['Vertices'].default_value = 48
    _l(ng, r, cyl.inputs['Radius'])
    _l(ng, t, cyl.inputs['Depth'])
    body = _xform(ng, cyl.outputs['Mesh'], _xyz(ng, 0, 0, _math(ng, 'MULTIPLY', t, 0.5, -1000, 200), -800, 200), x=-600, y=400)
    # holes: Holes points on a circle of the spacing, a cylinder through the body at each, cut out
    pts = _n(ng, 'GeometryNodePoints', -1000, -100)
    _l(ng, I['Holes'], pts.inputs['Count'])
    idx = _n(ng, 'GeometryNodeInputIndex', -1400, -300)
    ang = _math(ng, 'ADD', _math(ng, 'DIVIDE', _math(ng, 'MULTIPLY', idx.outputs[0], 2 * math.pi, -1250, -300),
                                 I['Holes'], -1100, -300),
                _math(ng, 'SUBTRACT', _math(ng, 'DIVIDE', math.pi, I['Holes'], -1100, -380), math.pi / 2, -950, -380), -950, -300)
    hr = _math(ng, 'MULTIPLY', I['Hole Spacing'], 0.5, -1100, -450)
    _l(ng, _xyz(ng, _math(ng, 'MULTIPLY', _math(ng, 'COSINE', ang, None, -800, -300), hr, -650, -300),
                _math(ng, 'MULTIPLY', _math(ng, 'SINE', ang, None, -800, -450), hr, -650, -450), 0.0, -500, -350),
       pts.inputs['Position'])
    hc = _n(ng, 'GeometryNodeMeshCylinder', -800, -600, fill_type='NGON')
    hc.inputs['Vertices'].default_value = 16
    _l(ng, _math(ng, 'MULTIPLY', I['Hole Diameter'], 0.5, -1000, -700), hc.inputs['Radius'])
    _l(ng, _math(ng, 'MULTIPLY', t, 4.0, -1000, -800), hc.inputs['Depth'])
    iop = _n(ng, 'GeometryNodeInstanceOnPoints', -400, -200)
    _l(ng, pts.outputs['Points'], iop.inputs['Points'])
    _l(ng, hc.outputs['Mesh'], iop.inputs['Instance'])
    real = _n(ng, 'GeometryNodeRealizeInstances', -200, -200)
    _l(ng, iop.outputs[0], real.inputs['Geometry'])
    boo = _n(ng, 'GeometryNodeMeshBoolean', 0, 300, operation='DIFFERENCE')
    _l(ng, body, boo.inputs['Mesh 1'])
    _l(ng, real.outputs[0], boo.inputs['Mesh 2'])
    # rim: a torus on the top edge, flattened
    rim = _xform(ng, _torus(ng, _math(ng, 'SUBTRACT', r, _math(ng, 'MULTIPLY', I['Rim'], 0.5, -600, 0), -400, 0),
                            _math(ng, 'MULTIPLY', I['Rim'], 0.5, -600, -50), -300, 0),
                 _xyz(ng, 0, 0, t, 0, -50), sc=(1.0, 1.0, 0.7), x=200, y=0)
    rim = _smooth(ng, _switch(ng, _math(ng, 'GREATER_THAN', I['Rim'], 0.0, 200, 100), rim, 400, 0), 600, 0)
    # dome: the upper half of a sphere of the button's radius, flattened to the dome's height, on the body
    sph = _n(ng, 'GeometryNodeMeshUVSphere', 0, -300)
    sph.inputs['Segments'].default_value = 48
    sph.inputs['Rings'].default_value = 16
    _l(ng, r, sph.inputs['Radius'])
    pos = _n(ng, 'GeometryNodeInputPosition', 0, -500)
    sep = _n(ng, 'ShaderNodeSeparateXYZ', 150, -500)
    _l(ng, pos.outputs[0], sep.inputs[0])
    cut = _n(ng, 'GeometryNodeSeparateGeometry', 300, -300, domain='POINT')
    _l(ng, sph.outputs['Mesh'], cut.inputs['Geometry'])
    _l(ng, _math(ng, 'GREATER_THAN', sep.outputs[2], -1e-6, 300, -500), cut.inputs['Selection'])
    dome = _xform(ng, cut.outputs['Selection'], _xyz(ng, 0, 0, t, 450, -450),
                  sc=_xyz(ng, 1.0, 1.0, _math(ng, 'DIVIDE', I['Dome'], r, 450, -650), 600, -600), x=700, y=-300)
    dome = _smooth(ng, _switch(ng, _math(ng, 'GREATER_THAN', I['Dome'], 0.0, 700, -150), dome, 900, -300), 1050, -300)
    # shank: a small loop under the button
    shk = _xform(ng, _torus(ng, 1.3, 0.45, 300, -800), (0.0, 0.0, -1.3), rot=(math.pi / 2, 0.0, 0.0), x=700, y=-800)
    shk = _smooth(ng, _switch(ng, I['Shank'], shk, 900, -800), 1050, -800)
    j = _n(ng, 'GeometryNodeJoinGeometry', 1100, 0)
    for g in (shk, dome, rim, boo.outputs['Mesh']):
        _l(ng, g, j.inputs[0])
    mat = _n(ng, 'GeometryNodeSetMaterial', 1200, 0)
    _l(ng, j.outputs[0], mat.inputs['Geometry'])
    _l(ng, I['Material'], mat.inputs['Material'])
    _l(ng, _xform(ng, mat.outputs[0], sc=(0.001, 0.001, 0.001), x=1300, y=0), go.inputs[0])
    return ng


def place_group():
    """AC9_ButtonPlace: the points (ac9_uv = layout point, ac9_style = instance index, ac9_out = the direction away
    from the body there) put on the garment by its UV map, on its outermost cloth, each the style's shape turned to
    the cloth and to the piece's layout x."""
    ng = bpy.data.node_groups.get(PLACE)
    if ng is not None and ng.get('ac9_v') == GROUP_V:
        return ng
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(PLACE, 'GeometryNodeTree')
    ng['ac9_v'] = GROUP_V
    _sock(ng, "Geometry", 'NodeSocketGeometry')
    _sock(ng, "Garment", 'NodeSocketObject')
    _sock(ng, "Styles", 'NodeSocketCollection')
    _sock(ng, "Lift", 'NodeSocketFloat', default=LIFT_M)
    _sock(ng, "Geometry", 'NodeSocketGeometry', 'OUTPUT')
    gi = _n(ng, 'NodeGroupInput', -1200, 0)
    go = _n(ng, 'NodeGroupOutput', 1100, 0)
    oi = _n(ng, 'GeometryNodeObjectInfo', -1000, 200, transform_space='RELATIVE')
    _l(ng, gi.outputs['Garment'], oi.inputs['Object'])
    cloth = _cloth_only(ng, oi.outputs['Geometry'], -800, 500)
    uvmap = _n(ng, 'GeometryNodeInputNamedAttribute', -1000, 0, data_type='FLOAT_VECTOR')
    uvmap.inputs['Name'].default_value = 'UVMap'
    suv = _n(ng, 'GeometryNodeInputNamedAttribute', -1000, -150, data_type='FLOAT_VECTOR')
    suv.inputs['Name'].default_value = 'ac9_uv'
    pos = _n(ng, 'GeometryNodeInputPosition', -1000, -300)
    nor = _n(ng, 'GeometryNodeInputNormal', -1000, -400)
    uv2 = _n(ng, 'ShaderNodeVectorMath', -800, -200, operation='ADD')
    _l(ng, suv.outputs[0], uv2.inputs[0])
    uv2.inputs[1].default_value = (0.002, 0.0, 0.0)

    def sample(val, at, y):
        s = _n(ng, 'GeometryNodeSampleUVSurface', -600, y, data_type='FLOAT_VECTOR')
        _l(ng, cloth, s.inputs['Mesh'])
        _l(ng, val, s.inputs['Value'])
        _l(ng, uvmap.outputs[0], s.inputs['UV Map'])
        _l(ng, at, s.inputs['Sample UV'])
        return s.outputs['Value']
    P0 = sample(pos.outputs[0], suv.outputs[0], 200)
    P2 = sample(pos.outputs[0], uv2.outputs[0], -200)
    # a fastened pair: the button is sewn on its own point (ac9_uv2) and comes out through the hole's layer (ac9_uv)
    # -- its own point, raised to the hole's layer when that lies outside, by NEAR_M at most (a continuous rule: while
    # the pair is still far apart it never jumps between the two)
    suv2 = _n(ng, 'GeometryNodeInputNamedAttribute', -1000, 400, data_type='FLOAT_VECTOR')
    suv2.inputs['Name'].default_value = 'ac9_uv2'
    Pb = sample(pos.outputs[0], suv2.outputs[0], 400)
    # the outermost cloth there: a ray from 20 mm outside (ac9_out: away from the body) back in -- a layer within
    # NEAR_M over the point takes the button (a fold of its own cloth); a hit further out is another part of the garment
    # passing in front (an open front flapping) and is ignored, else the button jumped onto it frame by frame
    out = _n(ng, 'GeometryNodeInputNamedAttribute', -600, -400, data_type='FLOAT_VECTOR')
    out.inputs['Name'].default_value = 'ac9_out'
    dd = _n(ng, 'ShaderNodeVectorMath', -500, 400, operation='SUBTRACT')
    _l(ng, Pb, dd.inputs[0])
    _l(ng, P0, dd.inputs[1])
    dp = _n(ng, 'ShaderNodeVectorMath', -400, 400, operation='DOT_PRODUCT')
    _l(ng, dd.outputs[0], dp.inputs[0])
    _l(ng, out.outputs[0], dp.inputs[1])
    up = _n(ng, 'ShaderNodeClamp', -300, 400)
    _l(ng, _math(ng, 'MULTIPLY', dp.outputs['Value'], -1.0, -350, 500), up.inputs['Value'])
    up.inputs['Min'].default_value = 0.0
    up.inputs['Max'].default_value = NEAR_M
    ou = _n(ng, 'ShaderNodeVectorMath', -250, 300, operation='SCALE')
    _l(ng, out.outputs[0], ou.inputs[0])
    _l(ng, up.outputs[0], ou.inputs['Scale'])
    sw = _n(ng, 'ShaderNodeVectorMath', -200, 300, operation='ADD')
    _l(ng, Pb, sw.inputs[0])
    _l(ng, ou.outputs[0], sw.inputs[1])
    P = sw.outputs[0]
    o20 = _n(ng, 'ShaderNodeVectorMath', -400, -400, operation='SCALE')
    _l(ng, out.outputs[0], o20.inputs[0])
    o20.inputs['Scale'].default_value = 0.02
    org = _n(ng, 'ShaderNodeVectorMath', -250, -400, operation='ADD')
    _l(ng, P, org.inputs[0])
    _l(ng, o20.outputs[0], org.inputs[1])
    neg = _n(ng, 'ShaderNodeVectorMath', -400, -550, operation='SCALE')
    _l(ng, out.outputs[0], neg.inputs[0])
    neg.inputs['Scale'].default_value = -1.0
    # the cloth's normal blurred over its neighbors (a button spans ~10 mm; one triangle's normal on a rippled
    # placket turns it sideways), read at the hit
    nin = _n(ng, 'GeometryNodeInputNormal', -400, -700)
    blur = _n(ng, 'GeometryNodeBlurAttribute', -250, -700, data_type='FLOAT_VECTOR')
    _l(ng, nin.outputs[0], blur.inputs[0])
    blur.inputs['Iterations'].default_value = BLUR_ITER
    rc = _n(ng, 'GeometryNodeRaycast', -100, -400, data_type='FLOAT_VECTOR')
    _l(ng, oi.outputs['Geometry'], rc.inputs['Target Geometry'])
    _l(ng, blur.outputs[0], rc.inputs['Attribute'])
    _l(ng, org.outputs[0], rc.inputs['Source Position'])
    _l(ng, neg.outputs[0], rc.inputs['Ray Direction'])
    rc.inputs['Ray Length'].default_value = 0.04
    far = _n(ng, 'FunctionNodeCompare', -50, -650, data_type='FLOAT', operation='GREATER_THAN')
    _l(ng, rc.outputs['Hit Distance'], far.inputs[0])
    far.inputs[1].default_value = 0.02 - NEAR_M
    near = _n(ng, 'FunctionNodeBooleanMath', 50, -650, operation='AND')
    _l(ng, rc.outputs['Is Hit'], near.inputs[0])
    _l(ng, far.outputs[0], near.inputs[1])
    own_n = sample(blur.outputs[0], suv.outputs[0], -800)

    def vswitch(f, t, y):
        w = _n(ng, 'GeometryNodeSwitch', 350, y, input_type='VECTOR')
        _l(ng, near.outputs[0], w.inputs['Switch'])
        _l(ng, f, w.inputs['False'])
        _l(ng, t, w.inputs['True'])
        return w.outputs[0]
    cn = vswitch(own_n, rc.outputs['Attribute'], -750)
    dot = _n(ng, 'ShaderNodeVectorMath', 50, -500, operation='DOT_PRODUCT')
    _l(ng, cn, dot.inputs[0])
    _l(ng, out.outputs[0], dot.inputs[1])
    ns = _n(ng, 'ShaderNodeVectorMath', 150, -450, operation='SCALE')
    _l(ng, cn, ns.inputs[0])
    _l(ng, _math(ng, 'SIGN', dot.outputs['Value'], None, 100, -600), ns.inputs['Scale'])
    # leaned a little towards the body's outside: a fold's edge still turns it, the body never does
    ob = _n(ng, 'ShaderNodeVectorMath', 150, -650, operation='SCALE')
    _l(ng, out.outputs[0], ob.inputs[0])
    ob.inputs['Scale'].default_value = OUT_LEAN
    nsum = _n(ng, 'ShaderNodeVectorMath', 250, -500, operation='ADD')
    _l(ng, ns.outputs[0], nsum.inputs[0])
    _l(ng, ob.outputs[0], nsum.inputs[1])
    nh = _n(ng, 'ShaderNodeVectorMath', 300, -450, operation='NORMALIZE')
    _l(ng, nsum.outputs[0], nh.inputs[0])
    nrm = nh.outputs[0]
    Pp = vswitch(P, rc.outputs['Hit Position'], -150)
    tg = _n(ng, 'ShaderNodeVectorMath', -400, -200, operation='SUBTRACT')
    _l(ng, P2, tg.inputs[0])
    _l(ng, P0, tg.inputs[1])
    r1 = _n(ng, 'FunctionNodeAlignRotationToVector', 450, 0, axis='Z')
    _l(ng, nrm, r1.inputs['Vector'])
    r2 = _n(ng, 'FunctionNodeAlignRotationToVector', 600, 0, axis='X', pivot_axis='Z')
    _l(ng, r1.outputs[0], r2.inputs['Rotation'])
    _l(ng, tg.outputs[0], r2.inputs['Vector'])
    lift = _n(ng, 'ShaderNodeVectorMath', 450, 200, operation='SCALE')
    _l(ng, nrm, lift.inputs[0])
    _l(ng, gi.outputs['Lift'], lift.inputs['Scale'])
    at = _n(ng, 'ShaderNodeVectorMath', 600, 200, operation='ADD')
    _l(ng, Pp, at.inputs[0])
    _l(ng, lift.outputs[0], at.inputs[1])
    sp = _n(ng, 'GeometryNodeSetPosition', 750, 200)
    _l(ng, gi.outputs['Geometry'], sp.inputs['Geometry'])
    _l(ng, at.outputs[0], sp.inputs['Position'])
    ci = _n(ng, 'GeometryNodeCollectionInfo', 200, -200, transform_space='ORIGINAL')
    _l(ng, gi.outputs['Styles'], ci.inputs['Collection'])
    ci.inputs['Separate Children'].default_value = True
    ci.inputs['Reset Children'].default_value = True
    sty = _n(ng, 'GeometryNodeInputNamedAttribute', 200, -400, data_type='INT')
    sty.inputs['Name'].default_value = 'ac9_style'
    iop = _n(ng, 'GeometryNodeInstanceOnPoints', 900, 0)
    _l(ng, sp.outputs[0], iop.inputs['Points'])
    _l(ng, ci.outputs[0], iop.inputs['Instance'])
    iop.inputs['Pick Instance'].default_value = True
    _l(ng, sty.outputs[0], iop.inputs['Instance Index'])
    _l(ng, r2.outputs[0], iop.inputs['Rotation'])
    _l(ng, iop.outputs[0], go.inputs[0])
    return ng


# ---- the style objects --------------------------------------------------------------------------------------
def _material(name, rgba, rough):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.diffuse_color = rgba
        m.roughness = rough
        m.use_nodes = True
        b = m.node_tree.nodes.get('Principled BSDF')
        if b is not None:
            b.inputs['Base Color'].default_value = rgba
            b.inputs['Roughness'].default_value = rough
    return m


def _hole_mesh():
    """A slit (a stadium, 0.3 mm thick) along y, its length set per button by the object's scale."""
    me = bpy.data.meshes.new(HOLE_OBJ)
    n = 12
    L, W = BS.STYLES[BS.DEFAULT]['diameter'] + BS.HOLE_EXTRA - BS.HOLE_W, BS.HOLE_W
    ring = []
    for s in (1, -1):
        for k in range(n + 1):
            a = math.pi * k / n
            ring.append((s * W / 2 * math.cos(a), s * (L / 2 + W / 2 * math.sin(a)), 0.0))
    top = [(x * 0.001, y * 0.001, 0.0003) for x, y, _ in ring]
    me.from_pydata(top, [], [list(range(len(top)))])
    me.update()
    me.materials.append(_material(MAT_HOLE, (0.05, 0.05, 0.06, 1.0), 0.6))
    return me


def styles_collection(spec=None):
    """The collection of style objects, in instance-index order (their names sort as `order`). Made or brought up
    to date with the spec's own styles. Returns (collection, [style ids] in index order, hole index)."""
    coll = bpy.data.collections.get(COLL)
    if coll is None:
        coll = bpy.data.collections.new(COLL)
        coll.use_fake_user = True
    ng = shape_group()
    mat = _material(MAT, (0.30, 0.24, 0.20, 1.0), 0.35)
    user = (spec or {}).get('button_styles', [])
    order = BS.ids(user)
    for i, sid in enumerate(order):
        st = BS.style(sid, user)
        name = f"AC9_BtnStyle_{i:02d}_{sid}"
        ob = bpy.data.objects.get(name)
        if st.get('mesh'):
            src = bpy.data.objects.get(st['mesh'])
            if ob is not None and ob.data is not (src.data if src else None):
                bpy.data.objects.remove(ob)
                ob = None
            if ob is None and src is not None:
                ob = bpy.data.objects.new(name, src.data)
        else:
            if ob is None:
                ob = bpy.data.objects.new(name, bpy.data.meshes.new(name))
            m = ob.modifiers.get("AC9 Button Shape") or ob.modifiers.new("AC9 Button Shape", 'NODES')
            m.node_group = ng
            for item in ng.interface.items_tree:
                if getattr(item, 'in_out', '') != 'INPUT':
                    continue
                key = {'Diameter': 'diameter', 'Thickness': 'thickness', 'Holes': 'holes', 'Hole Diameter': 'hole_d',
                       'Hole Spacing': 'hole_spacing', 'Rim': 'rim', 'Dome': 'dome', 'Shank': 'shank'}.get(item.name)
                if key is not None:
                    v = st[key]
                    _set_input(m, item.identifier, int(v) if item.socket_type == 'NodeSocketInt' else (
                        bool(v) if item.socket_type == 'NodeSocketBool' else float(v)))
                elif item.name == 'Material':
                    _set_input(m, item.identifier, mat)
        if ob is None:
            continue
        ob['ac9_style'] = sid
        for k in BS.NOTES:
            ob[f'ac9_{k}'] = st.get(k, '')
        if ob.name not in coll.objects:
            coll.objects.link(ob)
    hole = bpy.data.objects.get(HOLE_OBJ) or bpy.data.objects.new(HOLE_OBJ, _hole_mesh())
    if hole.name not in coll.objects:
        coll.objects.link(hole)
    kids = sorted(coll.objects, key=lambda o: o.name)
    names = [o.name for o in kids]
    ids_ = [o.get('ac9_style') for o in kids]
    return coll, ids_, names.index(HOLE_OBJ)


# ---- where each point goes ----------------------------------------------------------------------------------
def _locate(flat, F, fp, pi, pt):
    """(triangle index, barycentric weights) of layout point `pt` in piece index `pi` (the nearest triangle when it
    is outside every one)."""
    T = np.where(fp == pi)[0]
    A, B, C = flat[F[T, 0], :2], flat[F[T, 1], :2], flat[F[T, 2], :2]
    p = np.asarray(pt, float)
    v0, v1, v2 = B - A, C - A, p - A
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-12, 1e-12, d00 * d11 - d01 * d01)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    u = 1 - v - w
    bad = np.maximum.reduce([-u, -v, -w, np.zeros_like(u)])
    k = int(np.argmin(bad))
    return int(T[k]), np.array([u[k], v[k], w[k]])


def point_rows(spec, names, flat, F, fp, X_mm, body_bvh=None):
    """[(uv (2,) m, style id or None for a hole, out (3,), uv2 (2,) m)] for every button of the spec on these pieces: a
    fastened button at its hole, an unfastened one at its own point with its hole's slit. out: away from the body there
    (from the nearest body point). uv2: a fastened button's own point (the pair's other layer: the button sits on the
    outer of the two), else uv."""
    rows = []

    def flip_at(piece, pt):
        """The direction away from the body at the piece's layout point (the cloth's normal turned outwards
        without a body)."""
        if piece not in names:
            return None
        t, w = _locate(flat, F, fp, names.index(piece), pt)
        a, b, c = X_mm[F[t]]
        n = np.cross(b - a, c - a)
        n = n / max(np.linalg.norm(n), 1e-12)
        p = w @ X_mm[F[t]]
        if body_bvh is None:
            return n
        hit = body_bvh.find_nearest((p * 0.001).tolist())
        if hit[0] is None:
            return n
        o = p * 0.001 - np.array(hit[0])
        return o / max(np.linalg.norm(o), 1e-12)

    for b in spec.get('buttons', []) or []:
        h = b.get('hole')
        sid = b.get('style') or BS.DEFAULT
        if h and b.get('fastened', True) and h.get('piece') in names:
            f = flip_at(h['piece'], h['pt'])
            if f is not None:
                rows.append((np.asarray(h['pt'], float) / 1000.0, sid, f,
                             np.asarray(b['pt'], float) / 1000.0 if b.get('piece') in names else
                             np.asarray(h['pt'], float) / 1000.0))
            continue
        f = flip_at(b.get('piece'), b.get('pt'))
        if f is not None:
            rows.append((np.asarray(b['pt'], float) / 1000.0, sid, f, np.asarray(b['pt'], float) / 1000.0))
        if h and h.get('piece') in names:
            f = flip_at(h['piece'], h['pt'])
            if f is not None:
                rows.append((np.asarray(h['pt'], float) / 1000.0, None, f, np.asarray(h['pt'], float) / 1000.0))
    return rows


def sync(context, spec, g, job=None, X_mm=None):
    """Make the buttons' point object of garment `g` match the spec (or remove it when there are none). `job`: the
    garment's job folder (its layout, triangles and body); X_mm: the shape the outside is read from (default the
    job's last frame)."""
    pname = points_name(g.name) if g is not None else None
    old = bpy.data.objects.get(pname) if pname else None
    if g is None or not spec.get('buttons') or not job or not os.path.exists(os.path.join(job, 'scene.idx.npz')):
        if old is not None:
            me = old.data
            bpy.data.objects.remove(old)
            if me.users == 0:
                bpy.data.meshes.remove(me)
        return None
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
    rows = point_rows(spec, names, idx['flat'], idx['F'], idx['fp'], X_mm, bvh)
    coll, order, hole_i = styles_collection(spec)
    me = bpy.data.meshes.new(pname)
    me.from_pydata([(0.0, 0.0, 0.0)] * len(rows), [], [])
    me.update()
    uv = me.attributes.new('ac9_uv', 'FLOAT_VECTOR', 'POINT')
    uv.data.foreach_set('vector', np.array([(r[0][0], r[0][1], 0.0) for r in rows], np.float32).ravel())
    uv2 = me.attributes.new('ac9_uv2', 'FLOAT_VECTOR', 'POINT')
    uv2.data.foreach_set('vector', np.array([(r[3][0], r[3][1], 0.0) for r in rows], np.float32).ravel())
    st = me.attributes.new('ac9_style', 'INT', 'POINT')
    st.data.foreach_set('value', np.array([hole_i if r[1] is None else (order.index(r[1]) if r[1] in order else
                                           order.index(BS.DEFAULT)) for r in rows], np.int32))
    fl = me.attributes.new('ac9_out', 'FLOAT_VECTOR', 'POINT')
    fl.data.foreach_set('vector', np.array([r[2] for r in rows], np.float32).ravel())
    if old is None:
        old = bpy.data.objects.new(pname, me)
        context.scene.collection.objects.link(old)
    else:
        m_old = old.data
        old.data = me
        if m_old.users == 0:
            bpy.data.meshes.remove(m_old)
    old.matrix_world = g.matrix_world
    old['ac9_buttons_of'] = g.name
    from . import view_setup
    view_setup.ensure_accessory(context, old, g, spec)
    view_setup.refresh(context)
    mod = old.modifiers.get("AC9 Buttons") or old.modifiers.new("AC9 Buttons", 'NODES')
    ng = place_group()
    mod.node_group = ng
    for item in ng.interface.items_tree:
        if getattr(item, 'in_out', '') != 'INPUT':
            continue
        if item.name == 'Garment':
            _set_input(mod, item.identifier, g)
        elif item.name == 'Styles':
            _set_input(mod, item.identifier, coll)
    old.hide_select = True
    return old


# ---- on the flat pattern ------------------------------------------------------------------------------------
def flat_group():
    """AC9_ButtonFlat: each point the style's shape (ac9_style = instance index), upright on the layout plane."""
    ng = bpy.data.node_groups.get(FLAT)
    if ng is not None and ng.get('ac9_v') == GROUP_V:
        return ng
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(FLAT, 'GeometryNodeTree')
    ng['ac9_v'] = GROUP_V
    _sock(ng, "Geometry", 'NodeSocketGeometry')
    _sock(ng, "Styles", 'NodeSocketCollection')
    _sock(ng, "Geometry", 'NodeSocketGeometry', 'OUTPUT')
    gi = _n(ng, 'NodeGroupInput', -600, 0)
    ci = _n(ng, 'GeometryNodeCollectionInfo', -350, -200, transform_space='ORIGINAL')
    _l(ng, gi.outputs['Styles'], ci.inputs['Collection'])
    ci.inputs['Separate Children'].default_value = True
    ci.inputs['Reset Children'].default_value = True
    st = _n(ng, 'GeometryNodeInputNamedAttribute', -350, -400, data_type='INT')
    st.inputs['Name'].default_value = 'ac9_style'
    iop = _n(ng, 'GeometryNodeInstanceOnPoints', 0, 0)
    _l(ng, gi.outputs['Geometry'], iop.inputs['Points'])
    _l(ng, ci.outputs[0], iop.inputs['Instance'])
    iop.inputs['Pick Instance'].default_value = True
    _l(ng, st.outputs[0], iop.inputs['Instance Index'])
    go = _n(ng, 'NodeGroupOutput', 250, 0)
    _l(ng, iop.outputs[0], go.inputs[0])
    return ng


def flat_rows(spec):
    """[(layout point mm, style id or None for a hole)]: every button on its own point, every hole's slit."""
    on = {pc['name'] for pc in spec.get('pieces', []) if pc.get('enabled', True)}
    rows = []
    for b in spec.get('buttons', []) or []:
        if b.get('piece') in on:
            rows.append((np.asarray(b['pt'], float), b.get('style') or BS.DEFAULT))
        h = b.get('hole')
        if h and h.get('piece') in on:
            rows.append((np.asarray(h['pt'], float), None))
    return rows


def sync_flat(context, spec):
    """The buttons and holes on the flat pattern (removed when there are none or no layout Empty)."""
    from . import pattern_view as PV
    ob = bpy.data.objects.get(FLAT_OBJ)
    emp = PV.layout_empty(context)
    rows = flat_rows(spec) if spec else []
    if not rows or emp is None:
        if ob is not None:
            me = ob.data
            bpy.data.objects.remove(ob)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        return None
    coll, order, hole_i = styles_collection(spec)
    me = bpy.data.meshes.new(FLAT_OBJ)
    me.from_pydata([(float(p[0]) * 0.001, float(p[1]) * 0.001, 0.0) for p, _ in rows], [], [])
    me.update()
    st = me.attributes.new('ac9_style', 'INT', 'POINT')
    st.data.foreach_set('value', np.array([hole_i if s_ is None else (order.index(s_) if s_ in order else
                                           order.index(BS.DEFAULT)) for _, s_ in rows], np.int32))
    if ob is None:
        ob = bpy.data.objects.new(FLAT_OBJ, me)
        PV._collection(context).objects.link(ob)
    else:
        old = ob.data
        ob.data = me
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)
    ob.parent = emp
    ob.matrix_parent_inverse.identity()
    ob.location = (0.0, 0.0, 0.0)
    mod = ob.modifiers.get("AC9 Buttons") or ob.modifiers.new("AC9 Buttons", 'NODES')
    ng = flat_group()
    mod.node_group = ng
    for item in ng.interface.items_tree:
        if getattr(item, 'in_out', '') == 'INPUT' and item.name == 'Styles':
            _set_input(mod, item.identifier, coll)
    ob.hide_select = True
    return ob


def refresh_groups():
    """A .blend saved with an older placing group (GROUP_V): the buttons' and zippers' modifiers get the current one
    (on load, dress._relink_jobs; a garment's sync does it too)."""
    from . import zipper_view as ZV
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        for mod_name, make, v in (("AC9 Buttons", flat_group if ob.name == FLAT_OBJ else place_group, GROUP_V),
                                  ("AC9 Zippers", ZV.place_group, ZV.GROUP_V)):
            m = ob.modifiers.get(mod_name)
            if m is not None and m.type == 'NODES' and (m.node_group is None or m.node_group.get('ac9_v') != v):
                m.node_group = make()
