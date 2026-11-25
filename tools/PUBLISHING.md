# 分離配布の公開手順

公開は `publish-distribution.yml` の手動実行で行う。タグの `release.yml` はビルド/検査だけを行い、既存assetを差し替えない。PRのCIが作った同じZIPを昇格させ、公開時に再ビルドしない。

## 公開前に揃えるもの

1. 本体/Runtime/全部入りのCIと、同じ成果物での導入検査を完了する。採用するrelease workflowのrun IDとPR head SHAを固定する。保持期限を過ぎたartifactは使えない。再実行でZIPを作り直した場合は別成果物として検査する。
2. Runtimeの別Release配布について、同梱NVIDIAライセンスの配布条件を確認する。既存の0.4.0全部入りへの承認を流用しない。単に「standalone SDKではない」と表示したことを条件確認の代わりにしない。
3. 接続処理と配布条件の確認後、maintainerが `.github/release_scope.json` の `asset_publication_approved` をtrueにし、解消した残件を外す。正式URLでの導入/更新確認は公開後に行うため残してよい。**現時点はfalseで実公開を禁止している。** 変更後の最終headについてCIを通し、owner承認を得てmainへ通常マージする。
4. 既存の権限を持つ担当者が、mainのマージcommitに対する `v<本体版>` を用意する。初回Runtimeは `runtime-<runtime版>` のタグも用意し、既存Runtimeタグを移動しない。`v*` の既存保護を解除/迂回しない。workflow自身はタグを作らない。

## 公開を実行する

mainを対象にpublish-distributionを手動実行し、検査したrun IDとレビューされたPR head SHAを渡す。PR headとsquash後のmainはcommit SHAが異なるが、ファイルのtreeは一致する必要がある。別の変更がmainへ入った場合は止まり、古い版をそのまま公開しない。

- 成功した同じrepoのrelease PR CI、merged PR、同じheadへの承認をAPIで検査する。source treeと現在mainも照合する。
- artifactを取得し、3ZIPのCRC/サイズ/SHA/ID/版/wheel構成を検査する。
- mainタグとRuntimeタグの存在を確認。本体タグは現在mainを指すことを確認する。
- 旧公開記録と同じURLに異なるSHAを割り当てない。既存assetはサイズ/利用可能なdigestを見た後、実ダウンロードしてSHAを照合する。`--clobber`は使わない。
- 新規assetはdraftへ置き、全assetを再取得照合してからprereleaseとして公開する。公開後にも匿名取得して3形式のサイズ/SHAを照合する。
- 検証済み参照から公開目録と導入ページを生成し、Gitの `distribution-catalog` ブランチへ記録する。本体コードの追加PRは作らない。mainへの直接pushやブランチ保護変更はしない。
- docs workflowをmainから明示実行する。GITHUB_TOKENによるpushだけに起動を依存しない。[GitHub公式のイベント説明](https://docs.github.com/en/actions/concepts/security/github_token)

## 説明書サイトと目録を一緒に維持する

docs workflowは固定したcatalog SHAの `website/static/extensions/` を通常のサイトへ取り込み、サイト全体をビルドする。他系統と通常の静的ファイルを保持する。公開処理とdocsのビルド/配信は同じconcurrency groupを使う。GitHub concurrencyはFIFOの保証ではないため、遅れて到着したmain/catalogの世代は配信直前のhead照合で拒否する。

配信前にcurrent main/catalogがビルド時から変わっていないことと、公開index・保持台帳・3形式の匿名取得bytesを照合する。取得失敗/世代不一致/台帳欠落は旧サイトを保持して停止する。初回でasset公開許可だけ済み、まだcatalogが無い場合も配信を止める。assetとcatalogを準備した後の再実行が配信の再開点となる。

GitHubのCLIは `--clobber` を付けると既存assetを先に削除するため、この経路では使用しない。[公式説明](https://cli.github.com/manual/gh_release_upload)

## 公開後の確認と中断からの再開

正式HTTPSのページ/index/本体/Runtime/全部入りを匿名取得できること、実目録とassetのSHA、Blenderで初回導入/本体だけの更新を確認する。ローカル候補のD&D成功を正式URLの結果へ流用しない。

asset公開後にサイト配信で止まっても、既存assetを上書きせず同じrunから再開する。同じURL/同じSHAなら再利用する。mainやcatalogが先へ進んだ場合は古いジョブを無理に再開せず、最新の状態を確認する。GitHub側の実書込権限、保護タグ操作、匿名HTTPS/CDN、最終Blender更新は外部環境で実行するまで未検証。
