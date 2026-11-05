# SPDX-License-Identifier: GPL-3.0-or-later
"""Darts (no bpy): find, add, move (pivot) and close (true the cap) a dart in a piece's outline.

A dart is a V in the outline: two consecutive edges, leg 1 from its mouth point to the apex and leg 2 from the apex to
its mouth point, sewn to each other from the mouth to the apex by a seam on the piece itself (as the BOM gives them:
side a = leg 1 from 0 to 1, dir +1; side b = leg 2 from 1 to 0, dir -1). The simulation sews it like any other seam.
A V drawn and sewn by hand is the same dart (find_darts: legs of several edges, sewn from the apex; normalize).
Every edit keeps the other seam ends, notches and measure points where they were (draft.point_refs).
"""
import copy
from ._i18n import rpt

import numpy as np

from . import draft as D
from . import spec as S


LEG_MIN = 12.0        # mm: a dart's legs at least two mesh cells (6 mm) long, else the seam is one stitch
IN_LINE_DEG = 1.0     # deg: two edges bent less than this at a corner taken off become one straight line


def _seam_id(spec):
    used = {sm['id'] for sm in spec.get('seams', [])}
    k = 1
    while f"U{k}" in used:
        k += 1
    return f"U{k}"


def _corner_of(ids, ref):
    """Corner index of a point (edge id, t) at an end of its edge, else None."""
    e, t = ref
    if e not in ids:
        return None
    i = ids.index(e)
    if abs(float(t)) < 1e-6:
        return i
    if abs(float(t) - 1.0) < 1e-6:
        return (i + 1) % len(ids)
    return None


def _run(ids, sd):
    """(first corner, last corner, 'from' is the first) when the side covers whole outline edges, from a corner
    counter-clockwise to a corner; else None (part of an edge, an internal line)."""
    if 'line' in sd:
        return None
    c0, c1 = _corner_of(ids, sd['from']), _corner_of(ids, sd['to'])
    if c0 is None or c1 is None or c0 == c1:
        return None
    return (c0, c1, True) if sd['dir'] > 0 else (c1, c0, False)


def find_darts(spec, name):
    """The darts of piece `name`: [{'seam', 'apex' (corner index), 'legs' (edge ids: the first edge of leg 1, the last
    of leg 2), 'mouth' (P1, P2), 'A', 'deg', 'length', 'normal', 'leg_edges', 'inner'}]. A dart: a seam on the piece
    itself whose two sides are the two arms of a V in the outline -- leg 1 from its mouth point P1 to the apex, leg 2
    from the apex to its mouth point P2, each one or more whole edges -- sewn mouth to mouth (from the mouth to the
    apex, or from the apex to the mouth). However it was made: by Add Dart, or by hand (a V drawn, its legs sewn), it
    is the same dart (user 2026-10-21). A side over part of a leg only is not a dart. normal: each leg one edge and
    sewn from the mouth (as Add Dart makes it, what the edits below work on: normalize). inner: the corners inside
    the legs (points). A seam switched off is still a dart (a dart marked, not sewn)."""
    pc = D.piece(spec, name)
    n = len(pc['edges'])
    ids = [e['id'] for e in pc['edges']]
    C = D.corners(pc)
    out = []
    for sm in spec.get('seams', []):
        if sm['a']['piece'] != name or sm['b']['piece'] != name:
            continue
        ra, rb = _run(ids, sm['a']), _run(ids, sm['b'])
        if not ra or not rb:
            continue
        for r1, r2 in ((ra, rb), (rb, ra)):
            # leg 1 runs forward into the apex, leg 2 runs out of it; both sides start at the mouth or both at the apex
            if r1[1] != r2[0] or r1[0] == r2[1] or r1[2] != (not r2[2]):
                continue
            k = r1[1]
            n1, n2 = (k - r1[0]) % n, (r2[1] - k) % n
            if n1 + n2 >= n:
                continue
            A = C[k]
            P1, P2 = C[r1[0]], C[r2[1]]
            u, v = P1 - A, P2 - A
            deg = float(np.degrees(np.arccos(np.clip(u @ v / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-12), -1, 1))))
            leg1 = [ids[(r1[0] + j) % n] for j in range(n1)]
            leg2 = [ids[(k + j) % n] for j in range(n2)]
            inner = [C[(r1[0] + j) % n] for j in range(1, n1)] + [C[(k + j) % n] for j in range(1, n2)]
            out.append({'seam': sm['id'], 'apex': k, 'legs': (leg1[0], leg2[-1]), 'mouth': (P1, P2), 'A': A,
                        'deg': deg, 'length': 0.5 * float(np.linalg.norm(u) + np.linalg.norm(v)),
                        'normal': n1 == 1 and n2 == 1 and r1[2], 'leg_edges': leg1 + leg2, 'inner': inner})
            break
    return out


