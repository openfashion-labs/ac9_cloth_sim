# SPDX-License-Identifier: GPL-3.0-or-later
"""Fold a placed piece over one of its internal lines before simulating, by the line's own fold angle, with no
physics (user 2026-10-22, DECISIONS cloth-sim-place-tools-fold-lay-attach-2026-10-22; Fable
docs/reviews/REVIEW_fable_place_tools_r4_2026-10-22.md). Live and Simulate then start from the folded shape: the
fold angle set on the line keeps pulling it there while the cloth moves (the solver's fold_deg).

The fold is on the placed object's shape as made (its move as a whole, the pose, is taken off and put back): a line
straight in 3D (a vertical line on the cylinder) turns its side rigidly about the line, any angle; a curved line, and
a fold of FLAT_DEG or more (laid back flat), the collar's way (dress.prefold, developed round the guide's vertical
axis: at 45 degrees it stretched a back's side 1.29 x and came out 61, rigid about the chord 1.0 x and the angle set). The object's vertices and their made-at positions
(GEN_ATTR) are rewritten and it gets FOLD_PROP: placed_view.is_moved then counts it as the user's shape, so a
Simulate takes it as a hand move does and folds nothing again. Back to Placed Shape drops it with the object.

Not folded (one line each, docs/testing/RESULT_place_tools_stage0_2026-10-22.md): a line that does not cut the piece
apart (a pocket's position line: the two sides stay joined round its ends and tear, edges up to 75 x; Local Sim
folds it, user 2026-10-23), a piece whose object was edited vertex by vertex, an old collar line with no Turns
(turned when placed, dress.py collar_prefold; with Turns set it is folded here at any angle, user 2026-11-02). A piece on an arm or leg guide is folded in the guide's own frame (user 2026-10-23, DECISIONS
cloth-sim-phase-live-local-sim-2026-10-23): its cylinder's axis there stands where the torso's stands, so the turn
and the collar's way go round the arm as they go round the body. Into the body is not checked: the viewport shows it
(user 2026-10-22).

A line folded already is folded again from its shape before that fold (0 degrees, kept in BEFORE_ATTR) at the angle
set now, a replacement (user 2026-10-22: 45 degrees to look, then 180); its side is the one kept in the spec unless
picked again, and folds made after it on the piece are undone.

The side that turns (user 2026-10-24): A is the left of an internal line walked from its first point to its last on the
flat pattern, B the right -- the right and the left the other way round on a line whose hand is -1, the mirror image
of another line (core/fold_hand.py, 2026-11-19: A on a mirror pair is the same side); the spec's fold keeps it ("side": "A" / "B", only the adjusted shape uses it - the solver
pulls both sides to the angle). The Tool tab is the only way in (user 2026-10-24, no Fold button): its Fold Angle,
Direction and Moves fold the adjusted shape at once, the first time too; A and B are shown on the pattern and on the
adjusted shape, and a line it cannot fold shows why with Local Sim to see it."""
from . import prepare_build as PB
import json

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import placed_view as PLV
from . import store
from .core import spec as S
from .core import fold_hand as FH
from .core import fold_resolve as FR
from .i18n import tx
from . import selection as SEL

FOLD_PROP = PLV.FOLD_PROP   # on a placed object: JSON list of {"line", "deg", "side"} folded so far
TEAR_MIN = 10               # edges joining the two sides past the line's ends allowed (a vent's line stops short
TEAR_SHARE = 0.05           # of the hem): max(TEAR_MIN, this share of the edges on the line)
AXIS_GUIDES = ('torso', 'neck')   # vertical axes: folded in world space as before; the others in the guide's frame
STRAIGHT_MM = 2.0           # a line this close to its chord in 3D turns its side rigidly about the chord
FLAT_DEG = 170.0            # from this angle the side is laid back flat round a small bend (dress.prefold)
STRETCH_MAX = 1.05          # an edge longer than this x its pattern length when folded: said and red (the placement
                            # rule, DECISIONS cloth-sim-placement-no-stretch-rule-2026-10-08)
BEFORE_ATTR = "ac9_fold_before_"   # + k: a placed object's vertices before its fold k, to fold that line again
SEAM_KEY = "seam:"          # + seam id: a fold at a seam in FOLD_PROP's "line" (the turning piece's own seam edge)


def last_side(ob, line):
    """The side line was turned on when last folded, or -1."""
    return next((int(d.get('side', 0)) for d in folded(ob) if d.get('line') == line), -1)


def spec_side(spec, piece, line):
    """'A' / 'B' / 'NONE' kept on the line's fold; Python None is the legacy automatic A default."""
    piece, line = FR.origin(spec, piece, line)
    f = next((f for f in (spec or {}).get('folds', []) if f.get('piece') == piece and f.get('line') == line), {})
    return f.get('side') if f.get('side') in ('A', 'B', 'NONE') else None


def side_index(spec, piece, line, info):
    """The index into info['sides'] that turns: the spec's A / B, else the side last folded on the object, else A."""
    a = info.get('a_side')
    ab = spec_side(spec, piece, line)
    if ab is not None and a is not None:
        return a if ab == 'A' else 1 - a
    k = last_side(info['ob'], line) if info.get('ob') is not None else -1
    if k >= 0:
        return k
    return a if a is not None else 0


def side_ab(info, k):
    """'A' or 'B' for the index k into info['sides'] (A the index 0 when the line could not be told apart)."""
    a = info.get('a_side')
    return 'A' if k == (0 if a is None else a) else 'B'


