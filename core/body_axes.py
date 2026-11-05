# SPDX-License-Identifier: GPL-3.0-or-later
"""Placement guides from the avatar's body mesh (no bpy, numpy only).

A guide is a finite cylinder a piece is laid round: torso, neck, the two upper arms, the two legs.
Everything is found from exact horizontal sections of the body (plane x triangle edges, split into
closed loops) and is relative to the body (torso length = neck base - crotch), so A-pose and T-pose
avatars of any size go through the same code. Body coordinates are mm, z up, the avatar facing -y.

Guide frame: origin o, axis ez (for the torso / neck / legs: world up; for an arm: up the arm, towards
the shoulder), ey (the top of the arm; world -y = the front for the vertical ones), ex = ey x ez.
A point is given by (theta, h): h along ez from o, theta counter-clockwise about ez from ex. A flat
piece is laid with its +y along ez and its +x counter-clockwise (the viewer's right, seen from outside).
"""
import numpy as np


class Body:
    def __init__(self, co_mm, tri):
        self.B = np.asarray(co_mm, float)
        self.T = np.asarray(tri, np.int64)
        E = np.sort(np.r_[self.T[:, [0, 1]], self.T[:, [1, 2]], self.T[:, [2, 0]]], axis=1)
        self.Eu, e_inv = np.unique(E, axis=0, return_inverse=True)
        self.e_of_t = e_inv.reshape(3, -1).T
        self.z0, self.z1 = float(self.B[:, 2].min()), float(self.B[:, 2].max())
        self._loops = {}            # z -> the loops (dress keeps one Body while the body stays: asked again per press)

    def loops(self, z):
        """Closed section loops at height z, each an (n, 2) point array (unordered)."""
        z = float(z)
        if z not in self._loops:
            if len(self._loops) > 500:      # ~3.6 KB each, one prepare asks ~60
                self._loops.clear()
            self._loops[z] = self._loops_at(z)
        return [P.copy() for P in self._loops[z]]

    def _loops_at(self, z):
        B, Eu = self.B, self.Eu
        za, zb = B[Eu[:, 0], 2] - z, B[Eu[:, 1], 2] - z
        cut = (za * zb) < 0
        if not cut.any():
            return []
        ids = -np.ones(len(Eu), np.int64)
        ids[cut] = np.arange(cut.sum())
        t = za[cut] / (za[cut] - zb[cut])
        P = B[Eu[cut, 0]] + t[:, None] * (B[Eu[cut, 1]] - B[Eu[cut, 0]])
        tc = ids[self.e_of_t]
        tc = tc[(tc >= 0).sum(1) == 2]
        pr = np.sort(tc, axis=1)[:, 1:]                     # the two cut edges of each cut triangle
        parent = np.arange(len(P))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for a, b in pr:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        lab = np.array([find(i) for i in range(len(P))])
        out = []
        for l in np.unique(lab):
            m = lab == l
            if m.sum() >= 5:
                out.append(P[m][:, :2])
        return out

    def torso(self, z):
        c = [P for P in self.loops(z) if P[:, 0].min() < 0 < P[:, 0].max()]
        return max(c, key=lambda P: np.ptp(P[:, 0])) if c else None


def _ring_frame(ez):
    ez = np.asarray(ez, float) / np.linalg.norm(ez)
    ref = np.array([0.0, -1.0, 0.0]) if abs(ez[2]) > 0.9 else np.array([0.0, 0.0, 1.0])
    ey = ref - (ref @ ez) * ez
    ey /= np.linalg.norm(ey)
    ex = np.cross(ey, ez)
    return np.stack([ex, ey, ez])


