import contextlib
import ast
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

PACKAGE = Path(__file__).resolve().parents[1].name
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
B = importlib.import_module(PACKAGE + '.core.native_three_io')
M = importlib.import_module(PACKAGE + '.solver.native_runtime_io')


class Process:
    code = None

    def poll(self):
        return self.code


class NativeThreeIOTests(unittest.TestCase):
    def test_real_stage_entry_preserves_non_live_modes_and_allocates_only_live(self):
        presets = importlib.import_module(PACKAGE + '.core.presets')
        settings = presets.resolve('default', SimpleNamespace(native_three=True, fast_live=False), environ={})
        source = ast.parse((Path(__file__).resolve().parents[1] / 'dress.py').read_text(encoding='utf8'))
        operator = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'AC9_OT_SimDress')
        method = next(n for n in operator.body if isinstance(n, ast.FunctionDef) and n.name == '_next_stage')
        branch = next(n for n in method.body if isinstance(n, ast.If) and 'native_three' in ast.unparse(n.test))
        code = compile(ast.Module(body=[branch], type_ignores=[]), '<real stage entry>', 'exec')
        for mode, stages, selected, accurate, expected in [
            ('START', 2, True, False, 0), ('START', 1, True, False, 0),
            ('CONTINUE', 2, True, False, 0), ('LOCAL', 1, True, False, 0),
            ('LIVE', 1, True, False, 1), ('LIVE', 1, False, False, 0),
            ('LIVE', 1, False, True, 0),
        ]:
            effective = dict(settings, native_three=selected, fast_live=accurate)
            instance = SimpleNamespace(info={'settings': effective}, stage_dirs=['s'] * stages, mode=mode, _native_io=None)
            with patch.object(B, 'FrontendRuntime') as owner:
                exec(code, {'__package__': PACKAGE, 'self': instance, 'cfg1': 'MUST_NOT_BE_READ'})
                self.assertEqual(owner.call_count, expected, (mode, stages, selected, accurate))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native3_io_')
        self.root = Path(self.temporary.name).resolve()
        self.base_patch = patch.object(M, '_base', lambda: self.root / 'cache')
        self.base_patch.start()
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.cfg_path = self.stage / 'config.json'
        self.original = dict(native_three=True, live=True, density=.14, substeps=10,
                             iterations=20, grab_file=str(self.root / 'grab.json'),
                             params_file=str(self.root / 'params.json'))
        self.cfg_path.write_text(json.dumps(self.original), encoding='utf8')

    def tearDown(self):
        self.base_patch.stop()
        # The resolved synthetic root is the explicitly named disposable target.
        assert self.root.name.startswith('native3_io_') and self.root.is_absolute()
        self.temporary.cleanup()

    def test_aborted_before_launch_reclaims_only_its_cache(self):
        bridge = B.FrontendRuntime(self.cfg_path)
        effective = json.loads(self.cfg_path.read_text())
        for key in ('density', 'substeps', 'iterations', 'native_three', 'live'):
            self.assertEqual(effective[key], self.original[key])
        self.assertTrue(bridge.owner.directory.exists())
        with self.assertRaises(ValueError):
            bridge.path('../foreign')
        self.assertTrue(bridge.close())
        self.assertFalse(bridge.owner.directory.exists())
        self.assertTrue(bridge.close())

    def test_live_reader_paths_and_cleanup_wait_for_process_exit(self):
        import numpy as np
        bridge = B.FrontendRuntime(self.cfg_path)
        effective = json.loads(self.cfg_path.read_text())
        self.assertEqual(effective['params_file'], bridge.path('params.json'))
        process = Process()
        bridge.launched(process)
        with contextlib.redirect_stdout(io.StringIO()):
            worker = M.WorkerRuntime(effective, self.stage)
            worker.start()
            worker.set_expected_points(2)
            np.save(bridge.path('live.npy'), np.ones((2, 3), np.float32))
            Path(bridge.path('progress.txt')).write_text('8 0')
            self.assertIsNone(bridge.close())
            self.assertTrue(bridge.owner.directory.exists())
            worker.success = True
            worker.close()
        self.assertTrue((self.stage / 'live.npy').exists())
        self.assertIsNone(bridge.close())
        process.code = 0
        self.assertTrue(bridge.close())
        self.assertFalse(bridge.owner.directory.exists())
        self.assertEqual(json.loads((self.stage / M.DESCRIPTOR).read_text())['phase'], 'retired')

    def test_config_replacement_failure_preserves_original_and_aborts_owner(self):
        before = self.cfg_path.read_bytes()
        replace = B.os.replace

        def fail_config(source, target):
            if Path(target) == self.cfg_path:
                raise OSError('injected config publication failure')
            return replace(source, target)

        with patch.object(B.os, 'replace', fail_config), self.assertRaises(OSError):
            B.FrontendRuntime(self.cfg_path)
        self.assertEqual(self.cfg_path.read_bytes(), before)
        self.assertEqual(list((self.root / 'cache').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
