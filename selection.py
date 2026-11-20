"""Which pieces are selected: read and written here only (user 2026-11-03: the add-on keeps the piece selection in
one place, picked in 2D or 3D it is the selection everywhere).

The record is Blender's own selection flag of the AC9 objects, read over the whole view layer
(view_layer.objects.selected), never through one viewport (context.selected_objects lists only the objects that
viewport shows: the 2D pattern view and the 3D view show different collections, so a piece picked in 2D was not
selected for a Place or Part to Seam run from the 3D view). Blender's flag stays the record (not a second copy in the
scene): Undo, the Outliner, G / R and box select keep working on it as they do, and nothing has to be kept in step.
What changes is who clears it: deselect_all clears every object of the view layer, also those the viewport does not
show, and a replacing click or box select of Blender's own in one view drops the AC9 objects of the other view
(watch). The pieces themselves are named by core.piece_table from these facts."""
import bpy
from bpy.app.handlers import persistent

from . import pattern_view as PV
from . import placed_view as PLV
from .core import piece_table as PT

HANDLE_NAMES = PT.HANDLE_NAMES     # piece_move.NAMES_PROP: a handle picked on the dressed garment


def _fact(ob, active):
    if HANDLE_NAMES in ob:
        return PT.ObjFact('handle', PT.handle_names(ob), True, ob == active)
    if PLV.PROP in ob:
        return PT.ObjFact('placed', tuple(n for n in (ob.get(PV.PIECE_PROP), ob.get(PLV.PROP), ob.get(PLV.TWIN_PROP))
                                          if n), True, ob == active)
    if PV.PIECE_PROP in ob:
        return PT.ObjFact('pattern', (ob[PV.PIECE_PROP],), True, ob == active)
    return None


def objects(context):
    """The selected objects of the whole view layer (not only those a viewport shows), in the view layer's order."""
    vl = context.view_layer
    # Blender can expose an empty slot until the depsgraph catches up with a
    # removed selected object. Keep that lifetime detail out of every caller.
    return [ob for ob in vl.objects.selected if ob is not None] if vl is not None else []


def facts(context):
    """ObjFacts of the selected AC9 piece objects (pattern pieces, placed objects, handles)."""
    vl = context.view_layer
    if vl is None:
        return []
    active = vl.objects.active
    return [f for f in (_fact(ob, active) for ob in objects(context)) if f is not None]


def pieces(context):
    """Selected piece names, the active object's first (a pair's placed object gives both)."""
    return PT.selected_names(facts(context))


def pattern_pieces(context):
    """Names of the selected flat pattern pieces (not placed objects, not handles), in the view layer's order: the
    2D drafting tools work on these."""
    return list(dict.fromkeys(f.names[0] for f in facts(context) if f.kind == 'pattern'))


def lead(context, names=None):
    """The piece the Tool tab shows and edits (core.piece_table.lead_name): a pattern piece, a placed piece's object
    or a handle on the dressed garment alike."""
    return PT.lead_name(facts(context), names)


def any_pieces(context):
    """True when a pattern piece or a placed piece's object is selected (Place has something to place)."""
    return any(f.kind in ('pattern', 'placed') for f in facts(context))


def deselect_all(context, keep=()):
    """Deselect every object of the view layer, also those the viewport does not show (a replacing click in one view
    left the other view's pieces selected). keep: objects left as they are (a kept piece object keeps its pieces
    picked in every view)."""
    keep = [o for o in keep if o is not None and o.select_get()]
    project(context, [n for o in keep for n in _names(o)], others=True)
    for o in keep:
        if _kind(o) is None:
            _set(o, True)
    _remember(context)
    _last['snap'] = _snapshot(context)


# ---------------------------------------------------------------- Blender's own select tools
_seen = {'sig': None}


def _kind(ob):
    return 'world' if (PLV.PROP in ob or HANDLE_NAMES in ob) else 'pattern' if PV.PIECE_PROP in ob else None


# Blender's own select tools in the 3D views, with the properties that make them add instead of replace. They do not
# pick pieces, they only drop them (user 2026-11-17): pieces are picked with the AC9 tools, which also find a dressed
# piece. Other objects (a collision object, the body) are picked as ever. Before, a box in the pattern view while a
# collision object was active picked no piece at all, and a click on the dressed garment picked it whole while a
# placed piece was picked alone: one picks pieces, the other objects.
_NATIVE = {'VIEW3D_OT_select': ('extend', 'toggle', 'deselect'), 'VIEW3D_OT_select_box': ('mode',),
           'VIEW3D_OT_select_lasso': ('mode',), 'VIEW3D_OT_select_circle': ('mode',)}
