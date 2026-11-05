# SPDX-License-Identifier: GPL-3.0-or-later
"""Dress: the spec's enabled, placed pieces -> meshed, laid round their guides, sewn and draped by Newton.

Preparation runs here (pattern, triangulation, the initial shape clear of the body); the solver runs in a
separate process, one stage at a time (solver/newton_vbd.py in the Python that has newton + warp), so Blender
stays responsive. While it runs, the garment object follows the solver's latest frame; at the end every frame
is written to a PC2 point cache and played by a Mesh Cache modifier on the timeline.

Job folder: <blend folder>/AC9_ClothSim_jobs/<time>/ (or the system temp folder for an unsaved file).
"""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from .i18n import CTX, rpt, tx, wrap

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils.geometry import delaunay_2d_cdt

from . import button_view as BV
from . import zipper_view as ZV
from . import pattern_view as PV
from . import placement as PL
from . import placed_view as PLV
from . import store
from .core import attach as AT
from .core import body_axes as BA
from .core import buttons as BT
from .core import zippers as ZP
from .core import elastic as EL
from .core import fabric as FA
from .core import pattern_build
from .core import phase_from as PHF
from .core import presets
from .core import recipe
from .core import roles
from .core import seam_path as SPATH
from .core import simlog
from .core import spec as S
from .solver import arm_mask
from .solver.lifecycle_trace import mark as _trace, directory as _trace_dir
from .solver import preload_status as _preload_status
from .solver.stop_control import STAGE_START_S, STUCK_MIN_S, PendingStop, retire, retired_running, reap_retired, process_running
from . import selection as SEL

GARMENT_PREFIX = "AC9_Garment_"
COLLIDER_PROP = "ac9_collider"      # on an object: it is a static obstacle for the next Dress (an inner layer)
# on the garment: the pieces Reset with a selection put back (JSON list). The next Simulate / Live starts them from
# their placed shapes and sews them on, the rest goes on from the result (user 2026-10-14: one piece changed is no
# reason to dress the whole garment again)
RESET_PROP = "ac9_reset_pieces"
# on the garment: the timeline frame the next Phase Sim goes on from, kept when its shape was taken at that frame (a
# Reset of some pieces, Fix by Hand, a piece moved on it): the frame on screen may change since. Without it, the frame
# on screen (core/phase_from.py; user 2026-11-04: go on from the frame, as the same run would have)
FROM_FRAME_PROP = "ac9_from_frame"


def reset_pieces(g):
    """The pieces a Reset put back on the garment g ([] if none)."""
    if g is None or not g.get(RESET_PROP):
        return []
    try:
        return [str(n) for n in json.loads(g[RESET_PROP])]
    except (TypeError, ValueError):
        return []


def garment_name(spec):
    return GARMENT_PREFIX + spec['name']


def job_alive(d):
    """The job folder is there with its result (job.json, scene.idx.npz). A folder in the system temp folder (a Dress
    run before the .blend was saved) is emptied by Windows' cleanup (Storage Sense) but left, with the drape.pc2 the
    file kept open: it is lost as a folder that is gone is, and the garment goes on from its own shape
    (job_or_from_mesh). It crashed Live with job.json not found (user 2026-11-01)."""
    return (os.path.isfile(os.path.join(d, 'job.json'))
            and os.path.isfile(os.path.join(d, 'scene.idx.npz')))


def job_of(g):
    """The job folder of a garment's result: its 'ac9_job', or -- when that is gone while the result is still on the
    object (an undo can take the property back but leave the shape and its drape cache) -- the folder of the
    drape.pc2 its Mesh Cache modifier plays, which is then written back. None when there is no result. A Live after a
    finished Dress was refused "Nothing to continue" (user 2026-10-13, cause not reproduced)."""
    if g is None:
        return None
    j = g.get('ac9_job')
    if j and job_alive(str(j)):
        return str(j)
    for m in g.modifiers:
        if m.type == 'MESH_CACHE' and m.filepath:
            d = os.path.dirname(bpy.path.abspath(m.filepath))
            if os.path.exists(os.path.join(d, 'scene.idx.npz')):
                g['ac9_job'] = d
                return d
    return None


def collider_objects(context, exclude=None):
    # the other garments with a result are obstacles on their own (other_garments): not twice
    return [ob for ob in context.scene.objects if ob.type == 'MESH' and ob.get(COLLIDER_PROP) and ob != exclude
            and not ob.name.startswith(GARMENT_PREFIX)]


def job_last_frame(job):
    """The result's last shape (meters, full garment), or None: final.npy once the frames were dropped (another
    garment simulated since, drop_frames), else the last frame written (a job stopped on an anomaly has no frames
    for its later stages)."""
    p = os.path.join(job, 'final.npy')
    if os.path.exists(p):
        return np.load(p).astype(np.float64)
    try:
        n = len(json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))['stages'])
    except (OSError, ValueError, KeyError):
        n = 20
    for k in range(n, 0, -1):
        f = os.path.join(job, f"s{k}", 'frames.npy')
        if os.path.exists(f):
            return np.load(f)[-1].astype(np.float64)
    return None


def _job_info(job):
    try:
        return json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None


def _stage_steps(job, info):
    """Each stage's solver steps in the job's frames (job.json 'stage_steps', written since 2026-11-04; else the
    stages' frames files), or None."""
    if info.get('stage_steps') is not None:
        return [int(x) for x in info['stage_steps']]
    out = []
    for k in range(len(info.get('stages', []))):
        p = os.path.join(job, f"s{k + 1}", 'frames.npy')
        if not os.path.exists(p):
            break
        try:
            out.append(len(np.load(p, mmap_mode='r')) - 1)
        except (OSError, ValueError):
            return None
    return out or None


def _plan_place(job, sample, depth=0):
    """Where drape cache sample `sample` of a Phase Sim job is in its plan: (plan stage, frame in it, the plan's stage
    labels), 'end' past its last stage, or None when the job is not a Phase Sim's (a Continue, Live or Local Sim, a
    pattern edit carried over, a garment taken from the .blend) or cannot be read. A job that went on from a frame of
    another starts with that one's samples (job.json phase_from 'prefix'): those are looked up there."""
    info = _job_info(job) if job and depth < 20 else None
    if info is None or info.get('kind') in ('continue', 'live', 'local') or info.get('from'):
        return None
    pf = info.get('phase_from') or {}
    pre = int(pf.get('prefix', 0))
    if sample < pre:
        return _plan_place(pf.get('job'), sample, depth + 1)
    steps = _stage_steps(job, info)
    n = info.get('frames_cached')
    if not steps or not n:
        return None
    k, j = PHF.locate(steps, PHF.sample_step(sample - pre, int(n) - pre, sum(steps)))
    if k >= len(info.get('stages', [])):
        return 'end'
    k, j = PHF.plan_place(info, k, j)
    return k, j, list(info.get('plan_stages') or info['stages'])


def _drape(g):
    return next((m for m in g.modifiers if m.type == 'MESH_CACHE' and m.filepath), None) if g is not None else None


def phase_point(context, g):
    """Where the next Phase Sim on garment g goes on from (user 2026-11-04: from the frame, as the same run would
    have; core/phase_from.py). The frame: the one kept on the garment (FROM_FRAME_PROP: its shape was taken there),
    else the one on screen while its frames play. Returns None (go on as Continue: no Phase Sim's frames to go on
    from, or past their end), 'start' (its first frame with nothing changed by hand on the garment: the run again
    from the placement) or {'stage', 'frame' (in the plan), 'plan' (its stage labels), 'timeline_frame', 'sample'
    (the drape sample of that frame), 'job'}."""
    job = job_of(g)
    if not job:
        return None
    tf = g.get(FROM_FRAME_PROP)
    if tf is None:
        if frozen(g):
            return None
        tf = context.scene.frame_current
    m = _drape(g)
    sample = max(int(tf) - int(m.frame_start if m is not None else 1), 0)
    pp = _plan_place(job, sample)
    if pp is None or pp == 'end':
        return None
    k, j, plan = pp
    if k == 0 and j == 0 and not frozen(g):
        return 'start'
    return {'stage': k, 'frame': j, 'plan': plan, 'timeline_frame': int(tf), 'sample': sample, 'job': job}


_SPECS_CACHE = {}       # Text name -> ((pointer, hash of its text), parsed spec or None): garment_specs


def garment_specs():
    """{garment object name: spec} for every garment spec Text in the file (parsed: call outside draw). Each Text is
    parsed again only when its text changed (every prepare calls this, through other_garments). The specs are shared
    with the next call: read them, do not change them."""
    out = {}
    seen = set()
    for t in bpy.data.texts:
        if not t.name.startswith(store.TEXT_NAME):
            continue
        seen.add(t.name)
        s = t.as_string()
        k = (t.as_pointer(), hash(s))
        hit = _SPECS_CACHE.get(t.name)
        if hit is not None and hit[0] == k:
            sp = hit[1]
        else:
            try:
                sp = S.loads(s)
            except Exception:
                sp = None
            _SPECS_CACHE[t.name] = (k, sp)
        if sp is not None:
            out[garment_name(sp)] = sp
    for n in [n for n in _SPECS_CACHE if n not in seen]:
        del _SPECS_CACHE[n]
    return out


def _frames_of(g, job):
    """(the garment's Mesh Cache modifiers, its job's frame files): what drop_frames drops (both empty: nothing)."""
    caches = [m for m in g.modifiers if m.type == 'MESH_CACHE']
    files = [os.path.join(job, n) for n in sorted(os.listdir(job)) if n == 'drape.pc2']
    for n in sorted(os.listdir(job)):
        if n.startswith('s') and n[1:].isdigit():
            files += [os.path.join(job, n, f) for f in ('frames.npy', 'live.npy') if os.path.exists(os.path.join(job, n, f))]
    return caches, files


# files of other garments' frames dropped as a run started (other_garments): deleted once the run wrote a frame.
# A run that wrote none is undone (Ctrl+Z state), and the garments must find their frames again (2026-11-01)
_DROP_LATER = []


def drop_later_done(keep):
    """The run ended: delete the frames other_garments dropped (keep False) or forget them (keep True: undone)."""
    files, _DROP_LATER[:] = list(_DROP_LATER), []
    if keep:
        return
    for f in files:
        try:
            os.remove(f)
        except OSError:
            pass


def drop_frames(context, g, later=False):
    """Another garment is simulated: this one keeps its last shape only (user 2026-10-13; DECISIONS
    cloth-sim-other-garment-drops-frames-2026-10-13). The shape on screen at its end -- the last frame, or the hand
    edit after Fix by Hand -- goes into the mesh and into the job's final.npy (with final.json: which stage it came
    from); then the stages' frames.npy / live.npy and drape.pc2 are deleted and the Mesh Cache removed. The job's
    pattern, mesh and settings stay, so the garment can go on later (Simulate from final.npy). Returns the number of
    files deleted (0: nothing to drop)."""
    job = job_of(g)
    if not job:
        return 0
    caches, files = _frames_of(g, job)
    files = [f for f in files if f not in _DROP_LATER]
    if not caches and not files:
        return 0
    playing = any(m.show_viewport for m in caches)
    X = job_last_frame(job) if playing else None
    if X is not None and len(X) != len(g.data.vertices):
        X = None
    if X is None:
        X = shown_shape(context, g) * 0.001 if not playing else None
    if X is None:
        return 0
    if g.mode == 'EDIT':
        g.update_from_editmode()
    for m in caches:
        g.modifiers.remove(m)
    g.data.vertices.foreach_set('co', np.asarray(X, np.float32).ravel())
    g.data.update()
    g.pop('ac9_edit_frame', None)
    stage = 0
    for f in files:
        b = os.path.basename(os.path.dirname(f))
        if os.path.basename(f) == 'frames.npy' and b[1:].isdigit():
            stage = max(stage, int(b[1:]))
    np.save(os.path.join(job, 'final.npy'), np.asarray(X, np.float32))
    json.dump({'stage': stage, 'dropped': time.strftime('%Y-%m-%d %H:%M:%S')},
              open(os.path.join(job, 'final.json'), 'w', encoding='utf-8'))
    n = 0
    for f in files:
        if later:
            _DROP_LATER.append(f)
            n += 1
            continue
        try:
            os.remove(f)
            n += 1
        except OSError:
            pass
    _RESULT_CACHE.pop(os.path.join(job, 'job.json'), None)
    return n


def other_garments(context, spec):
    """The other garments with a result, as obstacles (P1 #9 step A, user 2026-10-13: automatic): the
    shape each shows (its frames dropped first, drop_frames), without the triangles of its pieces that are Off
    (Pattern / All). Returns ([(name, co mm, tri)], report lines)."""
    me_name = garment_name(spec)
    specs = garment_specs()
    out, rep = [], []
    for ob in context.scene.objects:
        if ob.type != 'MESH' or not ob.name.startswith(GARMENT_PREFIX) or ob.name == me_name or not job_of(ob):
            continue
        sp = specs.get(ob.name)
        names = json.loads(ob.get('ac9_piece_names', '[]'))
        off = {p['name'] for p in (sp or {}).get('pieces', []) if S.piece_state(p) in ('off_pattern', 'off_all')}
        if names and off >= set(names):
            rep.append(f"  {ob.name[len(GARMENT_PREFIX):]}: all its pieces are off, not an obstacle")
            continue
        nd = drop_frames(context, ob, later=True)
        if nd:
            rep.append(f"  {ob.name[len(GARMENT_PREFIX):]}: its frames dropped, the last shape kept ({nd} files)")
        me = ob.data
        me.calc_loop_triangles()
        nv = len(me.vertices)
        co = np.empty(nv * 3, np.float64)
        me.vertices.foreach_get('co', co)
        M = np.array(ob.matrix_world)
        co = (np.c_[co.reshape(-1, 3), np.ones(nv)] @ M.T)[:, :3] * 1000.0
        tri = np.empty(len(me.loop_triangles) * 3, np.int64)
        me.loop_triangles.foreach_get('vertices', tri)
        tri = tri.reshape(-1, 3)
        pa = me.attributes.get('ac9_piece')
        if off and pa is not None and names:
            fpc = np.empty(len(me.polygons), np.int64)
            pa.data.foreach_get('value', fpc)
            poly = np.empty(len(me.loop_triangles), np.int64)
            me.loop_triangles.foreach_get('polygon_index', poly)
            offi = [i for i, n in enumerate(names) if n in off]
            tri = tri[~np.isin(fpc[poly], offi)]
        # its pattern changed after its result: it is still an obstacle in the old shape (not stopped, said)
        stale = False
        try:
            js = json.load(open(os.path.join(job_of(ob), 'ac9_spec.json'), encoding='utf-8'))
            edges = lambda s_: {p['name']: [e['pts'] for e in p['edges']] for p in s_['pieces']
                                if p.get('enabled', True)}
            stale = sp is not None and edges(js) != edges(sp)
        except (OSError, ValueError, KeyError):
            pass
        rep.append(f"  obstacle {ob.name[len(GARMENT_PREFIX):]}: {len(tri)} triangles"
                   + (f" ({len(off & set(names))} piece(s) off left out)" if off & set(names) else "")
                   + (" -- its pattern changed after its result (old shape)" if stale else ""))
        out.append((ob.name, co, tri))
    return out, rep
MARGIN = 8.0          # mm: every vertex starts at least this far outside the body
RING_FRACTION = 0.9   # a piece that goes this far round its guide is a closed band
RING_GAP = 6.0        # mm left open where a band's ends meet (the seam closes it)
SIDE_GAP = 3.0        # mm: a piece on a leg guide stays on its own side of x = 0
SOLVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solver', 'newton_vbd.py')
RUN_STAGES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solver', 'run_stages.py')


def addon_prefs(context):
    a = context.preferences.addons.get(__package__)
    return a.preferences if a else None


SOLVER_MODULES = ('warp', 'newton', 'scipy')


def newton_python(context):
    """The Python that runs the solver: the preference, else AC9_CLOTH_SIM_PYTHON, else the development
    checkout's environment next to the add-on folder, else Blender's own Python when the release's bundled wheels
    are installed (the release ZIP: warp, newton and scipy in the extensions' site-packages)."""
    prefs = addon_prefs(context)
    for p in ((prefs.newton_python if prefs else ''), os.environ.get('AC9_CLOTH_SIM_PYTHON', ''),
              os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'sim_spike', 'newton_env', 'Scripts',
                           'python.exe')):
        if p and os.path.exists(bpy.path.abspath(p)):
            return os.path.normpath(bpy.path.abspath(p))
    if solver_path() is not None and os.path.exists(sys.executable):
        return sys.executable
    return None


def solver_path():
    """The folders holding the bundled solver modules in Blender (for the solver process's PYTHONPATH: Blender's
    Python started on its own does not see the extensions' site-packages), or None when one is missing."""
    import importlib.util
    dirs = []
    for m in SOLVER_MODULES:
        s = importlib.util.find_spec(m)
        if s is None or not s.origin:
            return None
        d = os.path.dirname(os.path.dirname(s.origin))
        if d not in dirs:
            dirs.append(d)
    return dirs


def solver_process_env(python, solver_env):
    """The solver process's environment: the job's solver_env over Blender's, the bundled wheels for Blender's own
    Python (also what the waiting process is started with, _standby_wake)."""
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    env.setdefault('AC9_LIFECYCLE_DIR', _trace_dir())
    # the settings set's solver environment (core/presets.py solver_env): the experiment's softer compression
    # (0.3, set for the jacket and shirt) goes with the sleeved recipe only; the pants' clean runs used the
    # solver's default
    env.pop('NEWTON_VBD_COMPRESSION_SCALE', None)       # only as the job's settings say (headless: from_env)
    env.update(solver_env)
    if os.path.normcase(python) == os.path.normcase(sys.executable):
        # Blender's own Python (the release): the bundled wheels' folders first
        env['PYTHONPATH'] = os.pathsep.join(solver_path() + [x for x in [env.get('PYTHONPATH')] if x])
    env['AC9_DRESS'] = '1'          # the stage-start check warns and goes on here (solver/start_check.py)
    return env


# ---------------------------------------------------------------- preparation

def triangulate(pat):
    """Constrained Delaunay per piece (keeps the input vertex order; fails if the CDT adds or merges vertices)."""
    tris, bad = [], []
    for i in range(len(pat['names'])):
        V, nb, C = pat[f"V{i}"], int(pat['nb'][i]), pat[f"C{i}"]
        verts = [Vector((float(x), float(y))) for x, y in V]
        vo, eo, fo, ov, oe, of = delaunay_2d_cdt(verts, [tuple(map(int, e)) for e in C], [list(range(nb))], 1, 1e-6,
                                                 True)
        ok = len(vo) == len(V) and all(len(o) == 1 for o in ov)
        remap = np.full(len(vo), -1, np.int64)
        for j, o in enumerate(ov):
            if len(o) == 1:
                remap[j] = o[0]
        T = np.array([[remap[a] for a in f] for f in fo if len(f) == 3], np.int64)
        T = pattern_build.drop_hole_tris(V, T, pattern_build.hole_rings(pat, i))   # no cloth in a hole (spec.holes)
        a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        ar = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
        T[ar < 0] = T[ar < 0][:, [0, 2, 1]]
        used = np.zeros(len(V), bool)
        used[T.ravel()] = True
        if not ok or (T < 0).any() or not used.all():
            bad.append(str(pat['names'][i]))
        tris.append(T)
    # a mirror-meshed twin takes its pair's triangles, turned over (its layout is the pair's mirror image)
    ts = pat.get('twin_src')
    if ts is not None:
        for i, j in enumerate(np.asarray(ts)):
            if j >= 0:
                tris[i] = tris[int(j)][:, [0, 2, 1]].copy()
                nm = str(pat['names'][i])
                if nm in bad and str(pat['names'][int(j)]) not in bad:
                    bad.remove(nm)
    return tris, bad


PRE_AREA = 0.15      # a part placed sewn to its partner is at most this share of the partner's area
MAX_TURN = 0.95       # a piece that is not a band never goes more than this far round (it would overlap itself)


def _min_radius(P):
    """The radius at which the piece's width is MAX_TURN of a full turn (a 296 mm collar on a 43 mm neck guide
    went round 1.1 times and its triangles collapsed onto each other, 2026-09-28)."""
    return float(np.ptp(np.asarray(P, float)[:, 0])) / (2 * np.pi * MAX_TURN)


def _wrap(g, P, anchor, theta, h, r, tilt=0.0):
    """Isometric wrap round guide g at radius r: layout +x counter-clockwise, +y along the axis; tilted tilt degrees
    about the line round the guide through the anchor (body_axes.tilted; not isometric then)."""
    r = max(r, _min_radius(P))
    Q = np.asarray(P, float) - np.asarray(anchor, float)
    th = np.radians(theta) + Q[:, 0] / r
    Rm = np.asarray(g['R'], float)
    rad, up = BA.tilted(Q, r, tilt)
    rad = np.broadcast_to(rad, th.shape)
    return (np.asarray(g['o'], float)[None] + rad[:, None] * (np.cos(th)[:, None] * Rm[0] + np.sin(th)[:, None] * Rm[1])
            + (h + up)[:, None] * Rm[2])


def _ring_wrap(g, P, anchor, theta, h, width, tilt=0.0, radius=None):
    """A band laid round once, stretched to close (its rest shape stays the flat pattern); tilted as _wrap about a
    ring of `radius` (default its own length's)."""
    Q = np.asarray(P, float) - np.asarray(anchor, float)
    r = (width + RING_GAP) / (2 * np.pi)
    th = np.radians(theta) + Q[:, 0] * (2 * np.pi * (1 - RING_GAP / (width + RING_GAP))) / width
    Rm = np.asarray(g['R'], float)
    rad, up = BA.tilted(Q, r if radius is None else radius, tilt)
    rad = np.broadcast_to(rad, th.shape)
    return (np.asarray(g['o'], float)[None] + rad[:, None] * (np.cos(th)[:, None] * Rm[0] + np.sin(th)[:, None] * Rm[1])
            + (h + up)[:, None] * Rm[2]), r


def _own_side(X, guide_name, cx=0.0):
    """A piece on a leg guide stays on its leg's side of the center plane. The two leg cylinders overlap at the
    hips (centers +-66 mm, radius ~115 mm once clear of the body), so the front panels crossed at the center
    front and were pulled into the body (784 vertices inside, pants, 2026-09-28); the earlier experiment kept
    leg pieces to their side for the same reason."""
    if not guide_name.startswith('leg'):
        return X
    sgn = 1.0 if guide_name.endswith('L') else -1.0
    X = X.copy()
    X[:, 0] = cx + sgn * np.maximum(sgn * (X[:, 0] - cx), SIDE_GAP)
    return X


def _clearance(bvh, X_mm, skip=None):
    """Signed distance (mm) of each point to the body: < 0 inside (by the nearest face's normal). skip: faces
    (bool per BVH face) that do not count -- a point whose nearest face is one of them is clear. With the BVH kept
    (_body_kept) the same points are answered from memory: every press of a placed-shape tool makes the start shapes
    again, the pieces it did not move searching the same radii (310 of 670 ms on the pants, prepare_cache t4)."""
    memo = _BODY_KEEP['clear'] if bvh is _BODY_KEEP['bvh'] else None
    if memo is not None:
        X_mm = np.ascontiguousarray(X_mm, float)
        k = (None if skip is None else _BODY_KEEP['skip_key'](skip), X_mm.shape,
             hashlib.sha1(X_mm.tobytes()).digest())     # the points' digest: the bytes themselves held 1.6 MB of keys (pants)
        if k in memo:
            return memo[k].copy()
    out = _clearance_now(bvh, X_mm, skip)
    if memo is not None:
        if len(memo) > 200:                 # one prepare asks 70 (pants) / 30 (jacket); the old answers go with a move
            memo.clear()
        memo[k] = out.copy()
    return out


def _clearance_now(bvh, X_mm, skip):
    out = np.empty(len(X_mm))
    for k, p in enumerate(X_mm * 0.001):
        loc, nrm, fi, d = bvh.find_nearest(Vector(p))
        if loc is None or (skip is not None and skip[fi]):
            out[k] = 1e9
            continue
        s = (Vector(p) - loc).dot(nrm)
        out[k] = d * 1000.0 * (1 if s >= 0 else -1)
    return out


LAYER = 8.0           # mm between pieces that lie on top of each other round one guide


def _girth(body, guide_name, z):
    """Convex (tape-measure) girth of the body section the guide wraps, at height z (mm); None if none."""
    from mathutils.geometry import convex_hull_2d
    if guide_name in ('torso', 'neck'):
        P = body.torso(z)
    else:
        # bands round a leg or an arm (hems, cuffs) are not detected yet: at the top of a leg guide the section
        # is a small sliver at the crotch (35 mm girth measured) and a panel was taken for a band
        return None
    if P is None or len(P) < 3:
        return None
    H = P[convex_hull_2d([tuple(q) for q in P])]
    return float(np.linalg.norm(np.roll(H, -1, 0) - H, axis=1).sum())


def radius_profile(body, guide_name, g, step=10.0, z_armpit=None):
    """Body radius about a vertical guide's axis per height: (z world mm, r mm), the largest distance from the
    axis of the body section this guide wraps (legs: that leg; above the crotch its side of the hips; torso and
    neck: the center loops, arms left out)."""
    o = np.asarray(g['o'], float)
    arm_x = 0.12 * (body.z1 - body.z0)
    zs = np.arange(o[2] + g['h0'] - 40.0, o[2] + g['h1'] + 40.0, step)
    rs = np.zeros(len(zs))
    sgn = 1 if guide_name.endswith('L') else -1
    for k, z in enumerate(zs):
        if z <= body.z0 or z >= body.z1:
            continue
        L = [P for P in body.loops(z) if abs(P[:, 0].mean()) < arm_x]
        if not L:
            continue
        P = np.vstack(L)
        if guide_name.startswith('leg'):
            P = P[sgn * P[:, 0] > 0]
            if not len(P):
                continue
        rs[k] = np.linalg.norm(P - o[:2], axis=1).max()
    # between sections and where none was found: the neighbors' largest
    good = rs > 0
    if good.any():
        rs = np.interp(zs, zs[good], rs[good])
    rs = np.maximum.reduce([rs, np.r_[rs[1:], rs[-1:]], np.r_[rs[:1], rs[:-1]]])
    if guide_name == 'torso' and z_armpit is not None:
        # above the armpit the section takes in the shoulders and the tops of the arms: the rows went out wide
        # there, the front and back met far apart over the shoulders and were pulled through them when sewn
        # (115 vertices inside a shirt at the shoulders, 2026-09-28)
        r_ap = float(np.interp(z_armpit, zs, rs))
        rs = np.where(zs > z_armpit, np.minimum(rs, r_ap), rs)
    return zs, rs


def _span(V, anchor, theta, h, r, tilt=0.0):
    """(theta lo, theta hi, h lo, h hi) the wrapped piece covers."""
    Q = V - np.asarray(anchor, float)
    up = BA.tilted(Q, r, tilt)[1]
    return (theta + np.degrees(Q[:, 0].min() / r), theta + np.degrees(Q[:, 0].max() / r),
            h + up.min(), h + up.max())


def _overlap(a, b):
    """Two spans overlap in angle (mod 360) and in height."""
    if a[3] <= b[2] or b[3] <= a[2]:
        return False
    ca, wa = 0.5 * (a[0] + a[1]), 0.5 * (a[1] - a[0])
    cb, wb = 0.5 * (b[0] + b[1]), 0.5 * (b[1] - b[0])
    d = abs((ca - cb + 180.0) % 360.0 - 180.0)
    return d < wa + wb


SHORT_BAND = "  short band "   # a report line's start: a band widened past its length to clear the body (placement.py)
IN_BODY = "  band in body "  # ... a band at a Ring Size set by hand that is in the body
RING_USED = {}                  # band piece -> (its ring as a share of its own length's, Auto): AC9 Piece's Ring Size
                                # shows it (the last shapes made, initial_shape)
CLOSURE_LAST = {}               # band -> (its closure's end on top or None, said by what, the seam id) (last shapes made)
IN_BODY_LAST = {}               # band -> its vertices in the body at a Ring Size set by hand (the last shapes made)


def _posed(X, pose):
    """X (mm, world, as made) moved by a placed object's pose (placed_view.pose_matrix about the middle of X)."""
    from mathutils import Euler
    org = 0.5 * (X.min(0) + X.max(0))
    R = np.array(Euler(np.radians(pose['rot']).tolist(), 'XYZ').to_matrix())
    return org + np.asarray(pose['loc'], float) + (X - org) @ R.T
BAND_LIFT = 0.0      # mm a band that meets the body may move up its guide (5 mm steps) before it is made wider: none
                     # since 2026-10-25 (a waistband went 25 mm up from where it was placed; the user's rule: nothing
                     # moves a piece by itself, DECISIONS cloth-sim-no-auto-placement-2026-10-25). It is widened, red


def _layers(names, pl, seams):
    """{piece: layer} round each guide, inside out: 0 = sewn to a piece on another guide (a collar stand to the
    bodice, a waistband to the legs), +1 per seam further along pieces on the same guide; unreached pieces last."""
    nb = {n: set() for n in names}
    for sm in seams:
        a, b = sm['a']['piece'], sm['b']['piece']
        if a != b:
            nb[a].add(b)
            nb[b].add(a)
    guide = {n: pl[n]['guide'] for n in names}
    lay = {n: 0 for n in names if any(guide[m] != guide[n] for m in nb[n])}
    front = list(lay)
    while front:
        nxt = []
        for n in front:
            for m in nb[n]:
                if m not in lay and guide[m] == guide[n]:
                    lay[m] = lay[n] + 1
                    nxt.append(m)
        front = nxt
    return {n: lay.get(n, 99) for n in names}


RING_SIDE = 0.3       # a seam joins two pieces side by side when it runs up at least this share of the piece's height
RING_GIRTH = 0.8      # ... and the pieces so joined go round at least this share of the body's girth


def _auto_ring(names, pl, pat, body, guide='torso', closures=()):
    """Pieces round the guide sewn side by side into a ring (each piece's left side sewn to one piece and its right
    side to another, round and back -- a front and a back sewn at both sides is a ring of two -- or open where a
    garment opens, as a jacket's fronts), the walk from the widest piece that has a side seam; [] if none. The
    caller takes it when the pieces go round most of the body. A seam joins two pieces 'side by side' when its
    stitches on a piece run up RING_SIDE of its height (a shoulder or yoke seam runs across and does not count);
    the side is the half of the piece its stitches are on (layouts of mirrored twins are turned over, so a walk
    takes the side it did not come in by). The pieces of a front closure (spec['closures']) overlap where they
    button and are not side by side: they are left out (a shirt's two plackets, laid side by side in the ring,
    crossed each other 968-995 times, 2026-10-08); they go on top of the fronts as any other piece. The jacket bodice the screen's Dress laid without it rode up over the shoulders and came off once
    the pieces were wrapped on one cylinder (2026-10-08); laid as a ring, p50 4.9 mm to the reference."""
    st = pat['stitch']
    shut = [set(c) for c in closures]
    shut_any = set().union(*shut) if shut else set()
    cand = [i for i, n in enumerate(names) if pl[n]['guide'] == guide and n not in shut_any]
    side = {i: {} for i in cand}                 # piece -> {'L' / 'R': partner}
    for i in cand:
        V = pat[f"V{i}"]
        Vb = V[:int(pat['nb'][i])]
        h_ = float(np.ptp(Vb[:, 1]))
        xm = 0.5 * (Vb[:, 0].min() + Vb[:, 0].max())
        for j in cand:
            if j == i or {names[i], names[j]} in shut:
                continue
            k = np.r_[st[(st[:, 0] == i) & (st[:, 2] == j), 1], st[(st[:, 2] == i) & (st[:, 0] == j), 3]]
            if not len(k):
                continue
            P = V[k]
            for sd, m in (('L', P[:, 0] < xm), ('R', P[:, 0] >= xm)):
                if m.sum() >= 2 and np.ptp(P[m, 1]) >= RING_SIDE * h_ and np.ptp(P[m, 0]) < np.ptp(P[m, 1]):
                    side[i].setdefault(sd, j)

    def walk(start, out):
        """The pieces met going out of start's side `out`, side by side, until an end or back at start."""
        seq, cur = [], start
        while True:
            nxt = side[cur].get(out)
            if nxt is None or nxt == start or nxt in seq:
                return seq, nxt == start
            came = next((sd for sd, j_ in side[nxt].items() if j_ == cur), None)
            if came is None:
                return seq, False
            seq.append(nxt)
            out, cur = ('R' if came == 'L' else 'L'), nxt
    for start in sorted((i for i in cand if side[i]), key=lambda i: -float(np.ptp(pat[f"V{i}"][:int(pat['nb'][i]), 0]))):
        right, closed = walk(start, 'R')
        left = [] if closed else walk(start, 'L')[0]
        ring = left[::-1] + [start] + right
        if len(ring) < 2:
            continue
        wid = sum(float(np.ptp(pat[f"V{i}"][:int(pat['nb'][i]), 0])) for i in ring)
        z = float(np.mean([pl[names[i]]['h'] for i in ring]))
        return [names[i] for i in ring], wid, z
    return [], 0.0, 0.0


