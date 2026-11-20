"""A kernel that runs both a BVH query and a mesh AABB/sphere query keeps two shared-memory stacks per thread.
At 256 threads per block that is 66.5 KB of static shared memory, over the 48 KB every NVIDIA GPU allows: the
whole module then fails to build on some GPUs while it builds on others. Such a kernel must sit in its own
module (module="unique") at a block size of 128 or less, and every launch of it must pass that block size
(wp.launch defaults to 256, not to the module's block size).

  python tests/test_kernel_shared_stack.py     (no GPU, no Warp: reads the source)
"""
import ast
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / 'solver'
MAX_BLOCK = 128
BVH = re.compile(r'\bwp\.bvh_query_(aabb|ray)\b')
MESH = re.compile(r'\bwp\.mesh_query_(aabb|sphere)\b')

files = {p: p.read_text(encoding='utf8') for p in SOLVER.rglob('*.py') if '__pycache__' not in p.parts}
trees = {p: ast.parse(s, str(p)) for p, s in files.items()}

# module-level integer constants, by name (block sizes are named constants shared between files)
consts = {}
for t in trees.values():
    for n in t.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, int):
            for tg in n.targets:
                if isinstance(tg, ast.Name):
                    consts[tg.id] = n.value.value


def value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def kernel_decorator(fn):
    for d in fn.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Attribute) and f.attr == 'kernel':
            return d
    return None


problems, found = [], []
for p, t in trees.items():
    for fn in ast.walk(t):
        if not isinstance(fn, ast.FunctionDef):
            continue
        dec = kernel_decorator(fn)
        if dec is None:
            continue
        body = ast.get_source_segment(files[p], fn) or ''
        if not (BVH.search(body) and MESH.search(body)):
            continue
        found.append(fn.name)
        where = f'{p.relative_to(ROOT).as_posix()}:{fn.lineno} {fn.name}'
        kw = {k.arg: k.value for k in dec.keywords} if isinstance(dec, ast.Call) else {}
        mod = kw.get('module')
        if not (isinstance(mod, ast.Constant) and mod.value == 'unique'):
            problems.append(f'{where}: not in its own module (module="unique")')
        bd = None
        opts = kw.get('module_options')
        if isinstance(opts, ast.Dict):
            for k, v in zip(opts.keys, opts.values):
                if isinstance(k, ast.Constant) and k.value == 'block_dim':
                    bd = value(v)
        if bd is None or bd > MAX_BLOCK:
            problems.append(f'{where}: module block_dim {bd} (needs <= {MAX_BLOCK})')
        # every launch of it
        launches = 0
        for q, tq in trees.items():
            for c in ast.walk(tq):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'launch'
                        and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == fn.name):
                    launches += 1
                    lb = next((value(k.value) for k in c.keywords if k.arg == 'block_dim'), None)
                    if lb is None or lb > MAX_BLOCK:
                        problems.append(f'{q.relative_to(ROOT).as_posix()}:{c.lineno} launch of {fn.name}: '
                                        f'block_dim {lb} (needs <= {MAX_BLOCK})')
        if launches == 0:
            problems.append(f'{where}: no launch found (the check cannot see how it is launched)')

assert found, 'no kernel with both queries found: the scan is broken or the layer recovery kernel moved'
assert not problems, '\n'.join(problems)
print('ok', found)
sys.exit(0)
