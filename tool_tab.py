# SPDX-License-Identifier: GPL-3.0-or-later
"""The Properties editor's Tool tab: the settings of the last thing touched (user
2026-10-13): a seam picked in the list or on the pattern, a piece selected in the viewport, else the garment.

The Tool tab draws the 3D view sidebar's "Tool" category (Blender's WorkSpaceButtonsPanel), so these panels are
VIEW_3D / UI / 'Tool' and show only where the area is the Properties editor (Fable review 2026-10-13 §1). No
bl_context: they stay in every mode (Fix by Hand goes to Sculpt or Edit Mode)."""
import bpy
from bpy.app.handlers import persistent
from .i18n import tx, wrap

from . import pattern_view as PV
from . import store
from . import selection as SEL
from . import ui as U

# what was touched last: 'SEAM' (seam_index changed, properties.py), 'PIECE' / 'GARMENT' (the selection changed,
# _on_depsgraph), 'FABRIC' / 'BOND' (a row of Lists > Fabrics: a fabric or an interlining, its id in 'item').
# None: from the selection
_focus = {'kind': None, 'sig': None, 'item': None}


def set_focus(kind):
    _focus['kind'] = kind
    _tag_properties()


def _sig(vl):
    act = vl.objects.active
    return (act.name if act is not None else None, tuple(o.name for o in vl.objects.selected))


def focus_item(context, kind, iid):
    """Show fabric (kind 'FABRIC') or interlining ('BOND') `iid` in the Tool tab (Lists > Fabrics, user
    2026-10-25). Its row selects its pieces: the selection seen now is taken as already seen, so _on_depsgraph does
    not turn the tab to AC9 Piece; a later click on a piece does (the last thing touched)."""
    _focus['item'] = iid
    _focus['sig'] = _sig(context.view_layer)
    set_focus(kind)


def focused_item(spec, kind):
    """The id of the fabric / interlining (kind 'FABRIC' / 'BOND') the Tool tab shows, or None."""
    from .core import fabric as FA
    iid = _focus['item'] if _focus['kind'] == kind else None
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
    return PV.lead_piece(context, {pc['name'] for pc in spec['pieces']})


def resolve(context, spec):
    """'MEASURE', 'INTERNAL', 'EDGES', 'BUTTON', 'FABRIC', 'SEAM', 'PIECE' or 'GARMENT': what the Tool tab shows."""
    k = _focus['kind']
    if k in ('FABRIC', 'BOND') and focused_item(spec, k) is not None:
        return k
    if k == 'BUTTON' and spec is not None and spec.get('buttons'):
        from . import button_ui as BU
        if BU._find(spec, BU.active_id(bpy.context, spec)) is not None:
            return 'BUTTON'
    if k == 'MEASURE':
        from . import measure_edit as MEE
        if MEE.selected(context) is not None:
            return 'MEASURE'
    if k == 'INTERNAL' and spec is not None:
        from . import draft_tool as DT
        if DT.selected_internal(context, spec) is not None:
            return 'INTERNAL'
    if k == 'EDGES' and spec is not None:
        from . import draft_tool as DT
        if DT.selected_edges(spec):
            return 'EDGES'
    if k == 'SEAM' and store.active_seam(context, spec) is not None:
        return 'SEAM'
    # whatever was touched last and is gone now (an edge, an internal line, a seam no longer picked) gives the tab
    # back to the selected piece: a click on a piece already selected changes no selection, so _on_depsgraph does
    # not run and the tab stayed on the garment
    if spec is not None and _selected_piece(context, spec) is not None:
        return 'PIECE'
    return 'GARMENT'


@persistent
def _on_depsgraph(scene, depsgraph):
    # the selection changed (a click in the viewport, select all, the Pieces list): the Tool tab shows the piece,
    # or the garment when no piece is selected. A seam picked since stays until the selection changes again
    try:
        vl = bpy.context.view_layer
    except AttributeError:
        return
    if vl is None:
        return
    sig = _sig(vl)
    if sig == _focus['sig']:
        return
    first = _focus['sig'] is None
    _focus['sig'] = sig
    if first:
        return
    piece = bool(SEL.facts(bpy.context))        # a pattern piece, a placed piece or a handle (a box select keeps the
    #                                             active object outside)
    set_focus('PIECE' if piece else 'GARMENT')
    from . import properties as P                 # the Look fields show the piece now selected
    try:
        P.sync_looks()
    except Exception:
        pass


