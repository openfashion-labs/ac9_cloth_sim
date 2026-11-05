"""The pieces' looks (core/look.py) put on the objects that show them: the dressed garment (per face, by its
ac9_piece attribute), the placed pieces and the flat pattern (one piece each), the Guide for Cloth Retopo. The slots
are linked to the mesh (DATA): the garment's mesh is replaced at every run and its looks are put on it again from the
spec (Fable review 2026-10-20 §2). Every one of them has the UV 'UVMap' = the pattern's mm / 1000 (1 UV = 1 m), so a
texture lies on the same place of the cloth on all of them."""

import json
import uuid

import bpy
import numpy as np

from .core import look as LK

UID_PROP = "ac9_look_uid"
UV_NAME = "UVMap"


def ref(mat):
    """The spec's reference to `mat` ({"material", "uid"}; a uid is given to the material the first time)."""
    if mat is None:
        return None
    if not mat.get(UID_PROP):
        mat[UID_PROP] = uuid.uuid4().hex[:12]
    return {"material": mat.name, "uid": str(mat[UID_PROP])}


def material(lk):
    """The material of a reference: the one material with its uid; else the one named (its uid written again);
    else None. A duplicate (Shift+D, append) carries the uid too: then the name decides."""
    if not lk:
        return None
    hits = [m for m in bpy.data.materials if m.get(UID_PROP) == lk.get('uid')] if lk.get('uid') else []
    if len(hits) == 1:
        return hits[0]
    m = bpy.data.materials.get(lk.get('material') or '')
    if m is not None and lk.get('uid') and not hits:
        m[UID_PROP] = lk['uid']
    return m


def set_slots(me, mats, index=None):
    """Mesh `me`'s slots to `mats` (None = an empty slot), each face's slot from `index` (per face; None = all 0).
    Left alone when already so (no depsgraph update on every redraw)."""
    if [m for m in me.materials] != list(mats):
        me.materials.clear()
        for m in mats:
            me.materials.append(m)
    if len(me.polygons):
        idx = np.zeros(len(me.polygons), np.int32) if index is None else np.asarray(index, np.int32)
        cur = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get('material_index', cur)
        if not np.array_equal(cur, idx):
            me.polygons.foreach_set('material_index', idx)
            me.update()


def apply_garment(ob, spec):
    """The dressed garment's (or the Guide's) slots from the looks: one slot per material used, a piece without a
    look on an empty slot. Nothing done when the garment has no look (a person's own materials stay). True if set."""
    if ob is None or ob.type != 'MESH' or not LK.any_look(spec):
        return False
    me = ob.data
    names = json.loads(ob.get('ac9_piece_names', '[]'))
    pa = me.attributes.get('ac9_piece')
    if pa is None or not names:
        return False
    per = [material(LK.resolved(spec, n)) for n in names]
    mats = list(dict.fromkeys(per))              # the order of first use, None (no look) included once
    slot = np.array([mats.index(m) for m in per], np.int32)
    fpc = np.empty(len(me.polygons), np.int32)
    pa.data.foreach_get('value', fpc)
    set_slots(me, mats, slot[np.clip(fpc, 0, len(slot) - 1)])
    return True


def apply_piece(ob, spec, name):
    """A one-piece object's (a placed piece, a flat pattern piece) slot from piece `name`'s look. Nothing done when
    the garment has no look."""
    if ob is None or ob.type != 'MESH' or not LK.any_look(spec):
        return False
    set_slots(ob.data, [material(LK.resolved(spec, name))])
    return True


def apply_all(context, spec):
    """Every object showing the garment's pieces (after a look changed): the dressed garment, the placed pieces,
    the flat pattern."""
    from . import dress as D
    from . import pattern_view as PV
    from . import placed_view as PLV
    apply_garment(bpy.data.objects.get(D.garment_name(spec)), spec)
    for n, ob in PV.piece_objects().items():
        ensure_uv_flat(ob.data)
        apply_piece(ob, spec, n)
    PV.refresh_enabled(spec)                          # an Off piece's gray on top of the look
    for n, ob in PLV.objects(spec).items():
        ensure_uv_placed(ob.data)
        apply_piece(ob, spec, n)


def _set_uv(me, uv_per_vertex):
    lay = me.uv_layers.get(UV_NAME) or me.uv_layers.new(name=UV_NAME)
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', lv)
    lay.data.foreach_set('uv', np.asarray(uv_per_vertex, np.float32)[lv].ravel())


def ensure_uv_flat(me):
    """A flat pattern piece (pattern_view: its vertices are the pattern in m on z = 0): the UV is x, y."""
    if me is None or me.uv_layers.get(UV_NAME) is not None or not len(me.loops):
        return
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get('co', co)
    _set_uv(me, co.reshape(-1, 3)[:, :2])


def ensure_uv_placed(me):
    """A placed piece (placed_view: the pattern's mm per vertex in its flat attribute): the UV is that / 1000."""
    from . import placed_view as PLV
    a = me.attributes.get(PLV.FLAT_ATTR) if me is not None else None
    if a is None or me.uv_layers.get(UV_NAME) is not None or not len(me.loops):
        return
    fl = np.empty(len(me.vertices) * 2, np.float32)
    a.data.foreach_get('vector', fl)
    _set_uv(me, fl.reshape(-1, 2) / 1000.0)
