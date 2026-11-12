# SPDX-License-Identifier: GPL-3.0-or-later
"""Drafting edits on the spec (no bpy): move corners and edges, split and join edges, curve an edge, draw a piece,
copy a piece mirrored. Every function edits the spec dict in place and keeps the seams valid.

A piece's corner k is where edge k starts (edges[k]['pts'][0], the end of edge k-1). An edge is its point list
(`pts`, what the simulation reads); a curved edge also has `ctrl` = its two cubic Bezier handles, and its `pts`
are made from them. Seams hold (edge id, t) with t a fraction of the edge's length, so:
  - moving or bending an edge keeps every seam end at the same fraction of its edge (a corner stays a corner),
    except a seam end inside an edge with only one end moved: it keeps its distance from the end that stayed;
  - splitting or joining edges re-states the fractions so every seam end stays at exactly the same point.
Twins (a piece and its mirrored copy, spec.twins) are edited together: the other one gets the mirror-image edit.
"""
import copy
from ._i18n import rpt, length as len_text

import numpy as np

from . import spec as S

STEP = 2.0            # mm between the points of a curved edge
SELF_TOL = 0.5        # mm: a piece is symmetric about its center line when its corners mirror onto its corners within this
TWIN_TOL = 0.1        # mm: corners of twins match within this after mirroring (pattern_build.mirror_into's fit)
LINE_END_ON_EDGE = 1.0  # mm: an internal line's end this close to an outline edge rides on it when the edge moves
MIN_EDGE = 2.1        # mm: shortest edge allowed (pattern_build merges outline samples closer than MERGE * h = 2.1 mm)


# ---------------------------------------------------------------- small helpers

def piece(spec, name):
    return next(p for p in spec['pieces'] if p['name'] == name)


def edge_index(pc, eid):
    return next(i for i, e in enumerate(pc['edges']) if e['id'] == eid)


def corners(pc):
    """(n, 2) corner points, corner k = start of edge k."""
    return np.array([e['pts'][0] for e in pc['edges']], float)


def new_edge_id(pc):
    used = {e['id'] for e in pc['edges']}
    k = len(pc['edges']) + 1
    while f"E{k}" in used:
        k += 1
    return f"E{k}"


def signed_area(P):
    P = np.asarray(P, float)
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _r(P):
    return [[round(float(x), 4), round(float(y), 4)] for x, y in np.asarray(P, float)]


def xform_internal(it, f):
    """Map internal line it by the affine layout map f (a move, a turn, a scale, a mirror); its curve handles too
    (core/iline.py: sampled again, a scaled curve has more or fewer points). Returns it."""
    from . import iline as IL
    N, C, _, own = IL.shape(it) if it.get('ctrl') else (None, None, None, False)
    it['pts'] = _r(f(it['pts']))
    if own:
        IL.set_shape(it, np.asarray(f(N), float), [None if c is None else np.asarray(f(c), float) for c in C])
    else:
        it.pop('nodes', None)
        it.pop('ctrl', None)
    return it


def _similarity(a0, b0, a1, b1):
    """The map z -> A z + B (complex: rotate, scale, shift) taking a0 to a1 and b0 to b1; for a0 == b0 a shift."""
    za0, zb0, za1, zb1 = complex(*a0), complex(*b0), complex(*a1), complex(*b1)
    if abs(zb0 - za0) < 1e-12:
        A = 1.0 + 0j
    else:
        A = (zb1 - za1) / (zb0 - za0)
    B = za1 - A * za0

    def f(P):
        P = np.asarray(P, float).reshape(-1, 2)
        z = A * (P[:, 0] + 1j * P[:, 1]) + B
        return np.stack([z.real, z.imag], 1)
    return f


# ---------------------------------------------------------------- curves

def bezier(p0, h0, h1, p1, n):
    t = np.linspace(0.0, 1.0, n)[:, None]
    p0, h0, h1, p1 = (np.asarray(x, float) for x in (p0, h0, h1, p1))
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * h0 + 3 * (1 - t) * t * t * h1 + (t ** 3) * p1


def bezier_pts(p0, h0, h1, p1, step=STEP):
    """Points along the curve about `step` mm apart (equal in arc length), ends exact."""
    dense = bezier(p0, h0, h1, p1, 400)
    L = S.polyline_len(dense)
    n = max(2, int(np.ceil(L / step)) + 1)
    P = S.resample(dense, n)
    P[0], P[-1] = p0, p1
    return P


def refresh_curve(e):
    """Remake a curved edge's pts from its end points and handles."""
    if e.get('ctrl'):
        P = bezier_pts(e['pts'][0], e['ctrl'][0], e['ctrl'][1], e['pts'][-1])
        e['pts'] = _r(P)


def fit_bezier(P, iters=8):
    """Cubic Bezier through the polyline's ends that fits it (least squares, parameters refined by projection).
    Returns (handles [[x, y], [x, y]], largest distance mm from the polyline's points to the curve)."""
    P = np.asarray(P, float)
    p0, p1 = P[0], P[-1]
    if len(P) < 3:
        return [_r([p0 + (p1 - p0) / 3])[0], _r([p0 + 2 * (p1 - p0) / 3])[0]], 0.0
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    t = cum / max(cum[-1], 1e-12)
    h0 = h1 = None
    for _ in range(iters):
        b0, b1, b2, b3 = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        R = P - np.outer(b0, p0) - np.outer(b3, p1)
        M = np.stack([b1, b2], 1)
        sol, *_ = np.linalg.lstsq(M, R, rcond=None)
        h0, h1 = sol[0], sol[1]
        # re-project every point to its nearest curve parameter
        ts = np.linspace(0, 1, 801)
        C = bezier(p0, h0, h1, p1, 801)
        d = ((P[:, None, :] - C[None]) ** 2).sum(-1)
        t = ts[d.argmin(1)]
    C = bezier(p0, h0, h1, p1, 2001)
    err = float(np.sqrt(((P[:, None, :] - C[None]) ** 2).sum(-1).min(1)).max())
    return _r([h0, h1]), err


def _split_bezier(p0, h0, h1, p1, tau):
    p0, h0, h1, p1 = (np.asarray(x, float) for x in (p0, h0, h1, p1))
    a, b, c = p0 + tau * (h0 - p0), h0 + tau * (h1 - h0), h1 + tau * (p1 - h1)
    d, e = a + tau * (b - a), b + tau * (c - b)
    m = d + tau * (e - d)
    return (p0, a, d, m), (m, e, c, p1)


def _tau_at_fraction(p0, h0, h1, p1, u):
    """Bezier parameter at the fraction u of its arc length."""
    n = 2001
    C = bezier(p0, h0, h1, p1, n)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(C, axis=0), axis=1))]
    return float(np.interp(u * cum[-1], cum, np.linspace(0, 1, n)))


# ---------------------------------------------------------------- twins

def twin_of(spec, name):
    for a, b in S.twins(spec):
        if name == a:
            return b
        if name == b:
            return a
    return None


def mirror_fn(pa, pb):
    """Layout map piece a -> piece b of a twin pair: mirror about a's bbox center onto b's (pattern_build.mirror_into).
    A piece onto itself: across its center line (self_reflect)."""
    if pa is pb:
        return self_reflect(pa)
    A, B = corners_all(pa), corners_all(pb)
    ca, cb = 0.5 * (A.min(0) + A.max(0)), 0.5 * (B.min(0) + B.max(0))
    k = 1 if 'y' in (pa.get('mirror_plane'), pb.get('mirror_plane')) else 0      # a front / back pair: across y

    def M(q):
        q = np.asarray(q, float).reshape(-1, 2) - ca
        q[:, k] *= -1.0
        return q + cb
    return M


TWIN_LINE_MM = 3.0     # a twin's internal line: the mirrored line within this (median), as pattern_build.mirror_into


def twin_line(spec, name, lid):
    """(twin, its line id) of internal line lid of `name`: the twin's line nearest the mirror image of it (a BOM's twins
    number their lines apart; a Mirror Copy keeps the ids), only when that line's nearest back is lid (two parallel
    lines within TWIN_LINE_MM, a tuck's sides, could pick each other's otherwise); or None."""
    tw = twin_of(spec, name)
    if tw is None:
        return None
    t = _nearest_mirrored(spec, name, lid, tw)
    if t is None or _nearest_mirrored(spec, tw, t, name) != lid:
        return None
    return tw, t


def _nearest_mirrored(spec, name, lid, tw):
    """The id of tw's internal line nearest the mirror image of name's line lid (median within TWIN_LINE_MM), or None."""
    pa, pb = piece(spec, name), piece(spec, tw)
    it = next((x for x in pa.get('internals', []) if x['id'] == lid), None)
    if it is None or len(it['pts']) < 2:
        return None
    Pm = mirror_fn(pa, pb)(it['pts'])
    best = None
    for x in pb.get('internals', []):
        Q = np.asarray(x['pts'], float)
        if x.get('kind') == 'baseline' or len(Q) < 2:
            continue
        d = float(np.median(_dist_to_polyline(Pm, Q)))
        if d < TWIN_LINE_MM and (best is None or d < best[0]):
            best = (d, x['id'])
    return best[1] if best else None