# (the Outliner still picks pieces: a click on a piece's object there picks that piece, user 2026-11-17)


def _native(wm):
    """(native, replacing): the last operator run was one of Blender's own select tools, not seen before."""
    try:
        op = wm.operators[-1]
    except (IndexError, AttributeError):
        return False, False
    keys = _NATIVE.get(op.bl_idname)
    if keys is None or op.as_pointer() == _seen.get('op'):
        return False, False
    _seen['op'] = op.as_pointer()
    p = op.properties
    if keys == ('mode',):
        return True, getattr(p, 'mode', 'SET') == 'SET'
    return True, not any(getattr(p, k, False) for k in keys)


def drop_other_view(context):
    """After a replacing select in one view: the AC9 objects of the other kind (2D pattern vs 3D placed / handle)
    still selected are what that view could not deselect; deselect them. Returns the names dropped."""
    vl = context.view_layer
    act = vl.objects.active if vl is not None else None
    k = _kind(act) if act is not None and act.select_get() else None
    if k is None:
        return []
    out = []
    for ob in objects(context):
        if _kind(ob) not in (None, k):
            ob.select_set(False)
            out.append(ob.name)
    return out


# ---------------------------------------------------------------- one selection, shown everywhere
# The picked pieces are the selected pattern objects: every piece has exactly one, Undo and saving keep their flags
# as Blender keeps any selection. The other objects are marks of that record (user 2026-11-14: a picked piece
# carries the same mark in every view it shows in, and only marked pieces are acted on): project() writes them all,
# apply() is the one way a tool changes the selection, and watch() turns a change Blender made itself (a click with
# its own select tools, the Outliner, A / Alt+A) into an apply. The 2026-11-04 reading kept each kind's flag as its
# own record, so a pick in 3D left the pattern unmarked and the pattern tools blind to it, and the garment's handle
# held the only copy of its pieces.
_last = {'snap': None, 'ids': []}
_projecting = [False]
_state_owner = [None]


def _ensure_owner(context):
    from . import store
    text = store.text(context)
    key = (context.scene.session_uid, context.view_layer.as_pointer(), text.session_uid if text else 0)
    if key != _state_owner[0]:
        _state_owner[0] = key
        _last.update(snap=None, ids=[])
        _seen.update(sig=None, op=None, objs=None)
        return True
    return False


def _names(ob):
    f = _fact(ob, None)
    return [n for n in f.names if n] if f is not None else []


def _snapshot(context):
    """{object pointer: (kind, piece names)} of the selected AC9 objects."""
    out = {}
    for ob in objects(context):
        k = _kind(ob)
        if k is not None:
            out[ob.as_pointer()] = (k, tuple(_names(ob)))
    return out


def ids(context):
    """The picked pieces, the active object's first."""
    return pieces(context)


def _set(ob, on):
    try:
        if ob.select_get() != on:
            ob.select_set(on)
    except RuntimeError:                            # not in this view layer (an excluded collection)
        pass


def _worn(context, spec):
    """Pieces shown on the dressed garment (not put back to their placed shapes), and the garment."""
    from . import piece_move as PM
    g = PM._garment(context)
    if g is None or spec is None:
        return set(), None
    import json
    try:
        have = set(json.loads(g.get('ac9_piece_names', '[]')))
    except ValueError:
        have = set()
    if not have:
        return set(), g
    from . import dress as D
    return have - set(D.hidden_pieces(g, spec)), g


