# SPDX-License-Identifier: GPL-3.0-or-later
"""Carry the dressed shape over a pattern edit (no bpy, no physics): where the garment starts when it is simulated
again. It shows so right after a pattern edit and settles when simulated; a frozen piece keeps it.

One rule: every point of the new pattern is where it was drawn, i.e. at the same place of the last result's flat
layout; a part outside the last result's cloth is laid on that cloth's surface carried straight on (the nearest
triangle's map extended), a part no longer in the pattern is simply gone. So what did not change stays as it hung,
added cloth goes on the side it was added, removed cloth disappears.
The one exception, so that a sewn seam starts closed: the two sides of a seam (one that was there before and is
still there) are sewn point for point by fraction, so both are laid on the old seam line the same way: the point at
fraction f of a side goes to arc length f * (L_ref / Lo_ref) * Lo from the end of the seam that moved least, past the
old end on along the old line's direction (Lo: this side's old length). L_ref, Lo_ref: the new and old lengths of the
side whose length changed least: a seam whose partner kept its length stays where the partner is (the longer side
eases in), a seam lengthened on both sides (a hem let down) goes on past its old end, and a seam the edit did not
change does not move, eased or not (before 2026-10-20 it was f * L_ref, which slid the shorter side of an eased seam
by the ease: a sleeve cap's tip 6 mm onto the next point and folded, Fable review REVIEW_fable_carry_redo). Pieces the edit did not change that are sewn only to sides it moved (a
turn-up, a cuff, a facing: followers) do not hold those sides: the sides are laid where drawn and the followers are
carried whole after them (the best rotation + shift onto the seams). The outline between sewn sides takes the shift of its two ends linearly, the
inside takes it by mean value coordinates (which keep the identity exactly, so an edit does not spread).
"""
import numpy as np
from ._i18n import rpt

from . import spec as S


def _outline_s(ol, P):
    """Outline parameter s of each point P (nearest point of the closed outline)."""
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    out = np.zeros(len(P))
    for k0 in range(0, len(P), 256):
        Q = P[k0:k0 + 256]
        t = np.clip(((Q[:, None, :] - A[None]) * Dd[None]).sum(-1) / np.maximum(ll, 1e-12)[None], 0, 1)
        d = np.linalg.norm(A[None] + t[..., None] * Dd[None] - Q[:, None, :], axis=-1)
        j = d.argmin(1)
        out[k0:k0 + 256] = ol.cum[j] + t[np.arange(len(Q)), j] * np.sqrt(ll[j])
    return out


def _along(P, u):
    """Points at arc lengths u (array) along the polyline P; past either end, on along that end's direction."""
    u = np.atleast_1d(np.asarray(u, float))
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    L = cum[-1]
    out = np.stack([np.interp(u, cum, P[:, 0]), np.interp(u, cum, P[:, 1])], 1)
    b0 = min(2.0, L)
    d0 = P[0] - np.array([np.interp(b0, cum, P[:, 0]), np.interp(b0, cum, P[:, 1])])
    d0 /= max(np.linalg.norm(d0), 1e-12)
    b1 = max(L - 2.0, 0.0)
    d1 = P[-1] - np.array([np.interp(b1, cum, P[:, 0]), np.interp(b1, cum, P[:, 1])])
    d1 /= max(np.linalg.norm(d1), 1e-12)
    lo, hi = u < 0, u > L
    out[lo] = P[0] + np.outer(-u[lo], d0)
    out[hi] = P[-1] + np.outer(u[hi] - L, d1)
    return out


MOVED_MM = 0.5            # a seam side whose ends moved more than this in the layout moved with the edit
FOLLOW_RMS_MM = 3.0       # pieces carried along: the rigid fit to the seams they follow must be this good, or within
FOLLOW_RMS_FRAC = 0.5     # this fraction of the mean motion (the hem let down 30 mm: sides laid past the old cloth
                          # scatter sideways by up to 19 mm, the fit 10.5 mm on 26 mm, 2026-10-08)


