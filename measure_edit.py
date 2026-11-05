# SPDX-License-Identifier: GPL-3.0-or-later
"""Measurements as things to pick and edit (user 2026-10-21, DECISIONS cloth-sim-select-measure-delete-2026-10-21;
Fable docs/reviews/REVIEW_fable_tools_measure_2026-10-21.md and REVIEW_fable_select_delete_internal_2026-10-21.md).

One kind of thing, "a measurement": the body's measured lines (Measure Body: bust, waist, ...), the tapes laid by hand
(round the body, along it, straight), and the lengths taken on the flat pattern. Each is picked by a click on its line
(the Tape tools, Edit Measure) or its row in the list, and the Properties editor's Tool tab shows it (AC9 Measurement:
name, kind, values, target, tilt, Mirror, Delete). The pick is kept as one string, scene.ac9_cloth_sim_measure.sel:
"item:<key>" | "tape:<name>" | "pattern:<name>" ('' none), so it does not shift when others are added or removed.

The Tape Around tool shows the ring under the cursor before the click (the line only; the length in the header), and
Ctrl+wheel tilts it forward / back (Shift: by 1 deg).
"""
import math
from .i18n import CTX, rpt, tx

import bpy
import numpy as np
from bpy.props import EnumProperty, FloatProperty, StringProperty

from . import measure_tool as MT
from . import store
from .core import attach as AT
from .core import measure as ME
from .core import spec as S

KINDS = {'around': "Round the body", 'along': "Along the body", 'straight': "Straight (taut)", 'girth': "Round the body",
         'height': "Height", 'length': "Along the body", 'pattern': "On the pattern"}
FRONT = np.array([0.0, -1.0, 0.0])          # the body faces -Y (placement, measure)
_hover = {'xy': None, 'key': None}


# ---------------------------------------------------------------- what is picked

def sel(context):
    mp = getattr(context.scene, 'ac9_cloth_sim_measure', None)
    return mp.sel if mp is not None else ''


def selected(context):
    """(kind 'item' | 'tape' | 'pattern', name, record) of the picked measurement, or None (gone or nothing)."""
    s = sel(context)
    if ':' not in s:
        return None
    kind, name = s.split(':', 1)
    if kind in ('item', 'tape'):
        data = MT.read(MT._body(context))
        if kind == 'item':
            it = data.get('items', {}).get(name)
            return None if it is None else ('item', name, it)
        t = next((t for t in data.get('tapes', []) if t['name'] == name), None)
        return None if t is None else ('tape', name, t)
    spec, _ = store.get(context)
    r = next((r for r in (spec or {}).get('measure', {}).get('pattern', []) if r['name'] == name), None)
    return None if r is None else ('pattern', name, r)


def select(context, key):
    """Pick a measurement ('' unpicks); the Tool tab shows it."""
    context.scene.ac9_cloth_sim_measure.sel = key or ''
    from . import tool_tab as TT
    TT.set_focus('MEASURE' if key else None)
    MT._redraw(context)


def pick(context, xy, items_only=False, kinds=None):
    """The key of the measurement line nearest the region point xy within PICK_PX, or None. kinds: only these
    ('item', 'tape', 'pattern')."""
    g = MT._build(context)
    best, bd = None, MT.PICK_PX
    q = np.asarray(xy, float)
    for key, W, closed in g.get('polys', []):
        if items_only and not key.startswith('item:'):
            continue
        if kinds is not None and key.split(':', 1)[0] not in kinds:
            continue
        P = MT._project(context, np.vstack([W, W[:1]]) if closed else W)
        ok = np.isfinite(P).all(1)
        if ok.sum() < 2:
            continue
        A, B = P[:-1], P[1:]
        m = ok[:-1] & ok[1:]
        A, B = A[m], B[m]
        AB = B - A
        t = np.clip(((q - A) * AB).sum(1) / np.maximum((AB * AB).sum(1), 1e-9), 0, 1)
        d = float(np.linalg.norm(A + t[:, None] * AB - q, axis=1).min()) if len(A) else np.inf
        if d < bd:
            best, bd = key, d
    return best


EDIT_KINDS = ('item', 'tape')            # what Edit Measure drags: a measured line, a tape on the body


