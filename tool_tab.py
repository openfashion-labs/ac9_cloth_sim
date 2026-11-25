# SPDX-License-Identifier: GPL-3.0-or-later
"""The Properties editor's Tool tab: the settings of the last thing touched (user
2026-10-13): a seam picked in the list or on the pattern, a piece selected in the viewport, else the garment.

The Tool tab draws the 3D view sidebar's "Tool" category (Blender's WorkSpaceButtonsPanel), so these panels are
VIEW_3D / UI / 'Tool' and show only where the area is the Properties editor (Fable review 2026-10-13 §1). No
bl_context: they stay in every mode (Fix by Hand goes to Sculpt or Edit Mode)."""
import bpy
from bpy.app.handlers import persistent
from .i18n import CTX, fabric_name, tx, wrap

from . import closure as CL_
from . import pattern_view as PV
from . import store
from . import selection as SEL
from . import ui as U

# Selection says what an operation acts on; focus says which selected thing's details to show.
# Only publish() writes focus. Internal projections acknowledge selection, without becoming a new input.
_focus = {'kind': None, 'owner': None, 'item': None, 'serial': 0, 'origin': None}
_observed = {'owner': None, 'selection': None}
_views = {}
_batch = []
_bus_key = __name__ + '.tools_msgbus_owner'
_bus_owner = bpy.app.driver_namespace.setdefault(_bus_key, object())


def _tools_changed():
    # WorkSpace.tools notifies even when Properties is closed. Do not wait for draw to observe a tool change.
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        with bpy.context.temp_override(window=win):
            _view(bpy.context)
    _tag_properties()
    from . import work_screen
    work_screen.apply_gizmo(bpy.context)


def _subscribe_tools():
    bpy.app.driver_namespace[_bus_key] = _bus_owner
    bpy.msgbus.clear_by_owner(_bus_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.WorkSpace, 'tools'), owner=_bus_owner,
                            args=(), notify=_tools_changed)


def _tool(context):
    from . import toolbar
    return toolbar.active_tool(context)


def _view(context):
    """Selection is shared; the meaning of an input belongs to its workspace and mode."""
    ws = getattr(context, 'workspace', None)
    key = (_owner(context), ws.session_uid if ws is not None else 0, context.mode)
    tool = _tool(context)
    state = _views.setdefault(key, {'tool': tool, 'focus': {}, 'tools': {}})
    if state['tool'] != tool:
        state['tool'] = tool
        state['focus'] = state['tools'].get(tool, {})
    return state


def _record(context):
    return _view(context)['focus']


class interaction:
    """Collect the typed result of one input; helpers may not publish intermediate details."""
    def __init__(self, context, kind):
        self.context, self.kind = context, kind

    def __enter__(self):
        _batch.append(self)
        return self

    def __exit__(self, typ, value, trace):
        _batch.pop()
        if typ is None:
            publish(self.context, self.kind() if callable(self.kind) else self.kind)
        return False


def clear(context):
    """An explicit empty click ends all browsing intentions in this context."""
    state = _view(context)
    state['tools'].clear()
    state['focus'] = {}
    publish(context, 'GARMENT', origin='clear')


def _owner(context):
    text = store.text(context)
    return (context.scene.session_uid, text.session_uid if text else 0, SEL.view_layer_key(context))


def _selection(context):
    active = context.view_layer.objects.active
    other = tuple(sorted(ob.session_uid for ob in SEL.objects(context) if SEL._kind(ob) is None))
    active_other = active.session_uid if active is not None and active.select_get() and SEL._kind(active) is None else 0
    return (SEL.lead(context), tuple(sorted(SEL.pieces(context))), active_other, other,
            tuple(sorted(SEL._snapshot(context).items())))


def acknowledge_selection(context):
    """An internal projection/restore is observed, but is not a click on a new detail target."""
    owner, selection = _owner(context), _selection(context)
    previous = _observed['selection'] if _observed['owner'] == owner else None
    _observed.update(owner=owner, selection=selection)
    if previous is None or previous[0] != selection[0]:
        from . import properties as P
        P.sync_looks()


def selection_changed(context):
    """Called by selection.watch BEFORE it projects an external selection; never from a second watcher."""
    return _observed['owner'] == _owner(context) and _observed['selection'] != _selection(context)


def _target(context, spec, kind):
    """Stable identity of the current editing target, rather than an index or a generated object name."""
    if kind == 'PIECE':
        return _selected_piece(context, spec) if spec is not None else None
    if kind == 'GARMENT':
        return 'GARMENT'
    if kind == 'SEAM':
        return (store.active_seam(context, spec) or {}).get('id')
    if kind == 'BUTTON' and spec is not None:
        from . import button_ui as BU
        bid = context.scene.ac9_button_active
        return bid if BU._find(spec, bid) is not None else None
    if kind == 'MEASURE':
        from . import measure_edit as MEE, measure_tool as MT
        picked = MEE.selected(context)
        if picked is not None:
            body = MT._body(context) if picked[0] in ('item', 'tape') else None
            return (MEE.sel(context), body.session_uid if body else 0)
    if kind == 'INTERNAL' and spec is not None:
        from . import draft_tool as DT
        picked = DT.selected_internal(context, spec)
        return tuple(picked[:2]) if picked else None
    if kind == 'EDGES' and spec is not None:
        from . import draft_tool as DT
        picked = tuple(x for x in DT.picked_lines(spec) if x[0] in ('edge', 'corner', 'ipoint'))
        return picked or None
    return None


