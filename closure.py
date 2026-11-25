# SPDX-License-Identifier: GPL-3.0-or-later
"""A band's closure laid on its ring, one end over the other (user 2026-11-02/03, DECISIONS
cloth-sim-closure-over-2026-11-02).

A band sewn to itself by a seam of two lines apart (a collar stand's or a cuff's ends buttoned over each other) goes
round once from line to line: the two lines meet, and each end's flap past its line goes on round over the other
end. The end on top lies DELTA mm outside the ring there, the other DELTA mm inside, so they start apart, on the
ring, whatever the bodice does (fitted to the fronts by Part to Seam they crossed like scissors, 2026-11-02).

Which end is on top, first that says: the self seam's Over (A / B); the garment's Front Over (the end on the
wearer's left / right of the ring); the buttonholes (the end with the hole: on the band itself, or on the front it
is sewn to). None: the ends lie on each other, said in one line. Nothing is guessed from shapes or roles."""
import numpy as np
from mathutils import Vector

from .core import spec as S
from .i18n import tx

DELTA = 2.0             # mm each flap lies off the ring (the top one out, the other in: 4 mm apart, self contact 1 mm x 2)
OVERS = ('left', 'right')
# A piece's Ends (placement 'ends', user 2026-11-22): open round its guide; closed, its two ends meeting; or closed
# with its left / right end on top. Left and right along the pattern's width as placed, the way it goes round
ENDS = ('open', 'closed', 'left', 'right')
LAPPED = ('left', 'right')
# the names shown for them (AC9 Piece's Ends, its right-click menu, reports, i18n_ja): read from here only
ENDS_NAMES = {'open': "Open", 'closed': "Closed", 'left': "Left on Top", 'right': "Right on Top"}
ENDS_TIPS = {'open': "Wrapped round its guide, the ends apart",
             'closed': "Goes once round, its two ends meeting (a rib, a waistband)",
             'left': "Goes round, its left end (on the pattern) over the right by the overlap",
             'right': "Goes round, its right end (on the pattern) over the left by the overlap"}


