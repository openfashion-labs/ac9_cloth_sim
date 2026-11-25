# SPDX-License-Identifier: GPL-3.0-or-later
"""One rule at one boundary: when an AC9 operator hands control back to Blender, memory equals the last undo step.

Blender's undo trusts that. Stepping back, it keeps every datablock that is the same in the two steps as it is in
memory and frees the datablocks the steps do not know. A change made in memory with no step is therefore not undone;
and when the change put a new datablock under an old one (a mesh under an object, an object in a collection, a
parent, a constraint's target), the old one is left pointing at freed memory: a crash at the next read.

Blender writes the step itself only when an operator with UNDO ends FINISHED and was not called from Python. Every
other ending that changed something (CANCELLED after a rollback that installed copies, an exception half way, FINISHED
without UNDO) gets its step here. Nothing is written when nothing changed, so a refusal that touched nothing leaves
the history as it was.

install(modules) wraps execute / invoke / modal of every operator class before it is registered (as
i18n.own_context does for the translation context). push() is the add-on's own undo_push: a step written in the
middle of an operator (a run's "before" and "result") is then what the operator's end is compared with.

Not covered: an operator with UNDO that a timer or a script calls without asking for a step (Blender writes none
and this file cannot tell), and writes made outside operators (handlers, timers)."""
import bpy
from bpy.app.handlers import persistent

STEP = "AC9 Cloth Sim: canceled"
pushed = [0]         # for tests: how many steps the guard wrote
_stack = []          # the operators running now, outermost first: (sequence number at the start, fingerprint)
_modal = {}          # operator pointer -> the start of the outermost operator when its invoke went modal
_seq = [0]
_mark = [0, None]    # the last moment memory was known to equal a step: its sequence number, its fingerprint
_texts = {'key': None, 'value': ()}
_later = [False]     # the operator ending now is undone from a timer (undone_later): it gets no step


def _text_part(data):
    """The add-on's Texts by content. A pattern is millions of characters (reading and hashing one: milliseconds),
    so the hashes are kept until the store says a Text was written or an undo / a load replaced it (store.GEN)."""
    from . import store
    texts = [t for t in data.texts if t.name.startswith('AC9_')]
    key = (store.GEN[0], tuple(t.session_uid for t in texts))
    if key != _texts['key']:
        _texts['key'], _texts['value'] = key, tuple(hash(t.as_string()) for t in texts)
    return key[1], _texts['value']


def fingerprint():
    """Cheap identity of what an undo step holds: which datablocks exist, what each object points at, the add-on's
    Texts. Not vertex coordinates: an in-place edit that is not undone is wrong but does not crash."""
    data = bpy.data
    return (tuple((o.session_uid, o.data.session_uid if o.data is not None else 0,
                   o.parent.session_uid if o.parent else 0,
                   len(o.modifiers), len(o.constraints), len(o.vertex_groups)) for o in data.objects),
            tuple(m.session_uid for m in data.meshes),
            tuple((c.session_uid, len(c.objects), len(c.children)) for c in data.collections),
            tuple(g.session_uid for g in data.node_groups),
            _text_part(data))


def _note(now=None):
    _seq[0] += 1
    _mark[0], _mark[1] = _seq[0], fingerprint() if now is None else now


def push(message):
    """bpy.ops.ed.undo_push for the add-on's own steps: memory equals this step from here."""
    bpy.ops.ed.undo_push(message=message)
    try:
        _note()
    except ReferenceError:
        pass


def undone_later():
    """The operator running now ends with its changes in memory and a timer takes them back with an undo (a run
    that wrote no frame). A step of the guard's at its end would be the one that undo returns to."""
    _later[0] = True


def _begin():
    try:
        _stack.append((_seq[0], fingerprint()) if not _stack else _stack[0])
    except ReferenceError:              # a datablock freed under us: no claim about this operator
        _stack.append(None)
    return _stack[0]


def _seal(start, result, cls):
    """The operator that began at `start` ended with `result`: write the step Blender will not write."""
    if start is None or (result is not None and ('RUNNING_MODAL' in result or 'PASS_THROUGH' in result)):
        return
    if _later[0]:
        _later[0] = False
        return
    try:
        now = fingerprint()
        if result is not None and 'FINISHED' in result and 'UNDO' in getattr(cls, 'bl_options', ()):
            _note(now)                  # Blender writes this one
            return
        if now == (_mark[1] if _mark[0] > start[0] else start[1]):
            return
        done = result is not None and 'FINISHED' in result      # one without UNDO that made or swapped datablocks
        bpy.ops.ed.undo_push(message=getattr(cls, 'bl_label', STEP) if done else STEP)
        pushed[0] += 1
        _note(now)
    except (RuntimeError, ReferenceError):
        pass


def _wrap_execute(fn, cls):
    def execute(self, context):
        start, result = _begin(), None
        try:
            _pattern_preflight(context, cls)
            result = fn(self, context)
            return result
        except _pattern_error() as exc:
            self.report({'WARNING'}, str(exc))
            result = {'CANCELLED'}
            return result
        finally:
            _stack.pop()
            if not _stack:
                _seal(start, result, cls)
    return execute


