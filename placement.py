# SPDX-License-Identifier: GPL-3.0-or-later
"""Placement on the avatar: guides from the body, and Place (select pieces, then click a spot on a guide).

spec['guides'] = {name: {o, R, r, h0, h1}} (mm, world frame of the body object).
spec['placement'][piece] = {'guide', 'theta' (deg), 'h' (mm), 'anchor' [x, y] layout mm, 'tilt' (deg, optional)}:
the piece's anchor point (its bounding-box center) sits at (theta, h) on the guide; the piece is laid round the guide
with its +x counter-clockwise (the viewer's right, seen from outside) and its +y along the guide's axis, then tilted
'tilt' degrees about the line round the guide through the anchor (90: its top laid flat out, 180: upside down).
A band (laid as a ring of its own length) may have 'ring' (optional): its ring as a share of that length's, set by
hand (none: Auto, pushed out until clear of the body where it is seen).
The numbers make the start: a tool or an AI can write them directly. Move by Hand then puts the start shape in
the scene as objects (placed_view.py) to move, rotate or edit freely; Dress starts a moved piece from its object.
"""
import json
from .i18n import CTX, rpt, tx

import bpy
import gpu
from bpy.props import FloatVectorProperty
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_origin_3d, region_2d_to_vector_3d
from mathutils import Matrix, Vector
from gpu_extras.batch import batch_for_shader

from . import overlay
from . import pattern_view as PV
from . import placed_view as PLV
from . import store
from . import toolbar as TB
from .core import body_axes as BA
from .core import spec as S
from . import selection as SEL

MM = 0.001
GAP = 10.0        # mm between pieces placed together
CENTER_PX = 12    # on the torso or neck guide, the center line (front / back) this close on screen takes the click


def selected_pieces(context):
    """Piece names of the selected objects: flat pattern pieces and placed objects (a pair's object = both)."""
    return SEL.pieces(context)


def lead_only(spec, names, selected_first=False):
    """One piece of each pair (its partner follows as the mirror image): moving both opens and closes them."""
    out = list(names)
    for a, b in S.placement_pairs(spec):
        if a in out and b in out:
            out.remove((a if out.index(b) < out.index(a) else b) if selected_first else b)
    return out


def with_twins(spec, pls):
    """The placements pls {piece: placement} and the pairs that follow them, for a preview."""
    sp = dict(spec, placement=dict(spec.get('placement', {}), **pls))
    full, _ = S.mirror_twins(sp, sources=set(pls))
    out = dict(pls)
    for a, b in S.placement_pairs(spec):
        for x, y in ((a, b), (b, a)):
            if x in pls and y not in pls and y in full:
                out[y] = full[y]
    return out


def with_pairs(spec, names):
    out = set(names)
    for a, b in S.placement_pairs(spec):
        if a in out or b in out:
            out |= {a, b}
    return out


def body_arrays(context, ob):
    """World-space vertices (mm) and triangles of the evaluated body object."""
    dg = context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        n = len(me.vertices)
        co = np.empty(n * 3, np.float64)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        M = np.array(ob.matrix_world)
        co = (np.c_[co, np.ones(n)] @ M.T)[:, :3] * 1000.0
        tri = np.empty(len(me.loop_triangles) * 3, np.int64)
        me.loop_triangles.foreach_get('vertices', tri)
        return co, tri.reshape(-1, 3)
    finally:
        ev.to_mesh_clear()


def norm_tilt(deg):
    """A Tilt in (-180, 180] (upside down reads 180)."""
    d = float(deg) % 360.0
    return d - 360.0 if d > 180.0 + 1e-9 else d


def with_tilt(p, deg):
    """Placement p tilted deg (AC9 Piece's Tilt); none stored for 0 (a 'spin' or 'flip' of the first builds dropped)."""
    p = {k: v for k, v in p.items() if k not in ('tilt', 'flip', 'spin')}
    d = round(norm_tilt(deg), 3)
    if d:
        p['tilt'] = d
    return p


def piece_anchor(outline):
    B = outline.B
    return 0.5 * (B.min(0) + B.max(0))


def sync(context):
    """The placed objects' moves written to the spec (placed_view.sync_pose) at an operator's start: the spec."""
    spec, _ = store.get(context)
    sp = PLV.sync_pose(context, spec)
    if sp is not None:
        store.put(context, sp)
        return sp
    return spec


def _remake_one(context, spec, name):
    """remake for one piece of a pair, its pair's object left as it is."""
    PLV.leave_edit(context)
    ob = PLV.objects(spec).get(name)
    if ob is None:
        return []
    edited = PLV.has_edits(ob)
    PLV.remove([name], spec)
    from . import dress as D
    try:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
    except ValueError as exc:
        return [tx("placed object not made again ({0}): Move by Hand", exc)]
    PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'])
    return [tx("edits in Edit Mode of {0} dropped", name)] if edited else []


def give_shape(context, spec, names):
    """Pieces placed after the garment was simulated (not in its result): their placed objects made when missing and
    sewn on from them by the next Phase Sim, as pieces Reset are (user 2026-10-25 b-2, DECISIONS
    cloth-sim-fold-face-mountain-valley-2026-10-25: the shape seen is the start; a pocket placed after the jacket
    was dressed had none, Fold, Move by Hand and Settle could not reach it). (spec as stored after, names given)."""
    from . import dress as D
    from . import place_seam as PS
    g = bpy.data.objects.get(D.garment_name(spec))
    if not D.has_result(g):
        return spec, []
    on_g = D.garment_pieces(g)
    st = {pc['name']: S.piece_state(pc) for pc in spec['pieces']}
    new = sorted(n for n in with_pairs(spec, names) if n not in on_g and n in spec.get('placement', {})
                 and st.get(n) in ('active', 'freeze'))
    if not new:
        return spec, []
    have = set(PLV.objects(spec))
    if set(new) - have:
        try:
            res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
        except ValueError:
            return spec, []
        PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'], only=set(new) - have)
    spec = store.get(context)[0]
    PS.mark_on_next_run(spec, new)
    return store.get(context)[0], new


def shape_kinds(spec, names):
    """{piece: what its placed object holds that placing it again does not make (b-1: said in one line)}: moved to
    its seams, laid on its edge, edited vertex by vertex. A fold is not among them: it is made again (refold_all)."""
    obs = PLV.objects(spec)
    out = {}
    for n in with_pairs(spec, names):
        ob = obs.get(n)
        if ob is None or ob.get(PLV.PROP) != n:
            continue
        k = ([tx("moved to its seams")] if ob.get(PLV.SEAM_PROP) else []) + \
            ([tx("laid on its edge")] if ob.get(PLV.LAID_PROP) else [])
        if k:
            out[n] = k
    return out


def _folded_only(ob):
    """The placed object holds folds and nothing else of the user's (no hand move, edit, seam or edge shape)."""
    return bool(ob.get(PLV.FOLD_PROP)) and not (PLV.from_sim(ob) or ob.get(PLV.SEAM_PROP) or ob.get(PLV.LAID_PROP)
                                                or PLV.has_edits(ob) or PLV.moved_since_made(ob))


def refold_all(context, names, prepared=None):
    """The folds in the spec of these pieces (and their pairs) made again on their placed objects just made again
    (user 2026-10-25 b-1: Place keeps a fold; the spec keeps its angle and side). Report lines for the lines that
    could not be folded."""
    from . import place_fold as PF
    spec = store.get(context)[0]
    obs = PLV.objects(spec)
    out = []
    for n in with_pairs(spec, names):
        ob = obs.get(n)
        if ob is None or ob.get(PLV.PROP) != n:
            continue
        for f in [f for f in spec.get('folds', []) if f.get('piece') == n and f.get('line') and f.get('deg')]:
            if prepared is not None and not spec.get('fold_rows'):
                r = PF.refold(context, n, f['line'], float(f['deg']), prepared=prepared)
            else:
                r = PF.refold(context, n, f['line'], float(f['deg']))
            if r is not None and not r[0]:
                out.append(tx("{0} {1} not folded again: {2}", n, f['line'], r[1]))
    return out


