# AC9 Cloth Sim 手順の補足（AI 向け）

この資料は、README.md の説明を複製しません。共通の説明は「README の『<見出し>』」と節名で参照します。ボタン・パネルの名前は画面の英語表記のままです。操作 ID は、コードの `bl_idname` で確かめたものだけを書きます。操作 ID が資料にあっても、AI に操作の権限が加わるわけではありません。

折りの案内: `docs/ja/features/fold-roles.md` を参照。左右のミラーペアの内部線は追加/移動/曲線編集/削除/折り/ゴムを同じ保存対応で同期する。配置だけのUnlinkと型紙の同期は別。古い片線を準備中に見えない線で補完しない。不一致は別メッシュで保持し、古い共有形の対応不明なA/Bは理由付きで拒否する。中央軸上の線は1回、軸外の鏡像2線は各1回適用し、No Sideでは今回の対称な折りをまとめて戻す。無関係な後続の折りや頂点編集は保護する。

人向けの正本は docs/ja/before-start.md（Docusaurusの「使い始める前に」）。同じ版のインストール済み本体のLLM/README.mdと必要ならprocedures.mdをAIへ添付する。ダウンロードZIPの保持は前提にしない。作業前に.blendを別名保存し、経過も必要なら隣のAC9_ClothSim_jobsを別途保持する。Keep Cacheは既定オン、Keep All Simulate Framesは既定オフ、途中記録上限は1,500。この0.4.1候補ではSpace（Blender側でツール/検索ならShift+Space）が開始/停止の既定。既存のカスタム割当はPreferencesのHotkeysで確認。画像・動画の説明と型紙/服の外観の承認を混同しない。

## 案内上の注意と画面の場所

目的: 利用者に操作を案内するときの、場所の言い方と、AI がしてよいこと・してはいけないことを決めておく。

画面の場所（新しい既定の配置。README の『画面の配置』）:

- 3D ビューのサイドバー（N パネル）の **AC9 Cloth Sim** タブ: **Setup**、**Pattern**、**Lists**、**Placement**、**Measure**。
- 右下の Properties エディタの **Tool** タブ: **AC9 Run**（常に最上段）、**AC9 Garment**、**AC9 Piece**、**AC9 Seam**（下に **Lengths & Notches** と **Blend**）、**AC9 Internal Line**、**AC9 Edges**、**AC9 Fabric**、**AC9 Interlining**、**AC9 Button**、**AC9 Measurement**。最後に触った物で切り替わる。何かを選んだのに該当パネルが出ないときは、ツールタブを開いているかを確かめる。
- サイドバーの **Lists** のタブは 6 つ（**Seams**、**Pieces**、**Fabrics**、**Collision**、**Tapes**、**Fasteners**）。一覧は見て選ぶだけで、設定は ツールタブ。
- 画面の言語: Blender が日本語表示なら、ボタンは日本語の訳で出る。案内では英語の名前と、日本語表示の利用者には画面の日本語の両方が通じるように言う（訳は `i18n_ja.py`）。
- 左の道具の列: 型紙の道具は **Setup** の **Pattern Tools** がオンのときだけ出る。**Edit Measure**・**Tape Around**・**Tape Along**・**Tape Straight**・**Place** は常に出る。
- 3D ビューのヘッダー右上: **Overlays**（目のボタンと一覧）。
- 型紙は、サイドバーの **Work Screen** で出る作業画面の真ん中の 3D ビューで直す。

してよいこと: 状態・選択・モードを読む。画面を撮って見せる。次の操作と確認する点を短く案内する。状態表示の文字と `ac9_job` を読み上げ、記録する。

してはいけないこと:

