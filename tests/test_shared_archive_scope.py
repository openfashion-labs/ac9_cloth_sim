"""Legacy shared archives and the last retained input across nested attempts.

Finite synthetic triangles isolate archive ownership, without physical simulation.
"""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import bpy
import numpy as np
from ac9_cloth_sim import store as ST, pattern_view as PAT, placed_view as PV, prepare_build as PB
from ac9_cloth_sim.core import draft as D, spec as S


class SharedArchiveScope(unittest.TestCase):
    def setUp(self):
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        ST.invalidate()
        self.ctx = bpy.context; self.sp = S.new_spec('SharedArchiveScope')
        self.V = np.array([[0.,0.],[100.,0.],[0.,100.]])
        for name in ('A','B'):
            D.add_polygon(self.sp,self.V.tolist(),name=name)
            self.sp.setdefault('placement',{})[name] = dict(guide=S.FLAT,
                flat=dict(o=[0,0,0],R=np.eye(3).tolist()),anchor=[30,30])
        ST.new_text(self.ctx,self.sp['name']); ST.write(self.ctx,self.sp,keep=True)
        PAT.rebuild(self.ctx,*ST.get(self.ctx),switch_document=True)
        PAT.require_integrity(self.ctx,self.sp)
        self.assertEqual(D.problems(self.sp,['A','B']),[])
        self.assertEqual(D.signed_area(self.V),5000)
        self.mesh = PV._mesh('SharedArchiveScope',self.V,np.array([[0,1,2]]),
                             np.c_[self.V,np.zeros(3)],np.zeros(3))
        self.obs = []
        for name in ('A','B','Outside'):
            ob = bpy.data.objects.new('ArchiveScope'+name,self.mesh)
            (self.ctx.scene.collection if name == 'Outside' else PV._collection(self.ctx)).objects.link(ob)
            if name != 'Outside':
                PV._tag(ob,self.sp,name,self.V)
            self.obs.append(ob)
        self.ctx.view_layer.update()

    def co(self,ob):
        return np.array([tuple(v.co) for v in ob.data.vertices])

    def test_unmodified_child_inherits_the_parents_protected_last_input(self):
        self.obs[2].data = self.mesh.copy()
        attr = self.mesh.attributes.new('CustomText','STRING','POINT'); attr.data[0].value = b'keep'
        original = self.mesh.session_uid
        with PB.operation(self.ctx) as owner:
            private = PV.writable_mesh(self.obs[0]); private.vertices[0].co.z = .025; private.update()
            self.assertEqual(self.mesh.users-int(self.mesh.use_fake_user),1)
            with owner.attempt():
                owner.refuse('expected refusal without a write')
            self.assertEqual(self.obs[1].data.session_uid,original)
        self.assertEqual(self.obs[1].data.session_uid,original)
        self.assertEqual(self.obs[1].data.attributes['CustomText'].data[0].value,b'keep')

    def test_saved_legacy_local_archive_restore_preserves_the_external_object(self):
        ob, outside = self.obs[0], self.obs[2]
        PV.write_local_result(ob,np.c_[self.V,np.full(3,30.)]*.001)
        outside.data = ob.data
        # Exact old on-disk representation: keep reused the result mesh even with external users.
        ob.data[PV.KEEP_PROP] = json.dumps(dict(PV.shape_record(ob),garment=PV.garment(self.sp)))
        ob.data[PV.GO.PLACED] = ST.text(self.ctx)
        ob.data.use_fake_user = True
        PV.remove(['A'],self.sp,forget_kept=False)
        self.assertIs(PV._kept(self.sp)['A'],outside.data)
        self.assertIsNotNone(outside.data.get(PV.LOCAL_INPUT))
        name = outside.name; before = self.co(outside)
        with tempfile.TemporaryDirectory(prefix='ac9_legacy_local_') as folder:
            path = str(Path(folder)/'legacy.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            ST.invalidate(); sp = ST.get(bpy.context)[0]
            self.assertIsNotNone(PV._kept(sp)['A'].get(PV.LOCAL_INPUT))
            self.assertEqual(PV.restore_local_inputs(sp,['A']),{'A'})
            outside = bpy.data.objects.get(name); self.assertIsNotNone(outside)
            np.testing.assert_array_equal(self.co(outside),before)
            restored = PV._kept(sp)['A']
            self.assertNotEqual(restored,outside.data)
            np.testing.assert_array_equal(np.array([tuple(v.co) for v in restored.vertices])[:,2],0)

    def test_centering_an_archive_preserves_an_external_user(self):
        ob, outside = self.obs[0], self.obs[2]
        PV.save_before_seam(ob,self.sp)
        outside.data = PV.before_seam(self.sp)['A']; before = self.co(outside)
        uid = outside.data.session_uid; name = outside.name
        PV.center_control_origins(self.ctx,self.sp,[ob])
        outside = bpy.data.objects.get(name); self.assertIsNotNone(outside)
        self.assertEqual(outside.data.session_uid,uid)
        np.testing.assert_array_equal(self.co(outside),before)

    def test_changing_control_side_preserves_an_external_archive_user(self):
        from ac9_cloth_sim import dress
        from ac9_cloth_sim.core import pattern_build
        sp = S.loads(ST.text(self.ctx).as_string())
        twin = D.mirror_copy(sp,'A'); sp['placement'], _ = S.mirror_twins(sp)
        ST.write(self.ctx,sp,keep=True); PAT.rebuild(self.ctx,*ST.get(self.ctx))
        PV._add_mirror(self.obs[0],PV.center_empty(self.ctx,sp),twin)
        PV.save_before_seam(self.obs[0],sp)
        outside = self.obs[2]; outside.data = PV.before_seam(sp)['A']
        before = self.co(outside); uid = outside.data.session_uid; name = outside.name
        pat, _ = pattern_build.build(sp,15.)
        with patch.object(dress,'prepare',return_value={'pat':pat}):
            apply, cleanup = PV._stage_control_side(self.ctx,sp,twin)
            try:
                apply()
            finally:
                cleanup()
        self.assertEqual(self.obs[0][PV.PROP],twin)
        outside = bpy.data.objects.get(name); self.assertIsNotNone(outside)
        self.assertEqual(outside.data.session_uid,uid); np.testing.assert_array_equal(self.co(outside),before)


if __name__ == '__main__':
    unittest.main()
