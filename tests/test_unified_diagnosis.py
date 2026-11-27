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
 def test_overview_has_separate_rows_without_changing_notice(self):
  a=shape([[0,0,0],[16,0,0],[0,16,0]]);a['V']=np.array([[0,0],[10,0],[0,10]],float)
  rec=dict(found=N.measure({'a':a},['a'],None,lambda n:None,links()),selected_scope=False,start=[])
  rows=N.summary_lines(rec)
  self.assertEqual(len(rows),4)
  self.assertIn('Checked all',rows[0]);self.assertIn('Body/obstacles',rows[1])
  self.assertIn('intersections',rows[2]);self.assertIn('Maximum stretch',rows[3])
  self.assertEqual(N.summary(rec),rows[0]+': '+'; '.join(rows[1:]))
 def test_one_display_group_has_all_four_choices(self):
  from types import SimpleNamespace
  events=[]
  class Layout:
   def label(self,**kw):pass
   def column(self,**kw):return self
   def row(self,**kw):events.append('row');return self
   def prop(self,*args,**kw):raise AssertionError('duplicate dropdown')
   def prop_enum(self,data,prop,value,**kw):events.append(value)
  p=bpy.context.scene.ac9_cloth_sim;old=p.show_overlays
  try:
   p.show_overlays=True
   N.draw_display(Layout(),bpy.context)
   self.assertEqual(events,['row','PROBLEMS','STRETCH','row','SIDE','NONE'])
  finally:p.show_overlays=old
 def test_problem_hud_keeps_spots_without_repeating_the_list(self):
  from ac9_cloth_sim import diagnose as DG,dress as D,text_band,ui
  p=bpy.context.scene.ac9_cloth_sim;old=p.show_overlays
  try:
   p.show_overlays=True;p.diagnosis_display='PROBLEMS'
   rows=[]
   with patch.object(ui,'view_text_origin',return_value=(24,700)),patch.object(DG.store,'get',return_value=({'name':'test'},{})),patch.object(DG.PV,'layout_empty',return_value=object()),patch.object(DG,'_draw_spots') as spots,patch.object(DG,'_draw_stretch_spot') as peak,patch.object(N,'problem_lines',side_effect=AssertionError('list belongs to panel')),patch.object(D,'run_preparing',return_value=None),patch.object(text_band,'draw',side_effect=lambda fid,items:rows.extend(items)):
    DG._draw_px()
    self.assertEqual(spots.call_count,2);self.assertEqual(peak.call_count,1);self.assertEqual(rows,[])
   with patch.object(ui,'view_text_origin',return_value=(24,700)),patch.object(DG.store,'get',return_value=({'name':'test'},{})),patch.object(DG.PV,'layout_empty',return_value=object()),patch.object(DG,'_draw_spots'),patch.object(DG,'_draw_stretch_spot'),patch.object(D,'run_preparing',return_value=(2.,)),patch.object(D,'run_kernels_built',return_value=False),patch.object(text_band,'draw',side_effect=lambda fid,items:rows.extend(items)):
    DG._draw_px()
    self.assertEqual(len(rows),1);self.assertIn('Preparing',rows[0][2])
  finally:p.show_overlays=old
 def test_stretch_only_is_reported_and_located_without_switching(self):
  from mathutils import Matrix
  from ac9_cloth_sim import pattern_view as PV
  for ratio,marked in ((1.,False),(1.6,True)):
   a=shape([[0,0,0],[10*ratio,0,0],[0,10*ratio,0]])
   a['V']=np.array([[0,0],[10,0],[0,10]],float);a['owner']=object()
   found=N.measure({'a':a},['a'],None,lambda n:None,links())
   rec=dict(found=found,selected_scope=False,start=[],stale=False,pending=False,frame=bpy.context.scene.frame_current)
   self.assertIn(f'x{ratio:.2f}',N.notice(rec));self.assertEqual(len(found['a']['cross_t']),0)
   with patch.object(N,'record',return_value=rec),patch.object(PV,'layout_matrix',return_value=Matrix.Identity(4)):
    spot=N.stretch_spot(bpy.context,{})
    self.assertEqual(spot is not None,marked)
    if spot:
     np.testing.assert_allclose(spot[2],[16/3000,16/3000,0],atol=1e-9)
     np.testing.assert_allclose(spot[3],[10/3000,10/3000,0],atol=1e-9)
    rec['pending']=True;self.assertIsNone(N.stretch_spot(bpy.context,{}))
    rec['pending']=False;rec['stale']=True;self.assertIsNone(N.stretch_spot(bpy.context,{}))
 def test_maximum_uses_diagnosed_scope_and_stable_tie(self):
  a=shape([[0,0,0],[16,0,0],[0,16,0]]);a['V']=np.array([[0,0],[10,0],[0,10]],float)
  b=shape([[100,0,0],[130,0,0],[100,30,0]]);b['V']=a['V'].copy()
  found=N.measure({'a':a,'b':b},['a'],None,lambda n:None,links())
  self.assertEqual(N.stretch_peak({'found':found})[0],'a');self.assertAlmostEqual(found['a']['stretch'],1.6)
  found['z']=dict(found['a']);self.assertEqual(N.stretch_peak({'found':{'z':found['z'],'a':found['a']}})[0],'a')
 def test_unchecked_material_cannot_supply_a_maximum(self):
  a=shape([[0,0,0],[16,0,0],[0,16,0]]);a['V']=np.array([[0,0],[10,0],[0,10]],float)
  good=N.measure({'a':a},['a'],None,lambda n:None,links())['a']
  b=dict(a,flat_checked=False)
  bad=N.measure({'b':b},['b'],None,lambda n:None,links())['b']
  self.assertIsNone(N.stretch_peak({'found':{'b':bad}}))
  self.assertIn('not checked',N.stretch_summary({'found':{'b':bad}}))
  self.assertIn('Checked pieces only',N.stretch_summary({'found':{'a':good,'b':bad}}))
 def test_maximum_face_keeps_material_and_world_centroid(self):
  from mathutils import Matrix
  from ac9_cloth_sim import pattern_view as PV
  a=shape([[0,0,0],[10,0,0],[0,10,0],[100,0,0],[120,0,0],[100,20,0]])
  a['T']=np.array([[0,1,2],[3,4,5]]);a['V']=np.array([[0,0],[10,0],[0,10],[30,0],[40,0],[30,10]],float);a['owner']=object()
  found=N.measure({'a':a},['a'],None,lambda n:None,links())
  self.assertEqual(found['a']['stretch_face'],1)
  with patch.object(N,'current',return_value={'found':found}),patch.object(PV,'layout_matrix',return_value=Matrix.Identity(4)):
   spot=N.stretch_spot(bpy.context,{})
   np.testing.assert_allclose(spot[2],np.mean(a['Y'][3:],axis=0)*.001,atol=1e-8)
   np.testing.assert_allclose(spot[3],list(np.mean(a['V'][3:],axis=0)*.001)+[0],atol=1e-8)
 def test_unchecked_material_map_has_no_fabricated_one(self):
  from types import SimpleNamespace
  from ac9_cloth_sim import diagnose as DG
  c=SimpleNamespace(area=None)
  a=shape([[0,0,0],[10,0,0],[0,10,0]])
  shapes={'missing':(np.zeros((3,2)),a['T'],a['Y']*.001,False),
          'valid':(a['V'],a['T'],a['Y']*.001,False)}
  with patch.object(DG,'_d',{'key':None}),patch.object(DG,'_pieces_now',return_value=(shapes,'test')):
   data=DG._data(c,{},None)
   self.assertEqual(set(data['per']),{'valid'})
   self.assertEqual([n for n,_m,_x in DG.summary(data['per'])],['valid'])
   self.assertIn('missing',shapes) # Face Side's geometry was not removed.
 def test_unplaced_scope_is_in_partial_stretch_notice(self):
  a=shape([[0,0,0],[16,0,0],[0,16,0]]);a['V']=np.array([[0,0],[10,0],[0,10]],float)
  found=N.measure({'a':a},['a'],None,lambda n:None,links())
  self.assertIn('not checked: 1 pieces',N.stretch_summary(dict(found=found,unavailable=['unplaced'])))
 def test_material_reader_rejects_inconsistent_shared_vertex_uv(self):
  from ac9_cloth_sim import diagnose as DG
  mesh=bpy.data.meshes.new('stretch_uv_contract')
  mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[],[(0,1,2),(0,2,3)])
  ob=bpy.data.objects.new('stretch_uv_contract',mesh)
  try:
   self.assertIsNone(DG._garment_flat(ob))
   uv=mesh.uv_layers.new(name='UVMap')
   for loop in mesh.loops:uv.data[loop.index].uv=mesh.vertices[loop.vertex_index].co.xy*.01
   self.assertIsNotNone(DG._garment_flat(ob))
   uv.data[3].uv.x+=.001 # one shared vertex differs by 1 mm in material space
   self.assertIsNone(DG._garment_flat(ob))
   uv.data[3].uv.x-=.001
   self.assertIsNotNone(DG._garment_flat(ob))
  finally:
   bpy.data.objects.remove(ob);bpy.data.meshes.remove(mesh)
 def test_hidden_maximum_is_not_replaced_by_another_piece(self):
  from types import SimpleNamespace
  from ac9_cloth_sim import diagnose as DG
  c=SimpleNamespace(region=object(),region_data=object())
  with patch.object(N,'stretch_spot',return_value=(object(),'hidden',None,None,2.)),patch.object(DG.VV,'object_decoration_allowed',return_value=False),patch.object(DG.PV,'piece_objects',return_value={}),patch.object(DG,'_seen',side_effect=AssertionError('hidden')):
   self.assertIsNone(DG._draw_stretch_spot(c,{}))
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
  with patch.object(N,'run',return_value=rec),patch.object(C.store,'get',return_value=({'pieces':[]},{})):
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
