# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure: the body's measurements (girths drawn round the body, levels the user can slide), tapes the user lays
on the body (round it, or along its surface through clicked points), the dressed garment's girths at the body's
levels with the ease (garment - body), and lengths taken on the flat pattern.

Where things are kept:
- on the body object: ob['ac9_measure'] (JSON: the values, the user's level offsets, the tapes' definitions) and
  ob['ac9_measure_geo'] (JSON: the lines drawn, body-local mm). The body's measurements belong to the body, not
  to a garment, so they need no spec.
- on the garment object: ob['ac9_measure'] (the garment's girths and ease, world mm lines).
- in the spec: spec['measure'] = {'targets': {key: mm}, 'pattern': [{'name', 'a': {...}, 'b': {...}}]} -- the
  finished sizes the user typed in, and the pattern lengths (outline points by edge and t, so they follow edits).
"""
import json
from .i18n import CTX, rpt, tx

import blf
import bpy
import gpu
import numpy as np
from bpy.props import EnumProperty, FloatProperty, IntProperty, PointerProperty, BoolProperty, StringProperty
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_origin_3d, region_2d_to_vector_3d
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from . import dress
from . import pattern_view as PV
from . import placement
from . import store
from . import toolbar as TB
from . import view_visibility as VV
from .core import body_axes as BA
from .core import measure as ME
from .core import spec as S

PROP = 'ac9_measure'
GEO = 'ac9_measure_geo'
PICK_PX = 14

LABEL = {'height': "Height", 'neck_height': "Neck height", 'crotch_height': "Crotch height",
         'back_length': "Back length", 'neck': "Neck", 'chest': "Bust", 'waist': "Waist", 'hip': "Hip",
         'upper_arm_L': "Upper arm L", 'upper_arm_R': "Upper arm R", 'wrist_L': "Wrist L", 'wrist_R': "Wrist R",
         'thigh_L': "Thigh L", 'thigh_R': "Thigh R", 'knee_L': "Knee L", 'knee_R': "Knee R"}

# the hovered / active item, for the overlay
_ui = {'hot': None, 'preview': None, 'preview_label': None,
       'preview_domain': None, 'preview_owner': None, 'preview_pieces': ()}


def _cm(v):
    return "—" if v is None else f"{v / 10.0:.1f}"


def _signed_cm(v):
    return "—" if v is None else f"{v / 10.0:+.1f}"


# ---------------------------------------------------------------- storage

def read(ob, key=PROP):
    if ob is None or key not in ob:
        return {}
    try:
        return json.loads(ob[key])
    except (ValueError, TypeError):
        return {}


def write(ob, data, key=PROP):
    ob[key] = json.dumps(data, ensure_ascii=False)


def _mat(ob):
    M = np.array(ob.matrix_world)
    return M, np.linalg.inv(M)


def _to_local(ob, P_world_mm):
    _, Mi = _mat(ob)
    P = np.asarray(P_world_mm, float) * 0.001
    return ((np.c_[P, np.ones(len(P))] @ Mi.T)[:, :3] * 1000.0)


def _dir_local(ob, d):
    _, Mi = _mat(ob)
    v = Mi[:3, :3] @ np.asarray(d, float)
    return v / np.linalg.norm(v)


def _to_world(ob, P_local_mm):
    M, _ = _mat(ob)
    P = np.asarray(P_local_mm, float) * 0.001
    return (np.c_[P, np.ones(len(P))] @ M.T)[:, :3]          # meters, for drawing


def _dir_world(ob, d):
    M, _ = _mat(ob)
    v = M[:3, :3] @ np.asarray(d, float)
    return v / np.linalg.norm(v)


class BodyCache:
    """The body mesh (world mm) with its guides, built once per tool run."""

    def __init__(self, context, ob):
        co, tri = placement.body_arrays(context, ob)
        self.ob = ob
        self.body = BA.Body(co, tri)
        self.gd, self.m = BA.guides(self.body)
        self._planes = None

    @property
    def planes(self):
        """The levels Measure Body finds (core/measure._planes), found once: a drag recomputes one line only."""
        if self._planes is None:
            self._planes = ME._planes(self.body, self.gd, self.m)
        return self._planes


_cache = {'key': None, 'cache': None}


def body_cache(context, ob, fresh=False):
    """The body's BodyCache, kept while the body is the same object with the same mesh and place (2026-10-21: it was
    built on every press, 0.9 s on a 31k-vertex body). fresh: build it again (Measure Body)."""
    key = (ob.name, ob.data.as_pointer(), len(ob.data.vertices), tuple(round(x, 6) for r in ob.matrix_world for x in r),
           ob.data.shape_keys.key_blocks.active.value if ob.data.shape_keys and ob.data.shape_keys.key_blocks else None)
    if fresh or _cache['key'] != key or _cache['cache'] is None or _cache['cache'].ob != ob:
        _cache.update(key=key, cache=BodyCache(context, ob))
    return _cache['cache']


def _body(context):
    return context.scene.ac9_cloth_sim.body


def _round(P, nd=1):
    return np.round(np.asarray(P, float), nd).tolist()


def store_body(ob, res, data, merge=False):
    """Values (and the definitions the overlay needs) to ob['ac9_measure'], lines to ob['ac9_measure_geo']. merge:
    res holds some of the items only (the rest are kept)."""
    old = read(ob, GEO)
    items = dict(data.get('items', {})) if merge else {}
    geo = dict(old.get('items', {})) if merge else {}
    for k, it in ME.ordered(res):
        items[k] = {'kind': it['kind'], 'value': round(float(it['value']), 2),
                    'surface': round(float(it['surface']), 2) if 'surface' in it else None,
                    'o': _round(_to_local(ob, [it['o']])[0], 2), 'n': np.round(_dir_local(ob, it['n']), 6).tolist(),
                    'offset': it.get('offset', 0.0)}
        geo[k] = {'pts': _round(_to_local(ob, it['pts'])), 'closed': it['kind'] == 'girth'}
    if merge:                                      # keep the order of the full measurement
        order = [k for k, _, _ in ME.ITEMS]
        items = {k: items[k] for k in sorted(items, key=lambda k: order.index(k) if k in order else 99)}
    data['items'] = items
    old['items'] = geo
    write(ob, data)
    write(ob, old, GEO)


def measure_body(context, ob, data, cache=None, only=None, fresh=False):
    """Measure the body (only: these keys, the others kept as they are) and store it."""
    cache = cache or body_cache(context, ob, fresh=fresh)
    res = ME.measure_body(cache.body, cache.gd, cache.m, data.get('offsets', {}), only=only, planes=cache.planes)
    store_body(ob, res, data, merge=only is not None)
    return res, cache


# ---------------------------------------------------------------- tapes

def tape_compute(cache, t):
    """A tape from its definition (body-local mm): (length, extra, world-mm polyline) or None."""
    ob = cache.ob
    M, _ = _mat(ob)
    if t['kind'] == 'around':
        p = (M @ np.r_[np.asarray(t['p'], float) * 0.001, 1.0])[:3] * 1000.0
        n = _dir_world(ob, t['n'])
        r = ME.tape_around(cache.body, p, n)
        return None if r is None else (r[0], r[1], r[2])
    P = (np.c_[np.asarray(t['pts'], float) * 0.001, np.ones(len(t['pts']))] @ M.T)[:, :3] * 1000.0
    if t['kind'] == 'straight':                      # pulled taut between two points: over the bulges, across
        N = [_dir_world(ob, n) for n in t['normals']] if t.get('normals') else [None, None]   # the hollows (10/21)
        return ME.tape_taut(cache.body, P[0], P[-1], N[0], N[-1])
    N = [_dir_world(ob, n) for n in t['normals']]
    L, straight, poly = ME.tape_along(cache.body, P, N)
    return L, straight, poly


def store_tape(ob, cache, t, data):
    r = tape_compute(cache, t)
    if r is None:
        return False
    t['value'], t['extra'] = round(float(r[0]), 2), round(float(r[1]), 2)
    data.setdefault('tapes', []).append(t) if t not in data.get('tapes', []) else None
    geo = read(ob, GEO)
    geo.setdefault('tapes', {})[t['name']] = {'pts': _round(_to_local(ob, r[2])), 'closed': t['kind'] == 'around',
                                              **({'straight': True} if t['kind'] == 'straight' else {})}
    data['tapes'] = [t if x['name'] == t['name'] else x for x in data['tapes']]
    write(ob, data)
    write(ob, geo, GEO)
    return True


def _next_tape_name(data):
    names = {t['name'] for t in data.get('tapes', [])}
    i = 1
    while f"Tape {i}" in names:
        i += 1
    return f"Tape {i}"


def _plane_normal(cache, p, mode):
    """The plane a tape round the body lies in, through the point p (world mm)."""
    if mode == 'HORIZONTAL':
        return np.array([0.0, 0.0, 1.0])
    if mode == 'FRONT':
        return np.array([1.0, 0.0, 0.0])
    if mode == 'SIDE':
        return np.array([0.0, 1.0, 0.0])
    best, bd = None, None
    for name, g in cache.gd.items():                          # the guide whose wall is nearest: its axis
        if 'full' in g:
            o, ez = np.asarray(g['full']['o'], float), np.asarray(g['full']['R'], float)[2]
        else:
            o, ez = np.asarray(g['o'], float), np.asarray(g['R'], float)[2]
        d = p - o
        h = d @ ez
        rad = np.linalg.norm(d - h * ez)
        if name.startswith('arm'):
            hs = (np.asarray(g['o'], float) - o) @ ez
            if h > hs + 0.5 * g['r']:                         # above the shoulder end: the torso's
                continue
        elif not (g['h0'] - 20 <= (p - np.asarray(g['o'], float)) @ np.asarray(g['R'], float)[2] <= g['h1'] + 20):
            continue
        dd = abs(rad - g['r'])
        if bd is None or dd < bd:
            best, bd = ez, dd
    return np.array([0.0, 0.0, 1.0]) if best is None else best


# ---------------------------------------------------------------- the garment

def garment_object(context):
    spec, _ = store.get(context)
    ob = bpy.data.objects.get(dress.garment_name(spec)) if spec else None
    if ob is None:
        a = context.active_object
        if a is not None and a.type == 'MESH' and a.name.startswith(dress.GARMENT_PREFIX):
            ob = a
    return ob


def garment_mesh(context, ob, result=False):
    """The garment as a Body in world mm: on screen (the frame shown), or result=True the result of the last run
    (the mesh: Dress leaves the last frame in it)."""
    if result:
        if ob.mode == 'EDIT':
            ob.update_from_editmode()
        X = np.empty(len(ob.data.vertices) * 3)
        ob.data.vertices.foreach_get('co', X)
        X = X.reshape(-1, 3)
    else:
        X = dress.shown_shape(context, ob) * 0.001
    M = np.array(ob.matrix_world)
    X = (np.c_[X, np.ones(len(X))] @ M.T)[:, :3] * 1000.0
    me = ob.data
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get('vertices', tri)
    return BA.Body(X, tri.reshape(-1, 3))


# ---------------------------------------------------------------- pattern lengths

def _pattern_rows(spec, outlines):
    """[(row, straight, along)] for spec['measure']['pattern'] (rows whose pieces are gone are skipped)."""
    out = []
    for r in spec.get('measure', {}).get('pattern', []):
        a, b = r['a'], r['b']
        if a['piece'] not in outlines or b['piece'] not in outlines:
            continue
        oa, ob_ = outlines[a['piece']], outlines[b['piece']]
        try:
            sa, sb = oa.s_of(a['edge'], a['t']), ob_.s_of(b['edge'], b['t'])
        except KeyError:
            continue
        st, al = ME.pattern_distance(oa, sa, ob_, sb)
        out.append((r, st, al, oa.at(sa), ob_.at(sb)))
    return out


# ---------------------------------------------------------------- operators

class AC9_OT_MeasureBody(bpy.types.Operator):
    """Measure the body: heights, girths (a tape measure round the bust, waist, hip, neck, arms and legs) and the
    back length. The girths are drawn round the body. The avatar must stand upright, facing -Y"""
    bl_idname = "ac9_cloth_sim.measure_body"
    bl_label = "Measure Body"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = _body(context)
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        ob = _body(context)
        data = read(ob)
        try:
            res, cache = measure_body(context, ob, data, fresh=True)
            for t in data.get('tapes', []):
                store_tape(ob, cache, t, data)
        except Exception as exc:
            self.report({'ERROR'}, rpt("Could not measure the body (upright, facing -Y?): {0}", exc))
            return {'CANCELLED'}
        _redraw(context)
        self.report({'INFO'}, rpt("{0} measurements", len(res)))
        return {'FINISHED'}


class AC9_OT_MeasureResetLevels(bpy.types.Operator):
    """Put every level back where Measure Body finds it"""
    bl_idname = "ac9_cloth_sim.measure_reset_levels"
    bl_label = "Reset Positions"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(read(_body(context)).get('offsets'))

    def execute(self, context):
        ob = _body(context)
        data = read(ob)
        data['offsets'] = {}
        measure_body(context, ob, data)
        _redraw(context)
        return {'FINISHED'}


def _screen_polys(context, ob, geo_items):
    """{key: (k, 2) screen points} of the body's drawn lines."""
    region, rv3d = context.region, context.region_data
    out = {}
    for k, g in geo_items.items():
        W = _to_world(ob, g['pts'])
        S2 = [location_3d_to_region_2d(region, rv3d, Vector(p)) for p in W]
        S2 = [s for s in S2 if s is not None]
        if len(S2) >= 2:
            out[k] = np.array([[s.x, s.y] for s in S2])
    return out


