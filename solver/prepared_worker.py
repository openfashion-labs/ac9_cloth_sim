# SPDX-License-Identifier: GPL-3.0-or-later
"""Build exactly one private Native3 job, wait once, execute once, then exit."""
import gc
import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback
from process_tree import join_owned_job
join_owned_job()
from prepared_live import Gate, write


def main(path):
    specification = {}
    gate = initial_log = None
    cleanup = []
    code = 0
    original_stdout, original_stderr = sys.stdout, sys.stderr
    def notify(phase, **fields):
        nonlocal code
        try:
            if gate is not None:
                gate.update(phase, **fields)
            elif specification.get('status'):
                write(specification['status'], dict(version=1, token=specification.get('token'),
                      generation=specification.get('generation'), pid=os.getpid(), phase=phase, **fields))
        except BaseException:
            # A missing/unwritable status target must never interrupt teardown.
            code = code or 1
    try:
        specification = json.loads(Path(path).read_text(encoding='utf8'))
        gate = Gate(specification)
        stage = Path(specification['stage'])
        write(stage / 'lifecycle.json', dict(pid=os.getpid(), ppid=os.getppid()))
        initial_log = (stage / 'stdout.txt').open('w', encoding='utf8')
        sys.stdout = sys.stderr = initial_log
        sys.argv = [str(Path(__file__).with_name('newton_vbd.py')), specification['idx'],
                    specification['avatar'], specification['config_path'], str(stage)]
        runpy.run_path(sys.argv[0], run_name='__main__', init_globals={
            '_AC9_T0': time.perf_counter(), '_AC9_CLEANUP': cleanup.append,
            '_AC9_PREPARED_GATE': gate})
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int else (0 if exc.code is None else 1)
        if gate is None or (not gate.accepted and gate.latest.get('phase') != 'canceled'):
            code = code or 1
            if gate is not None:
                notify('failed', reason=str(exc.code) if exc.code else 'Exited before prepared start')
    except BaseException as exc:
        try:traceback.print_exc()
        except BaseException:pass
        code = 1
        notify('failed', reason=str(exc))
    finally:
        try:
            for close in reversed(cleanup):
                try:
                    close()
                except BaseException as exc:
                    code = code or 1
                    notify('failed', reason='Cleanup failed: ' + str(exc))
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr
            if initial_log is not None:
                try:initial_log.close()
                except BaseException:code = code or 1
    if gate is not None and gate.accepted:
        try:
            log = json.loads((gate.canonical_stage / 'run_log.json').read_text(encoding='utf8'))
            if log.get('error'):code = code or 2
        except (OSError, ValueError):
            code = code or 2
        notify('complete' if code == 0 else 'failed', job=str(gate.canonical_stage))
    gc.collect()
    return code


if __name__ == '__main__':
    os._exit(main(sys.argv[1]))
