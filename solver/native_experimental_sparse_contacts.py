# SPDX-FileCopyrightText: Copyright (c) 2025 The Newton Developers
# SPDX-License-Identifier: Apache-2.0
# Modified by AC9: Newton 1.6.0's self-contact and planar truncation kernels (particle_vbd_kernels.py) run from
# per-color queues of primitives that have contacts; the contact functions are Newton's (with AC9's layer patch).
"""Experimental sparse scheduling. The original contact function is retained."""
import warp as wp
from native_experimental_contacts import SelfArgs, SelfArgs_func
from newton._src.solvers.vbd.particle_vbd_kernels import (TriMeshCollisionInfo, get_edge_colliding_edges_count, get_vertex_colliding_triangles_count, NUM_THREADS_PER_COLLISION_PRIMITIVE)

@wp.func
def add_color(mask: wp.uint32, color: int):
    if color < 0:
        return wp.uint32(0xFFFFFFFF)
    return mask | (wp.uint32(1) << wp.uint32(color))

@wp.kernel(enable_backward=False)
def build(info_array:wp.array[TriMeshCollisionInfo],tri:wp.array2d[int],edge:wp.array2d[int],colors:wp.array[int],ncolors:int,capacity:int,indices:wp.array[int],counts:wp.array[int]):
    primitive=wp.tid();info=info_array[0];mask=wp.uint32(0)
    if primitive<info.edge_colliding_edges_buffer_sizes.shape[0]:
        if get_edge_colliding_edges_count(info,primitive)>0:
            mask=add_color(mask,colors[edge[primitive,2]])
            mask=add_color(mask,colors[edge[primitive,3]])
    if primitive<info.vertex_colliding_triangles_buffer_sizes.shape[0]:
        n=get_vertex_colliding_triangles_count(info,primitive)
        if n>0:
            own=colors[primitive]
            offset=info.vertex_colliding_triangles_offsets[primitive]
            for i in range(n):
                face=info.vertex_colliding_triangles[(offset+i)*2+1]
                if face>=0:
                    mask=add_color(mask,own)
                    mask=add_color(mask,colors[tri[face,0]])
                    mask=add_color(mask,colors[tri[face,1]])
                    mask=add_color(mask,colors[tri[face,2]])
    for color in range(ncolors):
        if mask & (wp.uint32(1)<<wp.uint32(color)):
            slot=wp.atomic_add(counts,color,1)
            indices[color*capacity+slot]=primitive
    if mask:
        slot=wp.atomic_add(counts,ncolors,1)
        indices[ncolors*capacity+slot]=primitive

