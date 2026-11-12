# SPDX-License-Identifier: GPL-3.0-or-later
"""The correction's Newton step with Fold Layers' dependent points eliminated (test switch AC9_TEST_BOUND_REDUCED).

Native carries each dependent point's load to its support triangle (bound_constraints.transfer_bound: J.T f, J.T H J).
Without this, the correction leaves the dependent points out of the solve: their mass is 0, so their forces (the
folded side's membrane, its cloth contact, its stitches) are dropped and its rows are empty, and the step solves a
different problem than Native's iterations (a still test shirt with a one-layer placket slid
sideways).

Here the step is taken in the reduced coordinates x = P y, with P the identity on the free points and the binding's
position Jacobian (at the correction's start) on the dependent points: P.T A P dy = P.T r. Unlike transfer_bound, the
cross terms between supports through a dependent point are kept (the full pull-back of the linearized system).
The dependent points are evaluated with a negligible mass (EPS_MASS) so their forces and Hessian blocks exist.
"""
import os
import numpy as np
import warp as wp
from warp.sparse import (bsr_from_triplets, bsr_mm, bsr_mm_work_arrays, bsr_transposed, bsr_diag,
                         bsr_mv, bsr_axpy)  # bsr_axpy: the AC9_TEST_RED_CHECK reference
from warp.optim.linear import cg
try:
    from ..bound_constraints import binding_point
except ImportError:
    from bound_constraints import binding_point

EPS_MASS = 1.0e-12   # kg: inertia force eps/dt^2 * 1 mm ~ 1e-11 N at 120 substeps per second


@wp.kernel
def jacobian_blocks(vertices: wp.array[int], triangles: wp.array[wp.vec3i], weights: wp.array[wp.vec3],
                    offsets: wp.array[wp.vec3], positions: wp.array[wp.vec3], out: wp.array[wp.mat33d]):
    i = wp.tid()
    t = triangles[i]
    p, j0, j1, j2 = binding_point(positions[t[0]], positions[t[1]], positions[t[2]], weights[i], offsets[i])
    out[3 * i + 0] = wp.mat33d(j0)
    out[3 * i + 1] = wp.mat33d(j1)
    out[3 * i + 2] = wp.mat33d(j2)


@wp.kernel
def scatter_blocks(src: wp.array[wp.mat33d], slot: wp.array[int], dst: wp.array[wp.mat33d]):
    i = wp.tid()
    dst[slot[i]] = src[i]


@wp.kernel
def scatter_transposed(src: wp.array[wp.mat33d], slot: wp.array[int], dst: wp.array[wp.mat33d]):
    i = wp.tid()
    dst[slot[i]] = wp.transpose(src[i])


@wp.kernel
def invert_diag(values: wp.array[wp.mat33d], slot: wp.array[int], mask: wp.array[int], out: wp.array[wp.mat33d]):
    i = wp.tid()
    if slot[i] < 0 or mask[i] != 0:
        out[i] = wp.identity(3, wp.float64)
    else:
        out[i] = wp.inverse(values[slot[i]])


@wp.kernel
def zero_masked(mask: wp.array[int], v: wp.array[wp.vec3d]):
    i = wp.tid()
    if mask[i] != 0:
        v[i] = wp.vec3d(0.0)


