# SPDX-License-Identifier: GPL-3.0-or-later
"""One display plan per piece; scene changes are applied outside drawing.

The spec owns simulation state, reset_pieces owns the placement override, and
mesh metadata owns result membership. Visibility and Mask are outputs, never
evidence that a piece changed its shape source. No new saved state enum.
"""
import json
import os
from .core import spec as S

OFF = frozenset(('off_pattern', 'off_all'))
HIDDEN = 'ac9_display_hidden'
WAS_HIDDEN = 'ac9_display_was_hidden'
_applying = False
_jobs = {}
_masks = {}
_members = {}


def result_names(g):
    if g is None or g.type != 'MESH':
        return frozenset()
    from . import store
    me = g.data
    pa = me.attributes.get('ac9_piece')
    if pa is None or pa.domain != 'FACE' or pa.data_type != 'INT' or len(pa.data) != len(me.polygons):
        return frozenset()
    raw = str(g.get('ac9_piece_names', '[]'))
    key = (me.as_pointer(), len(me.polygons), raw, store.GEN[0])
    if key in _members:
        return _members[key]
    try:
        import numpy as np
        names = json.loads(raw)
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return frozenset()
        ids = np.empty(len(pa.data), np.int32)
        pa.data.foreach_get('value', ids)
        hit = frozenset(names[int(i)] for i in np.unique(ids) if 0 <= i < len(names))
        if len(_members) >= 32:
            _members.clear()
        _members[key] = hit
        return hit
    except (ValueError, TypeError):
        return frozenset()


def plan(spec, result=(), placed=(), reset=()):
    """Pure decision table. Proxy is a fallback, never a second solid shape."""
    result, placed, reset = set(result), set(placed), set(reset)
    pcs = {pc['name']: pc for pc in spec['pieces']}
    out = {}
    for n in pcs.keys() | result:
        pc = pcs.get(n)
        st = S.piece_state(pc) if pc is not None else 'off_all'
        source = ('none' if n in reset and n not in placed and n not in spec.get('placement', {})
                  else 'placed' if n in placed and (n in reset or n not in result)
                  else 'result' if n in result else 'placed' if n in placed
                  else 'proxy' if n in spec.get('placement', {}) else 'none')
        out[n] = {'state': st, 'source': source, 'solid': st not in OFF,
                  'dashed': st in OFF, 'seams': st != 'off_all',
                  'missing': st in ('active', 'freeze') and source in ('proxy', 'none'),
                  'can_materialize': source == 'proxy'}
    return out


def snapshot(spec, g=None):
    import bpy
    from . import placed_view as PV
    if g is None:
        g = bpy.context.scene.objects.get('AC9_Garment_' + spec['name'])
    elif bpy.context.scene.objects.get(g.name) != g:
        g = None
    try:
        reset = json.loads(g.get('ac9_reset_pieces', '[]')) if g else []
    except (ValueError, TypeError):
        reset = []
    placed = {n: ob for n, ob in PV.objects(spec).items() if bpy.context.scene.objects.get(ob.name) == ob}
    return plan(spec, result_names(g), placed, reset)


def job(g):
    """Read-only, cached job lookup for drawing; never repairs ac9_job."""
    import bpy
    if g is None:
        return None
    paths = tuple(m.filepath for m in g.modifiers if m.type == 'MESH_CACHE' and m.filepath)
    key = (g.as_pointer(), str(g.get('ac9_job', '')), paths)
    if key not in _jobs:
        p = key[1]
        if not p or not os.path.isdir(p):
            p = next((os.path.dirname(bpy.path.abspath(f)) for f in paths
                      if os.path.exists(os.path.join(os.path.dirname(bpy.path.abspath(f)), 'scene.idx.npz'))), None)
        _jobs[key] = p
    return _jobs[key]


def placed_names(spec, g):
    if not result_names(g):
        return None
    return frozenset(n for n, v in snapshot(spec, g).items() if v['source'] == 'placed')


def hidden_result(spec, g):
    return frozenset(n for n, v in snapshot(spec, g).items()
                     if n in result_names(g) and (v['source'] in ('placed', 'none') or not v['solid']))


