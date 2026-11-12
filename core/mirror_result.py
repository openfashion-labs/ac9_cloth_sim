"""The dressed garment's shape of one side of a left / right pair written onto the other side as its mirror image
(user 2026-11-04, DECISIONS cloth-sim-mirror-result-to-pair-2026-11-04): a side worked into shape by hand (grabbed in
Live) given to the other side at once.

Only pieces of a pair (mirrored twins, or two pieces made a Pair). A piece cut as one across the center (a back, a
collar) is not halved here. Nothing is simulated: the next Live / Phase Sim goes on from the shape written. Where the
two sides close over each other, the copy would lie on its source: when a person marked the closure -- an Over on a
seam between them, or buttons from one to the other -- the side on top is decided as a band's closure is (closure.py:
the seam's Over, then the garment's Front Over, then the side with the buttonholes) and the copy is put 2 mm out or in
where they overlap. Nothing is guessed from the pattern's shape (user 2026-11-02: no judging of pieces' kinds).
No bpy here: the operator (dress.py) reads the garment and writes the result.
"""
import numpy as np

GAP = 2.0               # mm the copy is put out (on top) or in where it overlaps its source (closure.DELTA)
NEAR = 8.0              # mm: a copied vertex this close to its source is in the overlap (fully moved)
FADE = 16.0             # mm: ... and the move fades out to nothing by this distance
SAME_TOL = 0.5          # mm: two meshes are the same mirrored mesh when the mirrored layout points match this close


def pairs(spec):
    """[(a, b)] the left / right pairs whose shape can be copied: mirrored twins and the pieces made a Pair."""
    from . import spec as S
    out = list(S.twins(spec))
    seen = {frozenset(p) for p in out}
    if spec.get('symmetry') != 'off':
        for a, b in spec.get('pairs', []):
            if a != b and frozenset((a, b)) not in seen:
                out.append((a, b))
                seen.add(frozenset((a, b)))
    return out


def plan(spec, selected, lead):
    """([(source, target)], notes): each selected piece of a pair is copied onto its partner. When both of a pair are
    selected the lead (the active piece) is the source; without it in the pair the pair is left out (said)."""
    sel = list(dict.fromkeys(selected))
    out, notes = [], []
    for a, b in pairs(spec):
        ia, ib = a in sel, b in sel
        if not (ia or ib):
            continue
        if ia and ib:
            if lead in (a, b):
                out.append((lead, b if lead == a else a))
            else:
                notes.append(f"{a} / {b}: both selected, click the side to copy last")
            continue
        out.append((a, b) if ia else (b, a))
    for n in sel:
        if not any(n in p for p in pairs(spec)):
            notes.append(f"{n}: not one of a left / right pair (a piece cut as one is fixed by hand)")
    return out, notes


def _mirror_layout(F, src_flat, tgt_flat, plane='x'):
    """Layout points F of the target mirrored into the source's layout (the bounding boxes' centers matched, as
    placed_view.mirror_flat does the other way; across y for a front / back pair)."""
    ca = 0.5 * (tgt_flat.min(0) + tgt_flat.max(0))
    cb = 0.5 * (src_flat.min(0) + src_flat.max(0))
    F = np.asarray(F, float) - ca
    F[:, 1 if plane == 'y' else 0] *= -1.0
    return F + cb


def bary(Q, V, T, X):
    """Positions for layout points Q from the source piece (layout V, local triangles T, positions X): barycentric in
    the containing triangle, else the nearest one's (clamped). numpy only; pieces are a few thousand vertices."""
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    v0, v1 = B - A, C - A
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    den = np.where(np.abs(d00 * d11 - d01 * d01) < 1e-12, 1e-12, d00 * d11 - d01 * d01)
    cen = (A + B + C) / 3.0
    out = np.empty((len(Q), 3))
    for s in range(0, len(Q), 256):
        q = Q[s:s + 256]
        cand = np.argsort(((q[:, None, :] - cen[None]) ** 2).sum(-1), 1)[:, :12]
        v2 = q[:, None, :] - A[cand]
        d20 = (v2 * v0[cand]).sum(-1); d21 = (v2 * v1[cand]).sum(-1)
        v = (d11[cand] * d20 - d01[cand] * d21) / den[cand]
        w = (d00[cand] * d21 - d01[cand] * d20) / den[cand]
        u = 1.0 - v - w
        k = np.argmax(np.minimum(np.minimum(u, v), w), 1)          # the containing (or least outside) candidate
        r = np.arange(len(q))
        t = cand[r, k]
        bw = np.clip(np.stack([u[r, k], v[r, k], w[r, k]], 1), 0.0, None)
        bw /= np.maximum(bw.sum(1, keepdims=True), 1e-12)
        out[s:s + 256] = bw[:, :1] * X[T[t, 0]] + bw[:, 1:2] * X[T[t, 1]] + bw[:, 2:] * X[T[t, 2]]
    return out