def project(context, names, active=None, view=None, others=False):
    """Mark pieces `names` (and no other) in every view: their pattern objects, their placed shapes (one object for
    each pair), the dressed garment's handle. active: the piece made active (as an object of `view`, 'pattern' or
    'world'; None keeps the active object). others: objects that are not pieces' are deselected too."""
    from . import store
    from . import piece_move as PM
    _ensure_owner(context)
    names = list(dict.fromkeys(n for n in names if n))
    want = set(names)
    vl = context.view_layer
    spec = store.get(context)[0]
    _projecting[0] = True
    try:
        pats = PV.piece_objects()
        for n, ob in pats.items():
            _set(ob, n in want)
        worn, g = _worn(context, spec)
        placed = PLV.objects(spec) if spec is not None else {}
        world = {}
        for ob in {o.as_pointer(): o for o in placed.values()}.values():
            own = [n for n in (ob.get(PLV.PROP), ob.get(PLV.TWIN_PROP)) if n and n not in worn]
            on = any(n in want for n in own)
            if on and ob.get(LEAD_PROP):            # a pair's following side: its leader carries it
                c = ob.constraints.get('AC9_Pair')
                lead = c.target if c is not None else None
                if lead is not None and any(n in want for n in _names(lead)):
                    on = False
            _set(ob, on)
            if on:
                for n in own:
                    world.setdefault(n, ob)
        picked_worn = [n for n in names if n in worn]
        h = None
        if picked_worn:
            if active in picked_worn:
                picked_worn.remove(active)
                picked_worn.insert(0, active)
            h = PM.ensure_handle(context, g, picked_worn)
        if h is None and PM.handles():
            PM.clear(context)
        if h is not None:
            _set(h, True)
            for n in picked_worn:
                world[n] = h
        if others:
            for ob in objects(context):
                if _kind(ob) is None:
                    _set(ob, False)
        if active is not None and active in want:
            ob = world.get(active) if view == 'world' else None
            vl.objects.active = ob or pats.get(active) or world.get(active)
    finally:
        _projecting[0] = False
    _remember(context)
    _last.update(snap=_snapshot(context), ids=names)
    return names


def marks(context):
    """The pieces marked as picked, per view, as they are drawn: the pattern objects (Blender's outline in the 2D
    view), the placed shapes (Blender's outline in 3D), the dressed garment (piece_look's tint and rim)."""
    from . import piece_look as PK
    pattern, placed = set(), set()
    for ob in objects(context):
        k = _kind(ob)
        if k == 'pattern':
            pattern.update(_names(ob))
        elif k == 'world' and HANDLE_NAMES not in ob:
            placed.update(_names(ob))
    return dict(pattern=pattern, placed=placed, garment=set(PK.garment_marked(context)))


def apply(context, names, mode='SET', active=None, view='pattern', others=None, interaction=False):
    """The one way a tool changes the picked pieces. mode: SET / ADD / SUB. active: the piece made active, or
    'KEEP' (the active object's piece while it stays picked, else the first of names). others: deselect the objects
    that are not pieces' (default: with SET). interaction publishes explicit selection input;
    internal projections leave details alone."""
    cur = ids(context)
    names = [n for n in dict.fromkeys(names) if n]
    if mode == 'SET':
        new = names
    elif mode == 'ADD':
        new = cur + [n for n in names if n not in cur]
    else:
        new = [n for n in cur if n not in names]
    if active == 'KEEP':
        act = context.view_layer.objects.active
        own = _names(act) if act is not None else []
        active = own[0] if own and own[0] in new else (names[0] if names and mode != 'SUB' else None)
        if view is None and act is not None:
            view = 'world' if _kind(act) == 'world' else 'pattern'
    if mode == 'SUB' and active not in new:
        active = new[0] if new else None
    out = project(context, new, active=active, view=view, others=(mode == 'SET') if others is None else others)
    to_leader(context)
    if interaction:
        from . import tool_tab as TT
        TT.piece_clicked(context)
    return out


def refresh(context, active=None, view=None):
    """The marks written again from the picked pieces (after placed objects or the garment were made again)."""
    return project(context, ids(context), active=active, view=view)


class borrow:
    """with borrow(context, objects): only `objects` selected for an operator that reads the selection (View
    Selected), then the selection exactly as it was; not a change of the picked pieces."""

    def __init__(self, context, obs):
        self.context, self.obs = context, list(obs)

    def __enter__(self):
        vl = self.context.view_layer
        self.sel, self.act = objects(self.context), vl.objects.active
        _projecting[0] = True
        for o in vl.objects:
            _set(o, o in self.obs)
        return self

    def __exit__(self, *exc):
        vl = self.context.view_layer
        try:
            for o in vl.objects:
                _set(o, o in self.sel)
            vl.objects.active = self.act
        finally:
            _projecting[0] = False
            _remember(self.context)
        return False


