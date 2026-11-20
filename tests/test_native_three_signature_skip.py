"""Native3 never consumes full-state signatures; regular Live still builds both."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def signature_block():
    tree=ast.parse((ROOT/'solver/newton_vbd.py').read_text(encoding='utf8'))
    node=next(n for n in tree.body if isinstance(n,ast.If) and isinstance(n.test,ast.Name)
              and n.test.id=='NATIVE_THREE' and any(isinstance(a,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='_CHECKPOINT_BUILD'for t in a.targets)for a in n.body))
    return compile(ast.Module(body=[node],type_ignores=[]),'signature_boundary','exec')

class SignatureBoundaryTests(unittest.TestCase):
    def run_block(self,native):
        calls=[];events=[]
        checkpoint=NS(build_signature=lambda g:calls.append('build')or {'build':'unchanged'},
                      fingerprint=lambda g:calls.append('fingerprint')or {'fingerprint':'unchanged'})
        g=dict(NATIVE_THREE=native,_trace=lambda event,**kw:events.append((event,kw)))
        with patch.dict(sys.modules,{'live_checkpoint':checkpoint}):exec(signature_block(),g)
        return calls,events,g
    def test_native_three_does_not_read_model_or_files_for_resume(self):
        calls,events,g=self.run_block(True)
        self.assertEqual(calls,[])
        self.assertIsNone(g['_CHECKPOINT_BUILD']);self.assertIsNone(g['_CHECKPOINT_FINGERPRINT'])
        self.assertEqual(events,[('checkpoint_signature_skipped',{'reason':'native_three_shape_only'})])
    def test_regular_live_keeps_order_and_results(self):
        calls,events,g=self.run_block(False)
        self.assertEqual(calls,['build','fingerprint'])
        self.assertEqual(g['_CHECKPOINT_BUILD'],{'build':'unchanged'})
        self.assertEqual(g['_CHECKPOINT_FINGERPRINT'],{'fingerprint':'unchanged'})
        self.assertEqual([v[0]for v in events],['checkpoint_build_signature_begin','checkpoint_build_signature_end','checkpoint_fingerprint_begin','checkpoint_fingerprint_end'])
    def test_native_three_still_rejects_checkpoint_input(self):
        from solver.native_three_contract import validate_config
        with self.assertRaises(ValueError):validate_config(dict(native_three=True,live=True,checkpoint_in='old.npz'))
    def test_native_three_save_refuses_before_consuming_signatures(self):
        from solver.live_checkpoint import save
        with patch.dict(sys.modules,{'warp':NS(synchronize=lambda:None)}):
            with self.assertRaisesRegex(ValueError,'full-state resume'):
                save('unused.npz',{'cfg':{'native_three':True}},1)

if __name__=='__main__':unittest.main()
