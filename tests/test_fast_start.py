"""Fast Simulation Start: one switch for the waiting solver, the kept Live model and the Live built ahead.
The preferences need Blender to run, so dress.py's source is checked."""
import ast
import importlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
JA = importlib.import_module(ROOT.name + '.i18n_ja')


def _dress():
    return ast.parse((ROOT / 'dress.py').read_text(encoding='utf-8'))


class FastStartTests(unittest.TestCase):
    def test_the_preferences_hold_one_switch(self):
        prefs = next(n for n in _dress().body if isinstance(n, ast.ClassDef) and n.name == 'AC9ClothSimPreferences')
        fields = {n.target.id for n in prefs.body if isinstance(n, ast.AnnAssign)}
        self.assertIn('fast_start', fields)
        for old in ('keep_solver_ready', 'keep_live_model', 'build_ahead'):
            self.assertNotIn(old, fields)

    def test_every_reader_goes_through_fast_start(self):
        text = '\n'.join((ROOT / f).read_text(encoding='utf-8') for f in ('dress.py', 'build_ahead.py'))
        for old in ("'keep_solver_ready'", "'keep_live_model'", "'build_ahead', True"):
            self.assertNotIn(old, text)
        funcs = {n.name: ast.unparse(n) for n in _dress().body if isinstance(n, ast.FunctionDef)}
        self.assertIn('fast_start(context)', funcs['standby_enabled'])
        self.assertIn('fast_start(context)', funcs['resident_live'])

    def test_the_name_has_one_source_and_a_japanese_text(self):
        tree = _dress()
        name = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], 'id', '') == 'FAST_START_NAME')
        self.assertEqual(name, 'Fast Simulation Start')
        text = (ROOT / 'dress.py').read_text(encoding='utf-8')
        self.assertEqual(text.count('"' + name + '"'), 1)       # written once, read by the preference and messages
        self.assertIn('name=FAST_START_NAME', text)
        self.assertIn('tx(FAST_START_NAME)', text)              # the out-of-memory message names it
        ja = ast.unparse(ast.parse((ROOT / 'i18n_ja.py').read_text(encoding='utf-8')))
        self.assertIn("'Fast Simulation Start': 'シミュレーション高速起動'", ja)


if __name__ == '__main__':
    unittest.main()
