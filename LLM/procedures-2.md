## 配置する（Place・Back to Flat）

操作 ID: `ac9_cloth_sim.place`、`ac9_cloth_sim.clear_placement`、`ac9_cloth_sim.unplace`、`ac9_cloth_sim.use_tool`、`ac9_cloth_sim.unlink_pair`、`ac9_cloth_sim.pair`、`ac9_cloth_sim.swap_sides`

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
5. 円柱の無い所で押すと平らに置く（spec placement `{'guide': 'flat', 'flat': {'o': [mm], 'R': [ex, ey, n]}, 'anchor'}`）。面の上なら型紙の法線を面の法線に合わせて 2 mm 浮かせ、何も無ければ Z = 0 に上向き。刺さりは自動で直さない。平らのピースを押してドラッグすると面（地面）の上を滑る。位置・回転は Tool タブ AC9 Piece の Location & Rotation（配置中のピースのオブジェクトの変形）。
6. **Back to Flat**（`ac9_cloth_sim.clear_placement`）は、選んだピースを 3D の今の位置・向きのまま、アンカーの接平面へ平らにする（平らの placement に書き換える）。手の移動は捨てる。
   **Return to Unplaced**（`ac9_cloth_sim.unplace`）は選択必須。3D配置・手編集・保存形だけ破棄し、2D定義・折り・縫合・生地を保つ。連動ペアは両側、それ以外は選択側。計算中不可、Ctrl+Z可。再Placeまで未配置を保つ。Offは形を保持する別操作。
7. 床: Run の **Floor (Z = 0)**（`scene.ac9_cloth_sim.floor`）。Z = 0 の下に厚み 20 mm の箱を当たりとして足す（シーンには何も足さない）。体・Collision・床・他の服が何も無いと、重力の無い段だけ回して止まり 1 行で知らせる。
8. 左右: **Pair**／**Unpair**（`ac9_cloth_sim.pair`、F9 に `plane`）、**Unlink**／**Link**（`ac9_cloth_sim.unlink_pair`）。鏡の面は Tool タブ AC9 Piece の **Pair** の **Mirror Plane**（`ac9_cloth_sim.pair_plane`、`plane='x'|'y'`。Mirror Copy の対も Pair の対も）。Pair の対は型紙を回さず、相手がどちら向きに描いてあっても正しい向きで置く（spec の `mirror_layout`）。平らに置いた対は服の中心 `spec['center']['o']`（mm、Empty「AC9 Center: 服名」）で左右も前後も鏡写し、体の上の対は体の中心。中心を選択に足すのは **Select Center**（`ac9_cloth_sim.center_select`）、古いファイルの自分の面（`mirror_at`）の対を中心へそろえるのは **Align to Center**（`ac9_cloth_sim.center_align`）。置いた対の左右を入れ替えるのは **Swap Sides**（`ac9_cloth_sim.swap_sides`。対の両方が on のとき）。

期待結果: 離すと配置した形が現れ、`○ guides; ○ / ○ pieces placed`が増える。未調整なら調整した形と同じ。Simulateは表示中の形から始める。品質はDiagnoseで確認する。

制限: 着用中の選択ピースは配置から除外しBack to Adjusted Shapeを案内。Back to Flatは選択と連動鏡像だけ平らにし、他の着装形・型紙・縫合を保持。無選択は確認後に配置済み全部、曖昧選択は拒否。Ctrl+Z可。体の凹凸へ伸ばす/自動移動はしない。Swap Sidesは寄せ/載せ/頂点編集を再生成し、手移動と折りは場所側に残す。着用中なら調整した形に戻る。

戻し方: Ctrl+Z。

### 調整した形を整える（Part to Seam・Band on Edge・Move Outside・Settle）

操作 ID: `ac9_cloth_sim.diagnose_placed`、`ac9_cloth_sim.move_to_seam`、`ac9_cloth_sim.lay_on_edge`、`ac9_cloth_sim.move_outside`、`ac9_cloth_sim.settle_placed`

目的: シミュ前に、物理計算なしで配置を調整する。

前提条件: 画面は **Placement** パネル。整えるピースが選ばれ、シミュレーション中でない。**Move Outside** は現在の配置形・着た形をそのまま扱うため、**Back to Adjusted Shape** は不要。他の3つは整えるピース自身が配置形であること（着た服の一部なら先に **Back to Adjusted Shape**）。相手の状態は問いません。

