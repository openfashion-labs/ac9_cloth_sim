# SPDX-License-Identifier: GPL-3.0-or-later
"""Material-space neighborhoods of the two endpoints of a cross-layer stitch.

A vertex only exempts contact with a small patch around its actual mate on the
other piece. Other faces of a folded piece at the same world location stay ordered.
"""
import numpy as np


def build(flat, pieces, layers, stitches, radius_mm):
    flat = np.asarray(flat, float)
    pieces = np.asarray(pieces)
    layers = np.asarray(layers)
    rows = [set() for _ in flat]
    count = 0
    for a, b in np.asarray(stitches, int):
        if layers[a] == layers[b]:
            continue
        count += 1
        for source, mate in ((a, b), (b, a)):
            near = np.nonzero((pieces == pieces[source]) &
                              (np.linalg.norm(flat - flat[source], axis=1) <= radius_mm))[0]
            for v in near:
                rows[int(v)].add(int(mate))
    starts = np.r_[0, np.cumsum([len(r) for r in rows])].astype(np.int32)
    mates = np.array([m for r in rows for m in sorted(r)], np.int32)
    return starts, mates, {'cross_layer_stitches': count, 'source_vertices': sum(bool(r) for r in rows),
                           'mate_records': len(mates), 'radius_mm': float(radius_mm)}
