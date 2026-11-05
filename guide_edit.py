# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit Guides: the placement guides (core/body_axes.py, spec['guides']) as objects to move, turn and scale by hand,
for a body Detect Guides read a little off (a scan, an unusual pose) (user 2026-10-14).

Edit Guides makes one wire cylinder object per guide (collection GUIDE_COLL): its location is the guide's origin o,
its rotation the guide's frame R (rows ex, ey, ez), its scale x / y the radius and z the length (h0, h1 scaled about
o). G / R / S move them like any object. With Mirror on (the default), moving one of a left / right pair (the arms,
the legs) moves the other as its mirror image about the body's center plane (the torso guide's axis x, as the placed
pairs use, placed_view.center_x); the torso and the neck are left free.

What is moved is written to the spec a moment after the move stops (WRITE_S), so Place and the overlay read it.
Done removes the cylinders; the placed objects, made on the old guides, are made again on the new ones (their hand
moves kept), as after Detect Guides.
"""
import json
from .i18n import rpt

import bpy
import numpy as np
from mathutils import Matrix

from . import overlay
from . import placed_view as PLV
from . import store
from .core import spec as S

GUIDE_COLL = "AC9_ClothSim_Guides"
PROP = "ac9_guide"             # the guide's name on its object
MM = 0.001
SEG = 32
WRITE_S = 0.4                  # s after the last move before the spec is written
PAIRS = {'arm_L': 'arm_R', 'arm_R': 'arm_L', 'leg_L': 'leg_R', 'leg_R': 'leg_L'}
_state = {'mat': {}, 'dirty': False, 'busy': False}


def objects():
    """{guide name: object}."""
    coll = bpy.data.collections.get(GUIDE_COLL)
    return {ob[PROP]: ob for ob in coll.objects if PROP in ob} if coll is not None else {}


def editing():
    return bool(objects())


def _mesh(name, g):
    """A unit-radius wire cylinder from h0 to h1 (m), in the guide's frame: the object's scale is (r, r, 1)."""
    me = bpy.data.meshes.new(name)
    E = [(i, (i + 1) % SEG) for i in range(SEG)] + [(SEG + i, SEG + (i + 1) % SEG) for i in range(SEG)]
    E += [(i, SEG + i) for i in range(0, SEG, SEG // 4)]
    me.from_pydata(_verts(g), E, [])
    return me


def _verts(g):
    a = np.linspace(0, 2 * np.pi, SEG, endpoint=False)
    return [(float(np.cos(t)), float(np.sin(t)), z) for z in (g['h0'] * MM, g['h1'] * MM) for t in a]


def _matrix(g, sz=1.0):
    R = np.asarray(g['R'], float)              # rows ex, ey, ez = the object's axes (columns)
    M = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            M[i][j] = R[j][i]
        M[i][3] = float(g['o'][i]) * MM
    r = g['r'] * MM
    return M @ Matrix.Diagonal((r, r, sz, 1.0))


def _read(ob, g0):
    """The guide as the object now holds it, from the guide it was made from (g0: its h0, h1, points)."""
    M = np.array(ob.matrix_world)
    A = M[:3, :3]
    sx, sy, sz = (float(np.linalg.norm(A[:, k])) for k in range(3))
    ex, ey = A[:, 0] / sx, A[:, 1] / sy
    ez = np.cross(ex, ey)
    ez /= np.linalg.norm(ez)
    ey = np.cross(ez, ex)
    if np.dot(ez, A[:, 2]) < 0:                # a negative scale: keep the frame right-handed, the axis as drawn
        ez, ex = -ez, -ex
    k = sz / max(float(ob.get('ac9_sz', 1.0)), 1e-9)   # the length's scale since the object was made
    g = dict(g0)
    g['o'] = [round(float(v) / MM, 3) for v in M[:3, 3]]
    g['R'] = [[round(float(v), 6) for v in row] for row in (ex, ey, ez)]
    g['r'] = round(0.5 * (sx + sy) / MM, 3)
    g['h0'], g['h1'] = round(g0['h0'] * k, 3), round(g0['h1'] * k, 3)
    g['points'] = [dict(p, h=round(p['h'] * k, 3)) for p in g0.get('points', [])]
    if 'full' in g0:
        # the arm's full axis moves with it, rigidly (core/measure.py, dress.py read it)
        R0, R1 = np.asarray(g0['R'], float), np.asarray(g['R'], float)
        Q = R1.T @ R0                          # world rotation old -> new
        o0, o1 = np.asarray(g0['o'], float), np.asarray(g['o'], float)
        g['full'] = {'o': [round(float(v), 3) for v in Q @ (np.asarray(g0['full']['o'], float) - o0) + o1],
                     'R': [[round(float(v), 6) for v in row] for row in (np.asarray(g0['full']['R'], float) @ Q.T)]}
    return g


def mirror(g, cx):
    """g's mirror image about the plane x = cx (mm): the frame S R S (ex flipped, ey / ez mirrored), so a point at
    theta on g is at 180 - theta on the mirror image (a 'front' point stays front)."""
    S_ = np.diag([-1.0, 1.0, 1.0])

    def pt(o):
        o = np.asarray(o, float).copy()
        o[0] = 2 * cx - o[0]
        return [round(float(v), 3) for v in o]

    def fr(R):
        R = np.asarray(R, float)
        return [[round(float(v), 6) for v in row] for row in (-(S_ @ R[0]), S_ @ R[1], S_ @ R[2])]
    m = dict(g, o=pt(g['o']), R=fr(g['R']))
    m['points'] = [dict(p, theta=round(((180.0 - p['theta'] + 180.0) % 360.0) - 180.0, 3)) for p in g.get('points', [])]
    if 'full' in g:
        m['full'] = {'o': pt(g['full']['o']), 'R': fr(g['full']['R'])}
    return m


def live():
    """{name: guide} as the objects hold them now (the overlay draws these while editing: the spec is written a
    moment after a move)."""
    return {n: _read(ob, json.loads(ob['ac9_g0'])) for n, ob in objects().items()}


def start(context, spec):
    """Make the guide objects (Edit Guides)."""
    coll = bpy.data.collections.get(GUIDE_COLL)
    if coll is None:
        coll = bpy.data.collections.new(GUIDE_COLL)
        context.scene.collection.children.link(coll)
        from . import view_setup
        view_setup.refresh(context)
    made = []
    for name, g in spec.get('guides', {}).items():
        ob = bpy.data.objects.new("Guide_" + name, _mesh("Guide_" + name, g))
        ob[PROP] = name
        ob['ac9_sz'] = 1.0
        ob['ac9_g0'] = json.dumps(g)
        ob.display_type = 'WIRE'
        ob.show_in_front = True
        ob.matrix_world = _matrix(g)
        coll.objects.link(ob)
        made.append(name)
    _state['mat'] = {n: tuple(map(tuple, ob.matrix_world)) for n, ob in objects().items()}
    return made


def _write(context):
    spec, _ = store.get(context)
    obs = objects()
    if spec is None or not obs:
        return False
    sp = S.loads(S.dumps(spec))
    for name, ob in obs.items():
        sp.setdefault('guides', {})[name] = _read(ob, json.loads(ob['ac9_g0']))
    if json.dumps(sp.get('guides'), sort_keys=True) == json.dumps(spec.get('guides'), sort_keys=True):
        return False
    store.put(context, sp)
    overlay.invalidate()
    return True


def finish(context):
    """Done: the spec written, the cylinders removed, the placed objects made again on the new guides."""
    _write(context)
    for ob in list(objects().values()):
        me = ob.data
        bpy.data.objects.remove(ob)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    coll = bpy.data.collections.get(GUIDE_COLL)
    if coll is not None and not coll.objects:
        bpy.data.collections.remove(coll)
    _state['mat'] = {}
    spec, _ = store.get(context)
    obs = PLV.objects(spec)
    # which placed pieces were hidden (after a Dress, user b-3): made again, they stay hidden
    hidden = sorted(n for n, o in obs.items() if o.hide_get())
    if obs:
        PLV.leave_edit(context)
        PLV.remove(list(obs), spec)
    return sorted(obs), hidden


@bpy.app.handlers.persistent         # kept when a file is opened (a plain handler is dropped then, 2026-10-14)
def _mirror_moved(scene):
    """The depsgraph handler: a moved guide of a pair moves its partner as its mirror image."""
    if _state['busy']:
        return
    obs = objects()
    if not obs:
        return
    props = scene.ac9_cloth_sim
    moved = [n for n, ob in obs.items() if _state['mat'].get(n) != tuple(map(tuple, ob.matrix_world))]
    if not moved:
        return
    _state['busy'] = True
    try:
        if props.guide_mirror:
            done = set()
            for n in moved:
                p = PAIRS.get(n)
                if p is None or p not in obs or n in done:
                    continue
                tor = obs.get('torso')
                cx = float(tor.matrix_world.translation.x) / MM if tor is not None else 0.0
                g = _read(obs[n], json.loads(obs[n]['ac9_g0']))
                m = mirror(g, cx)
                pob = obs[p]
                # the partner becomes the mirror image: its cylinder made again at the mirror's length
                pob['ac9_sz'] = 1.0
                pob['ac9_g0'] = json.dumps(m)
                pob.data.vertices.foreach_set('co', np.asarray(_verts(m), np.float32).ravel())
                pob.data.update()
                pob.matrix_world = _matrix(m)
                done |= {n, p}
        _state['mat'] = {n: tuple(map(tuple, ob.matrix_world)) for n, ob in obs.items()}
    finally:
        _state['busy'] = False
    _state['dirty'] = True
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=WRITE_S)


def _flush():
    if not _state['dirty']:
        return None
    _state['dirty'] = False
    try:
        _write(bpy.context)
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type == 'VIEW_3D':
                    a.tag_redraw()
    except Exception:
        pass
    return None


class AC9_OT_SimEditGuides(bpy.types.Operator):
    """Edit the placement guides by hand: each becomes a wire cylinder to move (G), turn (R) and scale (S, the radius;
    along its axis, the length). With Mirror on, a left / right pair follows as its mirror image. Done puts the
    placed shapes back on the new guides"""
    bl_idname = "ac9_cloth_sim.edit_guides"
    bl_label = "Edit Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('guides'))

    def execute(self, context):
        spec, _ = store.get(context)
        if editing():
            had, hidden = finish(context)
            from . import dress as D
            sp = store.get(context)[0]
            if had and D.has_result(bpy.data.objects.get(D.garment_name(sp))):
                # dressed with pieces put back by a Reset: those alone again, not every piece's over the garment
                # (Fable docs/reviews/REVIEW_fable_move_by_hand_unified_2026-10-22.md §1-e)
                try:
                    res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, placed_only=True)
                    PLV.build(context, sp, res['pat'], res['tris'], res['X'], res['pairs'], only=set(had))
                    D.sync_hidden(bpy.data.objects.get(D.garment_name(sp)), sp)
                except ValueError as exc:
                    self.report({'WARNING'}, str(exc))
            elif had and bpy.ops.ac9_cloth_sim.show_placed.poll():
                bpy.ops.ac9_cloth_sim.show_placed(placed_only=True)
                obs = PLV.objects(store.get(context)[0])
                for n in hidden:
                    if n in obs:
                        obs[n].hide_set(True)
            self.report({'INFO'}, rpt("Guides written") + (rpt("; placed shapes made again on them") if had else ""))
            return {'FINISHED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        made = start(context, spec)
        for o in list(context.view_layer.objects.selected):
            o.select_set(False)
        self.report({'INFO'}, rpt("{0} guides to move: G / R / S, then Done", len(made)))
        return {'FINISHED'}


CLASSES = (AC9_OT_SimEditGuides,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    if _mirror_moved not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_mirror_moved)


def unregister():
    for h in list(bpy.app.handlers.depsgraph_update_post):
        if getattr(h, '__name__', '') == '_mirror_moved' and getattr(h, '__module__', '') == __name__:
            bpy.app.handlers.depsgraph_update_post.remove(h)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
