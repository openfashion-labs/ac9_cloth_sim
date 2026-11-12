# SPDX-License-Identifier: GPL-3.0-or-later
"""The spec lives in a Text datablock; this reads it (cached) and writes it back.

The Text is the source of truth. Everything else the add-on shows (the pattern objects, the
seam list, the overlay) is rebuilt from it, so editing the JSON by hand or from an external
tool is as good as editing it through the panel.
"""
import time
from dataclasses import dataclass
from .i18n import tx

import bpy
from bpy.app.handlers import persistent

from .core import spec as S

TEXT_NAME = "AC9_ClothSim_Spec"

_cache = {'key': None, 'spec': None, 'outlines': None, 'error': None, 'quick': None, 'checked': -1.0}
# get() is called by every panel and every button's poll on each sidebar redraw (~30 times). Reading and hashing the
# whole Text each time cost ~5 ms for a 3.6 M character spec (a BOM jacket), ~150 ms per redraw (2026-10-05).
# The full check now runs at most every RECHECK_S; put(), undo / redo and loading a file force it at once, so only a
# hand edit in the Text editor shows up to RECHECK_S late.
RECHECK_S = 0.5


def text(context, create=False):
    props = context.scene.ac9_cloth_sim
    t = props.spec_text
    if t is None and create:
        t = bpy.data.texts.new(TEXT_NAME)
        props.spec_text = t
    return t


def new_text(context, garment_name):
    """A new Text for another garment's spec, made the current one (a scene can hold several garments)."""
    t = bpy.data.texts.new(f"{TEXT_NAME}_{garment_name}")
    context.scene.ac9_cloth_sim.spec_text = t
    return t


def _key(t):
    return (t.name, t.as_pointer(), hash(t.as_string()))


def get(context):
    """(spec, outlines by piece name) or (None, None). Parsing is cached on the text content."""
    t = text(context)
    if t is None:
        return None, None
    quick, now = (t.name, t.as_pointer()), time.monotonic()
    if _cache['quick'] == quick and now - _cache['checked'] < RECHECK_S and _cache['key'] is not None:
        return _cache['spec'], _cache['outlines']
    _cache['quick'], _cache['checked'] = quick, now
    k = _key(t)
    if _cache['key'] != k:
        _cache['key'] = k
        try:
            sp = S.loads(t.as_string())
            from . import look_reference as LR
            LR.bind_owner(t, sp)
            _cache['spec'] = sp
            _cache['outlines'] = {pc['name']: S.Outline(pc) for pc in sp['pieces']}
            _cache['error'] = None
        except Exception as exc:          # a hand edit broke the JSON: show it, do not raise in draw()
            _cache['spec'], _cache['outlines'], _cache['error'] = None, None, str(exc)
    return _cache['spec'], _cache['outlines']


def error():
    return _cache['error']


GEN = [0]          # bumped on every invalidate: the Pieces panel's per-garment summaries are kept until it changes
_epoch = globals().get('_epoch', -1) + 1


class EditChanged(ValueError):
    """The document an edit started from is no longer current."""


@dataclass(frozen=True)
class EditOwner:
    scene: int
    text: int
    epoch: int
    body: str

    def matches(self, context, content=False):
        try:
            t = text(context)
            return (context.scene.session_uid == self.scene and t is not None
                    and t.session_uid == self.text and self.epoch == _epoch
                    and (not content or t.as_string() == self.body))
        except (ReferenceError, AttributeError):
            return False

    def require(self, context, content=True):
        if not self.matches(context, content):
            raise EditChanged("The pattern changed during this edit; start the edit again")


def edit_owner(context):
    t = text(context)
    if t is None:
        raise EditChanged("No pattern to edit")
    return EditOwner(context.scene.session_uid, t.session_uid, _epoch, t.as_string())


def begin_edit(context):
    """Read an independent snapshot and its exact saved owner once, outside draw/poll."""
    owner = edit_owner(context)
    sp = S.loads(owner.body)
    return sp, {pc['name']: S.Outline(pc) for pc in sp['pieces']}, owner


def _discard_cache():
    _cache.update(key=None, spec=None, outlines=None, error=None, quick=None, checked=-1.0)


def invalidate():
    """The next get() reads the Text again."""
    _cache['checked'] = -1.0
    GEN[0] += 1


@persistent
def _on_undo_or_load(*_args):
    global _epoch
    _epoch += 1
    _discard_cache()
    invalidate()


_HANDLERS = ('undo_post', 'redo_post', 'load_post')


def register_handlers():
    for h in _HANDLERS:
        lst = getattr(bpy.app.handlers, h)
        for f in [f for f in lst if getattr(f, '__name__', '') == '_on_undo_or_load']:
            lst.remove(f)
        # Observers rebuilding their session must see the new generation.
        lst.insert(0, _on_undo_or_load)


def unregister_handlers():
    global _epoch
    _epoch += 1
    for h in _HANDLERS:
        lst = getattr(bpy.app.handlers, h)
        for f in [f for f in lst if getattr(f, '__name__', '') == '_on_undo_or_load']:
            lst.remove(f)