def _dist_to_polyline(Q, P):
    a, b = P[:-1], P[1:]
    ab = b - a
    t = np.clip(((Q[:, None, :] - a[None]) * ab[None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-12)[None], 0, 1)
    return np.linalg.norm(a[None] + t[..., None] * ab[None] - Q[:, None, :], axis=2).min(1)


def corners_all(pc):
    return np.vstack([np.asarray(e['pts'], float) for e in pc['edges']])


def twin_corner_map(spec, name):
    """(twin name, {corner k of `name`: corner j of the twin}, M) when every corner has a mirror image within
    TWIN_TOL, else (twin name or None, None, None)."""
    tw = twin_of(spec, name)
    if tw is None:
        return None, None, None
    pa, pb = piece(spec, name), piece(spec, tw)
    M = mirror_fn(pa, pb)
    ca, cb = M(corners(pa)), corners(pb)
    if len(ca) != len(cb):
        return tw, None, None
    d = np.linalg.norm(ca[:, None, :] - cb[None], axis=-1)
    j = d.argmin(1)
    if d[np.arange(len(ca)), j].max() > TWIN_TOL or len(set(j.tolist())) != len(j):
        return tw, None, None
    return tw, {int(k): int(v) for k, v in enumerate(j)}, M


def outline_centroid(pc):
    """The area centroid of a piece's outline (layout mm). A symmetric piece's centroid lies on its center line,
    wherever the piece lies and however it is turned."""
    B = S.Outline(pc).B
    x, y = B[:, 0], B[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cr = x * y1 - x1 * y
    A = cr.sum() / 2
    if abs(A) < 1e-9:
        return B.mean(0)
    return np.array([((x + x1) * cr).sum(), ((y + y1) * cr).sum()]) / (6 * A)


def axis_angle(pc):
    """The angle (deg, counterclockwise from vertical) of the piece's center line: 0 until the piece is turned (R)
    while edited symmetrically (user 2026-10-27: R broke the symmetry). Kept in 'mirror_axis' [x, y, deg]."""
    a = pc.get('mirror_axis')
    return float(a[2]) if a else 0.0


def self_axis(pc, c=None):
    """(a point on the center line, its unit direction). The point: c, else the outline's centroid."""
    a = np.radians(axis_angle(pc))
    return (outline_centroid(pc) if c is None else np.asarray(c, float)), np.array([-np.sin(a), np.cos(a)])


def self_reflect(pc, c=None, across=False):
    """The map of a piece onto itself across its center line (self_axis); across: across the line through the same
    center at right angles to it (a gusset symmetric front to back under a front / back copy of its panel)."""
    c, u = self_axis(pc, c)
    s = -1.0 if across else 1.0

    def M(q):
        d = np.asarray(q, float).reshape(-1, 2) - c
        return c + s * (2 * (d @ u)[:, None] * u - d)
    return M


def reflect_vec(pc, v):
    """A move v (mm) mirrored across the piece's center line: (-dx, dy) for an upright piece."""
    _, u = self_axis(pc, (0.0, 0.0))
    v = np.asarray(v, float)
    return 2 * float(v @ u) * u - v


def _store_axis(pc, c=None):
    c = outline_centroid(pc) if c is None else np.asarray(c, float)
    pc['mirror_axis'] = [round(float(c[0]), 4), round(float(c[1]), 4), round(axis_angle(pc), 6)]


def self_mirror_map(pc):
    """{corner k: corner j} of a piece symmetric about its center line (a front cut as one piece; the line through its
    centroid, upright or turned as 'mirror_axis' says), when every corner mirrors onto a corner within SELF_TOL; else
    None. A corner on the line maps to itself."""
    M = self_reflect(pc)
    C = corners(pc)
    d = np.linalg.norm(M(C)[:, None, :] - C[None], axis=-1)
    j = d.argmin(1)
    if d[np.arange(len(C)), j].max() > SELF_TOL or sorted(j.tolist()) != list(range(len(C))):
        return None
    return {int(k): int(v) for k, v in enumerate(j)}


def symmetric_edit(pc):
    """The piece's corner map when it is edited symmetrically ('mirror_edit' switched on, user 2026-10-20) and is
    symmetric now, else None. While so, its center line is kept in 'mirror_axis' (Symmetrize finds it there after
    one side was changed with the switch off)."""
    if not pc.get('mirror_edit'):
        return None
    m = self_mirror_map(pc)
    if m is not None:
        _store_axis(pc)
    return m


def _sym_deltas(pc, deltas):
    """Corner moves with their mirror images across the center line of a piece edited symmetrically: a corner gets
    the mean of its own move and its mirror's mirrored one (a corner on the line, or an edge across it, then moves
    only up and down, and the piece stays symmetric)."""
    cmap = symmetric_edit(pc)
    if cmap is None:
        return deltas
    acc = {}
    for k, d in deltas.items():
        acc.setdefault(k, []).append(tuple(map(float, d)))
        acc.setdefault(cmap[k], []).append(tuple(reflect_vec(pc, d)))
    return {k: tuple(np.mean(v, 0)) for k, v in acc.items()}


CENTER_SNAP = 3.0     # mm: a point put on an edge across the center line this close to the line goes onto it


def _self_mirror_place(pc, eid, t):
    """(edge id, t) of the mirror image across the center line of the point (eid, t) of a piece edited symmetrically,
    or None (not edited symmetrically, or no mirror edge)."""
    smap = symmetric_edit(pc)
    if smap is None:
        return None
    ks, rs = _twin_edge(pc, pc, smap, edge_index(pc, eid))
    if ks is None:
        return None
    return pc['edges'][ks]['id'], (1.0 - t) if rs else t


def _twin_edge(pa, pb, cmap, ka):
    """Index in pb of the edge that mirrors edge ka of pa, and whether it runs the other way."""
    n = len(pa['edges'])
    s, e = cmap[ka], cmap[(ka + 1) % n]
    nb = len(pb['edges'])
    if (s + 1) % nb == e:
        return s, False
    if (e + 1) % nb == s:
        return e, True
    return None, None


# ---------------------------------------------------------------- moving

def _move_corners_one(pc, deltas):
    """deltas: {corner k: (dx, dy)}. Each edge between two corners is carried by the similarity that takes its old
    ends to the new ones (a straight edge stays straight, a curve keeps its shape, handles move with it)."""
    n = len(pc['edges'])
    C = corners(pc)
    newC = C.copy()
    for k, d in deltas.items():
        newC[k] = C[k] + np.asarray(d, float)
    # an internal line's end on an edge that changes rides on that edge (the same carry as the edge's own points):
    # the ends were free points, and moving an outline edge left a line drawn to it short of it or out of the piece
    # (user 2026-11-11). Found first, on the edges as they were; an end at a corner goes with the corner.
    ends, seen = [], set()
    for k, e in enumerate(pc['edges']):
        k1 = (k + 1) % n
        if k not in deltas and k1 not in deltas:
            continue
        P0 = np.asarray(e['pts'], float)
        for it in pc.get('internals', []):
            if it.get('closed') or it.get('kind') == 'baseline' or len(it.get('pts') or ()) < 2:
                continue
            for j in (0, len(it['pts']) - 1):
                if (id(it), j) in seen:
                    continue
                q = np.asarray(it['pts'][j], float).reshape(1, 2)
                if _dist_to_polyline(q, P0)[0] <= LINE_END_ON_EDGE:
                    seen.add((id(it), j))
                    ends.append((it, j, _similarity(C[k], C[k1], newC[k], newC[k1])))
    for it, j, f in ends:                          # one end at a time, the line following it (curves keep shape)
        q = np.asarray(it['pts'][j], float)
        _move_internal_one(it, 'start' if j == 0 else 'end', f([q])[0] - q)
    for k, e in enumerate(pc['edges']):
        k1 = (k + 1) % n
        if k not in deltas and k1 not in deltas:
            continue
        f = _similarity(C[k], C[k1], newC[k], newC[k1])
        P = f(e['pts'])
        P[0], P[-1] = newC[k], newC[k1]
        e['pts'] = _r(P)
        if e.get('ctrl'):
            e['ctrl'] = _r(f(e['ctrl']))


SEAM_SNAP = 2.0       # mm: a seam end this close to an edge's end goes with that end when the edge changes length


def _keep_seam_ends(spec, pc, deltas):
    """Run before moving corners `deltas` of piece pc. Returns a function that, run after, puts back every seam end
    (and notch and measure point: point_refs) inside an edge (0 < t < 1) whose edge had one end moved at the same arc distance from the end that stayed, not
    at the same fraction: a seam split inside an edge is where a partner's corner is sewn (a pants inseam sewn to
    the back leg in two seams), and lengthening the edge at its moved end must not slide that point along with it
    (the hem let down 30 mm slid it 28 mm and left both seams 28 mm off their partners, 2026-10-08). An end within
    SEAM_SNAP of the moved end goes with it; an end the shortened edge can no longer hold at that distance keeps its
    fraction."""
    n = len(pc['edges'])
    one_end = {}
    for k, e in enumerate(pc['edges']):
        a, b = k in deltas, (k + 1) % n in deltas
        if a != b:
            one_end[e['id']] = (k, 'start' if a else 'end', S.polyline_len(e['pts']))
    ends = []
    for get, put in point_refs(spec, pc['name']):    # seam ends, notches, measure points: the same rule
        eid, t = get()
        if eid in one_end and 1e-9 < t < 1 - 1e-9:
            ends.append((put, eid, t))

    def restore():
        for put, eid, t in ends:
            k, moved, L0 = one_end[eid]
            L1 = S.polyline_len(pc['edges'][k]['pts'])
            if L1 < 1e-9:
                continue
            if moved == 'end':                     # the start stayed
                if (1 - t) * L0 <= SEAM_SNAP:
                    t1 = 1 - (1 - t) * L0 / L1
                else:
                    t1 = t * L0 / L1
            else:                                  # the end stayed
                if t * L0 <= SEAM_SNAP:
                    t1 = t * L0 / L1
                else:
                    t1 = 1 - (1 - t) * L0 / L1
            if not 0.0 < t1 < 1.0:                 # the edge got shorter than the distance: keep the fraction
                t1 = t
            put(eid, min(max(t1, 1e-6), 1 - 1e-6))
    return restore


def move_corners(spec, name, deltas, twins=True, merge=False):
    """Move corners of piece `name` by deltas {k: (dx, dy)} mm; the twin gets the mirror-image move. A seam end
    inside an edge with one end moved keeps its distance from the end that stayed (_keep_seam_ends). merge: one corner
    moved onto a corner next to it (within MIN_EDGE) becomes that corner (user 2026-10-27, _merge_dropped).
    Returns the names of the pieces changed."""
    pc = piece(spec, name)
    tw, cmap, _ = twin_corner_map(spec, name) if twins else (None, None, None)
    smap0 = symmetric_edit(pc)
    moved = list(deltas)
    deltas = _sym_deltas(pc, deltas)
    keep = _keep_seam_ends(spec, pc, deltas)
    _move_corners_one(pc, deltas)
    keep()
    out = [name]
    if cmap:
        ptw = piece(spec, tw)
        dtw = {cmap[k]: (-float(d[0]), float(d[1])) for k, d in deltas.items()}
        keep = _keep_seam_ends(spec, ptw, dtw)
        _move_corners_one(ptw, dtw)
        keep()
        out.append(tw)
    if merge and len(moved) == 1:
        _merge_dropped(spec, pc, moved[0], smap0, tw if cmap else None, cmap)
    return out


def _merge_dropped(spec, pc, k, smap, tw, cmap):
    """Corner k, just moved, on the corner next to it (within MIN_EDGE): k goes, that corner stays. Its mirror across
    the center line goes too (a piece edited symmetrically), unless the corner it met is its mirror (the two met on
    the line: one corner stays there). The twin loses the same corners. Returns whether it merged."""
    C = corners(pc)
    n = len(C)
    near = [(float(np.linalg.norm(C[k] - C[j])), j) for j in ((k - 1) % n, (k + 1) % n)]
    d, j = min(near)
    if d >= MIN_EDGE:
        return False
    ks = {k}
    if smap and smap[k] not in (k, j):
        ks.add(smap[k])
    if n - len(ks) < 3:
        return False
    if smap and smap[k] == j:                    # met on the line: the corner that stays goes onto it exactly
        c, u = self_axis(pc)
        q = C[j] - (C[j] - c) @ np.array([u[1], -u[0]]) * np.array([u[1], -u[0]])
        _move_corners_one(pc, {j: tuple(q - C[j])})
    for x in sorted(ks, reverse=True):
        _join_one(spec, pc, x)
    if tw and cmap:
        pb = piece(spec, tw)
        for x in sorted({cmap[x] for x in ks}, reverse=True):
            _join_one(spec, pb, x)
    return True


def move_edge(spec, name, eid, delta, twins=True):
    pc = piece(spec, name)
    k = edge_index(pc, eid)
    n = len(pc['edges'])
    return move_corners(spec, name, {k: delta, (k + 1) % n: delta}, twins)


def set_handle(spec, name, eid, which, pos, twins=True):
    """Put handle `which` (0: next to the edge's start, 1: next to its end) of a curved edge at pos (layout mm)."""
    pc = piece(spec, name)
    tw, cmap, M = twin_corner_map(spec, name) if twins else (None, None, None)
    k = edge_index(pc, eid)
    e = pc['edges'][k]
    if not e.get('ctrl'):
        raise ValueError(rpt("edge {0} is not curved", eid))
    smap = symmetric_edit(pc)
    Ms = self_reflect(pc) if smap else None          # before the edit: the center line while symmetric
    e['ctrl'][which] = _r([pos])[0]
    refresh_curve(e)
    if smap:
        # edited symmetrically: the mirror image of the handle across the center line (on the same edge when the
        # edge runs across the line)
        ks, rs = _twin_edge(pc, pc, smap, k)
        if ks is not None and pc['edges'][ks].get('ctrl'):
            w2 = (1 - which) if rs else which
            if (ks, w2) != (k, which):
                pc['edges'][ks]['ctrl'][w2] = _r(Ms([pos]))[0]
                refresh_curve(pc['edges'][ks])
    out = [name]
    if cmap:
        pb = piece(spec, tw)
        kb, rev = _twin_edge(pc, pb, cmap, k)
        if kb is not None and pb['edges'][kb].get('ctrl'):
            eb = pb['edges'][kb]
            eb['ctrl'][(1 - which) if rev else which] = _r(M([pos]))[0]
            refresh_curve(eb)
            out.append(tw)
    return out


def make_curve(spec, name, eid, twins=True):
    """Give an edge handles fitted to its present shape. Returns (pieces changed, largest fit error mm)."""
    pc = piece(spec, name)
    tw, cmap, M = twin_corner_map(spec, name) if twins else (None, None, None)
    k = edge_index(pc, eid)
    e = pc['edges'][k]
    smap = symmetric_edit(pc)
    Ms = self_reflect(pc) if smap else None
    ctrl, err = fit_bezier(e['pts'])
    e['ctrl'] = ctrl
    refresh_curve(e)
    if smap:                                    # edited symmetrically: the mirror edge curved alike
        ks, rs = _twin_edge(pc, pc, smap, k)
        if ks is not None and ks != k:
            h = _r(Ms(ctrl))
            pc['edges'][ks]['ctrl'] = h[::-1] if rs else h
            refresh_curve(pc['edges'][ks])
    out = [name]
    if cmap:
        pb = piece(spec, tw)
        kb, rev = _twin_edge(pc, pb, cmap, k)
        if kb is not None:
            eb = pb['edges'][kb]
            h = _r(M(ctrl))
            eb['ctrl'] = h[::-1] if rev else h
            refresh_curve(eb)
            out.append(tw)
    return out, err


def make_straight(spec, name, eid, twins=True):
    """Drop an edge's handles; with straight=True its points become the chord."""
    pc = piece(spec, name)
    tw, cmap, _ = twin_corner_map(spec, name) if twins else (None, None, None)
    k = edge_index(pc, eid)
    targets = [(pc, k)]
    smap = symmetric_edit(pc)
    if smap:                                    # edited symmetrically: the mirror edge straightened too
        ks, _ = _twin_edge(pc, pc, smap, k)
        if ks is not None and ks != k:
            targets.append((pc, ks))
    if cmap:
        pb = piece(spec, tw)
        kb, _ = _twin_edge(pc, pb, cmap, k)
        if kb is not None:
            targets.append((pb, kb))
    for p, i in targets:
        e = p['edges'][i]
        e.pop('ctrl', None)
        e['pts'] = [e['pts'][0], e['pts'][-1]]
    return list(dict.fromkeys(p['name'] for p, _ in targets))


# ---------------------------------------------------------------- splitting and joining

def edit_copy(spec, names, memo=None):
    """Copy edited geometry and its mutable references; share unrelated geometry and immutable metadata."""
    memo = {} if memo is None else memo
    out = dict(spec)
    out['pieces'] = [copy.deepcopy(pc, memo) if pc['name'] in names else pc for pc in spec['pieces']]
    out['seams'] = copy.deepcopy(spec.get('seams', []), memo)
    # the placement too: transform_piece / scale_piece move a piece's anchor in place, and a drag edits a fresh copy
    # on every redraw, so a shared placement took the move again each time (an anchor 135 m off its piece)
    for feature in ('placement', 'measure', 'elastic', 'attach', 'zippers'):
        if feature in spec:
            out[feature] = copy.deepcopy(spec[feature], memo)
    return out


def outline_sides(spec, name):
    """Saved outline intervals, using each feature's actual owner convention."""
    for sm in spec.get('seams', []):
        for key in ('a', 'b'):
            side = sm[key]
            if side['piece'] == name and 'line' not in side:
                yield side
    for el in spec.get('elastic') or []:
        side = el.get('side')
        # Elastic's outer piece is authoritative (solver_entries uses it too).
        if el.get('piece') == name and isinstance(side, dict) and 'line' not in side:
            yield side
    for entry in spec.get('attach') or []:
        side = entry.get('side')
        if isinstance(side, dict) and side.get('piece') == name and 'line' not in side:
            yield side


def point_refs(spec, name):
    """Every point held as (edge id, t) on piece `name`'s outline, as [(get, set)]: get() -> (edge id, t),
    set(edge id, t). Outline interval ends (seams/elastic/held attach), notches and pattern measure points.
    An edit that re-states edges or fractions goes through this one list, so none of them is left behind."""
    out = []

    def seam_end(sd, end):
        def get():
            return sd[end][0], float(sd[end][1])

        def put(e, t):
            sd[end] = [e, round(float(t), 6)]
        return get, put

    def held(d, ek='edge', tk='t'):
        def get():
            return d[ek], float(d[tk])

        def put(e, t):
            d[ek], d[tk] = e, round(float(t), 6)
        return get, put
    for side in outline_sides(spec, name):
        out += [seam_end(side, 'from'), seam_end(side, 'to')]
    pc = piece(spec, name)
    out += [held(n) for n in pc.get('notches') or []]
    for m in (spec.get('measure') or {}).get('pattern') or []:
        for k in ('a', 'b'):
            q = m.get(k)
            if isinstance(q, dict) and q.get('piece') == name and 'edge' in q and 't' in q:
                out.append(held(q))
    return out


def _remap_ends(spec, name, fn):
    """fn(edge id, t) -> (edge id, t) applied to every point held on piece `name`'s outline (point_refs)."""
    for get, put in point_refs(spec, name):
        e, t = get()
        put(*fn(e, t))


def _split_one(spec, pc, k, u, new_id):
    e = pc['edges'][k]
    P = np.asarray(e['pts'], float)
    if e.get('ctrl'):
        tau = _tau_at_fraction(P[0], e['ctrl'][0], e['ctrl'][1], P[-1], u)
        (a0, a1, a2, a3), (b0, b1, b2, b3) = _split_bezier(P[0], e['ctrl'][0], e['ctrl'][1], P[-1], tau)
        e1 = {'id': e['id'], 'pts': _r([a0, a3]), 'ctrl': _r([a1, a2])}
        e2 = {'id': new_id, 'pts': _r([b0, b3]), 'ctrl': _r([b1, b2])}
        refresh_curve(e1)
        refresh_curve(e2)
        # the seam fractions below are exact for the point lists; the remade halves differ from them by sampling only
    else:
        cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
        s = u * cum[-1]
        m = np.array([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])])
        i = int(np.searchsorted(cum, s, side='right'))     # cum[i - 1] <= s < cum[i]: P[i] is the first point beyond m
        A = np.vstack([P[:i], m]) if np.linalg.norm(P[i - 1] - m) > 1e-9 else P[:i]
        B = np.vstack([m, P[i:]])
        e1 = dict(e, pts=_r(A))
        e2 = {'id': new_id, 'pts': _r(B)}
        e1.pop('ctrl', None)
    pc['edges'][k:k + 1] = [e1, e2]
    old = e['id']

    def fn(eid, t):
        if eid != old:
            return eid, t
        if t <= u:
            return old, (t / u if u > 0 else 0.0)
        return new_id, ((t - u) / (1 - u) if u < 1 else 1.0)
    _remap_ends(spec, pc['name'], fn)
    from . import zippers
    zippers.replace_edges(spec, pc['name'], {old: [old, new_id]})