def publish(context, kind, item=None, origin='click'):
    """Commit one explicit interaction, including re-clicks and drag starts; callers finish selection first."""
    if _batch:
        return
    SEL.view_layer_key(context, create=True)
    spec = store.get(context)[0]
    # Whole Pieces may transform internal lines, but its attribute editor edits pieces.
    if _tool(context) == 'ac9_cloth_sim.transform_pattern_tool' and kind in ('INTERNAL', 'EDGES'):
        kind = 'PIECE'
    if kind not in ('FABRIC', 'BOND'):
        item = _target(context, spec, kind)
    if item is None:
        kind = 'PIECE' if _target(context, spec, 'PIECE') is not None else 'GARMENT'
        item = _target(context, spec, kind)
    _focus.update(kind=kind, owner=_owner(context), item=item, serial=_focus['serial'] + 1, origin=origin)
    state = _view(context)
    record = dict(_focus)
    state['focus'] = record
    # A list explicitly browses a data record; it is not a tool's persistent selection.
    if origin != 'list' and kind not in ('FABRIC', 'BOND') and getattr(context.area, 'type', None) != 'PROPERTIES':
        state['tools'][state['tool']] = record
    acknowledge_selection(context)
    from . import properties as P
    P.sync_looks(context)
    _tag_properties()


def release(context, kind):
    """Clearing another tool's selection must not steal the currently shown details."""
    record = _record(context)
    if record.get('owner') == _owner(context) and record.get('kind') == kind:
        publish(context, None, origin='clear')
    else:
        _tag_properties()


def piece_clicked(context=None, origin='click'):
    publish(context or bpy.context, 'PIECE', origin=origin)


def focus_item(context, kind, iid):
    """Show fabric (kind 'FABRIC') or interlining ('BOND') `iid` in the Tool tab (Lists > Fabrics, user
    2026-10-25). Its row selects its pieces; publishing acknowledges that projection. A later piece click publishes
    a new detail target even when the selection itself stays the same."""
    publish(context, kind, iid)


def focused_item(spec, kind, context=None):
    """The id of the fabric / interlining (kind 'FABRIC' / 'BOND') the Tool tab shows, or None."""
    from .core import fabric as FA
    context = context or bpy.context
    record = _record(context)
    iid = record.get('item') if record.get('owner') == _owner(context) and record.get('kind') == kind else None
    if spec is None or iid is None:
        return None
    return iid if iid in (FA.fabric_index(spec) if kind == 'FABRIC' else FA.interlining_index(spec)) else None


def _tag_properties():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else []:
        for area in win.screen.areas:
            if area.type == 'PROPERTIES':
                area.tag_redraw()


def _get_piece_name(self):
    spec = store.get(bpy.context)[0]
    name = _selected_piece(bpy.context, spec) if spec is not None else None
    if name is None:
        return ''
    from .core import spec as S
    return S.piece_index(spec)[name].get('label') or name


def _set_piece_name(self, value):
    context = bpy.context
    spec = store.get(context)[0]
    name = _selected_piece(context, spec) if spec is not None else None
    if name is not None:
        from . import seam_edit_tool as SE
        SE.set_label(context, spec, name, value)


PROPS = {                                       # on the Scene (about the selected piece)
    'ac9_piece_name': bpy.props.StringProperty(
        name="Name", get=_get_piece_name, set=_set_piece_name,
        description="The piece's name, shown on the pattern and in the lists (empty: its id; seams and results "
                    "keep the id, so nothing comes apart)"),
}


def _selected_piece(context, spec):
    """The selection's lead piece of this garment (pattern_view.lead_piece), else None."""
    return SEL.piece_selection(context, spec)[1]


def resolve(context, spec):
    """'MEASURE', 'INTERNAL', 'EDGES', 'BUTTON', 'FABRIC', 'SEAM', 'PIECE' or 'GARMENT': what the Tool tab shows."""
    record = _record(context)
    k = record.get('kind') if record.get('owner') == _owner(context) else None
    if k in ('FABRIC', 'BOND') and focused_item(spec, k, context) is not None:
        return k
    if k is not None and k not in ('FABRIC', 'BOND'):
        current = _target(context, spec, k)
        if current is not None and current == record.get('item'):
            return k
    # A removed or stale target falls back to the current piece. Never display a different object at its old index.
    if spec is not None and _selected_piece(context, spec) is not None:
        return 'PIECE'
    return 'GARMENT'


def snapshot(context, spec=None):
    """Read-only diagnostic: the selected editing target, resolved details and the last input's sequence."""
    if spec is None:
        spec = store.get(context)[0]
    record = _record(context)
    own = record.get('owner') == _owner(context)
    shown = resolve(context, spec)
    actual = focused_item(spec, shown, context) if shown in ('FABRIC', 'BOND') else _target(context, spec, shown)
    return dict(kind=shown, target=actual, requested=record.get('kind') if own else None,
                requested_target=record.get('item') if own else None,
                serial=record.get('serial', 0) if own else 0, origin=record.get('origin') if own else None)


def native_selection(context):
    """selection.watch is the sole source for external selection changes, after its canonical projection."""
    piece_clicked(context, origin='selection')
    from . import properties as P                 # the Look fields show the piece now selected
    try:
        P.sync_looks()
    except Exception:
        pass


@persistent
def _on_load(_):
    _views.clear()
    _batch.clear()
    _subscribe_tools()
    _focus.update(kind=None, owner=None, item=None, serial=0, origin=None)
    _observed.update(owner=None, selection=None)
    from . import properties as P
    try:
        P.sync_looks()
    except Exception:
        pass


@persistent
def _on_history(_):
    # Keep a surviving focus; resolve checks ownership, identity and the editing target after restoration.
    acknowledge_selection(bpy.context)
    _tag_properties()


def _base_poll(context):
    a = context.area
    return a is not None and a.type == 'PROPERTIES' and store.get(context)[0] is not None


class _ToolTab:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"


class AC9_PT_ToolRun(_ToolTab, bpy.types.Panel):
    """The run buttons above whatever was touched last: the same as the sidebar's Run panel."""
    bl_label = "AC9 Run"

    @classmethod
    def poll(cls, context):
        return _base_poll(context)

    def draw(self, context):
        U.draw_missing_pieces(self.layout, context, store.get(context)[0])
        from . import garment_view as GV
        GV.draw_mismatch(self.layout, context, store.get(context)[0])
        U.draw_run(self.layout, context)


