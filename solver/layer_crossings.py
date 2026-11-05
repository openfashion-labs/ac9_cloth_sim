# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental five-point recovery contacts witnessed by actual cross-layer edge/face intersections."""
import numpy as np
import warp as wp
from newton._src.geometry.kernels import triangle_closest_point, TRI_CONTACT_FEATURE_FACE_INTERIOR
from newton._src.solvers.vbd.particle_vbd_kernels import (
    AC9LayerPairs, ac9_layer_segment_hit, ac9_layer_patch_projection, ac9_layer_cross_geometry_active,
    ac9_layer_coplanar_neighbor, ac9_layer_cross_evaluate)


@wp.func
def intersection(p: wp.vec3, q: wp.vec3, a: wp.vec3, b: wp.vec3, c: wp.vec3):
    d = q - p
    e1 = b - a
    e2 = c - a
    h = wp.cross(d, e2)
    det = wp.dot(e1, h)
    t = float(-1.0)
    if wp.abs(det) > 1.0e-15:
        inv = 1.0 / det
        s = p - a
        u = inv * wp.dot(s, h)
        r = wp.cross(s, e1)
        v = inv * wp.dot(d, r)
        fraction = inv * wp.dot(e2, r)
        if u >= -1.0e-6 and v >= -1.0e-6 and u + v <= 1.0 + 1.0e-6 and fraction > 1.0e-6 and fraction < 1.0 - 1.0e-6:
            t = fraction
    return t