def _reset_on_garment(context, spec, names):
    """Pieces placed while the garment is dressed and not Reset yet (picked on the garment): Reset now and shown as
    placed objects where they were placed (user 2026-10-20 b-1: placing a piece of the dressed garment resets it, one
    step instead of two; Ctrl+Z takes it back). Report lines."""
    from . import dress as D
    g = bpy.data.objects.get(D.garment_name(spec))
    if not D.has_result(g):
        return []
    obs = PLV.objects(spec)
    on_g = D.garment_pieces(g)
    pick = with_pairs(spec, names)
    # a piece the garment does not have (placed after it) has nothing to Reset: shown as its placed shape at once,
    # the next Phase Sim sewing it on from there (user 2026-10-25 b-2)
    spec, new = give_shape(context, spec, pick)
    obs = PLV.objects(spec)
    out = [tx("{0}: placed shape, the next Phase Sim sews it on from there", ', '.join(new))] if new else []
    todo = sorted(n for n in pick if n not in obs and n in on_g)
    if not todo:
        return out
    try:
        done = D.reset_to_placed(context, g, spec, todo, context.scene.ac9_cloth_sim.mesh_size)
    except ValueError as exc:
        return out + [tx("not reset ({0})", exc)]
    return out + ([tx("reset {0} (the next Phase Sim sews it on from here)", ', '.join(done))] if done else [])


def remake(context, spec, names, prepared_out=None):
    """The placed objects of these pieces (and their pairs) made again from the placement in `spec` (already stored),
    each moved by its pose: what Place, Adjust and Mirror to Pair do to a piece shown as an object, so a hand move is
    kept (user 2026-10-14 b-2). Pieces without an object stay without. Report lines (edits in Edit Mode, which only
    the object held, are dropped and said so)."""
    PLV.leave_edit(context)
    obs = PLV.objects(spec)
    hit = {n for n in with_pairs(spec, names) if n in obs}
    if not hit:
        return []
    rep_ = []
    edited = sorted({obs[n][PLV.PROP] for n in hit if PLV.has_edits(obs[n])})
    PLV.remove(hit, spec)
    from . import dress as D
    try:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
    except ValueError as exc:
        return [tx("placed objects not made again ({0}): Move by Hand", exc)]
    if prepared_out is not None:
        prepared_out.append(res)
    g = bpy.data.objects.get(D.garment_name(spec))
    # on a dressed garment only the pieces Reset are shown as objects: the others stay on the garment
    PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'],
              only=hit if D.has_result(g) else None)
    D.sync_hidden(g, spec)
    if edited:
        rep_.append(tx("edits in Edit Mode dropped (moves kept): {0}", ', '.join(edited)))
    return rep_


def show_start(context, spec, names=()):
    """The placed objects made again as the sims start from them (user 2026-10-25, DECISIONS
    cloth-sim-place-shows-start-shape-2026-10-25: Place's arc and the start differed -- a band's ring, the push out of
    the body, the layers -- and a waistband was first seen as a ring after Move by Hand): every placed piece not moved
    by hand, and `names` (just placed, with their pairs) even when moved, folded or edited (as remake: a piece moved by
    hand and placed again stayed where it was, design review 2026-10-25). Not on a dressed garment (None: remake those
    placed then). Report lines: edits dropped, a band widened past its length to clear the body (red), a piece that
    starts through another it overlaps (red)."""
    from . import dress as D
    from . import place_settle as PT
    g = bpy.data.objects.get(D.garment_name(spec))
    if D.has_result(g):
        return None
    PLV.leave_edit(context)
    out = []
    obs = PLV.objects(spec)
    redo = {n for n in with_pairs(spec, names) if n in obs}
    edited = sorted({obs[n][PLV.PROP] for n in redo if PLV.has_edits(obs[n])})
    if redo:
        PLV.remove(redo, spec)
    try:
        res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
    except ValueError as exc:
        return [tx("placed objects not made ({0}): Move by Hand", exc)]
    notes = PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'])[2]
    if edited:
        out.append(tx("edits in Edit Mode dropped (moves kept): {0}", ', '.join(edited)))
    if notes:
        out.append(tx("moved to mirror their pair (placed apart before; Unlink Pair to move them apart): {0}", ', '.join(notes)))
    obs = PLV.objects(spec)
    from . import diagnose as DG
    for name, k in sorted(res.get('short_bands', {}).items()):
        if name in obs and not PLV.is_moved(obs[name]):
            DG.mark(obs[name], 'placement', None, f"stretched x{k} to clear the body")
        out.append(tx("{0} starts stretched x{1} to clear the body: it is shorter than the body here (red)", name, k))
    # a Ring Size set by hand is laid as it is (user 2026-11-02, DECISIONS cloth-sim-ring-size-2026-11-02): what is in
    # the body is red, where it is seen (its hand move in)
    for name, vs in sorted(res.get('in_body', {}).items()):
        if name in obs:
            DG.mark(obs[name], 'placement', vs, f"{len(vs)} vertices in the body")
        out.append(tx("{0}: {1} vertices start in the body at this Ring Size (red)", name, len(vs)))
    # a band's closure (user 2026-11-03, DECISIONS cloth-sim-closure-over-2026-11-02): with nothing saying which end
    # is on top it is laid as before, its ends apart, and they cross when sewn
    for name, (top, _by, sid) in sorted(res.get('closures', {}).items()):
        if top is None:
            out.append(tx("{0}: which end of its closure goes on top is not set (Over on seam {1}, Front Over on the "
                          "garment, or a buttonhole): its ends may cross when sewn", name, sid))
    # nothing moves a piece off another it overlaps (user 2026-10-25, DECISIONS cloth-sim-no-auto-placement-2026-10-25):
    # it starts through it or hidden under it, said and red; Move Outside moves the one selected off
    for name, o in sorted(res.get('overlaps', {}).items()):
        if name in obs and not PLV.is_moved(obs[name]):
            DG.mark(obs[name], 'placement', None, "overlaps " + ', '.join(o['through'] or o['inside']))
        if o['through']:
            out.append(tx("{0} overlaps {1}: it starts through it (red: select the one on top, Move Outside)",
                          name, ', '.join(o['through'])))
        if o['inside']:
            out.append(tx("{0} starts under {1} (red: if it goes on top, select it, Move Outside)",
                          name, ', '.join(o['inside'])))
    return out


class AC9_OT_SimDetectGuides(bpy.types.Operator):
    """Find the placement guides (torso, neck, arms, legs) from the body object. The avatar must stand upright, facing -Y"""
    bl_idname = "ac9_cloth_sim.detect_guides"
    bl_label = "Detect Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.scene.ac9_cloth_sim.body
        return ob is not None and ob.type == 'MESH' and store.get(context)[0] is not None

    def execute(self, context):
        ob = context.scene.ac9_cloth_sim.body
        co, tri = body_arrays(context, ob)
        try:
            gd, m = BA.guides(BA.Body(co, tri))
        except Exception as exc:
            self.report({'ERROR'}, rpt("Could not find the body's levels (upright, facing -Y?): {0}", exc))
            return {'CANCELLED'}
        spec = S.loads(S.dumps(sync(context)))
        spec['guides'] = BA.to_json(gd)
        spec['body'] = {'object': ob.name, 'z_crotch': m['z_crotch'], 'z_armpit': m['z_armpit'],
                        'z_neck': m['z_neck'], 'torso_half_width_armpit': m['torso_half_width_armpit']}
        store.put(context, spec)
        # the placed objects were made on the old guides: gone (their moves stay in the spec; Move by Hand makes them
        # again on the new guides, moved by them)
        PLV.leave_edit(context)
        old = sorted({ob.name for ob in PLV.objects(spec).values()})
        PLV.remove(list(PLV.objects(spec)), spec)
        PLV.forget(spec)                # shapes a Dress kept were made on the old guides too
        overlay.invalidate()
        self.report({'INFO'}, rpt("{0} guides: ", len(gd)) + ", ".join(gd)
                    + (rpt("; {0} placed objects removed (made on the old guides): Move by Hand makes them again, with their moves", len(old)) if old else ""))
        return {'FINISHED'}


