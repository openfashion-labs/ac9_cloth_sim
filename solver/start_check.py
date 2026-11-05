# SPDX-License-Identifier: GPL-3.0-or-later
"""The stage-start check: before frame 1, the pieces that start moving in this stage are looked at where they are.

A piece lying inside the body or through other cloth is no problem while it is not simulated; the moment it joins,
the stage would start from a broken shape and nothing after it means anything. Four times a run was judged that had
started so (user 2026-10-19): a shirt collar folded through the back yoke (10-07), a T-shirt rib 137 mm off the neck
(10-17), stages started from another job's frames (10-19), a rib joining 31-67 mm off its neckline through the back
(10-19). Rules written after each did not stop the next, so the solver looks itself.

Looked at, for the joining pieces only (all active pieces when the stage has no init_from, the active ones not in
init_from_pieces otherwise):
  inside   vertices inside the body (signed distance < -0.5 mm by the nearest face's normal)
  crossing cloth-through-cloth triangle pairs between a joining piece and another piece (within one piece:
           recorded and warned only -- the simulation is meant to take them out, user 2026-10-19), deeper than the contact holds (the guard's
           detector and its seam-line exception)
  through  stitches of this stage from a joining piece whose straight path goes through other cloth -- recorded
           only: layered pieces placed right (a shirt's fronts and placket) have such paths too
  side     a joining piece leaving its seam to both sides of its partner (part folded over it, part going on past):
           recorded and warned only (user 2026-10-19)
  lineage  init_from naming a stage of another job (the stage would start from someone else's frames)

Dress (env AC9_DRESS=1, set by dress.py) warns and goes on, as the 10-10 decision for hand-placed pieces; any other
caller (experiments, headless tools) stops before frame 1 unless env AC9_START_CHECK_ALLOW gives a reason, which the
log keeps. Written to run_log.json 'start_check' and start_check.json.
"""
import json
import os

import numpy as np


def _seg_tri_hits(P0, P1, A, B, C, eps=1e-9, each=False):
    """(m,) segments x (k,) triangles -> (m,) number of triangles each open segment passes through (Moller-Trumbore)"""
    d = P1 - P0
    e1, e2 = B - A, C - A
    hits = np.zeros(len(P0), np.int64)
    for i in range(len(P0)):
        h = np.cross(d[i], e2)
        a = (e1 * h).sum(1)
        ok = np.abs(a) > eps
        f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
        s = P0[i] - A
        u = f * (s * h).sum(1)
        q = np.cross(s, e1)
        v = f * (q * d[i]).sum(1)
        t = f * (q * e2).sum(1)
        m_ = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3) & (t < 1 - 1e-3)
        if each:
            return np.nonzero(m_)[0]
        hits[i] = int(m_.sum())
    return hits


