# SPDX-License-Identifier: GPL-3.0-or-later
"""The editing basis of a shape, independent of its storage and its result cache.

The record lives ON the Mesh: replacing, copying, undoing or loading the shape carries its provenance with it.
Readers never change the record. Only publication/adoption/restoration writers do. A live preview is not adoption.
"""
import json

PLACED, RESULT, UNKNOWN = 'placement', 'result', 'unknown'
PROP = 'ac9_control_basis'
OWNER = 'ac9_control_owner'
POSES = 'ac9_control_pose_projection'
_cache = {}


def names(g):
    try:
        value = json.loads(g.get('ac9_piece_names', '[]')) if g is not None else []
        return tuple(value) if isinstance(value, list) and all(isinstance(n, str) for n in value) else ()
    except (ValueError, TypeError):
        return ()


def record(g, context=None):
    """A validated mesh-qualified record, or None for a legacy/foreign mesh. No IO in redraw."""
    if g is None or g.type != 'MESH':
        return None
    from . import store
    import bpy
    me = g.data
    # Readers of other garments must use the shape's owner, not whichever garment is active in the sidebar.
    owner = g.get('ac9_garment_spec') or me.get(OWNER)
    if owner is None or not isinstance(owner, bpy.types.Text):
        return None
    raw = me.get(PROP)
    if raw is None or me.get(OWNER) != owner:
        return None
    key = (me.as_pointer(), raw, owner.as_pointer() if owner else 0, g.get('ac9_piece_names', ''),
           len(me.vertices), len(me.polygons))
    if key not in _cache:
        try:
            r = json.loads(raw)
            valid = (r.get('schema') == 1 and r.get('names') == list(names(g)) and
                     r.get('vertices') == len(me.vertices) and r.get('faces') == len(me.polygons) and
                     isinstance(r.get('basis'), dict) and
                     all(k in r['names'] and v in (PLACED, RESULT, UNKNOWN) for k, v in r['basis'].items()))
            value = r if valid else None
        except (ValueError, TypeError, AttributeError):
            value = None
        if len(_cache) > 32:
            _cache.clear()
        _cache[key] = value
    return _cache[key]


def basis(g, context=None):
    """{piece: editing basis}. A bare merged mesh is NOT evidence that a simulation was adopted."""
    r = record(g, context)
    if r is not None:
        return {n: r['basis'].get(n, UNKNOWN) for n in names(g)}
    # A legacy job reference alone cannot prove which shape was accepted for editing.
    return {n: UNKNOWN for n in names(g)}


def publish(context, g, values, *, default=UNKNOWN):
    """Bind provenance to the exact mesh being published. The caller already owns its shape mutation."""
    from . import store
    ns = names(g)
    r = {'schema': 1, 'names': list(ns), 'vertices': len(g.data.vertices), 'faces': len(g.data.polygons),
         'basis': {n: values.get(n, default) for n in ns}}
    g.data[OWNER] = store.text(context)
    g.data[PROP] = json.dumps(r, separators=(',', ':'), sort_keys=True)


def adopt(context, g, selected=None):
    """A validated output was committed as the editable shape. Nonparticipants retain their own basis."""
    values = basis(g, context)
    for n in names(g) if selected is None else selected:
        if n in values:
            values[n] = RESULT
    publish(context, g, values)


def result_names(g, context=None):
    return frozenset(n for n, b in basis(g, context).items() if b == RESULT)


def has_result(g, context=None):
    return bool(result_names(g, context))


def garment(context, spec=None):
    from . import store, garment_owner
    sp = spec if spec is not None else store.get(context)[0]
    return garment_owner.garment(sp, context)


def on_garment(context, spec=None):
    """Visible pieces stored in the garment; storage membership is separate from editing basis."""
    from . import display_state as DS, store
    sp = spec if spec is not None else store.get(context)[0]
    g = garment(context, sp)
    return (frozenset(n for n, v in DS.snapshot(sp, g).items() if v['source'] == 'result'), g) if g is not None else (frozenset(), None)


def placed_basis(ob):
    from . import placed_view as PV
    return RESULT if PV.from_sim(ob) else PLACED


def contexts(context, spec=None):
    from . import store, placed_view as PV
    sp = spec if spec is not None else store.get(context)[0]
    if sp is None:
        return {}
    g = garment(context, sp)
    from . import display_state as DS
    plan = DS.snapshot(sp, g)
    obs = PV.objects(sp)
    return {p['name']: {'basis': plan[p['name']]['basis'],
                        'storage': 'garment' if plan[p['name']]['source'] == 'result' else 'placed' if plan[p['name']]['source'] == 'placed' else 'rule',
                        'object': g if plan[p['name']]['source'] == 'result' else obs.get(p['name']) if plan[p['name']]['source'] == 'placed' else None}
            for p in sp['pieces']}


def transform_targets(context, selected, spec=None):
    """(piece, explicit lead, mirrored) once per target. Only PLACEMENT pairs follow implicitly."""
    from . import store
    from .core import spec as S
    sp = spec if spec is not None else store.get(context)[0]
    ctx = contexts(context, sp)
    selected = list(dict.fromkeys(n for n in selected if n in ctx))
    out, done = [], set()
    pairs = S.placement_pairs(sp)
    for n in selected:
        if n in done:
            continue
        out.append((n, n, False)); done.add(n)
        if ctx[n]['basis'] != PLACED:
            continue
        for a, b in pairs:
            other = b if n == a else a if n == b else None
            if other is not None and other not in done and ctx.get(other, {}).get('basis') == PLACED:
                out.append((other, n, True)); done.add(other)
    return out


def invalidate():
    _cache.clear()


def clear_pose(g, selected):
    if g is None or POSES not in g.data:
        return
    try:
        values = json.loads(g.data[POSES])
    except (ValueError, TypeError):
        values = {}
    for n in selected:
        values.pop(n, None)
    if values:
        g.data[POSES] = json.dumps(values, separators=(',', ':'))
    else:
        g.data.pop(POSES, None)


def project_pose(context, spec, *, consume=False):
    """Project the merged placement's compact transform record into the current spec at an operation boundary.
    The geometry record participates in native transform Undo; unrelated spec fields are never restored from it.
    """
    g = garment(context, spec)
    if g is None or record(g, context) is None:
        return None
    try:
        values = json.loads(g.data.get(POSES, '{}'))
    except (ValueError, TypeError):
        return None
    if not values:
        return None
    sp = dict(spec, placement={n: dict(p) for n, p in spec.get('placement', {}).items()})
    bs = basis(g, context)
    for n, pose in values.items():
        if n in sp['placement'] and bs.get(n) == PLACED:
            sp['placement'][n]['pose'] = pose
    if consume:
        g.data.pop(POSES, None)
    return sp
