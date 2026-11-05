# SPDX-License-Identifier: GPL-3.0-or-later
"""Blend across a seam (no bpy): where a seam ends at a corner of both pieces, the two outline edges that go on past
it (a front and a back waistline at the side seam, the two halves of an armhole at the shoulder) should run on as one
line once the pieces are sewn. Laid together along the seam's end (its end point and its direction there), the two
edges meet at a kink; Blend turns each one's direction at the junction by half of it, so they leave the junction in
line: a curved edge turns its handle there, any other bends over its first 30 mm only (the rest of the edge, the
junction point and the seam ends on the edge stay). Twins follow (Match Twin). Pieces that lie over each other at the
seam end (a facing, a pocket bag) are refused.
"""
import numpy as np
from ._i18n import rpt

from . import draft as D
from . import spec as S

TAN_MM = 3.0          # a direction at the junction: over this length
REACH_MM = 30.0       # the seam's direction at its end: over this length of the seam (or all of it, if shorter)
OVERLAP_DEG = 90.0    # more than this: the two pieces lie over each other there (a facing, a pocket bag, a turn-up)
CORNER_DEG = 45.0     # more than this (side by side): a corner of the garment (a waistband's end), not a line


def _side_end(spec, sd, end, ol):
    """(outline parameter at the end, +1 / -1: the way along the outline that leaves the side past that end)."""
    s = ol.s_of(*sd[end])
    sgn = 1 if sd['dir'] > 0 else -1
    return s, (sgn if end == 'to' else -sgn)


def _unit(v):
    return v / max(np.linalg.norm(v), 1e-12)


def junction(spec, seam_id, end):
    """The junction at end `end` ('from' / 'to') of a seam: {'pieces', 'edges' (the edge going on past the junction
    on each piece), 'at' ('start' / 'end' of that edge), 'P' (the junction point on each piece), 'kink' (deg, the
    two edges' directions laid together along the seam), 'R' (rotation laying piece b onto piece a)}."""
    sm = next(x for x in spec['seams'] if x['id'] == seam_id)
    if 'line' in sm['a'] or 'line' in sm['b']:
        raise ValueError(rpt("the seam is on an internal line"))
    info = {'pieces': [], 'edges': [], 'at': [], 'P': [], 'leave': [], 'into': []}
    for k in ('a', 'b'):
        sd = sm[k]
        pc = D.piece(spec, sd['piece'])
        ol = S.Outline(pc)
        s, out = _side_end(spec, sd, end, ol)
        eid, t = ol.edge_t(s)
        L = ol.edges[eid][1]
        if t * L <= D.SEAM_SNAP:
            s = ol.s_of(eid, 0.0)
        elif (1 - t) * L <= D.SEAM_SNAP:
            s = ol.s_of(eid, 1.0)
        else:
            raise ValueError(rpt("the seam ends in the middle of an edge on {0}: no corner to blend across", sd['piece']))
        nxt, tn = ol.edge_t(s + out * 0.5)
        info['pieces'].append(sd['piece'])
        info['edges'].append(nxt)
        info['at'].append('start' if out > 0 else 'end')
        P = ol.at(s)
        info['P'].append(P)
        info['leave'].append(_unit(ol.at(s + out * TAN_MM) - P))
        span = abs(ol.span(sd))
        info['into'].append(_unit(ol.at(s - out * min(REACH_MM, span)) - P))
    da, db = info['into']
    ang = np.arctan2(da[1], da[0]) - np.arctan2(db[1], db[0])
    c, s_ = np.cos(ang), np.sin(ang)
    R = np.array([[c, -s_], [s_, c]])
    ta, tb = info['leave'][0], R @ info['leave'][1]
    info['R'] = R
    info['kink'] = float(np.degrees(np.arccos(np.clip(ta @ (-tb), -1, 1))))
    return info


BEND_MM = 30.0        # a straight or polyline edge bends over this length from the junction (a third of it at most)


def _turn(spec, name, eid, at, P, delta):
    """Turn edge eid's direction at the junction end by delta (radians) without changing it further away: a curved
    edge turns its handle there; any other bends over its first BEND_MM, each point turned about the junction by
    delta over its first TAN_MM and by delta x (1 - (s - TAN_MM) / (BEND_MM - TAN_MM))^2 beyond (s its distance
    along the edge), so the far part and the seam ends on it stay."""
    pc = D.piece(spec, name)
    e = pc['edges'][D.edge_index(pc, eid)]
    c, sn = np.cos(delta), np.sin(delta)
    if e.get('ctrl'):
        w = 0 if at == 'start' else 1
        h = np.asarray(e['ctrl'][w], float) - P
        return D.set_handle(spec, name, eid, w, P + np.array([c * h[0] - sn * h[1], sn * h[0] + c * h[1]]),
                            twins=False)
    Q = np.asarray(e['pts'], float)
    if at == 'end':
        Q = Q[::-1]
    if len(Q) == 2:                                  # a straight edge: points to bend
        L = float(np.linalg.norm(Q[1] - Q[0]))
        k = max(3, int(L / 2.0))
        Q = np.linspace(Q[0], Q[1], k + 1)
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]
    Lb = max(min(BEND_MM, s[-1] / 3.0), 2.0 * TAN_MM)
    w = np.clip(1.0 - (s - TAN_MM) / (Lb - TAN_MM), 0.0, 1.0) ** 2     # all of it over the first TAN_MM (where the
                                                                       # direction is measured), then less and less
    out = Q.copy()
    for i in range(1, len(Q)):
        if w[i] <= 0:
            break
        a = delta * w[i]
        v = Q[i] - P
        out[i] = P + np.array([np.cos(a) * v[0] - np.sin(a) * v[1], np.sin(a) * v[0] + np.cos(a) * v[1]])
    if at == 'end':
        out = out[::-1]
    e['pts'] = D._r(out)
    return [name]


