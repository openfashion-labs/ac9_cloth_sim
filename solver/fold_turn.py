"""Folding a sewn piece along its fold line without simulating the fold (TEST, cfg 'fold_turn', 2026-10-07).

Folding a pattern by hand (pick an internal line, turn the far side with a gizmo; the angle it is
turned to becomes the line's fold angle). Simulated, by deepening the fold line's target angle (cfg fold_ramp), a
jacket collar sewn standing folded only 14-70 of 137.5 degrees (RESULT_collar_steps_2026-10-07). Here the far side is
put where the fold takes it, so the fold line starts at its set angle with nothing left to push (as the collar
placed folded at its set angle, A1). Modes:
  turn  each far-side vertex is turned round the bit of the fold line nearest to it (the line's 3D tangent there)
  cyl   prefold's way (dress.py): on the cylinder round the neck, developed flat, the far side is mirrored over the
        line and carried out round a 2 mm bend
  carry as turn, but carried out over N frames with the far side held (no mass), the rest simulating
numpy only (runs in the solver's environment). X in metres here."""
import numpy as np


def far_side(F, piece_verts, roll, seam, flat=None):
    """Vertices of a piece cut off from its seam by the fold line: a flood over the piece's triangles from the seam
    vertices that does not cross the line's vertices. F (m, 3) triangles (local ids), piece_verts / roll / seam local
    ids. A line that stops short of the outline (a jacket collar's roll line, ~30 mm short at both ends) does not cut:
    with flat given, each vertex then goes by the side of the line nearest to it in the flat pattern (prefold's way),
    the far side being the one without the seam. Returns the far side (without the line) or None."""
    far = _flood_far(F, piece_verts, roll, seam)
    if far is not None or flat is None:
        return far
    roll = np.asarray(roll)[_order(flat[roll])]
    r2 = flat[roll].astype(np.float64)
    tang = np.gradient(r2, axis=0)
    tang /= np.linalg.norm(tang, axis=1, keepdims=True) + 1e-18
    nrm = np.c_[-tang[:, 1], tang[:, 0]]

    def side(u):
        k = np.argmin(np.linalg.norm(r2 - flat[u], axis=1))
        return float((flat[u] - r2[k]) @ nrm[k])
    rs = set(int(v) for v in roll)
    seam_side = np.sign(np.mean([side(u) for u in seam]))
    far = np.array([u for u in piece_verts if int(u) not in rs and np.sign(side(u)) != seam_side], np.int64)
    return far if len(far) else None


def _flood_far(F, piece_verts, roll, seam):
    pv = set(int(v) for v in piece_verts)
    rs = set(int(v) for v in roll)
    nb = {v: set() for v in pv}
    for t in F:
        if all(int(v) in pv for v in t):
            for a in t:
                for b in t:
                    if a != b:
                        nb[int(a)].add(int(b))
    seen = set(int(v) for v in seam if int(v) not in rs)
    todo = list(seen)
    while todo:
        v = todo.pop()
        for w in nb[v]:
            if w not in seen and w not in rs:
                seen.add(w); todo.append(w)
    far = np.array(sorted(pv - seen - rs), np.int64)
    if not len(far) or len(far) > 0.8 * len(pv):
        return None
    return far


def _order(flat_roll):
    c = flat_roll - flat_roll.mean(0)
    pc = np.linalg.svd(c, full_matrices=False)[2][0]
    return np.argsort(c @ pc)


