"""Edges of the cloth passing through triangles of the cloth, on the CPU (cfg 'untangle_free').
From the 10/18 recovery bench (experiments/recover_2026-10-18, gia_untangle._crossing_edges).
  crossing_edges(X, E, Tb) -> (ei, ti): indices into E of the edges passing through a triangle of Tb, and that triangle"""
import numpy as np
from spatial import cKDTree


def crossing_edges(X, E, Tb):
    A, B, C = X[Tb[:, 0]], X[Tb[:, 1]], X[Tb[:, 2]]
    cen = (A + B + C) / 3
    rad = np.max(np.linalg.norm(np.stack([A, B, C]) - cen, axis=2), axis=0)
    mid = (X[E[:, 0]] + X[E[:, 1]]) / 2
    half = np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1) / 2
    cand = cKDTree(cen).query_ball_point(mid, half + rad.max())
    ei = np.repeat(np.arange(len(E)), [len(c) for c in cand])
    if not len(ei):
        return np.zeros(0, int), np.zeros(0, int)
    ti = np.concatenate([np.asarray(c, int) for c in cand])
    p, q = X[E[ei, 0]], X[E[ei, 1]]
    a, b, c = A[ti], B[ti], C[ti]
    n = np.cross(b - a, c - a)
    dp = ((p - a) * n).sum(1); dq = ((q - a) * n).sum(1)
    t = dp / np.where(dp - dq == 0, 1e-12, dp - dq)
    x = p + t[:, None] * (q - p)
    c1 = (np.cross(b - a, x - a) * n).sum(1); c2 = (np.cross(c - b, x - b) * n).sum(1); c3 = (np.cross(a - c, x - c) * n).sum(1)
    hit = (dp * dq < 0) & (c1 >= 0) & (c2 >= 0) & (c3 >= 0)
    return ei[hit], ti[hit]