def _same_outline(pa, pb):
    """The same edges with points within 1e-6 mm. Points equal as written (the pieces an edit did not touch) answer
    at once; only the others go to allclose (it was 0.12 s of a 0.76 s pants edit for 133 pieces, 2026-10-27)."""
    ea, eb = pa['edges'], pb['edges']
    return len(ea) == len(eb) and all(x['id'] == y['id'] and len(x['pts']) == len(y['pts'])
                                      and (x['pts'] == y['pts'] or np.allclose(np.asarray(x['pts'], float),
                                                                               np.asarray(y['pts'], float), atol=1e-6))
                                      for x, y in zip(ea, eb))


def _side_moved(ol_o, ol_n, so, sn):
    """How far (mm) the ends of a seam side moved in the layout."""
    on, oo = ol_n[sn['piece']], ol_o[so['piece']]
    return max(float(np.linalg.norm(on.at(on.s_of(*sn[e])) - oo.at(oo.s_of(*so[e])))) for e in ('from', 'to'))


def followers(old_spec, new_spec, reset=()):
    """Pieces the edit did not change that are sewn only to sides the edit moved (or to each other): a hem's
    turn-up, a cuff, a facing. They are carried along whole with what they are sewn to (carry), instead of staying
    where they hung and holding the moved side back -- the hem let down 30 mm stayed on its unchanged turn-up and
    the 30 mm of cloth had nowhere to go (2026-10-08). reset: pieces a Reset put back to their placed shapes; they
    are no follower and nothing follows them. Returns [set of piece names] (one per connected group)."""
    po = {p['name']: p for p in old_spec['pieces']}
    pn = {p['name']: p for p in new_spec['pieces']}
    ol_o, ol_n = S.Outlines(po), S.Outlines(pn)
    old = {sm['id']: sm for sm in old_spec.get('seams', []) if sm.get('enabled', True)}
    links = []                      # (piece, other piece, the other's side moved)
    for sm in new_spec.get('seams', []):
        so = old.get(sm['id'])
        if so is None or not sm.get('enabled', True):
            continue
        for k, o in (('a', 'b'), ('b', 'a')):
            p, q = sm[k]['piece'], sm[o]['piece']
            if p == q or p not in po or q not in po or so[o]['piece'] != q:
                continue
            # moved with the edit: the side's ends moved in the layout because q's outline changed. A seam end slid
            # along an outline that did not change (a seam re-attached) moves no cloth (2026-10-14)
            mv = (q not in reset and 'line' not in sm[o] and 'line' not in so[o] and q in pn
                  and not _same_outline(po[q], pn[q]) and _side_moved(ol_o, ol_n, so[o], sm[o]) > MOVED_MM)
            links.append((p, q, mv))
    C = {n for n in pn if n in po and n not in reset and _same_outline(po[n], pn[n])}
    while True:
        drop = {p for p, q, mv in links if p in C and q not in C and not mv}
        if not drop:
            break
        C -= drop
    anchors = {p for p, q, mv in links if p in C and q not in C and mv}
    adj = {}
    for p, q, _ in links:
        if p in C and q in C:
            adj.setdefault(p, set()).add(q)
            adj.setdefault(q, set()).add(p)
    groups, seen = [], set()
    for a in sorted(anchors):
        if a in seen:
            continue
        g, todo = set(), [a]
        while todo:
            x = todo.pop()
            if x in g:
                continue
            g.add(x)
            todo += list(adj.get(x, ()))
        seen |= g
        groups.append(g)
    return groups


def _rigid_fit(P, Q):
    """(R, t, rms) of the rotation + shift taking points P to Q best (Kabsch)."""
    cp, cq = P.mean(0), Q.mean(0)
    H = (P - cp).T @ (Q - cq)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    t = cq - R @ cp
    rms = float(np.sqrt(((P @ R.T + t - Q) ** 2).sum(1).mean()))
    return R, t, rms


class FoldError(ValueError):
    """A carry that would fold a piece's cloth over itself. piece; points: the folding triangles' centers on the new
    pattern (mm, the pattern's own coordinates); seams: the ids of the seams whose sides are nearest to them."""

    def __init__(self, msg, piece, points, seams):
        super().__init__(msg)
        self.piece, self.points, self.seams = piece, points, seams