def signed_to_line(P, L):
    """(signed distance, distance) of the points P (n, 2) to the polyline L (m, 2), by its nearest segment: + on the
    left walked from L[0] to L[-1] (side A)."""
    a, b = L[:-1], L[1:]
    ab = b - a
    q = P[:, None, :] - a[None]
    t = np.clip((q * ab[None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-12)[None], 0, 1)
    d = np.linalg.norm(q - ab[None] * t[..., None], axis=2)
    j = d.argmin(1)
    k = np.arange(len(P))
    sd = (ab[j, 0] * q[k, j, 1] - ab[j, 1] * q[k, j, 0]) / np.maximum(np.linalg.norm(ab[j], axis=1), 1e-12)
    return sd, d[k, j]


def _a_side(V, L, roll, sides, hand=1):
    """Which of sides (two index arrays into V, flat mm) is A: the one on the left of L (hand +1) or on its right
    (hand -1, core/fold_hand.py), by the median signed distance of the first side's vertices; None only when a side
    is empty. Before 2026-11-19 a line whose mesh vertices lay over 0.5 mm off it also got None, and A was then the
    mesh flood's first side whichever way the line ran; on 4 garments' 14 fold lines it never happened (the line's
    vertices 0.001 mm off it at most), and the rule here gave the same A on all 14."""
    if len(L) < 2 or not len(sides[0]) or not len(sides[1]):
        return None
    left = 0 if float(np.median(signed_to_line(V[sides[0]], L)[0])) > 0 else 1
    return left if hand > 0 else 1 - left


def _turn_back(ob, info, d):
    """The object's vertices (n, 3) before its last fold d, for a fold made before BEFORE_ATTR was kept (a file
    saved folded, user 2026-10-22): a rigid fold turned back about the same chord, the way that brings its side
    nearest its flat distances to the other side; None if the line is not straight in 3D (the collar's way)."""
    if abs(float(d.get('deg', 0.0))) >= FLAT_DEG or info.get('flap') is None:
        return None
    side = int(d.get('side', 0))
    roll, f0, o0 = info['flap']                      # such a fold was made on dress.fold_flap's split as it was
    fall, stay = (f0, o0) if side == 0 else (o0, f0)
    V, X = info['V'], info['Xid']
    r2 = V[roll]
    u = np.linalg.svd(r2 - r2.mean(0), full_matrices=False)[2][0]
    P = X[roll[np.argsort((r2 - r2.mean(0)) @ u)]]
    a = P[-1] - P[0]
    if np.linalg.norm(a) < 1e-6:
        return None
    a /= np.linalg.norm(a)
    off = (P - P[0]) - np.outer((P - P[0]) @ a, a)
    if np.linalg.norm(off, axis=1).max() > STRAIGHT_MM:
        return None
    rng = np.random.default_rng(0)
    fi, si = rng.choice(fall, 200), rng.choice(stay, 200)
    flat = np.linalg.norm(V[fi] - V[si], axis=1)
    best = None
    for sg in (1.0, -1.0):
        Rm = np.array(Matrix.Rotation(np.radians(sg * abs(float(d['deg']))), 3, Vector(a.tolist())))
        Xb = X.copy()
        Xb[fall] = (X[fall] - P[0]) @ Rm.T + P[0]
        err = float(np.abs(np.linalg.norm(Xb[fi] - Xb[si], axis=1) - flat).mean())
        if best is None or err < best[0]:
            best = (err, Xb)
    return _to_local(info, _to_world(info, best[1]))


def _unfold(ob, done, k, info=None):
    """Put the object back to its shape before fold k (BEFORE_ATTR), dropping that fold and the ones after it.
    False if that shape was not kept (and, for the last fold, cannot be turned back: _turn_back)."""
    me = ob.data
    a = me.attributes.get(f"{BEFORE_ATTR}{k}")
    if a is None or len(a.data) != len(me.vertices):
        back = _turn_back(ob, info, done[k]) if info is not None and k == len(done) - 1 else None
        if back is None:
            return False
        co = back.ravel()
    else:
        co = np.empty(len(me.vertices) * 3)
        a.data.foreach_get('vector', co)
    me = PLV.writable_mesh(ob)
    me.vertices.foreach_set('co', co.astype(np.float32))
    me.attributes.get(PLV.GEN_ATTR).data.foreach_set(
        'vector', PLV.generated_co(ob, co.reshape(-1, 3)).astype(np.float32).ravel())
    for j in range(k, len(done)):
        b = me.attributes.get(f"{BEFORE_ATTR}{j}")
        if b is not None:
            me.attributes.remove(b)
    me.update()
    ob[FOLD_PROP] = json.dumps(done[:k])
    return True


def folded(ob):
    try:
        return json.loads(ob.get(FOLD_PROP, '[]'))
    except (TypeError, ValueError):
        return []


def target(context, spec):
    """(piece, line id, deg) of the internal line picked in Edit Pieces, or None."""
    from . import draft_tool as DT
    s = DT.selected_internal(context, spec)
    if s is None:
        return None
    name, lid, _it = s
    deg = next((float(f['deg']) for f in spec.get('folds', []) if f.get('piece') == name and f.get('line') == lid
                and f.get('deg') is not None), 0.0)
    return name, lid, deg


def seam_target(context, spec):
    """(seam, deg, turning piece) of the seam picked in the Seams list when no internal line is picked, or None: a
    seam between two pieces (a dart's is an internal line's matter). The turning piece is the one set, else
    spec.turn_default (the one sewn to the others along less length)."""
    from . import draft_tool as DT
    if spec is None or DT.selected_internal(context, spec) is not None:
        return None
    sm = store.active_seam(context, spec)
    if sm is None or sm['a']['piece'] == sm['b']['piece']:
        return None
    f = next((f for f in spec.get('folds', []) if f.get('seam') == sm['id']), {})
    turn = f.get('turn') if f.get('turn') in (sm['a']['piece'], sm['b']['piece']) else S.turn_default(spec, sm)
    return sm, float(f.get('deg') or 0.0), turn


def _seam_of(spec, line):
    sid = line[len(SEAM_KEY):] if str(line).startswith(SEAM_KEY) else None
    return next((sm for sm in spec.get('seams', []) if sm['id'] == sid), None) if sid else None


_cache = {}     # (piece, line) -> (key, info): the Tool tab's Fold Angle refolds on every slider step (user 10/24)


def _effective(context, spec):
    from . import dress as D
    from .core import prepare_plan
    if spec is None:
        return None
    props = context.scene.ac9_cloth_sim
    if not spec.get('placement'):
        return spec
    effective = prepare_plan.resolve(spec, props, environ=None if bpy.app.background else {}).spec
    return effective


def _cache_scalars(context, ob):
    """The mesh size and the placed object as made (not its shape, read again on each use)."""
    return (float(context.scene.ac9_cloth_sim.mesh_size), ob.name, len(ob.data.vertices), ob.get(PLV.KEY_PROP),
            bool(PLV.has_edits(ob)), bool(ob.get(PLV.LAID_PROP)))


def _spec_rest(spec):
    """The spec but its folds (each step writes the angle), as (key, object) pairs."""
    return [(k, v) for k, v in sorted(spec.items()) if k != 'folds']


def _spec_hash(rest):
    return hash(S.dumps(dict(rest)))


def _same_rest(entry, spec):
    """The spec but its folds as when the entry was made: the very objects (a slider step writes only the folds,
    store.put keep) or, failing that, the same content (40 ms on a pants' spec), the objects then kept."""
    rest = _spec_rest(spec)
    if len(rest) == len(entry['rest']) and all(k == k2 and v is v2 for (k, v), (k2, v2) in zip(rest, entry['rest'])):
        return True
    if _spec_hash(rest) != entry['hash']:
        return False
    entry['rest'] = rest
    return True


def _info(context, spec, piece, line, *, preparation=None):
    """sides() for an internal line, seam_sides() for SEAM_KEY + seam id; kept while the pattern and the placed
    object as made stay the same (meshing the garment is most of a refold: 530 of 600 ms on a shirt), its shape and
    origin read again from the object."""
    spec = _effective(context, spec)
    ob = _object(spec, piece)
    hit = _cache.get((piece, line))
    if (ob is not None and hit is not None and hit[0]['scalars'] == _cache_scalars(context, ob)
            and _same_rest(hit[0], spec)):
        info = hit[1]
        co = np.empty(len(ob.data.vertices) * 3)
        ob.data.vertices.foreach_get('co', co)
        org = PLV.origin_mm(ob)
        info = dict(info, ob=ob, org=org, Xid=_to_guide(info, PLV.generated_co(ob, co.reshape(-1, 3)) * 1000.0))
        if info.get('seam') is not None:             # the partner may have moved since
            info['partner_dir'] = _partner_dir(info['pat'], info['i'], info['j'], info['mine'],
                                               _partner_X(context, spec, info['partner'], ob, info.get('G'),
                                                          preparation=preparation))
        return info, None
    if str(line).startswith(SEAM_KEY):
        sm = _seam_of(spec, line)
        if sm is None:
            return None, tx("{0}: the seam is gone", line)
        info, why = seam_sides(context, spec, sm, piece, preparation=preparation)
    else:
        info, why = sides(context, spec, piece, line, preparation=preparation)
    if info is not None and info['ob'] is ob:
        _cache.clear()                                  # one line at a time: the one being set
        rest = _spec_rest(spec)
        _cache[(piece, line)] = ({'scalars': _cache_scalars(context, ob), 'rest': rest, 'hash': _spec_hash(rest)}, info)
    return info, why


def seam_sides(context, spec, sm, piece, *, preparation=None):
    """What Fold needs to turn the whole of `piece` over its own edge of seam sm (user 2026-10-23, DECISIONS
    cloth-sim-seam-fold-2026-10-23; Fable docs/reviews/REVIEW_fable_seam_fold_3d_2026-10-23.md): the same dict as
    sides(), its 'sides' [every vertex, none]. The partner may be anywhere and the piece may have other seams: they
    come apart and Simulate closes them. Refused only when there is no edge to turn about."""
    from . import dress as D
    other = sm['b']['piece'] if piece == sm['a']['piece'] else sm['a']['piece']
    if not sm.get('enabled', True):
        return None, tx("{0} is off", sm['id'])
    pl = spec.get('placement', {}).get(piece)
    if pl is None:
        return None, tx("{0} is not placed", piece)
    guide = str(pl.get('guide', ''))
    if guide not in spec.get('guides', {}):
        return None, tx("{0} is not placed", piece)
    ob = _object(spec, piece)
    if ob is None:
        twin = next((o for o in PLV.objects(spec).values() if o.get(PLV.TWIN_PROP) == piece), None)
        if twin is not None:
            return None, tx("{0} follows {1} (a pair): set Turns to {2}", piece, twin[PLV.PROP], other)
        return None, tx("{0} has no adjusted shape: Place it first", piece)
    if ob.get(PLV.TWIN_PROP) == other:
        return None, tx("{0} joins a pair's two pieces: not folded when placed, its angle works while simulating",
                        sm['id'])
    if PLV.has_edits(ob):
        return None, tx("{0}'s adjusted shape was edited vertex by vertex: Back to Placed Shape first", piece)
    if ob.get(PLV.LAID_PROP):
        return None, tx("{0} was laid on its edge (Band on Edge): Back to Placed Shape first", piece)
    try:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
    except ValueError as exc:
        PB.cancel(preparation)
        return None, str(exc)
    pat, tris = res['pat'], res['tris']
    names = [str(n) for n in pat['names']]
    if piece not in names or other not in names:
        return None, tx("{0} is not placed", other if piece in names else piece)
    i, j = names.index(piece), names.index(other)
    V = np.asarray(pat[f"V{i}"], float)
    if not PLV.layout_matches(ob, V):
        PLV.remesh(ob, V, tris[i])
    st = np.asarray(pat['stitch'])
    mine = (st[:, 0] == i) & (st[:, 2] == j), (st[:, 2] == i) & (st[:, 0] == j)
    run = S.seam_run(spec, sm)          # with the seams continuing it in line (the yoke's two halves)
    if 'stitch_seam' in pat:            # this seam's stitches only (two pieces can share several seams)
        ss = np.asarray(pat['stitch_seam']).astype(str)
        mine = tuple(m & np.isin(ss, [str(r) for r in run]) for m in mine)
    roll = np.unique(np.r_[st[mine[0], 1], st[mine[1], 3]]).astype(int)
    if len(roll) < 3:
        return None, tx("{0}: too few points of {1} on it to fold about", sm['id'], piece)
    pv = np.unique(np.r_[st[mine[0], 3], st[mine[1], 1]]).astype(int)
    n = len(ob.data.vertices)
    co = np.empty(n * 3)
    ob.data.vertices.foreach_get('co', co)
    org = PLV.origin_mm(ob)
    G = _frame(spec, guide)
    Xid = _to_guide({'G': G}, PLV.generated_co(ob, co.reshape(-1, 3)) * 1000.0)
    axis = np.asarray(spec['guides'][guide]['o'], float)[:2] if G is None else np.zeros(2)
    # what the header shows (Fable §3: shown, not refused): the gap to the partner's edge, the other seams
    gap = None
    pob = _object(spec, other)
    if pob is not None and len(pob.data.vertices) == len(pat[f"V{j}"]):
        pc = np.empty(len(pob.data.vertices) * 3)
        pob.data.vertices.foreach_get('co', pc)
        P = (pc.reshape(-1, 3) @ np.asarray(pob.matrix_world)[:3, :3].T + np.asarray(pob.matrix_world)[:3, 3])[pv]
        M = np.asarray(ob.matrix_world)
        Q = co.reshape(-1, 3)[roll] @ M[:3, :3].T + M[:3, 3]
        gap = float(np.median(np.linalg.norm(Q[:, None] - P[None], axis=2).min(1))) * 1000.0
    partner_dir = _partner_dir(pat, i, j, mine, _partner_X(context, spec, other, ob, G, preparation=preparation))
    others = sorted({s['id'] for s in spec['seams'] if s.get('enabled', True) and s['id'] not in run
                     and piece in (s['a']['piece'], s['b']['piece']) and s['a']['piece'] != s['b']['piece']})
    return {'ob': ob, 'i': i, 'pat': pat, 'tris': tris, 'roll': roll, 'sides': [np.arange(n), np.zeros(0, int)],
            'Xid': Xid, 'org': org, 'guide': guide, 'axis': axis, 'V': V, 'flap': None, 'seam': sm['id'], 'G': G,
            'partner': other, 'gap_mm': gap, 'other_seams': others, 'run': run, 'a_side': None, 'piece': piece,
            'partner_dir': partner_dir, 'mine': mine, 'j': j}, None


def _partner_X(context, spec, other, ob, G, *, preparation=None):
    """The partner where it is now (its placed object, else the dressed garment, else its guide: place_seam.shapes,
    as Part to Seam reads it) in ob's generated frame and the guide's frame (mm), as Xid is read: what a seam's fold
    angle is measured against (_seam_hinge). None if it cannot be read."""
    from . import place_seam as PS
    if ob is None:
        return None
    got, _why = PS.shapes(context, spec, preparation=preparation)
    if got is None:
        return None
    names = [str(n) for n in got[0]['names']]
    if other not in names:
        return None
    W = np.asarray(got[2][names.index(other)], float) / 1000.0
    if not len(W):
        return None
    L = (np.c_[W, np.ones(len(W))] @ np.linalg.inv(np.asarray(ob.matrix_world)).T)[:, :3]
    return _to_guide({'G': G}, PLV.generated_co(ob, L) * 1000.0)


def _frame(spec, guide):
    """(o, R) of an arm or leg guide (its frame: rows of R the axes, R[2] along the cylinder), None for a torso or
    neck guide (folded in world space, its axis vertical)."""
    if guide in AXIS_GUIDES:
        return None
    g = spec['guides'][guide]
    return np.asarray(g['o'], float), np.asarray(g['R'], float)


def _to_guide(info, X):
    """World mm -> the guide's frame (unchanged on a torso or neck guide)."""
    G = info.get('G')
    return X if G is None else (np.asarray(X, float) - G[0]) @ G[1].T


def _to_world(info, X):
    G = info.get('G')
    return X if G is None else np.asarray(X, float) @ G[1] + G[0]


def _to_local(info, X):
    ob = info.get('ob')
    return (PLV.generated_co(ob, np.asarray(X) * PLV.MM, inverse=True) if ob is not None
            else (np.asarray(X) - info['org']) * PLV.MM)


def _object(spec, piece):
    """The placed object holding the piece itself (not the mirrored half of a pair's object)."""
    ob = PLV.objects(spec).get(piece)
    return ob if ob is not None and ob.get(PLV.PROP) == piece else None


def sides(context, spec, piece, line, prepared=None, *, preparation=None):
    """Everything Fold needs, or (None, reason): {'ob', 'i', 'pat', 'tris', 'roll', 'sides': [fall0, fall1],
    'Xid' (n, 3) mm the object's shape as made, 'org' mm, 'guide', 'axis'}."""
    from . import dress as D
    spec = _effective(context, spec)
    pl = spec.get('placement', {}).get(piece)
    if pl is None:
        return None, tx("{0} is not placed", piece)
    guide = str(pl.get('guide', ''))
    if not S.is_flat(pl) and guide not in spec.get('guides', {}):
        return None, tx("{0} is not placed", piece)
    ob = _object(spec, piece)
    if ob is None:
        twin = next((o for o in PLV.objects(spec).values() if o.get(PLV.TWIN_PROP) == piece), None)
        if twin is not None:
            return None, tx("{0} follows {1} (a pair): fold {1}'s line, both fold", piece, twin[PLV.PROP])
        return None, tx("{0} has no adjusted shape: Place it first", piece)
    if PLV.has_edits(ob):
        return None, tx("{0}'s adjusted shape was edited vertex by vertex: Back to Placed Shape first", piece)
    if ob.get(PLV.LAID_PROP):
        return None, tx("{0} was laid on its edge (Band on Edge): Back to Placed Shape first", piece)
    try:
        res = prepared if prepared is not None else D.prepare(context, context.scene.ac9_cloth_sim.mesh_size,
                                                             shapes_only=True, placed_only=True, preparation=preparation)
    except ValueError as exc:
        PB.cancel(preparation)
        return None, str(exc)
    pat, tris = res['pat'], res['tris']
    names = [str(n) for n in pat['names']]
    i = names.index(piece) if piece in names else -1
    fid = D._fold_fid(pat, piece, line)
    if i < 0 or fid is None:
        return None, tx("{0} is not a fold line of {1}", line, piece)
    V = np.asarray(pat[f"V{i}"], float)
    n = len(ob.data.vertices)
    if not PLV.layout_matches(ob, V):
        # the pattern re-meshed since (a fold line drawn): the adjusted shape carried onto the new mesh
        PLV.remesh(ob, V, tris[i])
        n = len(ob.data.vertices)
    co = np.empty(n * 3)
    ob.data.vertices.foreach_get('co', co)
    org = PLV.origin_mm(ob)
    return shape_info(spec, piece, line, pat, tris, PLV.generated_co(ob, co.reshape(-1, 3)) * 1000.0, org, ob)


def shape_info(spec, piece, line, pat, tris, X, org=(0., 0., 0.), ob=None):
    """Pure fold inputs shared by placement previews and committed shapes (world mm)."""
    from . import dress as D
    pl = spec.get('placement', {}).get(piece, {})
    flat = S.is_flat(pl)
    guide = str(pl.get('guide', ''))
    if not flat and guide not in spec.get('guides', {}):
        return None, tx("{0} is not placed", piece)
    names = [str(v) for v in pat['names']]
    i = names.index(piece) if piece in names else -1
    fid = D._fold_fid(pat, piece, line)
    if i < 0 or fid is None:
        return None, tx("{0} is not a fold line of {1}", line, piece)
    V = np.asarray(pat[f"V{i}"], float)
    n = len(V)
    pc = S.piece_index(spec).get(piece, {})
    il = next((l for l in pc.get('internals', []) if str(l.get('id')) == str(line)), None)
    if il is None or len(il.get('pts', ())) < 2:
        return None, tx("{0} is not a fold line of {1}", line, piece)
    L = np.asarray((il or {}).get('pts', ()), float)
    ab = split_ab(pat, i, fid, tris[i], L, FH.hand(il))
    if ab is None:
        return None, tx("{0} does not divide {1}", line, piece)
    roll, (fall, other), a_side, flap = ab
    if not cuts_apart(n, tris[i], roll, fall, other):
        return None, tx("{0} does not cut {1} apart (it stops inside the piece): not folded when placed, its angle "
                        "works while simulating", line, piece)
    guide = str(spec['placement'][piece]['guide'])
    G = (np.asarray(pl['flat']['o'], float), np.asarray(pl['flat']['R'], float)) if flat else _frame(spec, guide)
    Xid = _to_guide({'G': G}, np.asarray(X, float))
    axis = np.asarray(spec['guides'][guide]['o'], float)[:2] if G is None else np.zeros(2)
    return {'ob': ob, 'i': i, 'pat': pat, 'tris': tris, 'roll': roll, 'sides': [fall, other], 'Xid': Xid,
            'org': np.asarray(org, float), 'guide': guide, 'axis': axis, 'V': V, 'flap': flap, 'G': G,
            'a_side': a_side, 'piece': piece, 'flat': flat, 'fold_path':L.copy(),
            'fold_closed':bool(il.get('closed'))}, None


def cuts_apart(n, T, roll, fall, other):
    """The two sides of a line apart but for the line itself: few mesh edges join them round its ends (a line that
    stops inside the piece joins them all round its end, a pocket's position line tears up to 75 x)."""
    T = np.asarray(T)
    E = np.unique(np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1), axis=0)
    cls = np.zeros(n, int)
    cls[fall] = 1
    cls[other] = 2
    cls[roll] = 0
    a, b = cls[E[:, 0]], cls[E[:, 1]]
    torn = int(((a == 1) & (b == 2) | (a == 2) & (b == 1)).sum())
    on_line = int(((a == 0) | (b == 0)).sum())
    return torn <= max(TEAR_MIN, TEAR_SHARE * on_line)