def initial_shape(spec, pat, body_bvh, body, arm_faces=None, cs=None):
    """Per piece (mm, world): wrapped round its guide at its placement, pushed out until MARGIN clear.

    Measured on the pants (2026-09-28, against the experiment that dressed them cleanly): laying every piece at
    the guide's radius left 1505 crossing triangle pairs at the start (experiment 141) and the garment ended with
    ~800 vertices inside the body. Two rules from that experiment fix the start:
      - bands: a piece as long as the body's girth where it sits goes round once, stretched to close, and is
        sewn first (the waistband is 420 mm on a 143 mm guide: the guide's radius is the hips with the thighs)
      - layers: round one guide, inner pieces first; a piece that overlaps an earlier one (a pocket on a panel)
        starts LAYER mm outside it
    Since 2026-10-25 (DECISIONS cloth-sim-no-auto-placement-2026-10-25) none of the guesses below moves a piece from
    where it was placed: no stacking LAYER mm outside (an overlap is said, red), no lifting a band, no ring found by
    itself (only rings named). What stays: out of the body as a whole, a band as a ring, the leg's side, the twins.
    The shirt's neck (2026-09-29, Fable review; the experiment's placement dressed it cleanly) added three:
      - only a piece that is sewn round is a band -- shut by its own seam, or sewn along most of its length to
        other pieces (a collar stand on the neckline, a collar on the stand): the tie's neck loop (286 mm, sewn
        only to the tie) was stretched into a 118 mm ring; the experiment laid it as an open arc
      - inner first by the seams (or the placement's own 'layer'), not by area: by area the collar went innermost and the collar stand and the tie
        loop outside it (collar 99 < stand 110 < loop 118 mm), the reverse of the garment; they tangled shrinking
        through each other. Layer 0 = sewn to a piece on another guide (the stand to the bodice), then one more
        per seam away on the same guide (the collar on the stand); ties by the natural radius on the neck
      - a band that meets the body moves up its guide (5 mm steps, up to BAND_LIFT) before it is made wider:
        widened, the collar stand's lower edge over the shoulders took it to 111 mm (natural 36, the
        experiment 63) and the collar frilled; lifted, stand 36 mm up 30, collar 52 up 20 (the experiment lifted
        its collar 20 mm), and the shirt dressed with 2 vertices inside, 732 / 797 seams closed, 3.66 mm from the reference
        (p50). A clearance of 3 mm instead of 8 without lifting did not help (90 inside).
    A jacket (2026-09-29): its fronts and side panels reach round under the armpit, between the chest and the
    A-pose upper arm (the reference drapes them there); wrapped round the torso they meet the arm and were pushed out as a
    whole to 284 mm on a 143 mm guide. The sleeved stages sew the body and collar with the arms out, so for those
    pieces the arms do not count (arm_faces: a nearest face on an arm is clear). Taking the arm faces out of the
    body instead read the open shoulder tops from below as inside (side panels stuck at -133 mm).
    Swapping one group at a time for the experiment's start: the neck alone took 105 inside -> 12, the body
    (fronts, backs, trims) -> 45, the sleeves -> 116 (no change).
    With the experiment's start the add-on's own stages and solver dressed the shirt with 0 vertices inside the
    body and 739 / 797 seams closed (vs 85 and 667 with the old placement): the placement was the whole difference.
    """
    guides, pl = spec['guides'], spec['placement']
    names = [str(n) for n in pat['names']]
    X, rings, rep = [None] * len(names), [], []
    short = {}           # band -> how far it starts widened past its length to clear the body (placement.show_start)
    over = {}            # piece -> the pieces placed before it on its guide that it starts through (placement.show_start)
    short_in = IN_BODY_LAST                       # band -> its vertices in the body at a Ring Size set by hand (show_start)
    short_in.clear()
    CLOSURE_LAST.clear()
    from . import closure as CL
    cx_ = PLV.center_x(spec)                      # the body's center plane (the leg pieces keep to their side of it)

    def area(i):
        V = pat[f"V{i}"][:int(pat['nb'][i])]
        return abs(0.5 * float((V[:, 0] * np.roll(V[:, 1], -1) - np.roll(V[:, 0], -1) * V[:, 1]).sum()))
    idx = {n: i for i, n in enumerate(names)}
    seams = [sm for sm in spec.get('seams', []) if sm.get('enabled', True)
             and sm['a']['piece'] in idx and sm['b']['piece'] in idx]
    st = pat['stitch']
    layer = _layers(names, pl, seams)
    for n in names:
        if pl[n].get('layer') is not None:
            layer[n] = int(pl[n]['layer'])

    placed = {}                                   # guide -> [(span, r)]
    arm_shift = {}                                # arm guide -> its pieces go round the whole arm's line (bool)
    sleeved = arm_faces is not None and (any(pl[n]['guide'].startswith('arm') for n in names)
                                         or spec.get('recipe') == 'sleeved')

    def laid(i):
        """Piece i's layout points (its Tilt is laid by _wrap / _ring_wrap: it bends the piece out of the cylinder)."""
        return pat[f"V{i}"]

    def order(i):
        n = names[i]
        w = float(np.ptp(laid(i)[:int(pat['nb'][i]), 0]))
        # a piece the user moved outside (Move Outside) after the pieces it goes over
        return (int(pl[n].get('outside', 0)), layer[n], w if pl[n]['guide'] == 'neck' else -area(i))
    cs = cs or presets.DEFAULTS                 # the settings set (core/presets.py)
    # spec['ring_pieces'] (or the settings set's ring_pieces, for tests; was env AC9_SIM_RING) = pieces on one guide that are sewn side by
    # side into a closed ring (a jacket's fronts, side panels and backs): laid side by side round a common cylinder, in
    # their current angular order, gaps shared out, at the radius the ring needs (or the guide's + MARGIN), never
    # stacked on each other (REVIEW_fable_G4_r8 §3: a side panel judged 'over' the fronts and backs went outside them
    # and folded in half, 145 -> 55 mm; laid as a ring the jacket bodice ended within 15 mm of the reference in 3 of 3 runs,
    # foundation_2026-09-30/RESULT_G3 note 14). Only when named: found automatically (_auto_ring) it moved the pieces
    # away from where Place left them -- a pair of trouser fronts placed before their backs went to the sides whatever
    # angle they were released at (user 2026-10-25, DECISIONS cloth-sim-no-auto-placement-2026-10-25)
    ring = [n for n in (spec.get('ring_pieces') or cs['ring_pieces']) if n in pl]
    ring_r = None
    if ring:
        gname = pl[ring[0]]['guide']
        wid = {n: float(np.ptp(laid(idx[n])[:int(pat['nb'][idx[n]]), 0])) for n in ring}
        ring_r = max(guides[gname]['r'] + MARGIN, (sum(wid.values()) + RING_GAP * len(ring)) / (2 * np.pi))
        spare = 2 * np.pi * ring_r - sum(wid.values())
        gap = spare / len(ring)
        seq = sorted(ring, key=lambda n: pl[n]['theta'] % 360.0)
        t = np.radians(pl[seq[0]]['theta']) - 0.5 * wid[seq[0]] / ring_r
        new_t = {}
        for n in seq:
            new_t[n] = t + 0.5 * wid[n] / ring_r
            t += (wid[n] + gap) / ring_r
        # turn the whole ring so the pieces stay, on average, where they were placed (circular mean of the shifts)
        d_ = [np.radians(pl[n]['theta']) - new_t[n] for n in seq]
        rot = float(np.arctan2(np.mean(np.sin(d_)), np.mean(np.cos(d_))))
        for n in seq:
            pl[n] = dict(pl[n], theta=float(np.degrees(new_t[n] + rot)))
        rep.append(f"  ring {', '.join(seq)}: r {ring_r:.1f} mm, gaps {gap:.1f} mm")
    # the pieces round one arm or leg wider than it goes round: all of them start round it side by side once, at a
    # larger radius (core/body_axes.wrap_radii, what the Placement overlay draws; user 2026-10-15)
    wr = BA.wrap_radii(guides, {n: (pl[n]['guide'], *map(float, np.ptp(laid(idx[n])[:int(pat['nb'][idx[n]])], axis=0)))
                                for n in names}, roles.with_role(spec, roles.ARM_PANEL_ROLES))
    bands = roles.with_role(spec, roles.BAND_ROLES)
    for i in sorted(range(len(names)), key=order):
        name = names[i]
        p = pl[name]
        g = guides[p['guide']]
        V = laid(i)
        Vb = V[:int(pat['nb'][i])]
        width = float(np.ptp(Vb[:, 0]))
        skip = arm_faces if sleeved and not p['guide'].startswith('arm') else None
        z_world = float(np.asarray(g['o'], float)[2] + p['h'])
        girth = _girth(body, p['guide'], z_world)
        # a band (a ring of its own length) is a piece given a band role on a guide the body's girth is measured
        # round (torso, neck); its shape no longer decides it (it was: long and low, sewn round -- user 2026-11-02,
        # DECISIONS cloth-sim-no-auto-judgment-default-rules-2026-11-02)
        band = girth is not None and name in bands
        # a band closed by a seam of two lines apart (a stand's buttoned ends) goes round from line to line, its ends'
        # flaps on round over each other, the top one out (closure.py, user 2026-11-03)
        # Nothing saying which end is on top: laid as before (its own length's ring, the ends apart), said
        tl = float(p.get('tilt', 0.0))
        cpl = CL.plan(spec, pat, i, name) if band else None
        if cpl is not None:
            top, by = CL.top_end(spec, pat, names, i, cpl,
                                 _ring_wrap(g, V, p['anchor'], p['theta'], p['h'], width, tl)[0], cx_)
            CLOSURE_LAST[name] = (top, by, cpl['seam']['id'])
            if top is None:
                cpl = None
            else:
                width = cpl['span'] - RING_GAP
                cpl['d'] = CL.offsets(V, cpl, top, float(pat['h']) if 'h' in pat else 6.0)
        r = (width + RING_GAP) / (2 * np.pi) if band else max(g['r'], wr.get(name, g['r']))
        sp = (_span(Vb, p['anchor'], p['theta'], p['h'], r, tl) if not band
              else (-1e3, 1e3, *_span(Vb, p['anchor'], 0, p['h'], r, tl)[2:]))
        # a piece that overlaps one placed before it starts where it was placed, through it, and is said (red on
        # its placed object, below): it is no longer stacked LAYER mm outside (a jacket's side panel stacked over its fronts
        # and backs folded in half, 145 -> 55 mm, REVIEW_fable_G4_r8; a guess the user did not make, user
        # 2026-10-25, DECISIONS cloth-sim-no-auto-placement-2026-10-25). Move by Hand moves it off
        if name in ring:
            r = ring_r
        # Move Outside (user 2026-10-25): LAYER mm outside the pieces it overlaps on its guide, once per press; only
        # when asked (the user picks which piece goes on top: a pocket, the skirt's back at the sides)
        k_out = int(p.get('outside', 0))
        if k_out and not band:
            below = [r2 for (s2, r2, n2) in placed.get(p['guide'], []) if _overlap(sp, s2)]
            if below:
                r = max(r, max(below) + LAYER * k_out)
        # every piece that is not a band goes on one cylinder round its guide, pushed out as a whole until clear:
        # bent, never stretched (user rule 2026-10-08). Pieces round a vertical guide were wrapped with the radius
        # following the body per height (rows isometric, 2026-09-28, so the pieces round a leg met at the side
        # seams); rows at different radii shear apart at a piece's ends, and its vertical edges started stretched
        # up to 8.0 x (shirt back; jacket fronts 7.1, pants 3.3, skirt 1.4). On one cylinder they start at 1.00.
        # The largest body radius over the piece's height as the cylinder took a jacket's fronts out to the hips
        # (r 170 instead of 143 mm, 25-50 mm off the shoulders) and it fell off in the sleeved stages
        need = MARGIN
        lifted = 0.0
        if band and BAND_LIFT > 0:          # 0: a band stays at the height placed (user 2026-10-25)
            ez_ = np.asarray(g['R'], float)[2]
            for dh in np.arange(0.0, BAND_LIFT + 0.1, 5.0):
                Xs, r0 = _ring_wrap(g, V, p['anchor'], p['theta'], p['h'] + dh, width, tl)
                o_ = np.asarray(g['o'], float)
                rel = Xs - o_
                ax = (rel @ ez_)[:, None] * ez_
                Xs = o_ + ax + (rel - ax) * (r / r0)
                if _clearance(body_bvh, Xs, skip).min() >= need:
                    lifted = float(dh)
                    break
            p = dict(p, h=p['h'] + lifted)
        # a piece round an arm goes round the upper arm's axis (the guide) or, if the guide has it, the whole arm's
        # line (shoulder to hand): the one that clears the body at the smaller radius, chosen once per arm on its
        # largest piece and kept for the others. Round the upper arm's axis a long sleeve's forearm end lay 74 mm off
        # it, so the sleeve was made thicker (r 90.8 on a 58.3 mm guide) and its under-arm panel's inner side went
        # into the bodice (171 crossing pairs at the start, Fable r2 2026-10-09). Still one cylinder each
        gw, th_w, h_w = g, p['theta'], p['h']
        if p['guide'].startswith('arm') and not band and g.get('full'):
            gf = dict(g, o=g['full']['o'], R=g['full']['R'])
            Rg, Rf = np.asarray(g['R'], float), np.asarray(gf['R'], float)
            Pw = (np.asarray(g['o'], float) + g['r'] * (np.cos(np.radians(p['theta'])) * Rg[0]
                                                         + np.sin(np.radians(p['theta'])) * Rg[1]) + p['h'] * Rg[2])
            rel_f = (Pw - np.asarray(gf['o'], float)) @ Rf.T
            th_f, h_f = float(np.degrees(np.arctan2(rel_f[1], rel_f[0]))), float(rel_f[2])
            if p['guide'] not in arm_shift:
                need_r = []
                for gx, thx, hx in ((g, p['theta'], p['h']), (gf, th_f, h_f)):
                    rx = r
                    for _ in range(60):
                        Xs = _own_side(_wrap(gx, V, p['anchor'], thx, hx, rx, tl), p['guide'], cx_)
                        cx = _clearance(body_bvh, Xs, skip).min()
                        if cx >= MARGIN:
                            break
                        rx += max(3.0, MARGIN - cx)
                    need_r.append(rx)
                arm_shift[p['guide']] = need_r[1] < need_r[0]
                rep.append(f"  {p['guide']}: radius to clear the body round the upper arm's axis {need_r[0]:.1f} mm, "
                           f"round the whole arm's line {need_r[1]:.1f} mm -> {'whole arm' if arm_shift[p['guide']] else 'upper arm'}")
            if arm_shift[p['guide']]:
                gw, th_w, h_w = gf, th_f, h_f

        # Ring Size (user 2026-11-02, DECISIONS cloth-sim-ring-size-2026-11-02): a band's ring as a share of its own
        # length's, set by hand -- laid at it, not pushed out, what is in the body red. Auto (none set): pushed out
        # until clear where it is seen, after the hand move (its pose): judged before it, a collar placed high and
        # moved down 35 mm was widened x1.66 where x1.2 cleared the body
        ring_k = float(p['ring']) if band and p.get('ring') else None
        pose = p.get('pose') if band else None
        if ring_k is not None:
            r = (width + RING_GAP) / (2 * np.pi) * ring_k

        def at(r_):
            Xs = _at(r_)
            return Xs, _clearance(body_bvh, Xs if pose is None else _posed(Xs, pose), skip)

        def _band_at(r_, Vm):
            if tl:
                # tilted: about the ring at r_ (scaling the tilted band out to r_ scaled the tilt with it, a collar
                # tilted 90 reached 311 mm from the neck's axis instead of 231, 2026-11-01)
                return _ring_wrap(g, Vm, p['anchor'], p['theta'], p['h'], width, tl, radius=r_)[0]
            Xs, r0 = _ring_wrap(g, Vm, p['anchor'], p['theta'], p['h'], width, tl)
            o, ez = np.asarray(g['o'], float), np.asarray(g['R'], float)[2]
            rel = Xs - o
            ax = (rel @ ez)[:, None] * ez
            return o + ax + (rel - ax) * (r_ / r0)

        def _at(r_):
            if band and cpl is not None:
                wrap = lambda Vm: _band_at(r_, Vm)
                Xs = CL.lay(wrap, V, cpl['d'], np.asarray(g['o'], float), np.asarray(g['R'], float)[2])
            elif band:
                Xs = _band_at(r_, V)
            else:
                Xs = _own_side(_wrap(gw, V, p['anchor'], th_w, h_w, r_, tl), p['guide'], cx_)
            return Xs
        # a piece that meets the body goes out as a whole (out per height, the shirt dressed with 26-54 vertices
        # inside and 578-623 / 797 seams closed; 0 and 739-741 pushed as a whole, 2026-09-29), to the smallest
        # radius that clears it (within 1 mm): out in steps of the depth it is in, then halved back. Stepping out
        # only, a cylinder overshot -- the clearance is to the nearest face, not along the radius -- and a jacket's
        # back went out to 174 mm, 30-37 mm off the body (2026-10-08)
        Xi, cl = at(r)
        lo, hi = None, None
        for it in range(0 if ring_k is not None else 60):
            if cl.min() >= need:
                hi = (r, Xi, cl)
            else:
                lo = r
            if hi is None:
                r += max(3.0, need - cl.min())
                if r > 3.0 * g['r']:
                    rep.append(f"  WARNING {name}: could not clear the body within 3x the guide radius")
                    break
            elif lo is None or hi[0] - lo <= 1.0:
                r, Xi, cl = hi
                break
            else:
                r = 0.5 * (lo + hi[0])
            Xi, cl = at(r)
        if hi is not None and cl.min() < need:
            r, Xi, cl = hi
        if band:
            rings.append(name)
            # wider than its own length only where the body is: said, and shown red on its placed object
            r_own = (width + RING_GAP) / (2 * np.pi)
            RING_USED[name] = (round(r / r_own, 3), ring_k is None)
            if ring_k is not None and cl.min() < 0:
                # a Ring Size set by hand is kept: what is in the body is said and red (placement.show_start)
                short_in[name] = np.nonzero(cl < 0)[0].tolist()
                rep.append(f"{IN_BODY}{name}: {len(short_in[name])} vertices in the body at Ring Size x{ring_k:.2f}")
            if ring_k is None and r > 1.01 * r_own:
                short[name] = round(r / r_own, 2)
                rep.append(f"{SHORT_BAND}{name}: {2 * np.pi * r - width - RING_GAP:.0f} mm shorter than the body "
                           f"here, starts x{r / r_own:.2f}")
        sp = _span(Vb, p['anchor'], p['theta'], p['h'], r, tl) if not band else (-1e3, 1e3, *sp[2:])
        # on one surface with a piece placed before it (within LAYER of its radius): they start through each other.
        # At radii further apart one lies inside the other and nothing crosses (a waistband in the trousers' tops)
        under = [n2 for (s2, r2, n2) in placed.get(p['guide'], []) if _overlap(sp, s2) and abs(r - r2) < LAYER]
        # well inside a piece placed before it (pushed out less): hidden under it from the start. A pants' side
        # pocket at r 77.5 under its fronts pushed out to 108.8 by the hips crossed them 73 times when sewn on
        # (2026-10-25). Said, not moved: a lining is meant to be inside. Bands round the whole guide are left out
        inside = [n2 for (s2, r2, n2) in placed.get(p['guide'], [])
                  if not band and n2 not in rings and _overlap(sp, s2) and r2 - r >= LAYER]
        if under or inside:
            over[name] = {'through': under, 'inside': inside}
        placed.setdefault(p['guide'], []).append((sp, r, name))
        rep.append(f"  {name:<18} {p['guide']:<6} {'band' if band else 'wrap':<4} r {r:6.1f} mm, clearance min "
                   f"{cl.min():6.1f} mm, over {len(under)}, layer {layer[name]}" + (f", lifted {lifted:.0f} mm" if lifted else "")
                   + (f", girth {girth:.0f} width {width:.0f}" if band else "")
                   + (", round the whole arm's line" if gw is not g else ""))
        X[i] = Xi
    return X, rings, rep, short, over


def face_out(X, T, roll, axis_xy, fall=()):
    """The pattern's face beside a fold line (the winding normal of the triangles touching it, not on the side that
    turns) looks away from axis_xy. A fold's sign is seen from the pattern's face, as the solver reads it (user
    2026-10-25, DECISIONS cloth-sim-fold-face-mountain-valley-2026-10-25): a mountain (+) turns in towards the axis
    only while the face looks out -- a piece turned over in 3D folds the other way round the body, with its cloth."""
    T = np.asarray(T, np.int64).reshape(-1, 3)
    X = np.asarray(X, float)
    on = np.zeros(len(X), bool)
    on[np.asarray(roll, np.int64)] = True
    off = np.zeros(len(X), bool)
    off[np.asarray(fall, np.int64)] = True
    sel = T[on[T].any(1) & ~off[T].any(1)]
    if not len(sel):
        return True
    P = X[sel]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]).sum(0)
    c = P.reshape(-1, 3).mean(0)
    ax = np.asarray(axis_xy, float)
    return float(n[0] * (c[0] - ax[0]) + n[1] * (c[1] - ax[1])) >= 0.0


def prefold(X, flat, roll, seam, axis_xy, gap=4.0, fall=None, inward=False, angle_deg=180.0):
    """Turn a wrapped piece over along a fold line before simulating (FINDINGS 4j-5, the approved jacket's collar):
    on the cylinder round axis_xy, developed flat, the side of the roll line away from `seam` is mirrored across the
    line and carried out round a bend of radius gap/2. Left to the solver the collar turned over the wrong way
    (inwards and up) or, folded alone in zero gravity, drifted 50 mm. X (n, 3) mm world, flat (n, 2) layout,
    roll / seam: vertex indices of the fold line and of the seam that stays; fall: the vertices that turn over, when
    the fold is along a seam (the whole collar turns over its seam to the stand); inward: the turned side goes in
    towards the axis (a back vent folded under) instead of out. Returns the new X."""
    X = np.asarray(X, float).copy()
    ax = np.asarray(axis_xy, float)
    r2 = flat[roll]
    pc = np.linalg.svd(r2 - r2.mean(0), full_matrices=False)[2][0]
    order = np.argsort((r2 - r2.mean(0)) @ pc)
    roll, r2 = roll[order], r2[order]
    tang = np.gradient(r2, axis=0)
    tang /= np.linalg.norm(tang, axis=1, keepdims=True)
    nrm = np.c_[-tang[:, 1], tang[:, 0]]

    def side(u):
        k = np.argmin(np.linalg.norm(r2 - flat[u], axis=1))
        return float((flat[u] - r2[k]) @ nrm[k])
    rs = set(roll.tolist())
    if fall is None:
        seam_side = np.sign(np.mean([side(u) for u in seam]))
        fall = np.array([u for u in range(len(X)) if u not in rs and np.sign(side(u)) != seam_side])
    else:
        fall = np.array([u for u in fall if u not in rs])
    th = lambda P: np.arctan2(P[:, 1] - ax[1], P[:, 0] - ax[0])
    Pr = X[roll]
    t0 = np.arctan2(np.sin(th(Pr)).mean(), np.cos(th(Pr)).mean())
    Rref = np.hypot(Pr[:, 0] - ax[0], Pr[:, 1] - ax[1]).mean()
    dev = lambda P: np.c_[Rref * ((th(P) - t0 + np.pi) % (2 * np.pi) - np.pi), P[:, 2]]
    Ld = dev(Pr)
    sa, sb = Ld[:-1], Ld[1:]
    P = X[fall]
    Q = dev(P)
    rad = np.hypot(P[:, 0] - ax[0], P[:, 1] - ax[1])
    rho = gap / 2.0
    Qn, nd = np.empty_like(Q), np.empty(len(Q))
    for k, q in enumerate(Q):
        ab = sb - sa
        tt = ((q - sa) * ab).sum(1) / (ab * ab).sum(1)
        tt[1:] = np.maximum(tt[1:], 0)
        tt[:-1] = np.minimum(tt[:-1], 1)
        foot = sa + tt[:, None] * ab
        j = np.argmin(np.linalg.norm(q - foot, axis=1))
        tau = ab[j] / np.linalg.norm(ab[j])
        nu = np.array([-tau[1], tau[0]])
        n = (q - foot[j]) @ nu
        if n < 0:
            nu, n = -nu, -n
        # angle_deg: how far it turns over (180 = folded flat back; 90 = standing out at a right angle, user
        # 2026-10-03: folded flat, the collar's free end started next to its own seam and was caught under the bodice)
        th_ = np.radians(angle_deg)
        phi = min(n / rho, th_)
        rest_ = max(n - th_ * rho, 0.0)
        Qn[k] = foot[j] + nu * (rho * np.sin(phi) + rest_ * np.cos(th_))
        nd[k] = rho * (1 - np.cos(phi)) + rest_ * np.sin(th_)
    tn = t0 + Qn[:, 0] / Rref
    rn = rad - nd if inward else rad + nd
    X[fall, 0] = ax[0] + rn * np.cos(tn)
    X[fall, 1] = ax[1] + rn * np.sin(tn)
    X[fall, 2] = Qn[:, 1]
    return X, len(fall)


def fold_flap(pat, i, fid, T=None):
    """(roll, fall) local vertex indices of piece i's internal line fid: fall = the side that turns over, the one with
    fewer vertices sewn to other pieces, then the smaller. With the piece's triangles T: the part of the piece cut off
    by the line (the line and its ends carried on to the outline are the wall, a flood over the triangles) -- a back
    vent's hem row lies 7 mm below the line's end and is sewn to the hem of the rest (the fold's foot is stitched);
    without T, or if the flood leaks past half the piece: the vertices along the line's own length (+-3 mm) only (the
    rest of that side above a vent's 38 mm line is the center-back strip, 68 of its 92 vertices, 22 sewn)."""
    st_ = pat['stitch']
    fl_ = pat[f"V{i}"]
    roll = np.nonzero(pat[f"Lf{i}"] == fid)[0]
    if len(roll) < 3:
        return roll, np.zeros(0, np.int64)
    sewn = np.unique(np.r_[st_[(st_[:, 0] == i) & (st_[:, 2] != i), 1], st_[(st_[:, 2] == i) & (st_[:, 0] != i), 3]])
    r2 = fl_[roll]
    pc2 = np.linalg.svd(r2 - r2.mean(0), full_matrices=False)[2][0]
    sd = (fl_ - r2.mean(0)) @ np.array([-pc2[1], pc2[0]])
    tt_, tr_ = (fl_ - r2.mean(0)) @ pc2, (r2 - r2.mean(0)) @ pc2
    ext_ = (tt_ > tr_.min() - 3.0) & (tt_ < tr_.max() + 3.0)
    sides = [np.nonzero((sd > 1e-6) & ext_)[0], np.nonzero((sd < -1e-6) & ext_)[0]]
    if T is not None and len(T):
        h_ = float(pat['h']) if 'h' in pat else 6.0
        # the wall: the line's vertices, vertices on its extension, and every edge that crosses the extension (a
        # vent's line stops 7 mm short of the hem; the flood went round its end through the triangles in between)
        lo_, hi_ = tr_.min() - 1.5 * h_, tr_.max() + 1.5 * h_
        wall = set(roll.tolist()) | set(np.nonzero((np.abs(sd) < 0.05) & (tt_ > lo_) & (tt_ < hi_))[0].tolist())
        nbr = [set() for _ in range(len(fl_))]
        for a, b, c in np.asarray(T).tolist():
            for x_, y_ in ((a, b), (b, c), (c, a)):
                if x_ not in wall and y_ not in wall and sd[x_] * sd[y_] < 0:
                    tc_ = tt_[x_] + (tt_[y_] - tt_[x_]) * sd[x_] / (sd[x_] - sd[y_])
                    if lo_ < tc_ < hi_:
                        continue
                nbr[x_].add(y_); nbr[y_].add(x_)
        flood = []
        for sgn in (1.0, -1.0):
            seed = [u for r in roll for u in nbr[r] if u not in wall and sd[u] * sgn > 0 and ext_[u]]
            seen, todo = set(seed), list(seed)
            while todo:
                u = todo.pop()
                for w in nbr[u]:
                    if w not in wall and w not in seen:
                        seen.add(w); todo.append(w)
            flood.append(np.array(sorted(seen), np.int64))
        if min(len(f) for f in flood) and min(len(f) for f in flood) < 0.5 * len(fl_):
            sides = flood
    a_side, b_side = sides
    ka, kb = np.isin(a_side, sewn).sum(), np.isin(b_side, sewn).sum()
    fall = a_side if (ka, len(a_side)) < (kb, len(b_side)) else b_side
    return roll, np.setdiff1d(fall, roll)


def _fold_fid(pat, piece, line):
    for fid, m in enumerate(pat['fold_meta']):
        if str(m).split('|')[:2] == [piece, line]:
            return fid
    return None


# the side taken out lies at least this far off the side kept (its fabric's thickness if more): at 0.5 mm the two
# layers crossed 117 times on a shirt's placket after 150 frames, at 1 mm 30 (experiments/fold_one_layer_2026-10-24)
ONE_LAYER_GAP_MM = 1.0


def one_layer_for_stage(cfg):
    """cfg one_layer kept only for the pieces this stage solves: none under weld (the solver stops on the two together),
    none for a piece not active, frozen or a ghost (its side taken out would be meshed alone). Says why in
    cfg 'one_layer_skipped'."""
    rows = cfg.get('one_layer')
    if not rows:
        return
    out = set(cfg.get('frozen_pieces') or []) | set(cfg.get('ghost_pieces') or [])
    act = cfg.get('active_pieces')
    keep, why = [], []
    for r in rows:
        if cfg.get('weld'):
            why.append(f"{r['piece']} {r['line']}: weld")
        elif (act and r['piece'] not in act) or r['piece'] in out:
            why.append(f"{r['piece']} {r['line']}: not solved in this stage")
        else:
            keep.append(r)
    if keep:
        cfg['one_layer'] = keep
    else:
        cfg.pop('one_layer', None)
    if why:
        cfg['one_layer_skipped'] = why


def one_layer_rows(spec, names, pat, tris, offs, notes):
    """cfg one_layer for the folds set to one layer (spec fold "layers": "A" / "B" = the side kept; user 2026-10-24,
    DECISIONS cloth-sim-fold-one-layer-2026-10-24): [{"piece", "line", "keep", "verts": the side taken out, "roll":
    the line, full-garment ids, "deg": the signed fold angle, "gap_mm"}]. The solver folds the side taken out off the
    flat pattern by the angle, binds it to the side kept and only simulates that one (solver/newton_vbd.py). Never
    refused (user 2026-10-24: a patch laid on without a fold comes later): what looks wrong is said in notes."""
    from . import place_fold as PF
    rows, taken = [], set()
    for f in spec.get('folds', []):
        keep = f.get('layers')
        if keep not in ('A', 'B') or not f.get('line') or f.get('piece') not in names:
            continue
        pn, ln = f['piece'], str(f['line'])
        i, fid = names.index(pn), _fold_fid(pat, pn, ln)
        il = next((l for l in S.piece_index(spec).get(pn, {}).get('internals', []) if str(l.get('id')) == ln), None)
        ab = PF.split_ab(pat, i, fid, tris[i], np.asarray((il or {}).get('pts', ()), float)) if fid is not None \
            else None
        if ab is None:
            notes.append(tx("one layer: {0} {1} does not divide the piece, both layers simulated", pn, ln))
            continue
        roll, sides, a, _ = ab
        ia = 0 if a is None else a
        kept, drop = (sides[ia], sides[1 - ia]) if keep == 'A' else (sides[1 - ia], sides[ia])
        if taken & set(drop.tolist()) or taken & set(kept.tolist()):
            notes.append(tx("one layer: {0} {1} overlaps another line set to one layer, both layers simulated",
                            pn, ln))
            continue
        taken |= set(drop.tolist())
        V = np.asarray(pat[f"V{i}"], float)
        r2 = V[roll]
        c = r2.mean(0)
        u = np.linalg.svd(r2 - c, full_matrices=False)[2][0]
        w = np.array([-u[1], u[0]])
        s = (V - c) @ w
        if np.median(s[drop]) < 0:
            s = -s
        deg = float(f.get('deg', 0.0))
        th = FA.thickness(FA.piece_fabric(spec, pn))
        gap = max(float(th or 0.0), ONE_LAYER_GAP_MM)
        wd, wk = float(s[drop].max()), float(-s[kept].min()) if len(kept) else 0.0
        if wd > wk + 1.0:
            notes.append(tx("one layer: {0} {1}: the side taken out is {2:.1f} mm wider than the side kept",
                            pn, ln, wd - wk))
        st = np.asarray(pat['stitch'])
        own = ((st[:, 0] == i) & np.isin(st[:, 1], drop) & (st[:, 2] != i)) | \
              ((st[:, 2] == i) & np.isin(st[:, 3], drop) & (st[:, 0] != i))
        if own.any():
            notes.append(tx("one layer: {0} {1}: {2} stitch(es) of the side taken out move to the side kept",
                            pn, ln, int(own.sum())))
        # sewn shut to the side kept (a placket's edges): at an angle short of 180 they show open by this much
        same = (st[:, 0] == i) & (st[:, 2] == i)
        pr = [(a_, b_) for a_, b_ in st[same][:, [1, 3]] if (a_ in drop) != (b_ in drop)]
        if pr and abs(deg) < 179.0:
            th_ = np.radians(abs(deg))
            op = 0.0
            for a_, b_ in pr:
                dv, kv = (a_, b_) if a_ in drop else (b_, a_)
                t_ = (V[dv] - c) @ u
                P = np.array([t_, s[dv] * np.cos(th_), s[dv] * np.sin(th_)])
                Q = np.array([(V[kv] - c) @ u, s[kv], 0.0])
                op = max(op, float(np.linalg.norm(P - Q)))
            notes.append(tx("one layer: {0} {1}: the seams closing the fold show {2:.0f} mm open at {3:g} degrees",
                            pn, ln, op, abs(deg)))
        inner = [g.get('line') for g in spec.get('folds', []) if g.get('piece') == pn and g.get('line')
                 and str(g['line']) != ln and _fold_fid(pat, pn, str(g['line'])) is not None
                 and np.isin(np.nonzero(np.asarray(pat[f"Lf{i}"]) == _fold_fid(pat, pn, str(g['line'])))[0],
                             drop).any()]
        if inner:
            notes.append(tx("one layer: {0} {1}: the side taken out holds the fold of {2}, which follows it",
                            pn, ln, ', '.join(map(str, inner))))
        rows.append({'piece': pn, 'line': ln, 'keep': keep, 'verts': (offs[i] + drop).tolist(),
                     'roll': (offs[i] + roll).tolist(), 'deg': deg, 'gap_mm': gap})
    return rows


def attach_rows(spec, names, pat, offs, E_st, body_ob, co, tri, rep):
    """[(entry, (verts, line length mm, tape points world mm lifted off the body, closed))] of spec['attach'] (core/
    attach.py); what is skipped and why goes to rep."""
    ents = AT.entries(spec)
    if not ents or body_ob is None:
        return []
    geo = {}
    try:
        geo = json.loads(body_ob.get('ac9_measure_geo', '{}'))
    except (ValueError, TypeError):
        pass
    M = np.array(body_ob.matrix_world)
    bvh = body_bvh(co, tri)
    out = []
    for a in ents:
        tp = AT.tape_points(geo, a['tape'])
        if tp is None:
            rep.append(f"  attach {a.get('id', '?')}: no tape '{a['tape']}' on the body (Measure Body / Tape) -- skipped")
            continue
        lv = AT.line_vertices(spec, a, names, pat, offs, E_st, pat['stitch_seam'] if 'stitch_seam' in pat else None)
        if lv is None:
            rep.append(f"  attach {a.get('id', '?')}: its line is not in this garment -- skipped")
            continue
        P = (np.c_[tp[0] * 0.001, np.ones(len(tp[0]))] @ M.T)[:, :3]             # world m
        P = AT.resample(P * 1000.0, tp[1]) * 0.001
        for i, p in enumerate(P):
            loc, nrm, _i, dist = bvh.find_nearest(Vector(p.tolist()))
            if loc is not None and dist < AT.LIFT_MM * 0.001:
                P[i] = np.array(loc) + np.array(nrm) * (AT.LIFT_MM * 0.001)
        out.append((a, (lv[0], lv[1], P * 1000.0, tp[1])))
        rep.append(f"  attach {a.get('id', '?')}: {len(lv[0])} vertices to '{a['tape']}' ({a.get('until', 'end')})")
    return out


def body_bvh(co_mm, tri):
    return BVHTree.FromPolygons((co_mm * 0.001).tolist(), tri.tolist(), all_triangles=True)


def _same_arrays(a, b):
    return b is not None and len(a) == len(b) and all(x.shape == y.shape and np.array_equal(x, y) for x, y in zip(a, b))


def _skip_key(skip):
    """A skip mask by content, the last one remembered (one array per prepare, the same on every press)."""
    last = _BODY_KEEP['skip']
    if last is None or last[0].shape != skip.shape or not np.array_equal(last[0], skip):
        _BODY_KEEP['skip'] = (np.array(skip, copy=True), len(_BODY_KEEP['skips']))
        _BODY_KEEP['skips'].append(None)
    return _BODY_KEEP['skip'][1]


# What prepare makes of the body while the body (with the obstacles) stays the same (prepare_cache t4): the Body (its
# section loops), the BVH and the clearances asked of it. Compared by content each time, copies kept.
_BODY_KEEP = {'body_in': None, 'body': None, 'bvh_in': None, 'bvh': None, 'clear': {}, 'skip': None, 'skips': [],
              'skip_key': _skip_key}


def _body_kept(co, tri):
    """BA.Body(co, tri), the same one while co and tri are the same."""
    if not _same_arrays((co, tri), _BODY_KEEP['body_in']):
        _BODY_KEEP['body_in'] = (np.array(co, copy=True), np.array(tri, copy=True))
        _BODY_KEEP['body'] = BA.Body(co, tri)
    return _BODY_KEEP['body']


def _bvh_kept(co, tri):
    """body_bvh(co, tri), the same one (and its clearances) while co and tri are the same."""
    if not _same_arrays((co, tri), _BODY_KEEP['bvh_in']):
        _BODY_KEEP['bvh_in'] = (np.array(co, copy=True), np.array(tri, copy=True))
        _BODY_KEEP['bvh'] = body_bvh(co, tri)
        _BODY_KEEP['clear'] = {}
        _BODY_KEEP['skip'], _BODY_KEEP['skips'] = None, []
    return _BODY_KEEP['bvh']


def _xinit_from(ref, pat, X):
    """DIAGNOSIS ONLY: take the initial shape from another run's pattern + mesh + placement (same pieces): each vertex
    is placed by barycentric interpolation in the reference triangle that contains it in the flat layout (the two
    layouts differ by a shift per piece)."""
    R = np.load(ref['pattern'], allow_pickle=True)
    M = np.load(ref['mesh'], allow_pickle=True)
    L = np.load(ref['place'], allow_pickle=True)
    rn = [str(x) for x in R['names']]
    out = []
    for i, n in enumerate(pat['names']):
        j = rn.index(str(n))
        V, Vr, T = pat[f"V{i}"], R[f"V{j}"], M[f"T{j}"]
        Bo, Br = V[:int(pat['nb'][i])], Vr[:int(R['nb'][j])]
        Q = V - 0.5 * (Bo.min(0) + Bo.max(0)) + 0.5 * (Br.min(0) + Br.max(0))
        A, B, C = Vr[T[:, 0]], Vr[T[:, 1]], Vr[T[:, 2]]
        Xr = L[f"Xinit{j}"]
        res = np.zeros((len(Q), 3))
        for k, q in enumerate(Q):
            v0, v1, v2 = B - A, C - A, q - A
            d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
            d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
            den = d00 * d11 - d01 * d01
            v = (d11 * d20 - d01 * d21) / den
            w = (d00 * d21 - d01 * d20) / den
            u = 1 - v - w
            bad = np.minimum(np.minimum(u, v), w)
            t = int(np.argmax(bad))                        # inside, or the least outside
            uu, vv, ww = np.clip([u[t], v[t], w[t]], 0, 1)
            ssum = uu + vv + ww
            res[k] = (uu * Xr[T[t, 0]] + vv * Xr[T[t, 1]] + ww * Xr[T[t, 2]]) / ssum
        out.append(res)
    return out