- 長いモーダル処理や結果を変える処理（`ac9_cloth_sim.update_3d`、`ac9_cloth_sim.dress`（**Simulate**）、`ac9_cloth_sim.edit_frame`、`ac9_cloth_sim.delete_cache`、`ac9_cloth_sim.export_retopo`）を AI が押す・Python で呼ぶ。案内だけして本人が押す。
- 保存、ジョブのフォルダの操作（消す・改名する・手で作る）、服の形（頂点）の変更。
- 赤い塗りを見て「不良です」と言い切る。1 回の結果で細かい差の良し悪しを決める。

本人が押す前に一言添えること: 別名で保存済みか、現在形の確認は **Diagnose** で行い、変更後は再診断したか、**Fix by Hand** は経過の再生が止まる（Ctrl+Z で戻る）、**Simulate** は **Esc** か **Stop** を押すまで止まらず、経過の範囲はKeep CacheとKeep All Simulate Framesによる、**Delete Cache** で消したコマは戻らない、**To AC9 Cloth Retopo** は画面に出ているコマを使う。

表示名の注意: 画面にない名前を案内しない。今の名前は、**Edit Measure**（採寸の線を動かす）、**Whole Pieces**（3D でピースをクリックしてギズモ・G・R で動かす。着せる前も後も）、**Lengths & Notches**（縫い目の長さと合印）、**Particle Distance (mm)**（網目の間隔）、**Default Fabric**（生地。Weight や Bending の欄は服の設定には無い）、**Simulate**（着せる・続きから回す。**Phase Sim** と役割は **Experimental Features** をオンにしたときだけ）、**Import BOM**、**Update BOM Seams**（着装形を保って縫い目を更新）、**All On**。画面に無い名前: Move by Hand（ボタンは無くなった。3D の **Whole Pieces** で動かす）、Live（今の **Simulate**）、Continue、Dress、Re-dress、Use Selected Pieces、Seams from BOM、Fold のボタン、Adjust、Back to Frames、Name のボタン（名前は **AC9 Piece** の欄か F2）、Center、Tape／Hold（縫い目を巻き尺に沿わせる欄は今は出ない）。

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

Setupの「BOM から縫い目を更新」を押す。保存された読み込み元BOMを使い、全ピースの型紙・識別子・辺/内部線参照の互換性を確認し、縫い目と自動分類だけを更新する。着装形・配置・オン/オフ・凍結・物性・役割・折り・手動分類/縫い目は保持する。型紙変更、消える縫い目の参照/手設定、改名した群の曖昧な統合/分割があれば変更せず理由を表示する。続くSimulateが新しい縫い目を使う。新規Importは3D/配置を破棄する別操作なので、更新の代わりに案内しない。

## 着せ上がりの FBX から縫い目を決める（Seams from 3D Drape (FBX)）

操作 ID: `ac9_cloth_sim.read_drape`（読み込み時は `ac9_cloth_sim.import_bom` の `drape_fbx`）

BOM の番号を何枚ものピースが共有すると（何着も集めた服・別ファイルの部品）縫い目が別のピースに付く。BOM と一緒に書き出した服の FBX で相手を決める。**Import BOM** のファイル選択の **3D Drape (FBX)**（空なら同じフォルダの FBX 1 つ）、読み込み済みなら **Setup** の **Seams from 3D Drape (FBX)**（着装形・配置は保持）。結果「3D drape: ○ / ○ BOM seams found」。FBX に無いピースは BOM の番号から。見つからない縫い目は「not found on the 3D drape」。FBX が無ければ F3 では直らないことが多い: **Edit Seams** で消す／オフ、**Add Seam**。

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
- 選ぶ物: 服のメッシュを **Setup** の **Mesh** の欄に入れる（`scene.ac9_cloth_sim.from_mesh_object`。空ならアクティブなオブジェクト。体とは別のオブジェクト）。**Placement** の **Body** に体が選ばれている（無いと **From Mesh** は灰色）。
- 服は厚みの無い面 1 枚で、袖口や裾は開いている。UV シームを入れて展開済み。
- 体は、まっすぐ立って −Y を向いている。