def turn(X, flat, roll, far, phi):
    """Each far vertex turned by phi (rad) round the 3D fold line at the point of the line nearest to it in the flat
    pattern (the line's tangent there as the axis). Returns new X (copy)."""
    X = np.asarray(X, np.float64).copy()
    roll = np.asarray(roll)[_order(flat[roll])]
    R2, R3 = flat[roll].astype(np.float64), X[roll]
    T3 = np.gradient(R3, axis=0)
    T3 /= np.linalg.norm(T3, axis=1, keepdims=True) + 1e-18
    a2, b2 = R2[:-1], R2[1:]
    ab = b2 - a2
    for v in far:
        q = flat[v].astype(np.float64)
        t = np.clip(((q - a2) * ab).sum(1) / ((ab * ab).sum(1) + 1e-18), 0.0, 1.0)
        dd = np.linalg.norm(a2 + t[:, None] * ab - q, axis=1)
        j = int(np.argmin(dd)); s = t[j]
        p = R3[j] * (1 - s) + R3[j + 1] * s
        k = T3[j] * (1 - s) + T3[j + 1] * s
        k /= np.linalg.norm(k) + 1e-18
        r = X[v] - p
        X[v] = p + r * np.cos(phi) + np.cross(k, r) * np.sin(phi) + k * (k @ r) * (1 - np.cos(phi))
    return X


def cyl(X, flat, roll, seam, far, axis_xy, angle_deg, gap=0.004, inward=False, from_flat=True, mm=0.001):
    """dress.py prefold() (2026-10-07) on a shape in metres, the far side given. from_flat: each far vertex's distance
    from the line and its foot on the line come from the flat pattern (mm * mm -> m), not from its developed position
    -- a collar sewn standing already leans out ~53 deg, and its developed height is foreshortened (edges squashed to
    0.38 of their length when the developed position was used). Returns new X (copy)."""
    X = np.asarray(X, np.float64).copy()
    ax = np.asarray(axis_xy, float)
    roll = np.asarray(roll)[_order(flat[roll])]
    r2 = flat[roll].astype(np.float64)
    th = lambda P: np.arctan2(P[:, 1] - ax[1], P[:, 0] - ax[0])
    Pr = X[roll]
    t0 = np.arctan2(np.sin(th(Pr)).mean(), np.cos(th(Pr)).mean())
    Rref = np.hypot(Pr[:, 0] - ax[0], Pr[:, 1] - ax[1]).mean()
    dev = lambda P: np.c_[Rref * ((th(P) - t0 + np.pi) % (2 * np.pi) - np.pi), P[:, 2]]
    Ld = dev(Pr)
    if from_flat and len(Ld) >= 5:
        # the sewn line is crumpled (a collar's roll line zigzags a few mm): its developed curve smoothed over five
        # vertices, or the far side's foot and normal swing from one segment to the next
        Ld = np.array([Ld[max(0, k - 2):k + 3].mean(0) for k in range(len(Ld))])
    sa, sb = Ld[:-1], Ld[1:]
    P = X[far]
    Q = dev(P)
    rad = np.hypot(P[:, 0] - ax[0], P[:, 1] - ax[1])
    rho = gap / 2.0
    th_ = np.radians(angle_deg)
    Qn, nd = np.empty_like(Q), np.empty(len(Q))
    r2f = flat[roll].astype(np.float64)
    Rl = np.hypot(Pr[:, 0] - ax[0], Pr[:, 1] - ax[1])
    rad = rad.copy()
    a2f, abf = r2f[:-1], r2f[1:] - r2f[:-1]
    for k, q in enumerate(Q):
        ab = sb - sa
        if from_flat:
            qf = flat[far[k]].astype(np.float64)
            tf = np.clip(((qf - a2f) * abf).sum(1) / ((abf * abf).sum(1) + 1e-18), 0.0, 1.0)
            df = np.linalg.norm(a2f + tf[:, None] * abf - qf, axis=1)
            j = int(np.argmin(df))
            foot_ = sa[j] + tf[j] * ab[j]
            tau = ab[j] / np.linalg.norm(ab[j])
            nu = np.array([-tau[1], tau[0]])
            if nu[1] < 0:                               # away from the seam: up the developed neck
                nu = -nu
            n = df[j] * mm
            phi = min(n / rho, th_)
            rest_ = max(n - th_ * rho, 0.0)
            Qn[k] = foot_ + nu * (rho * np.sin(phi) + rest_ * np.cos(th_))
            nd[k] = rho * (1 - np.cos(phi)) + rest_ * np.sin(th_)
            rad[k] = Rl[j] * (1 - tf[j]) + Rl[j + 1] * tf[j]    # from the line's radius, not the vertex's own
            continue
        tt = ((q - sa) * ab).sum(1) / (ab * ab).sum(1)
        tt[1:] = np.maximum(tt[1:], 0)
        tt[:-1] = np.minimum(tt[:-1], 1)
        foot = sa + tt[:, None] * ab
        j = np.argmin(np.linalg.norm(q - foot, axis=1))
        tau = ab[j] / np.linalg.norm(ab[j])
        nu = np.array([-tau[1], tau[0]])
        n = (q - foot[j]) @ nu
        if n < 0:
            nu, n = -nu, -n
        phi = min(n / rho, th_)
        rest_ = max(n - th_ * rho, 0.0)
        Qn[k] = foot[j] + nu * (rho * np.sin(phi) + rest_ * np.cos(th_))
        nd[k] = rho * (1 - np.cos(phi)) + rest_ * np.sin(th_)
    tn = t0 + Qn[:, 0] / Rref
    rn = rad - nd if inward else rad + nd
    X[far, 0] = ax[0] + rn * np.cos(tn)
    X[far, 1] = ax[1] + rn * np.sin(tn)
    X[far, 2] = Qn[:, 1]
    return X


