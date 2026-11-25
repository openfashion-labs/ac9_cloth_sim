# SPDX-License-Identifier: GPL-3.0-or-later
"""Operation-local visibility values; LayerCollection RNA never crosses edits."""


def _paths(node, prefix=()):
    path = prefix + (int(node.collection.session_uid),)
    yield path, node
    for child in node.children:
        yield from _paths(child, path)


def _resolve(context, path):
    node = context.view_layer.layer_collection
    if int(node.collection.session_uid) != path[0]:
        return None
    for uid in path[1:]:
        node = next((child for child in node.children if int(child.collection.session_uid) == uid), None)
        if node is None:
            return None
    return node


def reveal_collection(context, collection_uid):
    """Open the first existing path, reacquiring after each visibility write."""
    path = next((path for path, node in _paths(context.view_layer.layer_collection)
                 if int(node.collection.session_uid) == collection_uid), None)
    opened = []
    if path is None:
        return opened
    for length in range(2, len(path) + 1):
        current = path[:length]
        node = _resolve(context, current)
        if node is None:
            break
        if node.exclude or node.hide_viewport or node.collection.hide_viewport:
            opened.append(node.collection.name)
        for prop in ('exclude', 'hide_viewport', 'collection'):
            node = _resolve(context, current)
            if node is None:
                break
            if prop == 'collection':
                if node.collection.hide_viewport:
                    node.collection.hide_viewport = False
            elif getattr(node, prop):
                setattr(node, prop, False)
    return opened


class VisibilityBackup:
    """Restore only surviving paths of the original Scene and ViewLayer."""
    def __init__(self, context):
        from .selection import view_layer_key
        self.scene = int(context.scene.session_uid)
        self.view_layer = int(context.view_layer.as_pointer())
        self.layer_key = view_layer_key(context, create=True)
        self.values = [(path, bool(node.exclude), bool(node.hide_viewport), bool(node.collection.hide_viewport))
                       for path, node in _paths(context.view_layer.layer_collection)]
        self.collections = {path[-1]: hidden for path, _exclude, _hidden, hidden in self.values if len(path) > 1}

    def restore(self, context):
        from .selection import view_layer_key
        if (int(context.scene.session_uid) != self.scene
                or int(context.view_layer.as_pointer()) != self.view_layer
                or view_layer_key(context) != self.layer_key):
            return
        # Collection flags belong to the datablock, independently of which
        # parent paths still exist. Only touch collections in this same scene.
        for uid, value in self.collections.items():
            collection = next((node.collection for path, node in _paths(context.view_layer.layer_collection)
                               if path[-1] == uid), None)
            if collection is not None and collection.hide_viewport != value:
                collection.hide_viewport = value
        for path, exclude, hidden, collection_hidden in self.values:
            if len(path) == 1:  # the Scene Collection is not an excludable user path
                continue
            for prop, value in (('exclude', exclude), ('hide_viewport', hidden)):
                # Every write can resynchronize Blender's collection tree. Resolve
                # again even between two properties of the same saved path.
                node = _resolve(context, path)
                if node is None:
                    break
                if getattr(node, prop) != value:
                    setattr(node, prop, value)
