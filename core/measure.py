# SPDX-License-Identifier: GPL-3.0-or-later
"""Body and garment measurements (no bpy, numpy only). Coordinates are mm, z up, the avatar facing -y,
as in core/body_axes.py.

Everything is a section: the mesh cut by a plane, split into loops. A girth is read two ways --
'tape': the length of a tape measure pulled round the section (the perimeter of its convex hull, as a person
measures, bridging hollows such as between the breasts) and 'surface': the length of the section itself.
The tape is the one shown (apparel measures with a tape).

Body measurements: the girths at the levels the placement guides already use (bust / waist / hip from
body_axes.torso_levels, the limb points from the guides), the heights from the floor, and the back length.
A level can be moved by the user: an offset (mm) along its plane's normal, kept per key.

Garment measurements: the same planes cut through the dressed garment; the garment's points round each body
loop give the garment's girth there, and garment - body is the ease.
"""
import numpy as np

from . import body_axes as BA

# key -> (kind, what it is): the order the table shows them in. Names follow ISO 8559-1 where it has one.
ITEMS = (
    ('height', 'height', "Height: floor to the top of the head (hair and ears count on an avatar)"),
    ('neck_height', 'height', "Neck base height: floor to where the neck meets the shoulders"),
    ('crotch_height', 'height', "Crotch height (inside leg): floor to the crotch"),
    ('back_length', 'length', "Back length: back of the neck base down the back to the waist"),
    ('neck', 'girth', "Neck base girth: round the bottom of the neck"),
    ('chest', 'girth', "Bust / chest girth: round the fullest part of the chest"),
    ('waist', 'girth', "Waist girth: round the narrowest part of the torso"),
    ('hip', 'girth', "Hip girth: round the fullest part of the seat"),
    ('upper_arm_L', 'girth', "Upper arm girth, left: round the upper arm, across the arm"),
    ('upper_arm_R', 'girth', "Upper arm girth, right"),
    ('wrist_L', 'girth', "Wrist girth, left"),
    ('wrist_R', 'girth', "Wrist girth, right"),
    ('thigh_L', 'girth', "Thigh girth, left: round the top of the thigh"),
    ('thigh_R', 'girth', "Thigh girth, right"),
    ('knee_L', 'girth', "Knee girth, left"),
    ('knee_R', 'girth', "Knee girth, right"),
)
KIND = {k: kind for k, kind, _ in ITEMS}
HELP = {k: h for k, _, h in ITEMS}
COVER_GAP_DEG = 90.0      # the garment goes round a level when no angle gap round it is wider than this


# ---------------------------------------------------------------- sections

def _frame(n):
    n = np.asarray(n, float) / np.linalg.norm(n)
    ref = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = ref - (ref @ n) * n
    u /= np.linalg.norm(u)
    return u, np.cross(n, u), n


def section(mesh, o, n):
    """Loops of mesh (a body_axes.Body) cut by the plane through o with normal n: a list of
    (points (k, 3) in order, closed)."""
    o, n = np.asarray(o, float), np.asarray(n, float) / np.linalg.norm(n)
    B, Eu = mesh.B, mesh.Eu
    d = (B - o) @ n
    d = np.where(np.abs(d) < 1e-9, 1e-9, d)            # a vertex exactly on the plane: just off it
    da, db = d[Eu[:, 0]], d[Eu[:, 1]]
    cut = (da * db) < 0
    if not cut.any():
        return []
    ids = -np.ones(len(Eu), np.int64)
    ids[cut] = np.arange(cut.sum())
    t = da[cut] / (da[cut] - db[cut])
    P = B[Eu[cut, 0]] + t[:, None] * (B[Eu[cut, 1]] - B[Eu[cut, 0]])
    tc = ids[mesh.e_of_t]
    tc = tc[(tc >= 0).sum(1) == 2]
    pr = np.sort(tc, axis=1)[:, 1:]
    adj = [[] for _ in range(len(P))]
    for a, b in pr:
        adj[a].append(b)
        adj[b].append(a)
    seen = np.zeros(len(P), bool)
    out = []
    # open chains first (start at an end), then the closed loops
    starts = [i for i in range(len(P)) if len(adj[i]) == 1] + list(range(len(P)))
    for s in starts:
        if seen[s] or not adj[s]:
            continue
        chain, prev, cur = [s], -1, s
        seen[s] = True
        while True:
            nxt = [j for j in adj[cur] if j != prev and not seen[j]]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seen[cur] = True
            chain.append(cur)
        closed = len(chain) > 2 and s in adj[cur]
        if len(chain) >= 3:
            out.append((P[chain], closed))
    return out


