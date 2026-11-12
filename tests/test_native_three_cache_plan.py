"""Execute the real plan constructors using CPU array adapters, no GPU context."""
import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np
from solver import native_three_cache as C


class Array:
    def __init__(self, values, **kwargs):self.values = np.asarray(values, dtype=np.int32).copy()
    def numpy(self):return self.values.copy()
    def assign(self, values):self.values[:] = values
    @property
    def size(self):return self.values.size


def model():
    return NS(particle_count=5, tri_count=3, edge_count=1, spring_count=1, tet_count=0,
              particle_color_groups=[Array([i])for i in range(4)],
              particle_colors=Array([0, 1, 2, 3, -1]), device='cpu-adapter',
              tri_indices=Array([[0, 1, 2], [1, 2, 3], [4, 0, 1]]),
              edge_indices=Array([[0, 1, 2, 3]]), spring_indices=Array([[0, 3]]),
              tet_indices=None, tri_materials=Array([[1, 1], [1, 1], [0, 0]]),
              edge_bending_properties=Array([[1, 0]]))


class PlanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
        with patch.dict(sys.modules, {'warp': NS(array=Array), 'native_three_cache': C}):
            cls.module = importlib.import_module('native_three_automatic')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ac9_plan_constructor_')
        self.root = Path(self.temp.name).resolve()
        self.where = patch.object(C, 'directory', lambda: self.root); self.where.start()
        self.alg = patch.object(C, 'algorithm_id', lambda: 'constructor-test'); self.alg.start()
        self.modules = patch.dict(sys.modules, {'native_three_cache': C}); self.modules.start()
        self.bound = NS(vertices=Array([4]), triangles=Array([[0, 1, 2]]))

    def tearDown(self):
        self.where.stop(); self.alg.stop(); self.modules.stop()
        assert self.root.is_absolute() and self.root.name.startswith('ac9_plan_constructor_')
        self.temp.cleanup()

    def test_hit_skips_search_preserves_labels_and_installs_same_groups(self):
        first = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        self.assertFalse(first.summary['cache']['hit'])
        with patch.object(self.module, 'ColoringPlan', side_effect=AssertionError('cache repeated coloring')):
            second = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        self.assertTrue(second.summary['cache']['hit'])
        self.assertTrue(np.array_equal(first.colors, second.colors))
        first.install(); second.install()
        self.assertEqual([g.numpy().tolist()for g in first.groups], [g.numpy().tolist()for g in second.groups])

    def test_occupancy_invalid_cache_rebuilds(self):
        first = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        key = first.summary['cache']['key']
        C.save(key, np.array([0, 0, 0, 1, -1], np.int32))
        next_plan = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        self.assertFalse(next_plan.summary['cache']['hit'])
        self.assertTrue(np.array_equal(first.colors, next_plan.colors))

    def test_current_material_reactivation_is_not_hidden_by_hit(self):
        self.module.AutomaticThreeBatchPlan(model(), self.bound)
        changed = model(); changed.tri_materials.values[2, 0] = 1
        with patch.object(self.module, 'ColoringPlan', side_effect=AssertionError('reactivation rebuilt instead of refusing')):
            with self.assertRaisesRegex(ValueError, 'masked bound triangle'):
                self.module.AutomaticThreeBatchPlan(changed, self.bound)
        late = self.module.AutomaticThreeBatchPlan(changed, self.bound, validate_materials=False)
        with self.assertRaisesRegex(ValueError, 'masked bound triangle'):late.validate_excluded()

    def test_cache_off_and_hit_have_identical_membership(self):
        initial = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        with patch.object(C, 'enabled', lambda: False):
            uncached = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        self.assertTrue(np.array_equal(initial.colors, uncached.colors))
        self.assertIsNone(uncached.summary['cache']['key'])


if __name__ == '__main__':unittest.main()
