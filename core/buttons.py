# SPDX-License-Identifier: GPL-3.0-or-later
"""Buttons (no bpy): a button on one piece, its buttonhole on another, fastened or not (user 2026-10-15, DECISIONS
cloth-sim-fasteners-alpha-2026-10-15).

    spec["buttons"] = [
      {"id": str,                              # unique
       "piece": name, "pt": [x, y],            # the button, a point of the 2D layout (mm) on its piece
       "hole": {"piece": name, "pt": [x, y]} | None,   # its buttonhole; None = a button sewn on for show only.
                                               # On another piece, or its own at SELF_MIN_MM or more (a cuff)
       "hole_by": "hand",                      # optional: the hole set by hand (Set Hole, No Hole): a change of
                                               # Closes onto leaves it as it is (user 2026-10-22)
       "style": str,                           # an id of the button library (the look; not read by the simulation)
       "fastened": bool}                       # default true: held to its hole while the garment is simulated
    ]

Fastening is not a seam: a pair is held by the solver's temp_stitches (as spec "tacks", dress.py), each fastened
button's nearest mesh vertex to its hole's. Unfasten a button and Continue: the pair is no longer held and the
garment opens there; fasten it and Continue: it closes again. A button whose two pieces form a front closure
(core/roles.closures) is held only in the stages that sew that closure (the closure stays open until everything
hangs, core/recipe.py), as a closure seam would be.

Placing (user 2026-10-15): one at a time by a click on the dressed garment, or N lined up on the pattern -- along the
closure's sewn line (a shirt's plackets are sewn to each other along their button line), else along an edge at an
inset. The hole goes with its button (hole_for) onto the piece it closes onto -- its Closes onto (a piece's field,
set by hand with an eyedropper, user 2026-10-22), else spec closures, else the BOM's button stitches: the point
the seams between them sew the button's point to; else the twin's mirror image; else none (Set Hole puts it by hand).
Buttons on a closure take over from its seams: the seams between the two pieces are turned off (off_by "buttons")
and turned on again when the last such button goes (sync_closure_seams).
"""
import numpy as np

from . import spec as S

LINE_TOL = 6.0          # mm: a button this near a closure's sewn line takes its hole from the seam
SELF_MIN_MM = 20.0      # mm: a hole on the button's own piece (a cuff, a waistband's extension) at least this far


def entries(spec, names):
    """The fastened buttons with a hole whose pieces are both in `names`, as spec tacks entries (+ the button id)."""
    out = []
    for b in spec.get('buttons', []) or []:
        h = b.get('hole')
        if not h or not b.get('fastened', True):
            continue
        if b.get('piece') not in names or h.get('piece') not in names:
            continue
        out.append({'a': b['piece'], 'pa': list(b['pt']), 'b': h['piece'], 'pb': list(h['pt']), 'id': b.get('id')})
    return out


def held_in_stage(cfg, pa, pb):
    """False when a stage keeps the seams between pieces pa and pb open the whole stage (a front closure's
    stitch_delay that never starts within the stage's frames)."""
    fr = 10 ** 9 if cfg.get('live') else int(cfg.get('frames', 0) or 0)     # Live: until stopped, no cap
    for d in cfg.get('stitch_delay', []) or []:
        ps = d.get('pieces', [])
        if sorted(ps) == sorted([pa, pb]) and int(d.get('start', 0)) >= fr:
            return False
    return True


# ---------------------------------------------------------------- placing

STITCH_MIN = 2          # seams on internal lines of both pieces: this many make the pieces a buttoned pair
STITCH_MAX_MM = 25.0    # and every such seam between them this short (a button's width; a sleeve placket sewn
                        # on its slit lines has a 105 mm one, measured on a BOM's shirt 2026-10-22)


def is_stitch(sm):
    """A seam on an internal line of both its pieces: how a BOM writes a button (its hole sewn to it)."""
    a, b = sm['a'], sm['b']
    return a['piece'] != b['piece'] and bool(a.get('line') and b.get('line'))


