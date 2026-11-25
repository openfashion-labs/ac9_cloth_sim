# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy result adoption and refusal, including independent documents/scenes.

Finite synthetic triangles isolate identity from solver quality; no simulation.
Run in Blender: -- <package parent> <result.json>.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import ac9_cloth_sim as A
A.register()
from ac9_cloth_sim import control_basis as CB, display_state as DS, dress as D
from ac9_cloth_sim import garment_owner as GO, legacy_garment as LG
from ac9_cloth_sim import result_reference as RR, store as ST
from ac9_cloth_sim.core import draft, spec as S


class LegacyOwner(unittest.TestCase):
    measures = {}

    def setUp(self):
        self.home = bpy.context.window.scene
        self.scene = bpy.data.scenes.new('LegacyIdentityTest')
        bpy.context.window.scene = self.scene
        self.ctx = bpy.context
        self.docs, self.extra_scenes = [], []
        self.tmp = tempfile.TemporaryDirectory(prefix='ac9_legacy_identity_')
        self.flat = np.array([[0,0],[40,0],[0,40],[100,0],[140,0],[100,40]], float)
        self.X = np.column_stack((self.flat[:,0], np.zeros(6), self.flat[:,1]))
        self.F = np.array([[0,1,2],[3,4,5]], np.int32)
        self.fp = np.array([0,1], np.int32)
        areas = np.linalg.norm(np.cross(self.X[self.F[:,1]]-self.X[self.F[:,0]],
                                        self.X[self.F[:,2]]-self.X[self.F[:,0]]), axis=1)/2
        self.assertTrue(np.isfinite(self.X).all())
        np.testing.assert_array_equal(areas, [800,800])

    def tearDown(self):
        for sc in self.extra_scenes + [self.scene]:
            for ob in list(sc.objects):
                if bpy.data.objects.get(ob.name) == ob:
                    bpy.data.objects.remove(ob, do_unlink=True)
        bpy.context.window.scene = self.home
        for sc in self.extra_scenes + [self.scene]:
            if bpy.data.scenes.get(sc.name) == sc:
                bpy.data.scenes.remove(sc)
        for t in self.docs:
            if bpy.data.texts.get(t.name) == t:
                bpy.data.texts.remove(t)
        self.tmp.cleanup()
        ST.invalidate(); DS.invalidate()

    def doc(self, *, different=False, name='LegacyShirt'):
        sp = S.new_spec(name)
        for i, n in enumerate(('A', 'B')):
            draft.add_polygon(sp, self.flat[i*3:i*3+3].tolist(), name=n)
        sp['placement'] = {n: dict(guide='torso', theta=70. if different else 40., h=0., anchor=[0,0])
                           for n in ('A','B')}
        sp['guides'] = {'torso': dict(o=[0,0,0], R=np.eye(3).tolist(), r=100., h0=0., h1=100.)}
        t = ST.new_text(self.ctx, sp['name']); self.docs.append(t); ST.put(self.ctx, sp)
        return t, sp

    def garment(self, t, sp):
        self.ctx.scene.ac9_cloth_sim.spec_text = t; ST.invalidate()
        return D.garment_object(self.ctx, D.garment_name(sp), self.flat, self.F,
                                self.fp, self.X, ['A','B'])

    def job(self, g, sp):
        path = str(Path(self.tmp.name) / ('job_' + str(len(list(Path(self.tmp.name).iterdir())))))
        RR.write_result(path, 'carried', ['A','B'], np.array([0,3]), self.F,
                        self.fp, self.flat, self.X, S.dumps(sp))
        g['ac9_job'] = path
        return path

    def unclaim(self, g):
        for data, keys in ((g, [GO.GARMENT]), (g.data, [GO.GARMENT, CB.OWNER, CB.PROP])):
            for key in keys:
                data.pop(key, None)

    def fixture(self, *, same=False):
        t, sp = self.doc(); g = self.garment(t, sp); job = self.job(g, sp)
        other, sp2 = self.doc(different=not same)
        self.unclaim(g)
        self.assertIsNone(D.garment(sp2))
        return t, sp, g, other, sp2, job

    def co(self, g):
        q = np.empty((len(g.data.vertices),3)); g.data.vertices.foreach_get('co', q.ravel())
        return q

    def unchanged_refusal(self, reason, *, same=False, mutate=None):
        t, sp, g, other, sp2, job = self.fixture(same=same)
        if mutate: mutate(g, job, t, other, sp2)
        q = self.co(g).copy(); texts = {x.name:x.as_string() for x in self.docs}
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.assertNotIn(GO.GARMENT, g)
        np.testing.assert_array_equal(q, self.co(g))
        self.assertEqual(texts, {x.name:x.as_string() for x in self.docs})
        self.assertIn(reason, [r for _n,r in LG.notices(self.ctx)])

    def test_exact_record_binds_nonactive_document_without_changing_any_shape(self):
        t, sp, g, other, sp2, job = self.fixture()
        self.ctx.scene.ac9_cloth_sim.spec_text = other; ST.invalidate()
        q = self.co(g).copy(); texts = {x.name:x.as_string() for x in self.docs}
        self.assertEqual(LG.reconcile(self.ctx), [g.name])
        self.assertEqual(g[GO.GARMENT], t)
        self.assertIsNone(D.garment(sp2)); self.assertEqual(D.garment(sp, owner=t), g)
        np.testing.assert_array_equal(q, self.co(g))
        self.assertEqual(texts, {x.name:x.as_string() for x in self.docs})
        self.assertNotIn(CB.PROP, g.data)
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.measures['positive'] = {'qualified':1, 'mesh_change_mm':0, 'nonactive_owner':True}

    def test_same_pattern_twice_is_ambiguous(self):
        self.unchanged_refusal(LG.REASONS['many'], same=True)

    def test_unrelated_unique_document_cannot_recognize_or_block_the_legacy_shirt(self):
        t,sp,g,other,sp2,j=self.fixture()
        jacket,js=self.doc(name='UnrelatedJacket')
        self.unclaim(g)  # A document publication itself may already run reconciliation.
        self.assertIsNone(D.garment(js))
        self.assertFalse(GO.owns(g,GO.GARMENT,js,owner=jacket,legacy_name=js['name']))
        self.assertEqual(LG.reconcile(self.ctx),[g.name])
        self.assertEqual(g[GO.GARMENT],t)
        self.assertIsNone(D.garment(js))

    def test_unrelated_garment_keeps_its_own_shape_and_visibility(self):
        t,sp,g,other,sp2,j=self.fixture()
        jacket,js=self.doc(name='UnrelatedJacket');jacket_g=self.garment(jacket,js)
        q=self.co(jacket_g).copy();hidden=jacket_g.hide_get()
        DS._sync_all()
        self.assertEqual(g[GO.GARMENT],t);self.assertEqual(D.garment(js),jacket_g)
        np.testing.assert_array_equal(q,self.co(jacket_g))
        self.assertEqual(jacket_g.hide_get(),hidden)

    def test_no_matching_document_keeps_both_documents(self):
        def mutate(g, job, t, other, sp2):
            saved = json.loads(Path(job, RR.SPEC).read_text(encoding='utf8'))
            saved['placement']['A']['theta'] = 123.
            Path(job, RR.SPEC).write_text(S.dumps(saved), encoding='utf8')
        self.unchanged_refusal(LG.REASONS['none'], mutate=mutate)

    def test_missing_job_is_reported_without_creation(self):
        self.unchanged_refusal(LG.REASONS['missing'],
                              mutate=lambda g,j,*x: g.__setitem__('ac9_job', j+'_absent'))

    def test_corrupt_spec_is_reported(self):
        self.unchanged_refusal(LG.REASONS['record'],
                              mutate=lambda g,j,*x: Path(j, RR.SPEC).write_text('{broken',encoding='utf8'))

    def test_truncated_index_is_reported_without_aborting_sync(self):
        def mutate(g,j,*x):
            p=Path(j,RR.INDEX); p.write_bytes(p.read_bytes()[:64])
        self.unchanged_refusal(LG.REASONS['record'],mutate=mutate)

    def test_empty_index_is_reported_without_aborting_sync(self):
        self.unchanged_refusal(LG.REASONS['record'],
                              mutate=lambda g,j,*x: Path(j,RR.INDEX).write_bytes(b''))

    def test_wrong_saved_label_still_notifies_the_original_document(self):
        def mutate(g,j,*x):
            p=Path(j,RR.SPEC); saved=json.loads(p.read_text(encoding='utf8'))
            saved['name']='AnAbsentOtherGarment'; p.write_text(S.dumps(saved),encoding='utf8')
        self.unchanged_refusal(LG.REASONS['none'],mutate=mutate)

    def test_bad_uv_is_not_relabelled(self):
        self.unchanged_refusal(LG.REASONS['layout'],
                              mutate=lambda g,*x: setattr(g.data.uv_layers['UVMap'].data[0], 'uv', (.2,.4)))

    def test_bad_face_piece_is_not_relabelled(self):
        self.unchanged_refusal(LG.REASONS['layout'],
                              mutate=lambda g,*x: setattr(g.data.attributes['ac9_piece'].data[0], 'value', 1))

    def test_same_counts_wrong_topology_is_not_relabelled(self):
        def mutate(g,j,*x):
            with np.load(Path(j, RR.INDEX)) as z: data={k:z[k] for k in z.files}
            data['F'] = data['F'][:, [0,2,1]]
            np.savez(Path(j, RR.INDEX), **data)
        self.unchanged_refusal(LG.REASONS['layout'], mutate=mutate)

    def test_shared_scene_object_is_not_claimed(self):
        def mutate(g,*x):
            sc=bpy.data.scenes.new('OtherLegacyScene'); self.extra_scenes.append(sc)
            sc.collection.objects.link(g)
        self.unchanged_refusal(LG.REASONS['shared'], mutate=mutate)

    def test_shared_mesh_is_not_claimed(self):
        def mutate(g,*x):
            ob=bpy.data.objects.new('OtherSharedGeometry',g.data); self.scene.collection.objects.link(ob)
        self.unchanged_refusal(LG.REASONS['shared'], mutate=mutate)

    def test_owner_active_in_another_scene_is_not_claimed(self):
        def mutate(g,j,t,*x):
            sc=bpy.data.scenes.new('OtherOwnerScene'); self.extra_scenes.append(sc)
            sc.ac9_cloth_sim.spec_text=t
        self.unchanged_refusal(LG.REASONS['shared'], mutate=mutate)

    def test_two_unowned_results_are_both_preserved(self):
        t,sp,g,other,sp2,j=self.fixture()
        clone=g.copy(); clone.data=g.data.copy(); self.scene.collection.objects.link(clone)
        self.ctx.scene.ac9_cloth_sim.spec_text=t; ST.invalidate()
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.assertNotIn(GO.GARMENT,g); self.assertNotIn(GO.GARMENT,clone)
        self.assertEqual(len(LG.notices(self.ctx)),2)

    def test_existing_owned_result_blocks_legacy_duplicate(self):
        t,sp,g,other,sp2,j=self.fixture()
        clone=g.copy(); clone.data=g.data.copy(); clone[GO.GARMENT]=t
        self.scene.collection.objects.link(clone)
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.assertNotIn(GO.GARMENT,g); self.assertEqual(clone[GO.GARMENT],t)

    def test_explicit_foreign_claim_is_never_overwritten(self):
        t,sp,g,other,sp2,j=self.fixture(); g[GO.GARMENT]=other
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.assertEqual(g[GO.GARMENT],other)

    def test_conflicting_mesh_owner_is_preserved_and_reported(self):
        t,sp,g,other,sp2,j=self.fixture(); g[GO.GARMENT]=t; g.data[CB.OWNER]=other
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.assertEqual(g[GO.GARMENT],t); self.assertEqual(g.data[CB.OWNER],other)
        self.assertIn(LG.REASONS['claimed'],[r for n,r in LG.notices(self.ctx)])

    def test_null_claim_is_not_absent(self):
        t,sp,g,other,sp2,j=self.fixture(); g[GO.GARMENT]=None
        self.assertIn(GO.GARMENT,g)
        self.assertEqual(LG.reconcile(self.ctx), [])
        self.assertIsNone(g[GO.GARMENT])
        self.assertIn(LG.REASONS['claimed'],[r for n,r in LG.notices(self.ctx)])

    def test_notifications_do_not_parse_or_touch_the_disk(self):
        t,sp,g,other,sp2,j=self.fixture(same=True); LG.reconcile(self.ctx)
        from ac9_cloth_sim import ui
        with patch.object(D,'garment_records',side_effect=AssertionError('reader parses documents')), \
                patch('builtins.open',side_effect=AssertionError('reader opens disk')):
            self.assertTrue(ui.AC9_PT_SimLegacyOwner.poll(self.ctx))
            self.assertEqual(len(LG.notices(self.ctx)),1)

    def test_explicit_retry_recovers_after_evidence_returns(self):
        t,sp,g,other,sp2,j=self.fixture(); g['ac9_job']=j+'_absent'; LG.reconcile(self.ctx)
        self.assertTrue(LG.notices(self.ctx))
        from ac9_cloth_sim import pattern_view
        pattern_view.rebuild(self.ctx,sp2,ST.get(self.ctx)[1])
        g['ac9_job']=j
        self.assertEqual(bpy.ops.ac9_cloth_sim.recheck_garment_owner(),{'FINISHED'})
        self.assertEqual(g[GO.GARMENT],t); self.assertEqual(LG.notices(self.ctx),[])

    def test_load_handler_qualifies_before_display_and_keeps_other_garment(self):
        t,sp,g,other,sp2,j=self.fixture(); second=self.garment(other,sp2)
        q=self.co(second).copy(); owner=second[GO.GARMENT]; hidden=second.hide_get()
        DS._on_load()
        self.assertEqual(g[GO.GARMENT],t)
        np.testing.assert_array_equal(q,self.co(second))
        self.assertEqual(second[GO.GARMENT],owner); self.assertEqual(second.hide_get(),hidden)
        self.assertEqual(DS.snapshot(sp,g,owner=t)['A']['source'],'result')
        self.assertEqual(D.garment(sp2),second)

    def test_qualified_scene_does_not_reread_documents_on_operations(self):
        t,sp,g,other,sp2,j=self.fixture(); LG.reconcile(self.ctx)
        with patch.object(D,'garment_records',side_effect=AssertionError('qualified scene parsed again')):
            self.assertEqual(LG.reconcile(self.ctx),[])

    def test_actual_save_load_keeps_both_documents_and_the_other_result(self):
        t,sp,g,other,sp2,j=self.fixture(); second=self.garment(other,sp2)
        scene_name,home_name=self.scene.name,self.home.name
        names=[d.name for d in self.docs]; gname,sname=g.name,second.name
        texts={d.name:d.as_string() for d in self.docs}; q=self.co(second).copy()
        path=Path(self.tmp.name)/'legacy_load.blend'
        bpy.context.preferences.filepaths.save_version=0
        bpy.context.preferences.filepaths.file_preview_type='NONE'
        bpy.ops.wm.save_as_mainfile(filepath=str(path),relative_remap=False)
        bpy.ops.wm.open_mainfile(filepath=str(path))
        self.scene=bpy.data.scenes[scene_name]; self.home=bpy.data.scenes[home_name]
        self.docs=[bpy.data.texts[n] for n in names]
        g=bpy.data.objects[gname]; second=bpy.data.objects[sname]
        self.assertEqual(g[GO.GARMENT].name,names[0])
        self.assertEqual(second[GO.GARMENT].name,names[1])
        np.testing.assert_array_equal(q,self.co(second))
        self.assertEqual(texts,{d.name:d.as_string() for d in self.docs})
        self.assertEqual(LG.notices(self.ctx),[])


result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LegacyOwner))
Path(args[1]).write_text(json.dumps({'count':result.testsRun,
    'health':{'scope':'identity-only finite triangles, no solver','area_mm2':[800,800]},
    'measurements':LegacyOwner.measures,'failures':[tb for _,tb in result.failures],
    'errors':[tb for _,tb in result.errors]},indent=2),encoding='utf8')
if not result.wasSuccessful(): raise RuntimeError('Legacy ownership regression failed')
