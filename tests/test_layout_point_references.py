"""Layout maps carry button/hole/tack material points with their own pieces.

Synthetic rectangles isolate reference coordinates; no solver or physical claim.
"""
import copy
import unittest
import numpy as np
from ac9_cloth_sim.core import spec as S, draft as D, buttons as B


class LayoutPointReferences(unittest.TestCase):
    def fixture(self):
        sp = S.new_spec('LayoutPoints')
        for name, x in [('A', 0), ('B', 300)]:
            D.add_polygon(sp, [[x, 0], [x+200, 0], [x+200, 200], [x, 200]], name=name)
        sp['buttons'] = [
            dict(id='Cross', piece='A', pt=[40, 60], hole=dict(piece='B', pt=[340, 60]), fastened=True),
            dict(id='Self', piece='A', pt=[40, 120], hole=dict(piece='A', pt=[140, 120]), fastened=False),
            dict(id='Other', piece='B', pt=[380, 160], hole=None, fastened=True),
        ]
        sp['tacks'] = [dict(a='A', pa=[60, 80], b='B', pb=[360, 80]),
                       dict(a='A', pa=[60, 140], b='A', pb=[140, 140])]
        self.assertEqual(D.problems(sp, ['A', 'B']), [])
        self.assertEqual([D.signed_area(S.Outline(p).B) for p in sp['pieces']], [40000, 40000])
        return sp

    def points(self, sp):
        out = []
        for b in sp['buttons']:
            out.append((b['piece'], b['pt']))
            if b.get('hole'):
                out.append((b['hole']['piece'], b['hole']['pt']))
        for t in sp['tacks']:
            out.extend([(t['a'], t['pa']), (t['b'], t['pb'])])
        return out

    def check_map(self, sp, before, maps):
        for (owner, old), (owner_after, new) in zip(self.points(before), self.points(sp)):
            self.assertEqual(owner, owner_after)
            expected = maps[owner]([old])[0] if owner in maps else old
            np.testing.assert_allclose(new, expected, atol=1.1e-4, rtol=0,
                                       err_msg=f'{owner} material point {old}')

    def test_move_piece_carries_only_its_material_points(self):
        sp = self.fixture(); before = copy.deepcopy(sp)
        D.move_piece(sp, 'A', shift=[25, -10], twins=False)
        self.check_map(sp, before, {'A': D.group_map(shift=[25, -10])})
        entries = B.entries(sp, ['A', 'B'])
        np.testing.assert_allclose(entries[0]['pa'], [65, 50])
        np.testing.assert_allclose(entries[0]['pb'], [340, 60])

    def test_scale_piece_carries_both_ends_when_owned(self):
        sp = self.fixture(); before = copy.deepcopy(sp)
        D.scale_piece(sp, 'A', 1.2, .8, center=[100, 100], twins=False)
        self.check_map(sp, before, {'A': D.group_map(k=[1.2, .8], center=[100, 100])})

    def test_whole_group_transforms_each_endpoint_exactly_once(self):
        sp = self.fixture(); before = copy.deepcopy(sp)
        params = dict(deg=30, k=[1.2, .8], shift=[25, -10], center=[200, 100])
        D.transform_group(sp, pieces=['A', 'B'], **params)
        self.check_map(sp, before, {n: D.group_map(**params) for n in ['A', 'B']})

    def test_layout_round_trip_preserves_binding(self):
        sp = self.fixture(); before = copy.deepcopy(sp)
        D.transform_piece(sp, 'A', deg=90, center=[100, 100])
        rotated = S.loads(S.dumps(sp))
        self.check_map(rotated, before, {'A': D.group_map(deg=90, center=[100, 100])})
        D.transform_piece(rotated, 'A', deg=-90, center=[100, 100])
        self.check_map(rotated, before, {})

    def test_preview_has_independent_mutable_point_references(self):
        sp = self.fixture(); before = copy.deepcopy(sp)
        candidate = D.edit_copy(sp, ['A'])
        candidate['buttons'][0]['pt'][0] += 25
        candidate['tacks'][0]['pa'][0] += 25
        self.assertEqual(sp, before, 'a canceled preview changed the saved source')

    def test_unselected_mirror_uses_its_own_map(self):
        for operation in ('rotate', 'scale'):
            with self.subTest(operation=operation):
                sp = self.fixture(); twin = D.mirror_copy(sp, 'A')
                center = D.piece_center(D.piece(sp, twin))
                point = (center + [20, -30]).tolist()
                sp['buttons'].append(dict(id='Twin', piece=twin, pt=point, hole=None))
                before = copy.deepcopy(sp)
                if operation == 'rotate':
                    D.move_piece(sp, 'A', deg=30)
                    maps = {'A': D.group_map(deg=30, center=[100, 100]),
                            twin: D.group_map(deg=-30, center=center)}
                else:
                    D.scale_piece(sp, 'A', 1.2, .8)
                    maps = {'A': D.group_map(k=[1.2, .8], center=[100, 100]),
                            twin: D.group_map(k=[1.2, .8], center=center)}
                self.check_map(sp, before, maps)

    def test_moved_material_point_still_selects_the_same_solver_vertex(self):
        sp = self.fixture()
        flat = np.array([[x, y] for x in range(0, 201, 20) for y in range(0, 201, 20)], float)
        original = int(np.linalg.norm(flat-np.array([40, 60]), axis=1).argmin())
        D.move_piece(sp, 'A', shift=[25, -10], twins=False)
        moved = flat + [25, -10]
        material_point = B.entries(sp, ['A', 'B'])[0]['pa']
        self.assertEqual(int(np.linalg.norm(moved-material_point, axis=1).argmin()), original)


if __name__ == '__main__':
    unittest.main()
