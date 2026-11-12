# SPDX-License-Identifier: GPL-3.0-or-later
"""Double-precision primal active-set QP for the isolated patch experiment."""
from types import SimpleNamespace
import time
import numpy as np


def qr(a, mode="economic"):
    return np.linalg.qr(a, mode="reduced")


def solve_triangular(a, b, lower=False):
    return np.linalg.solve(a, b)


def feasible_point(a, rhs, center):
    """Find a feasible start with the existing active-set QP, plus one slack.

    Only Phase I uses the small center regularization. The actual contact QP
    and all of its acceptance thresholds remain unchanged. A positive final
    slack never relaxes the original constraints: they are checked explicitly.
    """
    n=len(center)
    constraints=np.vstack([np.column_stack([a,np.ones(len(a))]),np.r_[np.zeros(n),1.]])
    limits=np.r_[rhs,0.]
    slack=max(0.,float(np.max(rhs-a@center)))+1.e-4
    initial=np.r_[center,slack]
    trust=max(10.,float(np.max(np.abs(center)))*2.,slack*2.)
    for regularization in (1.e-6,1.e-8,1.e-10):
        matrix=np.diag(np.r_[np.full(n,regularization),1.])
        linear=np.r_[center*regularization,-1.]
        result=solve_dense_qp(matrix,linear,constraints,limits,trust,
                              feasible_start=initial,max_iterations=3000)
        point=result.x[:n]
        violation=float(np.max(rhs-a@point))
        # A feasible point is sufficient, even if the auxiliary objective has
        # not converged. Never use its slack as proof of infeasibility.
        if np.isfinite(point).all() and violation<=1.e-9:
            return SimpleNamespace(success=True,x=point,message='verified feasible start')
    return SimpleNamespace(success=False,x=point,message='feasible start not found')


def solve_dense_qp(matrix,linear,a,rhs,trust_mm,*,max_iterations=1000,feasible_start=None,working_start=None):
    started=time.perf_counter();n=len(linear);a=np.vstack([a,np.eye(n),-np.eye(n)]);rhs=np.r_[rhs,np.full(2*n,-trust_mm)]
    center=np.clip(np.linalg.solve(matrix,linear),-trust_mm,trust_mm)if feasible_start is None else np.asarray(feasible_start)
    if np.max(rhs-a@center)<=1.e-10:
        phase=SimpleNamespace(success=True,x=center.copy(),message='previous point feasible')
    else:
        phase=feasible_point(a,rhs,center)
    if not phase.success:
        return SimpleNamespace(x=np.zeros(n),success=False,message='active-set feasible start not found',fun=0.,diagnostics={'phase_message':phase.message,'total_seconds':time.perf_counter()-started})
    lower=np.linalg.cholesky(matrix);raw=solve_triangular(lower,a.T,lower=True).T
    scale=np.linalg.norm(raw,axis=1);usable=scale>1.e-16
    if np.any((~usable)&(rhs>1.e-9)):
        return SimpleNamespace(x=phase.x,success=False,message='zero row infeasible',fun=0.,diagnostics={})
    normalized=raw/np.maximum(scale[:,None],1.e-30);z=lower.T@phase.x;g=solve_triangular(lower,linear,lower=True)
    phase_finished=time.perf_counter();working=[];q=np.zeros((n,0));r=np.zeros((0,0));rank_tolerance=1.e-12

    def basis(indices):
        return qr(normalized[indices].T,mode='economic')if indices else(np.zeros((n,0)),np.zeros((0,0)))

    def independent(index):
        vector=normalized[index];return np.linalg.norm(vector-q@(q.T@vector))>rank_tolerance

    slack=raw@z-rhs
    candidates=list(np.flatnonzero((slack<=1.e-9)&usable))
    if working_start is not None:
        candidates=[i for i in working_start if i in candidates]+[i for i in candidates if i not in working_start]
    for index in candidates:
        if independent(index):working.append(int(index));q,r=basis(working)
    visited={};message='active-set iteration limit';success=False;multiplier=np.zeros(len(rhs));d=phase.x
    for iteration in range(max_iterations):
        gradient=z-g;direction=-gradient+q@(q.T@gradient)
        if np.linalg.norm(direction)<=1.e-11*max(1.,np.linalg.norm(gradient)):
            lambdas=solve_triangular(r,q.T@gradient)if working else np.empty(0)
            if not len(lambdas)or np.min(lambdas)>=-1.e-10:
                multiplier[:]=0
                if working:multiplier[working]=lambdas/scale[working]
                success=True;message='active-set stationary';break
            smallest=float(np.min(lambdas));choice=min(j for j,value in enumerate(lambdas)if value<=smallest+1.e-13)
            working.pop(choice);q,r=basis(working);continue
        slack=raw@z-rhs;denominator=raw@direction
        blocked=(denominator < -1.e-13*np.maximum(scale*np.linalg.norm(direction),1.e-12))&usable
        # Near-dependent rows with only roundoff-sized slack do not shorten a
        # direction that remains within the same final 1e-9 mm feasibility gate.
        blocked &= (slack+denominator < -1.e-9)
        if working:blocked[working]=False
        candidates=np.flatnonzero(blocked);alpha=1.;hit=None
        if len(candidates):
            ratios=np.maximum(slack[candidates],0.)/(-denominator[candidates]);smallest=float(np.min(ratios))
            if smallest<1.:
                ties=candidates[ratios<=smallest+1.e-13];hit=int(np.min(ties));alpha=max(0.,smallest)
        z=z+alpha*direction
        if hit is not None:
            if independent(hit):working.append(hit);q,r=basis(working)
            elif alpha<1.e-12:
                message='dependent blocker: conservative refusal';break
        signature=tuple(working)
        objective=float(.5*z@z-g@z)
        old=visited.get(signature)
        if old is not None and abs(old[0]-objective)<1.e-16:
            visited[signature]=(objective,old[1]+1)
            if old[1]>=4:message='active-set cycle refused';break
        else:visited[signature]=(objective,0)
    d=solve_triangular(lower.T,z,lower=False);slack=a@d-rhs
    primal=float(max(0.,-slack.min()));dual=float(max(0.,-multiplier.min()));stationary=matrix@d-linear-a.T@multiplier
    relative=float(np.linalg.norm(stationary)/max(np.linalg.norm(linear),np.linalg.norm(matrix@d),1.e-20))
    complement=float(np.max(np.abs(multiplier*slack)))
    success=bool(success and np.isfinite(d).all()and primal<=1.e-9 and relative<=1.e-7 and dual<=1.e-8 and complement<=1.e-8)
    return SimpleNamespace(x=d,success=success,message=message,fun=float(.5*d@matrix@d-linear@d),working_set=working.copy(),diagnostics={'iterations':iteration+1,'working_rank':len(working),'max_primal_violation_mm':primal,'min_dual_violation':dual,'stationarity_relative_residual':relative,'complementarity_max':complement,'phase_seconds':phase_finished-started,'active_seconds':time.perf_counter()-phase_finished,'total_seconds':time.perf_counter()-started})


class LocalWarmStart:
    """Reuse only within one fixed shadow problem's relinearizations, never across frames."""
    def __init__(self):
        self.x=None;self.working=None

    def __call__(self,matrix,linear,a,rhs,trust_mm):
        result=solve_dense_qp(matrix,linear,a,rhs,trust_mm,feasible_start=self.x,working_start=self.working)
        if result.success:self.x=result.x.copy();self.working=result.working_set
        return result
