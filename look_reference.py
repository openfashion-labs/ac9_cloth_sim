"""Read material identities; persist observed legacy-reference repair only at an explicit save boundary."""
import json

import bpy
from bpy.app.handlers import persistent

UID_PROP = 'ac9_look_uid'
# Only plain identity values survive history/reload. RNA objects are re-acquired and checked before use.
_bindings = globals().get('_bindings', {})
_owners = globals().get('_owners', {})
_observations = globals().get('_observations', {})
_last_repair = {}


def _key(ref):
    return json.dumps([ref.get('uid'), ref.get('material')], ensure_ascii=False, sort_keys=True)


def _uid(mat):
    return str(mat.get(UID_PROP) or '')


def _slots(spec):
    if isinstance(spec.get('default_look'), dict):
        yield ('default', ''), spec['default_look']
    for piece in spec.get('pieces', []):
        if isinstance(piece.get('look'), dict):
            yield ('piece', piece['name']), piece['look']


def bind_owner(text, spec, reader='store'):
    """Associate parsed dictionaries with their actual saved slot, without parsing or writing during a query."""
    owner = int(text.session_uid)
    alive = {int(t.session_uid) for t in bpy.data.texts}
    for identity, (ref, sources) in list(_owners.items()):
        # Each reader owns its current parse. Another reader of the same Text must not retire that parse.
        # Two-element sources are observations from the initial development version before reader ownership.
        remaining = [source for source in sources if source[0] in alive and
                     not (source[0] == owner and (source[2] if len(source) > 2 else 'store') == reader)]
        if remaining:
            _owners[identity] = ref, remaining
        else:
            del _owners[identity]
    for slot, ref in _slots(spec):
        identity = id(ref)
        previous = _owners.get(identity)
        sources = previous[1] if previous and previous[0] is ref else []
        _owners[identity] = ref, list(dict.fromkeys([*sources, (owner, slot, reader)]))


def _sources(ref):
    owned = _owners.get(id(ref))
    return list(dict.fromkeys((source[0], source[1]) for source in owned[1])) if owned and owned[0] is ref else []


def _identity_material(packets):
    candidates = [mat for mat in bpy.data.materials if (int(mat.session_uid), _uid(mat)) in packets]
    return candidates[0] if len(candidates) == 1 else None


def resolve(ref, source=None):
    """Existing UID/name precedence first, verified historical identity only if neither resolves."""
    if not ref:
        return None
    uid = ref.get('uid')
    hits = [mat for mat in bpy.data.materials if mat.get(UID_PROP) == uid] if uid else []
    if len(hits) == 1:
        return hits[0]
    named = bpy.data.materials.get(ref.get('material') or '')
    if named is not None:
        return named
    key = _key(ref)
    sources = [source] if source is not None else _sources(ref)
    if sources:
        packets = set().union(*(_observations.get((owner, slot, key), set()) for owner, slot in sources))
    else:
        packets = _bindings.get(key, set())
    return _identity_material(packets)


def material(ref):
    """Pure with respect to Blender data and files; observations own only temporary identity values."""
    mat = resolve(ref)
    if mat is not None:
        key, packet = _key(ref), (int(mat.session_uid), _uid(mat))
        _bindings.setdefault(key, set()).add(packet)
        for owner, slot in _sources(ref):
            _observations.setdefault((owner, slot, key), set()).add(packet)
    return mat


def _loaded_records():
    from . import store
    from .core import spec as S
    records = []
    for text in bpy.data.texts:
        if not text.name.startswith(store.TEXT_NAME):
            continue
        body = text.as_string()
        try:
            spec = S.loads(body)
            slots = list(_slots(spec))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        # Duplicate piece IDs are not a unique saved owner.
        unique = len({slot for slot, _ in slots}) == len(slots)
        if unique:
            records.append((text, int(text.session_uid), body, spec, slots))
    return records


def _replace_text(text, body):
    text.from_string(body)


