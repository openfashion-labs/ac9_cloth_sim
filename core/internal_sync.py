# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored internal-line correspondences. Readers never add lines or persist matches."""
import uuid
import numpy as np
from . import spec as S, fold_hand as FH
from ._i18n import rpt

KEY = 'mirror_id'


def partner(spec, name):
    for a, b in S.twins(spec, any_state=True):
        if name == a:return b
        if name == b:return a
    return None


def _error(lid):
    return ValueError(rpt('Cannot match internal line {0} to its mirror', lid))


def _ordered_close(P, Q, tol):
    """Compare every linear span at common normalized arc lengths, preserving traversal."""
    def lengths(X):
        t = np.r_[0., np.cumsum(np.linalg.norm(np.diff(X, axis=0), axis=1))]
        keep = np.r_[True, np.diff(t) > 1e-10]
        return X[keep], t[keep]
    P, tp = lengths(P);Q, tq = lengths(Q)
    if len(P) < 2 or len(Q) < 2:return False
    tp /= tp[-1];tq /= tq[-1]
    t = np.unique(np.r_[tp, tq])
    at = lambda X, u:np.column_stack([np.interp(t, u, X[:, k]) for k in range(2)])
    return np.linalg.norm(at(P, tp) - at(Q, tq), axis=1).max() < tol


def _same(a, b, M):
    from . import draft as D
    if (a.get('kind', 'line') != b.get('kind', 'line') or bool(a.get('closed')) != bool(b.get('closed'))
            or bool(a.get('hole')) != bool(b.get('hole'))):return False
    P, Q = np.asarray(M(a['pts']), float), np.asarray(b['pts'], float)
    if len(P) < 2 or len(Q) < 2 or not np.isfinite(P).all() or not np.isfinite(Q).all():return False
    if not a.get('closed'):
        return _ordered_close(P, Q, D.TWIN_TOL) or _ordered_close(P, Q[::-1], D.TWIN_TOL)
    if np.linalg.norm(P[0] - P[-1]) > 1e-10:P = np.vstack([P, P[0]])
    if np.linalg.norm(Q[0] - Q[-1]) < 1e-10:Q = Q[:-1]
    if len(Q) < 2:return False
    # A closed line may start anywhere on the same traversal, including inside a span.
    for seq in (Q, Q[::-1]):
        ends = np.roll(seq, -1, axis=0);v = ends - seq
        vv = np.einsum('ij,ij->i', v, v)
        u = np.clip(np.einsum('ij,ij->i', P[0] - seq, v) / np.maximum(vv, 1e-20), 0., 1.)
        hits = seq + u[:, None] * v
        for i in np.flatnonzero(np.linalg.norm(hits - P[0], axis=1) < D.TWIN_TOL):
            rotated = np.vstack([hits[i], seq[i+1:], seq[:i+1], hits[i]])
            if _ordered_close(P, rotated, D.TWIN_TOL):return True
    return False


def match(spec, name, lid):
    """(piece, local line id), or None. A broken stored ID never falls back to proximity."""
    from . import draft as D
    other = partner(spec, name)
    if other is None:return None
    pa, pb = D.piece(spec, name), D.piece(spec, other)
    src = next((it for it in pa.get('internals', []) if it['id'] == lid), None)
    if src is None:return None
    token = src.get(KEY)
    if KEY in src:
        mine = [it for it in pa.get('internals', []) if it.get(KEY) == token]
        theirs = [it for it in pb.get('internals', []) if it.get(KEY) == token]
        if not isinstance(token, str) or not token or len(mine) != 1 or len(theirs) != 1:raise _error(lid)
        return other, theirs[0]['id']
    if src.get('kind') == 'baseline':return None
    M, Mi = D.mirror_fn(pa, pb), D.mirror_fn(pb, pa)
    candidates = [it for it in pb.get('internals', []) if it.get('kind') != 'baseline' and _same(src, it, M)]
    if len(candidates) > 1:raise _error(lid)
    if not candidates:return None
    dst = candidates[0]
    reverse = [it for it in pa.get('internals', []) if it.get('kind') != 'baseline' and _same(dst, it, Mi)]
    if len(reverse) != 1 or KEY in dst:raise _error(lid)
    return other, dst['id']


