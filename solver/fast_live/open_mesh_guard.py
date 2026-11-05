# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative local-side guard for an unchanged, fixed open mesh.

No volume interpretation; no cloth-face CCD. Ambiguous near-surface and feature
paths are rejected. Native collision generation is not filtered or modified.
"""
import numpy as np,warp as wp
from .mesh_safety import path_limits

@wp.kernel
def query_local(mesh:wp.uint64,q:wp.array[wp.vec3],band:wp.array[float],distance:wp.array[float],side:wp.array[float],valid:wp.array[int],face:wp.array[int],bary:wp.array[wp.vec3]):
    i=wp.tid();r=wp.mesh_query_point_no_sign(mesh,q[i],1.e6)
    valid[i]=0;face[i]=-1;distance[i]=-1.;side[i]=-1.
    if r.result:
        point=wp.mesh_eval_position(mesh,r.face,r.u,r.v);d=wp.length(q[i]-point)
        distance[i]=d;face[i]=r.face;bary[i]=wp.vec3(r.u,r.v,1.-r.u-r.v);side[i]=d;valid[i]=1
        if d<=band[i]:
            local=wp.mesh_query_point_sign_normal(mesh,q[i],band[i]+1.e-6)
            if not local.result:valid[i]=0
            else:side[i]=local.sign*wp.length(q[i]-wp.mesh_eval_position(mesh,local.face,local.u,local.v))

@wp.kernel
def feature_distance(q:wp.array[wp.vec3],a:wp.array[wp.vec3],b:wp.array[wp.vec3],distance:wp.array[float]):
    i=wp.tid();best=float(1.e6)
    for j in range(a.shape[0]):
        delta=b[j]-a[j];ll=wp.dot(delta,delta);t=float(0.)
        if ll>0.:t=wp.clamp(wp.dot(q[i]-a[j],delta)/ll,0.,1.)
        best=wp.min(best,wp.length(q[i]-a[j]-t*delta))
    distance[i]=best

class OpenMeshGuard:
    def __init__(self,mesh_id,vertices,faces,band,device='cuda:0'):
        self.mesh_id=mesh_id;self.device=device;self.band_cpu=np.asarray(band,np.float32);self.n=len(band)
        if not np.isfinite(self.band_cpu).all()or np.any(self.band_cpu<=0):raise ValueError('Positive finite Native query band required')
        v=np.asarray(vertices,np.float32);f=np.asarray(faces,np.int32)
        edges=np.vstack([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);unique,count=np.unique(np.sort(edges,axis=1),axis=0,return_counts=True)
        if np.any(count>2):raise ValueError('Nonmanifold open mesh is unsupported')
        area=np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]),axis=1);bad=f[area<=1e-14]
        features=np.vstack([unique[count==1],bad[:,[0,1]],bad[:,[1,2]],bad[:,[2,0]]]);self.features_cpu=features
        self.a=wp.array(v[features[:,0]],dtype=wp.vec3,device=device);self.b=wp.array(v[features[:,1]],dtype=wp.vec3,device=device)
        self.q0=wp.zeros(self.n,dtype=wp.vec3,device=device);self.q1=wp.zeros_like(self.q0)
        self.band=wp.array(self.band_cpu,dtype=float,device=device)
        self.distance=wp.zeros(self.n,dtype=float,device=device);self.side=wp.zeros_like(self.distance);self.feature=wp.zeros_like(self.distance)
        self.valid=wp.zeros(self.n,dtype=int,device=device);self.face=wp.zeros_like(self.valid);self.bary=wp.zeros(self.n,dtype=wp.vec3,device=device)
        self.hits=wp.zeros_like(self.valid);self.limits=wp.zeros_like(self.distance);self.ray_distance=wp.zeros_like(self.distance);self.last={}
    def inspect(self,q):
        q=np.asarray(q,np.float32)
        # exact memo of the last two inputs (fixed mesh and band, so the same q gives the same answer)
        for key,value in getattr(self,'_memo',()):
            if np.array_equal(key,q):
                self.last=value[3];return value[:3]
        if not np.isfinite(q).all():raise ValueError('Nonfinite mesh query input')
        self.q0.assign(q)
        wp.launch(query_local,dim=self.n,inputs=[self.mesh_id,self.q0,self.band],outputs=[self.distance,self.side,self.valid,self.face,self.bary],device=self.device)
        wp.launch(feature_distance,dim=self.n,inputs=[self.q0,self.a,self.b],outputs=[self.feature],device=self.device)
        d=self.distance.numpy();s=self.side.numpy();feature=self.feature.numpy()
        if not np.all(self.valid.numpy()==1)or not all(np.isfinite(x).all()for x in [d,s,feature,self.bary.numpy()]):raise ValueError('Invalid open surface query')
        near=d<=self.band_cpu
        safe=bool(np.all(s[near]>1.e-6)and np.all(feature>self.band_cpu+1.e-6))
        self.last={'safe':safe,'near_negative_count':int(np.sum(near&(s<0))),'near_ambiguous_count':int(np.sum(near&(np.abs(s)<=1.e-6))),'min_unsigned_distance_mm':float(d.min()*1000),'min_feature_distance_mm':float(feature.min()*1000),'face':self.face.numpy().copy(),'bary':self.bary.numpy().copy()}
        self._memo=[(q.copy(),(s,feature,safe,self.last))]+list(getattr(self,'_memo',()))[:1]
        return s,feature,safe
    def limit(self,start,end):
        _,feature,safe=self.inspect(start)
        if not safe:raise ValueError('Native post-iteration state has ambiguous/unsupported local side or feature proximity')
        delta=np.asarray(end,float)-np.asarray(start,float);length=np.linalg.norm(delta,axis=1)
        clearance=feature.astype(float)-self.band_cpu-1.e-6
        feature_limit=np.min(np.minimum(1.,.99*clearance/np.maximum(length,1.e-30)))
        self.q0.assign(start);self.q1.assign(end)
        wp.launch(path_limits,dim=self.n,inputs=[self.mesh_id,self.q0,self.q1],outputs=[self.limits,self.hits,self.ray_distance],device=self.device)
        return float(min(feature_limit,self.limits.numpy().min())),int(self.hits.numpy().sum())
    def verify(self,start,end):
        _,feature,safe=self.inspect(start)
        if not safe:return False
        length=np.linalg.norm(np.asarray(end,float)-np.asarray(start,float),axis=1)
        if np.any(feature-length<=self.band_cpu+1.e-6):return False
        self.q0.assign(start);self.q1.assign(end)
        wp.launch(path_limits,dim=self.n,inputs=[self.mesh_id,self.q0,self.q1],outputs=[self.limits,self.hits,self.ray_distance],device=self.device)
        if np.any(self.hits.numpy()):return False
        return self.inspect(end)[2]
