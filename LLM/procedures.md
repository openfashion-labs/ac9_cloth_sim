# AC9 Cloth Sim 手順の補足（AI 向け）

この資料は、README.md の説明を複製しません。共通の説明は「README の『<見出し>』」と節名で参照します。ボタン・パネルの名前は画面の英語表記のままです。操作 ID は、コードの `bl_idname` で確かめたものだけを書きます。操作 ID が資料にあっても、AI に操作の権限が加わるわけではありません。

## 案内上の注意と画面の場所

目的: 利用者に操作を案内するときの、場所の言い方と、AI がしてよいこと・してはいけないことを決めておく。

画面の場所（新しい既定の配置。README の『画面の配置』）:

- 3D ビューのサイドバー（N パネル）の **AC9 Cloth Sim** タブ: **Setup**、**Pattern**、**Lists**、**Piece Roles**、**Placement**、**Measure**。
- 右下の Properties エディタの **Tool** タブ: **AC9 Run**（常に最上段）、**AC9 Garment**、**AC9 Piece**、**AC9 Seam**（下に **Lengths & Notches** と **Blend**）、**AC9 Internal Line**、**AC9 Edges**、**AC9 Fabric**、**AC9 Interlining**、**AC9 Button**、**AC9 Measurement**。最後に触った物で切り替わる。何かを選んだのに該当パネルが出ないときは、Tool タブを開いているかを確かめる。
- サイドバーの **Lists** のタブは 6 つ（**Seams**、**Pieces**、**Fabrics**、**Collision**、**Tapes**、**Fasteners**）。一覧は見て選ぶだけで、設定は Tool タブ。
- 画面の言語: Blender が日本語表示なら、ボタンは日本語の訳で出る。案内では英語の名前と、日本語表示の利用者には画面の日本語の両方が通じるように言う（訳は `i18n_ja.py`）。
- 左の道具の列: 型紙の道具は **Setup** の **Pattern Tools** がオンのときだけ出る。**Edit Measure**・**Tape Around**・**Tape Along**・**Tape Straight**・**Place** は常に出る。
- 3D ビューのヘッダー右上: **Overlays**（目のボタンと一覧）。
- 型紙は、サイドバーの **Work Screen** で出る作業画面の真ん中の 3D ビューで直す。

してよいこと: 状態・選択・モードを読む。画面を撮って見せる。次の操作と確認する点を短く案内する。状態表示の文字と `ac9_job` を読み上げ、記録する。

してはいけないこと:

- 長いモーダル処理や結果を変える処理（`ac9_cloth_sim.update_3d`、`ac9_cloth_sim.dress` の LIVE と LOCAL、`ac9_cloth_sim.edit_frame`、`ac9_cloth_sim.delete_cache`、`ac9_cloth_sim.export_retopo`）を AI が押す・Python で呼ぶ。案内だけして本人が押す。
- 保存、ジョブのフォルダの操作（消す・改名する・手で作る）、服の形（頂点）の変更。
- 赤い塗りを見て「不良です」と言い切る。1 回の結果で細かい差の良し悪しを決める。

本人が押す前に一言添えること: 別名で保存済みか、**Watch** は **Phase Sim** の前に入れたか、**Edit Frame** は経過の再生が止まる（Ctrl+Z で戻る）、**Live Sim** と **Local Sim** は **Esc** か **Stop** を押すまで止まらず経過のコマは残らない、**Delete Cache** で消したコマは戻らない、**To AC9 Cloth Retopo** は画面に出ているコマを使う。

表示名の注意: 画面にない名前を案内しない。今の名前は、**Edit Measure**（採寸の線を動かす）、**Move by Hand**（3D で手で動かす。着せる前も後も）、**Lengths & Notches**（縫い目の長さと合印）、**Particle Distance (mm)**（網目の間隔）、**Default Fabric**（生地。Weight や Bending の欄は服の設定には無い）、**Phase Sim**（続きから回す。最初の回は着せる）、**Live Sim**、**Local Sim**、**Import BOM**、**Update BOM Seams**（着装形を保って縫い目を更新）、**All On**。画面に無い名前: Simulate、Live、Continue、Dress、Re-dress、Use Selected Pieces、Seams from BOM、Fold のボタン、Adjust、Back to Frames、Name のボタン（名前は **AC9 Piece** の欄か F2）、Center、Tape／Hold（縫い目を巻き尺に沿わせる欄は今は出ない）。

戻し方（共通）: ほとんどの操作は Ctrl+Z で戻る。

## 型紙を読み込む（Import BOM）

操作 ID: `ac9_cloth_sim.import_bom`

目的: 3D の服のソフトが書き出した `BOM.xml` から型紙と縫い目を読み込む。

前提条件:

- `BOM.xml` のファイルがある。
- 画面は **Setup** パネル（サイドバー、**AC9 Cloth Sim** タブ）。選んでいる物は問わない。

手順:

1. **Setup** の **Import BOM**（`ac9_cloth_sim.import_bom`）を押す。ファイル選択が出る。
2. `BOM.xml` を選ぶ。

期待結果: 現在のBOMの服の3D・配置・保存した配置形を破棄し、型紙が床に平らに並び、画面に「○ pieces, ○ seams」と出る。**Garment** の欄にその服が入り、**Lists** の **Seams** タブに縫い目が並ぶ。1 つの BOM に何着も入っていると、ピースは服のまとまりに分けられ、**Setup** の **Garment** の欄の下にまとまりごとの行が出る。縫い目の相手はまとまりの中で決まる。BOM の Hole の形はピースから切り抜かれ、共用の線でピースを自分に縫う偽の縫い目は取り込まれない。

制限・してはいけないこと: BOM には 1 着より多いピースが入っていることがある。使うピースの選び方とまとまりの直し方は「使うピースを選ぶ」の節。読み込みに失敗すると「Could not read the BOM: …」と出る（ファイルの種類が違う可能性）。

戻し方: Ctrl+Z。旧版Textへの切り替えだけでは破棄した3D・配置は復活しない。別服・外部jobファイルは保持する。

## 着装形を保ってBOMの縫い目を更新（Update BOM Seams）

操作 ID: `ac9_cloth_sim.seams_from_bom`

Setupの「BOM から縫い目を更新」を押す。保存された読み込み元BOMを使い、全ピースの型紙・識別子・辺/内部線参照の互換性を確認し、縫い目と自動分類だけを更新する。着装形・配置・オン/オフ・凍結・物性・役割・折り・手動分類/縫い目は保持する。型紙変更、消える縫い目の参照/手設定、改名した群の曖昧な統合/分割があれば変更せず理由を表示する。続くPhase Simが新しい縫い目を使う。新規Importは3D/配置を破棄する別操作なので、更新の代わりに案内しない。

## 型紙を読み込む（Import DXF）

操作 ID: `ac9_cloth_sim.import_dxf`、`ac9_cloth_sim.export_dxf`、`ac9_cloth_sim.import_svg`

目的: DXF-AAMA 形式の型紙を新しい服として読み込む。または今の型紙を DXF で書き出す。

前提条件: DXF-AAMA のファイルがある。書き出しは型紙が読み込まれていること。

手順:

1. **Setup** の **Import DXF** を押し、ファイルを選ぶ。
2. 書き出しは **Export DXF** を押し、保存先を選ぶ。

期待結果: 外周、角、内部線、合印、ピース名が入った新しい服ができる。報告に、読まなかったもの（地の目線、内側の切り抜き、ほかのサイズ）の数が出る。

制限・してはいけないこと: 縫い目は DXF に入らないので、読んだ服には縫い目が無い。縫い目の付け方は「縫い目を付ける・直す」の節。今の服のピースを差し替える読み方は無い。

