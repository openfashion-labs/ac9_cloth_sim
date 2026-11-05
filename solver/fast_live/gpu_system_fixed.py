# SPDX-License-Identifier: GPL-3.0-or-later
"""StitchedGpuSystem with a fixed block structure and a cheaper CG (Accurate stage C,
docs/design/DESIGN_S3_accurate_rebuild_2026-11-03.md).

Same matrix, right-hand side and acceptance as gpu_system_substep_budget.py (the reference):
- The block structure (membrane 9, hinge 16, spring 4 blocks per element, plus the diagonal) depends only on the
  topology, which the correction already requires to stay fixed. It is built once; afterwards the triplets are summed
  into the existing blocks in a fixed order (no sort per solve, no atomics). A degenerate hinge writes zero blocks,
  here at its own rows and columns instead of at (0, 0): the same matrix.
- CG modes (AC9_TEST_CG_MODE): 'f64' (default) as the reference; 'f32' solves in float32; 'mixed' refines in float64:
  r = b - A x in float64, a float32 CG on A d = r, x += d, until the float64 residual meets the same relative tolerance.
  All modes start from the previous correction's solution and check convergence every 50 iterations. The accepted
  solution always passes the float64 true-residual check of the reference.
  Measured on a test T-shirt: float32 rounding of this stiff matrix alone leaves about 2.5e-3 relative error, so 'f32'
  fails the float64 check at 1e-3 and 'mixed' needs 2-3 rounds; 'f64' with the warm start was fastest at both 1e-3 and
  1e-7. The other two stay as test switches.
"""
import os
import numpy as np
import warp as wp
from warp.sparse import bsr_from_triplets, bsr_mv
from warp.optim.linear import cg
from .gpu_system_substep_budget import StitchedGpuSystem
from .gpu_assembly_pants import hinges, inertia
from .gpu_block_pack import pack_projected
from .spring_terms import assemble as springs
from .spring_cpu_terms import validate_springs


@wp.kernel
def gather_blocks(values: wp.array(dtype=wp.mat33d), order: wp.array(dtype=int), start: wp.array(dtype=int),
                  out: wp.array(dtype=wp.mat33d)):
    b = wp.tid()
    total = wp.mat33d(wp.float64(0.0))
    for k in range(start[b], start[b + 1]):
        total = total + values[order[k]]
    out[b] = total


@wp.kernel
def invert_diagonal(values: wp.array(dtype=wp.mat33d), diagonal: wp.array(dtype=int), out: wp.array(dtype=wp.mat33d)):
    i = wp.tid()
    out[i] = wp.inverse(values[diagonal[i]])


@wp.kernel
def to_f32_blocks(a: wp.array(dtype=wp.mat33d), out: wp.array(dtype=wp.mat33f)):
    i = wp.tid()
    out[i] = wp.mat33f(a[i])


@wp.kernel
def to_f32_vec(a: wp.array(dtype=wp.vec3d), out: wp.array(dtype=wp.vec3f)):
    i = wp.tid()
    out[i] = wp.vec3f(a[i])


@wp.kernel
def add_f32(x: wp.array(dtype=wp.vec3d), d: wp.array(dtype=wp.vec3f)):
    i = wp.tid()
    x[i] = x[i] + wp.vec3d(d[i])


_TIMING = bool(os.environ.get('AC9_TEST_STAGE_TIMING'))


