"""A seam just added gets its left / right mirror image by one rule (seam_twin.lr_images): a piece with a left / right
twin goes onto that twin, a single piece edited symmetrically onto itself. Symmetric Edit says the outline is
symmetric, not that the piece lies on the body's center: before this rule a box's front (a front / back pair) sewn to
its right side panel (a left / right pair, both in Symmetric Edit) got its left edge sewn to the same right panel, and a
side seam between two left / right pairs got a second image sewing center front to center back."""
import sys
import unittest

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D
from ac9_cloth_sim.core import seam_twin as ST
from ac9_cloth_sim.core import spec as S


def rect(name, x, y, w, h, sym=False):
    q = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
    pc = {'name': name, 'edges': [{'id': f'{name}_e{i}', 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}
    if sym:
        pc['mirror_edit'] = True
    return pc


def side(piece, edge, t0=0.0, t1=1.0, d=1):
    return {'piece': piece, 'from': [edge, t0], 'to': [edge, t1], 'dir': d}


def add(spec, a, b, sid='U1', **kw):
    spec['seams'].append(dict({'id': sid, 'a': a, 'b': b, 'source': 'user', 'check': ''}, **kw))
    return ST.mirror_added(spec, [sid])


def ends(spec, sid):
    sm = next(x for x in spec['seams'] if x['id'] == sid)
    ol = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    pts = lambda s: [[round(float(v), 1) for v in ol[s['piece']].at(ol[s['piece']].s_of(*s[k]))] for k in ('from', 'to')]
    return {(sm['a']['piece'], str(pts(sm['a']))), (sm['b']['piece'], str(pts(sm['b'])))}


def box():
    """bottom (single), front with its front / back copy, a side panel with its left / right copy; all in Symmetric
    Edit (the copies inherit it)."""
    spec = {'version': 1, 'name': 't', 'seams': [], 'pieces': [
        rect('bottom', 0, 300, 200, 80, True), rect('front', 0, 400, 200, 200, True),
        rect('side', 250, 300, 200, 80, True)]}
    D.mirror_copy(spec, 'front', plane='y')
    D.mirror_copy(spec, 'side', plane='x')
    return spec


def unpair(spec):
    """The side panels as two pieces drawn separately and linked by Pair, not a Mirror Copy pair."""
    for pc in spec['pieces']:
        if pc['name'] in ('side', 'side_R'):
            pc.pop('family', None)
            pc.pop('mirrored', None)
    spec['pairs'] = [['side', 'side_R']]


FRONT_RIGHT = side('front', 'front_e1')             # x = 200, up
SIDE_TOP = side('side', 'side_e2', 1.0, 0.0, -1)     # y = 380, 250 -> 450


class Box(unittest.TestCase):
    def test_front_left_goes_to_the_other_side_panel(self):
        spec = box()
        made, notes = add(spec, FRONT_RIGHT, SIDE_TOP)
        self.assertEqual((made, notes), (['U1m'], []))
        self.assertEqual(ends(spec, 'U1m'), {('front', '[[0.0, 400.0], [0.0, 600.0]]'),
                                             ('side_R', '[[700.0, 380.0], [500.0, 380.0]]')})

    def test_a_b_order_does_not_matter(self):
        spec = box()
        made, _ = add(spec, SIDE_TOP, FRONT_RIGHT)
        self.assertEqual(made, ['U1m'])
        self.assertEqual({p for p, _ in ends(spec, 'U1m')}, {'front', 'side_R'})

    def test_off_twin_or_symmetry_off_make_no_wrong_image(self):
        def off(spec):
            D.piece(spec, 'side_R')['enabled'] = False

        def sym_off(spec):
            spec['symmetry'] = 'off'

        def pair_off(spec):
            unpair(spec)
            D.piece(spec, 'side_R')['enabled'] = False
        for how in (off, sym_off, pair_off):
            spec = box()
            how(spec)
            made, _ = add(spec, FRONT_RIGHT, SIDE_TOP)
            self.assertEqual(made, [], how.__name__)

    def test_a_pairs_partner_takes_the_image(self):
        """Side panels drawn separately and linked by Pair (as a user's bag was): the image goes onto the partner."""
        spec = box()
        unpair(spec)
        made, _ = add(spec, FRONT_RIGHT, SIDE_TOP)
        self.assertEqual(made, ['U1m'])
        self.assertEqual({p for p, _ in ends(spec, 'U1m')}, {'front', 'side_R'})

    def test_a_front_back_pair_mirrors_onto_itself(self):
        """A Pair set to front / back (mirror_plane 'y' on both): no left / right counterpart, the piece mirrors onto
        itself across its center line."""
        spec = box()
        unpair(spec)
        spec['pairs'] = [['front', 'front_R']]
        made, _ = add(spec, side('front', 'front_e1', 0.0, 0.4), side('bottom', 'bottom_e1'))
        self.assertEqual(made, ['U1m'])
        self.assertEqual(ends(spec, 'U1m'), {('front', '[[0.0, 400.0], [0.0, 480.0]]'),
                                             ('bottom', '[[0.0, 300.0], [0.0, 380.0]]')})

    def test_no_image_on_a_pair_partner_of_another_shape(self):
        spec = box()
        unpair(spec)
        pc = D.piece(spec, 'side_R')                    # the partner made a trapezoid: the mirrored ends miss it
        pc['edges'][1]['pts'][1] = [700.0, 360.0]
        pc['edges'][2]['pts'][0] = [700.0, 360.0]
        made, _ = add(spec, FRONT_RIGHT, SIDE_TOP)
        self.assertEqual(made, [])

    def test_path_side_swaps_whatever_piece_is_side_a(self):
        for a, b in ((FRONT_RIGHT, SIDE_TOP), (SIDE_TOP, FRONT_RIGHT)):
            spec = box()
            add(spec, a, b, path={'side': 'left', 'guide': 'arm_L'})
            m = next(x for x in spec['seams'] if x['id'] == 'U1m')
            self.assertEqual((m['path']['side'], m['path']['guide']), ('right', 'arm_R'))


def shirt(sym=True):
    spec = {'version': 1, 'name': 't', 'seams': [],
            'pieces': [rect('front', 0, 0, 200, 500, sym), rect('back', 300, 0, 200, 500, sym)]}
    made, _ = D.mirror_group(spec, ['front', 'back'])
    ST.mirror_seams_group(spec, made)
    return spec


class Pairs(unittest.TestCase):
    def test_side_seam_between_pairs_gets_one_image(self):
        for sym in (False, True):
            spec = shirt(sym)
            made, _ = add(spec, side('front', 'front_e1'), side('back', 'back_e3', 1.0, 0.0, -1))
            self.assertEqual(made, ['U1m'], sym)
            self.assertEqual({p for p, _ in ends(spec, 'U1m')}, {'front_R', 'back_R'})

    def test_none_where_the_twins_are_sewn_already(self):
        spec = shirt()
        add(spec, side('front', 'front_e1'), side('back', 'back_e3', 1.0, 0.0, -1))
        m = next(x for x in spec['seams'] if x['id'] == 'U1m')
        made, notes = add(spec, dict(m['a']), dict(m['b']), sid='U2')
        self.assertEqual(made, [])
        self.assertEqual(len(notes), 1)

    def test_dart_on_a_symmetric_pair_piece_keeps_its_image_across_the_piece(self):
        for sym, n in ((False, 1), (True, 2)):
            spec = shirt(sym)
            made, _ = add(spec, side('front', 'front_e0', 0.1, 0.2), side('front', 'front_e0', 0.3, 0.2, -1))
            self.assertEqual(len(made), n, sym)

    def test_none_between_a_pairs_two_halves(self):
        spec = shirt()
        er = [e['id'] for e in D.piece(spec, 'front_R')['edges']]
        made, _ = add(spec, side('front', 'front_e3'), {'piece': 'front_R', 'from': [er[1], 0.0], 'to': [er[1], 1.0],
                                                        'dir': 1})
        self.assertEqual(made, [])


class PairToSingle(unittest.TestCase):
    def sleeve(self, sym):
        spec = {'version': 1, 'name': 't', 'seams': [],
                'pieces': [rect('bodice', 0, 0, 300, 400, sym), rect('sleeve', 400, 0, 400, 300)]}
        D.mirror_copy(spec, 'sleeve')
        return spec

    def test_onto_the_twin_and_the_single_pieces_other_half(self):
        spec = self.sleeve(True)
        made, _ = add(spec, side('sleeve', 'sleeve_e2'), side('bodice', 'bodice_e1'))
        self.assertEqual(made, ['U1m'])
        self.assertEqual({p for p, _ in ends(spec, 'U1m')}, {'sleeve_R', 'bodice'})
        self.assertIn(('bodice', '[[0.0, 0.0], [0.0, 400.0]]'), ends(spec, 'U1m'))

    def test_none_when_the_single_piece_is_not_symmetric_edit(self):
        spec = self.sleeve(False)
        self.assertEqual(add(spec, side('sleeve', 'sleeve_e2'), side('bodice', 'bodice_e1'))[0], [])


class Singles(unittest.TestCase):
    def test_none_across_the_center_line(self):
        spec = {'version': 1, 'name': 't', 'seams': [],
                'pieces': [rect('front', 0, 0, 400, 500, True), rect('back', 500, 0, 400, 500, True)]}
        made, _ = add(spec, side('front', 'front_e2'), side('back', 'back_e2', 1.0, 0.0, -1))
        self.assertEqual(made, [])


class EditsFollow(unittest.TestCase):
    def test_partner_both_ways_on_the_box(self):
        spec = box()
        add(spec, FRONT_RIGHT, SIDE_TOP)
        self.assertEqual(ST.mirror_partner(spec, 'U1'), 'U1m')
        self.assertEqual(ST.mirror_partner(spec, 'U1m'), 'U1')
        self.assertEqual(ST.delete_with_partners(spec, ['U1']), {'U1', 'U1m'})

    def test_flip_reaches_the_image(self):
        spec = box()
        add(spec, FRONT_RIGHT, SIDE_TOP)
        pairs = ST.partners(spec, ['U1'])
        sm = next(x for x in spec['seams'] if x['id'] == 'U1')
        sm['b']['from'], sm['b']['to'] = sm['b']['to'], sm['b']['from']
        sm['b']['dir'] = -sm['b']['dir']
        self.assertEqual(ST.sync_partners(spec, pairs), ['U1m'])
        m = next(x for x in spec['seams'] if x['id'] == 'U1m')
        self.assertEqual(ends(spec, 'U1m'), {('front', '[[0.0, 400.0], [0.0, 600.0]]'),
                                             ('side_R', '[[500.0, 380.0], [700.0, 380.0]]')})
        self.assertEqual(m['b']['piece'], 'side_R')


if __name__ == '__main__':
    unittest.main()