def tape_value(t):
    """The length a tape shows: along the body, or straight between its points when that is its main value."""
    if t['kind'] == 'along' and t.get('main') == 'straight':
        return t.get('extra')
    return t.get('value')


# ---------------------------------------------------------------- the ring's tilt

def _rot(v, a, deg):
    a = a / np.linalg.norm(a)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return v * c + np.cross(a, v) * s + a * (a @ v) * (1 - c)


def tilted(n0, fb, side):
    """The ring's normal n0 (world) tilted forward / back by fb deg (front down for +) and to the side by side deg."""
    n0 = np.asarray(n0, float) / np.linalg.norm(n0)
    a = np.cross(n0, FRONT)
    if np.linalg.norm(a) < 1e-6:                      # a ring seen from the side (its normal is the front)
        a = np.array([1.0, 0.0, 0.0])
    n = _rot(n0, a, fb)
    b = np.cross(a / np.linalg.norm(a), n0)
    if np.linalg.norm(b) > 1e-6 and side:
        n = _rot(n, b, side)
    return n / np.linalg.norm(n)


# ---------------------------------------------------------------- the Tape Around preview under the cursor

def hover(context, event, tool):
    """The tools' hover: light the line under the cursor; Tape Around shows the ring it would lay."""
    xy = (event.mouse_region_x, event.mouse_region_y)
    if _hover['xy'] is not None and abs(xy[0] - _hover['xy'][0]) + abs(xy[1] - _hover['xy'][1]) < 4:
        return
    _hover['xy'] = xy
    ob = MT._body(context)
    key = pick(context, xy, kinds=EDIT_KINDS if tool == 'MOVE_LEVEL' else None) \
        if ob is not None or tool != 'MOVE_LEVEL' else None
    if tool == 'MOVE_LEVEL' and key and key.startswith('item:') and (
            key == 'item:back_length' or MT.read(ob).get('items', {}).get(key[5:], {}).get('kind') == 'height'):
        key = None                                      # a height or the back length: nothing to drag
    changed = key != MT._ui['hot']
    MT._ui['hot'] = key
    if tool == 'MEASURE':
        prev = MT._ui['preview']
        MT._ui['preview'] = None
        MT._ui['preview_label'] = None
        if key is None and ob is not None and ob.type == 'MESH':
            r = around_at(context, ob, event)
            if r is not None:
                MT._ui['preview'] = (r[0][2] * 0.001, True)
                context.area.header_text_set(tx("Tape Around: {0:.1f} cm   click: keep, Tab: direction, Ctrl+wheel: tilt ({1:+.0f} deg)", r[0][0] / 10.0, ring_tilt(context)))
        if MT._ui['preview'] is None and prev is not None:
            context.area.header_text_set(None)
        changed = changed or prev is not None or MT._ui['preview'] is not None
    if changed:
        MT._redraw(context)


def ring_plane(context):
    return context.window_manager.ac9_tape_plane


def ring_tilt(context):
    return context.window_manager.ac9_tape_tilt


def around_at(context, ob, event, plane=None, tilt=None):
    """((length, surface, polyline world mm), point world mm, base normal, normal) of the ring through the body point
    under the cursor, or None."""
    hit = MT._ray_body(context, ob, event)
    if hit is None:
        return None
    cache = MT.body_cache(context, ob)
    n0 = MT._plane_normal(cache, hit[0], plane or ring_plane(context))
    n = tilted(n0, ring_tilt(context) if tilt is None else tilt, 0.0)
    r = ME.tape_around(cache.body, hit[0], n)
    return None if r is None else (r, hit[0], n0, n)


def clear_hover(context):
    _hover.update(xy=None, key=None)
    if MT._ui['hot'] is not None or MT._ui['preview'] is not None:
        MT._ui.update(hot=None, preview=None, preview_label=None)
        MT._redraw(context)


class AC9_OT_TapeTilt(bpy.types.Operator):
    """Tilt the ring forward / back"""
    bl_idname = "ac9_cloth_sim.tape_tilt"
    bl_label = "Tilt Ring"
    bl_options = {'INTERNAL'}

    delta: FloatProperty()

    def invoke(self, context, event):
        wm = context.window_manager
        wm.ac9_tape_tilt = max(-80.0, min(80.0, wm.ac9_tape_tilt + self.delta))
        _hover['xy'] = None
        hover(context, event, 'MEASURE')
        return {'FINISHED'}


