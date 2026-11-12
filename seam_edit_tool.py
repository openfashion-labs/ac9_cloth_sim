# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit Seams: a toolbar tool for the seams on the flat pattern (core/seam_edit.py does the edits).

With the tool active: a click on a seam selects it (and its row in the Seams list); clicking again on the same spot
goes on to the next seam there. A drag on an end of the selected seam (the dots) slides it along the outline or the
internal line; an M:N seam's split point moves both stretches; it snaps to corners, notches and other seams' ends
(Ctrl: no snapping). M moves the selected seam's side under the cursor (or the panel's Move A / Move B) to another
place, on this piece or another: click where it starts, F runs it the other way. F2 names the piece under the cursor.

Only seams change here; the pattern stays. On a dressed garment the edit is carried as a pattern edit is
(draft_tool.commit): an end slid moves where the cloth is sewn, a side moved to another piece is sewn anew.
"""
import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import intersect_line_plane
from .i18n import rpt, tx
from . import units as U

from . import overlay
from . import click_log as CL
from . import pattern_view as PV
from . import store
from . import toolbar as TB
from . import view_visibility as VV
from .core import seam_edit as SE
from .core import spec as S

TOOL_ID = "ac9_cloth_sim.edit_seams_tool"
PICK_PX = 10          # a seam within this of the cursor
END_PX = 12           # an end of the selected seam within this
SNAP_PX = 10          # an end snaps to a corner / notch / other end within this

_state = {'hover': None, 'hover_area': None, 'drag': None, 'handles': []}


_trk = {'key': None, 'trk': None}


def tracks(spec, outlines):
    """SE.tracks for the spec in the store, kept until the spec changes (the hover runs on every mouse move: ~4 ms
    for a BOM of 133 pieces all on)."""
    if _trk['key'] != store._cache['key'] or _trk['trk'] is None:
        _trk.update(key=store._cache['key'], trk=SE.tracks(spec, outlines))
    return _trk['trk']


def tool_active(context):
    try:
        return context.workspace.tools.from_space_view3d_mode(context.mode).idname == TOOL_ID
    except Exception:
        return False


# ---------------------------------------------------------------- mouse in the 3D view's main region

def view_region(context):
    """(region, rv3d) of the area's main 3D region (the tool's keys may come from the sidebar region)."""
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return None, None
    for r in area.regions:
        if r.type == 'WINDOW':
            return r, area.spaces.active.region_3d
    return None, None


def mouse_mm(context, M, event, dx=0):
    if not VV.allows_pattern(context):
        return None
    region, rv3d = view_region(context)
    if region is None:
        return None
    mx, my = event.mouse_x - region.x + dx, event.mouse_y - region.y
    o = region_2d_to_origin_3d(region, rv3d, (mx, my))
    v = region_2d_to_vector_3d(region, rv3d, (mx, my))
    Minv = M.inverted()
    o_l, v_l = Minv @ o, (Minv.to_3x3() @ v)
    hit = intersect_line_plane(o_l, o_l + v_l * 1000.0, Vector((0, 0, 0)), Vector((0, 0, 1)))
    return None if hit is None else np.array([hit.x / PV.MM, hit.y / PV.MM])


def _snap_on(context, event):
    """The header's magnet, the other way while Ctrl is held (DECISIONS cloth-sim-drag-distance-snap-2026-10-23)."""
    from .shape_tool import snap_settings
    return snap_settings(context)[0] != bool(event.ctrl)


def px_mm(context, M, event):
    a, b = mouse_mm(context, M, event), mouse_mm(context, M, event, 10)
    if a is None or b is None:
        return 1.0
    return max(float(np.linalg.norm(b - a)) / 10.0, 1e-6)


def to_world(M, P2):
    return [M @ Vector((float(x) * PV.MM, float(y) * PV.MM, 0.0)) for x, y in np.atleast_2d(P2)]


