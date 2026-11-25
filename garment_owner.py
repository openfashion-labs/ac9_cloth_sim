# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve existing geometry by its document. Names are labels, never ownership.

An omitted owner means the current document; background reconciliation passes
the Text explicitly. Legacy name-only data is usable only for a unique document.
No geometry creation, persistence migration, or document registry in readers.
"""
import bpy
import json

CURRENT = object()
GARMENT = 'ac9_garment_spec'
PLACED = 'ac9_placed_spec'


def text(owner=CURRENT, context=None):
    if owner is CURRENT:
        from . import store
        owner = store.text(context or bpy.context)
    return owner if isinstance(owner, bpy.types.Text) and bpy.data.texts.get(owner.name) == owner else None


def legacy_owner(spec, owner=CURRENT, context=None):
    """Name-only files have no evidence to choose between two documents."""
    owner = text(owner, context)
    if owner is None or spec is None:
        return False
    from . import dress
    matches = [t for t, sp in dress.garment_records() if sp.get('name') == spec.get('name')]
    return len(matches) == 1 and matches[0] == owner


def owns(data, prop, spec, *, owner=CURRENT, context=None, legacy_name=None):
    from . import private_ids
    if private_ids.contains(data):
        return False
    owner = text(owner, context)
    if data is None or owner is None:
        return False
    mesh = getattr(data, 'data', None)
    claims = [data.get(prop)]
    if getattr(mesh, 'get', None) is not None:
        claims += [mesh.get(prop)]
        if prop == GARMENT:
            claims += [mesh.get('ac9_control_owner')]
    claims = [claim for claim in claims if claim is not None]
    if claims:
        return all(claim == owner for claim in claims)
    if prop in data:
        return False
    if prop == GARMENT:
        # A label collision with an ordinary user mesh is not a legacy garment.
        if data.type != 'MESH':
            return False
        attr = data.data.attributes.get('ac9_piece')
        try:
            names = json.loads(data.get('ac9_piece_names', '[]'))
        except (ValueError, TypeError):
            return False
        if (not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names)
                or attr is None or attr.domain != 'FACE' or attr.data_type != 'INT'
                or len(attr.data) != len(data.data.polygons)):
            return False
        # A different document having a unique label is no evidence about this
        # unqualified mesh. Renamed/suffixed legacy results need saved evidence.
        from . import dress
        if spec is None or data.name != dress.garment_name(spec):
            return False
    return (spec is not None and legacy_name == spec.get('name')
            and legacy_owner(spec, owner, context))


def garment(spec, context=None, *, owner=CURRENT):
    if spec is None:
        return None
    context = context or bpy.context
    owner = text(owner, context)
    if owner is None:
        return None
    name = 'AC9_Garment_' + spec['name']
    candidate = context.scene.objects.get(name)
    if (candidate is not None and candidate.type == 'MESH'
            and owns(candidate, GARMENT, spec, owner=owner, legacy_name=spec['name'])):
        return candidate
    # A same-name document may have created its mesh with Blender's .001 suffix.
    return next((ob for ob in context.scene.objects
                 if ob.type == 'MESH' and 'ac9_piece_names' in ob
                 and owns(ob, GARMENT, spec, owner=owner)), None)


def auxiliary(label, spec, context=None, *, owner=CURRENT):
    """Find a document's generated Empty, without borrowing a shared legacy Empty."""
    return next((ob for ob in bpy.data.objects if ob.type == 'EMPTY'
                 and (ob.name == label or ob.name.startswith(label + '.'))
                 and owns(ob, PLACED, spec, context=context, owner=owner,
                          legacy_name=ob.get('ac9_center_of'))), None)
