"""Placement settings and derived shapes share one refusal boundary.

Healthy finite synthetic rectangles isolate ownership, not garment physics.
The standard saved garment is covered by the separate native/metadata probe.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_fold_shared_mesh as FX
from ac9_cloth_sim import placement as PL, placed_view as PV, store as ST, dress as DR, prepare_build as PB
from ac9_cloth_sim import look_reference as LR
from ac9_cloth_sim.core import spec as S


class PlacementSettings(unittest.TestCase):
    co = FX.SharedFold.co
    ids = FX.SharedFold.ids

    def setUp(self):
        FX.SharedFold.setUp(self)
        self.before = self.text.as_string()
        self.coordinates = self.co()
        self.identities = self.ids()
        self.next = copy.deepcopy(self.sp)
        self.next['placement']['A']['anchor'] = [60, 50]

    def call(self):
        return PL._remade(self.ctx, self.sp, self.next, ['A'])

    def untouched(self):
        self.assertEqual(self.text.as_string(), self.before)
        self.assertEqual(self.ids(), self.identities)
        np.testing.assert_array_equal(self.co(), self.coordinates)

    def changed(self, context, spec, names, *, preparation=None):
        self.assertIsNotNone(preparation)
        mesh = PV.writable_mesh(self.obs[0])
        xyz = np.array([v.co[:] for v in mesh.vertices]); xyz[:,2] += .005
        mesh.vertices.foreach_set('co', xyz.astype(np.float32).ravel()); mesh.update()
        return ['private rebuilt']

    def test_rebuild_exception_restores_text_and_shared_shape(self):
        def reject(*args, **kwargs):
            self.changed(*args, **kwargs)
            raise ValueError('private refusal')
        with patch.object(PL, 'rebuild_placement', side_effect=reject):
            with self.assertRaisesRegex(ValueError, 'private refusal'): self.call()
        self.untouched()

    def test_refresh_exception_restores_owned_text(self):
        with patch.object(ST, 'refresh', side_effect=ValueError('private refresh refusal')):
            with self.assertRaisesRegex(ValueError, 'private refresh refusal'): self.call()
        self.untouched()

    def test_write_tail_exception_restores_only_the_exact_candidate(self):
        with patch.object(LR, 'bind_owner', side_effect=ValueError('private write tail refusal')):
            with self.assertRaisesRegex(ValueError, 'private write tail refusal'): self.call()
        self.untouched()

    def test_foreign_write_tail_is_not_adopted(self):
        foreign = copy.deepcopy(self.next); foreign['private_foreign'] = 'tail preserve'
        def reject(*args, **kwargs):
            self.text.clear(); self.text.write(S.dumps(foreign)); ST.invalidate()
            raise ValueError('foreign write tail')
        with patch.object(LR, 'bind_owner', side_effect=reject):
            with self.assertRaisesRegex(ValueError, 'foreign write tail'): self.call()
        self.assertEqual(ST.get(self.ctx)[0]['private_foreign'], 'tail preserve')

    def test_finish_exception_restores_the_accepted_child(self):
        with patch.object(PL, 'rebuild_placement', side_effect=self.changed), \
                patch.object(DR.Preparation, 'finish', side_effect=ValueError('private finish refusal')):
            with self.assertRaisesRegex(ValueError, 'private finish refusal'): self.call()
        self.untouched()

    def test_normal_child_refusal_does_not_report_success(self):
        def reject(*args, **kwargs):
            self.changed(*args, **kwargs)
            PB.cancel(kwargs['preparation'])
            return ['private domain refusal']
        with patch.object(PL, 'rebuild_placement', side_effect=reject), \
                patch.object(PL,'shape_kinds',return_value={'A':['private old edits']}):
            with self.assertRaisesRegex(ValueError, 'private domain refusal') as error: self.call()
        self.assertEqual(str(error.exception),'private domain refusal')
        self.untouched()

    def test_success_keeps_new_settings_and_only_changes_its_shape(self):
        with patch.object(PL, 'rebuild_placement', side_effect=self.changed):
            self.assertEqual(self.call(), ['private rebuilt'])
        self.assertEqual(ST.get(self.ctx)[0]['placement']['A']['anchor'], [60,50])
        self.assertNotEqual(self.ids()[0], self.identities[0])
        self.assertEqual(self.ids()[1:], self.identities[1:])
        np.testing.assert_array_equal(self.co()[1:], self.coordinates[1:])
        np.testing.assert_allclose(self.co()[0,:,2], 5., atol=.00001)

    def test_foreign_text_is_not_overwritten_on_refusal(self):
        self.ctx.view_layer.objects.active=self.obs[0]
        foreign = copy.deepcopy(self.next); foreign['private_foreign'] = 'preserve'
        def reject(*args, **kwargs):
            self.ctx.view_layer.objects.active=self.obs[1]
            self.text.clear(); self.text.write(S.dumps(foreign)); ST.invalidate()
            raise ValueError('foreign edit')
        with patch.object(PL, 'rebuild_placement', side_effect=reject):
            with self.assertRaisesRegex(ValueError, 'foreign edit'): self.call()
        self.assertEqual(ST.get(self.ctx)[0]['private_foreign'], 'preserve')
        self.assertEqual(self.ids(), self.identities)
        self.assertEqual(self.ctx.view_layer.objects.active,self.obs[1])

    def test_removed_active_returns_to_its_owned_replacement(self):
        old=self.obs[0];old_uid=int(old.session_uid)
        self.ctx.view_layer.objects.active=old
        def reject(*args,**kwargs):
            bpy.data.objects.remove(old,do_unlink=True)
            raise ValueError('removed active')
        with patch.object(PL,'rebuild_placement',side_effect=reject):
            with self.assertRaisesRegex(ValueError,'removed active'):self.call()
        active=self.ctx.view_layer.objects.active
        self.assertEqual(active,PV.objects(ST.get(self.ctx)[0])['A'])
        self.assertNotEqual(int(active.session_uid),old_uid)
        self.assertEqual(self.text.as_string(),self.before)

    def test_none_active_is_restored(self):
        self.ctx.view_layer.objects.active=None
        def reject(*args,**kwargs):
            self.ctx.view_layer.objects.active=self.obs[1]
            raise ValueError('changed active')
        with patch.object(PL,'rebuild_placement',side_effect=reject):
            with self.assertRaisesRegex(ValueError,'changed active'):self.call()
        self.assertIsNone(self.ctx.view_layer.objects.active)
