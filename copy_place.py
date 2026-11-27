# SPDX-License-Identifier: GPL-3.0-or-later
"""One transaction for every pattern copy: preview at the pointer, click to publish, cancel without writes."""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from . import store, pattern_view as PV, view_visibility as VV
from .core import draft as D
from .i18n import tx, rpt

_active = None


def viewport(context, event):
    """The visible pattern WINDOW under the pointer, excluding overlaid sidebars and headers."""
    if event is None or context.window is None:
        return None
    x, y = event.mouse_x, event.mouse_y
    for area in context.window.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        inside = lambda r: r.width > 0 and r.height > 0 and r.x <= x < r.x+r.width and r.y <= y < r.y+r.height
        if any(inside(r) for r in area.regions if r.type in {'UI','TOOLS','HEADER','TOOL_HEADER'}):
            continue
        region = next((r for r in area.regions if r.type == 'WINDOW' and inside(r)), None)
        if region is not None:
            with context.temp_override(area=area, region=region):
                if VV.allows_pattern(context):
                    return area, region
    return None


def move_candidate(candidate, names, anchor, point):
    """Move only the new copies, never their original twins; keep each 3D placement anchored to its shape."""
    preview = D.edit_copy(candidate, names)
    shift = np.asarray(point, float) - np.asarray(anchor, float)
    for name in names:
        D.transform_piece(preview, name, shift=shift)
    return preview


class CopyPlacement:
    """Operator mixin. Subclasses build a private candidate and publish it only from _copy_finish."""

    def execute(self, context):
        # EXEC_DEFAULT is the same interaction as a button; it must never leave a copy at an invisible default.
        return self._copy_start(context, None)

    def invoke(self, context, event):
        return self._copy_start(context, event)

    def _copy_start(self, context, event):
        global _active
        if _active is not None or context.window is None:
            self.report({'WARNING'}, rpt("Finish the current copy placement first"))
            return {'CANCELLED'}
        try:
            original, _, self._copy_owner = store.begin_edit(context)
            self._copy_original = original
            self._copy_event = event
            candidate, names, anchor_name = self._copy_build(context, original)
            if not names:
                return {'CANCELLED'}
        except (ValueError, store.EditChanged) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        self._copy_candidate, self._copy_names = candidate, names
        self._copy_anchor_name = anchor_name
        self._copy_anchor = D.piece_center(D.piece(candidate, anchor_name))
        self._copy_preview, self._copy_area = None, None
        self._copy_segments, self._copy_handle = [], None
        self._copy_ready = False
        _active = self
        self._copy_handle = bpy.types.SpaceView3D.draw_handler_add(self._copy_draw, (), 'WINDOW', 'POST_VIEW')
        self._copy_hint(context)
        self._copy_update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _copy_hint(self, context):
        context.workspace.status_text_set(tx("Copy: click in the pattern view to place; Esc / right click: cancel"))

    def _copy_update(self, context, event):
        from . import draft_tool as DT
        target = viewport(context, event)
        self._copy_ready = False
        self._copy_segments = []
        if target is not None:
            area, region = target
            with context.temp_override(area=area, region=region):
                M = PV.layout_matrix(context)
                q = DT.mouse_mm(context, M, event.mouse_x-region.x, event.mouse_y-region.y)
                if q is not None:
                    self._copy_preview = move_candidate(self._copy_candidate, self._copy_names, self._copy_anchor, q)
                    self._copy_segments = DT._outline_segs(M, self._copy_preview, self._copy_names)
                    self._copy_area = area.as_pointer()
                    self._copy_ready = True
        DT._redraw_views()

    def _copy_draw(self):
        context = bpy.context
        if not self._copy_ready or context.area is None or context.area.as_pointer() != self._copy_area:
            return
        if not VV.allows_pattern(context) or not self._copy_segments:
            return
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        try:
            shader.bind()
            shader.uniform_float('viewportSize', (context.region.width, context.region.height))
            shader.uniform_float('lineWidth', 3.0)
            shader.uniform_float('color', (1.0, 0.85, 0.2, 1.0))
            batch_for_shader(shader, 'LINES', {'pos':self._copy_segments}).draw(shader)
        finally:
            gpu.state.blend_set('NONE')

    def _copy_end(self, context):
        global _active
        from . import draft_tool as DT
        if getattr(self, '_copy_handle', None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._copy_handle, 'WINDOW')
            self._copy_handle = None
        self._copy_ready = False
        if _active is self:
            _active = None
        context.workspace.status_text_set(None)
        DT._redraw_views()

    def cancel(self, context):
        self._copy_end(context)

    def modal(self, context, event):
        if event.value == 'PRESS' and event.type in {'ESC','RIGHTMOUSE'}:
            self._copy_end(context)
            return {'CANCELLED'}
        if not self._copy_owner.matches(context, content=False):
            self._copy_end(context)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE','INBETWEEN_MOUSEMOVE','LEFTMOUSE'}:
            self._copy_update(context,event)
        if event.value == 'PRESS' and event.type == 'LEFTMOUSE' and self._copy_ready:
            try:
                self._copy_owner.require(context)
                PV.require_integrity(context)
                bad = D.problems(self._copy_preview,self._copy_names)
                if bad:
                    raise ValueError(bad[0])
                self._copy_end(context)
                return self._copy_finish(context,self._copy_preview)
            except (ValueError, store.EditChanged) as exc:
                self._copy_end(context)
                self.report({'WARNING'},str(exc))
                return {'CANCELLED'}
        # Navigation is allowed; the next move/click recomputes the same pointer position in the new view.
        if event.type in {'MIDDLEMOUSE','WHEELUPMOUSE','WHEELDOWNMOUSE','NDOF_MOTION'}:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}
