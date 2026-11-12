# SPDX-License-Identifier: GPL-3.0-or-later
"""Drafting in the viewport: the Edit Pieces tool (toolbar) and the drafting operators (core/draft.py does the edits).

Edit Pieces is a tool, like Blender's Move: with it active, a left drag on a corner, an edge or a curve handle of a
flat piece -- or on its inside, to move the whole piece -- runs one Pattern Drag (one undo step); R rotates the piece
under the cursor; K splits the edge under the cursor, X / Delete removes the corner
under the cursor, C curves the edge under the cursor (or makes it straight again), N puts a notch on the outline
under the cursor (X removes it, a drag slides it along the outline). The corner, edge, handle or notch under the cursor
is highlighted. While dragging, the pieces being changed are drawn in yellow and each seam on them shows
its two lengths and their difference (the ease). Twins (a piece and its mirrored copy) are edited together.

Keys while dragging: X / Y lock to an axis, Shift holds the move to 0 / 45 / 90 degrees, type a number for the
distance (in the scene's length unit), Ctrl turns the snapping off (on when the header's magnet is off),
right click / Esc cancels. A point, an internal line or an edge shows how far it is from the lines around
it in its piece and snaps to their middle, to even spacing and to a distance another internal line has; a corner
snaps onto a corner, a line's end onto its piece's outline and lines; a whole piece lines up with the other pieces'
corners (user 2026-10-23, DECISIONS cloth-sim-drag-distance-snap-2026-10-23).
Shift+click picks more corners, edges and internal lines; a drag on one of them moves them all (user 2026-10-25).
"""
from . import prepare_build as PB
import copy
import math
from .i18n import CTX, F9, rpt, tx

import blf
import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_origin_3d, region_2d_to_vector_3d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import intersect_line_plane

from . import overlay
from . import click_log as CL
from . import pattern_view as PV
from . import store
from . import toolbar as TB
from . import view_visibility as VV
from .core import draft as D
from .core import iline as IL
from .core import snap as SN
from .core import spec as S
from . import selection as SEL
from . import units as U

TOOL_ID = "ac9_cloth_sim.edit_pattern_tool"
# Whole Pieces (user 2026-10-28, DECISIONS cloth-sim-transform-edit-split): whole pieces and internal lines
TRANSFORM_ID = "ac9_cloth_sim.transform_pattern_tool"
CORNER_PX = 12        # grab a corner (or a handle) within this
EDGE_PX = 10          # grab an edge within this
YELLOW = (1.0, 0.85, 0.2, 1.0)
GAP_COLOR, GAP_HOT = (0.85, 0.85, 0.85, 0.85), (1.0, 0.35, 0.75, 1.0)     # a gap, a gap snapped to
GAP_NAMES = {'center': "middle", 'even': "even spacing", 'same': "same distance"}
EASE_OK, EASE_MID = 3.0, 10.0      # mm: the ease shown white up to 3, yellow up to 10, orange beyond

# what the tool shows: the hovered thing, or the drag in progress (spec being previewed, pieces changed)
_state = {'hover': None, 'hover_area': None, 'drag': None, 'handles': []}
# the internal line picked on the pattern (user 2026-10-21): the Tool tab shows its fold, elastic and seams
_isel = {'piece': None, 'line': None}
# more internal lines picked with Shift+click (user 2026-10-25): moved together with the picked one and the picked
# edges; the Tool tab shows the last one picked (_isel)
_imore = []                   # [(piece, line id)]
# corners picked (user 2026-10-25): a click picks one, Shift+click adds or takes it out; kept by the id of the edge
# that starts at the corner, so a split elsewhere (K) keeps it
_csel = []                    # [(piece, edge id)]
ISEL = (1.0, 0.55, 0.1, 1.0)
# the outline edges picked on the pattern (user 2026-10-22, DECISIONS cloth-sim-edge-select-2026-10-22): a click on
# an edge picks it, Shift+click adds or takes it out (across pieces); the Tool tab shows their lengths and total
_esel = []                    # [(piece, edge id)]
_companion_selection_scope = {}


def _companion_scope(context, kind):
    t = context.scene.ac9_cloth_sim.spec_text
    _companion_selection_scope[kind] = (context.scene.as_pointer(), t.as_pointer() if t else None)


def select_internal(context, piece=None, line=None, extend=False):
    """Pick internal line `line` of `piece` (None: none picked); the Tool tab then shows it. extend: add it to the
    lines picked (the Tool tab shows it), or take it out if picked."""
    _companion_scope(context, 'internal')
    k, cur = (piece, line), (_isel['piece'], _isel['line'])
    if extend and piece:
        if k == cur:                              # taken out: the one picked before it is shown
            piece, line = _imore.pop() if _imore else (None, None)
        elif k in _imore:
            _imore.remove(k)
            piece, line = cur
        elif cur[0]:
            _imore.append(cur)
    else:
        _imore.clear()
    _isel.update(piece=piece, line=line)
    from . import tool_tab
    if piece:
        tool_tab.set_focus('INTERNAL')
    else:
        tool_tab._tag_properties()
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def select_edge(context, piece=None, eid=None, extend=False):
    """Pick outline edge eid of piece (None: drop the edges picked); extend: add it, or take it out if picked."""
    _companion_scope(context, 'edges')
    k = (piece, eid)
    if piece is None:
        had = bool(_esel)
        _esel.clear()
        if not had:
            return
    elif extend:
        if k in _esel:
            _esel.remove(k)
        else:
            _esel.append(k)
    else:
        _esel[:] = [k]
    from . import tool_tab
    if _esel:
        tool_tab.set_focus('EDGES')
    else:
        tool_tab._tag_properties()
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def selected_edges(spec):
    """[(piece, edge id, length mm)] of the picked edges still there (an edit or an undo may have removed some)."""
    out = []
    if spec is None:
        return out
    for nm, eid in _esel:
        pc = next((x for x in spec['pieces'] if x['name'] == nm), None)
        if pc is None:
            continue
        ol = S.Outline(pc)
        if eid in ol.edges:
            out.append((nm, eid, float(ol.edges[eid][1])))
    return out


def select_corner(context, piece=None, eid=None, extend=False):
    """Pick the corner starting edge eid of piece (None: drop the corners picked); extend: add it, or take it out."""
    k = (piece, eid)
    if piece is None:
        _csel.clear()
    elif extend:
        if k in _csel:
            _csel.remove(k)
        else:
            _csel.append(k)
    else:
        _csel[:] = [k]
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def corner_eid(spec, piece, k):
    """The id of the edge starting at corner k of piece."""
    return D.piece(spec, piece)['edges'][k]['id']


def picked_lines(spec):
    """[('edge', piece, edge id) / ('iline', piece, line id) / ('corner', piece, id of the edge starting there)] of
    every picked edge, internal line and corner still there."""
    out = [('edge', nm, eid) for nm, eid, _ in selected_edges(spec)]
    for nm, eid in _csel:
        pc = next((x for x in spec['pieces'] if x['name'] == nm), None) if spec else None
        if pc is not None and any(e['id'] == eid for e in pc['edges']):
            out.append(('corner', nm, eid))
    for nm, lid in [(_isel['piece'], _isel['line'])] + _imore:
        pc = next((x for x in spec['pieces'] if x['name'] == nm), None) if nm and spec else None
        if pc is not None and any(it['id'] == lid for it in pc.get('internals', [])):
            out.append(('iline', nm, lid))
    return out


def picked_pieces(context, spec):
    """The names of the pattern pieces selected (their objects), in the spec."""
    have = {p['name'] for p in spec['pieces']} if spec else set()
    return [n for n in SEL.pattern_pieces(context) if n in have]


def transform_targets(context, spec, h):
    """(pieces, lines) a Whole Pieces drag or G / R / S acts on: what is picked when the thing under the cursor
    is one of them or nothing is under it, else that thing alone."""
    visible = VV.pattern_names(context, spec)
    P = [n for n in picked_pieces(context, spec) if n in visible]
    L = [(n, x) for k, n, x in picked_lines(spec) if k == 'iline' and n in visible]
    if h is not None and h[0] == 'piece' and h[1] not in P:
        return [h[1]], []
    if h is not None and h[0] == 'iline' and (h[1], h[2]) not in L:
        return [], [(h[1], h[2])]
    return P, L


def edit_item(spec, h):
    """Edit Pieces' hit h as a picked_lines item (a corner, an edge, an internal line; a line's end or point: its
    line), else None."""
    if h is None:
        return None
    if h[0] == 'corner':
        return ('corner', h[1], corner_eid(spec, h[1], h[2]))
    if h[0] == 'edge':
        return ('edge', h[1], h[2])
    if h[0] in ('iline', 'iend', 'inode', 'ihandle'):
        return ('iline', h[1], h[2])
    return None


def _in_rect(context, M, rect):
    """A function: points (n, 2) mm on the pattern -> inside the region rectangle rect (x0, y0, x1, y1), per point."""
    region, rv3d = context.region, context.region_data
    x0, y0, x1, y1 = rect

    def inside(P):
        Q = [location_3d_to_region_2d(region, rv3d, Vector(w)) for w in to_world(M, np.asarray(P, float))]
        return np.array([q is not None and x0 <= q.x <= x1 and y0 <= q.y <= y1 for q in Q], bool)
    return inside


def _pick_lines(context, lines, mode):
    """Add (or with mode 'SUB' take out) the internal lines [(piece, line id)] to the lines picked."""
    for n, l in lines:
        cur = [(_isel['piece'], _isel['line'])] + _imore
        if (mode == 'SUB') == ((n, l) in cur):
            select_internal(context, n, l, extend=True)


def box_points(context, rect, mode='SET'):
    """Edit Pieces' box (user 2026-10-28): the corners inside the region rectangle rect and the internal lines all
    inside it picked; mode 'SET' (only these), 'ADD', 'SUB'. Returns how many."""
    from . import view_visibility as VV
    spec, _ = store.get(context)
    M = PV.layout_matrix(context)
    if spec is None or M is None:
        return 0
    inside = _in_rect(context, M, rect)
    objs = PV.piece_objects()
    cs, ls = [], []
    for pc in spec['pieces']:
        ob = objs.get(pc['name'])
        if ob is None or not VV.object_allowed(context, ob) or not pc.get('enabled', True):
            continue
        cs += [(pc['name'], pc['edges'][k]['id']) for k in np.nonzero(inside(D.corners(pc)))[0]]
        ls += [(pc['name'], it['id']) for it in pc.get('internals', [])
               if it.get('kind') != 'baseline' and len(it['pts']) >= 2 and inside(it['pts']).all()]
    if mode == 'SET':
        select_corner(context)
        select_edge(context)
        select_internal(context)
    for k in cs:
        if mode == 'SUB' and k in _csel:
            _csel.remove(k)
        elif mode != 'SUB' and k not in _csel:
            _csel.append(k)
    _pick_lines(context, ls, mode)
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return len(cs) + len(ls)


def box_transform(context, rect, mode='SET'):
    """Whole Pieces' box (user 2026-10-28): the pieces with an outline point inside the region rectangle rect,
    and the internal lines all inside it on the other pieces, picked; mode 'SET', 'ADD', 'SUB'. Returns the pieces."""
    from . import view_visibility as VV
    spec, _ = store.get(context)
    M = PV.layout_matrix(context)
    if spec is None or M is None:
        return []
    inside = _in_rect(context, M, rect)
    objs = PV.piece_objects()
    hit, ls = [], []
    for pc in spec['pieces']:
        ob = objs.get(pc['name'])
        if ob is None or not VV.object_allowed(context, ob):
            continue
        if inside(S.Outline(pc).B).any():
            hit.append(pc['name'])
            continue
        ls += [(pc['name'], it['id']) for it in pc.get('internals', [])
               if it.get('kind') != 'baseline' and len(it['pts']) >= 2 and inside(it['pts']).all()]
    if mode == 'SET':
        SEL.deselect_all(context)
        select_internal(context)
    for nm in hit:
        objs[nm].select_set(mode != 'SUB')
    act = context.view_layer.objects.active
    if hit and mode != 'SUB' and (act is None or not act.select_get()):
        context.view_layer.objects.active = objs[hit[0]]
    _pick_lines(context, ls, mode)
    return hit


def move_lines(sp, items, d):
    """Shift the edges and internal lines `items` (picked_lines) of spec sp by d mm, all at once (in place); the
    pieces changed. Edges move their corners (a corner two picked edges share moves once); a twin moves as the mirror
    image, so a line picked with its twin's mirror line moves once, not twice."""
    lines, moved = [], set()
    for kind, nm, lid in items:
        if kind == 'iline' and (nm, lid) not in moved:
            tw, tlid, _ = D._twin_internal(sp, nm, lid)
            moved |= {(nm, lid), (tw, tlid), (nm, D.self_mirror_line(D.piece(sp, nm), lid)[0])}
            lines.append((nm, lid))
    changed = []
    for nm, lid in lines:
        changed += D.move_internal(sp, nm, lid, 'all', tuple(d))
    ks = {}
    for kind, nm, eid in items:
        if kind == 'edge':
            pc = D.piece(sp, nm)
            k = D.edge_index(pc, eid)
            ks.setdefault(nm, set()).update({k, (k + 1) % len(pc['edges'])})
        elif kind == 'corner':
            ks.setdefault(nm, set()).add(D.edge_index(D.piece(sp, nm), eid))
    done = set()
    for nm, cs in ks.items():
        if nm in done:
            continue
        tw, cmap, _ = D.twin_corner_map(sp, nm)
        if cmap and tw in ks:                        # its twin's picked corners: the mirror image of these
            inv = {j: k for k, j in cmap.items()}
            cs = cs | {inv[j] for j in ks[tw]}
            done.add(tw)
        done.add(nm)
        changed += D.move_corners(sp, nm, {k: tuple(d) for k in cs})
    return list(dict.fromkeys(changed))


def edge_length(spec, piece, eid):
    pc = next((x for x in spec['pieces'] if x['name'] == piece), None)
    ol = S.Outline(pc) if pc is not None else None
    return float(ol.edges[eid][1]) if ol is not None and eid in ol.edges else None


def _edge_poly(spec, piece, eid):
    ol = S.Outline(D.piece(spec, piece))
    s0, L, _ = ol.edges[eid]
    return ol.at(s0 + np.linspace(0.0, L, max(2, int(L / 2.0) + 1)))


def _draw_esel(context):
    spec, _ = store.get(context)
    visible = VV.pattern_names(context, spec)
    sel = [item for item in selected_edges(spec) if item[0] in visible]
    if not sel:
        return
    M = PV.layout_matrix(context)
    segs = []
    for nm, eid, _L in sel:
        W = to_world(M, _edge_poly(spec, nm, eid))
        segs += [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (context.region.width, context.region.height))
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    # drawn over the white outline of the piece under the cursor, with a faint dark edge (user 2026-10-26: the
    # white outline covered the picked edge)
    bt = batch_for_shader(sh, 'LINES', {'pos': segs})
    for w, c in ((6.0, (0.0, 0.0, 0.0, 0.4)), (4.0, ISEL)):
        sh.uniform_float('lineWidth', w)
        sh.uniform_float('color', c)
        bt.draw(sh)
    gpu.state.blend_set('NONE')


DARK = (0.0, 0.0, 0.0, 0.85)


def _dots(pts, layers):
    """Points drawn as stacked dots, largest first: layers [(size px, color)] (a dark rim under a colored middle)."""
    if not pts:
        return
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    sh.bind()
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    bt = batch_for_shader(sh, 'POINTS', {'pos': pts})
    for size, c in layers:
        gpu.state.point_size_set(size)
        sh.uniform_float('color', c)
        bt.draw(sh)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_csel(context):
    spec, _ = store.get(context)
    visible = VV.pattern_names(context, spec)
    M = PV.layout_matrix(context)
    pts = [tuple(to_world(M, [D.piece(spec, nm)['edges'][D.edge_index(D.piece(spec, nm), eid)]['pts'][0]])[0])
           for kind, nm, eid in picked_lines(spec) if kind == 'corner' and nm in visible]
    _dots(pts, ((13.0, DARK), (9.0, ISEL)))


def _draw_all_corners(context):
    """Every corner of every piece, small, while Edit Pieces is the tool: what can be picked is seen (user
    2026-10-26, Fable review REVIEW_fable_drafting_from_scratch §2.2)."""
    spec, _ = store.get(context)
    if spec is None:
        return
    M = PV.layout_matrix(context)
    visible = VV.pattern_names(context, spec)
    pts = [tuple(w) for pc in spec['pieces'] if pc['name'] in visible for w in to_world(M, D.corners(pc))]
    _dots(pts, ((7.0, DARK), (4.0, (1.0, 1.0, 1.0, 0.85))))


def _draw_all_internals(context):
    """Every internal line of every shown piece, thin, with any tool: they are seen without pointing at the piece
    (user 2026-11-11). The piece under the cursor draws its lines again on top (_draw_hover)."""
    spec, _ = store.get(context)
    if spec is None:
        return
    visible = VV.pattern_names(context, spec)
    moving = set((_state['drag'] or {}).get('names') or ())       # drawn by the drag where they go, not where they were
    pts = []
    for pc in spec['pieces']:
        if pc['name'] not in visible or pc['name'] in moving:
            continue
        for it in pc.get('internals', []):
            if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                continue
            P = it['pts']
            pts += [p for u, v in zip(P[:-1], P[1:]) for p in (u, v)]
    if not pts:
        return
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', 1.0)
    sh.uniform_float('color', (0.45, 0.9, 0.8, 0.6))
    batch_for_shader(sh, 'LINES', {'pos': [tuple(w) for w in to_world(PV.layout_matrix(context), pts)]}).draw(sh)


def selected_internal(context, spec):
    """(piece, line id, the line) of the picked internal line, or None (gone with an edit or an undo)."""
    if not _isel['piece'] or spec is None:
        return None
    pc = next((x for x in spec['pieces'] if x['name'] == _isel['piece']), None)
    it = next((x for x in (pc or {}).get('internals', []) if x['id'] == _isel['line']), None)
    return (_isel['piece'], _isel['line'], it) if it is not None else None


def _draw_isel(context):
    spec, _ = store.get(context)
    visible = VV.pattern_names(context, spec)
    s = selected_internal(context, spec)
    if s is None or len(s[2]['pts']) < 2:
        return
    from . import fold_sides as FS
    M = PV.layout_matrix(context)
    segs = []
    for kind, nm, lid in picked_lines(spec):
        if kind == 'iline' and nm in visible:
            P = np.asarray(D.internal(D.piece(spec, nm), lid)['pts'], float)
            try:
                deg = float(FS._fold(spec, nm, lid).get('deg') or 0.0)
            except (TypeError, ValueError):
                deg = 0.0
            # folded: drawn as on a paper pattern (user 2026-10-25), a mountain dash-dot, a valley dashed
            sg = (_dashes(P, FOLD_MOUNTAIN if deg > 0 else FOLD_VALLEY) if abs(deg) > 1e-6
                  else list(zip(P[:-1], P[1:])))
            segs += [tuple(p) for p in to_world(M, [q for u, v in sg for q in (u, v)])]
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (context.region.width, context.region.height))
    sh.uniform_float('lineWidth', 4.0)
    sh.uniform_float('color', ISEL)
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
    gpu.state.blend_set('NONE')


FOLD_MOUNTAIN = (8.0, 2.0, 1.5, 2.0)     # mm on the pattern: dash, gap, dot, gap
FOLD_VALLEY = (6.0, 3.0)                 # dash, gap


def _dashes(P, pattern):
    """[(a, b)] the pieces of polyline P (mm) that are drawn, the pattern (on, off, on, off ...) repeated along it."""
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    at = lambda x: np.array([np.interp(x, s, P[:, 0]), np.interp(x, s, P[:, 1])])
    out, x, k = [], 0.0, 0
    while x < s[-1]:
        ln = pattern[k % len(pattern)]
        if k % 2 == 0:
            y = min(x + ln, s[-1])
            cut = [x] + [c for c in s if x < c < y] + [y]      # the polyline's corners kept
            out += [(at(a), at(b)) for a, b in zip(cut[:-1], cut[1:])]
        x += ln
        k += 1
    return out


def ease_color(d):
    a = abs(d)
    return (1, 1, 1, 1) if a <= EASE_OK else ((1.0, 0.9, 0.3, 1.0) if a <= EASE_MID else (1.0, 0.55, 0.1, 1.0))


def _edit_current(op, context, event):
    """Cheap identity checks while moving; compare saved content only on completion."""
    owner = getattr(op, '_owner', None)
    box = getattr(op, 'box', None) is not None
    dr = _state['drag']
    completion = event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}
    current = (owner is not None and owner.matches(context, content=completion)
               and (box or (dr is not None and dr.get('owner') is owner)))
    if current:
        return True
    if box:
        bpy.types.SpaceView3D.draw_handler_remove(op._box_h, 'WINDOW')
        op.box = None
    elif dr is not None and dr.get('owner') is owner:
        op._end(context)
    if context.area is not None and (dr is None or dr.get('area') != context.area.as_pointer()):
        context.area.header_text_set(None)
    op.report({'WARNING'}, rpt("The pattern changed during this edit; start the edit again"))
    return False


def light_copy(spec, names):
    """Compatibility entry point for the core's isolated edit snapshot."""
    return D.edit_copy(spec, names)


VIEW_TOOLS = (TOOL_ID, TRANSFORM_ID, "ac9_cloth_sim.draw_piece_tool", "ac9_cloth_sim.draw_line_tool", "ac9_cloth_sim.rect_tool",
              "ac9_cloth_sim.ellipse_tool", "ac9_cloth_sim.internal_rect_tool", "ac9_cloth_sim.internal_ellipse_tool")


def tool_active(context):
    """Edit Pieces or a drawing tool is the active tool: the pattern shows the same view with each of them (the
    piece under the cursor, its internal lines and corners; user 2026-10-21, Fable review
    REVIEW_fable_tool_modes_2026-10-21 §2). Edit Seams draws its own."""
    try:
        return context.workspace.tools.from_space_view3d_mode(context.mode).idname in VIEW_TOOLS
    except Exception:
        return False


def hover_piece(context, event):
    """A drawing tool's hover: the piece under the cursor, for the common view."""
    _state['hover_area'] = context.area.as_pointer() if context.area else None
    kind, ob, _ = TB.under(context, event.mouse_region_x, event.mouse_region_y)
    h = ('piece', ob[PV.PIECE_PROP]) if kind == 'PATTERN' and ob is not None and PV.PIECE_PROP in ob else None
    if h != _state['hover']:
        _state['hover'] = h
        context.area.tag_redraw()


# ---------------------------------------------------------------- mouse <-> layout mm

def mouse_mm(context, M, mx, my):
    region, rv3d = context.region, context.region_data
    if rv3d is None:
        return None
    o = region_2d_to_origin_3d(region, rv3d, (mx, my))
    v = region_2d_to_vector_3d(region, rv3d, (mx, my))
    Minv = M.inverted()
    o_l, v_l = Minv @ o, (Minv.to_3x3() @ v)
    hit = intersect_line_plane(o_l, o_l + v_l * 1000.0, Vector((0, 0, 0)), Vector((0, 0, 1)))
    if hit is None:
        return None
    return np.array([hit.x / PV.MM, hit.y / PV.MM])


def px_to_mm(context, M, mx, my):
    a = mouse_mm(context, M, mx, my)
    b = mouse_mm(context, M, mx + 10, my)
    if a is None or b is None:
        return 1.0
    return max(float(np.linalg.norm(b - a)) / 10.0, 1e-6)


def to_world(M, P2):
    return [M @ Vector((float(x) * PV.MM, float(y) * PV.MM, 0.0)) for x, y in np.atleast_2d(P2)]


_ishapes = {'key': None, 'data': {}}


def ishape(spec, name, it):
    """(vertices, span handles, closed, editable) of internal line it (core/iline.py), kept per pattern version: the
    hover runs pick on every mouse move."""
    k = (store._cache.get('key'), id(spec))
    if _ishapes['key'] != k:
        _ishapes.update(key=k, data={})
    kk = (name, it['id'])
    if kk not in _ishapes['data']:
        N, C, closed, own = IL.shape(it)
        _ishapes['data'][kk] = (N, C, closed, own or len(N) <= IL.DENSE)
    return _ishapes['data'][kk]


def tool_mode(context):
    """'TRANSFORM' with Whole Pieces the active tool, 'EDIT' with Edit Pieces, else None."""
    try:
        t = context.workspace.tools.from_space_view3d_mode(context.mode).idname
    except Exception:
        return None
    return 'TRANSFORM' if t == TRANSFORM_ID else 'EDIT' if t == TOOL_ID else None


def as_mode(h, mode):
    """pick's hit as the tool takes it (user 2026-10-28, DECISIONS cloth-sim-transform-edit-split): Whole Pieces
    takes whole pieces and internal lines (a corner or an edge: its piece; a line's end, point or handle: the line);
    Edit Pieces takes no piece by its inside (a drag there boxes corners and lines). mode None: as it is."""
    if h is None or mode is None:
        return h
    if mode == 'TRANSFORM':
        return ('iline', h[1], h[2]) if h[0] in ('iline', 'iend', 'inode', 'ihandle') else ('piece', h[1])
    return None if h[0] == 'piece' else h


def pick(context, spec, outlines, M, mx, my, mode='TOOL'):
    """pick_any as the active tool takes it (as_mode); mode: 'TRANSFORM', 'EDIT' or None instead of the tool's."""
    return as_mode(pick_any(context, spec, outlines, M, mx, my), tool_mode(context) if mode == 'TOOL' else mode)


