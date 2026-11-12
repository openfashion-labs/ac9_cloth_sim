# SPDX-License-Identifier: GPL-3.0-or-later
"""The flat placement's frames (no Blender): python tests/test_place_flat_frames.py from the add-on folder.

Laid flat a piece keeps every edge length, its frame is right-handed with the face along the normal, the frame read
back from a shape is the one it was laid with, and a pair's mirror image is the mirror of the shape."""
import os
import sys
import importlib.util

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, '..', 'core', name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BA = _load('body_axes')


def edges(P, T):
    E = np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]
    return np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)


def mirror_flat(flat, cx=0.0):
    # the same as core/spec.mirror_flat (spec.py imports bpy-free modules only through the package: copied here)
    o = list(map(float, flat['o']))
    ex, ey, n = (np.asarray(r, float) for r in flat['R'])
    S = np.array([-1.0, 1.0, 1.0])
    return {'o': [2.0 * cx - o[0], o[1], o[2]], 'R': [list(-S * ex), list(S * ey), list(S * n)]}


def main():
    rng = np.random.default_rng(1)
    V = np.array([[0, 0], [300, 0], [300, 200], [0, 200], [150, 260]], float)
    T = np.array([[0, 1, 2], [0, 2, 3], [3, 2, 4]])
    anchor = [150.0, 100.0]
    fails = []
    # the ground: ex = X, ey = Y, n = Z, 2 mm up
    g = BA.flat_frame([10.0, 20.0, 0.0], [0, 0, 1])
    if not (np.allclose(g['R'], np.eye(3)) and np.allclose(g['o'], [10, 20, 2])):
        fails.append(('ground frame', g))
    for k in range(50):
        n = rng.normal(size=3)
        if k == 0:
            n = [0, 0, -1]                     # a face facing down: Y is the up of the piece
        f = BA.flat_frame(rng.normal(size=3) * 500, n, lift=2.0)
        R = np.asarray(f['R'])
        if not np.allclose(np.cross(R[0], R[1]), R[2], atol=1e-5) or not np.allclose(R @ R.T, np.eye(3), atol=1e-5):
            fails.append(('frame not right-handed orthonormal', k))
        X = BA.lay_flat(f, V, anchor)
        r = edges(X, T) / edges(V, T)
        if np.abs(r - 1).max() > 1e-5:          # R is rounded to 6 digits
            fails.append(('stretched', k, float(np.abs(r - 1).max())))
        if np.abs((X - np.asarray(f['o'])) @ R[2]).max() > 1e-3:     # mm
            fails.append(('not flat', k))
        back = BA.frame_at(V, T, X, anchor)
        if not (np.allclose(back['o'], f['o'], atol=1e-3) and np.allclose(back['R'], f['R'], atol=1e-5)):
            fails.append(('frame_at does not give the frame back', k))
        # the pair: the mirrored piece (layout x mirrored about its center) laid with the mirrored frame is the
        # mirror image of the shape
        cx = 37.0
        c = 0.5 * (V.min(0) + V.max(0))
        Vm = np.c_[2 * c[0] - V[:, 0], V[:, 1]]
        am = [2 * c[0] - anchor[0], anchor[1]]
        Xm = BA.lay_flat(mirror_flat(f, cx), Vm, am)
        Xs = np.c_[2 * cx - X[:, 0], X[:, 1], X[:, 2]]
        if np.abs(Xm - Xs).max() > 1e-3:
            fails.append(('mirror', k, float(np.abs(Xm - Xs).max())))
    # a shape bent round a cylinder: frame_at gives the tangent plane at the anchor (the piece stays where it is)
    g = {'o': [0, 0, 1000], 'R': [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 'r': 120.0}
    Vd = np.array([[x, y] for y in np.linspace(0, 200, 11) for x in np.linspace(0, 300, 16)])
    Td = []
    for j in range(10):
        for i in range(15):
            a = j * 16 + i
            Td += [[a, a + 1, a + 17], [a, a + 17, a + 16]]
    Td = np.array(Td)
    Xw = BA.wrap(g, Vd, anchor, 30.0, 50.0)
    fr = BA.frame_at(Vd, Td, Xw, anchor)
    pa = BA.point(g, 30.0, 50.0)
    if np.linalg.norm(np.asarray(fr['o']) - pa) > 0.5:
        fails.append(('frame_at anchor moved', float(np.linalg.norm(np.asarray(fr['o']) - pa))))
    nrm = np.asarray(fr['R'][2])
    radial = np.array([np.cos(np.radians(30)), np.sin(np.radians(30)), 0.0])
    if nrm @ radial < 0.99:
        fails.append(('frame_at normal not out', float(nrm @ radial)))
    # a flat frame's orientation (2026-11-11): 0 as made, turned and read back, on the ground and on a slanted face;
    # the origin and the face kept; on the ground 90 takes the pattern's +y to -X (counter-clockwise from above)
    for nrm_ in ([0, 0, 1], [0.3, -0.8, 0.5], [1, 0, 0]):
        f0 = BA.flat_frame([10.0, 20.0, 30.0], nrm_, 2.0)
        if abs(BA.flat_turn(f0)) > 1e-4:
            fails.append(('turn of a new frame', nrm_, BA.flat_turn(f0)))
        for deg in (90.0, -45.0, 179.0, 30.0):
            f1 = BA.turn_flat(f0, deg)
            R1 = np.asarray(f1['R'])
            if abs(BA.flat_turn(f1) - deg) > 1e-3:
                fails.append(('turn read back', nrm_, deg, BA.flat_turn(f1)))
            if np.abs(R1 @ R1.T - np.eye(3)).max() > 1e-5 or np.cross(R1[0], R1[1]) @ R1[2] < 0.9999:
                fails.append(('turned frame not right-handed', nrm_, deg))
            if np.abs(R1[2] - np.asarray(f0['R'][2])).max() > 1e-6 or f1['o'] != f0['o']:
                fails.append(('turn moved the face or the origin', nrm_, deg))
    g90 = BA.turn_flat(BA.flat_frame([0, 0, 0], [0, 0, 1], 2.0), 90.0)
    if np.abs(np.asarray(g90['R'][1]) - [-1, 0, 0]).max() > 1e-6:
        fails.append(('ground 90 not counter-clockwise', g90['R'][1]))
    print('FAIL' if fails else 'OK', fails[:5])
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
