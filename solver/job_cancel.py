# SPDX-License-Identifier: GPL-3.0-or-later
"""Cooperative cancellation of one owned session job; never a GPU/model cache."""
from pathlib import Path
import json
import os
import threading
import time
from lifecycle_trace import mark


class CanceledJob(Exception):
    pass


_stage = None
_identity = None
_preparing = False
_beat = 0.0
BEAT_S = 2.0      # the add-on's stuck check (stop_control.PREP_QUIET_S, 30 s) watches session_setup.json's time


def activate(stage, token=None, generation=None):
    global _stage, _identity
    if _stage is not None:
        raise RuntimeError('An owned job cancellation scope is already active')
    _stage = Path(stage)
    _identity = dict(version=1, job_token=token, generation=generation,
                     stage=os.path.normcase(os.path.realpath(stage)))
    _phase('preparing')


def clear():
    global _stage, _identity, _preparing
    busy_end()
    _stage = None
    _identity = None
    _preparing = False


def _phase(phase):
    global _preparing, _beat
    _preparing, _beat = phase == 'preparing', time.monotonic()
    path = _stage / 'session_setup.json'
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(dict(_identity, phase=phase)), encoding='utf8')
        os.replace(temporary, path)
    finally:
        if temporary.exists():temporary.unlink()


def loop_ready():
    check()
    _phase('loop')


def check():
    stage = _stage
    if stage is not None and (stage / 'stop').exists():
        try:
            (stage / 'stop_seen').touch()
        except OSError:
            pass
        mark('session_job_cancelled', stage=str(stage))
        raise CanceledJob()
    if stage is not None and _preparing:
        _heartbeat(stage)


def _heartbeat(stage):
    """While the job prepares (a first Native 3 plan takes half a minute and writes nothing), touch
    session_setup.json every BEAT_S so the add-on sees it is still working: only its time changes, never its
    content (no replace, so a reader holding it open cannot make this fail). Every loop that runs long calls check()."""
    global _beat
    now = time.monotonic()
    if now - _beat < BEAT_S:
        return
    _beat = now
    try:
        os.utime(stage / 'session_setup.json')
    except OSError:
        pass


def checkpoint(event):
    mark(event)
    check()



_busy_stop = None


def busy_begin(event):
    """A known long step that calls nothing back while it runs: Newton's builder.finalize builds its kernels the
    first time a version runs (22-30 s seen on a coat, 0.5 s once built). A thread keeps session_setup.json's time
    moving until busy_end (or the job leaves preparing, or ends), so a real hang anywhere else still reads as stuck
    after 30 s."""
    global _busy_stop
    busy_end()
    stage = _stage
    if stage is None or not _preparing:
        return
    mark(event + '_begin')
    stop = _busy_stop = threading.Event()

    def beat():
        while not stop.wait(BEAT_S):
            if _stage is not stage or not _preparing:
                return
            try:
                os.utime(stage / 'session_setup.json')
            except OSError:
                pass

    threading.Thread(target=beat, name='ac9-prepare-beat', daemon=True).start()


def busy_end():
    global _busy_stop
    if _busy_stop is not None:
        _busy_stop.set()
        _busy_stop = None
