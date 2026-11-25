# SPDX-License-Identifier: GPL-3.0-or-later
"""The solver script's kernels are made with _kernel (only wp in their globals). With @wp.kernel, Warp hashed the
script's loop counters (f, k, x) into the kernels whose arguments or loop variables share their names, so the module
was compiled again after every pattern edit. Checked from the source alone (no Warp needed)."""
import ast
import os
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'solver', 'newton_vbd.py')


class KernelCleanGlobals(unittest.TestCase):
    def test_all_kernels_use_the_clean_decorator(self):
        tree = ast.parse(open(SRC, encoding='utf-8').read())
        kernels, plain = [], []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for d in fn.decorator_list:
                s = ast.unparse(d)
                if s.startswith('wp.kernel'):
                    plain.append((fn.lineno, fn.name))
                elif s.startswith('_kernel'):
                    kernels.append(fn)
        self.assertEqual(plain, [], 'use @_kernel in solver/newton_vbd.py')
        self.assertGreater(len(kernels), 10)

    def test_kernels_need_no_other_global_than_wp(self):
        import builtins
        tree = ast.parse(open(SRC, encoding='utf-8').read())
        for fn in ast.walk(tree):
            if not (isinstance(fn, ast.FunctionDef) and any(ast.unparse(d).startswith('_kernel') for d in fn.decorator_list)):
                continue
            local = {a.arg for a in fn.args.args}
            nodes = [n for st in fn.body for n in ast.walk(st)]
            local |= {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            ext = {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            self.assertEqual(ext - local - set(dir(builtins)) - {'wp'}, set(), fn.name)


if __name__ == '__main__':
    unittest.main()
