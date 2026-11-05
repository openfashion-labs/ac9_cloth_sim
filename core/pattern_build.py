# SPDX-License-Identifier: GPL-3.0-or-later
"""Garment spec -> the sewing pattern the solver takes (numpy only, no bpy).

Per enabled piece:
  boundary  the outline resampled at <= h, with every corner (edge joint) and every seam sample as a vertex;
            seam samples closer than MERGE * h along the outline are one vertex (a corner wins)
  internal  internal lines that are seam sides become constrained polylines inside the piece; every internal line
            (not base lines, not shorter than 2 h, not lying on the outline) is a fold line, sewn or not, also
            constrained, tagged per line in Lf (so a solver can be told to fold there)
  interior  rows laid off the outline and the constraint lines (a strip up to 6 h wide divided evenly, the middle of a
            narrow one gets a row), the hex grid at spacing h between, holes filled, then bubble packing of these
            points only; all kept >= 0.5 h away from the outline and the internal polylines
Seams: both sides get the same number of samples, equally spaced in their own arc length from their "from"
ends; sample k of side a is stitched to sample k of side b. Crossing internal lines get a shared vertex.

Output: a dict with the keys of the experiment's pattern npz (names, nb, V{i}, C{i}, Lf{i}, fold_meta,
stitch, S, h, fabrics), so the triangulation / assembly / solver steps read it unchanged.
"""
import collections
import hashlib
import json
import os

import numpy as np

from . import spec as S
from . import walk as W

MERGE = 0.35
LINE_GAP = 0.05       # mm: an internal line's sample closer than this to the outline is on it (dropped); farther, it
                      # goes into the mesh however near -- a line parallel to the outline 1 mm in (user 2026-10-28;
                      # before, samples within 0.5 h were dropped and such a line never reached the mesh)
FOLD_ROW = 0.4       # x h: the rows beside a fold line (spec fold_rows), clear of MERGE so not merged into it

# The interior mesher's version: 1 the hex grid trimmed to the piece (to 2026-10-23), 2 rows off the edges and bubble
# packing, 3 a sewn internal line is a fold line too (its stretches not sewn get samples, 2026-10-25), 4 an internal line
# near the outline kept where it runs along it (2026-10-28), 5 an internal line sampled span by span between its
# corners, a corner a vertex (a rectangle drawn inside had no vertex at its corners, 4.6 mm off at 10 mm; user
# 2026-10-28). Part of the interior cache's key and written to a job (job.json mesh 'mesher'); nothing branches on it.
# Raise it whenever _interior (or what it calls) makes different points: else the cache on disk returns the old ones
MESHER_VERSION = 5
CORNER_DEG = 20.0     # an internal line turning more than this at a point: a corner there (sampled up to it)

# The interior points of a piece are a function of its outline vertices, its internal line vertices, its constraint
# segments, its sewn internal polylines, h and the version alone (section 4 reads nothing else), so they are kept by
# a hash of these: a pattern edit remakes the pieces it changed (and their seam partners, whose outline samples
# move), a Simulate after it reads them all (Fable review 2026-10-23 mesher_lifecycle). In memory, and as one small
# npz per key in CACHE_DIR when the add-on sets it (the first Simulate after opening Blender reads them too)
CACHE_DIR = None
_INTERIOR = collections.OrderedDict()
_INTERIOR_MAX = 512


# ---------------------------------------------------------------- 2D geometry (numpy)

def points_in_polygon(Q, P):
    """Even-odd test of points Q (n, 2) against the closed polygon P (m, 2)."""
    Q = np.asarray(Q, float)
    A, B = P, np.roll(P, -1, 0)
    inside = np.zeros(len(Q), bool)
    for k in range(0, len(Q), 4096):
        q = Q[k:k + 4096]
        ya, yb = A[None, :, 1], B[None, :, 1]
        cond = (ya > q[:, None, 1]) != (yb > q[:, None, 1])
        with np.errstate(divide='ignore', invalid='ignore'):
            xint = A[None, :, 0] + (q[:, None, 1] - ya) * (B[None, :, 0] - A[None, :, 0]) / (yb - ya)
        inside[k:k + 4096] = (cond & (q[:, None, 0] < xint)).sum(1) % 2 == 1
    return inside


def dist_to_segments(Q, A, B):
    """Distance of each point Q to the nearest of the segments A[j]-B[j]."""
    Q = np.asarray(Q, float)
    out = np.empty(len(Q))
    D = B - A
    ll = np.maximum((D * D).sum(1), 1e-12)
    for k in range(0, len(Q), 2048):
        q = Q[k:k + 2048]
        t = np.clip(((q[:, None, :] - A[None]) * D[None]).sum(-1) / ll[None], 0, 1)
        X = A[None] + t[..., None] * D[None]
        out[k:k + 2048] = np.sqrt(((X - q[:, None, :]) ** 2).sum(-1)).min(1)
    return out


def dist_near(Q, A, B, rmax):
    return seg_index(A, B, rmax)(Q)


def seg_index(A, B, rmax):
    """dist_to_segments capped at rmax (a point farther than rmax from every segment gets rmax), through a grid of
    cells of size rmax: only the segments in the 9 cells around a point are measured. For the many points of the
    interior mesher, where the brute force was most of the time (a T-shirt at 6 mm: 16 s, 2026-10-23)."""
    A, B = np.asarray(A, float).reshape(-1, 2), np.asarray(B, float).reshape(-1, 2)
    c = float(rmax)
    if len(A) == 0:
        return lambda Q: np.full(len(Q), c)
    o0 = np.minimum(A.min(0), B.min(0))
    lo = np.floor((np.minimum(A, B) - o0) / c).astype(np.int64)
    hi = np.floor((np.maximum(A, B) - o0) / c).astype(np.int64)
    K = int(max(hi[:, 1].max(), 0)) + 4
    segs, keys = [], []
    span = (hi - lo + 1)
    for dx in range(int(span[:, 0].max())):
        for dy in range(int(span[:, 1].max())):
            m = (dx < span[:, 0]) & (dy < span[:, 1])
            j = np.nonzero(m)[0]
            segs.append(j)
            keys.append((lo[j, 0] + dx + 1) * K + lo[j, 1] + dy + 1)
    segs, keys = np.concatenate(segs), np.concatenate(keys)
    o = np.argsort(keys, kind='stable')
    segs, keys = segs[o], keys[o]
    D = B - A
    ll = np.maximum((D * D).sum(1), 1e-12)

    def query(Q):
        Q = np.asarray(Q, float).reshape(-1, 2)
        out = np.full(len(Q), c)
        if len(Q):
            _query(Q, out)
        return out

    def _query(Q, out):
        qc = np.floor((Q - o0) / c).astype(np.int64) + 1
        inb = (qc[:, 1] >= 0) & (qc[:, 1] <= K - 2) & (qc[:, 0] >= 0)
        ar = np.nonzero(inb)[0]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                q = qc[ar, 0] * K + qc[ar, 1] + dx * K + dy
                l_ = np.searchsorted(keys, q, 'left')
                n = np.searchsorted(keys, q, 'right') - l_
                tot = int(n.sum())
                if not tot:
                    continue
                ii = np.repeat(ar, n)
                jj = segs[np.arange(tot) - np.repeat(np.cumsum(n) - n, n) + np.repeat(l_, n)]
                t = np.clip(((Q[ii] - A[jj]) * D[jj]).sum(1) / ll[jj], 0, 1)
                d = np.linalg.norm(A[jj] + t[:, None] * D[jj] - Q[ii], axis=1)
                np.minimum.at(out, ii, d)
    return query