SVG（描画アプリで描いた型紙など）は **Setup** の **Import SVG**。ほかの線の中にない閉じた線が 1 枚のピース、仕上がり線を囲む裁断線は読まない、外周に触れる 15 mm 以下の短い線が合印。大きさが px か単位なしのファイルは、ファイルを選ぶ画面の **Units**（Auto／Point／Pixel／Millimeter）で 1 単位を選べる。報告の「1 unit = ○ mm」で大きさを確かめる。縫い目は無い。

戻し方: Ctrl+Z。

## 型紙を読み込む（From Mesh）

操作 ID: `ac9_cloth_sim.from_mesh`、`ac9_cloth_sim.new_pattern`

目的: BOM の無い、Blender で作った服のメッシュから、型紙と縫い目を作る（UV の島 1 つが 1 ピース、2 つの島が接する辺が縫い目）。

前提条件:

- 画面: 3D ビュー、オブジェクトモード。
- 選ぶ物: 服のメッシュをアクティブにする（体とは別のオブジェクト）。**Placement** の **Body** に体が選ばれている（無いと **From Mesh** は灰色）。
- 服は厚みの無い面 1 枚で、袖口や裾は開いている。UV シームを入れて展開済み。
- 体は、まっすぐ立って −Y を向いている。

手順:

1. **Placement** の **Body** に体を選ぶ。
2. 服のメッシュを選ぶ（アクティブにする）。
3. **Setup** の **From Mesh**（`ac9_cloth_sim.from_mesh`）を押す。

期待結果: 床に平らな型紙が並ぶ。着た状態の服 `AC9_Garment_<名前>` ができ、元のメッシュは非表示になる。「○ pieces, ○ seams」と、体の中に入っている点の数が出る（入っている点は赤く塗られる）。続けて **Phase Sim** で落ち着く。

制限・してはいけないこと: 作った形のままで、布として落ち着いていない。UV が無いと UV が無い旨のエラーで止まる。体より先に服のメッシュだけを選んでいると押せない。

戻し方: Ctrl+Z。元のメッシュは非表示になっているだけなので、Outliner で表示し直せる。

空の服から描き始めるときは **Setup** の **New Garment**（`ac9_cloth_sim.new_pattern`）。

## 使うピースを選ぶ

操作 ID: `ac9_cloth_sim.select_garment`、`ac9_cloth_sim.rename_garment`、`ac9_cloth_sim.garment_from_selection`、`ac9_cloth_sim.piece_state`、`ac9_cloth_sim.invert_piece_selection`、`ac9_cloth_sim.enable_all_pieces`、`ac9_cloth_sim.refresh`

目的: BOM に入っている、1 着ぶんより多いピースから、使うピースだけを on にする。服のまとまりの分け方が違えば直す。

前提条件: 型紙が読み込まれている。画面は **Setup** パネルと Tool タブの **AC9 Piece**。

手順:

1. **Setup** の **Garment** の欄の下の、使うまとまりの行（`<名前> (on の数/ピースの数)`、`ac9_cloth_sim.select_garment`）を押す。そのまとまりのピースが選ばれる（Shift で足す）。まとまりが 1 つだけなら行は出ないので、3D ビューで使うピースを選ぶ。
2. Tool タブの **AC9 Piece** の **State** で **Active**（`ac9_cloth_sim.piece_state`）。
3. **State** の行の **Invert Selection**（`ac9_cloth_sim.invert_piece_selection`）で残りを選び、**Off All**。
4. まとまりの分け方が違う（オレンジの行、縫い目が別の服のピースや自分自身に付いている）ときは、まず **Update BOM Seams**（`ac9_cloth_sim.seams_from_bom`）。手動分類の訂正が必要な場合だけ正しい 1 着ぶんのピースを選びF3の **Garment from Selection**（`ac9_cloth_sim.garment_from_selection`）。そのまとまりと元のまとまりの BOM の縫い目が作り直される。名前は行の鉛筆（`ac9_cloth_sim.rename_garment`）。
5. 全部を戻すときは **All On**（`ac9_cloth_sim.enable_all_pieces`）。

期待結果: 使うピースだけが on（`13 / 20 pieces on` のように数が変わる）。off のピースは灰色の半透明になる。ピースの on／off では縫い目は変わらない。

制限・してはいけないこと: 状態のボタンは左右の対を一緒に変える（片側だけは Ctrl＋クリック）。F3の **Garment from Selection** はピースを選んでいないと「Select pattern pieces in the viewport first」と出て何も起きない。`ac9_cloth_sim.use_selected_pieces` はスクリプト用に残っているが画面には無いので、案内しない。型紙データの JSON を手で直したときは **Reload Spec**（`ac9_cloth_sim.refresh`）を押す。JSON が壊れていると赤い「The spec text is not valid JSON」が出る。

戻し方: **All On**、または Ctrl+Z。

## 型紙を直す（Whole Pieces・Edit Pieces）

操作 ID: `ac9_cloth_sim.use_tool`（道具に切り替える。`tool` に道具の ID。Whole Pieces は `transform_pattern_tool`、Edit Pieces は `edit_pattern_tool`、どちらも頭に `ac9_cloth_sim.` を付ける。道具の ID で、操作 ID ではない）、`ac9_cloth_sim.edit_pattern`（Edit Pieces に切り替える操作）、`ac9_cloth_sim.pattern_transform`（G・R・S、`mode='MOVE'|'ROTATE'|'SCALE'`）、`ac9_cloth_sim.pattern_edit`（K・X・C・N のキー）、`ac9_cloth_sim.pattern_drag`、`ac9_cloth_sim.pick_piece_under`（ダブルクリック）、`ac9_cloth_sim.context_menu`（何もしていないときの右クリック）、`ac9_cloth_sim.sync_3d`（Update 3D）、`ac9_cloth_sim.set_gap`、`ac9_cloth_sim.pattern_rotate`、`ac9_cloth_sim.pattern_scale`、`ac9_cloth_sim.duplicate_pieces`、`ac9_cloth_sim.symmetrize`、`ac9_cloth_sim.parallel_line`、`ac9_cloth_sim.add_dart`、`ac9_cloth_sim.move_dart`、`ac9_cloth_sim.spread`、`ac9_cloth_sim.cut_line`、`ac9_cloth_sim.piece_label`、`ac9_cloth_sim.mirror_copy`、`ac9_cloth_sim.symmetric_edit`、`ac9_cloth_sim.delete_piece`、`ac9_cloth_sim.delete_selected`、`ac9_cloth_sim.restore_pieces`

目的: 型紙の形を直す。直した形は、着せた服があれば、その場で服へ写る（README の『Pattern（型紙を直す）』）。

前提条件:

- 画面: 型紙を見る 3D ビュー（サイドバーの **Work Screen** で出る作業画面の真ん中）。オブジェクトモード。
- 型紙が読み込まれている。道具は **Pattern** パネルの **Whole Pieces**と **Edit Pieces**（または左の列。**Pattern Tools** がオンのとき）。
- 道具は 2 つに分かれている（2026-10-28〜29）。**ピースを丸ごと動かす・回す・拡大縮小するのは Whole Pieces。角・辺・内部線の点を動かすのは Edit Pieces。** Edit Pieces でピースの内側を引くと箱選択になり、ピースは動かない。利用者が「ピースが動かない」と言ったら、まず持っている道具を確かめる。

手順:

