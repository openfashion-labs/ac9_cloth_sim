# SPDX-License-Identifier: GPL-3.0-or-later
"""One-shot prepared Native3 worker protocol; no scene or GPU state serialization."""
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import shutil
import sys
import uuid
import numpy as np

VERSION = 1
IO_KEYS = frozenset(('pattern', 'grab_file', 'params_file', 'checkpoint_out',
                     'native_runtime_dir', 'native_runtime_token'))


def write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def environment_id(environment):
    values = {k: v for k, v in environment.items()
              if k.startswith(('NEWTON_', 'WP_', 'AC9_TEST_'))
              or k in ('AC9_DRESS', 'AC9_START_CHECK_ALLOW')}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode('utf8')).hexdigest()


def dependency_manifest():
    result = {}
    for package in ('numpy', 'warp-lang', 'newton'):
        distribution = importlib.metadata.distribution(package)
        metadata = next(path for path in distribution.files or ()
                        if path.name == 'METADATA' and path.parent.suffix == '.dist-info')
        path = Path(distribution.locate_file(metadata)).resolve()
        result[package] = dict(version=distribution.version, path=str(path),
                               sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def code_id():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode('utf8'))
        digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def source_id(manifest=None):
    """Always fresh. Blender can verify the actual worker's dependency metadata."""
    manifest = dependency_manifest() if manifest is None else manifest
    for row in manifest.values():
        if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Prepared worker dependency metadata changed')
    return hashlib.sha256(json.dumps(dict(code=code_id(), dependencies=manifest),
                                     sort_keys=True).encode('utf8')).hexdigest()


def _array(digest, name, values):
    values = np.asarray(values)
    if values.dtype.hasobject:
        raise ValueError('Object arrays are not eligible for prepared Live')
    header = json.dumps([name, values.dtype.str, list(values.shape)]).encode('utf8')
    raw = np.ascontiguousarray(values).tobytes()
    digest.update(len(header).to_bytes(8, 'little')); digest.update(header)
    digest.update(len(raw).to_bytes(8, 'little')); digest.update(raw)


def file_id(path):
    path = Path(path); digest = hashlib.sha256()
    if path.suffix.lower() == '.npz':
        with np.load(path, allow_pickle=False) as archive:
            for key in sorted(archive.files):
                _array(digest, key, archive[key])
    elif path.suffix.lower() == '.npy':
        _array(digest, '', np.load(path, allow_pickle=False))
    else:
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                digest.update(block)
    return digest.hexdigest()


def fingerprint(idx, avatar, config, env_id, *, implementation=None):
    """Separate physical inputs from the new job's output/control ownership."""
    config = dict(config)
    if config.get('native_three') is not True or not config.get('live') or config.get('checkpoint_in'):
        raise ValueError('Prepared Live requires shape-restart Native3 Live')
    for key in ('untangle', 'iron', 'experimental_fold_terminal', 'init_from', 'color_dump',
                'watch', 'diag', 'hinges', 'stitch_lock'):
        if config.get(key):
            raise ValueError('This option is not audited for prepared Live: ' + key)
    files = {'idx': file_id(idx), 'avatar': file_id(avatar)}
    def normalize(value, name):
        if isinstance(value, dict):
            return {key: normalize(child, name + '.' + key) for key, child in value.items()}
        if isinstance(value, list):
            return [normalize(child, name + '[' + str(i) + ']') for i, child in enumerate(value)]
        if isinstance(value, str) and Path(value).is_file():
            files['config:' + name] = file_id(value)
            return {'input_file': name}
        return value
    for key, value in list(config.items()):
        if key in IO_KEYS:
            if key == 'pattern':
                files['pattern'] = file_id(value)
            config.pop(key)
        else:
            config[key] = normalize(value, key)
    raw = json.dumps(dict(version=VERSION, source=source_id() if implementation is None else implementation, environment=env_id,
                          files=files, config=config), sort_keys=True, allow_nan=False).encode('utf8')
    return hashlib.sha256(raw).hexdigest()


