# SPDX-License-Identifier: GPL-3.0-or-later
"""A read-only final placement candidate, shared by the overlay and its commit."""
import copy
import json
import bpy
import numpy as np
from . import dress as D, placed_view as PV, place_fold as PF, prepare_build as PB, store
from .core import spec as S
from .i18n import rpt


def _bindings(context, spec, selected):
    from . import control_basis as CB
    states = CB.contexts(context, spec)
    bindings = {}
    for name in selected:
        state = states[name]
        ob = state['object']
        if ob is None:
            bindings[name] = (state['basis'], None, None, None, None)
            continue
        co = np.empty(len(ob.data.vertices) * 3, np.float32)
        ob.data.vertices.foreach_get('co', co)
        bindings[name] = (state['basis'], ob.session_uid, ob.data.session_uid,
                          tuple(v for row in ob.matrix_world for v in row), co)
    return bindings


def _require_bindings(context, candidate):
    candidate['owner'].require(context)
    now = _bindings(context, candidate['spec'], candidate['selected'])
    for name, before in candidate['bindings'].items():
        after = now.get(name)
        if after is None or before[:4] != after[:4] or not np.array_equal(before[4], after[4]):
            raise ValueError(rpt('The placement source changed; place the piece again'))
    if 'environment' in candidate and _environment(context, candidate['spec']) != candidate['environment']:
        raise ValueError(rpt('The placement source changed; place the piece again'))


def _environment(context, spec):
    """Exactly the evaluated body/obstacle inputs read by shape preparation, with no retirement commit."""
    from . import placement as PL, control_basis as CB
    props = context.scene.ac9_cloth_sim
    probe = PB.PreparationBuild(context, lambda _: None)
    try:
        co, tri = PL.body_arrays(context, props.body)
        colliders, arrays = D.collider_arrays(context, exclude=CB.garment(context, spec))
        garments, notes = D.other_garments(context, spec, _build=probe)
        data = [co, tri] + [a for pair in arrays for a in pair] + [a for _, c, t in garments for a in (c, t)]
        tokens = tuple((a.shape, a.dtype.str, a.tobytes()) for a in data)
        settings = D.presets.resolve(props.preset, props, environ=None if bpy.app.background else {})
        return (props.mesh_size, json.dumps(settings, sort_keys=True), bool(props.floor),
                props.body.session_uid if props.body else None,
                tuple(o.session_uid for o in colliders), tuple(n for n, _, _ in garments), tuple(notes), tokens)
    finally:
        probe.abort()


def build(context, spec, selected, cache=None):
    """Generate the actual mesh including rings, guide frames and explicit folds; never edit the scene."""
    selected = set(selected)
    environment = _environment(context, spec)
    if cache is not None:
        contract = (tuple(store.GEN), tuple((k, id(v)) for k, v in spec.items() if k != 'placement'),
                    tuple(sorted(selected)), tuple(sorted(spec.get('placement', {}))), environment)
        if cache.get('contract') != contract:
            cache.clear()
            cache['contract'] = contract
    source_owner = store.edit_owner(context)
    bindings = _bindings(context, spec, selected)
    # This derived recipe controls geometry only. A result/unknown partner keeps its saved placement recipe.
    blocked = set()
    for a, b in S.placement_pairs(spec):
        if (a in selected) != (b in selected):
            blocked.update((a, b))
    shape_spec = dict(spec, pairs=[p for p in spec.get('pairs', []) if not set(p).intersection(blocked)],
                      placement={n: dict(p, unlinked=True) if n in blocked else p
                                 for n, p in spec.get('placement', {}).items()})
    diagnostics = [D.IN_BODY_LAST, D.CLOSURE_LAST, D.ENDS_LAST, D.LAP_LAST, D.RING_USED]
    previous = [copy.deepcopy(v) for v in diagnostics]
    owner = None if cache is not None and 'inputs' in cache else D.Preparation(context)
    try:
        res = D._prepare(context, context.scene.ac9_cloth_sim.mesh_size,
                         shapes_only=True, placed_only=True, _build=owner.build if owner else None, _owner=owner,
                         _preview_spec=shape_spec, _preview_cache=cache)
    finally:
        if owner is not None:
            owner.abort()
        for target, values in zip(diagnostics, previous):
            target.clear(); target.update(values)
    names = [str(v) for v in res['pat']['names']]
    # A shared Mirror object has exactly one source. Fold that source, then mirror its final shape.
    targets = {t for s, t in res['pairs']}
    X = [np.array(v, dtype=float, copy=True) for v in res['X']]
    folded = {}
    notes = []
    from .core import fold_resolve
    fold_spec = shape_spec
    for i, name in enumerate(names):
        if name not in selected or i in targets:
            continue
        done = []
        for f in fold_resolve.ordered_folds(fold_spec):
            if f.get('piece') != name or not f.get('line') or not f.get('deg'):
                continue
            line, deg = f['line'], float(f['deg'])
            if PF.spec_side(fold_spec, name, line) == 'NONE' or PF.legacy_collar(fold_spec, name, line, deg):
                continue
            info, why = PF.shape_info(fold_spec, name, line, res['pat'], res['tris'], X[i])
            if info is None:
                from .i18n import tx
                notes.append(tx('{0} {1} not folded again: {2}', name, line, why))
                continue  # Same refusal as refold; the physical fold remains in the simulation recipe.
            side = PF.side_index(fold_spec, name, line, info)
            before = X[i].copy()
            co, _ = PF._shape(info, side, deg)
            X[i] = co / PV.MM
            done.append((dict(line=line, deg=deg, side=int(side)), before))
        folded[name] = done
    for s, t in res['pairs']:
        if names[s] in selected or names[t] in selected:
            plane = S.pair_plane(spec, names[s])
            X[t] = S.mirror_points(X[s], plane, S.mirror_center(spec, plane, names[s]))
    canonical = [v.copy() for v in X]
    poses = PV.placement_poses(shape_spec)
    for i, name in enumerate(names):
        pose = poses.get(name)
        if pose and name in selected:
            org = .5 * (np.min(res['X'][i], axis=0) + np.max(res['X'][i], axis=0))
            matrix = np.asarray(PV.pose_matrix(pose, org), float)
            X[i] = (np.c_[(X[i] - org) * PV.MM, np.ones(len(X[i]))] @ matrix.T)[:, :3] / PV.MM
    lines = {}
    for i, name in enumerate(names):
        if name not in selected:
            continue
        boundary = cache.setdefault('boundary', {}) if cache is not None else {}
        if name not in boundary:
            tri = np.asarray(res['tris'][i])
            edges, count = np.unique(np.sort(np.r_[tri[:,[0,1]], tri[:,[1,2]], tri[:,[2,0]]], axis=1),axis=0,return_counts=True)
            boundary[name] = edges[count == 1]
        lines[name] = (X[i][boundary[name]].reshape(-1,3) * PV.MM).astype(np.float32)
    return dict(spec=spec, res=res, X=X, canonical=canonical, folded=folded, lines=lines, selected=selected,
                notes=notes, owner=source_owner, bindings=bindings, environment=environment)


