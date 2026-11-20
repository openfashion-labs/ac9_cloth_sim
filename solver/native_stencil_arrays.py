# SPDX-License-Identifier: GPL-3.0-or-later
"""Host structural rows. No material, device, or solver state is changed."""
import numpy as np


def independent_rows(elements, active, followers=(), supports=()):
    """Expand supports, filter inactive nodes and return distinct sorted IDs per row.

    Padding is -1, at the right. Repeated support IDs count only once.
    """
    active = np.asarray(active, dtype=bool)
    count = len(active)
    rows = np.asarray(elements)
    if rows.ndim != 2 or not np.issubdtype(rows.dtype, np.integer):
        raise ValueError('Expected integer structural rows')
    if np.any(rows < -1) or np.any(rows >= count):
        raise ValueError('Invalid structural point index')
    followers = np.asarray(followers, dtype=np.int32)
    supports = np.asarray(supports, dtype=np.int32).reshape(-1, 3)
    if (followers.ndim != 1 or len(followers) != len(supports)
            or len(np.unique(followers)) != len(followers)
            or np.any(followers < 0) or np.any(followers >= count)
            or np.any(supports < 0) or np.any(supports >= count)):
        raise ValueError('Invalid dependent point supports')
    # One-level substitution matches the original set expansion (not recursive).
    if len(followers):
        lookup = np.full((count + 1, 3), count, np.int32)
        lookup[:count, 0] = np.arange(count)
        lookup[followers] = supports
        nodes = lookup[np.where(rows >= 0, rows, count)].reshape(len(rows), rows.shape[1] * 3)
    else:
        nodes = np.where(rows >= 0, rows, count).astype(np.int32)
    mask = np.r_[active, False]
    nodes[~mask[nodes]] = count
    nodes.sort(axis=1)
    if nodes.shape[1] > 1:
        duplicate = nodes[:, 1:] == nodes[:, :-1]
        nodes[:, 1:][duplicate] = count
        nodes.sort(axis=1)
    nodes[nodes == count] = -1
    return nodes


def model_stencils(model, bound, active, *, unique=False):
    followers = bound.vertices.numpy() if bound is not None else np.empty(0, np.int32)
    supports = bound.triangles.numpy() if bound is not None else np.empty((0, 3), np.int32)
    rows = []
    excluded = {}
    for name, width in (('tri_indices', 3), ('edge_indices', 4), ('spring_indices', 2)):
        source = getattr(model, name).numpy().reshape(-1, width)
        if name == 'spring_indices':
            source = source[:model.spring_count]
        else:
            excluded[name] = np.isin(source, followers).any(axis=1)
            source = source[~excluded[name]]
        rows.append(independent_rows(source, active, followers, supports))
    if getattr(model, 'tet_count', 0):
        rows.append(independent_rows(model.tet_indices.numpy(), active, followers, supports))
    width = max((r.shape[1] for r in rows), default=0)
    result = np.concatenate([np.pad(r, ((0, 0), (0, width-r.shape[1])), constant_values=-1) for r in rows])
    if unique:
        result = result[(result >= 0).sum(axis=1) > 1]
        result = np.unique(result, axis=0)
    return result, excluded