1. ピースごと動かす: **Pattern** の **Whole Pieces** を押して道具を持つ。ピースか内部線をクリックで選び（Shift で足す、何も無い所から引くと箱）、ドラッグで動かす。G（移動）・R（回す）・S（拡大縮小）は選んだ物の真ん中を基準にする。Shift+D（そのまま複製）、F2（名前）、X／Delete（選んだ物を消す）。
2. 形を直す: **Pattern** の **Edit Pieces** を押して道具を持つ。型紙の上で、角・辺・取っ手・内部線（端・途中の点・線全体）をドラッグする。クリックで選び、Shift＋クリックで足すと、選んだ全部を一緒にドラッグできる。ダブルクリックでピース全体を選ぶ。ドラッグ中は X／Y で軸、Shift で 0／45／90°、数字で長さ、Ctrl で吸着を切る。G／R／S（選んだ角・辺・線）、K（辺か内部線に点を足す）、C（辺か内部線の区間をカーブ／直線）、N（合印）、Shift+D（そのまま複製）、F9（直前の操作の数値）。S は拡大・縮小だけ。ダーツを足す・動かす、切り開き、線で切る、平行な線は右クリックのメニューにだけある（右クリック＝カーソルの下の物にできることの全部、キーは近道）。道具のヘッダーにはカーソルの下の物のキーだけが出る。全部は **Pattern** パネルの **Keys**（2 つの道具の両方）。
2b. 何もしていないときの右クリックは、カーソルの下の物のメニュー（`ac9_cloth_sim.context_menu`）。右クリックした物が先に選ばれ、項目はキーやボタンと同じ操作を呼ぶ。操作の途中の右クリックは取り消し。
2c. **AC9 Run** の **3D Follows Edits**（シーンのプロパティ `follow_edits`、既定オン）を外すと、型紙の直しは 3D に写らずに待つ（`sync_pending` が真、**Update 3D** が赤）。**Update 3D**（`ac9_cloth_sim.sync_3d`）か、次の Phase Sim・Live Sim・Local Sim で写る。
3. 距離をそろえる: 選んだ内部線・辺の Tool タブに周りまでの距離が並ぶ。距離は表示だけで、「数値で動かす…」（`ac9_cloth_sim.set_gap`）の小窓で、今の位置からの X・Y か、周りまでの距離を入れて動かす。
4. 左右ペアの片方を直すと、相方も鏡像で直る。ずれたときは片方を消し、もう片方を **Mirror Copy** する。**Mirror Copy**（`ac9_cloth_sim.mirror_copy`）は選んだ全部を縫い目ごと鏡に写す。
5. そのままの複製は **Selected Pieces** の **Duplicate** か Shift+D（`ac9_cloth_sim.duplicate_pieces`）。鏡にせず、左右ペアにもしない。複製したピースどうしの縫い目だけ一緒に写る。複製は置かれていないので Place で置く。
6. 左右対称の 1 ピース（1 枚で裁つ前身頃など）は Tool タブ **AC9 Piece** の **Symmetric Edit**。Rectangle／Ellipse の道具のヘッダーのチェック（既定オン）で、描いたときからオンにもできる。オンのままなら K・X・N、内部線（描く・動かす・消す）・折り・ダーツ・切り開き・縫い目も反対側へ鏡で入る（中心線をまたいで自分の鏡になる線は 1 本）。角を隣の角に落とすと 1 点にまとまる。中心線は `mirror_axis`（中心 x, y と角度）に持ち、R で回すと一緒に回る。中心線で切ると左右ペアでない 2 枚になる。崩れたら隣の **Symmetrize**（`ac9_cloth_sim.symmetrize`、`keep='LEFT'|'RIGHT'`、既定 LEFT）で反対側を作り直す。
7. 外周の辺か内部線に平行な内部線は右クリックの Parallel Line（`ac9_cloth_sim.parallel_line`、先に小窓。`distance` m・`count` 本・等間隔。辺なら `piece`・`edges` はカンマ区切りの辺 ID で内側へ、隣り合う辺は 1 本の線。内部線なら `line` に線 ID、`flip`・`both` で側、`extend` で端を外周まで）。
8. ピースの名前は Tool タブの **AC9 Piece** のいちばん上の欄か F2（`ac9_cloth_sim.piece_label`）。

期待結果: 型紙の形が変わり、着せた服があればその場で服へ写る（**3D Follows Edits** が外れていれば待つ）。置いた形のオブジェクト（Move by Hand・Part to Seam・Fold などの後）も、その場で新しい型紙の形に作り直す（頂点ごとに直したものは除き、そう出る）。写したか・写せなかった理由は Run の状態行に「Pattern edit: …」として残る。ピースを動かす・回すだけでは着た服は動かない。写せない形は、理由が出て止まる。続けて **Phase Sim**（「Phase Sim（シミュレーションする）」の節）で落ち着く。

制限・してはいけないこと: 型紙のピースのオブジェクトは鍵が掛かっている。Blender の G／R／S や標準の編集で動かさない。型紙データを JSON で直接変えると、着せた服へ写せずエラーになることがある。Blender の標準の X でピースのオブジェクトだけが消えたときは、**Lists** の **Pieces** タブの **Restore N Deleted Object(s)**（`ac9_cloth_sim.restore_pieces`）で戻す。

戻し方: Ctrl+Z。

## 型紙を描く（Draw Piece・Internal Line・図形）

操作 ID: `ac9_cloth_sim.draw_piece`、`ac9_cloth_sim.draw_line`、`ac9_cloth_sim.draw_shape`、`ac9_cloth_sim.delete_internal`、`ac9_cloth_sim.cut_out`（Convert to Hole／Fill Hole）、`ac9_cloth_sim.clone_shape`（Clone as Pattern。`sew=True` で +Sew）

目的: 新しいピース、内部線、長方形・楕円のピースや内部線を描く。

前提条件: 型紙の見える 3D ビュー。**Pattern** の **Piece** と **Internal** の各ボタン（または左の列）。内部線は既存のピースの上。

手順:

1. **Piece** の **Draw**（角を順にクリック、最初の角か Enter で閉じる）、**Rectangle**、**Ellipse**。
2. **Internal** の **Line**（ピースの上で点をクリックして Enter。3 点以上の後に最初の点をクリックすると閉じた図形）、**Rectangle**、**Ellipse**。
2b. 閉じた図形では、Tool タブの **AC9 Internal Line** と右クリックのメニューに **Convert to Hole**（中を穴にする、もう一度で埋める。外周に触れる・交わる、ほかの穴と重なる、折り・ゴムの付いた図形は断られる）、**Clone as Pattern**（同じ形の新しいピース。生地は元のピース、縫い目なし、置かれていない、元のピースの右）、**Clone as Pattern (+Sew)**（さらに新しいピースの辺を図形に縫い付け、元のピースが置いてあれば 3D でもその上に付ける）が出る（Clone の 2 つは右クリックのメニューだけ）。
3. 数字を打つと寸法を指定できる（シーンの長さの単位）。Shift で 15° 刻み。Backspace で 1 歩戻る。

期待結果: ピース、または内部線が型紙に増える。

制限・してはいけないこと: 内部線を描くとき、点がピースの外にあると描けない。ピースを消すとその縫い目・配置・折り線も消える（**Selected Pieces** の **Delete**、`ac9_cloth_sim.delete_piece`。確認が出る）。

戻し方: Ctrl+Z。

## 縫い目を付ける・直す

操作 ID: `ac9_cloth_sim.edit_seams`、`ac9_cloth_sim.seam_click`、`ac9_cloth_sim.seam_list_pick`、`ac9_cloth_sim.seam_move_side`、`ac9_cloth_sim.seam_notch`、`ac9_cloth_sim.add_seam`、`ac9_cloth_sim.add_seam_mn`、`ac9_cloth_sim.match_length`、`ac9_cloth_sim.seam_edit`、`ac9_cloth_sim.seam_path`、`ac9_cloth_sim.seam_path_part`、`ac9_cloth_sim.pick_seam`、`ac9_cloth_sim.blend`、`ac9_cloth_sim.match_notches`、`ac9_cloth_sim.notches_from_bom`、`ac9_cloth_sim.close_dart`、`ac9_cloth_sim.fold_dart`

