"""Smooth corners of the flat pattern: the curve handles either side stay on one line when a handle, the corner or an
edge next to it moves; sharp corners (the default) as before (no Blender)."""
import importlib.util
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_corner_smooth_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
S = __import__(PACKAGE + '.core.spec', fromlist=['spec'])
D = __import__(PACKAGE + '.core.draft', fromlist=['draft'])


def kink(pc, k):
    """Degrees the outline turns at corner k (0: smooth)."""
    _, _, ta, tb = D._tangents(pc, k)
    c = float(ta @ tb) / (np.linalg.norm(ta) * np.linalg.norm(tb))
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def mirror_gap(pc):
    """Largest distance (mm) from a corner or a handle of the piece to the nearest one of its mirror image across
    its center line."""
    M = D.self_reflect(pc)
    P = np.vstack([np.asarray(e['pts'][:1] + (e.get('ctrl') or []), float) for e in pc['edges']])
    return float(np.linalg.norm(M(P)[:, None] - P[None], axis=-1).min(1).max())


def cap():
    """A sleeve-cap-like piece: the top edge split at its middle (corner 3), both halves curved, the handles there
    bent so the outline kinks 40 deg or so."""
    spec = S.new_spec('test')
    nm = D.add_polygon(spec, [(0, 0), (400, 0), (400, 100), (0, 100)], name='Cap')
    pc = D.piece(spec, nm)
    top = next(e['id'] for e in pc['edges'] if e['pts'][0][1] == 100 and e['pts'][-1][1] == 100)
    new = D.split_edge(spec, nm, top, 0.5)[nm]
    D.make_curve(spec, nm, top)
    D.make_curve(spec, nm, new)
    k = D.edge_index(pc, new)                       # the corner between the two halves
    D.set_handle(spec, nm, top, 1, (240, 160))      # the handle before the corner, up and to the right
    D.set_handle(spec, nm, new, 0, (150, 150))      # the one after it, up and to the left
    return spec, nm, top, new, k


