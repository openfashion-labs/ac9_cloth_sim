"""Making a twin the mirror image again keeps the twin's own edge ids. A BOM numbers the two halves' lines apart, and
rebuilding the BOM seams looks the lines up by those numbers: before the fix the twin took the other half's ids and
rebuilding a garment's seams stopped on a KeyError (a jacket front: 1 line of 13 renumbered, a back: 2 of 14)."""
import sys
import unittest

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D
from ac9_cloth_sim.core import spec as S


def pair():
    q = [[0, 0], [300, 0], [320, 150], [300, 280], [0, 280]]
    spec = {'version': 1, 'name': 't', 'seams': [],
            'pieces': [{'name': 'a', 'edges': [{'id': f'L{i + 1}', 'pts': [q[i], q[(i + 1) % 5]]} for i in range(5)]}]}
    tw = D.mirror_copy(spec, 'a', plane='x')
    pb = D.piece(spec, tw)
    for e in pb['edges']:                       # the BOM's numbering of the other half: one line apart
        e['id'] = 'L99' if e['id'] == 'L3' else e['id']
    spec['seams'].append({'id': 'S1', 'source': 'user', 'enabled': True,
                          'a': {'piece': tw, 'from': ['L99', 0.2], 'to': ['L99', 0.8], 'dir': 1},
                          'b': {'piece': 'a', 'from': ['L1', 0.2], 'to': ['L1', 0.8], 'dir': 1}})
    return spec, tw


class MatchTwinKeepsIds(unittest.TestCase):
    def test_ids_and_seam_ends_stay(self):
        spec, tw = pair()
        ids0 = [e['id'] for e in D.piece(spec, tw)['edges']]
        pa = D.piece(spec, 'a')
        pa['edges'][2]['pts'][0] = [330, 150]   # edited apart: the corner between L2 and L3 moved
        pa['edges'][1]['pts'][-1] = [330, 150]
        D.match_twin(spec, 'a')
        pb = D.piece(spec, tw)
        self.assertEqual([e['id'] for e in pb['edges']], ids0)
        sm = spec['seams'][0]
        self.assertEqual(sm['a']['from'][0], 'L99')
        self.assertEqual(sm['a']['to'][0], 'L99')
        self.assertEqual(S.check(spec), [])
        ol = S.Outline(pb)
        self.assertGreater(S.side_length(spec, sm['a'], {tw: ol, 'a': S.Outline(pa)}), 1.0)

    def test_shape_is_mirror_image(self):
        spec, tw = pair()
        pa = D.piece(spec, 'a')
        pa['edges'][2]['pts'][0] = [330, 150]
        pa['edges'][1]['pts'][-1] = [330, 150]
        D.match_twin(spec, 'a')
        _, cmap, _ = D.twin_corner_map(spec, 'a')
        self.assertIsNotNone(cmap)


if __name__ == '__main__':
    unittest.main()