def stitched_partners(spec, piece):
    """The pieces sewn to `piece` by button stitches (is_stitch; on or turned off by buttons), at least STITCH_MIN
    of them, most first."""
    n, long_ = {}, set()
    for sm in spec.get('seams', []):
        a, b = sm['a'], sm['b']
        if piece not in (a['piece'], b['piece']) or not is_stitch(sm) or not (sm.get('enabled', True)
                                                                             or sm.get('off_by') == 'buttons'):
            continue
        o = b['piece'] if a['piece'] == piece else a['piece']
        n[o] = n.get(o, 0) + 1
        if max(_side_len(spec, sm[k]) for k in ('a', 'b')) > STITCH_MAX_MM:
            long_.add(o)
    return [o for o, k in sorted(n.items(), key=lambda t: -t[1]) if k >= STITCH_MIN and o not in long_]


def _side_len(spec, side):
    """Length (mm) of a seam side on an internal line (its from / to along the line)."""
    try:
        P = np.asarray(S.side_polyline(spec, side), float)
    except Exception:
        return 0.0
    return float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum()) if len(P) > 1 else 0.0


def closes_onto(spec, piece):
    """The piece `piece` buttons onto, set by hand (pieces[].closes_onto), or None."""
    pc = S.piece_index(spec).get(piece)
    o = (pc or {}).get('closes_onto')
    return o if o and o in S.piece_index(spec) and o != piece else None


def closure_partners(spec, piece):
    """The pieces `piece` closes onto: its Closes onto (set by hand, user 2026-10-22), else spec closures, else those
    its button stitches sew it to (the plackets of a BOM whose roles are not set)."""
    co = closes_onto(spec, piece)
    if co:
        return [co]
    out = [b if a == piece else a for a, b in (spec.get('closures') or []) if piece in (a, b)]
    return out or stitched_partners(spec, piece)


def set_closes_onto(spec, piece, other, outlines=None):
    """Piece `piece` buttons onto `other` (None: onto nothing), written on both; the pieces each was paired with
    before let go. The holes of the two pieces' buttons not placed by hand follow (refill_holes). Returns the
    buttons whose hole changed."""
    pcs = S.piece_index(spec)
    for n in (piece, other):
        if n is None:
            continue
        old = pcs[n].get('closes_onto')
        if old and old in pcs and pcs[old].get('closes_onto') == n:
            pcs[old].pop('closes_onto', None)
        pcs[n].pop('closes_onto', None)
    if other is not None and other != piece:
        pcs[piece]['closes_onto'] = other
        pcs[other]['closes_onto'] = piece
    return refill_holes(spec, outlines)


def refill_holes(spec, outlines=None):
    """Every button whose hole was not set by hand (hole_by 'hand': Set Hole, No Hole) given the hole hole_for finds
    now: fastened when it gets one, for show (no hole, unfastened) when it has none. Returns the ids changed."""
    out = []
    for b in spec.get('buttons', []) or []:
        if b.get('hole_by') == 'hand':
            continue
        h = hole_for(spec, b['piece'], b['pt'], outlines)
        if h != b.get('hole'):
            if h is not None and not b.get('hole'):
                b['fastened'] = True
            elif h is None:
                b['fastened'] = False
            b['hole'] = h
            out.append(b['id'])
    return out


def partners(spec, piece):
    """The pieces `piece` closes onto: spec closures (the front) first, then its twin."""
    out = closure_partners(spec, piece)
    for a, b in S.twins(spec):
        if piece in (a, b):
            t = b if a == piece else a
            if t not in out:
                out.append(t)
    return out


def pair_seams(spec, pa, pb):
    """Every seam (on or off) between pieces pa and pb."""
    return [sm for sm in spec.get('seams', []) if {sm['a']['piece'], sm['b']['piece']} == {pa, pb}
            and sm['a']['piece'] != sm['b']['piece']]


def _closest(P, q):
    """(distance, arc fraction) of the point of polyline P nearest q."""
    P = np.asarray(P, float)
    q = np.asarray(q, float)
    d = np.diff(P, axis=0)
    L2 = np.maximum((d * d).sum(1), 1e-18)
    t = np.clip(((q - P[:-1]) * d).sum(1) / L2, 0.0, 1.0)
    C = P[:-1] + d * t[:, None]
    D = np.linalg.norm(C - q, axis=1)
    k = int(np.argmin(D))
    cum = np.r_[0, np.cumsum(np.sqrt(L2))]
    return float(D[k]), float((cum[k] + t[k] * np.sqrt(L2[k])) / max(cum[-1], 1e-12))


def _at(P, f):
    P = np.asarray(P, float)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    s = f * cum[-1]
    return np.array([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])])


