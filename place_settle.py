# SPDX-License-Identifier: GPL-3.0-or-later
"""Settle: a placed piece tidied with no physics, so a Simulate does not start from cloth already through cloth (it
cannot undo that: 108 crossings stayed 110-122 whatever was tried, 2026-10-18) -- user 2026-10-24, DECISIONS
cloth-sim-seam-tools-any-state-settle-2026-10-24; Fable docs/reviews/REVIEW_fable_seam_tools_untangle_2026-10-24.md
section 4-2.

The piece moves alone; its sewn points, the body and the other cloth stay. Repeated ITERS times:
  S1 out of the body: a vertex under GAP mm from the body (and no deeper than CAP) goes out along the nearest face's
     normal to GAP; deeper ones are left (the nearest face may be another part of the body there) and marked.
  S2 to its side of each piece it is sewn to: the side is where most of its vertices near that piece already are;
     a vertex on the other side, or closer than GAP, goes along that piece's normal to GAP on its side. A vertex whose
     nearest point is at that piece's edge (outside its shadow) is left.
  S3 edge lengths back: each edge into LEN_BAND of its length on the pattern, the sewn points held.
     Moving vertex paths are clipped before crossing static other cloth. Initial contacts may separate;
     this does not cover reverse vertex/face, edge/edge, coplanar motion, or existing intersections.
Then measured (the body, crossings with the other cloth, stretch, squashed triangles): what is left is marked red on
the object and said in one line, the shape is kept (user: a check that only refuses is of little use).

Not solved: two pieces that both move (the two fronts), a folded piece whose partner's edge should go between its
two layers, the deep body. Those stay red."""
from . import prepare_build as PB
import json

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import placed_view as PLV
from . import store
from .i18n import tx

GAP = 2.0          # mm off the body and off a partner (solver/lay.py GAP_MM)
CAP = 10.0         # mm: deeper in the body than this is not pushed out
NEAR = 10.0        # mm: a vertex this near a partner counts for its side and is kept on it
LEN_BAND = 0.05    # edges back into +-5 % of their pattern length
ITERS = 12
LEFT_GROUP = "AC9_settle_left"
RED = (1.0, 0.25, 0.2, 1.0)
OK = (0.55, 0.75, 1.0, 1.0)


def _edges(T):
    T = np.asarray(T)
    return np.unique(np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1), axis=0)


def _bvh_mm(X, T):
    return BVHTree.FromPolygons([tuple(map(float, p)) for p in X], np.asarray(T).tolist(), all_triangles=True)


def _boundary_verts(T, n):
    E = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    u, c = np.unique(E, axis=0, return_counts=True)
    b = np.zeros(n, bool)
    b[u[c == 1].ravel()] = True
    return b


def _crossing_tris(Y, T, others, fixed):
    """Triangles of the piece (Y, T) through the other cloth ([(X, T)]), those with a sewn vertex left out (they sit
    on their partners)."""
    keep = ~fixed[np.asarray(T)].any(1)
    if not keep.any():
        return np.zeros(0, np.int64)
    idx = np.nonzero(keep)[0]
    mine = _bvh_mm(Y, np.asarray(T)[idx])
    hit = set()
    for Xo, To in others:
        if not len(To):
            continue
        for a, _b in mine.overlap(_bvh_mm(Xo, To)):
            hit.add(int(idx[a]))
    return np.array(sorted(hit), np.int64)


