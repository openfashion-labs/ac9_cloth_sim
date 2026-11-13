# SPDX-License-Identifier: GPL-3.0-or-later
import os
from .i18n import CTX, tx, wrap
from . import units as U

import bpy

from . import store
from . import selection as SEL


class AC9_UL_SimSeams(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # the seam's color on the pattern (a click on it changes nothing: properties.py puts it back), its id, the
        # pieces (their names when they have one), the lengths and their difference. Widths fixed for the default
        # sidebar (~270 px): the pieces take what is left
        row = layout.row(align=True)
        sw = row.row(align=True)
        sw.ui_units_x = 0.9
        sw.prop(item, "color", text="")
        if item.in_group:                             # a later stretch of an M:N seam: under the first
            row.label(text="", icon='BLANK1')
        # the id is in the box under the list (and the filter finds it): the row has no room for it at the default
        # width once the pieces are named
        # a button, not a label: the list's own row click cannot see Shift (user 2026-10-23, every list picks alike)
        nm = row.row(align=True)
        nm.alignment = 'LEFT'                         # the text where a label's is; the rest of the row picks too
        op = nm.operator("ac9_cloth_sim.seam_list_pick", text=item.label, emboss=False,
                          icon='LAYER_ACTIVE' if item.seam_id in store.seam_more else (
                              'CHECKBOX_DEHLT' if not item.enabled else (
                                  'ERROR' if item.check else ('GREASEPENCIL' if item.source == 'user' else 'LINKED'))))
        op.seam = item.seam_id
        d = item.len_a - item.len_b
        sub = row.row(align=True)
        sub.ui_units_x = 3.4
        sub.alignment = 'RIGHT'
        # in the scene's unit, one digit fewer than the pattern's boxes (mm 0, cm 1, in 2): the row is narrow
        sub.label(text=f"{U.num(context, item.len_a, body=True)}/{U.num(context, item.len_b, body=True)}"
                  + (f" {U.num(context, d, signed=True, body=True)}" if abs(d) >= 1 else ""))

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt = [self.bitflag_filter_item] * len(items)
        if self.filter_name:                          # by seam id or piece name / label
            f = self.filter_name.lower()
            flt = [self.bitflag_filter_item if (f in it.label.lower() or f in it.seam_id.lower()) else 0
                   for it in items]
        if getattr(data, 'seams_selected_only', False):
            # only the seams on the selected pieces (user 2026-10-21, off by default: a seam that vanished from the
            # list would read as deleted)
            from . import pattern_view as PV
            names = set(SEL.pieces(context))
            spec = store.get(context)[0]
            on = {sm['id'] for sm in (spec or {}).get('seams', [])
                  if sm['a']['piece'] in names or sm['b']['piece'] in names}
            flt = [f if it.seam_id in on else 0 for f, it in zip(flt, items)]
        return flt, []


def view_text_origin(context, top=150):
    """(x, y) for lines of text drawn at a 3D view's upper left, clear of its toolbar and of the view's own text.
    With Region Overlap (Blender's default) the toolbar lies over the view: its width is added (1, 2 columns or
    with names); without it the view starts after the toolbar. The statistics overlay, when shown, pushes the text
    down below it. top: px from the region's top at UI scale 1."""
    area, region = context.area, context.region
    scale = context.preferences.system.ui_scale
    x = 0
    if area is not None and context.preferences.system.use_region_overlap:
        for r in area.regions:
            if r.type == 'TOOLS' and r.width > 1:    # a hidden toolbar keeps a width of 1
                x = r.width
    y = region.height - top * scale
    ov = getattr(context.space_data, 'overlay', None)
    if ov is not None and ov.show_overlays and ov.show_stats:
        y -= 110 * scale
    return int(x + 24 * scale), int(y)


# the steps of a run's own preparation, as the bar names them
PREP_PHASES = {'imports': "Loading the solver", 'device': "Starting the GPU", 'model': "Building the cloth",
               'kernels': "Preparing the compute programs"}


def draw_solver_preload(layout, context):
    """A cached preload snapshot; the timer owns all I/O and compatibility checks. One line at most: a bar while
    it loads, a line when it failed or waits for the GPU, nothing once ready (the usual state is not news)."""
    from . import dress as D
    shown = D.solver_preload_status()
    state = shown['state']
    if state == 'loading':
        done, total = shown['completed'], shown['total']
        layout.progress(factor=done / total, type='BAR', text=tx('Preparing the solver {0:.0f} %',
                                                                 done * 100 / total))
    elif state == 'failed':
        layout.label(text=tx('Solver preload failed (Sim still starts)'), icon='ERROR')
    elif state == 'gpu_wait':
        layout.label(text=tx('Waiting for GPU memory'), icon='INFO')


def draw_run(layout, context, status=True):
    """The run buttons, one place (user 2026-10-14, REVIEW_fable_run_buttons_2026-10-14): Simulate (dresses in
    stages and stops; goes on from the result) and Live (from the shape on screen until stopped, Ctrl+drag pulls),
    then Reset (back to the placed shapes: the selected pieces, or the whole garment) and Fix by Hand / Back to
    Frames. While a run goes the button that started it reads Stop (pressing it again stops as Esc does) and the
    others are off. Drawn by the Tool tab's AC9 Run (tool_tab.py; user 2026-10-20: the only place in the new
    layout). status: the run's state under the buttons (draw_run_status)."""
    from . import dress as D
    run = D.AC9_OT_SimDress.running()
    btn = D.AC9_OT_SimDress.running_button()
    p = context.scene.ac9_cloth_sim
    # Phase Sim, Local Sim and the Native 3 choice only with Experimental Features on (user 2026-11-12, 0.2.0:
    # Live Sim at the center for a first user); a run started elsewhere still shows its Stop
    exp = D.experimental(context)
    if exp or btn == 'SIMULATE':
        sub = layout.row(align=True)
        sub.enabled = run is None or btn == 'SIMULATE'
        # the running one drawn pressed, as the tool buttons (user 2026-10-21)
        sub.operator("ac9_cloth_sim.update_3d", text="Stop" if btn == 'SIMULATE' else "Phase Sim",
                     icon='PAUSE' if btn == 'SIMULATE' else 'PLAY', depress=btn == 'SIMULATE')
    # Live Sim and Local Sim side by side, each with its own Gravity under it (user 2026-10-23: Local starts
    # without it)
    modes = [('LIVE', "Live Sim", 'HAND')]
    if exp or btn == 'LOCAL':
        modes.append(('LOCAL', "Local Sim", 'PIVOT_INDIVIDUAL'))
    col = layout.column(align=True)
    row = col.row(align=True)
    for mode, text, icon in modes:
        sub = row.row(align=True)
        sub.enabled = run is None or btn == mode
        op = sub.operator("ac9_cloth_sim.dress", text="Stop" if btn == mode else text,
                          icon='PAUSE' if btn == mode else icon, depress=btn == mode)
        op.mode, op.button = mode, mode
    row = col.row(align=True)
    row.prop(p, "live_gravity", text="Gravity")
    if len(modes) > 1:
        row.prop(p, "local_gravity", text="Gravity")
    # the floor at Z = 0 for every run (user 2026-11-10): beside the runs it changes, off while one runs (read at
    # its start)
    row = layout.row()
    row.enabled = run is None
    row.prop(p, "floor", text="Floor (Z = 0)")
    # Native 3 (on by default, user 2026-11-12) can be turned off under Experimental Features; Fast Live is not
    # drawn anywhere (a value saved on in a file is not used, core/presets.resolve)
    if exp:
        n3 = layout.column(align=True)
        n3.enabled = run is None
        n3.prop(p, "native_three", text="Native 3")
    # pattern edits to the 3D at once, or when asked (user 2026-10-27, TASKS 85): under the run buttons, where the
    # runs that take the waiting edits are
    # always both, side by side (user 2026-10-28: no button that shows up only now and then): the switch as a button
    # held down while on, Update 3D gray until it is off and edits wait (its poll), then red (a run takes them too)
    row = layout.row(align=True)
    row.prop(p, "follow_edits", toggle=True, icon='LINKED' if p.follow_edits else 'UNLINKED')
    sub = row.row(align=True)
    sub.alert = p.sync_pending
    sub.operator("ac9_cloth_sim.sync_3d", icon='FILE_REFRESH')
    # Back to Placed (the old Reset) is with the other "Back to" in Placement (user 2026-10-22)
    spec = store.get(context)[0]
    g = bpy.data.objects.get(D.garment_name(spec)) if spec is not None else None
    draw_fix_by_hand(layout, context, g, enabled=run is None)
    # pieces a Reset put back: what the next Simulate does with them (an id property, cheap to read in a draw)
    n = len(D.reset_pieces(g))
    if n and run is None:
        layout.label(text=tx("Next: {0} piece(s) from placed", n), icon='INFO')
    # the solver's preload: a bar while it loads, nothing once ready (2026-11-04: three lines at rest were too many).
    # Not while a run goes: the preload's progress is not updated then and its last share stayed on screen ("25 %"
    # through a whole Sim); the run's own state is under the buttons
    if run is None:
        draw_solver_preload(layout, context)
    # the stroke brush (Ctrl+Shift+drag while Live runs, user 2026-11-02): set once before a run, so folded; always
    # there (no button that shows up only now and then, 10/28)
    head, body = layout.panel("ac9_cloth_sim_run_live", default_closed=True)
    head.label(text="Live Options")
    if body is not None:
        row = body.row(align=True)
        row.prop(context.scene.ac9_cloth_sim, "stroke_radius_len", text="Smooth")
        row.prop(context.scene.ac9_cloth_sim, "stroke_strength", text="Strength")
    if status and spec is not None:
        draw_run_status(layout, context, spec)


def draw_cache_row(layout, context):
    """Delete Cache and Keep Cache (the frames on the timeline: deleted now, or not kept after each run, user
    2026-10-22). Under Details: not touched every run, and Delete Cache asks first."""
    from . import dress as D
    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.enabled = D.AC9_OT_SimDress.running() is None
    sub.operator("ac9_cloth_sim.delete_cache", text="Delete Cache", icon='TRASH')
    prefs = D.addon_prefs(context)
    if prefs is not None:
        row.prop(prefs, "keep_cache", text="Keep Cache")


def draw_fix_by_hand(layout, context, g, enabled=True):
    """Fix by Hand (Sculpt Mode) and its Edit Mode button, only while the garment's frames play: with no frames its
    mesh is its shape and Sculpt works on it as it is (REVIEW_fable_edit_frame_ux_2026-11-02 A, D). Red while the
    garment is in Sculpt / Edit Mode with its frames on (B: the hand edits do not show then)."""
    from . import dress as D
    if g is None or not any(m.type == 'MESH_CACHE' and m.show_viewport for m in g.modifiers):
        return
    row = layout.row(align=True)
    row.enabled = enabled
    row.alert = D.frames_hide_edits(context)
    row.operator("ac9_cloth_sim.edit_frame", text="Fix by Hand", icon='SCULPTMODE_HLT').mode = 'SCULPT'
    row.operator("ac9_cloth_sim.edit_frame", text="", icon='EDITMODE_HLT').mode = 'EDIT'


def _wrap_width(context):
    """Characters per line at the region's width (the sidebar ~270 px: 38)."""
    r = context.region
    return max(18, int(r.width / (7.0 * context.preferences.system.ui_scale))) if r else 40


def draw_run_status(layout, context, spec):
    """Under the run buttons (user 2026-10-20, the Dress panel gone): the pieces not placed, the run's share done
    as a bar, the status line; folded under Details the log's last lines, Log and To Cloth Retopo."""
    import textwrap
    from . import dress as D
    from .core import spec as S
    props = context.scene.ac9_cloth_sim
    on = [pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)]
    placed = set(spec.get('placement', {}))
    for a, b in S.placement_pairs(spec):
        if a in placed or b in placed:
            placed.update((a, b))
    unplaced = [nm for nm in on if nm not in placed]
    if unplaced:
        layout.label(text=tx("Skipping {0} unplaced", len(unplaced)), icon='INFO')
    run = D.AC9_OT_SimDress.running()
    # P1 #7: the run's share done as a bar (the status below says the stage and frame)
    shown = D.AC9_OT_SimDress._shown if run else {}
    # while it starts: a bar only from the run's own preparation, fresh (dress.run_preparing); words otherwise
    prep = D.run_preparing() if run else None
    built = D.run_kernels_built() if prep is not None else 0
    if built:
        # a first run on this PC (or after an update): the modules built so far move the bar, which otherwise held
        # at one step for the whole wait
        from .core import kernel_cache as KC
        layout.progress(factor=min(built / KC.EXPECTED_MODULES, 0.97), type='BAR',
                        text=tx("Building the compute programs for this PC ({0})", built))
    elif prep is not None and prep[1] is not None:
        done, total, phase = prep[1]
        layout.progress(factor=done / total, type='BAR',
                        text=tx("{0} {1:.0f} %", tx(PREP_PHASES.get(phase, PREP_PHASES['imports'])), done * 100 / total))
    if shown.get('frac') is not None and not shown.get('starting'):
        layout.progress(factor=shown['frac'], type='BAR', text=tx("{0:.0f} % of the run", shown['frac'] * 100))
    n = _wrap_width(context)
    if props.dress_status:
        # wrapped to the region width: an anomaly stop reads "stage 3: anomaly at the end: ... (13 stitches)"
        col = layout.column(align=True)
        for line in wrap(props.dress_status, n):
            col.label(text=line)
    head, body = layout.panel("ac9_cloth_sim_run_details", default_closed=True)
    head.label(text="Details")
    if body is None:
        return
    # the simulation log's last lines (the mesh each stage ran on, a shape carried from another mesh)
    if props.log_tail:
        col = body.box().column(align=True)
        for entry in props.log_tail.split(chr(10))[-3:]:
            for i, line in enumerate(wrap(entry, n)[:2]):
                col.label(text=line if i == 0 else '  ' + line)
    col = body.column(align=True)        # one a row: "To AC9 Cloth Retopo" does not fit beside Log
    col.operator("ac9_cloth_sim.log", text=tx("Log"), icon='TEXT')
    col.operator("ac9_cloth_sim.export_retopo", text="To AC9 Cloth Retopo", icon='EXPORT')
    draw_cache_row(body, context)


