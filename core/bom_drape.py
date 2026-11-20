"""The garment's 3D drape (an FBX the BOM's software exported with it) tells which pieces a BOM seam sews.

A BOM seam names its two sides by a shape number and a line number. A project assembled from several files reuses
those numbers: on a measured jacket 129 of 280 seam sides named lines that other, different pieces carried too, so the
BOM alone left the partner open (the best rules found 105 of 140 seams). On the drape the two sides of a real seam lie
on top of each other: all 140 were found there, the two sides within 0.81 mm.

How: the drape's UV is the 2D layout of the pattern (one scale and one shift for every piece: 21 pieces within
0.1 mm). Each piece is found on the mesh by its layout position, every candidate pairing of a seam (core/bom_to_spec)
is mapped to 3D, and the pairings whose two sides meet are kept. Pure numpy: the caller reads the meshes (drape_fbx.py
in Blender) and passes arrays.

Result, stored in spec['source']['drape']: {'path', 'pairs': {gid: [[piece idx, piece idx], ...]}, 'pieces',
'seams', 'mm_per_uv'}. A gid with [] was not found on the drape (its partners stay the BOM's rules')."""
import collections

import numpy as np

from . import bom_parse as B
from . import spec as S

GAP_MM = 1.5          # a pairing is real when its two sides lie this close on the drape (median over the side;
                      # the jacket's real ones: at most 1.0 mm, a wrong pairing that met at 2.7 mm was let in at 3)
N_SIDE = 9            # points compared along a seam side
PIECE_HIT = 0.9       # a piece is on the drape when this share of its inner points land on one mesh piece
MIN_PIECES = 2        # the FBX holds the pattern when at least this many pieces are on it (an FBX of one garment
                      # of a BOM with several: only that garment's pieces are)


# ---------------------------------------------------------------- the mesh as a UV locator

class _Locator:
    """Triangles of one mesh in layout mm (UV scaled and shifted), binned on a grid; maps a layout point to 3D."""

    def __init__(self, co_mm, tri, uv_mm, cell=20.0):
        self.co, self.tri, self.uv = co_mm, tri, uv_mm
        self.cell = cell
        lo = uv_mm.min(axis=1)
        hi = uv_mm.max(axis=1)
        self.o = lo.min(axis=0) - cell
        a = np.floor((lo - self.o) / cell).astype(int)
        b = np.floor((hi - self.o) / cell).astype(int)
        bins = collections.defaultdict(list)
        for t in range(len(tri)):
            for i in range(a[t, 0], b[t, 0] + 1):
                for j in range(a[t, 1], b[t, 1] + 1):
                    bins[(i, j)].append(t)
        self.bins = {k: np.asarray(v) for k, v in bins.items()}

    def near(self, q, allowed=None, reach=1):
        i, j = np.floor((np.asarray(q) - self.o) / self.cell).astype(int)
        ts = [self.bins[(i + di, j + dj)] for di in range(-reach, reach + 1) for dj in range(-reach, reach + 1)
              if (i + di, j + dj) in self.bins]
        if not ts:
            return np.zeros(0, int)
        t = np.unique(np.concatenate(ts))
        return t if allowed is None else t[allowed[t]]

    def bary(self, q, t):
        """Barycentric coordinates of q in triangles t (n, 3) and how far outside each it is (0 inside)."""
        A, Bv, C = self.uv[t, 0], self.uv[t, 1], self.uv[t, 2]
        v0, v1, v2 = Bv - A, C - A, q - A
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        den = d00 * d11 - d01 * d01
        den = np.where(np.abs(den) < 1e-18, 1e-18, den)
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        L = np.stack([1 - v - w, v, w], 1)
        return L, np.clip(-L, 0, None).sum(1)

    def inside(self, q):
        """Triangles containing the layout point q."""
        t = self.near(q, reach=0)
        if not len(t):
            return t
        _, out = self.bary(q, t)
        return t[out < 1e-9]

    def to3d(self, q, allowed):
        """The 3D point (mm) at layout point q on the triangles `allowed` (bool mask): the triangle q is in, else the
        nearest (q on a piece's outline may fall a hair outside); None when nothing is near."""
        for reach in (1, 3):
            t = self.near(q, allowed, reach)
            if len(t):
                break
        else:
            return None
        L, out = self.bary(q, t)
        k = int(out.argmin())
        if out[k] > 0.5:                                  # far outside every triangle near
            return None
        lam = np.clip(L[k], 0, None)
        lam = lam / lam.sum()
        return lam @ self.co[self.tri[t[k]]]


