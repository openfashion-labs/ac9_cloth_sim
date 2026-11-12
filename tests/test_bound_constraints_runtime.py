"""Run with the solver's Newton/Warp Python environment (CPU tests)."""
import pathlib
import sys
import unittest
from types import SimpleNamespace

import numpy as np
import warp as wp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'solver'))
import bound_constraints as B
import nring_filter as N


@wp.kernel
def evaluate(q: wp.array[wp.vec3], w: wp.vec3, d: wp.vec3, p: wp.array[wp.vec3], j: wp.array[wp.mat33]):
    pp, j0, j1, j2 = B.binding_point(q[0], q[1], q[2], w, d)
    p[0] = pp
    j[0] = j0
    j[1] = j1
    j[2] = j2


def reference(q, w, d):
    u = q[1] - q[0]
    u /= np.linalg.norm(u)
    n = np.cross(q[1] - q[0], q[2] - q[0])
    n /= np.linalg.norm(n)
    return w @ q + d[0] * u + d[1] * np.cross(n, u) + d[2] * n


class BoundConstraintTests(unittest.TestCase):
    def test_native_fused_increment_matches_original_writes(self):
        rng = np.random.default_rng(12)
        for count in (4, 257):
            reference = rng.uniform(-2, 2, (count, 3)).astype(np.float32)
            positions = (reference + rng.uniform(-.03, .03, (count, 3))).astype(np.float32)
            displacement = rng.uniform(-.001, .001, (count, 3)).astype(np.float32)
            scales = rng.uniform(0, 1, count).astype(np.float32)
            scales[:2] = [0., 1.]
            ref = wp.array(reference, dtype=wp.vec3, device='cpu')
            pos = wp.array(positions, dtype=wp.vec3, device='cpu')
            disp = wp.array(displacement, dtype=wp.vec3, device='cpu')
            accepted = wp.empty_like(pos); increment = wp.empty_like(pos)
            wp.launch(B.prepare_increment, dim=count, inputs=[ref, pos, disp, accepted, increment], device='cpu')
            expected_increment = reference + displacement - positions
            np.testing.assert_array_equal(accepted.numpy(), positions)
            np.testing.assert_array_equal(increment.numpy(), expected_increment)
            scale = wp.array(scales, device='cpu')
            wp.launch(B.apply_increment, dim=count, inputs=[accepted, increment, scale, pos, ref, disp], device='cpu')
            expected_position = positions + expected_increment * scales[:, None]
            np.testing.assert_array_equal(pos.numpy(), expected_position)
            np.testing.assert_array_equal(disp.numpy(), expected_position - reference)

    def test_finished_increment_is_exact_placement_fixed_point(self):
        for height in [.02, .0001]:
            for offset in [0., .001]:
                q = np.array([[0., 0., 0.], [.02, 0., 0.], [0., height, 0.], [0., 0., 0.]], np.float32)
                model = SimpleNamespace(device=wp.get_device('cpu'), particle_count=4,
                    spring_indices=wp.array([], dtype=int, device='cpu'),
                    particle_colors=wp.array([0, 1, 2, 3], dtype=int, device='cpu'),
                    particle_color_groups=[wp.array([i], dtype=int, device='cpu') for i in range(4)])
                binding = B.BoundConstraints(model, [3], [[0, 1, 2]], [[.2, .3, .5]], [[0, 0, offset]])
                pos = wp.array(q, dtype=wp.vec3, device='cpu'); binding.place(pos)
                ref = wp.clone(pos)
                for delta in [.00001, -.00002, .00003]:
                    proposal = np.zeros_like(q); proposal[2] = [delta, -delta, delta]
                    disp = wp.array(proposal, dtype=wp.vec3, device='cpu')
                    binding.prepare_increment(ref, pos, disp)
                    scales = wp.ones(4, dtype=float, device='cpu')
                    binding.limit_travel(ref, scales, .00019125)
                    pos.assign(pos.numpy() + scales.numpy()[:, None] * binding.increment.numpy())
                    binding.finish_increment(pos, ref, disp)
                    before = pos.numpy().copy()
                    diagnostics = {n: getattr(binding, n).numpy().copy() for n in
                        ['accepted', 'increment', 'limits', 'minimum_scale', 'scale_counts', 'path_counts', 'sweep_error']}
                    before_disp = disp.numpy().copy()
                    binding.place(pos)
                    np.testing.assert_array_equal(pos.numpy(), before)
                    np.testing.assert_array_equal(disp.numpy(), before_disp)
                    for n, value in diagnostics.items():
                        np.testing.assert_array_equal(getattr(binding, n).numpy(), value)

    def test_skinny_support_cannot_flip_and_jump_out_of_query_radius(self):
        q = np.array([[0, 0, 0], [.02, 0, 0], [0, .0001, 0], [.02 / 3, .0001 / 3, .001]], np.float32)
        model = SimpleNamespace(device=wp.get_device('cpu'), particle_count=4,
            spring_indices=wp.array([], dtype=int, device='cpu'),
            particle_colors=wp.array([0, 1, 2, 3], dtype=int, device='cpu'),
            particle_color_groups=[wp.array([i], dtype=int, device='cpu') for i in range(4)])
        binding = B.BoundConstraints(model, [3], [[0, 1, 2]], [[1 / 3] * 3], [[0, 0, .001]])
        ref = wp.array(q, dtype=wp.vec3, device='cpu')
        pos = wp.array(q, dtype=wp.vec3, device='cpu')
        delta = np.zeros_like(q)
        delta[2, 1] = -1.0001
        disp = wp.array(delta, dtype=wp.vec3, device='cpu')
        binding.prepare_increment(ref, pos, disp)
        scales = wp.ones(4, dtype=float, device='cpu')
        maximum = .00019125
        binding.limit_travel(ref, scales, maximum)
        pos.assign(q + scales.numpy()[:, None] * binding.increment.numpy())
        binding.finish_increment(pos, ref, disp)
        actual = pos.numpy()
        self.assertLess(float(np.linalg.norm(actual - q, axis=1).max()), maximum)
        self.assertGreater(float(np.cross(actual[1] - actual[0], actual[2] - actual[0])[2]), 0.)

    def test_restored_fold_contacts_preserve_explicit_seam_and_other_pieces(self):
        tri = np.array([[0, 1, 2], [3, 4, 5]], np.int32)
        edges = np.array([[-1, -1, 0, 1], [-1, -1, 3, 4], [-1, -1, 6, 7]], np.int32)
        model = SimpleNamespace(particle_count=8, tri_indices=wp.array(tri, dtype=int, ndim=2, device='cpu'),
                                edge_indices=wp.array(edges, dtype=int, ndim=2, device='cpu'))
        vf, vo = N._csr(np.array([0, 3, 6]), np.array([1, 0, 0]), 8, 2)
        ef, eo = N._csr(np.array([0, 0, 1]), np.array([1, 2, 0]), 3, 3)
        detector = SimpleNamespace(model=model, device=wp.get_device('cpu'),
            vertex_triangle_filtering_list=wp.array(vf, dtype=int, device='cpu'),
            vertex_triangle_filtering_list_offsets=wp.array(vo, dtype=int, device='cpu'),
            edge_filtering_list=wp.array(ef, dtype=int, device='cpu'),
            edge_filtering_list_offsets=wp.array(eo, dtype=int, device='cpu'))
        result = N.restore_fold_contacts(detector, [1, 1, 1, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, -1, -1], {0: {1}}, {1: {0}})
        self.assertEqual(result, {'vertex_triangle_restored': 1, 'edge_edge_restored': 1})
        off, val = detector.vertex_triangle_filtering_list_offsets.numpy(), detector.vertex_triangle_filtering_list.numpy()
        self.assertEqual(val[off[0]:off[1]].tolist(), [1])
        self.assertEqual(val[off[3]:off[4]].tolist(), [])
        self.assertEqual(val[off[6]:off[7]].tolist(), [0])

    def test_rotating_offset_jacobian_force_and_torque(self):
        rng = np.random.default_rng(429)
        for _ in range(20):
            q = np.array([[0., 0., 0.], [.02, 0., .002], [.004, .025, .003]]) + rng.normal(0, .001, (3, 3))
            w, d = np.array([.2, .3, .5]), np.array([.0003, -.0002, .001])
            p = wp.zeros(1, dtype=wp.vec3, device='cpu')
            j = wp.zeros(3, dtype=wp.mat33, device='cpu')
            wp.launch(evaluate, dim=1, inputs=[wp.array(q, dtype=wp.vec3, device='cpu'), wp.vec3(w), wp.vec3(d), p, j], device='cpu')
            J = j.numpy()
            fd = np.zeros((3, 3, 3))
            for a in range(3):
                for k in range(3):
                    lo, hi = q.copy(), q.copy()
                    lo[a, k] -= 1e-6
                    hi[a, k] += 1e-6
                    fd[a, :, k] = (reference(hi, w, d) - reference(lo, w, d)) / 2e-6
            self.assertLess(float(np.abs(J - fd).max()), 2e-6)
            load = np.array([.3, -.2, .7])
            force = np.array([jj.T @ load for jj in J])
            self.assertLess(float(np.linalg.norm(force.sum(0) - load)), 2e-6)
            self.assertLess(float(np.linalg.norm(np.cross(q, force).sum(0) - np.cross(reference(q, w, d), load))), 1e-7)

    def test_truncation_preserves_other_colors_accepted_motion(self):
        q = np.array([[0., 0., 0.], [.02, 0., 0.], [0., .02, 0.], [.006, .01, .001]], np.float32)
        model = SimpleNamespace(device=wp.get_device('cpu'), particle_count=4,
            spring_indices=wp.array([], dtype=int, device='cpu'),
            particle_colors=wp.array([0, 1, 2, 3], dtype=int, device='cpu'),
            particle_color_groups=[wp.array([i], dtype=int, device='cpu') for i in range(4)])
        binding = B.BoundConstraints(model, [3], [[0, 1, 2]], [[.2, .3, .5]], [[0, 0, .001]])
        ref = wp.array(q, dtype=wp.vec3, device='cpu')
        accepted = q.copy()
        accepted[:3, 0] += .0001
        pos = wp.array(accepted, dtype=wp.vec3, device='cpu')
        binding.place(pos)
        accepted = pos.numpy()
        proposal = accepted - q
        proposal[0, 0] += .00001
        binding.prepare_increment(ref, pos, wp.array(proposal, dtype=wp.vec3, device='cpu'))
        scales = wp.array([.5, 1, 1, 1], dtype=float, device='cpu')
        binding.limit_travel(ref, scales, wp.inf)
        after = accepted + scales.numpy()[:, None] * binding.increment.numpy()
        np.testing.assert_array_equal(after[1], accepted[1])
        self.assertGreater(float(after[0, 0] - accepted[0, 0]), 0.)

    def test_rotating_offset_travel_limit_against_thin_obstacle(self):
        tri = np.array([[-.01, -.006, 0.], [.01, -.006, 0.], [0., .012, 0.]])
        def rotate(deg):
            t = np.radians(deg)
            return tri @ np.array([[np.cos(t), 0, -np.sin(t)], [0, 1, 0], [np.sin(t), 0, np.cos(t)]]).T
        start, end = rotate(-10), rotate(10)
        for offset in (0., .001):
            w, d = np.ones(3) / 3, np.array([0., 0., offset])
            q = np.r_[start, reference(start, w, d)[None]].astype(np.float32)
            proposal = np.r_[end, reference(end, w, d)[None]].astype(np.float32) - q
            model = SimpleNamespace(device=wp.get_device('cpu'), particle_count=4,
                spring_indices=wp.array([], dtype=int, device='cpu'),
                particle_colors=wp.array([0, 1, 2, 3], dtype=int, device='cpu'),
                particle_color_groups=[wp.array([i], dtype=int, device='cpu') for i in range(4)])
            binding = B.BoundConstraints(model, [3], [[0, 1, 2]], [w], [d])
            pos = wp.array(q, dtype=wp.vec3, device='cpu')
            binding.prepare_increment(pos, pos, wp.array(proposal, dtype=wp.vec3, device='cpu'))
            scales = wp.ones(4, dtype=float, device='cpu')
            binding.limit_travel(pos, scales, .00019125)
            alpha = float(scales.numpy()[0])
            unbounded = [reference(start + t * (end - start), w, d)[2] for t in np.linspace(0, 1, 101)]
            bounded = [reference(start + t * alpha * (end - start), w, d)[2] for t in np.linspace(0, 1, 101)]
            if offset:
                # Both endpoint chords miss this plane; the unbounded normal arc crosses it.
                self.assertGreater(max(unbounded), .00099)
                self.assertLess(max(bounded), .00099)
            else:
                self.assertLess(max(np.abs(bounded)), 1e-10)


if __name__ == '__main__':
    unittest.main()
