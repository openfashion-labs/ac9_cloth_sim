# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only preparation from a private Blender snapshot. No solver or source scene writes."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

SCHEMA = 1
PREPARE_SCALARS = ('floor', 'live_gravity', 'local_gravity', 'hold_while_sewing',
                   'mesh_size', 'native_three', 'fast_live', 'accurate_research',
                   'preset', 'fabric_weight', 'fabric_bending', 'watch')


def _inside(path, root):
    p=Path(path).resolve();base=Path(root).resolve()
    if p!=base and base not in p.parents:raise ValueError('Preparation path escaped its owned directory')
    return p


def _remove(root, token):
    root=Path(root).resolve()
    try:
        marker=json.loads((root/'owner.json').read_text(encoding='utf-8'))
        if marker.get('token')!=token or marker.get('schema')!=SCHEMA:return
        if root.parent!=(Path(tempfile.gettempdir())/'ac9_ahead_prepare').resolve():return
        shutil.rmtree(root)
    except OSError:pass


class Task:
    def __init__(self, root, token, process, owner, generation, snapshot_s):
        self.root=Path(root);self.token=token;self.process=process
        self.owner=owner;self.generation=generation;self.snapshot_s=snapshot_s
        self.started=time.monotonic();self.aborted=False

    def poll(self):return self.process.poll()

    def matches(self,context):
        try:
            if not self.owner.matches(context,content=True) or self.anchor!=_anchor(context,self.garment):return False
            if hasattr(self,'job'):
                import bpy
                from . import dress as DR
                if bpy.data.filepath!=self.source_file or DR.job_of(self.garment)!=self.job:return False
            return True
        except (ReferenceError,AttributeError):return False

    def read(self):
        if self.poll()!=0:raise ValueError('Preparation process did not exit successfully')
        row=json.loads((self.root/'result.json').read_text(encoding='utf-8'))
        if row.get('schema')!=SCHEMA or row.get('token')!=self.token or row.get('generation')!=self.generation:raise ValueError('Unowned preparation reply')
        if not row.get('ok'):raise ValueError(row.get('error','Preparation failed'))
        for key in ('idx','avatar','cfg','stage'):
            path=_inside(row[key],self.root)
            if not (path.is_dir() if key=='stage' else path.is_file()):raise ValueError('Missing preparation output: '+key)
        if not isinstance(row.get('key'),str) or not row['key']:raise ValueError('Preparation has no structure key')
        def duration(value):
            return type(value) in (float,int) and math.isfinite(value) and value>=0
        for key in ('prepare_s','key_s','worker_s'):
            if not duration(row.get(key)):raise ValueError('Invalid preparation timing: '+key)
        if not isinstance(row.get('phases'),list) or any(not isinstance(p,dict) or not isinstance(p.get('name'),str)
                or not duration(p.get('seconds')) for p in row['phases']):raise ValueError('Invalid preparation phases')
        if not isinstance(row.get('solver_env'),dict) or any(not isinstance(k,str) or not isinstance(v,str)
                for k,v in row['solver_env'].items()):raise ValueError('Invalid preparation environment')
        return row

    def abort(self):
        if self.aborted:return
        self.aborted=True
        _retire(self.root,self.token,self.process)


def _retire(root,token,process):
        if process.poll() is None:
            try:process.terminate()
            except OSError:pass
        def retire():
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:process.kill();process.wait(timeout=5)
                except (OSError,subprocess.TimeoutExpired):return
            _remove(root,token)
        threading.Thread(target=retire,name='AC9 prepare cleanup',daemon=True).start()


def _anchor(context,g):
    p=context.scene.ac9_cloth_sim
    body=p.body
    return (context.scene.frame_current,context.view_layer.name,body.session_uid if body else None,
            g.session_uid,g.get('ac9_job'),g.get('ac9_job_generation'),
            g.get('ac9_reset_pieces'),g.get('ac9_from_frame'),
            tuple((ob.session_uid,ob.get('ac9_collider'),ob.get('ac9_job_generation'))
                  for ob in getattr(context.scene,'objects',())),
            tuple(getattr(p,name,None) for name in PREPARE_SCALARS))