def _split_at(spec, pc, eid, us):
    """Split edge eid at the increasing fractions us (of its length now). Returns {u: id of the edge starting there}."""
    out, done, cur = {}, 0.0, eid
    for u in us:
        nid = new_edge_id(pc)
        _split_one(spec, pc, edge_index(pc, cur), (u - done) / (1.0 - done), nid)
        out[u], cur, done = nid, nid, u
    return out


def split_edge(spec, name, eid, u, twins=True):
    """Split edge `eid` at the fraction u (0..1) of its length into eid and a new edge. A piece edited symmetrically is
    split at the mirror point too (user 2026-10-26: K broke the symmetry, a neckline could not be made); on an edge
    across the center line a point within CENTER_SNAP of the line goes onto it. The twin gets the mirror image of
    every split. Returns {piece: new edge id at u}."""
    if not 1e-4 < u < 1 - 1e-4:
        raise ValueError(rpt("split point on a corner"))
    pc = piece(spec, name)
    tw, cmap, _ = twin_corner_map(spec, name) if twins else (None, None, None)
    jobs = {eid: [u]}
    mp = _self_mirror_place(pc, eid, u)
    if mp:
        e2, u2 = mp
        if e2 == eid and abs(u2 - u) * S.polyline_len(pc['edges'][edge_index(pc, eid)]['pts']) < 2 * CENTER_SNAP:
            u = 0.5
            jobs = {eid: [u]}
        elif 1e-4 < u2 < 1 - 1e-4:
            jobs.setdefault(e2, []).append(u2)
    twin_jobs, main_tw = {}, None
    if cmap:
        pb = piece(spec, tw)
        for e, us in jobs.items():
            kb, rev = _twin_edge(pc, pb, cmap, edge_index(pc, e))
            if kb is not None:
                twin_jobs[pb['edges'][kb]['id']] = [(1 - x) if rev else x for x in us]
                if e == eid:
                    main_tw = (pb['edges'][kb]['id'], (1 - u) if rev else u)
    made = {e: _split_at(spec, pc, e, sorted(us)) for e, us in jobs.items()}
    out = {name: made[eid][u]}
    if twin_jobs:
        pb = piece(spec, tw)
        tmade = {e: _split_at(spec, pb, e, sorted(us)) for e, us in twin_jobs.items()}
        if main_tw:
            out[tw] = tmade[main_tw[0]][main_tw[1]]
    return out


def _is_straight(e, tol_mm=0.1):
    """An edge with no curve handles whose points all lie on its chord (within tol_mm)."""
    if e.get('ctrl'):
        return False
    P = np.asarray(e['pts'], float)
    if len(P) <= 2:
        return True
    d = P[-1] - P[0]
    L = float(np.linalg.norm(d))
    if L < 1e-9:
        return False
    return float(np.abs((P[:, 0] - P[0, 0]) * d[1] - (P[:, 1] - P[0, 1]) * d[0]).max()) / L <= tol_mm


def _join_one(spec, pc, k, keep_shape=False):
    """Remove corner k: edge k-1 and edge k become edge k-1: two straight edges become a straight line between the
    corners either side (user 2026-10-20: a deleted point keeps their bend), a curve stays as drawn. Seam ends on them keep their fraction of the
    two edges' length. keep_shape: the point stays on the joined edge (the outline keeps its shape, only the corner
    goes: a moved dart's closed mouth, core/dart.py)."""
    n = len(pc['edges'])
    if n <= 3:
        raise ValueError(rpt("a piece needs at least 3 corners"))
    from . import zippers
    zippers.check_join(spec, pc['name'], [k])
    k0 = (k - 1) % n
    e1, e2 = pc['edges'][k0], pc['edges'][k]
    L1, L2 = S.polyline_len(e1['pts']), S.polyline_len(e2['pts'])
    P1, P2 = np.asarray(e1['pts'], float), np.asarray(e2['pts'], float)
    if _is_straight(e1) and _is_straight(e2) and not keep_shape:
        P = np.vstack([P1[:1], P2[-1:]])
    else:                                  # a curve kept as drawn (a point put on a curve and taken off again)
        P = np.vstack([P1, P2[1:]])
    joined = dict(e1, pts=_r(P))
    joined.pop('ctrl', None)
    pc['edges'][k0] = joined
    del pc['edges'][k]
    a, b, Lt = e1['id'], e2['id'], max(L1 + L2, 1e-12)

    def fn(eid, t):
        if eid == a:
            return a, t * L1 / Lt
        if eid == b:
            return a, (L1 + t * L2) / Lt
        return eid, t
    _remap_ends(spec, pc['name'], fn)
    from . import zippers
    zippers.replace_edges(spec, pc['name'], {a: [a], b: [a]})


def join_at_corner(spec, name, k, twins=True):
    """Remove corner k of piece `name` (the two edges meeting there become one). Returns the pieces changed."""
    pc = piece(spec, name)
    tw, cmap, _ = twin_corner_map(spec, name) if twins else (None, None, None)
    smap = symmetric_edit(pc)
    ks = sorted({k, smap[k]} if smap else {k}, reverse=True)    # edited symmetrically: the mirror corner goes too
    if len(pc['edges']) - len(ks) < 3:
        raise ValueError(rpt("a piece needs at least 3 corners"))
    jbs = sorted({cmap[x] for x in ks}, reverse=True) if cmap else []
    from . import zippers
    zippers.check_join(spec, name, ks)
    if jbs:
        zippers.check_join(spec, tw, jbs)
    for x in ks:                                   # the higher index first: the lower keeps its number
        _join_one(spec, pc, x)
    out = [name]
    if jbs:
        for x in jbs:
            _join_one(spec, piece(spec, tw), x)
        out.append(tw)
    return out


# ---------------------------------------------------------------- new pieces

def unique_name(spec, base):
    names = {p['name'] for p in spec['pieces']}
    if base not in names:
        return base
    k = 2
    while f"{base}.{k:03d}" in names:
        k += 1
    return f"{base}.{k:03d}"


def add_polygon(spec, P, name="Piece", fabric=""):
    """A new piece with straight edges through the corners P (any winding; stored counter-clockwise)."""
    P = np.asarray(P, float)
    if len(P) < 3:
        raise ValueError(rpt("a piece needs at least 3 corners"))
    if signed_area(P) < 0:
        P = P[::-1]
    n = len(P)
    nm = unique_name(spec, name)
    spec['pieces'].append({'name': nm, 'fabric': fabric, 'enabled': True, 'role': 'auto', 'internals': [],
                           'edges': [{'id': f"E{k + 1}", 'pts': _r([P[k], P[(k + 1) % n]])} for k in range(n)]})
    return nm


KAPPA = 0.5522847498      # a quarter ellipse as one cubic Bezier: handle length / radius


def ellipse_quarters(center, rx, ry):
    """The ellipse as 4 cubic Beziers from its right, top, left and bottom points (counter-clockwise):
    [(p0, h0, h1, p1)] x 4."""
    c = np.asarray(center, float)
    out = []
    for k in range(4):
        a0, a1 = k * np.pi / 2, (k + 1) * np.pi / 2
        p0 = c + [rx * np.cos(a0), ry * np.sin(a0)]
        p1 = c + [rx * np.cos(a1), ry * np.sin(a1)]
        t0 = np.array([-rx * np.sin(a0), ry * np.cos(a0)])
        t1 = np.array([-rx * np.sin(a1), ry * np.cos(a1)])
        out.append((p0, p0 + KAPPA * t0, p1 - KAPPA * t1, p1))
    return out


def ellipse_points(center, rx, ry, step=STEP):
    """The ellipse as one closed point list (first point repeated at the end), about `step` mm apart."""
    P = [bezier_pts(*q, step=step)[:-1] for q in ellipse_quarters(center, rx, ry)]
    P = np.vstack(P)
    return np.vstack([P, P[:1]])


def add_ellipse(spec, center, rx, ry, name="Ellipse", fabric=""):
    """A new piece: an ellipse of radii rx, ry (mm), 4 curved edges (E1..E4 from its right, top, left and bottom
    points), so seams, notches and K (split) work as on any curved edge."""
    if rx <= 0 or ry <= 0:
        raise ValueError(rpt("an ellipse needs a width and a height"))
    nm = unique_name(spec, name)
    edges = []
    for k, (p0, h0, h1, p1) in enumerate(ellipse_quarters(center, rx, ry)):
        e = {'id': f"E{k + 1}", 'pts': _r([p0, p1]), 'ctrl': _r([h0, h1])}
        refresh_curve(e)
        edges.append(e)
    spec['pieces'].append({'name': nm, 'fabric': fabric, 'enabled': True, 'role': 'auto', 'internals': [],
                           'edges': edges})
    return nm


def clone_shape(spec, name, lid, gap=20.0, sew=False):
    """A new piece from the closed internal shape `lid` of piece `name` (a pocket drawn on the front: Clone as Pattern,
    Fable r2 b-5, user OK 2026-10-28): its points are the corners, its curved spans the curved edges. The shape stays;
    the piece has the source's fabric, no seams, is not placed, and lies `gap` mm right of its piece at the shape's
    height, farther right past any piece it would lie on (right of every piece, it was hard to find, user 2026-10-28).
    sew: each of its edges sewn to its span of the shape (Clone as Pattern (+Sew): a pocket sewn on where it was
    drawn, user 2026-10-28), the seams one group. Returns its name."""
    from . import iline as IL
    pc = piece(spec, name)
    it = internal(pc, lid)
    if it is None or not it.get('closed'):
        raise ValueError(rpt("Pick a closed internal shape first"))
    N, C, _, _ = IL.shape(it)
    if len(N) < 3:
        raise ValueError(rpt("a piece needs at least 3 corners"))
    N = np.asarray(N, float)
    if sew and np.linalg.norm(np.asarray(it['pts'][0], float) - np.asarray(it['pts'][-1], float)) > 1e-6:
        it['pts'] = [list(map(float, q)) for q in it['pts']] + [list(map(float, it['pts'][0]))]   # the closing span on the line
    P = np.asarray(it['pts'], float)                # where each vertex is along the line (0..1)
    cum = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    at = [float(cum[int(np.linalg.norm(P - q, axis=1).argmin())] / cum[-1]) for q in N]
    rev = False
    if signed_area(N) < 0:                          # counter-clockwise: the spans backwards, each handle pair swapped
        n = len(N)
        N = N[::-1]
        C = [None if c is None else np.asarray(c, float)[::-1] for c in C[::-1]]
        C = C[1:] + C[:1] if n > 1 else C        # span k ran N[k] -> N[k+1]; reversed, span n-1-k runs back
        at, rev = at[::-1], True
    x = corners_all(pc)[:, 0].max() + gap
    w = float(np.ptp(N[:, 0]))
    y0, y1 = N[:, 1].min(), N[:, 1].max()
    boxes = [(B[:, 0].min(), B[:, 0].max()) for B in (corners_all(p) for p in spec['pieces'])
             if B[:, 1].max() > y0 and B[:, 1].min() < y1]
    moved = True
    while moved:                                    # past every piece its box would lie on, at the shape's height
        moved = False
        for a0, a1 in boxes:
            if a0 < x + w + gap and a1 > x - gap * 0.5:
                x, moved = a1 + gap, True
    dx = np.array([x - N[:, 0].min(), 0.0])
    nm = unique_name(spec, f"{name}_{lid}")
    edges = []
    for k in range(len(N)):
        e = {'id': f"E{k + 1}", 'pts': _r([N[k] + dx, N[(k + 1) % len(N)] + dx])}
        if C[k] is not None:
            e['ctrl'] = _r(np.asarray(C[k], float) + dx)
            refresh_curve(e)
        edges.append(e)
    spec['pieces'].append({'name': nm, 'fabric': pc.get('fabric', ''), 'enabled': True, 'role': 'auto',
                           'internals': [], 'edges': edges})
    if sew:
        used = {sm['id'] for sm in spec.get('seams', [])}
        ids = (f"U{i}" for i in range(1, 100000) if f"U{i}" not in used)
        n, group = len(N), None
        for k in range(n):
            t0, t1 = at[k], at[(k + 1) % n]
            if not rev and t1 <= t0:                # the span ending at the line's start ends at 1
                t1 = 1.0
            if rev and t0 <= t1:                    # backwards, the span leaving the start leaves from 1
                t0 = 1.0
            sid = next(ids)
            group = group or ("UG" + sid[1:])
            spec.setdefault('seams', []).append({
                'id': sid, 'a': {'piece': nm, 'from': [edges[k]['id'], 0.0], 'to': [edges[k]['id'], 1.0], 'dir': 1},
                'b': {'piece': name, 'line': lid, 'from': round(t0, 6), 'to': round(t1, 6)},
                'source': 'user', 'check': '', 'enabled': True, 'group': group})
    return nm


