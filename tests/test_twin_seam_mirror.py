"""A seam added between pieces of left / right pairs gets its mirror image between their twins, and only then."""
import sys
import unittest

import numpy as np
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


def hexa(name, x0=0.0):
    """A gusset symmetric front to back only (about y = 389), its straight side at x0 + 200."""
    q = [[x0 + 200, 250], [x0 + 200, 528], [x0 + 18, 491], [x0, 389], [x0 + 18, 287]]
    return {'name': name, 'edges': [{'id': f'{name}_e{i}', 'pts': [q[i], q[(i + 1) % 5]]} for i in range(5)]}


class FrontBackCopy(unittest.TestCase):
    """A front / back Mirror Copy of a panel sewn to a left / right pair of gussets (a bag): the copy's seams go to
    the same gussets' other halves, not to their twins (they sewed the copy's right to the left gusset, on the
    places the panel's own seams had)."""

    def bag(self):
        spec = {'version': 1, 'name': 't', 'pieces': [hexa('gusset'), rect('panel', 300, 300, 280)], 'seams': []}
        D.mirror_group(spec, ['gusset'])                                 # gusset_R: its left / right twin
        spec['seams'] = [{'id': 'U1', 'a': side('panel', 'panel_e3'), 'b': side('gusset', 'gusset_e4', 0.0, 1.0, -1)},
                         {'id': 'U2', 'a': side('panel', 'panel_e1'), 'b': side('gusset_R', 'gusset_e4', 0.0, 1.0, -1)}]
        return spec

    def test_seams_go_to_the_same_gussets_other_half(self):
        spec = self.bag()
        made, _ = D.mirror_group(spec, ['panel'], plane='y')
        ids, notes = ST.mirror_seams_group(spec, made)
        self.assertEqual(len(ids), 2, notes)
        new = [sm for sm in spec['seams'] if sm['id'] in ids]
        ol = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
        for sm, gus in zip(new, ('gusset', 'gusset_R')):
            g = sm['b'] if sm['b']['piece'].startswith('gusset') else sm['a']
            self.assertEqual(g['piece'], gus)
            ys = [float(np.asarray(ol[gus].at(ol[gus].s_of(*g[e]))).reshape(2)[1]) for e in ('from', 'to')]
            self.assertGreaterEqual(min(ys), 389.0 - 1e-6)                # the other (upper) half

    @staticmethod
    def lay_flat(spec, names):
        for n in names:
            spec.setdefault('placement', {})[n] = {'guide': S.FLAT, 'flat': {'o': [0, -500, 0], 'R': [[1, 0, 0], [0, 0, 1],
                                                                                                   [0, -1, 0]]},
                                                   'anchor': [0, 0]}

    def test_placed_on_the_body_again_mirrors_about_the_body(self):
        spec = self.bag()
        made, _ = D.mirror_group(spec, ['panel'], plane='y')
        spec.setdefault('guides', {})['torso'] = {'o': [0.0, 42.0, 1000.0]}
        pair = ('panel', made['panel'])
        self.lay_flat(spec, pair)
        S.set_mirror_at(spec, pair, -513.2)
        self.assertAlmostEqual(S.mirror_center(spec, 'y', 'panel'), -513.2)
        spec['placement'][made['panel']] = {'guide': 'torso', 'anchor': [0, 0]}     # one placed on the torso again
        for n in pair:
            self.assertEqual(S.mirror_center(spec, 'y', n), 42.0)        # the torso's center, not the old place

    def test_stored_plane_position_wins(self):
        spec = self.bag()
        made, _ = D.mirror_group(spec, ['panel'], plane='y')
        self.assertEqual(S.mirror_center(spec, 'y', 'panel'), 0.0)       # no body, nothing stored: 0
        S.set_mirror_at(spec, ('panel', made['panel']), -513.2)
        self.assertEqual(S.mirror_center(spec, 'y', 'panel'), 0.0)       # not laid flat: not read
        self.lay_flat(spec, ('panel', made['panel']))
        self.assertAlmostEqual(S.mirror_center(spec, 'y', 'panel'), -513.2)
        self.assertEqual(S.mirror_center(spec, 'x', 'panel'), 0.0)       # another plane: not it
        self.assertEqual(S.mirror_center(spec, 'y', 'gusset'), 0.0)      # another pair: not it
        D.set_pair_plane(spec, 'panel', 'x')
        self.assertIsNone(S.stored_mirror_at(spec, 'panel'))             # a new plane drops the old position


if __name__ == '__main__':
    unittest.main()
