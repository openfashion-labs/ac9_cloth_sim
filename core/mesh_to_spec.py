# SPDX-License-Identifier: GPL-3.0-or-later
"""From Mesh: a garment modeled in 3D with UV seams -> a spec (no bpy).

The modeller's UV islands are the pattern pieces (user 2026-10-09, DECISIONS cloth-sim-mesh-to-pattern-2026-10-09):
- an island's UVs, scaled to mm by the least-squares ratio of its 3D edge lengths to its UV edge lengths (each island
  its own scale: UV packing scales islands as it likes), are the piece's flat layout;
- two runs of island boundary that share 3D edges are a seam (an island sewn to itself: a sleeve's one seam);
- the outline is cut at corners -- where the seam changes, a seam meets a free edge, a sharp turn -- and every run
  between corners becomes an edge, a cubic Bezier fitted with core/draft.fit_bezier (split when it does not fit).

convert() also returns what Continue starts from (design review 2026-10-09, model3d/DESIGN_mesh_to_pattern §8): the
modeller's own triangles with their flat layout and 3D positions, written as a job by mesh_import.py.

How far each island is stretched by its unwrap is reported (2D / 3D edge length, the same yardstick as a placement:
DECISIONS cloth-sim-placement-no-stretch-rule, <= 1.05); it does not stop the conversion.
"""
import numpy as np

from . import draft as D
from . import spec as S

STRETCH_OK = 1.05          # 2D / 3D edge length (DECISIONS cloth-sim-placement-no-stretch-rule-2026-10-08)
CORNER_DEG = 60.0          # a turn of the outline sharper than this is a corner (a low-poly 8-gon turns 45)
STRAIGHT_MM = 0.2         # a run whose points keep this close to its chord is a straight edge
FIT_TOL_MM = 1.0           # a Bezier edge may leave the modeller's outline points by this much, else it is split
MIN_CORNERS = 3
LAYOUT_GAP = 50.0          # mm between pieces laid out in a row
UV_KEY = 1e-6              # UVs closer than this are one point


class MeshError(ValueError):
    pass


def _islands(T, UV):
    """Weld the corners into flat points (vertex, UV) and group the triangles into UV islands.
    T (n, 3) vertex ids, UV (n, 3, 2). Returns (flat point -> vertex id, flat point UVs, triangles in flat
    points, island of every triangle)."""
    key = {}
    fvid, fuv = [], []
    TF = np.empty(T.shape, np.int64)
    for t in range(len(T)):
        for c in range(3):
            k = (int(T[t, c]), int(round(UV[t, c, 0] / UV_KEY)), int(round(UV[t, c, 1] / UV_KEY)))
            f = key.get(k)
            if f is None:
                f = key[k] = len(fvid)
                fvid.append(int(T[t, c]))
                fuv.append(UV[t, c])
            TF[t, c] = f
    parent = list(range(len(T)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    owner = {}
    for t in range(len(T)):
        for c in range(3):
            e = tuple(sorted((int(TF[t, c]), int(TF[t, (c + 1) % 3]))))
            o = owner.get(e)
            if o is None:
                owner[e] = t
            else:
                ra, rb = find(o), find(t)
                if ra != rb:
                    parent[ra] = rb
    roots = {}
    isl = np.array([roots.setdefault(find(t), len(roots)) for t in range(len(T))], np.int64)
    return np.array(fvid, np.int64), np.array(fuv, float), TF, isl


def _loop(tris):
    """The island's outline as one closed loop of flat points, in the triangles' winding. Raises on a hole or a
    non-manifold boundary."""
    cnt = {}
    for a, b, c in tris:
        for u, v in ((a, b), (b, c), (c, a)):
            cnt[(u, v)] = cnt.get((u, v), 0) + 1
    nxt = {}
    for (u, v), n in cnt.items():
        if (v, u) not in cnt:
            if u in nxt:
                raise MeshError("the outline touches itself (a non-manifold point)")
            nxt[u] = v
    if not nxt:
        raise MeshError("a closed island (no outline): cut it with a UV seam")
    start = min(nxt)
    L = [start]
    while True:
        v = nxt[L[-1]]
        if v == start:
            break
        L.append(v)
        if len(L) > len(nxt):
            raise MeshError("the outline does not close")
    if len(L) != len(nxt):
        raise MeshError("an island with a hole: cut the hole open with a UV seam to the outline")
    return L


def _area(P):
    return 0.5 * float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))