class AC9_OT_SimPlace(bpy.types.Operator):
    """Place the selected pieces: press on a guide round the avatar, drag to slide them round and along it, release to keep. Several pieces go side by side"""
    bl_idname = "ac9_cloth_sim.place"
    bl_label = "Place"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # the 3D view's own region only: started from the sidebar its clicks were read in the sidebar's region and
        # never met a guide (the Placement panel's Place button switches to the Place tool, user 2026-10-15)
        spec = store.get(context)[0]
        return (context.area is not None and context.area.type == 'VIEW_3D' and context.region is not None
                and context.region.type == 'WINDOW' and spec is not None
                and spec.get('guides') and bool(selected_pieces(context)))

    def _hit(self, context, mx, my):
        """(guide, theta, h, t, center line name or None): the nearest guide wall under the cursor; on the torso or
        neck the center front / back holds it when near on screen. The guides' named points no longer take it (user
        2026-10-15: spread along the limbs by fixed fractions, not from the body)."""
        region, rv3d = context.region, context.region_data
        o = np.array(region_2d_to_origin_3d(region, rv3d, (mx, my))) * 1000.0
        d = np.array(region_2d_to_vector_3d(region, rv3d, (mx, my)))
        if self.rel_guide is not None:      # moving placed pieces: on their own guide
            h = BA.ray_hit({self.rel_guide: self.guides[self.rel_guide]}, o, d)
            if h is None:
                return None
            return self._slide_center(region, rv3d, h) or (*h, None)
        h = BA.ray_hit(self.guides, o, d)
        if h is None:
            return None
        if h[0] in ('torso', 'neck'):
            # the center line (front theta 90, back -90 in the guide's frame) holds the click when near it on screen
            g = self.guides[h[0]]
            for th_c, label in ((90.0, 'center front'), (-90.0, 'center back')):
                xy = location_3d_to_region_2d(region, rv3d, Vector(BA.point(g, th_c, h[2]) * MM))
                hx = location_3d_to_region_2d(region, rv3d, Vector(BA.point(g, h[1], h[2]) * MM))
                if xy is not None and hx is not None and (xy - hx).length <= CENTER_PX:
                    return (h[0], th_c, h[2], h[3], label)
        return (*h, None)

    def _slide_center(self, region, rv3d, h):
        """Sliding placed pieces on the torso or neck: the pressed piece held on the center front / back when it comes
        near it on screen, as a press on the guide is (user 2026-11-04). The hit returned moves it exactly there."""
        if not self.rel or self.press is None or h[0] not in ('torso', 'neck'):
            return None
        lead = self.drag_piece if self.drag_piece in self.rel else next(iter(self.rel))
        p = self.rel[lead]
        th = float(p['theta']) + h[1] - self.press[1]
        hh = float(p['h']) + h[2] - self.press[2]
        g = self.guides[h[0]]
        at = location_3d_to_region_2d(region, rv3d, Vector(BA.point(g, th, hh) * MM))
        if at is None:
            return None
        for th_c, label in ((90.0, 'center front'), (-90.0, 'center back')):
            th_c += 360.0 * round((th - th_c) / 360.0)          # the turn nearest where it is
            xy = location_3d_to_region_2d(region, rv3d, Vector(BA.point(g, th_c, hh) * MM))
            if xy is not None and (xy - at).length <= CENTER_PX:
                return (h[0], self.press[1] + th_c - float(p['theta']), h[2], h[3], label)
        return None

    def _placements(self, hit):
        """Per selected piece: its own (theta, h). Several pieces are put side by side round the guide in their
        left-to-right layout order, GAP apart, the row centered on the click, each piece's center at the click height.
        (Keeping the layout distances failed: a BOM's 2D layout is not arranged like the body, and pieces a meter
        apart in the layout went round an arm guide twice.)"""
        name, th0, h0 = hit[0], hit[1], hit[2]
        if self.rel is not None:
            # placed pieces pressed on: moved by how far the cursor went round and along the guide since the press,
            # not put under it (user 2026-11-01, DECISIONS cloth-sim-collar-flip-2026-11-01)
            dth, dh = th0 - self.press[1], h0 - self.press[2]
            return {pc: dict(p, theta=round(float(p['theta']) + dth, 3), h=round(float(p['h']) + dh, 3))
                    for pc, p in self.rel.items()}
        order = sorted(self.anchors, key=lambda pc: self.anchors[pc][0])
        widths = [float(np.ptp(self.outlines[pc].B[:, 0])) for pc in order]
        total = sum(widths) + GAP * (len(order) - 1)
        # round the radius they are wrapped at (wider than the guide when the pieces round an arm or leg do not fit
        # round it, overlay.wrap_radii), so the row is drawn and dressed side by side
        rad = overlay.wrap_radii(self.spec, self.outlines, {pc: {'guide': name} for pc in order})
        r = max(rad.get(pc, self.guides[name]['r']) for pc in order)
        x = -0.5 * total
        out = {}
        for pc, w in zip(order, widths):
            cx = x + 0.5 * w
            # placed again: a tilted piece stays tilted (its Tilt is the piece's, not the press's), and a Ring Size set
            # by hand stays (Auto is measured again where it is released)
            old = self.spec.get('placement', {}).get(pc, {})
            out[pc] = with_tilt({'guide': name, 'theta': round(th0 + np.degrees(cx / r), 3), 'h': round(h0, 3),
                                 'anchor': np.round(self.anchors[pc], 3).tolist(),
                                 **({'ring': old['ring']} if old.get('ring') else {})},
                                old.get('tilt', 0.0))
            x += w + GAP
        return out

    def _draw(self, context):
        try:
            sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            sh.bind()
            sh.uniform_float('viewportSize', (context.region.width, context.region.height))
            if self.hit is not None:
                sh.uniform_float('lineWidth', 3.0)
                sh.uniform_float('color', (1.0, 0.85, 0.2, 0.9))
                batch_for_shader(sh, 'LINES', {'pos': [tuple(p) for p in overlay.guide_lines(self.guides[self.hit[0]])]}).draw(sh)
                sh.uniform_float('lineWidth', 3.0)
                sh.uniform_float('color', (0.3, 1.0, 0.5, 1.0))
                if self.hit[4]:
                    sp = gpu.shader.from_builtin('UNIFORM_COLOR')
                    sp.bind()
                    sp.uniform_float('color', (1.0, 1.0, 1.0, 1.0))
                    gpu.state.point_size_set(16.0)
                    w = BA.point(self.guides[self.hit[0]], self.hit[1], self.hit[2]) * MM
                    batch_for_shader(sp, 'POINTS', {'pos': [tuple(w)]}).draw(sp)
                    gpu.state.point_size_set(1.0)
                    sh.bind()
                pls = with_twins(self.spec, self._placements(self.hit))
                rad = overlay.wrap_radii(self.spec, self.outlines, pls)
                for pc, pl in pls.items():
                    P = overlay.wrapped_outline(self.spec, self.outlines, pc, pl, radii=rad)
                    pts = [tuple(p) for u, v in zip(P[:-1], P[1:]) for p in (u, v)]
                    batch_for_shader(sh, 'LINES', {'pos': pts}).draw(sh)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def invoke(self, context, event):
        sync(context)
        from . import dress as DR
        DR.standby_wake(context)          # a run usually follows a Place: the solver starts while the pieces move
        self.spec, self.outlines = store.get(context)
        self.guides = self.spec['guides']
        selected = selected_pieces(context)
        kind, touched, face = TB.under(context, event.mouse_region_x, event.mouse_region_y)
        self.drag_piece = (PLV.placed_face_piece(context, touched, face)
                           if kind == 'PLACED' and touched.select_get() else None)
        if self.drag_piece in selected:
            # An already-selected shared object skipped select_object on this
            # press. Its touched face, not its stored source, owns this drag.
            selected = [self.drag_piece] + [n for n in selected if n != self.drag_piece]
            PLV.clear_control_hints()
        else:
            selected = PLV.hand_selection(context, selected)
        sel = lead_only(self.spec, selected, selected_first=True)
        self.anchors = {pc: piece_anchor(self.outlines[pc]) for pc in sel}
        # pressed on a placed piece: the selected pieces placed on its guide move from where they are (user
        # 2026-11-01); pressed elsewhere, the pieces go under the cursor as before
        pl = self.spec.get('placement', {})
        self.rel, self.rel_guide, self.press = None, None, None
        g0 = pl.get(self.drag_piece, {}).get('guide') if self.drag_piece else None
        if g0 in self.guides:
            self.rel_guide = g0
            self.press = self._hit(context, event.mouse_region_x, event.mouse_region_y)
            if self.press is not None:
                self.rel = {pc: pl[pc] for pc in sel if pc in pl and pl[pc]['guide'] == g0}
            else:
                self.rel_guide = None
        self.hit = self._hit(context, event.mouse_region_x, event.mouse_region_y)
        self.pressed = False
        self.mouse = (event.mouse_region_x, event.mouse_region_y)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_VIEW')
        self._status(context)
        return TB.start_modal(self, context, event)        # the Place tool's press is the first one

    def _status(self, context):
        n = len(self.rel) if self.rel is not None else len(self.anchors)
        if self.hit is None:
            txt = tx("Place {0} piece(s): move over a guide round the avatar", n)
        elif self.rel is not None:
            txt = tx("Move {0} placed piece(s) on {1} ({2:+.0f} deg, {3:+.0f} mm)   ", n, self.hit[0],
                     self.hit[1] - self.press[1], self.hit[2] - self.press[2])
            if self.hit[4]:
                txt += tx("({0})   ", self.hit[4])
        else:
            where = self.hit[4] if self.hit[4] else tx("{0:.0f} deg, {1:.0f} mm", self.hit[1], self.hit[2])
            txt = tx("Place {0} piece(s) on {1} ({2})   ", n, self.hit[0], where)
        if self.hit is not None:
            # angle only, height only, turned over: the Tool tab's AC9 Piece (user 2026-11-01: nothing held while
            # dragging, a press always slides)
            txt += tx("release: keep   angle / height / tilt: Tool tab, AC9 Piece")
        context.area.header_text_set(txt + tx("   Esc: cancel"))

    def _finish(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE':
            self.mouse = (event.mouse_region_x, event.mouse_region_y)
            h = self._hit(context, *self.mouse)
            if h is not None or not self.pressed:          # dragged off the guide: they stay where they were
                self.hit = h
            self._status(context)
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.pressed:
            released = self._hit(context, event.mouse_region_x, event.mouse_region_y)
            if released is not None:
                self.hit = released
        if event.type != 'LEFTMOUSE' or self.hit is None:
            return {'RUNNING_MODAL'}
        # press: the pieces go there; held, they follow the cursor round and along the guide (this took the separate
        # Adjust's place, user 2026-10-15); release keeps them. A second press keeps them too (a release lost to
        # another window)
        if event.value == 'PRESS' and not self.pressed:
            self.pressed = True
            self._status(context)
            return {'RUNNING_MODAL'}
        if self.pressed and event.value in {'RELEASE', 'PRESS'}:
            spec = S.loads(S.dumps(self.spec))
            new = self._placements(self.hit)
            # what placing again does not make: said in one line (b-1); a fold is made again below
            lost = shape_kinds(store.get(context)[0], list(new))
            spec.setdefault('placement', {}).update(new)
            # the twins follow the pieces just placed (Dress used to put them back as the other side's mirror image)
            spec['placement'], _ = S.mirror_twins(spec, sources=set(new))
            store.put(context, spec)
            prepared = []
            r = show_start(context, spec, new)
            if r is None:
                r = remake(context, spec, new, prepared_out=prepared)
            r += _reset_on_garment(context, spec, new)
            r += refold_all(context, list(new), prepared=prepared[0] if prepared else None)
            r += [tx("{0}: {1} dropped (placed again)", n, ', '.join(k)) for n, k in sorted(lost.items())]
            overlay.invalidate()
            reselect_placed(context, spec, new, self.drag_piece)
            self._finish(context)
            self.report({'WARNING'} if r else {'INFO'},
                        rpt("Placed {0} piece(s) on {1}", len(new), self.hit[0]) + "".join(rpt("; ") + x for x in r))
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


def reselect_placed(context, spec, names, lead=None):
    """After a Place: the placed objects of the pieces just placed selected (they were made again and nothing held
    them), so the next press on one slides it at once (user 2026-11-04 b-3; before, every press only selected).
    The pattern pieces stay selected too (the 2D view shows the same pieces)."""
    keep = [ob for ob in SEL.objects(context) if ob.get(PV.PIECE_PROP) in names]
    SEL.select_placed(context, spec, list(names), side=lead, keep=keep)


class AC9_OT_SimClearPlacement(bpy.types.Operator):
    """Return selected pieces and linked partners to the 2D pattern, clearing their placement and hand edits. Other dressed pieces stay. With nothing selected, confirm before clearing all placements and taking off the dressed result. The patterns and seams stay; Ctrl+Z restores the change"""
    bl_idname = "ac9_cloth_sim.clear_placement"
    bl_label = "Back to Flat"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('placement')) and not _dress_running()

    def _names(self, context, spec):
        sel = set(selected_pieces(context))
        from . import piece_move as PM
        for ob in SEL.objects(context):
            sel.update(n for n in str(ob.get(PM.NAMES_PROP, '')).split(',') if n)
        # AC9's own objects only: a body or a light selected alongside does not make it refuse or skip the question
        if not sel and SEL.facts(context):
            raise ValueError(tx("The selected objects do not identify any pieces; nothing was changed"))
        return with_pairs(spec, sel) if sel else set(spec.get('placement', {}))

    def invoke(self, context, event):
        spec = store.get(context)[0]
        try:
            n = len(self._names(context, spec) & set(spec.get('placement', {})))
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        if not SEL.facts(context) and n:
            return context.window_manager.invoke_confirm(
                self, event, title=tx("All placed pieces back to flat?"),
                message=tx("Clear all {0} placements and hand edits, and take off the dressed result? Ctrl+Z restores the change", n),
                confirm_text=tx("Back to Flat"))
        return self.execute(context)

    def execute(self, context):
        # The selected pieces lose their 3D placement; the remaining dressed
        # result keeps its geometry and cache vertex correspondence.
        from . import dress as D
        from . import piece_move as PM
        from . import display_state as DS
        try:
            current = store.get(context)[0]
            names = self._names(context, current) & set(current.get('placement', {}))
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        if not names:
            self.report({'WARNING'}, tx("Select a placed pattern piece or placed shape first"))
            return {'CANCELLED'}
        all_pieces = not SEL.facts(context)
        PLV.leave_edit(context)
        g = bpy.data.objects.get(D.garment_name(current))
        if g is not None and D.has_result(g) and not all_pieces and not DS.result_names(g):
            self.report({'WARNING'}, tx("Cannot return selected pieces to flat: the dressed result's piece mapping is missing"))
            return {'CANCELLED'}
        backup = PLV.PreparedResetBackup(context, current)
        try:
            spec = S.loads(S.dumps(sync(context)))
            moved = sorted(n for n in names if spec['placement'][n].get('pose'))
            for pc in names:
                spec['placement'].pop(pc, None)
            PLV.remove(names, spec)
            store.put(context, spec)
            if g is not None and not all_pieces:
                # Keep the result's vertex/cache correspondence. Reset ownership
                # plus no placement means this piece has no 3D representation.
                hidden = set(D.reset_pieces(g)) | (names & set(DS.result_names(g)))
                D.set_shown_as_placed(g, spec, hidden)
            PM.clear(context)
            from . import garment_mark as GM
            GM.clear()
        except Exception:
            backup.rollback()
            raise
        finally:
            backup.close()
        took_off = g is not None and all_pieces
        if took_off:
            if context.object == g and context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            me = g.data
            bpy.data.objects.remove(g)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        overlay.invalidate()
        self.report({'INFO'}, rpt("{0} piece(s) back to the flat pattern", len(names))
                    + (rpt("; the dressed result taken off") if took_off else "")
                    + (rpt("; hand moves of {0} dropped", ', '.join(moved)) if moved else ""))
        return {'FINISHED'}