def to_plane(P, o, n):
    u, v, _ = _frame(n)
    Q = np.asarray(P, float) - np.asarray(o, float)
    return np.stack([Q @ u, Q @ v], 1)


def hull(Q):
    """Convex hull of 2D points, counter-clockwise (monotone chain)."""
    Q = np.unique(np.round(np.asarray(Q, float), 6), axis=0)
    if len(Q) < 3:
        return Q

    def half(pts):
        h = []
        for p in pts:
            while len(h) >= 2 and np.cross(h[-1] - h[-2], p - h[-2]) <= 0:
                h.pop()
            h.append(p)
        return h
    lo, up = half(Q), half(Q[::-1])
    return np.array(lo[:-1] + up[:-1])


def perimeter(Q, closed=True):
    Q = np.asarray(Q, float)
    if len(Q) < 2:
        return 0.0
    if closed:
        Q = np.vstack([Q, Q[:1]])
    return float(np.linalg.norm(np.diff(Q, axis=0), axis=1).sum())


def _inside(q, Q):
    """2D point q inside the polygon Q (even-odd)."""
    x, y = q
    X, Y = Q[:, 0], Q[:, 1]
    X2, Y2 = np.roll(X, -1), np.roll(Y, -1)
    c = ((Y > y) != (Y2 > y)) & (x < (X2 - X) * (y - Y) / np.where(Y2 - Y == 0, 1e-12, Y2 - Y) + X)
    return bool(c.sum() % 2)


def ring(mesh, o, n, target=None):
    """The closed loop of mesh in the plane (o, n) round target (default o): (points (k, 3), tape, surface)
    or None. Taken: the loop that contains target, else the one nearest to it."""
    target = np.asarray(o if target is None else target, float)
    L = [P for P, closed in section(mesh, o, n) if closed]
    if not L:
        return None
    q = to_plane(target[None], o, n)[0]
    best, bd = None, None
    for P in L:
        Q = to_plane(P, o, n)
        dist = 0.0 if _inside(q, Q) else float(np.linalg.norm(Q - q, axis=1).min()) + 1e6
        if bd is None or dist < bd or (dist == bd == 0.0 and perimeter(Q) < perimeter(to_plane(best, o, n))):
            best, bd = P, dist                            # nested loops (a sleeve inside a body hole): the inner
    Q = to_plane(best, o, n)
    return best, perimeter(hull(Q)), perimeter(Q)


# ---------------------------------------------------------------- the body

def _planes(body, gd, m):
    """key -> (o, n, target) of every girth, before the user's offsets."""
    lv = BA.torso_levels(m)
    tor = gd['torso']
    c = np.asarray(tor['o'], float)
    out = {}
    for k in ('chest', 'waist', 'hip'):
        p = np.array([c[0], c[1], lv[k]])
        out[k] = (p, np.array([0.0, 0.0, 1.0]), p)
    if 'neck' in gd:
        cn = np.asarray(gd['neck']['o'], float)
        p = np.array([cn[0], cn[1], m['z_neck']])
        out['neck'] = (p, np.array([0.0, 0.0, 1.0]), p)

    def at(gname, label):
        g = gd.get(gname)
        if g is None:
            return None
        hs = [p['h'] for p in g.get('points', []) if p['name'].startswith(label)]
        if not hs:
            return None
        Rm = np.asarray(g['R'], float)
        p = np.asarray(g['o'], float) + hs[0] * Rm[2]
        return p, Rm[2].copy(), p
    for side in ('L', 'R'):
        w = _wrist(body, gd.get(f'arm_{side}'))
        if w is not None:
            out[f'wrist_{side}'] = w
        for key, gname, label in ((f'upper_arm_{side}', f'arm_{side}', 'upper arm'),
                                  (f'thigh_{side}', f'leg_{side}', 'thigh'), (f'knee_{side}', f'leg_{side}', 'knee')):
            r = at(gname, label)
            if r is not None:
                out[key] = r
    return out


