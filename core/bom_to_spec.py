# SPDX-License-Identifier: GPL-3.0-or-later
"""BOM.xml -> garment spec (no bpy).

The BOM gives every piece's outline and internal lines and every seam as two "seam lines"
(start line + ratio, end line + ratio). This turns them into the spec's edge + interval form.

Facts this relies on (measured against seams placed in 3D on the reference drape, 2026-09-28):
- A seam line that spans several outline lines runs in the BOM's own line order exactly when
  its Reverse flag is set, otherwise the other way round (67 / 67 over two garments). So the
  BOM alone fixes the path; the 3D drape is not needed.
- Ratios are arc-length fractions along the line's own point order.
- Copies of a piece (left / right, linked copies) share line GIDs, so a seam line can name more
  than one piece. Which copy is sewn to which is not in the BOM; those seams are paired by a
  rule here and flagged ``check`` for a person to confirm.
- Two sides sewn edge to edge run opposite ways round their pieces (each piece seen from its outside,
  the layout's left copies already mirrored). Measured on the raglan sleeves (2026-10-15): the BOM writes
  one seam per sleeve copy on the same sleeve line, against the front's left and right raglan edges, and
  only the copy that runs opposite to its edge is the one sewn there (checked on the reference 3D drape).
  Not true of everything (a sleeve placket is sewn the same way round), so it only picks between copies
  sewn to a piece that has no copies (see seams_from_bom).
"""
import collections
import copy
import hashlib
import os

import numpy as np
from ..i18n import tx

from . import bom_parse as B
from . import spec as S

N_LINE = 64      # samples per curved line (the outline density the file's own outline matched at 0.02 mm)


def _chain(piece):
    """Boundary lines in BOM order, each with 'rev' = its own point order runs against the chain."""
    pts = piece['boundary']['points']
    seq, last = [], None
    for ln in piece['boundary']['lines']:
        q = B.eval_line(ln, pts, N_LINE)
        rev = last is not None and np.allclose(last, q[-1], atol=1e-6) and not np.allclose(last, q[0], atol=1e-6)
        seq.append((ln, q, rev))
        last = q[0] if rev else q[-1]
    return seq


def _ratio_map(q_local, q_layout):
    """BOM ratios are arc-length fractions of the line in the piece's OWN (local) shape; a scaled instance
    (non-uniform R) moves them along the laid-out line. Returns (local fractions, layout fractions)."""
    a = np.r_[0, np.cumsum(np.linalg.norm(np.diff(q_local, axis=0), axis=1))]
    b = np.r_[0, np.cumsum(np.linalg.norm(np.diff(q_layout, axis=0), axis=1))]
    return a / max(a[-1], 1e-12), b / max(b[-1], 1e-12)


def _to_layout_ratio(rmap, r):
    return float(np.interp(r, rmap[0], rmap[1]))


def _piece_outline(piece):
    """ccw edges in layout mm, and per line gid: (edge id, own order flipped vs ccw). ccw_is_chain_fwd."""
    seq = _chain(piece)
    chain = [B.to_layout(piece, q[::-1] if rev else q) for _, q, rev in seq]
    ring = np.vstack([c[:-1] for c in chain])
    fwd = B.polygon_area(ring) >= 0
    edges, flip = [], {}
    items = list(zip(seq, chain))
    if not fwd:
        items = items[::-1]
    for (ln, q, rev), c in items:
        eid = f"L{ln['gid']}"
        P = c if fwd else c[::-1]
        edges.append({'id': eid, 'pts': np.round(P, 4).tolist()})
        flip[ln['gid']] = (eid, rev if fwd else not rev, _ratio_map(q, B.to_layout(piece, q)))
    return edges, flip, fwd


def _internals(piece):
    """Internal and base lines in layout mm (own point order) and their ratio maps."""
    out, where, rmaps = [], {}, {}
    for key in ('internals', 'baselines'):
        for sh in piece[key]:
            for ln in sh['lines']:
                ql = B.shape_to_piece(sh, B.eval_line(ln, sh['points'], 128))
                q = B.to_layout(piece, ql)
                eid = f"L{ln['gid']}"
                if eid in where:
                    continue
                where[eid] = len(out)
                rmaps[ln['gid']] = _ratio_map(ql, q)
                out.append({'id': eid, 'kind': key[:-1], 'hole': bool(sh['hole']), 'pts': np.round(q, 4).tolist()})
    return out, rmaps


