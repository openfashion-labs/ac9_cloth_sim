# SPDX-License-Identifier: GPL-3.0-or-later
"""Zippers (no bpy): two edges joined tooth by tooth, closed from the bottom up to the slider (user 2026-10-15,
DECISIONS cloth-sim-fasteners-alpha-2026-10-15: open and close is the aim, the teeth a texture).

    spec["zippers"] = [
      {"id": str,                                   # unique
       "a": {"piece": name, "edges": [edge id, ...]},   # the two sides: consecutive whole edges of the outline
       "b": {"piece": name, "edges": [edge id, ...]},
       "closed": float,                             # the slider: 0 = open, 1 = closed to the top (from the bottom)
       "pitch": float,                              # mm between the teeth held together (the simulation's, not the look)
       "style": str}                                # the look (not read by the simulation)
    ]

Not a seam, as buttons are not (core/buttons.py): every `pitch` mm of the closed part the two sides' nearest mesh
vertices are held by the solver's temp_stitches (dress.py), the same as a fastened button. Move the slider and
Continue: the part above it lets go, or closes. The two edges are taken bottom to top (the layout's y), each by its
own length, so the n-th tooth of one side meets the n-th of the other.
"""
import numpy as np

from . import spec as S

PITCH = 5.0             # mm: a held pair every 5 mm (the mesh is ~6 mm)


def _edge(spec, piece, eid):
    pc = S.piece_index(spec)[piece]
    return np.asarray(next(e['pts'] for e in pc['edges'] if e['id'] == eid), float)


def _chain(spec, piece, eids):
    """Consecutive edges as one polyline (in outline order)."""
    pc = S.piece_index(spec)[piece]
    order = [e['id'] for e in pc['edges']]
    k = sorted(order.index(e) for e in eids)
    if k and k[-1] - k[0] != len(k) - 1:            # wraps round the outline's start
        k = sorted(k, key=lambda i: (i - k[-1] - 1) % len(order))
    P = [_edge(spec, piece, order[i]) for i in k]
    return np.vstack([P[0]] + [q[1:] for q in P[1:]])


def _dir(P):
    d = P[-1] - P[0]
    return d / max(np.linalg.norm(d), 1e-12)


def extend(spec, piece, eid, max_turn=30.0):
    """Edge eid and the free edges carrying it on (each turning less than max_turn degrees from the last):
    a front's center line drawn as two edges (straight, then easing into the neckline) is one zipper side."""
    pc = S.piece_index(spec)[piece]
    order = [e['id'] for e in pc['edges']]
    free = set(free_edges(spec, piece))
    out = [eid]
    for step in (1, -1):
        k, cur = order.index(eid), eid
        while True:
            k = (k + step) % len(order)
            nxt = order[k]
            if nxt not in free or nxt in out:
                break
            a, b = (cur, nxt) if step == 1 else (nxt, cur)
            ang = np.degrees(np.arccos(np.clip(_dir(_edge(spec, piece, a)) @ _dir(_edge(spec, piece, b)), -1, 1)))
            if ang > max_turn:
                break
            out.append(nxt)
            cur = nxt
    return [e for e in order if e in out]


def _up(P):
    """The polyline bottom first (layout y up)."""
    return P if P[0, 1] <= P[-1, 1] else P[::-1]


