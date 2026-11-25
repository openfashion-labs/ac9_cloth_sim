# SPDX-License-Identifier: GPL-3.0-or-later
"""Box select of pieces in the 3D view: the placed pieces and the pieces of the dressed garment inside a region
rectangle, picked one piece at a time (user 2026-11-17, DECISIONS cloth-sim-place-click-drag-box-2026-11-17). The
dressed garment is one object, so Blender's own Select Box picks the whole garment; the add-on's box picks its pieces.
Blender's Select Box is left as it is.

Used by Whole Pieces' drag from nothing in the 3D view (draft_tool), with Blender's box keys (Shift: add, Ctrl:
take out). The Place tool has no box (user 2026-11-17: it places one piece at a time)."""
import json

import numpy as np

import gpu
from gpu_extras.batch import batch_for_shader

from . import placed_view as PLV
from . import selection as SEL
from . import store

BOX_PX = 4                  # a box smaller than this (width + height, px) is a click


def _region_xy(context, W):
    """World points W (n, 3, meters) -> region pixels (n, 2) and the mask of those in front of the view."""
    rv3d, region = context.region_data, context.region
    if rv3d is None or region is None or not len(W):
        return np.zeros((0, 2)), np.zeros(0, bool)
    M = np.array(rv3d.perspective_matrix, float)
    H = np.c_[W, np.ones(len(W))] @ M.T
    w = H[:, 3]
    ok = w > 1e-9
    w = np.where(ok, w, 1.0)
    P = np.c_[(H[:, 0] / w * 0.5 + 0.5) * region.width, (H[:, 1] / w * 0.5 + 0.5) * region.height]
    return P, ok


def _inside(P, ok, rect):
    x0, y0, x1, y1 = rect
    return ok & (P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1)


def _world(ev, flat):
    n = len(flat) // 3
    V = np.asarray(flat, float).reshape(n, 3)
    M = np.array(ev.matrix_world, float)
    return V @ M[:3, :3].T + M[:3, 3]


def garment_names(context, rect):
    """The pieces of the dressed garment with a face center inside rect (pieces put back to their adjusted shapes by a
    Reset are not on the garment: their placed objects answer for them)."""
    from . import view_visibility as VV
    spec = store.get(context)[0]
    from . import control_basis as CB
    worn, g = CB.on_garment(context, spec)
    if g is None or not worn or not VV.object_allowed(context, g):
        return []
    ev = g.evaluated_get(context.evaluated_depsgraph_get())
    me = ev.data
    a = me.attributes.get('ac9_piece')
    try:
        names = json.loads(g.get('ac9_piece_names', '[]'))
    except ValueError:
        return []
    n = len(me.polygons)
    if a is None or a.domain != 'FACE' or not n:
        return []
    C = np.empty(n * 3, np.float32)
    me.polygons.foreach_get('center', C)
    K = np.empty(n, np.int32)
    a.data.foreach_get('value', K)
    P, ok = _region_xy(context, _world(ev, C))
    ks = np.unique(K[_inside(P, ok, rect)])
    return [names[k] for k in ks if 0 <= k < len(names) and names[k] in worn]


def placed_names(context, rect):
    """The placed pieces with a vertex inside rect (a pair shown as one object: each half on its own)."""
    from . import view_visibility as VV
    spec = store.get(context)[0]
    if spec is None:
        return []
    from . import control_basis as CB
    worn, _ = CB.on_garment(context, spec)
    dg = context.evaluated_depsgraph_get()
    out = []
    for ob in {o.as_pointer(): o for o in PLV.objects(spec).values()}.values():
        if not VV.object_allowed(context, ob):
            continue
        ev = ob.evaluated_get(dg)
        me = ev.data
        n = len(me.vertices)
        if not n:
            continue
        V = np.empty(n * 3, np.float32)
        me.vertices.foreach_get('co', V)
        P, ok = _region_xy(context, _world(ev, V))
        idx = np.nonzero(_inside(P, ok, rect))[0]
        if not idx.size:
            continue
        idx = idx % PLV.core_count(ob, n)          # the thickness's copies: the vertices they were copied from
        src, twin = len(ob.data.vertices), ob.get(PLV.TWIN_PROP)
        # the Mirror modifier puts the mirrored vertices after the source's (placed_view.placed_face_piece)
        if not twin or (idx < src).any():
            out.append(ob.get(PLV.PROP))
        if twin and (idx >= src).any():
            out.append(twin)
    return [n for n in dict.fromkeys(out) if n and n not in worn]


def world_names(context, rect):
    """The 3D pieces inside rect: placed pieces, then the dressed garment's."""
    return list(dict.fromkeys(placed_names(context, rect) + garment_names(context, rect)))


def draw_rect(rect):
    """The box being dragged, as the pattern tools draw theirs (draft_tool)."""
    (x0, y0), (x1, y1) = rect
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


def select_box(context, rect, mode):
    """Pick the pieces in the region rectangle rect (x0, y0, x1, y1): the flat pattern pieces this view shows, the
    placed pieces and the dressed garment's pieces. mode: SET / ADD / SUB. Returns the names boxed."""
    from . import draft_tool as DT
    return DT.box_transform(context, rect, mode, extra=world_names(context, rect), lines=False)
