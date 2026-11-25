# SPDX-License-Identifier: GPL-3.0-or-later
"""Start check (no bpy): does each piece start the run (the solver's Xinit) where it was on screen when the run was
pressed? The rule (DECISIONS cloth-sim-place-shows-start-shape-2026-10-25): the shape seen is the start. Nothing
compared them, so a run could start a piece from somewhere else in silence: a collar switched On after a result
started from the placement rule (a ring x2.71, its hand move and fold lost) while its adjusted shape was on screen
(2026-11-04, review docs/reviews/REVIEW_fable_run_start_shape_2026-11-04.md step 1).

A mismatch is said and shown red, the run goes on (user 2026-11-04, DECISIONS cloth-sim-run-start-shape-2026-11-04
b-2: no dead end). The shape is not changed here.

screen: {piece: (source, shape or None, note)} read before the run changes anything (start_check.read_screen):
  source 'placed' (a placed object's own vertices), 'mirrored' (the mirrored half of a pair's object, with its own
  layout points: the source's mirrored into its outline), 'result' (the dressed garment as shown), 'rule' (no shape on screen to hold it to: drawn from the
  placement rule, or a Restart), shape (flat (n, 2) mm, triangles (m, 3), world (n, 3) mm). An optional 4th item
  names what a piece from the rule loses (its hand move): it has no shape on screen to compare with, but the rule
  does not hold that move, so it is said as a piece off (the old Phase Sim took the placed objects away before the
  run read them: the check then saw the collars as drawn from the rule, 2026-11-04)."""
import numpy as np

from .shape_source import CARRY_ATOL, carry, same_layout

TOL_MM = 0.1           # a start this far from the shape on screen is said (mm)
# a piece shown on screen from one of these: a start that could not be compared with it is said and shown red, never
# passed in silence (2026-11-23: a garment whose mesh did not fit its job started a collar 114 mm from where it was
# shown, and the check said nothing because it could not read the garment)
ON_SCREEN = ('placed', 'mirrored', 'result')


def _unread(rec, a, b, why):
    rec.update(checked=True, unread=why, over=list(range(a, b)), over_mm=[SHADE_FULL_MM] * (b - a))


def compare(screen, names, flat, offs, Xinit, mapper):
    """{piece: {'source', 'checked', 'dmax_mm', 'over' (Xinit vertex indices beyond TOL_MM), 'note'}} for the pieces
    of the run. flat / Xinit: the run's layout and start (mm), offs: each piece's first vertex. mapper(Q, V, T, X):
    the positions of layout points Q on a shape (V, T, X) (dress._bary_map)."""
    ends = list(offs) + [len(Xinit)]
    out = {}
    for i, n in enumerate(names):
        a, b = int(ends[i]), int(ends[i + 1])
        src, shape, note, *lost = screen.get(n, ('unknown', None, "not read before the run"))
        rec = {'source': src, 'checked': False, 'dmax_mm': None, 'over': [], 'note': note, 'lost': '', 'unread': ''}
        out[n] = rec
        if shape is None:
            if lost and lost[0]:
                rec.update(checked=True, lost=lost[0], over=list(range(a, b)))
            elif src in ON_SCREEN:
                _unread(rec, a, b, note or "its shape could not be read")
            continue
        V, X = np.asarray(flat[a:b], float), np.asarray(Xinit[a:b], float)
        Fs, Ts, Ws = (np.asarray(s) for s in shape)
        # compared through the layout points as the start is made (shape_source.carry), never by a mark: a check
        # taking the same shortcut as the start it checks says 0.0 mm when the start is torn
        if not (len(Ws) == len(V) and same_layout(Fs, V, atol=CARRY_ATOL)) and not len(Ts):
            _unread(rec, a, b, note or "no triangles to map through")
            continue
        Y, _same = carry(V, Fs, Ts, Ws, mapper)
        d = np.linalg.norm(X - Y, axis=1)
        k = np.nonzero(d > TOL_MM)[0]
        # over_mm: each over vertex's distance, in the same order, for the red shaded by how far it is
        rec.update(checked=True, dmax_mm=round(float(d.max()), 3) if len(d) else 0.0,
                   over=(k + a).tolist(), over_mm=np.round(d[k], 2).tolist())
    return out


SHADE_FULL_MM = 10.0      # the red is at its strongest from this far (1-2 cells of the default 6 mm mesh)


def shade(d_mm, tol=TOL_MM, full=SHADE_FULL_MM):
    """0..1 per distance (mm), on a log scale: tol -> 0, full and beyond -> 1 (0.1 mm faint, 1 mm half, 10 mm full).
    Linear, a 0.3 mm shoulder and a 15 mm hem could not be told apart in one picture."""
    d = np.maximum(np.asarray(d_mm, float), tol)
    return np.clip(np.log10(d / tol) / np.log10(full / tol), 0.0, 1.0)


def lines(res):
    """(summary line, [one line per piece off its screen shape]) for the log."""
    off = {n: r for n, r in res.items() if r['checked'] and r['over']}
    n_ok = sum(1 for r in res.values() if r['checked'] and not r['over'])
    n_rule = sum(1 for r in res.values() if not r['checked'])
    head = (f"start check: {n_ok} piece(s) start as shown" + (f", {len(off)} do NOT" if off else "")
            + (f", {n_rule} from the placement rule or not compared" if n_rule else ""))
    return head, [f"  {n}: starts from the placement rule, without its {r['lost']}" if r['lost'] else
                  f"  {n}: its {r['source']} shape on screen was not compared ({r['unread']}): the start may differ"
                  if r.get('unread') else
                  f"  {n}: starts up to {r['dmax_mm']:.1f} mm from its {r['source']} shape on screen "
                  f"({len(r['over'])} vertices over {TOL_MM} mm)" for n, r in sorted(off.items())]


SQUASHED_RATIO = 1e-6     # a start triangle with less than this of its pattern area: squashed flat


def squashed_starts(Xinit, F, fp, flat, names, ratio=SQUASHED_RATIO):
    """{piece: count} of triangles whose start (Xinit, mm) has less than `ratio` of their flat pattern area: the
    solver drops a triangle of zero area and then cannot run the piece (stage 1 failed). Empty when none."""
    X = np.asarray(Xinit, float)
    F = np.asarray(F, np.int64)
    fl = np.asarray(flat, float)
    a3 = 0.5 * np.linalg.norm(np.cross(X[F[:, 1]] - X[F[:, 0]], X[F[:, 2]] - X[F[:, 0]]), axis=1)
    e1, e2 = fl[F[:, 1]] - fl[F[:, 0]], fl[F[:, 2]] - fl[F[:, 0]]
    a2 = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    bad = a3 < ratio * a2
    if not bad.any():
        return {}
    fp = np.asarray(fp)
    return {str(names[int(p)]): int((bad & (fp == p)).sum()) for p in np.unique(fp[bad])}
