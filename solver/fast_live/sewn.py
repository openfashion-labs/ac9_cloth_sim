# SPDX-License-Identifier: GPL-3.0-or-later
"""When the placed pieces count as sewn (Native until then, the correction after; correction.py)."""
import numpy as np


def sewn(q, indices, stiffness, n_stitch, limit):
    """True when every pattern stitch (the first n_stitch springs, ke > 0) is no longer than limit (m).

    The springs after the pattern stitches (temp_stitches, anchor_ke, seam_paths targets) are left out: a target
    that stays apart would otherwise keep the run on Native for good (design review 2026-11-02)."""
    n = int(n_stitch)
    on = np.asarray(stiffness[:n]) > 0
    if not on.any():
        return True
    idx = np.asarray(indices).reshape(-1, 2)[:n][on]
    return float(np.linalg.norm(q[idx[:, 0]] - q[idx[:, 1]], axis=1).max()) <= limit
