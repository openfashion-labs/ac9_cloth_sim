# SPDX-License-Identifier: GPL-3.0-or-later
"""Run several solver stages in ONE process: newton_vbd.py once per stage folder, in order.

Starting Python, importing warp + newton and loading the compiled kernels cost a few seconds per stage when
every stage was its own process (trousers: 20 s of solving took 35 s end to end). Here they are paid once.
A stage that fails or is stopped ends the run; each stage folder keeps its own stdout, progress and run_log.

Usage: python run_stages.py <scene.idx.npz> <avatar.npz> <stage dir> [<stage dir> ...]
"""
import contextlib
import gc
import json
import os
import runpy
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
from lifecycle_trace import mark as _trace, directory as _trace_dir
_trace('runner_start', argv=sys.argv)


def _end(code):
    """Leave without Python's and CUDA's teardown (0.7-0.8 s after the last file was written, which the add-on waited
    for on every Stop and every end, sim_latency 2026-10-20): every output file is closed by now (gc for any left to
    the collector), so nothing is lost."""
    _trace('exit_begin', code=code)
    gc.collect()
    _trace('exit_gc_done', code=code)
    sys.stdout.flush()
    sys.stderr.flush()
    _trace('exit', code=code)
    os._exit(code)


if sys.argv[1:2] == ['--standby']:
    # Started ahead of the next Simulate (dress.py, sim_latency 2026-10-20): Python, numpy, warp and the patched newton
    # are imported now, with the compile-time switches of <config.json> (the membrane; the NEWTON_VBD_* environment
    # this process was started with), and the GPU context made, while nobody waits. Then one line on stdin:
    # {"args": [<scene.idx.npz>, <avatar.npz>, <stage dir>...], "log": <path>} runs those stages as below; an empty
    # line or a closed stdin (the add-on gone) ends the process.
    _nvbd = os.path.join(HERE, 'newton_vbd.py')
    from preload_status import write as _preload_write
    _preload_path = os.environ.get('AC9_PRELOAD_STATUS', '')
    _preload_done, _preload_phase = 0, 'imports'
    def _preload_progress(done, phase, state='loading', error=''):
        global _preload_done, _preload_phase
        _preload_done, _preload_phase = done, phase
        _preload_write(_preload_path, done, phase, state, error)
    # Report import/context failures too; process existence alone is never readiness.
    def _preload_exception(kind, value, tb):
        _preload_progress(_preload_done, _preload_phase, 'failed', str(value))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = _preload_exception
    _preload_progress(0, 'imports')
    sys.argv = [_nvbd, '', '', sys.argv[2]]
    _trace('prewarm_import_begin')
    try:
        runpy.run_path(_nvbd, run_name='__main__', init_globals={'_AC9_PREWARM': True})
    except SystemExit:
        pass
    _trace('prewarm_import_end')
    _preload_progress(1, 'device')
    import warp as _wp
    _trace('prewarm_gpu_begin')
    _wp.zeros(1, device=_wp.get_preferred_device())      # the CUDA context (the kernels load in ms from warp's cache)
    _trace('prewarm_gpu_end')
    _preload_progress(2, 'model')
    _trace('prewarm_model_begin')
    try:
        # Newton's modules a stage first touches when it builds (ModelBuilder, colouring, finalize, SolverVBD, the
        # collision pipeline) import and define their kernels then (0.7 s on a first stage): a two-triangle cloth goes
        # through the same calls now. Nothing of it is kept; any failure here only leaves that time to the stage.
        import newton as _nt
        _b = _nt.ModelBuilder(gravity=(0.0, 0.0, -9.81))
        _b.add_cloth_mesh(vertices=[_wp.vec3(0.0, 0.0, 0.0), _wp.vec3(0.01, 0.0, 0.0), _wp.vec3(0.0, 0.01, 0.0),
                                    _wp.vec3(0.01, 0.01, 0.0)], indices=[0, 1, 2, 1, 3, 2], pos=_wp.vec3(0.0, 0.0, 0.0),
                          rot=_wp.quat_identity(), scale=1.0, vel=_wp.vec3(0.0, 0.0, 0.0), density=0.14, tri_ke=1.0e3,
                          tri_ka=1.0e3, tri_kd=0.0, edge_ke=1.0, edge_kd=0.0, particle_radius=0.002)
        _b.add_spring(0, 3, 1.0e3, 0.0, 0.0)
        _b.color(include_bending=True)
        _m = _b.finalize()
        _solver = _nt.solvers.SolverVBD(_m, iterations=1, particle_enable_self_contact=True,
                              particle_topological_contact_filter_threshold=2)
        _pipe = _nt.CollisionPipeline(_m)
        _preload_progress(3, 'kernels')
        # The patched solver's optional friction features use empty arrays when off,
        # just as newton_vbd.py initializes them for a real run.
        _solver.ac9_fric_anchor = _wp.zeros(0, dtype=_wp.vec3)
        _solver.ac9_fric_n = _wp.zeros(0, dtype=_wp.vec3)
        _solver.ac9_fric_hit = _wp.zeros(0, dtype=int)
        _solver.ac9_bend_fric = _wp.zeros((0, 3), dtype=float)
        # Compile/load the common execution path on this private four-vertex model.
        # No user garment, scene or simulation state is touched.
        _s0, _s1 = _m.state(), _m.state()
        _contacts = _pipe.contacts()
        _s0.clear_forces()
        _pipe.collide(_s0, _contacts)
        _solver.step(_s0, _s1, _m.control(), _contacts, 1.0 / 600.0)
        _wp.synchronize()
        del _b, _m, _solver, _pipe, _s0, _s1, _contacts
    except Exception as _exc:
        _preload_progress(_preload_done, _preload_phase, 'failed', str(_exc))
        traceback.print_exc()
        _end(1)
    _trace('prewarm_model_end')
    _preload_progress(4, 'ready', 'ready')
    sys.excepthook = sys.__excepthook__
    _trace('ready')
    print('ready', flush=True)
    try:
        _req = json.loads(sys.stdin.readline() or 'null')
    except ValueError:
        _req = None
    _trace('request_received', present=bool(_req))
    if not _req:
        _end(0)
    _fd = os.open(_req['log'], os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    sys.stdout.flush(); sys.stderr.flush()
    os.dup2(_fd, 1); os.dup2(_fd, 2)
    sys.argv = [sys.argv[0]] + _req['args']
idx_f, av_f, *stage_dirs = sys.argv[1:]
for sd in stage_dirs:
    _trace('stage_begin', stage=sd)
    try:
        with open(os.path.join(sd, 'lifecycle.json'), 'w', encoding='utf-8') as _lf:
            json.dump({'pid': os.getpid(), 'ppid': os.getppid(), 'trace_dir': _trace_dir()}, _lf)
    except OSError:
        pass
    code = 0
    cleanup = []
    with open(os.path.join(sd, 'stdout.txt'), 'w', encoding='utf-8') as log,             contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        sys.argv = [os.path.join(HERE, 'newton_vbd.py'), idx_f, av_f, os.path.join(sd, 'config.json'), sd]
        try:
            runpy.run_path(sys.argv[0], run_name='__main__', init_globals={
                '_AC9_T0': time.perf_counter(), '_AC9_CLEANUP': cleanup.append})
        except SystemExit:
            pass
        except Exception:
            traceback.print_exc()
            code = 1
        finally:
            # _end uses os._exit: release stage-owned CPU resources before it.
            for close in reversed(cleanup):
                try:
                    close()
                except Exception:
                    traceback.print_exc()
                    code = 1
    _trace('stage_end', stage=sd, code=code)
    if code:
        _end(code)
    rl = os.path.join(sd, 'run_log.json')
    if not os.path.exists(rl) or json.load(open(rl)).get('error'):
        _end(2)
_end(0)
