# SPDX-License-Identifier: GPL-3.0-or-later
"""Overlay the Git-managed catalog into the whole site; reject stale deployments."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from publish_distribution import CATALOG, PREFIX, BASE_URL, command
from verify_release_assets import valid_asset_url, valid_sha256


def remote_head(root, name):
    value = command('git', 'ls-remote', 'origin', 'refs/heads/' + name, cwd=root)
    return value.split()[0] if value else ''


def assert_heads(source, catalog, actual_source, actual_catalog):
    if source != actual_source or catalog != actual_catalog:
        raise ValueError('Stale site/catalog generation; refusing deployment')


def overlay(root):
    root = Path(root)
    source = command('git', 'rev-parse', 'HEAD', cwd=root)
    catalog = remote_head(root, CATALOG)
    if catalog:
        command('git', 'fetch', 'origin', catalog, cwd=root)
        files = command('git', 'ls-tree', '-r', '--name-only', catalog, '--', 'website/static/extensions', cwd=root).splitlines()
        required = {PREFIX + '/' + n for n in ('index.json', 'index.html', 'publication-ledger.json', 'publication.json')}
        if not required.issubset(files):
            raise ValueError('Existing catalog branch is incomplete')
        for name in files:
            if not name.startswith('website/static/extensions/') or '..' in Path(name).parts:
                raise ValueError('Unexpected catalog path')
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(command('git', 'show', catalog + ':' + name, cwd=root) + '\n', encoding='utf-8')
    return {'source_head': source, 'catalog_sha': catalog}


def fetch_digest(url):
    checksum, size = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=60) as response:
        if response.status != 200:
            raise ValueError('Asset did not return HTTP 200')
        while data := response.read(1024 * 1024):
            size += len(data); checksum.update(data)
    return size, checksum.hexdigest()


def verify_index(index, ledger, publication, downloader=fetch_digest):
    if type(ledger) is not list:
        raise ValueError('Invalid retained catalog ledger')
    retained = {}
    for record in ledger:
        if (not isinstance(record, dict) or not valid_asset_url(record.get('url'))
                or not valid_sha256(record.get('sha256'))):
            raise ValueError('Invalid retained URL/hash')
        if record['url'] in retained and retained[record['url']] != record['sha256']:
            raise ValueError('Conflicting retained URL hashes')
        retained[record['url']] = record['sha256']
    rows = index.get('data', [])
    if index.get('version') != 'v1' or len(rows) != 2 or {r['id'] for r in rows} != {'ac9_cloth_sim', 'ac9_cloth_sim_runtime'}:
        raise ValueError('Unexpected public repository entries')
    references = publication['references']['packages']
    if set(references) != {'full', 'main', 'runtime'}:
        raise ValueError('Incomplete publication references')
    for row in rows:
        role = 'main' if row['id'] == 'ac9_cloth_sim' else 'runtime'
        reference = references[role]
        if (row['archive_url'] != reference['url'] or row['archive_size'] != reference['bytes']
                or row['archive_hash'] != 'sha256:' + reference['sha256']):
            raise ValueError('Public index differs from publication references')
    for reference in references.values():
        url, sha, size = reference['url'], reference['sha256'], reference['bytes']
        if (not valid_asset_url(url) or not url.startswith('https://github.com/openfashion-labs/ac9_cloth_sim/releases/download/')
                or retained.get(url) != sha or type(size) is not int or size <= 0):
            raise ValueError('Unretained or unexpected public asset')
        if downloader(url) != (size, sha):
            raise ValueError('Anonymous asset bytes differ from the catalog')


def guard(root, source, catalog):
    root = Path(root)
    assert_heads(source, catalog, remote_head(root, 'main'), remote_head(root, CATALOG))
    if catalog:
        command('git', 'fetch', 'origin', catalog, cwd=root)
        def read(name):
            return json.loads(command('git', 'show', catalog + ':' + PREFIX + '/' + name, cwd=root))
        verify_index(read('index.json'), read('publication-ledger.json'), read('publication.json'))
    else:
        scope_path = root / '.github/release_scope.json'
        if scope_path.exists() and json.loads(scope_path.read_text())['publication'].get('asset_publication_approved'):
            raise ValueError('An authorized distribution expects its persistent catalog; keep the existing site')
        # Missing branch is acceptable only before any catalog was published.
        try:
            with urllib.request.urlopen(BASE_URL + 'index.json', timeout=30):
                raise ValueError('Published catalog exists but its Git branch is missing')
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    assert_heads(source, catalog, remote_head(root, 'main'), remote_head(root, CATALOG))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('overlay', 'guard'))
    parser.add_argument('--source-head'); parser.add_argument('--catalog-sha', default='')
    args = parser.parse_args(); root = Path(__file__).resolve().parents[1]
    if args.phase == 'overlay':
        result = overlay(root)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                for key, value in result.items():stream.write(f'{key}={value}\n')
        print(json.dumps(result))
    else:
        if not args.source_head:parser.error('--source-head is required')
        guard(root, args.source_head, args.catalog_sha)


if __name__ == '__main__':
    main()
