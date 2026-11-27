"""The real guard must seal changed cancellations from EXEC-started modals.

blender --factory-startup --python this.py -- <parent> <out.json>
Fingerprint and undo-push are CPU stand-ins; native history is checked separately.
"""
import json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import bpy
ARGS=sys.argv[sys.argv.index('--')+1:];sys.path.insert(0,ARGS[0]);OUT=Path(ARGS[1])
from ac9_cloth_sim import undo_guard as G


class ModalGuard(unittest.TestCase):
    def setUp(self):
        self.value=[0];self.steps=[]
        fake=SimpleNamespace(ops=SimpleNamespace(ed=SimpleNamespace(undo_push=lambda **kw:self.steps.append(kw))))
        for name,value in dict(bpy=fake,_stack=[],_modal={},_seq=[0],_mark=[0,None],_later=[False],pushed=[0],
                               fingerprint=lambda:self.value[0],_pattern_preflight=lambda *a:None).items():
            p=patch.object(G,name,value);p.start();self.addCleanup(p.stop)
        self.cls=SimpleNamespace(bl_options={'UNDO'},bl_label='Private modal',modal=lambda *a:None)
        self.op=SimpleNamespace(as_pointer=lambda:42)

    def start(self,mode='execute',nested=False,cls=None,op=None):
        cls=cls or self.cls;op=op or self.op
        execute=G._wrap_execute(lambda s,c:{'RUNNING_MODAL'},cls)
        invoke=G._wrap_invoke((lambda s,c,e:execute(s,c)) if nested else (lambda s,c,e:{'RUNNING_MODAL'}),cls)
        return invoke(op,None,None) if mode=='invoke' else execute(op,None)

    def finish(self,result,changed=True,cls=None,op=None):
        if changed:self.value[0]+=1
        def modal(s,c,e):
            if result=='raise':raise RuntimeError('private modal failure')
            return {result}
        return G._wrap_modal(modal,cls or self.cls)(op or self.op,None,None)

    def test_changed_cancel_is_sealed_from_both_entrypoints(self):
        for mode in ('execute','invoke'):
            with self.subTest(mode=mode):
                prior=len(self.steps);self.start(mode)
                self.assertEqual(len(G._modal),1)
                self.finish('CANCELLED')
                self.assertEqual(len(self.steps)-prior,1);self.assertEqual(G._modal,{})

    def test_noop_cancel_and_undo_finished_add_no_extra_step(self):
        for mode in ('execute','invoke'):
            for result,changed in [('CANCELLED',False),('FINISHED',True)]:
                prior=len(self.steps);self.start(mode);self.finish(result,changed)
                self.assertEqual(len(self.steps),prior);self.assertEqual(G._modal,{})

    def test_exception_seals_and_removes_modal_start(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError,'private modal failure'):self.finish('raise')
        self.assertEqual(len(self.steps),1);self.assertEqual(G._modal,{})

    def test_invoke_calling_execute_keeps_one_outer_start(self):
        self.start('invoke',nested=True)
        self.assertEqual(len(G._modal),1);self.assertEqual(G._stack,[])
        self.finish('CANCELLED');self.assertEqual(len(self.steps),1);self.assertEqual(G._modal,{})

    def test_execute_calling_invoke_keeps_one_outer_start(self):
        invoke=G._wrap_invoke(lambda s,c,e:{'RUNNING_MODAL'},self.cls)
        execute=G._wrap_execute(lambda s,c:invoke(s,c,None),self.cls)
        self.assertEqual(execute(self.op,None),{'RUNNING_MODAL'})
        self.assertEqual(len(G._modal),1);self.assertEqual(G._stack,[])
        self.finish('CANCELLED');self.assertEqual(len(self.steps),1);self.assertEqual(G._modal,{})

    def test_finished_without_undo_gets_the_guards_step(self):
        cls=SimpleNamespace(bl_options=set(),bl_label='No Undo',modal=lambda *a:None)
        self.start(cls=cls);self.finish('FINISHED',cls=cls)
        self.assertEqual(len(self.steps),1);self.assertEqual(G._modal,{})

    def test_nonmodal_parent_delegates_without_keeping_a_record(self):
        parent=SimpleNamespace(bl_options={'UNDO'},bl_label='Parent')
        op=SimpleNamespace(as_pointer=lambda:84)
        def delegate(s,c):return self.start('invoke')
        self.assertEqual(G._wrap_execute(delegate,parent)(op,None),{'RUNNING_MODAL'})
        self.assertEqual(set(G._modal),{42})
        self.finish('CANCELLED');self.assertEqual(G._modal,{})
        self.assertEqual(len(self.steps),1)

    def test_intermediate_modal_event_keeps_start_until_terminal(self):
        self.start();self.finish('RUNNING_MODAL')
        self.assertEqual(len(self.steps),0);self.assertIn(42,G._modal)
        self.finish('PASS_THROUGH',False)
        self.assertEqual(len(self.steps),0);self.assertIn(42,G._modal)
        self.finish('CANCELLED');self.assertEqual(len(self.steps),1);self.assertEqual(G._modal,{})


result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ModalGuard))
OUT.write_text(json.dumps(dict(ok=result.wasSuccessful(),tests=result.testsRun,
    failures=[str(c)+'\n'+err for c,err in result.failures],errors=[str(c)+'\n'+err for c,err in result.errors]),indent=2),encoding='utf8')
if not result.wasSuccessful():raise RuntimeError('Modal guard contracts failed')
