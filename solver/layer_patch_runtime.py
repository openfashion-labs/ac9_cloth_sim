"""Coupled layer recovery inside the first native substep, before velocity update."""
import json,time
from collections import deque, Counter
from pathlib import Path
import numpy as np
import warp as wp


class Bundle:
    def __init__(self,prefix,suffix,runtime):self.prefix=prefix;self.suffix=suffix;self.runtime=runtime


class GraphRecovery:
    """Capture native graphs directly; no source rewriting or experiment imports."""
    def __init__(self,context,out):
        self.context=context;self.out=out;self.enabled=True;reason=None
        s=context['solver'];m=context['model']
        if getattr(s,'ac9_bound_constraints',None)is not None:reason='dependent constraints use the existing layer path'
        elif m.body_count:reason='moving rigid bodies use the existing layer path'
        elif not context['cfg'].get('graph',True)or not wp.get_device().is_cuda:reason='coupled recovery requires CUDA graphs'
        elif m.shape_count:
            transforms=m.shape_transform.numpy()
            if np.any(np.abs(transforms-np.array([0,0,0,0,0,0,1]))>1.e-10):reason='transformed body shapes use the existing layer path'
        self.enabled=reason is None
        context['log']['layer_coupled']={'enabled':self.enabled,'reason':reason,'every_frames':2,'goal_mm':.001,'trust_mm':.02}

    def capture(self):
        return SplitScope(self.context,self.out,'coupled')if self.enabled else wp.ScopedCapture()

    def launch(self,graph):
        if isinstance(graph,Bundle):
            wp.capture_launch(graph.prefix);graph.runtime.apply();wp.capture_launch(graph.suffix)
        else:wp.capture_launch(graph)


class SplitScope:
    def __init__(self,context,out,mode):
        self.context=context;self.out=Path(out);self.mode=mode;self.first=wp.ScopedCapture();self.second=None;self.original=wp.launch;self.calls={};self.finished=False;self.graph=None
        if getattr(context['solver'],'ac9_bound_constraints',None)is not None:raise ValueError('BIND/one_layer mapping not implemented in coupled experiment')
        if context['model'].body_count:raise ValueError('Moving rigid bodies unsupported in coupled experiment')

    def __enter__(self):
        self.first.__enter__();wp.launch=self.launch;return self

    def launch(self,*args,**kwargs):
        kernel=kwargs.get('kernel',args[0]if args else None);name=getattr(kernel,'key','').split('.')[-1]
        if not self.finished:
            if name in ['accumulate_particle_body_contact_force_and_hessian','accumulate_spring_force_and_hessian','accumulate_self_contact_force_and_hessian','solve_elasticity','solve_elasticity_tile']:
                self.calls['elastic'if name.startswith('solve_elasticity')else name]=dict(kwargs)
            if name=='update_velocity':
                self.first.__exit__(None,None,None);self.second=wp.ScopedCapture();self.second.__enter__();self.finished=True;self.velocity_inputs=list(kwargs['inputs'])
        return self.original(*args,**kwargs)

    def __exit__(self,*exc):
        wp.launch=self.original
        if self.second is None:
            self.first.__exit__(*exc);raise RuntimeError('Native velocity-update split was not found')
        self.second.__exit__(*exc)
        self.graph=Bundle(self.first.graph,self.second.graph,Runtime(self.context,self.out,self.mode,self.calls,self.velocity_inputs))


