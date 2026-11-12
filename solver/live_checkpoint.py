"""Experimental numeric Live checkpoints. GPU resources are always rebuilt."""
import hashlib
import json
import os
import importlib.metadata
import numpy as np

VERSION = 1
AUDITED_KEYS = frozenset('fps substeps density tri_ke tri_ka tri_kd edge_ke edge_kd stitch_kd particle_radius body_gap filter_rings rest_from_flat internal_filter_rings seam_bend pattern crop iterations stitch_ke self_margin self_gap body_mu fold_deg frames live active_pieces frozen_pieces stitch_ramp_frames live_every init_xv mi friction_epsilon membrane tri_ks bw_hess_reg fric_anchor bend_fric2 fold_ke_scale grab_file params_file fabric_pieces guard jitter layers stitch_edge_filter checkpoint_in checkpoint_out start_check gravity_z stitch_rest_mm temp_stitches strengthen_pieces'.split())
GLOBAL_FIELDS = ('BF2', 'FANCH', 'FOLD_RAMP', 'FOLD_CARRY', 'ELASTIC', 'ELASTIC_FROM',
                 'STRENGTHEN', 'DELAY', 'KE0', 'RL', 'DL0', 'L0s', 'LIVEP', 'TERMINAL_FIELD_STATE')


def _digest(value):
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def _validate_persistent(path, value):
    """Finite physical definitions/history; leave recomputed solver scratch unconstrained."""
    parts = path.split('.')
    persistent = (parts[0] in ('model', 'control', 'history') or
                  (parts[0] in ('s0', 's1') and len(parts)>1 and
                   parts[1] in ('particle_q', 'particle_qd', 'body_q', 'body_qd')) or
                  (parts[0] == 'solver' and len(parts)>1 and
                   parts[1] in ('particle_q_prev', 'ac9_fric_anchor', 'ac9_fric_n')) or
                  (parts[0] == 'globals' and len(parts)>1 and parts[1] in GLOBAL_FIELDS))
    if not persistent or path.startswith('globals.LIVEP.applied'):
        return
    array = np.asarray(value)
    if np.issubdtype(array.dtype, np.inexact) and not np.isfinite(array).all():
        raise ValueError('Nonfinite persistent physical state: ' + path)


def fingerprint(g):
    ignored = {'frames', 'live_every', 'params_file', 'grab_file', 'checkpoint_in', 'checkpoint_out'}
    cfg = {k: v for k, v in g['cfg'].items() if k not in ignored}
    files = {key: hashlib.sha256(open(g[key], 'rb').read()).hexdigest() for key in ('idx_f', 'av_f')}
    if g['cfg'].get('experimental_fold_terminal'):
        from terminal_checkpoint import generator_hash
        files['terminal_generator']=generator_hash()
    for key,value in list(cfg.items()):
        if isinstance(value,str) and os.path.isfile(value):
            files['input:'+key] = hashlib.sha256(open(value,'rb').read()).hexdigest()
            cfg.pop(key)
    files['config'] = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode('utf8')).hexdigest()
    files.update(source_fingerprint())
    for distribution in ('newton','warp-lang','numpy'):
        files['version:'+distribution]=importlib.metadata.version(distribution)
    files['effective_environment']={k:v for k,v in os.environ.items()if k.startswith(('NEWTON_','WP_'))}
    files['start_validation_override']=os.environ.get('AC9_START_CHECK_ALLOW')
    files['start_validation_caller']='addon' if os.environ.get('AC9_DRESS')=='1' else 'direct'
    import newton
    root=os.path.dirname(newton.__file__)
    digest=hashlib.sha256()
    for directory,folders,names in os.walk(root):
        folders.sort()
        for name in sorted(names):
            if name.endswith('.py'):
                path=os.path.join(directory,name);digest.update(os.path.relpath(path,root).encode());digest.update(open(path,'rb').read())
    files['newton_source']=digest.hexdigest()
    return files


def source_fingerprint():
    """The same code boundary in the Blender resume gate and the worker."""
    files = {}
    here = os.path.dirname(__file__)
    for directory,_,names in os.walk(here):
        for name in names:
            if name.endswith('.py') and not name.startswith('_resume_'):
                path=os.path.join(directory,name)
                files[os.path.relpath(path,here)] = hashlib.sha256(open(path,'rb').read()).hexdigest()
    return files


