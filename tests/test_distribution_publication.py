# SPDX-License-Identifier: GPL-3.0-or-later
"""Publication failure cases; no external mutations or network access."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import publish_distribution as publication
import catalog_site as site

REPO = 'openfashion-labs/ac9_cloth_sim'
SHA = 'a' * 40


class Publication(unittest.TestCase):
    def test_unapproved_assets_never_call_github(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(publication, 'validate_plan', return_value={'publication': {}}), patch.object(publication, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'not been approved'):
                publication.Publisher(temporary, REPO).execute('123', SHA, Path(temporary))
            command.assert_not_called()

    def test_only_post_publication_install_check_may_remain(self):
        for remaining in (['runtime_distribution_terms_review'], ['public_repository_connection']):
            with self.subTest(remaining=remaining), self.assertRaises(ValueError):
                publication.require_asset_authorization({'publication': {'asset_publication_approved': True, 'remaining': remaining}})
        publication.require_asset_authorization({'publication': {'asset_publication_approved': True, 'remaining': ['production_install_update_check']}})

    def test_run_must_be_successful_internal_release_ci(self):
        run = {'head_sha': SHA, 'event': 'pull_request', 'status': 'completed', 'conclusion': 'success',
               'path': '.github/workflows/release.yml', 'head_repository': {'full_name': REPO}}
        publication.validate_run(run, SHA, REPO)
        for key, value in [('conclusion', 'failure'), ('head_sha', 'b' * 40), ('event', 'push'),
                           ('path', '.github/workflows/docs.yml'), ('head_repository', {'full_name': 'other/fork'})]:
            bad = {**run, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):publication.validate_run(bad, SHA, REPO)

    def test_approval_of_an_old_head_does_not_pass(self):
        pulls = [{'merged_at': '2026-11-25', 'head': {'sha': SHA, 'repo': {'full_name': REPO}}, 'base': {'ref': 'main'}}]
        with self.assertRaises(ValueError):
            publication.require_merged_approval(pulls, [{'state': 'APPROVED', 'commit_id': 'b' * 40}], SHA, REPO)
        publication.require_merged_approval(pulls, [{'state': 'APPROVED', 'commit_id': SHA}], SHA, REPO)
        pulls[0]['merged_at'] = None
        with self.assertRaises(ValueError):publication.require_merged_approval(pulls, [{'state': 'APPROVED', 'commit_id': SHA}], SHA, REPO)

    def test_existing_asset_is_reused_but_never_overwritten(self):
        expected = {'bytes': 7, 'sha256': SHA + 'b' * 24}
        self.assertEqual(publication.asset_action(None, expected), 'upload')
        self.assertEqual(publication.asset_action({'size': 7, 'digest': 'sha256:' + expected['sha256']}, expected), 'verify-and-reuse')
        for existing in ({'size': 8}, {'size': 7, 'digest': 'sha256:' + 'c' * 64}):
            with self.assertRaises(ValueError):publication.asset_action(existing, expected)

    def test_partial_upload_failure_never_publishes_a_release(self):
        publisher = publication.Publisher('.', REPO)
        publisher.ensure_asset = lambda *args: (_ for _ in ()).throw(RuntimeError('simulated upload failure'))
        with patch.object(publication, 'command') as command, self.assertRaises(RuntimeError):
            publisher.publish_assets({'runtime_version': '1.0.0', 'packages': {'full': {}}}, '0.4.1', SHA, Path('.'), [])
        command.assert_not_called()

    def test_retained_url_rejects_different_bytes_before_upload(self):
        build = {'runtime_version': '1.0.0', 'packages': {'main': {'path': 'ac9_cloth_sim-0.4.1.zip', 'sha256': 'b' * 64}}}
        urls = publication.planned_asset_urls(build, '0.4.1', REPO, [])
        with self.assertRaises(ValueError):
            publication.planned_asset_urls(build, '0.4.1', REPO, [{'url': urls['main'], 'sha256': 'c' * 64}])
        for invalid in (None, {}, [{'url': urls['main'], 'sha256': 'invalid'}]):
            with self.assertRaises(ValueError):publication.planned_asset_urls(build, '0.4.1', REPO, invalid)

    def test_catalog_versions_cannot_go_backwards(self):
        old = {'data': [{'id': 'main', 'version': '0.4.2'}]}
        with self.assertRaises(ValueError):publication.reject_downgrade(old, {'data': [{'id': 'main', 'version': '0.4.1'}]})
        publication.reject_downgrade(old, {'data': [{'id': 'main', 'version': '0.4.3'}]})

    def test_either_head_advance_rejects_an_old_pages_artifact(self):
        site.assert_heads('a', 'b', 'a', 'b')
        for current in [('new', 'b'), ('a', 'new'), ('a', '')]:
            with self.subTest(current=current), self.assertRaises(ValueError):site.assert_heads('a', 'b', *current)

    def test_public_index_checks_actual_anonymous_bytes(self):
        refs, rows, ledger = {}, [], []
        for role in ('full', 'main', 'runtime'):
            url = f'https://github.com/{REPO}/releases/download/v0.4.1/{role}.zip'
            record = {'url': url, 'bytes': 8, 'sha256': 'a' * 64}
            refs[role] = record; ledger.append(record)
            if role != 'full':rows.append({'id': 'ac9_cloth_sim' + ('_runtime' if role == 'runtime' else ''),
                'archive_url': url, 'archive_size': 8, 'archive_hash': 'sha256:' + 'a' * 64})
        index = {'version': 'v1', 'data': rows}; published = {'references': {'packages': refs}}
        site.verify_index(index, ledger, published, lambda url: (8, 'a' * 64))
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            site.verify_index(index, ledger, published, lambda url: (8, 'b' * 64))
        tampered = copy.deepcopy(index);tampered['data'][0]['archive_size'] = 9
        with self.assertRaises(ValueError):site.verify_index(tampered, ledger, published, lambda url: (8, 'a' * 64))

    def test_all_uploaded_bytes_are_checked_before_publishing_and_then_checked_anonymously(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);packages = {};payloads = {}
            for role in ('full', 'main', 'runtime'):
                data = ('tested-' + role).encode();path = root / (role + '.zip');path.write_bytes(data)
                packages[role] = {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                payloads[role + '.zip'] = data
            publisher = publication.Publisher(root, REPO);checked = [];published = set()
            def ensure(tag, entry, head):
                checked.append(entry['path']);name = Path(entry['path']).name
                return {'tag': tag, 'name': name, 'url': f'https://github.com/{REPO}/releases/download/{tag}/{name}'}
            publisher.ensure_asset = ensure
            publisher.release = lambda tag: {'draft': tag not in published, 'published_at': '2026-11-25' if tag in published else None}
            def cmd(*args, **kwargs):
                self.assertEqual(len(checked), 3)
                self.assertEqual(args[:3], ('gh', 'release', 'edit'))
                published.add(args[3]);return ''
            def download(url, **kwargs):return io.BytesIO(payloads[url.rsplit('/', 1)[1]])
            build = {'runtime_version': '1.0.0', 'packages': packages}
            with patch.object(publication, 'command', side_effect=cmd), patch.object(publication.urllib.request, 'urlopen', side_effect=download):
                result = publisher.publish_assets(build, '0.4.1', SHA, root, [])
            self.assertEqual(set(result['packages']), {'full', 'main', 'runtime'})
            self.assertEqual(len(published), 2)
            with patch.object(publication, 'command', side_effect=cmd), patch.object(publication.urllib.request, 'urlopen', return_value=io.BytesIO(b'corrupted')):
                with self.assertRaises(ValueError):publisher.publish_assets(build, '0.4.1', SHA, root, [])

    def test_overlay_preserves_the_whole_site_and_other_channels(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary);origin = root / 'origin.git';work = root / 'work'
            def git(*args, cwd=None):
                result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, encoding='utf-8')
                if result.returncode:raise RuntimeError(result.stderr)
                return result.stdout.strip()
            git('init', '--bare', str(origin));git('clone', str(origin), str(work))
            git('checkout', '-b', 'main', cwd=work)
            (work / 'website/static').mkdir(parents=True)
            (work / 'website/static/manual.txt').write_text('existing manual')
            git('add', '.', cwd=work)
            def commit(message):git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', message, cwd=work)
            commit('initial');git('push', '-u', 'origin', 'main', cwd=work)
            git('checkout', '-b', publication.CATALOG, cwd=work)
            directory = work / publication.PREFIX;directory.mkdir(parents=True)
            for name in ('index.json', 'index.html', 'publication-ledger.json', 'publication.json'):(directory / name).write_text('{}\n')
            other = work / 'website/static/extensions/other';other.mkdir();(other / 'index.json').write_text('{"retained":true}\n')
            git('add', '.', cwd=work);commit('catalog');git('push', 'origin', publication.CATALOG, cwd=work)
            git('checkout', 'main', cwd=work)
            result = site.overlay(work)
            self.assertEqual((work / 'website/static/manual.txt').read_text(), 'existing manual')
            self.assertEqual((other / 'index.json').read_text(), '{"retained":true}\n')
            self.assertTrue((directory / 'index.html').exists())
            site.assert_heads(result['source_head'], result['catalog_sha'], site.remote_head(work, 'main'), site.remote_head(work, publication.CATALOG))


if __name__ == '__main__':unittest.main()
