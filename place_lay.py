# SPDX-License-Identifier: GPL-3.0-or-later
"""Band on Edge: lay a narrow band (a T-shirt's neck rib) on the edge it is sewn to before simulating, its width
running on from there along the body, with no physics (user 2026-10-22, DECISIONS
cloth-sim-place-tools-fold-lay-attach-2026-10-22; Fable docs/reviews/REVIEW_fable_place_tools_r4_2026-10-22.md).
Simulate's lay (core/lay.py, solver/lay.py) does the same after the body is sewn; this is for Live, which sews
everything at once from the start: from the level ring round the neck guide the rib fell over the bodice (Live
3600 frames: 25 of 87 far points below -45 deg, 25 edges through other cloth, 125 vertices in the body), laid first
it ended as Simulate's lay does (0 / 0 / 0, docs/testing/RESULT_place_tools_stage0_2026-10-22.md).

The partners' edge is taken as placed now (hand moves, folds, Part to Seam in); placed apart where they are not
sewn yet (the front and back necklines at the shoulders) the band runs straight over the gaps, and its length is
compared without them (solver/lay.py lay_piece bridge). The solver's code runs in the solver's Python (Warp;
solver/lay_once.py), with its check: laid, the band must have no more vertices in the body and edges through other
cloth than placed, else it stays. A laid band's vertices and their made-at positions are rewritten and it gets
LAID_PROP: Simulate takes it as the user's shape but still lays it again on the sewn edge (dress.py: not 'hand'),
Fold and Part to Seam leave it alone, Back to Guide drops it.

Not laid: a piece with a fold of 90 degrees or more (a folded band, laid as one layer only), a piece not placed."""
import json
import os
import subprocess
import sys
import tempfile

import bpy
import numpy as np

from . import placed_view as PLV
from . import units as U
from . import store
from .i18n import tx

LAID_PROP = PLV.LAID_PROP
LAY_ONCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solver', 'lay_once.py')


from . import prepare_build as PB


def _object(spec, piece):
    ob = PLV.objects(spec).get(piece)
    return ob if ob is not None and ob.get(PLV.PROP) == piece else None


def _run(context, arrays):
    """solver/lay_once.py on arrays; returns (row dict, X or None) or raises RuntimeError."""
    from . import dress as D
    py = D.newton_python(context)
    if py is None:
        raise RuntimeError(tx("No solver Python: set it in the add-on's preferences"))
    env = dict(os.environ)
    if os.path.normcase(py) == os.path.normcase(sys.executable):
        env['PYTHONPATH'] = os.pathsep.join((D.solver_path() or []) + [x for x in [env.get('PYTHONPATH')] if x])
    with tempfile.TemporaryDirectory(prefix='ac9_lay_') as d:
        src, dst = os.path.join(d, 'in.npz'), os.path.join(d, 'out.npz')
        np.savez(src, **arrays)
        p = subprocess.run([py, LAY_ONCE, src, dst], capture_output=True, text=True, env=env, timeout=120)
        if p.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError(tx("Band on Edge failed: {0}", (p.stderr or p.stdout).strip().splitlines()[-1:]))
        with np.load(dst) as z:          # closed before the folder goes (Windows keeps an open file)
            return json.loads(str(z['row'])), (np.array(z['X']) if 'X' in z else None)


def band_why(spec, piece):
    """Why `piece` is not a band to lay (None when it is): Simulate's lay's shape conditions (core/lay.py 2 and the
    length against the width of 3) without its width limit, the user picking the piece. A panel laid on one edge
    collapsed (the user's 455 x 311 mm rectangle sewn to three pieces, 10/22: 1243 triangles of no area, the solver
    stopped on them)."""
    from .core import lay as CL
    from .core import roles as RO
    try:
        f = RO.facts(spec).get(piece)
    except Exception:
        f = None
    if f is None or not f['sewn']:
        return tx("{0} is not sewn to another piece", piece)
    long_x = f['w'] >= f['h']
    short, long_ = min(f['w'], f['h']), max(f['w'], f['h'])
    if long_ < 4.0 * short:
        return tx("{0} is not a narrow band ({1} x {2}): Band on Edge lays bands (a rib); "
                  "Part to Seam carries a part", piece, U.fmt(bpy.context, long_), U.fmt(bpy.context, short))
    sides = [P for y_ in f['sewn'] for P in f['sides'].get(y_, [])]
    c = 1 if long_x else 0
    if sides and (max(float(np.max(np.asarray(P)[:, c])) for P in sides)
                  - min(float(np.min(np.asarray(P)[:, c])) for P in sides)) > CL.EDGE_FRAC * short:
        return tx("{0} is sewn on more than one long side: Band on Edge lays a band sewn along one edge", piece)
    return None


