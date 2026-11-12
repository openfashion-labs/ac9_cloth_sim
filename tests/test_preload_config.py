import ast
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from solver import preload_config as P


class PreloadConfigTests(unittest.TestCase):
    def test_current_mode_overrides_previous_job_without_copying_state(self):
        previous=dict(membrane='bw', bw_hess_reg=1., fast_live=True, checkpoint_in='old.npz', layers={'A':1}, native_runtime_dir='old', substeps=10)
        before=copy.deepcopy(previous); actual=P.choose(previous, True, True)
        self.assertEqual(actual,dict(membrane='bw',bw_hess_reg=1.,native_three=True,live=True))
        self.assertEqual(previous,before)
        self.assertNotEqual(P.variant_key(actual),P.variant_key(previous))

    def test_switching_back_to_normal_invalidates_native_ready(self):
        previous=dict(membrane='bw',bw_hess_reg=1.,native_three=True,live=True)
        actual=P.choose(previous,True,False)
        self.assertNotIn('native_three',actual)
        self.assertNotEqual(P.variant_key(actual),P.variant_key(previous))
        self.assertEqual(P.variant_key(actual),P.variant_key(dict(actual,native_three=False)))

    def test_membrane_switch_and_all_existing_compile_fields(self):
        previous=dict(membrane='bw',bw_hess_reg=2.,bw_comp_ratio=.1,bw_comp_width=.2,bw_comp_scale=.3)
        self.assertEqual(P.choose(previous,True,False),previous)
        self.assertEqual(P.choose(previous,False,False),{})
        self.assertEqual(P.choose({},True,False),dict(membrane='bw',bw_hess_reg=1.))
        for key in P.MATERIAL_FIELDS:
            changed=dict(previous,**{key:'changed'})
            self.assertNotEqual(P.variant_key(previous),P.variant_key(changed),key)

    def test_layer_compile_constants_survive_material_switch_and_key_defaults(self):
        previous=dict(membrane='bw',layers_recovery_friction=.25,layers_crossing_cache=False)
        for fabric in (False,True):
            actual=P.choose(previous,fabric,False)
            self.assertEqual(actual['layers_recovery_friction'],.25)
            self.assertFalse(actual['layers_crossing_cache'])
        self.assertEqual(P.variant_key({}),P.variant_key(dict(layers_recovery_friction=1.,layers_crossing_cache=True)))
        self.assertNotEqual(P.variant_key({}),P.variant_key(dict(layers_recovery_friction=.25)))
        self.assertNotEqual(P.variant_key({}),P.variant_key(dict(layers_crossing_cache=False)))
        self.assertEqual(P.snapshot(previous),previous)

    def test_snapshot_at_spawn_preserves_effective_compile_settings(self):
        actual=dict(membrane='bw',bw_hess_reg=1.,layers_recovery_friction=.25,layers_crossing_cache=False,native_three=True,live=True,grab_file='mutable',checkpoint_in=None)
        self.assertEqual(P.variant_key(actual),P.variant_key(P.snapshot(actual)))
        self.assertNotIn('grab_file',P.snapshot(actual))

    def test_preload_paths_are_immutable_per_variant(self):
        with tempfile.TemporaryDirectory(prefix='ac9_preload_cfg_')as base:
            with patch.object(P.tempfile,'gettempdir',lambda:base):
                a=P.materialize(dict(membrane='bw')); before=Path(a).read_bytes()
                b=P.materialize(dict(membrane='bw',native_three=True,live=True))
                self.assertNotEqual(a,b)
                self.assertEqual(Path(a).read_bytes(),before)
                self.assertEqual(a,P.materialize(dict(membrane='bw')))
                self.assertTrue(json.loads(Path(b).read_text())['native_three'])

    def test_real_take_refuses_failed_and_incompatible_ready(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'dress.py').read_text(encoding='utf8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='_standby_take')
        code=compile(ast.Module(body=[function],type_ignores=[]),'<real standby take>','exec')
        for failed,old_key,new_key,expected in [(True,'three','three',False),(False,'normal','three',False),(False,'three','three',True)]:
            calls=[];process=NS(pid=123,poll=lambda:None)
            state={'proc':process,'key':old_key}
            scope=dict(_STANDBY=state,_PRELOAD_SESSION={'path':'status'},_preload_status=NS(read=lambda p:{'state':'failed'if failed else'ready'}),_trace=lambda *a,**k:None,_standby_key=lambda *a:new_key,standby_end=lambda:calls.append('end'),_standby_drop_log=lambda:calls.append('log'))
            exec(code,scope);actual=scope['_standby_take']('python',{},'config')
            self.assertEqual(actual is process,expected)
            self.assertEqual('end'in calls,not expected)


if __name__ == '__main__':unittest.main()