手順:

0. 確かめる（何も動かさない）: **診断**（Diagnose）。配置状態・シミュレーション後の現在形について、体内侵入・他布との交差・自己交差を調べる。確認済みを赤、縫合を照合できない交差候補を黄で示し、対象範囲と面数を結果に出す。面数は箇所数ではない。
1. **Part to Seam**（`ac9_cloth_sim.move_to_seam`）: 折り設定を反映し、有効な縫う点を指定相手の点へ重ね、固定して整える（Settle）。相手の形は調整した形→着た服→円柱の順。起動後クリックで相手指定、Shiftで追加/解除、Aで全相手、Enterで適用、Escで取消。指定は今回だけでOn/Off・次Simの縫合設定は不変。移動側/追従ペアは起動時に固定表示し、相手選択でactiveは変えない。伸びは数と「オーバーレイ > 伸び」で表示。三角の潰れは拒否。伸び過ぎで形を保って近くへ運ぶ場合も、侵入/貫通が増すなら拒否。外側への補正は別途Move Outside。寄せる前の手配置・頂点編集・折りを別に保持し、反復しても基準は上書きしない。
2. 細い帯（衿リブ）を指定相手の縫い付け線に沿わせ、幅方向も含めて配置する（相手指定は1と同じ）: **Band on Edge**（`ac9_cloth_sim.lay_on_edge`）。
3. 重なったピースを外側へ出す: **Move Outside**（`ac9_cloth_sim.move_outside`）。選んだ現在形を保ち、相手の表側へ移します。自分・相手とも配置形／シミュレーション後の形で利用でき、縫い合わせは不要。補正後の形から再Simできます。移動量は重なる相手の 8 mm 外まで（報告に移動量と交差の前後が出る）。既に相手の 8 mm 以上外にあるピースは動かさない。ペアは鏡の相方も一緒に動くので、相方どうしが交差しているときは無変更で「ペアの連動を切り（Tool タブ > AC9 Piece > Pair）上の方だけで押す」と案内する。表側が曖昧、または途中で別の層や人体を通り抜ける場合も理由を表示して無変更です。
4. 体から出す・相手の側へ載せる・辺の長さを戻す: **Settle**（`ac9_cloth_sim.settle_placed`）。1 と 2 も運んだあとに同じ整えを行う。

期待結果: 調整した形が変わり、残った所は赤く示される。Part to Seam の報告は 1 ピース 1 行で、相手の名前・隙間・伸び（赤が残れば体の中と貫く所の数）が出る。

制限: 押したときだけ動く。未選択/未配置/着用中などで使えない場合は灰色になり理由を示す。着用中ならBack to Adjusted Shapeで戻してから使う。

戻し方: Ctrl+Z。**Move Outside** は現在形に対する編集で、配置形なら **Back to Placed Shape** で手配置とともに戻せます。着た形の補正は **Back to Adjusted Shape** で元の配置形へ戻ります。

## 配置を手で直す（Whole Pieces・Back to Placed Shape）

操作 ID: `ac9_cloth_sim.show_placed`、`ac9_cloth_sim.reset_placed`、`ac9_cloth_sim.set_pose`、`ac9_cloth_sim.mirror_pose`

目的: 調整した形をG・R・ギズモ・Edit Modeで直す。未調整でもSimulateの開始形は同じなので微調整時だけ使う。着せた後はピースのつまみで動かす。

前提条件: 配置済み・シミュ停止中。配置中のオブジェクトはPlace/Detect Guidesで作る。配置済みなのに無い場合（「No placed shapes: Place the pieces again」）は再配置か`ac9_cloth_sim.show_placed`（Make Placed Shapes、画面にボタンなし）。

手順:

1. 3D ビューの道具 **Whole Pieces** で、動かすピースをクリックする（着せた後は着た服のピースをクリックするとつまみが出る）。Shift＋クリックで足す。
2. ギズモ（W で移動と回転を切り替え）か G・R で動かす。数値で動かすなら Tool タブ AC9 Piece の Location & Rotation（操作 `ac9_cloth_sim.set_pose` も残っているが、パネルのボタンは無い）。連動したペアは相手が鏡で付いてくる。対の相手へ鏡で写すのは **Mirror Placement**（`ac9_cloth_sim.mirror_pose`）。
3. 手直しを捨てるなら、そのピースを選んで **Back to Placed Shape**（`ac9_cloth_sim.reset_placed`）。選ばずに押すと全部（確認が出る）。着た服のピースは先に調整した形に戻る。

