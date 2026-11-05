# SPDX-License-Identifier: GPL-3.0-or-later
"""Work Screen: the add-on's workspace in one click (Fable review 2026-10-13 §6-2, step 5): the 3D view on the left
(no toolbar, no sidebar), the pattern in the middle with the sidebar and a toolbar wide enough for the tools' names,
the Properties editor (its Tool tab, tool_tab.py) on the right, one timeline under both 3D views (user 2026-10-28,
to scrub the drape's frames; 2026-10-21 had none), no Outliner (user 2026-10-27: the pieces are in Lists > Pieces and
AC9 Piece). The left view is in perspective: the view carried over keeps where it looks from, not an orthographic
projection (a scene's 3D view left orthographic came over as is, user 2026-11-02).

The pattern view looks straight down on the layout, orthographic, its rotation locked, framed on the pieces (user
2026-10-21: this replaces the Pattern View button). It is not a local view: pieces drawn or rebuilt later are new
objects, which a local view would not show.

The layout is kept in workspace.blend next to this file (made by experiments/work_screen_regions_2026-10-27/
make_ws_gui.py, the timeline added by experiments/work_screen_timeline_2026-11-02/make_ws_timeline.py: the toolbar
and sidebar widths and which panels are open as saved there; the API cannot set a region's width or a panel's open state). It is loaded with libraries.load, not workspace.append_activate: that
resets every region's size and panel to the defaults (BLO_update_defaults_workspace), so the toolbar came out icons
only and the folded panels open (GUI 2026-10-27). The views are set after it is shown, on a timer (the window's
screen changes after the operator returns).

The file's workspace carries a version (VERSION, the custom property ac9_ws_version); a copy in the .blend from an
older add-on is replaced by the new one (user 2026-10-27).
"""
import os
from .i18n import rpt

import bpy

from . import pattern_view as PV
from . import selection as SEL

NAME = "AC9 Cloth Sim"
FILE = os.path.join(os.path.dirname(__file__), "workspace.blend")
VERSION = 3            # workspace.blend's ac9_ws_version (1: the 2026-10-21 layout with the Outliner, no mark;
                       # 2: no timeline)


def _view_of(screen, leftmost=False):
    """The largest (or the leftmost) 3D view's view (to carry over to the new workspace's 3D view), or None."""
    vs = [a for a in screen.areas if a.type == 'VIEW_3D'] if screen else []
    if not vs:
        return None
    a = min(vs, key=lambda a: a.x) if leftmost else max(vs, key=lambda a: a.width * a.height)
    rv = a.spaces.active.region_3d
    return {'view_location': rv.view_location.copy(), 'view_rotation': rv.view_rotation.copy(),
            'view_distance': rv.view_distance, 'view_perspective': 'PERSP'}


def _same_file(path):
    return os.path.normcase(os.path.abspath(bpy.path.abspath(path))) == os.path.normcase(os.path.abspath(FILE))


def _load():
    """The workspace from workspace.blend, sizes and panels as saved; the Library entry the load leaves is removed."""
    with bpy.data.libraries.load(FILE, link=False) as (_src, dst):
        dst.workspaces = [NAME]
    for lib in [lib for lib in bpy.data.libraries if _same_file(lib.filepath)]:
        bpy.data.libraries.remove(lib)
    return dst.workspaces[0]


def _settle(ws, old):
    """The new workspace's tab at the end (where append_activate put it); the older copy deleted."""
    win = next((w for w in bpy.context.window_manager.windows if w.workspace == ws), None)
    if win is None:
        return None
    with bpy.context.temp_override(window=win, workspace=ws):
        bpy.ops.workspace.reorder_to_back()
    # not workspace.delete: it deletes the window's own workspace (later, from the event loop), whatever the
    # context override says, so it took the new one (GUI 2026-10-27)
    if old is not None and old.name in bpy.data.workspaces and \
            not any(w.workspace == old for w in bpy.context.window_manager.windows):
        bpy.data.batch_remove({old})
    return None


