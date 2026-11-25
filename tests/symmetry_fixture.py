# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite, independently counted 2D fixtures; no saved garment or solver quality is assumed."""
import copy
import numpy as np
from ac9_cloth_sim.core import draft as D, spec as S, iline as IL

MODES = ('off', 'self', 'pair_x', 'pair_y', 'both_x', 'both_y')


def fixture(mode, closed=False, reverse=False, centered=False, curved=False):
    sp = S.new_spec('Command symmetry regression')
    n = D.add_polygon(sp, [[0, 0], [400, 0], [400, 500], [0, 500]], 'Panel')
    self_sym = mode == 'self' or mode.startswith('both')
    paired = mode.startswith(('pair', 'both'))
    if self_sym:
        D.piece(sp, n)['mirror_edit'] = True
    peer = None
    if paired:
        made, _ = D.mirror_group(sp, [n], plane=mode[-1]);peer = made[n]
    x = 160 if centered else 50
    N = np.array([[x, 100], [400-x if centered else 120, 100],
                  [400-x if centered else 120, 180], [x, 180]], float) if closed else np.array([[200 if centered else 80, 0], [200 if centered else 80, 500]], float)
    C = [None] * (len(N) if closed else len(N)-1)
    if curved:
        C[0] = np.array([N[0] + [20, -12], N[1] + [-20, -12]])
    lid = IL.add_shape(sp, n, N, C, kind='shape' if closed else 'line', closed=closed)
    expected = (2 if paired else 1) * (2 if self_sym and not centered else 1)
    targets = [(p['name'], it['id']) for p in sp['pieces'] for it in p['internals']]
    assert len(targets) == expected
    assert not D.problems(sp, [p['name'] for p in sp['pieces']])
    assert all(np.isfinite(np.asarray(it['pts'])).all() for p in sp['pieces'] for it in p['internals'])
    assert all(not D.internal_problems(sp,p['name']) for p in sp['pieces'])
    if reverse and peer:
        n, lid = D.twin_line(sp, n, lid)
    return sp, n, lid, targets


def outline_error(sp, name):
    pc = D.piece(sp, name);tw = D.twin_of(sp, name)
    err = 0.
    if tw:
        pb = D.piece(sp, tw)
        for a, b in ((pc, pb), (pb, pc)):
            err = max(err, float(D._dist_to_polyline(D.mirror_fn(a,b)(S.Outline(a).B), S.Outline(b).B).max()))
    return err


def assert_valid(sp):
    bad=D.problems(sp,[p['name'] for p in sp['pieces']]);assert not bad,bad
    for sm in sp.get('seams',[]):
        for side in ('a','b'):
            assert np.isfinite(S.side_polyline(sp,sm[side])).all()


def area(sp):
    return sum(abs(D.signed_area(S.Outline(p).B)) for p in sp['pieces'])