def normalize(spec, name):
    """Make every dart of piece `name` as Add Dart makes it: each leg one edge (the corners inside a leg taken off,
    the shape kept), sewn from the mouth to the apex (both sides turned round: the same stitches). Returns the number
    of darts changed."""
    pc = D.piece(spec, name)
    changed = 0
    while True:
        dt = next((x for x in find_darts(spec, name) if not x['normal']), None)
        if dt is None:
            return changed
        changed += 1
        if dt['inner']:
            q = np.asarray(dt['inner'][0], float)
            k = int(np.linalg.norm(D.corners(pc) - q, axis=1).argmin())
            nn = len(pc['edges'])
            e1, e2 = pc['edges'][(k - 1) % nn], pc['edges'][k]
            u = np.asarray(e1['pts'][-1], float) - np.asarray(e1['pts'][-2], float)
            v = np.asarray(e2['pts'][1], float) - np.asarray(e2['pts'][0], float)
            bend = np.degrees(np.arccos(np.clip(u @ v / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-12), -1.0, 1.0)))
            D._join_one(spec, pc, k, keep_shape=bend > IN_LINE_DEG)
            continue
        sm = next(x for x in spec['seams'] if x['id'] == dt['seam'])
        for s in ('a', 'b'):
            sd = sm[s]
            sd['from'], sd['to'], sd['dir'] = sd['to'], sd['from'], -sd['dir']


def _keep_id(spec, old, new_id):
    """The new seam of a moved dart takes the old one's id and settings (on/off, name...): what points at the dart
    (the Seams list's selection, attachments) still finds it."""
    if new_id == old['id']:
        return old['id']
    sm = next(x for x in spec['seams'] if x['id'] == new_id)
    if any(x['id'] == old['id'] for x in spec['seams']):
        return new_id
    for k, v in old.items():
        if k not in ('a', 'b'):
            sm[k] = v
    return old['id']


def _split_mouth(spec, pc, eid, u1, u2):
    """Split edge eid at the fractions u1 < u2: returns the id of the middle edge (from u1 to u2)."""
    k = D.edge_index(pc, eid)
    id_b = D.new_edge_id(pc)
    D._split_one(spec, pc, k, u2, id_b)
    id_m = D.new_edge_id(pc)
    D._split_one(spec, pc, k, u1 / u2, id_m)
    return id_m


def _add_one(spec, name, eid, t, width, length, apex=None):
    pc = D.piece(spec, name)
    e = pc['edges'][D.edge_index(pc, eid)]
    L = S.polyline_len(e['pts'])
    u1, u2 = t - 0.5 * width / L, t + 0.5 * width / L
    if apex is None and np.hypot(length, 0.5 * width) < LEG_MIN:
        raise ValueError(rpt("the dart's legs would be shorter than {0:.0f} mm (two mesh cells)", LEG_MIN))
    if u1 * L < D.MIN_EDGE or (1 - u2) * L < D.MIN_EDGE:
        raise ValueError(rpt("the dart's mouth ({0:.0f} mm) does not fit inside edge {1} ({2:.0f} mm) clear of its corners: narrower, or split the edge", width, eid, L))
    mid = _split_mouth(spec, pc, eid, u1, u2)
    k = D.edge_index(pc, mid)
    _split_seams_at(spec, name, pc['edges'][k - 1]['id'], mid, gone=width)   # a seam across the mouth: two seams
    m1 = np.asarray(pc['edges'][k]['pts'][0], float)
    m2 = np.asarray(pc['edges'][k]['pts'][-1], float)
    d = m2 - m1
    d /= max(np.linalg.norm(d), 1e-12)
    A = 0.5 * (m1 + m2) + length * np.array([-d[1], d[0]])     # ccw outline: the inside is on the left
    if apex is not None:                                       # (shift_dart: the apex stays where it was)
        A = np.asarray(apex, float)
        if min(np.linalg.norm(A - m1), np.linalg.norm(A - m2)) < LEG_MIN:
            raise ValueError(rpt("the dart's legs would be shorter than {0:.0f} mm (two mesh cells)", LEG_MIN))
    leg1, leg2 = mid, D.new_edge_id(pc)
    prev_id, next_id = pc['edges'][k - 1]['id'], pc['edges'][(k + 1) % len(pc['edges'])]['id']
    moved = 0
    for get, put in D.point_refs(spec, name):        # points inside the mouth go to its nearer end
        e_, t_ = get()
        if e_ == mid:
            # (mid, 0) is where a seam across the mouth was split: its far part starts after the mouth
            put(*((next_id, 0.0) if t_ <= 1e-9 or t_ >= 0.5 else (prev_id, 1.0)))
            moved += 1 if 1e-9 < t_ < 1 - 1e-9 else 0
    pc['edges'][k:k + 1] = [{'id': leg1, 'pts': D._r([m1, A])}, {'id': leg2, 'pts': D._r([A, m2])}]
    sid = _seam_id(spec)
    spec['seams'].append({'id': sid, 'a': {'piece': name, 'from': [leg1, 0.0], 'to': [leg1, 1.0], 'dir': 1},
                          'b': {'piece': name, 'from': [leg2, 1.0], 'to': [leg2, 0.0], 'dir': -1},
                          'source': 'user', 'check': '', 'enabled': True})
    return sid, moved


def _self_mirror_point(spec, name, eid, t):
    """The layout point mirroring (eid, t) across the center line of piece `name` edited symmetrically, or None (not
    so edited, or the point is on the line: a dart there is its own mirror image)."""
    pc = D.piece(spec, name)
    mp = D._self_mirror_place(pc, eid, t)
    if mp is None or D._on_center(pc, eid, t, mp):
        return None
    ol = S.Outline(pc)
    return ol.at(ol.s_of(*mp))


