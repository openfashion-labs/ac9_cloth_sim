"""Build every solver kernel module ahead of time (CUBIN, offline ptxas) for several GPU generations.

Why: the solver is normally built as PTX that the driver compiles at run time, for the GPU in the machine.
A kernel over a hardware limit (e.g. more than 48 KB of static shared memory) can pass on one machine and
fail on another, and the development machine only ever exercises its own GPU and its own build path.
Building every module as CUBIN for each target generation runs the same ptxas checks everywhere, so such a
kernel fails here instead of on a user's machine.

  python tests/check_gpu_kernel_budget.py --out <folder> [--arches 75,86,89,120] [--min-modules 100]

Run it with the Python the add-on runs its solver with (the release: Blender's own Python with the bundled
wheels on PYTHONPATH). Writes <out>/receipt.json and exits 1 on any failure.

Covered: every Warp module the solver files define (found by scanning solver/ for kernels), at the module's
own block size, plus the launches that use a different block size (VARIANTS). Not covered, and left to the
release Dress runs: generic kernels without overloads (built only at run time), and the kernels defined in
the body of solver/newton_vbd.py after its prewarm point. This compiles; it does not run anything on the
target GPUs.
"""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True)
parser.add_argument('--arches', default='75,86,89,120')
parser.add_argument('--min-modules', type=int, default=100,
                    help='fewer compiled modules than this means the scan or an import broke')
parser.add_argument('--root', default='',
                    help='the add-on folder to check (default: the one this script is in)')
parser.add_argument('--break-import', default='',
                    help='self-test: a solver module name to make fail on import (the check must then fail)')
args = parser.parse_args()
ROOT = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
SOLVER = ROOT / 'solver'
OUT = Path(args.out).resolve()
shutil.rmtree(OUT, ignore_errors=True)       # never reuse an earlier run's artifacts
OUT.mkdir(parents=True)
ARCHES = [int(a) for a in args.arches.split(',') if a.strip()]

# launches whose block size differs from their module's default: (python module, kernel, block size).
# A module is compiled once per block size it is launched with, so each of these is a separate build.
VARIANTS = [
    ('newton._src.solvers.vbd.solver_vbd', 'solve_elasticity_tile', 16),
    ('native_experimental_packed', 'solve_elasticity_packed', 32),        # module='unique': its own module
    ('native_experimental_small_tiles', 'solve_elasticity_width8', 32),   # the narrow colors (ac9_elasticity_narrow)
    ('native_experimental_sparse_contacts', None, 128),
]
# every launch in solver/ that passes a block size (file, kernel expression): a new one fails the check until it is
# looked at and added to VARIANTS (a launch at another size builds another variant of its module)
KNOWN_LAUNCHES = {
    ('native_experimental_sparse_contacts.py', 'accumulate'),
    ('native_experimental_sparse_contacts.py', 'accumulate_planar'),
    ('newton_patch/solver_vbd.py', 'solve_elasticity_tile if packed_elasticity is None else packed_elasticity'),
    ('newton_patch/solver_vbd.py', 'ac9_layer_recovery_candidates'),
}
# modules that must be among the compiled ones (a scan that misses them is broken)
REQUIRED = ['newton._src.solvers.vbd.particle_vbd_kernels', 'layer_crossings', 'ac9_layer_recovery_candidates']

report = dict(root=str(ROOT), arches=ARCHES, imports={}, modules=[], variants=[], failures=[], notes=[])


def save():
    (OUT / 'receipt.json').write_text(json.dumps(report, indent=1), encoding='utf8')


def fail(what, detail=''):
    report['failures'].append(dict(what=what, detail=detail[-3000:]))
    save()


# 1. the prewarm import of the solver (the same modules a run loads before its first step)
sys.path.insert(0, str(SOLVER))
cfg = OUT / 'bootstrap.json'
cfg.write_text('{"membrane":"bw"}')
sys.argv = [str(SOLVER / 'newton_vbd.py'), '', '', str(cfg)]
scope = dict(__file__=sys.argv[0], __name__='__main__', _AC9_PREWARM=True)
try:
    exec(compile(Path(sys.argv[0]).read_text(encoding='utf8'), sys.argv[0], 'exec'), scope)
except SystemExit:
    pass
import warp as wp                              # noqa: E402
import warp._src.context as C                  # noqa: E402
wp.config.kernel_cache_dir = str(OUT / 'cache')
wp.init()
report.update(warp=wp.__version__, toolkit=wp.get_cuda_toolkit_version(), driver=wp.get_cuda_driver_version())
# the press-time driver check (core/driver_check.py) compares the driver with the bundled CUDA written there
if (ROOT / 'core' / 'driver_check.py').exists():    # (an add-on from before the check has none: noted, not failed)
    _dc = importlib.util.spec_from_file_location('driver_check', ROOT / 'core' / 'driver_check.py')
    _DC = importlib.util.module_from_spec(_dc)
    _dc.loader.exec_module(_DC)
    if tuple(_DC.BUNDLED_CUDA) != tuple(report['toolkit']):
        fail(f'core/driver_check.py BUNDLED_CUDA {_DC.BUNDLED_CUDA} is not the bundled Warp\'s CUDA '
             f'{report["toolkit"]}')
else:
    report['notes'].append('core/driver_check.py not in this add-on: the bundled CUDA was not compared')

