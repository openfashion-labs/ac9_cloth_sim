# SPDX-License-Identifier: GPL-3.0-or-later
"""Add Seam: click a stretch of outline (or of an internal line: a pocket sewn onto a panel) on one piece, then
on the other.

Steps: A start -> A end -> B start -> B end -> confirm. The two sides are paired from their start
clicks, so click both starts at the same end of the seam. While placing an end, the stretch runs the
shorter way round from the start; F runs it the other way. Points snap to the corners of the outline
(where one edge meets the next). Backspace steps back, Esc cancels, Enter / click confirms at the end.
A start clicked on the middle of an edge takes the whole edge (or internal line) as that side, starting at
its end nearer the click (user 2026-10-22, DECISIONS cloth-sim-edge-select-2026-10-22); Ctrl+click puts a
free start point there instead.
"""
import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import intersect_line_plane
from .i18n import rpt, tx

from . import overlay
from . import pattern_view as PV
from . import store
from . import view_visibility as VV
from . import toolbar as TB
from .core import seam_edit as SE
from .core import spec as S

SNAP_PX = 14          # pick distance to an outline
CORNER_PX = 10        # snap to a corner inside this


SEP = "\x1f"          # track key of an internal line: piece + SEP + line id (an outline's key is the piece name)


class LineTrack:
    """An internal line as a track a seam side can run along (open: no wrap-round)."""
    closed = False

    def __init__(self, pts):
        self.B = np.asarray(pts, float)
        self.cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(self.B, axis=0), axis=1))]
        self.L = float(self.cum[-1])

    def at(self, s):
        s = np.clip(np.asarray(s, float), 0.0, self.L)
        return np.stack([np.interp(s, self.cum, self.B[:, 0]), np.interp(s, self.cum, self.B[:, 1])], -1)


def tracks_of(spec, outlines):
    """Every enabled piece's outline (key = piece name) and internal lines (key = piece SEP line id)."""
    out = {}
    for pc in spec['pieces']:
        if not pc.get('enabled', True):
            continue
        out[pc['name']] = outlines[pc['name']]
        for it in pc.get('internals', []):
            if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                continue
            out[pc['name'] + SEP + it['id']] = LineTrack(it['pts'])
    return out


def is_closed(track):
    return getattr(track, 'closed', True)


class OutlineIndex:
    """All enabled outlines and internal lines as one segment array for nearest-point queries (layout mm)."""

    def __init__(self, spec, outlines, names=None):
        segA, segB, owner, s_at = [], [], [], []
        self.corners = {}
        self.tracks = tracks_of(spec, outlines)
        if names is not None:
            self.tracks = {key: tr for key, tr in self.tracks.items() if key.split(SEP)[0] in names}
        for key, tr in self.tracks.items():
            B = tr.B
            if is_closed(tr):
                n = len(B)
                segA.append(B)
                segB.append(np.roll(B, -1, 0))
                s_at.append(tr.cum[:n])
                self.corners[key] = np.array([tr.edges[e][0] for e in tr.order])
            else:
                n = len(B) - 1
                segA.append(B[:-1])
                segB.append(B[1:])
                s_at.append(tr.cum[:-1])
                self.corners[key] = np.array([0.0, tr.L])
            owner += [key] * n
        self.A = np.vstack(segA) if segA else np.zeros((0, 2))
        self.D = (np.vstack(segB) - self.A) if segA else np.zeros((0, 2))
        self.owner = np.array(owner)
        self.s0 = np.concatenate(s_at) if s_at else np.zeros(0)
        self.outlines = self.tracks

    def nearest(self, q, piece=None):
        """(piece, s, point, distance mm) of the outline point closest to q."""
        if not len(self.A):
            return None
        m = np.ones(len(self.A), bool) if piece is None else (self.owner == piece)
        if not m.any():
            return None
        A, D = self.A[m], self.D[m]
        ll = (D * D).sum(1)
        t = np.clip(((q - A) * D).sum(1) / np.maximum(ll, 1e-12), 0, 1)
        X = A + t[:, None] * D
        d = np.linalg.norm(X - q, axis=1)
        j = int(d.argmin())
        pc = self.owner[m][j]
        s = float(self.s0[m][j] + t[j] * np.sqrt(ll[j]))
        return pc, s, X[j], float(d[j])

    def snap_corner(self, piece, s, tol_mm):
        ol = self.outlines[piece]
        c = self.corners[piece]
        d = np.abs(((c - s) + ol.L / 2) % ol.L - ol.L / 2) if is_closed(ol) else np.abs(c - s)
        k = int(d.argmin())
        return (float(c[k]), True) if d[k] <= tol_mm else (s, False)


