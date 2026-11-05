# SPDX-License-Identifier: GPL-3.0-or-later
"""The left toolbar's AC9 tools (user 2026-10-13): grouped by what they do with separators, no "2D / 3D" in the
names, each tool's header says what it works on, and over something it cannot work on a note by the cursor says
so. A tool works by what is clicked (the pattern, the body, the garment), not by which view it is in (Fable review
REVIEW_fable_ui_layout_r2_2026-10-13 §2). The pattern tools are in the toolbar only while the "Pattern Tools"
switch at the top of the sidebar is on (scene.ac9_cloth_sim.pattern_tools).

  ──  Edit Pattern | Draw Piece ▾ Rectangle Piece ▾ Ellipse Piece | Internal Line ▾ Internal Rectangle ▾
      Internal Ellipse | Edit Seams ▾ Add Seam                                (the switch on)
  ──  Edit Measure ▾ Tape Around ▾ Tape Along ▾ Tape Straight | Place      (always)
  (names: user 2026-10-21, DECISIONS cloth-sim-select-measure-delete-2026-10-21; the ids are kept)
In the Work Screen's pattern view (Object mode) the toolbar shows only the AC9 tools and Select, Move, Rotate and
Annotate (user 2026-10-28): VIEW3D_PT_tools_active.draw is wrapped, see _toolbar_draw.

The tools that start one of the sidebar's modal operators (Measure, Draw, Add Seam, Place) do it on the click
itself: the click is fed to the operator as its first (start_modal), so one click with the tool does what the
button and a click did."""
import json
from .i18n import tx

import numpy as np

import bpy
import blf
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from . import click_log as CL
from . import pattern_view as PV
from . import store
from . import selection as SEL

GARMENT_PREFIX = "AC9_Garment_"            # dress.GARMENT_PREFIX (not imported: dress imports the solver side)
COLLIDER_PROP = "ac9_collider"
PLACED_PROP = "ac9_placed"                 # placed_view.PROP

# what each tool works on: (its header, the note's short form, the kinds it acts on)
WORKS = {
    'EDIT_PATTERN': ("pattern (on the garment: picks the piece)", "the pattern", {'PATTERN', 'PLANE', 'GARMENT',
                                                                                  'PLACED'}),
    'TRANSFORM_PATTERN': ("pieces and internal lines", "the pattern", {'PATTERN', 'PLANE', 'GARMENT', 'PLACED'}),
    'DRAW_PIECE': ("pattern", "the pattern", {'PATTERN', 'PLANE'}),
    'DRAW_LINE': ("pattern", "the pattern", {'PATTERN', 'PLANE'}),
    'RECT': ("pattern", "the pattern", {'PATTERN', 'PLANE', 'NONE'}),
    'ELLIPSE': ("pattern", "the pattern", {'PATTERN', 'PLANE', 'NONE'}),
    'IRECT': ("a piece of the pattern", "a pattern piece", {'PATTERN', 'PLANE'}),
    'IELLIPSE': ("a piece of the pattern", "a pattern piece", {'PATTERN', 'PLANE'}),
    'EDIT_SEAMS': ("pattern (3D seam lines: select)", "the pattern and the 3D seam lines",
                   {'PATTERN', 'PLANE', 'GARMENT', 'BODY', 'PLACED'}),
    'ADD_SEAM': ("pattern", "the pattern", {'PATTERN', 'PLANE'}),
    'ADD_SEAM_MN': ("pattern", "the pattern", {'PATTERN', 'PLANE'}),
    'MEASURE': ("body (tape), pattern (length)", "the body and the pattern", {'BODY', 'PATTERN', 'PLANE'}),
    'TAPE_ALONG': ("body (tape), pattern (length)", "the body and the pattern", {'BODY', 'PATTERN', 'PLANE'}),
    'TAPE_STRAIGHT': ("body (tape), pattern (length)", "the body and the pattern", {'BODY', 'PATTERN', 'PLANE'}),
    'MOVE_LEVEL': ("body", "the body", {'BODY'}),
    'PLACE': ("body (pattern: picks pieces)", "the body and the pattern",
              {'BODY', 'PATTERN', 'PLANE', 'NONE', 'GARMENT', 'OTHER', 'OBSTACLE', 'PLACED'}),
}
NAMES = {'EDIT_PATTERN': "Edit Pattern", 'TRANSFORM_PATTERN': "Transform Pattern", 'DRAW_PIECE': "Draw Piece", 'DRAW_LINE': "Internal Line",
         'RECT': "Rectangle Piece", 'ELLIPSE': "Ellipse Piece", 'IRECT': "Internal Rectangle",
         'IELLIPSE': "Internal Ellipse", 'EDIT_SEAMS': "Edit Seams", 'ADD_SEAM': "Add Seam",
         'ADD_SEAM_MN': "Add Seam (M:N)", 'MEASURE': "Tape Around",
         'TAPE_ALONG': "Tape Along", 'TAPE_STRAIGHT': "Tape Straight", 'MOVE_LEVEL': "Edit Measure", 'PLACE': "Place"}
TAPE_MODES = {'MEASURE': 'AROUND', 'TAPE_ALONG': 'ALONG', 'TAPE_STRAIGHT': 'STRAIGHT'}
KIND_TEXT = {'BODY': "the body", 'GARMENT': "the garment", 'OBSTACLE': "an obstacle", 'OTHER': "this object",
             'PLACED': "a placed piece",
             'NONE': "empty space", 'PATTERN': "the pattern", 'PLANE': "the pattern"}

_hint = {'text': None, 'xy': None, 'area': None}
_first = {'on': False, 'event': None}
_handles = []
_KEEP = "ac9_cloth_sim_pattern_tools"     # bpy.app.driver_namespace: the pattern tools were shown before F3
_trace = []                 # the last clicks (tool, kind, object, value, shift): for the GUI probe
_registered = []


# ---------------------------------------------------------------- what is under the cursor

