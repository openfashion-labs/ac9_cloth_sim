# SPDX-License-Identifier: GPL-3.0-or-later
"""The placed shapes as Blender objects: one mesh per piece (a mirrored pair is one object with a Mirror modifier),
what Dress would start from. Move, rotate, tilt or edit them like any mesh (G, R, the N panel, Edit Mode); Dress then
starts a moved piece from its object instead of from the guide.

The guide cylinder is only a way to put a piece somewhere quickly (user decision 2026-10-10, DECISIONS
cloth-sim-placement-cylinder-is-only-a-start-2026-10-10): once placed, the shape is free. A piece is 'moved' when its
object's vertices are no longer where they were made (each vertex keeps the world position it was made at in the
attribute GEN_ATTR and its layout point in FLAT_ATTR, so a changed pattern still maps by the layout).

How far the object was moved and turned as a whole (its matrix: the pose) is also written to the spec, as
placement[piece]['pose'] (user 2026-10-14, DECISIONS cloth-sim-3d-piece-move-2026-10-14): numbers a person, the
panel's fields or an AI read and write, and what the object is made again with (Place, Adjust, Mirror Placement keep
it; only Back to Guide drops it). Written by sync_pose at the start of the AC9 operators, never by a handler (the
spec's Text is then written inside an operator, so Ctrl+Z takes it back with the move). Edits in Edit Mode stay in
the object only.

A mirrored pair (S.linked_twins) is one object: the piece on +x (as Dress chooses it) with a Mirror modifier
about the center plane (CENTER_NAME, an Empty at the torso guide's axis x), so moving or editing it moves its pair too.
"""
from . import prepare_build as PB
import json
import hashlib

import bpy
import numpy as np
from mathutils import Matrix
from .i18n import tx

COLL_NAME = "AC9_ClothSim_Placed"
CENTER_NAME = "AC9_ClothSim_Center"
OLD_CENTER_NAME = "AC9_ClothSim_Centre"     # the name before 2026-10-21 (American spelling), in older files
PREFIX = "AC9_Placed_"
PROP = "ac9_placed"             # on an object: the piece it holds
TWIN_PROP = "ac9_placed_twin"   # ... and the piece its Mirror modifier makes ('' if none)
LEAD_PROP = "ac9_placed_lead"   # ... a pair's side not mirror-meshed: the piece whose object it follows mirrored
FOLLOWER_PROP = "ac9_placed_follower"   # ... on that leader: the piece following it
MIRROR_NAME = "AC9_ClothSim_Mirror"     # the empty mirroring a follower (its matrix: the x mirror at the center)
FLAT_ATTR = "ac9_flat"          # per vertex: the layout point (mm)
GEN_ATTR = "ac9_gen"            # per vertex: the world position it was made at (m)
HAND_PROP = "ac9_hand"          # on an object, in files before the computed-shape mark: read as SIM_PROP
SIM_PROP = "ac9_from_sim"       # ... its shape was computed (a Local Sim's last shape, a piece's shape kept while it was
                                #     Off): kept until Back to Placed / Back to Guide, which make it from the numbers again
FOLD_PROP = "ac9_fold"          # ... folded over its lines by Fold (place_fold): the user's shape too
SEAM_PROP = "ac9_to_seam"       # ... moved onto its seams by Part to Seam (place_seam): the user's shape too
LAID_PROP = "ac9_laid"          # ... laid on its sewn edge by Band on Edge (place_lay): the user's shape, laid again
                                # by Simulate's lay (dress.py leaves it out of 'hand')
KK_PROP = "ac9_kk"              # ... and its vertex k is the piece's mesh vertex k (a mirror-meshed twin's: its layout
                                # points in FLAT_ATTR are the source piece's)
GARMENT_PROP = "ac9_placed_garment"   # ... and the garment (spec name) it belongs to: a scene can hold several
KEY_PROP = "ac9_placed_key"     # ... and the piece's layout mesh when made (vertex count, a hash of the points): a
                                # pattern edited or re-meshed since is reported (mapped through the layout)
NV_PROP = "ac9_placed_nv"       # ... and its vertex count when made: vertices added or deleted by hand -> numbers
BODY_PROP = "ac9_placed_body"   # ... and the body it was made on (placement.body_fingerprint): the body changed since
                                # -> a piece not moved by hand is made again (user 2026-11-13, the shape seen is the start)
ORG_PROP = "ac9_placed_org"     # ... and the world point (mm) it was made round: its location then, the pose's pivot
ORIGIN_SHIFT_PROP = "ac9_origin_shift"  # local meters: display origin shift from the original pose frame
MOVED_TOL = 1e-5                # m: a vertex this far from where it was made has been moved
POSE_MM, POSE_DEG = 1e-3, 1e-4  # a pose smaller than this is no pose (not written)
MM = 0.001
_clicked_control = {}  # one-shot AC9 3D face selection, never saved as pattern state
_control_signature = None


def _selected(context):
    from . import selection as SEL
    return SEL.objects(context)


def _selection_signature(context):
    active = context.view_layer.objects.active
    return (tuple(sorted(ob.session_uid for ob in _selected(context))), active.session_uid if active else None)


@bpy.app.handlers.persistent
def clear_control_hints(*_args):
    _clicked_control.clear()


@bpy.app.handlers.persistent
def watch_control_selection(*_args):
    if _clicked_control and _selection_signature(bpy.context) != _control_signature:
        _clicked_control.clear()


def placed_face_piece(context, ob, face_index):
    """Resolve the actual half touched, independently of the object's current source."""
    if not ob.get(TWIN_PROP):
        return ob.get(PROP)
    modifiers = [m for m in ob.modifiers if m.show_viewport]
    if len(modifiers) != 1 or modifiers[0].type != 'MIRROR':
        return None  # face ownership is known only for the standard generated pair
    me = ob.evaluated_get(context.evaluated_depsgraph_get()).data
    if not (0 <= face_index < len(me.polygons)):
        return None
    verts, n = me.polygons[face_index].vertices, len(ob.data.vertices)
    return ob[PROP] if max(verts) < n else ob[TWIN_PROP] if min(verts) >= n and max(verts) < 2 * n else None


def remember_control_side(context, ob, face_index):
    """Keep the actual clicked half of an AC9 placed-pair selection."""
    global _control_signature
    _clicked_control.clear()
    side = placed_face_piece(context, ob, face_index)
    if not ob.get(TWIN_PROP):
        return
    if side:
        _clicked_control[ob.session_uid] = (ob.name, ob[PROP], ob[TWIN_PROP], side)
        _control_signature = _selection_signature(context)


def hand_selection(context, names):
    """Use a fresh clicked half for explicit movement controls; other pair operations stay paired."""
    out = list(names)
    watch_control_selection()
    hints = dict(_clicked_control)
    _clicked_control.clear()
    explicit = {ob.get('ac9_piece') for ob in _selected(context) if not ob.get(PROP)}
    for ob in _selected(context):
        hint = hints.get(ob.session_uid)
        if hint is not None and hint[:3] == (ob.name, ob.get(PROP), ob.get(TWIN_PROP)):
            other = hint[2] if hint[3] == hint[1] else hint[1]
            if other in out and other not in explicit:
                out.remove(other)
    return out


def center_x(spec):
    """World x (mm) of the plane a pair mirrors about: the torso guide's axis (the body's center), else 0."""
    g = spec.get('guides', {}).get('torso')
    return float(g['o'][0]) if g else 0.0


def center_y(spec):
    """World y (mm) of the plane a front / back pair mirrors about: the torso guide's axis, else 0."""
    g = spec.get('guides', {}).get('torso')
    return float(g['o'][1]) if g else 0.0


PLANE_PROP = "ac9_mirror_plane"    # on a follower (link) and a Mirror-modified object: 'y' for a front / back pair


def _plane_c(spec, plane, name=None):
    """(diagonal of the mirror, world m of the plane along its axis) of a pair mirrored in `plane` (the pair of
    `name`: its own plane position when it has one, S.mirror_center)."""
    from .core import spec as S
    return Matrix.Diagonal((*S.mirror_diag(plane), 1.0)), S.mirror_center(spec, plane, name) * MM


def _mirror_T(plane, c):
    """The translation that, after the mirror's diagonal, puts the plane back at c (m) along its axis."""
    v = [0.0, 0.0, 0.0]
    v[1 if plane == 'y' else 0] = 2.0 * c
    return Matrix.Translation(v)


def _collection(context):
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
        context.scene.collection.children.link(coll)
        from . import view_setup
        view_setup.refresh(context)
    return coll


def garment(spec):
    return str((spec or {}).get('name', ''))


def layout_key(V):
    V = np.ascontiguousarray(np.round(np.asarray(V, float), 3))
    return f"{len(V)}:{hash(V.tobytes()) & 0xFFFFFFFF:08x}"


def objects(spec=None):
    """{piece: object} for every piece an object holds (a pair's mirrored piece maps to the same object), of the
    garment of spec (all garments if None)."""
    coll = bpy.data.collections.get(COLL_NAME)
    out = {}
    if coll is None:
        return out
    gname = garment(spec) if spec is not None else None
    for ob in coll.objects:
        if PROP in ob and (gname is None or ob.get(GARMENT_PROP, '') == gname):
            out[ob[PROP]] = ob
            if ob.get(TWIN_PROP):
                out[ob[TWIN_PROP]] = ob
    return out


def _center_object():
    ob = bpy.data.objects.get(CENTER_NAME)
    old = bpy.data.objects.get(OLD_CENTER_NAME)
    if ob is None and old is not None:
        old.name = CENTER_NAME
        ob = old
    return ob


def _pair_key(spec, name):
    """The pair of `name` named by its two pieces, sorted (the suffix of a pair's own empties), or None."""
    from .core import draft as D
    tw = D.twin_of(spec, name) if name else None
    return "|".join(sorted((name, tw))) if tw else None


def aux_empties():
    """The empties placed_view makes: the center, a pair's own center, the mirrors (all names)."""
    return [o.name for o in bpy.data.objects if o.type == 'EMPTY'
            and (o.name in (CENTER_NAME, OLD_CENTER_NAME) or o.name.startswith(CENTER_NAME + ":")
                 or o.name.startswith(MIRROR_NAME))]