def _wrist(body, g, step=5.0):
    """The wrist: walking the whole arm's line from the end of the hand towards the shoulder, the narrowest section
    after the hand's widest one (the fingers before it are narrower still). Measured on three avatars (A and T pose):
    the hand's widest lies within the first 35 % of the arm, the wrist before 60 %. The arm ends where a section
    reaches the torso (more than twice the upper arm's guide girth)."""
    if g is None or 'full' not in g:
        return None
    o = np.asarray(g['full']['o'], float)
    ez = np.asarray(g['full']['R'], float)[2]             # towards the shoulder
    D = body.B - o
    t = D @ ez
    near = np.linalg.norm(D - t[:, None] * ez, axis=1) < 1.5 * float(g['r'])
    if not near.any():
        return None
    limit = 2.0 * 2.0 * np.pi * float(g['r'])
    prof = []
    for tt in np.arange(float(t[near].min()) + step, float(t[near].max()), step):
        p = o + tt * ez
        r = ring(body, p, ez, p)
        if r is None or not _inside(np.zeros(2), to_plane(r[0], p, ez)):
            continue
        if r[1] > limit:
            break
        prof.append((tt, r[1], p))
    if len(prof) < 5:
        return None
    t0, L = prof[0][0], prof[-1][0] - prof[0][0]
    hand = [x for x in prof if x[0] <= t0 + 0.35 * L]
    th = max(hand, key=lambda x: x[1])[0]
    cand = [x for x in prof if th < x[0] <= t0 + 0.60 * L]
    if not cand:
        return None
    p = min(cand, key=lambda x: x[1])[2]
    return p, ez.copy(), p


def _back_length(body, gd, m, z_waist):
    """Back of the neck base down to the waist along the back, a tape bridging the hollow of the back: the outer
    (back) envelope of the body's middle section between the two heights. (length, polyline (k, 3)) or None."""
    c = np.asarray(gd['torso']['o'], float)
    o = np.array([c[0] + 1.0, c[1], 0.5 * (m['z_neck'] + z_waist)])   # 1 mm off a mirror seam's vertices on x = 0
    secs = section(body, o, np.array([1.0, 0.0, 0.0]))     # the body is rarely watertight here (a separate head):
    if not secs:                                            # open chains count as well
        return None
    P = np.vstack([Q for Q, _ in secs])
    zn = m['z_neck']
    S = P[(P[:, 2] >= z_waist) & (P[:, 2] <= zn) & (P[:, 1] > c[1])]
    if len(S) < 3:
        return None
    # the back's profile only: per 2 mm of height the outermost point (the neck's front is also behind the
    # torso's center at the top, and took the tape 54 mm across the neck)
    b = np.floor((S[:, 2] - z_waist) / 2.0).astype(int)
    S = np.array([S[b == k][np.argmax(S[b == k][:, 1])] for k in np.unique(b)])
    S = S[np.argsort(S[:, 2])]
    keep = [S[0]]
    for p in S[1:]:                                       # a sudden step inwards is the neck's front, not the back
        if not (p[2] - keep[-1][2] < 10.0 and keep[-1][1] - p[1] > 15.0):
            keep.append(p)
    S = np.array(keep)
    # upper envelope in (z, y): the tape lies on the outermost points of the back
    pts = [S[0]]
    for p in S[1:]:
        while len(pts) >= 2:
            a, b = pts[-2], pts[-1]
            if (b[2] - a[2]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[2] - a[2]) >= 0:   # b under the chord a-p
                pts.pop()
            else:
                break
        pts.append(p)
    E = np.array(pts)
    return perimeter(E, closed=False), E