def _components(tri, n_vert):
    """Connected pieces of a mesh (triangles sharing a vertex): a label per triangle."""
    par = np.arange(n_vert)

    def find(x):
        r = x
        while par[r] != r:
            r = par[r]
        while par[x] != r:
            par[x], x = r, par[x]
        return r
    for a, b, c in tri:
        ra, rb, rc = find(a), find(b), find(c)
        if ra != rb:
            par[rb] = ra
        if ra != rc:
            par[find(rc)] = ra
    roots = np.array([find(v) for v in range(n_vert)])
    return roots[tri[:, 0]]


# ---------------------------------------------------------------- pieces on the mesh

def _inner_points(ring, n=48, d=1.5):
    """Points just inside a piece's outline (d mm in from it, both normals tried: whichever side is inside). Pulling
    the outline toward its center lost every point of a curved facing strip, whose center lies outside it."""
    P = S.resample(np.vstack([ring, ring[:1]]), n + 1)
    m = 0.5 * (P[:-1] + P[1:])
    t = P[1:] - P[:-1]
    nrm = np.stack([-t[:, 1], t[:, 0]], 1) / np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
    pts = np.vstack([m + d * nrm, m - d * nrm])
    return pts[S.in_holes(pts, [ring])]


def _fits(uv, comp, rings, tol=0.004):
    """Candidate (scale, shift) with uv * scale + shift = layout mm, best first: a mesh piece (connected triangles)
    whose UV box has a pattern piece's box shape votes for the scale between them, then the shift (other meshes and
    parts, like stitches or trims, sit elsewhere on the UV and do not agree)."""
    labs, inv = np.unique(comp, return_inverse=True)
    flat = uv.reshape(-1, 2)
    lo = np.full((len(labs), 2), np.inf)
    hi = np.full((len(labs), 2), -np.inf)
    idx = np.repeat(inv, 3)
    np.minimum.at(lo, idx, flat)
    np.maximum.at(hi, idx, flat)
    usz = hi - lo
    big = (usz > 1e-6).all(1)
    plo = np.array([r.min(0) for r in rings.values()])
    psz = np.array([np.ptp(r, axis=0) for r in rings.values()])
    votes = []
    for k in np.flatnonzero(big):
        r = psz / usz[k]                                    # (pieces, 2)
        ok = np.abs(np.log(r[:, 0] / r[:, 1])) < tol
        for p in np.flatnonzero(ok):
            s = float(r[p].mean())
            votes.append((s, plo[p] - lo[k] * s))
    if not votes:
        return []
    S_ = np.array([v[0] for v in votes])
    out = []
    for s in S_[np.argsort(-np.array([(np.abs(np.log(S_ / x)) < tol).sum() for x in S_]))][:3]:
        T_ = np.array([v[1] for v in votes if abs(np.log(v[0] / s)) < tol])
        n = [(np.linalg.norm(T_ - t, axis=1) < 2.0).sum() for t in T_]
        out.append((float(s), T_[int(np.argmax(n))]))
    return out


def _place(mesh, rings):
    """Find each piece on one mesh. Returns (locator, {piece idx: triangle mask}, hit share, scale) or None."""
    co, tri, uv = mesh['co'], mesh['tri'], mesh['uv']
    if len(tri) == 0:
        return None
    best = None
    comp = _components(tri, len(co))
    for s, t in _fits(uv, comp, rings):
        loc = _Locator(co * 1000.0, tri, uv * s + t)
        masks = {}
        for i, ring in rings.items():
            pts = _inner_points(ring)
            votes = collections.Counter()
            for q in pts:
                ts = loc.inside(q)
                if len(ts):
                    votes.update(set(comp[ts].tolist()))
            if votes:
                c, n = votes.most_common(1)[0]
                if n >= PIECE_HIT * len(pts):
                    masks[i] = comp == c
        if best is None or len(masks) > len(best[1]):
            best = (loc, masks, len(masks), s)
    return best


# ---------------------------------------------------------------- seams

def _ref(sm):
    return tuple((s['ref_pattern_gid'], s['line_start'], s['line_end'], round(s['r0'], 5), round(s['r1'], 5))
                 for s in sm['sides'])


