# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in recorder: stalled callbacks, interrupted runs, rotation and transparent wrappers."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

FILE = Path(__file__).resolve().parents[1] / 'latency_trace.py'
spec = importlib.util.spec_from_file_location('latency_test', FILE)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)


def rows(folder):
    return [json.loads(line) for p in Path(folder).glob('*.jsonl') for line in p.read_text().splitlines()]


class TraceTests(unittest.TestCase):
    def tearDown(self):
        if L._recorder:
            L._recorder.close()
        L._recorder = None
        L._pending.clear()

    def test_disabled_is_transparent(self):
        marker = object()
        self.assertIs(L.wrap(lambda: marker, 'off')(), marker)
        self.assertFalse(L._pending)

    def test_begin_is_readable_before_return_and_exception_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            L._recorder = L.Recorder(d)
            error = ValueError('same exception')
            def callback(_self, _ctx):
                self.assertTrue(any(r['event']=='begin' for r in rows(d)))
                raise error
            with self.assertRaises(ValueError) as caught:
                L.wrap(callback, 'op.execute', method=True)(None, None)
            self.assertIs(caught.exception, error)
            L._recorder.close()
            records=rows(d)
            begin=next(r for r in records if r['event']=='begin')
            end=next(r for r in records if r['event']=='end')
            self.assertEqual(begin['token'], end['token'])
            self.assertEqual(end['error'], 'ValueError')
            self.assertGreaterEqual(end['seconds'], 0)

    def test_stall_sample_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            r=L.Recorder(d, stall_s=.1)
            span=r.begin('slow_prepare', durable=True)
            time.sleep(.32)
            r.end(span);r.pulse();r.close();r.thread.join(1)
            sample=next(x for x in rows(d) if x['event']=='stall_sample')
            self.assertEqual(sample['active'][0]['label'], 'slow_prepare')
            self.assertIn('test_stall_sample', sample['python_stack'])
            self.assertFalse(r.thread.is_alive())

    def test_rotation_is_bounded_and_other_session_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            old=Path(d)/'earlier';old.mkdir();keep=old/'keep';keep.write_text('keep')
            r=L.Recorder(Path(d)/'session', limit=400)
            for i in range(50):r.write('sample', i=i, payload='a'*150)
            r.close()
            self.assertLessEqual(len(list((Path(d)/'session').glob('*.jsonl'))),4)
            self.assertEqual(keep.read_text(),'keep')
            self.assertTrue(any(x.get('i')==49 for x in rows(Path(d)/'session')))

    def test_fast_noise_has_no_begin_end_records(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            L._recorder=L.Recorder(d)
            fn=L.wrap(lambda *_: {'RUNNING_MODAL'},'op.modal',method=True)
            self.assertEqual(fn(None,None,SimpleNamespace(type='MOUSEMOVE',value='NOTHING')),{'RUNNING_MODAL'})
            self.assertFalse(any(x['event'] in ('begin','end') for x in rows(d)))
            self.assertFalse(L._pending)
            L._recorder.close()

    def test_disk_failure_does_not_replace_operation_result(self):
        with tempfile.TemporaryDirectory() as d:
            r=L.Recorder(d);L._recorder=r
            class Broken:
                def tell(self):raise OSError('full')
                def close(self):pass
            r.stream.close();r.stream=Broken()
            self.assertEqual(L.wrap(lambda *_:{'FINISHED'},'op.execute',method=True)(None,None),{'FINISHED'})
            self.assertIn('full',r.error)

    def test_process_exit_preserves_unfinished_begin(self):
        with tempfile.TemporaryDirectory() as d:
            runner=Path(d)/'child.py'
            runner.write_text('import importlib.util,os\n'
                f's=importlib.util.spec_from_file_location("l",{str(FILE)!r})\n'
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
                f'r=m.Recorder({str(Path(d)/"logs")!r})\n'
                'r.begin("interrupted",durable=True)\nos._exit(17)\n',encoding='utf-8')
            child=subprocess.run([sys.executable,str(runner)],capture_output=True,timeout=10)
            self.assertEqual(child.returncode,17)
            records=rows(Path(d)/'logs')
            self.assertTrue(any(r['event']=='begin' and r['label']=='interrupted' for r in records))
            self.assertFalse(any(r['event']=='session_end' for r in records))

    def test_close_failure_is_fail_open_and_stop_detaches(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            r=L.Recorder(d)
            real=r.stream
            class CloseFails:
                def tell(self):return real.tell()
                def write(self,value):return real.write(value)
                def flush(self):return real.flush()
                def close(self):
                    real.close()
                    raise OSError('close failed')
            r.stream=CloseFails();r.close();r.thread.join(1)
            self.assertTrue(r.closed);self.assertEqual(r.error,'close failed')
        detached=[];handle=object();L._draw_handle=handle
        class RecorderFails:
            def close(self):raise OSError('injected close')
        L._recorder=RecorderFails()
        bpy=NS(app=NS(timers=NS(is_registered=lambda _:True,unregister=lambda _:detached.append('timer'))),
               types=NS(SpaceView3D=NS(draw_handler_remove=lambda h,_:detached.append(('draw',h)))))
        with patch.dict(sys.modules,{'bpy':bpy}):L.stop()
        self.assertEqual(detached,['timer',('draw',handle)])
        self.assertIsNone(L._recorder);self.assertIsNone(L._draw_handle)


if __name__=='__main__':unittest.main()