def _side(piece_name, side, outl):
    """One BOM seam line on one piece -> spec SIDE (None if the lines are not on this piece)."""
    edges, flip, fwd, rmaps = outl
    ls, le = side['line_start'], side['line_end']
    if ls in flip and le in flip:
        (e0, f0, m0), (e1, f1, m1) = flip[ls], flip[le]
        t0, t1 = _to_layout_ratio(m0, side['r0']), _to_layout_ratio(m1, side['r1'])
        t0 = 1.0 - t0 if f0 else t0
        t1 = 1.0 - t1 if f1 else t1
        if ls == le:
            d = 1 if t1 >= t0 else -1
        else:
            chain_dir = 1 if side['reverse'] else -1          # measured: Reverse -> BOM line order
            d = chain_dir if fwd else -chain_dir
        return {'piece': piece_name, 'from': [e0, round(t0, 6)], 'to': [e1, round(t1, 6)], 'dir': d}
    if ls == le and ls in rmaps:
        return {'piece': piece_name, 'line': f"L{ls}", 'from': round(_to_layout_ratio(rmaps[ls], side['r0']), 6),
                'to': round(_to_layout_ratio(rmaps[ls], side['r1']), 6)}
    return None


def _runs_with(spec, side, outlines):
    """+1 if the piece lies on the left of the side's from -> to direction (it runs with the piece's
    counter-clockwise outline), -1 if on the right, 0 if too short to tell."""
    P = S.side_polyline(spec, side, outlines, step=2.0)
    if len(P) < 3:
        return 0
    k = len(P) // 2
    t = P[k + 1] - P[k - 1]
    t = t / (np.linalg.norm(t) + 1e-12)
    q = P[k] + np.array([-t[1], t[0]])                 # 1 mm to the left
    Bd = np.asarray(outlines[side['piece']].B, float)
    A, Bn = Bd, np.roll(Bd, -1, 0)
    cross = (A[:, 1] > q[1]) != (Bn[:, 1] > q[1])
    xs = A[:, 0] + (q[1] - A[:, 1]) * (Bn[:, 0] - A[:, 0]) / np.where(Bn[:, 1] == A[:, 1], 1e-12, Bn[:, 1] - A[:, 1])
    return 1 if np.count_nonzero(cross & (xs > q[0])) % 2 else -1


LEN_RATIO = 1.43     # the two sides of a real seam: lengths within 1 : 1.43 (measured: this drops 28 of 40 false pairs)


def _copy_family(piece):
    """Copies of a piece (left / right, linked copies) have the same own outline points."""
    q = np.ascontiguousarray(np.round(piece['boundary']['points'], 2))
    return hashlib.md5(q.tobytes()).hexdigest()[:12]


def _pieces(bom):
    P = bom['pieces']
    cnt = collections.Counter(p['uname'] for p in P)
    return [p['uname'] if cnt[p['uname']] == 1 else f"{p['uname']}@{p['idx']}" for p in P]


def convert(bom_path, name=None):
    """BOM -> spec with every piece (all enabled), split into garments (garments_from_bom), and no seams yet.
    Returns (spec, report lines)."""
    bom = B.load_bom(bom_path)
    spec = S.new_spec(name or os.path.splitext(os.path.basename(bom_path))[0],
                      {'kind': 'bom', 'path': bom_path})
    for p, lab in zip(bom['pieces'], _pieces(bom)):
        edges, _, _ = _piece_outline(p)
        internals, _ = _internals(p)
        spec['pieces'].append({'name': lab, 'fabric': p['fabric'], 'edges': edges, 'internals': internals,
                               'mirrored': bool(np.linalg.det(p['R']) < 0), 'enabled': True,
                               'bom': {'idx': p['idx'], 'inst_gid': p['inst_gid'], 'family': _copy_family(p),
                                       'arrangement': p.get('arrangement', '')}})
    rep = assign_garments(spec, bom)
    return spec, [f"{len(bom['pieces'])} pieces, {len(bom['seams'])} BOM seams"] + rep


