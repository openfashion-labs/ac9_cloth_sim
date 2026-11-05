# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent potential for the bounded frictionless fixed Native problem.

BW compression softening, BF history, body damping/friction are excluded.
Native float32 hinge masks/charts are recorded; no smoothness is claimed when
those branches change. This evaluator does not change positions or forces.
"""
import numpy as np,warp as wp
from .native_hinge_geometry import angles

class FixedProblemEnergy:
    def __init__(self,model,bend,spring,prev,inertia,dt,radius,margin,device,*,angle_pool=None):
        self.angle_pool=angle_pool
        self.model=model;self.bend=bend;self.spring=spring;self.prev=np.asarray(prev,float);self.inertia=np.asarray(inertia,float);self.dt=dt;self.radius=np.asarray(radius,float);self.margin=float(margin);self.device=device
        self.q=wp.zeros(len(prev),dtype=wp.vec3,device=device);self.edge=wp.array(bend['edge'],dtype=int,device=device);self.theta=wp.zeros(len(bend['edge']),dtype=float,device=device);self.active=wp.zeros(len(bend['edge']),dtype=int,device=device)
        self.prev_theta,self.prev_active,self.prev_theta32=self.geometry(prev)
        pose=model['poses'];tri=model['tri'];self.Fprev=np.stack([self.prev[tri[:,1]]-self.prev[tri[:,0]],self.prev[tri[:,2]]-self.prev[tri[:,0]]],axis=-1)@pose
    def geometry(self,q):
        self.q.assign(q);wp.launch(angles,dim=len(self.bend['edge']),inputs=[self.q,self.edge],outputs=[self.theta,self.active],device=self.device);angle32=self.theta.numpy();active=self.active.numpy().astype(bool);theta=np.zeros(len(active),float)
        if active.any():
            e=self.bend['edge'][active]
            if self.angle_pool is not None:
                value=self.angle_pool.angles(q,e,angle32[active])
            else:
                from .cpu_hinge_geometry import _angles
                value=_angles(q,e,angle32[active])
            theta[active]=value
        return theta,active,angle32
    def evaluate(self,q,body):
        q=np.asarray(q,float);model=self.model;tri=model['tri'];mat=model['materials'];area=model['areas'];dt=self.dt;F=np.stack([q[tri[:,1]]-q[tri[:,0]],q[tri[:,2]]-q[tri[:,0]]],axis=-1)@model['poses'];u=F[:,:,0];v=F[:,:,1];up=self.Fprev[:,:,0];vp=self.Fprev[:,:,1];lu=np.linalg.norm(u,axis=1);lv=np.linalg.norm(v,axis=1);uv=np.sum(u*v,axis=1)
        elastic=np.sum(area*.5*(mat[:,0]*(lu-1)**2+mat[:,1]*(lv-1)**2+mat[:,3]*uv**2));dc00=np.sum(u*u-up*up,axis=1);dc01=uv-np.sum(up*vp,axis=1);dc11=np.sum(v*v-vp*vp,axis=1);metric=np.sum(area*mat[:,2]/(2*dt)*(dc00**2+2*dc01**2+dc11**2))
        inertia=.5*np.sum(model['mass'][:,None]*(q-self.inertia)**2)/dt**2
        theta,mask,theta32=self.geometry(q);bend=self.bend;ke=bend['bending'][:,0]*bend['length'];kd=bend['bending'][:,1]*bend['length'];be=.5*np.sum(ke[mask]*(theta[mask]-bend['rest'][mask])**2)
        damping_mask=mask&self.prev_active;delta=theta-self.prev_theta;delta32=theta32-self.prev_theta32;wrapped=np.where(delta32>np.float32(np.pi),delta32-np.float32(2*np.pi),np.where(delta32<np.float32(-np.pi),delta32+np.float32(2*np.pi),delta32));delta+=2*np.pi*np.rint((wrapped.astype(float)-delta)/(2*np.pi));bd=.5*np.sum(kd[damping_mask]*delta[damping_mask]**2)/dt
        indices,rest,ske,skd,prev=self.spring;length=np.maximum(np.linalg.norm(q[indices[:,0]]-q[indices[:,1]],axis=1),1e-8);old=np.maximum(np.linalg.norm(prev[indices[:,0]]-prev[indices[:,1]],axis=1),1e-8);se=.5*np.sum(ske*(length-rest)**2)+.5*np.sum(skd*(length-old)**2)/dt
        ids=body['indices'][:,0];distance=np.sum(body['normal'].astype(float)*(q[ids]-body['point']),axis=1);penetration=np.maximum(self.radius[ids]+self.margin-distance,0);ce=.5*np.sum(body['ke'].astype(float)*penetration**2)
        parts={'inertia':float(inertia),'membrane_elastic':float(elastic),'membrane_metric_damping':float(metric),'bending_elastic':float(be),'bending_damping':float(bd),'springs':float(se),'body_penalty':float(ce)};total=float(sum(parts.values()))
        if not np.isfinite(total):raise ValueError('Nonfinite fixed problem energy')
        return total,parts,{'hinge_active':mask,'hinge_angle32':theta32}
