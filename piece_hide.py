# SPDX-License-Identifier: GPL-3.0-or-later
"""Hide a piece in the 3D view only: an eye, apart from its state.

A hidden piece is still simulated, still collides and its seams still pull; it is only not drawn in 3D (its placed
object hidden, its part of the dressed garment masked) and cannot be clicked or grabbed there. The flat pattern keeps
it, faint and marked, so it can still be picked and edited. The four states (Active / Freeze / Off) say what the
simulation does with a piece; the eye says only whether it is seen. An Off piece is out of the simulation and
has no eye (turning a piece Off opens it).

Kept on the scene, one list per owning Text (saved with the file, undone with Ctrl+Z).
Name-only files are qualified once at an operation boundary when ownership is unique.
Nothing here is read by the solver, the recipe or the spec.
"""
import json

import bpy

from .i18n import rpt, tx
from . import garment_owner as GO

KEY = 'ac9_hidden_pieces'
OWNERS = 'ac9_hidden_piece_owners'
_cache = {'raw': None, 'map': {}}


def _map(scene):
    raw = scene.get(KEY, '') if scene is not None else ''
    raw = raw if isinstance(raw, str) else ''
    if _cache['raw'] != raw:
        try:
            m = json.loads(raw) if raw else {}
            m = {str(k): frozenset(str(n) for n in v) for k, v in m.items() if isinstance(v, list)}
        except (ValueError, TypeError, AttributeError):
            m = {}
        _cache.update(raw=raw, map=m)
    return _cache['map']


def names(scene, spec, *, owner=GO.CURRENT):
    """The hidden pieces of this garment (frozenset); read on every draw, parsed only when the property changed."""
    if not spec:
        return frozenset()
    owner = GO.text(owner)
    if owner is None:
        return frozenset()
    for entry in scene.get(OWNERS, {}).values():
        if entry.get('text') == owner:
            return frozenset(json.loads(entry.get('pieces', '[]')))
    # Retain name-only files when that name identifies exactly one document.
    legacy = _map(scene).get(str(spec.get('name', '')), frozenset())
    return (legacy
            if legacy and GO.legacy_owner(spec, owner) else frozenset())


def _write(scene, spec, hidden, *, owner=GO.CURRENT):
    owner = GO.text(owner)
    if owner is None:
        return
    known = {pc['name'] for pc in spec['pieces']}
    hidden = frozenset(n for n in hidden if n in known)
    entries = {key: {'text': entry.get('text'), 'pieces': entry.get('pieces', '[]')}
               for key, entry in scene.get(OWNERS, {}).items()
               if entry.get('text') is not None and entry.get('text') != owner}
    # Empty records also suppress any legacy value after an explicit show-all.
    key = str(next(i for i in range(len(entries) + 1) if str(i) not in entries))
    entries[key] = {'text': owner, 'pieces': json.dumps(sorted(hidden))}
    scene[OWNERS] = entries


def bind_legacy(scene, spec, *, owner=GO.CURRENT):
    """Qualify old name-only eye state outside drawing, only when unambiguous."""
    owner = GO.text(owner)
    if any(entry.get('text') == owner for entry in scene.get(OWNERS, {}).values()):
        return
    if spec['name'] in _map(scene) and GO.legacy_owner(spec, owner):
        _write(scene, spec, names(scene, spec, owner=owner), owner=owner)
        legacy = dict(_map(scene))
        legacy.pop(spec['name'], None)
        if legacy:
            scene[KEY] = json.dumps({k: sorted(v) for k, v in legacy.items()})
        else:
            scene.pop(KEY, None)


def hideable(spec):
    """Pieces that can be hidden: every piece in the simulation (not Off)."""
    from .core import spec as S
    return {pc['name'] for pc in spec['pieces'] if S.piece_state(pc) != 'off_all'}


def set_hidden(context, spec, pieces, hide=True):
    """Hide (or show) `pieces` of this garment; returns how many changed. The display follows at once."""
    old = names(context.scene, spec)
    pieces = set(pieces) & (hideable(spec) if hide else set(old))
    new = (old | pieces) if hide else (old - pieces)
    if new == old:
        return 0
    _write(context.scene, spec, new)
    refresh(context, spec)
    return len(new ^ old)


def show_all(context, spec):
    return set_hidden(context, spec, names(context.scene, spec), hide=False)


