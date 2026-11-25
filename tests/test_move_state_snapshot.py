"""Control rollback keeps intent; transient RNA-bearing move caches are rebuilt."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import bpy
import numpy as np
from mathutils import Matrix
from ac9_cloth_sim import piece_move as PM, placed_view as PLV

class ControlRollback(unittest.TestCase):
    def setUp(self):
        self.old=PM._state.copy();PM._state.clear()
        self.obs=[];self.meshes=[]
        self.text=bpy.data.texts.new('ControlRollbackSpec')
    def tearDown(self):
        PM._state.clear();PM._state.update(self.old)
        for ob in self.obs:
            try:bpy.data.objects.remove(ob,do_unlink=True)
            except ReferenceError:pass
        for me in self.meshes:
            try:
                if me.users==0:bpy.data.meshes.remove(me)
            except ReferenceError:pass
        bpy.data.texts.remove(self.text)
    def ob(self,name):
        ob=bpy.data.objects.new(name,None);bpy.context.scene.collection.objects.link(ob);self.obs.append(ob);return ob
    def test_recreated_handle_preserves_pivot_and_active_binds_fresh(self):
        old=self.ob('OldControl');new=self.ob('NewControl');g=self.ob('Result')
        old[PM.NAMES_PROP]='A,B';new[PM.NAMES_PROP]='A,B'
        old.matrix_world=Matrix.Translation((.1,.2,.3))@Matrix.Rotation(.4,4,'Z')
        expected=old.matrix_world.copy()
        bpy.context.view_layer.objects.active=old
        PM._state[old.name]={'invalid_cache':self.text,'base':np.ones((2,3))}
        with patch.object(PM,'handles',return_value=[old]),patch.object(PM.SEL,'objects',return_value=[old]),patch.object(PM.SEL,'piece_selection',return_value=(('A','B'),'A')):
            saved=PM.ControlSelectionBackup(bpy.context)
        def project(*args,**kw):
            bpy.data.objects.remove(old,do_unlink=True)
            PM._state.clear()
        with patch.object(PM.SEL,'project',side_effect=project),patch.object(PM,'handles',return_value=[new]),patch.object(PM,'_garment',return_value=g),patch.object(PM,'piece_verts',return_value=np.array([0,1])),patch.object(PM,'_binding',return_value=(self.text,123,456)):
            saved.restore(bpy.context)
        self.assertIs(bpy.context.view_layer.objects.active,new)
        np.testing.assert_allclose(np.array(new.matrix_world),np.array(expected),atol=1e-7,rtol=0)
        state=PM._state[new.name];self.assertIsNone(state['base']);self.assertIsNone(state['M0'])
        self.assertEqual(state['last'],tuple(map(tuple,new.matrix_world)))
        self.assertEqual(state['binding'],(self.text,123,456))
        self.assertNotIn('invalid_cache',state)
    def test_shared_mesh_isolation_keeps_outside_and_inside_groups(self):
        me=bpy.data.meshes.new('SharedControlMesh');self.meshes.append(me)
        me.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
        obs=[]
        for name in ['InsideA','InsideB','Outside']:
            ob=bpy.data.objects.new(name,me);self.obs.append(ob);obs.append(ob)
        backup=object.__new__(PLV.PreparedResetBackup)
        backup.spec={};backup._shared_meshes={};backup._shared_originals=[]
        with patch.object(PLV,'objects',return_value=dict(A=obs[0],B=obs[1])):
            backup.isolate_shared_meshes()
        private=obs[0].data;self.meshes.append(private)
        self.assertIs(obs[1].data,private);self.assertIsNot(private,me);self.assertIs(obs[2].data,me)
        private.vertices[0].co.x=10
        self.assertEqual(me.vertices[0].co.x,0)
        for ob in obs[:2]:ob.data=backup._shared_meshes[ob.name]
        for orig,fake in backup._shared_originals:orig.use_fake_user=fake
        self.assertTrue(all(ob.data==me for ob in obs));self.assertFalse(me.use_fake_user)

    def test_unrelated_shared_mesh_survives_commit_and_cancel(self):
        from ac9_cloth_sim import dress as DR,store as ST
        for cancel in (False,True):
            with self.subTest(cancel=cancel):
                shared=bpy.data.meshes.new('UnrelatedShared');self.meshes.append(shared)
                shared.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
                own=shared.copy();self.meshes.append(own)
                obs=[]
                for name,me in [('Move',own),('Other',shared),('Outside',shared)]:
                    ob=bpy.data.objects.new(name,me);bpy.context.scene.collection.objects.link(ob)
                    self.obs.append(ob);obs.append(ob)
                for name,ob in zip(('A','B'),obs):ob[PLV.PROP]=name
                with patch.object(ST,'text',return_value=self.text),patch.object(DR,'garment',return_value=None),patch.object(PLV,'objects',return_value=dict(A=obs[0],B=obs[1])),patch.object(PLV,'_kept',return_value={}),patch.object(PLV,'before_seam',return_value={}):
                    backup=PLV.PreparedResetBackup(bpy.context,{})
                    backup.isolate_shared_meshes({'A'})
                    self.assertIs(obs[1].data,shared)
                    if cancel:backup.rollback()
                    backup.close()
                self.assertIs(obs[1].data,shared);self.assertIs(obs[2].data,shared)
                self.assertFalse(shared.use_fake_user)
    def test_unchanged_shared_target_commit_reconnects(self):
        from ac9_cloth_sim import dress as DR,store as ST
        me=bpy.data.meshes.new('TargetShared');self.meshes.append(me)
        me.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
        obs=[]
        for name in ('Target','OutsideTarget'):
            ob=bpy.data.objects.new(name,me);bpy.context.scene.collection.objects.link(ob)
            self.obs.append(ob);obs.append(ob)
        obs[0][PLV.PROP]='A'
        with patch.object(ST,'text',return_value=self.text),patch.object(DR,'garment',return_value=None),patch.object(PLV,'objects',return_value={'A':obs[0]}),patch.object(PLV,'_kept',return_value={}),patch.object(PLV,'before_seam',return_value={}):
            backup=PLV.PreparedResetBackup(bpy.context,{})
            backup.isolate_shared_meshes({'A'})
            self.assertIsNot(obs[0].data,me)
            backup.close()
        self.assertIs(obs[0].data,me);self.assertIs(obs[1].data,me)
        self.assertFalse(me.use_fake_user)
    def test_foreign_name_reuse_is_not_written_during_close(self):
        from ac9_cloth_sim import dress as DR,store as ST
        me=bpy.data.meshes.new('ForeignGuardShared');self.meshes.append(me)
        me.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
        obs=[]
        for name in ('ForeignGuardTarget','ForeignGuardOutside'):
            ob=bpy.data.objects.new(name,me);bpy.context.scene.collection.objects.link(ob)
            self.obs.append(ob);obs.append(ob)
        obs[0][PLV.PROP]='A'
        with patch.object(ST,'text',return_value=self.text),patch.object(DR,'garment',return_value=None),patch.object(PLV,'objects',return_value={'A':obs[0]}),patch.object(PLV,'_kept',return_value={}),patch.object(PLV,'before_seam',return_value={}):
            backup=PLV.PreparedResetBackup(bpy.context,{})
            backup.isolate_shared_meshes({'A'})
            self.meshes.append(obs[0].data)
            name=obs[0].name;obs[0].name='RenamedForeignGuardTarget'
            foreignmesh=me.copy();self.meshes.append(foreignmesh)
            foreign=bpy.data.objects.new(name,foreignmesh);bpy.context.scene.collection.objects.link(foreign);self.obs.append(foreign)
            backup.preparation_owner=SimpleNamespace(can_restore=lambda snapshot:False)
            backup.close()
        self.assertIs(foreign.data,foreignmesh)
        self.assertIs(obs[1].data,me)
        self.assertFalse(me.use_fake_user)