def _dress_running():
    from . import dress as D
    return D.AC9_OT_SimDress.running()


class AC9_OT_SimShowPlaced(bpy.types.Operator):
    """Only to adjust the placement by hand before a Phase Sim (Phase Sim works without it): the start shape Phase Sim would make put in the scene as objects, to move, rotate, tilt or edit like any mesh; Phase Sim then starts those pieces from them. Pieces already moved by hand are kept"""
    bl_idname = "ac9_cloth_sim.show_placed"
    bl_label = "Move by Hand"
    bl_options = {'REGISTER', 'UNDO'}
    # Part to Seam / Band on Edge: the placed pieces' objects while others are not placed yet (dress.prepare)
    placed_only: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('placement')) and context.scene.ac9_cloth_sim.body is not None

    def execute(self, context):
        from . import dress as D
        sync(context)
        try:
            res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True,
                             placed_only=self.placed_only)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        spec, _ = store.get(context)
        made, kept, notes = PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'])
        # a Dress hid them (b-3): Move by Hand shows them again, their collection too if the Outliner hid it
        opened = PLV.reveal(context, spec, set(PLV.objects(spec).values()))
        self.report({'WARNING'} if notes else {'INFO'}, rpt("{0} placed object(s) made", len(made))
                    + (rpt(", {0} moved by hand kept", len(kept)) if kept else "")
                    + (rpt("; moved to mirror their pair (placed apart before; Unlink Pair to move them apart): {0}", ', '.join(notes))
                       if notes else "")
                    + (rpt("; shown again: {0} (hidden in the Outliner)", ', '.join(opened)) if opened else ""))
        return {'FINISHED'}