def _nearest_poly(polys, xy):
    best, bd = None, PICK_PX
    for k, P in polys.items():
        A, B = P[:-1], P[1:]
        AB = B - A
        t = np.clip(((xy - A) * AB).sum(1) / np.maximum((AB * AB).sum(1), 1e-9), 0, 1)
        d = np.linalg.norm(A + t[:, None] * AB - xy, axis=1).min()
        if d < bd:
            best, bd = k, d
    return best


class AC9_OT_MeasureMoveLevel(bpy.types.Operator):
    """Edit a measurement on the body: drag a measured line up or down the body, or a tape you laid to another place (a ring slides with the cursor, the nearest point of a tape follows it). release keeps, Esc or right-click puts it back"""
    bl_idname = "ac9_cloth_sim.measure_move_level"
    bl_label = "Edit Measure"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    HEADER = "Edit Measure: drag a line or a tape   release: keep   Esc / right-click: put back"

    @classmethod
    def poll(cls, context):
        d = read(_body(context))
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and bool(d.get('items') or d.get('tapes')))

    def invoke(self, context, event):
        ob = _body(context)
        self.ob = ob
        self.data = read(ob)
        self.cache = body_cache(context, ob)
        self.drag = None
        self.polys = self._polys(context)
        context.area.header_text_set(self.HEADER)
        return TB.start_modal(self, context, event)        # the Edit Measure tool's press grabs the line

    def _polys(self, context):
        """{'item:<key>' | 'tape:<name>': screen points} of what can be dragged (not a height)."""
        geo = read(self.ob, GEO)
        its = self.data.get('items', {})
        P = {'item:' + k: v for k, v in _screen_polys(context, self.ob, geo.get('items', {})).items()
             if its.get(k, {}).get('kind') != 'height' and k != 'back_length'}
        P.update({'tape:' + k: v for k, v in _screen_polys(context, self.ob, geo.get('tapes', {})).items()})
        return P

    def _finish(self, context):
        _ui['hot'] = None
        context.area.header_text_set(None)
        _redraw(context)

    @staticmethod
    def _only(key):
        return {key, 'back_length'} if key == 'waist' else {key}     # the back length ends at the waist

    def _mm_per_px(self, context, key):
        it = self.data['items'][key]
        o_w = _to_world(self.ob, [it['o']])[0]
        n_w = _dir_world(self.ob, it['n'])
        region, rv3d = context.region, context.region_data
        a = location_3d_to_region_2d(region, rv3d, Vector(o_w))
        b = location_3d_to_region_2d(region, rv3d, Vector(o_w + 0.1 * n_w))     # 100 mm along the normal
        if a is None or b is None:
            return None
        d = np.array([b.x - a.x, b.y - a.y])
        L = np.linalg.norm(d)
        return (d / L, 100.0 / L) if L > 1e-3 else None

    # ---- a tape laid by hand: a ring slides through the point under the cursor, a tape's point follows it
    def _tape(self, name):
        return next((t for t in self.data.get('tapes', []) if t['name'] == name), None)

    def _grab_tape(self, context, name, xy):
        t = self._tape(name)
        if t is None:
            return None
        i = None
        if t['kind'] != 'around':                         # the tape's point nearest the press
            region, rv3d = context.region, context.region_data
            S2 = [location_3d_to_region_2d(region, rv3d, Vector(p)) for p in _to_world(self.ob, t['pts'])]
            d = [np.hypot(s.x - xy[0], s.y - xy[1]) if s is not None else np.inf for s in S2]
            i = int(np.argmin(d))
        return {'kind': 'tape', 'key': name, 'i': i, 'start': json.loads(json.dumps(t))}

    def _move_tape(self, context, event):
        hit = _ray_body(context, self.ob, event)
        t = self._tape(self.drag['key'])
        if hit is None or t is None:
            return None
        prev = json.loads(json.dumps(t))
        loc = _round(_to_local(self.ob, [hit[0]]), 2)[0]
        if t['kind'] == 'around':
            t['p'] = loc
        else:
            t['pts'][self.drag['i']] = loc
            if t.get('normals'):
                t['normals'][self.drag['i']] = np.round(_dir_local(self.ob, hit[1]), 6).tolist()
        if store_tape(self.ob, self.cache, t, self.data):
            return t
        t.clear()                                         # off the body there: the tape stays where it was
        t.update(prev)
        return None

    def _put_back(self, context):
        dr = self.drag
        if dr['kind'] == 'item':
            self.data['offsets'][dr['key']] = dr['start']
            measure_body(context, self.ob, self.data, self.cache, only=self._only(dr['key']))
        else:
            t = self._tape(dr['key'])
            if t is not None:
                t.clear()
                t.update(dr['start'])
                store_tape(self.ob, self.cache, t, self.data)

    def modal(self, context, event):
        context.area.tag_redraw()
        xy = np.array([event.mouse_region_x, event.mouse_region_y], float)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            if self.drag is not None:                       # cancel the drag in progress
                self._put_back(context)
                self.drag = None
                self.polys = self._polys(context)
                if getattr(self, 'from_tool', False):
                    self._finish(context)
                    return {'FINISHED'}
                return {'RUNNING_MODAL'}
            self._finish(context)
            return {'FINISHED'}
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type == 'MOUSEMOVE':
            if self.drag is None:
                self.polys = self.polys or self._polys(context)
                _ui['hot'] = _nearest_poly(self.polys, xy)
                return {'RUNNING_MODAL'}
            if self.drag['kind'] == 'tape':
                t = self._move_tape(context, event)
                if t is not None:
                    from . import measure_edit as MEE
                    context.area.header_text_set(tx("{0}: {1} cm   release: keep   Esc / right-click: put back", t['name'], _cm(MEE.tape_value(t))))
                _redraw(context)
                return {'RUNNING_MODAL'}
            k, (u, mm) = self.drag['key'], self.drag['axis']
            off = self.drag['start'] + float((xy - self.drag['xy']) @ u) * mm
            self.data.setdefault('offsets', {})[k] = round(off, 1)
            res, _ = measure_body(context, self.ob, self.data, self.cache, only=self._only(k))
            v = res.get(k)
            context.area.header_text_set(tx("{0}: {1} cm, moved {2:+.0f} mm   release: keep   Esc / right-click: put back", tx(LABEL.get(k, k)), _cm(v['value'] if v else None), off))
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hot = _ui['hot'] or ''
            from . import measure_edit as MEE
            if hot.startswith('tape:'):
                MEE.select(context, hot)
                self.drag = self._grab_tape(context, hot[5:], xy)
                return {'RUNNING_MODAL'}
            k = hot[5:] if hot.startswith('item:') else None
            if k is None or k in ('height', 'back_length'):
                return {'RUNNING_MODAL'}
            MEE.select(context, 'item:' + k)
            ax = self._mm_per_px(context, k)
            if ax is None:
                return {'RUNNING_MODAL'}
            self.drag = {'kind': 'item', 'key': k, 'xy': xy, 'axis': ax,
                         'start': float(self.data.get('offsets', {}).get(k, 0.0))}
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.drag is not None:
            self.drag = None
            if getattr(self, 'from_tool', False):              # the Edit Measure tool: one drag per press
                self._finish(context)
                return {'FINISHED'}
            self.polys = self._polys(context)
            context.area.header_text_set(self.HEADER)
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}