def fold_error(spec, name, V, tris):
    """FoldError for the triangles `tris` of piece `name` (vertices V): where they are, by which seams. The places are
    merged within 20 mm (one fold is often a few triangles side by side)."""
    C = V[np.asarray(tris)].mean(1)
    places = []
    for c in C:
        if not any(np.linalg.norm(c - q) < 20.0 for q in places):
            places.append(c)
    pc = next(p for p in spec['pieces'] if p['name'] == name)
    ol = S.Outline(pc)
    seams = []
    for c in places:
        best = None
        for sm in spec.get('seams', []):
            if not sm.get('enabled', True):
                continue
            for k in ('a', 'b'):
                sd = sm[k]
                if sd['piece'] != name or 'line' in sd:
                    continue
                P = ol.side_polyline(sd, step=2.0)
                d = float(np.linalg.norm(P - c, axis=1).min())
                if best is None or d < best[0]:
                    best = (d, sm['id'])
        if best and best[1] not in seams:
            seams.append(best[1])
    where = f" near seam {' / '.join(seams)}" if seams else ""
    n = len(places)
    return FoldError(f"{name}: the cloth would fold over itself{where} ({n} place{'s' if n > 1 else ''})",
                     name, np.asarray(places), seams)


def _sewn_sides(old_spec, new_spec, skip=()):
    """[(piece, new side, old side, base end 'from' | 'to', scale)] for the outline sides of seams in both specs. The
    base end is the end whose two sides moved least in the layout (the sum over both sides; from on a tie); scale = L_ref / Lo_ref, the
    new over the old length of the side whose length changed least (a side's old length times it: its new arc). Seams with a piece in `skip` (pieces carried along) are left
    out: the side sewn to them is laid where it was drawn. A side on a piece whose outline did not change is not
    returned (its points stay where they are drawn: a seam re-attached along an unchanged outline moves no cloth;
    laid on the old seam, 33 of 132 such edits on a shirt folded the cloth and the rest moved pieces up to 17 mm,
    2026-10-14); it still counts for the scale."""
    po = {p['name']: p for p in old_spec['pieces']}
    pn = {p['name']: p for p in new_spec['pieces']}
    ol_o, ol_n = S.Outlines(po), S.Outlines(pn)
    old = {sm['id']: sm for sm in old_spec.get('seams', []) if sm.get('enabled', True)}
    out = []
    for sm in new_spec.get('seams', []):
        so = old.get(sm['id'])
        if so is None or not sm.get('enabled', True):
            continue
        if sm['a']['piece'] in skip or sm['b']['piece'] in skip:
            continue
        keys = [k for k in ('a', 'b') if 'line' not in sm[k] and 'line' not in so[k]
                and sm[k]['piece'] == so[k]['piece'] and sm[k]['piece'] in ol_n and sm[k]['piece'] in ol_o]
        if not keys:
            continue
        moved = {'from': 0.0, 'to': 0.0}
        for k in keys:
            on, oo = ol_n[sm[k]['piece']], ol_o[so[k]['piece']]
            for e in ('from', 'to'):
                moved[e] += float(np.linalg.norm(on.at(on.s_of(*sm[k][e])) - oo.at(oo.s_of(*so[k][e]))))
        base = 'from' if moved['from'] <= moved['to'] else 'to'
        ln = {k: abs(ol_n[sm[k]['piece']].span(sm[k])) for k in keys}
        lo = {k: abs(ol_o[so[k]['piece']].span(so[k])) for k in keys}
        kr = min(keys, key=lambda k: abs(ln[k] - lo[k]))
        scale = ln[kr] / max(lo[kr], 1e-9)
        for k in keys:
            if _same_outline(po[sm[k]['piece']], pn[sm[k]['piece']]):
                continue
            out.append((sm[k]['piece'], sm[k], so[k], base, scale))
    return out


