# SPDX-License-Identifier: GPL-3.0-or-later
"""Nonnegative least-squares residual for small contact patches."""
import numpy as np


def nnls(a,b,maxiter=None):
    """Lawson-Hanson active-set nonnegative least squares for the KKT residual."""
    original_a=np.asarray(a,float);original_b=np.asarray(b,float);n=original_a.shape[1]
    matrix_scale=float(np.max(np.abs(original_a),initial=0.))
    rhs_scale=float(np.max(np.abs(original_b),initial=0.))
    if not matrix_scale or not rhs_scale or not n:
        return np.zeros(n),float(np.linalg.norm(original_b))
    # Normalize both sides before choosing a stopping tolerance. An absolute
    # floor on the original RHS would turn a small nonzero KKT residual into
    # an apparent relative residual of one.
    a=original_a/matrix_scale;b=original_b/rhs_scale
    x=np.zeros(n);passive=np.zeros(n,bool);iterations=0
    limit=maxiter if maxiter is not None else max(1000,10*n)
    tolerance=10*np.finfo(float).eps*max(a.shape)*max(1.,np.linalg.norm(a,1))*max(1.,np.linalg.norm(b))
    while True:
        gradient=a.T@(b-a@x);eligible=(~passive)&(gradient>tolerance)
        if not eligible.any():
            x=x*(rhs_scale/matrix_scale)
            return x,float(np.linalg.norm(original_a@x-original_b))
        index=int(np.argmax(np.where(eligible,gradient,-np.inf)));passive[index]=True
        while True:
            iterations+=1
            if iterations>limit:raise RuntimeError('NNLS iteration limit')
            trial=np.zeros(n);trial[passive]=np.linalg.lstsq(a[:,passive],b,rcond=None)[0]
            if np.all(trial[passive]>0.):x=trial;break
            blocked=passive&(trial<=0.)
            alpha=np.min(x[blocked]/(x[blocked]-trial[blocked]))
            x+=alpha*(trial-x)
            leaving=passive&(x<=tolerance);x[leaving]=0.;passive[leaving]=False
