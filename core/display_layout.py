# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only coordinates between an authored pattern and a displayed material mesh.

Edge identity precedes geometric coincidence: a symmetric outline can turn in
place without changing its occupied region. No cloth positions are changed.
"""
import numpy as np
from . import spec as S


def side_points(spec, side, outlines, count):
    """Sample the directed boundary itself, without cutting across its corners."""
    if 'line' in side:
        return S.resample(S.side_polyline(spec, side, outlines, step=2.), count)
    ol = outlines[side['piece']]
    return ol.at(ol.s_of(*side['from']) + np.linspace(0., ol.span(side), count))


class LayoutMap:
    def __init__(self, current, saved, tolerance=0.001):
        self.current, self.saved = S.Outline(current), S.Outline(saved)
        self.tolerance = tolerance
        self.rigid = None
        a, b = self.current, self.saved
        if a.order != b.order or len(set(a.order)) != len(a.order):
            return
        new, old = [], []
        for eid in a.order:
            na, la, pa = a.edges[eid]
            nb, lb, pb = b.edges[eid]
            if min(la, lb) <= 1e-9:
                return
            def fractions(p, length):
                return np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))] / length
            t = np.unique(np.r_[fractions(pa, la), fractions(pb, lb)])
            new.append(a.at(na + t * la))
            old.append(b.at(nb + t * lb))
        new, old = np.vstack(new), np.vstack(old)
        cn, co = new.mean(0), old.mean(0)
        if np.linalg.matrix_rank(new-cn) < 2 or np.linalg.matrix_rank(old-co) < 2:
            return
        u, _, vt = np.linalg.svd((new-cn).T @ (old-co))
        r = u @ vt
        if np.linalg.det(r) < 0:
            u[:, -1] *= -1
            r = u @ vt
        shift = co-cn @ r
        if np.linalg.norm(new @ r + shift-old, axis=1).max() <= tolerance:
            hn = {it['id']: it for it in current.get('internals', []) if it.get('hole')}
            ho = {it['id']: it for it in saved.get('internals', []) if it.get('hole')}
            if hn.keys() != ho.keys():
                return
            for lid in hn:
                p, q = np.asarray(hn[lid]['pts'], float), np.asarray(ho[lid]['pts'], float)
                count = max(len(p), len(q), 32)
                if np.linalg.norm(S.resample(p, count) @ r + shift-S.resample(q, count), axis=1).max() > tolerance:
                    return
            self.rigid = r, shift

    def points(self, points):
        """Current queries in saved material coordinates; None without evidence.

        A nonrigid edit only has a proven map on the same directed authored
        edges. Interior locations require an explicit map, never a nearest
        triangle in an unrelated coordinate frame.
        """
        q = np.asarray(points, float)
        if q.ndim != 2 or q.shape[1] != 2 or not np.isfinite(q).all():
            return None
        if self.rigid is not None:
            r, shift = self.rigid
            return q @ r + shift
        a, b = self.current, self.saved
        if a.order != b.order:
            return None
        result, distance = np.zeros_like(q), np.full(len(q), np.inf)
        for eid in a.order:
            s0, length, p = a.edges[eid]
            if eid not in b.edges or length <= 1e-9:
                return None
            cum = np.r_[0., np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
            for i, (v, w) in enumerate(zip(p[:-1], p[1:])):
                d = w-v
                t = np.clip((q-v) @ d / max(float(d@d), 1e-18), 0., 1.)
                dist = np.linalg.norm(q-(v+t[:, None]*d), axis=1)
                better = dist < distance
                f = (cum[i]+t*np.linalg.norm(d))/length
                bs, bl, _ = b.edges[eid]
                result[better] = b.at(bs+f[better]*bl)
                distance[better] = dist[better]
        return result if (distance <= self.tolerance).all() else None