def _ray_body(context, ob, event):
    """(world mm point, world normal) where the mouse ray meets the body, or None."""
    if not VV.object_allowed(context, ob):
        return None
    region, rv3d = context.region, context.region_data
    co = (event.mouse_region_x, event.mouse_region_y)
    o = region_2d_to_origin_3d(region, rv3d, co)
    d = region_2d_to_vector_3d(region, rv3d, co)
    Mi = ob.matrix_world.inverted()
    ol = Mi @ o
    dl = (Mi.to_3x3() @ d).normalized()
    ok, loc, nrm, _ = ob.ray_cast(ol, dl, depsgraph=context.evaluated_depsgraph_get())
    if not ok:
        return None
    p = np.array(ob.matrix_world @ loc) * 1000.0
    n = np.array((ob.matrix_world.to_3x3().inverted().transposed() @ nrm).normalized())
    return p, n


PLANES = [('AUTO', "Auto", "Across the nearest limb, or level round the torso"),
          ('HORIZONTAL', "Level", "A level (horizontal) ring"),
          ('FRONT', "Front-back", "A vertical ring seen from the front (over the shoulder, through the crotch)"),
          ('SIDE', "Side", "A vertical ring seen from the side")]


class AC9_OT_MeasureTape(bpy.types.Operator):
    """Lay a tape measure on the body. Around: click a point, the tape goes round the body through it. Along:
    click points, the tape follows the body's surface through them; Enter keeps it, Esc or right-click cancels"""
    bl_idname = "ac9_cloth_sim.measure_tape"
    bl_label = "Tape"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    mode: EnumProperty(translation_context=CTX, items=[('AROUND', "Around", "Round the body through one point"),
                              ('ALONG', "Along", "Along the body's surface through clicked points"),
                              ('STRAIGHT', "Straight", "Pulled taut between two points: over the bulges, across the hollows")],
                       default='AROUND')

    @classmethod
    def description(cls, context, props):
        if props.mode == 'AROUND':
            return "Tape round the body: click a point; Tab changes the ring's direction, Ctrl+wheel tilts it"
        if props.mode == 'STRAIGHT':
            return ("Tape pulled taut between two points of the body (over the bulges, across the hollows): "
                    "click one, then the other")
        return ("Tape along the body: click points along the way; Enter keeps it, Backspace removes the last "
                "point, Esc or right-click cancels")

    @classmethod
    def poll(cls, context):
        ob = _body(context)
        return ob is not None and ob.type == 'MESH' and context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        self.ob = _body(context)
        _ui.update(preview_domain='WORLD', preview_owner=self.ob.name, preview_pieces=())
        self.cache = body_cache(context, self.ob)
        self.data = read(self.ob)
        self.pts, self.nrms = [], []
        self.plane = [p[0] for p in PLANES].index(context.window_manager.ac9_tape_plane)
        self._header(context, None)
        return TB.start_modal(self, context, event)        # the Measure tool's click is the first point

    def _header(self, context, val):
        v = "" if val is None else f"{val / 10.0:.1f} cm   "
        if self.mode == 'AROUND':
            txt = tx("Tape Around: {0}click: keep   Tab: direction ({1})   Esc / right-click: done", v, PLANES[self.plane][1])
        elif self.mode == 'STRAIGHT':
            txt = tx("Tape Straight: {0}click two points   Esc / right-click: cancel", v)
        else:
            txt = tx("Tape Along: {0}click points   Enter: keep   Backspace: undo point   Esc / right-click: cancel", v)
        context.area.header_text_set(txt)

    def _finish(self, context):
        _ui['preview'] = None
        _ui['preview_label'] = None
        context.area.header_text_set(None)
        _redraw(context)

    def _keep(self, context, t):
        t['name'] = _next_tape_name(self.data)
        if store_tape(self.ob, self.cache, t, self.data):
            self.report({'INFO'}, rpt("{0}: {1} cm", t['name'], _cm(t['value'])))
            from . import measure_edit as MEE                # the new tape is the one picked
            MEE.select(context, 'tape:' + t['name'])
            _redraw(context)

    def _preview(self, context, event):
        hit = _ray_body(context, self.ob, event)
        if self.mode == 'AROUND':
            if hit is None:
                _ui['preview'] = None
                self._header(context, None)
                return None
            from . import measure_edit as MEE
            n0 = _plane_normal(self.cache, hit[0], PLANES[self.plane][0])
            n = MEE.tilted(n0, context.window_manager.ac9_tape_tilt, 0.0)
            r = ME.tape_around(self.cache.body, hit[0], n)
            if r is None:
                _ui['preview'] = None
                return None
            _ui['preview'] = (r[2] * 0.001, True)          # the line; the length in the header (user 2026-10-21)
            _ui['preview_label'] = None
            self._header(context, r[0])
            return hit, n, n0
        if self.mode == 'STRAIGHT':
            if self.pts and hit is not None:
                L, _, poly = ME.tape_taut(self.cache.body, self.pts[0], hit[0], self.nrms[0], hit[1])
                _ui['preview'] = (poly * 0.001, False)
                self._header(context, L)
            return hit
        P = list(self.pts) + ([hit[0]] if hit is not None else [])
        N = list(self.nrms) + ([hit[1]] if hit is not None else [])
        if len(P) >= 2:
            L, _, poly = ME.tape_along(self.cache.body, P, N)
            _ui['preview'] = (poly * 0.001, False)
            _ui['preview_label'] = (poly[-1] * 0.001, f"{L / 10.0:.1f} cm")
            self._header(context, L)
        elif P:
            _ui['preview'] = (np.array(P) * 0.001, False)
        return hit

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type == 'ESC' and event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED'} if self.mode == 'AROUND' or not self.pts else {'CANCELLED'}
        if self.mode == 'AROUND':
            if event.type == 'TAB' and event.value == 'PRESS':
                self.plane = (self.plane + 1) % len(PLANES)
                context.window_manager.ac9_tape_plane = PLANES[self.plane][0]
                self._preview(context, event)
            elif event.type == 'MOUSEMOVE':
                self._preview(context, event)
            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                r = self._preview(context, event)
                if r is not None:
                    (p, _), n, n0 = r
                    tilt = round(float(context.window_manager.ac9_tape_tilt), 1)
                    self._keep(context, {'kind': 'around', 'p': _round(_to_local(self.ob, [p])[0], 2),
                                         'n': np.round(_dir_local(self.ob, n), 6).tolist(),
                                         'n0': np.round(_dir_local(self.ob, n0), 6).tolist(), 'tilt': [tilt, 0.0]})
                    if getattr(self, 'from_tool', False):      # the Measure tool: one ring per click
                        self._finish(context)
                        return {'FINISHED'}
            elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                self._finish(context)
                return {'FINISHED'}
            return {'RUNNING_MODAL'}
        if self.mode == 'STRAIGHT':
            if event.type == 'MOUSEMOVE':
                self._preview(context, event)
            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                hit = _ray_body(context, self.ob, event)
                if hit is not None:
                    self.pts.append(hit[0])
                    self.nrms.append(hit[1])
                if len(self.pts) == 2:
                    self._keep(context, {'kind': 'straight', 'pts': _round(_to_local(self.ob, self.pts), 2),
                                         'normals': [np.round(_dir_local(self.ob, n), 6).tolist() for n in self.nrms]})
                    self._finish(context)
                    return {'FINISHED'}
            elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                self._finish(context)
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            self._preview(context, event)
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hit = _ray_body(context, self.ob, event)
            if hit is not None:
                self.pts.append(hit[0])
                self.nrms.append(hit[1])
        elif event.type == 'BACK_SPACE' and event.value == 'PRESS' and self.pts:
            self.pts.pop()
            self.nrms.pop()
            self._preview(context, event)
        elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            # right-click cancels as in every other tool and in Blender; Enter keeps (user 2026-10-21, it kept)
            self._finish(context)
            return {'CANCELLED'}
        elif event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if len(self.pts) >= 2:
                self._keep(context, {'kind': 'along', 'pts': _round(_to_local(self.ob, self.pts), 2),
                                     'normals': [np.round(_dir_local(self.ob, n), 6).tolist() for n in self.nrms]})
            self._finish(context)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class AC9_OT_MeasureTapeRemove(bpy.types.Operator):
    """Remove this tape"""
    bl_idname = "ac9_cloth_sim.measure_tape_remove"
    bl_label = "Remove Tape"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        ob = _body(context)
        data = read(ob)
        if context.scene.ac9_cloth_sim_measure.sel == 'tape:' + self.name:
            context.scene.ac9_cloth_sim_measure.sel = ''
        data['tapes'] = [t for t in data.get('tapes', []) if t['name'] != self.name]
        geo = read(ob, GEO)
        geo.get('tapes', {}).pop(self.name, None)
        write(ob, data)
        write(ob, geo, GEO)
        _redraw(context)
        return {'FINISHED'}