def rect_points(p0, p1):
    """The rectangle with opposite corners p0, p1 (sides along x and y), counter-clockwise from its lower left."""
    (x0, y0), (x1, y1) = np.minimum(p0, p1), np.maximum(p0, p1)
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)


def shape_box(p0, cur, w=None, h=None, square=False, center=False):
    """(lower-left, upper-right) of the shape dragged from p0 to cur (mm). w, h: typed full width / height. square:
    w = h. center: p0 is the middle."""
    d = np.asarray(cur, float) - np.asarray(p0, float)
    sx, sy = (1.0 if d[0] >= 0 else -1.0), (1.0 if d[1] >= 0 else -1.0)
    k = 2.0 if center else 1.0
    ww = w if w is not None else abs(d[0]) * k
    hh = h if h is not None else abs(d[1]) * k
    if square:
        if w is not None and h is None:
            hh = ww
        elif h is not None and w is None:
            ww = hh
        elif w is None:
            ww = hh = max(ww, hh)
    p0 = np.asarray(p0, float)
    if center:
        a, b = p0 - [ww / 2, hh / 2], p0 + [ww / 2, hh / 2]
    else:
        a, b = p0, p0 + [sx * ww, sy * hh]
    return np.minimum(a, b), np.maximum(a, b)


def mirror_copy(spec, name, gap=50.0, x_axis=None, plane='x'):
    """A mirrored copy of the piece beside it (x mirrored), made its twin (same family, one of the two mirrored).
    Seams are not copied here (mirror_group / seam_twin.mirror_seams_group do). x_axis: the mirror line (layout mm),
    by default just right of the piece (mirror_group: right of every piece). What the piece buttons onto (closes_onto) is not copied: mirror_group sets
    the copy's. plane 'y' (front / back, user 2026-11-11): the copy turned upside down above the piece (x_axis is
    then the horizontal line, its y; the Mirror Copy operator then moves the copies beside the originals), and both pieces hold mirror_plane 'y'."""
    pc = piece(spec, name)
    k = 1 if plane == 'y' else 0
    if x_axis is None:
        x_axis = corners_all(pc)[:, k].max() + 0.5 * gap      # mirror line just right of (above) the piece
    if plane == 'y':
        pc['mirror_plane'] = 'y'
    else:
        pc.pop('mirror_plane', None)

    def M(q):
        q = np.array(q, float).reshape(-1, 2)
        q[:, k] = 2 * x_axis - q[:, k]
        return q
    edges = []
    for e in reversed(pc['edges']):                  # mirroring turns the outline clockwise: run it backwards
        ne = {'id': e['id'], 'pts': _r(M(e['pts'])[::-1])}
        if e.get('ctrl'):
            ne['ctrl'] = _r(M(e['ctrl'])[::-1])
        edges.append(ne)
    fam = pc.get('family') or pc.get('bom', {}).get('family') or pc['name']
    pc['family'] = fam
    cp = {k: v for k, v in pc.items() if k not in ('edges', 'internals', 'name', 'bom', 'mirrored', 'closes_onto')}
    cp.update(name=unique_name(spec, name + "_R" if not pc.get('mirrored') else name + "_L"), edges=edges,
              internals=[xform_internal(copy.deepcopy(it), M) for it in pc.get('internals', [])],
              family=fam, mirrored=not pc.get('mirrored', False))
    if pc.get('notches'):                            # the edges run backwards: a fraction t is 1 - t
        cp['notches'] = [dict(n, t=round(1.0 - float(n['t']), 6)) for n in pc['notches']]
    if pc.get('mirror_axis'):                        # a turned center line turns the other way on the copy
        ax = pc['mirror_axis']
        cp['mirror_axis'] = _r(M([ax[:2]]))[0] + [-float(ax[2])]
    spec['pieces'].append(cp)
    return cp['name']


def family_of(pc):
    return pc.get('bom', {}).get('family') or pc.get('family')


def has_partner(spec, name):
    """Another piece shares name's family, On or Off: name has its mirrored twin already (twin_of misses an Off
    twin, and a third copy would end the family's pair)."""
    f = family_of(piece(spec, name))
    return bool(f) and any(family_of(p) == f for p in spec['pieces'] if p['name'] != name)


def mirror_group(spec, names, gap=50.0, plane='x'):
    """Mirrored copies of the pieces `names` (each made the twin of its own), about one line right of every piece,
    so they lie as the originals do and on no other piece (user 2026-10-23, the symmetric pattern). A piece that has its twin already is
    skipped. What a copy buttons onto: the copy (or the twin) of what its original buttons onto, else nothing.
    Folds along a copied piece's internal lines go with it. Seams: seam_twin.mirror_seams_group. Returns
    ({original: copy}, [skipped])."""
    order = [p['name'] for p in spec['pieces'] if p['name'] in set(names)]
    skipped = [n for n in order if has_partner(spec, n)]
    todo = [n for n in order if n not in skipped]
    if not todo:
        return {}, skipped
    # right of every piece, not only of these: a line just right of the group laid the copies over the pieces not
    # picked between them (a shirt's back, 2026-10-23)
    k = 1 if plane == 'y' else 0                    # front / back: above every piece, turned upside down
    x_axis = max(corners_all(p)[:, k].max() for p in spec['pieces']) + 0.5 * gap
    made = {n: mirror_copy(spec, n, gap, x_axis=x_axis, plane=plane) for n in todo}
    for n, c in made.items():
        t = piece(spec, n).get('closes_onto')
        # a twin only in the copy's plane (a left / right twin is not the other side of a front / back copy)
        to = made.get(t) or (twin_of(spec, t) if t and S.pair_plane(spec, t) == plane else None)
        if to:
            piece(spec, c)['closes_onto'] = to
    fo = spec.get('folds', [])
    # the copy's lines keep their point order and are mirrored: the side on their left is the other one (A <-> B)
    fo += [dict(f, piece=made[f['piece']], **{k: {'A': 'B', 'B': 'A'}[f[k]] for k in ('side', 'layers')
                                              if f.get(k) in ('A', 'B')})
           for f in list(fo) if f.get('piece') in made and 'line' in f]
    if fo:
        spec['folds'] = fo
    return made, skipped


def set_pair_plane(spec, name, plane):
    """The mirror plane of the pair `name` is in made `plane` ('x' left / right, 'y' front / back; user 2026-11-11):
    both pieces hold it, and the mirrored one of the two is turned 180 degrees about its middle in the layout, so it
    is the other mirror image of its source (a y mirror is an x mirror turned half round) -- its outline, internal
    lines and anchor turn with it (transform_piece); seams and folds keep (edge ids and fractions). Returns (the
    piece turned, its rigid map (R, t)), or (None, None) (no twin, or the plane already so)."""
    tw = twin_of(spec, name)
    if not tw:
        return None, None
    a, b = piece(spec, name), piece(spec, tw)
    if ('y' if a.get('mirror_plane') == 'y' else 'x') == plane:
        return None, None
    for pc in (a, b):
        if plane == 'y':
            pc['mirror_plane'] = 'y'
        else:
            pc.pop('mirror_plane', None)
        pc.pop(S.MIRROR_AT, None)              # the plane's place is of the old plane (the caller sets a new one)
    turned = tw if b.get('mirrored') or not a.get('mirrored') else name
    return turned, transform_piece(spec, turned, 180.0)


def piece_center(pc):
    A = corners_all(pc)
    return 0.5 * (A.min(0) + A.max(0))


def transform_piece(spec, name, deg=0.0, shift=(0.0, 0.0), center=None, spin=False):
    """Move a whole piece in the layout: rotate by deg about center (its bbox center by default), then shift (mm).
    Its outline, handles, internal lines and placement anchor move together; the shape does not change, nor does any
    seam or fold (they refer to edge ids and fractions). spin: a turn the user gave it in the layout, kept in its
    placement ('spin', core/spec.spun) so that where it is placed in 3D does not turn (user 2026-11-11). Returns the
    rigid map (R, t), new = R @ old + t."""
    pc = piece(spec, name)
    c = piece_center(pc) if center is None else np.asarray(center, float)
    a = np.radians(deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    t = c - R @ c + np.asarray(shift, float)

    def f(P):
        return _r(np.asarray(P, float).reshape(-1, 2) @ R.T + t)
    for e in pc['edges']:
        e['pts'] = f(e['pts'])
        if e.get('ctrl'):
            e['ctrl'] = f(e['ctrl'])
    for it in pc.get('internals', []):
        xform_internal(it, f)
    pl = spec.get('placement', {}).get(name)
    if pl and 'anchor' in pl:
        pl['anchor'] = f([pl['anchor']])[0]
        if spin and abs(deg) > 1e-9:
            pl['spin'] = round((float(pl.get('spin', 0.0)) + deg + 180.0) % 360.0 - 180.0, 6)
    if pc.get('mirror_axis') or pc.get('mirror_edit'):   # the center line turns with it (user 2026-10-27)
        ax = pc.get('mirror_axis') or [*outline_centroid(pc), 0.0]
        q = R @ np.asarray(ax[:2], float) + t
        pc['mirror_axis'] = [round(float(q[0]), 4), round(float(q[1]), 4), round(float(ax[2]) + deg, 6)]
    return R, t


def move_piece(spec, name, deg=0.0, shift=(0.0, 0.0), twins=True):
    """transform_piece for the editor: a rotated piece's twin turns the other way about its own center, so the two stay
    mirror images (pattern_build meshes a twin as its pair's mirror only then); a shift moves the piece alone (a twin
    is matched about its own center wherever it lies). Returns {piece: (R, t)} for the pieces moved."""
    out = {name: transform_piece(spec, name, deg=deg, shift=shift, spin=True)}
    tw = twin_of(spec, name) if twins and abs(deg) > 1e-9 else None
    if tw:
        out[tw] = transform_piece(spec, tw, deg=-deg, spin=True)
    return out


def scale_piece(spec, name, sx, sy=None, center=None, twins=True):
    """Scale a whole piece in the layout by sx along x and sy along y (sy None: sx) about center (its bbox center by
    default; user 2026-10-26). Outline, handles, internal lines and placement anchor scale together; seams and notches
    hold (edge id, t), so they stay where they are on the edges. The twin scales alike about its own center (they stay
    mirror images), and a piece edited symmetrically stays symmetric (its center line is the bbox middle). Returns the
    pieces changed."""
    sy = sx if sy is None else sy
    if sx <= 0 or sy <= 0:
        raise ValueError(rpt("a scale must be above 0"))
    out = []
    for nm in [name] + ([twin_of(spec, name)] if twins and twin_of(spec, name) else []):
        pc = piece(spec, nm)
        c = piece_center(pc) if (center is None or nm != name) else np.asarray(center, float)
        k = np.array([sx, sy], float)

        def f(P):
            return _r(c + (np.asarray(P, float).reshape(-1, 2) - c) * k)
        for e in pc['edges']:
            e['pts'] = f(e['pts'])
            if e.get('ctrl'):
                e['ctrl'] = f(e['ctrl'])
                refresh_curve(e)
            if S.polyline_len(e['pts']) < MIN_EDGE:
                raise ValueError(rpt("edge {0} would be shorter than {1}", e['id'], len_text(MIN_EDGE)))
        for it in pc.get('internals', []):
            xform_internal(it, f)
        pl = spec.get('placement', {}).get(nm)
        if pl and 'anchor' in pl:
            pl['anchor'] = f([pl['anchor']])[0]
        if pc.get('mirror_axis'):                   # the center line scales with it (turned: its slant changes)
            ax = pc['mirror_axis']
            q = f([ax[:2]])[0]
            _, u = self_axis(pc, (0.0, 0.0))
            u = u * k
            pc['mirror_axis'] = [q[0], q[1], round(float(np.degrees(np.arctan2(-u[0], u[1]))), 6)]
        out.append(nm)
    return out


def group_center(spec, pieces=(), lines=(), points=()):
    """The middle of the bounding box of whole pieces, internal lines [(piece, line id)] and points (mm)."""
    A = ([corners_all(piece(spec, n)) for n in pieces]
         + [np.asarray(internal(piece(spec, n), l)['pts'], float) for n, l in lines]
         + ([np.asarray(points, float).reshape(-1, 2)] if len(points) else []))
    A = np.vstack(A)
    return 0.5 * (A.min(0) + A.max(0))


def group_map(deg=0.0, k=(1.0, 1.0), shift=(0.0, 0.0), center=(0.0, 0.0)):
    """The layout map of a group's turn and scale: scaled by k about center, turned by deg about it, then shifted."""
    c, k, sh = np.asarray(center, float), np.asarray(k, float), np.asarray(shift, float)
    a = np.radians(deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])

    def f(P):
        return ((np.asarray(P, float).reshape(-1, 2) - c) * k) @ R.T + c + sh
    return f


