# SPDX-License-Identifier: GPL-3.0-or-later
"""FixedProblemEnergy on the GPU in float64 (Accurate stage B, docs/design/DESIGN_S3_accurate_rebuild_2026-11-03.md).

Same terms and formulas as fixed_problem_energy.py (which stays the reference): membrane elastic and metric damping per
triangle, inertia per particle, bending elastic and damping per hinge on Native's float32 chart and activity mask,
springs. The body penalty (a few hundred contacts) stays on the host. Per-element values are summed on the host, so the
result does not depend on the order of GPU atomics. With AC9_TEST_FAST_AUDIT the CPU reference is evaluated too and a
relative difference above 1e-9 stops the run.
"""
import numpy as np, warp as wp
from .fixed_problem_energy import FixedProblemEnergy
from .native_hinge_geometry import angles
from . import audit

f64 = wp.float64


@wp.kernel
def tri_energy(q: wp.array(dtype=wp.vec3d), qp: wp.array(dtype=wp.vec3d), tri: wp.array2d(dtype=int),
               pose: wp.array(dtype=wp.mat22d), mat: wp.array2d(dtype=f64), area: wp.array(dtype=f64),
               dt: f64, out: wp.array2d(dtype=f64)):
    t = wp.tid()
    i = tri[t, 0]; j = tri[t, 1]; k = tri[t, 2]
    P = pose[t]
    e1 = q[j] - q[i]; e2 = q[k] - q[i]
    u = e1 * P[0, 0] + e2 * P[1, 0]; v = e1 * P[0, 1] + e2 * P[1, 1]
    p1 = qp[j] - qp[i]; p2 = qp[k] - qp[i]
    up = p1 * P[0, 0] + p2 * P[1, 0]; vp = p1 * P[0, 1] + p2 * P[1, 1]
    lu = wp.length(u); lv = wp.length(v); uv = wp.dot(u, v)
    one = f64(1.0); half = f64(0.5)
    out[t, 0] = area[t] * half * (mat[t, 0] * (lu - one) * (lu - one) + mat[t, 1] * (lv - one) * (lv - one) + mat[t, 3] * uv * uv)
    c00 = wp.dot(u, u) - wp.dot(up, up); c01 = uv - wp.dot(up, vp); c11 = wp.dot(v, v) - wp.dot(vp, vp)
    out[t, 1] = area[t] * mat[t, 2] / (f64(2.0) * dt) * (c00 * c00 + f64(2.0) * c01 * c01 + c11 * c11)


@wp.kernel
def hinge_energy(q: wp.array(dtype=wp.vec3d), edge: wp.array2d(dtype=int), theta32: wp.array(dtype=float),
                 active: wp.array(dtype=int), rest: wp.array(dtype=f64), ke: wp.array(dtype=f64), kd: wp.array(dtype=f64),
                 prev_theta: wp.array(dtype=f64), prev_theta32: wp.array(dtype=float), prev_active: wp.array(dtype=int),
                 dt: f64, pi32: float, two_pi32: float, two_pi: f64,
                 theta: wp.array(dtype=f64), out: wp.array2d(dtype=f64)):
    t = wp.tid()
    out[t, 0] = f64(0.0); out[t, 1] = f64(0.0); theta[t] = f64(0.0)
    if active[t] == 0:
        return
    a = q[edge[t, 0]]; b = q[edge[t, 1]]; c = q[edge[t, 2]]; d = q[edge[t, 3]]
    n1 = wp.cross(c - a, d - a); n2 = wp.cross(d - b, c - b); e = d - c
    n1 = n1 / wp.length(n1); n2 = n2 / wp.length(n2); e = e / wp.length(e)
    value = wp.atan2(wp.dot(wp.cross(n1, n2), e), wp.dot(n1, n2))
    value = value + two_pi * wp.rint((f64(theta32[t]) - value) / two_pi)
    theta[t] = value
    r = value - rest[t]
    out[t, 0] = f64(0.5) * ke[t] * r * r
    if prev_active[t] == 0:
        return
    d32 = theta32[t] - prev_theta32[t]
    w = d32
    if d32 > pi32:
        w = d32 - two_pi32
    elif d32 < -pi32:
        w = d32 + two_pi32
    delta = value - prev_theta[t]
    delta = delta + two_pi * wp.rint((f64(w) - delta) / two_pi)
    out[t, 1] = f64(0.5) * kd[t] * delta * delta / dt