# ---------------------------------------------------------------- garments
# A BOM of a project with several outfits reuses piece and line numbers across them (the exporting tool writes a piece added from
# another file with its own numbers: 116 of 133 pieces shared an instance number, 234 of 443 seams name a line more
# than one piece carries, 2026-10-25). The BOM has no field for the garment, so the seams' partners are looked for
# within a garment, not among the pieces that are on: switching pieces on and off then never changes a seam (Fable
# review REVIEW_fable_bom_seam_ownership_2026-10-25, user OK 10/25)
SHAKY = 2.0       # a seam joined to the nearest garment in the layout when the next one is less than this much farther


def garments_from_bom(bom):
    """[(piece indices, shaky piece indices)] the BOM's pieces split into garments, largest first: 1) pieces sewn by a
    seam whose both sides name one piece each are one garment; 2) a seam with one side on one piece and the other on
    several: the family (copies of one piece) nearest to that piece in the 2D layout joins its garment (shaky when the
    next family is less than SHAKY times as far); 3) a piece left alone is a garment by itself. Seams with both sides
    on several pieces are not used (on the measured jacket they joined 35 wrong seams)."""
    P = bom['pieces']
    n = len(P)
    fam = {i: _copy_family(p) for i, p in enumerate(P)}
    T = np.array([np.asarray(p['T'], float)[:2] for p in P]) if n else np.zeros((0, 2))
    own = [(B.owners(bom, s['sides'][0]), B.owners(bom, s['sides'][1])) for s in bom['seams']]
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for oa, ob in own:
        if len(oa) == 1 and len(ob) == 1:
            par[find(oa[0])] = find(ob[0])
    shaky = set()
    for _ in range(5):                       # a join can bring a later seam's partner into the garment
        changed = False
        for oa, ob in own:
            if (len(oa) == 1) == (len(ob) == 1) or not oa or not ob:
                continue
            u, cands = (oa[0], ob) if len(oa) == 1 else (ob[0], oa)
            if any(find(c) == find(u) for c in cands):
                continue
            fams = collections.defaultdict(list)
            for c in cands:
                fams[fam[c]].append(c)
            d = {f: min(float(np.linalg.norm(T[c] - T[u])) for c in cs) for f, cs in fams.items()}
            f0 = min(d, key=d.get)
            ds = sorted(d.values())
            if len(ds) > 1 and ds[1] < SHAKY * max(ds[0], 1e-9):
                shaky.update(fams[f0])
            for c in fams[f0]:
                par[find(c)] = find(u)
            changed = True
        if not changed:
            break
    comp = collections.defaultdict(list)
    for i in range(n):
        comp[find(i)].append(i)
    out = sorted(comp.values(), key=lambda c: (-len(c), min(c)))
    return [(set(c), {i for i in c if i in shaky}) for c in out]


def assign_garments(spec, bom=None):
    """Split a BOM spec's pieces into garments (garments_from_bom): pieces[].garment = an id of spec['garments']
    [{'id', 'name', 'source': 'bom', 'check'}]. A piece alone is a garment of its own named after it. Returns report
    lines."""
    bom = bom or B.load_bom(spec['source']['path'])
    by_idx = {pc['bom']['idx']: pc for pc in spec['pieces'] if 'bom' in pc}
    spec['garments'] = []
    k = 0
    loose = 0
    for idx, shaky in garments_from_bom(bom):
        pcs = [by_idx[i] for i in sorted(idx) if i in by_idx]
        if not pcs:
            continue
        k += 1
        gid = f"G{k}"
        alone = len(pcs) == 1
        loose += alone
        spec['garments'].append({'id': gid, 'name': pcs[0]['name'] if alone else f"Garment {k}", 'source': 'bom',
                                 'check': (f"joined by the layout, not sure: {', '.join(by_idx[i]['name'] for i in sorted(shaky))}"
                                           if shaky else '')})
        for pc in pcs:
            pc['garment'] = gid
    n_big = len(spec['garments']) - loose
    return [f"  {n_big} garments of 2 pieces or more, {loose} pieces alone"]


