# SPDX-License-Identifier: GPL-3.0-or-later
"""An end taper on a curved fold line (no Blender).

Why (2026-11-25): a densely drawn curved line (points 1.2 mm apart) is meshed with hinges about 6 mm long, chords of
the curve. The solver's audit measured each hinge's midpoint against the drawn line with a 0.01 mm tolerance; a
chord's midpoint sat up to 0.019 mm off the curve, so the taper was refused and the run stopped, at its start or
when the taper was set while Live ran. The hinges' own vertices are on the line: those are checked now.

Run: python tests/test_terminal_curved_line.py
"""
import hashlib
import importlib.util
import json
import os
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec_ = importlib.util.spec_from_file_location('fold_reference', os.path.join(HERE, '..', 'core', 'fold_reference.py'))
FR = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(FR)
KEY = 'P|L1'


def field(poly, deg=90.0, taper=30.0):
    f = {'schema': 1, 'lines': {KEY: {'polyline_mm': poly.tolist(), 'deg': deg, 'taper_mm': taper, 'terminal_ends': [0]}}}
    f['sha256'] = hashlib.sha256(json.dumps(f, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return f


def arc(n, radius=200.0, sweep=0.6):
    a = np.linspace(0.0, sweep, n)
    return np.c_[radius * np.sin(a), radius * (1 - np.cos(a))]


def dist_to(poly, p):
    a, e = poly[:-1], np.diff(poly, axis=0)
    t = np.clip(((p - a) * e).sum(1) / (e * e).sum(1), 0, 1)
    return float(np.linalg.norm(p - (a + t[:, None] * e), axis=1).min())


class CurvedLine(unittest.TestCase):
    def setup(self, offset=0.0):
        poly = arc(101)                         # 1.2 mm apart along a 200 mm radius
        verts = poly[::5].copy()                # the mesh's vertices on the line, hinges 6 mm long
        verts[:, 1] += offset
        hinges = np.array([[0, 0, i, i + 1] for i in range(len(verts) - 1)])   # opposite vertices unused here
        ids = np.arange(len(hinges))
        rest = np.zeros(len(hinges))
        return field(poly), verts, hinges, rest, {KEY: (ids, 0.0)}, {KEY: 90.0}

    def test_chords_of_a_curve_are_accepted(self):
        f, verts, hinges, rest, groups, degrees = self.setup()
        mid = verts[hinges[:, 2:]].mean(1)
        poly = np.asarray(f['lines'][KEY]['polyline_mm'])
        self.assertGreater(max(dist_to(poly, m) for m in mid), 0.01)      # the case that was refused
        out, audit = FR.apply_terminal_field(f, verts, hinges, rest, groups, degrees)
        self.assertEqual(len(audit['lines'][KEY]['hinges']), len(hinges))
        self.assertTrue(np.isfinite(out).all())

    def test_hinges_off_the_line_are_still_refused(self):
        f, verts, hinges, rest, groups, degrees = self.setup(offset=0.1)
        with self.assertRaises(ValueError):
            FR.apply_terminal_field(f, verts, hinges, rest, groups, degrees)


if __name__ == '__main__':
    unittest.main()
