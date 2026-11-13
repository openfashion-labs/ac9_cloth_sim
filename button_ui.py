# SPDX-License-Identifier: GPL-3.0-or-later
"""Buttons, the screen (user 2026-10-15, DECISIONS cloth-sim-fasteners-alpha-2026-10-15 / cloth-sim-buttons-flow-
2026-10-22). No panel of its own since 2026-10-22: the tools in the Pattern panel's Fasteners group (draw_tools), the
buttons in Lists > Fasteners (draw_list), the picked one in Properties > Tool, AC9 Button (draw_button_tab), and a
piece's Closes onto in AC9 Piece (draw_closes_onto).

Placing: Add (click the dressed garment or a piece of the flat pattern, one button a click, its hole with it), Line Up
(N along the internal line picked in Edit Pieces, else the active piece's closure line, else one of its edges at an
inset; the Redo panel sets the number and the spacing), From BOM (a button at each button stitch of a BOM).
Each button's hole goes on the piece its piece closes onto (Closes onto: an eyedropper, by hand) by
core/buttons.hole_for; a button with none is for show. One at a time: Set Hole (click the hole's place, another piece
or its own), No Hole, Hole Auto (from Closes onto again). Fasten / Unfasten (one, or all): the next Simulate opens or
closes the garment there. Style: one of the library's (core/button_shape.py) or an object of the scene (Use Object).

The buttons are spec["buttons"] (core/buttons.py); the 3D look and the pattern's are button_view.py.
"""
import textwrap

import bpy
import numpy as np

from . import button_view as BV
from . import units as U
from . import pattern_view as PV
from . import store
from .core import button_shape as BS
from .core import buttons as BT
from .core import spec as S
from .i18n import rpt, tx

_enum_keep = []


def _style_items(self, context):
    spec = store.get(context)[0] if context else None
    user = (spec or {}).get('button_styles', [])
    items = []
    for sid in BS.ids(user):
        st = BS.style(sid, user)
        items.append((sid, st.get('name') or sid, st.get('description', '') or sid))
    _enum_keep[:] = items                      # Blender keeps no reference to a callback's strings
    return items


def _garment(spec):
    from . import dress as D
    return bpy.data.objects.get(D.garment_name(spec)) if spec else None


def refresh(context, spec):
    """The buttons drawn again: on the dressed garment (when there is a result) and on the flat pattern."""
    from . import dress as D
    g = _garment(spec)
    try:
        BV.sync(context, spec, g, D.job_of(g) if g is not None else None)
    except Exception as exc:                   # a missing job leaves the pattern's buttons to draw
        print("AC9 Cloth Sim buttons:", exc)
    BV.sync_flat(context, spec)
    for a in context.screen.areas if context.screen else ():
        if a.type == 'VIEW_3D':
            a.tag_redraw()


def commit(context, spec):
    """Write the buttons (and the closure seams they take over) and draw them. Returns the seams turned on/off."""
    changed = BT.sync_closure_seams(spec)
    store.put(context, spec)
    refresh(context, spec)
    return changed


def piece_at(spec, outlines, q):
    """The enabled piece whose outline holds layout point q (mm), or None."""
    for pc in spec['pieces']:
        if not pc.get('enabled', True) or pc['name'] not in outlines:
            continue
        B = outlines[pc['name']].B
        lo, hi = B.min(0), B.max(0)
        if lo[0] <= q[0] <= hi[0] and lo[1] <= q[1] <= hi[1] and BT._inside(B, q):
            return pc['name']
    return None


def view_under(context, event):
    """(area, region, x, y in the region) of the 3D view under the mouse, or None: the clicks of the button tools
    land in a 3D view though they may be started from the Properties editor's Tool tab."""
    win = context.window
    for a in (win.screen.areas if win else ()):
        if a.type != 'VIEW_3D':
            continue
        for r in a.regions:
            if r.type == 'WINDOW' and r.x <= event.mouse_x < r.x + r.width and r.y <= event.mouse_y < r.y + r.height:
                return a, r, event.mouse_x - r.x, event.mouse_y - r.y
    return None


def point_under(context, event, spec, outlines):
    """(piece, layout point mm) under the mouse: on the dressed garment first, else on the flat pattern."""
    v = view_under(context, event)
    if v is None:
        return None, None
    a, r, mx, my = v
    with context.temp_override(area=a, region=r):
        return point_in_view(bpy.context, mx, my, spec, outlines)


