# SPDX-License-Identifier: GPL-3.0-or-later
"""Ordered host topology for model preparation; no physical parameters are changed."""
from contextlib import contextmanager
import numpy as np


def _entries(faces):
    f=np.asarray(faces).reshape(-1,3)
    return f[:,[0,1,2,1,2,0,2,0,1]].reshape(-1,3)


def _edge_keys(entries):
    a=np.minimum(entries[:,0],entries[:,1]).astype(np.int64)
    b=np.maximum(entries[:,0],entries[:,1]).astype(np.int64)
    width=int(max(a.max(initial=0),b.max(initial=0)))+1
    if np.any(a<0) or int(a.max(initial=0))>(np.iinfo(np.int64).max-width+1)//width:
        return None
    return a*width+b


def boundary_edges(faces):
    entries=_entries(faces);keys=_edge_keys(entries)
    if keys is None:
        table={}
        for a,b,c in entries.tolist():table.setdefault(frozenset((a,b)),[]).append((a,b,c))
        return {k:v[0] for k,v in table.items() if len(v)==1}
    _,first,counts=np.unique(keys,return_index=True,return_counts=True)
    indices=np.sort(first[counts==1])
    return {frozenset((int(a),int(b))):(int(a),int(b),int(c)) for a,b,c in entries[indices]}


def cloth_edges(faces):
    """Newton 1.6 first-occurrence edge order; None requests its non-manifold path."""
    entries=_entries(np.asarray(faces,np.int32));keys=_edge_keys(entries)
    if keys is None:return None
    _,first,inverse,counts=np.unique(keys,return_index=True,return_inverse=True,return_counts=True)
    if np.any(counts>2):return None
    order=np.argsort(first,kind='stable');first=first[order]
    edges=np.full((len(order),4),-1,np.int32)
    edges[:,0]=entries[first,2];edges[:,2:]=entries[first,:2]
    if len(entries):
        grouped=np.argsort(inverse,kind='stable');starts=np.r_[0,np.cumsum(counts)]
        has_second=counts[order]==2
        second=grouped[starts[order[has_second]]+1]
        edges[has_second,1]=entries[second,2]
    return edges


@contextmanager
def bulk_cloth_edges(builder):
    """Only the temporary builder is wrapped, and only its edge-only request is specialized."""
    name='_add_soft_mesh_edges_from_triangles';original=getattr(builder,name)
    if name in vars(builder) or getattr(original,'__module__','')!='newton._src.sim.builder':
        yield;return
    def add(start_tri,end_tri,*,edge_ke=None,edge_kd=None,custom_attributes=None):
        if custom_attributes is not None:
            return original(start_tri,end_tri,edge_ke=edge_ke,edge_kd=edge_kd,custom_attributes=custom_attributes)
        edges=cloth_edges(builder.tri_indices[start_tri:end_tri])
        if edges is None:
            return original(start_tri,end_tri,edge_ke=edge_ke,edge_kd=edge_kd,custom_attributes=custom_attributes)
        start=len(builder.edge_indices)
        if len(edges):
            builder.add_edges(*edges.T,edge_ke=builder._expand_edge_parameter(edge_ke,len(edges)),
                edge_kd=builder._expand_edge_parameter(edge_kd,len(edges)))
        return range(start,len(builder.edge_indices))
    setattr(builder,name,add)
    try:yield
    finally:delattr(builder,name)


class IndexRows:
    def __init__(self,vertices,values,count):
        valid=(vertices>=0)&(vertices<count)
        vertices,values=vertices[valid],values[valid]
        order=np.argsort(vertices,kind='stable')
        self.values=values[order].tolist()
        self.starts=np.r_[0,np.cumsum(np.bincount(vertices,minlength=count))].tolist()
    def __getitem__(self,v):return self.values[self.starts[v]:self.starts[v+1]]


class TriangleRings:
    def __init__(self,faces,triangles):self.faces,self.triangles,self.saved=faces,triangles,{}
    def __getitem__(self,v):
        if v not in self.saved:
            result=set();last=-1
            for t in self.triangles[v]:
                if t==last:continue
                last=t;i,j,k=self.faces[t]
                if i==v:result.update((j,k))
                if j==v:result.update((i,k))
                if k==v:result.update((i,j))
            self.saved[v]=result
        return self.saved[v]


def triangle_rows(faces,count):
    triangles=IndexRows(faces.reshape(-1),np.repeat(np.arange(len(faces)),3),count)
    return triangles,TriangleRings(faces,triangles)


def edge_rows(edges,count):
    return IndexRows(edges.reshape(-1),np.repeat(np.arange(len(edges)),2),count)
