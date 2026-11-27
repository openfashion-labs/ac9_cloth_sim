"""A real committed layout edit updates buttons/holes before deferred 3D returns.

Finite synthetic rectangles isolate saved 2D/3D ownership; no solver or GPU.
"""
import copy
import tempfile
import unittest
from pathlib import Path
import bpy
import numpy as np
from ac9_cloth_sim import button_view as BV, draft_tool as DT, pattern_view as PV, store as ST
from ac9_cloth_sim.core import draft as D, spec as S


class LayoutPointDisplay(unittest.TestCase):
    def setUp(self):
        self.ctx = bpy.context
        self.sp = S.new_spec('PointDisplay')
        for name, x in [('A', 0), ('B', 300)]:
            D.add_polygon(self.sp, [[x, 0], [x+100, 0], [x+100, 100], [x, 100]], name=name)
        self.sp['buttons'] = [dict(id='B1', piece='A', pt=[40, 60],
                                   hole=dict(piece='B', pt=[340, 60]), fastened=True)]
        self.assertEqual(D.problems(self.sp, ['A', 'B']), [])
        ST.new_text(self.ctx, self.sp['name']); ST.write(self.ctx, self.sp, keep=True)
        PV.rebuild(self.ctx, *ST.get(self.ctx), switch_document=True)
        PV.layout_empty(self.ctx, create=True); BV.sync_flat(self.ctx, self.sp)

    def co(self):
        ob = bpy.data.objects[BV.FLAT_OBJ]
        return np.array([tuple(v.co) for v in ob.data.vertices])[:, :2]*1000

    def test_commit_matches_saved_points_with_3d_follow_off_and_on(self):
        for follows in (False, True):
            with self.subTest(follows=follows):
                old = S.loads(ST.text(self.ctx).as_string()); candidate = D.edit_copy(old, ['A'])
                rigid = D.move_piece(candidate, 'A', shift=[25, 0], twins=False)
                self.ctx.scene.ac9_cloth_sim.follow_edits = follows
                note = DT.commit(self.ctx, candidate, ['A'], old_spec=old, rigid=rigid)
                self.assertFalse(note, note)
                saved = S.loads(ST.text(self.ctx).as_string())
                expected = np.array([p for p, style in BV.flat_rows(saved)])
                np.testing.assert_allclose(self.co(), expected, atol=1e-5, rtol=0)
                self.assertEqual(old['buttons'][0]['pt'][0]+25, saved['buttons'][0]['pt'][0])

    def test_save_reopen_keeps_visible_and_saved_coordinates(self):
        old = copy.deepcopy(self.sp); candidate = D.edit_copy(old, ['A'])
        rigid = D.move_piece(candidate, 'A', shift=[25, 0], twins=False)
        self.ctx.scene.ac9_cloth_sim.follow_edits = False
        DT.commit(self.ctx, candidate, ['A'], old_spec=old, rigid=rigid)
        with tempfile.TemporaryDirectory(prefix='ac9_points_') as folder:
            path = str(Path(folder)/'points.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            ST.invalidate()
            saved = ST.get(bpy.context)[0]
            self.assertEqual(saved['buttons'][0]['pt'], [65, 60])
            np.testing.assert_allclose(self.co(), [p for p, style in BV.flat_rows(saved)], atol=1e-5, rtol=0)
            self.assertFalse(Path(path+'1').exists())


if __name__ == '__main__':
    unittest.main()