def mirror_piece(X, flat, F, rng_src, rng_tgt, cx, plane='x'):
    """(positions for the target's vertices, 'same' | 'mapped'): the source's shape mirrored about x = cx (front /
    back, plane 'y': about y = cx). The same
    mirrored mesh (a pair meshed as mirror images: vertex i is vertex i's mirror) is copied vertex for vertex; other
    meshes through the layout (barycentric in the source's triangles)."""
    s0, s1 = rng_src
    t0, t1 = rng_tgt
    from .spec import mirror_points
    M = mirror_points(np.asarray(X[s0:s1], float), plane, cx)
    fs, ft = np.asarray(flat[s0:s1], float), np.asarray(flat[t0:t1], float)
    if s1 - s0 == t1 - t0:
        if float(np.abs(_mirror_layout(ft, fs, ft, plane) - fs).max(initial=0.0)) <= SAME_TOL:
            return M, 'same'
    F = np.asarray(F)
    T = F[(F[:, 0] >= s0) & (F[:, 0] < s1)] - s0
    return bary(_mirror_layout(ft, fs, ft, plane), fs, T, M), 'mapped'


def closure_top(spec, a, b, side_of):
    """(marked, top piece or None, by) for the pair a / b closing over each other. marked: a person marked them as a
    closure -- an Over ('a' / 'b') on a seam between them, or a button on one with its hole on the other. The top: the
    seam's Over, then the garment's Front Over ('left' / 'right', side_of(piece) tells each piece's side), then the
    piece with the buttonholes (closure.top_end's order). Not marked: (False, None, None) -- nothing is laid over."""
    over = None
    for sm in spec.get('seams', []):
        if sm.get('enabled', True) and {sm['a']['piece'], sm['b']['piece']} == {a, b} and sm.get('over') in ('a', 'b'):
            over = sm[sm['over']]['piece']
            break
    holes = [bt['hole']['piece'] for bt in spec.get('buttons', [])
             if bt.get('hole') and {bt.get('piece'), bt['hole'].get('piece')} == {a, b}]
    if over is None and not holes:
        return False, None, None
    if over is not None:
        return True, over, 'seam'
    fo = spec.get('closure_over')
    if fo in ('left', 'right') and side_of(a) != side_of(b):
        return True, (a if side_of(a) == fo else b), 'front'
    return True, holes[0], 'buttons'


def sewn_together(spec, a, b):
    """A seam joins the two sides of the pair (a center seam or a closure)."""
    return any(sm.get('enabled', True) and {sm['a']['piece'], sm['b']['piece']} == {a, b}
               for sm in spec.get('seams', []))


def side(Xp, cx):
    """'left' | 'right': the side of the body a piece is on (closure.top_end: x past the center is the left)."""
    return 'left' if float(np.asarray(Xp)[:, 0].mean()) > cx else 'right'


def lap(Xt, Xs, axis_xy, top):
    """The copied target Xt moved off its source Xs where they overlap: out (away from the body's axis, horizontally)
    when the target is the top side, else in, by GAP, fading out from NEAR to FADE mm. Returns (Xt, vertices moved)."""
    Xt = np.array(Xt, float)
    d = np.full(len(Xt), np.inf)
    Xs = np.asarray(Xs, float)
    for s in range(0, len(Xt), 512):
        d[s:s + 512] = np.sqrt(((Xt[s:s + 512, None, :] - Xs[None]) ** 2).sum(-1)).min(1)
    w = np.clip((FADE - d) / (FADE - NEAR), 0.0, 1.0)
    if not (w > 0).any():
        return Xt, 0
    rad = Xt[:, :2] - np.asarray(axis_xy, float)[None]
    rad /= np.maximum(np.linalg.norm(rad, axis=1, keepdims=True), 1e-9)
    Xt[:, :2] += (GAP if top else -GAP) * w[:, None] * rad
    return Xt, int((w > 0).sum())


OPEN_MM = 2.0           # a seam this far apart after the copy is said


def opened_seams(X, pidx, ranges, targets, tol=OPEN_MM):
    """[(piece, piece, max gap mm)] of the seams touching the copied targets that are now apart, widest first.
    pidx: the job's scene.idx (E_st, E_seam); ranges {piece: (first, end)}."""
    E = np.asarray(pidx['E_st'])
    if not len(E):
        return []
    who = np.full(len(X), -1)
    names = list(ranges)
    for k, n in enumerate(names):
        who[ranges[n][0]:ranges[n][1]] = k
    tk = [names.index(t) for t in targets if t in ranges]
    touch = np.isin(who[E[:, 0]], tk) | np.isin(who[E[:, 1]], tk)
    gap = np.linalg.norm(np.asarray(X)[E[:, 0]] - np.asarray(X)[E[:, 1]], axis=1)
    out = {}
    for i in np.where(touch & (gap > tol))[0]:
        key = tuple(sorted((names[who[E[i, 0]]], names[who[E[i, 1]]])))
        out[key] = max(out.get(key, 0.0), float(gap[i]))
    return sorted(((a, b, g) for (a, b), g in out.items()), key=lambda r: -r[2])
