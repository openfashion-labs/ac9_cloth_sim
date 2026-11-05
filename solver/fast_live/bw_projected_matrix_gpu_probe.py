# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated float64 9-DOF GPU tangent / cyclic Jacobi PSD probe.

No Native force or acceptance policy changes. Validate before integration.
"""
import time
import numpy as np
import warp as wp
wp.set_module_options({'enable_backward':False,'max_unroll':0})

M9=wp.types.matrix(shape=(9,9),dtype=wp.float64)
V9=wp.types.vector(length=9,dtype=wp.float64)

@wp.kernel
def projected(q:wp.array[wp.vec3d],prev:wp.array[wp.vec3d],tri:wp.array2d[int],poses:wp.array[wp.mat22d],materials:wp.array2d[wp.float64],areas:wp.array[wp.float64],dt:wp.float64,out:wp.array3d[wp.float64],diagnostics:wp.array2d[wp.float64]):
    tid=wp.tid()
    p=poses[tid]
    u=(q[tri[tid,1]]-q[tri[tid,0]])*p[0,0]+(q[tri[tid,2]]-q[tri[tid,0]])*p[1,0]
    v=(q[tri[tid,1]]-q[tri[tid,0]])*p[0,1]+(q[tri[tid,2]]-q[tri[tid,0]])*p[1,1]
    up=(prev[tri[tid,1]]-prev[tri[tid,0]])*p[0,0]+(prev[tri[tid,2]]-prev[tri[tid,0]])*p[1,0]
    vp=(prev[tri[tid,1]]-prev[tri[tid,0]])*p[0,1]+(prev[tri[tid,2]]-prev[tri[tid,0]])*p[1,1]
    a=wp.vec3d(-p[0,0]-p[1,0],p[0,0],p[1,0])
    b=wp.vec3d(-p[0,1]-p[1,1],p[0,1],p[1,1])
    lu=wp.length(u);lv=wp.length(v)
    ku=materials[tid,0];kv=materials[tid,1];d=materials[tid,2];ks=materials[tid,3]
    d00=wp.dot(u,u)-wp.dot(up,up);d01=wp.dot(u,v)-wp.dot(up,vp);d11=wp.dot(v,v)-wp.dot(vp,vp)
    h=M9(wp.float64(0));vectors=M9(wp.float64(0))
    for i in range(9):
        vectors[i,i]=wp.float64(1)
        vi=i/3;ci=i%3
        gui=a[vi]*u[ci];gvi=b[vi]*v[ci];gsi=a[vi]*v[ci]+b[vi]*u[ci]
        for j in range(9):
            vj=j/3;cj=j%3
            guj=a[vj]*u[cj];gvj=b[vj]*v[cj];gsj=a[vj]*v[cj]+b[vj]*u[cj]
            ident=wp.float64(0)
            if ci==cj:ident=wp.float64(1)
            aa=a[vi]*a[vj];bb=b[vi]*b[vj];ab=a[vi]*b[vj]+b[vi]*a[vj]
            g=(ku/(lu*lu)+wp.float64(4)*d/dt)*gui*guj+(kv/(lv*lv)+wp.float64(4)*d/dt)*gvi*gvj+(ks+wp.float64(2)*d/dt)*gsi*gsj
            geo=aa*ku*(lu-wp.float64(1))/lu*(ident-u[ci]*u[cj]/(lu*lu))+bb*kv*(lv-wp.float64(1))/lv*(ident-v[ci]*v[cj]/(lv*lv))
            scalar=ks*wp.dot(u,v)*ab+wp.float64(2)*d/dt*(d00*aa+d01*ab+d11*bb)
            h[i,j]=(g+geo+scalar*ident)*areas[tid]
    for i in range(9):
        for j in range(i+1,9):
            z=(h[i,j]+h[j,i])*wp.float64(.5);h[i,j]=z;h[j,i]=z
    # Cyclic Jacobi on the original nine-component symmetric matrix.
    for sweep in range(20):
        for i in range(8):
            for j in range(i+1,9):
                off=h[i,j]
                if wp.abs(off)>wp.float64(1e-18):
                    tau=(h[j,j]-h[i,i])/(wp.float64(2)*off)
                    sign=wp.float64(1)
                    if tau<wp.float64(0):sign=wp.float64(-1)
                    tangent=sign/(wp.abs(tau)+wp.sqrt(wp.float64(1)+tau*tau))
                    c=wp.float64(1)/wp.sqrt(wp.float64(1)+tangent*tangent);s=tangent*c
                    oldi=h[i,i];oldj=h[j,j]
                    h[i,i]=oldi-tangent*off;h[j,j]=oldj+tangent*off;h[i,j]=wp.float64(0);h[j,i]=wp.float64(0)
                    for k in range(9):
                        if k!=i and k!=j:
                            hi=h[k,i];hj=h[k,j]
                            h[k,i]=c*hi-s*hj;h[i,k]=h[k,i];h[k,j]=s*hi+c*hj;h[j,k]=h[k,j]
                        ei=vectors[k,i];ej=vectors[k,j]
                        vectors[k,i]=c*ei-s*ej;vectors[k,j]=s*ei+c*ej
    psd=M9(wp.float64(0));minimum=h[0,0];negative=wp.float64(0);offmax=wp.float64(0)
    for k in range(9):
        minimum=wp.min(minimum,h[k,k])
        if h[k,k]<wp.float64(-1e-8):negative+=wp.float64(1)
        for i in range(9):
            for j in range(9):
                psd[i,j]+=vectors[i,k]*wp.max(h[k,k],wp.float64(0))*vectors[j,k]
                if i!=j:offmax=wp.max(offmax,wp.abs(h[i,j]))
    # Remove translations using the same P H P projection as CPU.
    left=M9(wp.float64(0))
    for i in range(9):
        for j in range(9):
            left[i,j]=psd[i,j]-(psd[i%3,j]+psd[i%3+3,j]+psd[i%3+6,j])/wp.float64(3)
    for i in range(9):
        for j in range(9):
            out[tid,i,j]=left[i,j]-(left[i,j%3]+left[i,j%3+3]+left[i,j%3+6])/wp.float64(3)
    diagnostics[tid,0]=minimum;diagnostics[tid,1]=negative;diagnostics[tid,2]=offmax

class Projector:
    def __init__(self,model,device='cuda:0'):
        self.device=device;self.nt=len(model['tri']);self.n=len(model['mass'])
        self.tri=wp.array(model['tri'],dtype=int,device=device)
        self.poses=wp.array(np.asarray(model['poses'],float),dtype=wp.mat22d,device=device)
        self.materials=wp.array(np.asarray(model['materials'],float),dtype=wp.float64,device=device)
        self.areas=wp.array(np.asarray(model['areas'],float),dtype=wp.float64,device=device)
        self.q=wp.zeros(self.n,dtype=wp.vec3d,device=device);self.prev=wp.zeros_like(self.q)
        self.output=wp.zeros((self.nt,9,9),dtype=wp.float64,device=device)
        self.diagnostics=wp.zeros((self.nt,3),dtype=wp.float64,device=device)
    def assemble(self,q,prev,dt):
        tic=time.perf_counter();self.q.assign(np.asarray(q,float));self.prev.assign(np.asarray(prev,float))
        wp.launch(projected,self.nt,[self.q,self.prev,self.tri,self.poses,self.materials,self.areas,dt,self.output,self.diagnostics],device=self.device)
        result=self.output.numpy();diag=self.diagnostics.numpy()
        if not np.isfinite(result).all():raise ValueError('Nonfinite GPU projection')
        return result,None,{'seconds':time.perf_counter()-tic,'element_eigenvalue_min_N_m':float(diag[:,0].min()),'negative_element_eigenvalues':int(diag[:,1].sum()),'jacobi_max_off_diagonal':float(diag[:,2].max())}

    def assemble_device(self,q,prev,dt):
        tic=time.perf_counter();self.q.assign(np.asarray(q,float));self.prev.assign(np.asarray(prev,float))
        wp.launch(projected,self.nt,[self.q,self.prev,self.tri,self.poses,self.materials,self.areas,dt,self.output,self.diagnostics],device=self.device)
        diag=self.diagnostics.numpy()
        # The consuming pack validates every value before sparse assembly.
        return self.output,None,{'seconds':time.perf_counter()-tic,'element_eigenvalue_min_N_m':float(diag[:,0].min()),'negative_element_eigenvalues':int(diag[:,1].sum()),'jacobi_max_off_diagonal':float(diag[:,2].max())}