def center_empty(context, spec, name=None):
    """The empty a pair's Mirror modifier mirrors about: the body's center (one for every pair), or, for a pair
    with its own plane position (S.MIRROR_AT, the pair of `name`), an empty of its own at it."""
    from .core import spec as S
    at = S.stored_mirror_at(spec, name) if name else None
    key = _pair_key(spec, name) if at is not None else None
    if key is None:
        ob = _center_object()
        nm = CENTER_NAME
    else:
        nm = CENTER_NAME + ":" + key
        ob = bpy.data.objects.get(nm)
    if ob is None:
        ob = bpy.data.objects.new(nm, None)
        ob.empty_display_type = 'SINGLE_ARROW'
        ob.empty_display_size = 0.2
        ob.hide_select = True
        _collection(context).objects.link(ob)
    loc = [center_x(spec) * MM, center_y(spec) * MM, 0.0]     # x mirrors about its x, front / back its y
    if key is not None:
        loc[S.axis_of(S.pair_plane(spec, name))] = at * MM
    ob.location = loc
    ob.rotation_euler = (0.0, 0.0, 0.0)
    return ob


def sync_edit(ob):
    """An object in Edit Mode: its mesh as edited so far (else the vertices read are those before Edit Mode)."""
    if ob is not None and ob.mode == 'EDIT':
        ob.update_from_editmode()


def leave_edit(context):
    """Back to Object Mode when a placed object is being edited: it may be removed or rebuilt next, and in Edit Mode
    its attributes (the layout points) cannot be read (Blender keeps them in the edit mesh)."""
    if any(ob.mode == 'EDIT' for ob in objects().values()):
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            for ob in objects().values():
                if ob.mode == 'EDIT':
                    ob.update_from_editmode()


KEEP_PREFIX = "AC9_PlacedKeep_"
KEEP_PROP = "ac9_keep"          # on a kept mesh: JSON {"piece", "garment", "matrix", "props"} (see keep)
KEEP_OB_PROPS = (SIM_PROP, HAND_PROP, FOLD_PROP, SEAM_PROP, LAID_PROP, KK_PROP, KEY_PROP, NV_PROP, ORG_PROP, ORIGIN_SHIFT_PROP)
BEFORE_SEAM_PROP = "ac9_before_seam"  # fake-user mesh: the prepared shape, never the fitted result


def shape_record(ob):
    """Shape metadata for an operation-local backup or a persistent prepared mesh."""
    sync_edit(ob)
    return {'piece': ob[PROP], 'matrix': [float(v) for row in ob.matrix_world for v in row],
            'props': {p: (ob[p].to_list() if hasattr(ob[p], 'to_list') else ob[p])
                      for p in KEEP_OB_PROPS if p in ob}}


def put_shape(ob, me, record):
    """Install a mesh and its pose/shape flags together; the object's pair modifier stays."""
    old = ob.data
    ob.data = me
    ob.matrix_world = Matrix([record['matrix'][r * 4:r * 4 + 4] for r in range(4)])
    for p in KEEP_OB_PROPS:
        ob.pop(p, None)
    for p, v in record['props'].items():
        ob[p] = v
    if old.users == 0:
        bpy.data.meshes.remove(old)
    invalidate_shown()


def before_seam(spec):
    """Prepared meshes owned by this garment; read only at explicit operations, never draw/poll."""
    out = {}
    for me in bpy.data.meshes:
        if me.get(BEFORE_SEAM_PROP):
            d = json.loads(me[BEFORE_SEAM_PROP])
            if d['garment'] == garment(spec):
                out[d['piece']] = me
    return out


def forget_before_seam(spec, names):
    for n, me in before_seam(spec).items():
        if n in names:
            bpy.data.meshes.remove(me)


def save_before_seam(ob, spec):
    """Capture only the first successful fit's input, after folding; repeated fits keep it."""
    n = ob[PROP]
    if n in before_seam(spec):
        return
    me = ob.data.copy()
    me.name = "AC9_BeforeSeam_" + garment(spec) + '_' + n
    d = dict(shape_record(ob), garment=garment(spec), twin=ob.get(TWIN_PROP, ''),
             pattern=before_seam_pattern(spec, n))
    me[BEFORE_SEAM_PROP] = json.dumps(d)
    me.use_fake_user = True


def before_seam_pattern(spec, piece):
    """Stable geometry identity even when the placed object is currently archived."""
    pc = next((p for p in spec.get('pieces', []) if p['name'] == piece), {})
    shape = {k: pc[k] for k in ('edges', 'internals', 'holes', 'mirror_axis') if k in pc}
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode('utf-8')).hexdigest()


def check_before_seam(spec, names):
    """Preflight all requested pieces before mutating any of them. Old fitted files have no inverse."""
    from .core import spec as S
    obs, saved, archived = objects(spec), before_seam(spec), _kept(spec)
    aliases = dict(saved)
    for me in saved.values():
        d = json.loads(me[BEFORE_SEAM_PROP])
        if d.get('twin'):
            aliases[d['twin']] = me
    for n in names:
        ob = obs.get(n)
        lead = ob.get(PROP) if ob is not None else n
        me = aliases.get(lead)
        if me is not None:
            lead = json.loads(me[BEFORE_SEAM_PROP])['piece']
        props = ob if ob is not None else json.loads(archived[n][KEEP_PROP])['props'] if n in archived else {}
        if me is not None:
            d = json.loads(me[BEFORE_SEAM_PROP])
            pair_changed = (ob is not None and ob.get(TWIN_PROP, '') != d.get('twin', ''))
            if ob is None and d.get('twin'):
                pair_changed = {lead, d['twin']} not in [set(p) for p in S.placement_pairs(spec)]
            if pair_changed:
                raise ValueError(tx("{0}: its pair changed after Part to Seam; undo the pair change or place it again", n))
            if d.get('invalid') or d.get('pattern') != before_seam_pattern(spec, lead):
                raise ValueError(tx("{0}: the pattern mesh changed after Part to Seam; its prepared shape was kept "
                                    "but cannot be restored. Undo the pattern change or place the piece again", n))
        elif props.get(SEAM_PROP):
            raise ValueError(tx("{0}: this file has no shape saved before Part to Seam. Undo that move or use "
                                "Back to Guide to place it again (hand moves are lost)", n))


def restore_before_seam(spec, names, consume=True):
    """Remove the fit only. A simulation's archived fitted shape is replaced before Reset reads it."""
    names = set(names)
    check_before_seam(spec, names)
    obs, archived = objects(spec), _kept(spec)
    done = []
    for n, me in before_seam(spec).items():
        d = json.loads(me[BEFORE_SEAM_PROP])
        if not ({n, d.get('twin')} & names):
            continue
        restored = me.copy()
        restored.use_fake_user = False
        del restored[BEFORE_SEAM_PROP]
        ob = obs.get(n)
        if ob is not None:
            put_shape(ob, restored, d)
        else:
            if n in archived:
                bpy.data.meshes.remove(archived[n])
            restored[KEEP_PROP] = json.dumps({k: d[k] for k in ('piece', 'garment', 'matrix', 'props')})
            restored.use_fake_user = True
            restored.name = KEEP_PREFIX + garment(spec) + '_' + n
        if consume:
            bpy.data.meshes.remove(me)
        done.append(n)
    return done


