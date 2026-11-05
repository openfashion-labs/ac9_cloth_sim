# SPDX-License-Identifier: GPL-3.0-or-later
"""Drawing shapes on the flat pattern, the grid under it and the snap marks (Blender only). Design: 2609_Apparel_CAD/
docs/design/DESIGN_draw_basics_2026-10-20.md F1, F2, F3, F5, F6 (user 2026-10-20, Fable review r1).

Rectangle / Ellipse: press and drag from corner to corner (or click two corners: a press released where it started
waits for a second click). Shift after pressing: a square / circle, Alt after pressing: from the center
(a tool's key map only gets a plain click, so a modifier held before pressing goes to Blender). Type the width, Tab,
the height (in the scene's length unit), Enter. Ctrl: no snapping. Internal Rectangle / Ellipse draw the same inside
a piece: a closed internal shape (a pocket's place, a button's mark), not a new piece.
"""
import math
import bpy
import gpu
import numpy as np
from bpy.props import BoolProperty, EnumProperty
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from .i18n import CTX, rpt, tx

from . import pattern_view as PV
from . import store
from . import view_visibility as VV
from .core import draft as D
from .core import iline as IL
from .core import snap as SN

YELLOW = (1.0, 0.85, 0.2, 1.0)
SNAP_PX = 12                  # snap within this many pixels (the corner pick of Edit Pattern)
CLICK_PX = 5                  # a press released within this is a click: the second corner is clicked
MIN_MM = 5.0                  # the smallest side of a shape

# ---------------------------------------------------------------- units (the scene's length unit; the pattern is mm)

_UNITS = {'MILLIMETERS': (1.0, "mm"), 'CENTIMETERS': (10.0, "cm"), 'METERS': (1000.0, "m"),
          'MICROMETERS': (0.001, "µm"), 'KILOMETERS': (1e6, "km")}


def unit(context):
    """(mm per unit, symbol) of the scene's Length unit; Adaptive and the rest show mm."""
    try:
        us = context.scene.unit_settings
        if us.system == 'METRIC':
            return _UNITS.get(us.length_unit, _UNITS['MILLIMETERS'])
    except Exception:
        pass
    return _UNITS['MILLIMETERS']


_DIGITS = {1.0: 1, 10.0: 2, 1000.0: 4}          # mm 1, cm 2 (no 0.1 mm lost), m 4; the rest 3 (DECISIONS
                                                # cloth-sim-dimension-display-2026-10-23 b-4)


def num(context, mm, signed=False):
    """mm in the scene's Length unit, the number only (the boxes on the pattern)."""
    f = unit(context)[0]
    return f"{mm / f:{'+' if signed else ''}.{_DIGITS.get(f, 3)}f}"


def fmt(context, mm, signed=False):
    """mm in the scene's Length unit with its symbol: "123.4 mm", "12.34 cm"."""
    return f"{num(context, mm, signed)} {unit(context)[1]}"


def typed_mm(context, txt):
    """A typed number in the scene's unit as mm, else None."""
    try:
        return float(txt) * unit(context)[0]
    except ValueError:
        return None


def set_mm_if_default(context):
    """A new pattern: the scene's Length unit to Millimeters when it is still Blender's default (Meters). Only how
    lengths are shown: sizes and the Unit Scale stay (user 2026-10-20; 5.2: a cube stays 2 m, scale 1.0)."""
    try:
        us = context.scene.unit_settings
        if us.system == 'METRIC' and us.length_unit == 'METERS' and abs(us.scale_length - 1.0) < 1e-9:
            us.length_unit = 'MILLIMETERS'
    except Exception:
        pass


# ---------------------------------------------------------------- snapping in a tool

def snap_settings(context):
    wm = context.window_manager
    return getattr(wm, 'ac9_pattern_snap', True), getattr(wm, 'ac9_grid_snap', False)