def dist_to_polyline(Q, P):
    P = np.asarray(P, float)
    return dist_to_segments(Q, P[:-1], P[1:])


def dist_to_ring(Q, P):
    return dist_to_segments(Q, P, np.roll(P, -1, 0))


def along_ring(Q, D, P, cos_min=0.9):
    """Per point of Q (direction D): it runs along the ring P's nearest segment (|cos| > cos_min), not across it."""
    A, B = np.asarray(P, float), np.roll(np.asarray(P, float), -1, 0)
    if not len(Q):
        return np.zeros(0, bool)
    dd = B - A
    ll = np.maximum((dd * dd).sum(1), 1e-12)
    t = np.clip(((Q[:, None, :] - A[None]) * dd[None]).sum(2) / ll[None], 0, 1)
    di = np.linalg.norm(A[None] + t[..., None] * dd[None] - Q[:, None, :], axis=2)
    T = dd[di.argmin(1)]
    c = np.abs((T * D).sum(1)) / np.maximum(np.linalg.norm(T, axis=1) * np.linalg.norm(D, axis=1), 1e-12)
    return c > cos_min


def seg_meets_ring(a, b, P, tol):
    """Segment a-b crosses the ring P, or passes within tol of one of its vertices."""
    P = np.asarray(P, float)
    a, b = np.asarray(a, float), np.asarray(b, float)
    # an outline vertex that is the segment's own end is not passed by it
    away = (np.linalg.norm(P - a, axis=1) > 1e-6) & (np.linalg.norm(P - b, axis=1) > 1e-6)
    if (dist_to_segments(P[away], a[None], b[None]) < tol).any():
        return True
    Q = np.roll(P, -1, 0)

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
    d1, d2 = orient(a, b, P), orient(a, b, Q)
    d3, d4 = orient(P, Q, a), orient(P, Q, b)
    return bool(((d1 * d2 < 0) & (d3 * d4 < 0)).any())


def _unsewn(sewn):
    """[(t0, t1, starts on a seam, ends on a seam)] the stretches of a line (shares of its length) not in the sewn
    ones [(t0, t1)]."""
    out, t = [], 0.0
    for a, b in sorted(sewn):
        if a > t + 1e-9:
            out.append((t, a, t > 0.0, True))
        t = max(t, b)
    if t < 1.0 - 1e-9:
        out.append((t, 1.0, t > 0.0, False))
    return out


def _resample_corners(P, h):
    """Polyline P sampled about every h, each span between its corners (where it turns more than CORNER_DEG, its ends)
    on its own: the corners are samples. S.resample spaced them along the whole line and cut a rectangle's corners."""
    P = np.asarray(P, float)
    if len(P) > 1:      # repeated points (also a closed line's end doubled): a 0-long edge would read as a corner
        P = P[np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-6]]
    if len(P) < 3:
        return S.resample(P, max(2, int(round(S.polyline_len(P) / h)) + 1))
    a, b = P[1:-1] - P[:-2], P[2:] - P[1:-1]
    c = (a * b).sum(1) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-12)
    cut = [0] + [k + 1 for k in np.nonzero(c < np.cos(np.radians(CORNER_DEG)))[0]] + [len(P) - 1]
    out = []
    for i, j in zip(cut[:-1], cut[1:]):
        Q = P[i:j + 1]
        r = S.resample(Q, max(2, int(round(S.polyline_len(Q) / h)) + 1))
        out.append(r if not out else r[1:])
    return np.vstack(out)


def _sub_polyline(P, t0, t1, ja, jb):
    """(the stretch of polyline P from share t0 to t1 of its length, its corners kept, ja, jb)."""
    P = np.asarray(P, float)
    cum = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    a, b = t0 * cum[-1], t1 * cum[-1]
    s = np.r_[a, cum[(cum > a) & (cum < b)], b]
    return np.stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])], 1), ja, jb


ROW =np.sqrt(3) / 2   # x h: a row of the interior off its edge, the height of an equilateral triangle of side h
ROW_CAP = 3.0          # x h: a strip wider than 2 x this is wide (one row off each edge, the hex grid between)
ROW_GAP = 0.7          # x h: the least distance between two interior points (rows, grid) when they are laid
RELAX_ITERS = 40       # bubble packing steps of the laid points
RELAX_DT = 0.5         # x h per unit force, a step capped at 0.25 h
FILL_GAP = 0.8         # x h: the same for the points that fill the holes left where the rows meet the grid


def _edge_rows(Vb, SA, SB, inward, h):
    """Rows of points laid off the edges of a piece (the reference drape's mesh follows its outline and internal lines,
    ours had a fixed grid whose gap to the outline swung 10-30 mm at 20 mm and left one triangle across a 24 mm strip,
    2026-10-23). SA, SB: the segments the rows are laid off (outline, then constraint lines); inward[j]: the outline's
    inward side (+1/-1 times the left normal), 0 for a line (both sides). From the middle and the ends of each segment
    a ray along its normal finds the half width m of the strip there (where the nearest edge stops being this one).
    A strip up to 2 ROW_CAP h wide is divided evenly: n = ceil(w / h) - 1 inner rows at w j / (n + 1), each edge lays
    the rows of its own half (the middle row by both). A wider one gets one row at ROW h, the grid fills the rest.
    Odd rows start from the segment middles, even ones from the ends, so neighboring rows are staggered.
    Returns the candidates (row order, then edge order) with their row number."""
    D = SB - SA
    L = np.maximum(np.linalg.norm(D, axis=1), 1e-12)
    N = np.c_[-D[:, 1], D[:, 0]] / L[:, None]
    src, nrm, mid = [], [], []
    for j in range(len(SA)):
        for s in ((1.0,) if inward[j] else (1.0, -1.0)):
            n_ = N[j] * (inward[j] if inward[j] else s)
            for t in (0.5, 0.0, 1.0):
                src.append(SA[j] + t * D[j])
                nrm.append(n_)
                mid.append(t == 0.5)
    if not src:
        return np.zeros((0, 2)), np.zeros(0, int)
    src, nrm, mid = np.asarray(src), np.asarray(nrm), np.asarray(mid)
    # half width: march along the normal until the nearest edge is closer than the distance walked, then bisect
    cap = ROW_CAP * h
    near = seg_index(SA, SB, cap + h)
    m = np.full(len(src), cap)
    open_ = np.ones(len(src), bool)
    step = 0.25 * h
    prev = np.zeros(len(src))
    hit_at = np.full(len(src), np.nan)
    for k in range(1, int(ROW_CAP / 0.25) + 1):
        if not open_.any():
            break
        x = k * step
        ii = np.nonzero(open_)[0]
        d = near(src[ii] + x * nrm[ii])
        hit = d < 0.999 * x
        hit_at[ii[hit]] = x
        open_[ii[hit]] = False
        prev[ii[~hit]] = x
    hi_ = np.nonzero(~np.isnan(hit_at))[0]
    lo, hi = prev[hi_].copy(), hit_at[hi_].copy()
    for _ in range(8):
        c = 0.5 * (lo + hi)
        bad = near(src[hi_] + c[:, None] * nrm[hi_]) < 0.999 * c
        hi = np.where(bad, c, hi)
        lo = np.where(bad, lo, c)
    m[hi_] = lo
    pts, row = [], []
    for i_ in range(len(src)):
        if m[i_] >= ROW_CAP * h - 1e-9:
            offs = [ROW * h]
        else:
            w = 2.0 * m[i_]
            n = int(np.ceil(w / h - 1e-9)) - 1
            offs = [w * j / (n + 1) for j in range(1, n + 1) if w * j / (n + 1) <= m[i_] + 1e-6]
        for k, o in enumerate(offs, 1):
            if (k % 2 == 1) == bool(mid[i_]):
                pts.append(src[i_] + o * nrm[i_])
                row.append(k)
    if not pts:
        return np.zeros((0, 2)), np.zeros(0, int)
    pts, row = np.asarray(pts), np.asarray(row)
    o = np.argsort(row, kind='stable')
    return pts[o], row[o]