目的: 縫い目が正しいか確かめ、足し、直す（README の『Seams（縫い目）』）。

前提条件:

- 画面: 縫い目の選択と端のドラッグは、型紙の見える 3D ビューで **Edit Seams** を持って行う。着た服の上でも 3D の縫い目の線は選べる。
- 縫い目を選ぶと、Tool タブに **AC9 Seam** が出る。

手順:

1. **Lists** の **Seams** タブで行を押す（`ac9_cloth_sim.seam_list_pick`）か、**Edit Seams** で型紙の縫い目をクリックして選ぶ。Shift＋クリックで何本も選べ、そのときは **On**／**Off** と **Delete** だけが全部に効く。
2. 長さの差を直す: **AC9 Seam** の **Fit the length of** の、直したい側の **A** か **B**（`ac9_cloth_sim.match_length`）。どちらがどのピースかは、上の A・B の行と型紙の札で見る。
3. ねじれて縫われる: **Flip the direction of** の、その側の **A** か **B**（`ac9_cloth_sim.seam_edit` の FLIP_A／FLIP_B）。
4. 警告の確認: **Confirm**。縫わない: **Off**（戻すのは **On**）。消す: **Delete**。いずれも `ac9_cloth_sim.seam_edit`。
5. 縫い目を足す: **Add Seam**（`ac9_cloth_sim.add_seam`）で、片方のピースの始点・終点、次に相手のピースの始点・終点をクリックし、Enter。片側が複数区間なら **Add Seam (M:N)**（`ac9_cloth_sim.add_seam_mn`）で、側 A の区間を打って Enter、側 B も同じく Enter。
6. 側を付け替える: **Edit Seams** で M（`ac9_cloth_sim.seam_move_side`）。向きを逆にするのは F。
7. 合印: N（`ac9_cloth_sim.seam_notch`、Ctrl+N で片側）。**Lengths & Notches** の **Lay the notches of** の **A**／**B**（`ac9_cloth_sim.match_notches`）、BOM の合印は **Notches from BOM**（`ac9_cloth_sim.notches_from_bom`）。
8. 引き寄せ方: **Pull**（`ac9_cloth_sim.seam_path`。**Straight** か **Round: …**）、**Part**（`ac9_cloth_sim.seam_path_part`）。
9. 縫い目の折り: **AC9 Seam** の **Fold (deg)**（`scene.ac9_seam_fold`）と、回るピースの **Turns**（`scene.ac9_seam_fold_turn`）。値を変えると置いた形がその場で折れる。
10. ダーツの縫い目を選ぶと **Move**（`ac9_cloth_sim.move_dart`）、**Fold**（`ac9_cloth_sim.fold_dart`）、必要なとき **True Mouth**（`ac9_cloth_sim.close_dart`）が出る。縫い目の端のなめらかなつながりは **Blend**（`ac9_cloth_sim.blend`）。
11. BOM の縫い目を作り直す: BOM の縫い目は服のまとまりごとに持つ。**Setup** の **Update BOM Seams**（`ac9_cloth_sim.seams_from_bom`）で、着装形を保持して縫い目と自動分類を更新する。手動分類は保持する。ピースの on／off では変わらない。

期待結果: 一覧の行と型紙の縫い目の色が対応し、両側の長さと差が更新される。警告アイコンは確認や直しで消える。

制限・してはいけないこと: 縫い目の付け替えは、続きから **Phase Sim** すると新しく縫う扱いになり、布が最初の数コマで引き寄せられる（異常ではない）。**Pull** の **Round** は試験中の機能で、部位が無いと「No body part: pulled straight」と出てまっすぐになる。縫い目の一覧から消した BOM の縫い目は、**Update BOM Seams** を押すと戻る。縫い目を巻き尺に沿わせる欄（Tape／Hold）は今は画面に出ないので案内しない。

戻し方: Ctrl+Z。

## 使う体と置く目安を用意する（Detect Guides・Edit Guides）

操作 ID: `ac9_cloth_sim.detect_guides`、`ac9_cloth_sim.edit_guides`

目的: 体を指定し、型紙を置く目安（胴・首・腕・脚の円柱）を作る。

前提条件: 型紙が読み込まれている。アバターがまっすぐ立って −Y を向いている。

手順:

1. **Placement** の **Body** に体を選ぶ。
2. **Detect Guides**（`ac9_cloth_sim.detect_guides`）を押す。
3. ずれていれば **Edit Guides**（`ac9_cloth_sim.edit_guides`）を押し、円柱を G・R・S で直し、**Done** を押す。腕・脚は **L/R together** がオンなら対の側も付いてくる。

期待結果: 「○ guides; ○ / ○ pieces placed」が出る。円柱が 3D に出る（隣の目のアイコンで切り替え）。

制限・してはいけないこと: 体の向きや姿勢が違うと「upright, facing -Y?」と出る。**Detect Guides** をやり直すと、すでに置いてあった置いた形のオブジェクトは作り直しになる（手で動かした量は残る）。**Edit Guides** は **Done** を押すまで、円柱がオブジェクトのまま残る。

戻し方: Ctrl+Z、または **Detect Guides** をやり直す。

## 配置する（Place・Back to Flat）

操作 ID: `ac9_cloth_sim.place`、`ac9_cloth_sim.clear_placement`、`ac9_cloth_sim.use_tool`、`ac9_cloth_sim.unlink_pair`、`ac9_cloth_sim.pair`、`ac9_cloth_sim.swap_sides`

目的: 型紙のピースを体のまわりの円柱に置く。円柱の無い所では平らに置く（バッグなど体の無いもの）。

前提条件:

- 画面: 3D ビュー（作業画面の左の 3D ビュー）。オブジェクトモード。
- 円柱に置くときは **Detect Guides** が済んでいる（目安がある）。平らに置くだけなら体もガイドも要らない。
- 置くピースが選ばれている。

手順:

1. 左の列の **Place**（または **Placement** の **Place** ボタン。`ac9_cloth_sim.use_tool` で道具が切り替わる）を持つ。
2. 型紙のピースをクリックして選ぶ（Shift で追加）。
3. カーソルを円柱の上に持っていく（黄色に光る）。
4. 円柱の上で押し、そのまま動かして位置を決め、離す。置いたピース（3Dの鏡像ピースも）を押してドラッグすると、最初に触れた側を操作対象に固定し、押した所からの移動量だけ動かす（カーソルの下へ跳ばない）。相手だけが反対側へ従う。角度だけ・高さだけは Tool タブ AC9 Piece の Around／Height（`placement.move_placed`）。倒す角度は AC9 Piece の Tilt（spec placement の `tilt`、度。anchor を通るガイドまわりの線を軸に倒す。90 で上側が外へ水平、180 で上下逆、外へ倒れた部分は周方向に伸びる。`placement.set_tilt`）。`ac9_cloth_sim.flip_up_down` は +180。折りなど他は変えない。Esc／右クリックで取り消し。操作は `ac9_cloth_sim.place`（道具のクリックから始まる）。
5. 円柱の無い所で押すと平らに置く（spec placement `{'guide': 'flat', 'flat': {'o': [mm], 'R': [ex, ey, n]}, 'anchor'}`）。面の上なら型紙の法線を面の法線に合わせて 2 mm 浮かせ、何も無ければ Z = 0 に上向き。刺さりは自動で直さない。平らのピースを押してドラッグすると面（地面）の上を滑る。位置・回転は Tool タブ AC9 Piece の Location & Rotation（置いた形のオブジェクトの変形）。
6. **Back to Flat**（`ac9_cloth_sim.clear_placement`）は、選んだピースを 3D の今の位置・向きのまま、アンカーの接平面へ平らにする（平らの placement に書き換える）。手の移動は捨てる。3D から外すのは状態 Off。
7. 床: Run の **Floor (Z = 0)**（`scene.ac9_cloth_sim.floor`）。Z = 0 の下に厚み 20 mm の箱を当たりとして足す（シーンには何も足さない）。体・Collision・床・他の服が何も無いと、重力の無い段だけ回して止まり 1 行で知らせる。
8. 左右: **Pair**／**Unpair**（`ac9_cloth_sim.pair`）、**Unlink**／**Link**（`ac9_cloth_sim.unlink_pair`）。置いた対の左右を入れ替えるのは **Swap Sides**（`ac9_cloth_sim.swap_sides`。対の両方が on のとき）。