class PreparedResetBackup:
    """Operation-local rollback. Copies have no ownership tags, so builders cannot consume them."""

    def __init__(self, context, spec):
        from . import store, dress as D
        self.context, self.spec = context, spec
        self.text = store.text(context)
        if self.text is None:
            raise ValueError('No active pattern to back up')
        self.text_value = self.text.as_string()
        self._restored = self._closed = False
        self.preparation_owner = None
        self.had_collection = bpy.data.collections.get(COLL_NAME) is not None
        self.auxiliary = []
        for name in aux_empties():
            ob = bpy.data.objects.get(name)
            if ob is not None and ob.type == 'EMPTY':
                self.auxiliary.append((name, ob.copy(), tuple(c.name for c in ob.users_collection), ob.hide_get()))
        self.live, self.meshes = [], []
        self.original_objects = {}
        self.parents, self.targets = {}, {}
        self.selected = {}
        active = context.view_layer.objects.active
        self.active = active.name if active is not None else None
        for ob in set(objects(spec).values()):
            sync_edit(ob)
            backup = ob.copy()
            backup.data = ob.data.copy()
            self.live.append((ob.name, backup, ob.hide_get()))
            self.original_objects[ob.name] = int(ob.session_uid)
            self.selected[ob.name] = ob.select_get(view_layer=context.view_layer)
            self.parents[ob.name] = (ob.parent.name if ob.parent else None, ob.matrix_world.copy(), ob.matrix_parent_inverse.copy())
            self.targets[ob.name] = [(c.name, c.target.name) for c in ob.constraints
                                     if hasattr(c, 'target') and c.target is not None]
        for prop, meshes in ((KEEP_PROP, _kept(spec)), (BEFORE_SEAM_PROP, before_seam(spec))):
            for me in meshes.values():
                backup = me.copy()
                value = backup[prop]
                del backup[prop]
                backup.use_fake_user = False
                self.meshes.append((prop, value, backup))
        self.g = context.scene.objects.get(D.garment_name(spec))
        self.gmesh = self.g.data.copy() if self.g is not None else None
        self.gprops = {k: self.g.get(k) for k in D.ADDON_PROPS} if self.g else {}
        self.cache = [(m, m.show_viewport, m.show_render) for m in self.g.modifiers if m.type == 'MESH_CACHE'] if self.g else []

    def can_restore(self):
        return self.preparation_owner is None or self.preparation_owner.can_restore(self)

    def rollback(self):
        from . import store, dress as D
        if self._restored:
            return
        if not self.can_restore():
            # A foreign Text/Scene/history owns the new state; an old operation must not write its snapshot over it.
            self._restored = True
            return
        for index, (name, backup, collections, hidden) in enumerate(self.auxiliary):
            ob = bpy.data.objects.get(name)
            if ob is None:
                ob = backup
                ob.name = name
                for coll_name in collections:
                    coll = bpy.data.collections.get(coll_name)
                    if coll is not None:coll.objects.link(ob)
                if not ob.users_collection:_collection(self.context).objects.link(ob)
                self.auxiliary[index] = (name, None, collections, hidden)
            else:
                ob.matrix_world = backup.matrix_world
            ob.hide_set(hidden)
        originals = set(self.original_objects.values())
        for ob in set(objects(self.spec).values()):
            if int(ob.session_uid) not in originals:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me.users == 0:
                    bpy.data.meshes.remove(me)
        for i, (name, backup, hidden) in enumerate(self.live):
            uid = self.original_objects[name]
            ob = next((o for o in bpy.data.objects if int(o.session_uid) == uid), None)
            if ob is not None:ob.name = name
            if ob is None:
                ob = backup
                ob.name = name
                _collection(self.context).objects.link(ob)
                self.live[i] = (name, None, hidden)
            else:
                put_shape(ob, backup.data, shape_record(backup))
                for key in ('color', 'hide_viewport', 'hide_render', 'show_in_front', 'display_type', 'active_material_index'):
                    setattr(ob, key, getattr(backup, key))
                for k in list(ob.keys()):
                    del ob[k]
                for k, v in backup.items():
                    ob[k] = v.to_list() if hasattr(v, 'to_list') else v
                if backup.get(TWIN_PROP) and not any(m.type == 'MIRROR' for m in ob.modifiers):
                    _add_mirror(ob, center_empty(self.context, self.spec, backup[TWIN_PROP]), backup[TWIN_PROP], backup.get(PLANE_PROP, 'x'))
            ob.hide_set(hidden)
        # Removing a leader clears RNA references even on object copies. Restore links by name only after all
        # original objects exist again, then restore their original world transforms.
        for name, (parent, world, inverse) in self.parents.items():
            ob = bpy.data.objects.get(name)
            if ob is not None:
                ob.parent = bpy.data.objects.get(parent) if parent else None
                ob.matrix_parent_inverse = inverse
                for constraint, target in self.targets[name]:
                    c = ob.constraints.get(constraint)
                    if c is not None:
                        c.target = bpy.data.objects.get(target)
                ob.matrix_world = world
                if ob.name in self.context.view_layer.objects:
                    ob.select_set(self.selected[name], view_layer=self.context.view_layer)
        if self.active and self.active in self.context.view_layer.objects:
            self.context.view_layer.objects.active = self.context.view_layer.objects[self.active]
        forget(self.spec)
        forget_before_seam(self.spec, set(before_seam(self.spec)))
        for prop, value, me in self.meshes:
            me[prop] = value
            me.use_fake_user = True
        self.meshes.clear()  # restored meshes now belong to the garment
        if self.text.as_string() != self.text_value:
            self.text.from_string(self.text_value)
        store.invalidate()
        if self.g is not None:
            old = self.g.data
            self.g.data, self.gmesh = self.gmesh, None
            if old.users == 0:
                bpy.data.meshes.remove(old)
            for k, v in self.gprops.items():
                if v is None:
                    self.g.pop(k, None)
                else:
                    self.g[k] = v
            for m, viewport, render in self.cache:
                m.show_viewport, m.show_render = viewport, render
            D.sync_hidden(self.g, self.spec)
        invalidate_shown()
        original_aux = {name for name, _, _, _ in self.auxiliary}
        for name in aux_empties():
            ob = bpy.data.objects.get(name)
            if name in original_aux or ob is None or ob.type != 'EMPTY':continue
            used = any(other.parent == ob or any(m.type == 'MIRROR' and m.mirror_object == ob for m in other.modifiers)
                       or any(getattr(c, 'target', None) == ob for c in other.constraints)
                       for other in bpy.data.objects if other != ob)
            if not used:bpy.data.objects.remove(ob, do_unlink=True)
        coll = bpy.data.collections.get(COLL_NAME)
        if not self.had_collection and coll is not None and not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)
        self._restored = True

    def close(self):
        if self._closed:
            return
        for _, ob, _ in self.live:
            if ob is not None:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me.users == 0:
                    bpy.data.meshes.remove(me)
        for _, _, me in self.meshes:
            bpy.data.meshes.remove(me)
        if self.gmesh is not None:
            bpy.data.meshes.remove(self.gmesh)
        for _, ob, _, _ in self.auxiliary:
            if ob is not None:bpy.data.objects.remove(ob, do_unlink=True)
        self._closed = True


def _kept(spec):
    """{piece: mesh} kept at a Dress (keep) for the garment of spec."""
    out = {}
    gname = garment(spec)
    for me in bpy.data.meshes:
        k = me.get(KEEP_PROP)
        if k:
            d = json.loads(k)
            if d.get('garment') == gname:
                out[d['piece']] = me
    return out


def forget(spec, names=None):
    """The placed shapes kept for these pieces (all the garment's if None) dropped: they are made from the numbers."""
    for n, me in _kept(spec).items():
        if names is None or n in names:
            bpy.data.meshes.remove(me)


def keep(ob, spec):
    """At a Dress: a moved object's mesh kept as it is (a fake user, out of the scene) with its matrix and the props
    of its shape, so the next build of its piece gives it back -- folded, moved to its seams, laid, edited (user
    2026-10-22: Back to Placed after a Simulate kept the hand moves but lost a placket's Fold)."""
    forget(spec, [ob[PROP]])
    me = ob.data
    if ob.mode == 'EDIT':
        ob.update_from_editmode()
    me[KEEP_PROP] = json.dumps({'piece': ob[PROP], 'garment': garment(spec),
                                'matrix': [float(v) for row in ob.matrix_world for v in row],
                                'props': {p: (ob[p].to_list() if hasattr(ob[p], 'to_list') else ob[p])
                                          for p in KEEP_OB_PROPS if p in ob}})
    me.name = KEEP_PREFIX + garment(spec) + "_" + ob[PROP]
    me.use_fake_user = True


def keep_shape(spec, piece, flat, tris, X_mm):
    """A shape for `piece` kept as keep does, made from numbers (flat mm, triangles, world mm): the user's shape,
    given back when the piece is made again, under the object's own matrix (its pose kept). A piece switched Off
    keeps its last simulated shape this way when a run leaves it out (user 2026-11-03: switched On again it is
    where it was simulated, not where it was placed)."""
    forget(spec, [piece])
    me = _mesh(KEEP_PREFIX + garment(spec) + "_" + piece, flat, tris, X_mm, np.zeros(3))
    me[KEEP_PROP] = json.dumps({'piece': piece, 'garment': garment(spec), 'world': True,
                                'props': {SIM_PROP: True, NV_PROP: len(flat), KEY_PROP: layout_key(flat)}})
    me.use_fake_user = True


def _give_back(ob, me):
    """A kept mesh back on the object build just made for its piece (the made mesh dropped)."""
    d = json.loads(me[KEEP_PROP])
    del me[KEEP_PROP]
    me.use_fake_user = False
    made = ob.data
    ob.data = me
    me.name = made.name
    if made.users == 0:
        bpy.data.meshes.remove(made)
    if d.get('world'):                          # keep_shape: vertices in world m, put under the object's matrix
        me.transform(ob.matrix_world.inverted())
    else:
        ob.matrix_world = Matrix([d['matrix'][r * 4:r * 4 + 4] for r in range(4)])
        ob.pop(ORIGIN_SHIFT_PROP, None)
    for p, v in d['props'].items():
        ob[p] = v


def remove(names, spec=None, forget_kept=True):
    """Drop the objects holding these pieces (they are made again from the numbers), and what a Dress kept of
    them (forget_kept)."""
    obs = objects(spec)
    names = set(names)
    names |= {obs[n][PROP] for n in names if n in obs}
    if forget_kept and spec is not None:
        forget(spec, set(names))
        forget_before_seam(spec, set(names))
    going = {obs[n] for n in names if n in obs}
    for ob in set(obs.values()) - going:
        if ob.get(LEAD_PROP) in names:      # its leader goes: it stays where it is, on its own until linked again
            unlink(ob)
    for ob in going:
        if ob.mode == 'EDIT':
            ob.update_from_editmode()
        me = ob.data
        bpy.data.objects.remove(ob)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def drop(spec):
    """At a Dress: the garment's placed objects removed, and the collection with its center Empty once no garment's
    are left (user 2026-10-14: after a Dress the garment is the one mesh to work on; the placed shapes are made again
    from the spec's placement and hand moves -- the pose -- when wanted: a Reset, Move by Hand after a whole Reset).
    The moved objects' shapes are kept (keep: a Fold, Part to Seam, Band on Edge or an Edit Mode edit is not only a
    pose, user 2026-10-22) and given back when their pieces are made again. Returns the pieces removed."""
    obs = objects(spec)
    gone = sorted(obs)
    forget(spec, set(gone))
    bpy.context.view_layer.update()             # a follower's matrix after its leader was just written
    for ob in {o.name: o for o in obs.values()}.values():
        unlink(ob)                              # kept as a plain object (its pose's matrix), linked again when made
    for ob in {o.name: o for o in obs.values()}.values():
        if is_moved(ob):
            keep(ob, spec)
            ob.data = bpy.data.meshes.new(ob.name)      # the kept mesh off the object, which goes
    remove(list(obs), spec, forget_kept=False)
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is not None and not objects():
        for nm in aux_empties():
            bpy.data.objects.remove(bpy.data.objects[nm])
        if not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)
    return gone


def is_moved(ob):
    """Has the object (its own vertices, not the mirrored copy) left the shape it was made with?"""
    if from_sim(ob) or ob.get(FOLD_PROP) or ob.get(SEAM_PROP) or ob.get(LAID_PROP):
        return True
    return moved_since_made(ob)


def from_sim(ob):
    """Does the object (or a kept mesh's props dict) hold a computed shape (SIM_PROP, or HAND_PROP of older files)?"""
    return bool(ob.get(SIM_PROP) or ob.get(HAND_PROP))


def drop_sim_shapes(spec, names):
    """Back to Placed / Back to Guide on these pieces: the computed shapes they hold (an object or a mesh kept at a
    Dress, from a Local Sim or a piece switched Off) dropped, so they are made from the numbers (their pose stays in
    the spec). Returns the pieces dropped."""
    names = set(names)
    obs = objects(spec)
    gone = {n for n in names if n in obs and from_sim(obs[n])}
    gone |= {n for n, me in _kept(spec).items()
             if n in names and from_sim(json.loads(me[KEEP_PROP]).get('props', {}))}
    if gone:
        forget(spec, gone)                      # the shape before Part to Seam stays (Back to Placed restores it)
        remove(sorted(gone), spec, forget_kept=False)
    return sorted(gone)


def moved_since_made(ob):
    """Have the object's own vertices left where they were made or last written (GEN_ATTR: by a Dress, Fold, Part
    to Seam, Band on Edge), by a move as a whole or an edit?"""
    sync_edit(ob)
    me = ob.data
    n = len(me.vertices)
    gen = me.attributes.get(GEN_ATTR)
    if gen is None or n == 0 or len(gen.data) != n:
        return True
    co = np.empty(n * 3)
    me.vertices.foreach_get('co', co)
    g = np.empty(n * 3)
    gen.data.foreach_get('vector', g)
    M = np.array(ob.matrix_world)
    W = (np.c_[co.reshape(-1, 3), np.ones(n)] @ M.T)[:, :3]
    return bool(np.abs(W - g.reshape(-1, 3)).max() > MOVED_TOL)


