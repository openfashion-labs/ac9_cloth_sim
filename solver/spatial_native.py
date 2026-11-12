# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU Warp point searches and mesh averages. No CUDA allocation or capture."""
import warp as wp

Stack = wp.types.vector(length=64, dtype=wp.int32)
Distances = wp.types.vector(length=16, dtype=wp.float64)
Indices = wp.types.vector(length=16, dtype=wp.int32)


@wp.func
def box_distance(q: wp.vec3d, lo: wp.vec3d, hi: wp.vec3d):
    d = wp.max(wp.max(lo-q, q-hi), wp.vec3d(0.0))
    return wp.dot(d, d)


@wp.kernel
def nearest(points: wp.array(dtype=wp.vec3d), order: wp.array(dtype=wp.int32),
            lower: wp.array(dtype=wp.vec3d), upper: wp.array(dtype=wp.vec3d),
            left: wp.array(dtype=wp.int32), right: wp.array(dtype=wp.int32),
            begin: wp.array(dtype=wp.int32), count: wp.array(dtype=wp.int32),
            queries: wp.array(dtype=wp.vec3d), k: int, bound: wp.float64, n: int,
            output_d: wp.array2d(dtype=wp.float64), output_i: wp.array2d(dtype=wp.int32)):
    tid = wp.tid(); q = queries[tid]
    ds = Distances(bound); ids = Indices(n)
    stack = Stack(0); size = int(1)
    while size > 0:
        size -= 1; node = stack[size]
        # Conservative box test; exact point distances decide membership.
        tolerance = wp.float64(1.e-14)*wp.max(wp.float64(1.0), ds[k-1])
        if box_distance(q, lower[node], upper[node]) > ds[k-1]+tolerance:
            continue
        if count[node] > 0:
            for offset in range(count[node]):
                index = order[begin[node]+offset]
                delta = q-points[index]; distance = wp.dot(delta, delta)
                if distance >= bound:
                    continue
                if distance < ds[k-1] or (distance == ds[k-1] and index < ids[k-1]):
                    position = k-1
                    while position > 0:
                        if distance > ds[position-1] or (distance == ds[position-1] and index >= ids[position-1]):
                            break
                        ds[position] = ds[position-1]; ids[position] = ids[position-1]
                        position -= 1
                    ds[position] = distance; ids[position] = index
        else:
            a = left[node]; b = right[node]
            if box_distance(q, lower[a], upper[a]) > box_distance(q, lower[b], upper[b]):
                temp = a; a = b; b = temp
            stack[size] = b; stack[size+1] = a; size += 2
    for j in range(k):
        output_i[tid,j] = ids[j]
        if ids[j] < n: output_d[tid,j] = wp.sqrt(ds[j])
        else: output_d[tid,j] = wp.float64(wp.inf)


@wp.kernel
def ball(points: wp.array(dtype=wp.vec3d), order: wp.array(dtype=wp.int32),
         lower: wp.array(dtype=wp.vec3d), upper: wp.array(dtype=wp.vec3d),
         left: wp.array(dtype=wp.int32), right: wp.array(dtype=wp.int32),
         begin: wp.array(dtype=wp.int32), count: wp.array(dtype=wp.int32),
         queries: wp.array(dtype=wp.vec3d), radii: wp.array(dtype=wp.float64),
         offsets: wp.array(dtype=wp.int64), write: bool,
         counts: wp.array(dtype=wp.int64), output: wp.array(dtype=wp.int32)):
    tid=wp.tid(); q=queries[tid]; radius=radii[tid]; found=wp.int64(0)
    stack=Stack(0); size=int(1)
    while size>0:
        size-=1; node=stack[size]
        tolerance=wp.float64(1.e-14)*wp.max(wp.float64(1.0),radius)
        if box_distance(q,lower[node],upper[node])>radius+tolerance: continue
        if count[node]>0:
            for j in range(count[node]):
                index=order[begin[node]+j];delta=q-points[index]
                if wp.dot(delta,delta)<=radius:
                    if write: output[offsets[tid]+found]=index
                    found+=wp.int64(1)
        else:
            stack[size]=left[node];stack[size+1]=right[node];size+=2
    counts[tid]=found


@wp.kernel
def mean_vector(starts:wp.array(dtype=wp.int64),columns:wp.array(dtype=wp.int32),weights:wp.array(dtype=wp.float64),
                values:wp.array(dtype=wp.vec3d),output:wp.array(dtype=wp.vec3d)):
    row=wp.tid();total=wp.vec3d(0.)
    j=starts[row]
    while j<starts[row+1]:
        total+=weights[j]*values[columns[j]];j+=wp.int64(1)
    output[row]=total


@wp.kernel
def mean_scalar(starts:wp.array(dtype=wp.int64),columns:wp.array(dtype=wp.int32),weights:wp.array(dtype=wp.float64),
                values:wp.array(dtype=wp.float64),output:wp.array(dtype=wp.float64)):
    row=wp.tid();total=wp.float64(0.)
    j=starts[row]
    while j<starts[row+1]:
        total+=weights[j]*values[columns[j]];j+=wp.int64(1)
    output[row]=total
