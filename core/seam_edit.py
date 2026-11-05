# SPDX-License-Identifier: GPL-3.0-or-later
"""Seams on the flat pattern as things to point at and move (no bpy): which seam is under a point, a seam's stable
color, sliding one end of a side along its track, and moving a side to another stretch (another piece).

A side runs along a TRACK: a piece's closed outline (key = piece name) or one of its internal lines (open; key =
piece SEP line id). Positions on a track are arc lengths s in mm.

M:N seams (one edge sewn to several edges, a BOM SeamLinePairGroup) stay 1:1 seams in the spec; the ones from one
BOM group carry the same "group" (bom.group on import, core/bom_to_spec.py). Which of them share an end is never
stored: it is found when an end is dragged (same group, same track, ends within SHARED_MM), so an edit cannot leave
a stale link (Fable review 2026-10-13).
"""
import colorsys
import zlib
from ._i18n import rpt

import numpy as np

from . import spec as S

SEP = "\x1f"            # same as seam_tool.SEP
SHARED_MM = 0.05        # two group members' ends this close are one split point: they move together
MIN_SIDE_MM = 2.0       # a side no shorter than this after an end is dragged
LINE_END_MM = 1.0       # an internal line's end this close to the outline is a snap target there


class LineTrack:
    """An internal line as a track (open: no wrap-round)."""
    closed = False

    def __init__(self, pts):
        self.B = np.asarray(pts, float)
        self.cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(self.B, axis=0), axis=1))]
        self.L = float(self.cum[-1])

    def at(self, s):
        s = np.clip(np.asarray(s, float), 0.0, self.L)
        return np.stack([np.interp(s, self.cum, self.B[:, 0]), np.interp(s, self.cum, self.B[:, 1])], -1)


def is_closed(tr):
    return getattr(tr, 'closed', True)


def tracks(spec, outlines=None, only_enabled=True):
    """{key: track} for every (enabled) piece's outline and internal lines."""
    out = {}
    for pc in spec['pieces']:
        if only_enabled and not pc.get('enabled', True):
            continue
        out[pc['name']] = outlines[pc['name']] if outlines else S.Outline(pc)
        for it in pc.get('internals', []):
            if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                continue
            out[pc['name'] + SEP + it['id']] = LineTrack(it['pts'])
    return out


def track_key(side):
    return side['piece'] + SEP + side['line'] if 'line' in side else side['piece']


def span(side, tr):
    """(s0, s1, signed length) of a side on its track: s1 = s0 + signed length (unwrapped)."""
    if 'line' in side:
        s0, s1 = side['from'] * tr.L, side['to'] * tr.L
        return s0, s1, s1 - s0
    s0 = tr.s_of(*side['from'])
    d = tr.span(side)
    return s0, s0 + d, d


def make_side(tr, piece_key, s0, s1, direction):
    """A spec side from s0 to s1 (s1 unwrapped or not) running `direction` (+1 ccw / -1 cw) on the track."""
    if not is_closed(tr):
        pc, line = piece_key.split(SEP)
        return {'piece': pc, 'line': line, 'from': round(float(np.clip(s0, 0, tr.L)) / tr.L, 6),
                'to': round(float(np.clip(s1, 0, tr.L)) / tr.L, 6)}
    ef, tf = tr.edge_t(s0)
    et, tt = tr.edge_t(s1)
    return {'piece': piece_key, 'from': [ef, round(tf, 6)], 'to': [et, round(tt, 6)], 'dir': int(direction)}


def _on_span(s, s0, d, L, closed):
    """Is track position s on the stretch from s0 running d (signed)? Returns the distance along it or None."""
    if closed:
        u = (s - s0) % L if d >= 0 else (s0 - s) % L
    else:
        u = (s - s0) if d >= 0 else (s0 - s)
    return u if -1e-6 <= u <= abs(d) + 1e-6 else None