def measure_body(body, gd, m, offsets=None, only=None, planes=None):
    """The body's measurements. body: body_axes.Body (mm, world); gd, m: body_axes.guides(body).
    offsets: {key: mm along the level's normal} the user moved a level by. only: the keys to measure (None: all; a
    level being dragged measures its own line only, 2026-10-21: all of them took 1.4 s a mouse move).
    planes: _planes(body, gd, m) when the caller kept it (the wrists' search cuts 260 rings, 1.3 s on a 31k body).
    Returns {key: {'kind', 'value', 'tape', 'surface', 'pts', 'o', 'n', 'offset'}} (mm; 'pts' world mm)."""
    offsets = offsets or {}
    want = (lambda k: True) if only is None else (lambda k: k in only)
    out = {}
    z0 = m['z_min']
    side_x = float(body.B[:, 0].max()) + 40.0
    c = np.asarray(gd['torso']['o'], float)
    for key, z in (('height', m['z_top']), ('neck_height', m['z_neck'] + offsets.get('neck_height', 0.0)),
                   ('crotch_height', m['z_crotch'] + offsets.get('crotch_height', 0.0))):
        if not want(key):
            continue
        pts = np.array([[side_x, c[1], z0], [side_x, c[1], z]])
        out[key] = {'kind': 'height', 'value': float(z - z0), 'pts': pts, 'o': pts[1], 'n': np.array([0.0, 0.0, 1.0]),
                    'offset': float(offsets.get(key, 0.0))}
    planes = planes if planes is not None else _planes(body, gd, m)
    for key, (o, n, tgt) in planes.items():
        if not want(key):
            continue
        off = float(offsets.get(key, 0.0))
        o2, t2 = o + off * n, tgt + off * n
        r = ring(body, o2, n, t2)
        if r is None:
            continue
        P, tape, surf = r
        out[key] = {'kind': 'girth', 'value': tape, 'tape': tape, 'surface': surf, 'pts': P, 'o': o2, 'n': n,
                    'target': t2, 'offset': off}
    if 'waist' in out and want('back_length'):
        bl = _back_length(body, gd, m, float(out['waist']['o'][2]))
        if bl is not None:
            out['back_length'] = {'kind': 'length', 'value': bl[0], 'pts': bl[1], 'o': bl[1][-1],
                                  'n': np.array([0.0, 0.0, 1.0]), 'offset': 0.0}
    return {k: out[k] for k, _, _ in ITEMS if k in out}


def ordered(res):
    return [(k, res[k]) for k, _, _ in ITEMS if k in res]


# ---------------------------------------------------------------- the garment

def garment_girth(body, garment, item):
    """The garment's tape girth round the body loop of item (a girth from measure_body), in the same plane.
    Returns {'value', 'ease', 'covered' (bool), 'gap_deg', 'pts' (the garment's tape, world mm)} or None when
    the garment does not reach the plane there."""
    o, n = np.asarray(item['o'], float), np.asarray(item['n'], float)
    G = [P for P, _ in section(garment, o, n)]
    if not G:
        return None
    G = np.vstack(G)
    Bl = [P for P, closed in section(body, o, n) if closed]
    mine = np.asarray(item['pts'], float)
    # each garment point belongs to the body loop nearest to it (a sleeve's points to the arm, not the torso)
    Qg = to_plane(G, o, n)
    best = np.full(len(G), np.inf)
    owner = np.full(len(G), -1)
    for i, P in enumerate([mine] + Bl):
        Qb = to_plane(P, o, n)
        d = np.sqrt(((Qg[:, None, :] - Qb[None, :, :]) ** 2).sum(-1)).min(1)
        better = d < best - 1e-9
        best[better] = d[better]
        owner[better] = i
    # loop 0 is the item's own loop; the same loop found again in Bl ties and stays 0
    sel = Qg[owner == 0]
    if len(sel) < 3:
        return None
    cq = to_plane(mine, o, n).mean(0)
    ang = np.sort(np.degrees(np.arctan2(sel[:, 1] - cq[1], sel[:, 0] - cq[0])))
    gaps = np.diff(np.r_[ang, ang[0] + 360.0])
    gap = float(gaps.max())
    H = hull(sel)
    u, v, _ = _frame(n)
    pts = o + H[:, :1] * u + H[:, 1:] * v
    val = perimeter(H)
    covered = gap <= COVER_GAP_DEG
    return {'value': val if covered else None, 'ease': (val - item['tape']) if covered else None,
            'covered': covered, 'gap_deg': gap, 'pts': pts}