期待結果: 離すと、シミュレーションが実際に始まる形が置いた形として出て、`○ guides; ○ / ○ pieces placed` の数が増える。帯は自分の長さの輪で始まり、体に入るときだけ一様に広げて赤く示す。ほかのピースと重なったまま始まるピースも赤くなり、1 行で理由が出る（上に来る方を選んで **Move Outside**、と出る）。

制限・してはいけないこと: ピースを選ばずに押すと「click pattern pieces first」、型紙の上では「press on a guide, a surface or the ground」と出て始まらない。**Back to Flat** は選択ピースと連動中の鏡像を平らにし、未選択の着装形・型紙・縫い目を保持する。無選択のときだけ確認後に置いたピース全部。服オブジェクトだけなど対象ピースを特定できない選択は、全体操作に広げず変更前に拒否する。Ctrl+Zで戻せる。体の凹凸をなぞって伸ばすことはせず、置いた所から勝手に動かすこともしない。**Swap Sides** は、相手へ運んだ形・縁に載せた形・Edit Mode の直しを作り直す（手の動きと折りは場所の側に残る）。着せた服の上では、そのピースが置いた形に戻る。

戻し方: Ctrl+Z。

### 置いた形を整える（Part to Seam・Band on Edge・Move Outside・Settle）

操作 ID: `ac9_cloth_sim.move_to_seam`、`ac9_cloth_sim.lay_on_edge`、`ac9_cloth_sim.move_outside`、`ac9_cloth_sim.settle_placed`

目的: シミュレーションの前に、置いた形を物理計算なしで整える（README の『Placement（体のまわりに置く）』の「置いた形を整える道具」）。

前提条件: 画面は **Placement** パネル。整えるピースが選ばれ、シミュレーション中でない。**Move Outside** は現在の配置形・着た形をそのまま扱うため、**Back to Placed** は不要。他の3つは整えるピース自身が配置形であること（着た服の一部なら先に **Back to Placed**）。相手の状態は問いません。

手順:

1. ピース（衿・台衿・カフス・ポケットなど）に折り設定を反映してから、縫う点を今回指定した相手との有効な縫い目の点の上へ乗せ、縫う点を固定して整える（Settle と同じ）: **Part to Seam**（`ac9_cloth_sim.move_to_seam`）。相手は今ある所（置いた形、着た服、円柱の順）。起動後に相手をクリックして指定する。Shiftクリックで追加・解除、Aで全相手、Enterで適用、Escで取消。相手の指定は今回だけで、ピースや縫い目のOn/Offと次Simの縫合設定は変更しない。移動側と追従ペアは起動時に固定して表示し、寄せ先選択でactiveを変更しない。伸びは数と「オーバーレイ > 伸び」で示し、断るのは三角が潰れるときだけ。外側へは出さない（必要なら 3 の Move Outside を別に押す）。寄せる前の手配置・頂点編集・折りは別に保持し、繰り返しても基準を上書きしない。
2. 細い帯（衿リブ）を指定相手の縫い付け線に沿わせ、幅方向も含めて配置する（相手指定は1と同じ）: **Band on Edge**（`ac9_cloth_sim.lay_on_edge`）。
3. 重なったピースを外側へ出す: **Move Outside**（`ac9_cloth_sim.move_outside`）。選んだ現在形を保ち、相手の表側へ移します。自分・相手とも配置形／シミュレーション後の形で利用でき、縫い合わせは不要。補正後の形から再Simできます。表側が曖昧、または途中で別の層や人体を通り抜ける場合は理由を表示して無変更です。
4. 体から出す・相手の側へ載せる・辺の長さを戻す: **Settle**（`ac9_cloth_sim.settle_placed`）。1 と 2 も運んだあとに同じ整えを行う。

期待結果: 置いた形が変わり、残った所は赤く示される。Part to Seam の報告は 1 ピース 1 行で、相手の名前・隙間・伸び（赤が残れば体の中と貫く所の数）が出る。

制限・してはいけないこと: どれも自動では動かない（押したときだけ）。押せないときは灰色で理由が出る（「Select a piece first (on the pattern)」「The selected piece is not placed: Place it first」「… is part of the dressed garment: Back to Placed it first (Placement)」）。

戻し方: Ctrl+Z。**Move Outside** は現在形に対する編集で、配置形なら **Back to Guide** で手配置とともに戻せます。着た形の補正は **Back to Placed** で元の配置形へ戻ります。

## 配置を手で直す（Move by Hand・Back to Guide）

操作 ID: `ac9_cloth_sim.move_by_hand`、`ac9_cloth_sim.show_placed`、`ac9_cloth_sim.reset_placed`、`ac9_cloth_sim.set_pose`、`ac9_cloth_sim.mirror_pose`

目的: 置いた形をオブジェクトとして出し、G・R で動かす、または Edit Mode で直す。手で直さなくても **Phase Sim** は同じ始まりの形を作るので、微調整が要るときだけ使う。着せた後は、着た服のピースにつまみを付けて動かす。

前提条件: ピースが置かれている。**Body** が選ばれている。シミュレーション中でない。着せた後は、型紙か着た服で動かすピースを選んでおく（無いと「Click a piece on the pattern or the garment first」）。

手順:

1. **Placement** の **Move by Hand**（`ac9_cloth_sim.move_by_hand`。着せる前は置いた形のオブジェクトを出す `ac9_cloth_sim.show_placed` と同じ）を押す。選んだピースが選ばれ、移動の道具に切り替わる。
2. 置いた形のオブジェクトを選んで G・R などで動かす。数値で動かすなら **Type Move**（`ac9_cloth_sim.set_pose`）。対の相手へ鏡で写すのは **Mirror Placement**（`ac9_cloth_sim.mirror_pose`）。
3. 手直しを捨てるなら、そのピースを選んで **Back to Guide**（`ac9_cloth_sim.reset_placed`）。選ばずに押すと全部（確認が出る）。着た服のピースは先に置いた形に戻る。

期待結果: 置いたピースのオブジェクトが現れ、Move と Turn の値が出る。続く **Phase Sim** がその形から始まる。

制限・してはいけないこと: 着せた後の手直しは、着た服のピースのつまみを G・R で動かす（README の『Placement（体のまわりに置く）』の「着せた後の操作」）。**Back to Placed** で戻したピースや着せた後に置いたピースは、つまみではなく置いた形のオブジェクトが選ばれる。Edit Mode で頂点まで直した分は、作り直すと残らない（動かした量は残る）。

戻し方: Ctrl+Z、または **Back to Guide**。

## シミュレーションする（Phase Sim）

操作 ID: `ac9_cloth_sim.update_3d`（画面の **Phase Sim**）、`ac9_cloth_sim.dress`（内部の本体。**Live Sim** は mode が LIVE、**Local Sim** は LOCAL）、`ac9_cloth_sim.log`、`ac9_cloth_sim.delete_cache`