def rebuild_seams(spec, garments=None, bom_path=None):
    """Remake the BOM seams of the garments `garments` (ids; None = all), each between its own pieces, in place: the
    other garments' BOM seams and the seams a person added, flipped or confirmed (source 'user') stay. A spec read
    before garments existed is split first and all its BOM seams remade. A seam the BOM gives in two garments (their
    lines share numbers) keeps its id in the first and gets "~<garment>" in the next. Returns report lines."""
    path = bom_path or spec['source'].get('path', '')
    rep = []
    if not spec.get('garments'):
        rep += assign_garments(spec)
        garments = None
    G = garment_pieces(spec)
    todo = list(G) if garments is None else [g for g in G if g in set(garments)]
    gone = set(todo)
    # a BOM seam stays when its garment is kept and still there (a garment emptied by Garment from Selection: gone)
    kept = [sm for sm in spec['seams'] if sm.get('source') != 'bom' or
            (garments is not None and (sm.get('bom') or {}).get('garment', '') in set(G) - gone)]
    # what a person set on a BOM seam that is remade (switched off by buttons, its path round the body, ...) stays:
    # the remade seam with the same BOM seam between the same pieces takes the old one's id (folds, attachments,
    # buttons point at it) and every key the BOM does not make (design review 2026-10-25: Garment from Selection is
    # pressed freely, Use Selected Pieces lost these too)
    kept_ids = {id(sm) for sm in kept}
    old = {((sm.get('bom') or {}).get('gid'), tuple((sm.get('bom') or {}).get('pieces', ()))): sm
           for sm in spec['seams'] if id(sm) not in kept_ids and sm.get('source') == 'bom'}
    used = {sm['id'] for sm in kept}
    new = []
    stats = collections.Counter()
    for g in todo:
        if not G[g]:
            continue
        seams, r = seams_from_bom(spec, path, names=set(G[g]))
        for line in r[1:]:
            k, _, v = line.strip().rpartition(': ')
            if v.isdigit():
                stats[k] += int(v)
        for sm in seams:
            was = old.pop((sm['bom']['gid'], tuple(sm['bom']['pieces'])), None)
            if was is not None and was['id'] not in used:
                sm['id'] = was['id']
                sm['enabled'] = was.get('enabled', True)
                for key_, v in was.items():
                    if key_ not in sm:
                        sm[key_] = v
                stats['person\'s settings kept'] += 1
            if sm['id'] in used:
                sm['id'] = f"{sm['id']}~{g}"
            if sm['id'] in used:
                continue                       # a person's seam of that id (it replaced the BOM's)
            sm['bom']['garment'] = g
            used.add(sm['id'])
            new.append(sm)
    spec['seams'] = kept + new
    rep.append(f"{len(todo)} garment(s): {len(new)} BOM seams")
    rep += [f"  {k}: {v}" for k, v in sorted(stats.items())]
    return rep


ALONE = '*alone'      # garment_pieces(spec, ALONE): the pieces that are a garment by themselves, as one selection


def garment_pieces(spec, only=None):
    """{garment id: [piece names]} in spec order; a piece without a garment is in '' (one garment: a spec not split).
    only: that garment's names alone ([] if none; ALONE: every piece alone in its garment)."""
    out = collections.OrderedDict((g['id'], []) for g in spec.get('garments', []))
    for pc in spec['pieces']:
        out.setdefault(pc.get('garment', ''), []).append(pc['name'])
    out = collections.OrderedDict((g, ns) for g, ns in out.items() if ns)
    if only is None:
        return out
    if only == ALONE:
        return [ns[0] for ns in out.values() if len(ns) == 1]
    return out.get(only, [])


