# SPDX-License-Identifier: GPL-3.0-or-later
"""Cut a piece in two along an internal line (no bpy): the line, drawn from outline to outline, becomes an edge of
each half, and a new seam sews the two edges back together (user 2026-10-21: "more important than the darts").

Nothing moves: the two halves keep every point where it was drawn, so the garment hangs as before (the carry to 3D
lays each half on the cloth it was part of, transfer.carry's `sources`). A dart turns into a style line this way:
draw a line from the dart's apex to another edge and cut; the dart's own seam then joins the two halves too.

The half with the placement anchor and the grain line (else the larger) keeps the piece's name; the other is a new
piece with the same fabric, role and settings, placed by the same numbers. Seams, notches and measure points go with
the edge they are on (a seam running across a cut end becomes two there, dart._split_seams_at), internal lines with
the half they are in (a grain line across the cut goes to both, any other line across it refuses the cut). The twin
is cut along the mirror image, and the two new halves are twins too.
"""
import copy
from ._i18n import rpt

import numpy as np

from . import dart as DT
from . import draft as D
from . import spec as S
from . import spread as SP

ON_LINE_MM = 0.5      # an internal line's point this close to the cut line is on it (either half)


def _seam_side_arc(ol, sd, arc_ids):
    """'a' or 'b': the half an outline seam side lies in, by its middle."""
    s0 = ol.s_of(*sd['from'])
    e, _ = ol.edge_t((s0 + 0.5 * ol.span(sd)) % ol.L)
    return 'a' if e in arc_ids['a'] else 'b'


def _near_line(P, Q, tol):
    """Points of Q within tol of the polyline P."""
    A, B = P[:-1], P[1:]
    Dd = B - A
    ll = np.maximum((Dd * Dd).sum(1), 1e-12)
    t = np.clip(((Q[:, None, :] - A[None]) * Dd[None]).sum(2) / ll[None], 0, 1)
    d = np.linalg.norm(A[None] + t[..., None] * Dd[None] - Q[:, None, :], axis=2)
    return d.min(1) < tol


def _cut_edges(it, P, used):
    """The cut line as outline edges from its start to its end: one per span between the points the line was drawn
    through (a V cut gives its tip as a corner: as one edge, the tip followed the mouth's corners as a stretched curve
    and swung 53 mm sideways when one of them moved 20 mm, user 2026-10-28), a curved span a curved edge. A line of
    many points with no points of its own (a traced or imported line) stays one edge."""
    from . import iline as IL
    N, C, _, own = IL.shape(it)
    ids = (f"C{i}" for i in range(1, 100000) if f"C{i}" not in used)
    if not (own or len(N) <= IL.DENSE) or len(N) < 3:
        return [{'id': next(ids), 'pts': D._r(P)}]
    out = []
    for k in range(len(N) - 1):
        e = {'id': next(ids), 'pts': D._r([N[k], N[k + 1]])}
        if C[k] is not None:
            e['ctrl'] = D._r(np.asarray(C[k], float))
            D.refresh_curve(e)
        out.append(e)
    return out


