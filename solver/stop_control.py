# SPDX-License-Identifier: GPL-3.0-or-later
"""Request a solver stop and observe it without blocking Blender's event loop."""
import os
import json
import time
from .lifecycle_trace import mark


# A solver reads the stop file after each frame (newton_vbd writes progress.txt, then looks for 'stop'). One that
# writes no new progress for this long after the stop is stuck inside a frame and never reads it: it is killed then,
# not at the 60 s deadline. The user waited the full 60 s on every stuck Layer run, and 60 s gained nothing (2026-11-01,
# experiments/claude_layer_nocross_2026-11-01; a healthy run answers a stop in about 1 s).
STUCK_MIN_S = 5.0
STUCK_FRAMES = 3.0
# a later stage's first frame comes only after the stage is set up: up to 17.6 s from one stage's end to the next
# one's loop in 378 later-stage run logs (2026-10-18..11-01), so a stop between stages waits twice that
STAGE_START_S = 40.0
# A kept Live solver preparing a job is stopped at its own checkpoints, not killed (a kill loses the warmed process),
# and Stop waited up to the 60 s deadline. One stuck in native code never reaches a checkpoint: it is killed once
# nothing it writes (its output, its setup file, the kernel cache) has changed for this long. A first run on an empty
# kernel cache went at most 10.1 s without a change, a later run 1.6 s (a coat's Live start, 2026-11-22)
PREP_QUIET_S = 30.0


def _stamp(paths):
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(out)


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _printed_progress(p, since):
    """a 'progress <f> <n>' line in the stage's stdout after byte `since`: the same frame end as progress.txt, which
    Windows may refuse to replace while the add-on reads it (newton_vbd skips that write)"""
    try:
        with open(p, 'rb') as f:
            f.seek(since)
            return b'progress ' in f.read(1 << 16)
    except OSError:
        return False