def hand_shape(ob):
    """(flat (n, 2) mm, triangles (m, 3), world (n, 3) mm) of the object's own vertices, or None."""
    sync_edit(ob)
    me = ob.data
    fl = me.attributes.get(FLAT_ATTR)
    n = len(me.vertices)
    if fl is None or n == 0:
        return None
    co = np.empty(n * 3)
    me.vertices.foreach_get('co', co)
    W = (np.c_[co.reshape(-1, 3), np.ones(n)] @ np.array(ob.matrix_world).T)[:, :3] * 1000.0
    F = np.empty(n * 2)
    fl.data.foreach_get('vector', F)
    me.calc_loop_triangles()
    T = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get('vertices', T)
    return F.reshape(-1, 2), T.reshape(-1, 3), W


def shape_snapshot(ob, *, only_moved=False):
    """Capture the physical source once at an operation boundary; never keep RNA in the returned record.

    Existing flags are evidence, not a second persistent state. The world coordinates exclude display modifiers,
    exactly as hand_shape does. Layout coordinates remain millimeters, independent of the object's origin.
    """
    from .core.shape_source import PlacedShape
    moved = is_moved(ob)
    if only_moved and not moved:
        return None
    shape = hand_shape(ob)
    if shape is None:
        return None
    provenance = tuple(name for name, present in (
        ('result', from_sim(ob)), ('fold', bool(ob.get(FOLD_PROP))), ('seam', bool(ob.get(SEAM_PROP))),
        ('lay', bool(ob.get(LAID_PROP)))) if present)
    if not provenance:
        provenance = ('edited' if moved else 'guide',)
    flat, triangles, world = shape
    return PlacedShape(str(ob.get(PROP, '')), int(ob.session_uid), int(ob.data.session_uid), provenance,
                       flat, triangles, world, ob.get(NV_PROP), ob.get(KEY_PROP), bool(ob.get(KK_PROP)))


def set_mirror_plane(ob, plane):
    """A Mirror-modified pair object's axis set to its pair's plane (x, or y for front / back)."""
    for md in ob.modifiers:
        if md.type == 'MIRROR':
            md.use_axis = (plane != 'y', plane == 'y', False)
    if plane == 'y':
        ob[PLANE_PROP] = 'y'
    else:
        ob.pop(PLANE_PROP, None)


def _add_mirror(ob, cen, twin, plane='x'):
    ob[TWIN_PROP] = twin
    md = ob.modifiers.new("AC9 Pair", 'MIRROR')
    md.use_axis = (plane != 'y', plane == 'y', False)
    if plane == 'y':
        ob[PLANE_PROP] = 'y'
    else:
        ob.pop(PLANE_PROP, None)
    md.mirror_object = cen
    md.use_mirror_merge = False
    md.use_clip = False


def _tag(ob, spec, piece, V):
    ob.pop(ORIGIN_SHIFT_PROP, None)
    ob[ORG_PROP] = [float(c) * 1000.0 for c in ob.location]
    ob[PROP] = piece
    ob[GARMENT_PROP] = garment(spec)
    ob[KEY_PROP] = layout_key(V)
    ob[NV_PROP] = len(ob.data.vertices)


def activate_side(context, spec, name, *, preparation=None):
    activate_sides(context, spec, [name], preparation=preparation)
    return objects(spec).get(name)


@PB.prepared_consumer
def activate_sides(context, spec, names, *, preparation=None):
    """Validate every requested pair before changing any pair's representation."""
    staged = []
    try:
        for name in dict.fromkeys(names):
            change = _stage_control_side(context, spec, name, preparation=preparation)
            if change is not None:
                staged.append(change)
        for commit, cleanup in staged:
            commit()
    finally:
        for commit, cleanup in staged:
            cleanup()
    invalidate_shown()
    from . import overlay, piece_look
    overlay.invalidate()
    piece_look.invalidate()
    context.view_layer.update()


def _stage_control_side(context, spec, name, *, preparation=None):
    """Make a specifically chosen mirrored half the control side, without moving either piece.

    This changes representation, not placement or pair membership. Stage every
    owned mesh and its fold/preparation metadata before replacing anything.
    A normal 3D selection still selects the whole pair; callers invoke this only
    when the other half was not selected explicitly.
    """
    from .core import spec as S, draft as DR
    from . import place_fold as PF
    ob = objects(spec).get(name)
    if ob is None or ob.get(TWIN_PROP) != name:
        return None
    source = ob[PROP]
    outlines = {p['name']: S.Outline(p) for p in spec['pieces'] if p['name'] in (source, name)}
    if set(outlines) != {source, name}:
        raise ValueError('AC9 cannot switch control: the paired pattern is missing')
    sync_edit(ob)
    plane = outlines[source].plane
    k = 1 if plane == 'y' else 0
    F, cx = _plane_c(spec, plane, source)
    T = _mirror_T(plane, cx)
    saved, kept = before_seam(spec), _kept(spec)
    if name in saved or name in kept:
        raise ValueError('AC9 cannot switch control while the other half has a separate saved shape')
    # The mesh attribute stores float32 layout points. Reflecting those can cross
    # layout_key's rounding boundary even though the pattern has not changed.
    # Use the canonical twin layout only when its vertex correspondence matches.
    from . import dress as D
    prepared = D.prepare(context, context.scene.ac9_cloth_sim.mesh_size,
                         shapes_only=True, placed_only=True, preparation=preparation)
    names = [str(n) for n in prepared['pat']['names']]
    canonical = np.asarray(prepared['pat'][f"V{names.index(name)}"], float) if name in names else None
    pending = []

    def converted(me, record):
        if me.has_custom_normals:
            raise ValueError('AC9 cannot switch control of a shape with custom normals')
        props = dict(record.get('props', {}))
        if props.get(FOLD_PROP):
            folds = []
            for fold in json.loads(props[FOLD_PROP]):
                lid = str(fold.get('line', ''))
                if lid.startswith(PF.SEAM_KEY):
                    raise ValueError('AC9 cannot switch control of a saved seam fold; its shape was kept')
                twin = DR.twin_line(spec, source, lid)
                if twin is None or twin[0] != name:
                    raise ValueError('AC9 cannot match the saved fold to the other half: ' + lid)
                row = dict(fold, line=twin[1])
                if PF._ab_swapped(context, spec, source, lid, name, twin[1]):
                    row['side'] = 1 - int(row.get('side', 0))
                folds.append(row)
            props[FOLD_PROP] = json.dumps(folds)
        fl = me.attributes.get(FLAT_ATTR)
        if fl is None or len(fl.data) != len(me.vertices):
            raise ValueError('AC9 cannot switch control: the saved layout correspondence is missing')
        V = np.empty(len(me.vertices) * 2)
        fl.data.foreach_get('vector', V)
        V = mirror_flat(V.reshape(-1, 2), outlines[source].B, outlines[name].B, plane)
        canonical_match = (canonical is not None and canonical.shape == V.shape
                           and np.allclose(canonical, V, atol=1e-3, rtol=0))
        if canonical_match:
            V = canonical
        q = json.loads(json.dumps(record))
        q['piece'] = name
        if 'twin' in q:
            q['twin'] = source
        if 'pattern' in q:
            if q.get('invalid') or q['pattern'] != before_seam_pattern(spec, source):
                raise ValueError('AC9 cannot switch control: the saved prepared pattern is incompatible')
            q['pattern'] = before_seam_pattern(spec, name)
        M = Matrix([q['matrix'][i:i + 4] for i in range(0, 16, 4)])
        q['matrix'] = [float(v) for row in (T @ F @ M @ F) for v in row]
        if ORG_PROP in props:
            org = list(props[ORG_PROP])
            org[k] = 2.0 * cx / MM - org[k]
            props[ORG_PROP] = org
        if ORIGIN_SHIFT_PROP in props:
            shift = list(props[ORIGIN_SHIFT_PROP])
            shift[k] = -shift[k]
            props[ORIGIN_SHIFT_PROP] = shift
        props[KEY_PROP], props[KK_PROP] = layout_key(V), bool(canonical_match)
        q['props'] = props
        clone = me.copy()
        pending.append(clone)
        clone.transform(F, shape_keys=True)
        clone.flip_normals()
        clone.attributes[FLAT_ATTR].data.foreach_set('vector', V.astype(np.float32).ravel())
        for attr in clone.attributes:
            if attr.domain != 'POINT' or attr.data_type != 'FLOAT_VECTOR':
                continue
            if attr.name == GEN_ATTR or attr.name.startswith(PF.BEFORE_ATTR):
                co = np.empty(len(attr.data) * 3)
                attr.data.foreach_get('vector', co)
                co = co.reshape(-1, 3)
                co[:, k] = (2.0 * cx - co[:, k]) if attr.name == GEN_ATTR else -co[:, k]
                attr.data.foreach_set('vector', co.astype(np.float32).ravel())
        clone.update()
        return clone, q

    try:
        live, record = converted(ob.data, shape_record(ob))
        archives = []
        for prop, me in ((KEEP_PROP, kept.get(source)), (BEFORE_SEAM_PROP, saved.get(source))):
            if me is None:
                continue
            d = json.loads(me[prop])
            if prop == BEFORE_SEAM_PROP and d.get('twin', '') != name:
                raise ValueError('AC9 cannot switch control: the saved prepared pair is incompatible')
            clone, q = converted(me, d)
            clone[prop] = json.dumps(q)
            clone.use_fake_user = True
            archives.append((me, clone))
        def commit():
            put_shape(ob, live, record)
            pending.remove(live)
            ob[PROP], ob[TWIN_PROP] = name, source
            if ob.name.startswith(PREFIX):
                ob.name = PREFIX + name
            ob.data.name = PREFIX + name
            for old, clone in archives:
                clone.name = old.name.rsplit('_' + source, 1)[0] + '_' + name
                bpy.data.meshes.remove(old)
                pending.remove(clone)
        def cleanup():
            for me in pending:
                bpy.data.meshes.remove(me)
            pending.clear()
        return commit, cleanup
    except Exception:
        for me in pending:
            bpy.data.meshes.remove(me)
        raise