def pick_any(context, spec, outlines, M, mx, my):
    """('corner', piece, k) | ('handle', piece, edge id, which) | ('notch', piece, notch id) |
    ('iend', piece, line id, 'start' | 'end') | ('inode', piece, line id, vertex) |
    ('ihandle', piece, line id, span, which) | ('edge', piece, edge id, u) | ('iline', piece, line id) |
    ('piece', piece) | None. A handle wins over a notch, a notch over a corner, a corner over an internal line's end,
    then the outline, an internal line, the inside."""
    from . import view_visibility as VV
    names = VV.pattern_names(context, spec)
    objects = PV.piece_objects()
    names = frozenset(n for n in names if VV.object_allowed(context, objects.get(n)))
    if not names:
        return None
    # Filter a read-only view of the piece list; shared selection and the source
    # spec stay intact. Width estimation must use the same visible candidates.
    spec = dict(spec, pieces=[pc for pc in spec['pieces'] if pc['name'] in names])
    q = mouse_mm(context, M, mx, my)
    if q is None:
        return None
    mmpx = px_to_mm(context, M, mx, my)
    # inside a narrow piece (a placket) the reaches of its edges, lines and points are cut to a quarter of its width
    # there, so its middle half still picks the piece (user 2026-10-24: a placket could be picked only zoomed far in,
    # its two edges' 10 px and its fold line covered it)
    w = _width_at(spec, outlines, q)
    r_pt = CORNER_PX * mmpx if w is None else min(CORNER_PX * mmpx, w / 4.0)
    r_ln = EDGE_PX * mmpx if w is None else min(EDGE_PX * mmpx, w / 4.0)
    handles, cors, iends, ilines, notches = [], [], [], [], []
    for pc in spec['pieces']:
        if not pc.get('enabled', True):
            continue
        if pc.get('notches'):
            ol = outlines[pc['name']]
            for n in pc['notches']:
                if n['edge'] in ol.edges:
                    p = ol.at(ol.s_of(n['edge'], float(n['t'])))
                    notches.append((float(np.linalg.norm(p - q)), ('notch', pc['name'], n['id'])))
        for e in pc['edges']:
            for w, h in enumerate(e.get('ctrl') or []):
                handles.append((float(np.linalg.norm(np.asarray(h) - q)), ('handle', pc['name'], e['id'], w)))
        d = np.linalg.norm(D.corners(pc) - q, axis=1)
        k = int(d.argmin())
        cors.append((float(d[k]), ('corner', pc['name'], k)))
        for it in pc.get('internals', []):
            if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                continue
            P = np.asarray(it['pts'], float)
            if not it.get('closed'):     # a closed shape's ends are one point: a drag there would move it whole
                iends.append((float(np.linalg.norm(P[0] - q)), ('iend', pc['name'], it['id'], 'start')))
                iends.append((float(np.linalg.norm(P[-1] - q)), ('iend', pc['name'], it['id'], 'end')))
            A, Dd = P[:-1], np.diff(P, axis=0)
            t = np.clip(((q - A) * Dd).sum(1) / np.maximum((Dd * Dd).sum(1), 1e-12), 0, 1)
            ilines.append((float(np.linalg.norm(A + t[:, None] * Dd - q, axis=1).min()), ('iline', pc['name'], it['id'])))
            # its vertices and curve handles (user 2026-10-28): a vertex as an end, a handle as an edge's handle
            N, C, closed, ed = ishape(spec, pc['name'], it)
            if ed:
                for i in range(len(N)):
                    if closed or 0 < i < len(N) - 1:
                        iends.append((float(np.linalg.norm(N[i] - q)), ('inode', pc['name'], it['id'], i)))
                for s, c in enumerate(C):
                    for w in range(2) if c is not None else ():
                        handles.append((float(np.linalg.norm(c[w] - q)), ('ihandle', pc['name'], it['id'], s, w)))
    for cand in (handles, notches, cors, iends):
        near = [c for c in cand if c[0] <= r_pt]
        if near:
            return min(near, key=lambda c: c[0])[1]
    best = None
    for pc in spec['pieces']:
        if not pc.get('enabled', True):
            continue
        ol = outlines[pc['name']]
        B = np.vstack([ol.B, ol.B[:1]])
        A, Dd = B[:-1], np.diff(B, axis=0)
        ll = (Dd * Dd).sum(1)
        t = np.clip(((q - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
        d = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
        j = int(d.argmin())
        if best is None or d[j] < best[2]:
            best = (pc['name'], float(ol.cum[j] + t[j] * np.sqrt(ll[j])), float(d[j]))
    if best is not None and best[2] <= r_ln:
        eid, u = outlines[best[0]].edge_t(best[1])
        return ('edge', best[0], eid, u)
    near = [c for c in ilines if c[0] <= r_ln]
    if near:
        return min(near, key=lambda c: c[0])[1]
    for pc in reversed(spec['pieces']):              # inside a piece: the whole piece (the last drawn on top)
        if pc.get('enabled', True) and _inside(outlines[pc['name']].B, q):
            return ('piece', pc['name'])
    for pc in reversed(spec['pieces']):              # an off piece (wire) is picked whole, to bring it back
        if not pc.get('enabled', True) and _inside(outlines[pc['name']].B, q):
            return ('piece', pc['name'])
    return None


def _width_at(spec, outlines, q):
    """The width (mm) at q of the piece q lies inside (the top one drawn): from the nearest outline point straight
    through q to the outline on the far side. None outside every piece or on its outline."""
    for pc in reversed(spec['pieces']):
        if not pc.get('enabled', True) or not _inside(outlines[pc['name']].B, q):
            continue
        B = np.asarray(outlines[pc['name']].B, float)
        A, Dd = B, np.roll(B, -1, axis=0) - B
        t = np.clip(((q - A) * Dd).sum(1) / np.maximum((Dd * Dd).sum(1), 1e-12), 0, 1)
        P = A + t[:, None] * Dd
        d = np.linalg.norm(P - q, axis=1)
        j = int(d.argmin())
        if d[j] < 1e-6:
            return None
        u = (q - P[j]) / d[j]                       # into the piece, away from the nearest edge
        # the ray q + s u against every outline segment A + r Dd: s > 0, 0 <= r <= 1
        den = u[0] * Dd[:, 1] - u[1] * Dd[:, 0]
        ok = np.abs(den) > 1e-12
        e = A - q
        s = np.where(ok, (e[:, 0] * Dd[:, 1] - e[:, 1] * Dd[:, 0]) / np.where(ok, den, 1.0), np.inf)
        r = np.where(ok, (e[:, 0] * u[1] - e[:, 1] * u[0]) / np.where(ok, den, 1.0), -1.0)
        s = s[ok & (s > 1e-6) & (r >= 0.0) & (r <= 1.0)]
        return float(d[j] + s.min()) if len(s) else None
    return None


def outline_point(pc, q):
    """(edge id, t) of the point of piece pc's outline nearest q (layout mm)."""
    ol = S.Outline(pc)
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((np.asarray(q, float) - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    j = int(np.linalg.norm(A + t[:, None] * Dd - q, axis=1).argmin())
    return ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j]))


def walk_text(spec, name):
    """One line: the running difference at each notch of the seams on piece `name` (core/walk.py)."""
    from .core import walk as W
    parts = []
    for sm in spec.get('seams', []):
        if name not in (sm['a']['piece'], sm['b']['piece']) or not sm.get('enabled', True):
            continue
        w = W.walk(spec, sm)
        if not w['pairs']:
            continue
        parts.append(f"{sm['id']}: " + " ".join(f"{p['cum']:+.1f}" for p in w['pairs'])
                     + (tx(" ({0} unpaired)", w['unpaired']) if w['unpaired'] else ""))
    return "   ".join(parts) if parts else "no notched seams on " + name


def _inside(poly, q):
    x, y = q
    ax, ay = poly[:, 0], poly[:, 1]
    bx, by = np.roll(ax, -1), np.roll(ay, -1)
    hit = ((ay > y) != (by > y)) & (x < (bx - ax) * (y - ay) / np.where(by == ay, 1e-12, by - ay) + ax)
    return bool(hit.sum() % 2)


def hot_point(spec, h):
    if h is None:
        return None
    try:
        pc = D.piece(spec, h[1])
        if h[0] == 'corner':
            return D.corners(pc)[h[2]]
        if h[0] == 'handle':
            return np.asarray(pc['edges'][D.edge_index(pc, h[2])]['ctrl'][h[3]], float)
        if h[0] == 'piece':
            return D.piece_center(pc)
        if h[0] == 'notch':
            n = next(x for x in pc['notches'] if x['id'] == h[2])
            ol = S.Outline(pc)
            return ol.at(ol.s_of(n['edge'], float(n['t'])))
        if h[0] in ('inode', 'ihandle'):
            N, C, _, _ = IL.shape(D.internal(pc, h[2]))
            return N[h[3]] if h[0] == 'inode' else C[h[3]][h[4]]
        if h[0] in ('iend', 'iline'):
            P = np.asarray(D.internal(pc, h[2])['pts'], float)
            return P[0] if h[0] == 'iend' and h[3] == 'start' else (P[-1] if h[0] == 'iend' else P[len(P) // 2])
        ol = S.Outline(pc)
        return ol.at(ol.s_of(h[2], h[3]))
    except Exception:
        return None


# ---------------------------------------------------------------- gaps around the thing dragged (2026-10-23)

def gap_frame(spec, g):
    """What a drag of g measures with, fixed when it starts: the line's direction (an internal line) or the way into
    the piece (an edge), else None."""
    pc = D.piece(spec, g[1])
    if g[0] == 'iline':
        P = np.asarray(D.internal(pc, g[2])['pts'], float)
        return P[-1] - P[0]
    if g[0] == 'edge':
        P = np.asarray(pc['edges'][D.edge_index(pc, g[2])]['pts'], float)
        t = P[-1] - P[0]
        t = t / max(np.linalg.norm(t), 1e-12)
        n = np.array([-t[1], t[0]])                  # left of the edge: inside a counter-clockwise outline
        return n if D.signed_area(S.Outline(pc).B) > 0 else -n
    return None


def gap_probe(spec, g, frame=None):
    """(probe, A, B): the rays from the thing g (a corner, a line's end, an internal line, an edge) and the lines of
    its piece they can meet (not the thing itself, nor the outline edges that move with it). None for other things."""
    try:
        pc = D.piece(spec, g[1])
        if g[0] == 'corner':
            n = len(pc['edges'])
            A, B = SN.piece_segments(pc, skip_edges=((g[2] - 1) % n, g[2]))
            return SN.point_probe(D.corners(pc)[g[2]]), A, B
        if g[0] in ('iend', 'iline'):
            A, B = SN.piece_segments(pc, skip_lines=(g[2],))
            if g[0] == 'iend':
                return SN.point_probe(hot_point(spec, g)), A, B
            return SN.line_probe(D.internal(pc, g[2])['pts'], frame), A, B
        if g[0] == 'edge':
            k, n = D.edge_index(pc, g[2]), len(pc['edges'])
            A, B = SN.piece_segments(pc, skip_edges=((k - 1) % n, k, (k + 1) % n))
            return SN.edge_probe(pc['edges'][k]['pts'], frame if frame is not None else gap_frame(spec, g)), A, B
    except Exception:
        return None
    return None


def gap_list(spec, g, frame=None):
    """[(origin, end, mm, ray index)]: how far the thing g is from the lines around it in its piece."""
    pr = gap_probe(spec, g, frame)
    return [] if pr is None else SN.gaps(pr[0], SN.gauge(*pr))


ARROWS = ('→', '↗', '↑', '↖', '←', '↙', '↓', '↘')


def arrow(u):
    a = np.degrees(np.arctan2(u[1], u[0]))
    return ARROWS[int(round(a / 45.0)) % 8]


def commit(context, spec, names, old_spec=None, rigid=None, sources=None, owner=None):
    """Write the spec and remesh the pieces changed (the operator's REGISTER / UNDO makes the undo step). With
    old_spec (the pattern before the edit) the dressed garment is carried over the edit (carry_to_3d). Returns a
    report line about the 3D (or '')."""
    _, saved_note = store.commit(context, spec, owner=owner)
    sp, outlines = store.get(context)
    try:
        PV.update_pieces(context, sp, outlines, names)
    except Exception as exc:
        saved_note = '; '.join(x for x in (saved_note, tx("Pattern saved; pattern objects not updated: {0}", exc)) if x)
    if rigid:
        # the placed objects' layout points follow the pieces moved whole, whether the 3D follows edits or not: they
        # are where on the pattern each vertex is, not the 3D shape
        try:
            from . import placed_view as PLV
            PLV.follow_layout(sp, rigid)
        except Exception as exc:
            saved_note = '; '.join(x for x in (saved_note, tx("placed shape not updated: {0}", exc)) if x)
    overlay.invalidate()
    if old_spec is None:
        return saved_note
    from . import dress as DR
    try:
        DR.standby_wake(context)                    # optional preload, not part of the saved edit
    except Exception as exc:
        saved_note = '; '.join(x for x in (saved_note, tx("Preload not started: {0}", exc)) if x)
    if not context.scene.ac9_cloth_sim.follow_edits:      # 3D Follows Edits off: the 3D waits (sync_3d)
        _defer(context, old_spec, sp, names, rigid, sources)
        return saved_note
    note = ''
    if not rigid:
        try:
            if context.scene.ac9_cloth_sim.sync_pending:   # edits deferred before: every placed shape (sync_3d)
                from . import placed_view as PLV
                names = list(PLV.objects(sp))
            note = refresh_placed(context, sp, names)
        except Exception as exc:                  # the placed shape stays as it was; Place again remakes it
            note = tx("placed shape not updated: {0}", exc)
    try:
        msg = carry_to_3d(context, old_spec, sp, rigid, sources)
        if context.scene.ac9_cloth_sim.sync_pending:   # the edits deferred before went with this carry
            drop_pending(context)
    except ValueError as exc:
        msg = tx("3D not updated: {0}", exc)
    msg = "; ".join(x for x in (msg, note, saved_note) if x)
    if msg:                                         # kept where it is seen after the report line is gone (Fable a-1)
        context.scene.ac9_cloth_sim.dress_status = tx("Pattern edit: {0}", msg)
    return msg


# The edits made with 3D Follows Edits off (user 2026-10-27, TASKS 85): what the carry would have been told of them,
# so the deferred carry (sync_3d, _carry_pending before a run) is told the same. key: the cloth (cloth_key, sha1) of the
# pattern before the first of them, the record the garment's result holds (ac9_spec.json) if the 3D took everything
# before; the hints are used only against that. rigid: {piece: back map new -> that pattern} for pieces only moved
# whole or in parts since; touched: pieces whose shape an edit changed (no hint for them); sources: {new piece: the
# piece of that pattern it was cut from}. Dropped on undo / redo / a file loaded (the pattern goes back, the hints
# would not): the carry is then the plain one, as for an edit the carry refused.
_PENDING = {'key': None, 'rigid': {}, 'touched': set(), 'sources': {}}


def _cloth_sha(spec):
    import hashlib
    return hashlib.sha1(cloth_key(spec).encode('utf-8')).hexdigest()


def _then(first, second):
    """The back map of `first` (that pattern's motion) after `second` (this edit's): new -> that pattern."""
    from .core import transfer as TR
    return lambda P: TR.back_map(first, TR.back_map(second, P))


def _defer(context, old_spec, spec, names, rigid, sources):
    """Note an edit the 3D did not take (3D Follows Edits off)."""
    context.scene.ac9_cloth_sim.sync_pending = True
    P = _PENDING
    if P['key'] is None:
        P['key'] = _cloth_sha(old_spec)
        _record_dressed(context, old_spec)
    for n in dict.fromkeys(names):
        if rigid and n in rigid:
            if n in P['touched']:
                continue
            P['rigid'][n] = _then(P['rigid'][n], rigid[n]) if n in P['rigid'] else rigid[n]
        else:
            P['rigid'].pop(n, None)
            P['touched'].add(n)
    for nw, src in (sources or {}).items():
        P['sources'][nw] = P['sources'].get(src, src)


def _record_dressed(context, old_spec):
    """A Dress result no edit has been carried from has no record of its pattern (ac9_spec.json): written now from
    the pattern before the first deferred edit, as carry_to_3d does, when it matches the result's mesh; else the
    deferred carry stops as an immediate one would."""
    import os
    from . import dress as DR
    from .core import transfer as TR
    g = bpy.data.objects.get(DR.garment_name(old_spec))
    job = DR.job_of(g)
    if not job or not os.path.exists(os.path.join(job, 'job.json')):
        return
    sj = os.path.join(job, 'ac9_spec.json')
    if os.path.exists(sj):
        return
    try:
        pat = np.load(os.path.join(job, 'pattern.npz'), allow_pickle=True)
        idx = np.load(os.path.join(job, 'scene.idx.npz'))
    except (OSError, ValueError):
        return
    base = S.loads(S.dumps(old_spec))
    if [str(n) for n in pat['names']] != [p['name'] for p in base['pieces'] if p.get('enabled', True)] or \
            TR.shape_changed(base, pat, idx):
        return
    open(sj, 'w', encoding='utf-8').write(S.dumps(base))


def _pending_hints(base):
    """(rigid, sources) for a carry from the pattern `base`, or (None, None) when the hints are not about it."""
    P = _PENDING
    if P['key'] is None or P['key'] != _cloth_sha(base):
        return None, None
    return dict(P['rigid']) or None, dict(P['sources']) or None


def drop_pending(context=None, flag=True):
    """Forget the deferred edits' hints (and, flag, the 3D Not Updated mark)."""
    _PENDING.update(key=None, rigid={}, touched=set(), sources={})
    if flag and context is not None:
        context.scene.ac9_cloth_sim.sync_pending = False


@bpy.app.handlers.persistent
def _drop_hints(*_args):
    # undo / redo / a file loaded: the pattern changed under the hints (the mark is a scene property: undone with it)
    drop_pending(flag=False)
    _state['drag'] = None


_HINT_HANDLERS = ('undo_post', 'redo_post', 'load_post')


def sync_3d(context):
    """The edits made with 3D Follows Edits off to the 3D now: the placed shapes remade, the dressed garment carried
    (_carry_pending). Update 3D, the switch back on. Returns a report line; the status line says it too."""
    from . import dress as DR
    from . import placed_view as PLV
    props = context.scene.ac9_cloth_sim
    spec = store.get(context)[0]
    if spec is None or not props.sync_pending:
        return ''
    notes = []
    try:
        notes.append(refresh_placed(context, spec, list(PLV.objects(spec))))
    except Exception as exc:
        notes.append(tx("placed shape not updated: {0}", exc))
    g = bpy.data.objects.get(DR.garment_name(spec))
    if g is not None and DR.job_of(g):
        try:
            notes.append(_carry_pending(context, g, spec))
        except ValueError as exc:
            notes.append(pending_refused(exc))
            msg = "; ".join(x for x in notes if x)
            props.dress_status = tx("Pattern edit: {0}", msg)
            return msg
    drop_pending(context)
    msg = "; ".join(x for x in notes if x) or tx("3D updated")
    props.dress_status = tx("Pattern edit: {0}", msg)
    return msg


def refresh_placed(context, spec, names):
    """The placed objects (Move by Hand, Part to Seam, Fold...) of the pieces `names` given the edited pattern's mesh,
    their shape carried over (placed_view.remesh, extended past the old outline): until 2026-10-26 they kept the old
    shape until the next Phase Sim (Fable review REVIEW_fable_drafting_from_scratch §2.3). Once per edit, not during
    a drag. Returns a report line ('' when all were updated or none had an object)."""
    from . import placed_view as PLV
    obs = PLV.objects(spec)
    todo = [n for n in dict.fromkeys(names) if n in obs and obs[n].get(PLV.PROP) == n]
    if not todo:
        return ''
    from . import dress as DR
    pat, _ = DR._build_cached(spec, context.scene.ac9_cloth_sim.mesh_size)
    tris, bad = DR.triangulate(pat)
    nm = [str(x) for x in pat['names']]
    kept = []
    for n in todo:
        ob = obs[n]
        if n not in nm or n in bad or PLV.has_edits(ob):
            kept.append(n)
            continue
        i = nm.index(n)
        V = np.asarray(pat[f"V{i}"], float)
        if ob.get(PLV.KEY_PROP) != PLV.layout_key(V):
            PLV.remesh(ob, V, tris[i], extend=True)
    return tx("placed shape of {0} kept (edited vertex by vertex: Back to Guide, then Place)", ', '.join(kept)) if kept else ''


def _last_result(job, shown=None):
    """(pattern.npz, scene.idx.npz, shape mm, job dict) of a dressed (or carried) result. The shape is `shown` (the
    garment as on screen, dress.shown_shape: a frame picked on the timeline, or the mesh edited by hand after Edit
    Frame) when it has the result's vertex count -- what Continue starts from too -- else the last frame written: a
    job stopped on an anomaly has frames only up to the stage it stopped in. None when there are no frames."""
    import json
    import os
    info = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))
    idx = np.load(os.path.join(job, 'scene.idx.npz'))
    if shown is not None and len(shown) == len(idx['flat']):
        X = np.asarray(shown, float)
    else:
        from . import dress as D
        X = D.job_last_frame(job)                 # final.npy once its frames were dropped
        if X is None:
            return None
        X = X * 1000.0
    return np.load(os.path.join(job, 'pattern.npz'), allow_pickle=True), idx, X, info


def _simulated_root(job):
    """The simulated result a chain of carried edits (job.json 'from') started from, or None."""
    import json
    import os
    for _ in range(50):
        try:
            info = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not str(info.get('kind', '')).startswith('pattern edit carried'):
            return job
        job = info.get('from')
        if not job:
            return None
    return None


def carry_to_3d(context, old_spec, new_spec, rigid=None, sources=None):
    """Carry the dressed garment over a pattern edit at once, without simulating (core/transfer.py): the garment
    object shows the edited pattern where the old one hung, and a result folder is written that Continue goes on
    from. Pieces the last result did not have are left for the simulation to place. Returns a report line."""
    import json
    import os
    import time
    from . import dress as DR
    from .core import pattern_build
    from .core import transfer as TR
    g = bpy.data.objects.get(DR.garment_name(new_spec))
    job = DR.job_of(g)
    if not job or not os.path.exists(os.path.join(job, 'job.json')):
        return ''
    t0 = time.time()
    shown = DR.shown_shape(context, g) if hasattr(DR, 'shown_shape') else None
    last = _last_result(job, shown)
    if last is None:
        return ''
    old_pat, old_idx, X_old, _ = last
    h = context.scene.ac9_cloth_sim.mesh_size
    # the pattern the garment was dressed with: written next to a carried result; for a Dress result the spec before
    # this edit, checked against the result's mesh by its shape (core/transfer.shape_changed: the outline and the
    # internal lines, not the mesh remade, as _carry_pending does), so a garment dressed with an older mesher or
    # another mesh size is carried, not stopped (it stopped with "simulate first" once the mesher changed, 2026-10-23)
    sj = os.path.join(job, 'ac9_spec.json')
    if os.path.exists(sj):
        base = S.loads(open(sj, encoding='utf-8').read())
    else:
        base = S.loads(S.dumps(old_spec))
        if [str(n) for n in old_pat['names']] != [p['name'] for p in base['pieces'] if p.get('enabled', True)] or \
                TR.shape_changed(base, old_pat, old_idx):
            raise ValueError("the pattern differs from the dressed one since the garment was dressed. Ctrl+Z, or "
                             "Back to Placed with nothing selected")
        open(sj, 'w', encoding='utf-8').write(S.dumps(base))
    new_pat, _ = DR._build_cached(new_spec, h)
    tris, bad = DR.triangulate(new_pat)
    if bad:
        raise ValueError("could not mesh " + ", ".join(bad))
    reset = DR.reset_pieces(g)                     # put back to their placed shapes by Reset: not carried
    # a half cut from a Reset piece is Reset too (core/cut.py)
    reset = sorted(set(reset) | {nw for nw, src in (sources or {}).items() if src in set(reset)})
    try:
        Xs, rep = TR.carry(base, new_spec, old_pat, old_idx, X_old, new_pat, tris, rigid, reset=reset, sources=sources)
    except ValueError as exc:
        # carried edits one after another can fold where the edits made together from the last simulated result do
        # not (a hem let down on the front leg first held at the unchanged turn-up, then the back leg folded;
        # both together carry the turn-up along, 2026-10-08): carry everything since that result in one go
        root = _simulated_root(job)
        rsj = os.path.join(root, 'ac9_spec.json') if root else None
        last = _last_result(root, None) if rsj and root != job and os.path.exists(rsj) else None
        if last is None:
            raise _with_seam_hint(exc, base, new_spec) from None
        old_pat, old_idx, X_old, _ = last
        try:
            Xs, rep = TR.carry(S.loads(open(rsj, encoding='utf-8').read()), new_spec, old_pat, old_idx, X_old,
                               new_pat, tris, reset=reset, sources=sources)
        except ValueError:
            raise _with_seam_hint(exc, base, new_spec) from None
        rep['from_simulated'] = root
    names = [str(n) for n in new_pat['names'] if str(n) in Xs]
    if not names:
        # no piece of the last result is on any more (all turned Off, the new ones never simulated): nothing to
        # carry, the run places the pieces on as before any result (numpy stopped on the empty lists here)
        return ''
    offs, F, fp, flat, X = [], [], [], [], []
    o = 0
    for k, n in enumerate(names):
        i = [str(x) for x in new_pat['names']].index(n)
        offs.append(o)
        F.append(tris[i] + o)
        fp.append(np.full(len(tris[i]), k))
        flat.append(new_pat[f"V{i}"])
        X.append(Xs[n])
        o += len(new_pat[f"V{i}"])
    F, fp, flat, X = np.vstack(F), np.concatenate(fp), np.vstack(flat), np.vstack(X)
    allnames = [str(x) for x in new_pat['names']]
    sub = {f"V{k}": new_pat[f"V{allnames.index(n)}"] for k, n in enumerate(names)}
    sub.update(names=np.array(names), nb=np.array([int(new_pat['nb'][allnames.index(n)]) for n in names]))
    d = os.path.join(os.path.dirname(job), time.strftime("%Y%m%d_%H%M%S") + "_edit")
    k = 1
    while os.path.exists(d):
        k += 1
        d = os.path.join(os.path.dirname(job), time.strftime("%Y%m%d_%H%M%S") + f"_edit{k}")
    os.makedirs(os.path.join(d, 's1'))
    np.savez(os.path.join(d, 'pattern.npz'), **sub)
    # the stitches too (as Dress writes them): the garment's seam lines are drawn from them, and an edit's result
    # without them showed no seam lines on the garment until the next Simulate (user 2026-10-20)
    extra = {}
    if 'stitch' in new_pat.files if hasattr(new_pat, 'files') else 'stitch' in new_pat:
        col = {allnames.index(n): k for k, n in enumerate(names)}
        st = np.asarray(new_pat['stitch'])
        keep = np.array([int(a) in col and int(b) in col for a, b in st[:, [0, 2]]], bool) if len(st) else np.zeros(0, bool)
        st = st[keep]
        oo = np.array(offs)
        extra['E_st'] = (np.stack([oo[[col[int(a)] for a in st[:, 0]]] + st[:, 1],
                                   oo[[col[int(b)] for b in st[:, 2]]] + st[:, 3]], 1) if len(st)
                         else np.zeros((0, 2), int))
        if 'stitch_seam' in (new_pat.files if hasattr(new_pat, 'files') else new_pat):
            extra['E_seam'] = np.asarray(new_pat['stitch_seam'])[keep]
    np.savez(os.path.join(d, 'scene.idx.npz'), offs=np.array(offs), F=F, fp=fp, flat=flat, Xinit=X, **extra)
    np.save(os.path.join(d, 's1', 'frames.npy'), (X[None] * 0.001).astype(np.float32))
    open(os.path.join(d, 'ac9_spec.json'), 'w', encoding='utf-8').write(S.dumps(new_spec))
    secs = round(time.time() - t0, 2)
    # a garment dressed with another mesher, remade here: said in one line as a Continue says it (design review
    # 2026-10-28; the Continue after this edit reads this job and no longer sees the change)
    mesh, report = {'h_mm': round(float(h), 3), 'mesher': pattern_build.MESHER_VERSION}, []
    try:
        prev_m = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8')).get('mesh', {}).get('mesher', 1)
    except (OSError, ValueError):
        prev_m = None
    if prev_m is not None and prev_m != pattern_build.MESHER_VERSION:
        mesh['from_mesher'] = prev_m
        report.append(f"  mesh remade (mesher {prev_m} -> {pattern_build.MESHER_VERSION}), the last shape carried onto it")
    json.dump({'kind': 'pattern edit carried over (no simulation)', 'from': job, 'names': names,
               'stages': ['carried'], 'verts': int(len(flat)), 'tris': int(len(F)), 'carry_s': secs,
               'pieces': rep['pieces'], 'mesh': mesh, 'report': report},
              open(os.path.join(d, 'job.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    ob = DR.garment_object(context, DR.garment_name(new_spec), flat, F, fp, X, names)
    ob['ac9_job'] = d
    DR.refresh_thickness(context, ob, new_spec)
    if reset:                                       # the next Simulate still sews them on from their placed shapes
        DR.set_shown_as_placed(ob, new_spec, reset)   # still shown as placed objects: hidden here
    return tx("3D carried over in {0:.1f} s (Phase Sim to settle it)", secs)


# ---------------------------------------------------------------- drawing (one handler for the tool)

def _outline_segs(M, spec, names):
    segs = []
    for nm in names:
        B = S.Outline(D.piece(spec, nm)).B
        W = to_world(M, np.vstack([B, B[:1]]))
        segs += [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
    return segs


# ---------------------------------------------------------------- a dialog's ghost
# While a dialog that takes numbers is open (Parallel Line, Move by Number, Slash and Spread), where the result would
# go is drawn on the pattern, made again when a value changes; nothing is applied until OK (user 2026-11-12).

_ghost = {'key': None, 'spec': None, 'names': (), 'lines': [], 'why': "", 'live': None}


def _redraw_views():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def ghost(op, key, make):
    """Show the ghost for these values: make() -> (preview spec or None, pieces to outline, extra polylines in mm).
    Made only when key (the values) changed; a ValueError's text is kept as why it would not apply."""
    if _ghost['live'] is None or _ghost['live'] != op.as_pointer():
        # only between a dialog's invoke and its OK / Cancel: the Adjust Last Operation panel draws the operator
        # too, and its ghost, made after OK from the changed pattern, stayed on for good (user 2026-11-12); left
        # open, it took over the next dialog's ghost: only the dialog that opened draws one
        return ""
    k = (store._cache.get('key'),) + tuple(key)
    if _ghost['key'] == k:
        return _ghost['why']
    try:
        sp, names, lines = make()
        why = ""
    except (ValueError, StopIteration, KeyError) as exc:
        sp, names, lines, why = None, (), [], (str(exc) or tx("Cannot apply"))
    _ghost.update(key=k, spec=sp, names=tuple(names), lines=[np.asarray(P, float) for P in lines], why=why)
    _redraw_views()
    return why


def ghost_open(op):
    """Dialog op, which draws a ghost, opens now."""
    clear_ghost()
    _ghost['live'] = op.as_pointer()


def clear_ghost():
    _ghost['live'] = None
    if _ghost['key'] is None:
        return
    _ghost.update(key=None, spec=None, names=(), lines=[], why="")
    _redraw_views()


def draw_why(layout, why):
    """Under a dialog's fields: why the values would not apply (the ghost shows nothing then)."""
    if why:
        import textwrap
        col = layout.column(align=True)
        for k, t in enumerate(textwrap.wrap(why, 44)):
            col.label(text=t, icon='ERROR' if k == 0 else 'BLANK1')


GHOST = (0.55, 0.8, 1.0, 0.95)

# A dialog opens under the cursor and hid most of the ghost (user 2026-11-12): the cursor is moved beside the piece
# first, so the dialog opens there, and goes back where it was when the dialog closes.
DLG_SIZE = (312.0, 210.0)        # px at UI scale 1: the largest of these dialogs (Move by Number, 312 x 206)
DLG_GRIP = (126.0, 46.0)         # where Blender puts the cursor in an opened dialog: right of its left side, below
                                 # its top (px at UI scale 1; the same for all three, measured on screen)
_aside = {'xy': None}


def dialog_aside(context, event, names, spec=None):
    """Move the cursor to beside pieces names (the side with room for the dialog: right, left, above, below), so the
    dialog about to open does not cover them. Nothing moves when no side has room."""
    _aside['xy'] = None
    region, rv3d, win = context.region, context.region_data, context.window
    if region is None or rv3d is None or win is None or PV.layout_empty(context) is None:
        return
    spec = spec if spec is not None else store.get(context)[0]
    M = PV.layout_matrix(context)
    try:
        B = np.vstack([S.Outline(D.piece(spec, n)).B for n in names])
    except (StopIteration, KeyError, ValueError):
        return
    xy = [location_3d_to_region_2d(region, rv3d, w) for w in to_world(M, B)]
    xy = np.array([(v.x, v.y) for v in xy if v is not None], float)
    if not len(xy):
        return
    s = context.preferences.system.ui_scale
    w, h, gap = DLG_SIZE[0] * s, DLG_SIZE[1] * s, 12.0 * s
    (x0, y0), (x1, y1) = xy.min(0), xy.max(0)
    cy = min(max(0.5 * (y0 + y1), h / 2), region.height - h / 2)
    cx = min(max(0.5 * (x0 + x1), w / 2), region.width - w / 2)
    spots = [(x1 + gap + w / 2, cy, x1 + gap + w <= region.width),
             (x0 - gap - w / 2, cy, x0 - gap - w >= 0),
             (cx, y1 + gap + h / 2, y1 + gap + h <= region.height),
             (cx, y0 - gap - h / 2, y0 - gap - h >= 0)]
    spot = next(((x, y) for x, y, ok in spots if ok), None)
    if spot is None:
        return
    _aside['xy'] = (event.mouse_x, event.mouse_y)
    left, top = spot[0] - w / 2, spot[1] + h / 2     # the dialog's top left, then the cursor where Blender wants it
    win.cursor_warp(int(region.x + left + DLG_GRIP[0] * s), int(region.y + top - DLG_GRIP[1] * s))


def dialog_back(context):
    """The cursor back where it was before dialog_aside moved it."""
    xy, _aside['xy'] = _aside['xy'], None
    if xy is not None and context.window is not None:
        context.window.cursor_warp(*xy)


def _draw_ghost(context):
    if _ghost['key'] is None or PV.layout_empty(context) is None:
        return
    sp, names, lines = _ghost['spec'], _ghost['names'], _ghost['lines']
    M = PV.layout_matrix(context)
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('color', GHOST)
    if sp is not None and names:
        sh.uniform_float('lineWidth', 2.0)
        batch_for_shader(sh, 'LINES', {'pos': _outline_segs(M, sp, names)}).draw(sh)
        segs = []
        for nm in names:
            for it in D.piece(sp, nm).get('internals', []):
                if it.get('kind') != 'baseline' and len(it['pts']) >= 2:
                    W = to_world(M, it['pts'])
                    segs += [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
        if segs:
            sh.uniform_float('lineWidth', 1.5)
            batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
    segs = []
    for P in lines:
        if len(P) >= 2:
            W = to_world(M, P)
            segs += [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
    if segs:
        sh.uniform_float('lineWidth', 3.0)
        batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)


def _view_state(context):
    """(spec, pieces to draw, the hot thing, is a drag, drag not allowed) or None."""
    if not VV.allows_pattern(context):
        return None
    area = context.area.as_pointer() if context.area else None
    dr = _state['drag']
    if dr is not None:
        if dr.get('area', area) != area:
            return None
        spec = dr['preview'] or dr['spec']
        visible = VV.pattern_names(context, spec)
        names = [name for name in dr['names'] if name in visible]
        return ((spec, names, dr['grab'], True, bool(dr['bad']))
                if names and dr['grab'][1] in visible else None)
    if not tool_active(context):
        return None
    spec, _ = store.get(context)
    h = _state['hover']
    if spec is None or h is None or _state.get('hover_area', area) not in (None, area) \
            or h[1] not in VV.pattern_names(context, spec):
        return None
    return spec, [h[1]], h, False, False


def _draw_view():
    context = bpy.context
    if not VV.allows_pattern(context):
        return
    try:
        if PV.layout_empty(context) is not None:
            _draw_all_internals(context)
        if PV.layout_empty(context) is not None and tool_active(context) and _state['drag'] is None:
            _draw_all_corners(context)
        _draw_hover(context)
    except Exception:
        pass
    try:
        # the picked lines, edges and corners on top of the piece's white outline under the cursor (user
        # 2026-10-26: drawn first, the white line covered the picked edge)
        if PV.layout_empty(context) is not None and _isel['piece']:
            _draw_isel(context)
        if PV.layout_empty(context) is not None and _esel:
            _draw_esel(context)
        if PV.layout_empty(context) is not None and _csel:
            _draw_csel(context)
    except Exception:
        pass
    try:
        _draw_hot(context)
    except Exception:
        pass
    try:
        _draw_lock(context)
    except Exception:
        pass
    try:
        _draw_ghost(context)
    except Exception:
        pass


def lock_name(u):
    """What a Shift-held move keeps to, for its label: horizontal, vertical or 45 degrees."""
    a = abs(math.degrees(math.atan2(u[1], u[0]))) % 180.0
    return tx("Horizontal") if min(a, 180.0 - a) < 1.0 else tx("Vertical") if abs(a - 90.0) < 1.0 else "45°"


def _draw_lock(context):
    """While Shift holds a move to 0 / 45 / 90 degrees: a dotted line through where it started along the kept
    direction (user 2026-11-11: what the 45 was relative to was not clear). Its label is drawn with the numbers
    (_draw_px: a POST_VIEW handler draws in the 3D space, the label went off the screen)."""
    dr = _state['drag']
    lk = (dr or {}).get('lock')
    if not lk or PV.layout_empty(context) is None:
        return
    o, u, end = np.asarray(lk[0], float), np.asarray(lk[1], float), np.asarray(lk[2], float)
    mmpx = dr.get('mmpx') or 1.0
    R = 4000.0 * mmpx
    M = PV.layout_matrix(context)
    segs = _dashes(np.array([o - u * R, o + u * R]), (6.0 * mmpx, 5.0 * mmpx))
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', 1.5)
    sh.uniform_float('color', (1.0, 0.85, 0.3, 0.9))
    batch_for_shader(sh, 'LINES', {'pos': [tuple(w) for w in to_world(M, [q for a, b in segs for q in (a, b)])]}).draw(sh)


def _draw_hot(context):
    """The point under the cursor: a white ring round whatever it shows (picked orange or not), not a red dot the
    size of the picked one (user 2026-10-26). Drawn last."""
    vs = _view_state(context)
    if vs is None or PV.layout_empty(context) is None:
        return
    spec, names, hot, dragging, bad = vs
    p = hot_point(spec, hot)
    if p is None:
        return
    M = PV.layout_matrix(context)
    w = tuple(to_world(M, [p])[0])
    picked = hot[0] == 'corner' and any(k == 'corner' and nm == hot[1] and eid == corner_eid(spec, hot[1], hot[2])
                                        for k, nm, eid in picked_lines(spec))
    _dots([w], ((17.0, (1.0, 1.0, 1.0, 1.0)), (13.0, DARK), (9.0, ISEL if picked else (1.0, 0.3, 0.3, 1.0))))


def _draw_hover(context):
    vs = _view_state(context)
    if vs is None or PV.layout_empty(context) is None:
        return
    spec, names, hot, dragging, bad = vs
    M = PV.layout_matrix(context)
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', 3.0 if dragging else 2.0)
    sh.uniform_float('color', (1.0, 0.25, 0.2, 1.0) if bad else (YELLOW if dragging else (1, 1, 1, 0.7)))
    batch_for_shader(sh, 'LINES', {'pos': _outline_segs(M, spec, names)}).draw(sh)
    # internal lines of those pieces (the one under the cursor or being moved thicker)
    segs, hot_segs, iend_pts, ihl = [], [], [], []
    for nm in names:
        for it in D.piece(spec, nm).get('internals', []):
            if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                continue
            W = to_world(M, it['pts'])
            tgt = (hot_segs if hot and hot[0] in ('iend', 'iline', 'inode', 'ihandle') and hot[1] == nm
                   and hot[2] == it['id'] else segs)
            tgt += [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
            iend_pts += [tuple(W[0]), tuple(W[-1])]
            N, C, closed, own = IL.shape(it)            # its vertices and curve handles (2026-10-28)
            if own or len(N) <= IL.DENSE:
                WN = to_world(M, N)
                iend_pts += [tuple(w) for k, w in enumerate(WN) if closed or 0 < k < len(WN) - 1]
                for s, c in enumerate(C):
                    for w in range(2) if c is not None else ():
                        a, b = to_world(M, [N[(s + w) % len(N)], c[w]])
                        ihl.append((tuple(a), tuple(b)))
    if segs:
        sh.uniform_float('lineWidth', 1.5)
        sh.uniform_float('color', (0.45, 0.9, 0.8, 0.9))
        batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
    if hot_segs:
        sh.uniform_float('lineWidth', 3.0)
        sh.uniform_float('color', (0.45, 1.0, 0.85, 1.0))
        batch_for_shader(sh, 'LINES', {'pos': hot_segs}).draw(sh)
    hl, hp = [p for ab in ihl for p in ab], [ab[1] for ab in ihl]
    for nm in names:
        for e in D.piece(spec, nm)['edges']:
            if e.get('ctrl'):
                ends = [e['pts'][0], e['pts'][-1]]
                for w in range(2):
                    a, b = to_world(M, [ends[w], e['ctrl'][w]])
                    hl += [tuple(a), tuple(b)]
                    hp.append(tuple(b))
    if hl:
        sh.uniform_float('lineWidth', 1.5)
        sh.uniform_float('color', (0.6, 0.85, 1.0, 0.9))
        batch_for_shader(sh, 'LINES', {'pos': hl}).draw(sh)
    sp = gpu.shader.from_builtin('UNIFORM_COLOR')
    sp.bind()
    gpu.state.point_size_set(8.0)
    pts = [tuple(w) for nm in names for w in to_world(M, D.corners(D.piece(spec, nm)))]
    if pts:
        sp.uniform_float('color', (1, 1, 1, 0.9))
        batch_for_shader(sp, 'POINTS', {'pos': pts}).draw(sp)
    if iend_pts:
        sp.uniform_float('color', (0.45, 0.9, 0.8, 1.0))
        batch_for_shader(sp, 'POINTS', {'pos': iend_pts}).draw(sp)
    if hp:
        sp.uniform_float('color', (0.6, 0.85, 1.0, 1.0))
        batch_for_shader(sp, 'POINTS', {'pos': hp}).draw(sp)
    _draw_gaps(sh, sp, M)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set('NONE')


def _gap_lines(dr):
    """[(a, b, mm, hot)] to draw for the drag dr: each gap from the thing dragged, the gaps a snap matched (the next
    gap on for even spacing, the other line's gap for the same distance) and the lines a piece lined up on."""
    out = []
    sn = dr.get('snapped')
    hot = set()
    if sn and sn[0] == 'gap':
        for kind, rays, o in sn[1]:
            hot |= set(rays)
    for a, b, mm, i in dr.get('gaps') or ():
        out.append((a, b, mm, i in hot))
    if sn and sn[0] == 'gap':
        byray = {i: (a, b) for a, b, mm, i in dr.get('gaps') or ()}
        pv = dr['preview'] or dr['spec']
        pr = gap_probe(pv, dr['grab'], None)
        for kind, rays, o in sn[1]:
            if kind == 'even' and rays[0] in byray and pr is not None:
                r = pr[0].rays[rays[0]]
                h = SN.ray_hits(pr[1], pr[2], np.asarray(r[0], float), r[1]) if r is not None else []
                if len(h) > 1:
                    a = np.asarray(r[0], float) + r[1] * h[0]
                    out.append((a, np.asarray(r[0], float) + r[1] * h[1], float(h[1] - h[0]), True))
            if kind == 'same' and o is not None and rays[0] in byray:
                a, b = byray[rays[0]]
                u = (b - a) / max(np.linalg.norm(b - a), 1e-9)
                out.append((o, o + u * np.linalg.norm(b - a), float(np.linalg.norm(b - a)), True))
    if sn and sn[0] == 'align':
        for ax, mine, theirs in sn[1]:
            out.append((np.asarray(mine, float), np.asarray(theirs, float), None, True))
    return out


def _draw_gaps(sh, sp, M):
    """The gap lines of the drag in progress, dashed (6 px dashes); their ends as small points."""
    dr = _state['drag']
    if dr is None:
        return
    lines = _gap_lines(dr)
    if not lines:
        return
    dash = 6.0 * float(dr.get('mmpx') or 1.0)
    segs, hot_segs, ends = [], [], []
    for a, b, mm, hot in lines:
        L = float(np.linalg.norm(b - a))
        n = max(1, int(L / dash))
        t = np.linspace(0.0, 1.0, n + 1)
        P = a[None] + (b - a)[None] * t[:, None]
        W = to_world(M, P)
        tgt = hot_segs if hot else segs
        tgt += [tuple(W[k]) for j in range(0, n, 2) for k in (j, j + 1)]
        ends.append(tuple(W[-1]))
    sh.bind()
    sh.uniform_float('lineWidth', 1.5)
    if segs:
        sh.uniform_float('color', GAP_COLOR)
        batch_for_shader(sh, 'LINES', {'pos': segs}).draw(sh)
    if hot_segs:
        sh.uniform_float('color', GAP_HOT)
        batch_for_shader(sh, 'LINES', {'pos': hot_segs}).draw(sh)
    sp.bind()
    gpu.state.point_size_set(5.0)
    sp.uniform_float('color', GAP_COLOR)
    batch_for_shader(sp, 'POINTS', {'pos': ends}).draw(sp)
    gpu.state.point_size_set(8.0)


def _draw_gap_labels(context, M):
    dr = _state['drag']
    if dr is not None:
        draw_gap_labels(context, M, _gap_lines(dr))


def draw_gap_labels(context, M, lines):
    """Each gap's length at its middle (pink when a snap matched it). lines: [(a, b, mm, hot)]."""
    from .shape_tool import num
    draw_labels(context, M, [((a + b) / 2, num(context, mm), hot) for a, b, mm, hot in lines if mm is not None])


def draw_labels(context, M, items):
    """The numbers on the pattern, one look (DECISIONS cloth-sim-dimension-display-2026-10-23 b-2): a dark box, white,
    pink when a snap matched it. items: [(point mm, text, hot)] centered on the point, or [(point, text, hot, away
    mm)] set off 14 px from the point, away from `away` (an edge's length outside its piece), or [(point, text, hot,
    away, row)]: `row` boxes further out, stacked (the seams on an edge under its length). hot: True (pink), False
    (white) or a color."""
    if not items:
        return
    region, rv3d = context.region, context.region_data
    fid = 0
    blf.size(fid, 13)
    boxes, texts = [], []
    for it in items:
        p, txt, hot = it[0], it[1], it[2]
        xy = location_3d_to_region_2d(region, rv3d, to_world(M, [p])[0])
        if xy is None:
            continue
        w, h = blf.dimensions(fid, txt)
        x, y = xy[0] - w / 2, xy[1] - h / 2
        if len(it) > 3 and it[3] is not None:
            c = location_3d_to_region_2d(region, rv3d, to_world(M, [it[3]])[0])
            dv = np.array([xy[0] - c[0], xy[1] - c[1]]) if c is not None else np.array([1.0, 0.0])
            dv = dv / max(np.linalg.norm(dv), 1e-6)
            x += dv[0] * (14 + w / 2)
            y += dv[1] * (14 + h / 2)
            if len(it) > 4 and it[4]:
                y += it[4] * (h + 8) * (1 if dv[1] >= 0 else -1)
        boxes += [(x - 3, y - 3), (x + w + 3, y - 3), (x + w + 3, y + h + 3),
                  (x - 3, y - 3), (x + w + 3, y + h + 3), (x - 3, y + h + 3)]
        texts.append((x, y, txt, hot))
    gpu.state.blend_set('ALPHA')
    sb = gpu.shader.from_builtin('UNIFORM_COLOR')
    sb.bind()
    sb.uniform_float('color', (0.08, 0.08, 0.08, 0.8))
    batch_for_shader(sb, 'TRIS', {'pos': boxes}).draw(sb)
    for x, y, txt, hot in texts:
        blf.color(fid, *(hot if isinstance(hot, tuple) else GAP_HOT if hot else (1.0, 1.0, 1.0, 1.0)))
        blf.position(fid, x, y, 0)
        blf.draw(fid, txt)
    gpu.state.blend_set('NONE')


def _line_label(P):
    """(point, away) for an internal line's length: a third along it, set off to its left (clear of the gaps from
    its middle); a closed shape: above its box."""
    P = np.asarray(P, float)
    if len(P) > 2 and np.linalg.norm(P[0] - P[-1]) < 1e-6:
        lo, hi = P.min(0), P.max(0)
        return np.array([(lo[0] + hi[0]) / 2, hi[1]]), (lo + hi) / 2
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    t = s[-1] / 3.0
    k = min(int(np.searchsorted(s, t, side='right')) - 1, len(P) - 2)
    d = P[k + 1] - P[k]
    p = P[k] + d * ((t - s[k]) / max(s[k + 1] - s[k], 1e-9))
    n = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-9)
    return p, p - n


_selc = {'key': None, 'data': None}


def _sel_dims(context, spec):
    """([(point, mm, away, thing)] lengths, [(a, b, mm, False)] gaps) of the picked edges and internal line (b-1): computed
    once per pattern version and pick, a redraw only projects them."""
    visible = VV.pattern_names(context, spec)
    k = (store._cache.get('key'), id(spec), tuple(_esel), _isel['piece'], _isel['line'], tuple(_imore), visible)
    if _selc['key'] == k:
        return _selc['data']
    labels, gaps = [], []
    sel = [item for item in selected_edges(spec) if item[0] in visible]
    for nm, eid, L in sel:
        P = _edge_poly(spec, nm, eid)
        labels.append((P[len(P) // 2], L, D.piece_center(D.piece(spec, nm)), ('edge', nm, eid)))
    if len(sel) == 1:                       # the gaps for one edge only (as the Tool tab's fields)
        gaps += gap_list(spec, ('edge', sel[0][0], sel[0][1]))
    lines = [(nm, lid) for kind, nm, lid in picked_lines(spec) if kind == 'iline' and nm in visible]
    for nm, lid in lines:
        P = D.internal(D.piece(spec, nm), lid)['pts']
        if len(P) >= 2:
            p, away = _line_label(P)
            labels.append((p, S.polyline_len(P), away, ('iline', nm, lid)))
    if len(lines) == 1:                      # only the visible line contributes its gaps
        gaps += gap_list(spec, ('iline',) + lines[0])
    data = (labels, [(a, b, mm, False) for a, b, mm, i in gaps])
    _selc.update(key=k, data=data)
    return data


_seamc = {'key': None, 'data': {}}


def _side_rows(spec, sides, ol):
    """[(text, rgba)] for seam sides [(seam, key)]: the difference to the partner, then this side's and the
    partner's lengths, colored by the difference."""
    from .shape_tool import num
    ctx = bpy.context
    out = []
    for sm, k2 in sides:
        o = 'b' if k2 == 'a' else 'a'
        la, lb = S.side_length(spec, sm[k2], ol), S.side_length(spec, sm[o], ol)
        out.append((f"{num(ctx, la - lb, True)}  ({num(ctx, la)} / {num(ctx, lb)})", ease_color(la - lb)))
    return out


def _seams_on(spec, thing):
    """The seams on an outline edge ('edge', piece, eid) or an internal line ('iline', piece, line), one row each
    (user 2026-10-23: the seam labels on every edge of the piece under the cursor ran into each other; an edge may
    carry several seams, a cuff's top six). Kept per pattern version."""
    k, thing = (store._cache.get('key'), id(spec)), tuple(thing)
    if _seamc['key'] != k:
        _seamc.update(key=k, data={})
    if thing in _seamc['data']:
        return _seamc['data'][thing]
    nm = thing[1]
    sms = [(sm, k2) for sm in spec.get('seams', []) for k2 in 'ab' if sm[k2]['piece'] == nm]
    ol = {p['name']: S.Outline(p) for p in spec['pieces']
          if p['name'] in {sm[x]['piece'] for sm, _ in sms for x in 'ab'}}
    sides = []
    if thing[0] == 'iline':
        sides = [(sm, k2) for sm, k2 in sms if sm[k2].get('line') == thing[2]]
    elif thing[0] == 'edge':
        E = _edge_poly(spec, nm, thing[2])
        a, d = E[:-1], E[1:] - E[:-1]
        dd = np.maximum((d * d).sum(1), 1e-12)
        for sm, k2 in sms:
            if 'line' in sm[k2]:
                continue
            P = S.side_polyline(spec, sm[k2], ol, step=2.0)
            Q = P[1:-1] if len(P) > 2 else (P[:1] + P[-1:]) / 2      # its inside points: not one only touching an end
            t = np.clip((((Q[:, None, :] - a[None]) * d[None]).sum(2)) / dd[None], 0.0, 1.0)
            dist = np.linalg.norm(Q[:, None, :] - (a[None] + t[..., None] * d[None]), axis=2).min(1)
            if (dist < 0.3).any():
                sides.append((sm, k2))
    data = _side_rows(spec, sides, ol)
    _seamc['data'][thing] = data
    return data


_dragc = {'key': None, 'data': None}


def _drag_seams(dr):
    """[(point, piece center, text, rgba)]: the seam sides a drag is changing (their length moved), the difference
    to the partner only."""
    from .shape_tool import num
    names = dr['names']
    if _dragc['key'] != id(dr):
        base = dr['spec']
        sides = [(i, k2) for i, sm in enumerate(base.get('seams', [])) for k2 in 'ab' if sm[k2]['piece'] in names]
        ol0 = {nm: S.Outline(D.piece(base, nm)) for nm in names}
        _dragc.update(key=id(dr), data={s: S.side_length(base, base['seams'][s[0]][s[1]], ol0) for s in sides})
    spec = dr['preview'] or dr['spec']
    ol, out = {}, []

    def outline(nm):
        if nm not in ol:
            ol[nm] = S.Outline(D.piece(spec, nm))
        return ol

    for (i, k2), L0 in _dragc['data'].items():
        if i >= len(spec.get('seams', [])):
            continue
        sm = spec['seams'][i]
        o = 'b' if k2 == 'a' else 'a'
        try:
            P = S.side_polyline(spec, sm[k2], outline(sm[k2]["piece"]))
            la = S.polyline_len(P)
            if abs(la - L0) < 0.05:
                continue
            lb = S.side_length(spec, sm[o], outline(sm[o]['piece']))
        except Exception:
            continue
        out.append((P[len(P) // 2], D.piece_center(D.piece(spec, sm[k2]['piece'])),
                    num(bpy.context, la - lb, True), ease_color(la - lb)))
    return out


def _fold_line(context, spec):
    """(piece, line) of the picked internal line while the tool is on, or None."""
    if not (tool_active(context) and PV.layout_empty(context) is not None and _isel['piece']):
        return None
    return selected_internal(context, spec)


def _draw_fold_ab(context, spec):
    """A and B at the middle of the picked internal line's two sides, on the pattern and on the placed shape (user
    2026-10-25; the fills: _draw_fold_sides)."""
    from . import fold_sides as FS
    s = selected_internal(context, spec)
    if s is not None:
        FS.draw_px(context, spec, s[0], s[1])


def _draw_fold_sides():
    """The picked internal line's sides filled: A orange, B blue, Moves darker, Layers' side taken out hatched (user
    2026-10-25)."""
    context = bpy.context
    try:
        from . import fold_sides as FS
        spec = store.get(context)[0]
        s = _fold_line(context, spec) if spec is not None and _state['drag'] is None else None
        if s is not None:
            FS.draw_view(context, spec, s[0], s[1])
    except Exception:
        pass


def _draw_px():
    """The numbers on the pattern (DECISIONS cloth-sim-dimension-display-2026-10-23, seams changed 2026-10-23): the
    length of the edge or internal line under the cursor, with a row per seam on it (the difference to the partner,
    both lengths); the picked ones' lengths, seams and gaps; a drag's gaps and the difference of each seam it
    changes. A piece under the cursor alone shows no seam numbers (the whole list: Walk > Lengths & Notches)."""
    context = bpy.context
    if not VV.allows_pattern(context):
        spec = store.get(context)[0] if tool_active(context) else None
        if spec is not None:
            _draw_fold_ab(context, spec)
        return
    try:
        from .shape_tool import num, _draw_gaps_px
        dragging = _state['drag'] is not None
        on_tool = tool_active(context) and PV.layout_empty(context) is not None
        picked = set()
        if on_tool and not dragging and (_esel or _isel['piece']):
            spec0 = store.get(context)[0]
            if spec0 is not None:
                M = PV.layout_matrix(context)
                labels, gl = _sel_dims(context, spec0)
                _draw_gaps_px(context, M, gl)
                items = []
                for p, L, aw, th in labels:
                    items.append((p, num(context, L), False, aw))
                    items += [(p, t, c, aw, r + 1) for r, (t, c) in enumerate(_seams_on(spec0, th))]
                draw_labels(context, M, items)
                _draw_fold_ab(context, spec0)
                picked = ({('edge',) + tuple(k) for k in _esel} | {('iline', _isel['piece'], _isel['line'])}
                          | {('iline',) + tuple(k) for k in _imore})
        vs = _view_state(context)
        if vs is None:
            return
        spec, names, hot, dragging, _ = vs
        M = PV.layout_matrix(context)
        if dragging:
            _draw_gap_labels(context, M)
        items = []
        if hot and hot[0] == 'edge' and (dragging or ('edge', hot[1], hot[2]) not in picked):
            L = edge_length(spec, hot[1], hot[2])                  # the edge a click picks, or dragged: its length
            if L is not None:
                P = _edge_poly(spec, hot[1], hot[2])
                ctr = D.piece_center(D.piece(spec, hot[1]))
                items.append((P[len(P) // 2], num(context, L), False, ctr))
                if not dragging:
                    items += [(P[len(P) // 2], t, c, ctr, r + 1) for r, (t, c) in enumerate(_seams_on(spec, hot))]
        if hot and hot[0] == 'iline' and (dragging or ('iline', hot[1], hot[2]) not in picked):
            it = next((x for x in D.piece(spec, hot[1]).get('internals', []) if x['id'] == hot[2]), None)
            if it is not None and len(it['pts']) >= 2:
                p, away = _line_label(it['pts'])
                items.append((p, num(context, S.polyline_len(it['pts'])), False, away))
                if not dragging:
                    items += [(p, t, c, away, r + 1) for r, (t, c) in enumerate(_seams_on(spec, hot))]
        if dragging:                                          # the seams the drag is changing: their difference
            items += [(pt, txt, col, ctr) for pt, ctr, txt, col in _drag_seams(_state['drag'])]
        lk = (_state['drag'] or {}).get('lock')
        if lk:                                                # Shift held: what the move keeps to, at the cursor
            o, end = np.asarray(lk[0], float), np.asarray(lk[2], float)
            items.append((end, lock_name(np.asarray(lk[1], float)), (1.0, 0.85, 0.3, 1.0),
                          o if np.linalg.norm(end - o) > 1e-6 else None))
        draw_labels(context, M, items)
    except Exception:
        pass


# ---------------------------------------------------------------- the tool's operators

class AC9_OT_SimPatternHover(bpy.types.Operator):
    """(internal) Edit Pieces: what is under the cursor"""
    bl_idname = "ac9_cloth_sim.pattern_hover"
    bl_label = "Pattern Hover"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        _state['hover_area'] = context.area.as_pointer() if context.area else None
        spec, outlines = store.get(context)
        if spec is not None and PV.layout_empty(context) is not None and context.area.type == 'VIEW_3D':
            kind = TB.under(context, event.mouse_region_x, event.mouse_region_y)[0]
            TB.hint(context, event, 'TRANSFORM_PATTERN' if tool_mode(context) == 'TRANSFORM' else 'EDIT_PATTERN',
                    kind)
            h = (None if kind not in ('PATTERN', 'PLANE') else
                 pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y))
            if h != _state['hover']:
                _state['hover'] = h
                context.area.tag_redraw()
        return {'PASS_THROUGH'}


BOX_PX = 4                    # a press that moved less is a click


def box_select(context, rect, mode='SET'):
    """Select the flat pattern pieces inside or touching the region rectangle rect (x0, y0, x1, y1): an outline point
    in it, or the rectangle inside the piece. mode: 'SET' (only these), 'ADD', 'SUB'. Returns the names."""
    from . import view_visibility as VV
    spec, outlines = store.get(context)
    region, rv3d = context.region, context.region_data
    M = PV.layout_matrix(context)
    if spec is None or M is None:
        return []
    x0, y0, x1, y1 = rect
    objs = PV.piece_objects()
    hit = []
    for pc in spec['pieces']:
        ob = objs.get(pc['name'])
        if ob is None or not VV.object_allowed(context, ob):
            continue
        B = S.Outline(pc).B
        P = np.array([(v.x, v.y) if v is not None else (np.nan, np.nan)
                      for v in (location_3d_to_region_2d(region, rv3d, Vector(w)) for w in to_world(M, B))])
        ok = np.isfinite(P).all(1)
        if not ok.any():
            continue
        Q = P[ok]
        inside = ((Q[:, 0] >= x0) & (Q[:, 0] <= x1) & (Q[:, 1] >= y0) & (Q[:, 1] <= y1)).any()
        if not inside:                                  # the box inside the piece: its center in the outline
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            X, Y = Q[:, 0], Q[:, 1]
            Xn, Yn = np.roll(X, -1), np.roll(Y, -1)
            cr = ((Y > cy) != (Yn > cy)) & (cx < (Xn - X) * (cy - Y) / np.where(Yn != Y, Yn - Y, 1e-12) + X)
            inside = bool(cr.sum() % 2)
        if inside:
            hit.append(pc['name'])
    if mode == 'SET':
        SEL.deselect_all(context)
    for nm in hit:
        objs[nm].select_set(mode != 'SUB')
    act = context.view_layer.objects.active
    if hit and mode != 'SUB' and (act is None or not act.select_get()):
        context.view_layer.objects.active = objs[hit[0]]
    return hit


class AC9_OT_SimPatternDrag(bpy.types.Operator):
    """Drag a corner, an edge or a curve handle of a flat pattern piece (X / Y: axis, Shift: 0/45/90 deg, type a length, Ctrl: no snap)"""
    bl_idname = "ac9_cloth_sim.pattern_drag"
    bl_label = "Move Pattern"
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and store.get(context)[0] is not None and PV.layout_empty(context) is not None)

    def _delta(self, context, event):
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        d = np.zeros(2) if q is None else q - self.grab_mm
        along = None
        if self.axis == 'X':
            d[1], along = 0.0, np.array([1.0, 0.0])
        elif self.axis == 'Y':
            d[0], along = 0.0, np.array([0.0, 1.0])
        self.snapped, self.lock = None, None
        if self.typed:
            # in the scene's unit, as every label on the pattern (user 2026-11-11: one unit; it was mm, 2026-10-23)
            v = U.typed_mm(context, self.typed)
            if v is not None:
                if self.axis == 'X':
                    d = np.array([v, 0.0])
                elif self.axis == 'Y':
                    d = np.array([0.0, v])
                else:
                    n = np.linalg.norm(d)
                    d = d / n * v if n > 1e-9 else np.array([v, 0.0])
            return d
        if event.shift and not self.axis and np.linalg.norm(d) > 1e-9:
            # held to 0 / 45 / 90 degrees (DECISIONS cloth-sim-drag-distance-snap-2026-10-23, b-1)
            d = SN.lock_angle((0.0, 0.0), d, 45.0)
            along = d / np.linalg.norm(d)
            self.lock = along
        from .shape_tool import snap_settings
        if snap_settings(context)[0] != bool(event.ctrl):    # Ctrl: the other way while held
            d = self._snap(context, event, d, along)
        return d

    def _snap(self, context, event, d, along):
        """d snapped: a corner onto a corner, a line's end onto its piece's outline or lines (points first, 12 px);
        else to the middle, even spacing or a distance another internal line has (8 px); a whole piece lines up
        with the other pieces' corners. self.snapped: what it snapped to, for the view."""
        g = self.grab
        mmpx = px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        if g[0] in ('corner', 'piece') and along is None:
            # a corner onto another corner (moving a piece: any of its corners, the nearest pair)
            mine = (D.corners(D.piece(self.spec, g[1])) if g[0] == 'piece' else np.atleast_2d(self.grab_pt))
            best = None
            smap = D.symmetric_edit(D.piece(self.spec, g[1])) if g[0] == 'corner' else None
            for pc in self.spec['pieces']:
                if pc['name'] == g[1]:
                    if g[0] == 'piece':
                        continue
                    # not its own mirror corner: that one moves with it
                    others = [c for k, c in enumerate(D.corners(pc)) if k != g[2] and not (smap and k == smap[g[2]])]
                else:
                    others = list(D.corners(pc))
                for c in others:
                    dd = np.linalg.norm(mine + d - c, axis=1)
                    j = int(dd.argmin())
                    if dd[j] <= CORNER_PX * mmpx and (best is None or dd[j] < best[0]):
                        best = (float(dd[j]), c - mine[j], np.asarray(c, float))
            if best is not None:
                self.snapped = ('point', best[2])
                return best[1]
            if smap and smap[g[2]] != g[2]:
                # onto the center line of a piece edited symmetrically: it meets its mirror there and the two become
                # one corner (user 2026-10-27: a point in the exact middle)
                c, u = D.self_axis(D.piece(self.spec, g[1]))
                nrm = np.array([u[1], -u[0]])
                off = float((self.grab_pt + d - c) @ nrm)
                if abs(off) <= CORNER_PX * mmpx:
                    self.snapped = ('point', self.grab_pt + d - off * nrm)
                    return d - off * nrm
        if g[0] == 'corner':
            # in line with another corner of its piece across or up and down (user 2026-11-11: a trapezoid back to a
            # rectangle; Fable review): x and / or y, the dotted lines shown
            smap = D.symmetric_edit(D.piece(self.spec, g[1]))
            C = D.corners(D.piece(self.spec, g[1]))
            others = np.array([c for k, c in enumerate(C) if k != g[2] and not (smap and k == smap[g[2]])])
            if len(others):
                shift, lines = SN.align_snap(self.grab_pt + d, others, SN.GAP_PX * mmpx)
                if along is not None:                # held: only along the axis it is held to
                    keep = [ax for ax in (0, 1) if abs(along[ax]) > 0.99]
                    shift = np.array([shift[ax] if ax in keep else 0.0 for ax in (0, 1)])
                    lines = [x for x in lines if x[0] in keep]
                if lines:
                    self.snapped = ('align', [(ax, self.grab_pt + d + shift, others[j]) for ax, i, j in lines])
                    return d + shift
        if g[0] in ('iend', 'inode') and along is None and self.ptargets is not None:
            p, kind, _ = SN.snap(self.grab_pt + d, CORNER_PX * mmpx, self.ptargets, SN.POINT_KINDS + SN.LINE_KINDS)
            if kind is not None:
                self.snapped = ('point', p)
                return p - self.grab_pt
        if g[0] == 'piece':
            if self.others is None or not len(self.others):
                return d
            mine = D.corners(D.piece(self.spec, g[1])) + d
            shift, lines = SN.align_snap(mine, self.others, SN.GAP_PX * mmpx)
            if along is not None:                    # held: only the axis it is held to
                keep = [ax for ax in (0, 1) if abs(along[ax]) > 0.99]
                shift = np.array([shift[ax] if ax in keep else 0.0 for ax in (0, 1)])
                lines = [x for x in lines if x[0] in keep]
            if lines:
                self.snapped = ('align', [(ax, mine[i] + shift, self.others[j]) for ax, i, j in lines])
            return d + shift
        if g[0] == 'notch' and along is None:
            # onto an edge's middle or where the partner's notch falls (not a corner; user 2026-11-11)
            pc = D.piece(self.spec, g[1])
            eid, t = outline_point(pc, self.grab_pt + d)
            e2, t2, kind = D.snap_notch(self.spec, g[1], eid, t, CORNER_PX * mmpx, skip=g[2])
            if kind is not None:
                p = S.Outline(pc).at(S.Outline(pc).s_of(e2, t2))
                self.snapped = ('point', p)
                return p - self.grab_pt
            return d
        if g[0] in ('corner', 'iend', 'iline', 'edge'):
            # what the rays meet does not move with the thing (gap_probe leaves its moving lines out): the lines
            # gathered when the drag started, the thing moved by d, no copy of the pattern per mouse move
            if self.still is None:
                return d
            A, B, P0 = self.still
            if g[0] in ('corner', 'iend'):
                pr = (SN.point_probe(self.grab_pt + d), A, B)
            elif g[0] == 'iline':
                pr = (SN.line_probe(P0 + d, self.frame), A, B)
            else:
                pr = (SN.edge_probe(P0 + d, self.frame), A, B)
            if pr is None:
                return d
            shift, what = SN.gap_snap(pr[0], SN.gauge(*pr), SN.GAP_PX * mmpx, self.refs, along)
            if what:
                self.snapped = ('gap', what)
            return d + shift
        return d

    def _edit(self, sp, d):
        """The drag's edit on the spec sp (in place); the pieces it changed."""
        g = self.grab
        if self.tgroup:                              # Whole Pieces: the picked pieces and lines, as one
            ch, self.rigid = D.transform_group(sp, *self.tgroup, shift=tuple(d))
            return ch
        if self.group:                               # the picked lines, the one grabbed among them
            return move_lines(sp, self.group, d)
        if g[0] == 'corner':                         # dropped on the corner next to it: the two become one
            return D.move_corners(sp, g[1], {g[2]: tuple(d)}, merge=True)
        if g[0] == 'edge':
            return D.move_edge(sp, g[1], g[2], tuple(d))
        if g[0] == 'piece':
            self.rigid = D.move_piece(sp, g[1], shift=tuple(d))
            return list(self.rigid)
        if g[0] in ('iend', 'iline'):
            return D.move_internal(sp, g[1], g[2], g[3] if g[0] == 'iend' else 'all', tuple(d))
        if g[0] == 'inode':
            return IL.move_node(sp, g[1], g[2], g[3], tuple(d))
        if g[0] == 'ihandle':
            return IL.set_handle(sp, g[1], g[2], g[3], g[4], self.grab_pt + d)
        if g[0] == 'notch':                          # slides along the outline: the nearest outline point
            eid, t = outline_point(D.piece(sp, g[1]), self.grab_pt + d)
            return D.move_notch(sp, g[1], g[2], eid, t)
        return D.set_handle(sp, g[1], g[2], g[3], self.grab_pt + d)

    def _apply(self, d):
        names = {self.grab[1]} | {x[1] for x in self.group}
        if self.tgroup:
            names |= set(self.tgroup[0]) | {n for n, _ in self.tgroup[1]}
        for nm in list(names) if (self.grab[0] != 'piece' or self.tgroup) else ():
            tw = D.twin_of(self.spec, nm)
            if tw:
                names.add(tw)
        sp = light_copy(self.spec, names)
        return sp, self._edit(sp, d)

    def _update(self, context, event):
        if not self.dragging:
            px = np.hypot(event.mouse_region_x - self.press[0], event.mouse_region_y - self.press[1])
            if px < context.preferences.inputs.drag_threshold_mouse:
                return
            self.dragging = True
        self.delta = self._delta(context, event)
        sp, ch = self._apply(self.delta)
        dr = _state['drag']
        bad = D.problems(sp, ch)
        for x in self.group or [self.grab]:
            if x[0] in ('iend', 'iline', 'inode', 'ihandle'):
                bad += D.internal_problems(sp, x[1], [x[2]])
        dr.update(preview=sp, names=ch, bad=bad, gaps=gap_list(sp, self.grab, self.frame), snapped=self.snapped,
                  mmpx=px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y),
                  lock=(self.grab_mm, self.lock, self.grab_mm + self.delta) if getattr(self, 'lock', None) is not None
                  else None)
        from .shape_tool import fmt
        txt = tx("Move {0}, {1} ({2})", fmt(context, self.delta[0], True), fmt(context, self.delta[1], True),
                 fmt(context, np.linalg.norm(self.delta)))
        if self.axis:
            txt += tx("  along {0}", self.axis)
        if self.group:
            txt += tx("  ({0} lines)", len(self.group))
        if self.tgroup:
            txt += tx("  ({0} pieces, {1} lines)", len(self.tgroup[0]), len(self.tgroup[1]))
        if self.typed:
            txt += f"  [{self.typed}]"
        ln = D.seam_lengths(sp, ch)
        if self.grab[0] == 'notch':
            txt += "   " + walk_text(sp, self.grab[1])
        elif ln:
            w = max(ln, key=lambda x: abs(x[1] - x[2]))
            txt += tx("   seam {0}: {1} / {2} ({3})", w[0], U.num(context, w[1]), U.num(context, w[2]), U.num(context, w[1] - w[2], signed=True))
        if dr['bad']:
            txt += "   NOT ALLOWED: " + dr['bad'][0]
        if self.snapped and self.snapped[0] == 'gap':
            txt += "   " + ", ".join(tx(GAP_NAMES[w[0]]) for w in self.snapped[1])
        txt += tx("   X/Y: axis   Shift: 0/45/90 deg   type a length   Ctrl: no snap   Esc / right click: cancel")
        context.area.header_text_set(txt)

    @CL.logged
    def invoke(self, context, event):
        spec, outlines, self._owner = store.begin_edit(context)
        self.M = PV.layout_matrix(context)
        kind, ob, idx = TB.under(context, event.mouse_region_x, event.mouse_region_y)
        if kind == 'PLACED':                     # a placed piece's object: select it, to move it with G / R
            from . import garment_mark as GM     # and the point clicked shown on the pattern too
            GM.mark(context, ob, event.mouse_region_x, event.mouse_region_y)
            return {'FINISHED'} if TB.select_object(context, ob, extend=event.shift, face_index=idx) else {'CANCELLED'}
        if kind == 'GARMENT':                    # the dressed garment: pick the piece clicked (the Tool tab shows it)
            from . import garment_mark as GM     # and the point clicked shown on the pattern too
            GM.mark(context, ob, event.mouse_region_x, event.mouse_region_y)
            name = TB.garment_piece(ob, idx)
            return {'FINISHED'} if name and TB.pick_garment_piece(context, ob, name, extend=event.shift) else {'CANCELLED'}
        if kind not in ('PATTERN', 'PLANE', 'NONE'):
            return {'CANCELLED'}
        g = None if kind == 'NONE' else pick(context, spec, outlines, self.M, event.mouse_region_x,
                                              event.mouse_region_y)
        self.mode = tool_mode(context)
        self.box_piece = None
        if g is None and self.mode == 'EDIT' and kind != 'NONE':
            # Edit Pieces inside a piece: a drag boxes corners and lines, a click still picks the piece (its Tool tab)
            raw = pick_any(context, spec, outlines, self.M, event.mouse_region_x, event.mouse_region_y)
            self.box_piece = raw[1] if raw and raw[0] == 'piece' else None
        if g is None:                           # nothing under the cursor: a drag boxes pieces, a click drops all
            if _isel['piece'] and not event.shift:
                select_internal(context)
            if _esel and not event.shift:
                select_edge(context)
            if _csel and not event.shift:
                select_corner(context)
            return self._box_start(context, event)
        self.spec = spec
        self.grab = g
        # a picked edge or internal line grabbed while more are picked (Shift+click): they all move (user 2026-10-25)
        sel = (picked_lines(self.spec) if g[0] in ('edge', 'iline', 'corner') and not event.shift
               and self.mode != 'TRANSFORM' else [])
        me = ('corner', g[1], corner_eid(self.spec, g[1], g[2])) if g[0] == 'corner' else tuple(g[:3])
        self.group = sel if len(sel) > 1 and me in sel else []
        # Whole Pieces: a picked piece or line grabbed while more are picked: they all move (user 2026-10-28)
        self.tgroup = None
        if self.mode == 'TRANSFORM' and not event.shift:
            P, L = transform_targets(context, self.spec, g)
            if len(P) + len(L) > 1:
                self.tgroup = (P, L)
        self.grab_mm = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        self.grab_pt = hot_point(self.spec, g)
        self.axis, self.typed, self.delta, self.rigid = None, '', np.zeros(2), None
        self.snapped, self.frame, self.refs, self.ptargets, self.others = None, None, None, None, None
        self.still = None
        if g[0] in ('corner', 'iend', 'iline', 'edge'):
            self.frame = gap_frame(self.spec, g)
            pr = gap_probe(self.spec, g, self.frame)
            if pr is not None:
                self.refs = SN.same_refs(D.piece(self.spec, g[1]), pr[0], g[2] if g[0] in ('iend', 'iline') else None)
                pc = D.piece(self.spec, g[1])
                P0 = (np.asarray(D.internal(pc, g[2])['pts'], float) if g[0] == 'iline' else
                      np.asarray(pc['edges'][D.edge_index(pc, g[2])]['pts'], float) if g[0] == 'edge' else None)
                self.still = (pr[1], pr[2], P0)
        if g[0] in ('iend', 'inode'):            # its piece's outline and other lines (not its own line)
            pc = copy.deepcopy(D.piece(self.spec, g[1]))
            pc['internals'] = [it for it in pc.get('internals', []) if it['id'] != g[2]]
            self.ptargets = SN.Targets({'pieces': [pc]})
        if g[0] == 'piece':
            visible = VV.pattern_names(context, self.spec)
            cs = [D.corners(pc) for pc in self.spec['pieces']
                  if pc['name'] in visible and pc['name'] != g[1] and pc.get('enabled', True)]
            self.others = np.vstack(cs) if cs else np.zeros((0, 2))
        # a piece grabbed by its inside moves only once the mouse went past Blender's drag threshold: let go before
        # that and the click selects the piece (Shift: adds or takes it out; user 2026-10-21, Fable review
        # REVIEW_fable_selection_flow_2026-10-21: a 1 px shake used to move it)
        self.press = (event.mouse_region_x, event.mouse_region_y)
        # an internal line too: a click picks it (its fold, elastic and seams in the Tool tab), a drag moves it
        # an outline edge too: a click picks it (its length in the Tool tab; Shift adds), a drag moves it (2026-10-22)
        # a corner too: a click picks it (Shift adds), a drag moves it (user 2026-10-25)
        self.dragging = g[0] not in ('piece', 'iline', 'edge', 'corner')
        _state['drag'] = {'spec': self.spec, 'preview': None, 'names': [g[1]], 'grab': g, 'bad': []}
        _state['drag']['owner'] = self._owner
        _state['drag']['area'] = context.area.as_pointer() if context.area else None
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        if _state['drag'] is not None and _state['drag'].get('owner') is not self._owner:
            return
        _state['drag'] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    # ---- the box (Shift: add, Ctrl: take out; as Blender's Select Box)
    def _box_start(self, context, event):
        self.box = [(event.mouse_region_x, event.mouse_region_y)] * 2
        self.box_mode = 'ADD' if event.shift else ('SUB' if event.ctrl else 'SET')
        self._box_h = bpy.types.SpaceView3D.draw_handler_add(self._box_draw, (), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _box_draw(self):
        try:
            (x0, y0), (x1, y1) = self.box
            if abs(x1 - x0) + abs(y1 - y0) < BOX_PX:
                return
            gpu.state.blend_set('ALPHA')
            sh = gpu.shader.from_builtin('UNIFORM_COLOR')
            sh.bind()
            sh.uniform_float('color', (1, 1, 1, 0.08))
            batch_for_shader(sh, 'TRIS', {'pos': [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]}).draw(sh)
            sh.uniform_float('color', (1, 1, 1, 0.8))
            batch_for_shader(sh, 'LINE_LOOP', {'pos': [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]}).draw(sh)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def _box_modal(self, context, event):
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE':
            self.box[1] = (event.mouse_region_x, event.mouse_region_y)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            bpy.types.SpaceView3D.draw_handler_remove(self._box_h, 'WINDOW')
            (x0, y0), (x1, y1) = self.box
            if abs(x1 - x0) + abs(y1 - y0) < BOX_PX:          # a click on nothing: nothing selected
                if self.box_mode == 'SET':
                    SEL.deselect_all(context)
                if getattr(self, 'box_piece', None):            # Edit Pieces, a click inside a piece: the piece
                    TB.select_piece(context, self.box_piece, extend=self.box_mode == 'ADD')
                return {'FINISHED'}
            r = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            mode = getattr(self, 'mode', None)
            if mode == 'EDIT':
                box_points(context, r, self.box_mode)
            elif mode == 'TRANSFORM':
                box_transform(context, r, self.box_mode)
            else:
                box_select(context, r, self.box_mode)
            return {'FINISHED'}
        if event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            bpy.types.SpaceView3D.draw_handler_remove(self._box_h, 'WINDOW')
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    @CL.logged_end
    def modal(self, context, event):
        if not _edit_current(self, context, event):
            return {'CANCELLED'}
        if getattr(self, 'box', None) is not None:
            return self._box_modal(context, event)
        context.area.tag_redraw()
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self._update(context, event)
        elif (event.type == 'LEFTMOUSE' and event.value == 'RELEASE') or (
                event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS'):
            dr = _state['drag']
            if not self.dragging:                    # a click on a piece: select it; on an internal line: pick it
                self._end(context)
                # Shift: adds or takes out, the edges, the internal lines and the corners together (user 2026-10-25)
                if not event.shift:
                    select_corner(context)
                if self.grab[0] == 'corner':
                    if not event.shift:
                        select_internal(context)
                        select_edge(context)
                    select_corner(context, self.grab[1], corner_eid(self.spec, self.grab[1], self.grab[2]),
                                  extend=event.shift)
                    return {'FINISHED'}
                if self.grab[0] == 'edge':
                    if not event.shift:
                        select_internal(context)
                    select_edge(context, self.grab[1], self.grab[2], extend=event.shift)
                    return {'FINISHED'}
                if not event.shift:
                    select_edge(context)
                if self.grab[0] == 'iline':
                    if self.mode == 'TRANSFORM' and not event.shift:     # a line alone: the pieces dropped
                        SEL.deselect_all(context)
                    select_internal(context, self.grab[1], self.grab[2], extend=event.shift)
                    return {'FINISHED'}
                if not event.shift:
                    select_internal(context)
                TB.select_piece(context, self.grab[1], extend=event.shift)
                return {'FINISHED'}
            if dr['bad'] or np.linalg.norm(self.delta) < 1e-6:
                if dr['bad']:
                    self.report({'WARNING'}, rpt("Not applied: ") + dr['bad'][0])
                self._end(context)
                return {'CANCELLED'}
            msg = commit(context, dr['preview'], dr['names'],
                         old_spec=self.spec if self.grab[0] != 'notch' else None,   # a notch moves no cloth
                         rigid=self.rigid if (self.grab[0] == 'piece' or self.tgroup) else None, owner=self._owner)
            if msg:
                self.report({'INFO'}, msg)
            self._end(context)
            # the line or edge moved stays picked: its gaps can be typed in the Tool tab (2026-10-23, b-2)
            if self.group or self.tgroup:            # the things moved together stay picked as they were
                pass
            elif self.grab[0] in ('iend', 'iline', 'inode', 'ihandle'):
                select_internal(context, self.grab[1], self.grab[2])
            elif self.grab[0] == 'edge':
                select_edge(context, self.grab[1], self.grab[2])
            elif self.grab[0] == 'corner':
                select_internal(context)
                select_edge(context)
                # the corner where it was let go (dropped on the next one: the corner it became, 2026-10-27)
                pc = D.piece(dr['preview'], self.grab[1])
                k = int(np.linalg.norm(D.corners(pc) - (self.grab_pt + self.delta), axis=1).argmin())
                select_corner(context, self.grab[1], pc['edges'][k]['id'])
            return {'FINISHED'}
        elif event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            self._end(context)
            return {'CANCELLED'}
        elif event.value == 'PRESS' and event.type in {'X', 'Y'}:
            self.axis = None if self.axis == event.type else event.type
            self._update(context, event)
        elif event.value == 'PRESS' and event.type == 'BACK_SPACE':
            self.typed = self.typed[:-1]
            self._update(context, event)
        elif event.value == 'PRESS' and event.unicode and event.unicode in '0123456789.-':
            self.typed += event.unicode
            self._update(context, event)
        return {'RUNNING_MODAL'}


class AC9_OT_SimPatternEdit(bpy.types.Operator):
    """Edit the pattern under the cursor"""
    bl_idname = "ac9_cloth_sim.pattern_edit"
    bl_label = "Edit Pieces"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[('SPLIT', "Split Edge", "Add a corner on the edge under the cursor"),
                                          ('REMOVE', "Remove Corner", "Remove the corner under the cursor (its two edges become one straight edge)"),
                                          ('CURVE', "Curve / Straighten", "Make the edge under the cursor a curve with two handles, or straight again"),
                                          ('NOTCH', "Add Notch", "Put a notch on the outline under the cursor")])

    @classmethod
    def description(cls, context, props):
        return {'SPLIT': "Add a corner on the edge under the cursor (seams stay where they are)",
                'REMOVE': "Remove the corner under the cursor: its two edges become one straight edge (seam ends keep their fraction of it)",
                'CURVE': "Make the edge under the cursor a curve with two handles, or straight again",
                'NOTCH': "Put a notch (a matching mark) on the outline under the cursor; X removes it, a drag slides it"}[props.action]

    @classmethod
    def poll(cls, context):
        return AC9_OT_SimPatternDrag.poll(context)

    def invoke(self, context, event):
        spec, outlines = store.get(context)
        h = pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        notch_only = self.action == 'NOTCH' or (self.action == 'REMOVE' and h and h[0] == 'notch')
        try:
            if self.action == 'NOTCH' and h and h[0] in ('edge', 'corner'):
                if h[0] == 'corner':
                    pc = D.piece(sp, h[1])
                    eid, u = pc['edges'][h[2]]['id'], 0.0
                else:
                    eid, u = h[2], h[3]
                    from .shape_tool import snap_settings
                    if snap_settings(context)[0] != bool(event.ctrl):      # an edge's middle, the partner's notch
                        mmpx = px_to_mm(context, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
                        eid, u, _ = D.snap_notch(spec, h[1], eid, u, CORNER_PX * mmpx)

                def edit(x):
                    return list(D.add_notch(x, h[1], eid, u))
            elif self.action == 'REMOVE' and h and h[0] == 'notch':
                def edit(x):
                    return D.remove_notch(x, h[1], h[2])
            elif self.action == 'SPLIT' and h and h[0] == 'edge':
                def edit(x):
                    return list(D.split_edge(x, h[1], h[2], h[3]))
            elif self.action == 'REMOVE' and h and h[0] == 'corner':
                def edit(x):
                    return D.join_at_corner(x, h[1], h[2])
            elif self.action == 'SPLIT' and h and h[0] == 'iline':
                q = mouse_mm(context, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)

                def edit(x):
                    return IL.insert_node(x, h[1], h[2], q)
            elif self.action == 'REMOVE' and h and h[0] == 'inode':
                def edit(x):
                    return IL.delete_node(x, h[1], h[2], h[3])
            elif self.action == 'CURVE' and h and h[0] in ('iline', 'ihandle'):
                q = mouse_mm(context, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
                span = h[3] if h[0] == 'ihandle' else None

                def edit(x):
                    return IL.toggle_curve(x, h[1], h[2], q, span)
                it0 = D.internal(D.piece(sp, h[1]), h[2])
                if not IL.editable(it0) and not it0.get('closed') and IL.fit_error(it0) > 1.0:
                    self.report({'WARNING'}, rpt("The curve is up to {0} off the line: K first for a closer fit (Ctrl+Z to undo)", U.fmt(context, IL.fit_error(it0))))
            elif self.action == 'REMOVE' and h and h[0] in ('iend', 'iline'):
                def edit(x):
                    gone = D.delete_internal(x, h[1], h[2])
                    if gone:
                        self.report({'INFO'}, rpt("Removed the line and its seam(s) {0}", ', '.join(gone)))
                    return [h[1]]
            elif self.action == 'CURVE' and h and h[0] in ('edge', 'handle'):
                pc = D.piece(sp, h[1])
                if pc['edges'][D.edge_index(pc, h[2])].get('ctrl'):
                    def edit(x):
                        return D.make_straight(x, h[1], h[2])
                else:
                    def edit(x):
                        return D.make_curve(x, h[1], h[2])[0]
                    err = D.make_curve(S.loads(S.dumps(spec)), h[1], h[2])[1]
                    if err > 1.0:
                        self.report({'WARNING'}, rpt("The curve is up to {0} off the edge: split the edge first for a closer fit (Ctrl+Z to undo)", U.fmt(context, err)))
            else:
                return {'PASS_THROUGH'}
            ch = edit(sp)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        bad = D.problems(sp, ch)
        if bad:
            self.report({'WARNING'}, bad[0])
            return {'CANCELLED'}
        msg = commit(context, sp, ch, old_spec=None if notch_only else old)
        if msg:
            self.report({'INFO'}, msg)
        if notch_only:
            self.report({'INFO'}, walk_text(sp, h[1]))
        _state['hover'] = None
        return {'FINISHED'}


class AC9_OT_SimPatternRotate(bpy.types.Operator):
    """Rotate the pattern piece under the cursor about its center (type degrees, Shift: 15 degree steps); the garment keeps hanging as it was"""
    bl_idname = "ac9_cloth_sim.pattern_rotate"
    bl_label = "Rotate Piece"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return AC9_OT_SimPatternDrag.poll(context)

    def _angle(self, context, event):
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        if q is None:
            return 0.0
        a = np.degrees(np.arctan2(q[1] - self.c[1], q[0] - self.c[0]) - self.a0)
        a = (a + 180.0) % 360.0 - 180.0
        if self.typed:
            try:
                a = float(self.typed)
            except ValueError:
                pass
        elif event.shift:
            a = round(a / 15.0) * 15.0
        return a

    def _update(self, context, event):
        self.deg = self._angle(context, event)
        tw = D.twin_of(self.spec, self.piece_name)
        sp = light_copy(self.spec, {self.piece_name, tw} - {None})
        self.rigid = D.move_piece(sp, self.piece_name, deg=self.deg)
        _state['drag'].update(preview=sp, names=list(self.rigid), bad=D.problems(sp, list(self.rigid)))
        context.area.header_text_set(tx("Rotate {0}: {1:+.1f} deg", self.piece_name, self.deg) + (tx("  [{0}]", self.typed) if self.typed else "")
                                     + tx("   type degrees   Shift: 15 deg steps   click / Enter: done   Esc / right click: cancel"))

    def invoke(self, context, event):
        spec, outlines, self._owner = store.begin_edit(context)
        self.M = PV.layout_matrix(context)
        h = pick(context, spec, outlines, self.M, event.mouse_region_x, event.mouse_region_y, mode=None)
        if h is None:
            return {'PASS_THROUGH'}
        self.spec = spec
        self.piece_name = h[1]
        self.c = D.piece_center(D.piece(self.spec, self.piece_name))
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        self.a0 = np.arctan2(q[1] - self.c[1], q[0] - self.c[0]) if q is not None else 0.0
        self.typed, self.deg, self.rigid = '', 0.0, None
        _state['drag'] = {'spec': self.spec, 'preview': None, 'names': [self.piece_name], 'grab': ('piece', self.piece_name),
                          'bad': []}
        _state['drag']['owner'] = self._owner
        _state['drag']['area'] = context.area.as_pointer() if context.area else None
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        if _state['drag'] is not None and _state['drag'].get('owner') is not self._owner:
            return
        _state['drag'] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if not _edit_current(self, context, event):
            return {'CANCELLED'}
        context.area.tag_redraw()
        if event.type in {'MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self._update(context, event)
        elif event.value == 'PRESS' and event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
            dr = _state['drag']
            if dr['bad'] or abs(self.deg) < 1e-6:
                self._end(context)
                return {'CANCELLED'}
            msg = commit(context, dr['preview'], list(self.rigid), old_spec=self.spec, rigid=self.rigid, owner=self._owner)
            if msg:
                self.report({'INFO'}, msg)
            self._end(context)
            return {'FINISHED'}
        elif event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            self._end(context)
            return {'CANCELLED'}
        elif event.value == 'PRESS' and event.type == 'BACK_SPACE':
            self.typed = self.typed[:-1]
            self._update(context, event)
        elif event.value == 'PRESS' and event.unicode and event.unicode in '0123456789.-':
            self.typed += event.unicode
            self._update(context, event)
        return {'RUNNING_MODAL'}


class AC9_OT_SimPatternScale(bpy.types.Operator):
    """Scale the pattern piece under the cursor about its center (move the mouse, or type a factor; X / Y: one direction); its seams stay on its edges, a dressed garment takes the new shape"""
    bl_idname = "ac9_cloth_sim.pattern_scale"
    bl_label = "Scale Piece"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    piece: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    owner_scene: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    owner_text: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    factor: bpy.props.FloatProperty(name="Factor", description="How much larger (1.1) or smaller (0.9)",
                                    default=1.0, min=0.05, max=20.0, step=1, precision=3)
    axis: bpy.props.EnumProperty(name="Direction", items=[('BOTH', "Both", "Width and height"),
                                                          ('X', "Width", "Width only"),
                                                          ('Y', "Height", "Height only")], default='BOTH')

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None          # invoke needs the pattern's view; F9's execute does not

    def _factors(self, f):
        return (f if self.axis != 'Y' else 1.0), (f if self.axis != 'X' else 1.0)

    def _apply(self, sp, f):
        sx, sy = self._factors(f)
        return D.scale_piece(sp, self.piece, sx, sy)

    def _update(self, context, event):
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        f = 1.0 if q is None else float(np.linalg.norm(q - self.c)) / max(self.r0, 1e-6)
        if self.typed:
            try:
                f = float(self.typed)
            except ValueError:
                pass
        self.factor = max(f, 0.05)
        tw = D.twin_of(self.spec, self.piece)
        sp = light_copy(self.spec, {self.piece, tw} - {None})
        try:
            names, bad = self._apply(sp, self.factor), []
            bad = D.problems(sp, names)
        except ValueError as exc:
            names, bad = [self.piece], [str(exc)]
        _state['drag'].update(preview=sp, names=names, bad=bad)
        ax = {'BOTH': "", 'X': tx("  width only"), 'Y': tx("  height only")}[self.axis]
        context.area.header_text_set(tx("Scale {0}: x{1:.3f}", self.piece, self.factor) + ax
                                     + (tx("  [{0}]", self.typed) if self.typed else "")
                                     + tx("   type a factor   X / Y: one direction   click / Enter: done   Esc / right click: cancel"))

    def invoke(self, context, event):
        if not AC9_OT_SimPatternDrag.poll(context):
            return {'PASS_THROUGH'}
        spec, outlines, self._owner = store.begin_edit(context)
        self.M = PV.layout_matrix(context)
        h = pick(context, spec, outlines, self.M, event.mouse_region_x, event.mouse_region_y, mode=None)
        if h is None:
            return {'PASS_THROUGH'}
        self.spec = spec
        self.piece = h[1]
        self.owner_scene = str(context.scene.session_uid)
        self.owner_text = str(store.text(context).session_uid)
        self.c = D.piece_center(D.piece(self.spec, self.piece))
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        self.r0 = float(np.linalg.norm(q - self.c)) if q is not None else 1.0
        if self.r0 < 5.0:                          # the cursor at the center: scale from a fixed 50 mm away
            self.r0 = 50.0
        self.typed, self.axis = '', 'BOTH'
        _state['drag'] = {'spec': self.spec, 'preview': None, 'names': [self.piece], 'grab': ('piece', self.piece),
                          'bad': []}
        _state['drag']['owner'] = self._owner
        _state['drag']['area'] = context.area.as_pointer() if context.area else None
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        if _state['drag'] is not None and _state['drag'].get('owner') is not self._owner:
            return
        _state['drag'] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def execute(self, context):
        # F9: the factor and direction changed after the scale
        current_text = store.text(context)
        if (current_text is None or self.owner_scene != str(context.scene.session_uid)
                or self.owner_text != str(current_text.session_uid)):
            self.report({'WARNING'}, rpt("The pattern changed during this edit; start the edit again"))
            return {'CANCELLED'}
        spec, _ = store.get(context)
        if not self.piece or not any(p['name'] == self.piece for p in spec['pieces']):
            return {'CANCELLED'}
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            names = self._apply(sp, self.factor)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        bad = D.problems(sp, names)
        if bad:
            self.report({'WARNING'}, rpt("Not applied: ") + bad[0])
            return {'CANCELLED'}
        msg = commit(context, sp, names, old_spec=old)
        self.report({'INFO'}, rpt("Scaled {0} x{1:.3f}", self.piece, self.factor) + (rpt("; {0}", msg) if msg else "")
                    + rpt(F9))
        return {'FINISHED'}

    def modal(self, context, event):
        if not _edit_current(self, context, event):
            return {'CANCELLED'}
        context.area.tag_redraw()
        if event.type in {'MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            self._update(context, event)
        elif event.value == 'PRESS' and event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
            dr = _state['drag']
            self._end(context)
            if dr['bad'] or abs(self.factor - 1.0) < 1e-6:
                if dr['bad']:
                    self.report({'WARNING'}, rpt("Not applied: ") + dr['bad'][0])
                return {'CANCELLED'}
            msg = commit(context, dr['preview'], dr['names'], old_spec=self.spec, owner=self._owner)
            self.report({'INFO'}, rpt("Scaled {0} x{1:.3f}", self.piece, self.factor) + (rpt("; {0}", msg) if msg else "")
                        + rpt(F9))
            return {'FINISHED'}
        elif event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            self._end(context)
            return {'CANCELLED'}
        elif event.value == 'PRESS' and event.type in {'X', 'Y'}:
            self.axis = 'BOTH' if self.axis == event.type else event.type
            self._update(context, event)
        elif event.value == 'PRESS' and event.type == 'BACK_SPACE':
            self.typed = self.typed[:-1]
            self._update(context, event)
        elif event.value == 'PRESS' and event.unicode and event.unicode in '0123456789.':
            self.typed += event.unicode
            self._update(context, event)
        return {'RUNNING_MODAL'}


class AC9_OT_SimPatternTransform(bpy.types.Operator):
    """Move (G), rotate (R) or scale (S) what is picked about its middle: in Whole Pieces whole pieces and internal lines, in Edit Pieces corners, edges and internal lines (type a number, X / Y: one direction, Shift: angle, Ctrl: no snap)"""
    bl_idname = "ac9_cloth_sim.pattern_transform"
    bl_label = "Transform Picked"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    mode: bpy.props.EnumProperty(items=[('MOVE', "Move", ""), ('ROTATE', "Rotate", ""), ('SCALE', "Scale", "")],
                                 options={'SKIP_SAVE'})
    pieces: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # JSON names: these whole pieces (Duplicate)

    @classmethod
    def poll(cls, context):
        return AC9_OT_SimPatternDrag.poll(context)

    def invoke(self, context, event):
        if not VV.allows_pattern(context):
            return {'PASS_THROUGH'}
        spec, outlines, self._owner = store.begin_edit(context)
        self.M = PV.layout_matrix(context)
        self.tool = tool_mode(context) or 'TRANSFORM'
        h = pick(context, spec, outlines, self.M, event.mouse_region_x, event.mouse_region_y, mode=self.tool)
        self.spec = spec
        self.items, self.cpts, self.P, self.L = None, {}, [], []
        if self.pieces:
            import json
            have = {pc['name'] for pc in self.spec['pieces']}
            self.P = [n for n in json.loads(self.pieces) if n in have]
            if not self.P:
                return {'CANCELLED'}
            self.c = D.group_center(self.spec, self.P)
            names = set(self.P)
        elif self.tool == 'TRANSFORM':
            self.P, self.L = transform_targets(context, self.spec, h)
            if not self.P and not self.L:
                return {'PASS_THROUGH'}
            self.c = D.group_center(self.spec, self.P, self.L)
            names = set(self.P) | {n for n, _ in self.L}
        else:
            items = picked_lines(self.spec)
            me = edit_item(self.spec, h)
            if me is not None and me not in items:          # not picked: the thing under the cursor alone
                items = [me]
            if not items:
                return {'PASS_THROUGH'}
            self.items = items
            for kind, n, x in items:
                if kind in ('edge', 'corner'):
                    pc = D.piece(self.spec, n)
                    k = D.edge_index(pc, x)
                    self.cpts.setdefault(n, set()).update({k, (k + 1) % len(pc['edges'])} if kind == 'edge' else {k})
            self.L = [(n, x) for kind, n, x in items if kind == 'iline']
            pts = [D.corners(D.piece(self.spec, n))[sorted(ks)] for n, ks in self.cpts.items()]
            self.c = D.group_center(self.spec, [], self.L, np.vstack(pts) if pts else ())
            names = set(self.cpts) | {n for n, _ in self.L}
        self.names = names | {t for t in (D.twin_of(self.spec, n) for n in names) if t}
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        self.q0 = np.asarray(q if q is not None else self.c, float)
        v = self.q0 - self.c
        self.a0 = float(np.arctan2(v[1], v[0]))
        self.r0 = float(np.linalg.norm(v))
        if self.r0 < 5.0:                           # the cursor at the middle: scale from a fixed 50 mm away
            self.r0 = 50.0
        self.axis, self.typed, self.rigid, self.amount, self.snapped = None, '', None, None, None
        self.others = None
        if self.items is None and self.P:             # whole pieces move: they line up with the other pieces
            visible = VV.pattern_names(context, self.spec)
            cs = [D.corners(pc) for pc in self.spec['pieces'] if pc['name'] in visible
                  and pc['name'] not in self.names and pc.get('enabled', True)]
            self.others = np.vstack(cs) if cs else None
        _state['drag'] = {'spec': self.spec, 'preview': None, 'names': sorted(names),
                          'grab': ('piece', sorted(names)[0]), 'bad': []}
        _state['drag']['owner'] = self._owner
        _state['drag']['area'] = context.area.as_pointer() if context.area else None
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _snap(self, context, event, d):
        """d lined up: the moving pieces' corners onto the other pieces' corners' rows and columns (as the drag of
        a piece). Ctrl turns the snapping the other way while held. self.snapped: the lines, for the view."""
        from .shape_tool import snap_settings
        self.snapped = None
        if self.others is None or self.axis or snap_settings(context)[0] == bool(event.ctrl):
            return d
        mine = np.vstack([D.corners(D.piece(self.spec, n)) for n in self.P]) + d
        mmpx = px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        shift, lines = SN.align_snap(mine, self.others, SN.GAP_PX * mmpx)
        if event.shift:                                # held to an angle: only along a held axis
            n = np.linalg.norm(d)
            along = d / n if n > 1e-9 else np.zeros(2)
            keep = [ax for ax in (0, 1) if abs(along[ax]) > 0.99]
            shift = np.array([shift[ax] if ax in keep else 0.0 for ax in (0, 1)])
            lines = [x for x in lines if x[0] in keep]
        if lines:
            self.snapped = ('align', [(ax, mine[i] + shift, self.others[j]) for ax, i, j in lines])
        return d + shift

    def _value(self, context=None):
        """The typed number: a length in the scene's unit (as mm) for Move, degrees for Rotate, a factor for Scale."""
        try:
            v = float(self.typed) if self.typed else None
        except ValueError:
            return None
        return v * U.unit(context)[0] if v is not None and self.mode == 'MOVE' and context is not None else v

    def _update(self, context, event):
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        q = self.q0 if q is None else np.asarray(q, float)
        deg, k, d, v = 0.0, (1.0, 1.0), np.zeros(2), self._value(context)
        lock = None
        from .shape_tool import fmt
        if self.mode == 'MOVE':
            d = q - self.q0
            if self.axis == 'X':
                d[1] = 0.0
            elif self.axis == 'Y':
                d[0] = 0.0
            if v is not None:
                n = np.linalg.norm(d)
                d = (np.array([v, 0.0]) if self.axis != 'Y' else np.array([0.0, v])) if self.axis or n < 1e-9 \
                    else d / n * v
            else:
                if event.shift and not self.axis and np.linalg.norm(d) > 1e-9:     # 0 / 45 / 90 degrees
                    d = SN.lock_angle((0.0, 0.0), d, 45.0)
                    lock = d / np.linalg.norm(d)
                d = self._snap(context, event, d)
            txt = tx("Move {0}, {1}", fmt(context, d[0], True), fmt(context, d[1], True))
        elif self.mode == 'ROTATE':
            w = q - self.c
            deg = (np.degrees(np.arctan2(w[1], w[0]) - self.a0) + 180.0) % 360.0 - 180.0
            if v is not None:
                deg = v
            elif event.shift:
                deg = round(deg / 15.0) * 15.0
            txt = tx("Rotate {0:+.1f} deg", deg)
        else:
            f = float(np.linalg.norm(q - self.c)) / self.r0
            if v is not None:
                f = v
            f = max(f, 0.05)
            k = (f, 1.0) if self.axis == 'X' else (1.0, f) if self.axis == 'Y' else (f, f)
            txt = tx("Scale x{0:.3f}", f)
        self.amount = (deg, k, d)
        sp = light_copy(self.spec, self.names)
        try:
            if self.items is None:
                ch, self.rigid = D.transform_group(sp, self.P, self.L, deg, k, d, center=self.c)
            elif self.mode == 'MOVE':
                ch, self.rigid = move_lines(sp, self.items, d), None
            else:
                fm = D.group_map(deg, k, center=self.c)
                ch = []
                for n, ks in self.cpts.items():
                    C = D.corners(D.piece(self.spec, n))
                    ch += D.move_corners(sp, n, {j: tuple(fm(C[j])[0] - C[j]) for j in ks})
                ch += D.transform_group(sp, [], self.L, deg, k, center=self.c)[0]
                self.rigid = None
            bad = D.problems(sp, ch)
        except ValueError as exc:
            ch, bad = sorted(self.names), [str(exc)]
        _state['drag'].update(preview=sp, names=list(dict.fromkeys(ch)), bad=bad, snapped=self.snapped,
                              mmpx=px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y),
                              lock=(self.q0, lock, self.q0 + d) if lock is not None else None)
        if self.axis:
            txt += tx("  along {0}", self.axis)
        if self.typed:
            txt += f"  [{self.typed}]"
        n_p = len(self.P) if self.items is None else len(self.cpts)
        txt += (tx("  ({0} pieces, {1} lines)", n_p, len(self.L)) if self.items is None
                else tx("  ({0} items)", len(self.items)))
        if bad:
            txt += "   NOT ALLOWED: " + bad[0]
        txt += tx("   type a number   X/Y: one direction   Shift: angle   Ctrl: no snap   click / Enter: done   Esc / right click: cancel")
        context.area.header_text_set(txt)

    def _end(self, context):
        if _state['drag'] is not None and _state['drag'].get('owner') is not self._owner:
            return
        _state['drag'] = None
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if not _edit_current(self, context, event):
            return {'CANCELLED'}
        context.area.tag_redraw()
        if event.type in {'MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT'}:   # a held key shows at once (user 2026-11-11)
            self._update(context, event)
        elif event.value == 'PRESS' and event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
            dr = _state['drag']
            deg, k, d = self.amount
            same = abs(deg) < 1e-6 and abs(k[0] - 1.0) < 1e-6 and abs(k[1] - 1.0) < 1e-6 and np.linalg.norm(d) < 1e-6
            self._end(context)
            if dr['bad'] or same:
                if dr['bad']:
                    self.report({'WARNING'}, rpt("Not applied: ") + dr['bad'][0])
                return {'CANCELLED'}
            msg = commit(context, dr['preview'], dr['names'], old_spec=self.spec, rigid=self.rigid, owner=self._owner)
            if msg:
                self.report({'INFO'}, msg)
            return {'FINISHED'}
        elif event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            self._end(context)
            return {'CANCELLED'}
        elif event.value == 'PRESS' and event.type in {'X', 'Y'} and self.mode != 'ROTATE':
            self.axis = None if self.axis == event.type else event.type
            self._update(context, event)
        elif event.value == 'PRESS' and event.type == 'BACK_SPACE':
            self.typed = self.typed[:-1]
            self._update(context, event)
        elif event.value == 'PRESS' and event.unicode and event.unicode in '0123456789.-':
            self.typed += event.unicode
            self._update(context, event)
        return {'RUNNING_MODAL'}


class AC9_WT_TransformPattern(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = TRANSFORM_ID
    bl_label = "Whole Pieces"
    bl_description = ("Pick whole pieces and internal lines (click, Shift adds, drag from nothing: box) and move, "
                      "rotate or scale them together. G: move, R: rotate, S: scale about the middle of what is picked, "
                      "Shift+D: copy pieces, F2: name a piece. Corners, edges and the points of a line: Edit Pieces")
    bl_icon = "ops.transform.transform"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.pattern_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, None),
        ("ac9_cloth_sim.pattern_drag", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.pattern_drag", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, None),
        ("ac9_cloth_sim.pattern_drag", {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True}, None),
        ("ac9_cloth_sim.pick_piece_under", {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK'}, None),
        ("ac9_cloth_sim.pick_piece_under", {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK', "shift": True}, None),
        ("ac9_cloth_sim.pattern_transform", {"type": 'G', "value": 'PRESS'}, {"properties": [("mode", 'MOVE')]}),
        ("ac9_cloth_sim.pattern_transform", {"type": 'R', "value": 'PRESS'}, {"properties": [("mode", 'ROTATE')]}),
        ("ac9_cloth_sim.pattern_transform", {"type": 'S', "value": 'PRESS'}, {"properties": [("mode", 'SCALE')]}),
        ("ac9_cloth_sim.delete_selected", {"type": 'X', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.delete_selected", {"type": 'DEL', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.duplicate_pieces", {"type": 'D', "value": 'PRESS', "shift": True},
         {"properties": [("follow", True)]}),
        ("ac9_cloth_sim.piece_label", {"type": 'F2', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.context_menu", {"type": 'RIGHTMOUSE', "value": 'PRESS'},
         {"properties": [("tool", 'TRANSFORM_PATTERN')]}),
    )

    def draw_settings(context, layout, tool):
        if context.scene.ac9_cloth_sim.sync_pending:    # 3D Follows Edits off and edits waiting (AC9 Run)
            layout.label(text=tx("3D not updated"), icon='ERROR')
        layout.label(text=tx("Works on: ") + tx("pieces and internal lines"), icon='INFO')
        layout.label(text="click: select (Shift adds)  drag: move  G move  R rotate  S scale")
        layout.label(text="right-click: menu   corners and points: Edit Pieces")


# Whole Pieces' keys, for the Pattern panel's Keys (ui.draw_keys)
TRANSFORM_KEYS = (
    ("click", "select a piece or an internal line (Shift adds)"),
    ("drag", "move it, with everything picked"),
    ("drag", "from nothing: box select (pieces, and lines on the others)"),
    ("G", "move what is picked (X/Y: axis, type a length)"),
    ("R", "rotate what is picked about its middle (type degrees, Shift: 15 deg)"),
    ("S", "scale what is picked about its middle (X/Y: one direction, type a factor)"),
    ("Shift+D", "copy the selected pieces"),
    ("F2", "name the piece"),
    ("X / Del", "delete what is picked"),
)


class AC9_WT_EditPattern(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = TOOL_ID
    bl_label = "Edit Pieces"
    bl_description = ("Drag the corners, edges and curve handles of the flat pattern and internal lines (an end, a "
                      "point or the whole line); drag inside a piece: box select corners and lines. G / R / S: move, "
                      "rotate, scale what is picked. K: split an edge, X: remove a corner, an internal line or a "
                      "notch, C: curve / straighten an edge, N: put a notch, double-click: select the whole piece. "
                      "Right-click: everything for what is under the cursor (darts, slash and spread, cut, parallel "
                      "line). Whole pieces: Whole Pieces")
    bl_icon = "ops.gpencil.edit_bend"
    bl_widget = None
    bl_keymap = (
        ("ac9_cloth_sim.pattern_hover", {"type": 'MOUSEMOVE', "value": 'ANY'}, None),
        ("ac9_cloth_sim.pattern_drag", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.pattern_drag", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, None),
        ("ac9_cloth_sim.pattern_drag", {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True}, None),
        ("ac9_cloth_sim.pick_piece_under", {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK'}, None),
        ("ac9_cloth_sim.pick_piece_under", {"type": 'LEFTMOUSE', "value": 'DOUBLE_CLICK', "shift": True}, None),
        ("ac9_cloth_sim.pattern_edit", {"type": 'K', "value": 'PRESS'}, {"properties": [("action", 'SPLIT')]}),
        ("ac9_cloth_sim.pattern_edit", {"type": 'X', "value": 'PRESS'}, {"properties": [("action", 'REMOVE')]}),
        ("ac9_cloth_sim.pattern_edit", {"type": 'DEL', "value": 'PRESS'}, {"properties": [("action", 'REMOVE')]}),
        # nothing under the cursor (pattern_edit passes it on): the picked line, seam, measurement or pieces
        ("ac9_cloth_sim.delete_selected", {"type": 'X', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.delete_selected", {"type": 'DEL', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.pattern_edit", {"type": 'C', "value": 'PRESS'}, {"properties": [("action", 'CURVE')]}),
        ("ac9_cloth_sim.pattern_edit", {"type": 'N', "value": 'PRESS'}, {"properties": [("action", 'NOTCH')]}),
        # S scales only; spread, darts, cut and parallel lines are right-click items (keys for the few verbs used
        # many times on one pattern, every one also in the menu; user 2026-11-11)
        ("ac9_cloth_sim.pattern_transform", {"type": 'S', "value": 'PRESS'}, {"properties": [("mode", 'SCALE')]}),
        ("ac9_cloth_sim.pattern_transform", {"type": 'R', "value": 'PRESS'}, {"properties": [("mode", 'ROTATE')]}),
        ("ac9_cloth_sim.pattern_transform", {"type": 'G', "value": 'PRESS'}, {"properties": [("mode", 'MOVE')]}),
        ("ac9_cloth_sim.duplicate_pieces", {"type": 'D', "value": 'PRESS', "shift": True},
         {"properties": [("follow", True)]}),
        ("ac9_cloth_sim.piece_label", {"type": 'F2', "value": 'PRESS'}, None),
        ("ac9_cloth_sim.context_menu", {"type": 'RIGHTMOUSE', "value": 'PRESS'},
         {"properties": [("tool", 'EDIT_PATTERN')]}),
    )

    def draw_settings(context, layout, tool):
        # only the keys for what is under the cursor; all of them in the Pattern panel's Keys (user 2026-10-23, Fable
        # review REVIEW_fable_tool_hints_2026-10-23: the 14 items ran past the header's one row)
        if context.scene.ac9_cloth_sim.sync_pending:    # 3D Follows Edits off and edits waiting (AC9 Run)
            layout.label(text=tx("3D not updated"), icon='ERROR')
        layout.label(text=tx("Works on: ") + tx("pattern"), icon='INFO')
        layout.label(text="click: select (Shift adds)  double-click: whole piece  drag: move")
        h = _state.get('hover')
        layout.label(text=HOVER_KEYS.get(h[0] if h else None, ""))
        layout.label(text="right-click: menu   all keys: sidebar Pattern > Keys")


# the header's keys by what is under the cursor (pick's kinds); None: nothing under it
HOVER_KEYS = {
    'edge': "K add point  C curve  N notch",
    'corner': "drag: move  X delete",
    'notch': "drag: slide  X delete",
    'iend': "drag: move the end  X delete line",
    'iline': "K point  C curve  X delete",
    'inode': "drag: move the point  X delete point",
    'ihandle': "drag: bend the curve  C straighten",
    'handle': "drag: bend the curve",
    'piece': "Shift+D copy  F2 name",
    None: "drag: box select corners and lines   G / R / S: what is picked",
}

# every key of Edit Pieces, for the Pattern panel's Keys (ui.draw_keys) and the tool's tooltip
ALL_KEYS = (
    ("click", "select a piece, a corner, an edge or a line (Shift adds)"),
    ("drag", "move (X/Y: axis, type a length, Ctrl: no snap)"),
    ("drag", "a picked corner, edge or line: all picked move"),
    ("drag", "from nothing or inside a piece: box select corners and lines"),
    ("G / R / S", "move, rotate, scale what is picked about its middle"),
    ("double-click", "select the whole piece (a narrow one, lines on it)"),
    ("right-click", "the menu of what is under the cursor"),
    ("K", "add a point on the edge or line"),
    ("C", "curve / straighten the edge or line"),
    ("N", "put a notch on the edge"),
    ("Shift+D", "copy the selected pieces (or the one under the cursor)"),
    ("X / Del", "delete the corner, notch or line"),
    ("F9", "adjust the last action's numbers (dart, spread, length)"),
)


class AC9_OT_SimEditPatternTool(bpy.types.Operator):
    """Make Edit Pieces the active tool: drag corners, edges and curve handles of the flat pattern"""
    bl_idname = "ac9_cloth_sim.edit_pattern"
    bl_label = "Edit Pieces"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT'

    def execute(self, context):
        return bpy.ops.ac9_cloth_sim.use_tool(tool=TOOL_ID)


# ---------------------------------------------------------------- drawing a piece

class _DrawPoints:
    """Draw Piece's clicks, drawing and keys, shared with Draw Line. A plain mixin: Draw Line once subclassed the
    registered Draw Piece operator, and Blender then lost Draw Piece's Python class ("unable to get Python class for
    RNA struct", the button grayed out)."""
    bl_idname = "ac9_cloth_sim.draw_piece"
    bl_label = "Draw Piece"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and store.get(context)[0] is not None

    def _draw(self, context):
        try:
            if bpy.context.area is None or bpy.context.area.as_pointer() != self._draw_area \
                    or not VV.allows_pattern(bpy.context):
                return
            region = context.region
            P = list(self.pts) + ([self.cur] if self.cur is not None else [])
            if not P:
                return
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            sh.bind()
            sh.uniform_float('viewportSize', (region.width, region.height))
            sh.uniform_float('lineWidth', 2.5)
            sh.uniform_float('color', YELLOW)
            W = to_world(self.M, P)
            if len(W) > 1:
                batch_for_shader(sh, 'LINES', {'pos': [tuple(p) for u, v in zip(W[:-1], W[1:]) for p in (u, v)]}).draw(sh)
            sp = gpu.shader.from_builtin('UNIFORM_COLOR')
            sp.bind()
            gpu.state.point_size_set(9.0)
            sp.uniform_float('color', (1, 1, 1, 1))
            batch_for_shader(sp, 'POINTS', {'pos': [tuple(w) for w in W]}).draw(sp)
            gpu.state.point_size_set(1.0)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    SNAP_KINDS = ('corner', 'notch', 'mid', 'edge')     # a new piece lines up with the others' corners and sides

    def invoke(self, context, event):
        from . import shape_tool as ST
        if not VV.allows_pattern(context):
            return {'CANCELLED'}
        PV.layout_empty(context, create=True)
        self.M = PV.layout_matrix(context)
        self.pts, self.cur, self.typed = [], None, ''
        self.snapper = ST.Snapper(context, self.SNAP_KINDS)
        self.snap_piece = None
        self._draw_area = context.area.as_pointer()
        self._h = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        self._status(context)
        return TB.start_modal(self, context, event)

    def _status(self, context):
        txt = tx("Draw Piece: {0} corner(s)", len(self.pts))
        if self.pts and self.cur is not None:
            from .shape_tool import fmt
            txt += tx("   side {0}", fmt(context, np.linalg.norm(np.asarray(self.cur) - self.pts[-1])))
        if self.typed:
            txt += f"  [{self.typed}]"
        txt += tx("   click: corner   click the first corner / Enter: close   Shift: 15 degree steps"
                  "   Ctrl: no snap   type a length: side   Backspace: undo corner   Esc / right click: cancel")
        context.area.header_text_set(txt)

    def _target(self, context, event):
        from . import shape_tool as ST
        from .core import snap as SN
        q = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        if q is None:
            return None
        mmpx = px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        if event.shift and self.pts:      # the angle holds; it snaps only where the locked ray meets a line
            q, kind = self.snapper.on_ray(context, self.pts[-1], SN.lock_angle(self.pts[-1], q), mmpx,
                                          ctrl=event.ctrl, piece=getattr(self, 'on', None))
        else:
            q, kind, self.snap_piece = self.snapper.point(context, q, mmpx, ctrl=event.ctrl,
                                                          piece=getattr(self, 'on', None))
        q0 = q
        if self.pts and self.typed:
            v = U.typed_mm(context, self.typed)          # in the scene's unit
            if v is not None:
                d = q - self.pts[-1]
                n = np.linalg.norm(d)
                q = self.pts[-1] + (d / n * v if n > 1e-9 else np.array([v, 0.0]))
        if q is not q0:
            self.snapper.measure(q)
        ST.show_mark(context, self.snapper, self.M)
        return q

    def _end(self, context, close):
        from . import shape_tool as ST
        bpy.types.SpaceView3D.draw_handler_remove(self._h, 'WINDOW')
        ST.clear_mark()
        context.area.header_text_set(None)
        context.area.tag_redraw()
        if not close or len(self.pts) < 3:
            return {'CANCELLED'}
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        nm = D.add_polygon(spec, self.pts, "Piece")
        bad = D.problems(spec, [nm])
        if bad:
            self.report({'WARNING'}, bad[0])
            return {'CANCELLED'}
        commit(context, spec, [nm])
        self.report({'INFO'}, rpt("Added piece {0}", nm))
        return {'FINISHED'}

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE' or event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self.cur = self._target(context, event)
        elif event.value == 'PRESS':
            if event.type in {'ESC', 'RIGHTMOUSE'}:
                return self._end(context, False)
            if event.type in {'RET', 'NUMPAD_ENTER'}:
                return self._end(context, True)
            if event.type == 'BACK_SPACE':
                if self.typed:
                    self.typed = self.typed[:-1]
                elif self.pts:
                    self.pts.pop()
            elif event.type == 'LEFTMOUSE':
                q = self._target(context, event)
                if q is not None:
                    mmpx = px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
                    if len(self.pts) >= 3 and np.linalg.norm(q - self.pts[0]) <= CORNER_PX * mmpx:
                        return self._end(context, True)
                    self.pts.append(q)
                    self.typed = ''
            elif event.unicode and event.unicode in '0123456789.-':
                self.typed += event.unicode
            self.cur = self._target(context, event)
        self._status(context)
        return {'RUNNING_MODAL'}


class AC9_OT_SimDrawPiece(_DrawPoints, bpy.types.Operator):
    """Draw a new pattern piece: click its corners, then click the first corner (or Enter) to close it"""
    bl_idname = "ac9_cloth_sim.draw_piece"
    bl_label = "Draw Piece"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}


class AC9_OT_SimDrawLine(_DrawPoints, bpy.types.Operator):
    """Draw an internal line on a pattern piece (a dart leg, a pocket mark, a fold line): click its points on the piece, Enter to finish"""
    bl_idname = "ac9_cloth_sim.draw_line"
    bl_label = "Internal Line"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    def _status(self, context):
        txt = tx("Internal Line{0}: {1} point(s)", ' on ' + self.on if getattr(self, 'on', None) else '', len(self.pts))
        if self.pts and self.cur is not None:
            from .shape_tool import fmt
            txt += tx("   length {0}", fmt(context, np.linalg.norm(np.asarray(self.cur) - self.pts[-1])))
        if self.typed:
            txt += f"  [{self.typed}]"
        txt += tx("   click on a piece: point (snaps to the outline: a cut line for Slash & Spread)   Enter: finish"
                  "   click the first point: close (a shape)   Shift: 15 degree steps   Ctrl: no snap   type a length   Backspace: undo point"
                  "   Esc / right click: cancel")
        context.area.header_text_set(txt)

    SNAP_KINDS = ('corner', 'notch', 'iend', 'mid', 'edge', 'iline')

    def invoke(self, context, event):
        self.on = None
        self.shut = False
        return super().invoke(context, event)

    def _at_first(self, context, event, q):
        """Close on the first point: 3 points or more and the click (or where it snapped) within a corner's reach of
        it, as Draw Piece closes (user 2026-10-28, Fable docs/reviews/REVIEW_fable_cutout_ui_2026-10-28)."""
        if len(self.pts) < 3:
            return False
        mmpx = px_to_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        raw = mouse_mm(context, self.M, event.mouse_region_x, event.mouse_region_y)
        return any(p is not None and np.linalg.norm(np.asarray(p) - self.pts[0]) <= CORNER_PX * mmpx for p in (q, raw))

    def _end(self, context, close):
        from . import shape_tool as ST
        bpy.types.SpaceView3D.draw_handler_remove(self._h, 'WINDOW')
        ST.clear_mark()
        context.area.header_text_set(None)
        context.area.tag_redraw()
        if not close or len(self.pts) < 2 or self.on is None:
            return {'CANCELLED'}
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        spec = S.loads(S.dumps(spec))
        if self.shut:                           # a closed shape, as the Rectangle tool draws it
            lid = D.add_internal(spec, self.on, self.pts, kind='shape', closed=True)
        else:
            lid = D.add_internal(spec, self.on, self.pts)
        bad = D.internal_problems(spec, self.on, [lid])
        if bad:
            self.report({'WARNING'}, bad[0])
            return {'CANCELLED'}
        msg = commit(context, spec, [self.on], old_spec=old)
        notes = D.internal_mesh_notes(spec, self.on, context.scene.ac9_cloth_sim.mesh_size, [lid])
        self.report({'WARNING'} if notes else {'INFO'}, rpt("Added line {0} on {1}", lid, self.on)
                    + (rpt("; {0}", notes[0]) if notes else "") + (rpt("; {0}", msg) if msg else ""))
        return {'FINISHED'}

    def modal(self, context, event):
        if event.value == 'PRESS' and event.type == 'LEFTMOUSE' and self.on is None:
            q = self._target(context, event)
            spec, outlines = store.get(context)
            visible = VV.pattern_names(context, spec)
            hit = self.snap_piece if q is not None else None      # snapped onto a piece's outline or line
            hit = hit or (None if q is None else next((pc['name'] for pc in reversed(spec['pieces'])
                                                       if pc['name'] in visible and pc.get('enabled', True)
                                                       and _inside(outlines[pc['name']].B, q)),
                                                      None))
            if hit is None:
                self.report({'WARNING'}, "Start the line on a piece")
                return {'RUNNING_MODAL'}
            self.on = hit
            self.pts.append(q)
            self.typed = ''
            self._status(context)
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type == 'LEFTMOUSE':
            q = self._target(context, event)
            if self._at_first(context, event, q):            # the first point again: a closed shape
                self.shut = True
                return self._end(context, True)
            if q is not None:
                self.pts.append(q)
                self.typed = ''
            self._status(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        r = super().modal(context, event)
        if not self.pts:
            self.on = None
        elif event.type == 'MOUSEMOVE' and self._at_first(context, event, self.cur):
            self.cur = np.asarray(self.pts[0], float)      # the preview closes onto the first point
        return r


class AC9_OT_SimMatchLength(bpy.types.Operator):
    """Make one side of the active seam as long as the other plus the ease: a corner at its end moves along the side, an end mid-edge slides (twins follow)"""
    bl_idname = "ac9_cloth_sim.match_length"
    bl_label = "Match Length"
    bl_options = {'REGISTER', 'UNDO'}

    side: bpy.props.EnumProperty(name="Change", items=[('a', "A", "Change side A to fit side B"),
                                                       ('b', "B", "Change side B to fit side A")])
    ease: bpy.props.FloatProperty(name="Ease-in", default=0.0, min=-0.2, max=0.2, precision=1, subtype='DISTANCE', unit='LENGTH', 
                                  description="How much longer the changed side ends up than the other: the "
                                              "fullness eased in when the two are sewn (0 = the same length)")
    end: bpy.props.EnumProperty(name="At", translation_context=CTX, items=[('to', "End", "Change the side at its end"),
                                                  ('from', "Start", "Change the side at its start")])

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and store.active_seam(context, spec) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        sm = store.active_seam(context, sp)
        try:
            ch, l0, l1, tg = D.match_side_length(sp, sm['id'], self.side, self.ease * 1000.0, self.end)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        bad = D.problems(sp, ch)
        if bad:
            self.report({'WARNING'}, bad[0])
            return {'CANCELLED'}
        msg = commit(context, sp, ch, old_spec=old)
        self.report({'INFO'}, rpt("{0} side {1}: {2} -> {3}", sm['id'], self.side.upper(), U.fmt(context, l0), U.fmt(context, l1)) + (rpt("; {0}", msg) if msg else "") + rpt(F9))
        return {'FINISHED'}


class AC9_OT_SimExportDXF(bpy.types.Operator):
    """Write the pattern as DXF-AAMA for a 2D pattern CAD (outline, corners, internal lines, piece names; each piece and corner carries its AC9 id so a piece that comes back can be matched again). Seams are not in DXF: they stay in AC9"""
    bl_idname = "ac9_cloth_sim.export_dxf"
    bl_label = "Export DXF"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.dxf", options={'HIDDEN'})
    only_on: bpy.props.BoolProperty(name="Pieces On Only", default=True,
                                    description="Write the pieces that are on (off: every piece)")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        if not self.filepath:
            spec, _ = store.get(context)
            self.filepath = bpy.path.ensure_ext(bpy.path.clean_name(spec.get('name') or "pattern"), ".dxf")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .core import dxf_out
        spec, _ = store.get(context)
        path = bpy.path.ensure_ext(bpy.path.abspath(self.filepath), ".dxf")
        names = None if self.only_on else [p['name'] for p in spec['pieces']]
        sm = dxf_out.write(spec, path, pieces=names)
        self.report({'INFO'}, rpt("Wrote {0} pieces, {1} corners, {2} internal lines to {3}", sm['pieces'], sm['turn_points'], sm['internal_lines'], path))
        return {'FINISHED'}


class AC9_OT_SimImportDXF(bpy.types.Operator):
    """Read a DXF-AAMA pattern from a 2D pattern CAD as a new garment (outlines, corners, internal lines, notches, piece names). DXF has no seams: sew the pieces with Edit Seams"""
    bl_idname = "ac9_cloth_sim.import_dxf"
    bl_label = "Import DXF"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.dxf", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .core import dxf_in
        from . import shape_tool as ST
        try:
            spec, rep = dxf_in.read(bpy.path.abspath(self.filepath))
        except Exception as exc:
            self.report({'ERROR'}, rpt("Could not read the DXF: {0}", exc))
            return {'CANCELLED'}
        ST.set_mm_if_default(context)
        store.new_text(context, spec['name'])
        store.put(context, spec)
        spec, outlines = store.get(context)
        PV.rebuild(context, spec, outlines)
        overlay.invalidate()
        for a in context.screen.areas if context.screen else ():
            a.tag_redraw()
        left = [f"{rep[k]} {t}" for k, t in (('grain_lines', "grain lines"), ('cutouts', "cut-outs"),
                                              ('skipped_sizes', "other sizes"), ('no_outline', "blocks without outline"))
                if rep[k]]
        self.report({'WARNING'} if left else {'INFO'},
                    rpt("{0} pieces, {1} internal lines, {2} notches; no seams", rep['pieces'], rep['internals'], rep['notches'])
                    + (rpt("; not read: {0}", ', '.join(left)) if left else ""))
        return {'FINISHED'}


class AC9_OT_SimImportSVG(bpy.types.Operator):
    """Read an SVG pattern from a drawing app or a pattern generator as a new garment (outlines, corners, internal lines, notches, piece names). Every closed line not inside another is a piece; a cut line round a sewing line is left out. SVG has no seams: sew the pieces with Edit Seams"""
    bl_idname = "ac9_cloth_sim.import_svg"
    bl_label = "Import SVG"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.svg", options={'HIDDEN'})
    units: bpy.props.EnumProperty(
        name="Units",
        description="What one unit is in a file whose size is in px or has no unit (a size in mm, cm, in or pt is read as written)",
        items=(('AUTO', "Auto", "Points for a file a drawing app that works in points wrote, else px (1/96 inch)"),
               ('PT', "Point", "1 unit = 1/72 inch"),
               ('PX', "Pixel", "1 unit = 1/96 inch"),
               ('MM', "Millimeter", "1 unit = 1 mm")),
        default='AUTO')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .core import svg_in
        from . import shape_tool as ST
        try:
            spec, rep = svg_in.read(bpy.path.abspath(self.filepath), units=self.units)
        except Exception as exc:
            self.report({'ERROR'}, rpt("Could not read the SVG: {0}", exc))
            return {'CANCELLED'}
        ST.set_mm_if_default(context)
        store.new_text(context, spec['name'])
        store.put(context, spec)
        spec, outlines = store.get(context)
        PV.rebuild(context, spec, outlines)
        overlay.invalidate()
        for a in context.screen.areas if context.screen else ():
            a.tag_redraw()
        left = [f"{rep[k]} {rpt(t)}" for k, t in (('grain_lines', "grain lines"), ('marks', "marks"),
                                              ('help', "construction lines"), ('outside', "lines outside the pieces"))
                if rep[k]]
        msg = (rpt("{0} pieces, {1} internal lines, {2} notches; no seams", rep['pieces'], rep['internals'], rep['notches'])
               + rpt("; 1 unit = {0:.4f} mm ({1})", rep['units_mm_per'], rep['units_from']))
        if rep['seam_allowance']:
            msg += rpt("; cut lines left out: {0}", rep['seam_allowance'])
        self.report({'WARNING'} if left else {'INFO'}, msg + (rpt("; not read: {0}", ', '.join(left)) if left else ""))
        return {'FINISHED'}


# ---------------------------------------------------------------- pieces and garments

class AC9_OT_SimNewPattern(bpy.types.Operator):
    """Start a new garment with no pieces (draw them with Draw Piece)"""
    bl_idname = "ac9_cloth_sim.new_pattern"
    bl_label = "New Garment"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", default="Garment")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from . import shape_tool as ST
        spec = S.new_spec(self.name or "Garment")
        ST.set_mm_if_default(context)
        store.new_text(context, spec['name'])
        store.put(context, spec)
        PV.layout_empty(context, create=True)
        overlay.invalidate()
        return {'FINISHED'}


def _selected_piece(context):
    ob = context.active_object
    return ob[PV.PIECE_PROP] if ob is not None and PV.PIECE_PROP in ob else None


def _drop_placed(spec, names):
    """The placed objects of deleted pieces taken away (a placed object stayed behind, and a piece made later under
    the same name -- Mirror Copy again -- took it over, upside down, user 2026-11-12): a piece's own object goes; the
    half a pair's Mirror modifier showed for it goes with the modifier."""
    from . import placed_view as PLV
    obs = PLV.objects(spec)
    own = []
    for nm in names:
        ob = obs.get(nm)
        if ob is None:
            continue
        if ob.get(PLV.PROP) == nm:
            own.append(nm)
        elif ob.get(PLV.TWIN_PROP) == nm:
            for md in [m for m in ob.modifiers if m.type == 'MIRROR' and m.name == "AC9 Pair"]:
                ob.modifiers.remove(md)
            del ob[PLV.TWIN_PROP]
            ob.pop(PLV.PLANE_PROP, None)
    if own:
        PLV.remove(own, spec)


class AC9_OT_SimDeletePiece(bpy.types.Operator):
    """Delete the selected pattern pieces, with their seams, placement and fold lines"""
    bl_idname = "ac9_cloth_sim.delete_piece"
    bl_label = "Delete Piece"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and bool(SEL.pattern_pieces(context))

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        names = SEL.pattern_pieces(context)
        gone = []
        for nm in names:
            gone += D.delete_piece(spec, nm)
        _drop_placed(spec, names)
        _, note = store.commit(context, spec)
        obs = PV.piece_objects()
        for nm in names:
            ob = obs.get(nm)
            if ob is not None:
                me = ob.data
                bpy.data.objects.remove(ob)
                if me is not None and me.users == 0:
                    bpy.data.meshes.remove(me)
        overlay.invalidate()
        from . import button_view, zipper_view, dress
        g = bpy.data.objects.get(dress.garment_name(spec))
        job = dress.job_of(g) if g is not None else None
        updates = (("buttons", button_view.sync, (context, spec, g, job)),
                   ("pattern buttons", button_view.sync_flat, (context, spec)),
                   ("zippers", zipper_view.sync, (context, spec, g, job)))
        for label, update, args in updates:
            try:
                update(*args)
            except Exception as exc:
                note = '; '.join(x for x in (note, tx("Pattern saved; {0} not updated: {1}", label, exc)) if x)
        message = rpt("Deleted {0} piece(s) and {1} seam(s)", len(names), len(gone))
        if note:
            context.scene.ac9_cloth_sim.dress_status = note
            message += rpt("; {0}", note)
        self.report({'WARNING'} if note else {'INFO'}, message)
        return {'FINISHED'}


class AC9_OT_SimDeleteInternal(bpy.types.Operator):
    """Delete the picked internal line, with the seams sewn along it and its fold"""
    bl_idname = "ac9_cloth_sim.delete_internal"
    bl_label = "Delete Internal Line"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return selected_internal(context, store.get(context)[0]) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        name, lid, _it = selected_internal(context, spec)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        gone = D.delete_internal(sp, name, lid)
        msg = commit(context, sp, [name], old_spec=old)
        select_internal(context)
        self.report({'INFO'}, rpt("Removed line {0}", lid) + (rpt(" and its seam(s) {0}", ', '.join(gone)) if gone else "")
                    + (rpt("; {0}", msg) if msg else ""))
        return {'FINISHED'}


class AC9_OT_SimCutOut(bpy.types.Operator):
    """Cut the closed internal shape out of the piece as a hole (no cloth inside it), or fill the hole again. Its mirror on the twin and across the center line follow"""
    bl_idname = "ac9_cloth_sim.cut_out"
    bl_label = "Convert to Hole"
    bl_options = {'REGISTER', 'UNDO'}

    piece: bpy.props.StringProperty(options={'SKIP_SAVE'})
    line: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def _target(self, context, spec):
        if self.piece and self.line:
            pc = next((x for x in spec['pieces'] if x['name'] == self.piece), None)
            it = next((x for x in (pc or {}).get('internals', []) if x['id'] == self.line), None)
            return (self.piece, self.line, it) if it is not None else None
        return selected_internal(context, spec)

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None

    def execute(self, context):
        spec, _ = store.get(context)
        t = self._target(context, spec)
        if t is None:
            self.report({'WARNING'}, "Pick a closed internal shape first")
            return {'CANCELLED'}
        name, lid, it = t
        on = not it.get('hole')
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            names = D.set_hole(sp, name, lid, on)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        msg = commit(context, sp, names, old_spec=old)
        self.report({'INFO'}, rpt("{0} on {1}: cut out", lid, name) if on else rpt("{0} on {1}: filled", lid, name))
        if msg:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_SimCloneShape(bpy.types.Operator):
    """Make a new piece from the closed internal shape (a pocket drawn where it goes): the same outline, the piece's fabric, no seams, just right of the piece. The shape stays on the piece"""
    bl_idname = "ac9_cloth_sim.clone_shape"
    bl_label = "Clone as Pattern"
    bl_options = {'REGISTER', 'UNDO'}

    piece: bpy.props.StringProperty(options={'SKIP_SAVE'})
    line: bpy.props.StringProperty(options={'SKIP_SAVE'})
    sew: bpy.props.BoolProperty(name="Sew", options={'SKIP_SAVE'},
                                description="Sew the new piece's edges to the shape (a pocket sewn on where it was drawn)")

    _target = AC9_OT_SimCutOut._target

    @classmethod
    def description(cls, context, props):
        if props.sew:
            return tx("Make a new piece from the closed internal shape and sew its edges to the shape (a pocket sewn "
                      "on where it was drawn). Where the piece is placed, the new piece goes onto it (Part to Seam)")
        return tx(cls.__doc__)

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        t = self._target(context, spec)
        if t is None:
            self.report({'WARNING'}, "Pick a closed internal shape first")
            return {'CANCELLED'}
        name, lid, _ = t
        sp = S.loads(S.dumps(spec))
        try:
            nm = D.clone_shape(sp, name, lid, sew=self.sew)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        bad = D.problems(sp, [nm])
        if bad:
            self.report({'WARNING'}, rpt("Not applied: ") + bad[0])
            return {'CANCELLED'}
        commit(context, sp, [nm])
        SEL.deselect_all(context)
        TB.select_piece(context, nm)
        frame_pieces(context, [name, nm])
        self.report({'INFO'}, rpt("Added piece {0} from {1} on {2}", nm, lid, name))
        # sewn on: onto its piece in 3D when that is placed or worn (Part to Seam, user 2026-10-28); else Place it
        if self.sew and bpy.ops.ac9_cloth_sim.move_to_seam.poll():
            try:
                bpy.ops.ac9_cloth_sim.move_to_seam('INVOKE_DEFAULT')
            except RuntimeError as e:
                self.report({'WARNING'}, str(e))
        return {'FINISHED'}


def frame_pieces(context, names):
    """The pattern view framed on the pieces `names` (their objects), the selection left as it was: a new piece is
    seen where it went (user 2026-10-28). No pattern view or no window (background): nothing."""
    obs = [o for n, o in PV.piece_objects().items() if n in names]
    win = context.window
    if not obs or win is None:
        return
    area = next((a for a in win.screen.areas if a.type == 'VIEW_3D' and a.spaces.active.region_3d is not None
                 and a.spaces.active.region_3d.lock_rotation), None)
    if area is None:
        return
    vl = context.view_layer
    sel, act = SEL.objects(context), vl.objects.active
    for o in vl.objects:
        o.select_set(o in obs)
    try:
        reg = next(r for r in area.regions if r.type == 'WINDOW')
        with context.temp_override(window=win, area=area, region=reg):
            bpy.ops.view3d.view_selected()
    except (RuntimeError, TypeError, StopIteration):
        pass
    finally:
        for o in vl.objects:
            o.select_set(o in sel)
        vl.objects.active = act


class AC9_OT_SimPickSeam(bpy.types.Operator):
    """Pick this seam (the Tool tab shows it)"""
    bl_idname = "ac9_cloth_sim.pick_seam"
    bl_label = "Pick Seam"
    bl_options = {'INTERNAL'}

    seam: bpy.props.StringProperty()

    def execute(self, context):
        from . import seam_edit_tool as SET
        return {'FINISHED'} if SET.select_seam(context, self.seam) else {'CANCELLED'}


def piece_under(spec, outlines, q):
    """The piece whose outline holds layout point q (the top one drawn; an off piece last), or None."""
    for on in (True, False):
        for pc in reversed(spec['pieces']):
            if pc.get('enabled', True) == on and _inside(outlines[pc['name']].B, q):
                return pc['name']
    return None


class AC9_OT_SimPickPieceUnder(bpy.types.Operator):
    """Double-click: select the whole piece under the cursor, whatever lies on it (internal lines, notches, seams).
    Shift: add it or take it out"""
    bl_idname = "ac9_cloth_sim.pick_piece_under"
    bl_label = "Select Piece"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and store.get(context)[0] is not None and PV.layout_empty(context) is not None)

    @CL.logged
    def invoke(self, context, event):
        # user 2026-10-24: a placket (its two edges, 200 buttonhole lines) could be picked only zoomed far in
        spec, outlines = store.get(context)
        if TB.under(context, event.mouse_region_x, event.mouse_region_y)[0] not in ('PATTERN', 'PLANE'):
            return {'PASS_THROUGH'}
        q = mouse_mm(context, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
        name = None if q is None else piece_under(spec, outlines, q)
        if name is None:
            return {'PASS_THROUGH'}
        select_internal(context)
        if not event.shift:
            select_edge(context)
        TB.select_piece(context, name, extend=event.shift)
        context.area.tag_redraw()
        return {'FINISHED'}


def _delete_keys_poll(context):
    return context.area is not None and context.area.type == 'VIEW_3D'


class AC9_OT_SimDeleteSelected(bpy.types.Operator):
    """Delete what is under the cursor (a corner, a notch, an internal line's end), else what is picked: an internal line, a seam, a measurement, or the selected pieces (asks first)"""
    bl_idname = "ac9_cloth_sim.delete_selected"
    bl_label = "Delete"
    bl_options = {'INTERNAL'}       # each delete below is its own undo step

    @classmethod
    def poll(cls, context):
        return _delete_keys_poll(context)

    def invoke(self, context, event):
        from . import tool_tab
        spec, outlines = store.get(context)
        if spec is not None and AC9_OT_SimPatternDrag.poll(context):
            h = pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
            if h and h[0] in ('corner', 'notch', 'iend'):
                return bpy.ops.ac9_cloth_sim.pattern_edit('INVOKE_DEFAULT', action='REMOVE')
        kind = tool_tab.resolve(context, spec) if spec is not None else None
        if kind is None:
            from . import measure_edit as MEE
            kind = 'MEASURE' if MEE.selected(context) is not None else None
        if kind == 'MEASURE':
            return bpy.ops.ac9_cloth_sim.measure_delete()
        if kind == 'INTERNAL':
            return bpy.ops.ac9_cloth_sim.delete_internal()
        if kind == 'SEAM':
            return bpy.ops.ac9_cloth_sim.seam_edit(action='DELETE')
        if kind == 'PIECE':
            return bpy.ops.ac9_cloth_sim.delete_piece('INVOKE_DEFAULT')
        self.report({'INFO'}, "Nothing picked to delete")
        return {'CANCELLED'}           # an AC9 tool's X never deletes the objects (Blender's own X does)


def _fold_get(self):
    s = selected_internal(bpy.context, store.get(bpy.context)[0])
    if s is None:
        return 0.0
    spec = store.get(bpy.context)[0]
    return float(next((f['deg'] for f in spec.get('folds', []) if f.get('piece') == s[0] and f.get('line') == s[1]),
                      0.0))


def _fold_set(self, v):
    context = bpy.context
    spec, _ = store.get(context)
    s = selected_internal(context, spec)
    if s is None:
        return
    if abs(v - _fold_get(self)) < 1e-6:
        # the same angle typed again: written on the twin's line if it has another (a file from before the twins
        # followed), and the placed shape folded at it -- a file opened with the angle set and its pieces placed since
        # showed them flat, retyping did nothing (user 2026-10-25)
        from . import place_fold as PF
        PF.sync_twin(context, s[0], s[1])
        if abs(v) > 1e-6:
            PF.refold_pair(context, s[0], s[1], float(v))
            overlay.invalidate()
        return
    old = next((f for f in spec.get('folds', []) if f.get('piece') == s[0] and f.get('line') == s[1]), {})
    folds = [f for f in spec.get('folds', []) if f is not old]
    if abs(v) > 1e-6:
        folds.append(dict(old, piece=s[0], line=s[1], deg=round(float(v), 2)))      # its side kept
        if not old:
            folds[-1]['side'] = 'NONE'       # new folds do not turn a whole side; legacy folds keep their default
    # only the folds change: a new spec dict sharing the rest (nothing below changes it), kept by the store
    store.put(context, dict(spec, folds=folds), keep=True, seams=False)
    # folded on the placed shape already: folded again now, the same side (user 2026-10-23); the twin's line the same
    # (user 2026-10-25)
    from . import place_fold as PF
    PF.sync_twin(context, s[0], s[1])
    PF.refold_pair(context, s[0], s[1], float(v))
    overlay.invalidate()


# the Fold shown as an angle, which way and the side that moves (user 2026-10-24): the spec keeps the signed deg
def _fangle_get(self):
    return abs(_fold_get(self))


_taper_ui_cache = (None,None,())


def _taper_ends(spec, selected):
    global _taper_ui_cache
    if selected is None:return ()
    key=selected[:2]
    if _taper_ui_cache[0] is not spec or _taper_ui_cache[1]!=key:
        from .core.fold_reference import terminal_ends
        piece=next(p for p in spec['pieces'] if p['name']==selected[0])
        _taper_ui_cache=(spec,key,tuple(terminal_ends(piece,selected[2])))
    return _taper_ui_cache[2]


def _ftaper_get(self):
    spec=store.get(bpy.context)[0];selected=selected_internal(bpy.context,spec)
    if selected is None:return 0.
    return float(next((f.get('end_taper_mm',0.) for f in spec.get('folds',[]) if f.get('piece')==selected[0] and f.get('line')==selected[1]),0.))/1000.  # a length field: m


def _ftaper_set(self,value):
    context=bpy.context;spec=store.get(context)[0];selected=selected_internal(context,spec)
    if not _taper_ends(spec,selected) or abs(_fold_get(self))<1e-6:return
    folds=[dict(f) for f in spec.get('folds',[])]
    fold=next(f for f in folds if f.get('piece')==selected[0] and f.get('line')==selected[1])
    if value:fold['end_taper_mm']=round(float(value)*1000.,2)   # the field's m to mm
    else:fold.pop('end_taper_mm',None)
    store.put(context,dict(spec,folds=folds),keep=True,seams=False)
    from . import place_fold as PF
    PF.sync_twin(context,selected[0],selected[1]);overlay.invalidate()


def _fangle_set(self, v):
    d = _fold_get(self)
    _fold_set(self, -abs(v) if d < 0 else abs(v))          # In from flat


def _fdir_get(self):
    return 1 if _fold_get(self) < 0 else 0


def _fdir_set(self, v):
    _fold_set(self, -abs(_fold_get(self)) if v == 1 else abs(_fold_get(self)))


def _fside_get(self):
    spec = store.get(bpy.context)[0]
    s = selected_internal(bpy.context, spec)
    from . import place_fold as PF
    ab = PF.spec_side(spec, s[0], s[1]) if s is not None else 'NONE'
    return {'A': 0, 'B': 1, 'NONE': 2}.get(ab, 0)  # missing side on an old fold still means A


def _fside_set(self, v):
    context = bpy.context
    spec, _ = store.get(context)
    s = selected_internal(context, spec)
    if s is None or v == _fside_get(self) or abs(_fold_get(self)) < 1e-6:
        return
    from . import place_fold as PF
    PF.set_turn_side(context, s[0], s[1], _fold_get(self), ('A', 'B', 'NONE')[v])
    overlay.invalidate()


# one layer (user 2026-10-24, DECISIONS cloth-sim-fold-one-layer-2026-10-24): the spec fold's "layers" = the side
# kept ("A" / "B"; absent = both simulated). The notes are worked out when it is set, not while drawing
_layers_notes = {}      # (piece, line) -> (spec key, the notes of dress.one_layer_rows, "one layer: piece line: " cut off)
_notes_pending = set()


def layers_notes(context, spec, piece, line):
    """dress.one_layer_rows' notes for this one line, without the "one layer: piece line: " in front. On the garment's
    mesh as Simulate makes it, this piece alone triangulated (fold_sides.piece_mesh): the start shapes (prepare) took
    3.4 s a press on a shirt, made again for every change of a fold of 90 degrees or more."""
    from . import dress as D
    from . import fold_sides as FS
    js = FS.job_spec(context, spec)
    pm = FS.piece_mesh(context, js, piece)
    if pm is None:
        return []
    pat, names, i, T = pm
    tris = [np.zeros((0, 3), np.int64)] * len(names)
    tris[i] = T
    one = dict(js, folds=[f for f in js.get('folds', []) if f.get('piece') == piece and f.get('line') == line])
    notes = []
    D.one_layer_rows(one, names, pat, tris, [0] * len(names), notes)
    head = f"{piece} {line}: "
    return [n_.split(head, 1)[-1] if head in n_ else n_.split(': ', 1)[-1] for n_ in notes]


def _notes_run(piece, line):
    _notes_pending.discard((piece, line))
    context = bpy.context
    try:
        spec = store.get(context)[0]
        _layers_notes[(piece, line)] = (store._cache['key'], layers_notes(context, spec, piece, line))
    except Exception:
        _layers_notes[(piece, line)] = (store._cache['key'], [])
    for area in context.screen.areas if context.screen else ():
        area.tag_redraw()
    return None


def notes_for(piece, line):
    """The Layers notes as last worked out; worked out again (a timer, not while drawing) when the spec changed since:
    an angle changed, or the file opened again (they were kept in memory only, user 2026-10-24)."""
    hit = _layers_notes.get((piece, line))
    if (hit is None or hit[0] != store._cache['key']) and (piece, line) not in _notes_pending:
        _notes_pending.add((piece, line))
        bpy.app.timers.register(lambda: _notes_run(piece, line), first_interval=0.0)
    return hit[1] if hit is not None else ()


def _flayers_get(self):
    spec = store.get(bpy.context)[0]
    s = selected_internal(bpy.context, spec)
    if s is None:
        return 0
    f = next((f for f in spec.get('folds', []) if f.get('piece') == s[0] and f.get('line') == s[1]), {})
    return {'A': 1, 'B': 2}.get(f.get('layers'), 0)


def _flayers_set(self, v):
    context = bpy.context
    spec, _ = store.get(context)
    s = selected_internal(context, spec)
    if s is None or v == _flayers_get(self) or abs(_fold_get(self)) < 1e-6:
        return
    spec = S.loads(S.dumps(spec))
    for f in spec.get('folds', []):
        if f.get('piece') == s[0] and f.get('line') == s[1]:
            if v:
                f['layers'] = 'AB'[v - 1]
            else:
                f.pop('layers', None)
    store.put(context, spec)
    from . import place_fold as PF
    PF.sync_twin(context, s[0], s[1])
    spec = store.get(context)[0]
    _layers_notes[(s[0], s[1])] = (store._cache['key'], layers_notes(context, spec, s[0], s[1]) if v else [])
    overlay.invalidate()


def _el_get(self):
    spec = store.get(bpy.context)[0]
    s = selected_internal(bpy.context, spec)
    if s is None:
        return 100.0
    e = next((e for e in spec.get('elastic', []) if e.get('piece') == s[0] and e.get('line') == s[1]), None)
    return 100.0 if e is None else round(float(e['ratio']) * 100.0, 2)


def _el_set(self, v):
    context = bpy.context
    spec, _ = store.get(context)
    s = selected_internal(context, spec)
    if s is None or abs(v - _el_get(self)) < 1e-6:
        return
    spec = S.loads(S.dumps(spec))
    el = [e for e in spec.get('elastic', []) if not (e.get('piece') == s[0] and e.get('line') == s[1])]
    if v < 100.0 - 1e-6:
        el.append({'piece': s[0], 'line': s[1], 'ratio': round(float(v) / 100.0, 4)})   # 10 mm each side
    if el:
        spec['elastic'] = el
    else:
        spec.pop('elastic', None)
    store.put(context, spec)
    overlay.invalidate()


def draw_gaps(layout, context, spec, g):
    """The gaps of the picked thing g (an internal line, or one outline edge), shown with their arrows, and one button
    that moves it by a number: X / Y or a gap's distance (user 2026-11-11, Fable review: one dialog for the arrow
    buttons, as "how far from where it is now")."""
    pr = gap_probe(spec, g)
    gl = SN.gaps(pr[0], SN.gauge(*pr)) if pr is not None else []
    from .shape_tool import fmt
    col = layout.column(align=True)
    if gl:
        row = col.grid_flow(columns=2, align=True)
        for a, b, mm, i in gl[:GAP_MAX]:
            row.label(text=f"{arrow(pr[0].rays[i][1])} {fmt(context, mm)}")
    # INVOKE: the dialog asks for the numbers first. An EXEC row drawn before it in the same panel (Cut) reaches this
    # button too (user 2026-11-11)
    col.operator_context = 'INVOKE_DEFAULT'
    op = col.operator("ac9_cloth_sim.set_gap", text=tx("Move by Number..."), icon='DRIVER_DISTANCE')
    op.piece, op.kind, op.thing = g[1], g[0].upper(), g[2]


GAP_MAX = 8          # the gaps the dialog lists (one each way of the probe)


class AC9_OT_SimSetGap(bpy.types.Operator):
    """Move the picked internal line or edge by a number: how far along X and Y from where it is, or the distance a
    gap should be (along that gap's arrow)"""
    bl_idname = "ac9_cloth_sim.set_gap"
    bl_label = "Move by Number"
    bl_options = {'REGISTER', 'UNDO'}

    piece: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    kind: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})        # 'ILINE' / 'EDGE'
    thing: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})       # the line id / the edge id
    move_x: bpy.props.FloatProperty(name="X", precision=1, subtype='DISTANCE', unit='LENGTH',      # m
                                    description="How far to move it to the right (left: below 0)")
    move_y: bpy.props.FloatProperty(name="Y", precision=1, subtype='DISTANCE', unit='LENGTH',
                                    description="How far to move it up (down: below 0)")
    gaps: bpy.props.FloatVectorProperty(size=GAP_MAX, min=0.0, precision=1, subtype='NONE', unit='LENGTH')    # m
    gaps0: bpy.props.FloatVectorProperty(size=GAP_MAX, options={'HIDDEN'})
    rays: bpy.props.IntVectorProperty(size=GAP_MAX, default=[-1] * GAP_MAX, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def _g(self):
        return (self.kind.lower(), self.piece, self.thing)

    def _hit(self, spec, ray):
        """(the gap along ray now in mm, its direction) or (None, None)."""
        pr = gap_probe(spec, self._g())
        if pr is None or ray < 0 or ray >= len(pr[0].rays) or pr[0].rays[ray] is None:
            return None, None
        r = pr[0].rays[ray]
        h = SN.ray_hits(pr[1], pr[2], np.asarray(r[0], float), r[1])
        return (float(h[0]) if len(h) else None), np.asarray(r[1], float)

    def invoke(self, context, event):
        spec, _ = store.get(context)
        pr = gap_probe(spec, self._g())
        if pr is None:
            return {'CANCELLED'}
        gl = SN.gaps(pr[0], SN.gauge(*pr))[:GAP_MAX]
        rays, gaps = [-1] * GAP_MAX, [0.0] * GAP_MAX
        for k, (a, b, mm, i) in enumerate(gl):
            rays[k], gaps[k] = i, mm / 1000.0
        self.rays, self.gaps, self.gaps0 = rays, gaps, gaps
        self.move_x = self.move_y = 0.0
        dialog_aside(context, event, [self.piece], spec)     # beside the piece: the dialog does not cover the ghost
        ghost_open(self)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        spec, _ = store.get(context)
        layout = self.layout
        col = layout.column(align=True)
        col.label(text=tx("Move from where it is"))
        col.prop(self, "move_x")
        col.prop(self, "move_y")
        pr = gap_probe(spec, self._g()) if spec is not None else None
        shown = [k for k in range(GAP_MAX) if self.rays[k] >= 0]
        if pr is not None and shown:
            col = layout.column(align=True)
            col.label(text=tx("Or the distance to the outline / lines"))
            for k in shown:
                r = pr[0].rays[self.rays[k]] if self.rays[k] < len(pr[0].rays) else None
                if r is not None:
                    col.prop(self, "gaps", index=k, text=arrow(r[1]))
        if spec is not None:            # where it would go, drawn on the pattern while the values change
            draw_why(layout, ghost(self, ("set_gap", self.kind, self.piece, self.thing, self.move_x, self.move_y,
                                    tuple(self.gaps)), lambda: self._ghost(spec)))

    def _delta(self, spec):
        """How far the values move it (mm), or a ValueError saying why not."""
        d = np.array([self.move_x, self.move_y]) * 1000.0
        for k in range(GAP_MAX):
            if self.rays[k] < 0 or abs(self.gaps[k] - self.gaps0[k]) < 1e-7:
                continue
            h, u = self._hit(spec, self.rays[k])
            if h is None:
                raise ValueError(rpt("Nothing that way"))
            d = d + u * (h - self.gaps[k] * 1000.0)
        return d

    def _apply(self, spec, d, light=False):
        """(the moved copy, pieces changed); a ValueError when it may not go there."""
        g = self._g()
        sp = light_copy(spec, [g[1]]) if light else S.loads(S.dumps(spec))
        if g[0] == 'iline':
            ch = D.move_internal(sp, g[1], g[2], 'all', tuple(d))
        else:
            ch = D.move_edge(sp, g[1], g[2], tuple(d))
        bad = D.problems(sp, ch) + (D.internal_problems(sp, g[1], [g[2]]) if g[0] == 'iline' else [])
        if bad:
            raise ValueError(rpt("Not applied: ") + bad[0])
        return sp, ch

    def _ghost(self, spec):
        d = self._delta(spec)
        if np.linalg.norm(d) < 1e-6:
            return None, (), []
        sp, ch = self._apply(spec, d, light=True)
        g = self._g()
        return sp, ch, ([D.internal(D.piece(sp, g[1]), g[2])['pts']] if g[0] == 'iline' else [])

    def cancel(self, context):
        clear_ghost()
        dialog_back(context)

    def execute(self, context):
        clear_ghost()
        dialog_back(context)
        spec, _ = store.get(context)
        try:
            d = self._delta(spec)
            if np.linalg.norm(d) < 1e-6:
                return {'CANCELLED'}
            sp, ch = self._apply(spec, d)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        msg = commit(context, sp, ch, old_spec=spec)
        if msg:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


def draw_edges_tab(layout, context):
    """The Tool tab's AC9 Edges: the picked outline edges, each with its length, and their total."""
    sel = selected_edges(store.get(context)[0])
    if not sel:
        return
    col = layout.column()
    from .shape_tool import fmt
    col.label(text=tx("Edges: {0}   total {1}", len(sel), fmt(context, sum(x[2] for x in sel))), icon='IPO_LINEAR')
    box = col.column(align=True)
    for nm, eid, L in sel[:12]:
        box.label(text=f"{nm[:14]}  {eid}   {fmt(context, L)}")
    if len(sel) > 12:
        box.label(text=tx("... and {0} more", len(sel) - 12))
    if len(sel) == 1:
        draw_gaps(col, context, store.get(context)[0], ('edge', sel[0][0], sel[0][1]))
    col.label(text="Shift+click: add / take out", icon='INFO')


def draw_internal_tab(layout, context):
    """The Tool tab's AC9 Internal Line: the picked line's fold, elastic, the seams along it, Cut and Delete."""
    spec, _ = store.get(context)
    s = selected_internal(context, spec)
    if s is None:
        return
    name, lid, it = s
    col = layout.column()
    from .shape_tool import fmt
    col.label(text=f"{name[:16]}  {lid}  ({fmt(context, S.polyline_len(it['pts']))})", icon='IPO_LINEAR')
    hole = bool(it.get('hole'))
    if it.get('closed'):            # a closed shape: a hole or not, pressed while it is one (user 2026-10-28)
        # a hole or not is the shape's state, kept here; Clone as Pattern is a right-click item (the Tool tab holds
        # values, the menu the verbs; user 2026-11-11)
        op = col.operator("ac9_cloth_sim.cut_out", text=tx("Convert to Hole"), icon='SELECT_SUBTRACT', depress=hole)
        op.piece, op.line = name, lid
    f = col.column()
    f.enabled = not hole            # a hole has no cloth to fold or shrink
    f.use_property_split = True
    f.use_property_decorate = False
    f.prop(context.scene, "ac9_iline_fold_angle", text=tx("Fold Angle"))
    if _taper_ends(spec,s):
        taper=f.row();taper.enabled=abs(context.scene.ac9_iline_fold)>1e-6
        taper.prop(context.scene,"ac9_iline_end_taper",text=tx("Taper"))
    # a line that stops inside the piece (a tuck's) folds by its angle while simulating, but has no two sides:
    # Moves and Layers grayed with why (user 2026-10-25)
    # asked only for a line with an angle: it meshes every piece when the pattern changed, and a line with none was
    # meshed again after each drag of it, the screen still for 0.3-3 s (user 2026-10-28)
    from . import fold_sides as FS
    partial = FS.partial(context, name, lid) if abs(context.scene.ac9_iline_fold) > 1e-6 else False
    if abs(context.scene.ac9_iline_fold) > 1e-6:
        f.row(align=True).prop(context.scene, "ac9_iline_fold_dir", expand=True)
        _draw_side_choices(f, context.scene, "ac9_iline_fold_side", "Turns", 'NONE', not partial)
    # the placed shape follows the fields at once, the first time too (user 2026-10-24: no Fold button); a line it
    # cannot fold says why, with Local Sim to see it
    from . import place_fold as PF
    if abs(context.scene.ac9_iline_fold) > 1e-6:
        PF.draw_why(f, context, name, lid)
        # one layer (user 2026-10-24): never refused, what looks wrong is said under it
        _draw_side_choices(f, context.scene, "ac9_iline_fold_layers", "Layers", 'BOTH', not partial)
        if partial:
            f.label(text=tx("Stops inside the piece: no A / B"), icon='INFO')
        if _flayers_get(None):
            import textwrap
            nc = f.column(align=True)
            for k, t in enumerate(t_ for n_ in notes_for(name, lid) for t_ in textwrap.wrap(n_, 34)):
                nc.label(text=t, icon='INFO' if k == 0 else 'BLANK1')
    f.prop(context.scene, "ac9_iline_elastic", text=tx("Elastic"))
    sewn = [sm['id'] for sm in spec['seams'] if any(sm[k]['piece'] == name and sm[k].get('line') == lid
                                                    for k in ('a', 'b'))]
    if sewn:
        col.label(text="Sewn along it:")
        g = col.grid_flow(columns=3, align=True)
        for sid in sewn[:9]:
            g.operator("ac9_cloth_sim.pick_seam", text=sid).seam = sid
    # Cut and Delete are right-click items and keys (X): the Tool tab holds the line's values (user 2026-11-11)
    draw_gaps(col, context, spec, ('iline', name, lid))


def _draw_side_choices(layout, scene, prop, label, neutral, enabled):
    """Three adjacent choices per setting (user 2026-10-30), with room reserved for the label."""
    row = layout.row(align=True)
    row.enabled = enabled
    row.use_property_split = False
    region = bpy.context.region
    if region is not None and region.width >= 350:
        split = row.split(factor=0.26, align=True)
        name = split.row()
        name.alignment = 'RIGHT'
        name.label(text=tx(label))
        row = split.row(align=True)
    else:
        row.label(text=tx(label))
        row = layout.row(align=True)
        row.enabled = enabled
        row.use_property_split = False
    row.prop_enum(scene, prop, neutral)
    row.prop_enum(scene, prop, 'A')
    row.prop_enum(scene, prop, 'B')


class AC9_OT_SimRestorePieces(bpy.types.Operator):
    """Make the flat pattern's missing piece objects again (deleted with Blender's own X: the pattern still has them)"""
    bl_idname = "ac9_cloth_sim.restore_pieces"
    bl_label = "Restore Pieces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and PV.layout_empty(context) is not None

    def execute(self, context):
        spec, outlines = store.get(context)
        gone = missing_pieces(context, spec)
        if not gone:
            return {'CANCELLED'}
        PV.update_pieces(context, spec, outlines, set(gone))
        overlay.invalidate()
        self.report({'INFO'}, rpt("Restored {0} piece(s): {1}", len(gone), ', '.join(gone[:6])))
        return {'FINISHED'}


def missing_pieces(context, spec):
    """The pieces whose flat pattern objects are gone (every piece has one, Off All too), [] when no pattern is
    shown."""
    if spec is None or PV.layout_empty(context) is None:
        return []
    obs = PV.piece_objects()
    return [pc['name'] for pc in spec['pieces'] if pc['name'] not in obs]


class AC9_OT_SimMirrorCopy(bpy.types.Operator):
    """Make mirrored copies of the selected pattern pieces beside them, with their seams; each copy is edited together with its original from then on, and placed in 3D as its mirror image when the original is placed"""
    bl_idname = "ac9_cloth_sim.mirror_copy"
    bl_label = "Mirror Copy"
    bl_options = {'REGISTER', 'UNDO'}

    # the pair's mirror plane (user 2026-11-11): left / right beside the piece, front / back turned upside down above
    # it; changed later in Tool tab > AC9 Piece > Pair
    plane: bpy.props.EnumProperty(name="Mirror Plane", items=[
        ('x', "Left / Right", "Mirror left to right: the copy beside the piece, its 3D mirror image across the body's center (X)"),
        ('y', "Front / Back", "Mirror front to back: the copy upside down beside the piece; in 3D a piece laid flat is mirrored where it is, one on the body across its center (Y)")],
        default='x')

    # the right-click menu: the copies follow the mouse until a click, as Duplicate (Shift+D)
    follow: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and bool(SEL.pattern_pieces(context))

    def invoke(self, context, event):
        r = self.execute(context)
        if r == {'FINISHED'} and self.follow and getattr(self, 'made', None) and context.region is not None                 and context.region.type == 'WINDOW' and AC9_OT_SimPatternDrag.poll(context):
            import json
            SEL.deselect_all(context)               # the copies selected, as Duplicate's
            for c in self.made:
                TB.select_piece(context, c, extend=True)
            bpy.ops.ac9_cloth_sim.pattern_transform('INVOKE_DEFAULT', mode='MOVE', pieces=json.dumps(self.made))
        return r

    def execute(self, context):
        # the symmetric pattern (user 2026-10-23, Fable review REVIEW_fable_symmetric_copy_2026-10-23): several
        # pieces at once, always with their seams, placed as mirror images; buttons are not copied
        from .core import seam_twin as ST
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        names = SEL.pattern_pieces(context)
        made, skipped = D.mirror_group(spec, names, plane=self.plane)
        if not made:
            self.report({'WARNING'}, rpt("{0}: each has its mirrored twin already", ', '.join(skipped)))
            return {'CANCELLED'}
        # a little off the originals, as Duplicate (user 2026-11-11: mirror_group lays them right of every piece,
        # far off the screen); before the 3D placement below, which mirrors the original's
        d = (D.group_center(spec, list(made)) - D.group_center(spec, list(made.values()))
             + np.array([DUPLICATE_OFF, -DUPLICATE_OFF]))
        D.transform_group(spec, list(made.values()), (), 0.0, (1.0, 1.0), tuple(d))
        self.made = list(made.values())
        ids, notes = ST.mirror_seams_group(spec, made)
        pl = spec.get('placement', {})
        placed = [n for n in made if 'anchor' in pl.get(n, {})]
        for n in placed:
            # a new pair starts linked ('unlinked' left on the original by a pair deleted before would set it apart)
            pl[n].pop('unlinked', None)
            if self.plane == 'y' and S.is_flat(pl[n]):
                # a piece laid flat: its front / back copy where it is, mirrored about its own middle (user
                # 2026-11-12); moving one apart in y moves the other the opposite way
                from . import placed_view as PLV
                ob = PLV.objects(spec).get(n)
                if ob is not None and ob.get(PLV.PROP) == n:          # where it is shown (moved by hand too)
                    W = PLV._world_co(ob)
                    mid = 0.5 * (W[:, 1].min() + W[:, 1].max()) * 1000.0
                else:
                    mid = S.flat_center(pl[n], S.Outline(D.piece(spec, n)))[1]
                S.set_mirror_at(spec, (n, made[n]), mid)
            pl[made[n]] = S.mirrored_placement(pl[n], S.Outline(D.piece(spec, n)), S.Outline(D.piece(spec, made[n])),
                                               pose='mirror', cx=S.mirror_center(spec, self.plane, n))
        commit(context, spec, list(made.values()))
        r = _show_placed(context, spec, made, placed)
        for n in notes:
            self.report({'WARNING'}, rpt("Seams: {0}", n))
        msg = rpt("Added {0} mirrored ({1}), {2} seams", len(made), ', '.join(made.values()), len(ids))
        if notes:
            msg += rpt(", {0} not mirrored", len(notes))
        if skipped:
            msg += rpt("; skipped, twin already: {0}", ', '.join(skipped))
        self.report({'WARNING'} if notes or skipped or r else {'INFO'}, msg + "".join(rpt("; ") + x for x in r))
        return {'FINISHED'}


class AC9_OT_SimParallelLine(bpy.types.Operator):
    """Add internal lines parallel to the outline edge or the internal line under the cursor (or the picked edges:
    edges next to each other give one line). The dialog asks how far and how many first"""
    bl_idname = "ac9_cloth_sim.parallel_line"
    bl_label = "Parallel Line"
    bl_options = {'REGISTER', 'UNDO'}

    distance: bpy.props.FloatProperty(name="Distance", default=0.02, min=0.0005, soft_max=0.2, precision=1,
                                      subtype='DISTANCE', unit='LENGTH',
                                      description="How far from the edge or line (each next line as far again)")
    count: bpy.props.IntProperty(name="Lines", translation_context=CTX, default=1, min=1, max=20,
                                 description="How many lines, evenly spaced")
    flip: bpy.props.BoolProperty(name="Other Side", description="Draw them on the line's other side")
    both: bpy.props.BoolProperty(name="Both Sides", description="Draw them on both sides of the line")
    extend: bpy.props.BoolProperty(name="To the Outline", default=True,
                                   description="Run their ends on (or cut them back) to the outline; else as long as "
                                               "the line")
    piece: bpy.props.StringProperty(options={'HIDDEN'})
    edges: bpy.props.StringProperty(options={'HIDDEN'})
    line: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        # the edge or internal line under the cursor (with the other picked edges of its piece when it is one of
        # them), else the picked edges or line
        spec, outlines = store.get(context)
        h = None
        if AC9_OT_SimPatternDrag.poll(context):
            h = pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)
        sel = [(nm, eid) for nm, eid, _ in selected_edges(spec)]
        isel = selected_internal(context, spec)
        self.piece = self.edges = self.line = ""
        if h and h[0] == 'edge':
            self.piece = h[1]
            self.edges = ",".join([eid for nm, eid in sel if nm == h[1]] if (h[1], h[2]) in sel else [h[2]])
        elif h and h[0] in ('iline', 'iend', 'inode', 'ihandle'):
            self.piece, self.line = h[1], h[2]
        elif sel:
            self.piece = sel[-1][0]
            self.edges = ",".join(eid for nm, eid in sel if nm == self.piece)
        elif isel is not None:
            self.piece, self.line = isel[0], isel[1]
        else:
            self.report({'WARNING'}, rpt("Point at an outline edge or an internal line, or pick one"))
            return {'CANCELLED'}
        dialog_aside(context, event, [self.piece], spec)     # beside the piece: the dialog does not cover the ghost
        ghost_open(self)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "distance")
        col.prop(self, "count")
        if self.line:
            col.prop(self, "both")
            sub = col.column()
            sub.enabled = not self.both
            sub.prop(self, "flip")
            col.prop(self, "extend")
        spec, _ = store.get(context)
        if spec is not None and self.piece:     # the lines, drawn on the pattern while the values change
            draw_why(col, ghost(self, ("parallel_line", self.piece, self.edges, self.line, self.distance, self.count,
                                 self.flip, self.both, self.extend),
                                lambda: (None, (), self._lines(D.piece(spec, self.piece)))))

    def _lines(self, pc):
        """The new lines' points (mm); a ValueError when one does not fit."""
        lines = []
        for k in range(1, self.count + 1):
            dist = self.distance * 1000.0 * k
            if self.line:
                for side in ((1.0, -1.0) if self.both else (-1.0,) if self.flip else (1.0,)):
                    lines.append(D.offset_internal(pc, self.line, dist, side, self.extend))
            else:
                lines += [D.offset_line(pc, ch, dist) for ch in D.edge_chains(pc, self.edges.split(","))]
        return lines

    def cancel(self, context):
        clear_ghost()
        dialog_back(context)

    def execute(self, context):
        clear_ghost()
        dialog_back(context)
        spec, _ = store.get(context)
        if not self.piece or not (self.edges or self.line):
            return {'CANCELLED'}
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        try:
            lines = self._lines(D.piece(sp, self.piece))
            lids = [D.add_internal(sp, self.piece, P) for P in lines]
        except (ValueError, StopIteration, KeyError) as exc:
            self.report({'WARNING'}, str(exc) or rpt("the edge is gone"))
            return {'CANCELLED'}
        bad = D.internal_problems(sp, self.piece, lids)
        if bad:
            self.report({'WARNING'}, rpt("Not added: ") + bad[0])
            return {'CANCELLED'}
        msg = commit(context, sp, [self.piece], old_spec=old)
        select_internal(context, self.piece, lids[-1])
        self.report({'INFO'}, rpt("Added line {0} on {1}, {2} apart", ', '.join(lids), self.piece,
                                  U.fmt(context, self.distance * 1000.0))
                    + (rpt("; {0}", msg) if msg else "") + rpt(F9))
        return {'FINISHED'}


DUPLICATE_OFF = 20.0     # mm right and down of the originals: a copy shows next to them


class AC9_OT_SimDuplicatePieces(bpy.types.Operator):
    """Copy the selected pattern pieces as they are (not mirrored, not a left-right pair), a little off the originals; with the mouse over the pattern they follow it until a click (right click: they stay a little off). The seams between the copied pieces are copied too. The copies are not placed (Place them)"""
    bl_idname = "ac9_cloth_sim.duplicate_pieces"
    bl_label = "Duplicate"
    bl_options = {'REGISTER', 'UNDO'}

    # Shift+D and the right-click menu: the copies follow the mouse. A panel button leaves them a little off the
    # originals (its region is the 3D view's WINDOW too, so the region cannot tell them apart: GUI test 2026-11-11)
    follow: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        self.names = SEL.pattern_pieces(context)
        if not self.names and AC9_OT_SimPatternDrag.poll(context):
            spec, outlines = store.get(context)
            h = pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y,
                     mode=None)
            self.names = [h[1]] if h else []
        self.near = True
        r = self.execute(context)
        # over the pattern: the copies follow the mouse until a click, as Blender's Shift+D (user 2026-11-11: they
        # were laid right of every piece, off the screen); right click leaves them a little off the originals
        if r == {'FINISHED'} and self.follow and getattr(self, 'made', None) and context.region is not None \
                and context.region.type == 'WINDOW' and AC9_OT_SimPatternDrag.poll(context):
            import json
            bpy.ops.ac9_cloth_sim.pattern_transform('INVOKE_DEFAULT', mode='MOVE', pieces=json.dumps(self.made))
        return r

    def execute(self, context):
        names = getattr(self, 'names', None) or SEL.pattern_pieces(context)
        if not names:
            self.report({'WARNING'}, rpt("Select the pieces to copy"))
            return {'CANCELLED'}
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        made, ids = D.duplicate_group(spec, names)
        if getattr(self, 'near', False) and made:
            # from the place duplicate_group lays them (right of every piece) to a little off the originals
            a, b = next(iter(made.items()))
            d = D.group_center(spec, [a]) - D.group_center(spec, [b]) + np.array([DUPLICATE_OFF, -DUPLICATE_OFF])
            D.transform_group(spec, list(made.values()), (), 0.0, (1.0, 1.0), tuple(d))
        self.made = list(made.values())
        commit(context, spec, list(made.values()))
        SEL.deselect_all(context)                   # the copies selected: Place, G or S goes on with them
        for c in made.values():
            TB.select_piece(context, c, extend=True)
        self.report({'INFO'}, rpt("Copied {0} ({1}), {2} seams", len(made), ', '.join(made.values()), len(ids)))
        return {'FINISHED'}


class AC9_OT_SimSymmetrize(bpy.types.Operator):
    """Make the selected piece symmetric about its center line: the left half stays and the right is made again as its mirror image (F9: keep the right half instead). Symmetric Edit is switched on"""
    bl_idname = "ac9_cloth_sim.symmetrize"
    bl_label = "Symmetrize"
    bl_options = {'REGISTER', 'UNDO'}

    keep: bpy.props.EnumProperty(name="Keep", items=[('LEFT', "Left Half", "Keep the left half, the right is made again"),
                                                    ('RIGHT', "Right Half", "Keep the right half, the left is made again")],
                                 default='LEFT')

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and _selected_piece(context) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        old = S.loads(S.dumps(spec))
        sp = S.loads(S.dumps(spec))
        name = _selected_piece(context)
        try:
            ch = D.symmetrize(sp, name, self.keep)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        bad = D.problems(sp, ch)
        if bad:
            self.report({'WARNING'}, rpt("Not applied: ") + bad[0])
            return {'CANCELLED'}
        msg = commit(context, sp, ch, old_spec=old)
        self.report({'INFO'}, (rpt("{0}: symmetric, the left half kept", name) if self.keep == 'LEFT'
                               else rpt("{0}: symmetric, the right half kept", name))
                    + (rpt("; {0}", msg) if msg else "") + rpt(F9))
        return {'FINISHED'}


@PB.prepared_consumer
def _show_placed(context, spec, made, placed, *, preparation=None):
    """The copies of placed pieces shown as placed objects too, when their originals are (placement.MirrorPose's
    way). Report lines."""
    from . import placed_view as PLV
    from . import dress as DR
    sp = store.get(context)[0]
    obs = PLV.objects(sp)
    if not any(n in obs for n in placed):
        return []
    PLV.leave_edit(context)
    try:
        res = DR.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
        PLV.build(context, sp, res['pat'], res['tris'], res['X'], res['pairs'], only={made[n] for n in placed})
    except ValueError as exc:
        PB.cancel(preparation)
        return [tx("placed object not made again ({0}): Move by Hand", exc)]
    return []


class AC9_OT_SimSymmetricEdit(bpy.types.Operator):
    """Edit the selected piece symmetrically about its center line: a corner, edge or curve handle moved on one side moves on the other side as its mirror image (for a piece cut as one, symmetric left and right)"""
    bl_idname = "ac9_cloth_sim.symmetric_edit"
    bl_label = "Symmetric Edit"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and _selected_piece(context) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        name = _selected_piece(context)
        # the selected pieces all follow the active one's switch (the Tool tab sets them all)
        names = [n for n in dict.fromkeys(SEL.pattern_pieces(context) + [name])]
        if D.piece(spec, name).get('mirror_edit'):
            for n in names:
                D.piece(spec, n).pop('mirror_edit', None)
            store.put(context, spec)
            self.report({'INFO'}, rpt("{0}: each side edited on its own", ', '.join(names)))
            return {'FINISHED'}
        bad = [n for n in names if D.self_mirror_map(D.piece(spec, n)) is None]
        ok = [n for n in names if n not in bad]
        if not ok:
            self.report({'WARNING'}, rpt("{0}: not symmetric about the center line (corners more than {1:g} mm off their mirror images): press Symmetrize", ', '.join(bad), D.SELF_TOL))
            return {'CANCELLED'}
        for n in ok:
            D.piece(spec, n)['mirror_edit'] = True
        store.put(context, spec)
        self.report({'WARNING'} if bad else {'INFO'},
                    rpt("{0}: edited symmetrically about the center line", ', '.join(ok))
                    + (rpt("; not symmetric, left as they were: {0}", ', '.join(bad)) if bad else ""))
        return {'FINISHED'}


class AC9_OT_SimUpdate3D(bpy.types.Operator):
    """Simulate in phases from the garment as it hangs now (pattern edits are carried onto it as you make them): the whole garment settles to the edited pattern, frozen pieces stay, new pieces are placed and sewn on. Dresses the first time"""
    bl_idname = "ac9_cloth_sim.update_3d"
    bl_label = "Phase Sim"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bpy.ops.ac9_cloth_sim.dress.poll()

    def execute(self, context):
        from . import dress as DR
        if DR.AC9_OT_SimDress.running():               # pressed again while it runs: stop, as the Dress panel's buttons
            DR.AC9_OT_SimDress.request_stop(context)
            return {'CANCELLED'}
        spec = store.get(context)[0]
        g = bpy.data.objects.get(DR.garment_name(spec))
        mode = 'CONTINUE' if DR.job_of(g) else 'START'
        if mode == 'CONTINUE' and DR.phase_point(context, g) == 'start':
            # the first frame of the last Phase Sim on screen: that run again from the placement, the pieces put back
            # by Reset from their placed objects (read by the run, so not written into the garment first)
            mode = 'START'
        # the undo step, flush_placed (Reset pieces' placed shapes into the garment) and the pattern carry are the
        # run's own (dress execute, in that order, with the same refusal): done here first as well, the flush
        # dropped the placed objects before the run read the shapes the garment has no place for, and pieces On
        # again after a result started from the placement rule instead of their placed shapes (a collar x2.71
        # with its hand move and fold lost, 2026-11-04, review REVIEW_fable_run_start_shape_2026-11-04 step 0)
        return bpy.ops.ac9_cloth_sim.dress('INVOKE_DEFAULT', mode=mode, button='SIMULATE')


def _carry_pending(context, g, spec):
    """Before Simulate: a pattern edit the 3D could not take when it was made (the carry refused it) leaves the
    pattern ahead of the garment, and Continue would start from a garment that does not fit the pattern (cloth
    added since squeezed to nothing: the solver dropped those triangles and stopped, 2026-10-08). Carry the edits
    made since the garment's pattern now (several edits together can go where one alone could not: one leg
    lengthened, then the other). Returns a report line ('' when nothing was pending); raises ValueError when the
    garment still cannot take the pattern. Called by Draft > Simulate and by Dress > Continue / Live (dress.py), so a
    pattern changed any way is caught before the solver sees it."""
    import json
    import os
    from . import dress as DR
    job = DR.job_of(g)
    sj = os.path.join(job, 'ac9_spec.json') if job else None
    if not sj:
        return ''
    if not os.path.exists(sj):
        # a dressed result no edit has been carried from yet (a job from before 2026-10-10 wrote no record): the
        # pattern's shape against the result's mesh (core/transfer.shape_changed: the outline and internal lines,
        # not the mesh remade, so an older mesher or another mesh size still matches). A pattern changed some other
        # way (the spec text, an AI, store.put) cannot be carried: stop (2026-10-10, Continue laid the added cloth on
        # the old edge, 1366 triangles of no area, and the solver stopped)
        from .core import transfer as TR
        try:
            old = np.load(os.path.join(job, 'pattern.npz'), allow_pickle=True)
            idx = np.load(os.path.join(job, 'scene.idx.npz'))
        except (OSError, ValueError):
            return ''
        changed = TR.shape_changed(spec, old, idx)
        if not changed:
            return ''
        raise ValueError(f"{', '.join(n for n, _ in changed[:3])} changed outside the drafting tools since the "
                         "garment was simulated, so there is no record to carry from: undo the change, or Back to "
                         "Placed with nothing selected")
    base = S.loads(open(sj, encoding='utf-8').read())
    if cloth_key(base) == cloth_key(spec):
        drop_pending(context)
        return ''
    rigid, sources = _pending_hints(base)          # edits made with 3D Follows Edits off: told as they were made
    msg = carry_to_3d(context, base, spec, rigid, sources) or ''
    drop_pending(context)
    return msg


def seams_off_length(base, spec, tol_mm=10.0, tol_rel=0.15):
    """Seams whose two sides the edit made unequal (they were within tolerance when dressed): [(id, a mm, b mm)].
    A seam laid back onto the dressed garment holds both sides on one line, so a side made much longer than its
    partner is what folds the carry (user's sleeve 2026-10-20: underarm 55.8 / 55.8 dressed, 159.6 / 75.7 edited)."""
    old = {sm['id']: sm for sm in base.get('seams', [])}
    out = []
    for sm in spec.get('seams', []):
        try:
            a, b = S.side_length(spec, sm['a']), S.side_length(spec, sm['b'])
        except Exception:
            continue
        if abs(a - b) <= max(tol_mm, tol_rel * max(a, b)):
            continue
        o = old.get(sm['id'])
        try:
            oa, ob = (S.side_length(base, o['a']), S.side_length(base, o['b'])) if o else (None, None)
        except Exception:
            oa = ob = None
        if oa is not None and abs(oa - ob) > max(tol_mm, tol_rel * max(oa, ob)):
            continue                                       # already unequal when dressed: eased on purpose
        out.append((sm['id'], a, b))
    return sorted(out, key=lambda x: -abs(x[1] - x[2]))


def _with_seam_hint(exc, base, spec, near_mm=20.0):
    """exc with a Match Length hint added when a seam the edit made unequal runs within near_mm of where the cloth
    folds (a far one is not the cause: the user's sleeve folded at the cap tip, 270 mm from the lengthened underarm,
    Fable review 2026-10-20). An error without a place keeps the hint for any such seam."""
    from .core import transfer as TR
    msg = str(exc)
    off = seams_off_length(base, spec)
    pts, piece = getattr(exc, 'points', None), getattr(exc, 'piece', None)
    if off and pts is not None and len(pts):
        ol = S.Outline(D.piece(spec, piece))
        keep = []
        for sid, a, b in off:
            sm = next(x for x in spec['seams'] if x['id'] == sid)
            sides = [sm[k] for k in ('a', 'b') if sm[k]['piece'] == piece and 'line' not in sm[k]]
            if any(np.linalg.norm(ol.side_polyline(sd, step=2.0)[:, None] - pts[None], axis=-1).min() <= near_mm
                   for sd in sides):
                keep.append((sid, a, b))
        off = keep
    if off:
        sid, a, b = off[0]
        msg += (tx("; seam {0}'s two sides are now {1} / {2} (Match Length makes them equal)", sid, U.fmt(bpy.context, a), U.fmt(bpy.context, b))
                + (tx(", {0} more seam(s) like it", len(off) - 1) if len(off) > 1 else ""))
    if pts is not None:
        return TR.FoldError(msg, piece, pts, getattr(exc, 'seams', []))
    return ValueError(msg)


def cloth_key(spec):
    """What of a spec shapes the cloth a carry has to follow: each piece's outline, internal lines and whether it is
    on, and the seams (their check note, source and stage aside: confirming or flipping a seam's origin moves no
    cloth). Notches, roles, measures, the mesh size and the rest are left out (Continue re-meshes for a new mesh size
    itself)."""
    import json
    return json.dumps([[{k: p.get(k) for k in ('name', 'edges', 'internals', 'enabled')} for p in spec['pieces']],
                       [{k: v for k, v in sm.items() if k not in ('check', 'source', 'when')}
                        for sm in spec.get('seams', [])]], sort_keys=True)


# where the last refused carry folds: drawn red on the pattern while the pattern is the refused one (Ctrl+Z hides it)
fold_marks = {'key': None, 'piece': None, 'points': None}


def pending_refused(exc):
    """The one message for a garment that cannot take the pattern as it is (Simulate, Dress > Continue / Live), and
    the red marks where it folds. Two ways out, in order: take the edit back, or Reset the piece (placed and sewn on
    again: a Reset piece is not carried, 2026-10-20)."""
    pts = getattr(exc, 'points', None)
    fold_marks.update(key=store._cache['key'] if pts is not None else None, piece=getattr(exc, 'piece', None),
                      points=pts)
    where = " (red in the pattern view)" if pts is not None else ""
    from . import dress as DR
    if isinstance(exc, DR.MeshMismatch):
        return tx("Not simulated: {0}", exc)
    return (tx("Not simulated: {0}{1}. Ctrl+Z takes the edit back. Or select the piece, press Reset, then Phase Sim: it is placed and sewn on again", exc, where))


_handles = []

class AC9_OT_SimSync3D(bpy.types.Operator):
    """Give the dressed garment and the placed shapes the pattern edits made with 3D Follows Edits off (no simulation)"""
    bl_idname = "ac9_cloth_sim.sync_3d"
    bl_label = "Update 3D"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import dress as DR
        return context.scene.ac9_cloth_sim.sync_pending and DR.AC9_OT_SimDress.running() is None

    def execute(self, context):
        msg = sync_3d(context)
        if msg:
            self.report({'WARNING'} if context.scene.ac9_cloth_sim.sync_pending else {'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------- the right-click menu (user 2026-10-28)
# Right click while nothing is going on: the verbs of what is under the cursor (Fable docs/reviews/
# REVIEW_fable_context_menu_ui_2026-10-28 §3). In the middle of an operation (a drag, points being clicked) the
# operation's own modal takes the right click: cancel, as before (10/21). Only the verbs the keys already have, no
# values (those are the Tool tab's), Delete last. The menu remembers what was under the cursor when it opened; an
# item puts the cursor back there and runs the key's operator, so every operator keeps reading the cursor.

_menu = {'tool': None, 'hit': None, 'xy': None}


def _menu_hit(context, event, tool):
    spec, outlines = store.get(context)
    if spec is None:
        return None
    kind, ob, idx = TB.under(context, event.mouse_region_x, event.mouse_region_y)
    if kind == 'GARMENT':                         # the dressed garment, in every tool: its piece under the cursor
        name = TB.garment_piece(ob, idx)
        return ('garment', name) if name else None
    if tool == 'EDIT_SEAMS':
        from . import seam_edit_tool as SET
        h = SET.seam_hit(context, event, kind)
        return ('seam', h[1], h[2]) if h else None
    if tool == 'EDIT_MEASURE':                    # a measured line or a tape
        from . import measure_edit as MEE
        key = MEE.pick(context, (event.mouse_region_x, event.mouse_region_y), kinds=MEE.EDIT_KINDS)
        return ('measure', key) if key else None
    if tool == 'PLACE':                           # a flat piece to place, or a placed piece's object
        if kind == 'PATTERN':
            return ('placed', ob[PV.PIECE_PROP], ob)
        if kind == 'PLACED':
            return ('placed', ob[TB.PLACED_PROP], ob)
        return None
    if tool not in ('EDIT_PATTERN', 'TRANSFORM_PATTERN') or kind not in ('PATTERN', 'PLANE'):
        return None
    return pick(context, spec, outlines, PV.layout_matrix(context), event.mouse_region_x, event.mouse_region_y)


def _menu_select(context, hit):
    """What the menu's verbs act on (the selected pieces or seams): the thing right-clicked, unless it is picked."""
    from . import placement as PL
    if hit[0] == 'piece' and hit[1] != _selected_piece(context):
        TB.select_piece(context, hit[1])
    elif hit[0] == 'seam':
        from . import seam_edit_tool as SET
        spec = store.get(context)[0]
        if hit[1] not in {sm['id'] for sm in store.selected_seams(context, spec)}:
            SET.select_seam(context, hit[1])
    elif hit[0] == 'measure':
        from . import measure_edit as MEE
        if MEE.sel(context) != hit[1]:
            MEE.select(context, hit[1])
    elif hit[0] == 'placed' and not hit[2].select_get():
        TB.select_object(context, hit[2])
    elif hit[0] == 'garment' and hit[1] not in PL.selected_pieces(context):
        from . import dress as DR
        g = bpy.data.objects.get(DR.garment_name(store.get(context)[0]))
        if g is not None:
            TB.pick_garment_piece(context, g, hit[1])


class AC9_OT_SimContextMenu(bpy.types.Operator):
    """The verbs of what is under the cursor"""
    bl_idname = "ac9_cloth_sim.context_menu"
    bl_label = "AC9 Context Menu"
    bl_options = {'INTERNAL'}

    tool: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        hit = _menu_hit(context, event, self.tool)
        _menu.update(tool=self.tool, hit=hit, xy=(event.mouse_x, event.mouse_y))
        if hit:
            _menu_select(context, hit)
        bpy.ops.wm.call_menu(name=AC9_MT_SimContext.bl_idname)
        return {'FINISHED'}


class AC9_OT_SimContextDo(bpy.types.Operator):
    """Run a menu item on what was under the cursor"""
    bl_idname = "ac9_cloth_sim.context_do"
    bl_label = "AC9 Context Menu Item"
    bl_options = {'INTERNAL'}

    op: bpy.props.StringProperty(options={'SKIP_SAVE'})
    action: bpy.props.StringProperty(options={'SKIP_SAVE'})     # the operator's action, or "prop=value"

    def execute(self, context):
        mod, name = self.op.split('.')
        fn = getattr(getattr(bpy.ops, mod), name)
        if _menu['xy'] is not None and context.window is not None:
            context.window.cursor_warp(*_menu['xy'])     # its operators read the cursor, as the key would
        try:
            kw = (dict(kv.split('=', 1) for kv in self.action.split(',')) if '=' in self.action
                  else {'action': self.action} if self.action else {})
            kw = {k: {'True': True, 'False': False}.get(v, v) for k, v in kw.items()}    # "one_side=True"
            # undo=True: an operator called from Python leaves no undo step of its own unless asked, and this one has
            # none either; Ctrl+Z then went back past the menu item's edit, the piece objects back but the pattern
            # (and the outline drawn from it) still edited (a spread from the menu, user 2026-11-11)
            return fn('INVOKE_DEFAULT', True, **kw)
        except RuntimeError as e:                       # its poll failed, or it refused
            self.report({'WARNING'}, str(e).split('\n')[0].replace('Error: ', ''))
            return {'CANCELLED'}


# (label, operator, action or "prop=value") per what is under the cursor (pick's kinds, 'seam'); None: a separator
_MENU_ITEMS = {
    'iline': [("Cut Along Line", "ac9_cloth_sim.cut_line", ""), ("Slash and Spread", "ac9_cloth_sim.spread", ""),
              ("Parallel Line", "ac9_cloth_sim.parallel_line", ""), None,
              ("Add Point", "ac9_cloth_sim.pattern_edit", 'SPLIT'), ("Curve", "ac9_cloth_sim.pattern_edit", 'CURVE'),
              None, ("Delete", "ac9_cloth_sim.pattern_edit", 'REMOVE')],
    'shape': [("Add Point", "ac9_cloth_sim.pattern_edit", 'SPLIT'), ("Curve", "ac9_cloth_sim.pattern_edit", 'CURVE'),
              None, ("Delete", "ac9_cloth_sim.pattern_edit", 'REMOVE')],
    'inode': [("Delete Point", "ac9_cloth_sim.pattern_edit", 'REMOVE')],
    'ihandle': [("Straighten", "ac9_cloth_sim.pattern_edit", 'CURVE')],
    'iend': [("Delete Line", "ac9_cloth_sim.pattern_edit", 'REMOVE')],
    'edge': [("Add Point", "ac9_cloth_sim.pattern_edit", 'SPLIT'), ("Parallel Line", "ac9_cloth_sim.parallel_line", ""),
             ("Curve", "ac9_cloth_sim.pattern_edit", 'CURVE'), ("Add Notch", "ac9_cloth_sim.pattern_edit", 'NOTCH'),
             ("Add Dart", "ac9_cloth_sim.add_dart", "")],
    'corner': [("Delete", "ac9_cloth_sim.pattern_edit", 'REMOVE')],
    'notch': [("Delete", "ac9_cloth_sim.pattern_edit", 'REMOVE')],
    'piece': [("Duplicate", "ac9_cloth_sim.duplicate_pieces", "follow=True"), ("Mirror Copy", "ac9_cloth_sim.mirror_copy", "follow=True"),
              ("Symmetrize", "ac9_cloth_sim.symmetrize", ""), None,
              ("Move", "ac9_cloth_sim.pattern_transform", "mode=MOVE"),
              ("Rotate", "ac9_cloth_sim.pattern_transform", "mode=ROTATE"),
              ("Scale", "ac9_cloth_sim.pattern_transform", "mode=SCALE"),
              ("Name", "ac9_cloth_sim.piece_label", ""), None, ("Delete Piece", "ac9_cloth_sim.delete_piece", "")],
    'placed': [("Back to Guide", "ac9_cloth_sim.reset_placed", ""), ("Back to Flat", "ac9_cloth_sim.clear_placement", ""),
               None, ("Mirror Placement", "ac9_cloth_sim.mirror_pose", ""), ("Swap Sides", "ac9_cloth_sim.swap_sides", ""),
               ("Unlink Pair", "ac9_cloth_sim.unlink_pair", "")],
    'garment': [("Back to Placed", "ac9_cloth_sim.reset", ""), ("Local Sim", "ac9_cloth_sim.dress", 'mode=LOCAL,button=LOCAL')],
    'seam': [("Turn Off", "ac9_cloth_sim.seam_edit", 'TOGGLE'), ("Match Length A", "ac9_cloth_sim.match_length", 'side=a'),
             ("Match Length B", "ac9_cloth_sim.match_length", 'side=b'), None,
             ("Move Side", "ac9_cloth_sim.seam_move_side", ""), ("Notch Pair", "ac9_cloth_sim.seam_notch", ""),
             ("Notch This Side", "ac9_cloth_sim.seam_notch", "one_side=True"), None,
             ("Delete", "ac9_cloth_sim.seam_edit", 'DELETE')],
}
# Whole Pieces on an internal line: the whole line (user 2026-10-28)
_MENU_ITEMS_T = {
    'iline': [("Move", "ac9_cloth_sim.pattern_transform", "mode=MOVE"),
              ("Rotate", "ac9_cloth_sim.pattern_transform", "mode=ROTATE"),
              ("Scale", "ac9_cloth_sim.pattern_transform", "mode=SCALE"), None,
              ("Delete", "ac9_cloth_sim.delete_selected", "")],
}
_MENU_KEYS = {"Add Point": "K", "Curve": "C", "Delete": "X", "Delete Point": "X", "Straighten": "C",
              "Delete Line": "X", "Add Notch": "N", "Duplicate": "Shift+D", "Rotate": "R", "Scale": "S", "Name": "F2",
              "Move": "G", "Move Side": "M", "Notch Pair": "N", "Notch This Side": "Ctrl+N"}


def menu_items(spec, hit, tool=None):
    """The menu's rows for hit (pick's tuple) as [(label, operator, action, extra props) | None]: what the menu
    shows, kept apart from drawing so a test can read it. tool: the menu's tool ('TRANSFORM_PATTERN': an internal
    line is moved, turned and scaled whole)."""
    if not hit:
        return []
    kind = hit[0]
    if kind == 'iline' and tool == 'TRANSFORM_PATTERN':
        pc = next((x for x in spec['pieces'] if x['name'] == hit[1]), None)
        it = next((x for x in (pc or {}).get('internals', []) if x['id'] == hit[2]), None)
        first = ([("Fill Hole" if it.get('hole') else "Convert to Hole", "ac9_cloth_sim.cut_out", "",
                   {'piece': hit[1], 'line': hit[2]}),
                  ("Clone as Pattern", "ac9_cloth_sim.clone_shape", "", {'piece': hit[1], 'line': hit[2]}),
                  ("Clone as Pattern (+Sew)", "ac9_cloth_sim.clone_shape", "",
                   {'piece': hit[1], 'line': hit[2], 'sew': True}), None]
                 if it is not None and it.get('closed') else [])
        return first + [r if r is None else r + ({},) for r in _MENU_ITEMS_T['iline']]
    if kind == 'iline':
        pc = next((x for x in spec['pieces'] if x['name'] == hit[1]), None)
        it = next((x for x in (pc or {}).get('internals', []) if x['id'] == hit[2]), None)
        if it is not None and it.get('closed'):
            hole = bool(it.get('hole'))
            first = [("Fill Hole" if hole else "Convert to Hole", "ac9_cloth_sim.cut_out", "",
                      {'piece': hit[1], 'line': hit[2]}),
                     ("Clone as Pattern", "ac9_cloth_sim.clone_shape", "", {'piece': hit[1], 'line': hit[2]}),
                     ("Clone as Pattern (+Sew)", "ac9_cloth_sim.clone_shape", "",
                      {'piece': hit[1], 'line': hit[2], 'sew': True}), None]
            rows = _MENU_ITEMS['shape']
            if hole:                                    # a hole: its points and Delete, no curve to bend here
                rows = [r for r in rows if r is None or r[2] != 'CURVE']
            return first + [r if r is None else r + ({},) for r in rows]
    if kind == 'measure':                         # a tape: Mirror, Delete; a measured line: back to where it was
        tape = hit[1].startswith('tape:')
        rows = ([("Mirror", "ac9_cloth_sim.measure_mirror", "")] if tape else []) +             [("Delete" if tape else "Reset Position", "ac9_cloth_sim.measure_delete", "")]
        return [r + ({},) for r in rows]
    if kind in ('placed', 'garment'):            # then the state rows: the ones it is not in
        pc = next((x for x in spec['pieces'] if x['name'] == hit[1]), None)
        cur = S.piece_state(pc) if pc is not None else 'active'
        st = [(lab, "ac9_cloth_sim.piece_state", 'state=' + k) for k, lab in
              (('active', "Active"), ('freeze', "Freeze"), ('off_all', "Off (All)")) if k != cur]
        return [r if r is None else r + ({},) for r in _MENU_ITEMS[kind] + [None] + st]
    if kind == 'seam':
        sm = next((x for x in spec.get('seams', []) if x['id'] == hit[1]), None)
        rows = list(_MENU_ITEMS['seam'])
        if sm is not None and not sm.get('enabled', True):
            rows[0] = ("Turn On",) + rows[0][1:]
        if sm is not None and 'line' in sm[hit[2]]:     # the notch pair goes on outlines only
            rows = [r for r in rows if r is None or r[0] not in ("Notch Pair", "Notch This Side")]
        return [r if r is None else r + ({},) for r in rows]
    rows = list(_MENU_ITEMS.get(kind, []))
    if kind in ('edge', 'corner') and _dart_hit(spec, hit):   # a dart's leg or apex: moving it (no key now)
        rows = [("Move Dart", "ac9_cloth_sim.move_dart", "")] + ([None] if rows else []) + rows
    return [r if r is None else r + ({},) for r in rows]


def _dart_hit(spec, hit):
    """Whether hit (an edge or a corner) is a leg or the apex of a dart."""
    from .core import dart as DT
    try:
        return any((hit[0] == 'edge' and hit[2] in dt['leg_edges']) or (hit[0] == 'corner' and hit[2] == dt['apex'])
                   for dt in DT.find_darts(spec, hit[1]))
    except Exception:
        return False


def _can_run(op):
    mod, name = op.split('.')
    try:
        return getattr(getattr(bpy.ops, mod), name).poll()
    except Exception:
        return False


POLLED_KINDS = {'placed', 'garment', 'measure'}


def shown_rows(rows, can_run=_can_run):
    """menu_items' rows without those whose operator cannot run now (a pair's rows on a piece without a pair, Mirror
    to Pair with no partner), and without a separator at either end or twice in a row."""
    out = []
    for r in rows:
        if r is not None and not can_run(r[1]):
            continue
        if r is None and (not out or out[-1] is None):
            continue
        out.append(r)
    while out and out[-1] is None:
        out.pop()
    return out


class AC9_MT_SimContext(bpy.types.Menu):
    bl_idname = "AC9_MT_sim_context"
    bl_label = "AC9"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'EXEC_DEFAULT'
        spec, _ = store.get(context)
        hit = _menu['hit']
        # the rows of a piece, the garment or a measurement act on what is selected: one that cannot run now is left
        # out (a pair's rows without a pair). The pattern's and the seams' rows read the cursor and stay
        rows = (shown_rows(menu_items(spec, hit, _menu['tool']), can_run=_can_run if hit and hit[0] in POLLED_KINDS else lambda op: True)
                if spec is not None else [])
        for r in rows:
            if r is None:
                layout.separator()
                continue
            label, op, action, props = r
            key = _MENU_KEYS.get(label)
            text = tx(label) + (f"   {key}" if key else "")
            if props:                                   # it names its target: run as it is
                o = layout.operator(op, text=text)
                for k, v in props.items():
                    setattr(o, k, v)
                continue
            o = layout.operator("ac9_cloth_sim.context_do", text=text)
            o.op, o.action = op, action
        if _menu['tool'] == 'EDIT_MEASURE':           # the pieces' Select All is no verb of a measurement
            if not rows:
                layout.label(text=tx("Nothing here"))
            return
        if rows:
            layout.separator()
        layout.operator("ac9_cloth_sim.select_all_pieces", text=tx("Select All")).action = 'SELECT'
        layout.operator("ac9_cloth_sim.select_all_pieces", text=tx("Deselect All")).action = 'DESELECT'


class AC9_OT_SimSelectAllPieces(bpy.types.Operator):
    """Select all the flat pattern's pieces, or none (the other objects are left as they are)"""
    bl_idname = "ac9_cloth_sim.select_all_pieces"
    bl_label = "Select All Pieces"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[('SELECT', "Select", ""), ('DESELECT', "Deselect", "")])

    def execute(self, context):
        obs = [o for o in PV.piece_objects().values() if o is not None and o.name in context.view_layer.objects]
        for o in obs:
            o.select_set(self.action == 'SELECT')
        if self.action == 'SELECT' and obs and (context.view_layer.objects.active not in obs):
            context.view_layer.objects.active = obs[0]
        return {'FINISHED'}


CLASSES = (AC9_OT_SimSync3D, AC9_OT_SimPatternHover, AC9_OT_SimPatternDrag, AC9_OT_SimSetGap, AC9_OT_SimPatternEdit, AC9_OT_SimPatternRotate,
           AC9_OT_SimPatternScale, AC9_OT_SimDuplicatePieces, AC9_OT_SimSymmetrize, AC9_OT_SimParallelLine,
           AC9_OT_SimEditPatternTool,
           AC9_OT_SimMatchLength, AC9_OT_SimExportDXF, AC9_OT_SimImportDXF, AC9_OT_SimImportSVG, AC9_OT_SimDrawPiece, AC9_OT_SimDrawLine, AC9_OT_SimNewPattern, AC9_OT_SimDeletePiece, AC9_OT_SimMirrorCopy,
           AC9_OT_SimSymmetricEdit, AC9_OT_SimUpdate3D, AC9_OT_SimDeleteInternal, AC9_OT_SimCutOut,
           AC9_OT_SimPickSeam, AC9_OT_SimPickPieceUnder, AC9_OT_SimDeleteSelected, AC9_OT_SimRestorePieces,
           AC9_OT_SimContextMenu, AC9_OT_SimContextDo, AC9_MT_SimContext, AC9_OT_SimSelectAllPieces,
           AC9_OT_SimCloneShape, AC9_OT_SimPatternTransform)


_icons = None       # bpy.utils.previews: the A / B color squares on Moves (user 2026-10-25)


def _make_icons():
    """Square color icons, the fill colors of the picked line's sides (fold_sides): 'A' orange, 'B' blue, 'AB' half
    each. On Moves only: on Layers they cut its labels to "B..." / "Ke..." in the sidebar's width (2026-10-25)."""
    global _icons
    import bpy.utils.previews
    from . import fold_sides as FS
    _icons = bpy.utils.previews.new()
    n = 32
    for key, cols in (('A', (FS.A_COL, FS.A_COL)), ('B', (FS.B_COL, FS.B_COL)), ('AB', (FS.A_COL, FS.B_COL))):
        px = np.zeros((n, n, 4), np.float32)
        px[3:-3, 3:n // 2, :3] = cols[0]
        px[3:-3, n // 2:-3, :3] = cols[1]
        px[3:-3, 3:-3, 3] = 1.0
        pv = _icons.new(key)
        pv.icon_size = pv.image_size = (n, n)
        pv.icon_pixels_float = pv.image_pixels_float = px.ravel().tolist()


def _icon(key):
    try:
        return _icons[key].icon_id
    except Exception:
        return 0


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    try:
        _make_icons()
    except Exception:
        pass
    bpy.types.Scene.ac9_iline_fold = bpy.props.FloatProperty(
        name="Fold", min=-180.0, max=180.0, step=100, get=_fold_get, set=_fold_set, options=set(),
        description="Fold angle on the picked internal line: + mountain, - valley, seen from the pattern's face; 0 = no fold")
    bpy.types.Scene.ac9_iline_fold_angle = bpy.props.FloatProperty(
        name="Fold Angle", translation_context=CTX, min=0.0, max=180.0, precision=1, step=100, get=_fangle_get,
        set=_fangle_set, options=set(),
        description="How far the picked internal line folds, 0 = no fold (Mountain / Valley and the side that turns below it)")
    bpy.types.Scene.ac9_iline_end_taper = bpy.props.FloatProperty(
        name="End Taper",translation_context=CTX,min=0.,max=1.,precision=1,subtype='DISTANCE',unit='LENGTH',
        get=_ftaper_get,set=_ftaper_set,options=set(),
        description="Distance over which the fold angle fades to zero at an internal endpoint; 0 keeps a constant angle. Simulation only")
    # seen from the pattern's face, as the solver folds it; the placed shape too since 2026-10-25 (user: mountain and
    # valley, the words of pattern making; DECISIONS cloth-sim-fold-face-mountain-valley-2026-10-25). The ids stay
    # 'IN' / 'OUT' (not saved: read from the sign of the spec's deg)
    bpy.types.Scene.ac9_iline_fold_dir = bpy.props.EnumProperty(
        name="Direction", translation_context=CTX, get=_fdir_get, set=_fdir_set, options=set(),
        items=[('IN', "Mountain", "A mountain fold seen from the pattern's face: the side turns behind the face (in "
                "towards the body when the face looks out)", 0),
               ('OUT', "Valley", "A valley fold seen from the pattern's face: the side turns over onto the face (out "
                "away from the body when the face looks out)", 1)])
    bpy.types.Scene.ac9_iline_fold_side = bpy.props.EnumProperty(
        name="Turns", translation_context=CTX, get=_fside_get, set=_fside_set, options=set(),
        items=[('NONE', "No Side", "Keep the fold direction and angle without turning a whole side when placed", 2),
               ('A', "Side A", "The side filled orange and labeled A on the pattern turns when the placed shape is "
                "folded", _icon('A'), 0),
               ('B', "Side B", "The side filled blue and labeled B on the pattern turns when the placed shape is "
                "folded", _icon('B'), 1)])
    bpy.types.Scene.ac9_iline_fold_layers = bpy.props.EnumProperty(
        name="Layers", translation_context=CTX, get=_flayers_get, set=_flayers_set, options=set(),
        items=[('BOTH', "Both", "Both sides of the fold are simulated as two layers", 0),
               ('A', "A only", "Only side A (orange) is simulated; side B, hatched on the pattern, follows it as a "
                "second layer, its weight and bending added to side A (a placket folded and sewn shut)", 1),
               ('B', "B only", "Only side B (blue) is simulated; side A, hatched on the pattern, follows it as a "
                "second layer, its weight and bending added to side B", 2)])
    bpy.types.Scene.ac9_iline_elastic = bpy.props.FloatProperty(
        name="Elastic", translation_context=CTX, min=30.0, max=100.0, precision=0, subtype='PERCENTAGE', get=_el_get, set=_el_set,
        options=set(), description="Rest length along the picked internal line (10 mm each side of it), as a share "
                                   "of the pattern's (75 = shortened to 75 %; 100 = not elastic)")
    for hn in _HINT_HANDLERS:
        lst = getattr(bpy.app.handlers, hn)
        for f in [f for f in lst if getattr(f, '__name__', '') == '_drop_hints']:
            lst.remove(f)
        lst.append(_drop_hints)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_fold_sides, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_px, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    for hn in _HINT_HANDLERS:
        lst = getattr(bpy.app.handlers, hn)
        for f in [f for f in lst if getattr(f, '__name__', '') == '_drop_hints']:
            lst.remove(f)
    _state.update(hover=None, drag=None)
    _isel.update(piece=None, line=None)
    _imore.clear()
    _csel.clear()
    _esel.clear()
    for k in ('ac9_iline_fold', 'ac9_iline_fold_angle', 'ac9_iline_fold_dir', 'ac9_iline_fold_side',
              'ac9_iline_fold_layers', 'ac9_iline_elastic', 'ac9_iline_end_taper'):
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
    global _icons
    if _icons is not None:
        # bound as its own name: "import bpy.utils.previews" here made bpy local to the whole function, and the
        # bpy.types.Scene above raised UnboundLocalError (every unregister, F3 reload included, failed, 2026-10-25)
        import bpy.utils.previews as previews
        previews.remove(_icons)
        _icons = None
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