class AC9_PT_SimMissing(bpy.types.Panel):
    """The deleted piece objects, above every panel of the tab and only while there are some: no header to fold it
    away (Setup is often folded)."""
    # user 2026-10-27
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Deleted Pieces"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        from . import draft_tool as DT
        return bool(DT.missing_pieces(context, store.get(context)[0]))

    def draw(self, context):
        draw_missing_pieces(self.layout, context, store.get(context)[0])


class AC9_PT_SimMain(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Setup"

    def draw(self, context):
        layout = self.layout
        props = context.scene.ac9_cloth_sim
        row = layout.row(align=True)
        row.prop(props, "pattern_tools", icon='TOOL_SETTINGS')         # the pattern tools in the toolbar (toolbar.py)
        row.operator("ac9_cloth_sim.work_screen", icon='WORKSPACE')    # the workspace (work_screen.py)
        warning = context.screen.get('ac9_view_warning', '') if context.screen else ''
        if warning:
            box = layout.box()
            for entry in warning.splitlines():
                for i, line in enumerate(wrap(entry, _wrap_width(context))):
                    box.label(text=line, icon='ERROR' if i == 0 else 'NONE')
        draw_units(layout, context)
        # the garment's files (user 2026-10-21: New Garment and the DXF in and out are Setup's; SVG in 2026-10-31)
        layout.operator("ac9_cloth_sim.new_pattern", icon='FILE_NEW')
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.import_dxf", icon='IMPORT')
        row.operator("ac9_cloth_sim.export_dxf", icon='EXPORT')
        layout.operator("ac9_cloth_sim.import_svg", icon='IMPORT')
        # The two BOM actions have full-width labels: a truncated "new" concealed the destructive import.
        layout.operator("ac9_cloth_sim.import_bom", icon='IMPORT')
        layout.operator("ac9_cloth_sim.from_mesh", icon='MESH_DATA')
        layout.operator("ac9_cloth_sim.add_avatar", icon='OUTLINER_OB_ARMATURE')   # the bundled CC0 avatar (work_screen.py)
        spec, _ = store.get(context)
        if store.text(context) is not None and spec is None:
            box = layout.box()
            box.alert = True
            box.label(text="The spec text is not valid JSON", icon='ERROR')
            box.label(text=(store.error() or '')[:80])
            layout.operator("ac9_cloth_sim.refresh", icon='FILE_REFRESH')
            return
        if spec is None:
            layout.label(text="No pattern yet", icon='INFO')
            return
        on = sum(1 for pc in spec['pieces'] if pc.get('enabled', True))
        col = layout.column(align=True)
        col.prop(props, "spec_text", text="Garment")
        col.label(text=tx("{0}: {1} / {2} pieces on", spec['name'], on, len(spec['pieces'])), icon='MESH_GRID')
        from .core import spec as S
        if S.from_bom(spec):
            col.operator("ac9_cloth_sim.seams_from_bom", icon='FILE_REFRESH')
        col.operator("ac9_cloth_sim.enable_all_pieces", text="All On", icon='CHECKBOX_HLT')
        draw_garments(layout, spec)
        draw_overlay_switch(layout.row(align=True), props)
        layout.operator("ac9_cloth_sim.refresh", icon='FILE_REFRESH')


def draw_units(layout, context):
    """Units: mm / cm / m / in, one pressed: every length on the screen in it (user 2026-11-11). The particle
    distance stays mm."""
    from . import units
    row = layout.row(align=True)
    row.label(text="Units")
    cur = units.current(context)
    for key, label, _sy, _lu in units.CHOICES:
        row.operator("ac9_cloth_sim.set_units", text=label, depress=(cur == key)).unit = key


def draw_garments(layout, spec):
    """One row per garment of 2 pieces or more: its name and size (click: select its pieces, Shift: add them), a
    rename button; the pieces alone in one row. Orange: split by the layout, not sure (user 2026-10-25, DECISIONS
    cloth-sim-bom-seams-per-garment-2026-10-25)."""
    from .core import bom_to_spec as BS
    G = BS.garment_pieces(spec)
    if len(G) < 2:
        return
    info = {g['id']: g for g in spec.get('garments', [])}
    col = layout.column(align=True)
    alone = 0
    for gid, names in G.items():
        if len(names) == 1:
            alone += 1
            continue
        g = info.get(gid, {})
        row = col.row(align=True)
        row.alert = bool(g.get('check'))
        on = sum(1 for pc in spec['pieces'] if pc['name'] in set(names) and pc.get('enabled', True))
        op = row.operator("ac9_cloth_sim.select_garment", icon='STICKY_UVS_LOC',
                          text=f"{g.get('name', gid) or tx('(no garment)')} ({on}/{len(names)})")
        op.garment = gid
        row.operator("ac9_cloth_sim.rename_garment", text="", icon='GREASEPENCIL').garment = gid
    if alone:
        col.operator("ac9_cloth_sim.select_garment", icon='STICKY_UVS_DISABLE',
                     text=tx("Pieces alone ({0})", alone)).garment = BS.ALONE
    # fix a wrong split: select the pieces that belong apart and make them a garment (user 2026-10-30)
    col.operator("ac9_cloth_sim.garment_from_selection", icon='ADD')


_twin_cache = {'key': None, 'apart': []}


def twins_apart(spec):
    """Twin pairs whose outlines are no longer mirror images: pattern_build then meshes them apart (quietly)."""
    if _twin_cache['key'] != store._cache['key']:
        from .core import draft as D
        from .core import spec as S
        apart = []
        for a, b in S.twins(spec):
            try:
                if D.twin_corner_map(spec, a)[1] is None:
                    apart.append((a, b))
            except Exception:
                apart.append((a, b))
        _twin_cache.update(key=store._cache['key'], apart=apart)
    return _twin_cache['apart']


class AC9_PT_SimDraft(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Pattern"
    bl_translation_context = CTX

    def draw(self, context):
        layout = self.layout
        # the flat pattern view is the Work Screen's (user 2026-10-21: the Pattern View button is no longer needed);
        # New Garment and the DXF are in Setup
        row = layout.row(align=True)        # the length unit is Setup's (user 2026-11-11); the snapping stays here
        from . import shape_tool as ST
        ST.draw_snap_settings(context, row)
        # the toolbar's groups opened up, by what they make (user 2026-10-21); a tool's button shows pressed while it
        # is the active tool, and pressed again goes back to Select Box (user 2026-10-14 / 10-21)
        from . import toolbar as TB
        spec, _ = store.get(context)
        row = layout.row(align=True)        # the two pattern tools (user 2026-10-28, DECISIONS cloth-sim-transform-edit-split)
        TB.tool_button(row, context, "ac9_cloth_sim.transform_pattern_tool", "Whole Pieces", 'ORIENTATION_GLOBAL')
        TB.tool_button(row, context, "ac9_cloth_sim.edit_pattern_tool", "Edit Pieces", 'RESTRICT_SELECT_OFF')
        # Update BOM Seams is a labeled button in Setup; manual regrouping remains available through F3.
        for title, tools in (
                ("Piece", (("draw_piece_tool", "Draw", 'MESH_DATA'), ("rect_tool", "Rectangle", 'MESH_PLANE'),
                           ("ellipse_tool", "Ellipse", 'MESH_CIRCLE'))),
                ("Internal", (("draw_line_tool", "Line", 'IPO_LINEAR'), ("internal_rect_tool", "Rectangle", 'MESH_PLANE'),
                              ("internal_ellipse_tool", "Ellipse", 'MESH_CIRCLE'))),
                ("Seams", (("edit_seams_tool", "Edit Seams", 'RESTRICT_SELECT_OFF'), ("add_seam_tool", "Add Seam", 'ADD'),
                           ("add_seam_mn_tool", "Add M:N", 'ADD')))):
            col = layout.column(align=True)
            row = col.row()
            row.label(text=tx(title))
            for i in range(0, len(tools), 2):
                row = col.row(align=True)
                for tid, text, icon in tools[i:i + 2]:
                    TB.tool_button(row, context, "ac9_cloth_sim." + tid, tx(text), icon)
        if spec is not None:                            # buttons (user 2026-10-22: out of their own panel)
            from . import button_ui as BU
            BU.draw_tools(layout, context, spec)
        # what is done to the selected pieces (the Tool tab's AC9 Piece holds their settings; user 2026-10-21)
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Selected Pieces")
        row = col.row(align=True)
        row.operator("ac9_cloth_sim.mirror_copy", text="Mirror Copy", icon='MOD_MIRROR')
        # (Mirror to Pair, a twin made the mirror image of the other again, was taken out: deleting one and Mirror
        # Copy of the other does it, user 2026-11-11)
        # the name: the Tool tab's AC9 Piece field and F2 (user OK 2026-10-25: one place to look, not three)
        row = col.row(align=True)
        row.operator("ac9_cloth_sim.duplicate_pieces", text="Duplicate", icon='DUPLICATE')     # user 2026-10-26
        row.operator("ac9_cloth_sim.delete_piece", text="Delete", icon='TRASH')
        # Mirror 3D to Pair (the dressed shape of one side given to the other) is in the Tool tab's AC9 Piece now: it
        # sat with the 2D tools here and read as one of them (user 2026-11-11, REVIEW_fable_flat_turn_snap_mirror_ui §3)
        # Symmetric Edit is a piece's setting: the Tool tab's AC9 Piece (user 2026-10-21)
        apart = twins_apart(spec) if spec else []
        if apart:
            box = layout.box()
            box.alert = True
            box.label(text=tx("{0} left / right pair(s) no longer mirror images", len(apart)), icon='ERROR')
            for a, b in apart[:3]:
                box.label(text=tx("    {0}  /  {1}", a, b))
            box.label(text="Delete one and Mirror Copy the other to make them match")
        draw_keys(layout, context)
        # Simulate is in the Run panel (user 2026-10-14: one place for the run buttons)


def draw_keys(layout, context):
    """Every key of Edit Pieces, closed until opened: the tool's header shows only the keys for what is under the
    cursor (user 2026-10-23, Fable review REVIEW_fable_tool_hints_2026-10-23)."""
    from . import draft_tool as DT
    head, body = layout.panel("ac9_cloth_sim_pattern_keys", default_closed=True)
    head.label(text="Keys", icon='EVENT_K')
    if body is None:
        return
    n = max(12, _wrap_width(context) - 12)
    col = body.column(align=True)
    for title, keys in (("Whole Pieces", DT.TRANSFORM_KEYS), ("Edit Pieces", DT.ALL_KEYS)):
        col.label(text=tx(title))
        for key, what in keys:
            for i, ln in enumerate(wrap(tx(what), n)):
                sp = col.split(factor=0.3, align=True)
                sp.label(text=(tx(key) if key in ("click", "drag") else key) if i == 0 else "")   # a letter as is
                sp.label(text=ln)


def draw_seam_list(layout, context, spec):
    """The seam count and the list. A list only shows and picks: Edit Seams / Add Seam and Seams from BOM are in the
    Pattern panel (user 2026-10-21)."""
    props = context.scene.ac9_cloth_sim
    n_flag = sum(1 for sm in spec['seams'] if sm.get('check'))
    row = layout.row()
    row.label(text=tx("{0} seams", len(spec['seams'])) + (tx(", {0} to check", n_flag) if n_flag else ""))
    row.prop(props, "seams_selected_only", text="", icon='RESTRICT_SELECT_OFF')
    layout.template_list("AC9_UL_SimSeams", "", props, "seams", props, "seam_index", rows=8)


def draw_seam_attrs(layout, context, spec, act, viewport=True):
    """The selected seam: its sides, lengths, Match length, Flip, On/Off, Confirm, Delete. viewport: drawn in a 3D
    view (the sidebar), where Move A / B can pick the new place; the Properties editor has no viewport to click in"""
    props = context.scene.ac9_cloth_sim
    from .core import seam_edit as SE
    sel = store.selected_seams(context, spec)
    if len(sel) > 1:                # several picked (Shift+click): only what acts on all of them
        layout.label(text=tx("{0} seams selected", len(sel)), icon='RESTRICT_SELECT_OFF')
        layout.label(text=", ".join(sm['id'] for sm in sel[:6]) + (" ..." if len(sel) > 6 else ""))
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.seam_edit", text="On" if not act.get('enabled', True) else "Off",
                     icon='CHECKBOX_HLT').action = 'TOGGLE'
        row.operator("ac9_cloth_sim.seam_edit", text="Delete", icon='TRASH').action = 'DELETE'
        layout.label(text="Shift+click: add / take out", icon='INFO')
        return
    n_st = len(SE.stretches(spec, act))
    layout.label(text=tx("Seam {0}", act['id']) + (tx("  (M:N, {0} stretches)", n_st) if n_st > 1 else ""))
    if act.get('check'):
        # wrapped to the region's width: a check reads "shared line: other pieces use the same line number"
        import textwrap
        n = max(18, int(context.region.width / (7.0 * context.preferences.system.ui_scale))) if context.region else 40
        col = layout.column(align=True)
        col.alert = True
        for i, ln in enumerate(wrap(act['check'], n)[:4]):
            col.label(text=ln, icon='ERROR' if i == 0 else 'BLANK1')
    dup = SE.duplicates(spec, act)
    if dup:
        # the same two lines sewn by another seam too (user 2026-10-14: an underarm carried twice, its copy drawn
        # straight over the round one)
        col = layout.column(align=True)
        col.alert = True
        col.label(text=tx("Same lines as {0}", ', '.join(dup[:3])), icon='ERROR')
        col.label(text="(a duplicate: delete one)", icon='BLANK1')
    it = props.seams[props.seam_index]
    lab = {pc['name']: pc.get('label') for pc in spec['pieces']}
    for k, L in (('a', it.len_a), ('b', it.len_b)):
        sd = act[k]
        # name first, id in brackets, like the seams list (2026-10-25)
        nm = f"{lab[sd['piece']]} ({sd['piece']})" if lab.get(sd['piece']) else sd['piece']
        if 'line' in sd:
            txt = tx("{0}: {1}  internal {2}  {3:.2f} > {4:.2f}", k.upper(), nm, sd['line'], sd['from'], sd['to'])
        else:
            arrow = "ccw" if sd['dir'] > 0 else "cw"
            txt = (f"{k.upper()}: {nm}  {sd['from'][0]} {sd['from'][1]:.2f} > "
                   f"{sd['to'][0]} {sd['to'][1]:.2f} ({arrow})")
        layout.label(text=txt)
        r = layout.row(align=True)
        r.label(text="    " + U.fmt(context, L))
        if viewport:
            r.operator("ac9_cloth_sim.seam_move_side", text=tx("Move {0}", k.upper()), icon='FORWARD').side = k
    _seam_buttons(layout, spec, act)
    if act['a']['piece'] == act['b']['piece']:
        # a closure (a stand's, a cuff's ends buttoned over each other): which end goes on top on its placed ring
        # (user 2026-11-02/03, DECISIONS cloth-sim-closure-over-2026-11-02)
        r = layout.row(align=True)
        r.label(text=tx("Over"))
        r.prop(context.scene, "ac9_seam_over", expand=True)
    from . import dart_ui
    dart_ui.draw_dart_rows(layout, context, spec, act, viewport)
    # the angle the two sides keep across the seam while simulated (user 2026-10-23), as on internal lines; on
    # every seam alike, a dart's or a one-piece sleeve's too (user 2026-10-23)
    f = layout.column()
    f.use_property_split = True
    f.use_property_decorate = False
    f.prop(context.scene, "ac9_seam_fold", text="Fold (deg)")
    if abs(context.scene.ac9_seam_fold) > 1e-6 and act['a']['piece'] != act['b']['piece']:
        # the piece that turns on the placed shape (user 2026-10-24: was the Fold button's Tab)
        f.row(align=True).prop(context.scene, "ac9_seam_fold_turn", expand=True)
        from . import place_fold as PF
        PF.draw_why(f, context, PF.seam_turn(spec, act), PF.SEAM_KEY + act['id'])
    # how the seam is pulled (a test feature, the no-gravity stage of Phase Sim only): Experimental Features
    from . import dress as _D
    if _D.experimental(context):
        _seam_path_rows(layout, spec, act)
    from .core import attach as _AT
    if not _AT.ON_HOLD:
        _seam_attach_rows(layout, spec, act)
    if not viewport:
        layout.label(text="Move a side: Edit Seams tool on the pattern", icon='INFO')


def _sfold_get(self):
    spec = store.get(bpy.context)[0]
    act = store.active_seam(bpy.context, spec) if spec else None
    if act is None:
        return 0.0
    return float(next((f['deg'] for f in spec.get('folds', []) if f.get('seam') == act['id']), 0.0))


def _sfold_set(self, v):
    """A seam's fold in the spec ({'seam', 'deg'}, 0 removes it): core/recipe.seam_fold_deg gives the solver the
    rest angle of the hinges across it (per seam)."""
    context = bpy.context
    spec = store.get(context)[0]
    act = store.active_seam(context, spec) if spec else None
    if act is None or abs(v - _sfold_get(self)) < 1e-6:
        return
    from .core import spec as S
    from . import overlay
    run = S.seam_run(spec, act)          # the seams continuing it in line take the same angle (the yoke's halves)
    old = {f['seam']: f for f in spec.get('folds', []) if f.get('seam') in run}
    folds = [f for f in spec.get('folds', []) if f.get('seam') not in run]
    if abs(v) > 1e-6:
        folds += [dict(old.get(r, {}), seam=r, deg=round(float(v), 2)) for r in run]
    store.put(context, dict(spec, folds=folds), keep=True, seams=False)       # only the folds change
    # folded on the placed shape already: folded again now, the same piece turning (user 2026-10-23)
    from . import place_fold as PF
    PF.refold_seam(context, act['id'], float(v))
    overlay.invalidate()


def _sover_get(self):
    spec = store.get(bpy.context)[0]
    act = store.active_seam(bpy.context, spec) if spec else None
    return {'a': 1, 'b': 2}.get((act or {}).get('over'), 0)


def _sover_set(self, v):
    """The seam's 'over' in the spec ('a' / 'b', none removes it): closure.py puts that end on top."""
    context = bpy.context
    spec = store.get(context)[0]
    act = store.active_seam(context, spec) if spec else None
    if act is None or v == _sover_get(self):
        return
    seams = [dict({k: x for k, x in sm.items() if k != 'over'}, **({'over': 'ab'[v - 1]} if v else {}))
             if sm['id'] == act['id'] else sm for sm in spec['seams']]
    store.put(context, dict(spec, seams=seams), seams=False)


def _sturn_items(self, context):
    spec = store.get(context)[0]
    act = store.active_seam(context, spec) if spec else None
    if act is None:
        return [('A', "A", ""), ('B', "B", "")]
    return [('A', act['a']['piece'][:16], "This piece turns over the seam on the placed shape"),
            ('B', act['b']['piece'][:16], "This piece turns over the seam on the placed shape")]


def _sturn_get(self):
    spec = store.get(bpy.context)[0]
    act = store.active_seam(bpy.context, spec) if spec else None
    if act is None:
        return 0
    from . import place_fold as PF
    return 1 if PF.seam_turn(spec, act) == act['b']['piece'] else 0


def _sturn_set(self, v):
    context = bpy.context
    spec = store.get(context)[0]
    act = store.active_seam(context, spec) if spec else None
    if act is None or v == _sturn_get(self):
        return
    from . import place_fold as PF
    turn = act['b']['piece'] if v == 1 else act['a']['piece']
    PF.write_angle(context, turn, PF.SEAM_KEY + act['id'], _sfold_get(self))
    PF.refold_seam(context, act['id'], _sfold_get(self), turn)
    from . import overlay
    overlay.invalidate()


def _seam_path_rows(box, spec, act):
    """How the seam is pulled together (TEST, core/seam_path.py): Straight / Round with the side, and the part"""
    from .core import seam_path as SPATH
    pth = act.get('path')
    row = box.row(align=True)
    row.label(text=tx("Pull"))
    side = (pth or {}).get('side', 'auto')
    txt = tx("Straight") if not pth else tx("Round: {0}", side.capitalize())
    row.operator_menu_enum("ac9_cloth_sim.seam_path", "side", text=txt)
    if pth:
        gname, _g = SPATH.guide_of(spec, act)
        row = box.row(align=True)
        row.label(text="Part")
        row.operator_menu_enum("ac9_cloth_sim.seam_path_part", "guide", text=gname or "none")
        if gname is None:
            box.label(text="No body part: pulled straight", icon='ERROR')


def _seam_attach_rows(box, spec, act):
    """The tape on the body the seam is held to while simulated (core/attach.py): its name, and for how long"""
    att = next((a for a in spec.get('attach', []) if str(a.get('seam')) == str(act['id'])), None)
    row = box.row(align=True)
    row.label(text="Tape")
    from .measure_tool import LABEL
    row.operator_menu_enum("ac9_cloth_sim.seam_attach", "tape", text=tx(LABEL.get(att['tape'], att['tape'])) if att else "None")
    if att:
        row = box.row(align=True)
        row.label(text=tx("Hold"))
        row.operator_menu_enum("ac9_cloth_sim.seam_attach_until", "until",
                               text="While sewing" if att.get('until') == 'sewn' else "To the end")


def _seam_buttons(box, spec, act):
    # the side each button changes, as A / B: the rows above name each side's piece and the pattern carries the A / B
    # tags (user 2026-10-23; piece names cut short in the sidebar read alike, e.g. two Body_Front...)
    box.label(text="Fit the length of")
    row = box.row(align=True)
    row.operator("ac9_cloth_sim.match_length", text="A").side = 'a'
    row.operator("ac9_cloth_sim.match_length", text="B").side = 'b'
    box.label(text="Flip the direction of")
    row = box.row(align=True)
    row.operator("ac9_cloth_sim.seam_edit", text="A").action = 'FLIP_A'
    row.operator("ac9_cloth_sim.seam_edit", text="B").action = 'FLIP_B'
    row = box.row(align=True)
    row.operator("ac9_cloth_sim.seam_edit", text="On" if not act.get('enabled', True) else "Off",
                 icon='CHECKBOX_HLT' if act.get('enabled', True) else 'CHECKBOX_DEHLT').action = 'TOGGLE'
    if act.get('check'):
        row.operator("ac9_cloth_sim.seam_edit", text="Confirm", icon='CHECKMARK').action = 'CONFIRM'
    row.operator("ac9_cloth_sim.seam_edit", text="Delete", icon='TRASH').action = 'DELETE'


ROLE_ROWS = 24          # rows of the Piece Roles list (the uncertain ones first)


class AC9_PT_SimRoles(bpy.types.Panel):
    """What each piece is (core/roles.py): it decides the stage the piece is sewn in. Reads the spec only (no proposal
    in draw)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Piece Roles"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def draw(self, context):
        from .core import roles as RO
        layout = self.layout
        spec, _ = store.get(context)
        on = [p for p in spec['pieces'] if p.get('enabled', True)]
        set_ = [p for p in on if p.get('role_by')]
        ask = [p for p in on if p.get('role_ask')]
        g = (spec.get('garment') or {}).get('kind')
        layout.label(text=(tx("{0}: ", tx(RO.GARMENTS.get(g, g))) if g else "") + tx("{0} / {1} set, {2} to check", len(set_), len(on), len(ask)))
        layout.operator("ac9_cloth_sim.propose_roles", icon='LIGHT')
        col = layout.column(align=True)
        for p in sorted(on, key=lambda p: (not p.get('role_ask'), p['name']))[:ROLE_ROWS]:
            # name | role | start (the third column, user 2026-11-02; As Placed, the usual case, greyed): the name
            # keeps the wider share so it is not cut more than with two columns
            sp = col.row(align=True).split(factor=0.42, align=True)
            nm = p['name'] if len(p['name']) <= 14 else p['name'][:7] + '..' + p['name'][-5:]
            sp.label(text=nm, icon='ERROR' if p.get('role_ask') else
                     ('USER' if p.get('role_by') == 'user' else 'BLANK1'))
            sp = sp.split(factor=0.5, align=True)
            sp.label(text=tx(RO.EN.get(RO.role_of(p), "Other")) if p.get('role_by') or p.get('role') else "-")
            stt = RO.start_of(p)
            sub = sp.row(align=True)
            sub.active = stt != 'placed'
            sub.label(text=tx(RO.START_EN[stt]))
        if len(on) > ROLE_ROWS:
            col.label(text=tx("... {0} more", len(on) - ROLE_ROWS))


class AC9_PT_SimPlacement(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Placement"

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def draw(self, context):
        layout = self.layout
        props = context.scene.ac9_cloth_sim
        spec, _ = store.get(context)
        layout.prop(props, "body")
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.detect_guides", icon='MESH_CYLINDER')
        row.prop(props, "show_guides", text="", icon='HIDE_OFF')
        from . import guide_edit as GE
        row = layout.row(align=True)
        if GE.editing():
            row.alert = True
            row.operator("ac9_cloth_sim.edit_guides", text="Done", icon='CHECKMARK')
            # the cylinders' left / right mirror only while they are edited: it was taken for the pieces' (Fable
            # review 2026-10-14, a user placing a raglan)
            layout.prop(props, "guide_mirror", text="L/R together", toggle=True, icon='MOD_MIRROR')
        else:
            row.operator("ac9_cloth_sim.edit_guides", icon='OBJECT_ORIGIN')
        gd = spec.get('guides', {})
        if not gd:
            layout.label(text="Pick the body, then Detect Guides", icon='INFO')
            return
        on = [pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)]
        placed = [n for n in on if n in spec.get('placement', {})]
        layout.label(text=tx("{0} guides; {1} / {2} pieces placed", len(gd), len(placed), len(on)))
        # Place is the Place tool (its operator only works in the 3D view's own region); dragging with it slides the
        # pieces round their guide, which Adjust did (user 2026-10-15)
        row = layout.row(align=True)
        from . import toolbar as TB
        TB.tool_button(row, context, "ac9_cloth_sim.place_tool", "Place", 'PIVOT_CURSOR')
        # the Overlays' Stretch here too: the popover is easily missed, and a placed piece is where it shows first
        # (Fable review 2026-11-02 §2.2)
        row.prop(props, "show_stretch", text="", icon='MOD_SIMPLEDEFORM')
        # no Fold button (user 2026-10-24): the Tool tab's Fold Angle on a line, a seam's Fold (deg), fold the placed shape
        # a small piece carried onto the pieces it is sewn to, its shape kept as far as it goes (user 2026-10-22)
        row = layout.row(align=True)
        row.operator_context = 'INVOKE_DEFAULT'
        row.operator("ac9_cloth_sim.move_to_seam", text="Part to Seam", icon='SNAP_EDGE')
        # a narrow band laid on the edge it is sewn to, for Live (user 2026-10-22)
        row.operator("ac9_cloth_sim.lay_on_edge", text="Band on Edge", icon='MOD_CURVE')
        if show_hints(context):
            col = layout.column(align=True)
            col.label(text=tx("Choose targets, then Enter"), icon='INFO')
            col.label(text=tx("Shift-click: add/remove"), icon='BLANK1')
            col.label(text=tx("A: all targets; Esc: cancel"), icon='BLANK1')
        # the piece on top of the ones it overlaps, only when asked (user 2026-10-25)
        layout.operator("ac9_cloth_sim.move_outside", text="Move Outside", icon='SELECT_EXTEND')
        draw_hint(layout, context, tx("Pick pieces, press on a guide, drag, release, then Live Sim"))
        # the placed shapes as objects: move / rotate / edit them freely (the guide is only a start, user 2026-10-10)
        from . import placed_view as PLV
        nob = len({ob.name for ob in PLV.objects(spec).values()})
        from . import dress as D
        dressed = D.has_result(bpy.data.objects.get(D.garment_name(spec)))
        # before a Dress the placed shapes, after it a handle on the garment's chosen pieces (user 2026-10-22)
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.move_by_hand", text="Move by Hand", icon='MESH_GRID')
        # out of the body and off the other cloth, no physics; what is left red (user 2026-10-24)
        row.operator("ac9_cloth_sim.settle_placed", text="Settle", icon='MOD_SMOOTH')
        # the three steps back, the nearest first: the selected pieces, else all (user 2026-10-22)
        col = layout.column(align=True)
        col.operator("ac9_cloth_sim.reset", text="Back to Placed", icon='LOOP_BACK')
        col.operator("ac9_cloth_sim.reset_placed", text="Back to Guide", icon='LOOP_BACK')
        col.operator("ac9_cloth_sim.clear_placement", text="Back to Flat", icon='MESH_PLANE')
        if dressed:
            draw_hint(layout, context, tx("Simulated: pick pieces (2D or on the garment), Move by Hand"))
        elif nob:
            draw_hint(layout, context, tx("{0} shapes: move them freely", nob))
        elif placed:
            # Move by Hand is a side step: Simulate makes the same start shape without it (user 2026-10-15)
            draw_hint(layout, context, tx("Move by Hand: only to adjust"))
        ob = context.active_object
        if ob is not None and ob.get(PLV.PROP) and ob.get(PLV.GARMENT_PROP, '') == PLV.garment(spec):
            # the active placed piece's move from its placement (the spec's pose; G / R and the fields change it)
            p = PLV.pose_of(ob) or {'loc': [0.0] * 3, 'rot': [0.0] * 3}
            col = layout.column(align=True)
            col.label(text=ob[PLV.PROP] + (tx(" + {0}", ob[PLV.TWIN_PROP]) if ob.get(PLV.TWIN_PROP) else ""))
            col.label(text=tx("Move ") + "  ".join(U.num(context, v, signed=True) for v in p['loc']) + " " + U.unit(context)[1])
            col.label(text=tx("Turn ") + "  ".join(tx("{0:+.1f}", v) for v in p['rot']) + tx(" deg"))
            # Type Move (set_pose) is not drawn here (user 2026-11-12): Tool tab > AC9 Piece > Location & Rotation
        # the pair's state and buttons: Tool tab > AC9 Piece > Pair (user 2026-11-11, REVIEW_fable_pair_ui b-1)


def lr_state(spec, name):
    """(label lines, kind) of piece `name`'s left / right: 'twin' (drawn as a mirrored copy: placing one places the
    other), 'unlinked' (a twin set apart by Unlink), 'paired' (Pair), 'none'. Fable review 2026-10-14: the panel did
    not say which, and a user could not find how to place a raglan's sleeves as a pair."""
    from .core import spec as S
    pl = spec.get('placement', {})
    # the second line is a how-to (Show Hints): with hints off only the state shows, none with no pair
    for a, b in S.twins(spec):
        if name in (a, b):
            o = b if name == a else a
            if pl.get(a, {}).get('unlinked') or pl.get(b, {}).get('unlinked'):
                return ([tx("Set apart from {0}", o), tx("Link: place as a pair")], 'unlinked')
            return ([tx("Pair: {0} follows", o), tx("as its mirror image")], 'twin')
    for p in spec.get('pairs', []):
        if name in p:
            o = p[1] if name == p[0] else p[0]
            return ([tx("Paired with {0}", o), tx("Mirror Placement copies a move")], 'paired')
    return ([None, tx("No pair: select two pieces, then Pair")], 'none')


