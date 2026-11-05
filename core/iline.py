# SPDX-License-Identifier: GPL-3.0-or-later
"""The drafting shape of an internal line: vertices and curved spans (user 2026-10-28).

An internal line's 'pts' stays the line everything else reads (the mesh, seams, folds, snapping, the 3D). A line
shaped here may also hold 'nodes' (indices into pts: the vertices one drags) and 'ctrl' (one per span between two
vertices: None for straight, or [h0, h1], its Bezier handles); pts is then sampled from them as a curved edge is.
Without them every point is a vertex and every span straight. An edit that moves pts without them (slash & spread, a
cut) leaves them stale: shape() then reads every point as a vertex, and the next edit here writes them afresh.

Each edit gives the line's mirror lines (the twin's, across a piece's center line: draft.mirror_lines) the mirror
image; a line that is its own mirror image (across or on the center line) is edited at both mirrored places."""
import numpy as np
from ._i18n import rpt

from . import draft as D

TOL = 0.05         # mm: pts sampled again from nodes and ctrl must match the stored pts within this
DENSE = 12         # a line without vertices of its own and more points than this (a curve read from a BOM, an old
                   # ellipse) offers no vertices to drag: K or C first gives it vertices and handles
NEAR = 1.0         # mm: a vertex this close to the mirror image of another is its partner
MIN_GAP = 0.5      # mm: a new vertex at least this far from the span's ends


def _spans(n, closed):
    return n if closed else n - 1


def sample(N, C, closed):
    """(pts (m, 2), indices of the vertices in pts) of the line through vertices N with span handles C."""
    N = np.asarray(N, float)
    n = len(N)
    out, idx = [], []
    for s in range(_spans(n, closed)):
        a, b = N[s], N[(s + 1) % n]
        idx.append(len(out))
        seg = D.bezier_pts(a, C[s][0], C[s][1], b) if C[s] is not None else np.array([a, b])
        out.extend(seg[:-1])
    if closed:
        out.append(N[0])
    else:
        idx.append(len(out))
        out.append(N[-1])
    return np.asarray(out, float), idx


def shape(it):
    """(vertices (n, 2), span handles [None | (2, 2)], closed, own) of internal line it; own: the line holds its
    vertices (False: every point read as one)."""
    P = np.asarray(it['pts'], float)
    closed = bool(it.get('closed'))
    nodes, ctrl = it.get('nodes'), it.get('ctrl')
    if nodes is not None and ctrl is not None:
        try:
            N = P[np.asarray(nodes, int)]
            C = [None if c is None else np.asarray(c, float).reshape(2, 2) for c in ctrl]
            if len(C) == _spans(len(N), closed):
                Q, idx = sample(N, C, closed)
                if len(Q) == len(P) and list(idx) == [int(i) for i in nodes] and np.abs(Q - P).max() < TOL:
                    return N.copy(), C, closed, True
        except (IndexError, TypeError, ValueError):
            pass
    N = P[:-1] if closed and len(P) > 1 and np.linalg.norm(P[0] - P[-1]) < 1e-6 else P
    return N.copy(), [None] * _spans(len(N), closed), closed, False


def editable(it):
    """The line offers its vertices to drag (its own, or a hand-drawn polyline of few points)."""
    N, _, _, own = shape(it)
    return own or len(N) <= DENSE


def set_shape(it, N, C):
    """Write vertices N and span handles C into internal line it (pts sampled from them)."""
    closed = bool(it.get('closed'))
    # rounded before sampling, as stored: shape() samples the stored values again, and a curve's point count can
    # change with the rounding (31 -> 32 points seen), which read the line as stale
    N = np.asarray(D._r(np.asarray(N, float)), float)
    C = [None if c is None else np.asarray(D._r(np.asarray(c, float).reshape(2, 2)), float) for c in C]
    P, idx = sample(N, C, closed)
    it['pts'] = D._r(P)
    if any(c is not None for c in C):
        it['nodes'] = [int(i) for i in idx]
        it['ctrl'] = [None if c is None else D._r(c) for c in C]
    else:
        it.pop('nodes', None)
        it.pop('ctrl', None)


def _targets(spec, name, lid):
    """[(piece, line id, map, reversed)]: the mirror lines of line lid (before the edit) and whether each runs the
    other way along the mirror image."""
    P = np.asarray(D.internal(D.piece(spec, name), lid)['pts'], float)
    out = []
    for nm, l2, M in D.mirror_lines(spec, name, lid):
        Q = np.asarray(D.internal(D.piece(spec, nm), l2)['pts'], float)
        MP = np.asarray(M(P), float)
        rev = (not D.internal(D.piece(spec, nm), l2).get('closed')
               and np.linalg.norm(Q[0] - MP[-1]) < np.linalg.norm(Q[0] - MP[0]))
        out.append((nm, l2, M, rev))
    return out


def _copy_to(src, dst, M, rev):
    N, C, _, _ = shape(src)
    N2 = np.asarray(M(N), float)
    C2 = [None if c is None else np.asarray(M(c), float) for c in C]
    if rev:
        N2 = N2[::-1]
        C2 = [None if c is None else c[::-1] for c in C2[::-1]]
    set_shape(dst, N2, C2)