def split_ab(pat, i, fid, T, L, hand=1):
    """The two sides of piece i's internal line fid (L its points, flat mm; hand the line's, core/fold_hand.hand), as
    Fold, Layers and the fills all read them: (roll, [fall, other], a, flap) -- a the index of side A in [fall,
    other] or None (the sides could not be told apart by the line: no A / B), flap dress.fold_flap's split before
    _split_end_to_end -- or None when the line does not divide it."""
    from . import dress as D
    V = np.asarray(pat[f"V{i}"], float)
    roll, fall = D.fold_flap(pat, i, fid, T)
    if len(roll) < 3 or not len(fall):
        return None
    flap = (roll, fall, np.setdiff1d(np.arange(len(V)), np.r_[roll, fall]))
    if len(L):
        roll, fall = _split_end_to_end(V, T, L, roll, fall)
    other = np.setdiff1d(np.arange(len(V)), np.r_[roll, fall])
    return roll, [fall, other], _a_side(V, L, roll, [fall, other], hand), flap


def _split_end_to_end(V, T, L, roll, fall, near=1.0):
    """(roll, fall) again for a line L (flat mm) whose two ends are on the piece's outline: every vertex goes by its
    side of the line (on it: roll). The line's mesh vertices can stop a row short of its ends on a coarse mesh (a
    placket at 20 mm: 20 mm short), and the flood then left the corner rows on the other side, stretched across
    the line (user 2026-10-22: the top and bottom ends stayed unfolded). Unchanged if an end is inside the piece."""
    if len(L) < 2 or not len(fall):
        return roll, fall
    T = np.asarray(T)
    E = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    u, c = np.unique(E, axis=0, return_counts=True)
    B = u[c == 1]
    if not len(B):
        return roll, fall

    def to_outline(q):
        a, b = V[B[:, 0]], V[B[:, 1]]
        ab = b - a
        t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        return float(np.linalg.norm(a + ab * t[:, None] - q, axis=1).min())
    if to_outline(L[0]) > near or to_outline(L[-1]) > near:
        return roll, fall
    # signed distance to the polyline, by its nearest segment (signed_to_line, with where along it)
    a, b = L[:-1], L[1:]
    ab = b - a
    q = V[:, None, :] - a[None]
    t0 = (q * ab[None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-12)[None]
    t = np.clip(t0, 0, 1)
    d = np.linalg.norm(q - ab[None] * t[..., None], axis=2)
    j = d.argmin(1)
    k = np.arange(len(V))
    # only the vertices beside the line itself (not past its ends, but for `near` mm): the rest stays the flood's
    lab = np.linalg.norm(ab[j], axis=1)
    tj = t0[k, j] * lab
    beside = (tj > -near) & (tj < lab + near)
    beside &= ~((j > 0) & (tj < 0)) & ~((j < len(ab) - 1) & (tj > lab))
    sd = ab[j, 0] * q[k, j, 1] - ab[j, 1] * q[k, j, 0]
    sd = sd / np.maximum(np.linalg.norm(ab[j], axis=1), 1e-12)
    if d[roll, j[roll]].max() > 0.5:           # the line's points not in the mesh's frame (a mirrored piece)
        return roll, fall
    on = np.abs(sd) < 0.05
    sg = np.sign(np.median(sd[fall]))
    roll2 = np.union1d(roll, np.nonzero(on & beside)[0])
    sw = np.zeros(len(V), bool)
    sw[fall] = True
    sw[beside & ~on] = np.sign(sd[beside & ~on]) == sg
    fall2 = np.setdiff1d(np.nonzero(sw)[0], roll2)
    return roll2, fall2


def _rigid(info, fall, deg, inward):
    """The side `fall` turned rigidly by deg about the line's chord, the way that takes it in towards the guide's
    axis (inward) or out; (None, 0) if the line is not straight in 3D."""
    V, X, roll = info['V'], info['Xid'], info['roll']
    r2 = V[roll]
    u = np.linalg.svd(r2 - r2.mean(0), full_matrices=False)[2][0]
    roll = roll[np.argsort((r2 - r2.mean(0)) @ u)]
    P = X[roll]
    a = P[-1] - P[0]
    if np.linalg.norm(a) < 1e-6:
        return None, 0
    a /= np.linalg.norm(a)
    off = (P - P[0]) - np.outer((P - P[0]) @ a, a)
    if np.linalg.norm(off, axis=1).max() > STRAIGHT_MM:
        return None, 0
    ax = info['axis']
    best = None
    for sg in (1.0, -1.0):
        Rm = np.array(Matrix.Rotation(np.radians(sg * deg), 3, Vector(a.tolist())))
        Q = (X[fall] - P[0]) @ Rm.T + P[0]
        rad = float(np.hypot(Q[:, 0] - ax[0], Q[:, 1] - ax[1]).mean())
        key = rad if not inward else -rad
        if best is None or key > best[0]:
            best = (key, Q)
    Xf = X.copy()
    Xf[fall] = best[1]
    return Xf, len(fall)


def _feet(info, idx, smooth=2):
    """For the vertices idx: their foot on the line in 3D and the line's tangent there (found on the flat pattern,
    carried to the 3D roll polyline), their distance from the line on the flat pattern (mm), and the 3D roll
    polyline (m, 3) with its tangents; None if the line has under 2 points."""
    V, X, roll = info['V'], np.asarray(info['Xid'], float), info['roll']
    r2 = V[roll]
    path=np.asarray(info.get('fold_path',[]),float)
    if not info.get('fold_closed') and path.ndim==2 and path.shape[1]==2 and len(path)>1 and np.linalg.norm(np.diff(path,axis=0),axis=1).sum()>1e-9:
        from .core import iline as IL
        # Material order follows the authored crease, including U/S bends.
        order=np.argsort([IL._project(path,q)[0] for q in r2],kind='stable')
    else:
        # Preserve closed creases until cyclic feet/tangents include their closing segment.
        # Seam folds and older callers also use the established PCA fallback.
        pc = np.linalg.svd(r2 - r2.mean(0), full_matrices=False)[2][0]
        order = np.argsort((r2 - r2.mean(0)) @ pc)
    R, r2 = roll[order], r2[order]
    if len(R) < 2:
        return None
    P3 = X[R]
    tg = np.gradient(P3, axis=0)
    if smooth:
        k = np.ones(2 * smooth + 1) / (2 * smooth + 1)
        tg = np.stack([np.convolve(np.pad(tg[:, c], smooth, 'edge'), k, 'valid') for c in range(3)], 1)
    tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-12)
    sa, ab = r2[:-1], r2[1:] - r2[:-1]
    q = V[idx][:, None, :] - sa[None]
    tt = np.clip((q * ab[None]).sum(2) / np.maximum((ab * ab).sum(1), 1e-12)[None], 0, 1)
    dist = np.linalg.norm(q - ab[None] * tt[..., None], axis=2)
    j = dist.argmin(1)
    t = tt[np.arange(len(j)), j][:, None]
    foot = P3[j] + (P3[j + 1] - P3[j]) * t
    tau = tg[j] + (tg[j + 1] - tg[j]) * t
    tau /= np.maximum(np.linalg.norm(tau, axis=1, keepdims=True), 1e-12)
    return foot, tau, dist[np.arange(len(j)), j], P3, tg, R, j, t[:, 0]