目的: 縫って着せる。または、型紙を直した後・手で直した後の形から続けて、服全体を落ち着かせる（README の『Run（Phase Sim・Live Sim・Edit Frame）』）。

前提条件:

- 画面: Tool タブの最上段 **AC9 Run**。
- **Body** が選ばれ、**Detect Guides** で目安があること（無いとボタンは灰色）。
- 3Dに配置済みのピースだけを計算する。未配置のピースとそれにつながる縫い目はその回の計算から外れ、型紙のオン／オフ設定は変わらない。配置済みが0枚なら計算対象がないことを表示する。
- Preferences の **Newton Python** が使える状態であること（無いと「Set the Newton Python in the add-on preferences」）。
- テスト用の .blend は別名で保存済み。**Watch** を使うなら **Phase Sim** を押す前にオンにする。

手順:

1. 必要なら、生地・点の間隔を **AC9 Garment** で決める（「生地を選ぶ」の節）。
2. **AC9 Run** の **Phase Sim**（`ac9_cloth_sim.update_3d`）を押す。本人が押す。
3. 計算中は服が動き、進み具合のバーと段・コマが出る。止めるときは **Stop** に変わったボタンをもう一度押すか **Esc**。
4. 終わったら、**AC9 Garment** の **Result** を確かめる。

期待結果: 「done: …」が状態表示に出て、経過がタイムラインに入り、最後のコマが結果になる。**Result** に、日時とコマ数、`Frames: on the timeline` と保存の状態（`Shape: …`）が出る。

制限・してはいけないこと:

- 押してから最初の約 10 秒はソルバーの起動（`starting the solver`）。
- 2 回目からは「画面に出ているコマ」から続ける。タイムラインの位置を確かめてから押す。
- 始まらなかったときは、状態表示に `… did not start: <理由>` が残る。型紙を JSON で直接変えて写せないとき「changed outside the drafting tools …」と出ることがある。
- 結果は回ごとに揺れる。良し悪しを 1 回で決めない。赤い塗りは「候補」。
- ほかの服をシミュレーションすると、前の服の経過は消えて最後の形だけが残る。**Keep Cache**（**AC9 Run** とアドオンの設定）がオフなら、毎回最後の形だけが残る。**Delete Cache**（`ac9_cloth_sim.delete_cache`）は経過のコマを消す（戻せない。AI は押さない）。
- 最後の形は .blend の中に残る。ジョブのフォルダが無いと **Result** は `Result: folder gone` になるが、**Phase Sim** はその形から続く。
- AI は押さない。状態表示の `done:` の行、`stopped`、エラー文、**Log**（`ac9_cloth_sim.log`、**Details** の中）を読み上げる。

戻し方: **Stop**／**Esc** で止めた所までが残る。**Back to Placed** で置いた形に戻す。Ctrl+Z。

## Live Sim・Local Sim と Stop

操作 ID: `ac9_cloth_sim.dress`（mode は LIVE か LOCAL）、`ac9_cloth_sim.fold_local_sim`

目的: **Live Sim** は、画面の形から止めるまで回り続け、布を手でつまんで直したり、設定を変えながら様子を見る。**Local Sim** は、選んだピースだけを止めるまで動かす。

前提条件: **Phase Sim** と同じ（**Body**、目安、**Newton Python**）。**Live Sim** は一度も着せていなくても押せる（置いた形から重力ありの 1 段）。**Local Sim** は動かすピースを選んでおく（無いと「Local Sim did not start: select the pieces to simulate first」）。

手順:

1. **AC9 Run** の **Live Sim** か **Local Sim** を押す（本人が押す）。押したボタンが **Stop** に変わる。重力は、それぞれの下の **Gravity**（**Live Sim** は既定オン、**Local Sim** は既定オフ。回っている間も切り替えられる）。
2. 10〜15 秒ほど待ち、布が動き始めてから、3D ビューで布を Ctrl＋左ドラッグしてつまむ。**Freeze** と **Off** のピースはつかめない（つかむと、そう出る）。布が布を突き抜けているときは、ドラッグ中に Shift を押すと、つかんだ布がほかの布をすり抜ける（押している間だけ。Shift を押しながらつかみ始めると、マウスの下のピース 1 枚だけ）。
3. 回っている間に変えるとその場で効くもの: 内部線・縫い目の折りの角度、縫い目の **On**／**Off**、ピースの **Freeze**、ピースの **Elastic**、**Gravity**。変えた値はピースの設定として残る。
4. 止めるには、もう一度同じボタン（**Stop**）か **Esc**。

期待結果: 止めた所が結果になり、**Result** に `Live Sim`（か `Local Sim`）と出る。コマ数の上限は無く、経過のコマは残らず最後の形だけが残る（`Frames: dropped (shape kept)`）。**Local Sim** では、選んでいないピースは回っている間 **Off All** と同じ扱い（動かず当たらない）で、選んだピースどうしの縫い目だけが引く。着せた服があれば結果はその服の形、着せる前なら置いた形に残る。

制限・してはいけないこと: **Esc** か **Stop** を押すまで止まらない。計算中に布をつかめるのは **Live Sim** と **Local Sim** だけ。布の無い所で Ctrl＋クリックしても何も起きない。生地の値など、上の 3 以外の変更は次に押したときから効く。置いた形で折れない線の欄の下に出る **Local Sim**（`ac9_cloth_sim.fold_local_sim`）は、そのピースだけの **Local Sim**。

戻し方: Ctrl+Z。

## Back to Placed と Edit Frame

操作 ID: `ac9_cloth_sim.reset`、`ac9_cloth_sim.edit_frame`

目的: **Back to Placed** は、選んだピース（選ばなければ有効な配置済みの全部）を、Part to Seam前の準備形へ戻す。縫い目へ寄せた影響を取り除き、手配置・頂点編集・折りを残す（シミュレーションはしない）。**Edit Frame** は、画面のコマを服の形にして手で直せるようにする。

前提条件:

- **Back to Placed**: 服（着せた結果、または置いた形のオブジェクト）がある。シミュレーション中は押せない。ピースだけ戻すなら、型紙をクリック（**Place** 道具）か、**Place** 道具で着た服のピースをクリックして選んでおく。
- **Edit Frame**: 着せた結果がある。経過の再生が有効なときか、**Live Sim** などの後で服の形がもう最後の形のとき（そのときは「the garment's mesh is its shape」と出て、そのまま直せる）。

手順:

1. **Back to Placed**（`ac9_cloth_sim.reset`）を押す。選んだピースがあれば、そのピースだけが置いた形に戻る（パネルに「Next: ○ piece(s) from placed」）。何も選ばなければ、確認のあと服全体の結果を外す。
2. 戻したピースを、つまみで G・R で動かしてよい。
3. **Edit Frame**（`ac9_cloth_sim.edit_frame`）を押し、Edit Mode やスカルプトで形を直す。
4. どちらも、続けて **Phase Sim**（**Edit Frame** の後は **Live Sim** も）を押すと、その形から続く。

期待結果: **Back to Placed** では、押した瞬間に着た服の中のそのピースだけが置いた形になる。**Edit Frame** では、タイムラインを動かしても形が変わらず、`Frames: off (shape kept)` と出る。

制限・してはいけないこと: どちらも経過の再生を止める。何も選ばない **Back to Placed** は服全体の結果を外す（確認が出る。ジョブのフォルダは残る）。寄せる前の形がない古いファイルや、型紙・ペアが変わって復元できない場合は理由を表示し、失敗時は形と基準を保つ。復元不能を推測で補わない。変更のUndo、または手移動を捨てるBack to Guideで再配置できる。**Edit Frame** は AI が押さない。

戻し方: Ctrl+Z（経過に戻る）。

## ピースの状態を変える