def outline_images(old_spec, new_spec, name, Vb, skip=()):
    """psi of the new mesh's outline vertices Vb of piece `name`: where each was in the last result's layout.
    skip: pieces carried along (followers), whose seams do not hold the sides sewn to them."""
    ol = S.Outline(next(p for p in new_spec['pieces'] if p['name'] == name))
    s = _outline_s(ol, Vb)
    acc, cnt = np.zeros_like(Vb), np.zeros(len(Vb))
    ends = []                                  # (outline s, correction) at the ends of sewn sides
    for pc, sn, so, base, scale in _sewn_sides(old_spec, new_spec, skip):
        if pc != name:
            continue
        Pn = S.side_polyline(new_spec, sn, {name: ol}, step=0.5)
        Po = S.side_polyline(old_spec, so, step=0.5)
        Ln = S.polyline_len(Pn)
        Lo = S.polyline_len(Po)
        s0, span = ol.s_of(*sn['from']), ol.span(sn)
        u_from = (s - s0) % ol.L if span > 0 else (s0 - s) % ol.L          # arc from the side's 'from' end
        on = u_from <= abs(span) + 1e-6

        def image(uf):                                 # arc from the new side's 'from' -> point on the old side
            u = uf if base == 'from' else Ln - uf      # arc from the base end
            u = u / max(Ln, 1e-9) * scale * Lo         # the same fraction of the seam on both sides
            return _along(Po, u) if base == 'from' else _along(Po, Lo - u)
        acc[on] += image(u_from[on])
        cnt[on] += 1
        pe = image(np.array([0.0, Ln]))
        ends.append((ol.s_of(*sn['from']), pe[0] - Pn[0]))
        ends.append((ol.s_of(*sn['to']), pe[1] - Pn[-1]))
    psi = Vb.astype(float).copy()
    sewn = cnt > 0
    psi[sewn] = acc[sewn] / cnt[sewn][:, None]
    free = ~sewn
    if free.any() and ends:
        es = np.array([e[0] for e in ends])
        ec = np.array([e[1] for e in ends])
        for k in np.nonzero(free)[0]:
            back = (s[k] - es) % ol.L                  # arc back to each sewn end
            fwd = (es - s[k]) % ol.L
            a, b = int(back.argmin()), int(fwd.argmin())
            t = back[a] / max(back[a] + fwd[b], 1e-9)
            psi[k] = Vb[k] + (1 - t) * ec[a] + t * ec[b]
    return psi


def mean_value_weights(poly, Q):
    """(len(Q), len(poly)) mean value coordinates of the points Q in the closed polygon poly (Floater 2003)."""
    W = np.zeros((len(Q), len(poly)))
    for k0 in range(0, len(Q), 512):
        q = Q[k0:k0 + 512]
        d = poly[None, :, :] - q[:, None, :]
        r = np.maximum(np.linalg.norm(d, axis=-1), 1e-12)
        dn, rn = np.roll(d, -1, axis=1), np.roll(r, -1, axis=1)
        cross = d[..., 0] * dn[..., 1] - d[..., 1] * dn[..., 0]
        dot = (d * dn).sum(-1)
        tan_half = cross / np.maximum(r * rn + dot, 1e-12)
        w = (np.roll(tan_half, 1, axis=1) + tan_half) / r
        W[k0:k0 + 512] = w / w.sum(1, keepdims=True)
    return W


def signed_areas(V, T):
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


