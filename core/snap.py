# SPDX-License-Identifier: GPL-3.0-or-later
"""Snapping while drawing on the flat pattern (no bpy). Design: 2609_Apparel_CAD/docs/design/
DESIGN_draw_basics_2026-10-20.md F5 (user 2026-10-20, Fable review r1).

Targets, first wins: a corner, a notch, an internal line's end, the middle of an edge, the nearest point on an outline
or an internal line, a grid point. Each tool picks its kinds (Draw Line wants the outline, a corner drag does not).
A drawn line's direction can be held to 15 degree steps (Shift); nothing pulls a line to 0 / 90 degrees on its own:
a tight skirt's side seam leans 2.1 degrees and must stay so (Fable r1, must-fix 1).
"""
import math

import numpy as np

from . import spec as S

POINT_KINDS = ('corner', 'notch', 'iend', 'mid')       # tested by distance, in this order
LINE_KINDS = ('edge', 'iline')                         # the nearest point on a line
ANGLE_STEP = 15.0                                      # degrees, Shift while drawing


class Targets:
    """What can be snapped to on the pieces that are on, gathered once per tool use (the pattern does not change
    while drawing). `outlines`: {name: S.Outline} as store.get gives them (made here if None)."""

    def __init__(self, spec, outlines=None, skip=()):
        self.pts = {k: [] for k in POINT_KINDS}       # kind -> [(x, y, piece)]
        segA, segB, segP, segK = [], [], [], []
        for pc in spec.get('pieces', []):
            nm = pc['name']
            if not pc.get('enabled', True) or nm in skip:
                continue
            ol = outlines[nm] if outlines is not None and nm in outlines else S.Outline(pc)
            for e in pc['edges']:
                self.pts['corner'].append((*e['pts'][0], nm))
                if e['id'] in ol.edges:
                    self.pts['mid'].append((*ol.at(ol.s_of(e['id'], 0.5)), nm))
            for n in pc.get('notches', []):
                if n['edge'] in ol.edges:
                    self.pts['notch'].append((*ol.at(ol.s_of(n['edge'], float(n['t']))), nm))
            B = np.asarray(ol.B, float)
            segA.append(B)
            segB.append(np.roll(B, -1, 0))
            segP += [nm] * len(B)
            segK += ['edge'] * len(B)
            for it in pc.get('internals', []):
                if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                    continue
                P = np.asarray(it['pts'], float)
                if not it.get('closed'):
                    self.pts['iend'] += [(*P[0], nm), (*P[-1], nm)]
                segA.append(P[:-1])
                segB.append(P[1:])
                segP += [nm] * (len(P) - 1)
                segK += ['iline'] * (len(P) - 1)
        self.arr = {k: (np.array([p[:2] for p in v], float).reshape(-1, 2), [p[2] for p in v])
                    for k, v in self.pts.items()}
        self.A = np.vstack(segA) if segA else np.zeros((0, 2))
        self.B = np.vstack(segB) if segB else np.zeros((0, 2))
        self.seg_piece = segP
        self.seg_kind = np.array(segK)

    def nearest_on_lines(self, q, kinds):
        """(point, distance, kind, piece) of the nearest point on the outlines / internal lines of `kinds`."""
        if not len(self.A):
            return None
        m = np.isin(self.seg_kind, list(kinds))
        if not m.any():
            return None
        A, B = self.A[m], self.B[m]
        D = B - A
        t = np.clip(((q - A) * D).sum(1) / np.maximum((D * D).sum(1), 1e-12), 0.0, 1.0)
        P = A + t[:, None] * D
        d = np.linalg.norm(P - q, axis=1)
        j = int(d.argmin())
        idx = np.nonzero(m)[0][j]
        return P[j], float(d[j]), str(self.seg_kind[idx]), self.seg_piece[idx]


def grid_step(mm_per_px, min_px=8.0):
    """The finest grid spacing (1, 10, 100, 1000 mm) at least `min_px` pixels apart on screen."""
    for s in (1.0, 10.0, 100.0, 1000.0):
        if s / max(mm_per_px, 1e-9) >= min_px:
            return s
    return 10000.0


