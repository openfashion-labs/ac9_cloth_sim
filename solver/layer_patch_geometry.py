# SPDX-License-Identifier: GPL-3.0-or-later
"""Batched double-precision geometry for the isolated CPU patch prototype."""
import numpy as np


def triangle_projection(p, tri):
    a,b,c=tri[:,0],tri[:,1],tri[:,2];e=b-a;f=c-a;r=p-a
    ee=np.einsum('ij,ij->i',e,e);ef=np.einsum('ij,ij->i',e,f);ff=np.einsum('ij,ij->i',f,f)
    er=np.einsum('ij,ij->i',e,r);fr=np.einsum('ij,ij->i',f,r);det=ee*ff-ef*ef
    safe=np.where(det>1.e-28,det,1.)
    u=(ff*er-ef*fr)/safe;v=(ee*fr-ef*er)/safe
    bary=np.stack([1.-u-v,u,v],axis=1);cp=np.einsum('mi,mij->mj',bary,tri)
    candidates=[cp];weights=[bary];valid=[(det>1.e-28)&(bary.min(1)>=0)]
    for i,j in ((0,1),(1,2),(2,0)):
        delta=tri[:,j]-tri[:,i];t=np.clip(np.einsum('ij,ij->i',p-tri[:,i],delta)/np.maximum(np.einsum('ij,ij->i',delta,delta),1.e-30),0.,1.)
        w=np.zeros_like(bary);w[:,i]=1.-t;w[:,j]=t
        candidates.append(tri[:,i]+t[:,None]*delta);weights.append(w);valid.append(np.ones(len(p),bool))
    candidates=np.stack(candidates,axis=1);weights=np.stack(weights,axis=1)
    distance=((candidates-p[:,None,:])**2).sum(2);distance[~np.stack(valid,axis=1)]=np.inf
    chosen=distance.argmin(1);rows=np.arange(len(p))
    return candidates[rows,chosen],weights[rows,chosen]


def edge_projection(q):
    a,b,c,d=q[:,0],q[:,1],q[:,2],q[:,3];u=b-a;v=d-c;r=c-a
    dot=lambda p,q:np.einsum('ij,ij->i',p,q)
    aa,bb,cc=dot(u,u),dot(u,v),dot(v,v);ur,vr=dot(u,r),dot(v,r);det=aa*cc-bb*bb;safe=np.where(det>1.e-28,det,1.)
    s=(cc*ur-bb*vr)/safe;t=(bb*ur-aa*vr)/safe
    ss=[s];tt=[t];valid=[(det>1.e-28)&(s>=0)&(s<=1)&(t>=0)&(t<=1)]
    for value in (0.,1.):
        ss.append(np.full(len(q),value));tt.append(np.clip(dot(a+value*u-c,v)/np.maximum(cc,1.e-30),0.,1.));valid.append(np.ones(len(q),bool))
    for value in (0.,1.):
        ss.append(np.clip(dot(c+value*v-a,u)/np.maximum(aa,1.e-30),0.,1.));tt.append(np.full(len(q),value));valid.append(np.ones(len(q),bool))
    ss=np.stack(ss,1);tt=np.stack(tt,1);delta=a[:,None]+ss[:,:,None]*u[:,None]-c[:,None]-tt[:,:,None]*v[:,None]
    distance=(delta*delta).sum(2);distance[~np.stack(valid,1)]=np.inf;chosen=distance.argmin(1);rows=np.arange(len(q));s=ss[rows,chosen];t=tt[rows,chosen]
    return delta[rows,chosen],np.stack([1.-s,s,t-1.,-t],1)


class BatchGeometry:
    def __init__(self,x,movable,contacts):
        self.x=np.asarray(x,float);self.movable=np.asarray(movable,int);self.rows=len(contacts);self.width=len(movable)*3;self.groups={}
        mapping=np.full(len(x),-1,int);mapping[movable]=np.arange(len(movable))
        for kind in ('vt','ee','body','witness'):
            indices=np.array([i for i,row in enumerate(contacts)if row.kind==kind],int)
            if not len(indices):continue
            items=[contacts[i]for i in indices];count=max(len(row.nodes)for row in items);nodes=np.zeros((len(items),count),int);weights=np.zeros((len(items),count))
            for i,row in enumerate(items):
                nodes[i,:len(row.nodes)]=row.nodes
                if kind=='body':weights[i,:len(row.nodes)]=row.weights
            self.groups[kind]={'indices':indices,'nodes':nodes,'mapping':mapping[nodes],'normal':np.array([row.normal for row in items]),'weights':weights,'point':np.array([row.point for row in items])if kind=='body'else None,'t':np.array([row.t for row in items]),'sign':np.array([row.sign for row in items])}

    def __call__(self,d):
        x=self.x.copy();x[self.movable]+=d.reshape(-1,3)*.001
        return self.at_positions(x)

    def at_positions(self,x,with_jac=True):
        gaps=np.zeros(self.rows);jac=np.zeros((self.rows,self.width))if with_jac else None
        for kind,g in self.groups.items():
            q=x[g['nodes']];normal=g['normal']
            if kind=='vt':
                cp,bary=triangle_projection(q[:,3],q[:,:3]);diff=q[:,3]-cp;weights=np.c_[-bary,np.ones(len(q))]
            elif kind=='ee':diff,weights=edge_projection(q)
            elif kind=='body':
                weights=g['weights'];diff=np.einsum('mi,mij->mj',weights,q)-g['point']
            else:
                t=g['t'];point=(1.-t[:,None])*q[:,0]+t[:,None]*q[:,1];cp,bary=triangle_projection(point,q[:,2:]);diff=point-cp
                normal=np.cross(q[:,3]-q[:,2],q[:,4]-q[:,2]);length=np.linalg.norm(normal,axis=1)
                if np.min(length)<1.e-14:raise ValueError('Degenerate witness support')
                normal=normal/length[:,None]*g['sign'][:,None];weights=np.c_[1.-t,t,-bary]
            gaps[g['indices']]=np.einsum('ij,ij->i',diff,normal)*1000.
            if not with_jac:continue
            gradient=weights[:,:,None]*normal[:,None,:];local=g['mapping'];valid=local>=0
            row=np.broadcast_to(g['indices'][:,None,None],gradient.shape)[np.broadcast_to(valid[:,:,None],gradient.shape)]
            col=(local[:,:,None]*3+np.arange(3)[None,None,:])[np.broadcast_to(valid[:,:,None],gradient.shape)]
            np.add.at(jac,(row,col),gradient[np.broadcast_to(valid[:,:,None],gradient.shape)])
        return x,gaps,jac
