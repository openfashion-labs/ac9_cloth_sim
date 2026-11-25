# SPDX-License-Identifier: GPL-3.0-or-later
"""The flat pattern in the viewport: one mesh object per piece, children of one layout Empty.

The Empty maps the spec's layout (mm) to the scene: a piece point (x, y) mm sits at
Empty.matrix_world @ (x / 1000, y / 1000, 0). Move or rotate the Empty to put the pattern
where it is convenient; the overlay and the seam tools follow it.
Disabled pieces are shown as wire.
"""
import bpy
import numpy as np
from collections import Counter
from mathutils import Matrix, Vector

from .core import spec as S

LAYOUT_NAME = "AC9_ClothSim_Layout"
COLL_NAME = "AC9_ClothSim_Pattern"
PIECE_PROP = "ac9_piece"
MM = 0.001


def layout_empty(context, create=False):
    ob = bpy.data.objects.get(LAYOUT_NAME)
    if ob is None and create:
        ob = bpy.data.objects.new(LAYOUT_NAME, None)
        ob.empty_display_type = 'PLAIN_AXES'
        ob.empty_display_size = 0.1
        _collection(context).objects.link(ob)
    return ob


def _collection(context):
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is not None and not _in_scene(context, coll):
        from .i18n import tx
        raise PatternIntegrityError(tx("Pattern collection belongs to another scene: {0}", coll.name))
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
        context.scene.collection.children.link(coll)
    return coll


def layout_matrix(context):
    ob = layout_empty(context)
    return ob.matrix_world.copy() if ob else Matrix.Identity(4)


def to_world(M, P):
    """(n, 2) mm -> list of world Vectors."""
    return [M @ Vector((float(x) * MM, float(y) * MM, 0.0)) for x, y in P]


OWNER_PROP = 'ac9_pattern_spec'


def _in_scene(context, coll):
    pending = [context.scene.collection]
    seen = set()
    while pending:
        parent = pending.pop()
        if parent == coll:
            return True
        if parent.as_pointer() not in seen:
            seen.add(parent.as_pointer())
            pending.extend(parent.children)
    return False


def _groups(context=None):
    """Enumerate before resolving: copying a Blender object also copies its piece ID."""
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        return {}
    context = context or bpy.context
    if not _in_scene(context, coll):
        return {}
    groups = {}
    for ob in coll.objects:
        if PIECE_PROP in ob and ob.name in context.scene.objects:
            groups.setdefault(ob[PIECE_PROP], []).append(ob)
    return groups


def piece_objects():
    """Only unambiguous mesh representatives. Never choose a duplicate by collection order."""
    props = getattr(bpy.context.scene, 'ac9_cloth_sim', None)
    owner = props.spec_text if props is not None else None
    return {name: obs[0] for name, obs in _groups().items()
            if len(obs) == 1 and obs[0].type == 'MESH'
            and (OWNER_PROP not in obs[0] or obs[0].get(OWNER_PROP) == owner)}


class PatternIntegrityError(ValueError):
    pass


ISSUES = ('duplicates', 'missing', 'unexpected', 'foreign', 'invalid', 'repeated', 'other_scene')


def require_piece_ids(spec):
    from .i18n import tx
    names = [p['name'] for p in spec['pieces']]
    repeated = [n for n, count in Counter(names).items() if count > 1]
    if repeated:
        raise PatternIntegrityError(tx("Duplicate pattern IDs in document: {0}", ', '.join(repeated)))


