# SPDX-License-Identifier: GPL-3.0-or-later
"""Darts on the flat pattern (core/dart.py). Add Dart puts a dart on the outline point under the cursor (Edit Pieces'
right-click menu on an outline edge, its width and length in the Redo panel) and trues its mouth. Move Dart (the right-click menu on a
dart's leg or apex with Edit Pieces, or Move in the seam box) pivots it to the outline point clicked next, or slides it along
its line (Shift), showing the result under the cursor first; it leaves no corner where the dart was (the seams
there join up again, user 2026-10-21 B-3). The selected seam's box shows a dart's angle and legs, Move, Fold
(the mouth folded shut, dotted) and True Mouth (draw_dart_rows)."""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from .i18n import CTX, F9, rpt, tx
from . import units as U

from . import draft_tool as T
from . import pattern_view as PV
from . import store
from . import view_visibility as VV
from .core import dart as DT
from .core import draft as D
from .core import spec as S


def active_dart(context, spec):
    """(piece, dart) when the seam picked in the Seams list is a dart, else (None, None)."""
    sm = store.active_seam(context, spec)
    if sm is None or sm['a']['piece'] != sm['b']['piece']:
        return None, None
    dt = next((x for x in DT.find_darts(spec, sm['a']['piece']) if x['seam'] == sm['id']), None)
    return (sm['a']['piece'], dt) if dt else (None, None)


def dart_at(context, spec, event):
    """(piece, dart) for the dart whose leg or apex is under the cursor on the flat pattern, else (None, None)."""
    _, outlines = store.get(context)
    h = T.pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
    if not h or h[0] not in ('edge', 'corner'):
        return None, None
    for dt in DT.find_darts(spec, h[1]):
        if (h[0] == 'edge' and h[2] in dt['leg_edges']) or (h[0] == 'corner' and h[2] == dt['apex']):
            return h[1], dt
    return None, None


def _identity(P):
    return np.asarray(P, float).reshape(-1, 2)


def _select_seam(context, sid):
    props = context.scene.ac9_cloth_sim
    ids = [it.seam_id for it in props.seams]
    if sid in ids:
        props.seam_index = ids.index(sid)


class AC9_OT_SimAddDart(bpy.types.Operator):
    """Put a dart on the outline under the cursor: its mouth along the outline, its apex straight inside, sewn leg to leg (the twin gets the mirror image)"""
    bl_idname = "ac9_cloth_sim.add_dart"
    bl_label = "Add Dart"
    bl_options = {'REGISTER', 'UNDO'}

    width: bpy.props.FloatProperty(name="Width", default=0.02, min=0.002, max=0.2, precision=1, subtype='DISTANCE', unit='LENGTH', 
                                   description="Width of the dart's mouth along the outline")
    length: bpy.props.FloatProperty(name="Length", default=0.08, min=0.005, max=0.6, precision=1, subtype='DISTANCE', unit='LENGTH', 
                                    description="From the middle of the mouth to the apex")
    true_mouth: bpy.props.BoolProperty(name="True the Mouth", default=True,
                                       description="Shape the outline across the mouth so that, sewn, it runs on smoothly")
    piece: bpy.props.StringProperty(options={'HIDDEN'})
    edge: bpy.props.StringProperty(options={'HIDDEN'})
    t: bpy.props.FloatProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        if not T.AC9_OT_SimPatternDrag.poll(context):
            self.report({'WARNING'}, "Use it on the flat pattern in a 3D view")
            return {'CANCELLED'}
        spec, outlines = store.get(context)
        h = T.pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
        if not h or h[0] != 'edge':
            self.report({'WARNING'}, "Point at an edge of the outline")
            return {'CANCELLED'}
        self.piece, self.edge, self.t = h[1], h[2], h[3]
        return self.execute(context)

    def execute(self, context):
        if not self.piece:
            return {'CANCELLED'}
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            out, moved = DT.add_dart(sp, self.piece, self.edge, self.t, self.width * 1000.0, self.length * 1000.0,
                                         true=self.true_mouth)
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc) or "the edge is gone")
            return {'CANCELLED'}
        bad = D.problems(sp, list(out))
        if bad:
            self.report({'WARNING'}, bad[0])
            return {'CANCELLED'}
        # the cloth outside the dart did not move: every point of the new pieces is where it was (the seams split at
        # the mouth would otherwise be laid by fraction over the whole old seam and fold the cloth)
        same = {p: _identity for p in out}
        msg = T.commit(context, sp, list(out), old_spec=old, rigid=same)
        _select_seam(context, out[self.piece])
        self.report({'INFO'}, rpt("Dart {0} on {1}", out[self.piece], self.piece) + (rpt(" ({0} mark(s) moved off its mouth)", moved)
                                                                             if moved else "") + (rpt("; {0}", msg) if msg else "") + rpt(F9))
        return {'FINISHED'}


