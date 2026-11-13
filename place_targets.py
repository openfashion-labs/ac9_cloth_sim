# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, temporary partner picking shared by Part to Seam and Band on Edge."""
import json

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import pattern_view as PV, placed_view as PLV, selection as SEL, store, toolbar as TB
from . import view_visibility as VV
from .core import place_targets as scope
from .core import spec as S
from .i18n import tx, wrap

_active = None


def resolve_movers(context, spec, requested):
    from . import placement as PL, place_seam as PS
    names = list(dict.fromkeys(requested))
    if not names or PS.on_garment(spec, names):
        raise ValueError(tx("Select placed pieces to move; use Back to Placed for dressed pieces"))
    if any(n not in spec.get('placement', {}) and not PS.sewn_to_placed(spec, n) for n in names):
        raise ValueError(tx("Place the moving pieces first"))
    names = PL.lead_only(spec, names)
    obs = PLV.objects(spec)
    # Match the execution's ownership rule without promoting a shared Mirror side.
    names = list(dict.fromkeys(obs[n].get(PLV.PROP, n) if n in obs else n for n in names))
    follow = {ob.get(PLV.TWIN_PROP) for n in names if (ob := obs.get(n)) is not None}
    for a, b in S.placement_pairs(spec):
        if a in names:
            follow.add(b)
    return names, sorted(follow - {None} - set(names))


def arguments(op, context):
    """Public EXEC needs explicit partners too; internal geometry helpers keep their API."""
    try:
        targets = json.loads(op.target_pieces)
        requested = json.loads(op.moving_pieces) if op.moving_pieces else SEL.pieces(context)
        if not isinstance(targets, list) or not targets or not all(isinstance(n, str) for n in targets):
            raise ValueError
        if not isinstance(requested, list) or not all(isinstance(n, str) for n in requested):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(tx("Choose target pieces first: start this tool from its button")) from None
    return requested, set(targets)


def validate(context, requested, targets, got=None):
    from . import place_seam as PS
    spec = store.get(context)[0]
    movers, followers = resolve_movers(context, spec, requested)
    if set(targets) & set(movers + followers):
        raise ValueError(tx("Moving pieces and their following pair cannot be targets"))
    if got is None:
        got, why = PS.shapes(context, spec)
        if got is None:
            raise ValueError(why)
    available = {n: scope.partner_names(got, n, movers + followers) for n in movers}
    if set(targets) - {p for row in available.values() for p in row}:
        raise ValueError(tx("Targets must share an enabled seam with a moving piece"))
    missing = [n for n in movers if not set(available[n]) & set(targets)]
    if missing:
        raise ValueError(tx("Choose a sewn target for: {0}", ', '.join(missing)))
    return movers, followers, available, got


def _redraw(context):
    for area in context.window.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _view_at(context, event):
    for area in context.window.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for region in area.regions:
            if region.type == 'WINDOW' and region.x <= event.mouse_x < region.x + region.width \
                    and region.y <= event.mouse_y < region.y + region.height:
                return area, region
    return None


def _pick(context, event):
    found = _view_at(context, event)
    if found is None:
        return None
    area, region = found
    with context.temp_override(area=area, region=region):
        kind, ob, face = TB.under(context, event.mouse_x - region.x, event.mouse_y - region.y)
        if kind == 'PATTERN':
            return ob.get(PV.PIECE_PROP)
        if kind == 'PLACED':
            return PLV.placed_face_piece(context, ob, face)
        if kind == 'GARMENT':
            return TB.garment_piece(ob, face)
    return None