def refresh_seams(spec, fresh=None):
    """Return an updated copy, never changing the input. Only compatible BOM patterns may keep a drape.

    Manual groups and seam references take precedence over new automatic ownership. This is deliberately
    separate from rebuild_seams: callers that explicitly regroup pieces keep that function's contract.
    """
    fresh = fresh if fresh is not None else convert(spec['source']['path'])[0]
    if spec.get('units') != fresh.get('units'):
        raise ValueError(tx('BOM units changed; use Import BOM instead'))
    before = {p['name']: p for p in spec['pieces']}
    after = {p['name']: p for p in fresh['pieces']}
    if len(before) != len(spec['pieces']) or before.keys() != after.keys():
        raise ValueError(tx('BOM pieces changed; use Import BOM instead'))
    def same_pattern(p, q):
        # A BOM layout translation can change automatic ownership without changing any pattern shape or reference.
        shift = np.asarray(q['edges'][0]['pts'][0]) - np.asarray(p['edges'][0]['pts'][0])
        for key in ('edges', 'internals'):
            aa, bb = p.get(key, []), q.get(key, [])
            if len(aa) != len(bb):
                return False
            for a, b in zip(aa, bb):
                if {k: v for k, v in a.items() if k not in ('pts', 'ctrl')} != {k: v for k, v in b.items() if k not in ('pts', 'ctrl')}:
                    return False
                for coords in ('pts', 'ctrl'):
                    x, y = a.get(coords, []), b.get(coords, [])
                    if len(x) != len(y) or (len(x) and not np.allclose(np.asarray(x) + shift, y, atol=0.0001, rtol=0)):
                        return False
        return True
    for name, p in before.items():
        q = after[name]
        if (not same_pattern(p, q) or p.get('mirrored') != q.get('mirrored') or
                any(p.get('bom', {}).get(k) != q.get('bom', {}).get(k)
                    for k in ('idx', 'inst_gid', 'family'))):
            raise ValueError(tx('BOM pattern or piece identity changed: {0}; use Import BOM instead', name))
    result = copy.deepcopy(spec)
    pcs = {p['name']: p for p in result['pieces']}
    old_groups = {g['id']: g for g in spec.get('garments', [])}
    members = garment_pieces(spec)
    manual = {gid for gid, g in old_groups.items() if g.get('source') == 'user'}
    claimed = {n for gid in manual for n in members.get(gid, [])}
    groups = [copy.deepcopy(g) for gid, g in old_groups.items() if gid in manual]
    used = set(old_groups) | {g['id'] for g in fresh.get('garments', [])}
    fresh_info = {g['id']: g for g in fresh.get('garments', [])}
    for gid, names in garment_pieces(fresh).items():
        names = set(names) - claimed
        if not names:
            continue
        match = next((g for k, g in old_groups.items() if k not in manual and set(members.get(k, [])) == names), None)
        if match:
            group = copy.deepcopy(match)
            group['check'] = fresh_info[gid].get('check', '')
        else:
            # Automatic default names are expendable; a person's renamed group must not silently vanish.
            for k, g in old_groups.items():
                if k in manual or not (set(members.get(k, [])) & names):
                    continue
                default = g.get('name') in (f"Garment {k[1:]}", *(members.get(k, []) if len(members.get(k, [])) == 1 else []))
                if not default:
                    raise ValueError(tx('A renamed garment would be split or merged: {0}', g.get('name')))
            i = 1
            while f'G{i}' in used:
                i += 1
            used.add(f'G{i}')
            group = dict(copy.deepcopy(fresh_info[gid]), id=f'G{i}')
        groups.append(group)
        for n in names:
            pcs[n]['garment'] = group['id']
    result['garments'] = groups
    report = rebuild_seams(result)
    old_by = {s['id']: s for s in spec['seams']}
    new_by = {s['id']: s for s in result['seams']}
    def signature(s):
        return ((s.get('bom') or {}).get('gid'), tuple((s.get('bom') or {}).get('pieces', [])))
    def sides(s):
        return sorted(S.dumps(s[k]) for k in ('a', 'b'))
    users = [s for s in spec['seams'] if s.get('source') == 'user']
    user_bom = {signature(s) for s in users if s.get('bom')}
    result['seams'] = [s for s in result['seams'] if s.get('source') != 'bom' or
                       (signature(s) not in user_bom and not any(sides(s) == sides(u) for u in users))]
    new_by = {s['id']: s for s in result['seams']}
    # A disappearing id must never be recycled for another BOM pairing.
    for sid in old_by.keys() & new_by.keys():
        if old_by[sid].get('source') == 'bom' and signature(old_by[sid]) != signature(new_by[sid]):
            i = 1
            target = sid + '~new'
            while target in old_by or target in new_by:
                target = sid + f'~new{i}'
                i += 1
            new_by[sid]['id'] = target
    new_ids = {s['id'] for s in result['seams']}
    gone = old_by.keys() - new_ids
    ordinary = {'id', 'a', 'b', 'source', 'enabled', 'check', 'bom'}
    def refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('seam', 'seam_id') and isinstance(item, str) and item in gone:
                    yield item
                yield from refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from refs(item)
    protected = set(refs({k: v for k, v in spec.items() if k != 'seams'}))
    protected |= {sid for sid in gone if not old_by[sid].get('enabled', True) or set(old_by[sid]) - ordinary}
    if protected:
        raise ValueError(tx('BOM update would remove seams with settings or references: {0}', ', '.join(sorted(protected)[:8])))
    issues = S.check(result)
    if issues:
        raise ValueError(tx('BOM seam references are incompatible: {0}', '; '.join(issues[:3])))
    return result, report