def move_tool(context):
    """The Move tool on in a 3D view (the panel's button may be pressed from the Properties editor's Tool tab)."""
    win = context.window
    areas = ([context.area] if context.area and context.area.type == 'VIEW_3D' else []) + \
            [a for a in (win.screen.areas if win else []) if a.type == 'VIEW_3D']
    for area in areas:
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        try:
            with context.temp_override(window=win, area=area, region=region):
                bpy.ops.wm.tool_set_by_id(name="builtin.move")
            return True
        except Exception:
            continue
    return False


class AC9_OT_SimMoveByHand(bpy.types.Operator):
    """Make the chosen pieces ready to move by hand in 3D"""
    bl_idname = "ac9_cloth_sim.move_by_hand"
    bl_label = "Move by Hand"
    bl_options = {'REGISTER', 'UNDO'}

    # one button for "move this piece in 3D by hand" before and after a Simulate (user 2026-10-22, Fable
    # docs/reviews/REVIEW_fable_move_by_hand_unified_2026-10-22.md): before, the placed shapes as objects (show_placed,
    # unchanged: Reset, Done, Fold... call it) with the chosen ones selected; after, a handle on the garment's chosen
    # pieces (piece_move.pick), a piece Reset (hidden on the garment) its placed object -- a handle there would move
    # hidden vertices the next Simulate's flush_placed overwrites

    @staticmethod
    def _dressed(spec):
        from . import dress as D
        g = bpy.data.objects.get(D.garment_name(spec))
        return g if D.has_result(g) else None

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or not spec.get('placement') or context.scene.ac9_cloth_sim.body is None:
            return False
        from . import dress as D
        if D.AC9_OT_SimDress.running():
            cls.poll_message_set("Not while a Phase Sim or Live Sim runs")
            return False
        if cls._dressed(spec) is not None and not selected_pieces(context):
            cls.poll_message_set("Click a piece on the pattern or the garment first")
            return False
        return True

    @classmethod
    def description(cls, context, properties):
        spec = store.get(context)[0]
        if spec is not None and cls._dressed(spec) is not None:
            return tx("Select the chosen pieces in 3D and switch to the Move tool: a handle on the garment for a "
                      "piece on it, the placed shape for one put back or placed after it (made if missing)")
        return tx("Select the chosen pieces' placed shapes in 3D (made if missing) and switch to the Move tool: G / R "
                  "them like any object; Phase Sim starts them from there")

    def execute(self, context):
        # show_placed/give_shape can materialize shapes before control-side
        # conversion. A refusal of any pair must restore the whole operation.
        spec, _ = store.get(context)
        selected = [o.name for o in SEL.objects(context)]
        active = context.view_layer.objects.active
        active = active.name if active else None
        object_names = set(context.scene.objects.keys())
        collection_names = set(bpy.data.collections.keys())
        visibility = {o.name: o.hide_viewport for o in set(PLV.objects(spec).values())}
        layers = []
        def remember_layer(layer):
            layers.append((layer, layer.exclude, layer.hide_viewport, layer.collection.hide_viewport))
            for child in layer.children:
                remember_layer(child)
        remember_layer(context.view_layer.layer_collection)
        mesh_names = {o.name: o.data.name for o in set(PLV.objects(spec).values())}
        archive_names = {prop: {n: me.name for n, me in getter(spec).items()}
                         for prop, getter in ((PLV.KEEP_PROP, PLV._kept),
                                              (PLV.BEFORE_SEAM_PROP, PLV.before_seam))}
        backup = PLV.PreparedResetBackup(context, spec)
        gmesh_name = backup.g.data.name if backup.g is not None else None
        def rollback():
            backup.rollback()
            self._restore_selection(context, selected, active)
            for name, mesh_name in mesh_names.items():
                ob = context.scene.objects.get(name)
                if ob is not None:
                    ob.data.name = mesh_name
            for prop, getter in ((PLV.KEEP_PROP, PLV._kept), (PLV.BEFORE_SEAM_PROP, PLV.before_seam)):
                for n, me in getter(spec).items():
                    if n in archive_names[prop]:
                        me.name = archive_names[prop][n]
            if backup.g is not None:
                backup.g.data.name = gmesh_name
            for ob in list(context.scene.objects):
                if ob.name not in object_names:
                    bpy.data.objects.remove(ob, do_unlink=True)
            for col in list(bpy.data.collections):
                if col.name not in collection_names and not col.objects and not col.children:
                    bpy.data.collections.remove(col)
            for name, hidden in visibility.items():
                ob = context.scene.objects.get(name)
                if ob is not None:
                    ob.hide_viewport = hidden
            for layer, exclude, hidden, collection_hidden in layers:
                layer.exclude, layer.hide_viewport = exclude, hidden
                layer.collection.hide_viewport = collection_hidden
            context.view_layer.update()
        try:
            result = self._execute(context)
            if 'CANCELLED' in result:
                rollback()
            return result
        except Exception:
            rollback()
            raise
        finally:
            backup.close()

    @staticmethod
    def _restore_selection(context, selected, active):
        SEL.deselect_all(context)
        for name in selected:
            ob = context.scene.objects.get(name)
            if ob is not None:
                ob.select_set(True)
        context.view_layer.objects.active = context.scene.objects.get(active) if active else None

    def _execute(self, context):
        from . import dress as D
        from . import piece_move as PM
        spec, _ = store.get(context)
        names = PLV.hand_selection(context, selected_pieces(context))
        PLV.leave_edit(context)
        if context.object is not None and context.object.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                pass
        g = self._dressed(spec)
        if g is None:
            r = bpy.ops.ac9_cloth_sim.show_placed(placed_only=True)   # pieces not placed yet: no object
            if 'FINISHED' not in r:
                return r
            spec, _ = store.get(context)
            obs = PLV.objects(spec)
            try:
                PLV.activate_sides(context, spec, [n for n in names if n in obs
                    and obs[n].get(PLV.TWIN_PROP) == n and obs[n].get(PLV.PROP) not in names])
            except ValueError as exc:
                self.report({'WARNING'}, str(exc))
                return {'CANCELLED'}
            obs = PLV.objects(spec)
            # a pair's mirrored side: its lead's object (the mirror follows)
            first = lead_only(spec, names, selected_first=True)
            picked = SEL.select_placed(context, spec, first, side=first[0] if first else None)
            if not picked:
                return {'FINISHED'}       # show_placed said what it made; nothing chosen, the tool stays
            PLV.center_control_origins(context, spec, picked)
            move_tool(context)
            self.report({'INFO'}, rpt("placed shape of {0} selected (round the body): G / R it",
                                      ', '.join(o[PLV.PROP] for o in picked)))
            return {'FINISHED'}
        # a piece placed after the garment was simulated: its placed shape made and chosen (user 2026-10-25 b-3: it
        # was refused "not on the dressed garment"); a piece not placed at all is the only one refused
        unplaced = [n for n in names if n not in spec.get('placement', {})]
        names = [n for n in names if n not in unplaced]
        spec, _given = give_shape(context, spec, names)
        hidden = set(D.hidden_pieces(g, spec))
        obs = PLV.objects(spec)
        have = D.garment_pieces(g)
        try:
            PLV.activate_sides(context, spec, [n for n in names if n in obs
                and obs[n].get(PLV.TWIN_PROP) == n and obs[n].get(PLV.PROP) not in names
                and (n in hidden or n not in have)])
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        obs = PLV.objects(spec)
        on_g = [n for n in names if n not in hidden and n in have]
        names = names + unplaced
        reset_names = lead_only(spec, [n for n in names if (n in hidden or n not in have) and n in obs],
                                selected_first=True)
        took = []
        for n in on_g:
            if PM.pick(context, g, n, extend=bool(took)) is not None:
                took.append(n)
        if not took:
            SEL.deselect_all(context)
        reset = SEL.select_placed(context, spec, reset_names, side=reset_names[0] if reset_names else None,
                                  keep=SEL.objects(context), activate=not took) if reset_names else []
        if not took and not reset:
            self.report({'WARNING'}, rpt("{0}: not placed (Place it on a guide first)", ', '.join(names)))
            return {'CANCELLED'}
        PLV.center_control_origins(context, spec, reset)
        move_tool(context)
        msg = []
        if took:
            msg.append(rpt("{0} on the garment (its pair stays)", ', '.join(took)))
        if reset:
            msg.append(rpt("{0} as placed shapes (the next Phase Sim sews them on from there)", ', '.join(o[PLV.PROP] for o in reset)))
        self.report({'INFO'}, "; ".join(msg) + rpt(": G / R it"))
        return {'FINISHED'}