def whole_garment_picked(context):
    """The dressed garment object picked as a whole (Blender's own select tools: they do not pick pieces) while no
    AC9 tool is active."""
    from . import dress as D, toolbar as TB
    act = context.view_layer.objects.active if context.view_layer else None
    if act is None or not act.name.startswith(D.GARMENT_PREFIX) or not act.select_get():
        return False
    t = TB.active_tool(context) or ''
    return not t.startswith('ac9_cloth_sim.')


def draw_whole_garment_hint(layout, context):
    """One line and the button to Whole Pieces, only while the whole garment is picked with Blender's own tools
    (Fable review 2026-11-17: the step between the 2D pattern and the dressed garment; not shown all the time)."""
    if not whole_garment_picked(context):
        return
    from . import toolbar as TB
    box = layout.box()
    box.label(text_ctxt=CTX, text="Whole garment selected", icon='INFO')      # short: the Properties editor is narrow
    box.label(text_ctxt=CTX, text="Click a piece with:")
    TB.tool_button(box, context, TB.WHOLE_PIECES_ID, "Whole Pieces", 'ORIENTATION_GLOBAL')


class AC9_PT_ToolGarment(_ToolTab, bpy.types.Panel):
    """The garment: which one, its pieces, mesh and fabric, obstacles, its result."""
    bl_label = "AC9 Garment"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'GARMENT'

    def draw(self, context):
        from .core import spec as S
        layout = self.layout
        props = context.scene.ac9_cloth_sim
        spec, _ = store.get(context)
        L = U.live_running()          # nothing of the garment is taken by a running Simulate
        draw_whole_garment_hint(layout, context)
        r = layout.row()
        r.enabled = not L
        r.prop(props, "spec_text", text="Garment")
        c = {s: 0 for s in S.PIECE_STATES}
        for pc in spec['pieces']:
            c[S.piece_state(pc)] += 1
        layout.label(text=tx("{0}: {1} on, {2} freeze, {3} off", spec['name'], c['active'], c['freeze'], c['off_pattern'] + c['off_all']), icon='MESH_GRID')
        layout.separator()
        fc = layout.column()
        fc.enabled = not L
        U.draw_fabric(fc, context, spec)
        # which end is on top where a piece closes over itself is the piece's Ends (AC9 Piece > Placement, user
        # 2026-11-22); the garment's Front Over of an older file is still read while a piece has no Ends
        sp = layout.row().split(factor=0.4)          # the pieces' default look, under the default fabric
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text_ctxt=CTX, text="Default Look")
        sp.template_ID(props, "garment_look", new="material.new")
        # the obstacles are listed and added in Lists > Obstacles (Fable UI review 2026-10-21, b-3): their count here
        from . import dress as D
        cols = D.collider_objects(context)
        if cols:
            layout.label(text=tx("Collision: {0}", len(cols)), icon='MOD_PHYSICS')
        if D.AC9_OT_SimDress.running() is None:
            head, body = layout.panel("ac9_cloth_sim_tool_result", default_closed=False)
            head.label(text_ctxt=CTX, text="Result")
            if body is not None and not U.draw_result(body, spec):
                body.label(text_ctxt=CTX, text="No result yet", icon='INFO')


_sizec = {}


def _piece_size(context, spec, pc):
    """The piece's width x height and the length around it, in the scene's unit (DECISIONS
    cloth-sim-dimension-display-2026-10-23); per pattern version."""
    import numpy as np
    from .core import spec as S
    from .shape_tool import fmt
    k = (store._cache.get('key'), pc['name'])
    if k not in _sizec:
        if len(_sizec) > 64:
            _sizec.clear()
        B = S.Outline(pc).B
        _sizec[k] = (*(B.max(0) - B.min(0)), S.polyline_len(np.vstack([B, B[:1]])))
    w, h, around = _sizec[k]
    return tx("{0} x {1}   around {2}", fmt(context, w), fmt(context, h), fmt(context, around))