def split_pair(context, spec, outlines, a, b, keep_shape=False):
    """Unlink: a pair's object becomes its source piece's alone. Moved by hand, the other side gets its own object
    where the mirror showed it: its own vertices the source's mirrored (an edit in Edit Mode included), its matrix the
    source's mirrored, so its pose is the mirror image of the source's and it was made at the mirror image of the
    source's start (its layout points the source's mirrored into its own outline, as the pattern mirrors a twin,
    core/pattern_build.py). Not moved, both are made again from the numbers."""
    obs = objects(spec)
    ob = obs.get(a) or obs.get(b)
    if ob is None or not ob.get(TWIN_PROP):
        return
    twin, src_name = ob[TWIN_PROP], ob[PROP]
    if not keep_shape and not is_moved(ob):
        remove([a, b], spec)
        return
    from .core import spec as S
    plane = outlines[src_name].plane
    k = 1 if plane == 'y' else 0
    D3 = np.array(S.mirror_diag(plane))
    src = ob.data
    n = len(src.vertices)
    co = np.empty(n * 3)
    src.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3) * D3
    fl = np.empty(n * 2)
    src.attributes[FLAT_ATTR].data.foreach_get('vector', fl)
    fl_t = mirror_flat(fl.reshape(-1, 2), outlines[src_name].B, outlines[twin].B, plane)
    g = np.empty(n * 3)
    src.attributes[GEN_ATTR].data.foreach_get('vector', g)
    F, cx = _plane_c(spec, plane, src_name)
    g = g.reshape(-1, 3) * D3
    g[:, k] += 2.0 * cx
    src.calc_loop_triangles()
    T = np.empty(len(src.loop_triangles) * 3, np.int64)
    src.loop_triangles.foreach_get('vertices', T)
    me = bpy.data.meshes.new(PREFIX + twin)
    me.from_pydata(co.tolist(), [], T.reshape(-1, 3)[:, [0, 2, 1]].tolist())
    me.validate()
    me.shade_smooth()
    me.attributes.new(FLAT_ATTR, 'FLOAT2', 'POINT').data.foreach_set('vector', fl_t.astype(np.float32).ravel())
    me.attributes.new(GEN_ATTR, 'FLOAT_VECTOR', 'POINT').data.foreach_set('vector', g.astype(np.float32).ravel())
    nob = bpy.data.objects.new(PREFIX + twin, me)
    nob.matrix_world = _mirror_T(plane, cx) @ F @ ob.matrix_world @ F
    org = list(origin_mm(ob))
    _tag(nob, spec, twin, fl_t)
    org[k] = 2.0 * cx * 1000.0 - org[k]
    nob[ORG_PROP] = org
    if ob.get(ORIGIN_SHIFT_PROP) is not None:
        shift = list(ob[ORIGIN_SHIFT_PROP])
        shift[k] = -shift[k]
        nob[ORIGIN_SHIFT_PROP] = shift
    nob[KK_PROP] = True
    nob.color = ob.color
    _collection(context).objects.link(nob)
    for md in [m for m in ob.modifiers if m.type == 'MIRROR']:
        ob.modifiers.remove(md)
    del ob[TWIN_PROP]
    ob.pop(PLANE_PROP, None)


def join_pair(context, spec, a, b, prefer=None):
    """Link again: a side moved by hand leads (prefer: the active object's piece, when both were moved); its object
    gets the Mirror again and the other side's object goes. Neither moved: both are made again from the numbers."""
    obs = objects(spec)
    oa, ob_ = obs.get(a), obs.get(b)
    cand = [o for o in ((ob_, oa) if prefer == b else (oa, ob_)) if o is not None and is_moved(o)]
    lead = cand[0] if cand else None
    if lead is None:
        remove([a, b], spec)
        return
    other = b if lead.get(PROP) == a else a
    o2 = obs.get(other)
    if o2 is not None and o2 is not lead:
        me = o2.data
        bpy.data.objects.remove(o2)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    if not lead.get(TWIN_PROP):
        from .core import spec as S
        _add_mirror(lead, center_empty(context, spec, other), other, S.pair_plane(spec, other))


def origin_mm(ob):
    """The world point (mm) the object was made round (its pose's pivot): ORG_PROP, else (an object made before
    2026-10-14) the middle of where its vertices were made."""
    if ob.get(ORG_PROP) is not None:
        return np.asarray(ob[ORG_PROP], float)
    g = ob.data.attributes.get(GEN_ATTR)
    if g is None or not len(g.data):
        return np.array(ob.location) * 1000.0
    G = np.empty(len(g.data) * 3)
    g.data.foreach_get('vector', G)
    G = G.reshape(-1, 3) * 1000.0
    return 0.5 * (G.min(0) + G.max(0))


def pose_of(ob):
    """The object's move as a whole from where it was made: {'loc': mm, 'rot': deg (Euler XYZ)}, None if none.
    A scale is not part of it (it stays in the object, as an edit)."""
    shift = np.asarray(ob.get(ORIGIN_SHIFT_PROP, (0.0, 0.0, 0.0)), float)
    canonical = ob.matrix_world @ Matrix.Translation((-shift).tolist())
    loc, q, _sc = canonical.decompose()
    L = np.array(loc) * 1000.0 - (origin_mm(ob) - shift * 1000.0)
    E = np.degrees(np.array(q.to_euler('XYZ')))
    if np.abs(L).max() < POSE_MM and np.abs(E).max() < POSE_DEG:
        return None
    return {'loc': [round(float(x), 4) + 0.0 for x in L], 'rot': [round(float(x), 5) + 0.0 for x in E]}


def pose_matrix(pose, org_mm):
    """The object matrix of a pose about org_mm (the identity's location org_mm: as made)."""
    from mathutils import Euler
    loc = np.asarray(org_mm, float) + (np.asarray(pose['loc'], float) if pose else 0.0)
    R = Euler(np.radians(pose['rot']).tolist(), 'XYZ').to_matrix().to_4x4() if pose else Matrix.Identity(4)
    return Matrix.Translation((loc * MM).tolist()) @ R


def matrix_for_pose(ob, pose):
    """Apply a canonical saved pose while keeping the display origin on its piece."""
    shift = np.asarray(ob.get(ORIGIN_SHIFT_PROP, (0.0, 0.0, 0.0)), float)
    scale = ob.matrix_world.to_scale()
    return (pose_matrix(pose, origin_mm(ob) - shift * 1000.0)
            @ Matrix.Diagonal((*scale, 1.0)) @ Matrix.Translation(shift.tolist()))


def center_control_origins(context, spec, obs):
    """Put native gizmos on their source geometry, keeping shape and canonical pose.

    Only explicit Move by Hand calls this. Each live/kept/prepared shape carries
    its own local frame; no current-world translation is copied into an archive.
    """
    staged = []
    pending = []
    def centered(me, record):
        if not me.vertices:
            return None
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get('co', co)
        shift = co.reshape(-1, 3).mean(0)
        if np.abs(shift).max() < 1e-7:
            return None
        q = json.loads(json.dumps(record))
        M = Matrix([q['matrix'][r * 4:r * 4 + 4] for r in range(4)])
        org = q['props'].get(ORG_PROP)
        if org is None:
            gen = me.attributes.get(GEN_ATTR)
            G = np.empty(len(me.vertices) * 3)
            if gen is not None:
                gen.data.foreach_get('vector', G)
                G = G.reshape(-1, 3)
                org = (0.5 * (G.min(0) + G.max(0)) * 1000.0).tolist()
            else:
                org = (np.array(M.translation) * 1000.0).tolist()
        q['props'][ORG_PROP] = (np.asarray(org) + shift * 1000.0).tolist()
        q['props'][ORIGIN_SHIFT_PROP] = (np.asarray(q['props'].get(ORIGIN_SHIFT_PROP, (0, 0, 0))) + shift).tolist()
        q['matrix'] = [float(v) for row in (M @ Matrix.Translation(shift.tolist())) for v in row]
        clone = me.copy()
        pending.append(clone)
        clone.transform(Matrix.Translation((-shift).tolist()), shape_keys=True)
        for attr in clone.attributes:
            if attr.name.startswith('ac9_fold_before_') and attr.domain == 'POINT' and attr.data_type == 'FLOAT_VECTOR':
                V = np.empty(len(attr.data) * 3)
                attr.data.foreach_get('vector', V)
                attr.data.foreach_set('vector', (V.reshape(-1, 3) - shift).astype(np.float32).ravel())
        clone.update()
        return clone, q
    try:
        saved, kept = before_seam(spec), _kept(spec)
        for ob in set(obs):
            sync_edit(ob)
            item = centered(ob.data, shape_record(ob))
            if item is not None:
                staged.append((ob, None, ob.data, *item))
            for prop, meshes in ((KEEP_PROP, kept), (BEFORE_SEAM_PROP, saved)):
                me = meshes.get(ob[PROP])
                if me is None:
                    continue
                item = centered(me, json.loads(me[prop]))
                if item is not None:
                    staged.append((None, prop, me, *item))
        for ob, prop, old, clone, record in staged:
            if ob is not None:
                put_shape(ob, clone, record)
                clone.name = PREFIX + ob[PROP]
            else:
                name = old.name
                clone[prop] = json.dumps(record)
                clone.use_fake_user = True
                bpy.data.meshes.remove(old)
                clone.name = name
            pending.remove(clone)
    finally:
        for me in pending:
            bpy.data.meshes.remove(me)
    invalidate_shown()
    context.view_layer.update()


def same_pose(p, q):
    if not p or not q:
        return not p and not q
    return (np.abs(np.subtract(p['loc'], q['loc'])).max() < POSE_MM
            and np.abs(np.subtract(p['rot'], q['rot'])).max() < POSE_DEG * 10)


