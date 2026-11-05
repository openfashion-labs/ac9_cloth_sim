# SPDX-License-Identifier: GPL-3.0-or-later
"""What the next click of a tool lands on, shown before the click (user 2026-10-21, DECISIONS
cloth-sim-interaction-grammar-mn-seam-2026-10-21; Fable docs/reviews/REVIEW_fable_interaction_model_2026-10-21.md
R1). The tools that start a modal operator on their first click (Add Seam, Internal Line, the drawing tools, the
tapes, Place) showed nothing until that click; Tape Around already showed its ring.

- Add Seam (and Add Seam M:N), Tape on the pattern: the outline point a click takes (white; red on a corner;
  on the middle of an edge, Add Seam takes the whole edge: drawn yellow from its start)
- Draw Piece, Internal Line, the shapes: the snap mark (shape_tool's)
- Tape Along / Straight on the body: the body point
- Place: the guide under the cursor in yellow when pieces to place are picked; else a note to pick them

Called from the tools' hover (toolbar.AC9_OT_SimToolHover) only, so a modal operator's own drawing takes over once
it runs. Drawn only while the tool that set it is the active one."""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import pattern_view as PV
from . import store
from . import selection as SEL
from . import view_visibility as VV

_m = {'area': None, 'tool': None, 'dots': [], 'lines': []}       # dots: (world m, rgba); lines: (segments, rgba)
_cache = {}                        # (what, store key, kinds): built once per pattern version
_handles = []
WHITE, RED, YELLOW = (1.0, 1.0, 1.0, 1.0), (1.0, 0.3, 0.3, 1.0), (1.0, 0.85, 0.2, 0.9)


def _key():
    return store._cache.get('key')


def _cached(what, build):
    k = (what, _key())
    if k not in _cache:
        if len(_cache) > 8:
            _cache.clear()
        _cache[k] = build()
    return _cache[k]


def _set(context, dots=(), lines=()):
    from . import toolbar as TB
    new = (list(dots), list(lines))
    old = (_m['dots'], _m['lines'])
    _m.update(area=context.area.as_pointer() if context.area else None, tool=TB.active_tool(context),
              dots=new[0], lines=new[1])
    if (bool(old[0]) or bool(old[1]) or bool(new[0]) or bool(new[1])) and context.area:
        context.area.tag_redraw()


def clear(context=None):
    had = bool(_m['dots'] or _m['lines'])
    _m.update(dots=[], lines=[])
    if had and context is not None and context.area:
        context.area.tag_redraw()


def seam_point(context, event):
    """Add Seam: the outline (or internal line) point the click would take."""
    from . import seam_tool as ST
    from . import draft_tool as DT
    spec, outlines = store.get(context)
    if spec is None or PV.layout_empty(context) is None:
        return clear(context)
    visible = VV.pattern_names(context, spec)
    idx = _cached(('seam', visible), lambda: ST.OutlineIndex(spec, outlines, names=visible))
    M = PV.layout_matrix(context)
    mx, my = event.mouse_region_x, event.mouse_region_y
    q = DT.mouse_mm(context, M, mx, my)
    if q is None:
        return clear(context)
    mmpx = DT.px_to_mm(context, M, mx, my)
    hit = idx.nearest(q)
    if hit is None or hit[3] > ST.SNAP_PX * mmpx:
        return _set(context)
    pc, s, _, _ = hit
    s, corner = idx.snap_corner(pc, s, ST.CORNER_PX * mmpx)
    tr = idx.tracks[pc]
    wh = None if corner or event.ctrl else ST.whole_edge(tr, s)
    if wh is not None:                     # mid-edge: the click takes the whole edge, from its nearer end
        s0, s1, d = wh
        span = (((s1 - s0) % tr.L) if d > 0 else -((s0 - s1) % tr.L)) if ST.is_closed(tr) else s1 - s0
        P = tr.at(s0 + np.linspace(0.0, span, max(2, int(abs(span) / 2.0) + 1)))
        W = [tuple(M @ Vector((float(x) * PV.MM, float(y) * PV.MM, 0.0))) for x, y in P]
        p0 = W[0]
        return _set(context, dots=[(p0, YELLOW)], lines=[([p for u, v in zip(W[:-1], W[1:]) for p in (u, v)], YELLOW)])
    p = tr.at(s)
    w = M @ Vector((float(p[0]) * PV.MM, float(p[1]) * PV.MM, 0.0))
    _set(context, dots=[(tuple(w), RED if corner else WHITE)])


