# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry helpers and the public Diagnose operator.

The operator delegates to diagnosis.py: an explicit snapshot of displayed placed
and dressed surfaces, without preparing a garment. The older prepare-based run,
mark, and summary helpers remain callable for existing inspection scripts.
The shared intrusion rule matches the solver: depth >0.5 mm, reach 50 mm,
excluding open-boundary faces and the appropriate arm faces.
"""
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import prepare_build as PB
from . import placed_view as PLV
from . import store
from .i18n import tx
from . import diagnosis

# the names on screen, one place (the panel, the operator, the docs read these)
NAME = diagnosis.NAME
HEADING = "Check and Fix"
KIND = 'check'                  # diagnose.KINDS: its red in the Overlays' Problems
OLD_KINDS = ('placement', 'hand', 'left')   # older red on the same pieces, dropped when they are diagnosed
INSIDE_MM = 0.5                 # solver/start_check: inside deeper than this
REACH_MM = 50.0                 # newton_vbd _sc_inside: mesh_query_point_sign_normal(m, p, 0.05)

# what the last press found, per garment: {'line': (English, args), 'stamps': {object name: stamp}, 'n': pieces}
_LAST = {}
_BODY = {'key': None, 'val': None}


def _stamp(ob):
    try:
        return (tuple(round(v, 6) for row in ob.matrix_world for v in row), len(ob.data.vertices))
    except (ReferenceError, AttributeError):
        return None


def targets(context, spec):
    """The placed pieces to look at: the selected ones (with their pairs), else all; not those on the dressed
    garment."""
    from . import placement as PL
    from . import place_seam as PS
    obs = PLV.objects(spec)
    mine = [n for n in obs if n not in set(PS.on_garment(spec, list(obs)))]
    sel = PL.with_pairs(spec, PL.selected_pieces(context)) & set(mine)
    return sorted(sel or mine)


def body(context, spec):
    """(BVH in metres, faces not judged per piece: skip(name) -> bool array or None): the body and the Collision
    objects, as the run's start check sees them. Kept while their arrays are the same."""
    from . import dress as D
    from . import placement as PL
    from .core import recipe
    from .solver import arm_mask
    props = context.scene.ac9_cloth_sim
    co, tri = PL.body_arrays(context, props.body)
    co_b, tri_b = co, tri
    _cols, col_arr = D.collider_arrays(context, exclude=D.garment(spec))
    for c2, t2 in col_arr:
        tri = np.vstack([tri, t2 + len(co)])
        co = np.vstack([co, c2])
    if not len(tri) or not np.isfinite(co).all():
        return None, lambda n: None
    key = (co.shape, tri.shape, hash(co.tobytes()), hash(tri.tobytes()), str(recipe.drop_arms(spec)))
    if _BODY['key'] != key:
        bvh = BVHTree.FromPolygons((co * 0.001).tolist(), tri.tolist(), all_triangles=True)
        E = np.sort(np.r_[tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]], 1)
        u, c = np.unique(E, axis=0, return_counts=True)
        bv = np.zeros(len(co), bool)
        bv[u[c == 1].ravel()] = True
        openf = bv[tri].any(1)
        arm = np.zeros(len(tri), bool)
        if len(tri_b) and spec.get('guides', {}).get('torso'):     # as prepare finds the arms
            body_only = D._body_kept(co_b, tri_b)
            arm[:len(body_only.T)] = arm_mask.arm_tris(co_b[body_only.T].mean(1), recipe.drop_arms(spec))
        _BODY['key'], _BODY['val'] = key, (bvh, openf, openf | arm)
    bvh, openf, open_arm = _BODY['val']
    pl = spec.get('placement', {})

    def skip(name):
        return open_arm if D.arm_skip(pl.get(name), open_arm) is not None else openf
    return bvh, skip


def inside(bvh, X_mm, skip):
    """bool per vertex: inside the body as the run's start check counts it."""
    out = np.zeros(len(X_mm), bool)
    if bvh is None:
        return out
    for k, p in enumerate(np.asarray(X_mm, float) * 0.001):
        q = Vector(p)
        loc, nrm, fi, _d = bvh.find_nearest(q, REACH_MM * 0.001)
        if loc is None or (skip is not None and skip[fi]):
            continue
        if (q - loc).dot(nrm) < -INSIDE_MM * 0.001:
            out[k] = True
    return out


def _bvh(X, T):
    T = np.asarray(T)
    if not len(T):
        return None
    return BVHTree.FromPolygons(np.asarray(X, float).tolist(), T.tolist(), all_triangles=True)