def _lay(cand, fixed, gap, gap_fixed=None):
    """Greedy: keep a candidate (in the given order) when it is at least gap from the kept ones and gap_fixed (default
    gap) from the fixed points. The middle row of a strip 1-2 h wide is 0.5-1 h from its edges' vertices."""
    gf = gap if gap_fixed is None else gap_fixed
    out = []
    cell = {}
    for q in fixed:
        cell.setdefault((int(np.floor(q[0] / gap)), int(np.floor(q[1] / gap))), []).append((q, gf * gf))
    g2 = gap * gap
    for q in cand:
        cx, cy = int(np.floor(q[0] / gap)), int(np.floor(q[1] / gap))
        ok = True
        for ax in (cx - 1, cx, cx + 1):
            for ay in (cy - 1, cy, cy + 1):
                for r, lim in cell.get((ax, ay), ()):
                    if (r[0] - q[0]) ** 2 + (r[1] - q[1]) ** 2 < lim:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            out.append(q)
            cell.setdefault((cx, cy), []).append((q, g2))
    return np.asarray(out).reshape(-1, 2)


def _pairs(P, r):
    """Index pairs (i < j) of the points P closer than r (cells of size r, the 9 around each point)."""
    if len(P) < 2:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    c = np.floor(P / r).astype(np.int64)
    c -= c.min(0)
    K = int(c[:, 1].max()) + 3
    key = c[:, 0] * K + c[:, 1]
    o = np.argsort(key, kind='stable')
    ks = key[o]
    ar = np.arange(len(P))
    I, J = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            q = key + dx * K + dy
            lo = np.searchsorted(ks, q, 'left')
            n = np.searchsorted(ks, q, 'right') - lo
            tot = int(n.sum())
            if not tot:
                continue
            ii = np.repeat(ar, n)
            jj = o[np.arange(tot) - np.repeat(np.cumsum(n) - n, n) + np.repeat(lo, n)]
            m = ii < jj
            ii, jj = ii[m], jj[m]
            m = ((P[ii] - P[jj]) ** 2).sum(1) < r * r
            I.append(ii[m]); J.append(jj[m])
    return (np.concatenate(I), np.concatenate(J)) if I else (np.zeros(0, np.int64), np.zeros(0, np.int64))


def _relax(Vfix, Vg, h, feat, iters=RELAX_ITERS, dt=RELAX_DT):
    """Bubble packing of the laid points only (Shimada's force between two points d apart, r = d / h:
    1.25 r^3 - 2.375 r^2 + 1.125, pushing below h and pulling up to 1.5 h): the band between the rows off the edges
    and the hex grid evens out to about h. The outline and line vertices push but do not move; a move that leaves
    the piece is undone: feat = (A, B), the segments of the outline and the lines; a point may not come closer to
    them than 0.5 h (or than it already was). A step is at most 0.25 h, so a point cannot jump across an edge; only the
    points that were within 2.5 h at the last measure (every 8 steps) are measured."""
    if len(Vg) == 0 or iters <= 0:
        return Vg
    nf = len(Vfix)
    I = J = None
    feat_q = seg_index(feat[0], feat[1], 3.0 * h)
    near_q = seg_index(feat[0], feat[1], 0.5 * h)
    for it in range(iters):
        P = np.vstack([Vfix, Vg])
        if it % 8 == 0:
            I, J = _pairs(P, 2.0 * h)
            Dm = feat_q(Vg)
        dv = P[I] - P[J]
        d = np.maximum(np.linalg.norm(dv, axis=1), 1e-9)
        r = d / h
        f = np.where(r < 1.5, 1.25 * r ** 3 - 2.375 * r ** 2 + 1.125, 0.0)
        u = dv / d[:, None] * f[:, None]
        F = np.zeros_like(P)
        np.add.at(F, I, u)
        np.add.at(F, J, -u)
        step = dt * h * F[nf:]
        sn = np.linalg.norm(step, axis=1)
        step *= (np.minimum(sn, 0.25 * h) / np.maximum(sn, 1e-12))[:, None]
        new = Vg + step
        risky = np.nonzero(Dm - 0.25 * h * (it % 8 + 1) < 0.55 * h)[0]
        good = np.ones(len(Vg), bool)
        if len(risky):
            good[risky] = near_q(new[risky]) >= np.minimum(0.5 * h, near_q(Vg[risky])) - 1e-9
        Vg = np.where(good[:, None], new, Vg)
    return Vg


def _fix_crossings(V, C, Lf, snap=0.0):
    """Internal constraint edges that cross: add the crossing as a shared vertex and split both edges. A crossing
    closer than snap to an end of either edge uses that end instead (a new vertex there made a sliver triangle)."""
    C = [tuple(map(int, e)) for e in C]
    added = 0
    changed = True
    while changed and C:
        changed = False
        A = V[[a for a, b in C]]
        B = V[[b for a, b in C]]
        for k in range(len(C)):
            p, r = A[k], B[k] - A[k]
            s = B - A
            den = r[0] * s[:, 1] - r[1] * s[:, 0]
            with np.errstate(divide='ignore', invalid='ignore'):
                t = ((A[:, 0] - p[0]) * s[:, 1] - (A[:, 1] - p[1]) * s[:, 0]) / den
                u = ((A[:, 0] - p[0]) * r[1] - (A[:, 1] - p[1]) * r[0]) / den
            share = np.array([len({C[k][0], C[k][1]} & {a, b}) > 0 for a, b in C])
            hit = (np.abs(den) > 1e-12) & (t > 1e-6) & (t < 1 - 1e-6) & (u > 1e-6) & (u < 1 - 1e-6) & ~share
            js = np.nonzero(hit)[0]
            if len(js):
                j = int(js[0])
                x = p + t[j] * r
                (a1, b1), (a2, b2) = C[k], C[j]
                ends = [a1, b1, a2, b2]
                dn = [float(np.linalg.norm(V[e] - x)) for e in ends]
                if snap > 0 and min(dn) < snap:
                    n = ends[int(np.argmin(dn))]
                    C = [e for m, e in enumerate(C) if m not in (k, j)]
                    C += [e for e in ((a1, n), (n, b1), (a2, n), (n, b2)) if e[0] != e[1]]
                    added += 1
                    changed = True
                    break
                n = len(V)
                V = np.vstack([V, x])
                Lf = np.r_[Lf, Lf[C[k][0]]]
                C = [e for m, e in enumerate(C) if m not in (k, j)] + [(a1, n), (n, b1), (a2, n), (n, b2)]
                added += 1
                changed = True
                break
    return V, np.asarray(C, np.int64).reshape(-1, 2), Lf, added


# ---------------------------------------------------------------- build