def _cut_one(spec, name, line_id, new_name):
    pc = D.piece(spec, name)
    it = D.internal(pc, line_id)
    if it.get('closed'):
        raise ValueError(rpt("cut along an open line drawn from outline to outline, not a closed shape"))
    P = np.asarray(it['pts'], float)
    E0, E1 = P[0], P[-1]
    Lp = S.polyline_len(P)
    W = S.resample(P, max(3, int(Lp / 2.0) + 1))
    inner = W[(np.linalg.norm(W - E0, axis=1) > 2.0) & (np.linalg.norm(W - E1, axis=1) > 2.0)]
    if len(inner) and not DT._inside(S.Outline(pc).B, inner).all():
        raise ValueError(rpt("the cut line runs outside the piece: draw it inside, from outline to outline"))
    SP._corner_at(spec, name, E0)
    SP._corner_at(spec, name, E1)
    pc = D.piece(spec, name)
    C = D.corners(pc)
    k0 = int(np.linalg.norm(C - E0, axis=1).argmin())
    k1 = int(np.linalg.norm(C - E1, axis=1).argmin())
    if k0 == k1:
        raise ValueError(rpt("the cut line's ends are on the same point of the outline"))
    n = len(pc['edges'])
    # a seam running through a cut end becomes two there
    for k in (k0, k1):
        DT._split_seams_at(spec, name, pc['edges'][(k - 1) % n]['id'], pc['edges'][k]['id'])
    arcs = {'a': SP._arc(n, k0, k1), 'b': SP._arc(n, k1, k0)}
    regions = {'a': np.vstack([SP._arc_pts(pc, arcs['a']), P[::-1][1:-1]]),
               'b': np.vstack([SP._arc_pts(pc, arcs['b']), P[1:-1]])}
    for k in 'ab':
        if D.signed_area(regions[k]) <= 0:
            raise ValueError(rpt("the cut line leaves the piece: draw it inside, from outline to outline"))
    # the half that keeps the name: the one with the anchor and the grain line, else the larger
    M = SP._marks(spec, name)
    hit = {k: int(DT._inside(regions[k], M).sum()) if len(M) else 0 for k in 'ab'}
    if hit['a'] != hit['b']:
        keep = 'a' if hit['a'] > hit['b'] else 'b'
    else:
        keep = 'a' if D.signed_area(regions['a']) >= D.signed_area(regions['b']) else 'b'
    other = 'b' if keep == 'a' else 'a'
    arc_ids = {k: {pc['edges'][j]['id'] for j in arcs[k]} for k in 'ab'}
    ol = S.Outline(pc)
    # every outline seam side on this piece: which half (before the edges are re-made)
    side_half = []
    for sm in spec.get('seams', []):
        for k in ('a', 'b'):
            sd = sm[k]
            if sd['piece'] == name and 'line' not in sd:
                side_half.append((sd, _seam_side_arc(ol, sd, arc_ids)))
    # internal lines: which half
    line_half = {}
    for jt in pc.get('internals', []):
        if jt['id'] == line_id:
            continue
        Q = np.asarray(jt['pts'], float)
        if not len(Q):
            line_half[jt['id']] = keep
            continue
        if len(Q) > 1 and not jt.get('closed'):
            # its inner points and the middles of its segments: the ends of a line drawn from outline to outline lie
            # on the outline, either half (a symmetric piece's center line, 2026-10-27)
            Q = np.vstack([Q[1:-1], 0.5 * (Q[:-1] + Q[1:])])
        free = ~_near_line(P, Q, ON_LINE_MM)
        ins = DT._inside(regions[keep], Q[free]) if free.any() else np.ones(1, bool)
        if ins.all():
            line_half[jt['id']] = keep
        elif not ins.any():
            line_half[jt['id']] = other
        elif jt.get('kind') == 'baseline':
            line_half[jt['id']] = 'both'
        else:
            raise ValueError(rpt("internal line {0} crosses the cut line: cut it first or remove it", jt['id']))
    # the two outlines: each half's edges, closed by the cut line
    edges_of = {k: [copy.deepcopy(pc['edges'][j]) for j in arcs[k]] for k in 'ab'}
    used = {e['id'] for e in pc['edges']}
    chain = _cut_edges(it, P, used)                               # E0 -> E1, one edge per span of the line
    cid = chain[0]['id']
    for e in reversed(chain):                                     # half a: back from E1 to E0
        ea = {'id': e['id'], 'pts': e['pts'][::-1]}
        if e.get('ctrl'):
            ea['ctrl'] = e['ctrl'][::-1]
            D.refresh_curve(ea)
        edges_of['a'].append(ea)
    edges_of['b'].extend(copy.deepcopy(chain))                    # half b: on from E0 to E1
    new_pc = {k: v for k, v in pc.items() if k not in ('edges', 'internals', 'notches', 'name', 'bom', 'label')}
    new_pc['name'] = new_name
    if pc.get('label'):
        new_pc['label'] = pc['label'] + " 2"
    new_pc['edges'] = edges_of[other]
    new_pc['internals'] = [copy.deepcopy(jt) for jt in pc.get('internals', [])
                           if line_half.get(jt['id']) in (other, 'both')]
    notches = pc.get('notches') or []
    keep_notches = [x for x in notches if x['edge'] in arc_ids[keep]]
    new_notches = [x for x in notches if x['edge'] in arc_ids[other]]
    if new_notches:
        new_pc['notches'] = new_notches
    pc['edges'] = edges_of[keep]
    pc['internals'] = [jt for jt in pc.get('internals', []) if jt['id'] != line_id
                       and line_half.get(jt['id']) in (keep, 'both')]
    if notches:
        pc['notches'] = keep_notches
    spec['pieces'].insert(spec['pieces'].index(pc) + 1, new_pc)
    # outline seam sides: to the half they lie in; an end on a cut corner named by the other half's edge is named
    # again on its own half's outline
    halves = {keep: pc, other: new_pc}
    for sd, h in side_half:
        tgt = halves[h]
        sd['piece'] = tgt['name']
        ids = {e['id'] for e in tgt['edges']}
        for end in ('from', 'to'):
            if sd[end][0] not in ids:
                q = ol.at(ol.s_of(*sd[end]))
                (e, t), _ = SP._outline_point(tgt, q)
                sd[end] = [e, round(float(t), 6)]
    for sm in spec.get('seams', []):                 # seams on the internal lines that went to the new half
        for k in ('a', 'b'):
            sd = sm[k]
            if sd['piece'] == name and 'line' in sd and line_half.get(sd['line']) == other:
                sd['piece'] = new_name
    for f in spec.get('folds', []):
        if f.get('piece') == name and line_half.get(f.get('line')) == other:
            f['piece'] = new_name
    spec['folds'] = [f for f in spec.get('folds', []) if not (f.get('piece') == name and f.get('line') == line_id)]
    for m in (spec.get('measure') or {}).get('pattern') or []:
        for k in ('a', 'b'):
            q = m.get(k)
            if isinstance(q, dict) and q.get('piece') == name and q.get('edge') in arc_ids[other]:
                q['piece'] = new_name
    for el in spec.get('elastic', []):
        if el.get('piece') == name and line_half.get(el.get('line')) == other:
            el['piece'] = new_name
    pl = spec.get('placement', {}).get(name)
    if pl is not None:                               # the same numbers: the half lands where it was
        spec['placement'][new_name] = copy.deepcopy(pl)
    # the cut line sewn back: from E1 on half a's cut edge (its start) and on half b's (its end)
    a_name, b_name = halves['a']['name'], halves['b']['name']
    sid = DT._seam_id(spec)
    last = chain[-1]['id']
    spec['seams'].append({'id': sid, 'a': {'piece': a_name, 'from': [last, 0.0], 'to': [cid, 1.0], 'dir': 1},
                          'b': {'piece': b_name, 'from': [last, 1.0], 'to': [cid, 0.0], 'dir': -1},
                          'source': 'user', 'check': '', 'enabled': True})
    return {'new': new_name, 'seam': sid, 'kept': keep}


