# SPDX-License-Identifier: GPL-3.0-or-later
"""Metric orthographic references. Avatar convention: Z up, facing -Y.

All coordinates are metres. Nothing in this module edits a source or a layout.
"""
import numpy as np

DIRECTIONS = ('RIGHT', 'FRONT', 'LEFT')
GAP = 0.15


def frame(points):
    """Shared horizontal centre / floor for an avatar (or the source union)."""
    p = np.asarray(points, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not len(p) or not np.isfinite(p).all():
        raise ValueError('The reference needs finite 3D vertices')
    lo, hi = p.min(0), p.max(0)
    return np.array(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))


def project(points, origin, direction):
    p = np.asarray(points, dtype=float) - np.asarray(origin)
    if direction == 'FRONT':
        x = p[:, 0]
    elif direction == 'RIGHT':
        x = -p[:, 1]       # the avatar's right, seen from -X
    elif direction == 'LEFT':
        x = p[:, 1]
    else:
        raise ValueError(direction)
    return np.column_stack((x, p[:, 2]))


def arrangement(points, origin):
    """Fixed, non-overlapping columns for ALL registered sources, even when off."""
    f = project(points, origin, 'FRONT')
    s = project(points, origin, 'LEFT')
    # Centre each column on the same body frame, preserving accessory offsets.
    fw = float(np.abs(f[:, 0]).max())
    sw = float(np.abs(s[:, 0]).max())
    step = fw + sw + GAP
    return {'RIGHT': -step, 'FRONT': 0.0, 'LEFT': step}


def quad(bounds, offset=(0.0, 0.0), column=0.0, z=-0.001):
    lo, hi = np.asarray(bounds, dtype=float)
    x, y = offset
    return [(lo[0] + x + column, lo[1] + y, z),
            (hi[0] + x + column, lo[1] + y, z),
            (hi[0] + x + column, hi[1] + y, z),
            (lo[0] + x + column, hi[1] + y, z)]