class AC9_PT_ToolPiece(_ToolTab, bpy.types.Panel):
    """The selected piece: its state, role, mesh, fold lines, placement."""
    bl_label = "AC9 Piece"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'PIECE'

    def draw(self, context):
        from .core import spec as S
        from .core import elastic as EL
        from .core import fabric as FA
        layout = self.layout
        spec, _ = store.get(context)
        name = _selected_piece(context, spec)
        if name is None:                              # the selection changed between poll and draw
            return
        pcs = S.piece_index(spec)
        sel, name = SEL.piece_selection(context, spec)
        pc = pcs[name]
        names = list(sel)
        # the name is a field at the top, as an object's name is in the Properties editor (user OK 2026-10-25,
        # REVIEW_fable_piece_rename_ui_2026-10-25); the id the seams and results hold stays under it
        layout.prop(context.scene, "ac9_piece_name", text="", icon='MESH_DATA')
        if len(sel) > 1:
            layout.label(text=tx("Name and size: {0}", name), icon='INFO')
        if pc.get('label'):
            layout.label(text=tx("id: {0}", name))
        layout.label(text=_piece_size(context, spec, pc))
        gname = next((g['name'] for g in spec.get('garments', []) if g['id'] == pc.get('garment')), None)
        if gname:                                    # its garment (user 2026-10-25, picked from the Simulation panel)
            layout.label(text=tx("Garment: {0}", gname), icon='STICKY_UVS_LOC')
        if len(sel) > 1:
            layout.label(text=tx("{0} pieces selected: a change sets all", len(sel)), icon='INFO')
        # while Live / Local Sim runs it takes Active / Freeze, Strengthen and Elastic at once; the rest of the piece
        # (its pattern, pair, fabric, mesh, placement) is read when a run starts, so it is grayed (user 2026-11-16)
        L = U.live_running()
        # the order (user 2026-11-17, REVIEW_fable_piece_panel_mirror_center_2026-11-17 §3 案 1): what is touched most
        # first -- the state and display, always shown -- then folding sections: fabric and look, the simulation's
        # values (those a running Simulate takes on top), the pair, the piece's own symmetry, where it is placed
        row = layout.row()
        row.label(text_ctxt=CTX, text="State:")
        row.operator("ac9_cloth_sim.invert_piece_selection", icon='ARROW_LEFTRIGHT')    # user 2026-10-25
        U.draw_selected_states(layout, current=S.piece_state(pc), title=None,
                               states={S.piece_state(pcs[n]) for n in names})
        layout.label(text_ctxt=CTX, text="Display:")               # its own row: hiding is not a state (user 2026-11-15)
        U.draw_hide_buttons(layout)
        # several pieces selected: each field shows this (the active) piece's value, its label says "mixed" in red
        # when the selected differ, a change sets them all (user 2026-10-20; the label, not a label beside the field,
        # so the field keeps its width: Fable UI review 2026-10-21)
        props = context.scene.ac9_cloth_sim

        def section(idname, label):                   # label: the English (a helper translates what it draws)
            head, body = layout.panel(idname, default_closed=False)
            head.label(text=tx(label))
            if body is None:
                return None
            c = body.column()
            c.use_property_split = True
            c.use_property_decorate = False
            return c

        col = None

        def field(prop, label, value, extra=None, live=False, shown=False, container=None, eligible=True):
            # live: taken by a running Simulate (marked then); shown: only how it looks (never grayed)
            row = (container if container is not None else col).row(align=True)
            row.enabled = eligible and (live or shown or not L)
            mixed = len({value(n) for n in names}) > 1
            row.alert = mixed
            lab = tx(label)                           # the English given; tx() of a translated one keeps it
            row.prop(props, prop, text=tx("{0} (mixed)", lab) if mixed else lab, icon=U.live_icon(live and L))
            if extra:
                extra(row)

        from . import control_basis as CB
        control_states = CB.contexts(context, spec)
        if any(control_states.get(n, {}).get('basis') == CB.UNKNOWN for n in names):
            box = layout.box()
            box.alert = True
            box.label(text=tx('Resolve saved shape'), icon='INFO')
            row = box.row(align=True)
            row.operator('ac9_cloth_sim.reset')
            row.operator('ac9_cloth_sim.reset_placed')

        # ---- fabric and look
        col = section("ac9_cloth_sim_piece_fabric", "Fabric & Look")
        if col is not None:
            def edit_fabric(row):                     # the same dialog as Lists > Fabrics' Edit... (user 2026-10-25)
                f = FA.resolved(spec, name)
                sub = row.row(align=True)
                sub.enabled = f is not None
                op = sub.operator("ac9_cloth_sim.fabric_edit", text="", icon='GREASEPENCIL')
                op.fabric_id = f['id'] if f else ''

            field("piece_fabric", "Fabric", lambda n: (FA.resolved(spec, n) or {}).get('id'), edit_fabric)

            def edit_bond(row):                       # the same dialog as AC9 Interlining's Edit... (user 2026-10-25)
                b = FA.piece_bond(spec, name)
                sub = row.row(align=True)
                sub.enabled = b is not None
                op = sub.operator("ac9_cloth_sim.interlining_edit", text="", icon='GREASEPENCIL')
                op.interlining_id = (b or {}).get('id') or ''     # none yet: a garment from before the list
                op.piece = name

            field("piece_bond", "Interlining",
                  lambda n: (lambda b: (b.get('name'), b.get('preset')) if b else None)(FA.piece_bond(spec, n)),
                  edit_bond)
            # the look (render material): Blender's material field (a picture, search, New, x = the default's)
            from .core import look as LK
            lmix = len({(LK.piece_look(spec, n) or {}).get('material') for n in names}) > 1
            sp = col.row().split(factor=0.4)
            r = sp.row()
            r.alignment = 'RIGHT'
            r.alert = lmix
            r.label(text_ctxt=CTX, text="Look (mixed)" if lmix else "Look")
            sp.template_ID(props, "piece_look", new="material.new")
            if LK.piece_look(spec, name) is None:
                d = LK.default_look(spec)
                r = col.row()
                r.active = False
                r.label(text_ctxt=CTX, text=tx("Default: {0}", d['material']) if d else "No look (none for the garment)")
            field("piece_thickness", "Thickness", lambda n: pcs[n].get('thickness', True) is not False, shown=True)

        # ---- the simulation's values: those a running Simulate takes first
        col = section("ac9_cloth_sim_piece_sim", "Simulation")
        if col is not None:
            field("piece_strengthen", tx("Strengthen"), lambda n: bool(pcs[n].get('strengthen')), live=True)
            field("piece_strengthen_factor", tx("Strength Factor"), lambda n: S.piece_strengthen_factor(pcs[n]),
                  live=True)
            field("piece_elastic", "Elastic", lambda n: EL.piece_ratio(spec, n), live=True)
            # Role and Start Shape under Experimental Features (user 2026-11-22, DECISIONS
            # cloth-sim-roles-auto-dressing-2026-11-22): a role is a label for filling fields and for the automation to
            # come, not a setting a Simulate run reads; kept, out of the default screen
            from . import dress as _D
            if _D.experimental(context):
                col.separator()
                from .core import roles as RO
                if names:
                    row = col.row()
                    row.enabled = not L and all(pcs[n].get('enabled', True) for n in names)
                    row.alert = len({RO.role_of(pcs[n]) for n in names}) > 1
                    sp = row.split(factor=0.4)
                    r = sp.row()
                    r.alignment = 'RIGHT'
                    r.label(text_ctxt=CTX, text="Role (mixed)" if row.alert else "Role")
                    sp.operator_menu_enum("ac9_cloth_sim.set_role", "role", text=tx(RO.EN.get(RO.role_of(pc), "Other")))
                    # how it starts against its partners (user 2026-11-02): its own field, under the role
                    row = col.row()
                    row.enabled = not L and all(pcs[n].get('enabled', True) for n in names)
                    row.alert = len({RO.start_of(pcs[n]) for n in names}) > 1
                    sp = row.split(factor=0.4)
                    r = sp.row()
                    r.alignment = 'RIGHT'
                    r.label(text_ctxt=CTX, text="Start Shape (mixed)" if row.alert else "Start Shape")
                    st_ = RO.start_of(pc)
                    sp.operator_menu_enum("ac9_cloth_sim.set_start", "start", text=tx(RO.START_EN[st_]))
                    note = {'edge': ("Simulate pulls it from this ring onto the sewn edge by its seams.",
                                     "Band on Edge lays it there now."),}.get(st_)
                    for ln in note or ():
                        for w_ in wrap(tx(ln), U._wrap_width(context)):
                            col.label(text=w_)
                else:
                    col.label(text_ctxt=CTX, text="Role: (the piece is off)")

            def back(row):                            # its own distance: the arrow gives it back the garment's
                if pc.get('mesh_mm'):
                    sub = row.row(align=True)
                    sub.operator_context = 'EXEC_DEFAULT'
                    sub.operator("ac9_cloth_sim.piece_mesh", text="", icon='LOOP_BACK').mesh_mm = 0.0

            field("piece_distance", "Particle Distance (mm)",
                  lambda n: pcs[n].get('mesh_mm') or props.mesh_size, back)
            # the layer (user 2026-10-25): higher = kept outside the lower while they differ; a person sets
            # them back to 0 (the arrow: every piece's). Under Experimental Features (user 2026-11-12); still drawn
            # while a piece of the garment has one, so a layer kept in an older file can be seen and set back
            from . import dress as _D
            if _D.experimental(context) or any(S.piece_layer(x) for x in spec.get('pieces', [])):
                field("piece_layer", "Layer", lambda n: S.piece_layer(pcs[n]),
                      lambda row: row.operator("ac9_cloth_sim.layers_to_zero", text="", icon='LOOP_BACK'))
            if pc.get('enabled', True):
                from . import button_ui as BU   # the piece it buttons onto (user 2026-10-22, by hand)
                bc = col.column()
                bc.enabled = not L
                BU.draw_closes_onto(bc, context, spec, name)

        # ---- the pair
        head, body = layout.panel("ac9_cloth_sim_piece_pair", default_closed=False)
        head.label(text_ctxt=CTX, text="Pair", icon='MOD_MIRROR')
        if body is not None:
            pb = body.column()
            pb.enabled = not L
            if len(names) > 1:
                pb.label(text=tx("Pair shown: {0}", name), icon='INFO')
            draw_pair_box(pb, context, spec, name)

        # ---- the piece's own symmetry: edited symmetrically about its center line (a piece's setting, user
        # 2026-10-21; not a pair setting, so its own section, 2026-11-17). Switched on but no longer symmetric: red
        head, body = layout.panel("ac9_cloth_sim_piece_symmetry", default_closed=False)
        head.label(text_ctxt=CTX, text="This Piece's Symmetry")
        if body is not None:
            from .core import draft as DR
            on = bool(pc.get('mirror_edit'))
            smix = len({bool(pcs[n].get('mirror_edit')) for n in sel if n in pcs}) > 1
            r = body.row()
            r.enabled = not L
            broken = on and DR.self_mirror_map(pc) is None
            sub = r.row()
            sub.alert = smix or broken
            # a button that stays pressed while on (user 2026-10-27: not a checkbox; as Fold of a dart)
            sub.operator("ac9_cloth_sim.symmetric_edit", depress=on, icon='MOD_MIRROR',
                         text=tx("Symmetric Edit") + (tx(" (mixed)") if smix else (tx(" (broken)") if broken else "")))
            # make it symmetric again: which half stays chosen from the button's list (user 2026-11-17, not F9)
            r.operator_menu_enum("ac9_cloth_sim.symmetrize", "keep", text=tx("Symmetrize"))

        # ---- where it is placed: read when a run starts
        head, body = layout.panel("ac9_cloth_sim_piece_placed", default_closed=False)
        head.label(text_ctxt=CTX, text="Placement")
        if body is None:
            return
        placements = spec.get('placement', {})
        placed = name in placements
        all_placed = all(n in placements for n in names)
        flats = {S.is_flat(placements.get(n, {})) for n in names}
        styles = {'flat' if S.is_flat(placements.get(n, {})) else 'curved' if n in placements else 'missing'
                  for n in names}
        if len(names) > 1:
            body.label(text=tx("{0} / {1} placed", sum(n in placements for n in names), len(names)),
                       icon='CHECKMARK' if all_placed else 'INFO')
        else:
            body.label(text_ctxt=CTX, text="Placed" if placed else "Not placed yet (Place in the sidebar)",
                       icon='CHECKMARK' if placed else 'ERROR')
        layout = body.column()
        layout.enabled = not L
        if 'flat' in styles:
            # laid flat (user 2026-11-10): no guide to go round or along. Its turn on the face it lies on takes the
            # seat Around / Height / Tilt have for a guide's piece (user 2026-11-11, REVIEW_fable_flat_turn_snap_mirror_ui
            # §1: one field, never shown beside those); the folded transform below stays the hand move
            pcol = layout.column()
            pcol.use_property_split = True
            pcol.use_property_decorate = False
            row = pcol.row(align=True)
            from .core import body_axes as BA
            field("piece_turn", "Orientation (deg)",
                  lambda n: BA.flat_turn(placements[n]['flat']) if S.is_flat(placements.get(n, {})) else None,
                  container=pcol, eligible=all_placed and flats == {True})
            row.operator("ac9_cloth_sim.turn_flat_90", text="", icon='FILE_REFRESH')
        if 'curved' in styles:
            # where it is on its guide, set without dragging, and turned over (user 2026-11-01: the angle changed
            # alone, the collar's outer edge to the neck; a press of Place always slides)
            pcol = layout.column()
            pcol.use_property_split = True
            pcol.use_property_decorate = False
            eligible = all_placed and flats == {False}
            field("piece_around", "Around (deg)", lambda n: placements.get(n, {}).get('theta', 0.),
                  container=pcol, eligible=eligible)
            field("piece_height", "Height", lambda n: placements.get(n, {}).get('h', 0.),
                  container=pcol, eligible=eligible)
            row = pcol.row(align=True)
            field("piece_tilt", "Tilt (deg)", lambda n: placements.get(n, {}).get('tilt', 0.),
                  container=pcol, eligible=eligible)
            row.operator("ac9_cloth_sim.flip_up_down", text="", icon='FILE_REFRESH')
            # the adjusted shape's location and rotation, folded (user 2026-11-10: the sidebar's Item tab is far from
            # here). The placed object's own transform: typing in it is a hand move, as G / R are
        if placed:
            draw_transform(layout, spec, name)
        # how it goes round (user 2026-11-22): Ends on every placed piece; Overlap only when one end is over the
        # other, Stretch only when closed. Roles do not decide it
        from . import properties as P
        eligible = all_placed and flats == {False}
        ends = {P._ends_of(n, spec) for n in names}
        field("piece_ends", "Ends", lambda n: P._ends_of(n, spec), container=layout, eligible=eligible)
        if ends <= set(CL_.LAPPED):
            field("piece_overlap", "Overlap", lambda n: round(P._overlap_of(n, spec)[0], 1),
                  extra=lambda row: row.prop(props, "piece_overlap_auto", toggle=True),
                  container=layout, eligible=eligible)
        if 'open' not in ends:
            field("piece_ring", "Stretch (x)", lambda n: P._ring_of(n, spec)[0],
                  container=layout, eligible=eligible)
        # the fold lines are set on the line itself now: click it (AC9 Internal Line, user 2026-10-21)
        n_lines = sum(1 for x in pc.get('internals', []) if x.get('kind') != 'baseline')
        if n_lines:
            layout.label(text=tx("{0} internal line(s): use Edit Pieces", n_lines), icon='INFO')


