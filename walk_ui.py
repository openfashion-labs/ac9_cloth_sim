# SPDX-License-Identifier: GPL-3.0-or-later
"""Walk: the active seam's two sides compared notch by notch (core/walk.py), as a sub-panel of Seams and on the flat
pattern. Notches are put, slid and removed with the Edit Pieces tool (N, drag, X); Match Notches lays one side's
notches where the other's are (the ease spread in proportion); Notches from BOM reads the BOM's."""
import textwrap
from .i18n import rpt, tx, wrap
from . import units as U

import blf
import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader

from . import draft_tool as T
from . import overlay
from . import pattern_view as PV
from . import store
from . import view_visibility as VV
from .core import draft as D
from .core import spec as S
from .core import walk as W

NOTCH_MM = 6.0        # drawn length of a notch, into the piece
MAX_ROWS = 8          # segments listed in the panel
_handles = []
_cache = {'key': None}


def _walk(context, spec, outlines, sm):
    return W.walk(spec, sm, outlines, reverse=bool(getattr(context.window_manager, 'ac9_walk_reverse', False)))


class AC9_OT_SimMatchNotches(bpy.types.Operator):
    """Lay the notches of one side of the active seam where the other side's are, at the same fraction of each side's length (the seam's ease spread over its segments in proportion; twins follow)"""
    bl_idname = "ac9_cloth_sim.match_notches"
    bl_label = "Match Notches"
    bl_options = {'REGISTER', 'UNDO'}

    side: bpy.props.EnumProperty(items=[('a', "A", "Move side A's notches"), ('b', "B", "Move side B's notches")])

    @classmethod
    def poll(cls, context):
        spec, _ = store.get(context)
        return store.active_seam(context, spec) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        sm = store.active_seam(context, spec)
        sp = S.loads(S.dumps(spec))
        try:
            ch, n = D.match_notches(sp, sm['id'], self.side)
        except ValueError as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        T.commit(context, sp, ch)                      # notches move no cloth: no carry
        w = W.walk(sp, next(x for x in sp['seams'] if x['id'] == sm['id']))
        self.report({'INFO'}, rpt("{0}: {1} notch(es) on {2}; each segment eases {3:+.1f} %", sm['id'], n, self.side.upper(), w['pct']))
        return {'FINISHED'}


