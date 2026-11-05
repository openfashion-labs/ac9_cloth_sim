# SPDX-License-Identifier: GPL-3.0-or-later
"""Point-path guards for one fixed identity-transformed triangle mesh.

This is not cloth-face CCD. Meshes and particles use world-space meters.
"""
import warp as wp

@wp.kernel
def signed_distance(mesh:wp.uint64,q:wp.array[wp.vec3],distance:wp.array[float],valid:wp.array[int]):
    i=wp.tid();query=wp.mesh_query_point_sign_normal(mesh,q[i],1.e6)
    valid[i]=0;distance[i]=-1.e6
    if query.result:
        point=wp.mesh_eval_position(mesh,query.face,query.u,query.v)
        distance[i]=query.sign*wp.length(q[i]-point);valid[i]=1

@wp.kernel
def signed_distance_native_mode(mesh:wp.uint64,q:wp.array[wp.vec3],parity:int,distance:wp.array[float],valid:wp.array[int]):
    i=wp.tid()
    query=wp.mesh_query_point_sign_normal(mesh,q[i],1.e6)
    if parity!=0:
        query=wp.mesh_query_point_sign_parity(mesh,q[i],1.e6)
    valid[i]=0;distance[i]=-1.e6
    if query.result:
        point=wp.mesh_eval_position(mesh,query.face,query.u,query.v)
        distance[i]=query.sign*wp.length(q[i]-point);valid[i]=1

@wp.kernel
def path_limits(mesh:wp.uint64,start:wp.array[wp.vec3],end:wp.array[wp.vec3],limits:wp.array[float],hits:wp.array[int],distance:wp.array[float]):
    i=wp.tid();delta=end[i]-start[i];length=wp.length(delta)
    limits[i]=1.;hits[i]=0;distance[i]=length
    if length>1.e-8:
        ray=wp.mesh_query_ray(mesh,start[i],delta/length,length)
        if ray.result:
            hits[i]=1;distance[i]=ray.t
            limits[i]=wp.max(0.,wp.min(1.,.99*wp.max(0.,ray.t-1.e-6)/length))