def add_dart(spec, name, eid, t, width=20.0, length=80.0, twins=True, true=True):
    """A dart on edge `eid` of piece `name` centered at the fraction t: its mouth `width` mm along the edge, its apex
    `length` mm inside, square to the mouth (legs of equal length), sewn by a new seam. Seam ends and notches inside
    the mouth go to its nearer end. The twin gets the mirror image, and so does the other half of a piece edited
    symmetrically (user 2026-10-27). true: the mouth trued at once (true_dart: sewn, the outline runs on straight
    across it; user 2026-10-21). Returns ({piece: seam id}, points moved)."""
    tw, cmap, _ = D.twin_corner_map(spec, name) if twins else (None, None, None)
    twin_job = None
    if cmap:
        pa, pb = D.piece(spec, name), D.piece(spec, tw)
        kb, rev = D._twin_edge(pa, pb, cmap, D.edge_index(pa, eid))
        if kb is not None:
            twin_job = (pb['edges'][kb]['id'], (1.0 - t) if rev else t)
    q_self = _self_mirror_point(spec, name, eid, t)
    q_twin = _self_mirror_point(spec, tw, *twin_job) if twin_job else None
    sid, moved = _add_one(spec, name, eid, t, width, length)
    out = {name: sid}
    trues = [(name, sid, 1)]
    if twin_job:
        sid2, m2 = _add_one(spec, tw, twin_job[0], twin_job[1], width, length)
        out[tw] = sid2
        moved += m2
        trues.append((tw, sid2, 2))
    for nm, q, onto in ((name, q_self, 2), (tw, q_twin, 1)):
        if q is not None:                          # the mirror half: where that point is now (the first dart split edges)
            e2, t2 = D.nearest_on_outline(D.piece(spec, nm), q)
            s3, m3 = _add_one(spec, nm, e2, t2, width, length)
            moved += m3
            trues.append((nm, s3, onto))
    if true:
        for nm, sd, onto in trues:
            _true_quiet(spec, nm, sd, onto=onto)
    return out, moved


def _self_mirror_dart(spec, name, seam_id):
    """The seam of the dart mirroring dart seam_id across the center line of piece `name` edited symmetrically (its
    apex the mirror corner), or None."""
    smap = D.symmetric_edit(D.piece(spec, name))
    if smap is None:
        return None
    ds = find_darts(spec, name)
    dt = next((x for x in ds if x['seam'] == seam_id), None)
    if dt is None or smap[dt['apex']] == dt['apex']:
        return None
    return next((x['seam'] for x in ds if x['apex'] == smap[dt['apex']]), None)


def _true_quiet(spec, name, seam_id, onto=1):
    """_true_one, or nothing where the mouth cannot be trued (on a corner of the outline, no better shape)."""
    try:
        _true_one(spec, name, seam_id, onto)
    except (ValueError, StopIteration):
        pass


def _same_point(ol, a, b, tol=0.5):
    return abs((ol.s_of(*a) - ol.s_of(*b) + 0.5 * ol.L) % ol.L - 0.5 * ol.L) < tol


def _merge_through(spec, name, at_a, at_b=None):
    """Seams cut in two at a point of piece `name`'s outline sewn back into one: a seam whose side here ends at at_a
    ((edge, t)) and one whose side here starts at at_b (the same point, or the far end of a dart's mouth -- the other
    way round for sides run clockwise), the partner sides meeting end to start on one piece, both run the same way.
    Returns the ids merged away."""
    at_b = at_b or at_a
    ol = S.Outline(D.piece(spec, name))

    def find():
        for s1 in spec.get('seams', []):
            for k, o in (('a', 'b'), ('b', 'a')):
                x, xo = s1[k], s1[o]
                if x['piece'] != name or 'line' in x or 'line' in xo:
                    continue
                end, start = (at_a, at_b) if x.get('dir', 1) > 0 else (at_b, at_a)
                if not _same_point(ol, x['to'], end):
                    continue
                for s2 in spec['seams']:
                    if s2 is s1 or s2.get('enabled', True) != s1.get('enabled', True):
                        continue
                    for k2, o2 in (('a', 'b'), ('b', 'a')):
                        y, yo = s2[k2], s2[o2]
                        if (y['piece'] != name or 'line' in y or 'line' in yo or y.get('dir') != x.get('dir')
                                or yo['piece'] != xo['piece'] or yo.get('dir') != xo.get('dir')):
                            continue
                        if not _same_point(ol, y['from'], start):
                            continue
                        if _same_point(S.Outline(D.piece(spec, xo['piece'])), xo['to'], yo['from']):
                            return x, xo, y, yo, s2
        return None
    gone = []
    while True:
        hit = find()
        if hit is None:
            return gone
        x, xo, y, yo, s2 = hit
        x['to'], xo['to'] = list(y['to']), list(yo['to'])
        spec['seams'] = [sm for sm in spec['seams'] if sm is not s2]
        gone.append(s2['id'])


def _remove_corner_at(spec, name, q):
    """Take off the corner of piece `name` at the point q (the closed mouth a moved dart left, user 2026-10-21): the
    seams cut there sewn back into one, the two edges joined. Only the corner goes, not the cloth: two straight edges
    in line become one straight line, a bend (the mouth closed untrued) stays as a point of the joined edge (made
    straight, a rectangle's test dart lost 2673 mm2 of cloth)."""
    pc = D.piece(spec, name)
    C = D.corners(pc)
    n = len(pc['edges'])
    k = int(np.linalg.norm(C - np.asarray(q, float), axis=1).argmin())
    if np.linalg.norm(C[k] - q) > 1e-2 or n <= 3:
        return False
    e1, e2 = pc['edges'][(k - 1) % n], pc['edges'][k]
    u = np.asarray(e1['pts'][-1], float) - np.asarray(e1['pts'][-2], float)
    v = np.asarray(e2['pts'][1], float) - np.asarray(e2['pts'][0], float)
    bend = np.degrees(np.arccos(np.clip(u @ v / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-12), -1.0, 1.0)))
    _merge_through(spec, name, [e1['id'], 1.0])
    D._join_one(spec, pc, k, keep_shape=bend > IN_LINE_DEG)
    return True


