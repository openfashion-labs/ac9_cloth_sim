# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded read-only garment state. No operators, syncing, file I/O or scene edits."""
import json
import hashlib
import time

TOOL_DOCUMENTS = {
    'edit_pattern_tool': 'edit_pattern', 'transform_pattern_tool': 'edit_pattern', 'edit_seams_tool': 'seams',
    'add_seam_tool': 'seams', 'add_seam_mn_tool': 'seams', 'place_tool': 'place',
    'measure_tool': 'measure', 'tape_along_tool': 'measure',
    'tape_straight_tool': 'measure', 'move_level_tool': 'measure',
    'draw_piece_tool': 'draw_piece', 'draw_line_tool': 'draw_piece',
    'rect_tool': 'draw_piece', 'ellipse_tool': 'draw_piece',
    'internal_rect_tool': 'draw_piece', 'internal_ellipse_tool': 'draw_piece',
}


def _text(value):
    return str(value)[:100] if value is not None else None


def preparation(context, spec):
    """Read-only subset of START prerequisites, not a solver or mesh preflight."""
    from .core import presets, spec as S
    from . import ui
    import bpy
    props = context.scene.ac9_cloth_sim
    # Master always uses Properties Tool; older layout-switch builds expose new_layout.
    layout_switch = getattr(ui, 'new_layout', None)
    properties_tool = layout_switch(context) if callable(layout_switch) else True
    location = {'editor': 'properties', 'properties_context': 'TOOL', 'panel': 'AC9 Run'} if properties_tool else {
        'editor': 'viewport', 'panel': 'Run'}
    location.update(operator='ac9_cloth_sim.update_3d', visibility='unverified')
    base = {'schema': 1, 'status': 'unavailable',
            'scope': {'scene': str(context.scene.as_pointer()),
                      'text': str(props.spec_text.as_pointer()) if props.spec_text else None},
            'observed_at': time.time(), 'run_location': location,
            'unchecked': ['mesh', 'sewing_geometry', 'solver_environment', 'cache_and_continue', 'unsynced_object_edits']}
    if spec is None:
        return dict(base, reason='garment_spec_unavailable')
    try:
        # Resolve exactly the START spec overrides and twin completion, on a
        # private copy. Never call prepare(), sync(), job_of() or an operator.
        st = presets.resolve(getattr(props, 'preset', ''), props, environ=None if bpy.app.background else {})
        effective = presets.spec_for_job(json.loads(json.dumps(spec)), st)
        effective['placement'], _ = S.mirror_twins(effective)
        pieces = effective['pieces']
        enabled = [p['name'] for p in pieces if p.get('enabled', True)]
        placed = effective.get('placement', {})
        missing = [n for n in enabled if n not in placed]
        body = bool(props.body and context.scene.objects.get(props.body.name) == props.body)
        guides = bool(effective.get('guides'))
        counts = dict(total=len(pieces), enabled=len(enabled), disabled=len(pieces)-len(enabled),
                      enabled_placed=len(enabled)-len(missing), enabled_unplaced=len(missing))
        blockers = ([ 'body' ] if not body else []) + ([ 'guides' ] if not guides else [])
        if not enabled: blockers.append('no_enabled_pieces')
        if missing: blockers.append('unplaced_pieces')
        signature = {'scope': base['scope'], 'pieces': [(p['name'], bool(p.get('enabled', True)), p['name'] in placed) for p in pieces],
                     'body': props.body.name if body else None, 'guides': effective.get('guides'),
                     'preset': st['name'], 'layout': location}
        seams = effective.get('seams', [])
        return dict(base, status='available', counts=counts, blockers=blockers,
                    missing_names=[_text(n) for n in missing[:6]], missing_returned=min(len(missing), 6),
                    missing_truncated=len(missing)>6,
                    seams={'total': len(seams), 'enabled': sum(bool(s.get('enabled', True)) for s in seams),
                           'geometry_checked': False},
                    condition_signature=hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24])
    except (ValueError, TypeError, KeyError, IndexError) as error:
        return dict(base, reason=str(error)[:120])


