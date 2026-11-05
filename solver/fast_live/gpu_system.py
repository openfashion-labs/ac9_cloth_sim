# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned GPU membrane/hinge GN solve with a native vertex-contact diagonal."""
import numpy as np
import warp as wp
from warp.sparse import bsr_from_triplets,bsr_get_diag
from warp.optim.linear import cg
from .gpu_assembly import membrane,hinges,inertia

@wp.kernel
def invert(block:wp.array[wp.mat33d],output:wp.array[wp.mat33d]):
    i=wp.tid();output[i]=wp.inverse(block[i])

class GpuSystem:
    def __init__(self,model,bend,device):
        self.device=device;self.n=len(model['mass']);self.nt=len(model['tri']);self.ne=len(bend['edge'])
        def arr(v,dtype):return wp.array(v,dtype=dtype,device=device)
        self.mass=arr(model['mass'],wp.float64);self.active=model['mass']>0
        self.tri=arr(model['tri'],int);self.poses=arr(model['poses'],wp.mat22d);self.materials=arr(model['materials'],wp.float64);self.areas=arr(model['areas'],wp.float64)
        self.edge=arr(bend['edge'],int);self.length=arr(bend['length'],wp.float64);self.properties=arr(bend['bending'],wp.float64)
        self.bf=arr(bend['bf']if len(bend['bf'])else np.zeros((self.ne,3)),wp.float64)
        count=9*self.nt+16*self.ne+self.n
        self.rows=wp.zeros(count,dtype=int,device=device);self.cols=wp.zeros_like(self.rows);self.values=wp.zeros(count,dtype=wp.mat33d,device=device)
        self.q=wp.zeros(self.n,dtype=wp.vec3d,device=device);self.contact=wp.zeros(self.n,dtype=wp.mat33d,device=device)
        self.rhs=wp.zeros_like(self.q);self.x=wp.zeros_like(self.q);self.inverse=wp.zeros(self.n,dtype=wp.mat33d,device=device)
        self.ids=arr(np.arange(self.n,dtype=np.int32),int);self.prepared=None
    def solve(self,q,hc,residual,dt):
        self.q.assign(q);self.contact.assign(hc)
        args=[self.rows,self.cols,self.values]
        wp.launch(membrane,dim=self.nt,inputs=[self.q,self.mass,self.tri,self.poses,self.materials,self.areas,dt],outputs=args,device=self.device)
        wp.launch(hinges,dim=self.ne,inputs=[self.q,self.mass,self.edge,self.length,self.properties,self.bf,dt,9*self.nt],outputs=args,device=self.device)
        wp.launch(inertia,dim=self.n,inputs=[self.mass,self.contact,dt,9*self.nt+16*self.ne],outputs=args,device=self.device)
        A=bsr_from_triplets(self.n,self.n,self.rows,self.cols,self.values)
        diag=bsr_get_diag(A);wp.launch(invert,dim=self.n,inputs=[diag],outputs=[self.inverse],device=self.device)
        M=bsr_from_triplets(self.n,self.n,self.ids,self.ids,self.inverse)
        r=residual.copy();r[~self.active]=0;self.rhs.assign(r);self.x.zero_()
        if self.prepared is None:self.prepared=cg(A,self.rhs,self.x,M=M,tol=1e-7,atol=1e-12,maxiter=1000,run=False)
        it,err,tolerance=self.prepared(A=A,b=self.rhs,x=self.x,M=M)
        if not np.isfinite(err)or not np.isfinite(tolerance)or tolerance<0 or err>tolerance:raise RuntimeError('GPU PCG did not converge to a finite residual')
        solution=self.x.numpy()
        if not np.isfinite(solution).all():raise RuntimeError('GPU PCG returned a nonfinite correction')
        return solution,int(it),float(err)