操作 ID: `ac9_cloth_sim.piece_state`、`ac9_cloth_sim.select_piece`、`ac9_cloth_sim.invert_piece_selection`、`ac9_cloth_sim.collider`、`ac9_cloth_sim.collider_select`

目的: ピースごとに **Active**、**Freeze**、**Off Pattern**、**Off All** を決める。ほかの服や小物を障害物にする。

前提条件: 型紙が読み込まれている。画面は **Lists** の **Pieces** タブ、またはピースを選んだときの **AC9 Piece** の **State**。

手順:

1. **Lists** の **Pieces** タブで、ピースの行の右の 4 つのアイコンを押す（行の名前を押すとそのピースが選ばれる、`ac9_cloth_sim.select_piece`）。服の行の 3 つのアイコンはその服の全ピースをまとめて切り替える（**Off All** は除く）。
2. 選んだピースをまとめて切り替えるなら、**AC9 Piece** の **State**。残りを選び直すのは同じ行の **Invert Selection**（`ac9_cloth_sim.invert_piece_selection`）。
3. 障害物は、**Collision** タブで物を選んで **Add Selected**（`ac9_cloth_sim.collider`）、やめるのは **Remove**。行の名前を押すとその物が選ばれる（`ac9_cloth_sim.collider_select`）。

期待結果: ピースの状態がアイコンの押された表示と型紙の見え方（**Off** は灰色の半透明）に出る。重ね着では、結果を持つほかの服が自動で障害物になる。

制限・してはいけないこと: 状態のボタンは左右の対（鏡のペアと **Pair** の対）を一緒に切り替える。片側だけは Ctrl＋クリック。**Off All** のピースは一覧に数だけ出る。戻すときは **Edit Pieces** で灰色のピースをクリックして選び、**Active** にする（戻したピースは残った側の鏡の形にそろう）。

戻し方: 同じボタンで **Active** に戻す。Ctrl+Z。

## 生地を選ぶ

操作 ID: `ac9_cloth_sim.fabric_new`、`ac9_cloth_sim.fabric_preset`、`ac9_cloth_sim.fabric_edit`、`ac9_cloth_sim.fabric_default`、`ac9_cloth_sim.fabric_remove`、`ac9_cloth_sim.fabric_select`、`ac9_cloth_sim.interlining_new`、`ac9_cloth_sim.interlining_preset`、`ac9_cloth_sim.interlining_edit`、`ac9_cloth_sim.interlining_remove`、`ac9_cloth_sim.interlining_select`、`ac9_cloth_sim.piece_mesh`、`ac9_cloth_sim.layers_to_zero`、`ac9_cloth_sim.set_role`、`ac9_cloth_sim.propose_roles`、`ac9_cloth_sim.set_fold`

目的: 服の既定の生地、ピースごとの生地・芯地・見た目・厚み・レイヤー・点の間隔・縮み、役割、折りを決める（README の『生地と Garment の設定』）。

前提条件: 型紙が読み込まれている。服の設定は何も選ばない状態（Tool タブの **AC9 Garment**）、ピースの設定はピースを選んだ状態（**AC9 Piece**）。

手順:

1. 服の既定の生地: **AC9 Garment** の **Default Fabric**。点の間隔: **Particle Distance (mm)**。
2. 生地の一覧: **Lists** の **Fabrics** タブ。行を押すと、その生地のピースが選ばれ（`ac9_cloth_sim.fabric_select`）、Tool タブに **AC9 Fabric** が出る。そこで **Preset**（`ac9_cloth_sim.fabric_preset`）、**Edit...**（`ac9_cloth_sim.fabric_edit`）、**Make Default**（`ac9_cloth_sim.fabric_default`）、**Select Pieces**、**Delete**（`ac9_cloth_sim.fabric_remove`）。一覧の下の **New**（`ac9_cloth_sim.fabric_new`）で、既定の生地と同じ数値の生地を足す。
3. 芯地の一覧: 同じタブの下半分。行を押すと **AC9 Interlining**（**Preset**、**Edit...**、**Select Pieces**、**Delete**）。**New**（`ac9_cloth_sim.interlining_new`）で足す。芯地は服ごとの一覧で、直すとその芯地の全ピースに効く。
4. ピースの生地と芯地: **AC9 Piece** の **Fabric** と **Interlining** の欄（スクリプトからは `scene.ac9_cloth_sim.piece_fabric`、`piece_bond`）。右の鉛筆は **Edit...** と同じ画面。
5. ピースの見た目 **Look**、厚みの表示 **Thickness**、**Layer**（0〜9。高い方を低い方の外側に保つ。全部を 0 に戻すのは右の矢印 **All Layers to 0**、`ac9_cloth_sim.layers_to_zero`）、点の間隔 **Distance (mm)**（左の矢印で服の値に戻す、`ac9_cloth_sim.piece_mesh`）、縮み **Elastic**。どれも **AC9 Piece** の欄。Layer は一時的な順序補助として、内外を確認したら停止→All Layers to 0→Live Sim で再計算する。変更は次のシム開始から有効。深い食い込みの追加回復は異層の間だけで、全0の再計算では起動しない。本人の指示なしにAIが自動解除しない。
6. 役割は **Piece Roles** の **Propose Roles**（`ac9_cloth_sim.propose_roles`）か、**AC9 Piece** の **Role**（`ac9_cloth_sim.set_role`）。
7. 折りは、型紙の内部線をクリックして出る **AC9 Internal Line** の **Fold Angle**（角度）・**Direction**（Mountain／Valley、布の表から見て）・**Turns**（置いた形で動く側 **Side A**／**Side B**）・**Layers**（**Both**／**A only**／**B only**。片側だけを計算し、反対側は 2 層目として付いていく）。スクリプトからは `scene.ac9_iline_fold`（符号つきの角度、＋＝Mountain）のままでも書けます。値を書くと置いた形がその場で折れます（初めてでも。左右ペアの相方の線にも入る）。折れなかった理由は `place_fold.why_not(piece, line)`。縫い目は `scene.ac9_seam_fold` と `scene.ac9_seam_fold_turn`（'A'／'B'＝縫い目の a／b のピースが回る）。

期待結果: 生地の一覧と、ピースの生地の割り当てが変わる。型紙に **Show Fabric Colors** で色が出る。何枚か選んでいて値が違う欄は「(mixed)」と赤く出て、変えると全部に効く。

制限・してはいけないこと: 値の変更は次の **Phase Sim** から効く（**Live Sim** 中にその場で効くのは、折りの角度・縫い目の On／Off・Freeze・Elastic・Gravity だけ）。既定の生地は消せない（先に別の生地を既定にする）。**Layer** は自動では 0 に戻らない。AI が値を変えるときは、本人が頼んだときだけにし、変える前の値を答えに書いておく。**Settings:** という赤い行が出ているときは、試験用の設定のセットが生地や **Watch** を固定していて、値を変えても効かない。

戻し方: Ctrl+Z。

### Strengthen（強化）

- 目的: 選んだピースの伸び・せん断・曲げを人が指定する倍率で硬くする。芯地とは別。
- 場所: Properties → Tool → AC9 Piece → Strengthen（強化）と Strength Factor（強化倍率）。型紙か 3D のピースを選ぶ。
- スクリプト: `scene.ac9_cloth_sim.piece_strengthen` と `piece_strengthen_factor`。現在の選択と左右の対へ同時適用。spec は `pieces[].strengthen`（未指定 false）と `strengthen_factor`（未指定 5、範囲 1〜100）。
- Live 中のオン・オフと倍率変更は次に値を受け取るコマから効く。設定は spec/.blend に残り、停止しても解除しない。Off は倍率を消さない。
- 紫の色が目印（Freeze/Off の色が優先）。倍率 1 は硬さ不変。重さ・自然形・厚みは変わらない。高倍率は縫い寄せにも抵抗するので、必要なら解除して落ち着かせる。形が必ず改善するとは案内しない。
- 色には Overlays → Piece Colors が必要。End Taper 併用で Live 中に強化を実変更した回は、次の Live が見た形から再構築される。見た形と属性は保存される。強化が変わらない回と、End Taper がない回の完全状態保存にはこの制限を加えない。