def cut(spec, name, line_id, twins=True):
    """Cut piece `name` in two along its internal line `line_id` (drawn from outline to outline). Returns
    {piece: {'new': the new half's name, 'seam': the seam sewing the cut, 'kept': 'a' / 'b'}} (the twin too). A piece
    edited symmetrically is cut along the line's mirror image too (that cut in the first entry's 'more': [(piece cut,
    {...})]); cut on its center line, it gives two pieces that are not a pair (user 2026-10-27, b-4). A half no
    longer symmetric is no longer edited symmetrically."""
    if SP.seams_on_line(spec, name, line_id):
        raise ValueError(rpt("the line is sewn: cut along a line that is not part of a seam"))
    twin = None
    if twins:
        tw, lid_b, _ = D._twin_internal(spec, name, line_id)
        if tw:
            twin = (tw, lid_b)
    ms = D.self_mirror_line(D.piece(spec, name), line_id)[0]
    new_a = D.unique_name(spec, name + "_2")
    out = {name: _cut_one(spec, name, line_id, new_a)}
    if ms is not None and ms != line_id:
        holder = next(n for n in (name, new_a) if any(it['id'] == ms for it in D.piece(spec, n).get('internals', [])))
        new_c = D.unique_name(spec, name + "_3")
        out[name]['more'] = [(holder, _cut_one(spec, holder, ms, new_c))]
        pn = D.piece(spec, new_c)
        pn.pop('family', None)
        pn.pop('mirrored', None)
    if twin:
        new_b = D.unique_name(spec, twin[0] + "_2")
        out[twin[0]] = _cut_one(spec, twin[0], twin[1], new_b)
        pa, pb = D.piece(spec, new_a), D.piece(spec, new_b)
        pa['family'] = pb['family'] = new_a          # the two new halves are twins (one family, one mirrored)
        pa['mirrored'] = bool(D.piece(spec, name).get('mirrored'))
        pb['mirrored'] = not pa['mirrored']
    else:
        pn = D.piece(spec, new_a)
        pn.pop('family', None)
        pn.pop('mirrored', None)
    for v in [out[name]] + [i for _, i in out[name].get('more', [])]:
        for n in (name, v['new']):
            pc = D.piece(spec, n)
            if D.self_mirror_map(pc) is None:
                pc.pop('mirror_edit', None)
                pc.pop('mirror_axis', None)
    return out
