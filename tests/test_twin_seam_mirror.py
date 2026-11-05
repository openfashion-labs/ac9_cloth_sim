"""A seam added between pieces of left / right pairs gets its mirror image between their twins, and only then."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D
from ac9_cloth_sim.core import seam_twin as ST
from ac9_cloth_sim.core import spec as S


def rect(name, x, w=200, h=500):
    q = [[x, 0], [x + w, 0], [x + w, h], [x, h]]
    return {'name': name, 'edges': [{'id': f'{name}_e{i}', 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}


def side(piece, edge, t0=0.0, t1=1.0, d=1):
    return {'piece': piece, 'from': [edge, t0], 'to': [edge, t1], 'dir': d}


def garment():
    """front and back, each with its Mirror Copy (front_R, back_R), and a single collar."""
    spec = {'version': 1, 'name': 't', 'pieces': [rect('front', 0), rect('back', 300), rect('collar', 600, 300, 60)],
            'seams': []}
    made, _ = D.mirror_group(spec, ['front', 'back'])
    ST.mirror_seams_group(spec, made)
    return spec


def add(spec, sid, a, b):
    spec['seams'].append({'id': sid, 'a': a, 'b': b, 'source': 'user', 'check': ''})
    return ST.mirror_onto_twins(spec, [sid])


def ends(spec, sid):
    sm = next(x for x in spec['seams'] if x['id'] == sid)
    ol = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    pts = lambda s: [[round(float(v), 3) for v in ol[s['piece']].at(ol[s['piece']].s_of(*s[k]))] for k in ('from', 'to')]
    return (sm['a']['piece'], pts(sm['a'])), (sm['b']['piece'], pts(sm['b']))


class TwinSeamMirror(unittest.TestCase):
    def test_pair_to_pair_is_mirrored_between_the_twins(self):
        spec = garment()
        made, notes = add(spec, 'U1', side('front', 'front_e1'), side('back', 'back_e3'))
        self.assertEqual(made, ['U1m'])
        self.assertEqual(notes, [])
        (pa, ea), (pb, eb) = ends(spec, 'U1m')
        self.assertEqual((pa, pb), ('front_R', 'back_R'))
        # the mirror of front's x = 200 edge and back's x = 300 edge, the same way along
        self.assertEqual(ea, [[1650.0, 0.0], [1650.0, 500.0]])
        self.assertEqual(eb, [[1550.0, 500.0], [1550.0, 0.0]])

    def test_a_dart_on_a_pair_piece_goes_onto_its_twin(self):
        spec = garment()
        made, _ = add(spec, 'U1', side('front', 'front_e0', 0.0, 0.4), side('front', 'front_e0', 1.0, 0.6, -1))
        self.assertEqual(made, ['U1m'])
        (pa, ea), (pb, eb) = ends(spec, 'U1m')
        self.assertEqual((pa, pb), ('front_R', 'front_R'))
        self.assertEqual(ea, [[1850.0, 0.0], [1770.0, 0.0]])
        self.assertEqual(eb, [[1650.0, 0.0], [1730.0, 0.0]])

    def test_not_when_the_other_piece_is_single(self):
        spec = garment()
        self.assertEqual(add(spec, 'U1', side('front', 'front_e2'), side('collar', 'collar_e0')), ([], []))

    def test_not_between_the_two_halves_of_a_pair(self):
        spec = garment()
        er = [e['id'] for e in D.piece(spec, 'front_R')['edges']]
        right = {'piece': 'front_R', 'from': [er[1], 0.0], 'to': [er[1], 1.0], 'dir': 1}
        self.assertEqual(add(spec, 'U1', side('front', 'front_e3'), right), ([], []))

    def test_not_where_the_twins_are_sewn_already(self):
        spec = garment()
        add(spec, 'U1', side('front', 'front_e1'), side('back', 'back_e3'))
        m = next(x for x in spec['seams'] if x['id'] == 'U1m')
        made, notes = add(spec, 'U2', dict(m['a']), dict(m['b']))
        self.assertEqual(made, [])
        self.assertEqual(len(notes), 1)

    def test_not_when_symmetry_is_off(self):
        spec = garment()
        spec['symmetry'] = 'off'
        self.assertEqual(add(spec, 'U1', side('front', 'front_e1'), side('back', 'back_e3')), ([], []))


if __name__ == '__main__':
    unittest.main()
