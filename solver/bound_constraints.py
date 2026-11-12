# SPDX-License-Identifier: GPL-3.0-or-later
"""Eliminate a colliding follower into its supporting triangle's degrees of freedom.

The complete rotating offset is retained. Forces use the exact position Jacobian.
Each endpoint's positive Hessian block is pulled back as J.T H J; cross terms
between endpoints sharing a support and second position derivatives are omitted.
This is a block approximation, not the complete reduced Hessian.
"""
import numpy as np
import warp as wp


@wp.func
def binding_point(a: wp.vec3, b: wp.vec3, c: wp.vec3, w: wp.vec3, d: wp.vec3):
    e = b - a
    f = c - a
    le = wp.max(wp.length(e), 1.0e-12)
    cross = wp.cross(e, f)
    lc = wp.max(wp.length(cross), 1.0e-18)
    u = e / le
    n = cross / lc
    v = wp.cross(n, u)
    eye = wp.identity(3, float)
    pu = (eye - wp.outer(u, u)) / le
    pn = (eye - wp.outer(n, n)) / lc
    jn0 = pn * wp.skew(f - e)
    jn1 = pn * (-wp.skew(f))
    jn2 = pn * wp.skew(e)
    ju0 = -pu
    ju1 = pu
    ju2 = wp.mat33(0.0)
    jv0 = -wp.skew(u) * jn0 + wp.skew(n) * ju0
    jv1 = -wp.skew(u) * jn1 + wp.skew(n) * ju1
    jv2 = -wp.skew(u) * jn2 + wp.skew(n) * ju2
    j0 = w[0] * eye + d[0] * ju0 + d[1] * jv0 + d[2] * jn0
    j1 = w[1] * eye + d[0] * ju1 + d[1] * jv1 + d[2] * jn1
    j2 = w[2] * eye + d[0] * ju2 + d[1] * jv2 + d[2] * jn2
    point = w[0] * a + w[1] * b + w[2] * c + d[0] * u + d[1] * v + d[2] * n
    return point, j0, j1, j2


@wp.kernel
def place_bound(vertices: wp.array[int], triangles: wp.array[wp.vec3i], weights: wp.array[wp.vec3],
                offsets: wp.array[wp.vec3], positions: wp.array[wp.vec3]):
    i = wp.tid()
    t = triangles[i]
    p, j0, j1, j2 = binding_point(positions[t[0]], positions[t[1]], positions[t[2]], weights[i], offsets[i])
    positions[vertices[i]] = p


@wp.kernel
def transfer_bound(vertices: wp.array[int], triangles: wp.array[wp.vec3i], weights: wp.array[wp.vec3],
                   offsets: wp.array[wp.vec3], positions: wp.array[wp.vec3], colors: wp.array[int], color: int,
                   forces: wp.array[wp.vec3], hessians: wp.array[wp.mat33]):
    i = wp.tid()
    t = triangles[i]
    p, j0, j1, j2 = binding_point(positions[t[0]], positions[t[1]], positions[t[2]], weights[i], offsets[i])
    v = vertices[i]
    force = forces[v]
    hessian = hessians[v]
    if colors[t[0]] == color:
        wp.atomic_add(forces, t[0], wp.transpose(j0) * force)
        wp.atomic_add(hessians, t[0], wp.transpose(j0) * hessian * j0)
    if colors[t[1]] == color:
        wp.atomic_add(forces, t[1], wp.transpose(j1) * force)
        wp.atomic_add(hessians, t[1], wp.transpose(j1) * hessian * j1)
    if colors[t[2]] == color:
        wp.atomic_add(forces, t[2], wp.transpose(j2) * force)
        wp.atomic_add(hessians, t[2], wp.transpose(j2) * hessian * j2)
    # A follower is evaluated for every color. Do not carry its previous color's load.
    forces[v] = wp.vec3(0.0)
    hessians[v] = wp.mat33(0.0)