@persistent
def _on_load(_):
    _focus.update(kind=None, sig=None, item=None)
    from . import properties as P
    try:
        P.sync_looks()
    except Exception:
        pass


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
        U.draw_run(self.layout, context)


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
        layout.prop(props, "spec_text", text="Garment")
        c = {s: 0 for s in S.PIECE_STATES}
        for pc in spec['pieces']:
            c[S.piece_state(pc)] += 1
        layout.label(text=tx("{0}: {1} on, {2} freeze, {3} off", spec['name'], c['active'], c['freeze'], c['off_pattern'] + c['off_all']), icon='MESH_GRID')
        layout.separator()
        U.draw_fabric(layout, context, spec)
        # which front is on top where the garment closes (user 2026-11-02, DECISIONS cloth-sim-closure-over-2026-11-02)
        sp = layout.row().split(factor=0.4)
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text=tx("Front Over"))
        sp.row(align=True).prop(props, "closure_over", expand=True)
        sp = layout.row().split(factor=0.4)          # the pieces' default look, under the default fabric
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text="Default Look")
        sp.template_ID(props, "garment_look", new="material.new")
        # the obstacles are listed and added in Lists > Obstacles (Fable UI review 2026-10-21, b-3): their count here
        from . import dress as D
        cols = D.collider_objects(context)
        if cols:
            layout.label(text=tx("Collision: {0}", len(cols)), icon='MOD_PHYSICS')
        if D.AC9_OT_SimDress.running() is None:
            head, body = layout.panel("ac9_cloth_sim_tool_result", default_closed=False)
            head.label(text="Result")
            if body is not None and not U.draw_result(body, spec):
                body.label(text="No result yet", icon='INFO')


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
        pc = S.piece_index(spec)[name]
        sel = SEL.pieces(context)
        # the name is a field at the top, as an object's name is in the Properties editor (user OK 2026-10-25,
        # REVIEW_fable_piece_rename_ui_2026-10-25); the id the seams and results hold stays under it
        layout.prop(context.scene, "ac9_piece_name", text="", icon='MESH_DATA')
        if pc.get('label'):
            layout.label(text=tx("id: {0}", name))
        layout.label(text=_piece_size(context, spec, pc))
        gname = next((g['name'] for g in spec.get('garments', []) if g['id'] == pc.get('garment')), None)
        if gname:                                    # its garment (user 2026-10-25, picked from the Simulation panel)
            layout.label(text=tx("Garment: {0}", gname), icon='STICKY_UVS_LOC')
        if len(sel) > 1:
            layout.label(text=tx("{0} pieces selected: a change sets all", len(sel)), icon='INFO')
        draw_pair_box(layout, context, spec, name)
        # edited symmetrically about its center line (a piece's setting, user 2026-10-21: here, not in the Pattern
        # panel). Switched on but no longer symmetric: red, it does nothing
        from .core import draft as DR
        on = bool(pc.get('mirror_edit'))
        smix = len({bool(S.piece_index(spec)[n].get('mirror_edit')) for n in sel if n in S.piece_index(spec)}) > 1
        r = layout.row()
        broken = on and DR.self_mirror_map(pc) is None
        sub = r.row()
        sub.alert = smix or broken
        # a button that stays pressed while on (user 2026-10-27: not a checkbox; as Fold of a dart)
        sub.operator("ac9_cloth_sim.symmetric_edit", depress=on, icon='MOD_MIRROR',
                     text=tx("Symmetric Edit") + (tx(" (mixed)") if smix else (tx(" (broken)") if broken else "")))
        # make it symmetric again: the left half kept, F9 for the right (user 2026-10-27)
        r.operator("ac9_cloth_sim.symmetrize", text=tx("Symmetrize"))
        layout.separator()
        row = layout.row()
        row.label(text="State:")
        row.operator("ac9_cloth_sim.invert_piece_selection", icon='ARROW_LEFTRIGHT')    # user 2026-10-25
        U.draw_selected_states(layout, current=S.piece_state(pc), title=None)
        layout.separator()
        # several pieces selected: each field shows this (the active) piece's value, its label says "mixed" in red
        # when the selected differ, a change sets them all (user 2026-10-20; the label, not a label beside the field,
        # so the field keeps its width: Fable UI review 2026-10-21)
        pcs = S.piece_index(spec)
        names = [n for n in dict.fromkeys(sel) if n in pcs] or [name]
        props = context.scene.ac9_cloth_sim
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False

        def field(prop, label, value, extra=None):
            row = col.row(align=True)
            mixed = len({value(n) for n in names}) > 1
            row.alert = mixed
            row.prop(props, prop, text=tx("{0} (mixed)", label) if mixed else label)
            if extra:
                extra(row)

        from .core import roles as RO
        if pc.get('enabled', True):
            row = col.row()
            row.alert = len({RO.role_of(pcs[n]) for n in names}) > 1
            sp = row.split(factor=0.4)
            r = sp.row()
            r.alignment = 'RIGHT'
            r.label(text="Role (mixed)" if row.alert else "Role")
            sp.operator_menu_enum("ac9_cloth_sim.set_role", "role", text=tx(RO.EN.get(RO.role_of(pc), "Other")))
            # how it starts against its partners (user 2026-11-02): its own field, under the role
            row = col.row()
            row.alert = len({RO.start_of(pcs[n]) for n in names}) > 1
            sp = row.split(factor=0.4)
            r = sp.row()
            r.alignment = 'RIGHT'
            r.label(text="Start Shape (mixed)" if row.alert else "Start Shape")
            st_ = RO.start_of(pc)
            sp.operator_menu_enum("ac9_cloth_sim.set_start", "start", text=tx(RO.START_EN[st_]))
            note = {'edge': ("Phase Sim lays it on the sewn edge after the body is sewn.",
                             "Live Sim starts from this shape (Band on Edge lays it now)."),}.get(st_)
            for ln in note or ():
                for w_ in wrap(tx(ln), U._wrap_width(context)):
                    col.label(text=w_)
        else:
            col.label(text="Role: (the piece is off)")
        col.separator()
        # the material (fabric, interlining), then the simulation's values for the piece
        def edit_fabric(row):                         # the same dialog as Lists > Fabrics' Edit... (user 2026-10-25)
            f = FA.resolved(spec, name)
            sub = row.row(align=True)
            sub.enabled = f is not None
            op = sub.operator("ac9_cloth_sim.fabric_edit", text="", icon='GREASEPENCIL')
            op.fabric_id = f['id'] if f else ''

        field("piece_fabric", "Fabric", lambda n: (FA.resolved(spec, n) or {}).get('id'), edit_fabric)
        def edit_bond(row):                           # the same dialog as AC9 Interlining's Edit... (user 2026-10-25)
            b = FA.piece_bond(spec, name)
            sub = row.row(align=True)
            sub.enabled = b is not None
            op = sub.operator("ac9_cloth_sim.interlining_edit", text="", icon='GREASEPENCIL')
            op.interlining_id = (b or {}).get('id') or ''     # none yet: a garment from before the list, see piece
            op.piece = name

        field("piece_bond", "Interlining",
              lambda n: (lambda b: (b.get('name'), b.get('preset')) if b else None)(FA.piece_bond(spec, n)), edit_bond)
        # the look (render material): Blender's material field (a picture, search, New, x = the default's)
        from .core import look as LK
        lmix = len({(LK.piece_look(spec, n) or {}).get('material') for n in names}) > 1
        sp = col.row().split(factor=0.4)
        r = sp.row()
        r.alignment = 'RIGHT'
        r.alert = lmix
        r.label(text="Look (mixed)" if lmix else "Look")
        sp.template_ID(props, "piece_look", new="material.new")
        if LK.piece_look(spec, name) is None:
            d = LK.default_look(spec)
            r = col.row()
            r.active = False
            r.label(text=tx("Default: {0}", d['material']) if d else "No look (none for the garment)")
        field("piece_strengthen", tx("Strengthen"), lambda n: bool(pcs[n].get('strengthen')))
        field("piece_strengthen_factor", tx("Strength Factor"), lambda n: S.piece_strengthen_factor(pcs[n]))
        field("piece_thickness", "Thickness", lambda n: pcs[n].get('thickness', True) is not False)
        # the layer (user 2026-10-25): higher = kept outside the lower while they differ; a person sets
        # them back to 0 (the arrow: every piece's)
        field("piece_layer", "Layer", lambda n: S.piece_layer(pcs[n]),
              lambda row: row.operator("ac9_cloth_sim.layers_to_zero", text="", icon='LOOP_BACK'))
        col.separator()

        def back(row):                                # its own distance: the arrow gives it back the garment's
            if pc.get('mesh_mm'):
                sub = row.row(align=True)
                sub.operator_context = 'EXEC_DEFAULT'
                sub.operator("ac9_cloth_sim.piece_mesh", text="", icon='LOOP_BACK').mesh_mm = 0.0

        field("piece_distance", "Particle Distance (mm)", lambda n: pcs[n].get('mesh_mm') or 0, back)
        field("piece_elastic", "Elastic", lambda n: EL.piece_ratio(spec, n))
        if pc.get('enabled', True):
            from . import button_ui as BU       # the piece it buttons onto (user 2026-10-22, by hand)
            BU.draw_closes_onto(col, context, spec, name)
        placed = name in spec.get('placement', {})
        layout.label(text="Placed" if placed else "Not placed yet (Place in the sidebar)",
                     icon='CHECKMARK' if placed else 'ERROR')
        if placed and S.is_flat(spec['placement'][name]):
            # laid flat (user 2026-11-10): no guide to go round or along. Its turn on the face it lies on takes the
            # seat Around / Height / Tilt have for a guide's piece (user 2026-11-11, REVIEW_fable_flat_turn_snap_mirror_ui
            # §1: one field, never shown beside those); the folded transform below stays the hand move
            pcol = layout.column()
            pcol.use_property_split = True
            pcol.use_property_decorate = False
            row = pcol.row(align=True)
            row.prop(props, "piece_turn", text=tx("Orientation (deg)"))
            row.operator("ac9_cloth_sim.turn_flat_90", text="", icon='FILE_REFRESH')
            draw_transform(layout, spec, name)
        elif placed:
            # where it is on its guide, set without dragging, and turned over (user 2026-11-01: the angle changed
            # alone, the collar's outer edge to the neck; a press of Place always slides)
            pcol = layout.column()
            pcol.use_property_split = True
            pcol.use_property_decorate = False
            pcol.prop(props, "piece_around", text=tx("Around (deg)"))
            pcol.prop(props, "piece_height", text=tx("Height"))
            row = pcol.row(align=True)
            row.prop(props, "piece_tilt", text=tx("Tilt (deg)"))
            row.operator("ac9_cloth_sim.flip_up_down", text="", icon='FILE_REFRESH')
            # a band laid as a ring: how big the ring is (user 2026-11-02, DECISIONS cloth-sim-ring-size-2026-11-02)
            from .core import roles as RO
            if (name in RO.with_role(spec, RO.BAND_ROLES)
                    and spec['placement'][name].get('guide') in ('torso', 'neck')):
                row = pcol.row(align=True)
                row.prop(props, "piece_ring", text=tx("Ring Size (x)"))
                row.prop(props, "piece_ring_auto", text=tx("Auto"), toggle=True)
            # the placed shape's location and rotation, folded (user 2026-11-10: the sidebar's Item tab is far from
            # here). The placed object's own transform: typing in it is a hand move, as G / R are
            draw_transform(layout, spec, name)
        # the fold lines are set on the line itself now: click it (AC9 Internal Line, user 2026-10-21)
        n_lines = sum(1 for x in pc.get('internals', []) if x.get('kind') != 'baseline')
        if n_lines:
            layout.label(text=tx("{0} internal line(s): click one for its fold / elastic", n_lines), icon='INFO')


