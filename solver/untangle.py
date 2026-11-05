# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloth through cloth, taken back out (cfg 'untangle', test option, default off).

Once a vertex has gone through another layer the self contact keeps it there: the solver's penetration-free step
only limits the motion from where each collision pass found the cloth, so it defends whatever order it finds
(a fold tucked into the back of a jacket stayed for 300 frames, 2026-10-01). It has no memory, though: cloth moved
across the other layer between two frames is taken as it is from then on. So, at the end of a frame:

  1. triangle-triangle intersections from Newton's own GPU detector (pairs sharing a vertex are left out there),
  2. each pair's depth = how far the straddling triangle's shallower side is through the other one's plane; pairs
     no deeper than min_depth (the self contact's margin + gap) are left alone -- two stitched vertices 0.3 mm apart
     overlap their neighbours by that much wherever a seam folds, and the contact cannot resolve it (Fable review,
     REVIEW_fable_untangle_2026-10-01 1-1),
  3. the crossing triangles of one surface, joined where they share a vertex = one intersection line; on that
     surface the line's vertices split by the side of the other surface they are on, and each side grows over the
     surface without stepping onto the other side's vertices, up to max_region vertices. The side that stops first is
     the part that went through (closed off by the line and the piece's own outline -- open cloth needs no closed
     loop). Both surfaces of an intersection are tried, the smaller region wins (Baraff, Witkin, Kass 2003),
  4. the region moves across the other surface (its signed distance + gap along the other surface's normal),
     its velocity across that normal set to 0. If the moved region now crosses something deeper than before, the
     move is taken back and counted.
Vertices without mass (grabbed, frozen, bound) never move.
"""
import collections

import numpy as np


class Untangler:
    def __init__(self, solver, F, tri_piece, n_cloth, cfg, flat=None, fold=None, body=None, stitched=None):
        self.det = solver.trimesh_collision_detector
        # room for the pairs per triangle; the arrays are made on the first call (tri_mesh_collision.py L1002)
        self.det.triangle_triangle_collision_buffer_pre_alloc = int(cfg.get('pairs_per_tri', 32))
        self.F = np.asarray(F, np.int64)
        self.tp = np.asarray(tri_piece, np.int64)
        self.n = int(n_cloth)
        self.min_depth = float(cfg.get('min_depth_mm', 0.45)) / 1000.0
        self.gap = float(cfg.get('gap_mm', 0.45)) / 1000.0
        self.max_region = int(cfg.get('max_region', 60))
        self.detect_only = bool(cfg.get('detect_only', False))
        self.rings = int(cfg.get('local_rings', 4))          # how far from the line the local count looks
        self.reach = float(cfg.get('local_reach_mm', 6.0)) / 1000.0   # ... and how far from the other surface
        self.local_ratio = float(cfg.get('local_ratio', 0.5))  # the smaller side at most this x the larger
        self.vp = np.zeros(self.n, np.int64)
        self.vp[self.F.ravel()] = np.repeat(self.tp, 3)
        # pieces the moving cloth may go through (newton_vbd ghost_pieces): their pairs are dropped
        self.skip_t = np.isin(self.tp, cfg.get('skip_pieces') or [])
        # mode "retreat" (the user's proposal, 2026-10-02, like AC9 Cloth Retopo's Guide Separate): no side of a
        # whole surface is decided; each crossing pair backs off, both triangles at once, each to the side of the
        # other's plane where most of its own vertices are, by half the depth of its deepest wrong-side vertex
        # plus gap; spread to the one-ring at spread x, the moves of all pairs averaged per vertex
        self.mode = cfg.get('mode', 'region')
        self.spread = float(cfg.get('spread', 0.5))
        self.cap = float(cfg.get('cap_mm', 1.0)) / 1000.0           # at most this per vertex per frame
        # as Guide Separate's test: a pair counts only when the two triangles are far apart on the pattern too
        # (the same piece; different pieces always count). A pressed fold's crease crosses itself by its own
        # neighbours, near on the pattern, and backing those off undid a jacket's lapel (2026-10-02)
        self.flat = None if flat is None else np.asarray(flat, np.float64) / 1000.0
        self.flat_far = float(cfg.get('flat_far_mm', 15.0)) / 1000.0
        # only crossings that stay: a pair seen in `persist` checks in a row (the solver resolves most crossings
        # it makes within a few frames by itself; backing those off too broke a jacket's vent, 2026-10-02)
        self.persist = int(cfg.get('persist', 1))
        self.vage = np.zeros(self.n, np.int64)
        # side "back" = the side each triangle came from: its side of the other's plane one check before the pair
        # first crossed, kept while the pair stays crossed (side_from "history"); "majority" = where most of its
        # own vertices are now. Two layers lying flat on each other (a turned lapel on its front) have no
        # majority side, and the jacket's lapel was pushed through the front by it (2026-10-02)
        self.side_from = cfg.get('side_from', 'history')
        self.par_cos = float(np.cos(np.radians(float(cfg.get('parallel_deg', 30.0)))))
        self.Xprev = None
        self.side_mem = {}
        # side_from "contact": which side of another layer each vertex was on when it first came within mem_mm of
        # it without crossing it -- outside or inside as seen from the body -- kept per (vertex, other piece), so it
        # survives a crossing sliding over neighbouring triangles. The cloth contact keeps the order two layers
        # first touched in, and pushing them into an order afterwards broke things (a back vent, RESULT_G3 26-31;
        # a lapel, 2026-10-03): the back-off returns a crossing to that first order; with no memory, the majority
        self.mem_r = float(cfg.get('mem_mm', 3.0)) / 1000.0
        # no memory next to a seam: two sewn pieces meet edge to edge there and either side is the same seam
        # (recorded there, the memory pushed whole seams the wrong way: 650-2800 deep pairs, 2026-10-03)
        self.no_mem = None                                  # built on the first memory pass (needs the one-rings)
        self._stitched = None if stitched is None else np.asarray(stitched).ravel()
        self._mem_rings = int(cfg.get('mem_seam_rings', 2))
        # Fable r3: leave the seams' neighbourhood alone (crossings there are made and resolved by the solver
        # itself; pushing them back every frame turned a jacket's front edge inwards) and two layers of one piece
        # lying on each other (their majority side is a coin toss)
        self.seam_rings = int(cfg.get('seam_rings', 0))
        self.same_parallel_skip = bool(cfg.get('same_parallel_skip', False))
        self.near_seam = None
        self.vmem = {}
        self.mem_every = int(cfg.get('mem_every', 5))     # the memory pass is CPU work: every few checks
        self.body_kd = None
        if body is not None:
            from scipy.spatial import cKDTree
            self.body = np.asarray(body, np.float64)
            self.body_kd = cKDTree(self.body)   # checks in a row each vertex has been in a deep crossing
        # (counted per vertex, not per triangle pair: a crossing slides over neighbouring triangles from frame to
        # frame, so the pair's own age kept restarting and the trousers kept 10-12 deep pairs, 2026-10-02)
        # a designed overlap is left alone: two triangles of one piece facing each other across one of its fold
        # lines (a lapel turned onto its front). They are folded into place before the run and the contact keeps
        # that order; the back-off picked a side by chance between two parallel layers and pushed the lapel
        # through its front (2026-10-02). Fold lines = the pattern's internal fold lines (`fold`, per vertex id)
        self.fold_segs = {}
        if fold is not None and self.flat is not None:
            fold = np.asarray(fold, np.int64)
            for k in np.unique(fold[fold >= 0]):
                vs = np.nonzero(fold == k)[0]
                if len(vs) < 2:
                    continue
                P = self.flat[vs]
                ax = np.linalg.svd(P - P.mean(0), full_matrices=False)[2][0]
                P = P[np.argsort(P @ ax)]
                self.fold_segs.setdefault(int(self.vp[vs[0]]), []).append(P)
        self.mirror_tol = float(cfg.get('mirror_tol_mm', 15.0)) / 1000.0
        self.cross_piece_only = bool(cfg.get('cross_piece_only', False))   # test: leave a piece's own crossings alone
        self.dump = cfg.get('dump')                    # diagnosis: the first call's pairs and positions (.npz)
        nb = [[] for _ in range(self.n)]
        for i, j, k in self.F:
            nb[i] += (j, k); nb[j] += (i, k); nb[k] += (i, j)
        self.nb = [np.unique(x) for x in nb]
        vt = [[] for _ in range(self.n)]
        for t, (i, j, k) in enumerate(self.F):
            vt[i].append(t); vt[j].append(t); vt[k].append(t)
        self.vt = vt
        self.log = {'frames': [], 'moved': 0, 'skipped_both_big': 0, 'reverted': 0, 'shallow_last': 0, 'deep_last': 0}

    # ---- detection
    def pairs(self, q_wp):
        det = self.det
        det.refit(q_wp)
        det.triangle_triangle_intersection_detection()
        cnt = det.triangle_intersecting_triangles_count.numpy()
        if det.resize_flags.numpy()[3]:
            self.log['overflow'] = True
        off = det.triangle_intersecting_triangles_offsets.numpy()
        lst = det.triangle_intersecting_triangles.numpy()
        ts = np.nonzero(cnt)[0]
        if not len(ts):
            return np.zeros((0, 2), np.int64)
        a = np.repeat(ts, np.minimum(cnt[ts], off[ts + 1] - off[ts]))
        b = np.concatenate([lst[off[t]:off[t] + min(cnt[t], off[t + 1] - off[t])] for t in ts])
        p = np.unique(np.stack([np.minimum(a, b), np.maximum(a, b)], 1), axis=0)
        if self.skip_t.any():
            p = p[~(self.skip_t[p[:, 0]] | self.skip_t[p[:, 1]])]
        # the detector's pairs are a superset: on a pants' final shape 2388 pairs, of which 170 cross by an exact
        # edge-through-triangle test (the same 170 as the CPU check), the rest near misses 4-6 mm apart (2026-10-01)
        X = q_wp.numpy()[:self.n].astype(np.float64)
        return p[self._exact(X, p)]

    def _exact(self, X, P):
        hit = np.zeros(len(P), bool)
        for s, t in ((P[:, 0], P[:, 1]), (P[:, 1], P[:, 0])):
            A, B, C = X[self.F[t, 0]], X[self.F[t, 1]], X[self.F[t, 2]]
            n = np.cross(B - A, C - A)
            v0, v1 = B - A, C - A
            d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
            den = d00 * d11 - d01 * d01 + 1e-30
            for i, j in ((0, 1), (1, 2), (2, 0)):
                P0, P1 = X[self.F[s, i]], X[self.F[s, j]]
                h0 = ((P0 - A) * n).sum(1); h1 = ((P1 - A) * n).sum(1)
                w = h0 / (h0 - h1 + 1e-30)
                v2 = P0 + w[:, None] * (P1 - P0) - A
                d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
                b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den
                hit |= (h0 * h1 < 0) & (b1 >= 0) & (b2 >= 0) & (b1 + b2 <= 1)
        return hit

    def _plane(self, X, t):
        A, B, C = X[self.F[t, 0]], X[self.F[t, 1]], X[self.F[t, 2]]
        n = np.cross(B - A, C - A); n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-18
        return A, n

    def depths(self, X, P):
        """per pair: the shallower side's distance through the other plane, of the straddling triangle(s)"""
        out = np.full(len(P), np.inf)
        for s, o in ((0, 1), (1, 0)):
            A, n = self._plane(X, P[:, o])
            h = ((X[self.F[P[:, s]]] - A[:, None, :]) * n[:, None, :]).sum(2)
            st = (h.min(1) < 0) & (h.max(1) > 0)
            d = np.minimum(-h.min(1), h.max(1))
            out = np.where(st, np.minimum(out, d), out)
        out[np.isinf(out)] = 0.0
        return out

    # ---- regions
    def _components(self, tris):
        """triangles of one piece joined where they share a vertex"""
        tris = list(tris); seen = set(); comps = []
        tset = set(tris)
        for t0 in tris:
            if t0 in seen:
                continue
            comp, st = [], [t0]; seen.add(t0)
            while st:
                t = st.pop(); comp.append(t)
                for v in self.F[t]:
                    for u in self.vt[v]:
                        if u in tset and u not in seen:
                            seen.add(u); st.append(u)
            comps.append(comp)
        return comps

    def _grow(self, seeds, block):
        seen = set(seeds); st = list(seeds)
        while st:
            v = st.pop()
            for w in self.nb[v]:
                w = int(w)
                if w in seen or w in block:
                    continue
                seen.add(w); st.append(w)
                if len(seen) > self.max_region:
                    return None
        return seen

    def regions(self, X, P, D):
        """candidate regions to move: list of (verts, partner triangles, depth) -- one per intersection group"""
        deep = D > self.min_depth
        P, D = P[deep], D[deep]
        if not len(P):
            return [], 0
        partner = collections.defaultdict(set)
        for a, b in P:
            partner[int(a)].add(int(b)); partner[int(b)].add(int(a))
        crossing = np.unique(P.ravel())
        comps = []
        for pc in np.unique(self.tp[crossing]):
            comps += self._components(crossing[self.tp[crossing] == pc].tolist())
        # one group = the components of both surfaces of one intersection
        cid = {}
        for k, c in enumerate(comps):
            for t in c:
                cid[t] = k
        par = list(range(len(comps)))

        def root(k):
            while par[k] != k:
                par[k] = par[par[k]]; k = par[k]
            return k
        for a, b in P:
            ra, rb = root(cid[int(a)]), root(cid[int(b)])
            if ra != rb:
                par[ra] = rb
        groups = collections.defaultdict(list)
        for k in range(len(comps)):
            groups[root(k)].append(k)
        out, both_big = [], 0
        for ks in groups.values():
            best = None
            for k in ks:
                comp = comps[k]
                # this surface's crossing vertices by the side of the surface they cross
                side = {}
                for t in comp:
                    for u in partner[t]:
                        A, n = self._plane(X, np.array([u]))
                        for v in self.F[t]:
                            h = float((X[v] - A[0]) @ n[0])
                            if abs(h) > abs(side.get(int(v), (0.0, None))[0]):
                                side[int(v)] = (h, u)
                neg = {v for v, (h, _) in side.items() if h < 0}
                pos = {v for v, (h, _) in side.items() if h >= 0}
                for seeds, block, sg in ((neg, pos, -1.0), (pos, neg, 1.0)):
                    if not seeds:
                        continue
                    r = self._grow(seeds, block)
                    if r is None:
                        continue
                    if best is None or len(r) < len(best[0]):
                        tri_o = sorted(set(u for t in comp for u in partner[t]))
                        best = (sorted(r), tri_o, sg, k)
            if best is None:
                # the line does not cut the surface in two: by a seam both pieces' outlines are there and the
                # line ends at the other piece's edge (all 14 groups of a pants' crotch, 2026-10-01). Then near the
                # line: the side of the other surface with fewer vertices of this piece (the part that poked through,
                # held between the line and its own outline)
                best = self._local(X, comps, ks, partner)
                if best is None:
                    both_big += 1
                    continue
                self.log['local'] = self.log.get('local', 0) + 1
            out.append(best)
        return out, both_big

    def _local(self, X, comps, ks, partner):
        best = None
        for k in ks:
            comp = comps[k]
            pc = int(self.tp[comp[0]])
            vs = set(int(v) for t in comp for v in self.F[t])
            for _ in range(self.rings):
                vs |= set(int(w) for v in list(vs) for w in self.nb[v])
            vs = np.asarray(sorted(v for v in vs if self.vp[v] == pc), np.int64)
            To = np.asarray(sorted(set(u for t in comp for u in partner[t])), np.int64)
            # the other surface around the line: its crossing triangles and their neighbours on that piece
            ov = set(int(v) for t in To for v in self.F[t])
            for _ in range(2):
                ov |= set(int(w) for v in list(ov) for w in self.nb[v])
            opc = int(self.tp[To[0]])
            Tn = np.asarray(sorted(set(t for v in ov for t in self.vt[v] if self.tp[t] == opc)), np.int64)
            A, B, C = X[self.F[Tn, 0]], X[self.F[Tn, 1]], X[self.F[Tn, 2]]
            n = np.cross(B - A, C - A); n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-18
            # sides by the winding normal (one piece is wound one way, so a fold keeps its side of the sheet)
            p = X[vs]
            h = ((p[:, None, :] - A[None]) * n[None]).sum(2)
            pr = p[:, None, :] - h[..., None] * n[None]
            v0, v1, v2 = (B - A)[None], (C - A)[None], pr - A[None]
            d00, d01, d11 = (v0 * v0).sum(2), (v0 * v1).sum(2), (v1 * v1).sum(2)
            d20, d21 = (v2 * v0).sum(2), (v2 * v1).sum(2)
            den = d00 * d11 - d01 * d01 + 1e-30
            b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den
            inside = (b1 >= -0.05) & (b2 >= -0.05) & (b1 + b2 <= 1.05) & (np.abs(h) < self.reach)
            hh = np.where(inside, np.abs(h), np.inf)
            j = np.argmin(hh, axis=1)
            ok = np.isfinite(hh[np.arange(len(vs)), j])
            hs = h[np.arange(len(vs)), j]
            neg, pos = ok & (hs < 0), ok & (hs >= 0)
            nn, np_ = int(neg.sum()), int(pos.sum())
            if not nn or not np_:
                continue
            small, sg = (neg, -1.0) if nn < np_ else (pos, 1.0)
            if min(nn, np_) > self.local_ratio * max(nn, np_):
                continue
            r = vs[small].tolist()
            if best is None or len(r) < len(best[0]):
                best = (r, Tn.tolist(), sg, k)
        return best

    # ---- the move
    def move(self, X, V, region, inv_mass):
        verts, tri_o, sg, _ = region
        verts = np.asarray([v for v in verts if inv_mass[v] > 0.0], np.int64)
        if not len(verts):
            return None
        To = np.asarray(tri_o, np.int64)
        A, n = self._plane(X, To)
        cen = X[self.F[To]].mean(1)
        # each region vertex against its nearest partner triangle's plane, normal pointing to the region's side
        k = np.argmin(((X[verts][:, None, :] - cen[None]) ** 2).sum(2), axis=1)
        nn = sg * n[k]
        h = ((X[verts] - A[k]) * nn).sum(1)
        amt = np.where(h > -self.gap, h + self.gap, 0.0)
        Xn = X.copy(); Vn = V.copy()
        Xn[verts] -= amt[:, None] * nn
        vn = (V[verts] * nn).sum(1)
        Vn[verts] -= vn[:, None] * nn
        return Xn, Vn, verts

    def _across_fold(self, ta, tb):
        """same piece, and tb lies where ta would be folded over one of the piece's fold lines"""
        if self.tp[ta] != self.tp[tb]:
            return False
        ca = self.flat[self.F[ta]].mean(0); cb = self.flat[self.F[tb]].mean(0)
        for L in self.fold_segs.get(int(self.tp[ta]), []):
            A, B = L[:-1], L[1:]
            AB = B - A; ln2 = np.maximum((AB * AB).sum(1), 1e-18)
            u = np.clip(((ca - A) * AB).sum(1) / ln2, 0, 1)
            k = int(np.argmin(np.linalg.norm(A + u[:, None] * AB - ca, axis=1)))
            d = AB[k] / np.sqrt(ln2[k]); nrm = np.array([-d[1], d[0]])
            r = ca - 2.0 * ((ca - A[k]) @ nrm) * nrm                        # ta folded over the line
            if np.linalg.norm(r - cb) < max(self.mirror_tol, 0.3 * abs((ca - A[k]) @ nrm)):
                return True
        return False

    def _outward(self, X, T):
        """+1 / -1 per triangle: its winding normal points away from the body / towards it"""
        A, n = self._plane(X, T)
        c = X[self.F[T]].mean(1)
        _, i = self.body_kd.query(c)
        return np.where(((c - self.body[i]) * n).sum(1) >= 0, 1.0, -1.0), A, n

    def remember(self, X, P):
        """first-contact sides: vertices within mem_r of another layer (another piece, or its own piece far on
        the pattern), not in a crossing now, and with no side for that layer yet"""
        from scipy.spatial import cKDTree
        if self.no_mem is None:
            self.no_mem = np.zeros(self.n, bool)
            if self._stitched is not None and len(self._stitched):
                sv = set(int(v) for v in self._stitched)
                for _ in range(self._mem_rings):
                    sv |= set(int(w) for v in list(sv) for w in self.nb[v])
                self.no_mem[list(sv)] = True
        busy = np.zeros(self.n, bool)
        if len(P):
            busy[self.F[P.ravel()].ravel()] = True
        cen = X[self.F].mean(1)
        el = np.linalg.norm(X[self.F[:, 1]] - X[self.F[:, 0]], axis=1)
        reach = self.mem_r + float(np.percentile(el, 99))
        kd = cKDTree(cen)
        vs = np.nonzero(~busy & ~self.no_mem)[0]
        d, cand = kd.query(X[vs], k=12, distance_upper_bound=reach)
        ok = np.isfinite(d)
        for col in range(cand.shape[1]):
            m = ok[:, col]
            if not m.any():
                continue
            v = vs[m]; t = cand[m, col]
            other = self.tp[t] != self.vp[v]
            if self.flat is not None:
                far = np.linalg.norm(self.flat[v] - self.flat[self.F[t]].mean(1), axis=1) >= self.flat_far
                other = other | far
            notadj = ~(self.F[t] == v[:, None]).any(1)
            m2 = other & notadj & ~self.no_mem[self.F[np.minimum(t, len(self.F) - 1)]].any(1)
            if not m2.any():
                continue
            v, t = v[m2], t[m2]
            o, A, n = self._outward(X, t)
            h = ((X[v] - A) * n).sum(1)
            near = np.abs(h) < self.mem_r
            for vv, tt, hh, oo in zip(v[near].tolist(), t[near].tolist(), h[near].tolist(), o[near].tolist()):
                key = (vv, int(self.tp[tt]))
                if key not in self.vmem and abs(hh) > 1e-6:
                    self.vmem[key] = float(np.sign(hh) * oo)       # +1 = outside the other layer
        self.log['memory'] = len(self.vmem)

    def save_memory(self, path, sel):
        """the first-contact sides in full-garment vertex numbers, for the next stage"""
        import json
        json.dump([[int(sel[v]), int(p), s] for (v, p), s in self.vmem.items()], open(path, 'w'))

    def load_memory(self, path, sel):
        import json, os
        if not os.path.exists(path):
            return
        loc = {int(g): i for i, g in enumerate(sel)}
        for g, p, s in json.load(open(path)):
            if int(g) in loc:
                self.vmem[(loc[int(g)], int(p))] = float(s)
        self.log['memory_loaded'] = len(self.vmem)

    def retreat(self, X, P, D):
        """per-vertex displacement for the deep pairs (both triangles of each pair back off)"""
        P = P[D > self.min_depth]
        if self.persist > 1:
            inc = np.zeros(self.n, bool)
            if len(P):
                inc[self.F[P.ravel()].ravel()] = True
            self.vage = np.where(inc, self.vage + 1, 0)
            if len(P):
                old = (self.vage[self.F[P[:, 0]]].max(1) >= self.persist) | (self.vage[self.F[P[:, 1]]].max(1) >= self.persist)
                P = P[old]
        if self.cross_piece_only and len(P):
            P = P[self.tp[P[:, 0]] != self.tp[P[:, 1]]]
        if self.seam_rings and len(P):
            if self.near_seam is None:
                self.near_seam = np.zeros(self.n, bool)
                if self._stitched is not None and len(self._stitched):
                    sv = set(int(v) for v in self._stitched)
                    for _ in range(self.seam_rings):
                        sv |= set(int(w) for v in list(sv) for w in self.nb[v])
                    self.near_seam[list(sv)] = True
            ns_ = self.near_seam[self.F[P[:, 0]]].any(1) | self.near_seam[self.F[P[:, 1]]].any(1)
            self.log['skipped_near_seam'] = self.log.get('skipped_near_seam', 0) + int(ns_.sum())
            P = P[~ns_]
        if self.same_parallel_skip and len(P):
            _, na = self._plane(X, P[:, 0]); _, nb_ = self._plane(X, P[:, 1])
            sp = (self.tp[P[:, 0]] == self.tp[P[:, 1]]) & (np.abs((na * nb_).sum(1)) > self.par_cos)
            self.log['skipped_same_parallel'] = self.log.get('skipped_same_parallel', 0) + int(sp.sum())
            P = P[~sp]
        if self.flat is not None and len(P):
            same = self.tp[P[:, 0]] == self.tp[P[:, 1]]
            c0 = self.flat[self.F[P[:, 0]]].mean(1); c1 = self.flat[self.F[P[:, 1]]].mean(1)
            near2d = np.linalg.norm(c0 - c1, axis=1) < self.flat_far
            self.log['skipped_flat_near'] = self.log.get('skipped_flat_near', 0) + int((same & near2d).sum())
            P = P[~(same & near2d)]
            if self.fold_segs and len(P):
                keep = np.array([not self._across_fold(int(a), int(b)) for a, b in P], bool)
                self.log['skipped_across_fold'] = self.log.get('skipped_across_fold', 0) + int((~keep).sum())
                P = P[keep]
        acc = np.zeros((self.n, 3)); wsum = np.zeros(self.n)
        if not len(P):
            self.side_mem = {}
            return acc, 0
        # which pieces the back-off acts on (test log), by pair of pieces
        pp = self.log.setdefault('pairs_by_piece', {})
        for a_, b_ in P.tolist():
            k_ = f'{min(self.tp[a_], self.tp[b_])}x{max(self.tp[a_], self.tp[b_])}'
            pp[k_] = pp.get(k_, 0) + 1
        mem = {}
        for s, o in ((0, 1), (1, 0)):
            ts, to = P[:, s], P[:, o]
            A, n = self._plane(X, to)
            h = ((X[self.F[ts]] - A[:, None, :]) * n[:, None, :]).sum(2)         # (m, 3) own verts vs other plane
            side = np.sign(h.sum(1) + 1e-15)                                     # where most of me is
            if self.side_from == 'contact' and self.body_kd is not None:
                o_to, _, _ = self._outward(X, to)
                _, ns_ = self._plane(X, ts)
                par = np.abs((ns_ * n).sum(1)) > self.par_cos       # only two layers lying on each other
                used = 0
                for k_, (a_, b_) in enumerate(zip(ts.tolist(), to.tolist())):
                    if not par[k_]:
                        continue
                    votes = [self.vmem.get((int(v_), int(self.tp[b_]))) for v_ in self.F[a_]]
                    votes = [x_ for x_ in votes if x_ is not None]
                    if votes:
                        side[k_] = np.sign(sum(votes) + 1e-9) * o_to[k_]
                        used += 1
                self.log['side_from_memory'] = self.log.get('side_from_memory', 0) + used
            if self.side_from == 'history':
                # the history only where the two lie nearly parallel (a lapel on its front): crossing at an angle
                # the majority side is clear, and the history lost a trousers' crotch (deep 0/0/0 -> 1-12)
                As_, ns_ = self._plane(X, ts)
                par = np.abs((ns_ * n).sum(1)) > self.par_cos
                for k_, (a_, b_) in enumerate(zip(ts.tolist(), to.tolist())):
                    key = (a_, b_)
                    if not par[k_]:
                        continue
                    if key in self.side_mem:
                        side[k_] = self.side_mem[key]
                    elif self.Xprev is not None and not self._exact(self.Xprev, np.array([[a_, b_]]))[0]:
                        # the side it had one check ago, if the two did not cross then (a crossing that slid onto
                        # these triangles already had the wrong side one check ago: keep the majority side)
                        Ap, npv = self._plane(self.Xprev, np.array([b_]))
                        hp = float((self.Xprev[self.F[a_]].mean(0) - Ap[0]) @ npv[0])
                        if abs(hp) > 1e-7:
                            side[k_] = np.sign(hp)
                    mem[key] = side[k_]
            wrong = np.maximum(-side[:, None] * h, 0.0).max(1)                   # deepest vertex on the other side
            amt = np.minimum(0.5 * wrong + self.gap, self.cap)
            mv = (side * amt)[:, None] * n                                       # (m, 3) the whole triangle moves
            for k in range(3):
                np.add.at(acc, self.F[ts, k], mv); np.add.at(wsum, self.F[ts, k], 1.0)
        if self.side_from == 'history':
            self.side_mem = mem
        core = wsum > 0
        acc[core] /= wsum[core][:, None]
        if self.spread > 0:
            # the one-ring follows at spread x (the fabric comes along instead of tearing away, as Separate does)
            ring = np.zeros((self.n, 3)); rw = np.zeros(self.n)
            for v in np.nonzero(core)[0]:
                nb = self.nb[v]; nb = nb[~core[nb]]
                np.add.at(ring, nb, acc[v]); np.add.at(rw, nb, 1.0)
            m = rw > 0
            acc[m] = self.spread * ring[m] / rw[m][:, None]
        return acc, int(core.sum())

    def step(self, state, inv_mass, f):
        q = state.particle_q
        X = q.numpy()[:self.n].astype(np.float64)
        P = self.pairs(q)
        D = self.depths(X, P) if len(P) else np.zeros(0)
        if self.dump and not self.log['frames']:
            np.savez(self.dump, P=P, D=D, X=X, F=self.F)
        n_deep = int((D > self.min_depth).sum())
        row = {'f': f, 'pairs': int(len(P)), 'deep': n_deep,
               'max_depth_mm': round(float(D.max()) * 1000, 2) if len(D) else 0.0}
        self.log['shallow_last'] = int(len(P)) - n_deep; self.log['deep_last'] = n_deep
        if not self.detect_only and self.mode == 'retreat':     # every check, so the vertex ages reset
            self._mem_tick = getattr(self, '_mem_tick', -1) + 1
            if self.side_from == 'contact' and self.body_kd is not None and self._mem_tick % self.mem_every == 0:
                self.remember(X, P[D > self.min_depth] if len(P) else P)
            mv, n_core = self.retreat(X, P, D)
            mv[inv_mass[:self.n] <= 0.0] = 0.0
            full_q = q.numpy(); full_v = state.particle_qd.numpy()
            full_q[:self.n] += mv.astype(np.float32)
            # the velocity along the move is taken out (it would carry the vertex straight back)
            ln = np.linalg.norm(mv, axis=1); m = ln > 0
            u = np.zeros_like(mv); u[m] = mv[m] / ln[m][:, None]
            V = full_v[:self.n].astype(np.float64); vn = (V * u).sum(1)
            V -= np.minimum(vn, 0.0)[:, None] * u
            full_v[:self.n] = V.astype(np.float32)
            q.assign(full_q); state.particle_qd.assign(full_v)
            self.Xprev = full_q[:self.n].astype(np.float64)
            row.update({'moved_verts': int(m.sum()), 'core': n_core})
            self.log['moved'] += int(m.sum())
        elif n_deep and not self.detect_only:
            import warp as wp
            regs, bb = self.regions(X, P, D)
            self.log['skipped_both_big'] += bb
            V = state.particle_qd.numpy()[:self.n].astype(np.float64)
            full_q = q.numpy(); full_v = state.particle_qd.numpy()
            moved, rev = 0, 0
            for r in regs:
                m = self.move(X, V, r, inv_mass)
                if m is None:
                    continue
                Xn, Vn, vs = m
                # kept only if no deep crossing touches the moved vertices afterwards (Fable r2 5: "4 removed and
                # 4 made" was taken every frame by a count-only rule)
                tq = full_q.copy(); tq[:self.n] = Xn.astype(np.float32)
                P2 = self.pairs(wp.array(tq, dtype=wp.vec3, device=q.device))
                D2 = self.depths(Xn, P2) if len(P2) else np.zeros(0)
                bad = P2[D2 > self.min_depth]
                vset = np.zeros(self.n, bool); vset[vs] = True
                if len(bad) and vset[self.F[bad.ravel()]].any():
                    rev += len(vs); self.log['reverted'] += 1
                    continue
                X, V = Xn, Vn; full_q = tq; moved += len(vs)
            if moved:
                full_v[:self.n] = V.astype(np.float32)
                q.assign(full_q); state.particle_qd.assign(full_v)
            row.update({'regions': len(regs), 'moved_verts': moved, 'reverted_verts': rev, 'both_big': bb})
            self.log['moved'] += moved
        self.log['frames'].append(row)