SEAM_READ_MM = 25.0     # the turning piece's points this close to its seam (on the pattern) give its angle now


def _seam_hinge(info, deg, inward):
    """A seam's fold on the adjusted shape (user 2026-11-24: setting it moved nothing on a collar placed tilted out):
    the angle is the one between the two pieces, as the solver holds it, not a turn added to the shape now. The
    turning piece's angle now is read against the partner where it is now (info['partner_dir']: at each stitch, the
    way the partner runs on from its own seam edge; straight on from it = 0, + out from the guide's axis), and the
    piece is turned about the seam's own tangent by what is missing, each vertex keeping its offset from the seam.
    dress.prefold develops the guide's cylinder and so put every vertex of a piece tilted out onto the seam (moved
    0 mm at any angle); turning by deg from the shape now counted a Tilt twice. Read from the partner's own edge,
    not from this piece's seam: a placed collar's seam is 40 mm off the dressed stand's, and the direction to the
    stand from there read the collar 60 deg off. (None, 0) without the partner's shape."""
    pdir = info.get('partner_dir') or {}
    X = np.asarray(info['Xid'], float).copy()
    roll = info['roll']
    fall = np.setdiff1d(np.arange(len(X)), roll)
    if len(pdir) < 2 or not len(fall):
        return None, 0
    ft = _feet(info, fall)
    if ft is None:
        return None, 0
    foot, tau, near, _P3, _tg, R, j, t = ft
    have = np.array([int(r) in pdir for r in R])
    if have.sum() < 2:
        return None, 0
    hi = np.nonzero(have)[0]
    k = hi[np.abs(np.arange(len(R))[:, None] - hi[None]).argmin(1)]          # a seam point with none: the nearest
    Dk = np.array([pdir[int(R[q])] for q in k], float)
    ds = Dk[j] + (Dk[j + 1] - Dk[j]) * t[:, None]                             # the partner's way at each foot
    d0 = -ds                                                                   # straight on from the partner
    d0 -= (d0 * tau).sum(1, keepdims=True) * tau
    d0 /= np.maximum(np.linalg.norm(d0, axis=1, keepdims=True), 1e-12)
    b = np.cross(tau, d0)
    ax = np.asarray(info['axis'], float)
    rad = np.c_[foot[:, 0] - ax[0], foot[:, 1] - ax[1], np.zeros(len(foot))]
    b *= np.where((b * rad).sum(1) < 0, -1.0, 1.0)[:, None]                  # + = out from the axis
    D = X[fall] - foot
    Dp = (D * tau).sum(1, keepdims=True) * tau
    Dn = D - Dp
    th = np.arctan2((Dn * b).sum(1), (Dn * d0).sum(1))
    sel = (near < SEAM_READ_MM) & (np.linalg.norm(Dn, axis=1) > 1.0)
    now = float(np.median(np.unwrap(th[sel] if sel.sum() >= 3 else th)))
    want = -np.radians(abs(float(deg))) if inward else np.radians(abs(float(deg)))
    dlt = want - now
    wv = np.cross(d0, b)
    wv /= np.maximum(np.linalg.norm(wv, axis=1, keepdims=True), 1e-12)
    X[fall] = foot + Dp + Dn * np.cos(dlt) + np.cross(wv, Dn) * np.sin(dlt)
    info['turned_from_deg'] = round(float(np.degrees(now)), 1)
    return X, len(fall)


