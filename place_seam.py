# SPDX-License-Identifier: GPL-3.0-or-later
"""Part to Seam: carry a small placed piece (a collar, a cuff, a pocket) onto the pieces it is sewn to before
simulating, with no physics (user 2026-10-22, DECISIONS cloth-sim-place-tools-fold-lay-attach-2026-10-22; Fable
docs/reviews/REVIEW_fable_place_tools_r4_2026-10-22.md). Live and Simulate then start with its seams closed.

One promise (user 2026-11-04, DECISIONS cloth-sim-part-to-seam-one-promise-2026-11-04; Fable
docs/reviews/REVIEW_fable_part_to_seam_2026-11-04.md): the sewn points go onto their partners' points as placed now
(hand moves and folds included), on the explicitly chosen partners with enabled seams. One way: rigid (Kabsch), then bent (each
vertex moved by the sewn points' remaining moves, weighted 1/d^2 by the distance on the pattern, or the prepared
3D shape when it has placed folds, so nearby layers move together). Then Settle with
the sewn points held (out of the body, onto its side of each partner, edges back to length). Stretch is shown in
Overlays > Stretch and the one line, what is left in the body or through cloth is red; refused only when a triangle
would be squashed (the solver cannot start). Sewn on, a piece that would be red in Overlays > Stretch (over 1.5, its
partners far apart) keeps its shape instead: carried near all of them (rigid) and settled, the gap said. The target picker scopes this operation only; it never changes seam states and never
moves the piece further out (Move Outside, pressed apart).

Before: four promises' checks stacked up (shape kept or refused 10/22, settled and red 10/24, moved outside 10/30,
made whenever it can be simulated 11/02): the stretch was judged before Settle, partners with the fewest stitches
were dropped one by one (one of two fronts dropped by its name), and the outside step moved the piece 10-20 mm off
the seams it had just reached. A shirt's collar stand on 5 partners was left 10-99 mm off one front.
The vertices and their made-at positions (GEN_ATTR) are rewritten and it gets SEAM_PROP, as Fold does. A shared
Mirror's other half moves with it; its old position is not an obstacle. Not followed: a partner moved after (move
this piece again; the message names it)."""
from . import prepare_build as PB
from . import units as U
import json
import os

import bpy
import numpy as np
from mathutils import Matrix

from . import placed_view as PLV
from . import store
from .core import spec as S
from .i18n import tx
from . import selection as SEL
from .core import place_targets as TS

SEAM_PROP = PLV.SEAM_PROP
MIN_STITCHES = 3


def _object(spec, piece):
    ob = PLV.objects(spec).get(piece)
    return ob if ob is not None and ob.get(PLV.PROP) == piece else None


def _edges(T):
    T = np.asarray(T)
    return np.unique(np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1), axis=0)


def _stretch(E, V, X):
    r = np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1) / np.maximum(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1),
                                                                      1e-9)
    return float(np.percentile(r, 99))


def _kabsch(P, Q):
    pc, qc = P.mean(0), Q.mean(0)
    U, _, Wt = np.linalg.svd((P - pc).T @ (Q - qc))
    R = Wt.T @ np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt.T @ U.T))]) @ U.T
    return R, qc - pc @ R.T


def _bend(Fl, vi, X, Q):
    dd = np.linalg.norm(Fl[:, None, :] - Fl[vi][None, :, :], axis=2) + 1e-6
    w = 1.0 / dd ** 2
    return X + (w @ (Q - X[vi])) / w.sum(1, keepdims=True)


def _bend_folded(Fl, vi, X, Q):
    """Carry nearby folded layers together using their prepared spatial distances."""
    Y = _bend(X, vi, X, Q)
    # Distinct sewn vertices may overlap in 3D but have different targets.
    # Keep their original material-space result, including duplicate-vi targets.
    on = np.unique(vi)
    Y[on] = _bend(Fl, vi, X, Q)[on]
    return Y