def make_side(outline, piece, s_from, s_to, direction):
    if not is_closed(outline):
        pc, line = piece.split(SEP)
        return {'piece': pc, 'line': line, 'from': round(s_from / outline.L, 6), 'to': round(s_to / outline.L, 6)}
    ef, tf = outline.edge_t(s_from)
    et, tt = outline.edge_t(s_to)
    return {'piece': piece, 'from': [ef, round(tf, 6)], 'to': [et, round(tt, 6)], 'dir': int(direction)}


def whole_edge(track, s):
    """(s start, s end, dir) of the whole edge (an outline) or line (an internal line) at s, starting at its end
    nearer s; None for a closed outline of one edge (it would start and end at the same point)."""
    if not is_closed(track):
        return (0.0, track.L, 1) if s <= track.L / 2 else (track.L, 0.0, -1)
    eid, t = track.edge_t(s)
    s0, L, _ = track.edges[eid]
    if L >= track.L - 1e-6:
        return None
    return (s0, s0 + L, 1) if t <= 0.5 else (s0 + L, s0, -1)


def shorter_dir(outline, s0, s1):
    if not is_closed(outline):
        return 1 if s1 >= s0 else -1
    ccw = (s1 - s0) % outline.L
    return 1 if ccw <= outline.L - ccw else -1