手順:

1. **Placement** の **Body** に体を選ぶ。
2. 服のメッシュを選ぶ（アクティブにする）。
3. **Setup** の **From Mesh**（`ac9_cloth_sim.from_mesh`）を押す。

期待結果: 床に平らな型紙が並ぶ。着た状態の服 `AC9_Garment_<名前>` ができ、元のメッシュは非表示になる。「○ pieces, ○ seams」と、体の中に入っている点の数が出る（入っている点は赤く塗られる）。続けて **Simulate** で落ち着く。

制限・してはいけないこと: 作った形のままで、布として落ち着いていない。UV が無いと UV が無い旨のエラーで止まる。体より先に服のメッシュだけを選んでいると押せない。

戻し方: Ctrl+Z。元のメッシュは非表示になっているだけなので、Outliner で表示し直せる。

空の服から描き始めるときは **Setup** の **New Garment**（`ac9_cloth_sim.new_pattern`）。

## 使うピースを選ぶ

操作 ID: `ac9_cloth_sim.select_garment`、`ac9_cloth_sim.rename_garment`、`ac9_cloth_sim.garment_from_selection`、`ac9_cloth_sim.piece_state`、`ac9_cloth_sim.invert_piece_selection`、`ac9_cloth_sim.enable_all_pieces`、`ac9_cloth_sim.refresh`

目的: BOM に入っている、1 着ぶんより多いピースから、使うピースだけを on にする。服のまとまりの分け方が違えば直す。

前提条件: 型紙が読み込まれている。画面は **Setup** パネルと ツールタブの **AC9 Piece**。

手順:

1. **Setup** の **Garment** の欄の下の、使うまとまりの行（`<名前> (on の数/ピースの数)`、`ac9_cloth_sim.select_garment`）を押す。そのまとまりのピースが選ばれる（Shift で足す）。まとまりが 1 つだけなら行は出ないので、3D ビューで使うピースを選ぶ。
2. ツールタブの **AC9 Piece** の **State** で **Active**（`ac9_cloth_sim.piece_state`）。
3. **State** の行の **Invert Selection**（`ac9_cloth_sim.invert_piece_selection`）で残りを選び、**Off**。
4. まとまりの分け方が違う（オレンジの行、縫い目が別の服のピースや自分自身に付いている）ときは、まず **Update BOM Seams**（`ac9_cloth_sim.seams_from_bom`）。手動分類の訂正が必要な場合だけ正しい 1 着ぶんのピースを選びF3の **Garment from Selection**（`ac9_cloth_sim.garment_from_selection`）。そのまとまりと元のまとまりの BOM の縫い目が作り直される。名前は行の鉛筆（`ac9_cloth_sim.rename_garment`）。
5. 全部を戻すときは **All On**（`ac9_cloth_sim.enable_all_pieces`）。

期待結果: 使うピースだけが on（`13 / 20 pieces on` のように数が変わる）。off のピースは灰色の半透明になる。ピースの on／off では縫い目は変わらない。

制限・してはいけないこと: 状態のボタンは左右の対を一緒に変える（片側だけは Ctrl＋クリック）。F3の **Garment from Selection** はピースを選んでいないと「Select pattern pieces in the viewport first」と出て何も起きない。`ac9_cloth_sim.use_selected_pieces` はスクリプト用に残っているが画面には無いので、案内しない。型紙データの JSON を手で直したときは **Reload Spec**（`ac9_cloth_sim.refresh`）を押す。JSON が壊れていると赤い「The spec text is not valid JSON」が出る。

戻し方: **All On**、または Ctrl+Z。

## 型紙を直す（Whole Pieces・Edit Pieces）