class AC9_OT_SimResetPlaced(bpy.types.Operator):
    """Put the selected pieces (nothing selected: all) back on their guides, the cylinders round the body: their hand moves (moved or edited by hand) go and they are placed again from the placement. Pieces on the dressed garment go back to placed first (Back to Placed)"""
    bl_idname = "ac9_cloth_sim.reset_placed"
    bl_label = "Back to Guide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('placement')) and not _dress_running()

    def _names(self, context, spec):
        """The selected pieces that are on and placed, with their pairs; all placed pieces when none is selected."""
        on = {pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)} & set(spec.get('placement', {}))
        sel = with_pairs(spec, selected_pieces(context)) & on
        return sel or on, bool(sel)

    def invoke(self, context, event):
        spec = store.get(context)[0]
        names, picked = self._names(context, spec)
        if not picked:
            return context.window_manager.invoke_confirm(
                self, event, title="All pieces back on their guides?",
                message=tx("{0} piece(s) lose their hand moves (and a dressed result is taken off; Ctrl+Z brings it "
                           "back). Select pieces first to put back only those", len(names)), confirm_text="Back to Guide")
        return self.execute(context)

    def execute(self, context):
        from . import dress as D
        spec, _ = store.get(context)
        names, picked = self._names(context, spec)
        if not names:
            return {'CANCELLED'}
        # on the dressed garment: back to placed first, the step between (user 2026-10-22: the three "Back to")
        g = bpy.data.objects.get(D.garment_name(spec))
        if D.has_result(g) and (not picked or any(n not in PLV.objects(spec) for n in names)):
            if bpy.ops.ac9_cloth_sim.reset(to_guide=True) != {'FINISHED'}:
                return {'CANCELLED'}
            spec, _ = store.get(context)
        PLV.leave_edit(context)
        names = with_pairs(spec, names)
        PLV.remove(names, spec)
        # the hand moves go too: only Back to Guide drops them (user 2026-10-14 b-3)
        sp = S.loads(S.dumps(spec))
        for n in names:
            sp.get('placement', {}).get(n, {}).pop('pose', None)
        store.put(context, sp)
        if not D.has_result(bpy.data.objects.get(D.garment_name(sp))):
            return bpy.ops.ac9_cloth_sim.show_placed(placed_only=True)
        # on a dressed garment only these pieces (put back to placed) come back as objects: Move by Hand would have
        # made every piece's object over the garment (user 2026-10-21)
        try:
            res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        PLV.build(context, sp, res['pat'], res['tris'], res['X'], res['pairs'], only=set(names))
        D.sync_hidden(bpy.data.objects.get(D.garment_name(sp)), sp)
        self.report({'INFO'}, rpt("back on the guide: {0}", ', '.join(sorted(names))))
        return {'FINISHED'}


class AC9_OT_SimUnlinkPair(bpy.types.Operator):
    """Place a mirrored pair independently, or link it again: unlinked, each side moves on its own (a monster with its arms in different places). The pattern stays mirrored"""
    bl_idname = "ac9_cloth_sim.unlink_pair"
    bl_label = "Unlink Pair"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None:
            return False
        sel = set(selected_pieces(context))
        return any(a in sel or b in sel for a, b in S.twins(spec))

    def execute(self, context):
        sync(context)
        spec, outlines = store.get(context)
        PLV.leave_edit(context)
        spec2 = S.loads(S.dumps(spec))
        pl = spec2.setdefault('placement', {})
        sel = set(selected_pieces(context))
        done = []
        for a, b in S.twins(spec):
            if a not in sel and b not in sel:
                continue
            link = bool(pl.get(a, {}).get('unlinked') or pl.get(b, {}).get('unlinked'))
            for x in (a, b):
                if x in pl:
                    if link:
                        pl[x].pop('unlinked', None)
                    else:
                        pl[x]['unlinked'] = True
            obs_ = PLV.objects(spec2)
            if (obs_.get(a) is not None and obs_.get(b) is not None and obs_[a] is not obs_[b]
                    and len(obs_[a].data.vertices) != len(obs_[b].data.vertices)):
                pass        # two meshes that are not mirror images: link_pairs below makes one follow, or parts them
            elif link:
                PLV.join_pair(context, spec2, a, b, prefer=context.active_object.get(PLV.PROP) if context.active_object else None)
            else:
                PLV.split_pair(context, spec2, outlines, a, b)
            done.append(f"{a} / {b} {'linked' if link else 'unlinked'}")
        store.put(context, spec2)
        PLV.link_pairs(context, spec2)          # a pair shown as two objects: following again, or apart
        sync(context)                           # linked again: the leading side's move is its pair's, mirrored
        self.report({'INFO'}, "; ".join(done))
        return {'FINISHED'}


