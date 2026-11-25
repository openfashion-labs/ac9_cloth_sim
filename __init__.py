# SPDX-License-Identifier: GPL-3.0-or-later
"""AC9 Cloth Sim: sew garment patterns and dress avatars in Blender.

The garment spec (pieces, seams as edge + interval, placement) is JSON in a Text datablock
(store.py); the panels, the flat pattern objects and the overlay are all views of it.
"""
import importlib


def ac9_companion_state(context):
    """Read-only integration; this never enables a tool or changes selection."""
    from .companion_state import snapshot
    return snapshot(context)

try:
    import bpy  # noqa: F401
except ImportError:          # core/ is plain Python: tests import it without Blender
    bpy = None

if bpy is not None:
    if "companion_state" in locals():
        importlib.reload(companion_state)
    # F3 re-runs this file only; reloading _addon re-runs it too, and it reloads the rest in order
    if "_addon" in locals():
        importlib.reload(_addon)  # noqa: F821
    from . import _addon  # noqa: E402

    def register():
        _addon.register()

    def unregister():
        _addon.unregister()
AC9_GUIDANCE_BUILD = "f3b92da6971234e08f3c4f08e5d82c8e1b2e09ece0db5096940993d2c7c3d83b"
