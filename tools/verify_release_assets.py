# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline release gate: compare tested files with downloaded release assets.

This never uploads, downloads, changes an index, or authorizes publication.
The caller supplies release API metadata and downloaded files; CI must collect
those from the actual service, rather than treating this rehearsal as evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


LABELS = {'full', 'main', 'runtime'}
_MISSING = object()


def valid_sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def valid_asset_url(value, filename=None):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(ord(c) <= 32 for c in value)):
            return False
    except ValueError:
        return False
    parts = parsed.path.split('/releases/download/')
    tail = parts[1].split('/') if len(parts) == 2 else []
    return (len(tail) == 2 and bool(tail[0]) and tail[0] != 'latest'
            and bool(tail[1]) and tail[1].endswith('.zip')
            and (filename is None or tail[1] == filename))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_release_assets(build, assets, existing=_MISSING):
    """Return references only when all three downloaded assets match the test ledger.

    `existing` is the retained publication ledger. An existing URL with different
    content is always an error; matching assets can be reused, including runtime.
    Published prereleases are valid for the initial preview channel.
    """
    packages = build.get('packages', {})
    if set(packages) != LABELS:
        raise ValueError('Expected exactly full, main, and runtime test records')
    if not isinstance(assets, list) or len(assets) != 3 or {a.get('label') for a in assets} != LABELS:
        raise ValueError('Expected exactly one release asset for each package')
    if existing is _MISSING:
        existing = []
    if type(existing) is not list:
        raise ValueError('Retained publication records must be a JSON array')
    retained = {}
    for item in existing:
        if (type(item) is not dict or not valid_asset_url(item.get('url'))
                or not valid_sha256(item.get('sha256'))):
            raise ValueError('Invalid retained publication URL or SHA256')
        url, sha = item['url'], item['sha256']
        if url in retained and retained[url] != sha:
            raise ValueError('Conflicting retained publication records')
        retained[url] = sha
    references, seen_urls = {}, set()
    for asset in assets:
        label = asset['label']
        expected = packages[label]
        tested = Path(expected['path'])
        downloaded = Path(asset['download_path'])
        expected_sha = expected['sha256']
        if not valid_sha256(expected_sha):
            raise ValueError('Invalid test record SHA256')
        if type(expected['bytes']) is not int or expected['bytes'] <= 0:
            raise ValueError('Invalid test record size')
        url = asset['url']
        if not valid_asset_url(url, tested.name):
            raise ValueError('Expected a versioned release download matching the tested filename')
        if url in seen_urls:
            raise ValueError('Release asset URL was reused for different packages')
        seen_urls.add(url)
        if asset.get('release_published') is not True or asset.get('release_draft') is not False:
            raise ValueError('Draft or unpublished releases cannot enter the preview index')
        for path in (tested, downloaded):
            if path.stat().st_size != expected['bytes'] or digest(path) != expected_sha:
                raise ValueError(f'{label}: bytes differ from the tested artifact')
        if url in retained and retained[url] != expected_sha:
            raise ValueError('Refusing to overwrite an existing release asset with different bytes')
        references[label] = {'url': url, 'sha256': expected_sha, 'bytes': expected['bytes'],
                             'reuse_existing': url in retained}
    return {'channel': 'preview', 'packages': references,
            'scope': 'supplied release metadata and downloaded files only; no deployment performed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--existing', type=Path, required=True,
                        help='Retained publication records; supply [] explicitly for the first publication')
    args = parser.parse_args()
    result = verify_release_assets(json.loads(args.build.read_text(encoding='utf8')),
                                   json.loads(args.assets.read_text(encoding='utf8')),
                                   json.loads(args.existing.read_text(encoding='utf8')))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
