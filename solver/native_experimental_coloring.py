# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental structural recoloring. Collision geometry and mass are retained."""
import heapq
import time
import numpy as np
import warp as wp


def compact(neighbors,initial,depth=2,budget=20000):
    colors=initial.copy();attempts=[]
    while colors.max()>=1:
        last=int(colors.max());palette=range(last);steps=0
        # Remove the least populated color without changing graph edges.
        count=np.bincount(colors[colors>=0]);rare=int(np.argmin(count))
        if rare!=last:
            low=colors==rare;high=colors==last;colors[low]=last;colors[high]=rare
        before=colors.copy()
        def assign(v,target,remaining,blocked):
            nonlocal steps,colors
            steps+=1
            if steps>budget or v in blocked:return False
            conflicts=[u for u in neighbors[v]if colors[u]==target]
            if conflicts and remaining==0:return False
            saved=colors.copy();colors[v]=target;blocked=blocked|{v}
            for u in conflicts:
                if colors[u]!=target:continue
                alternatives=sorted((c for c in palette if c!=target),key=lambda c:sum(colors[w]==c for w in neighbors[u]))
                moved=False
                for alternative in alternatives:
                    if assign(u,alternative,remaining-1,blocked):moved=True;break
                if not moved:colors=saved;return False
            return True
        unresolved=[]
        for v in sorted(np.flatnonzero(colors==last),key=lambda v:-len(neighbors[v])):
            options=sorted(palette,key=lambda c:sum(colors[w]==c for w in neighbors[v]))
            if not any(assign(int(v),target,depth,set())for target in options):unresolved.append(int(v))
        attempts.append(dict(from_colors=last+1,to_colors=int(colors.max()+1),steps=steps,unresolved=len(unresolved)))
        if unresolved:
            colors=before
            break
    return colors,attempts


def reduce_colors(neighbors,initial,steps=20000,seed=2121):
    rng=np.random.default_rng(seed);active=np.flatnonzero(initial>=0);target=int(initial.max())
    if target<1:return initial.copy(),dict(target=target,steps=0,best_conflicts=0,final_conflicts=0,success=False)
    colors=initial.copy();counts=np.zeros((len(colors),target),np.int32)
    for v in active:
        if colors[v]==target:colors[v]=int(rng.integers(target))
    for v in active:
        if neighbors[v]:counts[v]=np.bincount(colors[list(neighbors[v])],minlength=target)
    bad={int(v)for v in active if counts[v,colors[v]]}
    conflicts=sum(int(counts[v,colors[v]])for v in active)//2
    best=conflicts;tabu=np.zeros_like(counts);result=initial.copy();used=0
    for iteration in range(steps):
        used=iteration+1
        if conflicts==0:result=colors.copy();break
        vertices=np.array(sorted(bad),np.int32)
        delta=counts[vertices]-counts[vertices,colors[vertices]][:,None]
        allowed=(tabu[vertices]<=iteration)|(delta+conflicts<best)
        allowed[np.arange(len(vertices)),colors[vertices]]=False
        scores=np.where(allowed,delta,1000000)
        score=int(scores.min());choices=np.argwhere(scores==score)
        if score==1000000:break
        row,new=choices[int(rng.integers(len(choices)))];v=int(vertices[row]);new=int(new);old=int(colors[v])
        conflicts+=score;colors[v]=new
        tabu[v,old]=iteration+int(.6*len(bad))+int(rng.integers(1,11))
        for u in neighbors[v]:
            counts[u,old]-=1;counts[u,new]+=1
            if counts[u,colors[u]]:bad.add(int(u))
            else:bad.discard(int(u))
        if counts[v,new]:bad.add(v)
        else:bad.discard(v)
        best=min(best,conflicts)
    if conflicts==0:result=colors.copy()
    return result,dict(target=target,steps=used,best_conflicts=best,final_conflicts=conflicts,success=conflicts==0)

