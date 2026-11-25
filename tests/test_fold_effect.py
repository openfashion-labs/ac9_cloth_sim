# SPDX-License-Identifier: GPL-3.0-or-later
"""The table of when each fold field takes effect (core/fold_effect.py) agrees with the screen and with what Live
sends the solver (no Blender).

Why (2026-11-25): the same answer was kept in four places -- the fields' enabled / live mark, ui.LIVE_FIELDS (the
"Takes effect now" line), recipe.live_params and a reference table -- and they had drifted: the end taper acted while
Live ran but was not named as live.

Run: python tests/test_fold_effect.py
"""
import ast
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_fold_effect_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
FE = __import__(PACKAGE + '.core.fold_effect', fromlist=['fold_effect'])
S = __import__(PACKAGE + '.core.spec', fromlist=['spec'])
recipe = __import__(PACKAGE + '.core.recipe', fromlist=['recipe'])

SOURCE = {n: (ROOT / n).read_text(encoding='utf-8') for n in ('draft_tool.py', 'ui.py')}


def _seam(sid, a, b):
    return {'id': sid, 'enabled': True, 'a': {'piece': a, 'from': ['e0', 0.0], 'to': ['e0', 1.0], 'dir': 1},
            'b': {'piece': b, 'from': ['e0', 0.0], 'to': ['e0', 1.0], 'dir': 1}}


class FoldEffect(unittest.TestCase):
    def test_every_field_is_a_registered_property(self):
        for f in FE.FIELDS:
            self.assertTrue(any(f"bpy.types.Scene.{f['prop']} =" in src for src in SOURCE.values()), f['prop'])

    def test_rules(self):
        for f in FE.FIELDS:
            self.assertIn(f['live'], ('folded', 'no'), f['key'])
            self.assertIn(f['kind'], ('line', 'seam'), f['key'])
            if f['live'] != 'no':
                self.assertTrue(f['param'], f['key'] + ': a field live in Live must be sent to the solver')
        # nothing acts while no run goes; a field that cannot take a run's change is grayed while it runs
        for f in FE.FIELDS:
            self.assertTrue(FE.enabled(f['prop'], False, False))
            self.assertFalse(FE.live_now(f['prop'], False, True))
            self.assertEqual(FE.enabled(f['prop'], True, True), f['live'] != 'no')
            self.assertFalse(FE.enabled(f['prop'], True, False))

    def test_live_params_carry_the_live_fields(self):
        spec = S.new_spec('t')
        spec['pieces'] = [{'name': n} for n in ('A', 'B')]
        spec['seams'] = [_seam('s1', 'A', 'B')]
        spec['folds'] = [{'seam': 's1', 'deg': 90.0, 'turn': 'B'}]
        p = recipe.live_params(spec, ['A', 'B'], True)
        params = {f['param'] for f in FE.FIELDS if f['param']}
        for k in params:
            self.assertIn(k, p, k)
        # every fold key Live sends is in the table (a new one needs its row: when it acts, and its live mark)
        fold_keys = {k for k in p if 'fold' in k or k == 'seam_turn'}
        self.assertEqual(fold_keys - params, set())

    def test_takes_effect_line_reads_the_table(self):
        tree = ast.parse(SOURCE['ui.py'])
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, 'id', '') == 'LIVE_FIELDS' for t in n.targets))
        written = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        self.assertEqual(set(written) & {f['name'] for f in FE.FIELDS}, set())       # not written twice
        self.assertTrue(any(isinstance(e, ast.Starred) and 'live_names' in ast.unparse(e) for e in node.value.elts))
        self.assertIn('Taper', FE.live_names())

    def test_the_fields_are_gated_by_the_table(self):
        # the internal line's live fields and grayed fields ask the table, not their own condition
        for f in FE.FIELDS:
            if f['kind'] == 'line':
                self.assertIn(f'FE.enabled("{f["prop"]}"', SOURCE['draft_tool.py'], f['prop'])
        self.assertIn('_FE.live_now("ac9_seam_fold_angle"', SOURCE['ui.py'])


if __name__ == '__main__':
    unittest.main()
