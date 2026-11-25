# SPDX-License-Identifier: GPL-3.0-or-later
"""Move Piece: after a Dress, a piece is moved or turned on the garment itself (user 2026-10-14, DECISIONS
cloth-sim-one-mesh-after-dress-2026-10-14 b-A): a click on the garment with the Place or Edit Pieces tool picks the
piece (toolbar.garment_piece) and puts a handle (an Empty, collection HANDLE_COLL) at its center, selected. G / R /
the gizmo / typed numbers move the handle as any object, and the piece's part of the garment's mesh follows it
rigidly. Shift+click adds a piece to the handle.

The first move freezes the garment (dress.freeze: the result's last shape becomes its mesh, the frames off), as a
Reset does; Simulate goes on from the shape on screen. The move is a fix of the result: it is not written to the
placement (b-C), so a Reset still puts the piece back to its adjusted shape. The handle goes when it is no longer
selected, when another piece is picked, and before a Reset or a Simulate.
"""
import bpy
import numpy as np
from mathutils import Matrix

from . import pattern_view as PV
from . import store
from . import selection as SEL
from .core import piece_table as PT

HANDLE_COLL = "AC9_ClothSim_Move"
NAMES_PROP = PT.HANDLE_NAMES       # legacy value retained; PT.HANDLE_IDS is the explicit collection
_state = {}                        # handle name -> {'idx', 'base' (n, 3) m, 'M0' (4x4), 'last'}
_busy = [False]


class ControlSelectionBackup:
    """Restore control intent after geometry rollback, never transient move caches.

    Geometry restoration may replace Objects/Meshes. Project logical selection
    first, then restore pivots and bind fresh caches to the final live controls.
    """
    def __init__(self, context):
        from . import placed_view as PLV
        active = context.view_layer.objects.active
        self.active = active.name if active is not None else None
        self.selected = [ob.name for ob in SEL.objects(context)]
        self.names, self.lead = SEL.piece_selection(context)
        self.view = 'world' if active is not None and (NAMES_PROP in active or PLV.PROP in active) else 'pattern'
        self.controls = {frozenset(PT.handle_names(h)): h.matrix_world.copy() for h in handles()}
        self.active_control = next((frozenset(PT.handle_names(h)) for h in handles() if h == active), None)

    def restore(self, context):
        previous_busy = _busy[0]
        _busy[0] = True
        try:
            SEL.project(context, self.names, active=self.lead, view=self.view, others=True)
            current = handles()
            for name in self.selected:
                ob = context.scene.objects.get(name)
                if ob is not None and name in context.view_layer.objects:
                    ob.select_set(True)
            g = _garment(context)
            _state.clear()
            for h in current:
                names = tuple(PT.handle_names(h))
                if frozenset(names) in self.controls:
                    h.matrix_world = self.controls[frozenset(names)]
            # Blender normalizes a rotated Object's assigned matrix on update.
            # Read that final matrix while the handler is suppressed, otherwise
            # normalization alone can look like a new G and freeze/move a piece.
            context.view_layer.update()
            for h in current:
                names = tuple(PT.handle_names(h))
                if g is not None:
                    _state[h.name] = {'idx': piece_verts(g, names), 'base': None, 'M0': None,
                                      'last': tuple(map(tuple, h.matrix_world)), 'binding': _binding(context, g)}
            context.view_layer.objects.active = (
                next((h for h in current if frozenset(PT.handle_names(h)) == self.active_control), None)
                if self.active_control is not None else context.scene.objects.get(self.active) if self.active else None)
        finally:
            _busy[0] = previous_busy


def handles():
    coll = bpy.data.collections.get(HANDLE_COLL)
    return [ob for ob in coll.objects if NAMES_PROP in ob] if coll is not None else []


def _garment(context):
    from . import dress as D
    spec = store.get(context)[0]
    ob = D.garment(spec) if spec is not None else None
    return ob if ob is not None and ob.get('ac9_garment_spec', store.text(context)) == store.text(context) else None


