# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate a public preview catalog from independently verified asset references.

This writes only a new local directory. It performs no upload, deployment or
network verification; the publisher must provide actual downloaded-asset checks.
"""
import argparse
import html
import json
from pathlib import Path
import tempfile
from urllib.parse import urlencode, urlsplit

from build_candidate_repository import build_candidate
from verify_release_assets import valid_asset_url, valid_sha256

EXPECTED_BASE = 'https://openfashion-labs.github.io/ac9_cloth_sim/extensions/preview/'


def build_public(source, references, existing, output, base_url, version):
    output, source = Path(output), Path(source)
    if base_url != EXPECTED_BASE or references.get('channel') != 'preview':
        raise ValueError('Unexpected preview channel or repository URL')
    if output.exists() or type(existing) is not list:
        raise ValueError('New output directory and explicit retained ledger are required')
    packages = references.get('packages', {})
    if set(packages) != {'full', 'main', 'runtime'}:
        raise ValueError('All three publication references are required')
    retained = {}
    records = []
    for record in existing:
        if (not isinstance(record, dict) or not valid_asset_url(record.get('url'))
                or not valid_sha256(record.get('sha256'))):
            raise ValueError('Invalid retained publication record')
        if record['url'] in retained and retained[record['url']] != record['sha256']:
            raise ValueError('Conflicting retained publication records')
        retained[record['url']] = record['sha256']; records.append(record)
    for role, record in packages.items():
        url, sha, size = record.get('url'), record.get('sha256'), record.get('bytes')
        if (not valid_asset_url(url) or not url.startswith('https://github.com/openfashion-labs/ac9_cloth_sim/releases/download/')
                or not valid_sha256(sha) or type(size) is not int or size <= 0):
            raise ValueError('Invalid publication reference')
        if url in retained and retained[url] != sha:
            raise ValueError('Refusing retained URL content changes')
        if url not in retained:
            records.append({'url': url, 'sha256': sha, 'bytes': size}); retained[url] = sha
    if Path(urlsplit(packages['full']['url']).path).name != f'ac9_cloth_sim-{version}-full.zip':
        raise ValueError('Unexpected full distribution filename')
    # Reuse ZIP/manifest/contract checks without copying wheels into the website.
    with tempfile.TemporaryDirectory() as temporary:
        checked = build_candidate(source, Path(temporary) / 'checked', base_url, version, copy_archives=False)
    index = json.loads((source / 'index.json').read_text(encoding='utf-8'))
    links = {}
    for row in index['data']:
        role = 'main' if row['id'] == 'ac9_cloth_sim' else 'runtime'
        record, local = packages[role], checked['packages'][row['id']]
        if record['sha256'] != local['sha256'] or record['bytes'] != local['bytes']:
            raise ValueError('References differ from checked repository bytes')
        if Path(urlsplit(record['url']).path).name != local['filename']:
            raise ValueError('Published filename differs from the checked artifact')
        row['archive_url'] = record['url']
        query = {'repository': base_url + 'index.json'}
        for key in ('blender_version_min', 'blender_version_max'):
            if row.get(key):query[key] = row[key]
        for key in ('platforms', 'python_versions'):
            if row.get(key):query[key] = ','.join(row[key])
        links[role] = record['url'] + '?' + urlencode(query)
    escape = lambda value: html.escape(value, quote=True)
    page = f'''<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AC9 Cloth Sim {escape(version)} — 実験版</title>
<style>body{{font:18px/1.7 sans-serif;max-width:740px;margin:36px auto;padding:0 24px}}a.install{{display:inline-block;background:#265da0;color:white;padding:16px 24px;border-radius:6px}}code{{overflow-wrap:anywhere}}</style>
<h1>AC9 Cloth Sim {escape(version)}</h1><p>週次更新の実験版（Preview）です。Blender 5.2・Windows x64向けです。</p>
<p><a class="install" draggable="true" href="{escape(links['main'])}">このリンクをBlenderへドラッグ</a></p>
<ol><li>Blenderのオンラインアクセスを許可し、本体リンクをドラッグします。</li>
<li>配布元が未登録なら追加を確認し、同じリンクをもう一度ドラッグします。</li>
<li>本体を導入し、「計算部品を入れる」から対応するRuntimeを導入します。</li></ol>
<p>初回は本体とRuntimeの両方が必要です。対応するRuntimeが変わらない本体更新では、Runtimeを再取得しません。</p>
<p>手動導入には<a href="{escape(packages['full']['url'])}">全部入りZIP</a>も利用できます。既存環境からの自動移行は行いません。</p>
<details><summary>配布元と部品</summary><code>{escape(base_url+'index.json')}</code><p><a draggable="true" href="{escape(links['runtime'])}">対応Runtime</a></p></details></html>'''
    output.mkdir(parents=True)
    (output / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'index.html').write_text(page + '\n', encoding='utf-8')
    (output / 'publication-ledger.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    return {'channel': 'preview', 'links': links, 'retained_records': len(records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', type=Path, required=True); parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--existing', type=Path, required=True); parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--base-url', required=True); parser.add_argument('--main-version', required=True)
    args = parser.parse_args()
    print(json.dumps(build_public(args.source_repo, json.loads(args.references.read_text()),
                                 json.loads(args.existing.read_text()), args.out, args.base_url, args.main_version)))


if __name__ == '__main__':main()