def shapes(context, spec, *, preparation=None):
    """(pat, tris, X world mm per piece where it is now) or (None, why): a piece with a placed object as that object
    shows it (hand moves, folds and pairs' mirrors in), else as the dressed garment shows it, else on its guide (user
    2026-10-24: a collar goes onto the dressed collar stand, whatever state the bodice is in; DECISIONS
    cloth-sim-seam-tools-any-state-settle-2026-10-24)."""
    from . import dress as D
    try:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
    except ValueError as exc:
        PB.cancel(preparation)
        return None, str(exc)
    X, took, _ = D._hand_placed(spec, res['pat'], list(res['X']))
    X = D._mirror_hand(X, took, res['pairs'], spec, res['pat']['names'])
    g = bpy.data.objects.get(D.garment_name(spec))
    job = D.job_or_from_mesh(context, g) if D.has_result(g) else None   # the job folder lost: made from the garment
    if job:
        try:
            X, _c = D._from_last_result(job, res['pat'], X, shown=D.shown_shape(context, g),
                                        redo=set(PLV.objects(spec)), redo_shown=False)
        except (OSError, KeyError, ValueError):
            pass                          # a job that does not read: the pieces stay on their guides
    return (_with_guide(res), res['tris'], [np.asarray(x, float) for x in X]), None


def _with_guide(res):
    """prepare's pattern with the pieces' shapes as made on their guides (guide_X): the stretch a move is measured
    against (Fable 2026-10-25: against the placed object it rose on every press, 1.044 -> 1.243 -> 1.375)."""
    return dict(res['pat'], guide_X=[np.asarray(x, float) for x in res['X']])


def on_garment(spec, names):
    """Of `names`, the pieces that are part of the dressed garment now (in its result and not Reset)."""
    from . import dress as D
    g = bpy.data.objects.get(D.garment_name(spec))
    if not D.has_result(g):
        return []
    done, back = D.garment_pieces(g), set(D.reset_pieces(g))
    return [n for n in names if n in done and n not in back]


@PB.prepared_consumer
def objects_for(context, spec, names, *, preparation=None):
    """The placed objects of `names` (and their pairs) made when missing: on a dressed garment those alone, else all
    of them (as Show Placed does). Returns the spec as stored after."""
    from . import dress as D
    from . import placement as PL
    g = bpy.data.objects.get(D.garment_name(spec))
    want = set(PL.with_pairs(spec, list(names)))
    have = set(PLV.objects(spec))
    if not D.has_result(g):
        if not have:
            PL.show_placed(context, placed_only=True, preparation=preparation)
        elif want - have:
            # some pieces shown as objects, these not yet (a band Placed after a skirt was moved by hand: Band on
            # Edge said "Move by Hand first", user 2026-10-25): these alone made, the others left as they are
            res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
            PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'], only=want - have)
        return store.get(context)[0]
    if want - have:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
        PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'], only=want | have)
    return store.get(context)[0]


def mark_on_next_run(spec, names, *, preparation=None):
    """On a dressed garment: `names` (and their pairs) are sewn on from their placed shapes by the next Simulate,
    as pieces Reset are (dress.RESET_PROP)."""
    from . import dress as D
    from . import placement as PL
    g = bpy.data.objects.get(D.garment_name(spec))
    if not D.has_result(g):
        return
    D.set_shown_as_placed(g, spec, set(D.reset_pieces(g)) | set(PL.with_pairs(spec, list(names))))


def sewn_to_placed(spec, piece):
    """The placed pieces `piece` is sewn to (by the spec's seams, no meshing)."""
    pl = spec.get('placement', {})
    out = []
    for sm in spec.get('seams', []):
        a, b = sm['a']['piece'], sm['b']['piece']
        for x, y in ((a, b), (b, a)):
            if x == piece and y != piece and y in pl and y not in out:
                out.append(y)
    return out