def resolve(spec, meshes, path=''):
    """The drape's answer for every BOM seam of a BOM spec. meshes: [{'co' (V, 3) m, 'tri' (T, 3) vertex ids,
    'uv' (T, 3, 2)}]. Returns (drape dict for spec['source']['drape'], report lines); drape is None when no mesh is
    the garment (its UV does not hold the pattern)."""
    from . import bom_to_spec as B2S
    bom = B.load_bom(spec['source']['path'])
    P = bom['pieces']
    label = B2S._pieces(bom)
    by_idx = {pc['bom']['idx']: pc for pc in spec['pieces'] if 'bom' in pc}
    outlines = {pc['name']: S.Outline(pc) for pc in by_idx.values()}
    rings = {i: outlines[pc['name']].B for i, pc in by_idx.items()}
    placed = None
    for m in meshes:
        r = _place(m, rings)
        if r is not None and (placed is None or r[2] > placed[2]):
            placed = r
    if placed is None or placed[2] < MIN_PIECES:
        n = 0 if placed is None else placed[2]
        return None, [f"the FBX does not hold this pattern ({n} of the pieces found on its UV)", f"{n}"]
    loc, masks, _, scale = placed
    outl = {}
    for i in by_idx:
        edges, flip, fwd = B2S._piece_outline(P[i])
        _, rmaps = B2S._internals(P[i])
        outl[i] = (edges, flip, fwd, rmaps)

    cache = {}

    def side3d(i, x):
        key = (i, S.dumps(x))
        if key not in cache:
            pts = S.resample(S.side_polyline(spec, x, outlines, step=1.0), N_SIDE)
            q3 = [loc.to3d(q, masks[i]) for q in pts] if i in masks else [None]
            cache[key] = None if any(p is None for p in q3) else np.array(q3)
        return cache[key]

    def gap(i, xa, j, xb):
        a, b = side3d(i, xa), side3d(j, xb)
        if a is None or b is None:
            return None
        return float(min(np.median(np.linalg.norm(a - b, axis=1)), np.median(np.linalg.norm(a - b[::-1], axis=1))))

    # every BOM seam is one pairing (measured: 140 of 140 on the jacket), and one pairing on the drape is one BOM seam:
    # seams with the same references (one per copy of a piece) and a copy's dart that also meets on the other copy
    # would otherwise take the same pairing twice. So: every seam's pairings that meet, then the closest first, each
    # seam and each pairing (pieces and sides) used once.
    opts = []
    unknown = set()           # seams with a candidate piece the FBX does not hold: no answer, not "not found"
    for sm in bom['seams']:
        sa, sb = sm['sides']
        if any(i in by_idx and i not in masks for s_ in (sa, sb) for i in B.owners(bom, s_)):
            unknown.add(sm['gid'])
        for pa in B.owners(bom, sa):
            if pa not in by_idx:
                continue
            xa = B2S._side(label[pa], sa, outl[pa])
            if xa is None:
                continue
            for pb in B.owners(bom, sb):
                if pb not in by_idx:
                    continue
                xb = B2S._side(label[pb], sb, outl[pb])
                if xb is None or (pa == pb and xa == xb):
                    continue
                try:
                    g = gap(pa, xa, pb, xb)
                except (KeyError, ValueError, IndexError):
                    g = None
                if g is not None and g < GAP_MM:
                    key = tuple(sorted([(int(pa), S.dumps(xa)), (int(pb), S.dumps(xb))]))
                    opts.append((g, sm['gid'], [int(pa), int(pb)], key))
    # a matching of seams to pairings, as many seams as can be (closest first was not enough: a sleeve's dart lost
    # its seam to a back's dart that had another), each seam trying its closest pairings first
    by_gid = collections.defaultdict(list)
    for g, gid, pair, key in sorted(opts, key=lambda o: (o[0], o[1])):
        by_gid[gid].append((g, pair, key))
    owner = {}                                         # pairing key -> gid

    def take(gid, seen):
        for g, pair, key in by_gid[gid]:
            if key in seen:
                continue
            seen.add(key)
            if key not in owner or take(owner[key], seen):
                owner[key] = gid
                return True
        return False
    for gid in sorted(by_gid, key=lambda k: (by_gid[k][0][0], k)):
        take(gid, set())
    pairs = {str(sm['gid']): [] for sm in bom['seams'] if sm['gid'] not in unknown}
    gaps = []
    for key, gid in owner.items():
        g, pair, _ = next(o for o in by_gid[gid] if o[2] == key)
        pairs[str(gid)] = [pair]
        gaps.append(g)
    found = sum(bool(v) for v in pairs.values())
    gaps = np.asarray(gaps) if gaps else np.zeros(1)
    drape = {'path': path, 'pairs': pairs, 'pieces': len(masks), 'on': sorted(int(i) for i in masks), 'seams': found,
             'mm_per_uv': round(scale, 3)}
    rep = [f"3D drape: {len(masks)} / {len(by_idx)} pieces, {found} / {len(bom['seams'])} BOM seams found "
           f"(sides apart: median {float(np.median(gaps)):.2f} mm, max {float(gaps.max()):.2f} mm)"]
    return drape, rep