def point_in_view(context, mx, my, spec, outlines):
    """point_under in the 3D view of the context, at region (mx, my)."""
    from . import garment_mark as GM
    from . import draft_tool as T
    g = _garment(spec)
    if g is not None and g.visible_get():
        q = GM.mark(context, g, mx, my)
        if q is not None:
            q = np.asarray(q, float)
            name = piece_at(spec, outlines, q)
            if name is not None:
                return name, q
    q = T.mouse_mm(context, PV.layout_matrix(context), mx, my)
    if q is None:
        return None, None
    name = piece_at(spec, outlines, q)
    return (name, q) if name is not None else (None, None)


def _find(spec, bid):
    return next((b for b in spec.get('buttons', []) or [] if b.get('id') == bid), None)


def active_id(context, spec):
    bid = context.scene.ac9_button_active
    if bid and _find(spec, bid) is None and spec.get('buttons'):       # removed: the last one ('' = none picked)
        return spec['buttons'][-1]['id']
    return bid if _find(spec, bid) else ''


def selected_ids(context, spec):
    """The picked buttons: the active one, then the others Shift+clicked in Lists > Fasteners (user 2026-10-23:
    every list picks alike, and what acts on several acts on the picked ones -- no "All" buttons)."""
    have = {b['id'] for b in spec.get('buttons') or []}
    act = active_id(context, spec)
    more = [i for i in context.scene.ac9_button_sel.split(',') if i in have and i != act]
    return ([act] if act else []) + more


def _ids(context, spec, one):
    return [one] if one else selected_ids(context, spec)


