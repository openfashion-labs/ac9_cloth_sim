# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only viewport membership, separate from AC9's piece display state.

The viewport's local collections/local view select a domain. Active/Freeze/Off
still belong to display_state. Nothing here creates geometry, changes selection,
rewrites visibility, parses a spec Text, or stores an Area/Space pointer.
"""
from . import pattern_view as PV


def _space(context):
    space = getattr(context, 'space_data', None)
    return space if space is not None and getattr(space, 'type', None) == 'VIEW_3D' else None


def _in_scene(context, obj):
    if obj is None:
        return False
    try:
        scene = getattr(context, 'scene', None)
        return scene is None or scene.objects.get(obj.name) == obj
    except (AttributeError, ReferenceError):
        return False


def object_allowed(context, obj, include_state_hidden=False):
    """Whether an existing owner belongs to this viewport.

    Normal picking honors ordinary hide flags as well as viewport membership.
    include_state_hidden is for logical decoration owners: it tests only local
    view/local collections, so an Off owner's intentional hide does not erase
    its dashed rim. Callers still apply display_state and their user-hide policy.
    """
    try:
        obj = obj.original if obj is not None else None
    except (AttributeError, ReferenceError):
        return False
    if not _in_scene(context, obj):
        return False
    space = _space(context)
    try:
        if include_state_hidden:
            if space is None:
                return True
            # Blender stores local-collection runtime membership on the evaluated
            # owner. The original owner returned false for every collection in a
            # 5.2.1 background fixture even when visible_get(viewport) was true.
            evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
            return bool(evaluated.visible_in_viewport_get(viewport=space))
        kwargs = {}
        layer = getattr(context, 'view_layer', None)
        if layer is not None:
            kwargs['view_layer'] = layer
        if space is not None:
            kwargs['viewport'] = space
        return bool(obj.visible_get(**kwargs))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        # A stale RNA owner is not a usable target. An absent viewport uses the
        # traditional path above; it is not inferred from rotation or position.
        return False


def pattern_names(context, spec=None):
    """Visible flat-piece owners; a user's H/collection hide remains effective."""
    names = None if spec is None else {pc['name'] for pc in spec.get('pieces', ())}
    return frozenset(name for name, obj in PV.piece_objects().items()
                     if (names is None or name in names)
                     and object_decoration_allowed(context, obj))


def _collection_path_visible(context, obj):
    layer = getattr(context, 'view_layer', None)
    if layer is None:
        return True
    owners = set(obj.users_collection)
    def visit(node, parent_visible):
        visible = parent_visible and not (node.exclude or node.hide_viewport or node.collection.hide_viewport)
        if visible and node.collection in owners:
            return True
        return visible and any(visit(child, visible) for child in node.children)
    return visit(layer.layer_collection, True)


def object_decoration_allowed(context, obj):
    """Keep DS-hidden rims, but honor user object and collection visibility."""
    if not _in_scene(context, obj):
        return False
    if not obj.get('ac9_display_hidden') or obj.get('ac9_display_was_hidden', False):
        return object_allowed(context, obj)
    if obj.hide_viewport or not _collection_path_visible(context, obj):
        return False
    return object_allowed(context, obj, include_state_hidden=True)


def world_names(context, spec, display=None):
    """Logical 3D pieces allowed by their result, placed, or proxy owner."""
    if not spec:
        return frozenset()
    from . import display_state as DS
    from . import placed_view as PLV
    scene = context.scene
    result = scene.objects.get('AC9_Garment_' + str(spec.get('name', '')))
    display = DS.snapshot(spec, result) if display is None else display
    placed = PLV.objects(spec)
    result_visible = object_decoration_allowed(context, result)
    body = getattr(getattr(scene, 'ac9_cloth_sim', None), 'body', None)
    proxy_visible = (object_decoration_allowed(context, body) if body is not None else
                     _space(context) is None or not bool(_space(context).local_view) and allows_world(context, spec))
    return frozenset(name for name, state in display.items()
                     if (state['source'] == 'result' and result_visible or
                         state['source'] == 'placed' and object_decoration_allowed(context, placed.get(name)) or
                         state['source'] == 'proxy' and proxy_visible))


def allows_pattern(context):
    """Whether flat-pattern decorations belong here, even if pieces were deleted."""
    if _space(context) is None:
        return True
    owners = [obj for obj in PV.piece_objects().values() if _in_scene(context, obj)]
    layout = PV.layout_empty(context)
    if _in_scene(context, layout):
        owners.append(layout)
    # Before a pattern has been created there is no membership evidence. Keep
    # legacy drawing behavior rather than guessing that this is a world view.
    return not owners or any(object_decoration_allowed(context, obj) for obj in owners)


def allows_world(context, spec=None):
    """Whether known body/result/placed owners belong to this viewport.

    No all-scene scan or store.get() is performed. With no known world owner we
    retain the legacy domain; absence is not evidence of viewport exclusion.
    """
    if _space(context) is None:
        return True
    scene = getattr(context, 'scene', None)
    if scene is None:
        return True
    owners = []
    props = getattr(scene, 'ac9_cloth_sim', None)
    body = getattr(props, 'body', None)
    if _in_scene(context, body):
        owners.append(body)
    if spec is not None:
        result = scene.objects.get('AC9_Garment_' + str(spec.get('name', '')))
        if _in_scene(context, result):
            owners.append(result)
    from . import placed_view as PLV
    owners.extend(obj for obj in set(PLV.objects(spec).values()) if _in_scene(context, obj))
    return not owners or any(object_decoration_allowed(context, obj) for obj in owners)