def show_hints(context):
    """The add-on preference Show Hints (the panels' how-to lines), on when it cannot be read."""
    from . import dress as D
    prefs = D.addon_prefs(context)
    return True if prefs is None else prefs.show_hints


def draw_hint(layout, context, text, icon='INFO'):
    """A how-to line, wrapped to the region, only with Show Hints on."""
    if not show_hints(context):
        return
    col = layout.column(align=True)
    for i, line in enumerate(wrap(text, max(12, _wrap_width(context) - 4))):
        col.label(text=line, icon=icon if i == 0 else 'BLANK1')


STATE_ICON = {'active': 'PLAY', 'freeze': 'FREEZE', 'off_pattern': 'GHOST_ENABLED', 'off_all': 'HIDE_ON'}
_gsum = {'gen': None, 'cur': None, 'rows': {}}


def garment_rows(context):
    """[(Text name, garment name, {state: count}, is current)] for every garment spec Text. The other garments'
    Texts are parsed only when the spec store changes (store.GEN), never on a plain redraw (a jacket's spec is 3.6 M
    characters, AGENTS: the sidebar redraws on every mouse move)."""
    from .core import spec as S
    cur = store.text(context)
    if _gsum['gen'] != store.GEN[0]:
        rows = {}
        for t in bpy.data.texts:
            if not t.name.startswith(store.TEXT_NAME) or t == cur:
                continue
            k = (t.as_pointer(), hash(t.as_string()))
            old = _gsum['rows'].get(t.name)
            if old is not None and old[0] == k:
                rows[t.name] = old
                continue
            try:
                sp = S.loads(t.as_string())
            except Exception:
                continue
            c = {s: 0 for s in S.PIECE_STATES}
            for pc in sp['pieces']:
                c[S.piece_state(pc)] += 1
            rows[t.name] = (k, sp['name'], c)
        _gsum.update(gen=store.GEN[0], rows=rows)
    out = []
    spec = store.get(context)[0]
    if cur is not None and spec is not None:
        c = {s: 0 for s in S.PIECE_STATES}
        for pc in spec['pieces']:
            c[S.piece_state(pc)] += 1
        out.append((cur.name, spec['name'], c, True))
    out += [(n, r[1], r[2], False) for n, r in sorted(_gsum['rows'].items())]
    return out


