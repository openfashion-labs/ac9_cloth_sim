# SPDX-License-Identifier: GPL-3.0-or-later
"""Small atomic progress messages for a single, uniquely named preload session."""
import json
import os

PHASES = ('imports', 'device', 'model', 'kernels')
TOTAL = len(PHASES)


def write(path, completed, phase, state='loading', error=''):
    if not path:
        return
    row = dict(version=1, completed=completed, total=TOTAL, phase=phase, state=state,
               error=str(error)[:500])
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(row, stream)
    os.replace(temporary, path)


def read(path):
    """An incomplete/invalid message cannot turn into a ready state."""
    try:
        with open(path, encoding='utf-8') as stream:
            row = json.loads(stream.read(4096))
        done = row['completed']
        if (row.get('version') != 1 or type(done) is not int or row.get('total') != TOTAL
                or not 0 <= done <= TOTAL or row.get('state') not in ('loading', 'ready', 'failed')
                or row.get('phase') not in (*PHASES, 'ready')
                or (row['state'] == 'ready' and (done != TOTAL or row['phase'] != 'ready'))):
            return None
        return row
    except (OSError, ValueError, KeyError, TypeError):
        return None