def _seg_dist(P, L):
    """Distance of the 2D points P (n, 2) to the polyline L (m, 2)."""
    d = np.full(len(P), np.inf)
    for a, b in zip(L[:-1], L[1:]):
        ab = b - a
        t = np.clip(((P - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
        d = np.minimum(d, np.linalg.norm(P - (a + t[:, None] * ab), axis=1))
    return d


def self_seams(spec, piece):
    """The enabled seams of `piece` to itself whose two sides are lines apart (a closure), not a band's own closing
    seam drawn as one outline side to the other."""
    return [s for s in spec.get('seams', []) if s.get('enabled', True) and s['a']['piece'] == piece
            and s['b']['piece'] == piece]


def ends(spec, pat, i, seam):
    """{'a': mask, 'b': mask} of piece i's vertices at each end of the self seam, or None when they meet (a seam
    too short to be a closure, or the band's own closing seam: the two sides touch on the pattern)."""
    V = np.asarray(pat[f"V{i}"], float)
    h = float(pat['h']) if 'h' in pat else 6.0
    L = {k: S.side_polyline(spec, seam[k]) for k in 'ab'}
    d = {k: _seg_dist(V, L[k]) for k in 'ab'}
    reach = {}
    for k, o in (('a', 'b'), ('b', 'a')):
        # the flap: past the line, away from the other line
        c, co = L[k].mean(0), L[o].mean(0)
        u = c - co
        u /= max(np.linalg.norm(u), 1e-9)
        far = (V - c) @ u
        reach[k] = float(max(far.max(), h))
    R = 2.0 * max(reach.values()) + 2.0 * h
    m = {k: d[k] <= R for k in 'ab'}
    if (m['a'] & m['b']).any() or not m['a'].any() or not m['b'].any():
        return None
    return m


def plan(spec, pat, i, piece):
    """{'seam', 'x': {'a', 'b'} the lines' layout x, 'span' the length round (line to line), 'm' the ends' masks} of
    piece i's closure (the first self seam of two lines apart), or None."""
    for seam in self_seams(spec, piece):
        if 'line' not in seam['a'] or 'line' not in seam['b']:
            continue
        m = ends(spec, pat, i, seam)
        if m is None:
            continue
        x = {k: float(S.side_polyline(spec, seam[k])[:, 0].mean()) for k in 'ab'}
        span = abs(x['b'] - x['a'])
        if span < 1.0:
            continue
        return {'seam': seam, 'x': x, 'span': span, 'm': m}
    return None


def _sewn_to(pat, i, m):
    """The piece sewn most to the vertices m of piece i, or None."""
    st = np.asarray(pat['stitch'])
    cnt = {}
    for r in st:
        if r[0] == i and r[2] != i and m[r[1]]:
            cnt[int(r[2])] = cnt.get(int(r[2]), 0) + 1
        elif r[2] == i and r[0] != i and m[r[3]]:
            cnt[int(r[0])] = cnt.get(int(r[0]), 0) + 1
    return max(cnt, key=cnt.get) if cnt else None


def top_end(spec, pat, names, i, pl, Xring, center_x):
    """('a' | 'b' | None, by: 'seam' | 'front' | 'buttons' | None): the end on top. Xring: the band on its ring
    (world mm), for the side of the body each end is on."""
    seam, m = pl['seam'], pl['m']
    if seam.get('over') in ('a', 'b'):
        return seam['over'], 'seam'
    fo = spec.get('closure_over')
    if fo in OVERS:
        side = {k: 'left' if float(Xring[m[k], 0].mean()) > center_x else 'right' for k in 'ab'}
        if side['a'] != side['b']:
            return ('a' if side['a'] == fo else 'b'), 'front'
    piece = names[i]
    L = {k: S.side_polyline(spec, seam[k]) for k in 'ab'}
    owner = {k: _sewn_to(pat, i, m[k]) for k in 'ab'}
    for b in spec.get('buttons', []):
        h = b.get('hole')
        if not h:
            continue
        if h['piece'] == piece and b['piece'] == piece:
            q = np.asarray([h['pt']], float)
            d = {k: float(_seg_dist(q, L[k])[0]) for k in 'ab'}
            return min(d, key=d.get), 'buttons'
        if h['piece'] != b['piece']:
            for k in 'ab':
                if owner[k] is not None and names[owner[k]] == h['piece'] and owner['ab'.replace(k, '')] is not None                         and names[owner['ab'.replace(k, '')]] == b['piece']:
                    return k, 'buttons'
    return None, None


def _self_button(spec, piece):
    """A button on `piece` whose hole is on `piece` too (its two ends buttoned over each other), or None."""
    for b in spec.get('buttons', []):
        h = b.get('hole')
        if h and b.get('piece') == piece and h.get('piece') == piece and 'pt' in b and 'pt' in h:
            return b
    return None


def _edge_lap(spec, pat, i, piece, p):
    """(the two sides' layout x, seam) of a self seam sewing one end's outline side onto a line inside the other end
    (a waistband's end over its button stand, as pattern files often draw it), or (None, None). Both sides go across
    the piece (each under a quarter of its width along it); two outline sides are the ring's own closing seam (the
    ends meet: no overlap) and are not one."""
    V = np.asarray(pat[f"V{i}"], float)
    W = float(V[:, 0].max() - V[:, 0].min())
    for seam in self_seams(spec, piece):
        if 'line' not in seam['a'] and 'line' not in seam['b']:
            continue
        try:
            L = [S.spun(p, S.side_polyline(spec, seam[k])) for k in 'ab']
        except (KeyError, StopIteration, ValueError):
            continue
        if any(float(np.ptp(q[:, 0])) > 0.25 * W for q in L):
            continue
        # the outline side is one of the piece's two ends (within two mesh steps), not a tuck mid-way along it
        near = 2.0 * (float(pat['h']) if 'h' in pat else 6.0)
        lo, hi = float(V[:, 0].min()), float(V[:, 0].max())
        if any('line' not in seam[k] and min(abs(float(q[:, 0].mean()) - lo), abs(float(q[:, 0].mean()) - hi)) > near
               for k, q in zip('ab', L)):
            continue
        x = [float(q[:, 0].mean()) for q in L]
        if abs(x[1] - x[0]) >= 1.0:
            return x, seam
    return None, None


def lap_from(spec, pat, i, piece, p):
    """(overlap mm, by): how far the two ends of piece i go over each other when nothing is typed -- from the self seam
    of two lines apart or of one end sewn onto a line, else from a button and its hole both on the piece, else 0 (the
    ends meet)."""
    cpl = plan(spec, pat, i, piece)
    if cpl is not None:
        x = [float(S.spun(p, S.side_polyline(spec, cpl['seam'][k]))[:, 0].mean()) for k in 'ab']
        return x, 'seam', cpl['seam']
    x, seam = _edge_lap(spec, pat, i, piece, p)
    if x is not None:
        return x, 'seam', seam
    b = _self_button(spec, piece)
    if b is not None:
        q = S.spun(p, np.asarray([b['pt'], b['hole']['pt']], float))
        return [float(q[0, 0]), float(q[1, 0])], 'button', None
    return None, None, None


def lap_plan(spec, pat, i, piece, V, p):
    """{'x': {'a', 'b'} the two lines' layout x (where the ends meet round the ring), 'span' the length round (line to
    line), 'by', 'seam', 'overlap'} of piece i lapped over itself (Ends: left / right on top). The overlap: the
    number typed (placement 'overlap', mm), else from the seam or the button joining its ends, else 0. At most two
    layers: an overlap over half the piece is cut to it."""
    V = np.asarray(V, float)
    lo_x, hi_x = float(V[:, 0].min()), float(V[:, 0].max())
    W = hi_x - lo_x
    x, by, seam = (None, 'number', None) if p.get('overlap') is not None else lap_from(spec, pat, i, piece, p)
    if x is None:
        o = max(0.0, float(p.get('overlap') or 0.0))
        o = min(o, 0.5 * W - 1.0)
        x = [lo_x + 0.5 * o, hi_x - 0.5 * o]
        by = by if p.get('overlap') is not None else 'none'
    x = sorted(x)
    lim = 0.5 * W - 1.0                       # at most two layers, from a seam or a button too
    if W - (x[1] - x[0]) > lim:
        more = 0.5 * (W - (x[1] - x[0]) - lim)
        x = [x[0] - more, x[1] + more]
    span = x[1] - x[0]
    if span < 1.0:
        return None
    return {'x': {'a': x[0], 'b': x[1]}, 'span': span, 'by': by, 'seam': seam, 'overlap': W - span}


def top_of(pl, ends):
    """The key ('a' | 'b') of the end on top: Ends left = the end at the lower layout x."""
    lo, hi = sorted('ab', key=lambda k: pl['x'][k])
    return lo if ends == 'left' else hi


def offsets(V, pl, top, h):
    """Each vertex's distance off the ring (mm, + out): the flaps past their lines, the top one out and the other in,
    reached over one mesh step past the line."""
    V = np.asarray(V, float)
    x = pl['x']
    lo, hi = sorted('ab', key=lambda k: x[k])
    past = {lo: x[lo] - V[:, 0], hi: V[:, 0] - x[hi]}
    d = np.zeros(len(V))
    for k in 'ab':
        ramp = np.clip(past[k] / max(h, 1e-6), 0.0, 1.0)
        d += (DELTA if k == top else -DELTA) * ramp
    return d


def lay(wrap, V, d, axis_o, axis_z):
    """The band's ring points with each moved d off the ring, along the ring's surface normal (outwards)."""
    eps = 0.5
    X = wrap(V)
    if not np.any(d):
        return X
    dx = wrap(V + [eps, 0.0]) - X
    dy = wrap(V + [0.0, eps]) - X
    n = np.cross(dx, dy)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rel = X - axis_o
    rad = rel - (rel @ axis_z)[:, None] * axis_z
    n *= np.where((n * rad).sum(1) < 0, -1.0, 1.0)[:, None]
    return X + d[:, None] * n
