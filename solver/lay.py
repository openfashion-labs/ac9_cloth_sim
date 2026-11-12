# SPDX-License-Identifier: GPL-3.0-or-later
"""cfg 'lay' (user 2026-10-21; which pieces: core/lay.py): {"pieces": [name, ...]} -- at the start of the stage they
join, each piece's sewn edge laid on the points it is sewn to (where they are now: the last frame of the stage
before) and its width run on from there along the body, away from those pieces, 2 mm off the body. Ported from
experiments/rib_vneck_2026-10-16/run_rib.py 'lay' (the rule of 2026-10-19, user OK), the piece name and its
pattern's direction no longer fixed.

Checked before it is used (Fable review 2026-10-21 §3; never stops, the user's b2): the partners' sewn edge in one
piece (no jump along it), the length ratio (partner edge / band) in [RATIO_MIN, RATIO_MAX], and the laid shape against
the placed one on the same count -- vertices inside the body + edges through other cloth. Laid wins a tie; failing,
the piece starts where it was placed. Every outcome is one line in run_log 'lay'.
"""
import numpy as np
from spatial import cKDTree

from crossings import crossing_edges

GAP_MM = 2.0
RATIO_MIN, RATIO_MAX = 0.95, 1.35     # 推測: < 1 the band is longer than its edge (gathered); the T-shirt rib 1.035
JUMP_MM = 10.0                         # a step along the partners' edge longer than this and 5 x the median: not one line


def _body(B, T):
    nb = np.cross(B[T[:, 1]] - B[T[:, 0]], B[T[:, 2]] - B[T[:, 0]])
    nb /= np.maximum(np.linalg.norm(nb, axis=1), 1e-12)[:, None]
    cb = B[T].mean(1)
    return nb, cb, cKDTree(cb)


def _inside(P, body, tol=0.5):
    nb, cb, kd = body
    _, fi = kd.query(P)
    return int((np.einsum('ij,ij->i', P - cb[fi], nb[fi]) < -tol).sum())


def lay_piece(X, flat, v_piece, E, k, body, bridge=False):
    """(Xr (n_k, 3) mm or None, report). X full mm, flat full mm, v_piece full, E stitches (full), k piece index.
    bridge: the partners' edge may jump (pieces placed but not sewn yet: a T-shirt's front and back necklines apart
    at the shoulders, 368 mm on the cylinder): the band runs straight over the gaps, the length ratio without them
    (Lay on Edge before a Simulate, place_lay.py; measured 10/22: Live from it ended as Simulate's lay does, from
    the level ring the rib fell over the bodice, docs/testing/RESULT_place_tools_stage0_2026-10-22.md)."""
    R = np.nonzero(v_piece == k)[0]
    isk = v_piece == k
    m = isk[E[:, 0]] ^ isk[E[:, 1]]
    if not m.any():
        return None, {'skip': 'not sewn to another piece'}
    rv = np.where(isk[E[m, 0]], E[m, 0], E[m, 1]); pv = np.where(isk[E[m, 0]], E[m, 1], E[m, 0])
    # the pattern's direction along the sewn edge (its points lie on one line, core/lay.py condition 2)
    F2 = flat[rv] - flat[rv].mean(0)
    ax = np.linalg.svd(F2, full_matrices=False)[2][0]
    nx = np.array([-ax[1], ax[0]])
    u_all, y_all = flat @ ax, flat @ nx
    u_s = u_all[rv]
    o = np.argsort(u_s); u_s, P_s = u_s[o], X[pv][o]
    y_sewn = float(np.median(y_all[rv]))
    u = u_all[R]; w = np.abs(y_all[R] - y_sewn)
    st_ = np.linalg.norm(np.diff(P_s, axis=0), axis=1)
    rep = {'partners': sorted({int(x) for x in v_piece[pv]}), 'width_mm': round(float(w.max()), 1)}
    jump = st_ > max(JUMP_MM, 5.0 * float(np.median(st_))) if len(st_) else np.zeros(0, bool)
    if jump.any() and not bridge:
        rep['skip'] = f'the partners\' edge jumps {st_.max():.1f} mm'
        return None, rep
    if jump.any():
        rep['bridged_mm'] = [round(float(x), 1) for x in st_[jump]]
    band_len = float(u_s.max() - u_s.min())
    edge_len = float(st_[~jump].sum())
    ratio = edge_len / max(band_len, 1e-9)
    rep['ratio'] = round(ratio, 3)
    if not RATIO_MIN <= ratio <= RATIO_MAX:
        if not bridge:
            rep['skip'] = f'length ratio {ratio:.2f} outside {RATIO_MIN}-{RATIO_MAX}'
            return None, rep
        rep['ratio_off'] = True           # the button lays it anyway and says so (user 2026-11-02)
    nb, cb, kd = body
    P = np.stack([np.interp(u, u_s, P_s[:, c]) for c in range(3)], 1)
    du = 2.0
    t = np.stack([np.interp(u + du, u_s, P_s[:, c]) - np.interp(u - du, u_s, P_s[:, c]) for c in range(3)], 1)
    t /= np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
    nrm = nb[kd.query(P)[1]]
    d = np.cross(nrm, t); d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    # the width goes away from the partners where it is sewn: the sign against the mean direction to their vertices
    # within 12 mm that are not on the seam (run_rib 2026-10-19; towards the edge's centroid it went down over the
    # back bodice for 16-21 of 135 stitches)
    seam_ = np.zeros(len(X), bool); seam_[pv] = True
    bod = np.nonzero(np.isin(v_piece, rep['partners']) & ~seam_)[0]
    kb = cKDTree(X[bod])
    C = P_s.mean(0)
    b_ = np.array([(X[bod[q]] - p).mean(0) if q else p - C for p, q in zip(P, kb.query_ball_point(P, 12.0))])
    b_ -= np.einsum('ij,ij->i', b_, t)[:, None] * t
    d[np.einsum('ij,ij->i', d, b_) > 0] *= -1
    # smoothed along the band (a ring: its ends sewn to each other) over its own width, so at a sharp corner (a V's
    # bottom) the far edges of the two sides do not cross; kept in the body's tangent plane
    W = max(float(w.max()), 1.0)
    ug = np.arange(u.min(), u.max() + 1.0, 1.0)
    Lr = ug[-1] - ug[0] + 1.0
    ou = np.argsort(u)
    Dg = np.stack([np.interp(ug, u[ou], d[ou, c]) for c in range(3)], 1)
    dx_ = np.abs(ug[:, None] - ug[None, :]); dx_ = np.minimum(dx_, Lr - dx_)
    Sg = np.exp(-0.5 * (dx_ / W) ** 2) @ Dg
    d = np.stack([np.interp(u, ug, Sg[:, c]) for c in range(3)], 1)
    d -= np.einsum('ij,ij->i', d, nrm)[:, None] * nrm
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    Xr = P + w[:, None] * d + GAP_MM * nrm
    _, fi = kd.query(Xr)
    s = np.einsum('ij,ij->i', Xr - cb[fi], nb[fi])
    inside = s < GAP_MM
    Xr[inside] += (GAP_MM - s[inside])[:, None] * nb[fi[inside]]
    rep['pushed_out'] = int(inside.sum())
    return Xr, rep