def viewport_ray_cast(context, origin, direction, distance=1.0e38):
    """Nearest visible mesh hit, using Blender's native evaluated-object BVHs.

    Ordinary views retain scene.ray_cast. Restricted views use the same fast
    path for an ordinary visible first hit, then traverse evaluated instances
    when a hidden object occludes the wanted hit. Native object BVHs also cover
    evaluated curves/surfaces. No vertices or polygons are copied per hit.
    """
    space = getattr(context, 'space_data', None)
    graph = context.evaluated_depsgraph_get()
    if space is None or space.type != 'VIEW_3D' or not (space.use_local_collections or space.local_view):
        return context.scene.ray_cast(graph, origin, direction, distance=distance)
    from . import view_visibility as VV
    origin = Vector(origin)
    ray = Vector(direction).normalized()
    native = context.scene.ray_cast(graph, origin, ray, distance=distance)
    if native[0] and VV.object_allowed(context, native[4]):
        evaluated = native[4].evaluated_get(graph)
        if native[5] == evaluated.matrix_world:
            return native
    best = float(distance)
    found = (False, None, None, -1, None, None)
    for instance in graph.object_instances:
        evaluated = instance.object
        obj = evaluated.original
        owner = instance.parent.original if instance.is_instance and instance.parent is not None else obj
        if evaluated.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'} or not VV.object_allowed(context, owner):
            continue
        try:
            matrix = instance.matrix_world
            inverse = matrix.inverted()
            local_direction = inverse.to_3x3() @ ray
            scale = local_direction.length
            if scale < 1e-12:
                continue
            ok, local_hit, normal, index = evaluated.ray_cast(
                inverse @ origin, local_direction / scale, distance=min(best * scale, 1.0e38))
            if not ok:
                continue
            hit = matrix @ local_hit
            length = (hit - origin).length
            if length < best:
                best = length
                world_normal = (inverse.transposed().to_3x3() @ normal).normalized()
                found = (True, hit, world_normal, index, obj, matrix.copy())
        except (ReferenceError, RuntimeError, ValueError, TypeError):
            continue
    return found


def under(context, mx, my):
    """(kind, object, face index): what the click at region (mx, my) lands on. kind: 'PATTERN' (a pattern piece),
    'GARMENT' (a dressed garment), 'PLACED' (a placed piece's object, placed_view), 'BODY' (the scene's body),
    'OBSTACLE', 'OTHER' (another mesh), 'PLANE' (the
    pattern's layout plane, nothing in front of it) or 'NONE'. The first thing along the view ray wins, so a body
    standing in front of the pattern is the body."""
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return 'NONE', None, -1
    o = region_2d_to_origin_3d(region, rv3d, (mx, my))
    d = region_2d_to_vector_3d(region, rv3d, (mx, my))
    t_plane = None
    emp = PV.layout_empty(context)
    from . import view_visibility as VV
    if emp is not None and VV.allows_pattern(context):
        M = emp.matrix_world
        n = (M.to_3x3() @ Vector((0, 0, 1))).normalized()
        hit = intersect_line_plane(o, o + d, M.translation, n)
        if hit is not None and (hit - o).dot(d) > 0:
            t_plane = (hit - o).length
    try:
        ok, loc, _, idx, ob, _ = viewport_ray_cast(context, o, d)
    except Exception:
        ok = False
    if ok and ob is not None:
        ob = ob.original
        if PV.PIECE_PROP in ob:
            return 'PATTERN', ob, idx
        if PLACED_PROP in ob:
            return 'PLACED', ob, idx
        if t_plane is not None and t_plane < (loc - o).length - 1e-4:
            return 'PLANE', None, -1
        body = context.scene.ac9_cloth_sim.body
        if body is not None and ob == body:
            return 'BODY', ob, idx
        if ob.name.startswith(GARMENT_PREFIX):
            return 'GARMENT', ob, idx
        if COLLIDER_PROP in ob:
            return 'OBSTACLE', ob, idx
        return 'OTHER', ob, idx
    return ('PLANE' if t_plane is not None else 'NONE'), None, -1


def _guide_under(context, event):
    """True when a placement guide (the cylinders round the avatar) is under the cursor."""
    spec = store.get(context)[0]
    if not spec or not spec.get('guides') or context.region_data is None:
        return False
    from . import view_visibility as VV
    body = getattr(context.scene.ac9_cloth_sim, 'body', None)
    if not VV.allows_world(context, spec) or (body is not None and not VV.object_allowed(context, body)):
        return False
    from .core import body_axes as BA
    xy = (event.mouse_region_x, event.mouse_region_y)
    o = np.array(region_2d_to_origin_3d(context.region, context.region_data, xy)) * 1000.0
    d = np.array(region_2d_to_vector_3d(context.region, context.region_data, xy))
    return BA.ray_hit(spec['guides'], o, d) is not None


def garment_piece(ob, idx):
    """The pattern piece name of face idx of a dressed garment (its face attribute ac9_piece), or None. idx is a face
    of the evaluated mesh (a ray cast's): the thickness's Solidify adds the back and the rim faces after the mesh's
    own, a Reset's Mask drops some, and the attribute goes with them, so it is read there (user 2026-11-03: a collar
    seen from its back hit faces past the mesh's 14214 and could not be picked; the click log showed it)."""
    try:
        a = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.attributes.get('ac9_piece')
        names = json.loads(ob.get('ac9_piece_names', '[]'))
        if a is None or not (0 <= idx < len(a.data)):
            return None
        k = a.data[idx].value
        return names[k] if 0 <= k < len(names) else None
    except Exception:
        return None


def select_object(context, ob, extend=False, face_index=None):
    """Make `ob` the selected active object (a click on a placed piece's object: as Blender's click select, so G /
    R move it next and the Placement panel shows its move)."""
    if not extend:
        SEL.deselect_all(context)
    ob.select_set(not (extend and ob.select_get() and context.view_layer.objects.active == ob))
    context.view_layer.objects.active = ob
    if face_index is not None and ob.select_get():
        from . import placed_view as PLV
        PLV.remember_control_side(context, ob, face_index)
    return True