def measure(body):
    """Levels (mm) and upper-arm axes, as in the experiment's measure_body.py."""
    B, z0, z1 = body.B, body.z0, body.z1
    zs = np.arange(z0 + 50, z1, 5.0)
    z_crotch = float(max(z for z in zs if z < z0 + 0.6 * (z1 - z0) and body.torso(z) is None))
    rows = []
    for z in np.arange(z_crotch + 5, z1 - 2, 5.0):
        P = body.torso(z)
        if P is None:
            continue
        c = 0.5 * (P.min(0) + P.max(0))
        rows.append((float(z), float(np.ptp(P[:, 0])), float(np.ptp(P[:, 1])), float(c[0]), float(c[1]),
                     float(np.percentile(np.linalg.norm(P - c, axis=1), 95))))
    R = np.array(rows)
    wid = R[:, 1]
    i_hipmax = int(np.argmax(np.where(R[:, 0] < z_crotch + 0.3 * (z1 - z_crotch), wid, 0)))
    jump = [i for i in range(i_hipmax + 3, len(R)) if wid[i] > 1.25 * wid[i - 1] and wid[i] - wid[i - 1] > 15]
    z_armpit = float(R[jump[0] - 1, 0])
    w_arm = float(R[R[:, 0] <= z_armpit][-1, 1])
    neck_r = R[(R[:, 0] > z_armpit) & (R[:, 1] < 0.4 * w_arm)]
    z_neck = float(neck_r[0, 0])
    TL = z_neck - z_crotch
    half_arm = 0.5 * w_arm

    def arm_axis(sgn):
        zr, hw = R[:, 0], 0.5 * R[:, 1]
        hwz = np.where(B[:, 2] > z_armpit, half_arm, np.interp(B[:, 2], zr, hw))
        A_ = B[(sgn * B[:, 0] > hwz + 0.04 * TL) & (B[:, 2] > z_armpit - 0.6 * TL)]
        m_ = A_.mean(0)
        u = np.linalg.svd(A_ - m_, full_matrices=False)[2][0]
        if u[0] * sgn < 0:
            u = -u
        t = (A_ - m_) @ u
        U = A_[t < t.min() + 0.45 * np.ptp(t)]
        mu = U.mean(0)
        w = np.linalg.svd(U - mu, full_matrices=False)[2][0]
        ez = -w if w[0] * sgn > 0 else w                   # up the arm, towards the shoulder
        Rm = _ring_frame(ez)
        L = (U - mu) @ Rm.T
        A2 = np.c_[L[:, :2], np.ones(len(L))]
        D_, E_, _ = np.linalg.lstsq(A2, -(L[:, :2] ** 2).sum(1), rcond=None)[0]
        o = mu + (-D_ / 2) * Rm[0] + (-E_ / 2) * Rm[1]
        r = np.linalg.norm(((U - o) @ Rm.T)[:, :2], axis=1)
        hh = (U - o) @ Rm[2]
        # the whole arm's line (shoulder to hand, the first principal direction of all its points): a long sleeve
        # reaches the wrist, and round the upper arm's axis the forearm of an avatar with its arms out at a flatter
        # angle lay 74 mm off it (2026-10-09) -- the sleeve was made 32 mm thicker to clear it
        return {'o': o, 'R': Rm, 'r_p90': float(np.percentile(r, 90)), 'h0': float(hh.min()), 'h1': float(hh.max()),
                'len_arm': float(np.ptp(t)), 'angle_from_vertical_deg': float(np.degrees(np.arccos(abs(ez[2])))),
                'o_full': m_, 'ez_full': -u}
    return {'z_min': z0, 'z_top': z1, 'z_crotch': z_crotch, 'z_armpit': z_armpit, 'z_neck': z_neck,
            'torso_len': TL, 'torso_half_width_armpit': half_arm, 'rows': R,
            'arm_L': arm_axis(+1), 'arm_R': arm_axis(-1)}


def guides(body, margin=15.0):
    """The six placement guides: {name: {'o', 'R' (rows ex, ey, ez), 'r', 'h0', 'h1'}} in mm."""
    m = measure(body)
    R, TL = m['rows'], m['torso_len']
    zc, za, zn = m['z_crotch'], m['z_armpit'], m['z_neck']
    out = {}
    tor = R[(R[:, 0] > zc) & (R[:, 0] < za)]
    c = np.median(tor[:, 3:5], axis=0)
    out['torso'] = {'o': np.array([c[0], c[1], zc]), 'R': _ring_frame([0, 0, 1]),
                    'r': float(tor[:, 5].max() + margin), 'h0': -0.25 * TL, 'h1': zn - zc}
    nk = R[(R[:, 0] > zn) & (R[:, 0] < zn + 0.15 * TL)]
    if len(nk):
        cn = np.median(nk[:, 3:5], axis=0)
        out['neck'] = {'o': np.array([cn[0], cn[1], zn]), 'R': _ring_frame([0, 0, 1]),
                       'r': float(nk[:, 5].max() + margin), 'h0': -0.05 * TL, 'h1': 0.2 * TL}
    for side, key in (('arm_L', 'arm_L'), ('arm_R', 'arm_R')):
        a = m[key]
        out[side] = {'o': a['o'], 'R': a['R'], 'r': a['r_p90'] + margin,
                     'h0': a['h1'] - a['len_arm'], 'h1': a['h1'],
                     'full': {'o': a['o_full'], 'R': _ring_frame(a['ez_full'])}}
    for side, sgn in (('leg_L', 1), ('leg_R', -1)):
        cs, rs = [], []
        for z in np.arange(m['z_min'] + 0.1 * TL, zc - 0.02 * TL, 0.05 * TL):
            L = [P for P in body.loops(z) if sgn * P[:, 0].mean() > 0]
            if not L:
                continue
            P = max(L, key=len)
            cc = 0.5 * (P.min(0) + P.max(0))
            cs.append(cc)
            rs.append(np.percentile(np.linalg.norm(P - cc, axis=1), 95))
        if cs:
            cc = np.median(np.array(cs), axis=0)
            out[side] = {'o': np.array([cc[0], cc[1], m['z_min']]), 'R': _ring_frame([0, 0, 1]),
                         'r': float(max(rs) + margin), 'h0': 0.0, 'h1': zc - m['z_min']}
    for k, g in out.items():
        g['points'] = _snap_points(k, g, m)
    return out, m