def _bary_map(Q, Vr, T, Xr, k=16):
    """Positions for flat points Q from a reference piece (flat Vr, triangles T, positions Xr): barycentric in the
    containing (or nearest) reference triangle. The candidates are the k triangles with the nearest centers (a
    KD tree), so a fine mesh carried from a coarse one is fast: 2 mm from 6 mm on a pair of pants took 9.7 s when
    every point was tried against every triangle (2026-10-08)."""
    Q = np.asarray(Q, np.float64)
    A, B, C = Vr[T[:, 0]], Vr[T[:, 1]], Vr[T[:, 2]]
    cen = (A + B + C) / 3.0
    k = min(k, len(T))
    tree = KDTree(len(T))
    for t, c in enumerate(cen):
        tree.insert((float(c[0]), float(c[1]), 0.0), t)
    tree.balance()
    cand = np.array([[i for _co, i, _d in tree.find_n((float(q[0]), float(q[1]), 0.0), k)] for q in Q], np.int64)
    a, v0, v1 = A[cand], B[cand] - A[cand], C[cand] - A[cand]
    v2 = Q[:, None, :] - a
    d00 = (v0 * v0).sum(-1); d01 = (v0 * v1).sum(-1); d11 = (v1 * v1).sum(-1)
    d20 = (v2 * v0).sum(-1); d21 = (v2 * v1).sum(-1)
    den = d00 * d11 - d01 * d01
    with np.errstate(divide='ignore', invalid='ignore'):
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
    u = 1 - v - w
    worst = np.nan_to_num(np.minimum(np.minimum(u, v), w), nan=-np.inf)
    j = np.argmax(worst, 1)                        # inside, or the least outside
    r = np.arange(len(Q))
    W = np.clip(np.stack([u[r, j], v[r, j], w[r, j]], 1), 0, 1)
    W /= W.sum(1, keepdims=True)
    t = cand[r, j]
    return W[:, 0:1] * Xr[T[t, 0]] + W[:, 1:2] * Xr[T[t, 1]] + W[:, 2:3] * Xr[T[t, 2]]


LOG_TEXT = 'AC9_ClothSim_Log'


def log(context, job_dir, kind, text, **data):
    """One entry of the simulation log (core/simlog.py): the job's log.jsonl, the Text block AC9_ClothSim_Log and
    the Dress panel's last lines."""
    line = simlog.write(job_dir, kind, text, **data)
    try:
        tb = bpy.data.texts.get(LOG_TEXT) or bpy.data.texts.new(LOG_TEXT)
        tb.cursor_set(len(tb.lines) - 1, character=len(tb.lines[-1].body))
        tb.write(line + chr(10))
        props = context.scene.ac9_cloth_sim
        tail = [x for x in props.log_tail.split(chr(10)) if x] + [line]
        props.log_tail = chr(10).join(tail[-3:])
    except Exception:
        pass
    return line


def _mesh_h(flat, F):
    """The mesh size of a meshed pattern (mm): the median edge length in the flat layout."""
    e = np.concatenate([np.linalg.norm(flat[F[:, a]] - flat[F[:, b]], axis=1) for a, b in ((0, 1), (1, 2), (2, 0))])
    return float(np.median(e)) if len(e) else 0.0


CONTINUE_INHERIT = presets.CONTINUE_INHERIT          # (core/presets.py build_stages applies it)


def _ease_entry(e, w):
    """The solver's ease for one seam side (TEST ONLY, settings ease_w_mm): its rest shape shrunk along the seam to
    1 / ratio where the side is 2-43 % longer than its partner. Per segment of the seam (with notches the ease differs
    notch to notch, pattern_build 'ratio_seg'); 1 where the side is not the longer by 2 %. None: nothing to ease."""
    rs = e.get('ratio_seg') or [e['ratio']] * max(1, len(e['seam_flat']) - 1)
    s0 = [1.0 / r if 1.02 < r <= 1.43 else 1.0 for r in rs]
    if all(v == 1.0 for v in s0):
        return None
    if len(set(s0)) == 1:
        return {'piece': e['piece'], 'seam_flat': e['seam_flat'], 's0': s0[0], 'w': w}
    return {'piece': e['piece'], 'seam_flat': e['seam_flat'], 's0': s0, 'w': w}


def _last_stage_config(job, depth=0):
    """The solver config of the last stage that wrote frames; a job without one (a pattern edit carried over,
    draft_tool.carry_to_3d) is followed to the job it came from ({} if none)."""
    try:
        fin = json.load(open(os.path.join(job, 'final.json'), encoding='utf-8')).get('stage', 0)
    except (OSError, ValueError):
        fin = 0
    for k in range(20, 0, -1):
        p = os.path.join(job, f"s{k}", 'config.json')
        if (os.path.exists(os.path.join(job, f"s{k}", 'frames.npy')) or k == fin) and os.path.exists(p):
            try:
                return json.load(open(p, encoding='utf-8'))
            except (OSError, ValueError):
                break
    try:
        src = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8')).get('from')
    except (OSError, ValueError):
        src = None
    return _last_stage_config(src, depth + 1) if src and depth < 10 and os.path.isdir(src) else {}


def _from_last_result(job, pat, X, shown=None, redo=(), redo_shown=False):
    """Continue: every piece that was in the last result starts where it ended there (mapped through the flat
    layout, so pieces re-meshed because a seam was switched on or off still map); a piece added since starts from
    its placement. shown (mm, the garment's vertices): the shape on screen instead of the job's last frame -- the
    frame the timeline is on, or the user's hand edits (user 2026-10-03: stop at an anomaly, fix it by hand, go
    on). redo: pieces put back by Reset, which start from their placement as a piece added since -- or, redo_shown
    (the garment frozen: a Reset wrote their placed shapes into it, and they may have been moved on it since), from
    the shape on screen, still sewn on as pieces added since (not carried). Returns (X, names carried over)."""
    prev = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))
    pidx = np.load(os.path.join(job, 'scene.idx.npz'))
    if shown is not None and len(shown) == len(pidx['flat']):
        last = np.asarray(shown, np.float64)
    else:
        last = job_last_frame(job) * 1000.0
    offs = list(pidx['offs']) + [len(pidx['flat'])]
    F, fp = pidx['F'], pidx['fp']
    carried = []
    out = list(X)
    for i, n in enumerate(pat['names']):
        n = str(n)
        if n not in prev['names'] or (n in redo and not redo_shown):
            continue
        j = prev['names'].index(n)
        Vr = pidx['flat'][offs[j]:offs[j + 1]]
        Xr = last[offs[j]:offs[j + 1]]
        T = F[fp == j] - offs[j]
        V = pat[f"V{i}"]
        if len(V) == len(Vr) and np.allclose(V, Vr, atol=1e-6):
            out[i] = Xr.copy()
        else:
            out[i] = _bary_map(V, Vr, T, Xr)
        if n not in redo:
            carried.append(n)
    return out, carried