def bind(spec, name, lid, other, tid):
    """Persist a known authored relationship inside the editing operation."""
    from . import draft as D
    if partner(spec, name) != other:raise _error(lid)
    a, b = D.internal(D.piece(spec, name), lid), D.internal(D.piece(spec, other), tid)
    if KEY in a or KEY in b:
        if match(spec, name, lid) != (other, tid):raise _error(lid)
        return
    a[KEY] = b[KEY] = uuid.uuid4().hex


def bind_targets(spec, name, lid, targets):
    from . import draft as D
    jobs = [(n, l, match(spec, n, l)) for n, l in
            dict.fromkeys([(name, lid)] + [(p, q) for p, q, _ in targets])]
    for n, l, peer in jobs:
        if peer:bind(spec, n, l, *peer)


def new_peer(spec, name, lid):
    """A visible counterpart created by a new drafting action, never by preparation."""
    from . import draft as D
    other = partner(spec, name)
    if other is None:return
    src = D.internal(D.piece(spec, name), lid)
    M = D.mirror_fn(D.piece(spec, name), D.piece(spec, other))
    tid = D.add_internal(spec, other, M(src['pts']), src.get('kind', 'line'), bool(src.get('closed')),
                         mirror=False, authored=bool(src.get('seam_authored_vertices')))
    dst = D.internal(D.piece(spec, other), tid)
    FH.carry(src, dst, M);bind(spec, name, lid, other, tid)


def copy_geometry(src, dst, M, rev=None):
    from . import iline as IL
    if rev is None:rev = not src.get('closed') and not FH.same_direction(M(src['pts']), dst['pts'])
    IL._copy_to(src, dst, M, rev)
    n = len(IL.shape(src)[0]);smooth = IL.smooth_nodes(src, n)
    IL._set_smooth_nodes(dst, {n-1-k for k in smooth} if rev else smooth, n)
    FH.carry(src, dst, M)


def set_elastic(spec, name, lid, ratio):
    from . import draft as D
    targets = D.mirror_lines(spec, name, lid)
    bind_targets(spec, name, lid, targets)
    keys = {(name, lid)} | {(p, l) for p, l, _ in targets}
    rows = [e for e in spec.get('elastic', []) if (e.get('piece'), e.get('line')) not in keys]
    if ratio < 1. - 1e-6:
        rows += [dict(piece=p, line=l, ratio=ratio) for p, l in sorted(keys)]
    if rows:spec['elastic'] = rows
    else:spec.pop('elastic', None)


def compatible(spec, name, other):
    """A shared mesh must not discard authored internal geometry or asymmetric line settings."""
    from . import draft as D
    pa, pb = D.piece(spec, name), D.piece(spec, other)
    aa = [it for it in pa.get('internals', []) if it.get('kind') != 'baseline']
    bb = [it for it in pb.get('internals', []) if it.get('kind') != 'baseline']
    if len(aa) != len(bb):return False
    M = D.mirror_fn(pa, pb)
    mapped = set()
    try:
        for a in aa:
            peer = match(spec, name, a['id'])
            if peer is None or peer[0] != other:return False
            b = D.internal(pb, peer[1])
            if b['id'] in mapped or not _same(a, b, M):return False
            mapped.add(b['id'])
            fs = [next((f for f in spec.get('folds', []) if f.get('piece') == n and f.get('line') == l), {})
                  for n, l in ((name, a['id']), (other, b['id']))]
            if not np.isclose(float(fs[0].get('deg') or 0), float(fs[1].get('deg') or 0)):return False
            if fs[0].get('deg'):
                same = FH.mapped_hand(a, b, M) == FH.hand(b)
                right = fs[1] if same else FH.swap_letters(fs[1])
                if any(fs[0].get(k) != right.get(k) for k in ('side', 'layers')):return False
            ratios = [next((e['ratio'] for e in spec.get('elastic', []) if e.get('piece') == n and e.get('line') == l), 1.)
                      for n, l in ((name, a['id']), (other, b['id']))]
            if not np.isclose(*ratios):return False
    except ValueError:
        return False
    return True