@PB.prepared_consumer
def place_by_partners(context, spec, piece, targets=None, *, preparation=None):
    """A piece not placed yet put on the guide of the placed pieces it is sewn to, where its sewn points' partners
    are (user 2026-10-22: a small part or a band need not go round a cylinder first when what it is sewn to is
    there). Its pair follows as its mirror image. Returns (spec, None) or (None, why); the spec is stored and the
    placed objects of the piece (and its pair) made."""
    from . import dress as D
    from . import placement as PL
    from .core import body_axes as BA
    partners = sewn_to_placed(spec, piece)
    if targets is not None:
        partners = [n for n in partners if n in targets]
    if not partners:
        return None, tx("{0} is not placed and not sewn to a placed piece: Place it first", piece)
    pl = spec['placement']
    guides = [pl[p]['guide'] for p in partners]
    gname = max(set(guides), key=guides.count)
    ref = pl[partners[guides.index(gname)]]
    outlines = store.get(context)[1]
    old = S.loads(S.dumps(spec))

    def put(theta, h, flat=None):
        sp = S.loads(S.dumps(old))
        anchor = np.round(PL.piece_anchor(outlines[piece]), 3).tolist()
        if flat is not None:
            # partners laid flat (user 2026-11-10): in their plane, the anchor at o
            new = {piece: {'guide': S.FLAT, 'flat': flat, 'anchor': anchor}}
        else:
            new = {piece: PL.with_tilt({'guide': gname, 'theta': round(float(theta), 3), 'h': round(float(h), 3),
                                        'anchor': anchor},
                                       old.get('placement', {}).get(piece, {}).get('tilt', 0.0))}
        sp['placement'].update(new)
        sp['placement'], _ = S.mirror_twins(sp, sources=set(new))
        store.put(context, sp)
        PB.replan_source(preparation)
        return sp

    # first where the partner is, then where its partners' sewn points are
    flat_ = gname == S.FLAT
    sp = put(None, None, ref['flat']) if flat_ else put(ref['theta'], ref['h'])
    got, why = shapes(context, sp, preparation=preparation)
    if got is None:
        store.put(context, old)
        PB.replan_source(preparation)
        return None, why
    pat, _t, X = TS.filter_stitches(got, piece, targets)
    names = [str(n) for n in pat['names']]
    i = names.index(piece)
    st = np.asarray(pat['stitch'])
    Q = [X[int(r[2])][int(r[3])] for r in st if r[0] == i and r[2] != i] + \
        [X[int(r[0])][int(r[1])] for r in st if r[2] == i and r[0] != i]
    if Q and flat_:
        # in the partner's plane, its anchor where the sewn points are on average
        R_ = np.asarray(ref['flat']['R'], float)
        c = np.mean(Q, 0)
        o_ = np.asarray(ref['flat']['o'], float)
        c = c - ((c - o_) @ R_[2]) * R_[2]
        sp = put(None, None, {'o': [round(float(v), 3) for v in c], 'R': ref['flat']['R']})
    elif Q:
        g = sp['guides'][gname]
        d = np.mean(Q, 0) - np.asarray(g['o'], float)
        sp = put(BA._theta_of(g, d), float(d @ np.asarray(g['R'], float)[2]))
    try:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True, preparation=preparation)
    except ValueError as exc:
        PB.cancel(preparation)
        store.put(context, old)
        PB.replan_source(preparation)
        return None, str(exc)
    dressed = D.has_result(bpy.data.objects.get(D.garment_name(sp)))
    PLV.build(context, sp, res['pat'], res['tris'], res['X'], res['pairs'],
              only=PL.with_pairs(sp, [piece]) if dressed else None)
    # the shapes as shapes() reads them, kept for the move that follows (it made them a third time: 1 s of 3.4)
    X, took, _ = D._hand_placed(sp, res['pat'], list(res['X']))
    X = D._mirror_hand(X, took, res['pairs'], sp, res['pat']['names'])
    _fresh.update(text=store.text(context).as_string(), got=(_with_guide(res), res['tris'], [np.asarray(x, float) for x in X]))
    return store.get(context)[0], None


_fresh = {'text': None, 'got': None}


def fresh_shapes(context):
    """The shapes place_by_partners just made, while the spec has not changed since (else None: shapes() again).
    Taken once: a simulation changes the garment and not the spec, and a band pressed again after the trousers were
    simulated went onto the trousers as they were placed before (user 2026-10-25, 62 mm in front of the hem)."""
    t = store.text(context)
    got = _fresh['got'] if t is not None and _fresh['text'] == t.as_string() else None
    _fresh.update(text=None, got=None)
    return got