def job_dir(context):
    base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else tempfile.gettempdir()
    d = os.path.join(base, "AC9_ClothSim_jobs", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(d, exist_ok=True)
    return d


def _laid(spec, piece):
    """Was the piece's placed object laid on its edge by Lay on Edge (place_lay)? Then Simulate's lay may lay it
    again on the sewn edge (it is not 'hand' for core/lay.candidates; user 2026-10-22, Fable r4 §3 request 1).
    Moved by hand after it was laid (G, R, an edit), it is the user's shape: not laid again (design review 10/22)."""
    ob = PLV.objects(spec).get(piece)
    return (ob is not None and ob.get(PLV.PROP) == piece and bool(ob.get(PLV.LAID_PROP))
            and not PLV.moved_since_made(ob) and not PLV.has_edits(ob))


def _hand_placed(spec, pat, X, bvh=None, tris=None, skip=None, only=None):
    """Pieces whose placed object (placed_view) the user moved, rotated or edited start from it (user decision
    2026-10-10: the guide is only a start). Nothing pushes them clear: vertices inside the body and edges stretched
    over 1.05 are reported as WARNING and the object turns red (user 2026-10-10: warn and go on). Returns (X,
    indices taken from an object, report lines). skip(i): body faces not counted for piece i (the arms, for a piece
    not on an arm guide, as the start shape does). only: these pieces alone (the ones a Reset put back)."""
    from . import diagnose as DG
    obs = PLV.objects(spec)
    names = [str(n) for n in pat['names']]
    took, rep = set(), []
    done = {}
    for i, n in enumerate(names):
        if only is not None and n not in only:
            continue
        ob = obs.get(n)
        if ob is None or ob.get(PLV.PROP) != n:
            continue                      # no object, or the mirrored half of a pair's object (follows its source)
        if ob.name not in done:
            done[ob.name] = PLV.is_moved(ob)
        if not done[ob.name]:
            continue
        hs = PLV.hand_shape(ob)
        if hs is None:
            continue
        Vp, Tp, Wp = hs
        V = np.asarray(pat[f"V{i}"], float)
        if ob.get(PLV.NV_PROP) is not None and len(Vp) != int(ob[PLV.NV_PROP]):
            # vertices added or deleted in Edit Mode have no layout point: the piece is placed from its numbers
            rep.append(f"  {n}: WARNING its placed object has {len(Vp)} vertices, made with {int(ob[PLV.NV_PROP])} "
                       f"(added or deleted by hand): placed from the guide instead -- Back to Guide, then move it")
            DG.mark(ob, 'hand', None, "vertices added or deleted by hand")
            continue
        stale = (len(V) != len(Vp)) if ob.get(PLV.KK_PROP) else (ob.get(PLV.KEY_PROP) != PLV.layout_key(V))
        if stale:
            rep.append(f"  {n}: the pattern changed since Move by Hand -- its placed object is mapped through the layout")
        same = len(V) == len(Vp) and (ob.get(PLV.KK_PROP) or np.allclose(V, Vp, atol=1e-3))
        X[i] = Wp.copy() if same else _bary_map(V, Vp, Tp, Wp)
        took.add(i)
        warn = []
        if bvh is not None:
            k_in = int((_clearance(bvh, X[i], skip(i) if skip else None) < 0).sum())
            if k_in:
                warn.append(f"{k_in} vertices inside the body")
        if tris is not None and len(tris[i]):
            T_ = tris[i]
            E_ = np.unique(np.sort(np.r_[T_[:, [0, 1]], T_[:, [1, 2]], T_[:, [2, 0]]], 1), axis=0)
            st_ = (np.linalg.norm(X[i][E_[:, 0]] - X[i][E_[:, 1]], axis=1)
                   / np.maximum(np.linalg.norm(V[E_[:, 0]] - V[E_[:, 1]], axis=1), 1e-9))
            if st_.max() > 1.05:
                warn.append(f"edges stretched up to {st_.max():.2f} x")
        if warn:
            DG.mark(ob, 'hand', None, ', '.join(warn))
        else:
            DG.clear(ob, 'hand')
        rep.append(f"  {n}: from its placed object (moved by hand)" + (f" -- WARNING {', '.join(warn)}" if warn else ""))
    return X, took, rep


def _mirror_hand(X, took, pairs, cx):
    """A linked pair (pairs = [(src, tgt)] piece indices) with one side taken from its placed object (moved by hand,
    took): the other side made its mirror image about x = cx, as a Dress from the start does. A Reset of a pair put
    the side its object's Mirror modifier shows back to the guide's place: the two sleeves at different heights
    (user 2026-10-14)."""
    for s_, t_ in pairs:
        if (s_ in took) != (t_ in took):
            a, b = (s_, t_) if s_ in took else (t_, s_)
            X[b] = np.asarray(X[a], float) * np.array([-1.0, 1.0, 1.0]) + np.array([2.0 * cx, 0.0, 0.0])
    return X


def sync_placed(context, h):
    """Before a Dress from the start: the placed objects' moves written to the spec (placed_view.sync_pose), and a
    piece moved by hand (a pose in the spec) whose placed object is gone made again with it, so Dress starts from
    the same shape either way. Report lines."""
    spec, _ = store.get(context)
    sp = PLV.sync_pose(context, spec)
    if sp is not None:
        store.put(context, sp)
        spec = sp
    pl = spec.get('placement', {})
    obs = PLV.objects(spec)
    miss = [pc['name'] for pc in spec['pieces'] if pc.get('enabled', True) and pl.get(pc['name'], {}).get('pose')
            and pc['name'] not in obs]
    if not miss:
        return []
    res = prepare(context, h, shapes_only=True)
    PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'])
    return [f"  placed objects made again with their hand moves (pose): {', '.join(miss)}"]


# The last meshes made (sim_latency 2026-10-20): core.pattern_build.build is a function of (spec, h) alone, so a
# Continue / Live on a pattern not changed since gets the same mesh back instead of making it again (0.5 s for a shirt,
# 0.8 s for a T-shirt at 6 mm). Keyed by the spec's text; a copy goes out, so nothing downstream changes the kept one.
_BUILD_CACHE = []


def _build_key(spec, h):
    """What pattern_build.build reads of the spec, and h: the pieces (outlines, internals, notches, enabled, mirrored,
    family, mesh_mm), the seams, the name, symmetry (S.twins), twin_swap, fold_rows and -- only with fold_rows -- the
    lines with a set angle. Not the placement, the poses or the fold angles: writing those (every placed-shape tool
    does, Part to Seam 2-3 times a press) remade the whole garment's mesh, 300 ms of a 600 ms press on a shirt
    (experiments/prepare_cache_2026-10-24). Of the pieces only the enabled ones: build meshes only those (and S.twins
    pairs only those), and a garment from a BOM holds every piece of the file -- 133 on a pants, 13 on, 40 ms of
    hashing per edit (2026-10-27). A new spec[...] read in build must be added here."""
    import hashlib
    sub = {k: spec.get(k) for k in ('seams', 'name', 'symmetry', 'twin_swap', 'fold_rows')}
    sub['pieces'] = [p for p in spec.get('pieces') or [] if p.get('enabled', True)]
    if spec.get('fold_rows'):
        sub['folded_lines'] = sorted([f['piece'], f['line']] for f in spec.get('folds', [])
                                     if f.get('line') and f.get('deg'))
    txt = json.dumps(sub, ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha1(txt.encode('utf-8')).hexdigest(), float(h)


def _build_cached(spec, h):
    """The one way in to pattern_build.build for Simulate, placing and the pattern edits' carry (draft_tool), so a
    mesh made by one is read by the next. Under this, build keeps each piece's interior by its inputs (memory, and
    AC9_ClothSim_jobs/mesh_cache next to the .blend): an edit remakes the pieces it changed."""
    import copy
    base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else tempfile.gettempdir()
    pattern_build.CACHE_DIR = os.path.join(base, "AC9_ClothSim_jobs", "mesh_cache")
    key = _build_key(spec, h)
    for k, v in _BUILD_CACHE:
        if k == key:
            return copy.deepcopy(v)
    v = pattern_build.build(spec, h)
    _BUILD_CACHE[:] = ([(key, copy.deepcopy(v))] + _BUILD_CACHE)[:2]
    return v


# The last start shapes prepare(shapes_only=True) made (experiments/prepare_cache_2026-10-24): every placed-shape tool
# (Part to Seam, Band on Edge, Fold, Back to Guide, Move by Hand ...) asks for them again on each press, 300 ms on a
# shirt with the mesh kept. [(scalar key, arrays, result)]; a copy goes out, so a caller writing into X changes nothing
# kept.
_SHAPES_CACHE = []


def _shapes_spec_key(spec):
    """The job's spec (the settings set laid over, the twins mirrored, the pieces not placed left out) as the start
    shapes read it: all but the folds, and of the folds those of 90 degrees or more (collar_prefold, prefold_torso),
    all of them with fold_rows (the mesh). A fold angle under 90 written by the Tool tab's Fold Angle does not change
    the start shapes: its placed object is folded from its own shape (place_fold)."""
    import hashlib
    keep_all = bool(spec.get('fold_rows'))

    def big(f):
        try:
            return abs(float(f.get('deg') or 0.0)) >= 90.0
        except (TypeError, ValueError):
            return True
    rest = {k: v for k, v in spec.items() if k != 'folds'}
    fk = [f for f in spec.get('folds', []) if keep_all or big(f)]
    txt = json.dumps([rest, fk], ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha1(txt.encode('utf-8')).hexdigest()


def _shapes_get(pkey):
    import copy
    k, arrs = pkey
    for k2, arrs2, v in _SHAPES_CACHE:
        if k2 == k and len(arrs2) == len(arrs) and all(a.shape == b.shape and np.array_equal(a, b)
                                                       for a, b in zip(arrs, arrs2)):
            return copy.deepcopy(v)
    return None


def _shapes_put(pkey, out):
    import copy
    k, arrs = pkey
    _SHAPES_CACHE[:] = ([(k, [np.array(a, copy=True) for a in arrs], copy.deepcopy(out))] + _SHAPES_CACHE)[:2]


def _others_have_frames(context, spec):
    """Another garment with frames other_garments would drop (drop_frames)."""
    me = garment_name(spec)
    for ob in context.scene.objects:
        if ob.type == 'MESH' and ob.name.startswith(GARMENT_PREFIX) and ob.name != me:
            job = job_of(ob)
            if job:
                caches, files = _frames_of(ob, job)
                if caches or any(f not in _DROP_LATER for f in files):
                    return True
    return False


def prepare(context, h=6.0, xinit_from=None, continue_job=None, continue_shown=None, live=False, shapes_only=False,
            redo=(), redo_shown=False, placed_only=False, local=None, new_placed=None, phase_at=None):
    """Everything up to the solver input. Returns a job dict (paths, stages) or raises ValueError.
    phase_at: with continue_job, a phase_point dict -- the Phase Sim's own stages from that stage and frame (the shape
    continue_shown, the frame's), not the Continue stages.
    continue_job: a finished job folder of this garment -- start from its last frame (same pattern needed).
    shapes_only: stop at the start shape, before any hand-moved placed object is read and before a job folder is
    made; returns {'pat', 'tris', 'X', 'pairs' [(src, tgt)], 'rep'} (placed_view.build shows it).
    redo: with continue_job, the pieces a Reset put back -- from their placed shapes (a hand move kept), sewn on.
    placed_only: retained for placed-shape callers; all preparation now leaves unplaced pieces and their seams out
    of the run (user 2026-10-30), without changing the stored pieces' enabled states.
    local: with live, Local Sim's pieces (the others left out as Off (All)).
    new_placed: with continue_job, what flush_placed(new_placed=...) filled -- pieces of redo the last result does
    not have, started from their moved placed shapes."""
    PLV.leave_edit(context)            # a placed object still in Edit Mode: its edits, and its layout points, readable
    rep_pose = [] if shapes_only or continue_job else sync_placed(context, h)
    spec, _ = store.get(context)
    props = context.scene.ac9_cloth_sim
    # every piece has one fabric, the garment's default one of the list (core/fabric.py, user 2026-10-20): a garment
    # saved before gets its default from the old Dress panel's Weight / Bending -- its solver input unchanged --
    # written back to the scene's spec
    fab_mig = []
    if FA.needs_migrate(spec):                  # (the copy below cost 20 ms a call on a shirt when nothing changed)
        sp_ = S.loads(S.dumps(spec))
        if FA.migrate(sp_, props.fabric_weight, props.fabric_bending, fab_mig):
            store.put(context, sp_)
            spec = sp_
    # the settings set (core/presets.py): the scene's preset (empty on the screen = 'default'); the old test
    # environment variables are read by headless runs only
    cs = presets.resolve(getattr(props, 'preset', ''), props, environ=None if bpy.app.background else {})
    # the garment's cloth is its default fabric (a test set may fix the weight / bending)
    dflt = FA.default_fabric(spec)
    gv = FA.garment_values(dflt)
    for k_ in ('weight_gsm', 'bending_uNm'):
        if k_ not in cs['fixed_fabric']:
            cs[k_] = gv[k_]
    cs['membrane'], cs['kappa_y'], cs['default_fabric'] = list(gv['membrane']), gv['kappa_y'], dflt['name']
    spec = presets.spec_for_job(spec, cs)
    spec['placement'], rep_tw = S.mirror_twins(spec)
    cx = PLV.center_x(spec)            # the plane a pair mirrors about: the body's center (was x = 0)
    if props.body is None:
        raise ValueError("Pick the body object (Placement panel)")
    if not spec.get('guides'):
        raise ValueError("Detect Guides first")
    on = [p['name'] for p in spec['pieces'] if p.get('enabled', True)]
    missing = [n for n in on if n not in spec.get('placement', {})]
    if missing:
        # Unplaced pieces are absent from 3D, so leave them out of every run, including nested preparation calls.
        # spec_for_job is a shallow copy: copy the changed pieces to preserve the stored enabled states.
        spec = dict(spec, pieces=[dict(pc, enabled=False) if pc['name'] in missing else pc for pc in spec['pieces']])
    placed_on = {pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)}
    if not placed_on or (local is not None and not placed_on.intersection(local)):
        raise ValueError(tx("No placed pieces to simulate"))
    t0 = time.time()
    _resume_gate = _resume_note = None
    _fast_live = live and local is None and bool(cs.get('fast_live'))
    if not shapes_only:
        from . import live_resume
        _resume_gate = live_resume.capture(context, h)
        if live and not _fast_live and continue_job and local is None and not (redo or new_placed or xinit_from):
            _resume_dir = job_dir(context)
            if os.path.abspath(_resume_dir) == os.path.abspath(continue_job):
                _resume_dir += '_resume_' + str(time.time_ns())
            _resumed, _resume_note = live_resume.prepare(context, h, continue_job, continue_shown,
                                                       _resume_dir, _resume_gate, job_last_frame)
            if _resumed is not None:
                return _resumed
    # the start shapes alone (the placed objects' tools, many presses on one spec): kept with every input they are
    # made from (_SHAPES_CACHE) -- the job spec (_shapes_spec_key), h, the flags, the settings set, the body's, the obstacles' and the
    # other garments' arrays as evaluated now. Not while another garment still has frames to drop (other_garments
    # drops them: then not read ahead of the meshing, as before)
    # A new read of the scene's state before the shapes_only return must be added to pkey too (as a new spec[...] read
    # in pattern_build to _build_key).
    pkey = pre = None
    if (shapes_only and not (redo or new_placed or continue_job or xinit_from)
            and not _others_have_frames(context, spec)):
        co, tri = PL.body_arrays(context, props.body)
        cols = collider_objects(context, exclude=bpy.data.objects.get(garment_name(spec)))
        col_arr = [PL.body_arrays(context, ob) for ob in cols]
        obst_g, rep_o = other_garments(context, spec)
        pkey = ((_shapes_spec_key(spec), float(h), bool(placed_only), None if local is None else tuple(sorted(local)),
                 json.dumps(cs, sort_keys=True, default=str), props.body.name, tuple(ob.name for ob in cols),
                 tuple(n_ for n_, _c, _t in obst_g), tuple(rep_o)),
                [co, tri] + [a_ for ct_ in col_arr for a_ in ct_] + [a_ for _n, c2, t2 in obst_g for a_ in (c2, t2)])
        hit = _shapes_get(pkey)
        if hit is not None:
            RING_USED.update(hit.get('ring_used', {}))   # the ring those shapes have (AC9 Piece's Ring Size shows it)
            return hit
        pre = (co, tri, col_arr, obst_g, rep_o)
    pat, rep = _build_cached(spec, h)
    tris, bad = triangulate(pat)
    if bad:
        raise ValueError("Could not mesh: " + ", ".join(bad))
    if pre is None:
        co, tri = PL.body_arrays(context, props.body)
        # obstacles (a garment already on): part of the solver's static body and of the start-clearance test
        cols = collider_objects(context, exclude=bpy.data.objects.get(garment_name(spec)))
        col_arr = [PL.body_arrays(context, ob) for ob in cols]
    else:
        co, tri, col_arr, obst_g, rep_o = pre
    body_only = _body_kept(co, tri)                 # one Body while the body stays (its loops remembered)
    co_body, tri_body = co, tri                     # the avatar alone (attach lifts its tapes off it)
    for c2, t2 in col_arr:
        tri = np.vstack([tri, t2 + len(co)])
        co = np.vstack([co, c2])
    if pre is None:
        obst_g, rep_o = other_garments(context, spec)
    for _n, c2, t2 in obst_g:
        tri = np.vstack([tri, t2 + len(co)])
        co = np.vstack([co, c2])
    bvh = _bvh_kept(co, tri)                        # one BVH while the body and obstacles stay (clearances remembered)
    if not cs['arm_legacy'] and spec.get('guides', {}).get('torso'):
        # the arms (left out of the body while the torso is sewn, and not counted when the torso pieces are placed)
        # are the faces beyond the torso's half width along x at their height (arm_mask.arm_tris), for the placement
        # and the solver's stage-1 body alike. Before (settings arm_legacy, was env AC9_SIM_ARM_LEGACY=1): |x| > 1.4 x the half width at the
        # armpit, which left the inner faces of an A-pose upper arm in the body and pushed a jacket's fronts 31 mm out
        # (REVIEW_fable_G4_r7 plan A; shirt and trousers unchanged or better, foundation_2026-09-30/RESULT_G3 note 14)
        gt = spec['guides']['torso']
        # the torso's half width along x per height: the center loops' largest |x - axis x| (arms left out as in
        # radius_profile). The largest radius would be the bust, deeper than the sides, and keep the inner arm faces
        o_ = np.asarray(gt['o'], float)
        arm_x_ = 0.12 * (body_only.z1 - body_only.z0)
        zs_ = np.arange(o_[2] + gt['h0'] - 40.0, o_[2] + gt['h1'] + 40.0, 10.0)
        hw_ = np.zeros(len(zs_))
        for k_, z_ in enumerate(zs_):
            if body_only.z0 < z_ < body_only.z1:
                L_ = [P_ for P_ in body_only.loops(z_) if abs(P_[:, 0].mean()) < arm_x_]
                if L_:
                    hw_[k_] = np.abs(np.vstack(L_)[:, 0] - o_[0]).max()
        if (hw_ > 0).any():
            hw_ = np.interp(zs_, zs_[hw_ > 0], hw_[hw_ > 0])
        spec['body'] = dict(spec.get('body', {}), torso_profile={
            'o': [float(gt['o'][0]), float(gt['o'][1])], 'zs': [float(z) for z in zs_], 'rs': [float(r) for r in hw_],
            'pad': float(cs['arm_profile_pad_mm'])})
    arm_faces = np.zeros(len(tri), bool)
    arm_faces[:len(body_only.T)] = arm_mask.arm_tris(co[body_only.T].mean(1), recipe.drop_arms(spec))
    X, rings, rep2, short_bands, overlaps = initial_shape(spec, pat, bvh, body_only, arm_faces=arm_faces, cs=cs)
    rep2 = rep_pose + rep_tw + rep_o + rep2
    # pieces whose placed object the user moved start from it (placed_view): before the folds and the small parts
    # placed sewn on, so a cuff follows a sleeve moved by hand; a piece so taken is not folded or moved again. Not on
    # Continue / Live: they start from the last result
    took = set()
    if not shapes_only and not continue_job:
        pl_h = spec.get('placement', {})
        nm_h = [str(n) for n in pat['names']]
        X, took, rep_h = _hand_placed(spec, pat, X, bvh, tris, skip=lambda i: None if str(
            pl_h.get(nm_h[i], {}).get('guide', '')).startswith('arm') else arm_faces)
        rep2 = rep2 + rep_h
    # spec['collar_prefold']: a piece on the neck guide with a fold of 90 degrees or more (a collar's roll line) is
    # turned over it before simulating, away from its seam to the other neck piece (the stand), by the fold's own set
    # angle. On by default (user decision 2026-10-07, DECISIONS cloth-sim-collar-place-at-set-angle-2026-10-07): placed
    # at the set angle the fold line has nothing left to push; a jacket collar placed at 90 against its 137.5 turned
    # its stand seam over (16-18 of 31 stitches) and lifted a front 46 mm, at 137.5 0 of 31 in 3 of 3 runs.
    # spec['collar_prefold_deg'] still overrides the angle (tests). False turns it off.
    if spec.get('collar_prefold', True):
        names_ = [str(n) for n in pat['names']]
        pl_ = spec.get('placement', {})
        # only a fold with no Turns written (from a BOM, or set before Turns existed): one with Turns A / B is folded
        # on the placed shape at any angle (place_fold), one with No Side not at all (user 2026-11-02, DECISIONS
        # cloth-sim-no-auto-judgment-default-rules-2026-11-02: 90 degrees no longer decides whether a set angle
        # folds; it is left here only for these old folds, placed as they were before)
        deg = {(f['piece'], f['line']): float(f.get('deg') or 0.0) for f in spec.get('folds', [])
               if f.get('line') and f.get('side') is None}
        st_ = pat['stitch']
        for i, n in enumerate(names_):
            if pl_.get(n, {}).get('guide') != 'neck' or i in took:
                continue
            fids = [k for k, m in enumerate(pat['fold_meta']) if m.split('|')[0] == n
                    and abs(deg.get((n, m.split('|')[1]), 0.0)) >= 90.0]
            others = [j for j, m in enumerate(names_) if j != i and pl_.get(m, {}).get('guide') == 'neck']
            if not fids or not others:
                continue
            Lf_ = pat[f"Lf{i}"]
            roll = np.nonzero(np.isin(Lf_, fids))[0]
            seam = np.unique(np.r_[st_[np.isin(st_[:, 2], others) & (st_[:, 0] == i), 1],
                                   st_[np.isin(st_[:, 0], others) & (st_[:, 2] == i), 3]])
            if len(roll) < 3 or not len(seam):
                continue
            sd_ = max((deg.get((n, pat['fold_meta'][k].split('|')[1]), 0.0) for k in fids), key=abs)
            set_deg = abs(sd_)
            ang_ = float(spec.get('collar_prefold_deg', set_deg))
            # a collar's roll line is a valley seen from its face (-137.5 on the reference jacket): out, as before
            # the sign was read; a mountain, or the face turned to the neck, turns it in
            ax_n = np.asarray(spec['guides']['neck']['o'], float)[:2]
            # no angle on the line (collar_prefold_deg alone): no sign to read, out as always
            fo_ = face_out(X[i], tris[i], roll, ax_n)
            X[i], nf = prefold(X[i], pat[f"V{i}"], roll, seam, ax_n, angle_deg=ang_,
                               inward=sd_ != 0 and (sd_ > 0) == fo_)
            if not fo_:
                rep2 = rep2 + [f"  {n}: its face looks at the neck by its roll line: folded round the neck as if on "
                               f"it, the shape may come out bent"]
            rep2 = rep2 + [f"  prefolded {n}: {nf} of {len(X[i])} vertices over its roll line ({len(roll)} vertices, "
                           f"{ang_:.1f} deg)"]
        # a fold of 90 degrees or more set on a seam between two neck pieces (a shirt collar on its stand): the
        # turning piece goes over that seam as a whole, the seam itself is the roll line. Only when asked
        # (collar_prefold: true): a shirt's collar folded so came out 98 vertices inside the body and through the back
        # yoke (37 triangle pairs) -- the clearance from the body and the layering are checked before the fold only
        # (shirt collar chat, 2026-10-07); default once that is fixed
        done_ = set()                        # once per pair of pieces (a collar on its stand is sewn in several seams)
        for sm, dg, turn in (S.seam_folds(spec) if spec.get('collar_prefold') else []):
            other = sm['b']['piece'] if turn == sm['a']['piece'] else sm['a']['piece']
            if sm['a']['piece'] == sm['b']['piece'] or abs(dg) < 90.0 or turn not in names_ or other not in names_ or (turn, other) in done_:
                continue
            done_.add((turn, other))
            if pl_.get(turn, {}).get('guide') != 'neck' or pl_.get(other, {}).get('guide') != 'neck':
                continue
            i, j = names_.index(turn), names_.index(other)
            if i in took:
                continue
            roll = np.unique(np.r_[st_[(st_[:, 0] == i) & (st_[:, 2] == j), 1],
                                   st_[(st_[:, 2] == i) & (st_[:, 0] == j), 3]])
            if len(roll) < 3:
                continue
            # at the seam's set angle, as the collars on a fold line (was 180 = flat back on the stand)
            ang_ = float(spec.get('collar_prefold_deg', abs(dg)))
            X[i], nf = prefold(X[i], pat[f"V{i}"], roll, roll, np.asarray(spec['guides']['neck']['o'], float)[:2],
                               fall=np.arange(len(X[i])), angle_deg=ang_)
            rep2 = rep2 + [f"  prefolded {turn}: {nf} of {len(X[i])} vertices over its seam to {other} "
                           f"({len(roll)} vertices, {ang_:.1f} deg)"]
    # TEST (user 2026-10-07, "A3"): spec['collar_preassemble'] -- the collar placed already sewn to its stand: the
    # collar is moved so that each of its stitched vertices sits on its stand partner, the move spread over the rest
    # of the collar by inverse distance in the flat layout (placed 34 mm apart at the median, 66 mm at most, the
    # stand-collar seam turned over while that gap closed, Fable r10)
    if spec.get('collar_preassemble'):
        names_ = [str(n) for n in pat['names']]
        pl_ = spec.get('placement', {})
        st_ = pat['stitch']
        nk_ = [i for i, n in enumerate(names_) if pl_.get(n, {}).get('guide') == 'neck']
        if len(nk_) >= 2:
            out_ = {i: int((((st_[:, 0] == i) & ~np.isin(st_[:, 2], nk_)) | ((st_[:, 2] == i) & ~np.isin(st_[:, 0], nk_))).sum())
                    for i in nk_}
            stand_ = max(nk_, key=lambda i: out_[i])
            for i in nk_:
                if i == stand_ or i in took:
                    continue
                pr_ = [(int(r[1]), int(r[3])) for r in st_ if r[0] == i and r[2] == stand_] + \
                      [(int(r[3]), int(r[1])) for r in st_ if r[2] == i and r[0] == stand_]
                if len(pr_) < 3:
                    continue
                vi_ = np.array([a for a, _ in pr_]); vj_ = np.array([b for _, b in pr_])
                Dv = np.asarray(X[stand_], float)[vj_] - np.asarray(X[i], float)[vi_]
                Fl = np.asarray(pat[f"V{i}"], float)
                dd = np.linalg.norm(Fl[:, None, :] - Fl[vi_][None, :, :], axis=2) + 1e-6
                w_ = 1.0 / dd ** 2
                X[i] = np.asarray(X[i], float) + (w_ @ Dv) / w_.sum(1, keepdims=True)
                rep2 = rep2 + [f"  preassembled {names_[i]} on {names_[stand_]}: {len(pr_)} stitches, moved "
                               f"p50 {np.median(np.linalg.norm(Dv, axis=1)):.1f} mm"]
    # small parts placed already sewn to the pieces they belong to (Fable r2, 2026-10-09; the collar's preassemble
    # made general): a piece with 70 % or more of its stitches to other pieces going to pieces on its own guide, at
    # most PRE_AREA of their area together, and sewn along an edge (at most half its outline stitched) -- a cuff on
    # its two-piece sleeve (11 %; 30 + 12 of its 46 stitches go to the sleeve's two pieces). It is moved as a whole,
    # its stitched vertices as near their partners as a rotation and a shift take them. Placed by the guide, the cuffs
    # went 8 mm outside the sleeve as a layer over it (over 1, r 106.5); sewn from there the stitches crossed the
    # sleeve (cuff x sleeve 83 pairs). A part lying on a partner's face (a sleeve placket: 31 of its 47 outline
    # vertices stitched) is left as placed: carried by inverse distance it cut through the curved sleeve (43 pairs).
    # Neck pieces keep the collar's own switch; closures and bands are left as placed.
    # Opt-in for now (spec['preassemble'] = True): on by default it would also place a trouser pocket on its panel.
    if spec.get('preassemble'):
        names_ = [str(n) for n in pat['names']]
        pl_ = spec.get('placement', {})
        st_ = pat['stitch']
        shut_ = set().union(*[set(c) for c in roles.declared_closures(spec, names_)])

        def area_(i):
            Vb_ = pat[f"V{i}"][:int(pat['nb'][i])]
            return abs(0.5 * float((Vb_[:, 0] * np.roll(Vb_[:, 1], -1) - np.roll(Vb_[:, 0], -1) * Vb_[:, 1]).sum()))
        todo = []
        for i, n in enumerate(names_):
            gi = pl_.get(n, {}).get('guide')
            if gi in (None, 'neck') or n in shut_ or n in rings or i in took:
                continue
            m_ = (((st_[:, 0] == i) & (st_[:, 2] != i)) | ((st_[:, 2] == i) & (st_[:, 0] != i)))
            if m_.sum() < 3:
                continue
            other = np.where(st_[m_, 0] == i, st_[m_, 2], st_[m_, 0])
            same = np.array([pl_.get(names_[o], {}).get('guide') == gi for o in other])
            mine = np.where(st_[m_, 0] == i, st_[m_, 1], st_[m_, 3])
            if same.mean() < 0.7 or len(np.unique(mine)) > 0.5 * int(pat['nb'][i]):
                continue
            # the partners are the larger pieces (a cuff onto the sleeve, not onto the placket sewn to its end: carried
            # to the placket's place as well, the cuff stretched 16 x and went 42 vertices into the wrist)
            partners = sorted(j for j in set(other[same].tolist()) if area_(j) > area_(i))
            if not partners:
                continue
            if area_(i) > PRE_AREA * sum(area_(j) for j in partners):
                continue
            todo.append((area_(i), i, partners))
        for _, i, partners in sorted(todo, reverse=True):
            pr_ = [(int(r[1]), int(r[2]), int(r[3])) for r in st_ if r[0] == i and r[2] in partners] +                   [(int(r[3]), int(r[0]), int(r[1])) for r in st_ if r[2] == i and r[0] in partners]
            vi_ = np.array([a for a, _, _ in pr_])
            Q_ = np.array([np.asarray(X[j], float)[b] for _, j, b in pr_])
            P_ = np.asarray(X[i], float)[vi_]
            Dv = Q_ - P_
            # moved as a whole (best rotation + shift of its stitched vertices onto their partners, Kabsch), so it keeps
            # the shape it was wrapped to: carried by inverse distance, a cuff stretched 15 x (its far edge stayed)
            pc_, qc_ = P_.mean(0), Q_.mean(0)
            U_, _, Wt_ = np.linalg.svd((P_ - pc_).T @ (Q_ - qc_))
            Rk = Wt_.T @ np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt_.T @ U_.T))]) @ U_.T
            X[i] = (np.asarray(X[i], float) - pc_) @ Rk.T + qc_
            rep2 = rep2 + [f"  preassembled {names_[i]} on {', '.join(names_[j] for j in partners)}: {len(pr_)} "
                           f"stitches, moved p50 {np.median(np.linalg.norm(Dv, axis=1)):.1f} mm"]
    # TEST (user decision 2026-09-30, DECISIONS cloth-sim-auto-prefold-2026-09-30; default once measured): an internal
    # line folded 90 degrees or more on a torso piece (a back vent, a lapel) is folded before simulating, as the
    # collar is: left to the solver the vent turned from flat towards 134 degrees in the 2 mm between the garment and
    # the body and stuck half way (foundation_2026-09-30 RESULT_G3 note 22, Fable r12). The side that turns: the one
    # (within the line's length) with fewer vertices sewn to other pieces, then the smaller; + angle (mountain seen from the pattern's face) turns it in while the face looks out (face_out).
    # settings prefold_torso (was env AC9_SIM_PREFOLD) = 'all' (all such lines) or a list of "piece|line"
    pf_set = cs['prefold_torso']
    if (pf_set or spec.get('prefold_folds')) and spec.get('guides', {}).get('torso'):
        want_ = None if pf_set in (None, 'all') else set(pf_set)
        names_ = [str(n) for n in pat['names']]
        pl_ = spec.get('placement', {})
        deg = {(f['piece'], f['line']): float(f.get('deg') or 0.0) for f in spec.get('folds', [])
               if f.get('line') and f.get('side') != 'NONE'}
        st_ = pat['stitch']
        ax_t = np.asarray(spec['guides']['torso']['o'], float)[:2]
        gap_pf = float(spec.get('prefold_gap_mm', 3.0))
        for i, n in enumerate(names_):
            if pl_.get(n, {}).get('guide') != 'torso' or i in took:
                continue
            Lf_ = pat[f"Lf{i}"]
            for fid, m in enumerate(pat['fold_meta']):
                pc_, ln_ = str(m).split('|')[:2]
                dg_ = deg.get((pc_, ln_), 0.0)
                if pc_ != n or abs(dg_) < 90.0 or (want_ is not None and f"{pc_}|{ln_}" not in want_):
                    continue
                roll, fall = fold_flap(pat, i, fid, tris[i])
                if len(roll) < 3 or not len(fall):
                    continue
                fl_ = pat[f"V{i}"]
                ka = int(np.isin(fall, np.r_[st_[(st_[:, 0] == i) & (st_[:, 2] != i), 1],
                                             st_[(st_[:, 2] == i) & (st_[:, 0] != i), 3]]).sum())
                stay = np.setdiff1d(np.arange(len(fl_)), np.r_[fall, roll])
                fo_ = face_out(X[i], tris[i], roll, ax_t, fall)
                in_ = (dg_ > 0) == fo_
                if not fo_:
                    rep2 = rep2 + [f"  {n}|{ln_}: its face looks at the body by the line: folded round the body as if "
                                   f"on it, the shape may come out bent"]
                X[i], nf = prefold(X[i], fl_, roll, stay[:1], ax_t, gap=gap_pf, fall=fall, inward=in_)
                rep2 = rep2 + [f"  prefolded {n}|{ln_} ({dg_:+.1f} deg, {'in' if in_ else 'out'}): {nf} of "
                               f"{len(X[i])} vertices, sewn to others on that side {ka}"]
    # mirror-meshed twins: the half on -x starts as the exact mirror image of the +x half. spec['symmetry'] per garment:
    # 'place' (default) = the mirrored start only, 'twins' = also kept a mirror image every frame by the solver,
    # 'off' = no twins at all (S.twins). Default 'place' since 2026-09-30 (user decision, DECISIONS
    # cloth-sim-mirror-default-off-2026-09-30): with the converged solver the every-frame copy lifted a jacket's
    # backs 50-100 mm even from the reference drape (foundation_2026-09-30/RESULT_G3 notes 10-14); 'twins' was the default
    # from 2026-09-29
    # A piece moved by hand (its placed object) leads its pair; the mirror is about the body's center plane x = cx
    # (the torso guide's axis; was x = 0, the same for an avatar standing on the origin). A pair set apart by
    # Unlink is not mirrored (S.linked_twins)
    mpairs = []
    sym = spec.get('symmetry', 'place')
    ts = np.asarray(pat.get('twin_src', -np.ones(len(pat['names']), np.int32)))
    linked = {frozenset(p) for p in S.linked_twins(spec)}
    nm_ = [str(n) for n in pat['names']]
    if sym != 'off':
        for i, j in enumerate(ts):
            if j < 0:
                continue
            j = int(j)
            if frozenset((nm_[i], nm_[j])) not in linked:
                continue
            if (i in took) != (j in took):
                src, tgt = (i, j) if i in took else (j, i)
            else:
                src, tgt = (i, j) if X[i][:, 0].mean() > X[j][:, 0].mean() else (j, i)
            X[tgt] = X[src] * np.array([-1.0, 1.0, 1.0]) + np.array([2.0 * cx, 0.0, 0.0])
            mpairs.append((src, tgt))
    if shapes_only:
        out = {'pat': pat, 'tris': tris, 'X': X, 'pairs': list(mpairs), 'rep': rep2, 'short_bands': short_bands,
               'overlaps': overlaps, 'in_body': dict(IN_BODY_LAST),
               'closures': dict(CLOSURE_LAST),
               'ring_used': {str(n): RING_USED[str(n)] for n in pat['names'] if str(n) in RING_USED}}
        if pkey is not None:
            _shapes_put(pkey, out)
        return out
    pairs0 = list(mpairs)
    if sym != 'off':
        if mpairs and sym == 'place':
            rep2 = rep2 + [f"  mirrored start (not kept every frame): " + ", ".join(
                f"{pat['names'][t]} <- {pat['names'][s_]}" for s_, t in mpairs)]
            mpairs = []
        if mpairs:
            rep2 = rep2 + [f"  mirrored every frame: " + ", ".join(f"{pat['names'][t]} <- {pat['names'][s_]}"
                                                                for s_, t in mpairs)]
    if xinit_from:
        # DIAGNOSIS: 'pieces' limits the swap to some pieces (the rest keep this placement), to find which group
        # of pieces makes the difference
        sel = xinit_from.get('pieces')
        Xr = _xinit_from(xinit_from, pat, X)
        nm = [str(n) for n in pat['names']]
        X = [Xr[i] if (sel is None or nm[i] in sel) else X[i] for i in range(len(nm))]
        ref_rings = xinit_from.get('rings', rings)
        rings = ref_rings if sel is None else [n for n in rings if n not in sel] + [n for n in ref_rings if n in sel]
        rep2 = rep2 + [f"  (initial shape taken from a reference for {'all pieces' if sel is None else ', '.join(sel)})"]
    carried = []
    from_h = None
    if continue_job:
        X, carried = _from_last_result(continue_job, pat, X, continue_shown, redo=set(redo),
                                       redo_shown=redo_shown and continue_shown is not None)
        nm_n = [str(n) for n in pat['names']]
        new_placed = new_placed or {}
        new_h = [n for n in redo if n in new_placed and n in nm_n and n not in carried]
        for n in new_h:                   # not in the last result: from the shape flush_placed kept (Part to Seam)
            i = nm_n.index(n)
            Vp, Tp, Xp = new_placed[n]
            V = np.asarray(pat[f"V{i}"], float)
            X[i] = Xp.copy() if len(V) == len(Vp) and np.allclose(V, Vp, atol=1e-6) else _bary_map(V, Vp, Tp, Xp)
        if new_h:
            rep2 = rep2 + [f"  not in the last result, from their placed shapes: {', '.join(sorted(new_h))}"]
        if redo and redo_shown and continue_shown is not None:
            rep2 = rep2 + [f"  put back by Reset, from the garment's shape: {', '.join(sorted(redo))}"]
        elif redo:
            pl_h = spec.get('placement', {})
            nm_h = [str(n) for n in pat['names']]
            X, took_r, rep_h = _hand_placed(spec, pat, X, bvh, tris, skip=lambda i: None if str(
                pl_h.get(nm_h[i], {}).get('guide', '')).startswith('arm') else arm_faces, only=set(redo))
            X = _mirror_hand(X, took_r, [p for p in pairs0 if nm_h[p[0]] in redo and nm_h[p[1]] in redo], cx)
            rep2 = rep2 + [f"  put back by Reset, from their placed shapes: {', '.join(sorted(redo))}"] + rep_h
        try:
            pz_ = np.load(os.path.join(continue_job, 'scene.idx.npz'))
            from_h = _mesh_h(pz_['flat'], pz_['F'])
        except (OSError, KeyError):
            from_h = None
        new = [str(n) for n in pat['names'] if str(n) not in carried]
        rep2 = rep2 + [f"  (continued from {os.path.basename(continue_job)}; new pieces: {', '.join(new) or 'none'})"]
    names = [str(n) for n in pat['names']]
    offs, F_all, fp, flat = [], [], [], []
    o = 0
    for i in range(len(names)):
        offs.append(o)
        F_all.append(tris[i] + o)
        fp.append(np.full(len(tris[i]), i))
        flat.append(pat[f"V{i}"])
        o += len(pat[f"V{i}"])
    F_all, fp = np.vstack(F_all), np.concatenate(fp)
    flat, Xinit = np.vstack(flat), np.vstack(X)
    st = pat['stitch']
    E_st = np.stack([np.array(offs)[st[:, 0]] + st[:, 1], np.array(offs)[st[:, 2]] + st[:, 3]], 1)
    d = job_dir(context)
    if continue_job and os.path.abspath(d) == os.path.abspath(continue_job):
        d += '_next_' + str(time.time_ns())
        os.makedirs(d, exist_ok=False)
    mirror_map = None
    if mpairs:
        tg, sr = [], []
        for s_, t in mpairs:
            n_ = len(pat[f"V{t}"])
            tg.append(offs[t] + np.arange(n_))
            sr.append(offs[s_] + np.arange(n_))
        tg, sr = np.concatenate(tg), np.concatenate(sr)
        mirror_map = os.path.join(d, 'mirror_map.npz')
        np.savez(mirror_map, tgt=tg.astype(np.int64), src=np.repeat(sr[:, None], 3, 1).astype(np.int64),
                 w=np.tile(np.array([1.0, 0.0, 0.0], np.float32), (len(tg), 1)))
    if any(ts_ >= 0 for ts_ in ts):
        # a pair set apart by Unlink is meshed mirrored but is not a pair for the solver (its shared push-out)
        ts2 = np.array(ts, copy=True)
        for i, j in enumerate(ts):
            if j >= 0 and frozenset((nm_[i], nm_[int(j)])) not in linked:
                ts2[i] = -1
        pat = dict(pat, twin_src=ts2)
    np.savez(os.path.join(d, 'pattern.npz'), **pat)
    extra = {'E_seam': pat['stitch_seam']} if 'stitch_seam' in pat else {}      # the spec seam id of each stitch
    if 'stitch_off' in pat and len(pat['stitch_off']):
        # the switched-off seams' stitches: springs a Live Sim holds at no stiffness, to sew one on while it runs
        so_ = pat['stitch_off']
        extra['E_st_off'] = np.stack([np.array(offs)[so_[:, 0]] + so_[:, 1], np.array(offs)[so_[:, 2]] + so_[:, 3]], 1)
        extra['E_seam_off'] = pat['stitch_off_seam']
    np.savez(os.path.join(d, 'scene.idx.npz'), offs=np.array(offs), E_st=E_st, fp=fp, Xclo=Xinit, Xinit=Xinit,
             flat=flat, F=F_all, **extra)
    np.savez(os.path.join(d, 'avatar.npz'), body_co=(co * 0.001).astype(np.float64), body_tri=tri)
    crop = [float(co[:, 2].min() - 10), float(co[:, 2].max() + 10), 10000.0]

    # spec['tacks'] (TEST, 2026-10-09): points held together that are not seams -- a waistband's two ends buttoned at
    # the front, which a BOM does not carry (buttons are only in the source's project file). [{"a": piece, "pa": [x, y] layout mm,
    # "b": piece, "pb": [x, y]}], each the nearest mesh vertex of its piece; held by the solver's temp_stitches in
    # every stage. Without it the waistband's front stayed open in every run while its seams all closed (user 10/09)
    tacks_path = None
    if spec.get('tacks'):
        tk_ = []
        for t_ in spec['tacks']:
            if t_['a'] not in names or t_['b'] not in names:
                continue
            ia_, ib_ = names.index(t_['a']), names.index(t_['b'])
            Va_, Vb_ = np.asarray(pat[f"V{ia_}"], float), np.asarray(pat[f"V{ib_}"], float)
            va_ = int(np.argmin(np.linalg.norm(Va_ - np.asarray(t_['pa'], float), axis=1)))
            vb_ = int(np.argmin(np.linalg.norm(Vb_ - np.asarray(t_['pb'], float), axis=1)))
            if (ia_, va_) != (ib_, vb_):
                tk_.append((offs[ia_] + va_, offs[ib_] + vb_))
        if tk_:
            tacks_path = os.path.join(d, 'tacks.npy')
            np.save(tacks_path, np.unique(np.asarray(tk_, np.int64), axis=0))
            rep2 = rep2 + [f"  tacks: {len(tk_)} points held together (spec tacks)"]

    # spec['buttons'] (2026-10-15, core/buttons.py): each fastened button held to its hole as a tack is (the nearest
    # mesh vertex of each), by the stages' temp_stitches; one on a front closure only in the stages that sew it
    # spec['zippers'] (2026-10-15, core/zippers.py): the closed part's teeth held the same way, every pitch mm
    btn_ = []
    zip_ = ZP.entries(spec, names)
    for t_ in BT.entries(spec, names) + zip_:
        ia_, ib_ = names.index(t_['a']), names.index(t_['b'])
        Va_, Vb_ = np.asarray(pat[f"V{ia_}"], float), np.asarray(pat[f"V{ib_}"], float)
        va_ = int(np.argmin(np.linalg.norm(Va_ - np.asarray(t_['pa'], float), axis=1)))
        vb_ = int(np.argmin(np.linalg.norm(Vb_ - np.asarray(t_['pb'], float), axis=1)))
        if (ia_, va_) != (ib_, vb_):
            btn_.append({'ij': (offs[ia_] + va_, offs[ib_] + vb_), 'a': t_['a'], 'b': t_['b'], 'id': t_['id']})
    if btn_:
        nz_ = sum(1 for r_ in btn_ if ':' in str(r_['id']))
        rep2 = rep2 + [f"  buttons: {len(btn_) - nz_} fastened (spec buttons)"] * (len(btn_) > nz_) +             [f"  zippers: {nz_} teeth held of {len(zip_)} closed (spec zippers)"] * (nz_ > 0)

    # spec seam 'path' (TEST, 2026-10-14, core/seam_path.py): seams pulled round the body in the sewing stages
    sp_path = os.path.join(d, 'seam_paths.npz')
    sp_ids = SPATH.write_rows(sp_path, spec, names, E_st, pat['stitch_seam'] if 'stitch_seam' in pat else None,
                              np.load(tacks_path) if tacks_path else None, offs)
    if sp_ids:
        rep2 = rep2 + [f"  pulled round the body: {len(sp_ids)} seam(s) ({', '.join(sp_ids)})"]

    # spec['attach'] (2026-10-18, core/attach.py): garment lines pulled to tapes on the body
    att_path, att_on = None, []
    att_rows = attach_rows(spec, names, pat, offs, E_st, props.body, co_body, tri_body, rep2)
    if att_rows:
        att_path = os.path.join(d, 'attach.npz')
        AT.write(att_path, [r_[1] for r_ in att_rows])
        att_on = [r_[0] for r_ in att_rows]

    # spec['elastic'] (S3, 2026-10-13): stretches of cloth whose rest length is shorter than the pattern (a waistband,
    # a cuff, shirring), core/elastic.py; every stage the piece is in, shortening over a few frames where it comes in
    el_notes = []
    el_entries = EL.solver_entries(spec, names, el_notes)
    rep2 = rep2 + [f"  {n_}" for n_ in el_notes]
    if el_entries:
        rep2 = rep2 + [f"  elastic: {len(el_entries)} (spec elastic: " + ', '.join(
            f"{e_['piece']}{' line' if 'line' in e_ else ''} x{e_['ratio']:g}" for e_ in el_entries) + ")"]

    # each piece's own fabric / interlining (core/fabric.py, user 2026-10-14): cfg fabric_pieces, only with the
    # measured fabric through mi (the per-hinge bending is the mi formula); none set = the solver input as before
    fab_notes = fab_mig + [f"fabric: the default {cs['default_fabric']} ({cs['weight_gsm']:g} g/m2, "
                           f"{cs['bending_uNm']:g} uN m, stretch {cs['membrane'][0]:g}/{cs['membrane'][1]:g}, "
                           f"shear {cs['membrane'][2]:g} N/m, bending hysteresis {cs['kappa_y']:g} /m)"]
    fab_map = FA.solver_pieces(spec, names, cs['weight_gsm'], cs['bending_uNm'], fab_notes)
    if fab_map and not (cs['physical'] and cs.get('mesh_independent')):
        fab_notes = [f"fabric: {len(fab_map)} piece(s) have their own fabric, not used: it needs Physical "
                     f"(the measured fabric)"]
        fab_map = {}
    rep2 = rep2 + [f"  {n_}" for n_ in fab_notes]

    # folds set to one layer (spec fold "layers", user 2026-10-24): the side taken out is bound to the side kept
    ol_notes = []
    ol_rows = one_layer_rows(spec, names, pat, tris, offs, ol_notes)
    if ol_rows:
        rep2 = rep2 + [f"  one layer: " + ', '.join(f"{r_['piece']} {r_['line']} (keeps {r_['keep']}, "
                                                    f"{len(r_['verts'])} vertices bound)" for r_ in ol_rows)]
    rep2 = rep2 + [f"  {n_}" for n_ in ol_notes]

    el_fresh = [False]                  # the plan of a Phase Sim from a frame is being made (its elastic as a first run's)

    def extras(k, cfg):
        """Per-stage additions that need this job's folder and meshed pattern (presets.build_stages calls it)."""
        held_ = [r_['ij'] for r_ in btn_ if BT.held_in_stage(cfg, r_['a'], r_['b'])]
        if held_:
            rows_ = [np.load(tacks_path)] if tacks_path else []
            rows_.append(np.asarray(held_, np.int64))
            p_ = os.path.join(d, f"temp_stitches_s{k + 1}.npy")
            np.save(p_, np.unique(np.concatenate(rows_), axis=0))
            cfg['temp_stitches'] = p_
        elif tacks_path:
            cfg['temp_stitches'] = tacks_path
        if sp_ids:
            cfg['seam_paths'] = {'file': sp_path}
            SPATH.drop_holds(cfg, spec, sp_ids)
        if att_path:
            gs_ = [g_ for g_, a_ in enumerate(att_on) if AT.stage_on(a_, cfg)]
            if gs_:
                cfg['attach'] = {'file': att_path, 'groups': gs_, 'pull_N_m': AT.PULL_N_M, 'stiff_N_m2': AT.STIFF_N_M2,
                                 'mass_frac': AT.MASS_FRAC}
        if k > 0:
            cfg['init_from'] = os.path.join(d, f"s{k}", 'frames.npy')
        cfg['grab_file'] = os.path.join(d, 'grab.json')
        if cfg.get('live'):
            # the values changed while it runs (the modal writes them, recipe.live_params); one left from an
            # earlier Live in this folder would be read as a change at its first frame
            cfg['params_file'] = os.path.join(d, 'params.json')
            if os.path.exists(cfg['params_file']):
                os.remove(cfg['params_file'])
            if not (props.local_gravity if local is not None else props.live_gravity):
                cfg['gravity_z'] = 0.0
        # TEST (user decision 2026-09-30 night, DECISIONS cloth-sim-fold-bake-2026-09-30): a fold that never opens
        # again (a back vent pressed and stitched down) is taken out of the simulation: its turned side follows the
        # piece as a second layer (bound to the nearest triangle of the rest of the piece when the stage starts), so
        # no hinge fights to fold it -- its 134 degree hinge lifted the backs' hem ~10 mm (RESULT_G3 note 23).
        # settings fold_bake (was env AC9_SIM_FOLD_BAKE) = list of "piece|line"
        # TEST: settings fold_free (was env AC9_SIM_FOLD_FREE) = list of "piece|line": a pressed fold left free along its line after it is
        # folded at placement (cfg fold_free, Fable r15)
        if cs['fold_free']:
            cfg['fold_free'] = list(cs['fold_free'])
        if cs['fold_bake']:
            bv_ = []
            for pl_ in cs['fold_bake']:
                pn_, ln_ = pl_.split('|')
                if pn_ not in names:
                    continue
                i_ = names.index(pn_)
                fid_ = _fold_fid(pat, pn_, ln_)
                if fid_ is None:
                    continue
                _, fall_ = fold_flap(pat, i_, fid_, tris[i_])
                if len(fall_):
                    bv_.append({'piece': pn_, 'line': ln_, 'verts': (offs[i_] + fall_).tolist()})
            if bv_:
                cfg['bind_verts'] = bv_
        if ol_rows:
            cfg['one_layer'] = ol_rows
        # TEST: settings stack_under (was env AC9_SIM_STACK_UNDER) = list of "piece|line>piece|line": the part the first line cuts off is
        # placed under the part the second line cuts off (the back's vent flap) before the first stage, point for
        # point: same position along the line, same distance off it (the reference drape has the two strips stacked so:
        # along r 0.98-0.99, 5 mm apart, the experiment's vent map), 3.5 mm towards the body (cfg stack_under)
        if cs['stack_under'] and k == 0:
            st_ = []
            for rule_ in cs['stack_under']:
                lo_s, up_s = [x_.strip() for x_ in rule_.split('>')]
                (pe_, le_), (ph_, lh_) = lo_s.split('|'), up_s.split('|')
                if pe_ not in names or ph_ not in names:
                    continue
                ie_, ih_ = names.index(pe_), names.index(ph_)
                re_, fe_ = fold_flap(pat, ie_, _fold_fid(pat, pe_, le_), tris[ie_])
                rh_, fh_ = fold_flap(pat, ih_, _fold_fid(pat, ph_, lh_), tris[ih_])
                Ve_, Vh_ = pat[f"V{ie_}"], pat[f"V{ih_}"]

                def frame_(V_, roll_):
                    c_ = V_[roll_].mean(0); u_ = np.linalg.svd(V_[roll_] - c_, full_matrices=False)[2][0]
                    return c_, u_, np.array([-u_[1], u_[0]])
                ce_, ue_, we_ = frame_(Ve_, re_); ch_, uh_, wh_ = frame_(Vh_, rh_)
                # the two lines run the same way when their ends sewn together (the vent's top) are at the same end
                sw_ = [(int(a_), int(b_)) for pa_, a_, pb_, b_ in pat['stitch'] if pa_ == ie_ and pb_ == ih_] + \
                      [(int(b_), int(a_)) for pa_, a_, pb_, b_ in pat['stitch'] if pa_ == ih_ and pb_ == ie_]
                if sw_:
                    ae_ = np.mean([(Ve_[a_] - ce_) @ ue_ for a_, _ in sw_]); ah_ = np.mean([(Vh_[b_] - ch_) @ uh_ for _, b_ in sw_])
                    if ae_ * ah_ < 0:
                        uh_ = -uh_
                sh_ = np.sign(np.mean((Vh_[fh_] - ch_) @ wh_))
                vv_ = np.unique(np.concatenate([fe_, re_]))
                Pe_ = Ve_[vv_] - ce_
                Q_ = ch_ + np.outer(Pe_ @ ue_, uh_) + np.outer(sh_ * np.abs(Pe_ @ we_), wh_)
                fs_ = set(np.concatenate([fh_, rh_]).tolist())
                Th_ = np.array([t_ for t_ in tris[ih_] if set(t_.tolist()) <= fs_])
                A_, B_, C_ = Vh_[Th_[:, 0]], Vh_[Th_[:, 1]], Vh_[Th_[:, 2]]
                tri_, w_ = [], []
                for q_ in Q_:
                    v0, v1, v2 = B_ - A_, C_ - A_, q_ - A_
                    den = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
                    w1 = (v2[:, 0] * v1[:, 1] - v2[:, 1] * v1[:, 0]) / den
                    w2 = (v0[:, 0] * v2[:, 1] - v0[:, 1] * v2[:, 0]) / den
                    W_ = np.stack([1 - w1 - w2, w1, w2], 1)
                    kk_ = int(np.argmax(W_.min(1)))                  # containing triangle, else the nearest one
                    wk_ = np.clip(W_[kk_], 0, None); wk_ /= wk_.sum()
                    tri_.append((offs[ih_] + Th_[kk_]).tolist()); w_.append(wk_.tolist())
                st_.append({'name': rule_.strip(), 'verts': (offs[ie_] + vv_).tolist(), 'tri': tri_, 'w': w_,
                            'depth_mm': float(cs['stack_depth_mm'])})
            if st_:
                cfg['stack_under'] = st_
        # TEST: settings layer (was env AC9_SIM_LAYER) = list of "LOWER<UPPER", each a piece or "piece|line" (the part that line cuts
        # off, fold_flap): the lower part is kept under the upper one (cfg layer_order, the user's layer order)
        if cs['layer']:
            def part_(x_):
                pn_, _, ln_ = x_.strip().partition('|')
                if pn_ not in names:
                    return None
                i_ = names.index(pn_)
                if not ln_:
                    return offs[i_] + np.arange(len(pat[f"V{i_}"]))
                fid_ = _fold_fid(pat, pn_, ln_)
                return None if fid_ is None else offs[i_] + fold_flap(pat, i_, fid_, tris[i_])[1]
            lo_ = []
            for rule_ in cs['layer']:
                a_, b_ = rule_.split('<')
                pa_, pb_ = part_(a_), part_(b_)
                if pa_ is not None and pb_ is not None and len(pa_) and len(pb_):
                    lo_.append({'name': rule_.strip(), 'lower': np.asarray(pa_).tolist(), 'upper': np.asarray(pb_).tolist(),
                                'free_contact': bool(cs['layer_free']),
                                'mode': cs['layer_mode'],
                                **cs['layer_opts']})
            # the first stage only (the order is settled while the pieces are drawn together; then it is taken
            # off, user 2026-10-01); settings layer_all keeps it on every stage
            if lo_ and (k == 0 or cs['layer_all']):
                cfg['layer_order'] = lo_
        if mirror_map:
            cfg['mirror_map'], cfg['mirror_x0'] = mirror_map, cx
        if fab_map and (cfg.get('mi') or {}).get('D'):
            cfg['fabric_pieces'] = fab_map
        if el_entries:
            # a Phase Sim from a frame runs its own stages: their elastic comes in as in the first run
            el_ = EL.for_stage(el_entries, cfg, EL.carried_pieces(cfg, k, names, () if el_fresh[0] else carried))
            if el_:
                cfg['elastic'] = el_
        # TEST ONLY: settings ease_w_mm (was env AC9_SIM_EASE_W) = w (mm) eases every seam whose sides differ by 2-43 % (the longer side's rest
        # shape shrunk along the seam to 1 / ratio at the seam, back to 1 at w mm; REVIEW_fable_G4_design, f = 0)
        if cs['ease_w_mm'] and 'ease_json' in pat:
            ew = float(cs['ease_w_mm'])
            cfg['ease'] = [_ease_entry(e_, ew) for e_ in json.loads(str(pat['ease_json'])) if e_['piece'] in names]
            cfg['ease'] = [e_ for e_ in cfg['ease'] if e_]

    # every stage's solver config, from the recipe and the settings set, in one place (core/presets.py)
    pf_info = pf_rep = None
    if phase_at and continue_job and not live and local is None:
        # Phase Sim from a frame of a Phase Sim (core/phase_from.py, user 2026-11-04): its plan made again as a first
        # run would make it, cut at that stage and frame. A piece put back by Reset starts where the person left it
        # (not laid on its edge again). The plan changed since (other roles, pieces on or off): the Continue stages
        el_fresh[0] = True
        kind, stg = presets.build_stages(spec, names, rings, os.path.join(d, 'pattern.npz'), crop, h, cs,
                                         extras=extras, hand=sorted(set(redo) & set(names)))
        el_fresh[0] = False
        plan = [s_[0] for s_ in stg]
        if plan == list(phase_at['plan']):
            k_, j_ = int(phase_at['stage']), int(phase_at['frame'])
            stg, dropped_ = PHF.cut(stg, k_, j_, lambda i_: os.path.join(d, f"s{i_ + 1}", 'frames.npy'))
            pf_info = {'stage': k_, 'frame': j_, 'job': phase_at['job'], 'timeline_frame': phase_at['timeline_frame'],
                       'prefix': int(phase_at['sample'])}
            pf_rep = (f"  Phase Sim from frame {phase_at['timeline_frame']}: stage {k_ + 1} of {len(plan)}"
                      + (f" from its frame {j_}" if j_ else "") + f", then {len(plan) - k_ - 1} more"
                      + (f"; started without its first-frame steps ({', '.join(dropped_)})" if dropped_ else ""))
        else:
            pf_rep = "  the Phase Sim's steps changed since that run: went on as Continue"
    if pf_info is None:
        prev_cfg = _last_stage_config(continue_job) if continue_job else None
        kind, stg = presets.build_stages(spec, names, rings, os.path.join(d, 'pattern.npz'), crop, h, cs, extras=extras,
                                         prev_cfg=prev_cfg, continue_=bool(continue_job),
                                         new=[n for n in names if n not in carried], live=live,
                                         hand=[names[i] for i in sorted(took) if not _laid(spec, names[i])])
    if pf_rep:
        rep2 = rep2 + [pf_rep]
    if local is not None:
        # Local Sim (user 2026-10-23, DECISIONS cloth-sim-phase-live-local-sim-2026-10-23): Live's one stage with only
        # the selected pieces moving; the others are left out as Off (All) (not simulated, nothing touches them), so
        # only the seams among the selected pull. Their folds come in over 30 frames from the angle they have
        # (fold_ramp), the bend hysteresis after them
        loc = [n_ for n_ in names if n_ in local]
        kind = 'local'
        for _label, cfg in stg:
            cfg['active_pieces'] = loc
            cfg['frozen_pieces'] = [n_ for n_ in cfg.get('frozen_pieces') or [] if n_ in loc]
            if cfg.get('ghost_pieces'):
                cfg['ghost_pieces'] = [n_ for n_ in cfg['ghost_pieces'] if n_ in loc]
            cfg.pop('mirror_map', None)
            cfg.pop('mirror_x0', None)
            # the velocities x 0.97 every frame (user OK 2026-10-24, Local Sim only): without gravity a piece pushed
            # off the body by its contact flew on for good (two chest pieces: 52 mm in 456 frames and still going;
            # with it 3.7 mm, stopped). An exception to "no damping in the main run" (2026-10-18)
            cfg['vel_damp'] = 0.97
            if any(k_.split('|')[0] in loc for k_ in cfg.get('fold_deg') or {}):
                cfg['fold_ramp'] = {'pieces': loc, 'frames': 30}
        stg = [("local (until stopped): " + ', '.join(loc), cfg) for _label, cfg in stg]
    for _label, cfg in stg:
        one_layer_for_stage(cfg)
    # the pieces' layers (spec piece "layer"; user 2026-10-25, DECISIONS on layers 2026-10-25): every
    # stage, until a person sets them back to 0 (cfg layers, the solver's cloth-cloth contact with a side)
    layers_ = {p_['name']: S.piece_layer(p_) for p_ in spec.get('pieces', [])
               if p_.get('name') in names and S.piece_layer(p_)}
    if layers_:
        for _label, cfg in stg:
            cfg['layers'] = dict(layers_)
    # the mesh: what this job runs on and, after a Continue on another Mesh Size, where the shape came from and how
    # it arrived (measured before the solver runs, as a placement is: Fable r3 §1-2)
    h_now = _mesh_h(flat, F_all)
    mesh = {'h_mm': round(float(h), 3), 'h_measured_mm': round(h_now, 3), 'mesher': pattern_build.MESHER_VERSION}
    if 'h_by_piece' in pat and float(np.ptp(pat['h_by_piece'])) > 1e-9:
        mesh['by_piece_mm'] = {n_: float(v_) for n_, v_ in zip(names, pat['h_by_piece']) if abs(v_ - h) > 1e-9}
    changed = from_h is not None and abs(from_h - h_now) > 0.05 * max(from_h, h_now)
    if continue_job:
        mesh.update(from_job=continue_job, from_h_mm=None if from_h is None else round(from_h, 3), changed=bool(changed))
        # a garment dressed with another mesher: its shape is carried onto this mesh as for another mesh size, not
        # stopped or asked (user 2026-10-23), and said in one line
        try:
            prev_m = json.load(open(os.path.join(continue_job, 'job.json'), encoding='utf-8')).get('mesh', {}).get('mesher', 1)
        except (OSError, ValueError):
            prev_m = None
        if prev_m is not None and prev_m != pattern_build.MESHER_VERSION:
            mesh['from_mesher'] = prev_m
            rep2 = rep2 + [f"  mesh remade (mesher {prev_m} -> {pattern_build.MESHER_VERSION}), the last shape carried onto it"]
    if changed:
        e_ = np.concatenate([F_all[:, [0, 1]], F_all[:, [1, 2]], F_all[:, [2, 0]]])
        r_ = (np.linalg.norm(Xinit[e_[:, 0]] - Xinit[e_[:, 1]], axis=1)
              / np.maximum(np.linalg.norm(flat[e_[:, 0]] - flat[e_[:, 1]], axis=1), 1e-9))
        g_ = np.linalg.norm(Xinit[E_st[:, 0]] - Xinit[E_st[:, 1]], axis=1) if len(E_st) else np.zeros(1)
        mesh['carried'] = {'stretch_p99': round(float(np.percentile(r_, 99)), 4), 'stretch_max': round(float(r_.max()), 4),
                           'seam_gap_p99_mm': round(float(np.percentile(g_, 99)), 3), 'seam_gap_max_mm': round(float(g_.max()), 3)}
        # the shape arrived with its seams closed: let the first stage stop once still, as the closing stage does
        if kind == 'continue' and stg and not stg[0][1].get('settle'):
            stg[0][1]['settle'] = {'mm': 0.1, 'frames': 10}
    # the end tapers the solver would stop on, left out with one line (recipe.fit_terminal, 2026-11-01): last, once
    # each stage's pieces, folds, layers and bindings are what it runs with
    taper_out = {}
    for _label, cfg in stg:
        taper_out.update(recipe.fit_terminal(cfg, local=local is not None))
    if taper_out:
        rep2 = rep2 + ["  end taper left out on " + ", ".join(f"{k_.replace('|', ' ')} ({w_})"
                                                            for k_, w_ in sorted(taper_out.items()))]
    if _fast_live:
        from .core import fast_live as FL
        FL.preflight(spec, cs)
        stg = [(label, FL.configure(cfg)) for label, cfg in stg]
        for _label, cfg in stg:
            FL.validate_config(cfg)
        _resume_gate['solver_contract'] = stg[0][1]['fast_live_contract']
        # Only the fully normalized effective configuration may match a saved state.
        if continue_job and not (redo or new_placed or xinit_from):
            _resume_dir = d + '_resume_' + str(time.time_ns())
            _resumed, _resume_note = live_resume.prepare(context, h, continue_job, continue_shown,
                _resume_dir, _resume_gate, job_last_frame)
            if _resumed is not None:
                return _resumed
        rep2 = rep2 + ['AC9 Fast Live: 2 substeps with a correction in each; static body']
    for k, (label, cfg) in enumerate(stg):
        sd = os.path.join(d, f"s{k + 1}")
        os.makedirs(sd, exist_ok=True)
        json.dump(cfg, open(os.path.join(sd, 'config.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    info = {'dir': d, 'kind': kind, 'mirror_pairs': [[names[a], names[b]] for a, b in mpairs], 'colliders': [ob.name for ob in cols] + [n_ for n_, _c, _t in obst_g], 'stages': [s[0] for s in stg], 'names': names, 'rings': rings,
            'verts': int(len(flat)), 'tris': int(len(F_all)), 'stitches': int(len(E_st)),
            'prep_s': round(time.time() - t0, 2), 'report': rep + rep2,
            'settings': cs, 'solver_env': presets.solver_env(cs, kind), 'mesh': mesh, 'taper_left_out': taper_out}
    if fab_map or fab_notes:
        info['fabric'] = {'pieces': fab_map, 'notes': fab_notes}
    if pf_info is not None:
        # where in its Phase Sim's plan this job starts (a later Phase Sim from one of its frames finds its place)
        info['phase_from'], info['plan_stages'] = pf_info, plan
    json.dump(info, open(os.path.join(d, 'job.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if _resume_gate is not None:
        live_resume.write_gate(d, _resume_gate)
    if _resume_note:
        info['state_resume'] = {'status': 'rebuilt', 'reason': _resume_note}
        json.dump(info, open(os.path.join(d, 'job.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    open(os.path.join(d, 'ac9_spec.json'), 'w', encoding='utf-8').write(S.dumps(store.get(context)[0]))  # what a
    #   later pattern edit is carried from (draft_tool.carry_to_3d, _carry_pending)
    what = {'continue': 'Continue', 'live': 'Live Sim'}.get(kind, 'Phase Sim')
    log(context, d, 'start', f"{what}: {len(names)} pieces, mesh {h_now:.1f} mm, {len(flat)} vertices, "
                             f"{len(stg)} stage(s)", job=d, mesh=mesh)
    if fab_notes:
        log(context, d, 'fabric', "; ".join(n_[len('fabric: '):] if n_.startswith('fabric: ') else n_
                                           for n_ in fab_notes), pieces=fab_map)
    if cs.get('plan'):
        log(context, d, 'roles', roles.plan_line(cs['plan']), ask=cs['plan'].get('ask'))
    if cs.get('laid'):
        log(context, d, 'lay', f"{', '.join(cs['laid'])}: sewn after the body, laid on its sewn edge "
                               f"(where it lands: the stage's run log)", pieces=cs['laid'])
    if kind in ('live', 'local'):
        edge_ = [pc['name'] for pc in store.get(context)[0]['pieces'] if pc['name'] in names
                 and roles.start_of(pc) == 'edge' and not _laid(spec, pc['name'])]
        if edge_:
            log(context, d, 'lay', f"{', '.join(edge_)}: On Edge starts from its placed shape in Live Sim "
                                   f"(Band on Edge lays it first)", pieces=edge_)
    if cs.get('lay_skipped'):
        log(context, d, 'lay', "; ".join(f"{n}: {w}" for n, w in cs['lay_skipped'].items()),
            pieces=list(cs['lay_skipped']), level='warn')
    # a hint, not a stop (user 2026-10-08): a piece meshed so finely that one substep's iterations cannot carry a
    # pull across it (iterations x h < L / 8, L the piece's flat diagonal; r2/r3: the jacket body, about 500 mm
    # across, dressed at 2 mm crumpled (L / (20 h) = 12), at 4 mm did not (6)).
    # Only for a Dress from the pattern; a Continue starts from a shape and moves little
    if kind != 'continue' and kind != 'live' and stg:
        it_ = int(stg[0][1].get('iterations', 10))
        hp_ = pat['h_by_piece'] if 'h_by_piece' in pat else np.full(len(names), float(h))
        tight = []
        for i_, n_ in enumerate(names):
            V_ = pat[f"V{i_}"]
            L_ = float(np.linalg.norm(V_.max(0) - V_.min(0)))
            if float(hp_[i_]) * it_ * 8 < L_:
                tight.append(f"{n_} ({L_:.0f} mm at {float(hp_[i_]):g} mm)")
        if tight:
            log(context, d, 'hint', "dressing straight at this mesh may be outside the tested range for "
                                    + ", ".join(tight[:4]) + (" ..." if len(tight) > 4 else "")
                                    + ": if the cloth crumples, dress coarser and Phase Sim at this mesh",
                pieces=tight, iterations=it_)
    if mesh.get('by_piece_mm'):
        log(context, d, 'mesh', "own mesh: " + ", ".join(f"{n_} {v_:g} mm" for n_, v_ in mesh['by_piece_mm'].items())
            + f" (the rest {float(h):g} mm)", by_piece_mm=mesh['by_piece_mm'])
    if changed:
        c_ = mesh['carried']
        log(context, d, 'mesh', f"mesh {from_h:.1f} -> {h_now:.1f} mm: the shape carried over (stretch max "
                                f"{c_['stretch_max']:.3f}, seam gap max {c_['seam_gap_max_mm']:.1f} mm); the first "
                                f"stage stops once still", **mesh)
    return info, (flat, F_all, fp, Xinit, E_st)


# ---------------------------------------------------------------- the garment object

# what the add-on puts on the garment object: taken off when its mesh is replaced (a new run's mesh is not the one
# they were made for); a person's own modifiers, parent, collection and transform's parent stay (Fable review
# 2026-10-20 §1)
ADDON_PROPS = ('ac9_job', 'ac9_job_missing', RESET_PROP, FROM_FRAME_PROP, 'ac9_edit_frame', 'ac9_measure', 'ac9_measure_geo',
               'ac9_problems')     # diagnose.PROP: a new result's problems are its own
PAUSED_PROP = 'ac9_paused_modifiers'


def _addon_modifier(m):
    return m.type == 'MESH_CACHE' or m.name in (HIDE_MOD, THICK_MOD)


def _clean_garment(ob):
    for m in [m for m in ob.modifiers if _addon_modifier(m)]:
        ob.modifiers.remove(m)
    for g in (HIDE_GROUP, THICK_VG, THICK_SHELL, THICK_RIM, ANOMALY_GROUP):
        vg = ob.vertex_groups.get(g)
        if vg is not None:
            ob.vertex_groups.remove(vg)
    for k in ADDON_PROPS:
        ob.pop(k, None)
    # the solver's positions are world positions and the drape cache writes them as the mesh's: the garment's world
    # transform is the identity (a contract, Fable review 2026-10-20 §1; a parent at rest, e.g. an armature, is fine)
    if ob.matrix_world != Matrix.Identity(4):
        ob.matrix_world = Matrix.Identity(4)


def garment_object(context, name, flat, F, fp, X_mm, names):
    """The dressed garment with a new mesh. The object is kept when it is there (user 2026-10-20: a person's
    modifiers, parent, user collections and materials stay; legacy scene-root results use the AC9 results collection.
    The add-on's modifiers and properties go, _clean_garment). Its
    slots: the pieces' looks (look_apply), or -- a garment without looks -- the old mesh's materials, all faces on the
    first (user 2026-10-21, b-3)."""
    from . import look_apply as LA
    me = bpy.data.meshes.new(name)
    me.from_pydata((X_mm * 0.001).tolist(), [], F.tolist())
    me.update()
    me.shade_smooth()           # cloth is drawn smooth (flat shading showed every triangle, user 2026-10-15)
    uv = me.uv_layers.new(name='UVMap')
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', lv)
    uv.data.foreach_set('uv', (flat[lv] / 1000.0).astype(np.float32).ravel())
    a = me.attributes.new('ac9_piece', 'INT', 'FACE')
    a.data.foreach_set('value', fp.astype(np.int32))
    ob = bpy.data.objects.get(name)
    mats = []
    if ob is not None and ob.type == 'MESH':
        old = ob.data
        mats = list(old.materials)
        ob.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
        _clean_garment(ob)
    else:
        if ob is not None:                             # another kind of object took the name
            bpy.data.objects.remove(ob)
        ob = bpy.data.objects.new(name, me)
        context.scene.collection.objects.link(ob)
    ob['ac9_piece_names'] = json.dumps(names, ensure_ascii=False)
    spec = store.get(context)[0]
    if spec is not None and name == garment_name(spec):
        from . import view_setup
        view_setup.ensure_result(context, ob, spec)
        view_setup.refresh(context)
    if not (spec is not None and LA.apply_garment(ob, spec)):
        for m in mats:
            me.materials.append(m)
    if spec is not None and name == garment_name(spec):
        from . import display_state
        display_state.apply(context, spec)
    return ob


def pause_modifiers(ob):
    """A person's modifiers off in the viewport while a run writes the garment (user 2026-10-21: switched off, not
    removed; back on at the end, resume_modifiers). Returns how many."""
    names = [m.name for m in ob.modifiers if not _addon_modifier(m) and m.show_viewport]
    for n in names:
        ob.modifiers[n].show_viewport = False
    if names:
        ob[PAUSED_PROP] = json.dumps(names + json.loads(ob.get(PAUSED_PROP, '[]')), ensure_ascii=False)
    return len(names)


def resume_modifiers(ob):
    """The modifiers pause_modifiers switched off, on again. Returns how many."""
    try:
        names = json.loads(ob.get(PAUSED_PROP, '[]')) if ob is not None else []
    except (ReferenceError, ValueError):
        return 0
    n = 0
    for nm in dict.fromkeys(names):
        m = ob.modifiers.get(nm)
        if m is not None and not m.show_viewport:
            m.show_viewport = True
            n += 1
    ob.pop(PAUSED_PROP, None)
    return n


THICK_MOD = THICK_VG = "AC9_Thickness"
THICK_SHELL, THICK_RIM = "AC9_ThicknessShell", "AC9_ThicknessRim"     # written by the Solidify (show_thickness)


THICK_OFFSET = {'INSIDE': -1.0, 'CENTER': 0.0, 'OUTSIDE': 1.0}


def refresh_thickness(context, ob, spec=None):
    """The garment's drawn thickness as the scene says (Overlays > Thickness and its side, user 2026-10-21)."""
    if ob is None or ob.type != 'MESH':
        return None
    props = context.scene.ac9_cloth_sim
    if not props.show_thickness:                 # not under the overlays' switch: it is the mesh, not drawn over it
        hide_thickness(ob)
        return None
    spec = spec if spec is not None else store.get(context)[0]
    return show_thickness(ob, spec, offset=THICK_OFFSET.get(props.thickness_side, -1.0))


def show_thickness(ob, spec, garment_mm=None, offset=-1.0):
    """The cloth drawn with its thickness (2026-10-17, APPROVED "T シャツの衿リブの生地": the rib 1.5 mm, the body 0.5 mm
    by a Solidify, experiments/rib_thick_2026-10-17/add_thickness.py): one Solidify after the drape cache, each piece
    its fabric's thickness_mm (core/fabric.py) through a vertex group; garment_mm for the pieces without one (None =
    none). offset: -1 inside (the default, user 2026-10-21: outside opened grooves at the seams, each side following
    its own normals), 0 centered, 1 outside. The display only: the solver never sees it. Returns {piece: mm}, or None (no thickness: the modifier
    removed)."""
    from .core import fabric as FA
    names = json.loads(ob.get('ac9_piece_names', '[]'))
    t = {n: FA.thickness(FA.resolved(spec, n)) for n in names}
    t = {n: (v if v is not None else float(garment_mm or 0.0)) for n, v in t.items()}
    off = {pc['name'] for pc in spec.get('pieces', []) if pc.get('thickness', True) is False}
    t = {n: (0.0 if n in off else v) for n, v in t.items()}     # a piece set to no thickness (user 2026-10-23)
    tmax = max(t.values(), default=0.0)
    me = ob.data
    pa = me.attributes.get('ac9_piece')
    if tmax <= 0.0 or pa is None:
        hide_thickness(ob)
        return None
    fpc = np.empty(len(me.polygons), np.int64)
    pa.data.foreach_get('value', fpc)
    tf = np.array([t[n] for n in names])[fpc] / tmax            # each face's share of the thickest
    w = np.zeros(len(me.vertices))
    ls = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get('loop_total', ls)
    lv = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get('vertex_index', lv)
    np.maximum.at(w, lv, np.repeat(tf, ls))                     # a piece's vertices are its own (seams are stitches)
    vg = ob.vertex_groups.get(THICK_VG) or ob.vertex_groups.new(name=THICK_VG)
    for val in np.unique(w):
        vg.add(np.nonzero(w == val)[0].tolist(), float(val), 'REPLACE')
    m = ob.modifiers.get(THICK_MOD) or ob.modifiers.new(THICK_MOD, 'SOLIDIFY')
    m.thickness = tmax / 1000.0
    m.offset = float(offset)
    m.vertex_group = THICK_VG
    m.thickness_vertex_group = 0.0          # weight 0 = no thickness (a piece without one)
    m.use_even_offset = False         # True multiplies the offset at a sharp crease: a 180-deg fold shot one vertex out 127 mm (0.6 mm set)
    _mark_thickness(ob, m)
    _thickness_last(ob)
    return t


def _mark_thickness(ob, m):
    """The copy and the rim the Solidify adds, marked: they carry the cloth's UV again, and the buttons / zippers
    placed by UV (button_view, zipper_view) leave them out -- on the doubled UV every button fell to the origin
    (user 2026-10-23: they showed only while a run went, the thickness off)."""
    for nm in (THICK_SHELL, THICK_RIM):
        if ob.vertex_groups.get(nm) is None:
            ob.vertex_groups.new(name=nm)
    m.shell_vertex_group = THICK_SHELL
    m.rim_vertex_group = THICK_RIM


def hide_thickness(ob):
    m = ob.modifiers.get(THICK_MOD)
    if m is not None:
        ob.modifiers.remove(m)
    for nm in (THICK_VG, THICK_SHELL, THICK_RIM):
        vg = ob.vertex_groups.get(nm)
        if vg is not None:
            ob.vertex_groups.remove(vg)


def _thickness_last(ob):
    """The add-on's modifiers first, in the order drape cache, Mask, Solidify; a person's after them (the cache
    replaces the positions whole, so an armature before it would do nothing; a Solidify before the cache would not
    find its vertices). Fable review 2026-10-20 §1-B."""
    order = ([m.name for m in ob.modifiers if m.type == 'MESH_CACHE']
             + [m.name for m in ob.modifiers if m.name == HIDE_MOD]
             + [m.name for m in ob.modifiers if m.name == THICK_MOD])
    for k, nm in enumerate(order):
        i = ob.modifiers.find(nm)
        if i != k:
            ob.modifiers.move(i, k)


# The next run's solver process, started when a run ends (sim_latency 2026-10-20): Python, numpy, warp and the patched
# newton imported and the GPU context made while the user looks at the result, about 1.5 s off the next Simulate /
# Continue / Live (solver/run_stages.py --standby). It waits on stdin; it is used only for a run whose interpreter,
# solver environment, compile-time switches (the membrane keys of the first stage's config) and solver files are those
# it was started with, else it is ended and the run starts cold as before. Blender gone = its stdin closed = it ends.
# Env AC9_STANDBY=0 = never (every run starts cold).
_STANDBY = {'proc': None, 'key': None, 'log': None}
_PRELOAD = {'state': 'waiting', 'completed': 0, 'total': 4, 'phase': 'imports'}
_PRELOAD_SESSION = {'path': None, 'proc': None, 'target': None, 'check_at': 0.0, 'pending': None}
_STANDBY_CFG = ('membrane', 'bw_comp_ratio', 'bw_comp_width', 'bw_comp_scale', 'bw_hess_reg')


def _standby_key(python, env, cfg1):
    try:
        c = json.load(open(cfg1, encoding='utf-8'))
        sd = os.path.dirname(RUN_STAGES)
        src = [os.path.join(sd, f) for f in os.listdir(sd) if f.endswith('.py')]
        src += [os.path.join(sd, 'newton_patch', f) for f in os.listdir(os.path.join(sd, 'newton_patch'))
                if f.endswith('.py')]
        mt = max(os.path.getmtime(f) for f in src)
    except (OSError, ValueError):
        return None
    return (os.path.normcase(python), tuple(sorted(env.items())), tuple((k, c.get(k)) for k in _STANDBY_CFG), mt)


def _standby_drop_log():
    if _STANDBY['log'] is not None:
        _STANDBY['log'].close()
        _STANDBY['log'] = None


def standby_end():
    """End the waiting solver process, if any (a file opened, the add-on disabled, a run it does not fit)."""
    p, _STANDBY['proc'], _STANDBY['key'] = _STANDBY['proc'], None, None
    if p is not None and p.poll() is None:
        try:
            p.stdin.close()             # end of input: it ends by itself
        except OSError:
            pass
        # A compile may not read stdin yet. Reuse the nonblocking process owner.
        stop = retire(p, [])
        stop.deadline = time.monotonic() + 5.0
        if not bpy.app.timers.is_registered(reap_retired):
            bpy.app.timers.register(reap_retired, first_interval=0.25, persistent=True)
    _standby_drop_log()
    _PRELOAD_SESSION.update(path=None, proc=None, pending=None)
    _PRELOAD.update(state='idle', completed=0)


def standby_enabled(context=None):
    """Keep the Solver Ready (Preferences, on by default) and not AC9_STANDBY=0."""
    if os.environ.get('AC9_STANDBY', '1') == '0':
        return False
    prefs = addon_prefs(context or bpy.context)
    return getattr(prefs, 'keep_solver_ready', True)


def _standby_start(python, env, cfg1):
    _trace('standby_start_begin')
    standby_end()
    if not python or not standby_enabled():
        return
    if retired_running():
        # Do not compile two variants concurrently while the old child is retiring.
        _PRELOAD_SESSION['pending'] = (python, env, cfg1)
        _PRELOAD.update(state='loading', completed=0, phase='imports')
        return
    free = _gpu_free_mb()
    if free is not None and free < STANDBY_MIN_FREE_MB:     # the GPU nearly full: the next run starts cold
        _PRELOAD.update(state='gpu_wait', completed=0)
        return
    key = _standby_key(python, env, cfg1)
    if key is None:
        return
    try:
        status_path = os.path.join(tempfile.gettempdir(), f'ac9_preload_{uuid.uuid4().hex}.json')
        child_env = dict(env, AC9_PRELOAD_STATUS=status_path)
        _PRELOAD_SESSION.update(path=status_path, proc=None)
        _PRELOAD.update(state='loading', completed=0, phase='imports', error='')
        lf = open(os.path.join(tempfile.gettempdir(), f'ac9_cloth_sim_standby_{os.getpid()}.txt'), 'w',
                  encoding='utf-8')
        _STANDBY['proc'] = subprocess.Popen([python, RUN_STAGES, '--standby', cfg1], stdin=subprocess.PIPE,
                                            stdout=lf, stderr=subprocess.STDOUT, env=child_env, text=True,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _trace('standby_spawned', child_pid=_STANDBY['proc'].pid)
        _STANDBY['key'], _STANDBY['log'] = key, lf
        _PRELOAD_SESSION['proc'] = _STANDBY['proc']
        _STANDBY['used'] = time.time()
        if not bpy.app.timers.is_registered(_standby_idle):
            bpy.app.timers.register(_standby_idle, first_interval=60.0, persistent=True)
    except OSError as exc:
        _STANDBY['proc'] = None
        _standby_drop_log()
        _PRELOAD.update(state='failed', completed=0, error=str(exc))


# Woken ahead of a run as well (user 2026-10-27, DECISIONS cloth-sim-work-screen-layout-2026-10-27; Fable
# REVIEW_fable_standby_and_sync_switch_ux_2026-10-27 A): a file with a pattern and a body opened, a pattern edit, a
# Place -- else the first run after opening Blender or a file always started cold (5-8 s). Not when Blender starts (a
# file without a pattern holds no GPU memory for it). It ends after STANDBY_IDLE_S without a run or a wake, and is
# not started while the GPU has less than STANDBY_MIN_FREE_MB free (both values a guess, the review's).
STANDBY_IDLE_S = 900.0
STANDBY_MIN_FREE_MB = 1024
_GPU_FREE = {'t': -1e9, 'mb': None}


def _gpu_free_mb():
    """Free memory (MB) of the first GPU by nvidia-smi (about 50 ms), kept 60 s; None when it cannot be read."""
    if time.time() - _GPU_FREE['t'] < 60.0:
        return _GPU_FREE['mb']
    mb = None
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=3,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        mb = float(r.stdout.split()[0]) if r.returncode == 0 and r.stdout.split() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    _GPU_FREE.update(t=time.time(), mb=mb)
    return mb


def _standby_idle():
    p = _STANDBY['proc']
    if p is None or p.poll() is not None:
        return None                                  # nothing waiting: the timer stops (started again with it)
    if time.time() - _STANDBY.get('used', 0.0) >= STANDBY_IDLE_S:
        standby_end()
        return None
    return 60.0


def _wake_args(context):
    """(python, env, first stage config) to start the waiting process with before any run, or None: a pattern
    and a solver Python. The config and the environment of the garment's last simulated result (the next
    run's membrane is the same unless the fabric changed); with no result, the membrane a Dress gives (core/recipe.py
    physical), written to the temporary folder. A guess that does not fit only means the run starts cold."""
    props = context.scene.ac9_cloth_sim
    spec = store.get(context)[0]
    if spec is None:
        return None
    python = newton_python(context)
    if python is None:
        return None
    g = bpy.data.objects.get(garment_name(spec))
    job = g.get('ac9_job') if g is not None else None
    if job:
        from .draft_tool import _simulated_root
        job = _simulated_root(job)
    cfg1 = os.path.join(job, 's1', 'config.json') if job else None
    if cfg1 and os.path.exists(cfg1):
        try:
            senv = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8')).get('solver_env', {})
        except (OSError, ValueError):
            senv = {}
        with open(cfg1, encoding='utf-8') as stream:
            previous = json.load(stream)
        # A saved run may describe the old fabric mode. Never advertise that variant
        # as ready after the user switches the current mode.
        if (previous.get('membrane') == 'bw') != props.physical_fabric:
            cfg1 = None
    else:
        senv = {}
        cfg1 = None
    if cfg1 is None:
        cfg1 = os.path.join(tempfile.gettempdir(), f'ac9_cloth_sim_standby_cfg_{os.getpid()}.json')
        try:
            json.dump({'membrane': 'bw', 'bw_hess_reg': 1.0} if props.physical_fabric else {},
                      open(cfg1, 'w', encoding='utf-8'))
        except OSError:
            return None
    return python, solver_process_env(python, senv), cfg1


def standby_wake(context=None):
    """Start the waiting process soon if there is none (a file opened, a pattern edit, a Place): on a timer, so the
    edit returns first (the GPU check and the start take about 0.1 s)."""
    _ensure_preload_timer()
    if not standby_enabled(context):
        return
    _STANDBY['used'] = time.time()
    p = _STANDBY['proc']
    if AC9_OT_SimDress.running() or bpy.app.timers.is_registered(_wake_now):
        return
    bpy.app.timers.register(_wake_now, first_interval=0.5)


def _ensure_preload_timer():
    if not bpy.app.timers.is_registered(_preload_tick):
        bpy.app.timers.register(_preload_tick, first_interval=0.25, persistent=True)


def solver_preload_status():
    """Cached only: panel draw performs no disk/GPU/process/config work."""
    return _PRELOAD


def _preload_tick():
    try:
        pending = _PRELOAD_SESSION['pending']
        if pending and standby_enabled() and not AC9_OT_SimDress.running():
            reap_retired()
            if not retired_running():
                _PRELOAD_SESSION['pending'] = None
                _standby_start(*pending)
        p = _PRELOAD_SESSION['proc']
        path = _PRELOAD_SESSION['path']
        if p is not None:
            row = _preload_status.read(path) if path else None
            if row is not None:
                _PRELOAD.update(row)
            if p.poll() is not None and not AC9_OT_SimDress.running():
                _PRELOAD.update(state='failed', completed=0)
        # Check compatibility away from draw(), at most once every two seconds.
        if time.monotonic() >= _PRELOAD_SESSION['check_at']:
            _PRELOAD_SESSION['check_at'] = time.monotonic() + 2.0
            if not standby_enabled():
                if _STANDBY['proc'] is not None:
                    standby_end()
                _PRELOAD.update(state='disabled', completed=0)
            elif not AC9_OT_SimDress.running():
                args = _wake_args(bpy.context)
                target = _standby_key(*args) if args else None
                old_target = _PRELOAD_SESSION['target']
                _PRELOAD_SESSION['target'] = target
                if target is None:
                    if _STANDBY['proc'] is not None:
                        standby_end()
                    _PRELOAD.update(state='waiting', completed=0)
                elif target != old_target or _PRELOAD['state'] == 'disabled':
                    _PRELOAD_SESSION['path'] = None
                    _PRELOAD.update(state='loading', completed=0, phase='imports')
                    standby_wake()
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type in ('VIEW_3D', 'PROPERTIES'):
                    area.tag_redraw()
    except Exception as exc:
        _PRELOAD.update(state='failed', completed=0, error=str(exc))
    return 0.25


def _wake_now():
    try:
        p = _STANDBY['proc']
        if AC9_OT_SimDress.running():
            return None
        args = _wake_args(bpy.context)
        if args is not None:
            key = _standby_key(*args)
            _PRELOAD_SESSION['target'] = key
            if p is not None and p.poll() is None and _STANDBY['key'] == key:
                return None
            _standby_start(*args)
    except Exception as exc:
        _PRELOAD.update(state='failed', completed=0, error=str(exc))
    return None


@bpy.app.handlers.persistent
def _wake_on_load(*_args):
    standby_wake()


def _standby_take(python, env, cfg1):
    """The waiting process if it is alive and fits this run, else None (and it is ended)."""
    p = _STANDBY['proc']
    _trace('standby_take_begin', child_pid=p.pid if p else None)
    if p is None or p.poll() is not None or _STANDBY['key'] != _standby_key(python, env, cfg1):
        standby_end()
        return None
    _STANDBY['proc'], _STANDBY['key'] = None, None
    _standby_drop_log()
    _trace('standby_taken', child_pid=p.pid)
    return p


def stop_solver(proc, stage_dirs, timeout=60):
    """Stop the running solver as Esc does: a stop file in every stage not yet ended (one pressed as a stage hands
    over would otherwise reach a stage that never looks at it), then wait; killed if it does not end."""
    _trace('stop_requested', child_pid=proc.pid)
    for sd in stage_dirs:
        try:
            open(os.path.join(sd, 'stop'), 'w').close()
            _trace('stop_file_created', stage=sd, child_pid=proc.pid)
        except OSError:
            pass
    try:
        proc.wait(timeout=timeout)
        _trace('child_exited', child_pid=proc.pid, code=proc.returncode)
    except subprocess.TimeoutExpired:
        _trace('stop_timeout', child_pid=proc.pid, timeout=timeout)
        proc.kill()
        _trace('kill_sent', child_pid=proc.pid)
        proc.wait()
        _trace('child_exited', child_pid=proc.pid, code=proc.returncode)


@bpy.app.handlers.persistent
def _drop_missing_cache(*_):
    """After an undo: a garment's Mesh Cache whose file is gone (Delete Cache, Keep Cache off, the frames of another
    garment dropped) comes back with the step before it; it is taken off, the mesh under it is the shape it showed
    last (design review 10/22)."""
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or not ob.name.startswith(GARMENT_PREFIX):
            continue
        for m in [m for m in ob.modifiers if m.type == 'MESH_CACHE']:
            if m.filepath and not os.path.exists(bpy.path.abspath(m.filepath)):
                ob.modifiers.remove(m)


def _rel(path):
    """A path next to the saved .blend as '//...' (Blender's relative form): the .blend and its job folder copied
    together to another PC still find each other (user 2026-10-23). Absolute when the file is not saved or the path
    is on another drive."""
    if not bpy.data.filepath:
        return path
    try:
        return bpy.path.relpath(path)
    except ValueError:
        return path


def job_from_mesh(context, g, was=None):
    """A job made from the dressed garment in the .blend (its vertices are the result's last shape, _finish), for a
    .blend whose job folder is not there -- copied alone to another PC (user 2026-10-23: the last shape is the
    .blend's, the frames are the folder's). Continue / Live / Simulate go on from it as from a carried edit
    (draft_tool.carry_to_3d): the frames before it and the last stage's solver config are not in it. Returns the
    folder, or None when the garment is not one Dress made (no piece attribute / names)."""
    me = g.data
    a = me.attributes.get('ac9_piece')
    try:
        names = [str(n) for n in json.loads(g.get('ac9_piece_names', ''))]
    except ValueError:
        return None
    if a is None or a.domain != 'FACE' or not names or not len(me.polygons) or not me.uv_layers:
        return None
    n_v = len(me.vertices)
    ls = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get('loop_total', ls)
    if (ls != 3).any():
        return None
    F = np.empty(len(me.polygons) * 3, np.int32)
    me.polygons.foreach_get('vertices', F)
    F = F.reshape(-1, 3)
    fp = np.empty(len(me.polygons), np.int32)
    a.data.foreach_get('value', fp)
    vp = np.full(n_v, -1, np.int64)
    vp[F.ravel()] = np.repeat(fp, 3)
    # Dress lays the pieces' vertices out one piece after another (prepare: offs)
    if (vp < 0).any() or (np.diff(vp) < 0).any() or vp.max() >= len(names):
        return None
    offs = np.searchsorted(vp, np.arange(len(names)))
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', lv)
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[0].data.foreach_get('uv', uv)
    flat = np.zeros((n_v, 2))
    flat[lv] = uv.reshape(-1, 2).astype(np.float64) * 1000.0      # the UV is the pattern in mm / 1000
    X = np.empty(n_v * 3, np.float32)
    me.vertices.foreach_get('co', X)
    X = X.reshape(-1, 3).astype(np.float64) * 1000.0
    d = None
    # next to the .blend; where that cannot be written (a shared or read-only folder), the system temp folder
    for base in ([os.path.dirname(bpy.data.filepath)] if bpy.data.filepath else []) + [tempfile.gettempdir()]:
        try:
            d = os.path.join(base, "AC9_ClothSim_jobs", time.strftime("%Y%m%d_%H%M%S") + "_blend")
            os.makedirs(os.path.join(d, 's1'), exist_ok=True)
            break
        except OSError:
            d = None
    if d is None:
        return None
    np.savez(os.path.join(d, 'scene.idx.npz'), offs=offs, F=F, fp=fp, flat=flat, Xinit=X)
    np.save(os.path.join(d, 's1', 'frames.npy'), (X[None] * 0.001).astype(np.float32))
    spec = store.get(context)[0]
    if spec is not None:
        open(os.path.join(d, 'ac9_spec.json'), 'w', encoding='utf-8').write(S.dumps(spec))
    json.dump({'kind': 'taken from the garment in the .blend (its job folder was not found)', 'from': was,
               'names': names, 'stages': ['from the .blend'], 'verts': int(n_v), 'tris': int(len(F))},
              open(os.path.join(d, 'job.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    g['ac9_job'] = d
    g.pop(JOB_MISSING_PROP, None)
    return d


JOB_MISSING_PROP = 'ac9_job_missing'          # set on load: the garment's job folder was not found (_relink_jobs)


def has_result(g):
    """The garment is dressed: its job folder is there, or it was lost with the .blend opened alone (JOB_MISSING_PROP,
    the result is the garment's own mesh). What the drawing and the tools ask instead of job_of when they only need
    to know the stage: job_of alone took a .blend from another PC for the placement stage and drew the placed
    pieces wrapped on their cylinders over the dressed garment (user 2026-10-28)."""
    return g is not None and (job_of(g) is not None or bool(g.get(JOB_MISSING_PROP)))


def job_or_from_mesh(context, g):
    """job_of, or -- for a garment marked on load as having lost its folder -- a job made from its mesh now (the run
    that needs it: Simulate / Continue / Live; design review 10/23: not on load, which would write a folder each time
    the file is opened)."""
    j = job_of(g)
    if j is None and g is not None and g.get(JOB_MISSING_PROP):
        j = job_from_mesh(context, g, str(g.get(JOB_MISSING_PROP)))
    return j


@bpy.app.handlers.persistent
def _relink_jobs(*_):
    """After a .blend opens: a garment whose job folder is not at its path finds it next to the .blend (the .blend
    and its AC9_ClothSim_jobs copied together), or -- the .blend alone -- is marked (JOB_MISSING_PROP), and its
    first Simulate / Live makes a job from its own shape (job_or_from_mesh), going on from the dressed garment
    instead of dressing again from the placement (user 2026-10-23, another PC). Nothing is written here. Its Mesh
    Cache is pointed at the found folder or taken off."""
    for g in [o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith(GARMENT_PREFIX)]:
        was = str(g.get('ac9_job') or '')
        if was and job_alive(was):
            continue
        cand = (os.path.join(os.path.dirname(bpy.data.filepath), 'AC9_ClothSim_jobs', os.path.basename(was))
                if was and bpy.data.filepath else '')
        found = cand if cand and os.path.exists(os.path.join(cand, 'scene.idx.npz')) else None
        for m in [m for m in g.modifiers if m.type == 'MESH_CACHE']:
            if m.filepath and os.path.exists(bpy.path.abspath(m.filepath)):
                continue
            pc2 = os.path.join(found, 'drape.pc2') if found else ''
            if pc2 and os.path.exists(pc2):
                m.filepath = _rel(pc2)
            else:
                g.modifiers.remove(m)
        try:
            if found:
                g['ac9_job'] = found
                g.pop(JOB_MISSING_PROP, None)
            elif job_of(g) is None and g.get('ac9_piece_names'):  # job_of finds a folder from a Mesh Cache still there
                g[JOB_MISSING_PROP] = was or '-'
        except Exception as e_:                               # noqa: BLE001 -- a file must open whatever this does
            print("AC9 Cloth Sim: the garment's job folder:", e_)
    # a file saved before the thickness marked its copy (2026-10-23): marked now, the buttons' groups made anew
    try:
        for g in [o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith(GARMENT_PREFIX)]:
            m = g.modifiers.get(THICK_MOD)
            if m is not None and m.type == 'SOLIDIFY' and m.shell_vertex_group != THICK_SHELL:
                _mark_thickness(g, m)
        BV.refresh_groups()
    except Exception as e_:                                   # noqa: BLE001
        print("AC9 Cloth Sim: the buttons on the thickness:", e_)


@bpy.app.handlers.persistent
def _stop_on_load(*_):
    """Another file opened while a run goes: stop the solver rather than leave it running for a result nobody
    will take (Fable review 2026-10-13). The frames it wrote stay in the job folder."""
    cls = AC9_OT_SimDress
    proc = cls._proc
    if proc is not None and process_running(proc):
        try:
            stop_solver(proc, cls._stage_dirs, timeout=10)
        except OSError as exc:
            _trace('owner_stop_failed', child_pid=proc.pid, error=str(exc))
        if process_running(proc):
            # No RNA/operator references in this owner: it survives disable and F3 reload.
            retire(proc, cls._stage_dirs)
            if not bpy.app.timers.is_registered(reap_retired):
                bpy.app.timers.register(reap_retired, first_interval=0.25, persistent=True)
    owner = getattr(cls, '_active_instance', None)
    timer = getattr(cls, '_active_timer', None)
    wm = getattr(cls, '_active_wm', None)
    if timer is not None and wm is not None:
        try:
            wm.event_timer_remove(timer)
        except (ReferenceError, RuntimeError):
            pass
    if owner is not None:
        try:
            owner._abandoned = True
            owner._timer = None
            owner._pending_stop = None
            owner.proc = None
            owner._resume(bpy.context)
        except (ReferenceError, RuntimeError):
            pass
    cls._active_instance = cls._active_timer = cls._active_wm = cls._active_window = None
    cls._running_mode, cls._running_t, cls._stop_req, cls._proc, cls._stage_dirs = None, 0.0, False, None, ()
    cls._running_button = None
    cls._running_job = None
    standby_end()


def _watch_run_window():
    cls = AC9_OT_SimDress
    if getattr(cls, '_active_instance', None) is None:
        return None
    try:
        present = cls._active_window in tuple(cls._active_wm.windows)
    except ReferenceError:
        present = False
    if not present:
        _stop_on_load()
        return None
    return 0.25


_RESULT_CACHE = {}


def result_info(g):
    """What the garment's result is, for the Dress panel (P1 #6, user 2026-10-13: "when is the Dress saved?").
    Read from the job's job.json, cached by its time stamp: the panel draws on every mouse move."""
    if g is None:
        return None
    d = g.get('ac9_job')
    if not d:
        return None
    d = str(d)
    jp = os.path.join(d, 'job.json')
    try:
        mt = os.path.getmtime(jp)
    except OSError:
        return {'dir': d, 'missing': True}
    hit = _RESULT_CACHE.get(jp)
    if hit is None or hit[0] != mt:
        try:
            j = json.load(open(jp, encoding='utf-8'))
        except (OSError, ValueError):
            return {'dir': d, 'missing': True}
        hit = (mt, {'dir': d, 'when': time.strftime('%m/%d %H:%M', time.localtime(mt)),
                    'mode': j.get('mode') or ('CONTINUE' if j.get('kind') == 'continue' else
                                              'LIVE' if j.get('kind') == 'live' else
                                              'LOCAL' if j.get('kind') == 'local' else 'START'),
                    'button': j.get('button'), 'frames': j.get('frames_cached'), 'wall_s': j.get('wall_s'), 'error': j.get('error'),
                    'temp': os.path.abspath(d).lower().startswith(os.path.abspath(tempfile.gettempdir()).lower()),
                    'dropped': os.path.exists(os.path.join(d, 'final.json'))})
        _RESULT_CACHE[jp] = hit
    r = dict(hit[1])
    r['playing'] = any(m.type == 'MESH_CACHE' and m.show_viewport and os.path.exists(bpy.path.abspath(m.filepath))
                       for m in g.modifiers)
    return r


def _mesh_back(context, ob, job):
    """The garment's mesh made again from <job> (its pattern mesh, at its last frame), when a run that replaced it
    wrote no frame. Its link alone went back before (2026-11-01: a Local Sim with a piece added left the 16-piece
    mesh under the 15-piece job, and the next Live stopped on "the pattern changed")."""
    try:
        z = np.load(os.path.join(job, 'scene.idx.npz'))
        names = json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))['names']
        X = job_last_frame(job)
        X = z['Xinit'] if X is None else X * 1000.0
    except (OSError, KeyError, ValueError) as e_:
        print("AC9 Cloth Sim: the previous mesh not made again:", e_)
        return ob
    if len(ob.data.vertices) == len(X) and json.loads(ob.get('ac9_piece_names', '[]')) == names:
        return ob
    return garment_object(context, ob.name, z['flat'], z['F'], z['fp'], X, names)


def set_positions(ob, X_m):
    ob.data.vertices.foreach_set('co', np.ascontiguousarray(X_m, np.float32).ravel())
    ob.data.update()


def write_pc2(path, frames_m):
    n_s, n_p = frames_m.shape[0], frames_m.shape[1]
    with open(path, 'wb') as f:
        f.write(struct.pack('<12siiffi', b'POINTCACHE2\0', 1, n_p, 1.0, 1.0, n_s))
        f.write(np.ascontiguousarray(frames_m, np.float32).tobytes())


def attach_cache(context, ob, pc2, n_frames):
    for m in list(ob.modifiers):
        if m.type == 'MESH_CACHE':
            ob.modifiers.remove(m)
    m = ob.modifiers.new("AC9_Drape", 'MESH_CACHE')
    m.cache_format = 'PC2'
    m.filepath = _rel(pc2)
    m.frame_start = 1.0
    _thickness_last(ob)
    sc = context.scene
    sc.frame_start, sc.frame_end = 1, n_frames
    sc.frame_set(n_frames)


_PC2 = {'key': None, 'co': None}


def _pc2_frame(path, i):
    """Sample i (clamped) of a PC2 file as float32 (n, 3), or None."""
    try:
        st = os.stat(path)
        with open(path, 'rb') as f:
            head = f.read(32)
            _, _, n_p, _, _, n_s = struct.unpack('<12siiffi', head)
            i = min(max(int(i), 0), n_s - 1)
            key = (path, st.st_mtime_ns, st.st_size, i)
            if _PC2['key'] == key:
                return _PC2['co']
            f.seek(32 + i * n_p * 12)
            co = np.frombuffer(f.read(n_p * 12), np.float32).reshape(-1, 3)
    except (OSError, struct.error, ValueError):
        return None
    _PC2.update(key=key, co=co)
    return co


def _pc2_frames(path, n, n_p):
    """The first n samples of a PC2 file (m, float32 (n, n_p, 3)), or None when it is missing, shorter, or of
    another vertex count."""
    try:
        with open(path, 'rb') as f:
            _, _, n_p_, _, _, n_s = struct.unpack('<12siiffi', f.read(32))
            if n_p_ != n_p or n_s < n:
                return None
            return np.frombuffer(f.read(n * n_p * 12), np.float32).reshape(n, n_p, 3).copy()
    except (OSError, struct.error, ValueError):
        return None


def garment_co(context, ob):
    """The garment's vertices as the add-on made them (local m, float32 (n, 3)): the drape cache at the frame shown,
    else the mesh. Indexed as the solver's vertices whatever else is on the object: a person's modifiers (kept on the
    garment, user 2026-10-21) and the thickness's Solidify change the count or the places of the evaluated mesh, so
    with one of them on, the cache's sample is read from its file (Fable review 2026-10-20 §1-B). The 4 readers
    (shown_shape, piece_look, overlay, piece_move) read this one."""
    me = ob.data
    # A display Mask also hides Off pieces during playback; it does not freeze
    # the cache. Read indexed positions before any topology-changing modifier.
    mc = next((m for m in ob.modifiers if m.type == 'MESH_CACHE' and m.show_viewport), None)
    if mc is not None:
        if not any(m.show_viewport for m in ob.modifiers if m.type != 'MESH_CACHE'):
            me = ob.evaluated_get(context.evaluated_depsgraph_get()).data
        else:
            co = _pc2_frame(bpy.path.abspath(mc.filepath), context.scene.frame_current - mc.frame_start)
            if co is not None and len(co) == len(ob.data.vertices):
                return co
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get('co', co)
    return co.reshape(-1, 3)


def shown_shape(context, ob):
    """The garment as it is on screen, in mm: the drape cache at the current frame, or -- with the cache switched
    off (Fix by Hand) -- the mesh with the user's edits. A person's modifiers are not in it (garment_co)."""
    if ob.mode == 'EDIT':
        ob.update_from_editmode()
    return garment_co(context, ob).astype(np.float64) * 1000.0


ANOMALY_ATTR = 'AC9_Anomaly'
# the vertex group must not share the color attribute's name: a vertex group is an attribute too, and the viewport's
# Attribute color then showed the group's weights (black / white) instead of the red (2026-10-05, user test). Since
# 2026-11-02 the red is drawn by the Overlays' Problems (diagnose.py); the attribute is no longer written
ANOMALY_GROUP = 'AC9_Anomaly_area'
ANOMALY_RADIUS = 0.015                            # m round the stitches that pulled hardest


def mark_anomaly(ob, job, X_m):
    """Mark where the solver stopped on an anomaly (anomaly.json of the stopped stage): the vertices within
    ANOMALY_RADIUS of the hardest-pulling stitches red in the Overlays' Problems (diagnose.mark; until 2026-11-02 a
    color attribute seen only in Solid shading's Attribute color), and in the vertex group AC9_Anomaly_area. Returns
    the last alarm (or None)."""
    from . import diagnose as DG
    DG.clear(ob, 'anomaly')
    if ob.data.color_attributes.get(ANOMALY_ATTR) is not None:      # an older result's
        ob.data.color_attributes.remove(ob.data.color_attributes[ANOMALY_ATTR])
    al, n_all = None, 0
    for k in range(1, 21):          # the first anomaly of the job: the rest may follow from it (user 2026-10-06)
        p = os.path.join(job, f"s{k}", 'anomaly.json')
        if os.path.exists(p):
            a = json.load(open(p, encoding='utf-8'))
            a = a if isinstance(a, list) else [a]
            n_all += len(a)
            if al is None and a:
                al = dict(a[0], stage=k)
    if al is None:
        return None
    al['count_in_job'] = n_all
    # only a record that names its stitches has a place to paint (a fold line opened, 'unfolded', names a line):
    # 'painted' says which, so the status never claims red that is not there (user 2026-10-13)
    al['painted'] = False
    ids = sorted({int(v) for st in al.get('top_stitches_full_verts', [])[:10] for v in st[:2] if int(v) < len(X_m)})
    if not ids:
        return al
    al['painted'] = True
    d = np.min(np.linalg.norm(X_m[:, None, :] - X_m[ids][None, :, :], axis=2), axis=1)
    hot = np.nonzero(d < ANOMALY_RADIUS)[0]
    DG.mark(ob, 'anomaly', hot, str(al.get('kind') or al.get('reason') or ''))
    if ob.vertex_groups.get(ANOMALY_ATTR) is not None:
        ob.vertex_groups.remove(ob.vertex_groups[ANOMALY_ATTR])
    vg = ob.vertex_groups.get(ANOMALY_GROUP) or ob.vertex_groups.new(name=ANOMALY_GROUP)
    vg.add(hot.tolist(), 1.0, 'REPLACE')
    ob.data.update()
    return al


UNSETTLED_FRAMES = 50       # the last stage's last frames (DECISIONS cloth-sim-unsettled-stage-flag-2026-10-07)
UNSETTLED_MM = 2.0


def mark_unsettled(ob, last_stage):
    """Mark the vertices that moved UNSETTLED_MM or more over the last UNSETTLED_FRAMES frames of the last stage
    (its frames, m): cloth still moving when the run ended is not a result (DECISIONS 2026-10-07; the red it asked
    for was never drawn until the Overlays' Problems, 2026-11-02). Returns how many, 0 for a stage shorter than that."""
    from . import diagnose as DG
    DG.clear(ob, 'unsettled')
    fr = np.asarray(last_stage)
    if fr.ndim != 3 or len(fr) <= UNSETTLED_FRAMES or fr.shape[1] != len(ob.data.vertices):
        return 0
    mv = np.linalg.norm(fr[-1] - fr[-1 - UNSETTLED_FRAMES], axis=1) * 1000.0
    hot = np.nonzero(mv >= UNSETTLED_MM)[0]
    if len(hot):
        DG.mark(ob, 'unsettled', hot, f"{len(hot)} vertices moved {UNSETTLED_MM:g} mm or more in the last "
                                      f"{UNSETTLED_FRAMES} frames (up to {mv.max():.1f} mm)")
    return int(len(hot))


# ---------------------------------------------------------------- operator

LIVE_MODES = ('LIVE', 'LOCAL')        # the runs that go on until stopped and keep their last shape only


def drop_held(names, held, fp, tris, near, face):
    """Live's Ctrl+drag and the pieces that do not move (frozen, ghosts): the solver would only move their vertices,
    as one block left where it was put on release, the cloth around it not following (user 2026-10-28). A press on
    such a piece returns its name (nothing grabbed, said); else their vertices are taken out of near (in place).
    fp: piece per face, tris: (faces, 3), near: bool per vertex, face: the face under the mouse."""
    bad = [i for i, n in enumerate(names) if n in held]
    if not bad:
        return None
    if fp[face] in bad:
        return names[fp[face]]
    near[tris[np.isin(fp, bad)].ravel()] = False
    return None


class NoFrame(ValueError):
    """A run ended before its first frame; .undone: the scene goes back to before the press (undo_to_before)."""
    def __init__(self, msg, undone=False):
        super().__init__(msg)
        self.undone = undone


def push_before_run(context):
    """An undo step of the scene as it is before a run (2026-11-01, Fable review): Ctrl+Z after the run, and the
    undo after a run that wrote no frame (undo_to_before), come back to exactly this. Edit Mode / Sculpt are left
    first (their undo steps hold the mesh only). Returns True when pushed (never in the background: no undo)."""
    if bpy.app.background:
        return False
    try:
        if context.mode != 'OBJECT' and context.object is not None:
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.ed.undo_push(message="Before AC9 Cloth Sim")
        return True
    except RuntimeError as e_:
        print("AC9 Cloth Sim: no undo step before the run:", e_)
        return False


def undo_to_before(msg):
    """After a run that wrote no frame: back to the step push_before_run made -- the garment, the placed objects,
    the Reset marks, Fix by Hand, thickness and measurements as before the press -- then the status line says why.
    Run from a timer once the operator has ended (an undo under a running operator would free its data)."""
    def run():
        wm = bpy.context.window_manager
        win = wm.windows[0] if wm is not None and wm.windows else None
        if win is None:
            return None
        try:
            with bpy.context.temp_override(window=win, screen=win.screen):
                bpy.ops.ed.undo_push(message="AC9 Cloth Sim: no frame")
                bpy.ops.ed.undo()
        except RuntimeError as e_:
            print("AC9 Cloth Sim: not undone:", e_)
        try:
            bpy.context.scene.ac9_cloth_sim.dress_status = msg
        except (AttributeError, ReferenceError):
            pass
        return None
    bpy.app.timers.register(run, first_interval=0.05)


class AC9_OT_SimDress(bpy.types.Operator):
    """Sew and simulate: mesh the pattern, lay the placed pieces round the avatar and run the Newton solver (Esc to stop)"""
    bl_idname = "ac9_cloth_sim.dress"
    bl_label = "Phase Sim"
    bl_options = {'REGISTER'}

    _timer = None
    xinit_from: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})   # diagnosis: JSON {pattern, place, rings}
    mode: bpy.props.EnumProperty(translation_context=CTX, items=[('START', "Phase Sim", "Sew and simulate from the placement"),
                                        ('CONTINUE', "Continue", "Go on from the last result (frozen pieces stay)"),
                                        ('LIVE', "Live Sim", "Go on from the last result (or, before one, from the placement) until stopped (Esc keeps it)"),
                                        ('LOCAL', "Local Sim", "Only the selected pieces, until stopped (Esc keeps it)")],
                                 options={'SKIP_SAVE'})
    # which panel button started it (P1 #8, 2026-10-13): Simulate goes on from the result (START only the first
    # time), Re-dress starts again from the placement; the button that started a run reads Stop
    button: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        if props.mode == 'LOCAL':
            return ("Simulate only the selected pieces until Esc, without gravity: the other pieces are left out "
                    "(not moved, nothing touches them), only the seams among the selected pull and their folds act. "
                    "From the dressed garment the result is the garment's; before one, the placed shapes'. Ctrl+drag "
                    "pulls the cloth (Alt held: through other cloth), Ctrl+Shift+drag smooths it (presses it down and carries it along)")
        if props.mode == 'LIVE':
            if getattr(context.scene.ac9_cloth_sim, 'fast_live', False):
                return ("AC9 Fast Live (Experimental): 2 substeps with a correction in each, static body and lower "
                        "contact damping. Ctrl+drag pulls the cloth (Alt held: through other cloth), Ctrl+Shift+drag "
                        "smooths it. Esc or Live Sim again stops and keeps the result")
            return ("Simulate on from the shape on screen (before a first result: from the placement, gravity on, "
                    "one stage) until Esc: Ctrl+drag pulls the cloth "
                    "meanwhile (with Alt held it passes through other cloth, to pull back cloth that has gone "
                    "through), Ctrl+Shift+drag smooths it (presses it down and carries it along), Esc or Live Sim again stops and keeps the result")
        return ("Go on from the last result: frozen pieces stay where they are, the rest drapes again"
                if props.mode == 'CONTINUE' else
                "Sew and dress: mesh the pattern, lay the placed pieces round the avatar and run the Newton solver "
                "(Esc to stop)")

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and bool(spec.get('guides')) and context.scene.ac9_cloth_sim.body is not None

    # the run on screen, kept on the class: pressing the running button again stops it as Esc does (user 2026-10-13:
    # "stopping is pressing the same button again"; a second press used to start a second run and fail). The
    # heartbeat is the modal's last event (a timer every 0.25 s): a run killed without _end (a file load) does not
    # leave the buttons stuck on Stop
    _running_mode = None
    _running_t = 0.0
    _stop_req = False
    _active_instance = _active_timer = _active_wm = _active_window = None
    _running_button = None                        # 'SIMULATE' / 'REDRESS' / 'LIVE'
    _pushed_before = False                        # Phase Sim made the undo step before the run (push_before_run)
    _proc = None                                  # the running solver (also for the file-load stop below)
    _stage_dirs = ()
    _running_job = None                           # (garment object name, job dir): the 3D seam lines follow
                                                  # the shape while it runs (user 2026-10-14)

    @classmethod
    def running(cls):
        # the solver process decides: a modal held up by a blocking operator (a file browser, a long drag) sends
        # no timer for a while, and a 3 s heartbeat alone let the buttons fall back to Dress and a second run
        # delete the first one's garment (Fable review 2026-10-13). The heartbeat (30 s) only covers a modal
        # that died without _end
        if cls._running_mode is None:
            return None
        if cls._proc is not None and cls._proc.poll() is None:
            return cls._running_mode
        return cls._running_mode if time.time() - cls._running_t < 30.0 else None

    @classmethod
    def running_button(cls):
        return cls._running_button if cls.running() else None

    @classmethod
    def request_stop(cls, context):
        cls._stop_req = True
        if cls._proc is not None and cls._proc.poll() is None and time.time() - cls._running_t > 2.0:
            # no heartbeat: the modal that would pass the stop on is gone (an exception, a closed window), so the
            # stop files go to the solver here; else it runs on with every button stuck on Stop (2026-11-01)
            for sd in cls._stage_dirs:
                try:
                    open(os.path.join(sd, 'stop'), 'w').close()
                except OSError:
                    pass
        context.scene.ac9_cloth_sim.dress_status = tx("stopping...")

    def _start_check(self, context, screen0, flat, Xinit):
        """Each piece's start (Xinit) against the shape on screen at the press (start_check): a line in the log,
        a warning and the pieces off when any piece starts more than 0.1 mm away. Never stops the run (user
        2026-11-04). Returns {piece: its record} of the pieces off."""
        if screen0 is None:
            return {}
        try:
            from . import start_check as SC
            res, head, off_lines = SC.check(screen0, self.info, flat, Xinit)
        except Exception as exc:                      # noqa: BLE001 -- the check must not stop a run
            _trace('start_check_failed', error=repr(exc))
            return {}
        off = {n: r for n, r in res.items() if r['checked'] and r['over']}
        self.info['start_check'] = {n: {k: r[k] for k in ('source', 'checked', 'dmax_mm', 'note')}
                                    for n, r in res.items()}
        log(context, self.info['dir'], 'start' if not off else 'start_warning', head,
            off={n: r['dmax_mm'] if not r['lost'] else r['lost'] for n, r in off.items()})
        for ln in off_lines:
            log(context, self.info['dir'], 'start_warning', ln.strip())
        if off:
            self.report({'WARNING'}, rpt("{0} did not start as shown on screen: see the log", ", ".join(sorted(off))))
        return off

    def invoke(self, context, event):
        # Re-dress over a result asks first: the garment is made again from the placement (the old job folder
        # stays). A press while a run goes is a Stop and asks nothing
        if (not type(self).running() and self.mode == 'START' and self.button == 'REDRESS'
                and has_result(bpy.data.objects.get(garment_name(store.get(context)[0])))):
            return context.window_manager.invoke_confirm(
                self, event, title="Restart from the placement?",
                message="The garment is sewn and dressed again from the start; the current result is replaced "
                        "(its job folder stays)", confirm_text="Restart")
        return self.execute(context)

    def execute(self, context):
        if retired_running():
            context.scene.ac9_cloth_sim.dress_status = tx("stopping...")
            return {'CANCELLED'}
        if type(self).running():
            type(self).request_stop(context)
            return {'CANCELLED'}
        self._pending_stop = None
        self._abandoned = False
        self.python = newton_python(context)
        if not self.python:
            self.report({'ERROR'}, "Set the Newton Python in the add-on preferences")
            return {'CANCELLED'}
        props = context.scene.ac9_cloth_sim
        if self.mode == 'LIVE' and getattr(props, 'fast_live', False):
            try:
                from .core import fast_live as FL
                settings = presets.resolve(props.preset, props,
                    environ=None if bpy.app.background else {})
                FL.preflight(presets.spec_for_job(store.get(context)[0], settings), settings)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
        local = None
        if self.mode == 'LOCAL':
            # the selected pieces (placed objects, pattern pieces, pieces picked on the garment), read before the
            # Move Piece handle goes (it is selected)
            from . import placement as PL_
            spec0 = store.get(context)[0]
            on = {pc['name'] for pc in spec0['pieces'] if pc.get('enabled', True)}
            local = sorted(set(PL_.selected_pieces(context)) & on)
            if not local:
                props.dress_status = tx("Local Sim did not start: select the pieces to simulate first")
                self.report({'ERROR'}, props.dress_status)
                return {'CANCELLED'}
        cls_ = type(self)
        self._pushed = cls_._pushed_before or push_before_run(context)   # Phase Sim pushed it before its flush
        cls_._pushed_before = False
        self._esc = False
        from . import piece_move as PM                    # a Move Piece handle: the moved shape stays, the handle goes
        PM.clear(context)
        try:
            ref = json.loads(self.xinit_from) if self.xinit_from else None
            cj = None
            new_placed = {}
            g = bpy.data.objects.get(garment_name(store.get(context)[0]))
            # the pieces switched Off this run leaves out of the result: their shape on screen now, kept below
            off_kept = off_shapes(context, g, store.get(context)[0])
            if self.mode == 'START' and self.button == 'SIMULATE' and g is not None and g.get(JOB_MISSING_PROP):
                # Simulate on a .blend opened without its job folder: on from the dressed garment (user 2026-10-23)
                if job_or_from_mesh(context, g):
                    self.mode = 'CONTINUE'
            if self.mode == 'CONTINUE' and phase_point(context, g) == 'start':
                # the first frame of a Phase Sim on screen, nothing changed on the garment by hand: the same run again
                # from the placement (user 2026-11-04: the sleeves were sewn at once, not after the bodice)
                self.mode = 'START'
            # the shapes on screen now, before anything is written into the garment or taken away: each piece's
            # start is compared with them after prepare (start_check; a check only, it never stops the run)
            try:
                from . import start_check as SC
                screen0 = SC.read_screen(context, store.get(context)[0], g, self.mode)
            except Exception as exc:                  # noqa: BLE001 -- the check must not stop a run
                screen0 = None
                _trace('start_check_read_failed', error=repr(exc))
            pt = None
            if self.mode in ('CONTINUE', 'LIVE', 'LOCAL'):
                job_or_from_mesh(context, g)
                # pieces Reset and shown as placed objects: their shapes now (moved or not) into the garment first
                flush_placed(context, g, props.mesh_size, new_placed=new_placed)
                # read first: a carry remakes the object. A Reset now writes the placed shapes into the frozen garment
                # (write_placed): they start from the shape on screen then (moved on it or not), sewn on as before
                redo = reset_pieces(g)
                redo_shown = frozen(g)
                if job_of(g):                             # a pattern edit the garment has not taken: carry it now
                    from . import draft_tool              # or stop (draft_tool._carry_pending, 2026-10-10)
                    try:
                        carried_ = draft_tool._carry_pending(context, g, store.get(context)[0])
                        if carried_:                  # what the carry did, said (Phase Sim said it before 2026-11-04)
                            self.report({'INFO'}, carried_)
                    except ValueError as exc:
                        raise ValueError(draft_tool.pending_refused(exc))
                    g = bpy.data.objects.get(garment_name(store.get(context)[0]))   # a carry remakes the object
                cj = job_of(g)
                if not cj and self.mode in LIVE_MODES:
                    # Live before any result (user 2026-10-22): one stage with gravity from the placement, the seams
                    # closing over its first frames (presets.build_stages); Esc keeps what it reached
                    redo, redo_shown = [], False
                elif not cj:
                    raise ValueError("Nothing to go on from: this garment has no finished result"
                                     if g is not None else "Nothing to go on from: no dressed garment")
                shown = shown_shape(context, g) if cj else None
                if self.mode == 'CONTINUE' and cj:
                    pt = phase_point(context, g)          # read after flush_placed froze it at that frame
                    pt = pt if isinstance(pt, dict) else None
            else:
                redo, redo_shown = [], False
            _trace('prepare_begin', mode=self.mode)
            self.info, (flat, F, fp, Xinit, E_st) = prepare(context, props.mesh_size, ref, cj,
                                                            shown if self.mode in ('CONTINUE', 'LIVE', 'LOCAL') else None,
                                                            live=self.mode in LIVE_MODES, redo=redo, redo_shown=redo_shown,
                                                            local=local, new_placed=new_placed, phase_at=pt)
            _trace('prepare_end', job=self.info['dir'])
        except ValueError as exc:
            # kept on the panel and in the log, not only in the status bar for a moment: a refused Live looked as
            # if it had started and done nothing (user 2026-10-13)
            what = ({'SIMULATE': 'Phase Sim', 'REDRESS': 'Restart', 'LIVE': 'Live Sim', 'LOCAL': 'Local Sim'}.get(self.button)
                    or {'START': 'Phase Sim', 'CONTINUE': 'Continue', 'LIVE': 'Live Sim', 'LOCAL': 'Local Sim'}[self.mode])
            props.dress_status = tx("{0} did not start: {1}", what, exc)
            self.report({'ERROR'}, props.dress_status)
            if self._pushed:            # what the press wrote before it was refused (flush_placed, a carry) goes
                undo_to_before(props.dress_status)
            return {'CANCELLED'}
        spec, _ = store.get(context)
        if self.info.get('state_resume', {}).get('status') == 'rebuilt':
            self.report({'WARNING'}, tx(self.info['state_resume']['reason']))
        if self.info.get('taper_left_out'):
            self.report({'WARNING'}, tx("End Taper not used this run on {0}: the fold there is held another way",
                                        ", ".join(k_.replace('|', ' ') for k_ in sorted(self.info['taper_left_out']))))
        sc_off = self._start_check(context, screen0, flat, Xinit)
        self.ob = garment_object(context, garment_name(spec), flat, F, fp, Xinit, self.info['names'])
        self.ob_name = self.ob.name
        try:                                          # the pieces that did not start as shown: red in Problems
            from . import diagnose as DG
            if sc_off:
                DG.mark(self.ob, 'start', sorted(v for r in sc_off.values() for v in r['over']),
                        "did not start as shown: " + ", ".join(sorted(sc_off)))
            else:
                DG.clear(self.ob, 'start')
        except Exception as exc:                      # noqa: BLE001
            _trace('start_check_mark_failed', error=repr(exc))
        self._paused = pause_modifiers(self.ob)
        # the result is what is seen now: the placed objects go (user 2026-10-14; they were hidden until then and stayed
        # in the scene). prepare read them above; a Reset makes them again from the pose
        PLV.drop(spec)
        for n, (fl, tr, X_) in off_kept.items():
            PLV.keep_shape(spec, n, fl, tr, X_)
        self._mk = (garment_name(spec), flat, F, fp, Xinit, self.info['names'])
        # the garment is made anew, without the previous result's link or cache: keep the link from the start, so
        # a run that fails before its first frame does not lose what it went on from (Fable review 2026-10-13: a
        # likely cause of the morning's "Nothing to continue"; _finish puts the old cache back in that case)
        self.prev_job = cj
        # Local Sim before any result: its result goes back to the placed shapes, not into a garment (user
        # 2026-10-23, for now: from the dressed garment it is the garment's, from the placement the placed shapes')
        self.local = local
        self.to_placed = self.mode == 'LOCAL' and not cj
        if cj:
            self.ob['ac9_job'] = cj
        self.k = -1
        self.proc = None
        self.t_start = time.time()
        self.solver_s = []
        pf_ = self.info.get('phase_from')
        props.dress_status = (tx("prepared in {0} s ({1}, {2} verts)", self.info['prep_s'], self.info['kind'], self.info['verts'])
                              + (tx("; on from frame {0} (stage {1} of the last Phase Sim)", pf_['timeline_frame'],
                                    pf_['stage'] + 1) if pf_ else "")
                              + (tx("; {0} modifier(s) of yours off while it runs", self._paused) if self._paused else ""))
        if self._paused:
            log(context, self.info['dir'], 'info', f"{self._paused} modifier(s) on the garment off while it runs")
        self._next_stage()
        wm = context.window_manager
        if bpy.app.background:
            while self.proc is not None or getattr(self, '_deferred_start', None):
                time.sleep(0.2)
                self._poll(context)
            self._resume(context)
            from . import display_state
            display_state.apply(context, store.get(context)[0])
            return {'FINISHED'} if not self.failed else {'CANCELLED'}
        self._window_manager = wm
        self._owner_window = context.window
        self._timer = wm.event_timer_add(0.25, window=context.window)
        type(self)._active_instance = self
        type(self)._active_timer = self._timer
        type(self)._active_wm = wm
        type(self)._active_window = context.window
        if not bpy.app.timers.is_registered(_watch_run_window):
            bpy.app.timers.register(_watch_run_window, first_interval=0.25, persistent=True)
        wm.modal_handler_add(self)
        cls = type(self)
        cls._running_mode, cls._running_t, cls._stop_req = self.mode, time.time(), False
        cls._proc, cls._stage_dirs = self.proc, tuple(self.stage_dirs)
        cls._running_job = (self.ob_name, self.info['dir'])
        cls._running_button = self.button or {'START': 'REDRESS', 'CONTINUE': 'SIMULATE', 'LIVE': 'LIVE',
                                              'LOCAL': 'LOCAL'}[self.mode]
        return {'RUNNING_MODAL'}

    failed = False
    stopped_by_user = False

    def _next_stage(self):
        """Start the solver once for every stage (solver/run_stages.py): the Python start, the warp / newton import
        and the kernel load are paid once instead of per stage."""
        self.k = 0
        d = self.info['dir']
        self.stage_dirs = [os.path.join(d, f"s{k + 1}") for k in range(len(self.info['stages']))]
        self.stage_dir = self.stage_dirs[0]
        self.log = open(os.path.join(d, 'solver_stdout.txt'), 'w', encoding='utf-8')
        env = solver_process_env(self.python, self.info.get('solver_env', {}))
        args = [os.path.join(d, 'scene.idx.npz'), os.path.join(d, 'avatar.npz'), *self.stage_dirs]
        cfg1 = os.path.join(self.stage_dirs[0], 'config.json')
        _trace('frontend_job', job=d)
        self._standby_args = (self.python, env, cfg1)
        self._deferred_start = None
        self._handoff_failed = False
        self.proc = _standby_take(self.python, env, cfg1)
        if self.proc is not None:
            # the process started after the last run (_standby_start): Python, warp and newton are in already
            self.log.close()
            _trace('request_send_begin', child_pid=self.proc.pid)
            try:
                self.proc.stdin.write(json.dumps({'args': args, 'log': self.log.name}) + '\n')
                self.proc.stdin.flush()
            except (OSError, ValueError):
                # A partial write/flush may already have reached the child. Keep its
                # owner, stop it, and use the normal failure/result-restoration path.
                # Never send an uncertain request a second time.
                retire(self.proc, self.stage_dirs)
                if not bpy.app.timers.is_registered(reap_retired):
                    bpy.app.timers.register(reap_retired, first_interval=0.25, persistent=True)
                self._handoff_failed = True
                self.log = open(os.devnull, 'w')
                _PRELOAD_SESSION.update(path=None, proc=None)
                _PRELOAD.update(state='failed', completed=0)
            else:
                _trace('request_sent', child_pid=self.proc.pid)
                self.log = open(os.devnull, 'w')
        if self.proc is None:
            def launch():
                _trace('new_process_begin')
                proc = subprocess.Popen([self.python, RUN_STAGES, *args], stdout=self.log, stderr=subprocess.STDOUT,
                                        env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                _trace('new_process_spawned', child_pid=proc.pid)
                return proc
            if retired_running():
                self._deferred_start = launch
            else:
                self.proc = launch()

    def _log_stages(self, context, done):
        """The simulation log: each stage when it starts (with the mesh it runs on) and when it ends."""
        if not hasattr(self, '_logged'):
            self._logged = set()
        d = self.info['dir']
        n_st = len(self.stage_dirs)
        h_ = (self.info.get('mesh') or {}).get('h_measured_mm')
        for k, ok in enumerate(done):
            if ok and ('end', k) not in self._logged:
                self._logged.add(('end', k))
                try:
                    rl = json.load(open(os.path.join(self.stage_dirs[k], 'run_log.json')))
                    nf = len(np.load(os.path.join(self.stage_dirs[k], 'frames.npy'), mmap_mode='r')) - 1
                    cap = int(json.load(open(os.path.join(self.stage_dirs[k], 'config.json'), encoding='utf-8')).get('frames', nf))
                except (OSError, ValueError):
                    continue
                upd = getattr(self, '_live_n', {}).get(k, 0)
                log(context, d, 'stage_end', tx("stage {0}/{1} ended: {2} frames in {3:.0f} s", k + 1, n_st, nf, rl.get('t_total', 0))
                                             + (tx(" (stopped by you)") if 'stopped at frame' in str(rl.get('error', ''))
                                                or (os.path.exists(os.path.join(self.stage_dirs[k], 'stop'))
                                                    and nf < cap)
                                                else tx(" (stopped once still)") if 0 < nf < cap else "")
                                             + tx(", the screen updated {0} times", upd),
                    stage=k + 1, frames=nf, frames_cap=cap, solver_s=rl.get('t_total'), screen_updates=upd)
                # the stage-start check (solver/start_check.py, 2026-10-19): the pieces joining this stage started
                # inside the body, through other cloth, or from another job's frames -- warned here, the run went on
                for p_ in ((rl.get('start_check') or {}).get('problems') or []):
                    log(context, d, 'start_check', f"stage {k + 1} WARNING at its start: {p_}", stage=k + 1)
                for p_ in ((rl.get('start_check') or {}).get('notes') or []):
                    log(context, d, 'start_check', f"stage {k + 1} note at its start: {p_}", stage=k + 1)
                # seam stitches whose two sides started turned against each other (over 120 degrees) get no seam
                # bending hinge: said, not left silent (user 2026-11-02, DECISIONS
                # cloth-sim-no-auto-judgment-default-rules-2026-11-02)
                _sk = int((rl.get('seam_bend') or {}).get('skipped_opposite') or 0)
                if _sk:
                    log(context, d, 'seam_bend', tx("stage {0} note: {1} seam hinges left out (the two sides started "
                                                    "turned over 120 degrees against each other)", k + 1, _sk),
                        stage=k + 1)
                # the guard's records (cfg 'guard', on by default): one line each, so a user sees them without
                # opening anomaly.json (10/08: what the system finds or does by itself goes in the log)
                for al_ in (rl.get('anomalies') or []):
                    if al_.get('kind') in ('entangle', 'crossing', 'reopened'):
                        log(context, d, 'guard', f"stage {k + 1} frame {al_.get('frame')}: {al_['kind']} "
                                                 f"{al_.get('piece', '')} ({al_.get('count')})",
                            stage=k + 1, frame=al_.get('frame'), guard=al_['kind'], count=al_.get('count'))
                # cfg 'jitter' (on by default): clusters still moving differently from their neighbors when the
                # stage ended, at least twice the rest of the garment (under that the garment is mostly still moving
                # as a whole: a shirt's no-gravity stage had 100 clusters at 1.2-1.6x), the largest first. A record;
                # the stage's whole list is in run_log.json 'jitter'
                for c_ in [c_ for c_ in ((rl.get('jitter') or {}).get('end') or [])
                           if (c_.get('x_rest_p99') or 0) >= 2.0][:3]:
                    log(context, d, 'jitter', f"stage {k + 1}: jittering at the end: {'/'.join(c_['pieces'])} "
                                              f"{c_['n']} vertices (worst {c_['worst']}: {c_['rel_mm']} mm/frame, "
                                              f"{c_.get('x_rest_p99')}x the rest, normals {c_['flick_deg']} deg/frame)",
                        stage=k + 1, verts=c_['n'], worst=c_['worst'], rel_mm=c_['rel_mm'])
        k = self.k
        if not done[k] and ('start', k) not in self._logged:
            self._logged.add(('start', k))
            log(context, d, 'stage_start', tx("stage {0}/{1}: {2}", k + 1, n_st, self.info['stages'][k])
                                           + (tx(", mesh {0:.1f} mm", h_) if h_ else ""), stage=k + 1, mesh_mm=h_)

    def _overall(self, done, f, n):
        """The share of the run done, by frames: ended stages count the frames they ran, the current one and the
        ones after it their caps (an 'until still' stage usually ends before its cap, so the share then jumps)."""
        c = self.__dict__.setdefault('_caps', {})
        tot = cur = 0
        for j, sd in enumerate(self.stage_dirs):
            if j not in c:
                try:
                    if done[j]:
                        c[j] = len(np.load(os.path.join(sd, 'frames.npy'), mmap_mode='r')) - 1
                    elif j != self.k:
                        c[j] = int(json.load(open(os.path.join(sd, 'config.json'), encoding='utf-8')).get('frames', 0))
                except (OSError, ValueError):
                    pass
            if j < self.k:
                tot += c.get(j, 0)
                cur += c.get(j, 0)
            elif j == self.k:
                tot += max(n, f)
                cur += f
            else:
                tot += c.get(j, 0)
        return min(1.0, cur / tot) if tot else 0.0

    _shown = {}                                   # the progress for the panel (P1 #7): {'frac', 'starting'}

    def _progress(self):
        try:
            with open(os.path.join(self.stage_dir, 'progress.txt')) as f:
                a, b = f.read().split()[:2]
            return int(a), int(b)
        except (OSError, ValueError):
            return 0, 0

    STALL_MIN_S = 10.0

    def _track_frames(self, f):
        """Notes when the solver's frame last moved and how long a frame takes (self._frame_s, for the Stop's wait).
        Returns the seconds without a new frame when that is far past the usual frame time, else 0."""
        now = time.time()
        key = (self.k, f)
        seen = getattr(self, '_frame_seen', None)
        if seen is None or seen[0] != key:
            if seen is not None and seen[0][0] == self.k and f > seen[0][1]:
                dt = (now - seen[1]) / (f - seen[0][1])
                old = getattr(self, '_frame_s', None)
                self._frame_s = dt if old is None else 0.7 * old + 0.3 * dt
            self._frame_seen = (key, now)
            return 0
        if not f:
            return 0
        quiet = now - seen[1]
        return quiet if quiet >= max(self.STALL_MIN_S, 10.0 * (getattr(self, '_frame_s', None) or 0.0)) else 0

    def _garment(self, context):
        """The run's garment, found again by name: an undo while the solver runs frees the Python reference (or
        brings back an older object), and writing into it killed the modal without _end (Fable review
        2026-10-13). Made anew when it is gone or has another vertex count."""
        try:
            ok = len(self.ob.data.vertices) == len(self._mk[4])
        except ReferenceError:
            ok = False
        if not ok:
            ob = bpy.data.objects.get(self.ob_name)
            if ob is None or ob.type != 'MESH' or len(ob.data.vertices) != len(self._mk[4]):
                ob = garment_object(context, *self._mk)
            self.ob = ob
            self.ob_name = ob.name
        return self.ob

    def _poll(self, context):
        props = context.scene.ac9_cloth_sim
        if getattr(self, '_deferred_start', None):
            reap_retired()
            if retired_running():
                props.dress_status = tx('Waiting for the previous Newton process to end')
                return
            launch, self._deferred_start = self._deferred_start, None
            self.proc = launch()
            type(self)._proc = self.proc
        self._garment(context)
        # the current stage: the first without a run log
        done = [os.path.exists(os.path.join(sd, 'run_log.json')) for sd in self.stage_dirs]
        self.k = min(sum(done), len(self.stage_dirs) - 1)
        self.stage_dir = self.stage_dirs[self.k]
        self._log_stages(context, done)
        live = os.path.join(self.stage_dir, 'live.npy')
        if os.path.exists(live):
            try:
                mt = os.path.getmtime(live)
                if mt != getattr(self, '_live_mt', None):
                    set_positions(self.ob, np.load(live))
                    self._live_mt = mt
                    # how often the screen took a new shape, per stage (in the log at the stage's end): the user saw
                    # the garment move only between stages (2026-10-13); this tells it from a run
                    self._live_n = getattr(self, '_live_n', {})
                    self._live_n[self.k] = self._live_n.get(self.k, 0) + 1
            except Exception:
                pass
        f, n = self._progress()
        el = time.time() - self.t_start
        stalled = self._track_frames(f)
        # the solver needs ~10 s to start (Python, warp, kernels) before its first frame: said, not shown as frame 0
        # (user 2026-10-13: a grab in Live did nothing for 15 s)
        starting = self.k == 0 and not os.path.exists(os.path.join(self.stage_dir, 'progress.txt'))
        frac = None if self.mode in LIVE_MODES else self._overall(done, f, n)
        type(self)._shown = {'frac': frac, 'starting': starting}
        if starting:
            props.dress_status = tx("Preparing the simulation ({0:.0f} s elapsed)", el)
        elif self._note is not None and time.time() < self._note[1]:
            props.dress_status = self._note[0]       # a few seconds: why a Ctrl+drag did nothing
        elif stalled:
            # the screen said "running 33 %" for as long as the solver hung in a frame (2026-11-01)
            props.dress_status = tx("No new frame for {0:.0f} s after frame {1}: the solver may be stuck. Stop ends it", stalled, f)
        elif self.mode == 'LIVE':
            if self.info.get('settings', {}).get('fast_live'):
                props.dress_status = tx("Fast Live: frame {0} ({1:.0f} s). Ctrl+drag pulls the cloth (+Alt: through cloth, Ctrl+Shift: smooth), Esc or Stop keeps it", f, el)
            else:
                props.dress_status = (tx("Live Sim: frame {0} ({1:.0f} s). Ctrl+drag pulls the cloth (+Alt: through cloth, Ctrl+Shift: smooth), Esc or Stop keeps it", f, el))
        elif self.mode == 'LOCAL':
            props.dress_status = (tx("Local Sim: frame {0} ({1:.0f} s). Ctrl+drag pulls the cloth (+Alt: through cloth, Ctrl+Shift: smooth), Esc or Stop keeps it", f, el))
        else:
            props.dress_status = (tx("stage {0}/{1}: {2}, frame {3} of at most {4} ({5:.0f} s). On the timeline every 2nd frame", self.k + 1, len(self.info['stages']), self.info['stages'][self.k], f, n, el))
        if self.proc.poll() is None:
            return
        self.log.close()
        err = None
        self.solver_s = []
        for k, sd in enumerate(self.stage_dirs):
            rl = os.path.join(sd, 'run_log.json')
            if not os.path.exists(rl):
                err = tx("stage {0} failed (see {1})", k + 1, os.path.join(sd, 'stdout.txt'))
                break
            log = json.load(open(rl))
            self.solver_s.append(log['t_total'])
            if log.get('error'):
                err = tx("stage {0}: {1}", k + 1, log['error'])
                break
        self.proc = None
        if getattr(self, '_handoff_failed', False):
            err = tx('Newton preload ended during startup; try Sim again')
        if err:
            self.failed = True
            # the frames up to the stop stay on the timeline and the place is marked, so it can be fixed by hand
            # and continued (user 2026-10-03). Esc is a stop like any other: what ran is kept (user 2026-10-13,
            # an Esc left nothing -- no frames, no result to Continue from)
            by_user = self.stopped_by_user and 'stopped at frame' in err
            level = {'INFO'} if by_user else {'ERROR'}
            try:
                self._finish(context, err)
                if by_user and getattr(self, '_dropped', 0) and self.mode in LIVE_MODES:
                    err = (tx("stopped at {0} (shape kept)", err.replace(': stopped at frame', ' frame')))
                elif by_user and getattr(self, '_dropped', 0):
                    err = (tx("stopped at {0} (shape kept)", err.replace(': stopped at frame', ' frame')))
                elif by_user:
                    err = (tx("stopped at {0}", err.replace(': stopped at frame', ' frame')))
            except NoFrame as exc:
                if exc.undone and getattr(self, '_esc', False):
                    err, level = tx("stopped before the first frame: everything is as before the press"), {'INFO'}
                elif exc.undone:
                    err = tx("{0} before its first frame: everything is as before the press", err)
                else:
                    err = tx("{0} (frames not loaded: {1})", err, exc)
            except Exception as exc:
                err = tx("{0} (frames not loaded: {1})", err, exc)
            props.dress_status = err
            self.report(level, err)
            self._standby_next()
            return
        try:
            self._finish(context)
        finally:
            self._standby_next()

    def _standby_next(self):
        # after the result is in (started earlier it took the CPU from loading it: Stop -> result +0.1 s)
        _standby_start(*getattr(self, '_standby_args', (None, None, None)))

    def _finish(self, context, err=None):
        d = self.info['dir']
        fr = []
        for k in range(len(self.info['stages'])):
            fp_ = os.path.join(d, f"s{k + 1}", 'frames.npy')
            if not os.path.exists(fp_):
                break
            fr.append(np.load(fp_))
        if not fr:
            # failed or stopped before a first frame: nothing new to show; a Continue / Live gets its previous
            # result back (its mesh, cache and link), so it can be continued again
            pj = getattr(self, 'prev_job', None)
            if pj:
                self.ob = _mesh_back(context, self.ob, pj)
                self.ob['ac9_job'] = pj         # also with no drape cache (a pattern edit carried over)
            old = os.path.join(pj, 'drape.pc2') if pj else None
            if old and os.path.exists(old):
                with open(old, 'rb') as f_:
                    n_old = struct.unpack('<12siiffi', f_.read(32))[5]
                attach_cache(context, self.ob, old, n_old)
                self.ob['ac9_job'] = pj
                refresh_thickness(context, self.ob)
            if getattr(self, '_pushed', False):
                self._undo_back = True
                raise NoFrame("no frame was written", undone=True)
            raise NoFrame("no frame was written" + ("; the previous result is back" if pj else ""))
        drop_later_done(keep=False)
        allf = np.concatenate([fr[0]] + [x[1:] for x in fr[1:]], 0)
        self._n_frames = len(allf) - 1
        # every 2nd frame, and always the last (the result): with an odd count of steps the last fell off
        allf = allf[::2] if self._n_frames % 2 == 0 else np.concatenate([allf[::2], allf[-1:]], 0)
        # a Phase Sim from a frame: the frames before it stay on the timeline, this run's after them (user 2026-11-04:
        # what follows the frame is replaced). Not when the mesh changed since (the old frames would not fit)
        pf_ = self.info.get('phase_from')
        if pf_ and pf_.get('prefix'):
            pre_ = _pc2_frames(os.path.join(pf_['job'], 'drape.pc2'), int(pf_['prefix']), allf.shape[1])
            if pre_ is None:
                pf_['prefix'] = 0
            else:
                allf = np.concatenate([pre_, allf], 0)
        pc2 = os.path.join(d, 'drape.pc2')
        write_pc2(pc2, allf)
        set_positions(self.ob, allf[-1])
        attach_cache(context, self.ob, pc2, len(allf))
        self.ob['ac9_job'] = d
        refresh_thickness(context, self.ob)
        try:                    # the buttons drawn on the result (button_view.py); never the run's failure
            BV.sync(context, store.get(context)[0], self.ob, d, allf[-1] * 1000.0)
            ZV.sync(context, store.get(context)[0], self.ob, d, allf[-1] * 1000.0)
        except Exception as e_:                 # noqa: BLE001
            print("AC9 Cloth Sim buttons:", e_)
        total = time.time() - self.t_start
        res = dict(self.info, mode=self.mode, button=type(self)._running_button or self.button, solver_s=[round(x, 2) for x in self.solver_s], solver_total_s=round(sum(self.solver_s), 2),
                   wall_s=round(total, 1), frames_cached=int(len(allf)), stage_steps=[int(len(x)) - 1 for x in fr])
        res['anomaly'] = mark_anomaly(self.ob, d, allf[-1])
        # Live keeps its last shape only: no frames to judge "still moving" by
        res['unsettled'] = 0 if self.mode in LIVE_MODES else mark_unsettled(self.ob, fr[-1])
        if err:
            res['error'] = err
        self._log_stages(context, [os.path.exists(os.path.join(sd, 'run_log.json')) for sd in self.stage_dirs])
        al_ = res.get('anomaly') or {}
        log(context, d, 'error' if err else 'done',
            (tx("stopped: {0}", err) if err
             else tx("done: solver {0:.0f} s, total {1:.0f} s, the last shape kept", sum(self.solver_s), total) if self.mode in LIVE_MODES
             else tx("done: solver {0:.0f} s, total {1:.0f} s, {2} frames on the timeline", sum(self.solver_s), total, len(allf)))
            + (tx("; {0} anomalies", al_.get('count_in_job')) if al_.get('count_in_job') else "")
            + (tx("; still moving at the end: {0} vertices (red)", res['unsettled']) if res.get('unsettled') else ""),
            solver_s=round(sum(self.solver_s), 2), wall_s=round(total, 1))
        json.dump(res, open(os.path.join(d, 'job.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        prefs = addon_prefs(context)
        dropped = 0
        # Keep Cache off: the last shape only (user 2026-10-22) -- after a run that finished or that the user stopped
        # (a Live ends with Esc), not after a failure (its frames show where it went wrong)
        by_user = bool(err) and getattr(self, 'stopped_by_user', False) and 'stopped at frame' in err
        # Live keeps its last shape only, whatever Keep Cache says (user 2026-10-23: it runs until stopped, its
        # frames are not wanted)
        if (not err or by_user) and (self.mode in LIVE_MODES or (prefs is not None and not prefs.keep_cache)):
            dropped = drop_frames(context, self.ob)
        self._dropped = dropped
        # the result is the garment's own vertices, written from Python with no undo step: the file stayed "saved"
        # and closing Blender did not ask to save, so the result could be lost (found 2026-10-13 checking P1 #6).
        # An undo step marks the file changed (and Ctrl+Z goes back to before the run)
        if not bpy.app.background:
            try:
                bpy.ops.ed.undo_push(message="AC9 Cloth Sim result")
            except RuntimeError:
                pass
        if err:
            return
        if getattr(self, 'to_placed', False):
            put, notes = self._to_placed(context, allf[-1])
            context.scene.ac9_cloth_sim.dress_status = (
                tx("done: solver {0:.0f} s; the placed shapes of {1} are the result (Phase Sim starts them from it)",
                   sum(self.solver_s), ', '.join(put) or '-') + ''.join("; " + n_ for n_ in notes))
            self.report({'INFO'}, context.scene.ac9_cloth_sim.dress_status)
            return
        al = res.get('anomaly')
        if not al:
            seen = "; no anomalies recorded"
        else:
            seen = (tx("; {0} anomalies, the first", al['count_in_job'])
                    + (tx(" (marked red)") if al.get('painted') else tx(" (not on the mesh, no red)"))
                    + tx(": stage {0} frame {1} {2} {3}", al['stage'], al['frame'], al.get('kind', ''), al.get('piece', '')))
        done = (tx("done: solver {0:.0f} s, total {1:.0f} s, {2} frames on the timeline (every 2nd frame of the {3} the stages ran)", sum(self.solver_s), total, len(allf), self._n_frames)
                if not dropped else
                tx("done: solver {0:.0f} s, total {1:.0f} s, the last shape kept", sum(self.solver_s), total) if self.mode in LIVE_MODES else
                tx("done: solver {0:.0f} s, total {1:.0f} s, the last shape kept (Keep Cache off: {2} frames not kept)", sum(self.solver_s), total, len(allf)))
        state_note = self.info.get('state_resume', {})
        if state_note.get('status') == 'rebuilt':
            done += '; ' + tx(state_note['reason'])
        elif state_note.get('status') == 'prepared':
            done += '; ' + tx('Saved simulation state resumed')
        if self.mode in LIVE_MODES:
            try:
                with open(os.path.join(self.stage_dirs[-1], 'run_log.json'), encoding='utf8') as _state_log:
                    _saved_state = json.load(_state_log).get('checkpoint', {})
                if _saved_state.get('status') == 'unavailable':
                    done += '; ' + tx('Simulation state was not saved; the shape was kept')
                    self.report({'WARNING'}, tx('The shape was kept, but simulation state could not be saved'))
            except (OSError, ValueError):
                pass
        context.scene.ac9_cloth_sim.dress_status = done + seen
        self.report({'INFO'}, context.scene.ac9_cloth_sim.dress_status)

    def _to_placed(self, context, X):
        """Local Sim from the placement: the selected pieces' last shape X (m, the run's vertices) written into their
        placed objects -- the user's shape from then on (Phase Sim starts them from it, Back to Guide drops it) --
        and the garment the run made removed: there was none before. Returns (pieces written, notes)."""
        spec = store.get(context)[0]
        d = self.info['dir']
        names = list(self.info['names'])
        offs = list(np.load(os.path.join(d, 'scene.idx.npz'))['offs']) + [len(X)]
        g = bpy.data.objects.get(self.ob_name)
        if g is not None:
            me = g.data
            bpy.data.objects.remove(g)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        r = prepare(context, context.scene.ac9_cloth_sim.mesh_size, shapes_only=True, local=self.local)
        PLV.build(context, spec, r['pat'], r['tris'], r['X'], r['pairs'])
        obs = PLV.objects(spec)
        put, notes = [], []
        for n in self.local:
            ob = obs.get(n)
            if ob is None or ob.get(PLV.PROP) != n:
                if ob is not None and obs.get(ob.get(PLV.PROP)) is ob and ob.get(PLV.PROP) not in self.local:
                    notes.append(tx("{0} follows its pair {1}: select that side", n, ob.get(PLV.PROP)))
                continue
            i = names.index(n)
            Xi = np.asarray(X[offs[i]:offs[i + 1]], np.float64)
            me = ob.data
            if len(Xi) != len(me.vertices):
                notes.append(tx("{0}: its placed object's vertices were edited, not written", n))
                continue
            Mi = np.linalg.inv(np.array(ob.matrix_world))
            co = (np.c_[Xi, np.ones(len(Xi))] @ Mi.T)[:, :3]
            me.vertices.foreach_set('co', co.astype(np.float32).ravel())
            gen = me.attributes.get(PLV.GEN_ATTR)
            if gen is not None:
                gen.data.foreach_set('vector', Xi.astype(np.float32).ravel())
            me.update()
            ob[PLV.SIM_PROP] = True
            put.append(n)
        PLV.invalidate_shown()
        return put, notes

    # ---- grab (PROVISIONAL: a first try to look at; how grabbing should feel is not decided yet)
    _grab = None                                  # (vertex ids, grabbed point, view depth point)
    GRAB_RADIUS = 0.02                            # m: vertices within this of the clicked point are held
    # Alt held: the grab passes through the other cloth (the solver frees the contacts around it), to pull back
    # cloth that has gone through cloth; a grab started with Alt holds only the piece under the mouse (user 10/25;
    # Shift until 11/02, when Shift went to the stroke brush)
    _through = False
    # the stroke brush (Ctrl+Shift+drag, user 2026-11-02, DECISIONS cloth-sim-live-stroke-2026-11-02): the brush's
    # center under the mouse and the view ray to grab.json as {"stroke": true, ...}; the solver presses the cloth
    # around it down and carries it along as the brush moves
    _stroke = None                                # the view the stroke started in: (region, rv3d, area)
    _stroke_bvh = None                            # (BVH of the shown mesh, built at time.time()), rebuilt every 0.2 s
    _stroke_id = 0                                # counts the drags: the solver carries the cloth within one only
    _note = None                                  # (status text, shown until): a Ctrl+drag that was refused

    def _grab_file(self):
        return os.path.join(self.info['dir'], 'grab.json')     # read by every stage (a grab may span two)

    def _write_grab(self, target):
        try:
            tmp = self._grab_file() + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'verts': self._grab[0], 'target': [float(x) for x in target],
                       'through': self._through}, f)
            os.replace(tmp, self._grab_file())
        except OSError:
            pass                                   # the solver may be reading it: the next move writes again

    @staticmethod
    def _view_at(context, event):
        """The 3D view under the mouse: (region, region_3d, mouse in the region, area). The modal's context is where the
        button was pressed (the sidebar), so context.region and mouse_region_x missed the cloth and the Ctrl+click
        went on to a box select (user 2026-10-13)."""
        for area in context.window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            inside = [r for r in area.regions if r.width > 1 and r.x <= event.mouse_x < r.x + r.width
                      and r.y <= event.mouse_y < r.y + r.height]
            if any(r.type != 'WINDOW' for r in inside):
                return None                       # over the sidebar / header drawn on top of the view
            for r in inside:
                return r, area.spaces.active.region_3d, (event.mouse_x - r.x, event.mouse_y - r.y), area
        return None

    def _grab_start(self, context, event):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        v = self._view_at(context, event)
        if v is None:
            return False
        region, rv3d, xy, area = v
        from . import view_visibility as VV
        # A modal's context may still be its launch sidebar. Check the actual
        # mouse viewport before copying vertices or building the grab BVH.
        with context.temp_override(area=area, region=region):
            if not VV.object_allowed(context, self.ob):
                return False
        mi = self.ob.matrix_world.inverted()
        o = mi @ region_2d_to_origin_3d(region, rv3d, xy)
        d = mi.to_3x3() @ region_2d_to_vector_3d(region, rv3d, xy)
        me = self.ob.data
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        tri = np.empty(len(me.polygons) * 3, np.int64)
        me.polygons.foreach_get('vertices', tri)
        bvh = BVHTree.FromPolygons(co.tolist(), tri.reshape(-1, 3).tolist(), all_triangles=True)
        hit = bvh.ray_cast(o, d)
        if hit[0] is None:
            return False
        p = np.array(hit[0])
        near = np.linalg.norm(co - p, axis=1) < self.GRAB_RADIUS
        self._through = bool(event.alt)
        pa = me.attributes.get('ac9_piece')
        fp = None
        if pa is not None and hit[2] is not None:
            fp = np.empty(len(me.polygons), np.int32)
            pa.data.foreach_get('value', fp)
        if self._through and fp is not None:
            # through: only the piece under the mouse; the cloth it crosses is what it has to pass (Fable 10/25 b-2)
            mine = np.zeros(len(co), bool)
            mine[tri.reshape(-1, 3)[fp == fp[hit[2]]].ravel()] = True
            near &= mine
        if fp is not None:
            held = self._held_pieces()
            refused = drop_held(json.loads(self.ob.get('ac9_piece_names', '[]')), held, fp, tri.reshape(-1, 3),
                                near, hit[2])
            if refused:
                return refused, held[refused]
        ids = np.nonzero(near)[0].tolist()
        if not ids:
            return False
        self._grab = (ids, p, region, rv3d)
        self._write_grab(p)
        return True

    def _stroke_at(self, context, event, start=False):
        """Write the stroke brush's center under the mouse and the view ray to grab.json (radius and strength as
        the panel has them now). False when no cloth is under it (a stroke going on keeps its last center)."""
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        v = self._view_at(context, event)
        if v is None:
            return False
        region, rv3d, xy, area = v
        if start:
            from . import view_visibility as VV
            with context.temp_override(area=area, region=region):
                if not VV.object_allowed(context, self.ob):
                    return False
        now = time.time()
        if start:
            self._stroke_id += 1
        if start or self._stroke_bvh is None or now - self._stroke_bvh[1] > 0.2:
            me = self.ob.data                     # the shown frame follows the solver: the BVH follows it
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get('co', co)
            tri = np.empty(len(me.polygons) * 3, np.int64)
            me.polygons.foreach_get('vertices', tri)
            self._stroke_bvh = (BVHTree.FromPolygons(co.reshape(-1, 3).tolist(), tri.reshape(-1, 3).tolist(),
                                                     all_triangles=True), now)
        mi = self.ob.matrix_world.inverted()
        ray = (mi.to_3x3() @ region_2d_to_vector_3d(region, rv3d, xy)).normalized()
        hit = self._stroke_bvh[0].ray_cast(mi @ region_2d_to_origin_3d(region, rv3d, xy), ray)
        if hit[0] is None:
            return False
        props = context.scene.ac9_cloth_sim
        try:
            tmp = self._grab_file() + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'stroke': True, 'id': self._stroke_id, 'center': [float(x) for x in hit[0]], 'view': [float(x) for x in ray],
                           'radius': float(props.stroke_radius) / 1000.0,
                           'strength': float(props.stroke_strength)}, f)
            os.replace(tmp, self._grab_file())
        except OSError:
            pass                                   # the solver may be reading it: the next move writes again
        if start:
            self._stroke = (region, rv3d, area)
        return True

    def _held_pieces(self):
        """The pieces that do not move in this Live now, {name: why}: 'frozen' (as the panel has them now, the
        params written last), 'ghost' (the stage's config) and 'outside' (a Local Sim's pieces not picked)."""
        try:
            cfg = json.load(open(os.path.join(getattr(self, 'stage_dir', ''), 'config.json'), encoding='utf-8'))
        except (OSError, ValueError):
            cfg = {}
        fz = (self._params or {}).get('frozen')
        if fz is None:
            fz = cfg.get('frozen_pieces') or []
        held = {n: 'frozen' for n in fz}
        held.update({n: 'ghost' for n in cfg.get('ghost_pieces') or []})
        act = cfg.get('active_pieces')
        if act:
            names = json.loads(self.ob.get('ac9_piece_names', '[]'))
            held.update({n: 'outside' for n in names if n not in act and n not in held})
        return held

    def _grab_move(self, context, event):
        from bpy_extras.view3d_utils import region_2d_to_location_3d
        region, rv3d = self._grab[2], self._grab[3]
        mw = self.ob.matrix_world
        tgt = region_2d_to_location_3d(region, rv3d, (event.mouse_x - region.x, event.mouse_y - region.y),
                                       mw @ Vector(self._grab[1]))
        self._write_grab(mw.inverted() @ tgt)

    _params = None                                # the live params last written (recipe.live_params)

    def _write_params(self, context):
        """Live: the fold angles, seams switched off, pieces held, elastics and gravity as the spec and the panel
        have them now, to the job's params.json when they changed (the solver eases each change in)."""
        spec = store.get(context)[0]
        if spec is None:
            return
        props = context.scene.ac9_cloth_sim
        try:
            p = recipe.live_params(spec, self.info['names'],
                                   props.local_gravity if self.mode == 'LOCAL' else props.live_gravity,
                                   active_pieces=self.local)
        except ValueError:                         # an End Taper the pattern cannot take yet: the rest goes on
            return
        if p.get('fold_terminal'):
            # the tapers this run can take (2026-11-01: one the solver refuses stopped the run where it was): the
            # stage's own checks (recipe.fit_terminal), and only on lines the run was meshed with
            if getattr(self, '_live_cfg', None) is None:
                try:
                    self._live_cfg = json.load(open(os.path.join(self.stage_dirs[0], 'config.json'), encoding='utf-8'))
                except (OSError, ValueError, IndexError):
                    self._live_cfg = {}
            c0 = self._live_cfg
            had = set(c0.get('fold_deg') or {}) | set((c0.get('experimental_fold_terminal') or {}).get('lines', {}))
            c_ = dict(c0, experimental_fold_terminal=p['fold_terminal'], fold_deg=p['fold'],
                      fold_ramp=json.loads(json.dumps(c0.get('fold_ramp'))) if c0.get('fold_ramp') else None)
            c_ = {k_: v_ for k_, v_ in c_.items() if v_ is not None}
            out = recipe.fit_terminal(c_, meshed=had)     # a ramp running now keeps its pieces
            p['fold_terminal'] = c_.get('experimental_fold_terminal')
            if out and out != getattr(self, '_taper_said', None):
                self._taper_said = out
                self.report({'WARNING'}, tx("End Taper not used this run on {0}: the fold there is held another way",
                                            ", ".join(k_.replace('|', ' ') for k_ in sorted(out))))
        if p == self._params:
            return
        try:
            f_ = os.path.join(self.info['dir'], 'params.json')
            with open(f_ + '.tmp', 'w', encoding='utf-8') as fo:
                json.dump(p, fo)
            os.replace(f_ + '.tmp', f_)
            self._params = p
        except OSError:
            pass                                   # the solver may be reading it: the next tick writes again

    _release = None                               # grab file still to remove (Windows refuses while it is read)

    def _grab_end(self):
        self._grab = None
        self._stroke = None
        self._release = self._grab_file()
        self._try_release()

    def _try_release(self):
        if self._release is None:
            return
        try:
            if os.path.exists(self._release):
                os.remove(self._release)
            if os.environ.get('AC9_SIM_DEBUG_EVENTS'):
                with open(os.path.join(self.info['dir'], 'events.txt'), 'a') as f_:
                    f_.write(f"released {self._release} exists={os.path.exists(self._release)}" + chr(10))
            self._release = None
        except OSError as exc:
            if os.environ.get('AC9_SIM_DEBUG_EVENTS'):
                with open(os.path.join(self.info['dir'], 'events.txt'), 'a') as f_:
                    f_.write(f"release failed {exc}" + chr(10))

    def cancel(self, context):
        if getattr(type(self), '_active_instance', None) is self:
            _stop_on_load()

    def modal(self, context, event):
        # an exception here would end the modal with the solver still running and the garment half replaced:
        # stop it and end as a failed run does (2026-11-01)
        try:
            return self._modal(context, event)
        except Exception as exc:                                  # noqa: BLE001
            import traceback
            traceback.print_exc()
            if self.proc is not None:
                try:
                    stop_solver(self.proc, self.stage_dirs[max(self.k, 0):], timeout=10)
                except Exception:                                 # noqa: BLE001
                    pass
                self.proc = None
            context.scene.ac9_cloth_sim.dress_status = tx("stopped: {0}", exc)
            try:
                self._finish(context, context.scene.ac9_cloth_sim.dress_status)
            except Exception:                                     # noqa: BLE001
                pass
            return self._end(context, {'CANCELLED'})

    def _modal(self, context, event):
        if getattr(self, '_abandoned', False):
            return {'CANCELLED'}
        if getattr(self, '_pending_stop', None) is not None:
            type(self)._running_t = time.time()
            type(self)._stop_req = False
            if event.type != 'TIMER':
                return {'RUNNING_MODAL'} if event.type == 'ESC' else {'PASS_THROUGH'}
            self._try_release()
            if not self._pending_stop.poll():
                if self._pending_stop.error:
                    # Keep ownership and show the actual OS error; a later exit is still reaped normally.
                    context.scene.ac9_cloth_sim.dress_status = self._pending_stop.error
                return {'RUNNING_MODAL'}
            self._pending_stop = None
            try:
                self._poll(context)
            except Exception as exc:
                self.proc = None
                context.scene.ac9_cloth_sim.dress_status = tx("stopped, but the frames were not loaded: {0}", exc)
                return self._end(context, {'CANCELLED'})
            return self._end(context, {'CANCELLED'} if self.failed else {'FINISHED'})
        if event.type in ('LEFTMOUSE',) and os.environ.get('AC9_SIM_DEBUG_EVENTS'):
            with open(os.path.join(self.info['dir'], 'events.txt'), 'a') as f_:
                f_.write(f"{event.type} {event.value} ctrl={event.ctrl} grab={self._grab is not None}" + chr(10))
        # grabbing is Live's only: Dress / Continue run fixed stages and the screen follows them only every few
        # frames, so a grab there could not be aimed (user 2026-10-13, "an operation design that does not hold")
        if (self.mode in LIVE_MODES and self.proc is not None and event.type == 'LEFTMOUSE' and event.ctrl
                and event.shift and event.value == 'PRESS'):
            if self.info.get('settings', {}).get('fast_live') and os.environ.get('AC9_TEST_FAST_E6', '1') == '0':   # tests only
                self.report({'WARNING'}, "Ctrl+Shift+drag (smooth) needs Native Live: AC9 Fast Live leaves it out")
                return {'RUNNING_MODAL'}
            if self._stroke_at(context, event, start=True):
                return {'RUNNING_MODAL'}
            if self._view_at(context, event) is not None:
                self.report({'INFO'}, "Ctrl+Shift+drag: no cloth under the mouse")
                return {'RUNNING_MODAL'}
        if (self.mode in LIVE_MODES and self.proc is not None and event.type == 'LEFTMOUSE' and event.ctrl
                and event.value == 'PRESS'):
            got = self._grab_start(context, event)
            if isinstance(got, tuple):
                piece, why = got
                if why == 'outside':
                    msg = tx("{0} is not in this Local Sim: Ctrl+drag does not move it", piece)
                    hint = msg
                elif why == 'ghost':
                    msg = tx("{0} is Off Pattern: Ctrl+drag does not move it", piece)
                    hint = msg
                else:
                    msg = tx("{0} is frozen: Ctrl+drag does not move it", piece)
                    hint = tx("{0} is frozen: Ctrl+drag does not move it (set it Active to pull it)", piece)
                self.report({'WARNING'}, hint)
                self._note = (msg, time.time() + 4.0)
                return {'RUNNING_MODAL'}
            if got:
                return {'RUNNING_MODAL'}
            if self._view_at(context, event) is not None:
                # a miss over the viewport is not passed on: it would start a box select
                self.report({'INFO'}, "Ctrl+drag: no cloth under the mouse")
                return {'RUNNING_MODAL'}
        if self._grab is not None:
            if event.type == 'MOUSEMOVE':
                self._grab_move(context, event)
                return {'RUNNING_MODAL'}
            if event.type in ('LEFT_ALT', 'RIGHT_ALT') and event.value in ('PRESS', 'RELEASE'):
                self._through = event.value == 'PRESS'
                self._grab_move(context, event)
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._grab_end()
                return {'RUNNING_MODAL'}
        if self._stroke is not None:
            if event.type == 'MOUSEMOVE':
                self._stroke_at(context, event)
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._grab_end()
                return {'RUNNING_MODAL'}
        type(self)._running_t = time.time()
        if (event.type == 'ESC' and event.value == 'PRESS') or type(self)._stop_req:
            type(self)._stop_req = False
            self._esc = True
            if self.proc is not None:
                # Live ends here as its normal end (the solver writes its frames, no error); a staged run keeps
                # the frames that ran, the stopped stage's too
                if self._grab is not None or self._stroke is not None:
                    self._grab_end()
                if self.mode not in LIVE_MODES:
                    self.stopped_by_user = True
                try:
                    between = self.k > 0 and not os.path.exists(os.path.join(self.stage_dir, 'progress.txt'))
                    self._pending_stop = PendingStop(self.proc, self.stage_dirs[self.k:],
                                                     frame_s=getattr(self, '_frame_s', None),
                                                     min_s=STAGE_START_S if between else STUCK_MIN_S)
                    context.scene.ac9_cloth_sim.dress_status = tx("stopping...")
                    return {'RUNNING_MODAL'}
                except Exception as exc:
                    self.proc = None
                    context.scene.ac9_cloth_sim.dress_status = tx("stopped, but the frames were not loaded: {0}", exc)
                    return self._end(context, {'CANCELLED'})
                return self._end(context, {'CANCELLED'} if self.failed else {'FINISHED'})
            context.scene.ac9_cloth_sim.dress_status = tx("stopped before the solver started")
            if getattr(self, '_deferred_start', None):
                self._deferred_start = None
                self.log.close()
                try:
                    self._finish(context, context.scene.ac9_cloth_sim.dress_status)
                except NoFrame as exc:
                    if exc.undone:
                        context.scene.ac9_cloth_sim.dress_status = tx(
                            "stopped before the first frame: everything is as before the press")
                except Exception:
                    pass
            return self._end(context, {'CANCELLED'})
        if event.type == 'TIMER':
            self._try_release()
            if self.mode in LIVE_MODES and self.proc is not None:
                self._write_params(context)
            try:
                self._poll(context)
            except Exception as exc:
                if self.proc is not None:
                    stop_solver(self.proc, self.stage_dirs[self.k:])
                    self.proc = None
                context.scene.ac9_cloth_sim.dress_status = tx("stopped: {0}", exc)
                self.report({'ERROR'}, context.scene.ac9_cloth_sim.dress_status)
                return self._end(context, {'CANCELLED'})
            for a in context.screen.areas:
                a.tag_redraw()
            if self.proc is None and not getattr(self, '_deferred_start', None):
                return self._end(context, {'CANCELLED'} if self.failed else {'FINISHED'})
        return {'PASS_THROUGH'}

    def _resume(self, context):
        """The person's modifiers on again (pause_modifiers), said on the status line."""
        try:
            n = resume_modifiers(bpy.data.objects.get(self.ob_name))
        except Exception:
            n = 0
        if n:
            context.scene.ac9_cloth_sim.dress_status += tx(" ({0} modifier(s) of yours on again)", n)

    def _end(self, context, r):
        drop_later_done(keep=True)
        self._resume(context)
        if getattr(self, '_undo_back', False):
            self._undo_back = False
            undo_to_before(context.scene.ac9_cloth_sim.dress_status)
        cls = type(self)
        cls._active_instance = cls._active_timer = cls._active_wm = cls._active_window = None
        cls._running_mode, cls._running_t, cls._stop_req, cls._proc, cls._stage_dirs = None, 0.0, False, None, ()
        cls._shown = {}
        cls._running_button = None
        from . import display_state
        display_state.apply(context, store.get(context)[0])
        cls._running_job = None
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        return r


class AC9_OT_SimEditFrame(bpy.types.Operator):
    """Make the frame on screen the garment's shape and go into Sculpt Mode (or Edit Mode) to fix it by hand; Live Sim or Phase Sim then goes on from it. Ctrl+Z puts the frames back"""
    # "Fix by Hand", was "Edit Frame" (user 2026-11-02, REVIEW_fable_edit_frame_ux_2026-11-02 A + D: the name says
    # what to do, and the button goes into the mode itself, so there is no "before or after Sculpt?"); the bl_idname
    # is kept for keymaps
    bl_idname = "ac9_cloth_sim.edit_frame"
    bl_label = "Fix by Hand"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        name="Mode", items=(('SCULPT', "Sculpt", "Fix it with the Sculpt brushes"),
                            ('EDIT', "Edit Mode", "Fix it vertex by vertex")), default='SCULPT')

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        g = bpy.data.objects.get(garment_name(spec)) if spec is not None else None
        # after a Live Sim the garment keeps no frames and its mesh is already its shape: pressable then too (user
        # 2026-10-24: a tangle fixed with the Sculpt brushes, then Live Sim goes on from it); the button is shown
        # only while frames play (ui.draw_fix_by_hand)
        return g is not None and (any(m.type == 'MESH_CACHE' and m.show_viewport for m in g.modifiers)
                                  or (frozen(g) and job_of(g) is not None))

    def execute(self, context):
        g = bpy.data.objects.get(garment_name(store.get(context)[0]))
        if frozen(g):
            msg = rpt("the garment's mesh is its shape: fix it, then Live Sim or Phase Sim")
        else:
            X = shown_shape(context, g)
            for m in g.modifiers:
                if m.type == 'MESH_CACHE':
                    m.show_viewport = m.show_render = False
            set_positions(g, X / 1000.0)
            g[FROM_FRAME_PROP] = int(context.scene.frame_current)   # the next Phase Sim goes on from this frame
            # one way again (user 2026-10-14: Back to Frames dropped, Ctrl+Z is the way back)
            msg = rpt("frame {0} is now the garment's shape: fix it, then Live Sim or Phase Sim (Ctrl+Z puts the frames back)", context.scene.frame_current)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if g.hide_get():
            g.hide_set(False)
        SEL.deselect_all(context)
        g.select_set(True)
        context.view_layer.objects.active = g
        bpy.ops.object.mode_set(mode=self.mode)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_SimMirrorResult(bpy.types.Operator):
    """Give the selected pieces' dressed shape to the other side of their left / right pairs, mirrored (a side grabbed into shape in Live, given to the other side). Select the pieces sewn to it too, so their seams stay together. Where the two sides close over each other (an Over on their seam, or buttons between them), the top one lies outside. Nothing is simulated: Live Sim or Phase Sim goes on from it. Ctrl+Z puts it back"""
    # user 2026-11-04 (DECISIONS cloth-sim-mirror-result-to-pair-2026-11-04): pairs only, a piece cut as one across
    # the center is fixed by hand; no settling run after it
    bl_idname = "ac9_cloth_sim.mirror_result"
    bl_label = "Mirror 3D to Pair"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        g = bpy.data.objects.get(garment_name(spec)) if spec is not None else None
        return g is not None and has_result(g) and bool(SEL.pieces(context))

    def execute(self, context):
        from .core import mirror_result as MR
        spec = store.get(context)[0]
        g = bpy.data.objects.get(garment_name(spec))
        rng = _piece_ranges(g)
        job = job_of(g)
        if rng is None or job is None:
            self.report({'WARNING'}, rpt("The garment's mesh is not its result's: Phase Sim again first"))
            return {'CANCELLED'}
        todo, notes = MR.plan(spec, SEL.pieces(context), SEL.lead(context))
        hidden = set(hidden_pieces(g, spec) or ())
        states = {pc['name']: S.piece_state(pc) for pc in spec['pieces']}
        ok = []
        for s_, t_ in todo:
            bad = [n for n in (s_, t_) if n not in rng or n in hidden or states.get(n) == 'off_all']
            if bad:
                notes.append(f"{s_} -> {t_}: " + ", ".join(bad) + " not on the dressed garment")
            else:
                ok.append((s_, t_))
        if not ok:
            self.report({'WARNING'}, "; ".join(rpt(n) for n in notes) or rpt("Select a piece of a left / right pair"))
            return {'CANCELLED'}
        try:
            pidx = np.load(os.path.join(job, 'scene.idx.npz'))
            flat, F = np.asarray(pidx['flat'], float), np.asarray(pidx['F'])
        except (OSError, KeyError, ValueError):
            self.report({'WARNING'}, rpt("The result's job folder is not readable: Phase Sim again first"))
            return {'CANCELLED'}
        freeze(context, g)                               # the frame on screen becomes the garment's mesh
        X = shown_shape(context, g).astype(float)
        cx = PLV.center_x(spec)
        tg = spec.get('guides', {}).get('torso')
        axis = (float(tg['o'][0]), float(tg['o'][1])) if tg else (cx, 0.0)
        over = spec.get('closure_over')
        done = []
        for s_, t_ in ok:
            Xt, how = MR.mirror_piece(X, flat, F, rng[s_], rng[t_], cx)
            lapped = ""
            # a closure the person marked (an Over on their seam, buttons between them) lies over as Place lays a
            # band's (closure.py order, 2 mm); nothing is guessed from the pattern (user 2026-11-02)
            side_of = {s_: MR.side(X[rng[s_][0]:rng[s_][1]], cx), t_: MR.side(Xt, cx)}.get
            marked, top_pc, _by = MR.closure_top(spec, s_, t_, side_of)
            if marked:
                Xt, n_ = MR.lap(Xt, X[rng[s_][0]:rng[s_][1]], axis, top_pc == t_)
                lapped = tx(", {0} on top", top_pc) if n_ else ""
            elif MR.sewn_together(spec, s_, t_) and over in ('left', 'right'):
                notes.append(f"{s_} / {t_}: not laid over each other (an Over on their seam or buttons between them "
                             f"mark a closure)")
            X[rng[t_][0]:rng[t_][1]] = Xt
            done.append(f"{s_} -> {t_}" + ("" if how == 'same' else tx(" (through the pattern)")) + lapped)
        set_positions(g, X / 1000.0)
        # the seams the copy opened (to a piece not copied with it, or a center seam off the body's center): said,
        # not closed here -- the next Live closes them (user 2026-11-04: no settling run after it)
        opened = MR.opened_seams(X, pidx, rng, [t for _s, t in ok])
        msg = rpt("Mirrored {0}: Live Sim or Phase Sim goes on from it", "; ".join(done))
        if opened:
            msg += rpt("; seams now apart: {0} (Live closes them; copy the pieces sewn to it with it to keep them)", ", ".join(
                f"{a}-{b} {gap:.0f} mm" for a, b, gap in opened[:4]))
        if notes:
            msg += rpt("; not done: {0}", "; ".join(notes))
        self.report({'WARNING'} if notes or opened else {'INFO'}, msg)
        return {'FINISHED'}


def frames_hide_edits(context):
    """The active object is a dressed garment in Sculpt / Edit Mode while its frames play: what is done by hand does
    not show (the Mesh Cache puts the frame's shape over it). Read by the viewport banner and the Run panel
    (REVIEW_fable_edit_frame_ux_2026-11-02 B: told, not frozen by itself)."""
    ob = context.object
    return (ob is not None and ob.type == 'MESH' and ob.mode in ('SCULPT', 'EDIT')
            and any(m.type == 'MESH_CACHE' and m.name == 'AC9_Drape' and m.show_viewport for m in ob.modifiers))


def frozen(g):
    """The garment's frames are switched off (Fix by Hand, a Reset or a piece moved on it): its mesh is its shape."""
    return g is not None and not any(m.type == 'MESH_CACHE' and m.show_viewport for m in g.modifiers)


def freeze(context, g):
    """Make the result's last shape (or, already frozen, the shape as it is) the garment's own mesh, the frames
    switched off: what is written on the mesh then shows at once, on any frame (user 2026-10-14 b-B: a Reset or a
    piece moved shows the moment it is pressed; the frames are not played again -- Ctrl+Z brings them back)."""
    if frozen(g):
        return
    # the frame kept by a Reset, else the one on screen (user 2026-11-04: until then the last frame, whatever was on
    # screen); kept, so the next Phase Sim goes on from it (phase_point)
    tf = int(g.get(FROM_FRAME_PROP, context.scene.frame_current))
    m_ = _drape(g)
    X = _pc2_frame(bpy.path.abspath(m_.filepath), tf - m_.frame_start) if m_ is not None else None
    X = X * 1000.0 if X is not None and len(X) == len(g.data.vertices) else shown_shape(context, g)
    g[FROM_FRAME_PROP] = tf
    for m in g.modifiers:
        if m.type == 'MESH_CACHE':
            m.show_viewport = m.show_render = False
    set_positions(g, X / 1000.0)


class MeshMismatch(ValueError):
    """The garment's mesh is not its result's: Reset cannot help (it writes into that mesh), Ctrl+Z or Back to Placed
    with nothing selected can (draft_tool.pending_refused says only those)."""


def write_placed(context, g, spec, names, h):
    """Write the placed shapes (hand moves kept) of pieces `names` into the garment's mesh, in place of their part of
    the result (user 2026-10-14 b-A: after a Dress the garment is the one mesh to work on). Returns the names written;
    raises ValueError when the garment's mesh is not its result's (a pattern change not carried yet)."""
    job = job_or_from_mesh(context, g)          # the job folder lost: made from the garment now, as a run does
    if job is None:
        # (Phase Sim would fail the same way: say why, design review 10/28)
        raise ValueError("The garment's result could not be made again from its mesh: no job folder could be "
                         "written next to the .blend or in the temp folder")
    pidx = np.load(os.path.join(job, 'scene.idx.npz'))
    prev = [str(n) for n in json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))['names']]
    if len(pidx['flat']) != len(g.data.vertices):
        raise MeshMismatch("The garment's mesh does not match its result (the pattern changed since). Ctrl+Z takes "
                         "the change back; Back to Placed with nothing selected puts the whole garment back")
    res = prepare(context, h, shapes_only=True)
    pat, tris = res['pat'], res['tris']
    # the placed objects went at the Dress (PLV.drop): made for a moment from the pose, read, and removed again
    made = not PLV.objects(spec)
    if made:
        PLV.build(context, spec, pat, tris, res['X'], res['pairs'])
    try:
        X, took, _ = _hand_placed(spec, pat, list(res['X']), only=set(names))
        X = _mirror_hand(X, took, res['pairs'], PLV.center_x(spec))
    finally:
        if made:
            PLV.drop(spec)
    offs = list(pidx['offs']) + [len(pidx['flat'])]
    co = np.empty(len(g.data.vertices) * 3, np.float64)
    g.data.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3) * 1000.0
    pn = [str(n) for n in pat['names']]
    done = []
    for n in names:
        if n not in prev or n not in pn:
            continue
        j, i = prev.index(n), pn.index(n)
        Vr = pidx['flat'][offs[j]:offs[j + 1]]
        V = np.asarray(pat[f"V{i}"], np.float64)
        Xi = np.asarray(X[i], np.float64)
        co[offs[j]:offs[j + 1]] = Xi if len(V) == len(Vr) and np.allclose(V, Vr, atol=1e-6) else _bary_map(Vr, V, tris[i], Xi)
        done.append(n)
    set_positions(g, co / 1000.0)
    return done


def off_shapes(context, g, spec):
    """{piece: (flat mm, triangles, world mm)}: the pieces switched Off (All) that the garment's result has, as on
    screen (not those Reset: their placed shape is theirs). Read before a run, which leaves them out of its result;
    kept as their placed shape (PLV.keep_shape), so switched On again they come back where they were simulated (user
    2026-11-03: the collar Off, the body run again, the collar On came back at its placement)."""
    rng = _piece_ranges(g) if g is not None and spec else None
    if not rng:
        return {}
    st = {pc['name']: S.piece_state(pc) for pc in spec['pieces']}
    names = [n for n in rng if st.get(n) == 'off_all' and n not in reset_pieces(g)]
    if not names:
        return {}
    try:
        flat = np.load(os.path.join(job_of(g), 'scene.idx.npz'))['flat']
    except (OSError, KeyError, TypeError):
        return {}
    co = shown_shape(context, g)
    me = g.data
    me.calc_loop_triangles()
    T = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get('vertices', T)
    T = T.reshape(-1, 3)
    out = {}
    for n in names:
        a, b = rng[n]
        t = T[(T >= a).all(1) & (T < b).all(1)] - a
        if len(t) and len(co) == len(flat):
            out[n] = (np.asarray(flat[a:b], np.float64), t, co[a:b])
    return out


# a Reset of some pieces on a dressed garment: those pieces shown as placed objects again (Place, G / R, the pose
# fields, Mirror to Pair work on them as before a Dress) and their part of the garment hidden by this modifier (the
# "reposition the selected pattern", user 2026-10-20). The next Simulate writes the objects' shapes into the garment
# (flush_placed), takes them away and sews those pieces on from there.
HIDE_MOD = "AC9_ResetHidden"
HIDE_GROUP = "AC9_reset_hidden"


def _piece_ranges(g):
    """{piece: (first vertex, end)} of the garment's mesh, from its job (None when the job does not fit the mesh)."""
    job = job_of(g)
    if not job:
        return None
    try:
        pidx = np.load(os.path.join(job, 'scene.idx.npz'))
        names = [str(n) for n in json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))['names']]
    except (OSError, KeyError, ValueError):
        return None
    if len(pidx['flat']) != len(g.data.vertices):
        return None
    ends = list(pidx['offs']) + [len(pidx['flat'])]
    return {n: (int(ends[i]), int(ends[i + 1])) for i, n in enumerate(names)}


# Which pieces are drawn as placed objects and which on the garment: decided here only (Fable review 2026-10-24
# REVIEW_fable_overlay_placket_shoulder §A). The garment's RESET_PROP is the state; the Mask modifier, the placed
# objects' visibility and ac9_piece_names follow from it and are not read as state.

def garment_pieces(g):
    """The pieces the garment's result has (its job's names; [] with no result), read once per job."""
    job = job_of(g)
    if job is None:
        if not has_result(g):
            return frozenset()
        # the job folder lost (.blend opened alone): the names the garment keeps, read once per garment and names
        job = ('blend', g.name, str(g.get('ac9_piece_names', '[]')))
        hit = _gp.get(job)
        if hit is None:
            while len(_gp) >= 8:
                _gp.pop(next(iter(_gp)))
            hit = _gp[job] = frozenset(str(n) for n in json.loads(job[2]))
        return hit
    hit = _gp.get(job)
    if hit is None:
        try:
            nm = [str(n) for n in json.load(open(os.path.join(job, 'job.json'), encoding='utf-8'))['names']]
        except (OSError, KeyError, ValueError):
            nm = [str(n) for n in json.loads(g.get('ac9_piece_names', '[]'))]
        while len(_gp) >= 8:                       # per job: two dressed garments drawn in turn do not read again
            _gp.pop(next(iter(_gp)))
        hit = _gp[job] = frozenset(nm)
    return hit


_gp = {}                                           # job folder -> the pieces of its result


def shown_as_placed(g, spec):
    """None with no result on the garment (the placement stage: every piece is its placed shape). Else the pieces
    drawn as their placed objects instead of on the garment: those put back (RESET_PROP: Back to Placed, Part to
    Seam, Band on Edge, Settle) and those the result does not have, each when it has a placed object."""
    from . import display_state as DS
    return DS.placed_names(spec, g)


def hidden_pieces(g, spec):
    """The pieces whose part of the garment is hidden (placed objects shown for them instead)."""
    sp = shown_as_placed(g, spec)
    if not sp:
        return []
    have = garment_pieces(g)
    return sorted(n for n in sp if n in have)


def set_shown_as_placed(g, spec, names):
    """Pieces `names` put back to their placed shapes on the dressed garment g (RESET_PROP), and the garment's
    hiding made to match: every tool that changes the set calls this, the drawing reads shown_as_placed."""
    names = sorted(set(names))
    if names:
        g[RESET_PROP] = json.dumps(names, ensure_ascii=False)
    elif RESET_PROP in g:
        del g[RESET_PROP]
    sync_hidden(g, spec)


def sync_hidden(g, spec):
    """The garment's Mask made to match shown_as_placed (after placed objects were made again or removed)."""
    from . import display_state as DS
    DS.apply(bpy.context, spec)


def hide_pieces(g, names):
    """Hide the garment's vertices of pieces `names` (a Mask modifier on a vertex group, first in the stack); none:
    the modifier and the group go."""
    # The mesh owns display membership, even when the external job is absent.
    ids = None
    if names:
        pa = g.data.attributes.get('ac9_piece')
        pn = json.loads(g.get('ac9_piece_names', '[]'))
        if pa is not None and pa.domain == 'FACE' and len(pa.data) == len(g.data.polygons):
            fp = np.empty(len(pa.data), np.int32)
            pa.data.foreach_get('value', fp)
            chosen = {i for i, n in enumerate(pn) if n in names}
            offv, onv = set(), set()
            for face in g.data.polygons:
                (offv if int(fp[face.index]) in chosen else onv).update(face.vertices)
            ids = sorted(offv - onv)
    vg = g.vertex_groups.get(HIDE_GROUP)
    m = g.modifiers.get(HIDE_MOD)
    if not names or ids is None or not ids:
        if m is not None:
            g.modifiers.remove(m)
        if vg is not None:
            g.vertex_groups.remove(vg)
        return
    if vg is not None:
        g.vertex_groups.remove(vg)
    vg = g.vertex_groups.new(name=HIDE_GROUP)
    vg.add(ids, 1.0, 'REPLACE')
    if m is None:
        m = g.modifiers.new(HIDE_MOD, 'MASK')
    m.vertex_group = HIDE_GROUP
    m.invert_vertex_group = True
    _thickness_last(g)


def reset_to_placed(context, g, spec, names, h):
    """Pieces `names` of the dressed garment g back to their placed shapes: shown as placed objects (only these and
    the pieces Reset before) and hidden on the garment. While its frames play they go on playing for the other pieces
    (user 2026-11-04), the frame on screen kept for the next Phase Sim to go on from (FROM_FRAME_PROP, the latest
    Reset's); flush_placed writes the shapes into the garment then. Returns the names written."""
    if not frozen(g):
        g[FROM_FRAME_PROP] = int(context.scene.frame_current)
    done = write_placed(context, g, spec, names, h)
    allr = sorted(set(reset_pieces(g)) | set(done))
    res = prepare(context, h, shapes_only=True)
    PLV.build(context, spec, res['pat'], res['tris'], res['X'], res['pairs'], only=set(allr))
    set_shown_as_placed(g, spec, allr)
    return done


def flush_placed(context, g, h, new_placed=None):
    """Before a Simulate / Continue / Live on garment g: the placed objects of pieces Reset on it (moved, placed or
    not) written into its mesh -- their pose synced into the spec first -- then the objects and the hiding go. The
    run then starts those pieces from that shape, as after a Reset. Returns the pieces written ([] if none).
    new_placed: a dict filled with {piece: (flat V, triangles, X world mm)} of the moved pieces the garment's
    result does not have, for prepare(new_placed=...) of the same run (passed, not kept: design review 10/24)."""
    if g is None:
        return []
    spec = store.get(context)[0]
    obs = PLV.objects(spec)
    # a piece the result does not have, shown as its placed object (placed after the garment was simulated): sewn on
    # from that shape as a piece Reset is (user 2026-10-25 b-2: the shape seen is the start; one made before
    # placement.give_shape marked them was started from its placement, its fold lost)
    if job_of(g) is not None:
        have = garment_pieces(g)
        # simulated pieces only: one switched Off keeps its placed object but is not sewn on (design review 10/25)
        st = {pc['name']: S.piece_state(pc) for pc in spec['pieces']}
        extra = [n for n in obs if n not in have and n not in reset_pieces(g) and st.get(n) in ('active', 'freeze')]
        if extra:
            set_shown_as_placed(g, spec, set(reset_pieces(g)) | set(extra))
    names = [n for n in reset_pieces(g) if n in obs]
    if not names:
        hide_pieces(g, [])
        return []
    sp = PLV.sync_pose(context, spec)
    if sp is not None:
        store.put(context, sp)
        spec = sp
    freeze(context, g)
    done = write_placed(context, g, spec, names, h)
    # a piece not in the garment's result (Off at the Dress, On now) moved to its seams by Part to Seam / Band on
    # Edge: its shape kept for prepare, which starts it there (user 2026-10-24, DECISIONS
    # cloth-sim-seam-tools-any-state-settle-2026-10-24); write_placed has no place for it in the garment's mesh
    new = [n for n in names if n not in done] if new_placed is not None else []
    if new:
        res = prepare(context, h, shapes_only=True)
        X, took, _ = _hand_placed(spec, res['pat'], list(res['X']), only=set(new))
        X = _mirror_hand(X, took, res['pairs'], PLV.center_x(spec))
        pn = [str(n) for n in res['pat']['names']]
        for n in new:
            if n in pn:
                i = pn.index(n)
                if i in took or any(i in pr and (pr[0] in took or pr[1] in took) for pr in res['pairs']):
                    new_placed[n] = (np.asarray(res['pat'][f"V{i}"], float).copy(), np.asarray(res['tris'][i]).copy(),
                                      np.asarray(X[i], float).copy())
    PLV.drop(spec)
    hide_pieces(g, [])
    return done


class AC9_OT_SimReset(bpy.types.Operator):
    """Return to the prepared shapes before Part to Seam and Band on Edge, keeping hand moves, mesh edits and folds. Selected pieces only; nothing selected returns the whole garment. Back to Guide also discards hand moves"""
    bl_idname = "ac9_cloth_sim.reset"
    bl_label = "Back to Placed"
    bl_options = {'REGISTER', 'UNDO'}
    to_guide: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or AC9_OT_SimDress.running():
            return False
        return bpy.data.objects.get(garment_name(spec)) is not None or bool(PLV.objects(spec))

    def _names(self, context, spec):
        """The selected pieces that are on and placed, with their pairs ([] = the whole garment)."""
        from . import placement as PL_
        on = {pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)} & set(spec.get('placement', {}))
        return sorted(PL_.with_pairs(spec, PL_.selected_pieces(context)) & on)

    def invoke(self, context, event):
        # the whole garment over a result asks first (dressing it again takes a while); a few pieces do not
        spec = store.get(context)[0]
        if not self._names(context, spec) and has_result(bpy.data.objects.get(garment_name(spec))):
            return context.window_manager.invoke_confirm(
                self, event, title="The whole garment back to placed?",
                message="The result is taken off and the next Phase Sim dresses from the placed shapes (its job "
                        "folder stays; Ctrl+Z brings it back). Select pieces first to put back only those",
                confirm_text="Back to Placed")
        return self.execute(context)

    def execute(self, context):
        spec = store.get(context)[0]
        backup = PLV.PreparedResetBackup(context, spec)
        try:
            result = self._execute_reset(context)
            if result == {'FINISHED'}:
                PLV.forget_before_seam(spec, getattr(self, '_restored_prepared', []))
                from . import piece_move as PM
                PM.clear(context)
            else:
                backup.rollback()
            return result
        except (ValueError, RuntimeError) as exc:
            backup.rollback()
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            backup.close()

    def _execute_reset(self, context):
        props = context.scene.ac9_cloth_sim
        spec, _ = store.get(context)
        PLV.leave_edit(context)
        names = self._names(context, spec)                # read before the Move Piece handle goes (it is selected)
        # Restore both live and archived placed shapes before write_placed reads them.
        # This also works on a piece added after the garment was simulated.
        try:
            reset_names = names or ({pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)}
                                   & set(spec.get('placement', {})))
            if not names and not self.to_guide:
                # the whole garment is never refused (2026-11-01, Fable review b-1): a piece whose shape from before
                # Part to Seam cannot come back stays as it is now, said in one line
                kept = []
                for n_ in sorted(reset_names):
                    try:
                        PLV.check_before_seam(spec, [n_])
                    except ValueError:
                        kept.append(n_)
                if kept:
                    reset_names = set(reset_names) - set(kept)
                    self.report({'WARNING'}, tx("{0}: kept as it is (its pattern changed after Part to Seam)",
                                                ", ".join(kept)))
            # computed shapes (a Local Sim's, one kept while Off) are not the user's: made from the numbers again
            # (user 2026-11-04: Back to Placed left three pieces in an earlier result's shape)
            from . import placement as PL_
            PLV.drop_sim_shapes(spec, PL_.with_pairs(spec, set(reset_names)))
            restored = ([] if self.to_guide else
                        PLV.restore_before_seam(spec, reset_names, consume=False))
            self._restored_prepared = restored
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if restored:
            sp = PLV.sync_pose(context, spec)
            if sp is not None:
                store.put(context, sp)
                spec = sp
        g = bpy.data.objects.get(garment_name(spec))
        if g is not None and context.object == g and context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')          # Fix by Hand's Sculpt / Edit Mode on the result
        if names and has_result(g):
            # these pieces only, on the garment itself (user 2026-10-14 b-A / b-B): the result's last shape frozen as
            # the garment's mesh and these pieces' part of it put back to their placed shapes, at once; the next
            # Simulate goes on from it and sews them on.
            # 2026-10-20: shown as placed objects again (Place / G / R move them), their part of the garment hidden
            from . import placement as PL
            spec, given = PL.give_shape(context, spec, names)
            names = [n for n in names if n not in given]
            try:
                done = reset_to_placed(context, g, spec, names, props.mesh_size) if names else []
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            done = list(done) + given
            n = len(reset_pieces(g))
            props.dress_status = (tx("{0}{1} back to placed (Place or G / R moves them); the next Phase Sim sews {2} piece(s) on, the rest goes on", ', '.join(done[:4]), ' ...' if len(done) > 4 else '', n))
            self.report({'INFO'}, props.dress_status)
            return {'FINISHED'}
        # Materialize first: a failed build must leave the result and its cache intact.
        if bpy.ops.ac9_cloth_sim.show_placed.poll():
            if bpy.ops.ac9_cloth_sim.show_placed(placed_only=True) != {'FINISHED'}:
                return {'CANCELLED'}
        # the whole garment: the result off (the job folder stays), the placed shapes shown
        if g is not None:
            me = g.data
            bpy.data.objects.remove(g)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
            from . import display_state
            display_state.apply(context, store.get(context)[0])     # the result gone: the placed shapes are shown
        props.dress_status = tx("Back to placed: the next Phase Sim dresses from the placed shapes")
        self.report({'INFO'}, props.dress_status)
        return {'FINISHED'}


