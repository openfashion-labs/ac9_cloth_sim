# SPDX-License-Identifier: GPL-3.0-or-later
"""CSR adjacency with legacy insertion order replayed only for ordered consumers."""
import numpy as np
from job_cancel import check as _cancel_check
from native_stencil_arrays import independent_rows


class _OrderedRow:
    def __init__(self, graph, vertex):
        self.graph, self.vertex = graph, vertex

    def __len__(self):
        return self.graph.degree[self.vertex]

    def __iter__(self):
        return iter(self.graph.ordered(self.vertex))


class ColoringNeighbors:
    def __init__(self, rows, count, active, followers, supports):
        self.count = count
        self.support = {int(v): list(map(int, t)) for v, t in zip(followers, supports)}
        self.active_set = set(map(int, active))
        mask = np.zeros(count, bool); mask[active] = True
        width = max((r.shape[1] for r in rows), default=0)
        self.rows = np.concatenate([np.pad(r, ((0, 0), (0, width-r.shape[1])), constant_values=-1) for r in rows])
        nodes = independent_rows(self.rows, mask, followers, supports)
        pairs = []
        for i in range(nodes.shape[1]):
            for j in range(i+1, nodes.shape[1]):
                _cancel_check()
                valid = (nodes[:, i] >= 0) & (nodes[:, j] >= 0)
                a, b = nodes[valid, i].astype(np.int64), nodes[valid, j].astype(np.int64)
                if len(a):
                    pairs.extend((a*count+b, b*count+a))
        keys = np.unique(np.concatenate(pairs)) if pairs else np.empty(0, np.int64)
        self.src = (keys//count).astype(np.int32)
        self.dst = (keys%count).astype(np.int32)
        self.offsets = np.r_[0, np.cumsum(np.bincount(self.src, minlength=count))]
        self.starts = self.offsets.tolist()
        self.degree = np.diff(self.offsets).tolist()
        self.values = self.dst.tolist()
        row_ids, slots = np.nonzero(nodes >= 0)
        vertices = nodes[row_ids, slots]
        order = np.argsort(vertices, kind='stable')
        self.incident_ids = row_ids[order]
        self.incident_offsets = np.r_[0, np.cumsum(np.bincount(vertices, minlength=count))]
        self._ordered = {}

    def __len__(self):
        return self.count

    def __getitem__(self, vertex):
        if vertex < 0 or vertex >= self.count:
            raise IndexError(vertex)
        saved = self._ordered.get(vertex)
        return saved if saved is not None else _OrderedRow(self, int(vertex))

    def fast(self, vertex):
        return self.values[self.starts[vertex]:self.starts[vertex+1]]

    def ordered(self, vertex):
        saved = self._ordered.get(vertex)
        if saved is not None:
            return saved
        result = set()
        a, b = self.incident_offsets[vertex:vertex+2]
        for index in self.incident_ids[a:b]:
            ids = set()
            for value in self.rows[index]:
                if value >= 0:
                    ids.update(self.support.get(int(value), [int(value)]))
            ids.intersection_update(self.active_set)
            # Preserve the exact original update operations, including duplicates.
            result.update(ids-{vertex})
        self._ordered[vertex] = result
        return result
