"""Actual Blender host I/O at long Windows paths (not just its standalone Python).

blender --factory-startup --python test_native_runtime_long_paths.py -- <parent> <out.json>
Synthetic owned files only: no solver, no GPU and no user .blend or preferences.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,ARGS[0])
from ac9_cloth_sim.solver import native_runtime_io as M
from ac9_cloth_sim.core import native_three_io as B
OUT = Path(ARGS[1])
MEASURES = []


def raw_path(path):
    text = str(Path(path).absolute())
    if os.name == 'nt' and not text.startswith(chr(92)*2+'?'+chr(92)):
        text = chr(92)*2+'?'+chr(92)+text
    return Path(text)


@unittest.skipUnless(os.name == 'nt','actual Windows Blender host')
class LongRuntimePaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ac9_native_paths_')
        self.root = Path(self.temp.name).resolve()
        self.base_patch = patch.object(M,'_base',lambda:self.root/'runtime')
        self.base_patch.start()

    def tearDown(self):
        self.base_patch.stop()
        # This fixed, newly created temp root is the only recursive cleanup target.
        assert self.root.is_absolute() and self.root.name.startswith('ac9_native_paths_')
        import shutil
        shutil.rmtree(raw_path(self.root))
        self.temp.cleanup()

    def stage(self,length,label='stage'):
        root = self.root/label
        if len(str(root).encode('utf-16-le'))//2 > length:
            self.skipTest('private TEMP base is longer than the requested path fixture')
        while len(str(root).encode('utf-16-le'))//2 < length:
            remaining = length-len(str(root).encode('utf-16-le'))//2-1
            root = root.with_name(root.name+'x') if remaining == 0 else root/('x'*min(75,remaining))
        raw_path(root).mkdir(parents=True)
        self.assertEqual(len(str(root).encode('utf-16-le'))//2,length)
        return root

    def bridge(self,stage):
        config = stage/'config.json'
        raw_path(config).write_text('{"native_three":true,"live":true,"density":0.14}')
        return B.FrontendRuntime(config)

    def test_short_path_control_and_long_descriptor_temporary_cleanup(self):
        for length in (100,212):
            stage = self.stage(length,str(length))
            bridge = self.bridge(stage)
            effective = json.loads(raw_path(stage/'config.json').read_text())
            self.assertEqual(effective[M.DIR_KEY],str(bridge.owner.directory))
            self.assertEqual(effective['density'],.14)
            self.assertTrue(bridge.close())
            self.assertFalse(raw_path(bridge.owner.directory).exists())
            descriptor = json.loads(raw_path(stage/M.DESCRIPTOR).read_text())
            self.assertEqual(descriptor['phase'],'retired')
            self.assertEqual(descriptor['stage'],M._canonical(stage))
            self.assertEqual(list(raw_path(stage).glob('native_runtime_cleanup_*.json')),[])
            MEASURES.append(dict(case='allocate_abort',stage_units=length,phase=descriptor['phase']))

    def test_foreign_descriptor_beyond_260_is_not_invisible(self):
        stage = self.stage(245)
        descriptor = stage/M.DESCRIPTOR
        body = json.dumps(dict(version=1,stage='FOREIGN',token='a'*32,phase='allocated'))
        raw_path(descriptor).write_text(body)
        with self.assertRaisesRegex(ValueError,'Unrecognized'):
            M.RuntimeOwner(stage,dict(native_three=True,live=True))
        self.assertEqual(raw_path(descriptor).read_text(),body)
        MEASURES.append(dict(case='foreign',descriptor_units=len(str(descriptor)),unchanged=True))

    def test_two_allocations_cannot_replace_the_long_descriptor(self):
        stage = self.stage(245)
        first = self.bridge(stage)
        descriptor = stage/M.DESCRIPTOR
        body = raw_path(descriptor).read_bytes()
        with self.assertRaisesRegex(ValueError,'already has'):
            M.RuntimeOwner(stage,dict(native_three=True,live=True))
        self.assertEqual(raw_path(descriptor).read_bytes(),body)
        self.assertTrue(first.close())
        MEASURES.append(dict(case='double_owner',descriptor_units=len(str(descriptor)),unchanged=True))

    def test_worker_publishes_only_to_its_long_stage_and_releases_it(self):
        stage = self.stage(245)
        bridge = self.bridge(stage)
        config = json.loads(raw_path(stage/'config.json').read_text())
        worker = M.WorkerRuntime(config,stage)
        worker.start()
        try:
            worker.set_expected_points(2)
            expected = np.zeros((2,3),np.float32)
            np.save(raw_path(bridge.owner.directory/'live.npy'),expected)
            raw_path(bridge.owner.directory/'progress.txt').write_text('11 0')
            worker.success = True
        finally:
            worker.close()
        np.testing.assert_array_equal(np.load(raw_path(stage/'live.npy')),expected)
        self.assertEqual(raw_path(stage/'progress.txt').read_text(),'11 0')
        self.assertTrue(bridge.close())
        self.assertFalse(raw_path(bridge.owner.directory).exists())
        self.assertEqual(list(raw_path(stage).glob('*.tmp')),[])
        MEASURES.append(dict(case='publish',stage_units=245,progress=11,temporary_files=0))

    def test_non_bmp_path_length_is_measured_in_utf16_units(self):
        stage = self.stage(245,'\U0001f9f5'*20)
        self.assertLess(len(str(stage)),245)
        bridge = self.bridge(stage)
        self.assertTrue(bridge.close())
        MEASURES.append(dict(case='utf16',python_length=len(str(stage)),windows_units=245))

    def test_io_spelling_handles_drive_unc_existing_prefix_and_nonwindows(self):
        slash = chr(92)
        prefix = slash*2+'?'+slash
        drive = 'C:'+slash+'a'*245
        unc = slash*2+'server'+slash+'share'+slash+'a'*245
        self.assertEqual(str(M.io_path(drive)),prefix+drive)
        self.assertEqual(str(M.io_path(unc)),prefix+'UNC'+slash+unc[2:])
        self.assertEqual(M.io_path(M.io_path(unc)),M.io_path(unc))
        self.assertEqual(str(M.io_path('C:'+slash+'short')), 'C:'+slash+'short')
        with patch.object(M,'os',SimpleNamespace(name='posix')):
            self.assertEqual(M.io_path('../relative'),Path('../relative'))

    def test_long_runtime_directory_keeps_logical_ownership_and_cleans_up(self):
        stage = self.stage(100)
        cache = self.stage(245,'cache')/'runtime'
        with patch.object(M,'_base',lambda:cache):
            bridge = self.bridge(stage)
            config = json.loads(raw_path(stage/'config.json').read_text())
            self.assertEqual(config[M.DIR_KEY],str(bridge.owner.directory))
            self.assertFalse(config[M.DIR_KEY].startswith(chr(92)*2+'?'))
            self.assertTrue(bridge.path('params.json').startswith(chr(92)*2+'?'))
            worker = M.WorkerRuntime(config,stage)
            worker.start()
            try:
                worker.set_expected_points(2)
                np.save(raw_path(bridge.owner.directory/'live.npy'),np.zeros((2,3),np.float32))
                raw_path(bridge.owner.directory/'progress.txt').write_text('12 0')
                worker.success = True
            finally:
                worker.close()
            self.assertTrue(bridge.close())
            self.assertFalse(raw_path(bridge.owner.directory).exists())
        MEASURES.append(dict(case='long_runtime_directory',logical_path_unchanged=True,cleaned=True))

    def test_fixture_padding_advances_when_only_one_unit_remains(self):
        original = self.root
        self.root = self.stage(129,'padding') # with '/stage': 135 -> 211 -> old helper never advanced
        try:
            path = self.stage(212)
            self.assertEqual(len(str(path).encode('utf-16-le'))//2,212)
        finally:
            self.root = original


suite = unittest.defaultTestLoader.loadTestsFromTestCase(LongRuntimePaths)
result = unittest.TextTestRunner().run(suite)
OUT.write_text(json.dumps(dict(ok=result.wasSuccessful(),tests=result.testsRun,measures=MEASURES,
    skipped=[str(case)+': '+reason for case,reason in result.skipped],
    failures=[str(case)+'\n'+error for case,error in result.failures],
    errors=[str(case)+'\n'+error for case,error in result.errors]),indent=2),encoding='utf-8')
if not result.wasSuccessful():
    raise RuntimeError('Long Native runtime paths failed in the Blender host')