class AC9_OT_SimCollider(bpy.types.Operator):
    """Use the selected objects (a garment already simulated, an accessory) as static collision objects for the next Phase Sim, or stop using them"""
    bl_idname = "ac9_cloth_sim.collider"
    bl_label = "Collision"
    bl_options = {'REGISTER', 'UNDO'}

    use: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return any(ob.type == 'MESH' for ob in context.selected_objects)

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if ob.type != 'MESH':
                continue
            if self.use:
                ob[COLLIDER_PROP] = True
            elif COLLIDER_PROP in ob:
                del ob[COLLIDER_PROP]
            n += 1
        self.report({'INFO'}, rpt("{0} object(s) {1} collision objects for Phase Sim", n, 'now' if self.use else 'no longer'))
        return {'FINISHED'}


class AC9ClothSimPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    newton_python: bpy.props.StringProperty(
        name="Newton Python", subtype='FILE_PATH',
        description="python.exe of an environment with newton and warp-lang (development setup; empty: the "
                    "release's bundled ones in Blender's own Python)",
        default="")

    # the frames on the timeline (the job's drape.pc2 and the stages' frames) kept after a run, or only the last
    # shape (drop_frames: Simulate still goes on from it), user 2026-10-22: some do not want the cache at all
    keep_cache: bpy.props.BoolProperty(
        name="Keep Cache",
        description="Keep the frames of a Phase Sim / Live Sim on the timeline (the job's cache files). Off: after each "
                    "run only the last shape is kept, the frames and their files deleted (Phase Sim still goes on "
                    "from the last shape)",
        default=True)

    # the solver process kept started between runs (_standby_start, standby_wake), user 2026-10-27 (DECISIONS
    # cloth-sim-work-screen-layout-2026-10-27): the only place it shows; off ends the waiting process at once
    keep_solver_ready: bpy.props.BoolProperty(
        name="Keep the Solver Ready",
        description="Start the simulation faster (by 3 to 5 s): the solver is started ahead of the next run and "
                    "waits, holding about 200 MB of GPU memory. It ends after 15 minutes unused. Off: every run "
                    "starts the solver anew",
        default=True, update=lambda self, context: standby_wake(context) if self.keep_solver_ready else standby_end())

    # the how-to lines in the panels ("Pick pieces, press on a guide, ..."), on for a first user; off once known
    # (user 2026-11-04: the panels are easier to read without them)
    show_hints: bpy.props.BoolProperty(
        name="Show Hints",
        description="Show the how-to lines in the panels (what to press next)",
        default=True)

    def draw(self, context):
        self.layout.prop(self, "newton_python")
        self.layout.prop(self, "keep_cache")
        self.layout.prop(self, "keep_solver_ready")
        self.layout.prop(self, "show_hints")