def draw_pair_box(layout, context, spec, name):
    """The Pair box (user 2026-11-11, REVIEW_fable_pair_ui_2026-11-11 b-1): which piece it pairs with and how, its
    mirror plane, and every pair button in one place (the Placement panel's pair rows moved here; the right-click
    menus keep theirs). Symmetric Edit (one piece's own halves) is not a pair setting and stays outside."""
    from .core import spec as S
    from .core import mirror_result as MR_
    from . import dress as DR_
    pl = spec.get('placement', {})
    twin = next((b if a == name else a for a, b in S.twins(spec) if name in (a, b)), None)
    paired = next((p[1] if p[0] == name else p[0] for p in spec.get('pairs', []) if name in p), None)
    box = layout.box()
    head = box.row()
    head.label(text="Pair", icon='MOD_MIRROR')
    if twin is None and paired is None:
        box.label(text="No pair (Mirror Copy, or select two pieces and Pair)")
        box.operator("ac9_cloth_sim.pair", text="Pair", icon='MOD_MIRROR')
        return
    other = twin or paired
    if twin:
        apart = any(name in p for p in U.twins_apart(spec))
        unlinked = bool(pl.get(name, {}).get('unlinked') or pl.get(twin, {}).get('unlinked'))
        r = box.row()
        r.alert = apart
        r.label(text=tx("With {0}", other) + (tx(" (no longer mirrored)") if apart else ""),
                icon='ERROR' if apart else 'BLANK1')
        r = box.row()
        r.active = not unlinked
        r.label(text=tx("Unlinked: each side moves on its own") if unlinked
                else tx("Linked: moves as its mirror image"), icon='UNLINKED' if unlinked else 'LINKED')
        # the mirror plane (user 2026-11-11): Left / Right, Front / Back
        plane = S.plane_of(S.piece_index(spec)[name])
        row = box.row(align=True)
        row.label(text="Mirror Plane")
        for v, t in (('x', "Left / Right"), ('y', "Front / Back")):
            row.operator("ac9_cloth_sim.pair_plane", text=t, depress=plane == v).plane = v
        row = box.row(align=True)
        row.operator("ac9_cloth_sim.unlink_pair", text=tx("Link") if unlinked else tx("Unlink"),
                     icon='LINKED' if unlinked else 'UNLINKED')
        row.operator("ac9_cloth_sim.swap_sides", text="Swap Sides", icon='ARROW_LEFTRIGHT')
        if unlinked:
            box.operator("ac9_cloth_sim.mirror_pose", text="Mirror Placement", icon='MOD_MIRROR')
    else:
        box.label(text=tx("Paired with {0}", other), icon='BLANK1')
        row = box.row(align=True)
        row.operator("ac9_cloth_sim.pair", text="Unpair", icon='X')
        row.operator("ac9_cloth_sim.swap_sides", text="Swap Sides", icon='ARROW_LEFTRIGHT')
        box.operator("ac9_cloth_sim.mirror_pose", text="Mirror Placement", icon='MOD_MIRROR')
    # simulated: one side's shape onto the other (the pairs Mirror 3D to Pair copies between, as its poll)
    g_ = bpy.data.objects.get(DR_.garment_name(spec))
    have_ = (set(DR_.garment_pieces(g_)) - set(DR_.hidden_pieces(g_, spec) or ())
             - {p['name'] for p in spec['pieces'] if S.piece_state(p) == 'off_all'}
             if g_ is not None and DR_.has_result(g_) else set())
    mate = next((b if a == name else a for a, b in MR_.pairs(spec) if name in (a, b)), None)
    if mate and name in have_ and mate in have_:
        box.operator("ac9_cloth_sim.mirror_result", text="Mirror 3D to Pair", icon='MOD_MIRROR')