def select_seam(context, sid, extend=False):
    """Make seam sid the active one. extend (Shift+click): the active one so far stays picked; sid already picked is
    taken out (the active one then passes to another picked seam)."""
    props = context.scene.ac9_cloth_sim
    ids = [it.seam_id for it in props.seams]
    if sid not in ids:
        return False
    cur = ids[props.seam_index] if 0 <= props.seam_index < len(ids) else None
    if not extend:
        store.seam_more.clear()
        target = sid
    elif sid in store.seam_more:
        store.seam_more.discard(sid)
        target = cur
    elif sid == cur:
        if not store.seam_more:
            return True
        target = sorted(store.seam_more)[0]
        store.seam_more.discard(target)
    else:
        if cur is not None:
            store.seam_more.add(cur)
        target = sid
    store._seam_keep[0] = True
    try:
        props.seam_index = ids.index(target)
    finally:
        store._seam_keep[0] = False
    overlay.invalidate()
    return True


def unselect_seam(context):
    """No seam selected (user 2026-10-21: a click on anything but a seam; its notches and the seam buttons go)."""
    props = context.scene.ac9_cloth_sim
    store.seam_more.clear()
    if props.seam_index != -1:
        props.seam_index = -1
        overlay.invalidate()
        if context.area is not None:
            context.area.tag_redraw()


def piece_label(spec, name):
    pc = S.piece_index(spec).get(name)
    return (pc or {}).get('label') or name


# ---------------------------------------------------------------- hover

def seam_hit(context, event, kind=None):
    """What of the seams is under the cursor: ('end', sid, k, which) of the selected seam, ('seam', sid, k), or None.
    kind: TB.under's first value when the caller has it already."""
    if not VV.allows_pattern(context):
        return None
    spec, outlines = store.get(context)
    if spec is None or PV.layout_empty(context) is None:
        return None
    if kind is None:
        kind = TB.under(context, event.mouse_region_x, event.mouse_region_y)[0]
    M = PV.layout_matrix(context)
    q = mouse_mm(context, M, event) if kind in ('PATTERN', 'PLANE') else None
    if q is None:
        return None
    mmpx = px_mm(context, M, event)
    trk = tracks(spec, outlines)
    visible = VV.pattern_names(context, spec)
    act = store.active_seam(context, spec)
    if act is not None:
        near = [(float(np.linalg.norm(p - q)), k, w) for k, w, _, _, p in SE.ends(spec, act, trk)
                if act[k]['piece'] in visible]
        near = [x for x in near if x[0] <= END_PX * mmpx]
        if near:
            _, k, w = min(near)
            return ('end', act['id'], k, w)
    c = SE.seams_at(spec, trk, q, PICK_PX * mmpx)
    seams = {sm['id']: sm for sm in spec['seams']}
    c = [hit for hit in c if seams[hit[1]][hit[2]]['piece'] in visible]
    return ('seam', c[0][1], c[0][2]) if c else None


class AC9_OT_SimSeamHover(bpy.types.Operator):
    """(internal) Edit Seams: the seam or end under the cursor"""
    bl_idname = "ac9_cloth_sim.seam_hover"
    bl_label = "Seam Hover"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        _state['hover_area'] = context.area.as_pointer() if context.area else None
        spec, _ = store.get(context)
        if spec is None or PV.layout_empty(context) is None:
            return {'PASS_THROUGH'}
        kind = TB.under(context, event.mouse_region_x, event.mouse_region_y)[0]
        TB.hint(context, event, 'EDIT_SEAMS', kind)
        h = seam_hit(context, event, kind)
        if h != _state['hover']:
            _state['hover'] = h
            context.area.tag_redraw()
        return {'PASS_THROUGH'}


# ---------------------------------------------------------------- click: select, or drag an end

