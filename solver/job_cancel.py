# SPDX-License-Identifier: GPL-3.0-or-later
"""Cooperative cancellation of one owned session job; never a GPU/model cache."""
from pathlib import Path
import json
import os
from lifecycle_trace import mark


class CanceledJob(Exception):
    pass


_stage = None
_identity = None


def activate(stage, token=None, generation=None):
    global _stage, _identity
    if _stage is not None:
        raise RuntimeError('An owned job cancellation scope is already active')
    _stage = Path(stage)
    _identity = dict(version=1, job_token=token, generation=generation,
                     stage=os.path.normcase(os.path.realpath(stage)))
    _phase('preparing')


def clear():
    global _stage, _identity
    _stage = None
    _identity = None


def _phase(phase):
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


def checkpoint(event):
    mark(event)
    check()
