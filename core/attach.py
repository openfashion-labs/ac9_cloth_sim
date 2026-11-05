# SPDX-License-Identifier: GPL-3.0-or-later
"""A garment line drawn to a tape on the body while it is simulated (no bpy).

    spec["attach"] = [{"id": str,
                       "seam": seam id | "side": SIDE,     # the line: a seam (both its sides) or a stretch of a piece
                       "tape": name,                       # a tape on the body (measure_tool: a Tape Around / Along
                                                           # name) or a body measurement's line (wrist_L, neck, ...)
                       "until": "end" | "sewn",            # held in every stage / only while sewing (no gravity)
                       "enabled": bool}]

Each vertex of the line is pulled towards the nearest point of the tape (lifted off the body by the contact gap), so
the line can slide along the tape but not away from it: an open tape does not pull a vertex past its ends along
it. The pull is gentle and capped -- a force per length of the line, never stronger than PULL_N_M however far off
the vertex is -- so seams and the body still win (solver cfg 'attach', solver/newton_vbd.py).

Why (2026-10-18, experiments/solver_compare_2026-10-18 (3)): which side of a T-shirt a fold lands on follows where
its shoulder seam ends up front to back, and with friction the seam stays wherever sewing left it; moving it after
does not hold (it springs back). The way to choose where it goes is to hold it there while it is sewn.
"""
import numpy as np

from . import measure as ME
from . import spec as S

ON_HOLD = True          # on hold (user 2026-10-24): hidden from the panels and not passed to the solver; the
                        # entries stay in the spec. Redesign first: docs/reviews/REVIEW_fable_attach_measure_2026-10-24.md
UNTIL = ('end', 'sewn')
PULL_N_M = 10.0         # the cap: N per meter of line (the line's whole pull, shared by its vertices); 2 left the
                        # T-shirt's shoulder seams 7-23 mm off their tapes (2026-10-18, experiments/attach_measure)
STIFF_N_M2 = 1000.0     # below the cap: N per meter of line per meter off the tape
MASS_FRAC = 0.8         # the stiffness held under this x m / dt^2 per vertex (the solver: an explicit force, constant
                        # over a substep, overshoots at 2); at 6 mm this, not STIFF_N_M2, sets the stiffness
LIFT_MM = 3.0           # the tape lifted off the body by the contact gap (solver body_gap 3 mm): no pull into it
STEP_MM = 4.0           # the tape resampled at most this far apart


def entries(spec):
    """The enabled attach entries (none while ON_HOLD)."""
    if ON_HOLD:
        return []
    return [a for a in spec.get('attach', []) if a.get('enabled', True) and a.get('tape')]


def tape_names(geo):
    """The tapes a line can be attached to, from the body's measure lines (measure_tool GEO): (name, closed)."""
    # a straight tape is pulled taut: it spans the hollows in the air, a line held to it would leave the body there
    out = [(k, bool(g.get('closed'))) for k, g in (geo.get('tapes') or {}).items() if not g.get('straight')]
    out += [(k, bool(g.get('closed'))) for k, g in (geo.get('items') or {}).items()
            if len(g.get('pts', [])) > 1 and ME.KIND.get(k) != 'height']     # a height is a line up from the floor
    return out


def tape_points(geo, name):
    """(points (k, 3) body-local mm, closed) of a tape or a measurement's line, or None."""
    for key in ('tapes', 'items'):
        g = (geo.get(key) or {}).get(name)
        if g and len(g.get('pts', [])) > 1:
            return np.asarray(g['pts'], float), bool(g.get('closed'))
    return None


def resample(P, closed, step=STEP_MM):
    """P with extra points so no two neighbors are further apart than step."""
    Q = np.vstack([P, P[:1]]) if closed else P
    out = []
    for a, b in zip(Q[:-1], Q[1:]):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.append(a + (b - a) * (np.arange(n) / n)[:, None])
    out.append(Q[-1:])
    R = np.vstack(out)
    return R[:-1] if closed else R


def line_vertices(spec, att, names, pat, offs, E_st, E_seam, tol_mm=0.5):
    """(garment vertex indices, line length mm) of an entry's line, or None. A seam: the vertices its stitches join
    (both sides). A side: the piece's mesh vertices on the side's flat polyline."""
    if att.get('seam') is not None:
        if E_seam is None:
            return None
        m = np.asarray(E_seam).astype(str) == str(att['seam'])
        if not m.any():
            return None
        sm = next((x for x in spec.get('seams', []) if str(x['id']) == str(att['seam'])), None)
        L = 0.0
        if sm is not None:
            L = sum(S.side_length(spec, sm[k]) for k in ('a', 'b') if sm[k]['piece'] in names)
        return np.unique(np.asarray(E_st)[m].reshape(-1)), L
    side = att.get('side')
    if not side or side.get('piece') not in names:
        return None
    i = names.index(side['piece'])
    P = S.side_polyline(spec, side, step=1.0)
    V = np.asarray(pat[f"V{i}"], float)
    d = np.full(len(V), np.inf)
    for a, b in zip(P[:-1], P[1:]):
        e = b - a
        t = np.clip(((V - a) @ e) / max(float(e @ e), 1e-12), 0.0, 1.0)
        d = np.minimum(d, np.linalg.norm(V - (a + t[:, None] * e), axis=1))
    k = np.nonzero(d < tol_mm)[0]
    if not len(k):
        return None
    return offs[i] + k, S.polyline_len(P)


def write(path, groups):
    """The solver's attach file from [(verts (n,), length mm, tape points (k, 3) world mm lifted, closed)]."""
    V, G, W, P, s0, s1, cl = [], [], [], [], [], [], []
    n = 0
    for g, (verts, L, T, closed) in enumerate(groups):
        verts = np.asarray(verts, np.int64)
        V.append(verts)
        G.append(np.full(len(verts), g, np.int32))
        W.append(np.full(len(verts), L / 1000.0 / max(len(verts), 1)))      # each vertex's share of the line, m
        s0.append(n)
        n += len(T)
        s1.append(n)
        P.append(np.asarray(T, float) / 1000.0)
        cl.append(int(closed))
    np.savez(path, verts=np.concatenate(V), group=np.concatenate(G), w=np.concatenate(W), pts=np.vstack(P),
             seg0=np.asarray(s0, np.int32), seg1=np.asarray(s1, np.int32), closed=np.asarray(cl, np.int32))


def stage_on(att, cfg):
    """Whether an entry pulls in a stage: 'sewn' only while there is no gravity (the sewing stages)."""
    if att.get('until', 'end') == 'sewn':
        return float(cfg.get('gravity_z', -9.81)) == 0.0
    return True
