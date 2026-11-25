# SPDX-License-Identifier: GPL-3.0-or-later
"""Local Sim has no entry on the screen any more, and the piece roles are drawn only with Experimental Features on:
the Run panel, the placed / worn piece's right-click rows, the Fold field's note, AC9 Piece's Role and Start Shape
rows and the Piece Roles panel.

Run: blender -b --factory-startup --python tests/test_experimental_roles.py -- <parent of ac9_cloth_sim>
"""
import sys
import types
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import (selection as SEL, toolbar as TB, pattern_view as PV, placed_view as PLV,  # noqa: E402
                           store as ST, tool_tab as TT, ui as U, dress as D, draft_tool as DT, place_fold as PF)
from ac9_cloth_sim.core import spec as S, draft as DR  # noqa: E402


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class _L:
    """A layout that records what is drawn: (kind, name, text)."""
    def __init__(self, out):
        self.__dict__['out'] = out

    def prop(self, _data, prop, text=None, **_k):
        self.out.append(('prop', prop, text))

    def operator(self, op, text=None, **_k):
        self.out.append(('op', op, text))
        return types.SimpleNamespace()

    def operator_menu_enum(self, op, prop, text=None, **_k):
        self.out.append(('menu', op, text))

    def label(self, text='', **_k):
        self.out.append(('label', '', text))

    def panel(self, *_a, **_k):
        return _L(self.out), _L(self.out)

    def __getattr__(self, _n):
        return lambda *_a, **_k: _L(self.out)


class ExperimentalRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = bpy.context
        spec = S.new_spec('ExpRoles')
        DR.add_polygon(spec, [[0, 0], [240, 0], [240, 50], [0, 50]], name='A')
        spec['placement'] = {'A': dict(guide='torso', theta=0., h=0., anchor=[120., 25.])}
        ST.new_text(ctx, spec['name'])
        ST.text(ctx).write(S.dumps(spec))
        ST.invalidate()
        spec, outlines = ST.get(ctx)
        PV.rebuild(ctx, spec, outlines)
        me = bpy.data.meshes.new('ER_placed_A')
        me.from_pydata([(0, 0, 0), (.03, 0, 0), (0, 0, .03)], [], [(0, 1, 2)])
        ob = bpy.data.objects.new('ER_placed_A', me)
        PLV._collection(ctx).objects.link(ob)
        ob[PLV.PROP] = 'A'
        ob[PLV.GARMENT_PROP] = spec['name']
        ob[PLV.ORG_PROP] = [0, 0, 0]
        cls.ob = ob
        cls._exp = D.experimental

    def tearDown(self):
        D.experimental = self._exp

    def set_exp(self, on):
        D.experimental = lambda context=None: on

    def piece_drawn(self):
        ctx = bpy.context
        SEL.deselect_all(ctx)
        self.assertTrue(TB.select_object(ctx, self.ob))
        ctx.view_layer.update()
        self.assertEqual(TT.resolve(ctx, ST.get(ctx)[0]), 'PIECE')
        out = []
        TT.AC9_PT_ToolPiece.draw(types.SimpleNamespace(layout=_L(out)), ctx)
        return out

    def test_role_rows_only_with_experimental(self):
        for on in (False, True):
            self.set_exp(on)
            menus = {o[1] for o in self.piece_drawn() if o[0] == 'menu'}
            self.assertEqual({'ac9_cloth_sim.set_role', 'ac9_cloth_sim.set_start'} <= menus, on, on)

    def test_roles_panel_only_with_experimental(self):
        for on in (False, True):
            self.set_exp(on)
            self.assertEqual(bool(U.AC9_PT_SimRoles.poll(bpy.context)), on)

    def test_no_local_sim_anywhere(self):
        self.assertFalse(hasattr(PF, 'AC9_OT_SimFoldLocalSim'))
        self.assertNotIn('fold_local_sim', dir(bpy.ops.ac9_cloth_sim))
        for on in (False, True):
            self.set_exp(on)
            for kind in ('placed', 'garment'):
                for r in DT._MENU_ITEMS[kind]:
                    self.assertFalse(r is not None and 'LOCAL' in r[2], (kind, r))
            drawn = []
            U.draw_run(_L(drawn), bpy.context)          # the AC9 Run panel, Settings opened (the recorder's panel)
            texts = [o[2] for o in drawn if o[2]]
            self.assertFalse(any('Local Sim' in t for t in texts), texts)
            self.assertFalse(any(o[1] == 'local_gravity' for o in drawn))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], exit=False)