def _split_seams_at(spec, name, e_in, e_out, gone=0.0):
    """Seams whose side on piece `name` runs through the corner between edges e_in (ending there) and e_out
    (starting there) become two seams there; the partner side is split at the same arc length from its start in
    proportion to the two lengths (where the two sides meet when sewn). A dart is about to open at that corner
    (gone: the mouth of a dart added there, taken out of the side's length). Returns the new seam ids."""
    ol = S.Outline(D.piece(spec, name))
    s_c = ol.s_of(e_out, 0.0)
    new = []
    for sm in list(spec.get('seams', [])):
        for k, o in (('a', 'b'), ('b', 'a')):
            sd = sm[k]
            if sd['piece'] != name or 'line' in sd:
                continue
            s0 = ol.s_of(*sd['from'])
            span = ol.span(sd)
            u = (s_c - s0) % ol.L if span > 0 else (s0 - s_c) % ol.L
            if not 1e-6 < u < abs(span) - 1e-6:
                continue
            L_k = abs(span)
            L_o = S.side_length(spec, sm[o])
            uo = u * L_o / max(L_k - gone, 1e-9)
            so = sm[o]
            if 'line' in so:
                Lline = S.polyline_len(next(it for it in D.piece(spec, so['piece'])['internals']
                                           if it['id'] == so['line'])['pts'])
                sgn = 1.0 if so['to'] >= so['from'] else -1.0
                mo = round(so['from'] + sgn * uo / max(Lline, 1e-9), 6)
                o1 = dict(so, to=mo)
                o2 = dict(so, **{'from': mo})
            else:
                mo = list(D.notch_at_arc(spec, so, uo))
                mo[1] = round(float(mo[1]), 6)
                o1 = dict(so, to=mo)
                o2 = dict(so, **{'from': list(mo)})
            here_end = [e_in, 1.0] if span > 0 else [e_out, 0.0]
            here_start = [e_out, 0.0] if span > 0 else [e_in, 1.0]
            k1 = dict(sd, to=here_end)
            k2 = dict(sd, **{'from': here_start})
            sid2 = _seam_id(spec)
            sm2 = dict(sm, id=sid2)
            sm2[k], sm2[o] = k2, o2
            sm[k], sm[o] = k1, o1
            spec['seams'].append(sm2)
            new.append(sid2)
            break
    return new


# ---------------------------------------------------------------- moving a dart (pivot about its apex)

def _rot(phi, A):
    c, s = np.cos(phi), np.sin(phi)
    R = np.array([[c, -s], [s, c]])

    def f(P):
        P = np.asarray(P, float).reshape(-1, 2)
        return (P - A) @ R.T + A
    return f


def _inside(poly, P):
    P = np.atleast_2d(P)
    x, y = P[:, 0][:, None], P[:, 1][:, None]
    ax, ay = poly[:, 0][None], poly[:, 1][None]
    bx, by = np.roll(poly[:, 0], -1)[None], np.roll(poly[:, 1], -1)[None]
    hit = ((ay > y) != (by > y)) & (x < (bx - ax) * (y - ay) / np.where(by == ay, 1e-12, by - ay) + ax)
    return hit.sum(1) % 2 == 1