class Snapper:
    """A tool's snapping: targets gathered once, then point(q) per mouse move. kinds: core/snap.py names."""

    def __init__(self, context, kinds, skip=()):
        spec, outlines = store.get(context)
        self._source_spec, self.outlines = spec, outlines
        self._skip, self._visible = tuple(skip), None
        self.spec, self.targets = None, None
        self.kinds = set(kinds)
        self.mark = None
        self.gaps = []                 # [(a, b, mm, hot)]: the point's gaps on its piece (DECISIONS
        self._segs = {}                # cloth-sim-drag-distance-snap-2026-10-23, the drawing tools)
        self._at = None                # the piece measured last
        self._refresh(context)

    def _refresh(self, context):
        """Targets belong to the current viewport, not to the shared selection."""
        names = VV.pattern_names(context, self._source_spec)
        if names == self._visible:
            return
        self._visible = names
        self.spec = (dict(self._source_spec, pieces=[pc for pc in self._source_spec['pieces']
                                                    if pc['name'] in names])
                     if self._source_spec is not None else None)
        self.targets = SN.Targets(self.spec, self.outlines, skip=self._skip) if self.spec is not None else None
        self._segs.clear()
        if self._at not in names:
            self._at = None

    def point(self, context, q, mm_per_px, ctrl=False, piece=None):
        """(q snapped, kind, piece). Ctrl turns the snapping the other way while held: no snapping and whole mm
        when the header's magnet is on, the snapping when it is off (DECISIONS cloth-sim-draw-basics-2026-10-20).
        No point to snap to: the middle, an even spacing or the distance another line has, within 8 px, on the
        piece (piece, else the one under q), as Edit Pattern's drag. kind 'gap' then."""
        self._refresh(context)
        on, grid_on = snap_settings(context)
        if ctrl and on:
            p, kind, pc = np.round(q), None, None
            on = False
        else:
            on = on or ctrl
            kinds = set(self.kinds) if on else set()
            grid = None
            if grid_on:
                kinds.add('grid')
                grid = SN.grid_step(mm_per_px)
            p, kind, pc = SN.snap(q, SNAP_PX * mm_per_px, self.targets, kinds, grid)
        p = self._gauge(p, piece or pc, mm_per_px if on and kind in (None, 'grid') else None)
        if self._snapped:
            kind = 'gap'
        self.mark = (p, kind) if kind else None
        return p, kind, pc

    def _piece(self, name, q):
        """(A, B, same distances) of the piece name, else of the piece q is in; None when q is on no piece."""
        if self.spec is None:
            return None
        if name is not None and name not in self._visible:
            return None
        if name is None:
            from .draft_tool import _inside
            name = next((pc['name'] for pc in reversed(self.spec['pieces']) if pc.get('enabled', True)
                         and pc['name'] in self.outlines and _inside(self.outlines[pc['name']].B, q)), None)
        self._at = name
        if name is None:
            return None
        if name not in self._segs:
            pc = next((x for x in self.spec['pieces'] if x['name'] == name), None)
            if pc is None:
                return None
            A, B = SN.piece_segments(pc)
            self._segs[name] = (A, B, SN.same_refs(pc, SN.point_probe((0.0, 0.0))))
        return self._segs[name]

    def _gauge(self, p, piece, mm_per_px):
        """The gaps from p on its piece; mm_per_px: snap to them too. Returns p (moved when it snapped)."""
        self._snapped = False
        seg = self._piece(piece, p)
        if seg is None:
            self.gaps = []
            return p
        A, B, refs = seg
        p = np.asarray(p, float)
        pr = SN.point_probe(p)
        hits = SN.gauge(pr, A, B)
        what = []
        if mm_per_px is not None:
            s, what = SN.gap_snap(pr, hits, SN.GAP_PX * mm_per_px, refs)
            if what:
                p = p + s
                pr = SN.point_probe(p)
                hits = SN.gauge(pr, A, B)
                self._snapped = True
        self.gaps = SN.gap_lines(pr, hits, what)
        return p

    def on_ray(self, context, o, q, mm_per_px, ctrl=False, piece=None):
        """Shift held: q stays on the ray from o (its angle locked) and snaps only where that ray meets an outline
        or an internal line within 12 px, so the angle holds on the outline too (user 2026-10-23). (q, kind)."""
        self._refresh(context)
        on = snap_settings(context)[0] != bool(ctrl)
        o, q = np.asarray(o, float), np.asarray(q, float)
        r = float(np.linalg.norm(q - o))
        hit = None
        if on and r > 1e-9 and self.spec is not None:
            if 'all' not in self._segs:
                S_ = [SN.piece_segments(pc) for pc in self.spec['pieces'] if pc.get('enabled', True)]
                self._segs['all'] = (np.vstack([a for a, b in S_]) if S_ else np.zeros((0, 2)),
                                     np.vstack([b for a, b in S_]) if S_ else np.zeros((0, 2)))
            u = (q - o) / r
            t = SN.ray_hits(*self._segs['all'], o, u)
            if len(t):
                t0 = t[int(np.abs(t - r).argmin())]
                if abs(t0 - r) <= SNAP_PX * mm_per_px:
                    hit = o + u * t0
        p = hit if hit is not None else q
        self._gauge(p, piece if piece is not None else self._at, None)
        self.mark = (p, 'edge') if hit is not None else None
        return p, ('edge' if hit is not None else None)

    def measure(self, p):
        """The gaps again for p moved after point() (Shift's angle, a typed length), on the same piece, no snap."""
        self._gauge(p, self._at, None)
        if self.mark is not None and self.mark[1] == 'gap':
            self.mark = None