def piece_verts(g, names):
    """The garment's vertex indices of pieces `names` (its face attribute ac9_piece and ac9_piece_names)."""
    import json
    me = g.data
    a = me.attributes.get('ac9_piece')
    try:
        pn = json.loads(g.get('ac9_piece_names', '[]'))
    except ValueError:
        pn = []
    ks = [pn.index(n) for n in names if n in pn]
    if a is None or not ks:
        return np.zeros(0, np.int64)
    fp = np.empty(len(me.polygons), np.int32)
    a.data.foreach_get('value', fp)
    ls = np.empty(len(me.polygons), np.int32)
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get('loop_start', ls)
    me.polygons.foreach_get('loop_total', lt)
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', lv)
    pick = np.isin(fp, ks)
    loops = np.concatenate([np.arange(s, s + t) for s, t in zip(ls[pick], lt[pick])]) if pick.any() else np.zeros(0, int)
    return np.unique(lv[loops]).astype(np.int64)


def _shown(context, g):
    """The garment's vertices as seen (m, world)."""
    # pieces Reset and hidden on the frozen garment (a Mask): its own vertices, indexed as the pieces are
    # (dress.garment_co: the solver's vertices whatever modifiers a person put on the garment)
    from . import dress as DR
    co = DR.garment_co(context, g).astype(np.float64)
    Mw = np.array(g.matrix_world)
    return co @ Mw[:3, :3].T + Mw[:3, 3]


def _remove_handles(obs):
    """Unselect before removing RNA so the layer does not retain a selected empty slot."""
    for ob in obs:
        for scene in ob.users_scene:
            for layer in scene.view_layers:
                if layer.objects.get(ob.name) == ob:
                    ob.select_set(False, view_layer=layer)
        _state.pop(ob.name, None)
        bpy.data.objects.remove(ob)
    coll = bpy.data.collections.get(HANDLE_COLL)
    if coll is not None and not coll.objects:
        bpy.data.collections.remove(coll)


def clear(context=None):
    """Remove the handles (the shapes they moved stay)."""
    _remove_handles(handles())


def rebase_handles():
    """After an external result edit, the next handle move starts from that edit."""
    for h in handles():
        state = _state.get(h.name)
        if state is not None:
            for key in ('full_base', 'targets', 'spec', 'placed', 'indices', 'cancel_raw', 'cancel_cache', 'cancel_frame', 'cancel_pose'):
                state.pop(key, None)
            state['base'] = None
            state['last'] = h.matrix_world.copy()


def _live():
    """A Phase Sim / Simulate is running: the garment is being written by it."""
    from . import dress as D
    return D.AC9_OT_SimDress.running() is not None


def pick(context, g, name, extend=False):
    """A click on the garment's piece `name`: a handle at the piece's center (Shift: the piece added to the handle).
    Not while a run goes on (its first move would freeze the garment the solver is writing, Fable review
    docs/reviews/REVIEW_fable_garment_piece_select_2026-10-22.md, user OK 2026-10-22)."""
    if _live():
        return None
    previous_busy = _busy[0]
    _busy[0] = True
    try:
        return _pick(context, g, name, extend)
    finally:
        _busy[0] = previous_busy


def _pick(context, g, name, extend):
    """The garment's piece `name` picked (Shift: added, or taken out again). The pick goes through the one
    selection (selection.apply), which marks the piece in every view and puts the handle on the garment's picked
    pieces. Shift keeps the pieces picked before, adjusted shapes too (user 2026-11-15, b-4: placed and garment pieces
    picked together; Blender's own G moved a placed piece and a garment piece together by the same 50 mm)."""
    if not len(piece_verts(g, [name])):
        return None
    if extend:
        on = name in SEL.ids(context)
        SEL.apply(context, [name], 'SUB' if on else 'ADD', active=None if on else name, view='world')
    else:
        SEL.apply(context, [name], 'SET', active=name, view='world')
    hs = handles()
    return hs[0] if hs else None