def invoke(op, context, event):
    global _active
    if _active is not None:
        op.report({'WARNING'}, tx("Finish or cancel the current target selection first"))
        return {'CANCELLED'}
    if context.window is None or context.area is None or context.area.type != 'VIEW_3D':
        op.report({'WARNING'}, tx("Start this tool in a 3D View"))
        return {'CANCELLED'}
    from . import place_seam as PS
    spec = store.get(context)[0]
    requested = SEL.pieces(context)
    try:
        movers, followers = resolve_movers(context, spec, requested)
        got, why = PS.shapes(context, spec)
        if got is None:
            raise ValueError(why)
        available = {n: scope.partner_names(got, n, movers + followers) for n in movers}
        missing = [n for n, partners in available.items() if not partners]
        if missing:
            raise ValueError(tx("No enabled external seam for: {0}", ', '.join(missing)))
    except (ValueError, RuntimeError) as exc:
        op.report({'WARNING'}, str(exc))
        return {'CANCELLED'}
    op.moving_pieces = json.dumps(movers)
    op.target_pieces = ''  # never reuse F9/operator history as the next operation's scope
    op._pick_state = dict(movers=movers, followers=followers, available=available,
                          candidates=sorted({p for row in available.values() for p in row}), targets=set(),
                          spec_text=store.text(context).as_string(), frame=context.scene.frame_current,
                          window=context.window.as_pointer(), note='', hover=None, handles=[], batches={},
                          area=context.area.as_pointer() if context.area else 0)
    _build_geometry(op, context, got)
    op._pick_state['handles'] = [
        bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (op,), 'WINDOW', 'POST_VIEW'),
        bpy.types.SpaceView3D.draw_handler_add(_draw_text, (op,), 'WINDOW', 'POST_PIXEL')]
    _active = op
    context.workspace.status_text_set(tx("Choose targets: click; Shift-click add/remove; A all; Enter apply; Esc cancel"))
    context.window_manager.modal_handler_add(op)
    _redraw(context)
    return {'RUNNING_MODAL'}


def _build_geometry(op, context, got):
    state = op._pick_state
    pat, tris, xyz = got
    names = list(map(str, pat['names']))
    state['world'], state['flat'], state['seams'] = {}, {}, []
    outlines = store.get(context)[1]
    matrix = PV.layout_matrix(context)
    for name in state['movers'] + state['followers'] + state['candidates']:
        if name in names:
            i = names.index(name)
            t = np.asarray(tris[i])
            edges, counts = np.unique(np.sort(np.vstack((t[:, :2], t[:, 1:], t[:, ::2])), axis=1),
                                      axis=0, return_counts=True)
            state['world'][name] = np.asarray(xyz[i])[edges[counts == 1]].reshape(-1, 3) * .001
        if name in outlines:
            points = PV.to_world(matrix, outlines[name].B)
            state['flat'][name] = np.asarray([tuple(v) for a, b in zip(points, points[1:] + points[:1]) for v in (a, b)])
    for row in np.asarray(pat['stitch']).reshape(-1, 4):
        a, va, b, vb = map(int, row)
        na, nb = names[a], names[b]
        if na in state['movers'] and nb in state['candidates']:
            state['seams'].append((nb, np.array([xyz[a][va], xyz[b][vb]]) * .001))
        elif nb in state['movers'] and na in state['candidates']:
            state['seams'].append((na, np.array([xyz[b][vb], xyz[a][va]]) * .001))


def _draw_lines(op):
    context = bpy.context
    state = getattr(op, '_pick_state', None)
    if not state or context.window.as_pointer() != state['window']:
        return
    world = VV.world_names(context, store.get(context)[0])
    flat = VV.allows_pattern(context)
    visible = {name for name, ob in PV.piece_objects().items() if VV.object_allowed(context, ob)} if flat else set()
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    old_depth, old_blend = gpu.state.depth_test_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('ALPHA')
        shader.bind()
        shader.uniform_float('viewportSize', (context.region.width, context.region.height))
        for name in state['movers'] + state['followers'] + state['candidates']:
            color = ((1., .5, .1, 1.) if name in state['movers'] + state['followers'] else
                     (.2, 1., .45, 1.) if name in state['targets'] else (.65, .7, .75, .55))
            shader.uniform_float('color', color)
            shader.uniform_float('lineWidth', 3. if name in state['targets'] or name in state['movers'] else 1.5)
            for kind, show in (('world', name in world), ('flat', name in visible)):
                if show and name in state[kind]:
                    key = kind, name
                    if key not in state['batches']:
                        state['batches'][key] = batch_for_shader(shader, 'LINES',
                                                                {'pos': np.asarray(state[kind][name], dtype=np.float32)})
                    state['batches'][key].draw(shader)
        if world:
            points = [p for name, segment in state['seams'] if name in state['targets'] and name in world for p in segment]
            if points:
                key = 'seams', tuple(sorted(state['targets']))
                if key not in state['batches']:
                    state['batches'][key] = batch_for_shader(shader, 'LINES', {'pos': np.asarray(points, dtype=np.float32)})
                shader.uniform_float('color', (.2, 1., .45, 1.))
                shader.uniform_float('lineWidth', 2.)
                state['batches'][key].draw(shader)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.blend_set(old_blend)