_marks = {'area': None, 'mark': None, 'M': None, 'tool': None, 'gaps': []}


def show_mark(context, snapper, M, hover=False):
    """hover: set by a tool's hover before its click (pre_click.py), drawn only while that tool is the active one"""
    from . import toolbar as TB
    _marks.update(area=context.area.as_pointer() if context.area else None, mark=snapper.mark if snapper else None,
                  M=M, tool=TB.active_tool(context) if hover else None, gaps=list(snapper.gaps) if snapper else [])


def clear_mark():
    _marks.update(area=None, mark=None, M=None, gaps=[])


def _draw_gaps_px(ctx, M, lines):
    """The point's gaps as dashed lines (6 px dashes) with their mm on dark boxes, as Edit Pattern's drag."""
    from .draft_tool import to_world, draw_gap_labels, GAP_COLOR, GAP_HOT
    plain, hot = [], []
    for a, b, mm, h in lines:
        pa = location_3d_to_region_2d(ctx.region, ctx.region_data, to_world(M, [a])[0])
        pb = location_3d_to_region_2d(ctx.region, ctx.region_data, to_world(M, [b])[0])
        if pa is None or pb is None:
            continue
        d = pb - pa
        n = max(1, int(d.length / 6.0))
        tgt = hot if h else plain
        tgt += [tuple(pa + d * (k / n)) for j in range(0, n, 2) for k in (j, j + 1)]
    gpu.state.blend_set('ALPHA')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
    sh.uniform_float('lineWidth', 1.5)
    for segs, col in ((plain, GAP_COLOR), (hot, GAP_HOT)):
        if segs:
            sh.uniform_float('color', col)
            batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
    draw_gap_labels(ctx, M, lines)


def _draw_mark():
    ctx = bpy.context
    if not VV.allows_pattern(ctx):
        return
    m = _marks['mark']
    if (m is None and not _marks['gaps']) or ctx.area is None or ctx.area.as_pointer() != _marks['area'] \
            or ctx.region_data is None:
        return
    if _marks['tool'] is not None:
        from . import toolbar as TB
        if TB.active_tool(ctx) != _marks['tool']:
            return
    from .draft_tool import to_world
    if _marks['gaps']:
        try:
            _draw_gaps_px(ctx, _marks['M'], _marks['gaps'])
        except Exception:
            pass
    if m is None:
        return
    p = location_3d_to_region_2d(ctx.region, ctx.region_data, to_world(_marks['M'], m[0])[0])
    if p is None:
        return
    x, y, r = p.x, p.y, 6.0
    kind = m[1]
    if kind == 'corner':
        pts = [(x - r, y - r), (x + r, y - r), (x + r, y - r), (x + r, y + r), (x + r, y + r), (x - r, y + r),
               (x - r, y + r), (x - r, y - r)]
    elif kind == 'mid':
        pts = [(x - r, y - r), (x + r, y - r), (x + r, y - r), (x, y + r), (x, y + r), (x - r, y - r)]
    elif kind == 'notch':
        pts = [(x, y - r), (x + r, y), (x + r, y), (x, y + r), (x, y + r), (x - r, y), (x - r, y), (x, y - r)]
    elif kind == 'grid':
        pts = [(x - r, y), (x + r, y), (x, y - r), (x, y + r)]
    else:                                                  # on a line (edge / internal line / an internal line's end)
        pts = [(x - r, y - r), (x + r, y + r), (x - r, y + r), (x + r, y - r)]
    gpu.state.blend_set('ALPHA')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
    sh.uniform_float('lineWidth', 2.0)
    sh.uniform_float('color', (0.3, 1.0, 0.5, 1.0))
    batch_for_shader(sh, 'LINES', {'pos': pts}).draw(sh)
    gpu.state.blend_set('NONE')