class AC9_OT_MeasureGarment(bpy.types.Operator):
    """Measure the dressed garment round the body's measured lines (as it is on screen now): its girth there, and
    the ease -- how much longer than the body it is"""
    bl_idname = "ac9_cloth_sim.measure_garment"
    bl_label = "Measure Garment"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return garment_object(context) is not None and bool(read(_body(context)).get('items'))

    def execute(self, context):
        go = garment_object(context)
        g = measure_garment(context, _body(context), go, frame=context.scene.frame_current)
        _redraw(context)
        n = sum(1 for it in g.values() if it['covered'])
        self.report({'INFO'}, rpt("{0}: goes round {1} of the body's girths", go.name, n))
        return {'FINISHED'}


def measure_garment(context, bo, go, frame=None):
    """Measure the garment round the body's lines and store it on the garment. frame: the frame shown (the button),
    or None: the result of the last run (after a Simulate, user 2026-10-21). A frame that is the last one counts as
    the result, so the panel names a frame only when one in the middle of the run was measured."""
    mc = next((m for m in go.modifiers if m.type == 'MESH_CACHE' and m.show_viewport), None)
    if frame is not None and (mc is None or frame >= context.scene.frame_end):
        frame = None
    data = read(bo)
    res, cache = measure_body(context, bo, data)
    g = ME.measure_garment(cache.body, garment_mesh(context, go, result=frame is None), res)
    out = {'body': bo.name, 'frame': frame, 'items': {}}
    for k, it in g.items():
        out['items'][k] = {'value': None if it['value'] is None else round(it['value'], 2),
                           'ease': None if it['ease'] is None else round(it['ease'], 2),
                           'covered': it['covered'], 'gap_deg': round(it['gap_deg'], 1), 'pts': _round(it['pts'])}
    write(go, out)
    return g


