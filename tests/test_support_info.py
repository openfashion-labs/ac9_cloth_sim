# SPDX-License-Identifier: GPL-3.0-or-later
"""Support reports never turn unknown metadata into arbitrary user-controlled text."""
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    from ac9_cloth_sim.core import support_info as S
except ModuleNotFoundError:
    # Worktrees may be named differently, with no package named ac9_cloth_sim beside them.
    import importlib.util
    spec = importlib.util.spec_from_file_location('support_contract', Path(__file__).resolve().parents[1] / 'core/support_info.py')
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)


class TestSupportInfo(unittest.TestCase):
    def test_exact_allowlist(self):
        data = json.loads(S.report('0.4.0', (5, 2, 1), 'win32'))
        self.assertEqual(data, {'schema': S.SCHEMA, 'addon_version': '0.4.0',
                                'blender_version': '5.2.1', 'os': 'Windows'})

    def test_reject_private_and_malformed_values(self):
        values = ('C:/DUMMY_PRIVATE_17/client.blend', 'DUMMY_SECRET_27',
                  '1.2.3\nDUMMY_SECRET_27', '1.2.3\n', '=DUMMY_FORMULA()',
                  '<img src="https://example.invalid/no-fetch">', '1' * 100000,
                  (1, True, 3), (1, -1, 3), (1, 2, 10000), (1, 2), [1, 2, 3],
                  None, True, {'version': 'DUMMY_SECRET_27'}, 1.23)
        for value in values:
            with self.subTest(type=type(value).__name__):
                text = S.report(value, value, value)
                self.assertEqual(json.loads(text)['addon_version'], S.UNKNOWN)
                self.assertEqual(json.loads(text)['blender_version'], S.UNKNOWN)
                self.assertEqual(json.loads(text)['os'], S.UNKNOWN)
                self.assertNotIn('DUMMY_', text)
                self.assertLessEqual(len(text.encode('utf-8')), S.MAX_BYTES)

    def test_does_not_coerce_unknown_objects(self):
        class Private:
            def __str__(self):
                raise AssertionError('must not stringify arbitrary data')
        self.assertNotIn('Private', S.report(Private(), Private(), Private()))

    def test_versions_and_platforms(self):
        for value in ('0.0.0', '9999.9999.9999', (0, 0, 0), (9999, 9999, 9999)):
            self.assertNotEqual(S.version(value), S.UNKNOWN)
        for platform, expected in (('win32', 'Windows'), ('linux', 'Linux'), ('darwin', 'macOS')):
            self.assertEqual(json.loads(S.report(None, None, platform))['os'], expected)

    def test_documented_command_matches_label(self):
        import ast
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'i18n_ja.py').read_text(encoding='utf-8'))
        ja = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'JA' for t in n.targets))
        for language in ('ja', 'en'):
            text = (root / 'docs' / language / 'troubleshooting.md').read_text(encoding='utf-8')
            self.assertIn(S.LABEL, text)
            self.assertIn('F3', text)
            if language == 'ja':
                self.assertIn(ja[S.LABEL], text)


if __name__ == '__main__':
    unittest.main()