def transform_group(spec, pieces=(), lines=(), deg=0.0, k=(1.0, 1.0), shift=(0.0, 0.0), center=None):
    """Whole Pieces' G / R / S on what is picked (user 2026-10-28, DECISIONS cloth-sim-transform-edit-split):
    whole pieces and internal lines [(piece, line id)] scaled by k and turned by deg about center (the middle of their
    bounding box), then shifted, all as one. A line on a picked piece goes with the piece. A piece's twin not picked
    turns the other way and scales about its own center (move_piece, scale_piece: the two stay mirror images); a
    line's twin line and its mirror line on a piece edited symmetrically follow as mirror images, and a line that is
    its own mirror image stays so. Returns (pieces changed, {piece: (R, t)} when every change is a rigid move of a
    whole piece, else None)."""
    pieces = list(dict.fromkeys(pieces))
    lines = [x for x in dict.fromkeys(tuple(x) for x in lines) if x[0] not in pieces]
    if not pieces and not lines:
        return [], None
    c = group_center(spec, pieces, lines) if center is None else np.asarray(center, float)
    k = np.asarray(k, float)
    f = group_map(deg, k, shift, c)
    scaled = bool(np.abs(k - 1.0).max() > 1e-12)
    changed, rigid = [], {}
    for n in pieces:
        tw = twin_of(spec, n)
        follow = tw is not None and tw not in pieces
        if scaled:
            changed += scale_piece(spec, n, float(k[0]), float(k[1]), center=c, twins=follow)
        rigid[n] = transform_piece(spec, n, deg=deg, shift=shift, center=c, spin=True)
        changed.append(n)
        if follow and abs(deg) > 1e-9:
            rigid[tw] = transform_piece(spec, tw, deg=-deg, spin=True)
            changed.append(tw)
    # the mirrors of every line found before any moves (they are found by where they lie)
    picked = set(lines)
    jobs = []
    for n, l in lines:
        pc = piece(spec, n)
        jobs.append((n, l, f))
        ms, _ = self_mirror_line(pc, l)
        if ms is not None:
            rf = self_reflect(pc)
            if ms == l:                                 # its own mirror image: the map made symmetric
                jobs[-1] = (n, l, lambda P, rf=rf: 0.5 * (f(P) + rf(f(rf(P)))))
            elif (n, ms) not in picked:
                jobs.append((n, ms, lambda P, rf=rf: rf(f(rf(P)))))
        tw, tl, M = _twin_internal(spec, n, l)
        if tw is not None and tw not in pieces and (tw, tl) not in picked:
            Mi = mirror_fn(piece(spec, tw), pc)
            jobs.append((tw, tl, lambda P, M=M, Mi=Mi: M(f(Mi(P)))))
    for n, l, g in jobs:
        xform_internal(internal(piece(spec, n), l), g)
        changed.append(n)
    return list(dict.fromkeys(changed)), (rigid if not scaled and not jobs else None)


DUP_SKIP = ('name', 'bom', 'family', 'mirrored', 'closes_onto')


def duplicate_group(spec, names, gap=50.0):
    """Copies of the pieces `names` as they are (not mirrored, not twins; user 2026-10-26: a back from the front),
    laid together right of every piece. A copy keeps the outline, internal lines, notches, fabric, look and role; it
    is not placed. The seams between two of the copied pieces go between the copies (a dart, a center seam; Fable b-2,
    user OK 2026-10-26), a seam to a piece outside is not copied; so do folds on the copied lines. Names are
    unique_name's ("Front.002"). Returns ({original: copy}, seam ids made)."""
    order = [p['name'] for p in spec['pieces'] if p['name'] in set(names)]
    if not order:
        return {}, []
    right = max(corners_all(p)[:, 0].max() for p in spec['pieces'])
    left = min(corners_all(piece(spec, n))[:, 0].min() for n in order)
    dx = right + gap - left
    made = {}
    for n in order:
        pc = piece(spec, n)
        cp = copy.deepcopy({k: v for k, v in pc.items() if k not in DUP_SKIP})
        cp['name'] = unique_name(spec, n)

        def f(P):
            return _r(np.asarray(P, float).reshape(-1, 2) + [dx, 0.0])
        for e in cp['edges']:
            e['pts'] = f(e['pts'])
            if e.get('ctrl'):
                e['ctrl'] = f(e['ctrl'])
        for it in cp.get('internals', []):
            xform_internal(it, f)
        if cp.get('mirror_axis'):
            cp['mirror_axis'] = f([cp['mirror_axis'][:2]])[0] + [cp['mirror_axis'][2]]
        spec['pieces'].append(cp)
        made[n] = cp['name']
    used = {sm['id'] for sm in spec.get('seams', [])}
    ids, idmap = [], {}
    for sm in list(spec.get('seams', [])):
        if sm['a']['piece'] in made and sm['b']['piece'] in made:
            nid = sm['id'] + 'c'
            while nid in used:
                nid += 'c'
            used.add(nid)
            nm = copy.deepcopy({k: v for k, v in sm.items() if k != 'bom'})
            nm.update(id=nid, source='user')
            for k in ('a', 'b'):
                nm[k]['piece'] = made[sm[k]['piece']]
            spec['seams'].append(nm)
            ids.append(nid)
            idmap[sm['id']] = nid
    fo = spec.get('folds', [])
    add = []
    for f in fo:
        if f.get('piece') in made and 'line' in f:
            add.append(dict(f, piece=made[f['piece']]))
        elif f.get('seam') in idmap:
            add.append(dict(f, seam=idmap[f['seam']],
                            **({'turn': made.get(f['turn'], f['turn'])} if f.get('turn') else {})))
    if add:
        spec['folds'] = fo + add
    return made, ids


AXIS_SNAP = 0.5       # mm: Symmetrize puts a corner this close to the center line onto it


def _line_crossings(P, xc, tol=1e-6):
    """Arc lengths along the polyline P where it crosses the vertical line x = xc (from one side to the other; a
    stretch lying on the line counts once, at its start)."""
    d = np.asarray(P, float)[:, 0] - xc
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    sg = np.where(d > tol, 1, np.where(d < -tol, -1, 0))
    nz = np.flatnonzero(sg)
    out = []
    for i, j in zip(nz[:-1], nz[1:]):
        if sg[i] == sg[j]:
            continue
        if j == i + 1:
            out.append(cum[i] + (cum[j] - cum[i]) * d[i] / (d[i] - d[j]))
        else:
            out.append(cum[i + 1])
    return out, cum[-1]


def _axis_c(pc):
    """The center line's point Symmetrize uses: the one kept while the piece was symmetric (mirror_axis) when the
    outline crosses that line twice, else the outline's centroid."""
    ax = pc.get('mirror_axis')
    if not ax:
        return outline_centroid(pc)
    c, u = self_axis(pc, ax[:2])
    n = np.array([u[1], -u[0]])
    B = S.Outline(pc).B
    d = (B - c) @ n
    sgn = np.sign(np.where(np.abs(d) < 1e-6, 0, d))
    sgn = sgn[sgn != 0]
    flips = int((sgn != np.roll(sgn, 1)).sum()) if len(sgn) else 0
    return c if flips == 2 else outline_centroid(pc)


def symmetrize(spec, name, keep='LEFT'):
    """Make a piece symmetric about its center line (the line Symmetric Edit uses, turned with the piece): the half on
    the `keep` side ('LEFT' or 'RIGHT', seen with the line upright) stays, the other half is made again as its mirror
    image (user 2026-10-27: the side chosen in F9, not by a picked corner). Seam ends and measure points on the
    outline go to the nearest point of the new outline (match_twin's way), notches on the kept half are mirrored and
    those on the other half dropped. Internal lines are left as they are. Symmetric Edit is switched on. Returns the
    pieces changed (the twin too, made its mirror image again)."""
    pc = piece(spec, name)
    c = _axis_c(pc)
    ang = axis_angle(pc)
    if abs(ang) > 1e-9:                         # worked on upright, turned back after
        transform_piece(spec, name, deg=-ang, center=c)
    pc['mirror_axis'] = [round(float(c[0]), 4), round(float(c[1]), 4), 0.0]
    _symmetrize_upright(spec, pc, float(c[0]), keep)
    pc['mirror_edit'] = True
    if abs(ang) > 1e-9:
        transform_piece(spec, name, deg=ang, center=c)
    out = [name]
    if twin_of(spec, name):
        out.append(match_twin(spec, name))
    return out


def _snap_corners_to(pc, xc):
    n = len(pc['edges'])
    for k, q in enumerate(corners(pc)):
        if 0 < abs(q[0] - xc) < AXIS_SNAP:
            pc['edges'][k]['pts'][0][0] = xc
            pc['edges'][(k - 1) % n]['pts'][-1][0] = xc


