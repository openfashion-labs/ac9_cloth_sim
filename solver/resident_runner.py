# SPDX-License-Identifier: GPL-3.0-or-later
"""Resident Live (DECISIONS cloth-sim-hstar-resident-2026-11-16; Preferences > Keep the Live Model, off by default):
the session worker runs newton_vbd.py statement by statement and keeps the last job's model. The next job of the same
structure (same mesh, seams, body and settings; another start shape or other fabric numbers) is begun on it instead of
built again (T-shirt 6 mm, GUI: press after a piece move 3.4 -> 1.5 s to the first drawn frame).

begin(job) on the kept run (measured: RESULT_other_breakdown sections 9 and 13-15):
  1. module values of this solver folder back to the kept run's (after its setup);
  2. statements before the builder again, as written (the start shape, the log, the runtime for live.npy);
  3. the builder segment is not run: its plain values are the kept run's, except the H* switches worked out again;
  4. the model's arrays back to the kept run's finalize, the ones the start shape and the fabric numbers decide from
     finalize_values(); the coloring (particle_color*) is left as it is (Native 3 replaced it after finalize);
  5. the solver's / pipeline's arrays back to the snapshot taken after their statements, the solver's dict as built;
  6. every statement after finalize again, except the heavy ones (solver, pipeline, Native 3 block): in their place
     the names, solver attributes and log entries they set are put back.
Built as today and not kept: another structure, folds or a drape's rest angles (no H* rows), a layer above 0 (no
Native 3), the shapes in NOT_KEPT. A begin that fails builds the same job fresh; a cancel or an exit drops the kept
model and is passed on.
"""
import ast, copy, hashlib, json, os, sys, time
import numpy as np

VALUE_KEYS = {'edge_ke', 'edge_kd', 'density', 'tri_ke', 'tri_ka', 'tri_kd'}      # finalize_values reads them
PATH_KEYS = {'grab_file', 'params_file', 'native_runtime_dir', 'native_runtime_token', 'checkpoint_in', 'checkpoint_out'}
SHAPE_ARRAYS = ('particle_q', 'particle_mass', 'particle_inv_mass', 'tri_poses', 'tri_areas', 'edge_rest_angle',
                'edge_rest_length', 'edge_bending_properties', 'spring_rest_length')
HEAVY = ('solver = newton.solvers.SolverVBD(', 'pipe = newton.CollisionPipeline(',
         'if NATIVE_THREE:' + chr(10) + '    if not solver.device.is_cuda')     # the Native 3 block, not the checkpoint branch
# 1-D (review 2026-11-16): shapes a begin was never checked on -> not kept (built as today)
NOT_KEPT = (('BOUND_COUPLING', lambda g: bool(g.get('BOUND_COUPLING')), 'one layer (bound coupling)'),
            ('WELD', lambda g: g.get('WELD') is not None, 'weld'),
            ('anchor_ke', lambda g: bool(g['cfg'].get('anchor_ke')), 'particles outside the cloth (anchor)'),
            ('seam_paths', lambda g: bool(g['cfg'].get('seam_paths')), 'particles outside the cloth (Pull Round)'),
            ('sym_color', lambda g: bool(g['cfg'].get('sym_color') and g['cfg'].get('mirror_map')), 'mirror coloring'),
            ('ghost_pieces', lambda g: bool(g['cfg'].get('ghost_pieces')), 'two worlds (ghost pieces)'))


def _digest(path, skip=()):
    z = np.load(path); h = hashlib.sha256()
    for k in sorted(z.files):
        if k in skip: continue
        a = z[k]; h.update(k.encode()); h.update(str(a.dtype).encode()); h.update(str(a.shape).encode()); h.update(a.tobytes())
    return h.hexdigest()


