# SPDX-License-Identifier: GPL-3.0-or-later
import warp as wp
from newton._src.solvers.vbd.particle_vbd_kernels import evaluate_spring_force_and_hessian_both_vertices as evaluate

@wp.kernel
def spring_force(dt:float,q:wp.array[wp.vec3],prev:wp.array[wp.vec3],indices:wp.array[int],rest:wp.array[float],ke:wp.array[float],kd:wp.array[float],force:wp.array[wp.vec3]):
    s=wp.tid();a,b,fa,fb,h=evaluate(s,dt,q,prev,indices,rest,ke,kd)
    wp.atomic_add(force,a,fa);wp.atomic_add(force,b,fb)
