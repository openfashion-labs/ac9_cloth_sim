# 一周テープの回帰

- `test_tape_points.py`: 3点平面、閉じた断面の一致、分離した輪・開断面・不定な3点の拒否、旧1点周長、再採寸失敗と復旧、失敗した編集の不書込み、鏡写しの左右名。Blenderでは9件。Blender外では保存系3件を除外。
- 実行: Blender 5.2を絶対パスで `-b --factory-startup --python <このフォルダ/test_tape_points.py> -- <結果.json絶対パス>`。空のBLENDER_USER_EXTENSIONSと共通のBLENDER_SYSTEM_SCRIPTSを指定する。
- 人工閉メッシュで断面の契約を分離する。服の物理試験の代用ではない。
- 道具の実入力・確定/取消・2本のUndo/Redo・点ドラッグ・旧1点/なぞる/直線は、開発側で別途実施した検証です。標準服の人体の評価済み頂点16620・三角形33216から作った表示コピーで確認し、物理計算はしていません。この記録は上記の公開リポジトリ内で再実行できる単体試験と区別します。
- 日英の操作説明: `docs/ja/features/measure.md` / `docs/en/features/measure.md`。