_DIRS = {'front': (0, -1, 0), 'back': (0, 1, 0), 'left': (1, 0, 0), 'right': (-1, 0, 0),
         'top': (0, 0, 1), 'under': (0, 0, -1)}


def _theta_of(g, d):
    Rm = np.asarray(g['R'], float)
    d = np.asarray(d, float)
    return float(np.degrees(np.arctan2(d @ Rm[1], d @ Rm[0])))


def torso_levels(m):
    """Heights (mm, world z) of the bust, the waist and the hip, found from the torso's sections. The placement
    points and the body measurements (core/measure.py) both use these, so the two never disagree."""
    R, TL, zc = m['rows'], m['torso_len'], m['z_crotch']
    area = R[:, 1] * R[:, 2]
    wr = (R[:, 0] > zc + 0.15 * TL) & (R[:, 0] < m['z_armpit'] - 0.1 * TL)
    z_waist = float(R[wr][np.argmin(area[wr]), 0]) if wr.any() else zc + 0.4 * TL
    hr = (R[:, 0] > zc + 0.05 * TL) & (R[:, 0] < z_waist)
    # hip: the deepest level (the seat). The width only narrows from the crotch up (the thighs' tops are
    # in the section), measured on three avatars; the depth peaks at 0.15-0.21 of the torso length
    z_hip = float(R[hr][np.argmax(R[hr][:, 2]), 0]) if hr.any() else zc + 0.15 * TL
    br = (R[:, 0] > z_waist + 0.05 * TL) & (R[:, 0] <= m['z_armpit'] - 0.05 * TL)   # not the shoulders
    z_bust = float(R[br][np.argmax(area[br]), 0]) if br.any() else m['z_armpit'] - 0.1 * TL
    return {'chest': z_bust, 'waist': z_waist, 'hip': z_hip}


def _snap_points(name, g, m):
    """Named spots a piece snaps to (arrangement points): [{'name', 'theta', 'h'}].
    Levels come from the body (torso: bust / waist / hip found from the sections; limbs: fractions of the guide)."""
    TL, zc = m['torso_len'], m['z_crotch']
    pts = []

    def add(label, dirs, h):
        for dn in dirs:
            pts.append({'name': f"{label} {dn}", 'theta': round(_theta_of(g, _DIRS[dn]), 3), 'h': round(float(h), 3)})
    if name == 'torso':
        for label, z in torso_levels(m).items():
            add(label, ('front', 'back', 'left', 'right'), z - zc)
    elif name == 'neck':
        add('neck', ('front', 'back'), 0.05 * TL)
    elif name.startswith('arm'):
        L = g['h1'] - g['h0']
        side = ('left',) if name.endswith('L') else ('right',)
        for label, h in (('upper arm', g['h1'] - 0.2 * L), ('elbow', g['h1'] - 0.5 * L), ('wrist', g['h0'] + 0.08 * L)):
            add(label, ('top', 'front', 'back', 'under'), h)
    elif name.startswith('leg'):
        H = g['h1']
        outer, inner = ('left', 'right') if name.endswith('L') else ('right', 'left')
        for label, h in (('thigh', 0.8 * H), ('knee', 0.55 * H), ('ankle', 0.08 * H)):
            for dn, lab2 in (('front', 'front'), ('back', 'back'), (outer, 'outer'), (inner, 'inner')):
                pts.append({'name': f"{label} {lab2}", 'theta': round(_theta_of(g, _DIRS[dn]), 3), 'h': round(float(h), 3)})
    return pts


def to_json(gd):
    return {k: {'o': np.round(v['o'], 3).tolist(), 'R': np.round(v['R'], 6).tolist(), 'r': round(v['r'], 3),
                'h0': round(float(v['h0']), 3), 'h1': round(float(v['h1']), 3), 'points': v.get('points', []),
                **({'full': {'o': np.round(v['full']['o'], 3).tolist(), 'R': np.round(v['full']['R'], 6).tolist()}}
                   if 'full' in v else {})}
            for k, v in gd.items()}


def point(g, theta_deg, h, r=None):
    """World (mm) point on guide g at (theta, h), radius r (default the guide's)."""
    Rm = np.asarray(g['R'], float)
    th = np.radians(theta_deg)
    rr = g['r'] if r is None else r
    return np.asarray(g['o'], float) + rr * (np.cos(th) * Rm[0] + np.sin(th) * Rm[1]) + h * Rm[2]


