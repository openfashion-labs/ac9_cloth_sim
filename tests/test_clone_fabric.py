"""Clone as Pattern keeps the documented source fabric, including twin inheritance.

Finite nondegenerate synthetic rectangles isolate fabric ownership, no physics.
"""
import unittest
from ac9_cloth_sim.core import spec as S, draft as D, iline as IL, fabric as F


class CloneFabric(unittest.TestCase):
    def fixture(self):
        sp = S.new_spec('CloneFabric')
        D.add_polygon(sp, [[0, 0], [200, 0], [200, 200], [0, 200]], name='A')
        line = IL.add_shape(sp, 'A', [[40, 40], [120, 40], [120, 120], [40, 120]], [None]*4)
        fid = F.add_custom(sp, 'Independent Fabric')
        F.fabric_index(sp)[fid]['weight_gsm'] = 777.
        F.fabric_index(sp)[fid]['bending_uNm'] = 321.
        self.assertEqual(D.problems(sp, ['A'])+D.internal_problems(sp, 'A'), [])
        return sp, line, fid

    def test_explicit_source_fabric_is_preserved_with_and_without_sewing(self):
        for sew in (False, True):
            with self.subTest(sew=sew):
                sp, lid, fid = self.fixture(); F.set_pieces(sp, ['A'], fabric_id=fid)
                name = D.clone_shape(sp, 'A', lid, sew=sew)
                self.assertEqual(F.resolved(sp, name), F.resolved(sp, 'A'))
                self.assertEqual(F.resolved(sp, name)['weight_gsm'], 777.)

    def test_default_source_still_follows_the_default(self):
        sp, lid, fid = self.fixture(); name = D.clone_shape(sp, 'A', lid)
        self.assertNotIn('fabric_id', D.piece(sp, name))
        self.assertEqual(F.resolved(sp, name), F.resolved(sp, 'A'))
        F.set_default(sp, fid)
        self.assertEqual(F.resolved(sp, name), F.resolved(sp, 'A'))

    def test_inherited_twin_fabric_is_preserved(self):
        sp, lid, fid = self.fixture(); twin = D.mirror_copy(sp, 'A')
        D.piece(sp, twin)['fabric_id'] = fid
        self.assertEqual(F.resolved(sp, 'A')['id'], fid)
        name = D.clone_shape(sp, 'A', lid)
        self.assertEqual(F.resolved(sp, name)['id'], fid)

    def test_explicit_assignment_to_current_default_remains_explicit(self):
        sp, lid, fid = self.fixture()
        original = sp['default_fabric_id']
        D.piece(sp, 'A')['fabric_id'] = original
        name = D.clone_shape(sp, 'A', lid)
        F.set_default(sp, fid)
        self.assertEqual(F.resolved(sp, name)['id'], original)
        self.assertEqual(F.resolved(sp, 'A')['id'], original)


if __name__ == '__main__':
    unittest.main()