@PB.prepared_consumer
def commit(context, candidate, *, preparation=None):
    """Install the displayed arrays and their fold undo history as one reversible operation."""
    sp, res, selected = candidate['spec'], candidate['res'], candidate['selected']
    _require_bindings(context, candidate)
    store.put(context, sp)
    PB.replan_source(preparation)
    PV.remove(selected, sp)
    # Canonical generated coordinates must be installed before a separate pair's reflected follower frame.
    PV.build(context, sp, res['pat'], res['tris'], res['X'], res['pairs'], only=selected, link=False)
    obs = PV.objects(sp)
    names = [str(v) for v in res['pat']['names']]
    for name in selected:
        ob = obs.get(name)
        if ob is None or ob.get(PV.PROP) != name or name not in names:
            continue
        final = candidate['canonical'][names.index(name)]
        ob.data.vertices.foreach_set('co', ((final - PV.origin_mm(ob)) * PV.MM).astype(np.float32).ravel())
        ob.data.attributes[PV.GEN_ATTR].data.foreach_set('vector', (final * PV.MM).astype(np.float32).ravel())
        ob.data.update()
    for name, folds in candidate['folded'].items():
        ob = obs.get(name)
        if ob is None or ob.get(PV.PROP) != name:
            continue
        # build chose its pivot from the final shape. Fold backups are transformed into that same object space.
        for k, (record, before) in enumerate(folds):
            local = (before - PV.origin_mm(ob)) * PV.MM
            attr = ob.data.attributes.new(PF.BEFORE_ATTR + str(k), 'FLOAT_VECTOR', 'POINT')
            attr.data.foreach_set('vector', local.astype(np.float32).ravel())
        if folds:
            ob[PV.FOLD_PROP] = json.dumps([record for record, _ in folds])
    pair_notes = PV.link_pairs(context, sp)
    from . import control_basis as CB
    g = CB.garment(context, sp)
    if g is not None:
        D.set_shown_as_placed(g, sp, set(D.shown_as_placed(g, sp)) | selected)
        D.sync_hidden(g, sp)
    PV.invalidate_shown()
    notes = list(candidate.get('notes', ()))
    notes += pair_notes
    for target, key in ((D.IN_BODY_LAST, 'in_body'), (D.CLOSURE_LAST, 'closures'),
                        (D.ENDS_LAST, 'ends_used'), (D.LAP_LAST, 'lap_used')):
        target.clear()
        target.update(res.get(key, {}))
    D.RING_USED.update(res.get('ring_used', {}))
    from . import placement as PL
    notes += PL._start_notes(context, sp, res, selected=selected)
    return notes
