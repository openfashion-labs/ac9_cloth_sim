# AC9 Cloth Sim

[English](README.en.md)

Blenderの中で服の型紙を読み込み、編集し、縫い合わせてアバターに着せるアドオンです。
型紙を直して、もう一度着せ、布の落ち着き方を物理シミュレーションで確かめられます。

**[説明書（日本語）](https://openfashion-labs.github.io/ac9_cloth_sim/ja/) · [User guide (English)](https://openfashion-labs.github.io/ac9_cloth_sim/)**

## 動作環境・開発状況

Windows 64ビット / Blender 5.2 / NVIDIA GeForce RTX 20・30・40・50シリーズ。
詳しい対応条件やGPUの確認範囲は、説明書をご覧ください。

現在はα版です。機能・操作は今後変わります。保存した.blendが次の版で開けない場合があります。

## インストール

**Extensionsからインストール（0.4.1・公開準備中）**

正式公開前のため、Extensionsの導入ページはまだ利用できません。
公開後は、この位置から導入ページを開き、ページ内のボタンをBlenderへドラッグ＆ドロップして本体を入れます。
シミュレーションに必要なNewtonは、アドオン設定から追加します。
対応するNewtonが変わらない本体更新では、大きな部品を再ダウンロードせずに使えます。

候補の確認方法は[Extensions候補ページの検査案内](tools/CANDIDATE_REPOSITORY.md)、残る作業は[0.4.1の準備状況](RELEASE_STATUS.md)をご覧ください。

<details>
<summary>現在の公開版・ZIPから手動でインストールする場合</summary>

現在は[0.4.0のRelease](https://github.com/openfashion-labs/ac9_cloth_sim/releases/tag/v0.4.0)のAssetsから `ac9_cloth_sim-0.4.0.zip` を選び、展開せずに使います。
0.4.1では全部入りZIP（`ac9_cloth_sim-0.4.1-full.zip`）も提供する予定です。まだ公開していません。
詳しい手順は説明書をご覧ください。

</details>

## 困ったとき

使い方とトラブル対処は説明書へ。不具合は[Issues](https://github.com/openfashion-labs/ac9_cloth_sim/issues)へご報告ください。

## ライセンス

アドオン本体は無料で、商用にも使えます。[GPL-3.0-or-later](LICENSE)。Copyright (C) 2026 OpenFashion。
Newtonの改変部分は[Apache-2.0](solver/newton_patch/LICENSE)（[NOTICE](solver/newton_patch/NOTICE)）。
第三者部品はそれぞれの条件に従います。[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)をご覧ください。
付属アバターは[CC0 1.0](avatar_LICENSE.txt)です。
