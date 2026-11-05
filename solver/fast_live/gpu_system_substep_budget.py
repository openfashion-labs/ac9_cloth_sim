# SPDX-License-Identifier: GPL-3.0-or-later
"""claude_s3_fast_friction copy: logs the true residual; Experimental spring-coupled extension of the bounded fixed-floor GPU system."""
import numpy as np
import os,json
from pathlib import Path
import warp as wp
from warp.sparse import bsr_from_triplets,bsr_get_diag,bsr_mv
from warp.optim.linear import cg
from .gpu_assembly_pants import membrane,hinges,inertia
from .gpu_block_pack import pack_projected
from .gpu_system import GpuSystem,invert
from .spring_terms import assemble as springs
from .bw_projected_matrix_gpu_probe import Projector
from .spring_cpu_terms import validate_springs

class StitchedGpuSystem(GpuSystem):
    def __init__(self,model,bend,device,*,maxiter=3000,tol=1e-7):
        super().__init__(model,bend,device)
        self.model_cpu=model;self.last_projection=None;self.projector=Projector(model,device);self.linear_tol=float(tol);self.last_linear=None;self.linear_maxiter=int(maxiter)
        if not 1e-8<=self.linear_tol<=1e-3:raise ValueError('Out-of-scope linear tolerance')
        self.spring_indices_cpu=model['spring_indices'].copy();self.ns=len(self.spring_indices_cpu)
        self.spring_indices=wp.array(self.spring_indices_cpu,dtype=int,device=device)
        self.rest=wp.zeros(self.ns,dtype=wp.float64,device=device);self.ke=wp.zeros_like(self.rest);self.kd=wp.zeros_like(self.rest)
        self.prev=wp.zeros_like(self.q);self.dummy_force=wp.zeros_like(self.q)
        self.pack_invalid=wp.zeros(1,dtype=int,device=device)
        count=9*self.nt+16*self.ne+4*self.ns+self.n
        self.rows=wp.zeros(count,dtype=int,device=device);self.cols=wp.zeros_like(self.rows);self.values=wp.zeros(count,dtype=wp.mat33d,device=device)
    def solve(self,q,hc,residual,dt,spring_data):
        indices,rest,ke,kd,prev=spring_data
        validate_springs(self.n,indices,rest,ke,kd)
        if not np.array_equal(indices,self.spring_indices_cpu):raise ValueError('Spring topology changed inside fixed model')
        self.q.assign(q);self.contact.assign(hc);self.prev.assign(prev);self.rest.assign(rest);self.ke.assign(ke);self.kd.assign(kd);self.dummy_force.zero_()
        args=[self.rows,self.cols,self.values];so=9*self.nt+16*self.ne
        wp.launch(membrane,dim=self.nt,inputs=[self.q,self.mass,self.tri,self.poses,self.materials,self.areas,dt],outputs=args,device=self.device)
        element,_,self.last_projection=self.projector.assemble_device(q,prev,dt)
        self.pack_invalid.zero_()
        wp.launch(pack_projected,dim=9*self.nt,inputs=[element,self.tri,self.mass],outputs=args+[self.pack_invalid],device=self.device)
        if int(self.pack_invalid.numpy()[0]):raise ValueError('Nonfinite GPU projection')
        wp.launch(hinges,dim=self.ne,inputs=[self.q,self.mass,self.edge,self.length,self.properties,self.bf,dt,9*self.nt],outputs=args,device=self.device)
        wp.launch(springs,dim=self.ns,inputs=[self.q,self.prev,self.mass,self.spring_indices,self.rest,self.ke,self.kd,dt,so],outputs=args+[self.dummy_force],device=self.device)
        wp.launch(inertia,dim=self.n,inputs=[self.mass,self.contact,dt,so+4*self.ns],outputs=args,device=self.device)
        A=bsr_from_triplets(self.n,self.n,self.rows,self.cols,self.values)
        # Compact construction leaves a host upper bound until this readback.
        # Use the stored block count for Warp's SpMV kernel selection, outside capture.
        A.nnz_sync()
        diagonal=bsr_get_diag(A)
        wp.launch(invert,dim=self.n,inputs=[diagonal],outputs=[self.inverse],device=self.device)
        M=bsr_from_triplets(self.n,self.n,self.ids,self.ids,self.inverse)
        r=residual.copy();r[~self.active]=0;self.rhs.assign(r);self.x.zero_()
        if self.prepared is None:self.prepared=cg(A,self.rhs,self.x,M=M,tol=self.linear_tol,atol=1e-12,maxiter=self.linear_maxiter,run=False)
        it,err,tolerance=self.prepared(A=A,b=self.rhs,x=self.x,M=M)
        # claude_s3_fast_friction: the true ||b - A x|| on the same GPU matrix (not the recursive residual)
        if not hasattr(self,'true_r'):self.true_r=wp.zeros_like(self.rhs)
        wp.copy(self.true_r,self.rhs);bsr_mv(A,self.x,self.true_r,alpha=-1.0,beta=1.0);true_residual=float(np.linalg.norm(self.true_r.numpy()))
        self.last_linear={'iterations':int(it),'residual':float(err),'tolerance':float(tolerance),'relative_target':self.linear_tol,'max_iterations':self.linear_maxiter,'true_residual':true_residual,'true_within_tolerance':bool(true_residual<=float(tolerance))}
        if not np.isfinite(err)or not np.isfinite(tolerance)or err>tolerance or true_residual>tolerance:raise RuntimeError(f'Stitched GPU PCG failed: {self.last_linear}')
        solution=self.x.numpy()
        if not np.isfinite(solution).all():raise RuntimeError('Stitched GPU correction nonfinite')
        return solution,int(it),float(err)