def build_signature(g):
    """Record immutable construction before restoring any dynamic state."""
    fields=('tri_indices','edge_indices','spring_indices','particle_world','tri_poses','tri_areas','edge_rest_length')
    model=g['model']
    result={name:_digest(getattr(model,name).numpy())for name in fields if getattr(model,name,None)is not None}
    if g['cfg'].get('experimental_fold_terminal'):
        state=g.get('TERMINAL_FIELD_STATE')
        if state is None:raise ValueError('Terminal definition is missing at construction')
        result['terminal_manifest']=state['manifest']['sha256']
    for i,group in enumerate(model.particle_color_groups):result['color:'+str(i)]=_digest(group.numpy())
    adjacency=model.soft_mesh_adjacency_device
    for name,value in vars(adjacency).items():
        if hasattr(value,'numpy'):result['adjacency:'+name]=_digest(value.numpy())
    return result


def bindings(g):
    """Explicit numeric roots; omit GPU resource IDs and pointer-bearing structs."""
    import warp as wp
    roots = {}
    for name in ('s0', 's1', 'control', 'solver', 'model'):
        obj = g[name]
        fields = {}
        for key, value in vars(obj).items():
            if name == 'model' and not key.startswith(('particle_', 'tri_', 'edge_', 'spring_', 'gravity')):
                continue
            if isinstance(value, wp.array):
                a = value.numpy()
                if not a.dtype.fields:
                    fields[key] = value
        roots[name] = fields
    roots['globals'] = {key: g[key] for key in GLOBAL_FIELDS if key in g}
    roots['history'] = {'first': g['res'].first, 'recent': g['res'].recent, 'n': g['res'].n}
    detector = getattr(g['solver'], 'trimesh_collision_detector', None)
    if g['cfg'].get('fast_live') and not g['solver'].particle_enable_self_contact and detector is None:
        roots['references'] = {}
    else:
        roots['references'] = {'detector_positions': g['solver'].trimesh_collision_detector.vertex_positions}
    return roots


def storage_layout(roots):
    """Compare shared storage/offset/strides without serializing device pointers."""
    import warp as wp
    fields = []
    def visit(value, path):
        if isinstance(value, wp.array) and value.size:
            element_bytes = value.numpy().nbytes // value.size
            end = int(value.ptr) + sum((n - 1) * s for n, s in zip(value.shape, value.strides)) + element_bytes
            fields.append((path, int(value.ptr), end, list(value.strides)))
        elif isinstance(value, dict):
            for key, child in value.items():visit(child, path + '/' + str(key))
        elif isinstance(value, (list, tuple)):
            for key, child in enumerate(value):visit(child, path + '/' + str(key))
    visit(roots, '')
    aliases = []
    for i, (left, a, ae, astrides) in enumerate(fields):
        for right, b, be, bstrides in fields[i+1:]:
            if max(a, b) < min(ae, be):aliases.append([left, right, b-a, astrides, bstrides])
    return sorted(aliases)