def _bary_extend(Q, Vr, T, Xr):
    """dress._bary_map, but a point outside the reference piece is carried by its nearest triangle's plane (not
    clamped onto the old outline): a pattern made larger grows on its placed shape too (user 2026-10-26)."""
    from mathutils.kdtree import KDTree
    Q = np.asarray(Q, np.float64)
    A, B, C = Vr[T[:, 0]], Vr[T[:, 1]], Vr[T[:, 2]]
    cen = (A + B + C) / 3.0
    k = min(16, len(T))
    tree = KDTree(len(T))
    for i, c in enumerate(cen):
        tree.insert((float(c[0]), float(c[1]), 0.0), i)
    tree.balance()
    cand = np.array([[i for _co, i, _d in tree.find_n((float(q[0]), float(q[1]), 0.0), k)] for q in Q], np.int64)
    a, v0, v1 = A[cand], B[cand] - A[cand], C[cand] - A[cand]
    v2 = Q[:, None, :] - a
    d00 = (v0 * v0).sum(-1); d01 = (v0 * v1).sum(-1); d11 = (v1 * v1).sum(-1)
    d20 = (v2 * v0).sum(-1); d21 = (v2 * v1).sum(-1)
    den = d00 * d11 - d01 * d01
    with np.errstate(divide='ignore', invalid='ignore'):
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
    u = 1 - v - w
    worst = np.nan_to_num(np.minimum(np.minimum(u, v), w), nan=-np.inf)
    j = np.argmax(worst, 1)
    r = np.arange(len(Q))
    W = np.stack([u[r, j], v[r, j], w[r, j]], 1)
    t = cand[r, j]
    return W[:, 0:1] * Xr[T[t, 0]] + W[:, 1:2] * Xr[T[t, 1]] + W[:, 2:3] * Xr[T[t, 2]]


def remesh(ob, V, T, extend=False):
    """Give a placed object the pattern's new mesh (flat V, triangles T), its shape carried over through its layout
    points: its vertices and where they were made, so a move as a whole, a Part to Seam or a fold stays (user
    2026-10-22: a fold line drawn on a placket after Part to Seam, then Fold said the pattern changed). Not for an
    object edited vertex by vertex (has_edits). extend: the pattern itself was edited (draft_tool.refresh_placed): a
    part outside the old outline is carried by the nearest triangle's plane, not clamped onto the old outline."""
    from . import dress as D
    me = ob.data
    n = len(me.vertices)
    fl, gen = me.attributes.get(FLAT_ATTR), me.attributes.get(GEN_ATTR)
    Fp, co, G = np.empty(n * 2), np.empty(n * 3), np.empty(n * 3)
    fl.data.foreach_get('vector', Fp)
    me.vertices.foreach_get('co', co)
    gen.data.foreach_get('vector', G)
    To = np.array([p.vertices[:] for p in me.polygons if len(p.vertices) == 3], np.int64)
    Fp = Fp.reshape(-1, 2)
    V = np.asarray(V, float)
    changed = (Fp.shape != V.shape or not np.allclose(Fp, V, atol=1e-6, rtol=0)
               or not np.array_equal(To, np.asarray(T)))
    if changed:
        # Preserve the backup, but never install old topology as the edited pattern.
        for saved in bpy.data.meshes:
            if saved.get(BEFORE_SEAM_PROP):
                d = json.loads(saved[BEFORE_SEAM_PROP])
                if d['piece'] == ob.get(PROP) and d['garment'] == ob.get(GARMENT_PROP):
                    d['invalid'] = True
                    saved[BEFORE_SEAM_PROP] = json.dumps(d)
    bm = _bary_extend if extend else D._bary_map
    co2 = bm(V, Fp, To, co.reshape(-1, 3))
    G2 = bm(V, Fp, To, G.reshape(-1, 3))
    new = bpy.data.meshes.new(me.name)
    new.from_pydata(co2.tolist(), [], np.asarray(T, np.int64).tolist())
    new.validate()
    new.shade_smooth()
    new.attributes.new(FLAT_ATTR, 'FLOAT2', 'POINT').data.foreach_set('vector', V.astype(np.float32).ravel())
    new.attributes.new(GEN_ATTR, 'FLOAT_VECTOR', 'POINT').data.foreach_set('vector', G2.astype(np.float32).ravel())
    for a in list(me.attributes):        # a fold's shape before it (place_fold.BEFORE_ATTR) carried too
        if a.name.startswith("ac9_fold_before_") and a.data_type == 'FLOAT_VECTOR' and a.domain == 'POINT':
            B = np.empty(n * 3)
            a.data.foreach_get('vector', B)
            B2 = D._bary_map(V, Fp, To, B.reshape(-1, 3))
            new.attributes.new(a.name, 'FLOAT_VECTOR', 'POINT').data.foreach_set('vector', B2.astype(np.float32).ravel())
    for m in me.materials:
        new.materials.append(m)
    from . import look_apply as LA
    LA.ensure_uv_placed(new)
    ob.data = new
    if me.users == 0:
        bpy.data.meshes.remove(me)
    ob[KEY_PROP] = layout_key(V)
    ob[NV_PROP] = len(new.vertices)
    invalidate_shown()


def has_edits(ob):
    """Were the object's own vertices edited (Edit Mode), beyond a move as a whole?"""
    if ob.get(NV_PROP) is not None and len(ob.data.vertices) != int(ob[NV_PROP]):
        return True
    g = ob.data.attributes.get(GEN_ATTR)
    n = len(ob.data.vertices)
    if g is None or len(g.data) != n or n == 0:
        return True
    co = np.empty(n * 3)
    ob.data.vertices.foreach_get('co', co)
    G = np.empty(n * 3)
    g.data.foreach_get('vector', G)
    # made with its vertices at (where made - its origin); an Unlink side's: the source's mirrored, as its GEN
    loc0 = G.reshape(-1, 3) - origin_mm(ob) * MM
    return bool(np.abs(co.reshape(-1, 3) - loc0).max() > MOVED_TOL)


def sync_pose(context, spec):
    """The placed objects' poses written to the spec's placement (a mirrored pair's object: its twin gets the
    mirror image). Returns the new spec, or None when nothing changed. Called at the start of the AC9 operators
    that read the placement (Dress, Place, Adjust, Center, Move by Hand, the pose fields, Mirror Placement...)."""
    from .core import spec as S
    if not spec or not spec.get('placement'):
        return None
    leave_edit(context)
    pl = spec['placement']
    new = {}
    for ob in {o.name: o for o in objects(spec).values()}.values():
        n = ob.get(PROP)
        if n not in pl or ob.get(LEAD_PROP):    # a follower's pose is its leader's mirrored (below)
            continue
        p = pose_of(ob)
        if not same_pose(p, pl[n].get('pose')):
            new[n] = p
        tw = ob.get(TWIN_PROP) or ob.get(FOLLOWER_PROP)
        if tw and tw in pl:
            pm = S.mirror_pose(p, S.pair_plane(spec, n))
            if not same_pose(pm, pl[tw].get('pose')):
                new[tw] = pm
    if not new:
        return None
    sp = S.loads(S.dumps(spec))
    for n, p in new.items():
        if p:
            sp['placement'][n]['pose'] = p
        else:
            sp['placement'][n].pop('pose', None)
    return sp


def _mesh(name, flat, tris, X_mm, origin_mm):
    me = bpy.data.meshes.new(name)
    V = (np.asarray(X_mm, float) - origin_mm) * MM
    me.from_pydata(V.tolist(), [], np.asarray(tris, np.int64).tolist())
    me.validate()
    me.shade_smooth()           # drawn smooth like the dressed garment (user 2026-10-15)
    a = me.attributes.new(FLAT_ATTR, 'FLOAT2', 'POINT')
    a.data.foreach_set('vector', np.asarray(flat, np.float32).ravel())
    g = me.attributes.new(GEN_ATTR, 'FLOAT_VECTOR', 'POINT')
    g.data.foreach_set('vector', (np.asarray(X_mm, float) * MM).astype(np.float32).ravel())
    from . import look_apply as LA                # the UV as the garment's: a look's texture in the same place
    LA.ensure_uv_placed(me)
    return me


def build(context, spec, pat, tris, X, pairs, keep_moved=True, only=None):
    """Make the objects from a start shape (dress.prepare(..., shapes_only=True)): X per piece (world mm), pairs =
    [(src, tgt)] piece indices mirrored (tgt made by the Mirror modifier). Moved objects are kept (keep_moved) and
    their pieces skipped. A piece with a pose in the spec (moved by hand before) is made moved by it. Returns (made, kept, notes): notes = the sides of pairs shown as two
    objects that were placed apart and now follow their pair mirrored (link_pairs, 2026-11-04). only: these pieces (with their pairs) alone,
    the other pieces' objects left as they are (a dressed garment with a few pieces Reset, user 2026-10-20)."""
    from .core import spec as S
    coll = _collection(context)
    cen = center_empty(context, spec)
    names = [str(n) for n in pat['names']]
    poses = {n: p['pose'] for n, p in S.mirror_twins(spec)[0].items() if p.get('pose')}
    old = objects(spec)
    if only is not None:
        only = set(only)
        only |= {names[t_] for s_, t_ in pairs if names[s_] in only} | {names[s_] for s_, t_ in pairs if names[t_] in only}
        old = {n: ob for n, ob in old.items() if n in only}
    kept = set()
    for n, ob in old.items():
        if keep_moved and is_moved(ob):
            kept.add(n)
    back = _kept(spec)                          # shapes a Dress kept (keep), given back to the pieces made
    remove([n for n in old if n not in kept], spec)
    tgt_of = {}
    for s_, t_ in pairs:
        if only is not None and names[s_] not in only:
            continue
        if (names[t_] in kept or names[t_] in back) and not (names[s_] in kept or names[s_] in back):
            s_, t_ = t_, s_                     # the side moved by hand leads
        tgt_of[s_] = t_
    tgts = set(tgt_of.values())
    from .core import spec as S_
    for s_, t_ in tgt_of.items():
        ob = objects(spec).get(names[s_])
        if names[s_] in kept and ob is not None and not ob.get(TWIN_PROP):
            other = objects(spec).get(names[t_])
            if other is not None and other is not ob:
                remove([names[t_]], spec)
            _add_mirror(ob, center_empty(context, spec, names[s_]), names[t_], S_.pair_plane(spec, names[s_]))
        elif names[s_] in kept and ob is not None:
            set_mirror_plane(ob, S_.pair_plane(spec, names[s_]))      # kept, its pair's plane changed since
            for md in ob.modifiers:
                if md.type == 'MIRROR' and md.name == "AC9 Pair":
                    md.mirror_object = center_empty(context, spec, names[s_])      # ... or its plane's place
    made = []
    from . import placement as PL_
    body_fp = PL_.body_fingerprint(context)
    for i, n in enumerate(names):
        if i in tgts or n in kept or (only is not None and n not in only):
            continue
        Xi = np.asarray(X[i], float)
        org = 0.5 * (Xi.min(0) + Xi.max(0))
        ob = bpy.data.objects.new(PREFIX + n, _mesh(PREFIX + n, pat[f"V{i}"], tris[i], Xi, org))
        # matrix_world, not location only: it stays the identity until the depsgraph runs, and a shape read at once
        # (a Reset after the Dress removed the objects, dress.write_placed) came out moved by -org
        ob.matrix_world = Matrix.Translation(tuple(org * MM))
        _tag(ob, spec, n, pat[f"V{i}"])
        ob[BODY_PROP] = body_fp
        from . import look_apply as LA
        LA.apply_piece(ob, spec, n)
        # the pieces' generated positions were written in world coordinates; the object's matrix is a translation, so
        # the comparison in is_moved holds from the start -- unless it has a pose: then it starts moved
        if n in poses:
            ob.matrix_world = pose_matrix(poses[n], org)
        if n in back and back[n].get(KEEP_PROP):
            _give_back(ob, back[n])
            LA.apply_piece(ob, spec, n)
        if i in tgt_of:
            _add_mirror(ob, center_empty(context, spec, n), names[tgt_of[i]], S_.pair_plane(spec, n))
        ob.color = (0.55, 0.75, 1.0, 1.0)
        ob.pop('ac9_problems', None)        # diagnose.PROP: a placed piece made again starts with none
        coll.objects.link(ob)
        made.append(n)
    # a pair shown as two objects: the second follows the first mirrored; notes = followers placed apart by hand
    # before, which go to their pair's mirror image (design review 11/04: told, not moved silently)
    notes = link_pairs(context, spec)
    from . import display_state
    display_state.apply(context, spec, allow_split=True)
    return made, sorted(kept), notes


