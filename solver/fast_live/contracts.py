# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only entry contracts for the experimental BW global correction."""
import numpy as np

def validate_arrays(model,inv_mass,flags,active_bit):
    for key in ['mass','poses','materials','areas']:
        if not np.isfinite(model[key]).all():raise ValueError('Nonfinite model array: '+key)
    mass=model['mass'];free=mass>0
    if np.any(mass<0)or not np.isfinite(inv_mass).all()or np.any(inv_mass<0):raise ValueError('Invalid mass/inverse mass')
    native_free=((flags&int(active_bit))!=0)&(inv_mass>0)
    if not np.array_equal(free,native_free):raise ValueError('Native ACTIVE/inverse mass/free-set mismatch')
    if np.any((mass==0)&(inv_mass!=0))or not np.allclose(mass[free]*inv_mass[free],1.,rtol=2e-6,atol=0):raise ValueError('Mass/inverse mass inconsistent')
    if np.any(model['materials'][:,:4]<0)or np.any(model['areas']<=0):raise ValueError('Invalid BW material or area')
    tri=model['tri']
    if tri.size and (tri.min()<0 or tri.max()>=len(mass)):raise ValueError('Triangle index out of range')
    return free

def validate_state(q,model,bend,dt):
    if not np.isfinite(dt)or dt<=0:raise ValueError('Positive finite dt required')
    if not np.isfinite(q).all():raise ValueError('Nonfinite positions')
    for key in ['rest','length','bending','bf']:
        if not np.isfinite(bend[key]).all():raise ValueError('Nonfinite bending data: '+key)
    if np.any(bend['bending']<0)or np.any(bend['length']<0):raise ValueError('Negative bending material')
    tri=model['tri']
    if len(tri):
        F=np.stack([q[tri[:,1]]-q[tri[:,0]],q[tri[:,2]]-q[tri[:,0]]],axis=-1)@model['poses']
        if not np.isfinite(F).all()or np.min(np.linalg.norm(F,axis=1))<=1e-12:raise ValueError('Undefined membrane length')
    edge=bend['edge'];valid=np.all(edge>=0,axis=1)&(bend['bending'][:,0]>0)
    if np.any(edge[valid]>=len(q)):raise ValueError('Hinge index out of range')
    edge=edge[valid]
    if len(edge):
        a,b,c,d=[q[edge[:,i]]for i in range(4)]
        if min(np.linalg.norm(np.cross(c-a,d-a),axis=1).min(),np.linalg.norm(np.cross(d-b,c-b),axis=1).min(),np.linalg.norm(d-c,axis=1).min())<1e-6:
            raise ValueError('Native-degenerate active hinge requires a separate validated contract')

def validate_mode(cfg,kernel):
    if cfg.get('membrane')!='bw' or int(kernel.SIMSPIKE_MEMBRANE_BW)!=1:
        raise ValueError('Loaded Native and global correction must both use BW')