def pair_map(spec, pa, pb, outlines=None, n=21):
    """The layout map piece pa -> piece pb the seams between them make (a closure's: a shirt's plackets sewn at
    the BOM's button stitches), as a fitted rotation + shift (+ a reflection when exactly one of them is a mirrored
    piece: both are seen from the outside when worn); None without such seams. Returns (M, rms mm)."""
    A, B = [], []
    for sm in pair_seams(spec, pa, pb):
        mine, other = (sm['a'], sm['b']) if sm['a']['piece'] == pa else (sm['b'], sm['a'])
        PA = S.side_polyline(spec, mine, outlines)
        PB = S.side_polyline(spec, other, outlines)
        for f in np.linspace(0.0, 1.0, n):
            A.append(_at(PA, f))
            B.append(_at(PB, f))
    if len(A) < 2:
        return None
    A, B = np.array(A), np.array(B)
    pcs = S.piece_index(spec)
    refl = bool(pcs[pa].get('mirrored')) != bool(pcs[pb].get('mirrored'))
    ca, cb = A.mean(0), B.mean(0)
    H = (A - ca).T @ (B - cb)
    U, _s, Vt = np.linalg.svd(H)
    # Kabsch with the determinant chosen: -1 (a reflection) when exactly one piece is mirrored, else +1
    d = (-1.0 if refl else 1.0) * np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, d]) @ U.T

    def M(q):
        q = np.asarray(q, float).reshape(-1, 2)
        return (q - ca) @ R.T + cb
    rms = float(np.sqrt(np.mean(np.sum((M(A) - B) ** 2, axis=1))))
    return M, rms


def hole_for(spec, piece, pt, outlines=None):
    """{'piece', 'pt'} of the hole for a button at `pt` on `piece` from the pattern alone, or None (no closure seam
    and no twin: the bpy side looks on the dressed garment). A closure's seams give the map to its other piece
    (pair_map), else the twin's mirror image."""
    close = closure_partners(spec, piece)
    for pb in close:
        pm = pair_map(spec, piece, pb, outlines)
        if pm is not None:
            q = pm[0](pt)[0]
            return {'piece': pb, 'pt': [round(float(x), 2) for x in q]}
    # the twin's mirror image only when the twin is what it closes onto (a front cut in one with its placket): a
    # sleeve's twin is the other sleeve, its button is for show (user 2026-10-22, Fable buttons review §4)
    tw = next((b if a == piece else a for a, b in S.twins(spec) if piece in (a, b)), None)
    if tw is not None and tw in close:
        from . import draft as D
        pcs = S.piece_index(spec)
        q = D.mirror_fn(pcs[piece], pcs[tw])(pt)[0]
        return {'piece': tw, 'pt': [round(float(x), 2) for x in q]}
    return None


def stitch_points(spec, piece, outlines=None):
    """The middles of the seams sewing `piece` to its closure partners, top first: where a BOM had its
    buttons (each button and its hole sewn as a short seam)."""
    out = []
    for pb in stitched_partners(spec, piece):
        for sm in pair_seams(spec, piece, pb):
            if not is_stitch(sm):
                continue
            mine = sm['a'] if sm['a']['piece'] == piece else sm['b']
            out.append(_at(S.side_polyline(spec, mine, outlines), 0.5))
    out.sort(key=lambda q: -q[1])
    return [[round(float(q[0]), 2), round(float(q[1]), 2)] for q in out]


def _inside(poly, q):
    x, y = q
    c = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _extend(P, poly, step=1.0, far=2000.0):
    """P with both ends carried on straight (their end directions) up to the piece's outline `poly`."""
    P = np.asarray(P, float)
    if len(P) < 2:
        return P

    def run(p, d):
        d = d / max(np.linalg.norm(d), 1e-12)
        q, s = p, 0.0
        while s < far and _inside(poly, q + d * step):
            q = q + d * step
            s += step
        return q
    head = run(P[0], P[0] - P[1])
    tail = run(P[-1], P[-1] - P[-2])
    return np.vstack([head, P, tail])


def closure_line(spec, piece, outlines=None):
    """The line (mm, top first = larger y) through `piece`'s closure seams (the BOM's button stitches lie on one),
    carried on to the piece's outline at both ends; None when it has none."""
    parts = []
    for pb in closure_partners(spec, piece):
        for sm in pair_seams(spec, piece, pb):
            mine = sm['a'] if sm['a']['piece'] == piece else sm['b']
            parts.append(np.asarray(S.side_polyline(spec, mine, outlines), float))
    if not parts:
        return None
    parts = [P if P[0, 1] >= P[-1, 1] else P[::-1] for P in parts]
    parts.sort(key=lambda P: -P[0, 1])
    pc = S.piece_index(spec)[piece]
    poly = np.vstack([np.asarray(e['pts'], float) for e in pc['edges']])
    return _extend(np.vstack(parts), poly)


