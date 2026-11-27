# 公開前の導入候補をローカルで検査する

`build_candidate_repository.py` はBlender標準の `server-generate` が作った本体/Runtimeの目録とZIPから、**別の新規フォルダ**に候補ページを作る。正式サイト・目録・Releaseの変更やアップロードは実行しない。

```text
python tools/build_candidate_repository.py --source-repo split/repository --out local-candidate --base-url http://127.0.0.1:8765/ --main-version 0.5.0
python -m http.server 8765 --bind 127.0.0.1 --directory local-candidate
```

ブラウザで `http://127.0.0.1:8765/` を開く。HTTPはloopbackだけを許容し、ローカル検査中のRuntimeをLANへ配信しない。`localhost` のURLを他人向けの公開プレビューURLとして案内しない。

リンクはBlender標準と同じ `repository`/Blender版/platform/python版のパラメータを持ち、配布元には候補の絶対URLを使う。ZIPのサイズ/SHA、manifestと目録の一致、本体/Runtime契約の一致を確認し、既存の出力先へ上書きしない。

初回は、未登録配布元の追加確認→配布元作成→同じ本体リンクをもう一度ドラッグ→本体導入確認→本体の「計算部品を入れる」→Runtime導入確認、という経路を検査する。隔離したBlender5.2のCONFIG/SCRIPTS/EXTENSIONSを先に検証し、個人設定の保存をしない。

候補ページの閲覧、HTTP受入、標準URLオペレーターによる導入、ブラウザからの実OSドラッグは異なる試験。URLをAPIへ渡して成功しても、実ドラッグ成功とは記録しない。操作ツールが別windowへのdragを拒否した場合は具体的なエラーを残す。

一般公開には別途、公開ホスト・実assetの到達性・権限・保持/配信順・配布条件の確認が必要。期限付きの認証付きCI artifactのURLを、Blenderが匿名で取得できる正式ZIPのURLへそのまま転用しない。既存Pagesへ候補を載せる場合も、全サイトと正式目録を保持した配信/権限の承認が必要。

この試験用ページは「準備中」であることを表示する。正式導線と案内の切替は、最終artifactでの検証と公開の承認を得てから行う。