class AC9_OT_SimDeleteCache(bpy.types.Operator):
    """Delete the frames of the last Phase Sim / Live Sim and their cache files: the garment keeps the shape it shows, and
    Phase Sim still goes on from it. The frames cannot be brought back (Ctrl+Z gives the shape back, not the frames)"""
    bl_idname = "ac9_cloth_sim.delete_cache"
    bl_label = "Delete Cache"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        if spec is None or AC9_OT_SimDress.running():
            return False
        g = bpy.data.objects.get(garment_name(spec))
        if g is None or not any(m.type == 'MESH_CACHE' for m in g.modifiers):
            cls.poll_message_set("No cache: Phase Sim first")
            return False
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Delete Cache",
                                                     message="The frames go; the shape shown is kept",
                                                     confirm_text="Delete")

    def execute(self, context):
        spec, _ = store.get(context)
        g = bpy.data.objects.get(garment_name(spec))
        had = any(m.type == 'MESH_CACHE' for m in g.modifiers)
        n = drop_frames(context, g)
        # a cache whose files are gone already (a copy of the file, its job folder shared): the modifier goes, 0 files
        if not n and any(m.type == 'MESH_CACHE' for m in g.modifiers) or not had:
            self.report({'WARNING'}, rpt("Nothing deleted"))
            return {'CANCELLED'}
        context.scene.ac9_cloth_sim.dress_status = rpt("cache deleted ({0} files), the shape shown kept", n)
        self.report({'INFO'}, context.scene.ac9_cloth_sim.dress_status)
        return {'FINISHED'}


