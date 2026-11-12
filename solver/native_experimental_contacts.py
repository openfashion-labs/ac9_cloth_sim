# SPDX-FileCopyrightText: Copyright (c) 2025 The Newton Developers
# SPDX-License-Identifier: Apache-2.0
# Modified by AC9: explicit thread index and experimental launch wrapper.
"""Experimental launch fusion. Generated from existing kernel signatures."""
import warp as wp
from newton._src.solvers.vbd.particle_vbd_kernels import AC9LayerPairs, NUM_THREADS_PER_COLLISION_PRIMITIVE, TriMeshCollisionInfo, _eval_body_particle_contact, _eval_soft_ef_contact, ac9_layer_cross_vt, ac9_layer_dir, ac9_layer_ee_free, ac9_layer_vt_contact_4_vertices, evaluate_edge_edge_contact_2_vertices, evaluate_spring_force_and_hessian_both_vertices, evaluate_vertex_triangle_collision_force_hessian_4_vertices, get_edge_colliding_edges_count, get_vertex_colliding_triangles_count
from newton._src.solvers.vbd import particle_vbd_kernels as k
from newton._src.solvers.vbd.particle_vbd_kernels import TriMeshCollisionInfo, AC9LayerPairs

@wp.struct
class BodyArgs:
    dt: float
    current_color: int
    pos_anchor: wp.array[wp.vec3]
    pos: wp.array[wp.vec3]
    particle_colors: wp.array[int]
    friction_epsilon: float
    particle_radius: wp.array[float]
    body_particle_contact_indices: wp.array[wp.vec3i]
    body_particle_contact_count: wp.array[int]
    body_particle_contact_max: int
    body_particle_contact_penalty_k: wp.array[float]
    body_particle_contact_material_ke: wp.array[float]
    body_particle_contact_material_kd: wp.array[float]
    body_particle_contact_material_mu: wp.array[float]
    shape_body: wp.array[int]
    body_q: wp.array[wp.transform]
    body_q_prev: wp.array[wp.transform]
    body_qd: wp.array[wp.spatial_vector]
    body_com: wp.array[wp.vec3]
    contact_shape: wp.array[int]
    contact_body_pos: wp.array[wp.vec3]
    contact_body_vel: wp.array[wp.vec3]
    contact_normal: wp.array[wp.vec3]
    shape_margin: wp.array[float]
    contact_barycentric: wp.array[wp.vec3]
    fric_anchor: wp.array[wp.vec3]
    fric_n: wp.array[wp.vec3]
    fric_hit: wp.array[int]
    particle_forces: wp.array[wp.vec3]
    particle_hessians: wp.array[wp.mat33]

@wp.struct
class SpringArgs:
    dt: float
    current_color: int
    pos_anchor: wp.array[wp.vec3]
    pos: wp.array[wp.vec3]
    particle_colors: wp.array[int]
    num_springs: int
    spring_indices: wp.array[int]
    spring_rest_length: wp.array[float]
    spring_stiffness: wp.array[float]
    spring_damping: wp.array[float]
    particle_forces: wp.array[wp.vec3]
    particle_hessians: wp.array[wp.mat33]

@wp.struct
class SelfArgs:
    dt: float
    current_color: int
    pos_prev: wp.array[wp.vec3]
    pos: wp.array[wp.vec3]
    particle_colors: wp.array[int]
    tri_indices: wp.array2d[wp.int32]
    edge_indices: wp.array2d[wp.int32]
    collision_info_array: wp.array[TriMeshCollisionInfo]
    collision_radius: float
    soft_contact_ke: float
    soft_contact_kd: float
    friction_mu: float
    friction_epsilon: float
    edge_edge_parallel_epsilon: float
    ac9_layer: wp.array[int]
    ac9_tri_sign: wp.array[float]
    ac9_wrong: wp.array[int]
    ac9_pairs: AC9LayerPairs
    particle_forces: wp.array[wp.vec3]
    particle_hessians: wp.array[wp.mat33]

