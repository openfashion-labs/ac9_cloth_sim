# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-contact topological filters of Newton's TriMeshCollisionDetector, built with numpy instead of a Python loop.

Newton 1.6.0 builds the vertex-triangle and edge-edge "n-ring" filters (contacts between cloth elements a ring or two
apart on the mesh are not contacts) one vertex / one edge at a time in Python, then sorts every row into a CSR list.
On a 19 000-vertex T-shirt stage that took 3-6 s of every stage's start, every stage (each stage has its own model),
more than half of the wait before the cloth moved (experiments/sim_latency_2026-10-20). Here the same sets are made
from index arrays: for threshold 2 (the solver's default topo_filter) the lists come out identical, element for
element, to Newton's (sorted rows, int32), so the simulation does not change; any other threshold, or no adjacency, goes
to Newton's own code as before.

install(module) replaces the two builder methods of module.TriMeshCollisionDetector. Env AC9_NRING_CHECK=1 also runs
Newton's code and stops on the first difference (the test of this file).
"""
import os

import numpy as np


def _entries(vals, offs):
    """(row, id, slot) of every entry of an interleaved (id, slot) CSR as Newton's MeshAdjacency stores it."""
    vals = np.asarray(vals, np.int64)
    offs = np.asarray(offs, np.int64)
    n = len(offs) - 1
    cnt = (offs[1:] - offs[:-1]) // 2
    row = np.repeat(np.arange(n, dtype=np.int64), cnt)
    flat = vals[offs[0]:offs[-1]]
    return row, flat[0::2], flat[1::2]


def _join(a_row, a_val, b_row, b_val, n_rows):
    """For every pair (r, x) of a and every (x, y) of b: (r, y). b is grouped by its row (sorted)."""
    order = np.argsort(b_row, kind='stable')
    b_row, b_val = b_row[order], b_val[order]
    cnt = np.bincount(b_row, minlength=n_rows)
    start = np.zeros(n_rows + 1, np.int64)
    np.cumsum(cnt, out=start[1:])
    rep = cnt[a_val]
    tot = int(rep.sum())
    own = np.repeat(a_row, rep)
    base = np.repeat(start[a_val], rep)
    step = np.arange(tot, dtype=np.int64) - np.repeat(np.cumsum(rep) - rep, rep)
    return own, b_val[base + step]


def _one_ring(P, ei, vae, vae_off):
    """Pairs (v, u), u != v, of vertices sharing a collision edge (Newton's one_ring_vertices for every v), and the
    endpoint pairs (v, e) the ring came from (the entries with slot >= 2)."""
    v, e, slot = _entries(vae, vae_off)
    m = slot >= 2
    v, e = v[m], e[m]
    u = np.r_[ei[e, 2], ei[e, 3]]
    vv = np.r_[v, v]
    k = vv != u
    key = np.unique(vv[k] * P + u[k])
    return key // P, key % P, v, e


def _csr(own, val, n_rows, width, extra=None):
    """Sorted unique (own, val) pairs as Newton's set_to_csr output (int32 flat values and offsets); extra = an
    external {row: set} map merged in, as Newton's builders do."""
    if extra:
        rr = np.fromiter((r for r, s in extra.items() for _ in s), np.int64)
        xx = np.fromiter((x for s in extra.values() for x in s), np.int64)
        own, val = np.r_[own, rr], np.r_[val, xx]
    key = np.sort(own * width + val)              # sort + drop repeats: np.unique's hashing took 3x as long
    key = key[np.r_[True, key[1:] != key[:-1]]] if len(key) else key
    rows = key // width
    offs = np.zeros(n_rows + 1, np.int32)
    np.cumsum(np.bincount(rows, minlength=n_rows), out=offs[1:])
    return (key % width).astype(np.int32), offs


def vertex_triangle(P, ei, vae, vae_off, vat, vat_off, tri_idx, extra=None):
    """Threshold 2: per vertex v, the triangles of v's one-ring vertices, less the triangles of v."""
    rv, ru, _, _ = _one_ring(P, ei, vae, vae_off)
    tv, tt, _ = _entries(vat, vat_off)
    own, t = _join(rv, ru, tv, tt, P)
    keep = (tri_idx[t] != own[:, None]).all(1)
    return _csr(own[keep], t[keep], P, max(len(tri_idx), 1), extra)


def edge_edge(P, ei, vae, vae_off, extra=None):
    """Threshold 2: per edge (v0, v1), the edges that have an endpoint among v0's and v1's one-ring vertices (other
    than v0, v1), less every edge v0 or v1 belongs to (any of its four slots)."""
    E = len(ei)
    rv, ru, epv, epe = _one_ring(P, ei, vae, vae_off)
    v0, v1 = ei[:, 2].astype(np.int64), ei[:, 3].astype(np.int64)
    ids = np.arange(E, dtype=np.int64)
    oe, w = _join(np.r_[ids, ids], np.r_[v0, v1], rv, ru, P)
    k = (w != v0[oe]) & (w != v1[oe])
    oe, w = oe[k], w[k]
    own, g = _join(oe, w, epv, epe, P)
    eg = ei[g]
    keep = ~((eg == v0[own][:, None]) | (eg == v1[own][:, None])).any(1)
    return _csr(own[keep], g[keep], E, max(E, 1), extra)


def install(tmc):
    """Replace the two filter builders of tmc.TriMeshCollisionDetector (tmc = newton._src.geometry.tri_mesh_collision)."""
    import warp as wp
    D = tmc.TriMeshCollisionDetector
    if getattr(D, '_ac9_nring', False):
        return
    orig_vt, orig_ee = D._build_vertex_triangle_filter, D._build_edge_edge_filter
    check = os.environ.get('AC9_NRING_CHECK') == '1'

    def _same(a, b, what):
        a = (a[0].numpy(), a[1].numpy()) if a[0] is not None else None
        if a is None or not (np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])):
            raise RuntimeError(f"nring_filter: {what} differs from Newton's")

    def vt(self, thr, ext, adjacency):
        if thr != 2 or adjacency is None:
            return orig_vt(self, thr, ext, adjacency)
        ei, vae, vae_off, vat, vat_off = adjacency
        flat, offs = vertex_triangle(self.model.particle_count, np.asarray(ei, np.int64), vae, vae_off, vat, vat_off,
                                     self.model.tri_indices.numpy().astype(np.int64), ext)
        if check:
            orig_vt(self, thr, ext, adjacency)
            _same((self.vertex_triangle_filtering_list, self.vertex_triangle_filtering_list_offsets), (flat, offs),
                  'vertex-triangle filter')
        self.vertex_triangle_filtering_list = wp.array(flat, dtype=wp.int32, device=self.device)
        self.vertex_triangle_filtering_list_offsets = wp.array(offs, dtype=wp.int32, device=self.device)

    def ee(self, thr, ext, adjacency):
        if thr != 2 or adjacency is None:
            return orig_ee(self, thr, ext, adjacency)
        ei, vae, vae_off, _, _ = adjacency
        flat, offs = edge_edge(self.model.particle_count, np.asarray(ei, np.int64), vae, vae_off, ext)
        if check:
            orig_ee(self, thr, ext, adjacency)
            _same((self.edge_filtering_list, self.edge_filtering_list_offsets), (flat, offs), 'edge-edge filter')
        self.edge_filtering_list = wp.array(flat, dtype=wp.int32, device=self.device)
        self.edge_filtering_list_offsets = wp.array(offs, dtype=wp.int32, device=self.device)

    D._build_vertex_triangle_filter, D._build_edge_edge_filter = vt, ee
    D._ac9_nring = True