def start(context, generation):
    import bpy
    from . import store,dress as DR
    marks={};tick=time.perf_counter()
    def mark(name):
        nonlocal tick
        now=time.perf_counter();marks[name]=now-tick;tick=now
    owner=store.edit_owner(context);sp,_=store.get(context)
    garment=DR.garment(sp);job=DR.job_of(garment) if garment else None
    if garment is None:raise ValueError('No result to prepare from')
    if not job:raise ValueError('No available result to prepare from')
    mark('source')
    base=Path(tempfile.gettempdir())/'ac9_ahead_prepare';base.mkdir(exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix=str(os.getpid())+'_',dir=base));token=uuid.uuid4().hex
    (root/'owner.json').write_text(json.dumps(dict(schema=SCHEMA,token=token)),encoding='utf-8')
    mark('root')
    t=time.perf_counter()
    process=None
    try:
        # Libraries.write does not run save handlers or change the open file.
        ids=set(bpy.data.scenes)|set(bpy.data.objects)|set(bpy.data.collections)|set(bpy.data.texts)
        bpy.data.libraries.write(str(root/'scene.blend'),ids,path_remap='ABSOLUTE',compress=False)
        mark('snapshot')
        owner.require(context)
        anchor=_anchor(context,garment)
        source_file=bpy.data.filepath
        dependencies={x.session_uid for x in ids}
        dependencies.update(ob.data.session_uid for ob in bpy.data.objects if ob.data is not None)
        snapshot_s=time.perf_counter()-t
        meta=dict(schema=SCHEMA,token=token,root=str(root),source=str(Path(__file__).parent),
                  scene=context.scene.name,view_layer=context.view_layer.name,
                  spec_sha=hashlib.sha256(owner.body.encode()).hexdigest(),job=job,generation=generation,
                  mesh=float(context.scene.ac9_cloth_sim.mesh_size),
                  native_three=context.scene.ac9_cloth_sim.native_three,
                  fast_live=context.scene.ac9_cloth_sim.fast_live)
        (root/'request.json').write_text(json.dumps(meta),encoding='utf-8')
        for name in ('config','extensions','tmp','cache'):(root/name).mkdir()
        env=dict(os.environ,BLENDER_USER_CONFIG=str(root/'config'),BLENDER_USER_EXTENSIONS=str(root/'extensions'),
                 TEMP=str(root/'tmp'),TMP=str(root/'tmp'),AC9_LIFECYCLE_DIR=str(root/'trace'),
                 PYTHONIOENCODING='utf-8',CUDA_VISIBLE_DEVICES='')
        mark('request')
        # Keep the normal system startup safeguards; no user extensions are loaded.
        with (root/'worker.log').open('wb') as log:
            process=subprocess.Popen([bpy.app.binary_path,'-b','--factory-startup','--disable-autoexec',
                '--python-exit-code','1','--python',str(Path(__file__).resolve()),'--',str(root/'request.json')],
                cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        mark('spawn')
        task=Task(root,token,process,owner,generation,snapshot_s)
        task.capture_phases=marks
        task.spec_sha=meta['spec_sha'];task.garment=garment;task.anchor=anchor
        task.job=job;task.source_file=source_file;task.dependencies=dependencies
        return task
    except Exception:
        if process is None:_remove(root,token)
        else:_retire(root,token,process)
        raise


def worker(request):
    import bpy,importlib.util,sys,traceback
    sys.dont_write_bytecode=True
    meta=json.loads(Path(request).read_text(encoding='utf-8'));root=Path(meta['root']).resolve()
    marker=json.loads((root/'owner.json').read_text(encoding='utf-8'))
    if marker!={'schema':SCHEMA,'token':meta['token']}:raise ValueError('Preparation request is not owned')
    row=dict(schema=SCHEMA,token=meta['token'],ok=False,generation=meta['generation'],phases=[])
    t=time.perf_counter()
    try:
        # No preparation output may escape into the user's job or cache folders.
        def audit(event,args):
            if event=='open' and isinstance(args[0],(str,bytes)):
                mode,flags=args[1:3]
                writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
                if writing:_inside(os.fsdecode(args[0]),root)
            elif event in ('os.mkdir','os.remove','os.rmdir'):_inside(args[0],root)
            elif event in ('os.rename','os.replace'):_inside(args[0],root);_inside(args[1],root)
        sys.addaudithook(audit)
        source=Path(meta['source'])
        spec=importlib.util.spec_from_file_location('ac9_cloth_sim',source/'__init__.py',submodule_search_locations=[str(source)])
        addon=importlib.util.module_from_spec(spec);sys.modules['ac9_cloth_sim']=addon;spec.loader.exec_module(addon)
        from ac9_cloth_sim import properties
        properties.register();properties._drop_handler()
        from ac9_cloth_sim import store,dress as DR,build_ahead as BA,prepare_build as PB,job_archive as JA
        from ac9_cloth_sim.core import pattern_build
        bpy.ops.wm.open_mainfile(filepath=str(root/'scene.blend'),load_ui=False)
        scene=bpy.data.scenes.get(meta['scene'])
        if scene is None:raise ValueError('Snapshot scene is missing')
        bpy.context.window.scene=scene
        layer=scene.view_layers.get(meta['view_layer'])
        if layer is None:raise ValueError('Snapshot view layer is missing')
        bpy.context.window.view_layer=layer
        props=scene.ac9_cloth_sim;props.fast_live=meta['fast_live'];props.native_three=meta['native_three']
        if hashlib.sha256(store.text(bpy.context).as_string().encode()).hexdigest()!=meta['spec_sha']:
            raise ValueError('Snapshot pattern does not match its owner')
        JA.working_root=lambda ctx:root/'work'
        DR.mesh_cache_dir=lambda ctx:str(root/'cache');pattern_build.CACHE_DIR=str(root/'cache')
        BA._QUIET['on']=True
        resolve=DR.PP.resolve
        def ui_inputs(*a,**kw):kw['environ']={};return resolve(*a,**kw)
        DR.PP.resolve=ui_inputs
        def measured(obj,name):
            fn=getattr(obj,name)
            def call(*a,**kw):
                tick=time.perf_counter()
                try:return fn(*a,**kw)
                finally:row['phases'].append(dict(name=name,seconds=round(time.perf_counter()-tick,6)))
            setattr(obj,name,call)
        for name in ('_build_cached','triangulate','initial_shape','_finish_start_shapes','shown_shape'):
            if hasattr(DR,name):measured(DR,name)
        sp,_=store.get(bpy.context);g=DR.garment(sp)
        if g is None:raise ValueError('Snapshot garment is missing')
        job=meta['job']
        if not job or not DR.job_alive(job):raise ValueError('Snapshot has no available result')
        owner=DR.Preparation(bpy.context)
        def allocate(*,allocator=None):
            owned=PB.OwnedJobDirectory.create(root/'output','prepared')
            owner.build.directories.append(owned);return owned
        owner.build.job_dir=allocate
        prepare_t=time.perf_counter()
        info,_=owner.prepare(meta['mesh'],xinit_from=None,continue_job=job,
            continue_shown=DR.shown_shape(bpy.context,g),live=True,redo=DR.reset_pieces(g),
            redo_shown=DR.frozen(g),for_build=True)
        row['prepare_s']=time.perf_counter()-prepare_t
        if len(info.get('stages',()))!=1:raise ValueError('Preparation has multiple stages')
        d=Path(info['dir']);cfg=json.loads((d/'s1/config.json').read_text(encoding='utf-8'))
        for k in BA.RUNTIME_KEYS:cfg.pop(k,None)
        stage=d/'build';stage.mkdir();cfg_path=stage/'config.json';cfg_path.write_text(json.dumps(cfg),encoding='utf-8')
        from ac9_cloth_sim import result_reference as RR
        idx,avatar=str(d/RR.INDEX),str(d/RR.AVATAR)
        key_t=time.perf_counter();key=BA._key(idx,avatar,cfg)
        if key is None:raise ValueError('Preparation structure key is missing')
        row.update(ok=True,idx=idx,avatar=avatar,cfg=str(cfg_path),stage=str(stage),key=key,
                   solver_env=info.get('solver_env',{}),key_s=time.perf_counter()-key_t)
    except Exception:row['error']=traceback.format_exc()
    row['worker_s']=time.perf_counter()-t
    temp=root/'result.next.json';temp.write_text(json.dumps(row),encoding='utf-8');temp.replace(root/'result.json')


if __name__=='__main__':
    import sys
    worker(sys.argv[-1])