def select_piece(context, name, extend=False):
    """Make the pattern piece object `name` the selected active object (the Tool tab then shows it)."""
    ob = PV.piece_objects().get(name)
    if ob is None:
        return False
    if not extend:
        SEL.deselect_all(context)
    ob.select_set(not (extend and ob.select_get() and context.view_layer.objects.active == ob))
    context.view_layer.objects.active = ob
    return True


def pick_garment_piece(context, g, name, extend=False):
    """A click on the dressed garment's piece `name`: a handle at its center, selected, to move or turn the piece on
    the garment with G / R (piece_move.py, user 2026-10-14 b-A)."""
    from . import piece_move as PM
    return PM.pick(context, g, name, extend=extend) is not None


# ---------------------------------------------------------------- the note by the cursor

def hint(context, event, tool, kind):
    """Show (or clear) the note by the cursor for `tool` over `kind`. Returns True when the tool works there."""
    ok = kind in WORKS[tool][2]
    text = None if ok else tx("{0} works on {1}, not on {2}", tx(NAMES[tool]), tx(WORKS[tool][1]), tx(KIND_TEXT[kind]))
    if kind == 'NONE':
        text = None                                   # empty space: nothing to say
    xy = (event.mouse_region_x, event.mouse_region_y)
    if text != _hint['text'] or (text and xy != _hint['xy']):
        _hint.update(text=text, xy=xy, area=context.area.as_pointer() if context.area else None)
        if context.area:
            context.area.tag_redraw()
    return ok


def set_note(context, event, text):
    _hint.update(text=text, xy=(event.mouse_region_x, event.mouse_region_y),
                 area=context.area.as_pointer() if context.area else None)
    if context.area:
        context.area.tag_redraw()


def clear_hint():
    if _hint['text']:
        _hint['text'] = None


def _draw_hint():
    if not _hint['text']:
        return
    ctx = bpy.context
    if ctx.area is None or ctx.area.as_pointer() != _hint['area']:
        return
    x, y = _hint['xy']
    fid = 0
    blf.size(fid, 13)
    blf.color(fid, 1.0, 0.85, 0.35, 1.0)
    blf.enable(fid, blf.SHADOW)
    blf.shadow(fid, 3, 0.0, 0.0, 0.0, 0.9)
    blf.position(fid, x + 16, y - 22, 0)
    blf.draw(fid, _hint['text'])
    blf.disable(fid, blf.SHADOW)


# ---------------------------------------------------------------- starting a modal operator on the click

class _Event:
    """The tool's click as the operator's modal reads it. An operator called from Python gets the window's last
    event state, whose region coordinates are not the click's (GUI probe 2026-10-14: the first click was lost)."""
    def __init__(self, ev, type_, value):
        self.type, self.value = type_, value
        self.mouse_region_x, self.mouse_region_y = ev.mouse_region_x, ev.mouse_region_y
        self.mouse_x, self.mouse_y = ev.mouse_x, ev.mouse_y
        self.ctrl, self.shift, self.alt, self.oskey = ev.ctrl, ev.shift, ev.alt, ev.oskey
        self.unicode, self.is_repeat = '', False


def start_modal(op, context, event):
    """End of a modal operator's invoke: add the handler and, when a tool started it with a click, run that click
    through the operator's modal first -- a move to the click, then the press -- so the click is the operator's
    first, not lost to starting it."""
    ev = _first['event'] if _first['on'] else None
    op.from_tool = ev is not None
    if ev is not None:
        _first.update(on=False, event=None)
        op.modal(context, _Event(ev, 'MOUSEMOVE', 'NOTHING'))
        r = op.modal(context, _Event(ev, 'LEFTMOUSE', 'PRESS'))
        if 'RUNNING_MODAL' not in r:
            return r
    context.window_manager.modal_handler_add(op)
    return {'RUNNING_MODAL'}


def _call(idname, event, **kw):
    mod, name = idname.split('.')
    fn = getattr(getattr(bpy.ops, mod), name)
    if not fn.poll():
        return None
    _first.update(on=True, event=event)
    try:
        return fn('INVOKE_DEFAULT', **kw)
    finally:
        _first.update(on=False, event=None)


# ---------------------------------------------------------------- the operators the tools' keymaps call

DRAW_TOOLS = ('DRAW_PIECE', 'DRAW_LINE', 'RECT', 'ELLIPSE', 'IRECT', 'IELLIPSE')
# what each drawing tool snaps to (as its operator: draft_tool SNAP_KINDS, shape_tool), for the mark before the click
_SHAPE_SNAP = ('corner', 'notch', 'iend', 'mid', 'edge', 'iline')
DRAW_SNAP = {'DRAW_PIECE': ('corner', 'notch', 'mid', 'edge'), 'DRAW_LINE': _SHAPE_SNAP, 'RECT': _SHAPE_SNAP,
             'ELLIPSE': _SHAPE_SNAP, 'IRECT': _SHAPE_SNAP, 'IELLIPSE': _SHAPE_SNAP}


class AC9_OT_SimToolHover(bpy.types.Operator):
    """(internal) An AC9 tool: the note by the cursor over what it cannot work on"""
    bl_idname = "ac9_cloth_sim.tool_hover"
    bl_label = "AC9 Tool Hover"
    bl_options = {'INTERNAL'}

    tool: StringProperty()

    def invoke(self, context, event):
        if context.area is not None and context.area.type == 'VIEW_3D' and self.tool in WORKS:
            from . import pre_click as PC
            t = self.tool
            kind = under(context, event.mouse_region_x, event.mouse_region_y)[0]
            ok = hint(context, event, t, kind)
            # before the click, what it would land on (pre_click.py, user 2026-10-21)
            if t in TAPE_MODES or t == 'MOVE_LEVEL':     # the line under the cursor, the ring to lay
                from . import measure_edit as MEE
                MEE.hover(context, event, t)
                if t in ('TAPE_ALONG', 'TAPE_STRAIGHT') and ok and MEE.MT._ui['hot'] is None:
                    PC.tape_point(context, event, kind)
                else:
                    PC.clear(context)
            elif t in DRAW_TOOLS:                        # the pattern's view, as with Edit Pattern
                from . import draft_tool as DT
                DT.hover_piece(context, event)
                if ok:
                    PC.snap_point(context, event, DRAW_SNAP[t])
            elif t in ('ADD_SEAM', 'ADD_SEAM_MN'):
                PC.seam_point(context, event) if ok else PC.clear(context)
            elif t == 'PLACE':
                picked = PC.place_guide(context, event)
                if not picked and kind == 'BODY' and _guide_under(context, event):
                    set_note(context, event, "Place: click pattern pieces first (Shift: more)")
        return {'PASS_THROUGH'}


