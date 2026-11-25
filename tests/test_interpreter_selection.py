"""Interpreter selection must not probe remote files for an owned live worker."""
import ast,os,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

class Selection(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).resolve().parents[1]/'dress.py'
        tree=ast.parse(source.read_text(encoding='utf8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='newton_python')
        self.path=os.path.abspath('owned-python.exe')
        self.prefs=NS(newton_python=self.path)
        self.session=NS(python=self.path,poisoned=False,process=NS(poll=lambda:None))
        self.g=dict(os=os,sys=sys,__file__=str(source),addon_prefs=lambda c:self.prefs,
                    bpy=NS(path=NS(abspath=os.path.abspath)),solver_path=lambda:None,_LIVE_SESSION=self.session)
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),self.g)
        self.fn=self.g['newton_python']

    def test_live_exact_interpreter_does_not_probe_filesystem(self):
        with patch.object(os.path,'exists',side_effect=AssertionError('remote stat')),patch.object(os.path,'realpath',side_effect=AssertionError('junction lookup')):
            self.assertEqual(self.fn(None),self.path)

    def test_new_preference_is_not_replaced_by_old_worker(self):
        self.prefs.newton_python=os.path.abspath('new-python.exe')
        with patch.object(os.path,'exists',return_value=True) as check:
            self.assertEqual(self.fn(None),self.prefs.newton_python)
        check.assert_called_once_with(self.prefs.newton_python)

    def test_dead_or_poisoned_worker_does_not_bypass_selection(self):
        for poisoned,exitcode in ((True,None),(False,0)):
            self.session.poisoned=poisoned;self.session.process.poll=lambda:exitcode
            with patch.object(os.path,'exists',return_value=True) as check:
                self.assertEqual(self.fn(None),self.path)
            check.assert_called_once_with(self.path)

    def test_explicit_existing_path_does_not_resolve_development_junction(self):
        self.g['_LIVE_SESSION']=None
        with patch.object(os.path,'exists',return_value=True),patch.object(os.path,'realpath',side_effect=AssertionError('fallback')):
            self.assertEqual(self.fn(None),self.path)

if __name__=='__main__':unittest.main()
