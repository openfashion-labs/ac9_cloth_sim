# SPDX-License-Identifier: GPL-3.0-or-later
"""The add-on's hotkeys: one key starts and stops Simulate in the AC9 work screen.

One add-on keymap item in Blender's "Frames" keymap, where the playback key lives: it is looked at in every area of
the window, so the key works wherever the mouse is. Its operator's poll lets it act only in the AC9 Cloth Sim work
screen with a garment that can run; elsewhere the poll fails and the event goes on to what the key did before
(Blender's playback by default; an add-on keymap item whose poll fails passes the event on, as piece_hide's H does).

The default key is F5, enabled, independently of Blender's Spacebar Action. It is shown, switched and rebound in
the add-on's preferences (Hotkeys, draw_prefs), like in Preferences >
Keymap: the item drawn and read is the one in the user key configuration, the one Blender saves a change to.
Stopping is read by Live's own modal (dress.py: is_stop_key) with the same key, which gets every key first while it
runs, so it stops wherever the mouse is. Reasons: docs/reviews/REVIEW_fable_live_hotkeys_2026-11-22 of the project
(user 2026-11-23: a key field in the preferences, the name showing the key).
"""
import bpy

from .i18n import CTX, tx, wrap
from .core import spec as S

KEYMAP = 'Frames'
LIVE_NAME = S.PLAY_KEY_NAME
HOTKEYS = "Hotkeys"
HOTKEYS_HELP = ("In the AC9 Cloth Sim work screen, wherever the mouse is. Elsewhere the key does what it did before "
                "(Space keeps Blender's playback, tools or search action)")
_keymaps = []


def play_key(context=None):
    """Default simulation key, independent of Blender's playback/tools/search preference."""
    return 'F5', False


def user_item(context=None):
    """The item as the user set it (the user key configuration: what Blender runs and saves), else the add-on's."""
    try:
        km = (context or bpy.context).window_manager.keyconfigs.user.keymaps.get(KEYMAP)
        if km is not None:
            for kmi in km.keymap_items:
                if kmi.idname == AC9_OT_LivePlayKey.bl_idname:
                    return kmi
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    return _keymaps[0][1] if _keymaps else None


def is_stop_key(event):
    """True for a press of the key set here (as the user set it), which stops a running Simulate."""
    if event.value != 'PRESS' or getattr(event, 'is_repeat', False):
        return False
    kmi = user_item()
    try:
        if kmi is None or not kmi.active:
            return False
        return (event.type == kmi.type and bool(event.shift) == bool(kmi.shift)
                and bool(event.ctrl) == bool(kmi.ctrl) and bool(event.alt) == bool(kmi.alt)
                and bool(event.oskey) == bool(kmi.oskey))
    except ReferenceError:
        return False


def draw_prefs(layout, context):
    """The Hotkeys part of the add-on's preferences: each key on or off and the key itself, changed in place."""
    head, body = layout.panel("ac9_cloth_sim_hotkeys", default_closed=False)
    head.label(text=HOTKEYS, text_ctxt=CTX)
    if body is None:
        return
    kmi = user_item(context)
    if kmi is None:
        body.label(text="Hotkeys are set when Blender runs with its window", text_ctxt=CTX, icon='INFO')
        return
    row = body.row()
    row.prop(kmi, "active", text="")
    sub = row.row()
    sub.active = kmi.active
    sub.label(text=LIVE_NAME, text_ctxt=CTX)
    sub.prop(kmi, "type", text="", full_event=True)
    col = body.column(align=True)
    col.active = False
    for ln in wrap(tx(HOTKEYS_HELP), 60):
        col.label(text=ln)


class AC9_OT_LivePlayKey(bpy.types.Operator):
    """Start Simulate, or stop it while it runs"""
    bl_idname = "ac9_cloth_sim.live_play_key"
    bl_label = LIVE_NAME
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import dress as D
        from . import work_screen as WS
        win = context.window
        if win is None or win.workspace is None or win.workspace.name != WS.NAME:
            return False
        btn = D.AC9_OT_SimDress.running_button()
        if btn is not None:
            return btn == 'LIVE'          # a Phase Sim running: the key does what it did before
        return D.AC9_OT_SimDress.poll(context)

    def execute(self, context):
        # the same operator as the panel's Simulate button: it starts, or stops a running Simulate. FINISHED either
        # way: a cancelled start (its report says why) must not pass the key on and start the playback instead
        bpy.ops.ac9_cloth_sim.dress('INVOKE_DEFAULT', mode='LIVE', button='LIVE')
        return {'FINISHED'}


CLASSES = (AC9_OT_LivePlayKey,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:          # background: no key configuration
        return
    km = kc.keymaps.new(name=KEYMAP, space_type='EMPTY')
    key, shift = play_key()
    kmi = km.keymap_items.new(AC9_OT_LivePlayKey.bl_idname, type=key, value='PRESS', shift=shift)
    kmi.active = True
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
