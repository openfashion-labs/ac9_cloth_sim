# SPDX-License-Identifier: GPL-3.0-or-later
"""Slash and spread on the flat pattern (core/spread.py): with the Edit Pieces tool, the right-click menu on an internal
line drawn from outline to outline (Slash and Spread) cuts the piece along it and opens it (a dialog asks the amount, flare / gathers, the hinge end and the side
that moves first). Cut Along Line (the same menu) cuts the piece in two along it, sewn back by a new seam (core/cut.py)."""
import bpy
import numpy as np
from .i18n import CTX, F9, rpt, tx
from . import units as U

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

    amount: bpy.props.FloatProperty(name="Spread", default=0.03, translation_context=CTX, min=0.0025, max=0.5, precision=1,
                                    subtype='DISTANCE', unit='LENGTH',      # m
                                    description="How far the cut opens (at the far end for a flare)")
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
            # not on a line: nothing to cut (S is Scale only since user 2026-11-11; this runs from the right-click menu)
            return {'PASS_THROUGH'}
        if not h:
            self.report({'WARNING'}, "Point at an internal line drawn from outline to outline")
            return {'CANCELLED'}
        self.piece, self.line = h[1], h[2]
        # the numbers first, in a dialog (user 2026-11-11: the Redo panel opens closed)
        T.dialog_aside(context, event, [self.piece], spec)   # beside the piece: the dialog does not cover the ghost
        T.ghost_open(self)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "amount")
        col.prop(self, "mode")
        sub = col.column()
        sub.enabled = self.mode == 'hinge'
        sub.prop(self, "hinge")
        col.prop(self, "side")
        spec, _ = store.get(context)
        if spec is not None and self.piece:     # the opened piece, drawn on the pattern while the values change
            T.draw_why(col, T.ghost(self, ("spread", self.piece, self.line, self.amount, self.mode, self.hinge, self.side),
                                    lambda: self._spread(spec)[:2] + ([],)))

    def _spread(self, spec):
        """(the spread copy, pieces changed, what spread returned); a ValueError when it may not open so."""
        sp = S.loads(S.dumps(spec))
        out = SP.spread(sp, self.piece, self.line, self.amount * 1000.0, self.mode, self.hinge, self.side)
        bad = D.problems(sp, list(out))
        if bad:
            raise ValueError(rpt("Not spread: ") + bad[0])
        return sp, list(out), out

    def cancel(self, context):
        T.clear_ghost()
        T.dialog_back(context)

    def execute(self, context):
        T.clear_ghost()
        T.dialog_back(context)
        if not self.piece:
            return {'CANCELLED'}
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        try:
            sp, _names, out = self._spread(spec)
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc) or "the line is gone")
            return {'CANCELLED'}
        msg = T.commit(context, sp, list(out), old_spec=old)     # the plain carry (core/spread.py)
        info = out[self.piece]
        self.report({'INFO'}, rpt("Spread {0} ({1}, side {2} moved, +{3:.0f} mm2)", U.fmt(context, self.amount * 1000.0), 'flare' if self.mode == 'hinge' else 'gathers', info['side'].upper(), info['area_added'])
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