class AC9_OT_SimMoveDart(bpy.types.Operator):
    """Move the active dart (pick it in the Seams list) to the point of its piece's outline you click. Pivot: the outline between turns about the apex (the dart closes there and opens here, same angle). Shift: slide it along its own line. The result shows under the cursor before you click"""
    bl_idname = "ac9_cloth_sim.move_dart"
    bl_label = "Move Dart"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(name="Mode", translation_context=CTX, items=[('pivot', "Pivot", "Turn the outline between about the apex: the dart opens at the new place, same apex, same angle"),
                                                    ('shift', "Shift", "Slide the dart along its own line (the outline does not turn)")])
    side: bpy.props.EnumProperty(name="Turn", items=[('auto', "Auto", "Turn the part without the placement anchor and the grain line (else the shorter)"),
                                                    ('short', "Shorter Side", "Turn the shorter part of the outline"),
                                                    ('after', "After", "Turn the outline after the dart"),
                                                    ('before', "Before", "Turn the outline before the dart")])
    apex_follows: bpy.props.BoolProperty(name="Apex Follows", default=False,
                                         description="Shift: move the apex with the mouth (off: the apex stays, the dart leans)")
    true_mouth: bpy.props.BoolProperty(name="True the Mouth", default=True,
                                       description="Shape the outline across the new mouth so that, sewn, it runs on smoothly")

    SIDES = ('auto', 'after', 'before')

    @classmethod
    def poll(cls, context):
        return T.AC9_OT_SimPatternDrag.poll(context)

    def invoke(self, context, event):
        # the dart under the cursor (M with Edit Pieces: moving a dart changes the piece's shape, so it is an Edit
        # Pattern key, not Edit Seams' M, which slides where a seam sews; Fable REVIEW_fable_dart_data_model), else
        # the one picked in the Seams list (the seam box's Move)
        spec, _ = store.get(context)
        self.piece, dt = dart_at(context, spec, event)
        if dt is None:
            self.piece, dt = active_dart(context, spec)
        if self.piece is not None and self.piece not in VV.pattern_names(context, spec):
            return {'CANCELLED'}
        if dt is None:
            self.report({'WARNING'}, "Point at a dart's leg or apex, or pick the dart in the Seams list")
            return {'CANCELLED'}
        self.seam = dt['seam']
        _select_seam(context, self.seam)
        self.M = PV.layout_matrix(context)
        self.key, self.trial = None, None        # the move under the cursor, tried on a copy: (outlines, sector, side, problem)
        self.pending = None                      # the point the trued mouth is still to be shown for (on the next timer tick)
        self._draw_area = context.area.as_pointer()
        self._h = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        self._timer = context.window_manager.event_timer_add(0.15, window=context.window)
        self._status(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _status(self, context):
        txt = tx("Move dart {0} ({1}", self.seam, 'Pivot' if self.mode == 'pivot' else 'Shift')
        if self.mode == 'pivot':
            used = self.trial[2] if self.trial else None
            txt += tx(", turn {0}", self.side) + (tx(" = {0}", used) if self.side == 'auto' and used else "")
        else:
            txt += ", apex follows" if self.apex_follows else ", apex stays"
        txt += ")"
        if self.trial and self.trial[3]:
            txt += tx("   cannot: {0}", self.trial[3])
        txt += ("   click the outline: move   S: Pivot / Shift   Tab: side to turn   A: apex follows (Shift)"
                "   Esc / right click: cancel")
        context.area.header_text_set(txt)

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._h, 'WINDOW')
        context.window_manager.event_timer_remove(self._timer)
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def _move(self, sp, eid, t, true=None):
        return DT.move_dart(sp, self.piece, self.seam, eid, t, side=self.side, mode=self.mode,
                            true=self.true_mouth if true is None else true, apex_follows=self.apex_follows)

    def _try(self, spec, eid, t, true=None):
        """The move on a copy: (new outlines of the piece and its twin, the part that turns as it is now, side, problem)."""
        sp = S.loads(S.dumps(spec))
        try:
            out, side, _, _ = self._move(sp, eid, t, true)
        except (ValueError, StopIteration) as exc:
            return None, None, None, str(exc) or "cannot move there"
        bad = D.problems(sp, list(out))
        lines = [(n, S.Outline(D.piece(sp, n)).B) for n in out]
        sector = None
        if side in ('after', 'before'):
            dt = next(x for x in DT.find_darts(spec, self.piece) if x['seam'] == self.seam)
            sector = DT._sector(spec, self.piece, dt, eid, t, side)
        return lines, sector, side, (bad[0] if bad else None)

    def _update(self, context, event):
        spec, outlines = store.get(context)
        h = T.pick(context, spec, outlines, self.M, event.mouse_region_x, event.mouse_region_y)
        on = h is not None and h[0] == 'edge' and h[1] == self.piece
        key = (h[2], round(h[3], 3), self.mode, self.side, self.apex_follows) if on else None
        if key != self.key:         # tried again only when the point or an option changed
            self.key = key
            # while the cursor moves: the move without truing the mouth (truing is ~0.1 s on a 700-point piece, the
            # move ~0.04 s); the trued mouth on the next timer tick (_settle)
            self.trial = self._try(spec, h[2], h[3], true=False) if on else None
            self.pending = (h[2], h[3]) if on and self.true_mouth else None
            self._status(context)
            context.area.tag_redraw()
        return h if on else None

    def _settle(self, context):
        if self.pending is None:
            return
        tr = self._try(store.get(context)[0], *self.pending)
        self.pending = None
        if tr[0] is not None or self.trial is None or self.trial[0] is None:
            self.trial = tr
        self._status(context)
        context.area.tag_redraw()

    @staticmethod
    def _segs(W):
        return [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]

    def _draw(self, context):
        try:
            if bpy.context.area is None or bpy.context.area.as_pointer() != self._draw_area \
                    or self.piece not in VV.pattern_names(bpy.context):
                return
            if not self.trial or self.trial[0] is None:
                return
            lines, sector, _, bad = self.trial
            region = context.region
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            sh.bind()
            sh.uniform_float('viewportSize', (region.width, region.height))
            if sector is not None:          # the part that turns, where it is now
                sh.uniform_float('lineWidth', 1.5)
                sh.uniform_float('color', (0.4, 0.8, 1.0, 0.8))
                batch_for_shader(sh, 'LINES', {'pos': self._segs(T.to_world(self.M, np.vstack([sector, sector[:1]])))}).draw(sh)
            sh.uniform_float('lineWidth', 2.5)
            sh.uniform_float('color', (1.0, 0.25, 0.2, 1.0) if bad else T.YELLOW)
            visible = VV.pattern_names(bpy.context)
            for name, B in lines:           # the outlines after the move, with their logical owners
                if name not in visible:
                    continue
                batch_for_shader(sh, 'LINES', {'pos': self._segs(T.to_world(self.M, np.vstack([B, B[:1]])))}).draw(sh)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.value == 'PRESS' and event.type in {'S', 'TAB', 'A'}:
            if event.type == 'S':
                self.mode = 'shift' if self.mode == 'pivot' else 'pivot'
            elif event.type == 'TAB':
                i = self.SIDES.index(self.side) if self.side in self.SIDES else 0
                self.side = self.SIDES[(i + 1) % len(self.SIDES)]
            else:
                self.apex_follows = not self.apex_follows
            self._update(context, event)
            self._status(context)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            self._update(context, event)
            return {'PASS_THROUGH'}
        if event.type == 'TIMER':
            self._settle(context)
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            h = self._update(context, event)
            if h is None:
                return {'RUNNING_MODAL'}
            self._end(context)
            return self.apply(context, h[2], h[3])
        return {'PASS_THROUGH'}

    def apply(self, context, eid, t):
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            out, side, turned, maps = self._move(sp, eid, t)
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc) or "cannot move there")
            return {'CANCELLED'}
        bad = D.problems(sp, list(out))
        if bad:
            self.report({'WARNING'}, rpt("Not moved: ") + bad[0])
            return {'CANCELLED'}
        msg = T.commit(context, sp, list(out), old_spec=old, rigid=maps)   # the 3D: the turned part turned back
        _select_seam(context, out[self.piece])
        how = "slid along its line" if side == 'shift' else tx("{0} side turned", side) + (
            tx(", {0} internal line(s) with it", turned) if turned else "")
        self.report({'INFO'}, rpt("Dart moved ({0})", how) + (rpt("; {0}", msg) if msg else ""))
        return {'FINISHED'}