def _squashed(V, T, Y, lo=0.1):
    """Triangles whose area laid is under lo times their area on the pattern: the solver drops them and stops (the
    user's rectangle 10/22). Stretched ones are left: the T-shirt rib's 22 over the 4 bridged shoulder gaps (375 mm
    edges) close as the shoulders are sewn; its least laid ratio is 0.305 (10/22)."""
    T = np.asarray(T)
    a3 = 0.5 * np.linalg.norm(np.cross(Y[T[:, 1]] - Y[T[:, 0]], Y[T[:, 2]] - Y[T[:, 0]]), axis=1)
    d1, d2 = V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]
    a2 = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    return int((a3 < lo * a2).sum())


@PB.prepared_consumer
def lay(context, spec, piece, got=None, targets=None, *, preparation=None):
    """Band on Edge on one piece. Returns (ok, message)."""
    from . import place_seam as PS
    from . import placement as PL
    ob = _object(spec, piece)
    if ob is None:
        return PB.refuse(preparation, tx("{0} has no placed shape: Place it first", piece))
    if PLV.has_edits(ob):
        return PB.refuse(preparation, tx("{0}'s placed shape was edited vertex by vertex: Back to Guide first", piece))
    if any(f.get('piece') == piece and abs(float(f.get('deg') or 0)) >= 90.0 for f in spec.get('folds', [])):
        return PB.refuse(preparation, tx("{0} has a fold of 90 degrees or more: Band on Edge lays one layer", piece))
    from .core import place_targets as TS
    why = band_why(TS.filter_spec(spec, piece, targets) if targets is not None else spec, piece)
    if why:
        return PB.refuse(preparation, why)
    if got is None:
        got, why = PS.shapes(context, spec, preparation=preparation)
        if got is None:
            return PB.refuse(preparation, why)
    pat, tris, X = TS.filter_stitches(got, piece, targets)
    names = [str(n) for n in pat['names']]
    if piece not in names:
        return PB.refuse(preparation, tx("{0} is not placed", piece))
    off = np.cumsum([0] + [len(x) for x in X])
    st = np.asarray(pat['stitch'])
    co, tri = PL.body_arrays(context, context.scene.ac9_cloth_sim.body)
    arrays = dict(X=np.vstack(X), flat=np.vstack([np.asarray(pat[f"V{i}"], float) for i in range(len(names))]),
                  vp=np.concatenate([np.full(len(x), i) for i, x in enumerate(X)]),
                  E=np.c_[off[st[:, 0]] + st[:, 1], off[st[:, 2]] + st[:, 3]].astype(np.int64),
                  F=np.vstack([np.asarray(tris[i]) + off[i] for i in range(len(names))]).astype(np.int64),
                  fp=np.concatenate([np.full(len(tris[i]), i) for i in range(len(names))]),
                  B=np.asarray(co, float), T=np.asarray(tri, np.int64), names=np.array(names), piece=np.array(piece))
    try:
        row, Xr = _run(context, arrays)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return PB.refuse(preparation, str(exc))
    if Xr is None:
        why = row.get('skip') or 'the laid shape is no better than the placed one'
        return PB.refuse(preparation, tx("{0} not laid: {1}", piece, why))
    k = names.index(piece)
    Vk = np.asarray(pat[f"V{k}"], float)
    bad = _squashed(Vk, tris[k], Xr)
    if bad:
        return PB.refuse(preparation, tx("{0} not laid: {1} triangles would be squashed flat", piece, bad))
    # the shape before it, as Part to Seam keeps it: Back to Placed puts this back (user 2026-11-01: the two the same,
    # back to the shape on the guide before the press)
    PLV.save_before_seam(ob, spec)
    why = PS.write_shape(ob, Vk, tris[k], Xr)
    if why:
        return PB.refuse(preparation, why)
    ob[LAID_PROP] = json.dumps({'partners': [names[j] for j in row.get('partners', []) if j < len(names)],
                                'bridged_mm': row.get('bridged_mm', [])})
    PLV.invalidate_shown()
    pn = ", ".join(names[j] for j in row.get('partners', []) if j < len(names))
    msg = tx("{0} laid on its edge to {1}", piece, pn)
    if row.get('bridged_mm'):
        msg += tx(" (over {0} gaps where they are not sewn yet)", len(row['bridged_mm']))
    # kept even when it is no better than the placed one, what is left red (user 2026-11-02)
    from . import place_settle as PT
    from . import dress as D
    others = [(X[j], tris[j]) for j in range(len(names)) if j != k]
    sewn = np.zeros(len(Xr), bool)       # triangles on the seam sit on their partners (solver/lay.py _crossings)
    sewn[st[(st[:, 0] == k) & (st[:, 2] != k), 1].astype(int)] = True
    sewn[st[(st[:, 2] == k) & (st[:, 0] != k), 3].astype(int)] = True
    num = PT.measure(Xr, Vk, tris[k], sewn, D.body_bvh(co, tri), others)
    left = PT.mark(ob, Vk, tris[k], num)
    if num['inside'] or num['cross']:
        msg += tx("; inside the body {0}, through other cloth {1}", num['inside'], num['cross'])
        if left:
            msg += tx(" ({0} vertices left red: Move by Hand, or simulate from here)", left)
    if row.get('ratio_off'):
        msg += tx("; its sewn edge is {0:.2f} times its length", row['ratio'])
    return True, msg