class _Click:
    """A modal: left click in a 3D view places (on the garment or the pattern), right click / Esc ends. Started from
    the sidebar or the Properties editor's Tool tab alike; the hint is in the status bar."""
    hint = ""

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and context.window is not None

    def invoke(self, context, event):
        context.workspace.status_text_set(self.hint + "   right click / Esc: done")
        context.window_manager.modal_handler_add(self)
        self.count = 0
        return {'RUNNING_MODAL'}

    def _end(self, context):
        context.workspace.status_text_set(None)
        for a in context.window.screen.areas if context.window else ():
            a.tag_redraw()

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'FINISHED'} if self.count else {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if view_under(context, event) is None:
                return {'PASS_THROUGH'}             # a click in a panel is the panel's (the tool goes on)
            spec, outlines = store.get(context)
            sp = S.loads(S.dumps(spec))
            name, q = point_under(context, event, sp, outlines)
            if name is None:
                self.report({'WARNING'}, "Click a piece of the garment or the pattern")
                return {'RUNNING_MODAL'}
            done = self.place(context, sp, outlines, name, q)
            if done is None:
                return {'RUNNING_MODAL'}
            self.count += 1
            bpy.ops.ed.undo_push(message=self.bl_label)
            if done:
                self._end(context)
                return {'FINISHED'}
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


class AC9_OT_SimButtonAdd(_Click, bpy.types.Operator):
    """Click the dressed garment or a piece of the flat pattern: a button there, its hole on the piece it closes onto (the piece's Closes onto), fastened; without one, a button for show"""
    bl_idname = "ac9_cloth_sim.button_add"
    bl_label = "Add Buttons"
    hint = "Add buttons: click the garment or a pattern piece"

    def place(self, context, sp, outlines, name, q):
        b = BT.add(sp, name, q, context.scene.ac9_button_style, outlines=outlines)
        commit(context, sp)
        context.scene.ac9_button_active = b['id']
        context.scene.ac9_button_sel = ''
        self.report({'INFO'}, rpt("{0} on {1}", b['id'], name) + (rpt(", its hole on {0}", b['hole']['piece']) if b['hole']
                                                         else rpt(" (for show: no hole)")))
        return False


class AC9_OT_SimButtonMove(_Click, bpy.types.Operator):
    """Move the active button, or put its hole where you click (the garment or the pattern; another piece, or its own piece 20 mm or more away for a cuff). A hole set by hand stays when Closes onto changes"""
    bl_idname = "ac9_cloth_sim.button_move"
    bl_label = "Move Button"

    which: bpy.props.EnumProperty(items=[('BUTTON', "Button", ""), ('HOLE', "Hole", "")], options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return _Click.poll(context) and bool(spec.get('buttons'))

    def invoke(self, context, event):
        bid = active_id(context, store.get(context)[0])
        self.hint = (f"Set Hole of {bid}: click where its hole goes" if self.which == 'HOLE'
                     else f"Move Button {bid}: click its new place")
        return _Click.invoke(self, context, event)

    def place(self, context, sp, outlines, name, q):
        b = _find(sp, active_id(context, sp))
        if b is None:
            return True
        pt = [round(float(q[0]), 2), round(float(q[1]), 2)]
        if self.which == 'HOLE':
            # on its own piece too, away from the button (a cuff closing on itself, user 2026-10-22 b-3)
            if name == b['piece'] and np.linalg.norm(np.asarray(pt) - np.asarray(b['pt'], float)) < BT.SELF_MIN_MM:
                self.report({'WARNING'}, rpt("On its own piece the hole goes {0} or more from the button", U.fmt(context, BT.SELF_MIN_MM)))
                return None
            b['hole'] = {'piece': name, 'pt': pt}
            b['hole_by'] = 'hand'
            b['fastened'] = True
        else:
            b['piece'], b['pt'] = name, pt
        commit(context, sp)
        self.report({'INFO'}, rpt("{0}: button on {1}" if self.which == 'BUTTON' else "{0}: hole on {1}", b['id'], name))
        return True


class AC9_OT_SimButtonLineUp(bpy.types.Operator):
    """Line up buttons on the active piece: along its closure line (where it is sewn to the piece it closes onto), else along an edge at an inset; each with its hole"""
    bl_idname = "ac9_cloth_sim.button_line_up"
    bl_label = "Line Up Buttons"
    bl_options = {'REGISTER', 'UNDO'}

    count: bpy.props.IntProperty(name="Count", default=6, min=1, max=40)
    top: bpy.props.FloatProperty(name="From Top", default=0.03, min=0.0, max=1.0, precision=1, subtype='DISTANCE', unit='LENGTH', step=U.LEN_STEP, 
                                 description="The first button this far down the line")
    bottom: bpy.props.FloatProperty(name="From Bottom", default=0.05, min=0.0, max=1.0, precision=1, subtype='DISTANCE', unit='LENGTH', step=U.LEN_STEP, 
                                    description="The last button this far above the line's end (Spacing 0)")
    spacing: bpy.props.FloatProperty(name="Spacing", default=0.0, min=0.0, max=0.5, precision=1, subtype='DISTANCE', unit='LENGTH', step=U.LEN_STEP, 
                                     description="Between buttons; 0: spread from the top to the bottom")
    along: bpy.props.StringProperty(name="Along Line", default="",
                                    description="An internal line of the piece to line up on (the one picked in Edit "
                                                "Pattern when Line Up is pressed)", options={'SKIP_SAVE'})
    edge: bpy.props.StringProperty(name="Edge", default="",
                                   description="An edge id of the piece to line up along instead of the closure line",
                                   options={'SKIP_SAVE'})     # not remembered: another piece has other edges
    inset: bpy.props.FloatProperty(name="Inset", default=0.015, min=0.0, max=0.2, precision=1, subtype='DISTANCE', unit='LENGTH', step=U.LEN_STEP, 
                                   description="Along an edge: this far inside it")
    replace: bpy.props.BoolProperty(name="Replace", default=True,
                                    description="Remove the piece's buttons first")
    piece: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec = store.get(context)[0]
        # an internal line picked in Edit Pieces (a placket's center line): on it (user 2026-10-22)
        from . import draft_tool as DT
        ob = context.active_object
        name = ob.get(PV.PIECE_PROP) if ob is not None else None
        s = DT.selected_internal(context, spec)
        if s is not None and len(s[2]['pts']) >= 2 and name in (None, s[0]):
            self.piece, self.along, self.edge = s[0], s[1], ''
            return self.execute(context)
        if name not in S.piece_index(spec):
            # no pattern piece selected: the first piece that closes onto another (Closes onto, else spec closures)
            cl = [pc['name'] for pc in spec['pieces'] if BT.closes_onto(spec, pc['name'])] + \
                [c[0] for c in spec.get('closures') or []]
            name = cl[0] if cl else None
        if name is None:
            self.report({'WARNING'}, "Select a pattern piece")
            return {'CANCELLED'}
        self.piece = name
        return self.execute(context)

    def execute(self, context):
        spec, outlines = store.get(context)
        sp = S.loads(S.dumps(spec))
        if self.piece not in S.piece_index(sp):
            return {'CANCELLED'}
        it = next((x for x in S.piece_index(sp)[self.piece].get('internals', []) if x['id'] == self.along),
                  None) if self.along else None
        if it is not None:
            line = np.asarray(it['pts'], float)
            line = line if line[0, 1] >= line[-1, 1] else line[::-1]
        elif self.edge:
            try:
                line = BT.edge_line(sp, self.piece, self.edge, self.inset * 1000.0)
            except StopIteration:
                self.report({'WARNING'}, rpt("{0} has no edge {1}", self.piece, self.edge))
                return {'CANCELLED'}
        else:
            line = BT.closure_line(sp, self.piece, outlines)
            if line is None:
                pc = S.piece_index(sp)[self.piece]
                eid = max(pc['edges'], key=lambda e: S.polyline_len(np.asarray(e['pts'], float)))['id']
                line = BT.edge_line(sp, self.piece, eid, self.inset * 1000.0)
                self.edge = eid
        if self.replace:
            sp['buttons'] = [b for b in sp.get('buttons', []) or [] if b.get('piece') != self.piece]
        pts = BT.line_up(line, self.count, self.top * 1000.0, self.bottom * 1000.0, self.spacing * 1000.0)   # fields: m
        no_hole = 0
        for q in pts:
            b = BT.add(sp, self.piece, q, context.scene.ac9_button_style, outlines=outlines)
            no_hole += b['hole'] is None
        commit(context, sp)
        if pts:
            context.scene.ac9_button_active = sp['buttons'][-1]['id']
            context.scene.ac9_button_sel = ''
        gap = float(np.linalg.norm(np.diff(np.asarray(pts), axis=0), axis=1).mean()) if len(pts) > 1 else 0.0
        self.report({'INFO'}, rpt("{0} button(s) on {1}", len(pts), self.piece) + (rpt(", {0} apart", U.fmt(context, gap)) if gap else "")
                    + (rpt("; {0} without a hole", no_hole) if no_hole else ""))
        return {'FINISHED'}


def _stitched(context, spec):
    """The piece From BOM puts buttons on: the selected pattern piece if it is sewn to another at button stitches,
    else the first enabled piece that is (spec closures are not looked at: a button put by Add wrote them before)."""
    ob = context.active_object
    name = ob.get(PV.PIECE_PROP) if ob is not None else None
    if name in S.piece_index(spec) and BT.stitched_partners(spec, name):
        return name
    on = [pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)]
    return next((n for n in on if BT.stitched_partners(spec, n)), None)


class AC9_OT_SimButtonFromStitches(bpy.types.Operator):
    """The buttons a BOM wrote as short seams (each button sewn to its hole, between internal lines of two pieces): a button and its hole at each, those seams turned off, the two pieces' Closes onto set to each other"""
    bl_idname = "ac9_cloth_sim.button_from_stitches"
    bl_label = "From BOM"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or _stitched(context, spec) is None:
            cls.poll_message_set("No buttons in the BOM (short seams between internal lines of two pieces): use Line "
                                 "Up or Add Button")
            return False
        return True

    def execute(self, context):
        spec, outlines = store.get(context)
        sp = S.loads(S.dumps(spec))
        name = _stitched(context, sp)
        if name is None:
            return {'CANCELLED'}
        pts = BT.stitch_points(sp, name, outlines)
        if not pts:
            self.report({'WARNING'}, rpt("{0}: no button stitches", name))
            return {'CANCELLED'}
        # what closes onto what, from the export: the two pieces' Closes onto (a front closure when both are on the
        # torso, core/roles.declared_closures; user 2026-10-22). A closure From BOM wrote before goes
        other = BT.stitched_partners(sp, name)[0]
        cl = [list(c) for c in sp.get('closures') or [] if set(c) != {name, other}]
        if sp.get('closures') is not None and len(cl) != len(sp['closures']):
            if cl:
                sp['closures'] = cl
            else:
                sp.pop('closures')
        sp['buttons'] = [b for b in sp.get('buttons', []) or [] if b.get('piece') != name]
        BT.set_closes_onto(sp, name, other, outlines)
        for q in pts:
            BT.add(sp, name, q, context.scene.ac9_button_style, outlines=outlines)
        off = commit(context, sp)
        context.scene.ac9_button_active = sp['buttons'][-1]['id']
        context.scene.ac9_button_sel = ''
        self.report({'INFO'}, rpt("{0} button(s) on {1}; seams off: {2}", len(pts), name, ', '.join(off) or rpt('none')))
        return {'FINISHED'}


class AC9_OT_SimButtonFasten(bpy.types.Operator):
    """Fasten or unfasten the button (or the picked ones): Live Sim then closes or opens the garment there"""
    bl_idname = "ac9_cloth_sim.button_fasten"
    bl_label = "Fasten"
    bl_options = {'REGISTER', 'UNDO'}

    button: bpy.props.StringProperty(options={'HIDDEN'})          # '' = the picked ones
    state: bpy.props.BoolProperty(options={'HIDDEN'}, default=True)

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('buttons'))

    def execute(self, context):
        spec = store.get(context)[0]
        sp = S.loads(S.dumps(spec))
        n = 0
        ids = set(_ids(context, sp, self.button))
        for b in sp['buttons']:
            if b['id'] in ids and b.get('hole'):
                n += b.get('fastened', True) != self.state
                b['fastened'] = self.state
        commit(context, sp)
        self.report({'INFO'}, rpt("{0} button(s) fastened: Live Sim to close the garment there" if self.state else
                              "{0} button(s) unfastened: Live Sim to open the garment there", n))
        return {'FINISHED'}


