"""Dense inactive seam samples must not collapse active sewing stations."""
import importlib,sys,types,unittest
from pathlib import Path
import numpy as np

package=types.ModuleType('_pattern_cluster_core')
package.__path__=[str(Path(__file__).resolve().parents[1]/'core')]
sys.modules[package.__name__]=package
PB=importlib.import_module(package.__name__+'.pattern_build')

def rect(name,x,w=27.,height=50.):
    q=[[x,0.],[x+w,0.],[x+w,height],[x,height]]
    return {'name':name,'edges':[{'id':str(i),'pts':[q[i],q[(i+1)%4]]}for i in range(4)]}
def side(name,edge,a=0.,b=1.,direction=1):
    return {'piece':name,'from':[str(edge),a],'to':[str(edge),b],'dir':direction}
def seam(name,a,b,enabled=True):return {'id':name,'a':a,'b':b,'enabled':enabled}
def base():return {'name':'dense samples','pieces':[rect('A',0),rect('B',50),rect('C',100,24.)],'seams':[]}
def pairs(pat,name):return pat['stitch'][pat['stitch_seam']==name]

class SeamClusterTests(unittest.TestCase):
    def test_dense_inactive_samples_do_not_collapse_active_stations(self):
        s=base();s['seams']=[seam('main',side('A',0),side('B',0)),
            seam('off',side('A',0,0.,3.35/27.),side('C',0),False)]
        pat,_=PB.build(s,6.);rows=pairs(pat,'main')
        self.assertEqual(len(rows),6)
        self.assertEqual(len(np.unique(rows[:,1])),6)
        self.assertEqual(len(np.unique(rows[:,3])),6)

    def test_dense_samples_across_closed_outline_join_do_not_collapse_stations(self):
        s=base();s['seams']=[seam('main',side('A',3,1.,0.,-1),side('B',3,1.,0.,-1)),
            seam('off',side('A',3,1.,1.-3.5/50.,-1),side('C',0),False)]
        pat,_=PB.build(s,6.);rows=pairs(pat,'main')
        self.assertEqual(len(rows),10)
        self.assertEqual(len(np.unique(rows[:,1])),10)

    def test_coarse_piece_wrap_respects_fine_seam_spacing(self):
        for coarse in (0,1):
            with self.subTest(coarse_piece=coarse):
                s=base();s['pieces'][coarse]['mesh_mm']=20.;s['pieces'][1-coarse]['mesh_mm']=6.
                s['seams']=[seam('main',side('A',3,1.,0.,-1),side('B',3,1.,0.,-1))]
                pat,_=PB.build(s,20.);rows=pairs(pat,'main')
                self.assertEqual(len(rows),10)
                self.assertEqual(len(np.unique(rows[:,1])),10)
                self.assertEqual(len(np.unique(rows[:,3])),10)

    def test_mixed_h_corner_snap_does_not_merge_two_sewing_stations(self):
        s=base();s['pieces'][0]['mesh_mm']=20.;s['pieces'][1]['mesh_mm']=6.
        s['seams']=[seam('main',side('A',0,.1,.9),side('B',0,.1,.9))]
        pat,_=PB.build(s,20.);rows=pairs(pat,'main')
        self.assertEqual(len(rows),5)
        self.assertEqual(len(np.unique(rows[:,1])),5)
        self.assertEqual(len(np.unique(rows[:,3])),5)
        # Coarse-piece corner snapping remains the existing policy (not a new strict fine-grid guarantee).
        np.testing.assert_allclose(pat['V0'][rows[[0,-1],1]],[[0.,0.],[27.,0.]])

    def test_merge_distance_is_strict_at_threshold(self):
        for width,distinct in ((6.9999,1),(7.,2),(7.0001,2)):
            with self.subTest(width=width):
                s={'name':'strict threshold','pieces':[rect('A',0,w=40.),rect('B',60,w=40.)],'seams':[]}
                s['seams']=[seam('main',side('A',0,.25,(10.+width)/40.),side('B',0,.25,(10.+width)/40.))]
                pat,_=PB.build(s,20.);rows=pairs(pat,'main')
                self.assertEqual(len(np.unique(rows[:,1])),distinct)
                self.assertEqual(len(np.unique(rows[:,3])),distinct)

    def test_true_shared_corner_remains_one_vertex(self):
        s=base();s['seams']=[seam('one',side('A',0),side('B',0)),seam('two',side('A',1),side('C',1))]
        pat,_=PB.build(s,6.);a=pairs(pat,'one')[:,1];b=pairs(pat,'two')[:,1]
        shared=set(a)&set(b)
        self.assertEqual(len(shared),1)
        np.testing.assert_array_equal(pat['V0'][next(iter(shared))],[27.,0.])

if __name__=='__main__':unittest.main()
