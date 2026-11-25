# SPDX-License-Identifier: GPL-3.0-or-later
"""Which side of an internal line is a fold's side A: the line's "hand".

A fold names the two sides of its line A and B (Turns: the side that turns on the adjusted shape; Layers: the side
simulated). A is the left of the line walked from its first point when the line's "hand" is +1 (the default, no
key stored), the right when it is -1. The hand is written on a line when the line is made or linked as the mirror
image of another (Mirror Copy, a piece edited symmetrically, a fold carried to the twin), so that A on the mirror
image is the mirror image of A: a mirror turns left into right, and a line keeps the direction it was drawn in.

The hand is the line's own value from then on. It is not worked out again from the pair, so the side a fold names
keeps its meaning when the pair goes (symmetry off, a piece unlinked or switched off, the lines edited apart).
Lines without a hand read +1, the rule before 2026-11-19, so a stored letter never changes its side.

Every place that decides A reads a_left(); every place that copies a fold onto a mirror line calls carry()."""
import numpy as np

HAND_KEY = 'hand'


def hand(line):
    """+1 (A on the left of the line walked from its first point) or -1 (A on the right) of an internal line dict."""
    return -1 if line is not None and int(line.get(HAND_KEY, 1) or 1) < 0 else 1


def set_hand(line, h):
    if int(h) < 0:
        line[HAND_KEY] = -1
    else:
        line.pop(HAND_KEY, None)


def shape_only(piece):
    """The piece dict with its lines' hands left out, for the keys that stand for the pattern's shape (the mesh, the
    shape kept before sewing, the carry): a hand names a fold's sides and moves nothing."""
    its = piece.get('internals')
    ignored = {HAND_KEY, 'mirror_id'}
    if not its or not any(ignored.intersection(it) for it in its):
        return piece
    return dict(piece, internals=[{k: v for k, v in it.items() if k not in ignored} for it in its])


def a_left(line):
    """True when side A is the left of the line (hand +1)."""
    return hand(line) > 0


def det_sign(M):
    """+1 when the 2D map M (points (n, 2) -> (n, 2)) keeps turning (a move, a turn), -1 when it mirrors."""
    q = np.asarray(M(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])), float).reshape(-1, 2)
    u, v = q[1] - q[0], q[2] - q[0]
    return 1 if float(u[0] * v[1] - u[1] * v[0]) > 0 else -1


def _signed_area(P):
    P = np.asarray(P, float)
    return 0.5 * float((P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]).sum())


def _even(P, n=24):
    """n points evenly spaced along the polyline P."""
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    if s[-1] <= 0:
        return np.repeat(P[:1], n, axis=0)
    t = np.linspace(0.0, s[-1], n)
    return np.c_[np.interp(t, s, P[:, 0]), np.interp(t, s, P[:, 1])]


def same_direction(MP, Q, closed=False):
    """True when line Q runs the way the mapped line MP does. Open: point for point along both lines (evenly spaced),
    forwards against backwards -- not only the ends, which a C-shaped line brings close together; closed: by its
    turning (signed area)."""
    MP, Q = np.asarray(MP, float).reshape(-1, 2), np.asarray(Q, float).reshape(-1, 2)
    if closed:
        return (_signed_area(MP) > 0) == (_signed_area(Q) > 0)
    a, b = _even(MP), _even(Q)
    return float(np.linalg.norm(a - b, axis=1).sum()) <= float(np.linalg.norm(a - b[::-1], axis=1).sum())


def mapped_hand(src, dst, M):
    """The hand dst needs so that its side A is the image under M of src's side A. The left of a line is the left of
    its image under a move or a turn, the right of it under a mirror; walked the other way, left and right swap."""
    s = det_sign(M) * (1 if same_direction(M(np.asarray(src['pts'], float)), dst['pts'], bool(dst.get('closed')))
                       else -1)
    return hand(src) * s


def carry(src, dst, M):
    """Make dst's A the image of src's A (writes dst's hand). Returns True when the hand changed: a fold stored on dst
    before then names the other side, the caller swaps its letters (swap_letters) to keep it where it was."""
    h = mapped_hand(src, dst, M)
    changed = h != hand(dst)
    set_hand(dst, h)
    return changed


def swap_letters(fold):
    """The fold's A and B swapped (Turns and Layers), as a new dict: the same physical sides after its line's hand
    was flipped."""
    sw = {'A': 'B', 'B': 'A'}
    return dict(fold, **{k: sw[fold[k]] for k in ('side', 'layers') if fold.get(k) in sw})