def _move_one(spec, name, seam_id, eq, tq, side):
    """Pivot dart `seam_id` of piece `name` to the point (eq, tq) of its outline, turning the outline between the
    dart and that point on `side` ('after': from the end of leg 2 on, 'before': back from the start of leg 1).
    Returns (the new seam id, internal lines turned)."""
    pc = D.piece(spec, name)
    dt = next((x for x in find_darts(spec, name) if x['seam'] == seam_id), None)
    if dt is None:
        raise ValueError(rpt("{0} is not a dart of {1}", seam_id, name))
    l1, l2 = dt['legs']
    if eq in (l1, l2):
        raise ValueError(rpt("the new place is on the dart itself"))
    A, (P1, P2) = dt['A'], dt['mouth']
    qa = D.new_edge_id(pc)
    k = D.edge_index(pc, eq)
    Lq = S.polyline_len(pc['edges'][k]['pts'])
    if tq * Lq < D.MIN_EDGE or (1 - tq) * Lq < D.MIN_EDGE:
        raise ValueError(rpt("the new place is too close to a corner"))
    D._split_one(spec, pc, k, tq, qa)              # edge eq = start..Q, qa = Q..end
    _split_seams_at(spec, name, eq, qa)            # a seam through Q becomes two, one each side of the new dart
    ids = [e['id'] for e in pc['edges']]
    n = len(ids)
    i1, i2 = ids.index(l1), ids.index(l2)          # i2 = i1 + 1 (mod n)
    moving = []
    if side == 'after':                            # turn the end of leg 2 onto the start of leg 1
        j = (i2 + 1) % n
        while ids[j] != qa:
            moving.append(j)
            j = (j + 1) % n
        u, v = P2 - A, P1 - A
    else:                                          # turn the start of leg 1 onto the end of leg 2
        j = (i1 - 1) % n
        while ids[j] != eq:                        # back to Q..end (qa) included, start..Q (eq) not
            moving.append(j)
            j = (j - 1) % n
        moving = moving[::-1]
        u, v = P1 - A, P2 - A
    if not moving:
        raise ValueError(rpt("nothing between the dart and the new place"))
    phi = np.arctan2(u[0] * v[1] - u[1] * v[0], u @ v)
    f = _rot(phi, A)
    # the part that turns is bounded by the cut A -> Q, the leg and the outline between: internal lines in it turn
    ring = [A] + [np.asarray(p, float) for j in moving for p in pc['edges'][j]['pts'][:-1]] + \
        [np.asarray(pc['edges'][moving[-1]]['pts'][-1], float)]
    poly = np.array(ring)
    turned = 0
    for it in pc.get('internals', []):
        P = np.asarray(it['pts'], float)
        if len(P) and _inside(poly, P).mean() > 0.5:
            D.xform_internal(it, f)
            turned += 1
    pl = spec.get('placement', {}).get(name)
    if pl and 'anchor' in pl and _inside(poly, pl['anchor'])[0]:
        pl['anchor'] = D._r(f(pl['anchor']))[0]
    for j in moving:
        e = pc['edges'][j]
        e['pts'] = D._r(f(e['pts']))
        if e.get('ctrl'):
            e['ctrl'] = D._r(f(e['ctrl']))
    # the old legs go and the mouth closes into one corner; points held on the legs go to that corner
    keep_corner = P1 if side == 'after' else P2
    before_dart = ids[(i1 - 1) % n]
    for get, put in D.point_refs(spec, name):
        e_, t_ = get()
        if e_ in (l1, l2):
            put(before_dart, 1.0)
    spec['seams'] = [sm for sm in spec['seams'] if sm['id'] != seam_id]
    pc['edges'] = [e for e in pc['edges'] if e['id'] not in (l1, l2)]
    kc = D._r([keep_corner])[0]
    for e in pc['edges']:                          # the closed mouth: exactly one point (rounding)
        if np.linalg.norm(np.asarray(e['pts'][0], float) - keep_corner) < 1e-3:
            e['pts'][0] = kc
        if np.linalg.norm(np.asarray(e['pts'][-1], float) - keep_corner) < 1e-3:
            e['pts'][-1] = kc
    # open the dart at Q: leg 1 from the end of the edge before Q (turned or not) to A, leg 2 from A to Q
    ids = [e['id'] for e in pc['edges']]
    kq = ids.index(qa)
    q_prev = np.asarray(pc['edges'][kq - 1]['pts'][-1], float)
    q_next = np.asarray(pc['edges'][kq]['pts'][0], float)
    n1 = D.new_edge_id(pc)
    pc['edges'].insert(kq, {'id': n1, 'pts': D._r([q_prev, A])})
    n2 = D.new_edge_id(pc)
    pc['edges'].insert(kq + 1, {'id': n2, 'pts': D._r([A, q_next])})
    sid = _seam_id(spec)
    spec['seams'].append({'id': sid, 'a': {'piece': name, 'from': [n1, 0.0], 'to': [n1, 1.0], 'dir': 1},
                          'b': {'piece': name, 'from': [n2, 1.0], 'to': [n2, 0.0], 'dir': -1},
                          'source': 'user', 'check': '', 'enabled': True})
    bm = back_map(A, phi, f(poly))
    # the closed mouth is no corner (user 2026-10-21, B-3): moved again and again, the corners it left cut the
    # outline and its seams into many short pieces
    _remove_corner_at(spec, name, kc)
    return sid, turned, bm


def _nearest(pc, q):
    """(edge id, t) of the outline point of piece pc nearest q, and the distance."""
    ol = S.Outline(pc)
    B = np.vstack([ol.B, ol.B[:1]])
    A0, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((np.asarray(q, float) - A0) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    d = np.linalg.norm(A0 + t[:, None] * Dd - q, axis=1)
    j = int(d.argmin())
    return ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j])), float(d[j])


def _shift_one(spec, name, seam_id, eq, tq, apex_follows=False):
    """Slide dart `seam_id` of piece `name` along the line it sits on to the point (eq, tq) (user 2026-10-21, B-2):
    the dart is taken out (its mouth sewn shut into the line again, the seams it cut sewn back), and made again there,
    as wide, its apex where it was (apex_follows: moved as far as the mouth). The rest of the outline stays. Only on
    the two edges either side of the dart. Returns the new seam id."""
    pc = D.piece(spec, name)
    dt = next((x for x in find_darts(spec, name) if x['seam'] == seam_id), None)
    if dt is None:
        raise ValueError(rpt("{0} is not a dart of {1}", seam_id, name))
    l1, l2 = dt['legs']
    ids = [e['id'] for e in pc['edges']]
    n = len(ids)
    i1, i2 = ids.index(l1), ids.index(l2)
    e_prev, e_next = ids[(i1 - 1) % n], ids[(i2 + 1) % n]
    if eq not in (e_prev, e_next):
        raise ValueError(rpt("Shift slides the dart along its own line: pick a point either side of it (Pivot moves it "
                         "elsewhere)"))
    ol = S.Outline(pc)
    Q = ol.at(ol.s_of(eq, tq))
    A, (P1, P2) = np.asarray(dt['A'], float), dt['mouth']
    width = float(np.linalg.norm(P2 - P1))
    mid0 = 0.5 * (P1 + P2)
    # take the dart out: the legs become one edge across the mouth, points on them go to its ends
    for get, put in D.point_refs(spec, name):
        e_, t_ = get()
        if e_ == l1:
            put(l1, 0.0)
        elif e_ == l2:
            put(l1, 1.0)
    spec['seams'] = [sm for sm in spec['seams'] if sm['id'] != seam_id]
    pc['edges'][i1] = {'id': l1, 'pts': D._r([P1, P2])}
    del pc['edges'][i2]
    # the seams the dart cut: sewn back across the mouth, then its two corners taken off
    _merge_through(spec, name, [l1, 0.0], [l1, 1.0])
    k2 = [e['id'] for e in pc['edges']].index(l1) + 1
    D._join_one(spec, pc, k2 % len(pc['edges']))         # the mouth's far corner
    k1 = [e['id'] for e in pc['edges']].index(e_prev) + 1
    D._join_one(spec, pc, k1 % len(pc['edges']))         # its near corner
    (e_new, t_new), _ = _nearest(pc, Q)
    apex = A + (Q - mid0) if apex_follows else A
    sid, _ = _add_one(spec, name, e_new, t_new, width, float(np.linalg.norm(A - mid0)), apex=apex)
    return sid