操作 ID: `ac9_cloth_sim.use_tool`（道具に切り替える。`tool` に道具の ID。Whole Pieces は `transform_pattern_tool`、Edit Pieces は `edit_pattern_tool`、どちらも頭に `ac9_cloth_sim.` を付ける。道具の ID で、操作 ID ではない）、`ac9_cloth_sim.edit_pattern`（Edit Pieces に切り替える操作）、`ac9_cloth_sim.pattern_transform`（G・R・S、`mode='MOVE'|'ROTATE'|'SCALE'`）、`ac9_cloth_sim.pattern_edit`（K・X・C・N のキー）、`ac9_cloth_sim.pattern_drag`、`ac9_cloth_sim.pick_piece_under`（ダブルクリック）、`ac9_cloth_sim.context_menu`（何もしていないときの右クリック）、`ac9_cloth_sim.sync_3d`（Update 3D）、`ac9_cloth_sim.set_gap`、`ac9_cloth_sim.pattern_rotate`、`ac9_cloth_sim.pattern_scale`、`ac9_cloth_sim.duplicate_pieces`、`ac9_cloth_sim.symmetrize`、`ac9_cloth_sim.parallel_line`、`ac9_cloth_sim.add_dart`、`ac9_cloth_sim.move_dart`、`ac9_cloth_sim.spread`、`ac9_cloth_sim.cut_line`、`ac9_cloth_sim.piece_label`、`ac9_cloth_sim.mirror_copy`、`ac9_cloth_sim.symmetric_edit`、`ac9_cloth_sim.delete_piece`、`ac9_cloth_sim.delete_selected`、`ac9_cloth_sim.restore_pieces`

目的: 型紙の形を直す。既定の3D Follows Editsオフでは、調整した形と着た服への反映はUpdate 3Dまたは次のSimulateまで待つ。オンなら編集ごとに写る。反映と物理計算は別。人向けの正本はdocs/ja/features/pattern.md。

前提条件:

- 画面: 型紙を見る 3D ビュー（サイドバーの **Work Screen** で出る作業画面の真ん中）。オブジェクトモード。
- 型紙が読み込まれている。道具は **Pattern** パネルの **Whole Pieces**と **Edit Pieces**（または左の列。**Pattern Tools** がオンのとき）。
- 道具は 2 つに分かれている（2026-10-28〜29）。**ピースを丸ごと動かす・回す・拡大縮小するのは Whole Pieces。角・辺・内部線の点を動かすのは Edit Pieces。** Edit Pieces でピースの内側を引くと箱選択になり、ピースは動かない。利用者が「ピースが動かない」と言ったら、まず持っている道具を確かめる。

手順:

