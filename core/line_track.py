# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal sewing tracks. Geometry closure is independent of the side's storage kind.

Legacy internal sides keep their direct from/to interval. New sides crossing a
closed line's origin carry an integer wrap; reversing the line negates it.
"""
import numpy as np


class LineTrack:
    internal = True

    def __init__(self, pts, closed=False, item=None):
        self.B = np.asarray(pts, float)
        self.closed = bool(closed)
        if self.closed and np.linalg.norm(self.B[0] - self.B[-1]) > 1e-6:
            self.B = np.vstack([self.B, self.B[:1]])
        self.cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(self.B, axis=0), axis=1))]
        self.L = float(self.cum[-1])
        self.vertices = np.array([0.0, self.L])
        self.edges_known = not self.closed
        if item is not None:
            from . import iline as IL
            N, _, _, own = IL.shape(item)
            if own or (item.get('seam_authored_vertices') and not item.get('ctrl')) or IL.editable(item):
                self.edges_known = True
                ids = item['nodes'] if own else list(range(len(N)))
                self.vertices = self.cum[np.asarray(ids, int)]
                if not self.closed:
                    self.vertices = np.unique(np.r_[self.vertices, self.L])
        self.breaks = np.unique(np.r_[0.0, self.vertices, self.L])

    def at(self, s):
        s = np.asarray(s, float)
        s = np.mod(s, self.L) if self.closed and self.L > 0 else np.clip(s, 0.0, self.L)
        return np.stack([np.interp(s, self.cum, self.B[:, 0]), np.interp(s, self.cum, self.B[:, 1])], -1)

    def whole_edge(self, s):
        if not self.edges_known:
            return None
        i = int(np.clip(np.searchsorted(self.breaks, s, side='right') - 1, 0, len(self.breaks) - 2))
        a, b = self.breaks[i:i + 2]
        return (float(a), float(b), 1) if s - a <= b - s else (float(b), float(a), -1)


def signed_interval(track, s0, s1, direction):
    if not getattr(track, 'closed', True):
        return s1 - s0
    # A whole authored span may itself be a closed curve.
    if abs(abs(s1 - s0) - track.L) < 1e-6:
        return track.L if direction > 0 else -track.L
    return (s1 - s0) % track.L if direction > 0 else -((s0 - s1) % track.L)


def side_span(side, length):
    a = float(side['from']) * length
    d = (float(side['to']) - float(side['from']) + int(side.get('wrap', 0))) * length
    return a, a + d, d


def sample(track, a, b, step=1.0):
    s = np.linspace(a, b, max(2, int(abs(b-a) / step + 1e-8) + 1))
    if track.L > 0:
        shifts = range(int(np.floor(min(a,b)/track.L)), int(np.floor(max(a,b)/track.L))+1)
        knots = np.concatenate([track.cum + k*track.L for k in shifts])
        s = np.unique(np.round(np.r_[s,knots[(knots>min(a,b)) & (knots<max(a,b))]],9))
        if b<a:
            s=s[::-1]
    return track.at(s)


def make_side(track, piece, line, s0, s1, direction):
    if not track.closed:
        return {'piece': piece, 'line': line, 'from': round(float(np.clip(s0 / track.L, 0, 1)), 6),
                'to': round(float(np.clip(s1 / track.L, 0, 1)), 6), 'wrap': 0}
    d = signed_interval(track, s0, s1, direction)
    a = float(s0 % track.L) / track.L
    b = float((s0 + d) % track.L) / track.L
    a, b = round(a, 6), round(b, 6)
    wrap = int(round(d / track.L - (b - a)))
    out = {'piece': piece, 'line': line, 'from': a, 'to': b, 'wrap': wrap}
    return out


def reverse_side(side):
    side['from'], side['to'] = side['to'], side['from']
    if 'dir' in side:
        side['dir'] = -side['dir']
    if 'wrap' in side:
        side['wrap'] = -side['wrap']


def covered_intervals(side):
    """Undirected intervals in [0,1], splitting an origin crossing."""
    a, b, d = side_span(side, 1.0)
    if abs(d) >= 1.0 - 1e-9:
        return [(0.0, 1.0)]
    lo, hi = min(a, b), max(a, b)
    shift = np.floor(lo)
    lo, hi = lo - shift, hi - shift
    return [(lo, hi)] if hi <= 1.0 else [(lo, 1.0), (0.0, hi - 1.0)]


def split_side(side, fraction):
    a, b, _ = side_span(side, 1.0)
    m = a + float(np.clip(fraction, 0, 1)) * (b - a)
    def part(x, y):
        out = dict(side)
        out.pop('wrap', None)
        if not side.get('wrap', 0):
            out.update({'from': round(x, 6), 'to': round(y, 6)})
            if 'wrap' in side:
                out['wrap'] = 0
        else:
            f, t = round(x % 1, 6), round(y % 1, 6)
            out.update({'from': f, 'to': t})
            w = int(round((y - x) - (t - f)))
            out['wrap'] = w
        return out
    return part(a, m), part(m, b)
