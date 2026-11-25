# SPDX-FileCopyrightText: Copyright (c) 2025 The Newton Developers
# SPDX-License-Identifier: Apache-2.0
"""Value-only geometry from Newton ModelBuilder, with its arithmetic order.

The two upstream functions are pinned by semantic source hash. Unknown builds
keep the ModelBuilder path. No topology, masses, state or history is cached.
"""
import ast
import hashlib
import inspect
import json
import textwrap
import numpy as np

EXPECTED={
    'add_triangles':'b90e75798a2826ce584f9b87048103e64ba0ab42a8bac0975d9190643a54320a',
    'add_edges':'21f9b280c5680c1f88ba296715166186274bf7e22e553afcf6112b2c1f6f2699',
}
WARP_CORE_SHA256='7a5ce6e5540a7d7e07004221c742f4e4cd7db1f09a076407bde4a1bb6240ea20'

_CORE_OK={}

def verified_warp_lengths(wp):
    """The CPU length implementation checked byte-for-byte on this platform. The library file does not change while
    the process runs, so its hash is read once per process (it took 0.15-0.2 s on every fresh build)."""
    try:
        from warp._src import context
        if not isinstance(wp.length,context.Function) or wp.length.key!='length':return False
        name=context.runtime.core._name
        if name not in _CORE_OK:
            digest=hashlib.sha256()
            with open(name,'rb') as stream:
                for block in iter(lambda:stream.read(1048576),b''):digest.update(block)
            _CORE_OK[name]=digest.hexdigest()==WARP_CORE_SHA256
        return _CORE_OK[name]
    except (OSError,TypeError,AttributeError):return False

def semantic_hash(fn):
    if isinstance(fn.body[0],ast.Expr) and isinstance(fn.body[0].value,ast.Constant) and isinstance(fn.body[0].value.value,str):fn.body.pop(0)
    def encode(value):
        if value is Ellipsis:return ['ellipsis']
        if isinstance(value,ast.AST):return [type(value).__name__,[(key,encode(v)) for key,v in ast.iter_fields(value)]]
        if isinstance(value,list):return [encode(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(encode(fn),separators=(',',':')).encode()).hexdigest()

def builder_identity(builder):
    actual={}
    try:
        for name in EXPECTED:
            function=getattr(builder,name)
            # getsource unwraps functools.wraps and would otherwise certify a
            # wrapper that changes the actual geometry after the original call.
            if inspect.unwrap(function) is not function:return False,actual
            tree=ast.parse(textwrap.dedent(inspect.getsource(function)))
            fn=tree.body[0]
            # Dedenting changes whitespace inside the method's documentation.
            # Ignore only that leading docstring; keep every executable node.
            actual[name]=semantic_hash(fn)
    except (OSError,TypeError,ValueError,AttributeError,SyntaxError):return False,actual
    return actual==EXPECTED,actual

def normalized(a):
    length=np.linalg.norm(a,axis=-1,keepdims=True)
    length[length==0]=1.0
    return a/length

def geometry_values(positions,triangles,edges):
    """Return the exact all-valid add_triangles/add_edges value outputs."""
    q_=np.asarray(positions,np.float64)
    p=q_[triangles[:,0]];q=q_[triangles[:,1]];r=q_[triangles[:,2]]
    qp=q-p;rp=r-p
    n=normalized(np.cross(qp,rp));e1=normalized(qp);e2=normalized(np.cross(n,e1))
    R=np.concatenate((e1[...,None],e2[...,None]),axis=-1)
    M=np.concatenate((qp[...,None],rp[...,None]),axis=-1)
    D=np.matmul(R.transpose(0,2,1),M)
    areas=np.linalg.det(D)/2.0
    areas[areas<0.0]=0.0
    if not np.all(areas>0.0):raise RuntimeError('a degenerate triangle at this start shape')
    poses=np.linalg.inv(D)
    i,j,k,l=(edges[:,axis] for axis in range(4))
    x3=q_[k];x4=q_[l];delta=x4-x3
    rest=np.zeros_like(i,dtype=float)
    valid=(i!=-1)&(j!=-1)
    x1=q_[i[valid]];x2=q_[j[valid]];a=x3[valid];b=x4[valid]
    n1=normalized(np.cross(a-x1,b-x1));n2=normalized(np.cross(b-x2,a-x2));e=normalized(b-a)
    cosine=np.clip((n1*n2).sum(axis=-1),-1.,1.)
    sine=(np.cross(n1,n2)*e).sum(axis=-1)
    rest[valid]=np.arctan2(sine,cosine)
    lengths=np.linalg.norm(delta,axis=1)
    return areas,poses,rest,lengths