def ensure_handle(context, g, names):
    """The one handle on the garment's pieces `names` (the first named it): kept when it holds them already, else
    a new one prepared before the old ones retire (a preparation error leaves the current handle as it was). The
    handle is a way to move the pieces with G / R, not a record of the selection (selection.project makes it)."""
    names = list(names)
    from . import control_basis as CB
    if any(CB.basis(g, context).get(n) == CB.UNKNOWN for n in names):
        _basis_notice(context)
        _remove_handles(handles())
        return None
    old = handles()
    if len(old) == 1 and list(PT.handle_names(old[0])) == names:
        return old[0]
    idx = piece_verts(g, names)
    if not len(idx):
        return None
    plan = CB.transform_targets(context, names)
    pivots = [n for n, _lead, mirrored in plan if not mirrored and n in names]
    W = _shown(context, g)[piece_verts(g, pivots)]
    h = _new_handle(context, names, W)
    _remove_handles(old)
    _state[h.name] = {'idx': idx, 'base': None, 'M0': None, 'last': tuple(map(tuple, h.matrix_world)),
                      'binding': _binding(context, g)}
    return h


def _basis_notice(context):
    from .i18n import tx
    context.scene.ac9_cloth_sim.dress_status = tx('Use Back to Adjusted Shape or Back to Placed Shape before moving this saved shape')


def _binding(context, g):
    return (store.text(context), g.session_uid, g.data.session_uid)


def _new_handle(context, names, W):
    """Create a candidate; a preparation error leaves no candidate datablock behind."""
    coll = bpy.data.collections.get(HANDLE_COLL)
    created = coll is None
    h = None
    try:
        if created:
            coll = bpy.data.collections.new(HANDLE_COLL)
            context.scene.collection.children.link(coll)
            from . import view_setup
            view_setup.refresh(context)
        h = bpy.data.objects.new("AC9_Move_" + names[0] + ("" if len(names) == 1 else f"_+{len(names) - 1}"), None)
        h.empty_display_type = 'ARROWS'
        lo, hi = W.min(0), W.max(0)
        h.empty_display_size = float(max(0.03, 0.35 * np.linalg.norm(hi - lo)))
        h.show_in_front = True
        # Set the matrix, not only location: the first depsgraph read must see
        # this pivot rather than the identity (the original 1.5 m jump).
        h.matrix_world = Matrix.Translation(W.mean(0).tolist())
        h[NAMES_PROP] = ",".join(names)
        h[PT.HANDLE_IDS] = PT.encode_handle_names(names)
        h[PV.PIECE_PROP] = names[0]
        spec = store.get(context)[0]
        h['ac9_handle_garment'] = spec['name'] if spec is not None else ''
        text = store.text(context)
        if text is not None:
            h['ac9_handle_spec'] = text
        coll.objects.link(h)
    except Exception:
        if h is not None:
            bpy.data.objects.remove(h)
        if created and coll is not None and not coll.objects:
            bpy.data.collections.remove(coll)
        raise
    return h


