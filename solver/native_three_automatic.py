# SPDX-License-Identifier: GPL-3.0-or-later
"""Private automatic max-two-per-stencil three-batch partition.

This is a structural occupancy certificate, not a global convergence guarantee.
"""
import time,hashlib
import numpy as np
from job_cancel import check as _cancel_check, checkpoint as _cancel_checkpoint
from native_experimental_coloring import ColoringPlan
from native_jacobi_diagnostic import TwoPerStencilBatch
from native_stencil_arrays import model_stencils


def find_three(stencils,proper,max_steps=200000,max_seconds=20.0,initial_colors=None):
    _cancel_check()
    started=time.monotonic();rng=np.random.default_rng(8123);proper=np.asarray(proper)
    if proper.dtype!=np.dtype(np.int32)or proper.ndim!=1:raise ValueError('Expected int32 proper colors')
    active=np.flatnonzero(proper>=0)
    if len(active)<3 or len(active)>120000 or proper.max(initial=-1)>=12:
        raise ValueError('Automatic three-batch diagnostic exceeds its planning range')
    if isinstance(stencils,np.ndarray):
        matrix=np.asarray(stencils)
        if matrix.ndim!=2 or not np.issubdtype(matrix.dtype,np.integer):raise ValueError('Invalid independent stencil')
    else:
        rows=[np.asarray(row,np.int32) for row in stencils]
        if any(row.ndim!=1 or np.any(row<0) for row in rows):raise ValueError('Invalid independent stencil')
        width=max(map(len,rows),default=0)
        matrix=np.full((len(rows),width),-1,np.int32)
        for i,row in enumerate(rows):matrix[i,:len(row)]=row
    present=matrix>=0
    if np.any(matrix < -1) or np.any(matrix>=len(proper)):raise ValueError('Invalid independent stencil')
    row_ids,slots=np.nonzero(present);vertices=matrix[row_ids,slots]
    if np.any(proper[vertices]<0):raise ValueError('Invalid independent stencil')
    sorted_nodes=np.sort(np.where(present,matrix,len(proper)),axis=1)
    pcolors=np.full(matrix.shape,12,np.int32);pcolors[present]=proper[vertices];pcolors.sort(axis=1)
    if (np.any(present.sum(1)>6) or np.any((sorted_nodes[:,1:]==sorted_nodes[:,:-1])&(sorted_nodes[:,1:]<len(proper)))
            or (initial_colors is None and np.any((pcolors[:,1:]==pcolors[:,:-1])&(pcolors[:,1:]<12)))):raise ValueError('Invalid proper stencil or capacity')
    def edge(i):return matrix[i,present[i]]
    order=np.argsort(vertices,kind='stable');incident_ids=row_ids[order].astype(np.int32)
    offsets=np.r_[0,np.cumsum(np.bincount(vertices,minlength=len(proper)))]
    def incident(v):return incident_ids[offsets[v]:offsets[v+1]]
    colors=np.full(len(proper),-1,np.int32);colors[active]=proper[active]//2
    if initial_colors is not None:
        initial_colors=np.asarray(initial_colors)
        if initial_colors.dtype!=np.int32 or initial_colors.shape!=proper.shape or np.any(initial_colors[active]<0) or np.any(initial_colors[active]>2) or np.any(initial_colors[proper<0]!=-1):raise ValueError('Invalid initial three-batch membership')
        colors=initial_colors.copy()
    unassigned=active[colors[active]>=3];colors[unassigned]=-1
    def recount():
        labels=colors[vertices];valid=labels>=0
        return np.bincount(row_ids[valid]*3+labels[valid],minlength=len(matrix)*3).reshape(-1,3).astype(np.int32)
    counts=recount()
    for v in unassigned:
        ids=incident(v);cost=np.maximum(counts[ids]+1-2,0).sum(0)-np.maximum(counts[ids]-2,0).sum(0)
        choices=np.flatnonzero(cost==cost.min());color=int(choices[rng.integers(len(choices))]);colors[v]=color;counts[ids,color]+=1
    bad=[];positions={}
    def update(i):
        flag=bool((counts[i]>2).any())
        if flag and i not in positions:positions[i]=len(bad);bad.append(i)
        elif not flag and i in positions:
            index=positions.pop(i);last=bad.pop()
            if index<len(bad):bad[index]=last;positions[last]=index
    bad=np.flatnonzero((counts>2).any(1)).tolist();positions={i:p for p,i in enumerate(bad)}
    initial=int(np.maximum(counts-2,0).sum());excess=initial;lowest=initial;tabu=np.zeros((len(proper),3),np.int32);used=0
    for step in range(max_steps):
        if step % 256 == 0:_cancel_check()
        if not bad or time.monotonic()-started>=max_seconds:break
        used=step+1;eid=bad[int(rng.integers(len(bad)))];row=edge(eid);v=int(row[rng.integers(len(row))]);current=int(colors[v]);ids=incident(v)
        before=int(np.maximum(counts[ids]-2,0).sum());options=[]
        for target in range(3):
            if target==current:continue
            changed=counts[ids].copy();changed[:,current]-=1;changed[:,target]+=1;delta=int(np.maximum(changed-2,0).sum()-before)
            if tabu[v,target]<=step or excess+delta<lowest:options.append((delta,target))
        if not options:continue
        delta=min(x[0]for x in options);choices=[target for score,target in options if score==delta];target=int(choices[rng.integers(len(choices))])
        counts[ids,current]-=1;counts[ids,target]+=1;colors[v]=target;tabu[v,current]=step+7+int(rng.integers(7));excess+=delta;lowest=min(lowest,excess)
        for i in ids:update(int(i))
    accounted=recount()
    if not np.array_equal(accounted,counts):raise ValueError('Three-batch accounting differs')
    # Splitting a populated batch into an empty one cannot increase occupancy.
    if not bad:
        for target in range(3):
            if np.any(colors==target):continue
            sizes=np.bincount(colors[active],minlength=3);largest=int(sizes.argmax());members=np.flatnonzero(colors==largest)
            if len(members)<2:raise ValueError('Cannot form three nonempty batches')
            colors[members[:len(members)//2]]=target
    actual=recount()
    success=not bad and bool((actual<=2).all())and len(np.unique(colors[active]))==3
    report=dict(success=success,initial_excess=initial,final_excess=int(np.maximum(actual-2,0).sum()),best_excess=lowest,steps=used,search_s=time.monotonic()-started,sizes=np.bincount(colors[active],minlength=3).tolist(),maximum_per_stencil=2,stencils=len(matrix))
    if success:report['membership_sha256']=hashlib.sha256(colors.tobytes()).hexdigest()
    return (colors if success else None),report


class AutomaticThreeBatchPlan(TwoPerStencilBatch):
    def __init__(self,model,bound=None,validate_materials=True):
        _cancel_check()
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
                                    cache=dict(hit=True, status='hit', key=cache_key),
                                    automatic_build_s=time.monotonic()-started)
                return
        # Preserve the original search budgets and fresh rebuild on invalid
        # cache. Seeded repair belongs to the separate experimental candidate.
        _cancel_checkpoint('plan_proper_begin')
        proper=ColoringPlan(model,bound,array_neighbors=True,reduce_steps=2000,
                            compact_budget=2000,compact_stop_at_budget=True)
        stencils,_=model_stencils(model,bound,proper.colors>=0,unique=True)
        _cancel_checkpoint('plan_search_begin')
        colors,search=find_three(stencils,proper.colors)
        if colors is None:raise ValueError('Three-batch search did not certify a partition')
        super().__init__(model,bound,colors,validate_materials=validate_materials)
        self.summary.update(automatic=True,external_membership_file=False,proper_build=proper.summary,search=search,automatic_build_s=time.monotonic()-started)
        saved = cache.save(cache_key, colors) if cache_key is not None else False
        self.summary['cache'] = dict(hit=False, status='rebuilt', key=cache_key, saved=saved)
        self.summary['automatic_build_s'] = time.monotonic()-started