def _angle(u, v):
    return float(np.arctan2(u[0] * v[1] - u[1] * v[0], u @ v))


def refusal(spec, seam_id, j, *, translate=False):
    """Why the junction j of a seam is not blended ('' = it is): a dart, pieces lying over each other, a real corner,
    edges going on that are a dart's legs or sewn to each other."""
    text = rpt if translate else lambda msg, *args: msg.format(*args) if args else msg
    sm0 = next(x for x in spec['seams'] if x['id'] == seam_id)
    k0 = j['kink']
    if sm0['a']['piece'] == sm0['b']['piece']:
        return text("a seam on one piece (a dart): Close Dart trues a dart's mouth")
    if k0 > OVERLAP_DEG:
        return text("the pieces lie over each other at this seam end ({0:.0f} deg), not side by side", k0)
    if k0 > CORNER_DEG:
        return text("the outline turns a corner there ({0:.0f} deg): a corner meant to be one, not a line to blend", k0)
    mine = set(zip(j['pieces'], j['edges']))
    for sm in spec.get('seams', []):                 # the edges going on are a dart's legs, or sewn to each other
        if not sm.get('enabled', True) or 'line' in sm['a'] or 'line' in sm['b']:
            continue
        on = {(sm[k]['piece'], sm[k][w][0]) for k in 'ab' for w in ('from', 'to')}
        if sm['a']['piece'] == sm['b']['piece'] and on & mine:
            return text("an edge going on past the seam end is a dart's leg: Close Dart trues a dart's mouth")
        if mine <= on and sm['id'] != seam_id:
            return text("the two edges going on are sewn to each other ({0}): nothing to blend", sm['id'])
    return ''


def blend(spec, seam_id, end):
    """Blend the two edges going on past end `end` of a seam (see the module). Returns (pieces changed, kink before,
    kink after, report line)."""
    j = junction(spec, seam_id, end)
    k0 = j['kink']
    pa, pb = j['pieces']
    ta, tb = j['leave'][0], j['R'] @ j['leave'][1]
    why = refusal(spec, seam_id, j)
    if why:
        raise ValueError(refusal(spec, seam_id, j, translate=True))
    lens0 = {(p, e): S.polyline_len(D.piece(spec, p)['edges'][D.edge_index(D.piece(spec, p), e)]['pts'])
             for p, e in zip(j['pieces'], j['edges'])}
    twins_pair = D.twin_of(spec, pa) == pb
    changed = set()
    for _ in range(4):                               # turn each by half the kink; the directions are measured over
        j = junction(spec, seam_id, end)             # TAN_MM, so go again until it is gone
        if j['kink'] < 0.1:
            break
        ta, tb = j['leave'][0], j['R'] @ j['leave'][1]
        u = _unit(ta - tb)
        changed |= set(_turn(spec, pa, j['edges'][0], j['at'][0], j['P'][0], _angle(ta, u)))
        changed |= set(_turn(spec, pb, j['edges'][1], j['at'][1], j['P'][1], _angle(tb, -u)))
    for p in list(changed):                          # twins of the pieces turned: the mirror image
        tw = D.twin_of(spec, p)
        if tw and tw not in changed and not twins_pair:
            D.match_twin(spec, p)
            changed.add(tw)
    k1 = junction(spec, seam_id, end)['kink']
    dl = {f"{p}:{e}": round(S.polyline_len(D.piece(spec, p)['edges'][D.edge_index(D.piece(spec, p), e)]['pts']) - L0, 2)
          for (p, e), L0 in lens0.items()}
    return sorted(changed), k0, k1, f"Blended {seam_id} {end}: kink {k0:.1f} -> {k1:.1f} deg (edge lengths {dl})"


def kinks(spec):
    """[(seam id, end, kink deg)] at every seam end that sits on a corner of both pieces, the largest first."""
    out = []
    for sm in spec.get('seams', []):
        if not sm.get('enabled', True) or 'line' in sm['a'] or 'line' in sm['b']:
            continue
        for end in ('from', 'to'):
            try:
                out.append((sm['id'], end, junction(spec, sm['id'], end)['kink']))
            except (ValueError, KeyError, StopIteration):
                continue
    return sorted(out, key=lambda x: -x[2])