def tilted(Q, r, tilt_deg):
    """(radius, height) per layout point Q (relative to the anchor) of a piece tilted tilt_deg about the line round
    the guide through its anchor (Tilt, user 2026-11-01: a collar's outer edge, low in the pattern, laid next to the
    neck; 90 lays it flat out, 180 turns it upside down). Its rows keep their length round, so the part tilted out
    is stretched round (the user's choice, 2026-11-01). 0: (r, the height) as before."""
    if not tilt_deg:
        return r, Q[:, 1]
    a = np.radians(float(tilt_deg))
    return r + np.sin(a) * Q[:, 1], np.cos(a) * Q[:, 1]


def wrap(g, P_layout, anchor_layout, theta_deg, h, r=None, tilt=0.0):
    """A flat piece laid round guide g at radius r (default the guide's): layout +x -> counter-clockwise about ez,
    +y -> along ez. anchor_layout (layout mm) lands at (theta, h); the piece tilted tilt degrees about it (tilted)."""
    rr = g['r'] if r is None else r
    P = np.asarray(P_layout, float) - np.asarray(anchor_layout, float)
    th = np.radians(theta_deg) + P[:, 0] / rr
    Rm = np.asarray(g['R'], float)
    rad, up = tilted(P, rr, tilt)
    rad = np.broadcast_to(rad, th.shape)
    return (np.asarray(g['o'], float)[None] + rad[:, None] * (np.cos(th)[:, None] * Rm[0] + np.sin(th)[:, None] * Rm[1])
            + (h + up)[:, None] * Rm[2])


MAX_TURN = 0.95     # a piece never goes more than this far round (it would overlap itself; dress.py's too)
WRAP_GAP = 10.0     # mm left between the pieces round one arm guide when they are laid side by side
def wrap_radii(guides, pieces, panels=()):
    """The radius each placed piece is wrapped at: the guide's, or larger when the panels round one arm are wider
    than it goes round -- all of them side by side once round, WRAP_GAP apart, whichever order they were placed in
    -- and never more than MAX_TURN round for one piece (user 2026-10-15: a sleeve wider than the arm guide was
    drawn round it and over itself, and the pattern could no longer be read from the 3D). One cylinder per guide,
    so the pieces are still only bent, never stretched (2026-10-08). pieces {name: (guide, width, height)} mm ->
    {name: r}. Only the panels round an arm: bands (a cuff) and narrow pieces (a placket) lie on them; the torso
    and neck have their own ring and band rules (dress.py); on the legs pockets and facings lie on the panels and
    counted as side by side they made the two legs start apart (a trouser leg 108.7 / 116.5 mm, 2026-10-15).
    panels: the pieces that go side by side -- those given a sleeve role (core/roles.ARM_PANEL_ROLES); which pieces
    they are is no longer told from their width and height (user 2026-11-02, DECISIONS
    cloth-sim-no-auto-judgment-default-rules-2026-11-02)."""
    total = {}
    for n, (gname, w, hgt) in pieces.items():
        if gname in guides and gname.startswith('arm') and n in panels:
            total[gname] = total.get(gname, 0.0) + w + WRAP_GAP
    out = {}
    for n, (gname, w, hgt) in pieces.items():
        if gname not in guides:
            continue
        r = float(guides[gname]['r'])
        if gname in total and n in panels:
            r = max(r, total[gname] / (2 * np.pi))
        out[n] = max(r, w / (2 * np.pi * MAX_TURN))
    return out


def ray_hit(gd, o, d):
    """Nearest hit of a ray (mm) with the guides' cylinder walls: (name, theta_deg, h, t) or None."""
    best = None
    d = np.asarray(d, float) / np.linalg.norm(d)
    for name, g in gd.items():
        Rm = np.asarray(g['R'], float)
        lo = Rm @ (np.asarray(o, float) - np.asarray(g['o'], float))
        ld = Rm @ d
        a = ld[0] ** 2 + ld[1] ** 2
        if a < 1e-12:
            continue
        b = 2 * (lo[0] * ld[0] + lo[1] * ld[1])
        c = lo[0] ** 2 + lo[1] ** 2 - g['r'] ** 2
        disc = b * b - 4 * a * c
        if disc < 0:
            continue
        for t in sorted(((-b - np.sqrt(disc)) / (2 * a), (-b + np.sqrt(disc)) / (2 * a))):
            if t <= 0:
                continue
            p = lo + t * ld
            if g['h0'] <= p[2] <= g['h1']:
                if best is None or t < best[3]:
                    best = (name, float(np.degrees(np.arctan2(p[1], p[0]))), float(p[2]), float(t))
                break
    return best