def draw_garment_rows(layout, context):
    """A row per garment: its name (the current one's dot filled), the state counts, three buttons for all its
    pieces."""
    rows = garment_rows(context)
    col = layout.column(align=True)
    for tname, gname, c, is_cur in rows:
        box = col.box()
        r = box.row(align=True)
        r.label(text=gname[:22], icon='RADIOBUT_ON' if is_cur else 'RADIOBUT_OFF')
        r = box.row(align=True)
        r.label(text=tx("{0} on  {1} fz  {2} off", c['active'], c['freeze'], c['off_pattern']))
        for st in ('active', 'freeze', 'off_pattern'):
            op = r.operator("ac9_cloth_sim.piece_state", text="", icon=STATE_ICON[st])
            op.state, op.scope, op.text = st, 'GARMENT', tname
    if len(rows) > 1:
        layout.label(text="Other garments: obstacles, not moved", icon='INFO')


def draw_selected_states(layout, current=None, title="Selected pieces:"):
    """Active / Freeze / Off Pattern / Off All for the selected pieces (current: the state shown pressed)."""
    if title:
        layout.label(text=title)
    g = layout.grid_flow(columns=2, align=True)
    for st, txt in (('active', "Active"), ('freeze', "Freeze"), ('off_pattern', "Off Pattern"),
                    ('off_all', "Off All")):
        op = g.operator("ac9_cloth_sim.piece_state", text=txt, icon=STATE_ICON[st], depress=(current == st))
        op.state, op.scope = st, 'SELECTED'


