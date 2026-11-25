# 公開済みZIPと検査済みZIPの照合（ローカル試作）

初期はpreviewの1系統。公開先の回答を待つ間に作った、公開処理の一部だけを担う検査。
アップロード、ダウンロード、目録更新、Git push、Pagesへの配信は実行しない。

呼び出し側は次の3点を準備する。

1. テストした時点で凍結した `build.json` と、そのfull/main/runtimeの3ZIP。テスト後に台帳を作り直さない。テストの合格判定自体は呼び出し側の責任。
2. 実際のRelease APIから取得した公開状態とURL、および実際にそのassetから取得したファイル。手で作った状態フラグや手元のコピーは、実公開の証拠にはならない。
3. 過去の公開台帳。初回だけ明示的な空配列 `[]`。台帳が紛失した場合に、初回と見なして進めない。

`assets.json` は3件の配列。各件は以下の形。runtimeは本体とは別の `runtime-1.0.0` ReleaseへのURLにできる。

```json
{
  "label": "main",
  "download_path": "/owned/download/ac9_cloth_sim-0.4.0.zip",
  "url": "https://example.invalid/owner/repo/releases/download/v0.4.0/ac9_cloth_sim-0.4.0.zip",
  "release_published": true,
  "release_draft": false,
  "release_prerelease": true
}
```

既存台帳は `[{"url": "...", "sha256": "..."}]`。系統の外で共有するruntimeも記録する。

```text
python tools/verify_release_assets.py --build tested/build.json --assets assets.json --existing retained.json
```

正常時だけ、参照URL/SHA/サイズと再利用の可否を標準出力へ返す。異常時は非ゼロ終了。
公開prereleaseは初期previewで認める。draft/未公開、moving latest URL、ファイル名違い、検査後のファイル変更、取得物の1byte変更、同一URLの異SHAを拒否する。

この検査だけでは、テストが実行されたこと、目録の版が後退しないこと、古いPages成果物の後着防止、Releaseの削除防止、実HTTP/権限/キャッシュの正しさまでは証明しない。実公開処理への接続と追加検証は公開先・権限・担当の回答後に行う。既存workflowの公開jobは変更していない。
