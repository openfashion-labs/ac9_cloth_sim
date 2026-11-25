"""Which pieces are selected: read and written here only (user 2026-11-03: the add-on keeps the piece selection in
one place, picked in 2D or 3D it is the selection everywhere).

The saved record on the ViewLayer contains document-qualified logical piece IDs and their representative.
Pattern flags, placed objects (including shared Mirror objects) and garment handles project that record.
The record survives an excluded pattern collection and does not expand one logical half into both halves.
Old files bootstrap from their flags. Blender/Outliner changes pass through watch before becoming logical input;
internal projection and redraw are never new input. Undo and file loading restore the same saved record."""
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


def owns(context, ob):
    """Validate an input object's document before interpreting its logical IDs."""
    from . import store, piece_move as PM
    spec = store.get(context)[0]
    if spec is None or ob is None:
        return False
    if PLV.PROP in ob:
        from . import garment_owner as GO
        return GO.owns(ob, GO.PLACED, spec, context=context, legacy_name=ob.get(PLV.GARMENT_PROP))
    if HANDLE_NAMES in ob:
        return (ob in PM.handles() and ob.get('ac9_handle_garment', spec['name']) == spec['name']
                and ob.get('ac9_handle_spec', store.text(context)) == store.text(context))
    return (PV.PIECE_PROP in ob and PV.piece_objects().get(ob[PV.PIECE_PROP]) == ob
            and ob.get('ac9_pattern_spec', store.text(context)) == store.text(context))


def facts(context):
    """ObjFacts of the selected AC9 piece objects (pattern pieces, placed objects, handles)."""
    vl = context.view_layer
    if vl is None:
        return []
    active = vl.objects.active
    from . import store, piece_move as PM
    spec = store.get(context)[0]
    if spec is None:
        return []
    valid = {pc['name'] for pc in spec['pieces']}
    out = []
    for ob in objects(context):
        if not owns(context, ob):
            continue
        f = _fact(ob, active)
        if f is not None:
            ns = tuple(n for n in f.names if n in valid)
            if ns:
                out.append(f._replace(names=ns))
    return out


def _logical_record(context):
    """A saved, owner-qualified record also works when the pattern collection is excluded."""
    import json
    from . import store
    raw = context.view_layer.get('ac9_piece_selection')
    if raw is None:
        return {} if context.view_layer.get('ac9_piece_selection_owner') is not None else None
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(record, dict) or not isinstance(record.get('ids'), list):
        return {}
    if any(not isinstance(n, str) or not n for n in record['ids']):
        return {}
    text = store.text(context)
    if text is None or context.view_layer.get('ac9_piece_selection_owner') != text:
        return {}
    return record


def view_layer_key(context, create=False):
    """Saved identity survives rename and undo; copied layers receive a distinct identity."""
    layer = context.view_layer
    key = layer.get('ac9_selection_layer_uid')
    for other in context.scene.view_layers:
        if other == layer:
            break
        if key and other.get('ac9_selection_layer_uid') == key:
            key = None
            break
    if key:
        return key
    if create:
        import uuid
        key = uuid.uuid4().hex
        layer['ac9_selection_layer_uid'] = key
        return key
    return layer.as_pointer()


def _save_logical(context, names, active):
    import json
    from . import store
    text = store.text(context)
    if text is None:
        return
    if context.view_layer.get('ac9_piece_selection_owner') != text:
        context.view_layer['ac9_piece_selection_owner'] = text
    spec = store.get(context)[0]
    record = json.dumps(dict(ids=names, garment=spec['name'] if spec is not None else '',
                             lead=active if active in names else (names[0] if names else None)))
    if context.view_layer.get('ac9_piece_selection') != record:
        context.view_layer['ac9_piece_selection'] = record


