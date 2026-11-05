# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded operational evidence for a Live run with no frame limit."""
from collections import deque
import json
from pathlib import Path


class Diagnostics:
    KEEP = 256

    def __init__(self, output):
        self.output = Path(output)
        self.corrections = deque(maxlen=self.KEEP)
        self.native = deque(maxlen=self.KEEP)
        self.queries = deque(maxlen=self.KEEP)
        self.summary = dict(corrections=0, accepted=0, native_substeps=0, queries=0,
                            energy_increases=0, linear_failures=0, native_unsafe=0,
                            guard_negative_max=0, guard_ambiguous_max=0,
                            cg_iterations_max=0, failures=0, retained_limit=self.KEEP)

    def record_correction(self, row):
        self.corrections.append(row)
        s = self.summary
        s['corrections'] += 1
        s['accepted'] += int(row['alpha'] > 0)
        s['energy_increases'] += int(row['energy_after_J'] > row['energy_before_J'] + 1e-12)
        linear = row['linear_solve']
        s['linear_failures'] += int(not linear['true_within_tolerance'])
        s['cg_iterations_max'] = max(s['cg_iterations_max'], linear['iterations'])
        s['guard_negative_max'] = max(s['guard_negative_max'], row['near_negative_count'])
        s['guard_ambiguous_max'] = max(s['guard_ambiguous_max'], row['near_ambiguous_count'])
        if s['corrections'] % 10 == 0:
            self.flush()

    def record_native(self, row):
        self.native.append(row)
        self.summary['native_substeps'] += 1
        self.summary['native_unsafe'] += int(not row['native_post_safe'])

    def record_query(self, row):
        self.queries.append(row)
        self.summary['queries'] += 1

    def flush(self):
        try:
            for name, value in [('correction', list(self.corrections)),
                                ('warmstarts', list(self.native)),
                                ('query_reach', list(self.queries)), ('summary', self.summary)]:
                temporary = self.output / (name + '.tmp')
                temporary.write_text(json.dumps(value, indent=2), encoding='utf8')
                temporary.replace(self.output / (name + '.json'))
        except OSError as error:
            # Optional diagnostics must never discard an accepted physical frame.
            self.summary['write_error'] = str(error)
        return dict(self.summary)
