# SPDX-License-Identifier: GPL-3.0-or-later
"""The flat pattern in the viewport: one mesh object per piece, children of one layout Empty.

The Empty maps the spec's layout (mm) to the scene: a piece point (x, y) mm sits at
Empty.matrix_world @ (x / 1000, y / 1000, 0). Move or rotate the Empty to put the pattern
where it is convenient; the overlay and the seam tools follow it.
Disabled pieces are shown as wire.
"""
import bpy
import numpy as np
from mathutils import Matrix, Vector

from .core import spec as S

LAYOUT_NAME = "AC9_ClothSim_Layout"
COLL_NAME = "AC9_ClothSim_Pattern"
PIECE_PROP = "ac9_piece"
MM = 0.001


def layout_empty(context, create=False):
    ob = bpy.data.objects.get(LAYOUT_NAME)
    if ob is None and create:
        ob = bpy.data.objects.new(LAYOUT_NAME, None)
        ob.empty_display_type = 'PLAIN_AXES'
        ob.empty_display_size = 0.1
        _collection(context).objects.link(ob)
    return ob


def _collection(context):
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
        context.scene.collection.children.link(coll)
    return coll


def layout_matrix(context):
    ob = layout_empty(context)
    return ob.matrix_world.copy() if ob else Matrix.Identity(4)


def to_world(M, P):
    """(n, 2) mm -> list of world Vectors."""
    return [M @ Vector((float(x) * MM, float(y) * MM, 0.0)) for x, y in P]


def piece_objects():
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        return {}
    return {ob[PIECE_PROP]: ob for ob in coll.objects if PIECE_PROP in ob}


def lead_piece(context, names=None):
    """The piece the selection is shown and edited by: the active object's when it is a selected pattern piece, else
    the first selected pattern piece (user 2026-10-22: a box select keeps the active object outside it, and the
    piece's panel and buttons were gone for some selections and not others). names: only pieces in it."""
    from . import selection as SEL      # a placed piece's object and a handle on the garment are the piece too
    return SEL.lead(context, names)


def rebuild(context, spec, outlines):
    """Recreate the piece objects from the spec (the layout Empty is kept where the user put it)."""
    coll = _collection(context)
    first = layout_empty(context) is None
    emp = layout_empty(context, create=True)
    for ob in list(coll.objects):
        if PIECE_PROP in ob:
            me = ob.data
            bpy.data.objects.remove(ob)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
    if first and spec['pieces']:
        allp = np.vstack([outlines[pc['name']].B for pc in spec['pieces']])
        lo, hi = allp.min(0), allp.max(0)
        c = 0.5 * (lo + hi)
        # beside the avatar (which stands at the origin), lying on the floor
        emp.location = (1.0 + 0.5 * (hi[0] - lo[0]) * MM - c[0] * MM, -c[1] * MM, 0.0)
    for pc in spec['pieces']:
        ob = bpy.data.objects.new("AC9_Piece_" + pc['name'], _piece_mesh(pc, outlines[pc['name']]))
        ob[PIECE_PROP] = pc['name']
        _look(ob, spec, pc['name'])
        ob.parent = emp
        _show_state(ob, pc)
        coll.objects.link(ob)
    from . import view_setup
    view_setup.refresh(context)


def _faces_with_holes(B, rings, base):
    """Triangles filling the outline B (its vertices 0..len(B)-1) round the hole rings (their vertices numbered from
    base on, ring after ring), none inside a hole."""
    from mathutils.geometry import delaunay_2d_cdt
    from .core import pattern_build as PB
    n = len(B)
    P = np.vstack([B] + list(rings))
    E, k = [], n
    for R in rings:
        E += [(k + j, k + (j + 1) % len(R)) for j in range(len(R))]
        k += len(R)
    vo, _, fo, ov, _, _ = delaunay_2d_cdt([Vector(p) for p in P], E, [list(range(n))], 1, 1e-6, True)
    remap = [o[0] if len(o) == 1 else -1 for o in ov]
    T = np.array([[remap[a] for a in f] for f in fo if len(f) == 3 and all(remap[a] >= 0 for a in f)], np.int64)
    T = PB.drop_hole_tris(P, T.reshape(-1, 3), rings)
    return [[int(a) if a < n else int(a) - n + base for a in t] for t in T]