def _interior(Vb, Vi_a, ca, sides, h, holes=()):
    """Section 4 of build for one piece: (interior points, (edge rows, filled)). holes: the rings cut out of the piece
    (core/spec.holes), kept clear like the outline."""
    nb = len(Vb)
    Pall = np.vstack([Vb, Vi_a])
    ring_q = seg_index(Vb, np.roll(Vb, -1, 0), h)
    side_q = [seg_index(P[:-1], P[1:], h) for P in sides]
    side_q += [seg_index(R, np.roll(R, -1, 0), h) for R in holes]
    cons_q = seg_index(Pall[ca[:, 0]], Pall[ca[:, 1]], h) if len(ca) else None

    def keep_of(Q):
        """Inside, >= 0.5 h from the outline, the sewn internal polylines, the holes' edges and the line vertices,
        >= 0.25 h from a constraint segment (a point half way between two samples 2 h apart sat on the segment: a flat
        triangle); not in a hole."""
        k_ = points_in_polygon(Q, Vb) & (ring_q(Q) >= 0.5 * h)
        if len(holes) and k_.any():
            k_[k_] &= ~S.in_holes(Q[k_], holes)
        for q_ in side_q:
            k_ &= q_(Q) >= 0.5 * h
        if cons_q is not None and k_.any():
            k_[k_] &= cons_q(Q[k_]) >= 0.25 * h
        if len(Vi_a) and k_.any():
            for k in range(0, len(Q), 4096):
                sl = slice(k, k + 4096)
                d2 = ((Q[sl][:, None, :] - Vi_a[None]) ** 2).sum(-1).min(1)
                k_[sl] &= d2 >= (0.5 * h) ** 2
        return k_
    area = 0.5 * float(np.sum(Vb[:, 0] * np.roll(Vb[:, 1], -1) - np.roll(Vb[:, 0], -1) * Vb[:, 1]))
    SA = np.vstack([Vb, Pall[ca[:, 0]]])
    SB = np.vstack([np.roll(Vb, -1, 0), Pall[ca[:, 1]]])
    inward = np.r_[np.full(nb, 1.0 if area > 0 else -1.0), np.zeros(len(ca))]
    cand, _ = _edge_rows(Vb, SA, SB, inward, h)
    cand = cand[keep_of(cand)] if len(cand) else cand
    Vr = _lay(cand, Pall, ROW_GAP * h, 0.5 * h)
    lo, hi = Vb.min(0), Vb.max(0)
    ys = np.arange(lo[1] + 0.5 * h, hi[1], h * np.sqrt(3) / 2)
    rows = []
    for r, y in enumerate(ys):
        xs = np.arange(lo[0] + (0.5 * h if r % 2 else 0.0), hi[0], h)
        rows.append(np.stack([xs, np.full(len(xs), y)], 1))
    grid = np.vstack(rows) if rows else np.zeros((0, 2))
    keep = keep_of(grid)
    keep[keep] &= dist_near(grid[keep], SA, SB, h) >= ROW * h
    Vc = _lay(grid[keep], np.vstack([Pall, Vr]), ROW_GAP * h)
    # where the rows meet the grid a hole up to 2 h wide can stay: fill it from the half-spaced lattice
    ys2 = np.arange(lo[1] + 0.25 * h, hi[1], h * np.sqrt(3) / 4)
    rows2 = [np.stack([xs, np.full(len(xs), y)], 1) for r, y in enumerate(ys2)
             for xs in [np.arange(lo[0] + (0.25 * h if r % 2 else 0.0), hi[0], 0.5 * h)]]
    fine = np.vstack(rows2) if rows2 else np.zeros((0, 2))
    fine = fine[keep_of(fine)] if len(fine) else fine
    Vf = _lay(fine, np.vstack([Pall, Vr, Vc]), FILL_GAP * h)
    FA = [SA] + [P[:-1] for P in sides] + [R for R in holes]
    FB = [SB] + [P[1:] for P in sides] + [np.roll(R, -1, 0) for R in holes]
    Vg = _relax(Pall, np.vstack([Vr, Vc, Vf]), h, (np.vstack(FA), np.vstack(FB)))
    return Vg, (len(Vr), len(Vf))


def _interior_cached(Vb, Vi_a, ca, sides, h, holes=()):
    """_interior through the cache: (interior points, counts, read from the cache)."""
    hs = hashlib.sha1(f"mesher {MESHER_VERSION} h {float(h)!r} sides {len(sides)}".encode())
    if len(holes):                                   # a piece without holes keeps the key it had before them
        hs.update(f" holes {len(holes)}".encode())
    for a in [Vb, Vi_a, ca] + sides + list(holes):
        a = np.ascontiguousarray(a)
        hs.update(str(a.shape).encode())
        hs.update(a.tobytes())
    key = hs.hexdigest()
    if key in _INTERIOR:
        _INTERIOR.move_to_end(key)
        Vg, cnt = _INTERIOR[key]
        return Vg.copy(), cnt, True
    fn = os.path.join(CACHE_DIR, key[:2], key + '.npz') if CACHE_DIR else None
    if fn and os.path.exists(fn):
        try:
            z = np.load(fn)
            Vg, cnt = z['Vg'], (int(z['cnt'][0]), int(z['cnt'][1]))
            _INTERIOR[key] = (Vg, cnt)
            return Vg.copy(), cnt, True
        except (OSError, ValueError, KeyError):
            pass
    Vg, cnt = _interior(Vb, Vi_a, ca, sides, h, holes)
    _INTERIOR[key] = (Vg.copy(), cnt)
    while len(_INTERIOR) > _INTERIOR_MAX:
        _INTERIOR.popitem(last=False)
    if fn:
        try:
            os.makedirs(os.path.dirname(fn), exist_ok=True)
            tmp = fn + '.tmp.npz'
            np.savez(tmp, Vg=Vg, cnt=np.asarray(cnt, np.int64))
            os.replace(tmp, fn)
        except OSError:
            pass                                    # a cache that cannot be written is only slower
    return Vg, cnt, False


