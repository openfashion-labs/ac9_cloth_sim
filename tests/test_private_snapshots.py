# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender ID lifetime: private copies, nested scopes, handoff and foreign auxiliaries."""
import unittest
import bpy
import numpy as np
from ac9_cloth_sim import placed_view as PV, store as ST, private_ids as PI, garment_owner as GO
from ac9_cloth_sim.core import spec as S, draft as DR


class PrivateSnapshots(unittest.TestCase):
    def setUp(self):
        self.ctx=bpy.context
        self.sp=S.new_spec('PrivateSnapshotContract')
        DR.add_polygon(self.sp,[[0,0],[100,0],[0,100]],name='P')
        ST.new_text(self.ctx,self.sp['name']);ST.write(self.ctx,self.sp,keep=True)
        V=np.array([[0.,0.],[100.,0.],[0.,100.]])
        me=PV._mesh('SnapshotTriangle',V,np.array([[0,1,2]]),np.c_[V,np.zeros(3)],np.zeros(3))
        self.ob=bpy.data.objects.new('SnapshotTriangle',me);PV._collection(self.ctx).objects.link(self.ob)
        PV._tag(self.ob,self.sp,'P',V)
        self.cen=PV.center_empty(self.ctx,self.sp)
        self.aux=PV._mirror_empty(self.ctx,self.sp)
        self.scopes=[]

    def backup(self):
        b=PV.PreparedResetBackup(self.ctx,self.sp);self.scopes.append(b);return b

    def tearDown(self):
        for b in reversed(self.scopes):b.close()
        self.assertFalse(PI._owners)
        self.assertFalse(PV._backup_scopes)
        for ob in list(bpy.data.objects):bpy.data.objects.remove(ob,do_unlink=True)
        for t in list(bpy.data.texts):bpy.data.texts.remove(t)
        ST.invalidate()

    def test_rollback_cannot_enumerate_or_delete_its_auxiliary_copies(self):
        before={int(o.session_uid) for o in bpy.data.objects}
        b=self.backup();aux=list(PV.aux_empties())
        for uid,ob in b._owned_objects.items():
            self.assertNotIn(ob.name,aux)
            self.assertFalse(GO.owns(ob,GO.PLACED,self.sp))
            self.assertNotIn(ob,PV.objects(self.sp).values())
        b.rollback();b.close();b.close()
        self.assertEqual(before,{int(o.session_uid) for o in bpy.data.objects})

    def test_restored_copy_is_handed_off_and_survives_close(self):
        b=self.backup();name=self.aux.name
        bpy.data.objects.remove(self.aux,do_unlink=True)
        b.rollback();restored=bpy.data.objects[name];uid=int(restored.session_uid)
        self.assertFalse(PI.contains(restored));b.close()
        self.assertEqual(uid,bpy.data.objects[name].session_uid)
        self.assertIn(name,PV.aux_empties())

    def test_nested_scopes_do_not_consume_each_others_copies(self):
        outer=self.backup();uids=set(outer._owned_objects)
        inner=self.backup();inner.rollback();inner.close()
        self.assertEqual(uids,set(outer._owned_objects));self.assertTrue(uids.issubset(PI._owners))
        outer.rollback();outer.close()

    def test_invalid_private_empty_is_released_once(self):
        b=self.backup();ob=b.auxiliary[0][1]
        bpy.data.objects.remove(ob,do_unlink=True)
        b.close();b.close();self.assertFalse(PI._owners)

    def test_accepted_child_auxiliary_is_owned_by_the_enclosing_rollback(self):
        outer=self.backup();inner=self.backup()
        created=PV._mirror_empty(self.ctx,self.sp,plane='y');uid=created.session_uid
        inner.close();self.assertIn(uid,outer.created_aux)
        outer.rollback();outer.close()
        self.assertFalse(any(o.session_uid==uid for o in bpy.data.objects))

    def test_foreign_new_and_preexisting_auxiliaries_are_untouched(self):
        foreign=bpy.data.texts.new('ForeignSnapshotDoc')
        old=bpy.data.objects.new(PV.MIRROR_NAME+':foreign_old',None);PV._collection(self.ctx).objects.link(old)
        old[GO.PLACED]=foreign;old.location.x=1.
        b=self.backup();old.location.x=2.
        new=bpy.data.objects.new(PV.MIRROR_NAME+':foreign_new',None);PV._collection(self.ctx).objects.link(new)
        new[GO.PLACED]=foreign;new.location.x=3.
        ids=(old.session_uid,new.session_uid);links=(tuple(old.users_collection),tuple(new.users_collection))
        b.rollback();b.close()
        self.assertEqual(ids,(old.session_uid,new.session_uid))
        self.assertEqual(links,(tuple(old.users_collection),tuple(new.users_collection)))
        self.assertEqual((old.location.x,new.location.x),(2.,3.))

    def garment(self):
        V=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[1.,1.,0.]])
        me=bpy.data.meshes.new('SnapshotGarmentMesh');me.from_pydata(V.tolist(),[],[(0,1,3,2)]);me.update()
        g=bpy.data.objects.new('AC9_Garment_'+self.sp['name'],me);PV._collection(self.ctx).objects.link(g)
        g[GO.GARMENT]=ST.text(self.ctx)
        return g

    def test_rollback_keeps_an_untouched_garment_mesh(self):
        # A fold field's refused refold rolled back by installing the garment's copy: a running Live stopped
        # ("the control shape changed") though nothing had touched the garment.
        g=self.garment();uid=int(g.data.session_uid)
        b=self.backup();self.assertIs(b.g,g)
        b.rollback();b.close()
        self.assertEqual(uid,int(g.data.session_uid))
        self.assertFalse(any(m.name.startswith('SnapshotGarmentMesh.') for m in bpy.data.meshes))

    def test_rollback_writes_moved_positions_back_in_place(self):
        g=self.garment();uid=int(g.data.session_uid)
        b=self.backup();g.data.vertices[3].co.z=0.5
        b.rollback();b.close()
        self.assertEqual(uid,int(g.data.session_uid));self.assertEqual(g.data.vertices[3].co.z,0.)

    def test_rollback_still_installs_the_copy_when_weights_changed(self):
        g=self.garment();g.vertex_groups.new(name='Mask').add([0],1.,'REPLACE');uid=int(g.data.session_uid)
        b=self.backup();g.vertex_groups['Mask'].add([1],0.5,'REPLACE')
        b.rollback();b.close()
        self.assertNotEqual(uid,int(g.data.session_uid))
        self.assertEqual([len(v.groups) for v in g.data.vertices],[1,0,0,0])

    def test_only_auxiliary_allocated_inside_operation_is_removed(self):
        b=self.backup()
        self.sp['center']=[10,20,30]
        # Create a fresh real center through its allocation site, then refuse the operation.
        created=PV._mirror_empty(self.ctx,self.sp,plane='y')
        uid=created.session_uid;self.assertIn(uid,b.created_aux)
        b.rollback();b.close()
        self.assertFalse(any(o.session_uid==uid for o in bpy.data.objects))