期待結果: 選んだピースが動き、Move と Turn の値が出る。続く **Simulate** がその形から始まる。

制限: 着装後はつまみをG・Rで動かす。**Back to Adjusted Shape**で戻した/着装後に配置したピースは、つまみではなく配置中のオブジェクトが選ばれる。再生成時は頂点編集を失い、移動量は残る。廃止済み「Move by Hand」は案内しない。

戻し方: Ctrl+Z、または **Back to Placed Shape**。

## シミュレーションする（Simulate）

操作 ID: `ac9_cloth_sim.dress`（画面の **Simulate**。mode が LIVE）、`ac9_cloth_sim.log`、`ac9_cloth_sim.delete_cache`

目的: 縫って着せる。型紙を直した後・手で直した後の形から続けて、服全体を落ち着かせる。回っている間に布を手でつまんで直す（README の『着せる（実行）』）。

前提条件:

- 画面: Tool タブの最上段 **AC9 Run**。
- **Body** が選ばれ、**Detect Guides** で目安があること（無いとボタンは灰色）。体が無くても、平らに置いたピースだけなら回る。
- 3Dに配置済みのピースだけを計算する。未配置のピースとそれにつながる縫い目はその回の計算から外れ、型紙のオン／オフ設定は変わらない。
- Preferences の **Newton Python** が使える状態であること（無いと「Set the Newton Python in the add-on preferences」）。
- テスト用の .blend は別名で保存済み。現在形の確認は **Diagnose**、形を直したら再診断する。

手順:

1. 必要なら、生地・点の間隔を **AC9 Garment** で決める（「生地を選ぶ」の節）。
2. **AC9 Run** の **Simulate** を押す。本人が押す。押したボタンが **Stop** に変わる。はじめて着せるときは調整した形から縫い目を引き寄せ、着せた後は今の形から続ける。
3. 布が動き始めるまで少し待つ（3D ビューに「シミュレーションを準備中（経過 N 秒）」と出る。初回は長め、パソコンによって変わる）。動き始めたら、3D ビューで布を Ctrl＋左ドラッグしてつまめる。**Freeze** と **Off** のピースはつかめない（つかむと、そう出る）。布が布を突き抜けているときは、ドラッグ中に Alt を押すと、つかんだ布がほかの布をすり抜ける（押している間だけ。Alt を押しながらつかみ始めると、マウスの下のピース 1 枚だけ）。Ctrl＋Shift＋ドラッグは **Smooth**（ならす）。
4. **Gravity**（既定オン）は回っている間も切り替えられる。衿や袖が縫い付く前に落ちるときは、**Gravity** を外して回し、縫い目が閉じたら入れる。**Floor (Z = 0)** は回す前に決める。
   - **Freeze Garment While Sewing**（`scene.ac9_cloth_sim.hold_while_sewing`、既定オン、Settings の中）: 縫い終わった服に 1 枚置き直して回すと、そのピースが縫い付くまで服を止める（Freeze の色で薄く塗られ、つかめない。Run の下に 1 行）。縫い付けば解ける。届かないときはしばらくして解く。オフにすると服がピースの方へ持ち上がる。回す前に決める。
5. 回っている間に効くものはRun下の案内で確認する。内部線/縫い目の角度・方向、内部線Taperは、開始時に角度が0°でなかった線だけ変更できる。Taperは現在角も非0でピース内に止まる端がある場合のみ。0°から新しく折る・Turns・Layersの変更は停止して次の実行から。縫い目On/Off、Active/Freeze、ピースElastic、Gravity、Strengthenのオン/オフと倍率も実行中に効く。変えた値は設定として残る。
6. 自動では止まらない。服が落ち着いたら、もう一度同じボタン（**Stop**）か **Esc**。
7. 止めたら、**AC9 Garment** の **Result** を確かめる。