def _logical_facts(context):
    """Logical IDs are the record. Object flags are its projection or an external input."""
    raw = facts(context)
    record = _logical_record(context)
    if record is None:
        # Bootstrap old files from their saved flags, once, before the first projection.
        picked = {f.names[0] for f in raw if f.kind == 'pattern'}
        if not PV.piece_objects():
            return raw
    else:
        picked = set(record.get('ids', ()))
    out = [f._replace(names=tuple(n for n in f.names if n in picked), active=False)
           for f in raw if any(n in picked for n in f.names)]
    represented = {n for f in out for n in f.names}
    out += [PT.ObjFact('pattern', (n,), True, False) for n in sorted(picked - represented)]
    return out


def pieces(context):
    """Selected piece names, the active object's first (a pair's placed object gives both)."""
    record = _logical_record(context)
    if record is not None:
        from . import store
        spec = store.get(context)[0]
        valid = {pc['name'] for pc in spec['pieces']} if spec is not None else set()
        return [n for n in record.get('ids', ()) if n in valid]
    return PT.selected_names(_logical_facts(context))


def pattern_pieces(context):
    """Names of the selected flat pattern pieces (not placed objects, not handles), in the view layer's order: the
    2D drafting tools work on these."""
    return list(dict.fromkeys(f.names[0] for f in facts(context) if f.kind == 'pattern'))


def lead(context, names=None):
    """The piece the Tool tab shows and edits (core.piece_table.lead_name): a pattern piece, a placed piece's object
    or a handle on the dressed garment alike."""
    record = _logical_record(context)
    if record is not None:
        picked = set(pieces(context))
        if names is not None:
            picked &= set(names)
        preferred = record.get('lead')
        return preferred if preferred in picked else next(iter(sorted(picked)), None)
    fs = _logical_facts(context)
    return PT.lead_name(fs, names)


def any_pieces(context):
    """True when a pattern piece or a placed piece's object is selected (Place has something to place)."""
    return any(f.kind in ('pattern', 'placed') for f in _logical_facts(context))


def piece_selection(context, spec=None):
    """The same validated logical set and representative for panels and property writes."""
    if spec is None:
        from . import store
        spec = store.get(context)[0]
    valid = {pc['name'] for pc in spec['pieces']} if spec is not None else set()
    names = tuple(n for n in pieces(context) if n in valid)
    return names, lead(context, set(names)) if names else None


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
# apply/project commit one saved logical selection; every available representation shows it.
# _last is only the previous external-input snapshot for watch, never the authoritative selected set.
_last = {'snap': None, 'ids': [], 'lead': None}
_projecting = [False]
_state_owner = [None]


def _ensure_owner(context):
    from . import store
    text = store.text(context)
    key = tuple(_owner(context))
    if key != _state_owner[0]:
        _state_owner[0] = key
        _last.update(snap=None, ids=[], lead=None)
        _seen.update(sig=None, op=None, objs=None, membership=None)
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
        if k is not None and owns(context, ob):
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
    """Pieces shown on the dressed garment (not put back to their adjusted shapes), and the garment."""
    from . import control_basis as CB
    on, g = CB.on_garment(context, spec)
    return set(on & CB.result_names(g, context)), g


