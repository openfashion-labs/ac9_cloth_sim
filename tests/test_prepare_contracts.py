"""Read-only preparation/physical source contracts and exclusive temporary directory ownership."""
import copy,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import dress as DR, placed_view as PLV, prepare_build as PB, store as ST, live_resume as LR
from ac9_cloth_sim.core import prepare_plan as PP, shape_source as SH, spec as S, draft as D, fabric as FA


class PreparePlans(unittest.TestCase):
    def setUp(self):
        self.sp=S.new_spec('PurePrepare');D.add_polygon(self.sp,[[0,0],[100,0],[100,100],[0,100]],name='A')
        D.add_polygon(self.sp,[[120,0],[150,0],[150,30],[120,30]],name='Unplaced')
        self.sp['placement']={'A':dict(guide='torso',theta=90.,h=0.,anchor=[50,50])}
        self.sp['guides']={'torso':dict(o=[7,0,0],R=np.eye(3).tolist(),r=100,h0=-100,h1=100,points=[])}
        self.sp['unknown_test_field']={'keep':42}
        self.panel=SimpleNamespace(body=object(),fabric_weight=140.,fabric_bending=2.,preset='')
    def test_resolve_keeps_input_and_unknown_fields(self):
        before=S.dumps(self.sp)
        with patch.object(PP.np,'savez',side_effect=AssertionError('plan cannot save')):
            plan=PP.resolve(self.sp,self.panel,environ={})
        self.assertEqual(S.dumps(self.sp),before);self.assertIsNone(plan.migrated)
        self.assertEqual(plan.center_x,7.);self.assertEqual(plan.spec['unknown_test_field'],{'keep':42})
        self.assertTrue(self.sp['pieces'][1].get('enabled',True));self.assertFalse(plan.spec['pieces'][1]['enabled'])
    def test_legacy_migration_is_a_copy_and_second_plan_is_idempotent(self):
        self.sp.pop('fabrics',None);self.sp.pop('default_fabric_id',None);before=S.dumps(self.sp)
        plan=PP.resolve(self.sp,self.panel,environ={});self.assertEqual(S.dumps(self.sp),before)
        self.assertIsNotNone(plan.migrated);self.assertFalse(FA.needs_migrate(plan.migrated))
        saved=S.dumps(plan.migrated);again=PP.resolve(plan.migrated,self.panel,environ={})
        self.assertIsNone(again.migrated);self.assertEqual(S.dumps(plan.migrated),saved)
    def test_invalid_guide_and_local_leave_source_unchanged(self):
        # no body is needed (pieces laid flat, 2026-11-10); a piece round a guide that is gone is refused
        before=S.dumps(self.sp);self.panel.body=None
        # a piece round a guide with no body: refused before a run (design review 2026-11-10)
        with self.assertRaisesRegex(ValueError,'Pick the body'):PP.resolve(self.sp,self.panel,environ={})
        mixed=copy.deepcopy(self.sp);mixed['placement']['Unplaced']={'guide':S.FLAT,'flat':{'o':[0,0,2],'R':np.eye(3).tolist()},'anchor':[135,15]}
        with self.assertRaisesRegex(ValueError,'Pick the body'):PP.resolve(mixed,self.panel,environ={})
        self.panel.body=object()
        gone=copy.deepcopy(self.sp);gone['placement']['A']['guide']='arm_l'
        with self.assertRaisesRegex(ValueError,'Detect Guides'):PP.resolve(gone,self.panel,environ={})
        self.panel.body=object()
        with self.assertRaisesRegex(ValueError,'No placed pieces'):PP.resolve(self.sp,self.panel,environ={},local={'Unplaced'})
        self.assertEqual(S.dumps(self.sp),before)
    def test_flat_without_body_or_guides(self):
        sp=copy.deepcopy(self.sp);sp['guides']={};self.panel.body=None
        sp['placement']={'A':{'guide':S.FLAT,'flat':{'o':[0,0,2],'R':np.eye(3).tolist()},'anchor':[50,50]}}
        plan=PP.resolve(sp,self.panel,environ={})
        self.assertEqual([p['name'] for p in plan.spec['pieces'] if p.get('enabled',True)],['A'])
    def test_native_default_does_not_gain_experimental_false_keys(self):
        plan=PP.resolve(self.sp,self.panel,environ={})
        self.assertNotIn('native_experimental',plan.settings)
        self.assertNotIn('native_experimental_variant',plan.settings)
    def test_assembly_keeps_arrays_and_exact_indices(self):
        pattern=dict(names=np.array(['A','B']),V0=np.array([[0,0],[1,0],[0,1.]]),
                     V1=np.array([[2,0],[3,0],[2,1.]]),stitch=np.array([[0,1,1,2]],np.int32))
        tris=[np.array([[0,1,2]],np.int64)]*2;world=[np.c_[pattern['V0'],np.zeros(3)],np.c_[pattern['V1'],np.ones(3)]]
        old=copy.deepcopy(pattern);arr=PP.assemble(pattern,tris,world)
        np.testing.assert_array_equal(arr.triangles,[[0,1,2],[3,4,5]])
        np.testing.assert_array_equal(arr.stitches,[[1,5]]);np.testing.assert_array_equal(arr.face_pieces,[0,1])
        self.assertEqual(arr.offsets,[0,3]);np.testing.assert_array_equal(arr.initial,np.vstack(world))
        for key in old:np.testing.assert_array_equal(pattern[key],old[key])


