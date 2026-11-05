# SPDX-License-Identifier: GPL-3.0-or-later
"""Report translation, with no Blender dependency for standalone core tests."""
try:
    import bpy
except ImportError:
    def rpt(msg, *args):
        return msg.format(*args) if args else msg
else:
    from ..i18n import rpt
