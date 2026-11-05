# SPDX-License-Identifier: GPL-3.0-or-later
"""Static-body BW correction. Native owns velocities, friction anchors and contact history.

Ported from the accepted 2026-11-02 friction trial. Diagnostic probes and CPU
alternatives are excluded. A failed substep is fatal to this run, never resumed.
"""
import sys, json, time
from pathlib import Path
import numpy as np
import traceback
from .contracts import validate_arrays,validate_state,validate_mode
from .pants_contracts import validate_dynamic_state, masked_bending
from .spring_cpu_terms import validate_springs
from .native_stitched_terms import spring_force
from .fresh_contacts_friction import FreshFrictionContacts as FreshContacts
from .friction_lagged import LaggedBodyFriction
from .diagnostics import Diagnostics
from . import audit as fast_audit
import os as _os
_TIMING=bool(_os.environ.get('AC9_TEST_STAGE_TIMING'))   # test only: synchronized section times in each correction row
_marks=[]
def _mark(name):
    if not _TIMING:return
    import warp as wp
    wp.synchronize();_marks.append((name,time.perf_counter()))
def _take_marks():
    rows={};prev=None
    for name,t in _marks:
        if prev is not None:rows[name]=rows.get(name,0.)+(t-prev)*1000
        prev=t
    _marks.clear();return rows


def clip_to_body(q, change, parameters, radius, margin):
    """Stop the step's normal component at the body surface for contact candidates that are not penetrating at q
    (Accurate, RESULT §6 of experiments/claude_s3_speed_budget_2026-11-03): their penalty has no Hessian stiffness,
    so the full step drove about 69 of them into the body and the line search halved. In place; returns the count."""
    ids = parameters['indices'][:, 0]
    if not len(ids): return 0
    normal = parameters['normal'].astype(float); reach = radius[ids].astype(float) + margin
    d0 = np.sum(normal * (q[ids] - parameters['point']), axis=1)
    d1 = d0 + np.sum(normal * change[ids], axis=1)
    hit = (d0 >= reach) & (d1 < reach)
    change[ids[hit]] += ((reach[hit] - d1[hit])[:, None]) * normal[hit]
    return int(hit.sum())