# a finished Simulate (Dress / Continue / Live) gives the garment a new job: measure its result then. A handler
# here, so dress.py is not touched; a garment seen for the first time (a file opened) is only remembered
_jobs = {}


@bpy.app.handlers.persistent
def _auto_garment(scene, depsgraph=None):
    try:
        objs = [o for o in scene.objects if o.type == 'MESH' and o.name.startswith(dress.GARMENT_PREFIX)]
    except Exception:
        return
    for go in objs:
        job = go.get('ac9_job', '')
        key = go.as_pointer()
        if key not in _jobs:
            _jobs[key] = job
            continue
        if _jobs[key] == job:
            continue
        _jobs[key] = job
        bo = scene.ac9_cloth_sim.body
        if not job or bo is None or not read(bo).get('items'):
            continue
        try:
            measure_garment(bpy.context, bo, go)
        except Exception as e:                          # never in the way of the result
            print("AC9 Cloth Sim: measuring the garment failed:", e)


class AC9_OT_MeasureSetTarget(bpy.types.Operator):
    """The finished size this measurement should have (from the size chart); 0 clears it"""
    bl_idname = "ac9_cloth_sim.measure_set_target"
    bl_label = "Target"
    bl_translation_context = CTX
    bl_options = {'REGISTER', 'UNDO'}

    key: StringProperty()
    value: FloatProperty(name="Target (cm)", min=0.0, max=1000.0, precision=1)

    @classmethod
    def description(cls, context, props):
        return tx(ME.HELP.get(props.key, "A length on the pattern")) + tx(". Click: set the finished size from the size chart")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, _ = store.get(context)
        self.value = spec.get('measure', {}).get('targets', {}).get(self.key, 0.0) / 10.0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        tg = spec.setdefault('measure', {}).setdefault('targets', {})
        if self.value > 0:
            tg[self.key] = round(self.value * 10.0, 1)
        else:
            tg.pop(self.key, None)
        store.put(context, spec)
        return {'FINISHED'}