def _partner_dir(pat, i, j, mine, PX):
    """{this piece's seam vertex: unit vector} the way the partner runs on from its own seam edge at the stitched
    point, read on the partner's shape PX (n_j, 3) between 3 and 25 mm from it on the partner's pattern."""
    st = np.asarray(pat['stitch'])
    Vj = np.asarray(pat[f"V{j}"], float)
    if PX is None or len(PX) != len(Vj):
        return {}
    pairs = list(zip(st[mine[0], 1], st[mine[0], 3])) + list(zip(st[mine[1], 3], st[mine[1], 1]))
    edge = {int(p) for _r, p in pairs}
    inner = np.array([q not in edge for q in range(len(Vj))])
    acc = {}
    for r, p in pairs:
        d = np.linalg.norm(Vj - Vj[int(p)], axis=1)
        near = (d > 3.0) & (d < 25.0) & inner
        if near.sum() < 2:
            continue
        v = PX[near].mean(0) - PX[int(p)]
        n = np.linalg.norm(v)
        if n > 1e-9:
            acc.setdefault(int(r), []).append(v / n)
    return {r: np.mean(v, 0) for r, v in acc.items()}


def _hinge(info, fall, deg, inward, smooth=2):
    """The side `fall` turned by deg about the line's own tangent at each vertex's foot (found on the flat pattern,
    carried to the 3D roll polyline): a line curved in 3D, on a piece that need not lie on the guide's cylinder (a
    collar tilted out 45 deg: dress.prefold, which develops the cylinder, crushed its leaf onto the line; Fable
    docs/reviews/REVIEW_fable_collar_fold_tilt_2026-11-02.md). Each vertex keeps its offset from the line, so the
    adjusted shape turns as it is; in towards the axis (inward) or out by the mean radius, as _rigid. A curved crease
    does not fold isometrically: _stretch says how much."""
    X = np.asarray(info['Xid'], float).copy()
    ft = _feet(info, fall, smooth)
    if ft is None:
        return None, 0
    foot, tau = ft[0], ft[1]
    D = X[fall] - foot
    Dp = (D * tau).sum(1, keepdims=True) * tau
    Dn, Dc = D - Dp, np.cross(tau, D - Dp)
    ax = info['axis']
    best = None
    for sg in (1.0, -1.0):
        th = np.radians(sg * deg)
        Y = foot + Dp + Dn * np.cos(th) + Dc * np.sin(th)
        rad = float(np.hypot(Y[:, 0] - ax[0], Y[:, 1] - ax[1]).mean())
        key = -rad if inward else rad
        if best is None or key > best[0]:
            best = (key, Y)
    X[fall] = best[1]
    return X, len(fall)


def _flat_hinge(info, fall, stay, deg):
    """Turn relative to the fabric face at the crease, independent of floor orientation."""
    X = np.asarray(info['Xid'], float).copy()
    ft = _feet(info, fall)
    if ft is None:
        raise ValueError(tx("{0} does not divide {1}", '', info['piece']))
    foot, tau, _, _, _, roll, j, t = ft
    T = np.asarray(info['tris'][info['i']], int)
    fixed = np.zeros(len(X), bool)
    fixed[np.r_[stay, info['roll']]] = True
    T = T[fixed[T].all(1)]
    normal = np.zeros_like(X)
    N = np.cross(X[T[:, 1]] - X[T[:, 0]], X[T[:, 2]] - X[T[:, 0]])
    for c in range(3):
        np.add.at(normal, T[:, c], N)
    N = normal[roll]
    good = np.linalg.norm(N, axis=1) > 1e-10
    if not good.any():
        raise ValueError(tx("{0} does not divide {1}", '', info['piece']))
    ids = np.flatnonzero(good)
    nearest = ids[np.abs(np.arange(len(roll))[:, None] - ids[None]).argmin(1)]
    N = N[nearest]
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    face = N[j] * (1 - t[:, None]) + N[j + 1] * t[:, None]
    delta = X[fall] - foot
    parallel = (delta * tau).sum(1, keepdims=True) * tau
    radial = delta - parallel
    cross = np.cross(tau, radial)
    # Mountain (+) turns behind the face; valley (-) turns in front of it.
    sign = np.where((cross * face).sum(1) >= 0, -1., 1.)
    theta = sign * np.radians(float(deg))
    X[fall] = foot + parallel + radial * np.cos(theta)[:, None] + cross * np.sin(theta)[:, None]
    return X, len(fall)


