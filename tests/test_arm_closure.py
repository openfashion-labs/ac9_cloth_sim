# SPDX-License-Identifier: GPL-3.0-or-later
"""blender -b --factory-startup --python tests/test_arm_closure.py -- <package parent> <result.json>"""
import copy
import io
import json
import sys
import unittest
from pathlib import Path

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import numpy as np
import ac9_cloth_sim
from ac9_cloth_sim import closure as C, dress as D
from ac9_cloth_sim.core import body_axes as BA


class ArmClosure(unittest.TestCase):
    def setUp(self):
        self.V = np.array([[x, y] for y in range(0, 51, 5) for x in range(0, 241, 5)], float)
        self.T = np.array([t for y in range(10) for x in range(48)
                           for t in ((49*y+x, 49*y+x+1, 49*(y+1)+x),
                                     (49*y+x+1, 49*(y+1)+x+1, 49*(y+1)+x))])
        lookup = {tuple(v): i for i, v in enumerate(self.V)}
        self.E = np.array([[lookup[(x, 25)], lookup[(x+210, 25)]] for x in (10, 15, 20)])
        self.pat = dict(names=np.array(['band']), V0=self.V, nb=np.array([len(self.V)]),
                        stitch=np.c_[np.zeros(3, int), self.E[:, 0], np.zeros(3, int), self.E[:, 1]], h=10.)
        self.spec = dict(name='ArmFixture', units='mm', pieces=[dict(name='band', internals=[
            dict(id='a', kind='internal', pts=[[10, 25], [22, 25]]),
            dict(id='b', kind='internal', pts=[[220, 25], [232, 25]])])],
            seams=[dict(id='close', a=dict(piece='band', line='a', **{'from': 0., 'to': 1.}),
                        b=dict(piece='band', line='b', **{'from': 0., 'to': 1.}), over='a')],
            placement={'band': dict(guide='arm_L', theta=35., h=0., anchor=[120., 25.])}, guides={})
        self.spec['pieces'][0]['edges'] = [dict(id=str(i), pts=[a, b]) for i, (a, b) in enumerate(
            (([0, 0], [240, 0]), ([240, 0], [240, 50]), ([240, 50], [0, 50]), ([0, 50], [0, 0])))]

    def test_same_existing_closure_on_every_guide(self):
        for over in ('a', 'b'):
            reference = None
            for guide in ('neck', 'torso', 'arm_L', 'arm_R', 'leg_L'):
                sp = copy.deepcopy(self.spec)
                sp['placement']['band']['guide'] = guide
                sp['guides'][guide] = dict(o=[0., 0., 1000.], R=np.eye(3).tolist(), r=36., h0=-150., h1=150.)
                sp['seams'][0]['over'] = over
                saved = copy.deepcopy(sp)
                X, rings, *_ = D.initial_shape(sp, self.pat, None, None)
                self.assertEqual(sp, saved)
                self.assertEqual(rings, ['band'])
                self.assertEqual(D.CLOSURE_LAST['band'][0], over)
                if reference is None: reference = X[0]
                np.testing.assert_allclose(X[0], reference, atol=1e-9, rtol=0)

    def test_manual_ring_without_role_or_closure(self):
        sp = copy.deepcopy(self.spec); sp['seams'] = []
        sp['guides']['arm_L'] = dict(o=[0., 0., 1000.], R=np.eye(3).tolist(), r=36., h0=-150., h1=150.)
        for k in (.9, 1.2):
            sp['placement']['band']['ring'] = k
            X, rings, *_ = D.initial_shape(sp, self.pat, None, None)
            radial = np.linalg.norm(X[0][:, :2], axis=1)
            expected = (240.+D.RING_GAP)/(2*np.pi)*k
            np.testing.assert_allclose(radial, expected, atol=1e-8, rtol=0)
            self.assertEqual(rings, ['band'])
            self.assertEqual(D.RING_USED['band'], (k, False))
        sp['placement']['band'].pop('ring')
        self.assertEqual(D.initial_shape(sp, self.pat, None, None)[1], [])

    def test_flat_with_no_guides_key(self):
        # a body picked but Detect Guides never run: the spec has no 'guides' at all, the piece is laid flat on a
        # face; placing it raised KeyError 'guides'
        sp = copy.deepcopy(self.spec); sp['seams'] = []; sp.pop('guides')
        sp['placement']['band'] = dict(guide='flat', flat=dict(o=[0., 0., 2.], R=np.eye(3).tolist()), anchor=[120., 25.])
        X, rings, *_ = D.initial_shape(sp, self.pat, None, None)
        self.assertEqual(rings, [])
        self.assertTrue(np.isfinite(X[0]).all())

if __name__ == '__main__':
    ac9_cloth_sim.register()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArmClosure))
    Path(args[1]).write_text(json.dumps(dict(passed=result.wasSuccessful(), tests=result.testsRun,
                          log=log.getvalue()), indent=2), encoding='utf8')
    if not result.wasSuccessful(): raise RuntimeError(log.getvalue())