def plan(context, spec, piece, got=None, bvh=None, *, preparation=None):
    """The move of `piece` onto every partner present in the operation-scoped stitches: {'X' (n, 3) world mm, 'R', 't' (the rigid
    part), 'gap' [p50, p99], 'stretch', 'partners', 'i', 'V', 'T'} or (None, why).

    One way, no choosing: rigid (Kabsch), then bent (each vertex moved by the sewn points' remaining moves, weighted
    1/d^2 by distance on the pattern, or on its prepared 3D shape for placed folds). Every partner is kept: a partner is given up only by its seam turned Off,
    never by the tool. On a shirt's collar stand (5 partners) the tool used to drop the partners with the fewest
    stitches when the stretch went over 1.3, and of the two fronts (19 stitches each) dropped one by its name: one
    side was left 10-99 mm off. Stretch is shown (Overlays > Stretch), not refused (DECISIONS
    cloth-sim-part-to-seam-one-promise-2026-11-04)."""
    if got is None:
        got, why = shapes(context, spec, preparation=preparation)
        if got is None:
            return None, why
    pat, tris, X = got
    names = [str(n) for n in pat['names']]
    if piece not in names:
        return None, tx("{0} is not placed", piece)
    i = names.index(piece)
    twin = {a: b for pr in S.placement_pairs(spec) for a, b in (pr, pr[::-1])}.get(piece)
    st = np.asarray(pat['stitch'])
    rows = [(int(r[1]), int(r[2]), int(r[3])) for r in st if r[0] == i and r[2] != i] + \
           [(int(r[3]), int(r[0]), int(r[1])) for r in st if r[2] == i and r[0] != i]
    rows = [r for r in rows if names[r[1]] != twin]
    if len(rows) < MIN_STITCHES:
        return None, tx("{0} is not sewn to another piece", piece)
    Fl = np.asarray(pat[f"V{i}"], float)
    Xi = X[i]
    vi = np.array([a for a, _, _ in rows])
    Q = np.array([X[j][b] for _, j, b in rows])
    R, t = _kabsch(Xi[vi], Q)
    from . import place_fold as PF
    ob = _object(spec, piece)
    bend = _bend_folded if ob is not None and PF.folded(ob) else _bend
    Y = bend(Fl, vi, Xi @ R.T + t, Q)
    g = np.linalg.norm(Y[vi] - Q, axis=1)
    return {'way': 'bent', 'X': Y, 'R': R, 't': t, 'i': i, 'V': Fl, 'T': tris[i],
            'partners': sorted({names[j] for _, j, _ in rows}),
            'gap': [float(np.median(g)), float(np.percentile(g, 99))],
            'stretch': _stretch(_edges(tris[i]), Fl, Y)}, None


def apply(context, spec, piece, p):
    """Put the plan on the piece's placed object: rigid as its pose, bent as its vertices (SEAM_PROP)."""
    ob = _object(spec, piece)
    M = np.array(ob.matrix_world)
    if p['way'] == 'rigid':
        A = np.eye(4)
        A[:3, :3] = p['R']
        A[:3, 3] = p['t'] * PLV.MM
        PLV.set_matrix(ob, Matrix((A @ M).tolist()))     # a pair's follower: its leader moves, mirrored
    else:
        why = write_shape(ob, p['V'], p['T'], p['X'])
        if why:
            return why
    ob[SEAM_PROP] = json.dumps({'way': p['way'], 'partners': p['partners']})
    PLV.invalidate_shown()
    return None


def write_shape(ob, V, T, Y):
    """Write a shape Y (world mm, in the order of the pattern's mesh: flat V, triangles T) into the placed object's
    vertices and their made-at positions (GEN_ATTR). An object made before the pattern was re-meshed (a seam added,
    its vertices no longer the mesh's: 4685 against 4686 in the user's file 10/22, written as they were the Rectangle's
    triangles stretched to 2192 x and the made-at write failed) gets Y through its layout points, as Dress reads it
    (dress._hand_placed). Returns None, or why nothing was written."""
    from . import dress as D
    me = ob.data
    n = len(me.vertices)
    fl, gen = me.attributes.get(PLV.FLAT_ATTR), me.attributes.get(PLV.GEN_ATTR)
    if fl is None or gen is None or len(gen.data) != n or n == 0:
        return tx("{0}'s placed object has lost its layout points: Back to Guide first", ob.get(PLV.PROP))
    Fp = np.empty(n * 2)
    fl.data.foreach_get('vector', Fp)
    Fp = Fp.reshape(-1, 2)
    V, Y = np.asarray(V, float), np.asarray(Y, float)
    same = n == len(V) and (ob.get(PLV.KK_PROP) or np.allclose(Fp, V, atol=1e-3))
    if not same:
        Y = D._bary_map(Fp, V, np.asarray(T), Y)
    M = np.array(ob.matrix_world)
    W = Y * PLV.MM
    co = (np.c_[W, np.ones(n)] @ np.linalg.inv(M).T)[:, :3]
    me.vertices.foreach_set('co', co.astype(np.float32).ravel())
    gen.data.foreach_set('vector', (co + PLV.origin_mm(ob) * PLV.MM).astype(np.float32).ravel())
    me.update()
    return None


