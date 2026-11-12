# SPDX-License-Identifier: GPL-3.0-or-later
"""Slash and spread (no bpy): cut a piece along an internal line drawn from outline to outline and open it.

hinge (flare): the part on one side of the line turns about one end of the line (on the outline) until the other end
has opened `amount` mm; parallel (gathers): that part moves `amount` mm square to the line, both ends opening. Each
opening is bridged by a new straight edge. The part that moves is the one without the placement anchor and the
grain line (else the shorter outline), as for a pivoted dart (core/dart.py); its internal lines move with it (a line
across the cut moves point by point; the grain line never bends: it stays, or moves whole), and the cut line goes. A
seam side running across an opening now runs over the bridge too: it is longer by the spread (the fullness eased or
gathered into its partner); a seam through the hinge keeps its length.

The carry to 3D is the plain one (core/transfer.carry: every point where it was drawn, the seams held to their
partners): measured on trousers, it lays the added cloth out along the leg, where turning the moved part back and
folding the opening onto the old cut line left a crease Simulate did not undo (2026-10-10).
"""
import numpy as np
from ._i18n import rpt, length as len_text

from . import dart as DT
from . import draft as D
from . import spec as S

SNAP_MM = 1.0         # a cut line's end must be this close to the outline


def _outline_point(pc, q):
    ol = S.Outline(pc)
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((np.asarray(q, float) - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    d = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
    j = int(d.argmin())
    return ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j])), float(d[j])


def _corner_at(spec, name, q):
    """Make the outline point nearest q a corner (a corner within SEAM_SNAP is used as it is)."""
    pc = D.piece(spec, name)
    (eid, t), d = _outline_point(pc, q)
    if d > SNAP_MM:
        raise ValueError(rpt("the cut line's end is {0} off the outline: draw it from outline to outline", len_text(d)))
    k = D.edge_index(pc, eid)
    L = S.polyline_len(pc['edges'][k]['pts'])
    if t * L <= max(D.SEAM_SNAP, D.MIN_EDGE):
        return
    if (1 - t) * L <= max(D.SEAM_SNAP, D.MIN_EDGE):
        return
    D._split_one(spec, pc, k, t, D.new_edge_id(pc))


def _arc(n, k0, k1):
    """Edge indices from corner k0 forward to corner k1."""
    out, j = [], k0
    while j != k1:
        out.append(j)
        j = (j + 1) % n
    return out


def _arc_pts(pc, idx):
    return np.vstack([np.asarray(pc['edges'][j]['pts'][:-1], float) for j in idx]
                     + [np.asarray(pc['edges'][idx[-1]]['pts'][-1:], float)])


def _marks(spec, name):
    pc = D.piece(spec, name)
    out = []
    pl = spec.get('placement', {}).get(name)
    if pl and 'anchor' in pl:
        out.append(np.asarray(pl['anchor'], float))
    for it in pc.get('internals', []):
        if it.get('kind') == 'baseline' and len(it['pts']):
            out.append(np.asarray(it['pts'], float).mean(0))
    return np.array(out).reshape(-1, 2)


