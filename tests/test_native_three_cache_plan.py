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

    def test_hinge_change_hits_with_a_plan_certified_on_the_new_hinges(self):
        first = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        changed = model()
        changed.edge_indices = Array([[0, 1, 2, 3], [1, 2, 3, 0]]); changed.edge_count = 2
        changed.edge_bending_properties = Array([[1, 0], [1, 0]])
        with patch.object(self.module, 'ColoringPlan', side_effect=AssertionError('cache repeated coloring')):
            second = self.module.AutomaticThreeBatchPlan(changed, self.bound)
        self.assertTrue(second.summary['cache']['hit'])
        self.assertEqual(second.summary['structural_rows_checked'], 5)      # 3 triangles (one masked) + 2 hinges + 1
        self.assertTrue(np.array_equal(first.colors, second.colors))

    def test_occupancy_invalid_cache_rebuilds_same_plan_and_certifies(self):
        first = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        key = first.summary['cache']['key']
        C.save(key, np.array([0, 0, 0, 1, -1], np.int32))
        next_plan = self.module.AutomaticThreeBatchPlan(model(), self.bound)
        self.assertFalse(next_plan.summary['cache']['hit'])
        self.assertEqual(next_plan.summary['cache']['status'], 'rebuilt')
        self.assertTrue(np.array_equal(next_plan.colors, first.colors))
        self.module.TwoPerStencilBatch(model(), self.bound, next_plan.colors)

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

    def test_search_success_cannot_bypass_independent_certificate(self):
        bogus=np.array([0,0,0,1,-1],np.int32)
        with patch.object(self.module,'find_three',return_value=(bogus,{'success':True})):
            with self.assertRaisesRegex(ValueError,'More than two'):
                self.module.AutomaticThreeBatchPlan(model(),self.bound)

    def test_inactive_or_missing_cached_membership_is_a_miss(self):
        first=self.module.AutomaticThreeBatchPlan(model(),self.bound)
        bad=first.colors.copy();bad[0]=-1
        C.save(first.summary['cache']['key'],bad)
        repaired=self.module.AutomaticThreeBatchPlan(model(),self.bound)
        self.assertEqual(repaired.summary['cache']['status'],'rebuilt')

    def test_invalid_cache_uses_only_original_fresh_search(self):
        first=self.module.AutomaticThreeBatchPlan(model(),self.bound)
        C.save(first.summary['cache']['key'],np.array([0,0,0,1,-1],np.int32))
        search=self.module.find_three
        def fresh_only(stencils,proper,**kwargs):
            self.assertIsNone(kwargs.get('initial_colors'))
            return search(stencils,proper,**kwargs)
        with patch.object(self.module,'find_three',side_effect=fresh_only)as observed:
            plan=self.module.AutomaticThreeBatchPlan(model(),self.bound)
        self.assertEqual(observed.call_count,1)
        self.assertEqual(plan.summary['cache']['status'],'rebuilt')
        self.assertTrue(np.array_equal(first.colors,plan.colors))

    def test_all_hinges_excluded_still_supports_fresh_and_cache(self):
        changed=model();changed.edge_indices=Array([[4,0,1,2]])
        changed.edge_bending_properties=Array([[0,0]])
        first=self.module.AutomaticThreeBatchPlan(changed,self.bound)
        self.assertEqual(first.summary['structural_rows_checked'],3)
        second=self.module.AutomaticThreeBatchPlan(changed,self.bound)
        self.assertTrue(second.summary['cache']['hit'])


if __name__ == '__main__':unittest.main()