def _extended(context, spec, internal, names):
    from . import store, placement, placed_view as PLV, pattern_view as PV, dress, dart_ui
    props = context.scene.ac9_cloth_sim
    placements = spec.get('placement', {}) if spec else {}
    def belongs(ob):
        return PLV.PROP not in ob or (spec is not None and ob.get(PLV.GARMENT_PROP) == PLV.garment(spec))
    from . import selection as SEL
    selected = sorted({n for o in SEL.objects(context) if belongs(o)
                       for n in (o.get(PV.PIECE_PROP), o.get(PLV.PROP), o.get(PLV.TWIN_PROP)) if n in names})
    active = context.active_object
    active_names = [n for n in (active.get(PV.PIECE_PROP), active.get(PLV.PROP), active.get(PLV.TWIN_PROP))
                    if n in names] if active and belongs(active) else []
    # Objects in another scene must not become placements of this scene just
    # because their garment and piece names match.
    placed = {n: ob for n, ob in PLV.objects(spec).items()
              if n in names and context.scene.objects.get(ob.name) == ob} if spec else {}
    rows = []
    for pc in (spec or {}).get('pieces', [])[:12]:
        ob = placed.get(pc['name'])
        rows.append({'name': _text(pc['name']), 'label': _text(pc.get('label')),
                     'enabled': bool(pc.get('enabled', True)), 'placed': pc['name'] in placements,
                     'object_present': ob is not None,
                     'marks': {k: bool(ob.get(prop)) for k, prop in
                               (('from_sim', PLV.SIM_PROP), ('hand', PLV.HAND_PROP), ('fold', PLV.FOLD_PROP),
                                ('to_seam', PLV.SEAM_PROP), ('laid', PLV.LAID_PROP))} if ob else {}})
    seam = store.active_seam(context, spec)
    seam_data = None
    if seam:
        row = props.seams[props.seam_index]
        # Only the persisted active seam is scoped. Never use seam_more here.
        _piece, dart = dart_ui.active_dart(context, spec)
        seam_data = {'id': _text(seam['id']), 'piece_a': _text(seam['a']['piece']),
                     'piece_b': _text(seam['b']['piece']), 'is_dart': dart is not None,
                     'length_a_mm': float(row.len_a), 'length_b_mm': float(row.len_b),
                     'check': _text(row.check), 'enabled': bool(row.enabled)}
    line = None
    if internal:
        n, lid, data = internal
        points = data.get('pts', [])
        fold = next((f.get('deg') for f in spec.get('folds', []) if f.get('piece') == n and f.get('line') == lid), 0)
        line = {'piece': _text(n), 'line_id': _text(lid), 'point_count': len(points),
                'points_mm': [[float(x) for x in p[:2]] for p in points[:12]],
                'fold_degrees': float(fold or 0),
                'elastic_present': any(e.get('piece') == n and e.get('line') == lid for e in spec.get('elastic', [])),
                'seam_ids': [_text(s['id']) for s in spec.get('seams', [])
                             if any(s[k].get('piece') == n and s[k].get('line') == lid for k in ('a','b'))][:12]}
    g = context.scene.objects.get(dress.garment_name(spec)) if spec else None
    cls = dress.AC9_OT_SimDress
    running = cls.running()
    # The modal's class globals carry no scene/spec token. A matching object
    # name alone is not enough to attach the global run to this scene.
    execution = {'global_mode': running, 'scope_confirmed': running is None,
                 'current_running': False if running is None else None,
                 'progress': None, 'status_text': str(props.dress_status)[:160],
                 'status_text_source': 'saved scene field, may be stale',
                 'result_object': _text(g.name) if g else None,
                 'job_reference_present': bool(g.get('ac9_job')) if g else False,
                 'frozen': dress.frozen(g) if g else None,
                 'reset_pieces': [_text(n) for n in dress.reset_pieces(g)[:12]],
                 'cache_modifier_count': sum(m.type == 'MESH_CACHE' for m in g.modifiers) if g else 0}
    return {'selected_pieces': [_text(n) for n in selected[:12]], 'selected_piece_count': len(selected),
            'active_pieces': [_text(n) for n in active_names], 'piece_count': len(names), 'pieces': rows,
            'active_seam': seam_data, 'additional_seam_selection': {'status': 'unverified', 'ids': None},
            'tool_tab_focus': {'status': 'unverified'}, 'internal_line_details': line,
            'placement': {'body': _text(props.body.name) if props.body else None,
                          'body_in_scene': bool(props.body and context.scene.objects.get(props.body.name) == props.body),
                          'guides_present': bool((spec or {}).get('guides')),
                          'placed_count': len(placements), 'placed_object_count': len(placed),
                          'pairs': [[_text(n) for n in pair[:2]] for pair in (spec or {}).get('pairs', [])[:12]],
                          'geometry_edits_checked': False},
            'execution': execution,
            'operation_targets': {'add_dart': 'cursor_outline_not_selected_edges',
                                  'move_dart': 'cursor_dart_or_active_dart_seam',
                                  'cursor_position_observed': False},
            'operation_document_requirements': {'move_to_seam': 'move_to_seam', 'lay_on_edge': 'lay_on_edge'}}