def integrity(context, spec):
    """Read-only, identity-only check. Hidden/Off pieces count; no vertices or JSON are read here.

    Legacy objects without an owner remain valid when unique. An explicit different owner never does.
    The fixed pattern collection represents the current document, not a collection of other garments.
    """
    from . import store
    expected = [pc['name'] for pc in spec['pieces']] if spec is not None else []
    owner = store.text(context)
    groups = _groups(context)
    coll = bpy.data.collections.get(COLL_NAME)
    other_scene = (coll.name,) if coll is not None and not _in_scene(context, coll) else ()
    duplicate = {n: tuple(o.name for o in obs) for n, obs in groups.items() if len(obs) > 1}
    foreign = tuple(o.name for obs in groups.values() for o in obs
                    if OWNER_PROP in o and o.get(OWNER_PROP) != owner)
    invalid = tuple(o.name for obs in groups.values() for o in obs if o.type != 'MESH')
    unexpected = tuple(n for n in groups if n not in expected)
    missing = tuple(n for n in expected if n not in groups)
    repeated = tuple(n for n, count in Counter(expected).items() if count > 1)
    return dict(duplicates=duplicate, missing=missing, unexpected=unexpected, foreign=foreign,
                invalid=invalid, repeated=repeated, other_scene=other_scene,
                ok=not (duplicate or missing or unexpected or foreign or invalid or repeated or other_scene))


def integrity_lines(check):
    """Same translated explanation for panels and refused operations."""
    from .i18n import tx
    lines = []
    for n, obs in check['duplicates'].items():
        lines.append(tx("Duplicate pattern {0}: {1}", n, ', '.join(obs)))
    for key, msg in (('missing', "Missing pattern objects: {0}"),
                     ('unexpected', "Objects without a pattern: {0}"),
                     ('foreign', "Objects from another pattern document: {0}"),
                     ('invalid', "Invalid pattern objects: {0}"),
                     ('repeated', "Duplicate pattern IDs in document: {0}"),
                     ('other_scene', "Pattern collection belongs to another scene: {0}")):
        if check[key]:
            lines.append(tx(msg, ', '.join(check[key])))
    return lines


def require_integrity(context, spec=None, *, allow_missing=False, allow_rebuild=False):
    from . import store
    from .i18n import tx
    if spec is None:
        spec = store.get(context)[0]
    if spec is None:
        return
    check = integrity(context, spec)
    if any(check[k] for k in ISSUES if not ((allow_missing and k == 'missing')
                                          or (allow_rebuild and k in ('missing', 'unexpected')))):
        raise PatternIntegrityError(tx("Pattern objects are inconsistent; no edit was made. {0}",
                                       '; '.join(integrity_lines(check))))


RECOVERY_PROP = 'ac9_recovered_piece'
RECOVERY_COLLECTION = 'ac9_pattern_recovery'


def restore_plan(context, spec, *, verify_legacy=True):
    """Read-only restoration scope shared by the warning and the operation. Never infer a winning duplicate."""
    from .i18n import tx
    from types import SimpleNamespace
    check = integrity(context, spec)
    if any(check[k] for k in ('foreign', 'invalid', 'repeated', 'other_scene')):
        return dict(names=(), archive=(), reason='; '.join(integrity_lines(check)))
    groups = _groups(context)
    names = tuple(p['name'] for p in spec['pieces']
                  if p['name'] in check['missing'] or p['name'] in check['duplicates'])
    archive = tuple(ob for name, obs in groups.items()
                    if name in check['duplicates'] or name in check['unexpected'] for ob in obs)
    pattern = bpy.data.collections.get(COLL_NAME)
    if pattern is not None and (pattern.library is not None or pattern.override_library is not None
            or any(sc != context.scene and _in_scene(SimpleNamespace(scene=sc), pattern) for sc in bpy.data.scenes)):
        return dict(names=(), archive=(), reason=tx("Restore cannot change a shared or linked pattern collection"))
    for ob in archive:
        if (ob.library is not None or ob.override_library is not None or RECOVERY_PROP in ob
                or len(ob.users_collection) != 1 or ob.users_collection[0] != pattern
                or any(sc != context.scene and ob.name in sc.objects for sc in bpy.data.scenes)):
            return dict(names=(), archive=(), reason=tx("Restore cannot isolate shared or linked object: {0}", ob.name))
    if verify_legacy:
        # Full Text reads belong at the operation boundary, never in Panel.draw. An old unqualified object
        # cannot be claimed when another saved document has the same logical ID.
        from . import store, dress
        legacy = {ob[PIECE_PROP] for ob in archive if OWNER_PROP not in ob}
        if legacy:
            owner = store.text(context)
            for text, other in dress.garment_records():
                if text == owner:
                    continue
                ids = {p['name'] for p in other['pieces']}
                if legacy & ids:
                    return dict(names=(), archive=(), reason=tx("Legacy pattern ownership is ambiguous: {0}", ', '.join(sorted(legacy & ids))))
    return dict(names=names, archive=archive, reason='')


