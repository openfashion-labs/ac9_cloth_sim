"""Collect native active contacts over the complete affected stencil of a shadow patch."""
import numpy as np
import warp as wp
from newton._src.solvers.vbd import particle_vbd_kernels as K
from layer_patch import Contact
@wp.kernel
def evaluate(rows:wp.array2d[int],x:wp.array[wp.vec3],prev:wp.array[wp.vec3],tri:wp.array2d[int],lay:wp.array[int],sign:wp.array[float],pairs:K.AC9LayerPairs,radius:float,ke:float,kd:float,mu:float,eps:float,dt:float,out_f:wp.array2d[wp.vec3],out_h:wp.array2d[wp.mat33]):
    row=wp.tid();v=rows[row,0];t=rows[row,1]
    if K.ac9_layer_cross_vt(v,t,x,tri,lay,sign,pairs):return
    use,n=K.ac9_layer_dir(v,t,x,tri,lay,sign,pairs)
    if use!=0:
        hit,fa,fb,fc,fv,ha,hb,hc,hv=K.ac9_layer_vt_contact_4_vertices(v,t,n,x,prev,tri,radius,ke,kd,mu,eps,dt)
    else:
        hit,fa,fb,fc,fv,ha,hb,hc,hv=K.evaluate_vertex_triangle_collision_force_hessian_4_vertices(v,t,x,prev,tri,radius,ke,kd,mu,eps,dt)
    if hit:
        out_f[row,0]=fa;out_f[row,1]=fb;out_f[row,2]=fc;out_f[row,3]=fv
        out_h[row,0]=ha;out_h[row,1]=hb;out_h[row,2]=hc;out_h[row,3]=hv




@wp.kernel
def edge_forces(rows:wp.array2d[int],pos:wp.array[wp.vec3],prev:wp.array[wp.vec3],tri:wp.array2d[int],edges:wp.array2d[int],layers:wp.array[int],signs:wp.array[float],wrong:wp.array[int],pairs:K.AC9LayerPairs,radius:float,ke:float,kd:float,epsilon:float,dt:float,parallel:float,out:wp.array2d[wp.vec3]):
    row=wp.tid();a=rows[row,0];b=rows[row,1]
    if K.ac9_layer_ee_free(a,b,edges,layers,wrong,pos,tri,signs,pairs):return
    hit,fa,fb,ha,hb=K.evaluate_edge_edge_contact_2_vertices(a,b,pos,prev,edges,radius,ke,kd,0.,epsilon,dt,parallel)
    reverse,fc,fd,hc,hd=K.evaluate_edge_edge_contact_2_vertices(b,a,pos,prev,edges,radius,ke,kd,0.,epsilon,dt,parallel)
    if hit and reverse:
        out[row,0]=fa;out[row,1]=fb;out[row,2]=fc;out[row,3]=fd


def buffered_rows(data, counts, sizes, offsets):
    count=counts.numpy();capacity=sizes.numpy();assert np.all(count<=capacity),'Contact buffer overflow: incomplete fixture refused'
    off=offsets.numpy()
    selected=np.flatnonzero(count);number=count[selected];total=int(number.sum())
    if not total:return np.empty((0,2),np.int32)
    prefix=np.r_[0,np.cumsum(number)[:-1]]
    slots=(np.repeat(off[selected]-prefix,number)+np.arange(total)).astype(np.int64)
    rows=data.numpy().reshape(-1,2)[slots]
    return rows[(rows>=0).all(1)].astype(np.int32)


def selected_witnesses(moving,edges,targets,proof,other,flags,F):
    active=np.flatnonzero(flags)
    touched=moving[edges[active]].any(1)|moving[F[targets[active]]].any(1)
    for support in (proof,other):
        valid=support[active]>=0
        touched|=valid&moving[F[np.maximum(support[active],0)]].any(1)
    return active[touched]


def validate_body_contacts(g):
    """Refuse incomplete native body records before any coupled evaluation."""
    call=g['captured'].get('accumulate_particle_body_contact_force_and_hessian')
    if call is None:return 0
    args=call['inputs'];counter=np.asarray(args[8].numpy()).reshape(-1)
    if len(counter)!=1:raise ValueError('Incomplete body contacts: invalid counter')
    count=int(counter[0]);maximum=int(args[9]);capacity=maximum
    for index in (7,10,11,12,13,19,20,21,22,24):
        array=args[index]
        if array is None:raise ValueError('Incomplete body contacts: missing record array')
        shape=getattr(array,'shape',None)
        length=int(shape[0])if shape is not None else len(array.numpy())
        capacity=min(capacity,length)
    if maximum<0 or count<0 or count>capacity:
        raise ValueError(f'Incomplete body contacts: count {count}, capacity {capacity}')
    solver=g['s'];overflow=getattr(solver,'body_particle_contact_overflow_max',None)
    allocated=getattr(solver,'body_particle_contact_buffer_pre_alloc',None)
    if overflow is not None and allocated is not None:
        values=overflow.numpy()
        if values.size and np.max(values)>allocated:
            raise ValueError('Incomplete body contacts: native per-body buffer overflow')
    return count