def free_edges(spec, piece, tol=1.0):
    """The piece's outline edges no enabled seam sews (a sample of a seam side within `tol` mm of the edge counts)."""
    pc = S.piece_index(spec)[piece]
    ol = S.Outline(pc)
    sewn = []
    for sm in spec.get('seams', []):
        if not sm.get('enabled', True):
            continue
        for k in ('a', 'b'):
            if sm[k]['piece'] == piece and 'line' not in sm[k]:
                sewn.append(S.side_polyline(spec, sm[k], {piece: ol}, step=2.0))
    Q = np.vstack(sewn) if sewn else np.zeros((0, 2))
    out = []
    for e in pc['edges']:
        P = np.asarray(e['pts'], float)
        L = S.polyline_len(P)
        if L <= 0:
            continue
        mid = P[len(P) // 2] if len(P) > 2 else 0.5 * (P[0] + P[-1])
        if len(Q) and np.linalg.norm(Q - mid, axis=1).min() < tol:
            continue
        out.append(e['id'])
    return out


def front_edge(spec, piece):
    """The free edge a zipper most likely goes on: the longest straight one (length x chord / length)."""
    best, score = None, 0.0
    for eid in free_edges(spec, piece):
        P = _edge(spec, piece, eid)
        L = S.polyline_len(P)
        s = L * np.linalg.norm(P[-1] - P[0]) / L * (abs(P[-1, 1] - P[0, 1]) / L)      # long, straight, upright
        if s > score:
            best, score = eid, s
    return best


def partner_edges(spec, pa, ea, pb):
    """The edges of pb matching edges ea of pa: the same ids if pb has them all free, else partner_edge of the
    first, extended."""
    fb = set(free_edges(spec, pb))
    if all(e in fb for e in ea):
        return list(ea)
    return extend(spec, pb, partner_edge(spec, pa, ea[0], pb))


def partner_edge(spec, pa, ea, pb):
    """The edge of pb matching edge ea of pa: the same id if pb has it free, else the free edge nearest ea's mirror
    image (twins), else pb's front_edge."""
    fb = free_edges(spec, pb)
    if ea in fb:
        return ea
    pcs = S.piece_index(spec)
    try:
        from . import draft as D
        M = D.mirror_fn(pcs[pa], pcs[pb])
        A = M(_edge(spec, pa, ea))
        best = min(fb, key=lambda e: np.mean([np.linalg.norm(_edge(spec, pb, e) - q, axis=1).min() for q in A]))
        return best
    except Exception:
        return front_edge(spec, pb)


def new_id(spec):
    used = {z.get('id') for z in spec.get('zippers', []) or []}
    k = 1
    while f"Z{k}" in used:
        k += 1
    return f"Z{k}"


def add(spec, pa, pb, ea=None, eb=None, closed=1.0, style='coil'):
    """A zipper between pieces pa and pb (lists of edge ids; their front edges, extended, unless given). Returns the
    entry."""
    ea = ea or extend(spec, pa, front_edge(spec, pa))
    eb = eb or partner_edges(spec, pa, ea, pb)
    z = {'id': new_id(spec), 'a': {'piece': pa, 'edges': list(ea)}, 'b': {'piece': pb, 'edges': list(eb)},
         'closed': float(closed), 'pitch': PITCH, 'style': style}
    spec.setdefault('zippers', []).append(z)
    return z


def _at(P, s):
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    s = np.clip(s, 0.0, cum[-1])
    return np.stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])], -1), cum[-1]


def teeth(spec, z, all_=False):
    """The held pairs of zipper z (all_: the open ones too, with closed False): [{'a', 'pa', 'b', 'pb', 'u', 'closed',
    'id'}], u = 0 at the bottom, 1 at the top, by each side's own length."""
    A = _up(_chain(spec, z['a']['piece'], z['a']['edges']))
    B = _up(_chain(spec, z['b']['piece'], z['b']['edges']))
    La, Lb = S.polyline_len(A), S.polyline_len(B)
    n = max(2, int(round(max(La, Lb) / float(z.get('pitch') or PITCH))) + 1)
    u = np.linspace(0.0, 1.0, n)
    Pa, _ = _at(A, u * La)
    Pb, _ = _at(B, u * Lb)
    c = float(z.get('closed', 1.0))
    out = []
    for k in range(n):
        shut = u[k] <= c + 1e-9 and c > 0
        if shut or all_:
            out.append({'a': z['a']['piece'], 'pa': [round(float(Pa[k, 0]), 2), round(float(Pa[k, 1]), 2)],
                        'b': z['b']['piece'], 'pb': [round(float(Pb[k, 0]), 2), round(float(Pb[k, 1]), 2)],
                        'u': float(u[k]), 'closed': bool(shut), 'id': f"{z['id']}:{k}"})
    return out


def entries(spec, names):
    """The closed teeth of every zipper whose pieces are both in `names`, as spec tacks entries (+ an id)."""
    out = []
    for z in spec.get('zippers', []) or []:
        if z['a']['piece'] in names and z['b']['piece'] in names:
            out.extend(teeth(spec, z))
    return out