def _draw_fold():
    """Red rings where the last refused carry folds (draft_tool.fold_marks), while the pattern is the refused one."""
    from . import draft_tool as DT
    from . import store
    fm = DT.fold_marks
    ctx = bpy.context
    if not VV.allows_pattern(ctx):
        return
    if fm.get('piece') is not None and fm['piece'] not in VV.pattern_names(ctx):
        return
    if fm['points'] is None or fm['key'] is None or fm['key'] != store._cache['key'] or ctx.region_data is None:
        return
    from . import pattern_view as PV
    M = PV.layout_matrix(ctx)
    pts = []
    for w in DT.to_world(M, fm['points']):
        p = location_3d_to_region_2d(ctx.region, ctx.region_data, w)
        if p is None:
            continue
        ring = [(p.x + 12.0 * math.cos(a), p.y + 12.0 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 25)]
        for k in range(24):
            pts += [ring[k], ring[k + 1]]
    if not pts:
        return
    gpu.state.blend_set('ALPHA')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
    sh.uniform_float('lineWidth', 3.0)
    sh.uniform_float('color', (1.0, 0.15, 0.1, 1.0))
    batch_for_shader(sh, 'LINES', {'pos': pts}).draw(sh)
    gpu.state.blend_set('NONE')


# ---------------------------------------------------------------- the grid under the pattern

_grid_cache = {'key': None, 'bbox': None}


def _pattern_bbox(context):
    spec, outlines = store.get(context)
    if spec is None or not outlines:
        return None
    visible = VV.pattern_names(context, spec)
    key = (id(outlines), visible)
    if _grid_cache['key'] != key:
        B = [np.asarray(o.B, float) for n, o in outlines.items() if n in visible]
        _grid_cache.update(key=key, bbox=(np.vstack(B).min(0), np.vstack(B).max(0)) if B else None)
    return _grid_cache['bbox']