class AC9_OT_MeasureTiltRing(bpy.types.Operator):
    """Edit Measure: tilt the ring under the cursor (else the picked one) forward / back"""
    bl_idname = "ac9_cloth_sim.measure_tilt_ring"
    bl_label = "Tilt Ring"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    delta: FloatProperty()

    def invoke(self, context, event):
        key = MT._ui['hot'] if (MT._ui['hot'] or '').startswith('tape:') else sel(context)
        if not key or not key.startswith('tape:'):
            return {'PASS_THROUGH'}
        t = next((t for t in MT.read(MT._body(context)).get('tapes', []) if t['name'] == key[5:]), None)
        if t is None or t['kind'] != 'around':
            return {'PASS_THROUGH'}
        select(context, key)
        sc = context.scene
        sc.ac9_msel_tilt_fb = max(-80.0, min(80.0, sc.ac9_msel_tilt_fb + self.delta))
        t = next(t for t in MT.read(MT._body(context))['tapes'] if t['name'] == key[5:])
        if context.area is not None:
            context.area.header_text_set(tx("{0}: {1} cm, tilted {2:+.0f} deg", t['name'], MT._cm(t.get('value')), t['tilt'][0]))
        _hover['xy'] = None
        return {'FINISHED'}


class AC9_OT_TapePlane(bpy.types.Operator):
    """Change the ring's direction (Auto, Level, Front-back, Side)"""
    bl_idname = "ac9_cloth_sim.tape_plane"
    bl_label = "Ring Direction"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        wm = context.window_manager
        ids = [p[0] for p in MT.PLANES]
        wm.ac9_tape_plane = ids[(ids.index(wm.ac9_tape_plane) + 1) % len(ids)]
        _hover['xy'] = None
        hover(context, event, 'MEASURE')
        return {'FINISHED'}


# ---------------------------------------------------------------- the operators

class AC9_OT_MeasureSelect(bpy.types.Operator):
    """Pick this measurement: its settings show in the Properties editor's Tool tab"""
    bl_idname = "ac9_cloth_sim.measure_select"
    bl_label = "Select Measurement"
    bl_options = {'INTERNAL'}

    key: StringProperty()

    def execute(self, context):
        select(context, '' if self.key == sel(context) else self.key)
        return {'FINISHED'}


def delete_selected(context, report=None):
    """Delete the picked measurement: a tape or a pattern length goes; a measured line goes back to where Measure Body
    puts it. Returns True when something was done."""
    s = selected(context)
    if s is None:
        return False
    kind, name, rec = s
    if kind == 'tape':
        spec, _ = store.get(context)
        held = [a for a in (spec or {}).get('attach', []) if a.get('tape') == name]
        bpy.ops.ac9_cloth_sim.measure_tape_remove(name=name)
        if held and report and not AT.ON_HOLD:
            report({'WARNING'}, rpt("{0} line(s) were held to {1}: they are held to nothing now", len(held), name))
        select(context, '')
    elif kind == 'pattern':
        bpy.ops.ac9_cloth_sim.measure_pattern_remove(name=name)
        select(context, '')
    else:
        ob = MT._body(context)
        data = MT.read(ob)
        if not data.get('offsets', {}).get(name):
            return False
        data['offsets'].pop(name, None)
        MT.measure_body(context, ob, data, only=MT.AC9_OT_MeasureMoveLevel._only(name))
        MT._redraw(context)
    return True


class AC9_OT_MeasureDelete(bpy.types.Operator):
    """Delete the picked tape or pattern length; a measured line goes back to where Measure Body puts it"""
    bl_idname = "ac9_cloth_sim.measure_delete"
    bl_label = "Delete Measurement"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return selected(context) is not None

    def execute(self, context):
        return {'FINISHED'} if delete_selected(context, self.report) else {'CANCELLED'}


def _mirror_pts(P, cx):
    P = np.array(P, float)
    P[:, 0] = 2 * cx - P[:, 0]
    return P


