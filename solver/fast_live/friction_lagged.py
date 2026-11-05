# SPDX-License-Identifier: GPL-3.0-or-later
"""Lagged body friction for the S3 global correction (static body, no friction anchor).

Contract (Native: rigid_vbd_kernels._compute_body_particle_contact_force with kd=0):
  slip u = x - x_prev (substep start; the static body has bv = 0), u_t = (I - n n^T) u
  f_fric = -mu * f_n * s(|u_t|) * u_t,  s = 1/|u|  (|u| > eps_u)  or  (2 - |u|/eps_u)/eps_u
  H_fric = mu * f_n * s(|u_t|) * (I - n n^T)          (normal and f_n treated as constant)
  mu = sqrt(soft_contact_mu * shape_mu), eps_u = friction_epsilon * dt, f_n = ke * penetration.

During one correction the contact set, n, f_n (lambda), mu and x_prev are frozen at the state the
correction starts from (Native's last iteration). Then the friction is the gradient of the convex
dissipation potential D(x) = sum mu*lambda*f0(|u_t|), f0' = s*|u|, so D joins the incremental energy
used for the merit, and -grad D at the lag point equals Native's friction force exactly.
Nothing is written back: Native keeps no friction history without fric_anchor, so only the positions
are committed and the next Native iteration recomputes its friction from its own contacts.
With fric_anchor the slip origin of an anchored particle is taken from Native's anchor (begin(anchor=...)); the
anchor, its normal and the touch flags stay Native's: the correction reads them and Native's own
_fric_anchor_update commits them after the substep from the corrected positions.
"""
import hashlib
import numpy as np


def _f0(u, eps):
    return np.where(u < eps, u * u / eps - u ** 3 / (3 * eps * eps), u - eps / 3)


def _s(u, eps):
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(u > eps, 1.0 / np.maximum(u, 1e-300), (2.0 - u / eps) / eps)


class LaggedBodyFriction:
    def __init__(self, n_particles, eps_u):
        self.n = n_particles
        self.eps = float(eps_u)
        self.ids = np.empty(0, int)

    def begin(self, q0, prev, ids, point, normal, ke, mu, radius, margin, anchor=None, anchored=None, kd=None, dt=None):
        """Freeze the contacts at q0. Only touching contacts with mu > 0 (or kd > 0) carry friction (damping).
        kd (Native: avg of soft_contact_kd and the shape's kd): while touching and moving into the body
        (n . u < 0, u from the slip origin) Native adds -(kd/dt) n (n . u) and Hessian (kd/dt) n n^T, the gradient of
        0.5 (kd/dt) min(n . u, 0)^2 with n frozen; it joins the same potential.
        fric_anchor (Native kernel, particle path): an anchored particle's slip origin is the anchor's tangential
        place and the substep start's normal place, a - n (n . (a - prev)); the others slip from prev."""
        q0 = np.asarray(q0, float)
        ids = np.asarray(ids, int)
        normal = np.asarray(normal, float)
        prev = np.asarray(prev, float)
        if anchor is not None:
            prev = prev.copy(); sel = np.asarray(anchored, bool)[ids]
            # in float32 like the kernel: the origin's rounding (~6e-8 m near 1 m) is a few % of eps_u (1.7e-6 m)
            a = np.asarray(anchor, np.float32)[ids]; n32 = normal.astype(np.float32); p32 = prev[ids].astype(np.float32)
            d = a - p32
            dn = n32[:, 0] * d[:, 0] + n32[:, 1] * d[:, 1] + n32[:, 2] * d[:, 2]
            origin = (a - n32 * dn[:, None]).astype(float)
            prev[ids[sel]] = origin[sel]
            self.anchored_count = int(sel.sum())
        depth = np.asarray(radius, float)[ids] + float(margin) - np.sum(normal * (q0[ids] - np.asarray(point, float)), axis=1)
        lam = np.asarray(ke, float) * np.maximum(depth, 0.0)
        kd = np.zeros(len(ids)) if kd is None else np.asarray(kd, float)
        keep = (lam > 0) & ((np.asarray(mu, float) > 0) | (kd > 0))
        self.damp = kd[keep] / dt if kd.any() else np.zeros(int(keep.sum()))   # kd / dt, N/m
        self.ids = ids[keep]
        self.normal = normal[keep]
        self.weight = np.asarray(mu, float)[keep] * lam[keep]   # mu * lambda, N
        self.mu = np.asarray(mu, float)[keep]
        self.lam = lam[keep]
        self.prev = prev[self.ids].copy()
        if len(np.unique(self.ids)) != len(self.ids):
            raise ValueError('Friction lag needs one contact per particle')
        self.signature = self._signature()
        return {'friction_contacts': int(len(self.ids)), 'dropped_untouching_or_mu0': int((~keep).sum()), 'damped_contacts': int((self.damp > 0).sum())}

    def _signature(self):
        h = hashlib.sha256()
        for a in (self.ids, self.normal, self.weight, self.prev, self.damp):
            h.update(np.ascontiguousarray(a).tobytes())
        return h.hexdigest()

    def check_unchanged(self):
        from . import audit
        if not audit.ON:
            return
        if self._signature() != self.signature:
            raise RuntimeError('Lagged friction state was modified during candidate evaluation')

    def _tangent(self, q):
        u = np.asarray(q, float)[self.ids] - self.prev
        ut = u - self.normal * np.sum(self.normal * u, axis=1)[:, None]
        return ut, np.linalg.norm(ut, axis=1)

    def _approach(self, q):
        """min(n . u, 0): the normal motion into the body since the slip origin (its normal place = the substep start)."""
        u = np.asarray(q, float)[self.ids] - self.prev
        return np.minimum(np.sum(self.normal * u, axis=1), 0.0)

    def energy(self, q):
        if not len(self.ids):
            return 0.0
        _, un = self._tangent(q)
        e = float(np.sum(self.weight * _f0(un, self.eps)))
        if self.damp.any():
            e += float(0.5 * np.sum(self.damp * self._approach(q) ** 2))
        return e

    def force(self, q):
        out = np.zeros((self.n, 3))
        if len(self.ids):
            ut, un = self._tangent(q)
            scale = np.where(un > 0, self.weight * _s(un, self.eps), 0.0)
            np.add.at(out, self.ids, -scale[:, None] * ut)
            if self.damp.any():
                np.add.at(out, self.ids, -(self.damp * self._approach(q))[:, None] * self.normal)
        return out

    def hessian(self, q):
        out = np.zeros((self.n, 3, 3))
        if len(self.ids):
            _, un = self._tangent(q)
            scale = np.where(un > 0, self.weight * _s(un, self.eps), 0.0)
            P = np.eye(3)[None] - self.normal[:, :, None] * self.normal[:, None, :]
            np.add.at(out, self.ids, scale[:, None, None] * P)
            if self.damp.any():
                on = np.where(self._approach(q) < 0, self.damp, 0.0)
                np.add.at(out, self.ids, on[:, None, None] * self.normal[:, :, None] * self.normal[:, None, :])
        return out

    def slip_mm(self, q):
        if not len(self.ids):
            return np.empty(0)
        return self._tangent(q)[1] * 1000