def build(spec, h=6.0):
    """Returns (pattern dict, report lines)."""
    pieces = [p for p in spec['pieces'] if p.get('enabled', True)]
    on = {p['name'] for p in pieces}
    OL = {p['name']: S.Outline(p) for p in pieces}
    INT = {p['name']: {x['id']: x for x in p.get('internals', [])} for p in pieces}
    rep = [f"{spec['name']}: {len(pieces)} pieces, h {h} mm"]
    stats = collections.Counter()
    # the mesh size per piece: the spec piece's 'mesh_mm' (the Dress panel's Piece Mesh; user 2026-10-08: a small part
    # meshed finer), else h. A mirror twin is its pair's mesh mirrored, so a pair takes the finer of the two
    H = {p['name']: float(p.get('mesh_mm') or h) for p in pieces}
    for a_, b_ in S.twins(spec):
        if a_ in H and b_ in H:
            H[a_] = H[b_] = min(H[a_], H[b_])
    if any(abs(v - h) > 1e-9 for v in H.values()):
        rep.append("  mesh per piece: " + ", ".join(f"{n} {v:g}" for n, v in H.items() if abs(v - h) > 1e-9)
                   + f" mm (the rest {h:g})")

    # ---- 1. seams sampled on both sides
    seams = []
    # a seam switched off is meshed and stitched as well (its stitches kept apart: pat 'stitch_off'), so switching it
    # on or off does not change the mesh and a Live Sim can sew it while it runs (user 2026-10-23, DECISIONS
    # cloth-sim-phase-live-local-sim-2026-10-23); it is not eased
    for sm in spec['seams']:
        if sm['a']['piece'] not in on or sm['b']['piece'] not in on:
            continue
        sides = []
        for key in ('a', 'b'):
            sd = sm[key]
            if 'line' in sd:
                P = S.side_polyline(spec, sd, OL, step=0.5)
                sides.append({'piece': sd['piece'], 'kind': 'internal', 'line': sd['line'], 'poly': P,
                              'len': S.polyline_len(P), 't': (float(sd['from']), float(sd['to']))})
            else:
                o = OL[sd['piece']]
                sides.append({'piece': sd['piece'], 'kind': 'boundary', 's0': o.s_of(*sd['from']),
                              'span': o.span(sd), 'len': abs(o.span(sd))})
        # both sides get the same samples: spaced for the finer of the two pieces. Between paired notches the seam
        # is sewn segment by segment (each segment's samples in proportion over its own length on each side, a
        # notch is a sample on both), so a notch meets its partner and the ease falls where the notches put it
        # (segment sewing, user 2026-10-13 b-D1a). No pair: one segment, the same samples as before
        hmin = min(H[x['piece']] for x in sides)
        brk = W.seam_breaks(spec, sm, OL) if all(x['kind'] == 'boundary' for x in sides) else []
        ends = [(0.0, 0.0)] + brk + [(sides[0]['len'], sides[1]['len'])]
        u = [[0.0], [0.0]]
        for (a0, b0), (a1, b1) in zip(ends[:-1], ends[1:]):
            # ceil, not round: the longer side's samples at most hmin apart. With round they came up to 1.5 hmin apart
            # and the outline (resampled at <= h below) put a vertex sewn to nothing in every such gap: 124 on a raglan
            # at 20 mm, the neck seam sawtoothed between them (2026-10-15)
            m = max(1, int(np.ceil(max(a1 - a0, b1 - b0) / hmin - 1e-9)))
            u[0] += list(np.linspace(a0, a1, m + 1)[1:])
            u[1] += list(np.linspace(b0, b1, m + 1)[1:])
        n = max(2, len(u[0]))
        if len(u[0]) < 2:
            u = [[0.0, sides[0]['len']], [0.0, sides[1]['len']]]
        for x, ux in zip(sides, u):
            x['u'] = ux if x['kind'] == 'boundary' else list(np.linspace(0.0, x['len'], n))
            if x['kind'] == 'boundary':
                x['s'] = x['s0'] + np.sign(x['span']) * np.asarray(ux)
            else:
                x['pts'] = S.resample(x['poly'], n)
        seams.append({'id': sm['id'], 'sides': sides, 'n': n, 'segments': len(brk) + 1, 'h': hmin,
                      'off': not sm.get('enabled', True)})
    n_off = sum(x['off'] for x in seams)
    rep.append(f"  seams: {len(seams) - n_off} between enabled pieces" + (f" (+{n_off} off)" if n_off else ""))

    fold_meta = []
    seam_vid = {}
    built = {}

    def make(p):
        name = p['name']
        h = H[name]
        o = OL[name]
        # ---- 2. outline vertices
        req_s = [o.edges[e['id']][0] for e in p['edges']]
        req_src = [None] * len(req_s)
        internal_sides = []
        for si, sm in enumerate(seams):
            for ks, x in enumerate(sm['sides']):
                if x['piece'] != name:
                    continue
                if x['kind'] == 'boundary':
                    for k, s in enumerate(x['s']):
                        req_s.append(float(s))
                        req_src.append((si, ks, k))
                else:
                    pts = x['pts']
                    inside = points_in_polygon(pts, o.B)
                    on_b = (dist_to_ring(pts, o.B) < 0.5 * h) | ~inside
                    # samples on (or outside) the outline are outline vertices
                    if on_b.any():
                        sb, _ = _project(o, pts[on_b])
                        for k, s in zip(np.nonzero(on_b)[0], sb):
                            req_s.append(float(s))
                            req_src.append((si, ks, int(k)))
                    internal_sides.append((si, ks, pts, ~on_b, x['line'], x['t']))
                    stats['internal seam sides'] += 1
        order = np.argsort(np.mod(req_s, o.L))
        s_sorted = np.mod(np.asarray(req_s)[order], o.L)
        src_sorted = [req_src[i] for i in order]
        clusters = []
        def near(s, c, src):
            # two seam samples merge only within MERGE x the finer of this piece's mesh and their seams' sample spacing:
            # a seam to a finer piece is sampled at that piece's spacing, and at this piece's own MERGE x h (7 mm at
            # 20 mm) its 6 mm samples chained into ONE vertex -- a whole armhole sewn to a point (sleeve at 6 mm, the
            # body at 20 mm, 2026-10-23). One mesh size for all pieces: the same as before (the seams' spacing is h)
            hh = h
            for x in [src] + c['srcs']:
                if x is not None:
                    hh = min(hh, seams[x[0]]['h'])
            if src is None or all(x is None for x in c['srcs']):
                hh = h
            return s - c['s_last'] < MERGE * hh
        for s, src in zip(s_sorted, src_sorted):
            if clusters and near(s, clusters[-1], src):
                c = clusters[-1]
            else:
                c = {'members': [], 'srcs': []}
                clusters.append(c)
            c['members'].append(s)
            c['srcs'].append(src)
            c['s_last'] = s
        if len(clusters) > 1 and (clusters[0]['members'][0] + o.L - clusters[-1]['s_last']) < MERGE * h:
            last = clusters.pop()
            clusters[0]['members'] = [m - o.L for m in last['members']] + clusters[0]['members']
            clusters[0]['srcs'] = last['srcs'] + clusters[0]['srcs']
        # a corner between two neighboring samples of the same seam side, further than MERGE h from both: it joins the
        # nearer sample (that sample moves to the corner, at most h / 2 along the seam), else it is a vertex inside the
        # seam sewn to nothing (pants at 6 mm: 2 per pair of legs, 2026-10-15)
        i = 0
        while len(clusters) > 2 and i < len(clusters):
            c = clusters[i]
            pv, nx = clusters[i - 1], clusters[(i + 1) % len(clusters)]
            nset = {sr for sr in nx['srcs'] if sr is not None}
            if all(sr is None for sr in c['srcs']) and any(
                    sr is not None and ((sr[0], sr[1], sr[2] + 1) in nset or (sr[0], sr[1], sr[2] - 1) in nset)
                    for sr in pv['srcs']):
                dp = (c['members'][0] - pv['members'][-1]) % o.L
                dn = (nx['members'][0] - c['members'][-1]) % o.L
                tgt = pv if dp <= dn else nx
                ref = tgt['members'][0]
                for m in c['members']:                      # kept on the same side of the wrap as the sample
                    tgt['members'].append(m + o.L * round((ref - m) / o.L))
                    tgt['srcs'].append(None)
                stats['corners joined to a seam sample'] += 1
                clusters.pop(i)
                continue
            i += 1
        for c in clusters:
            cm = [m for m, src in zip(c['members'], c['srcs']) if src is None]
            sm_ = [m for m, src in zip(c['members'], c['srcs']) if src is not None]
            c['s'] = cm[0] if cm else float(np.mean(sm_))
            stats['merged seam samples'] += max(0, len(sm_) - 1)
        bs, bsrc = [], []
        for i, c in enumerate(clusters):
            s0 = c['s']
            s1 = clusters[(i + 1) % len(clusters)]['s'] + (o.L if i == len(clusters) - 1 else 0)
            bs.append(s0)
            bsrc.append(c['srcs'])
            m = int(np.ceil((s1 - s0) / h - 1e-9))
            # two neighboring samples of the same seam side: no vertex between them, it would be sewn to nothing.
            # A corner takes a sample to it (within MERGE h, or the nearer one when it lies between two), so the gap
            # can be up to 2 h when both ends moved (a raglan sleeve at 20 mm: 20.5 mm, one free vertex on each sleeve
            # after the ceil fix; a shirt cuff at 6 mm: 10.0 mm between two corners, 2026-10-15)
            if m > 1 and s1 - s0 <= 2.0 * h + 1e-9:
                nxt = {sr for sr in clusters[(i + 1) % len(clusters)]['srcs'] if sr is not None}
                if any(sr is not None and ((sr[0], sr[1], sr[2] + 1) in nxt or (sr[0], sr[1], sr[2] - 1) in nxt)
                       for sr in c['srcs']):
                    m = 1
            for j in range(1, m):
                bs.append(s0 + (s1 - s0) * j / m)
                bsrc.append([])
        Vb = o.at(np.asarray(bs))
        nb = len(Vb)
        for i, srcs in enumerate(bsrc):
            for src in srcs:
                if src is not None:
                    seam_vid[src] = (name, i)

        # ---- 3. internal polylines: sewn internal lines, then fold lines
        Vi, cons = [], []
        base = nb

        def vid_near(q):
            for j, v in enumerate(Vi):
                if (v[0] - q[0]) ** 2 + (v[1] - q[1]) ** 2 < (MERGE * h) ** 2:
                    return j
            return None
        sewn_lines = set()
        sewn_t = collections.defaultdict(list)          # line -> [(t0, t1)] its sewn stretches (shares of its length)
        sewn_vids = collections.defaultdict(list)       # line -> the inner vertices of its seam sides
        for si, ks, pts, inner, line, t in internal_sides:
            sewn_lines.add(line)
            sewn_t[line].append(tuple(sorted(t)))
            idx = []
            for k, q in enumerate(pts):
                if not inner[k]:
                    idx.append(seam_vid[(si, ks, k)][1])
                    continue
                j = vid_near(q)
                if j is None:
                    Vi.append(q)
                    j = len(Vi) - 1
                else:
                    stats['merged internal samples'] += 1
                idx.append(base + j)
                seam_vid[(si, ks, k)] = (name, base + j)
            cons += [(a, b) for a, b in zip(idx[:-1], idx[1:]) if a != b and (a >= nb or b >= nb)]
            sewn_vids[line] += [v for v in idx if v >= nb]
        fold_of = {}
        folded_lines = {(f['piece'], f['line']) for f in spec.get('folds', []) if f.get('line') and f.get('deg')}
        # a hole's edge (core/spec.holes): its lines are constrained like a fold line's, however short (a gap in the
        # ring lets a triangle across the hole), but are no fold; the triangles inside are dropped (dress.triangulate)
        hole_rings = [R for _, R in S.holes(p)]
        hole_ids = {lid for ids, _ in S.holes(p) for lid in ids}
        for it in p.get('internals', []):
            if it.get('kind') == 'baseline':
                continue
            q = np.asarray(it['pts'], float)
            Lq = S.polyline_len(q)
            is_hole = it['id'] in hole_ids
            if Lq < (1e-6 if is_hole else 2 * h):
                continue
            pts = _resample_corners(q, h)
            dist = dist_to_ring(pts, Vb)
            if (dist < LINE_GAP).all():
                continue                                   # lies on the outline
            # a line sewn along a stretch is a fold line too (a pocket's side turned under and stitched at its
            # corner, the reference drape folds it: user 2026-10-25, docs/reviews/REVIEW_fable_sewn_fold_line_2026-10-25).
            # Until then a sewn line was skipped whole -- the first build's guard against adding a line twice -- and
            # its angle never reached the solver. Only its stretches not sewn get new samples, each ending on the
            # seam's own end sample (vid_near takes it: one chain); the seam's vertices on it are tagged with the line
            sewn = it['id'] in sewn_lines
            joint = np.zeros(len(pts), bool)               # a stretch's end on a seam's end: not "on another line"
            starts = set()                                  # where a stretch begins: the run breaks there
            if sewn:
                parts = [_sub_polyline(q, *u) for u in _unsewn(sewn_t[it['id']])]
                parts = [(P_, ja, jb) for P_, ja, jb in parts if S.polyline_len(P_) >= (1e-6 if is_hole else 0.5 * h)]
                segs, gr, jm = [], [], []
                for P_, ja, jb in parts:
                    r = _resample_corners(P_, h)
                    starts.add(sum(len(s_) for s_ in segs))
                    j_ = np.zeros(len(r), bool)
                    j_[0], j_[-1] = ja, jb
                    segs.append(r)
                    gr.append(np.gradient(r, axis=0))
                    jm.append(j_)
                pts = np.vstack(segs) if segs else np.zeros((0, 2))
                grad = np.vstack(gr) if gr else np.zeros((0, 2))
                joint = np.concatenate(jm) if jm else np.zeros(0, bool)
                dist = dist_to_ring(pts, Vb) if len(pts) else np.zeros(0)
            else:
                grad = np.gradient(pts, axis=0)
            # a sample off the outline is kept however near where the line runs along it (a line along the hem, user
            # 2026-10-28) and on a hole's ring; within 0.5 h where the line runs across it (its end on the outline),
            # dropped as before: kept, it made a sliver with the outline (1.4 deg on a pants piece). Kept only from
            # 0.25 h was tried and left out: the user wants lines nearer than that in the mesh, closeness to the
            # reference drape is not the aim yet (user 2026-10-28)
            ok = ((points_in_polygon(pts, Vb) & (dist >= LINE_GAP)
                   & ((dist >= 0.5 * h) | is_hole | along_ring(pts, grad, Vb))) if len(pts) else np.zeros(0, bool))
            if cons and not is_hole:     # a hole's next line starts where the last ended, often running on along it
                # a point within 0.25 h of an earlier line's segment, running along it: that part is the earlier
                # line already (two internal lines on top of each other made zero-area triangles at fine meshes)
                Pall = np.vstack([Vb, np.asarray(Vi).reshape(-1, 2)])
                ca = np.asarray(cons, np.int64)
                SA, SB = Pall[ca[:, 0]], Pall[ca[:, 1]]
                dq = grad
                for k in np.nonzero(ok & ~joint)[0]:
                    dd = SB - SA
                    ll = np.maximum((dd * dd).sum(1), 1e-12)
                    tt = np.clip(((pts[k] - SA) * dd).sum(1) / ll, 0, 1)
                    di = np.linalg.norm(SA + tt[:, None] * dd - pts[k], axis=1)
                    m = int(np.argmin(di))
                    cosv = abs(float(np.dot(dq[k], dd[m]))) / max(np.linalg.norm(dq[k]) * np.sqrt(ll[m]), 1e-12)
                    if di[m] < 0.25 * h and cosv > 0.9:
                        ok[k] = False
                        stats['fold samples on another line'] += 1
            fid = None if is_hole else len(fold_meta)
            if fid is not None:
                fold_meta.append(f"{name}|{it['id']}|{Lq:.1f}")
            run = []
            for k in range(len(pts) + 1):
                if k in starts:                    # the next stretch not sewn
                    cons += [(a, b) for a, b in zip(run[:-1], run[1:]) if a != b]
                    run = []
                # a step that crosses the outline (a line over the tip of a slit, its two samples inside on either
                # side: the triangulation added a vertex, "Could not mesh", a shirt sleeve with its placket Off,
                # 2026-10-24) ends the run there
                if k < len(pts) and ok[k] and run and seg_meets_ring(pts[k - 1], pts[k], Vb, LINE_GAP):
                    cons += [(a, b) for a, b in zip(run[:-1], run[1:]) if a != b]
                    run = []
                if k < len(pts) and ok[k]:
                    j = vid_near(pts[k])
                    if j is None:
                        Vi.append(pts[k])
                        j = len(Vi) - 1
                    run.append(base + j)
                    if fid is not None:
                        fold_of.setdefault(base + j, fid)
                else:
                    cons += [(a, b) for a, b in zip(run[:-1], run[1:]) if a != b]
                    run = []
            if is_hole:
                stats['hole lines'] += 1
                continue
            if sewn:
                for v in sewn_vids[it['id']]:
                    fold_of.setdefault(v, fid)
                stats['sewn lines also fold lines'] += 1
            stats['fold lines'] += 1
            # spec['fold_rows'] (opt-in while it is measured): a fold line with a set angle gets a row of vertices on
            # each side, FOLD_ROW x h off it, joined as constraints -- the fold becomes a 3-row ladder and bends on its
            # middle row. The reference drape's mesh has its lines so (1.5-2 mm off at 5 mm particles: 11-22 vertices by each tuck line of a
            # trouser front, ours 0-2), and its tucks fold cleanly where ours zig-zag (user 2026-10-09). Only for a
            # piece meshed at 4-8 mm (h is the piece's own): below, 6 -> 2 mm already makes thin triangles; at 10 mm the
            # rows next to the coarse grid made 16 slivers on a test trouser (mesh_check, 0 at 4 and 6 mm)
            if spec.get('fold_rows') and 4.0 <= h <= 8.0 and (name, it['id']) in folded_lines and not sewn:
                tg = np.gradient(pts, axis=0)
                tg /= np.linalg.norm(tg, axis=1, keepdims=True) + 1e-12
                nm_ = np.c_[-tg[:, 1], tg[:, 0]]
                for sgn in (1.0, -1.0):
                    rp = pts + sgn * FOLD_ROW * h * nm_
                    okr = ok & points_in_polygon(rp, Vb) & (dist_to_ring(rp, Vb) >= 0.5 * h)
                    run = []
                    for k in range(len(rp) + 1):
                        if k < len(rp) and okr[k]:
                            j = vid_near(rp[k])
                            if j is None:
                                Vi.append(rp[k])
                                j = len(Vi) - 1
                            run.append(base + j)
                        else:
                            cons += [(a, b) for a, b in zip(run[:-1], run[1:]) if a != b]
                            run = []
                    stats['fold rows'] += int(okr.sum())
        cons = sorted(set(tuple(sorted(c)) for c in cons))

        # ---- 4. interior: rows off the outline and the constraint lines, the hex grid in the middle, smoothed
        Vi_a = np.asarray(Vi, float).reshape(-1, 2)
        ca = np.asarray(cons, np.int64).reshape(-1, 2)
        sides = [np.asarray(x[2], float) for x in internal_sides]
        Vg, cnt, hit = _interior_cached(Vb, Vi_a, ca, sides, h, hole_rings)
        stats['filled'] += cnt[1]
        stats['edge rows'] += cnt[0]
        stats['interiors read' if hit else 'interiors made'] += 1
        V = np.vstack([Vb, Vi_a, Vg])
        Lf = -np.ones(len(V), np.int32)
        for v_, f_ in fold_of.items():
            Lf[v_] = f_
        C = np.asarray(cons, np.int64).reshape(-1, 2)
        V, C, Lf, nx = _fix_crossings(V, C, Lf, snap=0.25 * h)
        stats['crossings split'] += nx
        built[name] = {'name': name, 'fabric': p.get('fabric', ''), 'V': V, 'nb': nb, 'cons': C, 'Lf': Lf,
                       'holes': hole_rings}
        rep.append(f"  {name:<18} boundary {nb:5d} internal {len(Vi):4d} grid {len(Vg):6d} outline {o.L:7.1f} mm"
                   + (f" holes {len(hole_rings)}" if hole_rings else ""))

    def mirror_into(p, prim):
        """The twin's mesh = its pair's mesh mirrored in the layout (vertex k <-> vertex k), so the solver can copy
        one half onto the other every frame (FINDINGS 4j-8: two separately meshed halves copied through a fit still
        crossed at their seams, 17 pairs; one-to-one, 0). False (built on its own) when the outline or a seam sample
        does not land on the mirrored mesh."""
        h = H[p['name']]
        name = p['name']
        A, oa, ob = built[prim], OL[prim], OL[name]
        ca, cb = 0.5 * (oa.B.min(0) + oa.B.max(0)), 0.5 * (ob.B.min(0) + ob.B.max(0))

        def M(q):
            q = np.asarray(q, float)
            return np.stack([cb[0] - (q[:, 0] - ca[0]), q[:, 1] - ca[1] + cb[1]], 1)

        def Minv(q):
            q = np.asarray(q, float)
            return np.stack([ca[0] - (q[:, 0] - cb[0]), q[:, 1] - cb[1] + ca[1]], 1)
        fit = dist_to_ring(M(oa.B), ob.B).max()
        if fit > 0.1:
            rep.append(f"  {name} not mirror-meshed: outline fit {fit:.2f} mm")
            return False
        Va = A['V']
        new_vid = {}
        for si, sm in enumerate(seams):
            for ks, x in enumerate(sm['sides']):
                if x['piece'] != name:
                    continue
                pos = ob.at(np.mod(np.asarray(x['s']), ob.L)) if x['kind'] == 'boundary' else x['pts']
                q = Minv(pos)
                d2 = ((q[:, None, :] - Va[None]) ** 2).sum(-1)
                j = d2.argmin(1)
                far = np.sqrt(d2[np.arange(len(q)), j]) > MERGE * h
                if far.any():
                    rep.append(f"  {name} not mirror-meshed: {int(far.sum())} seam samples off the mirrored mesh")
                    return False
                for k, jj in enumerate(j):
                    new_vid[(si, ks, k)] = (name, int(jj))
        Lf = -np.ones(len(Va), np.int32)
        ints = {x['id']: np.asarray(x['pts'], float) for x in p.get('internals', [])}
        for fid in sorted({int(f) for f in A['Lf'] if f >= 0}):
            pn, lid, _ = fold_meta[fid].split('|')
            Pm = M(INT[prim][lid]['pts'])
            best = None
            for tid, Q in ints.items():
                d = float(np.median(dist_to_polyline(Pm, Q)))
                if d < 3.0 and (best is None or d < best[0]):
                    best = (d, tid)
            if best is None:
                continue
            nf = len(fold_meta)
            fold_meta.append(f"{name}|{best[1]}|{S.polyline_len(ints[best[1]]):.1f}")
            Lf[A['Lf'] == fid] = nf
        seam_vid.update(new_vid)
        built[name] = {'name': name, 'fabric': p.get('fabric', ''), 'V': M(Va), 'nb': A['nb'], 'cons': A['cons'].copy(),
                       'Lf': Lf, 'twin_of': prim, 'holes': [M(R) for R in A.get('holes', [])]}
        stats['mirror-meshed pairs'] += 1
        rep.append(f"  {name:<18} <- mirror of {prim}: {len(Va)} verts, outline fit {fit:.3f} mm, "
                   f"seam samples {len(new_vid)} on the mirrored mesh")
        return True

    twin_of = {b: a for a, b in S.twins(spec) if a in on and b in on}
    # TEST ONLY: spec['twin_swap'] (the settings set's twin_swap, core/presets.py; was env AC9_SIM_TWIN_SWAP) meshes
    # the mirrored copy of each pair and mirrors it into the unmirrored one (the mesh's direction only; placement
    # still follows spec.mirror_twins). Default unchanged.
    if spec.get('twin_swap'):
        twin_of = {a: b for b, a in twin_of.items()}
    for p in pieces:
        if p['name'] not in twin_of:
            make(p)
    for p in pieces:
        if p['name'] in twin_of and not mirror_into(p, twin_of[p['name']]):
            make(p)
    out_pieces = [built[p['name']] for p in pieces]

    # ---- 5. stitches
    pid = {x['name']: i for i, x in enumerate(out_pieces)}
    pairs, pairs_off = set(), set()
    seam_of = {}                                  # stitch -> the spec seam id it came from (the first, if shared)
    for si, sm in enumerate(seams):
        for k in range(sm['n']):
            a = seam_vid.get((si, 0, k))
            b = seam_vid.get((si, 1, k))
            if a is None or b is None:
                stats['stitch skipped (sample not placed)'] += 1
                continue
            if a == b:
                stats['stitch skipped (same vertex)'] += 1
                continue
            pr = tuple(sorted([(pid[a[0]], a[1]), (pid[b[0]], b[1])]))
            if sm['off']:
                pairs_off.add(pr)
                seam_of.setdefault(('off',) + pr, str(sm['id']))
            else:
                pairs.add(pr)
                seam_of.setdefault(pr, str(sm['id']))
    pairs = sorted(pairs)
    pairs_off = sorted(pairs_off - set(pairs))
    # the two sides of every seam as flat polylines, for easing (いせ込み): a seam whose sides differ in length gathers
    # the longer side, and with cloth that does not stretch its folds eat the panel's length (foundation_2026-09-30
    # RESULT_Y3). pat['ease'] lists the sides longer than their partner with their length ratio ('ratio', the whole
    # side) and, on a seam sewn notch to notch, per segment of 'seam_flat' the ratio there ('ratio_seg': the sample
    # spacing on this side over the other's -- the ease differs segment by segment, and a side may be the longer one
    # in only some of them); the add-on decides whether to ease them
    V_of = {x['name']: x['V'] for x in out_pieces}
    ease = []
    for si, sm in enumerate(seams):
        if sm['off']:
            continue
        sides = []
        for sd in (0, 1):
            ks = [k for k in range(sm['n']) if seam_vid.get((si, sd, k)) is not None]
            vs = [seam_vid[(si, sd, k)] for k in ks]
            if len(vs) < 3 or len({v[0] for v in vs}) != 1:
                break
            P = V_of[vs[0][0]][[v[1] for v in vs]]
            sides.append((vs[0][0], P, float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum()), ks))
        if len(sides) < 2 or min(sides[0][2], sides[1][2]) <= 0:
            continue
        U = [np.asarray(x['u'], float) for x in sm['sides']]
        for me in (0, 1):
            this, other = sides[me], sides[1 - me]
            ks = np.asarray(this[3])
            du_me, du_ot = np.diff(U[me][ks]), np.diff(U[1 - me][ks])
            rs = np.where(du_ot > 1e-9, du_me / np.maximum(du_ot, 1e-9), 1.0)
            r = this[2] / other[2]
            # the longer side as before (one seam without notches: the same entry); the shorter side only where
            # notches make it the longer one in a segment
            if r < 1.0 and (sm['segments'] == 1 or rs.max() <= 1.0 + 1e-6):
                continue
            if r == 1.0 and me == 0:
                continue
            e_ = {'seam': si, 'piece': this[0], 'other': other[0], 'ratio': r,
                  'seam_flat': np.round(this[1], 3).tolist()}
            if sm['segments'] > 1:
                e_['ratio_seg'] = np.round(rs, 4).tolist()
            ease.append(e_)
    rep.append(f"stitch loose edges: {len(pairs)}; " + ', '.join(f"{k} {v}" for k, v in sorted(stats.items())))
    pat = {'S': 1000.0, 'h': h, 'h_by_piece': np.array([H[x['name']] for x in out_pieces], float),
           'names': np.array([x['name'] for x in out_pieces]),
           'fabrics': np.array([x['fabric'] for x in out_pieces]),
           'islands': -np.ones(len(out_pieces), np.int32), 'nb': np.array([x['nb'] for x in out_pieces]),
           'fold_meta': np.array(fold_meta),
           'twin_src': np.array([[x['name'] for x in out_pieces].index(x['twin_of']) if x.get('twin_of') else -1
                                 for x in out_pieces], np.int32),
           'stitch': np.array([[a[0], a[1], b[0], b[1]] for a, b in pairs], np.int32).reshape(-1, 4),
           'stitch_seam': np.array([seam_of[pr] for pr in pairs], dtype=str),
           'stitch_off': np.array([[a[0], a[1], b[0], b[1]] for a, b in pairs_off], np.int32).reshape(-1, 4),
           'stitch_off_seam': np.array([seam_of[('off',) + pr] for pr in pairs_off], dtype=str)}
    for i, x in enumerate(out_pieces):
        pat[f"V{i}"], pat[f"C{i}"], pat[f"Lf{i}"] = x['V'], x['cons'], x['Lf']
        if x.get('holes'):        # the rings cut out (layout mm), one after another, Hn their lengths: no key, no hole
            pat[f"H{i}"] = np.vstack(x['holes'])
            pat[f"Hn{i}"] = np.array([len(R) for R in x['holes']], np.int64)
    pat['ease_json'] = np.array(json.dumps(ease))
    return pat, rep


