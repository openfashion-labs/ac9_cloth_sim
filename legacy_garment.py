# SPDX-License-Identifier: GPL-3.0-or-later
"""Qualify unowned legacy results at operation boundaries, never inside readers.

Only a unique complete saved document AND the result's exact mesh layout prove
ownership. Shape coordinates and editing provenance are not changed or inferred.
"""
import json
import zipfile
from collections import Counter

import bpy

from . import garment_owner as GO

_notices = {}
_busy = False
REASONS = {
    'missing': "The original result folder is unavailable",
    'record': "The pattern saved with the result cannot be read",
    'layout': "The result folder does not match the mesh layout",
    'none': "No pattern document matches the saved pattern",
    'many': "Several pattern documents match the saved pattern",
    'shared': "The result is shared or linked; its ownership was kept unchanged",
    'claimed': "The saved ownership references disagree or are invalid",
    'collision': "Several garment results belong to the same pattern document",
}


def _canon(spec):
    return json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _claims(g):
    from . import control_basis as CB
    return [data.get(key) for data, key in
            ((g, GO.GARMENT), (g.data, GO.GARMENT), (g.data, CB.OWNER)) if key in data]


def _layout_matches(g, result):
    import numpy as np
    from . import control_basis as CB
    me, z = g.data, result.index
    ns = CB.names(g)
    if not ns or len(ns) != len(set(ns)) or list(ns) != result.names:
        return False
    F, fp, flat, offs = (np.asarray(z[k]) for k in ('F', 'fp', 'flat', 'offs'))
    if (F.shape != (len(me.polygons), 3) or fp.shape != (len(me.polygons),)
            or flat.shape != (len(me.vertices), 2) or len(me.loops) != 3 * len(me.polygons)
            or not np.issubdtype(F.dtype, np.integer) or not np.issubdtype(fp.dtype, np.integer)
            or not np.isfinite(flat).all() or offs.shape != (len(ns),)
            or not np.issubdtype(offs.dtype, np.integer) or offs[0] != 0
            or np.any(np.diff(offs) <= 0) or offs[-1] >= len(flat)):
        return False
    attr, uv = me.attributes.get('ac9_piece'), me.uv_layers.get('UVMap')
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'INT' or uv is None:
        return False
    lv = np.empty(len(me.loops), np.int32)
    counts = np.empty(len(me.polygons), np.int32)
    face_piece = np.empty(len(me.polygons), np.int32)
    tex = np.empty((len(me.loops), 2), np.float32)
    co = np.empty((len(me.vertices), 3), np.float32)
    me.loops.foreach_get('vertex_index', lv)
    me.polygons.foreach_get('loop_total', counts)
    attr.data.foreach_get('value', face_piece)
    uv.data.foreach_get('uv', tex.ravel())
    me.vertices.foreach_get('co', co.ravel())
    if not (np.all(counts == 3) and np.array_equal(lv, F.ravel())
            and np.array_equal(face_piece, fp) and np.isfinite(co).all()):
        return False
    # Reproduce the original UV writer's float32 encoding; no new distance tolerance.
    if not np.array_equal(tex, (flat[lv] / 1000.).astype(np.float32)):
        return False
    ranges = np.searchsorted(offs, np.arange(len(flat)), side='right') - 1
    return bool(np.array_equal(ranges[F], np.repeat(fp[:, None], 3, axis=1)))


def reconcile(context):
    """Read all candidates before binding any; return newly qualified Object names.

    No folders are written, no mesh/Text is modified, and explicit claims (even
    broken null references) are never reinterpreted as absent legacy metadata.
    """
    global _busy
    from . import dress as D, private_ids, result_reference as RR
    from .core import spec as S
    if _busy or D.AC9_OT_SimDress.running():
        return []
    _busy = True
    try:
        pending = []
        for g in context.scene.objects:
            if g.type != 'MESH' or 'ac9_piece_names' not in g or private_ids.contains(g):
                continue
            claims = _claims(g)
            if not (claims and GO.text(claims[0]) is not None and all(c == claims[0] for c in claims)):
                pending.append(g)
        if not pending:
            _notices[context.scene.session_uid] = ()
            return []
        docs = D.garment_records()
        canonical = {}
        proposals, issues = [], []

        def issue(g, reason, owners):
            issues.append((g, reason, tuple(t for t, _sp in owners)))

        for g in pending:
            claims = _claims(g)
            if claims:
                if not (GO.text(claims[0]) is not None and all(c == claims[0] for c in claims)):
                    issue(g, 'claimed', docs)
                continue
            # Keep the existing single-document legacy contract. The new migration
            # is for results which no document can currently recognize.
            if any(GO.owns(g, GO.GARMENT, sp, owner=t, context=context,
                           legacy_name=sp['name']) for t, sp in docs):
                continue
            owners = [(t, sp) for t, sp in docs if g.name == D.garment_name(sp)]
            if not owners:
                owners = docs
            if (not g.is_editable or not g.data.is_editable or g.library or g.data.library
                    or g.data.users != 1 or sum(g.name in sc.objects for sc in bpy.data.scenes) != 1):
                issue(g, 'shared', owners)
                continue
            try:
                ref = RR.resolve(g)
                if ref is None:
                    issue(g, 'missing', owners)
                    continue
                result = RR.open_result(ref.path)
                raw = result.dressed_spec_text()
                if not raw:
                    issue(g, 'record', owners)
                    continue
                saved = S.loads(raw)
                candidates = [(t, sp) for t, sp in docs if sp['name'] == saved.get('name')]
                # Keep the original result's notification scope even if a wrong
                # reference describes a document which is not present at all.
                owners = list({t: (t, sp) for t, sp in owners + candidates}.values())
                for t, sp in candidates:
                    if t not in canonical:
                        try:
                            canonical[t] = _canon(sp)
                        except (TypeError, ValueError):
                            canonical[t] = None
                saved_key = _canon(saved)
                matches = [(t, sp) for t, sp in candidates if canonical[t] == saved_key]
                if len(matches) != 1:
                    issue(g, 'many' if matches else 'none', owners)
                    continue
                if any(sc != context.scene and sc.ac9_cloth_sim.spec_text == matches[0][0]
                       for sc in bpy.data.scenes):
                    issue(g, 'shared', owners)
                    continue
                if not _layout_matches(g, result):
                    issue(g, 'layout', owners)
                    continue
                proposals.append((g, matches[0][0]))
            except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError,
                    AttributeError, EOFError, zipfile.BadZipFile):
                issue(g, 'record', owners)

        # A first-hit writer would silently claim one of two equally valid results.
        # Count existing owners and all proposals before assigning anything.
        occupied = Counter()
        for g in bpy.data.objects:
            if g.type == 'MESH' and 'ac9_piece_names' in g and not private_ids.contains(g):
                claims = _claims(g)
                for owner in {c for c in claims if GO.text(c) is not None}:
                    occupied[owner] += 1
        totals = occupied + Counter(t for _g, t in proposals)
        bound = []
        for g, owner in proposals:
            if totals[owner] != 1:
                issue(g, 'collision', [(owner, None)])
                continue
            g[GO.GARMENT] = owner
            bound.append(g.name)
        _notices[context.scene.session_uid] = tuple(issues)
        return bound
    finally:
        _busy = False


def notices(context):
    """Cached operation result only: no parsing, disk reads, or ownership writes."""
    from . import store
    current = store.text(context)
    out = []
    for g, reason, owners in _notices.get(context.scene.session_uid, ()):
        try:
            if current in owners and context.scene.objects.get(g.name) == g:
                out.append((g.name, REASONS[reason]))
        except ReferenceError:
            pass
    return out