def _apply(context, h, st):
    from . import dress as D
    g = _garment(context)
    if g is None or len(g.data.vertices) <= int(st['idx'].max(initial=-1)):
        return
    from . import control_basis as CB
    from .core import spec as S
    from mathutils import Euler
    if st.get('binding', _binding(context, g)) != _binding(context, g):
        raise ValueError('The control shape changed during the move')
    from . import store as ST
    if st.get('spec_generation', ST.GEN[0]) != ST.GEN[0]:
        for key in ('full_base', 'targets', 'spec', 'placed', 'indices', 'cancel_raw', 'cancel_cache', 'cancel_frame', 'cancel_pose'):
            st.pop(key, None)
        st['base'] = None
    names = list(PT.handle_names(h))
    bs = CB.basis(g, context)
    if any(bs.get(n, CB.UNKNOWN) == CB.UNKNOWN for n in names):
        raise ValueError('Use Back to Adjusted Shape or Back to Placed Shape before moving this saved shape')
    if st['base'] is None:
        # the first move: the garment frozen (its last shape its mesh), the piece's vertices taken from it
        raw = np.empty((len(g.data.vertices), 3))
        g.data.vertices.foreach_get('co', raw.ravel())
        st['cancel_raw'] = raw
        st['cancel_frame'] = ('ac9_from_frame' in g, g.get('ac9_from_frame'))
        st['cancel_cache'] = [(m, m.show_viewport, m.show_render) for m in g.modifiers if m.type == 'MESH_CACHE']
        st['cancel_pose'] = g.data.get(CB.POSES)
        D.freeze(context, g)
        co = np.empty(len(g.data.vertices) * 3, np.float64)
        g.data.vertices.foreach_get('co', co)
        st['base'] = co.reshape(-1, 3)[st['idx']].copy()
        st['M0'] = np.array(st['last'])
    co = np.empty(len(g.data.vertices) * 3, np.float64)
    g.data.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    if 'full_base' not in st:
        st['full_base'] = co.copy()
        st['full_base'][st['idx']] = st['base']
        current = store.get(context)[0]
        st['spec'] = CB.project_pose(context, current) or current
        st['spec_generation'] = ST.GEN[0]
        st['targets'] = CB.transform_targets(context, names, st['spec'])
        st['binding'] = _binding(context, g)
        ctxs = CB.contexts(context, st['spec'])
        st['placed'] = {n: (c['object'], c['object'].matrix_world.copy()) for n, _l, _m in st['targets']
                        if (c := ctxs[n])['storage'] == 'placed'}
        st['indices'] = {n: piece_verts(g, [n]) for n, _l, _m in st['targets'] if n not in st['placed']}
        all_ids = np.concatenate(list(st['indices'].values())) if st['indices'] else np.array([], int)
        if len(np.unique(all_ids)) != len(all_ids):
            raise ValueError('Control pieces share vertices')
    Mg = np.array(g.matrix_world)
    delta = np.array(h.matrix_world) @ np.linalg.inv(st['M0'])
    if np.allclose(delta, np.eye(4), atol=1e-8) and 'cancel_raw' in st:
        g.data.vertices.foreach_set('co', st['cancel_raw'].astype(np.float32).ravel())
        for m, viewport, render in st['cancel_cache']:
            m.show_viewport, m.show_render = viewport, render
        had, value = st['cancel_frame']
        if had:
            g['ac9_from_frame'] = value
        else:
            g.pop('ac9_from_frame', None)
        if st.get('cancel_pose') is None:
            g.data.pop(CB.POSES, None)
        else:
            g.data[CB.POSES] = st['cancel_pose']
        for ob, matrix in st.get('placed', {}).values():
            ob.matrix_world = matrix
        g.data.update()
        for key in ('full_base', 'targets', 'spec', 'placed', 'indices', 'cancel_raw', 'cancel_cache', 'cancel_frame', 'cancel_pose'):
            st.pop(key, None)
        st['base'] = None
        return
    spec = dict(st['spec'], placement={n: dict(p) for n, p in st['spec'].get('placement', {}).items()})
    placed_updates = []
    written = set()
    for n, lead, mirrored in st['targets']:
        T = delta
        if mirrored:
            axis = S.axis_of(S.pair_plane(spec, lead))
            H = np.eye(4); H[axis, axis] = -1
            H[axis, 3] = 2.0 * S.mirror_center(spec, S.pair_plane(spec, lead), lead) * .001
            T = H @ delta @ H
        if n in st['placed']:
            ob, M = st['placed'][n]
            placed_updates.append((ob, Matrix(T.tolist()) @ M))
            continue
        ids = st['indices'][n]
        D_ = np.linalg.inv(Mg) @ T @ Mg
        B = st['full_base'][ids]
        co[ids] = B @ D_[:3, :3].T + D_[:3, 3]
        if bs.get(n) == CB.PLACED and n in spec.get('placement', {}):
            # Compose in a fixed canonical pose frame, not around the current gizmo origin.
            p = spec['placement'][n]
            old = p.get('pose') or {'loc': [0, 0, 0], 'rot': [0, 0, 0]}
            R0 = np.array(Euler(np.radians(old['rot']).tolist(), 'XYZ').to_matrix())
            W = (np.c_[B, np.ones(len(B))] @ Mg.T)[:, :3] * 1000
            unrot = W @ R0
            org = R0 @ (.5 * (unrot.min(0) + unrot.max(0))) - np.array(old['loc'])
            R1 = T[:3, :3] @ R0
            loc = T[:3, :3] @ (org + np.array(old['loc'])) + T[:3, 3] * 1000 - org
            rot = np.degrees(np.array(Matrix(R1.tolist()).to_euler('XYZ')))
            p['pose'] = {'loc': np.round(loc, 4).tolist(), 'rot': np.round(rot, 5).tolist()}
    # Candidates are complete before either storage adapter writes anything.
    if not np.isfinite(co).all():
        raise ValueError('Invalid control transform')
    g.data.vertices.foreach_set('co', co.astype(np.float32).ravel())
    g.data.update()
    for ob, M in placed_updates:
        ob.matrix_world = M
    if any(bs.get(n) == CB.PLACED for n, _l, _m in st['targets']):
        import json
        values = json.loads(g.data.get(CB.POSES, '{}'))
        values.update({n: spec['placement'][n]['pose'] for n, _l, _m in st['targets']
                       if bs.get(n) == CB.PLACED and n in spec['placement']})
        g.data[CB.POSES] = json.dumps(values, separators=(',', ':'))