def _caption(layout, text):
    """A section's gray caption inside the Pair box (no box in a box)."""
    r = layout.row()
    r.active = False
    r.label(text=tx(text))


def draw_pair_box(layout, context, spec, name):
    """The Pair box (user 2026-11-11, REVIEW_fable_pair_ui_2026-11-11 b-1; in three parts 2026-11-17): which piece it
    pairs with; the pattern (shapes linked or drawn apart); the placement in 3D (the mirror plane, the mirror center,
    the pair buttons); the simulated result (Mirror 3D to Pair). The right-click menus keep their buttons. Symmetric
    Edit (one piece's own halves) is not a pair setting: its own section."""
    from .core import spec as S
    from .core import mirror_result as MR_
    from . import dress as DR_
    from . import placement as PLC
    from . import placed_view as PLV_
    pl = spec.get('placement', {})
    twin = next((b if a == name else a for a, b in S.twins(spec) if name in (a, b)), None)
    paired = next((p[1] if p[0] == name else p[0] for p in spec.get('pairs', []) if name in p), None)
    box = layout.box()
    if twin is None and paired is None:
        box.label(text_ctxt=CTX, text="No pair (Mirror Copy, or select two pieces and Pair)")
        box.operator("ac9_cloth_sim.pair", text="Pair", icon='MOD_MIRROR')
        return
    other = twin or paired
    apart = twin is not None and any(name in p for p in U.twins_apart(spec))
    unlinked = twin is not None and S.pair_unlinked(spec, name, twin)
    r = box.row()
    r.alert = apart
    r.label(text_ctxt=CTX, text=tx("With {0}", other) + (tx(" (no longer mirrored)") if apart else ""),
            icon='ERROR' if apart else 'ARROW_LEFTRIGHT')
    # -- the pattern
    _caption(box, "Pattern")
    box.label(text_ctxt=CTX, text="Shapes linked: editing one edits the other" if twin
              else "Shapes drawn apart: only placed as a pair", icon='BLANK1')
    # -- the placement in 3D
    _caption(box, "3D Placement")
    if twin:
        r = box.row()
        r.active = not unlinked
        r.label(text_ctxt=CTX, text=tx("Unlinked: each side moves on its own") if unlinked
                else tx("Linked: moves as its mirror image"), icon='UNLINKED' if unlinked else 'LINKED')
    plane = S.plane_of(S.piece_index(spec)[name])
    row = box.row(align=True)
    row.label(text_ctxt=CTX, text="Mirror Plane")
    for v, t, _d in PLC.PLANE_ITEMS:
        row.operator("ac9_cloth_sim.pair_plane", text=t, depress=plane == v).plane = v
    # the mirror center (user 2026-11-17): a pair laid flat mirrors about the garment's center, an empty a person
    # moves; one on the body about the body's center; an older file's pair about its own plane until aligned
    lead = name if name in pl else other
    own = S.stored_mirror_at(spec, lead)
    flat = lead in pl and S._pair_flat(spec, lead)
    c = S.garment_center(spec)
    if flat and own is not None and S.plane_of(S.piece_index(spec)[lead]) == plane:
        r = box.row()
        r.alert = True
        r.label(text_ctxt=CTX, text=(tx("Own plane {0} mm, the center's {1} mm", f"{own:.0f}", f"{c[S.axis_of(plane)]:.0f}")
                      if c is not None else tx("Own plane {0} mm (no center yet)", f"{own:.0f}")), icon='ERROR')
        box.operator("ac9_cloth_sim.center_align", icon='PIVOT_CURSOR')
    elif flat and c is not None:
        ob = PLV_.find_garment_center(spec)
        r = box.row(align=True)
        r.label(text_ctxt=CTX, text=ob.name if ob is not None else tx("Mirror Center"), icon='EMPTY_AXIS')
        r.operator("ac9_cloth_sim.center_select", text="Select Center")
        if ob is not None:
            col = box.column(align=True)
            col.prop(ob, "location", text="")
    else:
        box.label(text=tx("Mirror Center: the body's center"), icon='BLANK1')
        if flat:                            # laid flat but no center yet (paired before it was laid flat)
            box.operator("ac9_cloth_sim.center_align", icon='PIVOT_CURSOR')
    row = box.row(align=True)
    if twin:
        row.operator("ac9_cloth_sim.unlink_pair", text=tx("Link") if unlinked else tx("Unlink"),
                     icon='LINKED' if unlinked else 'UNLINKED')
    else:
        row.operator("ac9_cloth_sim.pair", text="Unpair", icon='X')
    row.operator("ac9_cloth_sim.swap_sides", text="Swap Sides", icon='ARROW_LEFTRIGHT')
    if paired or unlinked:
        box.operator("ac9_cloth_sim.mirror_pose", text="Mirror Placement", icon='MOD_MIRROR')
    # -- simulated: one side's shape onto the other (the pairs Mirror 3D to Pair copies between, as its poll)
    g_ = DR_.garment(spec)
    have_ = (set(DR_.garment_pieces(g_)) - set(DR_.hidden_pieces(g_, spec) or ())
             - {p['name'] for p in spec['pieces'] if S.piece_state(p) == 'off_all'}
             if g_ is not None and DR_.has_result(g_) else set())
    mate = next((b if a == name else a for a, b in MR_.pairs(spec) if name in (a, b)), None)
    if mate and name in have_ and mate in have_:
        _caption(box, "Simulation Result")
        box.operator("ac9_cloth_sim.mirror_result", text="Mirror 3D to Pair", icon='MOD_MIRROR')