class CornerSmooth(unittest.TestCase):
    def test_sharp_by_default_handles_free(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        self.assertFalse(D.is_smooth(pc, k))
        self.assertGreater(kink(pc, k), 30)

    def test_smooth_straightens_the_kink_lengths_kept(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        _, _, ta0, tb0 = D._tangents(pc, k)
        before = kink(pc, k)
        D.set_smooth(spec, nm, k, True)
        _, _, ta1, tb1 = D._tangents(pc, k)
        print(f"kink at the corner: {before:.1f} -> {kink(pc, k):.4f} deg")
        self.assertLess(kink(pc, k), 0.05)
        self.assertAlmostEqual(np.linalg.norm(ta1), np.linalg.norm(ta0), delta=0.01)
        self.assertAlmostEqual(np.linalg.norm(tb1), np.linalg.norm(tb0), delta=0.01)
        self.assertTrue(D.is_smooth(pc, k))
        S.loads(S.dumps(spec))                      # the flag survives saving

    def test_handle_drag_turns_the_other(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        D.set_smooth(spec, nm, k, True)
        lb = np.linalg.norm(D._tangents(pc, k)[3])
        D.set_handle(spec, nm, top, 1, (120, 180))  # dragged far round
        _, _, ta, tb = D._tangents(pc, k)
        print(f"after a drag: kink {kink(pc, k):.4f} deg, other handle {lb:.2f} -> {np.linalg.norm(tb):.2f} mm")
        self.assertLess(kink(pc, k), 0.05)
        self.assertAlmostEqual(np.linalg.norm(tb), lb, delta=0.01)
        C = np.asarray(pc['edges'][k]['pts'][0])
        self.assertTrue(np.allclose(pc['edges'][D.edge_index(pc, top)]['ctrl'][1], (120, 180), atol=1e-3))

    def test_sharp_again_lets_it_kink(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        D.set_smooth(spec, nm, k, True)
        D.set_smooth(spec, nm, k, False)
        D.set_handle(spec, nm, top, 1, (120, 180))
        self.assertGreater(kink(pc, k), 10)

    def test_moving_corner_or_edge_keeps_it_smooth(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        D.set_smooth(spec, nm, k, True)
        D.move_corners(spec, nm, {k: (30, 25)})
        self.assertLess(kink(pc, k), 0.05)
        n = len(pc['edges'])
        D.move_corners(spec, nm, {(k + 1) % n: (0, 60)})       # the far end of the edge after it
        self.assertLess(kink(pc, k), 0.05)

    def test_straight_side_keeps_its_direction(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        D.make_straight(spec, nm, new)              # the edge after the corner straight again
        ub = D._unit(D._tangents(pc, k)[3])
        D.set_smooth(spec, nm, k, True)
        self.assertLess(kink(pc, k), 0.05)
        self.assertTrue(np.allclose(D._unit(D._tangents(pc, k)[3]), ub, atol=1e-6))
        D.set_handle(spec, nm, top, 1, (180, 140))  # the curve's handle only slides along the line
        self.assertLess(kink(pc, k), 0.05)

    def test_mirror_copy_keeps_the_smooth_corner(self):
        spec, nm, top, new, k = cap()
        D.set_smooth(spec, nm, k, True)
        tw = D.mirror_copy(spec, nm)
        tw = tw if isinstance(tw, str) else D.twin_of(spec, nm)
        pa, pb = D.piece(spec, nm), D.piece(spec, tw)
        _, cmap, _ = D.twin_corner_map(spec, nm)
        self.assertIsNotNone(cmap)
        self.assertTrue(D.is_smooth(pb, cmap[k]))
        self.assertEqual(sum(D.is_smooth(pb, j) for j in range(len(pb['edges']))), 1)
        D.set_handle(spec, nm, top, 1, (120, 180))  # edited together: the twin's corner stays smooth too
        self.assertLess(kink(pb, cmap[k]), 0.05)

    def test_center_line_corner_of_a_symmetric_piece(self):
        spec = S.new_spec('test')
        nm = D.add_polygon(spec, [(0, 0), (400, 0), (400, 100), (200, 150), (0, 100)], name='Front')
        pc = D.piece(spec, nm)
        pc['mirror_edit'] = True
        k = next(i for i, e in enumerate(pc['edges']) if e['pts'][0] == [200.0, 150.0])
        n = len(pc['edges'])
        D.make_curve(spec, nm, pc['edges'][k]['id'])           # both halves curved (the mirror one with it)
        self.assertTrue(pc['edges'][(k - 1) % n].get('ctrl'))
        D.set_smooth(spec, nm, k, True)
        self.assertLess(kink(pc, k), 0.05)
        D.set_handle(spec, nm, pc['edges'][k]['id'], 0, (260, 175))   # up and to the right
        print(f"center-line corner after a drag: kink {kink(pc, k):.4f} deg, "
              f"handle {pc['edges'][k]['ctrl'][0]}")
        self.assertLess(kink(pc, k), 0.05)
        self.assertIsNotNone(D.self_mirror_map(pc))           # still symmetric
        self.assertAlmostEqual(pc['edges'][k]['ctrl'][0][1], 150.0, delta=1e-3)   # runs straight across the line
        # the other handle is its mirror image: the same length (turning it only kept its own, 1.5 mm off)
        self.assertLess(mirror_gap(pc), 1e-3)

    def test_symmetric_piece_corner_off_the_line(self):
        """A corner off the center line of a piece edited symmetrically: its mirror corner goes smooth with it, a drag
        or a corner move on either side keeps both smooth and the piece symmetric (handles too)."""
        spec = S.new_spec('test')
        nm = D.add_polygon(spec, [(0, 0), (400, 0), (400, 300), (300, 330), (100, 330), (0, 300)], name='Front')
        pc = D.piece(spec, nm)
        pc['mirror_edit'] = True
        for e in list(pc['edges']):
            if min(e['pts'][0][1], e['pts'][-1][1]) >= 300 and not e.get('ctrl'):
                D.make_curve(spec, nm, e['id'])
        kR = next(i for i, e in enumerate(pc['edges']) if e['pts'][0] == [300.0, 330.0])
        kL = next(i for i, e in enumerate(pc['edges']) if e['pts'][0] == [100.0, 330.0])
        n = len(pc['edges'])
        D.set_handle(spec, nm, pc['edges'][(kR - 1) % n]['id'], 1, (330, 300))
        D.set_handle(spec, nm, pc['edges'][kR]['id'], 0, (260, 330))
        self.assertGreater(kink(pc, kR), 40)
        D.set_smooth(spec, nm, kR, True)
        self.assertTrue(D.is_smooth(pc, kL))
        D.set_handle(spec, nm, pc['edges'][kR]['id'], 0, (250, 380))
        D.move_corners(spec, nm, {kL: (-15, 10)})
        print(f"symmetric piece: kink R {kink(pc, kR):.4f} L {kink(pc, kL):.4f} deg, mirror gap {mirror_gap(pc):.4f} mm")
        self.assertLess(max(kink(pc, kR), kink(pc, kL)), 0.05)
        self.assertLess(mirror_gap(pc), 1e-3)

    def test_pair_either_side(self):
        """A pair: pressed on one side, the other goes smooth; a drag or a corner move on either keeps both mirrored."""
        spec, nm, top, new, k = cap()
        D.mirror_copy(spec, nm)
        tw = D.twin_of(spec, nm)
        pa, pb = D.piece(spec, nm), D.piece(spec, tw)
        _, cmap, M = D.twin_corner_map(spec, nm)
        kb = cmap[k]
        D.set_smooth(spec, tw, kb, True)                       # pressed on the copy
        self.assertTrue(D.is_smooth(pa, k))
        eb = pb['edges'][kb]
        D.set_handle(spec, tw, eb['id'], 0, np.asarray(eb['ctrl'][0]) + np.array([0, 25]))
        D.move_corners(spec, nm, {k: (5, 20)})
        self.assertLess(max(kink(pa, k), kink(pb, kb)), 0.05)
        Ha = np.vstack([e['ctrl'] for e in pa['edges'] if e.get('ctrl')])
        Hb = np.vstack([e['ctrl'] for e in pb['edges'] if e.get('ctrl')])
        self.assertLess(np.linalg.norm(M(Ha)[:, None] - Hb[None], axis=-1).min(1).max(), 1e-3)
        D.set_smooth(spec, tw, kb, False)
        self.assertFalse(D.is_smooth(pa, k))

    def test_split_keeps_the_corner_it_starts(self):
        spec, nm, top, new, k = cap()
        pc = D.piece(spec, nm)
        D.set_smooth(spec, nm, k, True)
        D.split_edge(spec, nm, new, 0.5)            # the edge starting at the smooth corner
        self.assertTrue(D.is_smooth(pc, k))
        self.assertLess(kink(pc, k), 0.05)


if __name__ == '__main__':
    unittest.main()