class AC9_OT_MeasureMirror(bpy.types.Operator):
    """Copy the picked tape to the mirror position on the other side of the body (the copy is a tape of its own)"""
    bl_idname = "ac9_cloth_sim.measure_mirror"
    bl_label = "Mirror"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = selected(context)
        return s is not None and s[0] == 'tape'

    def execute(self, context):
        _, name, t = selected(context)
        ob = MT._body(context)
        data = MT.read(ob)
        cache = MT.body_cache(context, ob)
        cx = float(np.asarray(cache.gd['torso']['o'], float)[0])        # world mm: the body's center line
        M, Mi = MT._mat(ob)

        def w(P):                                      # body-local mm -> world mm
            P = np.asarray(P, float) * 0.001
            return (np.c_[P, np.ones(len(P))] @ M.T)[:, :3] * 1000.0

        def mirror_dir(d):
            v = MT._dir_world(ob, d)
            v[0] = -v[0]
            return np.round(MT._dir_local(ob, v), 6).tolist()
        u = {k: v for k, v in t.items() if k not in ('name', 'value', 'extra')}
        if t['kind'] == 'around':
            u['p'] = MT._round(MT._to_local(ob, _mirror_pts(w([t['p']]), cx))[0], 2)
            for k in ('n', 'n0'):
                if k in t:
                    u[k] = mirror_dir(t[k])
            if 'tilt' in t:
                u['tilt'] = [t['tilt'][0], -t['tilt'][1]]
        else:
            u['pts'] = MT._round(MT._to_local(ob, _mirror_pts(w(t['pts']), cx)), 2)
            if 'normals' in t:
                u['normals'] = [mirror_dir(n) for n in t['normals']]
        side = " (L)" if w([u.get('p') or u['pts'][0]])[0][0] > cx else " (R)"
        base = name[:-4] if name.endswith((" (L)", " (R)")) else name
        names = {x['name'] for x in data.get('tapes', [])}
        nm, k = base + side, 2
        while nm in names:
            nm, k = f"{base}{side} {k}", k + 1
        u['name'] = nm
        if not MT.store_tape(ob, cache, u, data):
            self.report({'WARNING'}, "The mirror position is off the body")
            return {'CANCELLED'}
        select(context, 'tape:' + nm)
        self.report({'INFO'}, rpt("{0}: {1} cm", nm, MT._cm(tape_value(u))))
        return {'FINISHED'}


def _rename_tape(context, old, new):
    ob = MT._body(context)
    data = MT.read(ob)
    if any(t['name'] == new for t in data.get('tapes', [])) or new in MT.LABEL:
        return False
    for t in data.get('tapes', []):
        if t['name'] == old:
            t['name'] = new
    geo = MT.read(ob, MT.GEO)
    if old in geo.get('tapes', {}):
        geo['tapes'][new] = geo['tapes'].pop(old)
    MT.write(ob, data)
    MT.write(ob, geo, MT.GEO)
    spec, _ = store.get(context)
    if spec and any(a.get('tape') == old for a in spec.get('attach', [])):     # the lines held to it follow it
        spec = S.loads(S.dumps(spec))
        for a in spec['attach']:
            if a.get('tape') == old:
                a['tape'] = new
        store.put(context, spec)
    return True


def _rename_pattern(context, old, new):
    spec, _ = store.get(context)
    spec = S.loads(S.dumps(spec))
    m = spec.setdefault('measure', {})
    if any(r['name'] == new for r in m.get('pattern', [])):
        return False
    for r in m.get('pattern', []):
        if r['name'] == old:
            r['name'] = new
    tg = m.get('targets', {})
    if old in tg:
        tg[new] = tg.pop(old)
    store.put(context, spec)
    return True


# ---------------------------------------------------------------- the Tool tab's fields (they read / write the pick)

def _get_name(self):
    s = selected(bpy.context)
    return '' if s is None else s[1]


def _set_name(self, value):
    context = bpy.context
    s = selected(context)
    value = value.strip()
    if s is None or s[0] == 'item' or not value or value == s[1]:
        return
    ok = (_rename_tape if s[0] == 'tape' else _rename_pattern)(context, s[1], value)
    if ok:
        context.scene.ac9_cloth_sim_measure.sel = f"{s[0]}:{value}"
        MT._redraw(context)