def hole_rings(pat, i):
    """The rings cut out of piece i of a pattern (layout mm), [] when it has none (pat a dict or an npz)."""
    keys = pat.files if hasattr(pat, 'files') else pat
    if f"H{i}" not in keys:
        return []
    H, n = np.asarray(pat[f"H{i}"], float), np.asarray(pat[f"Hn{i}"], np.int64)
    return np.split(H, np.cumsum(n)[:-1])


def drop_hole_tris(V, T, rings):
    """The triangles T (k, 3) of the vertices V not inside a hole (by their centroids)."""
    if not len(rings) or not len(T):
        return T
    c = V[T].mean(1)
    return T[~S.in_holes(c, rings)]


def _project(o, q):
    """Outline parameter of the nearest outline point for each q."""
    A, Bn = o.B, np.roll(o.B, -1, 0)
    D = Bn - A
    ll = np.maximum((D * D).sum(1), 1e-12)
    t = np.clip(((q[:, None, :] - A[None]) * D[None]).sum(-1) / ll[None], 0, 1)
    X = A[None] + t[..., None] * D[None]
    dist = np.linalg.norm(X - q[:, None, :], axis=-1)
    j = dist.argmin(1)
    n = np.arange(len(q))
    return o.cum[j] + t[n, j] * np.sqrt(ll[j]), dist[n, j]