class AC9_OT_SimButtonRemove(bpy.types.Operator):
    """Remove the button (and its hole), or the picked ones"""
    bl_idname = "ac9_cloth_sim.button_remove"
    bl_label = "Remove Button"
    bl_options = {'REGISTER', 'UNDO'}

    button: bpy.props.StringProperty(options={'HIDDEN'})          # '' = the picked ones

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('buttons'))

    def execute(self, context):
        spec = store.get(context)[0]
        sp = S.loads(S.dumps(spec))
        before = len(sp['buttons'])
        ids = set(_ids(context, sp, self.button))
        sp['buttons'] = [b for b in sp['buttons'] if b['id'] not in ids]
        context.scene.ac9_button_sel = ''
        on = commit(context, sp)
        self.report({'INFO'}, rpt("{0} removed", before - len(sp['buttons'])) + (rpt("; seams on again: {0}", ', '.join(on))
                                                                         if on else ""))
        return {'FINISHED'}


class AC9_OT_SimButtonSelect(bpy.types.Operator):
    """Pick this button (or this closure's buttons): their settings in Properties > Tool (AC9 Button).
Shift+click adds them to the picked ones or takes them out"""
    bl_idname = "ac9_cloth_sim.button_select"
    bl_label = "Select Button"
    bl_options = {'INTERNAL'}

    button: bpy.props.StringProperty(options={'HIDDEN'})
    group: bpy.props.StringProperty(options={'HIDDEN'})            # several ids, comma separated (a closure)
    extend: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        spec = store.get(context)[0]
        ids = [i for i in (self.group.split(',') if self.group else [self.button]) if i]
        sel = selected_ids(context, spec) if spec is not None else []
        if not self.extend:
            sel = ids
        elif all(i in sel for i in ids):                            # all picked already: take them out
            sel = [i for i in sel if i not in ids]
        else:                                                       # the one clicked becomes the active one
            new = [i for i in ids if i not in sel]
            sel = new[:1] + sel + new[1:]
        context.scene.ac9_button_active = sel[0] if sel else ''
        context.scene.ac9_button_sel = ','.join(sel[1:])
        from . import tool_tab
        tool_tab.set_focus('BUTTON')
        return {'FINISHED'}


