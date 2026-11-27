# SPDX-License-Identifier: GPL-3.0-or-later
"""Resident Live (DECISIONS cloth-sim-hstar-resident-2026-11-16; part of Preferences > Fast Simulation Start):
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
FILTER_OUTPUTS = ('tri_of', 'ring', 'R', 'R_int', 'vfilter', 'GATE_ROWS', 'efilter', 'SEAM_TRI')
# 1-D (review 2026-11-16): shapes a begin was never checked on -> not kept (built as today)
NOT_KEPT = (('BOUND_COUPLING', lambda g: bool(g.get('BOUND_COUPLING')), 'one layer (bound coupling)'),
            ('WELD', lambda g: g.get('WELD') is not None, 'weld'),
            ('anchor_ke', lambda g: bool(g['cfg'].get('anchor_ke')), 'particles outside the cloth (anchor)'),
            ('seam_paths', lambda g: bool(g['cfg'].get('seam_paths')), 'particles outside the cloth (Pull Round)'),
            ('sym_color', lambda g: bool(g['cfg'].get('sym_color') and g['cfg'].get('mirror_map')), 'mirror coloring'),
            ('ghost_pieces', lambda g: bool(g['cfg'].get('ghost_pieces')), 'two worlds (ghost pieces)'),
            # fold options that read the start shape to set their hinges (test switches): the start shape is not in the key
            ('fold_shape', lambda g: any(g['cfg'].get(k) for k in ('fold_ramp', 'fold_turn', 'fold_profile', 'iron', 'fold_from_drape')),
             'a fold option that reads the start shape'))


def _digest(path, skip=()):
    z = np.load(path); h = hashlib.sha256()
    for k in sorted(z.files):
        if k in skip: continue
        a = z[k]; h.update(k.encode()); h.update(str(a.dtype).encode()); h.update(str(a.shape).encode()); h.update(a.tobytes())
    return h.hexdigest()


def structure_key(idx, avatar, cfg):
    try:from .prepared_live import literal_config_field
    except ImportError:from prepared_live import literal_config_field
    c = {k: v for k, v in cfg.items() if k not in PATH_KEYS and k not in VALUE_KEYS}
    c.pop('live_keep', None)            # which frames frames.npy keeps: the output only, never the model
    for k, v in list(c.items()):          # a file the job folder holds (pattern.npz): its contents, not where it is
        if isinstance(v, str) and not literal_config_field((k,)) and len(v) < 1024 and os.path.isfile(v):
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
        self.i_filters = h.index('tri_of = None')
        self.i_filters_end = next(i for i in range(self.i_filters, len(h)) if h[i].startswith("if cfg.get('layer_order'):"))
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
                n.rsplit('.',1)[-1] not in ('job_cancel','lifecycle_trace') and
                os.path.abspath(getattr(m, '__file__', '') or '').startswith(self.source)]

    def _hstar_structure(self, g):
        bnd, partner, IDX = g['bnd'], g['partner'], g['HSTAR_IDX']; bnd_at = g['bnd_at']; out_ = []
        for e, (k, l, oA) in bnd.items():
            for bk in partner.get(k, []):
                for bl in partner.get(l, []):
                    key = (frozenset((k, l)), bk)
                    if frozenset((bk, bl)) in bnd:
                        oB = bnd[frozenset((bk, bl))][2]
                        # None: a hinge newton_vbd never made (hinge_ok: not four different points)
                        out_.append(('F', key, IDX.get((frozenset((k, l)), bk, oB)), (oA, oB, k, l)))
                    else:
                        cand = bnd_at.get(bk, [])
                        if not cand: continue
                        rows = [(y_, IDX.get((frozenset((k, l)), bk, oB_)), (oA, oB_, k, l)) for y_, oB_ in cand]
                        out_.append(('N', key, bk, bl, rows))
        return out_

    @staticmethod
    def _hstar_switch(S, X0m, Xc, dih):
        on = {}; seen = set(); added = skipped = dropped = 0
        # Batch the geometric test, retaining the exact scalar predicate at
        # its decision boundary. Row selection and duplicate precedence below
        # stay in their original order.
        hinges = list(dict.fromkeys(tuple(q) for s in S
                      for q in ([s[3]] if s[0] == 'F' and s[2] is not None else
                                [r[2] for r in s[4] if r[1] is not None] if s[0] != 'F' else [])))
        accepted = {}
        if hinges:
            H = np.asarray(hinges, np.int64)
            a, b, k, l = (Xc[H[:, i]] for i in range(4))
            n1 = np.cross(k-a, l-a); n2 = np.cross(l-b, k-b)
            cosine = (n1*n2).sum(1)/(np.linalg.norm(n1,axis=1)*np.linalg.norm(n2,axis=1)+1e-18)
            angles = np.degrees(np.arccos(np.clip(cosine,-1,1)))
            for hinge, angle in zip(hinges, angles):
                if not np.isfinite(angle) or abs(angle-120) <= 1e-7:
                    angle = dih(*hinge, Xc)
                accepted[hinge] = bool(angle <= 120)
        ok_ = lambda q: accepted[tuple(q)]
        for s in S:
            if s[0] == 'F':
                _, key, row, ok = s
                if key not in seen:
                    seen.add(key)
                    if row is None: dropped += 1       # never made (newton_vbd counts it as dropped)
                    elif ok_(ok): on[row] = True; added += 1
                    else: skipped += 1
            else:
                _, key, bk, bl, rows = s
                dist = [np.linalg.norm(X0m[y_] - X0m[bl]) for y_, _, _ in rows]
                j = min(range(len(rows)), key=lambda q: dist[q])
                if key not in seen and not (dist[j] > np.linalg.norm(X0m[bk] - X0m[bl])):
                    seen.add(key)
                    if rows[j][1] is None: dropped += 1  # never made (newton_vbd counts it as dropped)
                    elif ok_(rows[j][2]): on[rows[j][1]] = True; added += 1
                    else: skipped += 1
        return on, seen, added, skipped, dropped

    def _fixed_structure(self, g, model):
        import newton
        from resident_values import builder_identity,verified_warp_lengths
        cfg = g['cfg']
        st = dict(tri=model.tri_indices.numpy().copy(), edges=model.edge_indices.numpy().copy(),
                  springs=model.spring_indices.numpy().reshape(-1, 2).copy(), e0=int(g['_hstar_e0']),
                  n_particles=int(model.particle_count), rest_s=float(g['rest_s']))
        st['hstar'] = self._hstar_structure(g); st['dihedral'] = g['dihedral']
        st['array_values'],st['builder_identity']=builder_identity(newton.ModelBuilder)
        st['warp_lengths_verified']=st['array_values'] and verified_warp_lengths(self.wp)
        return st

    def _finalize_values(self, X0, Xc, st, cfg):
        import newton
        wp = self.wp; X0 = np.asarray(X0)
        if st['n_particles'] != len(X0): raise RuntimeError('particles outside the cloth')
        P32 = X0.astype(np.float32)
        T = st['tri']; nt = len(T)
        E = st['edges']; e0 = st['e0']
        kw = dict(density=cfg.get('density', 0.14), tri_ke=cfg['tri_ke'], tri_ka=cfg['tri_ka'], tri_kd=cfg['tri_kd'])
        if st.get('array_values'):
            from resident_values import geometry_values
            areas,poses,base_angles,base_lengths=geometry_values(P32,T,E[:e0])
            model_areas=areas
            materials=np.tile(np.array([kw['tri_ke'],kw['tri_ka'],kw['tri_kd'],0.,0.]),(nt,1))
        else:
            sc = newton.ModelBuilder(); sc.particle_q = P32.astype(np.float64)
            areas = sc.add_triangles(T[:, 0], T[:, 1], T[:, 2], tri_ke=[kw['tri_ke']] * nt, tri_ka=[kw['tri_ka']] * nt,
                                 tri_kd=[kw['tri_kd']] * nt, tri_drag=[0.0] * nt, tri_lift=[0.0] * nt)
            if len(sc.tri_areas) != nt: raise RuntimeError('a degenerate triangle at this start shape')
            sc.add_edges(E[:e0, 0], E[:e0, 1], E[:e0, 2], E[:e0, 3])
            poses=sc.tri_poses;materials=sc.tri_materials;model_areas=sc.tri_areas
            base_angles=np.asarray(sc.edge_rest_angle);base_lengths=np.asarray(sc.edge_rest_length)
        spring_positions=P32 if st.get('array_values') else np.asarray(sc.particle_q,dtype=np.float32)
        m = np.zeros(len(X0))
        np.add.at(m, T.reshape(-1), np.repeat(kw['density'] * np.asarray(areas, dtype=np.float64) / 3.0, 3))
        rest_ang = np.concatenate((base_angles,np.full(len(E)-e0,st['rest_s']))).astype(np.float32)
        rl = base_lengths.tolist()
        # Match Warp's float32 length arithmetic and its addition order. A
        # generic dot/norm can contract/reorder operations and differ by a bit.
        extra=E[e0:]
        if st.get('array_values') and st.get('warp_lengths_verified'):
            dd=P32[extra[:,3]]-P32[extra[:,2]]
            lengths=np.sqrt((dd[:,0]*dd[:,0]+dd[:,1]*dd[:,1])+dd[:,2]*dd[:,2])
            rl.extend(lengths.tolist())
        else:
            for i,j,k,l in extra:rl.append(wp.length(wp.vec3(*P32[l])-wp.vec3(*P32[k])))
        sl = []
        for a, b in st['springs']:
            p = spring_positions[a]; q = spring_positions[b]
            dd = np.subtract(p, q); sl.append(np.sqrt(np.dot(dd, dd)))
        on, seen, added, skipped, dropped = self._hstar_switch(st['hstar'], X0, Xc, st['dihedral'])
        sb = cfg.get('seam_bend') or {}
        bp = np.zeros((len(E), 2)); bp[:e0] = (cfg['edge_ke'], cfg['edge_kd'])
        for r in on: bp[r] = (float(sb.get('ke', cfg['edge_ke'])), cfg['edge_kd'])
        ms = m.astype(np.float32)
        vals = dict(particle_q=P32, particle_mass=ms, particle_inv_mass=np.divide(1.0, ms, out=np.zeros_like(ms), where=ms != 0.0),
                    tri_poses=np.array(poses, dtype=np.float32), tri_areas=np.array(model_areas, dtype=np.float32),
                    edge_rest_angle=rest_ang, edge_rest_length=np.array(rl, dtype=np.float32),
                    edge_bending_properties=bp.astype(np.float32), spring_rest_length=np.array(sl, dtype=np.float32))
        if st.get('tri_uniform'):          # 1-A: the stretch (tri_ke / ka / kd) of this job, not the kept one's
            vals['tri_materials'] = np.array(materials, dtype=np.float32)
        elif tuple(cfg[k] for k in ('tri_ke', 'tri_ka', 'tri_kd')) != st.get('tri_cfg'):
            raise RuntimeError('the stretch changed and the build wrote per-triangle values')
        return vals, dict(seen=seen, added=added, skipped=skipped, dropped=dropped)

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
        if self.g is not None:
            self.runtime = self.g.get('_NATIVE_RUNTIME')
            self._close_inputs(self.g)
            self.g.clear()
        self.g = None; self.key = None; self.snap = None

    @staticmethod
    def _close_inputs(g):
        # Kept arrays do not need the NPZ zip handles after a completed job.
        # Keeping those open also keeps a consumed prepared job's folder locked
        # on Windows. The next begin reloads its own inputs before the builder.
        for value in list(g.values()):
            if isinstance(value, np.lib.npyio.NpzFile):
                value.close()

    def run(self, idx, avatar, cfg, init_globals, mark=lambda *a, **k: None, build_only=False):
        try:
            return self._run(idx,avatar,cfg,init_globals,mark,build_only)
        except BaseException:
            self.drop()
            raise

    def _run(self, idx, avatar, cfg, init_globals, mark=lambda *a, **k: None, build_only=False):
        """-> dict(mode='fresh'|'begin', setup_s, ...). Runs the whole script (setup + loop + writing).

        build_only (REVIEW_fable_edit_time_begin_2026-11-21, step 2): a job sent while the user edits. It builds the
        model up to the loop (the same statements a fresh job runs), keeps it under its key and returns: no frame, no
        result files. The press is an ordinary job; its key decides begin or fresh. A model of the same key is already
        kept -> nothing to do ('kept')."""
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
        if build_only:
            if self.g is not None and key is not None and key == self.key:
                info = dict(mode='kept', setup_s=0.0, keepable=True, why_not=None)
            else:
                # up to the last heavy statement only: begin runs every later one again on this job (graph, guard,
                # jitter, start check: about 0.22 s of a 1.77 s build on a T-shirt, 2026-11-26)
                self.drop(); info = self._fresh(init_globals, mark, upto=max(self.i_heavy) + 1 if self.i_heavy else None)
                self.key = key if (key is not None and info.get('keepable')) else None
                info['mode'] = 'build'
            if self.g is not None: self._close_inputs(self.g)
            info.update(why_fresh=self.why, vram=self.vram())
            if self.key is None: self.drop()
            self.last = info
            return info
        if self.g is not None and key is not None and key == self.key:
            try:
                ff = os.environ.get('AC9_TEST_RESIDENT_FAIL_FILE')       # tests: make this one begin fail
                if ff and os.path.isfile(ff):
                    os.remove(ff); raise RuntimeError('injected begin failure')
                info = self._begin(init_globals, mark); mode = 'begin'
            except BaseException as exc:
                runtime=self.g.get('_NATIVE_RUNTIME') if self.g is not None else None
                claimed=runtime is not None and getattr(runtime,'claimed',False) and not getattr(runtime,'closed',False)
                # 1-C: a cancel (job_cancel.CanceledJob) or an exit is not a failed begin: nothing kept, passed on
                if claimed or type(exc).__name__ == 'CanceledJob' or not isinstance(exc, Exception):
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
        finally:
            self._close_inputs(g)
        info.update(mode=mode, run_s=round(time.perf_counter() - t, 3), why_fresh=self.why, vram=self.vram())
        if self.key is None: self.drop()
        self.last = info
        return info

    def _fresh(self, init_globals, mark, upto=None):
        """upto (a build only): the statements before it, not up to the loop. Only for a model kept for a begin,
        which runs every statement after finalize except the heavy ones again."""
        wp = self.wp; g = dict(__name__='__main__', __file__=self.path, **init_globals); self.g = g
        g['_AC9_TOPOLOGY_CACHE'] = {}
        S = dict(heavy_snap={}, heavy_solver={}, heavy_globals={}, heavy_log={})
        t0 = time.perf_counter(); keepable = True
        for i in range(self.loop_i if upto is None else min(upto, self.loop_i)):
            if i == self.i_builder:
                pre_ids = {k: id(v) for k, v in g.items()}; pre_log = copy.deepcopy(g['log'])
            heavy = i in self.i_heavy
            pre_sd = dict(vars(g['solver'])) if heavy and 'solver' in g else None
            pre_g = dict(g) if heavy else None
            pre_hlog = dict(g['log']) if heavy and 'log' in g else None
            exec(self.codes[i], g)
            if i == self.i_filters_end - 1:
                # S: only mesh/seam/config topology, before shape-dependent
                # contact state. Clone on every begin: later stages may add
                # layer exclusions to these maps.
                # The maps are read only after this point except vfilter / efilter, which the layer blocks
                # extend (the same rule _restore_stitch_filters relies on): those two get their own sets, the
                # rest (tri_of, ring, GATE_ROWS, SEAM_TRI ...) is kept by reference: a later statement of newton_vbd
                # that writes into one of those would change this kept copy too. A deepcopy of the whole Python graph took 0.4 s per fresh build
                # (6-piece T-shirt, 2026-11-21).
                S['stitch_filters'] = {k: g[k] for k in FILTER_OUTPUTS}
                for key in ('vfilter', 'efilter'):
                    S['stitch_filters'][key] = {k: set(v) for k, v in g[key].items()}
                S['stitch_filter_log'] = copy.deepcopy(g['log'].get('stitch_edge_filter'))
            if pre_sd is not None: S['heavy_solver'][i] = {k: v for k, v in vars(g['solver']).items() if k not in pre_sd or pre_sd[k] is not v}
            if pre_hlog is not None: S['heavy_log'][i] = {k: copy.deepcopy(v) for k, v in g['log'].items() if k not in pre_hlog or pre_hlog[k] is not v}
            if pre_g is not None: S['heavy_globals'][i] = {k: v for k, v in g.items() if k not in pre_g or pre_g[k] is not v}
            if i == self.i_final:
                try:
                    if not g.get('HSTAR_ROWS'):
                        raise RuntimeError('H* rows off: ' + (g['log'].get('hstar_rows_off') or ('no hstar_rows in the settings' if 'HSTAR_ROWS' in g else 'no seam bending')))
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
        if 'seam_bend' in g['log']: g['log']['seam_bend'].update(active=sw['added'], skipped_opposite=sw['skipped'], dropped_degenerate=sw['dropped'])
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
            if self.i_filters <= i < self.i_filters_end:
                if i == self.i_filters:
                    tf = time.perf_counter()
                    self._restore_stitch_filters(g, S)
                    T['stitch_filters'] = time.perf_counter() - tf
                continue
            if i in self.i_heavy:
                g.update(S['heavy_globals'].get(i, {}))
                if i in S['heavy_solver']: vars(g['solver']).update(S['heavy_solver'][i])
                for k, v in S['heavy_log'].get(i, {}).items(): g['log'][k] = copy.deepcopy(v)
                if self.heads[i].startswith('solver = '):
                    # The solver built its self-contact trees on the kept job's start shape; a refit keeps that
                    # tree's layout, so the contacts came in another order (a fresh job differed by 7 um after one
                    # frame, build-only begins 10/10, kept begins 4/10; RESULT edit-time-begin 2026-11-21). Build
                    # them on this job's shape, as the constructor did (its vertex_positions: model.particle_q).
                    det = getattr(g['solver'], 'trimesh_collision_detector', None)
                    if det is not None: det.rebuild(g['model'].particle_q)
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

    @staticmethod
    def _restore_stitch_filters(g, snapshot):
        """Restore structure-only exclusions; never carry a frame's filter state."""
        # All consumers only read adjacency, gate rows and seam membership.
        # Only the layer blocks below this stage extend vfilter/efilter; give
        # those blocks their own maps. Copying the full Python graph per begin
        # costs more than building it.
        g.update(snapshot['stitch_filters'])
        if g['cfg'].get('layer_order') or len(g['OL_KIN']):
            for key in ('vfilter', 'efilter'):
                g[key] = {k: set(v) for k, v in snapshot['stitch_filters'][key].items()}
        if snapshot['stitch_filter_log'] is not None:
            g['log']['stitch_edge_filter'] = copy.deepcopy(snapshot['stitch_filter_log'])
