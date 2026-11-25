# SPDX-License-Identifier: GPL-3.0-or-later
"""Edges of one piece passing through triangles of another piece, on the GPU (cfg 'untangle_free').

The same test as crossings.crossing_edges (an edge crosses a triangle when its two ends are on opposite sides of the
triangle's plane and the point where it meets the plane is inside the triangle), searched through a BVH of the
triangles instead of a tree built on the CPU. The CPU search built a pure-Python tree once per piece every frame:
about 1.7 s a frame on a 35,000-vertex coat, whatever the number of crossings; this is a few ms.

  CrossingSearch(tri, edges, vert_piece, device) -> .run(q) -> (edge_hit bool[E], tri_hit bool[T])
q: a warp vec3 array of the vertex positions (the solver's particle_q) or a numpy (n, 3) array."""
import numpy as np
import warp as wp


@wp.kernel
def _tri_bounds(q: wp.array(dtype=wp.vec3), tri: wp.array2d(dtype=int), lo: wp.array(dtype=wp.vec3),
                hi: wp.array(dtype=wp.vec3)):
    t = wp.tid()
    a = q[tri[t, 0]]
    b = q[tri[t, 1]]
    c = q[tri[t, 2]]
    lo[t] = wp.min(wp.min(a, b), c)
    hi[t] = wp.max(wp.max(a, b), c)


@wp.kernel
def _edge_cross(bvh: wp.uint64, q: wp.array(dtype=wp.vec3), tri: wp.array2d(dtype=int),
                edges: wp.array2d(dtype=int), vp: wp.array(dtype=int), e_hit: wp.array(dtype=int),
                t_hit: wp.array(dtype=int)):
    e = wp.tid()
    i0 = edges[e, 0]
    i1 = edges[e, 1]
    p = q[i0]
    r = q[i1]
    pa = vp[i0]
    lo = wp.min(p, r)
    hi = wp.max(p, r)
    query = wp.bvh_query_aabb(bvh, lo, hi)
    t = int(0)
    while wp.bvh_query_next(query, t):
        a0 = tri[t, 0]
        if vp[a0] == pa:            # only crossings with another piece (as the CPU search)
            continue
        a = q[a0]
        b = q[tri[t, 1]]
        c = q[tri[t, 2]]
        n = wp.cross(b - a, c - a)
        dp = wp.dot(p - a, n)
        dq = wp.dot(r - a, n)
        if not (dp * dq < 0.0):
            continue
        s = dp / (dp - dq)
        x = p + s * (r - p)
        if wp.dot(wp.cross(b - a, x - a), n) < 0.0:
            continue
        if wp.dot(wp.cross(c - b, x - b), n) < 0.0:
            continue
        if wp.dot(wp.cross(a - c, x - c), n) < 0.0:
            continue
        e_hit[e] = 1
        t_hit[t] = 1


class CrossingSearch:
    def __init__(self, tri, edges, vert_piece, device=None):
        self.device = wp.get_device(device)
        self.tri = wp.array(np.asarray(tri, np.int32), dtype=int, device=self.device)
        self.edges = wp.array(np.asarray(edges, np.int32), dtype=int, device=self.device)
        self.vp = wp.array(np.asarray(vert_piece, np.int32), dtype=int, device=self.device)
        nt, ne = len(tri), len(edges)
        self.lo = wp.zeros(nt, dtype=wp.vec3, device=self.device)
        self.hi = wp.zeros(nt, dtype=wp.vec3, device=self.device)
        self.e_hit = wp.zeros(ne, dtype=int, device=self.device)
        self.t_hit = wp.zeros(nt, dtype=int, device=self.device)
        self.bvh = None

    def run(self, q):
        if not isinstance(q, wp.array):
            q = wp.array(np.asarray(q, np.float32), dtype=wp.vec3, device=self.device)
        wp.launch(_tri_bounds, dim=self.tri.shape[0], inputs=[q, self.tri, self.lo, self.hi], device=self.device)
        if self.bvh is None:
            self.bvh = wp.Bvh(self.lo, self.hi)
        else:
            self.bvh.refit()
        self.e_hit.zero_(); self.t_hit.zero_()
        wp.launch(_edge_cross, dim=self.edges.shape[0],
                  inputs=[self.bvh.id, q, self.tri, self.edges, self.vp, self.e_hit, self.t_hit], device=self.device)
        return self.e_hit.numpy().astype(bool), self.t_hit.numpy().astype(bool)
