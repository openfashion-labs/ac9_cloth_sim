# SPDX-License-Identifier: GPL-3.0-or-later
"""Lay on Edge (place_lay.py, user 2026-10-22): one band laid on its partners' edge as they are placed, outside
Blender (the solver's Python), with the check Simulate's lay uses (solver/lay.py apply, bridge on: the
partners may lie apart where they are not sewn yet).

  python lay_once.py <in.npz> <out.npz>

in: X (n, 3) mm, flat (n, 2) mm, vp (n,) piece, E (m, 2) stitches, F (t, 3), fp (t,) the cloth's triangles and their
piece, B (b, 3) mm, T (c, 3) the body, names, piece. out: X (n_k, 3) mm the band laid (absent when the placed shape
stays), row (JSON: the run_log 'lay' row)."""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lay as L   # noqa: E402


def main(src, dst):
    z = np.load(src, allow_pickle=False)
    X = np.array(z['X'], float)
    names = [str(n) for n in z['names']]
    piece = str(z['piece'])
    vp = np.asarray(z['vp'])
    X0 = X.copy()
    Xo, rows = L.apply({'lay': {'pieces': [piece]}}, X, np.asarray(z['flat'], float), vp, np.asarray(z['E']),
                       np.asarray(z['F']), np.asarray(z['fp']), names, np.asarray(z['B'], float), np.asarray(z['T']),
                       np.ones(len(X), bool), bridge=True)
    row = rows[0] if rows else {'piece': piece, 'used': 'placed', 'skip': 'not a piece of the pattern'}
    out = {'row': json.dumps(row, default=float)}
    if row.get('used') == 'laid':
        k = names.index(piece)
        out['X'] = Xo[vp == k]
        out['moved_mm'] = float(np.linalg.norm(Xo[vp == k] - X0[vp == k], axis=1).max())
    np.savez(dst, **out)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
