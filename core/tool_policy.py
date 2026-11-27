# SPDX-License-Identifier: GPL-3.0-or-later
"""One source for tool decorations, editing scope and the generated tool reference.

Add every registered AC9 workspace tool here. Consumers keep selection memory,
but only act on the domains owned by the current tool. External tools use EXTERNAL.
"""
INTERNAL_COLOR = (0.05, 0.25, 0.85, 1.0)
SELECTED_COLOR = (1.0, 0.55, 0.1, 1.0)
EXTENSION = dict(tool='EDIT_PATTERN', hit='edge', label='Extend Outline...',
                 operator='ac9_cloth_sim.extend_outline')
WHOLE_FRAME_PADDING_PX = 20.0
EXTENSION_METHOD_CHOICES = ('WIDTH','XY')
PARAMETER_FIELDS = dict(extension=dict(XY=('method','move_x','move_y','move_seams'),AXIS=('method','axis','distance','move_seams'),WIDTH=('method','distance','join','move_seams')),
                        numeric=('move_x','move_y'),parallel=('distance','count'),
                        dart=('width','length','true_mouth'),spread=('amount','mode','hinge','side'),match_length=('ease','end'))
# Kept in sync with actual parameter_open callers by test_parameter_inventory.
PARAMETER_OPERATORS={
    'ac9_cloth_sim.extend_outline':'extension',
    'ac9_cloth_sim.set_gap':'numeric',
    'ac9_cloth_sim.parallel_line':'parallel',
    'ac9_cloth_sim.add_dart':'dart',
    'ac9_cloth_sim.spread':'spread',
    'ac9_cloth_sim.match_length':'match_length',
}
WHOLE_FRAME_DARK = (0.08,0.08,0.08,1.0)
WHOLE_FRAME_LIGHT = (0.95,0.95,0.95,1.0)


def _row(key, suffix, name, domain, points, seams=False):
    return dict(key=key, id='ac9_cloth_sim.' + suffix, name=name, domain=domain,
                points=points, seams=seams, internals=True)


TOOLS = (
    _row('TRANSFORM_PATTERN', 'transform_pattern_tool', 'ピース全体', 'whole', '選択全体の点線枠'),
    _row('EDIT_PATTERN', 'edit_pattern_tool', 'ピース編集', 'element', '外周●・内部○・接続◎'),
    _row('DRAW_PIECE', 'draw_piece_tool', 'ピースを描く', 'draw', '作図点・吸着先'),
    _row('RECT', 'rect_tool', '長方形のピース', 'draw', '作図点・吸着先'),
    _row('ELLIPSE', 'ellipse_tool', '楕円のピース', 'draw', '作図点・吸着先'),
    _row('DRAW_LINE', 'draw_line_tool', '内部線', 'draw', '作図点・外周/内部位置吸着先'),
    _row('IRECT', 'internal_rect_tool', '内部の長方形', 'draw', '作図点・外周/内部位置吸着先'),
    _row('IELLIPSE', 'internal_ellipse_tool', '内部の楕円', 'draw', '作図点・外周/内部位置吸着先'),
    _row('EDIT_SEAMS', 'edit_seams_tool', '縫い目編集', 'seam', '縫い目端・吸着先', True),
    _row('ADD_SEAM', 'add_seam_tool', '縫い目を追加', 'seam', '縫う区間・吸着先', True),
    _row('ADD_SEAM_MN', 'add_seam_mn_tool', '縫い目を追加 (M:N)', 'seam', '縫う区間・吸着先', True),
    _row('MEASURE', 'measure_tool', '一周テープ', 'measure', '採寸点・吸着先'),
    _row('TAPE_ALONG', 'tape_along_tool', 'なぞるテープ', 'measure', '採寸点・吸着先'),
    _row('TAPE_STRAIGHT', 'tape_straight_tool', '直線テープ', 'measure', '採寸点・吸着先'),
    _row('MOVE_LEVEL', 'move_level_tool', '採寸を直す', 'measure', '採寸点・取っ手'),
    _row('PLACE', 'place_tool', '配置', 'place', '3D配置の取っ手'),
)
BY_ID = {r['id']: r for r in TOOLS}
BY_KEY = {r['key']: r for r in TOOLS}
EXTERNAL = dict(key='EXTERNAL', id='', name='その他', domain='external', points='ツール固有',
                seams=False, internals=True)