def write(context, spec, keep=False, owner=None):
    """Write the spec to its Text. keep: the caller hands the spec over (it does not change it after), so get()
    returns it instead of parsing the text again (a pants' spec is 3.6 M characters: 40 ms to parse and 35 ms of
    outlines; the Tool tab's Fold Angle writes on every slider step, experiments/prepare_cache_2026-10-24 t4); its
    outlines are kept when its pieces are the very list of the spec before. UI refresh is separate."""
    old_spec, old_out = _cache['spec'], _cache['outlines']
    try:
        body = S.dumps(spec)
        outlines = (old_out if old_spec is not None and old_out is not None
                    and old_spec.get('pieces') is spec.get('pieces')
                    else {pc['name']: S.Outline(pc) for pc in spec['pieces']}) if keep else None
        if owner is not None:
            owner.require(context)
    except Exception:
        _discard_cache()
        raise
    t = text(context, create=True)
    previous = t.as_string()
    try:
        t.from_string(body)
    except Exception:
        try:
            t.from_string(previous)
        finally:
            _discard_cache()
        raise
    invalidate()
    if keep:
        _cache['quick'], _cache['checked'] = (t.name, t.as_pointer()), time.monotonic()
        _cache['key'] = _key(t)
        _cache['spec'] = spec
        _cache['outlines'] = outlines
        _cache['error'] = None
    from . import look_reference as LR
    LR.bind_owner(t, spec)
    return EditOwner(context.scene.session_uid, t.session_uid, _epoch, body)


def refresh(context, spec, seams=True):
    """Rebuild disposable UI after persistence; failures do not undo saved edits."""
    if seams:
        sync_seam_list(context, spec)
    from . import toolbar
    toolbar.auto_on(context)            # a first garment in the file: the pattern tools on (user 2026-10-22)
    from . import display_state
    display_state.apply(context, spec, allow_split=True)


def put(context, spec, keep=False, seams=True, owner=None):
    """Compatibility entry point: write first, then refresh; refresh errors still propagate."""
    write(context, spec, keep=keep, owner=owner)
    refresh(context, spec, seams=seams)


def commit(context, spec, keep=False, seams=True, owner=None):
    """An operation boundary: persistence errors raise; derived-update errors become a report."""
    saved_owner = write(context, spec, keep=keep, owner=owner)
    try:
        refresh(context, spec, seams=seams)
    except Exception as exc:
        note = tx("Pattern saved; display not updated: {0}", exc)
        context.scene.ac9_cloth_sim.dress_status = note
        return saved_owner, note
    return saved_owner, ''


def sync_seam_list(context, spec=None):
    """Mirror the spec's seams into the UIList collection (id, label, lengths, flag)."""
    if spec is None:
        spec, _ = get(context)
    props = context.scene.ac9_cloth_sim
    coll = props.seams
    coll.clear()
    if spec is None:
        return
    from .core import seam_edit as SE
    outlines = S.Outlines({pc['name']: pc for pc in spec['pieces']})
    lab ={pc['name']: pc.get('label') or pc['name'] for pc in spec['pieces']}
    from .core import dart as DT
    darts = set()                                   # a dart reads as one in the list (user 2026-10-20: not found)
    for nm in {sm['a']['piece'] for sm in spec['seams'] if sm['a']['piece'] == sm['b']['piece']}:
        try:
            darts.update(d['seam'] for d in DT.find_darts(spec, nm))
        except Exception:
            pass
    for sm in spec['seams']:
        it = coll.add()
        it.seam_id = sm['id']
        if sm['id'] in darts:
            it.label = tx("Dart on {0}", lab.get(sm['a']['piece'], sm['a']['piece'])) + ("" if sm.get('enabled', True) else tx(" (off)"))
        else:
            it.label = f"{lab.get(sm['a']['piece'], sm['a']['piece'])}  -  {lab.get(sm['b']['piece'], sm['b']['piece'])}"
        g = SE.group_of(sm) or ''
        it.group = g
        st = SE.stretches(spec, sm) if g else [sm['id']]
        it.in_group = len(st) > 1 and st[0] != sm['id']      # a later stretch of an M:N seam
        it.color_ref = (0.5, 0.5, 0.5, 1.0) if not sm.get('enabled', True) else SE.color(sm)
        it.color = it.color_ref
        try:
            it.len_a = S.side_length(spec, sm['a'], outlines)
            it.len_b = S.side_length(spec, sm['b'], outlines)
        except Exception:
            it.len_a = it.len_b = 0.0
        it.check = sm.get('check', '')
        it.enabled = sm.get('enabled', True)
        it.source = sm.get('source', '')
    if props.seam_index >= len(coll):
        props.seam_index = max(0, len(coll) - 1)


# the seams Shift+clicked besides the active one (user 2026-10-22, DECISIONS cloth-sim-edge-select-2026-10-22): On/Off
# and Delete act on all of them, everything else on the active one. A pick in the list (no Shift there) drops them
seam_more = set()
_seam_keep = [False]          # True while a Shift pick sets seam_index (the list's own update must not drop them)


def selected_seams(context, spec):
    """The active seam then the other picked ones ([] when none is active)."""
    act = active_seam(context, spec)
    if act is None:
        return []
    by = {sm['id']: sm for sm in spec['seams']}
    return [act] + [by[i] for i in sorted(seam_more) if i in by and i != act['id']]


def active_seam(context, spec):
    props = context.scene.ac9_cloth_sim
    if not spec or not (0 <= props.seam_index < len(props.seams)):
        return None
    sid = props.seams[props.seam_index].seam_id
    return next((sm for sm in spec['seams'] if sm['id'] == sid), None)


def side_tag(spec, sm, k, n=10):
    """A seam side named for a button: its piece's name (or label), cut to n characters; "Side A" / "Side B" (the
    tags drawn on the pattern) when both sides are on one piece (a dart). User 2026-10-20: "A to B" did not say
    which side is A."""
    if sm['a']['piece'] == sm['b']['piece']:
        return tx("Side {0}", k.upper())
    lab = {pc['name']: pc.get('label') or pc['name'] for pc in spec.get('pieces', [])}
    name = lab.get(sm[k]['piece'], sm[k]['piece'])
    return name if len(name) <= n else name[:n - 1] + "…"