class AC9_OT_SimButtonNoHole(bpy.types.Operator):
    """The active button without a hole: sewn on for show, holding nothing (it stays so when Closes onto changes)"""
    bl_idname = "ac9_cloth_sim.button_no_hole"
    bl_label = "No Hole"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        b = _find(spec, active_id(context, spec)) if spec else None
        return b is not None and b.get('hole') is not None

    def execute(self, context):
        sp = S.loads(S.dumps(store.get(context)[0]))
        b = _find(sp, active_id(context, sp))
        b['hole'], b['fastened'], b['hole_by'] = None, False, 'hand'
        commit(context, sp)
        self.report({'INFO'}, rpt("{0}: no hole (for show)", b['id']))
        return {'FINISHED'}


class AC9_OT_SimButtonAutoHole(bpy.types.Operator):
    """The active button's hole from its piece's Closes onto again (a hole set by hand, or none, given back to it)"""
    bl_idname = "ac9_cloth_sim.button_auto_hole"
    bl_label = "Hole from Closes Onto"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        b = _find(spec, active_id(context, spec)) if spec else None
        return b is not None and b.get('hole_by') == 'hand'

    def execute(self, context):
        sp, outlines = store.get(context)
        sp = S.loads(S.dumps(sp))
        b = _find(sp, active_id(context, sp))
        b.pop('hole_by', None)
        BT.refill_holes(sp, outlines)
        commit(context, sp)
        self.report({'INFO'}, rpt("{0}: ", b['id']) + (rpt("hole on {0}", b['hole']['piece']) if b.get('hole') else
                                                rpt("no hole (its piece closes onto nothing)")))
        return {'FINISHED'}


def _piece_of(context, spec):
    from . import tool_tab
    return tool_tab._selected_piece(context, spec)