期待結果: 止めた所が結果になり、「done: …」が状態表示に出る。**Result** に `Simulate` と日時、保存の状態（`Shape: …`）が出る。実行時間にコマ数の上限はない。Keep Cacheがオンなら縫う区間とその直後を記録し、Keep All Simulate Framesがオンならその後も最大1,500コマの途中経過を記録する。上限後も計算と最後の形の保存は続く。Keep Cacheがオフ、または開始と最後だけの記録なら、正常終了/自分で停止した後は経過を残さない。失敗時の診断用記録は別。停止後はResultの経過表示を読む。

制限・してはいけないこと:

- 始まらなかったときは、状態表示に `… did not start: <理由>` が残る。型紙を JSON で直接変えて写せないとき「changed outside the drafting tools …」と出ることがある。
- 結果は回ごとに揺れる。良し悪しを1回で決めない。現在形の診断は赤が確認済み、黄が縫合未照合の交差候補。対象・件数・未検査と過去の実行時記録を分けて読む。
- ほかの服をシミュレーションすると、前の服の経過は消えて最後の形だけが残る。**Delete Cache**（`ac9_cloth_sim.delete_cache`）は経過のコマを消す（戻せない。AI は押さない）。
- 最後の形は .blend の中に残る。ジョブのフォルダが無いと **Result** は `Result: folder gone` になるが、**Simulate** はその形から続く。
- 布の無い所で Ctrl＋クリックしても何も起きない。生地の値など、上の 5 以外の変更は次に押したときから効く。
- AI は押さない。状態表示の `done:` の行、`stopped`、エラー文、**Log**（`ac9_cloth_sim.log`、**AC9 Run** の **Settings** の中）。状態表示はふだん 1 行で、全文も **Settings** の中を読み上げる。

戻し方: **Stop**／**Esc** で止めた所が残る。**Back to Adjusted Shape** で調整した形に戻す。Ctrl+Z。

## 試験中の機能（Phase Sim・ピースの役割）

操作 ID: `ac9_cloth_sim.update_3d`（画面の **Phase Sim**）、`ac9_cloth_sim.propose_roles`、`ac9_cloth_sim.set_role`、`ac9_cloth_sim.set_start`

目的: まだ試している機能。アドオンの設定（Edit → Preferences → Add-ons → AC9 Cloth Sim）の **Experimental Features** をオンにしたときだけ画面に出る。既定はオフで、利用者にはまず **Simulate** を案内する。

前提条件: **Experimental Features** がオン。ほかは **Simulate** と同じ（**Body**、目安、**Newton Python**）。

手順:

1. **Phase Sim**（`ac9_cloth_sim.update_3d`）: 段を追って縫い、決まった段を全部終えたら自動で止まる。正常終了時はKeep Cacheがオンなら経過を残し、オフなら最後の形だけを残す。2回目からは現在形を使い、経過を再生している場合だけ画面のコマから続ける。本人が押す。
2. **Piece Roles**（N パネル）の **Propose Roles** と、**AC9 Piece** の **Role**・**Start Shape**。役割は **Phase Sim** が読む（縫う段・前の留めなど）。**Simulate** は役割を読まない（Ends を付けていない古いピースの読み替えを除く）。
3. ほかに縫い目の引き寄せ方、ピースの **Layer** が出る。

期待結果: **Phase Sim** は「done: …」と出る。正常終了時はKeep Cacheがオンなら **Result** に `Frames: on the timeline`、オフなら `Frames: dropped (shape kept)`。

制限・してはいけないこと: 利用者が **Experimental Features** をオンにしていないときは、これらを案内しない。AI は押さない。

戻し方: Ctrl+Z。

## Back to Adjusted Shape と Fix by Hand

操作 ID: `ac9_cloth_sim.reset`、`ac9_cloth_sim.edit_frame`

目的: **Back to Adjusted Shape** は、選んだピース（選ばなければ有効な配置済みの全部）を調整した形へ戻す。「調整した形に戻す」は、シミュレーション後なら開始時に残した形（縫い目へ寄せた調整も含む）へ戻します。まだシミュレーション結果がない場合は、縫い目へ寄せる前の形へ戻し、手配置・頂点編集・折りは残します。 シミュレーションはしない。**Fix by Hand** は、画面のコマを服の形にして手で直せるようにする。

前提条件:

