# SPDX-License-Identifier: GPL-3.0-or-later
import sys,io,json,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import cloth_seams as C,dress as D,display_state as DS,result_reference as RR


class SewingCorrespondence(unittest.TestCase):
 def setUp(self):
  self.side=lambda n,e:dict(piece=n,**{'from':[e,0.], 'to':[e,1.], 'dir':1})
  self.s1=dict(id='s1',a=self.side('a','e1'),b=self.side('b','e1'))
  self.s2=dict(id='s2',a=self.side('a','e2'),b=self.side('b','e2'))
  self.spec=dict(name='test',pieces=[dict(name=n,edges=[]) for n in ('a','b')],seams=[self.s1])
  self.old=dict(self.spec,seams=[self.s1,self.s2])
  self.g=object();self.flat=np.array([[0,0],[1,0],[0,1],[10,0],[11,0],[10,1]],float)
  self.idx=dict(flat=self.flat,F=np.array([[0,1,2],[3,4,5]]),fp=np.array([0,1]))
  self.shapes={n:dict(source='result',owner=self.g,ids=np.arange(3)+3*i,V=self.flat[3*i:3*i+3],T=np.array([[0,1,2]]),flat_checked=True) for i,n in enumerate(('a','b'))}
 def read(self,old=None,seam_ids=None,edges=None):
  raw=json.dumps(self.old if old is None else old)
  ref=SimpleNamespace(index=self.idx,names=['a','b'],stitches=lambda:np.array([[0,3],[1,4]]) if edges is None else edges,seams=lambda:seam_ids,dressed_spec_text=lambda:raw)
  with patch.object(D,'garment',return_value=self.g),patch.object(DS,'job',return_value='fake'),patch.object(RR,'open_result',return_value=ref),patch.object(D,'_build_cached',side_effect=ValueError('no exact fallback')):
   return C.read(bpy.context,self.spec,self.shapes)
 def test_removed_seam_without_edge_identity_is_not_reused(self):
  r=self.read();self.assertIn(('a','b'),r['unknown']);self.assertFalse(r['pairs'])
 def test_identified_edges_keep_only_current_seam(self):
  r=self.read(seam_ids=np.array(['s1','s2']))
  self.assertFalse(r['unknown']);self.assertEqual(r['pairs'][('a','b')],{(0,0)})
 def test_malformed_saved_spec_is_local_uncertainty(self):
  for old in ([],{'name':'test','pieces':[],'seams':[{'id':'bad','a':None,'b':{}}]}):
   self.assertIn(('a','b'),self.read(old)['unknown'])
 def test_changed_notches_do_not_reuse_saved_edges(self):
  old=json.loads(json.dumps(self.spec));old['pieces'][0]['notches']=[dict(id='n',edge='e1',t=.5)]
  self.assertIn(('a','b'),self.read(old,seam_ids=np.array(['s1','s1']))['unknown'])
 def test_duplicate_seam_identity_is_not_reused(self):
  old=dict(self.spec,seams=[self.s1,dict(self.s2,id='s1')])
  result=self.read(old,seam_ids=np.array(['s1','s1']))
  self.assertIn(('a','b'),result['unknown']);self.assertFalse(result['pairs'])
 def test_other_garment_spec_and_invalid_indices_are_rejected(self):
  old=dict(self.spec,name='other')
  self.assertIn(('a','b'),self.read(old)['unknown'])
  self.assertIn(('a','b'),self.read(self.spec,edges=np.array([[-1,3]]))['unknown'])
 def test_truncated_zip_does_not_abort_diagnosis(self):
  with tempfile.TemporaryDirectory() as folder:
   Path(folder,'scene.idx.npz').write_bytes(b'PK\x03\x04broken')
   with patch.object(D,'garment',return_value=self.g),patch.object(DS,'job',return_value=folder),patch.object(D,'_build_cached',side_effect=ValueError('no exact fallback')):
    self.assertIn(('a','b'),C.read(bpy.context,self.spec,self.shapes)['unknown'])


if __name__=='__main__':
 import ac9_cloth_sim
 ac9_cloth_sim.register()
 args=sys.argv[sys.argv.index('--')+1:];log=io.StringIO()
 r=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SewingCorrespondence))
 Path(args[1]).write_text(json.dumps(dict(passed=r.wasSuccessful(),tests=r.testsRun,log=log.getvalue()),indent=2),encoding='utf8')
 if not r.wasSuccessful():raise RuntimeError(log.getvalue())
