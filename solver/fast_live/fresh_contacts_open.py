# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh vertex-body geometry on private buffers, preserving native penalties.

Scope: one closed static mesh in world coordinates, no friction/damping/history.
The post-VBD penalty for a known (particle, shape) stays fixed during correction;
new particles use the native cold-start kernel. Native contact state is read only.
"""
import hashlib,numpy as np,warp as wp
from . import audit
import newton
from newton._src.solvers.vbd.rigid_vbd_kernels import init_body_particle_contacts
from .mesh_fixtures import check_closed
from .mesh_safety import signed_distance,path_limits

@wp.kernel
def remap(registry:wp.array[float],indices:wp.array[wp.vec3i],count:wp.array[int],penalty:wp.array[float]):
    i=wp.tid()
    if i<count[0]:
        p=indices[i][0]
        if registry[p]>=0.:penalty[i]=registry[p]

class FreshOpenContacts:
    def __init__(self,g,kernel,colors,emptyv,emptyi):
        self.g=g;self.m=g['model'];self.s=g['solver'];self.pipe=g['pipe'];self.k=kernel;self.colors=colors;self.emptyv=emptyv;self.emptyi=emptyi
        m=self.m;s=self.s;device=s.device
        if m.body_count or m.shape_count!=1 or int(m.shape_type.numpy()[0])!=int(newton.GeoType.MESH):raise ValueError('Fresh contact scope is one static mesh')
        if not np.allclose(m.shape_scale.numpy(),1.,atol=1e-7):raise ValueError('Scaled mesh unsupported')
        if not np.allclose(m.shape_transform.numpy()[0],np.array([0,0,0,0,0,0,1]),atol=1e-7):raise ValueError('Mesh transform must be identity')
        if m.shape_body.numpy()[0]!=-1:raise ValueError('Mesh must not belong to a moving body')
        source=m.shape_source[0];v=np.asarray(source.vertices).reshape(-1,3);f=np.asarray(source.indices).reshape(-1,3)
        self.mesh_info=check_closed(v,f)
        if self.mesh_info['nonmanifold_edges'] or self.mesh_info['inconsistent_two_face_edges']:raise ValueError('Open mesh must have consistent two-face edges and no nonmanifold edges')
        if int(m._shape_mesh_properties.numpy()[0])!=0:raise ValueError('AC9 Fast Live does not support this closed-body contact mode; use Native Live')
        self.mesh_id=int(m.shape_source_ptr.numpy()[0]);self.contacts=self.pipe.contacts()
        if self.contacts._enable_rigid_soft_full_surface_contact:raise ValueError('Edge/face body contacts are not supported yet')
        capacity=self.contacts.soft_contact_max
        self.penalty=wp.zeros(capacity,dtype=float,device=device);self.ke=wp.zeros_like(self.penalty);self.kd=wp.zeros_like(self.penalty);self.mu=wp.zeros_like(self.penalty)
        self.registry=wp.full(m.particle_count,-1.,dtype=float,device=device)
        self.force=wp.zeros(m.particle_count,dtype=wp.vec3,device=device);self.H=wp.zeros(m.particle_count,dtype=wp.mat33,device=device)
        self.q0=wp.zeros(m.particle_count,dtype=wp.vec3,device=device);self.q1=wp.zeros_like(self.q0)
        self.distance=wp.zeros(m.particle_count,dtype=float,device=device);self.valid=wp.zeros(m.particle_count,dtype=int,device=device)
        self.limits=wp.zeros_like(self.distance);self.hits=wp.zeros_like(self.valid);self.ray_distance=wp.zeros_like(self.distance)
        self.original=None;self.signature=None;self.last_ids=np.empty(0,int);self.current_parameters=None;self.last_new=0
        from .open_mesh_guard import OpenMeshGuard
        band=m.particle_radius.numpy()+float(m.shape_margin.numpy()[0])+float(self.pipe.soft_contact_gap)
        self.guard=OpenMeshGuard(self.mesh_id,v,f,band,device)
        import hashlib
        self.geometry_sha256=hashlib.sha256(v.tobytes()+f.tobytes()).hexdigest()
    def _signature(self):
        c=self.original;s=self.s;count=int(c.soft_contact_count.numpy()[0]);arrays=[c.soft_contact_count.numpy()]
        arrays.extend(getattr(c,name).numpy()[:count]for name in ['soft_contact_indices','soft_contact_shape','soft_contact_body_pos','soft_contact_body_vel','soft_contact_normal','soft_contact_barycentric'])
        arrays.extend(getattr(s,name).numpy()[:count]for name in ['body_particle_contact_penalty_k','body_particle_contact_material_ke','body_particle_contact_material_kd','body_particle_contact_material_mu'])
        arrays.extend([s.particle_q_prev.numpy(),s.inertia.numpy()])
        h=hashlib.sha256()
        for a in arrays:h.update(a.tobytes())
        return h.hexdigest()
    def begin_step(self,original):
        self.original=original;count=int(original.soft_contact_count.numpy()[0]);indices=original.soft_contact_indices.numpy()[:count];shapes=original.soft_contact_shape.numpy()[:count]
        if count<0 or count>original.soft_contact_max:raise ValueError('Native contact overflow')
        if count and (np.any(indices[:,0]<0)or np.any(indices[:,0]>=self.m.particle_count)):raise ValueError('Native particle ID out of range')
        if count and(np.any(indices[:,1:]>=0)or np.any(shapes!=0)or len(np.unique(indices[:,0]))!=count):raise ValueError('Native contacts must be unique vertex/shape pairs')
        registry=np.full(self.m.particle_count,-1.,np.float32)
        if count:
            values=self.s.body_particle_contact_penalty_k.numpy()[:count]
            if not np.isfinite(values).all()or np.any(values<0):raise ValueError('Invalid native penalty')
            registry[indices[:,0]]=values
        self.registry.assign(registry);self.registry_cpu=registry;self.signature=self._signature() if audit.ON else None
    def check_unchanged(self):
        if not audit.ON:return
        if self._signature()!=self.signature:raise RuntimeError('Fresh detection modified native contact state or substep reference')
    def evaluate(self,state,dt,detect=True,preserve=True):
        m=self.m;s=self.s;c=self.contacts
        if detect:self.pipe.collide(state,c)
        count=int(c.soft_contact_count.numpy()[0])
        if count<0 or count>c.soft_contact_max:raise ValueError('Fresh contact overflow')
        indices=c.soft_contact_indices.numpy()[:count];shapes=c.soft_contact_shape.numpy()[:count]
        if count and (np.any(indices[:,0]<0)or np.any(indices[:,0]>=m.particle_count)):raise ValueError('Fresh particle ID out of range')
        if count and(np.any(indices[:,1:]>=0)or np.any(shapes!=0)or len(np.unique(indices[:,0]))!=count):raise ValueError('Fresh contacts must be unique vertex/shape pairs')
        wp.launch(init_body_particle_contacts,dim=c.soft_contact_max,inputs=[c.soft_contact_count,c.soft_contact_shape,m.soft_contact_ke,m.soft_contact_kd,m.soft_contact_mu,m.shape_material_ke,m.shape_material_kd,m.shape_material_mu,s.rigid_contact_k_start_value],outputs=[self.penalty,self.kd,self.mu,self.ke],device=s.device)
        if preserve:wp.launch(remap,dim=count,inputs=[self.registry,c.soft_contact_indices,c.soft_contact_count],outputs=[self.penalty],device=s.device)
        if count and(np.any(self.mu.numpy()[:count]!=0)or np.any(self.kd.numpy()[:count]!=0)):raise ValueError('Fresh friction or damping unsupported')
        self.force.zero_();self.H.zero_()
        if count:
            wp.launch(self.k.accumulate_particle_body_contact_force_and_hessian,dim=count,inputs=[dt,0,s.particle_q_prev,state.particle_q,self.colors,s.friction_epsilon,m.particle_radius,c.soft_contact_indices,c.soft_contact_count,c.soft_contact_max,self.penalty,self.ke,self.kd,self.mu,m.shape_body,state.body_q,None,state.body_qd,m.body_com,c.soft_contact_shape,c.soft_contact_body_pos,c.soft_contact_body_vel,c.soft_contact_normal,m.shape_margin,c.soft_contact_barycentric,self.emptyv,self.emptyv,self.emptyi],outputs=[self.force,self.H],device=s.device)
        self.check_unchanged();self.last_ids=indices[:,0].copy();self.last_new=int(np.sum(self.registry_cpu[self.last_ids]<0))
        self.current_parameters={'indices':indices,'shape':shapes,'point':c.soft_contact_body_pos.numpy()[:count],'normal':c.soft_contact_normal.numpy()[:count],'ke':self.penalty.numpy()[:count]}
        force=self.force.numpy().astype(float);hessian=self.H.numpy().astype(float)
        if not np.isfinite(force).all()or not np.isfinite(hessian).all():raise ValueError('Nonfinite fresh force/H')
        return force,hessian,count
    def audit_original_geometry(self,state,dt):
        c=self.original;s=self.s;m=self.m;k=self.k;device=s.device;count=int(c.soft_contact_count.numpy()[0])
        for name in ['soft_contact_count','soft_contact_indices','soft_contact_shape','soft_contact_body_pos','soft_contact_body_vel','soft_contact_normal','soft_contact_barycentric']:
            getattr(self.contacts,name).assign(getattr(c,name).numpy())
        f=wp.zeros_like(self.force);h=wp.zeros_like(self.H)
        if count:wp.launch(k.accumulate_particle_body_contact_force_and_hessian,dim=count,inputs=[dt,0,s.particle_q_prev,state.particle_q,self.colors,s.friction_epsilon,m.particle_radius,c.soft_contact_indices,c.soft_contact_count,c.soft_contact_max,s.body_particle_contact_penalty_k,s.body_particle_contact_material_ke,s.body_particle_contact_material_kd,s.body_particle_contact_material_mu,m.shape_body,state.body_q,None,state.body_qd,m.body_com,c.soft_contact_shape,c.soft_contact_body_pos,c.soft_contact_body_vel,c.soft_contact_normal,m.shape_margin,c.soft_contact_barycentric,self.emptyv,self.emptyv,self.emptyi],outputs=[f,h],device=device)
        native_f=f.numpy();native_h=h.numpy()
        cold_f,_,_=self.evaluate(state,dt,detect=False,preserve=False)
        mapped_f,mapped_h,_=self.evaluate(state,dt,detect=False,preserve=True)
        return {'contacts':count,'mapped_force_max_difference_N':float(np.abs(native_f-mapped_f).max()),'mapped_hessian_max_difference_N_m':float(np.abs(native_h-mapped_h).max()),'cold_only_force_max_difference_N':float(np.abs(native_f-cold_f).max()),'native_buffers_unchanged':self._signature()==self.signature}
    def signed(self,q):
        if not isinstance(q,np.ndarray):q=q.numpy()
        side,_,safe=self.guard.inspect(q)
        if not safe:raise ValueError('Open mesh local side/feature guard rejected state')
        return side
    def limit(self,start,end):
        return self.guard.limit(start,end)
    def verify_path(self,start,end):
        return self.guard.verify(start,end)
