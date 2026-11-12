# SPDX-License-Identifier: GPL-3.0-or-later
"""Move Piece: after a Dress, a piece is moved or turned on the garment itself (user 2026-10-14, DECISIONS
cloth-sim-one-mesh-after-dress-2026-10-14 b-A): a click on the garment with the Place or Edit Pieces tool picks the
piece (toolbar.garment_piece) and puts a handle (an Empty, collection HANDLE_COLL) at its center, selected. G / R /
the gizmo / typed numbers move the handle as any object, and the piece's part of the garment's mesh follows it
rigidly. Shift+click adds a piece to the handle.

The first move freezes the garment (dress.freeze: the result's last shape becomes its mesh, the frames off), as a
Reset does; Simulate goes on from the shape on screen. The move is a fix of the result: it is not written to the
placement (b-C), so a Reset still puts the piece back to its placed shape. The handle goes when it is no longer
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


def handles():
    coll = bpy.data.collections.get(HANDLE_COLL)
    return [ob for ob in coll.objects if NAMES_PROP in ob] if coll is not None else []


def _garment(context):
    from . import dress as D
    spec = store.get(context)[0]
    return bpy.data.objects.get(D.garment_name(spec)) if spec is not None else None


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
            state['base'] = None
            state['last'] = h.matrix_world.copy()


def _live():
    """A Phase Sim / Live Sim is running: the garment is being written by it."""
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
    """Prepare the next handle before retiring the current one."""
    old = handles()
    names = []
    if extend and old:
        names = list(PT.handle_names(old[0]))
        if name in names:
            names.remove(name)
        else:
            names.append(name)
    else:
        names = [name]
    if not names:
        clear(context)
        return None
    idx = piece_verts(g, names)
    if not len(idx):
        return None
    W = _shown(context, g)[idx]
    h = _new_handle(context, names, W)
    # All fallible shape reads and handle preparation completed. Retire only
    # the old handles, never the replacement just linked into the collection.
    SEL.deselect_all(context)
    _remove_handles(old)
    h.select_set(True)
    context.view_layer.objects.active = h
    _state[h.name] = {'idx': idx, 'base': None, 'M0': None, 'last': tuple(map(tuple, h.matrix_world))}
    return h


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
    if st['base'] is None:
        # the first move: the garment frozen (its last shape its mesh), the piece's vertices taken from it
        D.freeze(context, g)
        co = np.empty(len(g.data.vertices) * 3, np.float64)
        g.data.vertices.foreach_get('co', co)
        st['base'] = co.reshape(-1, 3)[st['idx']].copy()
        st['M0'] = np.array(st['last'])
    Mg = np.array(g.matrix_world)
    D_ = np.linalg.inv(Mg) @ np.array(h.matrix_world) @ np.linalg.inv(st['M0']) @ Mg   # in the garment's space
    co = np.empty(len(g.data.vertices) * 3, np.float64)
    g.data.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    B = st['base']
    co[st['idx']] = B @ D_[:3, :3].T + D_[:3, 3]
    g.data.vertices.foreach_set('co', co.astype(np.float32).ravel())
    g.data.update()


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
                                       'last': tuple(map(tuple, h.matrix_world))}
            m = tuple(map(tuple, h.matrix_world))
            if m != st['last']:
                if not _live():                    # during a run a handle's move is dropped (the solver writes the
                    _apply(ctx, h, st)             # garment), its new place taken as the start of the next move
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


def register():
    if _moved not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_moved)
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)


def unregister():
    for hl, nm in ((bpy.app.handlers.depsgraph_update_post, '_moved'), (bpy.app.handlers.load_post, '_loaded')):
        for h in list(hl):
            if getattr(h, '__name__', '') == nm and getattr(h, '__module__', '') == __name__:
                hl.remove(h)
    if bpy.app.timers.is_registered(_drop_unselected):
        bpy.app.timers.unregister(_drop_unselected)