class PhysicalSources(unittest.TestCase):
    def setUp(self):self.objects=[];self.meshes=[]
    def tearDown(self):
        for ob in self.objects:bpy.data.objects.remove(ob,do_unlink=True)
        for me in self.meshes:
            if me.users==0:bpy.data.meshes.remove(me)
    def source(self,offset=0.):
        flat=np.array([[0.,0.],[100.,0.],[0.,100.]])+[offset,0]
        world=np.array([[0.,0.,0.],[100.,0.,0.],[0.,100.,0.]])
        me=PLV._mesh('OwnedShapeSnapshot',flat,np.array([[0,1,2]]),world,np.zeros(3));self.meshes.append(me)
        ob=bpy.data.objects.new('OwnedShapeSnapshot',me);self.objects.append(ob)
        PLV._collection(bpy.context).objects.link(ob);sp=S.new_spec('Snapshot');PLV._tag(ob,sp,'A',flat)
        return ob,flat,world,sp
    def test_snapshot_has_identity_world_units_and_no_display_thickness(self):
        ob,flat,world,sp=self.source();ob.modifiers.new('User thickness','SOLIDIFY');ob[PLV.FOLD_PROP]=True
        snap=PLV.shape_snapshot(ob,only_moved=True)
        self.assertEqual((snap.piece,snap.object_uid,snap.mesh_uid),('A',ob.session_uid,ob.data.session_uid))
        self.assertIn('fold',snap.provenance);self.assertEqual(len(snap.world_mm),3)
        np.testing.assert_allclose(snap.world_mm,world,atol=1e-5,rtol=0)
        old=snap.world_mm.copy();ob.data.vertices[0].co.z=.01
        np.testing.assert_array_equal(snap.world_mm,old)
    def test_generated_shape_is_not_hand_until_transformed(self):
        ob,flat,world,sp=self.source();self.assertIsNone(PLV.shape_snapshot(ob,only_moved=True))
        ob.location.x=.01;bpy.context.view_layer.update();snap=PLV.shape_snapshot(ob,only_moved=True)
        self.assertEqual(snap.provenance,('edited',));self.assertAlmostEqual(snap.world_mm[0,0],10.,places=5)
    def test_legacy_computed_flag_and_multiple_provenance_are_kept(self):
        ob,*_=self.source();ob[PLV.HAND_PROP]=True;ob[PLV.SEAM_PROP]=True;ob[PLV.LAID_PROP]=True
        snap=PLV.shape_snapshot(ob,only_moved=True);self.assertEqual(snap.provenance,('result','seam','lay'))
        self.assertNotIn(PLV.SIM_PROP,ob)
    def test_hand_mapping_is_invariant_to_layout_origin(self):
        displacements=[]
        for offset in (0.,100000.,1000000.):
            ob,flat,world,sp=self.source(offset);ob[PLV.FOLD_PROP]=True
            target=flat.copy();target[0,0]+=1.
            pat=dict(names=np.array(['A']),V0=target)
            got,took,rep=DR._hand_placed(sp,pat,[world.copy()])
            self.assertEqual(took,{0});displacements.append(float(got[0][0,0]-world[0,0]))
            # Retire the current linked source before the next same-piece object is created.
            for collection in list(ob.users_collection):collection.objects.unlink(ob)
        # The Blender mesh stores meters as float32; the measured interpolation roundoff is 1.49e-8 mm.
        np.testing.assert_allclose(displacements,[1.,1.,1.],atol=1e-6,rtol=0)
    def test_absolute_layout_tolerance_is_origin_independent(self):
        for offset in (0.,1e5,1e6):
            a=np.array([[offset,0.],[offset+100,100.]])
            self.assertTrue(SH.same_layout(a,a+1e-7));self.assertFalse(SH.same_layout(a,a+1e-3))