def run(context, spec, names, *, preparation=None):
    """{piece: {'inside': bool per vertex, 'cross_t': triangle indices, 'stretch': largest edge ratio}} of `names`, the
    shapes `got` and the time taken; or (None, why)."""
    from . import place_seam as PS
    from . import place_settle as ST
    t0 = time.perf_counter()
    got, why = PS.shapes(context, spec, preparation=preparation)
    if got is None:
        return None, why
    pat, tris, X = got
    pnames = [str(n) for n in pat['names']]
    bvh, skip = body(context, spec)
    trees = {}                                  # one BVH per piece, used against every piece it is looked at with
    out = {}
    for n in names:
        if n not in pnames:
            continue
        i, V, T, fixed, _partners, _others = ST.context_of(context, spec, n, got)
        Y = np.asarray(X[i], float)
        ins = inside(bvh, Y, skip(n))
        if i not in trees:
            trees[i] = _bvh(Y, T)
        keep = ~fixed[T].any(1)                 # a triangle with a sewn vertex sits on its partner (place_settle)
        hit = set()
        if trees[i] is not None:
            for j in range(len(pnames)):
                if j == i:
                    continue
                if j not in trees:
                    trees[j] = _bvh(X[j], tris[j])
                if trees[j] is None:
                    continue
                hit.update(a for a, _b in trees[i].overlap(trees[j]) if keep[a])
        E = ST._edges(T)
        r = (np.linalg.norm(Y[E[:, 0]] - Y[E[:, 1]], axis=1)
             / np.maximum(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1), 1e-9))
        out[n] = {'i': i, 'V': V, 'T': T, 'Y': Y, 'inside': ins, 'cross_t': np.array(sorted(hit), np.int64),
                  'stretch': float(r.max()) if len(r) else 1.0}
    return (out, time.perf_counter() - t0), None


SPOT_MAX = 8                    # spots per piece: the largest kept


def spots(n, f):
    """Where on `n` the found vertices are, one spot per patch (vertices joined by an edge of the piece's mesh):
    [{'piece', 'c': the patch's vertex nearest its mean (the piece mesh's index: on the cloth, the same vertex in 3D
    and on the pattern), 'f': its layout point (m), 'in': vertices inside the body, 'x': triangles through cloth}]
    (user 2026-11-23: the red was said, not where -- inside the body it is under the body, so the red is not seen).
    mark() turns 'c' into the object's vertex."""
    from . import place_settle as ST
    T = np.asarray(f['T'], np.int64)
    ins = np.asarray(f['inside'], bool)
    xv = np.zeros(len(ins), bool)
    xt = np.asarray(f['cross_t'], np.int64)
    if len(xt):
        xv[T[xt].ravel()] = True
    bad = ins | xv
    if not bad.any():
        return []
    root = np.arange(len(bad))

    def find(a):
        while root[a] != a:
            root[a] = root[root[a]]
            a = root[a]
        return a
    for a, b in ST._edges(T):
        if bad[a] and bad[b]:
            ra, rb = find(a), find(b)
            if ra != rb:
                root[ra] = rb
    groups = {}
    for v in np.flatnonzero(bad):
        groups.setdefault(find(v), []).append(v)
    Y, V = np.asarray(f['Y'], float), np.asarray(f['V'], float)
    out = []
    for vs in sorted(groups.values(), key=len, reverse=True)[:SPOT_MAX]:
        vs = np.asarray(vs)
        c = vs[np.argmin(np.linalg.norm(Y[vs] - Y[vs].mean(0), axis=1))]
        k_x = int(np.isin(T[xt], vs).any(1).sum()) if len(xt) else 0
        out.append({'piece': n, 'c': int(c),
                    'f': [round(float(q) * 0.001, 6) for q in V[c][:2]],
                    'in': int(ins[vs].sum()), 'x': k_x})
    return out


def mark(spec, found):
    """The red on the placed objects: each object's older red of OLD_KINDS and KIND dropped, then what was found (a
    pair's two halves on their one object)."""
    from . import diagnose as DG
    from . import place_settle as ST
    from .core import spec as S
    from mathutils.kdtree import KDTree
    obs = PLV.objects(spec)
    per_ob = {}
    for n, f in found.items():
        ob = obs.get(n)
        if ob is None:
            continue
        bad = np.asarray(f['inside'], bool).copy()
        if len(f['cross_t']):
            bad[np.asarray(f['T'])[f['cross_t']].ravel()] = True
        lead = ob.get(PLV.PROP)
        sp = spots(n, f)
        if n == lead:
            on = ST.object_verts(ob, f['V'], bad)
            for s in sp:
                one = np.zeros(len(bad), bool)
                one[s.pop('c')] = True
                s['v'], s['half'] = int(np.flatnonzero(ST.object_verts(ob, f['V'], one))[0]), 0
        elif lead in found and bad.any():
            # the mirrored half of a pair: the object holds the lead's mesh only (the Mirror modifier shows this
            # half), and this half's mesh and layout are its own. Its red points mirrored back onto the lead's shape,
            # the nearest of the lead's vertices taken (through the layout points this marked 892 of 1379, 2026-11-23)
            pl_ = S.pair_plane(spec, n)
            Yl = found[lead]['Y']
            kd = KDTree(len(Yl))
            for k, q in enumerate(Yl):
                kd.insert(q.tolist(), k)
            kd.balance()

            def back(idx):
                P = S.mirror_points(np.asarray(f['Y'])[idx], pl_, S.mirror_center(spec, pl_, n))
                fl = np.zeros(len(Yl), bool)
                fl[[kd.find(q.tolist())[1] for q in P]] = True
                return ST.object_verts(ob, found[lead]['V'], fl)
            on = back(bad)
            for s in sp:
                # the Mirror's copy: the evaluated mesh holds the object's vertices, then their mirrored copy
                s['v'], s['half'] = int(np.flatnonzero(back(np.array([s.pop('c')])))[0]), 1
        else:
            on = np.zeros(len(ob.data.vertices), bool)
            sp = []
        acc = per_ob.setdefault(ob.name, [ob, np.zeros(len(on), bool), 0, 0, []])
        if len(acc[1]) == len(on):
            acc[1] |= on
        acc[2] += int(np.count_nonzero(f['inside']))
        acc[3] += int(len(f['cross_t']))
        acc[4] += sp
    for ob, on, k_in, k_x, sp in per_ob.values():
        DG.clear(ob, KIND, *OLD_KINDS)
        if on.any() or sp:
            DG.mark(ob, KIND, np.nonzero(on)[0], f"{k_in} inside the body, {k_x} through cloth",
                    extra={'spots': sp, 'n_in': k_in, 'n_x': k_x})


