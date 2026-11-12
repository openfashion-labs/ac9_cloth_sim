# SPDX-License-Identifier: GPL-3.0-or-later
"""Private Jacobi ownership batch, explicitly not a proper structural coloring."""
import numpy as np
import warp as wp


class JacobiBatch:
    def __init__(self,model,bound=None):
        self.model=model
        self.old_groups=model.particle_color_groups
        self.old_color_array=model.particle_colors
        self.old_colors=model.particle_colors.numpy().copy()
        self.active=np.sort(np.concatenate([group.numpy()for group in self.old_groups])).astype(np.int32)
        if len(np.unique(self.active))!=len(self.active):raise ValueError('Duplicate Jacobi batch membership')
        if bound is not None and np.isin(self.active,bound.vertices.numpy()).any():
            raise ValueError('Dependent followers cannot enter the independent Jacobi batch')
        self.colors=np.full(model.particle_count,-1,np.int32);self.colors[self.active]=0
        self.groups=[wp.array(self.active,dtype=int,device=model.device)]
        self.summary=dict(algorithm='Jacobi diagnostic; batch labels are not structural colors',
                          old_colors=len(self.old_groups),proposed_colors=1,independent_points=len(self.active))

    def install(self):
        self.model.particle_colors.assign(self.colors);self.model.particle_color_groups=self.groups;return True

    def restore(self):
        if self.model.particle_colors is not self.old_color_array:
            raise ValueError('Jacobi probe changed color storage')
        self.model.particle_colors.assign(self.old_colors);self.model.particle_color_groups=self.old_groups

    def validate_excluded(self):
        return dict(scope='No structural stencil is excluded; ownership batches only',active=0)


class TwoPerStencilBatch(JacobiBatch):
    """Three Jacobi batches, GS between them; immutable occupancy certificate."""
    def __init__(self,model,bound,colors,validate_materials=True):
        super().__init__(model,bound)
        colors=np.asarray(colors)
        if colors.dtype!=np.dtype(np.int32)or colors.shape!=(model.particle_count,)or np.any(colors[self.active]<0)or np.any(colors[self.active]>2):
            raise ValueError('Invalid three-batch membership')
        inactive=np.ones(model.particle_count,bool);inactive[self.active]=False
        if np.any(colors[inactive]!=-1):raise ValueError('Inactive vertex entered three-batch solve')
        followers=bound.vertices.numpy()if bound is not None else np.empty(0,np.int32)
        triangles=bound.triangles.numpy()if bound is not None else np.empty((0,3),np.int32)
        support={int(v):list(map(int,t))for v,t in zip(followers,triangles)}
        self.excluded_tri=np.isin(model.tri_indices.numpy(),followers).any(1)
        self.excluded_edge=np.isin(model.edge_indices.numpy(),followers).any(1)
        rows=[model.tri_indices.numpy()[~self.excluded_tri],model.edge_indices.numpy()[~self.excluded_edge],model.spring_indices.numpy().reshape(-1,2)[:model.spring_count]]
        if getattr(model,'tet_count',0):rows.append(model.tet_indices.numpy())
        count=0
        for elements in rows:
            for row in elements:
                nodes=set()
                for v in row:
                    if v>=0:nodes.update(support.get(int(v),[int(v)]))
                batches=colors[list(nodes)];batches=batches[batches>=0]
                if np.bincount(batches,minlength=3).max(initial=0)>2:
                    raise ValueError('More than two independent nodes of one stencil share a batch')
                count+=1
        self.colors=colors.copy();self.groups=[wp.array(np.flatnonzero(colors==i).astype(np.int32),dtype=int,device=model.device)for i in range(3)]
        if any(group.size==0 for group in self.groups):raise ValueError('Empty diagnostic batch')
        self.summary.update(algorithm='Three Jacobi batches with GS between batches; not proper coloring',proposed_colors=3,structural_rows_checked=count,max_independent_per_stencil=2,dynamic_contacts_certified=False)
        if validate_materials:self.validate_excluded()

    def validate_excluded(self):
        self.validate_tri_materials(self.model.tri_materials.numpy())
        self.validate_edge_materials(self.model.edge_bending_properties.numpy())
        return dict(tri=int(self.excluded_tri.sum()),edge=int(self.excluded_edge.sum()),active=0)

    def validate_tri_materials(self,materials):
        if len(materials)!=len(self.excluded_tri)or np.any(materials[self.excluded_tri,:2]!=0.):
            raise ValueError('The masked bound triangle stencil would become active')

    def validate_edge_materials(self,materials):
        if len(materials)!=len(self.excluded_edge)or np.any(materials[self.excluded_edge,0]!=0.):
            raise ValueError('The masked bound edge stencil would become active')