@PB.prepared_consumer
def move(context, spec, piece, got=None, bvh=None, targets=None, *, preparation=None):
    """Fold the prepared shape, then fit it. Keep the pre-fit mesh across repeats and simulations."""
    from . import place_fold as PF
    ob = _object(spec, piece)
    if ob is None:
        return _move_prepared(context, spec, piece, got, bvh, targets, preparation=preparation)
    try:
        PLV.check_before_seam(spec, [piece])
    except ValueError as exc:
        PB.cancel(preparation)
        return PB.refuse(preparation, str(exc))
    PLV.sync_edit(ob)
    backup, record = ob.data.copy(), PLV.shape_record(ob)
    old_spec = S.loads(S.dumps(store.get(context)[0]))
    had_saved = piece in PLV.before_seam(spec)
    ok = False
    try:
        restored = PLV.restore_before_seam(spec, [piece], consume=False)
        if restored:
            got = None
            sp = PLV.sync_pose(context, spec)
            if sp is not None:
                store.put(context, sp)
                PB.replan_source(preparation)
                spec = sp
        # Fold only missing/changed lines. Reapplying every line would undo later folds.
        # Remove placed turns first: unfolding also removes later fold records,
        # which must be checked again even if their spec entry came first.
        folds = list(spec.get('folds', []))
        folds.sort(key=lambda f: not (f.get('piece') == piece and f.get('line')
                                     and not str(f['line']).startswith(PF.SEAM_KEY)
                                     and PF.spec_side(spec, piece, f['line']) == 'NONE'))
        for f in folds:
            if not f.get('deg'):
                continue
            sm = next((s for s in spec.get('seams', []) if s['id'] == f.get('seam')), None)
            if sm is not None:
                if PF.seam_turn(spec, sm) != piece:
                    continue
                line = PF.SEAM_KEY + sm['id']
            elif f.get('piece') == piece and f.get('line'):
                line = f['line']
            else:
                continue
            done = next((d for d in PF.folded(ob) if d['line'] == line), None)
            no_placed_turn = not str(line).startswith(PF.SEAM_KEY) and PF.spec_side(spec, piece, line) == 'NONE'
            if no_placed_turn:
                # No Side keeps the solver target but needs no new placed turn.
                # An existing turn still has to pass through refold's unfold path.
                if done is None:
                    continue
            elif done is not None and abs(float(done['deg']) - float(f['deg'])) < 1e-6:
                continue
            result = PF.refold(context, piece, line, float(f['deg']), preparation=preparation)
            if result is None or not result[0]:
                return PB.refuse(preparation, result[1] if result else tx("{0} has no placed shape: Place it first", piece))
            spec = store.get(context)[0]
            got = None
        ok, msg = _move_prepared(context, spec, piece, got, bvh, targets, preparation=preparation)
        return ok, msg
    finally:
        if ok:
            bpy.data.meshes.remove(backup)
        else:
            PLV.put_shape(ob, backup, record)
            if not had_saved:
                PLV.forget_before_seam(spec, [piece])
            store.put(context, old_spec)
            PB.replan_source(preparation)


