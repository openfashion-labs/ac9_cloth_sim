# SPDX-License-Identifier: GPL-3.0-or-later
"""Main release metadata must not silently rebuild the unchanged runtime."""
import hashlib
import io
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('ac9_split_release_test', TOOLS / 'split_distribution.py')
splitter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(splitter)


class RuntimeReleaseInputs(unittest.TestCase):
    def test_main_notices_preserve_credits_without_claiming_bundled_wheels(self):
        canonical = (TOOLS.parent / 'THIRD_PARTY_NOTICES.txt').read_bytes()
        main = splitter.main_notices(canonical)
        self.assertNotIn(b'It bundles these Python packages as wheels', main)
        self.assertIn(b'this main ZIP does not contain wheels/', main)
        self.assertEqual(main.split(b'\n\n', 1)[1], canonical.split(b'\n\n', 1)[1])

    def test_missing_or_changed_notices_are_rejected(self):
        for canonical in (b'', b'Unknown notice format\n'):
            with self.subTest(canonical=canonical), self.assertRaises(ValueError):
                splitter.main_notices(canonical)

    def make_full(self, root, revision):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(zipfile.ZipInfo('fixture.py', date_time=(2026, 1, 1, 0, 0, 0)), '# synthetic metadata fixture\n')
        wheel = buffer.getvalue()
        manifest = ('schema_version = "1.0.0"\nid = "ac9_cloth_sim"\n'
                    f'version = "0.2.{revision}"\nmaintainer = "Main owner {revision}"\n'
                    f'blender_version_min = "5.{2 + revision}.0"\n'
                    'wheels = ["./wheels/fixture.whl"]\n')
        path = root / f'full_{revision}.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('blender_manifest.toml', manifest)
            archive.writestr('LICENSE', f'Main license revision {revision}')
            archive.writestr('THIRD_PARTY_NOTICES.txt', (TOOLS.parent / 'THIRD_PARTY_NOTICES.txt').read_bytes())
            archive.writestr('__init__.py', 'def register(): pass\ndef unregister(): pass\n')
            archive.writestr('wheels/fixture.whl', wheel)
        return path, {'fixture.whl': hashlib.sha256(wheel).hexdigest()}

    def test_main_metadata_and_license_changes_preserve_runtime_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, pins = self.make_full(root, 0)
            second, _ = self.make_full(root, 1)
            with patch.object(splitter, 'PINS', pins), patch.object(splitter, 'verify_newton'):
                a = splitter.split(first, root / 'a')
                b = splitter.split(second, root / 'b')
            self.assertEqual(a['packages']['runtime']['sha256'], b['packages']['runtime']['sha256'])
            self.assertNotEqual(a['packages']['main']['sha256'], b['packages']['main']['sha256'])
            self.assertNotEqual(a['packages']['full']['sha256'], b['packages']['full']['sha256'])
            with zipfile.ZipFile(a['packages']['runtime']['path']) as archive:
                self.assertEqual(archive.read('LICENSE'), (TOOLS / 'runtime_LICENSE.txt').read_bytes())
                manifest = archive.read('blender_manifest.toml').decode()
                self.assertIn('blender_version_min = "5.2.0"', manifest)
                self.assertNotIn('Main owner', manifest)

    def test_missing_main_license_fails_before_writing_output(self):
        self.assert_invalid_license(None)

    def test_empty_main_license_fails_before_writing_output(self):
        self.assert_invalid_license(b'  \n')

    def assert_invalid_license(self, license_bytes):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full, pins = self.make_full(root, 0)
            invalid = root / 'invalid.zip'
            with zipfile.ZipFile(full) as archive, zipfile.ZipFile(invalid, 'w') as target:
                for name in archive.namelist():
                    if name != 'LICENSE':
                        target.writestr(name, archive.read(name))
                if license_bytes is not None:
                    target.writestr('LICENSE', license_bytes)
            with patch.object(splitter, 'PINS', pins), patch.object(splitter, 'verify_newton'):
                with self.assertRaisesRegex(ValueError, 'main LICENSE'):
                    splitter.split(invalid, root / 'output')
            self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