def save(path, g, frame):
    import warp as wp
    wp.synchronize()
    if g['cfg'].get('native_three'):
        from native_three_contract import SHAPE_ONLY_CHECKPOINT
        raise ValueError(SHAPE_ONLY_CHECKPOINT)
    if g.get('STRENGTHEN_TERMINAL_DIRTY'):
        raise ValueError('Strengthen changed with End Taper during Live; restart from the shown shape before a full-state checkpoint')
    if g.get('TERMINAL_FIELD_DIRTY'):
        raise ValueError('End taper or its fold angle changed during Live; restart from the shown shape before a full-state checkpoint')
    if g['N_G'] != len(g['d']['Xinit']) or not np.array_equal(g['sel'],np.arange(g['N_G'])):
        raise ValueError('The experimental checkpoint requires the audited full cloth mapping')
    audited = AUDITED_KEYS
    if g['cfg'].get('fast_live'):
        from fast_live_contract import validate_config
        validate_config(g['cfg'])
        audited = audited | {'fast_live','fast_live_contract','graph','self_contact',
                             'soft_contact_mu','soft_contact_kd','body_kd','stitch_delay',
                             'fold_ramp','grab_mode','init_from','init_from_pieces','drop_arms'}
    unknown=set(g['cfg'])-audited
    if g['cfg'].get('experimental_fold_terminal') and g.get('TERMINAL_FIELD_STATE') is not None:
        unknown.discard('experimental_fold_terminal')
    if unknown:
        raise ValueError('Checkpoint configuration has unaudited fields: '+', '.join(sorted(unknown)))
    for name in ('particle_q','particle_qd'):
        if not np.isfinite(getattr(g['s0'],name).numpy()).all():
            raise ValueError('A nonfinite accepted state cannot be checkpointed')
    unsupported = [name for name in ('DRIVE', 'CN', 'UNT', 'GATE', 'LOCK', 'MIRROR', 'BIND')
                   if g.get(name) is not None]
    if hasattr(g['solver'], 'ac9_layer_recovery_mesh_ids'):
        # Layer recovery owns rebuilt BVH IDs and reference-pair flags. They are
        # outside the audited numeric checkpoint contract; never save pointers.
        unsupported.append('active layer recovery')
    free = g.get('FREE')
    if free is not None and (free['launch'] or np.any(free['mask'].numpy()) or g.get('_through') is not None):
        unsupported.append('active FREE/through state')
    if g.get('GRB') is not None and np.any(g['GRB']['mask'].numpy()):
        unsupported.append('active grab state')
    if unsupported:
        raise ValueError('Experimental checkpoint has unaudited feature state: ' + ', '.join(unsupported))
    arrays = {}
    descriptions = {}

    def encode(value, path=''):
        if isinstance(value, (wp.array, np.ndarray)):
            a = value.numpy() if isinstance(value, wp.array) else value
            if a.dtype.hasobject or a.dtype.fields:
                raise ValueError('Checkpoint cannot serialize object arrays or pointer-bearing structs')
            _validate_persistent(path, a)
            key = 'a' + str(len(arrays))
            arrays[key] = a
            descriptions[key] = {'dtype': str(a.dtype), 'shape': list(a.shape), 'sha256': _digest(a)}
            return {'kind': 'warp' if isinstance(value, wp.array) else 'numpy', 'key': key}
        if isinstance(value, dict):
            return {'kind': 'dict', 'items': [[encode(k), encode(v, path+'.'+str(k) if path else str(k))] for k, v in value.items()]}
        if isinstance(value, (list, tuple, set)):
            return {'kind': type(value).__name__, 'items': [encode(v,path+'.'+str(i)) for i,v in enumerate(value)]}
        if isinstance(value, np.generic):
            value = value.item()
        if value is None or isinstance(value, (str, bool, int, float)):
            _validate_persistent(path, value)
            return {'kind': 'scalar', 'value': value}
        raise ValueError('Unsupported checkpoint value: ' + str(type(value)))

    roots = bindings(g)
    if g['cfg'].get('experimental_fold_terminal'):
        from terminal_checkpoint import validate as validate_terminal
        validate_terminal(g,roots,lambda value:value.numpy() if isinstance(value,wp.array) else value,frame)
        if fingerprint(g)!=g['_CHECKPOINT_FINGERPRINT']:raise ValueError('Terminal checkpoint inputs changed during execution')
    tree = encode(roots)
    manifest = {'version': VERSION, 'frame': int(frame), 'fingerprint': g['_CHECKPOINT_FINGERPRINT'],
                'arrays': descriptions, 'tree': tree, 'storage_layout': storage_layout(roots),
                'build_signature': g['_CHECKPOINT_BUILD'], 'start_origin': g['_CHECKPOINT_ORIGIN']}
    arrays['manifest'] = np.frombuffer(json.dumps(manifest, allow_nan=False).encode('utf8'), dtype=np.uint8)
    tmp = str(path) + '.tmp'
    with open(tmp, 'wb') as output:
        np.savez_compressed(output, **arrays)
        output.flush()
        os.fsync(output.fileno())
    os.replace(tmp, path)
    return manifest


