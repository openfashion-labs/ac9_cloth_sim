# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the saved mesh's own piece/layout correspondence, independently of current 2D edits."""
import json
import numpy as np


def membership(g):
    """Validated face ownership; shared or loose vertices cannot safely be masked per piece."""
    if g is None or g.type != 'MESH':
        return None
    me = g.data
    try:
        names = json.loads(g.get('ac9_piece_names', '[]'))
        attr = me.attributes.get('ac9_piece')
        if (not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names)
                or len(set(names)) != len(names) or attr is None or attr.domain != 'FACE'
                or attr.data_type != 'INT' or len(attr.data) != len(me.polygons) or not len(me.polygons)):
            return None
        fp = np.empty(len(me.polygons), np.int32)
        attr.data.foreach_get('value', fp)
        if (fp < 0).any() or (fp >= len(names)).any():
            return None
        sizes = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get('loop_total', sizes)
        loops = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get('vertex_index', loops)
        low, high = np.full(len(me.vertices), len(names), np.int32), np.full(len(me.vertices), -1, np.int32)
        np.minimum.at(low, loops, np.repeat(fp, sizes))
        np.maximum.at(high, loops, np.repeat(fp, sizes))
        if (low != high).any() or (high < 0).any():
            return None
        return {'names': names, 'fp': fp, 'loops': loops, 'sizes': sizes, 'vertex_piece': high}
    except (ValueError, TypeError, AttributeError):
        return None


def material_index(g):
    """Triangle/range/flat data from one mesh. No job or current pattern is used as evidence."""
    result = membership(g)
    if result is None or (result['sizes'] != 3).any() or (np.diff(result['vertex_piece']) < 0).any():
        return None
    me = g.data
    uv_map = me.uv_layers.get('UVMap')
    if uv_map is None and len(me.uv_layers) == 1:
        uv_map = me.uv_layers[0]
    if uv_map is None:
        return None
    uv = np.empty(len(me.loops) * 2, np.float32)
    uv_map.data.foreach_get('uv', uv)
    points = uv.reshape(-1, 2).astype(np.float64) * 1000.0
    if not np.isfinite(points).all():
        return None
    flat = np.zeros((len(me.vertices), 2))
    flat[result['loops']] = points
    if np.max(np.abs(flat[result['loops']] - points)) > .001:
        return None
    offs = np.searchsorted(result['vertex_piece'], np.arange(len(result['names'])))
    if (np.diff(list(offs) + [len(flat)]) == 0).any():
        return None
    return dict(result, flat=flat, offs=offs, F=result['loops'].reshape(-1, 3))
