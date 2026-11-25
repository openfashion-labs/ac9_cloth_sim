# SPDX-License-Identifier: GPL-3.0-or-later
"""Authored edge identity and read-only layout conversion for displayed cloth."""
import copy
import unittest
import numpy as np
from ac9_cloth_sim.core.display_layout import LayoutMap


def piece(points):
    return {'name': 'P', 'edges': [{'id': 'E'+str(i), 'pts': [p, points[(i+1) % len(points)]]}
                                  for i, p in enumerate(points)], 'internals': []}


class DisplayLayout(unittest.TestCase):
    def setUp(self):
        self.points = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        self.old = piece(self.points.tolist())

    def test_rigid_directed_identity_including_symmetric_half_turn(self):
        queries = np.array([[0., 0.], [30., 0.], [60., 40.], [100., 100.]])
        for angle in (0., 90., 180., -37.):
            a = np.radians(angle); r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
            for shift in (np.zeros(2), np.array([759.7481, 119.5256])):
                with self.subTest(angle=angle, shift=shift):
                    current = piece((self.points @ r.T+shift).tolist())
                    mapping = LayoutMap(current, self.old)
                    self.assertIsNotNone(mapping.rigid)
                    np.testing.assert_allclose(mapping.points(queries @ r.T+shift), queries, atol=1e-9)

    def test_nonrigid_preserves_edge_fraction_and_rejects_unproven_interior(self):
        new = self.points.copy();new[1:3, 0] = 200.
        mapping = LayoutMap(piece(new.tolist()), self.old)
        self.assertIsNone(mapping.rigid)
        np.testing.assert_allclose(mapping.points([[50., 0.], [200., 50.]]), [[25., 0.], [100., 50.]])
        self.assertIsNone(mapping.points([[50., 50.]]))

    def test_changed_edge_order_or_split_is_not_inferred(self):
        current = copy.deepcopy(self.old);current['edges'][1]['id'] = 'new'
        self.assertIsNone(LayoutMap(current, self.old).points([[50., 0.]]))

    def test_hole_edit_does_not_certify_interior_identity(self):
        old = copy.deepcopy(self.old)
        old['internals'] = [{'id': 'I', 'hole': True, 'pts': [[20., 20.], [40., 20.], [40., 40.], [20., 20.]]}]
        current = copy.deepcopy(old);current['internals'][0]['pts'][1][0] += 5.
        mapping = LayoutMap(current, old)
        self.assertIsNone(mapping.rigid)
        self.assertIsNone(mapping.points([[50., 50.]]))
        np.testing.assert_allclose(mapping.points([[50., 0.]]), [[50., 0.]])

    def test_no_mutation(self):
        before = copy.deepcopy(self.old)
        LayoutMap(self.old, self.old).points([[50., 50.]])
        self.assertEqual(before, self.old)

    def test_topology_is_part_of_barycentric_cache_identity(self):
        from ac9_cloth_sim import placed_view as PV
        f = self.points.copy();w = np.c_[f, [0., 0., 100., 0.]]
        q = np.array([[50., 50.]])
        a = np.array([[0, 1, 2], [0, 2, 3]])
        b = np.array([[0, 1, 3], [1, 2, 3]])
        PV._bary.clear()
        first = PV.to_world((f, a, w, None), q)
        warm = PV.to_world((f, b, w, None), q)
        PV._bary.clear()
        fresh = PV.to_world((f, b, w, None), q)
        self.assertAlmostEqual(float(first[0, 2]), 50.)
        self.assertAlmostEqual(float(fresh[0, 2]), 0.)
        np.testing.assert_array_equal(warm, fresh)