def _piece_mesh(pc, ol):
    V = [(float(x) * MM, float(y) * MM, 0.0) for x, y in ol.B]
    n = len(V)
    E = [(i, (i + 1) % n) for i in range(n)]
    for it in pc.get('internals', []):
        P = np.asarray(it['pts'], float)
        base = len(V)
        V += [(float(x) * MM, float(y) * MM, 0.0) for x, y in P]
        E += [(base + k, base + k + 1) for k in range(len(P) - 1)]
    faces = [list(range(n))]
    rings = [R for _, R in S.holes(pc)]
    if rings:                         # holes cut out (a dart's diamond): triangles round them instead of one n-gon
        faces = _faces_with_holes(ol.B, rings, len(V))
        V += [(float(x) * MM, float(y) * MM, 0.0) for R in rings for x, y in R]
    me = bpy.data.meshes.new("AC9_Piece_" + pc['name'])
    me.from_pydata(V, E, faces)
    me.validate()
    from . import look_apply as LA                # the UV as the garment's: a look's texture in the same place
    LA.ensure_uv_flat(me)
    return me


def update_pieces(context, spec, outlines, names):
    """Remesh the pieces `names` in place (a drafting edit); a piece without an object yet gets one."""
    emp = layout_empty(context, create=True)
    obs = piece_objects()
    coll = _collection(context)
    for pc in spec['pieces']:
        if pc['name'] not in names:
            continue
        me = _piece_mesh(pc, outlines[pc['name']])
        ob = obs.get(pc['name'])
        if ob is None:
            ob = bpy.data.objects.new("AC9_Piece_" + pc['name'], me)
            ob[PIECE_PROP] = pc['name']
            ob.parent = emp
            coll.objects.link(ob)
        else:
            old = ob.data
            ob.data = me
            if old is not None and old.users == 0:
                bpy.data.meshes.remove(old)
            me.name = "AC9_Piece_" + pc['name']
        _look(ob, spec, pc['name'])
        _show_state(ob, pc)


def _look(ob, spec, name):
    from . import look_apply as LA                # the piece's look (user 2026-10-20: on the flat pattern too)
    LA.apply_piece(ob, spec, name)


OFF_MATERIAL = "AC9_PieceOff"
LOCKS = ('lock_location', 'lock_rotation', 'lock_scale')


def _off_material():
    m = bpy.data.materials.get(OFF_MATERIAL)
    if m is None:
        m = bpy.data.materials.new(OFF_MATERIAL)
        m.diffuse_color = (0.35, 0.35, 0.35, 0.5)
        if hasattr(m, 'blend_method'):
            m.blend_method = 'BLEND'
    return m


def _show_state(ob, pc):
    """A flat piece object as its state shows. Off (Pattern) / Off (All) (P1 #9: out of the simulation's contact) are
    gray and see-through but solid: as wire they could be picked only on their lines, not by a click inside or a
    Select Box round them (user 2026-10-21). Locked against Blender's own G / R / S: the pattern is moved by Edit
    Pattern, which writes the pattern; a moved object only moved the picture (Fable review
    REVIEW_fable_tool_modes_2026-10-21 §1)."""
    for a in LOCKS:
        setattr(ob, a, (True, True, True))
    ob.display_type = 'SOLID'
    off = S.piece_state(pc) in ('off_pattern', 'off_all')
    me = ob.data
    if off and not me.materials:
        me.materials.append(None)                       # a slot for the object's own material
    for sl in ob.material_slots:
        if off:
            sl.link = 'OBJECT'
            sl.material = _off_material()
        elif sl.link == 'OBJECT':
            sl.material = None
            sl.link = 'DATA'


def refresh_enabled(spec):
    obs = piece_objects()
    for pc in spec['pieces']:
        ob = obs.get(pc['name'])
        if ob is not None:
            _show_state(ob, pc)