def project(context, names, active=None, view=None, others=False):
    """Mark pieces `names` (and no other) in every view: their pattern objects, their adjusted shapes (one object for
    each pair), the dressed garment's handle. active: the piece made active (as an object of `view`, 'pattern' or
    'world'; None keeps the active object). others: objects that are not pieces' are deselected too."""
    from . import store
    from . import piece_move as PM
    view_layer_key(context, create=True)
    _ensure_owner(context)
    spec = store.get(context)[0]
    if spec is not None:
        PLV.detach_legacy_results(context, spec)
    valid = {pc['name'] for pc in spec['pieces']} if spec is not None else set()
    names = list(dict.fromkeys(n for n in names if n in valid))
    want = set(names)
    vl = context.view_layer
    # Adopt legacy placements once, before changing the document-qualified record.
    import json
    text = store.text(context)
    old_owner = vl.get('ac9_piece_selection_owner')
    try:
        old_name = json.loads(vl.get('ac9_piece_selection', '{}')).get('garment')
    except (ValueError, TypeError, AttributeError):
        old_name = None
    for ob in set(PLV.objects(spec).values()) if spec is not None else ():
        if ob.get('ac9_placed_spec') is None:
            ob['ac9_placed_spec'] = old_owner if old_owner is not None and old_name == spec['name'] else text
    from . import dress as D
    g = D.garment(spec) if spec is not None else None
    if g is not None and g.get('ac9_garment_spec') is None:
        g['ac9_garment_spec'] = old_owner if old_owner is not None and old_name == spec['name'] else text
    _save_logical(context, names, active)
    _projecting[0] = True
    try:
        pats = PV.piece_objects()
        for n, ob in pats.items():
            _set(ob, n in want and owns(context, ob))
        from . import control_basis as CB
        worn, g = CB.on_garment(context, spec)
        coll = bpy.data.collections.get(PLV.COLL_NAME)
        placed = {n: ob for ob in coll.objects if PLV.PROP in ob and owns(context, ob)
                  for n in _names(ob)} if coll is not None else {}
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
                if _kind(ob) is None or not owns(context, ob):
                    _set(ob, False)
        if active is not None and active in want:
            ob = world.get(active) if view == 'world' else None
            choices = (ob, pats.get(active), world.get(active))
            vl.objects.active = next((o for o in choices if o is not None and o.name in vl.objects), None)
    finally:
        _projecting[0] = False
    _last.update(snap=_snapshot(context), ids=names, lead=active if active in want else None)
    _remember(context)
    return names


def marks(context):
    """The pieces marked as picked, per view, as they are drawn: the pattern objects (Blender's outline in the 2D
    view), the adjusted shapes (Blender's outline in 3D), the dressed garment (piece_look's tint and rim)."""
    from . import piece_look as PK
    pattern, placed = set(), set()
    for ob in objects(context):
        if not owns(context, ob):
            continue
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
        previous = lead(context)
        active = previous if previous in new else (names[0] if names and mode != 'SUB' else None)
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


def capture_flags(context):
    """A short-lived rollback snapshot, not a new logical selection."""
    return dict(selected=tuple(objects(context)), active=context.view_layer.objects.active)


def restore_flags(context, snapshot):
    """Restore an operation's input flags after all original memberships have been put back."""
    previous = _projecting[0]
    _projecting[0] = True
    try:
        vl = context.view_layer
        current = set(vl.objects)
        for ob in current:
            _set(ob, ob in snapshot['selected'])
        vl.objects.active = snapshot['active'] if snapshot['active'] in current else None
    finally:
        _projecting[0] = previous
        _remember(context)


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
    membership = tuple(ob.as_pointer() for ob in vl.objects)
    previous_membership = _seen.get('membership')
    if previous_membership is not None and membership != previous_membership and _logical_record(ctx) is not None:
        # Excluding/rebuilding a collection changes visible flags, not the user's logical selection.
        record = _logical_record(ctx)
        project(ctx, record.get('ids', ()), active=record.get('lead'))
        return
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
        names = (pieces(ctx) if _logical_record(ctx) is not None else
                 PT.selected_names([f for f in facts(ctx) if f.kind == 'pattern']) or pieces(ctx))
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
    from . import draft_tool as DT, measure_edit as MEE, tool_tab as TT
    DT.select_internal(context)
    DT.select_edge(context)
    DT.select_corner(context)
    DT.select_ipoint(context)
    MEE.select(context, '')
    TT.clear(context)


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
        if a in obs and b in obs and not (PLV.from_sim(obs[a]) or PLV.from_sim(obs[b])):
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
    want = list(names)
    want += [n for o in keep for n in _names(o) if n not in want]
    project(context, want, active=names[0] if activate else None, view='world', others=True)
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
    _seen['membership'] = tuple(ob.as_pointer() for ob in vl.objects)
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
    return [context.scene.session_uid, view_layer_key(context), text.session_uid if text else 0]


@persistent
def _cancel_pending(*_args):
    _pending.clear()
    _pending_owner.clear()
    _seen.update(sig=None, op=None, membership=None)
    _last.update(snap=None, ids=[], lead=None)
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
