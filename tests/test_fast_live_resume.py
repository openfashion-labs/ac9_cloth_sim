"""Fast Live: stop, then Live again resumes the checkpoint (a CUDA test, a few frames on a synthetic tube).

Run with the solver's Newton/Warp Python environment. Every Fast Live resume with cloth-cloth contact on was once
refused before its first frame (the correction left the cloth-cloth detector on the other state buffer, and the
empty layer arrays a graph capture makes were missing), and no test ran a v2 checkpoint back in. This one does:
the same solver run twice in a subprocess, as the add-on's worker runs it.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np

SOLVER = pathlib.Path(__file__).resolve().parents[1] / 'solver'
sys.path.insert(0, str(SOLVER))


def _cuda():
    try:
        import warp as wp
        wp.init()
        return wp.is_cuda_available()
    except Exception:
        return False


def _panel(nx, ny, step):
    """A flat grid (mm) and its triangles."""
    x, y = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step)
    flat = np.c_[x.ravel(), y.ravel()]
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    return flat, np.r_[np.c_[a, b, c], np.c_[a, c, d]]


def write_tube(folder):
    """Two panels sewn into a tube around a cylinder body, already sewn (every stitch 0 mm)."""
    R, step, nx, ny = 150.0, 10.0, 48, 14                     # mm; each panel half the circumference
    flat0, tri = _panel(nx, ny, step)
    width = (nx - 1) * step
    X, F, fp, flat, offs = [], [], [], [], []
    for p in range(2):
        offs.append(p * nx * ny)
        ang = np.pi * (p + flat0[:, 0] / width)
        X.append(np.c_[R * np.cos(ang), R * np.sin(ang), 1000.0 + flat0[:, 1]])
        flat.append(flat0 + [p * (width + 50.0), 0.0])
        F.append(tri + offs[-1]); fp.append(np.full(len(tri), p))
    X, F, fp, flat = np.vstack(X), np.vstack(F), np.concatenate(fp), np.vstack(flat)
    n = nx * ny
    rows = np.arange(ny) * nx
    # panel 0's end edge to panel 1's start edge, and panel 1's end edge back to panel 0's start edge
    E = np.r_[np.c_[rows + nx - 1, n + rows], np.c_[n + rows + nx - 1, rows]].astype(np.int64)
    np.savez(folder / 'scene.idx.npz', offs=np.asarray(offs, np.int64), E_st=E, fp=fp.astype(np.int64),
             Xclo=X, Xinit=X, flat=flat, F=F.astype(np.int64), E_seam=np.array(['S1'] * len(E)))
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    z = np.linspace(0.8, 1.3, 11)
    co = np.array([[0.13 * np.cos(a), 0.13 * np.sin(a), h] for h in z for a in t])
    m = len(t); tris = []
    for i in range(len(z) - 1):
        for j in range(m):
            a, b, c, d = i * m + j, i * m + (j + 1) % m, (i + 1) * m + (j + 1) % m, (i + 1) * m + j
            tris += [[a, b, c], [a, c, d]]
    # closed ends: the body-side guard needs a closed body
    bottom, top = len(co), len(co) + 1
    top_row = (len(z) - 1) * m
    for j in range(m):
        tris += [[bottom, (j + 1) % m, j], [top, top_row + j, top_row + (j + 1) % m]]
    co = np.r_[co, [[0, 0, z[0]], [0, 0, z[-1]]]]
    # the pattern file Live reads: names, outline counts (nothing interior), no fold lines, no twins
    np.savez(folder / 'pattern.npz', names=np.array(['P0', 'P1']), nb=np.array([n, n]), fold_meta=np.array(['', '']),
             twin_src=np.array([-1, -1]), h_by_piece=np.array([step, step]),
             Lf0=np.full(n, -1, np.int32), Lf1=np.full(n, -1, np.int32))
    np.savez(folder / 'avatar.npz', body_co=co, body_tri=np.asarray(tris, np.int64))


def config(folder, frames, checkpoint=None):
    import fast_live_contract as contract
    cfg = {'fps': 60, 'substeps': 10, 'iterations': 20, 'density': 0.14, 'tri_ke': 3000.0, 'tri_ka': 3000.0,
           'tri_kd': 0.001, 'tri_ks': 29.0, 'edge_ke': 0.0004, 'edge_kd': 4e-08, 'stitch_ke': 1000, 'stitch_kd': 0.0,
           'particle_radius': 0.0015, 'body_gap': 0.002, 'body_mu': 1.0, 'self_margin': 0.0003, 'self_gap': 0.00015,
           'filter_rings': 2, 'rest_from_flat': True, 'membrane': 'bw', 'bw_hess_reg': 1.0, 'init_xv': True,
           'friction_epsilon': 0.001, 'fric_anchor': True, 'bend_fric2': {'kappa_per_m': 41.0, 'ratio': 100.0,
           'start_mm': 2.0}, 'self_contact': True, 'live': True, 'live_every': 2, 'frames': frames,
           'pattern': str(folder / 'pattern.npz'), 'params_file': str(folder / 'params.json'), 'grab_file': str(folder / 'grab.json')}
    cfg = contract.configure(cfg)
    if checkpoint:
        cfg['checkpoint_in'] = str(checkpoint)
    return cfg


def run(folder, tag, frames, checkpoint=None):
    out = folder / tag; out.mkdir()
    cfg_path = folder / (tag + '.json')
    cfg_path.write_text(json.dumps(config(folder, frames, checkpoint)), encoding='utf8')
    # Live runs until stopped: write the stop file the add-on writes, once the progress shows `frames` new frames
    console = open(out / 'console.txt', 'w', encoding='utf8')   # a file, not a pipe: a full pipe stalls the solver
    proc = subprocess.Popen([sys.executable, str(SOLVER / 'newton_vbd.py'), str(folder / 'scene.idx.npz'),
                             str(folder / 'avatar.npz'), str(cfg_path), str(out)],
                            cwd=str(out), stdout=console, stderr=subprocess.STDOUT)
    deadline, first = time.monotonic() + 600, None
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            f = int((out / 'progress.txt').read_text().split()[0])
        except (OSError, ValueError, IndexError):
            f = None
        if f is not None:
            first = f if first is None else first
            if f - first >= frames:
                (out / 'stop').touch()
                break
        time.sleep(0.05)
    try:
        proc.wait(timeout=max(60.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    console.close()
    text = (out / 'console.txt').read_text(encoding='utf8', errors='replace')
    log = out / 'run_log.json'
    if not log.exists():
        raise AssertionError('solver ended without a log:\n' + text[-3000:])
    return json.loads(log.read_text(encoding='utf8'))


@unittest.skipUnless(_cuda(), 'Fast Live needs a CUDA device')
class FastLiveResume(unittest.TestCase):
    def test_stop_then_live_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            write_tube(folder)
            first = run(folder, 'first', 6)
            self.assertIsNone(first.get('error'), 'first run')
            self.assertEqual(first['fast_live']['diagnostics']['fast_from_frame'], 1)   # on the corrected path
            self.assertEqual(first['checkpoint']['status'], 'saved')
            second = run(folder, 'second', 9, checkpoint=first['checkpoint']['path'])
            self.assertIsNone(second.get('error'), 'resumed run')
            self.assertEqual(second['checkpoint_resume'], {'status': 'restored', 'frame': first['checkpoint']['frame']})
            self.assertGreater(len(second['frame_s']), 0)


if __name__ == '__main__':
    unittest.main()
