# SPDX-License-Identifier: GPL-3.0-or-later
"""Start plan (no bpy): where each piece's start shape (the solver's Xinit) comes from, decided once from what was
on screen when the run was pressed.

The rule (DECISIONS cloth-sim-place-shows-start-shape-2026-10-25, cloth-sim-start-from-screen-2026-11-13): the
shape seen is the start. prepare builds every start from the placement rule and then overwrites it in turn (the
hand-moved placed objects, the mirrored half of a pair, the last result, the shapes a Reset put back), each step on
its own branch. A step taken before another one changed its source keeps a stale shape: the mirrored half of a pair
was mirrored from its source's rule shape, then the source was replaced by its moved shape on screen and the half,
not in the last result, was not mirrored again (it started 239.4 mm from where it was drawn, 2026-11-13).

This is a safety net, run after all those steps: the pieces it holds start from the shape on screen whatever the
steps made, and how far the steps were off is said as a WARNING (each such line is a fault left in a step, to be
taken out when prepare reads its starts from this plan first). Held now:
  'placed'   a placed object moved by hand (or holding a fold, a seam, a lay, a result) -> 'placed_object'
  'mirrored' the half a pair's Mirror modifier shows, its leading side so moved         -> 'mirrored_object'
Not held (left to prepare as before):
  a placed object not moved: placed by the rule (the screen is made again when the body or the pattern changes,
  so the two agree); one with vertices added or deleted by hand (it has no layout points: prepare places it from
  the guide and says so); 'result' (the frame a run goes on from is a run rule, cloth-sim-run-start-shape-2026-11-04
  b-1); 'rule'.

screen: {piece: (source, shape or None, note[, lost])} and sources: {piece: {'moved', 'vertex_count',
'made_vertex_count', 'reset'}} as start_check.read_screen gives them."""
import numpy as np

from .shape_source import CARRY_ATOL, carry, same_layout

TAKES = ('placed_object', 'mirrored_object')     # the sources whose shape on screen is taken as the start here
TOL_MM = 0.1


def plan(screen, names, sources=None, going_on=False):
    """{piece: {'source', 'why'}} for the run's pieces (names), in that order. screen None: not read, every piece
    left to prepare ('prepare'). going_on: the run goes on from a result (not a Phase Sim from the start): a piece
    put back by Reset and not moved starts from its shape written into the garment, the adjusted shape shown
    ('reset_shown'; sources[n]['reset'])."""
    sources = sources or {}
    out = {}
    for n in names:
        if screen is None:
            out[n] = {'source': 'prepare', 'why': "the screen was not read"}
            continue
        src, shape, note, *_ = screen.get(n, ('unknown', None, "not read before the run"))
        info = sources.get(n, {})
        if src in ('placed', 'mirrored') and shape is not None:
            made, nv = info.get('made_vertex_count'), info.get('vertex_count')
            if made is not None and nv is not None and int(made) != int(nv):
                out[n] = {'source': 'placement_rule', 'why': "vertices added or deleted by hand"}
            elif not info.get('moved') and going_on and info.get('reset'):
                out[n] = {'source': 'reset_shown', 'why': "put back by Reset: written into the garment as shown"}
            elif not info.get('moved'):
                out[n] = {'source': 'placement_rule', 'why': "not moved by hand: placed by the rule"}
            else:
                out[n] = {'source': 'placed_object' if src == 'placed' else 'mirrored_object', 'why': ''}
        elif src == 'result':
            out[n] = {'source': 'screen_result', 'why': note}
        else:
            out[n] = {'source': 'placement_rule', 'why': note or ('' if src == 'rule' else f"read as {src}")}
    return out


def take(pl, screen, sources, layouts, X, mapper):
    """The start shapes the plan takes from the screen. layouts[i] (n, 2) mm and X[i] (n, 3) mm for the i-th piece of
    pl (its order). mapper(Q, V, T, X): positions of layout points Q on a shape (dress._bary_map).
    Returns (X with the taken pieces replaced, {piece: max distance (mm) the steps had put its start from it},
    [pieces taken]). A shape that cannot be put on the run's layout is not taken (pl says why)."""
    X = list(X)
    moved, taken = {}, []
    for i, n in enumerate(pl):
        if pl[n]['source'] not in TAKES:
            continue
        Fs, Ts, Ws = (np.asarray(s) for s in screen[n][1])
        V = np.asarray(layouts[i], float)
        # a placed piece and the half a pair's Mirror modifier shows alike: vertex by vertex only where the layouts
        # are the same (shape_source.carry; the half's layout points are its own, start_check.read_screen)
        if not (len(Ws) == len(V) and same_layout(Fs, V, atol=CARRY_ATOL)) and not len(Ts):
            pl[n].update(source='prepare', why="its placed object has no triangles to map through")
            continue
        Y, _same = carry(V, Fs, Ts, Ws, mapper)
        old = np.asarray(X[i], float)
        moved[n] = float(np.linalg.norm(old - Y, axis=1).max()) if len(old) == len(Y) and len(Y) else None
        X[i] = Y.copy()
        taken.append(n)
    return X, moved, taken


def lines(pl, moved, tol=TOL_MM):
    """WARNING lines: a piece whose start the steps had put elsewhere than its shape on screen (corrected here)."""
    out = []
    for n in sorted(moved):
        d = moved[n]
        if d is None or d > tol:
            far = "" if d is None else f" {d:.1f} mm away"
            out.append(f"  {n}: WARNING the start was made{far} from its {pl[n]['source'].replace('_', ' ')} on "
                       f"screen; started from the screen")
    return out


def drop_names(line, names):
    """A report line listing pieces after ': ' with those of `names` taken out ('' when none is left)."""
    head, sep, rest = line.partition(': ')
    if not sep:
        return line
    left = [x for x in rest.split(', ') if x.strip() not in names]
    return head + sep + ', '.join(left) if left else ''


REPORT_HEADS = ("  put back by Reset", "  not in the last result")   # the steps' lines that list pieces by name


LABELS = {'placed_object': "as shown", 'mirrored_object': "as shown", 'reset_shown': "as shown",
          'screen_result': "from the result shown", 'placement_rule': "from the placement", 'prepare': "from the steps"}


def report(pl, taken):
    """The lines the run's log says about the starts, from the plan, in place of the steps' own lines for the pieces
    it took: those started as shown on screen (a mirrored half named so), then how many start from where."""
    out = []
    if taken:
        out.append("  started as shown on screen: " + ", ".join(
            f"{n} (mirrored half)" if pl.get(n, {}).get('source') == 'mirrored_object' else n for n in taken))
    count = {}
    for v in pl.values():
        k = LABELS.get(v['source'], v['source'])
        count[k] = count.get(k, 0) + 1
    if count:
        out.append("  starts: " + ", ".join(f"{count[k]} {k}" for k in sorted(count, key=lambda k: (-count[k], k))))
    return out


def drop_from_steps(lines, names):
    """The steps' report lines with the pieces of `names` taken out of those that list pieces (REPORT_HEADS); a line
    left with none is dropped."""
    out = []
    for l_ in lines:
        if l_.startswith(REPORT_HEADS):
            l_ = drop_names(l_, set(names))
        if l_:
            out.append(l_)
    return out