def install(g, out):
    import warp as wp
    out=Path(out);m,s=g['model'],g['solver'];cfg=g['cfg']
    diagnostics=Diagnostics(out)
    mode='full'
    if mode not in ('observe','full'):raise ValueError('Unknown S3 mode')
    if m.body_count or m.tet_count:
        raise ValueError('S3 supports static vertex-body cloth with validated spring coupling only')
    if (hasattr(s,'ac9_layer_crossings') or getattr(s,'ac9_bound_constraints',None) is not None) and not (_os.environ.get('AC9_TEST_FAST_LAYERS', '1') != '0'):
        raise ValueError('S3 does not support Layer or dependent points')
    # test (AC9_TEST_FAST_LAYERS): Fold Layers' dependent points ride their support triangle; the correction leaves them
    # out of the solve and places them on the trial positions
    bound=getattr(s,'ac9_bound_constraints',None)
    followers=None if bound is None else bound.vertices.numpy()
    bound_q=None if bound is None else wp.zeros(m.particle_count,dtype=wp.vec3,device=s.device)
    def place_followers(c):
        if bound is None:return c
        bound_q.assign(np.asarray(c,np.float32));bound.place(bound_q);return bound_q.numpy()
    # the layer crossing force, lagged at the correction's start like the cloth contact
    lc=None
    if hasattr(s,'ac9_layer_crossings') and not _os.environ.get('AC9_TEST_LC_OFF'):
        from .self_contact_lagged import LaggedLayerCrossing
        lc=LaggedLayerCrossing(m,s)
    def lc_energy(c):return 0. if lc is None else lc.energy(c)
    if cfg.get('graph'):
        raise ValueError('S3 friction needs graph=false')
    if cfg.get('fric_anchor') and (getattr(s,'ac9_fric_anchor',None) is None or s.ac9_fric_anchor.shape[0]==0):
        raise ValueError('fric_anchor requested but Native has no anchor arrays (membrane not bw)')
    # bend_fric2 (the fabric's bending hysteresis) in its default 'after' form rewrites each hinge's stiffness and rest
    # angle after every substep; within a substep the hinge is a plain spring, which the correction already reads
    # (bending properties and rest angles are taken per correction, sync_model). The 'inside' form (a capped moment
    # inside the bending force) is not carried.
    bf2=cfg.get('bend_fric2')
    if isinstance(bf2,dict) and bf2.get('solve','after')!='after':
        raise ValueError("Bend hysteresis solved 'inside' is not supported by Fast Live")
    if s.ac9_bend_fric.shape[0] and np.any(s.ac9_bend_fric.numpy()[:,1]>0):
        raise ValueError('Evolving bend friction history is not supported by this prototype')
    k=sys.modules['newton._src.solvers.vbd.particle_vbd_kernels']
    validate_mode(cfg,k)
    if float(k.SIMSPIKE_BUCKLE_RATIO)>0:raise ValueError('Buckling unsupported')
    floor=0. # unused: open mesh uses Native local-side semantics
    model={n:v for n,v in [('mass',m.particle_mass.numpy().astype(float)),('tri',m.tri_indices.numpy()),('poses',m.tri_poses.numpy().astype(float)),('materials',m.tri_materials.numpy().astype(float)),('areas',m.tri_areas.numpy().astype(float))]}
    validate_arrays(model,m.particle_inv_mass.numpy(),m.particle_flags.numpy(),k.ParticleFlags.ACTIVE)
    def spring_array(name):
        if m.spring_count:return getattr(m,name).numpy()
        return np.empty(0,dtype=np.int32 if name=='spring_indices' else np.float32)
    model['spring_indices']=spring_array('spring_indices').reshape(-1,2)
    validate_springs(m.particle_count,model['spring_indices'],spring_array('spring_rest_length'),spring_array('spring_stiffness'),spring_array('spring_damping'))
    (out/'spring_model.json').write_text(json.dumps({'count':int(m.spring_count),'indices':model['spring_indices'].tolist(),'initial_rest_m':spring_array('spring_rest_length').tolist(),'initial_stiffness':spring_array('spring_stiffness').tolist(),'initial_damping':spring_array('spring_damping').tolist()},indent=2),encoding='utf8')
    active=model['mass']>0
    colors=wp.array(np.full(m.particle_count,-1,dtype=np.int32),dtype=wp.int32,device=s.device)
    force=wp.zeros(m.particle_count,dtype=wp.vec3,device=s.device)
    hess=wp.zeros(m.particle_count,dtype=wp.mat33,device=s.device)
    rr=wp.zeros_like(force);hh=wp.zeros_like(hess);cf=wp.zeros_like(force);ch=wp.zeros_like(hess)
    emptyv=wp.zeros(0,dtype=wp.vec3,device=s.device);emptyi=wp.zeros(0,dtype=int,device=s.device)
    fresh=FreshContacts(g,k,colors,emptyv,emptyi)
    cf=fresh.force;ch=fresh.H
    # Native until sewn (2026-11-02, DECISIONS cloth-sim-s3-continue-on-claude-2026-11-02, alpha): from the placed pieces
    # the substeps stay Native's alone (no guard, no correction) until every stitch is shorter than the cloth's thickness
    # (2 particle radii); from then on, for the rest of the run, the correction as before. A dressed garment starts sewn,
    # so its Live runs as before from the first substep. Solving the sewing to equilibrium pressed the cloth through the
    # body's contact shell (experiments/claude_s3_placed_stop_2026-11-02/RESULT.md, docs/reviews/
    # REVIEW_fable_S3_placed_assembly_2026-11-02.md). Decided once per substep, at its start (begin_substep, before the
    # grab kernel and the collision query), and never undone.
    # Only the pattern stitches (the first N_ST springs) count; temp_stitches, anchor_ke and seam_paths targets follow.
    from .sewn import sewn as _sewn
    sewn_m=2.*float(m.particle_radius.numpy().max());n_stitch=int(g['n_stitch'])
    phase={'fast':not n_stitch,'native_only_substeps':0}
    diagnostics.summary.update(native_until_sewn_mm=sewn_m*1000,native_only_substeps=0,fast_from_frame=None)
    def sewn(q):return _sewn(q,model['spring_indices'],spring_array('spring_stiffness'),n_stitch,sewn_m)
    # Live before sewn (newton_vbd PRESEWN, 2026-11-04): until sewn, Native's own self-contact step cap and detection
    # too (NativeStepCap below widens them for the corrected substeps; widened from the placed pieces, the shirt moved
    # up to 6 mm away from the product Live in the first frame). AC9_TEST_PRESEWN_LIVE=0 = the old run.
    presewn=_os.environ.get('AC9_TEST_PRESEWN_LIVE','1')!='0'
    def begin_substep(state):
        if not phase['fast'] and sewn(state.particle_q.numpy()):
            phase['fast']=True;diagnostics.summary['fast_from_frame']=int(g['f'])
        if presewn:s.ac9_step_cap_active=phase['fast']
        return phase['fast']
    g.begin_substep=begin_substep
    # the grab spring in corrected substeps (S3 grab, design OK 11/02): newton_vbd skips its explicit kernel there and
    # the correction carries 1/2 kp |q - (target + offset)|^2 (force, kp I, and the same energy in the acceptance)
    grab=g.values.get('grab')
    def grab_term():
        if grab is None or 'kp' not in grab:return None
        on=grab['mask'].numpy()[:m.particle_count]!=0
        if not on.any():return None
        ids=np.nonzero(on)[0]
        return ids,grab['step'].numpy()[0].astype(float)+grab['off'].numpy()[ids].astype(float),grab['kp'][ids].astype(float)
    # Attach (E5, test switch AC9_TEST_FAST_E5): newton_vbd skips its explicit pull in corrected substeps and the
    # correction carries it implicitly: min(k d, cap) toward the closest tape point, k the 10-substep strength
    attach=g.values.get('attach')
    def attach_term(q):
        if attach is None:return None
        v=attach['v'];x=np.asarray(q,float)[v];bp=x.copy();best=np.full(len(v),1e30)
        for grp in np.unique(attach['g']):
            on=attach['g']==grp;a=int(attach['s0'][grp]);n=int(attach['s1'][grp])-a;closed=int(attach['cl'][grp])==1
            P=attach['pts'][a:a+n];nseg=n if closed else n-1
            for k_ in range(nseg):
                p0=P[k_];e=P[(k_+1)%n]-p0;t=(x[on]-p0)@e/max(float(e@e),1e-12)
                t=np.clip(t,-1e6 if(not closed and k_==0)else 0.,1e6 if(not closed and k_==nseg-1)else 1.);p=p0+t[:,None]*e
                dd=np.linalg.norm(x[on]-p,axis=1);idx=np.nonzero(on)[0];better=dd<best[idx];best[idx[better]]=dd[better];bp[idx[better]]=p[better]
        k_=attach['kp10'];cap=attach['cap'];d=np.maximum(best,1e-12);lin=k_*d<=cap
        f=np.where(lin[:,None],k_[:,None]*(bp-x),(cap/d)[:,None]*(bp-x));h=np.where(lin,k_,cap/d)
        e=np.where(lin,.5*k_*d*d,cap*d-cap*cap/(2*k_));return v,f,h,float(e.sum())
    def attach_energy(q):
        A=attach_term(q);return 0. if A is None else A[3]
    if True:
        pipe=g['pipe'];native_contacts=g['contacts'];base_gap=float(pipe.soft_contact_gap);raw_collide=pipe.collide
        def pack(c):
            n=int(c.soft_contact_count.numpy()[0]);idx=c.soft_contact_indices.numpy()[:n,0];order=np.argsort(idx)
            return idx[order],c.soft_contact_body_pos.numpy()[:n][order],c.soft_contact_normal.numpy()[:n][order]
        def velocity_query(state,contacts=None,**kwargs):
            if contacts is not native_contacts:return raw_collide(state,contacts,**kwargs)
            if not phase['fast']:return raw_collide(state,contacts,**kwargs)
            dt_query=float(g['dt']);q=state.particle_q.numpy();vel=state.particle_qd.numpy()
            if not np.isfinite(vel).all():raise ValueError('Nonfinite velocity in query reach')
            reach=float(np.linalg.norm(vel,axis=1).max())*dt_query+.5*float(np.linalg.norm(m.gravity.numpy(),axis=-1).max())*dt_query*dt_query
            saved=float(pipe.soft_contact_gap)
            try:
                pipe.soft_contact_gap=base_gap;raw_collide(state,contacts,**kwargs);ids0,point0,normal0=pack(contacts)
                pipe.soft_contact_gap=base_gap+reach;answer=raw_collide(state,contacts,**kwargs);ids1,point1,normal1=pack(contacts)
            finally:pipe.soft_contact_gap=saved
            common,old,new=np.intersect1d(ids0,ids1,return_indices=True)
            if len(common)!=len(ids0):raise RuntimeError('Query reach lost existing contact candidates')
            if not np.array_equal(point0[old],point1[new])or not np.array_equal(normal0[old],normal1[new]):raise RuntimeError('Query reach changed common geometry')
            extra=~np.isin(ids1,ids0);distance=np.sum(normal1[extra]*(q[ids1[extra]]-point1[extra]),axis=1)
            penetration=m.particle_radius.numpy()[ids1[extra]]+float(m.shape_margin.numpy()[0])-distance
            if len(penetration)and np.max(penetration)>1e-7:raise RuntimeError('Expanded query introduced an active contact at its initial geometry')
            diagnostics.record_query({'frame':int(g['f']),'substep_ordinal':diagnostics.summary['queries']+1,'base_gap_mm':base_gap*1000,'reach_mm':reach*1000,'expanded_gap_mm':(base_gap+reach)*1000,'base_contacts':len(ids0),'expanded_contacts':len(ids1),'extra_candidates':int(extra.sum()),'extra_max_penetration_mm':float(penetration.max()*1000)if len(penetration)else None,'common_geometry_exact':True,'native_warmstart_changed':False})
            return answer
        pipe.collide=velocity_query
    (out/'body_scope.json').write_text(json.dumps(fresh.mesh_info,indent=2),encoding='utf8')
    backend='gpu'
    from .gpu_system_fixed import FixedStitchedGpuSystem   # reference: gpu_system_substep_budget.StitchedGpuSystem
    gpu_system=FixedStitchedGpuSystem(model,{'edge':m.edge_indices.numpy(),'length':m.edge_rest_length.numpy(),'bending':m.edge_bending_properties.numpy(),'bf':s.ac9_bend_fric.numpy()},s.device,maxiter=3000,tol=1e-3)
    original=s._solve_particle_iteration;captured=False;step_energy=None
    # the fabric's bending hysteresis: its friction element with the moment capped (bend_hysteresis.py)
    hyst=None
    if g.values.get('bf2') is not None and not g['bf2']['inside'] and not _os.environ.get('AC9_TEST_HYST_OFF'):   # test switch: hysteresis left out
        from .bend_hysteresis import CappedBendHysteresis
        hyst=CappedBendHysteresis(g['bf2'],m.edge_indices.numpy(),s.device)
    def hyst_energy(q):return 0. if hyst is None else hyst.energy_at(q)
    # cloth-cloth contact: Native's contacts lagged at the correction's start, and Native's step limit (self_contact_lagged.py)
    sc=None
    if s.particle_enable_self_contact and not _os.environ.get('AC9_TEST_SC_OFF'):   # test switch: contact left out of the correction
        from .self_contact_lagged import LaggedSelfContact
        sc=LaggedSelfContact(m,s)
        if _os.environ.get('AC9_TEST_SC_CAP','1')!='0' and bound is None:   # with dependent points: Native's own bound-aware truncation in the iterations, the isotropic bound in the correction   # test switch: Native's step cap per point (self_contact_lagged.NativeStepCap)
            from .self_contact_lagged import NativeStepCap
            native_cap=NativeStepCap(m,s,1./float(cfg.get('fps',60))/int(cfg.get('substeps',2)),g['contacts'])
            if not _os.environ.get('AC9_TEST_SC_CORR_OLD'):sc.cap=native_cap   # the correction's step: planar truncation + per-point cap
    accept_form=_os.environ.get('AC9_TEST_E2_ACCEPT','wide')   # 'wide' (default): accept with the wide check; test values: 'ls' limit every trial, 'freeze' drop limited points
    def sc_energy(c):return 0. if sc is None else sc.energy(c)
    energy_cache=True
    sf=wp.zeros_like(force)
    def spring_data():
        data=(spring_array('spring_indices').reshape(-1,2),spring_array('spring_rest_length').astype(float),spring_array('spring_stiffness').astype(float),spring_array('spring_damping').astype(float),s.particle_q_prev.numpy().astype(float))
        validate_springs(m.particle_count,*data[:4])
        if not np.isfinite(data[4]).all():raise ValueError('Nonfinite spring anchor positions')
        return data
    fric={'obj':None,'pending':False,'begin':None}
    # test (AC9_TEST_BOUND_REDUCED): the dependent points' loads go to their supports in the step (bound_reduced.py)
    reduced=None;eval_mass=None
    if (bound is not None and _os.environ.get('AC9_TEST_BOUND_REDUCED','1')!='0') or _os.environ.get('AC9_TEST_SC_ACTIVESET'):   # test switch AC9_TEST_BOUND_REDUCED=0: the old step (dependent points' loads dropped)
        from .bound_reduced import ReducedBound
        reduced=ReducedBound(bound,m.particle_count,s.device)
        eval_mass=wp.array(reduced.eval_mass(m.particle_mass.numpy()).astype(np.float32),dtype=float,device=s.device)
    def evaluate(state,contacts,dt):
        wp.launch(k.simspike_eval_residual,dim=m.particle_count,inputs=[dt,s.particle_q_prev,state.particle_q,eval_mass if eval_mass is not None else m.particle_mass,s.inertia,m.particle_flags,m.tri_indices,m.tri_poses,m.tri_materials,m.tri_areas,m.edge_indices,m.edge_rest_angle,m.edge_rest_length,m.edge_bending_properties,s.ac9_bend_fric,s.particle_adjacency,force,hess],outputs=[rr,hh],device=s.device)
        _mark('ev_residual');body_force,body_hessian,count=fresh.evaluate(state,dt);_mark('ev_contacts')
        qn=state.particle_q.numpy().astype(float)
        if fric['pending']:
            p=fresh.current_parameters;F=LaggedBodyFriction(m.particle_count,float(s.friction_epsilon)*dt)
            anchor,anchored=fresh.anchor_state()
            fric['begin']=F.begin(qn,s.particle_q_prev.numpy(),p['indices'][:,0],p['point'],p['normal'],p['ke'],p['mu'],m.particle_radius.numpy(),float(m.shape_margin.numpy()[0]),anchor=anchor,anchored=anchored,kd=p['kd'],dt=dt)
            if anchor is not None:fric['begin']['anchored_friction_contacts']=int(np.sum(anchored[F.ids]));fric['begin']['anchored_contacts']=F.anchored_count
            fric['obj']=F;fric['pending']=False;_mark('ev_friction_begin')
        sf.zero_()
        if m.spring_count:wp.launch(spring_force,dim=m.spring_count,inputs=[dt,state.particle_q,s.particle_q_prev,m.spring_indices,m.spring_rest_length,m.spring_stiffness,m.spring_damping],outputs=[sf],device=s.device)
        F=fric['obj']
        _mark('ev_springs');force_=rr.numpy().astype(float)+cf.numpy().astype(float)+sf.numpy().astype(float)+F.force(qn);hess_=ch.numpy().astype(float)+F.hessian(qn);_mark('ev_host_sum')
        if _os.environ.get('AC9_TEST_DUMP_CHANGE'):fric['terms']={'rr':rr.numpy().astype(float),'cf':cf.numpy().astype(float),'sf':sf.numpy().astype(float),'friction':F.force(qn)}   # diagnostic only
        if hyst is not None and hyst.on:
            hf,slipping=hyst.force_at(qn);force_+=hf;fric['hyst_slip']=slipping;_mark('ev_hysteresis')
            if 'terms' in fric:fric['terms']['hyst']=hf
        if sc is not None:
            scf,sch=sc.evaluate(state.particle_q,dt);force_+=scf;hess_+=sch;fric['sc']=(scf,sch);_mark('ev_self_contact')
        if lc is not None:
            lcf,lch=lc.evaluate(state.particle_q);force_+=lcf;hess_+=lch;fric['lc']=(lcf,lch)
        G=fric.get('grab')
        if G is not None:
            ids,anchor,kp=G;force_[ids]+=kp[:,None]*(anchor-qn[ids]);hess_[ids]+=kp[:,None,None]*np.eye(3)
        A=attach_term(qn)
        if A is not None:
            np.add.at(force_,A[0],A[1]);np.add.at(hess_,A[0],A[2][:,None,None]*np.eye(3))
        return force_,hess_,count
    def grab_energy(q):
        G=fric.get('grab')
        if G is None:return 0.
        ids,anchor,kp=G;return float(.5*np.sum(kp*np.sum((np.asarray(q,float)[ids]-anchor)**2,axis=1)))
    def correct_once(state_in,state_out,contacts,dt,iteration):
        nonlocal captured,step_energy
        if iteration+1!=s.iterations:return
        if not active.any():return
        _mark('start');tic=time.perf_counter();q=state_in.particle_q.numpy().astype(float);fric['pending']=True;fric['grab']=grab_term()
        fric['hyst_slip']=None
        if hyst is not None:hyst.begin(m.edge_rest_length.numpy())
        bending={'edge':m.edge_indices.numpy(),'rest':m.edge_rest_angle.numpy(),'length':m.edge_rest_length.numpy(),'bending':m.edge_bending_properties.numpy(),'bf':s.ac9_bend_fric.numpy(),'buckle_ratio':0}
        activity=validate_dynamic_state(q,model,bending,dt,s.device) if fast_audit.ON or not diagnostics.summary['corrections'] else None
        _mark('validate');residual,hc,count=evaluate(state_in,contacts,dt);_mark('head_evaluate')
        if sc is not None:sc.begin(q,*fric['sc'])
        if lc is not None:lc.begin(q,*fric['lc'])
        # Native's layer recovery adds a wrong-side contact up to layers_recovery_mm away and pushes it back by
        # k (radius - s) with no bound on s; Native walks such a point back by its step cap (~0.19 mm per detection).
        # The correction would take that push in one step (30 N at 27 mm moved the cloth round it 25-100 mm), so
        # points pushed harder than any ordinary contact can push are left to Native's iterations.
        recovering=None
        if sc is not None and getattr(s,'ac9_layer_recovery_reach',0.)>s._self_contact_query_radius and not _os.environ.get('AC9_TEST_RECOVERY_IN_CORRECTION'):
            limit_N=2.*float(m.soft_contact_ke)*(float(s.particle_self_contact_margin)+float(s._self_contact_query_radius))
            recovering=np.linalg.norm(fric['sc'][0],axis=1)>limit_N
            if recovering.any():residual[recovering]=0.
            else:recovering=None
        sc_limited=0;sc_frozen=0
        if not np.isfinite(residual).all()or not np.isfinite(hc).all():raise ValueError('Nonfinite residual/contact matrix')
        F=fric['obj'];lag_force0=F.force(q)
        # audit only (2 Native evaluations per correction, not used by the step): cfg s3_friction_audit=False skips it
        audit=bool(_os.environ.get('AC9_TEST_FRIC_AUDIT'));native_fric0=fresh.native_friction(state_in,dt)if audit else lag_force0;mu_check=fresh.mu_agreement()if audit else None
        fric_at_start={'lag_vs_native_friction_max_N':float(np.abs(lag_force0-native_fric0).max())if audit else None,'native_friction_max_N':float(np.linalg.norm(native_fric0,axis=1).max())if audit else None,'lagged_friction_max_N':float(np.linalg.norm(lag_force0,axis=1).max()),'friction_opposes_slip_from_origin':bool(np.sum(lag_force0[F.ids]*(q[F.ids]-F.prev))<=1e-12) if len(F.ids) else True,'mu':mu_check}
        spring_force_before=float(np.linalg.norm(sf.numpy()[active]))
        before=float(np.linalg.norm(residual[active]));after=before;alpha=0.;change=np.zeros_like(q);clipped=0;limit=None;halvings=0
        if mode!='observe':
            free=np.repeat(model['mass']>0,3)
            if free.any():
                data=spring_data()
                slipping=fric['hyst_slip']
                if _os.environ.get('AC9_TEST_HYST_MATRIX','full')=='full':slipping=None   # the stick stiffness kept for slipping hinges too (test switch AC9_TEST_HYST_MATRIX=slip: k0 only, below)
                if slipping is not None and len(slipping):
                    # slipping hinges carry a constant moment: the solve matrix keeps only their elastic stiffness k0.
                    # Not the default: a hinge that goes back from slip to stick within the step meets the stick
                    # stiffness the matrix left out, the full step raised the energy (+1e-3 J against a predicted
                    # -2.5e-4 on a still shirt) and the line search cut it to 0.25-0.5. With the stick stiffness kept
                    # the full step is taken about 9 times in 10, 13-44 % less time per frame, the same or less drift.
                    props=m.edge_bending_properties.numpy().astype(float);props[slipping,0]=hyst.k0[np.searchsorted(hyst.rows_np,slipping)]
                    gpu_system.properties.assign(props)
                _mark('lag_and_data');change=reduced.solve(gpu_system,q,hc,residual,dt,data,gpu_system.linear_tol) if reduced is not None else gpu_system.solve(q,hc,residual,dt,data)[0];_mark('solve')
                if slipping is not None and len(slipping):gpu_system.properties.assign(m.edge_bending_properties.numpy().astype(float))
                if not np.isfinite(change).all():raise RuntimeError('Nonfinite correction')
                if followers is not None:change[followers]=0.
                if recovering is not None:change[recovering]=0.
                if ((_os.environ.get('AC9_TEST_DUMP_CHANGE') and np.linalg.norm(change,axis=1).max()>float(_os.environ['AC9_TEST_DUMP_CHANGE'])/1000) or int(_os.environ.get('AC9_TEST_DUMP_FRAME') or -1)==int(g['f'])) and not getattr(gpu_system,'_dumped',False):
                    np.savez(str(out/'change_dump.npz'),q=q,change=change,residual=residual,mass=model['mass'],frame=int(g['f']),inertia=s.inertia.numpy(),**{'term_'+k:v for k,v in fric.get('terms',{}).items()},**({'term_sc':fric['sc'][0]} if 'sc' in fric else {}),**({'term_lc':fric['lc'][0]} if 'lc' in fric else {}));gpu_system._dumped=True   # diagnostic only
                clipped=clip_to_body(q,change,fresh.current_parameters,m.particle_radius.numpy(),float(m.shape_margin.numpy()[0]))
                from .gpu_problem_energy import CachedGpuProblemEnergy as FixedProblemEnergy   # CPU reference: fixed_problem_energy.py
                # the cache (codex cached_problem_energy_speed) memoizes the frictionless fixed problem within one Native substep;
                # the lagged friction energy F.energy is added outside it, so the frozen friction state is never cached
                if energy_cache:
                    if step_energy is None:step_energy=FixedProblemEnergy(model,bending,data,data[4],s.inertia.numpy().astype(float),dt,m.particle_radius.numpy(),m.shape_margin.numpy()[0],s.device,angle_pool=g['cpu_geometry'])
                    energy=step_energy
                else:energy=FixedProblemEnergy(model,bending,data,data[4],s.inertia.numpy().astype(float),dt,m.particle_radius.numpy(),m.shape_margin.numpy()[0],s.device,angle_pool=g['cpu_geometry'])
                _mark('energy_setup');E0,_,meta0=energy.evaluate(q,fresh.current_parameters);parts0=[E0,F.energy(q),hyst_energy(q),sc_energy(q),lc_energy(q)];E0+=F.energy(q)+grab_energy(q)+attach_energy(q)+hyst_energy(q)+sc_energy(q)+lc_energy(q);_mark('energy_E0')
                sc_reach=None
                if sc is not None and accept_form=='wide':sc_reach=sc.widen(state_in,change)
                if sc is not None and accept_form=='freeze':
                    # the points Native's limit would cut at the full step keep Native's position; the rest are solved
                    _,cut=sc.truncate((q+change).astype(np.float32),state_in.particle_q)
                    if cut:
                        moved=np.any(state_in.particle_q.numpy()!=(q+change).astype(np.float32),axis=1)
                        change[moved]=0.;sc_frozen=int(moved.sum())
                    state_in.particle_q.assign(q.astype(np.float32))
                limit,path_hits=fresh.limit(q,(q+change).astype(np.float32));_mark('limit')
                trial=min(1.,max(0.,limit))
                identical_step_skipped=False;ls_fails=[];activeset_done=False;activeset_held=0
                for _ in range(12):
                    candidate=place_followers((q+trial*change).astype(np.float32))
                    if sc is not None:
                        candidate,sc_limited=(candidate,0) if _os.environ.get('AC9_TEST_SC_LIMITFREE') else sc.limit(candidate) if accept_form=='wide' else sc.truncate(candidate,state_in.particle_q)
                        if bound is not None:candidate=place_followers(candidate)
                        if sc_limited and _os.environ.get('AC9_TEST_SC_ACTIVESET') and not activeset_done and reduced is not None:
                            # test (active set): the points the cap held stay where it left them, the rest is solved again
                            activeset_done=True
                            held=np.nonzero(np.any(np.abs(candidate-(q+trial*change).astype(np.float32))>1e-9,axis=1))[0]
                            again=reduced.resolve(held,(candidate[held].astype(float)-q[held])/max(trial,1e-12))
                            if again is not None:
                                change=again;activeset_held=int(len(held))
                                candidate=place_followers((q+trial*change).astype(np.float32))
                                candidate,sc_limited=sc.limit(candidate)
                                if bound is not None:candidate=place_followers(candidate)
                        _mark('ls_truncate')
                    if False and np.array_equal(candidate,q.astype(np.float32)):
                        identical_step_skipped=True
                        break
                    state_in.particle_q.assign(candidate)
                    if fast_audit.ON:
                        r,candidate_h,_=evaluate(state_in,contacts,dt)
                        if not np.isfinite(r).all()or not np.isfinite(candidate_h).all():raise ValueError('Nonfinite candidate residual/H')
                        norm=float(np.linalg.norm(r[active]))
                    else:_mark('ls_assign');final_count=fresh.detect(state_in);norm=None;_mark('ls_detect')
                    Enew,_,metanew=energy.evaluate(candidate,fresh.current_parameters);_mark('ls_energy');Enew+=F.energy(candidate)+grab_energy(candidate)+attach_energy(candidate)+hyst_energy(candidate)+sc_energy(candidate)+lc_energy(candidate);_mark('ls_friction')
                    work=-float(np.sum(residual[active]*(candidate.astype(float)-q)[active]))
                    stable=bool(np.array_equal(meta0['hinge_active'],metanew['hinge_active']) and not np.any(meta0['hinge_active']&(np.abs(meta0['hinge_angle32']-metanew['hinge_angle32'])>np.pi)))
                    _mark('ls_checks')
                    path_ok=None
                    if stable and work<0 and Enew<E0 and Enew<=E0+1e-4*work and (path_ok:=fresh.verify_path(q,candidate)):
                        alpha=trial;after=norm;accepted=candidate;break
                    ls_fails.append([trial,stable,round(work,9),round(float(Enew-E0),9),path_ok]+([round(float(x-y),9) for x,y in zip([energy.evaluate(candidate,fresh.current_parameters)[0],F.energy(candidate),hyst_energy(candidate),sc_energy(candidate),lc_energy(candidate)],parts0)] if _os.environ.get('AC9_TEST_LS_PARTS') else []))
                    trial*=.5;halvings+=1
                if alpha==0:accepted=q.astype(np.float32)
                if sc is not None:   # Native's accumulated displacement matches the committed positions
                    s.particle_displacements.assign(accepted-s.pos_prev_collision_detection.numpy())
                _mark('line_search');state_in.particle_q.assign(accepted)
                state_out.particle_q.assign(accepted)
        finalq=state_in.particle_q.numpy()
        if fast_audit.ON or alpha==0:
            final_r,_,final_count=evaluate(state_in,contacts,dt)
            if not np.isfinite(final_r).all():raise ValueError('Nonfinite final residual')
            final_norm=float(np.linalg.norm(final_r[active]))
        else:final_norm=None
        _mark('final');signed=fresh.signed(finalq)
        if mode!='observe' and free.any():
            Efinal=energy.evaluate(finalq,fresh.current_parameters)[0]+F.energy(finalq)+grab_energy(finalq)+attach_energy(finalq)+hyst_energy(finalq)+sc_energy(finalq)+lc_energy(finalq)
            if Efinal>E0+1e-12:raise RuntimeError('Committed diagnostic energy increased')
        fresh.check_unchanged();F.check_unchanged();_mark('final_checks')
        lag_force1=F.force(finalq.astype(float));native_fric1=fresh.native_friction(state_in,dt)if audit else lag_force1
        slip=F.slip_mm(finalq.astype(float))
        fric_row={'friction_begin':fric['begin'],'at_start':fric_at_start,'final_lag_vs_native_friction_max_N':float(np.abs(lag_force1-native_fric1).max())if audit else None,'final_native_friction_max_N':float(np.linalg.norm(native_fric1,axis=1).max())if audit else None,'audit':audit,'final_slip_mm_p50_p99_max':[float(np.percentile(slip,50)),float(np.percentile(slip,99)),float(slip.max())]if len(slip)else None,'eps_u_mm':float(s.friction_epsilon)*dt*1000,'stick_fraction':float(np.mean(slip<float(s.friction_epsilon)*dt*1000))if len(slip)else None,'lagged_state_unchanged':True}
        diagnostics.record_correction({'linear_solve':gpu_system.last_linear if gpu_system is not None and hasattr(gpu_system,'last_linear') else None,'energy_cache':{'calls':energy.evaluation_calls,'hits':energy.cache_hits} if energy_cache and mode!='observe' else None,'friction':fric_row,'frame':int(g['f']),'dt':dt,'mode':mode,'energy_before_J':E0,'energy_after_J':Efinal,'grab_particles':0 if fric['grab'] is None else len(fric['grab'][0]),'grab_energy_after_J':grab_energy(finalq),'merit':'incremental_energy_diagnostic','identical_step_skipped':identical_step_skipped,'contacts':final_count,'new_contact_particles':fresh.last_new,'springs':int(m.spring_count),'spring_force_before_N':spring_force_before,'residual_before_N':before,'residual_after_N':final_norm,'alpha':alpha,'max_correction_mm':float(np.linalg.norm(alpha*change,axis=1).max()*1000),'min_mesh_signed_distance_mm':float(signed.min()*1000),'native_buffers_unchanged':True,'native_inactive_hinges':None if activity is None else int(np.sum(~activity)),'clipped':clipped,'path_limit':None if limit is None else float(limit),'halvings':halvings,'ls_fails':ls_fails,'activeset_held':activeset_held,'self_contact_limited':sc_limited,'self_contact_reach_mm':None if sc_reach is None else sc_reach*1000,'self_contact_frozen':sc_frozen,'layer_recovery_left_to_native':0 if recovering is None else int(recovering.sum()),'hysteresis_slipping':None if fric['hyst_slip'] is None else int(len(fric['hyst_slip'])),'near_negative_count':fresh.guard.last['near_negative_count'],'near_ambiguous_count':fresh.guard.last['near_ambiguous_count'],'min_feature_distance_mm':fresh.guard.last['min_feature_distance_mm'],'seconds':time.perf_counter()-tic,'stage_ms':_take_marks() if _TIMING else None})
    def sync_model():
        # Called before the first Native particle iteration of every substep.
        # Topology must stay fixed; Live material/mass arrays may update in place.
        if not np.array_equal(model['tri'],m.tri_indices.numpy()):raise ValueError('Fast Live topology changed')
        for key,source in [('mass',m.particle_mass),('poses',m.tri_poses),('materials',m.tri_materials),('areas',m.tri_areas)]:
            value=source.numpy().astype(float)
            if value.shape!=model[key].shape:raise ValueError('Fast Live array shape changed: '+key)
            model[key]=value
            getattr(gpu_system,key).assign(reduced.eval_mass(value) if key=='mass' and reduced is not None else value)
            if key!='mass':getattr(gpu_system.projector,key).assign(value)
        validate_arrays(model,m.particle_inv_mass.numpy(),m.particle_flags.numpy(),k.ParticleFlags.ACTIVE)
        active[:]=model['mass']>0
        gpu_system.active=active.copy()
        for attr,source in [('length',m.edge_rest_length),('properties',m.edge_bending_properties)]:
            getattr(gpu_system,attr).assign(source.numpy().astype(float))
        gpu_system.bf.assign(s.ac9_bend_fric.numpy() if s.ac9_bend_fric.shape[0] else np.zeros((gpu_system.ne,3)))
    last_safe=g['s0'].particle_q.numpy().copy();step_ordinal=0
    def wrapped(state_in,state_out,contacts,dt,iteration):
        nonlocal last_safe,step_ordinal,step_energy
        if iteration==0:sync_model()
        original(state_in,state_out,contacts,dt,iteration)
        if not phase['fast']:
            if iteration+1==s.iterations:
                phase['native_only_substeps']+=1;diagnostics.summary['native_only_substeps']=phase['native_only_substeps']
                last_safe=state_in.particle_q.numpy().copy()   # the substep's solved shape, guard not inspected
            return
        if iteration+1==s.iterations:
            original_in=state_in.particle_q.numpy().copy();original_out=state_out.particle_q.numpy().copy()
            try:
                step_ordinal+=1;step_energy=None   # the energy cache is valid within one Native substep only
                native_post=state_in.particle_q.numpy().copy()
                _mark('wrap_start');_,_,native_safe=fresh.guard.inspect(native_post);_mark('wrap_guard')
                warm={'frame':int(g['f']),'substep_ordinal':step_ordinal,'native_post_safe':native_safe,'native_post_guard':{key:value for key,value in fresh.guard.last.items()if key not in ('face','bary')},'restarted':False}
                diagnostics.record_native(warm)
                if not native_safe:raise RuntimeError('Native step left the supported body side; Fast Live stopped')
                fresh.begin_step(contacts);_mark('wrap_begin')
                fresh.signed(state_in.particle_q.numpy())
                every=int(_os.environ.get('AC9_TEST_CORRECT_EVERY','1'))   # test switch: correct every n-th substep only (the others keep the Native step)
                for _ in range(int(_os.environ.get('AC9_TEST_CORRECTIONS','1')) if step_ordinal%every==0 else 0):   # test switch: corrections per substep
                    correct_once(state_in,state_out,contacts,dt,iteration)
                last_safe=state_in.particle_q.numpy().copy()
            except Exception:
                diagnostics.summary['failures']+=1
                diagnostics.flush()
                np.savez_compressed(out/'failure_state.npz',q=state_in.particle_q.numpy(),native_post=native_post,last_safe=last_safe)
                error={'substep_ordinal':step_ordinal,'frame':int(g['f']),'exception':traceback.format_exc(),'rollback_scope':'candidate q only; stop required; no checkpoint permitted'}
                try:
                    error['discarded_in_correction_max_mm']=float(np.abs(state_in.particle_q.numpy()-original_in).max()*1000)
                    state_in.particle_q.assign(original_in);state_out.particle_q.assign(original_out)
                    error['rollback_in_max_difference_mm']=float(np.abs(state_in.particle_q.numpy()-original_in).max()*1000)
                    error['rollback_out_max_difference_mm']=float(np.abs(state_out.particle_q.numpy()-original_out).max()*1000)
                except Exception:error['rollback_exception']=traceback.format_exc()
                (out/'failure.json').write_text(json.dumps(error,indent=2),encoding='utf8')
                raise
    s._solve_particle_iteration=wrapped
    return diagnostics.flush