class AC9_OT_SimLog(bpy.types.Operator):
    """Show the simulation log of the last Phase Sim / Live Sim: the mesh of each stage, a shape carried from
    another mesh, the stages and the result (also in the Text AC9_ClothSim_Log and the job's log.jsonl)"""
    bl_idname = "ac9_cloth_sim.log"
    bl_label = "AC9 Simulation Log"

    def _job(self, context):
        obs = [o for o in bpy.data.objects if o.get('ac9_job') and os.path.isdir(str(o['ac9_job']))]
        ob = context.active_object if context.active_object in obs else None
        if ob is None and obs:
            ob = max(obs, key=lambda o: str(o['ac9_job']))
        return str(ob['ac9_job']) if ob else None

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        import textwrap
        job = self._job(context)
        rows = simlog.lines(job, 30) if job else []
        if not rows:
            tb = bpy.data.texts.get(LOG_TEXT)
            rows = [ln.body for ln in tb.lines if ln.body][-30:] if tb else []
        col = self.layout.column(align=True)
        if job:
            col.label(text=os.path.basename(job), icon='FILE_FOLDER')
        if not rows:
            col.label(text="No log yet (Phase Sim, Restart or Live Sim writes it)")
        for r in rows:
            for i, ln in enumerate(wrap(r, 80) or ['']):
                col.label(text=ln if i == 0 else '    ' + ln)

    def execute(self, context):
        return {'FINISHED'}


