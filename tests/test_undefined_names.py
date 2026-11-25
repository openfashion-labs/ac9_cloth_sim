"""No name is used that nothing defines (no Blender, no extra package: a scan with the standard library's symtable).

Why: a function was split in two and four names it still used stayed behind in the new function; every Simulate and
Live then stopped at once with NameError, and nothing before the merge ran that line. Python finds such a name only
when the line runs. This test finds it without running anything: a name a function reads that is neither its own,
an enclosing function's, the module's nor a builtin.

It reads names, not strings, so the strings in Blender property annotations are not taken for names. A module that
defines names at run time (globals() writes, star imports) can list them in RUNTIME_NAMES.

Run: python tests/test_undefined_names.py (from the add-on folder; no Blender)."""
import builtins
import os
import symtable
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = ('__pycache__', 'docs', '.github')
# names a module defines at run time, per file (relative path -> names); keep it short and say why
RUNTIME_NAMES = {
    # passed in by the run_path that starts the solver (live_session_worker / prepared_worker init globals)
    'solver/newton_vbd.py': ('_AC9_BUSY_BEGIN', '_AC9_BUSY_END', '_AC9_CLEANUP', '_AC9_LOOP_READY',
                             '_AC9_PREPARED_GATE'),
    # F3: reloaded only when an earlier load left it ("companion_state" in locals())
    '__init__.py': ('companion_state',),
}
MODULE_NAMES = {'__file__', '__name__', '__doc__', '__package__', '__spec__', '__loader__', '__path__',
                '__builtins__', '__annotations__', '__dict__', '__module__', '__qualname__', '__class__'}


def _sources():
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith('.py'):
                p = os.path.join(root, f)
                yield os.path.relpath(p, HERE).replace(os.sep, '/'), p


def _tables(t):
    yield t
    for c in t.get_children():
        yield from _tables(c)


def undefined(src, rel='<src>'):
    """[(name, scope, line)] for the names src reads that nothing defines."""
    if 'globals().update(' in src:
        return []                                    # names put in at run time: not known here (as a star import)
    top = symtable.symtable(src, rel, 'exec')
    defined = set(dir(builtins)) | MODULE_NAMES | set(RUNTIME_NAMES.get(rel, ()))
    for s in top.get_symbols():
        if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_parameter():
            defined.add(s.get_name())
    for t in _tables(top):
        for s in t.get_symbols():
            # a function that writes a module name with `global x` defines it
            if t is not top and s.is_declared_global() and (s.is_assigned() or s.is_imported()):
                defined.add(s.get_name())
    if '*' in (s.get_name() for s in top.get_symbols()):
        return []                                    # a star import: the module's names are not known here
    out = []
    for t in _tables(top):
        for s in t.get_symbols():
            n = s.get_name()
            if n in defined or not s.is_referenced():
                continue
            if t is top:
                bad = not (s.is_assigned() or s.is_imported() or s.is_namespace())
            else:
                # read in a function or class but bound nowhere inside: the name is looked up in the module
                bad = s.is_global() and not s.is_assigned() and not s.is_imported() and not s.is_namespace()
            if bad:
                out.append((n, t.get_name(), t.get_lineno()))
    return out


class UndefinedNames(unittest.TestCase):
    def test_none_in_the_add_on(self):
        found = []
        for rel, p in _sources():
            src = open(p, encoding='utf-8').read()
            try:
                bad = undefined(src, rel)
            except SyntaxError as e:                 # a file that does not compile is its own failure
                found.append(f"{rel}: does not compile: {e}")
                continue
            found += [f"{rel}: '{n}' in {scope} (line {ln})" for n, scope, ln in bad]
        self.assertEqual(found, [], "names used that nothing defines:\n" + "\n".join(found))

    def test_catches_a_name_left_behind(self):
        # the shape of the bug this test is for: a helper took the lines that set `sym`, the caller still reads it
        src = ("def helper(spec):\n    sym = spec.get('symmetry')\n    return sym\n\n"
               "def run(spec):\n    helper(spec)\n    if sym != 'off':\n        return 1\n")
        self.assertEqual([n for n, _s, _l in undefined(src)], ['sym'])

    def test_quiet_on_what_is_defined(self):
        src = ("import os\nfrom math import pi as P\nX = 1\n"
               "def f(a, *b, **c):\n    global Y\n    Y = a\n    def g():\n        return a + X + P + len(b)\n"
               "    return [i for i in range(3)], g, os, c\n"
               "class K:\n    z = 2\n    def m(self):\n        return Y, K, __name__\n"
               "class Props:\n    name: 'bpy.props.StringProperty(name=\"Name\")' = None\n")
        self.assertEqual(undefined(src), [])


if __name__ == '__main__':
    unittest.main()