def _symmetrize_upright(spec, pc, xc, keep):
    name = pc['name']
    sgn = -1.0 if keep == 'LEFT' else 1.0
    ol0 = S.Outline(pc)
    refs = [(put, ol0.at(ol0.s_of(*get()))) for get, put in point_refs(spec, name)]
    notch_q = [(n, ol0.at(ol0.s_of(n['edge'], float(n['t'])))) for n in pc.get('notches') or []]
    # corners near the line onto it, then every edge split once where it crosses the line (all its crossings found
    # first: splitting while searching split the same crossing again and again, user 2026-10-27 froze)
    _snap_corners_to(pc, xc)
    jobs = {}
    for e in pc['edges']:
        ss, L = _line_crossings(e['pts'], xc)
        us = sorted(x / L for x in ss if AXIS_SNAP < x < L - AXIS_SNAP)
        if us:
            jobs[e['id']] = us
    for eid, us in jobs.items():
        _split_at(spec, pc, eid, us)
    _snap_corners_to(pc, xc)                   # a curve's split point lies off the line by its sampling
    C = corners(pc)
    on = [i for i in range(len(C)) if abs(C[i, 0] - xc) < 1e-6]
    if len(on) != 2:
        raise ValueError(rpt("the outline crosses its center line {0} times: Symmetrize needs 2 (a shape that is one piece either side of the line)", len(on)))
    n = len(pc['edges'])
    a, b = on
    chain1 = [(a + j) % n for j in range((b - a) % n)]
    chain2 = [(b + j) % n for j in range((a - b) % n)]

    def side(ch):
        P = np.vstack([np.asarray(pc['edges'][i]['pts'], float) for i in ch])
        return float(np.mean(P[:, 0]) - xc)
    kept = chain1 if side(chain1) * sgn > 0 else chain2
    if side(kept) * sgn <= 0:
        raise ValueError(rpt("no half of the outline lies on that side"))

    def M(q):
        q = np.asarray(q, float).reshape(-1, 2)
        return np.stack([2 * xc - q[:, 0], q[:, 1]], 1)
    edges = [copy.deepcopy(pc['edges'][i]) for i in kept]
    used = {e['id'] for e in edges}
    j = 1
    for e in reversed(edges[:]):
        while f"E{j}" in used:
            j += 1
        ne = {'id': f"E{j}", 'pts': _r(M(e['pts'])[::-1])}
        used.add(ne['id'])
        if e.get('ctrl'):
            ne['ctrl'] = _r(M(e['ctrl'])[::-1])
        edges.append(ne)
    pc['edges'] = edges
    # seam ends, measure points: the nearest point of the new outline
    ol = S.Outline(pc)
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)

    def nearest(q):
        t = np.clip(((q - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
        dd = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
        jj = int(dd.argmin())
        return ol.edge_t(ol.cum[jj] + t[jj] * np.sqrt(ll[jj]))
    for put, q in refs:
        put(*nearest(q))
    if notch_q:                                     # the kept half's notches and their mirror images
        nn = []
        for n, q in notch_q:
            dx = (q[0] - xc) * sgn
            if dx < -0.25:
                continue
            e, t = nearest(q)
            nn.append(dict(n, edge=e, t=round(float(t), 6)))
            if dx > 0.25:
                e2, t2 = nearest(M(q)[0])
                nn.append(dict(n, id=None, edge=e2, t=round(float(t2), 6)))
        pc['notches'] = []
        for n in nn:
            if n['id'] is None or any(m['id'] == n['id'] for m in pc['notches']):
                n['id'] = _new_notch_id(pc)
            pc['notches'].append(n)
    # a corner on the line between two straight edges in one line (a straight neck or hem) goes again
    for i in sorted(on_line_corners(pc, xc), reverse=True):
        n = len(pc['edges'])
        e1, e2 = pc['edges'][(i - 1) % n], pc['edges'][i]
        if n > 3 and _is_straight(e1) and _is_straight(e2):
            u1 = np.subtract(e1['pts'][-1], e1['pts'][0])
            u2 = np.subtract(e2['pts'][-1], e2['pts'][0])
            if abs(u1[0] * u2[1] - u1[1] * u2[0]) <= 1e-6 * np.linalg.norm(u1) * np.linalg.norm(u2):
                _join_one(spec, pc, i)


def on_line_corners(pc, xc, tol=1e-3):
    return [i for i, q in enumerate(corners(pc)) if abs(q[0] - xc) < tol]


# ---------------------------------------------------------------- matching a seam's lengths

def match_side_length(spec, seam_id, key, ease=0.0, end='to', iters=8):
    """Make side `key` ('a' or 'b') of a seam as long as the other side plus `ease` mm. At the chosen end of the side
    (its 'from' or 'to'): a corner moves along the side's direction there (the pattern changes, twins too); an end in
    the middle of an edge, or on an internal line, slides along it (the seam's extent changes). Returns
    (pieces changed, length before, length after, target)."""
    sm = next(x for x in spec['seams'] if x['id'] == seam_id)
    other = 'b' if key == 'a' else 'a'
    target = S.side_length(spec, sm[other]) + float(ease)
    sd = sm[key]
    L0 = S.side_length(spec, sd)
    if target <= MIN_EDGE:
        raise ValueError(rpt("the target length is too short"))
    if 'line' in sd:                                 # an internal line: slide the end's fraction
        it = internal(piece(spec, sd['piece']), sd['line'])
        Lline = S.polyline_len(it['pts'])
        span = (sd['to'] - sd['from']) * Lline
        sgn = 1.0 if span >= 0 else -1.0
        if end == 'to':
            sd['to'] = round(float(np.clip(sd['from'] + sgn * target / Lline, 0, 1)), 6)
        else:
            sd['from'] = round(float(np.clip(sd['to'] - sgn * target / Lline, 0, 1)), 6)
        return [sd['piece']], L0, S.side_length(spec, sd), target
    name = sd['piece']
    pc = piece(spec, name)
    eid, t = sd[end]
    k = edge_index(pc, eid)
    n = len(pc['edges'])
    corner = k if t <= 1e-6 else ((k + 1) % n if t >= 1 - 1e-6 else None)
    if corner is None:                               # mid-edge: slide the end along the outline
        ol = S.Outline(pc)
        s_end = ol.s_of(eid, t)
        grow = (target - L0) * (1 if (end == 'to') == (sd['dir'] > 0) else -1)
        e2, t2 = ol.edge_t(s_end + grow)
        sd[end] = [e2, round(float(t2), 6)]
        return [name], L0, S.side_length(spec, sd), target
    P = S.side_polyline(spec, sd, step=0.5)
    j = min(4, len(P) - 1)                          # the side's direction over its last (first) 2 mm, outwards
    tip, back = (P[-1], P[-1 - j]) if end == 'to' else (P[0], P[j])
    u = np.asarray(tip, float) - np.asarray(back, float)
    u /= max(np.linalg.norm(u), 1e-12)

    def length_at(x):
        memo = {}
        trial = edit_copy(spec, (name, twin_of(spec, name)), memo=memo)
        trial_side = memo.get(id(sd), sd)
        move_corners(trial, name, {corner: tuple(u * x)})
        return S.side_length(trial, trial_side), trial
    x0, x1 = 0.0, target - L0
    f0 = L0 - target
    f1 = length_at(x1)[0] - target
    for _ in range(iters):                           # secant on the corner's travel along u
        if abs(f1) < 1e-3 or abs(f1 - f0) < 1e-12:
            break
        x0, x1, f0 = x1, x1 - f1 * (x1 - x0) / (f1 - f0), f1
        f1 = length_at(x1)[0] - target
    changed = move_corners(spec, name, {corner: tuple(u * x1)})
    return changed, L0, S.side_length(spec, sd), target


# ---------------------------------------------------------------- internal lines (darts, pocket marks, fold lines)

def internal(pc, lid):
    return next(it for it in pc.get('internals', []) if it['id'] == lid)


def _twin_internal(spec, name, lid):
    """(twin name, its line id, M) of the internal line mirroring line `lid` of piece `name`, else (None, None, None).
    Same id first (a mirrored copy keeps ids), else the line whose points lie on the mirror image."""
    tw = twin_of(spec, name)
    if tw is None:
        return None, None, None
    pa, pb = piece(spec, name), piece(spec, tw)
    M = mirror_fn(pa, pb)
    P = M(internal(pa, lid)['pts'])
    for it in sorted(pb.get('internals', []), key=lambda x: x['id'] != lid):
        Q = np.asarray(it['pts'], float)
        if len(Q) == len(P) and (np.abs(Q - P).max() < TWIN_TOL or np.abs(Q[::-1] - P).max() < TWIN_TOL):
            return tw, it['id'], M
    return None, None, None


def nearest_on_outline(pc, q):
    """(edge id, t) of the point of the piece's outline nearest q."""
    ol = S.Outline(pc)
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((np.asarray(q, float) - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    dd = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
    j = int(dd.argmin())
    return ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j]))


def _line_mirrors_onto(P, Q, closed=False):
    """0: Q is not P's place; 1: Q runs along P; -1: Q runs along P backwards (within SELF_TOL). A closed shape: any
    start, either way (1)."""
    P, Q = np.asarray(P, float), np.asarray(Q, float)
    if closed:
        return 1 if (len(P) > 1 and len(Q) > 1 and _dist_to_polyline(P, Q).max() < SELF_TOL
                     and _dist_to_polyline(Q, P).max() < SELF_TOL) else 0
    if len(P) != len(Q):
        return 0
    if np.abs(Q - P).max() < SELF_TOL:
        return 1
    if np.abs(Q[::-1] - P).max() < SELF_TOL:
        return -1
    return 0


def self_mirror_line(pc, lid):
    """(id of the internal line at the mirror image of line lid across the center line of a piece edited
    symmetrically, 1 / -1 as _line_mirrors_onto); (lid, ...) for a line that is its own mirror image (on the line,
    or across it); else (None, 0)."""
    if symmetric_edit(pc) is None:
        return None, 0
    it = internal(pc, lid)
    P = self_reflect(pc)(it['pts'])
    for x in sorted(pc.get('internals', []), key=lambda x: x['id'] != lid):
        if x.get('kind') == 'baseline' or bool(x.get('closed')) != bool(it.get('closed')):
            continue
        w = _line_mirrors_onto(P, x['pts'], it.get('closed'))
        if w:
            return x['id'], w
    return None, 0


def mirror_lines(spec, name, lid):
    """[(piece, line id, map from name's layout to that piece's)] of the lines that are line lid's mirror images: on
    the twin (twin_line), across the center line of a piece edited symmetrically (self_mirror_line, user 2026-10-27),
    and across the twin's own center line. A fold follows on them."""
    out = []
    pa = piece(spec, name)
    ms, _ = self_mirror_line(pa, lid)
    if ms is not None and ms != lid:
        out.append((name, ms, self_reflect(pa)))
    tl = twin_line(spec, name, lid)
    if tl:
        pb = piece(spec, tl[0])
        Mt = mirror_fn(pa, pb)
        out.append((tl[0], tl[1], Mt))
        m2, _ = self_mirror_line(pb, tl[1])
        if m2 is not None and m2 != tl[1]:
            Ms = self_reflect(pb)
            out.append((tl[0], m2, lambda q, Mt=Mt, Ms=Ms: Ms(Mt(q))))
    return out


def _move_internal_one(it, which, d):
    P = np.asarray(it['pts'], float)
    d = np.asarray(d, float)
    if which == 'all':
        xform_internal(it, lambda q: np.asarray(q, float) + d)
        return
    a, b = (P[0], P[-1]) if which == 'start' else (P[-1], P[0])     # a moves, b stays
    f = _similarity(b, a, b, a + d)
    xform_internal(it, f)
    Q = np.asarray(it['pts'], float)
    Q[0 if which == 'start' else -1] = a + d
    it['pts'] = _r(Q)


def move_internal(spec, name, lid, which, d, twins=True):
    """Move an internal line: which = 'start' / 'end' (that end moves, the line follows as a similarity, the other end
    stays) or 'all' (shifted). Seams on the line keep their fractions, folds their line id. Returns pieces changed."""
    tw, tlid, M = _twin_internal(spec, name, lid) if twins else (None, None, None)
    if tw is not None:
        P = np.asarray(internal(piece(spec, name), lid)['pts'], float)
        Q = np.asarray(internal(piece(spec, tw), tlid)['pts'], float)
        rev = np.abs(Q - M(P)).max() >= TWIN_TOL
    pc = piece(spec, name)
    ms, w = self_mirror_line(pc, lid)               # edited symmetrically: its mirror line too (user 2026-10-27)
    d = np.asarray(d, float)
    dm = reflect_vec(pc, d)
    if ms == lid:                                   # its own mirror image (on the line, or across it)
        it = internal(pc, lid)
        if which == 'all' or it.get('closed') or w == 1:
            _move_internal_one(it, which, 0.5 * (d + dm))      # it stays on (or square across) the line
        else:                                       # across the line: the other end moves as the mirror image
            _move_internal_one(it, which, d)
            _move_internal_one(it, 'end' if which == 'start' else 'start', dm)
    else:
        _move_internal_one(internal(pc, lid), which, d)
        if ms is not None:
            _move_internal_one(internal(pc, ms), which if (w == 1 or which == 'all')
                               else ('end' if which == 'start' else 'start'), dm)
    out = [name]
    if tw is not None:
        w = which if not rev or which == 'all' else ('end' if which == 'start' else 'start')
        _move_internal_one(internal(piece(spec, tw), tlid), w, (-float(d[0]), float(d[1])))
        out.append(tw)
    return out


def add_internal(spec, name, P, kind='line', closed=False, mirror=True):
    """A new internal line on piece `name` through the points P (layout mm). Returns its id. closed: a closed
    shape (a pocket's place, a button's mark): its last point is its first (added if missing), and an edit drags it
    whole, never by its 'ends' (Fable r1, must-fix 2). A piece edited symmetrically gets the mirror image too (user
    2026-10-27), unless the line is its own mirror image (drawn on the center line, or evenly across it)."""
    pc = piece(spec, name)
    if mirror and symmetric_edit(pc) is not None:
        Q = self_reflect(pc)(P)
        lid = add_internal(spec, name, P, kind, closed, mirror=False)
        if not _line_mirrors_onto(np.asarray(internal(pc, lid)['pts'], float), Q, closed):
            add_internal(spec, name, Q, kind, closed, mirror=False)
        return lid
    used = {it['id'] for it in pc.get('internals', [])}
    k = len(used) + 1
    while f"I{k}" in used:
        k += 1
    P = np.asarray(P, float)
    it = {'id': f"I{k}", 'pts': _r(P), 'kind': kind}
    if closed:
        if np.linalg.norm(P[0] - P[-1]) > 1e-6:
            P = np.vstack([P, P[:1]])
        it.update(pts=_r(P), closed=True)
    pc.setdefault('internals', []).append(it)
    return f"I{k}"


def edge_chains(pc, eids):
    """The edges eids of a piece in runs of edges next to each other along the outline: [[edge id, ...], ...], each
    run in outline order."""
    n = len(pc['edges'])
    ks = sorted({edge_index(pc, e) for e in eids})
    if len(ks) == n:
        return [[e['id'] for e in pc['edges']]]
    starts = [k for k in ks if (k - 1) % n not in ks]
    out = []
    for k in starts:
        run = [k]
        while (run[-1] + 1) % n in ks:
            run.append((run[-1] + 1) % n)
        out.append([pc['edges'][j]['id'] for j in run])
    return out


def _seg_hits(A, B, P, Q):
    """Parameters u along segment A->B (unbounded) where it crosses the segments P[i]->Q[i]."""
    d = B - A
    e = Q - P
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    ok = np.abs(den) > 1e-12
    w = P - A
    u = np.where(ok, (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / np.where(ok, den, 1), np.nan)
    v = np.where(ok, (w[:, 0] * d[1] - w[:, 1] * d[0]) / np.where(ok, den, 1), np.nan)
    return u[ok & (v >= -1e-9) & (v <= 1 + 1e-9)]


def offset_line(pc, eids, dist):
    """The points of an internal line parallel to the outline edges eids (one run next to each other) `dist` mm inside
    the piece, its ends run on (or cut back) to where they meet the outline (user 2026-10-27: an internal line as the
    other tool makes it, from an outline edge). Raises ValueError where it does not fit."""
    if dist <= 0:
        raise ValueError(rpt("the distance must be above 0"))
    ks = [edge_index(pc, e) for e in eids]
    P = np.vstack([np.asarray(pc['edges'][k]['pts'], float)[:-1] for k in ks]
                  + [np.asarray(pc['edges'][ks[-1]]['pts'], float)[-1:]])
    return _offset(pc, P, dist, 1.0 if signed_area(S.Outline(pc).B) > 0 else -1.0, True)


def offset_internal(pc, line_id, dist, side=1.0, extend=True):
    """The points of a line parallel to internal line line_id, `dist` mm to its left (side 1) or right (side -1) as
    it runs from its start; extend: its ends run on (or cut back) to the outline, else as long as the line (user
    2026-11-11: a parallel line from an internal line too). Raises ValueError where it does not fit."""
    if dist <= 0:
        raise ValueError(rpt("the distance must be above 0"))
    it = internal(pc, line_id)
    if it.get('closed'):
        raise ValueError(rpt("a closed shape has no parallel line"))
    return _offset(pc, np.asarray(it['pts'], float), dist, float(side), extend, rpt("that line"))


def _offset(pc, P, dist, sgn, extend, what=None):
    """P (a polyline, mm) offset dist mm to the side sgn * its left; extend: the ends run on or cut back to the
    outline, the stretch round the middle kept."""
    what = what or rpt("that edge")
    keep = np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-6]
    P = P[keep]
    if len(P) < 2:
        raise ValueError(rpt("the edge is too short"))
    T = np.diff(P, axis=0)
    T /= np.linalg.norm(T, axis=1)[:, None]
    Nseg = sgn * np.stack([-T[:, 1], T[:, 0]], 1)       # into the piece (an edge), to the side asked (a line)
    N = np.vstack([Nseg[:1], Nseg[:-1] + Nseg[1:], Nseg[-1:]])
    N /= np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]
    cosv = np.vstack([np.ones((1,)), (N[1:-1] * Nseg[1:]).sum(1)[:, None] if len(P) > 2 else np.zeros((0, 1)),
                      np.ones((1,))]).ravel()
    Q = P + dist * N / np.maximum(cosv, 0.5)[:, None]    # mitred, at most twice as far at a sharp bend
    if not extend:
        if len(Q) > 2 and _dist_to_polyline(Q[len(Q) // 2:len(Q) // 2 + 1], P)[0] < 0.5 * dist:
            raise ValueError(rpt("{0} bends too tightly for a line {1} away", what, len_text(dist)))
        return Q
    # run the ends on along their direction, then keep the stretch between the outline crossings round its middle
    B = S.Outline(pc).B
    Ps, Qs = B, np.roll(B, -1, 0)
    big = float(np.ptp(B, axis=0).max()) + 10 * dist
    t0, t1 = Q[1] - Q[0], Q[-1] - Q[-2]
    E = np.vstack([Q[:1] - big * t0 / np.linalg.norm(t0), Q, Q[-1:] + big * t1 / np.linalg.norm(t1)])
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(E, axis=0), axis=1))]
    hits = []
    for i in range(len(E) - 1):
        L = cum[i + 1] - cum[i]
        for u in _seg_hits(E[i], E[i + 1], Ps, Qs):
            if -1e-9 <= u <= 1 + 1e-9:
                hits.append(cum[i] + u * L)
    mid = 0.5 * (cum[1] + cum[-2])
    lo = max([h for h in hits if h < mid], default=None)
    hi = min([h for h in hits if h > mid], default=None)
    if lo is None or hi is None or hi - lo < 2 * MIN_EDGE:
        raise ValueError(rpt("no line fits {0} from {1}", len_text(dist), what))
    ss = np.r_[lo, cum[(cum > lo + 1e-6) & (cum < hi - 1e-6)], hi]
    out = np.stack([np.interp(ss, cum, E[:, 0]), np.interp(ss, cum, E[:, 1])], 1)
    kept = [0]                                       # points on a straight run left out (a straight edge: 2 points)
    for i in range(1, len(out) - 1):
        a, b = out[kept[-1]], out[i + 1]
        ab = b - a
        if abs(ab[0] * (out[i] - a)[1] - ab[1] * (out[i] - a)[0]) / max(np.linalg.norm(ab), 1e-12) > 1e-3:
            kept.append(i)
    out = out[kept + [len(out) - 1]]
    # it must lie dist mm from the edge (a tight curve folds the offset over itself)
    if _dist_to_polyline(out[len(out) // 2:len(out) // 2 + 1], P)[0] < 0.5 * dist:
        raise ValueError(rpt("{0} bends too tightly for a line {1} away", what, len_text(dist)))
    return out


def internal_mesh_notes(spec, name, h, lids=None):
    """Internal lines of piece `name` the mesh of size h (mm) will drop, why (core/pattern_build.py skips a line
    shorter than 2 h and the parts of a line within 0.5 h of the outline): a warning, not a refusal."""
    pc = piece(spec, name)
    B = S.Outline(pc).B
    A2, B2 = B, np.roll(B, -1, 0)
    out = []
    for it in pc.get('internals', []):
        if it.get('kind') == 'baseline' or (lids is not None and it['id'] not in lids):
            continue
        L = S.polyline_len(it['pts'])
        if L < 2 * h:
            out.append(f"{it['id']} is {len_text(L)} long: the {h:g} mm mesh needs {len_text(2 * h)}, it will not show in 3D")
            continue
        if it.get('closed'):
            ext = np.ptp(np.asarray(it['pts'], float), axis=0).min()
            if ext < h:                                # a shape narrower than one mesh cell collapses
                out.append(f"{it['id']} is {len_text(ext)} across: smaller than the {h:g} mm mesh, it will not show "
                           f"in 3D (a finer Piece Mesh shows it)")
                continue
        P = S.resample(it['pts'], max(3, int(L / 2.0)))
        Dd = B2 - A2
        t = np.clip(((P[:, None] - A2[None]) * Dd[None]).sum(2) / np.maximum((Dd * Dd).sum(1), 1e-12)[None], 0, 1)
        d = np.linalg.norm(A2[None] + t[..., None] * Dd[None] - P[:, None], axis=2).min(1)
        near = d < 0.5 * h
        if not it.get('closed'):                       # a line from the outline: its ends are on it on purpose
            s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
            near &= (s > h) & (s < s[-1] - h)
        if near.any():
            out.append(f"{it['id']} runs within {len_text(0.5 * h)} of the outline: that part will not show in 3D")
    return out


def _remove_seam_dependents(spec, gone):
    """Retire settings whose seam identity has been removed, independent of the editing tool."""
    spec['folds'] = [f for f in spec.get('folds', []) if f.get('seam') not in gone]
    if 'attach' in spec:
        spec['attach'] = [entry for entry in spec['attach'] if entry.get('seam') not in gone]


def delete_internal(spec, name, lid, mirror=True):
    """Remove an internal line with the seams sewn along it and its fold (and its mirror line on a piece edited
    symmetrically, user 2026-10-27). Returns the seam ids removed."""
    pc = piece(spec, name)
    ms = self_mirror_line(pc, lid)[0] if mirror else None
    lids = {lid} | ({ms} if ms else set())
    pc['internals'] = [it for it in pc.get('internals', []) if it['id'] not in lids]
    gone = [sm['id'] for sm in spec.get('seams', []) if any(sm[k]['piece'] == name and sm[k].get('line') in lids
                                                            for k in ('a', 'b'))]
    spec['seams'] = [sm for sm in spec.get('seams', []) if sm['id'] not in gone]
    _remove_seam_dependents(spec, gone)
    spec['folds'] = [f for f in spec.get('folds', []) if not (f.get('piece') == name and f.get('line') in lids)]
    if 'elastic' in spec:
        spec['elastic'] = [entry for entry in spec['elastic']
                           if not (entry.get('piece') == name
                                   and (entry.get('line') in lids or (entry.get('side') or {}).get('line') in lids))]
        if not spec['elastic']:
            spec.pop('elastic')
    if 'attach' in spec:
        spec['attach'] = [entry for entry in spec['attach']
                          if not ((entry.get('side') or {}).get('piece') == name
                                  and (entry.get('side') or {}).get('line') in lids)]
    return gone


def internal_problems(spec, name, lids=None):
    """Internal lines of piece `name` (only `lids` if given) that leave the piece (the ends may lie on the outline: a
    dart's legs). Checked for the lines an edit moved only: imported patterns have lines along their outline."""
    pc = piece(spec, name)
    B = S.Outline(pc).B
    ax, ay = B[:, 0][None], B[:, 1][None]
    bx, by = np.roll(B[:, 0], -1)[None], np.roll(B[:, 1], -1)[None]
    out = []
    for it in pc.get('internals', []):
        if it.get('kind') == 'baseline' or len(it['pts']) < 2 or (lids is not None and it['id'] not in lids):
            continue
        P = S.resample(it['pts'], max(3, int(S.polyline_len(it['pts']) / 2.0)))[1:-1]
        x, y = P[:, 0][:, None], P[:, 1][:, None]
        hit = ((ay > y) != (by > y)) & (x < (bx - ax) * (y - ay) / np.where(by == ay, 1e-12, by - ay) + ax)
        if not (hit.sum(1) % 2 == 1).all():
            out.append(f"{name}: internal line {it['id']} leaves the piece")
        elif it.get('closed') and len(it['pts']) >= 4 and _segments_cross(_ring(it)):
            out.append(f"{name}: internal shape {it['id']} crosses itself")
    return out


# ---------------------------------------------------------------- holes cut out of a piece (user 2026-10-28)

HOLE_GAP = 0.1       # mm: a hole's edge this close to the outline touches it -- a notch cut into the edge, not a hole


def _ring(it):
    """A closed line's points without the first repeated at the end."""
    P = np.asarray(it['pts'], float)
    return P[:-1] if len(P) > 1 and np.linalg.norm(P[0] - P[-1]) < 1e-6 else P


def _dense_ring(R, step=0.5):
    R = np.asarray(R, float)
    C = np.vstack([R, R[:1]])
    return S.resample(C, max(8, int(S.polyline_len(C) / step)))[:-1]


def _ring_dist(Q, R):
    """Distance of each point Q to the closed polyline R."""
    A, B = R, np.roll(R, -1, 0)
    Dd = B - A
    ll = np.maximum((Dd * Dd).sum(1), 1e-12)
    t = np.clip(((Q[:, None] - A[None]) * Dd[None]).sum(2) / ll[None], 0, 1)
    return np.linalg.norm(A[None] + t[..., None] * Dd[None] - Q[:, None], axis=2).min(1)


def hole_problems(spec, name, lid):
    """Why internal line lid of piece `name` cannot be cut out as a hole (empty = it can). Only where a hole is no
    hole is it refused: not closed, crossing itself, crossing or touching the outline (a notch cut into the edge),
    overlapping another hole, folded. Its size is not checked: its edge goes into the mesh as a line, the triangles
    inside are dropped (user 2026-10-28: the mesh spacing does not matter)."""
    pc = piece(spec, name)
    it = next((x for x in pc.get('internals', []) if x['id'] == lid), None)
    if it is None:
        return [rpt("{0}: no internal line {1}", name, lid)]
    if not it.get('closed'):
        return [rpt("{0}: {1} is not a closed shape (close it on its first point, or draw a Rectangle or Ellipse)", name, lid)]
    R = _ring(it)
    if len(R) < 3:
        return [rpt("{0}: {1} has fewer than 3 points", name, lid)]
    if _segments_cross(R):
        return [rpt("{0}: {1} crosses itself", name, lid)]
    B = S.Outline(pc).B
    Q = _dense_ring(R)
    if not S.in_holes(Q, [B]).all() or not S.in_holes(R, [B]).all():
        return [rpt("{0}: {1} crosses the outline", name, lid)]
    if min(_ring_dist(Q, B).min(), _ring_dist(B, R).min()) < HOLE_GAP:
        return [rpt("{0}: {1} touches the outline (a cut into the edge, not a hole)", name, lid)]
    for ids, R2 in S.holes(pc):
        if lid in ids:
            continue
        if S.in_holes(Q, [R2]).any() or S.in_holes(_dense_ring(R2), [R]).any():
            return [rpt("{0}: {1} overlaps the hole {2}", name, lid, '+'.join(ids))]
    if any(f.get('piece') == name and f.get('line') == lid and f.get('deg') for f in spec.get('folds', [])):
        return [rpt("{0}: {1} has a fold angle: set it to 0 first", name, lid)]
    if any(e.get('piece') == name and e.get('line') == lid for e in spec.get('elastic', [])):
        return [rpt("{0}: {1} has an elastic: remove it first", name, lid)]
    return []


def set_hole(spec, name, lid, on=True):
    """Cut the closed internal shape lid out of piece `name` as a hole (on), or fill it again; its mirror lines (twin,
    across the center line: mirror_lines) follow. Raises ValueError with the reason when it cannot be a hole. Returns
    the pieces changed."""
    targets = [(name, lid)] + [(p, l) for p, l, _ in mirror_lines(spec, name, lid)]
    if on:
        for p, l in targets:
            bad = hole_problems(spec, p, l)
            if bad:
                raise ValueError(bad[0])
    out = []
    for p, l in targets:
        it = internal(piece(spec, p), l)
        if on:
            it['hole'] = True
        else:
            it.pop('hole', None)
        if p not in out:
            out.append(p)
    return out


def match_twin(spec, name):
    """Make the twin of `name` its exact mirror image again (after the two were edited apart): the twin's outline is
    replaced by `name`'s mirrored about the twin's bbox center, and every seam end and measure point on the twin is
    moved to the nearest point of the new outline; its notches become `name`'s mirrored. Returns the twin's name."""
    tw = twin_of(spec, name)
    if tw is None:
        raise ValueError(rpt("{0} has no twin", name))
    pa, pb = piece(spec, name), piece(spec, tw)
    ol0 = S.Outline(pb)
    refs = [(put, ol0.at(ol0.s_of(*get()))) for get, put in point_refs(spec, tw)]   # where each point was
    M = mirror_fn(pa, pb)
    pb['edges'] = []
    for e in reversed(pa['edges']):
        ne = {'id': e['id'], 'pts': _r(M(e['pts'])[::-1])}
        if e.get('ctrl'):
            ne['ctrl'] = _r(M(e['ctrl'])[::-1])
        pb['edges'].append(ne)
    ol = S.Outline(pb)
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    for put, q in refs:                              # seam ends, measure points: the nearest point of the new outline
        t = np.clip(((q - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
        d = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
        j = int(d.argmin())
        put(*ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j])))
    if pa.get('notches') or pb.get('notches'):     # the twin's edges are name's backwards: t -> 1 - t
        pb['notches'] = [dict(n, t=round(1.0 - float(n['t']), 6)) for n in pa.get('notches') or []]
    return tw


# ---------------------------------------------------------------- notches (marks on the outline, core/walk.py)

NOTCH_KINDS = ('slit', 'v', 't')


def _new_notch_id(pc):
    used = {n['id'] for n in pc.get('notches') or []}
    k = len(used) + 1
    while f"N{k}" in used:
        k += 1
    return f"N{k}"


def _twin_notch_place(spec, name, eid, t):
    """(twin, edge id, t) of the mirror image of the point (eid, t) of piece `name`, or None."""
    tw, cmap, _ = twin_corner_map(spec, name)
    if not cmap:
        return None
    pa, pb = piece(spec, name), piece(spec, tw)
    kb, rev = _twin_edge(pa, pb, cmap, edge_index(pa, eid))
    if kb is None:
        return None
    return tw, pb['edges'][kb]['id'], (1.0 - t) if rev else t


def _twin_notch(spec, name, nid):
    """(twin, the twin's notch id) mirroring notch nid of `name`: the twin's notch nearest the mirror image."""
    pc = piece(spec, name)
    n = next((x for x in pc.get('notches') or [] if x['id'] == nid), None)
    pl = _twin_notch_place(spec, name, n['edge'], float(n['t'])) if n else None
    if pl is None:
        return None, None
    tw, eb, tb = pl
    pb = piece(spec, tw)
    olb = S.Outline(pb)
    q = olb.at(olb.s_of(eb, tb))
    best = None
    for m in pb.get('notches') or []:
        d = float(np.linalg.norm(olb.at(olb.s_of(m['edge'], float(m['t']))) - q))
        if d < 0.5 and (best is None or d < best[0]):
            best = (d, m['id'])
    return (tw, best[1]) if best else (tw, None)


def _self_mirror_notch(pc, nid):
    """The id of the notch at the mirror image of notch nid across the center line of a piece edited symmetrically
    (within 0.5 mm), or None (also for a notch on the line: it is its own mirror)."""
    n = next((x for x in pc.get('notches') or [] if x['id'] == nid), None)
    mp = _self_mirror_place(pc, n['edge'], float(n['t'])) if n else None
    if mp is None:
        return None
    ol = S.Outline(pc)
    q = ol.at(ol.s_of(*mp))
    best = None
    for m in pc.get('notches') or []:
        d = float(np.linalg.norm(ol.at(ol.s_of(m['edge'], float(m['t']))) - q))
        if m['id'] != nid and d < 0.5 and (best is None or d < best[0]):
            best = (d, m['id'])
    return best[1] if best else None


def _on_center(pc, eid, t, mp):
    """The point (eid, t) and its mirror mp are the same point (within 0.5 mm): it lies on the center line."""
    ol = S.Outline(pc)
    return float(np.linalg.norm(ol.at(ol.s_of(eid, t)) - ol.at(ol.s_of(*mp)))) < 0.5


def add_notch(spec, name, eid, t, kind='slit', twins=True):
    """A notch on edge `eid` of piece `name` at the fraction t (its twin gets the mirror image, and so does the other
    half of a piece edited symmetrically). Returns {piece: notch id}."""
    pc = piece(spec, name)
    edge_index(pc, eid)
    t = round(float(np.clip(t, 0.0, 1.0)), 6)
    out = {}
    mp = _self_mirror_place(pc, eid, t)
    pl = _twin_notch_place(spec, name, eid, t) if twins else None
    pl2 = _twin_notch_place(spec, name, *mp) if twins and mp else None
    nid = _new_notch_id(pc)
    pc.setdefault('notches', []).append({'id': nid, 'edge': eid, 't': t, 'kind': kind})
    out[name] = nid
    if mp and not _on_center(pc, eid, t, mp):
        pc['notches'].append({'id': _new_notch_id(pc), 'edge': mp[0], 't': round(float(mp[1]), 6), 'kind': kind})
    else:
        pl2 = None
    for p in (pl, pl2):
        if p:
            tw, eb, tb = p
            pb = piece(spec, tw)
            nb = _new_notch_id(pb)
            pb.setdefault('notches', []).append({'id': nb, 'edge': eb, 't': round(float(tb), 6), 'kind': kind})
            out.setdefault(tw, nb)
    return out


def move_notch(spec, name, nid, eid, t, twins=True):
    """Put notch nid of piece `name` at (eid, t) (its twin's mirror notch follows, and the mirror notch across the
    center line of a piece edited symmetrically). Returns the pieces changed."""
    pc = piece(spec, name)
    sid = _self_mirror_notch(pc, nid)
    jobs = [(nid, eid, float(t))]
    if sid:
        mp = _self_mirror_place(pc, eid, float(t))
        if mp:
            jobs.append((sid, mp[0], float(mp[1])))
    out = [name]
    twins_of = [(_twin_notch(spec, name, j[0]) if twins else (None, None)) for j in jobs]
    for (i, e, tt), (tw, tid) in zip(jobs, twins_of):
        n = next(x for x in pc['notches'] if x['id'] == i)
        n['edge'], n['t'] = e, round(float(np.clip(tt, 0.0, 1.0)), 6)
        if tid:
            pl = _twin_notch_place(spec, name, e, tt)
            if pl:
                m = next(x for x in piece(spec, tw)['notches'] if x['id'] == tid)
                m['edge'], m['t'] = pl[1], round(float(pl[2]), 6)
                out.append(tw)
    return list(dict.fromkeys(out))


def remove_notch(spec, name, nid, twins=True):
    """Remove notch nid of piece `name` (and its twin's mirror notch, and the mirror notch across the center line of a
    piece edited symmetrically). Returns the pieces changed."""
    pc = piece(spec, name)
    ids = [nid] + [x for x in [_self_mirror_notch(pc, nid)] if x]
    out = [name]
    for i in ids:
        tw, tid = _twin_notch(spec, name, i) if twins else (None, None)
        pc['notches'] = [x for x in pc.get('notches') or [] if x['id'] != i]
        if tid:
            pb = piece(spec, tw)
            pb['notches'] = [x for x in pb.get('notches') or [] if x['id'] != tid]
            out.append(tw)
    return list(dict.fromkeys(out))


def notch_snap_arcs(spec, name, skip=None):
    """(the outline, [(outline arc s, kind)]) where a notch on piece `name` snaps (user 2026-11-11, Fable review: not
    the corners, a notch is rarely on one): each edge's middle ('mid') and, on a side of a seam, where the other
    side's notches fall in proportion to the two sides' lengths ('partner', as Match Notches lays them). skip: a notch
    id of this piece left out (the one being moved)."""
    from . import walk as W
    pc = piece(spec, name)
    ol = S.Outline(pc)
    out = [(s0 + 0.5 * L, 'mid') for s0, L, _ in ol.edges.values()]
    for sm in spec.get('seams', []):
        for k in 'ab':
            sd, so = sm[k], sm['b' if k == 'a' else 'a']
            if sd.get('piece') != name or 'line' in sd or 'line' in so:
                continue
            Lk, Lo = S.side_length(spec, sd), S.side_length(spec, so)
            if Lo <= 0:
                continue
            s0 = ol.s_of(*sd['from'])
            for v, n in W.side_notches(spec, so):
                if so['piece'] == name and n['id'] == skip:
                    continue
                u = v * Lk / Lo
                out.append((float(np.mod(s0 + (u if sd['dir'] > 0 else -u), ol.L)), 'partner'))
    return ol, out


def snap_notch(spec, name, eid, t, tol, skip=None):
    """(edge id, t, kind) of the notch place (eid, t) on piece `name` moved onto the nearest snap place within tol mm
    of outline arc (notch_snap_arcs), or (eid, t, None)."""
    ol, arcs = notch_snap_arcs(spec, name, skip)
    s = ol.s_of(eid, t)
    best = None
    for a, kind in arcs:
        d = abs(np.mod(s - a + 0.5 * ol.L, ol.L) - 0.5 * ol.L)
        if d <= tol and (best is None or d < best[0]):
            best = (d, a, kind)
    if best is None:
        return eid, t, None
    e, tt = ol.edge_t(best[1])
    return e, tt, best[2]


def notch_at_arc(spec, side, u):
    """(edge id, t) of the point at arc u (mm) from a side's 'from' end along the side."""
    ol = S.Outline(piece(spec, side['piece']))
    s0 = ol.s_of(*side['from'])
    return ol.edge_t(s0 + (u if side['dir'] > 0 else -u))


def add_notch_pair(spec, seam_id, key, u, one_side=False):
    """A notch at arc u (mm) along side `key` of a seam and, unless one_side, its partner on the other side at the
    same place of the seam (user 2026-10-20: Edit Seam's N marks both sides): between the notches the two sides
    already pair, in proportion (the segment's ease stays spread as it was); with unpaired notches, in proportion to
    the whole side. Twins follow. Returns (pieces changed, notches laid)."""
    from . import walk as W
    sm = next(x for x in spec['seams'] if x['id'] == seam_id)
    other = 'b' if key == 'a' else 'a'
    sd, so = sm[key], sm[other]
    if 'line' in sd or (not one_side and 'line' in so):
        raise ValueError(rpt("notches are on outlines only"))
    Lk = S.side_length(spec, sd)
    u = float(np.clip(u, 0.0, Lk))
    if not one_side:
        Lo = S.side_length(spec, so)
        uk = [x for x, _ in W.side_notches(spec, sd)]
        uo = [x for x, _ in W.side_notches(spec, so)]
        if len(uk) != len(uo):
            uk, uo = [], []
        v = float(np.interp(u, [0.0] + uk + [Lk], [0.0] + uo + [Lo]))
    changed = set()
    eid, t = notch_at_arc(spec, sd, u)
    changed.update(add_notch(spec, sd['piece'], eid, t))
    if one_side:
        return sorted(changed), 1
    eid, t = notch_at_arc(spec, so, v)
    changed.update(add_notch(spec, so['piece'], eid, t))
    return sorted(changed), 2


def match_notches(spec, seam_id, key):
    """Lay the notches of side `key` of a seam where the other side's notches are, by fraction of each side's length:
    the ease of the seam spread over its segments in proportion to their lengths (each segment's ease / length is the
    seam's). Side `key` gets as many notches as the other has (its notches inside the side are replaced; its twin
    follows). Returns (pieces changed, notches laid)."""
    from . import walk as W
    sm = next(x for x in spec['seams'] if x['id'] == seam_id)
    other = 'b' if key == 'a' else 'a'
    sd, so = sm[key], sm[other]
    if 'line' in sd or 'line' in so:
        raise ValueError(rpt("notches are on outlines only"))
    Lk, Lo = S.side_length(spec, sd), S.side_length(spec, so)
    ref = W.side_notches(spec, so)
    if not ref:
        raise ValueError(rpt("side {0} has no notches", other.upper()))
    name = sd['piece']
    changed = {name}
    for _, n in W.side_notches(spec, sd):
        changed.update(remove_notch(spec, name, n['id']))
    for u, n in ref:
        eid, t = notch_at_arc(spec, sd, u * Lk / Lo)
        changed.update(add_notch(spec, name, eid, t, n.get('kind', 'slit')))
    return sorted(changed), len(ref)


def delete_piece(spec, name):
    """Remove a piece and its owned relationships; never reconnect them to a reused name."""
    spec['pieces'] = [p for p in spec['pieces'] if p['name'] != name]
    gone = {sm['id'] for sm in spec.get('seams', []) if name in (sm['a']['piece'], sm['b']['piece'])}
    spec['seams'] = [sm for sm in spec.get('seams', []) if sm['id'] not in gone]
    _remove_seam_dependents(spec, gone)
    spec.get('placement', {}).pop(name, None)
    spec['folds'] = [f for f in spec.get('folds', [])
                     if f.get('piece') != name and f.get('turn') != name and f.get('seam') not in gone]
    if 'closures' in spec:
        spec['closures'] = [c for c in spec['closures'] if name not in c]
    if 'pairs' in spec:
        spec['pairs'] = [pair for pair in spec['pairs'] if name not in pair]
    for pc in spec['pieces']:
        if pc.get('closes_onto') == name:
            pc.pop('closes_onto')
    if 'elastic' in spec:
        spec['elastic'] = [entry for entry in spec['elastic'] if entry.get('piece') != name]
    measure = spec.get('measure')
    if isinstance(measure, dict) and 'pattern' in measure:
        measure['pattern'] = [entry for entry in measure['pattern']
                              if not any(isinstance(entry.get(k), dict) and entry[k].get('piece') == name
                                         for k in ('a', 'b'))]
    if 'attach' in spec:
        spec['attach'] = [entry for entry in spec['attach']
                          if entry.get('seam') not in gone and (entry.get('side') or {}).get('piece') != name]
    if 'tacks' in spec:
        spec['tacks'] = [entry for entry in spec['tacks'] if name not in (entry.get('a'), entry.get('b'))]
    if 'zippers' in spec:
        spec['zippers'] = [entry for entry in spec['zippers']
                           if not any(entry[key].get('piece') == name for key in ('a', 'b'))]
    if 'buttons' in spec:
        spec['buttons'] = [entry for entry in spec['buttons'] if entry.get('piece') != name]
        for entry in spec['buttons']:
            if (entry.get('hole') or {}).get('piece') == name:
                entry['hole'], entry['fastened'] = None, False
    return sorted(gone)


# ---------------------------------------------------------------- checks and lengths

def _segments_cross(P):
    """True when the closed polyline P crosses itself (segments that do not share an end)."""
    P = np.asarray(P, float)
    n = len(P)
    A, B = P, np.roll(P, -1, 0)
    for i in range(n):
        a, b = A[i], B[i]
        j = np.arange(i + 2, n if i > 0 else n - 1)
        if not len(j):
            continue
        c, d = A[j], B[j]

        def orient(p, q, r):
            return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
        o1, o2 = orient(a, b, c), orient(a, b, d)
        o3, o4 = orient(c, d, a), orient(c, d, b)
        if np.any((o1 * o2 < 0) & (o3 * o4 < 0)):
            return True
    return False


def problems(spec, names):
    """Why the pieces `names` cannot be kept as they are (empty = fine)."""
    out = []
    for nm in names:
        pc = piece(spec, nm)
        B = S.Outline(pc).B
        if signed_area(B) <= 0:
            out.append(f"{nm}: the outline turned inside out")
        elif _segments_cross(B):
            out.append(f"{nm}: the outline crosses itself")
        for e in pc['edges']:
            if S.polyline_len(e['pts']) < MIN_EDGE:
                out.append(f"{nm}: edge {e['id']} is shorter than {len_text(MIN_EDGE)}")
    out += [x for x in S.check(spec) if any(x.split(':')[0] == sm['id'] for sm in spec['seams']
                                             if sm['a']['piece'] in names or sm['b']['piece'] in names)]
    return out


def seam_lengths(spec, names=None):
    """[(seam id, length a, length b)] for the seams that touch the pieces `names` (all when None)."""
    ol = {p['name']: S.Outline(p) for p in spec['pieces']}
    out = []
    for sm in spec.get('seams', []):
        if names is not None and sm['a']['piece'] not in names and sm['b']['piece'] not in names:
            continue
        try:
            out.append((sm['id'], S.side_length(spec, sm['a'], ol), S.side_length(spec, sm['b'], ol)))
        except Exception:
            continue
    return out


def seam_end_points(spec):
    """{(seam id, side, 'from'|'to'): (x, y)} -- where every outline seam end is, for checking an edit kept them."""
    ol = {p['name']: S.Outline(p) for p in spec['pieces']}
    out = {}
    for sm in spec.get('seams', []):
        for k in ('a', 'b'):
            sd = sm[k]
            if 'line' in sd:
                continue
            o = ol[sd['piece']]
            for end in ('from', 'to'):
                out[(sm['id'], k, end)] = o.at(o.s_of(*sd[end])).tolist()
    return out