def measure_garment(body, garment, body_res):
    """{key: garment_girth(...)} for every body girth the garment goes round (or reaches)."""
    out = {}
    for k, it in ordered(body_res):
        if it['kind'] != 'girth':
            continue
        g = garment_girth(body, garment, it)
        if g is not None:
            out[k] = g
    return out


# ---------------------------------------------------------------- tapes on the body

def tape_around(body, p, n):
    """A tape round the body through the surface point p, in the plane with normal n: (length, surface,
    polyline (k, 3) closed) or None."""
    r = ring(body, p, n, p)
    if r is None:
        return None
    P, tape, surf = r
    Q = to_plane(P, p, n)
    H = hull(Q)
    u, v, _ = _frame(n)
    return tape, surf, np.asarray(p, float) + H[:, :1] * u + H[:, 1:] * v


def _arc(P, closed, i, j):
    """The shorter way along the ordered loop / chain P from index i to index j (inclusive)."""
    if not closed:
        a, b = sorted((i, j))
        seg = P[a:b + 1]
        return seg if i <= j else seg[::-1]
    k = len(P)
    fwd = [(i + s) % k for s in range((j - i) % k + 1)]
    bwd = [(i - s) % k for s in range((i - j) % k + 1)]
    A, B_ = P[fwd], P[bwd]
    return A if perimeter(A, False) <= perimeter(B_, False) else B_


def tape_segment(body, p, q, n_p, n_q):
    """The body's surface from p to q: the section by the plane through p and q that holds the mean surface
    normal, the shorter way round. (length, polyline (k, 3))."""
    p, q = np.asarray(p, float), np.asarray(q, float)
    d = q - p
    if np.linalg.norm(d) < 1e-6:
        return 0.0, np.array([p, q])
    nm = np.asarray(n_p, float) + np.asarray(n_q, float)
    if np.linalg.norm(nm) < 1e-6:
        nm = np.asarray(n_p, float)
    pn = np.cross(d, nm)
    if np.linalg.norm(pn) < 1e-9:
        return float(np.linalg.norm(d)), np.array([p, q])
    best = None
    for P, closed in section(body, p, pn):
        ip = int(np.argmin(np.linalg.norm(P - p, axis=1)))
        iq = int(np.argmin(np.linalg.norm(P - q, axis=1)))
        miss = np.linalg.norm(P[ip] - p) + np.linalg.norm(P[iq] - q)
        if best is None or miss < best[0]:
            best = (miss, P, closed, ip, iq)
    if best is None or best[0] > 20.0:                 # the section missed the points: a straight line
        return float(np.linalg.norm(d)), np.array([p, q])
    _, P, closed, ip, iq = best
    A = np.vstack([p[None], _arc(P, closed, ip, iq), q[None]])
    return perimeter(A, False), A


def surface_normal(body, p):
    """The body's outward normal near p: the mean of the normals of the triangles round its nearest vertex."""
    B, T = body.B, body.T
    i = int(np.argmin(((B - np.asarray(p, float)) ** 2).sum(1)))
    t = T[(T == i).any(1)]
    if not len(t):
        return np.array([0.0, -1.0, 0.0])
    n = np.cross(B[t[:, 1]] - B[t[:, 0]], B[t[:, 2]] - B[t[:, 0]]).sum(0)
    return n / max(np.linalg.norm(n), 1e-12)


def _closest_on_tri(p, a, b, c):
    """The closest point of the triangle abc to p, as barycentric weights (3,). (Ericson, Real-Time Collision
    Detection 5.1.5.)"""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return np.array([1.0, 0.0, 0.0])
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return np.array([0.0, 1.0, 0.0])
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return np.array([1.0 - v, v, 0.0])
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return np.array([0.0, 0.0, 1.0])
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return np.array([1.0 - w, 0.0, w])
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return np.array([0.0, 1.0 - w, w])
    den = 1.0 / (va + vb + vc)
    v, w = vb * den, vc * den
    return np.array([1.0 - v - w, v, w])


