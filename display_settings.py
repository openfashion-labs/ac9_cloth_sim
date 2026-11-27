# SPDX-License-Identifier: GPL-3.0-or-later
"""One presentation of scene-shared settings for every display-settings entrance."""
from .core import overlay_policy as OP
from .i18n import CTX, tx


def draw_setting(layout, context, key, *, destination=True, toggle=False):
    entry = OP.BY_KEY[key]
    props = context.scene.ac9_cloth_sim
    owner = getattr(context.scene, 'ac9_cloth_sim_measure', None) if entry['owner']=='measure' else props
    if owner is None:
        return
    row = layout.row(align=True)
    if entry['parent']:
        row.active = bool(getattr(props, entry['parent']))
    split = row.split(factor=0.76, align=True) if destination else row
    left = split.row(align=True) if destination else row
    left.prop(owner, key, text=OP.label(key), text_ctxt=CTX, toggle=toggle)
    if destination:
        right = split.row(align=True)
        right.alignment = 'RIGHT'
        right.label(text=tx(OP.destination(key, getattr(props, 'diagnosis_display', 'NONE'))))


def draw_measure_visibility(layout, context):
    props = context.scene.ac9_cloth_sim
    col = layout.column(align=True)
    col.enabled = props.show_overlays
    for key in ('show_body', 'show'):
        draw_setting(col, context, key)
    if not props.show_overlays:
        layout.label(text=tx(OP.OFF), icon='INFO')


def draw(layout, context):
    props = context.scene.ac9_cloth_sim
    row = layout.row()
    row.scale_y = 1.2
    row.prop(props, 'show_overlays', text=OP.MASTER, text_ctxt=CTX, toggle=True,
             icon='HIDE_OFF' if props.show_overlays else 'HIDE_ON')
    hint = layout.row()
    hint.scale_y = 0.8
    hint.label(text=tx(OP.SHARED))
    from . import silhouette
    for group, title in OP.GROUPS:
        layout.separator(factor=0.6)
        col = layout.column(align=True)
        col.enabled = props.show_overlays if group != 'garment' else True
        heading = col.row(align=True)
        heading.label(text=tx(OP.group_label(group)))
        if group == 'seams':
            info = col.row()
            info.scale_y = 0.8
            info.label(text=tx(OP.WORKING))
        if group == 'garment':
            heading.label(text=tx(OP.DOMAINS['WORLD']))
        for entry in OP.ENTRIES:
            if entry['group'] != group:
                continue
            key = entry['key']
            if entry['kind'] == 'OPERATOR':
                row = col.row(align=True)
                row.operator('ac9_cloth_sim.silhouette_position', text=OP.label(key), text_ctxt=CTX)
                if not silhouette.pattern_view(context):
                    row.enabled = False
                    info = col.row()
                    info.scale_y = 0.8
                    info.label(text=tx(OP.PATTERN_ACTION))
            elif key == 'diagnosis_display':
                # The heading names the enum; keep the value and its destination visible.
                row = col.row(align=True)
                split = row.split(factor=0.76, align=True)
                split.prop(props, key, text='')
                right = split.row()
                right.alignment = 'RIGHT'
                right.label(text=tx(OP.destination(key, props.diagnosis_display)))
            else:
                draw_setting(col, context, key, destination=group!='garment')