def forget(context, spec, pieces):
    """Open the eye of pieces leaving the simulation (Off) without redrawing: the caller applies the display."""
    old = names(context.scene, spec)
    if set(pieces) & old:
        _write(context.scene, spec, old - set(pieces))


def refresh(context, spec):
    from . import display_state as DS
    DS.apply(context, spec, allow_split=True)
    from . import overlay, piece_look
    overlay.invalidate()
    piece_look.invalidate()
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in ('VIEW_3D', 'PROPERTIES'):
                area.tag_redraw()


# ------------------------------------------------------------------------------------------------ operators

def _spec(context):
    from . import store
    return store.get(context)[0]


class AC9_OT_HidePieces(bpy.types.Operator):
    """Hide the selected pieces in the 3D view only. Still simulated, still collides, their seams still pull"""
    bl_idname = "ac9_cloth_sim.hide_pieces"
    bl_label = "Hide"
    bl_options = {'REGISTER', 'UNDO'}

    unselected: bpy.props.BoolProperty(name="Others", options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        if props.unselected:
            return tx("Hide every piece but the selected ones in the 3D view only (still simulated)")
        return tx(cls.__doc__)

    @classmethod
    def poll(cls, context):
        # no piece selected: Blender's own H (hide the selected objects) takes the key
        from . import selection as SEL
        return _spec(context) is not None and bool(SEL.pieces(context))

    def execute(self, context):
        from . import selection as SEL
        spec = _spec(context)
        picked = set(SEL.pieces(context))
        if spec is None or not picked:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        target = (hideable(spec) - picked) if self.unselected else picked
        n = set_hidden(context, spec, target, hide=True)
        if n:
            self.report({"INFO"}, rpt("{0} piece(s) hidden in 3D (still simulated)", n))
        return {'FINISHED'}


class AC9_OT_HidePiece(bpy.types.Operator):
    """Show or hide this piece in the 3D view only (still simulated, still collides, seams still pull)"""
    bl_idname = "ac9_cloth_sim.hide_piece"
    bl_label = "Hide Piece"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    piece: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _spec(context) is not None

    def execute(self, context):
        spec = _spec(context)
        if spec is None or not self.piece:
            return {'CANCELLED'}
        hidden = self.piece in names(context.scene, spec)
        set_hidden(context, spec, [self.piece], hide=not hidden)
        return {'FINISHED'}


class AC9_OT_ShowAllPieces(bpy.types.Operator):
    """Show every hidden piece of this garment again (Alt+H also shows the objects Blender hid)"""
    bl_idname = "ac9_cloth_sim.show_all_pieces"
    bl_label = "Show All"
    bl_options = {'REGISTER', 'UNDO'}

    blender_too: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})   # the Alt+H key: Blender's own Alt+H too

    @classmethod
    def poll(cls, context):
        return _spec(context) is not None

    def execute(self, context):
        spec = _spec(context)
        n = show_all(context, spec) if spec is not None else 0
        if self.blender_too and context.mode == 'OBJECT':
            try:
                bpy.ops.object.hide_view_clear(select=False)
            except RuntimeError:
                pass
            if spec is not None:
                # the objects AC9 hides on purpose (a garment's pieces shown as placed, an Off piece's object) are
                # hidden again: Blender's Alt+H showed every object
                from . import display_state as DS
                DS.apply(context, spec)
        if n:
            self.report({"INFO"}, rpt("{0} piece(s) shown again", n))
        return {'FINISHED'}


CLASSES = (AC9_OT_HidePieces, AC9_OT_HidePiece, AC9_OT_ShowAllPieces)
_keymaps = []


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:          # background: no key configuration
        return
    km = kc.keymaps.new(name='Object Mode', space_type='EMPTY')
    # the add-on's items come before Blender's own: with no piece selected the operator's poll fails and Blender's H
    # takes the key (an add-on keymap item whose poll fails passes the event on)
    for kw, props in (({'type': 'H', 'value': 'PRESS'}, {}),
                      ({'type': 'H', 'value': 'PRESS', 'shift': True}, {'unselected': True})):
        kmi = km.keymap_items.new(AC9_OT_HidePieces.bl_idname, **kw)
        for k, v in props.items():
            setattr(kmi.properties, k, v)
        _keymaps.append((km, kmi))
    kmi = km.keymap_items.new(AC9_OT_ShowAllPieces.bl_idname, type='H', value='PRESS', alt=True)
    kmi.properties.blender_too = True
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
