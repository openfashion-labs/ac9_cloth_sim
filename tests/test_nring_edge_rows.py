"""Edge filter against an independent set definition, including dense-valence fallback."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'solver'))
import nring_filter as N
from native_mesh_prepare import cloth_edges


def adjacency(count,edges):
    rows=[[] for _ in range(count)]
    for edge,values in enumerate(edges):
        for slot,v in enumerate(values):
            if v>=0:rows[v].extend((edge,slot))
    return np.asarray([x for row in rows for x in row],np.int32),np.r_[0,np.cumsum(list(map(len,rows)))].astype(np.int32)


def reference(count,edges,extra):
    ring=[set() for _ in range(count)];incident=[set() for _ in range(count)]
    for e,(_,_,a,b) in enumerate(edges):
        if a!=b:ring[a].add(int(b));ring[b].add(int(a))
        incident[a].add(e);incident[b].add(e)
    result=[]
    for e,(_,_,a,b) in enumerate(edges):
        vertices=(ring[a]|ring[b])-{int(a),int(b)}
        values={g for v in vertices for g in incident[v] if a not in edges[g] and b not in edges[g]}
        values.update((extra or {}).get(e,set()));result.append(sorted(values))
    return np.asarray([x for row in result for x in row],np.int32),np.r_[0,np.cumsum(list(map(len,result)))].astype(np.int32)


class EdgeRowsTests(unittest.TestCase):
    def test_grid_filters_with_external_exclusions(self):
        faces=[]
        for y in range(3):
            for x in range(3):
                a=y*4+x;faces.extend(((a,a+1,a+4),(a+1,a+5,a+4)))
        edges=cloth_edges(faces).astype(np.int64);v,o=adjacency(16,edges)
        extra={0:{len(edges)-1},len(edges)-1:{0}}
        for exclusions in (None,{},extra):
            actual=N.edge_edge(16,edges,v,o,exclusions);expected=reference(16,edges,exclusions)
            for a,b in zip(actual,expected):self.assertEqual(a.tobytes(),b.tobytes())

    def test_dense_valence_fallback_is_exact(self):
        edges=np.array([[-1,-1,0,i] for i in range(1,34)],np.int64);v,o=adjacency(34,edges)
        extra={0:{32}}
        actual=N.edge_edge(34,edges,v,o,extra);expected=reference(34,edges,extra)
        for a,b in zip(actual,expected):self.assertEqual(a.tobytes(),b.tobytes())


if __name__=='__main__':unittest.main()
