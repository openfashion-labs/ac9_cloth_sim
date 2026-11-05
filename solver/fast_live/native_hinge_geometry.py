# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only float32 angle/early-exit chart matching Native bending geometry."""
import warp as wp

@wp.kernel
def angles(q:wp.array[wp.vec3],edge:wp.array2d[int],theta:wp.array[float],active:wp.array[int]):
    t=wp.tid();theta[t]=0.;active[t]=0
    if edge[t,0]<0 or edge[t,1]<0 or edge[t,2]<0 or edge[t,3]<0:return
    a=q[edge[t,0]];b=q[edge[t,1]];c=q[edge[t,2]];d=q[edge[t,3]]
    n1=wp.cross(c-a,d-a);n2=wp.cross(d-b,c-b);e=d-c
    l1=wp.length(n1);l2=wp.length(n2);le=wp.length(e)
    if l1<1.e-6 or l2<1.e-6 or le<1.e-6:return
    n1=n1/l1;n2=n2/l2;e=e/le
    theta[t]=wp.atan2(wp.dot(wp.cross(n1,n2),e),wp.dot(n1,n2));active[t]=1