def project(tr, q):
    """(s, distance mm) of the point of track tr nearest q."""
    if is_closed(tr):
        B = np.vstack([tr.B, tr.B[:1]])
    else:
        B = tr.B
    A, Dd = B[:-1], np.diff(B, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((np.asarray(q, float) - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    d = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
    j = int(d.argmin())
    return float(tr.cum[j] + t[j] * np.sqrt(ll[j])), float(d[j])


def seams_at(spec, trk, q, tol_mm):
    """[(distance mm, seam id, side 'a'|'b')] of every seam side passing within tol_mm of q, nearest first. Sides of
    seams on pieces that are off are skipped."""
    out = []
    proj = {}
    for sm in spec['seams']:
        for k in ('a', 'b'):
            key = track_key(sm[k])
            tr = trk.get(key)
            if tr is None:
                continue
            if key not in proj:
                proj[key] = project(tr, q)
            s, dist = proj[key]
            if dist > tol_mm:
                continue
            try:
                s0, _, d = span(sm[k], tr)
            except (KeyError, StopIteration):
                continue
            if _on_span(s, s0, d, tr.L, is_closed(tr)) is not None:
                out.append((dist, sm['id'], k))
    out.sort(key=lambda x: x[0])
    return out


def next_pick(cands, current):
    """The seam to select from candidates (seams_at) when clicking: the nearest, or -- clicking again where the
    current seam is one of them -- the next one (as Blender cycles through objects under the cursor)."""
    ids = []
    for _, sid, k in cands:
        if all(sid != x[0] for x in ids):
            ids.append((sid, k))
    if not ids:
        return None
    cur = [i for i, (sid, _) in enumerate(ids) if sid == current]
    if cur and len(ids) > 1:
        return ids[(cur[0] + 1) % len(ids)]
    return ids[0]


def ends(spec, sm, trk):
    """[(side 'a'|'b', 'from'|'to', track key, s, point mm)] of a seam's four ends."""
    out = []
    for k in ('a', 'b'):
        tr = trk.get(track_key(sm[k]))
        if tr is None:
            continue
        s0, s1, _ = span(sm[k], tr)
        for w, s in (('from', s0), ('to', s1)):
            out.append((k, w, track_key(sm[k]), s, tr.at(s)))
    return out


# ---------------------------------------------------------------- color

def group_of(sm):
    g = (sm.get('bom') or {}).get('group')
    return f"G{g}" if g is not None else sm.get('group')


def color(sm):
    """A seam's color from its group (or its id): stays the same when other seams are added or deleted."""
    key = group_of(sm) or sm['id']
    h = (zlib.crc32(key.encode('utf-8')) % 1000) / 1000.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.65, 0.95)
    return (r, g, b, 1.0)


def _side_key(sd):
    def end(e):
        if isinstance(e, (list, tuple)) and len(e) == 2:
            return (str(e[0]), round(float(e[1]), 3))
        return (str(sd.get('line', sd.get('edge'))), round(float(e), 3)) if isinstance(e, (int, float)) else repr(e)
    return (sd.get('piece'), sd.get('line'), frozenset(end(e) for e in (sd.get('from'), sd.get('to'))))


def duplicates(spec, sm):
    """Ids of the other seams that sew the same two lines as sm (either side order or direction): a BOM can carry a
    seam twice (a raglan sleeve's underarm, 2026-10-14), and the copy is drawn over it and sewn twice when on."""
    k = frozenset((_side_key(sm['a']), _side_key(sm['b'])))
    return [x['id'] for x in spec.get('seams', []) if x['id'] != sm['id']
            and frozenset((_side_key(x['a']), _side_key(x['b']))) == k]


def stretches(spec, sm):
    """The seams of sm's M:N seam: the same group with a side on the same piece track as sm's (one edge sewn to
    several stretches). [sm['id']] for a plain seam (a group whose copies sit on other pieces -- a left / right pair
    imported from one BOM seam -- is not M:N)."""
    g = group_of(sm)
    if g is None:
        return [sm['id']]
    keys = {track_key(sm['a']), track_key(sm['b'])}
    return [o['id'] for o in spec['seams'] if group_of(o) == g and (track_key(o['a']) in keys or track_key(o['b']) in keys)]


# ---------------------------------------------------------------- moving an end

def shared_ends(spec, sid, k, which, trk):
    """[(seam id, side, 'from'|'to')] of the ends of the same group's other seams that sit on this end (a split point
    of an M:N seam)."""
    sm = next(x for x in spec['seams'] if x['id'] == sid)
    g = group_of(sm)
    if g is None:
        return []
    key = track_key(sm[k])
    tr = trk[key]
    s0, s1, _ = span(sm[k], tr)
    s = s0 if which == 'from' else s1
    out = []
    for o in spec['seams']:
        if o['id'] == sid or group_of(o) != g:
            continue
        for ko in ('a', 'b'):
            if track_key(o[ko]) != key:
                continue
            o0, o1, _ = span(o[ko], tr)
            for wo, so in (('from', o0), ('to', o1)):
                d = abs(((so - s) + tr.L / 2) % tr.L - tr.L / 2) if is_closed(tr) else abs(so - s)
                if d <= SHARED_MM:
                    out.append((o['id'], ko, wo))
    return out


def _move_one(sm, k, which, ds, tr):
    """Slide end `which` of side k by ds mm along the track (+ = ccw / along the line); returns the new side's length
    (signed span) or raises ValueError."""
    s0, s1, d = span(sm[k], tr)
    if which == 'from':
        s0 += ds
    else:
        s1 += ds
    nd = s1 - s0
    if d != 0 and np.sign(nd) != np.sign(d) or abs(nd) < MIN_SIDE_MM:
        raise ValueError(rpt("{0}: the side would be shorter than {1:.0f} mm", sm['id'], MIN_SIDE_MM))
    if is_closed(tr) and abs(nd) > tr.L - MIN_SIDE_MM:
        raise ValueError(rpt("{0}: the side would go all the way round", sm['id']))
    if not is_closed(tr):
        if not (-0.01 <= s0 <= tr.L + 0.01 and -0.01 <= s1 <= tr.L + 0.01):      # t is kept to 6 decimals
            raise ValueError(rpt("{0}: past the end of the line", sm['id']))
        s0, s1 = float(np.clip(s0, 0, tr.L)), float(np.clip(s1, 0, tr.L))
    direction = 1 if nd > 0 else -1
    if not is_closed(tr):
        sm[k] = make_side(tr, track_key(sm[k]), s0, s1, direction)
    else:
        sm[k] = make_side(tr, sm[k]['piece'], s0 % tr.L, s1 % tr.L, direction)
    return nd


def move_end(spec, sid, k, which, ds, trk, with_group=True):
    """Slide one end of a seam's side by ds mm along its track, in place; the same group's ends on that point move
    with it (an M:N split point). The other side is not touched: the seam's ease changes. Returns the
    seam ids changed. Raises ValueError (spec unchanged) if a side would collapse or run past a line's end."""
    trk_key = None
    moves = [(sid, k, which)]
    if with_group:
        moves += shared_ends(spec, sid, k, which, trk)
    by_id = {sm['id']: sm for sm in spec['seams']}
    new = {}
    for i, kk, w in moves:
        sm = dict(by_id[i])
        sm.update(new.get(i, {}))
        trk_key = track_key(sm[kk])
        if trk_key not in trk:
            raise ValueError(rpt("{0}: its piece is off", i))
        _move_one(sm, kk, w, ds, trk[trk_key])
        new[i] = {kk: sm[kk]} if i not in new else {**new[i], kk: sm[kk]}
    for i, upd in new.items():
        by_id[i].update(upd)
    return list(new)


def snap_s(tr, s, tol_mm, targets):
    """s snapped to the nearest of targets (track positions: corners, notches, other seams' ends) within tol_mm."""
    if not len(targets):
        return s, False
    t = np.asarray(targets, float)
    d = np.abs(((t - s) + tr.L / 2) % tr.L - tr.L / 2) if is_closed(tr) else np.abs(t - s)
    j = int(d.argmin())
    return (float(t[j]), True) if d[j] <= tol_mm else (s, False)


def snap_targets(spec, key, trk, skip=()):
    """Track positions worth snapping an end to on track `key`: the outline's corners, the piece's notches, the
    ends of its internal lines that touch the outline (a pocket line, a dart leg), and the ends of the other seams
    on it (skip: (seam id, side, end) left out)."""
    tr = trk[key]
    out = []
    if is_closed(tr):
        out += [tr.edges[e][0] for e in tr.order]
        pc = S.piece_index(spec)[key]
        for n in pc.get('notches', []):
            if n['edge'] in tr.edges:
                out.append(tr.s_of(n['edge'], float(n['t'])))
        for k2, t2 in trk.items():
            if is_closed(t2) or k2.split(SEP)[0] != key:
                continue
            for q in (t2.B[0], t2.B[-1]):
                s, d = project(tr, q)
                if d <= LINE_END_MM:
                    out.append(s % tr.L)
    else:
        out += [0.0, tr.L]
    for sm in spec['seams']:
        for k in ('a', 'b'):
            if track_key(sm[k]) != key:
                continue
            s0, s1, _ = span(sm[k], tr)
            for w, s in (('from', s0), ('to', s1)):
                if (sm['id'], k, w) not in skip:
                    out.append(s % tr.L if is_closed(tr) else s)
    return out


# ---------------------------------------------------------------- moving a side

def move_side(spec, sid, k, key, s_from, direction, trk, length=None, snap_mm=3.0):
    """Put side k of seam sid on track `key` from s_from, running `direction`, as long as it was (mm) unless `length`
    is given; its far end snaps to a corner or another seam's end within snap_mm. In place; returns the new side.
    The seam keeps its id and the other side: on a dressed garment the carry treats the moved side as newly sewn."""
    sm = next(x for x in spec['seams'] if x['id'] == sid)
    if length is None:                                  # arc length on its track (the polyline resamples curves)
        otr = trk.get(track_key(sm[k]))
        length = abs(span(sm[k], otr)[2]) if otr is not None else S.side_length(spec, sm[k])
    tr = trk[key]
    if not is_closed(tr):
        s1 = s_from + direction * length
        if not (-0.01 <= s1 <= tr.L + 0.01):
            raise ValueError(rpt("the side would run past the end of the line"))
        s1 = float(np.clip(s1, 0, tr.L))
    else:
        if length > tr.L - MIN_SIDE_MM:
            raise ValueError(rpt("the side is longer than the piece's whole outline"))
        s1 = s_from + direction * length
    s1n, _ = snap_s(tr, s1 % tr.L if is_closed(tr) else s1, snap_mm,
                    [t for t in snap_targets(spec, key, trk, skip={(sid, k, 'from'), (sid, k, 'to')})])
    if is_closed(tr):
        u = ((s1n - s_from) * direction) % tr.L
        if u < MIN_SIDE_MM:
            s1n = s1 % tr.L
    side = make_side(tr, key, s_from % tr.L if is_closed(tr) else s_from, s1n, direction)
    sm[k] = side
    return side


# ---------------------------------------------------------------- M:N seams drawn by hand (Add Seam M:N)

def signed_len(tr, s0, s1, direction):
    """Signed length of the stretch from s0 to s1 running `direction` on the track (as seam_tool draws it)."""
    if not is_closed(tr):
        return s1 - s0
    return ((s1 - s0) % tr.L) if direction > 0 else -((s0 - s1) % tr.L)


def mn_breaks(la, lb, merge_mm=1.0):
    """The cut points of an M:N seam whose sides are stretches of lengths la (side A, in order) and lb (side B):
    [(fa, fb)] fractions of each side's whole length, from (0, 0) to (1, 1). Every stretch end of either side is a cut
    (the other side is cut at the same fraction, user 2026-10-21: by the proportion of the lengths); an end of A and
    an end of B closer than merge_mm (on either side) are one cut, each side at its own end, so no tiny seam is left."""
    TA, TB = float(sum(la)), float(sum(lb))
    FA = np.cumsum(la)[:-1] / TA
    FB = np.cumsum(lb)[:-1] / TB
    used = set()
    cuts = []
    for fa in FA:
        near = [j for j, fb in enumerate(FB) if j not in used and abs(fb - fa) * min(TA, TB) <= merge_mm]
        if near:
            j = min(near, key=lambda j: abs(FB[j] - fa))
            used.add(j)
            cuts.append((float(fa), float(FB[j])))
        else:
            cuts.append((float(fa), float(fa)))
    cuts += [(float(fb), float(fb)) for j, fb in enumerate(FB) if j not in used]
    cuts.sort(key=lambda c: c[0] + c[1])
    out = [(0.0, 0.0)]
    for c in cuts:
        if c[0] > out[-1][0] + 1e-9 and c[1] > out[-1][1] + 1e-9:
            out.append(c)
    return out + [(1.0, 1.0)]


def mn_sides(runs, tracks_):
    """runs: [(track key, s0, s1, direction)] of one side, in order. -> (lengths, at(f)): at(f0, f1) is the
    (track key, s from, s to, direction) of the part between fractions f0 and f1 of the side's whole length (the
    part lies on one stretch: the cuts include every stretch end)."""
    L = [abs(signed_len(tracks_[k], s0, s1, d)) for k, s0, s1, d in runs]
    F = np.r_[0.0, np.cumsum(L)] / sum(L)

    def at(f0, f1):
        m = 0.5 * (f0 + f1)
        i = int(np.clip(np.searchsorted(F, m, side='right') - 1, 0, len(runs) - 1))
        k, s0, s1, d = runs[i]
        sl = signed_len(tracks_[k], s0, s1, d)
        u0 = float(np.clip((f0 - F[i]) / (F[i + 1] - F[i]), 0.0, 1.0))
        u1 = float(np.clip((f1 - F[i]) / (F[i + 1] - F[i]), 0.0, 1.0))
        return k, s0 + u0 * sl, s0 + u1 * sl, d
    return L, at


def mn_seams(runs_a, runs_b, tracks_, merge_mm=1.0):
    """The 1:1 seams [(side a, side b)] an M:N seam is sewn as: both sides cut by the proportion of their lengths."""
    la, at_a = mn_sides(runs_a, tracks_)
    lb, at_b = mn_sides(runs_b, tracks_)
    cuts = mn_breaks(la, lb, merge_mm)
    out = []
    for (a0, b0), (a1, b1) in zip(cuts[:-1], cuts[1:]):
        ka, sa0, sa1, da = at_a(a0, a1)
        kb, sb0, sb1, db = at_b(b0, b1)
        out.append((make_side(tracks_[ka], ka, sa0, sa1, da), make_side(tracks_[kb], kb, sb0, sb1, db)))
    return out