class ColoringPlan:
    def __init__(self, model, bound=None):
        started=time.perf_counter()
        self.model=model;self.bound=bound
        self.old_groups=list(model.particle_color_groups or [])
        self.old_colors=model.particle_colors.numpy().copy()if model.particle_colors is not None else np.full(model.particle_count,-1,np.int32)
        self.active=np.sort(np.concatenate([g.numpy()for g in self.old_groups])).astype(np.int32)if self.old_groups else np.empty(0,np.int32)
        if len(np.unique(self.active))!=len(self.active):raise ValueError('Duplicate color membership')
        count=model.particle_count
        if not len(self.active):
            self.colors=self.old_colors.copy();self.applied=False
            self.excluded_tri=np.zeros(model.tri_count,bool);self.excluded_edge=np.zeros(model.edge_count,bool)
            self.summary=dict(old_colors=len(self.old_groups),proposed_colors=len(self.old_groups),conflicts=0,independent_points=0,structural_pairs=0,excluded_tri=0,excluded_edge=0,build_s=time.perf_counter()-started)
            return
        followers=bound.vertices.numpy()if bound is not None else np.empty(0,np.int32)
        triangles=bound.triangles.numpy()if bound is not None else np.empty((0,3),np.int32)
        if np.isin(self.active,followers).any():raise ValueError('Follower included in independent colors')
        support={int(v):list(map(int,t))for v,t in zip(followers,triangles)}
        active_set=set(map(int,self.active));neighbors=[set()for _ in range(count)]
        def read_rows(value,width):return value.numpy().reshape(-1,width)if value is not None else np.empty((0,width),np.int32)
        rows_tri=read_rows(model.tri_indices,3);rows_edge=read_rows(model.edge_indices,4)
        self.excluded_tri=np.isin(rows_tri,followers).any(axis=1)
        self.excluded_edge=np.isin(rows_edge,followers).any(axis=1)
        rows=[rows_tri[~self.excluded_tri],rows_edge[~self.excluded_edge],read_rows(model.spring_indices,2)[:model.spring_count]]
        if getattr(model,'tet_count',0):rows.append(model.tet_indices.numpy())
        for elements in rows:
            for row in elements:
                ids=set()
                for v in row:
                    if v>=0:ids.update(support.get(int(v),[int(v)]))
                ids.intersection_update(active_set)
                for v in ids:neighbors[v].update(ids-{v})
        colors=np.full(count,-1,np.int32);used=[set()for _ in range(count)]
        heap=[(0,-len(neighbors[v]),int(v))for v in self.active];heapq.heapify(heap)
        while heap:
            saturation,degree,v=heapq.heappop(heap)
            if colors[v]>=0 or saturation!=-len(used[v]):continue
            color=next(c for c in range(len(used[v])+1)if c not in used[v]);colors[v]=color
            for u in neighbors[v]:
                if colors[u]<0 and color not in used[u]:
                    used[u].add(color);heapq.heappush(heap,(-len(used[u]),-len(neighbors[u]),u))
        colors,local=compact(neighbors,colors)
        colors,tabu=reduce_colors(neighbors,colors)
        conflicts=sum(colors[v]==colors[u]for v in self.active for u in neighbors[v]if u>v)
        if conflicts:raise ValueError('Invalid structural coloring')
        self.colors=colors;self.applied=False
        self.summary=dict(old_colors=len(self.old_groups),proposed_colors=int(colors.max()+1),
            conflicts=int(conflicts),independent_points=len(self.active),
            structural_pairs=sum(map(len,neighbors))//2,
            excluded_tri=int(self.excluded_tri.sum()),excluded_edge=int(self.excluded_edge.sum()),
            local=local,tabu=tabu,build_s=time.perf_counter()-started)

    def validate_excluded(self):
        # This is called only after the existing kinematic material mask, or
        # on a frozen state. It never removes/changes the physical elements.
        self.validate_tri_materials(self.model.tri_materials.numpy())
        self.validate_edge_materials(self.model.edge_bending_properties.numpy())
        return dict(tri=int(self.excluded_tri.sum()),edge=int(self.excluded_edge.sum()),active=0)

    def validate_tri_materials(self, materials):
        # Validate the proposed host array before a live edit mutates the model.
        if len(materials)!=len(self.excluded_tri):
            raise ValueError('Recolored topology changed; rebuild the Native model')
        if self.excluded_tri.any()and np.any(materials[self.excluded_tri,:2]!=0.):
            raise ValueError('Excluded bound triangle stencils would become active')

    def validate_edge_materials(self, materials):
        if len(materials)!=len(self.excluded_edge):
            raise ValueError('Recolored topology changed; rebuild the Native model')
        if self.excluded_edge.any()and np.any(materials[self.excluded_edge,0]!=0.):
            raise ValueError('Excluded bound edge stencils would become active')

    def install(self):
        if self.summary['proposed_colors']>=self.summary['old_colors']:return False
        groups=[wp.array(np.flatnonzero(self.colors==c).astype(np.int32),dtype=int,device=self.model.device)for c in range(self.summary['proposed_colors'])]
        self.model.particle_colors.assign(self.colors);self.model.particle_color_groups=groups;self.applied=True
        return True

    def restore(self):
        self.model.particle_colors.assign(self.old_colors);self.model.particle_color_groups=self.old_groups;self.applied=False