def convert(name, V3, T, UV, guides=None):
    """V3 (n, 3) world positions mm, T (m, 3) triangles (vertex ids), UV (m, 3, 2) the triangles' corner UVs.
    Returns (spec, job, report lines). job: {'names', 'flat', 'F', 'fp', 'X', 'offs', 'V', 'nb'} -- flat and X per
    flat vertex (each island's outline first, in order), F in those, the pattern as pattern.npz holds it (V, nb)."""
    V3 = np.asarray(V3, float)
    T = np.asarray(T, np.int64)
    UV = np.asarray(UV, float)
    if len(T) == 0:
        raise MeshError("no faces")
    fvid, fuv, TF, isl = _islands(T, UV)
    rep = [f"{name}: {len(V3)} vertices, {len(T)} triangles, {isl.max() + 1} UV islands"]

    # ---- per island: its flat points (outline first), mm scale, outline loop
    P = []
    for k in range(isl.max() + 1):
        tris = TF[isl == k]
        loop = _loop(tris)
        used = list(dict.fromkeys(loop + [int(f) for f in tris.ravel()]))
        loc = {f: i for i, f in enumerate(used)}
        uv = fuv[used]
        x3 = V3[fvid[used]]
        lt = np.vectorize(loc.get)(tris)
        e = np.unique(np.sort(np.concatenate([lt[:, [0, 1]], lt[:, [1, 2]], lt[:, [2, 0]]]), axis=1), axis=0)
        l2 = np.linalg.norm(uv[e[:, 0]] - uv[e[:, 1]], axis=1)
        l3 = np.linalg.norm(x3[e[:, 0]] - x3[e[:, 1]], axis=1)
        if float(np.dot(l2, l2)) < 1e-18:
            raise MeshError(f"island {k + 1} has no UV area")
        s = float(np.dot(l2, l3) / np.dot(l2, l2))
        flat = uv * s
        tri_a = np.cross(flat[lt[:, 1]] - flat[lt[:, 0]], flat[lt[:, 2]] - flat[lt[:, 0]])
        if tri_a.sum() < 0:                       # the island is mirrored in UV: turn it over so it winds CCW
            flat[:, 0] *= -1
            tri_a = -tri_a
        flipped = int((tri_a <= 0).sum())
        r = l2 * s / np.maximum(l3, 1e-9)
        P.append({'tris': lt, 'flat': flat, 'X': x3, 'scale': s, 'vid': fvid[used], 'nb': len(loop), 'r': r,
                  'flipped': flipped})

    # ---- seams: the outline edges of every island by their 3D edge
    by3d = {}
    for k, p in enumerate(P):
        n = p['nb']
        for i in range(n):
            a, b = int(p['vid'][i]), int(p['vid'][(i + 1) % n])
            if a == b:
                raise MeshError(f"island {k + 1}: a zero-length outline edge (merge by distance)")
            by3d.setdefault(tuple(sorted((a, b))), []).append((k, i))
    for e, lst in by3d.items():
        if len(lst) > 2:
            raise MeshError("a 3D edge on more than two UV outlines (non-manifold)")
    partner = {}
    for lst in by3d.values():
        if len(lst) == 2:
            partner[lst[0]] = lst[1]
            partner[lst[1]] = lst[0]

    # ---- corners, by 3D vertex so both sides of a seam cut at the same points
    cv = set()
    for k, p in enumerate(P):
        n = p['nb']
        B = p['flat'][:n]
        own = []
        for i in range(n):
            pa, pb = partner.get((k, (i - 1) % n)), partner.get((k, i))
            corner = (pa is None) != (pb is None)
            if pa is not None and pb is not None:
                same = pa[0] == pb[0] and (pa[1] - pb[1]) % P[pa[0]]['nb'] in (1, P[pa[0]]['nb'] - 1)
                folds_back = pa == (k, i) or pb == (k, (i - 1) % n)
                corner = corner or not same or folds_back
            d0, d1 = B[i] - B[i - 1], B[(i + 1) % n] - B[i]
            c = float(np.dot(d0, d1) / max(np.linalg.norm(d0) * np.linalg.norm(d1), 1e-12))
            if np.degrees(np.arccos(np.clip(c, -1, 1))) > CORNER_DEG:
                corner = True
            if corner:
                own.append(i)
        if len(own) < MIN_CORNERS:                # a free circle (a cuff with no seam on it): cut it evenly
            cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(np.vstack([B, B[:1]]), axis=0), axis=1))]
            base = own[0] if own else 0
            for q in range(MIN_CORNERS):
                target = (cum[base] + cum[-1] * q / MIN_CORNERS) % cum[-1]
                own.append(int(np.argmin(np.abs(cum[:-1] - target))))
        cv.update(int(p['vid'][i]) for i in own)

    # ---- pieces: runs between corners -> edges (Bezier where it fits)
    spec = S.new_spec(name, {'kind': 'mesh', 'object': name})
    names = []
    runs = {}                                     # (island, first outline edge index) -> (piece, edge ids, n edges)
    x0 = 0.0
    for k, p in enumerate(P):
        n = p['nb']
        cidx = sorted(i for i in range(n) if int(p['vid'][i]) in cv)
        # lay the islands in a row (the flat layout, the job's flat and the spec are the same coordinates)
        lo, hi = p['flat'].min(0), p['flat'].max(0)
        p['flat'] = p['flat'] - lo + np.array([x0, 0.0])
        x0 += float(hi[0] - lo[0]) + LAYOUT_GAP
        B = p['flat'][:n]
        pname = f"{name}_{k + 1}"
        names.append(pname)
        edges = []
        for j, c0 in enumerate(cidx):
            c1 = cidx[(j + 1) % len(cidx)]
            idx = [(c0 + t) % n for t in range(((c1 - c0) % n) or n)] + [c1]
            ids = []
            for part in _fit(B[idx]):
                eid = f"e{len(edges) + 1}"
                edges.append(dict(part, id=eid))
                ids.append(eid)
            runs[(k, c0)] = (pname, ids, (c1 - c0) % n or n)
        pc = {'name': pname, 'fabric': '', 'edges': edges, 'internals': [], 'enabled': True, 'role': 'auto'}
        spec['pieces'].append(pc)
        r = p['r']
        bad = float(np.mean((r > STRETCH_OK) | (r < 1 / STRETCH_OK)))
        rep.append(f"  {pname}: {len(B)} outline points -> {len(edges)} edges, {len(cidx)} corners; "
                   f"UV x {p['scale']:.1f} mm; "
                   f"stretch 2D/3D p50 {np.median(r):.3f} min {r.min():.3f} max {r.max():.3f}, "
                   f"{100 * bad:.1f} % of edges beyond {STRETCH_OK}" + (f"; {p['flipped']} triangles flipped in UV"
                                                                        if p['flipped'] else ""))
        p['name'] = pname

    # ---- seams between runs
    done = set()
    for (k, c0), (pname, ids, ne) in runs.items():
        pt = partner.get((k, c0))
        if pt is None or (k, c0) in done:
            continue
        kb, ib = pt
        nbb = P[kb]['nb']
        # the partner run: the one whose edges are the partners of this run's edges
        mine = [(k, (c0 + t) % P[k]['nb']) for t in range(ne)]
        theirs = {partner.get(m) for m in mine}
        if None in theirs:
            raise MeshError(f"{pname}: a seam that ends inside a free edge (corners out of step)")
        rb = next(((kk, cc) for (kk, cc), v in runs.items() if kk == kb
                   and {(kb, (cc + t) % nbb) for t in range(v[2])} == theirs), None)
        if rb is None:
            raise MeshError(f"{pname}: the two sides of a seam do not cut at the same corners")
        done.update({(k, c0), rb})
        bname, bids, bne = runs[rb]
        # paired from the 'from' ends: this run's start vertex is the partner run's end vertex when the two
        # outlines run opposite ways along the seam (the usual case), its start when an island is turned over
        a_start = int(P[k]['vid'][c0])
        b_start = int(P[kb]['vid'][rb[1]])
        side_a = {'piece': pname, 'from': [ids[0], 0.0], 'to': [ids[-1], 1.0], 'dir': 1}
        if b_start == a_start:
            side_b = {'piece': bname, 'from': [bids[0], 0.0], 'to': [bids[-1], 1.0], 'dir': 1}
        else:
            side_b = {'piece': bname, 'from': [bids[-1], 1.0], 'to': [bids[0], 0.0], 'dir': -1}
        sid = f"m{len(spec['seams']) + 1}"
        spec['seams'].append({'id': sid, 'a': side_a, 'b': side_b, 'source': 'mesh', 'check': '', 'enabled': True})

    # seam lengths after the curve fit (both sides are paired point for point: a big difference pulls)
    OL = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    worst = 0.0
    for sm in spec['seams']:
        la, lb = (abs(OL[sm[s]['piece']].span(sm[s])) for s in ('a', 'b'))
        d = abs(la - lb) / max(la, lb, 1e-9)
        worst = max(worst, d)
        if d > 0.02:
            sm['check'] = f"sides differ {100 * d:.1f} % ({la:.0f} / {lb:.0f} mm)"
    rep.append(f"  seams: {len(spec['seams'])}, largest length difference {100 * worst:.1f} %")

    if guides:
        spec['placement'] = place_on_guides(spec, P, guides)
        rep.append("  guides: " + ", ".join(f"{n} {v['guide']}" for n, v in spec['placement'].items()))

    offs, F, fp, flat, X, Vs, nbs = [], [], [], [], [], {}, []
    o = 0
    for k, p in enumerate(P):
        offs.append(o)
        F.append(p['tris'] + o)
        fp.append(np.full(len(p['tris']), k))
        flat.append(p['flat'])
        X.append(p['X'])
        Vs[f"V{k}"] = p['flat']
        nbs.append(p['nb'])
        o += len(p['flat'])
    job = {'names': names, 'offs': np.array(offs), 'F': np.vstack(F), 'fp': np.concatenate(fp),
           'flat': np.vstack(flat), 'X': np.vstack(X), 'V': Vs, 'nb': np.array(nbs)}
    return spec, job, rep


