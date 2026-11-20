# SPDX-License-Identifier: GPL-3.0-or-later
"""Add Dart on a piece too narrow for its length: the dart is cut down until it fits, not refused.

A piece edited symmetrically gets the mirror dart on its other half; on a side edge of a piece narrower than two
darts' length the two used to cross, and the dart was refused with a warning only (from the right-click menu that
looked like nothing happened). Here: a 108 x 143 mm rectangle edited symmetrically, a dart on its right edge with the
default 80 mm length is added, shorter, both darts in; one that fits keeps its length; a plain piece is unchanged.

Run: blender -b --factory-startup --python tests/test_dart_fit.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import numpy as np                                           # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import pattern_view as PV, store as ST   # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as DR, dart as DA   # noqa: E402

RESULTS = {}


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def load(spec):
    ctx = bpy.context
    if ST.text(ctx) is None:
        ST.new_text(ctx, spec['name'])
    ST.text(ctx).clear()
    ST.text(ctx).write(S.dumps(spec))
    ST.invalidate()
    sp, outlines = ST.get(ctx)
    PV.rebuild(ctx, sp, outlines)
    return sp


def now():
    ST.invalidate()
    return ST.get(bpy.context)[0]


def side_edge(spec, name, which):
    pc = DR.piece(spec, name)
    C = DR.corners(pc)
    mids = [((C[k] + C[(k + 1) % len(C)]) / 2, e['id']) for k, e in enumerate(pc['edges'])]
    return (max(mids, key=lambda m: m[0][0]) if which == 'right' else min(mids, key=lambda m: m[0][1]))[1]


class DartFit(unittest.TestCase):
    def make(self, sym, w=108.0, h=143.0):
        spec = S.new_spec('DartFit')
        nm = DR.add_polygon(spec, [[0, 0], [w, 0], [w, h], [0, h]], name='A')
        if sym:
            DR.piece(spec, nm)['mirror_edit'] = True
        return load(spec), nm

    def run_dart(self, name, eid, length=0.08):
        return bpy.ops.ac9_cloth_sim.add_dart('EXEC_DEFAULT', piece=name, edge=eid, t=0.5, length=length)

    def test_symmetric_side_edge_cut_to_fit(self):
        sp, nm = self.make(True)
        n0 = len(DA.find_darts(sp, nm))
        r = self.run_dart(nm, side_edge(sp, nm, 'right'))
        sp2 = now()
        darts = DA.find_darts(sp2, nm)
        RESULTS['sym_side'] = {'ret': list(r), 'darts': len(darts) - n0, 'problems': DR.problems(sp2, [nm])}
        self.assertEqual(r, {'FINISHED'})
        self.assertEqual(len(darts) - n0, 2)               # the dart and its mirror
        self.assertEqual(DR.problems(sp2, [nm]), [])

    def test_fits_keeps_length(self):
        sp, nm = self.make(True)
        r = self.run_dart(nm, side_edge(sp, nm, 'bottom'), length=0.05)
        sp2 = now()
        apex_y = [float(DR.corners(DR.piece(sp2, nm))[d['apex']][1]) for d in DA.find_darts(sp2, nm)]
        RESULTS['fits'] = {'ret': list(r), 'darts': len(apex_y), 'apex_y': apex_y}
        self.assertEqual(r, {'FINISHED'})
        self.assertEqual(len(apex_y), 1)                   # on the center line: one dart
        self.assertAlmostEqual(apex_y[0], 50.0, delta=0.5)  # its length kept (the bottom edge at y = 0)

    def test_plain_piece(self):
        sp, nm = self.make(False)
        r = self.run_dart(nm, side_edge(sp, nm, 'right'))
        sp2 = now()
        RESULTS['plain'] = {'ret': list(r), 'darts': len(DA.find_darts(sp2, nm))}
        self.assertEqual(r, {'FINISHED'})
        self.assertEqual(len(DA.find_darts(sp2, nm)), 1)


if __name__ == '__main__':
    res = unittest.main(argv=['x'], exit=False, verbosity=2).result
    if len(ARGS) > 1:
        RESULTS['ok'] = res.wasSuccessful()
        Path(ARGS[1]).write_text(json.dumps(RESULTS, indent=1, default=str), encoding='utf-8')
    sys.exit(0 if res.wasSuccessful() else 1)
