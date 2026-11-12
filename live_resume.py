# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict no-edit Live resume preparation. Solver checkpoints are validated again by Newton."""
import hashlib
import json
import os
import shutil
import numpy as np
from . import placement as PL, store
from .core import presets


def capture(context, h, *, spec=None):
    spec = store.get(context)[0] if spec is None else spec
    props = context.scene.ac9_cloth_sim
    settings = presets.resolve(getattr(props, 'preset', ''), props,
                               environ=None if __import__('bpy').app.background else {})
    digest = hashlib.sha256()
    own = 'AC9_Garment_' + spec.get('name', '')
    objects = [props.body] if props.body else []
    for obj in context.scene.objects:
        if obj.type == 'MESH' and obj not in objects and (obj.get('ac9_collider') or
                (obj.name.startswith('AC9_Garment_') and obj.name != own)):
            objects.append(obj)
    for obj in sorted(objects, key=lambda v: v.name):
        digest.update(obj.name.encode('utf8'))
        for a in PL.body_arrays(context, obj):
            a = np.ascontiguousarray(a)
            if np.issubdtype(a.dtype,np.floating) and not np.isfinite(a).all():
                raise ValueError('A collider contains nonfinite coordinates')
            digest.update(str(a.dtype).encode());digest.update(str(a.shape).encode());digest.update(a.tobytes())
    return {'version': 1, 'spec': json.dumps(spec, sort_keys=True, ensure_ascii=False),
            'settings': json.dumps(settings, sort_keys=True, default=str), 'h': float(h),
            'environment': digest.hexdigest()}


def write_gate(folder, gate):
    with open(os.path.join(folder, 'resume_gate.json'), 'w', encoding='utf8') as output:
        json.dump(gate, output, ensure_ascii=False)


def prepare(context, h, previous, shown, folder, gate, last_frame):
    """Return a copied original-model job only when all strict no-edit checks pass."""
    if not previous or shown is None:
        return None, None
    gate_file = os.path.join(previous, 'resume_gate.json')
    if not os.path.exists(gate_file):
        return None, 'No saved simulation state; Live rebuilds from the shown shape'
    with open(gate_file, encoding='utf8') as source:
        old_gate = json.load(source)
    if gate != old_gate:
        return None, 'Inputs changed; Live rebuilds from the shown shape'
    with open(os.path.join(previous, 'job.json'), encoding='utf8') as source:
        info = json.load(source)
    if info.get('kind') != 'live' or len(info.get('stages', [])) != 1:
        return None, 'This result has no full Live checkpoint; Live rebuilds from the shown shape'
    checkpoint = os.path.join(previous, 's1', 'live_checkpoint.npz')
    if not os.path.exists(checkpoint):
        return None, 'No valid simulation checkpoint; Live rebuilds from the shown shape'
    log_path=os.path.join(previous,'s1','run_log.json')
    if os.path.exists(log_path):
        with open(log_path,encoding='utf8') as source:checkpoint_status=json.load(source).get('checkpoint',{})
        if checkpoint_status.get('status')=='unavailable':
            return None,'Full simulation state is unavailable; Live rebuilds from the shown shape'
    final = last_frame(previous)
    shown = np.asarray(shown, float)
    if final is None or shown.shape != final.shape or not np.isfinite(shown).all() or not np.isfinite(final).all() or np.max(np.linalg.norm(shown-final*1000.,axis=1)) > .001:
        return None, 'The garment was edited; Live rebuilds from the shown shape'
    # Confirm the visible last frame is actually the checkpoint's accepted shape.
    with np.load(checkpoint, allow_pickle=False) as archive:
        manifest = json.loads(archive['manifest'].tobytes())
        from .solver.live_checkpoint import source_fingerprint
        saved_source = {key: value for key, value in manifest.get('fingerprint', {}).items()
                        if key.endswith('.py') and not key.startswith('input:')}
        if saved_source != source_fingerprint():
            return None, 'Simulation code changed; Live rebuilds from the shown shape'
        if manifest.get('fingerprint',{}).get('start_validation_caller') != 'addon':
            raise ValueError('The saved state used a different simulation entry point; previous result kept')
        if manifest.get('fingerprint',{}).get('start_validation_override') != os.environ.get('AC9_START_CHECK_ALLOW'):
            raise ValueError('The saved simulation used different validation settings; previous result kept')
        def child(node, key):
            return next(value for name,value in node['items'] if name['value']==key)
        position = child(child(manifest['tree'], 's0'), 'particle_q')
        q = archive[position['key']]
        if not np.isfinite(q).all() or q.shape != final.shape or not np.array_equal(q, final.astype(q.dtype)):
            raise ValueError('Live checkpoint does not match the stopped garment; previous result kept')
    if os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.abspath(previous)):
        raise ValueError('Live resume needs a new job folder; previous result kept')
    os.makedirs(folder, exist_ok=True)
    for name in ('scene.idx.npz', 'avatar.npz', 'pattern.npz', 'ac9_spec.json'):
        shutil.copy2(os.path.join(previous,name),os.path.join(folder,name))
    stage = os.path.join(folder, 's1');os.makedirs(stage,exist_ok=True)
    with open(os.path.join(previous,'s1','config.json'),encoding='utf8')as source:cfg=json.load(source)
    for key,value in list(cfg.items()):
        if key not in ('pattern','params_file','grab_file','checkpoint_in','checkpoint_out') and isinstance(value,str) and os.path.isfile(value):
            target=os.path.join(folder,key+'_'+os.path.basename(value))
            shutil.copy2(value,target);cfg[key]=target
    cfg['pattern']=os.path.join(folder,'pattern.npz')
    cfg['checkpoint_in']=os.path.join(folder,'source_checkpoint.npz')
    cfg['checkpoint_out']=os.path.join(stage,'live_checkpoint.npz')
    cfg['params_file']=os.path.join(folder,'params.json');cfg['grab_file']=os.path.join(folder,'grab.json')
    shutil.copy2(checkpoint,cfg['checkpoint_in'])
    with open(os.path.join(stage,'config.json'),'w',encoding='utf8')as output:json.dump(cfg,output)
    info.update(dir=folder,kind='live',stages=['Live: resume saved simulation state'],prep_s=0.,
                state_resume={'status':'prepared','frame':manifest['frame'],'from':previous},
                report=['Live resumes the saved simulation state'])
    with open(os.path.join(folder,'job.json'),'w',encoding='utf8')as output:json.dump(info,output,ensure_ascii=False,indent=1)
    write_gate(folder,gate)
    d=np.load(os.path.join(folder,'scene.idx.npz'))
    return (info,(d['flat'],d['F'],d['fp'],shown.copy(),d['E_st'])),None