def draw_transform(layout, spec, name):
    """A folded Transform under a placed piece: its placed object's Location and Rotation (the object of a pair
    holds both pieces; nothing to show until the adjusted shape is made)."""
    from . import placed_view as PLV
    head, body = layout.panel("ac9_cloth_sim_piece_transform", default_closed=True)
    head.label(text_ctxt=CTX, text="Location & Rotation")
    if body is None:
        return
    from . import selection as SEL
    if len(SEL.piece_selection(bpy.context, spec)[0]) > 1:
        body.label(text=tx("Placed object: {0}", name), icon='INFO')
    ob = PLV.objects(spec).get(name)
    if ob is None:
        body.label(text_ctxt=CTX, text="No placed shape yet (Place it)", icon='INFO')
        return
    col = body.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(ob, "location", text="Location")
    col.prop(ob, "rotation_euler", text="Rotation")


def fabric_lines(f):
    """A fabric's numbers as short lines (AC9 Fabric; the dialog Edit... changes them)."""
    from .core import fabric as FA
    th = FA.thickness(f)
    out = [tx("{0:g} g/m², {1:g} µN·m", f['weight_gsm'], FA.bending(f)) + (tx(", {0:g} mm", th) if th else '')]
    st = FA.stretch(f)
    if st is not None:
        out.append(tx("Stretch {0:g}", st[0]) + (tx("/{0:g}", st[1]) if st[1] != st[0] else '') + tx(" N/m"))
    if f.get('shear_N_m') is not None or f.get('bend_hyst_per_m') is not None:
        out.append((tx("Shear {0:g} N/m", float(f['shear_N_m'])) if f.get('shear_N_m') is not None else tx("Shear -"))
                   + (tx(", hyst {0:g}/m", float(f['bend_hyst_per_m'])) if f.get('bend_hyst_per_m') is not None else ''))
    return out