def _move_prepared(context, spec, piece, got=None, bvh=None, targets=None, *, preparation=None):
    """Fit once from the folded prepared input: carried onto every partner (plan), then settled with its sewn points
    held (place_settle: out of the body, onto its side of each partner by GAP, edges back to length). What is left
    is red and said in the one line; refused only when a triangle would be squashed (the solver cannot start).
    Moving the piece further out is Move Outside, a separate press (user 2026-11-04: inside this button it moved
    the piece 10-20 mm off the seams it had just reached)."""
    from . import place_settle as PT
    from . import place_lay as PLY
    from . import placement as PL
    from . import dress as D
    ob = _object(spec, piece)
    if ob is None:
        twin = next((o for o in PLV.objects(spec).values() if o.get(PLV.TWIN_PROP) == piece), None)
        if twin is not None:
            return PB.refuse(preparation, tx("{0} follows {1} (a pair): Part to Seam on {1}, both move", piece, twin[PLV.PROP]))
        return PB.refuse(preparation, tx("{0} has no placed shape: Place it first", piece))
    if got is None:
        got, why = shapes(context, spec, preparation=preparation)
        if got is None:
            return PB.refuse(preparation, why)
    got = TS.filter_stitches(got, piece, targets)
    p, why = plan(context, spec, piece, got, bvh, preparation=preparation)
    if p is None:
        return PB.refuse(preparation, why)
    names = list(map(str, got[0]['names']))
    twin = ob.get(PLV.TWIN_PROP)
    xs = [p['X'] if k == p['i'] else x for k, x in enumerate(got[2])]
    if twin in names:
        # the shared Mirror's other half moves with it: not an obstacle where it was
        from .core import spec as S_
        pl_ = S_.pair_plane(spec, twin)
        mirrored = S_.mirror_points(p['X'], pl_, S_.mirror_center(spec, pl_, twin))
        xs[names.index(twin)] = mirrored
    g2 = (got[0], got[1], xs)
    i, V, T, fixed, partners, _o = PT.context_of(context, spec, piece, g2)
    partners = [(xs[j], got[1][j]) for j in range(len(names)) if names[j] in p['partners']]
    others = [(xs[j], got[1][j]) for j in range(len(names)) if names[j] not in (piece, twin)]
    if bvh is None:
        co, tri = PL.body_arrays(context, context.scene.ac9_cloth_sim.body)
        bvh = D.body_bvh(co, tri)
    before = PT.measure(p['X'], V, T, np.zeros(len(V), bool), bvh, others)
    Y, num = PT.settle(p['X'], V, T, fixed, bvh, partners, others)
    from . import diagnose as DG
    kept = None
    s1 = _stretch(_edges(T), V, Y)
    if s1 > DG.BAD:
        # sewn on, it would be red in Overlays > Stretch (its partners far apart: a jacket's collar stand sewn to the
        # collar and to the fronts 214 mm apart had 3.6 mm edges made 214 mm, p99 35 x; a pocket whose seam is longer
        # than its partner's, 3.6 x): its shape kept, carried near all of them (rigid) and settled, the seams close
        # while simulating (user 2026-11-04; the same yardstick as the overlay's red, no other number)
        kept = s1
        Xr = got[2][p['i']] @ p['R'].T + p['t']
        fixed = np.zeros(len(V), bool)
        Y, num = PT.settle(Xr, V, T, fixed, bvh, partners, others)
        st = np.asarray(got[0]['stitch'])
        rows = [(int(r[1]), int(r[2]), int(r[3])) for r in st if r[0] == i and names[int(r[2])] in p['partners']] +                [(int(r[3]), int(r[0]), int(r[1])) for r in st if r[2] == i and names[int(r[0])] in p['partners']]
        g = np.array([np.linalg.norm(Y[a] - xs[j][b]) for a, j, b in rows])
        p = dict(p, gap=[float(np.median(g)), float(np.percentile(g, 99))], way='kept')
    if PLY._squashed(V, T, Y):
        return PB.refuse(preparation, tx("{0} not moved: triangles would be squashed flat. Move it nearer by hand, or turn the far "
                         "seams Off", piece))
    # crossings: a triangle with a sewn vertex lies on its partner's edge (gap 0) and touches it, which is not a
    # crossing (20 of 24 on a shirt's collar stand); it counts against every other cloth
    rest = [(xs[j], got[1][j]) for j in range(len(names)) if names[j] not in (piece, twin) + tuple(p['partners'])]
    num.update(PT.measure(Y, V, T, fixed, bvh, others))
    if rest:
        ct = np.union1d(num['cross_t'], PT._crossing_tris(Y, T, rest, np.zeros(len(Y), bool)))
        num.update(cross_t=ct, cross=int(len(ct)))
    num['before'] = {k: before[k] for k in ('inside', 'cross', 'stretch')}
    p = dict(p, X=Y, stretch=_stretch(_edges(T), V, Y))
    PLV.save_before_seam(ob, spec)
    why = apply(context, spec, piece, p)
    if why:
        return PB.refuse(preparation, why)
    left = PT.mark(_object(spec, piece), V, T, num)
    if kept is None:
        msg = tx("{0} sewn onto {1}: gap {2}, stretch {3:.2f} (Overlays > Stretch)", piece,
                 ", ".join(p['partners']), U.fmt(bpy.context, p['gap'][0]), p['stretch'])
    else:
        msg = tx("{0} kept its shape, carried near {1}: sewn on, it would stretch {2:.1f} x. Gap {3}, the seams "
                 "close while simulating", piece, ", ".join(p['partners']), kept, U.fmt(bpy.context, p['gap'][0]))
    if left:
        msg += tx("; {0} inside the body, {1} through other cloth: red (Settle, Move by Hand, or simulate from here)",
                  num['inside'], num['cross'])
    # a piece moved to this one before goes on its old place: say so (not followed)
    after = sorted(o[PLV.PROP] for o in {o.name: o for o in PLV.objects(spec).values()}.values()
                   if o is not ob and o.get(SEAM_PROP) and piece in json.loads(o[SEAM_PROP]).get('partners', []))
    if after:
        msg += tx("; move {0} to its seams again", ", ".join(after))
    return True, msg