@wp.func
def BodyArgs_func(dt: float, current_color: int, pos_anchor: wp.array[wp.vec3], pos: wp.array[wp.vec3], particle_colors: wp.array[int], friction_epsilon: float, particle_radius: wp.array[float], body_particle_contact_indices: wp.array[wp.vec3i], body_particle_contact_count: wp.array[int], body_particle_contact_max: int, body_particle_contact_penalty_k: wp.array[float], body_particle_contact_material_ke: wp.array[float], body_particle_contact_material_kd: wp.array[float], body_particle_contact_material_mu: wp.array[float], shape_body: wp.array[int], body_q: wp.array[wp.transform], body_q_prev: wp.array[wp.transform], body_qd: wp.array[wp.spatial_vector], body_com: wp.array[wp.vec3], contact_shape: wp.array[int], contact_body_pos: wp.array[wp.vec3], contact_body_vel: wp.array[wp.vec3], contact_normal: wp.array[wp.vec3], shape_margin: wp.array[float], contact_barycentric: wp.array[wp.vec3], fric_anchor: wp.array[wp.vec3], fric_n: wp.array[wp.vec3], fric_hit: wp.array[int], particle_forces: wp.array[wp.vec3], particle_hessians: wp.array[wp.mat33], thread_index: int):
    t_id = thread_index
    count = min(body_particle_contact_max, body_particle_contact_count[0])
    if t_id >= count:
        return
    corners = body_particle_contact_indices[t_id]
    contact_ke = body_particle_contact_penalty_k[t_id]
    contact_kd = body_particle_contact_material_kd[t_id]
    contact_mu = body_particle_contact_material_mu[t_id]
    if corners[1] < 0:
        particle_idx = corners[0]
        if particle_colors[particle_idx] == current_color or particle_colors[particle_idx] < 0:
            _prev = pos_anchor[particle_idx]
            if fric_hit.shape[0] > 0:
                _an = contact_normal[t_id]
                _h = fric_hit[particle_idx]
                if _h == 1 or _h == 3:
                    _a = fric_anchor[particle_idx]
                    _prev = _a - _an * wp.dot(_an, _a - _prev)
                    fric_hit[particle_idx] = 3
                if _h == 0:
                    fric_hit[particle_idx] = 2
                fric_n[particle_idx] = _an
            body_contact_force, body_contact_hessian = _eval_body_particle_contact(particle_idx, pos[particle_idx], _prev, t_id, contact_ke, contact_kd, contact_mu, friction_epsilon, particle_radius, shape_body, body_q, body_q_prev, body_qd, body_com, contact_shape, contact_body_pos, contact_body_vel, contact_normal, shape_margin, dt)
            wp.atomic_add(particle_forces, particle_idx, body_contact_force)
            wp.atomic_add(particle_hessians, particle_idx, body_contact_hessian)
    else:
        bary = contact_barycentric[t_id]
        ef_force, ef_hessian, _cp_world = _eval_soft_ef_contact(t_id, corners, bary, pos, pos_anchor, particle_radius, contact_ke, contact_kd, contact_mu, friction_epsilon, shape_body, body_q, body_q_prev, body_qd, body_com, contact_shape, contact_body_pos, contact_body_vel, contact_normal, shape_margin, dt)
        for i in range(3):
            ci = corners[i]
            if ci >= 0:
                w = bary[i]
                if particle_colors[ci] == current_color or particle_colors[ci] < 0:
                    wp.atomic_add(particle_forces, ci, w * ef_force)
                    wp.atomic_add(particle_hessians, ci, w * w * ef_hessian)

@wp.func
def SpringArgs_func(dt: float, current_color: int, pos_anchor: wp.array[wp.vec3], pos: wp.array[wp.vec3], particle_colors: wp.array[int], num_springs: int, spring_indices: wp.array[int], spring_rest_length: wp.array[float], spring_stiffness: wp.array[float], spring_damping: wp.array[float], particle_forces: wp.array[wp.vec3], particle_hessians: wp.array[wp.mat33], thread_index: int):
    """Accumulate spring forces and hessians, parallelized by springs.

    Each thread handles one spring and uses atomic operations to add
    forces and hessians to vertices with the current color.
    """
    spring_idx = thread_index
    if spring_idx < num_springs:
        v0 = spring_indices[spring_idx * 2]
        v1 = spring_indices[spring_idx * 2 + 1]
        c_v0 = particle_colors[v0]
        c_v1 = particle_colors[v1]
        if c_v0 == current_color or c_v0 < 0 or c_v1 == current_color or (c_v1 < 0):
            _, _, force_v0, force_v1, hessian = evaluate_spring_force_and_hessian_both_vertices(spring_idx, dt, pos, pos_anchor, spring_indices, spring_rest_length, spring_stiffness, spring_damping)
            if c_v0 == current_color or c_v0 < 0:
                wp.atomic_add(particle_forces, v0, force_v0)
                wp.atomic_add(particle_hessians, v0, hessian)
            if c_v1 == current_color or c_v1 < 0:
                wp.atomic_add(particle_forces, v1, force_v1)
                wp.atomic_add(particle_hessians, v1, hessian)

