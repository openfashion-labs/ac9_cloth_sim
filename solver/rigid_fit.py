# SPDX-License-Identifier: GPL-3.0-or-later
"""The rotation + shift that takes points P onto points Q best, keeping the current orientation where the points do
not decide it. Pure numpy: used by the add-on (Part to Seam, preassembled parts, carried followers) and by the solver.

Plain Kabsch leaves the turn about a line undecided when the points are on one straight line (a placket sewn along
one straight edge: 57 points, spread 1222 x 0.00 x 0.00 mm). The SVD then returned an arbitrary turn about the seam,
158 degrees on a shirt's placket, and the placket lay folded onto the front it was sewn to whatever way it was placed;
0.5 mm of noise on the partner's points changed that turn by up to 166 degrees.

Here the fit also pays for turning away from where the piece is now: it minimizes
    sum |R p + t - q|^2 + lam |R - I|^2 - lam * kappa * (n . R n)
with lam = len(P) * tol^2 (tol: one point's uncertainty in the caller's unit). What the points decide is theirs (the
turn moves by about tol^2 / spread^2); what they leave open stays as it is: the line is turned onto its partner by the
smallest turn and the piece keeps its turn about it. `normal` (the piece's surface normal, either sign) keeps its face
when the line itself has to turn about 180 degrees, where the smallest turn is undecided again."""
import numpy as np


def plane_normal(X):
    """A unit normal of the best plane through points X (either sign)."""
    X = np.asarray(X, float).reshape(-1, 3)
    if len(X) < 3:
        return None
    return np.linalg.svd(X - X.mean(0), full_matrices=False)[2][2]


def rigid_fit(P, Q, tol, normal=None, kappa=1.0):
    """(R, t) with Q ~ P @ R.T + t, R a proper rotation. tol: in the unit of P and Q (1 mm in mm, 1e-3 in m); there is
    no default, a tol in the wrong unit would turn the fit off (too large) or do nothing (too small)."""
    P = np.asarray(P, float).reshape(-1, 3)
    Q = np.asarray(Q, float).reshape(-1, 3)
    pc, qc = P.mean(0), Q.mean(0)
    lam = len(P) * float(tol) ** 2
    M = (P - pc).T @ (Q - qc) + lam * np.eye(3)
    if normal is not None:
        n = np.asarray(normal, float)
        n = n / max(np.linalg.norm(n), 1e-30)
        M = M + lam * kappa * np.outer(n, n)
    U, _s, Wt = np.linalg.svd(M)
    d = np.sign(np.linalg.det(Wt.T @ U.T)) or 1.0
    R = Wt.T @ np.diag([1.0, 1.0, d]) @ U.T
    return R, qc - pc @ R.T