_branch = {'name': None}      # the branch the last click took (click_log writes it, then clears it)


def _branch_done(name, res):
    _branch['name'] = name
    return res


def place_branch(kind, picked, shift, to_place, guide):
    """The Place tool's press as one table: what is under it (kind), whether that placed object is already selected
    (picked), Shift, whether pieces to place are selected, whether a guide is under it -> the branch name.
    - a pattern piece: pick it (Shift: add)
    - a placed object: select it (G / R next), unless it is already selected and the press is on a guide: slide it
    - the dressed garment: pick the piece there (a handle), unless pieces to place are selected and a guide is
      under the press (a new piece placed through the garment in front of its guide, user 2026-10-21)
    - else: nothing selected to place / no guide under the press (a note), or start Place."""
    if kind == 'PATTERN':
        return 'pick_pattern'
    if kind == 'PLACED' and not (picked and not shift and guide):
        return 'select_placed'
    if kind == 'GARMENT' and not (to_place and guide):
        return 'pick_garment'
    if not to_place:
        return 'no_pieces'
    if not guide:
        return 'no_guide'
    return 'start_place'


class AC9_OT_SimToolClick(bpy.types.Operator):
    """(internal) An AC9 tool's click: what it does depends on what is clicked"""
    bl_idname = "ac9_cloth_sim.tool_click"
    bl_label = "AC9 Tool Click"
    bl_options = {'INTERNAL'}

    tool: StringProperty()

    @CL.logged
    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            return {'PASS_THROUGH'}
        kind, ob, idx = under(context, event.mouse_region_x, event.mouse_region_y)
        t = self.tool
        _trace.append((t, kind, ob.name if ob else None, event.value, event.shift))
        del _trace[:-20]
        if not hint(context, event, t, kind):
            return {'CANCELLED'}
        if kind in ('GARMENT', 'PLACED'):               # the point clicked shown on the pattern too (garment_mark)
            from . import garment_mark as GM
            GM.mark(context, ob, event.mouse_region_x, event.mouse_region_y)
        r = None
        if t in TAPE_MODES or t == 'MOVE_LEVEL':
            # a click on a measurement's line picks it (Edit Measure: a measured line or a tape, which it then
            # drags; a click on nothing just drops the pick)
            from . import measure_edit as MEE
            key = MEE.pick(context, (event.mouse_region_x, event.mouse_region_y),
                           kinds=MEE.EDIT_KINDS if t == 'MOVE_LEVEL' else None)
            if key is not None and t != 'MOVE_LEVEL':
                MEE.select(context, key)
                return {'FINISHED'}
            if key is None and MEE.sel(context):
                MEE.select(context, '')
            MEE.clear_hover(context)
            if key is None and t == 'MOVE_LEVEL':
                return {'FINISHED'}
        if t in TAPE_MODES:
            if kind == 'BODY':
                r = _call("ac9_cloth_sim.measure_tape", event, mode=TAPE_MODES[t])
            else:
                r = _call("ac9_cloth_sim.measure_pattern", event)
        elif t == 'MOVE_LEVEL':
            r = _call("ac9_cloth_sim.measure_move_level", event)
            if r is None:
                set_note(context, event, "Edit Measure: measure the body first (Measure Body)")
                return {'CANCELLED'}
        elif t in ('DRAW_PIECE', 'DRAW_LINE'):
            if store.get(context)[0] is None and t == 'DRAW_PIECE':
                bpy.ops.ac9_cloth_sim.new_pattern()
            r = _call("ac9_cloth_sim." + t.lower(), event)
        elif t in ('RECT', 'ELLIPSE', 'IRECT', 'IELLIPSE'):
            from . import shape_tool as ST
            shape, internal = ST.SHAPE_TOOLS[t]
            r = _call("ac9_cloth_sim.draw_shape", event, shape=shape, internal=internal)
        elif t == 'ADD_SEAM':
            r = _call("ac9_cloth_sim.add_seam", event)
        elif t == 'ADD_SEAM_MN':
            r = _call("ac9_cloth_sim.add_seam_mn", event)
        elif t == 'PLACE':
            guide = kind != 'PATTERN' and _guide_under(context, event)
            to_place = kind != 'PATTERN' and SEL.any_pieces(context)
            picked = kind == 'PLACED' and ob.select_get()
            b = place_branch(kind, picked, event.shift, to_place, guide)
            if b == 'pick_garment':
                name = garment_piece(ob, idx)
                if name and pick_garment_piece(context, ob, name, extend=event.shift):
                    return _branch_done(b, {'FINISHED'})
                b = place_branch(None, False, event.shift, to_place, guide)     # no piece there: as on nothing
            if b == 'pick_pattern':
                select_piece(context, ob[PV.PIECE_PROP], extend=event.shift)
                return _branch_done(b, {'FINISHED'})
            if b == 'select_placed':
                select_object(context, ob, extend=event.shift, face_index=idx)
                if picked and not event.shift:      # pressed again off its guide: say why it did not slide
                    set_note(context, event, "Place: press where it meets its guide to slide it")
                    b = 'select_placed_off_guide'
                return _branch_done(b, {'FINISHED'})
            # nothing to place, or no guide under the press: say so, do not start the operator (it waited for a move
            # onto a guide, an invisible state, Fable review 2026-10-21)
            if b == 'no_pieces':
                set_note(context, event, "Place: click pattern pieces first (Shift: more), then a guide")
                return _branch_done(b, {'CANCELLED'})
            if b == 'no_guide':
                set_note(context, event, "Place: press on a guide (the cylinders round the avatar)")
                return _branch_done(b, {'CANCELLED'})
            _branch['name'] = b
            r = _call("ac9_cloth_sim.place", event)
            if r is None:
                set_note(context, event, "Place: click pattern pieces first (Shift: more), then a guide")
                return {'CANCELLED'}
        if r is None:
            set_note(context, event, tx("{0}: nothing to work on yet", tx(NAMES[t])))
            return {'CANCELLED'}
        return {'FINISHED'}


