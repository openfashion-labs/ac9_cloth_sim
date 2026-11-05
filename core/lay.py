# SPDX-License-Identifier: GPL-3.0-or-later
"""Lay (user 2026-10-21, docs/reviews/REVIEW_fable_lay_rule_2026-10-21.md): a narrow band sewn along one edge to
pieces sewn before it -- a T-shirt's neck rib -- is left out of the stages that sew those pieces, and where it joins
its sewn edge is laid on their sewn edge, its width running on from them (solver/lay.py does the laying, from the
last frame). Placed as a level ring round the neck guide, the rib pulled the V-neck's bottom up to it and started
through the bodice (experiments/rib_vneck_2026-10-16, buried_features_2026-10-19); laid, the staged T-shirt stopped
to 0.00 mm a frame 4 of 4 (stop_check_2026-10-19).

Which pieces (no bpy): the ones whose Start the person (or the role's table, written when a role was set) made
On Edge (user 2026-11-02, DECISIONS cloth-sim-lay-by-role-2026-11-02; Fable docs/reviews/
REVIEW_fable_start_pose_roles_2026-11-02.md). Nothing is told from a piece's shape or role here: a rib left As Placed
is sewn from its placed ring like any piece. Of those, laid only where it can be (else skipped(), one line each):
  1. in the neck group (the α version: lay.apply knows the stage after 'body and collar (gravity)' only);
  2. sewn only to pieces of the body group (the pieces sewn before it), at least one;
  3. not moved by hand (the user's placed object is the start: a piece Placed and then moved keeps where it was put);
  4. what is sewn to it is one edge, along one long side (a ring's two ends sewn to each other is fine): the shape
     solver/lay.py lays by one axis.
The width and the length-to-width share are not checked (they would tell a kind of piece).
Whether it lands (the length, the jumps, no worse than placed) is solver/lay.py's check, from the last frame.
Only in the sleeved recipe's default order. Off: the settings set's 'lay' False or the spec's 'lay' False.
"""
import numpy as np

from . import roles as RO

# a sewn stretch counts as on the long side when it stays within this share of the short side of it. Not a kind of
# piece: solver/lay.py lays the band by one axis through its sewn points (design review 2026-11-02), so a piece sewn
# on two sides would be laid twisted
EDGE_FRAC = 0.25


def _one_edge(f):
    sides = [P for y_ in f['sewn'] for P in f['sides'].get(y_, [])]
    if not sides:
        return False
    c = 1 if f['w'] >= f['h'] else 0
    lo = min(float(np.min(np.asarray(P)[:, c])) for P in sides)
    hi = max(float(np.max(np.asarray(P)[:, c])) for P in sides)
    return hi - lo <= EDGE_FRAC * min(f['w'], f['h'])


def _why(spec, n, body, collar, hand, F):
    if n not in collar:
        return "On Edge is only for a neck piece for now: placed as it is"
    if n in hand:
        return "moved by hand: starts where it was put"
    f = F.get(n)
    if f is None or not f['sewn']:
        return "not sewn to another piece"
    if not set(f['sewn']) <= set(body):
        return "sewn to a piece that is not the body: placed as it is"
    if not _one_edge(f):
        return "sewn on more than one side: placed as it is"
    return ''


def _edge_pieces(spec, names):
    on = set(names)
    return [pc['name'] for pc in spec.get('pieces', []) if pc['name'] in on and RO.start_of(pc) == 'edge']


def candidates(spec, names, body, collar, hand=()):
    """The On Edge pieces that can be laid (in names' order)."""
    if spec.get('lay') is False:
        return []
    try:
        F = RO.facts(spec)
    except Exception:
        return []
    edge = set(_edge_pieces(spec, names))
    return [n for n in names if n in edge and not _why(spec, n, body, collar, hand, F)]


def skipped(spec, names, body, collar, hand=()):
    """{piece: why} for the On Edge pieces not laid."""
    edge = _edge_pieces(spec, names)
    if not edge:
        return {}
    if spec.get('lay') is False:
        return {n: "laying is off for this garment" for n in edge}
    try:
        F = RO.facts(spec)
    except Exception:
        F = {}
    out = {n: _why(spec, n, body, collar, hand, F) for n in edge}
    return {n: w for n, w in out.items() if w}


def apply(stg, laid):
    """Take the laid pieces out of the stages before the one after 'body and collar (gravity)' and have that stage
    lay them (cfg 'lay'). stg: [(label, cfg)] of the sleeved recipe's default order. Returns the pieces laid ([] when
    the order is not that one)."""
    if not laid:
        return []
    labels = [lb for lb, _c in stg]
    try:
        k2 = labels.index("body and collar (gravity)")
    except ValueError:
        return []
    if k2 + 1 >= len(stg) or not labels[0].startswith("body and collar"):
        return []
    L = set(laid)
    for k in range(k2 + 1):
        cfg = stg[k][1]
        cfg['active_pieces'] = [p for p in cfg['active_pieces'] if p not in L]
        if cfg.get('init_from_pieces') is not None:
            cfg['init_from_pieces'] = [p for p in cfg['init_from_pieces'] if p not in L]
        if cfg.get('stitch_delay'):
            cfg['stitch_delay'] = [s_ for s_ in cfg['stitch_delay'] if not (set(s_['pieces']) & L)]
            if not cfg['stitch_delay']:
                cfg.pop('stitch_delay')
    j = stg[k2 + 1][1]
    if j.get('init_from_pieces') is not None:
        j['init_from_pieces'] = [p for p in j['init_from_pieces'] if p not in L]
    j['lay'] = {"pieces": list(laid)}
    return list(laid)