def state_signature(g):
    """Test evidence that waiting/rebinding did not advance the prepared model."""
    from live_checkpoint import bindings, GLOBAL_FIELDS
    roots = bindings(g)
    digest = hashlib.sha256()
    def visit(name, value):
        if hasattr(value, 'numpy'):
            _array(digest, name, value.numpy())
        elif isinstance(value, np.ndarray):
            _array(digest, name, value)
        elif isinstance(value, dict):
            for key in sorted(value, key=str):
                if name == 'globals.LIVEP' and key in ('path', 'mt'):
                    continue
                visit(name + '.' + str(key), value[key])
        elif isinstance(value, (tuple, list)):
            for i, child in enumerate(value):visit(name + '.' + str(i), child)
        elif value is None or isinstance(value, (str, bool, int, float, np.generic)):
            digest.update((name + ':' + repr(value)).encode('utf8'))
    # The live checkpoint exposes physical state/history roots, not GPU handles.
    for key in ('s0', 's1', 'model', 'control', 'solver', 'history'):
        if key in roots:visit(key, roots[key])
    for key in GLOBAL_FIELDS:
        if key in g:visit('globals.' + key, g[key])
    visit('resume_frame', g.get('_RESUME_FRAME'))
    for root in ('solver', 'control'):
        for key, value in vars(g[root]).items():
            if isinstance(value, (bool, int, float, np.generic)):
                visit(root + '.scalar.' + key, value)
    return digest.hexdigest()


def resource_signature(g):
    """Explicit retained arrays, contact queues, detector references and graphs.

    This inventories these resources; it is not a claim to capture every possible
    native-library internal state or every pointer hidden inside a CUDA graph.
    """
    import warp as wp
    arrays = {}
    visited = set()
    def visit(value, name):
        if isinstance(value, wp.array):
            values = value.numpy(); digest = hashlib.sha256()
            _array(digest, '', values)
            arrays[name] = dict(ptr=int(value.ptr or 0), shape=list(value.shape),
                                strides=list(value.strides), values=digest.hexdigest())
        elif isinstance(value, (dict, tuple, list)):
            if id(value) in visited:return
            visited.add(id(value))
            items = sorted(value.items(), key=lambda pair:str(pair[0])) if isinstance(value,dict) else enumerate(value)
            for key, child in items:visit(child,name+'.'+str(key))
    for name in sorted(g):
        if name != '__builtins__':visit(g[name],'global.'+name)
    solver = g['solver']
    objects = {key:g[key] for key in ('model','solver','control','s0','s1','pipe','contacts')}
    objects['queue'] = getattr(solver,'ac9_self_contact_queue',None)
    objects['detector'] = getattr(solver,'trimesh_collision_detector',None)
    objects['adjacency'] = getattr(g['model'],'soft_mesh_adjacency_device',None)
    for name, value in objects.items():
        if value is not None:
            for key, child in vars(value).items():visit(child,name+'.'+key)
    graphs = {name:id(g[name]) for name in ('graph','graph_free') if g.get(name)is not None}
    record = dict(arrays=arrays, object_ids={key:id(value) for key,value in objects.items()},
                  graphs=graphs, widths=list(solver.ac9_native_elastic_widths))
    encoded = json.dumps(record,sort_keys=True).encode('utf8')
    return dict(sha256=hashlib.sha256(encoded).hexdigest(),array_count=len(arrays),
                array_names=sorted(arrays),graphs=graphs,widths=record['widths'])


def initial_hashes(g):
    values = {key:getattr(g['model'],key).numpy() for key in
              ('particle_mass','particle_inv_mass','tri_indices','tri_poses','tri_materials',
               'edge_indices','edge_rest_angle','edge_bending_properties','spring_indices',
               'spring_rest_length','spring_stiffness') if getattr(g['model'],key)is not None}
    values.update(q=g['s0'].particle_q.numpy(),qd=g['s0'].particle_qd.numpy(),sel=g['sel'])
    result = {}
    for key, value in values.items():
        digest=hashlib.sha256();_array(digest,key,value);result[key]=digest.hexdigest()
    return result


