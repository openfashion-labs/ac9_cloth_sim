# SPDX-License-Identifier: GPL-3.0-or-later
"""AC9 Piece draws its placement rows by the piece's Ends: Ends always on a placed piece; Overlap (+ Auto) only when
one end is over the other; Stretch (+ Auto) only when closed. The whole draw runs through, and is timed.

Run: blender -b --factory-startup --python tests/test_ends_panel.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import sys
import time
import types
import unittest
from unittest.mock import patch
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import (selection as SEL, toolbar as TB, pattern_view as PV, placed_view as PLV,  # noqa: E402
                           store as ST, tool_tab as TT)
from ac9_cloth_sim.core import spec as S, draft as DR  # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class _L:
    """A layout that records the properties drawn."""
    def __init__(self, out):
        self.__dict__['out'] = out

    def prop(self, _data, prop, **_k):
        self.out.append(prop)

    def operator(self, *_a, **_k):
        return types.SimpleNamespace()

    def panel(self, *_a, **_k):
        return _L(self.out), _L(self.out)

    def __getattr__(self, _n):
        return lambda *_a, **_k: _L(self.out)


class EndsPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('EndsPanel')
        DR.add_polygon(spec, [[0, 0], [240, 0], [240, 50], [0, 50]], name='A')
        spec['placement'] = {'A': dict(guide='torso', theta=0., h=0., anchor=[120., 25.])}
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        me = bpy.data.meshes.new('EP_placed_A')
        me.from_pydata([(0, 0, 0), (.03, 0, 0), (0, 0, .03)], [], [(0, 1, 2)])
        ob = bpy.data.objects.new('EP_placed_A', me)
        PLV._collection(ctx).objects.link(ob)
        ob[PLV.PROP] = 'A'
        ob[PLV.GARMENT_PROP] = spec['name']
        ob[PLV.ORG_PROP] = [0, 0, 0]
        cls.ob = ob

    def drawn(self, ends):
        ctx = bpy.context
        spec, _ = ST.get(ctx)
        p = spec['placement']['A']
        p.pop('ends', None)
        if ends:
            p['ends'] = ends
        ST.text(ctx).clear()
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        SEL.deselect_all(ctx)
        self.assertTrue(TB.select_object(ctx, self.ob))
        ctx.view_layer.update()
        self.assertEqual(TT.resolve(ctx, ST.get(ctx)[0]), 'PIECE')
        out = []
        t = time.perf_counter()
        TT.AC9_PT_ToolPiece.draw(types.SimpleNamespace(layout=_L(out)), ctx)
        return out, time.perf_counter() - t

    def test_rows_by_ends(self):
        want = {None: {'piece_ends'}, 'open': {'piece_ends'}, 'closed': {'piece_ends', 'piece_ring'},
                'left': {'piece_ends', 'piece_overlap', 'piece_overlap_auto', 'piece_ring'},
                'right': {'piece_ends', 'piece_overlap', 'piece_overlap_auto', 'piece_ring'}}
        rows = ('piece_ends', 'piece_overlap', 'piece_overlap_auto', 'piece_ring')
        self.times = []
        for ends, w in want.items():
            out, dt = self.drawn(ends)
            self.times.append(dt)
            self.assertEqual({r for r in rows if r in out}, w, ends)
        print("draw ms", [round(t * 1000, 2) for t in self.times])
        self.assertLess(max(self.times[1:]), 0.05)

    def test_values_read(self):
        props = bpy.context.scene.ac9_cloth_sim
        self.drawn('right')
        self.assertEqual(props.piece_ends, 'right')
        self.assertTrue(props.piece_overlap_auto)
        self.assertAlmostEqual(props.piece_overlap, 0.0)

    @patch('ac9_cloth_sim.placement.rebuild_placement', return_value=[])
    def test_fields_write_the_placement(self, rebuild):
        # This fixture tests RNA fields/draw only; its synthetic torso has no
        # body. Treat rebuild as an accepted service, not an accidental failure.
        props = bpy.context.scene.ac9_cloth_sim
        self.drawn('closed')
        props.piece_ring = 1.25
        props.piece_ends = 'left'
        props.piece_overlap = 0.03                      # m on the field, mm in the placement
        p = ST.get(bpy.context)[0]['placement']['A']
        self.assertEqual((p['ends'], p['overlap'], p['ring']), ('left', 30.0, 1.25))
        props.piece_overlap_auto = True
        props.piece_ends = 'open'                       # open drops the ring
        p = ST.get(bpy.context)[0]['placement']['A']
        self.assertEqual(p['ends'], 'open')
        self.assertNotIn('overlap', p)
        self.assertNotIn('ring', p)
        self.assertGreater(rebuild.call_count, 0)
        self.assertTrue(all(call.kwargs.get('preparation') is not None for call in rebuild.call_args_list))


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[(str(t[0]), t[1][-600:]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-900:]) for t in res.errors])), 'utf-8')