def bind(Q, V, T, X):
    """X evaluated at the flat points Q of a mesh (flat V, triangles T): barycentric in the nearest triangle, not
    clamped, so a point outside the mesh lands on the nearest triangle's plane carried on."""
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    v0, v1 = B - A, C - A
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-18, 1e-18, den)
    try:                                   # in Blender: the nearest triangle from a BVH (C), else numpy below
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
        bvh = BVHTree.FromPolygons([(float(x), float(y), 0.0) for x, y in V], T.tolist(), all_triangles=True)
        t = np.array([bvh.find_nearest(Vector((float(x), float(y), 0.0)))[2] for x, y in Q])
        v2 = Q - A[t]
        d20 = (v2 * v0[t]).sum(1); d21 = (v2 * v1[t]).sum(1)
        v = (d11[t] * d20 - d01[t] * d21) / den[t]
        w = (d00[t] * d21 - d01[t] * d20) / den[t]
        u = 1 - v - w
        return u[:, None] * X[T[t, 0]] + v[:, None] * X[T[t, 1]] + w[:, None] * X[T[t, 2]]
    except ImportError:
        pass
    out = np.zeros((len(Q), X.shape[1]))

    def seg_d(q, P0, P1):
        D = P1 - P0
        t = np.clip(((q[:, None, :] - P0[None]) * D[None]).sum(-1) / np.maximum((D * D).sum(1), 1e-12)[None], 0, 1)
        return np.linalg.norm(P0[None] + t[..., None] * D[None] - q[:, None, :], axis=-1)
    for k0 in range(0, len(Q), 64):
        q = Q[k0:k0 + 64]
        v2 = q[:, None, :] - A[None]
        d20 = (v2 * v0[None]).sum(-1); d21 = (v2 * v1[None]).sum(-1)
        v = (d11[None] * d20 - d01[None] * d21) / den[None]
        w = (d00[None] * d21 - d01[None] * d20) / den[None]
        u = 1 - v - w
        inside = (u >= -1e-9) & (v >= -1e-9) & (w >= -1e-9)
        dist = np.minimum(np.minimum(seg_d(q, A, B), seg_d(q, B, C)), seg_d(q, C, A))
        dist = np.where(inside, -1.0, dist)
        t = dist.argmin(1)
        r = np.arange(len(q))
        out[k0:k0 + 64] = (u[r, t, None] * X[T[t, 0]] + v[r, t, None] * X[T[t, 1]] + w[r, t, None] * X[T[t, 2]])
    return out


def _piece_mesh(pat, idx, X, n):
    names = [str(x) for x in pat['names']]
    offs = list(idx['offs']) + [len(idx['flat'])]
    j = names.index(n)
    return idx['flat'][offs[j]:offs[j + 1]], idx['F'][idx['fp'] == j] - offs[j], X[offs[j]:offs[j + 1]]


def _follow(old_spec, new_spec, group, skip, old_pat, old_idx, X_old, new_pat, rigid=None):
    """Carry a group of followers whole: the rotation + shift that takes their seam sides (as they hung) to the
    sides they are sewn to (as carried, sides laid where drawn). Returns ({piece: (n, 3)}, rms mm) or (None, rms)."""
    onames = [str(n) for n in old_pat['names']]
    P, Q = [], []
    for sm in new_spec.get('seams', []):
        if not sm.get('enabled', True):
            continue
        for g, c in (('a', 'b'), ('b', 'a')):
            pg, pc = sm[g]['piece'], sm[c]['piece']
            if pg not in group or pc in group or 'line' in sm[g] or 'line' in sm[c]:
                continue
            if pg not in onames or pc not in onames:
                continue
            Pg = S.side_polyline(new_spec, sm[g], step=2.0)
            Pc = S.side_polyline(new_spec, sm[c], step=2.0)
            m = max(3, len(Pg), len(Pc))
            Pg, Pc = S.resample(Pg, m), S.resample(Pc, m)
            Vg, Tg, Xg = _piece_mesh(old_pat, old_idx, X_old, pg)
            Vc, Tc, Xc = _piece_mesh(old_pat, old_idx, X_old, pc)
            P.append(bind(Pg, Vg, Tg, Xg))
            if rigid and pc in rigid:                  # sewn to a piece moved whole: follow that motion
                Q.append(bind(back_map(rigid[pc], Pc), Vc, Tc, Xc))
            else:
                Q.append(bind(outline_images(old_spec, new_spec, pc, Pc, skip), Vc, Tc, Xc))
    if not P:
        return None, float('inf')
    P, Q = np.vstack(P), np.vstack(Q)
    R, t, rms = _rigid_fit(P, Q)
    if rms > max(FOLLOW_RMS_MM, FOLLOW_RMS_FRAC * float(np.linalg.norm(Q - P, axis=1).mean())):
        return None, rms
    out = {}
    nnames = [str(x) for x in new_pat['names']]
    for g in group:
        if g in onames and g in nnames:
            # the new mesh of the piece (an unchanged outline is meshed anew when a sewn partner changes: the seam
            # points follow the partner's, 187 -> 167 vertices on a turn-up) laid on the old one, then moved
            Vg, Tg, Xg = _piece_mesh(old_pat, old_idx, X_old, g)
            out[g] = bind(np.asarray(new_pat[f"V{nnames.index(g)}"], float), Vg, Tg, Xg) @ R.T + t
    return out, rms