1. ピースごと動かす: **Pattern** の **Whole Pieces** を押して道具を持つ。ピースか内部線をクリックで選び（Shift で足す、何も無い所から引くと箱）、ドラッグで動かす。G（移動）・R（回す）・S（拡大縮小）は選んだ物の真ん中を基準にする。Shift+D（そのまま複製）、F2（名前）、X／Delete（選んだ物を消す）。3D のギズモは移動か回転の片方ずつ出る（道具のヘッダーの **Gizmo** か W で切り替え）。
2. 形を直す: **Pattern** の **Edit Pieces** を押して道具を持つ。型紙の上で、角・辺・取っ手・内部線（端・途中の点・線全体）をドラッグする。クリックで選び、Shift＋クリックで足すと、選んだ全部を一緒にドラッグできる。ダブルクリックでピース全体を選ぶ。ドラッグ中は X／Y で軸、Shift で 0／45／90°、数字で長さ、Ctrl で吸着を切る。G／R／S（選んだ角・辺・線）、K（辺か内部線に点を足す）、C（辺か内部線の区間をカーブ／直線）、N（合印）、Shift+D（そのまま複製）。S は拡大・縮小だけ。ダーツを足す・動かす、切り開き、線で切る、平行な線は右クリックのメニューにだけある（右クリック＝カーソルの下の物にできることの全部、キーは近道）。道具のヘッダーにはカーソルの下の物のキーだけが出る。全部は **Pattern** パネルの **Keys**（2 つの道具の両方）。
2b. 何もしていないときの右クリックは、カーソルの下の物のメニュー（`ac9_cloth_sim.context_menu`）。右クリックした物が先に選ばれ、項目はキーやボタンと同じ操作を呼ぶ。操作の途中の右クリックは取り消し。
2c. **AC9 Run** の **3D Follows Edits**（シーンのプロパティ `follow_edits`、既定オフ）が外れている間は、型紙の直しは 3D に写らずに待つ（`sync_pending` が真、**Update 3D** が赤）。**Update 3D**（`ac9_cloth_sim.sync_3d`）か、次の **Simulate** で写る。
3. 距離をそろえる: 選んだ内部線・辺の ツールタブに周りまでの距離が並ぶ。距離は表示だけで、「数値入力…」（`ac9_cloth_sim.set_gap`）の小窓で、今の位置からの X・Y か、周りまでの距離を入れて動かす。
4. 左右ペアの片方を直すと、相方も鏡像で直る。ずれたときは片方を消し、もう片方を **Mirror Copy** する。**Mirror Copy**（`ac9_cloth_sim.mirror_copy`）は選んだ全部を縫い目ごと鏡に写す。
5. そのままの複製は **Selected Pieces** の **Duplicate** か Shift+D（`ac9_cloth_sim.duplicate_pieces`）。鏡にせず、左右ペアにもしない。複製したピースどうしの縫い目だけ一緒に写る。複製は置かれていないので Place で置く。
6. 左右対称の 1 ピース（1 枚で裁つ前身頃など）は ツールタブ **AC9 Piece** の **Symmetric Edit**。Rectangle／Ellipse の道具のヘッダーのチェック（既定オン）で、描いたときからオンにもできる。オンのままなら K・X・N、内部線（描く・動かす・消す）・折り・ダーツ・切り開き・縫い目も反対側へ鏡で入る（中心線をまたいで自分の鏡になる線は 1 本）。角を隣の角に落とすと 1 点にまとまる。中心線は `mirror_axis`（中心 x, y と角度）に持ち、R で回すと一緒に回る。中心線で切ると左右ペアでない 2 枚になる。崩れたら隣の **Symmetrize**（`ac9_cloth_sim.symmetrize`、`keep='LEFT'|'RIGHT'`、既定 LEFT）で反対側を作り直す。
7. 外周の辺か内部線に平行な内部線は右クリックの Parallel Line（`ac9_cloth_sim.parallel_line`、先に小窓。`distance` m・`count` 本・等間隔。辺なら `piece`・`edges` はカンマ区切りの辺 ID で内側へ、隣り合う辺は 1 本の線。内部線なら `line` に線 ID、`flip`・`both` で側、`extend` で端を外周まで）。
8. ピースの名前は ツールタブの **AC9 Piece** のいちばん上の欄か F2（`ac9_cloth_sim.piece_label`）。

期待結果: 型紙の形が変わる。着せた服・調整した形へは、**3D Follows Edits** が外れていれば（既定）次の実行（Simulate など）か **Update 3D** でまとめて写り、押されていればその場で写る。調整した形も同じ反映時点で更新する。3D Follows Editsがオフの間は、型紙編集だけで即時更新されるとは案内しない。写したか・写せなかった理由は Run の状態行に「Pattern edit: …」として残る。ピースを動かす・回すだけでは着た服は動かない。写せない形は、理由が出て止まる。続けて **Simulate**（「シミュレーションする（Simulate）」の節）で落ち着く。

制限・してはいけないこと: 型紙のピースのオブジェクトは鍵が掛かっている。Blender の G／R／S や標準の編集で動かさない。型紙データを JSON で直接変えると、着せた服へ写せずエラーになることがある。Blender の標準の X でピースのオブジェクトだけが消えたときは、**Lists** の **Pieces** タブの **Restore N Deleted Object(s)**（`ac9_cloth_sim.restore_pieces`）で戻す。