class BoundConstraints:
    def __init__(self, model, vertices, triangles, weights, offsets):
        self.native_fused = False
        vertices = np.asarray(vertices, np.int32)
        triangles = np.asarray(triangles, np.int32)
        if np.isin(triangles, vertices).any():
            raise ValueError('AC9 bound constraints need independent supporting triangles')
        self.device = model.device
        self.vertices = wp.array(vertices, dtype=int, device=self.device)
        self.triangles = wp.array(triangles, dtype=wp.vec3i, device=self.device)
        self.weights = wp.array(np.asarray(weights, np.float32), dtype=wp.vec3, device=self.device)
        self.offsets = wp.array(np.asarray(offsets, np.float32), dtype=wp.vec3, device=self.device)
        parent = np.arange(model.particle_count)
        def find(v):
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return int(v)
        for v, t in zip(vertices, triangles):
            for u in t:
                parent[find(int(u))] = find(int(v))
        members = np.unique(np.r_[vertices, triangles.ravel()]).astype(np.int32)
        _, group = np.unique([find(int(v)) for v in members], return_inverse=True)
        self.members = wp.array(members, dtype=int, device=self.device)
        self.groups = wp.array(group.astype(np.int32), dtype=int, device=self.device)
        self.row_groups = wp.array(group[np.searchsorted(members, vertices)].astype(np.int32), dtype=int, device=self.device)
        self.limits = wp.ones(int(group.max()) + 1, dtype=float, device=self.device)
        self.path_counts = wp.zeros(3, dtype=int, device=self.device)
        self.sweep_error = wp.zeros(1, dtype=float, device=self.device)
        self.minimum_scale = wp.ones(1, dtype=float, device=self.device)
        self.scale_counts = wp.zeros(2, dtype=int, device=self.device)
        self.accepted = wp.zeros(model.particle_count, dtype=wp.vec3, device=self.device)
        self.increment = wp.zeros(model.particle_count, dtype=wp.vec3, device=self.device)
        # Refine existing colors so a spring cannot update a support and its other
        # endpoint simultaneously. Splitting a color cannot break existing conflicts.
        supports = {int(v): list(map(int, t)) for v, t in zip(vertices, triangles)}
        neighbors = {}
        springs = (model.spring_indices.numpy().reshape(-1, 2) if model.spring_indices is not None
                   else np.empty((0, 2), np.int32))
        for a, b in springs:
            if int(a) not in supports and int(b) not in supports:
                continue
            ids = set(supports.get(int(a), [int(a)])) | set(supports.get(int(b), [int(b)]))
            for v in ids:
                neighbors.setdefault(v, set()).update(ids - {v})
        colors = model.particle_colors.numpy().copy()
        groups = []
        for old in model.particle_color_groups:
            assigned, buckets = {}, []
            for v in map(int, old.numpy()):
                if v in supports:
                    continue
                used = {assigned[n] for n in neighbors.get(v, ()) if n in assigned}
                k = next(k for k in range(len(buckets) + 1) if k not in used)
                if k == len(buckets):
                    buckets.append([])
                buckets[k].append(v)
                assigned[v] = k
            for bucket in buckets:
                colors[bucket] = len(groups)
                groups.append(wp.array(bucket, dtype=int, device=self.device))
        colors[vertices] = -1   # Always evaluate follower loads, then scatter by support color.
        model.particle_colors.assign(colors)
        model.particle_color_groups = groups
        self.summary = {'vertices': len(vertices), 'colors': len(groups),
                        'spring_stencils': sum(int(a) in supports or int(b) in supports
                                               for a, b in springs)}

    def place(self, positions):
        wp.launch(place_bound, dim=len(self.vertices),
                  inputs=[self.vertices, self.triangles, self.weights, self.offsets, positions], device=self.device)

    def transfer(self, positions, colors, color, forces, hessians):
        wp.launch(transfer_bound, dim=len(self.vertices), inputs=[self.vertices, self.triangles, self.weights,
                  self.offsets, positions, colors, color, forces, hessians], device=self.device)

    def propose(self, reference, displacements):
        wp.launch(propose_bound, dim=len(self.vertices), inputs=[self.vertices, self.triangles, self.weights,
                  self.offsets, reference, displacements], device=self.device)

    def prepare_increment(self, reference, positions, displacements):
        if self.native_fused:
            wp.launch(prepare_increment, dim=len(positions), inputs=[reference, positions, displacements,
                      self.accepted, self.increment], device=self.device)
        else:
            wp.copy(self.accepted, positions)
            wp.launch(make_increment, dim=len(positions), inputs=[reference, positions, displacements,
                      self.increment], device=self.device)
        self.propose(self.accepted, self.increment)

    def limit_travel(self, reference, scales, maximum):
        wp.launch(limit_travel, dim=len(self.accepted), inputs=[reference, self.accepted, self.increment,
                  maximum, scales], device=self.device)
        # The path test consumes group limits directly. Publishing them to
        # per-particle scales here would be overwritten by the final scatter
        # without an intervening reader; keep the diagnostic recording order.
        self.limit(scales, self.increment, wp.inf, scatter=not self.native_fused)
        # A bound on the entire nonlinear path, not just the endpoint chord.
        wp.launch(limit_bound_path, dim=len(self.vertices), inputs=[self.vertices, self.triangles, self.weights,
            self.offsets, self.accepted, self.increment, reference, self.row_groups, self.limits, maximum,
            self.path_counts], device=self.device)
        wp.launch(scatter_limits, dim=len(self.members), inputs=[self.members, self.groups, self.limits,
                  scales], device=self.device)

    def finish_increment(self, positions, reference, displacements):
        self.synchronize(positions, reference, displacements)
        if not self.native_fused:
            wp.launch(sync_displacements, dim=len(positions), inputs=[positions, reference, displacements], device=self.device)

    def apply_increment(self, positions, reference, displacements, scales):
        # Write the same accepted endpoint and query-relative displacement in
        # one pass. synchronize() subsequently replaces follower rows only.
        wp.launch(apply_increment, dim=len(positions), inputs=[self.accepted, self.increment,
                  scales, positions, reference, displacements], device=self.device)

    def limit(self, scales, displacements, maximum, *, scatter=True):
        self.limits.fill_(1.0)
        wp.launch(gather_limits, dim=len(self.members), inputs=[self.members, self.groups, scales,
                  displacements, maximum, self.limits], device=self.device)
        if scatter:
            wp.launch(scatter_limits, dim=len(self.members), inputs=[self.members, self.groups, self.limits,
                      scales], device=self.device)
        wp.launch(record_limits, dim=len(self.limits), inputs=[self.limits, self.minimum_scale,
                  self.scale_counts], device=self.device)

    def synchronize(self, positions, reference, displacements):
        wp.launch(sync_bound, dim=len(self.vertices), inputs=[self.vertices, self.triangles, self.weights,
                  self.offsets, positions, reference, displacements, self.sweep_error], device=self.device)