class _AddSeamBase:
    """The picking, drawing and keys of Add Seam, shared with Add Seam (M:N). A plain mixin: subclassing a registered
    operator loses its Python class in Blender (draft_tool's Draw Line, 2026-10)."""

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and store.get(context)[0] is not None and PV.layout_empty(context) is not None)

    def _placing_end(self):
        """Placing the end of a stretch (its start clicked)."""
        return self.step in (1, 3)

    def _extra_dots(self):
        return ()

    # ---------------------------------------------------------------- geometry
    def _mouse_mm(self, context, mx, my):
        if not VV.allows_pattern(context):
            return None
        region, rv3d = context.region, context.region_data
        o = region_2d_to_origin_3d(region, rv3d, (mx, my))
        v = region_2d_to_vector_3d(region, rv3d, (mx, my))
        Minv = self.M.inverted()
        o_l, v_l = Minv @ o, (Minv.to_3x3() @ v)
        hit = intersect_line_plane(o_l, o_l + v_l * 1000.0, Vector((0, 0, 0)), Vector((0, 0, 1)))
        if hit is None:
            return None
        return np.array([hit.x / PV.MM, hit.y / PV.MM])

    def _px_to_mm(self, context, mx, my):
        a = self._mouse_mm(context, mx, my)
        b = self._mouse_mm(context, mx + 10, my)
        if a is None or b is None:
            return 1.0
        return max(float(np.linalg.norm(b - a)) / 10.0, 1e-6)

    def _world(self, P2):
        return [self.M @ Vector((float(x) * PV.MM, float(y) * PV.MM, 0.0)) for x, y in np.atleast_2d(P2)]

    # ---------------------------------------------------------------- state
    def _hover_update(self, context, mx, my):
        visible = VV.pattern_names(context, self.spec)
        if visible != self._visible:
            self._visible = visible
            self.idx = OutlineIndex(self.spec, self.source_outlines, names=visible)
            self.outlines = self.idx.tracks
        q = self._mouse_mm(context, mx, my)
        self.hover = None
        self.hover_edge = None
        if q is None or (self._placing_end() and self.cur_piece not in self.outlines):
            return
        mmpx = self._px_to_mm(context, mx, my)
        # an end stays on the track (outline or internal line) of its start
        piece = self.cur_piece if self._placing_end() else None
        hit = self.idx.nearest(q, piece)
        if self._placing_end() and (hit is None or hit[3] > SNAP_PX * mmpx):
            # the start may be where an internal line meets the outline: the click took the outline, the end is
            # on the line -- hop to a track of the same piece that passes through the start point
            p0 = self.outlines[self.cur_piece].at(self.cur_start)
            base = self.cur_piece.split(SEP)[0]
            for key in self.outlines:
                if key == self.cur_piece or key.split(SEP)[0] != base:
                    continue
                h0 = self.idx.nearest(p0, key)
                h1 = self.idx.nearest(q, key)
                if h0 and h1 and h0[3] < 0.5 and h1[3] <= SNAP_PX * mmpx:
                    self.cur_piece, self.cur_start, self.dir_forced = key, h0[1], False
                    hit = h1
                    break
        if hit is None or hit[3] > SNAP_PX * mmpx:
            return
        pc, s, _, _ = hit
        s, corner = self.idx.snap_corner(pc, s, CORNER_PX * mmpx)
        self.hover = (pc, s, corner)
        if not self._placing_end() and not corner and not getattr(self, 'ctrl', False):
            w = whole_edge(self.outlines[pc], s)
            if w is not None:
                self.hover_edge = (pc,) + w
        if self._placing_end() and not self.dir_forced:
            self.cur_dir = shorter_dir(self.outlines[pc], self.cur_start, s)

    def _preview_side(self):
        """The stretch being placed (piece, s0, s1, dir) or None."""
        if self._placing_end() and self.hover is not None:
            return (self.cur_piece, self.cur_start, self.hover[1], self.cur_dir)
        return getattr(self, 'hover_edge', None)

    def _side_poly(self, piece, s0, s1, d):
        ol = self.outlines[piece]
        if not is_closed(ol):
            return ol.at(np.linspace(s0, s1, max(2, int(abs(s1 - s0) / 2.0) + 1)))
        span = ((s1 - s0) % ol.L) if d > 0 else -((s0 - s1) % ol.L)
        n = max(2, int(abs(span) / 2.0) + 1)
        return ol.at(s0 + np.linspace(0.0, span, n))

    def _status(self, context):
        names = ["A: click an edge (whole) or a START (corner / Ctrl+click)", "A: click the END",
                 "B: click an edge or a START (same end as A's start)", "B: click the END",
                 ">>> Enter / click: add the seam <<<"]
        txt = names[min(self.step, 4)]
        if self.step in (1, 3) and is_closed(self.outlines[self.cur_piece]):
            txt += tx("   F: other way round ({0})", 'ccw' if self.cur_dir > 0 else 'cw')
        elif self.step in (1, 3):
            txt += "   (along an internal line)"
        if self.sides:
            L = [S.polyline_len(self._side_poly(*sd)) for sd in self.sides]
            pv = self._preview_side()
            if pv:
                L.append(S.polyline_len(self._side_poly(*pv)))
            from .shape_tool import fmt
            txt += "   lengths " + " / ".join(fmt(context, x) for x in L)
            if len(L) == 2:
                txt += tx("  (diff {0})", fmt(context, L[0] - L[1], True))
        elif self._preview_side():
            from .shape_tool import fmt
            txt += tx("   length {0}", fmt(context, S.polyline_len(self._side_poly(*self._preview_side()))))
        txt += "   Backspace: back   Esc: cancel"
        context.area.header_text_set(txt)

    # ---------------------------------------------------------------- draw
    def _draw(self, context):
        try:
            if bpy.context.area is None or bpy.context.area.as_pointer() != self._draw_area \
                    or not VV.allows_pattern(bpy.context):
                return
            visible = VV.pattern_names(bpy.context, self.spec)
            region = context.region
            sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            sh.bind()
            sh.uniform_float('viewportSize', (region.width, region.height))
            polys = [(sd, (0.2, 1.0, 0.4, 1.0)) for sd in self.sides]
            pv = self._preview_side()
            if pv:
                polys.append((pv, (1.0, 1.0, 0.3, 1.0)))
            for sd, c in polys:
                if sd[0].split(SEP)[0] not in visible:
                    continue
                W = self._world(self._side_poly(*sd))
                pts = [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
                sh.uniform_float('lineWidth', 7.0)
                sh.uniform_float('color', c)
                batch_for_shader(sh, 'LINES', {'pos': pts}).draw(sh)
                arr = overlay._arrow(np.array([tuple(w) for w in W]), size=0.015)
                if arr:
                    batch_for_shader(sh, 'LINES', {'pos': [tuple(p) for p in arr]}).draw(sh)
            # the start of the stretch being placed and the hover point
            dots = list(self._extra_dots())
            if self._placing_end():
                dots.append((self.cur_piece, self.cur_start, (1.0, 1.0, 0.3, 1.0)))
            if self.hover_edge is not None:
                dots.append((self.hover_edge[0], self.hover_edge[1], (1.0, 1.0, 0.3, 1.0)))
            elif self.hover is not None:
                dots.append((self.hover[0], self.hover[1], (1.0, 0.3, 0.3, 1.0) if self.hover[2]
                             else (1.0, 1.0, 1.0, 1.0)))
            sp = gpu.shader.from_builtin('UNIFORM_COLOR')
            sp.bind()
            gpu.state.point_size_set(12.0)
            for pc, s, c in dots:
                if pc.split(SEP)[0] not in visible:
                    continue
                w = self._world(self.outlines[pc].at(s))[0]
                sp.uniform_float('color', c)
                batch_for_shader(sp, 'POINTS', {'pos': [tuple(w)]}).draw(sp)
            gpu.state.point_size_set(1.0)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    # ---------------------------------------------------------------- run
    def invoke(self, context, event):
        if not VV.allows_pattern(context):
            return {'CANCELLED'}
        spec, outlines = store.get(context)
        self.spec, self.source_outlines = spec, outlines
        self._visible = VV.pattern_names(context, spec)
        self.idx = OutlineIndex(spec, outlines, names=self._visible)
        self.outlines = self.idx.tracks
        self.M = PV.layout_matrix(context)
        self.step = 0
        self.sides = []            # finished stretches: (piece, s0, s1, dir)
        self.cur_piece, self.cur_start, self.cur_dir, self.dir_forced = None, 0.0, 1, False
        self.hover = self.hover_edge = None
        self.ctrl = event.ctrl
        self._draw_area = context.area.as_pointer()
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        self._hover_update(context, event.mouse_region_x, event.mouse_region_y)
        self._status(context)
        return TB.start_modal(self, context, event)

    def _finish(self, context, canceled):
        from . import pre_click
        pre_click.clear(context)
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.area.header_text_set(None)
        context.area.tag_redraw()
        return {'CANCELLED'} if canceled else {'FINISHED'}

    def _click(self, context):
        if self.step == 4:
            return self._commit(context)
        if self.hover is None:
            return None
        pc, s, _ = self.hover
        if self.step in (0, 2) and self.hover_edge is not None:
            self.sides.append(self.hover_edge)
            self.step += 2
            return None
        if self.step in (0, 2):
            self.cur_piece, self.cur_start, self.dir_forced = pc, s, False
            self.step += 1
        else:
            tr = self.outlines[pc]
            gap = (abs(((s - self.cur_start) + tr.L / 2) % tr.L - tr.L / 2) if is_closed(tr)
                   else abs(s - self.cur_start))
            if gap < 1e-3:
                return None                                  # an end on top of its start
            self.sides.append((self.cur_piece, self.cur_start, s, self.cur_dir))
            self.step += 1
        return None

    def _commit(self, context):
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        used = {sm['id'] for sm in spec['seams']}
        k = 1
        while f"U{k}" in used:
            k += 1
        sa, sb = [make_side(self.outlines[p], p, s0, s1, d) for p, s0, s1, d in self.sides]
        spec['seams'].append({'id': f"U{k}", 'a': sa, 'b': sb, 'source': 'user', 'check': ''})
        from .core import seam_twin as ST          # pieces edited symmetrically: the mirror seam too (2026-10-27)
        mirrored = ST.mirror_within(spec, [f"U{k}"])
        # both ends on left / right pairs: between their twins too (user 2026-11-04)
        mirrored += ST.mirror_onto_twins(spec, [f"U{k}"])[0]
        store.put(context, spec)
        props = context.scene.ac9_cloth_sim
        props.seam_index = len(props.seams) - 1 - len(mirrored)
        overlay.invalidate()
        self.report({'INFO'}, rpt("Added seam U{0}", k) + (rpt(" and its mirror image {0}", ', '.join(mirrored))
                                                         if mirrored else ""))
        return self._finish(context, False)

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}
        self.ctrl = event.ctrl
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._hover_update(context, event.mouse_region_x, event.mouse_region_y)
            self._status(context)
            return {'RUNNING_MODAL'}
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            return self._finish(context, True)
        if event.type == 'F' and self.step in (1, 3) and is_closed(self.outlines[self.cur_piece]):
            self.cur_dir, self.dir_forced = -self.cur_dir, True
        elif event.type == 'BACK_SPACE':
            if self.step in (1, 3):
                self.step -= 1
            elif self.step in (2, 4):
                p, s0, _, d = self.sides.pop()
                self.cur_piece, self.cur_start, self.cur_dir, self.dir_forced = p, s0, d, True
                self.step -= 1
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
            if event.type != 'LEFTMOUSE' and self.step != 4:
                return {'RUNNING_MODAL'}
            r = self._click(context)
            if r is not None:
                return r
        self._hover_update(context, event.mouse_region_x, event.mouse_region_y)
        self._status(context)
        return {'RUNNING_MODAL'}


