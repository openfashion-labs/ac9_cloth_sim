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

HANDLE_NAMES = "ac9_move_pieces"     # piece_move.NAMES_PROP: a handle picked on the dressed garment


def _fact(ob, active):
    if PLV.PROP in ob:
        return PT.ObjFact('placed', tuple(n for n in (ob.get(PV.PIECE_PROP), ob.get(PLV.PROP), ob.get(PLV.TWIN_PROP))
                                          if n), True, ob == active)
    if PV.PIECE_PROP in ob:
        return PT.ObjFact('handle' if HANDLE_NAMES in ob else 'pattern', (ob[PV.PIECE_PROP],), True, ob == active)
    return None


def objects(context):
    """The selected objects of the whole view layer (not only those a viewport shows), in the view layer's order."""
    vl = context.view_layer
    return list(vl.objects.selected) if vl is not None else []


def facts(context):
    """ObjFacts of the selected AC9 piece objects (pattern pieces, placed objects, handles)."""
    vl = context.view_layer
    if vl is None:
        return []
    active = vl.objects.active
    return [f for f in (_fact(ob, active) for ob in vl.objects.selected) if f is not None]


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
    left the other view's pieces selected). keep: objects left as they are."""
    for ob in objects(context):
        if ob not in keep:
            ob.select_set(False)


# ---------------------------------------------------------------- Blender's own select tools
_seen = {'sig': None}
# Blender's select operators that replace the selection, with the properties that make them add instead
_REPLACE = {'VIEW3D_OT_select': ('extend', 'toggle', 'deselect'), 'VIEW3D_OT_select_box': ('mode',),
            'VIEW3D_OT_select_lasso': ('mode',), 'VIEW3D_OT_select_circle': ('mode',)}


def _kind(ob):
    return 'world' if (PLV.PROP in ob or HANDLE_NAMES in ob) else 'pattern' if PV.PIECE_PROP in ob else None


def _replaced(wm):
    """True when the last operator run was one of Blender's select tools replacing the selection, and it was not
    seen before (a later change of the selection by something else is not that operator's)."""
    try:
        op = wm.operators[-1]
    except (IndexError, AttributeError):
        return False
    keys = _REPLACE.get(op.bl_idname)
    if keys is None or op.as_pointer() == _seen.get('op'):
        return False
    _seen['op'] = op.as_pointer()
    p = op.properties
    if keys == ('mode',):
        return getattr(p, 'mode', 'SET') == 'SET'
    return not any(getattr(p, k, False) for k in keys)


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


@persistent
def watch(scene, depsgraph):
    try:
        ctx = bpy.context
        vl = ctx.view_layer
    except AttributeError:
        return
    if vl is None:
        return
    act = vl.objects.active
    sig = (act.name if act is not None else None, tuple(o.name for o in vl.objects.selected))
    if sig == _seen['sig']:
        return
    _seen['sig'] = sig
    if _replaced(ctx.window_manager):
        drop_other_view(ctx)
    to_leader(ctx)


LEAD_PROP = "ac9_placed_lead"       # placed_view.LEAD_PROP (not imported: this module loads before it)


def select_placed(context, spec, names, side=None, keep=(), activate=True):
    """The one way pieces' placed objects are selected (Place, Move by Hand, a click on a pair's following side).

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
    deselect_all(context, keep=[o for o in keep if not o.get(LEAD_PROP)])
    for ob in picked:
        ob.select_set(True)
    vl = context.view_layer
    if picked and activate:
        vl.objects.active = picked[0]
    _seen['sig'] = (vl.objects.active.name if vl.objects.active else None, tuple(o.name for o in vl.objects.selected))
    return picked


def to_leader(context):
    """Blender's own select (a click, a box) took a pair's following side. With its leader selected too: the
    follower is deselected (one gizmo, not one between the two). Alone: it is made the side moved (a data change, so
    deferred out of the depsgraph handler). Returns the follower handled, or None."""
    vl = context.view_layer
    fols = [o for o in vl.objects.selected if o.get(LEAD_PROP)]
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
            later.append(fol.name)
    _seen['sig'] = (vl.objects.active.name if vl.objects.active else None, tuple(o.name for o in vl.objects.selected))
    if later:
        _pending[:] = later
        if not bpy.app.timers.is_registered(_lead_clicked):
            bpy.app.timers.register(_lead_clicked, first_interval=0.0)
    return fols[0]


_pending = []


def _lead_clicked():
    from . import store
    ctx = bpy.context
    names, _pending[:] = list(_pending), []
    spec = store.get(ctx)[0]
    if spec is None:
        return None
    obs = PLV.objects(spec)
    fols = [n for n in names if n in obs and obs[n].get(LEAD_PROP) and obs[n].select_get()]
    if fols:
        keep = [o for o in objects(ctx) if not o.get(LEAD_PROP)]
        select_placed(ctx, spec, fols, side=fols[0], keep=keep)
    return None


def register():
    if watch not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(watch)


def unregister():
    if watch in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(watch)
