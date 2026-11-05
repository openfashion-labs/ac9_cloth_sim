# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np

def validate_springs(n,indices,rest,ke,kd):
    count=len(indices)
    if indices.shape!=(count,2)or any(a.shape!=(count,)for a in [rest,ke,kd]):raise ValueError('Spring array shape mismatch')
    if count and(indices.min()<0 or indices.max()>=n or np.any(indices[:,0]==indices[:,1])):raise ValueError('Invalid spring endpoint')
    if any(not np.isfinite(a).all()or np.any(a<0)for a in [rest,ke,kd]):raise ValueError('Spring parameters must be finite and nonnegative')