@wp.kernel
def generate(pos: wp.array[wp.vec3], tri: wp.array2d[int], worlds: wp.array[int],
             layers: wp.array[int], signs: wp.array[float], bvh: wp.uint64, body: wp.uint64,
             filter_triangles: wp.array[int], filter_offsets: wp.array[int],
             pairs: AC9LayerPairs, active: wp.array[int], count: wp.array[int],
             overflow: wp.array[int], blocked: wp.array[int], stuck: wp.array[int]):
    edge = wp.tid()
    pairs.cross_targets[edge] = -1
    pairs.cross_witnesses[edge] = -1
    pairs.cross_patch_other[edge] = -1
    pairs.cross_flags[edge] = 0
    ia = pairs.cross_vertices[edge, 0]
    ib = pairs.cross_vertices[edge, 1]
    if layers[ia] < 0 or layers[ib] != layers[ia]:
        return
    a = pos[ia]
    b = pos[ib]
    nearest = float(1.0e10)
    chosen = int(-1)
    chosen_t = float(0.0)
    chosen_sign = float(0.0)
    chosen_witness = int(-1)
    chosen_other = int(-1)
    chosen_n = wp.vec3(0.0)
    first_n = wp.vec3(0.0)
    ambiguous = bool(False)
    lower = wp.min(a, b) - wp.vec3(1.0e-7)
    upper = wp.max(a, b) + wp.vec3(1.0e-7)
    query = wp.bvh_query_aabb(bvh, lower, upper)
    target = int(0)
    seen = int(0)
    while wp.bvh_query_next(query, target):
        seen += 1
        # a traversal that does not end (a ruined query state) is cut and counted, not left to hang the run
        if seen > 100000:
            wp.atomic_add(stuck, 0, 1)
            break
        i = tri[target, 0]
        if layers[i] < 0 or layers[i] == layers[ia] or signs[target] == 0.0:
            continue
        if worlds[ia] >= 0 and worlds[i] >= 0 and worlds[ia] != worlds[i]:
            continue
        j = tri[target, 1]
        k = tri[target, 2]
        fraction = intersection(a, b, pos[i], pos[j], pos[k])
        if fraction < 0.0:
            continue
        hit_point = (1.0 - fraction) * a + fraction * b
        hit_cp, hit_bary, hit_feature = triangle_closest_point(pos[i], pos[j], pos[k], hit_point)
        if not ac9_layer_patch_projection(hit_point, hit_cp, hit_bary, hit_feature, target, pos, tri, pairs):
            continue
        hit_neighbor = int(-1)
        if wp.min(hit_bary[0], wp.min(hit_bary[1], hit_bary[2])) <= 1.0e-5:
            hit_neighbor = ac9_layer_coplanar_neighbor(target, hit_bary, pos, tri, pairs)
        sg = signs[target]
        if layers[ia] < layers[i]:
            sg = -sg
        normal = wp.normalize(wp.cross(pos[j] - pos[i], pos[k] - pos[i])) * sg
        end = float(0.0)
        if wp.dot(b - pos[i], normal) < 0.0:
            end = 1.0
        sample_t = 0.5 * (fraction + end)
        point = (1.0 - sample_t) * a + sample_t * b
        support = target
        cp, bary, feature = triangle_closest_point(pos[i], pos[j], pos[k], point)
        for attempt in range(10):
            for walk in range(32):
                if ac9_layer_patch_projection(point, cp, bary, feature, support, pos, tri, pairs):
                    break
                neighbor = ac9_layer_coplanar_neighbor(support, bary, pos, tri, pairs)
                if neighbor < 0:
                    break
                nn = wp.normalize(wp.cross(pos[tri[neighbor, 1]] - pos[tri[neighbor, 0]], pos[tri[neighbor, 2]] - pos[tri[neighbor, 0]])) * signs[neighbor]
                if wp.dot(nn, normal * sg * signs[target]) < 1.0 - 1.0e-5:
                    break
                support = neighbor
                cp, bary, feature = triangle_closest_point(pos[tri[support, 0]], pos[tri[support, 1]], pos[tri[support, 2]], point)
            if ac9_layer_patch_projection(point, cp, bary, feature, support, pos, tri, pairs):
                break
            sample_t = 0.5 * (sample_t + fraction)
            point = (1.0 - sample_t) * a + sample_t * b
            cp, bary, feature = triangle_closest_point(pos[tri[support, 0]], pos[tri[support, 1]], pos[tri[support, 2]], point)
        factor = 1.0
        if layers[ia] < layers[i]:
            factor = -1.0
        support_sign = signs[support] * factor
        normal = wp.normalize(wp.cross(pos[tri[support, 1]] - pos[tri[support, 0]], pos[tri[support, 2]] - pos[tri[support, 0]])) * support_sign
        depth = -wp.dot(point - cp, normal)
        if not ac9_layer_patch_projection(point, cp, bary, feature, support, pos, tri, pairs) or depth <= 1.0e-7 or depth > 0.03:
            continue
        i = tri[support, 0]
        j = tri[support, 1]
        k = tri[support, 2]
        exempt = bool(False)
        if filter_offsets.shape[0] > 0:
            for endpoint in range(2):
                vertex = ia
                if endpoint == 1:
                    vertex = ib
                for rec in range(filter_offsets[vertex], filter_offsets[vertex + 1]):
                    if filter_triangles[rec] == target or filter_triangles[rec] == support:
                        exempt = True
        # A material-space exemption of either participating edge endpoint protects the sewn patch.
        if pairs.starts.shape[0] > 0:
            qflat = bary[0] * pairs.flat[i] + bary[1] * pairs.flat[j] + bary[2] * pairs.flat[k]
            for endpoint in range(2):
                vertex = ia
                if endpoint == 1:
                    vertex = ib
                for rec in range(pairs.starts[vertex], pairs.starts[vertex + 1]):
                    mate = pairs.mates[rec]
                    if pairs.piece[mate] == pairs.piece[i] and wp.length(qflat - pairs.flat[mate]) <= pairs.radius_mm:
                        exempt = True
        if exempt:
            continue
        direction = (cp - point) / depth
        body_hit = wp.mesh_query_ray(body, point + direction * 1.0e-7, direction, depth - 2.0e-7)
        obstruction = bool(body_hit.result)
        sweep = wp.bvh_query_aabb(bvh, wp.min(point, cp) - wp.vec3(1.0e-7), wp.max(point, cp) + wp.vec3(1.0e-7))
        other = int(0)
        while wp.bvh_query_next(sweep, other):
            u = tri[other, 0]
            v = tri[other, 1]
            w = tri[other, 2]
            same_world = worlds[ia] < 0 or worlds[u] < 0 or worlds[ia] == worlds[u]
            own_edge = (u == ia or v == ia or w == ia) and (u == ib or v == ib or w == ib)
            if same_world and other != support and not own_edge:
                if ac9_layer_segment_hit(point, cp, pos[u], pos[v], pos[w]):
                    obstruction = True
        # On CUDA every bvh query of a kernel shares one traversal stack (warp's bvh.h, BVH_SHARED_STACK), so the
        # sweep above overwrote the outer query's: it looped for ever on a shirt's placket (2026-11-02,
        # experiments/claude_layer_hang_cause_2026-11-02). The outer query is made again and the hits already seen
        # skipped; its order is the same each time, so it goes on from where it was.
        query = wp.bvh_query_aabb(bvh, lower, upper)
        skipped = int(0)
        skip_target = int(0)
        while skipped < seen:
            if not wp.bvh_query_next(query, skip_target):
                break
            skipped += 1
        if obstruction:
            wp.atomic_add(blocked, 0, 1)
            continue
        if chosen >= 0 and wp.dot(normal, first_n) < 0.5:
            ambiguous = True
        if chosen < 0:
            first_n = normal
        if depth < nearest or (depth == nearest and target < chosen_witness):
            nearest = depth
            chosen = support
            chosen_witness = target
            chosen_other = hit_neighbor
            chosen_t = sample_t
            chosen_sign = support_sign
            chosen_n = normal
    if chosen >= 0 and not ambiguous:
        row = wp.atomic_add(count, 0, 1)
        if row >= active.shape[0]:
            overflow[0] = 1
            return
        pairs.cross_targets[edge] = chosen
        pairs.cross_witnesses[edge] = chosen_witness
        pairs.cross_patch_other[edge] = chosen_other
        pairs.cross_t[edge] = chosen_t
        pairs.cross_sign[edge] = chosen_sign
        pairs.cross_flags[edge] = 1
        active[row] = edge


