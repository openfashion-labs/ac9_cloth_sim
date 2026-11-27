# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, read-only diagnosis of the displayed cloth, independent of shape preparation.

The record belongs to a scene and spec Text. Rendering reads this snapshot; a
timer validates its complete geometry fingerprint, never from Panel.draw/poll.
Operation and solver records remain separate from this current-shape snapshot.
"""
import hashlib
import os
import time
from functools import wraps

import bpy
import numpy as np
from mathutils import Vector

from . import store
from .core import result_names as RN
from .core import overlay_policy as OP
from .i18n import tx

_records = {}
_sequence = [0]
NAME = "Diagnose"
DISPLAY_NAME = OP.label("diagnosis_display")
RESULTS_NAME = "Diagnosis Results"
RUN_RECORDS_NAME = "Recorded Run Checks"
DISPLAY_ITEMS = (('NONE', "None", ""), ('PROBLEMS', "Diagnosis Locations", ""),
                 ('STRETCH', "Stretch", ""), ('SIDE', "Face Side", ""))


def world_scope(function):
    """Read view-layer cloth independently of the button's local viewport.

    Passing space_data=None on a proxy is insufficient: Blender's visible_get()
    also inherits the real current viewport when its viewport argument is omitted.
    Keep the override local to inspection; never change either viewport's state.
    """
    @wraps(function)
    def read(context, *args, **kwargs):
        if bpy.context.area is None:
            return function(context, *args, **kwargs)
        with bpy.context.temp_override(area=None, region=None):
            return function(context, *args, **kwargs)
    return read


def owner(context):
    text = store.text(context)
    return (context.scene.as_pointer(), text.as_pointer()) if text is not None else None


def record(context):
    return _records.get(owner(context))


def mode(props):
    return props.diagnosis_display


@world_scope
def sources(context, spec, outlines):
    from . import diagnose as DG, display_state as DS, dress as D, placed_view as PV, piece_look as PL
    from . import view_visibility as VV
    # Validation reads real UV/evaluated geometry, never its display snapshot.
    PV.invalidate_shown()
    garment = D.garment(spec)
    state = DS.snapshot(spec, garment)
    visible = VV.world_names(context, spec, display=state)
    obs = PV.objects(spec)
    shown, _key = PV.shown(context, spec, outlines, include_off=True)
    raw = {}
    for name, e in shown.items():
        if state.get(name, {}).get('source') == 'placed':
            valid_flat = obs.get(name) is not None and obs[name].data.attributes.get(PV.FLAT_ATTR) is not None
            raw[name] = (e[0] if valid_flat else None, e[1], e[2], len(e[3]) > 2 and e[3][2] == 'twin')
    if garment is not None and any(n in visible and v['source'] == 'result' for n, v in state.items()):
        key = DS.job(garment) or 'blend:' + garment.name
        topology = PL._garment_topo(garment, key)
        world, _sig = PL._garment_co(context, garment, key)
        flat = DG._garment_flat(garment)
        for name, t in topology.items():
            if state.get(name, {}).get('source') == 'result':
                raw[name] = (flat, t['tri'], world, False)
    out = {}
    for name, (flat, tri, world, turned) in raw.items():
        if name not in visible or not state.get(name, {}).get('solid'):
            continue
        tri = np.asarray(tri, np.int64)
        ids, inverse = np.unique(tri.ravel(), return_inverse=True)
        flat_ok = flat is not None
        flat = np.asarray(flat, float)[ids].copy() if flat_ok else np.zeros((len(ids), 2))
        world = np.asarray(world, float)[ids].copy()
        local = inverse.reshape(-1, 3)
        if not len(local) or not np.isfinite(world).all():
            raise ValueError(tx("The displayed cloth cannot be read"))
        flat_ok = flat_ok and bool(np.isfinite(flat).all())
        if not flat_ok:
            flat[:] = 0.
        source = state[name]['source']
        ob = garment if source == 'result' else obs.get(name)
        if ob is None or context.scene.objects.get(ob.name) != ob:
            raise ValueError(tx("The displayed cloth cannot be read"))
        out[name] = dict(V=flat, T=local, Y=world * 1000.0, W=world, ids=ids,
                         owner=ob, source=source, turned=turned, flat_checked=flat_ok)
    return out


@world_scope
def fingerprint(context, spec, shapes):
    from . import placement as PL, dress as D
    from .core import spec as S
    h = hashlib.sha256()
    h.update(S.dumps(spec).encode('utf8'))
    h.update(str(context.scene.frame_current).encode())
    h.update(str(context.scene.ac9_cloth_sim.mesh_size).encode())
    h.update(repr(start_signature(context, spec)).encode())
    for name, f in sorted(shapes.items()):
        h.update((name + ':' + f['source'] + ':' + str(f['owner'].session_uid)).encode())
        h.update(str(f['flat_checked']).encode())
        for key in ('V', 'T', 'Y', 'ids'):
            h.update(np.ascontiguousarray(f[key]).tobytes())
    body = context.scene.ac9_cloth_sim.body
    h.update(str((body.session_uid, body.data.session_uid) if body is not None else None).encode())
    co, tri = PL.body_arrays(context, body)
    h.update(np.ascontiguousarray(co).tobytes())
    h.update(np.ascontiguousarray(tri).tobytes())
    cols, arrays = D.collider_arrays(context, exclude=D.garment(spec))
    for i, (co, tri) in enumerate(arrays):
        h.update(str(cols[i].session_uid if i < len(cols) else 'floor').encode())
        h.update(np.ascontiguousarray(co).tobytes())
        h.update(np.ascontiguousarray(tri).tobytes())
    return h.hexdigest()


def start_signature(context, spec):
    from . import dress as D, display_state as DS
    g = D.garment(spec)
    if g is None:
        return None
    cache = tuple((m.filepath, m.frame_start, m.show_viewport) for m in g.modifiers if m.type == 'MESH_CACHE')
    job = DS.job(g)
    paths = [bpy.path.abspath(m.filepath, library=g.library) for m in g.modifiers if m.type == 'MESH_CACHE' and m.filepath]
    if job:
        paths += [os.path.join(job, name) for name in ('start_check.json', RN.INDEX, RN.JOB, RN.SPEC)]
    files = []
    for path in paths:
        try:
            st = os.stat(path)
            files.append((path, st.st_size, st.st_mtime_ns))
        except OSError:
            files.append((path, None, None))
    return (str(g.get('ac9_job', '')), cache, tuple(files))


@world_scope
def cheap_stamp(context, spec):
    from . import dress as D, display_state as DS, view_visibility as VV, placed_view as PV
    props = context.scene.ac9_cloth_sim
    body = props.body
    garment = D.garment(spec)
    cols = D.collider_objects(context, exclude=garment)
    state = DS.snapshot(spec, garment)
    visible = VV.world_names(context, spec, display=state)
    obs = PV.objects(spec)
    view = tuple((n, v['source'], v['solid'], v['hidden'], n in visible,
                  (garment if v['source'] == 'result' else obs.get(n)).session_uid
                  if (garment if v['source'] == 'result' else obs.get(n)) is not None else None)
                 for n, v in sorted(state.items()))
    data_ids = tuple(sorted((ob.session_uid, ob.data.session_uid) for ob in {f for f in obs.values()} | ({garment} if garment else set())))
    return (store._cache['key'], context.scene.frame_current, context.mode, float(props.mesh_size), bool(props.floor),
            (body.session_uid, body.data.session_uid) if body else None,
            tuple((o.session_uid, o.data.session_uid) for o in cols), view, data_ids, start_signature(context, spec))


@world_scope
def dependencies(context, spec, shapes):
    from . import dress as D
    obs = {f['owner'] for f in shapes.values()}
    if context.scene.ac9_cloth_sim.body is not None:
        obs.add(context.scene.ac9_cloth_sim.body)
    obs.update(D.collider_objects(context, exclude=D.garment(spec)))
    return {x.session_uid for ob in obs for x in (ob, ob.data) if x is not None}


def selected(context, spec, shapes):
    from . import placement as PL
    names = PL.with_pairs(spec, PL.selected_pieces(context))
    # Do not silently replace an unavailable explicit selection with all cloth.
    return sorted(names & shapes.keys()) if names else sorted(shapes)


def seam_masks(context, spec, shapes):
    """Read direct sewing edges; uncertainty belongs to an individual piece pair."""
    from . import cloth_seams
    return cloth_seams.read(context, spec, shapes)


def _cloth_spots(f, *, candidates=False):
    """One marker per connected surface patch, interpolated on its own triangle.

    The two sides of a self intersection share a 3D point but have separate
    material coordinates. Only crossed faces connect patches, not nearby edges.
    """
    from . import place_check as C
    faces = f['candidate_t'] if candidates else f['cross_t']
    parent = {int(i): int(i) for i in faces}
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    vertex_face = {}
    for i in parent:
        for v in f['T'][i]:
            if int(v) in vertex_face:
                a, b = find(i), find(vertex_face[int(v)])
                parent[a] = b
            else:
                vertex_face[int(v)] = i
    groups = {}
    for i in parent:
        groups.setdefault(find(i), []).append(i)
    events = {}
    for e in f['intersection_points']:
        if e['candidate'] == candidates:
            events.setdefault(find(e['face']), []).append(e)
    out = []
    for root, group in sorted(groups.items(), key=lambda item: -len(item[1]))[:C.SPOT_MAX]:
        center = f['Y'][f['T'][group].ravel()].mean(0)
        e = min(events[root], key=lambda event: np.linalg.norm(np.asarray(event['world']) - center))
        out.append(dict(c=int(f['T'][e['face'], 0]), world=e['world'], f=(np.asarray(e['flat']) * .001).tolist(),
                        **{'in': 0, 'x': len(group)}))
    return out


def measure(shapes, names, bvh, skip, masks):
    """Geometry only; all arrays use the same per-piece local numbering."""
    from . import place_check as C, diagnose as DG
    from .core.cloth_intersection import intersection, direct_sewn
    from itertools import combinations_with_replacement
    trees = {n: C._bvh(f['Y'], f['T']) for n, f in shapes.items()}
    degenerate = {}
    for n, f in shapes.items():
        y, t = f['Y'], f['T']
        area2 = np.linalg.norm(np.cross(y[t[:, 1]] - y[t[:, 0]], y[t[:, 2]] - y[t[:, 0]]), axis=1)
        degenerate[n] = int((area2 <= 1e-12).sum())
    found = {}
    for name in names:
        f = dict(shapes[name])
        Y, T, V = f['Y'], f['T'], f['V']
        f['inside'] = C.inside(bvh, Y, skip(name))
        f['body_checked'] = bvh is not None
        f['degenerate_faces'] = degenerate[name]
        f['unchecked_geometry'] = {n: count for n, count in degenerate.items() if count}
        f.update(cross_t=set(), self_t=set(), candidate_t=set(),
                 partners={}, unchecked_partners=set(), self_segments=[], intersection_points=[])
        u, v = V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]
        valid = np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]) > 1e-9
        f['stretch_checked'] = f.get('flat_checked', True) and bool(valid.all())
        f['invalid_material_faces'] = int((~valid).sum())
        l1, l2 = DG.stretches(V, T[valid], Y)
        f['stretch'] = float(l1.max()) if len(l1) else 1.0
        f['median'] = float(np.median(l1)) if len(l1) else 1.0
        # Resolve the representative face during diagnosis, never from Panel.draw.
        f['stretch_face'] = (int(np.flatnonzero(valid)[int(np.argmax(l1))])
                             if f['stretch_checked'] and len(l1) and np.isfinite(l1).all() else None)
        found[name] = f
    face_vertices = {n: [frozenset(map(int, tri)) for tri in f['T']] for n, f in shapes.items()}
    for an, bn in combinations_with_replacement(sorted(shapes), 2):
        if not ({an, bn} & found.keys()) or trees[an] is None or trees[bn] is None:
            continue
        fa, fb = shapes[an], shapes[bn]
        pair = (an, bn)
        links = masks['pairs'].get(pair, set()) if masks is not None else set()
        uncertain = masks is None or pair in masks['unknown']
        if an == bn:
            links = links | {(b, a) for a, b in links}
        for ia, ib in trees[an].overlap(trees[bn]):
            ta, tb = fa['T'][ia], fb['T'][ib]
            if an == bn and (ia >= ib or not face_vertices[an][ia].isdisjoint(face_vertices[bn][ib])):
                continue
            if direct_sewn(ta, tb, links):
                continue
            if an != bn and fa.get('owner') is not None and fa.get('owner') == fb.get('owner') and fa.get('source') == fb.get('source') == 'result':
                if not set(fa['ids'][ta]).isdisjoint(fb['ids'][tb]):
                    continue
            hit = intersection(fa['Y'][ta], fb['Y'][tb])
            if hit is None:
                continue
            kind, points = hit
            for name, other, face in ((an, bn, ia), (bn, an, ib)):
                if name not in found:
                    continue
                f = found[name]
                point = points.mean(0)
                vertices = f['T'][face]
                y = f['Y'][vertices]
                weights = np.linalg.lstsq(np.column_stack((y[1] - y[0], y[2] - y[0])), point - y[0], rcond=None)[0]
                uv = f['V'][vertices]
                flat = uv[0] + weights[0] * (uv[1] - uv[0]) + weights[1] * (uv[2] - uv[0])
                f['intersection_points'].append(dict(face=int(face), world=point.tolist(), flat=flat.tolist(), candidate=uncertain))
                if uncertain:
                    f['candidate_t'].add(int(face))
                    f['unchecked_partners'].add(other)
                else:
                    f['cross_t'].add(int(face))
                    if an == bn:
                        f['self_t'].add(int(face))
                    else:
                        f['partners'].setdefault(other, set()).add(int(face))
            if an == bn and an in found and not uncertain and kind == 'cross':
                found[an]['self_segments'].append(points.tolist())
    for name, f in found.items():
        for field in ('cross_t', 'self_t', 'candidate_t'):
            f[field] = np.asarray(sorted(f[field]), np.int64)
        f['partners'] = {n: sorted(v) for n, v in f['partners'].items()}
        f['cross_checked'] = not f['unchecked_partners'] and not f['unchecked_geometry']
        f['unchecked_partners'] = sorted(f['unchecked_partners'])
        f['spots'] = C.spots(name, dict(f, cross_t=np.empty(0, np.int64))) + _cloth_spots(f)
        f['candidate_spots'] = _cloth_spots(f, candidates=True)
    return found


@world_scope
def run(context, spec):
    from . import place_check as C, garment_view as G, placement as PL, dress as D, display_state as DS
    _spec, outlines = store.get(context)
    shapes = sources(context, spec, outlines)
    names = selected(context, spec, shapes)
    bvh, skip = C.body(context, spec)
    found = measure(shapes, names, bvh, skip, seam_masks(context, spec, shapes))
    props = context.scene.ac9_cloth_sim
    body_requested = props.body is not None or props.floor or bool(D.collider_objects(context, exclude=D.garment(spec)))
    for f in found.values():
        f['body_applicable'] = body_requested
    _sequence[0] += 1
    rec = dict(serial=_sequence[0], found=found, display_shapes=shapes, all_names=sorted(shapes), stale=False, pending=False,
               fingerprint=fingerprint(context, spec, shapes), frame=context.scene.frame_current,
               time=time.strftime('%H:%M:%S'), start=G.check_lines(context, spec),
               checked_at=time.monotonic())
    requested = PL.with_pairs(spec, PL.selected_pieces(context))
    rec['selected_scope'] = bool(requested)
    scope = requested if requested else {n for n, state in DS.snapshot(spec).items() if state['solid']}
    rec['unavailable'] = sorted(scope - shapes.keys())
    rec['cheap'] = cheap_stamp(context, spec)
    rec['dependencies'] = dependencies(context, spec, shapes)
    _records[owner(context)] = rec
    return rec


def dirty(scene=None, depsgraph=None):
    ids = set()
    if depsgraph is not None:
        ids = {u.id.original.session_uid for u in depsgraph.updates if u.is_updated_geometry or u.is_updated_transform}
    for key, rec in _records.items():
        if scene is None or key[0] == scene.as_pointer():
            if depsgraph is None or rec['dependencies'] & ids:
                rec['pending'] = True


def validate(context):
    rec = record(context)
    if rec is None or rec['stale']:
        return
    if context.mode != 'OBJECT':
        rec['stale'] = True
        return
    was_pending = rec['pending']
    try:
        spec, outlines = store.get(context)
        shapes = sources(context, spec, outlines)
        rec['stale'] = fingerprint(context, spec, shapes) != rec['fingerprint']
        if not rec['stale']:
            # A dependency can be replaced without changing the measured surface.
            # A successful comparison must adopt its new monitoring generation.
            rec['cheap'] = cheap_stamp(context, spec)
            rec['dependencies'] = dependencies(context, spec, shapes)
    except (ValueError, RuntimeError, ReferenceError, KeyError, IndexError):
        rec['stale'] = True
    rec['pending'] = False
    if was_pending or rec['stale']:
        for area in context.screen.areas if context.screen else ():
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def timer():
    rec = record(bpy.context) if _records else None
    if rec is not None and not rec['stale']:
        try:
            spec, _ol = store.get(bpy.context)
            if rec['pending'] or cheap_stamp(bpy.context, spec) != rec['cheap']:
                validate(bpy.context)
        except (ValueError, KeyError, ReferenceError, RuntimeError, AttributeError):
            rec['stale'] = True
    return 0.5


def current(context):
    rec = record(context)
    if rec is not None and (rec['frame'] != context.scene.frame_current or context.mode != 'OBJECT'):
        rec['stale'] = True
    return rec if rec is not None and not rec['stale'] and not rec['pending'] else None


def display_pieces(context, spec):
    """Reuse a validated snapshot for color display; validation never reads this cache."""
    from . import view_visibility as VV
    rec = current(context)
    if rec is None:
        return None
    visible = VV.world_names(context, spec)
    pieces = {n: (f['V'], f['T'], f['W'], f['turned']) for n, f in rec['display_shapes'].items() if n in visible}
    return pieces, ('diagnosis', rec['serial'], tuple(sorted(pieces)))


def stretch_peak(rec):
    """One reproducible maximum among completely checked pieces in this diagnosis scope."""
    rows = [(n, f) for n, f in rec['found'].items()
            if f['stretch_checked'] and f.get('stretch_face') is not None and np.isfinite(f['stretch'])]
    return min(rows, key=lambda row: (-row[1]['stretch'], row[0])) if rows else None


def stretch_lines(rec):
    peak = stretch_peak(rec)
    if peak is None:
        return [tx("Stretch: not checked")]
    name, f = peak
    lines = [tx("Maximum stretch: x{0} ({1})", f"{f['stretch']:.2f}", name)]
    missing = (sum(not f['stretch_checked'] or f.get('stretch_face') is None for f in rec['found'].values())
               + len(rec.get('unavailable', ())))
    if missing:
        lines.append(tx("Checked pieces only; stretch not checked: {0} pieces", missing))
    return lines


def stretch_summary(rec):
    return '; '.join(stretch_lines(rec))


def stretch_spot(context, spec):
    """Maximum face centroid in cloth and matching material coordinates; never a live fallback."""
    from . import pattern_view as PV, diagnose as DG
    rec = current(context)
    peak = stretch_peak(rec) if rec is not None else None
    if peak is None:
        return None
    name, f = peak
    if f['stretch'] <= DG.OK:
        return None
    ids = f['T'][f['stretch_face']]
    world = Vector(f['Y'][ids].mean(0) * .001)
    u, v = f['V'][ids].mean(0) * .001
    flat = PV.layout_matrix(context) @ Vector((u, v, 0))
    return f['owner'], name, world, flat, f['stretch']


def summary_lines(rec):
    found = rec['found']
    if not found:
        return [(tx("Selected scope: no displayed 3D shapes; not checked") if rec.get('selected_scope') else
                 tx("No displayed 3D shapes; not checked"))]
    inside = sum(int(f['inside'].sum()) for f in found.values())
    cross = sum(len(f['cross_t']) for f in found.values())
    body = (tx("Body/obstacles: no collision surfaces (not applicable)")
            if not any(f.get('body_applicable', True) for f in found.values()) else
            tx("Inside body or obstacles: {0} vertices", inside) if all(f['body_checked'] for f in found.values()) else
            tx("Body/obstacles: not checked (no readable collision surface)"))
    cloth = tx("Confirmed cloth intersections: {0} faces", cross)
    lines = [body, cloth]
    self_count = sum(len(f['self_t']) for f in found.values())
    candidates = sum(len(f['candidate_t']) for f in found.values())
    if self_count:
        lines.append(tx("Self crossing: {0} faces", self_count))
    if candidates:
        lines.append(tx("Crossing candidates: {0} faces (sewing unverified)", candidates))
    degenerate = sum({n: count for f in found.values() for n, count in f['unchecked_geometry'].items()}.values())
    if degenerate:
        lines.append(tx("Degenerate faces not checked: {0}", degenerate))
    scope = (tx("Checked {0} selected pieces (including linked pairs)", len(found)) if rec.get('selected_scope') else
             tx("Checked all {0} displayed pieces", len(found)))
    return [scope] + lines + stretch_lines(rec)


def summary(rec):
    lines = summary_lines(rec)
    return lines[0] + (': ' + '; '.join(lines[1:]) if len(lines) > 1 else '')


def notice(rec):
    """One operation report: the measured scope/result, plus actionable start warnings."""
    warnings = sum(level in {'WARNING', 'ERROR'} for level, _text, _args in rec['start'])
    return summary(rec) + ('; ' + tx("Start-shape warnings: {0}; see Start-Shape Notes", warnings) if warnings else '')


def draw_display(layout, context):
    """One four-choice selector, at the same place before and after diagnosis."""
    props = context.scene.ac9_cloth_sim
    layout.label(text=tx(DISPLAY_NAME))
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop_enum(props, 'diagnosis_display', 'PROBLEMS', text=tx("Diagnosis Locations"))
    row.prop_enum(props, 'diagnosis_display', 'STRETCH', text=tx("3D Stretch Map"))
    row = col.row(align=True)
    row.prop_enum(props, 'diagnosis_display', 'SIDE', text=tx("Face Side"))
    row.prop_enum(props, 'diagnosis_display', 'NONE', text=tx("None"))
    if not props.show_overlays:
        layout.prop(props, 'show_overlays')


def draw_result(layout, context, spec, details=False):
    from . import ui as U, diagnose as DG
    rec = record(context)
    draw_display(layout, context)
    if rec is None:
        U.draw_state(layout, context, tx("Not diagnosed yet"), icon='INFO')
    else:
        U.draw_state(layout, context, tx("Diagnosis at {0}, frame {1}", rec['time'], rec['frame']))
        if rec['stale']:
            U.draw_state(layout, context, tx("Changed since Diagnose: press it again"), icon='ERROR')
        elif rec['pending']:
            U.draw_state(layout, context, tx("Checking whether the diagnosis is still current"))
        for line in summary_lines(rec):
            U.draw_state(layout, context, line)
        if rec['unavailable']:
            U.draw_state(layout, context, tx("No displayed shape, not checked: {0}", ', '.join(rec['unavailable'])))
        U.draw_state(layout, context, tx("Stretch map: displayed cloth. Values and diamond: at diagnosis."))
        layout.prop(context.scene.ac9_cloth_sim, 'diagnosis_details')
        if rec['start']:
            notes = layout.box()
            notes.label(text=tx("Start-Shape Notes"))
            for level, text, args in rec['start']:
                U.draw_state(notes, context, tx(text, *args), icon='ERROR' if level in {'WARNING', 'ERROR'} else 'INFO')
        if details or context.scene.ac9_cloth_sim.diagnosis_details:
            if not rec['start']:
                U.draw_state(layout, context, tx("No start-shape warnings found"))
            for name, f in rec['found'].items():
                box = layout.box()
                U.draw_state(box, context, name)
                box.label(text=tx("Current displayed shape"))
                body = (tx("Body/obstacles: no collision surfaces (not applicable)") if not f.get('body_applicable', True)
                        else tx("Inside body or obstacles: {0} vertices", int(f['inside'].sum())) if f['body_checked']
                        else tx("Body/obstacles: not checked (no readable collision surface)"))
                U.draw_state(box, context, body)
                U.draw_state(box, context, tx("Confirmed cloth intersections: {0} faces", len(f['cross_t'])))
                if len(f['self_t']):
                    U.draw_state(box, context, tx("Self crossing: {0} faces", len(f['self_t'])))
                if len(f['candidate_t']):
                    U.draw_state(box, context, tx("Crossing candidates: {0} faces (sewing unverified)", len(f['candidate_t'])))
                    U.draw_state(box, context, tx("Sewing unverified with: {0}", ', '.join(f['unchecked_partners'])))
                if f['degenerate_faces']:
                    U.draw_state(box, context, tx("Degenerate faces not checked: {0}", f['degenerate_faces']))
                if set(f['unchecked_geometry']) - {name}:
                    U.draw_state(box, context, tx("Other pieces with degenerate faces: {0}", ', '.join(sorted(set(f['unchecked_geometry']) - {name}))))
                for other in f['partners']:
                    U.draw_state(box, context, tx("Crosses {0}", other))
                U.draw_state(box, context, tx("Stretch: median x{0}, maximum x{1}", f"{f['median']:.2f}", f"{f['stretch']:.2f}")
                             if f['stretch_checked'] else tx("Stretch: not checked (pattern coordinates missing or degenerate)"))
            U.draw_state(layout, context, tx("Not checked: sewing correctness and coplanar contact"))
            history = [(DG.KINDS.get(k, k), r) for ob, _n, rs in DG._problem_objects(spec)
                       for k, r in rs.items() if k in {'start', 'layer', 'anomaly', 'unsettled'}]
            if history:
                layout.label(text=tx(RUN_RECORDS_NAME))
                for text, _r in history:
                    U.draw_state(layout, context, tx(text))
                    if _r.get('verts') is not None:
                        layout.label(text=tx("Recorded vertices: {0}", len(_r['verts'])))


def problem_lines(context):
    from . import view_visibility as VV
    rec = current(context)
    if rec is None:
        return []
    out = []
    spec, _ol = store.get(context)
    visible = VV.world_names(context, spec) if VV.allows_world(context, spec) else VV.pattern_names(context, spec)
    for n, f in rec['found'].items():
        if n not in visible:
            continue
        if f['inside'].any():
            out.append(tx("{0}: inside body or obstacles {1}", n, int(f['inside'].sum())))
        for other in f['partners']:
            out.append(tx("{0}: crosses {1}", n, other))
        if len(f['self_t']):
            out.append(tx("{0}: self crossing {1} faces", n, len(f['self_t'])))
        if len(f['candidate_t']):
            out.append(tx("{0}: crossing candidates {1} faces (sewing unverified)", n, len(f['candidate_t'])))
    return out[:4] + ([tx("{0} more findings in Diagnosis Results", len(out) - 4)] if len(out) > 4 else [])


def spot_points(context, spec, *, candidates=False):
    from . import pattern_view as PV
    rec = current(context)
    if rec is None:
        return []
    L = PV.layout_matrix(context)
    out = []
    for name, f in rec['found'].items():
        for s in f['candidate_spots'] if candidates else f['spots']:
            c = s['c']
            u, v = s['f']
            # No pattern marker can be asserted when the material correspondence is missing.
            out.append((f['owner'], name if f['flat_checked'] else None, Vector(np.asarray(s.get('world', f['Y'][c])) * .001),
                        L @ Vector((u, v, 0)), s['in'], s['x']))
    return out


def problem_geometry(context, *, candidates=False):
    from . import view_visibility as VV
    rec = current(context)
    if rec is None:
        return None
    points = []
    spec, _outlines = store.get(context)
    visible = VV.world_names(context, spec)
    for name, f in rec['found'].items():
        if name not in visible:
            continue
        faces = np.zeros(len(f['T']), bool) if candidates else f['inside'][f['T']].any(1)
        faces[f['candidate_t'] if candidates else f['cross_t']] = True
        tri = f['T'][faces]
        if len(tri):
            points.append(f['Y'][tri.ravel()] * .001)
    return np.ascontiguousarray(np.concatenate(points), np.float32) if points else None


@bpy.app.handlers.persistent
def clear_records(*_args):
    _records.clear()


def register():
    _records.clear()
    for handler in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if clear_records not in handler:
            handler.append(clear_records)
    if not bpy.app.timers.is_registered(timer):
        bpy.app.timers.register(timer, first_interval=0.5, persistent=True)


def unregister():
    for handler in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if clear_records in handler:
            handler.remove(clear_records)
    if bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)
    _records.clear()
