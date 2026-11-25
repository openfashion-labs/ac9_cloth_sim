"""Build ahead (build_ahead.py): how a press shares the waiting solver with a build, and that the switch and the
hooks are in place. Runs without Blender: bpy and dress are stand-ins."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load():
    pkg = types.ModuleType('ba_pkg'); pkg.__path__ = []
    bpy = types.ModuleType('bpy')
    bpy.app = types.SimpleNamespace(background=False, timers=types.SimpleNamespace(
        is_registered=lambda f: False, register=lambda *a, **k: None, unregister=lambda f: None))
    dress = types.ModuleType('ba_pkg.dress'); dress.ended = []
    dress._session_end = lambda: dress.ended.append(1)
    sys.modules.update({'bpy': bpy, 'ba_pkg': pkg, 'ba_pkg.dress': dress})
    spec = importlib.util.spec_from_file_location('ba_pkg.build_ahead', ROOT / 'build_ahead.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod, dress


class Job:
    def __init__(self, stage, receipt=None, code=None):
        self.command = dict(args=['run', 'idx', 'avatar', str(stage)], generation=1)
        self.receipt = receipt; self.returncode = code; self.released = 0
    def poll(self):
        return self.returncode
    def release(self):
        self.released += 1


class BuildAheadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.stage = Path(self.tmp.name)
        self.BA, self.DR = load()
        self.BA._key = lambda idx, avatar, cfg: cfg.get('k')
        self.press_cfg = str(self.stage / 'press.json')

    def tearDown(self):
        self.tmp.cleanup()
        for k in ('bpy', 'ba_pkg', 'ba_pkg.dress'):
            sys.modules.pop(k, None)

    def press(self, key):
        Path(self.press_cfg).write_text(json.dumps(dict(k=key)))
        self.BA._A['press_key'] = None
        return self.BA.yield_to('idx', 'avatar', self.press_cfg)

    def running(self, key='A'):
        job = Job(self.stage)
        self.BA._A.update(job=job, key=key, stop_t=None, t_sent=time.monotonic())
        return job

    def test_same_structure_waits_without_stopping(self):
        self.running('A')
        self.assertFalse(self.press('A'))
        self.assertFalse((self.stage / 'stop').exists())

    def test_other_structure_stops_the_build(self):
        self.running('A')
        self.assertFalse(self.press('B'))
        self.assertTrue((self.stage / 'stop').exists())
        self.assertEqual(self.DR.ended, [])

    def test_stop_not_landing_ends_the_session(self):
        job = self.running('A')
        self.assertFalse(self.press('B'))
        self.BA._A['stop_t'] -= self.BA.STOP_GIVE_UP_S + 0.1
        self.assertTrue(self.BA.yield_to('idx', 'avatar', self.press_cfg))
        self.assertEqual(self.DR.ended, [1])
        self.assertIsNone(self.BA._A['job'])
        self.assertEqual(job.released, 0)               # the session that held the lease is gone with it

    def test_finished_build_frees_the_lease(self):
        receipt = self.stage / 'receipt.json'
        receipt.write_text(json.dumps(dict(audit=dict(resident=dict(mode='build', setup_s=2.0)))))
        job = self.running('A'); job.receipt = str(receipt); job.returncode = 0
        self.assertTrue(self.press('B'))
        self.assertEqual(job.released, 1)
        self.assertIsNone(self.BA._A['failed'])
        self.assertEqual(self.BA._A['last']['mode'], 'build')

    def test_failed_key_is_remembered_but_not_a_cancelled_one(self):
        receipt = self.stage / 'receipt.json'
        receipt.write_text(json.dumps(dict(audit=dict(cancelled=True))))
        job = self.running('A'); job.receipt = str(receipt); job.returncode = 0
        self.BA._finish()
        self.assertIsNone(self.BA._A['failed'])
        receipt.write_text(json.dumps(dict(audit=dict())))
        job = self.running('A'); job.receipt = str(receipt); job.returncode = 1
        self.BA._finish()
        self.assertEqual(self.BA._A['failed'], 'A')

    def test_no_build_means_the_press_goes_on(self):
        self.BA._A.update(job=None)
        self.assertTrue(self.press('A'))

    def test_same_structure_wait_has_a_limit(self):
        job = self.running('A')
        self.assertFalse(self.press('A'))
        self.BA._A['wait_t'] -= 2 * self.BA.FRESH_GUESS_S + 0.1
        self.assertTrue(self.BA.yield_to('idx', 'avatar', self.press_cfg))
        self.assertEqual(self.DR.ended, [1])
        self.assertIsNone(self.BA._A['job'])

    def test_wait_limit_follows_the_measured_build(self):
        self.BA._A['last'] = dict(mode='build', wall_s=1.0)
        self.running('A')
        self.assertFalse(self.press('A'))
        self.BA._A['wait_t'] -= 1.5
        self.assertFalse(self.BA.yield_to('idx', 'avatar', self.press_cfg))
        self.BA._A['wait_t'] -= 0.6
        self.assertTrue(self.BA.yield_to('idx', 'avatar', self.press_cfg))

    def test_modal_operator_holds_it_back(self):
        win = types.SimpleNamespace(modal_operators=[])
        sys.modules['bpy'].context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[win]))
        self.assertFalse(self.BA._modal_running())
        win.modal_operators = ['TRANSFORM_OT_translate']
        self.assertTrue(self.BA._modal_running())

    def test_press_drops_the_waiting_build(self):
        bpy = sys.modules['bpy']; gone = []
        bpy.app.timers.is_registered = lambda f: f is self.BA._tick
        bpy.app.timers.unregister = gone.append
        traced = []; self.BA._trace = lambda event, **f: traced.append((event, f.get('why')))
        self.BA._A['due'] = time.monotonic() + 1.0
        self.BA.pressed()
        self.assertEqual(self.BA._A['due'], 0.0)
        self.assertEqual(gone, [self.BA._tick])
        self.assertEqual(traced, [('build_ahead_skip', 'pressed')])

    def test_skip_is_traced_once_per_reason(self):
        traced = []; self.BA._trace = lambda event, **f: traced.append(f.get('why'))
        self.BA._A['skip'] = None
        for why in ('drag', 'drag', 'modal', 'drag'):
            self.BA._skip(why)
        self.assertEqual(traced, ['drag', 'modal', 'drag'])

    def test_switch_overrides(self):
        # the preference is on by default; the environment switch overrides it either way
        os.environ['AC9_BUILD_AHEAD'] = '0'
        self.BA._A['due'] = 0.0
        self.BA.later(None)
        self.assertEqual(self.BA._A['due'], 0.0)
        os.environ['AC9_BUILD_AHEAD'] = '1'
        try:
            self.BA.later(None)
            self.assertGreater(self.BA._A['due'], 0.0)
        finally:
            os.environ.pop('AC9_BUILD_AHEAD')


class HookTests(unittest.TestCase):
    """The places that call build ahead (they need Blender to run, so their text is checked)."""

    def test_hooks(self):
        dress = (ROOT / 'dress.py').read_text(encoding='utf-8')
        draft = (ROOT / 'draft_tool.py').read_text(encoding='utf-8')
        ba = (ROOT / 'build_ahead.py').read_text(encoding='utf-8')
        self.assertIn('build_ahead.later(context)', draft)              # after a pattern / particle-distance edit
        self.assertIn('build_ahead.yield_to(', dress)                    # a press shares the waiting solver
        self.assertIn('build_ahead._QUIET', dress)                       # nothing is logged into the scene
        self.assertIn('build_ahead.end()', dress)                        # another file stops it
        self.assertIn('DR.fast_start(bpy.context)', ba)                  # part of Fast Simulation Start
        self.assertIn('build_ahead.pressed()', dress)                    # a press drops the build not sent yet
        self.assertIn("op='build'", ba)
        self.assertIn('for_build=True', ba)                              # a build's input: no resume gate, no record
        self.assertIn('not (for_build and not _fast_live)', dress)
        self.assertIn('if not for_build:', dress)
        self.assertIn('_PIECE_TRIS.get(', dress)                         # an unchanged piece is not triangulated again
        for word in ('.finish(', 'commit'):                              # the build never becomes a result
            self.assertNotIn(word, ba)


if __name__ == '__main__':
    unittest.main()
