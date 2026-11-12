# SPDX-FileCopyrightText: Copyright (c) 2025 The Newton Developers
# SPDX-License-Identifier: Apache-2.0
# Modified by AC9: pack two unchanged 16-lane particle solves per block.
"""CUDA-only experimental elastic packing; no physics or iteration reduction."""
import warp as wp
from newton._src.solvers.vbd.particle_vbd_kernels import MeshAdjacencyData, ParticleFlags, SIMSPIKE_BUCKLE_RATIO, SIMSPIKE_MEMBRANE_BW, TILE_SIZE_TRI_MESH_ELASTICITY_SOLVE, VBD_DEBUG_PRINTING_OPTIONS, evaluate_bw_membrane_force_hessian, evaluate_dihedral_angle_based_bending_force_hessian, evaluate_neo_hookean_membrane_force_hessian, evaluate_volumetric_neo_hookean_force_and_hessian, get_vertex_adjacent_edge_id_order, get_vertex_adjacent_face_id_order, get_vertex_adjacent_tet_id_order, get_vertex_num_adjacent_edges, get_vertex_num_adjacent_faces, get_vertex_num_adjacent_tets, simspike_buckle_factor

@wp.func_native('#if defined(__CUDA_ARCH__)\n    unsigned int mask = 0xFFFFu << (threadIdx.x & 16);\n    return wp::warp_reduce(value, [](auto x, auto y) { return wp::add(x, y); }, mask);\n#else\n    return value;\n#endif\n')
def half_sum_vec(value: wp.vec3) -> wp.vec3:
    ...

@wp.func_native('#if defined(__CUDA_ARCH__)\n    unsigned int mask = 0xFFFFu << (threadIdx.x & 16);\n    return wp::warp_reduce(value, [](auto x, auto y) { return wp::add(x, y); }, mask);\n#else\n    return value;\n#endif\n')
def half_sum_mat(value: wp.mat33) -> wp.mat33:
    ...