def pattern_view(win, area):
    """Make `area` the pattern view: looking down the layout's z, orthographic, rotation locked, framed on the pieces.
    Once locked it is left as the user moved or zoomed it."""
    rv3d = area.spaces.active.region_3d
    if rv3d.lock_rotation:
        return
    emp = PV.layout_empty(bpy.context)
    rv3d.view_perspective = 'ORTHO'
    if emp is not None:
        rv3d.view_rotation = emp.matrix_world.to_quaternion()
    else:
        rv3d.view_rotation = (1.0, 0.0, 0.0, 0.0)            # top: the layout is made on the floor
    rv3d.lock_rotation = True
    obs = list(PV.piece_objects().values())
    if not obs:
        return
    vl = bpy.context.view_layer
    sel, act = SEL.objects(bpy.context), vl.objects.active
    for o in vl.objects:
        o.select_set(o in obs)
    reg = next(r for r in area.regions if r.type == 'WINDOW')
    try:
        with bpy.context.temp_override(window=win, area=area, region=reg):
            bpy.ops.view3d.view_selected()
    except (RuntimeError, TypeError):            # no window (background): the view is left unframed
        pass
    finally:
        for o in vl.objects:
            o.select_set(o in sel)
        vl.objects.active = act


def _configure(view=None, win=None):
    """The two 3D views (the left one: the view carried over, no sidebar; the right one: looking at the pattern, its
    rotation locked, the sidebar on the add-on's tab beside it, user 2026-10-21) and the Properties editor's Tool tab,
    in the window showing the workspace."""
    win = win or next((w for w in bpy.context.window_manager.windows if w.workspace.name == NAME), None)
    if win is None:
        return None
    try:
        if win not in bpy.context.window_manager.windows[:] or win.workspace.name != NAME:
            return None
    except ReferenceError:
        return None
    with bpy.context.temp_override(window=win):
        _configure_window(win, view)
    return None


def _configure_window(win, view):
    scr = win.screen
    vs = sorted((a for a in scr.areas if a.type == 'VIEW_3D'), key=lambda a: a.x)
    for i, a in enumerate(vs):
        sp = a.spaces.active
        sp.show_region_ui = i == len(vs) - 1
        if i == 0 and view is not None:
            for k, v in view.items():
                setattr(sp.region_3d, k, v)
        for r in a.regions:
            if r.type == 'UI':
                try:
                    r.active_panel_category = 'AC9 Cloth Sim'
                except (TypeError, AttributeError):
                    pass
    for a in scr.areas:
        if a.type == 'PROPERTIES':
            a.spaces.active.context = 'TOOL'
    if len(vs) >= 2:
        pattern_view(win, vs[-1])
        from . import view_setup
        notes = view_setup.configure(bpy.context, win, vs[0], vs[-1])
        scr['ac9_view_warning'] = '\n'.join(notes)
    for a in scr.areas:
        a.tag_redraw()
    return None


class AC9_OT_SimWorkScreen(bpy.types.Operator):
    """Show the AC9 Cloth Sim workspace: the 3D view on the left, the pattern in the middle with the tools and the sidebar, the Properties editor's Tool tab (the selected piece's or seam's settings) on the right. Made the first time, shown again after; one made by an older version of the add-on is replaced"""
    bl_idname = "ac9_cloth_sim.work_screen"
    bl_label = "Work Screen"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        # A detached editor has a temporary Screen, not a full workspace layout.
        return (context.window is not None and context.window.parent is None
                and not context.window.screen.is_temporary)

    def execute(self, context):
        win = context.window
        old = bpy.data.workspaces.get(NAME)
        if old is not None and old.get('ac9_ws_version', 1) >= VERSION:
            from . import view_setup
            view_setup.organize_existing(context)
            win.workspace = old
            bpy.app.timers.register(lambda: _configure(None, win), first_interval=0.2)
            return {'FINISHED'}
        if not os.path.exists(FILE):
            self.report({'ERROR'}, rpt("The workspace file is missing: {0}", FILE))
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        from . import view_setup
        view_setup.organize_existing(context)
        if old is not None:            # the older copy's 3D view carried over; renamed so the new one takes the name
            view = _view_of(old.screens[0] if old.screens else None, leftmost=True)
            old.name = NAME + " (old)"
        else:
            view = _view_of(win.screen)
        ws = _load()
        win.workspace = ws
        bpy.app.timers.register(lambda: _settle(ws, old), first_interval=0.1)
        bpy.app.timers.register(lambda: _configure(view, win), first_interval=0.2)
        return {'FINISHED'}


CLASSES = (AC9_OT_SimWorkScreen,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
