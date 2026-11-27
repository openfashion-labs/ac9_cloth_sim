# SPDX-License-Identifier: GPL-3.0-or-later
"""Display-setting names, destinations and consumers; no viewport-local state.

Keep stored property identifiers unchanged. UI entries and the maintained reference
read these records. Tool-specific seam visibility remains in tool_policy.
"""
TITLE = 'Overlays'
MASTER = TITLE
MASTER_KEY = 'show_overlays'
MASTER_ENTRANCES = ('ui.draw_overlay_switch', 'ui.AC9_PT_SimOverlays')
SHARED = 'Shared across views of this scene'
OFF = 'Overlays are off'
WORKING = 'Pattern seams stay visible while sewing'
PATTERN_ACTION = 'Use the pattern view to move silhouettes'
DOMAINS = {'PATTERN': 'Pattern', 'WORLD': '3D', 'BOTH': 'Pattern & 3D', 'NONE': ''}
GROUPS = (('seams', 'Seams'), ('pieces', 'Pieces'), ('measure', 'Measure'),
          ('placement', 'Placement & Silhouettes'), ('diagnosis', None),
          ('garment', 'Garment'))


def _entry(key, label, group, domain, *, owner='cloth', parent=None, note='', extra=(), kind='PROP'):
    return dict(key=key, label=label, group=group, domain=domain, owner=owner,
                parent=parent, note=note, entrances=('ui.AC9_PT_SimOverlays',) + extra, kind=kind)


ENTRIES = (
    _entry('show_overlay', 'Seams', 'seams', 'WORLD',
           note='Show seams in 3D; pattern seams are controlled separately'),
    _entry('show_pattern_seams_always', 'Show Seams with Other Tools', 'seams', 'PATTERN',
           note='Show pattern seams for reference with other tools; this does not enable seam editing'),
    _entry('show_lengths', 'Selected Seam Lengths', 'seams', 'PATTERN',
           note='Show the selected seam lengths and their difference on the pattern'),
    _entry('show_notches', 'Notches', 'seams', 'PATTERN',
           note='Show pattern notches and matching lines; the selected seam dots in 3D follow Seams'),
    _entry('show_piece_colors', 'Piece Colors', 'pieces', 'BOTH',
           note='Show piece state colors on the pattern and in 3D; selection highlighting remains visible'),
    _entry('show_fabric_colors', 'Color by Fabric', 'pieces', 'BOTH', parent='show_piece_colors',
           note='Color pieces by fabric while piece colors are enabled; Freeze and Off keep their state colors',
           extra=('ui.draw_fabric_list',)),
    _entry('show_names', 'Piece Names', 'pieces', 'PATTERN', note='Show piece names on the pattern'),
    _entry('show_internal_3d', 'Internal Lines', 'pieces', 'WORLD',
           note='Show internal lines in 3D; selected lines may remain highlighted when this is off'),
    _entry('show_body', 'Body & Garment Measurements', 'measure', 'WORLD', owner='measure',
           note='Show body measurement lines and labels, and corresponding garment measurement lines in 3D',
           extra=('measure_tool.AC9_PT_SimMeasure',)),
    _entry('show', 'Measuring Tapes', 'measure', 'BOTH', owner='measure',
           note='Show manual tapes in 3D and measurement lines and values on the pattern',
           extra=('measure_tool.AC9_PT_SimMeasure',)),
    _entry('show_guides', 'Placement Guides', 'placement', 'WORLD',
           note='Show placement guides in 3D', extra=('ui.AC9_PT_SimPlacement',)),
    _entry('show_silhouette_avatar', 'Avatar Silhouette', 'placement', 'PATTERN',
           note='Show the avatar silhouette on the pattern'),
    _entry('show_silhouette_collisions', 'Collision Silhouettes', 'placement', 'PATTERN',
           note='Show collision mesh silhouettes on the pattern without changing simulation collisions'),
    _entry('silhouette_position', 'Move Silhouettes', 'placement', 'PATTERN', kind='OPERATOR',
           note='Click on the pattern to move the silhouettes; Esc cancels'),
    _entry('diagnosis_display', 'Diagnosis Display', 'diagnosis', 'MODE',
           note='Problem areas: pattern and 3D; stretch and face side: 3D',
           extra=('ui.AC9_PT_SimDiagnosis',)),
    _entry('show_thickness', 'Show Fabric Thickness', 'garment', 'WORLD',
           note='Show garment thickness in 3D independently of Overlays'),
    _entry('thickness_side', 'Direction', 'garment', 'WORLD', parent='show_thickness',
           note='The side on which the existing garment display thickness is added'),
)
BY_KEY = {e['key']: e for e in ENTRIES}
DIAGNOSIS_DOMAINS = {'NONE': 'NONE', 'PROBLEMS': 'BOTH', 'STRETCH': 'WORLD', 'SIDE': 'WORLD'}
DOC_NAMES = {
    'title': tuple(f'docs/{lang}/features/{page}.md' for lang in ('ja','en')
                   for page in ('screen','setup','fabric','placement')),
    'show_body': tuple(f'docs/{lang}/{page}.md' for lang in ('ja','en')
                       for page in ('features/measure','features/screen','first-garment')),
    'show': tuple(f'docs/{lang}/{page}.md' for lang in ('ja','en')
                  for page in ('features/measure','features/screen','first-garment')),
    'show_thickness': tuple(f'docs/{lang}/features/{page}.md' for lang in ('ja','en') for page in ('screen','setup')),
    'show_fabric_colors': tuple(f'docs/{lang}/features/fabric.md' for lang in ('ja','en')),
    'show_pattern_seams_always': tuple(f'docs/{lang}/features/pattern.md' for lang in ('ja','en')),
    'diagnosis_display': tuple(f'docs/{lang}/{page}.md' for lang in ('ja','en')
                              for page in ('features/placement','features/setup','first-garment')),
    'show_silhouette_avatar': ('docs/ja/glossary.md','docs/en/glossary.md'),
    'show_silhouette_collisions': ('docs/ja/glossary.md','docs/en/glossary.md'),
}