def _shape(info, side, deg):
    """The object's vertices (n, 3, its own space) with side `side` turned by deg over the line (+ a mountain seen
    from the pattern's face: behind the face, so in towards the guide's axis while the face looks out), and how many
    turned: rigidly about the chord for a straight line under FLAT_DEG, a line curved in 3D about its own tangent
    (_hinge), else the collar's way (dress.prefold: a seam, or laid back flat)."""
    from . import dress as D
    fall, stay = info['sides'][side], info['sides'][1 - side]
    if info.get('flat'):
        Xf, nf = _flat_hinge(info, fall, stay, deg)
        return _to_local(info, _to_world(info, Xf)), nf
    # a seam turns the whole piece (fall = every vertex): its face is read from its own triangles on the seam edge,
    # else none were left to read and the face was taken to look out (a piece turned over in 3D folded backwards)
    face = D.face_out(info['Xid'], info['tris'][info['i']], info['roll'], info['axis'],
                      () if info.get('seam') is not None else fall)
    inward = (deg > 0) == face
    if info.get('seam') is not None:
        # a seam: the angle between the two pieces, read against the partner's adjusted shape (_seam_hinge)
        Xf, nf = _seam_hinge(info, deg, inward)
        if Xf is not None:
            return _to_local(info, _to_world(info, Xf)), nf
    Xf, nf = _rigid(info, fall, abs(float(deg)), inward) if abs(deg) < FLAT_DEG else (None, 0)
    if Xf is None and abs(deg) < FLAT_DEG and info.get('seam') is None:
        Xf, nf = _hinge(info, fall, abs(float(deg)), inward)
    if Xf is None:
        # a seam turns the whole piece: the side that stays is the seam edge itself (dress.py's collar on its stand)
        ref = stay[:1] if len(stay) else info['roll']
        Xf, nf = D.prefold(info['Xid'], info['V'], info['roll'], ref, info['axis'], gap=3.0, fall=fall,
                           inward=inward, angle_deg=abs(float(deg)))
    return _to_local(info, _to_world(info, Xf)), nf


def write_angle(context, piece, line, deg, side=None):
    """The line's Fold (deg) in the spec (0 removes it), as the Tool tab's field writes it, with the side that turns
    ('A' / 'B') when given; for SEAM_KEY + seam id the seam's ({'seam', 'deg', 'turn': piece})."""
    from . import overlay
    spec = store.get(context)[0]
    sid = line[len(SEAM_KEY):] if str(line).startswith(SEAM_KEY) else None
    if sid:
        sm = next((s for s in spec.get('seams', []) if s['id'] == sid), None)
        run = S.seam_run(spec, sm) if sm else [sid]        # the seams continuing it in line, the same angle
        folds = [f for f in spec.get('folds', []) if f.get('seam') not in run]
        if abs(deg) > 1e-6:
            folds += [dict(seam=r, deg=round(float(deg), 2), turn=piece) for r in run]
    else:
        old = next((f for f in spec.get('folds', []) if f.get('piece') == piece and f.get('line') == line), {})
        folds = [f for f in spec.get('folds', []) if f is not old]
        if abs(deg) > 1e-6:
            folds.append(dict(old, piece=piece, line=line, deg=round(float(deg), 2)))
            if side in ('A', 'B', 'NONE'):
                folds[-1]['side'] = side
    # only the folds change (a new list of new dicts): the rest shared, kept by the store
    candidate = dict(spec, folds=folds)
    if not sid:
        from .core import draft as DR
        candidate = DR.carry_fold(candidate, piece, line)
    store.put(context, candidate, keep=True, seams=False)
    overlay.invalidate()


def _mesh_sides(context, js, piece, line):
    from . import fold_sides as FS
    from . import dress as D
    pm = FS.piece_mesh(context, js, piece)
    if pm is None:
        return None
    pat, _names, i, T = pm
    fid = D._fold_fid(pat, piece, str(line))
    il = next((l for l in S.piece_index(js).get(piece, {}).get('internals', []) if str(l.get('id')) == str(line)),
              None)
    if fid is None or il is None:
        return None
    ab = split_ab(pat, i, fid, T, np.asarray(il.get('pts', ()), float), FH.hand(il))
    return None if ab is None else (np.asarray(pat[f"V{i}"], float), ab[1])


def mirror_raw_side(context, spec, src, line, dst, dline, k, M=None):
    """The index into dst's two mesh sides of dline (split_ab's [fall, other]) that is the mirror image of src's
    side k of line: a placed object's saved fold keeps that index, which follows the mesh, not the letters. Raises
    ValueError when either line's sides cannot be made (nothing is guessed)."""
    from . import fold_sides as FS
    from .core import draft as DR
    js = FS.job_spec(context, spec)
    a, b = _mesh_sides(context, js, src, line), _mesh_sides(context, js, dst, dline)
    if a is None or b is None or not all(len(s) for s in a[1] + b[1]):
        raise ValueError(tx("AC9 cannot match the saved fold to the other half: {0}", line))
    M = M or DR.mirror_fn(DR.piece(spec, src), DR.piece(spec, dst))
    c = np.asarray(M(a[0][a[1][int(k)]].mean(0)), float).reshape(-1)
    d = [np.linalg.norm(b[0][s].mean(0) - c) for s in b[1]]
    return int(np.argmin(d))


_apart_cache = {}


def named_apart(spec, piece, line):
    """The mirror lines of the line whose A is not the mirror image of its A (their hand not carried, a file from
    before 2026-11-19: there each line's A was its own left, so a pair's same side had opposite letters). Read on the
    Tool tab's draw: kept per spec text (store's key)."""
    from .core import draft as DR
    k = (store._cache.get('key'), piece, str(line))
    hit = _apart_cache.get(k)
    if hit is not None:
        return hit
    out = []
    f = next((x for x in spec.get('folds', []) if x.get('piece') == piece and x.get('line') == line), {})
    try:
        if not any(f.get(n) in ('A', 'B') for n in ('side', 'layers')):
            raise ValueError('no letters to name apart')
        src = DR.internal(DR.piece(spec, piece), line)
        for tw, tid, M in DR.mirror_lines(spec, piece, line):
            if FH.mapped_hand(src, DR.internal(DR.piece(spec, tw), tid), M) != FH.hand(DR.internal(DR.piece(spec, tw),
                                                                                                    tid)):
                out.append((tw, tid))
    except (KeyError, StopIteration, ValueError, TypeError):
        out = []
    if len(_apart_cache) > 64:
        _apart_cache.clear()
    _apart_cache[k] = out
    return out


def sync_twin(context, piece, line):
    """The line's fold written on its twin's line too (user 2026-10-25: a symmetric pair's fold is one), and on its
    mirror line across the center line of a piece edited symmetrically (user 2026-10-27): the same angle (seen from
    each pattern's face, both faces out), the same Turns and Layers letters -- each mirror line's hand made so that
    its A is the mirror image of this line's A (core/fold_hand.carry, 2026-11-19); none if none."""
    from .core import draft as DR
    spec = store.get(context)[0]
    tls = DR.mirror_lines(spec, piece, line) if spec else []
    if not tls:
        return None
    # a new spec dict: the folds a new list, a piece copied only when a line's hand changed (the Fold Angle slider
    # writes here on every step: the pieces' list kept keeps the outlines, store.write)
    store.put(context, DR.carry_fold(spec, piece, line, tls), keep=True, seams=False)
    return tls[0][:2]


def _physical_targets(spec, piece, line, context=None):
    """Resolve real authored lines only; an inconsistent saved shared shape is refused."""
    from .core import draft as DR
    effective = _effective(context or bpy.context, spec)
    logical = [(piece, line)] + [(p, l) for p, l, _ in DR.mirror_lines(effective, piece, line)]
    obs, targets = PLV.objects(spec), []
    for p, l in logical:
        ob = obs.get(p)
        if ob is not None:
            if any(str(d.get('line', '')).startswith(FR.PREFIX) for d in folded(ob)):
                raise ValueError(tx('AC9 cannot match the saved fold to the other half: {0}', l))
            if ob.get(PLV.TWIN_PROP) and DR.twin_line(effective, p, l) is None:
                raise ValueError(tx('AC9 cannot match the saved fold to the other half: {0}', l))
            if ob.get(PLV.TWIN_PROP):
                from .core import internal_sync as IS
                other = IS.partner(effective, p)
                if other is None or not IS.compatible(effective, p, other):
                    # Side/angle is about to be synchronized by write_angle; check that geometry matches
                    # independently of the old turn setting here, then prepare the new shared recipe.
                    geometry = dict(effective, folds=[], elastic=[])
                    if other is None or not IS.compatible(geometry, p, other):
                        raise ValueError(tx('AC9 cannot match the saved fold to the other half: {0}', l))
        owner = ob.get(PLV.PROP) if ob is not None else p
        actual = FR.physical_line(effective, p, l, owner)
        if actual is not None and (owner, actual) not in targets:
            targets.append((owner, actual))
    def order(key):
        ob = obs.get(key[0])
        history = folded(ob) if ob is not None else []
        return next((i for i, d in enumerate(history) if d.get('line') == key[1]), len(history)), key
    return sorted(targets, key=order)


