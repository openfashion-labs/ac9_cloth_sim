"""A refused fold preserves sharing; accepted folds write only their physical object.

Healthy synthetic finite rectangles isolate shared datablock ownership and rollback.
"""
import json
import unittest
from unittest.mock import patch
import bpy
import numpy as np
from ac9_cloth_sim import (place_fold as F, placed_view as PV, store as ST,
                          prepare_build as PB, pattern_view as PAT)
from ac9_cloth_sim.core import draft as D, spec as S


class SharedFold(unittest.TestCase):
    def setUp(self):
        self.ctx = bpy.context
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        for text in list(bpy.data.texts):
            bpy.data.texts.remove(text)
        ST.invalidate(); F._cache.clear()
        self.sp = S.new_spec('SharedFold')
        self.V = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        self.T = np.array([[0, 1, 2], [0, 2, 3]])
        self.lines = {}
        for name in ('A', 'B'):
            D.add_polygon(self.sp, self.V.tolist(), name=name)
            self.sp.setdefault('placement', {})[name] = dict(
                guide=S.FLAT, flat=dict(o=[0, 0, 0], R=np.eye(3).tolist()), anchor=[50, 50])
            self.lines[name] = D.add_internal(self.sp, name, [[0, 50], [100, 50]], mirror=False)
        self.text = ST.new_text(self.ctx, self.sp['name']); ST.write(self.ctx, self.sp, keep=True)
        PAT.rebuild(self.ctx, *ST.get(self.ctx)); PAT.require_integrity(self.ctx, self.sp)
        self.mesh = PV._mesh('SharedFoldInput', self.V, self.T,
                             np.c_[self.V, np.zeros(4)], np.zeros(3))
        self.obs = []
        for name in ('A', 'B', 'Outside'):
            ob = bpy.data.objects.new(name, self.mesh); PV._collection(self.ctx).objects.link(ob)
            if name != 'Outside':
                PV._tag(ob, self.sp, name, self.V)
            self.obs.append(ob)
        self.ctx.view_layer.update()
        self.assertEqual(D.problems(self.sp, ['A', 'B']), [])
        self.assertTrue(np.isfinite(self.co()).all())
        self.assertTrue(all(abs(np.linalg.det(self.V[t[1:]]-self.V[t[0]])) > 1 for t in self.T))

    def co(self):
        return np.array([[tuple(v.co) for v in ob.data.vertices] for ob in self.obs]) * 1000

    def ids(self):
        return [ob.data.session_uid for ob in self.obs]

    def stored_fold(self):
        F.write_angle(self.ctx, 'A', self.lines['A'], 45., 'A')
        co = self.co()[0].astype(np.float32) / 1000
        self.mesh.attributes.new(F.BEFORE_ATTR+'0', 'FLOAT_VECTOR', 'POINT').data.foreach_set('vector', co.ravel())
        co[0, 2] = .025
        self.mesh.vertices.foreach_set('co', co.ravel())
        self.mesh.attributes[PV.GEN_ATTR].data.foreach_set('vector', PV.generated_co(self.obs[0], co).astype(np.float32).ravel())
        self.mesh.update()
        self.obs[0][F.FOLD_PROP] = json.dumps([dict(line=self.lines['A'], side=0, deg=45.)])
        self.assertFalse(PV.has_edits(self.obs[0]))

    def unfold(self, **kwargs):
        ok, why = F.set_turn_side(self.ctx, 'A', self.lines['A'], 45., 'NONE', **kwargs)
        self.assertTrue(ok, why)

    def test_natural_refusal_preserves_all_input_mesh_identities(self):
        self.obs[0][PV.LAID_PROP] = True
        before = self.ids(); co = self.co(); body = self.text.as_string()
        count = len(bpy.data.meshes)
        ok, why = F.set_turn_side(self.ctx, 'A', self.lines['A'], 45., 'A')
        self.assertFalse(ok); self.assertIn('Band on Edge', why)
        self.assertEqual(self.ids(), before); np.testing.assert_array_equal(self.co(), co)
        self.assertEqual(self.text.as_string(), body); self.assertEqual(len(bpy.data.meshes), count)

    def test_noop_cancellation_preserves_sharing_and_fake_user(self):
        for fake in (False, True):
            self.mesh.use_fake_user = fake
            ids = self.ids(); count = len(bpy.data.meshes)
            with PB.operation(self.ctx) as owner:
                owner.cancel()
            self.assertEqual(self.ids(), ids); self.assertEqual(self.mesh.use_fake_user, fake)
            self.assertEqual(len(bpy.data.meshes), count)

    def test_accepted_unfold_preserves_nonparticipants_and_attributes(self):
        self.stored_fold(); before = self.co(); original = self.mesh.session_uid
        self.unfold()
        self.assertAlmostEqual(self.co()[0, 0, 2], 0)
        np.testing.assert_array_equal(self.co()[1:], before[1:])
        self.assertNotEqual(self.ids()[0], original); self.assertEqual(self.ids()[1:], [original, original])
        self.assertIsNone(self.obs[0].data.attributes.get(F.BEFORE_ATTR+'0'))
        for ob in self.obs[1:]:
            self.assertIsNotNone(ob.data.attributes.get(F.BEFORE_ATTR+'0'))
        self.assertFalse(self.mesh.use_fake_user)

    def test_outer_cancel_restores_accepted_child_and_original_sharing(self):
        self.stored_fold(); ids = self.ids(); before = self.co(); body = self.text.as_string()
        count = len(bpy.data.meshes)
        with PB.operation(self.ctx) as owner:
            self.unfold(preparation=owner)
            self.assertNotEqual(self.ids()[0], ids[0])
            owner.cancel()
        self.assertEqual(self.ids(), ids); np.testing.assert_array_equal(self.co(), before)
        self.assertEqual(self.text.as_string(), body); self.assertEqual(len(bpy.data.meshes), count)

    def test_external_only_mesh_user_is_not_rewound(self):
        self.stored_fold(); self.obs[1].data = self.mesh.copy()
        before = self.co(); outside = self.ids()[2]
        self.unfold()
        np.testing.assert_array_equal(self.co()[1:], before[1:]); self.assertEqual(self.ids()[2], outside)

    def test_two_physical_targets_do_not_share_their_unfold_writes(self):
        self.stored_fold()
        F.write_angle(self.ctx, 'B', self.lines['B'], 45., 'A')
        self.obs[1][F.FOLD_PROP] = json.dumps([dict(line=self.lines['B'], side=0, deg=45.)])
        before = self.co()
        with PB.operation(self.ctx) as owner:
            self.unfold(preparation=owner)
            np.testing.assert_array_equal(self.co()[1], before[1])
            ok, why = F.set_turn_side(self.ctx, 'B', self.lines['B'], 45., 'NONE', preparation=owner)
            self.assertTrue(ok, why)
        np.testing.assert_allclose(self.co()[:, 0, 2], [0, 0, 25])
        self.assertEqual(len(set(self.ids())), 3)

    def test_existing_owner_cannot_restore_over_external_text_edit(self):
        def conflict(*args, **kwargs):
            changed = S.loads(self.text.as_string()); changed['external_marker'] = 1729
            self.text.from_string(S.dumps(changed))
            raise ValueError('injected external source change')
        with PB.operation(self.ctx) as owner:
            with patch.object(F, 'refold_pair', side_effect=conflict):
                ok, why = F.set_turn_side(self.ctx, 'A', self.lines['A'], 45., 'A', preparation=owner)
            self.assertFalse(ok); self.assertEqual(S.loads(self.text.as_string())['external_marker'], 1729)
        self.assertEqual(S.loads(self.text.as_string())['external_marker'], 1729)

    def test_opaque_shared_inputs_keep_identity_on_refusal_and_explicit_isolation(self):
        for kind in ('shape_keys', 'string'):
            for isolated in (False, True):
                with self.subTest(kind=kind, isolated=isolated):
                    self.setUp()
                    if kind == 'shape_keys':
                        self.obs[0].shape_key_add(name='Basis'); self.obs[0].shape_key_add(name='Detail')
                    else:
                        attr = self.mesh.attributes.new('CustomText', 'STRING', 'POINT')
                        attr.data[0].value = b'preserve'
                    before = self.ids(); count = len(bpy.data.meshes)
                    if isolated:
                        backup = PV.PreparedResetBackup(self.ctx, self.sp)
                        try:
                            backup.isolate_shared_meshes({'A'}); backup.rollback()
                        finally:
                            backup.close()
                    else:
                        self.obs[0][PV.LAID_PROP] = True
                        ok, _ = F.set_turn_side(self.ctx, 'A', self.lines['A'], 45., 'A')
                        self.assertFalse(ok)
                    self.assertEqual(self.ids(), before)
                    self.assertEqual(len(bpy.data.meshes), count)
                    self.assertFalse(self.mesh.use_fake_user)

    def test_single_input_restores_unknown_mesh_properties(self):
        self.obs[0].data = self.mesh.copy(); self.obs[0].data['custom_unknown'] = 'original'
        with PB.operation(self.ctx) as owner:
            self.obs[0].data['custom_unknown'] = 'transient'; owner.cancel()
        self.assertEqual(self.obs[0].data['custom_unknown'], 'original')

    def test_last_live_user_cannot_write_the_shared_input_held_by_outer_backup(self):
        self.obs[2].data = self.mesh.copy(); self.stored_fold()
        F.write_angle(self.ctx, 'B', self.lines['B'], 45., 'A')
        self.obs[1][F.FOLD_PROP] = json.dumps([dict(line=self.lines['B'], side=0, deg=45.)])
        before = self.co(); ids = self.ids()
        with PB.operation(self.ctx) as owner:
            self.unfold(preparation=owner)
            ok, why = F.set_turn_side(self.ctx, 'B', self.lines['B'], 45., 'NONE', preparation=owner)
            self.assertTrue(ok, why); owner.cancel()
        self.assertEqual(self.ids(), ids); np.testing.assert_array_equal(self.co(), before)

    def test_pair_link_then_cancel_preserves_every_shared_user(self):
        from mathutils import Matrix
        self.obs[0].matrix_world = Matrix.Translation((.1, .2, 0)); self.ctx.view_layer.update()
        before = self.co(); ids = self.ids()
        with PB.operation(self.ctx) as owner:
            PV.link(self.ctx, self.sp, self.obs[0], self.obs[1]); owner.cancel()
        np.testing.assert_array_equal(self.co(), before); self.assertEqual(self.ids(), ids)

    def test_drop_then_reset_never_deletes_the_external_mesh_user(self):
        outside = self.obs[2]; name = outside.name; uid = outside.data.session_uid
        self.ctx.scene.collection.objects.link(outside)
        for col in list(outside.users_collection):
            if col != self.ctx.scene.collection:
                col.objects.unlink(outside)
        self.obs[0][PV.LAID_PROP] = True; before = self.co()[2]
        PV.drop(self.sp)
        self.assertNotEqual(PV._kept(self.sp)['A'].session_uid, uid)
        self.assertNotIn(PV.KEEP_PROP, outside.data)
        PV.remove(['A'], self.sp)
        outside = bpy.data.objects.get(name); self.assertIsNotNone(outside)
        self.assertEqual(outside.data.session_uid, uid)
        np.testing.assert_array_equal(np.array([tuple(v.co) for v in outside.data.vertices])*1000, before)

    def legacy_archive(self):
        self.mesh[PV.KEEP_PROP] = json.dumps(dict(PV.shape_record(self.obs[0]), garment=PV.garment(self.sp)))
        self.mesh.use_fake_user = True

    def test_forget_old_archive_with_live_users_preserves_their_data(self):
        self.legacy_archive(); before = self.co(); ids = self.ids()
        PV.forget(self.sp)
        self.assertEqual(self.ids(), ids); np.testing.assert_array_equal(self.co(), before)
        self.assertNotIn(PV.KEEP_PROP, self.mesh)

    def test_reusing_old_archive_does_not_modify_its_other_live_users(self):
        self.legacy_archive(); before = self.co(); ids = self.ids()
        mesh = PV._mesh('Restored', self.V, self.T, np.c_[self.V, np.zeros(4)], np.zeros(3))
        restored = bpy.data.objects.new('Restored', mesh); PV._collection(self.ctx).objects.link(restored)
        PV._give_back(restored, self.mesh)
        self.assertNotEqual(restored.data.session_uid, ids[0]); self.assertEqual(self.ids(), ids)
        np.testing.assert_array_equal(self.co(), before); self.assertNotIn(PV.KEEP_PROP, restored.data)

    def test_genuinely_single_keep_keeps_its_mesh_identity(self):
        for ob in self.obs[1:]:
            ob.data = self.mesh.copy()
        uid = self.obs[0].data.session_uid
        PV.keep(self.obs[0], self.sp)
        self.assertEqual(PV._kept(self.sp)['A'].session_uid, uid)

    def test_local_result_rebinds_generated_attribute_after_copy(self):
        self.obs[0][PV.SIM_PROP] = True; before = self.co()
        wanted = before[0]/1000 + [0, 0, .025]
        PV.write_local_result(self.obs[0], wanted)
        np.testing.assert_allclose(self.co()[0], wanted*1000, atol=1e-5)
        np.testing.assert_array_equal(self.co()[1:], before[1:])
        gen = self.obs[0].data.attributes[PV.GEN_ATTR]
        values = np.empty(12, np.float32); gen.data.foreach_get('vector', values)
        np.testing.assert_allclose(values.reshape(-1, 3), wanted, atol=1e-7)

    def test_layout_and_seam_shape_writes_do_not_touch_other_users(self):
        from ac9_cloth_sim import place_seam as PS
        original_flat = PV._attr_values(self.mesh.attributes[PV.FLAT_ATTR]).copy()
        before = self.co()
        PV.follow_layout(self.sp, {'A': (np.eye(2), [25, 0])})
        np.testing.assert_array_equal(PV._attr_values(self.mesh.attributes[PV.FLAT_ATTR]), original_flat)
        # A separate write target whose raw material points still match the input.
        wanted = np.c_[self.V, np.full(4, 25.)]
        self.assertIsNone(PS.write_shape(self.obs[1], self.V, self.T, wanted))
        np.testing.assert_allclose(self.co()[1], wanted, atol=1e-5)
        np.testing.assert_array_equal(self.co()[2], before[2])


if __name__ == '__main__':
    unittest.main()