# 2. every solver file that defines a kernel (scanned, not listed by hand)
KERNEL = re.compile(r'@wp\.kernel|wp\.kernel\(')
for p in sorted(SOLVER.rglob('*.py')):
    rel = p.relative_to(SOLVER)
    if rel.parts[0] in ('newton_patch', '__pycache__') or rel.name in ('newton_vbd.py', '__init__.py'):
        continue                               # newton_patch is imported as newton's modules by the prewarm
    if not KERNEL.search(p.read_text(encoding='utf8', errors='replace')):
        continue
    name = '.'.join(rel.with_suffix('').parts)
    try:
        if name == args.break_import:
            raise ImportError('broken on purpose (--break-import)')
        importlib.import_module(name)
        report['imports'][name] = 'ok'
    except BaseException:
        report['imports'][name] = 'failed'
        fail('import ' + name, traceback.format_exc())

# 3. the Warp modules now defined (the registry and every kernel object, as some modules are unique per kernel)
import gc                                      # noqa: E402
mods = {id(m): m for m in C.user_modules.values()}
for o in gc.get_objects():
    try:
        if isinstance(o, C.Kernel) and o.module is not None:
            mods[id(o.module)] = o.module
    except BaseException:
        pass


def build(m, arch, block_dim, tag):
    t = time.perf_counter()
    d = OUT / 'build' / tag
    try:
        if block_dim is None:
            wp.compile_aot_module(m, arch=arch, use_ptx=False, module_dir=d)
        else:
            m._compile(None, d, output_arch=arch, use_ptx=False,
                       options=m.resolve_options(wp.config, block_dim=block_dim))
        return dict(ok=True, s=round(time.perf_counter() - t, 1))
    except BaseException as e:
        msg = traceback.format_exc()
        if 'No compilable kernels' in str(e):
            return dict(ok=None, s=0.0)        # generic kernels only: built at run time, not here
        return dict(ok=False, s=round(time.perf_counter() - t, 1), error=msg[-3000:])


compiled = set()
for i, m in enumerate(sorted(mods.values(), key=lambda m: m.name)):
    if m.name.startswith('warp.') and 'newton' not in m.name:
        continue                               # Warp's own modules
    try:
        live = m._get_live_kernels()
    except BaseException:
        live = None
    if live is not None and len(live) == 0:
        continue
    row = dict(module=m.name, block_dim=m.options.get('block_dim'), arches={})
    for arch in ARCHES:
        r = build(m, arch, None, f'm{i:03d}_{arch}')
        row['arches'][arch] = r
        if r['ok'] is False:
            fail(f'{m.name} sm_{arch}', r['error'])
        elif r['ok']:
            compiled.add(m.name)
    report['modules'].append(row)
    save()

# 4. the launches at another block size
for pymod, kname, bd in VARIANTS:
    try:
        pm = importlib.import_module(pymod)
        m = getattr(pm, kname).module if kname else C.get_module(pm.__name__)
    except BaseException:
        fail(f'variant {pymod}.{kname} not found', traceback.format_exc())
        continue
    row = dict(module=m.name, kernel=kname, block_dim=bd, arches={})
    for arch in ARCHES:
        r = build(m, arch, bd, f'v_{len(report["variants"])}_{arch}')
        row['arches'][arch] = r
        if r['ok'] is False:
            fail(f'{m.name} block_dim {bd} sm_{arch}', r['error'])
    report['variants'].append(row)
    save()

# 4b. the launches with a block size, against KNOWN_LAUNCHES
import ast                                     # noqa: E402
found = set()
for p in sorted(SOLVER.rglob('*.py')):
    if '__pycache__' in p.parts:
        continue
    for c in ast.walk(ast.parse(p.read_text(encoding='utf8'))):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'launch':
            kw = {k.arg: k.value for k in c.keywords}
            if 'block_dim' in kw:
                k = kw.get('kernel') or (c.args[0] if c.args else None)
                found.add((p.relative_to(SOLVER).as_posix(), ast.unparse(k) if k is not None else '?'))
report['launches_with_block_dim'] = sorted(found)
for f in sorted(found - KNOWN_LAUNCHES):
    fail(f'a launch with its own block size is not in KNOWN_LAUNCHES / VARIANTS: {f}')

# 5. self-checks: a scan that found too little passes nothing
report['compiled_modules'] = len(compiled)
report['generic_only'] = sum(1 for r in report['modules'] if all(a['ok'] is None for a in r['arches'].values()))
if len(compiled) < args.min_modules:
    fail(f'only {len(compiled)} modules compiled (expected at least {args.min_modules})')
for req in REQUIRED:
    if not any(n == req or n.startswith(req + '_') or n.endswith('.' + req) or req in n for n in compiled):
        fail(f'required module {req} was not compiled')
report['notes'].append('not covered: generic kernels without overloads; kernels in the body of '
                       'solver/newton_vbd.py after the prewarm point; the solver options of one bootstrap config '
                       '(membrane bw) -- modules a run builds with other options are covered by the release Dress '
                       'and Live runs, not here')
report['passed'] = not report['failures']
save()
print(json.dumps(dict(passed=report['passed'], compiled_modules=len(compiled), generic_only=report['generic_only'],
                      failures=[f['what'] for f in report['failures']]), indent=1))
raise SystemExit(0 if report['passed'] else 1)