def _closes_band(spec, sa, sb, outlines):
    """A piece sewn to itself end to end (a waistband's ends): both ends of the two sides lie a piece's width apart.
    The waistband's own closing seam was switched off with the unreal ones, and the band, laid round the waist
    stretched to close, sprang open through the body and dragged the trousers in (2026-09-28)."""
    pa = S.side_polyline(spec, sa, outlines, step=5.0)
    pb = S.side_polyline(spec, sb, outlines, step=5.0)
    w = float(np.ptp(outlines[sa['piece']].B[:, 0]))
    return min(np.linalg.norm(pa[0] - pb[0]), np.linalg.norm(pa[-1] - pb[-1])) >= 0.8 * w


def seams_from_bom(spec, bom_path=None, names=None):
    """The BOM's seams between the pieces `names` (one garment's; default: the spec's ENABLED pieces), as spec seams.
    Returns (seams, report lines).

    Rules, measured on a BOM that holds a whole project (133 pieces, Line GIDs reused across outfits)
    against the seams placed in 3D, restricted to one garment's 20 pieces: 79 / 79 found, 12 extra
    (lines whose GID is shared with another outfit's piece: not decidable from the BOM, flagged).
      1. a side is on a piece only if its start and end lines are on that piece's outline
         (or it is one internal line of that piece)
      2. the two sides' lengths are within LEN_RATIO
      3. if the same piece carries both sides, it is sewn to itself (a dart, a band closing on itself)
      4. otherwise the pair of copy families with the most candidates; left copies to left copies,
         except copies sewn to a piece without copies (a raglan sleeve to the one front): the copy whose
         side runs opposite to the other side (all the sleeve seams went to one sleeve before, 2026-10-15)
    """
    bom = B.load_bom(bom_path or spec['source']['path'])
    P = bom['pieces']
    label = _pieces(bom)
    on = {pc['bom']['idx'] for pc in spec['pieces'] if 'bom' in pc and
          (pc['name'] in names if names is not None else pc.get('enabled', True))}
    outl = {}
    for i in on:
        edges, flip, fwd = _piece_outline(P[i])
        _, rmaps = _internals(P[i])
        outl[i] = (edges, flip, fwd, rmaps)
    outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces'] if 'bom' in pc and pc['bom']['idx'] in on}
    mirrored = {i: np.linalg.det(P[i]['R']) < 0 for i in on}
    family = {i: _copy_family(P[i]) for i in on}
    n_copies = collections.Counter(family.values())
    stats = collections.Counter()
    seams = []
    for sm in bom['seams']:
        sa, sb = sm['sides']
        A_all, B_all = B.owners(bom, sa), B.owners(bom, sb)
        A, Bo = [x for x in A_all if x in on], [x for x in B_all if x in on]
        cand = []
        for pa in A:
            xa = _side(label[pa], sa, outl[pa])
            if xa is None:
                continue
            for pb in Bo:
                xb = _side(label[pb], sb, outl[pb])
                if xb is None or (pa == pb and xa == xb):
                    continue
                la, lb = S.side_length(spec, xa, outlines), S.side_length(spec, xb, outlines)
                r = la / max(lb, 1e-9)
                cand.append((pa, pb, xa, xb, r))
        if not cand:
            continue
        # the lengths: a pair within LEN_RATIO is preferred over one past it (the copies of a piece, Line GIDs reused
        # across outfits), but a seam whose every pair is past it is kept, switched off and marked (user 2026-11-02,
        # DECISIONS cloth-sim-no-auto-judgment-default-rules-2026-11-02: a gather, a tuck's mouth or a sleeve cap
        # eased in hard is longer on one side than 1.43 and was dropped without a word)
        near = [c for c in cand if 1 / LEN_RATIO < c[4] < LEN_RATIO]
        stats['candidate pairs set aside: lengths differ'] += len(cand) - len(near)
        far = not near
        cand = near or cand
        selfs = [c for c in cand if c[0] == c[1]]
        if selfs:
            picked = selfs
        else:
            by = collections.defaultdict(list)
            for c in cand:
                by[(family[c[0]], family[c[1]])].append(c)
            best = max(by.values(), key=len)
            na, nb = len({c[0] for c in best}), len({c[1] for c in best})
            single = (nb == 1 and n_copies[family[best[0][1]]] == 1) if na > 1 else                      (na == 1 and n_copies[family[best[0][0]]] == 1) if nb > 1 else False
            edges_only = all('line' not in c[2] and 'line' not in c[3] for c in best)
            if single and edges_only:                  # copies sewn edge to edge to a piece with no copies
                def key(c):
                    run = _runs_with(spec, c[2], outlines) * _runs_with(spec, c[3], outlines)
                    return (run != -1, mirrored[c[0]] != mirrored[c[1]], abs(np.log(c[4])))
            else:
                def key(c):
                    return (mirrored[c[0]] != mirrored[c[1]], abs(np.log(c[4])))
            picked, ua, ub = [], set(), set()
            for c in sorted(best, key=key):
                if c[0] in ua or c[1] in ub:
                    continue
                picked.append(c); ua.add(c[0]); ub.add(c[1])
        shared = len(A_all) > 1 or len(B_all) > 1
        # a piece sewn to itself through lines no piece claims (RefPatternGID 0 on both sides, lines shared by
        # several pieces): 12 of 17 such seams on a 20-piece garment were not real, and all 12 were these. They
        # are left out, except one that closes a band end to end (the real ones among the 17 were that and four
        # darts on claimed lines). They used to come in switched off, and were taken for seams that would be sewn
        # (a waistband's lower edge sewn to itself, user 2026-10-25).
        generic = all(s_['ref_pattern_gid'] == 0 and len(B.owners(bom, s_)) > 2 for s_ in (sa, sb))
        stats['BOM seams used'] += 1
        for j, c in enumerate(picked):         # ids by place in picked: the same as before seams were left out
            if c[0] == c[1] and generic and not _closes_band(spec, c[2], c[3], outlines):
                stats['left out: self seam on unclaimed lines'] += 1
                continue
            stats['flagged: line shared with other pieces'] += int(shared)
            stats['off: lengths differ past 1:1.43 (check it)'] += int(far)
            seams.append({'id': f"S{sm['gid']}" + (f".{j}" if len(picked) > 1 else ''),
                          'a': c[2], 'b': c[3], 'source': 'bom', 'enabled': not far,
                          'check': (f'lengths differ x{max(c[4], 1 / c[4]):.2f}: switched off, check it' if far else
                                    'shared line: other pieces use the same line number' if shared else ''),
                          'bom': {'gid': sm['gid'], 'pieces': [int(c[0]), int(c[1])],
                                  'group': sm.get('group')}})   # one BOM group = one M:N seam (core/seam_edit.py)
    rep = [f"{len(on)} pieces: {len(seams)} seams from {stats['BOM seams used']} BOM seams"]
    rep += [f"  {k}: {v}" for k, v in sorted(stats.items())]
    return seams, rep
