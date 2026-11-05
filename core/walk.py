# SPDX-License-Identifier: GPL-3.0-or-later
"""Walking a seam (no bpy): the two sides laid against each other from one end, compared notch by notch.

A notch is a mark on a piece's outline, held like a seam end: {"id", "edge", "t", "kind"} in the piece's "notches"
(core/spec.py). The notches inside a side are ordered by arc length from the end the walk starts at (the sides'
'from' ends, or their 'to' ends when walked from the other end). With the same number on both sides they pair in
order; otherwise the pairs are the most notches that pair in order with their positions (fraction of the side's
length) within PAIR_TOL of each other, the rest are left unpaired. A notch within END_MM of a side's end is that end
(the corner it sits on), not a mark inside the side.

At each pair the running difference (arc on A minus arc on B from the start) is what a pattern maker reads off when
walking one piece along the other; between pairs is a segment whose ease is its length on A minus on B.
"""
import numpy as np

from . import spec as S

END_MM = 2.0          # = draft.SEAM_SNAP: a notch this close to a side's end is the end
PAIR_TOL = 0.1        # fraction of the side's length: notches further apart than this do not pair


def notch_s(ol, n):
    """Outline parameter s of notch n."""
    return ol.s_of(n['edge'], float(n['t']))


def side_notches(spec, side, outlines=None):
    """[(arc from the side's 'from' end, notch)] of the notches inside the side, in order. Internal lines: []."""
    if 'line' in side:
        return []
    pc = S.piece_index(spec)[side['piece']]
    nts = pc.get('notches') or []
    if not nts:
        return []
    ol = outlines[side['piece']] if outlines else S.Outline(pc)
    s0 = ol.s_of(*side['from'])
    span = abs(ol.span(side))
    out = []
    for n in nts:
        if n['edge'] not in ol.edges:
            continue
        s = notch_s(ol, n)
        u = (s - s0) % ol.L if side['dir'] > 0 else (s0 - s) % ol.L
        if END_MM < u < span - END_MM:
            out.append((float(u), n))
    out.sort(key=lambda x: x[0])
    return out


def _pair(fa, fb):
    """Index pairs (i, j), increasing in both, pairing the most of fa with fb within PAIR_TOL (then the least total
    difference)."""
    if len(fa) == len(fb):
        return list(zip(range(len(fa)), range(len(fb))))
    n, m = len(fa), len(fb)
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]       # (pairs, -total difference)
    move = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            cands = [(best[i + 1][j], 'a'), (best[i][j + 1], 'b')]
            d = abs(fa[i] - fb[j])
            if d <= PAIR_TOL:
                c, t = best[i + 1][j + 1]
                cands.append(((c + 1, t - d), 'p'))
            best[i][j], move[i][j] = max(cands, key=lambda x: x[0])
    out, i, j = [], 0, 0
    while i < n and j < m:
        mv = move[i][j]
        if mv == 'p':
            out.append((i, j))
            i, j = i + 1, j + 1
        elif mv == 'a':
            i += 1
        else:
            j += 1
    return out


def seam_breaks(spec, seam, outlines=None):
    """[(arc on A, arc on B)] from the sides' 'from' ends at each pair of notches, in order: where the seam is sewn
    notch to notch (segment sewing; user 2026-10-13, BOARD b-D1a). [] when the seam has no pair (no notches,
    an internal-line side): it is sewn in proportion over its whole length, as before."""
    ol = outlines or {p['name']: S.Outline(p) for p in spec['pieces']}
    A, B = side_notches(spec, seam['a'], ol), side_notches(spec, seam['b'], ol)
    if not A or not B:
        return []
    La, Lb = abs(ol[seam['a']['piece']].span(seam['a'])), abs(ol[seam['b']['piece']].span(seam['b']))
    idx = _pair([u / max(La, 1e-9) for u, _ in A], [u / max(Lb, 1e-9) for u, _ in B])
    return [(A[i][0], B[j][0]) for i, j in idx]


def walk(spec, seam, outlines=None, reverse=False):
    """The walk of one seam: {'id', 'La', 'Lb', 'ease', 'pct', 'na', 'nb', 'unpaired', 'reverse', 'pairs':
    [{'a', 'b', 'at_a', 'at_b', 'cum'}], 'segments': [{'la', 'lb', 'ease', 'at_a', 'at_b', 'cum'}]}. Arcs are from
    the start of the walk (the 'from' ends, or the 'to' ends when reverse); the last segment ends at the sides' far
    ends (its cum is the seam's ease)."""
    ol = outlines or {p['name']: S.Outline(p) for p in spec['pieces']}
    La, Lb = S.side_length(spec, seam['a'], ol), S.side_length(spec, seam['b'], ol)
    A, B = side_notches(spec, seam['a'], ol), side_notches(spec, seam['b'], ol)
    if reverse:
        A = [(La - u, n) for u, n in reversed(A)]
        B = [(Lb - u, n) for u, n in reversed(B)]
    idx = _pair([u / max(La, 1e-9) for u, _ in A], [u / max(Lb, 1e-9) for u, _ in B])
    pairs = [{'a': A[i][1]['id'], 'b': B[j][1]['id'], 'at_a': A[i][0], 'at_b': B[j][0], 'cum': A[i][0] - B[j][0]}
             for i, j in idx]
    segs, pa, pb = [], 0.0, 0.0
    for p in pairs + [{'at_a': La, 'at_b': Lb}]:
        la, lb = p['at_a'] - pa, p['at_b'] - pb
        segs.append({'la': la, 'lb': lb, 'ease': la - lb, 'at_a': p['at_a'], 'at_b': p['at_b'],
                     'cum': p['at_a'] - p['at_b']})
        pa, pb = p['at_a'], p['at_b']
    return {'id': seam['id'], 'La': La, 'Lb': Lb, 'ease': La - Lb,
            'pct': 100.0 * (La - Lb) / max(min(La, Lb), 1e-9), 'na': len(A), 'nb': len(B),
            'unpaired': len(A) + len(B) - 2 * len(pairs), 'reverse': bool(reverse), 'pairs': pairs, 'segments': segs}


def walk_all(spec, enabled_only=True):
    """The walk of every seam, the largest |ease| first."""
    ol = {p['name']: S.Outline(p) for p in spec['pieces']}
    out = []
    for sm in spec.get('seams', []):
        if enabled_only and not sm.get('enabled', True):
            continue
        try:
            out.append(walk(spec, sm, ol))
        except (KeyError, StopIteration, ValueError):
            continue
    out.sort(key=lambda w: -abs(w['ease']))
    return out
