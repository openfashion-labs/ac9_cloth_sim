# SPDX-License-Identifier: GPL-3.0-or-later
"""The simulation log (no bpy): what a Dress / Continue / Live did, in the words a user reads -- the mesh each stage
ran on, a shape carried from another mesh, stages started and ended, the result. One JSON line per entry in
<job>/log.jsonl, so the screen, the headless tests, the regression report and the AI companion read the same thing
(Fable REVIEW_fable_mesh_integration_r3 §3; user 2026-10-08: the system may change the mesh by itself, but the user
has to be able to see that it did, in a log -- not in text objects in the scene)."""
import json
import os
import time

NAME = 'log.jsonl'


def stage_frame_counts(run_log, saved_steps):
    """Completed steps in this run and its absolute end frame, independent of rolling output."""
    times = run_log.get('frame_s')
    completed = len(times) if isinstance(times, list) else max(0, int(saved_steps))
    resume = run_log.get('checkpoint_resume') or {}
    start = int(resume.get('frame', 0)) if resume.get('status') == 'restored' else 0
    return completed, start + completed


def write(job_dir, kind, text, **data):
    """Append an entry; returns the line as the user reads it ("HH:MM:SS text")."""
    t = time.strftime('%H:%M:%S')
    row = {'t': t, 'kind': kind, 'text': text}
    row.update(data)
    try:
        with open(os.path.join(job_dir, NAME), 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    except OSError:
        pass
    return f"{t} {text}"


def read(job_dir):
    """Every entry of a job's log ([] if none)."""
    p = os.path.join(job_dir, NAME)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def lines(job_dir, n=None):
    """The log as the user reads it, the last n lines (all if None)."""
    rows = [f"{r.get('t', '')} {r.get('text', '')}" for r in read(job_dir)]
    return rows if n is None else rows[-n:]