def surface_anchor(body, p, k=12):
    """Where p sits on the body's skin: (triangle index, barycentric weights (3,), the point (3,)). The triangles
    round the k nearest vertices are tried. A point kept this way rides the skin when the body is reshaped with the
    same vertices (a sculpt, a proportion edit), where a point kept in space would float off it or sink in."""
    B, T = body.B, body.T
    p = np.asarray(p, float)
    near = np.argpartition(((B - p) ** 2).sum(1), min(k, len(B) - 1))[:k]
    cand = np.nonzero(np.isin(T, near).any(1))[0]
    best = None
    for ti in cand:
        a, b, c = B[T[ti]]
        w = _closest_on_tri(p, a, b, c)
        q = w @ B[T[ti]]
        d = float(((q - p) ** 2).sum())
        if best is None or d < best[0]:
            best = (d, int(ti), w, q)
    if best is None:
        return None
    return best[1], best[2], best[3]


def anchor_point(body, tri, bary):
    """The skin point of a surface_anchor on the body as it is now (None if the triangle is not there)."""
    if not 0 <= int(tri) < len(body.T):
        return None
    return np.asarray(bary, float) @ body.B[body.T[int(tri)]]


def tape_taut(body, p, q, n_p=None, n_q=None):
    """A tape pulled taut between p and q (Tape Straight, user 2026-10-21): it lies on the bulges and spans the
    hollows, as a tape measure held tight -- the upper convex hull, in tape_segment's plane, of the surface's way
    from p to q (Tape Around is the same: a girth is its section's convex hull). (length, chord, polyline (k, 3))."""
    p, q = np.asarray(p, float), np.asarray(q, float)
    chord = float(np.linalg.norm(q - p))
    if chord < 1e-6:
        return 0.0, 0.0, np.array([p, q])
    n_p = surface_normal(body, p) if n_p is None else np.asarray(n_p, float)
    n_q = surface_normal(body, q) if n_q is None else np.asarray(n_q, float)
    _, A = tape_segment(body, p, q, n_p, n_q)
    d = (q - p) / chord
    # outward: the ends' normals and the one halfway along the surface (over a shoulder, front to back, the ends'
    # normals face each other and only the middle says "up")
    mid = A[int(np.argmin(np.abs((A - p) @ d - 0.5 * chord)))]
    nm = n_p + n_q + surface_normal(body, mid)
    out = nm - (nm @ d) * d
    if len(A) < 3 or np.linalg.norm(out) < 1e-9:
        return chord, chord, np.array([p, q])
    out /= np.linalg.norm(out)
    x, y = (A - p) @ d, (A - p) @ out
    inner = [i for i in range(1, len(A) - 1) if 0.0 < x[i] < chord]
    order = [0] + sorted(inner, key=lambda i: x[i]) + [len(A) - 1]
    up = []
    for i in order:                                  # the upper hull from p to q (monotone chain)
        while len(up) >= 2:
            a, b = up[-2], up[-1]
            if (x[b] - x[a]) * (y[i] - y[a]) - (y[b] - y[a]) * (x[i] - x[a]) >= 0:
                up.pop()
            else:
                break
        up.append(i)
    P = A[up]
    return perimeter(P, False), chord, P


def tape_along(body, pts, normals):
    """A tape along the body's surface through the points in order: (length, straight, polyline)."""
    L, poly = 0.0, [np.asarray(pts[0], float)[None]]
    for a, b, na, nb in zip(pts[:-1], pts[1:], normals[:-1], normals[1:]):
        l, A = tape_segment(body, a, b, na, nb)
        L += l
        poly.append(A[1:])
    P = np.vstack(poly)
    straight = perimeter(np.asarray(pts, float), False)
    return L, straight, P


# ---------------------------------------------------------------- the flat pattern

def pattern_distance(oa, sa, ob, sb):
    """Between two points on the outlines (core/spec.Outline, outline parameter s): (straight, along) mm;
    along is None unless both are on the same outline (the shorter way round it)."""
    pa, pb = oa.at(sa), ob.at(sb)
    straight = float(np.linalg.norm(pb - pa))
    if oa is not ob:
        return straight, None
    d = abs(float(sb) - float(sa)) % oa.L
    return straight, float(min(d, oa.L - d))