def _edit(spec, name, lid, fn):
    """fn(N, C, closed, R) -> (N, C) on line lid of piece name, its mirror lines given the mirror image. R: the
    piece's reflection across its center line when the line is its own mirror image, else None. Returns the pieces
    changed."""
    pc = D.piece(spec, name)
    it = D.internal(pc, lid)
    targets = _targets(spec, name, lid)
    ms, _ = D.self_mirror_line(pc, lid)
    R = D.self_reflect(pc) if ms == lid else None
    N, C, closed, _ = shape(it)
    N, C = fn(N, C, closed, R)
    set_shape(it, N, C)
    for nm, l2, M, rev in targets:
        _copy_to(it, D.internal(D.piece(spec, nm), l2), M, rev)
    return list(dict.fromkeys([name] + [t[0] for t in targets]))


def _partner(N, i, R):
    """The vertex at the mirror image of vertex i (i itself on the center line), or None."""
    if R is None:
        return None
    d = np.linalg.norm(N - R([N[i]])[0], axis=1)
    j = int(d.argmin())
    return j if d[j] < NEAR else None


def _span_of(n, closed, i, j):
    """(span, reversed) running between vertices i and j, or (None, False)."""
    for s in range(_spans(n, closed)):
        a, b = s, (s + 1) % n
        if (a, b) == (i, j):
            return s, False
        if (a, b) == (j, i):
            return s, True
    return None, False


def _move(N, C, closed, k, m):
    n = len(N)
    N[k] = N[k] + m
    for s, w in (((k - 1) % n if closed or k > 0 else None, 1), (k if closed or k < n - 1 else None, 0)):
        if s is not None and C[s] is not None:
            C[s] = C[s].copy()
            C[s][w] = C[s][w] + m


def move_node(spec, name, lid, i, d):
    """Move vertex i of internal line lid by d mm (its handles go along). Returns the pieces changed."""
    d = np.asarray(d, float)

    def fn(N, C, closed, R):
        j = _partner(N, i, R)
        if j is None:
            _move(N, C, closed, i, d)
        else:
            dm = R([N[i] + d])[0] - N[j]
            if j == i:                                 # on the center line: stays on it
                _move(N, C, closed, i, 0.5 * (d + dm))
            else:
                _move(N, C, closed, i, d)
                _move(N, C, closed, j, dm)
        return N, C
    return _edit(spec, name, lid, fn)


def set_handle(spec, name, lid, s, w, pos):
    """Put handle w (0: by the span's first vertex) of span s of internal line lid at pos (layout mm)."""
    pos = np.asarray(pos, float)

    def fn(N, C, closed, R):
        if C[s] is None:
            raise ValueError(rpt("that part of the line is straight: C curves it"))
        n = len(N)
        C[s] = C[s].copy()
        C[s][w] = pos
        a, b = _partner(N, s, R), _partner(N, (s + 1) % n, R)
        if a is not None and b is not None:
            t, rev = _span_of(n, closed, a, b)
            if t is not None and C[t] is not None:
                w2 = 1 - w if rev else w
                if (t, w2) != (s, w):
                    C[t] = C[t].copy()
                    C[t][w2] = R([pos])[0]
        return N, C
    return _edit(spec, name, lid, fn)


def locate(it, q):
    """(span, t, distance mm) of the point of internal line it nearest q."""
    N, C, closed, _ = shape(it)
    return _locate(N, C, closed, q)


def _locate(N, C, closed, q):
    n = len(N)
    q = np.asarray(q, float)
    best = None
    for s in range(_spans(n, closed)):
        a, b = N[s], N[(s + 1) % n]
        if C[s] is None:
            ab = b - a
            t = float(np.clip((q - a) @ ab / max(ab @ ab, 1e-12), 0.0, 1.0))
            dist = float(np.linalg.norm(a + t * ab - q))
        else:
            B = D.bezier(a, C[s][0], C[s][1], b, 400)
            j = int(np.linalg.norm(B - q, axis=1).argmin())
            t, dist = j / 399.0, float(np.linalg.norm(B[j] - q))
        if best is None or dist < best[2]:
            best = (s, t, dist)
    return best


def _split(N, C, closed, s, t):
    """Insert a vertex in span s at parameter t (a curve split so its shape stays). Returns its index."""
    n = len(N)
    a, b = N[s], N[(s + 1) % n]
    if C[s] is None:
        m = a + t * (b - a)
        left = right = None
    else:
        h0, h1 = C[s]
        p01, p12, p23 = a + t * (h0 - a), h0 + t * (h1 - h0), h1 + t * (b - h1)
        p012, p123 = p01 + t * (p12 - p01), p12 + t * (p23 - p12)
        m = p012 + t * (p123 - p012)
        left, right = np.array([p01, p012]), np.array([p123, p23])
    if min(np.linalg.norm(m - a), np.linalg.norm(m - b)) < MIN_GAP:
        raise ValueError(rpt("too close to a point of the line"))
    N2 = np.insert(N, s + 1, m, axis=0)
    C[s] = left
    C.insert(s + 1, right)
    return N2, s + 1