class AC9_OT_SimClosesOntoPick(_Click, bpy.types.Operator):
    """Closes onto: click the piece the selected piece buttons onto (on the garment or the pattern). Its buttons get their holes there; two torso pieces are the front closure, closed last"""
    bl_idname = "ac9_cloth_sim.closes_onto_pick"
    bl_label = "Closes Onto"

    piece: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return _Click.poll(context) and _piece_of(context, spec) is not None

    def invoke(self, context, event):
        self.piece = self.piece or _piece_of(context, store.get(context)[0]) or ''
        self.hint = f"Closes onto: click the piece {self.piece} buttons onto"
        return _Click.invoke(self, context, event)

    def place(self, context, sp, outlines, name, q):
        if name == self.piece:
            self.report({'WARNING'}, rpt("Click another piece than {0}", self.piece))
            return None
        changed = BT.set_closes_onto(sp, self.piece, name, outlines)
        commit(context, sp)
        self.report({'INFO'}, rpt("{0} closes onto {1}", self.piece, name) + (rpt("; holes of {0} button(s) follow", len(changed))
                                                                    if changed else ""))
        return True


class AC9_OT_SimClosesOntoClear(bpy.types.Operator):
    """The selected piece closes onto nothing (its buttons' holes from Closes onto go; holes set by hand stay)"""
    bl_idname = "ac9_cloth_sim.closes_onto_clear"
    bl_label = "Clear Closes Onto"
    bl_options = {'REGISTER', 'UNDO'}

    piece: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        sp, outlines = store.get(context)
        sp = S.loads(S.dumps(sp))
        name = self.piece or _piece_of(context, sp)
        if name not in S.piece_index(sp):
            return {'CANCELLED'}
        BT.set_closes_onto(sp, name, None, outlines)
        commit(context, sp)
        self.report({'INFO'}, rpt("{0} closes onto nothing", name))
        return {'FINISHED'}


class AC9_OT_SimButtonSetStyle(bpy.types.Operator):
    """Give the current style (above) to the picked buttons"""
    bl_idname = "ac9_cloth_sim.button_set_style"
    bl_label = "Set Style"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('buttons'))

    def execute(self, context):
        spec = store.get(context)[0]
        sp = S.loads(S.dumps(spec))
        ids = set(selected_ids(context, sp))
        sid = context.scene.ac9_button_style
        n = 0
        for b in sp['buttons']:
            if b['id'] in ids:
                b['style'] = sid
                n += 1
        commit(context, sp)
        self.report({'INFO'}, rpt("{0} button(s): {1}", n, sid))
        return {'FINISHED'}


class AC9_OT_SimButtonStyleFromObject(bpy.types.Operator):
    """Your own button: the active mesh object becomes a style (drawn as it is: its origin at the middle of its back, +Z out of the cloth, in meters)"""
    bl_idname = "ac9_cloth_sim.button_style_from_object"
    bl_label = "Use Object as Style"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return store.get(context)[0] is not None and ob is not None and ob.type == 'MESH' \
            and PV.PIECE_PROP not in ob and not ob.name.startswith('AC9_')

    def execute(self, context):
        spec = store.get(context)[0]
        sp = S.loads(S.dumps(spec))
        ob = context.active_object
        sid = "own_" + "".join(c if c.isalnum() else "_" for c in ob.name.lower())
        user = [s_ for s_ in sp.get('button_styles', []) or [] if s_.get('id') != sid]
        user.append({'id': sid, 'name': ob.name, 'kind': 'button', 'mesh': ob.name, 'author': '', 'url': '',
                     'license': ''})
        sp['button_styles'] = user
        store.put(context, sp)
        context.scene.ac9_button_style = sid
        refresh(context, sp)
        self.report({'INFO'}, rpt("Style {0}: {1}", sid, ob.name))
        return {'FINISHED'}


def _wrap(layout, text, n=34):
    for ln in textwrap.wrap(text, n):
        layout.label(text=ln)


def _hole_text(b):
    h = b.get('hole')
    if not h:
        return tx("for show")
    return (tx("own piece") if h['piece'] == b['piece'] else h['piece']) + (tx(" (by hand)")
                                                                             if b.get('hole_by') == 'hand' else "")


def draw_tools(layout, context, spec):
    """The Pattern panel's Fasteners group (user 2026-10-22, Fable buttons pairing review §3): the tools that put
    buttons, and what acts on all of them. A button's own settings are in Properties > Tool (AC9 Button)."""
    sc = context.scene
    col = layout.column(align=True)
    col.label(text="Fasteners")
    col.prop(sc, "ac9_button_style", text="")
    row = col.row(align=True)
    row.operator("ac9_cloth_sim.button_add", text="Add Button", icon='ADD')
    row.operator("ac9_cloth_sim.button_line_up", text="Line Up", icon='THREE_DOTS')
    # always there, grayed out with the reason when nothing is sewn at button stitches (user 2026-10-22)
    row = col.row(align=True)
    row.operator("ac9_cloth_sim.button_from_stitches", text="From BOM", icon='LINKED')
    col.operator("ac9_cloth_sim.button_style_from_object", text="Use Object", icon='MESH_DATA')
    # no Fasten All / Remove All (user 2026-10-23): pick them in Lists > Fasteners (a closure's name picks its
    # buttons, Shift adds) and act on them in Properties > Tool


