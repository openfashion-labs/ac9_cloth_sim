"""An edit of a seam whose pieces are edited symmetrically (Symmetric Edit on single pieces, or left / right pairs)
goes to its mirror image too: Flip, On / Off, check, pull, moved ends; Delete removes both. Before: Flip on one
shoulder seam left the other shoulder's as it was."""
import sys
import unittest

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D
from ac9_cloth_sim.core import seam_twin as ST
from ac9_cloth_sim.core import spec as S


def rect(name, x, w=400, h=500, sym=False):
    q = [[x, 0], [x + w, 0], [x + w, h], [x, h]]
    pc = {'name': name, 'edges': [{'id': f'{name}_e{i}', 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}
    if sym:
        pc['mirror_edit'] = True
    return pc


def side(piece, edge, t0, t1, d=1):
    return {'piece': piece, 'from': [edge, t0], 'to': [edge, t1], 'dir': d}


def by_id(spec, sid):
    return next(x for x in spec['seams'] if x['id'] == sid)


def ends(spec, sm):
    ol = {pc['name']: S.Outline(pc) for pc in spec['pieces']}
    return {k: (sm[k]['piece'], [[round(float(v), 2) for v in ol[sm[k]['piece']].at(ol[sm[k]['piece']].s_of(*sm[k][e]))]
                                 for e in ('from', 'to')]) for k in ('a', 'b')}


def shoulders():
    """front and back in Symmetric Edit; a seam on the left of their top edges and its mirror image (as Add Seam)."""
    spec = {'version': 1, 'name': 't', 'pieces': [rect('front', 0, sym=True), rect('back', 500, sym=True)],
            'seams': []}
    # top edge e2 runs from x+400 to x: t 0.7..0.95 is the left part
    spec['seams'].append({'id': 'U1', 'a': side('front', 'front_e2', 0.7, 0.95),
                          'b': side('back', 'back_e2', 0.7, 0.95), 'source': 'user', 'check': ''})
    made = ST.mirror_within(spec, ['U1'])
    assert made == ['U1m'], made
    return spec


def pairs():
    spec = {'version': 1, 'name': 't', 'pieces': [rect('front', 0, 200), rect('back', 300, 200)], 'seams': []}
    made, _ = D.mirror_group(spec, ['front', 'back'])
    ST.mirror_seams_group(spec, made)
    spec['seams'].append({'id': 'U1', 'a': side('front', 'front_e1', 0.0, 1.0),
                          'b': side('back', 'back_e3', 0.0, 1.0), 'source': 'user', 'check': ''})
    assert ST.mirror_onto_twins(spec, ['U1'])[0] == ['U1m']
    return spec


def flip(spec, sid, k):
    sd = by_id(spec, sid)[k]
    sd['from'], sd['to'] = sd['to'], sd['from']
    sd['dir'] = -sd['dir']


class SeamEditMirror(unittest.TestCase):
    def test_partner_found(self):
        for spec in (shoulders(), pairs()):
            self.assertEqual(ST.mirror_partner(spec, 'U1'), 'U1m')
            self.assertEqual(ST.mirror_partner(spec, 'U1m'), 'U1')

    def test_flip_follows(self):
        for make in (shoulders, pairs):
            spec = make()
            before = ends(spec, by_id(spec, 'U1m'))
            mirror = ST.partners(spec, ['U1'])
            flip(spec, 'U1', 'a')
            self.assertEqual(ST.sync_partners(spec, mirror), ['U1m'])
            after = ends(spec, by_id(spec, 'U1m'))
            self.assertEqual(after['a'][1], before['a'][1][::-1])     # its side A runs the other way
            self.assertEqual(after['b'], before['b'])
            # and it is still the mirror image (found again)
            self.assertEqual(ST.mirror_partner(spec, 'U1'), 'U1m')

    def test_toggle_check_path_follow(self):
        spec = shoulders()
        mirror = ST.partners(spec, ['U1'])
        sm = by_id(spec, 'U1')
        sm.update(enabled=False, check='', path={'side': 'left'})
        ST.sync_partners(spec, mirror)
        x = by_id(spec, 'U1m')
        self.assertFalse(x['enabled'])
        self.assertEqual(x['path']['side'], 'right')

    def test_moved_end_follows(self):
        spec = shoulders()
        mirror = ST.partners(spec, ['U1'])
        by_id(spec, 'U1')['a']['to'] = ['front_e2', 0.9]
        ST.sync_partners(spec, mirror)
        e = ends(spec, by_id(spec, 'U1m'))
        self.assertEqual(sorted(p[0] for p in e['a'][1]), [280.0, 360.0])   # mirror of x 120..40 about x 200

    def test_delete_takes_both(self):
        spec = shoulders()
        self.assertEqual(ST.delete_with_partners(spec, ['U1']), {'U1', 'U1m'})

    def test_no_partner_without_symmetric_edit(self):
        spec = shoulders()
        for pc in spec['pieces']:
            pc.pop('mirror_edit')
        self.assertIsNone(ST.mirror_partner(spec, 'U1'))

    def test_notch_pair_lands_on_the_mirror_too(self):
        # Edit Seams N: a notch on each side of the seam, and the same on the mirror image's places (draft.add_notch)
        spec = shoulders()
        D.add_notch_pair(spec, 'U1', 'a', 30.0)
        for name in ('front', 'back'):
            ts = sorted(n['t'] for n in D.piece(spec, name)['notches'])
            self.assertEqual(ts, [0.225, 0.775])
        spec = pairs()
        D.add_notch_pair(spec, 'U1', 'a', 30.0)
        got = {pc['name']: [n['t'] for n in pc.get('notches', [])] for pc in spec['pieces']}
        self.assertEqual(got, {'front': [0.06], 'back': [0.06], 'front_R': [0.94], 'back_R': [0.94]})


if __name__ == '__main__':
    unittest.main()