class AC9_OT_SimPair(bpy.types.Operator):
    """Place the two selected pieces as mirror images though they were not drawn as a mirrored copy (about the same outline, mirrored): placing one places the other. Again on a pair: unpair"""
    bl_idname = "ac9_cloth_sim.pair"
    bl_label = "Pair"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and len(selected_pieces(context)) == 2

    def execute(self, context):
        sync(context)
        spec, outlines = store.get(context)
        a, b = selected_pieces(context)
        act = context.active_object
        lead = (act.get(PV.PIECE_PROP) or act.get(PLV.PROP)) if act is not None else None
        if lead == b:
            a, b = b, a                       # the active piece leads, the other is placed as its mirror image
        if any({a, b} == set(t) for t in S.twins(spec)):
            self.report({'INFO'}, "Already a mirrored pair (drawn as a mirrored copy): Unlink Pair sets it apart")
            return {'CANCELLED'}
        spec2 = S.loads(S.dumps(spec))
        prs = [p for p in spec2.get('pairs', []) if set(p) != {a, b}]
        if len(prs) != len(spec2.get('pairs', [])):
            spec2['pairs'] = prs
            store.put(context, spec2)
            self.report({'INFO'}, rpt("{0} / {1}: unpaired", a, b))
            return {'FINISHED'}
        wa, wb = np.ptp(outlines[a].B, axis=0), np.ptp(outlines[b].B, axis=0)
        if np.abs(wa - wb).max() > 0.1 * max(wa.max(), wb.max()):
            self.report({'WARNING'}, rpt("{0} and {1} differ in size by more than 10 %: paired anyway", a, b))
        prs.append([a, b])
        spec2['pairs'] = prs
        if a in spec2.get('placement', {}):
            spec2['placement'], _ = S.mirror_twins(spec2, sources={a})
        store.put(context, spec2)
        # b goes where a's mirror image is, still moved by its own hand move (b-5: Mirror to Pair copies a's); a's
        # object stays as it is
        r = []
        obs = PLV.objects(spec2)
        if b in obs and obs[b] is not obs.get(a):
            r = _remake_one(context, spec2, b)
        self.report({'WARNING'} if r else {'INFO'},
                    rpt("{0} / {1}: paired ({2} placed as {3}'s mirror image)", a, b, b, a) + "".join(rpt("; ") + x for x in r))
        return {'FINISHED'}


def active_placed(context):
    """The active object when it is a placed piece's object (placed_view), else None."""
    ob = context.active_object
    return ob if ob is not None and ob.get(PLV.PROP) and store.get(context)[0] is not None else None


def mirror_partner(spec, name):
    """The piece `name` is placed as a mirror image of, but does not follow on its own: its Pair partner, or its twin
    set apart by Unlink (user 2026-10-14 b-5). None for a linked twin (it follows by itself) or a single piece."""
    for a, b in spec.get('pairs', []):
        if name in (a, b):
            return b if name == a else a
    pl = spec.get('placement', {})
    for a, b in S.twins(spec):
        if name in (a, b) and (pl.get(a, {}).get('unlinked') or pl.get(b, {}).get('unlinked')):
            return b if name == a else a
    return None


class AC9_OT_SimSetPose(bpy.types.Operator):
    """Type how far the active placed piece is moved (mm) and turned (degrees, about its own middle) from where its placement put it. A mirrored pair's other half follows"""
    bl_idname = "ac9_cloth_sim.set_pose"
    bl_label = "Move Placed Piece"
    bl_options = {'REGISTER', 'UNDO'}

    loc: FloatVectorProperty(name="Move (mm)", size=3, precision=1, step=100)
    rot: FloatVectorProperty(name="Turn (deg)", size=3, precision=1, step=100)

    @classmethod
    def poll(cls, context):
        return active_placed(context) is not None

    def invoke(self, context, event):
        p = PLV.pose_of(active_placed(context))
        self.loc = p['loc'] if p else (0.0, 0.0, 0.0)
        self.rot = p['rot'] if p else (0.0, 0.0, 0.0)
        return context.window_manager.invoke_props_dialog(self, title=tx("Move {0}", active_placed(context)[PLV.PROP]))

    def execute(self, context):
        ob = active_placed(context)
        PLV.leave_edit(context)
        PLV.set_matrix(ob, PLV.matrix_for_pose(ob, {'loc': list(self.loc), 'rot': list(self.rot)}))
        sync(context)
        return {'FINISHED'}


class AC9_OT_SimMirrorPose(bpy.types.Operator):
    """Place the active piece's Pair partner (or its twin set apart by Unlink) as its mirror image once: where the active one is and how it is moved and turned, mirrored. It does not follow afterwards"""
    bl_idname = "ac9_cloth_sim.mirror_pose"
    bl_label = "Mirror to Pair"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = active_placed(context)
        return ob is not None and mirror_partner(store.get(context)[0], ob[PLV.PROP]) is not None

    def execute(self, context):
        a = active_placed(context)[PLV.PROP]
        spec = sync(context)
        _, outlines = store.get(context)
        b = mirror_partner(spec, a)
        pl = spec.get('placement', {})
        if a not in pl:
            self.report({'WARNING'}, rpt("{0} is not placed", a))
            return {'CANCELLED'}
        sp = S.loads(S.dumps(spec))
        keep = {k: sp['placement'][b][k] for k in ('unlinked',) if k in sp['placement'].get(b, {})}
        sp['placement'][b] = dict(S.mirrored_placement(pl[a], outlines[a], outlines[b], pose='mirror'), **keep)
        store.put(context, sp)
        PLV.leave_edit(context)
        obs = PLV.objects(sp)
        r = []
        if b in obs or a in obs:
            if b in obs:
                r = _remake_one(context, sp, b)
            else:
                from . import dress as D
                try:
                    res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
                    PLV.build(context, sp, res['pat'], res['tris'], res['X'], res['pairs'])
                except ValueError as exc:
                    r = [tx("placed object not made again ({0}): Move by Hand", exc)]
        overlay.invalidate()
        self.report({'WARNING'} if r else {'INFO'}, rpt("{0} placed as {1}'s mirror image", b, a) + "".join(rpt("; ") + x for x in r))
        return {'FINISHED'}


def side_pairs(spec, names):
    """The pairs (twins linked or set apart, Pair) touching `names`, both sides On and placed."""
    pl = spec.get('placement', {})
    out, seen = [], set()
    for a, b in list(S.placement_pairs(spec)) + list(S.twins(spec)):
        if frozenset((a, b)) in seen or not ({a, b} & set(names)):
            continue
        seen.add(frozenset((a, b)))
        if a in pl and b in pl:
            out.append((a, b))
    return out