class Gate:
    def __init__(self, specification):
        self.spec = specification
        self.env_id = environment_id(os.environ)
        self.token = specification['token']
        self.generation = specification['generation']
        if (not isinstance(self.token, str) or re.fullmatch('[0-9a-f]{32}', self.token) is None
                or type(self.generation) is not int or self.generation < 0):
            raise ValueError('Invalid prepared worker ownership')
        self.status = Path(specification['status'])
        self.dependencies = dependency_manifest()
        self.implementation = source_id(self.dependencies)
        self.initial = fingerprint(specification['idx'], specification['avatar'],
                                   specification['config'], self.env_id, implementation=self.implementation)
        self.accepted = False
        self.audit = {}
        self.latest = {}
        self.canonical_stage = None
        self.owned_log = None
        self.update('loading')

    def update(self, phase, **fields):
        row = dict(version=VERSION, token=self.token, generation=self.generation,
                   pid=os.getpid(), phase=phase, fingerprint=self.initial,
                   implementation=self.implementation, dependencies=self.dependencies, **self.audit)
        row.update(fields)
        self.latest.update(row)
        write(self.status, self.latest)

    def progress(self,stage):
        if self.accepted:return
        steps={'imported':(1,'model'),'finalized':(2,'model'),'solver':(3,'kernels')}
        if stage not in steps:return
        completed,phase=steps[stage]
        try:self.update('loading',completed=completed,preload_phase=phase)
        except OSError:pass

    def __call__(self, g):
        if self.accepted or not g.get('NATIVE_THREE') or g['log'].get('error'):
            raise ValueError('Invalid prepared Live state')
        if source_id() != self.implementation:
            raise ValueError('Prepared worker source changed while building')
        if fingerprint(g['idx_f'], g['av_f'], g['cfg'], self.env_id, implementation=self.implementation) != self.initial:
            raise ValueError('Prepared inputs changed while building')
        before = state_signature(g) if self.spec.get('audit_state') else None
        resources_before = resource_signature(g) if before is not None else None
        initial_before = initial_hashes(g) if before is not None else None
        self.update('prepared',completed=4,preload_phase='kernels',state_signature=before, retained_resources=resources_before,initial_arrays=initial_before)
        line = sys.stdin.readline(16385)
        if not line:
            self.update('canceled', reason='EOF before start')
            raise SystemExit(0)
        if len(line) > 16384:
            raise ValueError('Prepared start command is too large')
        command = json.loads(line)
        if (command.get('op') != 'start' or command.get('token') != self.token
                or type(command.get('generation')) is not int or command.get('generation') != self.generation):
            raise ValueError('Stale or invalid prepared start command')
        idx, avatar, cfg_path, stage = map(Path, command['args'])
        if source_id() != self.implementation:
            raise ValueError('Prepared worker source changed while waiting')
        cfg = json.loads(cfg_path.read_text(encoding='utf8'))
        actual = fingerprint(idx, avatar, cfg, self.env_id, implementation=self.implementation)
        if actual != self.initial or command.get('fingerprint') != actual:
            raise ValueError('Prepared inputs do not match the new job')
        if any((stage / name).exists() for name in ('run_log.json', 'frames.npy', 'live.npy')):
            raise ValueError('Prepared Live refuses an existing result target')
        # Claim the new runtime before opening/truncating any canonical log or
        # changing any output binding. Validation alone is not an exclusive claim.
        from native_runtime_io import WorkerRuntime
        runtime = WorkerRuntime(cfg, stage)
        g['_AC9_CLEANUP'](self.close)
        g['_AC9_CLEANUP'](runtime.close)
        runtime.start()
        self.accepted = True
        self.canonical_stage = stage
        # The new job owns all subsequent outputs. No old cache/token is reused.
        cfg_original = g['cfg']; cfg_original.clear(); cfg_original.update(cfg)
        g['log']['cfg'] = cfg_original
        old_stage = Path(g['out'])
        g.update(idx_f=str(idx), av_f=str(avatar), cfg_f=str(cfg_path), out=str(stage))
        if g.get('LIVEP') is not None:
            g['LIVEP']['path'] = cfg.get('params_file') or str(stage / 'params.json')
        sys.stdout.flush(); sys.stderr.flush()
        self.owned_log = (stage / 'stdout.txt').open('w', encoding='utf8')
        runtime.old_stdout = runtime.old_stderr = self.owned_log
        for name in ('start_check.json',):
            if (old_stage / name).is_file():shutil.copy2(old_stage / name, stage / name)
        write(stage / 'lifecycle.json', dict(pid=os.getpid(), ppid=os.getppid(), prepared_token=self.token))
        g['_NATIVE_RUNTIME'] = runtime
        g['LIVE_OUT'] = str(runtime.directory)
        g['log']['native_runtime_io'] = dict(directory=str(runtime.directory), job_directory=str(stage))
        after = state_signature(g) if before is not None else None
        resources_after = resource_signature(g) if before is not None else None
        initial_after = initial_hashes(g) if before is not None else None
        if before != after:
            raise ValueError('Prepared start changed physical state or history')
        if resources_before != resources_after:
            raise ValueError('Prepared start changed retained arrays or graph storage')
        if initial_before != initial_after:
            raise ValueError('Prepared start changed initial numeric inputs')
        self.audit = dict(state_signature_before=before, state_signature_after=after,
                          retained_resources_before=resources_before,retained_resources_after=resources_after)
        self.update('accepted', job=str(stage), state_signature_before=before, state_signature_after=after)

    def close(self):
        if self.owned_log is not None:
            self.owned_log.flush(); self.owned_log.close(); self.owned_log = None