def band_angle_fn(F, flat, piece_verts, roll, far, band_mm=12.0):
    """A measure of how far the line is folded that does not depend on the hinges right on the line (a collar sewn
    standing came out crumpled along its roll line: the line's own hinges -120..+109 deg at the same time): along the
    line, the mean normal of the piece's triangles within band_mm of it on either side (flat pattern), the angle
    between the two (0 = flat, 180 = folded flat back), median along the line. Returns measure(X) -> deg."""
    pv = set(int(v) for v in piece_verts)
    fs = set(int(v) for v in far)
    T = np.array([t for t in np.asarray(F) if all(int(v) in pv for v in t)], np.int64)
    is_far = np.array([any(int(v) in fs for v in t) for t in T])
    C = flat[T].mean(1)
    roll = np.asarray(roll)[_order(flat[roll])]
    R2 = flat[roll].astype(np.float64)
    groups = []
    for k in range(0, len(R2) - 1, 2):
        m = 0.5 * (R2[k] + R2[min(k + 2, len(R2) - 1)])
        dd = np.linalg.norm(C - m, axis=1)
        g = []
        for side in (False, True):
            ix = np.nonzero((is_far == side) & (dd < band_mm))[0]
            if not len(ix):
                ix = np.nonzero(is_far == side)[0][np.argsort(dd[is_far == side])[:3]]
            g.append(ix)
        groups.append(g)

    def measure(X):
        N = np.cross(X[T[:, 1]] - X[T[:, 0]], X[T[:, 2]] - X[T[:, 0]])
        N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-18
        out = []
        for a, b in groups:
            na, nb = N[a].sum(0), N[b].sum(0)
            na /= np.linalg.norm(na) + 1e-18; nb /= np.linalg.norm(nb) + 1e-18
            out.append(np.degrees(np.arccos(np.clip(na @ nb, -1, 1))))
        return float(np.median(out))
    return measure


