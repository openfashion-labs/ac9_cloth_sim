# SPDX-License-Identifier: GPL-3.0-or-later
"""Operation-boundary setup for the AC9 pattern/world viewports.

No registration timer, input interception, or drawing-time mutation. Native
collection visibility is the saved view state; this module stores no Area ID.
"""
import json
import bpy
from . import pattern_view as PV

RESULTS = 'AC9_ClothSim_Results'
OWNED = 'ac9_cloth_sim_results'
SCREEN = 'ac9_cloth_sim_separated'


def _result_owned(context, obj, spec):
    if not spec or obj is None or obj.type != 'MESH' or obj.library is not None:
        return False
    if context.scene.objects.get(obj.name) != obj or obj.name != 'AC9_Garment_' + str(spec.get('name', '')):
        return False
    attr = obj.data.attributes.get('ac9_piece')
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'INT' or len(attr.data) != len(obj.data.polygons):
        return False
    try:
        names = json.loads(obj.get('ac9_piece_names', '[]'))
        known = {p['name'] for p in spec.get('pieces', ())}
        from . import display_state
        return (bool(names) and all(isinstance(n, str) for n in names)
                and bool(display_state.result_names(obj) & known))
    except (ValueError, TypeError):
        return False


def _collection(context):
    coll = next((c for c in context.scene.collection.children if c.get(OWNED)), None)
    if coll is None:
        coll = bpy.data.collections.new(RESULTS)
        coll[OWNED] = True
        context.scene.collection.children.link(coll)
    return coll


def ensure_result(context, obj, spec):
    """Move only a verified root-linked result; preserve every user membership."""
    if not _result_owned(context, obj, spec) or context.scene.collection.objects.get(obj.name) != obj:
        return False
    coll = _collection(context)
    if coll.objects.get(obj.name) != obj:
        coll.objects.link(obj)
    context.scene.collection.objects.unlink(obj)
    return True


def ensure_accessory(context, obj, result, spec):
    """A generated button/zipper output shares its verified result's domain."""
    if not _result_owned(context, result, spec) or obj is None or obj.library is not None:
        return False
    if not any(obj.get(k) == result.name for k in ('ac9_buttons_of', 'ac9_zippers_of')):
        return False
    if context.scene.collection.objects.get(obj.name) != obj:
        return False
    coll = _collection(context)
    if coll.objects.get(obj.name) != obj:
        coll.objects.link(obj)
    context.scene.collection.objects.unlink(obj)
    return True


def organize_existing(context):
    """Explicit Work Screen action, never Load/F3: migrate known root results."""
    from . import dress
    moved = []
    for name, spec in dress.garment_specs().items():
        result = context.scene.objects.get(name)
        if ensure_result(context, result, spec):
            moved.append(name)
        if _result_owned(context, result, spec):
            for obj in list(context.scene.collection.objects):
                if ensure_accessory(context, obj, result, spec):
                    moved.append(obj.name)
    return moved


def _layers(context):
    out = []
    def walk(node):
        out.append(node)
        for child in node.children:
            walk(child)
    walk(context.view_layer.layer_collection)
    return out


def preflight(context):
    """Explain unsupported user structure without silently reorganizing it."""
    coll = bpy.data.collections.get(PV.COLL_NAME)
    if coll is None:
        return []
    notes = []
    nodes = [n for n in _layers(context) if n.collection == coll]
    if len(nodes) != 1 or context.scene.collection.children.get(coll.name) != coll:
        notes.append('AC9 pattern collection must have one path directly under the scene')
    allowed = {coll, *coll.children_recursive}
    for obj in coll.all_objects:
        if obj.name != PV.LAYOUT_NAME and not obj.get(PV.PIECE_PROP):
            notes.append('AC9 pattern collection contains an unrelated object: ' + obj.name)
        if set(obj.users_collection) - allowed:
            notes.append('AC9 pattern has an additional collection membership: ' + obj.name)
    for obj in context.scene.collection.objects:
        if obj.visible_get(view_layer=context.view_layer):
            notes.append('AC9 cannot isolate this scene-root object without moving it: ' + obj.name)
    if nodes and not nodes[0].is_visible:
        notes.append('AC9 pattern collection is hidden or excluded; its setting was kept')
    return notes


def configure(context, window, left, right):
    """Explicit Work Screen setup; preserve native Local View and other flags."""
    coll = PV._collection(context)
    context.view_layer.update()
    notes = preflight(context)
    if notes:
        return notes
    layers = _layers(context)
    index = next(i for i, node in enumerate(layers) if node.collection == coll)
    node = layers[index]
    for area, is_pattern in ((left, False), (right, True)):
        space = area.spaces.active
        space.use_local_collections = True
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with context.temp_override(window=window, area=area, region=region):
            if is_pattern:
                bpy.ops.object.hide_collection(collection_index=index, toggle=False, extend=False)
            elif node.visible_get():
                bpy.ops.object.hide_collection(collection_index=index, toggle=True, extend=False)
    window.screen[SCREEN] = True
    return []


def refresh(context):
    """New AC9 collection boundary: update pattern views already opted in.

    The pattern collection's native visibility identifies its domain, including
    an empty collection. No orientation, pointer, or screen-position guess.
    Deliberate manual collection changes and Local View are left intact.
    """
    coll = bpy.data.collections.get(PV.COLL_NAME)
    if coll is None:
        return
    context.view_layer.update()
    for win in context.window_manager.windows:
        if win.scene != context.scene or not win.screen.get(SCREEN):
            continue
        for area in win.screen.areas:
            if area.type != 'VIEW_3D' or not area.spaces.active.use_local_collections:
                continue
            reg = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if reg is None:
                continue
            with context.temp_override(window=win, area=area, region=reg):
                layers = _layers(bpy.context)
                item = next(((i, n) for i, n in enumerate(layers) if n.collection == coll), None)
                if item is not None and item[1].visible_get():
                    bpy.ops.object.hide_collection(collection_index=item[0], toggle=False, extend=False)