@persistent
def watch(scene, depsgraph):
    try:
        ctx = bpy.context
        vl = ctx.view_layer
    except AttributeError:
        return
    if vl is None or _projecting[0]:
        return
    if scene != ctx.scene or (depsgraph is not None and depsgraph.view_layer.name != vl.name):
        return
    first = _ensure_owner(ctx)
    sig = _signature(ctx)
    if sig == _seen['sig']:
        return
    before = _seen.get('objs')
    _seen['sig'] = sig
    native, replaced = _native(ctx.window_manager)
    from . import tool_tab as TT
    fresh = TT.selection_changed(ctx)
    if native and before is not None and _ac9_tool(ctx):
        # An AC9 tool with Blender's move / rotate gizmo (Whole Pieces): a click off the gizmo also runs Blender's
        # own select as the tool's fallback, after the AC9 tool handled it (picked, or Shift took a piece out). What
        # the AC9 tool left is the selection.
        _put_back(ctx, before)
        return
    changed = not first and _last['snap'] is not None and fresh
    _seen['objs'] = ([o.name for o in objects(ctx)], vl.objects.active.name if vl.objects.active else None)
    snap = _snapshot(ctx)
    prev = _last['snap']
    act = vl.objects.active
    act_kind = _kind(act) if act is not None and act.select_get() else None
    if prev is None:                                # a file opened, an undo: the record is the pattern objects
        names = PT.selected_names([f for f in facts(ctx) if f.kind == 'pattern']) or pieces(ctx)
    elif native:
        # Blender's own select tool: it does not pick pieces, it only drops them (a replacing select drops all, an
        # extending one drops what it deselected)
        gone = [n for p, (k, ns) in prev.items() if p not in snap for n in ns]
        names = [] if replaced else [n for n in _last['ids'] if n not in gone]
        act_kind = None
    else:
        gone = [n for p, (k, ns) in prev.items() if p not in snap for n in ns]
        came = [n for p, (k, ns) in snap.items() if p not in prev for n in ns]
        base = _last['ids']
        names = [n for n in base if n not in gone or n in came] + [n for n in came if n not in base]
    lead_piece = _names(act)[0] if act_kind is not None and _names(act) else None
    project(ctx, names, active=lead_piece, view='world' if act_kind == 'world' else 'pattern')
    if replaced and fresh and not names:
        _drop_seam(ctx)                             # a click on nothing in any view: the picked seam goes too
    to_leader(ctx)
    if changed:
        TT.native_selection(ctx)


def _drop_seam(context):
    """No seam picked (user 2026-11-13: a click on nothing in any tool drops it; the picked seam's lengths and A / B
    stayed drawn after the seam tool was left, with no way to take them away)."""
    try:
        from . import seam_edit_tool as SET
        SET.unselect_seam(context)
    except Exception:                               # noqa: BLE001 -- a selection watcher must not fail
        pass


LEAD_PROP = "ac9_placed_lead"       # placed_view.LEAD_PROP (not imported: this module loads before it)


def select_placed(context, spec, names, side=None, keep=(), activate=True):
    """The one way pieces' placed objects are selected (Place, a click on a pair's following side).

    One object for each pair, so Blender draws one gizmo, in that piece (user 2026-10-29: the gizmo is on the side
    picked; 2026-11-04: two objects selected put one gizmo between the sleeves). A pair shown as two objects: the side
    picked (side, else the one named that leads already) is made the one moved and its partner follows it mirrored;
    the partner is not selected. A mirror-meshed pair is one object already. keep: objects left selected (the 2D
    pattern pieces). Returns the objects selected, the active first."""
    from .core import spec as S
    obs = PLV.objects(spec)
    partner = {}
    for a, b in S.placement_pairs(spec):
        partner[a], partner[b] = b, a
    names = [n for n in dict.fromkeys(([side] if side else []) + list(names)) if n in obs]
    if not names:
        return []
    done, picked = set(), []
    for n in names:
        if n in done:
            continue
        p = partner.get(n)
        done |= {n, p}
        ob = obs[n]
        if ob.get(LEAD_PROP) and not (n != side and p in names and obs.get(p) is not None
                                      and not obs[p].get(LEAD_PROP)):
            ob = PLV.make_lead(context, spec, n)        # the following side picked: it leads now
        elif ob.get(LEAD_PROP):
            ob = obs[p]
        if ob is not None and ob not in picked:
            picked.append(ob)
    keep = [o for o in keep if _alive(o) and not o.get(LEAD_PROP) and o.select_get()]
    want = [n for o in picked for n in _names(o)]
    want += [n for o in keep for n in _names(o) if n not in want]
    first = _names(picked[0]) if picked and activate else []
    project(context, want, active=first[0] if first else None, view='world', others=True)
    for o in keep:                                  # objects that are not pieces', kept as they were
        if _alive(o) and _kind(o) is None:
            _set(o, True)
    _remember(context)
    _last['snap'] = _snapshot(context)
    return picked


