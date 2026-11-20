# SPDX-License-Identifier: GPL-3.0-or-later
"""Which pieces of a Live are sewn (no bpy, no warp): sew_first's per-piece state.

A piece is sewn once it belongs to a group of pieces joined by closed stitches whose stitches among themselves are
all closed; a piece with no stitch at all is sewn from the start. Sewn pieces feel the gravity and the bending
hysteresis; the others float (the gravity cancelled on them) until they join. So a collar placed by a garment that
is already sewn floats to its neckline while the garment keeps its weight and stays free to move (holding the
garment still was ruled out: a neckline that had gone wrong could no longer follow the collar), and pieces placed
apart all float until their seams close, as one sewing without gravity.

Closed: a stitch shorter than gap_mm. All the stitches count as closed once the widest is under gap_mm, once p99 is
under it and the widest no longer shrinks (by 0.5 mm over 30 frames: eased or body-pressed seams that stay a few mm
open), or after max_frames.
"""
import numpy as np


class PieceSewing:
    def __init__(self, stitches, piece_of_vertex, n_pieces, gap_mm=3.0, max_frames=600, stall_frames=30,
                 stall_mm=0.5):
        self.E = np.asarray(stitches, np.int64).reshape(-1, 2)
        self.vp = np.asarray(piece_of_vertex, np.int64)
        self.n = int(n_pieces)
        self.pa, self.pb = self.vp[self.E[:, 0]], self.vp[self.E[:, 1]]
        self.gap_mm, self.max_frames = float(gap_mm), int(max_frames)
        self.stall_frames, self.stall_mm = int(stall_frames), float(stall_mm)
        self.sewn_frame = np.full(self.n, -1, np.int64)     # the frame each piece joined; -1 floating
        self.first = None
        self.hist = {}
        self.all_closed_by = None                           # 'widest' / 'stalled' / 'max_frames' when forced
        # 'per_piece' when a group of pieces is sewn at the first frame (a garment going on from its result with a
        # piece added); 'together' when none is (pieces placed apart): then they all join at once, when every stitch
        # is closed -- joining group by group let the groups that closed first start falling while the others were
        # still sewn (a jacket's overshoot 6 -> 20-25 mm)
        self.mode = None

    def _groups(self, closed):
        parent = list(range(self.n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for a, b in zip(self.pa[closed], self.pb[closed]):
            ra, rb = find(int(a)), find(int(b))
            if ra != rb:
                parent[ra] = rb
        return np.array([find(i) for i in range(self.n)])

    def step(self, f, X):
        """X: the garment's vertex positions (m). Returns the pieces that joined at this frame."""
        if self.first is None:
            self.first = f
        if len(self.E) == 0:
            new = np.nonzero(self.sewn_frame < 0)[0]
            self.sewn_frame[new] = f
            return new
        gap = np.linalg.norm(X[self.E[:, 0]] - X[self.E[:, 1]], axis=1) * 1000.0
        closed = gap < self.gap_mm
        widest = float(gap.max())
        self.hist[f] = widest
        self.hist.pop(f - self.stall_frames - 1, None)
        forced = None
        if widest < self.gap_mm:
            forced = 'widest'
        elif (f - self.stall_frames in self.hist and self.hist[f - self.stall_frames] - widest < self.stall_mm
              and float(np.percentile(gap, 99)) < self.gap_mm):
            forced = 'stalled'
        elif f - self.first >= self.max_frames:
            forced = 'max_frames'
        if forced:
            closed[:] = True
            self.all_closed_by = self.all_closed_by or forced
        if self.mode == 'together' and not forced:
            return np.zeros(0, np.int64)
        root = self._groups(closed)
        sewn = np.zeros(self.n, bool)
        has_stitch = np.zeros(self.n, bool)
        has_stitch[self.pa] = True; has_stitch[self.pb] = True
        sewn[~has_stitch] = True
        for r in np.unique(root):
            members = root == r
            inside = members[self.pa] & members[self.pb]
            if inside.any() and closed[inside].all():
                sewn |= members
        if self.mode is None:
            self.mode = 'per_piece' if (sewn & has_stitch).any() else 'together'
            if self.mode == 'together':
                sewn &= ~has_stitch                       # only the pieces without a seam
        new = np.nonzero(sewn & (self.sewn_frame < 0))[0]
        self.sewn_frame[new] = f
        return new

    def floating(self):
        return self.sewn_frame < 0