@wp.kernel
def spring_energy(q: wp.array(dtype=wp.vec3d), qp: wp.array(dtype=wp.vec3d), idx: wp.array2d(dtype=int),
                  rest: wp.array(dtype=f64), ke: wp.array(dtype=f64), kd: wp.array(dtype=f64), dt: f64,
                  out: wp.array(dtype=f64)):
    t = wp.tid()
    i = idx[t, 0]; j = idx[t, 1]
    tiny = f64(1.0e-8)
    length = wp.max(wp.length(q[i] - q[j]), tiny); old = wp.max(wp.length(qp[i] - qp[j]), tiny)
    out[t] = f64(0.5) * ke[t] * (length - rest[t]) * (length - rest[t]) + f64(0.5) * kd[t] * (length - old) * (length - old) / dt


@wp.kernel
def inertia_energy(q: wp.array(dtype=wp.vec3d), y: wp.array(dtype=wp.vec3d), mass: wp.array(dtype=f64), dt: f64,
                   out: wp.array(dtype=f64)):
    i = wp.tid()
    r = q[i] - y[i]
    out[i] = f64(0.5) * mass[i] * wp.dot(r, r) / (dt * dt)


class GpuFixedProblemEnergy(FixedProblemEnergy):
    def __init__(self, model, bend, spring, prev, inertia, dt, radius, margin, device, *, angle_pool=None):
        self.device = device; self.dt = dt; self.model = model; self.bend = bend; self.spring = spring
        self.prev = np.asarray(prev, float); self.inertia = np.asarray(inertia, float)
        self.radius = np.asarray(radius, float); self.margin = float(margin); self.angle_pool = angle_pool
        A = lambda a, t: wp.array(np.ascontiguousarray(a), dtype=t, device=device)
        n = len(prev); ne = len(bend['edge'])
        self.nt = len(model['tri']); self.ne = ne; self.n = n
        self.q = wp.zeros(n, dtype=wp.vec3, device=device); self.q64 = wp.zeros(n, dtype=wp.vec3d, device=device)
        self.qp = A(self.prev, wp.vec3d); self.y = A(self.inertia, wp.vec3d); self.mass = A(model['mass'], f64)
        self.tri = A(model['tri'].reshape(-1, 3), int); self.pose = A(model['poses'].reshape(-1, 2, 2), wp.mat22d)
        self.mat = A(model['materials'], f64); self.area = A(model['areas'], f64)
        self.edge = A(bend['edge'], int); self.rest = A(np.asarray(bend['rest'], float), f64)
        self.ke = A(bend['bending'][:, 0].astype(float) * bend['length'].astype(float), f64)
        self.kd = A(bend['bending'][:, 1].astype(float) * bend['length'].astype(float), f64)
        self.theta32 = wp.zeros(ne, dtype=float, device=device); self.active = wp.zeros(ne, dtype=int, device=device)
        self.theta = wp.zeros(ne, dtype=f64, device=device)
        self.tri_out = wp.zeros((self.nt, 2), dtype=f64, device=device); self.hinge_out = wp.zeros((ne, 2), dtype=f64, device=device)
        self.inertia_out = wp.zeros(n, dtype=f64, device=device)
        indices, rest, ske, skd, sprev = spring
        self.ns = len(indices)
        if self.ns:
            self.sidx = A(np.asarray(indices).reshape(-1, 2), int); self.srest = A(np.asarray(rest, float), f64)
            self.ske = A(np.asarray(ske, float), f64); self.skd = A(np.asarray(skd, float), f64)
            self.sprev = A(np.asarray(sprev, float), wp.vec3d); self.spring_out = wp.zeros(self.ns, dtype=f64, device=device)
        # the previous substep's hinge state: the float32 chart/mask and float64 angle at prev
        self.prev_theta32 = wp.zeros(ne, dtype=float, device=device); self.prev_active = wp.zeros(ne, dtype=int, device=device)
        self.prev_theta = wp.zeros(ne, dtype=f64, device=device)
        self._upload(self.prev)
        wp.launch(angles, dim=ne, inputs=[self.q, self.edge], outputs=[self.prev_theta32, self.prev_active], device=device)
        self._hinges(self.prev_theta32, self.prev_active, out_theta=self.prev_theta)
        self.reference = FixedProblemEnergy(model, bend, spring, prev, inertia, dt, radius, margin, device, angle_pool=angle_pool) if audit.ON else None

    def _upload(self, q):
        q = np.asarray(q)
        self.q.assign(q.astype(np.float32)); self.q64.assign(q.astype(float))

    def _hinges(self, theta32, active, out_theta):
        wp.launch(hinge_energy, dim=self.ne, inputs=[self.q64, self.edge, theta32, active, self.rest, self.ke, self.kd,
                  self.prev_theta, self.prev_theta32, self.prev_active, f64(self.dt), float(np.float32(np.pi)),
                  float(np.float32(2 * np.pi)), f64(2 * np.pi)], outputs=[out_theta, self.hinge_out], device=self.device)

    def evaluate(self, q, body):
        q = np.asarray(q, float); dev = self.device; dt = f64(self.dt)
        self._upload(q)
        wp.launch(angles, dim=self.ne, inputs=[self.q, self.edge], outputs=[self.theta32, self.active], device=dev)
        self._hinges(self.theta32, self.active, out_theta=self.theta)
        wp.launch(tri_energy, dim=self.nt, inputs=[self.q64, self.qp, self.tri, self.pose, self.mat, self.area, dt], outputs=[self.tri_out], device=dev)
        wp.launch(inertia_energy, dim=self.n, inputs=[self.q64, self.y, self.mass, dt], outputs=[self.inertia_out], device=dev)
        if self.ns:
            wp.launch(spring_energy, dim=self.ns, inputs=[self.q64, self.sprev, self.sidx, self.srest, self.ske, self.skd, dt], outputs=[self.spring_out], device=dev)
        tri = self.tri_out.numpy(); hinge = self.hinge_out.numpy()
        ids = body['indices'][:, 0]
        distance = np.sum(body['normal'].astype(float) * (q[ids] - body['point']), axis=1)
        penetration = np.maximum(self.radius[ids] + self.margin - distance, 0)
        parts = {'inertia': float(np.sum(self.inertia_out.numpy())), 'membrane_elastic': float(np.sum(tri[:, 0])),
                 'membrane_metric_damping': float(np.sum(tri[:, 1])), 'bending_elastic': float(np.sum(hinge[:, 0])),
                 'bending_damping': float(np.sum(hinge[:, 1])), 'springs': float(np.sum(self.spring_out.numpy())) if self.ns else 0.,
                 'body_penalty': float(.5 * np.sum(body['ke'].astype(float) * penetration ** 2))}
        total = float(sum(parts.values()))
        if not np.isfinite(total): raise ValueError('Nonfinite fixed problem energy')
        meta = {'hinge_active': self.active.numpy().astype(bool), 'hinge_angle32': self.theta32.numpy()}
        if self.reference is not None:
            ref, ref_parts, ref_meta = self.reference.evaluate(q, body)
            if abs(total - ref) > 1e-9 * max(abs(ref), 1e-12) or not np.array_equal(meta['hinge_active'], ref_meta['hinge_active']) \
                    or not np.array_equal(meta['hinge_angle32'], ref_meta['hinge_angle32']):
                raise RuntimeError(f'GPU energy differs from the CPU reference: {total!r} vs {ref!r}, parts {parts} vs {ref_parts}')
        return total, parts, meta


from .cached_problem_energy_speed import CachedProblemEnergy


class CachedGpuProblemEnergy(CachedProblemEnergy, GpuFixedProblemEnergy):
    """The exact-input memo of cached_problem_energy_speed over the GPU evaluation."""
