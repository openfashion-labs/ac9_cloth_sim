# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact Euclidean point queries using NumPy and CPU Warp kernels.

The solver needs k <= 16, scalar radii, and no approximation or periodic axes.
Distance ties use original point index; callers must not assume a geometric
normal is unique at an equidistant sample. Memory is bounded by query batches.
"""
import numpy as np


class KDTree:
    def __init__(self, data, leafsize=32):
        self.data = np.array(data, dtype=np.float64, copy=True)
        if self.data.ndim != 2 or self.data.shape[1] == 0 or not np.isfinite(self.data).all():
            raise ValueError('Expected finite points with shape (n, dimension)')
        self.n, self.m = self.data.shape
        self.nodes = []

        def build(indices):
            points = self.data[indices]
            lower, upper = points.min(0), points.max(0)
            node = len(self.nodes)
            self.nodes.append(None)
            if len(indices) <= max(2, int(leafsize)):
                self.nodes[node] = (lower, upper, indices, -1, -1, 0, 0.)
            else:
                axis = int(np.argmax(upper-lower)); half = len(indices)//2
                order = np.argpartition(points[:, axis], half)
                indices = indices[order]
                split = float(self.data[indices[half], axis])
                left, right = build(indices[:half]), build(indices[half:])
                self.nodes[node] = (lower, upper, None, left, right, axis, split)
            return node
        self.root = build(np.arange(self.n)) if self.n else -1
        self._native = None

    def _native_inputs(self):
        import warp as wp
        if self._native is None:
            lo, hi, left, right, begin, count, order = [], [], [], [], [], [], []
            for lower, upper, indices, a, b, axis, split in self.nodes:
                lo.append(np.pad(lower, (0, 3-self.m)))
                hi.append(np.pad(upper, (0, 3-self.m)))
                left.append(a); right.append(b); begin.append(len(order))
                count.append(0 if indices is None else len(indices))
                if indices is not None: order.extend(indices)
            self._native = [wp.array(np.pad(self.data, ((0,0),(0,3-self.m))),dtype=wp.vec3d,device='cpu'),
                            wp.array(order,dtype=wp.int32,device='cpu'),
                            wp.array(lo,dtype=wp.vec3d,device='cpu'),wp.array(hi,dtype=wp.vec3d,device='cpu')]
            self._native += [wp.array(v,dtype=wp.int32,device='cpu') for v in (left,right,begin,count)]
        return self._native

    def _points(self, x):
        points = np.asarray(x, dtype=np.float64)
        if points.ndim == 0 or points.shape[-1] != self.m or not np.isfinite(points).all():
            raise ValueError('Query points must be finite and match the point dimension')
        return points, points.reshape(-1, self.m)

    def query(self, x, k=1, distance_upper_bound=np.inf):
        points, flat = self._points(x)
        if not isinstance(k, (int, np.integer)) or k < 1:
            raise ValueError('k must be a positive integer')
        if distance_upper_bound < 0 or np.isnan(distance_upper_bound):
            raise ValueError('Expected a nonnegative distance bound')
        if self.n and len(flat) and self.m <= 3 and k <= 16:
            import warp as wp
            from spatial_native import nearest
            qs=wp.array(np.pad(flat,((0,0),(0,3-self.m))),dtype=wp.vec3d,device='cpu')
            dd=wp.empty((len(flat),k),dtype=wp.float64,device='cpu')
            ii=wp.empty((len(flat),k),dtype=wp.int32,device='cpu')
            wp.launch(nearest,dim=len(flat),inputs=[*self._native_inputs(),qs,k,float(distance_upper_bound)**2,self.n,dd,ii],device='cpu')
            shape=points.shape[:-1]+((k,) if k!=1 else ())
            return dd.numpy().reshape(shape),ii.numpy().astype(np.intp).reshape(shape)
        distances = np.full((len(flat), k), np.inf)
        indices = np.full((len(flat), k), self.n, dtype=np.intp)
        bound = float(distance_upper_bound)**2
        for start in range(0, len(flat), 2048):
            q = flat[start:start+2048]
            best = np.full((len(q), k), bound)
            ids = np.full((len(q), k), self.n, dtype=np.intp)

            def leaf(node, rows):
                pi = self.nodes[node][2]
                delta = q[rows, None, :] - self.data[pi][None, :, :]
                ds = np.sum(delta*delta, axis=2)
                candidates = np.broadcast_to(pi, ds.shape)
                ds = np.concatenate((best[rows], ds), axis=1)
                candidates = np.concatenate((ids[rows], candidates), axis=1)
                order = np.lexsort((candidates, ds), axis=1)[:, :k]
                best[rows] = np.take_along_axis(ds, order, axis=1)
                ids[rows] = np.take_along_axis(candidates, order, axis=1)

            # Seed each query with its own leaf, tightening bounds before the
            # global walk. k larger than that leaf is completed by the walk.
            def seed(node, rows):
                if not len(rows): return
                lo, hi, pi, left, right, axis, split = self.nodes[node]
                if pi is not None:
                    leaf(node, rows); return
                side = q[rows, axis] < split
                seed(left, rows[side]); seed(right, rows[~side])

            if self.n:
                # Use only seed distances, not seed indices, so the exact walk
                # inserts every source point once (including coincident ones).
                seed(self.root, np.arange(len(q)))
                ceiling = best[:, -1].copy()
                best[:] = bound; ids[:] = self.n

                def walk(node, rows):
                    if not len(rows): return
                    lo, hi, pi, left, right, axis, split = self.nodes[node]
                    delta = np.maximum(np.maximum(lo-q[rows], q[rows]-hi), 0.)
                    limit = np.minimum(ceiling[rows], best[rows, -1])
                    # Roundoff in bbox sums must not prune a boundary candidate.
                    rows = rows[np.sum(delta*delta, axis=1) <= np.nextafter(limit, np.inf)]
                    if not len(rows): return
                    if pi is not None: leaf(node, rows)
                    else:
                        walk(left, rows); walk(right, rows)
                walk(self.root, np.arange(len(q)))
            valid = (best < bound) & (ids < self.n)
            distances[start:start+len(q)] = np.where(valid, np.sqrt(best), np.inf)
            indices[start:start+len(q)] = np.where(valid, ids, self.n)
        shape = points.shape[:-1] + ((k,) if k != 1 else ())
        return distances.reshape(shape), indices.reshape(shape)

    def query_ball_point(self, x, r):
        points, flat = self._points(x)
        radii = np.broadcast_to(np.asarray(r, float), points.shape[:-1]).reshape(-1)
        if np.any(radii < 0) or np.isnan(radii).any():
            raise ValueError('Expected nonnegative radii')
        if self.n and len(flat) and self.m <= 3:
            import warp as wp
            from spatial_native import ball
            qs=wp.array(np.pad(flat,((0,0),(0,3-self.m))),dtype=wp.vec3d,device='cpu')
            rr=wp.array(radii*radii,dtype=wp.float64,device='cpu')
            counts=wp.empty(len(flat),dtype=wp.int64,device='cpu')
            offsets=wp.zeros(len(flat),dtype=wp.int64,device='cpu')
            output=wp.empty(0,dtype=wp.int32,device='cpu')
            inputs=[*self._native_inputs(),qs,rr,offsets,False,counts,output]
            wp.launch(ball,dim=len(flat),inputs=inputs,device='cpu')
            ends=np.cumsum(counts.numpy()); starts=np.r_[0,ends[:-1]]
            offsets=wp.array(starts,dtype=wp.int64,device='cpu')
            output=wp.empty(int(ends[-1]),dtype=wp.int32,device='cpu')
            wp.launch(ball,dim=len(flat),inputs=[*self._native_inputs(),qs,rr,offsets,True,counts,output],device='cpu')
            values=output.numpy()
            result=[sorted(values[a:b].tolist()) for a,b in zip(starts,ends)]
            if points.ndim==1:return result[0]
            out=np.empty(len(result),dtype=object);out[:]=result
            return out.reshape(points.shape[:-1])
        result = [[] for _ in flat]
        for start in range(0, len(flat), 2048):
            q = flat[start:start+2048]; rr = radii[start:start+2048]**2
            def walk(node, rows):
                if not len(rows): return
                lo, hi, pi, left, right, axis, split = self.nodes[node]
                delta = np.maximum(np.maximum(lo-q[rows], q[rows]-hi), 0.)
                rows = rows[np.sum(delta*delta, axis=1) <= np.nextafter(rr[rows], np.inf)]
                if not len(rows): return
                if pi is None:
                    walk(left, rows); walk(right, rows)
                else:
                    delta = q[rows, None, :] - self.data[pi][None, :, :]
                    mask = np.sum(delta*delta, axis=2) <= rr[rows, None]
                    for row, keep in zip(rows, mask):
                        result[start+row].extend(pi[keep].tolist())
            if self.n: walk(self.root, np.arange(len(q)))
        for row in result: row.sort()
        if points.ndim == 1: return result[0]
        out = np.empty(len(result), dtype=object)
        out[:] = result
        return out.reshape(points.shape[:-1])


cKDTree = KDTree
