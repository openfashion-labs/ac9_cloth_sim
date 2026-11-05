# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np

def check_closed(v,f):
    e=np.vstack([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]);ordered=np.sort(e,axis=1)
    unique,counts=np.unique(ordered,axis=0,return_counts=True)
    orientation=np.zeros(len(unique),int);_,inverse=np.unique(ordered,axis=0,return_inverse=True)
    np.add.at(orientation,inverse,np.where(e[:,0]<e[:,1],1,-1))
    area=np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]),axis=1)
    return {'vertices':len(v),'triangles':len(f),'boundary_or_nonmanifold_edges':int(np.sum(counts!=2)),'boundary_edges':int(np.sum(counts==1)),'nonmanifold_edges':int(np.sum(counts>2)),'inconsistently_oriented_edges':int(np.sum(orientation!=0)),'inconsistent_two_face_edges':int(np.sum((counts==2)&(orientation!=0))),'zero_area_faces':int(np.sum(area==0))}