# ---------------------------------------------------------------- a pair not mirror-meshed as one (user 2026-11-04 b-1)
# A pair whose two meshes are not mirror images (a sleeve meshed 512 / 516 vertices) is two objects; the second
# follows the first mirrored: parented to an empty whose matrix is the x mirror at the center, with a Copy Transforms
# of the leader in its own (local) space, so its world matrix is Mirror @ leader and its vertices are kept in that
# frame. G / R / the Gizmo move the leader and the follower moves at once, mirrored (one undo step). Its world
# vertices stay where they were, so everything reading matrix_world @ co reads it as before.

def _mirror_empty(context, spec, plane='x', piece=None):
    """The empty a follower is parented to: the mirror in x (or, front / back, in y) about the center (the pair of
    `piece`: about its own plane position when it has one)."""
    from .core import spec as S
    cen = center_empty(context, spec, piece)
    key = _pair_key(spec, piece) if S.stored_mirror_at(spec, piece) is not None and piece else None
    name = MIRROR_NAME + ("_Y" if plane == 'y' else "") + (":" + key if key else "")
    ob = bpy.data.objects.get(name)
    if ob is None:
        ob = bpy.data.objects.new(name, None)
        ob.empty_display_size = 0.05
        ob.hide_select = True
        ob.hide_viewport = True
        _collection(context).objects.link(ob)
    C = Matrix.Translation(cen.location)
    ob.matrix_world = C @ Matrix.Diagonal((*S.mirror_diag(plane), 1.0)) @ C.inverted()
    return ob


def _world_co(ob):
    me = ob.data
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get('co', co)
    return (np.c_[co.reshape(-1, 3), np.ones(len(me.vertices))] @ np.array(ob.matrix_world).T)[:, :3]


def _set_world_co(ob, W, M):
    """The object's vertices put at world W (m) under matrix M (its world matrix from now on)."""
    Mi = np.array(M.inverted())
    ob.data.vertices.foreach_set('co', (np.c_[W, np.ones(len(W))] @ Mi.T)[:, :3].astype(np.float32).ravel())
    ob.data.update()


def set_matrix(ob, M):
    """Put the object at world matrix M; a follower is put there by moving its leader (it copies the leader)."""
    c = ob.constraints.get('AC9_Pair')
    # the follower's own mirror (front / back, or a pair's own plane), not the shared x one
    E = ob.parent if ob.parent is not None and ob.parent.name.startswith(MIRROR_NAME) else bpy.data.objects.get(MIRROR_NAME)
    if c is not None and c.target is not None and E is not None:
        c.target.matrix_world = E.matrix_world @ M      # follower = Mirror @ leader, Mirror its own inverse
    else:
        ob.matrix_world = M
    bpy.context.view_layer.update()


def _mirror_gap(lead, ob, plane='x'):
    """(mm, A): how far (the worst vertex) ob is from where its leader's pose mirrored puts it, and the world matrix
    A taking it there."""
    from .core import spec as S
    pl, po = pose_of(lead), pose_of(ob)
    org = origin_mm(ob) - np.asarray(ob.get(ORIGIN_SHIFT_PROP, (0.0, 0.0, 0.0)), float) * 1000.0
    A = np.array(pose_matrix(S.mirror_pose(pl, plane), org) @ pose_matrix(po, org).inverted())
    Wm = _world_co(ob)
    Wn = (np.c_[Wm, np.ones(len(Wm))] @ A.T)[:, :3]
    return float(np.linalg.norm(Wn - Wm, axis=1).max()) * 1000.0, Matrix(A.tolist())


def link(context, spec, lead, ob):
    """Make ob (a pair's side not mirror-meshed) follow lead mirrored, its world shape kept."""
    if ob.mode == 'EDIT':
        ob.update_from_editmode()
    W = _world_co(ob)
    from .core import spec as S
    plane = S.pair_plane(spec, ob.get(PROP, ''))
    E = _mirror_empty(context, spec, plane, ob.get(PROP))
    if plane == 'y':
        ob[PLANE_PROP] = 'y'
    else:
        ob.pop(PLANE_PROP, None)
    ob.parent = E
    ob.matrix_parent_inverse = Matrix.Identity(4)
    ob.matrix_basis = Matrix.Identity(4)
    c = ob.constraints.get('AC9_Pair') or ob.constraints.new('COPY_TRANSFORMS')
    c.name = 'AC9_Pair'
    c.target = lead
    c.target_space, c.owner_space = 'WORLD', 'LOCAL'
    _set_world_co(ob, W, E.matrix_world @ lead.matrix_world)
    ob[LEAD_PROP] = lead[PROP]
    lead[FOLLOWER_PROP] = ob[PROP]


def make_lead(context, spec, name):
    """The side `name` of a linked pair made the one moved, its partner following it mirrored; both shapes stay where
    they are. Returns its object."""
    obs = objects(spec)
    ob = obs.get(name)
    if ob is None or not ob.get(LEAD_PROP):
        return ob
    c = ob.constraints.get('AC9_Pair')
    lead = c.target if c is not None else obs.get(ob[LEAD_PROP])
    unlink(ob)
    if lead is not None and lead is not ob:
        context.view_layer.update()
        link(context, spec, ob, lead)
    context.view_layer.update()
    return ob


def unlink(ob):
    """ob follows no one any more, its world shape kept (a rigid matrix again: the leader's move mirrored)."""
    c = ob.constraints.get('AC9_Pair')
    if not ob.get(LEAD_PROP) and c is None:
        return
    if ob.mode == 'EDIT':
        ob.update_from_editmode()
    W = _world_co(ob)
    lead = c.target if c is not None else None
    if c is not None:
        ob.constraints.remove(c)
    if lead is not None and lead.get(FOLLOWER_PROP) == ob.get(PROP):
        del lead[FOLLOWER_PROP]
    ob.parent = None
    ob.matrix_world = Matrix.Identity(4)
    # the matrix its pose in the spec gives (its leader's mirrored), so pose_of reads that pose from it again
    from .core import spec as S
    M = matrix_for_pose(ob, S.mirror_pose(pose_of(lead), ob.get(PLANE_PROP, 'x')) if lead is not None else None)
    ob.matrix_world = M
    _set_world_co(ob, W, M)
    ob.pop(LEAD_PROP, None)


def link_pairs(context, spec):
    """Every linked pair shown as two objects made one: the second follows the first mirrored (the side with a pose
    leads, else the primary). A pair Unlinked is two independent objects."""
    from .core import spec as S
    obs = objects(spec)
    linked = set()
    notes = []
    pl = spec.get('placement', {})
    for a, b in S.placement_pairs(spec):
        oa, ob_ = obs.get(a), obs.get(b)
        if oa is None or ob_ is None or oa is ob_:
            continue
        linked |= {oa.name, ob_.name}
        if any(f.get(LEAD_PROP) == l_.get(PROP) and f.constraints.get('AC9_Pair')
               and f.constraints['AC9_Pair'].target is l_ for l_, f in ((oa, ob_), (ob_, oa))):
            continue
        lead, fol = (ob_, oa) if pl.get(b, {}).get('pose') and not pl.get(a, {}).get('pose') else (oa, ob_)
        unlink(lead)
        unlink(fol)
        context.view_layer.update()
        gap, A = _mirror_gap(lead, fol, S.pair_plane(spec, a))
        if gap > 1.0:                       # placed apart by hand before: it goes to its pair's mirror now (told)
            fol.matrix_world = A @ fol.matrix_world
            context.view_layer.update()
            notes.append(f"{fol[PROP]} ({gap:.0f} mm)")
        link(context, spec, lead, fol)
    for o in set(obs.values()):
        if o.name not in linked and o.get(LEAD_PROP):
            unlink(o)                       # Unlinked since
    context.view_layer.update()
    return notes


def center(ob, spec):
    """Move a single (unpaired) placed object along x so its shape's middle is on the center plane."""
    me = ob.data
    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get('co', co)
    W = (np.c_[co.reshape(-1, 3), np.ones(n)] @ np.array(ob.matrix_world).T)[:, :3]
    mid = 0.5 * (W[:, 0].min() + W[:, 0].max())
    ob.matrix_world = Matrix.Translation((center_x(spec) * MM - mid, 0.0, 0.0)) @ ob.matrix_world


# ---------------------------------------------------------------- the placed shapes as drawn (seams, rims, clicks)
# User 2026-10-14 (DECISIONS cloth-sim-3d-piece-move-2026-10-14): the seam ladders, the selected rim and the state
# colors of a piece shown by a placed object are drawn on the object as it is now (moved, rotated, edited, a pair's
# mirrored half), so the seams follow the piece being moved. Read from the evaluated mesh (the Mirror modifier's
# half, Edit Mode's edits); all coordinates participate in the update signature so local edits cannot be missed.

_ev = {}            # object name -> {'sig', 'W' (all evaluated vertices, world m), 'tkey', 'F', 'T', 'n'}
_bary = {}          # (piece, tkey, points) -> (triangle per point, barycentric weights)