class AC9_OT_MeasureHighlight(bpy.types.Operator):
    """Show where this is measured on the body"""
    bl_idname = "ac9_cloth_sim.measure_highlight"
    bl_label = "Highlight"

    key: StringProperty()

    @classmethod
    def description(cls, context, props):
        return tx(ME.HELP.get(props.key, "")) if props.key in ME.HELP else ""

    def execute(self, context):
        from . import measure_edit as MEE
        MEE.select(context, '' if MEE.sel(context) == 'item:' + self.key else 'item:' + self.key)
        return {'FINISHED'}


def _project(context, W):
    """World points (n, 3) -> region pixels (n, 2); points behind the view get inf."""
    region, rv3d = context.region, context.region_data
    PM = np.array(rv3d.perspective_matrix)
    C = np.c_[W, np.ones(len(W))] @ PM.T
    w = C[:, 3:4]
    ok = w[:, 0] > 1e-6
    S2 = np.full((len(W), 2), np.inf)
    S2[ok] = (C[ok, :2] / w[ok] + 1.0) * 0.5 * np.array([region.width, region.height])
    return S2


def _snap_outline(context, outlines, xy, step=2.0):
    """(piece, edge, t, world point) of the outline point nearest the mouse within PICK_PX, or None."""
    M = np.array(PV.layout_matrix(context))
    best, bd = None, PICK_PX
    visible = VV.pattern_names(context)
    for name, ol in outlines.items():
        if name not in visible:
            continue
        s = np.arange(0.0, ol.L, step)
        P = ol.at(s) * PV.MM
        W = (np.c_[P, np.zeros(len(P)), np.ones(len(P))] @ M.T)[:, :3]
        d = np.linalg.norm(_project(context, W) - np.asarray(xy, float), axis=1)
        k = int(np.argmin(d))
        if d[k] < bd:
            e, t = ol.edge_t(s[k])
            best, bd = (name, e, float(t), W[k]), float(d[k])
    return best


class AC9_OT_MeasurePattern(bpy.types.Operator):
    """Measure on the flat pattern: click two points on piece outlines. Kept as a row of the pattern lengths
    (straight, and along the outline when both are on one piece). Esc ends"""
    bl_idname = "ac9_cloth_sim.measure_pattern"
    bl_label = "Measure Pattern"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    @classmethod
    def poll(cls, context):
        return (store.get(context)[0] is not None and PV.layout_empty(context) is not None
                and context.area is not None and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        self.first = None
        _ui.update(preview_domain='PATTERN', preview_owner=None, preview_pieces=())
        context.area.header_text_set("Measure Pattern: click a point on an outline, then another. Esc: done")
        return TB.start_modal(self, context, event)

    def _finish(self, context):
        _ui['preview'] = None
        _ui['preview_label'] = None
        context.area.header_text_set(None)
        _redraw(context)

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED'}
        spec, outlines = store.get(context)
        xy = (event.mouse_region_x, event.mouse_region_y)
        if event.type == 'MOUSEMOVE':
            h = _snap_outline(context, outlines, xy)
            if self.first is not None and h is not None:
                _ui['preview_pieces'] = (self.first[0], h[0])
                oa, ob_ = outlines[self.first[0]], outlines[h[0]]
                st, al = ME.pattern_distance(oa, oa.s_of(self.first[1], self.first[2]), ob_, ob_.s_of(h[1], h[2]))
                _ui['preview'] = (np.array([self.first[3], h[3]]), False)
                _ui['preview_label'] = (h[3], f"{st / 10.0:.1f} cm" + ("" if al is None else f" / {al / 10.0:.1f}"))
            elif h is not None:
                _ui['preview_pieces'] = (h[0],)
                _ui['preview'] = (np.array([h[3], h[3]]), False)
                _ui['preview_label'] = None
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            h = _snap_outline(context, outlines, xy)
            if h is None:
                return {'RUNNING_MODAL'}
            if self.first is None:
                self.first = h
                return {'RUNNING_MODAL'}
            spec = S.loads(S.dumps(spec))
            rows = spec.setdefault('measure', {}).setdefault('pattern', [])
            names = {r['name'] for r in rows}
            i = 1
            while f"Length {i}" in names:
                i += 1
            rows.append({'name': f"Length {i}",
                         'a': {'piece': self.first[0], 'edge': self.first[1], 't': round(self.first[2], 5)},
                         'b': {'piece': h[0], 'edge': h[1], 't': round(h[2], 5)}})
            store.put(context, spec)
            self.first = None
            _ui['preview'] = None
            _ui['preview_label'] = None
            if getattr(self, 'from_tool', False):              # the Measure tool: one length per two clicks
                self._finish(context)
                return {'FINISHED'}
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}


class AC9_OT_MeasurePatternRemove(bpy.types.Operator):
    """Remove this pattern length"""
    bl_idname = "ac9_cloth_sim.measure_pattern_remove"
    bl_label = "Remove Length"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty()

    def execute(self, context):
        spec, _ = store.get(context)
        spec = S.loads(S.dumps(spec))
        m = spec.setdefault('measure', {})
        m['pattern'] = [r for r in m.get('pattern', []) if r['name'] != self.name]
        m.get('targets', {}).pop(self.name, None)
        store.put(context, spec)
        return {'FINISHED'}


# ---------------------------------------------------------------- panel

class AC9MeasureProps(bpy.types.PropertyGroup):
    sel: StringProperty(options={'HIDDEN', 'SKIP_SAVE'},
                        description="The picked measurement: item:<key> | tape:<name> | pattern:<name> (measure_edit)")
    show: BoolProperty(name="Show Measurements", default=True, update=lambda s, c: _redraw(c),
                       description="Draw the measured lines and tapes on the body")


def _cols(col):
    """A table row: a wide name column, then three value columns (equal columns cut the names, 2026-10-09)."""
    sp = col.row(align=True).split(factor=0.42, align=True)
    name = sp.row(align=True)                             # the first child takes the factor
    return name, sp.row(align=True)


def _target(spec, key):
    return (spec or {}).get('measure', {}).get('targets', {}).get(key) if spec else None


