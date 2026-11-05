"""A compilation-unit change must preserve SolverBase option restoration across solvers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def worker(folder):
    import runpy
    root = Path(__file__).resolve().parents[1]
    config = folder / 'config.json'
    config.write_text(json.dumps({'membrane': 'bw', 'bw_hess_reg': 1.0}))
    os.environ['WARP_CACHE_PATH'] = str(folder / 'warp')
    os.environ['CUDA_CACHE_PATH'] = str(folder / 'cuda')
    os.environ['AC9_LIFECYCLE_DIR'] = '0'
    code = root / 'solver/newton_vbd.py'
    sys.path.insert(0, str(code.parent))
    sys.argv = [str(code), '', '', str(config)]
    try:
        runpy.run_path(str(code), run_name='__main__', init_globals={'_AC9_PREWARM': True})
    except SystemExit:
        pass
    import warp as wp
    import newton
    modes = list(wp.DeterministicMode)
    first = modes[0]
    second = next(mode for mode in modes if mode != first)
    with wp.ScopedDevice('cpu'):
        builder = newton.ModelBuilder()
        builder.add_cloth_mesh(
            vertices=[wp.vec3(0, 0, 0), wp.vec3(.01, 0, 0), wp.vec3(0, .01, 0), wp.vec3(.01, .01, 0)],
            indices=[0, 1, 2, 1, 3, 2], pos=wp.vec3(0, 0, 0), rot=wp.quat_identity(), scale=1.,
            vel=wp.vec3(0, 0, 0), density=.14, tri_ke=1000., tri_ka=1000., tri_kd=0., edge_ke=1.,
            edge_kd=0., particle_radius=.002)
        builder.color(include_bending=True)
        model = builder.finalize(device='cpu')
        a = newton.solvers.SolverVBD(model, iterations=1, particle_enable_self_contact=False, deterministic=first)
        b = newton.solvers.SolverVBD(model, iterations=1, particle_enable_self_contact=False, deterministic=second)
        kernels = sys.modules['newton._src.solvers.vbd.particle_vbd_kernels']
        rows = []
        for solver, expected in [(a, first), (b, second), (a, first)]:
            solver._apply_module_options()
            parent = wp.get_module_options(kernels)
            tile = wp.get_module_options(kernels.solve_elasticity_tile.module)
            rows.append({
                'matched': parent['deterministic'] == tile['deterministic'] == expected,
                'registered': kernels.solve_elasticity_tile.module in solver._module_options,
                'backward': tile['enable_backward'],
            })
        (folder / 'result.json').write_text(json.dumps(rows))


class CompilationOptionsTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('warp') and importlib.util.find_spec('newton'),
                         'Newton/Warp are required for the runtime option contract')
    def test_solver_switch_restores_parent_and_tile_options(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', name],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads((folder / 'result.json').read_text())
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row['matched'] and row['registered'] and not row['backward'] for row in rows))


if __name__ == '__main__':
    if sys.argv[1:2] == ['--worker']:
        worker(Path(sys.argv[2]))
    else:
        unittest.main()