def _step_obstacles(others, bvh_factory=None):
    """Static cloth triangles for free-vertex path tests, in millimeters."""
    triangles = [np.asarray(X, float)[np.asarray(T, int)] for X, T in others if len(T)]
    if not triangles:
        return None
    q = np.concatenate(triangles)
    normal = np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0])
    norm = np.linalg.norm(normal, axis=1)
    good = norm > 0
    q, normal, norm = q[good], normal[good], norm[good]
    if not len(q):
        return None
    bvh = bvh_factory(q.reshape(-1, 3), np.arange(q.size // 3).reshape(-1, 3)) if bvh_factory else None
    # Conservative broad-phase padding for BVH/Vector float32 coordinates.
    bound = float(np.max(np.abs(q)))
    padding = 4 * float(np.spacing(np.float32(max(1.0, bound))))
    rounded = q.astype(np.float32)
    collapsed = np.flatnonzero(np.linalg.norm(np.cross(rounded[:, 1] - rounded[:, 0],
                                                       rounded[:, 2] - rounded[:, 0]), axis=1) == 0)
    return q, normal, norm, q.min(1), q.max(1), bvh, padding, collapsed


def _clip_vertex_steps(Y, step, obstacles):
    """Clip free-vertex S3 paths before static cloth, allowing initial contacts to separate.

    Tests every intersected face's segment parameter, using the original
    float64 arrays. Only a face already touching the start is skipped; a
    separate nearby layer still blocks the path. Does not resolve existing
    intersections, reverse vertex/face, edge/edge, or coplanar motion.
    """
    if obstacles is None:
        return step
    q, normals, norms, lo, hi, bvh, padding, collapsed = obstacles
    out = step.copy()
    for k in np.flatnonzero(np.linalg.norm(step, axis=1) > 0):
        p, move = Y[k], step[k]
        end = p + move
        length = float(np.linalg.norm(move))
        scale = max(1.0, float(np.max(np.abs(p))), float(np.max(np.abs(end))))
        eps = 8 * np.finfo(float).eps * scale
        if bvh is None:
            ids = np.arange(len(q))
        else:
            midpoint = (p + end) * .5
            radius = length * .5 + padding + 4 * float(np.spacing(np.float32(max(1.0, scale))))
            ids = np.array(sorted({int(h[2]) for h in bvh.find_nearest_range(Vector(midpoint), radius)} |
                                  set(collapsed.tolist())), dtype=int)
        ids = ids[((lo[ids] <= np.maximum(p, end) + eps) &
                   (hi[ids] >= np.minimum(p, end) - eps)).all(1)]
        if not len(ids):
            continue
        tri, n, nl = q[ids], normals[ids], norms[ids]
        start = np.einsum('ij,ij->i', p - tri[:, 0], n)
        change = n @ move
        active = (np.abs(start) > eps * nl) & (np.abs(change) > eps * nl)
        t = np.divide(-start, change, out=np.full(len(ids), np.inf), where=active)
        valid = active & (t > 0) & (t <= 1)
        if not valid.any():
            continue
        tri, n, nl, t = tri[valid], n[valid], nl[valid], t[valid]
        hit = p + t[:, None] * move
        nn = nl ** 2
        a = np.einsum('ij,ij->i', np.cross(tri[:, 1] - hit, tri[:, 2] - hit), n) / nn
        b = np.einsum('ij,ij->i', np.cross(tri[:, 2] - hit, tri[:, 0] - hit), n) / nn
        c = 1 - a - b
        longest = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
        bary_eps = eps * longest / nl
        inside = np.minimum(np.minimum(a, b), c) >= -bary_eps
        if inside.any():
            # The numerical surface margin matches Outside's 1e-4 mm tolerance.
            allowed = max(0.0, float(t[inside].min()) - 1e-4 / length)
            out[k] *= allowed
    return out


def settle(Y, V, T, fixed, body, partners, others):
    """Y (n, 3) world mm of the piece, V (n, 2) its pattern points, T its triangles, fixed (n,) its sewn points; body
    a BVH in metres (dress.body_bvh); partners [(X, T)] of the pieces it is sewn to; others [(X, T)] all the other
    cloth. Returns (Y, numbers)."""
    Y = np.asarray(Y, float).copy()
    V = np.asarray(V, float)
    T = np.asarray(T, np.int64)
    n = len(Y)
    E = _edges(T)
    L0 = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    free = ~np.asarray(fixed, bool)
    pb = [(_bvh_mm(X, Tp), _boundary_verts(np.asarray(Tp), len(X)), np.asarray(X, float)) for X, Tp in partners if len(Tp)]
    obstacles = _step_obstacles(others, _bvh_mm)
    # the side of each partner: where most of the piece's vertices near it are now
    sides = []
    for bv, _bd, _X in pb:
        s = 0.0
        for k in np.nonzero(free)[0]:
            loc, nrm, _i, d = bv.find_nearest(Vector(Y[k]))
            if loc is not None and d < NEAR:
                s += float(np.sign((Vector(Y[k]) - loc).dot(nrm)))
        sides.append(1.0 if s >= 0 else -1.0)
    moved_body = moved_side = 0
    deep = np.zeros(n, bool)
    for _it in range(ITERS):
        # S1 out of the body
        for k in np.nonzero(free)[0]:
            p = Vector(Y[k] * 0.001)
            loc, nrm, _i, d = body.find_nearest(p)
            if loc is None:
                continue
            s = (p - loc).dot(nrm) * 1000.0
            if s < GAP:
                if s < -CAP:
                    deep[k] = True
                    continue
                Y[k] = np.asarray(loc + nrm * (GAP * 0.001)) * 1000.0
                moved_body += 1
        # S2 to its side of each partner
        for (bv, bd, Xp), sg in zip(pb, sides):
            for k in np.nonzero(free)[0]:
                loc, nrm, fi, d = bv.find_nearest(Vector(Y[k]))
                if loc is None or d > NEAR:
                    continue
                s = (Vector(Y[k]) - loc).dot(nrm) * sg
                if s >= GAP:
                    continue
                # at the partner's edge (its nearest point by a boundary vertex): outside its shadow, left
                q = np.asarray(loc)
                near_b = bd & (np.linalg.norm(Xp - q, axis=1) < 3.0)
                if near_b.any():
                    continue
                Y[k] = np.asarray(loc + nrm * (sg * GAP))
                moved_side += 1
        # S3 edge lengths back, the sewn points held
        for _j in range(3):
            d = Y[E[:, 1]] - Y[E[:, 0]]
            ln = np.maximum(np.linalg.norm(d, axis=1), 1e-9)
            tgt = np.clip(ln, L0 * (1 - LEN_BAND), L0 * (1 + LEN_BAND))
            c = ((ln - tgt) / ln)[:, None] * d * 0.5
            acc = np.zeros_like(Y)
            cnt = np.zeros(n)
            np.add.at(acc, E[:, 0], c)
            np.add.at(acc, E[:, 1], -c)
            np.add.at(cnt, E[:, 0], 1)
            np.add.at(cnt, E[:, 1], 1)
            step = acc / np.maximum(cnt, 1)[:, None]
            step[~free] = 0.0
            step = _clip_vertex_steps(Y, step, obstacles)
            Y += step
    num = measure(Y, V, T, fixed, body, others)
    num.update(pushed_body=moved_body, pushed_side=moved_side, deep=int(deep.sum()))
    return Y, num


def measure(Y, V, T, fixed, body, others):
    """{'inside', 'inside_v' (bool per vertex), 'cross', 'cross_t' (triangle indices), 'stretch' (p99)}."""
    Y = np.asarray(Y, float)
    ins = np.zeros(len(Y), bool)
    for k in range(len(Y)):
        p = Vector(Y[k] * 0.001)
        loc, nrm, _i, d = body.find_nearest(p)
        if loc is not None and (p - loc).dot(nrm) < 0:
            ins[k] = True
    ct = _crossing_tris(Y, T, others, np.asarray(fixed, bool))
    E = _edges(T)
    V = np.asarray(V, float)
    r = np.linalg.norm(Y[E[:, 0]] - Y[E[:, 1]], axis=1) / np.maximum(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1), 1e-9)
    return {'inside': int(ins.sum()), 'inside_v': ins, 'cross': int(len(ct)), 'cross_t': ct,
            'stretch': round(float(np.percentile(r, 99)), 3)}


def mark(ob, V, T, num):
    """What is left after settling, on the placed object: red in the Overlays' Problems (diagnose.mark), and its
    vertices in the group LEFT_GROUP."""
    from . import diagnose as DG
    left = np.asarray(num['inside_v'], bool).copy()
    if len(num['cross_t']):
        left[np.asarray(T)[num['cross_t']].ravel()] = True
    vg = ob.vertex_groups.get(LEFT_GROUP)
    if vg is not None:
        ob.vertex_groups.remove(vg)
    if left.any():
        n = len(ob.data.vertices)
        if n == len(left):
            on = left
        else:                             # an object made before the pattern was re-meshed: by its layout points
            from mathutils.kdtree import KDTree
            fl = ob.data.attributes.get(PLV.FLAT_ATTR)
            Fp = np.empty(n * 2)
            fl.data.foreach_get('vector', Fp)
            kd = KDTree(len(V))
            for k, q in enumerate(np.asarray(V, float)):
                kd.insert((q[0], q[1], 0.0), k)
            kd.balance()
            on = np.array([left[kd.find((q[0], q[1], 0.0))[1]] for q in Fp.reshape(-1, 2)], bool)
        vg = ob.vertex_groups.new(name=LEFT_GROUP)
        vg.add(np.nonzero(on)[0].tolist(), 1.0, 'REPLACE')
        DG.mark(ob, 'left', np.nonzero(on)[0], f"{num['inside']} inside the body, {num['cross']} through cloth")
    else:
        DG.clear(ob, 'left')
    return int(left.sum())


def context_of(context, spec, piece, got):
    """(i, V, T, fixed, partners, others) of `piece` in the shapes `got` (place_seam.shapes)."""
    pat, tris, X = got
    names = [str(n) for n in pat['names']]
    i = names.index(piece)
    st = np.asarray(pat['stitch'])
    fixed = np.zeros(len(X[i]), bool)
    pj = set()
    for r in st:
        if r[0] == i and r[2] != i:
            fixed[int(r[1])] = True
            pj.add(int(r[2]))
        elif r[2] == i and r[0] != i:
            fixed[int(r[3])] = True
            pj.add(int(r[0]))
    partners = [(X[j], tris[j]) for j in sorted(pj)]
    others = [(X[j], tris[j]) for j in range(len(names)) if j != i]
    return i, np.asarray(pat[f"V{i}"], float), np.asarray(tris[i]), fixed, partners, others


def settle_piece(context, spec, piece, Y, got, bvh=None):
    """Settle `piece` from shape Y against the shapes `got`. Returns (Y, numbers) or (None, why)."""
    from . import dress as D
    from . import placement as PL
    pat, tris, X = got
    if piece not in [str(n) for n in pat['names']]:
        return None, tx("{0} is not placed", piece)
    i, V, T, fixed, partners, others = context_of(context, spec, piece, got)
    if bvh is None:
        co, tri = PL.body_arrays(context, context.scene.ac9_cloth_sim.body)
        bvh = D.body_bvh(co, tri)
    before = measure(Y, V, T, fixed, bvh, others)
    Y2, num = settle(Y, V, T, fixed, bvh, partners, others)
    num['before'] = {'inside': before['inside'], 'cross': before['cross'], 'stretch': before['stretch']}
    return Y2, num


def report(piece, num, left):
    b = num['before']
    msg = tx("{0} settled: inside the body {1} -> {2}, through other cloth {3} -> {4}", piece,
             b['inside'], num['inside'], b['cross'], num['cross'])
    if left:
        msg += tx(" ({0} vertices left red: Move by Hand, or simulate from here)", left)
    return msg


class AC9_OT_SimSettle(bpy.types.Operator):
    """Tidy the selected placed pieces with no physics: out of the body, onto their side of the pieces they are sewn to, their edges back to length. What is left is marked red"""
    bl_idname = "ac9_cloth_sim.settle_placed"
    bl_label = "Settle"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or not spec.get('placement'):
            return False
        from . import dress as D
        from . import placement as PL
        if D.AC9_OT_SimDress.running():
            cls.poll_message_set("Not while a simulation runs")
            return False
        sel = PL.selected_pieces(context)
        if not sel:
            cls.poll_message_set("Select a piece first (on the pattern)")
            return False
        obs = PLV.objects(spec)
        if not any(n in obs for n in sel):
            cls.poll_message_set("Works on placed shapes: Place it, or Back to Placed on the garment")
            return False
        return True

    @PB.prepared_consumer
    def execute(self, context, *, preparation=None):
        from . import place_seam as PS
        from . import placement as PL
        spec, _ = store.get(context)
        obs = PLV.objects(spec)
        lead = {o[PLV.TWIN_PROP]: o[PLV.PROP] for o in obs.values() if o.get(PLV.TWIN_PROP)}
        names = list(dict.fromkeys(n if obs.get(n) is not None and obs[n].get(PLV.PROP) == n else lead.get(n)
                                   for n in PL.lead_only(spec, PL.selected_pieces(context)) if n in obs))
        names = [n for n in names if n]
        got, why = PS.shapes(context, spec, preparation=preparation)
        if got is None:
            self.report({'WARNING'}, why)
            return {'CANCELLED'}
        ok_any = False
        for n in names:
            ob = obs[n]
            if PLV.has_edits(ob):
                self.report({'WARNING'}, tx("{0}'s placed shape was edited vertex by vertex: Back to Guide first", n))
                continue
            pat, tris, X = got
            i = [str(m) for m in pat['names']].index(n)
            Y, num = settle_piece(context, spec, n, X[i], got)
            if Y is None:
                self.report({'WARNING'}, num)
                continue
            why = PS.write_shape(ob, pat[f"V{i}"], tris[i], Y)
            if why:
                self.report({'WARNING'}, why)
                continue
            X[i] = Y
            if not ob.get(PLV.SEAM_PROP):          # read by Simulate as a shape of its own (placed_view.is_moved)
                ob[PLV.SEAM_PROP] = json.dumps({'way': 'settled', 'partners': []})
            left = mark(ob, pat[f"V{i}"], tris[i], num)
            PLV.invalidate_shown()
            ok_any = True
            self.report({'INFO'}, report(n, num, left))
            PS.mark_on_next_run(spec, [n], preparation=preparation)
        return {'FINISHED'} if ok_any else {'CANCELLED'}


CLASSES = (AC9_OT_SimSettle,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