@bpy.app.handlers.persistent         # kept when a file is opened (a plain handler is dropped then, 2026-10-14)
def _moved(scene, depsgraph=None):
    """depsgraph handler: a handle moved -> its pieces follow; a handle no longer selected -> removed (a timer)."""
    if _busy[0]:
        return
    hs = handles()
    if not hs:
        return
    _busy[0] = True
    try:
        ctx = bpy.context
        gone = False
        for h in hs:
            st = _state.get(h.name)
            if st is None:                         # a file reopened, an undo: the handle starts again from here
                g = _garment(ctx)
                if g is None:
                    continue
                names = PT.handle_names(h)
                st = _state[h.name] = {'idx': piece_verts(g, names), 'base': None, 'M0': None,
                                       'last': tuple(map(tuple, h.matrix_world)), 'binding': _binding(ctx, g)}
            m = tuple(map(tuple, h.matrix_world))
            if m != st['last']:
                if not _live():                    # during a run a handle's move is dropped (the solver writes the
                    try:
                        _apply(ctx, h, st)         # the shape and basis are validated before writing
                    except ValueError:
                        h.matrix_world = Matrix(st['last'])
                        _basis_notice(ctx)
                st['last'] = m
            try:
                if not h.select_get():
                    gone = True
            except RuntimeError:
                pass
        if gone and not bpy.app.timers.is_registered(_drop_unselected):
            bpy.app.timers.register(_drop_unselected, first_interval=0.05)
    finally:
        _busy[0] = False


def _drop_unselected():
    gone = []
    for h in handles():
        try:
            sel = h.select_get()
        except RuntimeError:
            sel = True
        if not sel:
            gone.append(h)
    _remove_handles(gone)
    return None


@bpy.app.handlers.persistent
def _loaded(*_a):
    _state.clear()                 # another file: its handles start again from where they are (_moved)
    from . import control_basis as CB
    CB.invalidate()


@bpy.app.handlers.persistent
def _save_basis(*_a):
    from . import control_basis as CB, dress as D
    ctx = bpy.context
    spec = store.get(ctx)[0]
    if spec is None:
        return
    projected = CB.project_pose(ctx, spec, consume=True)
    if projected is not None:
        store.put(ctx, projected)
    run = getattr(D.AC9_OT_SimDress, '_active_instance', None) if D.AC9_OT_SimDress.running() else None
    if run is not None and getattr(run, '_displayed_output', False):
        # Saving is an explicit adoption boundary for the validated frame actually on screen, not discovery of a
        # live.npy file. The executing owner must still own this exact shape generation.
        g = run._garment(ctx)
        CB.adopt(ctx, g, getattr(run, 'local', None))


def register():
    if _moved not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_moved)
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)
    for hs in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _loaded not in hs:
            hs.append(_loaded)
    if _save_basis not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_save_basis)


def unregister():
    for hl, nm in ((bpy.app.handlers.depsgraph_update_post, '_moved'), (bpy.app.handlers.load_post, '_loaded'),
                   (bpy.app.handlers.undo_post, '_loaded'), (bpy.app.handlers.redo_post, '_loaded')):
        for h in list(hl):
            if getattr(h, '__name__', '') == nm and getattr(h, '__module__', '') == __name__:
                hl.remove(h)
    for h in list(bpy.app.handlers.save_pre):
        if getattr(h, '__name__', '') == '_save_basis' and getattr(h, '__module__', '') == __name__:
            bpy.app.handlers.save_pre.remove(h)
    if bpy.app.timers.is_registered(_drop_unselected):
        bpy.app.timers.unregister(_drop_unselected)