def _get_main(self):
    s = selected(bpy.context)
    return 1 if s is not None and s[0] == 'tape' and s[2].get('main') == 'straight' else 0


def _set_main(self, value):
    context = bpy.context
    s = selected(context)
    if s is None or s[0] != 'tape' or s[2]['kind'] != 'along':
        return
    ob = MT._body(context)
    data = MT.read(ob)
    for t in data.get('tapes', []):
        if t['name'] == s[1]:
            t['main'] = 'straight' if value == 1 else 'surface'
    MT.write(ob, data)
    MT._redraw(context)


def _tilt_get(i):
    def get(self):
        s = selected(bpy.context)
        if s is None or s[0] != 'tape' or s[2]['kind'] != 'around':
            return 0.0
        return float((s[2].get('tilt') or [0.0, 0.0])[i])
    return get


def _tilt_set(i):
    def set_(self, value):
        context = bpy.context
        s = selected(context)
        if s is None or s[0] != 'tape' or s[2]['kind'] != 'around':
            return
        ob = MT._body(context)
        data = MT.read(ob)
        t = next(t for t in data['tapes'] if t['name'] == s[1])
        tilt = list(t.get('tilt') or [0.0, 0.0])
        tilt[i] = round(float(value), 1)
        n0 = MT._dir_world(ob, t.get('n0') or t['n'])
        t.setdefault('n0', np.round(MT._dir_local(ob, n0), 6).tolist())
        t['tilt'] = tilt
        t['n'] = np.round(MT._dir_local(ob, tilted(n0, tilt[0], tilt[1])), 6).tolist()
        if MT.store_tape(ob, MT.body_cache(context, ob), t, data):
            MT._redraw(context)
    return set_


PROPS = {                                       # on the Scene (they are about the scene's pick)
    'ac9_msel_name': StringProperty(name="Name", get=_get_name, set=_set_name,
                               description="The measurement's name (the lines held to a tape follow a new name)"),
    'ac9_msel_main': EnumProperty(name="Length", translation_context=CTX, get=_get_main, set=_set_main,
                             items=[('SURFACE', "Along the Body", "Along the body's surface through the points"),
                                    ('STRAIGHT', "Straight", "Straight between the points (through the air)")]),
    'ac9_msel_tilt_fb': FloatProperty(name="Tilt Front / Back", get=_tilt_get(0), set=_tilt_set(0), min=-80, max=80,
                                 precision=1,
                                 description="The ring tilted forward (+, front down) or back (deg)"),
    'ac9_msel_tilt_side': FloatProperty(name="Tilt Side", get=_tilt_get(1), set=_tilt_set(1), min=-80, max=80,
                                   precision=1, description="The ring tilted to the side (deg)"),
}