class AC9_OT_SimNotchesFromBOM(bpy.types.Operator):
    """Read the notches of the BOM onto the pattern (the pieces read from it; notches already there are kept)"""
    bl_idname = "ac9_cloth_sim.notches_from_bom"
    bl_label = "Notches from BOM"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec, _ = store.get(context)
        return spec is not None and S.from_bom(spec)

    def execute(self, context):
        from .core import notch_import as NI
        spec, _ = store.get(context)
        sp = S.loads(S.dumps(spec))
        try:
            rep = NI.import_notches(sp)
        except (OSError, ValueError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        T.commit(context, sp, [p['name'] for p in sp['pieces']])
        self.report({'INFO'}, rpt("{0} notch(es) on {1} piece(s)", rep['added'], rep['pieces'])
                              + (rpt(", {0} already there", rep['already']) if rep['already'] else "")
                              + (rpt(", {0} on internal lines skipped", rep['internal']) if rep['internal'] else ""))
        return {'FINISHED'}


class AC9_PT_SimWalk(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AC9 Cloth Sim"
    bl_label = "Lengths & Notches"         # "Walk" before 2026-10-20 (the patternmaker's word did not read)
    bl_parent_id = "AC9_PT_SimSeams"

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def draw(self, context):
        layout = self.layout
        spec, outlines = store.get(context)
        sm = store.active_seam(context, spec)
        if sm is None:
            layout.label(text="Pick a seam in the list")
            return
        try:
            w = _walk(context, spec, outlines, sm)
        except Exception:
            layout.label(text="This seam cannot be walked", icon='ERROR')
            return
        col = layout.column(align=True)
        col.label(text=tx("Ease-in {0} ({1:+.1f} %)", U.fmt(context, w['ease'], signed=True), w['pct']))
        col.label(text=tx("Notches  A {0}   B {1}", w['na'], w['nb']))
        if w['unpaired']:
            r = col.row()
            r.alert = True
            r.label(text=tx("{0} notch(es) unpaired", w['unpaired']), icon='ERROR')
        layout.prop(context.window_manager, "ac9_walk_reverse", text="From the other end", toggle=True)
        if len(w['segments']) > 1:
            box = layout.box()
            c = box.column(align=True)
            c.label(text="   A / B        ease-in   total")
            for i, g in enumerate(w['segments'][:MAX_ROWS]):
                at = U.num(context, g['cum'], signed=True) if i < len(w['segments']) - 1 else "end"
                c.label(text=tx("{0}  {1} / {2}   {3}   {4}", i + 1, U.num(context, g['la']), U.num(context, g['lb']), U.num(context, g['ease'], signed=True), at))
            if len(w['segments']) > MAX_ROWS:
                c.label(text=tx("... {0} more", len(w['segments']) - MAX_ROWS))
        layout.label(text="Lay the notches of")
        row = layout.row(align=True)
        row.operator("ac9_cloth_sim.match_notches", text="A").side = 'a'     # as the seam's buttons (user 2026-10-23)
        row.operator("ac9_cloth_sim.match_notches", text="B").side = 'b'
        if S.from_bom(spec):
            layout.operator("ac9_cloth_sim.notches_from_bom", icon='IMPORT')
        for ln in wrap(tx("Edit Seams: N puts a notch here and its partner on the other side (Ctrl+N: this side only). Edit Pieces: N one notch, drag slides it, X removes it"), 34):
            layout.label(text=ln)


# ---------------------------------------------------------------- drawing

def _build(context, spec, outlines):
    visible = VV.pattern_names(context, spec)
    M = np.array(PV.layout_matrix(context))
    act = store.active_seam(context, spec)
    rev = bool(getattr(context.window_manager, 'ac9_walk_reverse', False))
    key = (store._cache['key'], M.tobytes(), act['id'] if act else None, rev, visible)
    if _cache['key'] == key:
        return _cache

    def world(P2):
        P2 = np.asarray(P2, float).reshape(-1, 2) * PV.MM
        H = np.c_[P2, np.zeros(len(P2)), np.ones(len(P2))]
        return (H @ M.T)[:, :3]
    ticks, pairs, labels = [], [], []
    pos = {}
    for pc in spec['pieces']:
        if pc['name'] not in visible or not pc.get('enabled', True) or not pc.get('notches'):
            continue
        ol = outlines[pc['name']]
        for n in pc['notches']:
            if n['edge'] not in ol.edges:
                continue
            s = ol.s_of(n['edge'], float(n['t']))
            p = ol.at(s)
            d = ol.at(s + 0.5) - ol.at(s - 0.5)
            d /= max(np.linalg.norm(d), 1e-9)
            inward = np.array([-d[1], d[0]])            # ccw outline: the inside is to the left
            ticks += [tuple(x) for x in world([p, p + NOTCH_MM * inward])]
            pos[(pc['name'], n['id'])] = p
    if act is not None:
        try:
            w = W.walk(spec, act, outlines, reverse=rev)
            for pr in w['pairs']:
                qa, qb = pos.get((act['a']['piece'], pr['a'])), pos.get((act['b']['piece'], pr['b']))
                if qa is not None and qb is not None:
                    pairs += [tuple(x) for x in world([qa, qb])]
            if act['a']['piece'] in visible and 'line' not in act['a']:
                ol = outlines[act['a']['piece']]
                s0, sg = ol.s_of(*act['a']['from']), (1 if act['a']['dir'] > 0 else -1)
                La = w['La']
                prev = 0.0
                for g in w['segments']:
                    mid = 0.5 * (prev + g['at_a'])
                    u = (La - mid) if rev else mid
                    labels.append((world([ol.at(s0 + sg * u)])[0], g['ease'], len(w['segments']) > 1))
                    prev = g['at_a']
        except Exception:
            pass
    _cache.update(key=key, ticks=ticks, pairs=pairs, labels=labels)
    return _cache


def _draw_view():
    context = bpy.context
    if not VV.allows_pattern(context):
        return
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if not overlay.shows(props, 'show_notches'):
        return
    spec, outlines = store.get(context)
    if not spec or PV.layout_empty(context) is None:
        return
    g = _build(context, spec, outlines)
    if not g['ticks'] and not g['pairs']:
        return
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    if g['ticks']:
        sh.uniform_float('lineWidth', 2.5)
        sh.uniform_float('color', (1.0, 0.95, 0.4, 1.0))
        batch_for_shader(sh, 'LINES', {'pos': g['ticks']}).draw(sh)
    if g['pairs']:
        sh.uniform_float('lineWidth', 1.0)
        sh.uniform_float('color', (1.0, 0.95, 0.4, 0.45))
        batch_for_shader(sh, 'LINES', {'pos': g['pairs']}).draw(sh)
    gpu.state.blend_set('NONE')


def _draw_px():
    context = bpy.context
    if not VV.allows_pattern(context):
        return
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if not overlay.shows(props, 'show_notches'):
        return
    spec, outlines = store.get(context)
    if spec is None or not _build(context, spec, outlines)['labels']:
        return
    region, rv3d = context.region, context.region_data
    fid = 0
    blf.size(fid, 13)
    for p, ease, many in _cache['labels']:
        if not many:
            continue
        xy = location_3d_to_region_2d(region, rv3d, p)
        if xy is None:
            continue
        txt = f"{ease:+.1f}"
        w, _ = blf.dimensions(fid, txt)
        blf.color(fid, *T.ease_color(ease))
        blf.position(fid, xy[0] - w / 2, xy[1] + 6, 0)
        blf.draw(fid, txt)


def invalidate():
    _cache['key'] = None


CLASSES = (AC9_OT_SimMatchNotches, AC9_OT_SimNotchesFromBOM)   # AC9_PT_SimWalk: its draw only, for the Tool tab (tool_tab.py); the old sidebar Seams is gone (user 2026-10-22)


def register():
    bpy.types.WindowManager.ac9_walk_reverse = bpy.props.BoolProperty(
        name="From the other end", description="Walk the seam from its other end (the running difference at each notch "
                                               "counted from there)", default=False)
    for c in CLASSES:
        bpy.utils.register_class(c)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_px, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    invalidate()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.ac9_walk_reverse