ON_MM = 0.5           # a point this close to the turned part's outline (or its new leg) belongs to it


def _near_polyline(L, P, tol):
    A, Dd = L[:-1], np.diff(L, axis=0)
    ll = np.maximum((Dd * Dd).sum(1), 1e-12)
    out = np.zeros(len(P), bool)
    for k0 in range(0, len(P), 512):
        q = P[k0:k0 + 512]
        t = np.clip(((q[:, None, :] - A[None]) * Dd[None]).sum(-1) / ll[None], 0, 1)
        d = np.linalg.norm(A[None] + t[..., None] * Dd[None] - q[:, None, :], axis=-1)
        out[k0:k0 + 512] = d.min(1) <= tol
    return out


def back_map(A, phi, turned_poly):
    """new layout -> old for a pivoted piece (core/transfer.back_map): points in the turned part (its outline as it is
    now: the apex, then the turned outline) turn back by -phi about A, the rest stay. A point on the turned outline
    or on the new leg from its end to the apex is in the turned part."""
    g = _rot(-phi, A)
    poly = np.asarray(turned_poly, float)
    edge = np.vstack([poly[1:], poly[:1]])         # the turned outline, then back to the apex (the new leg)

    def fn(P):
        P = np.asarray(P, float).reshape(-1, 2)
        out = P.copy()
        m = _inside(poly, P) | _near_polyline(edge, P, ON_MM)
        if m.any():
            out[m] = g(P[m])
        return out
    return fn


def _sector(spec, name, dt, eq, tq, side):
    """The outline polygon (with the apex) of the part that would turn for `side`."""
    pc = D.piece(spec, name)
    ol = S.Outline(pc)
    s_q = ol.s_of(eq, tq)
    if side == 'after':
        s0, s1 = ol.s_of(dt['legs'][1], 1.0), s_q
    else:
        s0, s1 = s_q, ol.s_of(dt['legs'][0], 0.0)
    span = (s1 - s0) % ol.L
    P = ol.at(s0 + np.linspace(0.0, span, max(8, int(span / 5.0))))
    return np.vstack([dt['A'][None], P])


def _auto_side(spec, name, dt, eq, tq):
    """The side whose turning moves neither the placement anchor nor the grain line (baseline internals); the
    shorter if both or neither do."""
    pc = D.piece(spec, name)
    marks = []
    pl = spec.get('placement', {}).get(name)
    if pl and 'anchor' in pl:
        marks.append(np.asarray(pl['anchor'], float))
    for it in pc.get('internals', []):
        if it.get('kind') == 'baseline' and len(it['pts']):
            marks.append(np.asarray(it['pts'], float).mean(0))
    a, b = _arcs(spec, name, dt, eq, tq)
    short = 'after' if a <= b else 'before'
    if not marks:
        return short
    M = np.array(marks)
    hit = {sd: int(_inside(_sector(spec, name, dt, eq, tq, sd), M).sum()) for sd in ('after', 'before')}
    if hit['after'] < hit['before']:
        return 'after'
    if hit['before'] < hit['after']:
        return 'before'
    return short


def _arcs(spec, name, dt, eq, tq):
    """Outline length from the end of leg 2 forward to (eq, tq), and from (eq, tq) forward to the start of leg 1."""
    ol = S.Outline(D.piece(spec, name))
    s_q = ol.s_of(eq, tq)
    return (s_q - ol.s_of(dt['legs'][1], 1.0)) % ol.L, (ol.s_of(dt['legs'][0], 0.0) - s_q) % ol.L