def policy(identifier):
    if identifier in BY_ID:
        return BY_ID[identifier]
    if identifier in BY_KEY:
        return BY_KEY[identifier]
    if identifier.startswith('ac9_cloth_sim.'):
        raise KeyError('Unclassified workspace tool: ' + identifier)
    return EXTERNAL


def pattern_seams(identifier, always=False, *, enabled=True):
    return bool(policy(identifier)['seams'] or (enabled and always))


def editable(identifier, domain):
    return policy(identifier)['domain'] == domain


EDIT_HINTS = {
    'edge': 'K: add point   C: curve   N: notch',
    'corner': 'Drag: move   X: delete corner',
    'notch': 'Drag: slide   X: delete notch',
    'iend': 'Drag: move the end',
    'iline': 'Drag: move span   K: add point   C: curve',
    'inode': 'Drag: move the point   X: delete point',
    'ihandle': 'Drag: bend the curve   C: straighten',
    'handle': 'Drag: bend the curve',
    'piece': 'Drag: box select   Whole Pieces: move the whole piece',
    None: 'Drag: box select   G / R / S: transform selection',
}
EDIT_HEADER = 'Click: select (Shift: add)'
EDIT_MENU_HINT = 'Right-click: actions   All keys: sidebar Pattern > Keys'


