# SPDX-License-Identifier: GPL-3.0-or-later
"""A twin's seams made again as the mirror image of its partner's (user 2026-10-14, with Mirror to Twin; no bpy).

Mirror to Twin makes a twin's outline its partner's mirror image again; with this its seams follow too: every seam on
the twin (but those sewing the two to each other) is removed, and every seam on the partner is mirrored onto it --
the same On / Off, check, pull round the body (left <-> right, arm_L <-> arm_R), folds. The other side of each seam:

    on the partner itself (a sleeve's underarm)     -> on the twin
    on a piece with a twin of its own (front_L)     -> on that twin (front_R), mirrored the same way
    on a single piece (a raglan's front, a collar)  -> on the same piece, mirrored about its own middle: a sleeve on
                                                       its left armhole goes to its right armhole (the piece must be
                                                       symmetric there, within TOL_MM, else the seam is not made)

A side is mirrored point by point: its two ends carried by the mirror map and put on the nearest point of the target
outline (or internal line), its direction round the outline reversed (a mirror turns the outline round).
"""
import re
from ._i18n import rpt, length as len_text

import numpy as np

from . import draft as D
from . import spec as S

TOL_MM = 1.0           # a mirrored end this far off the target outline: the target is not symmetric there
LEN_TOL = 0.02         # a mirrored side whose length differs more than this (fraction) is not made


def _reflect_self(pc, plane='x'):
    """The mirror maps of a single piece onto itself, to try in turn: left / right, across its own center line
    (draft.self_reflect); front / back, across the line at right angles to it first (a center line runs up unless
    turned), then across the center line itself (a gusset whose center line was turned to run across)."""
    if plane == 'y':
        return [D.self_reflect(pc, across=True), D.self_reflect(pc)]
    return D.self_reflect(pc)


def twin_in(spec, piece, plane):
    """piece's twin when their pair mirrors in `plane`, else None (a gusset's left / right twin is not where a
    front / back copy of the panel sewn to it goes, user 2026-11-12)."""
    t = D.twin_of(spec, piece)
    return t if t is not None and S.pair_plane(spec, piece) == plane else None


def _target(spec, piece, name, tw, made=None):
    """(target piece, mirror map) of a side on `piece` when `name`'s seams are mirrored onto its twin `tw`. made:
    {original: copy} of a group just copied (their twins, though an Off pair is not one in S.twins). A piece in a
    pair mirrored in the same plane goes to its twin; one with no twin, or with a twin in the other plane, stays and
    is mirrored onto itself (_reflect_self: a list of maps to try in a front / back mirror)."""
    if piece == name:
        return tw, D.mirror_fn(D.piece(spec, name), D.piece(spec, tw))
    if made and piece in made:
        return made[piece], D.mirror_fn(D.piece(spec, piece), D.piece(spec, made[piece]))
    plane = S.pair_plane(spec, name) if name else S.pair_plane(spec, piece)
    t2 = twin_in(spec, piece, plane)
    if t2 is not None:
        return t2, D.mirror_fn(D.piece(spec, piece), D.piece(spec, t2))
    return piece, _reflect_self(D.piece(spec, piece), plane)