def draw_tool_tab(layout, context):
    """The Tool tab's AC9 Measurement."""
    s = selected(context)
    if s is None:
        return
    kind, name, rec = s
    mp = context.scene
    spec, _ = store.get(context)
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    if kind == 'item':
        col.label(text=tx(MT.LABEL.get(name, name)), icon='DRIVER_DISTANCE')
        col.label(text=tx("{0} (Measure Body)", tx(KINDS.get(rec['kind'], rec['kind']))))
        col.label(text=tx("{0} cm", MT._cm(rec['value'])))
        if ME.HELP.get(name):
            col.label(text=tx(ME.HELP[name])[:60])
        off = MT.read(MT._body(context)).get('offsets', {}).get(name)
        if off:
            col.label(text=tx("Moved {0:+.0f} mm (Edit Measure)", off))
    elif kind == 'tape':
        col.prop(mp, "ac9_msel_name")
        col.label(text=tx(KINDS.get(rec['kind'], rec['kind'])), icon='MESH_CIRCLE' if rec['kind'] == 'around' else
                  ('IPO_LINEAR' if rec['kind'] == 'straight' else 'CURVE_PATH'))
        if rec['kind'] == 'along':
            col.prop(mp, "ac9_msel_main", expand=True)
            col.label(text=tx("Along {0} cm  /  straight {1} cm", MT._cm(rec.get('value')), MT._cm(rec.get('extra'))))
        elif rec['kind'] == 'straight':
            col.label(text=tx("Taut {0} cm  /  through {1} cm", MT._cm(rec.get('value')), MT._cm(rec.get('extra'))))
        else:
            col.label(text=tx("{0} cm", MT._cm(rec.get('value'))))
        if rec['kind'] == 'around':
            col.prop(mp, "ac9_msel_tilt_fb")
            col.prop(mp, "ac9_msel_tilt_side")
        held = [a for a in (spec or {}).get('attach', []) if a.get('tape') == name]
        if held and not AT.ON_HOLD:
            col.label(text=tx("{0} line(s) held to it", len(held)), icon='CONSTRAINT')
    else:
        col.prop(mp, "ac9_msel_name")
        col.label(text="On the pattern", icon='DRIVER_DISTANCE')
    tg = MT._target(spec, name) if kind != 'tape' else None
    if kind != 'tape' and spec is not None:
        col.operator("ac9_cloth_sim.measure_set_target", text=tx("Target: {0} cm", MT._cm(tg))).key = name
    row = layout.row(align=True)
    if kind == 'tape':
        row.operator("ac9_cloth_sim.measure_mirror", icon='MOD_MIRROR')
    if kind == 'item':
        sub = row.row(align=True)
        sub.enabled = bool(MT.read(MT._body(context)).get('offsets', {}).get(name))
        sub.operator("ac9_cloth_sim.measure_delete", text="Back to Its Level", icon='LOOP_BACK')
    else:
        row.operator("ac9_cloth_sim.measure_delete", text="Delete", icon='TRASH')


def draw_list(layout, context):
    """Lists > Tapes: every measurement as a row that picks it. A list only shows and picks: the tools are in the
    Measure panel and the toolbar (user 2026-10-21)."""
    ob = MT._body(context)
    data = MT.read(ob)
    spec, outlines = store.get(context)
    cur = sel(context)
    col = layout.column(align=True)
    rows = [(f"item:{k}", MT.LABEL.get(k, k), it['value'], 'DRIVER_DISTANCE') for k, it in data.get('items', {}).items()]
    rows += [(f"tape:{t['name']}", t['name'], tape_value(t),
              'MESH_CIRCLE' if t['kind'] == 'around' else ('IPO_LINEAR' if t['kind'] == 'straight' else 'CURVE_PATH'))
             for t in data.get('tapes', [])]
    if spec and outlines:
        rows += [(f"pattern:{r['name']}", r['name'], al if al is not None else st, 'MESH_GRID')
                 for r, st, al, _, _ in MT._pattern_rows(spec, outlines)]
    from . import ui
    for key, label, v, icon in rows:               # the rows of every list (REVIEW_fable_lists_ui_2026-10-25 section 2)
        ui.draw_pick_row(col, "ac9_cloth_sim.measure_select", label[:18], depress=key == cur, icon=icon,
                         right=tx("{0} cm", MT._cm(v)), right_units=3.0).key = key
    if not rows:
        layout.label(text="Measure Body (Measure panel), or lay a tape", icon='INFO')


CLASSES = (AC9_OT_TapeTilt, AC9_OT_MeasureTiltRing, AC9_OT_TapePlane, AC9_OT_MeasureSelect, AC9_OT_MeasureDelete, AC9_OT_MeasureMirror)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for k, v in PROPS.items():
        setattr(bpy.types.Scene, k, v)
    bpy.types.WindowManager.ac9_tape_plane = EnumProperty(name="Direction", items=MT.PLANES, default='AUTO',
                                                          description="The ring's direction (Tab)")
    bpy.types.WindowManager.ac9_tape_tilt = FloatProperty(name="Tilt", default=0.0, min=-80.0, max=80.0,
                                                          precision=0, description="The ring tilted forward (+) or "
                                                          "back, deg (Ctrl+wheel; Shift: by 1)")


def unregister():
    for k in PROPS:
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
    del bpy.types.WindowManager.ac9_tape_tilt
    del bpy.types.WindowManager.ac9_tape_plane
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