class AC9_OT_SimAddSeam(_AddSeamBase, bpy.types.Operator):
    """Add a seam: click the start and end of a stretch of outline on one piece, then on the other piece (starts at the same end of the seam); Enter adds it"""
    bl_idname = "ac9_cloth_sim.add_seam"
    bl_label = "Add Seam"
    bl_options = {'REGISTER', 'UNDO'}


class AC9_OT_SimAddSeamMN(_AddSeamBase, bpy.types.Operator):
    """Add an M:N seam: on side A click the start and end of each stretch (several, on one piece or more), Enter; then the same on side B, Enter. The two sides are cut by the proportion of their lengths into seams of one group"""
    bl_idname = "ac9_cloth_sim.add_seam_mn"
    bl_label = "Add Seam (M:N)"
    bl_options = {'REGISTER', 'UNDO'}

    def _placing_end(self):
        return self.half == 1

    def invoke(self, context, event):
        self.side, self.half, self.runs = 0, 0, [[], []]
        return super().invoke(context, event)

    def _sync(self):
        self.sides = self.runs[0] + self.runs[1]

    def _lengths(self, k):
        L = [S.polyline_len(self._side_poly(*sd)) for sd in self.runs[k]]
        pv = self._preview_side() if k == self.side else None
        if pv:
            L.append(S.polyline_len(self._side_poly(*pv)))
        return L

    def _status(self, context):
        ab = "AB"[self.side]
        if self.half == 0:
            txt = tx("M:N side {0}: click an edge (whole) or a START (corner / Ctrl+click)", ab)
            if self.runs[self.side]:
                txt += tx("   >>> Enter: {0} <<<", 'go to side B' if self.side == 0 else 'add the seam')
        else:
            txt = tx("M:N side {0}: click the END", ab)
            if is_closed(self.outlines[self.cur_piece]):
                txt += tx("   F: other way round ({0})", 'ccw' if self.cur_dir > 0 else 'cw')
        la, lb = self._lengths(0), self._lengths(1)
        txt += f"   A {sum(la):.1f} mm ({len(la)})"
        if self.side == 1:
            txt += f"   B {sum(lb):.1f} mm ({len(lb)})"
            if la and lb:
                txt += tx("  diff {0:+.1f}", sum(la) - sum(lb))
        if self.runs[0] and self.runs[1] and self.half == 0:
            txt += tx("   -> {0} seam(s)", len(self._pairs()))
        txt += "   Backspace: back   Esc / right-click: cancel"
        context.area.header_text_set(txt)

    def _pairs(self):
        return SE.mn_seams(self.runs[0], self.runs[1], self.outlines)

    def _extra_dots(self):
        """Where the two sides are cut (by the proportion of their lengths), once both have a stretch."""
        if not (self.runs[0] and self.runs[1]):
            return ()
        la = [abs(SE.signed_len(self.outlines[k], s0, s1, d)) for k, s0, s1, d in self.runs[0]]
        lb = [abs(SE.signed_len(self.outlines[k], s0, s1, d)) for k, s0, s1, d in self.runs[1]]
        _, at_a = SE.mn_sides(self.runs[0], self.outlines)
        _, at_b = SE.mn_sides(self.runs[1], self.outlines)
        out = []
        for (a0, b0), (a1, b1) in zip(SE.mn_breaks(la, lb)[:-1], SE.mn_breaks(la, lb)[1:]):
            ka, sa, _, _ = at_a(a0, a1)
            kb, sb, _, _ = at_b(b0, b1)
            out += [(ka, sa, CUT_COLOR), (kb, sb, CUT_COLOR)]
        return out[2:]                                  # the cuts, not the seam's start

    def _click(self, context):
        if self.hover is None:
            return None
        pc, s, _ = self.hover
        if self.half == 0 and self.hover_edge is not None:
            self.runs[self.side].append(self.hover_edge)
            self._sync()
            return None
        if self.half == 0:
            self.cur_piece, self.cur_start, self.dir_forced = pc, s, False
            self.half = 1
            return None
        tr = self.outlines[pc]
        gap = (abs(((s - self.cur_start) + tr.L / 2) % tr.L - tr.L / 2) if is_closed(tr)
               else abs(s - self.cur_start))
        if gap < 1e-3:
            return None
        self.runs[self.side].append((self.cur_piece, self.cur_start, s, self.cur_dir))
        self.half = 0
        self._sync()
        return None

    def _enter(self, context):
        if self.half == 1 or not self.runs[self.side]:
            return None
        if self.side == 0:
            self.side = 1
            return None
        return self._commit(context)

    def _back(self):
        if self.half == 1:
            self.half = 0
        elif self.runs[self.side]:
            p, s0, _, d = self.runs[self.side].pop()
            self.cur_piece, self.cur_start, self.cur_dir, self.dir_forced = p, s0, d, True
            self.half = 1
        elif self.side == 1:
            self.side = 0
        self._sync()

    def _commit(self, context):
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        used = {sm['id'] for sm in spec['seams']}
        pairs = self._pairs()
        ids, k = [], 1
        while len(ids) < len(pairs):
            if f"U{k}" not in used:
                ids.append(f"U{k}")
            k += 1
        # one group: Edit Seams moves the cut points together, as a BOM M:N seam's (core/seam_edit.py)
        group = ("UG" + ids[0][1:]) if len(pairs) > 1 else None
        for sid, (sa, sb) in zip(ids, pairs):
            sm = {'id': sid, 'a': sa, 'b': sb, 'source': 'user', 'check': ''}
            if group:
                sm['group'] = group
            spec['seams'].append(sm)
        from .core import seam_twin as ST          # pieces edited symmetrically: the mirror seams too (2026-10-27)
        mirrored = ST.mirror_within(spec, ids)
        mirrored += ST.mirror_onto_twins(spec, ids)[0]     # both ends on left / right pairs (user 2026-11-04)
        if group and mirrored:                     # the mirror image is a group of its own
            for x in spec['seams']:
                if x['id'] in mirrored:
                    x['group'] = group + "m"
        store.put(context, spec)
        props = context.scene.ac9_cloth_sim
        props.seam_index = len(props.seams) - 1 - len(mirrored)
        overlay.invalidate()
        self.report({'INFO'}, (rpt("Added an M:N seam: {0}", ', '.join(ids)) if group else rpt("Added seam {0}", ids[0]))
                    + (rpt(" and its mirror image {0}", ', '.join(mirrored)) if mirrored else ""))
        return self._finish(context, False)

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}
        self.ctrl = event.ctrl
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._hover_update(context, event.mouse_region_x, event.mouse_region_y)
            self._status(context)
            return {'RUNNING_MODAL'}
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            return self._finish(context, True)
        if event.type == 'F' and self.half == 1 and is_closed(self.outlines[self.cur_piece]):
            self.cur_dir, self.dir_forced = -self.cur_dir, True
        elif event.type == 'BACK_SPACE':
            self._back()
        elif event.type in {'RET', 'NUMPAD_ENTER'}:
            r = self._enter(context)
            if r is not None:
                return r
        elif event.type == 'LEFTMOUSE':
            self._click(context)
        self._hover_update(context, event.mouse_region_x, event.mouse_region_y)
        self._status(context)
        return {'RUNNING_MODAL'}


CUT_COLOR = (0.4, 0.8, 1.0, 1.0)
CLASSES = (AC9_OT_SimAddSeam, AC9_OT_SimAddSeamMN)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