戻し方: Ctrl+Z。

## 型紙を描く（Draw Piece・Internal Line・図形）

操作 ID: `ac9_cloth_sim.draw_piece`、`ac9_cloth_sim.draw_line`、`ac9_cloth_sim.draw_shape`、`ac9_cloth_sim.delete_internal`、`ac9_cloth_sim.cut_out`（Convert to Hole／Fill Hole）、`ac9_cloth_sim.clone_shape`（Clone as Pattern。`sew=True` で +Sew）

目的: 新しいピース、内部線、長方形・楕円のピースや内部線を描く。

前提条件: 型紙の見える 3D ビュー。**Pattern** の **Piece** と **Internal** の各ボタン（または左の列）。内部線は既存のピースの上。

手順:

1. **Piece** の **Draw**（角を順にクリック、最初の角か Enter で閉じる）、**Rectangle**、**Ellipse**。
2. **Internal** の **Line**（ピースの上で点をクリックして Enter。3 点以上の後に最初の点をクリックすると閉じた図形）、**Rectangle**、**Ellipse**。
2b. 閉じた図形では、ツールタブの **AC9 Internal Line** と右クリックのメニューに **Convert to Hole**（中を穴にする、もう一度で埋める。外周に触れる・交わる、ほかの穴と重なる、折り・ゴムの付いた図形は断られる）、**Clone as Pattern**（同じ形の新しいピース。生地は元のピース、縫い目なし、置かれていない、元のピースの右）、**Clone as Pattern (+Sew)**（さらに新しいピースの辺を図形に縫い付け、元のピースが置いてあれば 3D でもその上に付ける）が出る（Clone の 2 つは右クリックのメニューだけ）。
3. 数字を打つと寸法を指定できる（シーンの長さの単位）。Shift で 15° 刻み。Backspace で 1 歩戻る。

期待結果: ピース、または内部線が型紙に増える。

制限・してはいけないこと: 内部線を描くとき、点がピースの外にあると描けない。ピースを消すとその縫い目・配置・折り線も消える（**Selected Pieces** の **Delete**、`ac9_cloth_sim.delete_piece`。確認が出る）。

戻し方: Ctrl+Z。

## 縫い目を付ける・直す

操作 ID: `ac9_cloth_sim.edit_seams`、`ac9_cloth_sim.seam_click`、`ac9_cloth_sim.seam_list_pick`、`ac9_cloth_sim.seam_move_side`、`ac9_cloth_sim.seam_notch`、`ac9_cloth_sim.add_seam`、`ac9_cloth_sim.add_seam_mn`、`ac9_cloth_sim.match_length`、`ac9_cloth_sim.seam_edit`、`ac9_cloth_sim.seam_path`、`ac9_cloth_sim.seam_path_part`、`ac9_cloth_sim.pick_seam`、`ac9_cloth_sim.blend`、`ac9_cloth_sim.match_notches`、`ac9_cloth_sim.notches_from_bom`、`ac9_cloth_sim.close_dart`、`ac9_cloth_sim.fold_dart`

目的: 縫い目が正しいか確かめ、足し、直す（README の『Seams（縫い目）』）。

前提条件:

- 画面: 縫い目の選択と端のドラッグは、型紙の見える 3D ビューで **Edit Seams** を持って行う。着た服の上でも 3D の縫い目の線は選べる。
- 縫い目を選ぶと、ツールタブに **AC9 Seam** が出る。

手順:

1. **Lists** の **Seams** タブで行を押す（`ac9_cloth_sim.seam_list_pick`）か、**Edit Seams** で型紙の縫い目をクリックして選ぶ。Shift＋クリックで何本も選べ、そのときは **On**／**Off** と **Delete** だけが全部に効く。
2. 長さの差を直す: **AC9 Seam** の **Fit the length of** の、直したい側の **A** か **B**（`ac9_cloth_sim.match_length`）。どちらがどのピースかは、上の A・B の行と型紙の札で見る。
3. ねじれて縫われる: **Flip the direction of** の、その側の **A** か **B**（`ac9_cloth_sim.seam_edit` の FLIP_A／FLIP_B）。
4. 警告の確認: **Confirm**。縫わない: **Off**（戻すのは **On**）。消す: **Delete**。いずれも `ac9_cloth_sim.seam_edit`。
5. 縫い目を足す: **Add Seam**（`ac9_cloth_sim.add_seam`）で、片方のピースの始点・終点、次に相手のピースの始点・終点をクリックし、Enter。片側が複数区間なら **Add Seam (M:N)**（`ac9_cloth_sim.add_seam_mn`）で、側 A の区間を打って Enter、側 B も同じく Enter。
6. 側を付け替える: **Edit Seams** で M（`ac9_cloth_sim.seam_move_side`）。向きを逆にするのは F。
7. 合印: N（`ac9_cloth_sim.seam_notch`、Ctrl+N で片側）。**Lengths & Notches** の **Lay the notches of** の **A**／**B**（`ac9_cloth_sim.match_notches`）、BOM の合印は **Notches from BOM**（`ac9_cloth_sim.notches_from_bom`）。
8. 引き寄せ方: **Pull**（`ac9_cloth_sim.seam_path`。**Straight** か **Round: …**）、**Part**（`ac9_cloth_sim.seam_path_part`）。
9. 縫い目の折り: **AC9 Seam** の **Fold Angle**（角度、`scene.ac9_seam_fold_angle`）・**Direction**（Mountain／Valley、回るピースの表から見て、`scene.ac9_seam_fold_dir`）と、回るピースの **Turns**（`scene.ac9_seam_fold_turn`）。符号つきの `scene.ac9_seam_fold`（＋＝Mountain）でも書ける。値を変えると調整した形がその場で折れる。角度は 2 枚のピースのあいだの角度で、相手のピースの今の形（調整した形、無ければ着た服）に対して回るピースを回す（Tilt で先に倒してあっても、その分を足さない）。
10. ダーツの縫い目を選ぶと **Move**（`ac9_cloth_sim.move_dart`）、**Fold**（`ac9_cloth_sim.fold_dart`）、必要なとき **True Mouth**（`ac9_cloth_sim.close_dart`）が出る。縫い目の端のなめらかなつながりは **Blend**（`ac9_cloth_sim.blend`）。
11. BOM の縫い目を作り直す: BOM の縫い目は服のまとまりごとに持つ。**Setup** の **Update BOM Seams**（`ac9_cloth_sim.seams_from_bom`）で、着装形を保持して縫い目と自動分類を更新する。手動分類は保持する。ピースの on／off では変わらない。

期待結果: 一覧の行と型紙の縫い目の色が対応し、両側の長さと差が更新される。警告アイコンは確認や直しで消える。

制限・してはいけないこと: 縫い目の付け替えは、続きから **Simulate** すると新しく縫う扱いになり、布が最初の数コマで引き寄せられる（異常ではない）。**Pull** の **Round** は試験中の機能で、部位が無いと「No body part: pulled straight」と出てまっすぐになる。縫い目の一覧から消した BOM の縫い目は、**Update BOM Seams** を押すと戻る。縫い目を巻き尺に沿わせる欄（Tape／Hold）は今は画面に出ないので案内しない。

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

制限・してはいけないこと: 体の向きや姿勢が違うと「upright, facing -Y?」と出る。**Detect Guides** をやり直すと、すでに置いてあった配置中のピースのオブジェクトは作り直しになる（手で動かした量は残る）。**Edit Guides** は **Done** を押すまで、円柱がオブジェクトのまま残る。

戻し方: Ctrl+Z、または **Detect Guides** をやり直す。

