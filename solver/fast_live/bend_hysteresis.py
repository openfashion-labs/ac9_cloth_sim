# SPDX-License-Identifier: GPL-3.0-or-later
"""The fabric's bending hysteresis (cfg bend_fric2, solved 'after' the substep) inside the correction.

Native rewrites each cloth hinge after every substep as one spring k0 (1 + ratio) whose rest angle mixes the elastic
rest and the stuck angle st (newton_vbd _bf2_update). Within one substep that is the elastic spring plus an uncapped
friction spring ratio k0 around st. Native's own iterations move a hinge little in one substep, and the next substep
lets it slip. The correction solves the substep to the end, and against an uncapped spring 100 times the bending
stiffness it held the cloth where it was (a test T-shirt's hem stayed 6 mm up after release; Native with the same 2
substeps and no correction brought it back).

So the correction carries the friction element with its moment capped at k0 theta_y (the slip moment), for the stuck
angle frozen at the substep start: energy H(d) = K d^2 / 2 for |d| <= lim, K lim (|d| - lim / 2) beyond, with
d = theta - st, K = ratio k0 L, lim = theta_y / ratio. Native's spring already holds K d^2 / 2, so this module adds the
difference H(d) - K d^2 / 2 (zero while stuck) to the energy and its negative gradient to the residual. The solve
matrix keeps the friction stiffness for every hinge (correction.py: taking it out for the hinges that slip made the
full step overshoot the ones that stick again). st itself still moves only in Native's update after
the substep.
"""
import numpy as np
import warp as wp

f64 = wp.float64


@wp.func
def _theta_gradient(x0: wp.vec3d, x1: wp.vec3d, x2: wp.vec3d, x3: wp.vec3d, i: int):
    # d theta / d x_i for theta = atan2((n1 x n2) . e, n1 . n2), n1 = (x2-x0) x (x3-x0), n2 = (x3-x1) x (x2-x1),
    # e = x3 - x2 (normalized); the same expressions as gpu_assembly_pants.hinges
    x02 = x2 - x0; x03 = x3 - x0; x13 = x3 - x1; x12 = x2 - x1; e = x3 - x2
    n1 = wp.cross(x02, x03); n2 = wp.cross(x13, x12)
    l1 = wp.length(n1); l2 = wp.length(n2); le = wp.length(e)
    n1 = n1 / l1; n2 = n2 / l2; e = e / le
    ss = wp.dot(wp.cross(n1, n2), e); cc = wp.dot(n1, n2)
    identity = wp.identity(n=3, dtype=f64)
    p1 = (identity - wp.outer(n1, n1)) / l1; p2 = (identity - wp.outer(n2, n2)) / l2
    d1 = wp.mat33d(f64(0.0)); d2 = wp.mat33d(f64(0.0))
    if i == 0:
        d1 = p1 * wp.skew(x3 - x2)
    elif i == 1:
        d2 = p2 * (-wp.skew(x3 - x2))
    elif i == 2:
        d1 = p1 * (-wp.skew(x03)); d2 = p2 * wp.skew(x13)
    else:
        d1 = p1 * wp.skew(x02); d2 = p2 * (-wp.skew(x12))
    ds = wp.transpose(wp.skew(n1) * d2 - wp.skew(n2) * d1) * e
    dc = wp.transpose(d1) * n2 + wp.transpose(d2) * n1
    return ds * cc - dc * ss


@wp.kernel
def capped_terms(q: wp.array(dtype=wp.vec3d), edge: wp.array2d(dtype=int), rows: wp.array(dtype=int),
                 K: wp.array(dtype=f64), lim: wp.array(dtype=f64), st: wp.array(dtype=float), with_force: int,
                 energy: wp.array(dtype=f64), slip: wp.array(dtype=int), force: wp.array(dtype=wp.vec3d)):
    i = wp.tid()
    r = rows[i]
    energy[i] = f64(0.0); slip[i] = 0
    x0 = q[edge[r, 0]]; x1 = q[edge[r, 1]]; x2 = q[edge[r, 2]]; x3 = q[edge[r, 3]]
    n1 = wp.cross(x2 - x0, x3 - x0); n2 = wp.cross(x3 - x1, x2 - x1); e = x3 - x2
    l1 = wp.length(n1); l2 = wp.length(n2); le = wp.length(e)
    if l1 < f64(1.0e-12) or l2 < f64(1.0e-12) or le < f64(1.0e-12):
        return
    n1 = n1 / l1; n2 = n2 / l2; e = e / le
    th = wp.atan2(wp.dot(wp.cross(n1, n2), e), wp.dot(n1, n2))
    pi = f64(3.141592653589793)
    d = th - f64(st[i])
    if d > pi:
        d = d - f64(2.0) * pi
    elif d < -pi:
        d = d + f64(2.0) * pi
    a = wp.abs(d); L = lim[i]
    if a <= L:
        return
    slip[i] = 1
    k = K[i]
    energy[i] = k * L * (a - f64(0.5) * L) - f64(0.5) * k * d * d
    if with_force == 0:
        return
    s = f64(1.0)
    if d < f64(0.0):
        s = f64(-1.0)
    dE = k * (L * s - d)            # d/dtheta of the added energy
    for j in range(4):
        g = _theta_gradient(x0, x1, x2, x3, j)
        wp.atomic_sub(force, edge[r, j], dE * g)


class CappedBendHysteresis:
    """Per substep: freeze st, evaluate the added energy/force at any q, and the slip mask for the solve matrix."""

    def __init__(self, bf2, edge, device):
        self.device = device; self.b = bf2
        self.rows_np = np.asarray(bf2['r_np']); self.n = len(self.rows_np)
        self.rows = bf2['rows']; self.edge = wp.array(np.asarray(edge), dtype=int, device=device)
        self.q = wp.zeros(0, dtype=wp.vec3d, device=device)
        self.energy = wp.zeros(self.n, dtype=f64, device=device); self.slip = wp.zeros(self.n, dtype=int, device=device)
        self.force = None; self.on = False

    def begin(self, length):
        """At the correction's start: whether the hysteresis is on, and its constants for this substep."""
        self.on = bool(self.b['on'].numpy()[0])
        if not self.on: return False
        rat = float(self.b['rat']); k0 = self.b['k0'].numpy().astype(float); thy = self.b['thy'].numpy().astype(float)
        self.K = wp.array(rat * k0 * np.asarray(length, float)[self.rows_np], dtype=f64, device=self.device)
        self.lim = wp.array(thy / rat, dtype=f64, device=self.device)
        self.st = wp.clone(self.b['st'])   # frozen: Native moves it only after the substep
        self.k0 = k0
        return True

    def _run(self, q, with_force):
        q = np.asarray(q, float)
        if self.q.shape[0] != len(q):
            self.q = wp.zeros(len(q), dtype=wp.vec3d, device=self.device); self.force = wp.zeros_like(self.q)
        self.q.assign(q)
        if with_force: self.force.zero_()
        wp.launch(capped_terms, dim=self.n, inputs=[self.q, self.edge, self.rows, self.K, self.lim, self.st, int(with_force)],
                  outputs=[self.energy, self.slip, self.force if with_force else self.q], device=self.device)

    def energy_at(self, q):
        if not self.on: return 0.
        self._run(q, False); return float(np.sum(self.energy.numpy()))

    def force_at(self, q):
        """Added residual force (n, 3) and the slipping hinge rows at q."""
        if not self.on: return None, None
        self._run(q, True)
        return self.force.numpy(), self.rows_np[self.slip.numpy().astype(bool)]
