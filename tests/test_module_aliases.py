# SPDX-License-Identifier: GPL-3.0-or-later
"""Every short module alias (S., PT., PLV. ...) used in a function is bound where it is used.

The add-on imports most modules inside functions under short aliases. One used where it was never imported is a
NameError only on the path that reaches it: Move Outside on a mirrored pair raised `name 'S' is not defined` for days
because no test pressed it on a pair. This reads the source only (no Blender)."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALIAS = re.compile(r'^[A-Z][A-Z0-9]{0,3}_?$')


def _bound(node):
    """Names a module or function body binds itself (imports, assignments, defs, arguments, for/with/except)."""
    out = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = node.args
        for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
            if x is not None:
                out.add(x.arg)
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            out |= {(al.asname or al.name).split('.')[0] for al in n.names}
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
            continue                      # their bodies are their own scopes
        elif isinstance(n, ast.Lambda):
            continue
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out |= set(n.names)
        stack.extend(ast.iter_child_nodes(n))
    return out


def unbound_aliases(src, name='<src>'):
    tree = ast.parse(src)
    found = []

    def walk(node, scopes):
        own = scopes + [_bound(node)]
        for n in ast.iter_child_nodes(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                walk(n, own)
            elif isinstance(n, ast.ClassDef):
                walk(n, own)
            else:
                stack = [n]
                while stack:
                    m = stack.pop()
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                        walk(m, own)
                        continue
                    if (isinstance(m, ast.Attribute) and isinstance(m.value, ast.Name)
                            and isinstance(m.value.ctx, ast.Load) and ALIAS.match(m.value.id)
                            and not any(m.value.id in s for s in own)):
                        found.append(f"{name}:{m.lineno}: {m.value.id}.{m.attr}")
                    stack.extend(ast.iter_child_nodes(m))

    walk(tree, [])
    return found


class TestModuleAliases(unittest.TestCase):
    def test_catches_the_missing_import(self):
        src = "def f():\n    from . import placed_view as PV\n    return S.pair_plane(1, 2)\n"
        self.assertEqual(unbound_aliases(src), ["<src>:3: S.pair_plane"])
        ok = "def f():\n    from .core import spec as S\n    def g():\n        return S.x\n    return g\n"
        self.assertEqual(unbound_aliases(ok), [])

    def test_add_on_sources(self):
        bad = []
        for p in sorted(ROOT.rglob('*.py')):
            if 'tests' in p.relative_to(ROOT).parts:
                continue
            bad += unbound_aliases(p.read_text(encoding='utf-8'), str(p.relative_to(ROOT)))
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
