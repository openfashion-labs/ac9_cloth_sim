# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration (Blender only). Split from __init__ so core/ imports without bpy."""
import importlib
import os

# F3 (Reload Scripts) re-runs this file: reload the submodules in dependency order first
if "_spec" in locals():
    if '_fast_contract' in locals():
        importlib.reload(_fast_contract)
        importlib.reload(_fast_live)
    if '_preload_status' in locals():
        importlib.reload(_preload_status)
    if "_fold_reference" in locals():
        importlib.reload(_fold_reference)
    if "_live_resume" in locals():
        importlib.reload(_live_resume)
    if "_outside" in locals():
        importlib.reload(_outside)
    if "_display_state" in locals():
        importlib.reload(_display_state)
    if "_view_visibility" in locals():
        importlib.reload(_view_visibility)
    if "_view_setup" in locals():
        importlib.reload(_view_setup)
    for _m in (*([_i18n_ja, _i18n] if '_i18n' in locals() else []), *([_core_i18n] if '_core_i18n' in locals() else []), _spec, *([_piece_table] if '_piece_table' in locals() else []), _bom_parse, _bom_to_spec, _body_axes, _pattern_build, _roles, *([_fabric, _look] if '_look' in locals() else []), _recipe, _presets, *([_phase_from] if '_phase_from' in locals() else []), _simlog,  # noqa: F821
               _store, *([_look_apply] if '_look_apply' in locals() else []), _pattern_view, _placed_view, *([_selection] if '_selection' in locals() else []), _overlay, _piece_look, *([_diagnose] if '_diagnose' in locals() else []), _click_log, _toolbar, *([_pre_click] if '_pre_click' in locals() else []), _seam_tool, _seam_edit, _seam_edit_tool, _draft, _snap, _transfer, _dxf_out, _walk, _notch_import, _dart, _spread, *([_cut] if '_cut' in locals() else []), _blend, _draft_tool, _shape_tool, _walk_ui, _dart_ui, _spread_ui, _blend_ui, _placement, _guide_edit, _piece_move, _garment_mark, _work_screen, _attach, _buttons, _zippers, _button_shape, _button_view, _zipper_view, _button_ui, *([_lifecycle_trace, _stop_control] if '_stop_control' in locals() else []), _dress, *([_place_fold] if '_place_fold' in locals() else []), *([_place_seam] if '_place_seam' in locals() else []), *([_place_lay] if '_place_lay' in locals() else []), *([_place_settle] if '_place_settle' in locals() else []), _export_retopo,  # noqa: F821
               _mesh_to_spec, _mesh_import, _measure, _measure_tool, *([_measure_edit] if '_measure_edit' in locals() else []), _properties, _operators, _ui, _tool_tab):                                                     # noqa: F821
        importlib.reload(_m)

from .core import spec as _spec                     # noqa: E402
from .core import piece_table as _piece_table       # noqa: E402
from .core import phase_from as _phase_from         # noqa: E402
from .solver import fast_live_contract as _fast_contract
from .core import fast_live as _fast_live
from .solver import preload_status as _preload_status # noqa: E402
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
from . import store as _store                       # noqa: E402
from . import look_apply as _look_apply             # noqa: E402
from . import pattern_view as _pattern_view         # noqa: E402
from . import placed_view as _placed_view           # noqa: E402
from . import selection as _selection               # noqa: E402
from . import display_state as _display_state       # noqa: E402
from . import view_visibility as _view_visibility   # noqa: E402
from . import view_setup as _view_setup             # noqa: E402
from . import overlay as _overlay                   # noqa: E402
from . import piece_look as _piece_look             # noqa: E402
from . import diagnose as _diagnose                 # noqa: E402
from . import click_log as _click_log               # noqa: E402
from . import toolbar as _toolbar                   # noqa: E402
from . import seam_tool as _seam_tool               # noqa: E402
from .core import seam_edit as _seam_edit           # noqa: E402
from . import seam_edit_tool as _seam_edit_tool     # noqa: E402
from .core import draft as _draft                   # noqa: E402
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
from . import place_seam as _place_seam             # noqa: E402
from . import place_lay as _place_lay               # noqa: E402
from . import place_settle as _place_settle         # noqa: E402
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

_CHAIN = (_core_i18n, _spec, _piece_table, _bom_parse, _bom_to_spec, _body_axes, _pattern_build, _roles, _fabric, _look, _recipe, _presets, _phase_from, _simlog, _attach, _store, _look_apply, _pattern_view, _placed_view, _selection, _view_visibility, _view_setup, _overlay, _piece_look, _toolbar, _pre_click,
          _seam_tool, _seam_edit, _seam_edit_tool, _draft, _snap, _transfer, _dxf_out, _walk, _notch_import, _dart, _spread, _cut, _blend, _draft_tool, _shape_tool, _walk_ui, _dart_ui, _spread_ui, _blend_ui, _placement, _guide_edit, _piece_move, _garment_mark, _work_screen, _buttons, _zippers, _button_shape, _button_view, _zipper_view, _button_ui, *([_lifecycle_trace, _stop_control] if '_stop_control' in locals() else []), _dress, _place_fold, _place_seam, _place_lay, _place_settle, _export_retopo, _mesh_to_spec,
          _mesh_import, _measure,
          _measure_tool, _measure_edit, _properties,
          _operators,
          _ui, _tool_tab, _display_state)
_STAMPS = {}
_CHAIN = (_preload_status, *_CHAIN)


def _file_stamps():
    out = {}
    for mod in (*_CHAIN, _outside):
        try:
            out[mod.__name__] = os.path.getmtime(mod.__file__)
        except OSError:
            out[mod.__name__] = None
    return out


def _reload_changed_submodules():
    """Enabling from Preferences does not re-run this file when only a submodule changed."""
    global _STAMPS
    now = _file_stamps()
    if _STAMPS and now != _STAMPS:
        for mod in (*_CHAIN, _outside):
            importlib.reload(mod)
        now = _file_stamps()
    _STAMPS = now


def register():
    _reload_changed_submodules()
    _i18n.register()
    _properties.register()
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
    _place_fold.register()
    _place_seam.register()
    _place_lay.register()
    _place_settle.register()
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
    _piece_look.register()            # before the seams' overlay: the seams are drawn on top
    _diagnose.register()              # over the piece colors, under the seams
    _overlay.register()
    _pre_click.register()
    _display_state.register()
    _selection.register()
    _garment_mark.register()          # last: its dot is drawn over the pieces' tints and the seams


def unregister():
    _selection.unregister()
    _display_state.unregister()
    _garment_mark.unregister()
    _pre_click.unregister()
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
    _i18n.unregister()