class AC9_PT_ToolFabric(_ToolTab, bpy.types.Panel):
    """The fabric picked in Lists > Fabrics: its preset, numbers, pieces; Edit... (the same dialog as the pencil of
    AC9 Piece), Make Default, Select Pieces, Delete."""
    # user 2026-10-25, REVIEW_fable_lists_ui_2026-10-25 §3.1
    bl_label = "AC9 Fabric"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'FABRIC'

    def draw(self, context):
        from .core import fabric as FA
        from . import piece_look as PL
        layout = self.layout
        spec, _ = store.get(context)
        fid = focused_item(spec, 'FABRIC', context)
        if fid is None:
            return
        f = FA.fabric_index(spec)[fid]
        is_d = fid == spec.get('default_fabric_id')
        row = layout.row(align=True)
        sw = row.row()
        sw.ui_units_x = 0.9
        sw.template_node_socket(color=PL.fabric_colors(spec).get(fid, (0.5, 0.5, 0.5, 1.0)))
        row.label(text_ctxt=CTX, text=fabric_name(f['name']) + (tx(" (default)") if is_d else ''), icon='SOLO_ON' if is_d else 'MATERIAL')
        L = U.live_running()          # a fabric is read when a run starts: grayed while Simulate runs
        r = layout.row()
        r.enabled = not L
        sp = r.split(factor=0.4)
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text_ctxt=CTX, text="Preset")
        p = FA.preset('fabrics', f.get('preset') or '')
        op = sp.operator_menu_enum("ac9_cloth_sim.fabric_preset", "preset", text=tx(p['name']) if p else tx("Custom"))
        if op is not None:
            op.fabric_id = fid
        col = layout.column(align=True)
        col.active = False
        for ln in fabric_lines(f):
            col.label(text=ln)
        row = layout.row(align=True)
        row.enabled = not L
        row.operator("ac9_cloth_sim.fabric_edit", text="Edit...", icon='GREASEPENCIL').fabric_id = fid
        sub = row.row(align=True)
        sub.enabled = not is_d
        sub.operator("ac9_cloth_sim.fabric_default", text="Make Default", icon='SOLO_ON').fabric_id = fid
        us = U.fabric_users(spec).get(fid, [])
        layout.separator()
        if is_d:
            layout.label(text=tx("Pieces: the rest ({0})", len(us)))
        else:
            for ln in U.wrap(tx("Pieces ({0}): {1}", len(us), ', '.join(us)) if us else tx("Pieces: none"), 40)[:4]:
                layout.label(text=ln)
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = bool(us)
        sub.operator("ac9_cloth_sim.fabric_select", text="Select Pieces", icon='RESTRICT_SELECT_OFF').fabric_id = fid
        sub = row.row(align=True)
        sub.enabled = not is_d and not L
        sub.operator("ac9_cloth_sim.fabric_remove", text="Delete", icon='X').fabric_id = fid
        if is_d:
            r = layout.row()
            r.active = False
            r.label(text_ctxt=CTX, text="The default stays: make another the default to delete it")