@wp.func
def SelfArgs_func(dt: float, current_color: int, pos_prev: wp.array[wp.vec3], pos: wp.array[wp.vec3], particle_colors: wp.array[int], tri_indices: wp.array2d[wp.int32], edge_indices: wp.array2d[wp.int32], collision_info_array: wp.array[TriMeshCollisionInfo], collision_radius: float, soft_contact_ke: float, soft_contact_kd: float, friction_mu: float, friction_epsilon: float, edge_edge_parallel_epsilon: float, ac9_layer: wp.array[int], ac9_tri_sign: wp.array[float], ac9_wrong: wp.array[int], ac9_pairs: AC9LayerPairs, particle_forces: wp.array[wp.vec3], particle_hessians: wp.array[wp.mat33], thread_index: int):
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
                            wp.atomic_add(particle_forces, e1_v1, collision_force_0)
                            wp.atomic_add(particle_hessians, e1_v1, collision_hessian_0)
                        if c_e1_v2 == current_color or c_e1_v2 < 0:
                            wp.atomic_add(particle_forces, e1_v2, collision_force_1)
                            wp.atomic_add(particle_hessians, e1_v2, collision_hessian_1)
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
                            wp.atomic_add(particle_forces, particle_idx, collision_force_3)
                            wp.atomic_add(particle_hessians, particle_idx, collision_hessian_3)
                        if c_tri_a == current_color or c_tri_a < 0:
                            wp.atomic_add(particle_forces, tri_a, collision_force_0)
                            wp.atomic_add(particle_hessians, tri_a, collision_hessian_0)
                        if c_tri_b == current_color or c_tri_b < 0:
                            wp.atomic_add(particle_forces, tri_b, collision_force_1)
                            wp.atomic_add(particle_hessians, tri_b, collision_hessian_1)
                        if c_tri_c == current_color or c_tri_c < 0:
                            wp.atomic_add(particle_forces, tri_c, collision_force_2)
                            wp.atomic_add(particle_hessians, tri_c, collision_hessian_2)
            collision_buffer_counter += NUM_THREADS_PER_COLLISION_PRIMITIVE

@wp.kernel(enable_backward=False)
def accumulate(body: BodyArgs, spring: SpringArgs, self_contact: SelfArgs, body_size: int, spring_size: int, self_size: int):
    tid = wp.tid()
    if tid < body_size:
        BodyArgs_func(body.dt, body.current_color, body.pos_anchor, body.pos, body.particle_colors, body.friction_epsilon, body.particle_radius, body.body_particle_contact_indices, body.body_particle_contact_count, body.body_particle_contact_max, body.body_particle_contact_penalty_k, body.body_particle_contact_material_ke, body.body_particle_contact_material_kd, body.body_particle_contact_material_mu, body.shape_body, body.body_q, body.body_q_prev, body.body_qd, body.body_com, body.contact_shape, body.contact_body_pos, body.contact_body_vel, body.contact_normal, body.shape_margin, body.contact_barycentric, body.fric_anchor, body.fric_n, body.fric_hit, body.particle_forces, body.particle_hessians, tid)
    if tid < spring_size:
        SpringArgs_func(spring.dt, spring.current_color, spring.pos_anchor, spring.pos, spring.particle_colors, spring.num_springs, spring.spring_indices, spring.spring_rest_length, spring.spring_stiffness, spring.spring_damping, spring.particle_forces, spring.particle_hessians, tid)
    if tid < self_size:
        SelfArgs_func(self_contact.dt, self_contact.current_color, self_contact.pos_prev, self_contact.pos, self_contact.particle_colors, self_contact.tri_indices, self_contact.edge_indices, self_contact.collision_info_array, self_contact.collision_radius, self_contact.soft_contact_ke, self_contact.soft_contact_kd, self_contact.friction_mu, self_contact.friction_epsilon, self_contact.edge_edge_parallel_epsilon, self_contact.ac9_layer, self_contact.ac9_tri_sign, self_contact.ac9_wrong, self_contact.ac9_pairs, self_contact.particle_forces, self_contact.particle_hessians, tid)

