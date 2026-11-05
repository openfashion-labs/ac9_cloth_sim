# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloth-cloth contact inside the correction: Native's own contacts, lagged, and Native's own step limit.

Native resolves cloth-cloth contact in its iterations: a barrier force on the contacts found at the last collision
detection, and a per-point limit on how far a point may move from where that detection ran (planar for each contact,
capped at an isotropic 0.425 x the query radius). The correction is one global step at the end of a substep, so it
borrows both:
- the contact force and its per-point 3x3 Hessian at the correction's start q (Native's kernel with every point
  counted, so not one color at a time), added to the residual and to the solve matrix, and the matching quadratic
  model of the contact energy, E(q + d) = -f.d + d.H.d / 2, added to the line search's energy;
- a step limit per point. Native's own limit holds every point within 0.425 x the query radius of the last detection
  (about 0.19 mm), which cut the correction's millimeter steps to nothing on a moving test T-shirt (step length median
  0.004 of the full step while the hem fell). So before the line search the detection is run again at the correction's
  start q with a query radius that covers the step (wide), and each point may move at most GAMMA x its distance to the
  nearest other cloth (the vertex block descent paper's conservative bound; GAMMA < 0.5 keeps every pair apart).
"""
import sys
import os as _os
import numpy as np
import warp as wp


GAMMA = float(_os.environ.get('AC9_TEST_SC_GAMMA', 0.4))   # test switch
ADVANCE = int(_os.environ.get('AC9_TEST_SC_ADVANCE', 8))   # test switch: safe steps per truncation outside the graph (0: one)
REACH_MAX = 0.008   # m: the widest detection (the step is limited to GAMMA x this away from other cloth)


class LaggedSelfContact:
    def __init__(self, model, solver):
        self.m = model; self.s = solver
        self.vbd = sys.modules[type(solver).__module__]
        n = model.particle_count
        self.colors = wp.full(n, -1, dtype=int, device=solver.device)   # -1: every point takes its share
        self.force = wp.zeros(n, dtype=wp.vec3, device=solver.device)
        self.hess = wp.zeros(n, dtype=wp.mat33, device=solver.device)
        self.f0 = None; self.h0 = None; self.q0 = None
        self.bounds = wp.zeros(n, dtype=float, device=solver.device); self.bound = None
        self.cap = None   # NativeStepCap, when installed
        self.scratch = wp.zeros(n, dtype=wp.vec3, device=solver.device)

    def evaluate(self, q_wp, dt):
        """Native's contact force and diagonal Hessian blocks at q_wp (float64 copies)."""
        s, m = self.s, self.m
        self.force.zero_(); self.hess.zero_()
        lay = s._ac9_layer_arrays()
        wp.launch(self.vbd.accumulate_self_contact_force_and_hessian, dim=s.particle_self_contact_evaluation_kernel_launch_size,
                  inputs=[dt, -1, s.particle_q_prev, q_wp, self.colors, m.tri_indices, m.edge_indices,
                          s.trimesh_collision_info, s.particle_self_contact_margin, m.soft_contact_ke, m.soft_contact_kd,
                          0. if _os.environ.get('AC9_TEST_SC_MU0') else m.soft_contact_mu, s.friction_epsilon, s._self_contact_edge_edge_parallel_epsilon,
                          lay[0], lay[1], lay[2], lay[3]],
                  outputs=[self.force, self.hess], device=s.device, max_blocks=m.device.sm_count)
        return self.force.numpy().astype(float), self.hess.numpy().astype(float)

    def begin(self, q, f, h):
        self.q0 = np.asarray(q, float); self.f0 = f; self.h0 = h

    def energy(self, c):
        d = np.asarray(c, float) - self.q0
        return float(-np.sum(self.f0 * d) + .5 * np.einsum('ni,nij,nj->', d, self.h0, d))

    def truncate(self, candidate, q_out):
        """Native's step limit on a trial: returns the limited positions and how many points it moved."""
        s = self.s
        prev = s.pos_prev_collision_detection.numpy()
        s.particle_displacements.assign((np.asarray(candidate, np.float32) - prev).astype(np.float32))
        s._penetration_free_truncation(q_out)
        limited = q_out.numpy()
        return limited, int(np.sum(np.any(limited != np.asarray(candidate, np.float32), axis=1)))

    def widen(self, state, step):
        """Detection at the correction's start with a radius that covers the full step; per point bounds GAMMA x d_min."""
        s = self.s
        if self.cap is not None:   # the same two stages as Native's iterations: planar for the contacts, per-point cap
            self.cap.detect(state); self.reach = self.cap.stats['reach_mm_max'] / 1000
            if _os.environ.get('AC9_TEST_SC_WATCH'):   # diagnostic only: the watched points' cap and bound at each correction
                import json
                w = getattr(self, '_wcap', None)
                if w is None:
                    w = self._wcap = {'v': json.load(open(_os.environ['AC9_TEST_SC_WATCH'])), 'rows': []}
                    import atexit; atexit.register(lambda: open(_os.environ['AC9_TEST_SC_WATCH_OUT'] + '.cap.json', 'w').write(json.dumps(w['rows'])))
                c = self.cap.cap.numpy()[w['v']]; b = self.cap.bound.numpy()[w['v']]
                w['rows'].append([float(np.min(c)), float(np.median(c)), float(np.min(b)), float(np.median(b))])
            return self.reach
        reach = float(min(REACH_MAX, max(s._self_contact_query_radius, np.linalg.norm(step, axis=1).max() / GAMMA)))
        s.pos_prev_collision_detection.assign(state.particle_q); s.particle_displacements.zero_()
        d = s.trimesh_collision_detector
        d.refit(state.particle_q)
        d.vertex_triangle_collision_detection(reach, min_query_radius=s.particle_rest_shape_contact_exclusion_radius,
                                              min_distance_filtering_ref_pos=s.particle_q_rest)
        d.edge_edge_collision_detection(reach, min_query_radius=s.particle_rest_shape_contact_exclusion_radius,
                                        min_distance_filtering_ref_pos=s.particle_q_rest)
        wp.launch(sys.modules['newton._src.solvers.vbd.particle_vbd_kernels'].compute_particle_conservative_bound, dim=self.m.particle_count,
                  inputs=[GAMMA, reach, s.particle_adjacency, s.trimesh_collision_detector.collision_info], outputs=[self.bounds], device=s.device)
        self.bound = self.bounds.numpy().astype(float); self.reach = reach
        return reach

    def limit(self, candidate):
        """Each point within its bound of the start q: returns the limited positions and how many points it moved."""
        if self.cap is not None:
            s = self.s
            if ADVANCE and _os.environ.get('AC9_TEST_SC_ADVANCE_AT', 'limit').find('limit') >= 0:
                left = self.cap.advance(self.scratch, candidate, s.pos_prev_collision_detection.numpy(), body=False)
                return self.scratch.numpy(), left
            s.particle_displacements.assign((np.asarray(candidate, np.float32) - self.q0.astype(np.float32)).astype(np.float32))
            self.cap._truncate_once(self.scratch, body=False)
            limited = self.scratch.numpy()
            if _os.environ.get('AC9_TEST_SC_WATCH'):   # diagnostic only: the watched points' cap, planar factor, wanted step
                import json
                w = getattr(self, '_watch', None)
                if w is None:
                    w = self._watch = {'v': json.load(open(_os.environ['AC9_TEST_SC_WATCH'])), 'rows': []}
                    import atexit; atexit.register(lambda: open(_os.environ['AC9_TEST_SC_WATCH_OUT'], 'w').write(json.dumps(w['rows'])))
                v = w['v']; want = np.linalg.norm(np.asarray(candidate, float)[v] - self.q0[v], axis=1)
                got = np.linalg.norm(limited[v].astype(float) - self.q0[v], axis=1)
                w['rows'].append([float(np.median(want)), float(np.median(got)), float(np.median(self.cap.cap.numpy()[v])),
                                  float(np.median(s.truncation_ts.numpy()[v])), float(np.min(s.truncation_ts.numpy()[v]))])
            return limited, int(np.sum(np.any(np.abs(limited - np.asarray(candidate, np.float32)) > 1e-9, axis=1)))
        c = np.asarray(candidate, float); d = c - self.q0; n = np.linalg.norm(d, axis=1)
        over = n > self.bound
        if over.any():
            d[over] *= (self.bound[over] / n[over])[:, None]
        return (self.q0 + d).astype(np.float32), int(over.sum())


class LaggedLayerCrossing:
    """Native's layer crossing force (a cloth point on the wrong side of a lower layer is pushed back), taken at the
    correction's start as a quadratic model, like the cloth contact."""
    def __init__(self, model, solver):
        self.m = model; self.s = solver
        n = model.particle_count
        self.colors = wp.full(n, -1, dtype=int, device=solver.device)
        self.force = wp.zeros(n, dtype=wp.vec3, device=solver.device)
        self.hess = wp.zeros(n, dtype=wp.mat33, device=solver.device)
        self.q0 = None; self.f0 = None; self.h0 = None

    def evaluate(self, q_wp):
        s = self.s; c = s.ac9_layer_crossings
        self.force.zero_(); self.hess.zero_()
        wp.launch(s.ac9_layer_crossing_module.accumulate, dim=c['active'].shape[0],
                  inputs=[q_wp, self.m.tri_indices, self.colors, -1, s._ac9_layer_arrays()[3], c['active'], c['count'],
                          s.particle_self_contact_margin, self.m.soft_contact_ke * getattr(s, 'ac9_layer_crossing_ke_scale', 1.0),
                          self.force, self.hess], device=s.device)
        return self.force.numpy().astype(float), self.hess.numpy().astype(float)

    def begin(self, q, f, h):
        self.q0 = np.asarray(q, float); self.f0 = f; self.h0 = h

    def energy(self, c):
        d = np.asarray(c, float) - self.q0
        return float(-np.sum(self.f0 * d) + .5 * np.einsum('ni,nij,nj->', d, self.h0, d))


class _Positions:
    def __init__(self, q):
        self.particle_q = q


@wp.kernel
def _cap_at_least(bound: wp.array(dtype=float), native: float, cap: wp.array(dtype=float)):
    i = wp.tid()
    cap[i] = wp.max(bound[i], native)


@wp.kernel
def _truncate_by_body(pos: wp.array(dtype=wp.vec3), displacement: wp.array(dtype=wp.vec3), count: wp.array(dtype=int),
                      particle: wp.array(dtype=int), body_pos: wp.array(dtype=wp.vec3), normal: wp.array(dtype=wp.vec3),
                      radius: wp.array(dtype=float), margin: float, gamma: float, floor: float,
                      truncation_ts: wp.array(dtype=float)):
    # planar, like Native's cloth contacts: a point's step toward a body contact candidate's plane is at most
    # max(Native's cap, gamma x its gap); sliding along the body is not limited
    i = wp.tid()
    if i >= count[0]:
        return
    p = particle[i]
    gap = wp.dot(normal[i], pos[p] - body_pos[i]) - radius[p] - margin
    approach = -wp.dot(displacement[p], normal[i])
    allowed = wp.max(floor, gamma * wp.max(gap, 0.0))
    if approach > allowed:
        wp.atomic_min(truncation_ts, p, allowed / approach)


@wp.kernel
def _truncate_per_point(pos: wp.array(dtype=wp.vec3), displacement_in: wp.array(dtype=wp.vec3),
                        truncation_ts: wp.array(dtype=float), cap: wp.array(dtype=float),
                        displacement_out: wp.array(dtype=wp.vec3), pos_out: wp.array(dtype=wp.vec3)):
    # Native's apply_truncation_ts with the isotropic cap per point instead of one value for all
    i = wp.tid()
    d = displacement_in[i] * truncation_ts[i]
    n = wp.length(d)
    if n > cap[i]:
        d = d * cap[i] / n
    displacement_out[i] = d
    pos_out[i] = pos[i] + d


class NativeStepCap:
    """Accurate only: Native's own per-detection step cap, per point.

    With cloth-cloth contact on, Native holds every point within 0.425 x the query radius of the last detection
    (about 0.19 mm with the product margin), whether other cloth is near or not, twice a substep (the inertial guess
    and the iterations). At 2 substeps that caps a falling hem's speed: a test T-shirt's hem stayed 8.6 mm up after
    release, and loosening the cap 10x / 100x (diagnostic only) brought it to 6.1 / 2.7 mm (contact off: 1.9).
    Here each detection is first run wide (reach = the fastest point's travel in one substep, |v| dt + g dt^2 / 2,
    over GAMMA), each point's cap becomes max(Native's cap, GAMMA x its distance to the nearest other cloth), and then
    Native's own detection runs again so its contact lists are exactly Native's. The planar per-contact truncation is
    Native's, unchanged.
    """

    def __init__(self, model, solver, dt, contacts=None):
        self.m = model; self.s = solver; self.dt = float(dt)
        # Attach (E5): an explicit pull lets a point far from other cloth step through the body in one iteration once
        # the cap is wider than Native's; the body contact candidates bound it too (test switch AC9_TEST_SC_CAP_BODY=0)
        self.contacts = contacts if _os.environ.get('AC9_TEST_SC_CAP_BODY', '1') != '0' else None
        self.margin = float(model.shape_margin.numpy()[0])   # read here: detect runs inside the captured graph
        self.floor = solver._self_contact_query_radius * solver.particle_conservative_bound_relaxation * 0.5   # Native's cap: a point on the body moves as in Native
        self.k = sys.modules['newton._src.solvers.vbd.particle_vbd_kernels']
        n = model.particle_count
        self.bound = wp.zeros(n, dtype=float, device=solver.device); self.cap = wp.zeros(n, dtype=float, device=solver.device)
        self.native_detect = solver._collision_detection_penetration_free
        self.native_truncate = solver._penetration_free_truncation
        if solver.collision_pipeline is not None or getattr(solver, 'ac9_bound_constraints', None) is not None:
            raise ValueError('Accurate step cap needs the solver-owned detector')
        self.stats = {'detections': 0, 'reach_mm_max': 0.}; self.origin = None
        self.seam = None
        if _os.environ.get('AC9_TEST_SC_SEAM_SLOW'):   # diagnostic only: advance steps keep Native's cap on the seam bands
            vk, ek = getattr(solver, 'ac9_seam_filter_keys', ([], []))
            pts = set(int(x) for x in vk)
            if ek:
                ei = model.edge_indices.numpy()[np.asarray(ek, int)]; pts.update(ei[:, 2].tolist()); pts.update(ei[:, 3].tolist())
            m_ = np.zeros(n, bool); m_[list(pts)] = True; self.seam = m_; self.stats['seam_points'] = int(m_.sum())
        solver._collision_detection_penetration_free = self.detect
        solver._penetration_free_truncation = self.truncate
        every = int(_os.environ.get('AC9_TEST_SC_DETECT_EVERY') or 0)   # test switch: detect every k iterations
        if every:
            F = type(solver).CollisionFrequencyType
            solver._resolve_self_contact_schedule = lambda: (F.ITERATIONS, every)

    def detect(self, state, again=False):
        if not getattr(self.s, 'ac9_step_cap_active', True):   # before sewn (correction.begin_substep): Native's own
            return self.native_detect(state)
        if not again:
            self.origin = None   # positions of Native's own last detection (set by advance when it detects again)
        # Runs inside Native's captured iteration graph, so everything here is a kernel with fixed arguments: a host
        # read (numpy) would be frozen at capture time. The reach is therefore fixed (REACH or the test switch).
        s = self.s; d = s.trimesh_collision_detector
        native = s._self_contact_query_radius * s.particle_conservative_bound_relaxation * 0.5
        native = float(_os.environ.get('AC9_TEST_SC_CAPFLOOR') or native)   # diagnostic only
        if again and _os.environ.get('AC9_TEST_SC_AGAIN_FLOOR'):   # test switch: an advance step keeps Native's floor
            pass
        elif again:   # an advance step: no floor (Native's floor is a per-detection speed limit that also guards the pairs
            native = 0.   # its contact lists leave out, near on the rest shape; here every pair is in d_min instead)
        reach = max(s._self_contact_query_radius, float(_os.environ.get('AC9_TEST_SC_REACH', REACH_MAX)))
        # d_min skips the pairs inside the query radius now (Native's planar truncation guards them), so a point in
        # contact may still slide; pairs between the query radius and the reach keep GAMMA x their distance. The rest
        # shape exclusion takes the same argument, so it is lost here: near pairs on the pattern count as cloth, which
        # only lowers the cap.
        one = _os.environ.get('AC9_TEST_SC_ONESTAGE')   # test switch: every pair in d_min (the earlier form)
        rq, rp = (s.particle_rest_shape_contact_exclusion_radius, s.particle_q_rest) if one else (s._self_contact_query_radius, state.particle_q)
        two = again and _os.environ.get('AC9_TEST_SC_AGAIN_TWO')   # test switch: advance steps keep the two stages too
        if (again and not two) or _os.environ.get('AC9_TEST_SC_DMIN') == 'all':   # every pair in d_min, the rest-shape neighbors included
            rq, rp = 0.0, s.particle_q_rest
        d.refit(state.particle_q)
        d.vertex_triangle_collision_detection(reach, min_query_radius=rq, min_distance_filtering_ref_pos=rp)
        d.edge_edge_collision_detection(reach, min_query_radius=rq, min_distance_filtering_ref_pos=rp)
        if _os.environ.get('AC9_TEST_SC_OVERFLOW'):   # diagnostic only, outside the captured graph
            f = d.resize_flags.numpy()[:3]; self.stats['overflow'] = self.stats.get('overflow', 0) + int(f.any())
        post = getattr(s, 'ac9_free_post', None)
        if post is not None:   # the grab through / untangle_free: the freed contacts do not bound the step either
            post(d.collision_info)
        wp.launch(self.k.compute_particle_conservative_bound, dim=self.m.particle_count,
                  inputs=[GAMMA, reach, s.particle_adjacency, d.collision_info], outputs=[self.bound], device=s.device)
        wp.launch(_cap_at_least, dim=self.m.particle_count, inputs=[self.bound, native], outputs=[self.cap], device=s.device)
        if again and self.seam is not None:
            c = self.cap.numpy(); c[self.seam] = np.minimum(c[self.seam], s._self_contact_query_radius * s.particle_conservative_bound_relaxation * 0.5)
            self.cap.assign(c.astype(np.float32))
        if _os.environ.get('AC9_TEST_SC_CAPFREE'):   # diagnostic only
            self.cap.fill_(1e9)
        self.stats['detections'] += 1; self.stats['reach_mm_max'] = reach * 1000
        self.native_detect(state)

    def truncate(self, particle_q_out=None):
        s = self.s
        if not s.particle_enable_self_contact or not getattr(s, 'ac9_step_cap_active', True):
            return self.native_truncate(particle_q_out)
        if ADVANCE and _os.environ.get('AC9_TEST_SC_ADVANCE_AT', 'limit').find('init') >= 0 and sys._getframe(1).f_code.co_name in ('_initialize_particles', '_init_xv'):
            # the inertial guess, outside the captured graph: Native's displacement is from the last detection
            # (from the detection Native ran, even if a previous advance detected again since)
            base = s.pos_prev_collision_detection.numpy()
            origin = base if self.origin is None else self.origin
            if _os.environ.get('AC9_TEST_SC_ADVANCE_NATIVE'):
                return self.advance_native(particle_q_out, origin + s.particle_displacements.numpy(), base)
            return self.advance(particle_q_out, origin + s.particle_displacements.numpy(), base)
        return self._truncate_once(particle_q_out)

    def advance(self, q_out, target, base, body=True):
        """Toward target in safe steps: Native's truncation (planar for the contacts, the per-point cap for the rest);
        while some point was stopped by its cap, detect again where the step ended and take the rest from there.

        One capped step is the vertex block descent bound (a point moves at most GAMMA x its distance to other cloth),
        and each further step starts from a detection at the positions the previous one reached, so the guarantee is
        that of Native's truncation repeated, like Native with more detections. Outside the captured graph only (the
        inertial guess and the correction's step). Returns how many points the last step left short of the target.
        """
        s = self.s; target = np.asarray(target, np.float32)
        for k in range(ADVANCE):
            s.particle_displacements.assign(target - base)
            self._truncate_once(q_out, body=body)
            want = np.linalg.norm((target - base) * s.truncation_ts.numpy()[:, None], axis=1)
            capped = want > self.cap.numpy() * (1 + 1e-4) + 1e-9
            if _os.environ.get('AC9_TEST_SC_DIAG'):   # diagnostic only: per advance step, points cut by the planar truncation / the cap
                moving = np.linalg.norm(target - base, axis=1) > 1e-9
                self._diag_row = getattr(self, '_diag_row', []) + [[k, int((moving & (s.truncation_ts.numpy() < 1 - 1e-6)).sum()), int(capped.sum())]]
            if not capped.any() or k == ADVANCE - 1:
                break
            if self.origin is None:
                self.origin = s.pos_prev_collision_detection.numpy()
            self.detect(_Positions(q_out), again=True); base = q_out.numpy()
            self.stats['advance_steps'] = self.stats.get('advance_steps', 0) + 1
        got = q_out.numpy()
        if _os.environ.get('AC9_TEST_SC_DIAG') and not body:   # the correction's candidates only
            import json
            if not hasattr(self, '_diag'):
                self._diag = []; import atexit; atexit.register(lambda: open(_os.environ['AC9_TEST_SC_DIAG'], 'w').write(json.dumps(self._diag)))
            self._diag.append(self._diag_row)
        self._diag_row = []
        return int(np.sum(np.linalg.norm(got - target, axis=1) > 1e-9))

    def advance_native(self, q_out, target, base, steps=None):
        """Toward target by Native's own truncation repeated: each step is Native's (planar for the listed contacts,
        0.425 x the query radius for every point, the pairs its filters leave out included), and while some point was
        held by that cap, Native's detection runs again where the step ended. The guarantee is Native's with more
        detections in the substep."""
        s = self.s; target = np.asarray(target, np.float32)
        native = s._self_contact_query_radius * s.particle_conservative_bound_relaxation * 0.5
        steps = steps or int(_os.environ.get('AC9_TEST_SC_ADVANCE_NATIVE_STEPS') or 24)
        for k in range(steps):
            s.particle_displacements.assign(target - base)
            self.native_truncate(q_out)
            want = np.linalg.norm((target - base) * s.truncation_ts.numpy()[:, None], axis=1)
            if not (want > native * (1 + 1e-4) + 1e-9).any() or k == steps - 1:
                break
            if self.origin is None:
                self.origin = s.pos_prev_collision_detection.numpy()
            self.native_detect(_Positions(q_out)); base = q_out.numpy()
            self.stats['advance_steps'] = self.stats.get('advance_steps', 0) + 1
        return int(np.sum(np.linalg.norm(q_out.numpy() - target, axis=1) > 1e-9))

    def _truncate_once(self, particle_q_out=None, body=True):
        s = self.s
        free = _os.environ.get('AC9_TEST_SC_INIT_FREE') and sys._getframe(1).f_code.co_name in ('_initialize_particles', '_init_xv')
        if free:   # diagnostic only: the inertial guess left uncut
            wp.launch(_truncate_per_point, dim=self.m.particle_count,
                      inputs=[s.pos_prev_collision_detection, s.particle_displacements, wp.full(self.m.particle_count, 1.0, dtype=float, device=s.device),
                              wp.full(self.m.particle_count, 1e9, dtype=float, device=s.device)],
                      outputs=[s.particle_displacements, particle_q_out], device=s.device)
            return
        s.truncation_ts.fill_(1.0)
        lay = s._ac9_layer_arrays()
        if _os.environ.get('AC9_TEST_SC_SITES'):   # diagnostic only (with AC9_TEST_NO_GRAPH): wanted step vs cap per call site
            import json
            w = getattr(self, '_sites', None)
            if w is None:
                w = self._sites = {'v': json.load(open(_os.environ['AC9_TEST_SC_SITES'])), 'rows': []}
                import atexit; atexit.register(lambda: open(_os.environ['AC9_TEST_SC_SITES'] + '.sites.json', 'w').write(json.dumps(w['rows'])))
            f = sys._getframe(1).f_code.co_name; v = w['v']
            dn = np.linalg.norm(s.particle_displacements.numpy()[v], axis=1); cp = self.cap.numpy()[v]
            w['rows'].append([f, float(dn.max()), float(np.median(dn)), float(cp.min()), int((dn > cp).sum())])
        wp.launch(self.k.apply_planar_truncation_parallel_by_collision,
                  inputs=[s.pos_prev_collision_detection, s.particle_displacements, self.m.tri_indices, self.m.edge_indices,
                          s.trimesh_collision_info, s._self_contact_edge_edge_parallel_epsilon,
                          float(_os.environ.get('AC9_TEST_SC_PLANAR_RELAX', s.particle_conservative_bound_relaxation)), lay[0], lay[1], lay[2],
                          s.ac9_layer_crossings['reference_pairs'] if hasattr(s, 'ac9_layer_crossings') else lay[3]],
                  outputs=[s.truncation_ts], dim=s.particle_self_contact_evaluation_kernel_launch_size, device=s.device)
        if self.contacts is not None and body:   # Native's iterations only: the correction's step has clip_to_body
            c = self.contacts
            wp.launch(_truncate_by_body, dim=c.soft_contact_max,
                      inputs=[s.pos_prev_collision_detection, s.particle_displacements, c.soft_contact_count, c.soft_contact_particle,
                              c.soft_contact_body_pos, c.soft_contact_normal, self.m.particle_radius, self.margin, GAMMA, self.floor],
                      outputs=[s.truncation_ts], device=s.device)
        wp.launch(_truncate_per_point, dim=self.m.particle_count,
                  inputs=[s.pos_prev_collision_detection, s.particle_displacements, s.truncation_ts, self.cap],
                  outputs=[s.particle_displacements, particle_q_out], device=s.device)
