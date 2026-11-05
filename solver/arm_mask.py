"""Which body triangles belong to the arms (to leave them out while the torso is sewn, stage 1).

An arm triangle has its centroid within `radius` of an arm axis line (place_pieces.py's tilted frames)
and |x| beyond `absx`. Shared by shrink_greedy_b52.py and newton/run_newton_vbd.py.
spec: list of [ox, oy, oz, dx, dy, dz, radius, absx(, zmax)] in mm. zmax keeps the shoulder tops: the axis line runs
on through the shoulder, and without the shoulders there is nothing for the jacket to hang on (2026-09-25).
"""
import numpy as np


def arm_tris(c_mm, spec):
    """c_mm: (n,3) triangle centroids in mm -> bool mask of arm triangles."""
    m = np.zeros(len(c_mm), bool)
    for sp in spec:
        ox, oy, oz, dx, dy, dz, rad, absx = sp[:8]
        zmax = sp[8] if len(sp) > 8 else np.inf
        o = np.array([ox, oy, oz]); d = np.array([dx, dy, dz]); d = d / np.linalg.norm(d)
        v = c_mm - o
        r = np.linalg.norm(v - np.outer(v @ d, d), axis=1)
        if len(sp) > 9 and sp[9]:
            # sp[9] = {"o": [x, y], "zs": [...], "rs": [...], "pad": mm}: the torso's half width along x per height
            # (dress.py). An arm triangle lies beyond the torso's side by more than pad, instead of beyond |x| > absx:
            # the A-pose upper arm hangs beside the chest, its inner faces at |x| 125-168 mm fell inside 1.4 x the
            # torso's half width and counted as body, and the jacket fronts were pushed 31 mm out onto them
            # (REVIEW_fable_G4_r7 §1-3)
            tp = sp[9]
            rt = np.interp(c_mm[:, 2], np.asarray(tp['zs'], float), np.asarray(tp['rs'], float))
            out_ = np.abs(c_mm[:, 0] - tp['o'][0]) > rt + float(tp.get('pad', 5.0))     # rs: the half width along x
            m |= (r < rad) & out_ & (np.sign(c_mm[:, 0] - tp['o'][0]) == np.sign(ox - tp['o'][0])) & (c_mm[:, 2] < zmax)
            continue
        m |= (r < rad) & (np.abs(c_mm[:, 0]) > absx) & (np.sign(c_mm[:, 0]) == np.sign(ox)) & (c_mm[:, 2] < zmax)
    return m
