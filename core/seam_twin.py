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
import itertools
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


def _mirror_seam(spec, sm, name, tw, outlines, made=None, skip=(), plane=None):
    """The seam sm mirrored (a new seam dict, added to nothing), or (None, why). skip: seams whose place the mirror
    image may land on (its own partner, when an edit is carried over). plane: the mirror's plane when known (the left
    / right image of a seam just added), else that of `name`'s pair (of side A's piece without one)."""
    sides = {}
    for k in ('a', 'b'):
        sides[k], why = mirror_side(spec, sm[k], name, tw, outlines, skip={sm['id']} | set(skip), made=made)
        if sides[k] is None:
            return None, why
    nm = {kk: v for kk, v in sm.items() if kk not in ('id', 'a', 'b', 'bom')}
    nm.update(id=_new_id(spec, sm['id']), a=sides['a'], b=sides['b'], source='user')
    pl_ = plane or (S.pair_plane(spec, name) if name else S.pair_plane(spec, sm['a']['piece']))
    if sm.get('path'):
        nm['path'] = dict(sm['path'], side=_swap_lr(sm['path'].get('side', 'auto'), pl_))
        if sm['path'].get('guide'):
            nm['path']['guide'] = _mirror_guide(sm['path']['guide'], pl_)
    return nm, None


def _lr_counterpart(spec, p):
    """p's left / right counterpart, ('twin', name) for the On twin of a left / right Mirror Copy pair (the seam's
    image goes onto it; also a Pair's partner), ('blocked', why) for a counterpart the image cannot go onto (an Off
    twin or partner, the garment's symmetry off), (None, None) for a piece with none (its image is itself, across
    its center line). Symmetric Edit says a piece's outline is symmetric, not that it lies on the body's center: a
    side panel symmetric in itself, with its twin on the other side of the body, mirrors onto that twin, never onto
    itself (user 2026-11-17: a box's front sewn to the right side panel got its left edge sewn to that panel too)."""
    pc = D.piece(spec, p)
    if S.plane_of(pc) != 'x':
        return None, None                          # a front / back pair: no left / right counterpart
    t = twin_in(spec, p, 'x')
    if t is not None:
        return 'twin', t
    fam = D.family_of(pc)
    if fam and any(D.family_of(o) == fam for o in spec['pieces'] if o['name'] != p):
        return 'blocked', f"{p}'s twin is Off" if spec.get('symmetry') != 'off' else "the garment's symmetry is off"
    for pr in spec.get('pairs', []):
        if p in pr and len(set(pr)) == 2:
            o = next(n for n in pr if n != p)
            op = next((x for x in spec['pieces'] if x['name'] == o), None)
            if op is None or not op.get('enabled', True) or spec.get('symmetry') == 'off':
                return 'blocked', f"{p}'s Pair partner {o} is Off" if op is not None else f"{o} is gone"
            # a Pair: two pieces drawn separately, placed as mirror images (a front / back Pair has its plane and left
            # above). The image goes onto the partner when the mirrored ends land on its outline, as long
            # (mirror_side's checks); else (a Pair from before the plane, front / back) the piece mirrors onto itself
            # (lr_images)
            return 'pair', o
    return None, None


def lr_images(spec, sm):
    """The ways seam sm has a left / right mirror image, or ([], why): a list of groups, each a list of maps {piece:
    its piece in the image} (for _mirror_seam) to try in turn, the first that works being the image. A piece goes to
    its twin when it has one, onto itself (across its center line) when it has no left / right counterpart and is
    edited symmetrically (Symmetric Edit); a piece in a Pair to its partner, else (the Pair a front / back one) onto
    itself. Not when a piece has a counterpart the image cannot go onto, or none and is not symmetric (a collar drawn
    freely: user 2026-11-04), not between a pair's two halves (a center front), not with the garment's symmetry off.
    A seam within one piece that has a twin and is edited symmetrically too (a dart) has its image across the piece's
    own center line as well (user 2026-11-17: kept as it was)."""
    if spec.get('symmetry') == 'off':
        return [], None
    ps = sorted({sm['a']['piece'], sm['b']['piece']})
    alts = {}
    for p in ps:
        kind, v = _lr_counterpart(spec, p)
        sym = D.symmetric_edit(D.piece(spec, p)) is not None
        if kind == 'twin':
            alts[p] = [v]
        elif kind == 'pair':
            alts[p] = [v] + ([p] if sym else [])
        elif kind == 'blocked':
            return [], v
        elif sym:
            alts[p] = [p]
        else:
            return [], None
    group = []
    for combo in itertools.product(*(alts[p] for p in ps)):
        tgt = dict(zip(ps, combo))
        if not any(tgt[p] in ps and tgt[p] != p for p in ps):      # not between a pair's two halves (a center front)
            group.append(tgt)
    ways = [group] if group else []
    if len(ps) == 1:
        p = ps[0]
        if alts[p][0] != p and D.symmetric_edit(D.piece(spec, p)) is not None:
            ways.append([{p: p}])
    return ways, None


def _same_place(spec, outlines, sm, nm):
    """nm lands where sm is (a seam across the center line is its own mirror image)."""
    return ((_same_side(spec, outlines, sm['a'], nm['a']) and _same_side(spec, outlines, sm['b'], nm['b']))
            or (_same_side(spec, outlines, sm['a'], nm['b']) and _same_side(spec, outlines, sm['b'], nm['a'])))


def mirror_added(spec, ids):
    """Seams `ids` just added (Add Seam, Add M:N): each gets its left / right mirror image (lr_images: onto the twins
    of pieces in left / right pairs, across the center line of single pieces edited symmetrically; user 2026-10-27
    b-3, 2026-11-04, 2026-11-17), unless it is its own mirror image or the place is sewn already. Front / back pairs
    get no image here (user 2026-11-17: left / right only for now). Returns (ids made, notes: the seams not mirrored
    and why)."""
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    made, notes = [], []
    for sm in [x for x in spec['seams'] if x['id'] in set(ids)]:
        ways, why = lr_images(spec, sm)
        if why:
            notes.append(f"{sm['id']}: {why}")
        for group in ways:
            nm = None
            for tgt in group:
                nm, why = _mirror_seam(spec, sm, None, None, outlines, tgt, plane='x')
                if nm is not None:
                    break
            if nm is None:
                notes.append(f"{sm['id']}: {why}")
                continue
            if _same_place(spec, outlines, sm, nm):
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
    """The maps under which sm has a mirror image (for _mirror_seam): the same as a seam just added gets (lr_images),
    so an edit follows the image Add Seam made."""
    return [tgt for group in lr_images(spec, sm)[0] for tgt in group]


def mirror_partner(spec, sid):
    """The id of the seam that is seam sid's mirror image (Symmetric Edit / a left-right pair), else None."""
    sm = next((x for x in spec['seams'] if x['id'] == sid), None)
    if sm is None:
        return None
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    every = {x['id'] for x in spec['seams']}
    for made in _mirror_ways(spec, sm):
        nm, _ = _mirror_seam(spec, sm, None, None, outlines, made, skip=every, plane='x')
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
            nm, _ = _mirror_seam(spec, sm, None, None, outlines, made, skip={pid}, plane='x')
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
