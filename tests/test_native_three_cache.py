import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np
from solver import native_three_cache as C


class Array:
    def __init__(self, values):self.values = np.asarray(values, np.int32)
    def numpy(self):return self.values.copy()


def fixture():
    model = NS(particle_count=6, tri_count=3, edge_count=2, spring_count=1, tet_count=0,
               particle_color_groups=[Array([0, 2]), Array([1, 3])],
               tri_indices=Array([[0, 1, 2], [1, 2, 3], [4, 0, 1]]),
               edge_indices=Array([[0, 1, 2, 3], [-1, -1, 0, 1]]),
               spring_indices=Array([[2, 3]]), tet_indices=None)
    bound = NS(vertices=Array([4]), triangles=Array([[0, 1, 2]]))
    return model, bound


class PlanCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ac9_plan_cache_test_')
        self.root = Path(self.temp.name).resolve()
        self.where = patch.object(C, 'directory', lambda: self.root)
        self.where.start()
        self.algorithm = patch.object(C, 'algorithm_id', lambda: 'fixed-test-algorithm')
        self.algorithm.start()
        self.model, self.bound = fixture()
        self.key = C.input_key(self.model, self.bound)
        self.colors = np.array([0, 1, 2, 0, -1, -1], np.int32)

    def tearDown(self):
        self.where.stop(); self.algorithm.stop()
        assert self.root.is_absolute() and self.root.name.startswith('ac9_plan_cache_test_')
        self.temp.cleanup()

    def test_round_trip_preserves_membership_labels_and_dtype(self):
        self.assertTrue(C.save(self.key, self.colors))
        actual = C.load(self.key, 6)
        self.assertTrue(np.array_equal(actual, self.colors))
        self.assertEqual(actual.dtype, np.int32)

    def test_same_size_connectivity_and_support_changes_miss(self):
        for field in ['tri_indices', 'edge_indices', 'spring_indices']:
            model = copy.deepcopy(self.model)
            a = getattr(model, field).values
            a.flat[0] = (int(a.flat[0]) + 1) % 4
            self.assertNotEqual(C.input_key(model, self.bound), self.key, field)
        bound = copy.deepcopy(self.bound); bound.triangles.values[0] = [0, 2, 3]
        self.assertNotEqual(C.input_key(self.model, bound), self.key)
        model = copy.deepcopy(self.model); model.particle_color_groups[1] = Array([1, 5])
        self.assertNotEqual(C.input_key(model, self.bound), self.key)

    def test_row_order_and_corresponding_bound_order_are_keyed(self):
        model = copy.deepcopy(self.model); model.tri_indices.values = model.tri_indices.values[::-1].copy()
        self.assertNotEqual(C.input_key(model, self.bound), self.key)
        bound = NS(vertices=Array([4, 5]), triangles=Array([[0, 1, 2], [1, 2, 3]]))
        alternate = NS(vertices=Array([5, 4]), triangles=Array([[0, 1, 2], [1, 2, 3]]))
        self.assertNotEqual(C.input_key(self.model, bound), C.input_key(self.model, alternate))

    def test_algorithm_revision_invalidates(self):
        with patch.object(C, 'algorithm_id', lambda: 'changed-test-algorithm'):
            self.assertNotEqual(C.input_key(self.model, self.bound), self.key)

    def test_full_triangle_and_edge_ranges_match_consumers(self):
        for name, count_name in [('tri_indices', 'tri_count'), ('edge_indices', 'edge_count')]:
            first = copy.deepcopy(self.model); setattr(first, count_name, 1)
            second = copy.deepcopy(first)
            values = getattr(second, name).values
            values[-1, -1] = (int(values[-1, -1]) + 1) % 4
            self.assertNotEqual(C.input_key(first, self.bound), C.input_key(second, self.bound), name)

    def test_positions_materials_and_weights_are_not_structural_key(self):
        model = copy.deepcopy(self.model); model.particle_q = Array(np.ones((6, 3)))
        model.tri_materials = Array(np.ones((3, 5)))
        bound = copy.deepcopy(self.bound); bound.weights = np.array([[.2, .3, .5]])
        self.assertEqual(C.input_key(model, bound), self.key)

    def test_invalid_input_is_rejected(self):
        for groups in [[Array([0, 1]), Array([1, 3])], [Array([0, 1, 4])], [Array([0, 1, 7])]]:
            model = copy.deepcopy(self.model); model.particle_color_groups = groups
            with self.assertRaises(ValueError):C.input_key(model, self.bound)

    def test_corrupt_version_hash_shape_and_values_are_misses(self):
        C.save(self.key, self.colors); path = C._path(self.key)
        original = json.loads(path.read_text())
        for change in [dict(version=0), dict(key='wrong'), dict(count=5),
                       dict(membership_sha256='wrong'), dict(colors=[0] * 6),
                       dict(colors=[True] * 6), dict(colors=[0.0] * 6),
                       dict(colors=[9] * 6), dict(colors=[0] * 5)]:
            path.write_text(json.dumps(dict(original, **change)))
            self.assertIsNone(C.load(self.key, 6), change)

    def test_oversized_and_truncated_data_are_misses(self):
        C.save(self.key, self.colors); path = C._path(self.key)
        for content in [b'{', b'[]', b'x' * (C.MAX_BYTES + 1)]:
            path.write_bytes(content); self.assertIsNone(C.load(self.key, 6))

    def test_failed_publication_keeps_previous_entry(self):
        C.save(self.key, self.colors); path = C._path(self.key); before = path.read_bytes()
        with patch.object(C.os, 'replace', side_effect=OSError('injected publication failure')):
            self.assertFalse(C.save(self.key, np.array([2, 1, 0, 2, -1, -1], np.int32)))
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_capacity_eviction_preserves_unrelated_files(self):
        unrelated = self.root / 'user.json'; unrelated.write_text('keep')
        with patch.object(C, 'MAX_ENTRIES', 2):
            for i in range(4):C.save(f'{i:064x}', self.colors)
        self.assertEqual(len(list(self.root.glob(C.PREFIX + '*.json'))), 2)
        self.assertEqual(unrelated.read_text(), 'keep')


if __name__ == '__main__':unittest.main()
