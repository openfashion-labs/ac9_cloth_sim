# SPDX-License-Identifier: GPL-3.0-or-later
"""A piece's Ends (placement 'ends'): open, closed, or closed with its left / right end (along the pattern's width)
on top by an overlap -- from a number typed, the self seam or a self button, cut to two layers. Its names come from
one table (closure.ENDS_NAMES) and the field, the translation and the docs agree with it; the old names (Ring Size,
Front Over) are gone. A file before Ends is read as it was laid then.

Run: blender -b --factory-startup --python tests/test_ends.py -- <parent of ac9_cloth_sim> <result.json>
"""
import copy
import io
import json
import sys
import types
import unittest
from pathlib import Path

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, args[0])
import numpy as np
import ac9_cloth_sim
from ac9_cloth_sim import closure as C, dress as D, i18n_ja as JA, properties as P
from ac9_cloth_sim.core import spec as S

ROOT = Path(ac9_cloth_sim.__file__).resolve().parent
W = 240.0


class Ends(unittest.TestCase):
    def setUp(self):
        self.V = np.array([[x, y] for y in range(0, 51, 5) for x in range(0, 241, 5)], float)
        lookup = {tuple(v): i for i, v in enumerate(self.V)}
        E = np.array([[lookup[(x, 25)], lookup[(x + 210, 25)]] for x in (10, 15, 20)])
        self.pat = dict(names=np.array(['band']), V0=self.V, nb=np.array([len(self.V)]),
                        stitch=np.c_[np.zeros(3, int), E[:, 0], np.zeros(3, int), E[:, 1]], h=10.)
        self.spec = dict(name='EndsFixture', units='mm', pieces=[dict(name='band', internals=[
            dict(id='a', kind='internal', pts=[[10, 25], [22, 25]]),
            dict(id='b', kind='internal', pts=[[220, 25], [232, 25]])])],
            seams=[dict(id='close', a=dict(piece='band', line='a', **{'from': 0., 'to': 1.}),
                        b=dict(piece='band', line='b', **{'from': 0., 'to': 1.}))],
            placement={'band': dict(guide='arm_L', theta=35., h=0., anchor=[120., 25.])},
            guides={'arm_L': dict(o=[0., 0., 1000.], R=np.eye(3).tolist(), r=36., h0=-150., h1=150.)})
        self.spec['pieces'][0]['edges'] = [dict(id=str(i), pts=[a, b]) for i, (a, b) in enumerate(
            (([0, 0], [240, 0]), ([240, 0], [240, 50]), ([240, 50], [0, 50]), ([0, 50], [0, 0])))]

    def lay(self, sp):
        X, rings, *_ = D.initial_shape(sp, self.pat, None, None)
        return X[0], rings

    def radial(self, X):
        return np.linalg.norm(X[:, :2], axis=1)

    def test_left_right_from_the_seam(self):
        mid = (self.V[:, 0] > 40) & (self.V[:, 0] < 200)
        for ends, top in (('left', 'a'), ('right', 'b')):
            sp = copy.deepcopy(self.spec)
            sp['placement']['band']['ends'] = ends
            X, rings = self.lay(sp)
            self.assertEqual(rings, ['band'])
            self.assertEqual(D.CLOSURE_LAST['band'][0], top)
            self.assertEqual(D.ENDS_LAST['band'], ends)
            self.assertAlmostEqual(D.LAP_LAST['band'], 30.0, places=6)     # lines at x 16 and 226
            r0 = float(np.median(self.radial(X)[mid]))
            np.testing.assert_allclose(r0, 210.0 / (2 * np.pi), atol=1e-6)
            left = self.V[:, 0] <= 5
            right = self.V[:, 0] >= 235
            out, inn = (left, right) if ends == 'left' else (right, left)
            self.assertTrue(np.all(self.radial(X)[out] > r0 + 1.0))        # the end on top lies outside the ring
            self.assertTrue(np.all(self.radial(X)[inn] < r0 - 1.0))

    def test_overlap_typed_button_and_clamp(self):
        cases = [(dict(overlap=40.0), [], 40.0), (dict(overlap=500.0), [], W - (W - (0.5 * W - 1.0))),
                 (dict(), [dict(piece='band', pt=[20., 25.], hole=dict(piece='band', pt=[225., 25.]))], 35.0),
                 (dict(), [dict(piece='band', pt=[100., 25.], hole=dict(piece='band', pt=[130., 25.]))],
                  0.5 * W - 1.0),                                   # a button lapping too far: two layers too
                 (dict(), [], 0.0)]
        for extra, buttons, overlap in cases:
            sp = copy.deepcopy(self.spec)
            sp['seams'] = []
            sp['buttons'] = buttons
            sp['placement']['band'].update(ends='left', **extra)
            X, rings = self.lay(sp)
            self.assertEqual(rings, ['band'])
            self.assertAlmostEqual(D.LAP_LAST['band'], overlap, places=6, msg=str(extra))
        sp = copy.deepcopy(self.spec)                                       # a number typed wins over the seam
        sp['placement']['band'].update(ends='right', overlap=12.0)
        self.lay(sp)
        self.assertAlmostEqual(D.LAP_LAST['band'], 12.0, places=6)

    def test_end_sewn_onto_a_line(self):
        # one end's outline side sewn onto a line across the other end (a waistband over its button stand): the
        # overlap is from the end to the line, and left / right lay differently
        sp = copy.deepcopy(self.spec)
        sp['pieces'][0]['internals'].append(dict(id='stand', kind='internal', pts=[[200, 0], [200, 50]]))
        sp['seams'] = [dict(id='lap', a=dict(piece='band', dir=1, **{'from': ['3', 0.0], 'to': ['3', 1.0]}),
                            b=dict(piece='band', line='stand', **{'from': 0., 'to': 1.}))]
        shapes = {}
        for ends in ('left', 'right'):
            sp['placement']['band']['ends'] = ends
            X, rings = self.lay(sp)
            self.assertEqual(rings, ['band'])
            self.assertAlmostEqual(D.LAP_LAST['band'], 40.0, places=6)
            shapes[ends] = X
        self.assertGreater(float(np.abs(shapes['left'] - shapes['right']).max()), 3.0)
        # a short stretch of the long side mid-way sewn onto a line (a tuck), not an end: no overlap from it
        sp['pieces'][0]['internals'].append(dict(id='tuck', kind='internal', pts=[[100, 0], [100, 50]]))
        tuck = copy.deepcopy(sp)
        tuck['seams'][0]['a'] = dict(piece='band', dir=1, **{'from': ['2', 0.45], 'to': ['2', 0.55]})
        tuck['seams'][0]['b'] = dict(piece='band', line='tuck', **{'from': 0., 'to': 1.})
        self.lay(tuck)
        self.assertAlmostEqual(D.LAP_LAST['band'], 0.0, places=6)
        sp['seams'][0]['b'] = dict(piece='band', dir=1, **{'from': ['1', 0.0], 'to': ['1', 1.0]})   # end to end: meet
        self.lay(sp)
        self.assertAlmostEqual(D.LAP_LAST['band'], 0.0, places=6)

    def test_open_and_closed(self):
        sp = copy.deepcopy(self.spec)
        sp['placement']['band'].update(ends='open', ring=1.3)                # open: no ring, whatever is left
        self.assertEqual(self.lay(sp)[1], [])
        self.assertEqual(D.ENDS_LAST['band'], 'open')
        sp['placement']['band'].update(ends='closed')
        sp['placement']['band'].pop('ring')
        X, rings = self.lay(sp)
        self.assertEqual(rings, ['band'])
        self.assertEqual(D.ENDS_LAST['band'], 'closed')
        self.assertNotIn('band', D.CLOSURE_LAST)                            # the ends meet, the seam lines ignored
        sp['placement']['band']['ring'] = 1.2
        X, _ = self.lay(sp)
        np.testing.assert_allclose(self.radial(X), (W + D.RING_GAP) / (2 * np.pi) * 1.2, atol=1e-8)

    def test_file_before_ends_read_as_laid(self):
        sp = copy.deepcopy(self.spec)
        for over, ends in (('a', 'left'), ('b', 'right')):
            sp['seams'][0]['over'] = over
            X0, _ = self.lay(sp)
            self.assertEqual(D.ENDS_LAST['band'], ends)
            s2 = copy.deepcopy(sp)
            s2['seams'][0].pop('over')
            s2['placement']['band']['ends'] = ends                          # the same, set as Ends
            X1, _ = self.lay(s2)
            np.testing.assert_allclose(X1, X0, atol=1e-9, rtol=0)
        sp['seams'] = []
        self.lay(sp)
        self.assertEqual(D.ENDS_LAST['band'], 'open')

    def test_mirror_swaps_left_right(self):
        ol = types.SimpleNamespace(plane='x', layout='x', B=np.array([[0., 0.], [W, 50.]]))
        pa = dict(self.spec['placement']['band'], ends='left')
        self.assertEqual(S.mirrored_placement(pa, ol, ol)['ends'], 'right')
        self.assertEqual(S.mirrored_placement(dict(pa, ends='closed'), ol, ol)['ends'], 'closed')
        oly = types.SimpleNamespace(plane='y', layout='y', B=ol.B)                      # drawn upside down: kept
        self.assertEqual(S.mirrored_placement(pa, oly, oly)['ends'], 'left')

    def test_names_from_one_table(self):
        rna = type(bpy.context.scene.ac9_cloth_sim).bl_rna.properties['piece_ends']
        self.assertEqual([(e.identifier, e.name) for e in rna.enum_items],
                         [(k, C.ENDS_NAMES[k]) for k in C.ENDS])
        for k in C.ENDS:
            self.assertIn(C.ENDS_NAMES[k], JA.JA, k)
            self.assertIn(C.ENDS_TIPS[k], JA.JA, k)
        for label in ("Ends", "Overlap", "Stretch (x)"):
            self.assertIn(label, JA.JA)
        tab = (ROOT / 'tool_tab.py').read_text(encoding='utf-8')
        for label in ('"Ends"', '"Overlap"', '"Stretch (x)"'):
            self.assertIn(label, tab)
        old = ("Ring Size", "Front Over", "輪の大きさ", "上前")
        files = [p for p in ROOT.rglob('*') if p.suffix in ('.py', '.md') and 'tests' not in p.parts and p.name != 'CHANGELOG.md']
        for p in files:
            text = p.read_text(encoding='utf-8', errors='replace')
            for o in old:
                # code comments about a file before Ends may still name them; the screen, the translation and the
                # manuals may not
                if p.suffix == '.md' or p.name == 'i18n_ja.py':
                    self.assertNotIn(o, text, f"{p.relative_to(ROOT)}: {o}")
        for name in ('ac9_seam_over',):
            self.assertFalse(hasattr(bpy.types.Scene, name))
        self.assertNotIn('closure_over', type(bpy.context.scene.ac9_cloth_sim).bl_rna.properties.keys())


if __name__ == '__main__':
    import bpy
    ac9_cloth_sim.register()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Ends))
    Path(args[1]).write_text(json.dumps(dict(passed=result.wasSuccessful(), tests=result.testsRun,
                                             log=log.getvalue()), indent=2), encoding='utf8')
    if not result.wasSuccessful():
        raise RuntimeError(log.getvalue())