MIN_AREA_MM2 = 1e-4    # a carried triangle smaller than this in 3D: the solver drops it and stops (2026-10-08)


def tri_areas_3d(X, T):
    T = np.asarray(T)
    return 0.5 * np.linalg.norm(np.cross(X[T[:, 1]] - X[T[:, 0]], X[T[:, 2]] - X[T[:, 0]]), axis=1)


SAME_SHAPE_MM = 0.05   # a pattern within this of a result's mesh (outline both ways, internal lines onto its edges)


def shape_changed(spec, pat, idx=None, tol=SAME_SHAPE_MM):
    """Pieces of the spec whose shape is not the one a result was meshed from (its pattern.npz): the outline against
    the mesh's outline both ways, and every constrained mesh vertex (C: the internal lines sewn or folded, the
    crossings) on the spec's outline or its internal lines (not the baselines, which are never meshed), within tol
    mm. The mesh is not remade, so a result meshed by an older mesher or at another mesh size still matches the
    pattern it came from; a line no constraint was made for shapes no cloth. Pieces only in one of the two (added or
    switched off since) are not counted. Returns [(piece, mm)]."""
    from .pattern_build import dist_to_ring, dist_to_segments
    names = [str(n) for n in pat['names']]
    out = []
    for pc in spec['pieces']:
        n = pc['name']
        if n not in names or not pc.get('enabled', True):
            continue
        j = names.index(n)
        V = np.asarray(pat[f"V{j}"], float)
        Vb = V[:int(pat['nb'][j])]
        B = S.Outline(pc).B
        h = float(pat['h']) if 'h' in pat.files else 6.0
        d = float(dist_to_ring(Vb, B).max())       # the mesh's outline on the spec's, and the spec's corners near the
        if float(dist_to_ring(B, Vb).max()) > max(tol, 0.5 * h):     # mesh's (the mesher merges points closer than
            d = max(d, float(dist_to_ring(B, Vb).max()))             # 2.1 mm: a corner can sit 0.1 mm off it)
        C = np.asarray(pat[f"C{j}"]).reshape(-1, 2) if f"C{j}" in pat.files else np.zeros((0, 2), int)
        if d <= tol and len(C):
            segs = [(B, np.roll(B, -1, 0))] + [(L[:-1], L[1:]) for L in
                                             (np.asarray(it['pts'], float) for it in pc.get('internals', [])
                                              if it.get('kind') != 'baseline' and len(it['pts']) >= 2)]
            A = np.vstack([a for a, _ in segs])
            E = np.vstack([b for _, b in segs])
            d = float(dist_to_segments(V[np.unique(C.ravel())], A, E).max())
        if d > tol:
            out.append((n, round(d, 3)))
    return out


def back_map(r, P):
    """Points of the new layout back to where they were: r is (R, t) for a piece moved whole (new = R @ old + t), or
    a function new -> old for one moved in parts (a dart pivoted: the turned part turned back, the rest as it is)."""
    P = np.asarray(P, float)
    if callable(r):
        return r(P)
    R, t = r
    return (P - np.asarray(t, float)) @ np.asarray(R, float)


