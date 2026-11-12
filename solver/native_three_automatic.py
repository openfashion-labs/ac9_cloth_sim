# SPDX-License-Identifier: GPL-3.0-or-later
"""Private automatic max-two-per-stencil three-batch partition.

This is a structural occupancy certificate, not a global convergence guarantee.
"""
import time,hashlib
import numpy as np
from native_experimental_coloring import ColoringPlan
from native_jacobi_diagnostic import TwoPerStencilBatch


def find_three(stencils,proper,max_steps=200000,max_seconds=20.0):
    started=time.monotonic();rng=np.random.default_rng(8123);proper=np.asarray(proper)
    if proper.dtype!=np.dtype(np.int32)or proper.ndim!=1:raise ValueError('Expected int32 proper colors')
    active=np.flatnonzero(proper>=0)
    if len(active)<3 or len(active)>120000 or proper.max(initial=-1)>=12:
        raise ValueError('Automatic three-batch diagnostic exceeds its planning range')
    edges=[np.asarray(row,np.int32)for row in stencils];incident=[[]for _ in proper]
    for i,row in enumerate(edges):
        if row.ndim!=1 or len(row)!=len(np.unique(row))or np.any(row<0)or np.any(row>=len(proper))or np.any(proper[row]<0):raise ValueError('Invalid independent stencil')
        if len(row)>6 or len(np.unique(proper[row]))!=len(row):raise ValueError('Invalid proper stencil or capacity')
        for v in row:incident[v].append(i)
    incident=[np.asarray(row,np.int32)for row in incident]
    colors=np.full(len(proper),-1,np.int32);colors[active]=proper[active]//2
    unassigned=active[colors[active]>=3];colors[unassigned]=-1
    counts=np.zeros((len(edges),3),np.int32)
    for i,row in enumerate(edges):
        values=colors[row];counts[i]=np.bincount(values[values>=0],minlength=3)
    for v in unassigned:
        ids=incident[v];cost=np.maximum(counts[ids]+1-2,0).sum(0)-np.maximum(counts[ids]-2,0).sum(0)
        choices=np.flatnonzero(cost==cost.min());color=int(choices[rng.integers(len(choices))]);colors[v]=color;counts[ids,color]+=1
    bad=[];positions={}
    def update(i):
        flag=bool((counts[i]>2).any())
        if flag and i not in positions:positions[i]=len(bad);bad.append(i)
        elif not flag and i in positions:
            index=positions.pop(i);last=bad.pop()
            if index<len(bad):bad[index]=last;positions[last]=index
    for i in range(len(edges)):update(i)
    initial=int(np.maximum(counts-2,0).sum());excess=initial;lowest=initial;tabu=np.zeros((len(proper),3),np.int32);used=0
    for step in range(max_steps):
        if not bad or time.monotonic()-started>=max_seconds:break
        used=step+1;eid=bad[int(rng.integers(len(bad)))];v=int(edges[eid][rng.integers(len(edges[eid]))]);current=int(colors[v]);ids=incident[v]
        before=int(np.maximum(counts[ids]-2,0).sum());options=[]
        for target in range(3):
            if target==current:continue
            changed=counts[ids].copy();changed[:,current]-=1;changed[:,target]+=1;delta=int(np.maximum(changed-2,0).sum()-before)
            if tabu[v,target]<=step or excess+delta<lowest:options.append((delta,target))
        if not options:continue
        delta=min(x[0]for x in options);choices=[target for score,target in options if score==delta];target=int(choices[rng.integers(len(choices))])
        counts[ids,current]-=1;counts[ids,target]+=1;colors[v]=target;tabu[v,current]=step+7+int(rng.integers(7));excess+=delta;lowest=min(lowest,excess)
        for i in ids:update(int(i))
    accounted=np.asarray([np.bincount(colors[row],minlength=3)for row in edges],np.int32).reshape(-1,3)
    if not np.array_equal(accounted,counts):raise ValueError('Three-batch accounting differs')
    # Splitting a populated batch into an empty one cannot increase occupancy.
    if not bad:
        for target in range(3):
            if np.any(colors==target):continue
            sizes=np.bincount(colors[active],minlength=3);largest=int(sizes.argmax());members=np.flatnonzero(colors==largest)
            if len(members)<2:raise ValueError('Cannot form three nonempty batches')
            colors[members[:len(members)//2]]=target
    actual=np.asarray([np.bincount(colors[row],minlength=3)for row in edges],np.int32).reshape(-1,3)
    success=not bad and bool((actual<=2).all())and len(np.unique(colors[active]))==3
    report=dict(success=success,initial_excess=initial,final_excess=int(np.maximum(actual-2,0).sum()),best_excess=lowest,steps=used,search_s=time.monotonic()-started,sizes=np.bincount(colors[active],minlength=3).tolist(),maximum_per_stencil=2,stencils=len(edges))
    if success:report['membership_sha256']=hashlib.sha256(colors.tobytes()).hexdigest()
    return (colors if success else None),report


class AutomaticThreeBatchPlan(TwoPerStencilBatch):
    def __init__(self,model,bound=None,validate_materials=True):
        started=time.monotonic()
        if getattr(model,'tet_count',0)or not model.spring_count:
            raise ValueError('Automatic three-batch diagnostic is audited only for stitched surfaces')
        import native_three_cache as cache
        try:
            cache_key = cache.input_key(model, bound) if cache.enabled() and 3 <= model.particle_count <= cache.MAX_POINTS else None
        except OSError:
            cache_key = None
        cached = cache.load(cache_key, model.particle_count) if cache_key is not None else None
        if cached is not None:
            try:
                super().__init__(model, bound, cached, validate_materials=False)
            except ValueError:
                cached = None
            else:
                # Material reactivation is an invalid input, never a cache miss.
                if validate_materials:
                    self.validate_excluded()
                self.summary.update(automatic=True, external_membership_file=False,
                                    cache=dict(hit=True, key=cache_key),
                                    automatic_build_s=time.monotonic()-started)
                return
        proper=ColoringPlan(model,bound);followers=bound.vertices.numpy()if bound is not None else np.empty(0,np.int32)
        supports=bound.triangles.numpy()if bound is not None else np.empty((0,3),np.int32)
        support={int(v):list(map(int,t))for v,t in zip(followers,supports)};stencils=set()
        for name,width in [('tri_indices',3),('edge_indices',4),('spring_indices',2)]:
            rows=getattr(model,name).numpy().reshape(-1,width)
            if name=='spring_indices':rows=rows[:model.spring_count]
            else:rows=rows[~np.isin(rows,followers).any(1)]
            for row in rows:
                nodes=set()
                for v in row:
                    if v>=0:nodes.update(support.get(int(v),[int(v)]))
                nodes=tuple(sorted(v for v in nodes if proper.colors[v]>=0))
                if len(nodes)>1:stencils.add(nodes)
        colors,search=find_three(sorted(stencils),proper.colors)
        if colors is None:raise ValueError('Three-batch search did not certify a partition')
        super().__init__(model,bound,colors,validate_materials=validate_materials)
        self.summary.update(automatic=True,external_membership_file=False,proper_build=proper.summary,search=search,automatic_build_s=time.monotonic()-started)
        saved = cache.save(cache_key, colors) if cache_key is not None else False
        self.summary['cache'] = dict(hit=False, key=cache_key, saved=saved)
        self.summary['automatic_build_s'] = time.monotonic()-started