@wp.kernel
def propose_bound(vertices: wp.array[int], triangles: wp.array[wp.vec3i], weights: wp.array[wp.vec3],
                  offsets: wp.array[wp.vec3], reference: wp.array[wp.vec3], displacements: wp.array[wp.vec3]):
    i = wp.tid()
    t = triangles[i]
    p, j0, j1, j2 = binding_point(reference[t[0]] + displacements[t[0]],
        reference[t[1]] + displacements[t[1]], reference[t[2]] + displacements[t[2]], weights[i], offsets[i])
    displacements[vertices[i]] = p - reference[vertices[i]]


@wp.kernel
def gather_limits(members: wp.array[int], groups: wp.array[int], scales: wp.array[float],
                  displacements: wp.array[wp.vec3], maximum: float, limits: wp.array[float]):
    i = wp.tid()
    v = members[i]
    scale = wp.min(scales[v], maximum / wp.max(wp.length(displacements[v]), 1.0e-20))
    wp.atomic_min(limits, groups[i], scale)


@wp.kernel
def scatter_limits(members: wp.array[int], groups: wp.array[int], limits: wp.array[float], scales: wp.array[float]):
    i = wp.tid()
    scales[members[i]] = limits[groups[i]]


@wp.kernel
def record_limits(limits: wp.array[float], minimum: wp.array[float], counts: wp.array[int]):
    i = wp.tid()
    wp.atomic_min(minimum, 0, limits[i])
    wp.atomic_add(counts, 0, 1)
    if limits[i] < 0.99999:
        wp.atomic_add(counts, 1, 1)


@wp.kernel
def prepare_increment(reference: wp.array[wp.vec3], positions: wp.array[wp.vec3],
                      displacements: wp.array[wp.vec3], accepted: wp.array[wp.vec3], increment: wp.array[wp.vec3]):
    i = wp.tid()
    accepted[i] = positions[i]
    increment[i] = reference[i] + displacements[i] - positions[i]


@wp.kernel
def apply_increment(accepted: wp.array[wp.vec3], increment: wp.array[wp.vec3], scales: wp.array[float],
                    positions: wp.array[wp.vec3], reference: wp.array[wp.vec3], displacements: wp.array[wp.vec3]):
    i = wp.tid()
    step = increment[i] * scales[i]
    increment[i] = step
    p = accepted[i] + step
    positions[i] = p
    displacements[i] = p - reference[i]


@wp.kernel
def make_increment(reference: wp.array[wp.vec3], positions: wp.array[wp.vec3],
                   displacements: wp.array[wp.vec3], increment: wp.array[wp.vec3]):
    i = wp.tid()
    increment[i] = reference[i] + displacements[i] - positions[i]


@wp.kernel
def limit_travel(reference: wp.array[wp.vec3], accepted: wp.array[wp.vec3], increment: wp.array[wp.vec3],
                 maximum: float, scales: wp.array[float]):
    i = wp.tid()
    if maximum < 1.0e10:
        a = accepted[i] - reference[i]
        d = increment[i]
        aa = wp.dot(d, d)
        if aa > 1.0e-24:
            bb = wp.dot(a, d)
            cc = wp.dot(a, a) - maximum * maximum
            root = (-bb + wp.sqrt(wp.max(0.0, bb * bb - aa * cc))) / aa
            scales[i] = wp.min(scales[i], wp.clamp(root, 0.0, 1.0))