def restore_fold_contacts(detector, bound, pieces, vertex_extra=None, edge_extra=None):
    """Remove mesh-neighbor exclusions across a dependent fold, preserving seam exclusions.

The folded sides can be near on the original flat topology but are independent
collision surfaces. Incident primitives remain excluded by Newton itself.
"""
    import warp as wp
    bound, pieces = np.asarray(bound, bool), np.asarray(pieces, int)
    # particles after the cloth's (the seams' Pull Round targets) are in no piece and bound to nothing (2026-11-01:
    # their rows made the arrays disagree and stopped every run with a one-layer fold and a seam pulled round)
    n_extra = max(int(detector.model.particle_count) - len(bound), 0)
    bound, pieces = np.r_[bound, np.zeros(n_extra, bool)], np.r_[pieces, -np.ones(n_extra, int)]
    tri = detector.model.tri_indices.numpy()
    edge = detector.model.edge_indices.numpy()[:, 2:4]
    report = {}
    for kind, val_attr, off_attr, n, width, extra in (
        ('vertex_triangle', 'vertex_triangle_filtering_list', 'vertex_triangle_filtering_list_offsets',
         len(bound), len(tri), vertex_extra),
        ('edge_edge', 'edge_filtering_list', 'edge_filtering_list_offsets', len(edge), len(edge), edge_extra)):
        values_array = getattr(detector, val_attr)
        offsets_array = getattr(detector, off_attr)
        values = values_array.numpy() if values_array is not None else np.zeros(0, np.int32)
        offsets = offsets_array.numpy() if offsets_array is not None else np.zeros(n + 1, np.int32)
        rows = np.repeat(np.arange(n), np.diff(offsets))
        if kind == 'vertex_triangle':
            same = (pieces[rows] >= 0) & (pieces[rows] == pieces[tri[values, 0]])
            cross = bound[rows] != bound[tri[values]].any(1)
        else:
            same = (pieces[edge[rows, 0]] >= 0) & (pieces[edge[rows, 0]] == pieces[edge[values, 0]])
            cross = bound[edge[rows]].any(1) != bound[edge[values]].any(1)
        keep = ~(same & cross)
        flat, off = _csr(rows[keep], values[keep], n, max(width, 1), extra)
        setattr(detector, val_attr, wp.array(flat, dtype=wp.int32, device=detector.device))
        setattr(detector, off_attr, wp.array(off, dtype=wp.int32, device=detector.device))
        report[kind + '_restored'] = int(len(values) - len(flat))
    return report
