# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh averages and masked components using NumPy and CPU Warp."""
import numpy as np


def _unique_pairs(rows, columns, return_counts=False):
    pairs = np.column_stack([rows, columns])
    # Nonnegative mesh indices can be sorted as one integer without changing
    # the lexicographic pair order. Fall back before any overflow or narrowing.
    if (np.issubdtype(pairs.dtype, np.integer) and len(pairs)
            and int(pairs.min()) >= 0):
        width = int(pairs[:, 1].max()) + 1
        limit = np.iinfo(np.intp).max
        if width <= limit and int(pairs[:, 0].max()) <= (limit - width + 1) // width:
            code = pairs[:, 0].astype(np.intp) * width + pairs[:, 1].astype(np.intp)
            keys, counts = np.unique(code, return_counts=True)
            result = np.column_stack([keys // width, keys % width]).astype(pairs.dtype, copy=False)
            return (result, counts) if return_counts else result
    return np.unique(pairs, axis=0, return_counts=return_counts)


class RowMean:
    def __init__(self, rows, columns, nrows):
        pairs, multiplicity=_unique_pairs(rows,columns,return_counts=True)
        self.rows=pairs[:,0];self.columns=pairs[:,1];self.nrows=nrows
        degree=np.bincount(self.rows,weights=multiplicity,minlength=nrows)
        self.weights=multiplicity/np.maximum(degree[self.rows],1.)
        self.starts=np.r_[0,np.cumsum(np.bincount(self.rows,minlength=nrows))]
        self._native=None

    def __matmul__(self, values):
        values=np.asarray(values)
        if self.nrows and (values.ndim==1 or (values.ndim==2 and values.shape[1]==3)):
            try:
                import warp as wp
            except ImportError:
                pass  # The NumPy-only diagnostic tests can run without Warp.
            else:
                from spatial_native import mean_vector,mean_scalar
                if self._native is None:
                    self._native=[wp.array(self.starts,dtype=wp.int64,device='cpu'),
                                  wp.array(self.columns,dtype=wp.int32,device='cpu'),
                                  wp.array(self.weights,dtype=wp.float64,device='cpu')]
                dtype=wp.vec3d if values.ndim==2 else wp.float64
                data=wp.array(values,dtype=dtype,device='cpu')
                out=wp.empty(self.nrows,dtype=dtype,device='cpu')
                wp.launch(mean_vector if values.ndim==2 else mean_scalar,dim=self.nrows,
                          inputs=[*self._native,data,out],device='cpu')
                return out.numpy()
        weighted=values[self.columns]*self.weights.reshape((-1,)+(1,)*(values.ndim-1))
        result=np.zeros((self.nrows,)+values.shape[1:],dtype=weighted.dtype)
        present=np.flatnonzero(np.diff(self.starts))
        if len(present):result[present]=np.add.reduceat(weighted,self.starts[present],axis=0)
        return result

    def components(self, mask):
        vertices=np.flatnonzero(mask);labels=np.full(self.nrows,-1,np.intp);number=0
        for vertex in vertices:
            if labels[vertex]>=0:continue
            labels[vertex]=number;todo=[int(vertex)]
            while todo:
                current=todo.pop()
                for neighbor in self.columns[self.starts[current]:self.starts[current+1]]:
                    if mask[neighbor] and labels[neighbor]<0:
                        labels[neighbor]=number;todo.append(int(neighbor))
            number+=1
        return number,labels[vertices]


def mesh_averages(faces,nvertices):
    faces=np.asarray(faces,np.intp)
    edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]])
    directed=_unique_pairs(np.r_[edges[:,0],edges[:,1]],np.r_[edges[:,1],edges[:,0]])
    adjacency=RowMean(directed[:,0],directed[:,1],nvertices)
    face_mean=RowMean(faces.ravel(),np.repeat(np.arange(len(faces)),3),nvertices)
    return adjacency,face_mean
