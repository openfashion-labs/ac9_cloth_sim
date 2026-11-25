# SPDX-License-Identifier: GPL-3.0-or-later
"""Review one immutable support snapshot and explicitly copy it, without sending it."""
from pathlib import Path
import sys
import tomllib

import bpy
from .core.support_info import LABEL, UNKNOWN, report
from .i18n import CTX, rpt, tx, wrap


def package_version():
    """Read only this package's bounded manifest, when explicitly requested."""
    try:
        with Path(__file__).with_name('blender_manifest.toml').open('rb') as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            return UNKNOWN
        return tomllib.loads(data.decode('utf-8')).get('version', UNKNOWN)
    except (OSError, UnicodeError, ValueError):
        return UNKNOWN


def collect():
    return report(package_version(), bpy.app.version, sys.platform)


class AC9_OT_SimCopyDiagnostics(bpy.types.Operator):
    """Review the add-on version, Blender version and OS, then copy them without sending anything"""
    bl_idname = 'ac9_cloth_sim.copy_diagnostics'
    bl_label = LABEL
    bl_options = {'REGISTER'}
    # REGISTER makes the command searchable with F3. No RNA payload or UNDO; repeat execution refuses.

    @classmethod
    def poll(cls, context):
        return context.window_manager is not None

    def invoke(self, context, event):
        self._snapshot = collect()
        return context.window_manager.invoke_props_dialog(
            self, width=440, title=tx(LABEL), confirm_text=tx('Copy'), translate=False)

    def draw(self, context):
        layout = self.layout
        for line in wrap(tx('Review the text below before copying.'), 54):
            layout.label(text=line, translate=False)
        box = layout.box()
        for line in getattr(self, '_snapshot', '').splitlines():
            box.label(text=line, translate=False)
        for message in ('This only prepares diagnostic text. Nothing is sent.',
                        'Clipboard history or sync is controlled by your OS.',
                        'Check a local paste before sharing; copying can fail silently.'):
            for line in wrap(tx(message), 54):
                layout.label(text=line, translate=False)

    def execute(self, context):
        snapshot = getattr(self, '_snapshot', None)
        self._snapshot = None
        if snapshot is None:
            self.report({'WARNING'}, rpt('Review the diagnostics before copying.'))
            return {'CANCELLED'}
        try:
            context.window_manager.clipboard = snapshot
        except (RuntimeError, AttributeError, TypeError, OSError):
            self.report({'ERROR'}, rpt('Could not copy the diagnostics.'))
            return {'CANCELLED'}
        # Blender's Windows clipboard setter can return without reporting a failed write.
        # Never read the previous clipboard to verify it; ask for a local paste check instead.
        self.report({'INFO'}, rpt('Check the pasted text in a local editor before sharing.'))
        return {'FINISHED'}

    def cancel(self, context):
        self._snapshot = None


CLASSES = (AC9_OT_SimCopyDiagnostics,)


def draw_help(self, context):
    # Default F3 searches menu entries, including when no garment exposes the run panel.
    self.layout.operator(AC9_OT_SimCopyDiagnostics.bl_idname, text=LABEL, icon='COPYDOWN')


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_help.append(draw_help)


def unregister():
    bpy.types.TOPBAR_MT_help.remove(draw_help)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