@wp.kernel(enable_backward=False)
def accumulate(args:SelfArgs,indices:wp.array[int],counts:wp.array[int],capacity:int,stride:int):
    task=wp.tid();count=counts[args.current_color]
    while task<count*NUM_THREADS_PER_COLLISION_PRIMITIVE:
        primitive=indices[args.current_color*capacity+task//NUM_THREADS_PER_COLLISION_PRIMITIVE]
        original_tid=primitive*NUM_THREADS_PER_COLLISION_PRIMITIVE+task%NUM_THREADS_PER_COLLISION_PRIMITIVE
        SelfArgs_func(args.dt, args.current_color, args.pos_prev, args.pos, args.particle_colors, args.tri_indices, args.edge_indices, args.collision_info_array, args.collision_radius, args.soft_contact_ke, args.soft_contact_kd, args.friction_mu, args.friction_epsilon, args.edge_edge_parallel_epsilon, args.ac9_layer, args.ac9_tri_sign, args.ac9_wrong, args.ac9_pairs, args.particle_forces, args.particle_hessians,original_tid)
        task+=stride

class SparseContacts:
    def __init__(self,solver):
        self.solver=solver;self.model=solver.model;self.device=solver.device
        self.ncolors=len(self.model.particle_color_groups)
        if not 0<self.ncolors<=32:raise ValueError('Sparse color queues require 1..32 colors')
        if self.model.particle_colors.numpy().max(initial=-1)>=self.ncolors:raise ValueError('Particle color exceeds group count')
        self.capacity=max(self.model.particle_count,self.model.edge_count)
        self.indices=wp.zeros(self.capacity*(self.ncolors+1),dtype=int,device=self.device)
        self.counts=wp.zeros(self.ncolors+1,dtype=int,device=self.device)
        self.stride=self.device.sm_count*128
        self.enable_planar=False
        self.fields=['dt', 'current_color', 'pos_prev', 'pos', 'particle_colors', 'tri_indices', 'edge_indices', 'collision_info_array', 'collision_radius', 'soft_contact_ke', 'soft_contact_kd', 'friction_mu', 'friction_epsilon', 'edge_edge_parallel_epsilon', 'ac9_layer', 'ac9_tri_sign', 'ac9_wrong', 'ac9_pairs', 'particle_forces', 'particle_hessians']
    def rebuild(self):
        self.counts.zero_()
        wp.launch(build,dim=self.capacity,inputs=[self.solver.trimesh_collision_info,self.model.tri_indices,self.model.edge_indices,self.model.particle_colors,self.ncolors,self.capacity,self.indices,self.counts],device=self.device)
    def launch(self,**kwargs):
        if kwargs['kernel'].key!='accumulate_self_contact_force_and_hessian':return wp.launch(**kwargs)
        observer=getattr(self.solver,'ac9_native_capture_observer',None)
        if observer is not None:observer(**kwargs)
        data=SelfArgs()
        values=kwargs['inputs']+kwargs['outputs']
        assert len(values)==len(self.fields)
        for name,value in zip(self.fields,values):setattr(data,name,value)
        wp.launch(accumulate,dim=self.stride,block_dim=128,inputs=[data,self.indices,self.counts,self.capacity,self.stride],device=self.device)

    def launch_planar(self,kernel=None,**kwargs):
        assert kernel.key=='apply_planar_truncation_parallel_by_collision'
        data=PlanarArgs();values=kwargs['inputs']+kwargs['outputs']
        fields=['pos', 'displacement_in', 'tri_indices', 'edge_indices', 'collision_info_array', 'parallel_eps', 'gamma', 'ac9_layer', 'ac9_tri_sign', 'ac9_wrong', 'ac9_pairs', 'truncation_t_out']
        assert len(values)==len(fields)
        for name,value in zip(fields,values):setattr(data,name,value)
        wp.launch(accumulate_planar,dim=self.stride,block_dim=128,inputs=[data,self.indices,self.counts,self.capacity,self.stride,self.ncolors],device=self.device)

import warp as wp
from newton._src.solvers.vbd.particle_vbd_kernels import AC9LayerPairs, NUM_THREADS_PER_COLLISION_PRIMITIVE, TriMeshCollisionInfo, _eval_body_particle_contact, _eval_soft_ef_contact, ac9_layer_cross_vt, ac9_layer_dir, ac9_layer_ee_free, ac9_layer_vt_contact_4_vertices, evaluate_edge_edge_contact_2_vertices, evaluate_spring_force_and_hessian_both_vertices, evaluate_vertex_triangle_collision_force_hessian_4_vertices, get_edge_colliding_edges_count, get_vertex_colliding_triangles_count
@wp.func
def count_self_func(dt: float, current_color: int, pos_prev: wp.array[wp.vec3], pos: wp.array[wp.vec3], particle_colors: wp.array[int], tri_indices: wp.array2d[wp.int32], edge_indices: wp.array2d[wp.int32], collision_info_array: wp.array[TriMeshCollisionInfo], collision_radius: float, soft_contact_ke: float, soft_contact_kd: float, friction_mu: float, friction_epsilon: float, edge_edge_parallel_epsilon: float, ac9_layer: wp.array[int], ac9_tri_sign: wp.array[float], ac9_wrong: wp.array[int], ac9_pairs: AC9LayerPairs, particle_forces: wp.array[wp.vec3], particle_hessians: wp.array[wp.mat33], emission_counts: wp.array[int], thread_index: int):
    t_id = thread_index
    collision_info = collision_info_array[0]
    use_layers = ac9_layer.shape[0] > 0
    primitive_id = t_id // NUM_THREADS_PER_COLLISION_PRIMITIVE
    t_id_current_primitive = t_id % NUM_THREADS_PER_COLLISION_PRIMITIVE
    if primitive_id < collision_info.edge_colliding_edges_buffer_sizes.shape[0]:
        e1_idx = primitive_id
        collision_buffer_counter = t_id_current_primitive
        collision_buffer_offset = collision_info.edge_colliding_edges_offsets[primitive_id]
        collision_count = get_edge_colliding_edges_count(collision_info, primitive_id)
        while collision_buffer_counter < collision_count:
            e2_idx = collision_info.edge_colliding_edges[2 * (collision_buffer_offset + collision_buffer_counter) + 1]
            if e1_idx != -1 and e2_idx != -1:
                e1_v1 = edge_indices[e1_idx, 2]
                e1_v2 = edge_indices[e1_idx, 3]
                c_e1_v1 = particle_colors[e1_v1]
                c_e1_v2 = particle_colors[e1_v2]
                if (c_e1_v1 == current_color or c_e1_v1 < 0 or c_e1_v2 == current_color or (c_e1_v2 < 0)) and (not ac9_layer_ee_free(e1_idx, e2_idx, edge_indices, ac9_layer, ac9_wrong, pos, tri_indices, ac9_tri_sign, ac9_pairs)):
                    has_contact, collision_force_0, collision_force_1, collision_hessian_0, collision_hessian_1 = evaluate_edge_edge_contact_2_vertices(e1_idx, e2_idx, pos, pos_prev, edge_indices, collision_radius, soft_contact_ke, soft_contact_kd, friction_mu, friction_epsilon, dt, edge_edge_parallel_epsilon)
                    if has_contact:
                        if c_e1_v1 == current_color or c_e1_v1 < 0:
                            wp.atomic_add(emission_counts, e1_v1, 1)
                            pass
                        if c_e1_v2 == current_color or c_e1_v2 < 0:
                            wp.atomic_add(emission_counts, e1_v2, 1)
                            pass
            collision_buffer_counter += NUM_THREADS_PER_COLLISION_PRIMITIVE
    if primitive_id < collision_info.vertex_colliding_triangles_buffer_sizes.shape[0]:
        particle_idx = primitive_id
        collision_buffer_counter = t_id_current_primitive
        collision_buffer_offset = collision_info.vertex_colliding_triangles_offsets[primitive_id]
        collision_count = get_vertex_colliding_triangles_count(collision_info, primitive_id)
        while collision_buffer_counter < collision_count:
            tri_idx = collision_info.vertex_colliding_triangles[(collision_buffer_offset + collision_buffer_counter) * 2 + 1]
            if particle_idx != -1 and tri_idx != -1:
                tri_a = tri_indices[tri_idx, 0]
                tri_b = tri_indices[tri_idx, 1]
                tri_c = tri_indices[tri_idx, 2]
                c_v = particle_colors[particle_idx]
                c_tri_a = particle_colors[tri_a]
                c_tri_b = particle_colors[tri_b]
                c_tri_c = particle_colors[tri_c]
                skip_cross = bool(False)
                if use_layers:
                    skip_cross = ac9_layer_cross_vt(particle_idx, tri_idx, pos, tri_indices, ac9_layer, ac9_tri_sign, ac9_pairs)
                if not skip_cross and (c_v == current_color or c_v < 0 or c_tri_a == current_color or (c_tri_a < 0) or (c_tri_b == current_color) or (c_tri_b < 0) or (c_tri_c == current_color) or (c_tri_c < 0)):
                    cross = int(0)
                    n_dir = wp.vec3(0.0)
                    if use_layers:
                        cross, n_dir = ac9_layer_dir(particle_idx, tri_idx, pos, tri_indices, ac9_layer, ac9_tri_sign, ac9_pairs)
                    if cross != 0:
                        has_contact, collision_force_0, collision_force_1, collision_force_2, collision_force_3, collision_hessian_0, collision_hessian_1, collision_hessian_2, collision_hessian_3 = ac9_layer_vt_contact_4_vertices(particle_idx, tri_idx, n_dir, pos, pos_prev, tri_indices, collision_radius, soft_contact_ke, soft_contact_kd, friction_mu, friction_epsilon, dt)
                    else:
                        has_contact, collision_force_0, collision_force_1, collision_force_2, collision_force_3, collision_hessian_0, collision_hessian_1, collision_hessian_2, collision_hessian_3 = evaluate_vertex_triangle_collision_force_hessian_4_vertices(particle_idx, tri_idx, pos, pos_prev, tri_indices, collision_radius, soft_contact_ke, soft_contact_kd, friction_mu, friction_epsilon, dt)
                    if has_contact:
                        if c_v == current_color or c_v < 0:
                            wp.atomic_add(emission_counts, particle_idx, 1)
                            pass
                        if c_tri_a == current_color or c_tri_a < 0:
                            wp.atomic_add(emission_counts, tri_a, 1)
                            pass
                        if c_tri_b == current_color or c_tri_b < 0:
                            wp.atomic_add(emission_counts, tri_b, 1)
                            pass
                        if c_tri_c == current_color or c_tri_c < 0:
                            wp.atomic_add(emission_counts, tri_c, 1)
                            pass
            collision_buffer_counter += NUM_THREADS_PER_COLLISION_PRIMITIVE
@wp.kernel(enable_backward=False)
def count_accumulate(args:SelfArgs,indices:wp.array[int],counts:wp.array[int],capacity:int,stride:int,emission_counts:wp.array[int]):
    task=wp.tid();count=counts[args.current_color]
    while task<count*NUM_THREADS_PER_COLLISION_PRIMITIVE:
        primitive=indices[args.current_color*capacity+task//NUM_THREADS_PER_COLLISION_PRIMITIVE]
        original_tid=primitive*NUM_THREADS_PER_COLLISION_PRIMITIVE+task%NUM_THREADS_PER_COLLISION_PRIMITIVE
        count_self_func(args.dt, args.current_color, args.pos_prev, args.pos, args.particle_colors, args.tri_indices, args.edge_indices, args.collision_info_array, args.collision_radius, args.soft_contact_ke, args.soft_contact_kd, args.friction_mu, args.friction_epsilon, args.edge_edge_parallel_epsilon, args.ac9_layer, args.ac9_tri_sign, args.ac9_wrong, args.ac9_pairs, args.particle_forces, args.particle_hessians,emission_counts,original_tid)
        task+=stride


from newton._src.solvers.vbd.particle_vbd_kernels import AC9LayerPairs, NUM_THREADS_PER_COLLISION_PRIMITIVE, TriMeshCollisionInfo, ac9_layer_cross_vt, ac9_layer_dir, ac9_layer_ee_free, create_edge_edge_division_plane_closest_pt, create_vertex_triangle_division_plane_closest_pt, get_edge_colliding_edges_count, get_vertex_colliding_triangles_count, planar_truncation_t, triangle_closest_point

@wp.struct
class PlanarArgs:
    pos: wp.array[wp.vec3]
    displacement_in: wp.array[wp.vec3]
    tri_indices: wp.array2d[wp.int32]
    edge_indices: wp.array2d[wp.int32]
    collision_info_array: wp.array[TriMeshCollisionInfo]
    parallel_eps: float
    gamma: float
    ac9_layer: wp.array[int]
    ac9_tri_sign: wp.array[float]
    ac9_wrong: wp.array[int]
    ac9_pairs: AC9LayerPairs
    truncation_t_out: wp.array[float]
@wp.func
def PlanarArgs_func(pos: wp.array[wp.vec3], displacement_in: wp.array[wp.vec3], tri_indices: wp.array2d[wp.int32], edge_indices: wp.array2d[wp.int32], collision_info_array: wp.array[TriMeshCollisionInfo], parallel_eps: float, gamma: float, ac9_layer: wp.array[int], ac9_tri_sign: wp.array[float], ac9_wrong: wp.array[int], ac9_pairs: AC9LayerPairs, truncation_t_out: wp.array[float], thread_index: int):
    t_id = thread_index
    collision_info = collision_info_array[0]
    use_layers = ac9_layer.shape[0] > 0
    primitive_id = t_id // NUM_THREADS_PER_COLLISION_PRIMITIVE
    t_id_current_primitive = t_id % NUM_THREADS_PER_COLLISION_PRIMITIVE
    if primitive_id < collision_info.edge_colliding_edges_buffer_sizes.shape[0]:
        e1_idx = primitive_id
        collision_buffer_counter = t_id_current_primitive
        collision_buffer_offset = collision_info.edge_colliding_edges_offsets[primitive_id]
        collision_count = get_edge_colliding_edges_count(collision_info, primitive_id)
        while collision_buffer_counter < collision_count:
            e2_idx = collision_info.edge_colliding_edges[2 * (collision_buffer_offset + collision_buffer_counter) + 1]
            if e1_idx != -1 and e2_idx != -1 and (not ac9_layer_ee_free(e1_idx, e2_idx, edge_indices, ac9_layer, ac9_wrong, pos, tri_indices, ac9_tri_sign, ac9_pairs)):
                e1_v1 = edge_indices[e1_idx, 2]
                e1_v2 = edge_indices[e1_idx, 3]
                e1_v1_pos = pos[e1_v1]
                e1_v2_pos = pos[e1_v2]
                delta_e1_v1 = displacement_in[e1_v1]
                delta_e1_v2 = displacement_in[e1_v2]
                e2_v1 = edge_indices[e2_idx, 2]
                e2_v2 = edge_indices[e2_idx, 3]
                e2_v1_pos = pos[e2_v1]
                e2_v2_pos = pos[e2_v2]
                delta_e2_v1 = displacement_in[e2_v1]
                delta_e2_v2 = displacement_in[e2_v2]
                is_dummy, n, d = create_edge_edge_division_plane_closest_pt(e1_v1_pos, delta_e1_v1, e1_v2_pos, delta_e1_v2, e2_v1_pos, delta_e2_v1, e2_v2_pos, delta_e2_v2)
                if not is_dummy[0]:
                    t = planar_truncation_t(e1_v1_pos, delta_e1_v1, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, e1_v1, t)
                if not is_dummy[1]:
                    t = planar_truncation_t(e1_v2_pos, delta_e1_v2, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, e1_v2, t)
                if not is_dummy[2]:
                    t = planar_truncation_t(e2_v1_pos, delta_e2_v1, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, e2_v1, t)
                if not is_dummy[3]:
                    t = planar_truncation_t(e2_v2_pos, delta_e2_v2, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, e2_v2, t)
            collision_buffer_counter += NUM_THREADS_PER_COLLISION_PRIMITIVE
    if primitive_id < collision_info.vertex_colliding_triangles_buffer_sizes.shape[0]:
        particle_idx = primitive_id
        colliding_particle_pos = pos[particle_idx]
        colliding_particle_displacement = displacement_in[particle_idx]
        collision_buffer_counter = t_id_current_primitive
        collision_buffer_offset = collision_info.vertex_colliding_triangles_offsets[primitive_id]
        collision_count = get_vertex_colliding_triangles_count(collision_info, primitive_id)
        while collision_buffer_counter < collision_count:
            tri_idx = collision_info.vertex_colliding_triangles[(collision_buffer_offset + collision_buffer_counter) * 2 + 1]
            wrong_side = False
            if use_layers and particle_idx != -1 and (tri_idx != -1):
                wrong_side = ac9_layer_cross_vt(particle_idx, tri_idx, pos, tri_indices, ac9_layer, ac9_tri_sign, ac9_pairs)
                cross, n_dir = ac9_layer_dir(particle_idx, tri_idx, pos, tri_indices, ac9_layer, ac9_tri_sign, ac9_pairs)
                if cross != 0:
                    cp, _bary, _ft = triangle_closest_point(pos[tri_indices[tri_idx, 0]], pos[tri_indices[tri_idx, 1]], pos[tri_indices[tri_idx, 2]], colliding_particle_pos)
                    wrong_side = wrong_side or wp.dot(colliding_particle_pos - cp, n_dir) < 0.0
            if particle_idx != -1 and tri_idx != -1 and (not wrong_side):
                tri_a = tri_indices[tri_idx, 0]
                tri_b = tri_indices[tri_idx, 1]
                tri_c = tri_indices[tri_idx, 2]
                t1 = pos[tri_a]
                t2 = pos[tri_b]
                t3 = pos[tri_c]
                delta_t1 = displacement_in[tri_a]
                delta_t2 = displacement_in[tri_b]
                delta_t3 = displacement_in[tri_c]
                is_dummy, n, d = create_vertex_triangle_division_plane_closest_pt(colliding_particle_pos, colliding_particle_displacement, t1, delta_t1, t2, delta_t2, t3, delta_t3)
                if not is_dummy[0]:
                    t = planar_truncation_t(colliding_particle_pos, colliding_particle_displacement, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, particle_idx, t)
                if not is_dummy[1]:
                    t = planar_truncation_t(t1, delta_t1, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, tri_a, t)
                if not is_dummy[2]:
                    t = planar_truncation_t(t2, delta_t2, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, tri_b, t)
                if not is_dummy[3]:
                    t = planar_truncation_t(t3, delta_t3, n, d, parallel_eps, gamma)
                    wp.atomic_min(truncation_t_out, tri_c, t)
            collision_buffer_counter += NUM_THREADS_PER_COLLISION_PRIMITIVE

@wp.kernel(enable_backward=False)
def accumulate_planar(args:PlanarArgs,indices:wp.array[int],counts:wp.array[int],capacity:int,stride:int,all_slot:int):
    task=wp.tid();count=counts[all_slot]
    while task<count*NUM_THREADS_PER_COLLISION_PRIMITIVE:
        primitive=indices[all_slot*capacity+task//NUM_THREADS_PER_COLLISION_PRIMITIVE]
        original_tid=primitive*NUM_THREADS_PER_COLLISION_PRIMITIVE+task%NUM_THREADS_PER_COLLISION_PRIMITIVE
        PlanarArgs_func(args.pos, args.displacement_in, args.tri_indices, args.edge_indices, args.collision_info_array, args.parallel_eps, args.gamma, args.ac9_layer, args.ac9_tri_sign, args.ac9_wrong, args.ac9_pairs, args.truncation_t_out,original_tid)
        task+=stride