def draw_transform(layout, spec, name):
    """A folded Transform under a placed piece: its placed object's Location and Rotation (the object of a pair
    holds both pieces; nothing to show until the placed shape is made)."""
    from . import placed_view as PLV
    head, body = layout.panel("ac9_cloth_sim_piece_transform", default_closed=True)
    head.label(text="Location & Rotation")
    if body is None:
        return
    ob = PLV.objects(spec).get(name)
    if ob is None:
        body.label(text="No placed shape yet (Move by Hand)", icon='INFO')
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
    """The fabric picked in Lists > Fabrics (user 2026-10-25, REVIEW_fable_lists_ui_2026-10-25 §3.1): its preset,
    numbers, pieces; Edit... (the same dialog as the pencil of AC9 Piece), Make Default, Select Pieces, Delete."""
    bl_label = "AC9 Fabric"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'FABRIC'

    def draw(self, context):
        from .core import fabric as FA
        from . import piece_look as PL
        layout = self.layout
        spec, _ = store.get(context)
        fid = focused_item(spec, 'FABRIC')
        if fid is None:
            return
        f = FA.fabric_index(spec)[fid]
        is_d = fid == spec.get('default_fabric_id')
        row = layout.row(align=True)
        sw = row.row()
        sw.ui_units_x = 0.9
        sw.template_node_socket(color=PL.fabric_colors(spec).get(fid, (0.5, 0.5, 0.5, 1.0)))
        row.label(text=f['name'] + (tx(" (default)") if is_d else ''), icon='SOLO_ON' if is_d else 'MATERIAL')
        sp = layout.row().split(factor=0.4)
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text="Preset")
        p = FA.preset('fabrics', f.get('preset') or '')
        op = sp.operator_menu_enum("ac9_cloth_sim.fabric_preset", "preset", text=tx(p['name']) if p else tx("Custom"))
        if op is not None:
            op.fabric_id = fid
        col = layout.column(align=True)
        col.active = False
        for ln in fabric_lines(f):
            col.label(text=ln)
        row = layout.row(align=True)
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
        sub.enabled = not is_d
        sub.operator("ac9_cloth_sim.fabric_remove", text="Delete", icon='X').fabric_id = fid
        if is_d:
            r = layout.row()
            r.active = False
            r.label(text="The default stays: make another the default to delete it")


