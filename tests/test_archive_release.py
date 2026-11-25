"""A late native writer release still seals the accepted result, never a new job."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


class Object(dict):
    session_uid = 7


class ArchiveRelease(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1]/'dress.py').read_text(encoding='utf8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AC9_OT_SimDress')
        methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('_release_native_io','_archive_result')]
        self.calls=[];self.timers=[];self.obj=Object(ac9_job='job')
        self.context=NS(scene=NS(ac9_cloth_sim=NS(dress_status='')))
        self.J=NS(_EPOCH=1,finish=lambda *a,**kw:self.calls.append((a,kw)),capture=lambda *a:dict(accepted=True))
        self.bpy=NS(context=self.context,data=NS(objects={'cloth':self.obj}),
                    app=NS(timers=NS(register=lambda fn,**kw:self.timers.append(fn))))
        ns=dict(JA=self.J,bpy=self.bpy,tx=lambda s,*a:s.format(*a),_trace=lambda *a,**kw:None)
        exec(compile(ast.Module(body=methods,type_ignores=[]),'<real release methods>','exec'),ns)
        self.lease=dict(writer_released=False,gui_complete=False)
        self.owner=NS(_archive_lease=self.lease,info={'dir':'job'},ob_name='cloth',ob=self.obj,log=NS(closed=True))
        self.release=lambda:ns['_release_native_io'](self.owner)
        self.archive=lambda:ns['_archive_result'](self.owner,self.context)

    def start_pending(self):
        answers=iter([None,True])
        self.owner._native_io=NS(close=lambda:next(answers),error=None)
        self.release();self.archive()
        self.assertEqual(self.calls,[])
        self.assertTrue(self.lease['gui_complete'])

    def test_late_release_seals_once(self):
        self.start_pending();self.assertIsNone(self.timers[0]())
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('_after_release',self.lease)

    def test_late_release_preserves_old_job_without_touching_next_job(self):
        self.start_pending();self.obj['ac9_job']='next';self.timers[0]()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.obj['ac9_job'],'next')

    def test_late_release_after_undo_still_preserves_old_job(self):
        self.start_pending();self.J._EPOCH+=1;self.timers[0]()
        self.assertEqual(len(self.calls),1)

    def test_unaccepted_result_never_requests_archive(self):
        self.J.capture=lambda *a:None
        self.archive()
        self.assertNotIn('_after_release',self.lease)
        self.assertEqual(self.calls,[])

    def test_failed_release_reports_unsaved_without_sealing(self):
        answers=iter([None,False])
        self.owner._native_io=NS(close=lambda:next(answers),error='injected')
        self.release();self.archive();self.timers[0]()
        self.assertEqual(self.calls,[])
        self.assertIn('not released',self.context.scene.ac9_cloth_sim.dress_status)

    def test_immediate_release_seals_without_callback(self):
        self.owner._native_io=NS(close=lambda:True,error=None)
        self.release();self.archive()
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('_after_release',self.lease)