class DisplayLayoutOwners(unittest.TestCase):
    """Actual mesh/UV/Text readers, without a run folder or solver."""
    def setUp(self):
        import bpy
        from ac9_cloth_sim import store as ST, dress as D, blend_record as BR, control_basis as CB, overlay as O
        from ac9_cloth_sim.core import spec as S
        self.bpy, self.ST, self.O, self.S = bpy, ST, O, S
        self.home_name = bpy.context.scene.name
        scene = bpy.data.scenes.new('DisplayLayoutTest')
        self.scene_name = scene.name
        bpy.context.window.scene = scene
        bpy.context.scene.ac9_cloth_sim.sync_pending = False
        self.old = S.new_spec('LayoutFixture')
        self.old['pieces'] = [piece([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])]
        self.old['pieces'][0]['notches'] = [{'id': 'N', 'edge': 'E0', 't': .5}]
        self.old['seams'] = [{'id': 'S', 'a': {'piece': 'P', 'from': ['E0', 0.], 'to': ['E0', 1.], 'dir': 1},
                               'b': {'piece': 'P', 'from': ['E2', 0.], 'to': ['E2', 1.], 'dir': 1}}]
        ST.new_text(bpy.context, self.old['name']);ST.write(bpy.context, self.old, keep=True)
        from ac9_cloth_sim import pattern_view as PV
        PV.rebuild(bpy.context, self.old, ST.get(bpy.context)[1])
        self.assertTrue(PV.integrity(bpy.context, self.old)['ok'])
        f = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        t = np.array([[0, 1, 2], [0, 2, 3]])
        self.g = D.garment_object(bpy.context, D.garment_name(self.old), f, t, np.zeros(2, int),
                                  np.c_[f, np.full(4, 100.)], ['P'], editing_basis={'P': CB.RESULT})
        self.g[BR.SPEC_PROP] = S.dumps(self.old);self.g[BR.KEY_PROP] = BR.mesh_key(self.g)
        O.invalidate()

    def shifted(self, angle=0.):
        a = np.radians(angle);r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        current = copy.deepcopy(self.old)
        for edge in current['pieces'][0]['edges']:
            edge['pts'] = (np.array(edge['pts']) @ r.T+[760., 120.]).tolist()
        self.ST.put(self.bpy.context, current)
        return current, r

    def test_embedded_document_without_job_supports_rotated_result(self):
        from ac9_cloth_sim import dress as D
        for angle in (0., 90., 180.):
            current, r = self.shifted(angle)
            self.O.invalidate()
            points, _ = self.O.garment_seams(self.bpy.context, current)
            self.assertGreater(len(points), 0)
            actual = self.O._material_points('P', np.array([[30., 20.]]) @ r.T+[760., 120.])
            np.testing.assert_allclose(actual, [[30., 20.]], atol=1e-4)
            self.assertIsNone(D.job_of(self.g))

    def test_internal_line_and_notch_share_material_mapping(self):
        current, _ = self.shifted(90.)
        self.O.garment_seams(self.bpy.context, current)
        q = np.array([[750., 130.], [740., 150.]])
        lines = self.O._lines_to_3d(self.bpy.context, current, {'P': self.S.Outline(current['pieces'][0])},
                                      'P', [q], True, {}, {})
        np.testing.assert_allclose(lines[0], [[.01, .01, .1], [.03, .02, .1]], atol=1e-7)
        dots = self.O.notches3d(self.bpy.context, current, {'P': self.S.Outline(current['pieces'][0])},
                                  current['seams'][0], True)
        self.assertEqual(len(dots), 1)
        self.assertAlmostEqual(float(dots[0, 1]), 0., places=6)

    def test_missing_old_document_does_not_guess_shifted_material(self):
        from ac9_cloth_sim import blend_record as BR
        current, _ = self.shifted()
        del self.g[BR.SPEC_PROP]
        self.O.invalidate()
        points, _ = self.O.garment_seams(self.bpy.context, current)
        self.assertEqual(len(points), 0)

    def test_unchanged_legacy_mesh_without_saved_document(self):
        from ac9_cloth_sim import blend_record as BR
        del self.g[BR.SPEC_PROP]
        points, _ = self.O.garment_seams(self.bpy.context, self.old)
        self.assertGreater(len(points), 0)

    def test_reused_seam_id_with_changed_side_does_not_reuse_old_stitches(self):
        from unittest.mock import patch
        current, _ = self.shifted()
        current['seams'][0]['a'].update({'from': ['E1', 0.], 'to': ['E1', 1.]})
        self.ST.put(self.bpy.context, current)
        flat = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        index = (flat, np.array([0]), np.array([[0, 2], [1, 3]]), np.array(['S', 'S']), ['P'], 'job')
        with patch.object(self.O, '_seam_index', return_value=index):
            seg, _ = self.O._garment_segments(None, current, 4, {'P': self.S.Outline(current['pieces'][0])}, self.g)
        rungs = seg[self.O._jobseg['rung']]
        self.assertEqual(set(rungs[:, 0]), {1, 2})

    def test_unsupported_line_does_not_hide_supported_boundary_line(self):
        current = copy.deepcopy(self.old)
        for edge in current['pieces'][0]['edges']:
            for p in edge['pts']:
                p[0] *= 2.
        self.ST.put(self.bpy.context, current)
        self.O.garment_seams(self.bpy.context, current)
        q = [np.array([[50., 0.], [100., 0.]]), np.array([[50., 50.], [100., 50.]])]
        lines = self.O._lines_to_3d(self.bpy.context, current, {'P': self.S.Outline(current['pieces'][0])}, 'P', q, True, {}, {})
        self.assertIsNotNone(lines[0]);self.assertIsNone(lines[1])

    def test_missing_old_document_pending_symmetric_rotation_is_uncertain(self):
        from ac9_cloth_sim import blend_record as BR
        current = copy.deepcopy(self.old)
        for edge in current['pieces'][0]['edges']:
            edge['pts'] = (100.-np.array(edge['pts'])).tolist()
        self.ST.put(self.bpy.context, current)
        self.bpy.context.scene.ac9_cloth_sim.sync_pending = True
        del self.g[BR.SPEC_PROP]
        points, _ = self.O.garment_seams(self.bpy.context, current)
        self.assertEqual(len(points), 0)

    def test_retargeted_stitches_preserve_verified_job_material_frame(self):
        import tempfile, json
        from pathlib import Path
        from ac9_cloth_sim import dress as D, blend_record as BR, overlay as O
        for target in ('Q', 'Missing'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                old = copy.deepcopy(self.old)
                q = copy.deepcopy(old['pieces'][0]);q['name'] = 'Q'
                for e in q['edges']:
                    e['pts'] = (np.asarray(e['pts'])+[200., 0.]).tolist()
                old['pieces'].append(q)
                current = copy.deepcopy(old)
                current['seams'][0]['b']['piece'] = target
                other = copy.deepcopy(old['seams'][0]);other['id'] = 'Unchanged'
                old['seams'].append(other);current['seams'].append(copy.deepcopy(other))
                self.ST.put(self.bpy.context, current)
                from ac9_cloth_sim import pattern_view as PV
                PV.rebuild(self.bpy.context, current, self.ST.get(self.bpy.context)[1])
                self.assertTrue(PV.integrity(self.bpy.context, current)['ok'])
                f = np.array([[0.,0.],[100.,0.],[100.,100.],[0.,100.]])
                flat = np.vstack([f, f+[200.,0.]])
                tri = np.array([[0,1,2],[0,2,3],[4,5,6],[4,6,7]])
                fp = np.array([0,0,1,1])
                self.g = D.garment_object(self.bpy.context, self.g.name, flat, tri, fp,
                                          np.c_[flat, np.full(8,100.)], ['P','Q'])
                self.g.pop(BR.SPEC_PROP, None)
                self.bpy.context.scene.ac9_cloth_sim.sync_pending = True
                path = Path(directory)
                np.savez(path/O.RR.INDEX, flat=flat, F=tri, fp=fp, offs=np.array([0,4]),
                         E_st=np.array([[0,2],[1,3],[0,2],[1,3]]),
                         E_seam=np.array(['S','S','Unchanged','Unchanged']))
                (path/O.RR.JOB).write_text(json.dumps({'names':['P','Q']}), encoding='utf8')
                (path/O.RR.SPEC).write_text(self.S.dumps(old), encoding='utf8')
                outlines = self.S.Outlines(self.S.piece_index(current))
                O.invalidate()
                index = O._seam_index(directory, self.g, 8, current, outlines)
                self.assertEqual(index[-1], 'job')
                self.assertEqual(index[3].tolist(), ['Unchanged','Unchanged'])
                seg, _ = O._garment_segments(directory, current, 8, outlines, self.g)
                self.assertIsNotNone(seg)
                self.assertIn('P', O._jobseg['layouts'])
                self.assertIn('Unchanged', O._jobseg['ids'])
                self.assertTrue(self.bpy.context.scene.ac9_cloth_sim.sync_pending)

                # Actual UV disagreement still rejects the whole job frame.
                with np.load(path/O.RR.INDEX) as z:bad = {k:z[k] for k in z.files}
                bad['flat'] = flat+25.
                np.savez(path/O.RR.INDEX, **bad)
                self.assertNotEqual(O._seam_index(directory,self.g,8,current,outlines)[-1], 'job')

    def test_job_document_precedes_stale_embedded_symmetric_document(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from ac9_cloth_sim import blend_record as BR
        stale = copy.deepcopy(self.old)
        for edge in stale['pieces'][0]['edges']:
            edge['pts'] = (100.-np.array(edge['pts'])).tolist()
        self.g[BR.SPEC_PROP] = self.S.dumps(stale)
        flat = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        with patch.object(self.O.RR, 'open_result', return_value=SimpleNamespace(dressed_spec_text=lambda: self.S.dumps(self.old))):
            maps, saved = self.O._display_layouts('verified', self.g, self.old, flat, {'P': (0, 4)}, 'job')
        np.testing.assert_allclose(maps['P'].points([[50., 0.]]), [[50., 0.]], atol=1e-9)

    def test_unchanged_job_without_saved_document_keeps_identity(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from ac9_cloth_sim import blend_record as BR
        del self.g[BR.SPEC_PROP]
        flat = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        with patch.object(self.O.RR, 'open_result', return_value=SimpleNamespace(dressed_spec_text=lambda: None)):
            maps, _ = self.O._display_layouts('verified', self.g, self.old, flat, {'P': (0, 4)}, 'job')
        np.testing.assert_allclose(maps['P'].points([[50., 0.]]), [[50., 0.]], atol=1e-9)

    def test_missing_job_document_tries_valid_embedded_before_identity(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        current = copy.deepcopy(self.old)
        for edge in current['pieces'][0]['edges']:
            edge['pts'] = (100.-np.array(edge['pts'])).tolist()
        flat = np.array([[0., 0.], [100., 0.], [100., 100.], [0., 100.]])
        with patch.object(self.O.RR, 'open_result', return_value=SimpleNamespace(dressed_spec_text=lambda: None)):
            maps, _ = self.O._display_layouts('verified', self.g, current, flat, {'P': (0, 4)}, 'job')
        np.testing.assert_allclose(maps['P'].points([[50., 100.]]), [[50., 0.]], atol=1e-9)

    def test_invalid_embedded_json_does_not_escape_draw(self):
        from ac9_cloth_sim import blend_record as BR
        self.g[BR.SPEC_PROP] = '[]'
        points, _ = self.O.garment_seams(self.bpy.context, self.old)
        self.assertGreater(len(points), 0)

    def test_nonrigid_corner_crossing_queries_stay_on_directed_edges(self):
        current = copy.deepcopy(self.old)
        for edge in current['pieces'][0]['edges']:
            for p in edge['pts']:p[0] *= 1.731
        current['seams'][0]['a'].update({'from': ['E0', .23], 'to': ['E1', .47]})
        current['seams'][0]['b'].update({'from': ['E1', .13], 'to': ['E2', .71]})
        self.ST.put(self.bpy.context, current)
        points, _ = self.O.garment_seams(self.bpy.context, current)
        self.assertGreater(len(points), 0)

    def test_internal_side_geometry_is_part_of_saved_sewing_identity(self):
        old = copy.deepcopy(self.old)
        old['pieces'][0]['internals'] = [{'id': 'L', 'pts': [[0., 0.], [100., 0.]]}]
        old['seams'][0]['a'] = {'piece': 'P', 'line': 'L', 'from': 0., 'to': 1.}
        current = copy.deepcopy(old)
        current['pieces'][0]['internals'][0]['pts'] = [[0., 100.], [100., 100.]]
        layouts = {'P': LayoutMap(current['pieces'][0], old['pieces'][0])}
        self.assertFalse(self.O._same_sewing(old['seams'][0], current['seams'][0], old, current, layouts))

    def test_saved_reload_keeps_embedded_material_mapping(self):
        import tempfile
        from pathlib import Path
        from ac9_cloth_sim import dress as D
        current, _ = self.shifted(180.)
        before = np.asarray(self.O.garment_seams(self.bpy.context, current)[0]).copy()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'display.blend')
            self.bpy.ops.wm.save_as_mainfile(filepath=path)
            self.bpy.ops.wm.open_mainfile(filepath=path)
            current = self.ST.get(self.bpy.context)[0]
            self.g = D.garment(current)
            after = self.O.garment_seams(self.bpy.context, current)[0]
            np.testing.assert_allclose(before, after, atol=1e-9)

    def test_undo_redo_rebuilds_the_material_mapping(self):
        from ac9_cloth_sim import dress as D
        self.bpy.context.preferences.edit.use_global_undo = True
        self.bpy.ops.ed.undo_push(message='Unedited pattern')
        current, _ = self.shifted(90.)
        self.bpy.ops.ed.undo_push(message='Moved pattern')
        expected = np.asarray(self.O.garment_seams(self.bpy.context, current)[0]).copy()
        for operation in (self.bpy.ops.ed.undo, self.bpy.ops.ed.redo):
            operation()
            current = self.ST.get(self.bpy.context)[0]
            self.g = D.garment(current)
            actual = self.O.garment_seams(self.bpy.context, current)[0]
            np.testing.assert_allclose(expected, actual, atol=1e-9)

    def tearDown(self):
        self.O.invalidate()
        self.bpy.data.objects.remove(self.g, do_unlink=True)
        from ac9_cloth_sim import pattern_view as PV
        coll = self.bpy.data.collections.get(PV.COLL_NAME)
        if coll is not None:
            for ob in list(coll.objects):self.bpy.data.objects.remove(ob, do_unlink=True)
            self.bpy.data.collections.remove(coll)
        scene = self.bpy.data.scenes.get(self.scene_name)
        self.bpy.context.window.scene = self.bpy.data.scenes[self.home_name]
        if scene is not None:self.bpy.data.scenes.remove(scene)