def summary(found):
    """(English line, args) for the panel."""
    k_in = sum(int(np.count_nonzero(f['inside'])) for f in found.values())
    p_in = sum(1 for f in found.values() if np.count_nonzero(f['inside']))
    k_x = sum(int(len(f['cross_t'])) for f in found.values())
    st = max((f['stretch'] for f in found.values()), default=1.0)
    if not k_in and not k_x:
        return "No problems ({0} pieces); stretch up to x{1}", (len(found), f"{st:.2f}")
    return "Inside the body {0} ({1} pieces), through cloth {2}; stretch up to x{3}", (k_in, p_in, k_x, f"{st:.2f}")


def piece_lines(found, most=4):
    rows = [(n, int(np.count_nonzero(f['inside'])), int(len(f['cross_t']))) for n, f in found.items()]
    rows = sorted([r for r in rows if r[1] or r[2]], key=lambda r: -(r[1] + r[2]))
    out = [tx("{0}: inside the body {1}, through cloth {2}", *r) for r in rows[:most]]
    if len(rows) > most:
        out.append(tx("and {0} more pieces", len(rows) - most))
    return out


def remember(spec, found, line):
    obs = PLV.objects(spec)
    _LAST[PLV.garment(spec)] = {'line': line, 'n': len(found),
                                'stamps': {obs[n].name: _stamp(obs[n]) for n in found if n in obs}}


def forget(spec, names=None):
    """What Diagnose found on `names` (all when None) is no longer true: their red goes, the panel's line says to
    press it again (a run, Place, Back to Placed Shape, Back to Flat)."""
    from . import diagnose as DG
    if spec is None:
        return
    obs = PLV.objects(spec)
    for n in (obs if names is None else [n for n in names if n in obs]):
        try:
            DG.clear(obs[n], KIND)
        except ReferenceError:
            pass
    rec = _LAST.get(PLV.garment(spec))
    if rec is not None:
        if names is None:
            _LAST.pop(PLV.garment(spec), None)
        else:
            for n in names:
                if n in obs:
                    rec['stamps'][obs[n].name] = None


def line(spec):
    """(English line, args, stale) of the last Diagnose of this garment, or None. Stale: a piece it looked at moved
    or was made again since (the objects' matrices; read on every redraw, a few per piece)."""
    rec = _LAST.get(PLV.garment(spec))
    if rec is None:
        return None
    for name, st in rec['stamps'].items():
        ob = bpy.data.objects.get(name)
        if ob is None or st is None or _stamp(ob) != st:
            return rec['line'][0], rec['line'][1], True
    return rec['line'][0], rec['line'][1], False


class AC9_OT_SimDiagnosePlaced(bpy.types.Operator):
    """Check the displayed placed and dressed pieces for body intrusion, cloth crossing, stretch and start-shape warnings. Nothing moves"""
    bl_idname = "ac9_cloth_sim.diagnose_placed"
    bl_label = NAME
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # light: no shape read (the panel draws it on every redraw)
        spec = store.get(context)[0]
        if spec is None:
            return False
        from . import dress as D
        if D.AC9_OT_SimDress.running():
            cls.poll_message_set(tx("Not while a simulation runs"))
            return False
        if context.mode != 'OBJECT':
            cls.poll_message_set(tx("Finish editing before Diagnose"))
            return False
        return True

    def execute(self, context, *, preparation=None):
        spec, _ = store.get(context)
        try:
            rec = diagnosis.run(context, spec)
        except (ValueError, RuntimeError, KeyError, IndexError) as exc:
            old = diagnosis.record(context)
            if old is not None:
                old['stale'] = True
            self.report({'WARNING'}, tx("Diagnosis could not finish: {0}", str(exc)))
            return {'CANCELLED'}
        context.scene.ac9_cloth_sim.diagnosis_display = 'PROBLEMS'
        self.report({'INFO'}, diagnosis.notice(rec))
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        return {'FINISHED'}


CLASSES = (AC9_OT_SimDiagnosePlaced,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    diagnosis.register()


def unregister():
    diagnosis.unregister()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