def grid_lines(lo, hi, mm_per_px, max_lines=300):
    """([(x0, y0, x1, y1)] minor, [...] major, step) for the box lo..hi (mm): the finest 1 / 10 / 100 / 1000 mm grid
    at least 8 px apart, coarser while it would draw more than max_lines a way."""
    step = grid_step(mm_per_px)
    while max((hi - lo) / step) > max_lines:
        step *= 10.0
    lo = np.floor(lo / step) * step
    hi = np.ceil(hi / step) * step
    minor, major = [], []
    for ax in (0, 1):
        n = int(round((hi[ax] - lo[ax]) / step))
        for i in range(n + 1):
            v = lo[ax] + i * step
            seg = (v, lo[1], v, hi[1]) if ax == 0 else (lo[0], v, hi[0], v)
            (major if abs(v / (step * 10) - round(v / (step * 10))) < 1e-6 else minor).append(seg)
    return minor, major, step


def snap(q, radius_mm, targets, kinds, grid=None):
    """The point q (mm) snapped: (point, kind, piece). kind None = not snapped (q itself). `kinds`: the target kinds
    this tool uses (POINT_KINDS / LINE_KINDS names, and 'grid' with `grid` = its spacing in mm)."""
    q = np.asarray(q, float)
    if targets is not None:
        for k in POINT_KINDS:
            if k not in kinds:
                continue
            P, names = targets.arr[k]
            if not len(P):
                continue
            d = np.linalg.norm(P - q, axis=1)
            j = int(d.argmin())
            if d[j] <= radius_mm:
                return P[j].copy(), k, names[j]
        lk = [k for k in LINE_KINDS if k in kinds]
        if lk:
            hit = targets.nearest_on_lines(q, lk)
            if hit is not None and hit[1] <= radius_mm:
                return hit[0], hit[2], hit[3]
    if 'grid' in kinds and grid:
        g = np.round(q / grid) * grid
        if np.linalg.norm(g - q) <= radius_mm:
            return g, 'grid', None
    return q, None, None


def lock_angle(origin, q, step=ANGLE_STEP):
    """q turned about `origin` onto the nearest multiple of `step` degrees, same distance."""
    o, q = np.asarray(origin, float), np.asarray(q, float)
    d = q - o
    r = float(np.hypot(*d))
    if r < 1e-9:
        return q
    a = math.radians(round(math.degrees(math.atan2(d[1], d[0])) / step) * step)
    return o + r * np.array([math.cos(a), math.sin(a)])


def polar(origin, length, deg):
    o = np.asarray(origin, float)
    a = math.radians(deg)
    return o + float(length) * np.array([math.cos(a), math.sin(a)])


def angle_of(origin, q):
    d = np.asarray(q, float) - np.asarray(origin, float)
    return math.degrees(math.atan2(d[1], d[0]))


# ---------------------------------------------------------------- gaps while dragging (Edit Pattern)
# While a point, an internal line or an edge is dragged: its distances to the lines around it in its own piece, and
# snapping to the middle, to even spacing and to a distance another internal line has (user 2026-10-23, DECISIONS
# cloth-sim-drag-distance-snap-2026-10-23, Fable review REVIEW_fable_snap_measure_input_2026-10-23).

GAP_PX = 8.0                                           # the gap snaps reach this far; points (12 px) win over them
GAP_KINDS = ('center', 'even', 'same')                 # tried in this order


def _closed(P):
    return len(P) > 2 and np.linalg.norm(P[0] - P[-1]) < 1e-6


def _middle(P):
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    cum = np.r_[0, np.cumsum(seg)]
    h = cum[-1] / 2
    j = min(max(int(np.searchsorted(cum, h, side='right')) - 1, 0), len(seg) - 1)
    return P[j] + (P[j + 1] - P[j]) * ((h - cum[j]) / max(seg[j], 1e-12))


