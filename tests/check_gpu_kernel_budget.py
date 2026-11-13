"""Compile the actual modules across SM generations; optional local GPU checks.

Run in the packaged Newton/Warp environment. Output is an evidence JSON, not a
claim that every target architecture was executed on physical hardware.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import traceback

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True)
parser.add_argument('--arches', default='75,86,89,120')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
OUT = Path(args.out).resolve(); OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'solver'))
cfg = OUT / 'bootstrap.json'; cfg.write_text('{"membrane":"bw"}')
sys.argv = [str(ROOT / 'solver/newton_vbd.py'), '', '', str(cfg)]
scope = dict(__file__=sys.argv[0], __name__='__main__', _AC9_PREWARM=True)
try:
    exec(compile(Path(sys.argv[0]).read_text(encoding='utf8'), sys.argv[0], 'exec'), scope)
except SystemExit:
    pass
import warp as wp
wp.config.kernel_cache_dir = str(OUT / 'cache')
wp.init()
K = importlib.import_module('newton._src.solvers.vbd.particle_vbd_kernels')
kernel = K.ac9_layer_recovery_candidates
report = dict(warp=wp.__version__, toolkit=wp.get_cuda_toolkit_version(),
              driver=wp.get_cuda_driver_version(), block=K.AC9_LAYER_RECOVERY_BLOCK_DIM,
              module_default=kernel.module.options['block_dim'], cases=[],
              source={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                      ('solver/newton_patch/particle_vbd_kernels.py', 'solver/newton_patch/solver_vbd.py')})
try:
    assert report['block'] == report['module_default'] == 128
    for arch in map(int, args.arches.split(',')):
        for label, module in [('particles', K), ('recovery', kernel.module)]:
            row = dict(arch=arch, module=label, physical_gpu_run=False)
            t = time.perf_counter()
            try:
                files = wp.compile_aot_module(module, arch=arch, use_ptx=False,
                                             module_dir=OUT / (str(arch)+'_'+label))
                row.update(ok=True, files=[str(p) for p in files])
            except BaseException:
                row.update(ok=False, error=traceback.format_exc())
            row['seconds'] = time.perf_counter()-t
            report['cases'].append(row)
            (OUT/'receipt.json').write_text(json.dumps(report, indent=1), encoding='utf8')
    report['passed'] = all(r['ok'] for r in report['cases'])
except BaseException:
    report.update(passed=False, error=traceback.format_exc())
(OUT/'receipt.json').write_text(json.dumps(report, indent=1), encoding='utf8')
raise SystemExit(0 if report.get('passed') else 1)