class AC9_PT_ToolInterlining(_ToolTab, bpy.types.Panel):
    """The interlining picked in Lists > Fabrics: its preset, what it adds, its pieces; Edit... (the same dialog as
    the pencil of AC9 Piece's Interlining field), Select Pieces, Delete."""
    # user 2026-10-25
    bl_label = "AC9 Interlining"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'BOND'

    def draw(self, context):
        from .core import fabric as FA
        layout = self.layout
        spec, _ = store.get(context)
        iid = focused_item(spec, 'BOND', context)
        if iid is None:
            return
        i = FA.interlining_index(spec)[iid]
        layout.label(text=fabric_name(i['name']), icon='MOD_SOLIDIFY')
        L = U.live_running()          # read when a run starts: grayed while Simulate runs
        r = layout.row()
        r.enabled = not L
        sp = r.split(factor=0.4)
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text_ctxt=CTX, text="Preset")
        p = FA.preset('interlinings', i.get('preset') or '')
        op = sp.operator_menu_enum("ac9_cloth_sim.interlining_preset", "preset", text=tx(p['name']) if p else tx("Custom"))
        if op is not None:
            op.interlining_id = iid
        r = layout.row()
        r.active = False
        r.label(text=tx("+{0:g} g/m², +{1:g} µN·m to the fabric", float(i['weight_gsm']), float(i['bending_uNm'])))
        r = layout.row()
        r.enabled = not L
        r.operator("ac9_cloth_sim.interlining_edit", text="Edit...", icon='GREASEPENCIL').interlining_id = iid
        us = U.interlining_users(spec).get(iid, [])
        layout.separator()
        for ln in U.wrap(tx("Pieces ({0}): {1}", len(us), ', '.join(us)) if us else tx("Pieces: none"), 40)[:4]:
            layout.label(text=ln)
        if not us:
            r = layout.row()
            r.active = False
            r.label(text_ctxt=CTX, text="Give it to pieces in AC9 Piece > Interlining")
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = bool(us)
        sub.operator("ac9_cloth_sim.interlining_select", text="Select Pieces",
                     icon='RESTRICT_SELECT_OFF').interlining_id = iid
        sub = row.row(align=True)
        sub.enabled = not L
        sub.operator("ac9_cloth_sim.interlining_remove", text="Delete", icon='X').interlining_id = iid


class AC9_PT_ToolSeam(_ToolTab, bpy.types.Panel):
    """The seam picked in the list or on the pattern."""
    bl_label = "AC9 Seam"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'SEAM'

    def draw(self, context):
        spec, _ = store.get(context)
        U.draw_seam_attrs(self.layout, context, spec, store.active_seam(context, spec), viewport=False)


class AC9_PT_ToolSeamWalk(_ToolTab, bpy.types.Panel):
    """Lengths & Notches (walk_ui.py) under the seam: the same draw as the sidebar's."""
    bl_label = "Lengths & Notches"
    bl_parent_id = "AC9_PT_ToolSeam"

    def draw(self, context):
        from . import walk_ui
        walk_ui.AC9_PT_SimWalk.draw(self, context)


class AC9_PT_ToolSeamBlend(_ToolTab, bpy.types.Panel):
    """Blend (blend_ui.py) under the seam: the same draw as the sidebar's."""
    bl_label = "Blend"
    bl_parent_id = "AC9_PT_ToolSeam"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        from . import blend_ui
        blend_ui.AC9_PT_SimBlend.draw(self, context)


class AC9_PT_ToolMeasure(_ToolTab, bpy.types.Panel):
    """The measurement picked on the body or the pattern, or in Lists > Tapes (measure_edit.py)."""
    bl_label = "AC9 Measurement"

    @classmethod
    def poll(cls, context):
        a = context.area
        return a is not None and a.type == 'PROPERTIES' and resolve(context, store.get(context)[0]) == 'MEASURE'

    def draw(self, context):
        from . import measure_edit as MEE
        MEE.draw_tool_tab(self.layout, context)


class AC9_PT_ToolButton(_ToolTab, bpy.types.Panel):
    """The button picked in Lists > Fasteners (button_ui.py): its hole, fastened, style."""
    bl_label = "AC9 Button"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'BUTTON'

    def draw(self, context):
        from . import button_ui as BU
        BU.draw_button_tab(self.layout, context)


class AC9_PT_ToolInternal(_ToolTab, bpy.types.Panel):
    """The internal line picked on the pattern (draft_tool.py): its fold, elastic, seams."""
    bl_label = "AC9 Internal Line"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'INTERNAL'

    def draw(self, context):
        from . import draft_tool as DT
        DT.draw_internal_tab(self.layout, context)


class AC9_PT_ToolEdges(_ToolTab, bpy.types.Panel):
    """The outline edges picked on the pattern (draft_tool.py): each length and the total."""
    bl_label = "AC9 Edges"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'EDGES'

    def draw(self, context):
        from . import draft_tool as DT
        DT.draw_edges_tab(self.layout, context)


CLASSES = (AC9_PT_ToolRun, AC9_PT_ToolMeasure, AC9_PT_ToolButton, AC9_PT_ToolInternal, AC9_PT_ToolEdges, AC9_PT_ToolFabric, AC9_PT_ToolInterlining, AC9_PT_ToolGarment, AC9_PT_ToolPiece,
           AC9_PT_ToolSeam, AC9_PT_ToolSeamWalk, AC9_PT_ToolSeamBlend)


def register():
    _views.clear()
    _subscribe_tools()
    for c in CLASSES:
        bpy.utils.register_class(c)
    for k, v in PROPS.items():
        setattr(bpy.types.Scene, k, v)
    _focus.update(kind=None, owner=None, item=None, serial=0, origin=None)
    _observed.update(owner=None, selection=None)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_history not in handlers:
            handlers.append(_on_history)


def unregister():
    bpy.msgbus.clear_by_owner(_bus_owner)
    for hs, n in ((bpy.app.handlers.depsgraph_update_post, '_on_depsgraph'), (bpy.app.handlers.load_post, '_on_load'),
                  (bpy.app.handlers.undo_post, '_on_history'), (bpy.app.handlers.redo_post, '_on_history')):
        for h in [h for h in hs if getattr(h, '__name__', '') == n and getattr(h, '__module__', '') == __name__]:
            hs.remove(h)
    for k in PROPS:
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
