# SPDX-License-Identifier: GPL-3.0-or-later
"""A BOM whose line numbers two pieces share (a project put together from several files) cannot tell which piece a
seam sews; the garment's 3D drape can (core/bom_drape.py). Synthetic: pieces A and C carry the same shape number and
line numbers, a seam sews line 2 to B's line 8; on the drape B lies against A, not C. Piece D is not in the FBX.

Run: python tests/test_bom_drape.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import bom_drape as BD, bom_to_spec as B2S, spec as S   # noqa: E402

# name, instance GID, first line GID, width, height, layout x
PIECES = [('A', 0, 1, 100, 200, 0), ('B', 1, 5, 100, 200, 300), ('C', 0, 1, 120, 200, 600), ('D', 2, 9, 80, 80, 900)]


def _pattern(name, inst, l0, w, h, x):
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    P = ''.join(f'<Point X="{px}" Y="{py}"/>' for px, py in pts)
    L = ''.join(f'<Line GID="{l0 + k}" PatternGID="{inst}" Type="0"><PointIndex Index="{k}"/>'
                f'<PointIndex Index="{(k + 1) % 4}"/></Line>' for k in range(4))
    return (f'<Pattern><Instance GID="{inst}" PatternName="{name}" R0="1" R1="0" R2="0" R3="1" X="{x}" Y="0">'
            f'<Arrangement Name="Arrangement Point"/></Instance><BoundaryShape><PointList>{P}</PointList>{L}'
            f'</BoundaryShape></Pattern>')


def _seam(gid, a, b):
    def side(ref, line, rev):
        return (f'<SeamLine Reverse="{rev}" RefPatternGID="{ref}"><SeamStartPoint RefLineGID="{line}" Ratio="0"/>'
                f'<SeamEndPoint RefLineGID="{line}" Ratio="1"/></SeamLine>')
    return (f'<SeamLinePairGroup><SeamLinePair GID="{gid}" FoldAngle="0">{side(*a, "false")}{side(*b, "true")}'
            f'</SeamLinePair></SeamLinePairGroup>')


def write_bom(folder):
    xml = ('<?xml version="1.0" encoding="UTF-8"?><Pacx Version="3">'
           + ''.join(_pattern(*p) for p in PIECES)
           + _seam(100, (0, 2), (1, 8))            # line 2 of shape 0: A or C (same numbers), to B's left edge
           + _seam(101, (2, 9), (2, 11))           # D to itself, not in the FBX
           + '</Pacx>')
    path = os.path.join(folder, 'synthetic.xml')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(xml)
    return path


def grid(w, h, n=4):
    xs, ys = np.linspace(0, w, n + 1), np.linspace(0, h, n + 1)
    V = np.array([(x, y) for y in ys for x in xs])
    T = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            T += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    return V, np.array(T)


def drape_mesh(scale=1000.0, shift=(-50.0, 20.0)):
    """A, B, C as one mesh: UV = (layout - shift) / scale; 3D (m): A at x 0..100 mm, B right against it (its left
    edge on A's right edge), C 1 m away."""
    co, tri, uv = [], [], []
    place3d = {'A': (0, 0, 0), 'B': (100, 0, 0), 'C': (0, 0, 1000)}
    for name, inst, l0, w, h, x in PIECES:
        if name not in place3d:
            continue
        V, T = grid(w, h)
        off = len(co)
        dx, dy, dz = place3d[name]
        co += [((vx + dx) / 1000, (vy + dy) / 1000, dz / 1000) for vx, vy in V]
        for t in T:
            tri.append([off + k for k in t])
            uv.append([((V[k][0] + x - shift[0]) / scale, (V[k][1] - shift[1]) / scale) for k in t])
    return {'co': np.array(co, float), 'tri': np.array(tri), 'uv': np.array(uv, float)}


class BomDrape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.xml = write_bom(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_partner_from_the_drape(self):
        fresh, _ = B2S.convert(self.xml)
        drape, rep = BD.resolve(fresh, [drape_mesh()], 'drape.fbx')
        self.assertIsNotNone(drape, rep)
        self.assertEqual(drape['on'], [0, 1, 2])                    # D is not in the FBX
        self.assertEqual(drape['pairs']['100'], [[0, 1]])           # A to B, not C
        self.assertNotIn('101', drape['pairs'])                     # D's seam: no answer, not "not found"
        self.assertAlmostEqual(drape['mm_per_uv'], 1000.0, places=1)
        spec, _ = B2S.convert(self.xml, drape=drape)
        B2S.rebuild_seams(spec, None, self.xml)
        sm = [s for s in spec['seams'] if s['bom']['gid'] == 100]
        self.assertEqual([(s['a']['piece'], s['b']['piece']) for s in sm], [('A', 'B')])
        self.assertEqual(sm[0]['check'], '')
        self.assertTrue(sm[0]['enabled'])
        g = {p['name']: p['garment'] for p in spec['pieces']}
        self.assertEqual(g['A'], g['B'])
        self.assertNotEqual(g['A'], g['C'])
        self.assertEqual(S.check(spec), [])

    def test_apply_to_an_imported_garment_says_what_it_regrouped(self):
        spec, _ = B2S.convert(self.xml)
        for pc in spec['pieces']:                                    # a person's group: A with C (wrong)
            if pc['name'] in ('A', 'C'):
                pc['garment'] = 'G9'
        spec['garments'].append({'id': 'G9', 'name': 'mine', 'source': 'user', 'check': ''})
        B2S.rebuild_seams(spec, None, self.xml)
        fresh, _ = B2S.convert(self.xml)
        drape, _ = BD.resolve(fresh, [drape_mesh()], 'drape.fbx')
        out, rep = B2S.apply_drape(spec, drape)
        self.assertTrue(any('G9' in r and 'mine' in r for r in rep), rep)
        g = {p['name']: p['garment'] for p in out['pieces']}
        self.assertEqual(g['A'], g['B'])
        self.assertEqual([(s['a']['piece'], s['b']['piece']) for s in out['seams'] if s['bom']['gid'] == 100],
                         [('A', 'B')])
        self.assertNotIn('drape', spec['source'])                    # the input is not changed
        same, rep2 = B2S.apply_drape(out, drape)                     # nothing hand-made left: no line
        self.assertFalse(any('regrouped' in r for r in rep2), rep2)

    def test_without_the_drape_the_bom_is_unsure(self):
        spec, _ = B2S.convert(self.xml)
        B2S.rebuild_seams(spec, None, self.xml)
        sm = [s for s in spec['seams'] if s['bom']['gid'] == 100]
        self.assertTrue(sm and all(s['check'] for s in sm))          # flagged: the line is shared

    def test_an_fbx_of_another_pattern(self):
        fresh, _ = B2S.convert(self.xml)
        m = drape_mesh()
        m['uv'] = m['uv'] * np.array([1.0, 3.0])                     # the boxes no longer fit any piece
        drape, rep = BD.resolve(fresh, [m], 'other.fbx')
        self.assertIsNone(drape)
        self.assertIn('does not hold this pattern', rep[0])


if __name__ == '__main__':
    unittest.main()