class AC9_OT_SimPatternToolsSwitch(bpy.types.Operator):
    """Make the tool the active one (turning the pattern tools on for one of them); pressed again, back to Select
    Box"""
    bl_idname = "ac9_cloth_sim.use_tool"
    bl_label = "Use Tool"
    bl_options = {'INTERNAL'}

    tool: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT'

    def execute(self, context):
        props = context.scene.ac9_cloth_sim
        if active_tool(context) == self.tool:
            bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
            return {'FINISHED'}
        if self.tool in PATTERN_TOOL_IDS and not props.pattern_tools:
            props.pattern_tools = True                   # update: sync() registers them
        bpy.ops.wm.tool_set_by_id(name=self.tool)
        return {'FINISHED'}


def active_tool(context):
    """The id of the 3D view's active tool in the current mode, or None."""
    try:
        t = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except (AttributeError, TypeError, RuntimeError):
        return None
    return t.idname if t is not None else None


def tool_button(layout, context, tool_id, text, icon):
    """A sidebar button that makes a tool the active one, shown pressed while it is (user 2026-10-14: which of the
    buttons was on could not be told)."""
    op = layout.operator("ac9_cloth_sim.use_tool", text=text, icon=icon, depress=active_tool(context) == tool_id)
    op.tool = tool_id
    return op


# ---------------------------------------------------------------- the tools

def works_on(layout, tool):
    layout.label(text=tx("Works on: ") + tx(WORKS[tool][0]), icon='INFO')


# what each tool does, one line in its header after "Works on" (user 2026-10-21: only Edit Pattern and Edit Seams
# said it; the rest was only in the tooltips). X / Del deletes with every AC9 tool (DELETE_KEYS).
KEYS = {
    'DRAW_PIECE': "click corners  first corner / Enter: close  type mm: length  Shift 15 deg  Backspace undo",
    'DRAW_LINE': "click points on a piece  Enter: finish  Shift 15 deg  type mm: length  Backspace undo",
    'RECT': "drag, or click two corners  Shift square  Alt from center  type W Tab H Enter",
    'ELLIPSE': "drag, or click two corners  Shift circle  Alt from center  type W Tab H Enter",
    'IRECT': "drag inside a piece, or click two corners  Shift square  Alt from center",
    'IELLIPSE': "drag inside a piece, or click two corners  Shift circle  Alt from center",
    'ADD_SEAM': "click start and end on piece A, then on piece B (same end first)  Enter / click: add  F other way",
    'ADD_SEAM_MN': "side A: click start and end of each stretch, Enter  side B: the same, Enter: add",
    'MEASURE': "click: keep the ring  Tab direction  Ctrl+wheel tilt (Shift 1 deg)  click a line: pick",
    'TAPE_ALONG': "click points  Enter: keep  Backspace undo  Esc / right-click: cancel  click a line: pick",
    'TAPE_STRAIGHT': "click two points: pulled taut (over bulges, across hollows)  click a line: pick",
    'MOVE_LEVEL': "drag a line or a tape, release: keep  Esc / right-click: put back  Ctrl+wheel on a ring: tilt",
    'PLACE': ("click pieces (Shift more), press on a guide and drag  drag a placed piece: moves it  "
              "angle / height / tilt: AC9 Piece  on the garment: G / R the piece"),
}


MENU_TOOLS = {'MOVE_LEVEL', 'PLACE'}       # these show a right-click menu (draft_tool.AC9_MT_SimContext)


def _keys(layout, tool):
    layout.label(text=tx(KEYS[tool]) + tx("  X/Del delete") + (tx("  right-click: menu") if tool in MENU_TOOLS else ""))


# X / Delete in every AC9 tool (user 2026-10-21): the corner, notch or line end under the cursor, else the picked
# internal line, seam, measurement or pieces (draft_tool.AC9_OT_SimDeleteSelected); never the objects themselves
DELETE_KEYS = (
    ("ac9_cloth_sim.delete_selected", {"type": 'X', "value": 'PRESS'}, None),
    ("ac9_cloth_sim.delete_selected", {"type": 'DEL', "value": 'PRESS'}, None),
)


class AC9_WT_DrawPiece(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.draw_piece_tool"
    bl_label = "Draw Piece"
    bl_description = ("Draw a new pattern piece: click its corners, click the first corner or Enter to close. Type "
                      "a number for the side length, Ctrl: whole mm")
    bl_icon = "ops.gpencil.draw.poly"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", 'DRAW_PIECE')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'DRAW_PIECE')]}),
    ) + DELETE_KEYS

    def draw_settings(context, layout, tool):
        works_on(layout, 'DRAW_PIECE')
        _keys(layout, 'DRAW_PIECE')


class AC9_WT_DrawLine(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.draw_line_tool"
    bl_label = "Internal Line"
    bl_description = "Draw an internal line inside a piece: click its points, Enter to finish"
    bl_icon = "ops.gpencil.draw.line"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", 'DRAW_LINE')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'DRAW_LINE')]}),
    ) + DELETE_KEYS

    def draw_settings(context, layout, tool):
        works_on(layout, 'DRAW_LINE')
        _keys(layout, 'DRAW_LINE')


