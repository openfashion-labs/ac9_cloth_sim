# SPDX-License-Identifier: GPL-3.0-or-later
"""Promote one reviewed CI build without rebuilding or overwriting release assets.

Only the explicit --execute path performs external mutations. License/asset
authorization is recorded separately in release_scope.json by the maintainer.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import urllib.request

from check_release_scope import validate_packages, validate_plan
from verify_release_assets import verify_release_assets, valid_asset_url, valid_sha256

CATALOG = 'distribution-catalog'
PREFIX = 'website/static/extensions/preview'
BASE_URL = 'https://openfashion-labs.github.io/ac9_cloth_sim/extensions/preview/'


def command(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(args[:2]))
    return result.stdout.strip()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require_asset_authorization(scope):
    publication = scope.get('publication', {})
    if publication.get('asset_publication_approved') is not True:
        raise ValueError('Asset publication has not been approved')
    if set(publication.get('remaining', [])) - {'production_install_update_check'}:
        raise ValueError('Connection or distribution terms remain unresolved')


def validate_run(run, sha, repo):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Expected an exact source SHA')
    if (run.get('head_sha') != sha or run.get('event') != 'pull_request'
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('path') != '.github/workflows/release.yml'
            or run.get('head_repository', {}).get('full_name') != repo):
        raise ValueError('Expected successful release CI from the same repository and source')


def require_merged_approval(pulls, reviews, sha, repo):
    candidates = [p for p in pulls if p.get('merged_at') and p['head']['sha'] == sha
                  and p['head']['repo']['full_name'] == repo and p['base']['ref'] == 'main']
    latest = {}
    for review in reviews:
        if review.get('state') not in {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'}:
            continue
        user = review.get('user') or {}
        if type(user.get('id')) is not int:
            raise ValueError('Review author must be identifiable')
        latest[user['id']] = review
    if (len(candidates) != 1 or any(r['state'] == 'CHANGES_REQUESTED' for r in latest.values())
            or not any(r['state'] == 'APPROVED' and r.get('commit_id') == sha for r in latest.values())):
        raise ValueError('A merged PR and approval of this exact source are required')
    return candidates[0]


def asset_action(existing, expected):
    if existing is None:
        return 'upload'
    if existing.get('size') != expected['bytes']:
        raise ValueError('Existing asset size differs; never overwrite')
    remote_digest = existing.get('digest')
    if remote_digest and remote_digest != 'sha256:' + expected['sha256']:
        raise ValueError('Existing asset hash differs; never overwrite')
    return 'verify-and-reuse'


def planned_asset_urls(build, version, repo, retained):
    if type(retained) is not list:
        raise ValueError('Retained ledger must be a list')
    old = {}
    for record in retained:
        if (not isinstance(record, dict) or not valid_asset_url(record.get('url'))
                or not valid_sha256(record.get('sha256'))):
            raise ValueError('Invalid retained publication record')
        if record['url'] in old and old[record['url']] != record['sha256']:
            raise ValueError('Conflicting retained records')
        old[record['url']] = record['sha256']
    urls = {}
    for role, entry in build['packages'].items():
        tag = 'runtime-' + build['runtime_version'] if role == 'runtime' else 'v' + version
        url = f'https://github.com/{repo}/releases/download/{tag}/{Path(entry["path"]).name}'
        if not valid_asset_url(url) or url in old and old[url] != entry['sha256']:
            raise ValueError('Planned URL conflicts with retained bytes')
        urls[role] = url
    return urls


def reject_downgrade(old_index, new_index):
    old = {r['id']: r['version'] for r in old_index['data']}
    for row in new_index['data']:
        version = row['version']
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError('Expected a numeric Blender extension version')
        if row['id'] in old and tuple(map(int, version.split('.'))) < tuple(map(int, old[row['id']].split('.'))):
            raise ValueError('Refusing catalog downgrade')


def relocate_build(build, directory, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', str(build.get('runtime_version', ''))):
        raise ValueError('Expected an explicit runtime version')
    expected = {'full': ('full', f'ac9_cloth_sim-{version}-full.zip'),
                'main': ('repository', f'ac9_cloth_sim-{version}.zip'),
                'runtime': ('repository', f'ac9_cloth_sim_runtime-{build["runtime_version"]}.zip')}
    if set(build.get('packages', {})) != set(expected):
        raise ValueError('Expected full, main and runtime')
    for role, parts in expected.items():
        build['packages'][role]['path'] = str(Path(directory).joinpath(*parts).resolve())
    validate_packages(build, version)
    return build


class Publisher:
    def __init__(self, root, repo):
        self.root, self.repo = Path(root).resolve(), repo
        if repo != 'openfashion-labs/ac9_cloth_sim':
            raise ValueError('Unexpected publication repository')

    def api(self, endpoint):
        return json.loads(command('gh', 'api', f'repos/{self.repo}/{endpoint}'))

    def api_list(self, endpoint):
        pages = json.loads(command('gh', 'api', '--paginate', '--slurp', f'repos/{self.repo}/{endpoint}'))
        return [item for page in pages for item in page]

    def current_head(self):
        return command('git', 'ls-remote', 'origin', 'refs/heads/main', cwd=self.root).split()[0]

    def release(self, tag):
        # Listing first distinguishes an absent release from an API/auth failure.
        pages = json.loads(command('gh', 'api', '--paginate', '--slurp', f'repos/{self.repo}/releases?per_page=100'))
        matches = [r for page in pages for r in page if r['tag_name'] == tag]
        if len(matches) > 1:
            raise ValueError('Duplicate release tag')
        return matches[0] if matches else None

    def ensure_asset(self, tag, entry, source_sha):
        if tag.startswith('runtime-'):
            notes = self.root / '.github/runtime_release_notes.md'
            if not notes.is_file() or not notes.read_text(encoding='utf-8').strip():
                raise ValueError('Runtime release notes must be prepared before publication')
            note_arguments = ('--notes-file', str(notes))
        else:
            note_arguments = ('--notes', 'AC9 Cloth Sim preview distribution. See the versioned installation guide and licenses.')
        release = self.release(tag)
        if release is None:
            command('gh', 'release', 'create', tag, '--repo', self.repo, '--target', source_sha,
                    '--verify-tag', '--draft', '--prerelease', '--title', tag, *note_arguments)
            release = self.release(tag)
        name = Path(entry['path']).name
        matches = [a for a in release['assets'] if a['name'] == name]
        if len(matches) > 1:
            raise ValueError('Duplicate asset name')
        action = asset_action(matches[0] if matches else None, entry)
        if action == 'upload':
            command('gh', 'release', 'upload', tag, entry['path'], '--repo', self.repo)
        # Authenticated download also works for new draft assets; compare before publication.
        with tempfile.TemporaryDirectory() as temporary:
            command('gh', 'release', 'download', tag, '--repo', self.repo, '--pattern', name, '--dir', temporary)
            if digest(Path(temporary) / name) != entry['sha256']:
                raise ValueError('Uploaded/reused asset differs from tested bytes')
        return {'tag': tag, 'name': name, 'url': f'https://github.com/{self.repo}/releases/download/{tag}/{name}'}

    def publish_assets(self, build, version, head, work, ledger):
        roles = {}
        for role, entry in build['packages'].items():
            tag = 'runtime-' + build['runtime_version'] if role == 'runtime' else 'v' + version
            roles[role] = self.ensure_asset(tag, entry, head)
        for tag in sorted({r['tag'] for r in roles.values()}):
            release = self.release(tag)
            if release['draft']:
                command('gh', 'release', 'edit', tag, '--repo', self.repo, '--draft=false', '--prerelease')
        public_assets = []
        for role, record in roles.items():
            release = self.release(record['tag'])
            downloaded = work / ('public-' + record['name'])
            with urllib.request.urlopen(record['url'], timeout=60) as response, downloaded.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            public_assets.append({'label': role, 'url': record['url'], 'download_path': str(downloaded),
                                  'release_published': bool(release.get('published_at')), 'release_draft': release['draft']})
        return verify_release_assets(build, public_assets, ledger)

    def execute(self, run_id, sha, work):
        if not re.fullmatch(r'[1-9][0-9]*', run_id):
            raise ValueError('Expected a numeric run ID')
        scope = validate_plan(self.root)
        require_asset_authorization(scope)
        run = self.api(f'actions/runs/{run_id}')
        validate_run(run, sha, self.repo)
        command('git', 'fetch', 'origin', sha, cwd=self.root)
        head = command('git', 'rev-parse', 'HEAD', cwd=self.root)
        if self.current_head() != head or command('git', 'diff', '--name-only', sha, head, cwd=self.root):
            raise ValueError('Reviewed CI tree must exactly match current main')
        pulls = self.api_list(f'commits/{sha}/pulls?per_page=100')
        matching = [p for p in pulls if p.get('merged_at') and p['head']['sha'] == sha]
        if len(matching) != 1:
            raise ValueError('Expected one merged PR for the tested head')
        reviews = self.api_list(f'pulls/{matching[0]["number"]}/reviews?per_page=100')
        require_merged_approval(pulls, reviews, sha, self.repo)
        artifact = work / 'tested'
        command('gh', 'run', 'download', run_id, '--repo', self.repo, '--name', 'split-distribution-preview', '--dir', str(artifact))
        version = tomllib.loads((self.root / 'blender_manifest.toml').read_text())['version']
        build = relocate_build(json.loads((artifact / 'build.json').read_text()), artifact, version)
        if version != scope['version']:
            raise ValueError('Scope and artifact versions differ')
        for tag in ('v' + version, 'runtime-' + build['runtime_version']):
            # Protected release tags are prepared by the existing authorized
            # maintainer procedure. Never bypass tag rules or auto-create tags.
            if not command('git', 'ls-remote', 'origin', 'refs/tags/' + tag, cwd=self.root):
                raise ValueError('Required remote tag is missing: ' + tag)
        command('git', 'fetch', 'origin', 'tag', 'v' + version, cwd=self.root)
        if command('git', 'rev-parse', 'v' + version + '^{commit}', cwd=self.root) != head:
            raise ValueError('Main release tag must identify the reviewed main commit')
        remote = command('git', 'ls-remote', 'origin', 'refs/heads/' + CATALOG, cwd=self.root)
        parent = remote.split()[0] if remote else None
        catalog = work / 'catalog'
        if parent:
            command('git', 'fetch', 'origin', parent, cwd=self.root)
        command('git', 'worktree', 'add', '--detach', str(catalog), parent or head, cwd=self.root)
        destination = catalog / PREFIX
        ledger = json.loads((destination / 'publication-ledger.json').read_text()) if parent else []
        # No mutation has occurred before every source/authorization/package/retained-ledger check.
        planned_asset_urls(build, version, self.repo, ledger)
        if parent:
            reject_downgrade(json.loads((destination / 'index.json').read_text()),
                             json.loads((artifact / 'repository/index.json').read_text()))
        if self.current_head() != head:
            raise ValueError('main advanced before publication')
        references = self.publish_assets(build, version, head, work, ledger)
        refs_path, old_path = work / 'references.json', work / 'retained.json'
        refs_path.write_text(json.dumps(references)); old_path.write_text(json.dumps(ledger))
        generated = work / 'generated'
        command('python3', str(self.root / 'tools/build_public_repository.py'), '--source-repo', str(artifact / 'repository'),
                '--references', str(refs_path), '--existing', str(old_path), '--out', str(generated),
                '--base-url', BASE_URL, '--main-version', version)
        destination.mkdir(parents=True, exist_ok=True)
        for filename in ('index.json', 'index.html', 'publication-ledger.json'):
            shutil.copyfile(generated / filename, destination / filename)
        (destination / 'publication.json').write_text(json.dumps({'source_sha': sha, 'run_id': run_id, 'main_head': head,
                                                                 'catalog_parent': parent, 'references': references}, indent=2) + '\n')
        command('git', 'add', PREFIX, cwd=catalog)
        changed = command('git', 'status', '--porcelain', cwd=catalog)
        if changed:
            command('git', '-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                    'commit', '-m', f'Publish preview catalog for {version} from run {run_id}', cwd=catalog)
            if self.current_head() != head:
                raise ValueError('main advanced; catalog was not pushed')
            command('git', 'push', 'origin', 'HEAD:refs/heads/' + CATALOG, cwd=catalog)
        command('gh', 'workflow', 'run', 'docs.yml', '--repo', self.repo, '--ref', 'main')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True); parser.add_argument('--source-sha', required=True)
    parser.add_argument('--execute', action='store_true'); parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error('External publication requires explicit --execute')
    root = Path(__file__).resolve().parents[1]
    args.work.mkdir(parents=True, exist_ok=False)
    Publisher(root, os.environ.get('GITHUB_REPOSITORY', '')).execute(args.run_id, args.source_sha, args.work)


if __name__ == '__main__':
    main()
