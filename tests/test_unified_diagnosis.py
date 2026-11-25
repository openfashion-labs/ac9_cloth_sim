# SPDX-License-Identifier: GPL-3.0-or-later
import sys,io,json,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import diagnosis as N, place_check as C, properties as P

args=sys.argv[sys.argv.index('--')+1:]
def shape(Y,source='placed'):
 Y=np.array(Y,float)
 return dict(V=Y[:,:2].copy(),T=np.array([[0,1,2]]),Y=Y,flat_checked=True,source=source)
def links(pairs=None,unknown=()):
 return {'pairs':pairs or {},'unknown':set(unknown)}
class UnifiedDiagnosis(unittest.TestCase):
 def test_self_intersection_marker_uses_both_material_sides(self):
  a=shape([[-1,-1,0],[1,-1,0],[0,1,0],[-1,0,-1],[1,0,-1],[0,0,1]])
  a['T']=np.array([[0,1,2],[3,4,5]])
  a['V']=np.array([[0,0],[2,0],[1,2],[100,0],[102,0],[101,2]],float)
  f=N.measure({'a':a},['a'],None,lambda n:None,links())['a']
  self.assertEqual(len(f['spots']),2)
  np.testing.assert_allclose(f['spots'][0]['world'],f['spots'][1]['world'],atol=1e-8)
  self.assertGreater(np.linalg.norm(np.array(f['spots'][0]['f'])-f['spots'][1]['f']),.09)
 def test_selected_piece_warns_about_degenerate_counterpart(self):
  a=shape([[0,0,0],[10,0,0],[0,10,0]])
  b=shape([[1,1,0],[1,1,0],[1,1,0]])
  found=N.measure({'a':a,'b':b},['a'],None,lambda n:None,links())
  self.assertFalse(found['a']['cross_checked'])
  self.assertEqual(found['a']['unchecked_geometry'],{'b':1})
  self.assertIn('Degenerate faces not checked: 1',N.summary({'found':found}))
 def test_self_crossing_survives_unverified_other_seam_pair(self):
  a=shape([[-1,-1,0],[1,-1,0],[0,1,0],[-1,0,-1],[1,0,-1],[0,0,1]])
  a['T']=np.array([[0,1,2],[3,4,5]])
  b=shape([[30,30,0],[40,30,0],[30,40,0]])
  found=N.measure({'a':a,'b':b},['a'],None,lambda n:None,links(unknown=[('a','b')]))
  self.assertEqual(found['a']['self_t'].tolist(),[0,1]);self.assertEqual(len(found['a']['candidate_t']),0)
 def test_malformed_start_record_is_a_note_not_a_diagnosis_failure(self):
  from ac9_cloth_sim import garment_view as G,dress as D,display_state as DS
  with tempfile.TemporaryDirectory() as folder,patch.object(D,'garment',return_value=object()),patch.object(D,'has_result',return_value=True),patch.object(D,'job_of',return_value=folder),patch.object(G,'fits',return_value=True),patch.object(DS,'snapshot',return_value={}):
   # An optional file which has not been written is different from a broken one.
   self.assertEqual(G.check_lines(bpy.context,{}),[])
   for payload in ('{','{"pieces":null}','[]','{"pieces":{"a":[]}}'):
    Path(folder,'start_check.json').write_text(payload,encoding='utf8')
    notes=G.check_lines(bpy.context,{})
    self.assertEqual(len(notes),1);self.assertEqual(notes[0][0],'WARNING')
    self.assertIn('current-shape diagnosis is unaffected',notes[0][1])
   Path(folder,'start_check.json').write_text('{"pieces":{"a":{"checked":true,"over":[1]}}}',encoding='utf8')
   self.assertIn('last run did not start as shown',G.check_lines(bpy.context,{})[0][1])
 def test_missing_display_does_not_predict_sim_participation(self):
  from ac9_cloth_sim import garment_view as G,dress as D,display_state as DS
  states={n:dict(solid=True,source=source,missing=True) for n,source in [('rule','proxy'),('unplaced','none')]}
  with patch.object(D,'garment',return_value=None),patch.object(DS,'snapshot',return_value=states):
   self.assertEqual(G.check_lines(bpy.context,{'name':'test'}),[])
 def test_diagnose_reports_one_scoped_result_with_start_warning_count(self):
  a=shape([[0,0,0],[10,0,0],[0,10,0]])
  found=N.measure({'a':a},['a'],None,lambda n:None,links())
  rec=dict(found=found,selected_scope=True,start=[('INFO','separate note',()),('WARNING','separate warning',())])
  class Reporter:
   def __init__(self):self.reports=[]
   def report(self,levels,text):self.reports.append((levels,text))
  caller=Reporter()
  with patch.object(N,'run',return_value=rec),patch.object(C.store,'get',return_value=({},{})):
   self.assertEqual(C.AC9_OT_SimDiagnosePlaced.execute(caller,bpy.context),{'FINISHED'})
  self.assertEqual(len(caller.reports),1)
  self.assertIn('selected pieces',caller.reports[0][1]);self.assertIn('Start-shape warnings: 1',caller.reports[0][1])
  self.assertNotIn('separate note',caller.reports[0][1]);self.assertNotIn('separate warning',caller.reports[0][1])
 def test_empty_scope_is_not_a_clean_diagnosis(self):
  for selected in (False,True):
   text=N.notice(dict(found={},selected_scope=selected,start=[]))
   self.assertIn('not checked',text);self.assertNotIn('0 faces',text)
 def test_world_scope_restores_viewport_after_failure(self):
  area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
  region=next(r for r in area.regions if r.type=='WINDOW')
  @N.world_scope
  def inspect(context):
   self.assertIsNone(bpy.context.area)
   self.assertIsNone(bpy.context.space_data)
   self.assertEqual(context.scene,bpy.context.scene)
   raise ValueError('inspection failed')
  with bpy.context.temp_override(area=area,region=region):
   before=(bpy.context.area.as_pointer(),bpy.context.region.as_pointer())
   with self.assertRaisesRegex(ValueError,'inspection failed'):inspect(bpy.context)
   self.assertEqual(before,(bpy.context.area.as_pointer(),bpy.context.region.as_pointer()))
 def test_real_crossing_and_partner(self):
  a=shape([[0,0,0],[10,0,0],[0,10,0]])
  b=shape([[2,2,-5],[2,2,5],[8,2,0]],'result')
  found=N.measure({'a':a,'b':b},['a','b'],None,lambda n:None,links())
  self.assertEqual(found['a']['partners'],{'b':[0]});self.assertEqual(found['b']['partners'],{'a':[0]})
  self.assertEqual(found['a']['spots'][0]['x'],1)
 def test_sewn_target_endpoint_excludes_only_its_face(self):
  a=shape([[0,0,0],[10,0,0],[0,10,0]])
  b=shape([[2,2,-5],[2,2,5],[8,2,0]])
  f=N.measure({'a':a,'b':b},['a','b'],None,lambda n:None,links({('a','b'):{(0,0)}}))
  self.assertEqual(len(f['a']['cross_t']),0);self.assertEqual(len(f['b']['cross_t']),0)
  # An unrelated stitch elsewhere on these pieces does not exempt these faces.
  f=N.measure({'a':a,'b':b},['a','b'],None,lambda n:None,links({('a','b'):{(8,8)}}))
  self.assertEqual(len(f['a']['cross_t']),1)
 def test_unknown_seams_are_unchecked(self):
  a=shape([[0,0,0],[10,0,0],[0,10,0]])
  b=shape([[2,2,-5],[2,2,5],[8,2,0]])
  f=N.measure({'a':a,'b':b},['a'],None,lambda n:None,links(unknown=[('a','b')]))['a']
  self.assertFalse(f['body_checked']);self.assertFalse(f['cross_checked'])
  self.assertEqual(len(f['cross_t']),0);self.assertEqual(len(f['candidate_t']),1)
  self.assertIn('sewing unverified',N.summary({'found':{'a':f}}))
 def test_principal_stretch_matches_color(self):
  a=shape([[0,0,0],[1,0,0],[1,1,0]]);a['V']=np.array([[0,0],[1,0],[0,1]])
  f=N.measure({'a':a},['a'],None,lambda n:None,links())['a']
  self.assertAlmostEqual(f['stretch'],1.61803398875,10)
 def test_degenerate_material_not_passed(self):
  a=shape([[0,0,0],[10,0,0],[0,10,0]]);a['V'][:]=0
  f=N.measure({'a':a},['a'],None,lambda n:None,links())['a']
  self.assertFalse(f['stretch_checked']);self.assertEqual(f['invalid_material_faces'],1)
 def test_explicit_unavailable_selection_does_not_expand(self):
  with patch('ac9_cloth_sim.placement.selected_pieces',return_value=['hidden']),patch('ac9_cloth_sim.placement.with_pairs',side_effect=lambda s,n:set(n)):
   self.assertEqual(N.selected(bpy.context,{}, {'other':{}}),[])
 def test_old_overlay_choice_matches_dropdown(self):
  p=bpy.context.scene.ac9_cloth_sim
  if '_ac9_diagnosis_display' in p:del p['_ac9_diagnosis_display']
  p.show_face_side=True;p.show_stretch=True;p.show_problems=True
  self.assertEqual(p.diagnosis_display,'SIDE');self.assertEqual(N.mode(p),'SIDE')
  p.diagnosis_display='STRETCH'
  self.assertFalse(p.show_face_side);self.assertTrue(p.show_stretch);self.assertFalse(p.show_problems)
 def test_display_switch_does_not_diagnose(self):
  with patch.object(N,'run',side_effect=AssertionError('must not run')):
   for m in ['SIDE','STRETCH','PROBLEMS','NONE']:bpy.context.scene.ac9_cloth_sim.diagnosis_display=m
 def test_saved_mark_not_promoted_to_snapshot(self):
  N.clear_records();self.assertIsNone(N.current(bpy.context));self.assertEqual(N.problem_lines(bpy.context),[])
 def test_color_display_uses_only_current_snapshot(self):
  f=shape([[0,0,0],[10,0,0],[0,10,0]]);f['W']=f['Y']*.001;f['turned']=False
  rec=dict(serial=1,stale=False,pending=False,frame=bpy.context.scene.frame_current,display_shapes={'a':f})
  with patch.object(N,'record',return_value=rec),patch.object(N,'sources',side_effect=AssertionError('view must not reread')),patch('ac9_cloth_sim.view_visibility.world_names',return_value={'a'}):
   pieces,key=N.display_pieces(bpy.context,{})
   self.assertIs(pieces['a'][2],f['W'])
   rec['stale']=True;self.assertIsNone(N.display_pieces(bpy.context,{}))
 def test_stretch_legend_belongs_to_its_view(self):
  from types import SimpleNamespace
  from ac9_cloth_sim import diagnose as DG
  a=SimpleNamespace(area=SimpleNamespace(as_pointer=lambda:1));b=SimpleNamespace(area=SimpleNamespace(as_pointer=lambda:2))
  f=shape([[0,0,0],[10,0,0],[0,10,0]])
  full=({'a':(f['V'],f['T'],f['Y']*.001,False)},'a')
  with patch.object(DG,'_d',{'key':None}),patch.object(DG,'_pieces_now',side_effect=[full,({},'b'),full]),patch.object(DG,'stretches',wraps=DG.stretches) as calc:
   DG._data(a,{},None);DG._data(b,{},None);DG._data(a,{},None)
   self.assertIn('a',DG.view_stretch(a));self.assertEqual(DG.view_stretch(b),{})
   self.assertEqual(calc.call_count,1)
   DG.invalidate();self.assertEqual(DG.view_stretch(a),{})
 def test_placement_does_not_mark_or_warn_quality(self):
  from ac9_cloth_sim import placement as PL,diagnose as DG
  with patch.object(PL.PLV,'objects',return_value={}),patch.object(DG,'mark',side_effect=AssertionError('automatic red')):
   self.assertEqual(PL._start_notes(bpy.context,{},dict(in_body={'a':[0,1]},overlaps={'a':{'through':['b'],'inside':[]}},closures={})),[])
 def test_replaced_equal_body_invalidates_once(self):
  props=bpy.context.scene.ac9_cloth_sim;previous=props.body
  mesh=bpy.data.meshes.new('diagnosis_body_test');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
  a=bpy.data.objects.new('diagnosis_body_a',mesh);bpy.context.scene.collection.objects.link(a)
  b=a.copy();b.data=a.data.copy();bpy.context.scene.collection.objects.link(b)
  spec={'name':'diagnosis_test','pieces':[],'seams':[]}
  try:
   props.body=a;bpy.context.view_layer.update()
   rec=dict(stale=False,pending=False,fingerprint=N.fingerprint(bpy.context,spec,{}),cheap=N.cheap_stamp(bpy.context,spec),dependencies=N.dependencies(bpy.context,spec,{}))
   props.body=b;bpy.context.view_layer.update()
   original=N.fingerprint
   with patch.object(N,'_records',{'test':rec}),patch.object(N,'record',return_value=rec),patch.object(N.store,'get',return_value=(spec,{})),patch.object(N,'sources',return_value={}),patch.object(N,'fingerprint',wraps=original) as read:
    for _ in range(3):N.timer()
    self.assertTrue(rec['stale']);self.assertEqual(read.call_count,1)
  finally:
   props.body=previous
   bpy.data.objects.remove(a,do_unlink=True);bpy.data.objects.remove(b,do_unlink=True)

if __name__=='__main__':
 import ac9_cloth_sim
 ac9_cloth_sim.register()
 log=io.StringIO();r=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UnifiedDiagnosis))
 Path(args[1]).write_text(json.dumps(dict(passed=r.wasSuccessful(),tests=r.testsRun,log=log.getvalue()),indent=2),encoding='utf8')
 if not r.wasSuccessful():raise RuntimeError(log.getvalue())