def reference():
    """The checked-in table is generated, never edited separately from this definition."""
    labels = dict(whole='ピース・内部線全体', element='点・1辺（Shift／箱で複数）',
                  draw='新しい形の作図', seam='縫い目・縫う区間', measure='採寸', place='3D配置')
    lines = ['# ツール別の表示・操作', '',
             'この表は `core/tool_policy.py` から生成します。手で変更しません。', '',
             '| ツール | 頂点・操作点 | 選択・編集対象 | 2D縫い目 |', '|---|---|---|---|']
    for r in TOOLS:
        lines.append(f"| {r['name']} | {r['points']} | {labels[r['domain']]} | {'表示' if r['seams'] else '通常は非表示'} |")
    lines += ['', f'2Dのピース全体は選択全体を画面上{WHOLE_FRAME_PADDING_PX:g}pxの余白付き点線枠で囲み、枠内のドラッグとG/R/Sは選択済み対象を操作します。短いクリックは従来どおり対象を選びます。',
              'クリックで先に選択。枠内ドラッグとG/R/Sは選択済みだけを操作し、未選択・枠外のドラッグは範囲選択。未選択のG/R/Sは変形しません。',
              '枠は明暗2色。同じ選択の間は向きと中心を保ち、G/R/S・取消・Undo/Redoに追従。ピース編集の面クリックは属性と3D位置の参照だけです。',
              '内部線は2D・3Dとも濃い青。ピース編集の曲線取っ手は選択時だけ□。',
              '外周と内部線の接続頂点は一緒に移動。右クリック「頂点を分離して移動」で候補の実線を強調して選び、片側を分離。外周への明示吸着で再接続します。',
              '内部線同士の頂点/辺へ作図/頂点編集で位置だけを吸着。自動接続/辺追従はせず、後から相手を変形しても独立。内部同士のXY/距離整列は対象外。明示複数選択は一緒に変形します。',
              '外周の分離候補はツールバー/サイドバーを避けて表示。候補の下の物体ではなく、指定した頂点を移動します。',
              'ツール外での2D縫い目参照設定は閲覧だけを追加し、編集権限は変えません。名称と入口は表示設定表を参照します。',
              '縫い目編集・追加・M:N追加中は、補助表示や縫い目のスイッチがオフでも型紙の作業用縫い目とA/Bを表示します。',
              '他のツールの参照表示と3Dの縫い目表示はオーバーレイ設定に従います。', '']
    lines += ['## ピース編集の上部案内', '',
              'ホバー対象に連動。操作対は日英とも半角の「入力: 動作」。3D未更新警告は別に維持。', '',
              '| 対象 | 案内（英語の共通定義） |', '|---|---|']
    for kind, text in EDIT_HINTS.items():
        lines.append(f"| {kind or '空白'} | {text} |")
    field_names=dict(width='幅',length='長さ',true_mouth='口を整える',amount='開き幅',mode='開き方',hinge='支点',side='動く側',ease='ゆとり',end='端',method='方式',join='角の処理',axis='軸',distance='距離',move_x='X',move_y='Y',count='本数',move_seams='縫い目を移す')
    fields=lambda names:'・'.join(field_names[n] for n in names)
    lines += ['', EDIT_HEADER, '', EDIT_MENU_HINT, '', '## 数値操作中の小窓', '',
              '延長・数値入力・平行線・ダーツ追加・切り開き・長さ合わせを開始すると、所有する型紙ビューに操作中だけの小窓を表示します。上部のツール設定と右の対象属性は置き換えません。',
              'パン・回転・ズームで値とプレビューを保持。確定まで型紙を書き換えません。', '',
              '| 操作 | 小窓の入力項目（実装と共通の定義） | 条件で増える項目 |', '|---|---|---|',
              '| 延長・XY | '+ fields(PARAMETER_FIELDS['extension']['XY']) +' | — |',
              '| 延長・幅 | '+ fields(PARAMETER_FIELDS['extension']['WIDTH']) +' | — |',
              '| 数値入力 | '+ fields(PARAMETER_FIELDS['numeric']) +' | 大きさ・外周や線までの距離（対象による） |',
              '| 平行線 | '+ fields(PARAMETER_FIELDS['parallel']) +' | 両側・反対側・外周まで（内部線の場合） |',
              '| ダーツ追加 | '+fields(PARAMETER_FIELDS['dart'])+' | 長すぎる場合の調整を表示 |',
              '| 切り開き | '+fields(PARAMETER_FIELDS['spread'])+' | 支点はフレアの場合 |',
              '| 長さ合わせ | '+fields(PARAMETER_FIELDS['match_length'])+' | 開いたときの縫い目を固定 |', '',
              '小窓の適用／取消、またはキャンバス上のEnter／Escで確定／取消。欄のEnterは数値だけを確定し、欄のEscはその入力だけを戻します。',
              '小窓を残したままパン・回転・ズームと再入力。タイトルをドラッグして移動、欄は単位付き四則演算・選択・コピー/貼付・左右/Home/End・Tab・局所Undoに対応します。Undo・ファイル切替・ツール変更で未確定分を取り消します。',
              '終了時は小窓とプレビューを回収。所有ウィンドウ/エリア/文書の外へ適用しません。対象属性のToolタブへは置きません。', '',
              '## 外周の延長', '',
              f"ピース編集の外周辺の右クリックから `{EXTENSION['label']}`。曲線・離れた複数辺も同じX・Yで延長。", '',
              '通常の入力は外周からの幅。X・Y移動量（全選択共通）は補助の指定。',
              '軸方式は通常の小窓に出さず、既存スクリプトとの互換用に保持。',
              '外周からの幅は軸を選ばず各辺の垂直な外側へ延長し、角は交点でつなぐか、辺ごとに元角へ戻って外側の空きを残すかを選べます。つなぐ場合、鋭い角の先端は入力幅より長く伸びます。曲線の新外周は細かい折れ線で近似し、元の曲線は内部線に残ります。',
              '小窓は幅方式から開始。元の辺は内部線に残す。縫い目を新外周へ移す指定は今回だけで、初期値OFF。',
              '「辺ごと」の保持元線は旧外周の論理辺ごとに独立した開線。曲線の生サンプル/制御点は同じ線に保持。既存の内部線の自動分割はしない。',
              'OFFでは元の位置で縫い続け、新外周は縫わない。既存内部線の位置は保持する。', '']
    return '\n'.join(lines)