class JobOwnership(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory(prefix='prepare_contract_',dir=os.environ.get('AC9_CONTRACT_OUTPUT'));self.root=Path(self.tmp.name).resolve()
    def tearDown(self):self.tmp.cleanup()
    def test_same_name_allocates_new_directory_and_keeps_existing_content(self):
        existing=self.root/'same';existing.mkdir();(existing/'keep.txt').write_text('old')
        own=PB.OwnedJobDirectory.create(self.root,'same');self.assertNotEqual(Path(own),existing)
        own.remove();self.assertFalse(Path(own).exists());self.assertEqual((existing/'keep.txt').read_text(),'old')
    def test_changed_marker_is_not_owned_cleanup(self):
        own=PB.OwnedJobDirectory.create(self.root,'changed');own.marker.write_text('different')
        own.remove();self.assertTrue(Path(own).exists())
    def test_reused_path_is_not_the_same_directory(self):
        own=PB.OwnedJobDirectory.create(self.root,'reused');own.release();Path(own).rmdir()
        Path(own).mkdir();(Path(own)/'keep.txt').write_text('new owner')
        own.remove();self.assertEqual((Path(own)/'keep.txt').read_text(),'new owner')
    def test_successful_release_removes_only_ownership_marker(self):
        own=PB.OwnedJobDirectory.create(self.root,'success');(Path(own)/'job.json').write_text('{}')
        own.release();own.remove();self.assertTrue((Path(own)/'job.json').exists());self.assertFalse(own.marker.exists())
    def test_changed_owner_is_neither_accepted_nor_deleted(self):
        own=PB.OwnedJobDirectory.create(self.root,'changed_during_build')
        build=PB.PreparationBuild(bpy.context,lambda context:own);build.job_dir()
        own.marker.write_text('new owner',encoding='ascii')
        with self.assertRaisesRegex(ValueError,'directory changed'):build.finish((dict(dir=str(own)),()))
        build.abort();self.assertTrue(Path(own).is_dir());self.assertEqual(own.marker.read_text(),'new owner')
    def test_public_job_directory_is_unique_and_orders_within_the_same_second(self):
        self.assertFalse(bpy.data.filepath)  # The runner owns a factory-startup file.
        with patch.object(DR.tempfile,'gettempdir',lambda:str(self.root)),patch.object(DR.time,'strftime',return_value='20000101_000000'),patch.object(DR.time,'time_ns',side_effect=[1000,1001]):
            first=DR.job_dir(bpy.context);(Path(first)/'keep.txt').write_text('first job')
            second=DR.job_dir(bpy.context)
        self.assertNotEqual(first,second);self.assertLess(first,second)
        self.assertEqual((Path(first)/'keep.txt').read_text(),'first job')


class ResumeResources(unittest.TestCase):
    """Only the preparation ownership boundary is mocked; this does not validate a solver checkpoint."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='prepare_resume_',dir=os.environ.get('AC9_CONTRACT_OUTPUT'))
        self.root=Path(self.tmp.name).resolve();self.previous_body=bpy.context.scene.ac9_cloth_sim.body
        bpy.ops.mesh.primitive_cube_add(size=.1);self.body=bpy.context.object
        bpy.context.scene.ac9_cloth_sim.body=self.body
        self.sp=S.new_spec('OwnedResumeBoundary');D.add_polygon(self.sp,[[0,0],[100,0],[100,100],[0,100]],name='A')
        self.sp['placement']={'A':dict(guide='torso',theta=90.,h=0.,anchor=[50,50])}
        self.sp['guides']={'torso':dict(o=[0,0,0],R=np.eye(3).tolist(),r=100,h0=-100,h1=100,points=[])}
        self.text=ST.new_text(bpy.context,self.sp['name']);ST.write(bpy.context,self.sp,keep=True)
        self.stack=[];self.count=0;self.obstacles=[]
        def allocate(context,**options):
            self.count+=1;return PB.OwnedJobDirectory.create(self.root,'job_'+str(self.count))
        self.patcher=patch.object(DR,'job_dir',allocate);self.patcher.start()
        self.temp_patcher=patch.object(DR.tempfile,'gettempdir',lambda:str(self.root));self.temp_patcher.start()
        self.source,self.arrays=DR.prepare(bpy.context,20.)
        np.save(Path(self.source['dir'])/'final.npy',self.arrays[3]*.001)
        (Path(self.source['dir'])/'keep.txt').write_text('previous result')
    def tearDown(self):
        self.patcher.stop();self.temp_patcher.stop()
        bpy.context.scene.ac9_cloth_sim.fast_live=False
        bpy.context.scene.ac9_cloth_sim.body=self.previous_body
        for ob in self.obstacles:
            mesh=ob.data;bpy.data.objects.remove(ob,do_unlink=True)
            if mesh.users==0:bpy.data.meshes.remove(mesh)
        me=self.body.data;bpy.data.objects.remove(self.body,do_unlink=True)
        if me.users==0:bpy.data.meshes.remove(me)
        bpy.data.texts.remove(self.text);ST._discard_cache();self.tmp.cleanup()
    def callback(self,outcome):
        def resume(context,h,previous,shown,folder,gate,last_frame):
            self.stack.append((str(folder),gate));(Path(folder)/'owned_copy.txt').write_text('copied input')
            if outcome=='raise':raise RuntimeError('injected resume copy failure')
            if outcome=='decline':return None,'owned resume refusal'
            info=dict(self.source,dir=str(folder),kind='live')
            return (info,self.arrays),None
        return resume
    def obstacle(self):
        mesh=bpy.data.meshes.new('OwnedStaleObstacle');mesh.from_pydata((self.arrays[3]*.001).tolist(),[],self.arrays[1].tolist())
        ob=bpy.data.objects.new('AC9_Garment_StaleObstacle',mesh);bpy.context.scene.collection.objects.link(ob);self.obstacles.append(ob)
        ob['ac9_job']=self.source['dir'];ob.modifiers.new('Owned stale cache','MESH_CACHE')
        return ob
    def test_native_accepted_resume_hands_over_only_accepted_job(self):
        with patch.object(LR,'prepare',self.callback('accept')):
            info,arrays=DR.prepare(bpy.context,20.,live=True,continue_job=self.source['dir'],continue_shown=self.arrays[3])
        self.assertEqual(info['dir'],self.stack[0][0]);self.assertTrue((Path(info['dir'])/'owned_copy.txt').exists())
        self.assertFalse((Path(info['dir'])/'.ac9_prepare_owner').exists())
        self.assertEqual(len(list(self.root.glob('job_*'))),2)
    def test_declined_resume_removes_abandoned_directory(self):
        with patch.object(LR,'prepare',self.callback('decline')):
            info,arrays=DR.prepare(bpy.context,20.,live=True,continue_job=self.source['dir'],continue_shown=self.arrays[3])
        self.assertFalse(Path(self.stack[0][0]).exists());self.assertTrue(Path(info['dir']).is_dir())
        self.assertEqual(len(list(self.root.glob('job_*'))),2)
    def test_resume_copy_failure_keeps_previous_result_and_no_new_job(self):
        with patch.object(LR,'prepare',self.callback('raise')):
            with self.assertRaisesRegex(RuntimeError,'resume copy failure'):
                DR.prepare(bpy.context,20.,live=True,continue_job=self.source['dir'],continue_shown=self.arrays[3])
        self.assertEqual(len(list(self.root.glob('job_*'))),1)
        self.assertEqual((Path(self.source['dir'])/'keep.txt').read_text(),'previous result')
    def test_accurate_accepted_resume_removes_the_redundant_built_job(self):
        bpy.context.scene.ac9_cloth_sim.fast_live=True
        with patch.object(LR,'prepare',self.callback('accept')):
            info,arrays=DR.prepare(bpy.context,20.,live=True,continue_job=self.source['dir'],continue_shown=self.arrays[3])
        self.assertEqual(info['dir'],self.stack[0][0]);self.assertFalse((self.root/'job_2').exists())
        self.assertEqual(len(list(self.root.glob('job_*'))),2)
    def test_legacy_resume_gate_uses_planned_migration_then_commits_it(self):
        legacy=S.loads(S.dumps(self.sp));legacy.pop('fabrics');legacy.pop('default_fabric_id')
        ST.write(bpy.context,legacy,keep=True);body=self.text.as_string()
        with patch.object(LR,'prepare',self.callback('accept')):
            DR.prepare(bpy.context,20.,live=True,continue_job=self.source['dir'],continue_shown=self.arrays[3])
        import json
        self.assertIn('fabrics',json.loads(self.stack[0][1]['spec']))
        self.assertNotEqual(self.text.as_string(),body);self.assertFalse(FA.needs_migrate(ST.get(bpy.context)[0]))
    def test_obstacle_plan_rejects_transform_and_in_place_geometry_changes(self):
        ob=self.obstacle();plan=DR.plan_frame_drop(bpy.context,ob);self.assertIsNotNone(plan)
        ob.location.x=.01;bpy.context.view_layer.update()
        with self.assertRaisesRegex(ValueError,'obstacle changed'):plan.require_current()
        ob.location.x=0.;bpy.context.view_layer.update();ob.data.vertices[0].co.z+=.01
        with self.assertRaisesRegex(ValueError,'obstacle changed'):plan.require_current()
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in ob.modifiers),1)
    def test_obstacle_plan_rejects_a_new_shared_job_reader(self):
        ob=self.obstacle();plan=DR.plan_frame_drop(bpy.context,ob)
        reader=ob.copy();reader.data=ob.data.copy();bpy.context.scene.collection.objects.link(reader);self.obstacles.append(reader)
        with self.assertRaisesRegex(ValueError,'obstacle changed'):plan.require_current()
        self.assertTrue(Path(self.source['dir']).is_dir())
    def test_obstacle_plan_rejects_an_unlinked_owner(self):
        ob=self.obstacle();plan=DR.plan_frame_drop(bpy.context,ob)
        for collection in list(ob.users_collection):collection.objects.unlink(ob)
        with self.assertRaisesRegex(ValueError,'obstacle changed'):plan.require_current()


class OperationScopes(unittest.TestCase):
    """A final placed-shape consumer and every child plan share one source/retirement commit."""
    obstacle=ResumeResources.obstacle
    def setUp(self):
        self.queue=list(DR._DROP_LATER);DR._DROP_LATER.clear()
        ResumeResources.setUp(self)
        world=self.arrays[3]*.001
        np.save(Path(self.source['dir'])/'s1/frames.npy',np.stack([world,world+[0,0,.02]]).astype(np.float32))
        DR.write_pc2(str(Path(self.source['dir'])/'drape.pc2'),np.stack([world,world+[0,0,.02]]))
        self.other=self.obstacle();self.other.modifiers[0].filepath=str(Path(self.source['dir'])/'drape.pc2')
        legacy=S.loads(S.dumps(self.sp));legacy.pop('fabrics');legacy.pop('default_fabric_id')
        ST.write(bpy.context,legacy,keep=True);self.old_body=self.text.as_string()
    def tearDown(self):
        PLV.remove(list(PLV.objects(self.sp)),self.sp)
        ResumeResources.tearDown(self);DR._DROP_LATER[:]=self.queue
    def consume(self,prepared):
        return PLV.build(bpy.context,ST.get(bpy.context)[0],prepared['pat'],prepared['tris'],prepared['X'],prepared['pairs'])
    def assert_not_committed(self):
        self.assertEqual(self.text.as_string(),self.old_body)
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),1)
        self.assertEqual(len(DR._DROP_LATER),0)
    def test_children_share_one_migration_and_one_obstacle_plan(self):
        with DR.preparation(bpy.context) as owner:
            first=owner.prepare(20.,shapes_only=True);second=owner.prepare(20.,shapes_only=True)
            self.assertEqual(len(owner.build.obstacles),1);self.assert_not_committed()
            self.consume(second)
        self.assertFalse(FA.needs_migrate(ST.get(bpy.context)[0]))
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),0)
        self.assertEqual(len(DR._DROP_LATER),2)
    def test_completed_consumer_then_failure_restores_original_ids_and_resources(self):
        ids={ob.session_uid for ob in bpy.data.objects};meshes={me.session_uid for me in bpy.data.meshes}
        with self.assertRaisesRegex(RuntimeError,'after construction'):
            with DR.preparation(bpy.context) as owner:
                self.consume(owner.prepare(20.,shapes_only=True))
                raise RuntimeError('after construction')
        self.assert_not_committed();self.assertEqual({ob.session_uid for ob in bpy.data.objects},ids)
        self.assertEqual({me.session_uid for me in bpy.data.meshes},meshes)
    def test_migration_commit_failure_restores_the_completed_consumer(self):
        ids={ob.session_uid for ob in bpy.data.objects}
        with patch.object(ST,'write',side_effect=RuntimeError('scope migration failure')):
            with self.assertRaisesRegex(RuntimeError,'scope migration failure'):
                with DR.preparation(bpy.context) as owner:self.consume(owner.prepare(20.,shapes_only=True))
        self.assert_not_committed();self.assertEqual({ob.session_uid for ob in bpy.data.objects},ids)
    def test_second_retirement_failure_restores_both_obstacles_and_consumer(self):
        import shutil
        second=self.obstacle();folder=self.root/'other_source';shutil.copytree(Path(self.source['dir']),folder)
        second['ac9_job']=str(folder);second.modifiers[0].filepath=str(folder/'drape.pc2')
        apply=DR.apply_frame_drop;calls=[]
        def fail(plan,**options):
            result=apply(plan,**options);calls.append(plan.object_uid)
            if len(calls)==2:raise RuntimeError('scope second retirement')
            return result
        with patch.object(DR,'apply_frame_drop',fail):
            with self.assertRaisesRegex(RuntimeError,'scope second retirement'):
                with DR.preparation(bpy.context) as owner:self.consume(owner.prepare(20.,shapes_only=True))
        self.assert_not_committed();self.assertEqual(sum(m.type=='MESH_CACHE' for m in second.modifiers),1)
        self.assertFalse(PLV.objects(self.sp))
    def test_intentional_write_discards_and_replans_old_migration(self):
        with DR.preparation(bpy.context) as owner:
            owner.prepare(20.,shapes_only=True)
            updated=S.loads(S.dumps(ST.get(bpy.context)[0]));updated['own_write']={'keep':'new'}
            ST.write(bpy.context,updated,keep=True);owner.replan_source()
            self.consume(owner.prepare(20.,shapes_only=True))
        saved=S.loads(self.text.as_string());self.assertEqual(saved['own_write'],{'keep':'new'})
        self.assertFalse(FA.needs_migrate(saved))
    def test_unacknowledged_foreign_body_is_never_overwritten_by_rollback(self):
        with self.assertRaises(ST.EditChanged):
            with DR.preparation(bpy.context) as owner:
                self.consume(owner.prepare(20.,shapes_only=True))
                foreign=S.loads(self.text.as_string());foreign['foreign_write']={'keep':77}
                foreign_body=S.dumps(foreign);self.text.from_string(foreign_body)
        self.assertEqual(self.text.as_string(),foreign_body)
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),1)
        self.assertEqual(len(DR._DROP_LATER),0)
    def test_handled_child_refusal_cancels_without_committing(self):
        with DR.preparation(bpy.context) as owner:
            self.consume(owner.prepare(20.,shapes_only=True));owner.cancel()
        self.assert_not_committed();self.assertFalse(PLV.objects(self.sp))

    def folded_fixture(self):
        from ac9_cloth_sim import place_fold as PF
        sp=S.loads(self.old_body);line=D.add_internal(sp,'A',[[0,50],[100,50]])
        twin=D.mirror_copy(sp,'A');sp['placement'],_=S.mirror_twins(sp)
        ST.write(bpy.context,sp,keep=True);self.old_body=self.text.as_string();self.sp=sp
        return PF,line,twin
    def test_real_mirrored_fold_stays_pending_until_all_children_finish(self):
        PF,line,twin=self.folded_fixture()
        with DR.preparation(bpy.context) as owner:
            ok,why=PF.set_turn_side(bpy.context,'A',line,35.,'A',preparation=owner)
            self.assertTrue(ok,why);self.assert_not_committed_except_source()
            self.assertEqual(len(owner.build.obstacles),1)
            self.assertTrue(PF.folded(PLV.objects(ST.get(bpy.context)[0])['A']))
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),0)
        self.assertFalse(FA.needs_migrate(ST.get(bpy.context)[0]))
    def assert_not_committed_except_source(self):
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),1)
        self.assertEqual(len(DR._DROP_LATER),0)
    def test_real_mirrored_fold_late_failure_restores_everything(self):
        PF,line,twin=self.folded_fixture();ids={o.session_uid for o in bpy.data.objects}
        with self.assertRaisesRegex(RuntimeError,'after mirrored fold'):
            with DR.preparation(bpy.context) as owner:
                ok,why=PF.set_turn_side(bpy.context,'A',line,35.,'A',preparation=owner)
                self.assertTrue(ok,why);raise RuntimeError('after mirrored fold')
        self.assert_not_committed();self.assertFalse(PLV.objects(self.sp))
        self.assertEqual({o.session_uid for o in bpy.data.objects},ids)
    def test_real_partner_placement_replans_two_intentional_source_writes(self):
        from ac9_cloth_sim import place_seam as PS
        sp=S.loads(self.old_body);D.add_polygon(sp,[[120,0],[220,0],[220,100],[120,100]],name='B')
        def side(n):return {'piece':n,'from':['E1',0.],'to':['E1',1.],'dir':1}
        sp['seams']=[dict(id='OwnedSeam',a=side('A'),b=side('B'),enabled=True)]
        ST.write(bpy.context,sp,keep=True);self.old_body=self.text.as_string();self.sp=sp
        with DR.preparation(bpy.context) as owner:
            placed,why=PS.place_by_partners(bpy.context,sp,'B',preparation=owner)
            self.assertIsNotNone(placed,why);self.assertIn('B',placed['placement'])
            self.assert_not_committed_except_source();self.assertEqual(len(owner.build.obstacles),1)
        self.assertFalse(FA.needs_migrate(ST.get(bpy.context)[0]))
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),0)
    def test_two_control_side_children_share_owner_and_second_failure_restores_first(self):
        sp=S.loads(self.old_body)
        D.mirror_copy(sp,'A');D.add_polygon(sp,[[300,0],[400,0],[400,100],[300,100]],name='B')
        sp['placement']['B']=dict(sp['placement']['A'],theta=-90.,anchor=[350,50]);D.mirror_copy(sp,'B')
        sp['placement'],_=S.mirror_twins(sp);ST.write(bpy.context,sp,keep=True);self.sp=sp
        res=DR.prepare(bpy.context,20.,shapes_only=True);self.consume(res)
        # Restore a live obstacle and the legacy source before the operation under test.
        DR.attach_cache(bpy.context,self.other,str(Path(self.source['dir'])/'drape.pc2'),2)
        sp.pop('fabrics',None);sp.pop('default_fabric_id',None);ST.write(bpy.context,sp,keep=True)
        self.old_body=self.text.as_string();DR._DROP_LATER.clear()
        leaders={n:(PLV.objects(sp)[n].get(PLV.PROP),PLV.objects(sp)[n].session_uid) for n in ('A','B')}
        followers=[PLV.objects(sp)[n][PLV.TWIN_PROP] for n in ('A','B')]
        stage=PLV._stage_control_side;calls=[]
        def staged(*args,**kw):
            commit,cleanup=stage(*args,**kw)
            def fail_second():
                commit();calls.append(args[2])
                if len(calls)==2:raise RuntimeError('second control commit')
            return fail_second,cleanup
        with patch.object(PLV,'_stage_control_side',staged):
            with self.assertRaisesRegex(RuntimeError,'second control commit'):
                PLV.activate_sides(bpy.context,sp,followers)
        self.assert_not_committed()
        self.assertEqual({n:(PLV.objects(sp)[n].get(PLV.PROP),PLV.objects(sp)[n].session_uid) for n in ('A','B')},leaders)

    def local_consumer(self):
        props=bpy.context.scene.ac9_cloth_sim;previous=props.mesh_size;props.mesh_size=20.
        self.addCleanup(setattr,props,'mesh_size',previous)
        g=self.obstacle();g.name=DR.garment_name(self.sp);g['ac9_piece_names']='["A"]'
        local=SimpleNamespace(info=self.source,local=['A'],ob_name=g.name)
        return g,local
    def test_local_sim_consumer_failure_keeps_current_result_until_scope_finishes(self):
        g,local=self.local_consumer();uid=g.session_uid
        with patch.object(PLV,'build',side_effect=RuntimeError('local consumer build failure')):
            with self.assertRaisesRegex(RuntimeError,'local consumer build failure'):
                DR.AC9_OT_SimDress._to_placed(local,bpy.context,self.arrays[3]*.001)
        self.assertEqual(g.session_uid,uid);self.assertEqual(bpy.data.objects.get(g.name),g)
        self.assert_not_committed();self.assertEqual(sum(m.type=='MESH_CACHE' for m in g.modifiers),1)
    def test_local_sim_consumer_success_retires_current_result_after_commit(self):
        g,local=self.local_consumer();name=g.name
        result=DR.AC9_OT_SimDress._to_placed(local,bpy.context,self.arrays[3]*.001)
        self.obstacles.remove(g)
        self.assertEqual(result[0],['A']);self.assertIsNone(bpy.data.objects.get(name))
        self.assertTrue(PLV.from_sim(PLV.objects(ST.get(bpy.context)[0])['A']))

    def test_real_back_to_guide_second_prepare_failure_keeps_current_result_and_obstacle(self):
        g,local=self.local_consumer();uid=g.session_uid
        prepare=DR._prepare;calls=[]
        def second_failure(*args,**kw):
            calls.append(True)
            if len(calls)==2:raise ValueError('second guide preparation failure')
            return prepare(*args,**kw)
        with patch.object(DR,'_prepare',second_failure):
            with self.assertRaisesRegex(RuntimeError,'second guide preparation failure'):
                bpy.ops.ac9_cloth_sim.reset_placed()
        self.assertEqual(len(calls),2)
        self.assertEqual(g.session_uid,uid);self.assertEqual(bpy.data.objects.get(g.name),g)
        self.assert_not_committed();self.assertFalse(PLV.objects(self.sp))
    def test_real_back_to_guide_success_retires_current_result_after_both_consumers(self):
        g,local=self.local_consumer();name=g.name
        self.assertEqual(bpy.ops.ac9_cloth_sim.reset_placed(),{'FINISHED'})
        self.obstacles.remove(g)
        self.assertIsNone(bpy.data.objects.get(name));self.assertTrue(PLV.objects(ST.get(bpy.context)[0]))
        self.assertFalse(FA.needs_migrate(ST.get(bpy.context)[0]))
        self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),0)
    def test_real_move_by_hand_late_control_failure_keeps_uncommitted_preparation(self):
        from ac9_cloth_sim import placement as PL
        ids={o.session_uid for o in bpy.data.objects}
        with patch.object(PL,'selected_pieces',return_value=['A']):
            with patch.object(PLV,'activate_sides',side_effect=ValueError('control refused after construction')):
                self.assertEqual(bpy.ops.ac9_cloth_sim.move_by_hand(),{'CANCELLED'})
        self.assert_not_committed();self.assertFalse(PLV.objects(self.sp))
        self.assertEqual({o.session_uid for o in bpy.data.objects},ids)

    def test_real_partial_reset_late_build_failure_restores_frame_provenance(self):
        g,local=self.local_consumer();old={k:g[k] for k in g.keys()}
        build=PLV.build;calls=[]
        def fail_second(*args,**kw):
            calls.append(True)
            if len(calls)==2:raise RuntimeError('second partial reset build failure')
            return build(*args,**kw)
        with patch.object(PLV,'build',fail_second):
            with self.assertRaisesRegex(RuntimeError,'second partial reset build failure'):
                DR.reset_result(bpy.context,names=['A'])
        self.assertEqual({k:g[k] for k in g.keys()},old)
        self.assertEqual(len(calls),2);self.assert_not_committed()
        self.assertTrue(next(m for m in g.modifiers if m.type=='MESH_CACHE').show_viewport)
        self.assertFalse(PLV.objects(self.sp))

    def refusal_fixture(self,internal=False):
        sp=S.loads(self.old_body);line=None
        if internal:
            line=D.add_internal(sp,'A',[[20,50],[80,50]])
            sp['folds']=[dict(piece='A',line=line,deg=35.,side='A')]
        ST.write(bpy.context,sp,keep=True)
        res=DR.prepare(bpy.context,20.,shapes_only=True);self.consume(res)
        DR.attach_cache(bpy.context,self.other,str(Path(self.source['dir'])/'drape.pc2'),2)
        sp.pop('fabrics',None);sp.pop('default_fabric_id',None);ST.write(bpy.context,sp,keep=True)
        self.old_body=self.text.as_string();self.sp=sp;DR._DROP_LATER.clear()
        return line
    def test_real_unsewn_part_refusal_does_not_commit_preparation(self):
        from ac9_cloth_sim import place_seam as PS
        self.refusal_fixture();result=PS.move(bpy.context,self.sp,'A')
        self.assertIs(result[0],False);self.assert_not_committed()
    def test_real_interior_fold_refusal_does_not_commit_preparation(self):
        from ac9_cloth_sim import place_fold as PF
        line=self.refusal_fixture(internal=True);result=PF.refold(bpy.context,'A',line,35.)
        self.assertIs(result[0],False);self.assert_not_committed()

    def test_placement_pipeline_final_consumer_failure_keeps_all_preparations_pending(self):
        from ac9_cloth_sim import placement as PL
        with patch.object(PL,'refold_all',side_effect=RuntimeError('last placement consumer failed')):
            with self.assertRaisesRegex(RuntimeError,'last placement consumer failed'):
                PL.rebuild_placement(bpy.context,self.sp,['A'])
        self.assert_not_committed();self.assertFalse(PLV.objects(self.sp))
    def test_real_band_refusal_keeps_all_preparations_pending(self):
        from ac9_cloth_sim import placement as PL,place_targets as PT
        with patch.object(PL,'selected_pieces',return_value=['A']),patch.object(PT,'arguments',return_value=(['A'],None)),patch.object(PT,'validate',return_value=None):
            self.assertEqual(bpy.ops.ac9_cloth_sim.lay_on_edge(),{'CANCELLED'})
        self.assert_not_committed();self.assertFalse(PLV.objects(self.sp))

    def mixed_batch_fixture(self):
        from ac9_cloth_sim import placement as PL,place_seam as PS,place_targets as PT
        sp=S.loads(self.old_body)
        for i,name in enumerate(('B','C'),1):
            D.add_polygon(sp,[[i*120,0],[i*120+100,0],[i*120+100,100],[i*120,100]],name=name)
            sp['placement'][name]=dict(sp['placement']['A'],theta=90.-i*60.,anchor=[i*120+50,50])
        def side(n):return {'piece':n,'from':['E1',0.],'to':['E1',1.],'dir':1}
        sp['seams']=[dict(id='OwnedMixed',a=side('A'),b=side('C'),enabled=True)]
        ST.write(bpy.context,sp,keep=True);self.consume(DR.prepare(bpy.context,20.,shapes_only=True))
        DR.attach_cache(bpy.context,self.other,str(Path(self.source['dir'])/'drape.pc2'),2)
        sp.pop('fabrics',None);sp.pop('default_fabric_id',None);ST.write(bpy.context,sp,keep=True)
        self.sp=sp;self.old_body=self.text.as_string();DR._DROP_LATER.clear()
        previous=bpy.context.scene.ac9_cloth_sim.mesh_size;bpy.context.scene.ac9_cloth_sim.mesh_size=20.
        self.addCleanup(setattr,bpy.context.scene.ac9_cloth_sim,'mesh_size',previous)
        return PL,PS,PT
    def mixed_batch(self,names,fatal=False):
        PL,PS,PT=self.mixed_batch_fixture();mark=PS.mark_on_next_run
        def marking(*args,**kw):
            result=mark(*args,**kw)
            if fatal:raise RuntimeError('fatal after accepted piece geometry')
            return result
        with patch.object(PL,'selected_pieces',return_value=names),patch.object(PT,'arguments',return_value=(names,None)),patch.object(PT,'validate',return_value=None),patch.object(PS,'mark_on_next_run',marking):
            result=bpy.ops.ac9_cloth_sim.move_to_seam()
        flags={n:bool(o.get(PS.SEAM_PROP)) for n,o in PLV.objects(ST.get(bpy.context)[0]).items()}
        if fatal or names==['B']:
            self.assertEqual(result,{'CANCELLED'});self.assert_not_committed()
            self.assertFalse(any(flags.values()))
        else:
            self.assertEqual(result,{'FINISHED'});self.assertEqual(flags,dict(A=True,B=False,C=False))
            self.assertEqual(sum(m.type=='MESH_CACHE' for m in self.other.modifiers),0)
    def test_mixed_piece_success_then_expected_refusal_keeps_success(self):self.mixed_batch(['A','B'])
    def test_mixed_piece_expected_refusal_then_success_keeps_success(self):self.mixed_batch(['B','A'])
    def test_all_piece_refusals_leave_pending_resources_uncommitted(self):self.mixed_batch(['B'])
    def test_fatal_failure_after_piece_geometry_rolls_back_the_whole_batch(self):self.mixed_batch(['A','B'],fatal=True)


if __name__=='__main__':unittest.main()