class AC9_PT_ToolInterlining(_ToolTab, bpy.types.Panel):
    """The interlining picked in Lists > Fabrics (user 2026-10-25): its preset, what it adds, its pieces; Edit...
    (the same dialog as the pencil of AC9 Piece's Interlining field), Select Pieces, Delete."""
    bl_label = "AC9 Interlining"

    @classmethod
    def poll(cls, context):
        return _base_poll(context) and resolve(context, store.get(context)[0]) == 'BOND'

    def draw(self, context):
        from .core import fabric as FA
        layout = self.layout
        spec, _ = store.get(context)
        iid = focused_item(spec, 'BOND')
        if iid is None:
            return
        i = FA.interlining_index(spec)[iid]
        layout.label(text=i['name'], icon='MOD_SOLIDIFY')
        sp = layout.row().split(factor=0.4)
        r = sp.row()
        r.alignment = 'RIGHT'
        r.label(text="Preset")
        p = FA.preset('interlinings', i.get('preset') or '')
        op = sp.operator_menu_enum("ac9_cloth_sim.interlining_preset", "preset", text=tx(p['name']) if p else tx("Custom"))
        if op is not None:
            op.interlining_id = iid
        r = layout.row()
        r.active = False
        r.label(text=tx("+{0:g} g/m², +{1:g} µN·m to the fabric", float(i['weight_gsm']), float(i['bending_uNm'])))
        layout.operator("ac9_cloth_sim.interlining_edit", text="Edit...", icon='GREASEPENCIL').interlining_id = iid
        us = U.interlining_users(spec).get(iid, [])
        layout.separator()
        for ln in U.wrap(tx("Pieces ({0}): {1}", len(us), ', '.join(us)) if us else tx("Pieces: none"), 40)[:4]:
            layout.label(text=ln)
        if not us:
            r = layout.row()
            r.active = False
            r.label(text="Give it to pieces in AC9 Piece > Interlining")
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = bool(us)
        sub.operator("ac9_cloth_sim.interlining_select", text="Select Pieces",
                     icon='RESTRICT_SELECT_OFF').interlining_id = iid
        row.operator("ac9_cloth_sim.interlining_remove", text="Delete", icon='X').interlining_id = iid


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
        if a is None or a.type != 'PROPERTIES' or _focus['kind'] != 'MEASURE':
            return False
        from . import measure_edit as MEE
        return MEE.selected(context) is not None

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
    for c in CLASSES:
        bpy.utils.register_class(c)
    for k, v in PROPS.items():
        setattr(bpy.types.Scene, k, v)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    for hs, n in ((bpy.app.handlers.depsgraph_update_post, '_on_depsgraph'), (bpy.app.handlers.load_post, '_on_load')):
        for h in [h for h in hs if getattr(h, '__name__', '') == n and getattr(h, '__module__', '') == __name__]:
            hs.remove(h)
    for k in PROPS:
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