class AC9_OT_SimSwapSides(bpy.types.Operator):
    """Swap the selected pair's sides: each piece goes where its partner was (a hand move and folds stay with the place; a shape moved to its seams, laid on its edge or edited in Edit Mode is made again)"""
    bl_idname = "ac9_cloth_sim.swap_sides"
    bl_label = "Swap Sides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None:
            return False
        if _dress_running():
            cls.poll_message_set("Not while a Phase Sim or Live Sim runs")
            return False
        sel = selected_pieces(context)
        if not side_pairs(spec, sel):
            cls.poll_message_set("Select a placed pair (both sides On)")
            return False
        return True

    def execute(self, context):
        spec = sync(context)
        PLV.leave_edit(context)
        pairs = side_pairs(spec, selected_pieces(context))
        names = [n for p in pairs for n in p]
        lost = shape_kinds(spec, names)
        sp = S.loads(S.dumps(spec))
        for a, b in pairs:
            sp['placement'] = S.swap_sides(sp, a, b)
        store.put(context, sp)
        # made again as Place makes a piece placed again (the start shape; on a dressed garment Reset, user 2026-10-26
        # b-1 as Place's), its folds made again from the spec
        r = show_start(context, sp, names)
        if r is None:
            r = remake(context, sp, names)
        r += _reset_on_garment(context, sp, names)
        r += refold_all(context, names)
        r += [tx("{0}: {1} dropped (placed again)", n, ', '.join(k)) for n, k in sorted(lost.items())]
        overlay.invalidate()
        self.report({'WARNING'} if r else {'INFO'},
                    rpt("Swapped sides: {0}", ', '.join(f"{a} / {b}" for a, b in pairs)) + "".join(rpt("; ") + x for x in r))
        return {'FINISHED'}


def _remade(context, spec, sp, lead):
    """sp stored and the pieces `lead` (with their pairs) made again as Place makes them: report lines."""
    lost = shape_kinds(spec, lead)
    store.put(context, sp)
    r = show_start(context, sp, lead)
    if r is None:
        r = remake(context, sp, lead)
    r += _reset_on_garment(context, sp, lead)
    r += refold_all(context, lead)
    r += [tx("{0}: {1} dropped (placed again)", n, ', '.join(k)) for n, k in sorted(lost.items())]
    overlay.invalidate()
    return r


def _placed_lead(context, names):
    spec = sync(context)
    pl = spec.get('placement', {})
    return spec, lead_only(spec, [n for n in names if n in pl])


def set_tilt(context, names, deg=None, turn=0.0):
    """The placed pieces `names` tilted to `deg` about the line round their guide through their anchor, or by `turn`
    from their own (Tilt and Flip Up/Down, user 2026-11-01, DECISIONS cloth-sim-collar-flip-2026-11-01): a collar's
    outer edge, low in the pattern, goes next to the neck at 180. Their pairs follow. Nothing else changes (user
    2026-11-01: folds and the rest by hand). (pieces, report lines), or (None, why)."""
    spec, lead = _placed_lead(context, names)
    if not lead:
        return None, tx("Place the piece first")
    PLV.leave_edit(context)
    sp = S.loads(S.dumps(spec))
    for n in lead:
        sp['placement'][n] = with_tilt(sp['placement'][n], deg if deg is not None
                                       else float(sp['placement'][n].get('tilt', 0.0)) + turn)
    sp['placement'], _ = S.mirror_twins(sp, sources=set(lead))
    return lead, _remade(context, spec, sp, lead)


def set_ring(context, names, k=None):
    """The placed bands `names` laid at a ring k times their own length's (AC9 Piece's Ring Size, user 2026-11-02,
    DECISIONS cloth-sim-ring-size-2026-11-02), or None: Auto, pushed out until clear of the body where it is now (its
    hand move in) -- measured again here. Their pairs follow. (pieces, report lines), or (None, why)."""
    spec, lead = _placed_lead(context, names)
    if not lead:
        return None, tx("Place the piece first")
    PLV.leave_edit(context)
    from .core import roles as RO
    sp = S.loads(S.dumps(spec))
    bands = RO.with_role(sp, RO.BAND_ROLES)
    for n in lead:
        p = {k_: v for k_, v in sp['placement'][n].items() if k_ != 'ring'}
        if k is not None and n in bands:    # a body piece selected with the band keeps no ring
            p['ring'] = round(float(k), 3)
        sp['placement'][n] = p
    sp['placement'], _ = S.mirror_twins(sp, sources=set(lead))
    return lead, _remade(context, spec, sp, lead)


def move_placed(context, names, theta=None, h=None):
    """The placed pieces `names` moved to the active one's angle round its guide `theta` (deg) or height `h` (mm), the
    others by as much (AC9 Piece's Around / Height, user 2026-11-01: the angle changed without the height, which a
    drag could not do). Their pairs follow. (pieces, report lines), or (None, why)."""
    spec, lead = _placed_lead(context, names)
    if not lead:
        return None, tx("Place the piece first")
    PLV.leave_edit(context)
    sp = S.loads(S.dumps(spec))
    pl = sp['placement']
    a = pl[lead[0]]
    dth = 0.0 if theta is None else float(theta) - float(a['theta'])
    dh = 0.0 if h is None else float(h) - float(a['h'])
    for n in lead:
        pl[n] = dict(pl[n], theta=round(float(pl[n]['theta']) + dth, 3), h=round(float(pl[n]['h']) + dh, 3))
    sp['placement'], _ = S.mirror_twins(sp, sources=set(lead))
    return lead, _remade(context, spec, sp, lead)


class AC9_OT_SimFlipUpDown(bpy.types.Operator):
    """Tilt the selected placed pieces 180 degrees more about the line round their guide: upside down, a collar's outer edge next to the neck (again: back). Their pairs follow"""
    bl_idname = "ac9_cloth_sim.flip_up_down"
    bl_label = "Flip Up/Down"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None:
            return False
        if _dress_running():
            cls.poll_message_set("Not while a Phase Sim or Live Sim runs")
            return False
        if not any(n in spec.get('placement', {}) for n in selected_pieces(context)):
            cls.poll_message_set("Place the piece first")
            return False
        return True

    def execute(self, context):
        done, r = set_tilt(context, selected_pieces(context), turn=180.0)
        if done is None:
            self.report({'WARNING'}, r)
            return {'CANCELLED'}
        self.report({'WARNING'} if r else {'INFO'},
                    rpt("Flipped up/down: {0}", ', '.join(done)) + "".join(rpt("; ") + x for x in r))
        return {'FINISHED'}


class AC9_OT_SimMoveOutside(bpy.types.Operator):
    """Move the selected current shapes outside nearby cloth, before or after simulation. Preserve their shape; continue simulation from the correction"""
    bl_idname = "ac9_cloth_sim.move_outside"
    bl_label = "Move Outside"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or context.scene.ac9_cloth_sim.body is None or _dress_running():
            return False
        if not selected_pieces(context):
            cls.poll_message_set("Select a piece first")
            return False
        return True

    def execute(self, context):
        from . import outside as OUT
        from . import piece_move as PM
        names = selected_pieces(context)
        for ob in SEL.objects(context):
            for n in str(ob.get(PM.NAMES_PROP, '')).split(','):
                if n and n not in names:names.append(n)
        try:
            rows = OUT.move_selected(context, names)
        except ValueError as exc:
            self.report({'WARNING'}, tx(str(exc)))
            return {'CANCELLED'}
        overlay.invalidate()
        self.report({'INFO'}, rpt("Moved outside: {0}", ', '.join(rows)))
        return {'FINISHED'}


CLASSES = (AC9_OT_SimDetectGuides, AC9_OT_SimPlace, AC9_OT_SimClearPlacement, AC9_OT_SimShowPlaced, AC9_OT_SimMoveByHand,
           AC9_OT_SimResetPlaced, AC9_OT_SimUnlinkPair, AC9_OT_SimPair, AC9_OT_SimSetPose,
           AC9_OT_SimMirrorPose, AC9_OT_SimSwapSides, AC9_OT_SimFlipUpDown, AC9_OT_SimMoveOutside)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