class FixedStitchedGpuSystem(StitchedGpuSystem):
    def _t(self, name):
        if not _TIMING: return
        import time
        wp.synchronize(); now = time.perf_counter()
        if getattr(self, '_t0', None) is not None: self.timing[name] = self.timing.get(name, 0.) + (now - self._t0) * 1000
        self._t0 = now
    INNER_TOL = float(os.environ.get('AC9_TEST_CG_INNER', 1e-3))
    CHECK_EVERY = 50
    MAX_REFINEMENTS = 12

    def __init__(self, model, bend, device, *, maxiter=3000, tol=1e-7):
        super().__init__(model, bend, device, maxiter=maxiter, tol=tol)
        self.mode = os.environ.get('AC9_TEST_CG_MODE', 'f64')
        if self.mode not in ('f64', 'f32', 'mixed'): raise ValueError('Unknown CG mode')
        self._build_structure(model, bend)
        self.previous = None

    def _build_structure(self, model, bend):
        n = self.n; dev = self.device
        tri = np.asarray(model['tri']).reshape(-1, 3); edge = np.asarray(bend['edge']).reshape(-1, 4)
        springs_ = np.asarray(self.spring_indices_cpu).reshape(-1, 2)
        r = [np.repeat(tri, 3, axis=1).ravel(), np.tile(tri, (1, 3)).ravel()]
        e = np.where(edge < 0, 0, edge)
        bad = np.any(edge < 0, axis=1)
        hr = np.repeat(e, 4, axis=1); hc = np.tile(e, (1, 4)); hr[bad] = 0; hc[bad] = 0
        sr = np.repeat(springs_, 2, axis=1).ravel(); sc = np.tile(springs_, (1, 2)).ravel()
        ii = np.arange(n)
        rows = np.concatenate([r[0], hr.ravel(), sr, ii]).astype(np.int64)
        cols = np.concatenate([r[1], hc.ravel(), sc, ii]).astype(np.int64)
        if len(rows) != self.rows.shape[0]: raise ValueError('Fixed structure size differs from the assembly')
        key = rows * n + cols
        # a hinge with a missing vertex (open boundary) always writes zero blocks (gpu_assembly_pants.hinges): left out of
        # the sums, or all of them would pile onto block (0, 0) and one thread would add them serially (15 ms measured)
        keep = np.ones(len(rows), bool); keep[9 * len(tri):9 * len(tri) + 16 * len(edge)] = ~np.repeat(bad, 16)
        block_keys = np.unique(key[keep])
        block = np.searchsorted(block_keys, key)
        kept = np.flatnonzero(keep)
        order = kept[np.argsort(block[kept], kind='stable')].astype(np.int32)
        start = np.concatenate([[0], np.cumsum(np.bincount(block[kept], minlength=len(block_keys)))]).astype(np.int32)
        self.structure_info = {'blocks': int(len(block_keys)), 'triplets': int(len(rows)), 'open_hinges': int(bad.sum()), 'max_contributors': int(np.diff(start).max())}
        self.nb = len(block_keys)
        self.order = wp.array(order, dtype=int, device=dev); self.start = wp.array(start, dtype=int, device=dev)
        self.diagonal = wp.array(np.searchsorted(block_keys, ii * n + ii).astype(np.int32), dtype=int, device=dev)
        brow = (block_keys // n).astype(np.int32); bcol = (block_keys % n).astype(np.int32)
        R = wp.array(brow, dtype=int, device=dev); Cc = wp.array(bcol, dtype=int, device=dev)
        self.A = bsr_from_triplets(n, n, R, Cc, wp.zeros(self.nb, dtype=wp.mat33d, device=dev), prune_numerical_zeros=False)
        self.A.nnz_sync()
        if self.A.nnz != self.nb or not np.array_equal(self.A.columns.numpy()[:self.nb], bcol): raise RuntimeError('Fixed BSR structure mismatch')
        ids = wp.array(ii.astype(np.int32), dtype=int, device=dev)
        self.M = bsr_from_triplets(n, n, ids, ids, wp.zeros(n, dtype=wp.mat33d, device=dev), prune_numerical_zeros=False); self.M.nnz_sync()
        if self.mode != 'f64':
            self.A32 = bsr_from_triplets(n, n, R, Cc, wp.zeros(self.nb, dtype=wp.mat33f, device=dev), prune_numerical_zeros=False); self.A32.nnz_sync()
            self.M32 = bsr_from_triplets(n, n, ids, ids, wp.zeros(n, dtype=wp.mat33f, device=dev), prune_numerical_zeros=False); self.M32.nnz_sync()
            self.r64 = wp.zeros(n, dtype=wp.vec3d, device=dev); self.r32 = wp.zeros(n, dtype=wp.vec3f, device=dev)
            self.d32 = wp.zeros(n, dtype=wp.vec3f, device=dev)
        self.prepared64 = None; self.prepared32 = None

    def solve(self, q, hc, residual, dt, spring_data):
        indices, rest, ke, kd, prev = spring_data
        validate_springs(self.n, indices, rest, ke, kd)
        if not np.array_equal(indices, self.spring_indices_cpu): raise ValueError('Spring topology changed inside fixed model')
        dev = self.device; self.timing = {}; self._t0 = None; self._t('start')
        self.q.assign(q); self.contact.assign(hc); self.prev.assign(prev); self.rest.assign(rest); self.ke.assign(ke); self.kd.assign(kd); self.dummy_force.zero_()
        args = [self.rows, self.cols, self.values]; so = 9 * self.nt + 16 * self.ne
        self._t('upload'); element, _, self.last_projection = self.projector.assemble_device(q, prev, dt); self._t('projector')
        self.pack_invalid.zero_()
        wp.launch(pack_projected, dim=9 * self.nt, inputs=[element, self.tri, self.mass], outputs=args + [self.pack_invalid], device=dev)
        wp.launch(hinges, dim=self.ne, inputs=[self.q, self.mass, self.edge, self.length, self.properties, self.bf, dt, 9 * self.nt], outputs=args, device=dev)
        wp.launch(springs, dim=self.ns, inputs=[self.q, self.prev, self.mass, self.spring_indices, self.rest, self.ke, self.kd, dt, so], outputs=args + [self.dummy_force], device=dev)
        wp.launch(inertia, dim=self.n, inputs=[self.mass, self.contact, dt, so + 4 * self.ns], outputs=args, device=dev)
        if int(self.pack_invalid.numpy()[0]): raise ValueError('Nonfinite GPU projection')
        self._t('element_kernels')
        A = self.A; M = self.M
        wp.launch(gather_blocks, dim=self.nb, inputs=[self.values, self.order, self.start], outputs=[A.values], device=dev)
        self._t('gather'); wp.launch(invert_diagonal, dim=self.n, inputs=[A.values, self.diagonal], outputs=[M.values], device=dev)
        self._t('gather_invert'); r = residual.copy(); r[~self.active] = 0; self.rhs.assign(r)
        if self.previous is not None: self.x.assign(self.previous)
        else: self.x.zero_()
        b_norm = float(np.linalg.norm(r)); tolerance = max(self.linear_tol * b_norm, 1e-12)
        if self.mode == 'f64':
            if self.prepared64 is None: self.prepared64 = cg(A, self.rhs, self.x, M=M, tol=self.linear_tol, atol=1e-12,   # test switches as in bound_reduced
                maxiter=int(os.environ.get('AC9_TEST_CG_MAXITER') or self.linear_maxiter), check_every=int(os.environ.get('AC9_TEST_CG_CHECK') or self.CHECK_EVERY), run=False)
            it, err, _ = self.prepared64(A=A, b=self.rhs, x=self.x, M=M); it = int(it); rounds = 1
        else:
            wp.launch(to_f32_blocks, dim=self.nb, inputs=[A.values], outputs=[self.A32.values], device=dev)
            wp.launch(to_f32_blocks, dim=self.n, inputs=[M.values], outputs=[self.M32.values], device=dev)
            self._t('rhs_and_f32'); inner = self.linear_tol if self.mode == 'f32' else self.INNER_TOL
            if self.prepared32 is None: self.prepared32 = cg(self.A32, self.r32, self.d32, M=self.M32, tol=inner, atol=0.0, maxiter=self.linear_maxiter, check_every=self.CHECK_EVERY, run=False)
            it = 0; rounds = 0
            for rounds in range(1, self.MAX_REFINEMENTS + 1):
                wp.copy(self.r64, self.rhs); bsr_mv(A, self.x, self.r64, alpha=-1.0, beta=1.0)
                left = float(np.linalg.norm(self.r64.numpy())); self._t('residual64')
                if left <= tolerance: rounds -= 1; break
                # each round only needs what is left: half the target over the current residual (not below INNER_TOL,
                # about what float32 resolves); the float64 residual above decides when to stop
                if self.mode == 'mixed': self.prepared32._tol = min(.5, max(self.INNER_TOL, .5 * tolerance / left))
                wp.launch(to_f32_vec, dim=self.n, inputs=[self.r64], outputs=[self.r32], device=dev); self.d32.zero_()
                k, _, _ = self.prepared32(A=self.A32, b=self.r32, x=self.d32, M=self.M32); it += int(k); self._t('cg32')
                wp.launch(add_f32, dim=self.n, inputs=[self.x, self.d32], device=dev)
                if self.mode == 'f32': break
        self._t('cg_loop')
        if not hasattr(self, 'true_r'): self.true_r = wp.zeros_like(self.rhs)
        wp.copy(self.true_r, self.rhs); bsr_mv(A, self.x, self.true_r, alpha=-1.0, beta=1.0); true_residual = float(np.linalg.norm(self.true_r.numpy()))
        self._t('true_residual')
        self.last_linear = {'timing_ms': self.timing if _TIMING else None, 'iterations': it, 'refinements': rounds, 'mode': self.mode, 'residual': true_residual, 'tolerance': tolerance,
                            'relative_target': self.linear_tol, 'max_iterations': self.linear_maxiter, 'true_residual': true_residual,
                            'true_within_tolerance': bool(true_residual <= tolerance), 'warm_start': self.previous is not None}
        # under the test cap (AC9_TEST_CG_MAXITER) an unfinished solve is an inexact step: kept when it reduced the residual
        # (the line search still checks the energy); without the cap an unfinished solve stops as before
        capped = bool(os.environ.get('AC9_TEST_CG_MAXITER')) and np.isfinite(true_residual) and true_residual < b_norm
        if not np.isfinite(true_residual) or (true_residual > tolerance and not capped): raise RuntimeError(f'Fixed GPU PCG failed: {self.last_linear}')
        solution = self.x.numpy()
        if not np.isfinite(solution).all(): raise RuntimeError('Fixed GPU correction nonfinite')
        self.previous = solution.copy()
        return solution, it, true_residual