def fit_grid(apply, measure, target_deg, prefer, lo=-180.0, hi=180.0):
    """The turn (deg, signed) whose measure(X) comes nearest target_deg (unsigned, 0 flat .. 180 folded back): a 5
    degree grid, then 1 degree round the best; among turns within 3 degrees of the target, the one prefer(X) rates
    highest (the far side out, away from the neck). apply(deg) -> X. Returns (deg, X, angle_deg)."""
    def scan(ps):
        r = []
        for p in ps:
            Xp = apply(p)
            r.append((p, Xp, measure(Xp)))
        return r
    res = scan(np.arange(lo, hi + 0.1, 5.0))
    ok = [x for x in res if abs(x[2] - target_deg) < 3.0]
    best = max(ok, key=lambda x: prefer(x[1])) if ok else min(res, key=lambda x: abs(x[2] - target_deg))
    res2 = scan(np.arange(best[0] - 4.0, best[0] + 4.1, 1.0))
    s = np.sign(best[0]) or 1.0
    res2 = [x for x in res2 if np.sign(x[0]) in (s, 0.0)] or [best]
    return min(res2 + [best], key=lambda x: abs(x[2] - target_deg))


def fit(apply, measure, target, start):
    """The turn (rad) that brings the fold line's angle (measure(X), rad) to target: both senses tried from the
    simple difference, then two secant steps. apply(phi) -> X. Returns (phi, X, angle)."""
    d0 = target - start
    best = None
    for p in (d0, -d0):
        Xp = apply(p); a = measure(Xp)
        if best is None or abs(a - target) < abs(best[2] - target):
            best = (p, Xp, a)
    p1, _, a1 = best
    p0, a0 = 0.0, start
    for _ in range(2):
        if abs(a1 - a0) < 1e-6:
            break
        p2 = p1 + (target - a1) * (p1 - p0) / (a1 - a0)
        X2 = apply(p2); a2 = measure(X2)
        if abs(a2 - target) < abs(best[2] - target):
            best = (p2, X2, a2)
        p0, a0, p1, a1 = p1, a1, p2, a2
    return best


def dir_angle_fn(X0, flat, roll, far, near_pool, near_mm=25.0, far_mm=(8.0, 30.0)):
    """How far the line is folded, from directions instead of normals (a sewn collar's roll line is crumpled): at
    each line vertex, the direction to the far side (far vertices 8-30 mm from it in the flat pattern) and to the near
    side (near_pool vertices -- the rest of the piece and the piece it is sewn to -- within near_mm of it in 3D at the
    start), both across the line; folded = 180 - the angle between them (0 = flat, 180 = folded back). Median along
    the line. Returns measure(X) -> deg."""
    roll = np.asarray(roll)[_order(flat[roll])]
    far = np.asarray(far); near_pool = np.asarray(near_pool)
    G = []
    for k, r in enumerate(roll):
        df = np.linalg.norm(flat[far] - flat[r], axis=1) * 1.0
        gf = far[(df > far_mm[0]) & (df < far_mm[1])]
        dn = np.linalg.norm(X0[near_pool] - X0[r], axis=1) * 1000.0
        gn = near_pool[dn < near_mm]
        if len(gf) and len(gn):
            G.append((k, gf, gn))

    def measure(X):
        R3 = X[roll]
        T3 = np.gradient(R3, axis=0)
        T3 /= np.linalg.norm(T3, axis=1, keepdims=True) + 1e-18
        out = []
        for k, gf, gn in G:
            t = T3[k]
            uf = (X[gf] - R3[k]).mean(0); uf -= t * (t @ uf)
            un = (X[gn] - R3[k]).mean(0); un -= t * (t @ un)
            c = uf @ un / (np.linalg.norm(uf) * np.linalg.norm(un) + 1e-18)
            out.append(180.0 - np.degrees(np.arccos(np.clip(c, -1, 1))))
        return float(np.median(out)) if out else 0.0
    return measure


def radial_pref(far, axis_xy):
    """prefer(X): the far side's mean distance from the neck axis (a collar folds out over its stand, not in)"""
    ax = np.asarray(axis_xy, float)
    return lambda X: float(np.hypot(X[far, 0] - ax[0], X[far, 1] - ax[1]).mean())