class AC9_OT_SimCloseDart(bpy.types.Operator):
    """True the active dart's mouth: shape the outline across it so that, sewn, the line runs on smoothly (the dart cap; the twin too)"""
    bl_idname = "ac9_cloth_sim.close_dart"
    bl_label = "True Mouth"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return hasattr(DT, 'true_dart') and active_dart(context, store.get(context)[0])[1] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        piece, dt = active_dart(context, spec)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            out, info = DT.true_dart(sp, piece, dt['seam'])
        except (ValueError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc) or "cannot close")
            return {'CANCELLED'}
        bad = D.problems(sp, list(out))
        if bad:
            self.report({'WARNING'}, bad[0])
            return {'CANCELLED'}
        msg = T.commit(context, sp, list(out), old_spec=old)
        self.report({'INFO'}, info + (rpt("; {0}", msg) if msg else ""))
        return {'FINISHED'}


_fold = {'on': None}      # (piece, seam) whose folded mouth is drawn (Fold)
FOLD_MM = 80.0            # the outline drawn either side of the folded mouth


class AC9_OT_SimFoldDart(bpy.types.Operator):
    """Show the active dart folded shut: the outline either side of its mouth as it runs on when sewn (dotted, on the pattern)"""
    bl_idname = "ac9_cloth_sim.fold_dart"
    bl_label = "Fold"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return active_dart(context, store.get(context)[0])[1] is not None

    def execute(self, context):
        piece, dt = active_dart(context, store.get(context)[0])
        key = (piece, dt['seam'])
        _fold['on'] = None if _fold['on'] == key else key
        for a in context.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()
        return {'FINISHED'}