def _alive(ob):
    """False for an object removed since it was listed (a piece placed again off the dressed garment: its handle and
    shapes are made again while the list is held; GUI probe 2026-11-17)."""
    try:
        ob.name
        return True
    except ReferenceError:
        return False


def to_leader(context):
    """Blender's own select (a click, a box) took a pair's following side. With its leader selected too: the
    follower is deselected (one gizmo, not one between the two). Alone: it is made the side moved (a data change, so
    deferred out of the depsgraph handler). Returns the follower handled, or None."""
    vl = context.view_layer
    fols = [o for o in objects(context) if o.get(LEAD_PROP)]
    if not fols:
        return None
    later = []
    for fol in fols:
        c = fol.constraints.get('AC9_Pair')
        lead = c.target if c is not None else None
        if lead is not None and lead.select_get():
            fol.select_set(False)
            if vl.objects.active is fol:
                vl.objects.active = lead
        else:
            later.append((fol.name, fol.session_uid))
    _remember(context)
    if later:
        _pending[:] = later
        _pending_owner[:] = _owner(context)
        if not bpy.app.timers.is_registered(_lead_clicked):
            bpy.app.timers.register(_lead_clicked, first_interval=0.0)
    return fols[0]


_pending = []
_pending_owner = []


def _remember(context):
    """The selection as the add-on left it: its signature (watch skips it) and the objects (put back when Blender's
    own select runs as an AC9 tool's fallback)."""
    _seen['sig'] = _signature(context)
    vl = context.view_layer
    act = vl.objects.active
    _seen['objs'] = ([o.name for o in objects(context)], act.name if act is not None else None)
    from . import tool_tab as TT
    TT.acknowledge_selection(context)


def _ac9_tool(context):
    try:
        from . import toolbar as TB
        return (TB.active_tool(context) or '').startswith('ac9_cloth_sim.')
    except Exception:                               # noqa: BLE001 -- a selection watcher must not fail
        return False


def _put_back(context, objs):
    """The selection made exactly `objs` (names, active name) again."""
    sel, act = objs
    vl = context.view_layer
    _projecting[0] = True
    try:
        keep = set(sel)
        for o in vl.objects:
            _set(o, o.name in keep)
        a = vl.objects.get(act) if act else None
        vl.objects.active = a
    finally:
        _projecting[0] = False
    _remember(context)


def _signature(context):
    vl = context.view_layer
    act = vl.objects.active
    return (context.scene.as_pointer(), vl.as_pointer(), act.as_pointer() if act is not None else 0,
            tuple(o.as_pointer() for o in objects(context)))


def _owner(context):
    from . import store
    text = store.text(context)
    return [context.scene.as_pointer(), context.view_layer.as_pointer(), text.as_pointer() if text else 0]


@persistent
def _cancel_pending(*_args):
    _pending.clear()
    _pending_owner.clear()
    _seen.update(sig=None, op=None)
    _last.update(snap=None, ids=[])
    _state_owner[0] = None
    if bpy.app.timers.is_registered(_lead_clicked):
        bpy.app.timers.unregister(_lead_clicked)


def _lead_clicked():
    from . import store
    ctx = bpy.context
    names, _pending[:] = list(_pending), []
    owner, _pending_owner[:] = list(_pending_owner), []
    if owner != _owner(ctx):
        return None
    spec = store.get(ctx)[0]
    if spec is None:
        return None
    obs = PLV.objects(spec)
    # Pending entries identify the original Blender object; objects(spec) is
    # keyed by piece ID. A same-name replacement is a different operation.
    current = {ob.name: (n, ob) for n, ob in obs.items() if ob.get(LEAD_PROP)}
    fols = [current[n][0] for n, uid in names if n in current and current[n][1].session_uid == uid
            and current[n][1].select_get()]
    if fols:
        keep = [o for o in objects(ctx) if not o.get(LEAD_PROP)]
        select_placed(ctx, spec, fols, side=fols[0], keep=keep)
    return None


def register():
    _cancel_pending()
    if watch not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(watch)
    for name in ('load_pre', 'undo_post', 'redo_post'):
        handlers = getattr(bpy.app.handlers, name)
        if _cancel_pending not in handlers:
            handlers.append(_cancel_pending)


def unregister():
    _cancel_pending()
    if watch in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(watch)
    for name in ('load_pre', 'undo_post', 'redo_post'):
        handlers = getattr(bpy.app.handlers, name)
        if _cancel_pending in handlers:
            handlers.remove(_cancel_pending)
