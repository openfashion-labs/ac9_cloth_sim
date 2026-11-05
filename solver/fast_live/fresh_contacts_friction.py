# SPDX-License-Identifier: GPL-3.0-or-later
"""FreshOpenContacts with Native's body friction coefficient carried along (no damping, no anchor).

The normal force and its Hessian stay Native's frictionless kernel output (mu zeroed on the private buffer);
the effective mu that Native's init kernel composes (sqrt(soft_contact_mu * shape mu)) is returned with the
contact parameters so the correction can add the lagged friction (friction_lagged.py) itself.
native_mu=True evaluates Native's full kernel with that mu instead (force incl. friction), for audits only.
"""
import numpy as np, warp as wp
from newton._src.solvers.vbd.rigid_vbd_kernels import init_body_particle_contacts
from .fresh_contacts_open import FreshOpenContacts, remap


class FreshFrictionContacts(FreshOpenContacts):
    def _signature(self):
        """Parent signature plus Native's friction anchor state (anchor, normal, touch flags), when on."""
        base = super()._signature(); s = self.s
        if getattr(s, 'ac9_fric_anchor', None) is None or s.ac9_fric_anchor.shape[0] == 0: return base
        import hashlib
        h = hashlib.sha256(base.encode())
        for a in (s.ac9_fric_anchor, s.ac9_fric_n, s.ac9_fric_hit): h.update(a.numpy().tobytes())
        return h.hexdigest()

    def anchor_state(self):
        """Native's anchor (positions) and which particles are anchored (hit 1 or 3), read only; None when off."""
        s = self.s
        if getattr(s, 'ac9_fric_anchor', None) is None or s.ac9_fric_anchor.shape[0] == 0: return None, None
        hit = s.ac9_fric_hit.numpy()
        return s.ac9_fric_anchor.numpy().astype(float), (hit == 1) | (hit == 3)

    def begin_step(self, original):
        super().begin_step(original)
        count = int(original.soft_contact_count.numpy()[0])
        self.native_mu_registry = np.full(self.m.particle_count, np.nan)
        if count:
            self.native_mu_registry[original.soft_contact_indices.numpy()[:count, 0]] = self.s.body_particle_contact_material_mu.numpy()[:count]

    def evaluate(self, state, dt, detect=True, preserve=True, native_mu=False, anchor_copy=False):
        m = self.m; s = self.s; c = self.contacts
        if detect: self.pipe.collide(state, c)
        count = int(c.soft_contact_count.numpy()[0])
        if count < 0 or count > c.soft_contact_max: raise ValueError('Fresh contact overflow')
        indices = c.soft_contact_indices.numpy()[:count]; shapes = c.soft_contact_shape.numpy()[:count]
        if count and (np.any(indices[:, 0] < 0) or np.any(indices[:, 0] >= m.particle_count)): raise ValueError('Fresh particle ID out of range')
        if count and (np.any(indices[:, 1:] >= 0) or np.any(shapes != 0) or len(np.unique(indices[:, 0])) != count): raise ValueError('Fresh contacts must be unique vertex/shape pairs')
        wp.launch(init_body_particle_contacts, dim=c.soft_contact_max, inputs=[c.soft_contact_count, c.soft_contact_shape, m.soft_contact_ke, m.soft_contact_kd, m.soft_contact_mu, m.shape_material_ke, m.shape_material_kd, m.shape_material_mu, s.rigid_contact_k_start_value], outputs=[self.penalty, self.kd, self.mu, self.ke], device=s.device)
        if preserve: wp.launch(remap, dim=count, inputs=[self.registry, c.soft_contact_indices, c.soft_contact_count], outputs=[self.penalty], device=s.device)
        kd = self.kd.numpy()[:count].astype(float).copy()
        if not np.isfinite(kd).all() or np.any(kd < 0): raise ValueError('Invalid composed kd')
        mu = self.mu.numpy()[:count].astype(float).copy()
        if not np.isfinite(mu).all() or np.any(mu < 0): raise ValueError('Invalid composed mu')
        if not native_mu: self.mu.zero_(); self.kd.zero_()
        self.force.zero_(); self.H.zero_()
        anchor = (self.emptyv, self.emptyv, self.emptyi)
        if anchor_copy and getattr(s, 'ac9_fric_anchor', None) is not None and s.ac9_fric_anchor.shape[0]:
            # the kernel writes the touch flags and normals: audits run on throwaway copies, never on Native's arrays
            anchor = (wp.clone(s.ac9_fric_anchor), wp.clone(s.ac9_fric_n), wp.clone(s.ac9_fric_hit))
        if count:
            wp.launch(self.k.accumulate_particle_body_contact_force_and_hessian, dim=count, inputs=[dt, 0, s.particle_q_prev, state.particle_q, self.colors, s.friction_epsilon, m.particle_radius, c.soft_contact_indices, c.soft_contact_count, c.soft_contact_max, self.penalty, self.ke, self.kd, self.mu, m.shape_body, state.body_q, None, state.body_qd, m.body_com, c.soft_contact_shape, c.soft_contact_body_pos, c.soft_contact_body_vel, c.soft_contact_normal, m.shape_margin, c.soft_contact_barycentric, *anchor], outputs=[self.force, self.H], device=s.device)
        self.check_unchanged(); self.last_ids = indices[:, 0].copy(); self.last_new = int(np.sum(self.registry_cpu[self.last_ids] < 0))
        self.current_parameters = {'indices': indices, 'shape': shapes, 'point': c.soft_contact_body_pos.numpy()[:count], 'normal': c.soft_contact_normal.numpy()[:count], 'ke': self.penalty.numpy()[:count], 'mu': mu, 'kd': kd}
        force = self.force.numpy().astype(float); hessian = self.H.numpy().astype(float)
        if not np.isfinite(force).all() or not np.isfinite(hessian).all(): raise ValueError('Nonfinite fresh force/H')
        return force, hessian, count

    def detect(self, state):
        """Detect and compose the penalties at state (as evaluate) without the force kernel: for energy evaluation only."""
        m = self.m; s = self.s; c = self.contacts
        self.pipe.collide(state, c)
        count = int(c.soft_contact_count.numpy()[0])
        if count < 0 or count > c.soft_contact_max: raise ValueError('Fresh contact overflow')
        indices = c.soft_contact_indices.numpy()[:count]; shapes = c.soft_contact_shape.numpy()[:count]
        if count and (np.any(indices[:, 0] < 0) or np.any(indices[:, 0] >= m.particle_count)): raise ValueError('Fresh particle ID out of range')
        if count and (np.any(indices[:, 1:] >= 0) or np.any(shapes != 0) or len(np.unique(indices[:, 0])) != count): raise ValueError('Fresh contacts must be unique vertex/shape pairs')
        wp.launch(init_body_particle_contacts, dim=c.soft_contact_max, inputs=[c.soft_contact_count, c.soft_contact_shape, m.soft_contact_ke, m.soft_contact_kd, m.soft_contact_mu, m.shape_material_ke, m.shape_material_kd, m.shape_material_mu, s.rigid_contact_k_start_value], outputs=[self.penalty, self.kd, self.mu, self.ke], device=s.device)
        wp.launch(remap, dim=count, inputs=[self.registry, c.soft_contact_indices, c.soft_contact_count], outputs=[self.penalty], device=s.device)
        self.check_unchanged(); self.last_ids = indices[:, 0].copy(); self.last_new = int(np.sum(self.registry_cpu[self.last_ids] < 0))
        self.current_parameters = {'indices': indices, 'shape': shapes, 'point': c.soft_contact_body_pos.numpy()[:count], 'normal': c.soft_contact_normal.numpy()[:count], 'ke': self.penalty.numpy()[:count]}
        return count

    def native_friction(self, state, dt):
        """Native kernel's friction force at state on the last detected contacts (full minus mu=0)."""
        with_mu, _, _ = self.evaluate(state, dt, detect=False, native_mu=True, anchor_copy=True)
        without, _, _ = self.evaluate(state, dt, detect=False, native_mu=False)
        return with_mu - without

    def mu_agreement(self):
        """Composed mu on the fresh contacts vs Native's own per-contact mu for the same particles."""
        p = self.current_parameters
        if p is None or not len(p['indices']): return None
        native = self.native_mu_registry[p['indices'][:, 0]]
        both = np.isfinite(native)
        return {'common': int(both.sum()), 'max_abs_diff': float(np.abs(native[both] - p['mu'][both]).max()) if both.any() else None,
                'mu_values': sorted(set(np.round(p['mu'], 6).tolist()))[:5]}