def _connectivity_key(me):
    """Read connectivity in bulk without retriangulating an unchanged mesh."""
    loops = np.empty(len(me.loops), np.int32)
    sizes = np.empty(len(me.polygons), np.int32)
    me.loops.foreach_get('vertex_index', loops)
    me.polygons.foreach_get('loop_total', sizes)
    return (len(me.vertices), hash(loops.tobytes()), hash(sizes.tobytes())), bool((sizes > 3).any())


def follow_layout(spec, rigid):
    """Pieces moved whole in the layout (Whole Pieces' G / R): the layout points their placed objects hold
    (FLAT_ATTR) moved alike, the 3D shape left as it is. They kept the old points: the point clicked on a placed
    piece was shown where the piece had been in 2D, and a Dress mapped a hand-moved shape through the old layout.
    rigid: {piece: (R, t)}, new = R @ old + t; a map new -> old (a dart's turned part) is not invertible here and is
    skipped (the cloth outside a dart does not move). Returns the pieces whose objects were updated."""
    done = []
    obs = objects(spec)
    for n, r in (rigid or {}).items():
        ob = obs.get(n)
        if ob is None or ob.get(PROP) != n or callable(r) or ob.type != 'MESH':
            continue                     # a pair's mirrored half reads its points from the source's, mirrored
        R, t = (np.asarray(x, float) for x in r)
        if np.allclose(R, np.eye(2)) and np.allclose(t, 0.0):
            continue
        a = ob.data.attributes.get(FLAT_ATTR)
        nv = len(ob.data.vertices)
        if a is None or nv == 0 or len(a.data) != nv:
            continue
        F = np.empty(nv * 2)
        a.data.foreach_get('vector', F)
        F = F.reshape(-1, 2) @ R.T + t
        a.data.foreach_set('vector', F.astype(np.float32).ravel())
        ob.data.update()
        _ev.pop(ob.name, None)
        done.append(n)
    return done


def mirror_flat(F, B_src, B_twin, plane='x'):
    """Layout points of a source piece mirrored into its twin's outline (as core/pattern_build.py mirrors a twin):
    across x for a left / right pair, across y for a front / back one."""
    ca, cb = 0.5 * (B_src.min(0) + B_src.max(0)), 0.5 * (B_twin.min(0) + B_twin.max(0))
    F = np.asarray(F, float) - ca
    F[:, 1 if plane == 'y' else 0] *= -1.0
    return F + cb


def _read(context, ob, dg):
    ev = ob.evaluated_get(dg)
    me = ev.data
    nE = len(me.vertices)
    edit = nE == 0 and ob.mode == 'EDIT'
    if edit:
        # in Edit Mode the evaluated mesh keeps its vertices in the edit mesh (none in .vertices, measured in 5.0):
        # to_mesh() makes them (the edits so far included), read whole each redraw (a placed piece is a few
        # thousand vertices)
        me = ev.to_mesh()
        nE = len(me.vertices)
    try:
        if nE == 0:
            return None
        co = np.empty(nE * 3, np.float32)
        me.vertices.foreach_get('co', co)
        connectivity, ngons = _connectivity_key(me)
        coordinate_key = hash(co.tobytes())
        fl = me.attributes.get(FLAT_ATTR)
        flat = np.zeros(nE * 2, np.float32)
        if fl is not None and len(fl.data) == nE:
            fl.data.foreach_get('vector', flat)
        twin = bool(ob.get(TWIN_PROP)) and any(m.type == 'MIRROR' and m.show_viewport for m in ob.modifiers)
        tkey = (ob.as_pointer(), ob.data.as_pointer(), nE, len(me.polygons), ob.get(KEY_PROP),
                ob.get(TWIN_PROP, ''), twin, connectivity, hash(flat.tobytes()),
                coordinate_key if ngons else None)
        sig = (tkey, np.array(ob.matrix_world).tobytes(), coordinate_key)
        c = _ev.get(ob.name)
        if c is not None and c['sig'] == sig:
            return c
        Mw = np.array(ob.matrix_world)
        W = co.reshape(-1, 3).astype(np.float64) @ Mw[:3, :3].T + Mw[:3, 3]
        if c is None or c.get('tkey') != tkey:
            me.calc_loop_triangles()
            triangles = np.empty(len(me.loop_triangles) * 3, np.int32)
            me.loop_triangles.foreach_get('vertices', triangles)
            F, T = flat.reshape(-1, 2).astype(np.float64), triangles.reshape(-1, 3)
            n_own = nE // 2 if twin else nE
            T = T[(T < n_own).all(1)]               # the object's own half (the Mirror appends the mirrored copy)
            c = {'tkey': tkey, 'F': F[:n_own], 'T': T, 'n': n_own, 'twin': twin}
            _ev[ob.name] = c
        c.update(sig=sig, W=W)
        return c
    finally:
        if edit:
            ev.to_mesh_clear()


def reveal(context, spec, obs):
    """Show the objects obs: the user's own hiding (H, the Outliner's eye, the screen icon, the exclude box) undone --
    hide_set alone left a hidden AC9_ClothSim_Placed hidden, and the seam lines stayed on the result (user 2026-10-14).
    Whether each object is shown is then the display plan's (display_state.apply, the one writer): an Off piece's
    placed shape stays hidden under its dashed outline. Returns the collections made visible."""
    def find(lc, coll):
        if lc.collection == coll:
            return [lc]
        for c in lc.children:
            p = find(c, coll)
            if p:
                return [lc] + p
        return []
    root = context.view_layer.layer_collection
    opened = []
    for ob in obs:
        ob.hide_viewport = False
        if ob.get('ac9_display_hidden'):
            ob['ac9_display_was_hidden'] = False     # shown when the plan lets it, not back to the user's hiding
        else:
            ob.hide_set(False)
        for coll in ob.users_collection:
            for lc in find(root, coll)[1:]:          # the path from the scene's collection down to coll
                if lc.exclude or lc.hide_viewport or lc.collection.hide_viewport:
                    opened.append(lc.collection.name)
                lc.exclude = False
                lc.hide_viewport = False
                lc.collection.hide_viewport = False
    from . import display_state
    display_state.apply(context, spec)
    return sorted(set(opened))


def any_seen(spec):
    """A placed object of the garment is seen in the viewport: the 3D seams and tints go on the placed pieces then,
    not on the result (moving them is what the seams are for; a Dress hides them, user 2026-10-14 b-3)."""
    return any(o.visible_get() for o in set(objects(spec).values()))


def shown(context, spec, outlines, include_off=False):
    """{piece: (layout mm (n, 2), triangles (m, 3), world m (n, 3), topology key, shape key)} for every piece a placed object
    shows (a pair's mirrored piece: the Mirror modifier's half, its layout points the source's mirrored into its own
    outline), and a signature that changes when any of them moved or changed. Hidden objects are left out (a
    Reset of some pieces shows their placed objects only: the others' seams and tints are not drawn in the air)."""
    dg = context.evaluated_depsgraph_get()
    out, sig = {}, []
    for ob in {o.name: o for o in objects(spec).values()}.values():
        try:
            if not ob.visible_get() and not (include_off and ob.get('ac9_display_hidden')
                                            and not ob.get('ac9_display_was_hidden')):
                continue
        except ReferenceError:
            continue
        try:
            c = _read(context, ob, dg)
        except (ReferenceError, RuntimeError):
            c = None
        if c is None:
            continue
        sig.append((ob.name, c['sig']))
        n = ob[PROP]
        n_ = c['n']
        out[n] = (c['F'], c['T'], c['W'][:n_], (ob.name, c['tkey']), c['sig'])
        tw = ob.get(TWIN_PROP)
        if c['twin'] and tw and outlines is not None and n in outlines and tw in outlines and len(c['W']) >= 2 * n_:
            if c.get('ftw_key') != (c['tkey'], store_key()):
                c['Ft'] = mirror_flat(c['F'], outlines[n].B, outlines[tw].B, outlines[n].plane)
                c['ftw_key'] = (c['tkey'], store_key())
            out[tw] = (c['Ft'], c['T'], c['W'][n_:2 * n_], (ob.name, c['tkey'], 'twin'), c['sig'])
    return out, tuple(sorted(sig))


def store_key():
    from . import store
    return store._cache['key']


def to_world(entry, P):
    """World (m) points of layout points P (mm, on the piece) on a shown piece: each in the layout triangle it lies
    in (the nearest, its weights clamped, for a point just off the meshed outline), carried by the same weights."""
    F, T, W, tkey = entry[:4]
    P = np.ascontiguousarray(np.asarray(P, float))
    if not len(T) or not len(P):
        return None
    k = (tkey, len(P), hash(P.tobytes()))
    b = _bary.get(k)
    if b is None:
        A, B_, C = F[T[:, 0]], F[T[:, 1]], F[T[:, 2]]
        cen = (A + B_ + C) / 3.0
        m = min(8, len(T))
        ti = np.empty(len(P), np.int64)
        wt = np.empty((len(P), 3))
        for s in range(0, len(P), 256):
            Q = P[s:s + 256]
            d = ((Q[:, None, :] - cen[None]) ** 2).sum(2)
            cand = np.argpartition(d, m - 1, axis=1)[:, :m]            # (q, m) nearest triangles
            a, bb, cc = A[cand], B_[cand], C[cand]
            v0, v1, v2 = bb - a, cc - a, Q[:, None, :] - a
            den = v0[..., 0] * v1[..., 1] - v1[..., 0] * v0[..., 1]
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            w1 = (v2[..., 0] * v1[..., 1] - v1[..., 0] * v2[..., 1]) / den
            w2 = (v0[..., 0] * v2[..., 1] - v2[..., 0] * v0[..., 1]) / den
            w0 = 1.0 - w1 - w2
            Wc = np.stack([w0, w1, w2], -1)
            best = np.argmax(Wc.min(-1), 1)                          # the triangle the point is most inside
            r = np.arange(len(Q))
            w = np.clip(Wc[r, best], 0.0, None)
            w /= np.maximum(w.sum(1, keepdims=True), 1e-12)
            ti[s:s + 256], wt[s:s + 256] = cand[r, best], w
        if len(_bary) > 4096:
            _bary.clear()
        b = _bary[k] = (ti, wt)
    ti, wt = b
    tr = T[ti]
    return (W[tr] * wt[:, :, None]).sum(1)


def invalidate_shown():
    _ev.clear()
    _bary.clear()