@wp.kernel(module='unique', enable_backward=False, module_options={'enable_backward': False})
def solve_elasticity_packed(dt: float, particle_ids_in_color: wp.array[wp.int32], pos_prev: wp.array[wp.vec3], pos: wp.array[wp.vec3], mass: wp.array[float], inertia: wp.array[wp.vec3], particle_flags: wp.array[wp.int32], tri_indices: wp.array2d[wp.int32], tri_poses: wp.array[wp.mat22], tri_materials: wp.array2d[float], tri_areas: wp.array[float], edge_indices: wp.array2d[wp.int32], edge_rest_angles: wp.array[float], edge_rest_length: wp.array[float], edge_bending_properties: wp.array2d[float], ac9_bf: wp.array2d[float], tet_indices: wp.array2d[wp.int32], tet_poses: wp.array[wp.mat33], tet_materials: wp.array2d[float], particle_adjacency: MeshAdjacencyData, particle_forces: wp.array[wp.vec3], particle_hessians: wp.array[wp.mat33], particle_displacements: wp.array[wp.vec3]):
    tid = wp.tid()
    block_idx = tid // TILE_SIZE_TRI_MESH_ELASTICITY_SOLVE
    thread_idx = tid % TILE_SIZE_TRI_MESH_ELASTICITY_SOLVE
    particle_index = int(0)
    valid_particle = block_idx < particle_ids_in_color.shape[0]
    active_particle = bool(False)
    if valid_particle:
        particle_index = particle_ids_in_color[block_idx]
        active_particle = bool(particle_flags[particle_index] & ParticleFlags.ACTIVE) and mass[particle_index] != 0.0
        if not active_particle and thread_idx == 0:
            particle_displacements[particle_index] = wp.vec3(0.0)
    dt_sqr_reciprocal = 1.0 / (dt * dt)
    f = wp.vec3(0.0)
    h = wp.mat33(0.0)
    batch_counter = wp.int32(0)
    if active_particle:
        if tri_indices.shape[0] > 0:
            num_adj_faces = get_vertex_num_adjacent_faces(particle_adjacency, particle_index)
            while batch_counter + thread_idx < num_adj_faces:
                adj_tri_counter = thread_idx + batch_counter
                batch_counter += TILE_SIZE_TRI_MESH_ELASTICITY_SOLVE
                tri_index, vertex_order = get_vertex_adjacent_face_id_order(particle_adjacency, particle_index, adj_tri_counter)
                if wp.static('connectivity' in VBD_DEBUG_PRINTING_OPTIONS):
                    wp.printf('particle: %d | num_adj_faces: %d | ', particle_index, get_vertex_num_adjacent_faces(particle_adjacency, particle_index))
                    wp.printf('i_face: %d | face id: %d | v_order: %d | ', adj_tri_counter, tri_index, vertex_order)
                    wp.printf('face: %d %d %d\n', tri_indices[tri_index, 0], tri_indices[tri_index, 1], tri_indices[tri_index, 2])
                if tri_materials[tri_index, 0] > 0.0 or tri_materials[tri_index, 1] > 0.0:
                    if SIMSPIKE_MEMBRANE_BW == 1:
                        f_tri, h_tri = evaluate_bw_membrane_force_hessian(tri_index, vertex_order, pos, pos_prev, tri_indices, tri_poses[tri_index], tri_areas[tri_index], tri_materials[tri_index, 0], tri_materials[tri_index, 1], tri_materials[tri_index, 3], tri_materials[tri_index, 2], dt)
                    else:
                        f_tri, h_tri = evaluate_neo_hookean_membrane_force_hessian(tri_index, vertex_order, pos, pos_prev, tri_indices, tri_poses[tri_index], tri_areas[tri_index], tri_materials[tri_index, 0], tri_materials[tri_index, 1], tri_materials[tri_index, 2], dt)
                    f += f_tri
                    h += h_tri
        if edge_indices.shape[0] > 0:
            batch_counter = wp.int32(0)
            num_adj_edges = get_vertex_num_adjacent_edges(particle_adjacency, particle_index)
            while batch_counter + thread_idx < num_adj_edges:
                adj_edge_counter = batch_counter + thread_idx
                batch_counter += TILE_SIZE_TRI_MESH_ELASTICITY_SOLVE
                nei_edge_index, vertex_order_on_edge = get_vertex_adjacent_edge_id_order(particle_adjacency, particle_index, adj_edge_counter)
                if edge_bending_properties[nei_edge_index, 0] > 0.0:
                    bend_ke = edge_bending_properties[nei_edge_index, 0]
                    if SIMSPIKE_BUCKLE_RATIO > 0.0:
                        bend_ke = bend_ke * simspike_buckle_factor(nei_edge_index, edge_indices, pos, tri_indices, tri_poses, particle_adjacency)
                    f_edge, h_edge = evaluate_dihedral_angle_based_bending_force_hessian(nei_edge_index, vertex_order_on_edge, pos, pos_prev, edge_indices, edge_rest_angles, edge_rest_length, bend_ke, edge_bending_properties[nei_edge_index, 1], dt, ac9_bf)
                    f += f_edge
                    h += h_edge
        if tet_indices.shape[0] > 0:
            batch_counter = wp.int32(0)
            num_adj_tets = get_vertex_num_adjacent_tets(particle_adjacency, particle_index)
            while batch_counter + thread_idx < num_adj_tets:
                adj_tet_counter = batch_counter + thread_idx
                batch_counter += TILE_SIZE_TRI_MESH_ELASTICITY_SOLVE
                nei_tet_index, vertex_order_on_tet = get_vertex_adjacent_tet_id_order(particle_adjacency, particle_index, adj_tet_counter)
                if tet_materials[nei_tet_index, 0] > 0.0 or tet_materials[nei_tet_index, 1] > 0.0:
                    f_tet, h_tet = evaluate_volumetric_neo_hookean_force_and_hessian(nei_tet_index, vertex_order_on_tet, pos_prev, pos, tet_indices, tet_poses[nei_tet_index], tet_materials[nei_tet_index, 0], tet_materials[nei_tet_index, 1], tet_materials[nei_tet_index, 2], dt)
                    f += f_tet
                    h += h_tet
    f_total = half_sum_vec(f)
    h_total = half_sum_mat(h)
    if thread_idx == 0 and active_particle:
        h_total = h_total + mass[particle_index] * dt_sqr_reciprocal * wp.identity(n=3, dtype=float) + particle_hessians[particle_index]
        if abs(wp.determinant(h_total)) > 1e-08:
            h_inv = wp.inverse(h_total)
            f_total = f_total + mass[particle_index] * (inertia[particle_index] - pos[particle_index]) * dt_sqr_reciprocal + particle_forces[particle_index]
            particle_displacements[particle_index] = particle_displacements[particle_index] + h_inv * f_total