@wp.kernel(enable_backward=False)
def accumulate_body_spring(body: BodyArgs, spring: SpringArgs, body_size: int, spring_size: int):
    tid = wp.tid()
    if tid < body_size:
        BodyArgs_func(body.dt, body.current_color, body.pos_anchor, body.pos, body.particle_colors, body.friction_epsilon, body.particle_radius, body.body_particle_contact_indices, body.body_particle_contact_count, body.body_particle_contact_max, body.body_particle_contact_penalty_k, body.body_particle_contact_material_ke, body.body_particle_contact_material_kd, body.body_particle_contact_material_mu, body.shape_body, body.body_q, body.body_q_prev, body.body_qd, body.body_com, body.contact_shape, body.contact_body_pos, body.contact_body_vel, body.contact_normal, body.shape_margin, body.contact_barycentric, body.fric_anchor, body.fric_n, body.fric_hit, body.particle_forces, body.particle_hessians, tid)
    if tid < spring_size:
        SpringArgs_func(spring.dt, spring.current_color, spring.pos_anchor, spring.pos, spring.particle_colors, spring.num_springs, spring.spring_indices, spring.spring_rest_length, spring.spring_stiffness, spring.spring_damping, spring.particle_forces, spring.particle_hessians, tid)

class ContactRuntime:
    def __init__(self, device, split_self=False, self_launcher=None):
        self.device = device
        self.split_self = split_self
        if self_launcher is not None and not split_self:
            raise ValueError('A separate self launcher requires split-self mode')
        self.self_launcher = self_launcher
        self.pending = {}
        self.last = None
        self.schemas = {
            k.accumulate_particle_body_contact_force_and_hessian: (BodyArgs, ['dt', 'current_color', 'pos_anchor', 'pos', 'particle_colors', 'friction_epsilon', 'particle_radius', 'body_particle_contact_indices', 'body_particle_contact_count', 'body_particle_contact_max', 'body_particle_contact_penalty_k', 'body_particle_contact_material_ke', 'body_particle_contact_material_kd', 'body_particle_contact_material_mu', 'shape_body', 'body_q', 'body_q_prev', 'body_qd', 'body_com', 'contact_shape', 'contact_body_pos', 'contact_body_vel', 'contact_normal', 'shape_margin', 'contact_barycentric', 'fric_anchor', 'fric_n', 'fric_hit', 'particle_forces', 'particle_hessians']),
            k.accumulate_spring_force_and_hessian: (SpringArgs, ['dt', 'current_color', 'pos_anchor', 'pos', 'particle_colors', 'num_springs', 'spring_indices', 'spring_rest_length', 'spring_stiffness', 'spring_damping', 'particle_forces', 'particle_hessians']),
            k.accumulate_self_contact_force_and_hessian: (SelfArgs, ['dt', 'current_color', 'pos_prev', 'pos', 'particle_colors', 'tri_indices', 'edge_indices', 'collision_info_array', 'collision_radius', 'soft_contact_ke', 'soft_contact_kd', 'friction_mu', 'friction_epsilon', 'edge_edge_parallel_epsilon', 'ac9_layer', 'ac9_tri_sign', 'ac9_wrong', 'ac9_pairs', 'particle_forces', 'particle_hessians']),
        }
    def enqueue(self, **kwargs):
        kernel = kwargs["kernel"]
        if self.split_self and kernel == k.accumulate_self_contact_force_and_hessian:
            self.flush(); (self.self_launcher or wp.launch)(**kwargs); return
        if kernel in self.pending: raise ValueError("Duplicate contact contribution")
        self.pending[kernel] = kwargs
    def flush(self):
        if not self.pending: return
        structs = []; sizes = []; calls = []
        for kernel, (schema, fields) in self.schemas.items():
            value = schema(); call = self.pending.get(kernel)
            if call is not None:
                args = call["inputs"] + call["outputs"]
                if len(args) != len(fields): raise ValueError("Contact signature mismatch")
                for name, arg in zip(fields, args): setattr(value, name, arg)
                sizes.append(int(call["dim"]))
            else: sizes.append(0)
            structs.append(value); calls.append(call)
        maximum = max(sizes)
        if maximum:
            options = {}
            if calls[2] is not None: options["max_blocks"] = calls[2]["max_blocks"]
            if calls[2] is None:
                wp.launch(accumulate_body_spring, dim=maximum, inputs=structs[:2] + sizes[:2], device=self.device)
            else:
                wp.launch(accumulate, dim=maximum, inputs=structs + sizes, device=self.device, **options)
        self.last = (structs, sizes, calls)
        self.pending.clear()
