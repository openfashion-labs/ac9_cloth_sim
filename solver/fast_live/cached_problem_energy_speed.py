# SPDX-License-Identifier: GPL-3.0-or-later
"""Memoize exact repeated inputs within one immutable Native substep.

Not valid for changing friction history. Constructor context must be reset at
every Native step. No tolerance-based keys or approximate energy values.
"""
import numpy as np
from .fixed_problem_energy import FixedProblemEnergy

class CachedProblemEnergy(FixedProblemEnergy):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.last_q=None;self.last_result=None
        self.cache_hits=0;self.evaluation_calls=0
    def evaluate(self,q,body):
        self.evaluation_calls+=1
        if self.last_q is not None and np.array_equal(q,self.last_q):
            self.cache_hits+=1
            # Contact detection can reorder contacts or update their geometry.
            # Recalculate body energy on every call; only the six body-independent
            # terms and Native hinge geometry are reused for identical q.
            q64=np.asarray(q,float);ids=body['indices'][:,0]
            distance=np.sum(body['normal'].astype(float)*(q64[ids]-body['point']),axis=1)
            penetration=np.maximum(self.radius[ids]+self.margin-distance,0)
            ce=.5*np.sum(body['ke'].astype(float)*penetration**2)
            parts=self.last_result[1].copy();parts['body_penalty']=float(ce)
            return float(sum(parts.values())),parts,self.last_result[2]
        result=super().evaluate(q,body)
        self.last_q=np.array(q,dtype=float,copy=True)
        self.last_result=result
        return result
