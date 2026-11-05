# SPDX-License-Identifier: GPL-3.0-or-later
"""Full two-endpoint spring GN/PSD coupling, preserving the native force law.

Compression's negative transverse geometric stiffness is projected to zero in
the solve matrix only. The force keeps the native length clamp and damping.
"""
import warp as wp

@wp.kernel
def assemble(q:wp.array[wp.vec3d],prev:wp.array[wp.vec3d],mass:wp.array[wp.float64],indices:wp.array2d[int],rest:wp.array[wp.float64],stiffness:wp.array[wp.float64],damping:wp.array[wp.float64],dt:wp.float64,offset:int,rows:wp.array[int],cols:wp.array[int],values:wp.array[wp.mat33d],force:wp.array[wp.vec3d]):
    s=wp.tid();a=indices[s,0];b=indices[s,1]
    diff=q[a]-q[b];length=wp.max(wp.length(diff),wp.float64(1e-8));n=diff/length
    old_length=wp.max(wp.length(prev[a]-prev[b]),wp.float64(1e-8))
    f=stiffness[s]*(rest[s]-length)/length*diff-n*(damping[s]/dt*(length-old_length))
    if mass[a]>0:wp.atomic_add(force,a,f)
    if mass[b]>0:wp.atomic_sub(force,b,f)
    outer=wp.outer(n,n);identity=wp.identity(n=3,dtype=wp.float64)
    tangent=wp.max(stiffness[s]*(wp.float64(1)-rest[s]/length),wp.float64(0))
    K=(stiffness[s]+damping[s]/dt)*outer+tangent*(identity-outer)
    for i in range(2):
        for j in range(2):
            r=indices[s,i];c=indices[s,j];at=offset+s*4+i*2+j;rows[at]=r;cols[at]=c
            h=wp.mat33d(wp.float64(0))
            if mass[r]>0 and mass[c]>0:
                sign=wp.float64(1)
                if i!=j:sign=wp.float64(-1)
                h=sign*K
            values[at]=h
