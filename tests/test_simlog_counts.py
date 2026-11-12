# SPDX-License-Identifier: GPL-3.0-or-later
"""Rolling Live output must not report one frame, including after checkpoint resume."""
from pathlib import Path
import ast
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import simlog
from core.simlog import stage_frame_counts


class StageFrameCountsTests(unittest.TestCase):
    def test_rolling_live_counts_execution_instead_of_two_saved_shapes(self):
        self.assertEqual(stage_frame_counts({'frame_s': [.1] * 481, 'stopped_at': 481}, 1), (481, 481))

    def test_resumed_run_reports_new_steps_and_absolute_end_separately(self):
        data = {'frame_s': [.1] * 100, 'stopped_at': 581,
                'checkpoint_resume': {'status': 'restored', 'frame': 481}}
        self.assertEqual(stage_frame_counts(data, 1), (100, 581))

    def test_failed_first_step_has_zero_completed_frames(self):
        self.assertEqual(stage_frame_counts({'frame_s': []}, 0), (0, 0))

    def test_phase_frames_and_legacy_log_keep_their_counts(self):
        self.assertEqual(stage_frame_counts({'frame_s': [.1] * 120}, 120), (120, 120))
        self.assertEqual(stage_frame_counts({}, 120), (120, 120))

    def test_actual_stage_end_does_not_label_resumed_errors_as_still(self):
        # Exercise the UI operator's actual log-writing method without importing bpy.
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'dress.py').read_text(encoding='utf8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AC9_OT_SimDress')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_log_stages')
        scope = dict(os=os, json=json, np=np, simlog=simlog, tx=lambda s, *a: s.format(*a),
                     log=lambda ctx, folder, kind, text, **kw: simlog.write(folder, kind, text, **kw))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'dress.py', 'exec'), scope)
        resume = {'status': 'restored', 'frame': 481}
        cases = [({'frame_s': [], 'checkpoint_resume': resume, 'error': 'failed'}, 1000, 0, 481, None),
                 ({'frame_s': [.1]*42, 'checkpoint_resume': resume, 'error': 'nonfinite'}, 1000, 42, 523, None),
                 ({'frame_s': [.1]*100, 'checkpoint_resume': resume, 'settled_at': 581}, 1000, 100, 581, 'stopped once still'),
                 ({'frame_s': [.1]*481, 'stopped_at': 481}, 0, 481, 481, 'stopped by you')]
        for data, cap, expected, end, reason in cases:
            with self.subTest(data=data.keys()), tempfile.TemporaryDirectory() as folder:
                p = Path(folder)
                (p/'run_log.json').write_text(json.dumps(data), encoding='utf8')
                (p/'config.json').write_text(json.dumps({'frames': cap}), encoding='utf8')
                # There is deliberately no frames.npy: modern logs survive cache cleanup.
                worker = SimpleNamespace(info={'dir': folder, 'mesh': {}, 'stages': ['test']}, stage_dirs=[folder], k=0)
                scope['_log_stages'](worker, None, [True])
                entry = simlog.read(folder)[0]
                self.assertEqual((entry['frames'], entry['frame_end']), (expected, end))
                if reason:
                    self.assertIn(reason, entry['text'])
                else:
                    self.assertNotIn('stopped once still', entry['text'])
                    self.assertNotIn('stopped by you', entry['text'])


if __name__ == '__main__':
    unittest.main()