def draw_piece_list(layout, spec, select=False):
    """The current garment's pieces with their four state buttons. select: the name is a button that selects the
    piece in the viewport (the Properties editor then shows it)."""
    from .core import spec as S
    # the Off (All) pieces folded into one line: a BOM keeps every piece of the file (a jacket's 133, 8 on), and
    # 133 rows took 1.4 ms a redraw; opened, they list with their state buttons
    n_off = sum(1 for pc in spec['pieces'] if S.piece_state(pc) == 'off_all')
    # a piece object deleted with Blender's own X (the pattern still has it; user 2026-10-21): marked, Restore
    from . import draft_tool as DT
    from . import pattern_view as PV
    gone = set(DT.missing_pieces(bpy.context, spec))
    picked = set(SEL.pieces(bpy.context)) if select else set()
    col = layout.column(align=True)
    for pc in spec['pieces']:
        s = S.piece_state(pc)
        if s == 'off_all':
            continue
        def states(r, pc=pc, s=s):
            for st in S.PIECE_STATES:
                op = r.operator("ac9_cloth_sim.piece_state", text="", icon=STATE_ICON[st], depress=(s == st))
                op.state, op.scope, op.piece = st, 'PIECE', pc['name']
        if select:
            # the rows of every list (REVIEW_fable_lists_ui_2026-10-25 section 2): the name picks (pressed while the
            # piece is selected), the four states are the row's own toggles
            c = col.column(align=True)
            c.alert = pc['name'] in gone
            draw_pick_row(c, "ac9_cloth_sim.select_piece", (pc.get('label') or pc['name'])[:16], depress=pc['name'] in picked,
                          extra=states).piece = pc['name']
        else:
            r = col.row(align=True)
            r.alert = pc['name'] in gone
            r.label(text=(pc.get('label') or pc['name'])[:16])
            states(r)
    if n_off and hasattr(layout, 'panel'):
        # folded by default: a BOM keeps every piece of the file (a jacket's 125 unused ones); opened, each piece
        # has its state buttons like the rows above (user 2026-11-11: the old line said "click one" but was a label)
        head, body = layout.panel("ac9_cloth_sim_off_all_pieces", default_closed=True)
        head.label(text=tx("{0} Off All", n_off), icon=STATE_ICON['off_all'])
        if body is not None:
            bc = body.column(align=True)
            for pc in spec['pieces']:
                if S.piece_state(pc) != 'off_all':
                    continue
                r = bc.row(align=True)
                r.alert = pc['name'] in gone
                r.label(text=(pc.get('label') or pc['name'])[:16])
                for st in S.PIECE_STATES:
                    op = r.operator("ac9_cloth_sim.piece_state", text="", icon=STATE_ICON[st], depress=(st == 'off_all'))
                    op.state, op.scope, op.piece = st, 'PIECE', pc['name']
    if gone:
        col.operator("ac9_cloth_sim.restore_pieces", text=tx("Restore {0} Deleted Object(s)", len(gone)), icon='ERROR')