@wp.kernel
def update_flags(pos: wp.array[wp.vec3], tri: wp.array2d[int], pairs: AC9LayerPairs,
                 active: wp.array[int], count: wp.array[int]):
    row = wp.tid()
    if row < wp.min(count[0], active.shape[0]):
        edge = active[row]
        pairs.cross_flags[edge] = int(ac9_layer_cross_geometry_active(edge, pos, tri, pairs))


@wp.kernel
def accumulate(pos: wp.array[wp.vec3], tri: wp.array2d[int], colors: wp.array[int], color: int,
               pairs: AC9LayerPairs, active: wp.array[int], count: wp.array[int], radius: float,
               stiffness: float, forces: wp.array[wp.vec3], hessians: wp.array[wp.mat33]):
    row = wp.tid()
    if row >= wp.min(count[0], active.shape[0]):
        return
    edge = active[row]
    target = pairs.cross_targets[edge]
    if target < 0:
        return
    valid, p, cp, bary, normal = ac9_layer_cross_evaluate(edge, pos, tri, pairs)
    pairs.cross_flags[edge] = int(valid)
    if not valid:
        return
    ia = pairs.cross_vertices[edge, 0]
    ib = pairs.cross_vertices[edge, 1]
    t = pairs.cross_t[edge]
    a = tri[target, 0]
    b = tri[target, 1]
    c = tri[target, 2]
    distance = wp.dot(p - cp, normal)
    force = stiffness * (radius - distance) * normal
    hessian = stiffness * wp.outer(normal, normal)
    for endpoint in range(5):
        vertex = ia
        weight = 1.0 - t
        if endpoint == 1:
            vertex = ib
            weight = t
        if endpoint == 2:
            vertex = a
            weight = -bary[0]
        if endpoint == 3:
            vertex = b
            weight = -bary[1]
        if endpoint == 4:
            vertex = c
            weight = -bary[2]
        if color < 0 or colors[vertex] < 0 or colors[vertex] == color:
            wp.atomic_add(forces, vertex, weight * force)
            wp.atomic_add(hessians, vertex, weight * weight * hessian)


def setup(solver, triangles, pairs, capacity=4096, layers=None, signs=None, cached=True):
    faces = np.asarray(triangles, np.int32)
    edges = np.unique(np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1), axis=0)
    table = {tuple(map(int, edge)): i for i, edge in enumerate(edges)}
    adjacent = [[] for _ in edges]
    for ti, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            adjacent[table[tuple(sorted((int(a), int(b))))]].append(ti)
    vertex_edges = [[] for _ in range(solver.model.particle_count)]
    for i, (a, b) in enumerate(edges):
        vertex_edges[a].append(i)
        vertex_edges[b].append(i)
    offsets = np.r_[0, np.cumsum([len(row) for row in vertex_edges])].astype(np.int32)
    pairs.cross_vertices = wp.array(edges, dtype=int)
    pairs.cross_targets = wp.full(len(edges), -1, dtype=int)
    pairs.cross_witnesses = wp.full(len(edges), -1, dtype=int)
    pairs.cross_patch_other = wp.full(len(edges), -1, dtype=int)
    pairs.cross_t = wp.zeros(len(edges), dtype=float)
    pairs.cross_sign = wp.zeros(len(edges), dtype=float)
    pairs.cross_flags = wp.zeros(len(edges), dtype=int)
    pairs.cross_cached = int(bool(cached))
    pairs.cross_edge_faces = wp.array(np.array([row if len(row) == 2 else [-1, -1] for row in adjacent], np.int32), dtype=int)
    pairs.cross_layers = layers if layers is not None else getattr(solver, 'ac9_layer', wp.zeros(0, dtype=int))
    pairs.cross_tri_signs = signs if signs is not None else getattr(solver, 'ac9_tri_sign', wp.zeros(0, dtype=float))
    pairs.cross_worlds = solver.model.particle_world
    pairs.vertex_cross_offsets = wp.array(offsets, dtype=int)
    pairs.vertex_cross_edges = wp.array(np.array([e for row in vertex_edges for e in row], np.int32), dtype=int)
    pairs.triangle_cross_edges = wp.array(np.array([[table[tuple(sorted((int(t[a]), int(t[b]))))] for a, b in ((0, 1), (1, 2), (2, 0))] for t in faces], np.int32), dtype=int)
    pairs.model_cross_edges = wp.array(np.array([table[tuple(sorted(map(int, edge)))] for edge in solver.model.edge_indices.numpy()[:, 2:4]], np.int32), dtype=int)
    reference_pairs = AC9LayerPairs()
    for name in AC9LayerPairs.vars:
        setattr(reference_pairs, name, getattr(pairs, name))
    reference_pairs.cross_flags = wp.zeros(len(edges), dtype=int)
    solver.ac9_layer_crossings = {'active': wp.zeros(capacity, dtype=int), 'count': wp.zeros(1, dtype=int),
                                  'overflow': wp.zeros(1, dtype=int), 'blocked': wp.zeros(1, dtype=int),
                                  'stuck': wp.zeros(1, dtype=int),
                                  'reference_pairs': reference_pairs}
