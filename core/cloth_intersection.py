# SPDX-License-Identifier: GPL-3.0-or-later
"""Triangle surface intersections in mm, independent of Blender and sewing rules.

The tolerance is floating-point geometry tolerance, not a clearance/quality limit.
Point/edge contact and coplanar contact are outside this transverse test;
shared topology is handled by the caller.
"""
import numpy as np


def intersection(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    scale = max(float(np.ptp(np.vstack((a, b)), axis=0).max()), 1.0)
    eps = max(1e-8, 1e-9 * scale)
    na, nb = np.cross(a[1] - a[0], a[2] - a[0]), np.cross(b[1] - b[0], b[2] - b[0])
    la, lb = np.linalg.norm(na), np.linalg.norm(nb)
    if la <= eps * eps or lb <= eps * eps:
        return None
    na, nb = na / la, nb / lb
    da, db = (a - b[0]) @ nb, (b - a[0]) @ na
    if (da.min() > eps or da.max() < -eps or db.min() > eps or db.max() < -eps):
        return None
    if np.max(np.abs(da)) <= eps and np.max(np.abs(db)) <= eps:
        return None
    # A face touching the other plane only along an edge is not a transverse
    # intersection through both face interiors.
    if not (da.min() < -eps and da.max() > eps and db.min() < -eps and db.max() > eps):
        return None
    axis = np.cross(na, nb)
    length = np.linalg.norm(axis)
    if length <= 1e-12:
        return None
    axis /= length

    def cut(t, distances):
        points = [p for p, d in zip(t, distances) if abs(d) <= eps]
        for i in range(3):
            j = (i + 1) % 3
            if distances[i] * distances[j] < 0:
                points.append(t[i] + (t[j] - t[i]) * distances[i] / (distances[i] - distances[j]))
        if len(points) < 2:
            return None
        points = np.asarray(points)
        values = points @ axis
        return float(values.min()), float(values.max()), points[0]

    ca, cb = cut(a, da), cut(b, db)
    if ca is None or cb is None:
        return None
    lo, hi = max(ca[0], cb[0]), min(ca[1], cb[1])
    if hi - lo <= eps:
        return None
    p = ca[2]
    return 'cross', np.asarray([p + axis * (s - np.dot(p, axis)) for s in (lo, hi)])


def direct_sewn(ta, tb, links):
    """Only a known edge directly joining these two faces exempts a face pair."""
    return bool(links) and any((int(a), int(b)) in links for a in ta for b in tb)
