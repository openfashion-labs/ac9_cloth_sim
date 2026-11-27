# 型紙の付属点・生地・共有メッシュの回帰

- `test_layout_point_references.py`: 全体移動/回転/拡縮、所属ごとのボタン/穴/仮留め、双子の未選択側、候補の独立性、計算入力の頂点対応。
- `test_layout_point_display.py`: 実commitで3D追随ON/OFFとも保存座標と型紙ボタン実メッシュを一致させ、保存再読込でも維持する。
- `test_clone_fabric.py`: 元に明示された有効生地IDと双子からの継承を保つ。明示既定と既定追随を区別し、縫合の有無も確認する。
- `test_fold_shared_mesh.py`: 健全な人工2枚＋外部参照で、通常拒否/無操作取消/受理/親取消/既存ownerの競合を確認する。対象外の座標・属性・元共有ID・fake user・一時メッシュ件数を照合する。
- `test_fold_shared_native.py`: 私有GUIの実キーイベントで既存の折る側プロパティを変更し、Blender標準Undo/Redo・保存再読込を確認する。入力はこのBlenderのevent_simulateだけで送り、OSや本人のセッションへ送らない。前後画像と全座標を結果JSONの隣に出す。
- `test_shared_archive_scope.py`: 保存済みの旧Local退避・中心原点/鏡映操作側への退避差替えと、親の共有元を継いだ子の拒否。生きた外部参照と親の入力IDを保護する。
- `test_placement_settings_rollback.py`: 有限・非退化の共有小片で、設定のwrite末尾/refresh/再配置/finish/通常拒否を同じ保存境界で戻し、外部本文・active/Noneの所有を守る。標準服の実RNA適用/拒否→UndoRedo/次tickは夜間の追加probeで照合。物理品質は判定しない。
- `test_placement_mixed_result.py`: 部分Resetと実在結果面が混在しても未選択片を再配置せず、基底UNKNOWNを結果へ昇格しない。全配置の従来一括生成、選択した結果側の欠損job拒否、新規ピースの実体化も対照にする。
- `test_native_runtime_long_paths.py`: 実Blenderホストの212/245 UTF-16単位stageで、原子的な設定/descriptor書込・workerの最終公開・abort/close/cleanupを確認する。265単位を超える既所有/異所有のdescriptorを見失わず拒否し、UNC/既拡張表記・非Windows分岐・長いruntimeディレクトリも対照にする。物理計算はしない。

人工小片で保存形式と所有を分離する検査であり、服の外観や物理品質の判定ではない。前2つの純Python試験は通常Pythonで、Blenderデータを使う2つはアドオン登録済みBlender5.2ヘッドレスで実行する。開発環境では正式の `cloth_sim_dev/autotest/run.py` がPythonを選ぶ。
