# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU shadow prototype for a coupled layer patch. Not connected to Live.

Coordinates and force/H inputs use meters, N, N/m. Optimization uses millimeters.
The caller must collect every affected contact and validate new intersections.
"""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import linprog, minimize, nnls


def closest_triangle(p, triangle):
    a, b, c = triangle
    e, f = b-a, c-a
    gram = np.array([[e@e, e@f], [e@f, f@f]])
    candidates = []
    if np.linalg.det(gram) > 1.e-28:
        u, v = np.linalg.solve(gram, [e@(p-a), f@(p-a)])
        bary = np.array([1.-u-v, u, v])
        if bary.min() >= 0.:
            candidates.append((bary@triangle, bary))
    for i, j in ((0, 1), (1, 2), (2, 0)):
        delta = triangle[j]-triangle[i]
        t = np.clip((p-triangle[i])@delta/max(delta@delta, 1.e-30), 0., 1.)
        bary = np.zeros(3); bary[i] = 1.-t; bary[j] = t
        candidates.append((bary@triangle, bary))
    return min(candidates, key=lambda item: np.sum((p-item[0])**2))


def closest_edges(a, b, c, d):
    u, v = b-a, d-c
    aa, bb, cc = u@u, u@v, v@v
    candidates = []
    determinant = aa*cc-bb*bb
    if determinant > 1.e-28:
        rhs = c-a
        s, t = np.linalg.solve([[aa, -bb], [-bb, cc]], [u@rhs, -v@rhs])
        if 0. <= s <= 1. and 0. <= t <= 1.:
            candidates.append((s, t))
    for s in (0., 1.):
        candidates.append((s, np.clip((a+s*u-c)@v/max(cc, 1.e-30), 0., 1.)))
    for t in (0., 1.):
        candidates.append((np.clip((c+t*v-a)@u/max(aa, 1.e-30), 0., 1.), t))
    s, t = min(candidates, key=lambda st: np.sum((a+st[0]*u-c-st[1]*v)**2))
    return a+s*u, c+t*v, np.array([1.-s, s, t-1., -t])


@dataclass
class Contact:
    kind: str
    nodes: tuple
    normal: np.ndarray
    key: tuple = ()
    weights: object = None
    point: object = None
    t: float = 0.
    sign: float = 1.

    def evaluate(self, x):
        q = x[list(self.nodes)]
        n = self.normal
        if self.kind == 'vt':
            cp, bary = closest_triangle(q[3], q[:3])
            weights = np.r_[-bary, 1.]
            gap = (q[3]-cp)@n
        elif self.kind == 'ee':
            p, cp, weights = closest_edges(*q)
            gap = (p-cp)@n
        elif self.kind == 'body':
            weights = np.asarray(self.weights)
            gap = (weights@q-self.point)@n
        elif self.kind == 'witness':
            p = (1.-self.t)*q[0]+self.t*q[1]
            cp, bary = closest_triangle(p, q[2:])
            n = np.cross(q[3]-q[2], q[4]-q[2])
            length = np.linalg.norm(n)
            if length < 1.e-14:
                raise ValueError('Degenerate witness support')
            n = n/length*self.sign
            weights = np.r_[1.-self.t, self.t, -bary]
            gap = (p-cp)@n
        else:
            raise ValueError(self.kind)
        return float(gap), weights[:, None]*n


def solve_shadow(x, movable, force, hessian, contacts, primary, *,
                 trust_mm=.02, goal_mm=.01, max_relinearizations=8,batch=False,qp_backend=None):
    """Propose only; non-linear contact verification is mandatory before caller acceptance."""
    x = np.asarray(x, float); movable = np.asarray(movable, int)
    mapping = {int(v): i for i, v in enumerate(movable)}
    width = 3*len(movable)
    matrix = np.zeros((width, width)); eigenvalue_clamps = 0
    for j, vertex in enumerate(movable):
        block = (hessian[vertex]+hessian[vertex].T)*.5
        eig, basis = np.linalg.eigh(block)
        floor = max(1.e-8, np.max(np.abs(eig))*1.e-8)
        eigenvalue_clamps += int(np.sum(eig < floor))
        matrix[3*j:3*j+3, 3*j:3*j+3] = (basis*np.maximum(eig, floor))@basis.T
    scale = max(float(np.max(np.diag(matrix))), 1.e-8)
    matrix /= scale
    linear = force[movable].reshape(-1)*1000./scale
    initial = np.array([row.evaluate(x)[0]*1000. for row in contacts])
    desired = initial.copy(); desired[primary] += goal_mm
    if goal_mm == 0. and not np.any(linear):
        return {'accepted':True, 'reason':'exact_stationary_no_op', 'positions':x.copy(),
                'movable':movable, 'history':[], 'gap_before_mm':initial,
                'gap_after_mm':initial.copy(), 'primary_gain_mm':0.,
                'max_vertex_displacement_mm':0., 'objective_before':0.,
                'objective_after':0., 'kkt_relative_residual':0.,
                'hessian_eigenvalue_clamps':eigenvalue_clamps}

    def geometry(d):
        y = x.copy(); y[movable] += d.reshape(-1, 3)*.001
        gaps = []; jacobian = np.zeros((len(contacts), width))
        for i, row in enumerate(contacts):
            gap, jac = row.evaluate(y); gaps.append(gap*1000.)
            for vertex, gradient in zip(row.nodes, jac):
                if int(vertex) in mapping:
                    j = mapping[int(vertex)]; jacobian[i, 3*j:3*j+3] += gradient
        return y, np.asarray(gaps), jacobian

    if batch:
        from layer_patch_geometry import BatchGeometry
        geometry=BatchGeometry(x,movable,contacts)

    d = np.zeros(width); history = []
    for iteration in range(max_relinearizations):
        y, gaps, a = geometry(d)
        rhs = desired-gaps+a@d
        # Millimeter scaling keeps HiGHS feasibility tolerance below the micro-step.
        if qp_backend is None:
            feasible = linprog(np.zeros(width), A_ub=-a, b_ub=-rhs,
                           bounds=(-trust_mm, trust_mm), method='highs',
                           options={'primal_feasibility_tolerance':1.e-9,
                                    'dual_feasibility_tolerance':1.e-9})
            if not feasible.success:
                return {'accepted':False, 'reason':'linear_infeasible', 'message':feasible.message,
                    'history':history, 'positions':x.copy(), 'movable':movable}
            optimum = minimize(lambda z: .5*z@matrix@z-linear@z, feasible.x,
            jac=lambda z: matrix@z-linear, bounds=[(-trust_mm, trust_mm)]*width,
            constraints={'type':'ineq', 'fun':lambda z:a@z-rhs, 'jac':lambda z:a},
            method='SLSQP', options={'ftol':1.e-12, 'maxiter':200})
            if not optimum.success:
                return {'accepted':False,'reason':'reference_qp_failed','message':str(optimum.message),
                        'history':history,'positions':x.copy(),'movable':movable}
        else:
            optimum=qp_backend(matrix,linear,a,rhs,trust_mm)
            if not optimum.success:
                return {'accepted':False,'reason':'qp_unconverged','message':optimum.message,
                        'diagnostics':optimum.diagnostics,'history':history,'positions':x.copy(),'movable':movable}
        proposal = optimum.x
        y, after, jac_after = geometry(proposal)
        violation = float(np.max(desired-after))
        history.append({'iteration':iteration, 'qp_success':bool(optimum.success),
                        'qp_message':str(optimum.message), 'max_gap_violation_mm':violation,
                        'linear_violation_mm':float(np.max(rhs-a@proposal))})
        if hasattr(optimum,'diagnostics'):history[-1]['qp_diagnostics']=optimum.diagnostics
        if optimum.success and violation <= 1.e-9:
            active = jac_after[(after-desired)<1.e-7]
            for j, value in enumerate(proposal):
                if abs(value+trust_mm)<1.e-7:
                    unit=np.zeros(width); unit[j]=1.; active=np.vstack([active,unit])
                if abs(value-trust_mm)<1.e-7:
                    unit=np.zeros(width); unit[j]=-1.; active=np.vstack([active,unit])
            grad = matrix@proposal-linear
            residual = nnls(active.T, grad, maxiter=max(1000, 10*len(active)))[1] if len(active) else np.linalg.norm(grad)
            return {'accepted':True, 'reason':'contact_verified_candidate', 'positions':y,
                    'movable':movable, 'history':history,
                    'gap_before_mm':initial, 'gap_after_mm':after,
                    'primary_gain_mm':float(after[primary]-initial[primary]),
                    'max_vertex_displacement_mm':float(np.linalg.norm(y-x,axis=1).max()*1000.),
                    'objective_before':0., 'objective_after':float(optimum.fun),
                    'kkt_relative_residual':float(residual/max(np.linalg.norm(grad),1.e-20)),
                    'hessian_eigenvalue_clamps':eigenvalue_clamps}
        d = proposal
    return {'accepted':False, 'reason':'nonlinear_contact_rejected', 'history':history,
            'positions':x.copy(), 'movable':movable}