class Runtime:
    _prepared=False
    def __init__(self,context,out,mode,calls,velocity_inputs):
        self.context=context;self.out=out;self.mode=mode;self.calls=calls;self.velocity_inputs=velocity_inputs;self.frame=0;self.rows=deque(maxlen=32);self.counts=Counter()
        self.goal_mm=.001
        if not np.isfinite(self.goal_mm)or not 0.<self.goal_mm<=.02:raise ValueError('Invalid experimental patch goal')
        self.m=context['model'];self.s=context['solver'];self.p=self.s._ac9_layer_arrays()[3];self.x_in=calls['elastic']['inputs'][3];self.x_out=velocity_inputs[2]
        self.frozen=self.m.particle_inv_mass.numpy()==0;self.F=self.m.tri_indices.numpy();self.g={'wp':wp,'s':self.s,'m':self.m,'p':self.p,'state':type('StateView',(),{'particle_q':self.x_in})(),'K':context['_km'],'LC':context['_LC'],'dt':float(velocity_inputs[0]),'captured':self.calls,'F':self.F}
        if mode!='split_only':
            from layer_patch_forces import evaluate
            from layer_patch_contacts import collect,validate_body_contacts
            from layer_patch import Contact,solve_shadow
            from layer_patch_active import LocalWarmStart,solve_dense_qp
            from layer_patch_geometry import BatchGeometry,triangle_projection
            self.evaluate=evaluate;self.collect=collect;self.validate_body=validate_body_contacts;self.Contact=Contact;self.solve_shadow=solve_shadow;self.Warm=LocalWarmStart;self.Geometry=BatchGeometry;self.project=triangle_projection
            if not Runtime._prepared:
                solve_dense_qp(np.eye(1),np.zeros(1),np.ones((1,1)),np.array([.001]),.02)
                wp.load_module(module='layer_patch_forces',device=self.s.device)
                wp.load_module(module='layer_patch_contacts',device=self.s.device)
                Runtime._prepared=True

    def apply(self):
        self.frame+=1
        if self.mode=='split_only':return
        if self.context.get('FREE')is not None and self.context['FREE']['launch']:
            self.record({'frame':self.frame,'status':'active_free_contact_update_refused'});return
        if self.frame%2:return
        started=time.perf_counter()
        self.frozen=self.m.particle_inv_mass.numpy()==0
        x=self.x_in.numpy().astype(float);self.g['x']=x
        try:self.validate_body(self.g)
        except ValueError as exc:
            self.record({'frame':self.frame,'status':'body_contacts_incomplete','reason':str(exc),'seconds':time.perf_counter()-started});return
        previous_flags=self.p.cross_flags.numpy()
        cross=self.s.ac9_layer_crossings
        wp.launch(self.g['LC'].update_flags,dim=cross['active'].shape[0],inputs=[self.x_in,self.m.tri_indices,self.p,cross['active'],cross['count']])
        edges=self.p.cross_vertices.numpy();targets=self.p.cross_targets.numpy();flags=self.p.cross_flags.numpy();ts=self.p.cross_t.numpy().astype(float);signs=self.p.cross_sign.numpy().astype(float);active=np.flatnonzero(flags)
        if not len(active):
            self.record({'frame':self.frame,'status':'no_witness','seconds':time.perf_counter()-started});return
        support=x[self.F[targets[active]]];point=(1.-ts[active,None])*x[edges[active,0]]+ts[active,None]*x[edges[active,1]];cp,bary=self.project(point,support);normal=np.cross(support[:,1]-support[:,0],support[:,2]-support[:,0]);normal=normal/np.maximum(np.linalg.norm(normal,axis=1,keepdims=True),1.e-30)*signs[active,None];gap=np.einsum('ij,ij->i',point-cp,normal)
        choice=int(np.argmin(gap));edge=int(active[choice]);root=self.Contact('witness',tuple(map(int,[*edges[edge],*self.F[targets[edge]]])),np.zeros(3),t=float(ts[edge]),sign=float(signs[edge]));seed=np.array(root.nodes);movable=np.unique(self.F[np.isin(self.F,seed).any(1)]);movable=movable[~self.frozen[movable]]
        if not len(movable):
            self.record({'frame':self.frame,'edge':edge,'status':'frozen_patch','seconds':time.perf_counter()-started});return
        selection_end=time.perf_counter()
        parts=self.evaluate(self.g,movable,include_external=False)
        if not self.p.cross_flags.numpy()[edge]:
            self.record({'frame':self.frame,'edge':edge,'status':'primary_retired_before_collect','seconds':time.perf_counter()-started});return
        force_end=time.perf_counter()
        records,primary,metadata=self.collect(self.g,movable,edge)
        collect_end=time.perf_counter()
        result=self.solve_shadow(x,movable,parts['force'].numpy(),parts['hessian'].numpy(),records,primary,goal_mm=self.goal_mm,trust_mm=.02,batch=True,qp_backend=self.Warm())
        solve_end=time.perf_counter()
        log={'frame':self.frame,'edge':edge,'status':result['reason'],'movable':len(movable),'constraints':len(records),'retired_before_selection':int(np.sum((previous_flags!=0)&(flags==0))),'stage_seconds':{'selection':selection_end-started,'force_launch':force_end-selection_end,'collect_and_sync':collect_end-force_end,'solve_and_transfer':solve_end-collect_end}}
        if result['accepted']:
            proposal=result['positions'];old_d=wp.clone(self.s.particle_displacements);old_ts=wp.clone(self.s.truncation_ts)
            self.s.particle_displacements.assign((proposal-self.s.pos_prev_collision_detection.numpy()).astype(np.float32));accepted=wp.clone(self.x_in);self.s._penetration_free_truncation(accepted);y=accepted.numpy().astype(float)
            outside=np.ones(len(x),bool);outside[movable]=False
            raw_outside_delta=float(np.max(np.abs(y[outside]-x[outside])))if outside.any()else 0.
            # Commit only this block's DOFs. Native global reconstruction can round
            # unrelated accepted positions; preserve their exact existing state.
            y[outside]=x[outside];accepted.assign(y.astype(np.float32))
            kept_d=self.s.particle_displacements.numpy();kept_ts=self.s.truncation_ts.numpy();kept_d[outside]=old_d.numpy()[outside];kept_ts[outside]=old_ts.numpy()[outside]
            self.s.particle_displacements.assign(kept_d);self.s.truncation_ts.assign(kept_ts)
            geometry=self.Geometry(x,movable,records);before=geometry.at_positions(x,with_jac=False)[1];after=geometry.at_positions(y,with_jac=False)[1];exact=geometry.at_positions(proposal,with_jac=False)[1];rounded=geometry.at_positions(proposal.astype(np.float32).astype(float),with_jac=False)[1];rounding=np.abs(rounded-exact)
            positive=np.array([c.kind!='witness'for c in records])&(before>=0);new_negative=int(np.sum(positive&(after<0)));excess=float(np.max(before-after-rounding));gain=float(after[primary]-before[primary]);fixed_delta=float(np.max(np.abs(y[self.frozen]-x[self.frozen])))if self.frozen.any()else 0.
            outside_delta=float(np.max(np.abs(y[outside]-x[outside])))if outside.any()else 0.
            allowed=bool(new_negative==0 and excess<=1.e-9 and gain>1.e-5 and fixed_delta==0. and outside_delta==0.)
            log.update({'primary_gain_mm':gain,'new_negative_contacts':new_negative,'max_gap_excess_beyond_direct_rounding_mm':excess,'outside_patch_displacement_mm':outside_delta*1000.,'raw_native_outside_rounding_mm':raw_outside_delta*1000.,'applied':allowed})
            if allowed:
                self.x_in.assign(accepted);self.x_out.assign(accepted);log['status']='applied_before_velocity_update'
            else:
                self.s.particle_displacements.assign(old_d);self.s.truncation_ts.assign(old_ts);log['status']='native_or_geometry_rejected'
        log['stage_seconds']['postcheck']=time.perf_counter()-solve_end
        log['seconds']=time.perf_counter()-started;self.record(log)

    def record(self,row):
        self.rows.append(row);self.counts[row['status']]+=1
        self.flush()

    def flush(self):
        started=time.perf_counter()
        (self.out/'coupled_runtime.json').write_text(json.dumps({'scope':'Coupled layer block before first-substep velocity update every second frame. Counts cover this graph runtime; detail is bounded to the last 32 records. Active free-contact updates are skipped.','counts':dict(self.counts),'rows':list(self.rows)},indent=2),encoding='utf8')
        metrics=self.context.get('log',{}).get('layer_coupled')
        if metrics is not None:
            seconds=time.perf_counter()-started
            metrics['diagnostic_io_seconds']=metrics.get('diagnostic_io_seconds',0.)+seconds
            metrics['diagnostic_io_max_seconds']=max(metrics.get('diagnostic_io_max_seconds',0.),seconds)

