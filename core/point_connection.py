# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored internal nodes at outline corners: bidirectional moves and explicit detach.

Separate drafting entities keep their identity. False in boundary_nodes records
an intentional detach; geometric proximity must not silently reconnect it.
Reading an old exact coincidence never writes the document.
"""
from contextlib import contextmanager
import numpy as np

_busy = 0
EPS = 1e-5
ROUND_EPS = 1e-4  # mm: two coordinates rounded by draft._r to four decimal places


@contextmanager
def raw():
    global _busy
    _busy += 1
    try:
        yield
    finally:
        _busy -= 1


def corner(pc, it, k):
    from . import draft as D, iline as IL
    links = it.get('boundary_nodes', {})
    if str(k) in links:
        value = links[str(k)]
        return next((j for j, e in enumerate(pc['edges']) if e['id'] == value), None) if value else None
    if not IL.editable(it) or it.get('kind') == 'baseline':
        return None
    N = IL.shape(it)[0]
    if k < 0 or k >= len(N):
        return None
    hits = np.flatnonzero(np.linalg.norm(D.corners(pc) - N[k], axis=1) < EPS)
    return int(hits[0]) if len(hits) == 1 else None


def members(pc, j):
    from . import iline as IL
    return [(it['id'], k) for it in pc.get('internals', []) if IL.editable(it)
            for k in range(len(IL.shape(it)[0])) if corner(pc, it, k) == j]


def detach(pc, lid, k):
    from . import draft as D
    D.internal(pc, lid).setdefault('boundary_nodes', {})[str(k)] = False


def bind(pc, lid, k, *, boundary_snap=None, _mirrored=False):
    from . import draft as D, iline as IL
    it = D.internal(pc, lid); q = IL.shape(it)[0][k]
    if boundary_snap is not None:
        kind, owner, point = boundary_snap
        if (kind not in ('edge', 'mid', 'notch', 'corner') or owner != pc['name']
                or not np.isfinite(point).all() or not np.isfinite(q).all()
                or np.linalg.norm(q-np.asarray(point, float)) > ROUND_EPS):
            return False
    hits = np.flatnonzero(np.linalg.norm(D.corners(pc) - q, axis=1) < EPS)
    if len(hits) == 1:
        it.setdefault('boundary_nodes', {})[str(k)] = pc['edges'][int(hits[0])]['id']
        return True
    # A witnessed outline snap restores the historical edge-endpoint carry.
    # Do not invent a second edge-reference schema or reconnect by proximity.
    N, _, closed, _ = IL.shape(it)
    if closed or k not in (0, len(N)-1) or boundary_snap is None:
        return False
    kind, owner, point = boundary_snap
    if kind not in ('edge', 'mid', 'notch', 'corner') or owner != pc['name']:
        return False
    from . import spec as S
    point = np.asarray(point, float)
    boundary = S.Outline(pc).B
    if (not np.isfinite(point).all() or np.linalg.norm(q-point) > ROUND_EPS
            or D._dist_to_polyline(np.atleast_2d(point), np.vstack((boundary,boundary[:1])))[0] > (ROUND_EPS if _mirrored else EPS)):
        return False
    links = it.get('boundary_nodes', {})
    links.pop(str(k), None)
    if not links:
        it.pop('boundary_nodes', None)
    return True




def _targets(spec, name, lid):
    from . import draft as D
    targets = list(D.mirror_lines(spec, name, lid))
    # A self-symmetric line may also have a twin: both ends of each copy
    # participate in the actual edit. Compose each copy's self reflection.
    for nm, other, M in [(name, lid, lambda q: np.asarray(q, float))] + list(targets):
        pc = D.piece(spec, nm)
        if D.self_mirror_line(pc, other)[0] == other:
            R = D.self_reflect(pc)
            targets.append((nm, other, lambda q, M=M, R=R: R(M(q))))
    return targets


def bind_pair(spec, name, lid, k, *, boundary_snap=None):
    from . import draft as D, iline as IL
    pc = D.piece(spec, name); q = IL.shape(D.internal(pc, lid))[0][k]
    bound = bind(pc, lid, k, boundary_snap=boundary_snap)
    if not bound:
        return False
    seen = {(name, lid, k)}
    for nm, other, M in _targets(spec, name, lid):
        dst = D.piece(spec, nm); N = IL.shape(D.internal(dst, other))[0]
        hits = np.flatnonzero(np.linalg.norm(N - M([q])[0], axis=1) < ROUND_EPS)
        if len(hits) == 1:
            key = (nm, other, int(hits[0]))
            if key in seen:
                continue
            seen.add(key)
            snap = (boundary_snap[0], nm, M([boundary_snap[2]])[0]) if boundary_snap is not None else None
            bind(dst, other, int(hits[0]), boundary_snap=snap, _mirrored=True)
    return bound


def move(spec, name, lid, deltas):
    if _busy:return None
    from . import boundary_connection as BC
    changed=BC.move(spec,name,lid,deltas)
    return changed if changed is not None else _move_boundary(spec,name,lid,deltas)


def _move_boundary(spec, name, lid, deltas):
    """Return None when no outline corner participates; otherwise one coherent edit."""
    from . import draft as D, iline as IL
    if _busy:
        return None
    pc = D.piece(spec, name); it = D.internal(pc, lid)
    N = IL.shape(it)[0].copy(); linked = {}
    for k, d in deltas.items():
        j = corner(pc, it, int(k))
        if j is not None:
            linked[j] = tuple(d)
    if not linked:
        return None
    with raw():
        changed = D.move_corners(spec, name, linked)
        now = IL.shape(D.internal(D.piece(spec, name), lid))[0]
        residual = {int(k): tuple(N[int(k)] + np.asarray(d) - now[int(k)]) for k, d in deltas.items()}
        changed += IL.move_nodes(spec, name, lid, residual)
    return list(dict.fromkeys(changed))


def remap(it, before, after, indices=None):
    """Keep detach/link identity when insertion or removal changes node indices."""
    if len(before) == len(after):
        return
    for field in ('boundary_nodes','vertex_groups'):
        if not it.get(field):continue
        links = {}
        for key, value in it[field].items():
            k = int(key)
            if indices is not None:
                if k in indices:links[str(indices[k])]=value
                continue
            if 0 <= k < len(before):
                hits = np.flatnonzero(np.linalg.norm(after - before[k], axis=1) < EPS)
                if len(hits) == 1:links[str(int(hits[0]))] = value
        it[field] = links


def detach_pair(spec, name, lid, k):
    from . import draft as D, iline as IL
    pc = D.piece(spec, name); q = IL.shape(D.internal(pc, lid))[0][k]
    targets = _targets(spec, name, lid)
    detach(pc, lid, k)
    seen = {(name, lid, k)}
    for nm, other, M in targets:
        dst = D.piece(spec, nm); N = IL.shape(D.internal(dst, other))[0]
        hits = np.flatnonzero(np.linalg.norm(N - M([q])[0], axis=1) < ROUND_EPS)
        if len(hits) == 1:
            key = (nm, other, int(hits[0]))
            if key not in seen:
                seen.add(key)
                detach(dst, other, int(hits[0]))


def carry(pc, C, newC, deltas):
    """Carry attached nodes once, retaining the historical edge-interior endpoint carry."""
    from . import draft as D, iline as IL
    for it in pc.get('internals', []):
        if it.get('kind') == 'baseline' or len(it.get('pts', [])) < 2:
            continue
        N, H, closed, own = IL.shape(it); desired = {}
        for k, q in enumerate(N):
            j = corner(pc, it, k)
            if j is not None:
                if j in deltas:
                    desired[k] = newC[j]
                continue
            if str(k) in it.get('boundary_nodes', {}):
                continue
            if closed or k not in (0, len(N) - 1):
                continue
            distances = [D._dist_to_polyline(np.atleast_2d(q), np.asarray(edge['pts'], float))[0]
                         for edge in pc['edges']]
            # An end actually on an unmoved edge must not be claimed by an
            # adjacent moved edge merely because it is within the legacy 1 mm.
            nearest = int(np.argmin(distances))
            candidates = [nearest] if distances[nearest] <= ROUND_EPS else range(len(pc['edges']))
            for j in candidates:
                edge = pc['edges'][j]
                j1 = (j + 1) % len(C)
                if j not in deltas and j1 not in deltas:
                    continue
                if distances[j] <= D.LINE_END_ON_EDGE:
                    desired[k] = D._similarity(C[j], C[j1], newC[j], newC[j1])([q])[0]
                    break
        if not desired:
            continue
        if not closed:
            # Targets were captured before either endpoint transformed the other nodes.
            for k, which in ((0, 'start'), (len(N)-1, 'end')):
                if k in desired:
                    now = IL.shape(it)[0][k]
                    D._move_internal_one(it, which, desired[k] - now)
        N1, H1, _, _ = IL.shape(it)
        touched = False
        for k, target in desired.items():
            if closed or k not in (0, len(N) - 1):
                IL._move(N1, H1, closed, k, target - N1[k]); touched = True
        if touched:
            IL.set_shape(it, N1, H1)