## 採寸する

操作 ID: `ac9_cloth_sim.measure_body`、`ac9_cloth_sim.measure_garment`、`ac9_cloth_sim.measure_reset_levels`、`ac9_cloth_sim.measure_move_level`、`ac9_cloth_sim.measure_tape`、`ac9_cloth_sim.measure_tape_remove`、`ac9_cloth_sim.measure_pattern`、`ac9_cloth_sim.measure_pattern_remove`、`ac9_cloth_sim.measure_set_target`、`ac9_cloth_sim.measure_highlight`、`ac9_cloth_sim.measure_select`、`ac9_cloth_sim.measure_delete`、`ac9_cloth_sim.measure_mirror`

目的: 体の寸法、巻き尺、服のゆとり、サイズ表の目標との差を見る（README の『Measure（採寸）』）。

前提条件:

- 画面: サイドバーの **Measure** パネル（普段は閉じている。開く）。
- **Body** に体が選ばれ、体はまっすぐ立って −Y を向いている。

手順:

1. **Measure Body**（`ac9_cloth_sim.measure_body`）を押す。表の **Body** の列が埋まる。
2. 道具箱の **Edit Measure**（`ac9_cloth_sim.measure_move_level`）で、測った線をドラッグして動かす。**Around**、**Along**、**Straight**（`ac9_cloth_sim.measure_tape`）で巻き尺を置く。Enter で残す（**Along**）。
3. 型紙の長さは **Size Check** の **Measure Pattern**（`ac9_cloth_sim.measure_pattern`）で、外周の 2 点をクリックする。行の × は `ac9_cloth_sim.measure_pattern_remove`。
4. 着せたあとは **Measure Garment**（`ac9_cloth_sim.measure_garment`。**Phase Sim** と **Live Sim** のあとは自動で測る）。表の **Cloth** と **Ease** が埋まる。
5. 目標は **Size Check** の項目名を押して数値を入れる（`ac9_cloth_sim.measure_set_target`）。
6. 採寸を選ぶのは 3D の線のクリック、または **Lists** の **Tapes** タブの行（`ac9_cloth_sim.measure_select`）。選ぶと **AC9 Measurement** が出て、**Mirror**（`ac9_cloth_sim.measure_mirror`）と **Delete**／**Back to Its Level**（`ac9_cloth_sim.measure_delete`）が使える。

期待結果: 表にシーンの長さの単位（**Setup** の **Units**: mm／cm／m／in、`ac9_cloth_sim.set_units`）の値が出る。**Cloth** は服が覆っていなければ「open」。項目名（`ac9_cloth_sim.measure_highlight`）を押すと、体のどこで測ったかが光る。

制限・してはいけないこと: **Measure Garment** は画面に出ているコマを測る。途中のコマを測ると「Cloth: frame N」と出る。**Tape Straight** は体から離れる所があるので、縫い目を沿わせる相手にはならない。

戻し方: 巻き尺は **Delete**。表の右下の戻しアイコン（`ac9_cloth_sim.measure_reset_levels`）で測った線の位置が最初に戻る。Ctrl+Z。

## To AC9 Cloth Retopo

操作 ID: `ac9_cloth_sim.export_retopo`

目的: 着せた服から、Cloth Retopo に渡す Guide `AC9_Guide_<服の名前>` を作る。

前提条件:

- 着せた結果がある（無いとボタンは灰色）。
- 画面: **AC9 Run** の下の状態表示の **Details** を開く（折りたたまれている）。
- タイムラインを最後のコマにしてある（画面に出ているコマが使われる）。

手順:

1. タイムラインを最後のコマに合わせる。
2. **Details** の **To AC9 Cloth Retopo**（`ac9_cloth_sim.export_retopo`）を押す。本人が押す。

期待結果: 画面に、頂点数と、縫い目がどれだけ開いていたか（閉じる前）が出る。Cloth Retopo が有効なら、その Guide に自動で設定される。

制限・してはいけないこと: 同じ名前の Guide があれば作り直される。途中のコマで押すと、そのコマの形で Guide ができる。AI が押さない。

戻し方: Ctrl+Z。

## 症状から探す

README の『うまく動かないとき』の表を土台に、今のコードの表示で確かめ直した補足です。

最初に見ること（画面で見えるものだけ）:

| 症状 | 最初に見ること | してよいこと | しないこと |
|---|---|---|---|
| Tool タブに **AC9 Run** や **AC9 Garment** が無い | Properties エディタが Tool タブか。 | 場所を案内する。 | Preferences を AI が書き換える。 |
| **Phase Sim** が灰色 | **Placement** の **Body**。**Detect Guides** をしたか。 | **Body** と **Detect Guides** を案内する。 | 体を選び直して押す操作を AI がする。 |
| 「Set the Newton Python in the add-on preferences」 | Preferences の **Newton Python** | 指定を案内する。 | パスを推測して書く。 |
| 赤が出ない | **Overlays** の目のボタンと **Diagnose > Problems** がオンか。**Watch** を **Phase Sim** の前に入れたか。 | 表示の設定を、本人が頼めば確認付きで代行してよい。 | 後から **Watch** を入れて今回の結果に効くと言う。 |
| 赤が出た | 状態表示の「the first (marked red): …」を書き写し、その場所の画像を撮る。 | 画像を見せ、本人に判断してもらう。 | 「異常です」「不良です」と言い切る。 |
| 布が交差した・巻き込んだか知りたい | **Details** の **Log**（`ac9_cloth_sim.log`）の `guard` の行。 | ログを読み上げる。 | 件数の意味を断定する。 |
| 経過が動かない・**Edit Frame** が灰色 | **Edit Frame** か **Back to Placed** の後なら正常。 | 正常と伝える。Ctrl+Z で経過に戻る。 | 押していないのに動かないなら、古い .blend や一時フォルダの消失を疑い、記録して知らせる。 |
| 衿が裏返る・つなぎ目がねじれる | **Piece Roles**（衿の役割が合っているか）、**Flip the direction of**、置き方。 | **Propose Roles** の結果を見せる。手で直すなら **Edit Frame**。 | 画面から変えられない縫う順番や置く角度を、勝手に変える。 |
| 新しい服が最初から崩れる | その場で **Esc**。どの段・何コマ目か。 | 状態表示を記録する。 | **Esc** を AI が押すこと自体は案内に留め、押させる。 |
| 最後の段で服がずっと動き続ける | 状態表示の段名と、最後の数コマで動いているか。 | **Esc** か **Stop** で止める案内。 | 計算の設定の変更。 |
| 結果が回ごとに違う | 同じ設定でも揺れる。 | 何度か回して見るよう案内する。 | 1 回で良否を言う。 |
| 結果の形が残っていない | **AC9 Garment** の **Result** の `Shape:` の行 | 保存（Ctrl+S）を案内する。 | AI が保存する。 |
| 画面の表記が資料と違う | アドオンが古く読み込まれたまま。 | Blender の再起動を案内する。 | 資料の名前を作って案内する。 |

利用者がお知らせするときに添えてもらうもの: 状態表示の `done:` の行（または `stopped` とエラー文）、押したボタンと入れた値、**Log** の内容、保存した .blend。数値は、利用者の画面で確かめた値と、開発側の実測を混ぜない。