def apply(context, spec, create_missing=False, allow_split=False):
    """Reconcile derived scene display inside an operation, never a draw/poll.

    Existing geometry is not regenerated. Missing placed shapes use the normal
    shape builder, without starting a simulation or creating a job folder.
    """
    global _applying
    if _applying or not spec:
        return []
    import bpy
    from . import dress as D, placed_view as PV, store
    _applying = True
    notes = []
    try:
        g = context.scene.objects.get(D.garment_name(spec))
        p = snapshot(spec, g)
        missing = {n for n, v in p.items() if v['missing']}
        if create_missing:
            unplaced = {n for n in missing if not p[n]['can_materialize']}
            if unplaced:
                notes.append('AC9 place these pieces first: ' + ', '.join(sorted(unplaced)))
            missing -= unplaced
            if (g is not None and len(g.data.polygons) and g.get('ac9_piece_names')
                    and not result_names(g)):
                notes.append('AC9 result piece mapping is invalid; its shape was kept, not rebuilt')
                missing.clear()
        if create_missing and missing and not D.AC9_OT_SimDress.running():
            try:
                res = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size,
                                shapes_only=True, placed_only=True)
                pn = [str(n) for n in res['pat']['names']]
                pairs = [(a, b) for a, b in res['pairs'] if pn[a] in missing and pn[b] in missing]
                made, _, _ = PV.build(context, spec, res['pat'], res['tris'], res['X'], pairs, only=missing)
                if made:
                    context.view_layer.update()
                if result_names(g) and made:
                    D.set_shown_as_placed(g, spec, set(D.reset_pieces(g)) | set(made) |
                                          {pn[b] for a, b in pairs if pn[a] in made})
                # PV.build restores saved hand geometry and its pose once.
                # That visible shape, not a cylinder proxy, is the next start.
            except (ValueError, RuntimeError) as exc:
                notes.append('AC9 placed shape unavailable: ' + str(exc))
            spec = store.get(context)[0] if store.text(context) is not None else spec
            p = snapshot(spec, g)
            unavailable = {n for n in missing if p.get(n, {}).get('missing')}
            if unavailable:
                notes.append('AC9 no 3D shape created for: ' + ', '.join(sorted(unavailable)))
        obs = {n: ob for n, ob in PV.objects(spec).items() if context.scene.objects.get(ob.name) == ob}
        # A legacy mirrored object may hold two pieces. Split only a mixed-state
        # pair, preserving its current geometry with the existing split tool.
        split = False
        for ob in set(obs.values()):
            a, b = ob.get(PV.PROP), ob.get(PV.TWIN_PROP)
            if (allow_split or create_missing) and b and p.get(a, {}).get('solid') != p.get(b, {}).get('solid'):
                outlines = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
                PV.split_pair(context, spec, outlines, a, b, keep_shape=True)
                split = True
        if split:
            # A newly linked twin is not in ViewLayer.objects until evaluated.
            context.view_layer.update()
        obs = {n: ob for n, ob in PV.objects(spec).items() if context.scene.objects.get(ob.name) == ob}
        for ob in set(obs.values()):
            if ob.name not in context.view_layer.objects:
                continue
            held = [n for n, o in obs.items() if o == ob]
            should_hide = all(not p.get(n, {}).get('solid', False) or p.get(n, {}).get('source') != 'placed'
                              for n in held)
            if should_hide and not ob.get(HIDDEN):
                ob[WAS_HIDDEN] = bool(ob.hide_get())
                ob[HIDDEN] = True
                ob.hide_set(True)
            elif should_hide and not ob.hide_get():
                # this module is the one writer of a placed object's display: shown again by something else (an
                # Alt+H, a file saved by an older version) it is hidden again, the plan read every time, the mark
                # only remembering the user's own hiding to give back
                ob.hide_set(True)
            elif not should_hide and ob.get(HIDDEN):
                ob.hide_set(bool(ob.get(WAS_HIDDEN, False)))
                ob.pop(HIDDEN, None)
                ob.pop(WAS_HIDDEN, None)
        if result_names(g):
            hidden = hidden_result(spec, g)
            key = (g.as_pointer(), g.data.as_pointer(), len(g.data.vertices), len(g.data.polygons),
                   str(g.get('ac9_piece_names', '')), hidden)
            m = g.modifiers.get(D.HIDE_MOD)
            signature = (key, m.as_pointer() if m else None)
            if _masks.get(g.as_pointer()) != signature:
                D.hide_pieces(g, hidden)
                m = g.modifiers.get(D.HIDE_MOD)
                _masks[g.as_pointer()] = (key, m.as_pointer() if m else None)
        return notes
    finally:
        _applying = False


def invalidate():
    _jobs.clear()
    _masks.clear()
    _members.clear()


def _sync_all():
    import bpy
    from . import dress as D, store
    invalidate()
    for spec in D.garment_specs().values():
        apply(bpy.context, spec)
    from . import overlay, piece_look
    overlay.invalidate()
    piece_look.invalidate()


def _on_load(*_):
    _sync_all()


def _on_undo(*_):
    _sync_all()


def _startup():
    _sync_all()


def register():
    import bpy
    for key, fn in (('load_post', _on_load), ('undo_post', _on_undo), ('redo_post', _on_undo)):
        handlers = getattr(bpy.app.handlers, key)
        for old in [f for f in handlers if getattr(f, '__module__', '') == __name__]:
            handlers.remove(old)
        handlers.append(bpy.app.handlers.persistent(fn))
    if not bpy.app.timers.is_registered(_startup):
        bpy.app.timers.register(_startup, first_interval=0.2)


def unregister():
    import bpy
    for key in ('load_post', 'undo_post', 'redo_post'):
        handlers = getattr(bpy.app.handlers, key)
        for old in [f for f in handlers if getattr(f, '__module__', '') == __name__]:
            handlers.remove(old)
    if bpy.app.timers.is_registered(_startup):
        bpy.app.timers.unregister(_startup)
    invalidate()