def _spread_one(spec, name, line_id, amount, mode, hinge, side, keep=None):
    pc = D.piece(spec, name)
    it = D.internal(pc, line_id)
    P = np.asarray(it['pts'], float)
    E0, E1 = P[0], P[-1]
    if np.linalg.norm(E1 - E0) < 5.0:
        raise ValueError(rpt("the cut line is too short"))
    _corner_at(spec, name, E0)
    _corner_at(spec, name, E1)
    pc = D.piece(spec, name)
    C = D.corners(pc)
    k0 = int(np.linalg.norm(C - E0, axis=1).argmin())
    k1 = int(np.linalg.norm(C - E1, axis=1).argmin())
    if k0 == k1:
        raise ValueError(rpt("the cut line's ends are on the same point of the outline"))
    n = len(pc['edges'])
    arcs = {'a': _arc(n, k0, k1), 'b': _arc(n, k1, k0)}
    # each side's region: its outline, closed back along the cut line itself
    regions = {'a': np.vstack([_arc_pts(pc, arcs['a']), P[::-1][1:-1]]),
               'b': np.vstack([_arc_pts(pc, arcs['b']), P[1:-1]])}
    if side == 'auto':
        M = _marks(spec, name)
        if keep is not None:                         # points to keep still (a symmetric piece's center)
            M = np.vstack([M, np.asarray(keep, float).reshape(-1, 2)])
        hit = {k: int(DT._inside(regions[k], M).sum()) if len(M) else 0 for k in arcs}
        if hit['a'] != hit['b']:
            side = 'a' if hit['a'] < hit['b'] else 'b'
        else:
            side = 'a' if S.polyline_len(_arc_pts(pc, arcs['a'])) <= S.polyline_len(_arc_pts(pc, arcs['b'])) else 'b'
    moving = arcs[side]
    region = regions[side]
    area0 = D.signed_area(S.Outline(pc).B)
    if mode == 'hinge':
        H, Sx = (E0, E1) if hinge == 'start' else (E1, E0)
        r = float(np.linalg.norm(Sx - H))
        if amount >= 2 * r:
            raise ValueError(rpt("the spread is wider than the cut line is long"))
        a = 2.0 * np.arcsin(0.5 * amount / r)
        motions = [(DT._rot(sg * a, H), DT._rot(-sg * a, H), sg) for sg in (1.0, -1.0)]
    else:
        d = (E1 - E0) / np.linalg.norm(E1 - E0)
        nrm = np.array([-d[1], d[0]])
        motions = []
        for sg in (1.0, -1.0):
            sh = sg * amount * nrm
            motions.append(((lambda Q, sh=sh: np.asarray(Q, float).reshape(-1, 2) + sh), None, sg))

    def opened_area(fwd):                          # the outline's area with the part moved one way
        ring = []
        for j in range(n):
            pts = np.asarray(pc['edges'][j]['pts'], float)
            ring.append(fwd(pts)[:-1] if j in moving else pts[:-1])
        return D.signed_area(np.vstack(ring))
    fwd = max(motions, key=lambda m: opened_area(m[0]))[0]         # opens: the area grows
    # move the part: outline edges (rigid), internal lines (by point; the grain line whole or not at all), anchor
    moved_lines = 0
    for jt in pc.get('internals', []):
        if jt['id'] == line_id:
            continue
        Q = np.asarray(jt['pts'], float)
        if not len(Q):
            continue
        inside = DT._inside(region, Q)
        if jt.get('kind') == 'baseline':
            if inside.all():
                jt['pts'] = D._r(fwd(Q))
                moved_lines += 1
            continue
        if inside.any():
            Q = Q.copy()
            Q[inside] = fwd(Q[inside])
            jt['pts'] = D._r(Q)
            moved_lines += 1
    pl = spec.get('placement', {}).get(name)
    if pl and 'anchor' in pl and DT._inside(region, pl['anchor'])[0]:
        pl['anchor'] = D._r(fwd(pl['anchor']))[0]
    for j in moving:
        e = pc['edges'][j]
        e['pts'] = D._r(fwd(e['pts']))
        if e.get('ctrl'):
            e['ctrl'] = D._r(fwd(e['ctrl']))
    # bridge the openings with straight edges
    ids = [e['id'] for e in pc['edges']]
    first, last = ids[moving[0]], ids[moving[-1]]
    bridges = []
    j = ids.index(first)
    p_end = np.asarray(pc['edges'][j - 1]['pts'][-1], float)
    q_start = np.asarray(pc['edges'][j]['pts'][0], float)
    if np.linalg.norm(p_end - q_start) > 1e-3:
        nid = D.new_edge_id(pc)
        pc['edges'].insert(j, {'id': nid, 'pts': D._r([p_end, q_start])})
        bridges.append(nid)
    ids = [e['id'] for e in pc['edges']]
    j = ids.index(last)
    q_end = np.asarray(pc['edges'][j]['pts'][-1], float)
    nx = (j + 1) % len(pc['edges'])
    p_start = np.asarray(pc['edges'][nx]['pts'][0], float)
    if np.linalg.norm(q_end - p_start) > 1e-3:
        nid = D.new_edge_id(pc)
        pc['edges'].insert(j + 1, {'id': nid, 'pts': D._r([q_end, p_start])})
        bridges.append(nid)
    pc['internals'] = [x for x in pc.get('internals', []) if x['id'] != line_id]
    spec['folds'] = [x for x in spec.get('folds', []) if not (x.get('piece') == name and x.get('line') == line_id)]
    info = {'bridges': bridges, 'moved_lines': moved_lines, 'side': side,
            'area_added': D.signed_area(S.Outline(pc).B) - area0}
    return info