class AC9_PT_SimMeasure(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Measure"
    bl_translation_context = CTX
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.ac9_cloth_sim
        mp = context.scene.ac9_cloth_sim_measure
        # the same Body field as Placement's: drawn here only when Placement is not shown (no garment), two fields
        # read as two bodies
        if store.get(context)[0] is None:
            layout.prop(props, "body")
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.measure_body", text="Measure Body")
        row.operator("ac9_cloth_sim.measure_garment", text="Measure Garment")
        row.prop(mp, "show", text="", icon='HIDE_OFF' if mp.show else 'HIDE_ON')
        ob = props.body
        data = read(ob)
        items = data.get('items')
        if not items:
            if ob is None:                  # with a body picked the button right above says it
                layout.label(text="Pick the body, then Measure Body", icon='INFO')
            return
        # the measuring tools (the toolbar's Edit Measure group, user 2026-10-21)
        box = layout.box()
        col = box.column(align=True)
        for pair in ((("move_level_tool", "Edit Measure"), ("measure_tool", "Around")),
                     (("tape_along_tool", "Along"), ("tape_straight_tool", "Straight"))):
            row = col.row(align=True)
            for tid, text in pair:
                TB.tool_button(row, context, "ac9_cloth_sim." + tid, text, 'NONE')
        spec, _ = store.get(context)
        go = garment_object(context)
        gdat = read(go) if go is not None else {}
        gi = gdat.get('items', {}) if gdat.get('body') == ob.name else {}
        box = layout.box()
        col = box.column(align=True)
        a, head = _cols(col)
        a.label(text="")
        for t in ("Body", "Cloth", "Ease"):
            head.label(text=tx(t))
        for k, it in items.items():
            a, r = _cols(col)
            a.operator("ac9_cloth_sim.measure_highlight", text=tx(LABEL.get(k, k)), emboss=False,
                       depress=mp.sel == 'item:' + k).key = k
            r.label(text=_cm(it['value']))
            g = gi.get(k)
            if it['kind'] != 'girth':
                r.label(text="")
                r.label(text="")
            elif g is None:
                r.label(text="—")
                r.label(text="")
            else:
                r.label(text=_cm(g['value']) if g['covered'] else "open")
                r.label(text=_signed_cm(g['ease']))
        row = box.row()
        row.label(text="cm. Girths: a tape pulled round the body")
        row.operator("ac9_cloth_sim.measure_reset_levels", text="", icon='LOOP_BACK')
        if go is not None and gi and gdat.get('frame') is not None:     # a frame in the middle of the run
            box.label(text=tx("Cloth: frame {0}", gdat['frame']), icon='TIME')


class AC9_PT_SimMeasureGarment(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Size Check"
    bl_parent_id = "AC9_PT_SimMeasure"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def draw(self, context):
        layout = self.layout
        spec, outlines = store.get(context)
        ob = _body(context)
        go = garment_object(context)
        gdat = read(go) if go is not None else {}
        gi = gdat.get('items', {}) if ob is not None and gdat.get('body') == ob.name else {}
        col = layout.column(align=True)
        a, head = _cols(col)
        a.label(text="")
        for t in ("Cloth", "Target", "Diff"):
            head.label(text=tx(t))
        for k, g in gi.items():
            if not g['covered']:
                continue
            a, r = _cols(col)
            a.operator("ac9_cloth_sim.measure_set_target", text=tx(LABEL.get(k, k)), emboss=False).key = k
            tg = _target(spec, k)
            r.label(text=_cm(g['value']))
            r.label(text=_cm(tg))
            r.label(text=_signed_cm(None if tg is None else g['value'] - tg))
        rows = _pattern_rows(spec, outlines)
        for row_, st, al, _, _ in rows:
            a, r = _cols(col)
            a.operator("ac9_cloth_sim.measure_set_target", text=row_['name'], emboss=False).key = row_['name']
            v = al if al is not None else st
            tg = _target(spec, row_['name'])
            r.label(text=_cm(v))
            r.label(text=_cm(tg))
            r.label(text=_signed_cm(None if tg is None else v - tg))
        if not gi and not rows:
            layout.label(text="Measure Garment, or Measure Pattern", icon='INFO')
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.measure_pattern", icon='DRIVER_DISTANCE')
        if rows:
            sub = layout.column(align=True)
            for row_, st, al, _, _ in rows:
                r = sub.row(align=True)
                r.label(text=row_['name'])
                r.label(text=tx("{0} straight", _cm(st)) if al is None else tx("{0} / {1}", _cm(st), _cm(al)))
                r.operator("ac9_cloth_sim.measure_pattern_remove", text="", icon='X', emboss=False).name = row_['name']
            layout.label(text="Length: straight / along the outline")
        layout.label(text="Click a name to set its target", icon='INFO')


# ---------------------------------------------------------------- overlay

_handles = []
_draw_cache = {'key': None}
_draw_views = {}

C_BODY = (1.0, 0.85, 0.2, 0.9)
C_HOT = (1.0, 1.0, 1.0, 1.0)                 # the picked measurement
C_HOVER = (1.0, 1.0, 1.0, 0.6)                # the one under the cursor
C_TAPE = (0.3, 0.9, 1.0, 0.95)
C_GARMENT = (1.0, 0.4, 0.75, 0.9)
C_PREVIEW = (0.4, 1.0, 0.5, 1.0)


def _segments(P, closed):
    P = np.asarray(P, float)
    if closed:
        P = np.vstack([P, P[:1]])
    return [tuple(p) for a, b in zip(P[:-1], P[1:]) for p in (a, b)]


def _build(context):
    """The world (m) lines to draw, rebuilt when the stored data or the body's place changes."""
    global _draw_cache
    ob = _body(context)
    spec, outlines = store.get(context)
    go = garment_object(context)
    visible_pattern = VV.pattern_names(context, spec)
    body_visible = VV.object_decoration_allowed(context, ob)
    garment_visible = VV.object_decoration_allowed(context, go)
    visibility = (visible_pattern, body_visible, garment_visible)
    if visibility not in _draw_views:
        if len(_draw_views) >= 16:
            _draw_views.clear()
        _draw_views[visibility] = {'key': None}
    _draw_cache = _draw_views[visibility]
    key = (ob.name if ob else None, ob[PROP] if ob is not None and PROP in ob else None,
           ob[GEO] if ob is not None and GEO in ob else None, tuple(map(tuple, ob.matrix_world)) if ob else None,
           go[PROP] if go is not None and PROP in go else None, id(spec), _ui['hot'],
           context.scene.ac9_cloth_sim_measure.sel, visible_pattern, body_visible, garment_visible)
    if _draw_cache['key'] == key:
        return _draw_cache
    from . import measure_edit as MEE
    sel = context.scene.ac9_cloth_sim_measure.sel
    out = {'key': key, 'body': [], 'hot': [], 'sel': [], 'tapes': [], 'garment': [], 'labels': [], 'pattern': [],
           'polys': [], 'sources': {}}

    def put(layer, pk, W, closed, domain, source):
        out['polys'].append((pk, W, closed))
        out['sources'][pk] = (domain, source)
        segs = _segments(W, closed)
        out[layer].extend(segs)
        if pk == sel:
            out['sel'].extend(segs)
        elif pk == _ui['hot']:
            out['hot'].extend(segs)
    if ob is not None and body_visible:
        geo = read(ob, GEO)
        data = read(ob)
        for k, g in geo.get('items', {}).items():
            W = _to_world(ob, g['pts'])
            put('body', 'item:' + k, W, g['closed'], 'WORLD', ob.name)
            it = data.get('items', {}).get(k)
            if it is not None and len(W):
                p = W[np.argmin(W[:, 1])] if g['closed'] else W[-1]
                out['labels'].append((p, f"{tx(LABEL.get(k, k))} {_cm(it['value'])}", 'WORLD', ob.name))
        tps = {t['name']: t for t in data.get('tapes', [])}
        for name, g in geo.get('tapes', {}).items():
            t = tps.get(name, {})
            if t.get('kind') == 'along' and t.get('main') == 'straight':      # its straight length: the chords
                W = _to_world(ob, t['pts'])
            else:
                W = _to_world(ob, g['pts'])
            put('tapes', 'tape:' + name, W, g['closed'], 'WORLD', ob.name)
            if len(W):
                out['labels'].append((W[np.argmin(W[:, 1])] if g['closed'] else W[-1],
                                      f"{name} {_cm(MEE.tape_value(t) if t else None)}", 'WORLD', ob.name))
    if ob is not None and go is not None and garment_visible:
        gd = read(go)
        if gd.get('body') == ob.name:
            for k, g in gd.get('items', {}).items():
                if g['covered']:
                    out['garment'].extend(_segments(np.asarray(g['pts']) * 0.001, True))
                    out['sources']['garment:' + k] = ('WORLD', go.name)
    if spec and outlines and PV.layout_empty(context) is not None:
        M = np.array(PV.layout_matrix(context))
        for row_, st, al, pa, pb in _pattern_rows(spec, outlines):
            pieces = (row_['a']['piece'], row_['b']['piece'])
            if not set(pieces) <= visible_pattern:
                continue
            W = np.array([[pa[0], pa[1], 0, 1000.0], [pb[0], pb[1], 0, 1000.0]]) * 0.001
            W = (W @ M.T)[:, :3]
            put('pattern', 'pattern:' + row_['name'], W, False, 'PATTERN', pieces)
            out['labels'].append((0.5 * (W[0] + W[1]), f"{row_['name']} {_cm(al if al is not None else st)}",
                                  'PATTERN', pieces))
    _draw_cache.clear()
    _draw_cache.update(out)
    return _draw_cache


def _preview_allowed(context):
    if _ui['preview_domain'] == 'WORLD':
        return VV.object_decoration_allowed(context, bpy.data.objects.get(_ui['preview_owner'] or ''))
    if _ui['preview_domain'] == 'PATTERN':
        return bool(_ui['preview_pieces']) and set(_ui['preview_pieces']) <= VV.pattern_names(context)
    return False


def _draw_view():
    context = bpy.context
    mp = getattr(context.scene, 'ac9_cloth_sim_measure', None)
    if mp is None or not mp.show or not getattr(context.scene.ac9_cloth_sim, 'show_overlays', True):
        return
    g = _build(context)
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    for name, col, w in (('body', C_BODY, 2.0), ('garment', C_GARMENT, 1.5), ('tapes', C_TAPE, 2.5),
                         ('pattern', C_TAPE, 2.5), ('hot', C_HOVER, 3.5), ('sel', C_HOT, 5.0)):
        if g[name]:
            sh.uniform_float('color', col)
            sh.uniform_float('lineWidth', w)
            batch_for_shader(sh, 'LINES', {'pos': g[name]}).draw(sh)
    if _ui['preview'] is not None and _preview_allowed(context):
        P, closed = _ui['preview']
        sh.uniform_float('color', C_PREVIEW)
        sh.uniform_float('lineWidth', 3.0)
        batch_for_shader(sh, 'LINES', {'pos': _segments(P, closed)}).draw(sh)
    gpu.state.blend_set('NONE')


def _draw_px():
    context = bpy.context
    mp = getattr(context.scene, 'ac9_cloth_sim_measure', None)
    if mp is None or not mp.show or not getattr(context.scene.ac9_cloth_sim, 'show_overlays', True):
        return
    g = _build(context)
    labels = list(g['labels']) if g else []
    if _ui['preview_label'] is not None and _preview_allowed(context):
        labels.append((*_ui['preview_label'], _ui['preview_domain'],
                       _ui['preview_owner'] or _ui['preview_pieces']))
    region, rv3d = context.region, context.region_data
    fid = 0
    blf.size(fid, 12)
    for p, txt, _domain, _source in labels:
        xy = location_3d_to_region_2d(region, rv3d, Vector(p))
        if xy is None:
            continue
        blf.position(fid, xy[0] + 6, xy[1] + 4, 0)
        blf.color(fid, 1, 1, 1, 0.95)
        blf.draw(fid, txt)


def _redraw(context):
    _draw_cache['key'] = None
    for cached in _draw_views.values():
        cached['key'] = None
    for area in context.screen.areas if context.screen else []:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


# ---------------------------------------------------------------- registration

CLASSES = (AC9MeasureProps, AC9_OT_MeasureBody, AC9_OT_MeasureResetLevels, AC9_OT_MeasureMoveLevel, AC9_OT_MeasureTape,
           AC9_OT_MeasureTapeRemove, AC9_OT_MeasureGarment, AC9_OT_MeasureSetTarget, AC9_OT_MeasureHighlight,
           AC9_OT_MeasurePattern, AC9_OT_MeasurePatternRemove, AC9_PT_SimMeasure,
           AC9_PT_SimMeasureGarment)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ac9_cloth_sim_measure = PointerProperty(type=AC9MeasureProps)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_px, (), 'WINDOW', 'POST_PIXEL'))
    bpy.app.handlers.depsgraph_update_post.append(_auto_garment)


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    _draw_cache['key'] = None
    for h in list(bpy.app.handlers.depsgraph_update_post):
        if getattr(h, '__name__', '') == '_auto_garment':
            bpy.app.handlers.depsgraph_update_post.remove(h)
    _jobs.clear()
    del bpy.types.Scene.ac9_cloth_sim_measure
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