def check(cfg, out, X, F, tri_piece, v_piece, pat_names, E, idle, body_inside, unt=None, seam_tri=None,
          q_wp=None, gap_mm=2.0, side_mm=10.0):
    """X (n, 3) m, F (t, 3), tri_piece (t,), v_piece (n,), E (s, 2) local stitches, idle: set of stitch ids not sewn in
    this stage, body_inside(P) -> bool (n,). Returns (report dict, action 'ok' | 'warn' | 'stop')."""
    import time
    t0 = time.perf_counter()
    names = [str(n) for n in pat_names] if pat_names is not None else []
    if cfg.get('init_from') and cfg.get('init_from_pieces') is not None:
        old = [names.index(n) for n in cfg['init_from_pieces'] if n in names]
        new_p = sorted(int(p) for p in np.unique(v_piece) if p not in old)
    elif cfg.get('init_from'):
        new_p = []
    else:
        new_p = sorted(int(p) for p in np.unique(v_piece))
    rep = {'joining': [names[p] if p < len(names) else str(p) for p in new_p]}
    bad, notes = [], []     # problems stop (outside Dress); notes are recorded and warned only
    if cfg.get('init_from'):
        job = os.path.normcase(os.path.normpath(os.path.dirname(os.path.dirname(os.path.abspath(cfg['init_from'])))))
        mine = os.path.normcase(os.path.normpath(os.path.dirname(os.path.abspath(out))))
        rep['lineage'] = 'own job' if job == mine else cfg['init_from']
        if job != mine and not cfg.get('init_from_other_job'):
            bad.append(f"starts from another job's frames ({cfg['init_from']})")
    if new_p:
        newv = np.isin(v_piece, new_p)
        X64 = np.asarray(X, np.float64)
        ins = body_inside(X64[newv])
        per = {}
        vp_new = v_piece[newv]
        for p in new_p:
            k = int(ins[vp_new == p].sum())
            if k:
                per[names[p] if p < len(names) else str(p)] = k
        rep['inside'] = per
        if per:
            bad.append('inside the body: ' + ', '.join(f"{n} {k}" for n, k in per.items()))
        # cloth through cloth, a joining piece on one side
        if unt is not None and q_wp is not None:
            P = unt.pairs(q_wp)
            if len(P):
                # between two pieces, or within one piece between triangles sharing no vertex (a rib's two ends
                # laid crossing at a V-neck, 2026-10-19, went unseen while only pairs of pieces were counted)
                Fq_ = np.asarray(F, np.int64)
                shared_ = np.array([len(set(Fq_[a_]) & set(Fq_[b_])) > 0 for a_, b_ in P], bool)
                P = P[(tri_piece[P[:, 0]] != tri_piece[P[:, 1]]) | ~shared_]
            if len(P):
                P = P[unt.depths(X64, P) > 0.00045]
            if seam_tri is not None and len(P):
                P = P[[not any((k_, 1 - sd_) in seam_tri.get(int(b_), ()) for k_, sd_ in seam_tri.get(int(a_), ()))
                       for a_, b_ in P]]
            if len(P):
                P = P[np.isin(tri_piece[P[:, 0]], new_p) | np.isin(tri_piece[P[:, 1]], new_p)]
            cnt, own = {}, {}
            for a_, b_ in P:
                if tri_piece[a_] == tri_piece[b_]:
                    own[names[tri_piece[a_]]] = own.get(names[tri_piece[a_]], 0) + 1
                    continue
                k_ = ' x '.join(sorted((names[tri_piece[a_]], names[tri_piece[b_]])))
                cnt[k_] = cnt.get(k_, 0) + 1
            rep['crossing'] = cnt
            rep['crossing_within'] = own
            if cnt:
                bad.append('through other cloth: ' + ', '.join(f"{k} {n}" for k, n in sorted(cnt.items(), key=lambda x: -x[1])))
            if own:
                # recorded and warned, not stopped (user 2026-10-19): the simulation is meant to take these out
                notes.append('through itself: ' + ', '.join(f"{k} {n}" for k, n in own.items()))
        # which side of the seam the joining piece goes to: per stitch, the piece's direction away from its sewn
        # vertex against its partner's. Folded back onto the partner (a facing) or going on past it (a rib into the
        # neck opening) are both right, a mix along one seam is not: a laid rib went down over the back bodice for
        # 80 mm of the back neck and up everywhere else, and was sewn in that way (2026-10-19)
        nb_ = [[] for _ in range(len(X64))]
        for t_ in np.asarray(F, np.int64):
            for i_ in range(3):
                nb_[t_[i_]] += [t_[(i_ + 1) % 3], t_[(i_ + 2) % 3]]
        E_s = np.asarray(E, np.int64).reshape(-1, 2)
        stv_ = np.zeros(len(X64), bool); stv_[E_s.ravel()] = True

        def _away(v):
            w_ = [u for u in set(nb_[v]) if not stv_[u] and v_piece[u] == v_piece[v]]
            if not w_:
                return None
            r_ = X64[w_].mean(0) - X64[v]
            n_ = np.linalg.norm(r_)
            return r_ / n_ if n_ > 1e-9 else None
        side = {}
        for k_, (a_, b_) in enumerate(E_s):
            if k_ in idle or newv[a_] == newv[b_] or np.linalg.norm(X64[a_] - X64[b_]) * 1000.0 > side_mm:
                continue                  # only where the two already lie together: a piece still far off has no side
            if not newv[a_]:
                a_, b_ = b_, a_
            da_, db_ = _away(a_), _away(b_)
            if da_ is None or db_ is None:
                continue
            key_ = f"{names[v_piece[a_]]} on {names[v_piece[b_]]}"
            side.setdefault(key_, [0, 0])[int(float(da_ @ db_) > 0.0)] += 1
        rep['side'] = {k_: {'past': v_[0], 'folded_over': v_[1]} for k_, v_ in side.items()}
        mixed = {k_: v_ for k_, v_ in side.items() if min(v_) >= 3 and min(v_) >= 0.05 * sum(v_)}
        if mixed:      # recorded and warned, not stopped (user 2026-10-19)
            notes.append('goes to both sides of its seam: ' + ', '.join(f"{k_} ({v_[1]} of {sum(v_)} stitches folded over)"
                                                                    for k_, v_ in mixed.items()))
        # straight stitch paths from a joining piece through other cloth
        E_ = np.asarray(E, np.int64).reshape(-1, 2)
        live = np.array([k not in idle for k in range(len(E_))], bool)
        jn = newv[E_[:, 0]] | newv[E_[:, 1]] if len(E_) else np.zeros(0, bool)
        gap = np.linalg.norm(X64[E_[:, 0]] - X64[E_[:, 1]], axis=1) * 1000.0 if len(E_) else np.zeros(0)
        sel = np.nonzero(live & jn & (gap > gap_mm))[0]
        thr = {}
        if len(sel):
            Fa = np.asarray(F, np.int64)
            A, B, C = X64[Fa[:, 0]], X64[Fa[:, 1]], X64[Fa[:, 2]]
            # only the triangles whose box (1 um wider) meets the stitch's: the same hits as testing them all (the
            # test of a triangle does not depend on the others), 1-2 s less per stage (sim_latency 2026-10-20)
            lo_t, hi_t = np.minimum(np.minimum(A, B), C) - 1e-6, np.maximum(np.maximum(A, B), C) + 1e-6
            for k in sel:
                a, b = int(E_[k, 0]), int(E_[k, 1])
                lo_s, hi_s = np.minimum(X64[a], X64[b]), np.maximum(X64[a], X64[b])
                near = np.nonzero((hi_t >= lo_s).all(1) & (lo_t <= hi_s).all(1))[0]
                keep = near[~((Fa[near] == a) | (Fa[near] == b)).any(1)]
                hit = _seg_tri_hits(X64[a][None], X64[b][None], A[keep], B[keep], C[keep], each=True)
                if len(hit):
                    pa, pb = names[v_piece[a]], names[v_piece[b]]
                    via = sorted({names[tri_piece[t]] for t in keep[hit]})
                    k_ = ' - '.join(sorted((pa, pb))) + ' through ' + '/'.join(via)
                    thr[k_] = thr.get(k_, 0) + 1
        rep['through'] = thr
        rep['stitch_gap_mm_p50_max'] = ([round(float(np.median(gap[jn & live])), 1), round(float(gap[jn & live].max()), 1)]
                                        if (jn & live).any() else None)
        # recorded, not a problem: a good shirt's fronts and placket are layered by design and their seams'
        # straight paths cross the other layer (19 on one Dress job, 2026-10-19), as the one-stage test bed's
        # underarm side seams cross the sleeves (5 + 5)
    rep['problems'] = bad
    rep['notes'] = notes
    rep['t_s'] = round(time.perf_counter() - t0, 2)
    if notes:
        json.dump(rep, open(os.path.join(out, 'start_check.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if not bad:
        return rep, ('warn' if notes else 'ok')
    allow = os.environ.get('AC9_START_CHECK_ALLOW', '').strip()
    if os.environ.get('AC9_DRESS') == '1':
        act_ = 'warn'
    elif allow:
        act_ = 'warn'; rep['allowed_because'] = allow
    else:
        act_ = 'stop'
    rep['action'] = act_
    json.dump(rep, open(os.path.join(out, 'start_check.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return rep, act_
