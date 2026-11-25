# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an unpublished candidate repository/page from a validated Blender index.

Only writes a new output folder. No upload, API mutation, or official index update.
Use a loopback HTTP server for local rehearsal; a public base URL alone does not
publish anything or establish permission to redistribute the runtime.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import tomllib
from urllib.parse import urlencode, urljoin, urlsplit
import zipfile


IDS = {'ac9_cloth_sim', 'ac9_cloth_sim_runtime'}


def build_candidate(source, output, base_url, main_version):
    source, output = Path(source).resolve(), Path(output).resolve()
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.query
            or parsed.fragment or parsed.username or parsed.password):
        raise ValueError('Use an absolute candidate base URL without query or credentials')
    if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '[::1]', '::1'}:
        raise ValueError('Plain HTTP is restricted to loopback rehearsal')
    base_url = base_url.rstrip('/') + '/'
    if output.exists() or output == source or source in output.parents:
        raise ValueError('Candidate output must be a new folder outside the source repository')
    index = json.loads((source / 'index.json').read_text(encoding='utf8'))
    if not isinstance(index, dict) or index.get('version') != 'v1':
        raise ValueError('Expected Blender repository schema v1')
    rows = index.get('data')
    if (not isinstance(rows, list) or len(rows) != 2 or not all(isinstance(r, dict) for r in rows)
            or {r.get('id') for r in rows} != IDS):
        raise ValueError('Expected exactly the main and runtime extension records')
    packages, contracts, links = {}, {}, {}
    for row in rows:
        relative = row['archive_url']
        parts = urlsplit(relative)
        filename = relative.removeprefix('./')
        if (parts.scheme or parts.netloc or parts.query or parts.fragment or '\\' in filename
                or '/' in filename or filename in {'', '.', '..'} or not filename.endswith('.zip')):
            raise ValueError('Input index must refer to adjacent local ZIP files')
        archive = source / filename
        if not archive.resolve().is_relative_to(source):
            raise ValueError('Archive escaped the source directory')
        with archive.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if archive.stat().st_size != row['archive_size'] or row['archive_hash'] != 'sha256:' + sha:
            raise ValueError('Archive differs from the validated index')
        with zipfile.ZipFile(archive) as zipped:
            manifest = tomllib.loads(zipped.read('blender_manifest.toml').decode('utf8'))
            for key in ('id', 'type', 'version', 'blender_version_min', 'blender_version_max', 'platforms'):
                if manifest.get(key) != row.get(key):
                    raise ValueError('Index and archive manifest disagree: ' + key)
            if manifest['type'] != 'add-on':
                raise ValueError('Candidate must install an add-on')
            contracts[row['id']] = json.loads(zipped.read('runtime_contract.json'))
        if row['id'] == 'ac9_cloth_sim' and row['version'] != main_version:
            raise ValueError('Wrong main candidate version')
        query = {'repository': urljoin(base_url, 'index.json')}
        for key in ('blender_version_min', 'blender_version_max'):
            if row.get(key):
                query[key] = row[key]
        for key in ('platforms', 'python_versions'):
            if row.get(key):
                query[key] = ','.join(row[key])
        links[row['id']] = urljoin(base_url, filename) + '?' + urlencode(query)
        packages[row['id']] = {'filename': filename, 'sha256': sha, 'bytes': archive.stat().st_size,
                               'version': row['version']}
        row['archive_url'] = './' + filename
    if contracts['ac9_cloth_sim'] != contracts['ac9_cloth_sim_runtime']:
        raise ValueError('Main and runtime contracts disagree')
    if contracts['ac9_cloth_sim']['runtime_id'] != 'ac9_cloth_sim_runtime':
        raise ValueError('Unexpected runtime contract ID')
    if contracts['ac9_cloth_sim']['runtime_version'] != packages['ac9_cloth_sim_runtime']['version']:
        raise ValueError('Runtime contract version disagrees with its extension')
    label = html.escape(main_version)
    main_link = html.escape(links['ac9_cloth_sim'], quote=True)
    runtime_link = html.escape(links['ac9_cloth_sim_runtime'], quote=True)
    repo_url = html.escape(urljoin(base_url, 'index.json'), quote=True)
    page = f'''<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AC9 Cloth Sim {label} 導入候補</title>
<style>body{{font:18px/1.7 sans-serif;max-width:740px;margin:36px auto;padding:0 24px;color:#20252b}}
.notice{{padding:12px;background:#fff0d1}}a.install{{display:inline-block;background:#265da0;color:white;padding:16px 24px;border-radius:6px}}
code{{overflow-wrap:anywhere}}small{{color:#48535e}}</style>
<h1>AC9 Cloth Sim {label} 導入候補</h1>
<p class="notice">検証用の候補ページです。正式配布への切り替えはまだ行っていません。</p>
<p><a id="install-main" class="install" draggable="true" href="{main_link}">このリンクをBlenderへドラッグ</a></p>
<ol><li>上のリンクを、隔離した検査用Blender 5.2へドラッグします。</li>
<li>配布元が未登録なら追加を確認し、<strong>同じリンクをもう一度ドラッグ</strong>します。</li>
<li>本体のインストールを確認します。本体の案内に出る「計算部品を入れる」（Install Runtime）から計算用部品を入れます。</li>
<li>部品が利用可能になったことを確認します。シミュレーションの品質検査は別工程です。</li></ol>
<p>オンラインアクセスを許可していない環境では、オンライン導入はできません。個人設定ではなく検査用環境で確認してください。</p>
<details><summary>配布元のURLと部品の検査用リンク</summary><p><code>{repo_url}</code></p>
<p><a id="install-runtime" draggable="true" href="{runtime_link}">Runtimeを標準インストーラで検査するリンク</a></p></details>
<p><small>初回は本体と計算用部品の両方が必要です。部品が変わらない更新では本体だけを取得する設計です。
公開前の配布条件・正式導入リンクの確認は残っています。</small></p></html>'''
    output.mkdir(parents=True)
    for record in packages.values():
        shutil.copyfile(source / record['filename'], output / record['filename'])
    (output / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf8')
    (output / 'index.html').write_text(page, encoding='utf8')
    result = {'scope': 'unpublished candidate only', 'base_url': base_url, 'main_version': main_version,
              'links': links, 'packages': packages, 'source_index_sha256': hashlib.sha256((source / 'index.json').read_bytes()).hexdigest()}
    (output / 'candidate.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--main-version', required=True)
    args = parser.parse_args()
    print(json.dumps(build_candidate(args.source_repo, args.out, args.base_url, args.main_version), indent=2))


if __name__ == '__main__':
    main()