def snap_point(context, event, kinds):
    """The drawing tools: the snap mark where the click would put a point (shape_tool draws it)."""
    from . import shape_tool as SH
    from . import draft_tool as DT
    if store.get(context)[0] is None:
        return SH.clear_mark()
    snapper = _cached(('snap', tuple(kinds)), lambda: SH.Snapper(context, kinds))
    M = PV.layout_matrix(context)
    q = DT.mouse_mm(context, M, event.mouse_region_x, event.mouse_region_y)
    if q is None:
        return SH.clear_mark()
    mmpx = DT.px_to_mm(context, M, event.mouse_region_x, event.mouse_region_y)
    had, had_gaps = SH._marks['mark'], SH._marks['gaps']
    snapper.point(context, q, mmpx, ctrl=event.ctrl)
    SH.show_mark(context, snapper, M, hover=True)
    if (had is None) != (snapper.mark is None) or snapper.mark is not None or had_gaps or snapper.gaps:
        context.area.tag_redraw()


def tape_point(context, event, kind):
    """Tape Along / Straight: the body point (on the body) or the outline point (on the pattern) a click takes."""
    from . import measure_tool as MT
    if kind == 'BODY':
        ob = MT._body(context)
        hit = MT._ray_body(context, ob, event) if ob is not None and ob.type == 'MESH' else None
        if hit is None:
            return _set(context)
        return _set(context, dots=[(tuple(hit[0] * 0.001), WHITE)])
    if kind in ('PATTERN', 'PLANE'):
        spec, outlines = store.get(context)
        h = MT._snap_outline(context, outlines, (event.mouse_region_x, event.mouse_region_y)) if spec else None
        if h is None:
            return _set(context)
        return _set(context, dots=[(tuple(h[3]), WHITE)])
    _set(context)


def place_guide(context, event):
    """Place: the guide a press would put the picked pieces on. True when pieces are picked."""
    from . import overlay
    from . import toolbar as TB
    from .core import body_axes as BA
    from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
    spec = store.get(context)[0]
    to_place = SEL.any_pieces(context)
    body = getattr(context.scene.ac9_cloth_sim, 'body', None)
    if not VV.allows_world(context, spec) or (body is not None and not VV.object_allowed(context, body)):
        clear(context)
        return to_place
    if not spec or not spec.get('guides') or context.region_data is None:
        _set(context)
        return to_place
    xy = (event.mouse_region_x, event.mouse_region_y)
    o = np.array(region_2d_to_origin_3d(context.region, context.region_data, xy)) * 1000.0
    d = np.array(region_2d_to_vector_3d(context.region, context.region_data, xy))
    h = BA.ray_hit(spec['guides'], o, d)
    if h is None or not to_place:
        _set(context)
        return to_place
    segs = _cached(('guide', h[0]), lambda: [tuple(p) for p in overlay.guide_lines(spec['guides'][h[0]])])
    _set(context, lines=[(segs, YELLOW)])
    return to_place


def _draw():
    ctx = bpy.context
    if not (_m['dots'] or _m['lines']) or ctx.area is None or ctx.area.as_pointer() != _m['area']:
        return
    from . import toolbar as TB
    if TB.active_tool(ctx) != _m['tool']:
        return
    if not VV.allows_pattern(ctx) and not VV.allows_world(ctx):
        return
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        if _m['lines']:
            sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            sh.bind()
            sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
            sh.uniform_float('lineWidth', 3.0)
            for segs, c in _m['lines']:
                sh.uniform_float('color', c)
                batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
        if _m['dots']:
            sp = gpu.shader.from_builtin('UNIFORM_COLOR')
            sp.bind()
            gpu.state.point_size_set(12.0)
            for p, c in _m['dots']:
                sp.uniform_float('color', c)
                batch_for_shader(sp, 'POINTS', {'pos': [p]}).draw(sp)
            gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')
    except Exception:
        pass


def register():
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW'))


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    _cache.clear()
    _m.update(dots=[], lines=[])