def spread(spec, name, line_id, amount, mode='hinge', hinge='start', side='auto', twins=True):
    """Slash piece `name` along internal line `line_id` (drawn from outline to outline) and open it `amount` mm: mode
    'hinge' turns one side about the line's `hinge` end ('start' / 'end'), 'parallel' moves it square to the line.
    side: 'auto' (the side without the anchor and grain line), 'a' (the outline from the line's start forward to its
    end) or 'b'. The twin gets the mirror image. Returns {piece: info} (the bridge edges, internal lines moved, the
    side moved, the area added)."""
    if amount < D.MIN_EDGE:
        raise ValueError(rpt("spread at least {0}", len_text(D.MIN_EDGE)))
    if mode not in ('hinge', 'parallel'):
        raise ValueError(f"unknown spread {mode}")
    if seams_on_line(spec, name, line_id):
        raise ValueError(rpt("the line is sewn: cut along a line that is not part of a seam"))
    twin = None
    if twins:
        tw, lid_b, M = D._twin_internal(spec, name, line_id)
        if tw:
            Pa = M(D.internal(D.piece(spec, name), line_id)['pts'])
            Pb = np.asarray(D.internal(D.piece(spec, tw), lid_b)['pts'], float)
            rev = len(Pa) == len(Pb) and np.abs(Pb[::-1] - Pa).max() < np.abs(Pb - Pa).max()
            h_b = {'start': 'end', 'end': 'start'}[hinge] if rev else hinge
            # the mirror image runs the outline the other way: side a there is side b here (the same line order)
            s_b = side if side == 'auto' else ({'a': 'b', 'b': 'a'}[side] if not rev else side)
            twin = (tw, lid_b, h_b, s_b)
    # a piece edited symmetrically: its mirror line opened alike, the center half staying (user 2026-10-27)
    pc0 = D.piece(spec, name)
    ms, w = D.self_mirror_line(pc0, line_id)
    selfm = None
    if ms is not None and ms != line_id:
        c = D.self_axis(pc0)[0]
        selfm = (ms, {'start': 'end', 'end': 'start'}[hinge] if w == -1 else hinge,
                 side if side == 'auto' else ({'a': 'b', 'b': 'a'}[side] if w != -1 else side), c)
    sym_edit = ms == line_id and _on_center_line(pc0, line_id)
    # a center line: the piece edited symmetrically about it, or a straight line the piece is a mirror image about
    # (user 2026-11-11: Symmetric Edit off, one half opened by the whole amount, the corners 31.6 mm off mirroring)
    on_axis = sym_edit or (side == 'auto' and _mirrors_about(pc0, line_id))
    if on_axis:                                      # the center line kept where it is (below)
        axis = None
        if sym_edit:
            if not pc0.get('mirror_axis'):
                D._store_axis(pc0)
            axis = list(pc0['mirror_axis'])
        Pl = np.asarray(D.internal(pc0, line_id)['pts'], float)
        H = Pl[0] if hinge == 'start' else Pl[-1]
        dl = (Pl[-1] - Pl[0]) / np.linalg.norm(Pl[-1] - Pl[0])
    out = {name: _spread_one(spec, name, line_id, amount, mode, hinge, side, keep=[selfm[3]] if selfm else None)}
    if on_axis:
        _recenter(spec, name, axis, mode, amount, H, dl, Pl)
    if selfm:
        out[name]['more'] = [_spread_one(spec, name, selfm[0], amount, mode, selfm[1], selfm[2], keep=[selfm[3]])]
    if twin:
        out[twin[0]] = _spread_one(spec, twin[0], twin[1], amount, mode, twin[2], twin[3])
    return out


