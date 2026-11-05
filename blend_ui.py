# SPDX-License-Identifier: GPL-3.0-or-later
"""Blend across a seam (core/blend.py), a sub-panel of Seams: the kink at each end of the active seam (the two pieces
laid together along it), and Blend to make the edges going on past that end run on in line."""
import bpy
from .i18n import CTX, rpt, tx

from . import draft_tool as T
from . import store
from .core import blend as BL
from .core import draft as D
from .core import spec as S


def _short(why):
    for key, txt in (("dart", "a dart: Close Dart"), ("over each other", "pieces overlap"), ("corner", "a real corner"),
                     ("sewn to each other", "edges sewn together")):
        if key in why:
            return tx(txt)
    return tx("cannot blend")


class AC9_OT_SimBlend(bpy.types.Operator):
    """Make the outline run on smoothly across the active seam's end: laid together along the seam, the two edges going on past it turn half the kink each, over their first 30 mm (twins too)"""
    bl_idname = "ac9_cloth_sim.blend"
    bl_label = "Blend"
    bl_options = {'REGISTER', 'UNDO'}

    end: bpy.props.EnumProperty(translation_context=CTX, items=[('from', "From", "The seam's start"), ('to', "To", "The seam's end")])

    @classmethod
    def poll(cls, context):
        spec, _ = store.get(context)
        return store.active_seam(context, spec) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        sm = store.active_seam(context, spec)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            ch, k0, k1, msg = BL.blend(sp, sm['id'], self.end)
        except (ValueError, StopIteration, KeyError) as exc:
            self.report({'WARNING'}, str(exc) or "cannot blend there")
            return {'CANCELLED'}
        bad = D.problems(sp, ch)
        if bad:
            self.report({'WARNING'}, rpt("Not blended: ") + bad[0])
            return {'CANCELLED'}
        carry = T.commit(context, sp, ch, old_spec=old)
        self.report({'INFO'}, rpt("{0} {1}: kink {2:.1f} -> {3:.1f} deg", sm['id'], self.end, k0, k1) + (rpt("; {0}", carry) if carry else ""))
        return {'FINISHED'}


class AC9_PT_SimBlend(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Blend"
    bl_parent_id = "AC9_PT_SimSeams"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def draw(self, context):
        layout = self.layout
        spec, _ = store.get(context)
        sm = store.active_seam(context, spec)
        if sm is None:
            layout.label(text="Pick a seam in the list")
            return
        col = layout.column(align=True)
        for end in ('from', 'to'):
            row = col.row(align=True)
            try:
                j = BL.junction(spec, sm['id'], end)
            except (ValueError, StopIteration, KeyError):
                row.label(text=tx("{0}: not on a corner", tx(end.capitalize())))
                continue
            why = BL.refusal(spec, sm['id'], j)
            if why:
                row.label(text=tx("{0}: ", tx(end.capitalize())) + _short(why))
            else:
                row.label(text=tx("{0}: kink {1:.1f} deg", tx(end.capitalize()), j['kink']))
                row.operator("ac9_cloth_sim.blend", text="Blend").end = end


CLASSES = (AC9_OT_SimBlend,)   # AC9_PT_SimBlend: its draw only, for the Tool tab (tool_tab.py); the old sidebar Seams it sat under is gone (user 2026-10-22)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