def piece_segments(pc, skip_edges=(), skip_lines=()):
    """(A, B) of the piece's outline edges (but the edge indices skip_edges) and internal lines (but skip_lines)."""
    A, B = [], []
    for k, e in enumerate(pc['edges']):
        if k in skip_edges:
            continue
        P = np.asarray(e['pts'], float)
        A.append(P[:-1])
        B.append(P[1:])
    for it in pc.get('internals', []):
        if it['id'] in skip_lines or it.get('kind') == 'baseline' or len(it['pts']) < 2:
            continue
        P = np.asarray(it['pts'], float)
        A.append(P[:-1])
        B.append(P[1:])
    if not A:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.vstack(A), np.vstack(B)


def ray_hits(A, B, o, u, eps=1e-3):
    """The distances (sorted, mm) along the ray o + t u (u a unit vector) where it meets the segments A-B."""
    if not len(A):
        return np.zeros(0)
    D = B - A
    den = u[0] * D[:, 1] - u[1] * D[:, 0]
    ok = np.abs(den) > 1e-12
    W = A - o
    with np.errstate(divide='ignore', invalid='ignore'):
        t = (W[:, 0] * D[:, 1] - W[:, 1] * D[:, 0]) / den
        w = (W[:, 0] * u[1] - W[:, 1] * u[0]) / den
    t = np.sort(t[ok & (t > eps) & (w >= -1e-9) & (w <= 1 + 1e-9)])
    if len(t) > 1:                                     # through a vertex: two segments, one crossing
        t = t[np.r_[True, np.diff(t) > 1e-6]]
    return t


class Probe:
    """Rays from the thing dragged, in pairs along axes square to each other: axis i is rays 2i (+u) and 2i+1 (-u),
    a ray None when the thing has nothing to see that way. Each ray is (origin, unit direction)."""

    def __init__(self, axes, rays):
        self.axes = [np.asarray(u, float) for u in axes]
        self.rays = rays


def point_probe(p):
    """A point: right, left, up and down."""
    p = np.asarray(p, float)
    X, Y = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    return Probe([X, Y], [(p, X), (p, -X), (p, Y), (p, -Y)])


def line_probe(P, t_dir=None):
    """An internal line: across it from its middle, and on from each end along it. A closed shape: right, left, up
    and down from the middles of its box's sides. t_dir: the line's direction when the drag started (kept while it
    moves, so the axes do not turn)."""
    P = np.asarray(P, float)
    X, Y = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    if _closed(P):
        lo, hi = P.min(0), P.max(0)
        c = (lo + hi) / 2
        return Probe([X, Y], [((hi[0], c[1]), X), ((lo[0], c[1]), -X), ((c[0], hi[1]), Y), ((c[0], lo[1]), -Y)])
    t = np.asarray(t_dir if t_dir is not None else P[-1] - P[0], float)
    t = t / max(np.linalg.norm(t), 1e-12)
    n = np.array([-t[1], t[0]])
    mid = _middle(P)
    s = P @ t
    return Probe([n, t], [(mid, n), (mid, -n), (P[int(s.argmax())], t), (P[int(s.argmin())], -t)])


def edge_probe(P, inward):
    """An outline edge: from its middle, across it into the piece (one ray)."""
    u = np.asarray(inward, float)
    return Probe([u], [(_middle(np.asarray(P, float)), u), None])


def gauge(probe, A, B):
    """[the sorted distances each ray meets] (None for a missing ray)."""
    return [None if r is None else ray_hits(A, B, np.asarray(r[0], float), r[1]) for r in probe.rays]


def gaps(probe, hits):
    """[(origin, end point, distance, ray index)]: each ray to the first line it meets."""
    out = []
    for i, (r, h) in enumerate(zip(probe.rays, hits)):
        if r is not None and h is not None and len(h):
            o = np.asarray(r[0], float)
            out.append((o, o + r[1] * h[0], float(h[0]), i))
    return out