class ReducedBound:
    def __init__(self, bound, n, device):
        self.bound = bound; self.n = n; self.device = device
        if bound is None:   # no dependent points: P is the identity (used for the active-set re-solve, AC9_TEST_SC_ACTIVESET)
            self.fv = np.zeros(0, np.int32); self.ft = np.zeros((0, 3), np.int32)
        else:
            self.fv = bound.vertices.numpy().astype(np.int32); self.ft = bound.triangles.numpy().astype(np.int32)
        free = np.setdiff1d(np.arange(n, dtype=np.int32), self.fv)
        self.free = free
        self.rows = wp.array(np.r_[free, np.repeat(self.fv, 3)].astype(np.int32), dtype=int, device=device)
        self.cols = wp.array(np.r_[free, self.ft.ravel()].astype(np.int32), dtype=int, device=device)
        self.eye = np.tile(np.eye(3), (len(free), 1, 1))
        self.jac = wp.zeros(3 * len(self.fv), dtype=wp.mat33d, device=device)
        self.pos = wp.zeros(n, dtype=wp.vec3, device=device)
        self.last = None

    def eval_mass(self, mass):
        m = np.array(mass, dtype=np.float64); m[self.fv] = np.where(m[self.fv] > 0, m[self.fv], EPS_MASS); return m

    def solve(self, gs, q, hc, residual, dt, spring_data, tol):
        """gs: FixedStitchedGpuSystem with its mass set by eval_mass. Returns the full change (dependent rows 0)."""
        dev = self.device
        indices, rest, ke, kd, prev = spring_data
        gs.q.assign(q); gs.contact.assign(hc); gs.prev.assign(prev); gs.rest.assign(rest); gs.ke.assign(ke); gs.kd.assign(kd); gs.dummy_force.zero_()
        from .gpu_system_fixed import gather_blocks
        from .gpu_assembly_pants import hinges, inertia
        from .gpu_block_pack import pack_projected
        from .spring_terms import assemble as springs
        args = [gs.rows, gs.cols, gs.values]; so = 9 * gs.nt + 16 * gs.ne
        element, _, gs.last_projection = gs.projector.assemble_device(q, prev, dt)
        gs.pack_invalid.zero_()
        wp.launch(pack_projected, dim=9 * gs.nt, inputs=[element, gs.tri, gs.mass], outputs=args + [gs.pack_invalid], device=dev)
        wp.launch(hinges, dim=gs.ne, inputs=[gs.q, gs.mass, gs.edge, gs.length, gs.properties, gs.bf, dt, 9 * gs.nt], outputs=args, device=dev)
        wp.launch(springs, dim=gs.ns, inputs=[gs.q, gs.prev, gs.mass, gs.spring_indices, gs.rest, gs.ke, gs.kd, dt, so], outputs=args + [gs.dummy_force], device=dev)
        wp.launch(inertia, dim=gs.n, inputs=[gs.mass, gs.contact, dt, so + 4 * gs.ns], outputs=args, device=dev)
        if int(gs.pack_invalid.numpy()[0]): raise ValueError('Nonfinite GPU projection')
        A = gs.A
        wp.launch(gather_blocks, dim=gs.nb, inputs=[gs.values, gs.order, gs.start], outputs=[A.values], device=dev)
        # P at the correction's start (its structure is fixed: the free identity and the bindings)
        self.pos.assign(np.asarray(q, np.float32))
        if len(self.fv): wp.launch(jacobian_blocks, dim=len(self.fv), inputs=[self.bound.vertices, self.bound.triangles, self.bound.weights,
                  self.bound.offsets, self.pos], outputs=[self.jac], device=dev)
        first = not hasattr(self, 'P')
        if first:
            vals = wp.array(np.concatenate([self.eye, self.jac.numpy()]), dtype=wp.mat33d, device=dev)
            self.P = bsr_from_triplets(self.n, self.n, self.rows, self.cols, vals, prune_numerical_zeros=False)
            self.P.nnz_sync()
            # where each triplet landed in P, to refill the Jacobian blocks in place
            r = np.r_[self.free, np.repeat(self.fv, 3)]; c = np.r_[self.free, self.ft.ravel()]
            off = self.P.offsets.numpy(); cols = self.P.columns.numpy()[:self.P.nnz]
            slot = np.array([off[a] + np.nonzero(cols[off[a]:off[a + 1]] == b)[0][0] for a, b in zip(r, c)], np.int32)
            self.jslot = wp.array(slot[len(self.free):], dtype=int, device=dev)
            self.Pt = bsr_transposed(self.P); self.Pt.nnz_sync()
            off = self.Pt.offsets.numpy(); cols = self.Pt.columns.numpy()[:self.Pt.nnz]
            tslot = np.array([off[b] + np.nonzero(cols[off[b]:off[b + 1]] == a)[0][0] for a, b in zip(r[len(self.free):], c[len(self.free):])], np.int32)
            self.tslot = wp.array(tslot, dtype=int, device=dev)
            self.w1 = bsr_mm_work_arrays(); self.w2 = bsr_mm_work_arrays()
            self.AP = bsr_mm(A, self.P, work_arrays=self.w1); self.R = bsr_mm(self.Pt, self.AP, work_arrays=self.w2)
            self.R.nnz_sync()
            # dependent rows and columns are empty in P.T A P (right-hand side 0 there, start 0): the CG stays off them,
            # and their preconditioner block is the identity (slot -1)
            off = self.R.offsets.numpy(); cols = self.R.columns.numpy()[:self.R.nnz]
            def at(a):
                k = np.nonzero(cols[off[a]:off[a + 1]] == a)[0]
                return off[a] + k[0] if len(k) else -1
            dg = np.array([at(a) for a in range(self.n)], np.int32)
            if (dg[self.free] < 0).any(): raise ValueError('Reduced matrix lost a free diagonal block')
            self.rdiag = wp.array(dg, dtype=int, device=dev)
            self.M = bsr_diag(wp.zeros(self.n, dtype=wp.mat33d, device=dev))
            self.full = wp.zeros(self.n, dtype=wp.vec3d, device=dev); self.rhs = wp.zeros_like(self.full)
            self.x = wp.zeros_like(self.full); self.fmask = wp.array(np.isin(np.arange(self.n), self.fv).astype(np.int32), dtype=int, device=dev)
        else:
            if len(self.fv): wp.launch(scatter_blocks, dim=len(self.fv) * 3, inputs=[self.jac, self.jslot], outputs=[self.P.values], device=dev)
            if len(self.fv): wp.launch(scatter_transposed, dim=len(self.fv) * 3, inputs=[self.jac, self.tslot], outputs=[self.Pt.values], device=dev)
            bsr_mm(A, self.P, self.AP, work_arrays=self.w1, reuse_topology=True)
            bsr_mm(self.Pt, self.AP, self.R, work_arrays=self.w2, reuse_topology=True)
        if not first and os.environ.get('AC9_TEST_RED_CHECK'):
            vals = wp.array(np.concatenate([self.eye, self.jac.numpy()]), dtype=wp.mat33d, device=dev)
            P0 = bsr_from_triplets(self.n, self.n, self.rows, self.cols, vals, prune_numerical_zeros=False); Pt0 = bsr_transposed(P0)
            R0 = bsr_mm(Pt0, bsr_mm(A, P0))
            v = wp.array(np.random.default_rng(0).standard_normal((self.n, 3)), dtype=wp.vec3d, device=dev)
            def mv(Mx):
                o = wp.zeros_like(v); bsr_mv(Mx, v, o); return o.numpy()
            open(os.environ['AC9_TEST_RED_CHECK'], 'a').write(f"P {np.abs(mv(P0)-mv(self.P)).max():.3g} Pt {np.abs(mv(Pt0)-mv(self.Pt)).max():.3g} AP {np.abs(mv(bsr_mm(A, P0))-mv(self.AP)).max():.3g} R {np.abs(mv(R0)-mv(self.R)).max():.3g} Rscale {np.abs(mv(R0)).max():.3g}\n")
        # points out of the solve (the dependent points, and any other point with no mass: a frozen or Off piece) keep
        # change 0: right-hand side 0 and an identity preconditioner block there
        out = ~np.asarray(gs.active, bool); out[self.fv] = True
        self.fmask.assign(out.astype(np.int32))
        wp.launch(invert_diag, dim=self.n, inputs=[self.R.values, self.rdiag, self.fmask], outputs=[self.M.values], device=dev)
        self.full.assign(np.asarray(residual, np.float64))
        bsr_mv(self.Pt, self.full, self.rhs, alpha=1.0, beta=0.0)
        wp.launch(zero_masked, dim=self.n, inputs=[self.fmask], outputs=[self.rhs], device=dev)
        rhs = self.rhs; x = self.x; R = self.R
        if self.last is not None:
            warm = self.last.copy()
            if not np.isfinite(warm).all(): raise RuntimeError('Reduced correction warm start nonfinite')
            warm[out] = 0.
            x.assign(warm)
        else: x.zero_()
        r_np = rhs.numpy()
        # The merit's directional derivative is in the same independent coordinates.
        # Keep the physical covector before any optional active-set penalty resolve.
        self.origin_rhs_numpy = r_np.copy()
        if not hasattr(self, 'prepared'):
            # test switches: AC9_TEST_CG_MAXITER caps the iterations (an inexact step; the line search still checks it),
            # AC9_TEST_CG_CHECK sets how often the residual is checked (default 50)
            maxiter = int(os.environ.get('AC9_TEST_CG_MAXITER') or gs.linear_maxiter)
            self.prepared = cg(R, rhs, x, M=self.M, tol=tol, atol=1e-12, maxiter=maxiter, check_every=int(os.environ.get('AC9_TEST_CG_CHECK', '50')), run=False)
        it, err, _ = self.prepared(A=R, b=rhs, x=x, M=self.M)
        y = x.numpy()
        if not np.isfinite(y).all(): raise RuntimeError(f'Reduced correction nonfinite first={first} M={np.isfinite(self.M.values.numpy()).all()} R={np.isfinite(R.values.numpy()[:R.nnz]).all()} rhs={np.isfinite(r_np).all()} it={it}')
        if np.any(y[out]):
            y[out] = 0.; x.assign(y)
        y[self.fv] = 0.
        self.last = y.copy()
        tr = wp.zeros_like(rhs); wp.copy(tr, rhs); bsr_mv(R, x, tr, alpha=-1.0, beta=1.0)
        true_residual = float(np.linalg.norm(tr.numpy())); b = float(np.linalg.norm(r_np))
        gs.last_linear = {'iterations': int(it), 'mode': 'reduced_f64', 'residual': true_residual,
                          'tolerance': max(tol * b, 1e-12), 'true_within_tolerance': bool(true_residual <= max(tol * b, 1e-12) * 1.01),
                          'reduced_dependent': int(len(self.fv))}
        y[self.fv] = 0.
        self.tol = tol
        return y

    def resolve(self, fixed, target):
        """The same step with the points `fixed` held at `target` (their change, e.g. where the cloth-cloth cap left
        them) and the rest solved again (active set, warm-started from the last solution). The hold is a stiff
        diagonal penalty on the fixed rows: kappa = 1e4 x the largest fixed diagonal block."""
        dev = self.device; fixed = np.asarray(fixed, np.int64); fixed = fixed[~np.isin(fixed, self.fv)]
        if not len(fixed): return None
        vals = self.R.values.numpy(); dg = self.rdiag.numpy()[fixed]
        if (dg < 0).any(): return None
        kappa = 1e4 * float(np.abs(vals[dg]).max())
        vals[dg] += kappa * np.eye(3); self.R.values.assign(vals)
        r = self.rhs.numpy(); r[fixed] += kappa * np.asarray(target, float); self.rhs.assign(r)
        wp.launch(invert_diag, dim=self.n, inputs=[self.R.values, self.rdiag, self.fmask], outputs=[self.M.values], device=dev)
        x0 = self.last.copy(); x0[fixed] = target; self.x.assign(x0)
        it, err, _ = self.prepared(A=self.R, b=self.rhs, x=self.x, M=self.M)
        y = self.x.numpy()
        if not np.isfinite(y).all(): return None
        y[self.fv] = 0.; y[fixed] = target
        self.resolve_iterations = int(it)
        return y
