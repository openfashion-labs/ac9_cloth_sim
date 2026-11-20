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
AC9_GUIDANCE_BUILD = "0eb5bb7366fbec38274f8648a51aa66c46c1bb7cd614dcc948f76797d04bee11"