def set_turn_side(context, piece, line, deg, side, *, preparation=None):
    """Change the turning side atomically, including mirrored lines and their placed meshes.

    A neutral fold must not silently discard later folds or vertex edits. When the exact
    pre-turn shape cannot be restored independently, keep the entire operation unchanged.
    """
    from .core import draft as DR
    from . import overlay
    spec = store.get(context)[0]
    try:
        todo = _physical_targets(spec, piece, line, context)
        if side == 'NONE':
            for p, l in todo:
                ob = _object(spec, p)
                done = folded(ob) if ob is not None else []
                k = next((j for j, d in enumerate(done) if d.get('line') == l), None)
                if k is None:
                    continue
                if PLV.has_edits(ob):
                    raise ValueError(tx("Cannot change Turns: the adjusted shape has vertex edits; its settings and shape were kept"))
                batch = {ll for pp, ll in todo if pp == p}
                if any(d.get('line') not in batch for d in done[k + 1:]):
                    raise ValueError(tx("Cannot change Turns: later folds depend on this turn; its settings and shape were kept"))
                attr = ob.data.attributes.get(f"{BEFORE_ATTR}{k}")
                if attr is None or len(attr.data) != len(ob.data.vertices):
                    raise ValueError(tx("Cannot change Turns: the saved shape before this turn is missing; its settings and shape were kept"))
        # an old collar line turned when placed (no Turns): once Turns is written, placing no longer turns it, so the
        # piece is made again without that turn and folded here (else it would be folded twice)
        was_legacy = legacy_collar(spec, piece, line)
        backup = PLV.PreparedResetBackup(context, spec)
        try:
            with PB.operation(context, owner=preparation, consumer_backup=backup) as preparation:
                write_angle(context, piece, line, deg, side)
                PB.replan_source(preparation)
                from . import placement as PL
                if was_legacy and PL.show_start(context, store.get(context)[0], [piece], preparation=preparation) is not None:
                    for why in PL.refold_all(context, [piece], preparation=preparation):
                        raise ValueError(why)
                else:
                    refold_pair(context, piece, line, deg, strict=True, preparation=preparation)
            if preparation.canceled:
                raise preparation.failure or ValueError('Fold preparation was canceled')
        except Exception:
            backup.rollback()
            _cache.clear()
            raise
        finally:
            backup.close()
    except Exception as exc:
        PB.cancel(preparation)
        _note(piece, line, (False, str(exc)))
        overlay.invalidate()
        return False, str(exc)
    overlay.invalidate()
    return True, ''


@PB.prepared_consumer
def refold_pair(context, piece, line, deg, strict=False, *, preparation=None):
    """refold() for the line and its twin's: on each piece that holds its own adjusted shape (a linked pair's object
    folds the twin's half with it; a follower alone is refused, so it is folded through its leader's line)."""
    from .core import draft as DR
    spec = store.get(context)[0]
    if spec is not None and abs(deg) >= 1e-6 and spec_side(spec, piece, line) != 'NONE':
        # the pair's objects made first when the piece has none, so the one that leads is known
        spec = _with_placed(context, spec, piece, preparation=preparation) or store.get(context)[0]
    todo = _physical_targets(spec, piece, line) if spec else []
    own = [(p, l) for p, l in todo if _object(spec, p) is not None]
    res = None
    accepted = False
    for p, l in own or todo[:1]:
        with preparation.attempt() as child:
            r = refold(context, p, l, deg, preparation=preparation)
            if strict and r is not None and not r[0]:
                raise ValueError(r[1])
            if r is not None and r[0]:
                child.accept()
                accepted = True
        res = r if (p, l) == (piece, line) or res is None else res
    for p, l in todo:
        if (p, l) not in own and own:
            _why.pop((p, str(l)), None)            # folded through its pair's object
    if not accepted and res is not None and not res[0]:PB.refuse(preparation, res[1])
    return res


_why = {}       # (piece, line) -> why the Tool tab's fold did not fold the adjusted shape, shown under the field


def why_not(piece, line):
    """The reason the last fold of the line on the adjusted shape did not happen, or None (the Tool tab shows it)."""
    return _why.get((piece, str(line)))


def _note(piece, line, res):
    if res is not None and not res[0]:
        _why[(piece, str(line))] = res[1]
    else:
        _why.pop((piece, str(line)), None)
    return res


@PB.prepared_consumer
def _with_placed(context, spec, piece=None, *, preparation=None):
    """The spec once the adjusted shapes are there to fold: shown first when none is (what the old Fold button did),
    None after a Simulate with them gone (the angle works while simulating). A piece placed after the others had
    their objects (a pocket put on its cylinder once the rest had been simulated) gets its own made then, it alone:
    the field folded nothing, saying "Move by Hand first" (user 2026-10-25); one on the dressed garment (simulated,
    not Reset) is left there, its angle working while simulating."""
    from . import dress as D
    if piece is not None and piece in spec.get('placement', {}) and PLV.objects(spec).get(piece) is None:
        g = D.garment(spec)
        try:
            dressed = set(json.loads(g.get('ac9_piece_names', '[]'))) if D.has_result(g) else set()
        except (TypeError, ValueError):
            dressed = set()
        if piece in dressed - set(D.reset_pieces(g)):
            return None
        if dressed:
            # placed after the garment was simulated: its shape made and sewn on from it by the next Phase Sim
            # (placement.give_shape, user 2026-10-25 b-2), not only shown
            from . import placement as PL
            return PL.give_shape(context, spec, [piece], preparation=preparation)[0]
        try:
            res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
        except ValueError:
            PB.cancel(preparation)
            return spec
        PLV.build(context, store.get(context)[0], res['pat'], res['tris'], res['X'], res['pairs'], only=[piece])
        PLV.invalidate_shown()
        return store.get(context)[0]
    if PLV.objects(spec):
        return spec
    if D.has_result(D.garment(spec)):
        return None
    from . import placement as PL
    PL.show_placed(context, placed_only=True, preparation=preparation)
    return store.get(context)[0]


@PB.prepared_consumer
def refold(context, piece, line, deg, prepared=None, *, preparation=None):
    """The Tool tab's Fold (deg) changed: the adjusted shape folded over the line at once at the new angle, on the side
    in the spec, the first time too (user 2026-10-24: the panel is the only way in, no Fold button); 0 lays it back
    flat. (ok, message), or None when there is no adjusted shape to fold (the angle works while simulating). A line
    it cannot fold keeps its reason for the Tool tab (why_not)."""
    spec = _effective(context, store.get(context)[0])
    if spec is None or piece not in spec.get('placement', {}):
        return _note(piece, line, None)
    if not str(line).startswith(SEAM_KEY) and spec_side(spec, piece, line) == 'NONE':
        deg = 0.0          # undo only the placed turn; the signed fold angle in the spec stays active in the solver
    spec = _with_placed(context, spec, piece, preparation=preparation) if abs(deg) >= 1e-6 else spec
    spec = _effective(context, spec)
    if spec is None:
        return _note(piece, line, None)
    ob = _object(spec, piece)
    if abs(deg) >= 1e-6:
        info = None
        if prepared is not None and not str(line).startswith(SEAM_KEY):
            info, why = sides(context, spec, piece, line, prepared=prepared, preparation=preparation)
            if info is None:
                return _note(piece, line, PB.refuse(preparation, why))
        return _note(piece, line, fold(context, spec, piece, line, deg, None, info=info, preparation=preparation))
    done = folded(ob) if ob is not None else []
    k = next((j for j, d in enumerate(done) if d.get('line') == line), None)
    if k is None:
        return _note(piece, line, None)
    restored = _unfold(ob, done, k)
    if not restored:
        info, _why_ = _info(context, spec, piece, line, preparation=preparation)
        restored = _unfold(ob, done, k, info)
    if not restored:
        return _note(piece, line, PB.refuse(preparation, tx("{0} is folded already and its shape before it is gone: Back to Placed Shape "
                                              "first", line)))
    PLV.invalidate_shown()
    return _note(piece, line, (True, tx("{0}: {1} laid flat again", piece, line)))