def detector_root(path):
    """The state ('s0' or 's1') whose positions the saved cloth-cloth detector referred to.

    Native Live's last detection of a frame leaves it on s0. Fast Live's correction detects again on the substep's
    other buffer, so its checkpoints refer to s1 (and every Fast Live resume with cloth-cloth contact was refused as a
    changed binding). The resume rebinds the fresh detector to the same state before restore() compares the layout,
    which stays an exact comparison."""
    with np.load(path, allow_pickle=False) as archive:
        manifest = json.loads(archive['manifest'].tobytes().decode('utf8'))
    for pair in manifest.get('storage_layout', []):
        ends = set(pair[:2])
        if '/references/detector_positions' in ends:
            for root in ('s0', 's1'):
                if '/' + root + '/particle_q' in ends:
                    return root
    return 's0'


def restore(path, g):
    """Validate all content before mutating the newly built private solver."""
    import warp as wp
    with np.load(path, allow_pickle=False) as archive:
        manifest = json.loads(archive['manifest'].tobytes().decode('utf8'))
        if manifest['version'] != VERSION or manifest['fingerprint'] != fingerprint(g):
            raise ValueError('Live checkpoint input, solver version, or settings changed')
        if manifest['build_signature'] != g['_CHECKPOINT_BUILD']:
            raise ValueError('Live checkpoint topology or immutable construction differs')
        if not manifest.get('start_origin',{}).get('accepted') or int(manifest['frame'])<1:
            raise ValueError('Live checkpoint has no accepted execution provenance')
        arrays = {}
        for key, desc in manifest['arrays'].items():
            a = archive[key]
            if str(a.dtype) != desc['dtype'] or list(a.shape) != desc['shape'] or _digest(a) != desc['sha256']:
                raise ValueError('Incomplete or damaged Live checkpoint: ' + key)
            arrays[key] = a
    writes = []

    def decode(node, current, path=''):
        kind = node['kind']
        if kind in ('warp', 'numpy'):
            a = arrays[node['key']]
            _validate_persistent(path, a)
            if kind == 'warp':
                if not isinstance(current, wp.array):
                    raise ValueError('Checkpoint Warp binding is missing: ' + path)
                sample = current.numpy()
                if sample.shape != a.shape or sample.dtype != a.dtype:
                    raise ValueError('Checkpoint Warp binding differs')
                if current is g['s0'].particle_q or current is g['s0'].particle_qd:
                    if not np.isfinite(a).all():
                        raise ValueError('Live checkpoint contains a nonfinite physical state')
                writes.append((current, a))
                return current
            return a.copy()
        if kind == 'scalar':
            _validate_persistent(path, node['value'])
            return node['value']
        if kind == 'dict':
            result = {}
            for key_node, value_node in node['items']:
                key = decode(key_node, None)
                result[key] = decode(value_node, current.get(key) if isinstance(current, dict) else None,
                                     path+'.'+str(key) if path else str(key))
            return result
        values = [decode(item, current[i] if isinstance(current, (list, tuple)) and i < len(current) else None,path+'.'+str(i))
                  for i, item in enumerate(node['items'])]
        return tuple(values) if kind == 'tuple' else set(values) if kind == 'set' else values

    current = bindings(g)
    if storage_layout(current) != manifest['storage_layout']:
        raise ValueError('Live checkpoint storage alias, offset, or stride binding differs')
    roots = decode(manifest['tree'], current)
    if g['cfg'].get('experimental_fold_terminal') or roots['globals'].get('TERMINAL_FIELD_STATE') is not None:
        from terminal_checkpoint import validate as validate_terminal
        pending={id(destination):value for destination,value in writes}
        validate_terminal(g,roots,lambda value:pending[id(value)] if isinstance(value,wp.array) else value,int(manifest['frame']))
    # Preserve this process's Live file path; the stored values and ramp progress persist.
    if roots['globals'].get('LIVEP') is not None:
        roots['globals']['LIVEP']['path'] = g['LIVEP']['path']
        roots['globals']['LIVEP']['mt'] = None
    for destination, value in writes:
        destination.assign(value)
    for key, value in roots['globals'].items():
        g[key] = value
    for key, value in roots['history'].items():
        setattr(g['res'], key, value)
    wp.synchronize()
    return manifest