def draw_missing_pieces(layout, context, spec):
    """A red box at the top of Setup and AC9 Run when piece objects were deleted with Blender's own X / Del (the
    pattern still has them, user 2026-10-21): which ones and Restore. Only the Pieces list said so, and a tester
    could neither see nor edit a piece for a while (user 2026-10-27: keep X, show it at the top and in the
    viewport)."""
    from . import draft_tool as DT
    gone = DT.missing_pieces(context, spec)
    if not gone:
        return
    box = layout.box()
    box.alert = True
    box.label(text=tx("{0} piece object(s) deleted", len(gone)), icon='ERROR')
    for ln in wrap(', '.join(gone[:6]) + (' …' if len(gone) > 6 else ''), 36):
        box.label(text=ln)
    box.operator("ac9_cloth_sim.restore_pieces", text=tx("Restore {0} Deleted Object(s)", len(gone)), icon='FILE_REFRESH')
    # how to delete a piece (user 2026-11-12: it happens with Blender's own tools active, where the right-click menu
    # has no Delete Piece; an AC9 tool's X deletes the piece)
    for key in ("Delete pieces with X in an AC9 tool", "(Transform Pattern, Edit Pattern, ...)", "X in Select Box, Move, ... deletes", "only the object"):
        box.label(text=tx(key))