def collect(g,movable,primary_edge):
    body_count=validate_body_contacts(g)
    s=g['s'];m=g['m'];state=g['state'];p=g['p'];x=g['x'];F=g['F'];dt=g['dt'];N=len(x)
    moving=np.zeros(N,bool);moving[movable]=True;E=m.edge_indices.numpy()
    affected_tri=moving[F].any(1);affected_edge=moving[E[:,2:4]].any(1)
    ci=s.trimesh_collision_detector.collision_info
    vt=buffered_rows(ci.vertex_colliding_triangles,ci.vertex_colliding_triangles_count,ci.vertex_colliding_triangles_buffer_sizes,ci.vertex_colliding_triangles_offsets)
    vt=vt[moving[vt[:,0]]|affected_tri[vt[:,1]]]
    forces=wp.zeros((len(vt),4),dtype=wp.vec3);hess=wp.zeros((len(vt),4),dtype=wp.mat33)
    wp.launch(evaluate,dim=len(vt),inputs=[wp.array(vt,dtype=int),state.particle_q,s.particle_q_prev,m.tri_indices,s.ac9_layer,s.ac9_tri_sign,p,s.particle_self_contact_margin,m.soft_contact_ke,m.soft_contact_kd,0.,s.friction_epsilon,dt,forces,hess])
    vf=forces.numpy();records=[]
    for (v,t),force in zip(vt,vf):
        norm=np.linalg.norm(force[3])
        if norm>1.e-12:
            records.append(Contact('vt',tuple(map(int,[*F[t],v])),force[3].astype(float)/norm,key=(int(v),int(t))))
    vt_active=len(records)
    ee=buffered_rows(ci.edge_colliding_edges,ci.edge_colliding_edges_count,ci.edge_colliding_edges_buffer_sizes,ci.edge_colliding_edges_offsets)
    ee=ee[affected_edge[ee].any(1)];ee=np.unique(np.sort(ee,axis=1),axis=0);ee=ee[ee[:,0]!=ee[:,1]]
    ef=wp.zeros((len(ee),4),dtype=wp.vec3)
    wp.launch(edge_forces,dim=len(ee),inputs=[wp.array(ee,dtype=int),state.particle_q,s.particle_q_prev,m.tri_indices,m.edge_indices,s.ac9_layer,s.ac9_tri_sign,s.ac9_wrong,p,s.particle_self_contact_margin,m.soft_contact_ke,m.soft_contact_kd,s.friction_epsilon,dt,s._self_contact_edge_edge_parallel_epsilon,ef])
    edge_force=ef.numpy();ee_active=0
    for (a,b),force in zip(ee,edge_force):
        normal=(force[0]+force[1]).astype(float);norm=np.linalg.norm(normal)
        if norm>1.e-12:
            records.append(Contact('ee',tuple(map(int,[*E[a,2:4],*E[b,2:4]])),normal/norm,key=(int(a),int(b))));ee_active+=1
    body_call=g['captured'].get('accumulate_particle_body_contact_force_and_hessian')
    if body_call is not None:
        ba=body_call['inputs'];count=body_count;indices=ba[7].numpy()[:count];weights=ba[24].numpy()[:count];point=ba[20].numpy()[:count];normals=ba[22].numpy()[:count];shapes=ba[19].numpy()[:count]
    else:
        count=0;indices=np.empty((0,3),int);weights=point=normals=np.empty((0,3));shapes=np.empty(0,int)
    body_active=0;shape_transform=m.shape_transform.numpy()if count else np.empty((0,7));shape_body=m.shape_body.numpy()if count else np.empty(0,int)
    valid_body=(indices>=0)&(np.abs(weights)>0)
    selected_body=np.flatnonzero((moving[np.maximum(indices,0)]&valid_body).any(1))
    for body_index in selected_body:
        ids=indices[body_index];bary=weights[body_index];cp=point[body_index];normal=normals[body_index];shape=shapes[body_index]
        valid=(ids>=0)&(np.abs(bary)>0)
        if not valid.any()or not moving[ids[valid]].any():continue
        assert shape_body[shape]<0 and np.max(np.abs(shape_transform[shape]-np.array([0,0,0,0,0,0,1])))<1.e-10,'Nonstatic/transformed body unsupported in CPU fixture'
        records.append(Contact('body',tuple(map(int,ids[valid])),normal.astype(float),key=(int(shape),body_active),weights=bary[valid].astype(float),point=cp.astype(float)));body_active+=1
    primary=None;witness_ids=[];cross_edges=p.cross_vertices.numpy();targets=p.cross_targets.numpy();proof=p.cross_witnesses.numpy();other=p.cross_patch_other.numpy();flags=p.cross_flags.numpy();ts=p.cross_t.numpy();sign=p.cross_sign.numpy()
    for edge in selected_witnesses(moving,cross_edges,targets,proof,other,flags,F):
        target=int(targets[edge]);nodes=tuple(map(int,[*cross_edges[edge],*F[target]]))
        if edge==primary_edge:primary=len(records)
        records.append(Contact('witness',nodes,np.zeros(3),key=(int(edge),target),t=float(ts[edge]),sign=float(sign[edge])));witness_ids.append(int(edge))
    if primary_edge is not None:assert primary is not None,'Primary witness retired'
    metadata={'movable_vertices':len(movable),'affected_triangles':int(affected_tri.sum()),'affected_edges':int(affected_edge.sum()),'native_vt_rows_examined':len(vt),'active_vt_rows':vt_active,'native_ee_pairs_examined':len(ee),'active_ee_pairs':ee_active,'body_records':body_active,'active_witnesses':len(witness_ids),'query_radius_mm':s._self_contact_query_radius*1000.,'contact_radius_mm':s.particle_self_contact_margin*1000.,'stencil_rule':'Any source point OR target triangle/edge with a moving endpoint; fixed opposite endpoints remain in constraints.','witness_edges':witness_ids,'all_records':len(records)}
    return records,primary,metadata
