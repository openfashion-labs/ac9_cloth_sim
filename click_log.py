# SPDX-License-Identifier: GPL-3.0-or-later
"""Every 3D click of the AC9 tools written to a file: what it landed on and what came of it (user 2026-11-03: a click
on a piece sometimes did nothing, in tools that clearly work on pieces, and nothing told why afterwards).

One line per click, JSON, in <temp>/ac9_cloth_sim_clicks.log (cut to its last half past 256 KB). A click with no line
never reached an AC9 operator (another modal took it, the tool's keymap was not active, an operator's poll failed).
Logging never stops a click: any error in here is swallowed."""
import functools
import json
import os
import tempfile
import time

PATH = os.path.join(tempfile.gettempdir(), "ac9_cloth_sim_clicks.log")
LIMIT = 256 * 1024


def _write(rec):
    try:
        if os.path.exists(PATH) and os.path.getsize(PATH) > LIMIT:
            with open(PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _names(obs):
    return [o.name for o in obs][:8]


def _state(context):
    """What the selection looks like (read before and after a click)."""
    try:
        act = context.view_layer.objects.active
        return {'active': act.name if act else None, 'selected': _names(context.view_layer.objects.selected)}
    except Exception:
        return {}


def _hit(context, event):
    """What the click lands on, as the tools see it (toolbar.under), with what decides a click on the garment."""
    from . import toolbar as TB
    from . import dress as D
    rec = {}
    try:
        kind, ob, idx = TB.under(context, event.mouse_region_x, event.mouse_region_y)
        rec.update(kind=kind, hit=ob.name if ob else None, hit_type=ob.type if ob else None, face=idx)
        if ob is not None:
            rec['hit_select_off'] = bool(ob.hide_select)
            rec['hit_display'] = ob.display_type
        if kind in ('PLANE', 'NONE'):
            # the pattern's layout plane (unbounded) taken for the click: what mesh lies along the ray, and how far
            from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
            xy = (event.mouse_region_x, event.mouse_region_y)
            o = region_2d_to_origin_3d(context.region, context.region_data, xy)
            d = region_2d_to_vector_3d(context.region, context.region_data, xy)
            ok, loc, _n, _i, mob, _m = TB.viewport_ray_cast(context, o, d)
            if ok and mob is not None:
                rec['mesh_behind'] = mob.original.name
                rec['mesh_dist'] = round((loc - o).length, 4)
            emp = TB.PV.layout_empty(context)
            if emp is not None:
                from mathutils import Vector
                from mathutils.geometry import intersect_line_plane
                M = emp.matrix_world
                hit = intersect_line_plane(o, o + d, M.translation, (M.to_3x3() @ Vector((0, 0, 1))).normalized())
                if hit is not None and (hit - o).dot(d) > 0:
                    rec['plane_dist'] = round((hit - o).length, 4)
        if kind == 'GARMENT':
            rec['piece'] = TB.garment_piece(ob, idx)
            ev = ob.evaluated_get(context.evaluated_depsgraph_get())
            rec['faces'] = [len(ob.data.polygons), len(ev.data.polygons)]   # differ: the face index is shifted
            rec['mods'] = [m.type for m in ob.modifiers if m.show_viewport]
    except Exception as exc:
        rec['hit_error'] = repr(exc)
    try:
        rec['running'] = D.AC9_OT_SimDress.running()
    except Exception:
        pass
    return rec


def logged(invoke):
    """Decorates an operator's invoke: one line per click."""
    @functools.wraps(invoke)
    def wrapper(self, context, event):
        rec = {}
        try:
            from . import toolbar as TB
            TB._branch['name'] = None
            rec = {'t': time.strftime("%H:%M:%S"), 'op': self.bl_idname, 'tool': getattr(self, 'tool', None),
                   'active_tool': TB.active_tool(context), 'mode': context.mode,
                   'event': [event.type, event.value, event.shift, event.ctrl, event.alt],
                   'xy': [event.mouse_region_x, event.mouse_region_y], 'before': _state(context)}
            rec.update(_hit(context, event))
        except Exception as exc:
            rec['log_error'] = repr(exc)
        try:
            res = invoke(self, context, event)
        except Exception as exc:
            rec['raised'] = repr(exc)
            _write(rec)
            raise
        try:
            from . import toolbar as TB
            rec['result'] = sorted(res) if res else res
            rec['note'] = TB._hint.get('text')
            rec['branch'] = TB._branch.get('name')
            TB._branch['name'] = None
            rec['after'] = _state(context)
        except Exception as exc:
            rec['log_error'] = repr(exc)
        _write(rec)
        return res
    return wrapper


def logged_end(modal):
    """Decorates a modal begun by a logged click: one more line when it ends (a press picks on its release)."""
    @functools.wraps(modal)
    def wrapper(self, context, event):
        res = modal(self, context, event)
        try:
            if res and not ({'RUNNING_MODAL', 'PASS_THROUGH'} & set(res)):
                _write({'t': time.strftime("%H:%M:%S"), 'op': self.bl_idname, 'end': sorted(res),
                        'event': [event.type, event.value], 'after': _state(context)})
        except Exception:
            pass
        return res
    return wrapper