def repair_observed():
    """Prepare/CAS metadata repair. Unqueried owners and ambiguous UID claims are not migrated."""
    from . import store
    from .core import spec as S
    global _last_repair
    records = _loaded_records()
    claims, pending = {}, []
    for text, owner, body, spec, slots in records:
        for slot, ref in slots:
            source, key = (owner, slot), _key(ref)
            mat = resolve(ref, source)
            if mat is None:
                continue
            sid = int(mat.session_uid)
            if ref.get('uid'):
                claims.setdefault(sid, set()).add(str(ref['uid']))
            packet = sid, _uid(mat)
            if packet in _observations.get((owner, slot, key), set()) and text.is_editable:
                pending.append((owner, ref, mat, packet))
    assignments, canonical, conflicts = [], {}, set()
    for owner, ref, mat, packet in pending:
        sid, actual = packet
        requested = claims.get(sid, set())
        uid_hits = sum(_uid(other) == actual for other in bpy.data.materials) if actual else 0
        if len(requested) > 1 or (actual and uid_hits != 1):
            conflicts.add(sid)
        elif actual:
            canonical[sid] = actual
        elif len(requested) == 1 and mat.is_editable:
            desired = next(iter(requested))
            # An existing, different material with this UID takes precedence; never replace that authority.
            if not any(_uid(other) == desired for other in bpy.data.materials):
                canonical[sid] = desired
                if not any(item[1] == sid for item in assignments):
                    assignments.append((mat, sid, UID_PROP in mat, mat.get(UID_PROP), desired))
    changed_owners, refs_changed = set(), 0
    for owner, ref, mat, packet in pending:
        changed = False
        if ref.get('material') != mat.name:
            ref['material'] = mat.name
            changed = True
        # Repair a stale nonempty reference only when the same resolved material has an unambiguous canonical ID.
        uid = canonical.get(packet[0])
        if uid and ref.get('uid') and str(ref['uid']) != uid:
            ref['uid'] = uid
            changed = True
        if changed:
            changed_owners.add(owner)
            refs_changed += 1
    plans = [(text, owner, body, S.dumps(spec)) for text, owner, body, spec, slots in records if owner in changed_owners]
    _last_repair = dict(texts_changed=0, refs_changed=0, material_uids_assigned=0,
                        conflicting_materials=len(conflicts), owner_changed=False, error=None)
    # All serialization and conflict checks finish before any saved datablock is changed.
    if any(text.session_uid != owner or text.as_string() != body for text, owner, body, spec, slots in records):
        _last_repair['owner_changed'] = True
        return dict(_last_repair)
    applied_texts, applied_materials = [], []
    try:
        for mat, sid, had, previous, desired in assignments:
            if mat.session_uid != sid or mat.get(UID_PROP) != previous:
                raise RuntimeError('The material changed during legacy repair')
            mat[UID_PROP] = desired
            applied_materials.append((mat, sid, had, previous, desired))
        for text, owner, body, new_body in plans:
            if text.session_uid != owner or text.as_string() != body:
                raise RuntimeError('The pattern changed during legacy repair')
            try:
                _replace_text(text, new_body)
            except Exception:
                # As with store.write, restore this owner's body if the low-level replacement failed partway.
                if text.session_uid == owner:
                    text.from_string(body)
                raise
            applied_texts.append((text, owner, body, new_body))
    except Exception as exc:
        for text, owner, body, new_body in reversed(applied_texts):
            if text.session_uid == owner and text.as_string() == new_body:
                text.from_string(body)
        for mat, sid, had, previous, desired in reversed(applied_materials):
            if mat.session_uid == sid and mat.get(UID_PROP) == desired:
                if had:mat[UID_PROP] = previous
                else:mat.pop(UID_PROP, None)
        store.invalidate()
        _last_repair['error'] = str(exc)
        return dict(_last_repair)
    if plans:
        store.invalidate()
    _last_repair.update(texts_changed=len(plans), refs_changed=refs_changed,
                        material_uids_assigned=len(applied_materials))
    return dict(_last_repair)


@persistent
def _save_pre(*_):
    global _last_repair
    try:
        repair_observed()
    except Exception as exc:
        _last_repair = dict(error=str(exc))


@persistent
def _load_post(*_):
    _bindings.clear()
    _owners.clear()
    _observations.clear()


def register():
    for name, callback in (('save_pre', _save_pre), ('load_post', _load_post)):
        handlers = getattr(bpy.app.handlers, name)
        for old in list(handlers):
            if old.__module__ == __name__ and old.__name__ == callback.__name__:
                handlers.remove(old)
        handlers.append(callback)


def unregister():
    # Keep plain identity observations for re-enable/F3 and history. Every use revalidates live IDs/UIDs;
    # a new Main clears them in load_post. No RNA references or running callbacks are retained here.
    for name in ('save_pre', 'load_post'):
        handlers = getattr(bpy.app.handlers, name)
        for callback in list(handlers):
            if callback.__module__ == __name__:
                handlers.remove(callback)