def structure_key(idx, avatar, cfg):
    c = {k: v for k, v in cfg.items() if k not in PATH_KEYS and k not in VALUE_KEYS}
    for k, v in list(c.items()):          # a file the job folder holds (pattern.npz): its contents, not where it is
        if isinstance(v, str) and len(v) < 1024 and os.path.isfile(v):
            c[k] = 'file:' + (_digest(v) if v.endswith('.npz') else hashlib.sha256(open(v, 'rb').read()).hexdigest())
    sb = dict(c.get('seam_bend') or {}); sb.pop('ke', None); c['seam_bend'] = sb
    # a fabric's numbers act after finalize (newton_vbd 'fabric_pieces' / 'mi' blocks, re-run by begin); only which
    # pieces have their own fabric and which fields are set stay in the key (2026-11-16 night, GUI fabric change)
    if c.get('fabric_pieces'): c['fabric_pieces'] = {n: sorted(f) for n, f in c['fabric_pieces'].items()}
    if c.get('mi'): c['mi'] = {k: (bool(v) if k == 'D' else v) for k, v in c['mi'].items()}
    return json.dumps(dict(idx=_digest(idx, ('Xinit', 'Xclo')), avatar=_digest(avatar), cfg=c), sort_keys=True, default=str)


class Resident:
    def __init__(self, path, source):
        import warp as wp
        self.wp = wp; self.path = str(path); self.source = os.path.abspath(str(source))
        tree = ast.parse(open(self.path, encoding='utf8').read())
        self.loop_i = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                           and n.target.id == 'f' and 'frames' in ast.unparse(n.iter))
        body = tree.body; self.heads = [ast.unparse(x) for x in body[:self.loop_i]]
        self.codes = [compile(ast.Module(body=[s], type_ignores=[]), self.path, 'exec') for s in body]
        h = self.heads
        self.i_builder = next(i for i, x in enumerate(h) if x.startswith('builder = newton.ModelBuilder('))
        self.i_final = next(i for i, x in enumerate(h) if x == 'model = builder.finalize()')
        self.i_heavy = {i for i, x in enumerate(h) if x.startswith(HEAVY) and i > self.i_final}
        self.used_after = {n.id for s in body[self.i_final + 1:] for n in ast.walk(s) if isinstance(n, ast.Name)}
        self.g = None; self.key = None; self.last = None; self.runtime = None

    # ------------------------------------------------------------------ helpers
    def _arrays(self, obj, prefix, depth=0, out_=None, seen=None):
        wp = self.wp; out_ = {} if out_ is None else out_; seen = set() if seen is None else seen
        if id(obj) in seen or depth > 4: return out_
        seen.add(id(obj))
        if isinstance(obj, wp.array): out_[prefix] = obj; return out_
        if isinstance(obj, dict): it = [(f'{prefix}[{k!r}]', v) for k, v in obj.items()]
        elif isinstance(obj, list): it = [(f'{prefix}[{i}]', v) for i, v in enumerate(obj)]
        elif hasattr(obj, '__dict__') and type(obj).__module__.split('.')[0] in ('newton', '__main__', 'bound_constraints', 'native_experimental_sparse_contacts'):
            try: it = [(f'{prefix}.{a}', v) for a, v in vars(obj).items()]
            except TypeError: it = []
        else: it = []
        for p, v in it: self._arrays(v, p, depth + 1, out_, seen)
        return out_

    def _plain_ok(self, v):
        wp = self.wp
        return not (callable(v) or isinstance(v, type) or type(v).__name__ == 'module' or isinstance(v, wp.array)
                    or type(v).__module__.split('.')[0] in ('warp', 'newton'))

    def _model_arrays(self, model):
        a = {k: v.numpy().copy() for k, v in vars(model).items() if isinstance(v, self.wp.array)}
        return a

    def _mods(self):
        return [m for n, m in list(sys.modules.items()) if m is not None and n != '__main__' and
                os.path.abspath(getattr(m, '__file__', '') or '').startswith(self.source)]

    def _hstar_structure(self, g):
        bnd, partner, IDX = g['bnd'], g['partner'], g['HSTAR_IDX']; bnd_at = g['bnd_at']; out_ = []
        for e, (k, l, oA) in bnd.items():
            for bk in partner.get(k, []):
                for bl in partner.get(l, []):
                    key = (frozenset((k, l)), bk)
                    if frozenset((bk, bl)) in bnd:
                        oB = bnd[frozenset((bk, bl))][2]
                        out_.append(('F', key, IDX[(frozenset((k, l)), bk, oB)], (oA, oB, k, l)))
                    else:
                        cand = bnd_at.get(bk, [])
                        if not cand: continue
                        rows = [(y_, IDX[(frozenset((k, l)), bk, oB_)], (oA, oB_, k, l)) for y_, oB_ in cand]
                        out_.append(('N', key, bk, bl, rows))
        return out_

    @staticmethod
    def _hstar_switch(S, X0m, Xc, dih):
        on = {}; seen = set(); added = skipped = 0
        ok_ = lambda q: bool(dih(q[0], q[1], q[2], q[3], Xc) <= 120)
        for s in S:
            if s[0] == 'F':
                _, key, row, ok = s
                if key not in seen:
                    seen.add(key)
                    if ok_(ok): on[row] = True; added += 1
                    else: skipped += 1
            else:
                _, key, bk, bl, rows = s
                dist = [np.linalg.norm(X0m[y_] - X0m[bl]) for y_, _, _ in rows]
                j = min(range(len(rows)), key=lambda q: dist[q])
                if key not in seen and not (dist[j] > np.linalg.norm(X0m[bk] - X0m[bl])):
                    seen.add(key)
                    if ok_(rows[j][2]): on[rows[j][1]] = True; added += 1
                    else: skipped += 1
        return on, seen, added, skipped

    def _fixed_structure(self, g, model):
        cfg = g['cfg']
        st = dict(tri=model.tri_indices.numpy().copy(), edges=model.edge_indices.numpy().copy(),
                  springs=model.spring_indices.numpy().reshape(-1, 2).copy(), e0=int(g['_hstar_e0']),
                  n_particles=int(model.particle_count), rest_s=float(g['rest_s']))
        st['hstar'] = self._hstar_structure(g); st['dihedral'] = g['dihedral']
        return st

    def _finalize_values(self, X0, Xc, st, cfg):
        import newton
        wp = self.wp; X0 = np.asarray(X0)
        if st['n_particles'] != len(X0): raise RuntimeError('particles outside the cloth')
        P32 = X0.astype(np.float32); sc = newton.ModelBuilder(); sc.particle_q = P32.astype(np.float64)
        T = st['tri']; nt = len(T)
        kw = dict(density=cfg.get('density', 0.14), tri_ke=cfg['tri_ke'], tri_ka=cfg['tri_ka'], tri_kd=cfg['tri_kd'])
        areas = sc.add_triangles(T[:, 0], T[:, 1], T[:, 2], tri_ke=[kw['tri_ke']] * nt, tri_ka=[kw['tri_ka']] * nt,
                                 tri_kd=[kw['tri_kd']] * nt, tri_drag=[0.0] * nt, tri_lift=[0.0] * nt)
        if len(sc.tri_areas) != nt: raise RuntimeError('a degenerate triangle at this start shape')
        m = np.zeros(len(X0))
        np.add.at(m, T.reshape(-1), np.repeat(kw['density'] * np.asarray(areas, dtype=np.float64) / 3.0, 3))
        E = st['edges']; e0 = st['e0']
        sc.add_edges(E[:e0, 0], E[:e0, 1], E[:e0, 2], E[:e0, 3])
        rest_ang = np.array(sc.edge_rest_angle + [st['rest_s']] * (len(E) - e0), dtype=np.float32)
        rl = list(sc.edge_rest_length)
        for i, j, k, l in E[e0:]: rl.append(wp.length(wp.vec3(*P32[l]) - wp.vec3(*P32[k])))
        sl = []
        for a, b in st['springs']:
            p = np.asarray(sc.particle_q[a], dtype=np.float32); q = np.asarray(sc.particle_q[b], dtype=np.float32)
            dd = np.subtract(p, q); sl.append(np.sqrt(np.dot(dd, dd)))
        on, seen, added, skipped = self._hstar_switch(st['hstar'], X0, Xc, st['dihedral'])
        sb = cfg.get('seam_bend') or {}
        bp = np.zeros((len(E), 2)); bp[:e0] = (cfg['edge_ke'], cfg['edge_kd'])
        for r in on: bp[r] = (float(sb.get('ke', cfg['edge_ke'])), cfg['edge_kd'])
        ms = m.astype(np.float32)
        vals = dict(particle_q=P32, particle_mass=ms, particle_inv_mass=np.divide(1.0, ms, out=np.zeros_like(ms), where=ms != 0.0),
                    tri_poses=np.array(sc.tri_poses, dtype=np.float32), tri_areas=np.array(sc.tri_areas, dtype=np.float32),
                    edge_rest_angle=rest_ang, edge_rest_length=np.array(rl, dtype=np.float32),
                    edge_bending_properties=bp.astype(np.float32), spring_rest_length=np.array(sl, dtype=np.float32))
        if st.get('tri_uniform'):          # 1-A: the stretch (tri_ke / ka / kd) of this job, not the kept one's
            vals['tri_materials'] = np.array(sc.tri_materials, dtype=np.float32)
        elif tuple(cfg[k] for k in ('tri_ke', 'tri_ka', 'tri_kd')) != st.get('tri_cfg'):
            raise RuntimeError('the stretch changed and the build wrote per-triangle values')
        return vals, dict(seen=seen, added=added, skipped=skipped)

    # ------------------------------------------------------------------ the two paths
    def vram(self):
        """GPU memory after the job: device used (all processes) and warp's pool in this process, MB."""
        out = {}
        try:
            d = self.wp.get_device('cuda:0'); out['device_used_mb'] = round((d.total_memory - d.free_memory) / 2**20, 1)
        except Exception as exc: out['device_err'] = repr(exc)[:80]
        try: out['pool_mb'] = round(self.wp.get_mempool_used_mem_current('cuda:0') / 2**20, 1)
        except Exception as exc: out['pool_err'] = repr(exc)[:80]
        return out

    def drop(self):
        # the job's Native runtime outlives the namespace: the worker marks it done on a cancel before frame 1 (1-C)
        if self.g is not None: self.runtime = self.g.get('_NATIVE_RUNTIME'); self.g.clear()
        self.g = None; self.key = None; self.snap = None

    def run(self, idx, avatar, cfg, init_globals, mark=lambda *a, **k: None):
        """-> dict(mode='fresh'|'begin', setup_s, ...). Runs the whole script (setup + loop + writing)."""
        key = None; self.runtime = None
        if os.environ.get('AC9_TEST_RESIDENT_KEEP_MAIN_MODULE') != '1':
            # warp's '__main__' module keeps every kernel the earlier jobs defined, so its hash depended on the history
            # (871d6d6 / e4f8715 / 4103bd2 ...) and each new history paid a ~1 s compile (GUI 11/16 night). A new
            # module per job: the kept kernels still hold the module they were built in
            try:
                from warp._src import context as _wc
                _wc.user_modules.pop('__main__', None)
            except Exception: pass
        try: key = structure_key(idx, avatar, cfg)
        except Exception as exc: self.last = dict(key_error=str(exc))
        self.why = None
        if self.g is not None and key is not None and self.key is not None and key != self.key:
            a, b = json.loads(self.key), json.loads(key)
            self.why = [k for k in ('idx', 'avatar') if a[k] != b[k]] + sorted(
                k for k in set(a['cfg']) | set(b['cfg']) if a['cfg'].get(k) != b['cfg'].get(k))
        elif self.g is None: self.why = 'nothing kept' if self.last is None else ('not keepable: ' + str((self.last or {}).get('why_not')))
        if self.g is not None and key is not None and key == self.key:
            try:
                ff = os.environ.get('AC9_TEST_RESIDENT_FAIL_FILE')       # tests: make this one begin fail
                if ff and os.path.isfile(ff):
                    os.remove(ff); raise RuntimeError('injected begin failure')
                info = self._begin(init_globals, mark); mode = 'begin'
            except BaseException as exc:
                # 1-C: a cancel (job_cancel.CanceledJob) or an exit is not a failed begin: nothing kept, passed on
                if type(exc).__name__ == 'CanceledJob' or not isinstance(exc, Exception):
                    self.drop(); raise
                # a failed begin never ends the job: drop what is kept and build this job from scratch
                err = repr(exc)[:300]; self.drop()
                info = self._fresh(init_globals, mark); mode = 'fresh_after_begin_error'; info['begin_error'] = err
                self.key = key if info.get('keepable') else None
        else:
            self.drop(); info = self._fresh(init_globals, mark); mode = 'fresh'
            self.key = key if (key is not None and info.get('keepable')) else None
        g = self.g
        if init_globals.get('_AC9_TEST_FRAMES'): g['frames'] = int(init_globals['_AC9_TEST_FRAMES'])   # headless tests only
        t = time.perf_counter()
        try:
            for c in self.codes[self.loop_i:]: exec(c, g)
        except BaseException:
            self.drop(); raise
        info.update(mode=mode, run_s=round(time.perf_counter() - t, 3), why_fresh=self.why, vram=self.vram())
        if self.key is None: self.drop()
        self.last = info
        return info

    def _fresh(self, init_globals, mark):
        wp = self.wp; g = dict(__name__='__main__', __file__=self.path, **init_globals); self.g = g
        S = dict(heavy_snap={}, heavy_solver={}, heavy_globals={}, heavy_log={})
        t0 = time.perf_counter(); keepable = True
        for i in range(self.loop_i):
            if i == self.i_builder:
                pre_ids = {k: id(v) for k, v in g.items()}; pre_log = copy.deepcopy(g['log'])
            heavy = i in self.i_heavy
            pre_sd = dict(vars(g['solver'])) if heavy and 'solver' in g else None
            pre_g = dict(g) if heavy else None
            pre_hlog = dict(g['log']) if heavy and 'log' in g else None
            exec(self.codes[i], g)
            if pre_sd is not None: S['heavy_solver'][i] = {k: v for k, v in vars(g['solver']).items() if k not in pre_sd or pre_sd[k] is not v}
            if pre_hlog is not None: S['heavy_log'][i] = {k: copy.deepcopy(v) for k, v in g['log'].items() if k not in pre_hlog or pre_hlog[k] is not v}
            if pre_g is not None: S['heavy_globals'][i] = {k: v for k, v in g.items() if k not in pre_g or pre_g[k] is not v}
            if i == self.i_final:
                try:
                    if not g.get('HSTAR_ROWS'): raise RuntimeError('H* rows off')
                    S['final'] = self._model_arrays(g['model'])
                    S['model_plain'] = {a: copy.deepcopy(v) for a, v in vars(g['model']).items() if isinstance(v, (int, float, bool, str, type(None)))}
                    names = [k for k, v in g.items() if not k.startswith('__') and (k not in pre_ids or pre_ids[k] != id(v))
                             and k not in ('builder', 'model') and k in self.used_after]
                    seg = {}
                    for k in names:
                        v = g[k]
                        if self._plain_ok(v):
                            try: seg[k] = copy.deepcopy(v)
                            except Exception: seg[k] = v
                        else: seg[k] = v
                    S['seg'] = seg
                    S['seg_log'] = {k: copy.deepcopy(v) for k, v in g['log'].items() if k not in pre_log or pre_log[k] != v}
                    S['st'] = self._fixed_structure(g, g['model'])
                    tm = S['final'].get('tri_materials'); c = g['cfg']
                    S['st']['tri_cfg'] = tuple(c[k] for k in ('tri_ke', 'tri_ka', 'tri_kd'))
                    S['st']['tri_uniform'] = bool(tm is not None and len(tm) and np.allclose(
                        tm[:, :3], np.array(S['st']['tri_cfg'], np.float32)[None], rtol=0, atol=0) and not tm[:, 3:].any())
                    for _n, test, why in NOT_KEPT:
                        if test(g): raise RuntimeError('not kept: ' + why)
                except Exception as exc:
                    keepable = False; S['why_not'] = repr(exc)
            if heavy and keepable:
                if self.heads[i].startswith('solver = '): S['solver_dict0'] = dict(vars(g['solver']))
                for root in ('solver', 'pipe'):
                    if root in g:
                        for p, a in self._arrays(g[root], root).items():
                            if a.is_contiguous and p not in S['heavy_snap']: S['heavy_snap'][p] = (a, wp.clone(a))
        if keepable:
            S['mod_state'] = [(m, a, copy.deepcopy(v)) for m in self._mods() for a, v in list(vars(m).items())
                              if not a.startswith('__') and isinstance(v, (int, float, bool, str, type(None), tuple, list, dict))]
        self.snap = S if keepable else None
        return dict(setup_s=round(time.perf_counter() - t0, 3), keepable=keepable, why_not=S.get('why_not'))

    def _begin(self, init_globals, mark):
        wp = self.wp; g = self.g; S = self.snap; T = {}; t0 = t = time.perf_counter()
        for m, a, v in S['mod_state']: setattr(m, a, copy.deepcopy(v))
        g.update(init_globals)
        for i in range(self.i_builder): exec(self.codes[i], g)
        T['before_builder'] = time.perf_counter() - t; t = time.perf_counter()
        vals, sw = self._finalize_values(g['X0'], g['d']['Xclo'] / 1000.0, S['st'], g['cfg'])
        T['finalize_values'] = time.perf_counter() - t; t = time.perf_counter()
        for k, v in S['seg'].items():
            g[k] = v.copy() if isinstance(v, np.ndarray) else copy.deepcopy(v) if self._plain_ok(v) else v
        g['X0m'] = g['X0']; g['Xc'] = g['d']['Xclo'] / 1000.0
        g['seen'] = sw['seen']; g['added'] = sw['added']; g['skipped'] = sw['skipped']
        for k, v in S['seg_log'].items(): g['log'][k] = copy.deepcopy(v)
        if 'seam_bend' in g['log']: g['log']['seam_bend'].update(active=sw['added'], skipped_opposite=sw['skipped'])
        mdl = g['model']
        for p, h in S['final'].items():
            if p.startswith('particle_color') or p == 'shape_source_ptr': continue
            a = getattr(mdl, p, None)
            if not isinstance(a, wp.array) or tuple(a.shape) != h.shape[:len(a.shape)]: raise RuntimeError('model array changed: ' + p)
            a.assign(vals[p] if p in vals else h)
        ids = {id(a) for a in self._arrays(mdl, 'model').values()}
        for p, (a, saved) in S['heavy_snap'].items():
            if id(a) not in ids: wp.copy(a, saved)
        sd = vars(g['solver']); sd.clear(); sd.update(S['solver_dict0'])
        for a, v in S['model_plain'].items(): setattr(mdl, a, v)
        wp.synchronize()          # the restores land before the statements after finalize (first begins took 1.6 s without)
        T['values'] = time.perf_counter() - t; t = time.perf_counter()
        prof = os.environ.get('AC9_RESIDENT_PROFILE') == '1'; slow = []
        for i in range(self.i_final + 1, self.loop_i):
            if i in self.i_heavy:
                g.update(S['heavy_globals'].get(i, {}))
                if i in S['heavy_solver']: vars(g['solver']).update(S['heavy_solver'][i])
                for k, v in S['heavy_log'].get(i, {}).items(): g['log'][k] = copy.deepcopy(v)
                continue
            t1 = time.perf_counter()
            exec(self.codes[i], g)
            # wait after each statement: without it a begin now and then took 1.6 s instead of 0.5 (GUI, 11/16)
            wp.synchronize()
            if prof: slow.append((round(time.perf_counter() - t1, 4), i, self.heads[i][:50].replace(chr(10), ' ')))
        wp.synchronize()
        if prof: T['slowest'] = sorted(slow, reverse=True)[:6]
        T['after_finalize'] = time.perf_counter() - t; T['total'] = time.perf_counter() - t0
        g['log']['resident'] = dict(begin_s={k: (round(v, 4) if isinstance(v, float) else v) for k, v in T.items()}, hstar_active=sw['added'])
        return dict(begin=T)