def draw_list(layout, context, spec):
    """Lists > Fasteners: the buttons under the pieces they close (A -> B), then those for show, then the zippers.
    A list only shows and picks (user 2026-10-21): a row picked shows its settings in Properties > Tool."""
    bts = spec.get('buttons') or []
    zps = spec.get('zippers') or []
    if not bts and not zps:
        _wrap(layout, "No buttons yet: Pattern > Fasteners (Add Button, Line Up, From BOM).")
        return
    bid = active_id(context, spec) if bts else ''
    sel = set(selected_ids(context, spec)) if bts else set()
    groups = {}
    for b in bts:
        h = b.get('hole')
        key = (b['piece'], h['piece']) if h else (b['piece'], None)
        groups.setdefault(key, []).append(b)
    from . import ui as UI
    col = layout.column(align=True)
    # every list picks alike (user 2026-10-23): a click picks one, Shift+click adds / takes out; a closure's name
    # picks its buttons, the first row all of them; the lock fastens / unfastens that one at once
    r = col.row(align=True)
    r.alignment = 'LEFT'
    op = r.operator("ac9_cloth_sim.button_select", text=tx("All buttons ({0})", len(bts)), emboss=False,
                    icon='CHECKBOX_HLT' if bts and sel >= {b['id'] for b in bts} else 'CHECKBOX_DEHLT')
    op.group = ','.join(b['id'] for b in bts)
    shown = 0
    for (pa, pb), rows in sorted(groups.items(), key=lambda t: (t[0][1] is None, t[0])):
        head = tx("{0} > {1} ({2})", pa, tx("itself") if pb == pa else pb, len(rows)) if pb else \
            tx("{0}, for show ({1})", pa, len(rows))
        r = col.row(align=True)
        r.alignment = 'LEFT'
        op = r.operator("ac9_cloth_sim.button_select", text=head, emboss=False, icon='LINKED' if pb else 'DOT')
        op.group = ','.join(b['id'] for b in rows)
        for b in rows:
            if shown >= 60:
                break
            shown += 1
            on = bool(b.get('hole')) and b.get('fastened', True)

            def lock(r, b=b, on=on):
                sub = r.row(align=True)
                sub.enabled = bool(b.get('hole'))
                op = sub.operator("ac9_cloth_sim.button_fasten", text="", icon='LOCKED' if on else 'UNLOCKED',
                                  emboss=False)
                op.button, op.state = b['id'], not on
            # the rows of every list (REVIEW_fable_lists_ui_2026-10-25 section 2): picked = pressed, the one mark
            UI.draw_pick_row(col, "ac9_cloth_sim.button_select", b['id'], depress=b['id'] in sel,
                             extra=lock).button = b['id']
    if len(bts) > shown:
        col.label(text=tx("... {0} more", len(bts) - shown))
    if bts:
        n_open = sum(1 for b in bts if b.get('hole') and not b.get('fastened', True))
        if n_open:
            layout.label(text=tx("{0} unfastened: Live Sim opens the garment there", n_open), icon='UNLOCKED')
    for z in zps:
        layout.label(text=tx("{0}: {1} / {2}, closed {3:.0f}%", z.get('id', ''), z['a']['piece'], z['b']['piece'],
                             100.0 * float(z.get('closed', 1.0))), icon='TRIA_UP_BAR')
    if bid:
        layout.label(text="Shift+click: add / take out", icon='INFO')