def _wrap_invoke(fn, cls):
    def invoke(self, context, event):
        start, result = _begin(), None
        try:
            _pattern_preflight(context, cls)
            result = fn(self, context, event)
            if result is not None and 'RUNNING_MODAL' in result:
                _modal[self.as_pointer()] = start       # also one started by another operator's invoke
            return result
        except _pattern_error() as exc:
            self.report({'WARNING'}, str(exc))
            result = {'CANCELLED'}
            return result
        finally:
            _stack.pop()
            if not _stack:
                _seal(start, result, cls)
    return invoke


def _pattern_error():
    from .pattern_view import PatternIntegrityError
    return PatternIntegrityError


def _pattern_preflight(context, cls):
    """Before the first side effect, not merely before the eventual Text write.

    Nested operators may run while their parent is publishing new pattern objects. The outer operation has
    already checked its stable input. Read-only operators remain available to inspect a mismatch.
    """
    if len(_stack) > 1 or 'UNDO' not in getattr(cls, 'bl_options', ()):
        return
    # These change selection/focus only. UNDO does not imply that an operator edits the pattern document.
    inspection = {'select_garment', 'invert_piece_selection', 'select_piece', 'button_select',
                  'measure_select', 'fabric_select', 'interlining_select', 'seam_list_pick',
                  'pick_seam', 'pick_piece_under', 'select_all_pieces', 'collider_select'}
    name = getattr(cls, 'bl_idname', '').removeprefix('ac9_cloth_sim.')
    if name in inspection:
        return
    if name == 'restore_pieces':
        return  # This operation has its own non-destructive restore_plan before staging any replacement.
    from . import pattern_view as PV
    PV.require_integrity(context, allow_missing=name == 'restore_pieces', allow_rebuild=name == 'refresh')


def _wrap_modal(fn, cls):
    def modal(self, context, event):
        result = None
        try:
            result = fn(self, context, event)
            return result
        except _pattern_error() as exc:
            cancel = getattr(cls, 'cancel', None)
            if cancel is not None:
                cancel(self, context)
            self.report({'WARNING'}, str(exc))
            result = {'CANCELLED'}
            return result
        finally:
            if result is None or 'CANCELLED' in result or 'FINISHED' in result:
                _seal(_modal.pop(self.as_pointer(), None), result, cls)
    return modal


def install(modules):
    for mod in modules:
        for cls in list(vars(mod).values()):
            if not (isinstance(cls, type) and cls.__module__ == mod.__name__ and issubclass(cls, bpy.types.Operator)):
                continue
            for name, wrap in (('execute', _wrap_execute), ('invoke', _wrap_invoke), ('modal', _wrap_modal)):
                fn = getattr(cls, name, None)           # also one inherited from a mix-in
                if fn is None or getattr(fn, '_ac9_guard', False):
                    continue
                new = wrap(fn, cls)
                new._ac9_guard = True
                new.__wrapped__ = fn            # the wrapped method stays reachable (tests call it without Blender's op)
                setattr(cls, name, new)


# ---- no undo while a run is on: a run replaces the garment's mesh between its two steps, and stepping back in
# between frees what the running operator holds. The run's modal asks is_undo_event() and takes the key.

_UNDO_OPS = ('ed.undo', 'ed.redo', 'ed.undo_history', 'ed.undo_redo')
_NOT_KEYS = frozenset(('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER', 'TIMER_REPORT', 'LEFTMOUSE', 'RIGHTMOUSE',
                       'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'ESC', 'WINDOW_DEACTIVATE', 'NONE'))


def is_undo_event(context, event):
    """Is this key press bound to undo, redo or the undo history? The person's keymap decides; Ctrl+Z,
    Ctrl+Shift+Z and their Cmd forms when no keymap is loaded."""
    if event.value != 'PRESS' or event.type in _NOT_KEYS:
        return False
    found = False
    try:
        configs = context.window_manager.keyconfigs
        maps = [kc.keymaps.get('Screen') for kc in (configs.user, configs.active) if kc is not None]
    except (AttributeError, ReferenceError):
        maps = []
    for km in maps:
        if km is None:
            continue
        for item in km.keymap_items:
            if item.idname not in _UNDO_OPS or not item.active:
                continue
            found = True
            if item.type == event.type and (item.any or (
                    bool(item.ctrl) == bool(event.ctrl) and bool(item.shift) == bool(event.shift)
                    and bool(item.alt) == bool(event.alt) and bool(item.oskey) == bool(event.oskey))):
                return True
    if found:
        return False
    return event.type == 'Z' and bool(event.ctrl or event.oskey) and not event.alt


@persistent
def _stepped(*_args):
    """After an undo, a redo or a load memory is a step again."""
    _later[0] = False
    try:
        _note()
    except ReferenceError:
        pass


_HANDLERS = ('undo_post', 'redo_post', 'load_post')


def register():
    unregister()
    for name in _HANDLERS:
        getattr(bpy.app.handlers, name).append(_stepped)


def unregister():
    for name in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        for f in [f for f in lst if getattr(f, '__name__', '') == '_stepped']:
            lst.remove(f)
