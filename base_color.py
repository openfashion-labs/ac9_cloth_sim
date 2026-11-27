# SPDX-License-Identifier: GPL-3.0-or-later
"""A small material color control, without changing selection or shader graphs."""
from . import store, look_apply
from .core import look
from .i18n import CTX, tx


def target(context, piece=False):
    spec, _ = store.get(context)
    if spec is None:
        return None, 'Assign a material first'
    if piece:
        from . import tool_tab
        names, _ = tool_tab.attribute_selection(context, spec)
        mats = [look_apply.material(look.resolved(spec, n)) for n in names]
        if len(frozenset(mats)) > 1:
            return None, 'Select pieces with the same material'
        mat = mats[0] if mats else None
    else:
        mat = look_apply.material(look.default_look(spec))
    return mat, '' if mat is not None else 'Assign a material first'


def socket(mat):
    """Only a direct, active Principled shader with an unlinked Base Color is editable."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    output = mat.node_tree.get_output_node('ALL')
    surface = output.inputs.get('Surface') if output else None
    if surface is None or not surface.is_linked:
        return None
    node = surface.links[0].from_node
    color = node.inputs.get('Base Color') if node.type == 'BSDF_PRINCIPLED' else None
    return color if color is not None and not color.is_linked else None


def reason(mat):
    if mat is None:
        return 'Assign a material first'
    if not mat.is_editable or (mat.use_nodes and mat.node_tree and not mat.node_tree.is_editable):
        return 'This material is read-only'
    if mat.use_nodes and socket(mat) is None:
        return 'Base Color is controlled by nodes'
    return ''


def get(context, piece=False):
    mat, _ = target(context, piece)
    color = socket(mat)
    return tuple(color.default_value[:3] if color else mat.diffuse_color[:3] if mat else (0.8, 0.8, 0.8))


def set(context, value, piece=False):
    mat, why = target(context, piece)
    if why or reason(mat):
        return
    color = socket(mat)
    if color is not None:
        color.default_value = (*value, color.default_value[3])
    mat.diffuse_color = (*value, mat.diffuse_color[3])
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def draw(layout, context, spec, piece=False):
    mat, why = target(context, piece)
    why = why or reason(mat)
    row = layout.row()
    row.enabled = not why
    row.prop(context.scene.ac9_cloth_sim, 'piece_base_color' if piece else 'garment_base_color',
             text='Base Color', text_ctxt=CTX)
    if why:
        layout.label(text=tx(why), icon='INFO')
    elif mat is not None:
        # Most pieces inherit one default. Resolve each distinct reference once per draw.
        counts = {}
        refs = {}
        for ref in look.resolved_all(spec).values():
            key = (ref.get('uid'), ref.get('material')) if ref else None
            counts[key] = counts.get(key, 0) + 1
            refs[key] = ref
        shared = sum(count for key, count in counts.items() if look_apply.material(refs[key]) == mat)
        if shared > 1:
            layout.label(text=tx('Shared by {0} pieces in this garment', shared), icon='INFO')
