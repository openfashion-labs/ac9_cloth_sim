# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration (Blender only). Split from __init__ so core/ imports without bpy."""
import ast
import importlib
import os
import sys
from . import distribution as _distribution

# Reloaded by __init__ itself, before this file.
_SELF_RELOADED = (__name__, __package__ + '.companion_state')


def _loaded_submodules():
    """Every module of this add-on now in sys.modules, except this file and the ones __init__ reloads."""
    head = __package__ + '.'
    return [name for name, mod in list(sys.modules.items())
            if name.startswith(head) and name not in _SELF_RELOADED and mod is not None]


def _module_imports(name, mod):
    """The add-on modules `name` imports at its top level (inside def/class is resolved at call time, so it is left out)."""
    path = getattr(mod, '__file__', None)
    try:
        with open(path, 'rb') as f:
            tree = ast.parse(f.read())
    except (OSError, TypeError, ValueError, SyntaxError):
        return []
    is_pkg = os.path.basename(path or '') == '__init__.py'
    here = name if is_pkg else name.rpartition('.')[0]
    out = []

    def walk(body):
        for node in body:
            if isinstance(node, ast.ImportFrom):
                base = here
                for _ in range(max(node.level - 1, 0)):
                    base = base.rpartition('.')[0]
                if node.level == 0:
                    base = node.module or ''
                elif node.module:
                    base = base + '.' + node.module
                out.append(base)
                out.extend(base + '.' + a.name for a in node.names)
            elif isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for part in ('body', 'orelse', 'finalbody'):
                    walk(getattr(node, part, []))
                for h in getattr(node, 'handlers', []):
                    walk(h.body)
    walk(tree.body)
    return out


def _reload_order(names):
    """Dependencies first, so a module's `from .x import y` binds the reloaded y (a cycle is cut where it closes)."""
    names = sorted(names)
    known = set(names)
    deps = {n: [d for d in dict.fromkeys(_module_imports(n, sys.modules[n])) if d in known and d != n] for n in names}
    order, state = [], {}

    def visit(n):
        if state.get(n):
            return
        state[n] = 1
        for d in deps[n]:
            visit(d)
        order.append(n)
    for n in names:
        visit(n)
    return order


def _reload_all_submodules():
    """Reload every loaded module of the add-on in place (same module objects, so state they keep across a reload
    stays). A module whose file is gone in the new version is dropped from sys.modules."""
    for name in _reload_order(_loaded_submodules()):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
        except ModuleNotFoundError:
            if not os.path.exists(getattr(mod, '__file__', None) or ''):
                sys.modules.pop(name, None)
            else:
                raise


# F3 (Reload Scripts) and an install over a loaded version re-run this file: reload every submodule first, not a
# hand-kept list (a module only imported by other modules, units.py, was missed and an upgrade stopped on its new names).
if "_spec" in locals():
    if '_latency_trace' in locals():
        _latency_trace.unregister()
    if '_dress' in locals() and hasattr(_dress, 'standby_end'):  # noqa: F821
        _dress.standby_end()  # noqa: F821
    if "_place_targets" in locals():
        _place_targets.unregister()  # noqa: F821
    _reload_all_submodules()

