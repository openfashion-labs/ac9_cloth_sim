# SPDX-License-Identifier: GPL-3.0-or-later
"""Public catalog creation from artificial package fixtures; no publication."""
import copy
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_public_repository as public
import test_candidate_repository as fixture


class PublicRepository(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.CandidateRepository()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.source = self.fixture.source;self.output = self.fixture.root / 'public'
        packages = {}
        for row in self.fixture.rows:
            role = 'main' if row['id'] == 'ac9_cloth_sim' else 'runtime'
            name = row['archive_url'].removeprefix('./')
            tag = 'v0.4.1' if role == 'main' else 'runtime-1.0.0'
            packages[role] = {'url': f'https://github.com/openfashion-labs/ac9_cloth_sim/releases/download/{tag}/{name}',
                              'sha256': row['archive_hash'].removeprefix('sha256:'), 'bytes': row['archive_size']}
        packages['full'] = {'url': 'https://github.com/openfashion-labs/ac9_cloth_sim/releases/download/v0.4.1/ac9_cloth_sim-0.4.1-full.zip',
                            'sha256': 'a' * 64, 'bytes': 200}
        self.refs = {'channel': 'preview', 'packages': packages}

    def build(self, existing=None):
        return public.build_public(self.source, self.refs, [] if existing is None else existing, self.output, public.EXPECTED_BASE, '0.4.1')

    def test_only_small_metadata_is_written_with_absolute_install_urls(self):
        result = self.build()
        self.assertEqual({p.name for p in self.output.iterdir()}, {'index.json', 'index.html', 'publication-ledger.json'})
        index = json.loads((self.output / 'index.json').read_text())
        self.assertEqual(len(index['data']), 2)
        for row in index['data']:self.assertTrue(row['archive_url'].startswith('https://github.com/'))
        query = parse_qs(urlsplit(result['links']['main']).query)
        self.assertEqual(query['repository'], [public.EXPECTED_BASE + 'index.json'])
        page = (self.output / 'index.html').read_text(encoding='utf-8')
        self.assertIn(self.refs['packages']['full']['url'], page)
        self.assertNotIn('127.0.0.1', page)
        self.assertNotIn('候補', page)

    def test_old_records_are_retained_and_same_runtime_is_deduplicated(self):
        old = {'url': 'https://github.com/openfashion-labs/ac9_cloth_sim/releases/download/v0.3.0/old.zip', 'sha256': 'c' * 64}
        runtime = self.refs['packages']['runtime']
        self.build([old, runtime])
        ledger = json.loads((self.output / 'publication-ledger.json').read_text())
        self.assertIn(old, ledger);self.assertEqual(sum(r['url'] == runtime['url'] for r in ledger), 1)

    def test_existing_url_with_different_content_is_rejected(self):
        with self.assertRaises(ValueError):self.build([{**self.refs['packages']['main'], 'sha256': 'f' * 64}])
        self.assertFalse(self.output.exists())

    def test_references_must_match_actual_package_bytes(self):
        self.refs['packages']['main']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):self.build()
        self.assertFalse(self.output.exists())

    def test_unknown_ledger_or_missing_role_is_not_a_first_release(self):
        with self.assertRaises(ValueError):self.build({})
        del self.refs['packages']['runtime']
        with self.assertRaises(ValueError):self.build()


if __name__ == '__main__':unittest.main()