def _fit(Q, depth=0):
    """Edges for one run of outline points: a straight edge for two points, a Bezier that keeps within FIT_TOL_MM
    of the points, or the run split in two at its middle point and fitted again."""
    Q = np.asarray(Q, float)
    if len(Q) <= 2:
        return [{'pts': D._r(Q)}]
    c = Q[-1] - Q[0]
    off = np.abs(np.cross(c, Q - Q[0])) / max(float(np.linalg.norm(c)), 1e-9)
    if float(off.max()) <= STRAIGHT_MM:           # a straight run: an edge without handles
        return [{'pts': D._r(Q[[0, -1]])}]
    ctrl, err = D.fit_bezier(Q)
    if err <= FIT_TOL_MM or depth >= 5:
        e = {'pts': D._r(Q[[0, -1]]), 'ctrl': ctrl}
        D.refresh_curve(e)
        return [e]
    m = len(Q) // 2
    return _fit(Q[:m + 1], depth + 1) + _fit(Q[m:], depth + 1)


def place_on_guides(spec, P, guides):
    """A placement for every piece (dress.prepare wants one even when Continue starts from the mesh, and Dress from
    the start uses it): the guide the piece wraps round most evenly -- its points' distance from the guide's axis
    varies least, and they stay within the guide's length -- at the angle and height of the piece's center.
    (Nearest axis to the center put a skirt's front panel on a leg, 2026-10-09.) Sleeves land on the arm guides,
    which Continue needs to keep the arms in the body (recipe.continue_stages)."""
    out = {}
    for pc, p in zip(spec['pieces'], P):
        X = p['X']
        best = None
        for gname, g in guides.items():
            R = np.asarray(g['R'], float)
            lo = (X - np.asarray(g['o'], float)) @ R.T
            rad = np.hypot(lo[:, 0], lo[:, 1])
            over = np.maximum(0.0, np.maximum(g['h0'] - lo[:, 2], lo[:, 2] - g['h1']))
            score = float(np.std(rad) + np.mean(over))
            if best is None or score < best[0]:
                c = lo.mean(0)
                best = (score, gname, float(np.degrees(np.arctan2(c[1], c[0]))), float(c[2]))
        B = S.Outline(pc).B
        out[pc['name']] = {'guide': best[1], 'theta': round(best[2], 3), 'h': round(best[3], 3),
                           'anchor': np.round(0.5 * (B.min(0) + B.max(0)), 3).tolist()}
    return out
