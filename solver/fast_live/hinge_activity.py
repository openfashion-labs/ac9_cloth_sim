# SPDX-License-Identifier: GPL-3.0-or-later
"""Native float32 geometric early-exit predicate, kept separate from force law."""
import warp as wp

@wp.kernel
def classify(q:wp.array[wp.vec3],edges:wp.array2d[int],active:wp.array[int],measures:wp.array[wp.vec3]):
    t=wp.tid();active[t]=0;measures[t]=wp.vec3(0.)
    if edges[t,0]<0 or edges[t,1]<0 or edges[t,2]<0 or edges[t,3]<0:return
    a=q[edges[t,0]];b=q[edges[t,1]];c=q[edges[t,2]];d=q[edges[t,3]]
    l1=wp.length(wp.cross(c-a,d-a));l2=wp.length(wp.cross(d-b,c-b));le=wp.length(d-c)
    measures[t]=wp.vec3(l1,l2,le)
    if l1<1.e-6 or l2<1.e-6 or le<1.e-6:return
    active[t]=1