class AC9_WT_AddSeam(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.add_seam_tool"
    bl_label = "Add Seam"
    bl_description = ("Add a seam: click the start and end of a stretch of outline on one piece, then on the other "
                      "piece")
    bl_icon = "ops.curve.pen"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", 'ADD_SEAM')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'ADD_SEAM')]}),
    ) + DELETE_KEYS

    def draw_settings(context, layout, tool):
        works_on(layout, 'ADD_SEAM')
        _keys(layout, 'ADD_SEAM')


class AC9_WT_AddSeamMN(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.add_seam_mn_tool"
    bl_label = "Add Seam (M:N)"
    bl_description = ("Add an M:N seam (one side made of several stretches, on one piece or more): on side A click the "
                      "start and end of each stretch, Enter; then side B the same, Enter. The sides are cut by the "
                      "proportion of their lengths")
    bl_icon = "ops.curve.pen"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", 'ADD_SEAM_MN')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'ADD_SEAM_MN')]}),
    ) + DELETE_KEYS

    def draw_settings(context, layout, tool):
        works_on(layout, 'ADD_SEAM_MN')
        _keys(layout, 'ADD_SEAM_MN')


def _tape_keymap(tool):
    return (
        ("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", tool)]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", tool)]}),
    ) + DELETE_KEYS