def restore(context, spec, outlines):
    """Stage replacements before retiring any display. Keep originals for Undo and manual recovery."""
    from . import store, selection as SEL
    from .i18n import tx
    from types import SimpleNamespace
    selection_before = SEL.capture_flags(context)
    plan = restore_plan(context, spec)
    if plan['reason']:
        raise PatternIntegrityError(plan['reason'])
    if not plan['names'] and not plan['archive']:
        return 0, 0
    owner = store.text(context)
    coll = _collection(context)
    emp = layout_empty(context, create=True)
    created, retired, recovery = [], [], None
    new_recovery = False
    meshes_before = set(bpy.data.meshes)
    try:
        for pc in spec['pieces']:
            if pc['name'] not in plan['names']:
                continue
            mesh = _piece_mesh(pc, outlines[pc['name']])
            ob = bpy.data.objects.new('AC9_Piece_' + pc['name'], mesh)
            created.append(ob)
            ob[PIECE_PROP], ob[OWNER_PROP] = pc['name'], owner
            ob.parent = emp
            _look(ob, spec, pc['name'])
            _show_state(ob, pc)
        if plan['archive']:
            recovery = next((c for c in context.scene.collection.children
                             if c.get(RECOVERY_COLLECTION) == owner and c.hide_viewport and c.hide_render
                             and c.library is None and c.override_library is None
                             and not any(sc != context.scene and _in_scene(SimpleNamespace(scene=sc), c)
                                         for sc in bpy.data.scenes)), None)
            if recovery is None:
                recovery = bpy.data.collections.new('AC9_ClothSim_Recovery')
                recovery[RECOVERY_COLLECTION] = owner
                recovery.hide_viewport = recovery.hide_render = True
                context.scene.collection.children.link(recovery)
                new_recovery = True
            for ob in plan['archive']:
                retired.append((ob, ob[PIECE_PROP]))
                recovery.objects.link(ob)
                ob[RECOVERY_PROP] = ob[PIECE_PROP]
                del ob[PIECE_PROP]
                coll.objects.unlink(ob)
        for ob in created:
            coll.objects.link(ob)
        require_integrity(context, spec)
    except Exception:
        for ob in created:
            bpy.data.objects.remove(ob, do_unlink=True)
        for ob, name in retired:
            if coll.objects.get(ob.name) is None:
                coll.objects.link(ob)
            ob[PIECE_PROP] = name
            ob.pop(RECOVERY_PROP, None)
            if recovery is not None and recovery.objects.get(ob.name) is not None:
                recovery.objects.unlink(ob)
        if new_recovery and recovery is not None:
            bpy.data.collections.remove(recovery)
        for mesh in list(bpy.data.meshes):
            if mesh not in meshes_before and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        context.view_layer.update()
        SEL.restore_flags(context, selection_before)
        raise
    return len(created), len(retired)


_history_checks = {}


def check_history():
    """After load/undo/redo, inspect each scene without changing any saved data."""
    from types import SimpleNamespace
    from .core import spec as S
    _history_checks.clear()
    for scene in bpy.data.scenes:
        props = getattr(scene, 'ac9_cloth_sim', None)
        t = props.spec_text if props is not None else None
        if t is not None:
            try:
                _history_checks[scene.session_uid] = integrity(SimpleNamespace(scene=scene), S.loads(t.as_string()))
            except (ValueError, KeyError, TypeError):
                pass  # store.error reports an unreadable document; do not replace it with an object diagnosis


