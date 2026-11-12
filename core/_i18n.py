# SPDX-License-Identifier: GPL-3.0-or-later
"""Report translation, with no Blender dependency for standalone core tests."""
try:
    import bpy
except ImportError:
    def rpt(msg, *args):
        return msg.format(*args) if args else msg

    def length(mm, signed=False):
        return f"{mm:{'+' if signed else ''}.1f} mm"
else:
    from ..i18n import rpt

    def length(mm, signed=False):
        """A length (mm) in the scene's unit with its symbol, for messages (user 2026-11-11: one unit everywhere)."""
        from .. import units
        try:
            return units.fmt(bpy.context, mm, signed=signed)
        except Exception:
            return f"{mm:{'+' if signed else ''}.1f} mm"
