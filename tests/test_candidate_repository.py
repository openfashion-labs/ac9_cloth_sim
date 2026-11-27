# SPDX-License-Identifier: GPL-3.0-or-later
"""Candidate page tests use artificial ZIPs, never native runtime behavior."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
import zipfile

spec = importlib.util.spec_from_file_location('candidate_page', Path(__file__).resolve().parents[1] / 'tools/build_candidate_repository.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CandidateRepository(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.output = self.root / 'candidate'
        self.rows = []
        for pkg, version in [('ac9_cloth_sim', '0.4.1'), ('ac9_cloth_sim_runtime', '1.0.0')]:
            archive = self.source / (pkg + '-' + version + '.zip')
            manifest = f'id = "{pkg}"\nversion = "{version}"\ntype = "add-on"\nblender_version_min = "5.2.0"\nplatforms = ["windows-x64"]\n'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('blender_manifest.toml', manifest)
                z.writestr('runtime_contract.json', json.dumps({'runtime_id': 'ac9_cloth_sim_runtime', 'runtime_version': '1.0.0'}))
            self.rows.append({'id': pkg, 'version': version, 'type': 'add-on', 'blender_version_min': '5.2.0',
                              'platforms': ['windows-x64'], 'archive_url': './' + archive.name,
                              'archive_hash': 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest(),
                              'archive_size': archive.stat().st_size})
        self.save_index()

    def save_index(self):
        (self.source / 'index.json').write_text(json.dumps({'version': 'v1', 'data': self.rows}), encoding='utf8')

    def build(self, base='http://127.0.0.1:9999/'):
        return builder.build_candidate(self.source, self.output, base, '0.4.1')

    def test_absolute_repository_query_and_unchanged_archives(self):
        result = self.build()
        query = parse_qs(urlsplit(result['links']['ac9_cloth_sim']).query)
        self.assertEqual(query['repository'], ['http://127.0.0.1:9999/index.json'])
        self.assertEqual(query['platforms'], ['windows-x64'])
        self.assertEqual(query['blender_version_min'], ['5.2.0'])
        for item in result['packages'].values():
            self.assertEqual((self.source / item['filename']).read_bytes(), (self.output / item['filename']).read_bytes())
        page = (self.output / 'index.html').read_text(encoding='utf8')
        self.assertIn('もう一度ドラッグ', page)
        self.assertIn('計算部品を入れる', page)
        self.assertNotIn('download=', page)

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.build()

    def test_wrong_main_version_is_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, 'version'):
            builder.build_candidate(self.source, self.output, 'http://localhost:9999/', '0.4.2')
        self.assertFalse(self.output.exists())

    def test_tampered_index_hash_is_rejected(self):
        self.rows[0]['archive_hash'] = 'sha256:' + '0' * 64
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_wrong_manifest_field_is_rejected(self):
        self.rows[0]['platforms'] = ['linux-x64']
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.build()

    def test_remote_or_traversal_source_archive_is_rejected(self):
        for url in ('https://example.invalid/file.zip', '../file.zip', 'folder/file.zip'):
            with self.subTest(url=url):
                self.rows[0]['archive_url'] = url
                self.save_index()
                with self.assertRaises(ValueError):
                    self.build()

    def test_nonlocal_plain_http_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build('http://example.invalid/')


if __name__ == '__main__':
    unittest.main()