def _draw_text(op):
    context = bpy.context
    state = getattr(op, '_pick_state', None)
    if not state or context.window.as_pointer() != state['window']:
        return
    # only in the 3D view it was started in (the others in the window showed it too)
    if state.get('area') and (context.area is None or context.area.as_pointer() != state['area']):
        return
    lines = [tx("Moving: {0}", ', '.join(state['movers']))]
    if state['followers']:
        lines.append(tx("Pair follows: {0}", ', '.join(state['followers'])))
    lines += [tx("Targets: {0}", ', '.join(sorted(state['targets'])) or tx("None chosen")),
              tx("Click target; Shift-click add/remove; A all; Enter apply; Esc cancel")]
    if state['note']:
        lines.append(state['note'])
    blf.size(0, 13)
    # clear of the toolbar (it was drawn at x = 24 under it; 2026-11-12)
    from . import ui as U
    from . import text_band
    x, y = U.view_text_origin(context, 150)
    items = []
    for line in lines:
        for text in wrap(line, max(24, int((context.region.width - x) / 7) - 6)):
            items.append((x, y, text, (1., 1., 1., 1.)))
            y -= 20
    text_band.draw(0, items)


def finish(op, context):
    global _active
    state = getattr(op, '_pick_state', None)
    if state:
        for handle in state['handles']:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        op._pick_state = None
    if _active is op:
        _active = None
    context.workspace.status_text_set(None)
    _redraw(context)


def modal(op, context, event):
    state = op._pick_state
    if state is None:
        return {'CANCELLED'}
    if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
        finish(op, context)
        return {'CANCELLED'}
    if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION'}:
        return {'PASS_THROUGH'}
    if event.type == 'A' and event.value == 'PRESS':
        state['targets'] = set(state['candidates'])
        state['note'] = ''
    elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
        name = _pick(context, event)
        if name not in state['candidates']:
            state['note'] = tx("Click a piece with an enabled seam to the moving pieces")
        else:
            if not event.shift:
                state['targets'] = {name}
            elif name in state['targets']:
                state['targets'].remove(name)
            else:
                state['targets'].add(name)
            state['note'] = ''
    elif event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
        if not state['targets']:
            state['note'] = tx("Choose at least one target piece")
        elif store.text(context).as_string() != state['spec_text'] or context.scene.frame_current != state['frame']:
            finish(op, context)
            op.report({'WARNING'}, tx("Garment changed during target selection: start again"))
            return {'CANCELLED'}
        else:
            try:
                movers, _, _, _ = validate(context, state['movers'], state['targets'])
                if op.bl_idname.endswith('lay_on_edge'):
                    from . import place_lay as PLY
                    spec = store.get(context)[0]
                    for name in movers:
                        why = PLY.band_why(scope.filter_spec(spec, name, state['targets']), name)
                        if why:
                            raise ValueError(why)
            except ValueError as exc:
                state['note'] = str(exc)
            else:
                op.target_pieces = json.dumps(sorted(state['targets']))
                finish(op, context)
                return op.execute(context)
    _redraw(context)
    return {'RUNNING_MODAL'}


def unregister():
    if _active is not None:
        finish(_active, bpy.context)