def move_dart(spec, name, seam_id, eq, tq, side='auto', twins=True, mode='pivot', true=True, apex_follows=False):
    """Move dart `seam_id` of piece `name` to the point (eq, tq) of its outline by pivoting about its apex: the outline
    between the dart and the new place turns by the dart's angle (the dart closes where it was and opens at the new
    place: same apex, same angle). side: 'after' turns the part after the dart, 'before' the part before it, 'short'
    the shorter of the two, 'auto' the one without the placement anchor and the grain line (else the shorter). A seam
    through the new place becomes two. Internal lines inside the turned part turn with it; the twin gets the mirror
    image (the mirrored side). Returns ({piece: new seam id}, side used, internal lines turned, {piece: new -> old
    layout map for the carry to 3D}). mode 'shift': slide it along its own line instead (_shift_one; side is then
    'shift'). true: the new mouth trued at once (true_dart, user 2026-10-21 B-4). The dart keeps its seam's id and
    settings (on/off). A dart made by hand is made normal first (normalize: its legs one edge each)."""
    dt = next((x for x in find_darts(spec, name) if x['seam'] == seam_id), None)
    if dt is None:
        raise ValueError(rpt("{0} is not a dart of {1}", seam_id, name))
    if eq in dt['leg_edges']:
        raise ValueError(rpt("the new place is on the dart itself"))
    olds = {name: copy.deepcopy(next(x for x in spec['seams'] if x['id'] == seam_id))}
    normalize(spec, name)
    if twins and D.twin_of(spec, name):
        normalize(spec, D.twin_of(spec, name))
    dt = next(x for x in find_darts(spec, name) if x['seam'] == seam_id)
    if mode == 'shift':
        side = 'shift'
    elif side == 'auto':
        side = _auto_side(spec, name, dt, eq, tq)
    elif side == 'short':
        a, b = _arcs(spec, name, dt, eq, tq)
        side = 'after' if a <= b else 'before'
    twin = None
    tw, cmap, _ = D.twin_corner_map(spec, name) if twins else (None, None, None)
    if cmap:
        pa, pb = D.piece(spec, name), D.piece(spec, tw)
        kb, rev = D._twin_edge(pa, pb, cmap, D.edge_index(pa, eq))
        dtb = next((x for x in find_darts(spec, tw) if x['apex'] == cmap[dt['apex']]), None)
        if kb is not None and dtb is not None:
            olds[tw] = copy.deepcopy(next(x for x in spec['seams'] if x['id'] == dtb['seam']))
            twin = (tw, dtb['seam'], pb['edges'][kb]['id'], (1.0 - tq) if rev else tq,
                    {'after': 'before', 'before': 'after', 'shift': 'shift'}[side])   # mirrored: the outline runs the other way
    # the other half of a piece edited symmetrically: its mirror dart moved to the mirror place (user 2026-10-27)
    mirror = None
    ms = _self_mirror_dart(spec, name, seam_id)
    if ms is not None:
        q = _self_mirror_point(spec, name, eq, tq)
        if q is not None:
            mirror = (ms, q, copy.deepcopy(next(x for x in spec['seams'] if x['id'] == ms)))
    if mode == 'shift':
        same = (lambda P: np.asarray(P, float).reshape(-1, 2))     # nothing turns: every point stays
        out, maps, turned = {name: _shift_one(spec, name, seam_id, eq, tq, apex_follows)}, {name: same}, 0
        if twin:
            out[twin[0]], maps[twin[0]] = _shift_one(spec, *twin[:4], apex_follows), same
    else:
        sid, turned, fn = _move_one(spec, name, seam_id, eq, tq, side)
        out, maps = {name: sid}, {name: fn}
        if twin:
            sid_b, _, fn_b = _move_one(spec, *twin)
            out[twin[0]], maps[twin[0]] = sid_b, fn_b
    out = {nm: _keep_id(spec, olds[nm], sd) for nm, sd in out.items()}
    trues = [(nm, sd, 1 if i == 0 else 2) for i, (nm, sd) in enumerate(out.items())]
    if mirror:
        ms, q, old_m = mirror
        e2, t2 = D.nearest_on_outline(D.piece(spec, name), q)
        if mode == 'shift':
            sm = _shift_one(spec, name, ms, e2, t2, apex_follows)
        else:
            sm, _, fn2 = _move_one(spec, name, ms, e2, t2, {'after': 'before', 'before': 'after'}[side])
            fn1 = maps[name]
            maps[name] = (lambda P, fn1=fn1, fn2=fn2: fn1(fn2(P)))     # new -> old through both moves
        trues.append((name, _keep_id(spec, old_m, sm), 2))
    if true:
        for nm, sd, onto in trues:
            _true_quiet(spec, nm, sd, onto=onto)
    return out, side, turned, maps


# ---------------------------------------------------------------- closing a dart: truing its mouth

TANGENT_MM = 3.0      # the outline's direction at a mouth corner: over this length
CORNER_KINK = 30.0    # deg: a folded mouth bent more than this sits on a corner of the outline, not a line to true


def fold_kink(spec, name, seam_id, onto=1):
    """Degrees between the outline coming into the dart's mouth and the outline leaving it, with the dart folded shut
    (onto=1: leg 2 onto leg 1; 2: the other way). 0 = the sewn line runs straight across."""
    pc = D.piece(spec, name)
    dt = next(x for x in find_darts(spec, name) if x['seam'] == seam_id)
    ol = S.Outline(pc)
    A, (P1, P2) = dt['A'], dt['mouth']
    s1, s2 = ol.s_of(dt['legs'][0], 0.0), ol.s_of(dt['legs'][1], 1.0)
    tin = P1 - ol.at(s1 - TANGENT_MM)
    tout = ol.at(s2 + TANGENT_MM) - P2
    u, v = (P2 - A, P1 - A) if onto == 1 else (P1 - A, P2 - A)
    phi = np.arctan2(u[0] * v[1] - u[1] * v[0], u @ v)
    c, sn = np.cos(phi), np.sin(phi)
    R = np.array([[c, -sn], [sn, c]])
    if onto == 1:
        tout = R @ tout
    else:
        tin = R @ tin
    ca = tin @ tout / max(np.linalg.norm(tin) * np.linalg.norm(tout), 1e-12)
    return float(np.degrees(np.arccos(np.clip(ca, -1, 1))))