def draw_button_tab(layout, context):
    """Properties > Tool, AC9 Button: the button picked in Lists > Fasteners."""
    spec = store.get(context)[0]
    b = _find(spec, active_id(context, spec))
    if b is None:
        return
    sel = selected_ids(context, spec)
    if len(sel) > 1:                # several picked (Shift+click in the list): only what acts on all of them
        layout.label(text=tx("{0} buttons selected", len(sel)), icon='RADIOBUT_ON')
        layout.label(text=", ".join(sel[:6]) + (" ..." if len(sel) > 6 else ""))
        row = layout.row(align=True)
        op = row.operator("ac9_cloth_sim.button_fasten", text="Fasten", icon='LOCKED')
        op.button, op.state = '', True
        op = row.operator("ac9_cloth_sim.button_fasten", text="Unfasten", icon='UNLOCKED')
        op.button, op.state = '', False
        row = layout.row(align=True)
        op = row.operator("ac9_cloth_sim.button_remove", text="Remove", icon='X')
        op.button = ''
        layout.separator()
        layout.prop(context.scene, "ac9_button_style", text="")
        layout.operator("ac9_cloth_sim.button_set_style", text="Set Style", icon='BRUSH_DATA')
        return
    layout.label(text=tx("Button {0}", b['id']), icon='RADIOBUT_ON')
    col = layout.column(align=True)
    col.label(text=tx("On: {0}", b['piece']))
    r = col.row()
    r.label(text=tx("Hole: {0}", _hole_text(b)))
    h = b.get('hole')
    co = BT.closes_onto(spec, b['piece'])
    if not h and b.get('hole_by') != 'hand':
        col.label(text=tx("{0} closes onto {1}: no hole found there", b['piece'], co) if co else
                  tx("{0} closes onto nothing (AC9 Piece)", b['piece']), icon='INFO')
    row = layout.row(align=True)
    op = row.operator("ac9_cloth_sim.button_move", text="Move Button", icon='VIEW_PAN')
    op.which = 'BUTTON'
    op = row.operator("ac9_cloth_sim.button_move", text="Set Hole", icon='PIVOT_CURSOR')
    op.which = 'HOLE'
    row = layout.row(align=True)
    row.operator("ac9_cloth_sim.button_no_hole", text="No Hole", icon='RADIOBUT_OFF')
    row.operator("ac9_cloth_sim.button_auto_hole", text="Hole Auto", icon='LOOP_BACK')
    row = layout.row(align=True)
    on = bool(h) and b.get('fastened', True)
    sub = row.row(align=True)
    sub.enabled = bool(h)
    op = sub.operator("ac9_cloth_sim.button_fasten", text="Fastened" if on else "Unfastened",
                      icon='LOCKED' if on else 'UNLOCKED', depress=on)
    op.button, op.state = b['id'], not on
    op = row.operator("ac9_cloth_sim.button_remove", text="Remove", icon='X')
    op.button = b['id']
    layout.separator()
    layout.label(text=tx("Style: {0}", b.get('style', '')))
    layout.prop(context.scene, "ac9_button_style", text="")
    layout.operator("ac9_cloth_sim.button_set_style", text="Set Style", icon='BRUSH_DATA')


def draw_closes_onto(col, context, spec, name):
    """AC9 Piece's Closes onto row: the piece it buttons onto, picked with the eyedropper (user 2026-10-22, by hand),
    x clears it; under it what it does (the front closure when both are on the torso)."""
    from .core import roles as RO
    co = BT.closes_onto(spec, name)
    sp = col.row().split(factor=0.4)
    r = sp.row()
    r.alignment = 'RIGHT'
    r.label(text="Closes Onto")
    r = sp.row(align=True)
    op = r.operator("ac9_cloth_sim.closes_onto_pick", text=co or tx("None"), icon='EYEDROPPER')
    op.piece = name
    if co:
        op = r.operator("ac9_cloth_sim.closes_onto_clear", text="", icon='X')
        op.piece = name
        front = any(set(c) == {name, co} for c in RO.closes_onto_pairs(spec))
        sub = col.row()
        sub.active = False
        sub.label(text="Front closure: closed last" if front else "Its buttons' holes only",
                  icon='BLANK1')


CLASSES = (AC9_OT_SimButtonAdd, AC9_OT_SimButtonMove, AC9_OT_SimButtonLineUp, AC9_OT_SimButtonFromStitches,
           AC9_OT_SimButtonFasten, AC9_OT_SimButtonRemove, AC9_OT_SimButtonSelect, AC9_OT_SimButtonSetStyle,
           AC9_OT_SimButtonStyleFromObject, AC9_OT_SimButtonNoHole, AC9_OT_SimButtonAutoHole,
           AC9_OT_SimClosesOntoPick, AC9_OT_SimClosesOntoClear)


def register():
    bpy.types.Scene.ac9_button_style = bpy.props.EnumProperty(name="Button Style", items=_style_items,
                                                              description="The style new buttons get")
    bpy.types.Scene.ac9_button_active = bpy.props.StringProperty(name="Active Button", default="")
    bpy.types.Scene.ac9_button_sel = bpy.props.StringProperty(name="Picked Buttons", default="")   # besides the active
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    for k in ('ac9_button_style', 'ac9_button_active', 'ac9_button_sel'):
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
