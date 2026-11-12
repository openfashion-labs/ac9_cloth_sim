"""AC9 Cloth Sim solver runner (copied from the sim_spike experiment's newton/run_newton_vbd.py, 2026-09-28,
and pinned here; changes: progress lines, a stop file, the latest frame written while running).

Newton (NVIDIA, Apache-2.0) SolverVBD on the same sewing scene -- solver-specific part, runs OUTSIDE Blender
in the isolated venv (sim_spike/newton_env).

Same inputs as the earlier solver's run (from prep/assemble_b52.py's .idx.npz and the avatar dump):
  cloth = initial placement (flat pieces wrapped isometrically -> triangle rest poses == the flat pattern),
  bending rest angle = 0 (flat), stitches = zero-rest-length springs, body = static triangle mesh.
Stitched vertex pairs are excluded from vertex-triangle self contact (otherwise the contact margin keeps seams open).
Writes frames.npy (metres, same vertex order) + run_log.json with wall time per frame.

Usage: newton_env/Scripts/python.exe run_newton_vbd.py <scene.blend.idx.npz> <avatar_dump.npz> <config.json> <out dir>
"""
import sys, os, json, time
from lifecycle_trace import mark as _trace
_trace('solver_import_begin')
# where the time goes (sim_latency 2026-10-20): seconds since this stage started (run_stages.py passes its clock as
# _AC9_T0; run alone, since this file started), written to run_log.json 'timing'. Only clock reads, no result changes.
_T0 = globals().get('_AC9_T0') or time.perf_counter()
TIMING = {'wall0': round(time.time() - (time.perf_counter() - _T0), 3)}


def _tmark(k):
    TIMING[k] = round(time.perf_counter() - _T0, 3)
    _trace('timing_' + k)
    if globals().get('_AC9_PREPARED_GATE') is not None:
        _AC9_PREPARED_GATE.progress(k)


import numpy as np
import warp as wp
# Orthotropic membrane (2026-10-17, DECISIONS cloth-sim-aniso-membrane-first-2026-10-17, tested in
# experiments/aniso_membrane_2026-10-17 and aniso_regress_2026-10-17): the membrane model is a compile-time switch of
# the patched particle kernels (env read when they import), so it is set from the config before `import newton`.
# cfg 'membrane': 'bw' = orthotropic Baraff-Witkin (ku = tri_ke along flat x, kv = tri_ka along flat y, ks = tri_ks),
# anything else = the isotropic neo-Hookean as before. 'bw_comp_ratio' / 'bw_comp_width' / 'bw_comp_scale' = the
# smooth compression softening of the BW stretch axes (ratio < 0 or absent = off).
_cfg0 = json.load(open(sys.argv[3], encoding='utf-8')) if len(sys.argv) > 3 else {}
FAST_LIVE = bool(_cfg0.get('fast_live'))
NATIVE_THREE = bool(_cfg0.get('native_three'))
if NATIVE_THREE:
    from native_three_contract import validate_config as _validate_native_three
    _validate_native_three(_cfg0)
# Live before sewn (2026-11-04, user): until every pattern stitch is sewn, a Fast Live run is the product's Live -
# 10 substeps of 1/(fps x 10) and the body's default contact damping. From the placed pieces the 2 substeps with no
# damping closed the seams hard and pressed the cloth into the body (user's 4-piece shirt: 114 points 1 mm+ inside,
# deepest 9.7 mm; Live 0), and those runs never became sewn. At the first frame start that is sewn: 2 substeps, the
# correction and the contract's damping (body_kd), for the rest of the run. AC9_TEST_PRESEWN_LIVE=0 = the old run.
# With it the same shirt matches Live within 0.009 mm until sewn (frame 69), 0 points inside to frame 120.
PRESEWN = FAST_LIVE and os.environ.get('AC9_TEST_PRESEWN_LIVE', '1') != '0'
if FAST_LIVE:
    import fast_live_contract as _fast_contract
    _fast_contract.validate_config(_cfg0)
    from fast_live.runtime import FastLiveError as _FastLiveError

os.environ['NEWTON_VBD_LAYER_RECOVERY_FRICTION'] = str(float(_cfg0.get('layers_recovery_friction', 1.0)))
if not _cfg0.get('layers_crossing_cache', True):
    os.environ['NEWTON_VBD_LAYER_CACHE_AUDIT'] = '1'
MEMBRANE_BW = _cfg0.get('membrane') == 'bw'
if MEMBRANE_BW:
    os.environ['NEWTON_VBD_MEMBRANE'] = 'bw'
    for _k, _e in (('bw_comp_ratio', 'NEWTON_VBD_BW_COMP_RATIO'), ('bw_comp_width', 'NEWTON_VBD_BW_COMP_WIDTH'),
                   ('bw_comp_scale', 'NEWTON_VBD_BW_COMP_SCALE'), ('bw_hess_reg', 'NEWTON_VBD_BW_HESS_REG')):
        if _cfg0.get(_k) is not None:
            os.environ[_e] = str(_cfg0[_k])
# All of the solver's changes to Newton live in solver/newton_patch/ (two files, Apache-2.0 like Newton; TASKS 83,
# user 2026-10-19): Newton's own modules of those names are loaded from there in every run, whatever the membrane, so
# the installed Newton is PyPI's newton 1.6.0 unchanged (the release ZIP bundles that wheel). They load only on the
# Newton they were made from: the installed originals' sha256 must be one of NEWTON_BASE_SHA256 (the one place they
# are written; cloth_sim_dev/release/build_zip.py reads this table to check the wheel it bundles); else stop with the
# reason rather than mix two versions. The first value is PyPI's file. particle_vbd_kernels.py also takes the sim_spike
# development venv's in-place patch of 2026-09-25 (compression / buckling, now inside newton_patch/ too) until that
# venv is put back to PyPI's newton: the module is replaced either way, the file is only checked for the version.
NEWTON_BASE_SHA256 = {
    'particle_vbd_kernels.py': ('7bd167d0b81e6bbb51c73a65dbfea7a86652788fed5388ed85266281450d838d',
                                '8859024160f0a4079ed9bd84acc5a5ee4be9486cc10557a1dad9f921040ec5f5'),
    'solver_vbd.py': ('2eaf20bde0cfa436bdaad375b689f5e562130667801ceb3e6098343f4418bc13',),
}
import hashlib, importlib.abc, importlib.util
_KMOD = 'newton._src.solvers.vbd.particle_vbd_kernels'
_SMOD = 'newton._src.solvers.vbd.solver_vbd'
_KPATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'newton_patch', 'particle_vbd_kernels.py')
_SPATCH = os.path.join(os.path.dirname(_KPATCH), 'solver_vbd.py')
if _KMOD in sys.modules:
    # run_stages.py runs every stage in one process: newton (and its compile-time switches) came in with the
    # first stage, so a later stage can only check that it got the patched kernels in the same mode
    _km = sys.modules[_KMOD]
    if os.path.normcase(os.path.abspath(_km.__file__)) != os.path.normcase(_KPATCH):
        sys.exit(f"Newton's VBD kernels were loaded from {_km.__file__}, not the add-on's solver/newton_patch")
    if bool(int(_km.SIMSPIKE_MEMBRANE_BW)) != MEMBRANE_BW:
        sys.exit("the membrane ('bw' or not) must be the same in every stage of the run (Newton keeps the first)")
else:
    _vbd = os.path.join(os.path.dirname(importlib.util.find_spec('newton').origin), '_src', 'solvers', 'vbd')
    for _f, _ok in NEWTON_BASE_SHA256.items():
        if hashlib.sha256(open(os.path.join(_vbd, _f), 'rb').read()).hexdigest() not in _ok:
            sys.exit(f"the add-on's solver/newton_patch/{_f} was made from newton 1.6.0 as on PyPI; "
                     f"{os.path.join(_vbd, _f)} differs (install that Newton or update solver/newton_patch)")

    class _KernelPatch(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == _KMOD:
                return importlib.util.spec_from_file_location(fullname, _KPATCH)
            if fullname == _SMOD:
                return importlib.util.spec_from_file_location(fullname, _SPATCH)
            return None
    sys.meta_path.insert(0, _KernelPatch())
import newton
_tmark('imported')
# Newton's self-contact n-ring filters built with numpy (solver/nring_filter.py, sim_latency 2026-10-20): the same lists
# element for element, 3-6 s less at every stage's start. Env AC9_NRING_FAST=0 = Newton's own Python loop.
if os.environ.get('AC9_NRING_FAST', '1') != '0':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nring_filter as _NRF
    import newton._src.geometry.tri_mesh_collision as _TMC
    _NRF.install(_TMC)
if globals().get('_AC9_PREWARM'):
    # run_stages.py --standby: only the imports above (the kernels' compile-time switches come from this config)
    raise SystemExit
print('newton', newton.__file__, 'membrane', 'bw' if MEMBRANE_BW else 'nh', flush=True)

idx_f, av_f, cfg_f, out = sys.argv[1:5]
cfg = json.load(open(cfg_f, encoding='utf-8')); os.makedirs(out, exist_ok=True)
_NATIVE_RUNTIME = None
_PREPARED_GATE = globals().get('_AC9_PREPARED_GATE')
if NATIVE_THREE and not globals().get('_AC9_PREWARM') and _PREPARED_GATE is None and ('native_runtime_dir' in cfg or 'native_runtime_token' in cfg):
    from native_runtime_io import WorkerRuntime
    _NATIVE_RUNTIME = WorkerRuntime(cfg, out)
    _runtime_cleanup = globals().get('_AC9_CLEANUP')
    if _runtime_cleanup is not None:
        _runtime_cleanup(_NATIVE_RUNTIME.close)
    else:
        import atexit
        atexit.register(_NATIVE_RUNTIME.close)
    _NATIVE_RUNTIME.start()
LIVE_OUT = str(_NATIVE_RUNTIME.directory) if _NATIVE_RUNTIME is not None else out
d = dict(np.load(idx_f))
N_FULL = len(d['Xinit'])
_tmark('loaded')
X_full = d['Xinit'].copy()                   # mm, full garment; frames.npy is always written full-size
d_flat_full = d['flat'].copy()               # mm, full garment (d['flat'] is cut to the simulated subset below)

# ---- staged simulation (sim_spike 2026-09-25): start from a previous stage, simulate a subset, freeze pieces
pat_names = list(np.load(cfg['pattern'])['names']) if cfg.get('pattern') else None
offs_full = list(d['offs']) + [N_FULL]
v_piece = np.searchsorted(d['offs'], np.arange(N_FULL), side='right') - 1
# the spec seam id of each stitch (scene.idx 'E_seam', from pattern_build), by its two full-garment vertices
SEAM_OF = ({frozenset((int(a_), int(b_))): str(s_) for (a_, b_), s_ in zip(d['E_st'], d['E_seam'])}
           if 'E_seam' in d else {})
if cfg.get('init_from'):
    prev = np.load(cfg['init_from'])[-1] * 1000.0
    take = np.ones(N_FULL, bool)
    if cfg.get('init_from_pieces') is not None:
        take = np.isin(v_piece, [pat_names.index(n) for n in cfg['init_from_pieces']])
    X_full[take] = prev[take]
    d['Xinit'] = X_full.copy()
# cfg 'lay' (core/lay.py, solver/lay.py; user 2026-10-21): a band joining here laid on its partners' sewn edge,
# where they are now; checked against where it was placed, never stopped
LAY_LOG = None
if cfg.get('lay') and pat_names is not None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import lay as _LAY
    _av = np.load(av_f)
    _act = np.isin(v_piece, [pat_names.index(n) for n in cfg.get('active_pieces', pat_names) if n in pat_names])
    X_full, LAY_LOG = _LAY.apply(cfg, X_full.astype(np.float64), np.asarray(d['flat'], np.float64), v_piece,
                                 np.asarray(d['E_st'], np.int64), np.asarray(d['F'], np.int64),
                                 np.asarray(d['fp'], np.int64), pat_names, _av['body_co'] * 1000.0,
                                 np.asarray(_av['body_tri'], np.int64), _act)
    X_full = X_full.astype(d['Xinit'].dtype)
    d['Xinit'] = X_full.copy()
    print('lay', LAY_LOG, flush=True)
# cfg 'stack_under': [{"verts": [full index], "tri": [[a, b, c] full index], "w": [[w0, w1, w2]], "depth_mm"}] --
# put each vertex on its point of another layer (barycentric on that layer's triangle) depth_mm towards the body,
# before anything else: a layer placed under another one before the seams draw them together (the side panel's
# vent extension under the back's folded flap, as the reference drape has them; dress.py AC9_SIM_STACK_UNDER)
if cfg.get('stack_under'):
    from spatial import cKDTree as _KD
    _bk = _KD(np.load(av_f)['body_co'] * 1000.0)
    _moved = []
    for g in cfg['stack_under']:
        v_ = np.asarray(g['verts'], np.int64); t_ = np.asarray(g['tri'], np.int64); w_ = np.asarray(g['w'], float)
        A_, B_, C_ = X_full[t_[:, 0]], X_full[t_[:, 1]], X_full[t_[:, 2]]
        P_ = w_[:, :1] * A_ + w_[:, 1:2] * B_ + w_[:, 2:] * C_
        n_ = np.cross(B_ - A_, C_ - A_); n_ /= np.linalg.norm(n_, axis=1, keepdims=True) + 1e-18
        _, bi = _bk.query(P_)
        n_ *= np.sign(((_bk.data[bi] - P_) * n_).sum(1) + 1e-12)[:, None]      # towards the body
        Q_ = P_ + float(g.get('depth_mm', 3.5)) * n_
        _moved.append(float(np.median(np.linalg.norm(Q_ - X_full[v_], axis=1))))
        X_full[v_] = Q_
    d['Xinit'] = X_full.copy()
    STACK_LOG = {'groups': len(cfg['stack_under']), 'moved_mm_p50': [round(x_, 1) for x_ in _moved]}
else:
    STACK_LOG = None
# vertices on the inside of a piece (not on its outline) = internal-line seam ends (pockets, patches)
nb_of = np.load(cfg['pattern'])['nb'] if cfg.get('pattern') else None
interior_full = (np.arange(N_FULL) - np.array(offs_full)[v_piece] >= nb_of[v_piece]) if nb_of is not None \
    else np.zeros(N_FULL, bool)
# static_pieces: garment pieces that are already on and stay put (an inner layer) join the body as a static triangle
# mesh instead of being simulated as frozen cloth -- the solver then only pays for the outer garment (2026-09-28)
STATIC_TRI = np.zeros((0, 3), np.int64)
if cfg.get('static_pieces'):
    _sp = [pat_names.index(n) for n in cfg['static_pieces']]
    STATIC_TRI = d['F'][np.isin(d['fp'], _sp)].astype(np.int64)
sel = np.arange(N_FULL)
F_FULL, FP_FULL = d['F'].copy(), d['fp'].copy()
# cfg 'bind_verts' = [{"piece", "line", "verts": [full index]}]: vertices taken out of the simulation that follow the
# rest of their piece (a back vent folded and stitched down, dress.py AC9_SIM_FOLD_BAKE): bound here to the nearest
# triangle of the piece's other vertices (barycentric + distance along its normal), rebuilt in every frame written
# cfg 'one_layer' = [{"piece", "line", "keep", "verts": the side taken out, "roll": the line, "deg", "gap_mm"}]
# (dress.one_layer_rows; user 2026-10-24, DECISIONS cloth-sim-fold-one-layer-2026-10-24): a fold simulated as one
# layer. The side taken out is bound like bind_verts, but where it goes comes from the flat pattern, not from this
# stage's start (a start with the two layers already through each other kept them so): turned rigidly about the
# line's chord by the fold angle, gap_mm further off, then held in the frame of the nearest triangle of the side
# kept (barycentric + an offset along that triangle's edge, its cross and its normal). In / Out picks the normal's
# side by the body. Its weight and a second layer's bending go to the side kept below (ONE_UNDER)
OL = [g for g in (cfg.get('one_layer') or []) if len(g.get('verts', ())) and len(g.get('roll', ())) >= 2]
# only for a piece this stage solves, never under weld (dress.one_layer_for_stage does the same; a row for a piece
# not active left its side taken out meshed alone, or stopped on 'outside the active pieces')
OL_SKIPPED = []
_ol_out = set(cfg.get('frozen_pieces') or []) | set(cfg.get('ghost_pieces') or [])
for g in list(OL):
    if cfg.get('weld') or (cfg.get('active_pieces') and g['piece'] not in cfg['active_pieces']) or g['piece'] in _ol_out:
        OL.remove(g)
        OL_SKIPPED.append({'piece': g['piece'], 'line': g['line'], 'skipped': 'weld' if cfg.get('weld') else 'not solved'})
if cfg.get('weld'):
    cfg.pop('one_layer', None)
OL_V = (np.unique(np.concatenate([np.asarray(g['verts'], np.int64) for g in OL])) if OL
        else np.zeros(0, np.int64))
BIND_V = (np.unique(np.concatenate([np.asarray(b['verts'], np.int64) for b in cfg['bind_verts']]))
          if cfg.get('bind_verts') else np.zeros(0, np.int64))
BIND_V = np.union1d(BIND_V, OL_V)
BIND = None
ONE_UNDER = None            # (bound full ids, their triangles (k, 3) full ids, barycentric (k, 3)) of the one_layer side
ORPHANS = np.zeros(0, np.int64)
if len(BIND_V):
    def _closest_on_tris(p, A, B, C):
        """closest point of p on each triangle (A, B, C: (m, 3)) -> barycentric (m, 3), squared distance (m,)"""
        ab, ac, ap = B - A, C - A, p - A
        d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
        bp = p - B; d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
        cp = p - C; d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
        va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
        den = np.where(np.abs(va + vb + vc) < 1e-30, 1e-30, va + vb + vc)
        w = np.stack([va / den, vb / den, vc / den], 1)
        w = np.clip(w, 0.0, None); w /= np.maximum(w.sum(1, keepdims=True), 1e-12)   # approximate clamp outside
        q = w[:, :1] * A + w[:, 1:2] * B + w[:, 2:] * C
        return w, ((q - p) ** 2).sum(1)
    def _tri_frame(A, B, C):
        """per triangle (edge AB, normal x edge, normal), unit (m, 3) each"""
        e1 = B - A; e1 /= np.linalg.norm(e1, axis=1, keepdims=True) + 1e-18
        n = np.cross(B - A, C - A); n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-18
        return e1, np.cross(n, e1), n
    Xb = X_full.astype(np.float64)          # mm, this stage's start
    bt, bw, bd = [], [], []                 # bd: offset (mm) along the bound triangle's (edge, cross, normal)
    for v in np.setdiff1d(BIND_V, OL_V):
        pc_ = v_piece[v]
        m_ = (FP_FULL == pc_) & ~np.isin(F_FULL, BIND_V).any(1)
        T_ = F_FULL[m_]
        A_, B_, C_ = Xb[T_[:, 0]], Xb[T_[:, 1]], Xb[T_[:, 2]]
        w_, d2_ = _closest_on_tris(Xb[v], A_, B_, C_)
        k_ = int(np.argmin(d2_))
        n_ = np.cross(B_[k_] - A_[k_], C_[k_] - A_[k_]); n_ /= np.linalg.norm(n_)
        q_ = w_[k_] @ np.stack([A_[k_], B_[k_], C_[k_]])
        bt.append(T_[k_]); bw.append(w_[k_]); bd.append([0.0, 0.0, float((Xb[v] - q_) @ n_)])
    if cfg.get('bind_depth_mm') is not None and bd:
        # the flap drawn this close to its piece (it is stuck on and has no contact; at the prefold's 3 mm the side
        # panel's extension placed under it showed through it, 2026-10-01)
        for r_ in bd:
            r_[2] = float(np.sign(r_[2]) * min(abs(r_[2]), float(cfg['bind_depth_mm'])))
    bv_order = list(np.setdiff1d(BIND_V, OL_V))
    OL_LOG = []
    if OL:
        _fl = np.asarray(d['flat'], np.float64)                     # mm, full garment, before any subset
        from spatial import cKDTree as _KDo
        _bko = _KDo(np.load(av_f)['body_co'] * 1000.0)
        for g in OL:
            gv = np.asarray(g['verts'], np.int64); gr = np.asarray(g['roll'], np.int64)
            pc_ = v_piece[gv[0]]
            T_ = F_FULL[(FP_FULL == pc_) & ~np.isin(F_FULL, BIND_V).any(1)]
            if not len(T_):
                OL_LOG.append({'piece': g['piece'], 'line': g['line'], 'skipped': 'no triangle left on the side kept'})
                BIND_V = np.setdiff1d(BIND_V, gv)
                OL_SKIPPED.append(OL_LOG[-1])
                continue
            r2 = _fl[gr]
            c_ = r2.mean(0)
            u_ = np.linalg.svd(r2 - c_, full_matrices=False)[2][0]
            w2 = np.array([-u_[1], u_[0]])
            if np.median((_fl[gv] - c_) @ w2) < 0:
                w2 = -w2
            th = np.radians(abs(float(g.get('deg', 180.0))))
            t_, s_ = (_fl[gv] - c_) @ u_, (_fl[gv] - c_) @ w2
            # the side taken out turned about the chord towards flat +z, gap_mm further off (flat frame, mm)
            P = np.zeros((len(gv), 3))
            P[:, :2] = c_ + t_[:, None] * u_ + (s_ * np.cos(th))[:, None] * w2
            P[:, 2] = s_ * np.sin(th) + float(g.get('gap_mm', 0.5))
            F3 = np.zeros((len(_fl), 3)); F3[:, :2] = _fl
            A_, B_, C_ = F3[T_[:, 0]], F3[T_[:, 1]], F3[T_[:, 2]]
            e1f, e2f, nf = _tri_frame(A_, B_, C_)
            gt, gw, go = [], [], []
            for p_ in P:
                w_, d2_ = _closest_on_tris(p_, A_, B_, C_)
                k_ = int(np.argmin(d2_))
                o_ = p_ - w_[k_] @ np.stack([A_[k_], B_[k_], C_[k_]])
                gt.append(T_[k_]); gw.append(w_[k_]); go.append([o_ @ e1f[k_], o_ @ e2f[k_], o_ @ nf[k_]])
            gt, gw, go = np.asarray(gt, np.int64), np.asarray(gw), np.asarray(go)
            # flat +z is the body's side of these triangles, or not: by where they are now (3D normals, same winding)
            A3, B3, C3 = Xb[gt[:, 0]], Xb[gt[:, 1]], Xb[gt[:, 2]]
            ctr = (A3 + B3 + C3) / 3.0
            n3 = np.cross(B3 - A3, C3 - A3); n3 /= np.linalg.norm(n3, axis=1, keepdims=True) + 1e-18
            _, bi_ = _bko.query(ctr)
            towards = np.sign(((_bko.data[bi_] - ctr) * n3).sum(1) * np.sign(go[:, 2]))
            inward = float(g.get('deg', 180.0)) > 0
            if (np.median(towards) > 0) != inward:
                go[:, 2] = -go[:, 2]
            bt += list(gt); bw += list(gw); bd += go.tolist(); bv_order += list(gv)
            OL_LOG.append({'piece': g['piece'], 'line': g['line'], 'keep': g.get('keep'), 'verts': int(len(gv)),
                           'off_mm_p50_max': [round(float(np.median(np.linalg.norm(go, axis=1))), 2),
                                              round(float(np.linalg.norm(go, axis=1).max()), 2)],
                           'flipped': bool((np.median(towards) > 0) != inward)})
            ONE_UNDER = ((np.r_[ONE_UNDER[0], gv] if ONE_UNDER is not None else gv),
                         (np.r_[ONE_UNDER[1], gt] if ONE_UNDER is not None else gt),
                         (np.r_[ONE_UNDER[2], gw] if ONE_UNDER is not None else gw))
    # a vertex left with no cloth of its own: every triangle around it has a bound vertex (those lose their stiffness
    # and hinges below, or leave the solver) and it is in no stitch. It kept its mass with no elastic element and fell
    # (a placket's fold-line end, its edges 370-794 mm; their contact search made each frame 2x slower, Fable
    # 2026-10-28). Bound like the others, on the nearest triangle of its piece in the flat pattern (where it starts
    # can be wherever it fell to)
    _bv = np.zeros(N_FULL, bool); _bv[np.asarray(bv_order, np.int64)] = True
    _ft = ~_bv[F_FULL].any(1)
    _own = np.zeros(N_FULL, bool); _own[F_FULL[_ft].ravel()] = True
    _orph = np.zeros(N_FULL, bool); _orph[F_FULL.ravel()] = True
    _orph[d['E_st'].ravel()] = False
    # held otherwise: a tack / button pair (temp_stitches) or an attach point keeps it (design review 2026-10-28)
    if cfg.get('temp_stitches'):
        _orph[np.load(cfg['temp_stitches']).ravel()] = False
    if cfg.get('attach'):
        _orph[np.load(cfg['attach']['file'])['verts']] = False
    ORPHANS = np.nonzero(_orph & ~_bv & ~_own)[0]
    if len(ORPHANS):
        _flo = np.zeros((N_FULL, 3)); _flo[:, :2] = np.asarray(d['flat'], np.float64)
        for v in ORPHANS:
            T_ = F_FULL[(FP_FULL == v_piece[v]) & _ft]
            if not len(T_):
                continue
            w_, d2_ = _closest_on_tris(_flo[v], _flo[T_[:, 0]], _flo[T_[:, 1]], _flo[T_[:, 2]])
            k_ = int(np.argmin(d2_))
            bt.append(T_[k_]); bw.append(w_[k_]); bd.append([0.0, 0.0, 0.0]); bv_order.append(int(v))
    BIND_V = np.asarray(bv_order, np.int64)          # in the order of the rows below (no longer sorted)
    # a row skipped above is out of OL / OL_V too (it stayed in the mesh as one_layer kept it, filters and all)
    OL = [g for g in OL if not any(x_.get('piece') == g['piece'] and x_.get('line') == g['line'] for x_ in OL_SKIPPED)]
    OL_V = (np.unique(np.concatenate([np.asarray(g['verts'], np.int64) for g in OL])) if OL
            else np.zeros(0, np.int64))
    if len(BIND_V):
        bd = np.asarray(bd, np.float64)
        BIND = (np.asarray(bt, np.int64), np.asarray(bw, np.float64), bd / 1000.0)
        BIND_LOG = {'verts': int(len(BIND_V)),
                    'dist_mm_p50_max': [round(float(np.median(np.linalg.norm(bd, axis=1))), 2),
                                        round(float(np.linalg.norm(bd, axis=1).max()), 2)]}
        if OL_LOG:
            BIND_LOG['one_layer'] = OL_LOG
        if len(ORPHANS):
            BIND_LOG['orphans'] = [int(v) for v in ORPHANS]


# edges this stage starts with stretched past 3x (and 20 mm over) their pattern length (the bound vertices left out: they are put
# back on their triangles): a vertex that fell or was dragged off its cloth. Warned at the start (start_check
# problems, Fable 2026-10-28: a placket vertex's 3 edges at 370-794 mm went unseen over a dozen runs)
_se = np.unique(np.sort(np.r_[F_FULL[:, [0, 1]], F_FULL[:, [1, 2]], F_FULL[:, [2, 0]]], 1), axis=0)
_se = _se[~np.isin(_se, BIND_V).any(1)]
_sfl = np.asarray(d['flat'], np.float64)
_sl3 = np.linalg.norm(X_full[_se[:, 0]].astype(np.float64) - X_full[_se[:, 1]], axis=1)
_slf = np.linalg.norm(_sfl[_se[:, 0]] - _sfl[_se[:, 1]], axis=1)
_sbad = (_sl3 > 3.0 * np.maximum(_slf, 1e-6)) & (_sl3 - _slf > 20.0)   # and 20 mm longer: a placed band's short
# edges reach 3x by a few mm (a jacket's PARTS2: 4 edges, 17 mm)
STRETCHED = {}
for _p, _l in zip(v_piece[_se[_sbad, 0]], _sl3[_sbad]):
    _n = str(pat_names[_p]) if pat_names is not None and _p < len(pat_names) else str(_p)
    _c, _m = STRETCHED.get(_n, (0, 0.0))
    STRETCHED[_n] = (_c + 1, max(_m, float(_l)))


def _bound_at(T_, W_, D_, P):
    """the bound vertices' positions on positions P: barycentric on each triangle + the offset in its frame"""
    A_, B_, C_ = P[T_[:, 0]].astype(np.float64), P[T_[:, 1]].astype(np.float64), P[T_[:, 2]].astype(np.float64)
    e1 = B_ - A_; e1 /= np.linalg.norm(e1, axis=1, keepdims=True) + 1e-18
    n_ = np.cross(B_ - A_, C_ - A_); n_ /= np.linalg.norm(n_, axis=1, keepdims=True) + 1e-18
    e2 = np.cross(n_, e1)
    return (W_[:, :1] * A_ + W_[:, 1:2] * B_ + W_[:, 2:] * C_
            + D_[:, :1] * e1 + D_[:, 1:2] * e2 + D_[:, 2:] * n_)


def _rebuild_bound(Xf):
    """Xf (N_FULL, 3) metres: the bound vertices put back on their triangles"""
    if BIND is None:
        return Xf
    Xf[BIND_V] = _bound_at(*BIND, Xf).astype(Xf.dtype)
    return Xf


# the flap stays in the simulation as massless (kinematic) cloth, put back on its binding every frame, so it still
# collides (Fable r14; the flap taken out altogether came through the side panel, user 2026-09-30). Its stitches to
# other pieces stay on it; a stitch within the same piece (the fold's foot sewn to the hem) is dropped: the binding
# already holds it
# cfg 'bind_mode': 'kinematic' (above) or 'removed' (default): the flap leaves the solver altogether and is only
# rebuilt in the frames written; its stitches to other pieces go to the bound triangle's heaviest corner, and those
# retargeted stitches get no contact exemption (cfg 'bind_filter' false: the exemption let the side panel's edge
# through the back between the flap and its piece, Fable r14 §1)
# 'hybrid' (the user, 2026-10-01: the folded flap is one cloth with its piece, stuck on like a Surface Deform):
# 'removed', except the flap vertices sewn to other pieces (the back vent's top, sewn to the side panel), which stay
# as massless vertices put on their binding (3 mm in) every frame, so the side panel is sewn where the flap is,
# not onto the piece's surface (retargeted there, the side panel's extension ended up on top of the back: 36/37)
BIND_MODE = cfg.get('bind_mode', 'removed')
_bound_default = bool(len(OL_V)) and cfg.get('one_layer_collide', True) and BIND_MODE in ('removed', 'hybrid')
BOUND_COUPLING = bool(cfg.get('bound_coupling', _bound_default)) and BIND is not None and bool(len(OL_V))
if BOUND_COUPLING and (not cfg.get('one_layer_collide', True) or BIND_MODE not in ('removed', 'hybrid')):
    raise ValueError('AC9 bound coupling requires a colliding one-layer follower in removed or hybrid mode')
# one_layer's side taken out stays as massless cloth put on its binding every frame, so it still collides (the
# other placket buttoned under it came through it: 98 crossings, Fable 2026-10-24), while its stitches go to the
# side kept as 'removed' does. cfg 'one_layer_collide' false: taken out altogether
OL_KIN = (OL_V if (len(OL_V) and cfg.get('one_layer_collide', True) and BIND_MODE != 'kinematic')
          else np.zeros(0, np.int64))
RT_PAIRS = set()
ANCH = np.zeros(0, np.int64)
if BIND is not None:
    _E = d['E_st']
    _same = (v_piece[_E[:, 0]] == v_piece[_E[:, 1]]) & np.isin(_E, BIND_V).any(1)
    _E = _E[~_same].copy()
    BIND_LOG['stitches_dropped_same_piece'] = int(_same.sum())
    if BIND_MODE == 'hybrid':
        ANCH = np.intersect1d(BIND_V, _E.ravel())
        BIND_LOG['anchors'] = int(len(ANCH))
    if BIND_MODE in ('removed', 'hybrid'):
        _bmap = {int(v): int(BIND[0][k][int(np.argmax(BIND[1][k]))]) for k, v in enumerate(BIND_V)
                 if int(v) not in set(ANCH.tolist()) and not (BOUND_COUPLING and int(v) in set(OL_V.tolist()))}
        for r_ in range(len(_E)):
            hit_ = False
            was_ = frozenset((int(_E[r_, 0]), int(_E[r_, 1])))
            for c_ in (0, 1):
                if int(_E[r_, c_]) in _bmap:
                    _E[r_, c_] = _bmap[int(_E[r_, c_])]; hit_ = True
            if hit_:
                RT_PAIRS.add((int(_E[r_, 0]), int(_E[r_, 1])))
                if was_ in SEAM_OF:             # the retargeted stitch keeps its seam (logs, Live's seam switches)
                    SEAM_OF[frozenset((int(_E[r_, 0]), int(_E[r_, 1])))] = SEAM_OF[was_]
        _E = _E[_E[:, 0] != _E[:, 1]]
        BIND_LOG['stitches_retargeted'] = len(RT_PAIRS)
    d['E_st'] = _E
# ghost_pieces (P1 #9, 2026-10-13, the off_pattern state): pieces that stay put (massless, listed in frozen_pieces
# too) and that nothing touches -- the moving cloth goes through them -- while their seams still pull. Newton's
# contact only looks within a world (+ the global one), so the moving cloth is world 0 and the ghosts world 1; the
# body is global and the springs cross worlds (ghost_strip_test.py: a strip fell through a ghost board 121/121,
# hung from it by a seam, stopped on a frozen one 0/121). Needs the ghosts last in the vertex order: a subset
GHOST_P = [pat_names.index(n) for n in (cfg.get('ghost_pieces') or [])]
SUBSET = (bool(cfg.get('active_pieces')) or (len(BIND_V) > 0 and BIND_MODE in ('removed', 'hybrid'))
          or bool(GHOST_P))
if SUBSET:
    keep_p = ([pat_names.index(n) for n in cfg['active_pieces']] if cfg.get('active_pieces')
              else list(range(len(offs_full) - 1)))
    keep_v = np.isin(v_piece, keep_p)
    if BIND_MODE in ('removed', 'hybrid'):
        keep_v[BIND_V] = False
        keep_v[ANCH] = True
        keep_v[OL_KIN] = True
    sel = np.nonzero(keep_v)[0]
    if GHOST_P:
        _gm = np.isin(v_piece[sel], GHOST_P)
        sel = np.concatenate([sel[~_gm], sel[_gm]])
    remap = -np.ones(N_FULL, np.int64); remap[sel] = np.arange(len(sel))
    fmask = np.isin(d['fp'], keep_p) & (remap[d['F']] >= 0).all(1)
    emask = (remap[d['E_st'][:, 0]] >= 0) & (remap[d['E_st'][:, 1]] >= 0)
    d['F'] = remap[d['F'][fmask]]; d['fp'] = d['fp'][fmask]
    if GHOST_P:
        # the ghosts' triangles last too (their vertices are): the two meshes of the two worlds below
        _o = np.argsort(np.isin(d['fp'], GHOST_P), kind='stable')
        d['F'] = d['F'][_o]; d['fp'] = d['fp'][_o]
    d['E_st'] = remap[d['E_st'][emask]]
    for k in ('Xinit', 'flat', 'Xclo'):
        d[k] = d[k][sel]
interior = interior_full[sel]
N_MOVE = int((~np.isin(v_piece[sel], GHOST_P)).sum()) if GHOST_P else len(sel)
RT_LOCAL = {(int(remap[a_]), int(remap[b_])) for a_, b_ in RT_PAIRS} if (RT_PAIRS and SUBSET) else set(RT_PAIRS)
X0 = d['Xinit'] / 1000.0                     # m
F = d['F'].astype(np.int32)
E = d['E_st'].astype(np.int32)
E_SEAM = np.array([SEAM_OF.get(frozenset((int(sel[a_]), int(sel[b_]))), '') for a_, b_ in E], dtype=object)
log = {'cfg': cfg, 'frame_s': []}
if _NATIVE_RUNTIME is not None:
    log['native_runtime_io'] = {'directory': LIVE_OUT, 'job_directory': out}
# the switched-off seams' stitches (scene.idx E_st_off, pattern_build 'stitch_off'): in a Live / Local stage they are
# springs right after the pattern stitches, held at no stiffness, so the add-on can sew a seam on while the stage
# runs (live_params below; user 2026-10-23). Never in E: nothing else (guards, hinges, the stitch cap) sees them.
E_OFF, E_OFF_SEAM = np.zeros((0, 2), np.int32), np.zeros(0, object)
if cfg.get('live') and 'E_st_off' in d and len(d['E_st_off']) and BIND is None and not cfg.get('weld'):
    _eo = np.asarray(d['E_st_off'], np.int64)
    _so = np.asarray(d['E_seam_off']).astype(object)
    if SUBSET:
        _m = (remap[_eo[:, 0]] >= 0) & (remap[_eo[:, 1]] >= 0)
        _eo, _so = remap[_eo[_m]], _so[_m]
    E_OFF, E_OFF_SEAM = _eo.astype(np.int32), _so
    log['seams_off'] = {'stitches': int(len(E_OFF)), 'seams': sorted(set(str(x) for x in E_OFF_SEAM))}
# cfg 'weld' (test option, default off): {"max_mm": 1.0} -- the stitched vertex pairs closer than max_mm at the
# stage's start become one vertex (union-find over the stitches, so a seam junction becomes one vertex), their
# stitches go and the seam is an ordinary inner edge of one mesh. A two-vertex seam needs its vertices left out
# of each other's contact (or the contact holds it open); 94 % of the cloth-through-cloth pairs born in a pants'
# stages were such exempt pairs (1019 of 1086, 2026-10-01). Last stage only: the seams must already be closed.
# Each triangle keeps its own flat rest shape (from its vertices before welding); output in the original numbering.
WELD = None
if cfg.get('weld'):
    for k_ in ('mirror_map', 'bind_verts', 'layer_order', 'stack_under', 'fold_from_drape', 'ease', 'shrink', 'elastic',
               'temp_stitches', 'anchor_ke', 'sym_color', 'strengthen_pieces', 'bend_scale_pieces', 'fold_free',
               'depenetrate', 'clear_rigid', 'static_pieces', 'carry_to_seam', 'ghost_pieces',
               'attach', 'one_layer', 'layers'):
        if cfg.get(k_):
            raise SystemExit(f'weld: not made to work with {k_}')
    _wmax = float(cfg['weld'].get('max_mm', 1.0)) if isinstance(cfg['weld'], dict) else 1.0
    _par = np.arange(len(X0))

    def _root(i_):
        while _par[i_] != i_:
            _par[i_] = _par[_par[i_]]; i_ = _par[i_]
        return i_
    _g = np.linalg.norm(X0[E[:, 0]] - X0[E[:, 1]], axis=1) * 1000.0
    for (a_, b_), g_ in zip(E, _g):
        if g_ < _wmax:
            ra_, rb_ = _root(int(a_)), _root(int(b_))
            if ra_ != rb_:
                _par[max(ra_, rb_)] = min(ra_, rb_)
    _roots = np.array([_root(i_) for i_ in range(len(X0))])
    _u, W_MAP = np.unique(_roots, return_inverse=True)     # local vertex -> welded vertex
    _cnt = np.bincount(W_MAP)
    _acc = np.zeros((len(_u), 3)); np.add.at(_acc, W_MAP, d['Xinit'] / 1000.0); X0 = _acc / _cnt[:, None]
    F_REST = F.copy()                                      # rest shapes from the vertices before welding
    _Fw = W_MAP[F]
    _keep = (_Fw[:, 0] != _Fw[:, 1]) & (_Fw[:, 1] != _Fw[:, 2]) & (_Fw[:, 0] != _Fw[:, 2])
    F_REST = F_REST[_keep]; F = _Fw[_keep].astype(np.int32); d['fp'] = d['fp'][_keep]
    _ke = W_MAP[E[:, 0]] != W_MAP[E[:, 1]]
    E = W_MAP[E[_ke]].astype(np.int32); E_SEAM = E_SEAM[_ke]
    # one original vertex per welded one, for the per-vertex lookups below (piece, flat point, reference point)
    _rep = np.zeros(len(_u), np.int64); _rep[W_MAP[::-1]] = np.arange(len(W_MAP))[::-1]
    SEL_OUT = sel.copy()                                    # where the output goes in the full garment
    FLAT_REST = d['flat'].copy()
    sel = sel[_rep]
    for k_ in ('Xinit', 'flat', 'Xclo'):
        d[k_] = d[k_][_rep]
    d['Xinit'] = X0 * 1000.0
    interior = interior[_rep]
    WELD = {'welded_verts': int(len(W_MAP) - len(_u)), 'groups_gt2': int((_cnt > 2).sum()),
            'tris_dropped': int((~_keep).sum()), 'stitches_left': int(len(E)), 'max_mm': _wmax}
    log['weld'] = WELD
    X0 = X0.astype(np.float32)
if STACK_LOG:
    log['stack_under'] = STACK_LOG
if LAY_LOG is not None:
    log['lay'] = LAY_LOG
if BIND is not None:
    log['bind_verts'] = BIND_LOG
if OL_SKIPPED:
    log['one_layer_skipped'] = OL_SKIPPED

# body, cropped as in add_body_b52.py
A = np.load(av_f)
B, T = A['body_co'], A['body_tri']
c = B[T].mean(1) * 1000.0
z0, z1, xm = cfg.get('crop', [200.0, 950.0, 200.0])
keep = (c[:, 2] > z0) & (c[:, 2] < z1) & (np.abs(c[:, 0]) < xm)
if cfg.get('drop_arms'):
    # stage 1 of a jacket: sew the torso with the arms left out (prep/arm_mask.py)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from arm_mask import arm_tris
    keep &= ~arm_tris(c, cfg['drop_arms'])
T = T[keep]
used, inv = np.unique(T, return_inverse=True)
Bv, Bt = B[used].astype(np.float32), inv.reshape(-1).astype(np.int32)
if len(STATIC_TRI):
    su, sinv = np.unique(STATIC_TRI, return_inverse=True)
    Bt = np.r_[Bt, (sinv.reshape(-1) + len(Bv)).astype(np.int32)]
    Bv = np.r_[Bv, (X_full[su] / 1000.0).astype(np.float32)]
    log['static_pieces'] = {'pieces': len(cfg['static_pieces']), 'tris': int(len(STATIC_TRI))}

_trace('gpu_init_begin')
wp.init()
_trace('gpu_init_end')
# depenetrate runs before clear_new (2026-10-07): a collar folded at placement started 98 vertices inside the body
# under the settled bodice; out of the cloth first and then out of the body put it back under the cloth
# TEST ONLY: cfg 'depenetrate' = {"gap_mm": 3, "sigma_mm": 30, "rounds": 10, "max_depth_mm": 25}: before the stage,
# garment vertices inside this stage's body (up to max_depth) are pushed out to gap along the body normal, and each piece follows with
# a Gaussian falloff over the flat pattern (like proportional editing) -- the placement had the fronts 14 vertices /
# 21 mm inside the arms-out body at the shoulder (user 2026-09-30: the pull from there set the dart crooked)
if cfg.get('depenetrate'):
    dp_ = cfg['depenetrate'] if isinstance(cfg['depenetrate'], dict) else {}
    gap_ = float(dp_.get('gap_mm', 3.0)) / 1000.0
    sig_ = float(dp_.get('sigma_mm', 30.0))
    maxd_ = float(dp_.get('max_depth_mm', 25.0)) / 1000.0     # deeper = on the far side of a hole (arms out)
    bm_ = wp.Mesh(points=wp.array(Bv, dtype=wp.vec3), indices=wp.array(Bt.astype(np.int32), dtype=wp.int32))

    @wp.kernel
    def _dp_query(m: wp.uint64, xs: wp.array(dtype=wp.vec3), gap: float, maxd: float,
                  out_d: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        p = xs[i]
        q = wp.mesh_query_point_sign_normal(m, p, maxd)
        out_d[i] = wp.vec3(0.0, 0.0, 0.0)
        if q.result:
            cp = wp.mesh_eval_position(m, q.face, q.u, q.v)
            nrm = wp.mesh_eval_face_normal(m, q.face)
            s = wp.dot(p - cp, nrm)
            if s < 0.0 and s > -maxd:
                out_d[i] = nrm * (gap - s)

    vp_ = v_piece[sel]
    fl_ = d['flat'].astype(np.float64)
    X0 = X0.astype(np.float64)
    hist_ = []
    for r_ in range(int(dp_.get('rounds', 10))):
        xs_ = wp.array(X0.astype(np.float32), dtype=wp.vec3)
        od_ = wp.zeros(len(X0), dtype=wp.vec3)
        wp.launch(_dp_query, dim=len(X0), inputs=[bm_.id, xs_, gap_, maxd_, od_])
        D_ = od_.numpy().astype(np.float64)
        if dp_.get('symmetric') and cfg.get('pattern'):
            # twins (vertex k <-> k, x mirrored): both take the larger of the two pushes, the start stays symmetric
            ts_ = np.load(cfg['pattern'])['twin_src']
            loc_ = -np.ones(N_FULL, np.int64); loc_[sel] = np.arange(len(sel))
            for a_p, b_p in enumerate(ts_):
                if b_p < 0:
                    continue
                ia = loc_[offs_full[a_p]:offs_full[a_p + 1]]; ib = loc_[offs_full[b_p]:offs_full[b_p + 1]]
                if (ia < 0).any() or (ib < 0).any() or len(ia) != len(ib):
                    continue
                Da, Db = D_[ia], D_[ib] * [-1.0, 1.0, 1.0]
                big = np.where((np.linalg.norm(Da, axis=1) >= np.linalg.norm(Db, axis=1))[:, None], Da, Db)
                D_[ia], D_[ib] = big, big * [-1.0, 1.0, 1.0]
        h_ = np.nonzero(np.linalg.norm(D_, axis=1) > 1e-6)[0]
        hist_.append([int(len(h_)), round(float(np.linalg.norm(D_, axis=1).max()) * 1000, 2)])
        if not len(h_):
            break
        move = np.zeros_like(X0)
        for pc_ in np.unique(vp_[h_]):
            vs = np.nonzero(vp_ == pc_)[0]
            hs = h_[vp_[h_] == pc_]
            w = np.exp(-((fl_[vs][:, None, :] - fl_[hs][None, :, :]) ** 2).sum(-1) / (2 * sig_ * sig_))
            move[vs] = (w @ D_[hs]) / np.maximum(1.0, w.sum(1))[:, None]
        X0 = X0 + move
    log['depenetrate'] = {'rounds': hist_, 'moved_max_mm': round(float(np.linalg.norm(X0 - d['Xinit'] / 1000.0,
                                                                                         axis=1).max()) * 1000, 1)}
    X0 = X0.astype(np.float32)
# cfg 'clear_new' (test option, default off): {"gap_mm": 2, "reach_mm": 25, "rounds": 12, "smooth": 8} -- pieces that
# join in this stage (active, not in init_from_pieces) start outside the cloth already there: a new vertex on the
# body's side of an existing triangle it lies over (within reach), or nearer than gap to it, moves out along that
# triangle's outward normal (away from the body), the move spread over its piece, until none is left. Later parts
# go on top, a tailor's order (user 2026-10-03: the collar joined with its notch end under the lapel and was sewn
# through it). Layers placed in the right order keep it; pushed into it during the run they broke (RESULT_G3 26-31)
CN = None
if cfg.get('clear_new') and (cfg.get('init_from_pieces') is not None or cfg['clear_new'].get('outer')):
    from spatial import cKDTree as _KDc
    cn_ = cfg['clear_new'] if isinstance(cfg['clear_new'], dict) else {}
    gap_c = float(cn_.get('gap_mm', 2.0)) / 1000.0
    reach_c = float(cn_.get('reach_mm', 25.0)) / 1000.0
    # by default the pieces joining this stage go outside the ones it starts from; "outer" / "inner" (piece names)
    # name the two layers explicitly, for the stages after the one they joined in
    vpl = v_piece[sel]
    if cn_.get('outer'):
        old_p = [pat_names.index(n) for n in cn_.get('inner', [])]
        newv = np.isin(vpl, [pat_names.index(n) for n in cn_['outer']])
    else:
        old_p = [pat_names.index(n) for n in cfg['init_from_pieces']]
        newv = ~np.isin(vpl, old_p)
    Fo = F[np.isin(d['fp'], old_p)].astype(np.int64)
    log_cn = {'new_verts': int(newv.sum()), 'rounds': []}
    if newv.any() and len(Fo):
        Xc = np.asarray(X0, np.float64).copy()
        nbc = [[] for _ in range(len(Xc))]
        for i_, j_, k_ in F:
            nbc[i_] += (j_, k_); nbc[j_] += (i_, k_); nbc[k_] += (i_, j_)
        bk_ = _KDc(np.asarray(Bv, np.float64))
        # a new vertex sewn to the old cloth, and its rings, stays where the seam pulls it: pushed out by gap it could
        # never close (49 of 288 stitches stayed open, 2026-10-03)
        _sv = set()
        for a_, b_ in E:
            if newv[a_] != newv[b_]:
                _sv.update((int(a_), int(b_)))
        for _ in range(int(cn_.get('seam_rings', 2))):
            _sv |= set(w for v in list(_sv) for w in nbc[v])
        _near_seam = np.zeros(len(Xc), bool); _near_seam[list(_sv)] = True
        if not cn_.get('seam_lenient'):
            Fo = Fo[~_near_seam[Fo].any(1)]
            nv_idx = np.nonzero(newv & ~_near_seam)[0]
        else:
            # seam_lenient (user 2026-10-03: the collar's end went under the lapel exactly where it is sewn, and leaving
            # the seams out left it there): every new vertex and old triangle is looked at; by a seam the vertex only
            # has to be on the outer side (gap_seam_mm), elsewhere gap_mm out
            nv_idx = np.nonzero(newv)[0]
        GAPV = np.where(_near_seam, float(cn_.get('gap_seam_mm', 0.5)) / 1000.0, gap_c)
        if cn_.get('free_mm'):
            # only the free end of a joining piece (user 2026-10-03: keep the collar's free edge outside, not the whole
            # island; its seam side goes where the stitches take it): the vertices at least free_mm on the pattern
            # from every vertex of theirs sewn to the old cloth
            _sn = np.array(sorted(v for v in _sv if newv[v]), np.int64)
            _sn = _sn[[any(newv[a_] != newv[b_] for a_, b_ in E if v in (a_, b_)) for v in _sn]] if len(_sn) else _sn
            if len(_sn):
                _fd, _ = _KDc(np.asarray(d['flat'], np.float64)[_sn]).query(np.asarray(d['flat'], np.float64)[nv_idx])
                nv_idx = nv_idx[_fd >= float(cn_['free_mm'])]
        log_cn['kept_verts'] = int(len(nv_idx))

        def _cn_need(Xc):
            """per vertex: the move that puts a new vertex gap outside the old cloth it lies over (0 if none)"""
            A_, B_, C_ = Xc[Fo[:, 0]], Xc[Fo[:, 1]], Xc[Fo[:, 2]]
            cen = (A_ + B_ + C_) / 3.0
            n_ = np.cross(B_ - A_, C_ - A_); n_ /= np.linalg.norm(n_, axis=1, keepdims=True) + 1e-18
            _, bi_ = bk_.query(cen)
            n_ *= np.where(((cen - bk_.data[bi_]) * n_).sum(1) >= 0, 1.0, -1.0)[:, None]
            dd_, cand = _KDc(cen).query(Xc[nv_idx], k=8, distance_upper_bound=reach_c)
            need = np.zeros((len(Xc), 3))
            for col in range(cand.shape[1]):
                ok_ = np.isfinite(dd_[:, col])
                if not ok_.any():
                    continue
                v_ = nv_idx[ok_]; t_ = cand[ok_, col]
                p_ = Xc[v_]; h_ = ((p_ - A_[t_]) * n_[t_]).sum(1)
                q_ = p_ - h_[:, None] * n_[t_]
                e0, e1, e2 = B_[t_] - A_[t_], C_[t_] - A_[t_], q_ - A_[t_]
                d00, d01, d11 = (e0 * e0).sum(1), (e0 * e1).sum(1), (e1 * e1).sum(1)
                d20, d21 = (e2 * e0).sum(1), (e2 * e1).sum(1)
                den = d00 * d11 - d01 * d01 + 1e-30
                b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den
                gv_ = GAPV[v_]
                over = (b1 >= -0.1) & (b2 >= -0.1) & (b1 + b2 <= 1.1) & (h_ < gv_) & (h_ > -reach_c)
                if not over.any():
                    continue
                mv = (gv_[over] - h_[over])[:, None] * n_[t_[over]]
                cur = np.linalg.norm(need[v_[over]], axis=1); new_ = np.linalg.norm(mv, axis=1)
                take = new_ > cur
                need[v_[over][take]] = mv[take]
            return need
        CN = {'need': _cn_need, 'newv': newv, 'nv_idx': nv_idx, 'nb': nbc,
              'every': int(cn_.get('every', 0)), 'cap': float(cn_.get('cap_mm', 1.0)) / 1000.0, 'moves': []}
        for r_ in range(int(cn_.get('rounds', 12))):
            A_, B_, C_ = Xc[Fo[:, 0]], Xc[Fo[:, 1]], Xc[Fo[:, 2]]
            cen = (A_ + B_ + C_) / 3.0
            n_ = np.cross(B_ - A_, C_ - A_); n_ /= np.linalg.norm(n_, axis=1, keepdims=True) + 1e-18
            _, bi_ = bk_.query(cen)
            n_ *= np.where(((cen - bk_.data[bi_]) * n_).sum(1) >= 0, 1.0, -1.0)[:, None]    # outward
            dd_, cand = _KDc(cen).query(Xc[nv_idx], k=8, distance_upper_bound=reach_c)
            need = np.zeros((len(Xc), 3))
            for col in range(cand.shape[1]):
                ok_ = np.isfinite(dd_[:, col])
                if not ok_.any():
                    continue
                v_ = nv_idx[ok_]; t_ = cand[ok_, col]
                p_ = Xc[v_]; h_ = ((p_ - A_[t_]) * n_[t_]).sum(1)
                q_ = p_ - h_[:, None] * n_[t_]
                e0, e1, e2 = B_[t_] - A_[t_], C_[t_] - A_[t_], q_ - A_[t_]
                d00, d01, d11 = (e0 * e0).sum(1), (e0 * e1).sum(1), (e1 * e1).sum(1)
                d20, d21 = (e2 * e0).sum(1), (e2 * e1).sum(1)
                den = d00 * d11 - d01 * d01 + 1e-30
                b1 = (d11 * d20 - d01 * d21) / den; b2 = (d00 * d21 - d01 * d20) / den
                gv_ = GAPV[v_]
                over = (b1 >= -0.1) & (b2 >= -0.1) & (b1 + b2 <= 1.1) & (h_ < gv_) & (h_ > -reach_c)
                if not over.any():
                    continue
                mv = (gv_[over] - h_[over])[:, None] * n_[t_[over]]
                cur = np.linalg.norm(need[v_[over]], axis=1); new_ = np.linalg.norm(mv, axis=1)
                take = new_ > cur
                need[v_[over][take]] = mv[take]
            bad = np.nonzero(np.linalg.norm(need, axis=1) > 0)[0]
            log_cn['rounds'].append(int(len(bad)))
            if not len(bad):
                break
            D_ = need.copy()
            for _ in range(int(cn_.get('smooth', 8))):         # the piece around comes along
                S_ = D_.copy()
                for v in nv_idx:
                    nb_ = [w for w in nbc[v] if newv[w]]
                    if nb_:
                        S_[v] = 0.5 * D_[v] + 0.5 * D_[nb_].mean(0)
                D_ = np.where(np.linalg.norm(need, axis=1, keepdims=True) > np.linalg.norm(S_, axis=1, keepdims=True), need, S_)
            D_[~newv] = 0.0
            Xc += D_
        log_cn['moved_max_mm'] = round(float(np.linalg.norm(Xc - X0, axis=1).max()) * 1000, 1)
        X0 = Xc.astype(np.float32)
    log['clear_new'] = log_cn
# TEST: cfg 'carry_to_seam' = {"pieces": [...], "to": [...]}: before the stage, each named piece moves whole (no change of
# shape) by the rotation and shift that take its stitched vertices nearest their partners on the "to" pieces as they
# are now. A shirt's pleated back started its pleat stage where it was placed, 99-119 mm below its yoke, already
# moved onto the shoulders; pulled up that far while sewn, its folded pleats came undone (2026-10-09)
if cfg.get('carry_to_seam'):
    ct_ = cfg['carry_to_seam']
    _cvp = np.full(len(X0), -1, np.int64)
    _cvp[np.asarray(F).ravel()] = np.repeat(np.asarray(d['fp'], np.int64), 3)
    _nm = [str(x) for x in pat_names] if pat_names is not None else []
    _to = {_nm.index(n) for n in ct_.get('to', []) if n in _nm}
    log['carry_to_seam'] = {}
    Xc_ = np.asarray(X0, np.float64).copy()
    for n in ct_.get('pieces', []):
        if n not in _nm:
            continue
        k_ = _nm.index(n)
        Ee = np.asarray(E, np.int64)
        m1 = (_cvp[Ee[:, 0]] == k_) & np.isin(_cvp[Ee[:, 1]], list(_to))
        m2 = (_cvp[Ee[:, 1]] == k_) & np.isin(_cvp[Ee[:, 0]], list(_to))
        a_ = np.r_[Ee[m1, 0], Ee[m2, 1]]
        b_ = np.r_[Ee[m1, 1], Ee[m2, 0]]
        if len(a_) < 3:
            continue
        P_, Q_ = Xc_[a_], Xc_[b_]
        pc_, qc_ = P_.mean(0), Q_.mean(0)
        U_, _, Wt_ = np.linalg.svd((P_ - pc_).T @ (Q_ - qc_))
        Rk = Wt_.T @ np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt_.T @ U_.T))]) @ U_.T
        vs_ = np.nonzero(_cvp == k_)[0]
        before = float(np.median(np.linalg.norm(P_ - Q_, axis=1))) * 1000
        Xc_[vs_] = (Xc_[vs_] - pc_) @ Rk.T + qc_
        after = float(np.median(np.linalg.norm(Xc_[a_] - Q_, axis=1))) * 1000
        log['carry_to_seam'][n] = {'stitches': int(len(a_)), 'gap_p50_mm': [round(before, 1), round(after, 1)]}
    X0 = Xc_.astype(np.float32)
# TEST ONLY: cfg 'clear_rigid' = {"dir": "down" | "out", "gap_mm": 3, "step_mm": 1, "max_mm": 80, "max_depth_mm": 25}:
# before the stage, each piece with vertices inside this stage's body moves whole (no change of shape) by the
# smallest step that clears it -- down (-z) or out (away from the body's vertical axis through the piece's centre);
# twins (vertex k <-> k, x mirrored) take the larger of their two amounts, so a mirrored start stays mirrored
# (user 2026-09-30: move the piece, a proportional push cannot be kept the same on both sides)
if cfg.get('clear_rigid'):
    cr_ = cfg['clear_rigid']
    gap_c = float(cr_.get('gap_mm', 3.0)) / 1000.0
    maxd_c = float(cr_.get('max_depth_mm', 25.0)) / 1000.0
    bm_c = wp.Mesh(points=wp.array(Bv, dtype=wp.vec3), indices=wp.array(Bt.astype(np.int32), dtype=wp.int32))

    @wp.kernel
    def _cr_inside(m: wp.uint64, xs: wp.array(dtype=wp.vec3), gap: float, maxd: float, out_s: wp.array(dtype=int)):
        i = wp.tid()
        p = xs[i]
        out_s[i] = 0
        q = wp.mesh_query_point_sign_normal(m, p, maxd)
        if q.result:
            cp = wp.mesh_eval_position(m, q.face, q.u, q.v)
            nrm = wp.mesh_eval_face_normal(m, q.face)
            s = wp.dot(p - cp, nrm)
            if s < gap and s > -maxd:
                out_s[i] = 1

    def _n_inside(P):
        o = wp.zeros(len(P), dtype=int)
        wp.launch(_cr_inside, dim=len(P), inputs=[bm_c.id, wp.array(P.astype(np.float32), dtype=wp.vec3), gap_c,
                                                  maxd_c, o])
        return int(o.numpy().sum())

    vp_c = v_piece[sel]
    X0 = X0.astype(np.float64)

    def _dir(vs):
        if cr_.get('dir', 'down') == 'down':
            return np.array([0.0, 0.0, -1.0])
        c_ = X0[vs].mean(0); r_ = np.array([c_[0], c_[1] - float(cr_.get('axis_y_mm', 0.0)) / 1000.0, 0.0])
        return r_ / max(np.linalg.norm(r_), 1e-9)
    need, rep_c = {}, {}
    for pc_ in np.unique(vp_c):
        vs = np.nonzero(vp_c == pc_)[0]
        n0 = _n_inside(X0[vs])
        if not n0:
            continue
        dv = _dir(vs); a_ = 0.0
        while a_ < float(cr_.get('max_mm', 80.0)):
            a_ += float(cr_.get('step_mm', 1.0))
            if not _n_inside(X0[vs] + dv * a_ / 1000.0):
                break
        need[int(pc_)] = a_
        rep_c[pat_names[pc_] if pat_names else str(pc_)] = {'inside_before': n0, 'mm': a_}
    if cfg.get('pattern'):
        ts_ = np.load(cfg['pattern'])['twin_src']
        for a_p, b_p in enumerate(ts_):
            if b_p >= 0 and (a_p in need or b_p in need):
                m_ = max(need.get(a_p, 0.0), need.get(int(b_p), 0.0))
                need[a_p] = need[int(b_p)] = m_
    if cr_.get('together') and need:
        # the whole garment moves by the largest amount (pieces sewn to a moved one are not left behind)
        m_ = max(need.values())
        need = {int(pc_): m_ for pc_ in np.unique(vp_c)}
    for pc_, a_ in need.items():
        vs = np.nonzero(vp_c == pc_)[0]
        X0[vs] = X0[vs] + _dir(vs) * a_ / 1000.0
    log['clear_rigid'] = {'pieces': rep_c, 'moved_mm': {(pat_names[k] if pat_names else str(k)): v for k, v in need.items()},
                          'inside_after': _n_inside(X0)}
    X0 = X0.astype(np.float32)
_tmark('prepared')
_trace('model_begin')
builder = newton.ModelBuilder(gravity=(0.0, 0.0, float(cfg.get('gravity_z', -9.81))))
_cloth_kw = dict(pos=wp.vec3(0.0, 0.0, 0.0), rot=wp.quat_identity(), scale=1.0, vel=wp.vec3(0.0, 0.0, 0.0),
                 density=cfg.get('density', 0.14),
                 tri_ke=cfg['tri_ke'], tri_ka=cfg['tri_ka'], tri_kd=cfg['tri_kd'],
                 edge_ke=cfg['edge_ke'], edge_kd=cfg['edge_kd'],
                 particle_radius=cfg.get('particle_radius', 0.002))
if N_MOVE < len(X0):
    # two meshes in two worlds (indices are each mesh's own); a triangle never mixes the two (one piece each)
    _fg = F >= N_MOVE
    if (_fg.any(1) & ~_fg.all(1)).any():
        raise SystemExit('ghost_pieces: a triangle has both ghost and moving vertices')
    _fg = _fg.all(1)
    if _fg[:int((~_fg).sum())].any():
        raise SystemExit('ghost_pieces: the ghost triangles are not last')
    builder.begin_world(label="moving")
    builder.add_cloth_mesh(vertices=[wp.vec3(*p) for p in X0[:N_MOVE]], indices=F[:int((~_fg).sum())].reshape(-1).tolist(),
                           **_cloth_kw)
    builder.end_world()
    builder.begin_world(label="ghost")
    builder.add_cloth_mesh(vertices=[wp.vec3(*p) for p in X0[N_MOVE:]],
                           indices=(F[int((~_fg).sum()):] - N_MOVE).reshape(-1).tolist(), **_cloth_kw)
    builder.end_world()
    log['ghost'] = {'pieces': list(cfg['ghost_pieces']), 'verts': int(len(X0) - N_MOVE), 'tris': int(_fg.sum())}
else:
    builder.add_cloth_mesh(vertices=[wp.vec3(*p) for p in X0], indices=F.reshape(-1).tolist(), **_cloth_kw)
for a, b in E:
    builder.add_spring(int(a), int(b), cfg['stitch_ke'], cfg['stitch_kd'], 0.0)
for a, b in E_OFF:                  # held at no stiffness after the stiffness scaling below (KE_OFF)
    builder.add_spring(int(a), int(b), cfg['stitch_ke'], cfg['stitch_kd'], 0.0)
# temp_stitches: extra stitches only for this stage, e.g. the fronts buttoned together while the jacket settles,
# released in the next stage (user's suggestion, 2026-09-25). File: (n,2) full-garment vertex indices.
if cfg.get('temp_stitches'):
    TS = np.load(cfg['temp_stitches'])
    if SUBSET:
        TS = TS[(remap[TS[:, 0]] >= 0) & (remap[TS[:, 1]] >= 0)]
        TS = remap[TS]
    for a, b in TS:
        builder.add_spring(int(a), int(b), float(cfg.get('temp_ke', cfg['stitch_ke'])), 0.0, 0.0)
    log['temp_stitches'] = int(len(TS))
# anchor_ke: a weak spring from every garment vertex to a fixed (inactive, massless) copy of its start position.
# For a zero-gravity sewing stage: nothing else holds the pieces, and the fronts drifted up 40-56 mm and tipped to
# either side depending on small differences (the mirrored scene failed on the other side, 2026-09-25).
N_G = len(X0)
if cfg.get('anchor_ke'):
    for v, p_ in enumerate(X0):
        a_ = builder.add_particle(wp.vec3(*p_), wp.vec3(0.0, 0.0, 0.0), 0.0, radius=0.0, flags=0)
        builder.add_spring(v, a_, float(cfg['anchor_ke']), float(cfg.get('anchor_kd', 0.0)), 0.0)
# seam_paths (TEST, 2026-10-14): stitched pairs pulled together round a body part instead of straight through it.
# The user's raglan sleeve: its underarm points, drawn straight to each other, went through the arm and stayed 36 mm
# apart with 135 sleeve vertices inside it. {"file": npz (pairs (n,2) full-garment vertex indices, o (n,3) m, u (n,3)
# the part's axis, sign (n,) +1 = counter-clockwise about u from a, r_min (n,) m), "carrot_mm": 10, "switch_mm": 15,
# "frames": stage frames, "ke": stitch_ke}. Each pair gets two massless targets (as anchor_ke's), each a carrot_mm
# ahead of its end on the arc from a to b about the axis (Fable review 2026-10-14: moving targets, not a fixed
# path -- both ends move); the pair's own stitch (seam or tack) is off until the arc is shorter than switch_mm.
PATH = None


def _path_sides(pl_, o_, u_, side_, grp_, rg_):
    """Which way round each pair goes (+1 = counter-clockwise about u from a) and the least radius of its arc (m), from
    the stage's start: through the side of the body given (a world direction), or -- side (0,0,0), "auto" -- one way
    for the whole seam, the side away from its pieces' cloth (the open side of a wrapped sleeve). The radius: the
    body round the axis at the pair's height (p95 of the body points within the part's radius) + 5 mm."""
    a_, b_ = X0[pl_[:, 0]], X0[pl_[:, 1]]
    da_, db_ = a_ - o_, b_ - o_
    pa_ = da_ - (da_ * u_).sum(1)[:, None] * u_
    pb_ = db_ - (db_ * u_).sum(1)[:, None] * u_
    e1_ = pa_ / np.maximum(np.linalg.norm(pa_, axis=1), 1e-9)[:, None]
    e2_ = np.cross(u_, e1_)
    th_ = np.mod(np.arctan2((pb_ * e2_).sum(1), (pb_ * e1_).sum(1)), 2 * np.pi)
    ts_ = np.mod(np.arctan2((side_ * e2_).sum(1), (side_ * e1_).sum(1)), 2 * np.pi)
    sg_ = np.where(ts_ < th_, 1.0, -1.0)
    vpl_ = v_piece[sel]
    for g_ in np.unique(grp_):
        m_ = (grp_ == g_) & (np.linalg.norm(side_, axis=1) < 1e-9)
        if not m_.any():
            continue
        i0_ = np.nonzero(m_)[0][0]
        P_ = X0[np.isin(vpl_, [vpl_[pl_[i0_, 0]], vpl_[pl_[i0_, 1]]])] - o_[i0_]
        pc_ = (P_ - (P_ @ u_[i0_])[:, None] * u_[i0_]).mean(0)
        sc_ = {}
        for s_ in (1.0, -1.0):
            dth_ = th_[m_] if s_ > 0 else th_[m_] - 2 * np.pi
            mid_ = np.cos(dth_ / 2)[:, None] * e1_[m_] + np.sin(dth_ / 2)[:, None] * e2_[m_]
            sc_[s_] = -float((mid_ @ pc_).sum())
        sg_[m_] = 1.0 if sc_[1.0] >= sc_[-1.0] else -1.0
    rm_ = np.zeros(len(pl_))
    for i_ in range(len(pl_)):
        dB_ = B - o_[i_]
        hB_ = dB_ @ u_[i_]
        rB_ = np.linalg.norm(dB_ - hB_[:, None] * u_[i_], axis=1)
        hm_ = 0.5 * ((da_[i_] + db_[i_]) @ u_[i_])
        nr_ = (np.abs(hB_ - hm_) < 0.015) & (rB_ < rg_[i_])
        rm_[i_] = (np.percentile(rB_[nr_], 95) if nr_.sum() > 10 else 0.6 * rg_[i_]) + 0.005
    return sg_, rm_


if cfg.get('seam_paths'):
    _sp = cfg['seam_paths']
    _pz = np.load(_sp['file'])
    _pr = _pz['pairs'].astype(np.int64)
    _ok = (remap[_pr[:, 0]] >= 0) & (remap[_pr[:, 1]] >= 0) if SUBSET else np.ones(len(_pr), bool)
    _pl = remap[_pr[_ok]] if SUBSET else _pr[_ok]
    _ke = float(_sp.get('ke', cfg['stitch_ke']))
    _tg = []
    for a_, b_ in _pl:
        ta_ = builder.add_particle(wp.vec3(*X0[a_]), wp.vec3(0.0, 0.0, 0.0), 0.0, radius=0.0, flags=0)
        tb_ = builder.add_particle(wp.vec3(*X0[b_]), wp.vec3(0.0, 0.0, 0.0), 0.0, radius=0.0, flags=0)
        builder.add_spring(int(a_), ta_, _ke, 0.0, 0.0)
        builder.add_spring(int(b_), tb_, _ke, 0.0, 0.0)
        _tg.append((ta_, tb_))
    _o, _u = _pz['o'][_ok].astype(np.float64), _pz['u'][_ok].astype(np.float64)
    if 'sign' in _pz.files:
        _sg, _rm = _pz['sign'][_ok].astype(np.float64), _pz['r_min'][_ok].astype(np.float64)
    else:
        _sg, _rm = _path_sides(_pl, _o, _u, _pz['side'][_ok].astype(np.float64), _pz['group'][_ok],
                               _pz['rg'][_ok].astype(np.float64))
    PATH = {'A': _pl[:, 0], 'B': _pl[:, 1], 'T': np.asarray(_tg, np.int64).reshape(-1, 2),
            'o': _o, 'u': _u, 'sg': _sg, 'rmin': _rm,
            'c': float(_sp.get('carrot_mm', 10.0)) / 1000.0, 'sw': float(_sp.get('switch_mm', 15.0)) / 1000.0,
            'n': int(_sp.get('frames', 10 ** 6)), 'ke': _ke, 'done': np.zeros(int(_ok.sum()), bool),
            'fd': np.zeros(int(_ok.sum()), np.int64), 'g0': np.zeros(int(_ok.sum()))}
    log['seam_paths'] = {'pairs': int(_ok.sum()), 'carrot_mm': PATH['c'] * 1000, 'switch_mm': PATH['sw'] * 1000}
    if not _ok.any():                      # none of its pieces in this stage
        PATH = None


def _path_arc(P_, a, b, t):
    """Points at fraction t (per pair) along the arc from a to b about each pair's axis, and the arc lengths (m)."""
    da, db = a - P_['o'], b - P_['o']
    ha, hb = (da * P_['u']).sum(1), (db * P_['u']).sum(1)
    pa, pb = da - ha[:, None] * P_['u'], db - hb[:, None] * P_['u']
    ra, rb = np.linalg.norm(pa, axis=1), np.linalg.norm(pb, axis=1)
    e1 = pa / np.maximum(ra, 1e-9)[:, None]
    e2 = np.cross(P_['u'], e1)
    th = np.mod(np.arctan2((pb * e2).sum(1), (pb * e1).sum(1)), 2 * np.pi)
    dth = np.where(P_['sg'] > 0, th, th - 2 * np.pi)
    L = np.sqrt((np.maximum(0.5 * (ra + rb), P_['rmin']) * dth) ** 2 + (hb - ha) ** 2)
    r = np.maximum(ra + (rb - ra) * t, P_['rmin'])
    an = t * dth
    p = P_['o'] + (ha + (hb - ha) * t)[:, None] * P_['u'] + r[:, None] * (np.cos(an)[:, None] * e1 + np.sin(an)[:, None] * e2)
    return p, L, dth
# seam_bend: bending elements ACROSS stitched seams. A seam joins two separate meshes, so nothing resists bending
# across it: the front darts came out 67-82 deg sharp against the reference's 18-21 (2026-09-25, user: the reference's dart is more
# blended; wants it adjustable). For each boundary edge (a1,a2) whose ends are stitched to a boundary edge (b1,b2),
# one hinge with the shared edge (a1,a2) and the opposite vertices of the triangles on each side.
# {"pairs": [[A,B], ...] | "darts" (a piece to itself) | "all", "ke": stiffness, "rest_deg": 0,
#  "rest_deg_pairs": {"A|B": deg}, "rest_deg_seams": {seam id: deg}, "fold_ke": stiffness of those hinges (default ke)}
#  rest_deg_seams: the same fold along one seam only (two pieces can share several seams: the back vent's top and the
#  side seam both join the back and the side panel). deg + = the two pieces close face to face (each piece's face
#  side, its winding normal, turns towards the other), - = back to back. Newton's rest angle is -deg: the hinge
#  below has n1, n2 = the pieces' winding normals, and closing face to face gives a negative angle (checked
#  2026-10-01 on a right-angle fold).  -- a person's fold along the seams between two pieces (a collar turned down over
#  its stand), + = mountain seen from outside as fold_deg; those hinges are kept however sharp the start is
N_CLOTH_EDGES = None
SEAM_REST = None            # rest angle of every seam hinge, in the order added (restored after finalize)
SEAM_PAIR_H = {}            # "A|B" or "seam:<id>" -> [(oA, oB, k, l)] hinges with a person's fold angle (measured at the end)
SEAM_SET_DEG = {}           # the same keys -> the angle set, in the person's sign
if cfg.get('seam_bend'):
    sb = cfg['seam_bend']
    vpc = v_piece[sel]
    tri_of_e = {}
    for t_, (x_, y_, z_) in enumerate(F.tolist()):
        for e0, e1, op in ((x_, y_, z_), (y_, z_, x_), (z_, x_, y_)):
            tri_of_e.setdefault(frozenset((e0, e1)), []).append((e0, e1, op))
    bnd = {e: v[0] for e, v in tri_of_e.items() if len(v) == 1}          # boundary edge -> (e0, e1, opposite) in winding order
    def want(pa, pb):
        if sb['pairs'] == 'all':
            return True
        if sb['pairs'] == 'darts':
            return pa == pb
        return any({pat_names[pa], pat_names[pb]} == set(pr) for pr in sb['pairs'])
    partner = {}
    for a_, b_ in E:
        if want(vpc[a_], vpc[b_]):
            partner.setdefault(int(a_), []).append(int(b_)); partner.setdefault(int(b_), []).append(int(a_))
    Xc = d['Xclo'] / 1000.0
    def dihedral(o0, o1, k, l, P):
        e = P[l] - P[k]; n1 = np.cross(P[k] - P[o0], P[l] - P[o0]); n2 = np.cross(P[l] - P[o1], P[k] - P[o1])
        return np.degrees(np.arccos(np.clip(n1 @ n2 / (np.linalg.norm(n1) * np.linalg.norm(n2) + 1e-18), -1, 1)))
    added, skipped, seen = 0, 0, set()
    rest_from_drape = bool(sb.get('rest_from_drape')); rest_list = []
    rfc_pairs = [set(p_) for p_ in sb['rest_from_drape_pairs']] if sb.get('rest_from_drape_pairs') else None
    ke_s = float(sb.get('ke', cfg['edge_ke'])); rest_s = np.radians(float(sb.get('rest_deg', 0.0)))
    # the hinge built below (edge along piece A's winding, far corners on either side) has the opposite sign to the
    # fold lines': the shirt collar turned down outwards over its stand (the outer edge 20 mm below the seam and
    # 8.5 mm further out, reference 23 / 2.7) at Newton rest -160, and rolled inwards at +160 (2026-09-29)
    rdp = {frozenset(k_.split('|')): (k_, -np.radians(float(v_))) for k_, v_ in (sb.get('rest_deg_pairs') or {}).items()}
    rds = {str(k_): -np.radians(float(v_)) for k_, v_ in (sb.get('rest_deg_seams') or {}).items()}
    FOLD_V = {int(v_) for r_ in np.nonzero(np.isin(E_SEAM, list(rds)))[0] for v_ in E[r_]} if rds else set()
    SEAM_REST = []
    N_CLOTH_EDGES = builder.edge_count if hasattr(builder, 'edge_count') else None
    bnd_at = {}
    for eb_, (x_, y_, o_) in bnd.items():
        bnd_at.setdefault(x_, []).append((y_, o_)); bnd_at.setdefault(y_, []).append((x_, o_))
    X0m = X0
    for e, (k, l, oA) in bnd.items():
        for bk in partner.get(k, []):
            for bl in partner.get(l, []):
                if frozenset((bk, bl)) in bnd:
                    oB = bnd[frozenset((bk, bl))][2]
                else:
                    # the two sides are resampled differently: bk's boundary edge that heads towards bl
                    cand = bnd_at.get(bk, [])
                    if not cand:
                        continue
                    y_, oB = min(cand, key=lambda t: np.linalg.norm(X0m[t[0]] - X0m[bl]))
                    if np.linalg.norm(X0m[y_] - X0m[bl]) > np.linalg.norm(X0m[bk] - X0m[bl]):
                        continue
                key = (frozenset((k, l)), bk)
                if key in seen:
                    continue
                seen.add(key)
                # triangles (oA, k, l) and (oB, l, k) must turn the same way: check at the reference drape
                # (with rest_from_drape a sharp fold at the seam is the point: a shirt collar turns down ~170 deg
                # over its stand along the seam, not along an internal line)
                rfc = rest_from_drape and (rfc_pairs is None or {pat_names[vpc[k]], pat_names[vpc[bk]]} in rfc_pairs)
                if frozenset((pat_names[vpc[k]], pat_names[vpc[bk]])) in rdp:
                    continue                       # a person's fold: hinges along the stitch chain, below
                if FOLD_V and ({k, l, bk, bl} & FOLD_V):
                    continue                       # a person's fold on that seam only: below
                if dihedral(oA, oB, k, l, Xc) > 120 and not rfc:
                    skipped += 1; continue
                for q_ in (oA, oB, k, l):          # add_edge subtracts positions; cloth particles are stored as lists
                    if not isinstance(builder.particle_q[q_], wp.types.vector(3, wp.float32)):
                        builder.particle_q[q_] = wp.vec3(*builder.particle_q[q_])
                r_ = rest_s
                if rfc:                            # Newton's own rest-angle formula (ModelBuilder.add_edge), at the drape
                    x1, x2, x3, x4 = Xc[oA], Xc[oB], Xc[k], Xc[l]
                    n1 = np.cross(x3 - x1, x4 - x1); n1 /= np.linalg.norm(n1) + 1e-18
                    n2 = np.cross(x4 - x2, x3 - x2); n2 /= np.linalg.norm(n2) + 1e-18
                    ee = (x4 - x3) / (np.linalg.norm(x4 - x3) + 1e-18)
                    r_ = float(np.arctan2(np.cross(n1, n2) @ ee, np.clip(n1 @ n2, -1, 1)))
                    rest_list.append(r_)
                builder.add_edge(oA, oB, k, l, rest=r_, edge_ke=ke_s, edge_kd=cfg['edge_kd'])
                SEAM_REST.append(float(r_))
                added += 1
    # a person's fold along the seams of two pieces: one hinge between each two neighbouring stitches along the
    # seam (the stitched vertices are sparser than the outline's: only 8 of the shirt collar's 35 stitches had a
    # stitched neighbour on the same boundary edge, 2026-09-29). The hinge edge runs from stitch to stitch on piece
    # A in A's winding; each side's other corner is the far corner of that piece's boundary triangle there.
    def _chain_hinges(prs_, key_, rad_):
        """prs_ {A vertex: its stitched B vertex}: hinges between neighbouring stitches, rest rad_"""
        added_ = 0
        if len(prs_) < 2:
            return 0
        av = list(prs_)
        P_ = X0m[av]
        dd_ = np.linalg.norm(P_[:, None] - P_[None], axis=2)
        cur = int(np.argmax(dd_.max(1)))              # an end of the (open) seam: the farthest from the others
        chain, left = [cur], set(range(len(av))) - {cur}
        while left:
            nx = min(left, key=lambda q: dd_[chain[-1], q]); chain.append(nx); left.discard(nx)
        steps = np.array([dd_[chain[q], chain[q + 1]] for q in range(len(chain) - 1)])
        lim = 3.0 * float(np.median(steps))
        for q in range(len(chain) - 1):
            if steps[q] > lim:
                continue                              # a gap in the seam (two seams between the same pieces)
            k0, l0 = av[chain[q]], av[chain[q + 1]]
            b0, b1 = prs_[k0], prs_[l0]
            def corner(v, towards):
                cs = bnd_at.get(v, [])
                if not cs:
                    return None, None
                y_, o_ = min(cs, key=lambda t: np.linalg.norm(X0m[t[0]] - X0m[towards]))
                e0 = bnd[frozenset((v, y_))][0]
                return o_, (e0 == v)                  # far corner, and whether v -> y runs with the winding
            oA, fwdA = corner(k0, l0)
            oB, _ = corner(b0, b1)
            if oA is None or oB is None:
                continue
            k, l, kb = (k0, l0, b0) if fwdA else (l0, k0, b1)
            for q_ in (oA, oB, k, l):
                if not isinstance(builder.particle_q[q_], wp.types.vector(3, wp.float32)):
                    builder.particle_q[q_] = wp.vec3(*builder.particle_q[q_])
            builder.add_edge(oA, oB, k, l, rest=rad_, edge_ke=float(sb.get('fold_ke', ke_s)), edge_kd=cfg['edge_kd'])
            SEAM_REST.append(float(rad_)); SEAM_PAIR_H.setdefault(key_, []).append((oA, oB, k, l))
            added_ += 1
        SEAM_SET_DEG[key_] = round(float(-np.degrees(rad_)), 1)
        return added_
    for (pn, (key_, rad_)) in rdp.items():
        pa_, pb_ = sorted(pn)
        ia_, ib_ = pat_names.index(pa_), pat_names.index(pb_)
        prs_ = {}
        for a_, b_ in E:
            a_, b_ = int(a_), int(b_)
            if vpc[a_] == ib_ and vpc[b_] == ia_:
                a_, b_ = b_, a_
            if vpc[a_] == ia_ and vpc[b_] == ib_:
                prs_[a_] = b_
        added += _chain_hinges(prs_, key_, rad_)
    for sid_, rad_ in rds.items():
        rows_ = np.nonzero(E_SEAM == sid_)[0]
        if not len(rows_):
            log.setdefault('seam_bend_missing_seams', []).append(sid_); continue
        ia_ = min({int(vpc[v_]) for v_ in E[rows_].ravel()}, key=lambda i_: pat_names[i_])
        prs_ = {}
        for a_, b_ in E[rows_]:
            a_, b_ = int(a_), int(b_)
            if vpc[a_] != ia_:
                a_, b_ = b_, a_
            prs_[a_] = b_
        added += _chain_hinges(prs_, f"seam:{sid_}", rad_)
    log['seam_bend'] = {'added': added, 'skipped_opposite': skipped,
                        'pair_hinges': {k_: len(v_) for k_, v_ in SEAM_PAIR_H.items()},
                        'rest_from_drape': len(rest_list), 'rest_deg_p90': round(float(np.degrees(np.percentile(np.abs(rest_list), 90))), 1) if rest_list else 0}
body_cfg = builder.default_shape_cfg.copy()
body_cfg.mu = cfg.get('body_mu', 0.5)
BODY_KD_LIVE = float(body_cfg.kd)      # the product Live's damping, used before sewn (PRESEWN)
if FAST_LIVE:
    body_cfg.kd = float(cfg['body_kd'])
BODY_SHAPE = builder.add_shape_mesh(-1, mesh=newton.Mesh(Bv, Bt), cfg=body_cfg)
SYM_COLOR_LOG = None
_tmark('built')
if cfg.get('sym_color') and cfg.get('mirror_map'):
    # mirror-symmetric colouring (Fable review r6, M1): the VBD updates one colour after another, and 65 % of the
    # mirror-meshed twins' vertex pairs had different colours, so the two halves were updated at different times in
    # every iteration -- symmetric input, asymmetric result. Colour the graph without the copied (-x) vertices, copy
    # the colours across, recolour only non-twin vertices that then clash. Hinges with vertices on both sides (the
    # back-centre seam's bends) are left out of the colouring graph only (their two sides then update together).
    from newton._src.sim.graph_coloring import construct_particle_graph, color_graph
    _m = np.load(cfg['mirror_map'])
    _loc = remap if SUBSET else np.arange(N_FULL)
    _ok = (_loc[_m['tgt']] >= 0) & (_loc[_m['src'][:, 0]] >= 0)
    c_tgt, c_src = _loc[_m['tgt'][_ok]], _loc[_m['src'][_ok, 0]]
    NP = len(builder.particle_q)
    is_t = np.zeros(NP, bool); is_t[c_tgt] = True
    is_s = np.zeros(NP, bool); is_s[c_src] = True
    tri_ = np.array(builder.tri_indices, np.int32)
    tm_ = np.array(builder.tri_materials)
    be_ = np.array(builder.edge_indices, np.int32) if builder.edge_indices else None
    act_ = None
    n_straddle = 0
    if be_ is not None:
        bp_ = np.array(builder.edge_bending_properties)
        act_ = (bp_[:, 0] != 0.0) | (bp_[:, 1] != 0.0)
        e4 = be_.copy(); e4v = e4 >= 0
        has_t = (np.where(e4v, is_t[np.maximum(e4, 0)], False)).any(1)
        has_s = (np.where(e4v, is_s[np.maximum(e4, 0)], False)).any(1)
        strad = has_t & has_s
        n_straddle = int((strad & act_).sum())
        act_ = act_ & ~strad
    G_w = construct_particle_graph(tri_, tm_[:, 0] * tm_[:, 1], be_, act_, None, None)
    G = G_w.numpy() if hasattr(G_w, 'numpy') else np.asarray(G_w)
    keep_e = ~(is_t[G[:, 0]] | is_t[G[:, 1]])
    groups = color_graph(NP, wp.array(np.ascontiguousarray(G[keep_e]), dtype=int, device='cpu'), True, 1.1)
    col = np.full(NP, -1, np.int64)
    for g_, ids_ in enumerate(groups):
        col[np.asarray(ids_, np.int64)] = g_
    col[c_tgt] = col[c_src]
    adj = [[] for _ in range(NP)]
    for a_, b_ in G:
        adj[a_].append(b_); adj[b_].append(a_)
    fixed = 0
    for _pass in range(4):
        bad = np.nonzero(col[G[:, 0]] == col[G[:, 1]])[0]
        if not len(bad):
            break
        for e_ in bad:
            a_, b_ = G[e_]
            if col[a_] != col[b_]:
                continue
            v_ = b_ if (is_t[a_] or is_s[a_]) else a_
            if is_t[v_] or is_s[v_]:
                continue                               # both twins: left for the report
            used = {int(col[w]) for w in adj[v_]}
            c_ = 0
            while c_ in used:
                c_ += 1
            col[v_] = c_; fixed += 1
    clash = int((col[G[:, 0]] == col[G[:, 1]]).sum())
    builder.set_coloring([np.nonzero(col == c_)[0] for c_ in range(int(col.max()) + 1) if (col == c_).any()])
    SYM_COLOR_LOG = {'twin_pairs': int(len(c_tgt)), 'twin_same_colour': int((col[c_tgt] == col[c_src]).sum()),
                     'colours': int(col.max()) + 1, 'recoloured_non_twin': fixed, 'clashes_left': clash,
                     'hinges_out_of_graph': n_straddle}
else:
    builder.color(include_bending=True)
if cfg.get('color_dump'):
    # DIAGNOSIS: the vertex colouring (update order), with and without the size balancing; then stop
    def _cols():
        c_ = np.full(len(builder.particle_q), -1, np.int64)
        for g_, ids_ in enumerate(builder.particle_color_groups):
            c_[np.asarray(ids_, np.int64)] = g_
        return c_
    c_bal = _cols()
    builder.color(include_bending=True, balance_colors=False)
    np.savez(cfg['color_dump'], balanced=c_bal, unbalanced=_cols(), sel=sel)
    json.dump({'color_dump': cfg['color_dump']}, open(os.path.join(out, 'run_log.json'), 'w'))
    sys.exit(0)
_tmark('colored')
model = builder.finalize()
_tmark('finalized')
if MEMBRANE_BW:
    # aniso_membrane: the shear stiffness rides in tri_materials' drag column (unused by VBD); ku / kv are the
    # tri_ke / tri_ka the builder already wrote. cfg 'tri_ks' (N/m) for all, 'tri_ks_pieces' {piece: ks} over it.
    _tm = model.tri_materials.numpy()
    _tm[:, 3] = float(cfg.get('tri_ks', 0.0))
    for _pn, _v in (cfg.get('tri_ks_pieces') or {}).items():
        _tm[:len(F)][d['fp'] == pat_names.index(_pn), 3] = float(_v)
    model.tri_materials.assign(_tm)
    log['membrane'] = {'model': 'bw', 'ku': cfg['tri_ke'], 'kv': cfg['tri_ka'], 'ks': cfg.get('tri_ks', 0.0),
                       'ks_pieces': cfg.get('tri_ks_pieces'), 'comp': [cfg.get('bw_comp_ratio'), cfg.get('bw_comp_width'),
                                                                     cfg.get('bw_comp_scale')], 'hess_reg': cfg.get('bw_hess_reg'),
                       'newton': newton.__file__}
# cfg 'fabric_pieces' {piece: {"D": N m, "rho": kg/m2[, "ku" / "kv" / "ks": N/m]}} (core/fabric.py, user 2026-10-14): each named piece's own
# fabric; the pieces not named keep the garment's (cfg density / mi.D). Its particles' mass by rho here, its cloth
# hinges' bending in the mi block, a seam hinge between two fabrics after it. Prototyped and measured in
# foundation_2026-09-30/calib/fabric_piece (strips: each fabric drapes as alone, within 0.05 deg)
FP = {n_: f_ for n_, f_ in (cfg.get('fabric_pieces') or {}).items() if n_ in pat_names}
if FP and WELD is not None:
    log['fabric_pieces_skipped'] = 'weld: a welded vertex belongs to two pieces'
    FP = {}
if FP:
    _vp = v_piece[sel]
    _pm, _pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
    _r0 = float(cfg.get('density', 0.14))
    for _n, _f in FP.items():
        _ix = np.nonzero(_vp == pat_names.index(_n))[0]
        _pm[_ix] *= float(_f['rho']) / _r0
        _pim[_ix] = np.where(_pm[_ix] > 0, 1.0 / np.maximum(_pm[_ix], 1e-12), 0.0)
    model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
    log['fabric_pieces'] = {n_: dict(f_) for n_, f_ in FP.items()}
    # a fabric's own membrane (N/m, absolute; core/fabric.py, 2026-10-17): "ku" along the flat x, "kv" along the flat
    # y, "ks" the shear, in place of the garment's tri_ke / tri_ka / tri_ks on that piece's triangles (strengthen /
    # elastic below scale them as before). The BW membrane only (the isotropic one's tri_ke / tri_ka are Lame-like, not
    # these). Tested as stretch_scale_pieces x0.01 on a T-shirt rib in experiments/rib_thick_2026-10-17 (APPROVED
    # "T シャツの衿リブの生地"); this block gives the same triangle stiffnesses (experiments/piece_stretch_2026-10-17)
    _FK = {n_: f_ for n_, f_ in FP.items() if any(k_ in f_ for k_ in ('ku', 'kv', 'ks'))}
    if _FK and not MEMBRANE_BW:
        log['fabric_pieces_membrane_skipped'] = 'the per-piece stretch / shear needs membrane bw'
    elif _FK:
        _tm = model.tri_materials.numpy()
        log['fabric_pieces_membrane'] = {}
        for _n, _f in _FK.items():
            _T = np.nonzero(d['fp'] == pat_names.index(_n))[0]
            for _k, _c in (('ku', 0), ('kv', 1), ('ks', 3)):
                if _k in _f:
                    _tm[_T, _c] = float(_f[_k])
            log['fabric_pieces_membrane'][_n] = {'tris': int(len(_T)), **{k_: _f[k_] for k_ in ('ku', 'kv', 'ks') if k_ in _f}}
        model.tri_materials.assign(_tm)
# one_layer: the side taken out still weighs, on the triangles it is bound to (its flat area x the kept side's
# mass per area, shared by the barycentric weights); its bending goes on below with the hinges
if ONE_UNDER is not None and WELD is None:
    _ov, _ot, _ow = ONE_UNDER
    _loc = remap if SUBSET else np.arange(N_FULL)
    _pm, _pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
    _flm = np.asarray(d_flat_full, np.float64)
    _ar = np.zeros(N_FULL)
    for _p in np.unique(v_piece[_ov]):
        _Tp = F_FULL[FP_FULL == _p]
        _u2, _v2 = _flm[_Tp[:, 1]] - _flm[_Tp[:, 0]], _flm[_Tp[:, 2]] - _flm[_Tp[:, 0]]
        _a2 = 0.5 * np.abs(_u2[:, 0] * _v2[:, 1] - _u2[:, 1] * _v2[:, 0])
        _kt = ~np.isin(_Tp, BIND_V).any(1)
        _ak = np.zeros(N_FULL); np.add.at(_ak, _Tp[_kt].ravel(), np.repeat(_a2[_kt] / 3.0, 3))
        np.add.at(_ar, _Tp[~_kt].ravel(), np.repeat(_a2[~_kt] / 3.0, 3))
        _kv = np.nonzero((_ak > 0) & (_loc >= 0))[0]
        _rho = float(_pm[_loc[_kv]].sum() / max(_ak[_kv].sum(), 1e-12))       # kg per mm2, as the model has it
        _sel = v_piece[_ov] == _p
        _add = np.zeros(len(_pm))
        _tl = _loc[_ot[_sel]]
        _ok = (_tl >= 0).all(1)
        np.add.at(_add, _tl[_ok].ravel(), (_ow[_sel][_ok] * (_ar[_ov[_sel][_ok]] * _rho)[:, None]).ravel())
        _m = (_add > 0) & (_pm > 0)
        _pm[_m] += _add[_m]
        _pim[_m] = 1.0 / _pm[_m]
        log.setdefault('one_layer_mass', {})[pat_names[_p]] = round(float(_add.sum()) / max(
            float(_pm[_loc[_kv]].sum() - _add.sum()), 1e-12), 3)
    model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
# the masses before any piece is held: what a piece frozen and let go again in a Live stage gets back (live_params)
PM_FREE, PIM_FREE = model.particle_mass.numpy().copy(), model.particle_inv_mass.numpy().copy()
if cfg.get('frozen_pieces') or GHOST_P:
    fz = np.isin(v_piece[sel], [pat_names.index(n) for n in (cfg.get('frozen_pieces') or [])] + GHOST_P)
    pm, pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
    pm[fz] = 0.0; pim[fz] = 0.0
    model.particle_mass.assign(pm); model.particle_inv_mass.assign(pim)
    log_frozen = int(fz.sum())
else:
    log_frozen = 0
_seam_rests = None
if cfg.get('seam_bend') and cfg['seam_bend'].get('rest_from_drape'):
    _ra0 = model.edge_rest_angle.numpy(); _seam_rests = _ra0[len(_ra0) - log['seam_bend']['added']:].copy()
model.edge_rest_angle.zero_()                  # flat is the bending rest shape (cloth edges); seam hinges below
if WELD is not None and cfg.get('seam_bend'):
    # a welded seam is now an inner edge with the cloth's bending; give it the seam hinges' stiffness instead
    _ei = model.edge_indices.numpy(); _bp = model.edge_bending_properties.numpy()
    _wv = np.bincount(W_MAP, minlength=len(X0)) > 1
    _se = (_ei[:, 2] >= 0) & (_ei[:, 3] >= 0) & _wv[np.maximum(_ei[:, 2], 0)] & _wv[np.maximum(_ei[:, 3], 0)]
    _bp[_se, 0] = float(cfg['seam_bend'].get('ke', cfg['edge_ke'])); model.edge_bending_properties.assign(_bp)
    WELD['seam_edges'] = int(_se.sum())
if _seam_rests is not None:
    _ra = model.edge_rest_angle.numpy(); _ra[len(_ra) - len(_seam_rests):] = _seam_rests; model.edge_rest_angle.assign(_ra)
if cfg.get('seam_bend') and float(cfg['seam_bend'].get('rest_deg', 0.0)):
    _ra = model.edge_rest_angle.numpy(); _n = len(_ra) - log['seam_bend']['added']
    _ra[_n:] = np.radians(float(cfg['seam_bend']['rest_deg'])); model.edge_rest_angle.assign(_ra)
if SEAM_PAIR_H:                                # a person's seam folds: every seam hinge's own rest angle
    _ra = model.edge_rest_angle.numpy(); _ra[len(_ra) - len(SEAM_REST):] = np.asarray(SEAM_REST, np.float32)
    model.edge_rest_angle.assign(_ra)

# fold lines (BOM internal lines, tagged by build_pattern.py): take the fold angle the reference drape shows along each line.
# BOM has the lines but no angle; the angle is read off the reference result (a stand-in for a user-set angle).
# Same mesh built at the reference positions gives the rest angles in Newton's own sign convention.
if cfg.get('fold_from_drape'):
    patz = np.load(cfg['pattern'])
    Lf_full = np.concatenate([patz[f"Lf{i}"] for i in range(len(patz['names']))])
    meta = [m.split('|') for m in patz['fold_meta']]
    fold = Lf_full[sel]
    b2 = newton.ModelBuilder()
    b2.add_cloth_mesh(pos=wp.vec3(0.0, 0.0, 0.0), rot=wp.quat_identity(), scale=1.0, vel=wp.vec3(0.0, 0.0, 0.0),
                      vertices=[wp.vec3(*p) for p in d['Xclo'] / 1000.0], indices=F.reshape(-1).tolist(),
                      density=cfg.get('density', 0.14), tri_ke=cfg['tri_ke'], tri_ka=cfg['tri_ka'],
                      tri_kd=cfg['tri_kd'], edge_ke=cfg['edge_ke'], edge_kd=cfg['edge_kd'])
    m2 = b2.finalize()
    ei_all = model.edge_indices.numpy()
    e2 = m2.edge_indices.numpy()
    if m2.edge_count <= len(ei_all) and (e2 == ei_all[:m2.edge_count]).all():
        ei = ei_all[:m2.edge_count]                           # seam-bend hinges come after the cloth's own edges
        ra_ref = m2.edge_rest_angle.numpy()
    else:
        # the builder dropped degenerate triangles at the reference positions (shirt, 2026-09-28): match hinges by their
        # edge's vertex pair instead of by order; hinges with no reference counterpart keep a flat rest angle
        key = lambda E: np.minimum(E[:, 2], E[:, 3]).astype(np.int64) * 10 ** 7 + np.maximum(E[:, 2], E[:, 3])
        ei = ei_all
        pos = dict(zip(key(e2).tolist(), range(len(e2))))
        idx_ = np.array([pos.get(k, -1) for k in key(ei).tolist()])
        ra_m2 = m2.edge_rest_angle.numpy()
        ra_ref = np.where(idx_ >= 0, ra_m2[np.maximum(idx_, 0)], 0.0)
        log['fold_edge_match'] = {'hinges': int(len(ei)), 'matched': int((idx_ >= 0).sum())}
    v0, v1 = ei[:, 2], ei[:, 3]
    isf = (v0 >= 0) & (v1 >= 0) & (fold[v0] >= 0) & (fold[v0] == fold[v1])
    if cfg.get('fold_pieces'):
        okp = np.array([meta[f][0] in cfg['fold_pieces'] if f >= 0 else False for f in fold[v0]])
        isf &= okp
    min_deg = float(cfg.get('fold_min_deg', 0.0))
    ra = model.edge_rest_angle.numpy()
    log['folds'] = {}
    for fid in np.unique(fold[v0][isf]):
        m = isf & (fold[v0] == fid)
        med = float(np.degrees(np.median(ra_ref[m])))
        use = abs(med) >= min_deg
        if use:
            ra[:len(ra_ref)][m] = ra_ref[m]
        log['folds'][f"{meta[fid][0]}|{meta[fid][1]}"] = {'edges': int(m.sum()), 'ref_median_deg': round(med, 1), 'used': use}
    model.edge_rest_angle.assign(ra)

# fold_deg: {"piece|line id": degrees} -- a person's fold angle per internal line (the spec's folds); the same
# sign as the experiment's reference measurements (a skirt's flare peaks came out +30..+61 = mountain seen from outside)
if cfg.get('fold_deg'):
    patz = np.load(cfg['pattern'])
    Lf_full = np.concatenate([patz[f"Lf{i}"] for i in range(len(patz['names']))])
    meta = ['|'.join(m.split('|')[:2]) for m in patz['fold_meta']]
    fold = Lf_full[sel]
    ei = model.edge_indices.numpy()
    v0, v1 = ei[:, 2], ei[:, 3]
    ra = model.edge_rest_angle.numpy()
    log['fold_deg'] = {}
    FOLD_SET_EDGES = np.zeros(len(ra), bool)        # the hinges of fold lines with a set angle (fold_ke_scale)
    FOLD_HINGES = {}                                # per line: (hinge ids, set degrees) -- the guard's fold check
    for key_, deg in cfg['fold_deg'].items():
        if key_ not in meta:
            continue
        fid = meta.index(key_)
        m = (v0 >= 0) & (v1 >= 0) & (fold[np.maximum(v0, 0)] == fid) & (fold[np.maximum(v1, 0)] == fid)
        ra[m] = np.radians(float(deg))
        _setd = float(deg)
        # TEST cfg 'fold_profile' {"piece|line": [[depth mm, deg], ...]} (2026-10-10): the set angle along the line,
        # by depth from its top end on the pattern (the larger y), the sign of fold_deg's. Tucks and pleats,
        # read off its UVs, are folded flat (175-180 deg) at the sewn top and open towards their lower end (a
        # trouser tuck 178 -> 70-90 deg, a shirt's box pleat 175 -> 130 then 50 just past the end); one angle
        # measured mid-line for the whole line left ours open right under the top (user 2026-10-10)
        _prof = (cfg.get('fold_profile') or {}).get(key_)
        if _prof and m.any():
            if '_Vfull' not in globals():
                _Vfull = np.concatenate([patz[f"V{i}"] for i in range(len(patz['names']))])[sel]
            _lv = np.nonzero(fold == fid)[0]
            _c = _Vfull[_lv].mean(0)
            _ax = np.linalg.svd(_Vfull[_lv] - _c, full_matrices=False)[2][0]
            _ax = _ax if _ax[1] < 0 else -_ax                 # pointing down the line (y falls)
            _top = ((_Vfull[_lv] - _c) @ _ax).min()
            _hm = 0.5 * (_Vfull[v0[m]] + _Vfull[v1[m]])
            _dep = (_hm - _c) @ _ax - _top
            _pd, _pa = np.asarray(_prof, float).T
            _ang = np.clip(np.interp(_dep, _pd, _pa), 0.0, 170.0) * np.sign(float(deg))    # 180 is the hinge's singular end
            ra[m] = np.radians(_ang)
            _setd = float(np.median(_ang))
            log.setdefault('fold_profile', {})[key_] = {'hinges': int(m.sum()), 'deg_top_mid_end': [
                round(float(_ang[np.argmin(_dep)]), 1), round(_setd, 1), round(float(_ang[np.argmax(_dep)]), 1)]}
        FOLD_SET_EDGES |= m
        if m.any():
            FOLD_HINGES[key_] = (np.nonzero(m)[0], _setd)
        log['fold_deg'][key_] = {'edges': int(m.sum()), 'deg': float(deg)}
    model.edge_rest_angle.assign(ra)

def _terminal_controls(keys):
    ramp_pieces=set((cfg.get('fold_ramp') or {}).get('pieces',[]))|set((cfg.get('fold_turn') or {}).get('pieces',[]))
    if not keys:return
    if WELD is not None or keys.intersection(cfg.get('fold_profile') or {}) or keys.intersection(cfg.get('fold_free') or []) or any(k.split('|')[0] in ramp_pieces for k in keys):
        raise ValueError('End taper does not support welded meshes or overlapping fold controls')
    if BIND is not None:
        # BIND uses full garment IDs, while d.flat and model hinges already use
        # sel. Other pieces keep an exact material-coordinate correspondence.
        # Protect both the bound vertices and their host triangles; do not infer
        # safety just from which line owns a binding row.
        affected=np.unique(np.r_[BIND_V,np.asarray(BIND[0]).ravel()])
        bound_pieces={str(pat_names[i]) for i in np.unique(v_piece[affected])}
        overlap=sorted(k for k in keys if k.split('|')[0] in bound_pieces)
        if overlap:raise ValueError('End taper overlaps a bound piece: '+', '.join(overlap))


def _terminal_module():
    global _terminal,TERMINAL_GENERATOR_SHA
    if '_terminal' not in globals():
        path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'core','fold_reference.py')
        spec=importlib.util.spec_from_file_location('_ac9_terminal_field',path)
        TERMINAL_GENERATOR_SHA=hashlib.sha256(open(path,'rb').read()).hexdigest()
        _terminal=importlib.util.module_from_spec(spec);spec.loader.exec_module(_terminal)
    return _terminal


# Per-line target field; it does not create or fit a natural geometric reference.
if cfg.get('experimental_fold_terminal'):
    _active_terminal=set(cfg['experimental_fold_terminal']['lines'])
    _terminal_controls(_active_terminal)
    _terminal=_terminal_module()
    _ra,_audit=_terminal.apply_terminal_field(cfg['experimental_fold_terminal'],d['flat'],model.edge_indices.numpy(),model.edge_rest_angle.numpy(),globals().get('FOLD_HINGES',{}),cfg.get('fold_deg',{}))
    model.edge_rest_angle.assign(_ra)
    TERMINAL_TARGET_BASE=_ra.copy()
    log['experimental_fold_terminal']=_audit
    for _key,_line in _audit['lines'].items():
        FOLD_HINGES[_key]=(np.asarray(_line['hinges'],int),float(np.median(_line['target_deg'])))

# cfg 'fold_free': ["piece|line id", ...] -- a pressed fold that is folded at placement (dress.py prefold) and then
# left free along its line: its hinges get no bending stiffness, so nothing fights to fold (or unfold) it, while
# the turned part stays ordinary cloth that collides and is sewn (Fable r15 (a'); the 134 degree hinge of a back vent
# lifted the backs' hem ~10 mm, the vent taken out of the simulation came out under the wrong layer, RESULT_G3 23-25)
if cfg.get('fold_free'):
    patz = np.load(cfg['pattern'])
    Lf_ff = np.concatenate([patz[f"Lf{i}"] for i in range(len(patz['names']))])[sel]
    meta_ff = ['|'.join(m.split('|')[:2]) for m in patz['fold_meta']]
    ei = model.edge_indices.numpy(); v0, v1 = ei[:, 2], ei[:, 3]
    bp = model.edge_bending_properties.numpy()
    log['fold_free'] = {}
    for key_ in cfg['fold_free']:
        if key_ not in meta_ff:
            continue
        fid = meta_ff.index(key_)
        m = (v0 >= 0) & (v1 >= 0) & (Lf_ff[np.maximum(v0, 0)] == fid) & (Lf_ff[np.maximum(v1, 0)] == fid)
        bp[m] = 0.0
        log['fold_free'][key_] = int(m.sum())
    model.edge_bending_properties.assign(bp)

# per-piece bending scale. VBD's hinge energy is 1/2 * edge_ke * edge_length * theta^2, so the bending of a
# piece meshed finer comes out softer (about in proportion to the edge length); scale edge_ke back up.
if cfg.get('bend_scale_pieces'):
    ei_ = model.edge_indices.numpy()
    bp = model.edge_bending_properties.numpy()
    vp = v_piece[sel]
    for pname, fac in cfg['bend_scale_pieces'].items():
        pi_ = pat_names.index(pname)
        m_ = (ei_[:, 2] >= 0) & (vp[ei_[:, 2]] == pi_)
        bp[m_] *= float(fac)
    model.edge_bending_properties.assign(bp)
    log['bend_scale_pieces'] = cfg['bend_scale_pieces']



def _dihedral(P_, H_):
    """Newton's hinge angle (rad) for hinges H_ (o0, o1, v1, v2) on positions P_ (the sign of its rest angle)"""
    o0, o1, a_, b_ = P_[H_[:, 0]], P_[H_[:, 1]], P_[H_[:, 2]], P_[H_[:, 3]]
    n1 = np.cross(a_ - o0, b_ - o0); n1 /= np.linalg.norm(n1, axis=1, keepdims=True) + 1e-18
    n2 = np.cross(b_ - o1, a_ - o1); n2 /= np.linalg.norm(n2, axis=1, keepdims=True) + 1e-18
    e_ = (b_ - a_) / (np.linalg.norm(b_ - a_, axis=1, keepdims=True) + 1e-18)
    return np.arctan2((np.cross(n1, n2) * e_).sum(1), np.clip((n1 * n2).sum(1), -1, 1))


# cfg 'iron' (test option, user 2026-10-07: a pressed shape is the right shape, nothing keeps pushing): the hinges of
# the named pieces take as rest angle the angle they have in a shape -- the first stage given the option saves its
# start shape to shape_file (default <job>/iron_shape.npy) and later stages load it, so the shape pressed once stays
# the rest shape. {"pieces": [...], "seams": false (also the seam hinges between two of the pieces), "shape_file"}
if cfg.get('iron'):
    IR_ = cfg['iron']
    _sf = IR_.get('shape_file') or os.path.join(os.path.dirname(os.path.abspath(out)), 'iron_shape.npy')
    if os.path.exists(_sf):
        _Pf = np.load(_sf)
        log['iron_loaded'] = _sf
    else:
        _Pf = (X_full / 1000.0).copy()
        _Pf[sel] = np.asarray(X0, float) if WELD is None else _Pf[sel]
        np.save(_sf, _Pf)
        log['iron_saved'] = _sf
    _ei = model.edge_indices.numpy(); _ok = (_ei >= 0).all(1)
    _vp = v_piece[sel]; _pi = [pat_names.index(n) for n in IR_['pieces']]
    _inp = np.isin(_vp, _pi)
    _m = _ok.copy()
    _m[_ok] = _inp[_ei[_ok]].all(1)
    if not IR_.get('seams', False):
        _m[_ok] &= (_vp[_ei[_ok]] == _vp[_ei[_ok][:, :1]]).all(1)
    _ra = model.edge_rest_angle.numpy()
    _new = _dihedral(np.asarray(_Pf, float)[sel], _ei[_m])
    log['iron'] = {'hinges': int(_m.sum()), 'changed_deg_p50_max': [round(float(np.degrees(np.median(np.abs(_new - _ra[_m])))), 1),
                                                                   round(float(np.degrees(np.abs(_new - _ra[_m]).max())), 1)] if _m.any() else None}
    _ra[_m] = _new
    model.edge_rest_angle.assign(_ra)

# cfg 'fold_ramp' (test option, user 2026-10-07: sew the collar flat, then fold it): the fold-line hinges of the named
# pieces (fold_deg) start at the angle they have at the stage's start and reach their set angle over frames
FOLD_RAMP = None
if cfg.get('fold_ramp') and cfg.get('fold_deg'):
    FR_ = cfg['fold_ramp']
    _patz = np.load(cfg['pattern'])
    _Lf = np.concatenate([_patz[f"Lf{i}"] for i in range(len(_patz['names']))])[sel]
    _meta = ['|'.join(m.split('|')[:2]) for m in _patz['fold_meta']]
    _ei = model.edge_indices.numpy()
    _fids = [_meta.index(k_) for k_ in cfg['fold_deg'] if k_ in _meta and k_.split('|')[0] in FR_['pieces']]
    _m = (_ei[:, 2] >= 0) & (_ei[:, 3] >= 0) & (_ei[:, 0] >= 0) & (_ei[:, 1] >= 0) \
        & np.isin(_Lf[np.maximum(_ei[:, 2], 0)], _fids) & (_Lf[np.maximum(_ei[:, 2], 0)] == _Lf[np.maximum(_ei[:, 3], 0)])
    _ids = np.nonzero(_m)[0]
    _tgt = model.edge_rest_angle.numpy()[_ids].copy()
    _a0 = _dihedral(np.asarray(X0, float), _ei[_ids])
    FOLD_RAMP = (_ids, _a0, _tgt, int(FR_.get('frames', 40)))
    _ra = model.edge_rest_angle.numpy(); _ra[_ids] = _a0; model.edge_rest_angle.assign(_ra)
    log['fold_ramp'] = {'hinges': int(len(_ids)), 'start_deg_p50': round(float(np.degrees(np.median(_a0))), 1) if len(_ids) else None,
                        'target_deg_p50': round(float(np.degrees(np.median(_tgt))), 1) if len(_ids) else None,
                        'frames': FOLD_RAMP[3]}

# cfg 'fold_turn' (TEST 2026-10-07, foundation_2026-09-30/RESEARCH_fold_and_grab): the fold lines of 90 degrees or
# more of the named pieces are folded by moving the far side (the side cut off from the piece's seams to the other
# named pieces), not by the simulation: {"pieces", "mode": turn | cyl | carry, "frames" (carry), "axis_xy" (mm, cyl)}.
# turn / cyl fold at the stage's start; carry carries the same turn out over "frames" with the far side held
FOLD_CARRY = None
DRIVE = None
if cfg.get('fold_turn') and cfg.get('fold_deg'):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fold_turn as FT
    FT_ = cfg['fold_turn']
    _patz = np.load(cfg['pattern'])
    _Lf = np.concatenate([_patz[f"Lf{i}"] for i in range(len(_patz['names']))])[sel]
    _meta = ['|'.join(m.split('|')[:2]) for m in _patz['fold_meta']]
    _ei = model.edge_indices.numpy()
    _ra_now = model.edge_rest_angle.numpy()
    _vpl = v_piece[sel]
    _pis = [pat_names.index(n) for n in FT_['pieces'] if n in pat_names]
    _flat = d['flat'].astype(np.float64)
    _X = np.asarray(X0, np.float64)
    log['fold_turn'] = {'mode': FT_.get('mode', 'turn'), 'lines': []}
    for _pn in FT_['pieces']:
        _pi = pat_names.index(_pn)
        _fids = [_meta.index(k_) for k_, dg_ in cfg['fold_deg'].items()
                 if k_ in _meta and k_.split('|')[0] == _pn and abs(float(dg_)) >= 90.0]
        _pv = np.nonzero(_vpl == _pi)[0]
        _roll = _pv[np.isin(_Lf[_pv], _fids)]
        if not _fids or len(_roll) < 3:
            continue
        _oth = np.isin(_vpl, [p_ for p_ in _pis if p_ != _pi])
        _seam = np.unique(np.r_[E[_oth[E[:, 1]] & (_vpl[E[:, 0]] == _pi), 0], E[_oth[E[:, 0]] & (_vpl[E[:, 1]] == _pi), 1]])
        _far = FT.far_side(F, _pv, _roll, _seam, _flat)
        if _far is None:
            log['fold_turn']['lines'].append({'piece': _pn, 'skipped': 'the line does not cut the piece'})
            continue
        _hm = (_ei >= 0).all(1)
        _hm[_hm] &= np.isin(_ei[_hm][:, 2], _roll) & np.isin(_ei[_hm][:, 3], _roll) \
            & (_Lf[_ei[_hm][:, 2]] == _Lf[_ei[_hm][:, 3]]) & (_vpl[_ei[_hm][:, 0]] == _pi) & (_vpl[_ei[_hm][:, 1]] == _pi)
        _H = _ei[_hm]
        # how far it is folded: the far side against the world's up (the collar's own hinges on the line come out
        # crumpled after sewing, -120..+109 deg at once); target = the set angle (A1 placed: 136.6, ended 138.4)
        _tgt = float(np.median([abs(float(dg_)) for k_, dg_ in cfg['fold_deg'].items()
                                if k_ in _meta and _meta.index(k_) in _fids]))
        _meas = FT.up_angle_fn(_flat, _roll, _far)
        _ax = np.asarray(FT_.get('axis_xy', [0, 0]), float) / 1000.0
        _pref = FT.radial_pref(_far, _ax)
        _a0 = _meas(_X)
        if FT_.get('mode') == 'cyl':
            _app = lambda p_, X_=_X, r_=_roll, s_=_seam, f_=_far: FT.cyl(X_, _flat, r_, s_, f_, _ax, p_)
            _phi, _Xn, _a1 = FT.fit_grid(_app, _meas, _tgt, _pref, lo=60.0, hi=180.0)
        else:
            _app = lambda p_, X_=_X, r_=_roll, f_=_far: FT.turn(X_, _flat, r_, f_, np.radians(p_))
            _phi, _Xn, _a1 = FT.fit_grid(_app, _meas, _tgt, _pref)
        log['fold_turn']['lines'].append({'piece': _pn, 'far_verts': int(len(_far)), 'roll_verts': int(len(_roll)),
                                          'hinges': int(len(_H)), 'start_deg_up': round(_a0, 1),
                                          'target_deg_up': round(_tgt, 1), 'reached_deg_up': round(_a1, 1),
                                          'turn_deg': round(float(_phi), 1),
                                          'hinge_deg_p50_start_end': [round(float(np.degrees(np.median(_dihedral(_X, _H)))), 1),
                                                                      round(float(np.degrees(np.median(_dihedral(_Xn, _H)))), 1)]})
        if FT_.get('mode') == 'carry':
            FOLD_CARRY = (FOLD_CARRY or []) + [(_far, _app, _phi, _H)]
        elif FT_.get('mode') == 'drive':
            # drive (Fable r1): turned a little each frame round the line as it is now, stopped short of the held
            # pieces and the body. The far vertices within one ring of the line or of a stitch are not checked (they
            # sit 1-2 mm from the stand / bodice all along)
            _ro, _J, _Tt = FT.drive_setup(_flat, _roll, _far)
            _nb = set(int(v_) for v_ in _roll) | set(int(v_) for v_ in np.unique(E))
            _skip = set(_nb)
            for t_ in F:
                if any(int(v_) in _nb for v_ in t_):
                    _skip.update(int(v_) for v_ in t_)
            _chk = np.array([v_ for v_ in _far if int(v_) not in _skip], np.int64)
            _, _rad0 = FT.turn_step(_X, _ro, _far, _J, _Tt, 0.0)
            _step = float(FT_.get('step_mm', 1.0)) / 1000.0
            _n = int(np.ceil(abs(np.radians(_phi)) * float(_rad0.max()) / _step))
            DRIVE = (DRIVE or []) + [{'far': _far, 'roll': _ro, 'J': _J, 'Tt': _Tt, 'chk': _chk, 'H': _H,
                                      'meas': _meas, 'tgt': _tgt, 'dphi': np.radians(_phi) / max(_n, 1), 'n': _n,
                                      'state': 'driving', 'steps': 0, 'piece': _pn, 'X_start': _X.copy()}]
        else:
            _X = _Xn
    if DRIVE:
        from spatial import cKDTree as _KDd, cKDTree      # cKDTree: _layer_sides
        _fzd = np.isin(_vpl, [pat_names.index(n_) for n_ in (cfg.get('frozen_pieces') or [])])
        _Thd = F[_fzd[F].all(1)].astype(np.int64)
        _Xd = np.asarray(X0, np.float64)
        _cen = _Xd[_Thd].mean(1)
        _nrm = np.cross(_Xd[_Thd[:, 1]] - _Xd[_Thd[:, 0]], _Xd[_Thd[:, 2]] - _Xd[_Thd[:, 0]])
        _bk = _KDd(np.asarray(Bv, float)); _, _bi = _bk.query(_cen)
        DRIVE_HELD = (_Thd, np.where(((_cen - _bk.data[_bi]) * _nrm).sum(1) >= 0, 1.0, -1.0))
        _bmd = wp.Mesh(points=wp.array(Bv, dtype=wp.vec3), indices=wp.array(Bt.astype(np.int32), dtype=wp.int32))

        @wp.kernel
        def _drive_body_sd(m: wp.uint64, xs: wp.array(dtype=wp.vec3), maxd: float, out_s: wp.array(dtype=float)):
            i = wp.tid()
            p = xs[i]
            q = wp.mesh_query_point_sign_normal(m, p, maxd)
            out_s[i] = maxd
            if q.result:
                cp = wp.mesh_eval_position(m, q.face, q.u, q.v)
                out_s[i] = wp.dot(p - cp, wp.mesh_eval_face_normal(m, q.face))

        def _drive_body(P_):
            o_ = wp.zeros(len(P_), dtype=float)
            wp.launch(_drive_body_sd, dim=len(P_),
                      inputs=[_bmd.id, wp.array(P_.astype(np.float32), dtype=wp.vec3), 0.02, o_])
            return o_.numpy().astype(np.float64)
        DRIVE_STOP = float(FT_.get('stop_mm', 1.0)) / 1000.0
        DRIVE_REACH = 0.01
        cfg['frames'] = max(int(cfg['frames']), max(D_['n'] for D_ in DRIVE) * 3 // 2 + 40)
        for D_ in DRIVE:
            log['fold_turn']['lines'][-1].update({'drive_steps_planned': D_['n'], 'checked_verts': int(len(D_['chk'])),
                                                  'held_tris': int(len(_Thd)), 'stage_frames': cfg['frames']})
    if FOLD_CARRY is None and not DRIVE:
        X0 = _X.astype(np.float32)
        model.particle_q.assign(np.r_[X0, model.particle_q.numpy()[len(X0):]].astype(np.float32))
    elif FOLD_CARRY is not None:
        FOLD_CARRY = (FOLD_CARRY, int(FT_.get('frames', 20)))

# optional elastic stand-in: shrink the rest shape of named pieces along their flat long axis
# (the reference wears the waistband up to ~11 % shorter than the pattern; BOM carries no elastic data)
def pose_from_flat(poses, t, S=np.eye(2)):
    flat = d['flat'] if WELD is None else FLAT_REST
    i, j, k = F[t] if WELD is None else F_REST[t]
    qp = S @ (flat[j] - flat[i]) / 1000.0; rp = S @ (flat[k] - flat[i]) / 1000.0
    if MEMBRANE_BW:
        # aniso_membrane: the rest frame is the flat pattern's own x / y (warp / weft), the same for every triangle,
        # so the BW axes u / v mean the same thing across a piece (a mirrored piece flips v; the energy is even in it)
        D = np.array([[qp[0], rp[0]], [qp[1], rp[1]]])
    else:
        e1 = qp / np.linalg.norm(qp); e2 = np.array([-e1[1], e1[0]])
        D = np.array([[qp @ e1, rp @ e1], [qp @ e2, rp @ e2]])
    if np.linalg.det(D) < 0:
        D[1] *= -1
    poses[t] = np.linalg.inv(D)


# rest shape of every triangle straight from the flat pattern (the initial placement may then be stretched)
def poses_from_flat_all(poses):
    """pose_from_flat for every triangle at once (S = identity), through the same numpy routines (np.linalg.det / inv
    are the same gufunc for one matrix or a stack): equal value for value to the loop (sim_latency 2026-10-20, 0.3 s
    less per stage at 6 mm; env AC9_POSE_CHECK=1 also runs the loop and stops if they differ)."""
    flat = d['flat'] if WELD is None else FLAT_REST
    T_ = F if WELD is None else F_REST
    qp = (flat[T_[:, 1]] - flat[T_[:, 0]]) / 1000.0
    rp = (flat[T_[:, 2]] - flat[T_[:, 0]]) / 1000.0
    if MEMBRANE_BW:
        D = np.stack([np.stack([qp[:, 0], rp[:, 0]], 1), np.stack([qp[:, 1], rp[:, 1]], 1)], 1)
    else:
        e1 = qp / np.linalg.norm(qp, axis=1, keepdims=True)
        e2 = np.stack([-e1[:, 1], e1[:, 0]], 1)
        D = np.stack([np.stack([(qp * e1).sum(1), (rp * e1).sum(1)], 1),
                      np.stack([(qp * e2).sum(1), (rp * e2).sum(1)], 1)], 1)
    neg = np.linalg.det(D) < 0
    D[neg, 1] *= -1
    poses[:] = np.linalg.inv(D)


if cfg.get('rest_from_flat'):
    poses = model.tri_poses.numpy()
    assert (model.tri_indices.numpy() == F).all()
    if os.environ.get('AC9_POSE_FAST', '1') != '0':
        poses_from_flat_all(poses)
        if os.environ.get('AC9_POSE_CHECK') == '1':
            _pl = poses.copy()
            for t in range(len(F)):
                pose_from_flat(_pl, t)
            if not np.array_equal(_pl, poses):
                raise SystemExit(f"poses_from_flat_all differs from the loop: max {np.abs(_pl - poses).max():.3g}")
            log['pose_check'] = 'equal'
    else:
        for t in range(len(F)):
            pose_from_flat(poses, t)
    model.tri_poses.assign(poses)
    log['rest_from_flat'] = True

if cfg.get('ease'):
    # cfg 'ease': [{"piece", "seam_flat": [[x, y], ...] (mm, the longer side of a seam in the flat layout), "s0" (one
    # value or one per segment of seam_flat), "w"}]
    # -- easing (いせ込み): the rest shape of the triangles within w mm of the seam is shrunk ALONG the seam to s0 at the
    # seam, back to 1 at w (as steaming the ease in). A seam whose sides differ in length otherwise gathers the longer
    # side and its folds eat the panel's length once the cloth no longer stretches (Y3: ratio 1.117 -> hem +15 mm,
    # 1.229 -> +39 mm, foundation_2026-09-30/RESULT_Y3; REVIEW_fable_G4_design §1)
    poses = model.tri_poses.numpy()
    flat = d['flat']
    fp_ = d['fp']
    log['ease'] = []
    for e_ in cfg['ease']:
        pi_ = pat_names.index(e_['piece'])
        Pl = np.asarray(e_['seam_flat'], float)
        # s0: one value, or one per segment of seam_flat (a seam sewn notch to notch eases segment by segment)
        s0l_ = np.broadcast_to(np.asarray(e_['s0'], float), (max(1, len(Pl) - 1),))
        s0_, w_ = float(s0l_.min()), float(e_['w'])
        n_t = 0
        for t in np.nonzero(fp_ == pi_)[0]:
            c_ = flat[F[t]].mean(0)
            A_, B_ = Pl[:-1], Pl[1:]
            AB = B_ - A_
            u_ = np.clip(((c_ - A_) * AB).sum(1) / np.maximum((AB * AB).sum(1), 1e-12), 0.0, 1.0)
            Q_ = A_ + u_[:, None] * AB
            dd = np.linalg.norm(c_ - Q_, axis=1)
            k_ = int(np.argmin(dd))
            if dd[k_] >= w_:
                continue
            tg = AB[k_] / max(np.linalg.norm(AB[k_]), 1e-12)
            if s0l_[k_] >= 1.0:
                continue
            s_ = s0l_[k_] + (1.0 - s0l_[k_]) * dd[k_] / w_
            S_ = np.eye(2) + (s_ - 1.0) * np.outer(tg, tg)      # shrink along the seam's tangent only
            pose_from_flat(poses, t, S_)
            n_t += 1
        log['ease'].append({'piece': e_['piece'], 's0': s0_, 'w': w_, 'tris': n_t})
    model.tri_poses.assign(poses)

if cfg.get('shrink'):
    pat = np.load(cfg['pattern'])
    names = list(pat['names'])
    flat = d['flat']; fp = d['fp']
    poses = model.tri_poses.numpy()
    tri_idx = model.tri_indices.numpy()
    assert len(tri_idx) == len(F) and (tri_idx == F).all()
    log['shrink'] = {}
    for pname, fac in cfg['shrink'].items():
        pi = names.index(pname)
        tris = np.nonzero(fp == pi)[0]
        vs = np.unique(F[tris])
        P = flat[vs] - flat[vs].mean(0)
        ax = np.linalg.svd(P, full_matrices=False)[2][0]          # long axis in the flat pattern
        S = np.eye(2) + (fac - 1.0) * np.outer(ax, ax)          # shrink along the long axis only
        for t in tris:
            pose_from_flat(poses, t, S)             # aniso_membrane: the same frame rule as rest_from_flat
        log['shrink'][pname] = {'factor': fac, 'tris': int(len(tris))}
    model.tri_poses.assign(poses)


def _rest_edges(poses_, T_):
    """The rest edges (j - i, k - i) of triangles T_ in the flat layout's frame, 2 x 2 per triangle (columns), as their
    poses hold them now: the pose is the inverse of the edges in the triangle's own frame, which is the flat frame
    turned (or mirrored), so the polar decomposition of D Q^-1 takes the turn out and leaves the stretch the earlier
    blocks gave (rest_from_flat 1, ease, shrink)."""
    flat_ = d['flat'] if WELD is None else FLAT_REST
    F_ = F if WELD is None else F_REST
    Q_ = np.stack([flat_[F_[T_, 1]] - flat_[F_[T_, 0]], flat_[F_[T_, 2]] - flat_[F_[T_, 0]]], -1) / 1000.0
    A_ = np.linalg.inv(poses_[T_].astype(np.float64)) @ np.linalg.inv(Q_)
    W_, s_, Vt_ = np.linalg.svd(A_)
    P_ = np.transpose(Vt_, (0, 2, 1)) @ (s_[..., None] * Vt_)       # the symmetric stretch of A = U P
    return P_ @ Q_


def _poses_of(E_):
    """Poses (inverse rest matrices in the triangle's own frame) of rest edges E_ (n x 2 x 2, columns), as
    pose_from_flat makes them."""
    q_, r_ = E_[:, :, 0], E_[:, :, 1]
    if MEMBRANE_BW:
        D_ = np.array(E_, dtype=np.float64)          # aniso_membrane: flat x / y frame, as pose_from_flat
        D_[np.linalg.det(D_) < 0, 1] *= -1
        return np.linalg.inv(D_)
    lq = np.linalg.norm(q_, axis=1)
    e1 = q_ / lq[:, None]
    e2 = np.stack([-e1[:, 1], e1[:, 0]], 1)
    D_ = np.zeros((len(E_), 2, 2))
    D_[:, 0, 0] = lq; D_[:, 0, 1] = (r_ * e1).sum(1); D_[:, 1, 1] = (r_ * e2).sum(1)
    D_[D_[:, 1, 1] < 0, 1] *= -1
    return np.linalg.inv(D_)


# cfg 'elastic' (S3 ゴム・縮み, 2026-10-13): a stretch of cloth whose rest length is shorter than its pattern -- an
# elastic waistband, a cuff, shirring. Kept apart from 'ease' (the easing chat's, not changed here) and composed on top
# of whatever rest shape the blocks above gave. [{"piece", "ratio" (rest length / pattern length along the direction,
# < 1 shrinks), "line": [[x, y], ...] (flat mm; absent = the whole piece), "w" (mm each side of the line, default 10),
# "dir": [dx, dy] (flat; absent = along the line, or the piece's long axis), "ke_scale" (the membrane's stiffness of
# the shrunk triangles, default 1), "start" (frame, default 1), "frames" (ramp, default 0 = at once)}]
ELASTIC, ELASTIC_A = [], {}
ELASTIC_P, ELASTIC_FROM = [], {}      # each entry's piece; the stretch a Live change starts from (live_params)
if cfg.get('elastic'):
    poses = model.tri_poses.numpy()
    tm = model.tri_materials.numpy()
    flat = d['flat'] if WELD is None else FLAT_REST
    F_el = F if WELD is None else F_REST
    fp_ = d['fp']
    log['elastic'] = []
    for el in cfg['elastic']:
        pi_ = pat_names.index(el['piece'])
        T_ = np.nonzero(fp_ == pi_)[0]
        C_ = flat[F_el[T_]].mean(1)
        if len(T_) and el.get('line'):
            Pl = np.asarray(el['line'], float)
            A_, B_ = Pl[:-1], Pl[1:]
            AB = B_ - A_
            u_ = np.clip(((C_[:, None] - A_[None]) * AB[None]).sum(-1) / np.maximum((AB * AB).sum(1), 1e-12)[None], 0, 1)
            dd = np.linalg.norm(C_[:, None] - (A_[None] + u_[..., None] * AB[None]), axis=-1)
            k_ = dd.argmin(1)
            keep = dd[np.arange(len(T_)), k_] < float(el.get('w', 10.0))
            T_, k_ = T_[keep], k_[keep]
            tg = AB[k_] / np.maximum(np.linalg.norm(AB[k_], axis=1, keepdims=True), 1e-12)
        elif len(T_):
            P_ = flat[np.unique(F_el[T_])]
            tg = np.tile(np.linalg.svd(P_ - P_.mean(0), full_matrices=False)[2][0], (len(T_), 1))
        if not len(T_):
            log['elastic'].append({'piece': el['piece'], 'tris': 0})
            continue
        if el.get('dir') is not None:
            dv = np.asarray(el['dir'], float)
            tg = np.tile(dv / np.linalg.norm(dv), (len(T_), 1))
        E0 = _rest_edges(poses, T_)
        TT = tg[:, :, None] * tg[:, None, :]
        ks = float(el.get('ke_scale', 1.0))
        if ks != 1.0:
            tm[T_, 0:2] *= ks
            tm[T_, 3] *= ks                      # aniso_membrane: the shear too
        r_, st_, n_ = float(el['ratio']), int(el.get('start', 1)), int(el.get('frames', 0))
        ELASTIC.append((T_, E0, TT, r_, st_, n_))
        ELASTIC_P.append(el['piece'] if not el.get('line') else None)
        log['elastic'].append({'piece': el['piece'], 'ratio': r_, 'tris': int(len(T_)), 'line': bool(el.get('line')),
                               'ke_scale': ks, 'start': st_, 'frames': n_})
    model.tri_materials.assign(tm)


def _elastic_at(f_):
    """The rest shapes of the elastic triangles at frame f_ (in place); True when anything changed."""
    if not ELASTIC:
        return False
    poses_ = None
    for k_, (T_, E0, TT, r_, st_, n_) in enumerate(ELASTIC):
        # frame 0 = before the first frame: an elastic starting at frame 1 at once is on from the start
        if n_ == 0:
            a_ = 1.0 if f_ >= st_ or (f_ == 0 and st_ <= 1) else 0.0
        else:
            a_ = min(1.0, max(0.0, (f_ - st_ + 1) / n_))
        if ELASTIC_A.get(k_) == a_:
            continue
        ELASTIC_A[k_] = a_
        s0_ = ELASTIC_FROM.get(k_, 1.0)
        s_ = s0_ + (r_ - s0_) * a_
        if poses_ is None:
            poses_ = model.tri_poses.numpy()
        poses_[T_] = _poses_of((np.eye(2)[None] + (s_ - 1.0) * TT) @ E0)
    if poses_ is not None:
        model.tri_poses.assign(poses_)
        return True
    return False


_elastic_at(0)
if model.spring_count:
    model.spring_rest_length.zero_()           # stitches pull to zero length
# stitch_rest_mm: a closed seam cannot reach 0 (contact keeps the two layers ~1 mm apart), so a zero-length stitch
# keeps pulling (~stitch_ke * 1 mm each) forever; with a rest length of the contact gap it goes slack once closed.
SREST = float(cfg.get('stitch_rest_mm', 0.0)) / 1000.0
model.soft_contact_ke = cfg.get('soft_contact_ke', 1.0e3)
model.soft_contact_kd = cfg.get('soft_contact_kd', 1.0e-2)
model.soft_contact_mu = cfg.get('soft_contact_mu', 0.5)
# cfg 'mi' (Fable REVIEW_fable_mesh_independence_2026-10-07 S1, measured in calib/solver_mi 2026-10-08): the stiffnesses defined per length /
# area so the same numbers mean the same cloth at any mesh size. h = the median cloth edge length.
#   ref_mm   the mesh the per-stitch / per-pair values were tuned on (6): stitches x h/ref, self contact x (h/ref)^2
#   D        flexural rigidity N m: every cloth hinge gets stiffness alpha*D*|e|/(A1+A2) (Newton multiplies by |e|),
#            alpha 1.155 = the C 0.75 conversion on an equilateral mesh
#   ac_correct  divide by C(a/c)/0.75 from the strip sweep (calib/mesh_sweep, coarse meshes come out softer)
MI = cfg.get('mi') or {}
if MI:
    _ei = model.edge_indices.numpy(); _bp = model.edge_bending_properties.numpy(); _q0 = model.particle_q.numpy()
    _ncl = len(_ei) - int((log.get('seam_bend') or {}).get('added', 0))
    _ev = _ei[:_ncl]
    _el = np.linalg.norm(_q0[_ev[:, 3]] - _q0[_ev[:, 2]], axis=1)
    h_mi = float(np.median(_el))
    ref = float(MI.get('ref_mm', 6.0)) / 1000.0
    log['mi'] = {'h_mm': round(h_mi * 1000, 3)}
    # pieces meshed at different sizes (pattern h_by_piece): every vertex's own mesh size; uniform = None (as before)
    _hv = None
    try:
        _hp = np.load(cfg['pattern'])['h_by_piece'] if cfg.get('pattern') else None
    except (KeyError, OSError, ValueError):
        _hp = None
    if _hp is not None and len(_hp) and float(np.ptp(_hp)) > 1e-9:
        _full = sel if SUBSET else np.arange(N_FULL)
        _hv = np.asarray(_hp, float)[v_piece[_full]] / 1000.0
        log['mi']['h_by_piece_mm'] = [float(x) for x in _hp]
    if MI.get('per_length', True) and model.spring_count:
        _k = model.spring_stiffness.numpy()
        if _hv is None:
            model.spring_stiffness.assign(_k * (h_mi / ref))
            model.soft_contact_ke = float(model.soft_contact_ke) * (h_mi / ref) ** 2
            log['mi'].update(stitch_scale=round(h_mi / ref, 4), soft_contact_ke=model.soft_contact_ke)
        else:
            # a stitch spaced for the finer of its two pieces (pattern_build); contact for the finest piece (one value
            # for the whole model in Newton: the coarser pieces get a softer contact, the safe side, Fable r3 §1-3)
            _si = model.spring_indices.numpy().reshape(-1, 2)
            _hs = np.ones(len(_k)) * h_mi
            _ok = (_si < len(_hv)).all(1)
            _hs[_ok] = np.minimum(_hv[_si[_ok, 0]], _hv[_si[_ok, 1]])
            model.spring_stiffness.assign((_k * (_hs / ref)).astype(np.float32))
            model.soft_contact_ke = float(model.soft_contact_ke) * (float(_hv.min()) / ref) ** 2
            log['mi'].update(stitch_scale_min_max=[round(float(_hs.min() / ref), 4), round(float(_hs.max() / ref), 4)],
                             soft_contact_ke=model.soft_contact_ke)
    if MI.get('D'):
        D = float(MI['D'])
        inner = (_ev[:, 0] >= 0) & (_ev[:, 1] >= 0)
        # only the plain cloth hinges (fold lines and welded seams keep their own stiffness)
        plain = inner & np.isclose(_bp[:_ncl, 0], float(cfg['edge_ke']))
        a, b, o0, o1 = _q0[_ev[:, 2]], _q0[_ev[:, 3]], _q0[np.maximum(_ev[:, 0], 0)], _q0[np.maximum(_ev[:, 1], 0)]
        A = 0.5 * (np.linalg.norm(np.cross(b - a, o0 - a), axis=1) + np.linalg.norm(np.cross(b - a, o1 - a), axis=1))
        _De, _re = D, float(cfg.get('density', 0.14))              # each hinge's fabric (fabric_pieces)
        if FP:
            _hpc = v_piece[sel][_ev[:, 2]]
            _De, _re = np.full(len(_ev), _De), np.full(len(_ev), _re)
            for _n, _f in FP.items():
                _m = _hpc == pat_names.index(_n)
                _De[_m], _re[_m] = float(_f['D']), float(_f['rho'])
        ke_e = float(MI.get('alpha', 1.155)) * _De * _el / np.maximum(A, 1e-12)   # A = A1 + A2
        if MI.get('ac_correct'):
            c = (_De / (_re * 9.81)) ** (1 / 3)
            # a hinge's own mesh size when pieces differ (the mesh of the edge's first vertex)
            ac = h_mi / c if _hv is None else _hv[np.minimum(_ev[:, 2], len(_hv) - 1)] / c
            # C(a/c) from the strip sweep, hex0/hex90 averaged, converged runs (RESULT_calibration 10/07 night)
            C = np.interp(ac, [0.04, 0.2, 0.32, 0.43, 0.53, 0.61], [0.77, 0.74, 0.68, 0.62, 0.55, 0.55])
            ke_e = ke_e * 0.75 / C
            if _hv is None and not FP:
                log['mi'].update(a_over_c=round(float(ac), 3), C=round(float(C), 3))
            else:
                log['mi'].update(C_min_max=[round(float(np.min(C)), 3), round(float(np.max(C)), 3)])
        _bp[:_ncl, 0] = np.where(plain, ke_e, _bp[:_ncl, 0])
        if FP:
            # per fabric: the hinges' stiffness, the bending length c and the mesh it asks for (h <= c / 4)
            _byp = {}
            for _n in list(FP) + ['(garment)']:
                _m = plain & (np.isin(_hpc, [pat_names.index(x) for x in FP], invert=True) if _n == '(garment)'
                              else _hpc == pat_names.index(_n))
                if _m.any():
                    _c = float(np.median(np.broadcast_to(c, _m.shape)[_m])) if MI.get('ac_correct') else \
                        float((np.median(_De[_m]) / (np.median(_re[_m]) * 9.81)) ** (1 / 3))
                    _byp[_n] = {'ke_p50': float(np.median(ke_e[_m])), 'c_mm': round(_c * 1000, 2),
                                'h_ok': bool(h_mi <= _c / 4)}
            log['mi']['by_piece'] = _byp
        _bp[:_ncl, 1] = np.where(plain, 1e-4 * ke_e, _bp[:_ncl, 1])
        model.edge_bending_properties.assign(_bp.astype(np.float32))
        log['mi'].update(hinges=int(plain.sum()), ke_p10_p50_p90=[float(x) for x in np.percentile(ke_e[plain], [10, 50, 90])])
if FP and MI.get('D') and len(_ei) > _ncl:
    _D0 = float(MI['D'])
    _vpp = v_piece[sel]
    _Dp = np.full(len(pat_names), _D0)
    for _n, _f in FP.items():
        _Dp[pat_names.index(_n)] = float(_f['D'])
    _es = _ei[_ncl:]
    _da, _db = _Dp[_vpp[_es[:, 0]]], _Dp[_vpp[_es[:, 1]]]
    _how = cfg.get('fabric_seam', 'softer')
    _ds = {'softer': np.minimum(_da, _db), 'stiffer': np.maximum(_da, _db), 'mean': 0.5 * (_da + _db)}[_how]
    _bps = model.edge_bending_properties.numpy()
    _bps[_ncl:, 0] *= (_ds / _D0).astype(np.float32)
    _bps[_ncl:, 1] *= (_ds / _D0).astype(np.float32)
    model.edge_bending_properties.assign(_bps.astype(np.float32))
    log['fabric_seam'] = {'how': _how, 'hinges': int(len(_es)), 'changed': int((_ds != _D0).sum()),
                          'scale_min_max': [round(float((_ds / _D0).min()), 4), round(float((_ds / _D0).max()), 4)]}
# cfg 'fold_ke_scale' (2026-10-09 as a TEST; the default 10 from core/recipe.FOLD_PRESS since 2026-10-29): the hinges of fold lines with a set angle (fold_deg) this many times stiffer than
# the cloth -- a pressed crease rather than plain cloth told a rest angle. At the cloth's own bending (Poplin 6, wool
# 18 uN m, through mi) a trouser's tucks reached their angles flat and in zero gravity, and opened to 22-45 deg once
# hung and sewn (user: the tucks never fold, while the prefolded vents and collars did)
if cfg.get('fold_ke_scale') and 'FOLD_SET_EDGES' in globals() and FOLD_SET_EDGES.any():
    _bpf = model.edge_bending_properties.numpy()
    _fm = FOLD_SET_EDGES[:len(_bpf)]
    _bpf[_fm, 0] *= float(cfg['fold_ke_scale'])
    _bpf[_fm, 1] *= float(cfg['fold_ke_scale'])
    model.edge_bending_properties.assign(_bpf.astype(np.float32))
    log['fold_ke_scale'] = {'scale': float(cfg['fold_ke_scale']), 'hinges': int(_fm.sum()),
                            'ke_p50': float(np.median(_bpf[_fm, 0]))}
# one_layer: the hinges under the side taken out bend for two layers (two cloths sewn together slide on each other:
# their bendings add, x2; bonded they would be x8). After the fabric's bending above, which sets the plain hinges
if ONE_UNDER is not None and WELD is None:
    _loc = remap if SUBSET else np.arange(N_FULL)
    _und = np.zeros(model.particle_count, bool)
    _tl = _loc[ONE_UNDER[1]].ravel()
    _und[_tl[_tl >= 0]] = True
    _eio = model.edge_indices.numpy(); _bpo = model.edge_bending_properties.numpy()
    _mo = (_eio[:, 2] >= 0) & (_eio[:, 3] >= 0) & _und[np.clip(_eio[:, 2], 0, None)] \
        & _und[np.clip(_eio[:, 3], 0, None)]
    _bpo[_mo, 0] *= 2.0
    model.edge_bending_properties.assign(_bpo.astype(np.float32))
    log['one_layer_hinges_x2'] = int(_mo.sum())
log['model'] = {'particles': model.particle_count, 'tris': model.tri_count, 'edges': model.edge_count,
                'springs': model.spring_count, 'body_tris': len(Bt) // 3}

# stitched pairs: exclude vertex a vs triangles around b (and b vs around a)
tri_of = [[] for _ in range(len(X0))]
for ti, (i, j, k) in enumerate(F):
    tri_of[i].append(ti); tri_of[j].append(ti); tri_of[k].append(ti)
ring = [set() for _ in range(len(X0))]
for i, j, k in F:
    ring[i].update((j, k)); ring[j].update((i, k)); ring[k].update((i, j))
R = int(cfg.get('filter_rings', 0))      # 0: only the stitched vertex; 1: its one-ring too
vfilter = {}
R_int = int(cfg.get('internal_filter_rings', R))   # for seams ending on a piece's inside (pockets on panels)
GATE_ROWS = []                           # per stitch: [(vertex, triangles)] its exemption adds (cfg stitch_gate_mm)
for a, b in E:
    GATE_ROWS.append([])
    if not cfg.get('bind_filter', False) and ((int(a), int(b)) in RT_LOCAL or (int(b), int(a)) in RT_LOCAL):
        continue
    Rs = R_int if (interior[a] or interior[b]) else R
    for u, w in ((int(a), int(b)), (int(b), int(a))):
        us = {u} | (ring[u] if Rs >= 1 else set())
        ws = {w} | (ring[w] if Rs >= 1 else set())
        tris = set(t for x in ws for t in tri_of[x])
        for x in us:
            vfilter.setdefault(x, set()).update(tris)
            GATE_ROWS[-1].append((x, tris))

# layer_order with "free_contact": the two layers do not collide with each other at all; the frame-end push (below)
# is then the only thing between them, a one-sided contact. Pushing a vertex across a layer the contact also sees
# tangled the cloth (crossings 5 -> 121-233 in three runs, 2026-10-01).
efilter = {}
# cfg 'stitch_edge_filter' (2026-10-18, sew-rethink, experiments/converge_2026-10-18): the stitch filter above takes
# the vertex-triangle contacts across a stitch out, but the edge-edge contacts of the two sewn edges stayed in: a probe
# found 99-100 % of the cloth-cloth friction on stitched vertices, its sum over a T-shirt +1.3 N upward while sewing
# (the garment weighs 0.76 N), and the garment rose 2 cm after the seams closed. Edges touching u's set vs edges
# touching w's set (the same rings as the vertex filter) are taken out too.
if cfg.get('stitch_edge_filter') and model.edge_count:
    _EIF = model.edge_indices.numpy()[:, 2:].astype(np.int64)
    _v2e = [[] for _ in range(len(X0))]
    for _e, (_p, _q) in enumerate(_EIF):
        if 0 <= _p < len(X0):
            _v2e[_p].append(_e)
        if 0 <= _q < len(X0):
            _v2e[_q].append(_e)
    for a, b in E:
        Rs = R_int if (interior[a] or interior[b]) else R
        for u, w in ((int(a), int(b)), (int(b), int(a))):
            us = {u} | (ring[u] if Rs >= 1 else set())
            ws = {w} | (ring[w] if Rs >= 1 else set())
            eu = set(e_ for x in us for e_ in _v2e[x])
            ew = set(e_ for x in ws for e_ in _v2e[x])
            for e_ in eu:
                efilter.setdefault(int(e_), set()).update(ew)
    log['stitch_edge_filter'] = {'edge_keys': len(efilter)}
    # The two sewn edges may now pass each other on the seam line (the sewn partners' overlap there is the seam
    # itself, not cloth through cloth): the guard's crossing count leaves out a triangle pair touching u's set and
    # w's set of one stitch (pants crossings 8-14 -> 658-686 and Dress reporting them, 96-100 % of them such pairs,
    # experiments/bend_hyst_2026-10-18/cross_where.py)
    SEAM_TRI = {}
    for _k, (a, b) in enumerate(E):
        Rs = R_int if (interior[a] or interior[b]) else R
        for _sd, x0 in enumerate((int(a), int(b))):
            for x in {x0} | (ring[x0] if Rs >= 1 else set()):
                for t_ in tri_of[x]:
                    SEAM_TRI.setdefault(t_, set()).add((_k, _sd))
else:
    SEAM_TRI = None
if cfg.get('layer_order'):
    _loc = (lambda ix: remap[np.asarray(ix, np.int64)]) if SUBSET else (lambda ix: np.asarray(ix, np.int64))
    _EI = model.edge_indices.numpy()[:, 2:] if model.edge_count else np.zeros((0, 2), np.int64)
    for g in cfg['layer_order']:
        if not g.get('free_contact'):
            continue
        sets_ = []
        for key_ in ('lower', 'upper'):
            m_ = np.zeros(len(X0), bool); ix_ = _loc(g[key_]); m_[ix_[ix_ >= 0]] = True
            sets_.append(m_)
        lo_m, up_m = sets_[0] & ~sets_[1], sets_[1]
        for va_, vb_ in ((lo_m, up_m), (up_m, lo_m)):
            tb_ = set(np.nonzero(vb_[F].all(1))[0].tolist())
            for x in np.nonzero(va_)[0].tolist():
                vfilter.setdefault(int(x), set()).update(tb_)
        ea_ = np.nonzero(lo_m[_EI].all(1))[0].tolist(); eb_ = set(np.nonzero(up_m[_EI].all(1))[0].tolist())
        for e_ in ea_:
            efilter.setdefault(int(e_), set()).update(eb_)
        for e_ in eb_:
            efilter.setdefault(int(e_), set()).update(ea_)
    log['layer_free_contact'] = {'vert_keys': len(vfilter), 'edge_keys': len(efilter)}
# one_layer kept colliding (OL_KIN): its side taken out sits 0.5 mm on the side kept, inside the 2 mm contact
# distance, so the two would push each other every frame and the side kept would drift away (Fable 2026-10-24).
# They do not collide with each other: either side's vertices vs the other's triangles, and their edges
_ol_contact_default = BOUND_COUPLING and all(float(g.get('gap_mm', 0.5)) * .001 >
    float(cfg.get('self_margin', .002)) + float(cfg.get('self_gap', .001)) for g in OL)
if len(OL_KIN) and not (BOUND_COUPLING and cfg.get('one_layer_self_contact', _ol_contact_default)):
    _loc1 = (lambda ix: remap[np.asarray(ix, np.int64)]) if SUBSET else (lambda ix: np.asarray(ix, np.int64))
    _EI1 = model.edge_indices.numpy()[:, 2:] if model.edge_count else np.zeros((0, 2), np.int64)
    _nv, _ne = len(vfilter), len(efilter)
    for g in OL:
        if not np.isin(np.asarray(g['verts'], np.int64), OL_KIN).all():
            continue
        _pv = np.nonzero(v_piece == pat_names.index(g['piece']))[0]
        _out = np.zeros(len(X0), bool); _ix = _loc1(g['verts']); _out[_ix[_ix >= 0]] = True
        _kept = np.zeros(len(X0), bool); _ix = _loc1(np.setdiff1d(_pv, g['verts'])); _kept[_ix[_ix >= 0]] = True
        # "touching" either side: the triangles and edges across the fold line belong to both
        for va_, vb_ in ((_out, _kept), (_kept, _out)):
            tb_ = set(np.nonzero(vb_[F].any(1))[0].tolist())
            for x in np.nonzero(va_)[0].tolist():
                vfilter.setdefault(int(x), set()).update(tb_)
        ea_ = np.nonzero(_out[_EI1].any(1))[0].tolist(); eb_ = set(np.nonzero(_kept[_EI1].any(1))[0].tolist())
        for e_ in ea_:
            efilter.setdefault(int(e_), set()).update(eb_)
        for e_ in eb_:
            efilter.setdefault(int(e_), set()).update(ea_)
    log['one_layer_contact_filter'] = {'vert_keys': len(vfilter) - _nv, 'edge_keys': len(efilter) - _ne}
_tmark('filters')
solver = newton.solvers.SolverVBD(
    model, iterations=cfg.get('iterations', 10),
    particle_enable_self_contact=cfg.get('self_contact', True),
    particle_self_contact_margin=cfg.get('self_margin', 0.002),
    particle_self_contact_gap=cfg.get('self_gap', 0.001),
    particle_topological_contact_filter_threshold=cfg.get('topo_filter', 2),
    particle_external_vertex_contact_filtering_map=vfilter if (cfg.get('stitch_filter', True) or efilter) else None,
    **({'particle_external_edge_contact_filtering_map': efilter} if efilter else {}),
    rigid_contact_k_start=cfg.get('contact_k_start', 1.0e2),
    # cfg 'friction_epsilon' (m/s): below this sliding speed Newton's friction is a drag proportional to the speed,
    # not Coulomb's static friction, so a garment under a steady pull (its membrane pressing it on the body) keeps
    # sliding slower than this. Newton's default 1e-2 (10 mm/s) let a T-shirt creep up ~1.4 mm/s for ever; 1e-4 stopped
    # it in ~125 frames (experiments/creep_tshirt_2026-10-15, DECISIONS cloth-sim-friction-epsilon-2026-10-16).
    **({'friction_epsilon': float(cfg['friction_epsilon'])} if cfg.get('friction_epsilon') is not None else {}),
    # cfg 'deterministic': the same input gives the same result every run (the atomics' summing order fixed)
    **({'deterministic': getattr(wp.DeterministicMode, str(cfg['deterministic']))} if cfg.get('deterministic') else {}))
_tmark('solver')
solver.ac9_seam_filter_keys = (list(vfilter), list(efilter))   # read by the Accurate step cap only
if BOUND_COUPLING and cfg.get('one_layer_self_contact', _ol_contact_default) and cfg.get('self_contact', True):
    import nring_filter as _NRF_fold
    _fold_bound = np.isin(sel, OL_V)
    _fold_pieces = np.where(np.isin(v_piece[sel], [pat_names.index(g['piece']) for g in OL]), v_piece[sel], -1)
    log['one_layer_contact_restored'] = _NRF_fold.restore_fold_contacts(
        solver.trimesh_collision_detector, _fold_bound, _fold_pieces, vfilter, efilter)
log['friction_epsilon'] = float(solver.friction_epsilon)
# cfg 'fric_anchor' (2026-10-18, sew-rethink; Fable REVIEW_fable_energy_convergence T1): the body friction measures the
# slip from an anchor kept per particle across substeps (Cundall-Strack) instead of from the substep's start, so a
# load inside the friction cone is held, not crept through by friction_epsilon x dt every substep (the T-shirt slid up
# p99 7-19 mm / 100 frames without it). The anchor follows only by the slip beyond eps_u = friction_epsilon x dt
# (_fric_anchor_update after each substep). Needs the patched kernels (membrane 'bw'); zero-length arrays = off.
solver.ac9_fric_anchor = wp.zeros(0, dtype=wp.vec3)
solver.ac9_bend_fric = wp.zeros((0, 3), dtype=float)   # bend_fric2 solve 'inside' (below); zero rows = off
solver.ac9_fric_n = wp.zeros(0, dtype=wp.vec3)
solver.ac9_fric_hit = wp.zeros(0, dtype=int)
FANCH = None
if cfg.get('fric_anchor') and MEMBRANE_BW:
    solver.ac9_fric_anchor = wp.zeros(model.particle_count, dtype=wp.vec3)
    solver.ac9_fric_n = wp.zeros(model.particle_count, dtype=wp.vec3)
    solver.ac9_fric_hit = wp.zeros(model.particle_count, dtype=int)
    FANCH = {'a': solver.ac9_fric_anchor, 'n': solver.ac9_fric_n, 'hit': solver.ac9_fric_hit,
             'eps': float(solver.friction_epsilon) / (cfg['fps'] * (10 if PRESEWN else cfg.get('substeps', 10))),
             'eps_fast': float(solver.friction_epsilon) / (cfg['fps'] * cfg.get('substeps', 10)), 'slid': wp.zeros(1, dtype=float)}
    log['fric_anchor'] = {'eps_u_m': FANCH['eps'], 'slid_mm_per_frame': []}
elif cfg.get('fric_anchor'):
    log['fric_anchor'] = {'off': "needs membrane 'bw' (the patched kernels)"}
if BOUND_COUPLING:
    import bound_constraints as _BC
    _loc_bc = remap if SUBSET else np.arange(N_FULL)
    _bc_mask = np.isin(BIND_V, OL_V)
    _bv_bc = _loc_bc[BIND_V[_bc_mask]]
    _bt_bc = _loc_bc[BIND[0][_bc_mask]]
    if (_bv_bc < 0).any() or (_bt_bc < 0).any():
        raise ValueError('AC9 bound coupling needs every follower and support in the active model')
    solver.ac9_bound_constraints = _BC.BoundConstraints(model, _bv_bc, _bt_bc, BIND[1][_bc_mask], BIND[2][_bc_mask])
    solver.ac9_bound_constraints.native_fused = not FAST_LIVE
    log['bound_coupling'] = solver.ac9_bound_constraints.summary
NATIVE_COLOR_PLAN = None
if NATIVE_THREE:
    if not solver.device.is_cuda or model.body_count or model.tet_count:
        raise ValueError('AC9 Native 3 requires CUDA and a static-body cloth surface')
    if hasattr(solver, 'ac9_layer_crossings'):
        raise ValueError('AC9 Native 3 does not support Piece Layer recovery')
    from native_three_automatic import AutomaticThreeBatchPlan
    from native_experimental_packed import solve_elasticity_packed
    from native_experimental_small_tiles import solve_elasticity_width8
    from native_experimental_sparse_contacts import SparseContacts
    NATIVE_COLOR_PLAN = AutomaticThreeBatchPlan(model, getattr(solver, 'ac9_bound_constraints', None), validate_materials=False)
    NATIVE_COLOR_PLAN.install()
    solver.ac9_elasticity_packed = solve_elasticity_packed
    solver.ac9_elasticity_narrow = solve_elasticity_width8
    solver.ac9_native_elastic_widths = tuple(8 if group.size >= 2048 else 16 for group in model.particle_color_groups)
    solver.ac9_self_contact_queue = SparseContacts(solver)
    solver.ac9_self_contact_queue.enable_planar = True
    log['native_three'] = dict(NATIVE_COLOR_PLAN.summary, applied=True, experimental=True)
if True:
    # the patched particle kernels must be the ones VBD loaded (Newton imports them with the solver), in every run
    _km = sys.modules.get(_KMOD)
    log['newton_patch_kernels'] = getattr(_km, '__file__', None)
    if MEMBRANE_BW:
        log['membrane']['kernels'] = log['newton_patch_kernels']
    if _km is None or os.path.normcase(os.path.abspath(_km.__file__)) != os.path.normcase(_KPATCH):
        sys.exit(f"the add-on's patched kernels did not load ({log['newton_patch_kernels']})")
    _sm = sys.modules.get('newton._src.solvers.vbd.solver_vbd')
    if _sm is None or os.path.normcase(os.path.abspath(_sm.__file__)) !=             os.path.normcase(os.path.join(os.path.dirname(_KPATCH), 'solver_vbd.py')):
        sys.exit(f"the patched solver_vbd did not load ({getattr(_sm, '__file__', None)})")
# cfg 'layers' (2026-10-25, DECISIONS on layers 2026-10-25; Fable REVIEW_fable_layer_redesign_2026-10-25 case A):
# {piece name: integer}, larger = further out, absent = 0. The cloth-cloth contact between two pieces
# of different layers gets a side: a vertex belongs on the outer side of a lower layer's triangle and on the inner
# side of a higher one's (outward = the winding normal with the sign it has towards the nearest body point when the
# stage starts, per triangle). A vertex on the wrong side is pushed back through and the solver's planar truncation
# lets that pair through (solver/newton_patch, ac9_layer_*). The contact distance is the usual one, so switching the
# layers off (a person setting them back to 0) does not move the cloth. Log: vertices marked on a wrong
# side per frame (at the frame's last collision pass).
LAYERS = None
if cfg.get('layers') and cfg.get('self_contact', True) and pat_names is not None:
    _labels = sorted({int(cfg['layers'].get(n_, 0)) for n_ in pat_names})
    _rank = {k_: i_ for i_, k_ in enumerate(_labels)}
    _lay_full = np.zeros(N_FULL, np.int32)
    for i_, n_ in enumerate(pat_names):
        _lay_full[v_piece == i_] = _rank[int(cfg['layers'].get(n_, 0))]
    _lay = _lay_full[sel]
    # the cloth's vertices come first; particles added after them (the seams' Pull Round targets, seam_paths: no
    # mass, no radius) touch nothing and get no side (2026-11-01: they stopped every run with layers and Round)
    if len(_lay) > model.particle_count:
        sys.exit(f"layers: {len(_lay)} layer ids for {model.particle_count} particles")
    if len(np.unique(_lay)) > 1:
        from spatial import cKDTree as _KDl
        _tl = model.tri_indices.numpy().astype(np.int64)
        _xl = np.asarray(X0, np.float64)
        _A, _B, _C = _xl[_tl[:, 0]], _xl[_tl[:, 1]], _xl[_tl[:, 2]]
        _cen = (_A + _B + _C) / 3.0
        _, _bi = _KDl(np.asarray(Bv, np.float64)).query(_cen)
        _tsg = np.where(((_cen - np.asarray(Bv, np.float64)[_bi]) * np.cross(_B - _A, _C - _A)).sum(1) >= 0, 1.0, -1.0)
        # the outward sign is per triangle (the nearest body point): under the arms, at the crotch and on a turned-back
        # collar a piece can split; the triangles against their piece's majority, logged (design review 2026-10-25)
        _tp = np.asarray(v_piece[sel], np.int64)[_tl[:, 0]]
        _minor = {}
        for _p in np.unique(_tp):
            _m = _tp == _p
            _n = int(min((_tsg[_m] > 0).sum(), (_tsg[_m] < 0).sum()))
            if _n:
                _minor[pat_names[int(_p)]] = [_n, int(_m.sum())]
        # left out: the vertices within layers_seam_rings rings (default 3) of a stitch joining two layers (a seam or
        # a button between a layer-1 placket and a layer-0 front): the stitch pulls them through each other's side,
        # so a side there held them wrong for good (a shirt's placket: 40 stuck vertices, 24 of 47 on a stitch,
        # crossings 23 -> 105, experiments/layer_contact_2026-10-25). They keep the usual two-sided contact
        # (negative layer id), and the triangles touching them have no side.
        _xs = np.asarray(E, np.int64)
        _xs = _xs[_lay[_xs[:, 0]] != _lay[_xs[:, 1]]] if len(_xs) else _xs
        _ex = np.zeros(len(_lay), bool)
        if len(_xs):
            _ex[_xs.ravel()] = True
            for _ in range(int(cfg.get('layers_seam_rings', 3))):
                _ex[_tl[_ex[_tl].any(1)].ravel()] = True
        if cfg.get('layers_pair_exclusions', BOUND_COUPLING):
            import layer_pairs as _LP
            _pair_cfg = cfg.get('layers_pair_exclusions', True)
            _radius = float(_pair_cfg.get('radius_mm', 1.0)) if isinstance(_pair_cfg, dict) else 1.0
            _starts, _mates, _pair_log = _LP.build(d_flat_full[sel], np.asarray(v_piece[sel]), _lay, E, _radius)
            _pairs = _km.AC9LayerPairs()
            _pairs.flat = wp.array(np.asarray(d_flat_full[sel], np.float32), dtype=wp.vec2)
            _pairs.piece = wp.array(np.asarray(v_piece[sel], np.int32), dtype=int)
            _pairs.starts = wp.array(_starts, dtype=int)
            _pairs.mates = wp.array(_mates, dtype=int)
            _edge_tri_table = {}
            for _ti_pair, _tri_pair in enumerate(model.tri_indices.numpy()):
                for _a_pair, _b_pair in ((_tri_pair[0], _tri_pair[1]), (_tri_pair[1], _tri_pair[2]), (_tri_pair[2], _tri_pair[0])):
                    _edge_tri_table.setdefault(tuple(sorted((int(_a_pair), int(_b_pair)))), _ti_pair)
            _pair_edge_tri = [_edge_tri_table.get(tuple(sorted(map(int, _ep))), -1)
                              for _ep in model.edge_indices.numpy()[:, 2:4]]
            _pairs.edge_tri = wp.array(np.asarray(_pair_edge_tri, np.int32), dtype=int)
            _pairs.radius_mm = _radius
            solver.ac9_layer_pairs = _pairs
            log['layer_pair_exclusions'] = _pair_log
        else:
            _tsg[_ex[_tl].any(1)] = 0.0
            _lay = np.where(_ex, -1 - _lay, _lay)
        solver.ac9_layer = wp.array(np.r_[_lay, -np.ones(model.particle_count - len(_lay), np.int64)].astype(np.int32),
                                    dtype=int)
        solver.ac9_tri_sign = wp.array(_tsg.astype(np.float32), dtype=float)
        solver.ac9_wrong = wp.zeros(model.particle_count, dtype=int)
        _recovery_mm = float(cfg.get('layers_recovery_mm', 30.0)) if len(_labels) > 1 else 0.0
        if _recovery_mm > 0:
            solver.ac9_layer_recovery_reach = _recovery_mm * 0.001
            solver.ac9_layer_recovery_body = wp.Mesh(points=wp.array(Bv, dtype=wp.vec3),
                                                     indices=wp.array(Bt, dtype=int))
            solver.ac9_layer_recovery_stats = wp.zeros((model.particle_count, 4), dtype=int)
            solver.ac9_layer_recovery_distances = wp.zeros((model.particle_count, len(_labels)), dtype=float)
            solver.ac9_layer_recovery_triangles = wp.zeros((model.particle_count, len(_labels)), dtype=int)
            solver.ac9_layer_recovery_mesh_ids = wp.zeros(0, dtype=wp.uint64)
            solver.ac9_layer_recovery_tri_map = wp.zeros(0, dtype=int)
            solver.ac9_layer_recovery_tri_offsets = wp.zeros(0, dtype=int)
            # A per-rank nearest-point BVH preserves the closest-face rule without enumerating
            # every triangle in a 30 mm box. Multi-world models keep the filtered legacy query.
            if cfg.get('layers_nearest_bvh', True) and len(np.unique(model.particle_world.numpy())) == 1:
                solver.ac9_layer_query_points = wp.clone(model.particle_q)
                solver.ac9_layer_query_meshes = []
                maps_ = []
                ids_ = []
                offsets_ = [0]
                for rank_ in range(len(_labels)):
                    tris_ = np.nonzero(_lay[_tl[:, 0]] == rank_)[0]
                    maps_.extend(tris_.tolist())
                    offsets_.append(len(maps_))
                    if len(tris_):
                        mesh_ = wp.Mesh(points=solver.ac9_layer_query_points, indices=wp.array(_tl[tris_].reshape(-1).astype(np.int32), dtype=int))
                        solver.ac9_layer_query_meshes.append(mesh_)
                        ids_.append(mesh_.id)
                    else:
                        ids_.append(0)
                solver.ac9_layer_recovery_mesh_ids = wp.array(np.asarray(ids_, np.uint64), dtype=wp.uint64)
                solver.ac9_layer_recovery_tri_map = wp.array(np.asarray(maps_, np.int32), dtype=int)
                solver.ac9_layer_recovery_tri_offsets = wp.array(np.asarray(offsets_, np.int32), dtype=int)
            if cfg.get('layers_crossings', True):
                import layer_crossings as _LC
                _cross_ke_scale = float(cfg.get('layers_crossing_ke_scale', 1.0))
                if not np.isfinite(_cross_ke_scale) or _cross_ke_scale <= 0.0:
                    raise ValueError('layers_crossing_ke_scale must be finite and positive')
                solver.ac9_layer_crossing_ke_scale = _cross_ke_scale
                _LC.setup(solver, model.tri_indices.numpy(), solver._ac9_layer_arrays()[3],
                          cached=bool(cfg.get('layers_crossing_cache', True)))
                solver.ac9_layer_crossing_module = _LC
        LAYERS = {'wrong': []}
        log['layers'] = {'pieces': {str(n_): int(k_) for n_, k_ in cfg['layers'].items() if n_ in pat_names},
                         'verts_per_layer': {_labels[int(k_)]: int((_lay == k_).sum()) for k_ in np.unique(_lay) if k_ >= 0},
                         'left_out_by_stitches': int(_ex.sum()), 'stitches_across': int(len(_xs)),
                         'sign_minority': _minor,
                         'wrong_per_frame': LAYERS['wrong']}
        log['layers']['recovery_friction_scale'] = float(cfg.get('layers_recovery_friction', 1.0))
    else:
        log['layers'] = {'off': 'every simulated piece is on the same layer'}
if cfg.get('init_xv'):
    # cfg 'init_xv': every substep's iterations start from x + v dt instead of Newton's free-fall guess
    # x + v dt + g dt^2. With the free-fall guess and 10-20 iterations the solve stops short of the equilibrium: on a
    # cantilever strip the bending came out at ~2 % of edge_ke and a 30 N/m pull stretched 2-60 % (0.375 % expected),
    # both depending on the mesh size; from x + v dt the strip matches an independent equilibrium within 1-3 deg and
    # the pull 0.38 % on 3/6/12 mm meshes, same wall time (foundation_2026-09-30/RESULT_G1). The displacement is
    # truncated again for self contact (the solver's own penetration-free step)
    @wp.kernel
    def _xv_disp(v: wp.array[wp.vec3], im: wp.array[float], dt_: float, disp: wp.array[wp.vec3]):
        i = wp.tid()
        d_ = wp.vec3(0.0, 0.0, 0.0)
        if im[i] > 0.0:
            d_ = v[i] * dt_
        disp[i] = d_
    _init_orig = solver._initialize_particles

    def _init_xv(state_in, state_out, dt_):
        _init_orig(state_in, state_out, dt_)
        wp.launch(_xv_disp, dim=model.particle_count,
                  inputs=[state_in.particle_qd, model.particle_inv_mass, dt_, solver.particle_displacements])
        solver._penetration_free_truncation(state_in.particle_q)
    solver._initialize_particles = _init_xv
    log['init_xv'] = True
# cfg 'stitch_gate_mm' (test option, default off): a stitch's contact exemption (above) only once its two vertices
# are closer than this; before that the two pieces collide like any others. The exemption is there so the contact
# does not hold a closing seam open, but while the seam is still being drawn in it let the seam allowances pass
# through each other: 94 % of a pants' new crossings were exempt pairs, born while the seams closed (frames 0-50 of
# the stage, 2026-10-01). The solver's filter rows are rewritten in place (same arrays, so the CUDA graph sees it),
# ascending with INT_MAX after the active entries (the lookup is a binary search with a first/last fast-fail).
GATE = None
if cfg.get('stitch_gate_mm') and vfilter:
    _det = solver.trimesh_collision_detector
    _offs = _det.vertex_triangle_filtering_list_offsets.numpy()
    _lst0 = _det.vertex_triangle_filtering_list.numpy().copy()
    # entries kept always: what the row holds that no stitch put there (Newton's topological filter)
    _ext = {x: s_ for x, s_ in vfilter.items()}
    _fixed = {}
    for x in _ext:
        row = set(_lst0[_offs[x]:_offs[x + 1]].tolist())
        _fixed[x] = row - _ext[x]
    GATE = {'offs': _offs, 'fixed': _fixed, 'on': np.zeros(len(E), bool), 'mm': float(cfg['stitch_gate_mm']),
            'list': _lst0, 'log': []}

    def _gate_update(q_):
        gp = np.linalg.norm(q_[E[:, 0]] - q_[E[:, 1]], axis=1) * 1000.0
        new = (~GATE['on']) & (gp < GATE['mm'])
        if not new.any() and GATE['log']:
            return 0
        GATE['on'] |= new
        rows = {}
        for k_ in np.nonzero(GATE['on'])[0]:
            for x, tr in GATE_ROWS[k_]:
                rows.setdefault(x, set()).update(tr)
        L_ = GATE['list']
        for x in _ext:
            a_, b_ = GATE['offs'][x], GATE['offs'][x + 1]
            vals = sorted(GATE['fixed'][x] | rows.get(x, set()))
            L_[a_:b_] = np.array(vals + [2 ** 31 - 1] * (b_ - a_ - len(vals)), np.int32)
        _det.vertex_triangle_filtering_list.assign(L_)
        GATE['log'].append(int(GATE['on'].sum()))
        return int(new.sum())
    _gate_update(X0.astype(np.float64))
    log['stitch_gate'] = {'mm': GATE['mm'], 'on_at_start': int(GATE['on'].sum()), 'stitches': int(len(E))}
s0, s1 = model.state(), model.state()
control = model.control()
# cfg 'layer_order': [{"name", "lower": [full vertex index], "upper": [full vertex index], "gap_mm", "reach_mm",
# "mode": "contact" | "push", "free_contact"}] -- a person's "lower lies under upper" (layer; the user,
# 2026-10-01: also for putting a shirt under a jacket already on). The contact between cloth layers is symmetric and
# keeps whatever order the layers first met in (RESULT_G3 26-28).
#  mode "contact" (default, Fable r17): every frame each lower vertex whose nearest point on the upper layer lies
#  inside an upper triangle within reach_mm gets a one-sided body contact (the body contact's own penalty, inside
#  the solver's iterations): a wall at that point gap_mm in from the upper layer, normal pointing in; each upper
#  vertex likewise against the lower layer, normal pointing out. The wall moves with the upper point's velocity.
#  mode "push": the first trial, a frame-end move of the lower vertices (tangled the cloth: crossings 5 -> 121-233).
#  Outward = the winding normal with the sign it had towards the body when the stage started (per triangle; a flap
#  folded at placement keeps its own sign). Vertices stitched across the two layers, and their one-ring, are left out.
LAYER = []
LAYER_K = 0
if cfg.get('layer_order'):
    from spatial import cKDTree
    _BODY_KD = cKDTree(np.asarray(Bv, float))
    _loc_ = (lambda ix: remap[np.asarray(ix, np.int64)]) if SUBSET else (lambda ix: np.asarray(ix, np.int64))
    _ring = [set() for _ in range(len(X0))]
    for i_, j_, k_ in F:
        _ring[i_].update((j_, k_)); _ring[j_].update((i_, k_)); _ring[k_].update((i_, j_))
    _X0d = X0.astype(np.float64)
    for g in cfg['layer_order']:
        ms_ = []
        for key_ in ('lower', 'upper'):
            m_ = np.zeros(len(X0), bool); ix_ = _loc_(g[key_]); m_[ix_[ix_ >= 0]] = True; ms_.append(m_)
        lo_m, up_m = ms_[0] & ~ms_[1], ms_[1]
        drop = set()
        for a_, b_ in E:
            if (lo_m[a_] and up_m[b_]) or (lo_m[b_] and up_m[a_]):
                for v_ in (int(a_), int(b_)):
                    drop.add(v_); drop.update(_ring[v_])
        dm = np.zeros(len(X0), bool); dm[list(drop)] = True
        sides = []
        for vm_, tm_, sgn_ in ((lo_m, up_m, -1.0), (up_m, lo_m, 1.0)):
            T_ = F[tm_[F].all(1)].astype(np.int64)
            vs_ = np.nonzero(vm_ & ~dm)[0]
            if not len(T_) or not len(vs_):
                continue
            A_, B_, C_ = _X0d[T_[:, 0]], _X0d[T_[:, 1]], _X0d[T_[:, 2]]
            cen = (A_ + B_ + C_) / 3.0; n_ = np.cross(B_ - A_, C_ - A_)
            _, bi = _BODY_KD.query(cen)
            tsg = np.where(((cen - _BODY_KD.data[bi]) * n_).sum(1) >= 0, 1.0, -1.0)
            sides.append((vs_, T_, tsg, sgn_))
        if g.get('mode', 'contact') == 'push' or g.get('lower_only'):
            sides = [x_ for x_ in sides if x_[3] < 0]
        LAYER.append({'name': g.get('name', ''), 'mode': g.get('mode', 'contact'), 'sides': sides,
                      'gap': float(g.get('gap_mm', 0.5)) / 1000.0, 'reach': float(g.get('reach_mm', 30.0)) / 1000.0,
                      'step': float(g.get('step_mm', 1.0)) / 1000.0, 'dropped': int(dm.sum())})
        if g.get('mode', 'contact') == 'contact':
            LAYER_K += sum(len(sd_[0]) for sd_ in sides)
    log['layer_order'] = {L_['name']: {'mode': L_['mode'], 'verts': [int(len(sd_[0])) for sd_ in L_['sides']],
                                       'dropped_stitched': L_['dropped'], 'contacts': 0} for L_ in LAYER}


def _layer_sides(q, vs_, T_, tsg, qd=None):
    """for each vertex: signed distance (m, + = on the triangle's outer side) to its nearest triangle of T_ (nearest
    point inside the triangle), that triangle's outward normal, the nearest point, its velocity, ok, |distance|"""
    A_, B_, C_ = q[T_[:, 0]], q[T_[:, 1]], q[T_[:, 2]]
    cen = (A_ + B_ + C_) / 3.0
    n_ = np.cross(B_ - A_, C_ - A_); n_ /= np.linalg.norm(n_, axis=1, keepdims=True) + 1e-18
    n_ *= tsg[:, None]
    _, cand = cKDTree(cen).query(q[vs_], k=min(16, len(T_)))
    cand = cand.reshape(len(vs_), -1)
    m = len(vs_)
    sd = np.zeros(m); nn = np.zeros((m, 3)); cp = np.zeros((m, 3)); cv = np.zeros((m, 3))
    ok = np.zeros(m, bool); best = np.full(m, np.inf)
    p_ = q[vs_]
    for c_ in range(cand.shape[1]):
        t_ = cand[:, c_]
        a_, b_, cc_ = A_[t_], B_[t_], C_[t_]
        nt = n_[t_]; h = ((p_ - a_) * nt).sum(1)
        pr = p_ - h[:, None] * nt
        v0, v1, v2 = b_ - a_, cc_ - a_, pr - a_
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        den = d00 * d11 - d01 * d01 + 1e-30
        w1 = (d11 * d20 - d01 * d21) / den; w2 = (d00 * d21 - d01 * d20) / den; w0 = 1.0 - w1 - w2
        better = (w0 >= 0) & (w1 >= 0) & (w2 >= 0) & (np.abs(h) < best)
        best[better] = np.abs(h[better]); sd[better] = h[better]; nn[better] = nt[better]; cp[better] = pr[better]
        ok[better] = True
        if qd is not None:
            tri = T_[t_]
            vel = w0[:, None] * qd[tri[:, 0]] + w1[:, None] * qd[tri[:, 1]] + w2[:, None] * qd[tri[:, 2]]
            cv[better] = vel[better]
    return sd, nn, cp, cv, ok, best


if LAYER_K:
    # synthetic one-sided contacts, appended to the body contacts after every collision pass (inside the graph)
    @wp.kernel
    def _append_layer_contacts(n_syn: wp.array(dtype=int), sp: wp.array(dtype=int), spos: wp.array(dtype=wp.vec3),
                               svel: wp.array(dtype=wp.vec3), snrm: wp.array(dtype=wp.vec3), shape: int, cmax: int,
                               count: wp.array(dtype=int), c_idx: wp.array(dtype=wp.vec3i), c_par: wp.array(dtype=int),
                               c_bary: wp.array(dtype=wp.vec3), c_shape: wp.array(dtype=int),
                               c_pos: wp.array(dtype=wp.vec3), c_vel: wp.array(dtype=wp.vec3),
                               c_nrm: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        if i >= n_syn[0]:
            return
        k = wp.atomic_add(count, 0, 1)
        if k >= cmax:
            return
        c_idx[k] = wp.vec3i(sp[i], -1, -1)
        c_par[k] = sp[i]
        c_bary[k] = wp.vec3(1.0, 0.0, 0.0)
        c_shape[k] = shape
        c_pos[k] = spos[i]
        c_vel[k] = svel[i]
        c_nrm[k] = snrm[i]
    SYN_N = wp.zeros(1, dtype=int); SYN_P = wp.zeros(LAYER_K, dtype=int)
    SYN_POS = wp.zeros(LAYER_K, dtype=wp.vec3); SYN_VEL = wp.zeros(LAYER_K, dtype=wp.vec3)
    SYN_NRM = wp.zeros(LAYER_K, dtype=wp.vec3)
# cfg 'deep_push' (10/18 recovery bench, test only): {"cap_mm": 5, "max_mm": 150}. The body's contacts are found only
# within body_gap of its surface, so a vertex deeper inside (a sleeve swung through the torso, 40-50 mm in) is never
# pushed out and stays for good. Every substep, each vertex inside the body (winding number) and deeper than the gap
# gets one more body contact at its closest surface point, normal outwards; the penetration it sees is capped at
# cap_mm (the contact point moved inwards), so the push is that of a cap_mm contact until it is out.
DEEP = None
_deep_n = model.particle_count if cfg.get('deep_push') else 0
pipe = newton.CollisionPipeline(model, soft_contact_gap=cfg.get('body_gap', 0.003),
                               **({'soft_contact_max': 2 * len(X0) + LAYER_K + _deep_n} if (LAYER_K or _deep_n) else {}))
contacts = pipe.contacts()
if _deep_n:
    @wp.kernel
    def _deep_push(mesh: wp.uint64, q: wp.array(dtype=wp.vec3), gap: float, cap: float, maxd: float, shape: int,
                   cmax: int, count: wp.array(dtype=int), c_idx: wp.array(dtype=wp.vec3i), c_par: wp.array(dtype=int),
                   c_bary: wp.array(dtype=wp.vec3), c_shape: wp.array(dtype=int), c_pos: wp.array(dtype=wp.vec3),
                   c_vel: wp.array(dtype=wp.vec3), c_nrm: wp.array(dtype=wp.vec3), n_deep: wp.array(dtype=int),
                   inside: wp.array(dtype=int)):
        i = wp.tid()
        inside[i] = 0
        p = q[i]
        r = wp.mesh_query_point_sign_winding_number(mesh, p, maxd, 2.0, 0.5)
        if not r.result:
            return
        if r.sign >= 0.0:
            return
        cp = wp.mesh_eval_position(mesh, r.face, r.u, r.v)
        d = wp.length(cp - p)
        if d <= gap:
            return
        inside[i] = 1
        n = (cp - p) / d
        # the avatar's arm and torso are separate overlapping shells: the closest surface may be buried in the other
        # shell. March on along n in 3 mm steps until a point is outside the body (winding number), up to maxd.
        t = d
        while t < maxd:
            r2 = wp.mesh_query_point_sign_winding_number(mesh, p + n * (t + 0.001), maxd, 2.0, 0.5)
            if (not r2.result) or r2.sign >= 0.0:
                break
            t = t + 0.003
        cp = p + n * t
        d = t
        bx = cp
        if d > cap:
            bx = cp - n * (d - cap)
        k = wp.atomic_add(count, 0, 1)
        if k >= cmax:
            return
        wp.atomic_add(n_deep, 0, 1)
        c_idx[k] = wp.vec3i(i, -1, -1)
        c_par[k] = i
        c_bary[k] = wp.vec3(1.0, 0.0, 0.0)
        c_shape[k] = shape
        c_pos[k] = bx
        c_vel[k] = wp.vec3(0.0, 0.0, 0.0)
        c_nrm[k] = n
    # the body's own contacts of a vertex found inside (the arm and torso shells overlap, so an inner shell's surface
    # pushes it further in against the deep push): moved 1 m behind their surface, i.e. no force, when 'own_off'
    @wp.kernel
    def _deep_count0(count: wp.array(dtype=int), c0: wp.array(dtype=int)):
        c0[0] = count[0]

    @wp.kernel
    def _deep_own_off(c0: wp.array(dtype=int), inside: wp.array(dtype=int), c_par: wp.array(dtype=int),
                      c_pos: wp.array(dtype=wp.vec3), c_nrm: wp.array(dtype=wp.vec3)):
        k = wp.tid()
        if k >= c0[0]:
            return
        if inside[c_par[k]] != 0:
            c_pos[k] = c_pos[k] - c_nrm[k] * 1.0
    _dp = cfg['deep_push'] if isinstance(cfg['deep_push'], dict) else {}
    _bm = wp.Mesh(points=wp.array(Bv, dtype=wp.vec3), indices=wp.array(Bt, dtype=int))
    DEEP = {'mesh': _bm, 'k': _deep_push, 'gap': float(cfg.get('body_gap', 0.003)), 'cap': float(_dp.get('cap_mm', 5)) / 1000,
            'maxd': float(_dp.get('max_mm', 150)) / 1000, 'n': wp.zeros(1, dtype=int), 'rows': [],
            'inside': wp.zeros(_deep_n, dtype=int), 'c0': wp.zeros(1, dtype=int), 'own_off': bool(_dp.get('own_off', False)),
            'k0': _deep_count0, 'koff': _deep_own_off}
    log['deep_push'] = {'cap_mm': DEEP['cap'] * 1000, 'max_mm': DEEP['maxd'] * 1000}
fps, sub = cfg.get('fps', 60), cfg.get('substeps', 10)
dt = 1.0 / fps / sub
# before sewn (PRESEWN): the product Live's substeps. The explicit forces (grab, smooth brush, attach) run only in
# those Native substeps (the correction carries them after sewn), so their dt^2 caps use DT_LIVE.
SUB_LIVE = 10 if PRESEWN else sub
DT_LIVE = 1.0 / fps / SUB_LIVE
DT_EXPLICIT = DT_LIVE if PRESEWN else dt
PRESEWN_STATE = {'fast': None, 'frames_live': 0}


# cfg 'diag' (test option, default off): what each vertex is held back from and pulled by, for spotting cloth that
# keeps pushing against something it cannot get past (user 2026-10-06: "the force that wants to go one way but
# cannot", not the motion that results). Per substep, the penetration-free truncation keeps a factor t per vertex
# (1 = moved as solved, 0 = not at all; it scales the step without turning it): accumulated are the substeps held
# back (t < 0.99) and the part of the step taken away, step * (1 / t - 1) (the direction it wanted). Per frame, on
# the CPU, the stitch springs' pull per vertex. Written to <out>/diag.npz (float16 per frame) with per-piece sums in
# the run log.
DIAG = None
if cfg.get('diag') and hasattr(solver, 'truncation_ts') and hasattr(solver, 'particle_displacements'):
    @wp.kernel
    def _diag_accumulate(ts: wp.array(dtype=float), disp: wp.array(dtype=wp.vec3),
                         held: wp.array(dtype=float), want: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        t = ts[i]
        if t < 0.99:
            held[i] = held[i] + 1.0
            want[i] = want[i] + disp[i] * (1.0 / wp.max(t, 0.05) - 1.0)
    DIAG = {'held': wp.zeros(model.particle_count, dtype=float), 'want': wp.zeros(model.particle_count, dtype=wp.vec3),
            'kernel': _diag_accumulate, 'rows_held': [], 'rows_want': [], 'rows_pull': [], 'piece_log': []}


# cfg 'attach' (2026-10-18, core/attach.py): garment lines pulled to tapes on the body.
# {"file": npz (verts (n,) full-garment indices, group (n,), w (n,) m of line per vertex, pts (k, 3) m the tapes
# lifted off the body, seg0 / seg1 (g,) each tape's points, closed (g,)), "groups": [the groups on in this stage],
# "pull_N_m", "stiff_N_m2"}. An external force on each vertex every substep, towards the nearest point of its tape
# (an open tape's end segments extended, so nothing is pulled along it past an end): stiff * w * distance, capped at
# pull * w. Not springs to massless particles: those are fixed obstacles to the cloth's self contact (anim_follow,
# 2026-10-17). The stiffness is also held under mass_frac (0.25) x m / dt^2 per vertex: an explicit force, constant
# over the substep, overshoots its target at 2.
ATTACH = None
if cfg.get('attach'):
    _ac = cfg['attach']
    _az = np.load(_ac['file'])
    _loc = remap if SUBSET else np.arange(N_FULL)
    _av = _loc[_az['verts']]
    _on = np.isin(_az['group'], np.asarray(_ac.get('groups', np.unique(_az['group'])), np.int64)) & (_av >= 0)
    _av, _ag, _aw = _av[_on], _az['group'][_on].astype(np.int32), _az['w'][_on].astype(np.float64)
    if len(_av):
        _m = model.particle_mass.numpy()[_av].astype(np.float64)
        _kmax = float(_ac.get('mass_frac', 0.25)) * _m / (DT_EXPLICIT if (os.environ.get('AC9_TEST_FAST_E5', '1') != '0') else dt) ** 2
        _kv = np.minimum(float(_ac.get('stiff_N_m2', 200.0)) * _aw, _kmax)
        _cap = float(_ac.get('pull_N_m', 2.0)) * _aw
        # Accurate (test switch AC9_TEST_FAST_E5): the strength in N/m set for the product's 10 substeps, like the grab
        # spring, solved implicitly by the correction (the explicit kernel is skipped in corrected substeps)
        _kp10 = np.minimum(float(_ac.get('stiff_N_m2', 200.0)) * _aw, float(_ac.get('mass_frac', 0.25)) * _m * (fps * 10) ** 2)
        ATTACH = {'v': _av, 'g': _ag, 'pts': _az['pts'].astype(np.float64), 's0': _az['seg0'], 's1': _az['seg1'], 'kp10': _kp10, 'cap': _cap,
                  'cl': _az['closed'],
                  'wv': wp.array(_av.astype(np.int32), dtype=int), 'wg': wp.array(_ag, dtype=int),
                  'wk': wp.array(_kv.astype(np.float32), dtype=float), 'wc': wp.array(_cap.astype(np.float32), dtype=float),
                  'wp': wp.array(_az['pts'].astype(np.float32), dtype=wp.vec3),
                  'ws0': wp.array(_az['seg0'].astype(np.int32), dtype=int),
                  'ws1': wp.array(_az['seg1'].astype(np.int32), dtype=int),
                  'wcl': wp.array(_az['closed'].astype(np.int32), dtype=int)}
        log['attach'] = {'verts': int(len(_av)), 'groups': sorted(int(x) for x in np.unique(_ag)),
                         'pull_N_m': float(_ac.get('pull_N_m', 2.0)), 'stiff_N_m2': float(_ac.get('stiff_N_m2', 200.0)),
                         'stiff_held_by_mass': int((_kv < float(_ac.get('stiff_N_m2', 200.0)) * _aw - 1e-12).sum())}


@wp.kernel
def _attach_f(q: wp.array(dtype=wp.vec3), vs: wp.array(dtype=int), gs: wp.array(dtype=int),
              kv: wp.array(dtype=float), cap: wp.array(dtype=float), pts: wp.array(dtype=wp.vec3),
              s0: wp.array(dtype=int), s1: wp.array(dtype=int), cl: wp.array(dtype=int), f: wp.array(dtype=wp.vec3)):
    i = wp.tid()
    v = vs[i]
    g = gs[i]
    x = q[v]
    a = s0[g]
    n = s1[g] - a
    nseg = n - 1
    if cl[g] == 1:
        nseg = n
    best = float(1.0e30)
    bp = x
    for k in range(nseg):
        p0 = pts[a + k]
        p1 = pts[a + (k + 1) % n]
        e = p1 - p0
        t = wp.dot(x - p0, e) / wp.max(wp.dot(e, e), 1.0e-12)
        lo = float(0.0)
        hi = float(1.0)
        if cl[g] == 0 and k == 0:
            lo = -1.0e6
        if cl[g] == 0 and k == nseg - 1:
            hi = 1.0e6
        t = wp.clamp(t, lo, hi)
        p = p0 + t * e
        dd = wp.length(x - p)
        if dd < best:
            best = dd
            bp = p
    if best > 1.0e-9:
        fm = wp.min(kv[i] * best, cap[i])
        wp.atomic_add(f, v, (bp - x) * (fm / best))    # two attach rows can share a vertex


def _attach_dist(P_):
    """Each attached vertex's distance (m) to its tape at positions P_ (local), as the kernel measures it."""
    out = np.zeros(len(ATTACH['v']))
    for g_ in np.unique(ATTACH['g']):
        m_ = ATTACH['g'] == g_
        T_ = ATTACH['pts'][ATTACH['s0'][g_]:ATTACH['s1'][g_]]
        cl_ = bool(ATTACH['cl'][g_])
        A_ = T_ if cl_ else T_[:-1]
        B_ = np.roll(T_, -1, 0) if cl_ else T_[1:]
        X_ = P_[ATTACH['v'][m_]]
        e_ = B_ - A_
        t_ = np.einsum('vsk,sk->vs', X_[:, None, :] - A_[None], e_) / np.maximum((e_ * e_).sum(1), 1e-12)
        lo_ = np.zeros(len(A_)); hi_ = np.ones(len(A_))
        if not cl_:
            lo_[0], hi_[-1] = -1e6, 1e6
        t_ = np.clip(t_, lo_, hi_)
        out[m_] = np.linalg.norm(X_[:, None, :] - (A_[None] + t_[..., None] * e_[None]), axis=2).min(1)
    return out


@wp.kernel
def _fric_anchor_update(x: wp.array(dtype=wp.vec3), a: wp.array(dtype=wp.vec3), n: wp.array(dtype=wp.vec3),
                        hit: wp.array(dtype=int), eps: float, slid: wp.array(dtype=float)):
    # cfg 'fric_anchor', after each substep: a particle touching the body for the first time is anchored where it is;
    # an anchored one that touched moves its anchor only by its tangential slip beyond eps; one that left drops it
    i = wp.tid()
    h = hit[i]
    if h == 2:
        a[i] = x[i]
        hit[i] = 1
    elif h == 3:
        d = x[i] - a[i]
        dt_ = d - n[i] * wp.dot(n[i], d)
        L = wp.length(dt_)
        if L > eps:
            a[i] = a[i] + dt_ * (1.0 - eps / L)
            wp.atomic_add(slid, 0, L - eps)
        hit[i] = 1
    elif h == 1:
        hit[i] = 0


# cfg 'bend_fric2' (2026-10-18, user OK, DECISIONS cloth-sim-bend-hysteresis-stick-slip-2026-10-18; Fable
# REVIEW_fable_jitter_energy D-3): the fabric's bending hysteresis (KES 2HB) as a stick-slip friction element in
# parallel with each cloth hinge's elastic spring (k0, rest ra0): a spring ratio x k0 stuck at angle st, which slips
# once it would carry more than the friction moment k0 x theta_y, theta_y = kappa_y x the hinge's rest length
# (kappa_y = 2HB / 2B of the fabric, the fabric table's bend_hyst_per_m). The hinge then is one spring k0 (1 + ratio)
# with the rest angle (ra0 + ratio st) / (1 + ratio), rewritten after every substep on the GPU. It comes on once the
# seams are closed (the active pattern stitches' p90 under start_mm), from the shape then: on from the first frame it
# remembered the folds of the sewing. Cloth stops where real cloth stops, by a force law, not by a speed-proportional
# damping (that never decided where the T-shirt settled; experiments/jitter_where_2026-10-18: two runs stopped at
# f385 / f304 with front gap 28.3 / 28.4 mm, folds 3.67 / 3.79 %; the same set without it never stopped).
@wp.kernel
def _bf2_update(q: wp.array(dtype=wp.vec3), ei: wp.array(dtype=wp.vec4i), rows: wp.array(dtype=int),
                k0: wp.array(dtype=float), ra0: wp.array(dtype=float), thy: wp.array(dtype=float),
                st: wp.array(dtype=float), rat: float, init: int, on: wp.array(dtype=int),
                bp: wp.array2d(dtype=wp.float32), ra: wp.array(dtype=wp.float32), slip: wp.array(dtype=int),
                inside: int, bf: wp.array2d(dtype=float)):
    i = wp.tid()
    if on[0] == 0:
        return
    e = ei[i]
    x0 = q[e[0]]
    x1 = q[e[1]]
    x2 = q[e[2]]
    x3 = q[e[3]]
    n1 = wp.normalize(wp.cross(x2 - x0, x3 - x0))
    n2 = wp.normalize(wp.cross(x3 - x1, x2 - x1))
    ed = wp.normalize(x3 - x2)
    th = wp.atan2(wp.dot(wp.cross(n1, n2), ed), wp.dot(n1, n2))
    r = rows[i]
    if init == 1:
        st[i] = th
    lim = thy[i] / rat
    d = th - st[i]
    if wp.abs(d) > lim:
        st[i] = st[i] + wp.sign(d) * (wp.abs(d) - lim)
        wp.atomic_add(slip, 0, 1)
    if inside == 1:
        bf[r, 0] = st[i]
    else:
        bp[r, 0] = wp.float32(k0[i] * (1.0 + rat))
        ra[r] = wp.float32((ra0[i] + rat * st[i]) / (1.0 + rat))


BF2 = None
if cfg.get('bend_fric2') and model.edge_count and not MEMBRANE_BW:
    log['bend_fric2'] = {'off': "needs membrane 'bw' (as before 2026-10-19: it took effect only there)"}
elif cfg.get('bend_fric2') and model.edge_count:
    _b2 = cfg['bend_fric2'] if isinstance(cfg['bend_fric2'], dict) else {}
    _ei2 = model.edge_indices.numpy().astype(np.int64)
    _nse = log['seam_bend']['added'] if 'seam_bend' in log else 0
    _r2 = np.arange(len(_ei2) - _nse)                # the cloth hinges (the seam hinges come last), inner only
    _r2 = _r2[(_ei2[_r2, 0] >= 0) & (_ei2[_r2, 1] >= 0)]
    _kap = np.full(len(_r2), float(_b2.get('kappa_per_m', 41.0)))
    # each hinge's fabric: fabric_pieces' kappa_y (core/fabric.py; FP is empty with weld: then the garment's)
    _kp = {n_: float(f_['kappa_y']) for n_, f_ in FP.items() if f_.get('kappa_y') is not None}
    if _kp:
        _hpc2 = v_piece[sel][_ei2[_r2, 2]]
        for _n, _k in _kp.items():
            _kap[_hpc2 == pat_names.index(_n)] = _k
    _rat2 = float(_b2.get('ratio', 100.0))
    if len(_r2):
        BF2 = {'rows': wp.array(_r2.astype(np.int32), dtype=int),
               'ei': wp.array(_ei2[_r2].astype(np.int32), dtype=wp.vec4i),
               'k0': wp.zeros(len(_r2), dtype=float), 'ra0': wp.zeros(len(_r2), dtype=float),
               'thy': wp.array((_kap * model.edge_rest_length.numpy()[_r2]).astype(np.float32), dtype=float),
               'st': wp.zeros(len(_r2), dtype=float), 'slip': wp.zeros(1, dtype=int), 'on': wp.zeros(1, dtype=int),
               'rat': _rat2, 'mm': float(_b2.get('start_mm', 2.0)) / 1000.0, 'r_np': _r2, 'n': len(_r2),
               'closed': None, 'f_on': None, 'inside': int(_b2.get('solve', 'after') == 'inside'),
               'freeze': _b2.get('freeze_after')}
        if BF2['inside']:
            # solve 'inside' (Fable REVIEW_fable_bend_hyst_stop_2026-10-18 1): the friction element is evaluated in the
            # bending force with its moment capped (return mapping, newton_patch); this kernel only moves st after the
            # substep. Rows [st, theta_y, ratio] per edge, theta_y set when it comes on
            solver.ac9_bend_fric = wp.zeros((model.edge_count, 3), dtype=float)
    log['bend_fric2'] = {'kappa_per_m': float(_b2.get('kappa_per_m', 41.0)), 'kappa_pieces': _kp or None,
                         'ratio': _rat2, 'start_mm': float(_b2.get('start_mm', 2.0)), 'hinges': int(len(_r2)),
                         'solve': _b2.get('solve', 'after'),
                         'theta_y_rad_p50': round(float(np.median(_kap * model.edge_rest_length.numpy()[_r2])), 4)
                         if len(_r2) else None}


def _bf2_launch(qarr, init):
    wp.launch(_bf2_update, dim=BF2['n'], inputs=[qarr, BF2['ei'], BF2['rows'], BF2['k0'], BF2['ra0'], BF2['thy'],
              BF2['st'], BF2['rat'], init, BF2['on'], model.edge_bending_properties, model.edge_rest_angle,
              BF2['slip'], BF2['inside'], solver.ac9_bend_fric])


# cfg 'untangle_free' (10/18 recovery bench, Fable REVIEW_fable_tangle_recovery_2026-10-18 Q2; test option, default
# off): {"rings": 2, "seams": true, "idle": 3}. Newton's self contact pushes a vertex away from its closest point
# whichever side it is on, and the penetration-free truncation keeps it there, so cloth that has gone through cloth is
# held through for good. At the start of a frame the edges of one piece passing through triangles of another (edges
# with a stitched vertex left out) and those triangles' vertices, grown by `rings` edges, plus (with deep_push) the
# vertices inside the body, are marked; right after each self-contact detection the marked vertices' vertex-triangle
# and edge-edge contacts are dropped and their truncation distances opened, so the stitches and the deep push can
# carry them back through. 'seams': the stitched vertices may be marked too. The search is on the CPU: while nothing
# crosses it runs every `idle` frames. Ported from experiments/chest_s3_2026-10-17/solver_copy (buried-features 10/19).
#
# The grab through (Live, Ctrl + drag with Shift held, user 2026-10-25, Fable REVIEW_fable_grab_through_2026-10-25):
# untangle_free's freeing is also made in every Live run (an empty mask writes nothing, so a run without a grab
# through is unchanged). While a grab is marked "through", (G) the grabbed vertices and one ring lose every contact
# with the rest of the cloth, both ways: the vertices of other pieces were pushed by the grabbed triangles and carried
# with them; (X) the crossings between the grabbed piece and the others within THROUGH_D of the grab, seams in, grown
# by two rings, are freed as untangle_free does, so the cloth around the hand can follow it back through. After the
# release (or Shift let go) X stays freed while it still crosses, at most THROUGH_K frames.
FREE = None
_through_on = bool(cfg.get('live')) or any(g_.get('through') for g_ in cfg.get('grab_script', []))
if (cfg.get('untangle_free') or _through_on) and cfg.get('self_contact', True):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from crossings import crossing_edges as _fx_cross
    _uf = cfg['untangle_free'] if isinstance(cfg.get('untangle_free'), dict) else {}
    _ft = model.tri_indices.numpy().astype(np.int64); _fe = model.edge_indices.numpy()[:, 2:4].astype(np.int64)
    _fvp = np.asarray(v_piece[sel], np.int64)
    _fsewn = np.zeros(model.particle_count, bool); _fsewn[np.asarray(E, np.int64).ravel()] = True
    _fEall = np.unique(np.sort(np.concatenate([_ft[:, [0, 1]], _ft[:, [1, 2]], _ft[:, [2, 0]]]), 1), axis=0)
    _fE = _fEall[~(_fsewn[_fEall[:, 0]] | _fsewn[_fEall[:, 1]])]

    @wp.kernel
    def _free_pairs(pair: wp.array(dtype=int), tri: wp.array2d(dtype=int), edges: wp.array2d(dtype=int),
                    nv: int, v_cnt: wp.array(dtype=wp.int32), v_size: wp.array(dtype=wp.int32),
                    v_off: wp.array(dtype=wp.int32), v_buf: wp.array(dtype=wp.int32),
                    e_cnt: wp.array(dtype=wp.int32), e_size: wp.array(dtype=wp.int32),
                    e_off: wp.array(dtype=wp.int32), e_buf: wp.array(dtype=wp.int32)):
        # every vertex-triangle and edge-edge contact that has a grabbed (G) vertex on either side is dropped
        i = wp.tid()
        if i < nv and i < v_cnt.shape[0]:
            n = wp.min(v_cnt[i], v_size[i])
            for k in range(n):
                j = (v_off[i] + k) * 2 + 1
                t = v_buf[j]
                if t >= 0:
                    if pair[i] != 0 or pair[tri[t, 0]] != 0 or pair[tri[t, 1]] != 0 or pair[tri[t, 2]] != 0:
                        v_buf[j] = -1
        if i < edges.shape[0] and i < e_cnt.shape[0]:
            mine = pair[edges[i, 0]] != 0 or pair[edges[i, 1]] != 0
            n = wp.min(e_cnt[i], e_size[i])
            for k in range(n):
                j = (e_off[i] + k) * 2 + 1
                e2 = e_buf[j]
                if e2 >= 0:
                    if mine or pair[edges[e2, 0]] != 0 or pair[edges[e2, 1]] != 0:
                        e_buf[j] = -1

    @wp.kernel
    def _free_contacts(mask: wp.array(dtype=int), inside: wp.array(dtype=int), use_inside: int,
                       tri: wp.array2d(dtype=int), edges: wp.array2d(dtype=int), nv: int, ntri: int,
                       v_cnt: wp.array(dtype=wp.int32), v_md: wp.array(dtype=float),
                       t_cnt: wp.array(dtype=wp.int32), t_md: wp.array(dtype=float),
                       e_cnt: wp.array(dtype=wp.int32), e_md: wp.array(dtype=float)):
        i = wp.tid()
        if i < nv and i < v_cnt.shape[0]:
            if mask[i] != 0 or (use_inside != 0 and inside[i] != 0):
                v_cnt[i] = 0
                v_md[i] = 1.0
        if i < ntri and i < t_cnt.shape[0] and i < t_md.shape[0]:
            a = tri[i, 0]
            b = tri[i, 1]
            c = tri[i, 2]
            if mask[a] != 0 or mask[b] != 0 or mask[c] != 0:
                t_cnt[i] = 0
                t_md[i] = 1.0
        if i < edges.shape[0] and i < e_cnt.shape[0] and i < e_md.shape[0]:
            a = edges[i, 0]
            b = edges[i, 1]
            if mask[a] != 0 or mask[b] != 0 or (use_inside != 0 and (inside[a] != 0 or inside[b] != 0)):
                e_cnt[i] = 0
                e_md[i] = 1.0
    FREE = {'mask': wp.zeros(model.particle_count, dtype=int), 'tri': wp.array(_ft.astype(np.int32), dtype=int),
            'edges': wp.array(_fe.astype(np.int32), dtype=int), 'rings': int(_uf.get('rings', 2)), 'rows': [],
            'idle': int(_uf.get('idle', 1)), 'next': 0 if cfg.get('untangle_free') else 1 << 30,
            'seams': bool(_uf.get('seams', False)), 'pair': wp.zeros(model.particle_count, dtype=int),
            'm_auto': np.zeros(model.particle_count, bool), 'm_x': np.zeros(model.particle_count, bool),
            'm_s': np.zeros(model.particle_count, bool),     # the smooth brush's own crossings (_stroke_untangle)
            # the two kernels are launched only while something may be freed (untangle_free, or a grab through);
            # with a CUDA graph both variants are captured and the frame picks one (design review 2026-10-26)
            'launch': bool(cfg.get('untangle_free'))}
    _fdim = max(model.particle_count, len(_ft), len(_fe))
    _orig_detect = solver._collision_detection_penetration_free

    def _free_post(ci):
        if not FREE['launch']:
            return
        wp.launch(_free_contacts, dim=_fdim,
                  inputs=[FREE['mask'], DEEP['inside'] if DEEP is not None else FREE['mask'], 1 if DEEP is not None else 0,
                          FREE['tri'], FREE['edges'], model.particle_count, len(_ft),
                          ci.vertex_colliding_triangles_count, ci.vertex_colliding_triangles_min_dist,
                          ci.triangle_colliding_vertices_count, ci.triangle_colliding_vertices_min_dist,
                          ci.edge_colliding_edges_count, ci.edge_colliding_edges_min_dist])
        wp.launch(_free_pairs, dim=_fdim,
                  inputs=[FREE['pair'], FREE['tri'], FREE['edges'], model.particle_count,
                          ci.vertex_colliding_triangles_count, ci.vertex_colliding_triangles_buffer_sizes,
                          ci.vertex_colliding_triangles_offsets, ci.vertex_colliding_triangles,
                          ci.edge_colliding_edges_count, ci.edge_colliding_edges_buffer_sizes,
                          ci.edge_colliding_edges_offsets, ci.edge_colliding_edges])

    def _detect_and_free(state):
        _orig_detect(state)
        _free_post(solver.trimesh_collision_detector.collision_info)
    solver._collision_detection_penetration_free = _detect_and_free
    solver.ac9_free_post = _free_post   # Accurate's own wide detection frees the same contacts (self_contact_lagged)
    solver.ac9_graph_extra = lambda: FREE['launch']   # the captured iteration differs with and without them

    def _free_assign():
        FREE['mask'].assign((FREE['m_auto'] | FREE['m_x'] | FREE['m_s']).astype(np.int32))

    def _grow(m, rings):
        for _ in range(rings):
            m2 = m.copy(); m2[_fEall[m[_fEall[:, 0]], 1]] = True; m2[_fEall[m[_fEall[:, 1]], 0]] = True; m = m2
        return m

    def _through_update(q, piece, center):
        """X: the crossings between `piece` and the other pieces within THROUGH_D of `center`, both ways, the
        stitched edges searched too, grown by two rings; returns the number of crossing edges."""
        near = np.linalg.norm(q - center, axis=1) < THROUGH_D
        Ed = _fEall[near[_fEall[:, 0]] | near[_fEall[:, 1]]]
        Td = _ft[near[_ft[:, 0]] | near[_ft[:, 1]] | near[_ft[:, 2]]]
        m = np.zeros(model.particle_count, bool); n_x = 0
        for Ea_, Tb_ in ((Ed[_fvp[Ed[:, 0]] == piece], Td[_fvp[Td[:, 0]] != piece]),
                         (Ed[_fvp[Ed[:, 0]] != piece], Td[_fvp[Td[:, 0]] == piece])):
            if not len(Ea_) or not len(Tb_):
                continue
            ei_, ti_ = _fx_cross(q, Ea_, Tb_)
            n_x += len(np.unique(ei_))
            m[Ea_[ei_].ravel()] = True; m[Tb_[ti_].ravel()] = True
        FREE['m_x'] = _grow(m, 2)
        _free_assign()
        return n_x

    def _free_mask_update(q):
        m = np.zeros(model.particle_count, bool); n_x = 0
        tp_ = _fvp[_ft[:, 0]]; ep_ = _fvp[_fE[:, 0]]
        for a_ in np.unique(_fvp):
            Ea_ = _fE[ep_ == a_]; Tb_ = _ft[tp_ != a_]
            if not len(Ea_) or not len(Tb_):
                continue
            ei_, ti_ = _fx_cross(q, Ea_, Tb_)
            n_x += len(np.unique(ei_))
            m[Ea_[ei_].ravel()] = True; m[Tb_[ti_].ravel()] = True
        for _ in range(FREE['rings']):
            m2 = m.copy(); m2[_fE[m[_fE[:, 0]], 1]] = True; m2[_fE[m[_fE[:, 1]], 0]] = True; m = m2
        if not FREE['seams']:
            m &= ~_fsewn
        FREE['m_auto'] = m
        _free_assign()
        FREE['rows'].append([int(n_x), int(m.sum())])
        log['untangle_free']['per_frame'] = FREE['rows']
    if cfg.get('untangle_free'):
        log['untangle_free'] = {'rings': FREE['rings'], 'seams': FREE['seams'], 'idle': FREE['idle'],
                                'per_frame_cols': ['crossing_edges', 'freed_vertices']}

# the grab carried inside the substeps (Fable fable_grab2 2026-10-28, user OK): the frame's move (at most GRAB_STEP)
# is cut into `sub` equal steps, one before each solve. Moved 5 mm at once, the ring next to the held vertices could
# not follow, its hinges turned over in one solve and stayed crossed (self contact skips 2 rings): 18 crossings while
# pulling the hem 50 mm, 0 at 1 mm a frame. cfg 'grab_substep': false = the old move at the frame's start (tests)
# The grab pulled by springs (Fable REVIEW_fable_live_grab_purpose_2026-10-28, user OK 10/28), the default: the held
# vertices keep their mass and every substep get a force towards the target + their offset from the held centroid,
# k = grab_mass_frac x m / dt^2 (as the attach force: explicit, overshoots at 2), no step limit. The massless block
# left the ring at its edge turned over where self contact does not look (2 rings): 7-59 crossings in 3 pulls of the
# back hem 50 mm, 0 with springs; at 1.0 the held centroid is 2-4 mm short of the target after 50 frames (0.25: 9-13
# mm) and 45 mm on after 20. cfg 'grab_mode': 'rigid' = that massless block, moved in `sub` equal steps (tests)
GRAB_MODE = cfg.get('grab_mode', 'spring')
GRB = None
# Fast Live: the runtime's per-substep phase (True = this substep is corrected), called before the grab kernel so the
# spring acts once: explicitly in Native substeps, implicitly inside the correction in corrected ones (see grab start)
_FAST_SUBSTEP = None
if (cfg.get('live') or cfg.get('grab_script') or cfg.get('grab_file')) and cfg.get('grab_substep', True):
    GRB = {'mask': wp.zeros(model.particle_count, dtype=int), 'step': wp.zeros(1, dtype=wp.vec3),
           'off': wp.zeros(model.particle_count, dtype=wp.vec3), 'k': wp.zeros(model.particle_count, dtype=float),
           'frac': float(cfg.get('grab_mass_frac', 1.0))}

    @wp.kernel
    def _grab_sub(q: wp.array(dtype=wp.vec3), qd: wp.array(dtype=wp.vec3), m: wp.array(dtype=int),
                  s: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        if m[i] != 0:
            q[i] = q[i] + s[0]
            qd[i] = wp.vec3(0.0)

    @wp.kernel
    def _grab_spring(q: wp.array(dtype=wp.vec3), m: wp.array(dtype=int), off: wp.array(dtype=wp.vec3),
                     k: wp.array(dtype=float), c: wp.array(dtype=wp.vec3), f: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        if m[i] != 0:
            wp.atomic_add(f, i, (c[0] + off[i] - q[i]) * k[i])

# The stroke brush (Live, Ctrl + Shift + drag, user 2026-11-02 "press and stroke", Fable
# REVIEW_fable_live_smooth_2026-11-02): {"stroke": true, "center": [x, y, z] m (on the cloth under the mouse), "view":
# [x, y, z] (the view ray), "radius": m, "strength": 0-1} in grab.json (or a grab_script entry). A pull towards the
# mean of the 1-ring did nothing (the folds are spare length the membrane keeps: 1/2000 of its stiffness at frac 0.5;
# at 50 it pressed the disc 29 mm in, experiments/claude_live_smooth_2026-11-02). What evens cloth out is a hand: it
# presses the cloth down and carries it along, the spare length is sent ahead and leaves at the hem. Every frame the
# vertices within radius of the center get the grab's springs (GRB, k = w x STROKE_FRAC x m / dt^2, w = smoothstep
# falloff x strength) towards where they are + the brush's move since the last frame (along the cloth) - the height
# they stand above the disc's plane towards the viewer (pressed down only, never pulled up). Standing still it holds the
# cloth softly. Native and Fast Live (AC9_TEST_FAST_E6=0 leaves it out of Fast Live, tests only); one drag at a time with the grab
STROKE_FRAC = float(cfg.get('stroke_frac', 1.0))
STROKE_PRESS = bool(cfg.get('stroke_press', True))   # tests: false = carry only
# The brush also undoes cloth gone through itself (user 2026-11-02: "Sculpt's Smooth undoes self crossings"): within the
# brush the edges of a piece passing through a triangle of the same piece (not touching it) are found on the CPU,
# their vertices and two rings lose their contacts (FREE['m_s'], as the grab through frees its crossings: self contact
# pushes crossed cloth away from its closest point whichever side it is on and so holds it crossed; within two rings
# the solver's topological filter already has none). What holds a flap turned over within a ring or two is the bending
# hysteresis (bend_fric2): its hinges are stuck at the turned-over angle. While brushed, the hinges of the crossing
# have their stuck angle set back to their design angle (ra0: flat, or the fold line's angle) and do not slip, so the
# same 100 x k0 that held the flap turns it back; the crossing's vertices are only carried, neither pressed nor pulled
# (Fable REVIEW_fable_smooth_untangle_2026-11-02: pulling to the 1-ring mean pulls the two layers into each other).
# On release the hinges slip again as usual. Crossings within two rings of a fold line (a hinge folded over 90 deg:
# a pleat, a hem turned at 180) are left alone: their layers cross by design, and an angle near 180 deg wraps
# (the user's shirt: brushing its box pleat took its crossings 46 -> 223). Crossings with other pieces are the grab
# through's (Ctrl+Alt).
# cfg 'stroke_untangle': false = off (tests)
STROKE_UNTANGLE = bool(cfg.get('stroke_untangle', True))
_stroke_bf = None                                   # (rows the brush holds at their design angle, their own theta_y)
_stroke_fold = None                                 # vertices within two rings of a fold line (made at the first stroke)


def _stroke_untangle(q_, c_, r_):
    """The crossings inside one piece within the brush: sets FREE['m_s'] (their vertices grown by two rings);
    returns (that mask, crossing edges)."""
    near_ = np.linalg.norm(q_ - c_, axis=1) < r_
    Ed_ = _fEall[near_[_fEall[:, 0]] & near_[_fEall[:, 1]]]
    Td_ = _ft[near_[_ft[:, 0]] & near_[_ft[:, 1]] & near_[_ft[:, 2]]]
    m_ = np.zeros(model.particle_count, bool); n_ = 0; x_ = m_.copy()
    if len(Ed_) and len(Td_):
        ei_, ti_ = _fx_cross(q_, Ed_, Td_)
        k_ = (_fvp[Ed_[ei_, 0]] == _fvp[Td_[ti_, 0]]) & ~(Ed_[ei_][:, :, None] == Td_[ti_][:, None, :]).any((1, 2))
        global _stroke_fold
        if _stroke_fold is None:
            # the design angles: bend_fric2 rewrites edge_rest_angle to each hinge's stuck angle (a turned-over flap
            # would read as a fold), so its rows take ra0
            _e4 = model.edge_indices.numpy(); _ra4 = model.edge_rest_angle.numpy().copy()
            if BF2 is not None and BF2['f_on'] is not None:
                _ra4[BF2['r_np']] = BF2['ra0'].numpy()
            _fo = np.abs(_ra4) > np.pi / 2
            _stroke_fold = np.zeros(model.particle_count, bool)
            _v4 = _e4[_fo].ravel(); _stroke_fold[_v4[_v4 >= 0]] = True
            _stroke_fold = _grow(_stroke_fold, 2)
        k_ &= ~(_stroke_fold[Ed_[ei_]].any(1) | _stroke_fold[Td_[ti_]].any(1))
        ei_, ti_ = ei_[k_], ti_[k_]
        n_ = len(np.unique(ei_))
        if n_:
            m_[Ed_[ei_].ravel()] = True; m_[Td_[ti_].ravel()] = True
            x_ = m_.copy(); m_ = _grow(m_, 2)
    FREE['m_s'] = m_; _free_assign()
    return x_, n_


def _stroke_hinges(x_):
    """Hold the bending hysteresis of the hinges touching the crossing vertices x_ at their design angle (None/empty:
    give back the ones held)."""
    global _stroke_bf
    if BF2 is None or BF2['f_on'] is None or BF2['inside']:
        return 0
    if _stroke_bf is not None:
        r_, t_ = _stroke_bf
        th_ = BF2['thy'].numpy(); th_[r_] = t_; BF2['thy'].assign(th_); _stroke_bf = None
    if x_ is None or not x_.any():
        return 0
    if 'ei_np' not in BF2:
        BF2['ei_np'] = BF2['ei'].numpy()
    r_ = np.nonzero(x_[BF2['ei_np']].any(1))[0]
    if len(r_):
        th_ = BF2['thy'].numpy(); _stroke_bf = (r_, th_[r_].copy()); th_[r_] = 1e3; BF2['thy'].assign(th_)
        st_ = BF2['st'].numpy(); st_[r_] = BF2['ra0'].numpy()[r_]; BF2['st'].assign(st_)
    return len(r_)
_stroke = None                                 # (the drag's id, the last frame's brush center)


def _stroke_frame(f_, S_):
    """Set the grab springs for this frame from grab.json's stroke entry S_ (None: let go)."""
    global _stroke
    if S_ is None:
        if _stroke is not None:
            GRB['mask'].zero_(); GRB['step'].zero_(); _stroke = None
            if FREE is not None and FREE['m_s'].any():
                FREE['m_s'][:] = False; _free_assign()
            _stroke_hinges(None)
            log['stroke'].append([f_, 'released'])
        return
    q_ = s0.particle_q.numpy().astype(np.float64)
    c_ = np.asarray(S_['center'], np.float64)
    r_ = max(float(S_.get('radius', 0.03)), 1e-4)
    t_ = np.clip(1.0 - np.linalg.norm(q_ - c_, axis=1) / r_, 0.0, 1.0)
    w_ = t_ * t_ * (3.0 - 2.0 * t_) * min(max(float(S_.get('strength', 0.5)), 0.0), 1.0)
    w_[model.particle_inv_mass.numpy() == 0.0] = 0.0         # held / frozen vertices stay as they are
    m_ = w_ > 0
    n_ = -np.asarray(S_.get('view', [0.0, 0.0, -1.0]), np.float64)
    n_ /= max(np.linalg.norm(n_), 1e-12)                    # towards the viewer: the side pressed down
    # a new drag (its id changed, or a grab came in between) does not carry from the last one's center
    mv_ = np.zeros(3) if _stroke is None or _stroke[0] != S_.get('id') else c_ - _stroke[1]
    mv_ -= n_ * (mv_ @ n_)                                  # along the cloth only
    mv_ *= min(1.0, 0.5 * r_ / max(np.linalg.norm(mv_), 1e-12))   # a jump of the screen is not a stroke
    _stroke = (S_.get('id'), c_)
    tg_ = q_ + mv_
    if m_.any() and STROKE_PRESS:
        p0_ = (q_[m_] * w_[m_, None]).sum(0) / w_[m_].sum()
        h_ = np.maximum((q_ - p0_) @ n_, 0.0)
        tg_ -= h_[:, None] * n_
    nx_ = nh_ = 0
    # only where the hinges can be turned back (bend_fric2 on, solve 'after'): freeing alone would let more through
    if FREE is not None and STROKE_UNTANGLE and BF2 is not None and BF2['f_on'] is not None and not BF2['inside']:
        x_, nx_ = _stroke_untangle(q_, c_, r_)
        nh_ = _stroke_hinges(x_)
        if nx_:
            x_ = _grow(x_, 1)
            tg_[x_] = q_[x_] + mv_                          # carried, not pressed
    GRB['off'].assign(np.where(m_[:, None], tg_, 0.0).astype(np.float32))
    GRB['k'].assign((STROKE_FRAC * w_ * model.particle_mass.numpy() / DT_EXPLICIT ** 2).astype(np.float32))
    if FAST_LIVE:   # test switch (stage E6): the correction carries the brush's springs at the 10-substep strength, as a grab's
        GRB['kp'] = (STROKE_FRAC * w_ * model.particle_mass.numpy() * (fps * 10) ** 2).astype(np.float32)
    GRB['mask'].assign(m_.astype(np.int32)); GRB['step'].zero_()
    log['stroke'].append([f_, int(m_.sum()), round(float(np.linalg.norm(mv_)) * 1000, 2), int(nx_), int(nh_)])


def _substep_a(fast=None, d=None):
    global s0, s1
    if GRB is not None and GRAB_MODE == 'rigid':    # a no-op while nothing is held (so the GPU graph keeps it)
        wp.launch(_grab_sub, dim=model.particle_count,
                  inputs=[s0.particle_q, s0.particle_qd, GRB['mask'], GRB['step']])
    s0.clear_forces()
    _fast_sub = (_FAST_SUBSTEP is not None and _FAST_SUBSTEP(s0)) if fast is None else fast
    if GRB is not None and GRAB_MODE != 'rigid' and not _fast_sub:
        wp.launch(_grab_spring, dim=model.particle_count,
                  inputs=[s0.particle_q, GRB['mask'], GRB['off'], GRB['k'], GRB['step'], s0.particle_f])
    if ATTACH is not None and not (_fast_sub and (os.environ.get('AC9_TEST_FAST_E5', '1') != '0')):
        wp.launch(_attach_f, dim=len(ATTACH['v']),
                  inputs=[s0.particle_q, ATTACH['wv'], ATTACH['wg'], ATTACH['wk'], ATTACH['wc'], ATTACH['wp'],
                          ATTACH['ws0'], ATTACH['ws1'], ATTACH['wcl'], s0.particle_f])
    pipe.collide(s0, contacts)
    if LAYER_K:
        wp.launch(_append_layer_contacts, dim=LAYER_K,
                  inputs=[SYN_N, SYN_P, SYN_POS, SYN_VEL, SYN_NRM, 0, contacts.soft_contact_max,
                          contacts.soft_contact_count, contacts.soft_contact_indices, contacts.soft_contact_particle,
                          contacts.soft_contact_barycentric, contacts.soft_contact_shape,
                          contacts.soft_contact_body_pos, contacts.soft_contact_body_vel,
                          contacts.soft_contact_normal])
    if DEEP is not None:
        wp.launch(DEEP['k0'], dim=1, inputs=[contacts.soft_contact_count, DEEP['c0']])
        wp.launch(DEEP['k'], dim=_deep_n,
                  inputs=[DEEP['mesh'].id, s0.particle_q, DEEP['gap'], DEEP['cap'], DEEP['maxd'], 0,
                          contacts.soft_contact_max, contacts.soft_contact_count, contacts.soft_contact_indices,
                          contacts.soft_contact_particle, contacts.soft_contact_barycentric,
                          contacts.soft_contact_shape, contacts.soft_contact_body_pos, contacts.soft_contact_body_vel,
                          contacts.soft_contact_normal, DEEP['n'], DEEP['inside']])
        if DEEP['own_off']:
            wp.launch(DEEP['koff'], dim=contacts.soft_contact_max,
                      inputs=[DEEP['c0'], DEEP['inside'], contacts.soft_contact_particle, contacts.soft_contact_body_pos,
                              contacts.soft_contact_normal])
    solver.step(s0, s1, control, contacts, dt if d is None else d)


def _substep_b():
    global s0, s1
    if BF2 is not None:                     # a no-op until BF2['on'] is set (so the GPU graph keeps it)
        _bf2_launch(s1.particle_q, 0)
    if FANCH is not None:
        wp.launch(_fric_anchor_update, dim=model.particle_count,
                  inputs=[s1.particle_q, FANCH['a'], FANCH['n'], FANCH['hit'], FANCH['eps'], FANCH['slid']])
    if DIAG is not None:
        wp.launch(DIAG['kernel'], dim=model.particle_count,
                  inputs=[solver.truncation_ts, solver.particle_displacements, DIAG['held'], DIAG['want']])
    s0, s1 = s1, s0


def _presewn_phase():
    # once per frame, at its start: Live's substeps and damping until sewn, then the contract's (never undone)
    fast = _FAST_SUBSTEP is not None and _FAST_SUBSTEP(s0)
    if fast != PRESEWN_STATE['fast']:
        kd = model.shape_material_kd.numpy()
        kd[BODY_SHAPE] = float(cfg['body_kd']) if fast else BODY_KD_LIVE
        model.shape_material_kd.assign(kd)
        if FANCH is not None:
            FANCH['eps'] = FANCH['eps_fast'] if fast else float(solver.friction_epsilon) / (fps * SUB_LIVE)
        PRESEWN_STATE['fast'] = fast
    if not fast:
        PRESEWN_STATE['frames_live'] += 1
    return fast


def simulate():
    # test (claude 11/03, s3-graph-split): one substep is _substep_a (up to solver.step) + _substep_b (after it)
    if PRESEWN:
        fast = _presewn_phase()
        for _ in range(sub if fast else SUB_LIVE):
            _substep_a(fast, dt if fast else DT_LIVE)
            _substep_b()
        return
    for _ in range(sub):
        _substep_a()
        _substep_b()


LAYER_RECOVERY = None
if hasattr(solver, 'ac9_layer_crossings') and cfg.get('layers_coupled', True):
    from layer_patch_runtime import GraphRecovery
    LAYER_RECOVERY = GraphRecovery(globals(), out)

_trace('graph_begin')
graph = None
_native_pairs = []


def _capture_frame():
    if (not FAST_LIVE and LAYER_RECOVERY is None
            and getattr(wp, 'is_conditional_graph_supported', lambda: False)()):
        # Native repeats the same two buffer orientations. Capture those once
        # and retain their order as child graphs, without executing a substep.
        from native_graph import capture as _native_capture
        frame, counter = _native_capture(_substep_a, _substep_b, sub)
        _native_pairs.append(counter)
        return frame
    with (LAYER_RECOVERY.capture() if LAYER_RECOVERY else wp.ScopedCapture()) as frame:
        simulate()
    return frame.graph


if cfg.get('graph', True) and wp.get_device().is_cuda:
    # stream capture records the launches without running them; an even substep count leaves s0/s1 as they were
    assert sub % 2 == 0
    graph = _capture_frame()
SPLIT_GRAPHS = None
if graph is not None and os.environ.get('AC9_TEST_SPLIT_GRAPH'):
    # test (claude 11/03): the same launches as 2*sub graphs (before / after solver.step), a slot after each step
    def _split_capture():
        out_ = []
        for _ in range(sub):
            for part in (_substep_a, _substep_b):
                with (LAYER_RECOVERY.capture() if LAYER_RECOVERY and part is _substep_a else wp.ScopedCapture()) as cap:
                    part()
                out_.append(cap.graph)
        return out_
    SPLIT_GRAPHS = _split_capture()
    SPLIT_GRAPHS_FREE = SPLIT_GRAPHS
    if FREE is not None:
        FREE['launch'] = True; SPLIT_GRAPHS_FREE = _split_capture(); FREE['launch'] = False
graph_free = graph                             # the substeps with the freeing kernels (see FREE['launch'])
if graph is not None and FREE is not None and not FREE['launch']:
    FREE['launch'] = True
    graph_free = _capture_frame()
    FREE['launch'] = False

_trace('graph_end')
frames = int(cfg['frames'])
VEL_DAMP = float(cfg.get('vel_damp', 1.0))
AIR_DAMP = float(cfg.get('air_damp', 1.0))
AIR_F = model.tri_indices.numpy().reshape(-1, 3) if AIR_DAMP != 1.0 else None


class _LastFrames:
    """The frames of a Live / Local stage (user 2026-10-23, DECISIONS cloth-sim-phase-live-local-sim-2026-10-23): it
    runs until stopped with no frame cap, and only its last shape is kept -- frame 0 and the last few frames are held
    (the loop reads res[f - 1]), the others dropped as it goes. Indexed like the full array for what the loop and
    the reports read: res[f], res[-1], res[0], len(res), res[:f + 1]."""

    KEEP = 4

    def __init__(self, first):
        self.first, self.recent, self.n = first, {}, 1

    def __len__(self):
        return self.n

    def _i(self, i):
        return self.n + i if i < 0 else i

    def __getitem__(self, i):
        if isinstance(i, slice):                # res[:f + 1]: the stage ends at frame f
            assert i.start is None and i.step is None
            self.n = min(self.n, i.stop)
            return self
        i = self._i(i)
        return self.first if i == 0 else self.recent[i]

    def __setitem__(self, i, x):
        i = self._i(i)
        self.recent[i] = np.array(x, np.float32)
        self.n = max(self.n, i + 1)
        for k in [k for k in self.recent if k <= i - self.KEEP]:
            del self.recent[k]

    def written(self):
        """What frames.npy gets: the start and the last shape."""
        return np.stack([self.first, self[-1]]) if self.n > 1 else self.first[None]


if cfg.get('live'):
    frames = 10 ** 9                            # until stopped: no cap
    res = _LastFrames(s0.particle_q.numpy()[:N_G].astype(np.float32))
else:
    res = np.zeros((frames + 1, len(X0), 3), np.float32)
    res[0] = s0.particle_q.numpy()[:N_G]
_trace('initial_sync_begin')
wp.synchronize()
_trace('initial_sync_end')
# stitch_ramp_frames N: the stitch rest length starts at the initial gap and shrinks linearly to 0 over N frames,
# so a 100 mm shoulder gap is drawn in instead of snapped shut (jacket fronts flipped over the shoulder, 2026-09-25)
RAMP = int(cfg.get('stitch_ramp_frames', 0))
if RAMP and model.spring_count:
    _si = model.spring_indices.numpy().reshape(-1, 2)
    _q = s0.particle_q.numpy()
    L0s = np.linalg.norm(_q[_si[:, 0]] - _q[_si[:, 1]], axis=1).astype(np.float32)
# stitch_delay: [{"pieces": [a, b], "start": f0, "frames": n}] -- the stitches between pieces a and b are off
# until frame f0, then take the gap they find there and close it over n frames. The collar-to-notch seam started
# 125-138 mm open and, closed together with the rest, dragged the collar end through a lapel (2026-09-25); after
# the shoulder / neck seams have drawn the fronts in, the gap is smaller.
DELAY = []
# the switched-off seams' springs (E_OFF, right after the pattern stitches): their stiffness kept for when one is sewn on
OFF_R = np.arange(len(E), len(E) + len(E_OFF))
KE_OFF = np.zeros(0, np.float32)
if len(OFF_R):
    _k = model.spring_stiffness.numpy()
    KE_OFF = _k[OFF_R].copy()
    _k[OFF_R] = 0.0
    model.spring_stiffness.assign(_k)
if cfg.get('stitch_delay') and model.spring_count:
    _si = model.spring_indices.numpy().reshape(-1, 2)
    vpc = v_piece[sel]
    for g in cfg['stitch_delay']:
        if g.get('seam') is not None:              # one seam by its spec id (the pattern stitches are the first springs)
            m = np.zeros(len(_si), bool); m[:len(E)] = E_SEAM == str(g['seam'])
            DELAY.append((np.nonzero(m)[0], int(g['start']), int(g['frames'])))
            continue
        pa, pb = [pat_names.index(n) for n in g['pieces']]
        m = ((vpc[_si[:, 0]] == pa) & (vpc[_si[:, 1]] == pb)) | ((vpc[_si[:, 0]] == pb) & (vpc[_si[:, 1]] == pa))
        m[OFF_R] = False                            # a switched-off seam stays off
        DELAY.append((np.nonzero(m)[0], int(g['start']), int(g['frames'])))
    KE0 = model.spring_stiffness.numpy().copy()
    ke = KE0.copy()
    for ids, _, _ in DELAY:
        ke[ids] = 0.0
    model.spring_stiffness.assign(ke)
    log['stitch_delay'] = [[int(len(i_)), st, n_] for i_, st, n_ in DELAY]
if model.spring_count and (len(OFF_R) or cfg.get('live')):
    # a Live stage's seams can be sewn on (or again) while it runs, from the gap they have then (live_params): their
    # stiffness when on
    if not cfg.get('stitch_delay'):
        KE0 = model.spring_stiffness.numpy().copy()
    KE0[OFF_R] = KE_OFF
# live_params (user 2026-10-23, DECISIONS cloth-sim-phase-live-local-sim-2026-10-23): the values a person changes while
# a Live / Local stage runs, in <job>/params.json (cfg params_file; the add-on writes it whenever the garment's spec
# changes): {"gravity": bool, "fold": {"piece|line": deg}, "seams_off": [seam ids], "frozen": [pieces],
# "elastic": {piece: ratio}}. Read when the file changes; each change eases in over LIVE_RAMP frames -- a fold from
# the hinges' rest angle now to the new one (with the bend hysteresis on, its rest angle ra0 too: the stick-slip then
# gives way past theta_y), a seam sewn on from the gap it has then (as a delayed seam), an elastic from its stretch
# now -- or at once (gravity, a piece held or let go, a seam let go). Nothing here changes the mesh.
LIVE_RAMP = 30
LIVEP = None
if cfg.get('live'):
    _pz = np.load(cfg['pattern'])
    LIVEP = {'path': cfg.get('params_file') or os.path.join(out, 'params.json'), 'mt': None,
             'meta': ['|'.join(m_.split('|')[:2]) for m_ in _pz['fold_meta']],
             'Lf': np.concatenate([_pz[f"Lf{i}"] for i in range(len(_pz['names']))])[sel],
             'fold': {k_: float(v_) for k_, v_ in (cfg.get('fold_deg') or {}).items()},
             'seams_off': set(str(x_) for x_ in E_OFF_SEAM),
             'frozen': set(cfg.get('frozen_pieces') or []) - set(cfg.get('ghost_pieces') or []),
             'gravity': float(cfg.get('gravity_z', -9.81)) != 0.0,
             'elastic': {ELASTIC_P[k_]: ELASTIC[k_][3] for k_ in range(len(ELASTIC)) if ELASTIC_P[k_]},
             'strengthen_pieces': dict(cfg.get('strengthen_pieces') or {}),
             'ramps': [], 'applied': []}
    LIVEP['terminal']=cfg.get('experimental_fold_terminal')
    if BF2 is not None:
        LIVEP['bf2_row'] = -np.ones(model.edge_count, np.int64)
        LIVEP['bf2_row'][BF2['r_np']] = np.arange(BF2['n'])


def _live_hinges(key_):
    """The cloth hinges along a fold line (both ends on it), as fold_deg finds them."""
    if key_ not in LIVEP['meta']:
        return np.zeros(0, np.int64)
    fid_ = LIVEP['meta'].index(key_)
    ei_ = model.edge_indices.numpy()
    v0_, v1_ = ei_[:, 2], ei_[:, 3]
    Lf_ = LIVEP['Lf']
    m_ = (v0_ >= 0) & (v1_ >= 0) & (Lf_[np.maximum(v0_, 0)] == fid_) & (Lf_[np.maximum(v1_, 0)] == fid_)
    return np.nonzero(m_)[0]


def _live_rest(ids_, ang_, preserve_history=False):
    """Set the rest angle of hinges ids_ (and the bend hysteresis' ra0 once it is on)."""
    ra_ = model.edge_rest_angle.numpy()
    values_=np.broadcast_to(ang_,ids_.shape).copy()
    if BF2 is not None and BF2['f_on'] is not None:
        r_ = LIVEP['bf2_row'][ids_]
        ok_ = r_ >= 0
        if ok_.any():
            r0_ = BF2['ra0'].numpy()
            r0_[r_[ok_]] = np.broadcast_to(ang_, ids_.shape)[ok_]
            BF2['ra0'].assign(r0_)
            if preserve_history and not BF2['inside']:
                values_[ok_]=(values_[ok_]+BF2['rat']*BF2['st'].numpy()[r_[ok_]])/(1.+BF2['rat'])
    ra_[ids_] = values_
    model.edge_rest_angle.assign(ra_)


def _live_params(f_):
    """Apply params.json if it changed since it was last read, then the running fold eases."""
    try:
        mt_ = os.path.getmtime(LIVEP['path'])
    except OSError:
        mt_ = None
    if mt_ is not None and mt_ != LIVEP['mt']:
        try:
            P_ = json.load(open(LIVEP['path'], encoding='utf-8'))
            LIVEP['mt'] = mt_
        except (OSError, ValueError):
            P_ = None                               # half written: read it next frame
        if P_ is not None:
            done_ = _live_apply(P_, f_)
            if done_:
                LIVEP['applied'].append([f_] + done_)
                log['live_params'] = LIVEP['applied']
                print('live_params', f_, '; '.join(done_), flush=True)
    keep_ = []
    for r_ in LIVEP['ramps']:
        kind_, k_, ids_, a0_, b_, f0_ = r_
        t_ = min(1.0, (f_ - f0_ + 1) / LIVE_RAMP)
        _live_rest(ids_, a0_ + (b_ - a0_) * t_,preserve_history=kind_=='terminal')
        if t_ < 1.0:
            keep_.append(r_)
    LIVEP['ramps'] = keep_


def _live_apply(P_, f_):
    global TERMINAL_FIELD_DIRTY
    if FAST_LIVE:_fast_contract.validate_live_update(P_, LIVEP)
    # Validate the complete profile before applying gravity, seams, or any rest.
    old_terminal=LIVEP.get('terminal');new_terminal=P_.get('fold_terminal',old_terminal)
    old_lines=(old_terminal or {}).get('lines',{});new_lines=(new_terminal or {}).get('lines',{})
    terminal_keys=set(old_lines)|set(new_lines);new_fold={k:float(v) for k,v in P_.get('fold',LIVEP['fold']).items()};terminal_targets={}
    if new_terminal is not None:
        _terminal_controls(set(new_lines))
        groups={k:(_live_hinges(k),0.) for k in new_lines}
        planned,audit=_terminal_module().apply_terminal_field(new_terminal,d['flat'],model.edge_indices.numpy(),model.edge_rest_angle.numpy(),groups,new_fold)
        terminal_targets={k:planned[v['hinges']].copy() for k,v in audit['lines'].items()}
    done_ = []
    if 'strengthen_pieces' in P_:
        changed = _set_strengthen(P_['strengthen_pieces'], live=True)
        if changed:
            done_.append('strengthen ' + ', '.join(f'{n} x{STRENGTHEN.get(n, 1.):g}' for n in changed))
        LIVEP['strengthen_pieces'] = dict(STRENGTHEN)
    if 'gravity' in P_ and bool(P_['gravity']) != LIVEP['gravity']:
        LIVEP['gravity'] = bool(P_['gravity'])
        model.set_gravity((0.0, 0.0, -9.81 if LIVEP['gravity'] else 0.0))
        done_.append(f"gravity {'on' if LIVEP['gravity'] else 'off'}")
    if 'fold' in P_ or 'fold_terminal' in P_:
        new_ = new_fold
        for k_ in sorted(set(new_) | set(LIVEP['fold']) | terminal_keys):
            a_, b_ = LIVEP['fold'].get(k_, 0.0), new_.get(k_, 0.0)
            profile_changed=old_lines.get(k_)!=new_lines.get(k_)
            if abs(a_ - b_) < 1e-6 and not profile_changed:
                continue
            ids_ = _live_hinges(k_)
            if len(ids_):
                a0_ = model.edge_rest_angle.numpy()[ids_].copy()
                kind_='terminal' if k_ in terminal_keys or k_ in LIVEP.get('terminal_seen',[]) else 'fold'
                if kind_=='terminal':
                    TERMINAL_FIELD_DIRTY=True
                    if BF2 is not None and BF2['f_on'] is not None:
                        rows_=LIVEP['bf2_row'][ids_];valid_=rows_>=0;a0_[valid_]=BF2['ra0'].numpy()[rows_[valid_]]
                LIVEP['ramps'] = [r_ for r_ in LIVEP['ramps'] if r_[1] != k_]
                target_=terminal_targets.get(k_,np.radians(b_))
                LIVEP['ramps'].append((kind_, k_, ids_, a0_, target_, f_))
                if 'FOLD_HINGES' in globals() and k_ in FOLD_HINGES:
                    FOLD_HINGES[k_]=(ids_,float(np.degrees(np.median(target_))))
            done_.append(f"fold {k_} {a_:g}->{b_:g} deg ({len(ids_)} hinges)")
        LIVEP['fold'] = new_
        LIVEP['terminal']=new_terminal
        LIVEP['terminal_seen']=sorted(set(LIVEP.get('terminal_seen',[]))|terminal_keys)
    if 'seams_off' in P_ and model.spring_count:
        new_ = set(str(x_) for x_ in P_['seams_off'])
        for sid_ in sorted(new_ ^ LIVEP['seams_off']):
            ids_ = np.concatenate([np.nonzero(E_SEAM == sid_)[0], OFF_R[E_OFF_SEAM == sid_]]).astype(np.int64)
            if not len(ids_):                   # no springs: off when the stage began under weld / BIND (no E_OFF)
                done_.append(f"seam {sid_}: not meshed")
                continue
            if sid_ in new_:                    # let go: at once
                ke_ = model.spring_stiffness.numpy()
                ke_[ids_] = 0.0
                model.spring_stiffness.assign(ke_)
                for k_, (i_, st_, n_) in enumerate(DELAY):
                    k_keep_ = ~np.isin(i_, ids_)
                    if not k_keep_.all():
                        DELAY[k_] = (i_[k_keep_], st_, n_)
                        if k_ in DL0:
                            DL0[k_] = DL0[k_][k_keep_]
                done_.append(f"seam {sid_} off ({len(ids_)} stitches)")
            else:                                # sewn on: from the gap it has now, over LIVE_RAMP frames
                DELAY.append((ids_, f_, LIVE_RAMP))
                done_.append(f"seam {sid_} on ({len(ids_)} stitches)")
        LIVEP['seams_off'] = new_
    if 'frozen' in P_:
        new_ = set(P_['frozen']) & set(pat_names)
        ch_ = new_ ^ LIVEP['frozen']
        if ch_:
            vp_ = v_piece[sel]
            pm_, pim_ = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
            qd_ = s0.particle_qd.numpy()
            for n_ in sorted(ch_):
                mv_ = np.nonzero(vp_ == pat_names.index(n_))[0]
                mv_ = mv_[mv_ < len(pm_)]
                if n_ in new_:
                    pm_[mv_] = 0.0
                    pim_[mv_] = 0.0
                    qd_[mv_] = 0.0
                else:
                    pm_[mv_] = PM_FREE[mv_]
                    pim_[mv_] = PIM_FREE[mv_]
                done_.append(f"{n_} {'held' if n_ in new_ else 'let go'}")
            model.particle_mass.assign(pm_)
            model.particle_inv_mass.assign(pim_)
            s0.particle_qd.assign(qd_)
        LIVEP['frozen'] = new_
    if 'elastic' in P_:
        new_ = {k_: float(v_) for k_, v_ in P_['elastic'].items() if k_ in pat_names}
        for n_ in sorted(set(new_) | set(LIVEP['elastic'])):
            r1_ = new_.get(n_, 1.0)
            if abs(LIVEP['elastic'].get(n_, 1.0) - r1_) < 1e-6:
                continue
            if n_ in ELASTIC_P:
                k_ = ELASTIC_P.index(n_)
                T_, E0_, TT_, r0_, st_, nn_ = ELASTIC[k_]
                s0_ = ELASTIC_FROM.get(k_, 1.0)
                ELASTIC_FROM[k_] = s0_ + (r0_ - s0_) * ELASTIC_A.get(k_, 1.0)     # the stretch it has now
                ELASTIC[k_] = (T_, E0_, TT_, r1_, f_, LIVE_RAMP)
                ELASTIC_A.pop(k_, None)
            elif WELD is None:                   # a piece that had none: the whole piece along its long axis
                T_ = np.nonzero(d['fp'] == pat_names.index(n_))[0]
                if len(T_):
                    P_v = d['flat'][np.unique(F[T_])]
                    tg_ = np.linalg.svd(P_v - P_v.mean(0), full_matrices=False)[2][0]
                    ELASTIC.append((T_, _rest_edges(model.tri_poses.numpy(), T_),
                                    np.repeat((tg_[:, None] * tg_[None, :])[None], len(T_), 0), r1_, f_, LIVE_RAMP))
                    ELASTIC_P.append(n_)
                    tm_ = model.tri_materials.numpy()
                    tm_[T_, 0:2] *= 0.3                  # core/elastic.STRENGTH
                    tm_[T_, 3] *= 0.3
                    if NATIVE_COLOR_PLAN is not None:
                        NATIVE_COLOR_PLAN.validate_tri_materials(tm_)
                    model.tri_materials.assign(tm_)
            done_.append(f"elastic {n_} x{r1_:g}")
        LIVEP['elastic'] = new_
    return done_


if PATH is not None:
    # the pairs' own stitches (a seam or a tack) are off while the targets pull; the target springs found by their ends
    _si = model.spring_indices.numpy().reshape(-1, 2)
    _key = {(int(min(x_, y_)), int(max(x_, y_))): i_ for i_, (x_, y_) in enumerate(_si)}
    PATH['orig'] = np.array([_key.get((int(min(a_, b_)), int(max(a_, b_))), -1) for a_, b_ in zip(PATH['A'], PATH['B'])],
                            np.int64)
    PATH['tsa'] = np.array([_key[(int(min(a_, t_)), int(max(a_, t_)))] for a_, t_ in zip(PATH['A'], PATH['T'][:, 0])])
    PATH['tsb'] = np.array([_key[(int(min(b_, t_)), int(max(b_, t_)))] for b_, t_ in zip(PATH['B'], PATH['T'][:, 1])])
    _k = model.spring_stiffness.numpy()
    PATH['ke_orig'] = _k[np.maximum(PATH['orig'], 0)].copy()
    _k[PATH['orig'][PATH['orig'] >= 0]] = 0.0
    model.spring_stiffness.assign(_k)
    log['seam_paths']['own_stitch_found'] = int((PATH['orig'] >= 0).sum())
SETTLE = None if cfg.get('live') else cfg.get('settle')       # see the end of the frame loop
# cfg 'watch' (test option, default off): {"drift_mm": 0.4, "window": 10, "min_verts": 300, "from": 0, "stop": true}
# -- an anomaly guard for the stages after the bodice has settled (user 2026-10-03: a collar moving the whole
# bodice cannot happen with real cloth): a piece of min_verts or more (not frozen) whose centroid keeps moving more
# than drift_mm per frame (mean over window frames) raises it. Measured afterwards on 7 jacket-with-collar jobs: in
# the stages after the collar is on, 5 of 5 broken runs went over 0.4 (0.55-0.88) within 2-39 frames and 2 of 2
# good runs stayed under (0.20-0.30); the open-stitch count did not tell them apart (0 in both). With "stop" the
# stage ends there and anomaly.json says which piece, when, and what pulled (stitches, collar under the bodice)
WATCH = None
if cfg.get('watch'):
    W_ = cfg['watch'] if isinstance(cfg['watch'], dict) else {}
    _vpw = v_piece[sel]
    _pim = model.particle_inv_mass.numpy()[:N_G]
    _bigp = [int(p_) for p_ in np.unique(_vpw) if (_vpw == p_).sum() >= int(W_.get('min_verts', 300))
             and (_pim[_vpw == p_] > 0).any()]
    WATCH = {'cfg': W_, 'pieces': _bigp, 'idx': [np.nonzero(_vpw == p_)[0] for p_ in _bigp], 'hist': [],
             'prev': None, 'alarm': None, 'tries': {},
             # nothing is judged while stitches are still being drawn in (ramp, delayed seams) and 10 frames after:
             # all 14 drift alarms at frame 11 of the first runs fell in that window (Fable review r9)
             'from_auto': max([int(cfg.get('stitch_ramp_frames', 0)) + 10]
                              + [int(g.get('start', 0)) + int(g.get('frames', 0)) + 10
                                 for g in (cfg.get('stitch_delay') or []) if int(g.get('start', 0)) < 10000])}
    # watch 'tug' (Fable review r8): a tug of war between two pieces -- per pair of pieces whose seam is closed (the
    # stitches' median length within closed_mm of their rest length), the net pull of its stitches on the first
    # piece, |sum of ke * stretch * direction|, averaged over window frames. Measured in the stage after the collar is on (gravity, stitch_ke 1000): 6 good runs at most
    # 0.25-0.91 N, the 90-degree collar 4.5 N (collar and stand pulling at each other, not moving). alarm_n for
    # alarm_frames stops the job and marks the pair's stitches; note_n for note_frames is only logged. Not for the
    # stage that sews pieces onto held ones nor the closing stage (stitch_ke 5000: good runs reach 3 N there).
    if W_.get('tug') is not None and W_.get('tug') is not False:      # {} = on with the defaults
        T_ = W_['tug'] if isinstance(W_['tug'], dict) else {}
        _sit = model.spring_indices.numpy().reshape(-1, 2)[:len(E)] if model.spring_count else np.zeros((0, 2), int)
        _pa, _pb = _vpw[_sit[:, 0]], _vpw[_sit[:, 1]]
        _keys = {}
        for e_ in range(len(_sit)):
            if _pa[e_] != _pb[e_]:
                _keys.setdefault((int(min(_pa[e_], _pb[e_])), int(max(_pa[e_], _pb[e_]))), []).append(e_)
        WATCH['tug'] = {'cfg': T_, 'pairs': {k_: np.asarray(v_) for k_, v_ in _keys.items()}, 'hist': [], 'over': {},
                        'note': {}}
    log['watch'] = {'pieces': [pat_names[p_] if pat_names else str(p_) for p_ in _bigp],
                    'drift_mm': float(W_.get('drift_mm', 0.4))}
if SETTLE:
    SETTLE_MM, SETTLE_N, SETTLE_RUN = float(SETTLE.get('mm', 0.1)), int(SETTLE.get('frames', 10)), 0
    # delayed seams that never open in this stage (the OPEN marks, start 100000) do not count: with them the settle
    # started at frame 100002 and stages 1-4 always ran to their cap (S1_settle run_log, Fable r2, 2026-10-09)
    SETTLE_FROM = max([RAMP + 1, int(SETTLE.get('min', 0))] + [st + n_ + 1 for _, st, n_ in DELAY if st < frames])
    # 'window' (frames, test): the net move over that many frames (p99, mm) is compared with mm instead of the
    # one-frame speed -- a cloth trembling in place moves back and forth and nets ~0, one that flows nets about
    # window x its speed. The ratio net / (window x speed) is logged ('settle_ratio': 0 = trembling, 1 = flowing)
    SETTLE_W = int(SETTLE.get('window', 0))
    log['settle'] = {'mm': SETTLE_MM, 'frames': SETTLE_N, 'from': SETTLE_FROM, 'cap': frames}
# cfg 'guard' (user decision 2026-10-09, DECISIONS cloth-sim-guard-on-by-default): every `every` frames, from frame 1 --
# also while seams are being pulled in, which the watch leaves alone -- two things are counted and an anomaly is
# recorded (never stopped: the run goes on, 10-06):
#   entangle: cloth of piece A at least far_mm from its seam with B (on the flat pattern) that comes within near_mm of
#     B's stitched vertices of that seam -- cloth taken into a seam. Counted against the stage's first frame (what is
#     near then -- a layer folded at placement, a placket over its front -- is the placement's, not an accident), only
#     for the pairs whose seams are sewn in this stage. Recorded when a pair's new count passes max(min, share x A's far
#     vertices). A trouser waist took the legs' cloth in at the frame the legs' own seams started (19 -> 195) with no
#     watch to see it (Fable r3, 2026-10-09). A's cloth already on its own seam at the start (a layer folded at
#     placement) is left out too
#   reopened: stitches that closed (under 1 mm) and are open again (over 2 mm); recorded at reopen_n or more twice running
#   crossing: new cloth-through-cloth pairs between pieces (below)
GUARD = cfg.get('guard')
if GUARD:
    from spatial import cKDTree as _KDg
    G_EVERY = int(GUARD.get('every', 10))
    G_FAR, G_NEAR = float(GUARD.get('far_mm', 15.0)), float(GUARD.get('near_mm', 3.0))
    G_MIN, G_SHARE = int(GUARD.get('min', 30)), float(GUARD.get('share', 0.03))
    G_REOPEN = int(GUARD.get('reopen_n', 5))
    _gvp = np.full(len(X0), -1, np.int64)
    _gvp[np.asarray(F).ravel()] = np.repeat(np.asarray(d['fp'], np.int64), 3)
    _gfl = np.asarray(d['flat'], np.float64)
    _gE = np.asarray(E, np.int64)
    _gidle = set()
    for _ids, _st, _n in DELAY:
        if _st >= frames:
            _gidle.update(int(x) for x in np.asarray(_ids).ravel())
    _gsides = {}
    for _k, (a_, b_) in enumerate(_gE):
        pa_, pb_ = int(_gvp[a_]), int(_gvp[b_])
        if pa_ < 0 or pb_ < 0 or pa_ == pb_ or _k in _gidle:
            continue
        _gsides.setdefault((pa_, pb_), set()).add(int(a_))
        _gsides.setdefault((pb_, pa_), set()).add(int(b_))
    G_CHECKS = []
    for (pa_, pb_), sv_ in _gsides.items():
        va_ = np.nonzero(_gvp == pa_)[0]
        if not len(va_):
            continue
        dfl_ = _KDg(_gfl[sorted(sv_)]).query(_gfl[va_])[0]
        far_ = va_[dfl_ >= G_FAR]
        partner_ = np.array(sorted(_gsides[(pb_, pa_)]))
        if len(far_) and len(partner_):
            G_CHECKS.append({'a': pa_, 'b': pb_, 'far': far_, 'partner': partner_, 'own': np.array(sorted(sv_)),
                             'base': None, 'flag': False})
    # crossing: cloth through cloth between two pieces, deeper than the contact can hold (0.45 mm, untangle's
    # min_depth), by Newton's own GPU triangle detector and the exact edge-through-triangle test (untangle.py).
    # The pairs crossing at the stage's start are the placement's; recorded when the new ones stay over cross_new
    # for cross_runs checks running. The tucks of a trouser front went through its held waistband this way
    # (user 2026-10-09) while the entangle count, which looks only at seams, stayed low
    G_CROSS_NEW, G_CROSS_RUNS = int(GUARD.get('cross_new', 20)), int(GUARD.get('cross_runs', 3))
    G_UNT, G_XBASE, G_XRUN, G_XFLAG = None, None, 0, False
    try:
        if model.tri_count == len(F):
            # (set up before the stage's own untangle, so a detect-only one of its own)
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from untangle import Untangler as _GU
            G_UNT = _GU(solver, F, d['fp'], N_G, {'detect_only': True, 'skip_pieces': GHOST_P})
    except Exception as _ge:
        log['guard_cross_error'] = str(_ge)[:200]
    _gtp = np.asarray(d['fp'], np.int64)
    G_CLOSED = np.zeros(len(_gE), bool)
    G_ACTIVE = np.array([k not in _gidle for k in range(len(_gE))], bool)
    G_REOPEN_RUN = 0
    log['guard'] = {'checks': len(G_CHECKS), 'every': G_EVERY, 'far_mm': G_FAR, 'near_mm': G_NEAR, 'trace': []}
    # fold kept: per fold line with a set angle, the median hinge angle every check. A line counts as folded once
    # its median comes within fold_reach_deg of the set angle; after that (and after the fold ramp), off by more than
    # fold_keep_deg for two checks running is recorded as 'unfolded'. The tucks of a trouser reached their angles
    # folding and opened to 15-70 deg once hung and sewn, with nothing in the guard to say so (user 2026-10-09)
    G_FOLD = []
    if 'FOLD_HINGES' in globals() and FOLD_HINGES:
        _gei = model.edge_indices.numpy()
        for _k, (_ids, _deg) in FOLD_HINGES.items():
            G_FOLD.append({'key': _k, 'H': _gei[_ids], 'set': _deg, 'reached': False, 'off': 0, 'flag': False})
        log['guard']['fold'] = []
    G_FREACH = float(GUARD.get('fold_reach_deg', 20.0))
    G_FKEEP = float(GUARD.get('fold_keep_deg', 30.0))

    def _gname(i_):
        return str(pat_names[i_]) if pat_names is not None and 0 <= i_ < len(pat_names) else str(i_)
# cfg 'jitter' (2026-10-15, on with the guard by default; record only, never stops -- 10-06): vertices that keep
# moving differently from their neighbours, what a frame-by-frame look picks out (a collar end went back and forth
# to the last frame while the median and the p99 speed said "settled", chats/test-qa.md). Per frame, from this frame's
# and the last frame's positions only:
#   rel   |d - mean of the one-ring neighbours' d|, d = this frame's move (mm): a region sliding as a whole cancels
#   flick the mean turn of the vertex's face normals this frame (deg): the shading flickering
# both smoothed (alpha, ~1/alpha frames). Flagged: rel >= rel_mm or flick >= flick_deg, not during the stitch ramp,
# the delayed seams and `skip` frames after them. At the stage's end the flagged vertices are joined along mesh edges
# into clusters: 'end' = flagged in at least half of the last `tail` frames (still jittering when the stage ended),
# 'stage' = in at least half of the judged frames. Measured (experiments/jitter_detector_2026-10-15): about 0.9 ms a
# frame (the solve ~78 ms); on the raglan with the 0.1x collar the collar-end cluster came first in stages 2, 4, 5
# and 6. In the stages that move a lot with no gravity (arms out, sleeves) many vertices are flagged: they move.
# How to read it: docs/reference/GUIDE_detectors_for_ai.md (2609_Apparel_CAD)
JIT = None
if cfg.get('jitter'):
    J_ = cfg['jitter'] if isinstance(cfg['jitter'], dict) else {}
    _jF = np.asarray(F, np.int64)
    if len(_jF) and int(_jF.max()) < len(X0):
        from mesh_graph import mesh_averages
        _jN = len(X0)
        _jA, _jVF = mesh_averages(_jF, _jN)
        JIT = {'rel_mm': float(J_.get('rel_mm', 0.5)), 'flick_deg': float(J_.get('flick_deg', 4.0)),
               'alpha': float(J_.get('alpha', 0.1)), 'tail': int(J_.get('tail', 30)),
               'F': _jF, 'A': _jA,
               'M': _jA,
               'VF': _jVF,
               'rel': np.zeros(_jN), 'flk': np.zeros(_jN), 'n': None, 'ever': np.zeros(_jN, np.int32),
               'ring': np.zeros((int(J_.get('tail', 30)), _jN), bool), 'judged': 0,
               'first': np.full(_jN, -1, np.int32), 'trace': [], 's': 0.0,
               'from': max([RAMP + int(J_.get('skip', 10))]
                           + [st + n_ + int(J_.get('skip', 10)) for _, st, n_ in DELAY if st < frames])}

        def _jit_normals(X_):
            n_ = np.cross(X_[JIT['F'][:, 1]] - X_[JIT['F'][:, 0]], X_[JIT['F'][:, 2]] - X_[JIT['F'][:, 0]])
            return n_ / (np.linalg.norm(n_, axis=1, keepdims=True) + 1e-12)

        def _jit_clusters(mask, score):
            ix_ = np.nonzero(mask)[0]
            if not len(ix_):
                return []
            nc_, lab_ = JIT['A'].components(mask)
            _vp = v_piece[sel]
            out_ = []
            for c_ in range(nc_):
                v_ = ix_[lab_ == c_]
                w_ = int(v_[np.argmax(score[v_])])
                _fs = JIT['first'][v_]
                out_.append({'n': int(len(v_)), 'pieces': sorted({str(pat_names[p_]) if pat_names else str(p_)
                                                                  for p_ in _vp[v_]}),
                             # vertex numbers of the garment mesh (the job's full order, as the .blend); a stage
                             # simulating some pieces only numbers its own vertices differently ('worst_sim')
                             'worst': int(sel[w_]), 'worst_sim': w_,
                             'rel_mm': round(float(JIT['rel'][w_]), 3), 'flick_deg': round(float(JIT['flk'][w_]), 2),
                             'first_frame': int(_fs[_fs >= 0].min()) if (_fs >= 0).any() else None,
                             'verts': [int(sel[x_]) for x_ in v_[:40]]})
            return sorted(out_, key=lambda r_: (-r_['n'], -r_['rel_mm']))
        log['jitter'] = {'rel_mm': JIT['rel_mm'], 'flick_deg': JIT['flick_deg'], 'alpha': JIT['alpha'],
                         'tail': JIT['tail'], 'from': JIT['from']}
    else:
        log['jitter'] = {'error': 'faces do not index the simulated vertices'}
# cfg 'untangle' (test option, default off): cloth through cloth taken back out at the end of each frame, see
# untangle.py. {"every": 1, "min_depth_mm": 0.45, "gap_mm": 0.45, "max_region": 60, "from": <frame>,
# "detect_only": false}; by default it starts once the stitch ramp and the delayed seams are over (moving a band
# while the stitches' rest lengths still change fights them, Fable review 5-3)
UNT = None
if cfg.get('untangle'):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from untangle import Untangler
    UT_ = cfg['untangle'] if isinstance(cfg['untangle'], dict) else {}
    if model.tri_count != len(F):
        raise SystemExit(f'untangle: the model has {model.tri_count} triangles, the scene {len(F)}')
    _ufold = None
    if cfg.get('pattern') and WELD is None:
        _pz = np.load(cfg['pattern'])
        if 'Lf0' in _pz:
            _ufold = np.concatenate([_pz[f"Lf{i}"] for i in range(len(_pz['names']))])[sel]
    if GHOST_P:
        UT_ = dict(UT_, skip_pieces=GHOST_P)       # cloth through a ghost is no crossing to undo
    UNT = Untangler(solver, F, d['fp'], N_G, UT_, flat=(d['flat'] if WELD is None else None), fold=_ufold, body=Bv, stitched=E)
    UNT_FROM = (int(UT_['from']) if UT_.get('from') is not None
                else max([RAMP + 1] + [st + n_ + 1 for _, st, n_ in DELAY if st < frames]))
    UNT_EVERY = int(UT_.get('every', 1))
    log['untangle'] = UNT.log
    # the first-contact sides carry over between the stages of one job (one file next to the stage folders)
    UNT_MEM = os.path.join(os.path.dirname(os.path.abspath(out)), 'untangle_memory.json')
    if WELD is None:
        UNT.load_memory(UNT_MEM, sel)
    log['untangle_from'] = UNT_FROM
RL = L0s.copy() if (RAMP and model.spring_count) else (np.zeros(model.spring_count, np.float32) if model.spring_count else None)
N_ST = len(E)                                   # the first springs are the pattern stitches (added first)
if SREST and model.spring_count:
    _r = model.spring_rest_length.numpy(); _r[:N_ST] = np.maximum(_r[:N_ST], SREST); model.spring_rest_length.assign(_r)
    if RL is not None:
        RL[:N_ST] = np.maximum(RL[:N_ST], SREST)
LOCK = cfg.get('stitch_lock') if model.spring_count else None      # test option, {"mm": 1.0}; see the frame loop
# cfg 'stitch_cap_N' (2026-10-16, DECISIONS cloth-sim-all-stage-coulomb-2026-10-16) -- no pattern stitch pulls harder than
# this (N): every frame its rest length is raised to at least (length - cap / ke), so a far seam is reeled in at a
# bounded force instead of ke x the whole gap
STCAP = float(cfg['stitch_cap_N']) if cfg.get('stitch_cap_N') and model.spring_count else None
if STCAP:
    _SI_CAP = model.spring_indices.numpy().reshape(-1, 2)[:N_ST]
    log['stitch_cap'] = {'N': STCAP, 'capped_frames': 0, 'capped_max': 0}
if LOCK is not None:
    LOCK = LOCK if isinstance(LOCK, dict) else {}
    # within mm of the stage's target length (stitch_rest_mm): with an absolute 1.0 mm the stitches of a 1 mm target
    # stage mostly never came within it (shirt: 206 of 559 locked), "relative": false keeps the absolute
    LOCK_MM = float(LOCK.get('mm', 1.0)) / 1000.0 + (SREST if LOCK.get('relative', True) else 0.0)
    LOCK_E = model.spring_indices.numpy().reshape(-1, 2)[:N_ST]
    LOCK_F = -np.ones(N_ST, np.int32)             # frame each stitch locked at (-1: not yet)
    LOCK_L = np.zeros(N_ST, np.float32)           # its length then (its rest length from then on)
    # "weld": true (user 2026-10-07, the idea as meant: the two become one point): an arrived stitch's ends are put
    # on one point at the end of every frame (vertices joined by several stitches, a corner where three pieces
    # meet, all on one), weighted by inverse mass so a held piece's end stays put; rest length 0
    LOCK_WELD = bool(LOCK.get('weld', False))
    LOCK_GROUPS = None

    def _weld_groups():
        par = {}

        def root(x):
            while par.get(x, x) != x:
                par[x] = par.get(par[x], par[x]); x = par[x]
            return x
        for e_ in np.nonzero(LOCK_F >= 0)[0]:
            a_, b_ = root(int(LOCK_E[e_, 0])), root(int(LOCK_E[e_, 1]))
            if a_ != b_:
                par[a_] = b_
        vs = np.array(sorted(par.keys() | set(par.values())), np.int64)
        gid = np.array([root(int(v)) for v in vs], np.int64)
        _, gid = np.unique(gid, return_inverse=True)
        return vs, gid
DL0 = {}
# mirror_map: every frame the left half is set to the mirror image of the right half (x -> 2*x0 - x), from a
# map made by prep/symmetrize.py (--map). Symmetric at every step, like a half model with a symmetry plane;
# copying once at the end drifted apart again when the result was settled (mirror diff 1.1 -> 3.4 mm).
MIRROR = None
if SYM_COLOR_LOG is not None:
    log['sym_color'] = SYM_COLOR_LOG
if cfg.get('mirror_map') and cfg.get('mirror_every_frame', True):
    _m = np.load(cfg['mirror_map'])
    keep = (remap[_m['tgt']] >= 0) & (remap[_m['src']] >= 0).all(1) if SUBSET else np.ones(len(_m['tgt']), bool)
    M_tgt = (remap[_m['tgt'][keep]] if SUBSET else _m['tgt'][keep]).astype(np.int64)
    M_src = (remap[_m['src'][keep]] if SUBSET else _m['src'][keep]).astype(np.int64)
    M_w = _m['w'][keep].astype(np.float32); M_x0 = float(cfg.get('mirror_x0', 0.0)) / 1000.0
    MIRROR = True
    _tset = set(M_tgt.tolist())
    M_pairs = np.array([(a_, b_) for a_, b_ in E if int(a_) in _tset and int(b_) in _tset], np.int64).reshape(-1, 2)
    # source vertices = all vertices of the pieces the map copies from (the +x halves)
    _srcp = np.unique(v_piece[sel][np.unique(M_src)])
    M_srcv = np.array([v for v in np.nonzero(np.isin(v_piece[sel], _srcp))[0] if v not in _tset], np.int64)
    _st_t = {}                                            # source vertices stitched to a copied vertex = on the plane
    for a_, b_ in E:
        if (int(a_) in _tset) != (int(b_) in _tset):
            _st_t[int(b_) if int(a_) in _tset else int(a_)] = True
    _self_src = set(np.unique(M_src).tolist())            # vertices used as sources inside self-symmetric pieces
    M_onplane = np.array([(v in _st_t) for v in M_srcv])
    M_gap = float(cfg.get('mirror_gap_mm', 1.0)) / 1000.0
    M_ONPLANE = float(cfg.get('mirror_onplane_mm', 0.25)) / 1000.0
    log['mirror'] = int(len(M_tgt)); log['mirror_seam_pairs'] = int(len(M_pairs))
LIVE_EVERY = int(cfg.get('live_every', 5))
# grab (a person pulling the cloth while it runs; the add-on's interaction is not designed yet, this is the solver
# side): {"verts": [full-garment vertex ids], "target": [x, y, z] m} in <out>/grab.json, read every frame, or
# cfg 'grab_script' [{"from": f0, "to": f1, "verts": [...], "target": [...]}] for tests. Grabbed vertices are held
# (no mass) and carried towards the target at most GRAB_STEP m per frame, keeping their shape; released when the
# file is gone (or the script's frames are over).
GRAB_SCRIPT = cfg.get('grab_script', [])
GRAB_STEP = float(cfg.get('grab_step', 0.005))
# the grab through (see FREE): "through": true in grab.json / a grab_script entry; X is searched within THROUGH_D m
# of the grab and kept freed at most THROUGH_K frames after the release
THROUGH_D = float(cfg.get('through_d_mm', 100.0)) / 1000.0
THROUGH_K = int(cfg.get('through_k', 60))
_through = None                                # {'piece', 'center', 'until', 'held'} while X is freed
log['grab_through'] = []                       # [frame, G vertices, X vertices, crossing edges]
_full2loc = -np.ones(N_FULL, np.int64)
if WELD is None:
    _full2loc[sel] = np.arange(len(sel))
else:
    _full2loc[SEL_OUT] = W_MAP
if BIND is not None and BIND_MODE in ('removed', 'hybrid'):
    # a bound vertex grabbed (Ctrl + drag on the layer taken out): its triangle's heaviest corner is held instead
    _bfree = (_full2loc[BIND_V] < 0) | np.isin(BIND_V, OL_KIN)
    _full2loc[BIND_V[_bfree]] = _full2loc[BIND[0][_bfree, np.argmax(BIND[1][_bfree], axis=1)]]
_grab = None                                   # (local ids, offsets, saved mass, saved inv mass)
KIN_ON = BIND is not None and (BIND_MODE in ('kinematic', 'hybrid') or len(OL_KIN) > 0)
if KIN_ON:
    _loc = remap if SUBSET else np.arange(N_FULL)
    _bk = (np.ones(len(BIND_V), bool) if BIND_MODE == 'kinematic'
           else np.isin(BIND_V, ANCH) | np.isin(BIND_V, OL_KIN))
    BL_V = _loc[BIND_V[_bk]]; BL_T = _loc[BIND[0][_bk]]; BL_W, BL_D = BIND[1][_bk], BIND[2][_bk]
    if (BL_V < 0).any() or (BL_T < 0).any():
        raise SystemExit('bind_verts: a flap or its triangles are outside the active pieces')
    pm, pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
    pm[BL_V] = 0.0; pim[BL_V] = 0.0
    model.particle_mass.assign(pm); model.particle_inv_mass.assign(pim)
    _isb = np.zeros(model.particle_count, bool); _isb[BL_V] = True
    tm = model.tri_materials.numpy(); _ti = model.tri_indices.numpy()
    _tb = _isb[_ti].any(1); tm[_tb, 0:3] = 0.0; model.tri_materials.assign(tm)
    bp = model.edge_bending_properties.numpy(); _ei = model.edge_indices.numpy()
    _eb = (_isb[np.clip(_ei, 0, None)] & (_ei >= 0)).any(1); bp[_eb, :] = 0.0; model.edge_bending_properties.assign(bp)
    BIND_LOG.update({'kinematic': int(len(BL_V)), 'tris_off': int(_tb.sum()), 'hinges_off': int(_eb.sum())})
    log['bind_verts'] = BIND_LOG

    def _place_flap(state):
        q_ = state.particle_q.numpy().astype(np.float64)
        q_[BL_V] = _bound_at(BL_T, BL_W, BL_D, q_)
        v_ = state.particle_qd.numpy(); v_[BL_V] = 0.0
        state.particle_q.assign(q_.astype(np.float32)); state.particle_qd.assign(v_)
# Strengthen changes constitutive stiffness only, after fabric/fold/bind setup.
# Ratio updates preserve independent elastic changes and the current hysteresis history.
STRENGTHEN = {}
STRENGTHEN_TERMINAL_DIRTY = False

def _set_strengthen(values, live=False):
    global STRENGTHEN, STRENGTHEN_TERMINAL_DIRTY
    new = {n: float(v) for n, v in values.items() if n in pat_names}
    if any(not np.isfinite(v) or v < 1.0 or v > 100.0 for v in new.values()):
        raise ValueError('AC9 Strengthen: multiplier must be finite and between 1 and 100')
    changed = {n for n in set(new) | set(STRENGTHEN) if new.get(n, 1.) != STRENGTHEN.get(n, 1.)}
    if changed:
        tm = model.tri_materials.numpy()
        bp = model.edge_bending_properties.numpy()
        ei = model.edge_indices.numpy()
        vp = v_piece[sel]
        ratios = np.ones(len(bp), np.float32)
        for n in changed:
            ratio = new.get(n, 1.) / STRENGTHEN.get(n, 1.)
            pi = pat_names.index(n)
            rows = np.nonzero(d['fp'] == pi)[0]
            tm[np.ix_(rows, [0, 1, 3] if MEMBRANE_BW else [0, 1])] *= ratio
            mask = (ei[:, 2] >= 0) & (vp[np.maximum(ei[:, 2], 0)] == pi)
            ratios[mask] = ratio
        bp[:, 0] *= ratios
        if NATIVE_COLOR_PLAN is not None:
            NATIVE_COLOR_PLAN.validate_tri_materials(tm)
            NATIVE_COLOR_PLAN.validate_edge_materials(bp)
        model.tri_materials.assign(tm)
        model.edge_bending_properties.assign(bp)
        if BF2 is not None and BF2['f_on'] is not None:
            BF2['k0'].assign(BF2['k0'].numpy() * ratios[BF2['r_np']])
        if live and cfg.get('experimental_fold_terminal'):
            # The terminal checkpoint manifest describes immutable initial stiffness.
            # Keep the visible result/settings, but rebuild on the next Live after an edit.
            STRENGTHEN_TERMINAL_DIRTY = True
    STRENGTHEN = new
    return sorted(changed)

_set_strengthen(cfg.get('strengthen_pieces') or {})
if STRENGTHEN:
    log['strengthen_pieces'] = dict(STRENGTHEN)
if NATIVE_COLOR_PLAN is not None:
    log['native_three']['excluded_validation'] = NATIVE_COLOR_PLAN.validate_excluded()

# the stage-start check (solver/start_check.py, user 2026-10-19): the pieces joining in this stage, where they are
# before frame 1 -- inside the body, through other cloth, sewn through other cloth, or another job's frames. Dress
# warns and goes on; any other caller stops here unless env AC9_START_CHECK_ALLOW gives a reason
_RESUME_FRAME = 0
_RESUME_FIRST = None
if cfg.get('experimental_fold_terminal'):
    from terminal_checkpoint import build as _build_terminal_checkpoint
    TERMINAL_INITIAL_PROPS=model.edge_bending_properties.numpy().copy()
    TERMINAL_FIELD_STATE=_build_terminal_checkpoint(globals())
from live_checkpoint import build_signature as _checkpoint_build_signature
_trace('checkpoint_build_signature_begin')
_CHECKPOINT_BUILD = _checkpoint_build_signature(globals())
_trace('checkpoint_build_signature_end')
from live_checkpoint import fingerprint as _checkpoint_fingerprint
_trace('checkpoint_fingerprint_begin')
_CHECKPOINT_FINGERPRINT = _checkpoint_fingerprint(globals())
_trace('checkpoint_fingerprint_end')
if cfg.get('checkpoint_in'):
    if not cfg.get('live'):
        raise ValueError('A Live checkpoint can only resume a Live stage')
    # Rebind fresh detector resources to the same state buffer used after accepted steps.
    # Validation still rejects any unexpected alias/view layout within the numeric state.
    from live_checkpoint import restore as _restore_live_checkpoint, detector_root as _detector_root
    # the solver makes its empty layer arrays at the first detection: a Live build has run one while capturing its
    # graph, a Fast Live build (no graph) not yet, and restore() needs every saved array to have its binding
    if hasattr(solver, '_ac9_layer_arrays'):
        solver._ac9_layer_arrays()
    if getattr(solver, 'trimesh_collision_detector', None) is not None:
        solver.trimesh_collision_detector.refit((s1 if _detector_root(cfg['checkpoint_in']) == 's1' else s0).particle_q)
    _resume_meta = _restore_live_checkpoint(cfg['checkpoint_in'], globals())
    _RESUME_FRAME = int(_resume_meta['frame'])
    _RESUME_FIRST = s0.particle_q.numpy()[:N_G].copy()
    log['checkpoint_resume'] = {'status': 'restored', 'frame': _RESUME_FRAME}
_trace('start_check_begin')
if cfg.get('start_check', True):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import start_check as _SC
    _scm = wp.Mesh(points=wp.array(Bv, dtype=wp.vec3), indices=wp.array(Bt.astype(np.int32), dtype=wp.int32))

    # faces on the body's open edges (the crop, the arms left out in a jacket's stage 1) are not judged: next to a
    # hole the nearest face's normal says inside for cloth that is outside (110 false on a jacket, 2026-10-19)
    _sce = np.sort(np.r_[Bt.reshape(-1, 3)[:, [0, 1]], Bt.reshape(-1, 3)[:, [1, 2]], Bt.reshape(-1, 3)[:, [2, 0]]], 1)
    _sceu, _scec = np.unique(_sce, axis=0, return_counts=True)
    _scbv = np.zeros(len(Bv), bool); _scbv[_sceu[_scec == 1].ravel()] = True
    _scopen = wp.array(_scbv[Bt.reshape(-1, 3)].any(1).astype(np.int32), dtype=int)

    @wp.kernel(module="unique")
    def _sc_inside(m: wp.uint64, xs: wp.array(dtype=wp.vec3), opn: wp.array(dtype=int), out_s: wp.array(dtype=int)):
        i = wp.tid()
        p = xs[i]
        out_s[i] = 0
        q = wp.mesh_query_point_sign_normal(m, p, 0.05)
        if q.result:
            if opn[q.face] == 0:
                cp = wp.mesh_eval_position(m, q.face, q.u, q.v)
                if wp.dot(p - cp, wp.mesh_eval_face_normal(m, q.face)) < -0.0005:
                    out_s[i] = 1

    def _sc_body(P):
        o_ = wp.zeros(len(P), dtype=int)
        wp.launch(_sc_inside, dim=len(P), inputs=[_scm.id, wp.array(P.astype(np.float32), dtype=wp.vec3), _scopen,
                                                  o_])
        return o_.numpy().astype(bool)
    _sc_unt = G_UNT if GUARD else None
    if _sc_unt is None and model.tri_count == len(F):
        try:
            from untangle import Untangler as _SCU
            _sc_unt = _SCU(solver, F, d['fp'], N_G, {'detect_only': True, 'skip_pieces': GHOST_P})
        except Exception as _sce:
            log['start_check_cross_error'] = str(_sce)[:200]
    _sc_idle = set()
    for _ids, _st, _n in DELAY:
        if _st >= frames:
            _sc_idle.update(int(x) for x in np.asarray(_ids).ravel())
    _sc_rep, _sc_act = _SC.check(cfg, out, s0.particle_q.numpy()[:N_G], F, np.asarray(d['fp'], np.int64),
                                 v_piece[sel], pat_names, E, _sc_idle, _sc_body, _sc_unt, SEAM_TRI, s0.particle_q)
    if _RESUME_FRAME and _sc_act == 'stop':
        # A verified accepted checkpoint continues the same calculation. Its geometry
        # diagnostics are retained; finite state, construction, and provenance were validated above.
        _sc_rep['initial_action'] = _sc_act
        _sc_rep['action'] = 'diagnostic_checkpoint'
        _sc_rep['continued_from_frame'] = _RESUME_FRAME
        _sc_act = 'warn'
    if STRETCHED:
        _sc_rep['stretched'] = {k_: [c_, round(m_, 1)] for k_, (c_, m_) in STRETCHED.items()}
        _sc_rep.setdefault('problems', []).append(
            'edges over 3x their pattern length: ' + ', '.join(f"{k_} {c_} (longest {m_:.0f} mm)"
                                                               for k_, (c_, m_) in STRETCHED.items()))
    log['start_check'] = _sc_rep
    print('start_check', _sc_act, _sc_rep.get('problems'), _sc_rep.get('notes'), flush=True)
    if _sc_act == 'stop':
        log['error'] = 'start check: ' + '; '.join(_sc_rep['problems'])
        res = res[:1]
        frames = 0
_trace('start_check_end')
_CHECKPOINT_ORIGIN = (_resume_meta['start_origin'] if _RESUME_FRAME else
                      {'accepted': 'error' not in log, 'start_check': log.get('start_check'),
                       'override': os.environ.get('AC9_START_CHECK_ALLOW'),
                       'caller': 'addon' if os.environ.get('AC9_DRESS')=='1' else 'direct'})
# Private buffers/graphs must see the final restored model, never pre-CP copies.
if FAST_LIVE and 'error' not in log:
    try:
        _unsupported = [name for name in ('DRIVE','CN','UNT','GATE','LOCK','MIRROR','BIND','ATTACH','DEEP','PATH')
                        if globals().get(name) is not None and not (name == 'BIND' and (os.environ.get('AC9_TEST_FAST_LAYERS', '1') != '0'))
                        and not (name in ('ATTACH', 'PATH') and (os.environ.get('AC9_TEST_FAST_E5', '1') != '0'))]
        if _unsupported:raise ValueError('AC9 Fast Live unsupported runtime: '+', '.join(_unsupported))
        from fast_live.runtime import install as _install_fast_live
        _FAST_RUNTIME = _install_fast_live(model=model,solver=solver,cfg=cfg,state=s0,
            pipe=pipe,contacts=contacts,dt=dt,frame=lambda:f,output=os.path.join(out,'fast_live'),n_stitch=N_ST,
            grab=GRB if GRAB_MODE != 'rigid' else None, bf2=BF2,
            attach=ATTACH if (os.environ.get('AC9_TEST_FAST_E5', '1') != '0') else None)
        _FAST_SUBSTEP = _FAST_RUNTIME.begin_substep
        if '_AC9_CLEANUP' in globals():
            _AC9_CLEANUP(_FAST_RUNTIME.close)
        log['fast_live'] = {'contract':cfg['fast_live_contract'],'status':'ready'}
    except Exception as _fast_error:
        log['error']='AC9 Fast Live pre-step: '+str(_fast_error)
        log['checkpoint']={'status':'unavailable','reason':'Fast Live initialization failed'}
        res=res[:1];frames=0
# [frame, mm]: springs (default) the held centroid's distance to the target at the frame's start; rigid the distance
# left after the frame's move; [frame, 'released']
log['grab'] = []
log['stroke'] = []                    # [frame, verts, move mm, own crossing edges, held hinges] / 'released'
if _PREPARED_GATE is not None:
    _PREPARED_GATE(globals())
_tmark('loop_start')
t_all = time.perf_counter()
f = _RESUME_FRAME
_trace('loop_ready')
for f in range(_RESUME_FRAME + 1, frames + 1):
    _trace('frame_begin', frame=f)
    if f == _RESUME_FRAME + 1:
        _trace('first_frame_begin', frame=f)
    t0 = time.perf_counter()
    upd = False
    if LIVEP is not None:
        try:
            _live_params(f)
        except Exception as _fast_error:
            if not FAST_LIVE:raise
            log['error']='AC9 Fast Live update rejected: '+str(_fast_error)
            log['checkpoint']={'status':'unavailable','reason':'Fast Live input update rejected'}
            res=res[:f];break
    if RAMP and model.spring_count and f <= RAMP + 1:
        RL = L0s * max(0.0, 1.0 - (f - 1) / RAMP); upd = True
    for k_, (ids, st, n_) in enumerate(DELAY):
        # frames count from 1, so "start": 0 comes on at frame 1 (until 2026-10-16 `f == st` never fired for it: a
        # sleeved garment's underarm seams stayed open 34 mm through stage 5 and shut at once in stage 6, the sleeves
        # jumping 26-29 mm; with this 3 mm and 3-7 mm, T-shirt, experiments/seam_open_B_2026-10-16)
        if f == max(st, 1):
            _q = s0.particle_q.numpy(); _si = model.spring_indices.numpy().reshape(-1, 2)
            DL0[k_] = np.linalg.norm(_q[_si[ids, 0]] - _q[_si[ids, 1]], axis=1).astype(np.float32)
            ke = model.spring_stiffness.numpy(); ke[ids] = KE0[ids]; model.spring_stiffness.assign(ke)
            log.setdefault('stitch_delay_gap_mm', []).append(round(float(np.median(DL0[k_])) * 1000, 1))
        if k_ in DL0 and st <= f <= st + n_ + 1:
            RL = RL.copy(); RL[ids] = DL0[k_] * max(0.0, 1.0 - (f - st) / max(n_, 1)); upd = True
    if MIRROR is not None and f > 1:
        _q = s0.particle_q.numpy(); _v = s0.particle_qd.numpy()
        # the source half must not cross the plane: its copy would then overlap it by construction (the fronts
        # met at the centre: 105 pairs). Seam/centre vertices may sit on the plane, others stay M_gap off it.
        # seam vertices on the plane stop just short of it: at exactly x0 a vertex meets its own copy and the
        # contact distance is 0 (2 of 20 zero-gravity runs of a bodice ended non-finite, Fable review r4)
        _xm = _q[M_srcv, 0]; _lim = M_x0 + np.where(M_onplane, M_ONPLANE, M_gap)
        _bad = _xm < _lim
        # source vertices held back from the centre plane this frame: what would pull the +x half across it
        # (with the copy in place a front crossing the centre no longer shows in the result)
        log.setdefault('mirror_clamped', []).append(int(_bad.sum()))
        if _bad.any():
            _q[M_srcv[_bad], 0] = _lim[_bad]; _v[M_srcv[_bad], 0] = np.maximum(_v[M_srcv[_bad], 0], 0.0)
        for arr in (_q, _v):
            P_ = (arr[M_src] * M_w[:, :, None]).sum(1)
            arr[M_tgt] = P_ * np.array([-1.0, 1.0, 1.0], np.float32) + np.array([2 * M_x0 if arr is _q else 0.0, 0, 0], np.float32)
        # a seam between two mirrored pieces (left front x left side panel): each is copied through its own
        # outline fit (up to ~3 mm apart), so the copies miss each other; put both ends on their midpoint. Off by
        # default: with mirror-meshed twins (one-to-one) the copies already meet, and two vertices put on exactly the
        # same point every frame took a bodice non-finite in 3 of 40 zero-gravity runs (0 of 20 without, 2026-09-29)
        if len(M_pairs) and cfg.get('mirror_seam_mid', False):
            for arr in (_q, _v):
                mid = 0.5 * (arr[M_pairs[:, 0]] + arr[M_pairs[:, 1]]); arr[M_pairs[:, 0]] = mid; arr[M_pairs[:, 1]] = mid
        s0.particle_q.assign(_q); s0.particle_qd.assign(_v)
    if LOCK is not None and f > 1:
        # stitch_lock: a stitch that has come within lock_mm takes the length it has now as its rest length and
        # keeps it (neither closer nor apart, user 2026-10-07); a locked seam then pulls only when something pulls
        # it open, so its stretch beyond the locked length is an anomaly whatever the garment
        _q = s0.particle_q.numpy()
        _Lc = np.linalg.norm(_q[LOCK_E[:, 0]] - _q[LOCK_E[:, 1]], axis=1)
        _new = (LOCK_F < 0) & (model.spring_stiffness.numpy()[:N_ST] > 0) & (_Lc <= LOCK_MM)
        if _new.any():
            LOCK_F[_new] = f; LOCK_L[_new] = 0.0 if LOCK_WELD else _Lc[_new]; upd = True
            if LOCK_WELD:
                LOCK_GROUPS = _weld_groups()
    if ELASTIC:
        _elastic_at(f)
    if FOLD_RAMP is not None and f <= FOLD_RAMP[3] + 1:
        _ids, _a0, _tgt, _n = FOLD_RAMP
        _ra = model.edge_rest_angle.numpy()
        _ra[_ids] = _a0 + (_tgt - _a0) * min(1.0, f / max(_n, 1))
        model.edge_rest_angle.assign(_ra)
    if DRIVE and any(D_['state'] == 'driving' for D_ in DRIVE):
        _pm, _pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
        if f == 1:
            for D_ in DRIVE:
                D_['saved'] = (_pm[D_['far']].copy(), _pim[D_['far']].copy())
                _pm[D_['far']] = 0.0; _pim[D_['far']] = 0.0
            model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
        _q = s0.particle_q.numpy().astype(np.float64); _v = s0.particle_qd.numpy()
        _chg = False
        for D_ in DRIVE:
            if D_['state'] != 'driving':
                continue
            _new, _ = FT.turn_step(_q, D_['roll'], D_['far'], D_['J'], D_['Tt'], D_['dphi'])
            _qc = _q.copy(); _qc[D_['far']] = _new
            # a contact = within stop_mm AND coming closer than the frame before: some far vertices already lie on
            # (or 2-5 mm in) the bodice when the stage starts (2 of 3 runs stopped at frame 1); those stop the drive
            # only if the turn would take them further in
            if 'sd0' not in D_:
                _s0h, _, _, _, _ok0, _ = _layer_sides(_q, D_['chk'], DRIVE_HELD[0], DRIVE_HELD[1])
                D_['sd0'] = (np.where(_ok0, _s0h, np.inf), _drive_body(_q[D_['chk']]))
                log['fold_turn'].setdefault('drive_start_contacts', []).append(
                    {'piece': D_['piece'], 'held': int(((D_['sd0'][0] < DRIVE_STOP) & (D_['sd0'][0] > -DRIVE_REACH)).sum()),
                     'body': int(((D_['sd0'][1] < DRIVE_STOP) & (D_['sd0'][1] > -DRIVE_REACH)).sum())})
            _sd, _, _, _, _ok, _ = _layer_sides(_qc, D_['chk'], DRIVE_HELD[0], DRIVE_HELD[1])
            _sb = _drive_body(_qc[D_['chk']])
            _ph, _pb = D_['sd0']
            _hh = _ok & (_sd < DRIVE_STOP) & (_sd > -DRIVE_REACH) & (_sd < _ph - 1e-5)
            _hb = (_sb < DRIVE_STOP) & (_sb > -DRIVE_REACH) & (_sb < _pb - 1e-5)
            _why = 'hit_bodice' if _hh.any() else ('hit_body' if _hb.any() else None)
            if _why is None:
                D_['tip_mm'] = max(D_.get('tip_mm', 0.0),
                                   float(np.linalg.norm(_new - _q[D_['far']], axis=1).max()) * 1000)
                _q = _qc; _v[D_['far']] = 0.0; _chg = True
                D_['steps'] += 1
                D_['sd0'] = (np.where(_ok, _sd, np.inf), _sb)
                if D_['meas'](_q) >= D_['tgt'] - 1.0:
                    _why = 'reached'
                elif D_['steps'] >= D_['n'] * 3 // 2:
                    _why = 'steps_out'
            if _why is not None:
                D_['state'] = 'stopped'
                _hit = np.nonzero(_hh if _why == 'hit_bodice' else _hb)[0] if _why.startswith('hit') else []
                _dd = _sd if _why == 'hit_bodice' else _sb
                _pm[D_['far']], _pim[D_['far']] = D_['saved']
                model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
                log['fold_turn'].setdefault('drive', []).append({
                    'piece': D_['piece'], 'why': _why, 'frame': f, 'steps': D_['steps'], 'planned': D_['n'],
                    'up_deg': round(D_['meas'](_q), 1), 'target_up_deg': round(D_['tgt'], 1),
                    'hinge_deg_p50': round(float(np.degrees(np.median(_dihedral(_q, D_['H'])))), 1),
                    'tip_mm_per_frame_max': round(D_.get('tip_mm', 0.0), 2), 'hit_verts': int(len(_hit)),
                    'first_hits': [[int(sel[D_['chk'][i_]]), round(float(_dd[i_]) * 1000, 2)] for i_ in _hit[:3]]})
        if _chg:
            s0.particle_q.assign(_q.astype(np.float32)); s0.particle_qd.assign(_v)
    if FOLD_CARRY is not None and f <= FOLD_CARRY[1] + 1:
        # fold_turn carry: the far side held (no mass) and turned a little more each frame, from the stage's start
        # shape (the near side simulates meanwhile); let go after the last step
        _lines, _n = FOLD_CARRY
        _pm, _pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
        if f == 1:
            FOLD_CARRY_SAVED = [(_far, _pm[_far].copy(), _pim[_far].copy()) for _far, _, _, _ in _lines]
            for _far, _, _, _ in _lines:
                _pm[_far] = 0.0; _pim[_far] = 0.0
            model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
        if f <= _n:
            _s = f / _n; _s = _s * _s * (3 - 2 * _s)
            _q = s0.particle_q.numpy(); _v = s0.particle_qd.numpy()
            for _far, _app, _phi, _ in _lines:
                _q[_far] = _app(_phi * _s)[_far]; _v[_far] = 0.0
            s0.particle_q.assign(_q); s0.particle_qd.assign(_v)
        else:
            for _far, _m0, _im0 in FOLD_CARRY_SAVED:
                _pm[_far] = _m0; _pim[_far] = _im0
            model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
            _q = s0.particle_q.numpy()
            log['fold_turn']['carry_released'] = {'frame': f, 'deg': [round(float(np.degrees(np.median(
                _dihedral(_q.astype(np.float64), _H_)))), 1) for _, _, _, _H_ in _lines]}
    if PATH is not None and (~PATH['done']).any():
        _sp_q = s0.particle_q.numpy().astype(np.float64)
        _sp_a, _sp_b = _sp_q[PATH['A']], _sp_q[PATH['B']]
        _, _sp_L, _sp_dth = _path_arc(PATH, _sp_a, _sp_b, np.zeros(len(_sp_a)))
        _sp_now = (~PATH['done']) & ((_sp_L < PATH['sw']) | (np.abs(_sp_dth) > 1.8 * np.pi) | (f > PATH['n']))
        if _sp_now.any():
            # switched: the targets let go, the own stitch closes the rest over 10 frames
            PATH['done'] |= _sp_now; PATH['fd'][_sp_now] = f
            PATH['g0'][_sp_now] = np.linalg.norm(_sp_a[_sp_now] - _sp_b[_sp_now], axis=1)
            _sp_k = model.spring_stiffness.numpy()
            _sp_k[PATH['tsa'][_sp_now]] = 0.0; _sp_k[PATH['tsb'][_sp_now]] = 0.0
            _sp_o = PATH['orig'][_sp_now]; _sp_k[_sp_o[_sp_o >= 0]] = PATH['ke_orig'][_sp_now][_sp_o >= 0]
            model.spring_stiffness.assign(_sp_k)
            log.setdefault('seam_paths_switch', []).append([f, int(_sp_now.sum()), round(float(np.median(PATH['g0'][_sp_now])) * 1000, 1)])
        _sp_act = ~PATH['done']
        if _sp_act.any():
            _sp_t = np.minimum(PATH['c'] / np.maximum(_sp_L, 1e-9), 0.5)
            _sp_pa, _, _ = _path_arc(PATH, _sp_a, _sp_b, _sp_t)
            _sp_pb, _, _ = _path_arc(PATH, _sp_a, _sp_b, 1.0 - _sp_t)
            for st_ in (s0, s1):
                _sp_qq = st_.particle_q.numpy()
                _sp_qq[PATH['T'][_sp_act, 0]] = _sp_pa[_sp_act]; _sp_qq[PATH['T'][_sp_act, 1]] = _sp_pb[_sp_act]
                st_.particle_q.assign(_sp_qq)
    if PATH is not None and (PATH['done'] & (PATH['orig'] >= 0)).any():
        # every frame the rest lengths are written (the stage's own ramp starts them from the straight gap)
        _sp_rd = PATH['done'] & (PATH['orig'] >= 0)
        if upd or (f - PATH['fd'][_sp_rd] <= 11).any():
            RL = RL.copy()
            RL[PATH['orig'][_sp_rd]] = PATH['g0'][_sp_rd] * np.maximum(0.0, 1.0 - (f - PATH['fd'][_sp_rd]) / 10.0)
            upd = True
    if upd:
        if SREST:
            RL[:N_ST] = np.maximum(RL[:N_ST], SREST)
        if LOCK is not None and (LOCK_F >= 0).any():
            RL = RL.copy(); _lk = np.nonzero(LOCK_F >= 0)[0]; RL[_lk] = LOCK_L[_lk]
        model.spring_rest_length.assign(RL)
    if STCAP:
        _q = s0.particle_q.numpy(); _L = np.linalg.norm(_q[_SI_CAP[:, 0]] - _q[_SI_CAP[:, 1]], axis=1)
        _k = model.spring_stiffness.numpy()[:N_ST]
        _base = (RL if RL is not None else model.spring_rest_length.numpy())[:N_ST]
        _lo = np.where(_k > 0, _L - STCAP / np.maximum(_k, 1e-9), 0.0)
        _cap = _lo > _base
        RLc = (RL if RL is not None else model.spring_rest_length.numpy()).copy()
        RLc[:N_ST] = np.maximum(_base, _lo).astype(np.float32)
        model.spring_rest_length.assign(RLc)
        if _cap.any():
            log['stitch_cap']['capped_frames'] += 1
            log['stitch_cap']['capped_max'] = max(log['stitch_cap']['capped_max'], int(_cap.sum()))
    G_ = None
    for g_ in GRAB_SCRIPT:
        if g_['from'] <= f <= g_['to']:
            G_ = g_
    _gp = cfg.get('grab_file') or os.path.join(out, 'grab.json')     # one file for the whole job (all stages)
    if os.path.exists(_gp):
        try:
            G_ = json.load(open(_gp))
        except (OSError, ValueError):
            pass
    if GRB is not None and GRAB_MODE != 'rigid':
        # the stroke brush shares grab.json and the grab's springs (one drag at a time): it is not a grab
        _S = G_ if G_ is not None and G_.get('stroke') else None
        if _S is not None:
            G_ = None
            if FAST_LIVE and not (os.environ.get('AC9_TEST_FAST_E6', '1') != '0'):   # the brush on Accurate (=0 refuses it, tests only)
                if 'stroke_off' not in log:
                    log['stroke_off'] = 'AC9 Fast Live leaves the stroke brush out (Native only)'
                _S = None
        if _grab is None:
            _stroke_frame(f, _S)
    if FAST_LIVE:
        try:_fast_contract.validate_grab(G_)
        except ValueError as _fast_error:
            log['error']=str(_fast_error)
            log['checkpoint']={'status':'unavailable','reason':'Unsupported Fast Live grab'}
            res=res[:f];break
    if G_ is not None:
        _ids = _full2loc[np.asarray(G_['verts'], np.int64)]
        _ids = _ids[_ids >= 0]
        _q = s0.particle_q.numpy(); _v = s0.particle_qd.numpy()
        if _grab is None and len(_ids):
            _stroke = None                         # a smooth brush after this grab starts afresh
            _pm, _pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
            _grab = (_ids, _q[_ids] - _q[_ids].mean(0), _pm[_ids].copy(), _pim[_ids].copy())
            if GRB is not None and GRAB_MODE != 'rigid':
                _go = np.zeros((model.particle_count, 3), np.float32); _go[_ids] = _grab[1]
                # the spring's strength in N/m is set for the product's 10 substeps (dt_ref), whatever this run's
                # substep (S3 grab, design OK 11/02, experiments/claude_s3_grab_2026-11-02): at 2 substeps frac x m /
                # dt^2 was 1/25 as stiff and the held cloth followed 2-3 mm of 40. 'kp' is that strength, solved
                # implicitly by the Fast Live correction; the explicit kernel (Native substeps) is capped at frac x m /
                # dt^2, today's value: a safe-side choice (an explicit spring overshoots at 2 x that; frac 25 at 2
                # substeps did not overshoot on the body, free cloth unmeasured). At 10 substeps both are the same.
                _dtr = 1.0 / fps / 10
                GRB['kp'] = np.zeros(model.particle_count, np.float32)
                GRB['kp'][_ids] = GRB['frac'] * _pm[_ids] / _dtr ** 2
                _gk = np.zeros(model.particle_count, np.float32)
                _gk[_ids] = np.minimum(GRB['kp'][_ids], GRB['frac'] * _pm[_ids] / DT_EXPLICIT ** 2)
                GRB['off'].assign(_go); GRB['k'].assign(_gk)
            else:
                _pm[_ids] = 0.0; _pim[_ids] = 0.0
                model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
            if GRB is not None:
                _gm = np.zeros(model.particle_count, np.int32); _gm[_ids] = 1
                GRB['mask'].assign(_gm)
        if _grab is not None:
            _c = _q[_grab[0]].mean(0)
            _d = np.asarray(G_['target'], np.float32) - _c
            _n = float(np.linalg.norm(_d))
            if GRB is not None and GRAB_MODE != 'rigid':
                # the springs' anchor is the target itself (no step limit); the log row is how far the centroid is
                GRB['step'].assign(np.asarray(G_['target'], np.float32).reshape(1, 3))
                _d = np.zeros(3, np.float32)
            elif GRB is not None:
                # held vertices have no mass, so the solves leave their shape; the kernel moves them _d / sub a solve
                _d *= min(1.0, GRAB_STEP / max(_n, 1e-12))
                GRB['step'].assign((_d / sub).reshape(1, 3).astype(np.float32))
            else:
                _d *= min(1.0, GRAB_STEP / max(_n, 1e-12))
                _q[_grab[0]] = _c + _d + _grab[1]; _v[_grab[0]] = 0.0
                s0.particle_q.assign(_q); s0.particle_qd.assign(_v)
            log['grab'].append([f, round(float(np.linalg.norm(np.asarray(G_['target']) - (_c + _d))) * 1000, 2)])
            if FREE is not None and G_.get('through'):
                _gm = np.zeros(model.particle_count, bool); _gm[_grab[0]] = True
                _gm = _grow(_gm, 1)
                FREE['pair'].assign(_gm.astype(np.int32))
                _through = {'piece': int(np.bincount(_fvp[_grab[0]]).argmax()), 'center': _c + _d,
                            'until': f + THROUGH_K, 'held': int(_gm.sum())}
            elif _through is not None and _through['held']:
                FREE['pair'].zero_(); _through['held'] = 0; _through['until'] = f + THROUGH_K
    elif _grab is not None:
        _pm, _pim = model.particle_mass.numpy(), model.particle_inv_mass.numpy()
        _pm[_grab[0]] = _grab[2]; _pim[_grab[0]] = _grab[3]
        model.particle_mass.assign(_pm); model.particle_inv_mass.assign(_pim)
        _grab = None
        if GRB is not None:
            GRB['mask'].zero_(); GRB['step'].zero_()
        log['grab'].append([f, 'released'])
        if _through is not None and _through['held']:
            FREE['pair'].zero_(); _through['held'] = 0; _through['until'] = f + THROUGH_K
    if _through is not None:
        _nx = _through_update(s0.particle_q.numpy()[:model.particle_count].astype(np.float64),
                              _through['piece'], _through['center'])
        log['grab_through'].append([f, _through['held'], int(FREE['m_x'].sum()), _nx])
        if f % LIVE_EVERY == 0 or not _nx:
            print('grab_through', f, _through['held'], int(FREE['m_x'].sum()), _nx, flush=True)
        if not _through['held'] and (not _nx or f >= _through['until']):
            FREE['m_x'][:] = False; _free_assign()
            log['grab_through'].append([f, 'ended', 'clean' if not _nx else 'limit'])
            _through = None
    if KIN_ON:
        _place_flap(s0)
    if LAYER_K:
        _q = s0.particle_q.numpy().astype(np.float64); _qd = s0.particle_qd.numpy().astype(np.float64)
        _r = model.particle_radius.numpy()
        _mg = float(model.shape_margin.numpy()[0]) if model.shape_count else 0.0
        P_, POS_, VEL_, NRM_ = [], [], [], []
        for L_ in LAYER:
            if L_['mode'] != 'contact' or L_.get('released'):
                continue
            # released once its job is done (the user, 2026-10-01: a layer comes off when it has done its work):
            # every vertex within reach on its own side, at least half of the lower layer touching (within 3 mm),
            # for release_frames frames in a row; then the symmetric cloth contact keeps the order
            wrong_, touch_ = 0, 0
            for vs_, T_, tsg, sgn_ in L_['sides']:
                sd, nn, cp, cv, ok, dist = _layer_sides(_q, vs_, T_, tsg, _qd)
                wrong_ += int((ok & (dist < L_['reach']) & (sgn_ * sd < 0)).sum())
                if sgn_ < 0:
                    touch_ = int((ok & (dist < 0.003)).sum()) >= 0.5 * len(vs_)
            L_['ok_frames'] = L_.get('ok_frames', 0) + 1 if (wrong_ == 0 and touch_) else 0
            if L_['ok_frames'] >= int(cfg.get('layer_release_frames', 10)):
                L_['released'] = f; log['layer_order'][L_['name']]['released_at'] = f
                continue
            for vs_, T_, tsg, sgn_ in L_['sides']:
                sd, nn, cp, cv, ok, dist = _layer_sides(_q, vs_, T_, tsg, _qd)
                use = ok & (dist < L_['reach'])
                if not use.any():
                    continue
                nin = sgn_ * nn[use]          # the side the vertex must stay on: in (lower) or out (upper)
                # active while dot(nin, p - c) < gap: the contact fires at radius + margin, so shift the point
                POS_.append(cp[use] + nin * (L_['gap'] - _r[vs_[use]][:, None] - _mg))
                P_.append(vs_[use]); VEL_.append(cv[use]); NRM_.append(nin)
                log['layer_order'][L_['name']]['contacts'] += int(((sgn_ * sd[use]) < L_['gap']).sum())
        k_ = sum(len(x_) for x_ in P_)
        SYN_N.assign(np.array([k_], np.int32))
        if k_:
            SYN_P.assign(np.pad(np.concatenate(P_).astype(np.int32), (0, LAYER_K - k_)))
            for arr_, src_ in ((SYN_POS, POS_), (SYN_VEL, VEL_), (SYN_NRM, NRM_)):
                arr_.assign(np.pad(np.concatenate(src_), ((0, LAYER_K - k_), (0, 0))).astype(np.float32))
    if GATE is not None:
        _gate_update(s0.particle_q.numpy()[:N_G].astype(np.float64))
    if FREE is not None and f >= FREE['next']:
        # while nothing crosses, look only every `idle` frames (the search is on the CPU)
        _free_mask_update(s0.particle_q.numpy()[:model.particle_count].astype(np.float64))
        FREE['next'] = f + (1 if FREE['rows'][-1][0] else FREE['idle'])
    if FREE is not None:
        FREE['launch'] = bool(cfg.get('untangle_free')) or _through is not None or bool(FREE['m_s'].any())
    _trace('frame_submit', frame=f)
    if f == _RESUME_FRAME + 1:
        _trace('first_submit_begin', frame=f)
    if graph is not None:
        if SPLIT_GRAPHS is not None:
            for _i, _g in enumerate(SPLIT_GRAPHS_FREE if FREE is not None and FREE['launch'] else SPLIT_GRAPHS):
                (LAYER_RECOVERY.launch if LAYER_RECOVERY else wp.capture_launch)(_g)
                if _i % 2 == 0 and os.environ.get('AC9_TEST_SPLIT_SYNC'):
                    wp.synchronize()
        elif LAYER_RECOVERY:
            LAYER_RECOVERY.launch(graph_free if FREE is not None and FREE['launch'] else graph)
        else:
            if f == _RESUME_FRAME + 1:
                _trace('first_graph_launch', frame=f)
            wp.capture_launch(graph_free if FREE is not None and FREE['launch'] else graph)
    else:
        try:
            simulate()
        except Exception as _fast_error:
            if not FAST_LIVE:raise
            log['error']='AC9 Fast Live stopped: '+str(_fast_error)
            log['checkpoint']={'status':'unavailable','reason':'Incomplete Fast Live substep'}
            log['fast_live']['status']='failed'
            res=res[:f];break
    _trace('frame_submitted', frame=f)
    if cfg.get('log_overflow') and getattr(solver, 'trimesh_collision_detector', None) is not None:
        # contact buffers that overflowed in the last collision pass of the frame (vertex, triangle, edge, tri-tri):
        # contacts past the buffer are dropped, and the cloth can then pass through (test log)
        _rf = solver.trimesh_collision_detector.resize_flags.numpy()
        if _rf[:3].any():
            log.setdefault('overflow_frames', []).append([f] + _rf[:4].astype(int).tolist())
    if VEL_DAMP != 1.0:
        # cfg 'vel_damp': velocities x this every frame. The free-fall guess damped the cloth numerically (about half
        # the velocity gone every substep); with init_xv that brake is gone (Fable G-3 review, the first suspect for
        # the bodies folding up in the zero-gravity sewing stage)
        s0.particle_qd.assign(s0.particle_qd.numpy() * VEL_DAMP)
    if AIR_DAMP != 1.0:
        # cfg 'air_damp': like air, only the velocity across the cloth (along the vertex normal) x this every frame.
        # VBD has no drag and its membrane damping leaves rigid rotations alone, so the open jacket fronts kept
        # swinging from the shoulders (40 mm per 100 frames after 300, 2026-10-01); vel_damp stopped that but also
        # the fronts sliding down along themselves (the fronts stayed 13 mm long)
        _q = s0.particle_q.numpy()[:N_G].astype(np.float64); _v = s0.particle_qd.numpy()
        _fn = np.cross(_q[AIR_F[:, 1]] - _q[AIR_F[:, 0]], _q[AIR_F[:, 2]] - _q[AIR_F[:, 0]])
        _n = np.zeros_like(_q)
        for _k in range(3):
            np.add.at(_n, AIR_F[:, _k], _fn)
        _n /= np.linalg.norm(_n, axis=1, keepdims=True) + 1e-18
        _vn = (_v[:N_G] * _n).sum(1)
        _v[:N_G] -= ((1.0 - AIR_DAMP) * _vn[:, None] * _n).astype(np.float32)
        s0.particle_qd.assign(_v)
    if CN is not None and CN['every'] and f % CN['every'] == 0:
        # clear_new 'every': the joining pieces kept outside the old cloth through the stage (user 2026-10-03: put
        # outside only at the start, the collar slid back under the bodice as it settled), at most cap per check,
        # spread at half to the one-ring, their velocity into the old cloth taken out
        _q = s0.particle_q.numpy(); _v = s0.particle_qd.numpy()
        _X = _q[:N_G].astype(np.float64)
        _nd = CN['need'](_X)
        _ln = np.linalg.norm(_nd, axis=1)
        _hit = np.nonzero(_ln > 0)[0]
        if len(_hit):
            _nd[_hit] *= (np.minimum(_ln[_hit], CN['cap']) / _ln[_hit])[:, None]
            _mv = _nd.copy()
            for _v0 in _hit:
                for _w in CN['nb'][_v0]:
                    if CN['newv'][_w] and _ln[_w] == 0:
                        _mv[_w] = np.where(np.linalg.norm(_mv[_w]) < 0.5 * _ln[_v0], 0.5 * _nd[_v0], _mv[_w])
            _X += _mv
            _u = _mv / (np.linalg.norm(_mv, axis=1, keepdims=True) + 1e-18)
            _V = _v[:N_G].astype(np.float64); _vn = (_V * _u).sum(1)
            _V -= np.minimum(_vn, 0.0)[:, None] * _u
            _q[:N_G] = _X.astype(np.float32); _v[:N_G] = _V.astype(np.float32)
            s0.particle_q.assign(_q); s0.particle_qd.assign(_v)
        CN['moves'].append(int(len(_hit)))
    if UNT is not None and (f >= UNT_FROM or UNT.detect_only) and f % UNT_EVERY == 0:
        _tu = time.perf_counter()
        UNT.step(s0, model.particle_inv_mass.numpy()[:N_G], f)
        UNT.log['frames'][-1]['ms'] = round((time.perf_counter() - _tu) * 1000, 1)
    if LAYER:
        _q = s0.particle_q.numpy(); _v = s0.particle_qd.numpy(); _mv = False
        for L_ in LAYER:
            for vs_, T_, tsg, sgn_ in L_['sides']:
                sd, nn, cp, cv, ok, dist = _layer_sides(_q.astype(np.float64), vs_, T_, tsg, _v.astype(np.float64))
                if f == frames:
                    log['layer_order'][L_['name']].setdefault('wrong_side_end', []).append(
                        int((ok & (dist < L_['reach']) & (sgn_ * sd < 0)).sum()))
                if L_['mode'] != 'push':
                    continue
                bad = ok & (dist < L_['reach']) & (sd > -L_['gap'])
                if bad.any():
                    vb = vs_[bad]
                    _q[vb] -= (np.minimum(sd[bad] + L_['gap'], L_['step'])[:, None] * nn[bad]).astype(np.float32)
                    vo = ((_v[vb] - cv[bad]) * nn[bad]).sum(1)          # outward velocity relative to the upper layer
                    _v[vb] -= (np.maximum(vo, 0.0)[:, None] * nn[bad]).astype(np.float32)
                    log['layer_order'][L_['name']]['contacts'] += int(bad.sum()); _mv = True
        if _mv:
            s0.particle_q.assign(_q); s0.particle_qd.assign(_v)
    if f == _RESUME_FRAME + 1:
        _trace('first_frame_sync_begin', frame=f)
    wp.synchronize()
    if f == _RESUME_FRAME + 1:
        _trace('first_frame_sync_end', frame=f)
    _trace('frame_synced', frame=f)
    log['frame_s'].append(round(time.perf_counter() - t0, 4))
    if LAYERS is not None:
        LAYERS['wrong'].append(int(solver.ac9_wrong.numpy().sum()))
        if hasattr(solver, 'ac9_layer_recovery_stats'):
            counts_ = solver.ac9_layer_recovery_stats.numpy().sum(axis=0).tolist()
            log.setdefault('layer_recovery', {'reach_mm': _recovery_mm, 'per_frame': []})['per_frame'].append(counts_)
            if counts_[3]:
                raise RuntimeError('Layer recovery contact buffer overflow; candidate run refused')
        if hasattr(solver, 'ac9_layer_crossings'):
            cross_ = solver.ac9_layer_crossings
            log.setdefault('layer_crossings', []).append([int(cross_['count'].numpy()[0]), int(cross_['blocked'].numpy()[0])])
            # edges whose search was cut as endless (layer_crossings.generate), over the frame's passes
            stuck_ = int(cross_['stuck'].numpy()[0])
            if stuck_:
                log.setdefault('layer_crossings_stuck', []).append([f, stuck_])
                print('layer_crossings_stuck', f, stuck_, flush=True)
                cross_['stuck'].zero_()
            if int(cross_['overflow'].numpy()[0]):
                raise RuntimeError('Layer crossing contact buffer overflow; candidate run refused')
    if cfg.get('debug'):
        log.setdefault('soft_contacts', []).append(int(contacts.soft_contact_count.numpy()[0]))
    if LOCK is not None and LOCK_WELD and LOCK_GROUPS is not None:
        _q = s0.particle_q.numpy(); _v = s0.particle_qd.numpy(); _w = model.particle_inv_mass.numpy()
        _vs, _g = LOCK_GROUPS
        _ng = int(_g.max()) + 1
        # weight: a held vertex (inverse mass 0) decides alone where its group goes
        _held = (_w[_vs] == 0).astype(np.float64)
        _hw = np.zeros(_ng); np.add.at(_hw, _g, _held)
        _wt = np.where(_hw[_g] > 0, _held, 1.0)
        _ws = np.zeros(_ng); np.add.at(_ws, _g, _wt)
        for arr in (_q, _v):
            _m = np.zeros((_ng, 3)); np.add.at(_m, _g, arr[_vs].astype(np.float64) * _wt[:, None])
            arr[_vs] = (_m / _ws[:, None])[_g].astype(np.float32)
        s0.particle_q.assign(_q); s0.particle_qd.assign(_v)
    res[f] = s0.particle_q.numpy()[:N_G]
    if FANCH is not None:
        log['fric_anchor']['slid_mm_per_frame'].append(round(float(FANCH['slid'].numpy()[0]) * 1000.0, 3))
        FANCH['slid'].zero_()
    if DEEP is not None:
        DEEP['rows'].append(int(DEEP['n'].numpy()[0])); DEEP['n'].zero_()
        log['deep_push']['per_frame'] = DEEP['rows']
    if BF2 is not None:
        if BF2['closed'] is None and (FOLD_RAMP is None or f > FOLD_RAMP[3]):
            # the stitches sewing now (a delayed one is off: stiffness 0); none = closed
            _act = np.nonzero(model.spring_stiffness.numpy()[:N_ST] > 0)[0] if N_ST else np.zeros(0, np.int64)
            _qb = s0.particle_q.numpy()
            _sib = model.spring_indices.numpy().reshape(-1, 2)[_act] if len(_act) else None
            if _sib is None or np.percentile(np.linalg.norm(_qb[_sib[:, 0]] - _qb[_sib[:, 1]], axis=1), 90) < BF2['mm']:
                BF2['closed'] = f
        if BF2['f_on'] is None and BF2['closed'] is not None:
            # the hinges' stiffness and rest angle as they are now (the mesh-independent / per-piece / fold values)
            BF2['k0'].assign(model.edge_bending_properties.numpy()[BF2['r_np'], 0].astype(np.float32))
            BF2['ra0'].assign(model.edge_rest_angle.numpy()[BF2['r_np']].astype(np.float32))
            if BF2['inside']:
                _bfh = np.zeros((model.edge_count, 3), np.float32)
                _bfh[BF2['r_np'], 1] = BF2['thy'].numpy(); _bfh[BF2['r_np'], 2] = BF2['rat']
                solver.ac9_bend_fric.assign(_bfh)      # st (column 0) by the launch below
            BF2['on'].fill_(1)
            BF2['f_on'] = f
            log['bend_fric2']['on_frame'] = f
            _bf2_launch(s0.particle_q, 1)
        # test only (Fable REVIEW_fable_bend_hyst_stop_2026-10-18 追記 2): "freeze_after": frame -- the stuck angles
        # stay as they are from then on (the hinges keep k (1 + ratio) at that rest angle); does the cycle die away?
        if BF2['f_on'] is not None and BF2.get('freeze') and f == BF2['freeze']:
            BF2['on'].fill_(0)
            log['bend_fric2']['frozen_at'] = f
        if BF2['f_on'] is not None and f % 25 == 0:
            log['bend_fric2'].setdefault('slips_per_25_frames', []).append([f, int(BF2['slip'].numpy()[0])])
            BF2['slip'].zero_()
    if JIT is not None:
        _tj = time.perf_counter()
        _Xj = res[f].astype(np.float64) * 1000.0
        _Xp = res[f - 1].astype(np.float64) * 1000.0
        _dj = _Xj - _Xp
        _nj = _jit_normals(_Xj)
        if JIT['n'] is None:
            JIT['n'] = _jit_normals(_Xp)
        JIT['rel'] += JIT['alpha'] * (np.linalg.norm(_dj - JIT['M'] @ _dj, axis=1) - JIT['rel'])
        JIT['flk'] += JIT['alpha'] * (JIT['VF'] @ np.degrees(np.arccos(np.clip((_nj * JIT['n']).sum(1), -1.0, 1.0)))
                                      - JIT['flk'])
        JIT['n'] = _nj
        if f >= JIT['from']:
            _fl = (JIT['rel'] >= JIT['rel_mm']) | (JIT['flk'] >= JIT['flick_deg'])
            JIT['ever'] += _fl
            JIT['ring'][JIT['judged'] % JIT['tail']] = _fl
            JIT['judged'] += 1
            JIT['first'][_fl & (JIT['first'] < 0)] = f
            if f % 10 == 0:
                JIT['trace'].append([f, int(_fl.sum()), round(float(np.percentile(JIT['rel'], 99)), 3)])
        JIT['s'] += time.perf_counter() - _tj
    if DIAG is not None:
        _h = DIAG['held'].numpy()[:N_G]; _w = DIAG['want'].numpy()[:N_G] * 1000.0     # substeps held, mm wanted
        DIAG['held'].zero_(); DIAG['want'].zero_()
        _p = np.zeros((N_G, 3))
        if model.spring_count:
            _si = model.spring_indices.numpy().reshape(-1, 2)[:N_ST]
            _k = model.spring_stiffness.numpy()[:N_ST]; _r = model.spring_rest_length.numpy()[:N_ST]
            _ok = (_si < N_G).all(1); _si, _k, _r = _si[_ok], _k[_ok], _r[_ok]
            _d = res[f][_si[:, 1]] - res[f][_si[:, 0]]
            _L = np.linalg.norm(_d, axis=1) + 1e-12
            _fv = (_k * np.maximum(_L - _r, 0.0) / _L)[:, None] * _d            # N, on the first end towards the second
            np.add.at(_p, _si[:, 0], _fv); np.add.at(_p, _si[:, 1], -_fv)
        DIAG['rows_held'].append(_h.astype(np.float16)); DIAG['rows_want'].append(_w.astype(np.float16))
        DIAG['rows_pull'].append(_p.astype(np.float16))
    if WATCH is not None and WATCH.get('tug') and WATCH['alarm'] is None and model.spring_count             and f >= WATCH['from_auto']:
        TG = WATCH['tug']; T_ = TG['cfg']
        _sit = model.spring_indices.numpy().reshape(-1, 2)
        _ke = model.spring_stiffness.numpy(); _rl = model.spring_rest_length.numpy()
        _d = res[f][_sit[:N_ST, 1]] - res[f][_sit[:N_ST, 0]]
        _L = np.linalg.norm(_d, axis=1) + 1e-12
        _fv = (_ke[:N_ST] * np.maximum(_L - _rl[:N_ST], 0.0) / _L)[:, None] * _d
        _vpw = v_piece[sel]
        row = {}
        _cm = float(T_.get('closed_mm', 0.5)) / 1000.0
        for k_, ids in TG['pairs'].items():
            # a pair counts once it is closed: its stitches' median length within closed_mm of their rest length
            _r0 = max(float(np.median(_rl[ids])), 0.0005)       # contact keeps closed stitches ~0.2-0.3 mm long
            if np.percentile(_L[ids], 90) >= _r0 + _cm:          # 90 % closed (the median let half-open seams in)
                continue
            _f2 = (_ke[ids] * np.maximum(_L[ids] - _r0, 0.0) / _L[ids])[:, None] * _d[ids]
            sgn = np.where(_vpw[_sit[ids, 0]] == k_[0], 1.0, -1.0)[:, None]
            row[k_] = float(np.linalg.norm((_f2 * sgn).sum(0)))
        TG['hist'].append(row)
        _wn = int(T_.get('window', 10))
        if len(TG['hist']) >= _wn:
            for k_ in TG['pairs']:
                m_ = np.mean([h_.get(k_, 0.0) for h_ in TG['hist'][-_wn:]])
                nm_ = f"{pat_names[k_[0]]}x{pat_names[k_[1]]}" if pat_names else f"{k_[0]}x{k_[1]}"
                TG['over'][k_] = TG['over'].get(k_, 0) + 1 if m_ > float(T_.get('alarm_n', 2.0)) else 0
                TG['note'][k_] = TG['note'].get(k_, 0) + 1 if m_ > float(T_.get('note_n', 1.0)) else 0
                if TG['note'][k_] == int(T_.get('note_frames', 30)):
                    log.setdefault('tug_notes', []).append({'frame': f, 'pair': nm_, 'net_pull_n': round(m_, 3)})
                if TG['over'][k_] >= int(T_.get('alarm_frames', 20)) and WATCH['alarm'] is None                         and k_ not in TG.setdefault('done', set()):
                    TG['done'].add(k_)
                    ids = TG['pairs'][k_]
                    _top = ids[np.argsort(-np.linalg.norm(_fv[ids], axis=1))][:20]
                    WATCH['alarm'] = {'frame': f, 'kind': 'tug of war', 'piece': nm_,
                                      'net_pull_n': round(m_, 3), 'action': 'stop' if WATCH['cfg'].get('stop', False) else 'record',
                                      'top_stitches_full_verts': [[int(sel[_sit[e_, 0]]), int(sel[_sit[e_, 1]]),
                                                                   round(float(_L[e_]) * 1000, 2),
                                                                   round(float(np.linalg.norm(_fv[e_])), 3)] for e_ in _top]}
                    log.setdefault('anomalies', []).append(WATCH['alarm']); log['anomaly'] = WATCH['alarm']
                    json.dump(log['anomalies'], open(os.path.join(out, 'anomaly.json'), 'w'), indent=1)
                    if not WATCH['cfg'].get('stop', False):
                        WATCH['alarm']['action'] = 'record'; WATCH['alarm'] = None
                    elif WATCH['cfg'].get('stop', False):
                        log['error'] = f"anomaly at frame {f}: {nm_} pulling at each other ({m_:.1f} N, seam closed)"
                        res = res[:f + 1]
                        break
            if WATCH['alarm'] is not None and WATCH['cfg'].get('stop', False):
                break
    if WATCH is not None and WATCH['pieces']:
        _cen = np.array([res[f][ix_].mean(0) for ix_ in WATCH['idx']])
        if WATCH['prev'] is not None:
            WATCH['hist'].append(np.linalg.norm(_cen - WATCH['prev'], axis=1) * 1000.0)
        WATCH['prev'] = _cen
        _wn = int(WATCH['cfg'].get('window', 10))
        if len(WATCH['hist']) >= _wn and f >= max(int(WATCH['cfg'].get('from', 0)), WATCH['from_auto']) and WATCH['alarm'] is None                 and f > WATCH.get('wait', 0) + _wn:
            _m = np.mean(WATCH['hist'][-_wn:], axis=0)
            _k = int(np.argmax(_m))
            if _m[_k] > float(WATCH['cfg'].get('drift_mm', 0.4)):
                # what pulls: the stitches on that piece with the most stretch x stiffness, by pair of pieces
                _pk = WATCH['pieces'][_k]
                _si = model.spring_indices.numpy().reshape(-1, 2) if model.spring_count else np.zeros((0, 2), int)
                _ke = model.spring_stiffness.numpy() if model.spring_count else np.zeros(0)
                _rl = model.spring_rest_length.numpy() if model.spring_count else np.zeros(0)
                _vpw = v_piece[sel]
                _len = np.linalg.norm(res[f][_si[:, 0]] - res[f][_si[:, 1]], axis=1) if len(_si) else np.zeros(0)
                _frc = _ke * np.maximum(_len - _rl, 0.0)
                _on = (_vpw[_si[:, 0]] == _pk) | (_vpw[_si[:, 1]] == _pk) if len(_si) else np.zeros(0, bool)
                _pairs = {}
                for e_ in np.nonzero(_on)[0]:
                    a_, b_ = sorted((int(_vpw[_si[e_, 0]]), int(_vpw[_si[e_, 1]])))
                    k_ = f"{pat_names[a_]}x{pat_names[b_]}" if pat_names else f"{a_}x{b_}"
                    _pairs[k_] = _pairs.get(k_, 0.0) + float(_frc[e_])
                _top = np.argsort(-_frc)[:20] if len(_frc) else []
                WATCH['alarm'] = {
                    'frame': f, 'piece': pat_names[_pk] if pat_names else str(_pk),
                    'drift_mm_per_frame': round(float(_m[_k]), 3),
                    'stitch_force_by_pair_N': {k_: round(v_, 3) for k_, v_ in sorted(_pairs.items(), key=lambda x: -x[1])[:6]},
                    'top_stitches_full_verts': [[int(sel[_si[e_, 0]]), int(sel[_si[e_, 1]]),
                                                 round(float(_len[e_]) * 1000, 2), round(float(_frc[e_]), 3)] for e_ in _top],
                    'under_outer': (int(sum(1 for x_ in CN['moves'][-1:] if x_)) if CN is not None and CN.get('moves') else None)}
                # relax (user decision 2026-10-03: the add-on itself only loosens the seam and closes it again; if
                # that does not help it stops and shows the place): the pair of pieces pulling hardest through
                # stitches still open (> open_mm) -- its open stitches take the length they have now and close
                # again over relax frames, and the guard waits for that before it looks again; a pair is loosened
                # once (the first trial also cut on a 2nd alarm: the shoulder and side seams went, 2026-10-03);
                # a 2nd alarm on it, or no open stitch to loosen: stop as below
                _rx = WATCH['cfg'].get('relax')        # default off: stop and show (user decision 2026-10-04)
                _act = 'stop'
                _opn = (_len - _rl) > float(WATCH['cfg'].get('open_mm', 1.0)) / 1000.0 if len(_si) else np.zeros(0, bool)
                _pairs_o = {}
                for e_ in np.nonzero(_on & _opn)[0]:
                    a_, b_ = sorted((int(_vpw[_si[e_, 0]]), int(_vpw[_si[e_, 1]])))
                    _pairs_o[(a_, b_)] = _pairs_o.get((a_, b_), 0.0) + float(_frc[e_])
                WATCH['alarm']['open_force_by_pair_N'] = {(f"{pat_names[a_]}x{pat_names[b_]}" if pat_names else f"{a_}x{b_}"): round(v_, 3)
                                                          for (a_, b_), v_ in sorted(_pairs_o.items(), key=lambda x: -x[1])[:6]}
                if _rx and _pairs_o and model.spring_count:
                    _pa, _pb = max(_pairs_o, key=_pairs_o.get)
                    _pn = f"{pat_names[_pa]}x{pat_names[_pb]}" if pat_names else f"{_pa}x{_pb}"
                    _ids = np.nonzero((((_vpw[_si[:, 0]] == _pa) & (_vpw[_si[:, 1]] == _pb))
                                       | ((_vpw[_si[:, 0]] == _pb) & (_vpw[_si[:, 1]] == _pa))) & _opn)[0]
                    _n = WATCH['tries'].get(_pn, 0)
                    if _n == 0:
                        try:
                            KE0
                        except NameError:
                            KE0 = model.spring_stiffness.numpy().copy()
                        _rf = int(_rx.get('frames', 30))
                        DELAY.append((_ids, f + 1, _rf)); _act = f'relax {_pn} ({len(_ids)} stitches, {_rf} frames)'
                        if SETTLE:
                            SETTLE_FROM = max(SETTLE_FROM, f + _rf + 2)
                        WATCH['wait'] = f + _rf
                    WATCH['tries'][_pn] = _n + 1
                # user 2026-10-06: run to the end and record every anomaly (one run then shows all that went wrong;
                # the first one is marked and can be fixed from its frame); "stop": true stops as before
                if _act == 'stop' and not WATCH['cfg'].get('stop', False):
                    _act = 'record'
                WATCH['alarm']['action'] = _act
                log.setdefault('anomalies', []).append(WATCH['alarm'])
                log['anomaly'] = WATCH['alarm']
                json.dump(log['anomalies'], open(os.path.join(out, 'anomaly.json'), 'w'), indent=1)
                if _act == 'record':
                    WATCH['alarm'] = None; WATCH['hist'] = []
                    WATCH['wait'] = f + int(WATCH['cfg'].get('cooldown', 30))
                elif _act != 'stop':
                    WATCH['alarm'] = None; WATCH['hist'] = []
                elif WATCH['cfg'].get('stop', False):
                    log['error'] = f"anomaly at frame {f}: {WATCH['alarm']['piece']} drifting {WATCH['alarm']['drift_mm_per_frame']} mm/frame"
                    res = res[:f + 1]
                    break
    # progress for the add-on (it reads stdout), the latest frame every LIVE_EVERY frames (written to a temp
    # file and renamed, so the reader never sees half a file), and a stop file the add-on writes to cancel
    print(f"progress {f} {frames}", flush=True)
    # the add-on reads these while they are written: on Windows a rename onto a file that is open for reading is
    # refused (it stopped a run at frame 66, 2026-09-28), so a refused update is skipped, never fatal
    try:
        _pt = os.path.join(LIVE_OUT, 'progress_tmp.txt')
        with open(_pt, 'w') as _fp:
            _fp.write(f"{f} {frames}")
        os.replace(_pt, os.path.join(LIVE_OUT, 'progress.txt'))
    except OSError:
        pass
    # Validate before either live publication or Stop: rejected frames are diagnostics, never results.
    if not np.isfinite(res[f]).all():
        log['error'] = f'non-finite positions at frame {f}'
        res = res[:f]
        break
    # frame 1 too: the cloth shows as soon as the loop runs, not LIVE_EVERY frames later (writing only, 2026-10-20)
    if LIVE_EVERY and (f % LIVE_EVERY == 0 or f == frames or f == 1):
        try:
            _tmp = os.path.join(LIVE_OUT, 'live_tmp.npy')
            _full = (X_full / 1000.0).astype(np.float32).copy()
            if _NATIVE_RUNTIME is not None:
                _NATIVE_RUNTIME.set_expected_points(len(_full))
            if WELD is None:
                _full[sel] = res[f]
            else:
                _full[SEL_OUT] = res[f][W_MAP]
            _rebuild_bound(_full)
            if not np.isfinite(_full).all():
                log['error'] = f'non-finite full positions at frame {f}'
                res = res[:f]
                break
            np.save(_tmp, _full); os.replace(_tmp, os.path.join(LIVE_OUT, 'live.npy'))
            if f == _RESUME_FRAME + 1:
                _trace('first_live_output', frame=f)
        except OSError:
            pass
    if os.path.exists(os.path.join(out, 'stop')):
        _trace('stop_detected', frame=f)
        # tells the add-on the stop was read (stop_control.PendingStop): the frames and checkpoint written from here
        # may take longer than the wait it gives a solver stuck inside a frame
        try:
            open(os.path.join(out, 'stop_seen'), 'w').close()
        except OSError:
            pass
        # cfg 'live' (Live, user 2026-10-07: runs until stopped): the stop is its normal end
        if cfg.get('live'):
            log['stopped_at'] = f
            _trace('checkpoint_begin', frame=f)
            try:
                from live_checkpoint import save as _save_live_checkpoint
                _checkpoint_path = cfg.get('checkpoint_out') or os.path.join(out, 'live_checkpoint.npz')
                _save_live_checkpoint(_checkpoint_path, globals(), f)
                log['checkpoint'] = {'status': 'saved', 'frame': f, 'path': _checkpoint_path}
            except Exception as _checkpoint_error:
                # A failed checkpoint never discards the visible stopped result.
                log['checkpoint'] = {'status': 'unavailable', 'reason': str(_checkpoint_error)}
        else:
            log['error'] = f'stopped at frame {f}'
        _trace('checkpoint_end', status=log.get('checkpoint', {}).get('status'))
        res = res[:f + 1]
        break
    if GUARD and (f == 1 or f % G_EVERY == 0):
        _Xm = np.asarray(res[f], np.float64) * 1000.0
        _tot, _new_al = 0, []
        for c_ in G_CHECKS:
            dq_ = _KDg(_Xm[c_['partner']]).query(_Xm[c_['far']])[0]
            near_ = set(c_['far'][dq_ < G_NEAR].tolist())
            if c_['base'] is None:
                # at the stage's start: what is near B's seam already, and A's cloth lying on A's own side of that seam
                # (a band or a placket folded at placement: its turned layer is far from the seam on the pattern and
                # comes to B's stitches when B arrives -- 56 false counts on a trouser waist before this)
                do_ = _KDg(_Xm[c_['own']]).query(_Xm[c_['far']])[0]
                c_['base'] = near_ | set(c_['far'][do_ < 2.0 * G_NEAR].tolist())
                continue
            n_new = len(near_ - c_['base'])
            _tot += n_new
            if not c_['flag'] and n_new > max(G_MIN, G_SHARE * len(c_['far'])):
                c_['flag'] = True
                _new_al.append({'frame': f, 'kind': 'entangle', 'piece': f"{_gname(c_['a'])} -> {_gname(c_['b'])}",
                                'count': n_new, 'action': 'record'})
        _gap = np.linalg.norm(_Xm[_gE[:, 0]] - _Xm[_gE[:, 1]], axis=1)
        G_CLOSED |= G_ACTIVE & (_gap < 1.0)
        _reo = int((G_CLOSED & (_gap > 2.0)).sum())
        G_REOPEN_RUN = G_REOPEN_RUN + 1 if _reo >= G_REOPEN else 0
        if G_REOPEN_RUN == 2:
            _new_al.append({'frame': f, 'kind': 'reopened', 'piece': 'seams', 'count': _reo, 'action': 'record'})
        _xn = -1
        if G_UNT is not None:
            _P = G_UNT.pairs(s0.particle_q)
            _P = _P[_gtp[_P[:, 0]] != _gtp[_P[:, 1]]] if len(_P) else _P
            if len(_P):
                _Xq = s0.particle_q.numpy()[:N_G].astype(np.float64)
                _P = _P[G_UNT.depths(_Xq, _P) > 0.00045]
            if SEAM_TRI is not None and len(_P):
                _P = _P[[not any((k_, 1 - sd_) in SEAM_TRI.get(int(b_), ()) for k_, sd_ in SEAM_TRI.get(int(a_), ()))
                         for a_, b_ in _P]]
            _keys = {(int(a_), int(b_)) for a_, b_ in _P}
            if G_XBASE is None:
                G_XBASE = _keys
            _newk = _keys - G_XBASE
            _xn = len(_newk)
            G_XRUN = G_XRUN + 1 if _xn > G_CROSS_NEW else 0
            if G_XRUN >= G_CROSS_RUNS and not G_XFLAG:
                G_XFLAG = True
                import collections as _gc
                _top = _gc.Counter(tuple(sorted((_gname(int(_gtp[a_])), _gname(int(_gtp[b_]))))) for a_, b_ in _newk)
                _new_al.append({'frame': f, 'kind': 'crossing', 'piece': ' x '.join(_top.most_common(1)[0][0]),
                                'count': _xn, 'top': [[f"{a_} x {b_}", n_] for (a_, b_), n_ in _top.most_common(4)],
                                'action': 'record'})
        if G_FOLD:
            _row = {}
            _after_ramp = FOLD_RAMP is None or f > FOLD_RAMP[3]
            for g_ in G_FOLD:
                _p50 = float(np.degrees(np.median(_dihedral(_Xm, g_['H']))))
                _row[g_['key']] = round(_p50, 1)
                _dev = abs(_p50 - g_['set'])
                if _dev <= G_FREACH:
                    g_['reached'] = True
                g_['off'] = g_['off'] + 1 if (g_['reached'] and _after_ramp and _dev > G_FKEEP) else 0
                if g_['off'] >= 2 and not g_['flag']:
                    g_['flag'] = True
                    _new_al.append({'frame': f, 'kind': 'unfolded', 'piece': g_['key'], 'count': int(round(_dev)),
                                    'deg': [round(_p50, 1), g_['set']], 'action': 'record'})
            log['guard']['fold'].append([f, _row])
        log['guard']['trace'].append([f, _tot, _reo, _xn])
        if _new_al:
            log.setdefault('anomalies', []).extend(_new_al)
            log['anomaly'] = _new_al[0]
            json.dump(log['anomalies'], open(os.path.join(out, 'anomaly.json'), 'w'), indent=1)
    # cfg 'settle': {"mm": 0.1, "frames": 10}: end the stage once the vertices' speed p99 stays under mm per frame
    # for that many frames, counted only after the stitch ramp and every delayed seam have finished; cfg 'frames' is
    # then the cap. A fixed count cut the stages mid-motion (the last frame still 0.75 mm/frame, Fable r19, 2026-10-01)
    if SETTLE:
        _sp = float(np.percentile(np.linalg.norm(res[f] - res[f - 1], axis=1), 99)) * 1000.0
        if SETTLE_W and f >= SETTLE_W:
            _net = float(np.percentile(np.linalg.norm(res[f] - res[f - SETTLE_W], axis=1), 99)) * 1000.0
            log.setdefault('settle_trace', []).append([f, round(_sp, 3), round(_net, 3), round(_net / max(SETTLE_W * _sp, 1e-9), 3)]) if f % 10 == 0 else None
            _sp = _net
        SETTLE_RUN = SETTLE_RUN + 1 if (f > SETTLE_FROM and _sp < SETTLE_MM) else 0
        if SETTLE_RUN >= SETTLE_N:
            log['settled_at'] = f
            res = res[:f + 1]
            break
log['t_total'] = round(time.perf_counter() - t_all, 3)
if FAST_LIVE and '_FAST_RUNTIME' in globals():
    try:
        log['fast_live']['diagnostics'] = _FAST_RUNTIME.flush()
        log['fast_live']['presewn_live'] = {'on': PRESEWN, 'frames_live': PRESEWN_STATE['frames_live'],
                                            'substeps_live': SUB_LIVE, 'body_kd_live': BODY_KD_LIVE}
        _dg = log['fast_live']['diagnostics']
        if 'native_until_sewn_mm' in _dg and _dg.get('fast_from_frame') is None:
            # never sewn: the whole run was Native's (correction.py, native until sewn)
            log['fast_live']['note'] = ('AC9 Fast Live: the seams never closed to %.1f mm, so the run stayed Native '
                                        '(no Fast correction)' % _dg['native_until_sewn_mm'])
    finally:
        _FAST_RUNTIME.close()
_tmark('loop_end')
if ATTACH is not None:
    for _g in sorted(set(ATTACH['g'].tolist())):
        _mg = ATTACH['g'] == _g
        _d0, _d1 = _attach_dist(res[0].astype(np.float64))[_mg] * 1000, _attach_dist(res[-1].astype(np.float64))[_mg] * 1000
        log['attach'].setdefault('dist_mm', {})[str(_g)] = {
            'start_p50_p90': [round(float(np.median(_d0)), 1), round(float(np.percentile(_d0, 90)), 1)],
            'end_p50_p90': [round(float(np.median(_d1)), 1), round(float(np.percentile(_d1, 90)), 1)]}
if JIT is not None:
    _jn = JIT['judged']
    _jt = min(_jn, JIT['tail'])
    _p99 = float(np.percentile(JIT['rel'], 99)) if len(JIT['rel']) else 0.0
    _end = _jit_clusters(JIT['ring'].sum(0) >= max(1, 0.5 * _jt), JIT['rel'] / JIT['rel_mm'] + JIT['flk'] / JIT['flick_deg']) if _jt else []
    _stg = _jit_clusters(JIT['ever'] >= max(1, 0.5 * _jn), JIT['ever'].astype(float)) if _jn else []
    for c_ in _end:
        c_['x_rest_p99'] = round(c_['rel_mm'] / max(_p99, 1e-6), 2)
    log['jitter'].update({'judged': _jn, 'rel_p99_end': round(_p99, 3), 'end': _end[:8], 'stage': _stg[:8],
                          'n_end': len(_end), 'n_stage': len(_stg), 'trace': JIT['trace'],
                          'ms_per_frame': round(JIT['s'] * 1000.0 / max(len(res) - 1, 1), 3)})
    # per vertex, for painting: share of judged frames flagged, share of the last tail frames, first frame, the
    # smoothed values at the end
    np.savez_compressed(os.path.join(out, 'jitter.npz'), share=(JIT['ever'] / max(_jn, 1)).astype(np.float16),
                        tail=(JIT['ring'].sum(0) / max(_jt, 1)).astype(np.float16), first=JIT['first'],
                        rel=JIT['rel'].astype(np.float16), flick=JIT['flk'].astype(np.float16), sel=sel)
if CN is not None and CN['every']:
    log['clear_new']['during'] = {'checks': len(CN['moves']), 'max': max(CN['moves'] or [0]),
                                  'last10': CN['moves'][-10:]}
if UNT is not None and WELD is None and UNT.side_from == 'contact':
    UNT.save_memory(UNT_MEM, sel)
if GATE is not None:
    log['stitch_gate']['on_at_end'] = int(GATE['on'].sum()); log['stitch_gate']['updates'] = len(GATE['log'])
if SEAM_PAIR_H:
    # the angle reached across each folded seam, in Newton's rest-angle sign (the one set): median and spread
    def _hinge_deg(_P, hs):
        ang = []
        for o0, o1, a_, b_ in hs:
            n1 = np.cross(_P[a_] - _P[o0], _P[b_] - _P[o0]); n1 /= np.linalg.norm(n1) + 1e-18
            n2 = np.cross(_P[b_] - _P[o1], _P[a_] - _P[o1]); n2 /= np.linalg.norm(n2) + 1e-18
            ee = (_P[b_] - _P[a_]) / (np.linalg.norm(_P[b_] - _P[a_]) + 1e-18)
            ang.append(np.degrees(np.arctan2(np.cross(n1, n2) @ ee, np.clip(n1 @ n2, -1, 1))))
        return np.asarray(ang)
    for k_, hs in SEAM_PAIR_H.items():
        ang = _hinge_deg(np.asarray(res[-1], float), hs)
        log.setdefault('seam_fold_start_deg', {})[k_] = [round(float(-x_), 1) for x_ in _hinge_deg(np.asarray(X0, float), hs)]
        if cfg.get('seam_fold_probe'):
            # diagnosis: the same hinges on other positions (full garment, metres), e.g. the reference drape on this mesh
            _pr = np.load(cfg['seam_fold_probe']).astype(float).reshape(-1, N_FULL, 3)[-1][sel]
            log.setdefault('seam_fold_probe_deg', {})[k_] = [round(float(-x_), 1) for x_ in _hinge_deg(_pr, hs)]
        # in the person's sign (+ = the turning piece folds outwards, a collar turned down)
        log.setdefault('seam_fold_end_deg', {})[k_] = {'set': SEAM_SET_DEG.get(k_),
                                                       'median': round(float(-np.median(ang)), 1),
                                                       'p10_p90': [round(float(-np.percentile(ang, q)), 1) for q in (90, 10)],
                                                       'hinges': len(ang)}
# watch, stage end (Fable review r6, 2026-10-03): a seam sewn turned over. The collar sewn to its stand flipped
# while the last ~9 mm closed (stage 3), stayed so, and its closed stitches then lifted the fronts (1.4-5.9 N up
# against 0.057 N of a front's weight). Per pair of pieces, the closed stitches (< closed_mm) whose two ends' vertex
# normals are more than flip_deg apart: 11-21 on the collar x stand in 6 broken jacket runs, at most 4 per pair in
# 5 good ones. flip_n or more stops the job here with those stitches as the place to show.
if WATCH is not None and WATCH['alarm'] is None and WATCH['cfg'].get('flip', True) and model.spring_count         and not log.get('error'):
    # turned over = the angle between the two ends' normals changed by flip_deg (90) or more since the stage started,
    # for flip_frac (25 %) or more of a pair's closed stitches and at least 3 (Fable review r9: the absolute angle and
    # count rang on pieces that start back to back, and missed 11-stitch seams)
    _T = model.tri_indices.numpy().reshape(-1, 3)
    _si = model.spring_indices.numpy().reshape(-1, 2)[:N_ST]

    def _end_angles(P_):
        T_ = _T[(_T < len(P_)).all(1)]
        n_ = np.cross(P_[T_[:, 1]] - P_[T_[:, 0]], P_[T_[:, 2]] - P_[T_[:, 0]])
        vn_ = np.zeros_like(P_)
        for k_ in range(3):
            np.add.at(vn_, T_[:, k_], n_)
        vn_ /= np.linalg.norm(vn_, axis=1, keepdims=True) + 1e-18
        a_ = np.zeros(len(_si))
        ok_ = (_si < len(P_)).all(1)
        a_[ok_] = np.degrees(np.arccos(np.clip((vn_[_si[ok_, 0]] * vn_[_si[ok_, 1]]).sum(1), -1, 1)))
        return a_
    _P = np.asarray(res[-1], np.float64)
    _ang = _end_angles(_P)
    _ang0 = _end_angles(np.asarray(res[0], np.float64))
    _ok = (_si < len(_P)).all(1)
    _len = np.full(len(_si), 1.0)
    _len[_ok] = np.linalg.norm(_P[_si[_ok, 0]] - _P[_si[_ok, 1]], axis=1)
    _closed_e = _len < float(WATCH['cfg'].get('closed_mm', 2.0)) / 1000.0
    _fl = (np.abs(_ang - _ang0) >= float(WATCH['cfg'].get('flip_deg', 90))) & _closed_e
    _vpw = v_piece[sel]
    _byp, _npair = {}, {}
    for e_ in range(len(_si)):
        if not _ok[e_]:
            continue
        a_, b_ = sorted((int(_vpw[_si[e_, 0]]), int(_vpw[_si[e_, 1]])))
        if _closed_e[e_]:
            _npair[(a_, b_)] = _npair.get((a_, b_), 0) + 1
        if _fl[e_]:
            _byp.setdefault((a_, b_), []).append(int(e_))
    _frac = float(WATCH['cfg'].get('flip_frac', 0.25))
    _bad = {k_: v_ for k_, v_ in _byp.items() if len(v_) >= max(3, _frac * _npair.get(k_, 0))}
    _nm = lambda a_, b_: f"{pat_names[a_]}x{pat_names[b_]}" if pat_names else f"{a_}x{b_}"
    log['watch_flips'] = {_nm(*k_): len(v_) for k_, v_ in _byp.items()}
    if _bad:
        _es = [e_ for v_ in _bad.values() for e_ in v_]
        _al = {'frame': len(res) - 1, 'kind': 'seam turned over',
               'piece': ', '.join(_nm(*k_) for k_ in _bad),
               'flipped_stitches_by_pair': {_nm(*k_): len(v_) for k_, v_ in _bad.items()},
               'top_stitches_full_verts': [[int(sel[_si[e_, 0]]), int(sel[_si[e_, 1]]), round(float(_len[e_]) * 1000, 2),
                                            round(float(_ang[e_]), 1)] for e_ in _es],
               'action': 'stop' if WATCH['cfg'].get('stop', False) else 'record'}
        log.setdefault('anomalies', []).append(_al); log['anomaly'] = _al
        json.dump(log['anomalies'], open(os.path.join(out, 'anomaly.json'), 'w'), indent=1)
        if WATCH['cfg'].get('stop', False):
            log['error'] = f"anomaly at the end: {_al['piece']} sewn turned over ({len(_es)} stitches)"
log['graph'] = graph is not None
log['active_verts'] = int(len(sel)); log['frozen_verts'] = log_frozen
# full-size output: vertices outside the active subset stay where the stage started them
if isinstance(res, _LastFrames):
    res = res.written()
full = np.repeat((X_full / 1000.0)[None].astype(np.float32), len(res), axis=0)
if WELD is None:
    full[:, sel] = res
else:
    full[:, SEL_OUT] = res[:, W_MAP]
if _RESUME_FIRST is not None:
    # The output's first frame is this run's starting shape; the solver retains its original history separately.
    if WELD is None:
        full[0, sel] = _RESUME_FIRST
    else:
        full[0, SEL_OUT] = _RESUME_FIRST[W_MAP]
if BIND is not None:
    for f_ in range(len(full)):
        _rebuild_bound(full[f_])
if DIAG is not None and DIAG['rows_held']:
    np.savez_compressed(os.path.join(out, 'diag.npz'), held=np.array(DIAG['rows_held']), want=np.array(DIAG['rows_want']),
                        pull=np.array(DIAG['rows_pull']), sel=np.asarray(sel), substeps=sub)
    log['diag'] = {'frames': len(DIAG['rows_held'])}
    # the bending hinges as solved (4 vertices o0, o1, v1, v2 in the active set, rest angle, ke): to measure the bending
    # moment per pair of pieces / fold line afterwards (Fable review r8: lifts the thread pull does not show)
    if model.edge_count:
        np.savez_compressed(os.path.join(out, 'hinges.npz'), idx=model.edge_indices.numpy(),
                            rest=model.edge_rest_angle.numpy(), props=model.edge_bending_properties.numpy(),
                            sel=np.asarray(sel))
if LOCK is not None:
    _okl = (LOCK_E < N_G).all(1)
    _ef = -np.ones_like(LOCK_E)
    _ef[_okl] = np.asarray(sel)[LOCK_E[_okl]] if WELD is None else -1
    np.savez_compressed(os.path.join(out, 'lock.npz'), e_full=_ef, frame=LOCK_F, length=LOCK_L,
                        ke=model.spring_stiffness.numpy()[:N_ST])
    log['stitch_lock'] = {'mm': LOCK_MM * 1000.0, 'locked': int((LOCK_F >= 0).sum()),
                          'on_not_locked': int(((LOCK_F < 0) & (model.spring_stiffness.numpy()[:N_ST] > 0)).sum()),
                          'stitches': int(N_ST)}
if DRIVE:
    # the far side at the stage's end: vertices inside the held pieces (within 10 mm under them), its edges' length
    # against the stage's start
    _qe = np.asarray(res[-1], np.float64)
    _ede = np.unique(np.sort(np.r_[F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], 1), axis=0)
    for D_ in DRIVE:
        _sd, _, _, _, _ok, _ = _layer_sides(_qe, D_['far'], DRIVE_HELD[0], DRIVE_HELD[1])
        _fm = np.zeros(len(_qe), bool); _fm[D_['far']] = True
        _ed = _ede[_fm[_ede].all(1)]
        _r = np.linalg.norm(_qe[_ed[:, 0]] - _qe[_ed[:, 1]], axis=1) / (
            np.linalg.norm(D_['X_start'][_ed[:, 0]] - D_['X_start'][_ed[:, 1]], axis=1) + 1e-12)
        log['fold_turn'].setdefault('drive_end', []).append({
            'piece': D_['piece'], 'far_inside_held': int((_ok & (_sd < 0) & (_sd > -DRIVE_REACH)).sum()),
            'far_edge_ratio_p1_p50_p99_max': [round(float(x_), 3) for x_ in np.percentile(_r, [1, 50, 99])]
            + [round(float(_r.max()), 3)],
            'up_deg': round(D_['meas'](_qe), 1),
            'hinge_deg_p50': round(float(np.degrees(np.median(_dihedral(_qe, D_['H'])))), 1)})
if _NATIVE_RUNTIME is not None:
    _NATIVE_RUNTIME.publish()
_trace('save_frames_begin')
_valid_full = np.isfinite(full).all(axis=(1, 2))
if not _valid_full.all():
    _first_bad_full = int(np.flatnonzero(~_valid_full)[0])
    log['error'] = f'non-finite full positions at saved frame {_first_bad_full}'
    full = full[:_first_bad_full]
np.save(os.path.join(out, 'frames.npy'), full)
_trace('save_frames_end')
if BOUND_COUPLING:
    _bc_end = solver.ac9_bound_constraints
    log['bound_coupling'].update({'sweep_correction_max_mm':float(_bc_end.sweep_error.numpy()[0])*1000,
        'minimum_scale':float(_bc_end.minimum_scale.numpy()[0]),'scale_counts':_bc_end.scale_counts.numpy().tolist(),
        'nonlinear_path_counts':_bc_end.path_counts.numpy().tolist()})
_tmark('written')
log['timing'] = TIMING
json.dump(log, open(os.path.join(out, 'run_log.json'), 'w'), indent=1)
_trace('save_log_end')
print('done', log['t_total'], log.get('error'))

if _NATIVE_RUNTIME is not None:
    _NATIVE_RUNTIME.success = not bool(log.get('error'))
    _NATIVE_RUNTIME.close()