class PendingStop:
    def _session_preparing(self):
        command=getattr(self.proc,'command',{})
        if not isinstance(command,dict):return False
        if not getattr(self.proc,'is_session_job',False):return False
        for sd in self.stage_dirs:
            try:
                path=os.path.join(sd,'session_setup.json')
                if os.path.getsize(path)>4096:continue
                with open(path,encoding='utf8') as stream:row=json.load(stream)
                if not isinstance(row,dict):continue
                if (row.get('version')==1 and type(row.get('generation'))is int
                        and row.get('generation')==command.get('generation')
                        and row.get('job_token')==command.get('job_token')
                        and isinstance(command.get('job_token'),str)
                        and row.get('stage')==os.path.normcase(os.path.realpath(sd))):
                    return row.get('phase')=='preparing'
            except (OSError,ValueError,TypeError):pass
        return False

    def __init__(self, proc, stage_dirs, timeout=60, frame_s=None, min_s=STUCK_MIN_S, activity=None):
        """frame_s: how long a frame has taken in this run (None: not known yet); a frame may take that long
        before the solver reaches the stop file, so it is waited for three times over, at least min_s
        (STAGE_START_S between stages; STUCK_MIN_S before the run's first frame, when a kill loses no frame).
        activity: a callable whose value changes while a preparing session still works (PREP_QUIET_S)"""
        self.proc = proc
        self.t0 = time.monotonic()
        self.deadline = self.t0 + timeout
        self.timeout = timeout
        # A session's setup has no completed frame yet, but killing its job also
        # destroys the warmed interpreter. Cooperative checkpoints acknowledge a
        # stop; retain the existing hard deadline for genuinely unresponsive work.
        self.stage_dirs=tuple(stage_dirs)
        stage_dirs=self.stage_dirs
        self.session_setup = self._session_preparing()
        self.frame_wait_start=self.t0
        self.grace = min(timeout, max(min_s, STUCK_FRAMES * frame_s if frame_s else 0.0))
        if getattr(proc,'is_session_job',False) and not frame_s:
            self.grace=min(timeout,max(STUCK_MIN_S,self.grace))
        self._watch = [os.path.join(sd, 'progress.txt') for sd in stage_dirs]
        self._seen = [os.path.join(sd, 'stop_seen') for sd in stage_dirs]
        self._stamp0 = _stamp(self._watch)
        self._out = [(os.path.join(sd, 'stdout.txt'), _size(os.path.join(sd, 'stdout.txt'))) for sd in stage_dirs]
        self.moved = not self._watch           # nothing to watch: only the deadline
        self.activity = activity
        self._act, self._act_t = self._read_activity(None), self.t0
        self.killed = False
        self.finished = False
        self.error = None
        mark('stop_requested', child_pid=proc.pid, grace_s=round(self.grace, 2))
        for sd in stage_dirs:
            try:
                with open(os.path.join(sd, 'stop'), 'w'):
                    pass
                mark('stop_file_created', child_pid=proc.pid, stage=sd)
            except OSError:
                pass

    def _read_activity(self, fallback):
        if self.activity is None:
            return None
        try:
            return self.activity()
        except Exception:
            return fallback

    def poll(self):
        try:
            code = self.proc.poll()
        except OSError as exc:
            self.error = str(exc)
            mark('poll_failed', child_pid=self.proc.pid, error=self.error)
            return False
        if code is not None:
            if not self.finished:
                mark('session_job_stopped' if getattr(self.proc,'is_session_job',False) else 'child_exited',
                     child_pid=self.proc.pid, code=code)
                self.finished = True
            return True
        now = time.monotonic()
        preparing=self._session_preparing()
        if self.session_setup and not preparing:self.frame_wait_start=now
        self.session_setup=preparing
        if not self.moved and (any(os.path.exists(p) for p in self._seen) or _stamp(self._watch) != self._stamp0
                               or any(_printed_progress(p, n) for p, n in self._out)):
            # the solver wrote stop_seen or ended a frame after the stop: it has read it and is writing its frames,
            # given the full timeout
            self.moved = True
            mark('stop_progress_seen', child_pid=self.proc.pid, after_s=round(now - self.t0, 3))
        quiet = False
        if self.session_setup and not self.moved and self.activity is not None:
            act = self._read_activity(self._act)
            if act != self._act:
                self._act, self._act_t = act, now
            quiet = now - self._act_t >= PREP_QUIET_S
        stuck = quiet or (not self.session_setup and not self.moved and now - self.frame_wait_start >= self.grace)
        if not self.killed and (stuck or now >= self.deadline):
            if stuck:
                mark('stop_stuck', child_pid=self.proc.pid, grace_s=round(self.grace, 2))
            else:
                mark('stop_timeout', child_pid=self.proc.pid, timeout=self.timeout)
            self.killed = True
            try:
                self.proc.kill()
                mark('kill_sent', child_pid=self.proc.pid)
            except OSError as exc:
                # An exit racing the kill is already done; a denied kill retains the process owner.
                if not process_running(self.proc):
                    return self.poll()
                self.error = str(exc)
                mark('kill_failed', child_pid=self.proc.pid, error=self.error)
        return False


# Kept across importlib.reload: these processes outlive an operator or an add-on disable.
_RETIRED_STOPS = globals().get('_RETIRED_STOPS', [])


def retire(proc, stage_dirs):
    for stop in _RETIRED_STOPS:
        if stop.proc is proc:
            return stop
    stop = PendingStop(proc, stage_dirs, timeout=0)
    _RETIRED_STOPS.append(stop)
    return stop


def process_running(proc):
    try:
        return proc.poll() is None
    except OSError:
        return True


def retired_running():
    return any(process_running(stop.proc) for stop in _RETIRED_STOPS)


def reap_retired():
    for stop in tuple(_RETIRED_STOPS):
        if stop.poll():
            _RETIRED_STOPS.remove(stop)
    return 0.25 if _RETIRED_STOPS else None