from .core import shape_source as _shape_source
from .core import prepare_plan as _prepare_plan
from . import prepare_build as _prepare_build
from . import undo_guard as _undo_guard
from . import latency_trace as _latency_trace
from . import build_ahead as _build_ahead
from .core import spec as _spec                     # noqa: E402
from .core import piece_table as _piece_table       # noqa: E402
from .core import phase_from as _phase_from         # noqa: E402
from .solver import fast_live_contract as _fast_contract
from .core import fast_live as _fast_live
from .solver import native_three_contract as _native_three_contract
from .core import native_three as _native_three
from .solver import native_runtime_io as _native_runtime_io
from .core import native_three_io as _native_three_io
from .solver import preload_status as _preload_status # noqa: E402
from .solver import preload_config as _preload_config
from .solver import prepared_live as _prepared_live
from .solver import process_tree as _process_tree
from .core import prepared_candidate as _prepared_candidate
from .core import live_session as _live_session
from .core import fold_reference as _fold_reference # noqa: E402
from .core import bom_parse as _bom_parse           # noqa: E402
from .core import bom_to_spec as _bom_to_spec       # noqa: E402
from .core import body_axes as _body_axes           # noqa: E402
from .core import pattern_build as _pattern_build   # noqa: E402
from .core import roles as _roles                   # noqa: E402
from .core import fabric as _fabric                 # noqa: E402
from .core import look as _look                     # noqa: E402
from .core import recipe as _recipe                 # noqa: E402
from .core import presets as _presets               # noqa: E402
from .core import simlog as _simlog                 # noqa: E402
from .core import attach as _attach                 # noqa: E402
from . import i18n_ja as _i18n_ja                   # noqa: E402
from . import i18n as _i18n                         # noqa: E402
from .core import _i18n as _core_i18n                # noqa: E402
from . import look_reference as _look_reference     # noqa: E402
from . import store as _store                       # noqa: E402
from . import look_apply as _look_apply             # noqa: E402
from . import pattern_view as _pattern_view         # noqa: E402
from . import placed_view as _placed_view           # noqa: E402
from . import selection as _selection               # noqa: E402
from . import result_reference as _result_reference # noqa: E402
from . import display_state as _display_state       # noqa: E402
from . import garment_view as _garment_view         # noqa: E402
from . import piece_hide as _piece_hide             # noqa: E402
from . import live_key as _live_key                 # noqa: E402
from . import view_visibility as _view_visibility   # noqa: E402
from . import view_setup as _view_setup             # noqa: E402
from . import text_band as _text_band               # noqa: E402
from . import overlay as _overlay                   # noqa: E402
from . import silhouette as _silhouette             # noqa: E402
from . import piece_look as _piece_look             # noqa: E402
from . import diagnose as _diagnose                 # noqa: E402
from . import click_log as _click_log               # noqa: E402
from . import box_pieces as _box_pieces             # noqa: E402
from . import toolbar as _toolbar                   # noqa: E402
from . import seam_tool as _seam_tool               # noqa: E402
from .core import seam_edit as _seam_edit           # noqa: E402
from . import seam_edit_tool as _seam_edit_tool     # noqa: E402
from .core import draft as _draft                   # noqa: E402
from .core import extend as _extend                 # noqa: E402
from .core import snap as _snap                     # noqa: E402
from .core import transfer as _transfer             # noqa: E402
from .core import dxf_out as _dxf_out               # noqa: E402
from .core import walk as _walk                     # noqa: E402
from .core import notch_import as _notch_import     # noqa: E402
from .core import dart as _dart                     # noqa: E402
from .core import spread as _spread                 # noqa: E402
from .core import cut as _cut                       # noqa: E402
from .core import blend as _blend                   # noqa: E402
from . import draft_tool as _draft_tool             # noqa: E402
from . import shape_tool as _shape_tool             # noqa: E402
from . import pre_click as _pre_click               # noqa: E402
from . import walk_ui as _walk_ui                   # noqa: E402
from . import dart_ui as _dart_ui                   # noqa: E402
from . import spread_ui as _spread_ui               # noqa: E402
from . import blend_ui as _blend_ui                 # noqa: E402
from . import outside as _outside                   # noqa: E402
from . import placement as _placement               # noqa: E402
from .core import buttons as _buttons               # noqa: E402
from .core import zippers as _zippers               # noqa: E402
from .core import button_shape as _button_shape     # noqa: E402
from . import button_view as _button_view         # noqa: E402
from . import zipper_view as _zipper_view         # noqa: E402
from . import button_ui as _button_ui             # noqa: E402
from . import guide_edit as _guide_edit           # noqa: E402
from . import piece_move as _piece_move           # noqa: E402
from . import garment_mark as _garment_mark       # noqa: E402
from . import work_screen as _work_screen         # noqa: E402
from . import live_resume as _live_resume            # noqa: E402
from .solver import lifecycle_trace as _lifecycle_trace
from .solver import stop_control as _stop_control
from . import dress as _dress                       # noqa: E402
from . import place_fold as _place_fold             # noqa: E402
from .core import place_targets as _target_scope
from . import place_targets as _place_targets
from . import place_seam as _place_seam             # noqa: E402
from . import place_lay as _place_lay               # noqa: E402
from . import place_settle as _place_settle         # noqa: E402
from . import place_check as _place_check           # noqa: E402
from . import export_retopo as _export_retopo       # noqa: E402
from .core import mesh_to_spec as _mesh_to_spec     # noqa: E402
from . import mesh_import as _mesh_import           # noqa: E402
from .core import measure as _measure               # noqa: E402
from . import measure_tool as _measure_tool         # noqa: E402
from . import measure_edit as _measure_edit         # noqa: E402
from . import properties as _properties             # noqa: E402
from . import operators as _operators               # noqa: E402
from . import ui as _ui                             # noqa: E402
from . import tool_tab as _tool_tab                 # noqa: E402