CLASSES = (AC9ClothSimPreferences, AC9_OT_SimDress, AC9_OT_SimEditFrame, AC9_OT_SimMirrorResult, AC9_OT_SimReset, AC9_OT_SimCollider,
           AC9_OT_SimLog, AC9_OT_SimDeleteCache)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    if _stop_on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_stop_on_load)
    for h in [h for h in bpy.app.handlers.undo_post if getattr(h, '__name__', '') == '_drop_missing_cache']:
        bpy.app.handlers.undo_post.remove(h)
    bpy.app.handlers.undo_post.append(_drop_missing_cache)
    for h in [h for h in bpy.app.handlers.load_post if getattr(h, '__name__', '') == '_relink_jobs']:
        bpy.app.handlers.load_post.remove(h)
    bpy.app.handlers.load_post.append(_relink_jobs)
    for h in [h for h in bpy.app.handlers.load_post if getattr(h, '__name__', '') == '_wake_on_load']:
        bpy.app.handlers.load_post.remove(h)
    bpy.app.handlers.load_post.append(_wake_on_load)
    _ensure_preload_timer()


def unregister():
    # Stop or transfer the active child before removing its RNA and event timer.
    _stop_on_load()
    for h in [h for h in bpy.app.handlers.load_post if getattr(h, '__name__', '') == '_wake_on_load']:
        bpy.app.handlers.load_post.remove(h)
    for t in (_wake_now, _standby_idle, _watch_run_window, _preload_tick):
        if bpy.app.timers.is_registered(t):
            bpy.app.timers.unregister(t)
    for h in [h for h in bpy.app.handlers.load_pre if getattr(h, '__name__', '') == '_stop_on_load']:
        bpy.app.handlers.load_pre.remove(h)
    for h in [h for h in bpy.app.handlers.load_post if getattr(h, '__name__', '') == '_relink_jobs']:
        bpy.app.handlers.load_post.remove(h)
    for h in [h for h in bpy.app.handlers.undo_post if getattr(h, '__name__', '') == '_drop_missing_cache']:
        bpy.app.handlers.undo_post.remove(h)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