def _draw_grid():
    ctx = bpy.context
    try:
        if not VV.allows_pattern(ctx):
            return
        if not getattr(ctx.window_manager, 'ac9_pattern_grid', False) or not ctx.scene.ac9_cloth_sim.pattern_tools:
            return
        if ctx.region_data is None or PV.layout_empty(ctx) is None:
            return
        bb = _pattern_bbox(ctx)
        if bb is None:
            return
        from .draft_tool import mouse_mm, px_to_mm
        M = PV.layout_matrix(ctx)
        lo, hi = bb[0] - 100.0, bb[1] + 100.0
        if not ctx.region_data.is_perspective:            # only what is on screen
            c = [mouse_mm(ctx, M, x, y) for x, y in ((0, 0), (ctx.region.width, ctx.region.height))]
            if all(v is not None for v in c):
                vlo, vhi = np.minimum(*c), np.maximum(*c)
                lo, hi = np.maximum(lo, vlo), np.minimum(hi, vhi)
                if (hi <= lo).any():
                    return
        mmpx = px_to_mm(ctx, M, ctx.region.width // 2, ctx.region.height // 2)
        minor, major, _ = SN.grid_lines(lo, hi, mmpx)
        z = -0.2 * PV.MM                                   # just under the pieces
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        sh.bind()
        sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
        sh.uniform_float('lineWidth', 1.0)
        for segs, col in ((minor, (0.6, 0.6, 0.6, 0.15)), (major, (0.7, 0.7, 0.7, 0.35))):
            if not segs:
                continue
            pos = []
            for x0, y0, x1, y1 in segs:
                pos += [tuple(M @ PV.Vector((x0 * PV.MM, y0 * PV.MM, z))), tuple(M @ PV.Vector((x1 * PV.MM, y1 * PV.MM, z)))]
            sh.uniform_float('color', col)
            batch_for_shader(sh, 'LINES', {'pos': pos}).draw(sh)
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')
    except Exception:
        pass


# ---------------------------------------------------------------- the shape operator

def shape_points(kind, lo, hi):
    if kind == 'RECT':
        return D.rect_points(lo, hi)
    return D.ellipse_points((lo + hi) / 2, (hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2)


class AC9_OT_SimDrawShape(bpy.types.Operator):
    """Draw a rectangle or an ellipse on the pattern: drag from corner to corner (or click two corners). Shift: square, Alt: from the center, type width Tab height, Ctrl: no snapping"""
    bl_idname = "ac9_cloth_sim.draw_shape"
    bl_label = "Draw Shape"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    shape: EnumProperty(items=[('RECT', "Rectangle", ""), ('ELLIPSE', "Ellipse", "")])
    internal: BoolProperty(name="Internal", default=False, translation_context=CTX,
                           description="Draw inside a piece: a closed internal shape, not a new piece")

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def _label(self):
        return ("Internal " if self.internal else "") + ("Rectangle" if self.shape == 'RECT' else "Ellipse")

    def _mm(self, context, event):
        from .draft_tool import mouse_mm, px_to_mm
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        if q is None:
            return None, None
        mmpx = px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        p, kind, pc = self.snapper.point(context, q, mmpx, ctrl=event.ctrl, piece=self.on if self.internal else None)
        return p, pc

    def _box(self, context):
        w = typed_mm(context, self.typed[0]) if self.typed[0] else None
        h = typed_mm(context, self.typed[1]) if self.typed[1] else None
        return D.shape_box(self.p0, self.cur if self.cur is not None else self.p0, w, h, self.square, self.center)

    def _status(self, context):
        txt = f"{self._label()}"
        if self.internal and self.on:
            txt += f" on {self.on}"
        if self.p0 is not None:
            lo, hi = self._box(context)
            txt += f":  W {fmt(context, hi[0] - lo[0])}  H {fmt(context, hi[1] - lo[1])}"
            if self.shape == 'ELLIPSE':
                rx, ry = (hi - lo) / 2
                h = ((rx - ry) / max(rx + ry, 1e-9)) ** 2              # Ramanujan's perimeter
                txt += tx("  round {0}", fmt(context, np.pi * (rx + ry) * (1 + 3 * h / (10 + np.sqrt(4 - 3 * h)))))
        f = ['W', 'H']
        if any(self.typed) or self.p0 is not None:
            txt += "   [" + "  ".join(f"{f[i]} {self.typed[i] or '-'}{'<' if i == self.field else ''}"
                                      for i in (0, 1)) + "]"
        txt += ("   drag, or click two corners   type W, Tab, H, Enter   Shift: square   Alt: from the center"
                "   Ctrl: no snap   Esc: cancel")
        context.area.header_text_set(txt)

    def _draw(self, context):
        try:
            if self.p0 is None or bpy.context.area is None or bpy.context.area.as_pointer() != self._draw_area \
                    or not VV.allows_pattern(bpy.context):
                return
            from .draft_tool import to_world
            lo, hi = self._box(context)
            P = shape_points(self.shape, lo, hi)
            W = to_world(self.M, np.vstack([P, P[:1]]))
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            sh.bind()
            sh.uniform_float('viewportSize', (context.region.width, context.region.height))
            sh.uniform_float('lineWidth', 2.5)
            sh.uniform_float('color', YELLOW)
            batch_for_shader(sh, 'LINE_STRIP', {'pos': [tuple(w) for w in W]}).draw(sh)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def invoke(self, context, event):
        from . import toolbar as TB
        if not VV.allows_pattern(context):
            return {'CANCELLED'}
        spec, _ = store.get(context)
        if spec is None:
            if self.internal:
                self.report({'WARNING'}, "No pattern yet: draw a piece first")
                return {'CANCELLED'}
            bpy.ops.ac9_cloth_sim.new_pattern('EXEC_DEFAULT')
        PV.layout_empty(context, create=True)
        self.M = PV.layout_matrix(context)
        self.p0 = self.cur = None
        self.on = None
        self.press_px = None
        self.waiting = False                  # True: the press was a click, the second corner is a click too
        self.typed, self.field = ['', ''], 0
        self.square = self.center = False
        self.snapper = Snapper(context, ('corner', 'notch', 'iend', 'mid', 'edge', 'iline'))
        self._draw_area = context.area.as_pointer()
        self._h = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        self._status(context)
        return TB.start_modal(self, context, event)

    def _end(self, context, ok):
        bpy.types.SpaceView3D.draw_handler_remove(self._h, 'WINDOW')
        clear_mark()
        context.area.header_text_set(None)
        context.area.tag_redraw()
        if not ok or self.p0 is None:
            return {'CANCELLED'}
        lo, hi = self._box(context)
        if (hi - lo).min() < MIN_MM:
            self.report({'WARNING'}, rpt("Too small: each side needs at least {0}", fmt(context, MIN_MM)))
            return {'CANCELLED'}
        from .draft_tool import commit
        from .core import spec as S
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        spec = S.loads(S.dumps(spec))
        if not self.internal:
            if self.shape == 'RECT':
                nm = D.add_polygon(spec, D.rect_points(lo, hi), "Rectangle")
            else:
                nm = D.add_ellipse(spec, (lo + hi) / 2, (hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2)
            bad = D.problems(spec, [nm])
            if bad:
                self.report({'WARNING'}, bad[0])
                return {'CANCELLED'}
            if context.window_manager.ac9_draw_symmetric:
                D.piece(spec, nm)['mirror_edit'] = True
            commit(context, spec, [nm])
            self.report({'INFO'}, rpt("Added piece {0}: {1} x {2}", nm, fmt(context, hi[0] - lo[0]), fmt(context, hi[1] - lo[1])))
            return {'FINISHED'}
        if self.shape == 'ELLIPSE':                  # four curved spans: its points can be dragged (2026-10-28)
            Q = D.ellipse_quarters((lo + hi) / 2, (hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2)
            lid = IL.add_shape(spec, self.on, [x[0] for x in Q], [np.array([x[1], x[2]]) for x in Q])
        else:
            lid = D.add_internal(spec, self.on, shape_points(self.shape, lo, hi), kind='shape', closed=True)
        bad = D.internal_problems(spec, self.on, [lid])
        if bad:
            self.report({'WARNING'}, bad[0] + rpt(": keep the shape inside the piece"))
            return {'CANCELLED'}
        msg = commit(context, spec, [self.on], old_spec=old)
        notes = D.internal_mesh_notes(spec, self.on, context.scene.ac9_cloth_sim.mesh_size, [lid])
        self.report({'WARNING'} if notes else {'INFO'},
                    rpt("Added {0} {1} on {2}", self._label().lower(), lid, self.on) + (rpt("; {0}", notes[0]) if notes else "")
                    + (rpt("; {0}", msg) if msg else ""))
        return {'FINISHED'}

    def _start(self, context, event):
        q, pc = self._mm(context, event)
        if q is None:
            return False
        if self.internal:
            from .draft_tool import _inside
            spec, outlines = store.get(context)
            visible = VV.pattern_names(context, spec)
            pc = pc if pc is not None else next((p['name'] for p in reversed(spec['pieces'])
                                                 if p['name'] in visible and p.get('enabled', True)
                                                 and _inside(outlines[p['name']].B, q)), None)
            if pc is None:
                self.report({'WARNING'}, "Start the shape on a piece")
                return False
            self.on = pc
        self.p0 = self.cur = q
        self.press_px = np.array([event.mouse_region_x, event.mouse_region_y], float)
        return True

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}
        self.square, self.center = bool(event.shift), bool(event.alt)
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_ALT', 'RIGHT_ALT',
                                                        'LEFT_CTRL', 'RIGHT_CTRL'}:
            q, _ = self._mm(context, event)
            if q is not None:
                self.cur = q
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self.p0 is None:
                self._start(context, event)
            else:                                         # the second click
                q, _ = self._mm(context, event)
                if q is not None:
                    self.cur = q
                return self._end(context, True)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.p0 is not None and not self.waiting:
            moved = np.linalg.norm(np.array([event.mouse_region_x, event.mouse_region_y]) - self.press_px)
            if moved > CLICK_PX or any(self.typed):
                q, _ = self._mm(context, event)
                if q is not None:
                    self.cur = q
                return self._end(context, True)
            self.waiting = True
        elif event.value == 'PRESS':
            if event.type in {'ESC', 'RIGHTMOUSE'}:
                return self._end(context, False)
            if event.type in {'RET', 'NUMPAD_ENTER'}:
                return self._end(context, self.p0 is not None)
            if event.type == 'TAB':
                self.field = 1 - self.field
            elif event.type == 'BACK_SPACE':
                self.typed[self.field] = self.typed[self.field][:-1]
            elif event.unicode and event.unicode in '0123456789.':
                self.typed[self.field] += event.unicode
        show_mark(context, self.snapper, self.M)
        self._status(context)
        return {'RUNNING_MODAL'}


# ---------------------------------------------------------------- the tools (toolbar.py registers them)

def _tool(idname, label, desc, icon, tool):
    from . import toolbar as TB
    def draw_settings(context, layout, _tool):
        from . import toolbar as TB
        TB.works_on(layout, tool)
        TB._keys(layout, tool)
        draw_snap_settings(context, layout)
        if tool in ('RECT', 'ELLIPSE'):
            # where it is set as the piece is drawn (user 2026-10-26, Fable b-5: not found in the Tool tab at first)
            layout.prop(context.window_manager, "ac9_draw_symmetric", toggle=True, icon='MOD_MIRROR')
    return type(f"AC9_WT_{tool.title().replace('_', '')}", (bpy.types.WorkSpaceTool,), {
        'bl_space_type': 'VIEW_3D', 'bl_context_mode': 'OBJECT', 'bl_idname': idname, 'bl_label': label,
        'bl_description': desc, 'bl_icon': icon, 'bl_widget': None,
        'bl_keymap': (("ac9_cloth_sim.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, {"properties": [("tool", tool)]}),
                      ("ac9_cloth_sim.tool_click", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("tool", tool)]}))
                     + TB.DELETE_KEYS,
        'draw_settings': staticmethod(draw_settings)})


def draw_snap_settings(context, layout):
    wm = context.window_manager
    row = layout.row(align=True)
    row.prop(wm, "ac9_pattern_snap", text="", icon='SNAP_ON' if wm.ac9_pattern_snap else 'SNAP_OFF')
    row.prop(wm, "ac9_grid_snap", text="", icon='SNAP_INCREMENT')
    row.prop(wm, "ac9_pattern_grid", text="", icon='MESH_GRID')


_DESC = "drag from corner to corner, or click two corners. Shift: {sq}, Alt: from the center, type width Tab height"
AC9_WT_Rect = _tool("ac9_cloth_sim.rect_tool", "Rectangle Piece", "Draw a rectangular piece: " + _DESC.format(sq="square"),
                    "ops.gpencil.primitive_box", 'RECT')
AC9_WT_Ellipse = _tool("ac9_cloth_sim.ellipse_tool", "Ellipse Piece", "Draw a round piece: " + _DESC.format(sq="circle"),
                       "ops.gpencil.primitive_circle", 'ELLIPSE')
AC9_WT_IRect = _tool("ac9_cloth_sim.internal_rect_tool", "Internal Rectangle",
                     "Draw a rectangle inside a piece (a pocket's place): " + _DESC.format(sq="square"),
                     "ops.mesh.inset", 'IRECT')
AC9_WT_IEllipse = _tool("ac9_cloth_sim.internal_ellipse_tool", "Internal Ellipse",
                        "Draw an ellipse inside a piece (a button's mark): " + _DESC.format(sq="circle"),
                        "ops.generic.select_circle", 'IELLIPSE')
SHAPE_TOOLS = {'RECT': ('RECT', False), 'ELLIPSE': ('ELLIPSE', False), 'IRECT': ('RECT', True),
               'IELLIPSE': ('ELLIPSE', True)}

_handles = []
CLASSES = (AC9_OT_SimDrawShape,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    wm = bpy.types.WindowManager
    wm.ac9_pattern_snap = BoolProperty(name="Snap", default=True,
                                       description="Snap to corners, notches, edges and lines while drawing on the "
                                                   "pattern (hold Ctrl to draw without)")
    wm.ac9_grid_snap = BoolProperty(name="Snap to Grid", default=False, description="Snap to the pattern grid too")
    wm.ac9_pattern_grid = BoolProperty(name="Grid", default=True,
                                       description="Show a grid under the pattern (1 / 10 / 100 mm as you zoom)")
    wm.ac9_draw_symmetric = BoolProperty(name="Symmetric Edit", default=True, translation_context=CTX,
                                         description="The piece drawn is edited symmetrically about its center line "
                                                     "(a corner moved, a point added on one side is mirrored on the "
                                                     "other); switched off or on later in Properties > Tool > AC9 Piece")
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_grid, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_mark, (), 'WINDOW', 'POST_PIXEL'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_fold, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    clear_mark()
    for nm in ('ac9_pattern_snap', 'ac9_grid_snap', 'ac9_pattern_grid', 'ac9_draw_symmetric'):
        if hasattr(bpy.types.WindowManager, nm):
            delattr(bpy.types.WindowManager, nm)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