_STAMPS = {}


def _file_stamps():
    out = {}
    for name in _loaded_submodules():
        try:
            out[name] = os.path.getmtime(sys.modules[name].__file__)
        except (OSError, TypeError, AttributeError):
            out[name] = None
    return out


def _reload_changed_submodules():
    """Enabling from Preferences does not re-run this file when only a submodule changed."""
    global _STAMPS
    now = _file_stamps()
    if _STAMPS and now != _STAMPS:
        _reload_all_submodules()
        now = _file_stamps()
    _STAMPS = now


def register():
    _reload_changed_submodules()
    # our own translation context on every class before it is registered (a plain word can be another add-on's)
    _i18n.own_context([sys.modules[name] for name in _loaded_submodules()])
    # memory equals the last undo step whenever an operator ends (undo_guard): wrapped before registration
    _undo_guard.install([sys.modules[name] for name in _loaded_submodules()])
    _latency_trace.install([sys.modules[name] for name in _loaded_submodules()])
    _undo_guard.register()
    _i18n.register()
    _look_reference.register()
    _properties.register()
    _distribution.register()
    _operators.register()
    _seam_tool.register()
    _draft_tool.register()
    _shape_tool.register()
    _seam_edit_tool.register()
    _placement.register()
    _guide_edit.register()
    _piece_move.register()
    _work_screen.register()
    _dress.register()
    _garment_view.register()
    _place_fold.register()
    _place_seam.register()
    _place_lay.register()
    _place_settle.register()
    _place_check.register()
    _export_retopo.register()
    _mesh_import.register()
    _ui.register()
    _tool_tab.register()
    _walk_ui.register()
    _dart_ui.register()
    _spread_ui.register()
    _blend_ui.register()
    _button_ui.register()
    _measure_tool.register()
    _measure_edit.register()
    _toolbar.register()               # after the operators its tools' keymaps call (a keymap's properties need them)
    _silhouette.register()            # background references, below the piece marks and seams
    _piece_look.register()            # before the seams' overlay: the seams are drawn on top
    _diagnose.register()              # over the piece colors, under the seams
    _overlay.register()
    _pre_click.register()
    _display_state.register()
    _piece_hide.register()
    _live_key.register()              # after dress: its keymap item reads the add-on preference
    _selection.register()
    _garment_mark.register()          # last: its dot is drawn over the pieces' tints and the seams
    _latency_trace.register()


def unregister():
    _place_targets.unregister()
    _selection.unregister()
    _live_key.unregister()
    _piece_hide.unregister()
    _display_state.unregister()
    _garment_view.unregister()
    _garment_mark.unregister()
    _pre_click.unregister()
    _silhouette.unregister()
    _overlay.unregister()
    _diagnose.unregister()
    _piece_look.unregister()
    _toolbar.unregister()
    _measure_edit.unregister()
    _measure_tool.unregister()
    _blend_ui.unregister()
    _button_ui.unregister()
    _spread_ui.unregister()
    _dart_ui.unregister()
    _walk_ui.unregister()
    _tool_tab.unregister()
    _ui.unregister()
    _mesh_import.unregister()
    _export_retopo.unregister()
    _place_check.unregister()
    _place_settle.unregister()
    _place_lay.unregister()
    _place_seam.unregister()
    _place_fold.unregister()
    _dress.unregister()
    _work_screen.unregister()
    _guide_edit.unregister()
    _piece_move.unregister()
    _placement.unregister()
    _seam_edit_tool.unregister()
    _shape_tool.unregister()
    _draft_tool.unregister()
    _seam_tool.unregister()
    _operators.unregister()
    _properties.unregister()
    _distribution.unregister()
    _operators.release_enum_strings()
    _i18n.unregister()
    _look_reference.unregister()
    _undo_guard.unregister()
    _latency_trace.unregister()