- **Back to Adjusted Shape**: 服（着せた結果、または配置中のピースのオブジェクト）がある。シミュレーション中は押せない。ピースだけ戻すなら、型紙をクリック（**Place** 道具）か、**Place** 道具で着た服のピースをクリックして選んでおく。
- **Fix by Hand**: 着せた結果がある。経過の再生が有効なときか、経過の再生が無効で服の形がメッシュそのもののとき（そのときは「the garment's mesh is its shape」と出て、そのまま直せる）。

手順:

1. **Back to Adjusted Shape**（`ac9_cloth_sim.reset`）を押す。選んだピースがあれば、そのピースだけが調整した形に戻る（パネルに「Next: ○ piece(s) from placed」）。何も選ばなければ、確認のあと服全体の結果を外す。
2. 戻したピースを、つまみで G・R で動かしてよい。
3. **Fix by Hand**（`ac9_cloth_sim.edit_frame`）を押し、Edit Mode やスカルプトで形を直す。
4. どちらも、続けて **Simulate** を押すと、その形から続く。

期待結果: **Back to Adjusted Shape** では、押した瞬間に着た服の中のそのピースだけが調整した形になる。**Fix by Hand** では、タイムラインを動かしても形が変わらず、`Frames: off (shape kept)` と出る。

制限・してはいけないこと: どちらも経過の再生を止める。何も選ばない **Back to Adjusted Shape** は服全体の結果を外す（確認が出る。ジョブのフォルダは残る）。寄せる前の形がない古いファイルや、型紙・ペアが変わって復元できない場合は理由を表示し、失敗時は形と基準を保つ。復元不能を推測で補わない。変更のUndo、または手移動を捨てるBack to Placed Shapeで再配置できる。**Fix by Hand** は AI が押さない。

戻し方: Ctrl+Z（経過に戻る）。

## ピースの状態を変える

操作 ID: `ac9_cloth_sim.piece_state`、`ac9_cloth_sim.select_piece`、`ac9_cloth_sim.invert_piece_selection`、`ac9_cloth_sim.collider`、`ac9_cloth_sim.collider_select`

目的: ピースごとに **Active**、**Freeze**、**Inactive**、**Off** を決める。ほかの服や小物を障害物にする。

前提条件: 型紙が読み込まれている。画面は **Lists** の **Pieces** タブ、またはピースを選んだときの **AC9 Piece** の **State**。

手順:

1. **Lists** の **Pieces** タブで、ピースの行の右の 4 つのアイコンを押す（行の名前を押すとそのピースが選ばれる、`ac9_cloth_sim.select_piece`）。服の行の 3 つのアイコンはその服の全ピースをまとめて切り替える（**Off** は除く）。
2. 選んだピースをまとめて切り替えるなら、**AC9 Piece** の **State**。残りを選び直すのは同じ行の **Invert Selection**（`ac9_cloth_sim.invert_piece_selection`）。
3. 障害物は、**Collision** タブの **Add** の欄（`scene.ac9_cloth_sim.collider_pick`。物を入れると登録され、欄は空に戻る）か、物を選んで **Add Selected**（`ac9_cloth_sim.collider`）、やめるのは **Remove**。登録した物は 3D ビューの **Whole Pieces** のクリックで選べる。行の名前を押すとその物が選ばれる（`ac9_cloth_sim.collider_select`）。

期待結果: ピースの状態がアイコンの押された表示と型紙の見え方（**Off** は灰色の半透明）に出る。重ね着では、結果を持つほかの服が自動で障害物になる。