def insert_node(spec, name, lid, q):
    """Add a vertex on internal line lid at its point nearest q (layout mm); a line read as one long curve (more than
    DENSE points of its own) is fitted with a curve each side of the new vertex. Returns the pieces changed."""
    it = D.internal(D.piece(spec, name), lid)
    if not editable(it):
        return _fit_split(spec, name, lid, q)

    def fn(N, C, closed, R):
        s, t, _ = _locate(N, C, closed, q)
        N, k = _split(N, C, closed, s, t)
        if R is not None:
            qm = R([N[k]])[0]
            if np.linalg.norm(qm - N[k]) > NEAR:
                s2, t2, _ = _locate(N, C, closed, qm)
                N, _ = _split(N, C, closed, s2, t2)
        return N, C
    return _edit(spec, name, lid, fn)


def _fit_split(spec, name, lid, q):
    """A dense line (no vertices of its own): two curves fitted to it, either side of its point nearest q."""
    it = D.internal(D.piece(spec, name), lid)
    if it.get('closed'):
        raise ValueError(rpt("this closed line has no points to edit: draw it again with the Ellipse or Rectangle tool"))
    P = np.asarray(it['pts'], float)
    j = int(np.linalg.norm(P - np.asarray(q, float), axis=1).argmin())
    j = min(max(j, 1), len(P) - 2)

    def fn(N, C, closed, R):
        h0, _ = D.fit_bezier(P[:j + 1])
        h1, _ = D.fit_bezier(P[j:])
        return np.array([P[0], P[j], P[-1]]), [np.asarray(h0, float), np.asarray(h1, float)]
    return _edit(spec, name, lid, fn)


def fit_error(it):
    """Largest distance (mm) between a dense line and one curve fitted to it (what C would make of it)."""
    return float(D.fit_bezier(np.asarray(it['pts'], float))[1])


def delete_node(spec, name, lid, i):
    """Remove vertex i of internal line lid: its two spans become one (a curve if either was). Returns the pieces
    changed."""
    def drop(N, C, closed, k):
        n = len(N)
        if n - 1 < (3 if closed else 2):
            raise ValueError(rpt("a line keeps at least 3 points" if closed else "a line keeps at least 2 points: X on the line removes it"))
        if not closed and k in (0, n - 1):
            return np.delete(N, k, axis=0), (C[1:] if k == 0 else C[:-1])
        sl, sr = (k - 1) % n, k
        a, b = N[(k - 1) % n], N[(k + 1) % n]
        if C[sl] is None and C[sr] is None:
            merged = None
        else:
            merged = np.array([C[sl][0] if C[sl] is not None else a + (b - a) / 3.0,
                               C[sr][1] if C[sr] is not None else a + 2.0 * (b - a) / 3.0])
        C = list(C)
        C[sl] = merged
        del C[sr]
        N = np.delete(N, k, axis=0)
        return N, C

    def fn(N, C, closed, R):
        j = _partner(N, i, R)
        for k in sorted({i} | ({j} if j is not None else set()), reverse=True):
            N, C = drop(N, C, closed, k)
        return N, C
    return _edit(spec, name, lid, fn)


def toggle_curve(spec, name, lid, q, span=None):
    """C on internal line lid near q: its span there curved (handles at a third, so the shape stays) or straight
    again; a dense line (a curve read from a BOM) becomes one curve fitted to it. Returns the pieces changed."""
    it = D.internal(D.piece(spec, name), lid)
    if not editable(it):
        if it.get('closed'):
            raise ValueError(rpt("this closed line has no points to edit: draw it again with the Ellipse or Rectangle tool"))
        P = np.asarray(it['pts'], float)

        def fit(N, C, closed, R):
            h, _ = D.fit_bezier(P)
            return np.array([P[0], P[-1]]), [np.asarray(h, float)]
        return _edit(spec, name, lid, fit)
    s = span if span is not None else locate(it, q)[0]

    def fn(N, C, closed, R):
        n = len(N)
        curve = C[s] is None

        def flip(t):
            a, b = N[t], N[(t + 1) % n]
            C[t] = np.array([a + (b - a) / 3.0, a + 2.0 * (b - a) / 3.0]) if curve else None
        flip(s)
        a, b = _partner(N, s, R), _partner(N, (s + 1) % n, R)
        if a is not None and b is not None:
            t, _ = _span_of(n, closed, a, b)
            if t is not None and t != s:
                flip(t)
        return N, C
    return _edit(spec, name, lid, fn)


def add_shape(spec, name, N, C, kind='shape', closed=True):
    """A new internal line with vertices N and span handles C (the Ellipse tool's four quarters). Returns its id."""
    P, _ = sample(N, C, closed)
    lid = D.add_internal(spec, name, P, kind=kind, closed=closed)
    it = D.internal(D.piece(spec, name), lid)
    targets = _targets(spec, name, lid)
    set_shape(it, N, C)
    for nm, l2, M, rev in targets:
        _copy_to(it, D.internal(D.piece(spec, nm), l2), M, rev)
    return lid