def folded_lines(spec, piece, seam):
    """The outline coming into the dart's mouth and the outline leaving it with leg 2 folded onto leg 1 (FOLD_MM
    each), in the pattern's mm: [(n, 2), (n, 2)], or None."""
    dt = next((x for x in DT.find_darts(spec, piece) if x['seam'] == seam), None)
    if dt is None:
        return None
    ol = S.Outline(D.piece(spec, piece))
    A, (P1, P2) = np.asarray(dt['A'], float), dt['mouth']
    s1, s2 = ol.s_of(dt['legs'][0], 0.0), ol.s_of(dt['legs'][1], 1.0)
    into = ol.at(s1 - np.linspace(FOLD_MM, 0.0, 33))
    out = ol.at(s2 + np.linspace(0.0, FOLD_MM, 33))
    u, v = P2 - A, P1 - A
    phi = np.arctan2(u[0] * v[1] - u[1] * v[0], u @ v)
    return [into, DT._rot(phi, A)(out)]


def _draw_fold():
    context = bpy.context
    try:
        if _fold['on'] is None or PV.layout_empty(context) is None:
            return
        spec, _ = store.get(context)
        piece, dt = active_dart(context, spec)
        if dt is None or (piece, dt['seam']) != _fold['on']:
            return
        if piece not in VV.pattern_names(context, spec):
            return
        lines = folded_lines(spec, *_fold['on'])
        if not lines:
            return
        M = PV.layout_matrix(context)
        region = context.region
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_float('lineWidth', 2.0)
        sh.uniform_float('color', (1.0, 0.55, 0.1, 1.0))
        for P in lines:                     # dotted: every other segment
            W = T.to_world(M, P)
            batch_for_shader(sh, 'LINES', {'pos': [tuple(p) for i in range(0, len(W) - 1, 2) for p in (W[i], W[i + 1])]}).draw(sh)
        gpu.state.blend_set('NONE')
    except Exception:
        pass


def draw_dart_rows(layout, context, spec, act, viewport=True):
    """In the selected seam's box when it is a dart (user 2026-10-21: the Darts panel under Draft was far from the
    seam picked): its angle and legs, Move, Fold, and True Mouth when its sewn mouth bends (a dart made by hand)."""
    if act is None or act['a']['piece'] != act['b']['piece']:
        return
    dt = next((x for x in DT.find_darts(spec, act['a']['piece']) if x['seam'] == act['id']), None)
    if dt is None:
        return
    box = layout.box()
    box.label(text=tx("Dart {0:.1f} deg, legs {1}", dt['deg'], U.fmt(bpy.context, dt['length'])) + ("" if act.get('enabled', True) else tx(" (off)")))
    row = box.row(align=True)
    if viewport:
        row.operator("ac9_cloth_sim.move_dart", text="Move")
    row.operator("ac9_cloth_sim.fold_dart", text="Fold", depress=_fold['on'] == (act['a']['piece'], act['id']))
    try:
        kink = DT.fold_kink(spec, act['a']['piece'], act['id'])
    except Exception:
        kink = 0.0
    if kink > 1.0:
        box.operator("ac9_cloth_sim.close_dart", text=tx("True Mouth ({0:.0f} deg)", kink))
    if not viewport:
        box.label(text="Move: right-click a leg, Edit Pieces", icon='INFO')


CLASSES = (AC9_OT_SimAddDart, AC9_OT_SimMoveDart, AC9_OT_SimCloseDart, AC9_OT_SimFoldDart)
_handles = []


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_fold, (), 'WINDOW', 'POST_VIEW'))


def unregister():
    while _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(), 'WINDOW')
        except ValueError:
            pass
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