def up_angle_fn(flat, roll, far, far_mm=(8.0, 30.0)):
    """How far a collar is folded, against the world's up (+z) instead of its crumpled near side: at each line vertex,
    the direction to the far side (far vertices 8-30 mm from it in the flat pattern) and +z, both across the line;
    the angle between them (0 = standing up, 137.5 = turned out and down as the collar placed folded at its set
    angle, A1 -- the same reference prefold folds from). Median along the line. Returns measure(X) -> deg."""
    roll = np.asarray(roll)[_order(flat[roll])]
    far = np.asarray(far)
    G = []
    for k, r in enumerate(roll):
        df = np.linalg.norm(flat[far] - flat[r], axis=1)
        gf = far[(df > far_mm[0]) & (df < far_mm[1])]
        if len(gf):
            G.append((k, gf))
    up = np.array([0.0, 0.0, 1.0])

    def measure(X):
        R3 = X[roll]
        T3 = np.gradient(R3, axis=0)
        T3 /= np.linalg.norm(T3, axis=1, keepdims=True) + 1e-18
        out = []
        for k, gf in G:
            t = T3[k]
            uf = (X[gf] - R3[k]).mean(0); uf -= t * (t @ uf)
            uu = up - t * (t @ up)
            c = uf @ uu / (np.linalg.norm(uf) * np.linalg.norm(uu) + 1e-18)
            out.append(np.degrees(np.arccos(np.clip(c, -1, 1))))
        return float(np.median(out)) if out else 0.0
    return measure


# ---- drive (Fable r1, foundation_2026-09-30/REVIEW_fable_nonphysical_fold_r1_2026-10-07.md): the far side turned a
# little each frame round the fold line where it is NOW (smoothed over five vertices), and stopped where it would
# come within stop_mm of the held pieces or the body. carry turned it round the stage's start shape, round the raw
# zigzag line, 6 mm a frame, with nothing stopping it, so it went through the bodice (user 2026-10-07)

def smooth_line(R3, k=5):
    """moving average over k vertices of a polyline (n, 3)"""
    h = k // 2
    return np.array([R3[max(0, i - h):i + h + 1].mean(0) for i in range(len(R3))])


def drive_setup(flat, roll, far):
    """roll ordered along the line; for each far vertex its segment j and parameter t on the line in the flat
    pattern (fixed for the drive: which bit of the line it turns round)"""
    roll = np.asarray(roll)[_order(flat[roll])]
    R2 = flat[roll].astype(np.float64)
    a2, ab = R2[:-1], R2[1:] - R2[:-1]
    J = np.zeros(len(far), np.int64); Tt = np.zeros(len(far))
    for i, v in enumerate(far):
        q = flat[v].astype(np.float64)
        t = np.clip(((q - a2) * ab).sum(1) / ((ab * ab).sum(1) + 1e-18), 0.0, 1.0)
        dd = np.linalg.norm(a2 + t[:, None] * ab - q, axis=1)
        J[i] = int(np.argmin(dd)); Tt[i] = t[J[i]]
    return roll, J, Tt


def turn_step(q, roll, far, J, Tt, dphi):
    """q (n, 3): the far vertices turned by dphi (rad) round the smoothed current line. Returns their new positions
    (len(far), 3) and the turn's pivot distance per vertex (for the step size)."""
    R = smooth_line(np.asarray(q[roll], np.float64))
    T = np.gradient(R, axis=0)
    T = smooth_line(T)
    T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-18
    s = Tt[:, None]
    p = R[J] * (1 - s) + R[J + 1] * s
    k = T[J] * (1 - s) + T[J + 1] * s
    k /= np.linalg.norm(k, axis=1, keepdims=True) + 1e-18
    r = np.asarray(q[far], np.float64) - p
    kr = (k * r).sum(1, keepdims=True)
    new = p + r * np.cos(dphi) + np.cross(k, r) * np.sin(dphi) + k * kr * (1 - np.cos(dphi))
    rad = np.linalg.norm(r - k * kr, axis=1)
    return new, rad