def group_label(group):
    return label('diagnosis_display') if group=='diagnosis' else dict(GROUPS)[group]


def label(key):
    return BY_KEY[key]['label']


def note(key):
    return BY_KEY[key]['note']


def destination(key, mode='NONE'):
    domain = BY_KEY[key]['domain']
    return DOMAINS[DIAGNOSIS_DOMAINS[mode] if domain == 'MODE' else domain]


def reference(translate=lambda s: s):
    rows = ['# Display settings / 表示設定', '',
            'Generated from `core/overlay_policy.py`. Do not edit this table by hand.', '',
            'All values are shared by views of the same scene. / 設定値は同じシーンの各画面で共有します。', '',
            '| ID | Group / 区画 | Label / 名前 | Destination / 表示先 | Dependency / 依存 | Entrances / 入口 |',
            '|---|---|---|---|---|---|']
    rows.append(f"| cloth.{MASTER_KEY} | — | {MASTER} / {translate(MASTER)} | {DOMAINS['BOTH']} | — | {', '.join(MASTER_ENTRANCES)} |")
    for e in ENTRIES:
        domain = ('; '.join(f'{mode}: {destination(e["key"], mode)}' for mode in DIAGNOSIS_DOMAINS if mode!='NONE')
                  if e['domain']=='MODE' else destination(e['key']))
        rows.append(f"| {e['owner']}.{e['key']} | {translate(group_label(e['group']))} | {e['label']} / {translate(e['label'])} | {domain} | {e['parent'] or '—'} | {', '.join(e['entrances'])} |")
    rows += ['', 'Garment thickness is outside the overlay master switch; selection highlighting may remain visible.',
             '厚みは補助表示の主スイッチ外。選択中の強調表示は残る場合があります。', '']
    return '\n'.join(rows)
