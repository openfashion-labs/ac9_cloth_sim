import ast
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import nullcontext
import unittest

class Garment(dict):
    type='MESH';name='AC9_Garment_probe'
    session_uid=7
    def as_pointer(self):return 1

class AutoMeasure(unittest.TestCase):
    def setUp(self):
        source=ast.parse((Path(__file__).resolve().parents[1]/'measure_tool.py').read_text(encoding='utf8'))
        functions=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('_measurement_job','_auto_garment','_measure_completed')]
        for n in functions:n.decorator_list=[]
        self.operator=NS(_active_instance=None);self.calls=[]
        self.obj=Garment(ac9_job='work/job1',ac9_job_generation='gen1',ac9_job_recovery='recovery/job1')
        self.scene=NS(name='Scene',objects=[self.obj],ac9_cloth_sim=NS(body=object()))
        self.scope=dict(Path=Path,_jobs={},_pending_results={},dress=NS(GARMENT_PREFIX='AC9_Garment_',AC9_OT_SimDress=self.operator),
                        bpy=NS(context=NS(temp_override=lambda **kw:nullcontext()),data=NS(objects={self.obj.name:self.obj},scenes={'Scene':self.scene}),
                               app=NS(timers=NS(is_registered=lambda fn:False,register=lambda *a,**kw:None))),
                        read=lambda *a:dict(items={'waist':1}),measure_garment=lambda *a:self.calls.append(a))
        exec(compile(ast.Module(body=functions,type_ignores=[]),'<real auto measurement>','exec'),self.scope)
        self.auto=lambda:self.scope['_auto_garment'](self.scene)
        self.measure=lambda:self.scope['_measure_completed']()
    def test_archive_paths_do_not_remeasure_identical_generation(self):
        self.auto();self.obj['ac9_job']='published/job1';self.auto()
        self.assertEqual(self.calls,[])
    def test_running_job_is_measured_once_after_completion(self):
        self.auto();self.operator._active_instance=object()
        self.obj.update(ac9_job='work/job2',ac9_job_recovery='recovery/job2',ac9_job_generation='gen2')
        self.auto();self.assertEqual(self.calls,[])
        self.operator._active_instance=None;self.auto();self.auto()
        self.assertEqual(self.calls,[])
        self.measure();self.measure()
        self.assertEqual(len(self.calls),1)
    def test_non_live_job_ignores_old_generation_attributes(self):
        self.auto();self.obj['ac9_job']='phase/different_job';self.auto();self.measure()
        self.assertEqual(len(self.calls),1)

    def test_a_new_run_defers_pending_measurement_and_rejects_old_identity(self):
        self.auto();self.obj['ac9_job']='phase/job2';self.auto()
        self.operator._active_instance=object();self.assertEqual(self.measure(),.1)
        self.assertEqual(self.calls,[])
        self.obj['ac9_job']='phase/job3';self.operator._active_instance=None
        self.measure();self.assertEqual(self.calls,[])
        self.auto();self.measure();self.assertEqual(len(self.calls),1)
