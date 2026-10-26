# website

[Docusaurus](https://docusaurus.io/) で作ったドキュメントサイト。マニュアル本文は
このディレクトリの外、リポジトリルートの `docs/en/` と `docs/ja/` に置く
（画像は `docs/images/`）。`docs/en` が無い間はトップページに準備中メッセージを出す。

## ローカル開発

```bash
npm install
npm run start
```

## ビルド

```bash
npm run build
```

`build/` に静的ファイルが出る。デプロイは `.github/workflows/docs.yml` が
GitHub Pages へ行う（このディレクトリから手動デプロイはしない）。
