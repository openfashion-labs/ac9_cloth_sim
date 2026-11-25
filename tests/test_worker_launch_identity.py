"""A warm import must not authorize a different source version for normal run."""
import io,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch

SOLVER=Path(__file__).resolve().parents[1]/'solver'
sys.path.insert(0,str(SOLVER))
import live_session_worker as W

class LaunchIdentity(unittest.TestCase):
    def test_warm_mutation_is_rejected_before_idle(self):
        for edit in (False,True):
            with self.subTest(edit=edit),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);solver=root/'solver';solver.mkdir()
                source=solver/'prepared_live.py';source.write_text('value=1\n')
                newton=root/'newton';newton.mkdir();(newton/'__init__.py').write_text('value=1\n')
                spec=dict(root=str(root),session_token='session')
                W.protocol.write(root/'warm.json',dict(native_three=True,live=True))
                warp=Mock()
                with patch.object(W.protocol,'__file__',str(source)),patch.object(W.protocol,'dependency_manifest',return_value={}),patch.dict(sys.modules,{'newton':NS(__file__=str(newton/'__init__.py')),'warp':warp}),patch.object(sys,'stdin',io.StringIO('')),patch.object(W,'mark'):
                    spec['code']=W.protocol.code_id();W.protocol.write(root/'session.json',spec)
                    def warm(*a,**k):
                        if edit:source.write_text('value=2\n')
                    with patch.object(W.runpy,'run_path',side_effect=warm):code=W.main(root/'session.json')
                    status=json.loads((root/'status.json').read_text())
                self.assertEqual(code,1 if edit else 0)
                self.assertEqual(status['phase'],'poisoned' if edit else 'idle')
                if edit:self.assertIn('source changed since launch',status['message'])
