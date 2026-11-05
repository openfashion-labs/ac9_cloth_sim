# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit Native-inactive hinge contract; no source/material mutation."""
import numpy as np,warp as wp
from .hinge_activity import classify
from .contracts import validate_state

def masked_bending(bend,activity):
    result=dict(bend);result['bending']=bend['bending'].astype(float).copy();result['bending'][~activity,0]=0
    return result

def validate_dynamic_state(q,model,bend,dt,device):
    ne=len(bend['edge']);active=wp.zeros(ne,dtype=int,device=device);measure=wp.zeros(ne,dtype=wp.vec3,device=device)
    if np.any(bend['edge']>=len(q)):raise ValueError('Hinge index out of range')
    wp.launch(classify,dim=ne,inputs=[wp.array(q,dtype=wp.vec3,device=device),wp.array(bend['edge'],dtype=int,device=device)],outputs=[active,measure],device=device)
    mask=active.numpy().astype(bool)
    # Original membrane/material/finite guards still apply. The original hinge
    # area guard would reject legitimate Native-inactive small-area hinges.
    checks=dict(bend);checks['bending']=np.zeros_like(bend['bending']);validate_state(q,model,checks,dt)
    for key in ['rest','length','bending','bf']:
        if not np.isfinite(bend[key]).all():raise ValueError('Nonfinite bending data')
    if np.any(bend['bending']<0)or np.any(bend['length']<0):raise ValueError('Negative bending material')
    if not np.isfinite(measure.numpy()).all():raise ValueError('Nonfinite Native hinge measures')
    return mask