def _crossings(X, F, fp, k, sewn):
    """Edges of piece k through other pieces' triangles + other pieces' edges through k's (edges with a sewn vertex
    left out: they sit on their partners)."""
    Tk, To = F[fp == k], F[fp != k]
    n = 0
    for Ta, Tb in ((Tk, To), (To, Tk)):
        if not len(Ta) or not len(Tb):
            continue
        Ea = np.unique(np.sort(np.r_[Ta[:, [0, 1]], Ta[:, [1, 2]], Ta[:, [2, 0]]], 1), axis=0)
        Ea = Ea[~(sewn[Ea[:, 0]] | sewn[Ea[:, 1]])]
        ei, _ = crossing_edges(X, Ea, Tb)
        n += len(np.unique(ei))
    return n


def apply(cfg, X_full, flat, v_piece, E, F, fp, pat_names, body_co_mm, body_tri, active_v, bridge=False):
    """X_full (mm) with the cfg's pieces laid where that passes the check; returns (X_full, log rows).
    F, fp: the active cloth's triangles (full vertex indices) and their piece; active_v: the active vertices.
    bridge: lay_piece's (Lay on Edge on the placed shapes, solver/lay_once.py)."""
    body = _body(body_co_mm, body_tri)
    keep = active_v[F].all(1)            # inactive cloth (not in this stage) is not judged against
    F, fp = F[keep], fp[keep]
    sewn = np.zeros(len(X_full), bool); sewn[np.asarray(E, np.int64).ravel()] = True
    rows = []
    for name in cfg['lay'].get('pieces', []):
        if name not in pat_names:
            continue
        k = pat_names.index(name)
        R = np.nonzero(v_piece == k)[0]
        Xr, rep = lay_piece(X_full, flat, v_piece, E, k, body, bridge=bridge)
        row = dict(piece=name, **rep)
        row['placed'] = {'inside': _inside(X_full[R], body), 'crossing': _crossings(X_full, F, fp, k, sewn)}
        if Xr is not None:
            Xa = X_full.copy()
            Xa[R] = Xr
            row['laid'] = {'inside': _inside(Xr, body), 'crossing': _crossings(Xa, F, fp, k, sewn)}
            sl, sp = sum(row['laid'].values()), sum(row['placed'].values())
            if sl <= sp or bridge:        # the button keeps what it laid, what is left red (user 2026-11-02)
                X_full[R] = Xr
                row['used'] = 'laid'
            else:
                row['used'] = 'placed'
                row['skip'] = f'laid {sl} problems, placed {sp}'
        else:
            row['used'] = 'placed'
        rows.append(row)
    return X_full, rows
