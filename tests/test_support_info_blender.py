# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender integration and failure boundaries; does not access the OS clipboard.

blender -b --factory-startup --python tests/test_support_info_blender.py -- <package parent> <out.json>
"""
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ARGS = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, ARGS[0])
import ac9_cloth_sim
from ac9_cloth_sim import support_info as S

C = S.AC9_OT_SimCopyDiagnostics
INVOKE, EXECUTE, DRAW, CANCEL = C.invoke, C.execute, C.draw, C.cancel
RESULT = {}
NETWORK = []


def audit(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system'):
        NETWORK.append(event)
        raise AssertionError('support operation must not connect or launch a process')


class Clipboard:
    def __init__(self, fail=False):
        self.writes, self.dialogs, self.fail = [], [], fail

    @property
    def clipboard(self):
        raise AssertionError('must not read previous clipboard')

    @clipboard.setter
    def clipboard(self, value):
        if self.fail:
            raise RuntimeError('DUMMY_PRIVATE_ERROR')
        self.writes.append(value)

    def invoke_props_dialog(self, op, **kw):
        self.dialogs.append(kw)
        return {'RUNNING_MODAL'}


class Layout:
    def __init__(self):
        self.lines = []

    def label(self, **kw):
        self.lines.append(kw)

    def box(self):
        return self


def operator():
    reports = []
    return SimpleNamespace(report=lambda *args: reports.append(args), reports=reports,
                           layout=Layout())


class TestSupportIntegration(unittest.TestCase):
    def test_confirmed_snapshot_only_once(self):
        wm, op = Clipboard(), operator()
        ctx = SimpleNamespace(window_manager=wm)
        self.assertEqual(INVOKE(op, ctx, None), {'RUNNING_MODAL'})
        snapshot = op._snapshot
        self.assertEqual(wm.writes, [])
        DRAW(op, ctx)
        shown = [item['text'] for item in op.layout.lines if item['text'] in snapshot.splitlines()]
        self.assertEqual(shown, snapshot.splitlines())
        self.assertTrue(all(item['translate'] is False for item in op.layout.lines))
        with patch.object(S, 'collect', side_effect=AssertionError('must not recollect')):
            self.assertEqual(EXECUTE(op, ctx), {'FINISHED'})
        self.assertEqual(wm.writes, [snapshot])
        self.assertEqual(EXECUTE(op, ctx), {'CANCELLED'})
        self.assertEqual(wm.writes, [snapshot])

    def test_cancel_does_not_write(self):
        wm, op = Clipboard(), operator()
        ctx = SimpleNamespace(window_manager=wm)
        INVOKE(op, ctx, None)
        CANCEL(op, ctx)
        self.assertEqual(EXECUTE(op, ctx), {'CANCELLED'})
        self.assertEqual(wm.writes, [])

    def test_direct_execute_does_not_write(self):
        wm = Clipboard()
        self.assertEqual(EXECUTE(operator(), SimpleNamespace(window_manager=wm)), {'CANCELLED'})
        self.assertEqual(wm.writes, [])

    def test_clipboard_failure_no_success_no_private_error(self):
        wm, op = Clipboard(fail=True), operator()
        ctx = SimpleNamespace(window_manager=wm)
        INVOKE(op, ctx, None)
        self.assertEqual(EXECUTE(op, ctx), {'CANCELLED'})
        self.assertEqual(wm.writes, [])
        self.assertNotIn('DUMMY_PRIVATE_ERROR', str(op.reports))
        self.assertTrue(all('INFO' not in levels for levels, text in op.reports))

    def test_silent_failure_does_not_claim_success(self):
        class Silent(Clipboard):
            @Clipboard.clipboard.setter
            def clipboard(self, value):
                pass
        wm, op = Silent(), operator()
        ctx = SimpleNamespace(window_manager=wm)
        INVOKE(op, ctx, None)
        self.assertEqual(EXECUTE(op, ctx), {'FINISHED'})
        self.assertEqual(wm.writes, [])
        message = op.reports[-1][1]
        self.assertNotIn('copied', message.lower())
        self.assertIn('local', message.lower())

    def test_bounded_manifest_and_fallback(self):
        for content, expected in ((b'version="1.2.3"', '1.2.3'),
                                  (b'version="DUMMY_PRIVATE"', 'DUMMY_PRIVATE'),
                                  (b'invalid [', 'unknown'), (b'\xff', 'unknown'),
                                  (b'x' * 65537, 'unknown')):
            with patch.object(Path, 'open', return_value=io.BytesIO(content)):
                self.assertEqual(S.package_version(), expected)
                self.assertNotIn('DUMMY_PRIVATE', S.collect())
        with patch.object(Path, 'open', side_effect=FileNotFoundError('DUMMY_PATH')):
            self.assertEqual(S.package_version(), 'unknown')
            self.assertNotIn('DUMMY_PATH', S.collect())

    def test_no_data_or_argument_properties(self):
        props = set(bpy.ops.ac9_cloth_sim.copy_diagnostics.get_rna_type().properties.keys())
        self.assertEqual(props, {'rna_type'})
        self.assertNotIn('INTERNAL', C.bl_options)
        self.assertNotIn('UNDO', C.bl_options)
        self.assertTrue(C.poll(SimpleNamespace(window_manager=Clipboard())))
        self.assertFalse(C.poll(SimpleNamespace(window_manager=None)))

    def test_real_operator_without_preview_refuses(self):
        self.assertEqual(bpy.ops.ac9_cloth_sim.copy_diagnostics('EXEC_DEFAULT'), {'CANCELLED'})


if __name__ == '__main__':
    ac9_cloth_sim.register()
    # Kept implementation: opt in only for these private integration tests, not public registration.
    S.register()
    # The report must not change even if unrelated scene metadata contains private-looking strings.
    before = S.collect()
    bpy.context.scene['support_dummy'] = 'DUMMY_PRIVATE_SCENE_9'
    after = S.collect()
    RESULT['scene_private_markers_emitted'] = int('DUMMY_PRIVATE_SCENE_9' in after)
    RESULT['unrelated_scene_changes_report'] = before != after
    del bpy.context.scene['support_dummy']
    initial = tuple((o.name, o.data.name if o.data else None) for o in bpy.data.objects)
    sys.addaudithook(audit)
    prog = unittest.main(argv=['x'], exit=False, verbosity=2)
    RESULT.update(ok=prog.result.wasSuccessful(), tests=prog.result.testsRun,
                  network_or_child_attempts=NETWORK,
                  scene_structure_unchanged=initial == tuple((o.name, o.data.name if o.data else None)
                                                           for o in bpy.data.objects),
                  report=json.loads(S.collect()))
    Path(ARGS[1]).write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(0 if RESULT['ok'] else 1)