def seam_turn(spec, sm):
    """The piece that turns at a seam's fold: the one set (its fold's 'turn'), else spec.turn_default."""
    f = next((f for f in spec.get('folds', []) if f.get('seam') == sm['id']), {})
    return f.get('turn') if f.get('turn') in (sm['a']['piece'], sm['b']['piece']) else S.turn_default(spec, sm)


@PB.prepared_consumer
def refold_seam(context, sid, deg, turn=None, *, preparation=None):
    """refold() for a seam's Fold (deg) (user 2026-10-23, the first time too 2026-10-24): its turning piece turns as a
    whole over its edge of the seam (seam_turn, or `turn` when the Seams list's Turns changed it: the other piece
    is laid back flat), 0 lays it back flat. The turning piece is written with the angle."""
    spec = store.get(context)[0]
    sm = next((s for s in (spec or {}).get('seams', []) if s['id'] == sid), None)
    if sm is None or sm['a']['piece'] == sm['b']['piece']:
        return None
    keys = [SEAM_KEY + r for r in S.seam_run(spec, sm)]    # folded from either half of a run
    given = turn is not None
    turn = turn or seam_turn(spec, sm)
    for piece in (sm['a']['piece'], sm['b']['piece']):
        ob = _object(spec, piece)
        k = next((d.get('line') for d in (folded(ob) if ob is not None else []) if d.get('line') in keys), None)
        if k is not None:
            requested = piece == turn or abs(deg) < 1e-6
            with preparation.attempt() as child:
                result = refold(context, piece, k, deg if requested else 0.0, preparation=preparation)
                if result is not None and result[0]:child.accept()
            if requested:
                if result is not None and not result[0]:PB.refuse(preparation, result[1])
                return result
    if abs(deg) < 1e-6:
        return None
    picked = turn
    with preparation.attempt() as child:
        res = refold(context, turn, SEAM_KEY + sid, deg, preparation=preparation)
        if res is not None and res[0]:child.accept()
    if res is not None and not res[0] and not given:
        # An expected refusal of the preferred half is recovered before trying the alternative.
        o = sm['b']['piece'] if turn == sm['a']['piece'] else sm['a']['piece']
        with preparation.attempt() as child:
            res2 = refold(context, o, SEAM_KEY + sid, deg, preparation=preparation)
            if res2 is not None and res2[0]:child.accept()
        if res2 is not None and res2[0]:
            _why.pop((turn, SEAM_KEY + sid), None)
            picked, res = o, res2
    if res is not None and not res[0]:PB.refuse(preparation, res[1])
    if res is not None and res[0]:
        write_angle(context, picked, SEAM_KEY + sid, deg)  # the turning piece with it
        PB.replan_source(preparation)
    return res


@PB.prepared_consumer
def fold(context, spec, piece, line, deg, side, info=None, *, preparation=None):
    """Fold the piece's placed object over `line` by deg (+ mountain seen from the pattern's face = turned behind it), side 0 or 1
    turning (None: side_index). Returns (ok, message)."""
    from . import dress as D
    if not str(line).startswith(SEAM_KEY) and spec_side(spec, piece, line) == 'NONE':
        return True, ''     # no direct caller may turn a whole side for an unspecified fold
    if abs(deg) < 1e-6:
        return PB.refuse(preparation, tx("{0}: set its fold angle first (Fold Angle)", line))
    if info is None:
        info, why = _info(context, spec, piece, line, preparation=preparation)
        if info is None:
            return PB.refuse(preparation, why)
    if side is None:
        side = 0 if info.get('seam') is not None else side_index(spec, piece, line, info)
    ob = info['ob']
    done = folded(ob)
    redone = ''
    k = next((j for j, d in enumerate(done) if d.get('line') == line), None)
    if k is not None:
        # folded already: back to its shape before that fold (0 degrees), then folded again at the angle now - a
        # replacement, not added on (user 2026-10-22: 45 degrees to look, then 180)
        if not _unfold(ob, done, k, info):
            return PB.refuse(preparation, tx("{0} is folded already and its shape before it is gone: Back to Placed Shape first", line))
        if len(done) > k + 1:
            redone = tx(" (folded after it, undone: {0})", ", ".join(str(d.get('line')) for d in done[k + 1:]))
        done = done[:k]
        co = np.empty(len(ob.data.vertices) * 3)
        ob.data.vertices.foreach_get('co', co)
        info = dict(info, Xid=_to_guide(info, PLV.generated_co(ob, co.reshape(-1, 3)) * 1000.0))
    # an old fold with no side: placing the piece turned it already (dress.py collar_prefold); a line with Turns set
    # is folded here at any angle (user 2026-11-02, DECISIONS cloth-sim-no-auto-judgment-default-rules-2026-11-02)
    if legacy_collar(spec, piece, line, deg) and not done and info.get('seam') is None:
        return PB.refuse(preparation, tx("{0} is turned when the collar is placed (no Turns set): pick Turns A or B to fold it here",
                         line))
    co, nf = _shape(info, side, deg)
    # folded at this angle the turned side may need more length than the pattern has (a collar turned out round the
    # neck: its far edge goes round a wider circle): folded anyway, the stretch said and the object red
    stretch = _stretch(info, co)
    me = PLV.writable_mesh(ob)
    before = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get('co', before)
    me.attributes.new(f"{BEFORE_ATTR}{len(done)}", 'FLOAT_VECTOR', 'POINT').data.foreach_set(
        'vector', before.astype(np.float32))
    me.vertices.foreach_set('co', co.astype(np.float32).ravel())
    gen = me.attributes.get(PLV.GEN_ATTR)
    gen.data.foreach_set('vector', PLV.generated_co(ob, co).astype(np.float32).ravel())
    me.update()
    ob[FOLD_PROP] = json.dumps(done + [{'line': line, 'deg': float(deg), 'side': int(side)}])
    PLV.invalidate_shown()
    msg = tx("{0} folded over {1} ({2:+.0f} deg): {3} vertices", piece, line, deg, nf) + redone
    if info.get('seam') is None:
        ab = side_ab(info, side)
        msg += tx(", side {0} moves", ab)
        source_piece, source_line = FR.origin(spec, piece, line)
        if spec_side(store.get(context)[0], source_piece, source_line) != ab:
            write_angle(context, source_piece, source_line, deg, ab)
            PB.replan_source(preparation)
    if ob.get(PLV.TWIN_PROP):
        msg += tx(" (its pair {0} too)", ob[PLV.TWIN_PROP])
    if stretch > STRETCH_MAX:
        from . import diagnose as DG
        DG.mark(ob, 'fold', None, f"the fold stretches it x{stretch:.2f}")
        msg += tx(" -- this fold stretched it x{0:.2f}; use Diagnose to inspect the current shape", stretch)
    return True, msg


def _stretch(info, co):
    """The most an edge of the piece is stretched by the fold: its length in the shape co (n, 3, the object's space)
    against its length before the fold (a band placed already stretched round the body is not the fold's doing)."""
    T = np.asarray(info['tris'][info['i']], np.int64)
    if not len(T):
        return 1.0
    E = np.unique(np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], 1), axis=0)
    X = info['Xid']
    l2 = np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1)
    l3 = np.linalg.norm(co[E[:, 0]] - co[E[:, 1]], axis=1) / PLV.MM
    ok = l2 > 1e-6
    return float((l3[ok] / l2[ok]).max()) if ok.any() else 1.0


def legacy_collar(spec, piece, line, deg=None):
    """True for a fold dress.prepare turns when the piece is placed (collar_prefold): a line of a piece on the neck
    guide, 90 degrees or more, with no Turns written (read from a BOM, or set before Turns existed). A line with Turns
    set is folded on the adjusted shape here instead, at any angle (user 2026-11-02)."""
    if spec is None or str(line).startswith(SEAM_KEY) or spec.get('collar_prefold') is False:
        return False
    f = next((f for f in spec.get('folds', []) if f.get('piece') == piece and f.get('line') == line), None)
    if f is None or f.get('side') is not None:
        return False
    d = float(f.get('deg') or 0.0) if deg is None else float(deg)
    return spec.get('placement', {}).get(piece, {}).get('guide') == 'neck' and abs(d) >= 90.0


def draw_why(layout, context, piece, line):
    """Under a Fold field: why the adjusted shape was not folded, wrapped to the sidebar (Local Sim's button under it
    taken out, user 2026-11-22: a Simulate run shows the fold)."""
    import textwrap
    why = why_not(piece, line)
    if not why:
        return
    col = layout.column(align=True)
    for k, t in enumerate(textwrap.wrap(why, 34)):
        col.label(text=t, icon='INFO' if k == 0 else 'BLANK1')


CLASSES = ()


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