@wp.kernel
def sync_displacements(positions: wp.array[wp.vec3], reference: wp.array[wp.vec3], displacements: wp.array[wp.vec3]):
    i = wp.tid()
    displacements[i] = positions[i] - reference[i]


@wp.kernel
def limit_bound_path(vertices: wp.array[int], triangles: wp.array[wp.vec3i], weights: wp.array[wp.vec3],
                     offsets: wp.array[wp.vec3], accepted: wp.array[wp.vec3], increment: wp.array[wp.vec3],
                     reference: wp.array[wp.vec3], groups: wp.array[int], limits: wp.array[float],
                     maximum: float, counts: wp.array[int]):
    i = wp.tid()
    t = triangles[i]
    e = accepted[t[1]] - accepted[t[0]]
    f = accepted[t[2]] - accepted[t[0]]
    de = increment[t[1]] - increment[t[0]]
    df = increment[t[2]] - increment[t[0]]
    c0 = wp.cross(e, f)
    area0 = wp.length(c0)
    normal0 = c0 / wp.max(area0, 1.0e-20)
    c1 = wp.cross(de, f) + wp.cross(e, df)
    c2 = wp.cross(de, df)
    a1 = wp.dot(c1, normal0)
    a2 = wp.dot(c2, normal0)
    alpha0 = limits[groups[i]]
    alpha = alpha0
    valid = bool(False)
    w = weights[i]
    d = offsets[i]
    speed_affine = wp.length(w[0] * increment[t[0]] + w[1] * increment[t[1]] + w[2] * increment[t[2]])
    start_distance = wp.length(accepted[vertices[i]] - reference[vertices[i]])
    for attempt in range(24):
        # Signed projected area is quadratic. Its minimum proves absence of a
        # degeneracy/normal flip anywhere along the whole support update.
        amin = wp.min(area0, area0 + a1 * alpha + a2 * alpha * alpha)
        if a2 > 0.0:
            critical = wp.clamp(-a1 / (2.0 * a2), 0.0, alpha)
            amin = wp.min(amin, area0 + a1 * critical + a2 * critical * critical)
        valid = area0 > 1.0e-16 and amin >= 0.5 * area0
        if valid and maximum < 1.0e10:
            de2 = wp.dot(de, de)
            te = wp.clamp(-wp.dot(e, de) / wp.max(de2, 1.0e-30), 0.0, alpha)
            emin = wp.length(e + te * de)
            du = wp.length(wp.cross(e, de)) / wp.max(emin * emin, 1.0e-30)
            normal_numerator = (wp.length(wp.cross(c0, c1)) + 2.0 * alpha * wp.length(wp.cross(c0, c2))
                                + alpha * alpha * wp.length(wp.cross(c1, c2)))
            dn = normal_numerator / wp.max(amin * amin, 1.0e-30)
            # |u'| = |e x de|/|e|^2, |n'| = |cross x cross'|/|cross|^2,
            # |(n x u)'| <= |n'|+|u'|. Integrating this speed bound
            # keeps every intermediate bound point in the collision query ball.
            speed = speed_affine + (wp.abs(d[0]) + wp.abs(d[1])) * du + (wp.abs(d[1]) + wp.abs(d[2])) * dn
            valid = start_distance + alpha * speed <= maximum * 0.99
        if valid:
            break
        alpha = alpha * 0.5
    if not valid:
        alpha = 0.0
    wp.atomic_min(limits, groups[i], alpha)
    wp.atomic_add(counts, 0, 1)
    if alpha < alpha0:
        wp.atomic_add(counts, 1, 1)
    if alpha == 0.0:
        wp.atomic_add(counts, 2, 1)


@wp.kernel
def sync_bound(vertices: wp.array[int], triangles: wp.array[wp.vec3i], weights: wp.array[wp.vec3],
               offsets: wp.array[wp.vec3], positions: wp.array[wp.vec3], reference: wp.array[wp.vec3],
               displacements: wp.array[wp.vec3], sweep_error: wp.array[float]):
    i = wp.tid()
    t = triangles[i]
    p, j0, j1, j2 = binding_point(positions[t[0]], positions[t[1]], positions[t[2]], weights[i], offsets[i])
    v = vertices[i]
    wp.atomic_max(sweep_error, 0, wp.length(p - positions[v]))
    positions[v] = p
    displacements[v] = p - reference[v]
