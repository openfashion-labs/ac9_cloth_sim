"""The explicit research choice must not change Native preferences or old files."""
import ast
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import sys, types

ROOT = Path(__file__).resolve().parents[1]
_pkg=types.ModuleType('ac9_cloth_sim');_pkg.__path__=[str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault('ac9_cloth_sim',_pkg)
from ac9_cloth_sim.core import presets
from solver import fast_live_contract as Fast


import sys, types


class AccurateResearchTests(unittest.TestCase):
    def test_mixed_mode_overrides_rejected_before_build(self):
        for override in ({'native_three': True}, {'s1': {'native_three': True}}):
            settings = {'cfg_override': override}
            with self.assertRaisesRegex(ValueError, 'cannot run together'):
                Fast.preflight({}, settings)
        with self.assertRaisesRegex(ValueError, 'cannot run together'):
            Fast.configure({'live': True, 'native_three': True})
        cfg = Fast.configure({'live': True})
        cfg['native_three'] = True
        with self.assertRaisesRegex(ValueError, 'cannot run together'):
            Fast.validate_config(cfg)

    def test_waiting_worker_consumption_checks_choice(self):
        # Execute the production consumption function with a live fake worker and an exact matching key.
        tree = ast.parse((ROOT / 'dress.py').read_text(encoding='utf8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_standby_take')
        for research in (False, True):
            events = []
            proc = SimpleNamespace(pid=123, poll=lambda: None)
            slot = {'proc': proc, 'key': 'same'}
            def end():
                events.append('end'); slot.update(proc=None, key=None)
            scope = dict(__package__='ac9_cloth_sim', _STANDBY=slot, _PRELOAD_SESSION={}, _trace=lambda *a, **k: None,
                         _standby_key=lambda *a: 'same', _standby_drop_log=lambda: events.append('drop'),
                         standby_enabled=lambda: not research, standby_end=end)
            exec(compile(ast.Module(body=[fn], type_ignores=[]), 'standby_take', 'exec'), scope)
            actual = scope['_standby_take']('python', {}, 'cfg')
            self.assertIs(actual, None if research else proc)
            self.assertEqual(events, ['end'] if research else ['drop'])

    def test_native_settings_and_preference_restore_exactly(self):
        for native in (False, True):
            p = SimpleNamespace(native_three=native, fast_live=False, accurate_research=False)
            original = presets.resolve('', p, environ={})
            p.accurate_research = True
            research = presets.resolve('', p, environ={})
            self.assertTrue(research['fast_live'])
            self.assertNotIn('native_three', research)
            self.assertEqual(p.native_three, native)
            p.accurate_research = False
            self.assertEqual(presets.resolve('', p, environ={}), original)

    def test_legacy_fast_does_not_enable_research(self):
        for native in (False, True):
            old = SimpleNamespace(native_three=native, fast_live=True)
            off = SimpleNamespace(native_three=native, fast_live=False)
            self.assertEqual(presets.resolve('', old, environ={}), presets.resolve('', off, environ={}))

    def test_phase_and_local_stage_configs_unchanged(self):
        # Solver flags are consumed only by Dress's Live-only final configuration.
        spec = {'name': 'test', 'pieces': [{'name': 'Front', 'enabled': True}], 'seams': [], 'folds': []}
        for live in (False, True):
            def build(on):
                st = presets.resolve('', SimpleNamespace(native_three=True, accurate_research=on), environ={})
                return presets.build_stages(copy.deepcopy(spec), ['Front'], {}, 'pattern.npz', None, 10., st,
                                            continue_=True, live=live)
            self.assertEqual(build(False), build(True))

    def test_off_default_and_early_guard(self):
        tree = ast.parse((ROOT / 'properties.py').read_text(encoding='utf8'))
        prop = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Name) and n.target.id == 'accurate_research')
        self.assertFalse(next(ast.literal_eval(k.value) for k in prop.annotation.keywords if k.arg == 'default'))
        # Real RNA and save/load are checked separately in Blender.
        source = (ROOT / 'dress.py').read_text(encoding='utf8')
        self.assertIn("_fast_live = live and local is None", source)
        self.assertIn("not getattr(props, 'accurate_research', False)", source)


if __name__ == '__main__':
    unittest.main()
