import copy,unittest
import numpy as np
from ac9_cloth_sim.core.triangulation_cache import Cache,input_key

class TriangulationCache(unittest.TestCase):
    def setUp(self):
        self.pattern=dict(names=np.array(['piece']),nb=np.array([3]),twin_src=np.array([-1]),
            V0=np.array([[0.,0.],[1.,0.],[0.,1.]]),C0=np.array([[0,1],[1,2],[2,0]]))
    def test_every_cdt_and_hole_input_invalidates(self):
        before=input_key(self.pattern)
        for key in self.pattern:
            pattern=copy.deepcopy(self.pattern)
            if key=='names':pattern[key]=np.array(['renamed'])
            else:pattern[key].flat[0]+=1
            self.assertNotEqual(input_key(pattern),before,key)
        pattern=dict(self.pattern,H0=np.array([[.1,.1],[.2,.1],[.1,.2]]),Hn0=np.array([3]))
        self.assertNotEqual(input_key(pattern),before)
        other=copy.deepcopy(pattern);other['H0'][0,0]+=.01
        self.assertNotEqual(input_key(pattern),input_key(other))
        other=copy.deepcopy(pattern);other['Hn0'][0]=2
        self.assertNotEqual(input_key(pattern),input_key(other))
    def test_results_do_not_share_mutable_faces_or_warning_lists(self):
        cache=Cache();faces=[np.array([[0,1,2]])];bad=['piece'];key=input_key(self.pattern)
        cache.put(key,faces,bad);faces[0][0,0]=99;bad.clear()
        a,b=cache.get(key);self.assertEqual(a[0][0,0],0);self.assertEqual(b,['piece'])
        a[0][0,0]=88;b.clear();a,b=cache.get(key)
        self.assertEqual(a[0][0,0],0);self.assertEqual(b,['piece'])
    def test_cache_is_bounded(self):
        cache=Cache(limit=2)
        for key in ('a','b','c'):cache.put(key,[np.zeros((1,3),int)],[])
        self.assertIsNone(cache.get('a'));self.assertEqual(len(cache.entries),2)
