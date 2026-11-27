"""Material crease ordering must follow the line through turns, not one PCA axis."""
import unittest
import numpy as np
import bpy
from ac9_cloth_sim import place_fold as F


class CreaseOrder(unittest.TestCase):
    def info(self,path,roll):
        V=np.asarray(roll+[[3,3],[7,3]],float)
        return dict(V=V,Xid=np.column_stack([V/1000,np.zeros(len(V))]),
                    roll=np.arange(len(roll)),fold_path=np.asarray(path,float))

    def test_u_and_reversed_path_follow_adjacent_material_points(self):
        roll=[[0,10],[0,5],[0,0],[5,0],[10,0],[10,5],[10,10]]
        for path in [roll,roll[::-1],[[0,10],[0,10],[0,0],[10,0],[10,10]]]:
            info=self.info(path,roll);ft=F._feet(info,[7,8])
            order=ft[5];steps=np.linalg.norm(np.diff(info['V'][order],axis=0),axis=1)
            np.testing.assert_allclose(steps,5)
            self.assertEqual(order.tolist(),list(range(7)) if path[0]==[0,10] else list(range(6,-1,-1)))
            self.assertTrue(all(np.isfinite(a).all() for a in ft))

    def test_straight_order_and_existing_seam_fallback(self):
        roll=[[10,0],[0,0],[5,0]]
        info=self.info([[0,0],[10,0]],roll)
        self.assertEqual(F._feet(info,[3,4])[5].tolist(),[1,2,0])
        info.pop('fold_path')
        ft=F._feet(info,[3,4]);self.assertTrue(all(np.isfinite(a).all() for a in ft))
        np.testing.assert_allclose(np.linalg.norm(np.diff(info['V'][ft[5]],axis=0),axis=1),5)

    def test_closed_path_keeps_legacy_feet_when_authored_start_changes(self):
        roll=[[0,0],[5,0],[10,0],[10,5],[10,10],[5,10],[0,10],[0,5]]
        info=self.info([],roll);baseline=F._feet(info,[8,9])
        for path in [roll+[roll[0]],roll[2:]+roll[:2]+[roll[2]],roll[::-1]+[roll[-1]]]:
            info.update(fold_closed=True,fold_path=np.asarray(path,float))
            actual=F._feet(info,[8,9])
            for before,after in zip(baseline,actual):np.testing.assert_array_equal(before,after)


if __name__=='__main__':unittest.main(argv=['test_fold_path_order'])