def lead_piece(context, names=None):
    """The piece the selection is shown and edited by: the active object's when it is a selected pattern piece, else
    the first selected pattern piece (user 2026-10-22: a box select keeps the active object outside it, and the
    piece's panel and buttons were gone for some selections and not others). names: only pieces in it."""
    from . import selection as SEL      # a placed piece's object and a handle on the garment are the piece too
    return SEL.lead(context, names)


def rebuild(context, spec, outlines, *, switch_document=False):
    """Recreate the piece objects from the spec (the layout Empty is kept where the user put it)."""
    # Rebuilding must not silently destroy an ambiguous user copy either.
    require_piece_ids(spec)
    if switch_document:
        # Only New/Import after their operation preflight: replace the previous document's derived display.
        # Reload never gets this permission, so an explicit foreign owner still refuses.
        check = integrity(context, spec)
        if any(check[k] for k in ('duplicates', 'invalid', 'repeated', 'other_scene')):
            require_integrity(context, spec)
    else:
        require_integrity(context, spec, allow_rebuild=True)
    coll = _collection(context)
    first = layout_empty(context) is None
    emp = layout_empty(context, create=True)
    for ob in list(coll.objects):
        if PIECE_PROP in ob:
            me = ob.data
            bpy.data.objects.remove(ob)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
    if first and spec['pieces']:
        allp = np.vstack([outlines[pc['name']].B for pc in spec['pieces']])
        lo, hi = allp.min(0), allp.max(0)
        c = 0.5 * (lo + hi)
        # beside the avatar (which stands at the origin), lying on the floor
        emp.location = (1.0 + 0.5 * (hi[0] - lo[0]) * MM - c[0] * MM, -c[1] * MM, 0.0)
    for pc in spec['pieces']:
        ob = bpy.data.objects.new("AC9_Piece_" + pc['name'], _piece_mesh(pc, outlines[pc['name']]))
        ob[PIECE_PROP] = pc['name']
        from . import store
        text = store.text(context)
        if text is not None:
            ob['ac9_pattern_spec'] = text
        _look(ob, spec, pc['name'])
        ob.parent = emp
        _show_state(ob, pc)
        coll.objects.link(ob)
    from . import view_setup
    view_setup.refresh(context)


def _faces_with_holes(B, rings, base):
    """Triangles filling the outline B (its vertices 0..len(B)-1) round the hole rings (their vertices numbered from
    base on, ring after ring), none inside a hole."""
    from mathutils.geometry import delaunay_2d_cdt
    from .core import pattern_build as PB
    n = len(B)
    P = np.vstack([B] + list(rings))
    E, k = [], n
    for R in rings:
        E += [(k + j, k + (j + 1) % len(R)) for j in range(len(R))]
        k += len(R)
    vo, _, fo, ov, _, _ = delaunay_2d_cdt([Vector(p) for p in P], E, [list(range(n))], 1, 1e-6, True)
    remap = [o[0] if len(o) == 1 else -1 for o in ov]
    T = np.array([[remap[a] for a in f] for f in fo if len(f) == 3 and all(remap[a] >= 0 for a in f)], np.int64)
    T = PB.drop_hole_tris(P, T.reshape(-1, 3), rings)
    return [[int(a) if a < n else int(a) - n + base for a in t] for t in T]


