# 2Dシルエットの検査

- silhouette_fixture.py: シミュ結果を使わない解析用の箱・平面型紙。使用前に有限性・面・寸法の台帳を出力する共通fixture。
- test_silhouette.py: Blender 5.2のヘッドレス検査。実Extension経路の登録、投影寸法・2チェック・データ不変・キャッシュ・変形/Modifier・空対象・保存再読込・パネル描画/poll。
- gui_silhouette.py: 隔離GUIで同梱アバターを使用前検査し、実GPUマスク・変更前後画像・実入力（--enable-event-simulate）・位置指定/取消・Undo/Redo・実F3を確認。終了時は自動で閉じる。
- 証拠フォルダを `--` の後に指定する。通常ユーザーのextensions/configで実行しない。出力ファイルのerrorなしと全assert通過を照合する。
- 初版の性能検査は同梱アバター約1.3万点。大規模な追加対象・任意のrigアニメーション・異なるGPUの性能保証は含まない。