def same_refs(pc, probe, skip_line=None):
    """{ray index: [(distance, origin)]}: how far the piece's other internal lines are from what they face, each
    looked at the way that ray looks (an open line from its middle, or from its end when the ray runs along it; a
    closed shape from its box's side). For a second pocket as far from the side as the first."""
    out = {i: [] for i in range(len(probe.rays))}
    for it in pc.get('internals', []):
        if it['id'] == skip_line or it.get('kind') == 'baseline' or len(it['pts']) < 2:
            continue
        A, B = piece_segments(pc, skip_lines=(it['id'], skip_line))
        P = np.asarray(it['pts'], float)
        lo, hi = P.min(0), P.max(0)
        chord = P[-1] - P[0]
        for i, r in enumerate(probe.rays):
            if r is None:
                continue
            u = r[1]
            if _closed(P):
                o = (lo + hi) / 2 + u * (np.abs(u) * (hi - lo) / 2).sum()
            elif abs(u @ chord) > 0.7 * np.linalg.norm(chord):
                o = P[int((P @ u).argmax())]
            else:
                o = _middle(P)
            h = ray_hits(A, B, o, u)
            if len(h):
                out[i].append((float(h[0]), o))
    return out


def gap_snap(probe, hits, radius, refs=None, along=None):
    """(shift (2,), [(kind, ray indices, ref origin or None)]): the move that puts the dragged thing at a middle, an
    even spacing or a distance in refs (same_refs). hits: gauge() where the mouse is. Each axis snaps on its own;
    along: the one direction the drag is held to (an axis key or Shift), then only an axis along it snaps."""
    shift = np.zeros(2)
    what = []
    for ax, u in enumerate(probe.axes):
        if along is not None and abs(float(np.dot(u, along))) < 0.99:
            continue
        hp, hm = hits[2 * ax], hits[2 * ax + 1]
        cand = {k: [] for k in GAP_KINDS}             # (s along +u, ray indices, ref origin)
        if hp is not None and hm is not None and len(hp) and len(hm):
            cand['center'].append(((hp[0] - hm[0]) / 2, (2 * ax, 2 * ax + 1), None))
        if hp is not None and len(hp) > 1:
            cand['even'].append((2 * hp[0] - hp[1], (2 * ax,), None))
        if hm is not None and len(hm) > 1:
            cand['even'].append((hm[1] - 2 * hm[0], (2 * ax + 1,), None))
        for ri, sign, h in ((2 * ax, 1.0, hp), (2 * ax + 1, -1.0, hm)):
            if h is not None and len(h) and refs:
                cand['same'] += [(sign * (h[0] - d), (ri,), o) for d, o in refs.get(ri, ())]
        for k in GAP_KINDS:
            near = [c for c in cand[k] if abs(c[0]) <= radius]
            if near:
                s, rays, o = min(near, key=lambda c: abs(c[0]))
                shift += s * u
                what.append((k, rays, o))
                break
    return shift, what


def align_snap(mine, others, radius):
    """A whole piece moved: the shift (2,) that lines one of its corners up with another piece's corner in x and / or
    in y, and the lines to show [(axis 0 / 1, my corner index, their corner index)]."""
    mine, others = np.atleast_2d(mine), np.atleast_2d(others)
    shift, lines = np.zeros(2), []
    if not len(others) or not len(mine):
        return shift, lines
    for ax in (0, 1):
        D = others[None, :, ax] - mine[:, None, ax]
        i, j = np.unravel_index(int(np.abs(D).argmin()), D.shape)
        if abs(D[i, j]) <= radius:
            shift[ax] = D[i, j]
            lines.append((ax, int(i), int(j)))
    return shift, lines


def gap_lines(probe, hits, what=()):
    """[(a, b, mm, hot)] to draw: each ray's gap (hot when a gap snap used that ray), and for an even spacing the
    next gap on that ray, for a same distance the other line's gap (from its origin). hits: gauge() at the snapped
    place."""
    hot = {i for kind, rays, o in what for i in rays}
    out = [(a, b, mm, i in hot) for a, b, mm, i in gaps(probe, hits)]
    for kind, rays, o in what:
        r, h = probe.rays[rays[0]], hits[rays[0]]
        if r is None or h is None or not len(h):
            continue
        org = np.asarray(r[0], float)
        if kind == 'even' and len(h) > 1:
            out.append((org + r[1] * h[0], org + r[1] * h[1], float(h[1] - h[0]), True))
        if kind == 'same' and o is not None:
            o = np.asarray(o, float)
            out.append((o, o + r[1] * h[0], float(h[0]), True))
    return out