def _nearest_s(ol, q):
    B = np.vstack([ol.B, ol.B[:1]])
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((q - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    d = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
    j = int(d.argmin())
    return ol.cum[j] + t[j] * np.sqrt(ll[j]), float(d[j])


def mirror_side(spec, side, name, tw, outlines, skip=(), made=None):
    """The side mirrored (a new SIDE dict), or (None, why). A target with several maps to try (_reflect_self): the
    first that lands on the outline, as long, on a place not sewn."""
    tp, Ms = _target(spec, side['piece'], name, tw, made)
    why = None
    for M in (Ms if isinstance(Ms, list) else [Ms]):
        out, why = _mirror_side_by(spec, side, name, tp, M, outlines, skip)
        if out is not None:
            return out, None
    return None, why


def _mirror_side_by(spec, side, name, tp, M, outlines, skip):
    src, dst = D.piece(spec, side['piece']), D.piece(spec, tp)
    if 'line' in side:
        P = M(D.internal(src, side['line'])['pts'])
        for it in dst.get('internals', []):
            Q = np.asarray(it['pts'], float)
            if len(Q) != len(P):
                continue
            if np.abs(Q - P).max() < TOL_MM:
                return dict(side, piece=tp, line=it['id']), None
            if np.abs(Q[::-1] - P).max() < TOL_MM:
                return dict(side, piece=tp, line=it['id'], **{'from': 1.0 - side['from'], 'to': 1.0 - side['to']}), None
        return None, f"no internal line on {tp} mirrors {side['piece']} {side['line']}"
    ol_s, ol_d = outlines[side['piece']], outlines[tp]
    out = dict(side, piece=tp, dir=-int(side.get('dir', 1)))
    for end in ('from', 'to'):
        q = M(ol_s.at(ol_s.s_of(*side[end])))[0]
        s, d = _nearest_s(ol_d, q)
        if d > TOL_MM:
            return None, f"{tp} is not symmetric there ({len_text(d)} off)"
        e, t = ol_d.edge_t(s)
        out[end] = [e, round(float(t), 6)]
    L0, L1 = abs(ol_s.span(side)), abs(ol_d.span(out))
    if abs(L1 - L0) > LEN_TOL * max(L0, 1e-6):
        return None, f"mirrored on {tp} it is {len_text(L1)} long, not {len_text(L0)}"
    if tp == side['piece'] and tp != name:
        # a single piece mirrored about its own middle: right for a front, wrong for a band closed into a ring whose
        # center front is not the middle of the strip (a raglan's neckband, 2026-10-14). Where the mirror image lands
        # on another seam of the piece, it is not the place
        hit = _taken(spec, out, ol_d, skip)
        if hit:
            return None, f"its place on {tp} is sewn by {hit} already"
    return out, None


def _interval(ol, side):
    s0 = ol.s_of(*side['from'])
    sp = ol.span(side)
    return (s0, s0 + sp) if sp >= 0 else (s0 + sp, s0)


def _taken(spec, side, ol, skip, tol=1.0):
    """The id of another seam whose side on the same piece overlaps `side` by more than tol mm, else None."""
    a0, a1 = _interval(ol, side)
    for sm in spec['seams']:
        if sm['id'] in skip:
            continue
        for k in ('a', 'b'):
            o = sm[k]
            if o['piece'] != side['piece'] or 'line' in o:
                continue
            b0, b1 = _interval(ol, o)
            for sh in (-ol.L, 0.0, ol.L):                      # the outline is closed: compare round the seam too
                if min(a1, b1 + sh) - max(a0, b0 + sh) > tol:
                    return sm['id']
    return None


def _swap_lr(s, plane='x'):
    """A seam path's side mirrored: left / right swap in a left / right mirror, front / back in a front / back one."""
    sw = {'left': 'right', 'right': 'left'} if plane != 'y' else {'front': 'back', 'back': 'front'}
    return sw.get(s, s)


def _mirror_guide(g, plane='x'):
    if not g or plane == 'y':              # a front / back mirror keeps an arm on its side
        return g
    m = re.match(r'^(.*)_(L|R)$', g)
    return f"{m.group(1)}_{'R' if m.group(2) == 'L' else 'L'}" if m else g


def _new_id(spec, base):
    used = {sm['id'] for sm in spec.get('seams', [])}
    k = base + 'm'
    while k in used:
        k += 'm'
    return k


def _mirror_seam(spec, sm, name, tw, outlines, made=None, skip=()):
    """The seam sm mirrored (a new seam dict, added to nothing), or (None, why). skip: seams whose place the mirror
    image may land on (its own partner, when an edit is carried over)."""
    sides = {}
    for k in ('a', 'b'):
        sides[k], why = mirror_side(spec, sm[k], name, tw, outlines, skip={sm['id']} | set(skip), made=made)
        if sides[k] is None:
            return None, why
    nm = {kk: v for kk, v in sm.items() if kk not in ('id', 'a', 'b', 'bom')}
    nm.update(id=_new_id(spec, sm['id']), a=sides['a'], b=sides['b'], source='user')
    pl_ = S.pair_plane(spec, name) if name else S.pair_plane(spec, sm['a']['piece'])
    if sm.get('path'):
        nm['path'] = dict(sm['path'], side=_swap_lr(sm['path'].get('side', 'auto'), pl_))
        if sm['path'].get('guide'):
            nm['path']['guide'] = _mirror_guide(sm['path']['guide'], pl_)
    return nm, None


def mirror_within(spec, ids):
    """Seams `ids` just added between pieces both edited symmetrically (Symmetric Edit on and symmetric): each gets
    its mirror image across the pieces' center lines too (user 2026-10-27, b-3), unless it is its own mirror image (a
    seam across the center line) or the place is sewn already. Returns the ids made."""
    made = []
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    for sm in [x for x in spec['seams'] if x['id'] in set(ids)]:
        ps = {sm['a']['piece'], sm['b']['piece']}
        if any(D.symmetric_edit(D.piece(spec, p)) is None for p in ps):
            continue
        selfs = {p: p for p in ps}                 # each side onto its own piece, across its center line
        nm, why = _mirror_seam(spec, sm, None, None, outlines, selfs)
        if nm is None:
            continue
        same = True
        for k in ('a', 'b'):
            o, m = sm[k], nm[k]
            if 'line' in o:
                same &= m.get('line') == o['line']
                continue
            ol = outlines[o['piece']]
            a0, a1 = _interval(ol, o)
            b0, b1 = _interval(ol, m)
            same &= any(min(a1, b1 + sh) - max(a0, b0 + sh) > 1.0 for sh in (-ol.L, 0.0, ol.L))
        if same:
            continue
        spec['seams'].append(nm)
        made.append(nm['id'])
    return made


def mirror_onto_twins(spec, ids):
    """Seams `ids` just added whose every side is on a piece of a left / right pair (Mirror Copy): each gets its
    mirror image between those pieces' twins (front - back -> front_R - back_R; a dart on front -> on front_R). Not
    when a side is on a piece with no twin (its mirror image would have to land on that single piece), not a seam
    between the two halves of one pair (a center front: its own mirror image), not where the twins are sewn there
    already (the other side added by hand first). Returns (ids made, notes: the seams not mirrored and why)."""
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    made, notes = [], []
    for sm in [x for x in spec['seams'] if x['id'] in set(ids)]:
        ps = {sm['a']['piece'], sm['b']['piece']}
        tw = {p: D.twin_of(spec, p) for p in ps}
        if any(t is None for t in tw.values()):
            continue                               # the user's rule: both ends on pairs, else no mirror
        if len({S.pair_plane(spec, p) for p in ps}) > 1:
            # a front / back pair sewn to a left / right one: no pair of twins is its mirror image
            notes.append(f"{sm['id']}: its pieces mirror in different planes")
            continue
        if any(tw[p] in ps for p in ps):
            continue                               # sews a pair's two halves to each other
        nm, why = _mirror_seam(spec, sm, None, None, outlines, tw)
        if nm is None:
            notes.append(f"{sm['id']}: {why}")
            continue
        hit = next((h for h in (_taken(spec, nm[k], outlines[nm[k]['piece']], ()) for k in ('a', 'b')
                                if 'line' not in nm[k]) if h), None)
        if hit:
            notes.append(f"{sm['id']}: its place on {nm['a']['piece']} is sewn by {hit} already")
            continue
        spec['seams'].append(nm)
        made.append(nm['id'])
    return made, notes


def mirror_seams_group(spec, made):
    """The seams of the pieces just copied (made = {original: its new twin}, draft.mirror_group) mirrored onto the
    copies, in one pass: a seam between two of them goes between their copies, one to a piece with a twin of its own
    goes to that twin, one to a single piece goes onto its other half (mirror_side's rules). Nothing is removed (the
    copies are new). Returns (ids made, notes: the seams not mirrored and why)."""
    copies = set(made.values())
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    src = [sm for sm in spec['seams'] if ({sm['a']['piece'], sm['b']['piece']} & set(made))
           and not ({sm['a']['piece'], sm['b']['piece']} & copies)]
    ids, notes, idmap = [], [], {}
    for sm in src:
        name = sm['a']['piece'] if sm['a']['piece'] in made else sm['b']['piece']
        nm, why = _mirror_seam(spec, sm, name, made[name], outlines, made)
        if nm is None:
            notes.append(f"{sm['id']}: {why}")
            continue
        spec['seams'].append(nm)
        ids.append(nm['id'])
        idmap[sm['id']] = nm['id']
    folds = spec.get('folds', [])
    for f in list(folds):
        if f.get('seam') in idmap:
            turn = f.get('turn')
            if turn is not None:
                turn = made.get(turn) or twin_in(spec, turn, S.pair_plane(spec, next(iter(made)))) or turn
            folds.append(dict(f, seam=idmap[f['seam']], **({'turn': turn} if turn is not None else {})))
    if folds:
        spec['folds'] = folds
    return ids, notes


def mirror_seams(spec, name):
    """Make the seams of `name`'s twin the mirror image of `name`'s (spec changed in place). Returns (twin, ids made,
    ids removed, notes: the seams of `name` not mirrored and why). Seams between the two twins are kept as they are."""
    tw = D.twin_of(spec, name)
    if tw is None:
        raise ValueError(rpt("{0} has no twin", name))
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    pieces = lambda sm: {sm['a']['piece'], sm['b']['piece']}
    removed = [sm['id'] for sm in spec['seams'] if tw in pieces(sm) and name not in pieces(sm)]
    src = [sm for sm in spec['seams'] if name in pieces(sm) and tw not in pieces(sm)]
    kept_aside = [sm for sm in spec['seams'] if sm['id'] in set(removed)]
    spec['seams'] = [sm for sm in spec['seams'] if sm['id'] not in set(removed)]
    made, notes, idmap, failed_to = [], [], {}, set()
    for sm in src:
        sides = {}
        for k in ('a', 'b'):
            sides[k], why = mirror_side(spec, sm[k], name, tw, outlines, skip={sm['id']})
            if sides[k] is None:
                notes.append(f"{sm['id']}: {why}")
                failed_to |= {_target(spec, p, name, tw)[0] for p in pieces(sm)} - {tw}
                break
        else:
            nm = {kk: v for kk, v in sm.items() if kk not in ('id', 'a', 'b', 'bom')}
            nm.update(id=_new_id(spec, sm['id']), a=sides['a'], b=sides['b'], source='user')
            if sm.get('path'):
                pl_ = S.pair_plane(spec, name)
                nm['path'] = dict(sm['path'], side=_swap_lr(sm['path'].get('side', 'auto'), pl_))
                if sm['path'].get('guide'):
                    nm['path']['guide'] = _mirror_guide(sm['path']['guide'], pl_)
            spec['seams'].append(nm)
            made.append(nm['id'])
            idmap[sm['id']] = nm['id']
    # a seam of name not mirrored: the twin keeps its own seams to that piece (removing them would only lose seams)
    back = [sm for sm in kept_aside if (pieces(sm) - {tw}) & failed_to]
    spec['seams'] += back
    removed = [x for x in removed if x not in {sm['id'] for sm in back}]
    # a seam only the twin has (to a piece name is not sewn to: a fly on one side) is kept -- a twin is not made
    # poorer than it was (pants, 2026-10-14: 4 seams of a one-sided piece were lost before this)
    src_to = {_target(spec, p, name, tw)[0] for sm in src for p in pieces(sm)}
    other = lambda sm: (pieces(sm) - {tw}) or {tw}          # a seam of the twin to itself: its other piece is itself
    own = [sm for sm in kept_aside if sm not in back and not (other(sm) & src_to)]
    spec['seams'] += own
    for sm in own:
        notes.append(f"{sm['id']} kept: only {tw} is sewn to {', '.join(sorted(other(sm)))}")
    removed = [x for x in removed if x not in {sm['id'] for sm in own}]
    gone = set(removed)
    folds = [f for f in spec.get('folds', []) if f.get('seam') not in gone]
    for f in list(folds):
        if f.get('seam') in idmap:
            turn = f.get('turn')
            if turn is not None:
                turn = _target(spec, turn, name, tw)[0]
            folds.append(dict(f, seam=idmap[f['seam']], **({'turn': turn} if turn is not None else {})))
    if 'folds' in spec:
        spec['folds'] = folds
    return tw, made, removed, notes


# ---------------------------------------------------------------- edits follow the mirror image

def _same_side(spec, outlines, o, m, tol=1.0):
    """o and m are the same place: one piece, the same internal line or overlapping stretches of the outline."""
    if o['piece'] != m['piece'] or ('line' in o) != ('line' in m):
        return False
    if 'line' in o:
        return o['line'] == m['line'] and abs(min(o['from'], o['to']) - min(m['from'], m['to'])) < 0.02
    ol = outlines[o['piece']]
    a0, a1 = _interval(ol, o)
    b0, b1 = _interval(ol, m)
    return any(min(a1, b1 + sh) - max(a0, b0 + sh) > min(tol, 0.5 * (a1 - a0)) for sh in (-ol.L, 0.0, ol.L))


def _mirror_ways(spec, sm):
    """The ways sm has a mirror image while its pieces are edited symmetrically ((name, tw, made) for _mirror_seam):
    both pieces in Symmetric Edit (each onto itself, as mirror_within), or both in pairs mirroring in one plane, not
    a seam between a pair's two halves (as mirror_onto_twins)."""
    ps = {sm['a']['piece'], sm['b']['piece']}
    ways = []
    if all(D.symmetric_edit(D.piece(spec, p)) is not None for p in ps):
        ways.append({p: p for p in ps})
    tw = {p: D.twin_of(spec, p) for p in ps}
    if (all(t is not None for t in tw.values()) and len({S.pair_plane(spec, p) for p in ps}) == 1
            and not any(tw[p] in ps for p in ps)):
        ways.append(tw)
    return ways


def mirror_partner(spec, sid):
    """The id of the seam that is seam sid's mirror image (Symmetric Edit / a left-right pair), else None."""
    sm = next((x for x in spec['seams'] if x['id'] == sid), None)
    if sm is None:
        return None
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    every = {x['id'] for x in spec['seams']}
    for made in _mirror_ways(spec, sm):
        nm, _ = _mirror_seam(spec, sm, None, None, outlines, made, skip=every)
        if nm is None:
            continue
        for x in spec['seams']:
            if x['id'] == sid:
                continue
            if ((_same_side(spec, outlines, x['a'], nm['a']) and _same_side(spec, outlines, x['b'], nm['b']))
                    or (_same_side(spec, outlines, x['a'], nm['b']) and _same_side(spec, outlines, x['b'], nm['a']))):
                return x['id']
    return None


def partners(spec, ids):
    """{seam id: its mirror image's id} for the seams ids that have one (read before an edit)."""
    out = {}
    for i in ids:
        p = mirror_partner(spec, i)
        if p is not None and p not in ids:
            out[i] = p
    return out


SYNC_KEYS = ('enabled', 'check', 'path', 'source')


def sync_partners(spec, pairs):
    """After seams were edited: each partner (pairs = partners() read before the edit) made the mirror image of its
    seam again -- its sides (moved ends, a flipped direction), On / Off, check and pull (left / right swapped).
    Its id, group and folds stay. Returns the partner ids rewritten."""
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    by = {x['id']: x for x in spec['seams']}
    done = []
    for sid, pid in pairs.items():
        sm, x = by.get(sid), by.get(pid)
        if sm is None or x is None:
            continue
        for made in _mirror_ways(spec, sm):
            nm, _ = _mirror_seam(spec, sm, None, None, outlines, made, skip={pid})
            if nm is None:
                continue
            # keep the partner's own a / b order (side A stays on the same piece)
            if x['a']['piece'] == nm['b']['piece'] and x['b']['piece'] == nm['a']['piece'] \
                    and x['a']['piece'] != x['b']['piece']:
                nm['a'], nm['b'] = nm['b'], nm['a']
            elif x['a']['piece'] == x['b']['piece'] and _same_side(spec, outlines, x['a'], nm['b']):
                nm['a'], nm['b'] = nm['b'], nm['a']
            x['a'], x['b'] = nm['a'], nm['b']
            for k in SYNC_KEYS:
                if k in nm:
                    x[k] = nm[k]
                else:
                    x.pop(k, None)
            done.append(pid)
            break
    return done


def delete_with_partners(spec, ids):
    """ids and their mirror images (to remove together)."""
    return set(ids) | set(partners(spec, ids).values())
