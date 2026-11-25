# SPDX-License-Identifier: GPL-3.0-or-later
"""Document and piece ownership across actual operations, not just metadata.

Finite nondegenerate synthetic triangles isolate ownership from solver quality.
"""
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
import bpy
import numpy as np

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import ac9_cloth_sim as A
from ac9_cloth_sim import control_basis as CB, display_state as DS, dress as D
from ac9_cloth_sim import garment_owner as GO, piece_hide as PH, piece_move as PM
from ac9_cloth_sim import placed_view as PV, selection as SEL, store as ST
from ac9_cloth_sim.core import draft, spec as S
A.register()


class Ownership(unittest.TestCase):
    measures = {}

    def setUp(self):
        self.ctx = bpy.context
        self.scene = self.ctx.scene
        self.docs = []
        self.flat = np.array([[0,0],[40,0],[0,40],[100,0],[140,0],[100,40]],float)
        self.X = np.column_stack((self.flat[:,0],np.zeros(6),self.flat[:,1]))
        self.F = np.array([[0,1,2],[3,4,5]])
        area = np.linalg.norm(np.cross(self.X[self.F[:,1]]-self.X[self.F[:,0]],self.X[self.F[:,2]]-self.X[self.F[:,0]]),axis=1)/2
        self.assertTrue(np.isfinite(self.X).all());np.testing.assert_array_equal(area,[800,800])

    def tearDown(self):
        PM.clear()
        for t in self.docs:
            for ob in list(bpy.data.objects):
                if ob.get(GO.GARMENT)==t or ob.get(GO.PLACED)==t or ob.get('ac9_pattern_spec')==t:
                    bpy.data.objects.remove(ob,do_unlink=True)
            for me in list(bpy.data.meshes):
                if me.get(GO.PLACED)==t and me.users<=1:
                    bpy.data.meshes.remove(me)
            if bpy.data.texts.get(t.name)==t:bpy.data.texts.remove(t)
        ST.invalidate();DS.invalidate()

    def doc(self,name='OwnedShape'):
        sp=S.new_spec(name)
        for i,n in enumerate(('A','B')):draft.add_polygon(sp,self.flat[3*i:3*i+3].tolist(),name=n)
        sp['placement']={n:dict(guide='torso',theta=40. if n=='A' else 140.,h=0.,anchor=[0,0]) for n in ('A','B')}
        sp['guides']={'torso':dict(o=[0,0,0],R=np.eye(3).tolist(),r=100.,h0=0.,h1=100.)}
        t=ST.new_text(self.ctx,name);self.docs.append(t);ST.put(self.ctx,sp)
        # A document switch publishes its flat representatives, as the actual UI
        # does. Missing/foreign flat objects are now rejected before operators.
        from ac9_cloth_sim import pattern_view
        pattern_view.rebuild(self.ctx,sp,ST.get(self.ctx)[1],switch_document=True)
        return t,sp

    def g(self,sp):
        return D.garment_object(self.ctx,D.garment_name(sp),self.flat,self.F,np.array([0,1]),self.X,['A','B'],editing_basis={'A':CB.PLACED,'B':CB.PLACED})

    def placed(self,sp):
        coll=bpy.data.collections.get(PV.COLL_NAME)
        if coll is None:coll=bpy.data.collections.new(PV.COLL_NAME);self.scene.collection.children.link(coll)
        me=bpy.data.meshes.new('OwnershipPlaced');me.from_pydata((self.X[:3]*.001).tolist(),[],[(0,1,2)])
        ob=bpy.data.objects.new('OwnershipPlaced',me);coll.objects.link(ob);PV._tag(ob,sp,'A',self.flat[:3])
        self.ctx.view_layer.update();return ob

    def faces(self,g):
        self.ctx.view_layer.update();return len(g.evaluated_get(self.ctx.evaluated_depsgraph_get()).data.polygons)

    def co(self,ob):
        a=np.empty((len(ob.data.vertices),3));ob.data.vertices.foreach_get('co',a.ravel());return a.copy()

    def test_same_name_new_publication_and_drop_leave_first_document_unchanged(self):
        t,sp=self.doc();g=self.g(sp);ob=self.placed(sp);DS.apply(self.ctx,sp)
        before=self.co(g);uid=g.data.session_uid;obuid=ob.session_uid
        self.assertEqual(self.faces(g),1);self.assertFalse(ob.hide_get())
        self.assertEqual(bpy.ops.ac9_cloth_sim.new_pattern('EXEC_DEFAULT',name=sp['name']),{'FINISHED'})
        self.docs.append(ST.text(self.ctx));self.assertEqual(self.faces(g),1);self.assertFalse(ob.hide_get())
        self.assertEqual(len(D.garment_specs()[g.name]['pieces']),2)
        sp2=S.loads(S.dumps(sp));ST.put(self.ctx,sp2)
        g2=self.g(sp2)
        self.assertNotEqual(g2,g);self.assertEqual(g2.get(GO.GARMENT),self.docs[-1]);self.assertEqual(D.garment(sp2),g2)
        self.assertEqual(D.garment(sp,owner=t),g)
        self.assertNotIn('A',PV.objects(sp2));PV.drop(sp2)
        self.assertEqual(g.data.session_uid,uid);self.assertEqual(ob.session_uid,obuid)
        self.assertEqual(self.faces(g),1);self.assertFalse(ob.hide_get());np.testing.assert_array_equal(self.co(g),before)
        self.measures['same_name']={'foreign_faces':self.faces(g),'foreign_placed_retained':True,'distinct_mesh':g2!=g}

    def test_archives_and_eye_follow_text_and_remain_separate_after_rename(self):
        t,sp=self.doc();g=self.g(sp);ob=self.placed(sp);PV.keep(ob,sp);saved=PV._kept(sp)['A']
        PH.set_hidden(self.ctx,sp,['B']);self.assertEqual(PH.names(self.scene,sp),{'B'})
        t2,sp2=self.doc();g2=self.g(sp2);self.placed(sp2);PV.keep_shape(sp2,'A',self.flat[:3],np.array([[0,1,2]]),self.X[:3])
        self.assertNotEqual(PV._kept(sp2)['A'],saved);PH.set_hidden(self.ctx,sp2,['A'])
        self.assertEqual(PH.names(self.scene,sp,owner=t),{'B'});self.assertEqual(PH.names(self.scene,sp2),{'A'})
        t.name+='Renamed';self.assertEqual(PH.names(self.scene,sp,owner=t),{'B'})
        PV.drop(sp2);self.assertIn(saved,list(bpy.data.meshes));self.assertEqual(saved.get(GO.PLACED),t)
        DS._sync_all();self.assertEqual(DS.snapshot(sp,g,owner=t)['B']['hidden'],True)
        self.assertEqual(D.garment(sp,owner=t),g);self.assertEqual(D.garment(sp2,owner=t2),g2)

    def test_legacy_owner_is_only_inherited_when_unique(self):
        t,sp=self.doc();g=self.g(sp);ob=self.placed(sp)
        del g[GO.GARMENT];del ob[GO.PLACED]
        self.assertEqual(D.garment(sp),g);self.assertEqual(PV.objects(sp)['A'],ob)
        self.scene[PH.KEY]=json.dumps({sp['name']:['B']});DS.apply(self.ctx,sp)
        self.assertEqual(g[GO.GARMENT],t);self.assertEqual(ob[GO.PLACED],t);self.assertEqual(PH.names(self.scene,sp),{'B'})
        del ob[GO.PLACED];t2,sp2=self.doc();ST.put(self.ctx,sp2)
        self.assertEqual(PV.objects(sp2),{});PV.drop(sp2)
        self.assertIn(ob,list(bpy.data.objects));self.assertEqual(g[GO.GARMENT],t)

    def test_eye_is_scoped_to_scene_and_text(self):
        t,sp=self.doc();PH.set_hidden(self.ctx,sp,['B'])
        other=bpy.data.scenes.new('OwnershipOtherScene')
        try:
            self.ctx.window.scene=other;other.ac9_cloth_sim.spec_text=t
            self.assertEqual(PH.names(other,sp),set());PH.set_hidden(self.ctx,sp,['A'])
            self.assertEqual(PH.names(other,sp),{'A'});self.assertEqual(PH.names(self.scene,sp),{'B'})
        finally:
            self.ctx.window.scene=self.scene;bpy.data.scenes.remove(other)

    def test_partial_reset_selects_and_moves_the_displayed_shape_only(self):
        t,sp=self.doc();g=self.g(sp);g.data.pop(CB.PROP);g.data.pop(CB.OWNER)
        me=bpy.data.meshes.new('OwnershipBody');me.from_pydata([(10,0,0),(10.1,0,0),(10,.1,0),(10,0,.1)],[],[(0,2,1),(0,1,3),(0,3,2),(1,2,3)])
        body=bpy.data.objects.new('OwnershipBody',me);self.scene.collection.objects.link(body)
        self.scene.ac9_cloth_sim.body=body;self.scene.ac9_cloth_sim.mesh_size=20
        try:
            raw=self.co(g);SEL.apply(self.ctx,['A'],'SET',active='A',view='world')
            self.assertEqual(bpy.ops.ac9_cloth_sim.reset('EXEC_DEFAULT'),{'FINISHED'})
            sp=ST.get(self.ctx)[0];ob=PV.objects(sp)['A'];SEL.project(self.ctx,['A'],active='A',view='world')
            self.assertEqual(CB.contexts(self.ctx,sp)['A']['object'],ob);self.assertEqual(CB.basis(g)['B'],CB.UNKNOWN)
            self.assertTrue(ob.select_get());self.assertEqual(len(PM.handles()),0);self.assertEqual(self.ctx.view_layer.objects.active,ob)
            old=np.array(ob.matrix_world.translation);self.assertEqual(bpy.ops.transform.translate(value=(.01,0,0)),{'FINISHED'})
            self.ctx.view_layer.update();delta=np.array(ob.matrix_world.translation)-old
            np.testing.assert_allclose(delta,[.01,0,0],atol=1e-7);np.testing.assert_array_equal(self.co(g),raw)
            self.measures['partial_reset']={'selected_placed':True,'hidden_handles':len(PM.handles()),'move_mm':(delta*1000.).tolist(),'B_basis':CB.basis(g)['B']}
        finally:bpy.data.objects.remove(body,do_unlink=True)

    def test_nonparticipant_adoption_cannot_change_another_placed_pieces_control(self):
        t,sp=self.doc();sp['pairs']=[['A','B']];ST.put(self.ctx,sp);g=self.g(sp);ob=self.placed(sp)
        PV.write_local_result(ob,(self.X[:3]+[0,8,0])*.001);DS.apply(self.ctx,sp)
        a=CB.contexts(self.ctx,sp)['A'];targets=CB.transform_targets(self.ctx,['A'],sp)
        self.assertEqual(a['object'],ob);self.assertEqual(a['basis'],CB.RESULT)
        CB.adopt(self.ctx,g,['B']);DS.apply(self.ctx,sp)
        self.assertEqual(CB.contexts(self.ctx,sp)['A'],a);self.assertEqual(CB.transform_targets(self.ctx,['A'],sp),targets)

    def test_import_allocates_a_label_without_reusing_the_current_manual_mesh(self):
        from ac9_cloth_sim import operators
        t,sp=self.doc();g=self.g(sp);raw=self.co(g)
        incoming=S.loads(S.dumps(sp));incoming['source']={'kind':'bom'}
        events=[0]
        def bounded(frame,event,arg):
            if frame.f_code==operators._replace_bom.__code__:
                events[0]+=1
                if events[0]>10000:raise AssertionError('Creation label allocation did not terminate')
            return bounded
        previous=sys.gettrace();sys.settrace(bounded)
        try:operators._replace_bom(self.ctx,incoming)
        finally:sys.settrace(previous)
        self.docs.append(ST.text(self.ctx))
        self.assertNotEqual(incoming['name'],sp['name']);self.assertEqual(g[GO.GARMENT],t)
        np.testing.assert_array_equal(self.co(g),raw)

    def test_plain_user_mesh_with_a_generated_label_is_never_adopted(self):
        t,sp=self.doc();me=bpy.data.meshes.new('OrdinaryGeometry')
        me.from_pydata((self.X[:3]*.001).tolist(),[],[(0,1,2)])
        ob=bpy.data.objects.new(D.garment_name(sp),me);self.scene.collection.objects.link(ob)
        uid=me.session_uid
        try:
            ST.put(self.ctx,sp);self.assertNotIn(GO.GARMENT,ob)
            g=self.g(sp);self.assertNotEqual(g,ob);self.assertEqual(ob.data.session_uid,uid)
        finally:bpy.data.objects.remove(ob,do_unlink=True)

    def test_referenced_notes_with_a_version_are_not_garment_documents(self):
        t,sp=self.doc();notes=bpy.data.texts.new('ReferencedNotes');self.docs.append(notes)
        notes.use_fake_user=True;notes.from_string(json.dumps({'version':S.SPEC_VERSION}))
        self.assertNotIn(notes,[owner for owner,doc in D.garment_records()])

    def test_renamed_text_and_mesh_owner_cannot_be_claimed_by_a_new_document(self):
        t,sp=self.doc();g=self.g(sp);t.name='IndependentDocument';del g[GO.GARMENT]
        t2,sp2=self.doc();self.assertIsNone(D.garment(sp2));DS.apply(self.ctx,sp2)
        self.assertNotIn(GO.GARMENT,g);self.assertEqual(g.data[CB.OWNER],t)
        self.assertEqual(len([owner for owner,doc in D.garment_records() if doc['name']==sp['name']]),2)
        DS._sync_all();self.assertEqual(g[GO.GARMENT],t)

    def test_mirror_auxiliaries_are_document_scoped_and_foreign_legacy_ones_survive(self):
        t,sp=self.doc();ob=self.placed(sp);center=PV.center_empty(self.ctx,sp);PV._add_mirror(ob,center,'B')
        mirror=PV._mirror_empty(self.ctx,sp,piece='A');self.ctx.view_layer.update()
        def evaluated():
            self.ctx.view_layer.update();return self.co(ob.evaluated_get(self.ctx.evaluated_depsgraph_get()))
        before=evaluated()
        t2,sp2=self.doc();sp2['guides']['torso']['o'][0]=100.;ST.put(self.ctx,sp2)
        self.assertNotEqual(PV.center_empty(self.ctx,sp2),center);self.assertNotEqual(PV._mirror_empty(self.ctx,sp2,piece='A'),mirror)
        np.testing.assert_array_equal(evaluated(),before)
        foreign=bpy.data.objects.new('ForeignLegacyCenter',None);foreign[PV.GCENTER_PROP]='A different garment'
        PV._collection(self.ctx).objects.link(foreign)
        try:PV.drop(sp2);self.assertIn(foreign,list(bpy.data.objects))
        finally:bpy.data.objects.remove(foreign,do_unlink=True)
        self.measures['mirror_foreign_max_mm']=float(np.abs(evaluated()-before).max()*1000.)

    def test_archived_local_input_restoration_retains_its_document(self):
        t,sp=self.doc();ob=self.placed(sp);PV.write_local_result(ob,(self.X[:3]+[0,8,0])*.001);PV.drop(sp)
        t2,sp2=self.doc();PV.keep_shape(sp2,'A',self.flat[:3],np.array([[0,1,2]]),self.X[:3]);other=PV._kept(sp2)['A']
        self.scene.ac9_cloth_sim.spec_text=t;ST.invalidate()
        self.assertEqual(PV.restore_local_inputs(sp,['A']),{'A'});self.assertIn('A',PV._kept(sp))
        self.assertEqual(PV._kept(sp)['A'][GO.PLACED],t);self.assertEqual(PV._kept(sp2,owner=t2)['A'],other)

    def test_resume_signature_tracks_other_garments_but_not_the_active_mesh(self):
        from ac9_cloth_sim import live_resume, result_reference
        t,sp=self.doc();first=self.g(sp);t2,sp2=self.doc();second=self.g(sp2)
        with tempfile.TemporaryDirectory() as directory:
            for i,g in enumerate((first,second)):
                where=str(Path(directory)/str(i))
                result_reference.write_result(where,'from mesh',['A','B'],[0,3],self.F,np.array([0,1]),self.flat,self.X)
                g['ac9_job']=where;self.assertEqual(D.job_of(g),where)
            def capture():return live_resume.capture(self.ctx,6.,spec=sp2)['environment']
            before=capture();first.data.vertices[0].co.z+=.012;first.data.update();self.ctx.view_layer.update()
            changed=capture();self.assertNotEqual(before,changed)
            second.data.vertices[0].co.z+=.012;second.data.update();self.ctx.view_layer.update();self.assertEqual(changed,capture())

    def test_save_load_restores_two_documents_and_selection_target(self):
        t,sp=self.doc();g=self.g(sp);ob=self.placed(sp);DS.apply(self.ctx,sp);PH.set_hidden(self.ctx,sp,['B'])
        t2,sp2=self.doc();g2=self.g(sp2);ob2=self.placed(sp2);DS.apply(self.ctx,sp2)
        textnames=[t.name,t2.name];objectnames=[g.name,g2.name,ob.name,ob2.name]
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'ownership.blend');bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.ctx=bpy.context;self.scene=self.ctx.scene;self.docs=[bpy.data.texts[n] for n in textnames]
            for i,owner in enumerate(self.docs):
                self.scene.ac9_cloth_sim.spec_text=owner;sp=ST.get(self.ctx)[0]
                g=D.garment(sp);ob=PV.objects(sp)['A'];self.assertEqual(g.name,objectnames[i]);self.assertEqual(ob.name,objectnames[i+2])
                self.assertEqual(PH.names(self.scene,sp),{'B'} if i==0 else set())
                SEL.project(self.ctx,['A'],active='A',view='world');self.assertTrue(ob.select_get());self.assertEqual(len(PM.handles()),0)

    def test_new_document_undo_redo_keeps_the_other_document(self):
        t,sp=self.doc();g=self.g(sp);ob=self.placed(sp);DS.apply(self.ctx,sp)
        names=(t.name,g.name,ob.name)
        self.ctx.preferences.edit.use_global_undo=True;bpy.ops.ed.undo_push(message='Ownership before new')
        self.assertEqual(bpy.ops.ac9_cloth_sim.new_pattern('EXEC_DEFAULT',True,name=sp['name']),{'FINISHED'})
        newname=ST.text(self.ctx).name
        self.assertEqual(bpy.ops.ed.undo(),{'FINISHED'})
        self.docs=[bpy.data.texts[names[0]]];self.assertEqual(self.faces(bpy.data.objects[names[1]]),1)
        self.assertFalse(bpy.data.objects[names[2]].hide_get())
        self.assertEqual(bpy.ops.ed.redo(),{'FINISHED'})
        self.docs=[bpy.data.texts[names[0]],bpy.data.texts[newname]]
        self.assertEqual(self.faces(bpy.data.objects[names[1]]),1);self.assertFalse(bpy.data.objects[names[2]].hide_get())

    def test_source_uses_document_resolver_not_name_lookups(self):
        root=Path(__file__).resolve().parents[1]
        bad=[];label_checks=[]
        for path in root.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf8'))
            for node in ast.walk(tree):
                if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='get'
                    and isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='objects'
                    and any(isinstance(n,ast.Call) and (getattr(n.func,'attr',None)=='garment_name' or getattr(n.func,'id',None)=='garment_name') for n in node.args)):
                    if path.name=='operators.py' and any(isinstance(f,ast.FunctionDef) and f.name=='_replace_bom' and f.lineno<=node.lineno<=f.end_lineno for f in tree.body):
                        label_checks.append(path.name)
                    else:bad.append((path.name,node.lineno))
        self.assertEqual(bad,[])
        self.assertEqual(label_checks,['operators.py'])


result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Ownership))
Path(args[1]).write_text(json.dumps({'count':result.testsRun,'health':{'finite_vertices':6,'area_mm2':[800,800],'purpose':'ownership, not physical quality'},'measurements':Ownership.measures,'failures':[tb for _,tb in result.failures],'errors':[tb for _,tb in result.errors]},indent=2),encoding='utf8')
if not result.wasSuccessful():raise RuntimeError('Display ownership contract failed')
