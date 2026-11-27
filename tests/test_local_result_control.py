# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless Local output adoption, selection, transforms and restoration, without starting a solver."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import ac9_cloth_sim
from ac9_cloth_sim import control_basis as CB, display_state, dress, placement, placed_view as PV, selection, store
from ac9_cloth_sim.core import draft, spec as S
from ac9_cloth_sim import pattern_view as PATTERN


class LocalResultControl(unittest.TestCase):
    measures = {}

    def exercise(self, shared, edited, reopen):
        ctx = bpy.context
        bpy.ops.mesh.primitive_cube_add(size=.1)
        ctx.scene.ac9_cloth_sim.body = ctx.object
        ctx.scene.ac9_cloth_sim.mesh_size = 20.
        sp = S.new_spec('LocalControl')
        draft.add_polygon(sp, [[0, 0], [300, 0], [300, 200], [0, 200]], name='A')
        if shared:
            draft.mirror_copy(sp, 'A')
            other = sp['pieces'][1]['name']
        else:
            other = 'B'
            draft.add_polygon(sp, [[500, 0], [800, 0], [800, 200], [650, 200], [500, 200]], name=other)
            sp['pairs'] = [['A', other]]
        sp['placement'] = {'A': dict(guide='torso', theta=40., h=0., anchor=[150, 100])}
        sp['guides'] = {'torso': dict(o=[0, 0, 0], R=np.eye(3).tolist(), r=100, h0=-200, h1=200, points=[])}
        sp['placement'], _ = S.mirror_twins(sp, sources={'A'})
        store.new_text(ctx, sp['name'])
        store.write(ctx, sp, keep=True)
        from ac9_cloth_sim import pattern_view
        pattern_view.rebuild(ctx,*store.get(ctx),switch_document=True)
        self.assertTrue(PATTERN.integrity(ctx,sp)['ok'])
        res = dress.prepare(ctx, 20., shapes_only=True, placed_only=True)
        PV.build(ctx, sp, res['pat'], res['tris'], res['X'], res['pairs'])
        self.assertEqual(PV.objects(sp)['A'] is PV.objects(sp)[other], shared)
        names = list(map(str, res['pat']['names']))
        n = other if edited == 'other' else 'A'
        untouched = 'A' if n == other else other
        V = [res['pat'][f'V{i}'] for i in range(len(names))]
        offs = np.cumsum([0] + list(map(len, V)))[:-1]
        triangles = np.vstack([t + o for t, o in zip(res['tris'], offs)])
        X = np.vstack(res['X']) * .001
        self.assertTrue(np.isfinite(X).all())
        self.assertGreater(float(np.linalg.norm(np.cross(X[triangles[:, 1]] - X[triangles[:, 0]],
                                                         X[triangles[:, 2]] - X[triangles[:, 0]]), axis=1).min()), 0.)
        leader = PV.objects(sp)['A']
        leader.matrix_world = Matrix.Translation((.011, 0, 0)) @ leader.matrix_world
        leader.data.vertices[0].co.z += .004
        leader.data.update()
        ctx.view_layer.update()
        sp = PV.sync_pose(ctx, sp) or sp
        store.put(ctx, sp)
        initial_pose = S.loads(S.dumps(sp))['placement']
        shown = PV.shown(ctx, sp, store.get(ctx)[1])[0]
        initial = shown[n][:3]
        other_initial = shown[untouched][:3]
        PV.drop(sp)  # the actual Local start archives the moved placement before exposing its initial garment
        expected = res['X'][names.index(n)] * .001
        X[offs[names.index(n)]:offs[names.index(n)] + len(expected), 2] += .025
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            np.savez(dest / 'scene.idx.npz', offs=offs, flat=np.vstack(V), F=triangles)
            op = SimpleNamespace(info={'dir': td, 'names': names}, local=[n], ob_name='UnusedLocalGarment')
            put, notes = dress.AC9_OT_SimDress._to_placed(op, ctx, X)
            self.assertEqual((put, notes), ([n], []))
            self.assertEqual(CB.contexts(ctx, sp)[n]['basis'], CB.RESULT)
            self.assertEqual(display_state.snapshot(sp)[n]['basis'], CB.RESULT)
            self.assertEqual(CB.contexts(ctx, sp)[untouched]['basis'], CB.PLACED)
            self.assertEqual(CB.transform_targets(ctx, [n]), [(n, n, False)])
            self.assertEqual(placement.split_worn(sp, [n, untouched]), ([untouched], [n]))
            obs = PV.objects(sp)
            self.assertIsNot(obs[n], obs[untouched])
            before = PV._world_co(obs[untouched])
            flat = PV.shown(ctx, sp, store.get(ctx)[1])[0][untouched][0]
            np.testing.assert_allclose(before, dress.carry_shape(flat, *other_initial), atol=2e-7)
            pos = PV._world_co(obs[n])
            selection.select_placed(ctx, sp, [n], side=n)
            self.assertEqual(ctx.view_layer.objects.active, obs[n])
            self.assertFalse(obs[untouched].select_get())
            bpy.ops.transform.translate(value=(.01, 0, 0))
            ctx.view_layer.update()
            np.testing.assert_allclose(PV._world_co(obs[n]), pos + [.01, 0, 0], atol=2e-7)
            np.testing.assert_allclose(PV._world_co(obs[untouched]), before, atol=2e-7)
            delta = (PV._world_co(obs[n]) - pos) * 1000.
            other_delta = (PV._world_co(obs[untouched]) - before) * 1000.
            synced = PV.sync_pose(ctx, sp) or sp
            self.assertEqual(synced['placement'][n], initial_pose[n])
            PV.drop(sp)
            PV.build(ctx, sp, res['pat'], res['tris'], res['X'], res['pairs'])
            obs = PV.objects(sp)
            self.assertIsNot(obs[n], obs[untouched])
            np.testing.assert_allclose(PV._world_co(obs[n]), pos + [.01, 0, 0], atol=2e-7)
            if reopen:
                path = str(dest / 'result.blend')
                bpy.ops.wm.save_as_mainfile(filepath=path)
                bpy.ops.wm.open_mainfile(filepath=path)
                ctx = bpy.context
                sp = store.get(ctx)[0]
            self.assertEqual(CB.contexts(ctx, sp)[n]['basis'], CB.RESULT)
            selection.apply(ctx, [n], active=n, view='world')
            self.assertEqual(bpy.ops.ac9_cloth_sim.reset('EXEC_DEFAULT'), {'FINISHED'})
            obs = PV.objects(sp)
            self.assertEqual(CB.contexts(ctx, sp)[n]['basis'], CB.PLACED)
            self.assertEqual(placement.split_worn(sp, [n]), ([n], []))
            self.assertEqual(CB.transform_targets(ctx, [n]), [(n, n, False), (untouched, n, True)])
            ctx.view_layer.update()
            self.assertTrue(obs[n] == obs[untouched] or obs[n].get(PV.LEAD_PROP) or obs[n].get(PV.FOLLOWER_PROP))
            restored = PV.shown(ctx, sp, store.get(ctx)[1])[0][n][:3]
            reference = dress.carry_shape(restored[0], initial[0], initial[1], initial[2])
            error = np.linalg.norm(restored[2] - reference, axis=1) * 1000.
            self.measures[self.id()] = {'vertices': len(delta), 'mean_x_mm': float(delta[:, 0].mean()),
                                        'other_max_mm': float(np.linalg.norm(other_delta, axis=1).max()),
                                        'restored_max_mm': float(error.max())}
            self.assertLess(error.max(), .001)

    def tearDown(self):
        sp = store.get(bpy.context)[0]
        if sp:
            PV.remove(list(PV.objects(sp)), sp)
        t = store.text(bpy.context)
        if t:
            for ob in list(PATTERN.piece_objects().values()):
                if ob.get('ac9_pattern_spec') == t:bpy.data.objects.remove(ob,do_unlink=True)
            bpy.data.texts.remove(t)
        store.invalidate()

    def test_shared_mirror_secondary_side_and_reload(self):
        self.exercise(True, 'other', True)

    def test_separate_following_side(self):
        self.exercise(False, 'other', False)

    def test_old_shared_local_result_opens_with_independent_controls(self):
        ctx = bpy.context
        sp = S.new_spec('LegacyLocalControl')
        draft.add_polygon(sp, [[0, 0], [180, 0], [180, 160], [0, 160]], name='A')
        draft.mirror_copy(sp, 'A')
        other = sp['pieces'][1]['name']
        sp['guides'] = {'torso': dict(o=[0, 0, 0], R=np.eye(3).tolist(), r=100, h0=-100, h1=100, points=[])}
        sp['placement'] = {'A': dict(guide='torso', theta=40., h=0., anchor=[90, 80])}
        sp['placement'], _ = S.mirror_twins(sp, sources={'A'})
        bpy.ops.mesh.primitive_cube_add(size=.1)
        ctx.scene.ac9_cloth_sim.body = ctx.object
        store.new_text(ctx, sp['name'])
        store.write(ctx, sp, keep=True)
        from ac9_cloth_sim import pattern_view
        pattern_view.rebuild(ctx,*store.get(ctx),switch_document=True)
        self.assertTrue(PATTERN.integrity(ctx,sp)['ok'])
        res = dress.prepare(ctx, 20., shapes_only=True, placed_only=True)
        PV.build(ctx, sp, res['pat'], res['tris'], res['X'], res['pairs'])
        obs = PV.objects(sp)
        self.assertIs(obs['A'], obs[other])
        obs['A'][PV.SIM_PROP] = True   # legacy Local wrote its solved shape on the shared object's mesh
        shown = PV.shown(ctx, sp, store.get(ctx)[1])[0]
        before = {n: shown[n][2].copy() for n in ('A', other)}
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'legacy.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            ctx = bpy.context
            sp = store.get(ctx)[0]
            obs = PV.objects(sp)
            self.assertIsNot(obs['A'], obs[other])
            for n in before:
                self.assertEqual(CB.contexts(ctx, sp)[n]['basis'], CB.RESULT)
                np.testing.assert_allclose(PV._world_co(obs[n]), before[n], atol=2e-7)


ac9_cloth_sim.register()
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(LocalResultControl))
Path(args[1]).write_text(json.dumps({'count': result.testsRun, 'measurements': LocalResultControl.measures,
                                    'failures': [t for _, t in result.failures + result.errors]},
                                   indent=2), encoding='utf8')
if not result.wasSuccessful():
    raise RuntimeError('Local result control failed')