def carry(old_spec, new_spec, old_pat, old_idx, X_old, new_pat, new_tris, rigid=None, reset=(), sources=None):
    """Positions (mm) of the edited pattern's mesh on the last result. old_pat / old_idx: the last result's
    pattern.npz / scene.idx.npz (flat layout, triangles), X_old: its last frame (mm); new_pat / new_tris: the edited
    spec meshed (pattern_build + triangulate). Returns ({piece: (n, 3)}, report). Pieces the last result did not have
    are left out. rigid: {piece: (R, t)} for pieces the edit moved whole in the layout (new = R @ old + t), or {piece:
    function new -> old} for one moved in parts (back_map): the map is that motion undone, so they hang as they did. Pieces sewn only to sides the edit moved (followers: a turn-up, a
    cuff) are carried whole with them. reset: pieces a Reset put back to their placed shapes (X_old holds those
    shapes): each is laid on its own placed shape where drawn, and its seams hold nothing (2026-10-20: a sleeve
    edited, then Reset, was still carried and refused). sources: {new piece: the piece it was cut from} (core/cut.py):
    the new piece is laid on that piece's cloth where drawn (with rigid, else as the plain carry). Raises ValueError
    when the edit would lay cloth folded over itself."""
    reset = set(reset)
    groups = followers(old_spec, new_spec, reset)
    while True:
        skip = (set().union(*groups) if groups else set()) | reset
        moved, bad = {}, None
        for g in groups:
            pos, rms = _follow(old_spec, new_spec, g, skip, old_pat, old_idx, X_old, new_pat, rigid)
            if pos is None:
                bad = g
                break
            moved.update(pos)
            for n in g:
                moved.setdefault('__rms__' + n, rms)
        if bad is None:
            break
        groups = [g for g in groups if g is not bad]      # that group stays where it hung, holding its seams
    out, rep = _carry(old_spec, new_spec, old_pat, old_idx, X_old, new_pat, new_tris, rigid, skip, reset, sources)
    for n in skip:
        if n in moved:
            out[n] = moved[n]
            rep['pieces'][n] = {'carried along': round(float(moved['__rms__' + n]), 2)}
    return out, rep


def _carry(old_spec, new_spec, old_pat, old_idx, X_old, new_pat, new_tris, rigid, skip, reset=(), sources=None):
    onames = [str(n) for n in old_pat['names']]
    offs = list(old_idx['offs']) + [len(old_idx['flat'])]
    F, fp = old_idx['F'], old_idx['fp']
    out, rep = {}, {'pieces': {}}
    for i, n in enumerate([str(x) for x in new_pat['names']]):
        src = n if n in onames else (sources or {}).get(n)
        if src not in onames:
            continue
        j = onames.index(src)
        Vo = old_idx['flat'][offs[j]:offs[j + 1]]
        Xo = X_old[offs[j]:offs[j + 1]]
        T = F[fp == j] - offs[j]
        Vn = np.asarray(new_pat[f"V{i}"], float)
        if n in reset:                                  # its placed shape, the new pattern laid on it where drawn
            out[n] = Xo.copy() if len(Vn) == len(Vo) and np.allclose(Vn, Vo, atol=1e-6) else bind(Vn, Vo, T, Xo)
            rep['pieces'][n] = 'reset (placed)'
            continue
        if n in skip:
            continue
        if len(Vn) == len(Vo) and np.allclose(Vn, Vo, atol=1e-6):
            out[n] = Xo.copy()
            rep['pieces'][n] = 'unchanged'
            continue
        nb = int(new_pat['nb'][i])
        if rigid and n in rigid:
            X = bind(back_map(rigid[n], Vn), Vo, T, Xo)
            out[n] = X
            rep['pieces'][n] = 'moved whole' if not callable(rigid[n]) else 'moved in parts'
            nz = int((tri_areas_3d(X, new_tris[i]) < MIN_AREA_MM2).sum())
            if nz:
                raise ValueError(rpt("{0}: the edit would lay cloth flat to nothing ({1} triangles)", n, nz))
            continue
        psi = Vn.copy()
        psi[:nb] = outline_images(old_spec, new_spec, n, Vn[:nb], skip)
        corr = psi[:nb] - Vn[:nb]
        if nb < len(Vn) and np.abs(corr).max() > 1e-9:
            psi[nb:] = Vn[nb:] + mean_value_weights(Vn[:nb], Vn[nb:]) @ corr
        a0, a1 = signed_areas(Vn, new_tris[i]), signed_areas(psi, new_tris[i])
        flipped = int(((a1 <= 0) & (a0 > 0)).sum())
        if flipped:
            raise fold_error(new_spec, n, Vn, new_tris[i][(a1 <= 0) & (a0 > 0)])
        out[n] = bind(psi, Vo, T, Xo)
        rep['pieces'][n] = {'outline_shift_max_mm': round(float(np.linalg.norm(corr, axis=1).max()), 2),
                            'verts': [int(len(Vo)), int(len(Vn))]}
    return out, rep