def _with_mouth(spec, name, dt, r):
    """A copy of spec (piece `name` and the seams copied) with the dart's mouth corners r mm from the apex."""
    import copy
    trial = dict(spec)
    trial['pieces'] = [copy.deepcopy(p) if p['name'] == name else p for p in spec['pieces']]
    trial['seams'] = copy.deepcopy(spec.get('seams', []))
    if spec.get('measure'):
        trial['measure'] = copy.deepcopy(spec['measure'])
    _set_mouth(trial, name, dt, r)
    return trial


def _set_mouth(spec, name, dt, r):
    pc = D.piece(spec, name)
    ids = [e['id'] for e in pc['edges']]
    n = len(ids)
    i1, i2 = ids.index(dt['legs'][0]), ids.index(dt['legs'][1])
    A, (P1, P2) = dt['A'], dt['mouth']
    X1 = A + r * (P1 - A) / max(np.linalg.norm(P1 - A), 1e-12)
    X2 = A + r * (P2 - A) / max(np.linalg.norm(P2 - A), 1e-12)
    D.move_corners(spec, name, {i1: tuple(X1 - P1), (i2 + 1) % n: tuple(X2 - P2)}, twins=False)
    return float(np.linalg.norm(X1 - P1)), float(np.linalg.norm(X2 - P2))


def _true_one(spec, name, seam_id, onto=1):
    """Move the mouth corners along the legs (both at the same distance r from the apex) to the r where the folded
    outline runs on straightest (draft.move_corners: the edges either side follow, keeping their shape). Returns
    (corner moves mm, r, kink before, kink after)."""
    dt = next((x for x in find_darts(spec, name) if x['seam'] == seam_id), None)
    if dt is None:
        raise ValueError(rpt("{0} is not a dart of {1}", seam_id, name))
    pc = D.piece(spec, name)
    ids = [e['id'] for e in pc['edges']]
    lo = max(LEG_MIN, 0.5 * dt['length'])
    hi = 1.5 * dt['length']
    # the edges either side must keep a length: the corners cannot pass their far ends
    k0 = fold_kink(spec, name, seam_id, onto)
    if k0 > CORNER_KINK:
        raise ValueError(rpt("the dart's mouth sits on a corner of the outline (folded, it bends {0:.0f} deg): nothing to true there", k0))

    def kink(r, check):
        try:
            tr = _with_mouth(spec, name, dt, r)
            if check and D.problems(tr, [name]):
                return 1e9
            return fold_kink(tr, name, seam_id, onto)
        except Exception:
            return 1e9

    def best(check):
        rs = np.linspace(lo, hi, 25)
        ks = [kink(r, check) for r in rs]
        j = int(np.argmin(ks))
        a, b = rs[max(j - 1, 0)], rs[min(j + 1, len(rs) - 1)]
        g = (np.sqrt(5) - 1) / 2                       # golden section around the best sample
        c, d = b - g * (b - a), a + g * (b - a)
        fc, fd = kink(c, check), kink(d, check)
        for _ in range(16):                            # the bracket 1/1000 of what it was: well under 0.1 mm here
            if fc < fd:
                b, d, fd = d, c, fc
                c = b - g * (b - a)
                fc = kink(c, check)
            else:
                a, c, fc = c, d, fd
                d = a + g * (b - a)
                fd = kink(d, check)
        return 0.5 * (a + b)
    # the outline checks (D.problems) are most of the time (Move Dart's preview runs this at every mouse step): search
    # by the kink alone, check the one found, and search again with the checks only when that one is bad
    r = best(False)
    k1 = kink(r, True)
    if k1 >= 1e9:
        r = best(True)
        k1 = kink(r, True)
    if k1 >= k0:                                       # nothing better than as it is
        return (0.0, 0.0), dt['length'], k0, k0
    mv = _set_mouth(spec, name, dt, r)
    return mv, r, k0, k1


def true_dart(spec, name, seam_id, twins=True):
    """Close the dart to true its mouth: folded shut (leg 2 onto leg 1 about the apex), the outline should run on across
    the mouth without a kink. The two mouth corners move along their legs (both legs keep the same length) to where the
    folded outline runs on straightest. The twin gets the mirror image (folded the other way). Returns
    ({piece: seam id}, report line)."""
    normalize(spec, name)
    if twins and D.twin_of(spec, name):
        normalize(spec, D.twin_of(spec, name))
    tw, cmap, _ = D.twin_corner_map(spec, name) if twins else (None, None, None)
    twin_seam = None
    if cmap:
        dt = next((x for x in find_darts(spec, name) if x['seam'] == seam_id), None)
        dtb = next((x for x in find_darts(spec, tw) if dt and x['apex'] == cmap[dt['apex']]), None)
        twin_seam = dtb['seam'] if dtb else None
    ms = _self_mirror_dart(spec, name, seam_id)     # the other half of a piece edited symmetrically (2026-10-27)
    (a, b), r, k0, k1 = _true_one(spec, name, seam_id, onto=1)
    if ms is not None:
        _true_quiet(spec, name, ms, onto=2)
    out = {name: seam_id}
    if twin_seam:
        _true_one(spec, tw, twin_seam, onto=2)          # mirrored: pressed the other way
        out[tw] = twin_seam
    return out, (f"Dart {seam_id} trued: kink {k0:.1f} -> {k1:.1f} deg, mouth corners moved {a:.1f} / {b:.1f} mm, "
                 f"legs {r:.1f} mm")
