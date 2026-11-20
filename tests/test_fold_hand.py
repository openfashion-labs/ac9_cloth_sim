# SPDX-License-Identifier: GPL-3.0-or-later
"""A fold's side A on mirror lines (core/fold_hand.py): on every kind of mirror pair the letter names one side (the
mirror image of the other's), the side that turns and the layer kept are the mirror images, and they keep their side
when the pair goes.

Run: blender -b --factory-startup --python tests/test_fold_hand.py -- <parent of ac9_cloth_sim> [out.json]
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
from ac9_cloth_sim import store as ST, pattern_view as PV, place_fold as PF, fold_sides as FS  # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as DR, fold_hand as FH  # noqa: E402

RESULTS = {}
NARROW_X = 60.0          # the line 60 mm from the left of a 200 mm piece: one side narrow, the other wide


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


def rect(spec, name, x0, w=200.0, h=300.0):
    return DR.add_polygon(spec, [[x0, 0], [x0 + w, 0], [x0 + w, h], [x0, h]], name=name)


def area(V, T):
    P = V[T]
    return float((0.5 * np.abs((P[:, 1, 0] - P[:, 0, 0]) * (P[:, 2, 1] - P[:, 0, 1])
                               - (P[:, 1, 1] - P[:, 0, 1]) * (P[:, 2, 0] - P[:, 0, 0]))).sum())


def sides(spec, piece, line):
    """{'A': 'narrow' | 'wide', 'B': ...} as filled (the sides Turns and Layers read), the fold's turning and kept
    sides as narrow / wide."""
    FS.invalidate()
    d = FS.compute(bpy.context, spec, piece, line)
    assert d.get('n'), (piece, line, {k: v for k, v in d.items() if k != 'bkey'})
    ar = [area(d['V'], d['tri'][k]) for k in (0, 1)]
    name = {ab: ('narrow' if ar[k] < ar[1 - k] else 'wide') for k, ab in enumerate('AB')}
    f = next(x for x in spec['folds'] if x.get('piece') == piece and x.get('line') == line)
    return {'A': name['A'], 'turns': name.get(f.get('side')), 'keeps': name.get(f.get('layers')),
            'side': f.get('side'), 'layers': f.get('layers')}


def set_fold(piece, line, side='A', layers='B', deg=30.0):
    PF.write_angle(bpy.context, piece, line, deg, side=side)
    spec = S.loads(S.dumps(now()))
    for f in spec['folds']:
        if f.get('piece') == piece and f.get('line') == line:
            f['layers'] = layers
    ST.put(bpy.context, spec)
    PF.sync_twin(bpy.context, piece, line)
    return now()


class FoldHand(unittest.TestCase):
    def check_pair(self, key, sp, pairs):
        out = {f"{p}/{l}": sides(sp, p, l) for p, l in pairs}
        RESULTS[key] = out
        first = out[f"{pairs[0][0]}/{pairs[0][1]}"]
        for k, v in out.items():
            self.assertEqual(v['side'], first['side'], k)          # the same letter
            self.assertEqual(v['layers'], first['layers'], k)
            self.assertEqual(v['A'], first['A'], k)                # A the same side (narrow or wide)
            self.assertEqual(v['turns'], first['turns'], k)
            self.assertEqual(v['keeps'], first['keeps'], k)

    def base(self):
        sp = S.new_spec('hand')
        p = rect(sp, 'P', 0)
        lid = DR.add_internal(sp, p, [[NARROW_X, 0], [NARROW_X, 300]])
        return sp, p, lid

    def test_mirror_copy_after_fold(self):
        sp, p, lid = self.base()
        sp['folds'] = [{'piece': p, 'line': lid, 'deg': 30.0, 'side': 'A', 'layers': 'B'}]
        made, _ = DR.mirror_group(sp, [p])
        sp = load(sp)
        self.check_pair('copy_after', sp, [(p, lid), (made[p], lid)])

    def test_copy_then_fold(self):
        for plane in ('x', 'y'):
            for on_twin in (False, True):
                sp, p, lid = self.base()
                made, _ = DR.mirror_group(sp, [p], plane=plane)
                load(sp)
                src = (made[p], lid) if on_twin else (p, lid)
                sp = set_fold(*src)
                self.check_pair(f'copy_then_fold_{plane}_{"twin" if on_twin else "orig"}', sp,
                                [src, (p, lid) if on_twin else (made[p], lid)])

    def test_hand_twin_either_direction(self):
        for rev in (False, True):
            sp = S.new_spec('hand')
            p, q = rect(sp, 'P', 0), rect(sp, 'Q', 300)
            for n, m in ((p, False), (q, True)):
                DR.piece(sp, n).update(family='F', mirrored=m)
            lid = DR.add_internal(sp, p, [[NARROW_X, 0], [NARROW_X, 300]])
            qid = DR.add_internal(sp, q, [[440, 300], [440, 0]] if rev else [[440, 0], [440, 300]])
            load(sp)
            sp = set_fold(p, lid)
            self.check_pair(f'drawn_twin_{"reversed" if rev else "same"}', sp, [(p, lid), (q, qid)])

    def test_symmetric_edit(self):
        sp, p, lid = self.base()
        DR.piece(sp, p)['mirror_edit'] = True
        sp['pieces'][0]['internals'] = []
        lid = DR.add_internal(sp, p, [[NARROW_X, 0], [NARROW_X, 300]])
        other = [it['id'] for it in DR.piece(sp, p)['internals'] if it['id'] != lid]
        self.assertEqual(len(other), 1)
        load(sp)
        sp = set_fold(p, lid)
        self.check_pair('symmetric_edit', sp, [(p, lid), (p, other[0])])

    def test_kept_when_pair_goes(self):
        sp, p, lid = self.base()
        made, _ = DR.mirror_group(sp, [p])
        load(sp)
        sp = set_fold(p, lid)
        before = sides(sp, made[p], lid)
        off = S.loads(S.dumps(sp))
        off['symmetry'] = 'off'                   # no twins: the copy's line is on its own
        off = load(off)
        self.assertEqual(DR.mirror_lines(off, p, lid), [])
        after = sides(off, made[p], lid)
        RESULTS['pair_goes'] = {'before': before, 'after': after}
        self.assertEqual(before, after)

    def test_old_file_named_apart_then_matched(self):
        """A file from before: each line's A its own left, the pair's letters swapped by the copy. Opening changes
        nothing (the same sides turn); Match Mirror A/B names them alike, the sides still the same."""
        sp, p, lid = self.base()
        made, _ = DR.mirror_group(sp, [p])
        tw = made[p]
        for it in DR.piece(sp, tw)['internals']:
            it.pop(FH.HAND_KEY, None)                 # as saved before the hand
        sp['folds'] = [{'piece': p, 'line': lid, 'deg': 30.0, 'side': 'A', 'layers': 'B'},
                       {'piece': tw, 'line': lid, 'deg': 30.0, 'side': 'B', 'layers': 'A'}]
        sp = load(sp)
        old = {k: sides(sp, *k) for k in ((p, lid), (tw, lid))}
        self.assertEqual(PF.named_apart(sp, p, lid), [(tw, lid)])
        bpy.ops.ac9_cloth_sim.fold_match_mirror(piece=p, line=lid)
        sp = now()
        new = {k: sides(sp, *k) for k in ((p, lid), (tw, lid))}
        RESULTS['old_file'] = {'old': {f"{a}/{b}": v for (a, b), v in old.items()},
                               'matched': {f"{a}/{b}": v for (a, b), v in new.items()}}
        self.assertEqual(PF.named_apart(sp, p, lid), [])
        for k in old:
            self.assertEqual(old[k]['turns'], new[k]['turns'], k)
            self.assertEqual(old[k]['keeps'], new[k]['keeps'], k)
        self.assertEqual(new[(p, lid)]['side'], new[(tw, lid)]['side'])

    def test_tool_tab_says_named_apart(self):
        """The Internal Line tab draws to its end; on an old pair it says so and offers Match Mirror A/B, after which
        it says nothing."""
        from ac9_cloth_sim import draft_tool as DT
        import types

        class Lay:
            def __init__(self, out):
                self.out, self.enabled, self.use_property_split, self.use_property_decorate = out, True, False, False

            def __getattr__(self, name):           # column / row / box / split / grid_flow ...
                return lambda *a, **k: self

            def label(self, text='', **_k):
                self.out.append(('label', text))

            def prop(self, _d, name, **_k):
                self.out.append(('prop', name))

            def operator(self, idname, **_k):
                self.out.append(('op', idname))
                return types.SimpleNamespace()

        sp, p, lid = self.base()
        made, _ = DR.mirror_group(sp, [p])
        for it in DR.piece(sp, made[p])['internals']:
            it.pop(FH.HAND_KEY, None)
        sp['folds'] = [{'piece': p, 'line': lid, 'deg': 30.0, 'side': 'A', 'layers': 'B'},
                       {'piece': made[p], 'line': lid, 'deg': 30.0, 'side': 'B', 'layers': 'A'}]
        load(sp)
        DT.select_internal(bpy.context, p, lid)

        def draw():
            out = []
            DT.draw_internal_tab(Lay(out), bpy.context)
            return out
        before = draw()
        bpy.ops.ac9_cloth_sim.fold_match_mirror(piece=p, line=lid)
        after = draw()
        RESULTS['tool_tab'] = {'before': before, 'after': after}
        self.assertIn(('op', 'ac9_cloth_sim.fold_match_mirror'), before)
        self.assertIn(('label', DT.tx("A / B named apart on its mirror")), before)
        self.assertNotIn(('op', 'ac9_cloth_sim.fold_match_mirror'), after)
        for out in (before, after):
            self.assertIn(('prop', 'ac9_iline_elastic'), out)     # drawn to the end

    def test_raw_side_to_the_other_half(self):
        """A placed object's saved fold keeps a mesh side index: the other half's index is the mirror image's, by
        where the sides lie (not through letters)."""
        sp, p, lid = self.base()
        made, _ = DR.mirror_group(sp, [p])
        load(sp)
        sp = set_fold(p, lid)
        js = FS.job_spec(bpy.context, sp)
        M = DR.mirror_fn(DR.piece(sp, p), DR.piece(sp, made[p]))
        a, b = PF._mesh_sides(bpy.context, js, p, lid), PF._mesh_sides(bpy.context, js, made[p], lid)
        got = [PF.mirror_raw_side(bpy.context, sp, p, lid, made[p], lid, k) for k in (0, 1)]
        RESULTS['raw_side'] = got
        self.assertEqual(sorted(got), [0, 1])
        for k in (0, 1):
            c = np.asarray(M(a[0][a[1][k]].mean(0)), float).reshape(-1)
            self.assertLess(np.linalg.norm(b[0][b[1][got[k]]].mean(0) - c), 1.0)    # mm

    def test_hand_not_in_shape_keys(self):
        from ac9_cloth_sim import placed_view as PLV, dress as D
        sp, p, lid = self.base()
        a = S.loads(S.dumps(sp))
        b = S.loads(S.dumps(sp))
        FH.set_hand(DR.internal(DR.piece(b, p), lid), -1)
        self.assertEqual(PLV.before_seam_pattern(a, p), PLV.before_seam_pattern(b, p))
        self.assertEqual(D._build_key(a, 6.0), D._build_key(b, 6.0))

    def test_formula(self):
        line = {'pts': [[0, 0], [0, 10]]}

        def mx(q):
            q = np.array(q, float).reshape(-1, 2)
            q[:, 0] *= -1
            return q
        same = {'pts': mx(line['pts']).tolist()}
        back = {'pts': mx(line['pts'])[::-1].tolist()}
        self.assertEqual(FH.mapped_hand(line, same, mx), -1)      # a mirror, the same way: left becomes right
        self.assertEqual(FH.mapped_hand(line, back, mx), 1)       # walked back: left again
        self.assertEqual(FH.mapped_hand(line, line, lambda q: np.asarray(q, float)), 1)
        def two(q):                                                 # mirrored in x then in y: a half turn
            return mx(q) * [1.0, -1.0]
        turned = {'pts': two(line['pts']).tolist()}
        self.assertEqual(FH.det_sign(two), 1)
        self.assertEqual(FH.det_sign(mx), -1)
        self.assertEqual(FH.mapped_hand(line, turned, two), 1)     # a turn keeps left left
        ring = {'pts': [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], 'closed': True}
        ring_m = {'pts': mx(ring['pts']).tolist(), 'closed': True}
        # a C whose ends nearly meet: told by the whole line, not by its ends
        th = np.linspace(0.2, 2 * np.pi - 0.2, 40)
        c = {'pts': np.c_[50 + 20 * np.cos(th), 20 * np.sin(th)].tolist()}
        cm = mx(c['pts'])
        self.assertEqual(FH.mapped_hand(c, {'pts': cm.tolist()}, mx), -1)
        self.assertEqual(FH.mapped_hand(c, {'pts': cm[::-1].tolist()}, mx), 1)
        # the mirror turns the ring clockwise: its left is outside, so A (inside on the ring) is its right
        self.assertEqual(FH.mapped_hand(ring, ring_m, mx), -1)
        self.assertEqual(FH.mapped_hand(ring, {'pts': ring_m['pts'][::-1], 'closed': True}, mx), 1)


if __name__ == '__main__':
    unittest.main(argv=['x'], exit=False, verbosity=2)
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps(RESULTS, indent=1, default=str), encoding='utf-8')