class AC9_OT_SimMoveToSeam(bpy.types.Operator):
    """Choose target pieces, then fit the selected pieces to their enabled seams with those targets. Shift-click adds targets, A chooses all, Enter applies, Esc cancels. Other seams stay enabled for simulation"""
    bl_idname = "ac9_cloth_sim.move_to_seam"
    bl_label = "Part to Seam"
    bl_translation_context = 'ac9_cloth_sim'
    bl_options = {'REGISTER', 'UNDO'}

    target_pieces: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    moving_pieces: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        from . import place_targets
        return place_targets.invoke(self, context, event)

    def modal(self, context, event):
        from . import place_targets
        return place_targets.modal(self, context, event)

    def cancel(self, context):
        from . import place_targets
        place_targets.finish(self, context)

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or not spec.get('placement'):
            return False
        from . import dress as D
        from . import placement as PL
        if D.AC9_OT_SimDress.running():
            cls.poll_message_set("Not while a Phase Sim or Live Sim runs")
            return False
        # why it is grey, on the button's tooltip (user 2026-10-22: could not tell why it could not be pressed)
        sel = PL.selected_pieces(context)
        if not sel:
            cls.poll_message_set("Select a piece first (on the pattern)")
            return False
        # the selected piece's own state only, not its partners' (user 2026-10-24)
        worn = on_garment(spec, sel)
        if worn and len(worn) == len(sel):
            cls.poll_message_set(tx("{0} is part of the dressed garment: Back to Placed it first (Placement)",
                                    ", ".join(worn[:3])))
            return False
        # not placed: placed by the pieces it is sewn to when they are (user 2026-10-22)
        if not any(n in spec['placement'] or sewn_to_placed(spec, n) for n in sel):
            cls.poll_message_set("The selected piece is not placed: Place it first")
            return False
        return True

    def execute(self, context, *, preparation=None):
        import copy
        from . import placement as PL
        from . import piece_move as PM
        if context.mode != 'OBJECT':
            self.report({'WARNING'}, tx("Return to Object Mode before correcting the current shapes"))
            return {'CANCELLED'}
        spec, _ = store.get(context)
        selected = [ob.name for ob in SEL.objects(context)]
        active = context.view_layer.objects.active.name if context.view_layer.objects.active else None
        from . import diagnose as DG
        live = {ob.name: (ob.data.name, tuple(ob.color), ob.hide_viewport, ob.get(DG.PROP))
                for ob in set(PLV.objects(spec).values())}
        archive_names = {prop: {n: me.name for n, me in getter(spec).items()}
                         for prop, getter in ((PLV.KEEP_PROP, PLV._kept),
                                              (PLV.BEFORE_SEAM_PROP, PLV.before_seam))}
        collections = set(bpy.data.collections.keys())
        center = PLV._center_object()
        center_matrix = center.matrix_world.copy() if center is not None else None
        handle_state = copy.deepcopy(PM._state)
        handle_matrices = {ob.name: ob.matrix_world.copy() for ob in PM.handles()}
        layers = []
        def remember(layer):
            layers.append((layer, layer.exclude, layer.hide_viewport, layer.collection.hide_viewport))
            for child in layer.children:
                remember(child)
        remember(context.view_layer.layer_collection)
        backup = PLV.PreparedResetBackup(context, spec)
        garment_mesh_name = backup.g.data.name if backup.g is not None else None
        groups = {ob.name: ([(g.name, g.lock_weight) for g in ob.vertex_groups],
                            ob.vertex_groups.active_index)
                  for ob in set(PLV.objects(spec).values()) | ({backup.g} if backup.g is not None else set())}
        def rollback():
            if not backup.can_restore():return
            # Group definitions live on the object, while weights live in its
            # mesh. Rebuild definitions before installing the untouched backup
            # mesh, so clearing the current definitions cannot erase old weights.
            for name, (definitions, active_group) in groups.items():
                ob = context.scene.objects.get(name)
                if ob is not None:
                    ob.vertex_groups.clear()
                    for group_name, locked in definitions:
                        ob.vertex_groups.new(name=group_name).lock_weight = locked
                    if definitions:
                        ob.vertex_groups.active_index = active_group
            backup.rollback()
            for name, (mesh_name, color, hidden, probs) in live.items():
                ob = context.scene.objects.get(name)
                if ob is not None:
                    ob.data.name, ob.color, ob.hide_viewport = mesh_name, color, hidden
                    if probs is None:
                        ob.pop(DG.PROP, None)
                    else:
                        ob[DG.PROP] = probs
            if backup.g is not None:
                backup.g.data.name = garment_mesh_name
            for prop, getter in ((PLV.KEEP_PROP, PLV._kept), (PLV.BEFORE_SEAM_PROP, PLV.before_seam)):
                for name, me in getter(spec).items():
                    if name in archive_names[prop]:
                        me.name = archive_names[prop][name]
            if center is not None:
                center.matrix_world = center_matrix
            elif not PLV.objects():
                made_center = PLV._center_object()
                if made_center is not None:
                    bpy.data.objects.remove(made_center, do_unlink=True)
            coll = bpy.data.collections.get(PLV.COLL_NAME)
            if coll is not None and coll.name not in collections and not coll.objects and not coll.children:
                bpy.data.collections.remove(coll)
            for layer, exclude, hidden, collection_hidden in layers:
                layer.exclude, layer.hide_viewport = exclude, hidden
                layer.collection.hide_viewport = collection_hidden
            for name, matrix in handle_matrices.items():
                ob = context.scene.objects.get(name)
                if ob is not None:
                    ob.matrix_world = matrix
            PM._state.clear()
            PM._state.update(handle_state)
            PL.AC9_OT_SimMoveByHand._restore_selection(context, selected, active)
        try:
            with PB.operation(context, owner=preparation, consumer_backup=backup) as preparation:
                result = self._execute(context, preparation=preparation)
                if 'CANCELLED' in result:
                    preparation.cancel()
            if preparation.canceled:
                rollback()
                return {'CANCELLED'}
            return result
        except (ValueError, RuntimeError) as exc:
            PB.cancel(preparation)
            rollback()
            self.report({'WARNING'}, tx("Part to Seam canceled: {0}. Original shapes kept", tx(str(exc))))
            return {'CANCELLED'}
        except Exception:
            rollback()
            raise
        finally:
            backup.close()

    def _execute(self, context, *, preparation=None):
        from . import placement as PL
        from . import place_settle as PT
        spec, _ = store.get(context)
        from . import place_targets
        requested, targets = place_targets.arguments(self, context)
        place_targets.validate(context, requested, targets)
        names = [n for n in PL.lead_only(spec, requested)
                 if n in spec['placement'] or sewn_to_placed(spec, n)]
        worn = on_garment(spec, names)
        if worn:
            self.report({'WARNING'}, tx("{0} is part of the dressed garment: Back to Placed it first (Placement)",
                                        ", ".join(worn)))
            names = [n for n in names if n not in worn]
        lines0 = []
        for n in [n for n in names if n not in spec['placement']]:
            sp, why = place_by_partners(context, spec, n, targets=targets, preparation=preparation)
            if sp is None:
                raise ValueError(why)
            spec = sp
            lines0.append(tx("{0} placed by the pieces it is sewn to", n))
        for m in lines0:
            self.report({'INFO'}, m)
        spec = objects_for(context, spec, names, preparation=preparation)
        # a pair's mirrored side: its object's own piece moves (the mirror follows)
        lead = {o[PLV.TWIN_PROP]: o[PLV.PROP] for o in PLV.objects(spec).values() if o.get(PLV.TWIN_PROP)}
        names = list(dict.fromkeys(n if _object(spec, n) is not None else lead.get(n, n) for n in names))
        if not names:
            return {'CANCELLED'}
        lines, moved_names = [], []
        for n in names:
            with preparation.attempt() as child:
                ok, msg = move(context, spec, n, got=fresh_shapes(context) if len(names) == 1 and lines0 else None,
                               targets=targets, preparation=preparation)
                if ok:
                    mark_on_next_run(store.get(context)[0], [n], preparation=preparation)
                    child.accept()
            spec = store.get(context)[0]
            if not ok:
                # Expected piece refusals keep successful pieces; a fatal exception still rolls back the root.
                self.report({'WARNING'}, msg)
                continue
            lines.append(msg)
            moved_names.append(n)
        if not moved_names:
            return {'CANCELLED'}
        sp = PLV.sync_pose(context, spec)
        if sp is not None:
            store.put(context, sp)
            PB.replan_source(preparation)
        # one line a piece: a warning when red is left on it. Moving it further out is Move Outside, pressed apart.
        obs = PLV.objects(store.get(context)[0])
        for n, m in zip(moved_names, lines):
            ob = obs.get(n)
            red = ob is not None and ob.vertex_groups.get(PT.LEFT_GROUP) is not None
            self.report({'WARNING'} if red else {'INFO'}, m)
        # the button acts once: it no longer writes the piece's Start (user 2026-11-03, a press had become a rule
        # that moved the piece after every Place, its user unaware of it)
        return {'FINISHED'}


CLASSES = (AC9_OT_SimMoveToSeam,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