class AC9_OT_SimLayOnEdge(bpy.types.Operator):
    """Choose target pieces, then lay the selected narrow bands on their sewn edges, extending the bands' width. Shift-click adds targets, A chooses all, Enter applies, Esc cancels. Other seams stay enabled for simulation"""
    bl_idname = "ac9_cloth_sim.lay_on_edge"
    bl_label = "Band on Edge"
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
        from . import place_seam as PS
        # the selected piece's own state only, not its partners' (user 2026-10-24)
        worn = PS.on_garment(spec, sel)
        if worn and len(worn) == len(sel):
            cls.poll_message_set(tx("{0} is part of the dressed garment: Back to Placed it first (Placement)",
                                    ", ".join(worn[:3])))
            return False
        # not placed: placed by the pieces it is sewn to when they are (user 2026-10-22)
        if not any(n in spec['placement'] or PS.sewn_to_placed(spec, n) for n in sel):
            cls.poll_message_set("The selected piece is not placed: Place it first")
            return False
        return True

    @PB.prepared_consumer
    def execute(self, context, *, preparation=None):
        from . import placement as PL
        from . import place_seam as PS
        spec, _ = store.get(context)
        from . import place_targets
        try:
            requested, targets = place_targets.arguments(self, context)
            place_targets.validate(context, requested, targets)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        names = [n for n in PL.lead_only(spec, requested)
                 if n in spec['placement'] or PS.sewn_to_placed(spec, n)]
        worn = PS.on_garment(spec, names)
        if worn:
            self.report({'WARNING'}, tx("{0} is part of the dressed garment: Back to Placed it first (Placement)",
                                        ", ".join(worn)))
            names = [n for n in names if n not in worn]
        lines0 = []
        for n in [n for n in names if n not in spec['placement']]:
            with preparation.attempt() as child:
                sp, why = PS.place_by_partners(context, spec, n, targets=targets, preparation=preparation)
                if sp is not None:child.accept()
            if sp is None:
                names.remove(n)
                self.report({'WARNING'}, why)
                continue
            spec = sp
            lines0.append(tx("{0} placed by the pieces it is sewn to", n))
        for m in lines0:
            self.report({'INFO'}, m)
        spec = PS.objects_for(context, spec, names, preparation=preparation)
        lead = {o[PLV.TWIN_PROP]: o[PLV.PROP] for o in PLV.objects(spec).values() if o.get(PLV.TWIN_PROP)}
        names = list(dict.fromkeys(n if _object(spec, n) is not None else lead.get(n, n) for n in names))
        ok_any = False
        for n in names:
            with preparation.attempt() as child:
                ok, msg = lay(context, spec, n, got=PS.fresh_shapes(context) if len(names) == 1 and lines0 else None,
                              targets=targets, preparation=preparation)
                if ok:
                    PS.mark_on_next_run(store.get(context)[0], [n], preparation=preparation)
                    child.accept()
            spec = store.get(context)[0]
            ok_any |= ok
            self.report({'INFO'} if ok else {'WARNING'}, msg)
        return {'FINISHED'} if ok_any else {'CANCELLED'}


CLASSES = (AC9_OT_SimLayOnEdge,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
