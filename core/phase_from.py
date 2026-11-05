"""Phase Sim again from a frame of an earlier Phase Sim: the rest of that run's steps, not a new plan.

A Phase Sim's frames on the timeline are its stages' frames joined (every 2nd solver step, and always the last).
Pressing Phase Sim again with the timeline on frame f -- or after pieces were put back to their placed shapes at
frame f -- goes on from there as the same run would have: the stage f falls in, from f's place in it, then the stages
after it. Before, it ran the Continue stages (gravity, then close), so pieces that wait in the first run (a sleeve
until the bodice hangs) were sewn at once.

No bpy here: the add-on (dress.py) reads the job and calls these.
"""

# a stage's first-frame steps: done once when the stage starts, already done at a frame inside it
START_OPS = ('lay', 'stack_under', 'clear_new', 'fold_turn', 'carry_to_seam')
NEVER = 10000                       # a stitch_delay start at or past this never opens in its stage (the OPEN marks)


def sample_step(i, n_samples, n_steps):
    """The solver step (0..n_steps) of drape cache sample i: every 2nd step, the last sample always the last step
    (dress.AC9_OT_SimDress._finish)."""
    i = min(max(int(i), 0), max(int(n_samples) - 1, 0))
    if i >= n_samples - 1:
        return int(n_steps)
    return min(2 * i, int(n_steps))


def locate(stage_steps, step):
    """(stage index, frame in that stage) of a step of the joined frames; stage_steps: each stage's steps (frames
    written minus its frame 0). A step on a stage's last frame is the next stage's frame 0 (the same shape); past the
    last stage, (len(stage_steps), 0): nothing of the run is left."""
    step = max(int(step), 0)
    for k, n in enumerate(stage_steps):
        if step < n:
            return k, step
        step -= n
    return len(stage_steps), 0


def plan_place(job_info, k, j):
    """The plan's (stage, frame) of a job's own (stage k, frame j): a job that itself went on from a frame keeps
    where it started in the plan (job.json 'phase_from': {'stage', 'frame'})."""
    pf = job_info.get('phase_from') or {}
    k0, j0 = int(pf.get('stage', 0)), int(pf.get('frame', 0))
    return k0 + k, (j0 + j if k == 0 else j)


def _shift(start, n, j):
    """A schedule entry starting at frame `start` over n frames, seen from frame j: (start, n) or None (done)."""
    if start >= NEVER:
        return start, n
    if j <= start:
        return start - j, n
    if j < start + n:
        return 0, start + n - j
    return None


def trim(cfg, j):
    """The stage's config to run from its frame j: the frame cap, the sewing ramp, the delayed seams, the fold ramp
    and the elastic shortening shifted by j (their gaps / angles then taken where they are at j, as the solver does
    at a stage's start); the first-frame steps (laying a band, stacking a flap, the fold drives) dropped. Returns
    (cfg, notes). The cloth starts at rest: the velocities at j are not kept."""
    if j <= 0:
        return cfg, []
    c = dict(cfg)
    notes = []
    c['frames'] = max(int(c['frames']) - j, 1)
    if c.get('stitch_ramp_frames'):
        c['stitch_ramp_frames'] = max(int(c['stitch_ramp_frames']) - j, 0)
    if c.get('stitch_delay'):
        out = []
        for g in c['stitch_delay']:
            s = _shift(int(g.get('start', 0)), int(g.get('frames', 0)), j)
            if s is not None:
                out.append(dict(g, start=s[0], frames=s[1]))
        if out:
            c['stitch_delay'] = out
        else:
            c.pop('stitch_delay')
    if c.get('fold_ramp'):
        n = int(c['fold_ramp'].get('frames', 40)) - j
        if n > 0:
            c['fold_ramp'] = dict(c['fold_ramp'], frames=n)
        else:
            c.pop('fold_ramp')
    if c.get('elastic'):
        # the shortening is of the rest shape, a function of the frame alone: the same frame numbers, moved back by
        # j (a start before the stage is fine for the solver's ramp)
        c['elastic'] = [dict(e, start=int(e.get('start', 1)) - j) for e in c['elastic']]
    for k in START_OPS:
        if k in c:
            c.pop(k)
            notes.append(k)
    return c, notes


def cut(stages, k, j, stage_frames):
    """The plan's stages [(label, cfg)] from stage k, frame j: the stages before k dropped, stage k trimmed (trim),
    each stage after starting from the one before it in this job (stage_frames(i) = the frames file of the new i-th
    stage, 0-based). The first stage starts from the job's start shape (the shape on screen). Returns (stages,
    notes)."""
    out = []
    notes = []
    for i, (label, cfg) in enumerate(stages[k:]):
        cfg = dict(cfg)
        if i == 0:
            cfg.pop('init_from', None)
            cfg.pop('init_from_pieces', None)
            cfg, dropped = trim(cfg, j)
            notes += dropped
            if j > 0:
                label = f"{label} (from frame {j})"
        else:
            cfg['init_from'] = stage_frames(i - 1)
        out.append((label, cfg))
    return out, notes
