# SPDX-License-Identifier: GPL-3.0-or-later
"""Append-only lifecycle diagnostics, flushed before returning; independent of normal shutdown."""
import json
import os
import tempfile
import time

_DEFAULT_DIR = os.path.join(tempfile.gettempdir(), 'ac9_cloth_sim_lifecycle',
                            f'{os.getpid()}_{time.time_ns()}')
_stream = None
_stream_key = None


def directory():
    """AC9_LIFECYCLE_DIR overrides the session folder; 0 disables diagnostics."""
    return os.environ.get('AC9_LIFECYCLE_DIR', _DEFAULT_DIR)


def mark(event, **fields):
    global _stream, _stream_key
    folder = directory()
    if not folder or folder == '0':
        return
    try:
        row = dict(wall_ns=time.time_ns(), monotonic_ns=time.perf_counter_ns(),
                   pid=os.getpid(), ppid=os.getppid(), event=event, **fields)
        key = (folder, os.getpid())
        if _stream is None or _stream_key != key or _stream.closed:
            if _stream is not None:
                _stream.close()
            os.makedirs(folder, exist_ok=True)
            _stream = open(os.path.join(folder, f'{os.getpid()}.jsonl'), 'a', encoding='utf-8')
            _stream_key = key
        _stream.write(json.dumps(row, ensure_ascii=True) + '\n')
        _stream.flush()
    except (OSError, ValueError, TypeError):
        pass
