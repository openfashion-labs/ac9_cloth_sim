# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless placement and save/load contracts. Pass the package parent and JSON output path after --."""
import copy
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

import bpy
import numpy as np

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import ac9_cloth_sim
from ac9_cloth_sim import control_basis as CB, dress as D, place_candidate as PC, place_fold as PF
from ac9_cloth_sim import placed_view as PV, placement as PL, store, selection as SEL
from ac9_cloth_sim.core import draft, spec as S


class PlacementCandidate(unittest.TestCase):
    measurements = {}

    def setUp(self):
        self.ctx = bpy.context
        bpy.ops.mesh.primitive_cube_add(size=.1, location=(10., 0., 0.))
        self.ctx.scene.ac9_cloth_sim.body = self.ctx.object
        self.ctx.scene.ac9_cloth_sim.mesh_size = 12.
        self.sp = S.new_spec(self._testMethodName)
        draft.add_polygon(self.sp, [[0, 0], [300, 0], [300, 200], [0, 200]], name='A')
        self.sp['guides'] = {'torso': dict(o=[0, 0, 0], R=np.eye(3).tolist(), r=100, h0=-200, h1=200)}
        self.sp['placement'] = {'A': dict(guide='torso', theta=90., h=0., anchor=[150, 100])}
        store.new_text(self.ctx, self.sp['name'])
        store.write(self.ctx, self.sp, keep=True)

    def install_mixed_garment(self):
        sp = self.sp
        draft.add_polygon(sp, [[500, 0], [800, 0], [800, 200], [650, 200], [500, 200]], name='B')
        draft.add_polygon(sp, [[10, 330], [100, 330], [100, 410], [10, 410]], name='C')
        sp['pairs'] = [['A', 'B']]
        sp['placement']['B'] = dict(guide='torso', theta=113., h=21., anchor=[650, 100])
        sp['placement']['C'] = dict(guide='torso', theta=190., h=11., anchor=[55, 370])
        store.put(self.ctx, sp)
        x = np.array([[1, 0, 0], [20, 0, 0], [3, 0, 20],
                      [40, 0, 0], [60, 0, 0], [40, 7, 20],
                      [80, 0, 0], [90, 0, 0], [80, 0, 10]], float)
        faces = np.arange(9).reshape(3, 3)
        areas = np.linalg.norm(np.cross(x[faces[:, 1]] - x[faces[:, 0]], x[faces[:, 2]] - x[faces[:, 0]]), axis=1)
        self.assertTrue(np.isfinite(x).all() and np.all(areas > 0))
        return D.garment_object(self.ctx, D.garment_name(sp), x[:, [0, 2]], faces, np.arange(3), x,
                                ['A', 'B', 'C'], editing_basis={'A': CB.PLACED, 'B': CB.RESULT, 'C': CB.PLACED})

    def press(self):
        """Run the actual press. Replace viewport hit testing and modal scheduling for background Blender."""
        op = SimpleNamespace(_hit=lambda *_: True, _draw=lambda *_: None, _status=lambda *_: None)
        event = SimpleNamespace(ctrl=False, mouse_region_x=20, mouse_region_y=30)
        SEL.apply(self.ctx, ['A'], active='A')
        with patch.object(D, 'standby_wake'), patch.object(PL, 'selected_pieces', return_value=['A']), \
                patch.object(PL.TB, 'under', return_value=(None, None, -1)), \
                patch.object(PL.TB, 'start_modal', return_value={'CANCELLED'}) as schedule:
            PL.AC9_OT_SimPlace.invoke(op, self.ctx, event)
            schedule.assert_called_once()
        self.addCleanup(bpy.types.SpaceView3D.draw_handler_remove, op._handle, 'WINDOW')
        op._placements = lambda hit: {'A': dict(op.spec['placement']['A'], theta=float(hit))}
        op.hit = 22
        return op

    def update(self, op):
        PL.AC9_OT_SimPlace._update_candidate(op, self.ctx)
        return op._candidate

    def test_fold_pose_preview_commit_and_unfold(self):
        sp = self.sp
        axis = draft.add_internal(sp, 'A', [[150, 0], [150, 200]], mirror=False)
        sp['folds'] = [dict(piece='A', line=axis, deg=45., side='A')]
        sp['placement']['A']['pose'] = dict(loc=[11, 9, 0], rot=[26, 15, 34])
        store.put(self.ctx, sp)
        res = D.prepare(self.ctx, 12., shapes_only=True, placed_only=True)
        PV.build(self.ctx, sp, res['pat'], res['tris'], res['X'], res['pairs'])
        original = PV._world_co(PV.objects(sp)['A']) * 1000.
        self.assertEqual(original.shape, (488, 3))
        self.assertTrue(np.isfinite(original).all())
        candidate = PC.build(self.ctx, sp, ['A'])
        PC.commit(self.ctx, candidate)
        ob = PV.objects(sp)['A']
        folded = PV._world_co(ob) * 1000.
        self.assertGreater(np.linalg.norm(folded - original, axis=1).max(), 1.)
        preview_error = float(np.linalg.norm(folded - candidate['X'][0], axis=1).max())
        PF._unfold(ob, json.loads(ob[PV.FOLD_PROP]), 0)
        unfold_error = float(np.linalg.norm(PV._world_co(ob) * 1000. - original, axis=1).max())
        self.measurements['fold_pose'] = dict(vertices=len(original), preview_max_mm=preview_error,
                                               unfold_max_mm=unfold_error)
        self.assertLess(preview_error, .00005)
        self.assertLess(unfold_error, .00005)

    def separate_pair(self, folds=False):
        sp = self.sp
        draft.add_polygon(sp, [[500, 0], [800, 0], [800, 200], [650, 200], [500, 200]], name='B')
        sp['pairs'] = [['A', 'B']]
        sp['placement'], _ = S.mirror_twins(sp)
        axes = {}
        if folds:
            for name, x in [('A', 150), ('B', 650)]:
                axes[name] = draft.add_internal(sp, name, [[x, 0], [x, 200]], mirror=False)
            sp['folds'] = [dict(piece=n, line=line, deg=45., side='A') for n, line in axes.items()]
        store.put(self.ctx, sp)
        return axes

    def assert_candidate(self, candidate):
        PC.commit(self.ctx, candidate)
        self.ctx.view_layer.update()
        obs = PV.objects(candidate['spec'])
        self.assertIsNot(obs['A'], obs['B'])
        if not candidate['spec'].get('folds'):
            self.assertNotEqual(len(obs['A'].data.vertices), len(obs['B'].data.vertices))
        self.assertFalse(candidate['res']['pairs'])
        errors = []
        for name in ('A', 'B'):
            i = list(candidate['res']['pat']['names']).index(name)
            expected = candidate['X'][i]
            tri = np.asarray(candidate['res']['tris'][i])
            area2 = np.linalg.norm(np.cross(expected[tri[:, 1]] - expected[tri[:, 0]],
                                            expected[tri[:, 2]] - expected[tri[:, 0]]), axis=1)
            self.assertTrue(np.all(area2 > 1e-9))
            actual = PV._world_co(obs[name]) * 1000.
            self.assertTrue(np.isfinite(actual).all())
            errors.append(float(np.linalg.norm(actual - expected, axis=1).max()))
            self.assertLess(errors[-1], .0001)
            self.assertFalse(PV.has_edits(obs[name]))
        return max(errors)

    def test_separate_pair_angles_reset_both_free_transforms(self):
        self.separate_pair()
        worst = 0.
        for lead in ('A', 'B'):
            for theta in range(-180, 181, 45):
                sp = copy.deepcopy(store.get(self.ctx)[0])
                pose = dict(loc=[30, 20, 11], rot=[26, 15, 34])
                sp['placement']['A']['pose'] = pose
                sp['placement']['B']['pose'] = S.mirror_pose(pose)
                store.put(self.ctx, sp)
                p = sp['placement'][lead]
                op = SimpleNamespace(spec=sp, rel={lead: p}, press=(p['guide'], p['theta'], p['h']),
                                     hit=(p['guide'], theta, p['h'], 0, None),
                                     _source_owner=store.edit_owner(self.ctx), _candidate_cache={})
                op._placements = lambda hit: PL.AC9_OT_SimPlace._placements(op, hit)
                PL.AC9_OT_SimPlace._update_candidate(op, self.ctx)
                c = op._candidate
                self.assertEqual(c['selected'], {'A', 'B'})
                self.assertTrue(all('pose' not in c['spec']['placement'][n] for n in ('A', 'B')))
                worst = max(worst, self.assert_candidate(c))
        self.measurements['separate_pair_angles'] = dict(conditions=18, preview_max_mm=worst)

    def test_separate_pair_pose_fold_history_and_relink(self):
        axes = self.separate_pair(folds=True)
        worst = 0.
        for posed in ('A', 'B'):
            sp = copy.deepcopy(self.sp)
            sp['placement'][posed]['pose'] = dict(loc=[11, 9, 0], rot=[26, 15, 34])
            store.put(self.ctx, sp)
            c = PC.build(self.ctx, sp, ['A', 'B'])
            worst = max(worst, self.assert_candidate(c))
            obs = PV.objects(sp)
            def history(ob):
                values = np.empty((len(ob.data.vertices), 3))
                ob.data.attributes[PF.BEFORE_ATTR + '0'].data.foreach_get('vector', values.ravel())
                return (np.c_[values, np.ones(len(values))] @ np.asarray(ob.matrix_world).T)[:, :3]
            before = {n: history(ob) for n, ob in obs.items()}
            PV.make_lead(self.ctx, sp, 'B')
            PV.make_lead(self.ctx, sp, 'A')
            worlds = {n: PV._world_co(ob) for n, ob in obs.items()}
            PV.center_control_origins(self.ctx, sp, [obs['A']])
            for name in ('A', 'B'):
                np.testing.assert_allclose(history(obs[name]), before[name], atol=1e-7)
                np.testing.assert_allclose(PV._world_co(obs[name]), worlds[name], atol=1e-7)
                self.assertFalse(PV.has_edits(obs[name]))
            # Public fold controls must work on the reflected follower, including repeated changes and undo.
            for name in ('A', 'B'):
                for angle in (75., 0., 45., 0.):
                    result = PF.refold(self.ctx, name, axes[name], angle)
                    self.assertTrue(result and result[0], result)
                    self.assertFalse(PV.has_edits(obs[name]))
                np.testing.assert_allclose(PV._world_co(obs[name]), before[name], atol=1e-7)
        self.measurements['separate_pair_fold_pose'] = dict(conditions=2, preview_max_mm=worst,
                                                            public_fold_changes=16, history_max_m=1e-7)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'pair.blend')
            worlds = {n: PV._world_co(ob) for n, ob in obs.items()}
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            for n, ob in PV.objects(sp).items():
                np.testing.assert_allclose(PV._world_co(ob), worlds[n], atol=1e-7)
                self.assertFalse(PV.has_edits(ob))

    def gesture(self, selected, touched, hit):
        SEL.apply(self.ctx, selected, active=selected[0] if selected else None, view='world')
        op = SimpleNamespace(dragged=True, _draw=lambda *_: None, _status=lambda *_: None)
        op._hit = lambda *a, **k: hit
        event = SimpleNamespace(ctrl=True, mouse_region_x=20, mouse_region_y=30)
        with patch.object(D, 'standby_wake'), patch.object(PL.TB, 'under', return_value=('PLACED', touched, 0)), \
                patch.object(PL.TB, 'start_modal', return_value={'CANCELLED'}):
            PL.AC9_OT_SimPlace.invoke(op, self.ctx, event)
        self.addCleanup(bpy.types.SpaceView3D.draw_handler_remove, op._handle, 'WINDOW')
        op._placements = lambda h: PL.AC9_OT_SimPlace._placements(op, h)
        return op

    def test_gesture_unselected_cover_places_selection_and_keeps_it_on_release(self):
        self.separate_pair()
        self.sp['pairs'] = []
        self.sp['guides']['leg_L'] = dict(o=[400, 0, 0], R=np.eye(3).tolist(), r=80, h0=-200, h1=200)
        self.sp['placement']['B']['guide'] = 'leg_L'
        store.put(self.ctx, self.sp)
        PC.commit(self.ctx, PC.build(self.ctx, self.sp, ['A', 'B']))
        cover = PV.objects(self.sp)['B']
        before = PV._world_co(cover).copy()
        op = self.gesture(['A'], cover, ('leg_L', 40., 0., 1., None))
        self.assertIsNone(op.drag_piece)
        self.assertIsNone(op.rel_guide)
        PL.AC9_OT_SimPlace._update_candidate(op, self.ctx)
        self.assertEqual(op._candidate['selected'], {'A'})
        self.assertEqual(op._candidate['spec']['placement']['A']['guide'], 'leg_L')
        self.assertEqual(SEL.pieces(self.ctx), ['A'])
        # Actual modal release, including the rebuilt objects' selection projection.
        op.pressed=True;op.hit=('leg_L',40.,0.,1.,None);op._finish=lambda *_:None;op.report=lambda *_:None
        op._update_candidate=lambda c: PL.AC9_OT_SimPlace._update_candidate(op,c)
        class Context:
            area=SimpleNamespace(tag_redraw=lambda:None)
            def __getattr__(self,k):return getattr(bpy.context,k)
        context=Context()
        result=PL.AC9_OT_SimPlace._modal(op,context,SimpleNamespace(type='LEFTMOUSE',value='RELEASE',ctrl=True,mouse_region_x=21,mouse_region_y=31))
        self.assertEqual(result,{'FINISHED'})
        self.assertEqual(SEL.pieces(self.ctx), ['A'])
        np.testing.assert_array_equal(PV._world_co(cover), before)

    def test_gesture_shared_mirror_unselected_half_is_a_destination(self):
        draft.mirror_copy(self.sp,'A')
        self.sp['pieces'][-1]['name']='B'
        self.sp['placement'],_=S.mirror_twins(self.sp)
        store.put(self.ctx,self.sp)
        PC.commit(self.ctx,PC.build(self.ctx,self.sp,['A','B']))
        obs=PV.objects(self.sp)
        self.assertIs(obs['A'],obs['B'])
        with patch.object(PV,'placed_face_piece',return_value='B'):
            op=self.gesture(['A'],obs['B'],('torso',25.,0.,1.,None))
        self.assertIsNone(op.drag_piece)
        self.assertIsNone(op.rel)
        PL.AC9_OT_SimPlace._update_candidate(op,self.ctx)
        PC.commit(self.ctx,op._candidate)
        self.assertEqual(SEL.pieces(self.ctx),['A'])

    def test_gesture_mixed_guides_does_not_narrow_selection(self):
        self.separate_pair();self.sp['pairs']=[]
        self.sp['guides']['leg_L']=dict(o=[400,0,0],R=np.eye(3).tolist(),r=80,h0=-200,h1=200)
        self.sp['placement']['B']['guide']='leg_L';store.put(self.ctx,self.sp)
        PC.commit(self.ctx,PC.build(self.ctx,self.sp,['A','B']))
        op=self.gesture(['A','B'],PV.objects(self.sp)['A'],('torso',10.,0.,1.,None))
        self.assertIsNone(op.rel)
        PL.AC9_OT_SimPlace._update_candidate(op,self.ctx)
        self.assertEqual(op._candidate['selected'],{'A','B'})

    def test_gesture_selected_piece_keeps_slide_and_cancel_selection(self):
        PC.commit(self.ctx,PC.build(self.ctx,self.sp,['A']))
        before=PV._world_co(PV.objects(self.sp)['A']).copy()
        op=self.gesture(['A'],PV.objects(self.sp)['A'],('torso',10.,0.,1.,None))
        self.assertEqual(op.rel_guide,'torso')
        self.assertEqual(set(op.rel),{'A'})
        PL.AC9_OT_SimPlace._update_candidate(op,self.ctx)
        op._finish=lambda *_:None
        ctx=SimpleNamespace(area=SimpleNamespace(tag_redraw=lambda:None))
        self.assertEqual(PL.AC9_OT_SimPlace._modal(op,ctx,SimpleNamespace(type='ESC',value='PRESS',ctrl=True)),{'CANCELLED'})
        np.testing.assert_array_equal(PV._world_co(PV.objects(self.sp)['A']),before)
        self.assertEqual(SEL.pieces(self.ctx),['A'])

    def test_gesture_selected_result_is_not_a_slide_reference(self):
        self.separate_pair();self.sp['pairs']=[];store.put(self.ctx,self.sp)
        PC.commit(self.ctx,PC.build(self.ctx,self.sp,['A','B']))
        ob=PV.objects(self.sp)['B'];before=PV._world_co(ob).copy()
        PV.write_local_result(ob,before)
        self.assertEqual(CB.contexts(self.ctx,self.sp)['B']['basis'],CB.RESULT)
        op=self.gesture(['A','B'],ob,('torso',20.,0.,1.,None))
        self.assertIsNone(op.drag_piece)
        self.assertIsNone(op.rel_guide)
        PL.AC9_OT_SimPlace._update_candidate(op,self.ctx)
        self.assertEqual(op._candidate['selected'],{'A'})
        PC.commit(self.ctx,op._candidate)
        np.testing.assert_array_equal(PV._world_co(ob),before)
        self.assertEqual(SEL.pieces(self.ctx),['A','B'])

    def test_gesture_foreign_document_same_id_is_not_a_slide_reference(self):
        PC.commit(self.ctx,PC.build(self.ctx,self.sp,['A']))
        foreign=PV.objects(self.sp)['A'].copy()
        self.ctx.scene.collection.objects.link(foreign)
        foreign['ac9_placed_spec']=bpy.data.texts.new('Other garment owner')
        self.assertFalse(SEL.owns(self.ctx,foreign))
        op=self.gesture(['A'],foreign,('torso',20.,0.,1.,None))
        self.assertIsNone(op.drag_piece)
        self.assertIsNone(op.rel_guide)
        self.assertEqual(SEL.pieces(self.ctx),['A'])

    def test_gesture_invalid_reference_never_falls_back_to_free_move(self):
        PC.commit(self.ctx,PC.build(self.ctx,self.sp,['A']))
        ob=PV.objects(self.sp)['A'];SEL.apply(self.ctx,['A'],active='A',view='world')
        before=PV._world_co(ob).copy();source=store.text(self.ctx).as_string()
        event=SimpleNamespace(ctrl=True,shift=False,mouse_region_x=20,mouse_region_y=30)
        place=SimpleNamespace(dragged=True,_hit=lambda *a,**k:None)
        free=Mock(return_value={'RUNNING_MODAL'})
        press=SimpleNamespace(p=dict(event=event,kind='PLACED',idx=0),_ob=lambda:ob,_worn_note=lambda:None,_start_move=free)
        with patch.object(D,'standby_wake'),patch.object(PL.TB,'under',return_value=('PLACED',ob,0)), \
                patch.object(PL.TB,'_call',side_effect=lambda *a,**k:PL.AC9_OT_SimPlace.invoke(place,self.ctx,event)):
            self.assertEqual(PL.TB.AC9_OT_SimPlacePress._drag_(press,self.ctx,event),{'CANCELLED'})
        free.assert_not_called()
        np.testing.assert_array_equal(PV._world_co(ob),before)
        self.assertEqual(store.text(self.ctx).as_string(),source)
        self.assertEqual(SEL.pieces(self.ctx),['A'])

    def test_cached_evaluated_body_and_preset_placement(self):
        props = self.ctx.scene.ac9_cloth_sim
        old_preset = props.preset
        self.addCleanup(setattr, props, 'preset', old_preset)
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=.06, depth=.5)
        body = self.ctx.object
        props.body = body
        body.shape_key_add(name='Basis')
        wide = body.shape_key_add(name='Wide')
        for point in wide.data:
            point.co.x *= 3.5
            point.co.y *= 3.5
        wide.value = 0.
        body.active_shape_key_index = 0
        body.show_only_shape_key = False
        cache = {}
        observations = []

        def check():
            self.ctx.view_layer.update()
            fast = PC.build(self.ctx, self.sp, ['A'], cache=cache)
            slow = PC.build(self.ctx, self.sp, ['A'])
            np.testing.assert_array_equal(fast['X'][0], slow['X'][0])
            self.assertEqual(fast['res']['in_body'], slow['res']['in_body'])
            observations.append(sum(map(len, fast['res']['in_body'].values())))

        check()
        self.sp['placement']['A'] = dict(self.sp['placement']['A'], tilt=18.)
        check()
        wide.value = 1.
        check()
        self.assertEqual(observations[0], 0)
        self.assertGreater(observations[-1], 0)
        props.preset = json.dumps({'spec': {'placement': {'A': dict(self.sp['placement']['A'], theta=-30.)}}})
        check()
        self.sp['placement']['A'] = dict(self.sp['placement']['A'], theta=55.)
        check()
        self.measurements['cached_body'] = dict(conditions=len(observations), body_inside=observations,
                                                all_vertices_exact=True)

    def test_mixed_partner_and_existing_reset_survive_rehit(self):
        g = self.install_mixed_garment()
        sp = self.sp
        b_before = np.array([v.co[:] for v in g.data.vertices])[3:6]
        PC.commit(self.ctx, PC.build(self.ctx, sp, ['C']))
        self.assertEqual(D.reset_pieces(g), ['C'])
        op = self.press()
        self.update(op)
        op.hit = None
        self.assertFalse(self.update(op))
        op.hit = 22
        candidate = self.update(op)
        self.assertEqual(candidate['selected'], {'A'})
        PC.commit(self.ctx, candidate)
        self.assertEqual(D.reset_pieces(g), ['A', 'C'])
        self.assertEqual(CB.contexts(self.ctx, sp)['B']['basis'], CB.RESULT)
        self.assertEqual(store.get(self.ctx)[0]['placement']['B'], sp['placement']['B'])
        np.testing.assert_array_equal([v.co[:] for v in g.data.vertices[3:6]], b_before)

    def test_late_failure_restores_spec_and_mesh(self):
        sp = self.sp
        PC.commit(self.ctx, PC.build(self.ctx, sp, ['A']))
        before = PV._world_co(PV.objects(sp)['A']).copy()
        body = store.text(self.ctx).as_string()
        changed = copy.deepcopy(sp)
        changed['placement']['A']['theta'] = 44.
        candidate = PC.build(self.ctx, changed, ['A'])
        with patch.object(PV, 'build', side_effect=RuntimeError('late rebuild error')):
            with self.assertRaisesRegex(RuntimeError, 'late rebuild error'):
                PC.commit(self.ctx, candidate)
        self.assertEqual(store.text(self.ctx).as_string(), body)
        np.testing.assert_array_equal(PV._world_co(PV.objects(sp)['A']), before)

    def test_adoption_during_drag_refuses_commit_and_regeneration(self):
        g = self.install_mixed_garment()
        op = self.press()
        candidate = self.update(op)
        self.assertEqual(candidate['selected'], {'A'})
        saved = store.text(self.ctx).as_string()
        CB.adopt(self.ctx, g, ['A'])
        with self.assertRaises(ValueError):
            PC.commit(self.ctx, candidate)
        op.hit = 87
        with self.assertRaises(ValueError):
            self.update(op)
        self.assertEqual(CB.contexts(self.ctx, self.sp)['A']['basis'], CB.RESULT)
        self.assertEqual(store.text(self.ctx).as_string(), saved)

    def test_save_displayed_local_output_keeps_coordinates_and_ownership(self):
        g = self.install_mixed_garment()
        g.data.vertices[0].co.z += .025
        g.data.update()
        cls = D.AC9_OT_SimDress
        run = SimpleNamespace(ob_name=g.name, _displayed_output=True, local=['A'],
                              _control_binding=(store.text(self.ctx), g.session_uid, g.data.session_uid))
        run._garment = lambda context: cls._garment(run, context)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'live.blend')
            with patch.object(cls, '_active_instance', run), patch.object(cls, '_running_mode', 'LOCAL'), \
                    patch.object(cls, '_running_t', time.time()):
                self.assertEqual(cls.running(), 'LOCAL')
                bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            restored = CB.garment(bpy.context, self.sp)
            self.assertAlmostEqual(restored.data.vertices[0].co.z * 1000., 25., places=5)
            self.assertEqual(CB.basis(restored), {'A': CB.RESULT, 'B': CB.RESULT, 'C': CB.PLACED})


ac9_cloth_sim.register()
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(PlacementCandidate))
Path(args[1]).write_text(json.dumps({'count': result.testsRun, 'measurements': PlacementCandidate.measurements,
                                    'failures': [tb for _, tb in result.failures + result.errors]},
                                   indent=2), encoding='utf8')
if not result.wasSuccessful():
    raise RuntimeError('Placement candidate contract failure')