def _piece_mesh(pc, ol):
    V = [(float(x) * MM, float(y) * MM, 0.0) for x, y in ol.B]
    n = len(V)
    E = [(i, (i + 1) % n) for i in range(n)]
    for it in pc.get('internals', []):
        P = np.asarray(it['pts'], float)
        base = len(V)
        V += [(float(x) * MM, float(y) * MM, 0.0) for x, y in P]
        E += [(base + k, base + k + 1) for k in range(len(P) - 1)]
    faces = [list(range(n))]
    rings = [R for _, R in S.holes(pc)]
    if rings:                         # holes cut out (a dart's diamond): triangles round them instead of one n-gon
        faces = _faces_with_holes(ol.B, rings, len(V))
        V += [(float(x) * MM, float(y) * MM, 0.0) for R in rings for x, y in R]
    me = bpy.data.meshes.new("AC9_Piece_" + pc['name'])
    me.from_pydata(V, E, faces)
    me.validate()
    from . import look_apply as LA                # the UV as the garment's: a look's texture in the same place
    LA.ensure_uv_flat(me)
    return me


def update_pieces(context, spec, outlines, names):
    """Remesh the pieces `names` in place (a drafting edit); a piece without an object yet gets one."""
    # Additions may legitimately be missing until this function creates them. All other ambiguities refuse
    # before allocating a mesh, including a lower-level caller outside draft_tool.commit.
    check = integrity(context, spec)
    check['missing'] = tuple(n for n in check['missing'] if n not in names)
    if any(check[k] for k in ISSUES):
        from .i18n import tx
        raise PatternIntegrityError(tx("Pattern objects are inconsistent; no edit was made. {0}",
                                       '; '.join(integrity_lines(check))))
    emp = layout_empty(context, create=True)
    obs = piece_objects()
    coll = _collection(context)
    for pc in spec['pieces']:
        if pc['name'] not in names:
            continue
        me = _piece_mesh(pc, outlines[pc['name']])
        ob = obs.get(pc['name'])
        if ob is None:
            ob = bpy.data.objects.new("AC9_Piece_" + pc['name'], me)
            ob[PIECE_PROP] = pc['name']
            from . import store
            owner = store.text(context)
            if owner is not None:
                ob[OWNER_PROP] = owner
            ob.parent = emp
            coll.objects.link(ob)
        else:
            old = ob.data
            ob.data = me
            if old is not None and old.users == 0:
                bpy.data.meshes.remove(old)
            me.name = "AC9_Piece_" + pc['name']
        _look(ob, spec, pc['name'])
        _show_state(ob, pc)


def _look(ob, spec, name):
    from . import look_apply as LA                # the piece's look (user 2026-10-20: on the flat pattern too)
    LA.apply_piece(ob, spec, name)


OFF_MATERIAL = "AC9_PieceOff"
LOCKS = ('lock_location', 'lock_rotation', 'lock_scale')


def _off_material():
    m = bpy.data.materials.get(OFF_MATERIAL)
    if m is None:
        m = bpy.data.materials.new(OFF_MATERIAL)
        m.diffuse_color = (0.35, 0.35, 0.35, 0.5)
        if hasattr(m, 'blend_method'):
            m.blend_method = 'BLEND'
    return m


def _show_state(ob, pc):
    """A flat piece object as its state shows. Inactive / Off (P1 #9: out of the simulation's contact) are
    gray and see-through but solid: as wire they could be picked only on their lines, not by a click inside or a
    Select Box round them (user 2026-10-21). Locked against Blender's own G / R / S: the pattern is moved by Edit
    Pattern, which writes the pattern; a moved object only moved the picture (Fable review
    REVIEW_fable_tool_modes_2026-10-21 §1)."""
    for a in LOCKS:
        setattr(ob, a, (True, True, True))
    ob.display_type = 'SOLID'
    off = S.piece_state(pc) in ('off_pattern', 'off_all')
    me = ob.data
    if off and not me.materials:
        me.materials.append(None)                       # a slot for the object's own material
    for sl in ob.material_slots:
        if off:
            sl.link = 'OBJECT'
            sl.material = _off_material()
        elif sl.link == 'OBJECT':
            sl.material = None
            sl.link = 'DATA'


def refresh_enabled(spec):
    obs = piece_objects()
    for pc in spec['pieces']:
        ob = obs.get(pc['name'])
        if ob is not None:
            _show_state(ob, pc)