def edge_line(spec, piece, eid, inset):
    """The piece's edge `eid` moved `inset` mm into the piece, top first."""
    pc = S.piece_index(spec)[piece]
    E = np.asarray(next(e['pts'] for e in pc['edges'] if e['id'] == eid), float)
    poly = np.vstack([np.asarray(e['pts'], float) for e in pc['edges']])
    area = 0.5 * np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1])
    t = np.gradient(E, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    n_in = np.stack([-t[:, 1], t[:, 0]], 1) * (1.0 if area > 0 else -1.0)     # left of a CCW outline is inside
    L = E + n_in * inset
    return L if L[0, 1] >= L[-1, 1] else L[::-1]


def line_up(line, count, top=30.0, bottom=50.0, spacing=0.0):
    """`count` points along `line` (top first): from `top` mm under its top, every `spacing` mm, or (spacing 0)
    spread evenly down to `bottom` mm above its end."""
    P = np.asarray(line, float)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    if count < 1 or cum[-1] <= 0:
        return []
    if spacing > 0 or count == 1:
        s = top + np.arange(count) * spacing
    else:
        s = np.linspace(top, cum[-1] - bottom, count)
    s = np.clip(s, 0.0, cum[-1])
    return [[round(float(np.interp(x, cum, P[:, 0])), 2), round(float(np.interp(x, cum, P[:, 1])), 2)] for x in s]


def new_id(spec):
    used = {b.get('id') for b in spec.get('buttons', []) or []}
    k = 1
    while f"B{k}" in used:
        k += 1
    return f"B{k}"


def add(spec, piece, pt, style, hole=None, outlines=None):
    """Add a button (fastened when it has a hole); its hole by hole_for unless given. Returns the entry."""
    if hole is None:
        hole = hole_for(spec, piece, pt, outlines)
    b = {'id': new_id(spec), 'piece': piece, 'pt': [round(float(pt[0]), 2), round(float(pt[1]), 2)],
         'hole': hole, 'style': style, 'fastened': hole is not None}
    spec.setdefault('buttons', []).append(b)
    return b


def sync_closure_seams(spec):
    """The button stitches (is_stitch) between a button's piece and its hole's piece off while such a button exists
    (off_by "buttons"), back on when none is left; the other seams between them stay as they are. spec['closures']
    is not written here: a button is a mark, not what closes onto what (user 2026-10-22, Fable buttons review §4;
    From BOM writes it). A closure of two twins with no stitch between them -- what this wrote before -- is dropped.
    Returns the seam ids changed."""
    pairs = {frozenset((b['piece'], b['hole']['piece'])) for b in spec.get('buttons', []) or [] if b.get('hole')}
    pairs = {p for p in pairs if len(p) == 2}
    bpairs = {p for p in pairs if next(iter(p - {min(p)}), None) in stitched_partners(spec, min(p))}
    changed = []
    for sm in spec.get('seams', []):
        pr = frozenset((sm['a']['piece'], sm['b']['piece']))
        if len(pr) != 2:
            continue
        if pr in pairs and pr in bpairs and is_stitch(sm) and sm.get('enabled', True):
            sm['enabled'] = False
            sm['off_by'] = 'buttons'
            changed.append(sm['id'])
        elif not (pr in pairs and pr in bpairs and is_stitch(sm)) and sm.get('off_by') == 'buttons':
            sm['enabled'] = True
            sm.pop('off_by', None)
            changed.append(sm['id'])
    if spec.get('closures'):
        tw = {frozenset(t) for t in S.twins(spec)}
        stitched = {frozenset((sm['a']['piece'], sm['b']['piece'])) for sm in spec.get('seams', []) if is_stitch(sm)}
        keep = [c for c in spec['closures'] if not (frozenset(c) in tw and frozenset(c) not in stitched)]
        if len(keep) != len(spec['closures']):
            if keep:
                spec['closures'] = keep
            else:
                spec.pop('closures')            # core/roles.closures falls back to the plackets again
    return changed