class AC9_WT_Measure(bpy.types.WorkSpaceTool):
    """Tape Around (its id stays measure_tool: the keymaps and workspaces keep it)."""
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.measure_tool"
    bl_label = "Tape Around"
    bl_description = ("A tape round the body: the ring under the cursor shows, click keeps it. Tab: its direction, "
                      "Ctrl+wheel: tilt it (Shift: by 1 deg). Click a measured line: pick it. On the pattern: the "
                      "length between two points on the outlines")
    bl_icon = "ops.mesh.loopcut_slide"
    bl_widget = None
    bl_keymap = _tape_keymap('MEASURE') + (
        ("ac9_cloth_sim.tape_plane", {"type": 'TAB', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.tape_tilt", {"type": 'WHEELUPMOUSE', "value": 'PRESS', "ctrl": True},
         {"properties": [("delta", 5.0)]}),
        ("ac9_cloth_sim.tape_tilt", {"type": 'WHEELDOWNMOUSE', "value": 'PRESS', "ctrl": True},
         {"properties": [("delta", -5.0)]}),
        ("ac9_cloth_sim.tape_tilt", {"type": 'WHEELUPMOUSE', "value": 'PRESS', "ctrl": True, "shift": True},
         {"properties": [("delta", 1.0)]}),
        ("ac9_cloth_sim.tape_tilt", {"type": 'WHEELDOWNMOUSE', "value": 'PRESS', "ctrl": True, "shift": True},
         {"properties": [("delta", -1.0)]}),
    )

    def draw_settings(context, layout, tool):
        layout.prop(context.window_manager, "ac9_tape_plane", text="")
        layout.prop(context.window_manager, "ac9_tape_tilt")
        works_on(layout, 'MEASURE')
        _keys(layout, 'MEASURE')


class AC9_WT_TapeAlong(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.tape_along_tool"
    bl_label = "Tape Along"
    bl_description = ("A tape along the body's surface: click points along the way, Enter keeps it, Esc or "
                      "right-click cancels (it also has the straight length). Click a measured line: pick it")
    bl_icon = "ops.curve.draw"
    bl_widget = None
    bl_keymap = _tape_keymap('TAPE_ALONG')

    def draw_settings(context, layout, tool):
        works_on(layout, 'TAPE_ALONG')
        _keys(layout, 'TAPE_ALONG')


class AC9_WT_TapeStraight(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.tape_straight_tool"
    bl_label = "Tape Straight"
    bl_description = ("A tape pulled taut between two points of the body: it lies on the bulges and spans the "
                      "hollows, as a tape measure held tight (e.g. bust point to bust point, over the shoulder). Its "
                      "straight line through the body is kept too. Click a measured line: pick it")
    bl_icon = "ops.gpencil.draw.line"
    bl_widget = None
    bl_keymap = _tape_keymap('TAPE_STRAIGHT')

    def draw_settings(context, layout, tool):
        works_on(layout, 'TAPE_STRAIGHT')
        _keys(layout, 'TAPE_STRAIGHT')


class AC9_WT_MoveLevel(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.move_level_tool"
    bl_label = "Edit Measure"
    bl_description = ("Edit a measurement on the body: drag a measured line up or down, or a tape you laid to "
                      "another place (a ring slides with the cursor, a tape's nearest point follows it); Ctrl+wheel "
                      "over a ring tilts it forward / back (Shift: by 1 deg)")
    bl_icon = "ops.transform.edge_slide"
    bl_widget = None
    bl_keymap = _tape_keymap('MOVE_LEVEL') + (
        ("ac9_cloth_sim.context_menu", {"type": 'RIGHTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'EDIT_MEASURE')]}),
    ) + tuple(
        ("ac9_cloth_sim.measure_tilt_ring", {"type": w, "value": 'PRESS', "ctrl": True, **({"shift": True} if s else {})},
         {"properties": [("delta", (1.0 if s else 5.0) * (1 if w == 'WHEELUPMOUSE' else -1))]})
        for w in ('WHEELUPMOUSE', 'WHEELDOWNMOUSE') for s in (False, True))

    def draw_settings(context, layout, tool):
        works_on(layout, 'MOVE_LEVEL')
        _keys(layout, 'MOVE_LEVEL')


class AC9_WT_Place(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "ac9_cloth_sim.place_tool"
    bl_label = "Place"
    bl_description = ("Click pattern pieces to pick them (Shift: more), then press on a guide round the body to place "
                      "them there; drag to slide them round and along it, release to keep")
    bl_icon = "ops.mesh.primitive_cylinder_add_gizmo"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", 'PLACE')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'PLACE')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True},
         {"properties": [("tool", 'PLACE')]}),
        ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True},
         {"properties": [("tool", 'PLACE')]}),
        ("ac9_cloth_sim.context_menu", {"type": 'RIGHTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'PLACE')]}),
    ) + DELETE_KEYS

    def draw_settings(context, layout, tool):
        works_on(layout, 'PLACE')
        _keys(layout, 'PLACE')


def _pattern_tools():
    from . import draft_tool, seam_edit_tool, shape_tool as ST
    # (class, starts a group); a group's later members follow its first
    return [(draft_tool.AC9_WT_TransformPattern, False), (draft_tool.AC9_WT_EditPattern, False), (AC9_WT_DrawPiece, True), (ST.AC9_WT_Rect, None),
            (ST.AC9_WT_Ellipse, None), (AC9_WT_DrawLine, True), (ST.AC9_WT_IRect, None), (ST.AC9_WT_IEllipse, None),
            (seam_edit_tool.AC9_WT_EditSeams, True), (AC9_WT_AddSeam, None),
            (AC9_WT_AddSeamMN, None)]


def _always_tools():
    # Edit Measure and the tapes in one group, like Edit Seams / Add Seam (user 2026-10-21)
    return [(AC9_WT_MoveLevel, True), (AC9_WT_Measure, None), (AC9_WT_TapeAlong, None), (AC9_WT_TapeStraight, None),
            (AC9_WT_Place, False)]


PATTERN_TOOL_IDS = ("ac9_cloth_sim.transform_pattern_tool", "ac9_cloth_sim.edit_pattern_tool", "ac9_cloth_sim.draw_piece_tool",
                    "ac9_cloth_sim.draw_line_tool", "ac9_cloth_sim.edit_seams_tool", "ac9_cloth_sim.add_seam_tool",
                    "ac9_cloth_sim.add_seam_mn_tool",
                    "ac9_cloth_sim.rect_tool", "ac9_cloth_sim.ellipse_tool", "ac9_cloth_sim.internal_rect_tool",
                    "ac9_cloth_sim.internal_ellipse_tool")


def _register_tools(show_pattern):
    seq = (_pattern_tools() if show_pattern else []) + _always_tools()
    first_of_bunch = {id(seq[0][0])} | ({id(_always_tools()[0][0])} if show_pattern else set())
    prev = None
    for cls, group in seq:
        kw = {}
        if group is None:                                # joins the group started by the tool before it
            kw['after'] = {prev.bl_idname}
        else:
            kw['separator'] = id(cls) in first_of_bunch
            kw['group'] = bool(group)
        bpy.utils.register_tool(cls, **kw)
        _registered.append(cls)
        prev = cls


def _unregister_tools():
    while _registered:
        cls = _registered.pop()
        try:
            bpy.utils.unregister_tool(cls)
        except Exception:
            pass


def _drop_active_pattern_tool():
    """Workspaces holding a pattern tool switch to Select Box (a removed tool would stay active, its keymap gone)."""
    wm = bpy.context.window_manager
    for win in wm.windows if wm else []:
        try:
            if win.workspace.tools.from_space_view3d_mode('OBJECT').idname not in PATTERN_TOOL_IDS:
                continue
        except Exception:
            continue
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                try:
                    with bpy.context.temp_override(window=win, area=area, region=region):
                        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
                except Exception:
                    pass
                break


def sync(context=None):
    """Register the tools as the scene's Pattern Tools switch says (re-registering all: their order and the
    separators stay put)."""
    context = context or bpy.context
    scene = getattr(context, 'scene', None)
    if auto_on(context):
        return                          # the switch's update ran sync with it on
    show = bool(scene is not None and hasattr(scene, 'ac9_cloth_sim') and scene.ac9_cloth_sim.pattern_tools)
    shown = any(c.bl_idname in PATTERN_TOOL_IDS for c in _registered)
    if _registered and show == shown:
        return
    if not show:
        _drop_active_pattern_tool()
    _unregister_tools()
    _register_tools(show)


def auto_on(context):
    """The first time the file has a garment, the Pattern Tools switch on (user 2026-10-22). True when it was turned
    on now. Cheap: one property read once it is decided."""
    scene = getattr(context, 'scene', None)
    props = getattr(scene, 'ac9_cloth_sim', None) if scene is not None else None
    if props is None or props.pattern_tools_set or props.pattern_tools:
        return False
    from . import store
    try:
        if store.get(context)[0] is None:
            return False
    except Exception:
        return False
    props.pattern_tools = True          # its update marks it decided and registers the tools
    return True


# ---------------------------------------------------------------- the pattern view's toolbar

# Besides the add-on's tools the pattern view's toolbar keeps only these (user 2026-10-28, DECISIONS
# cloth-sim-work-screen-layout-2026-10-27): the Select bunch, Move, Rotate and the Annotate bunch. Only what is shown:
# a hidden tool can still be picked by key or Shift+Space. The other 3D view's toolbar is left whole.
PATTERN_VIEW_KEEP = frozenset((
    "builtin.select_box", "builtin.select", "builtin.select_circle", "builtin.select_lasso",
    "builtin.move", "builtin.rotate",
    "builtin.annotate", "builtin.annotate_line", "builtin.annotate_polygon", "builtin.annotate_eraser"))
_ORIG_DRAW = "_ac9_orig_draw"              # on VIEW3D_PT_tools_active: its own draw, kept once over F3


def _kept(item):
    return item.idname.startswith("ac9_cloth_sim.") or item.idname in PATTERN_VIEW_KEEP


def _pattern_view_tools(items):
    """The toolbar's items (tools, bunches as tuples, None for a separator) cut to the kept ones; separators and
    bunches that end up empty or doubled are dropped."""
    out = []
    for item in items:
        if item is None:
            if out and out[-1] is not None:
                out.append(None)
        elif type(item) is tuple:
            sub = tuple(i for i in item if i is not None and _kept(i))
            if sub:
                out.append(sub if len(sub) > 1 else sub[0])
        elif _kept(item):
            out.append(item)
    while out and out[-1] is None:
        out.pop()
    return out


def _in_pattern_view(context):
    """The Work Screen's pattern view: the 3D view whose rotation work_screen.pattern_view locked."""
    sd = context.space_data
    return sd is not None and sd.type == 'VIEW_3D' and sd.region_3d is not None and sd.region_3d.lock_rotation


_cut = {'cls': None}                       # the helper class lending draw_cls the cut list, made in _wrap_toolbar


def _toolbar_draw(self, context):
    """VIEW3D_PT_tools_active.draw: in the pattern view in Object mode the cut list, elsewhere Blender's own. Any
    failure (a newer Blender's toolbar code) falls back to the whole toolbar."""
    from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active as TA
    orig = getattr(TA, _ORIG_DRAW)
    cls = _cut['cls']
    try:
        if cls is None or context.mode != 'OBJECT' or not _in_pattern_view(context):
            return orig(self, context)
        cls.items = _pattern_view_tools(TA.tools_from_context(context))
    except Exception:
        return orig(self, context)
    try:
        cls.draw_cls(self.layout, context)
    except Exception:
        orig(self, context)


def _wrap_toolbar():
    from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active as TA
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper

    class CutTools(ToolSelectPanelHelper):          # not registered
        _tool_group_active = TA._tool_group_active  # the bunch's shown tool, shared with the whole toolbar
        items = []

        @classmethod
        def tools_from_context(cls, context, mode=None):
            return iter(cls.items)
    _cut['cls'] = CutTools
    if not hasattr(TA, _ORIG_DRAW):              # an F3 without unregister must not wrap the wrapper
        setattr(TA, _ORIG_DRAW, TA.draw)
    TA.draw = _toolbar_draw


def _unwrap_toolbar():
    from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active as TA
    orig = getattr(TA, _ORIG_DRAW, None)
    if orig is not None:
        TA.draw = orig
        delattr(TA, _ORIG_DRAW)


_ORIG_POLL = "_ac9_orig_poll"              # on VIEW3D_PT_active_tool_duplicate: its own poll, kept once over F3


def _active_tool_poll(cls, context):
    """The Properties editor's copy of the active tool's settings (Move: orientation, drag), hidden in the Work
    Screen: its Tool tab is for the piece and the run, the tool's settings stay in the 3D view's header. A name
    compare only (it runs on every redraw); any failure falls back to Blender's own poll."""
    orig = getattr(cls, _ORIG_POLL, None)
    try:
        from . import work_screen as WS
        a, ws = context.area, context.window.workspace if context.window else None
        if a is not None and a.type == 'PROPERTIES' and ws is not None and ws.name.startswith(WS.NAME):
            return False
    except Exception:
        pass
    return orig.__func__(cls, context) if orig is not None else True


def _wrap_active_tool():
    from bl_ui.space_view3d import VIEW3D_PT_active_tool_duplicate as AT
    if 'poll' not in AT.__dict__:                # only a poll it had when registered is called (bpy_rna)
        return
    if not hasattr(AT, _ORIG_POLL):
        setattr(AT, _ORIG_POLL, AT.__dict__['poll'])
    AT.poll = classmethod(_active_tool_poll)


def _unwrap_active_tool():
    from bl_ui.space_view3d import VIEW3D_PT_active_tool_duplicate as AT
    orig = getattr(AT, _ORIG_POLL, None)
    if orig is not None:
        AT.poll = orig
        delattr(AT, _ORIG_POLL)


def _sync_later():
    try:
        sync()
    except Exception:
        pass
    return None


@persistent
def _on_load(_):
    bpy.app.timers.register(_sync_later, first_interval=0.0)


CLASSES = (AC9_OT_SimToolHover, AC9_OT_SimToolClick, AC9_OT_SimPatternToolsSwitch)


def register():
    from . import placed_view as PLV
    for handlers, callback in ((bpy.app.handlers.depsgraph_update_post, PLV.watch_control_selection),
                               (bpy.app.handlers.load_post, PLV.clear_control_hints),
                               (bpy.app.handlers.undo_post, PLV.clear_control_hints),
                               (bpy.app.handlers.redo_post, PLV.clear_control_hints)):
        if callback not in handlers:
            handlers.append(callback)
    for c in CLASSES:
        bpy.utils.register_class(c)
    # F3 registers with the scene out of reach and a timer added then never ran (GUI probe 2026-10-14): what the
    # toolbar showed before F3 is kept over the reload. At startup and on loading a file the switch decides
    _register_tools(bool(bpy.app.driver_namespace.pop(_KEEP, False)))
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_sync_later, first_interval=0.0)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_hint, (), 'WINDOW', 'POST_PIXEL'))
    try:
        _wrap_toolbar()
    except Exception:                     # Blender's toolbar code changed: the whole toolbar everywhere
        pass
    try:
        _wrap_active_tool()
    except Exception:                     # Blender's panel changed: shown as before
        pass


def unregister():
    from . import placed_view as PLV
    for handlers, callback in ((bpy.app.handlers.depsgraph_update_post, PLV.watch_control_selection),
                               (bpy.app.handlers.load_post, PLV.clear_control_hints),
                               (bpy.app.handlers.undo_post, PLV.clear_control_hints),
                               (bpy.app.handlers.redo_post, PLV.clear_control_hints)):
        for fn in list(handlers):
            if getattr(fn, '__module__', '') == PLV.__name__ and getattr(fn, '__name__', '') == callback.__name__:
                handlers.remove(fn)
    PLV.clear_control_hints()
    try:
        _unwrap_toolbar()
    except Exception:
        pass
    try:
        _unwrap_active_tool()
    except Exception:
        pass
    _cut['cls'] = None
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_sync_later):
        bpy.app.timers.unregister(_sync_later)
    bpy.app.driver_namespace[_KEEP] = any(c.bl_idname in PATTERN_TOOL_IDS for c in _registered)
    _unregister_tools()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