def draw_fabric(layout, context, spec):
    """Particle Distance, the garment's default fabric, Watch, a settings set: one column of fields (Fable UI review
    2026-10-21: the pieces' own distance is set in AC9 Piece, the fabrics are listed in Lists > Fabrics)."""
    props = context.scene.ac9_cloth_sim
    # a settings set saved in this file (core/presets.py, set by a test): Dress runs it, not the default. The
    # panel rows it fixes are grayed out (Fable review 2026-10-08)
    from .core import presets as PR
    fixed = PR.fixed_fabric(props.preset) if props.preset else {}
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(props, "mesh_size", text="Particle Distance (mm)")
    own = [pc for pc in spec['pieces'] if pc.get('enabled', True) and pc.get('mesh_mm')]
    if own:
        r = col.row()
        r.active = False
        r.label(text=tx("{0} piece(s) own distance", len(own)))
    # every piece has one fabric, the default one of the list (user 2026-10-20): no Weight / Bending / Physical here
    col.prop(props, "garment_fabric")
    if fixed:
        col.label(text="Settings fix the fabric", icon='ERROR')
    row = col.row()
    row.active = 'watch' not in fixed
    row.prop(props, "watch", text="Watch")
    if props.preset:
        import textwrap
        for i, ln in enumerate(wrap(tx("Settings: {0}", props.preset), 34)[:4]):
            layout.label(text=ln, icon='ERROR' if i == 0 else 'BLANK1')


def draw_pick_row(col, op_id, text, depress=False, icon='NONE', swatch=None, right=None, right_units=1.6, pad=False,
                  extra=None):
    """A row of a list (REVIEW_fable_lists_ui_2026-10-25 §2): a color swatch or nothing, the name as a button that
    picks (pressed when picked: the one mark of "picked" in every tab), a short value on the right. pad: an empty
    swatch's room (the row lines up with rows that have one). extra(row): the row's own toggles on the right (Pieces'
    states, a button's lock). Returns the operator's properties."""
    row = col.row(align=True)
    if swatch is not None or pad:
        sw = row.row()
        sw.ui_units_x = 0.9
        if swatch is not None:
            sw.template_node_socket(color=swatch)
        else:
            sw.label(text="")
    op = row.operator(op_id, text=text, icon=icon, depress=depress)
    if right is not None:
        sub = row.row(align=True)
        sub.ui_units_x = right_units
        sub.alignment = 'RIGHT'
        sub.label(text=right)
    if extra is not None:
        extra(row)
    return op


_fusers = {'k': None, 'v': {}, 'il': [], 'iu': {}}


def _fabric_cache(spec):
    """core/fabric.users_by_fabric, the interlinings and users_by_interlining, per spec version (the sidebar redraws
    on every mouse move; per piece they made the twins again: 10 ms a draw for a 133-piece spec). A garment saved
    before the interlinings' list shows the list migrate() will make (its row's click makes it)."""
    from .core import fabric as FA
    from .core import spec as S
    k = (store._cache.get('key'), id(spec))
    if _fusers['k'] != k:
        view = spec
        if any(pc.get('bond') for pc in spec['pieces']):
            view = S.loads(S.dumps(spec))
            FA._migrate_bonds(view, [])
        _fusers.update(k=k, v=FA.users_by_fabric(spec), il=view.get('interlinings', []), iu=FA.users_by_interlining(view))
    return _fusers


def fabric_users(spec):
    return _fabric_cache(spec)['v']


def interlining_users(spec):
    return _fabric_cache(spec)['iu']


def draw_fabric_list(layout, context, spec):
    """The garment's fabrics (core/fabric.py, user 2026-10-20) as rows (user 2026-10-25, REVIEW_fable_lists_ui
    _2026-10-25): its color, the default starred, its name (a click selects its pieces and shows the fabric in
    Properties > Tool, where its numbers and buttons are), how many pieces; New below; the pieces with an interlining."""
    from .core import fabric as FA
    from . import tool_tab as TT
    props = context.scene.ac9_cloth_sim
    fl = spec.get('fabrics', [])
    d = FA.default_fabric(spec)
    if d is None:          # saved before 2026-10-20: the default is made (core/fabric.migrate) at the next change
        box = layout.box()
        box.label(text="Garment (old values)", icon='SOLO_ON')
        box.label(text=tx("{0:g} g/m², {1:g} µN·m", props.fabric_weight, props.fabric_bending))
    from . import piece_look as PL
    cols = PL.fabric_colors(spec)
    us = fabric_users(spec)
    cur = TT.focused_item(spec, 'FABRIC')
    layout.label(text=tx("{0} fabric(s)", len(fl)))
    col = layout.column(align=True)
    for f in fl:
        is_d = d is not None and f['id'] == d['id']
        draw_pick_row(col, "ac9_cloth_sim.fabric_select", f['name'][:22], depress=(f['id'] == cur),
                      icon='SOLO_ON' if is_d else 'BLANK1', swatch=cols.get(f['id'], (0.5, 0.5, 0.5, 1.0)),
                      right=str(len(us.get(f['id'], [])))).fabric_id = f['id']
    layout.operator("ac9_cloth_sim.fabric_new", text="New", icon='ADD')
    # the interlinings, the lower half (user 2026-10-25, b-3: not a seventh tab); a row picks its pieces and shows
    # it in Properties > Tool, the pieces get one in AC9 Piece > Interlining
    c = _fabric_cache(spec)
    cur = TT.focused_item(spec, 'BOND')
    layout.separator()
    layout.label(text=tx("{0} interlining(s)", len(c['il'])))
    col = layout.column(align=True)
    for it in c['il']:
        draw_pick_row(col, "ac9_cloth_sim.interlining_select", it['name'][:22], depress=(it['id'] == cur),
                      icon='MOD_SOLIDIFY', right=str(len(c['iu'].get(it['id'], []))), pad=True).interlining_id = it['id']
    layout.operator("ac9_cloth_sim.interlining_new", text="New", icon='ADD')
    layout.separator()
    layout.prop(props, "show_fabric_colors", text="Show Fabric Colors")


def draw_obstacles(layout, context):
    """The obstacles a person added (objects without a pattern) as rows (a click selects the object, Shift adds it),
    Add Selected / Remove below (REVIEW_fable_lists_ui_2026-10-25 section 2)."""
    from . import dress as D
    cols = D.collider_objects(context)
    layout.label(text=tx("{0} collision object(s)", len(cols)) if cols else "No collision object", icon='MOD_PHYSICS')
    col = layout.column(align=True)
    for ob in cols:
        draw_pick_row(col, "ac9_cloth_sim.collider_select", ob.name[:24], depress=ob.select_get(),
                      icon='MESH_DATA').name = ob.name
    r2 = layout.row(align=True)
    r2.operator("ac9_cloth_sim.collider", text="Add Selected").use = True
    r2.operator("ac9_cloth_sim.collider", text="Remove").use = False