制限・してはいけないこと: 状態のボタンは左右の対（鏡のペアと **Pair** の対）を一緒に切り替える。片側だけは Ctrl＋クリック。**Off** のピースは一覧に数だけ出る。戻すときは **Edit Pieces** で灰色のピースをクリックして選び、**Active** にする（戻したピースは残った側の鏡の形にそろう）。

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
5. ピースの見た目 **Look**、厚みの表示 **Thickness**、**Layer**（0〜9。高い方を低い方の外側に保つ。全部を 0 に戻すのは右の矢印 **All Layers to 0**、`ac9_cloth_sim.layers_to_zero`）、点の間隔 **Distance (mm)**（左の矢印で服の値に戻す、`ac9_cloth_sim.piece_mesh`）、縮み **Elastic**。どれも **AC9 Piece** の欄。Layer は一時的な順序補助として、内外を確認したら停止→All Layers to 0→Simulate で再計算する。変更は次のシム開始から有効。深い食い込みの追加回復は異層の間だけで、全0の再計算では起動しない。本人の指示なしにAIが自動解除しない。
6. 役割は試験中の機能（上の『試験中の機能』）。**Simulate** の置き方・巻き方は **AC9 Piece** の欄（**Ends** など）で決める。
7. 折り: 内部線を選ぶと **AC9 Internal Line**。**Fold Angle**（角度）、**Direction**（Mountain/Valley、布の表から見た方向）、**Turns**（回る側 **Side A**/**Side B**）、**Layers**（**Both**/**A only**/**B only**、片側計算なら反対側は2層目として追従）。`scene.ac9_iline_fold`は符号付き角度（＋Mountain）。値変更で調整した形を折り、左右ペアの対応線にも同じ文字を反映。相方のAは元のAの鏡側（hand=-1なら線の右）。拒否理由は`place_fold.why_not(piece,line)`。縫い目は`scene.ac9_seam_fold`（＋は回るピース表からMountain）、または`ac9_seam_fold_angle`＋`ac9_seam_fold_dir`。`scene.ac9_seam_fold_turn`のA/Bは縫い目a/bの回るピース。

期待結果: 生地の一覧と、ピースの生地の割り当てが変わる。型紙に **Show Fabric Colors** で色が出る。何枚か選んでいて値が違う欄は「(mixed)」と赤く出て、変えると全部に効く。

制限・してはいけないこと: 値の変更は次に **Simulate** を押したときから効く（実行中に効く対象はRun下の案内で確認。角度・方向・Taperは開始時に折っていた線だけ（現在角が非0・ピース内端ありも必要）、Strengthenのオン/オフ・倍率も対象）。既定の生地は消せない（先に別の生地を既定にする）。**Layer** は自動では 0 に戻らない。AI が値を変えるときは、本人が頼んだときだけにし、変える前の値を答えに書いておく。**Settings:** という赤い行が出ているときは、試験用の設定のセットが生地や **Watch** を固定していて、値を変えても効かない。

戻し方: Ctrl+Z。

### Strengthen（強化）

- 目的: 選んだピースの伸び・せん断・曲げを人が指定する倍率で硬くする。芯地とは別。
- 場所: Properties → Tool → AC9 Piece → Strengthen（強化）と Strength Factor（強化倍率）。型紙か 3D のピースを選ぶ。
- スクリプト: `scene.ac9_cloth_sim.piece_strengthen` と `piece_strengthen_factor`。現在の選択と左右の対へ同時適用。spec は `pieces[].strengthen`（未指定 false）と `strengthen_factor`（未指定 5、範囲 1〜100）。
- シミュレート中のオン・オフと倍率変更は次に値を受け取るコマから効く。設定は spec/.blend に残り、停止しても解除しない。Off は倍率を消さない。
- 紫の色が目印（Freeze/Off の色が優先）。倍率 1 は硬さ不変。重さ・厚みは変わらない。もう垂れているピースに掛けると、掛けた時の形を保つ（平らな型紙へ戻らない、まわりの縫い目を引かない）。倍率変更は同じ形のまま、外して掛け直すとその時の形を取り直す。高倍率は縫い寄せにも抵抗するので、必要なら解除して落ち着かせる。形が必ず改善するとは案内しない。
- 色には Overlays → Piece Colors が必要。End Taper 併用でシミュレート中に強化を実変更した回は、次のシミュレートが見た形から再構築される。見た形と属性は保存される。強化が変わらない回と、End Taper がない回の完全状態保存にはこの制限を加えない。

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
4. 着せたあとは **Measure Garment**（`ac9_cloth_sim.measure_garment`。**Simulate** のあとは自動で測る）。表の **Cloth** と **Ease** が埋まる。
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
- 画面: **AC9 Run** の **Settings** を開く（折りたたまれている）。
- タイムラインを最後のコマにしてある（画面に出ているコマが使われる）。

手順:

1. タイムラインを最後のコマに合わせる。
2. **Settings** の **To AC9 Cloth Retopo**（`ac9_cloth_sim.export_retopo`）を押す。本人が押す。

期待結果: 画面に、頂点数と、縫い目がどれだけ開いていたか（閉じる前）が出る。Cloth Retopo が有効なら、その Guide に自動で設定される。

制限・してはいけないこと: 同じ名前の Guide があれば作り直される。途中のコマで押すと、そのコマの形で Guide ができる。AI が押さない。

戻し方: Ctrl+Z。

## 症状から探す

README の『うまく動かないとき』の表を土台に、今のコードの表示で確かめ直した補足です。

最初に見ること（画面で見えるものだけ）:

| 症状 | 最初に見ること | してよいこと | しないこと |
|---|---|---|---|
| Tool タブに **AC9 Run** や **AC9 Garment** が無い | Properties エディタが Tool タブか。 | 場所を案内する。 | Preferences を AI が書き換える。 |
| **Simulate** が灰色 | **Placement** の **Body**。**Detect Guides** をしたか。 | **Body** と **Detect Guides** を案内する。 | 体を選び直して押す操作を AI がする。 |
| 「Set the Newton Python in the add-on preferences」 | Preferences の **Newton Python** | 指定を案内する。 | パスを推測して書く。 |
| 診断の位置印が出ない | オーバーレイと「表示：問題箇所」、診断の対象範囲・結果時刻・失効表示を確認。黄は縫合未照合の候補で、赤の確認済みと区別する。 | 古い結果なら再診断。現在の診断結果・各ビューの対象・マーカー投影位置を分けて確認する。 | Watchの設定が手動診断の前提だと言う。未検査や候補を0件・問題なしと説明する。 |
| 赤が出た | 状態表示の「the first (marked red): …」を書き写し、その場所の画像を撮る。 | 画像を見せ、本人に判断してもらう。 | 「異常です」「不良です」と言い切る。 |
| 布が交差した・巻き込んだか知りたい | **AC9 Run** の **Settings** の **Log**（`ac9_cloth_sim.log`）の `guard` の行。 | ログを読み上げる。 | 件数の意味を断定する。 |
| 経過が動かない・**Fix by Hand** が灰色 | **Fix by Hand** か **Back to Adjusted Shape** の後なら正常。 | 正常と伝える。Ctrl+Z で経過に戻る。 | 押していないのに動かないなら、古い .blend や一時フォルダの消失を疑い、記録して知らせる。 |
| 衿が裏返る・つなぎ目がねじれる | **AC9 Piece** の欄（**Ends** など）、**Flip the direction of**、置き方。 | 欄と置き方を見せる。手で直すなら **Fix by Hand**。 | 画面から変えられない縫う順番や置く角度を、勝手に変える。 |
| 新しい服が最初から崩れる | その場で **Esc**。どの段・何コマ目か。 | 状態表示を記録する。 | **Esc** を AI が押すこと自体は案内に留め、押させる。 |
| 最後の段で服がずっと動き続ける | 状態表示の段名と、最後の数コマで動いているか。 | **Esc** か **Stop** で止める案内。 | 計算の設定の変更。 |
| 結果が回ごとに違う | 同じ設定でも揺れる。 | 何度か回して見るよう案内する。 | 1 回で良否を言う。 |
| 結果の形が残っていない | **AC9 Garment** の **Result** の `Shape:` の行 | 保存（Ctrl+S）を案内する。 | AI が保存する。 |
| 画面の表記が資料と違う | アドオンが古く読み込まれたまま。 | Blender の再起動を案内する。 | 資料の名前を作って案内する。 |

利用者がお知らせするときに添えてもらうもの: 状態表示の `done:` の行（または `stopped` とエラー文）、押したボタンと入れた値、**Log** の内容、保存した .blend。数値は、利用者の画面で確かめた値と、開発側の実測を混ぜない。


### 折りの設定が効くとき

人向け正本: docs/ja/features/fold-roles.md。内部線の角度・方向は、調整した形ではTurnsがA/Bのときだけ片側を倒す。指定なしでも、開始前に角度があれば計算の折り目になる。内部線/縫い目の角度・方向、内部線Taperを実行中に変えられるのは、開始時に角度が0°でなかった線だけ。0°から新しく折る場合は、本人が停止して設定し再実行する。TurnsとLayersは実行中変更不可。Taperは角度ではなく長さで、ピース内に止まる端だけをなだらかにし、外周の端には効かない。両端が外周なら欄は出ず、現在角0°では無効。実行中の強化オン/オフ・倍率は変更可能。欄のグレーと理由を確認し、AIは実行/停止や形の編集を本人の代わりに行わない。