class AC9_OT_SimSeamClick(bpy.types.Operator):
    """Select the seam under the cursor (again: the next one there), or drag an end of the selected seam along its outline (Ctrl: no snapping)"""
    bl_idname = "ac9_cloth_sim.seam_click"
    bl_label = "Select / Move Seam End"
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and store.get(context)[0] is not None and PV.layout_empty(context) is not None)

    @CL.logged
    def invoke(self, context, event):
        spec, outlines = store.get(context)
        self.M = PV.layout_matrix(context)
        self._mx, self._my = event.mouse_region_x, event.mouse_region_y
        kind, ob_, _i = TB.under(context, event.mouse_region_x, event.mouse_region_y)
        if kind in ('GARMENT', 'PLACED'):        # the point clicked shown on the pattern too (garment_mark)
            from . import garment_mark as GM
            GM.mark(context, ob_, event.mouse_region_x, event.mouse_region_y)
        self._shift = event.shift
        if kind not in ('PATTERN', 'PLANE'):
            if self._pick3d(context, spec):
                return {'FINISHED'}
            if event.shift:                     # Shift just off a 3D seam: the seams picked so far stay
                return {'CANCELLED'}
            unselect_seam(context)
            return {'CANCELLED'} if kind != 'NONE' else {'PASS_THROUGH'}
        if kind == 'PLANE' and self._pick3d(context, spec):     # a 3D seam line drawn in front of the empty layout plane
            return {'FINISHED'}
        q = mouse_mm(context, self.M, event)
        if q is None:
            unselect_seam(context)
            return {'PASS_THROUGH'}
        mmpx = px_mm(context, self.M, event)
        self.trk = SE.tracks(spec, outlines)
        visible = VV.pattern_names(context, spec)
        act = store.active_seam(context, spec)
        if act is not None:
            near = [(float(np.linalg.norm(p - q)), k, w, key, s) for k, w, key, s, p in SE.ends(spec, act, self.trk)
                    if act[k]['piece'] in visible]
            near = [x for x in near if x[0] <= END_PX * mmpx]
            if near:
                _, k, w, key, s = min(near, key=lambda x: x[0])
                return self._start_drag(context, event, spec, act['id'], k, w, key, s)
        c = SE.seams_at(spec, self.trk, q, PICK_PX * mmpx)
        seams = {sm['id']: sm for sm in spec['seams']}
        c = [hit for hit in c if seams[hit[1]][hit[2]]['piece'] in visible]
        pick = SE.next_pick(c, act['id'] if act else None)
        if pick is None:
            if not event.shift:
                unselect_seam(context)
            elif act is not None:               # Shift just off a seam while picking seams: no piece picked instead
                return {'CANCELLED'}
            return {'PASS_THROUGH'}             # nothing here: the click selects objects as usual
        select_seam(context, pick[0], extend=event.shift)
        overlay.invalidate()
        context.area.tag_redraw()
        n = len({x[1] for x in c})
        self.report({'INFO'}, rpt("Seam {0}", pick[0]) + (rpt(" ({0} here: click again for the next)", n) if n > 1 else ""))
        return {'FINISHED'}

    def _pick3d(self, context, spec):
        """Select the 3D seam line under the click (the placed pieces' or the garment's, overlay.pick3d): select only,
        its ends are dragged on the pattern."""
        if not VV.allows_world(context, spec) or not overlay.shows(context.scene.ac9_cloth_sim, 'show_overlay'):
            return False
        act = store.active_seam(context, spec)
        sid = overlay.pick3d(context, spec, self._mx, self._my, PICK_PX, act['id'] if act else None)
        if sid is None or not select_seam(context, sid, extend=getattr(self, '_shift', False)):
            return False
        overlay.invalidate()
        context.area.tag_redraw()
        self.report({'INFO'}, rpt("Seam {0} (picked in 3D)", sid))
        return True

    # ------------------------------------------------ drag
    def _start_drag(self, context, event, spec, sid, k, w, key, s):
        self.spec = S.loads(S.dumps(spec))
        self.sid, self.k, self.w, self.key = sid, k, w, key
        self.s0 = self.s_cur = s
        self.ds = 0.0
        self.targets = SE.snap_targets(self.spec, key, self.trk, skip={(sid, k, w)} | set(
            SE.shared_ends(self.spec, sid, k, w, self.trk)))
        _state['drag'] = {'spec': self.spec, 'preview': None, 'changed': [], 'bad': '', 'point': None,
                          'piece': key.split(SE.SEP)[0]}
        _state['drag']['area'] = context.area.as_pointer() if context.area else None
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _update(self, context, event):
        tr = self.trk[self.key]
        q = mouse_mm(context, self.M, event)
        dr = _state['drag']
        if q is None:
            return
        s_raw, _ = SE.project(tr, q)
        if _snap_on(context, event):
            s_raw, _ = SE.snap_s(tr, s_raw, SNAP_PX * px_mm(context, self.M, event), self.targets)
        if SE.is_closed(tr):                       # unwrapped: continuous with the last position
            s_raw = self.s_cur + (((s_raw - self.s_cur) + tr.L / 2) % tr.L - tr.L / 2)
        self.s_cur = s_raw
        self.ds = s_raw - self.s0
        sp = dict(self.spec)
        sp['seams'] = [dict(x) for x in self.spec['seams']]
        try:
            ch = SE.move_end(sp, self.sid, self.k, self.w, self.ds, self.trk)
            dr.update(preview=sp, changed=ch, bad='')
        except ValueError as exc:
            dr.update(preview=None, changed=[], bad=str(exc))
        dr['point'] = tr.at(s_raw)
        from .shape_tool import fmt
        txt = tx("End {0}", fmt(context, self.ds, True))
        if dr['preview'] is not None:
            for i in dr['changed']:
                sm = next(x for x in sp['seams'] if x['id'] == i)
                la, lb = S.side_length(sp, sm['a']), S.side_length(sp, sm['b'])
                txt += f"   {i}: {U.num(context, la)} / {U.num(context, lb)} ({U.num(context, la - lb, signed=True)})"
        else:
            txt += "   NOT ALLOWED: " + dr['bad']
        txt += "   Ctrl: no snapping   Esc / right click: cancel"
        context.area.header_text_set(txt)

    def _end(self, context):
        _state['drag'] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._update(context, event)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            dr = _state['drag']
            if dr['preview'] is None or abs(self.ds) < 1e-3:
                if dr['bad']:
                    self.report({'WARNING'}, rpt("Not moved: ") + dr['bad'])
                self._end(context)
                return {'CANCELLED'}
            sp = dr['preview']
            for i in dr['changed']:
                sm = next(x for x in sp['seams'] if x['id'] == i)
                sm['source'] = 'user'
            msg = commit_seams(context, sp, self.spec, dr['changed'])
            self.report({'INFO'}, rpt("Moved the end {0}", U.fmt(context, self.ds, signed=True)) + (rpt("; {0}", msg) if msg else ""))
            self._end(context)
            return {'FINISHED'}
        elif event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            self._end(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


def commit_seams(context, sp, old, changed):
    """Write the seams; on a dressed garment carry the edit as a pattern edit (draft_tool.commit). Returns its line."""
    from . import draft_tool as T
    names = sorted({x[k]['piece'] for x in sp['seams'] if x['id'] in changed for k in ('a', 'b')}
                   | {x[k]['piece'] for x in old['seams'] if x['id'] in changed for k in ('a', 'b')})
    sid = None
    props = context.scene.ac9_cloth_sim
    if 0 <= props.seam_index < len(props.seams):
        sid = props.seams[props.seam_index].seam_id
    msg = T.commit(context, S.loads(S.dumps(sp)), names, old_spec=old)
    if sid:
        select_seam(context, sid)
    overlay.invalidate()
    return msg


# ---------------------------------------------------------------- move a side

class AC9_OT_SimSeamMoveSide(bpy.types.Operator):
    """Move one side of the selected seam to another place, on this piece or another: click where it starts (it keeps its length; F: the other way round; Esc cancels)"""
    bl_idname = "ac9_cloth_sim.seam_move_side"
    bl_label = "Move Side"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    side: bpy.props.EnumProperty(items=[('AUTO', "Under the Cursor", ""), ('a', "A", ""), ('b', "B", "")],
                                 default='AUTO')

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return (AC9_OT_SimSeamClick.poll(context) and spec is not None
                and store.active_seam(context, spec) is not None)

    def invoke(self, context, event):
        spec, outlines = store.get(context)
        self.M = PV.layout_matrix(context)
        self.spec = S.loads(S.dumps(spec))
        self.trk = SE.tracks(self.spec, outlines)
        act = store.active_seam(context, self.spec)
        k = self.side
        if k == 'AUTO':
            q = mouse_mm(context, self.M, event)
            visible = VV.pattern_names(context, self.spec)
            c = [x for x in SE.seams_at(self.spec, self.trk, q, 1e9)
                 if x[1] == act['id'] and act[x[2]]['piece'] in visible] if q is not None else []
            if not c:
                return {'CANCELLED'}
            k = c[0][2]
        self.k, self.sid = k, act['id']
        tr0 = self.trk.get(SE.track_key(act[k]))
        if tr0 is None:
            self.report({'WARNING'}, "That side's piece is off")
            return {'CANCELLED'}
        _, _, d = SE.span(act[k], tr0)
        self.length, self.dir = abs(d), (1 if d >= 0 else -1)
        self.forced = False
        self.hover = None
        _state['drag'] = {'spec': self.spec, 'preview': None, 'changed': [act['id']], 'bad': '', 'point': None,
                          'moving': (act['id'], k)}
        _state['drag']['area'] = context.area.as_pointer() if context.area else None
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _update(self, context, event):
        dr = _state['drag']
        q = mouse_mm(context, self.M, event)
        dr.update(preview=None, bad='', point=None)
        if q is None:
            return
        mmpx = px_mm(context, self.M, event)
        best = None
        visible = VV.pattern_names(context, self.spec)
        for key, tr in self.trk.items():
            if key.split(SE.SEP)[0] not in visible:
                continue
            s, dist = SE.project(tr, q)
            if dist <= PICK_PX * mmpx and (best is None or dist < best[2]):
                best = (key, s, dist)
        if best is None:
            context.area.header_text_set(tx("Move side {0} of {1} ({2}): point at an outline or an internal line   Esc / right click: cancel", self.k.upper(), self.sid, U.fmt(context, self.length)))
            return
        key, s, _ = best
        tr = self.trk[key]
        if _snap_on(context, event):
            s, _ = SE.snap_s(tr, s, SNAP_PX * mmpx, SE.snap_targets(self.spec, key, self.trk,
                                                                     skip={(self.sid, self.k, 'from'),
                                                                           (self.sid, self.k, 'to')}))
        sp = dict(self.spec)
        sp['seams'] = [dict(x) for x in self.spec['seams']]
        try:
            SE.move_side(sp, self.sid, self.k, key, s, self.dir, self.trk, length=self.length)
            dr.update(preview=sp)
        except ValueError as exc:
            dr.update(bad=str(exc))
        dr['point'] = tr.at(s)
        dr['piece'] = key.split(SE.SEP)[0]
        self.hover = (key, s)
        txt = tx("Move side {0} of {1} to {2}", self.k.upper(), self.sid, piece_label(self.spec, key.split(SE.SEP)[0]))
        if dr['preview'] is not None:
            sm = next(x for x in sp['seams'] if x['id'] == self.sid)
            la, lb = S.side_length(sp, sm['a']), S.side_length(sp, sm['b'])
            txt += f"   {U.num(context, la)} / {U.num(context, lb)} ({U.num(context, la - lb, signed=True)})"
        else:
            txt += "   NOT ALLOWED: " + dr['bad']
        txt += tx("   F: other way ({0})   click: here   Ctrl: no snapping   Esc / right click: cancel", 'ccw' if self.dir > 0 else 'cw')
        context.area.header_text_set(txt)

    def _end(self, context):
        _state['drag'] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._update(context, event)
        elif event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        elif event.type in {'ESC', 'RIGHTMOUSE'}:
            self._end(context)
            return {'CANCELLED'}
        elif event.type == 'F':
            self.dir = -self.dir
            self._update(context, event)
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
            dr = _state['drag']
            if dr['preview'] is None:
                if dr['bad']:
                    self.report({'WARNING'}, rpt("Not moved: ") + dr['bad'])
                return {'RUNNING_MODAL'}
            sp = dr['preview']
            sm = next(x for x in sp['seams'] if x['id'] == self.sid)
            sm['source'], sm['check'] = 'user', ''
            msg = commit_seams(context, sp, self.spec, [self.sid])
            self.report({'INFO'}, rpt("Moved side {0} of {1} to {2}", self.k.upper(), self.sid, piece_label(sp, sm[self.k]['piece'])) + (rpt("; {0}", msg) if msg else ""))
            self._end(context)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


# ---------------------------------------------------------------- naming a piece

class AC9_OT_SimPieceLabel(bpy.types.Operator):
    """Name a piece (shown on the pattern and in the seam list; the piece's id stays, so seams, results and roles keep it)"""
    bl_idname = "ac9_cloth_sim.piece_label"
    bl_label = "Name Piece"
    bl_options = {'REGISTER', 'UNDO'}

    piece: bpy.props.StringProperty(options={'HIDDEN'})
    owner_scene: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    owner_text: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    label: bpy.props.StringProperty(name="Name", description="Empty: show the piece's id")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, outlines = store.get(context)
        name = ''
        if PV.layout_empty(context) is not None and context.area is not None and context.area.type == 'VIEW_3D':
            q = mouse_mm(context, PV.layout_matrix(context), event)
            if q is not None:
                from .draft_tool import _inside
                for pc in reversed(spec['pieces']):
                    if pc.get('enabled', True) and _inside(outlines[pc['name']].B, q):
                        name = pc['name']
                        break
        if not name and context.active_object is not None:
            name = context.active_object.get('ac9_piece') or ''
        if not name or name not in S.piece_index(spec):
            self.report({'WARNING'}, "Point at a piece (or select one)")
            return {'CANCELLED'}
        self.piece = name
        self.owner_scene = str(context.scene.session_uid)
        self.owner_text = str(store.text(context).session_uid)
        self._label_owner = store.edit_owner(context)
        self.label = S.piece_index(spec)[name].get('label', '')
        return context.window_manager.invoke_props_dialog(self, title=tx("Name piece {0}", name))

    def execute(self, context):
        current = store.text(context)
        if current is None:
            return {'CANCELLED'}
        if getattr(self, 'owner_text', ''):
            if (self.owner_scene != str(context.scene.session_uid)
                    or self.owner_text != str(current.session_uid)):
                self.report({'WARNING'}, rpt("The pattern changed during this edit; start the edit again"))
                return {'CANCELLED'}
        else:
            # Direct EXEC binds once too; redo retains the hidden owner IDs.
            self.owner_scene = str(context.scene.session_uid)
            self.owner_text = str(current.session_uid)
        owner = getattr(self, '_label_owner', None)
        if owner is not None and not owner.matches(context, content=True):
            self.report({'WARNING'}, rpt("The pattern changed during this edit; start the edit again"))
            return {'CANCELLED'}
        spec, _ = store.get(context)
        changed = set_label(context, spec, self.piece, self.label)
        self._label_owner = None
        return {'FINISHED'} if changed else {'CANCELLED'}


def set_label(context, spec, piece, label):
    """Name `piece` (empty or its id: no name, the id shows). False if there is no such piece."""
    sp = S.loads(S.dumps(spec))
    pc = S.piece_index(sp).get(piece)
    if pc is None:
        return False
    lab = label.strip()
    if lab == pc.get('label', '') or (not lab and 'label' not in pc):
        return True
    if lab and lab != pc['name']:
        pc['label'] = lab
    else:
        pc.pop('label', None)
    store.put(context, sp)
    overlay.invalidate()
    return True


class AC9_OT_SimSeamNotch(bpy.types.Operator):
    """Put a notch on the seam under the cursor and its partner on the other side, at the same place of the seam (Ctrl+N: this side only)"""
    bl_idname = "ac9_cloth_sim.seam_notch"
    bl_label = "Notch Pair"
    bl_options = {'REGISTER', 'UNDO'}

    one_side: bpy.props.BoolProperty(name="This Side Only", default=False,
                                     description="Only the side under the cursor gets a notch")
    sid: bpy.props.StringProperty(options={'HIDDEN'})
    k: bpy.props.StringProperty(options={'HIDDEN'})
    u: bpy.props.FloatProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return AC9_OT_SimSeamClick.poll(context)

    def invoke(self, context, event):
        spec, outlines = store.get(context)
        M = PV.layout_matrix(context)
        q = mouse_mm(context, M, event)
        if q is None:
            return {'PASS_THROUGH'}
        trk = tracks(spec, outlines)
        c = SE.seams_at(spec, trk, q, PICK_PX * px_mm(context, M, event))
        act = store.active_seam(context, spec)
        c = sorted(c, key=lambda x: (act is None or x[1] != act['id'], x[0]))      # the selected seam first
        c = [x for x in c if 'line' not in next(s for s in spec['seams'] if s['id'] == x[1])[x[2]]]
        if not c:
            self.report({'WARNING'}, "Point at a seam on an outline")
            return {'CANCELLED'}
        _, sid, k = c[0]
        sm = next(s for s in spec['seams'] if s['id'] == sid)
        tr = trk[SE.track_key(sm[k])]
        s, _ = SE.project(tr, q)
        s0, _, d = SE.span(sm[k], tr)
        u = SE._on_span(s, s0, d, tr.L, SE.is_closed(tr))
        self.sid, self.k, self.u = sid, k, float(u or 0.0)
        return self.execute(context)

    def execute(self, context):
        from . import draft_tool as T
        from .core import draft as D
        spec, _ = store.get(context)
        sp = S.loads(S.dumps(spec))
        try:
            ch, n = D.add_notch_pair(sp, self.sid, self.k, self.u, one_side=self.one_side)
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        T.commit(context, sp, ch)                      # notches move no cloth: no carry
        select_seam(context, self.sid)
        overlay.invalidate()
        self.report({'INFO'}, rpt("Seam {0}: {1} notch(es)", self.sid, n))
        return {'FINISHED'}


# ---------------------------------------------------------------- the tool

class AC9_WT_EditSeams(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = TOOL_ID
    bl_label = "Edit Seams"
    bl_description = ("Click a seam on the flat pattern or a 3D seam line to select it (again: the next one there); "
                      "drag an end of the "
                      "selected seam along the outline (an M:N split point moves both stretches; Ctrl: no snapping). "
                      "M: move the side under the cursor to another place, N: a notch here and its partner on the other "
                      "side (Ctrl+N: this side only), F2: name the piece under the cursor, Shift+click: add a seam, double-click: "
                      "select the piece")
    bl_icon = "ops.curve.draw"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.seam_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, None),
        ("ac9_cloth_sim.seam_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        # Shift+click adds a seam (select_seam extend); without its own item it fell to Blender's click select and
        # picked the piece under the seam (user 2026-10-24)
        ("ac9_cloth_sim.seam_click", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, None),
        ("ac9_cloth_sim.pick_piece_under", {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK'}, None),
        ("ac9_cloth_sim.pick_piece_under", {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK', "shift": True}, None),
        ("ac9_cloth_sim.seam_move_side", {"type": 'M', "value": 'PRESS'}, {"properties": [("side", 'AUTO')]}),
        ("ac9_cloth_sim.piece_label", {"type": 'F2', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.seam_notch", {"type": 'N', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.seam_notch", {"type": 'N', "value": 'PRESS', "ctrl": True}, {"properties": [("one_side", True)]}),
        ("ac9_cloth_sim.context_menu", {"type": 'RIGHTMOUSE', "value": 'PRESS'}, {"properties": [("tool", 'EDIT_SEAMS')]}),
    ) + TB.DELETE_KEYS

    def draw_settings(context, layout, tool):
        TB.works_on(layout, 'EDIT_SEAMS')
        layout.label(text="click a seam (again: the next one there; Shift adds)  drag its end  Ctrl no snapping  "
                              "M move a side  N notch pair (Ctrl+N one side)  F2 name  double-click piece  X/Del delete  "
                              "right-click: menu")


class AC9_OT_SimEditSeamsTool(bpy.types.Operator):
    """Make Edit Seams the active tool: click seams on the flat pattern to select them, drag their ends"""
    bl_idname = "ac9_cloth_sim.edit_seams"
    bl_label = "Edit Seams"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT'

    def execute(self, context):
        return bpy.ops.ac9_cloth_sim.use_tool(tool=TOOL_ID)


# ---------------------------------------------------------------- drawing

def _segs(M, P):
    W = to_world(M, P)
    return [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]


_ilines = {'key': None, 'segs': []}


def _internal_segs(spec, M, visible=None):
    """Every enabled piece's internal lines (world segments), built once per pattern version and layout."""
    key = (store._cache.get('key'), np.array(M).tobytes(), visible)
    if _ilines['key'] != key:
        segs = []
        for pc in spec['pieces']:
            if not pc.get('enabled', True) or (visible is not None and pc['name'] not in visible):
                continue
            for it in pc.get('internals', []):
                if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                    continue
                segs += _segs(M, it['pts'])
        _ilines.update(key=key, segs=segs)
    return _ilines['segs']


def _draw_view():
    context = bpy.context
    try:
        if not VV.allows_pattern(context):
            return
        dr = _state['drag']
        area = context.area.as_pointer() if context.area else None
        if dr is not None and dr.get('area', area) != area:
            return
        if dr is None and not tool_active(context):
            return
        spec, outlines = store.get(context)
        if spec is None or PV.layout_empty(context) is None:
            return
        visible = VV.pattern_names(context, spec)
        M = PV.layout_matrix(context)
        region = context.region
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        # the internal lines of every piece, thin: a seam end is often placed against one (user 2026-10-22)
        isegs = _internal_segs(spec, M, visible)
        if isegs:
            sh.uniform_float('lineWidth', 1.5)
            sh.uniform_float('color', (0.45, 0.9, 0.8, 0.8))
            batch_for_shader(sh, 'LINES', {'pos': isegs}).draw(sh)
        dots = []
        if dr is not None:
            sp = dr['preview']
            if sp is not None:
                ol = {p['name']: S.Outline(p) for p in sp['pieces']}
                for i in dr['changed']:
                    sm = next(x for x in sp['seams'] if x['id'] == i)
                    for k in ('a', 'b'):
                        if sm[k]['piece'] not in visible:
                            continue
                        segs = _segs(M, S.side_polyline(sp, sm[k], ol, step=2.0))
                        sh.uniform_float('lineWidth', 7.0)
                        sh.uniform_float('color', (1.0, 0.85, 0.2, 1.0))
                        batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
            if dr.get('point') is not None and dr.get('piece') in visible:
                dots.append((dr['point'], (1.0, 0.25, 0.2, 1.0) if dr['bad'] else (1.0, 1.0, 1.0, 1.0), 13.0))
        else:
            h = _state['hover'] if _state.get('hover_area') in (None, area) else None
            if h is not None and h[0] == 'seam':
                sm = next((x for x in spec['seams'] if x['id'] == h[1]), None)
                if sm is not None:
                    sh.uniform_float('lineWidth', 7.0)
                    sh.uniform_float('color', SE.color(sm)[:3] + (0.6,))
                    for k in ('a', 'b'):
                        if sm[k]['piece'] not in visible:
                            continue
                        try:
                            batch_for_shader(sh, 'LINES', {'pos': _segs(M, S.side_polyline(spec, sm[k], outlines,
                                                                                            step=2.0))}).draw(sh)
                        except Exception:
                            pass
            act = store.active_seam(context, spec)
            if act is not None:
                trk = tracks(spec, outlines)
                for k, w, _, _, p in SE.ends(spec, act, trk):
                    if act[k]['piece'] not in visible:
                        continue
                    hot = h is not None and h[0] == 'end' and h[2] == k and h[3] == w
                    dots.append((p, (1.0, 0.3, 0.3, 1.0) if hot else (1.0, 1.0, 1.0, 1.0), 13.0 if hot else 9.0))
        sp_ = gpu.shader.from_builtin('UNIFORM_COLOR')
        sp_.bind()
        for p, c, size in dots:
            gpu.state.point_size_set(size)
            sp_.uniform_float('color', c)
            batch_for_shader(sp_, 'POINTS', {'pos': [tuple(to_world(M, [p])[0])]}).draw(sp_)
        gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')
    except Exception:
        pass


class AC9_OT_SimSeamListPick(bpy.types.Operator):
    """Pick this seam; Shift+click adds it to the picked ones or takes it out"""
    bl_idname = "ac9_cloth_sim.seam_list_pick"
    bl_label = "Pick Seam"
    bl_options = {'INTERNAL'}

    seam: bpy.props.StringProperty(options={'HIDDEN'})
    extend: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        # every list picks the same way (user 2026-10-23): a click picks one, Shift+click adds / takes out
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        return {'FINISHED'} if select_seam(context, self.seam, extend=self.extend) else {'CANCELLED'}


CLASSES = (AC9_OT_SimSeamHover, AC9_OT_SimSeamClick, AC9_OT_SimSeamMoveSide, AC9_OT_SimPieceLabel, AC9_OT_SimSeamNotch,
           AC9_OT_SimEditSeamsTool, AC9_OT_SimSeamListPick)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    _state['handles'].append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))


def unregister():
    for h in _state['handles']:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _state['handles'].clear()
    _state.update(hover=None, drag=None)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
