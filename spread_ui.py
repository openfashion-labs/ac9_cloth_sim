# SPDX-License-Identifier: GPL-3.0-or-later
"""Slash and spread on the flat pattern (core/spread.py): with the Edit Pattern tool, S on an internal line drawn from
outline to outline cuts the piece along it and opens it (the amount, flare / gathers and the hinge end in the Redo
panel). Ctrl+K on such a line cuts the piece in two along it, sewn back by a new seam (core/cut.py)."""
import bpy
import numpy as np
from .i18n import CTX, F9, rpt, tx

from . import draft_tool as T
from . import pattern_view as PV
from . import store
from .core import draft as D
from .core import spec as S
from .core import cut as CUT
from .core import spread as SP


class AC9_OT_SimSpread(bpy.types.Operator):
    """Cut the piece along the internal line under the cursor (drawn from outline to outline) and open it: a flare turns one side about an end of the line, gathers move it square to the line; the opening is bridged by a straight edge (the twin gets the mirror image)"""
    bl_idname = "ac9_cloth_sim.spread"
    bl_label = "Slash and Spread"
    bl_options = {'REGISTER', 'UNDO'}

    amount: bpy.props.FloatProperty(name="Spread", default=30.0, translation_context=CTX, min=2.5, max=500.0, precision=1,
                                    description="How far the cut opens (mm; at the far end for a flare)")
    mode: bpy.props.EnumProperty(name="Open", items=[
        ('hinge', "Flare", "Turn one side about an end of the line: open at the other end only"),
        ('parallel', "Gathers", "Move one side square to the line: open the same at both ends")])
    hinge: bpy.props.EnumProperty(name="Hinge", translation_context=CTX, items=[
        ('start', "Start", "Turn about the end the line was drawn from"),
        ('end', "End", "Turn about the end the line was drawn to")])
    side: bpy.props.EnumProperty(name="Moves", items=[
        ('auto', "Auto", "The side without the placement anchor and the grain line (else the smaller)"),
        ('a', "Side A", "The outline from the line's start round to its end"),
        ('b', "Side B", "The outline from the line's end round to its start")])
    piece: bpy.props.StringProperty(options={'HIDDEN'})
    line: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        if not T.AC9_OT_SimPatternDrag.poll(context):
            self.report({'WARNING'}, "Use it on the flat pattern in a 3D view")
            return {'CANCELLED'}
        spec, outlines = store.get(context)
        h = T.pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
        if h and h[0] not in ('iline', 'iend'):
            # S on a piece is Scale Piece (the next key of Edit Pattern; user 2026-10-26, Fable b-1)
            return {'PASS_THROUGH'}
        if not h:
            self.report({'WARNING'}, "Point at an internal line drawn from outline to outline")
            return {'CANCELLED'}
        self.piece, self.line = h[1], h[2]
        return self.execute(context)

    def execute(self, context):
        if not self.piece:
            return {'CANCELLED'}
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            out = SP.spread(sp, self.piece, self.line, self.amount, self.mode, self.hinge, self.side)
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc) or "the line is gone")
            return {'CANCELLED'}
        bad = D.problems(sp, list(out))
        if bad:
            self.report({'WARNING'}, rpt("Not spread: ") + bad[0])
            return {'CANCELLED'}
        msg = T.commit(context, sp, list(out), old_spec=old)     # the plain carry (core/spread.py)
        info = out[self.piece]
        self.report({'INFO'}, rpt("Spread {0:.0f} mm ({1}, side {2} moved, +{3:.0f} mm2)", self.amount, 'flare' if self.mode == 'hinge' else 'gathers', info['side'].upper(), info['area_added'])
                    + (rpt("; its mirror line too") if info.get('more') else "")
                    + (rpt("; {0}", msg) if msg else "") + rpt(F9))
        return {'FINISHED'}


class AC9_OT_SimCutLine(bpy.types.Operator):
    """Cut the piece in two along the internal line under the cursor (drawn from outline to outline): the line becomes an edge of each half and a new seam sews them together; nothing moves, so a dressed garment hangs as before (the twin is cut along the mirror image)"""
    bl_idname = "ac9_cloth_sim.cut_line"
    bl_label = "Cut Along Line"
    bl_options = {'REGISTER', 'UNDO'}

    piece: bpy.props.StringProperty(options={'HIDDEN'})
    line: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        if not T.AC9_OT_SimPatternDrag.poll(context):
            self.report({'WARNING'}, "Use it on the flat pattern in a 3D view")
            return {'CANCELLED'}
        spec, outlines = store.get(context)
        h = T.pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
        if not h or h[0] not in ('iline', 'iend'):
            self.report({'WARNING'}, "Point at an internal line drawn from outline to outline")
            return {'CANCELLED'}
        self.piece, self.line = h[1], h[2]
        return self.execute(context)

    def execute(self, context):
        if not self.piece:
            return {'CANCELLED'}
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            out = CUT.cut(sp, self.piece, self.line)
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc) or "the line is gone")
            return {'CANCELLED'}
        more = [x for v in out.values() for x in v.get('more', [])]     # its mirror line on a symmetric piece
        sources = {v['new']: n for n, v in out.items()}
        sources.update({i['new']: sources.get(h, h) for h, i in more})
        names = sorted(set(out) | set(sources))
        bad = D.problems(sp, names)
        if bad:
            self.report({'WARNING'}, rpt("Not cut: ") + bad[0])
            return {'CANCELLED'}
        ident = (np.eye(2), np.zeros(2))                  # nothing moved: each half hangs where it was
        msg = T.commit(context, sp, names, old_spec=old, rigid={n: ident for n in names}, sources=sources)
        from . import dress as DR
        g = bpy.data.objects.get(DR.garment_name(sp))
        rs = set(DR.reset_pieces(g)) if DR.has_result(g) else set()
        redo = sorted(n for n in names if n in rs)
        if redo:                                           # shown as placed objects: those made again, cut
            try:
                DR.reset_to_placed(context, g, store.get(context)[0], redo, context.scene.ac9_cloth_sim.mesh_size)
            except ValueError as exc:
                msg = (msg + tx("; ") if msg else "") + tx("placed objects not made again ({0})", exc)
        info = out[self.piece]
        self.report({'INFO'}, rpt("Cut {0}: new piece {1}, sewn by seam {2}", self.piece, info['new'], info['seam'])
                              + "".join(rpt("; its mirror line too: new piece {0}, seam {1}", i['new'], i['seam'])
                                        for _, i in more)
                              + (rpt("; {0}", msg) if msg else ""))
        return {'FINISHED'}


CLASSES = (AC9_OT_SimSpread, AC9_OT_SimCutLine)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
