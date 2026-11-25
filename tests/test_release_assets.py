# SPDX-License-Identifier: GPL-3.0-or-later
"""Publication integrity failures must not produce a promotion plan."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_gate', Path(__file__).resolve().parents[1] / 'tools/verify_release_assets.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ReleaseAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = {'packages': {}}
        self.assets = []
        for label in sorted(gate.LABELS):
            filename = 'ac9-' + label + '-0.4.0.zip'
            tested, downloaded = self.root / filename, self.root / ('downloaded-' + filename)
            data = ('owned artificial artifact ' + label).encode()
            tested.write_bytes(data)
            downloaded.write_bytes(data)
            self.build['packages'][label] = {'path': str(tested), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            self.assets.append({'label': label, 'download_path': str(downloaded), 'url': 'https://example.invalid/ac9/releases/download/v0.4.0/' + filename, 'release_published': True, 'release_draft': False, 'release_prerelease': True})

    def test_published_prerelease_and_retained_runtime_are_accepted(self):
        runtime = next(a for a in self.assets if a['label'] == 'runtime')
        retained = [{'url': runtime['url'], 'sha256': self.build['packages']['runtime']['sha256']}]
        result = gate.verify_release_assets(self.build, self.assets, retained)
        self.assertTrue(result['packages']['runtime']['reuse_existing'])
        self.assertFalse(result['packages']['main']['reuse_existing'])
        self.assertEqual(result['channel'], 'preview')

    def test_draft_or_unpublished_releases_are_rejected(self):
        for changes in ({'release_draft': True}, {'release_published': False}, {'release_draft': None}):
            assets = copy.deepcopy(self.assets)
            assets[0].update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'unpublished'):
                gate.verify_release_assets(self.build, assets)

    def test_changed_download_bytes_are_rejected_even_at_same_size(self):
        path = Path(self.assets[0]['download_path'])
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        with self.assertRaisesRegex(ValueError, 'differ'):
            gate.verify_release_assets(self.build, self.assets)

    def test_changed_tested_artifact_is_rejected(self):
        Path(self.build['packages']['main']['path']).write_bytes(b'rebuilt after testing')
        with self.assertRaisesRegex(ValueError, 'differ'):
            gate.verify_release_assets(self.build, self.assets)

    def test_same_url_different_sha_is_rejected(self):
        retained = [{'url': self.assets[0]['url'], 'sha256': '0' * 64}]
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            gate.verify_release_assets(self.build, self.assets, retained)

    def test_missing_or_duplicate_package_is_rejected(self):
        for assets in (self.assets[:2], [self.assets[0]] * 3):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                gate.verify_release_assets(self.build, assets)

    def test_moving_or_non_asset_urls_are_rejected(self):
        for url in ('http://example.invalid/releases/download/v0.4.0/ac9-full-0.4.0.zip',
                    'https://example.invalid/releases/latest/download/ac9-full-0.4.0.zip',
                    'https://example.invalid/download/ac9-full-0.4.0.zip',
                    'https://example.invalid/releases/download/v0.4.0/wrong.zip'):
            assets = copy.deepcopy(self.assets)
            assets[0]['url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                gate.verify_release_assets(self.build, assets)

    def test_cli_requires_retained_ledger_even_for_first_publication(self):
        process = subprocess.run([sys.executable, str(Path(gate.__file__)), '--build', 'unused', '--assets', 'unused'],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn('--existing', process.stderr)

    def test_cli_writes_plan_only_after_validating_all_assets(self):
        paths = [self.root / name for name in ('build.json', 'assets.json', 'retained.json')]
        for path, data in zip(paths, (self.build, self.assets, [])):
            path.write_text(json.dumps(data), encoding='utf8')
        command = [sys.executable, str(Path(gate.__file__)), '--build', str(paths[0]), '--assets', str(paths[1]), '--existing', str(paths[2])]
        accepted = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(set(json.loads(accepted.stdout)['packages']), gate.LABELS)
        self.assets[-1]['release_draft'] = True
        paths[1].write_text(json.dumps(self.assets), encoding='utf8')
        refused = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, '')

    def test_cli_rejects_malformed_retained_ledgers(self):
        paths = [self.root / name for name in ('build.json', 'assets.json', 'retained.json')]
        paths[0].write_text(json.dumps(self.build), encoding='utf8')
        paths[1].write_text(json.dumps(self.assets), encoding='utf8')
        command = [sys.executable, str(Path(gate.__file__)), '--build', str(paths[0]), '--assets', str(paths[1]), '--existing', str(paths[2])]
        invalid = [{}, '', None, 0, [{}], [None], [1],
                   [{'url': 'http://example.invalid/releases/download/v0.4.0/file.zip', 'sha256': '0' * 64}],
                   [{'url': self.assets[0]['url'], 'sha256': 'invalid'}]]
        for ledger in invalid:
            with self.subTest(ledger=ledger):
                paths[2].write_text(json.dumps(ledger), encoding='utf8')
                process = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(process.stdout, '')


if __name__ == '__main__':
    unittest.main()