def snapshot(context):
    from . import draft_tool as DT, store, pattern_view as PV
    spec, _ = store.get(context)
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    tool_id = tool.idname if tool else None
    active = bool(tool_id and tool_id.startswith('ac9_cloth_sim.'))
    t = context.scene.ac9_cloth_sim.spec_text
    scope = (context.scene.as_pointer(), t.as_pointer() if t else None)
    edges_current = DT._companion_selection_scope.get('edges') == scope
    internal_current = DT._companion_selection_scope.get('internal') == scope
    edges = DT.selected_edges(spec) if edges_current else []
    internal = DT.selected_internal(context, spec) if internal_current and spec else None
    names = {pc['name'] for pc in spec['pieces']} if spec else set()
    pieces = sorted({str(o[PV.PIECE_PROP]) for o in context.view_layer.objects.selected
                     if PV.PIECE_PROP in o and o[PV.PIECE_PROP] in names})
    result = {'schema': 2, 'active': active, 'tool_id': tool_id,
            'scene': context.scene.name, 'spec_text': t.name if t else None,
            'spec_available': spec is not None, 'selected_pieces': pieces[:32],
            'selected_piece_count': len(pieces),
            'selected_edges': [{'piece': str(n)[:160], 'edge_id': str(e)[:160], 'length_mm': length}
                               for n, e, length in edges[:32]],
            'selected_edge_count': len(edges), 'edges_scope_confirmed': edges_current,
            'selected_internal_line': {'piece': str(internal[0])[:160], 'line_id': str(internal[1])[:160]} if internal else None,
            'internal_scope_confirmed': internal_current,
            'recommended_documents': ['guide_notes', TOOL_DOCUMENTS.get(tool_id.split('.')[-1], 'unmapped_tool')] if active else [],
            'selection_scope': 'Addon drafting selection, not Blender mesh selection. Unknown scope is not no selection.',
            'limits': 'Selections before this integration loaded or from another scene/spec are not confirmed. Cursor hover is not reported as a fresh observation.'}
    result.update(_extended(context, spec, internal, names))
    result['simulation_preparation'] = preparation(context, spec)
    result['piece_details'] = {'total': len(names), 'returned': len(result['pieces']),
                               'truncated': len(names) > len(result['pieces'])}
    # Preserve the aggregate counts while bounding optional detail arrays.
    truncated = []
    while len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 7400:
        lists = [(k, v) for k, v in result.items() if isinstance(v, list) and v and k != 'recommended_documents']
        if not lists:
            raise ValueError('Runtime details exceed the read-only response budget')
        key, value = max(lists, key=lambda kv: len(json.dumps(kv[1], ensure_ascii=False)))
        value.pop()
        if key not in truncated:
            truncated.append(key)
    result['truncated_fields'] = truncated
    result['piece_details'].update(returned=len(result['pieces']), truncated=len(names)>len(result['pieces']))
    return result