def _on_center_line(pc, line_id, tol=0.5):
    """True when internal line line_id lies on the center line of a piece edited symmetrically (within tol mm)."""
    c, u = D.self_axis(pc)
    P = np.asarray(D.internal(pc, line_id)['pts'], float) - np.asarray(c, float)
    return bool(np.abs(P[:, 0] * u[1] - P[:, 1] * u[0]).max() <= tol)


def _mirrors_about(pc, line_id, tol=0.5):
    """True when internal line line_id is straight (within tol mm) and the piece's corners mirror onto its corners
    across it (each within tol mm): the line is the piece's center line, Symmetric Edit on or not."""
    P = np.asarray(D.internal(pc, line_id)['pts'], float)
    if len(P) < 2 or np.linalg.norm(P[-1] - P[0]) < 1e-6:
        return False
    c, u = P[0], (P[-1] - P[0]) / np.linalg.norm(P[-1] - P[0])
    V = P - c
    if np.abs(V[:, 0] * u[1] - V[:, 1] * u[0]).max() > tol:
        return False
    return _asymmetry_about(D.corners(pc), c, u, worst=True) <= tol


def _asymmetry_about(C, c, u, worst=False):
    """How far corners C are from mirroring onto corners across the line through c along u (mm, summed or the
    worst)."""
    C = np.asarray(C, float)
    V = C - c
    M = c + 2.0 * (V @ u)[:, None] * u - V
    d = np.linalg.norm(M[:, None, :] - C[None], axis=-1).min(1)
    return float(d.max() if worst else d.sum())


def _asymmetry(pc):
    """How far the corners are from mirroring onto corners across the piece's center line (mm, summed)."""
    C = D.corners(pc)
    M = D.self_reflect(pc)(C)
    return float(np.linalg.norm(M[:, None, :] - C[None], axis=-1).min(1).sum())


def _recenter(spec, name, axis, mode, amount, H, dl, Pl):
    """A spread along the center line moved one half by the whole amount; turn (flare) or shift (gathers) the whole
    piece back by half of it, so both halves open alike about the center line, which stays where it was. Spread on
    a piece edited symmetrically used to tilt it: one half turned about the hinge, the other still (user
    2026-11-11)."""
    if mode == 'hinge':
        r = float(np.linalg.norm((Pl[-1] if np.allclose(H, Pl[0]) else Pl[0]) - H))
        half = np.degrees(np.arcsin(0.5 * amount / r))         # a = 2 asin(amount / 2r): half of it
        tries = [dict(deg=s * half, center=H) for s in (1.0, -1.0)]
    else:
        nrm = np.array([-dl[1], dl[0]])
        tries = [dict(shift=tuple(s * 0.5 * amount * nrm), center=H) for s in (1.0, -1.0)]
    best = None
    for kw in tries:
        sp = S.loads(S.dumps(spec))
        D.transform_piece(sp, name, **kw)
        if axis is not None:
            D.piece(sp, name)['mirror_axis'] = list(axis)
            err = _asymmetry(D.piece(sp, name))
        else:                                        # about where the line was
            err = _asymmetry_about(D.corners(D.piece(sp, name)), Pl[0], dl)
        if best is None or err < best[0]:
            best = (err, kw)
    D.transform_piece(spec, name, **best[1])
    if axis is not None:
        D.piece(spec, name)['mirror_axis'] = list(axis)


def seams_on_line(spec, name, line_id):
    return [sm['id'] for sm in spec.get('seams', []) for k in 'ab'
            if sm[k]['piece'] == name and sm[k].get('line') == line_id]
