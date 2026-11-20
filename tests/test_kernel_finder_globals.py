"""The kernel finder newton_vbd.py puts on sys.meta_path outlives the script's globals in a Native 3 session (the
worker clears each job's globals, solver/live_session_worker.py): its find_spec must read nothing from them."""
import ast
import builtins
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'solver' / 'newton_vbd.py'


class KernelFinder(unittest.TestCase):
    def test_find_spec_reads_no_script_globals(self):
        tree = ast.parse(SRC.read_text(encoding='utf8'))
        cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == '_KernelPatch')
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            local = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            local |= {t.id for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.AugAssign))
                      for t in ([n.target] if isinstance(n, ast.AugAssign) else n.targets) if isinstance(t, ast.Name)}
            used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            outside = sorted(u for u in used - local if not hasattr(builtins, u))
            self.assertEqual(outside, [], f"_KernelPatch.{fn.name} reads script globals {outside}")

    def test_finder_works_after_the_globals_are_cleared(self):
        # the class as the script defines it, run in a namespace that is then cleared, as the session does
        tree = ast.parse(SRC.read_text(encoding='utf8'))
        cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == '_KernelPatch')
        ns = {'importlib': __import__('importlib.abc').abc and __import__('importlib')}
        exec(compile(ast.Module([cls], []), str(SRC), 'exec'), ns)
        f = ns['_KernelPatch']({'zz_kernel_mod': 'x.py'}, lambda name, p: (name, p))
        ns.clear()
        self.assertEqual(f.find_spec('zz_kernel_mod'), ('zz_kernel_mod', 'x.py'))
        self.assertIsNone(f.find_spec('json'))


if __name__ == '__main__':
    unittest.main()
