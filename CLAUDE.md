# AC9 Cloth Sim — 開発ルール

このファイルはこのリポジトリ専用の開発ルール（リポジトリに同梱して公開する。配布 ZIP には入らない）。
人にも AI にも同じルールを当てる。

## 何のアドオンか
型紙（BOM.xml・DXF・UV シーム付きメッシュ、またはゼロから描いた型紙）を縫い合わせ、Blender の中でアバターに着せる。
計算は NVIDIA Newton（VBD）を別プロセスの Python で回す（`solver/`）。`solver/newton_patch/` は Newton 1.6.0 の改変コピーで
Apache-2.0（同じフォルダの `LICENSE`・`NOTICE`）。それ以外は GPL-3.0-or-later。

## 命名
- クラスは `AC9_OT_*`／`AC9_PT_*`、オペレーターの bl_idname は `ac9_cloth_sim.*`、シーンのプロパティは `scene.ac9_cloth_sim`。
- 英語はアメリカ英語（color・center・gray・neighbor・meter）。
- .blend に残る名前（プロパティ名・列挙の識別子・オブジェクト名・JSON のキー・bl_idname）を変えるときは、古いファイルが開けることを確かめる。

## 公開に向けて守ること
- **絶対パス・個人名・顧客名や商品名を、コード・コメント・コミットメッセージ・PR 文面に書かない。** このリポジトリは一般公開されている。
- **作業データの固有名も書かない。** .blend のファイル名、オブジェクト名、コレクション名、シェイプキー名、マテリアル名は、
  案件名そのものでなくても、データの作り方と命名の習慣が読み取れる。**一度 push したコミットメッセージは消せない。**
- **ただし実測値は落とさない。** どこで測ったかは匿名化して書けば足りる（「試験用の T シャツで」「12 枚のシャツで」）。
  数値・Before/After・件数はそのまま残す。
- **他社の製品名を、画面・説明文・README・ソースのコメントに書かない。** 例外は読み込むファイル形式の名前（`BOM.xml` を
  書き出すソフトの名前など、書かないと使い手に伝わらないもの）と、使っているライブラリの識別子・クレジット。
  「〜と同じ動き」「〜に合わせた値」のように他社製品と比べる書き方は、例外に入らない。
- **画面に出る文字（`description`・`bl_label`・`report`・オペレーターの docstring・`i18n_ja.py`）に、開発中のメモを入れない**
  （日付、`(TEST …)`、レビューの名前、社内の記録の ID）。試験中の機能だと伝えたいときは「a test feature」とだけ書く。
- **利用者向けの文言（画面・README・docs）に、開発者のパソコンで測った時間を書かない。** 「少し待ちます」「パソコンによって変わります」。
  コメントに実測を残すのはよい。
- ソースのコメントにある `DECISIONS <id>`・`RESULT_*`・`experiments/…` などは、開発元の社内の記録を指す（外からは読めない）。
  設計の理由をたどる手がかりとして残してある。新しく書くときは、理由そのものをコメントに書き、ID は添えるだけにする。
- テスト用のアセットは、商用の実データから作らない（合成データにする）。
- `maintainer` 欄とログの接頭辞は `AC9` で統一する（`blender_manifest.toml`）。

## 配布
- 配布 ZIP に入らないもの: `docs/`・`website/`・`.github/`・`CLAUDE.md`・`CHANGELOG.md`（`blender_manifest.toml` の `paths_exclude_pattern`）。`wheels/`（`release.yml` が PyPI から落とす）・`THIRD_PARTY_NOTICES.txt`・`licenses/`・`BUILD_INFO.txt`（`release.yml` が書く）は ZIP に入る。`LLM/` と `tests/` は ZIP に入る（LLM/ は AICompanion が読む説明書で、その封印がアドオンの .py 全部の一覧を見るため）。
- 秘密情報の検査（gitleaks）は `.github/workflows/gitleaks.yml` が PR と main への push で回す。コミットに社内の名前が入っていないかは機械では検査しないので、上の「公開に向けて守ること」を自分で確かめる。
- 公開の流れ: 手元のブランチは push せず細かくコミットしてよい。版を出すときに main へ 1 コミットにまとめて入れ、変更点は `CHANGELOG.md` に書く（`## [X.Y.Z] - 日付` の節。`[Unreleased]` に溜めて版を出すときに付け替える）。
- リリース: `blender_manifest.toml` の `version` と `CHANGELOG.md` の節を更新した PR をマージしたあと、admin が `vX.Y.Z` タグを push する。`.github/workflows/release.yml` が Blender を落として ZIP をビルドし、draft の Release に添付する（本文は CHANGELOG の該当節）。本文を確かめて Release を公開する。
- 計算に使う wheel（newton・warp-lang の Windows 用）はリポジトリに置かない（warp-lang が約 150 MB）。`release.yml` が PyPI から `blender_manifest.toml` の `wheels` と同じ版を落とし、sha256 を確かめてから ZIP に入れる。版を変えるときは manifest・`release.yml` の sha256・`THIRD_PARTY_NOTICES.txt`・`licenses/` を一緒に直す。newton を上げるときは `solver/newton_patch/` と `solver/newton_vbd.py` の `NEWTON_BASE_SHA256` も。
- 版のコミットには、`LLM/` の封印（`LLM/manifest.toml` の `build_id` と `__init__.py` の `AC9_GUIDANCE_BUILD`）を開発元が入れておく。封印はアドオンの .py 全部のハッシュを持つので、.py を変えたコミットでは古くなる（AI アシスタントがその説明書を使わなくなるだけで、アドオンの動きには関係しない）。
