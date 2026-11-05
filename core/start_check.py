# SPDX-License-Identifier: GPL-3.0-or-later
"""Start check (no bpy): does each piece start the run (the solver's Xinit) where it was on screen when the run was
pressed? The rule (DECISIONS cloth-sim-place-shows-start-shape-2026-10-25): the shape seen is the start. Nothing
compared them, so a run could start a piece from somewhere else in silence: a collar switched On after a result
started from the placement rule (a ring x2.71, its hand move and fold lost) while its placed shape was on screen
(2026-11-04, review docs/reviews/REVIEW_fable_run_start_shape_2026-11-04.md step 1).

A mismatch is said and shown red, the run goes on (user 2026-11-04, DECISIONS cloth-sim-run-start-shape-2026-11-04
b-2: no dead end). The shape is not changed here.

screen: {piece: (source, shape or None, note)} read before the run changes anything (start_check.read_screen):
  source 'placed' (a placed object's own vertices), 'mirrored' (the mirrored half of a pair's object: compared vertex
  by vertex), 'result' (the dressed garment as shown), 'rule' (no shape on screen to hold it to: drawn from the
  placement rule, or a Restart), shape (flat (n, 2) mm, triangles (m, 3), world (n, 3) mm). An optional 4th item
  names what a piece from the rule loses (its hand move): it has no shape on screen to compare with, but the rule
  does not hold that move, so it is said as a piece off (the old Phase Sim took the placed objects away before the
  run read them: the check then saw the collars as drawn from the rule, 2026-11-04)."""
import numpy as np

TOL_MM = 0.1            # a start this far from the shape on screen is said (mm)


def compare(screen, names, flat, offs, Xinit, mapper):
    """{piece: {'source', 'checked', 'dmax_mm', 'over' (Xinit vertex indices beyond TOL_MM), 'note'}} for the pieces
    of the run. flat / Xinit: the run's layout and start (mm), offs: each piece's first vertex. mapper(Q, V, T, X):
    the positions of layout points Q on a shape (V, T, X) (dress._bary_map)."""
    ends = list(offs) + [len(Xinit)]
    out = {}
    for i, n in enumerate(names):
        a, b = int(ends[i]), int(ends[i + 1])
        src, shape, note, *lost = screen.get(n, ('unknown', None, "not read before the run"))
        rec = {'source': src, 'checked': False, 'dmax_mm': None, 'over': [], 'note': note, 'lost': ''}
        out[n] = rec
        if shape is None:
            if lost and lost[0]:
                rec.update(checked=True, lost=lost[0], over=list(range(a, b)))
            continue
        V, X = np.asarray(flat[a:b], float), np.asarray(Xinit[a:b], float)
        Fs, Ts, Ws = (np.asarray(s) for s in shape)
        if src == 'mirrored' or (len(Fs) == len(V) and np.allclose(Fs, V, atol=1e-3)):
            if len(Ws) != len(X):
                rec['note'] = note or "a different mesh from the run's (mirrored half): not compared"
                continue
            Y = Ws.astype(float)
        else:
            if not len(Ts):
                rec['note'] = note or "no triangles to map through"
                continue
            Y = mapper(V, Fs.astype(float), Ts.astype(np.int64), Ws.astype(float))
        d = np.linalg.norm(X - Y, axis=1)
        rec.update(checked=True, dmax_mm=round(float(d.max()), 3) if len(d) else 0.0,
                   over=(np.nonzero(d > TOL_MM)[0] + a).tolist())
    return out


def lines(res):
    """(summary line, [one line per piece off its screen shape]) for the log."""
    off = {n: r for n, r in res.items() if r['checked'] and r['over']}
    n_ok = sum(1 for r in res.values() if r['checked'] and not r['over'])
    n_rule = sum(1 for r in res.values() if not r['checked'])
    head = (f"start check: {n_ok} piece(s) start as shown" + (f", {len(off)} do NOT" if off else "")
            + (f", {n_rule} from the placement rule or not compared" if n_rule else ""))
    return head, [f"  {n}: starts from the placement rule, without its {r['lost']}" if r['lost'] else
                  f"  {n}: starts up to {r['dmax_mm']:.1f} mm from its {r['source']} shape on screen "
                  f"({len(r['over'])} vertices over {TOL_MM} mm)" for n, r in sorted(off.items())]
