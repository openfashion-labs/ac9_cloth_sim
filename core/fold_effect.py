# SPDX-License-Identifier: GPL-3.0-or-later
"""When each fold setting takes effect: on the adjusted (placed) shape, when a simulation starts, while Live runs.

One table for the screen (which fold fields stay live while Live runs and carry the live mark, the "Takes effect
now" line), for what Live sends the solver (core/recipe.live_params) and for the user guide. tests/test_fold_effect.py
fails when the screen or live_params disagree with it.

live:
  'folded' -- while Live runs, only on a line / seam folded when the run began (only those have fold hinges with the
              fold's stiffness); on one that began flat the field is grayed and waits for the next run
  'no'     -- read when a run starts; grayed while Live runs
placed:
  True / False -- whether the adjusted shape follows the field; 'turns' -- only when Turns names a side (A / B)
param: the key of live_params that carries the field to the running solver (None: not sent)
note: named in the "Takes effect now" line under the run buttons (a direction goes with its angle)
"""

FIELDS = (
    # internal lines (Tool tab > AC9 Internal Line)
    {'key': 'line_angle', 'kind': 'line', 'name': "Fold Angle", 'prop': 'ac9_iline_fold_angle',
     'placed': 'turns', 'start': True, 'live': 'folded', 'param': 'fold', 'note': True},
    {'key': 'line_direction', 'kind': 'line', 'name': "Direction", 'prop': 'ac9_iline_fold_dir',
     'placed': 'turns', 'start': True, 'live': 'folded', 'param': 'fold', 'note': False},
    {'key': 'line_taper', 'kind': 'line', 'name': "Taper", 'prop': 'ac9_iline_end_taper',
     'placed': False, 'start': True, 'live': 'folded', 'param': 'fold_terminal', 'note': True},
    {'key': 'line_turns', 'kind': 'line', 'name': "Turns", 'prop': 'ac9_iline_fold_side',
     'placed': True, 'start': False, 'live': 'no', 'param': None, 'note': False},
    {'key': 'line_layers', 'kind': 'line', 'name': "Layers", 'prop': 'ac9_iline_fold_layers',
     'placed': False, 'start': True, 'live': 'no', 'param': None, 'note': False},
    # seams (AC9 Seam)
    {'key': 'seam_angle', 'kind': 'seam', 'name': "Seam Fold", 'prop': 'ac9_seam_fold_angle',
     'placed': True, 'start': True, 'live': 'folded', 'param': 'seam_fold', 'note': True},
    {'key': 'seam_direction', 'kind': 'seam', 'name': "Direction", 'prop': 'ac9_seam_fold_dir',
     'placed': True, 'start': True, 'live': 'folded', 'param': 'seam_fold', 'note': False},
    # sent so the solver can say "at the next run" when it changes (its hinges are measured from the run's piece)
    {'key': 'seam_turns', 'kind': 'seam', 'name': "Turns", 'prop': 'ac9_seam_fold_turn',
     'placed': True, 'start': True, 'live': 'no', 'param': 'seam_turn', 'note': False},
)

BY_KEY = {f['key']: f for f in FIELDS}
BY_PROP = {f['prop']: f for f in FIELDS}


def live_field(prop):
    """Whether the field `prop` (a Scene property name) can change a running Live at all."""
    return BY_PROP[prop]['live'] != 'no'


def live_now(prop, running, folded_at_start):
    """While drawing: does this field act now? (False when no run is going: nothing is 'live' then.)"""
    return bool(running) and live_field(prop) and bool(folded_at_start)


def enabled(prop, running, folded_at_start):
    """While drawing: the field is editable unless a Live runs that cannot take it."""
    return not running or live_now(prop, running, folded_at_start)


def live_names():
    """The fold fields named in the 'Takes effect now' line, in table order."""
    return [f['name'] for f in FIELDS if f['note']]