def draw_result(layout, spec, box=False):
    """P1 #6: what the result is and where it is kept (user 2026-10-13: "when is the Dress saved?"); each line kept
    to ~24 characters, longer ones were cut at the default sidebar width. False when the garment has no result."""
    from . import dress as D
    ri = D.result_info(bpy.data.objects.get(D.garment_name(spec)))
    if not ri:
        return False
    box = layout.box().column(align=True) if box else layout.column(align=True)
    if ri.get('missing'):
        box.label(text="Result: folder gone", icon='ERROR')
        box.label(text="Shape stays in garment")
        return True
    what = ({'SIMULATE': 'Phase Sim', 'REDRESS': 'Restart', 'LIVE': 'Live Sim', 'LOCAL': 'Local Sim'}.get(ri.get('button'))
            or {'START': 'Phase Sim', 'CONTINUE': 'Phase Sim', 'LIVE': 'Live Sim', 'LOCAL': 'Local Sim'}.get(ri['mode'], ri['mode']))
    stopped = tx(" (stopped)") if ri.get('error') else ""
    box.label(text=tx("Result: {0}{1} {2}", tx(what), stopped, ri['when']), icon='CHECKMARK')
    box.label(text=tx("{0} frames, {1:.0f} s", ri['frames'], ri['wall_s']) if ri.get('wall_s') is not None
              else tx("{0} frames", ri['frames']))
    # the shape is the garment's own vertices: in the blend once it is saved
    if not bpy.data.is_saved:
        box.label(text="Shape: file never saved", icon='ERROR')
    elif bpy.data.is_dirty:
        box.label(text="Shape: Ctrl+S to keep it", icon='FILE_TICK')
    else:
        box.label(text="Shape: saved in the blend", icon='FILE_TICK')
    box.label(text=("Frames: on the timeline" if ri['playing'] else
                    "Frames: dropped (shape kept)" if ri.get('dropped') else
                    "Frames: off (shape kept)"), icon='SEQUENCE')
    if ri['playing']:            # to fix a frame by hand, right under the line saying frames play
        draw_fix_by_hand(box, bpy.context, bpy.data.objects.get(D.garment_name(spec)),
                         enabled=D.AC9_OT_SimDress.running() is None)
    if ri.get('temp'):
        box.label(text="Frames in a temp folder", icon='ERROR')
    row = box.row(align=True)
    row.label(text=os.path.basename(ri['dir']))
    row.operator("wm.path_open", text="Open", icon='FILE_FOLDER').filepath = ri['dir']
    return True


class AC9_PT_SimLists(bpy.types.Panel):
    """The lists in one panel with tabs: Seams, Pieces, Obstacles, Tapes. A row picked here shows its settings in
    the Properties editor's Tool tab."""
    # user 2026-10-13, b-3
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Lists"

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def draw(self, context):
        layout = self.layout
        props = context.scene.ac9_cloth_sim
        spec, _ = store.get(context)
        # five tabs do not fit one row of the default sidebar ("Obsta..."): 3 + 2
        col = layout.column(align=True)
        for tabs in (('SEAMS', 'PIECES', 'FABRICS'), ('OBSTACLES', 'TAPES', 'FASTENERS')):
            row = col.row(align=True)
            for t in tabs:
                row.prop_enum(props, "list_tab", t)
        tab = props.list_tab
        if tab == 'SEAMS':
            draw_seam_list(layout, context, spec)
        elif tab == 'PIECES':
            draw_garment_rows(layout, context)
            draw_piece_list(layout, spec, select=True)
        elif tab == 'FABRICS':
            draw_fabric_list(layout, context, spec)
        elif tab == 'OBSTACLES':
            from . import dress as D
            me = D.garment_name(spec)
            auto = [ob for ob in context.scene.objects if ob.name.startswith(D.GARMENT_PREFIX) and ob.name != me
                    and D.job_of(ob)]
            col = layout.column(align=True)
            for ob in auto:
                col.label(text=ob.name[len(D.GARMENT_PREFIX):] + tx(" (garment)"), icon='MOD_CLOTH')
            if auto:
                col.label(text="Garments with a result: always", icon='INFO')
            draw_obstacles(layout, context)
        elif tab == 'FASTENERS':
            from . import button_ui as BU
            BU.draw_list(layout, context, spec)
        else:
            from . import measure_edit as MEE
            MEE.draw_list(layout, context)


def draw_overlay_switch(row, props):
    """The master switch and the popover with each kind (the header and the sidebar)."""
    row.prop(props, "show_overlays", text="", toggle=True, icon='HIDE_OFF' if props.show_overlays else 'HIDE_ON')
    row.popover(panel="AC9_PT_SimOverlays", text="Overlays")


class AC9_PT_SimOverlays(bpy.types.Panel):
    """What the add-on draws over the viewports, on the pattern and in 3D alike. A HEADER-region panel: never listed
    in the sidebar, opened with layout.popover() (draw_overlay_switch)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Overlays"
    bl_ui_units_x = 11

    def draw(self, context):
        layout = self.layout
        props = context.scene.ac9_cloth_sim
        row = layout.row()
        row.scale_y = 1.3
        row.prop(props, "show_overlays", text="Overlays: ON" if props.show_overlays else "Overlays: OFF",
                 toggle=True, icon='HIDE_OFF' if props.show_overlays else 'HIDE_ON')
        col = layout.column(align=True)
        col.enabled = props.show_overlays
        col.label(text=tx("Seams"))
        col.prop(props, "show_overlay", text="Seam Lines")
        col.prop(props, "show_lengths")
        col.prop(props, "show_notches")
        col.separator()
        col.label(text="Pieces")
        col.prop(props, "show_piece_colors")
        sub = col.row()
        sub.active = props.show_piece_colors
        sub.prop(props, "show_fabric_colors")
        col.prop(props, "show_names")
        col.separator()
        # colors that tell what is wrong with the cloth (diagnose.py, user 2026-11-02)
        col.label(text="Diagnose")
        col.prop(props, "show_problems")
        col.prop(props, "show_stretch")
        col.separator()
        col.label(text="Body")
        col.prop(props, "show_guides")
        mp = getattr(context.scene, 'ac9_cloth_sim_measure', None)
        if mp is not None:
            col.prop(mp, "show", text="Measurements")
        # the cloth's thickness: a Solidify on the garment, not drawn over it, so not under the switch above
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Garment")
        col.prop(props, "show_thickness")
        sub = col.row()
        sub.active = props.show_thickness
        sub.prop(props, "thickness_side", text="")


def _header(self, context):
    """Overlays in the 3D viewport header, only in a scene with a garment (a spec): Cloth Retopo adds its own
    Overlays the same way, and the two add-ons are not used on the same file (user 2026-10-14)."""
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if props is None or props.spec_text is None:
        return
    layout = self.layout
    layout.separator()
    draw_overlay_switch(layout.row(align=True), props)


CLASSES = (AC9_PT_SimOverlays, AC9_UL_SimSeams, AC9_PT_SimMissing, AC9_PT_SimMain, AC9_PT_SimDraft, AC9_PT_SimLists, AC9_PT_SimRoles, AC9_PT_SimPlacement)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ac9_seam_fold = bpy.props.FloatProperty(
        name="Fold", min=-180.0, max=180.0, get=_sfold_get, set=_sfold_set, options=set(),
        description="The angle the two pieces keep across this seam while simulated (+ = mountain seen from "
                    "outside, as on internal lines; 0 = none)")
    bpy.types.Scene.ac9_seam_fold_turn = bpy.props.EnumProperty(
        name="Turns", items=_sturn_items, get=_sturn_get, set=_sturn_set, options=set(),
        description="The piece that turns over the seam on the placed shape (the other stays)")
    bpy.types.Scene.ac9_seam_over = bpy.props.EnumProperty(
        name="Over", get=_sover_get, set=_sover_set, options=set(),
        items=[('NONE', "Auto", "From the garment's Front Over, else the buttonholes (the end with the hole on top)"),
               ('A', "A", "Side A's end on top: over the other on the placed ring"),
               ('B', "B", "Side B's end on top: over the other on the placed ring")],
        description="Which end of this closure is on top (a stand's or a cuff's ends buttoned over each other)")
    store.register_handlers()
    bpy.types.VIEW3D_HT_header.append(_header)


def unregister():
    for k in ('ac9_seam_fold', 'ac9_seam_fold_turn', 'ac9_seam_over'):
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
    bpy.types.VIEW3D_HT_header.remove(_header)
    store.unregister_handlers()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
