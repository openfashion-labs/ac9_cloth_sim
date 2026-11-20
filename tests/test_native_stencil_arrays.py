"""Independent set/count oracle; includes followers, sentinels and hostile plans."""
from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np

SOLVER=Path(__file__).resolve().parents[1]/'solver'
sys.path.insert(0,str(SOLVER))
from native_stencil_arrays import independent_rows

def oracle(rows, active, followers, supports):
    support=dict(zip(map(int,followers),supports))
    return [tuple(sorted({int(u) for v in row if v>=0 for u in support.get(int(v),[v]) if active[u]}))for row in rows]

class StencilArrayTests(unittest.TestCase):
    def test_random_rows_equal_independent_set_oracle(self):
        rng=np.random.default_rng(76)
        for count in (8,27,100):
            active=rng.random(count)>.2;followers=np.arange(count-3,count,dtype=np.int32);active[followers]=False
            supports=rng.integers(0,count-3,size=(3,3),dtype=np.int32)
            for width in (2,3,4):
                rows=rng.integers(-1,count,size=(300,width),dtype=np.int32)
                actual=independent_rows(rows,active,followers,supports)
                self.assertEqual([tuple(r[r>=0])for r in actual],oracle(rows,active,followers,supports))
    def test_bounds_and_empty_rows(self):
        self.assertEqual(independent_rows(np.empty((0,4),np.int32),np.ones(5,bool)).shape,(0,4))
        for width in (2,3,4):
            rows=independent_rows(np.empty((0,width),np.int32),np.array([1,1,1,0],bool),np.array([3],np.int32),np.array([[0,1,2]],np.int32))
            self.assertEqual(rows.shape,(0,width*3))
        for value in (-2,5):
            with self.assertRaises(ValueError):independent_rows(np.array([[value,1]]),np.ones(5,bool))
    def test_array_search_and_seeded_repair_pass_independent_count(self):
        import native_three_automatic as A
        rng=np.random.default_rng(777)
        for count in (12,30,90):
            proper=np.arange(count,dtype=np.int32)%6
            rows=[np.arange(i,i+4,dtype=np.int32) for i in range(count-3)]
            colors,report=A.find_three(rows,proper)
            self.assertTrue(report['success'])
            self.assertTrue(all(np.bincount(colors[r],minlength=3).max()<=2 for r in rows))
            repair_seed=colors.copy();repair_seed[0]=(repair_seed[0]+1)%3
            repaired,report=A.find_three(rows,proper,initial_colors=repair_seed)
            self.assertTrue(report['success'])
            self.assertTrue(all(np.bincount(repaired[r],minlength=3).max()<=2 for r in rows))

if __name__=='__main__':unittest.main()
