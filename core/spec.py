# SPDX-License-Identifier: GPL-3.0-or-later
"""The garment spec: the one source of truth the add-on edits (no bpy).

A spec is plain JSON, stored in the .blend as a Text datablock, so a person, the UI and an
external tool (MCP) all read and write the same thing.

    {
      "version": 1, "name": str, "units": "mm",
      "source": {"kind": "bom" | "manual" | "mesh", "path": str},   # mesh: {"object": name} (core/mesh_to_spec.py)
      "pieces": [
        {"name": str,                       # unique
         "fabric": str,                     # the source's fabric name, kept as a note: never read
         "fabric_id": str,                  # optional (2026-10-14, core/fabric.py): the piece's fabric, an id of
                                            # "fabrics"; absent = the default_fabric_id (2026-10-20: it follows a
                                            # changed default)
         "strengthen": bool, "strengthen_factor": float, # optional; off / 5 by default, multiplier 1..100
         "bond_id": str,                    # optional (2026-10-25): its interlining, an id of "interlinings",
                                            # added to the fabric's weight and bending. A twin takes both alike.
                                            # Before 2026-10-25 "bond": {"preset", "name", "weight_gsm",
                                            # "bending_uNm", "source"} held it whole (core/fabric.migrate moves it)
         "edges": [{"id": str, "pts": [[x, y], ...]}, ...],   # the closed outline, counter-clockwise
         "internals": [{"id": str, "pts": [[x, y], ...],     # internal lines (sewn or folded); kind "line" (drawn),
                        "kind": str, "closed": bool,       # "baseline" (BOM, not meshed), "shape" (closed: last pt = first)
                        "hole": bool}],                    # optional (2026-10-25): the edge of a hole cut out of the
                                                           # piece (a dart's diamond); lines that chain into a closed
                                                           # loop make one hole (holes() below), no cloth inside it
         "enabled": bool,
         "frozen": bool,                    # optional: stays put in the simulation (still touched, still sewn)
         "contact": bool,                   # optional, default true; false with frozen = nothing touches it, the
                                            # moving cloth goes through, its seams still pull. Read the four
                                            # states with piece_state() (never store a state key, P1 #9)
         "role": str,                       # what the piece is: an id of core/roles.VOCAB (front, placket, stand,
                                            # cuff, waistband ...; the old "auto" = "other", "top" = "trim", an
                                            # accessory laid on last). Its unit decides the stage it is sewn in
         "role_by": "rule" | "source" | "ai" | "user",   # who set it; "user" is never overwritten by a proposal
         "role_conf": float, "role_why": str, "role_ask": bool,   # a proposal's confidence, reason, still to check
         "thickness": False,                # optional (2026-10-23): no drawn thickness on this piece (a placket
                                            # folded flat on itself, user's choice by hand; display only)
         "garment": str,                    # optional (2026-10-25): an id of "garments"; a BOM's seams are paired
                                            # within a garment (core/bom_to_spec.rebuild_seams), a garment is also
                                            # picked as a selection (ui.draw_garments). Absent: one garment. Not the
                                            # spec's "garment" (the kind proposed by core/roles.py)
         "mesh_mm": float,                  # optional (2026-10-08): this piece's own mesh size (the Piece Mesh); a
                                            # mirrored pair takes the finer of its two (core/pattern_build.py)
         "family": str, "mirrored": bool,   # optional: a drawn piece and its mirrored copy share a family (twins)
         "mirror_edit": bool,               # optional (2026-10-21): a piece symmetric about its center line, edited
                                            # on one side is edited on the other too (core/draft.symmetric_edit)
         "closes_onto": name,               # optional (2026-10-22): the piece it buttons onto, set by hand (Closes
                                            # onto), written on both; its buttons' holes go there (core/buttons.py).
                                            # Both placed on the torso guide: a front closure as "closures" is
                                            # (core/roles.declared_closures)
         "notches": [{"id": str, "edge": edge id, "t": float,     # optional (2026-10-09): matching marks on the
                      "kind": "slit" | "v" | "t"}]}               # outline, held like a seam end (core/walk.py);
                                                                 # a seam with notches paired on both sides is
                                                                 # sewn notch to notch (2026-10-13, pattern_build)
      ],
      "seams": [
        {"id": str, "a": SIDE, "b": SIDE, "source": "bom" | "user" | "mesh", "check": str,   # check: why to look
         "enabled": bool}                                                           # off: kept, not sewn
      ],                                    # a BOM seam's "bom": {"gid", "pieces", "group", "garment"}
      "garments": [{"id": str, "name": str, "source": "bom" | "user", "check": str}],   # optional (2026-10-25):
                                            # the pieces' garments (a BOM split at import, core/bom_to_spec
                                            # .garments_from_bom; check: split by the layout, not sure). Not the
                                            # spec's "garment" below (one kind proposed for the enabled pieces)
      "placement": {piece name: {...}},     # {guide, theta, h, anchor} (placement.py); "unlinked": true on both of a
                                            # mirrored pair = placed independently (a monster's arms, 2026-10-10);
                                            # "pose": {"loc": [x, y, z] mm, "rot": [x, y, z] deg} = the piece moved
                                            # by hand from where the numbers put it (2026-10-14, placed_view.py): the
                                            # placed shape is turned by rot (Euler XYZ) about its own middle (the
                                            # center of its bounding box as made) and moved by loc. Absent = not
                                            # moved. A mirrored pair's twin holds the mirror image (mirror_pose)
      "pairs": [[piece, piece], ...],        # optional: two pieces placed as mirror images though not drawn as a
                                            # mirrored copy (Pair, 2026-10-10); their meshes stay their own
      "folds": [{"piece": name, "line": internal id, "deg": float}    # + = mountain seen from the pattern's face; optional
                                            # "side": "A" / "B", the side Fold turns on the placed shape (A: the left
                                            # walking the line from its first point; not read by the solver, 10/24)
                                            # "side": "NONE" keeps the angle but skips the placed turn (10/30).
                                            # Missing side on old folds retains the legacy A default.
                                            # "layers": "A" / "B", the side kept when the fold is simulated as one
                                            # layer (the other bound to it, dress.one_layer_rows; absent = both, 10/24)
                | {"seam": seam id, "deg": float, "turn": piece}],       # a fold along a seam: `turn` is the
                                                                         # piece that turns over (a collar on its stand)
      "closures": [[piece, piece], ...],     # a front closure: its seams are sewn only once everything hangs
                                             # (absent, and no torso pieces' closes_onto: the two pieces with role
                                             # "placket" sewn to each other)
      "elastic": [{"piece": name, "ratio": float,      # optional (2026-10-13, core/elastic.py): cloth whose rest
                   "line": internal id | "side": SIDE,  # length is ratio x the pattern's along the line (a band w mm
                   "w": mm, "dir": [dx, dy],            # each side), or the whole piece along its long axis / dir --
                   "strength": float, "enabled": bool}],  # an elastic waistband, a cuff, shirring; a twin follows
      "fabrics": [{"id": str, "name": str, "preset": str | null, "source": str,   # (2026-10-14 / 10-20, core/
                   "weight_gsm": float, "bending_uNm": float | {"warp", "weft"},  # fabric.py) the garment's fabrics,
                   "stretch_N_m": float | {"warp", "weft"},   # as numbers (a preset's copied in); N/m, warp = flat x;
                   "shear_N_m": float, "bend_hyst_per_m": float,   # kappa_y = 2HB / 2B (1/m), the bending hysteresis
                   "thickness_mm": float | null}],      # the display only (a Solidify), not simulated
      "default_fabric_id": str,                 # (2026-10-20) the garment's default, one of "fabrics": a new spec
                                                # starts with Cotton Jersey (Poplin before 2026-11-12); one saved before gets it from the old
                                                # Dress panel (core/fabric.migrate)
      "interlinings": [{"id": str, "name": str, "preset": str | null,   # (2026-10-25) the garment's interlinings,
                        "source": str, "weight_gsm": float,         # what each adds to the fabric of the pieces
                        "bending_uNm": float}],                     # with it (pieces[].bond_id): an edit reaches all
      "attach": [{"id": str, "seam": seam id | "side": SIDE,   # optional (2026-10-18, core/attach.py): a line held
                  "tape": name,                             # to a tape on the body while simulated (Attach to
                  "until": "end" | "sewn",                  # Measure): a measure tape's name or a measurement's key
                  "enabled": bool}],                        # (wrist_L ...); outline SIDE ends follow draft edits
      "garment": {"kind": "shirt" | "jacket" | "top" | "pants" | "skirt" | "other", "by": str, "conf": float},
                                            # the kind proposed for all the enabled pieces together (core/roles.py),
                                            # not "garments" (the groups a BOM is split into) nor pieces[].garment
      "measure": {"targets": {key: mm},      # optional (measure_tool.py, 2026-10-09): target body sizes, keys as
                                             # core/measure.py (height, chest, waist, hip, upper_arm_L, ...)
                  "pattern": [{"name": str, "a": {"piece", "edge", "t"}, "b": {...}}]}   # pattern lengths to watch,
                                             # points as edge id + fraction like a seam's (never point ids)
    }

A BOM piece keeps "bom": {"idx", "inst_gid", "family", "arrangement"} (arrangement: the source's arrangement point name, a
hint for the role, core/roles.py).

Explicit BOM seam updates preserve geometry, placement, material settings and manual groups/seams. Only automatic
groups are reconciled; incompatible pattern references or removal of a seam with settings/references is refused.
A new BOM import retires the active BOM's result and its compatible Text versions (their placement/guides cleared),
without deleting external job files or other garments. Old versions have distinct names and never share the new result.

An edge may also hold "ctrl": [[x, y], [x, y]], the two handles of a cubic Bezier from its first to its last
point (a curved edge drawn or bent in the add-on, core/draft.py); its "pts" are then made from the curve and are
still what everything else reads.

Coordinates are the 2D pattern layout in mm. A SIDE is a stretch of one piece:

    outline:  {"piece": name, "from": [edge id, t], "to": [edge id, t], "dir": +1 | -1}
    internal: {"piece": name, "line": internal id, "from": t, "to": t}

Cut restates interval, line, zipper and point owners and split-seam dependents on the corresponding half.
Elastic bands are partitioned only when their finite-width field can be preserved; an unrepresentable band or
reference refuses the complete cut, including its twin, without changing the spec. Shared baseline bands retain
their full line on both halves. Whole-piece elastic direction and inheritance are a separate unresolved product
decision; these guarantees do not claim that whole-piece settings are already preserved by Cut.

Result reference queries do not write legacy ac9_job properties. Stored result references need job.json and
scene.idx.npz; cache-derived index-only references remain readable, while drawing may retain a stored directory.
These file capabilities are distinct from solver resume validation. Explicit run/load boundaries repair recovered
references idempotently. Relative cache paths resolve against their owning library, without modifying linked data.

Material queries preserve saved Text and Material properties. Observed legacy look references keep a temporary,
verified Material identity across rename/history. Explicit save repair rechecks the saved owner and the current
UID/name resolution before updating its reference. It never replaces a Material's nonempty UID or copies a
default/twin-inherited look onto a piece. Conflicting saved UID claims are retained rather than arbitrated.

t is the fraction of that edge's length (0 = its start, 1 = its end, edges run counter-clockwise);
the side runs from "from" to "to", counter-clockwise (+1) or clockwise (-1) round the outline,
over as many edges as it takes. The two sides of a seam are paired point for point from their
"from" ends. Point ids are never used: an edit that re-meshes a piece keeps every seam valid.
"""
import json

import numpy as np

SPEC_VERSION = 1


def new_spec(name, source=None):
    from . import fabric                      # (fabric imports this module)
    spec = {'version': SPEC_VERSION, 'name': name, 'units': 'mm', 'source': source or {'kind': 'manual'},
            'pieces': [], 'seams': [], 'placement': {}, 'folds': []}
    fabric.add_default(spec)                  # every garment has a default fabric (user 2026-10-20)
    return spec


def from_bom(spec):
    """The spec was read from a BOM.xml. Its source kind is 'bom'; specs saved before 2026-10-21 carry an older
    name for it that also ends in '_bom' (TASKS 84), so both are read the same."""
    k = str((spec.get('source') or {}).get('kind', ''))
    return k == 'bom' or k.endswith('_bom')


def dumps(spec):
    return json.dumps(spec, ensure_ascii=False, indent=1)


def loads(text):
    spec = json.loads(text)
    if spec.get('version') != SPEC_VERSION:
        raise ValueError(f"spec version {spec.get('version')} (expected {SPEC_VERSION})")
    return spec


def polyline_len(P):
    P = np.asarray(P, float)
    return float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum()) if len(P) > 1 else 0.0


def resample(P, n):
    """n points equally spaced in arc length along the polyline P."""
    P = np.asarray(P, float)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    s = np.linspace(0, cum[-1], n)
    return np.stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])], 1)


class Outline:
    """A piece's closed outline with arc-length lookups per edge."""

    def __init__(self, piece):
        self.edges = {}
        self.order = []
        pts, s0 = [], 0.0
        for e in piece['edges']:
            P = np.asarray(e['pts'], float)
            L = polyline_len(P)
            self.edges[e['id']] = (s0, L, P)
            self.order.append(e['id'])
            pts.append(P[:-1])
            s0 += L
        self.B = np.vstack(pts)
        self.L = s0
        self.plane = plane_of(piece)       # the plane its pair mirrors in (a pair's pieces both hold it)
        Bc = np.vstack([self.B, self.B[:1]])
        self.cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(Bc, axis=0), axis=1))]
        self._Bc = Bc

    def s_of(self, edge, t):
        s0, L, _ = self.edges[edge]
        return s0 + float(t) * L

    def at(self, s):
        s = np.mod(np.asarray(s, float), self.L)
        return np.stack([np.interp(s, self.cum, self._Bc[:, 0]), np.interp(s, self.cum, self._Bc[:, 1])], -1)

    def edge_t(self, s):
        """(edge id, t) of an outline parameter s."""
        s = float(np.mod(s, self.L))
        for eid in self.order:
            s0, L, _ = self.edges[eid]
            if s <= s0 + L + 1e-9:
                return eid, (s - s0) / L if L > 0 else 0.0
        return self.order[-1], 1.0

    def span(self, side):
        """Signed outline length the side covers (> 0 counter-clockwise)."""
        s0 = self.s_of(*side['from'])
        s1 = self.s_of(*side['to'])
        if side['dir'] > 0:
            return float(np.mod(s1 - s0, self.L))
        return -float(np.mod(s0 - s1, self.L))

    def side_polyline(self, side, step=1.0):
        """The side as a polyline from 'from' to 'to' (outline points kept, extra samples every step mm)."""
        s0 = self.s_of(*side['from'])
        span = self.span(side)
        n = max(2, int(abs(span) / step) + 1)
        return self.at(s0 + np.linspace(0.0, span, n))


class Outlines:
    """Outline of each piece by name (pieces: {name: piece}), made when first asked for: a garment from a BOM holds
    every piece of the file (133 on a pants), of which a pattern edit's carry and the seam list read a few
    (2026-10-27). Only `name in` and `[name]`."""

    def __init__(self, pieces):
        self._p, self._o = pieces, {}

    def __contains__(self, n):
        return n in self._p

    def __getitem__(self, n):
        o = self._o.get(n)
        if o is None:
            o = self._o[n] = Outline(self._p[n])
        return o


HOLE_TOL = 0.05      # mm: two hole lines whose ends lie this close are joined (the BOM's lines share their end points)


def holes(piece):
    """The holes cut out of a piece: [(line ids, ring (n, 2) without the first point repeated)], from its internal
    lines marked "hole" (a BOM's Hole shape is several lines, a drawn shape one closed line). Lines that do not chain
    into a closed loop make no hole (an open cut is a slit, not a hole)."""
    lines = [(it['id'], np.asarray(it['pts'], float)) for it in piece.get('internals', [])
             if it.get('hole') and len(it['pts']) >= 2]
    out, left = [], list(lines)
    while left:
        ids, ring = [left[0][0]], [left[0][1]]
        left = left[1:]
        while np.linalg.norm(ring[-1][-1] - ring[0][0]) > HOLE_TOL:
            end = ring[-1][-1]
            k = next((i for i, (_, P) in enumerate(left)
                      if min(np.linalg.norm(P[0] - end), np.linalg.norm(P[-1] - end)) <= HOLE_TOL), None)
            if k is None:
                break
            lid, P = left.pop(k)
            ids.append(lid)
            ring.append(P if np.linalg.norm(P[0] - end) <= HOLE_TOL else P[::-1])
        R = np.vstack([ring[0]] + [P[1:] for P in ring[1:]])
        if len(R) >= 4 and np.linalg.norm(R[-1] - R[0]) <= HOLE_TOL:
            out.append((ids, R[:-1]))
    return out


def in_holes(Q, hole_rings):
    """Points Q (n, 2) inside any of the rings (even-odd per ring)."""
    Q = np.asarray(Q, float).reshape(-1, 2)
    inside = np.zeros(len(Q), bool)
    for R in hole_rings:
        A, B = R, np.roll(R, -1, 0)
        ya, yb = A[None, :, 1], B[None, :, 1]
        cond = (ya > Q[:, None, 1]) != (yb > Q[:, None, 1])
        with np.errstate(divide='ignore', invalid='ignore'):
            xint = A[None, :, 0] + (Q[:, None, 1] - ya) * (B[None, :, 0] - A[None, :, 0]) / (yb - ya)
        inside |= (cond & (Q[:, None, 0] < xint)).sum(1) % 2 == 1
    return inside


PIECE_STATES = ('active', 'freeze', 'off_pattern', 'off_all')


def piece_state(pc):
    """One of PIECE_STATES (four), from the keys a piece keeps: enabled false -> off_all (not meshed, its seams
    dropped); frozen and contact false -> off_pattern (stays put, nothing touches it, its seams still pull); frozen ->
    freeze (stays put, touched); else active. Read every time, never stored: a tool that writes only `enabled` stays
    consistent (Fable review 2026-10-13)."""
    if not pc.get('enabled', True):
        return 'off_all'
    if pc.get('frozen'):
        return 'freeze' if pc.get('contact', True) else 'off_pattern'
    return 'active'


STRENGTHEN_DEFAULT = 5.0
STRENGTHEN_MAX = 100.0


def piece_strengthen_factor(pc):
    """The stored multiplier, including while Strengthen is off."""
    import math
    try:
        v = float(pc.get('strengthen_factor', STRENGTHEN_DEFAULT))
        return min(STRENGTHEN_MAX, max(1.0, v)) if math.isfinite(v) else STRENGTHEN_DEFAULT
    except (TypeError, ValueError):
        return STRENGTHEN_DEFAULT


def strengthen_pieces(spec, names):
    """Explicit piece attributes only; off pieces absent from this stage are ignored."""
    names = set(names)
    return {p['name']: piece_strengthen_factor(p) for p in spec.get('pieces', [])
            if p['name'] in names and p.get('strengthen', False)}


def piece_layer(pc):
    """The piece's layer (user 2026-10-25, DECISIONS on layers 2026-10-25): an integer, larger =
    further out, 0 when not set. While two pieces have different layers the simulation keeps the higher one outside
    the lower one; a person sets them back to 0 when the garment has settled (nothing turns them off by itself)."""
    try:
        return int(pc.get('layer', 0) or 0)
    except (TypeError, ValueError):
        return 0


def set_piece_state(pc, state):
    """Write one of PIECE_STATES as the three keys piece_state() reads."""
    if state not in PIECE_STATES:
        raise ValueError(f"piece state: {state!r} is none of {PIECE_STATES}")
    pc['enabled'] = state != 'off_all'
    pc['frozen'] = state in ('freeze', 'off_pattern')
    if state == 'off_pattern':
        pc['contact'] = False
    else:
        pc.pop('contact', None)


def piece_index(spec):
    return {p['name']: p for p in spec['pieces']}


def side_polyline(spec, side, outlines=None, step=1.0):
    pc = piece_index(spec)[side['piece']]
    if 'line' in side:
        P = next(np.asarray(x['pts'], float) for x in pc['internals'] if x['id'] == side['line'])
        cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
        a, b = side['from'] * cum[-1], side['to'] * cum[-1]
        s = np.linspace(a, b, max(2, int(abs(b - a) / step) + 1))
        return np.stack([np.interp(s, cum, P[:, 0]), np.interp(s, cum, P[:, 1])], 1)
    ol = outlines[side['piece']] if outlines else Outline(pc)
    return ol.side_polyline(side, step)


def side_length(spec, side, outlines=None):
    return polyline_len(side_polyline(spec, side, outlines))


def turn_default(spec, seam):
    """The piece of a seam that turns over when the seam is folded: the one sewn to other pieces along less length
    (a collar is sewn to its stand only; the stand to the bodice and the collar). Same rule as the stand in
    recipe (the neck piece sewn to the bodice along the most length)."""
    a, b = seam['a']['piece'], seam['b']['piece']
    tot = {a: 0.0, b: 0.0}
    for sm in spec.get('seams', []):
        if not sm.get('enabled', True) or sm['a']['piece'] == sm['b']['piece']:
            continue
        for k in ('a', 'b'):
            if sm[k]['piece'] in tot:
                tot[sm[k]['piece']] += side_length(spec, sm[k])
    return a if tot[a] <= tot[b] else b


def seam_run(spec, seam, tol_mm=1.0, max_deg=30.0):
    """The seam and the seams between the same two pieces that continue it end to end and in line on one of them
    (a yoke's seam cut in two at the centre back; on the back the two halves are 50 mm apart, a pleat between them),
    [ids], the seam first. A fold set on one half only is pulled flat
    by the other half (2026-10-23: the shirt's yoke at 30 deg ended at 2..12 deg, both halves folded at 34 deg).
    Seams meeting at a corner (a vent's top and the side seam) are not in it."""
    a, b = seam['a']['piece'], seam['b']['piece']
    if a == b:
        return [seam['id']]
    cand = [sm for sm in spec.get('seams', []) if sm['id'] != seam['id'] and sm.get('enabled', True)
            and {sm['a']['piece'], sm['b']['piece']} == {a, b} and sm['a']['piece'] != sm['b']['piece']]

    def ends(sm, piece):
        P = side_polyline(spec, sm['a'] if sm['a']['piece'] == piece else sm['b'])
        return [(P[0], P[0] - P[min(3, len(P) - 1)]), (P[-1], P[-1] - P[max(-4, -len(P))])]

    def joins(s1, s2, piece):
        for p1, d1 in ends(s1, piece):
            for p2, d2 in ends(s2, piece):
                if np.linalg.norm(p1 - p2) <= tol_mm:
                    c = -(d1 @ d2) / (np.linalg.norm(d1) * np.linalg.norm(d2) + 1e-12)   # the two run on: -d1 ~ d2
                    if c >= np.cos(np.radians(max_deg)):
                        return True
        return False
    run, todo = [seam], [seam]
    while todo:
        s = todo.pop()
        for sm in cand:
            if sm not in run and (joins(s, sm, a) or joins(s, sm, b)):
                run.append(sm); todo.append(sm)
    return [s['id'] for s in run]


def seam_folds(spec):
    """[(seam, deg, turn piece)] for the folds set on enabled seams, a piece sewn to itself too (a dart, a one-piece
    sleeve's underarm: user 2026-10-23, every seam alike; the turn piece is then the piece itself)."""
    by = {sm['id']: sm for sm in spec.get('seams', [])}
    out = []
    for f in spec.get('folds', []):
        sm = by.get(f.get('seam'))
        if sm is None or not sm.get('enabled', True) or not f.get('deg'):
            continue
        t = f.get('turn') if f.get('turn') in (sm['a']['piece'], sm['b']['piece']) else turn_default(spec, sm)
        out.append((sm, float(f['deg']), t))
    return out


def check(spec):
    """Problems a person has to look at, as short strings (empty = consistent)."""
    out = []
    pc = piece_index(spec)
    if len(pc) != len(spec['pieces']):
        out.append('piece names are not unique')
    for p in spec['pieces']:
        ids = {e['id'] for e in p['edges']}
        for n in p.get('notches') or []:
            if n.get('edge') not in ids:
                out.append(f"{p['name']}: notch {n.get('id')} on no edge {n.get('edge')}")
    for sm in spec['seams']:
        for k in ('a', 'b'):
            sd = sm[k]
            p = pc.get(sd['piece'])
            if p is None:
                out.append(f"{sm['id']}: no piece {sd['piece']}")
                continue
            if 'line' in sd:
                if not any(x['id'] == sd['line'] for x in p['internals']):
                    out.append(f"{sm['id']}: no internal line {sd['line']} on {sd['piece']}")
            else:
                ids = {e['id'] for e in p['edges']}
                for end in ('from', 'to'):
                    if sd[end][0] not in ids:
                        out.append(f"{sm['id']}: no edge {sd[end][0]} on {sd['piece']}")
    return out


MIRROR_GUIDE = {'arm_L': 'arm_R', 'arm_R': 'arm_L', 'leg_L': 'leg_R', 'leg_R': 'leg_L'}


def twins(spec, any_state=False):
    """[(primary, twin)]: a piece and its mirrored copy (the same outline mirrored in the layout, the mirrored flag on
    exactly one of them), both enabled. Linked copies that are not mirrored (a shirt's overlapping fronts) are not
    twins. spec['symmetry'] = 'off': none (the garment's halves are meshed, placed and simulated independently).
    any_state: Off (All) pieces too (the state buttons: switching a pair back on brings both, user 2026-10-24)."""
    if spec.get('symmetry') == 'off':
        return []
    fam = {}
    for pc in spec['pieces']:
        f = pc.get('bom', {}).get('family') or pc.get('family')      # family: a drawn piece's (core/draft.py)
        if (any_state or pc.get('enabled', True)) and f:
            fam.setdefault(f, []).append(pc)
    out = []
    for pcs in fam.values():
        if len(pcs) == 2 and bool(pcs[0].get('mirrored')) != bool(pcs[1].get('mirrored')):
            a, b = sorted(pcs, key=lambda p: bool(p.get('mirrored')))
            out.append((a['name'], b['name']))
    return out


def linked_twins(spec):
    """twins() whose placement is linked: not set apart by "unlinked" on either piece (Unlink, user 2026-10-10: a
    pair may be placed independently, a monster with its arms in different places; the pattern stays mirrored)."""
    pl = spec.get('placement', {})
    return [(a, b) for a, b in twins(spec) if not (pl.get(a, {}).get('unlinked') or pl.get(b, {}).get('unlinked'))]


def placement_pairs(spec):
    """Every pair placed as mirror images: the linked twins and spec['pairs'] (Pair: two pieces of about the same
    outline mirrored, drawn or cut separately)."""
    on = {pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)}
    out = list(linked_twins(spec))
    seen = {frozenset(p) for p in out}
    if spec.get('symmetry') != 'off':
        for a, b in spec.get('pairs', []):
            if a in on and b in on and a != b and frozenset((a, b)) not in seen:
                out.append((a, b))
                seen.add(frozenset((a, b)))
    return out


PLANES = ('x', 'y')    # a pair's mirror plane: 'x' left / right (x -> -x, the default), 'y' front / back (user 2026-11-11)


def plane_of(pc):
    """The plane a piece's pair mirrors in: 'y' when its 'mirror_plane' says front / back, else 'x' (left / right;
    a piece without the key, as every one before 2026-11-11). A pair's two pieces hold the same value."""
    return 'y' if (pc or {}).get('mirror_plane') == 'y' else 'x'


def pair_plane(spec, name):
    """plane_of the piece `name` (x when there is no such piece)."""
    return plane_of(next((pc for pc in spec.get('pieces', []) if pc['name'] == name), None))


def axis_of(plane):
    """The coordinate a plane turns over: 0 (x) for 'x', 1 (y) for 'y' -- in the layout and in the world alike."""
    return 1 if plane == 'y' else 0


MIRROR_AT = 'mirror_at'     # on both pieces of a pair: world mm of its mirror plane along the plane's axis


def mirror_center(spec, plane='x', name=None):
    """World mm of the mirror plane along its axis: where the pair of `name` has its plane (MIRROR_AT, set when a
    front / back pair laid flat is linked or copied, user 2026-11-12: a bag's back panel mirrors about the bag, not
    the body), else x of the torso guide's axis for left / right, its y for front / back (else 0)."""
    if name is not None:
        pc = next((p for p in spec.get('pieces', []) if p['name'] == name), None)
        if pc is not None and plane_of(pc) == plane and pc.get(MIRROR_AT) is not None and _pair_flat(spec, name):
            return float(pc[MIRROR_AT])
    t = spec.get('guides', {}).get('torso')
    return float(t['o'][axis_of(plane)]) if t else 0.0


def _pair_flat(spec, name):
    """The piece `name` laid flat, and its pair partner too where it has a placement of its own: a stored plane
    position holds only then (a pair placed on a guide again mirrors about the body, not where it was laid flat)."""
    pl = spec.get('placement', {})
    if not is_flat(pl.get(name)):
        return False
    pairs = list(twins(spec, any_state=True)) + [tuple(p) for p in spec.get('pairs', [])]
    for a, b in pairs:
        o = b if a == name else a if b == name else None
        if o is not None and o in pl and not is_flat(pl[o]):
            return False
    return True


def stored_mirror_at(spec, name):
    """The pair's own plane position (mm) when it has one, else None."""
    pc = next((p for p in spec.get('pieces', []) if p['name'] == name), None)
    return None if pc is None or pc.get(MIRROR_AT) is None else float(pc[MIRROR_AT])


def set_mirror_at(spec, names, value):
    """Write (None: remove) the plane position on the pieces `names` (a pair's two)."""
    for pc in spec.get('pieces', []):
        if pc['name'] in names:
            if value is None:
                pc.pop(MIRROR_AT, None)
            else:
                pc[MIRROR_AT] = round(float(value), 3)


def flat_center(p, ol):
    """World mm of the middle of a piece laid flat (its outline's box center carried by the flat frame)."""
    f = p['flat']
    o, R = np.asarray(f['o'], float), np.asarray(f['R'], float)
    d = 0.5 * (ol.B.min(0) + ol.B.max(0)) - np.asarray(p['anchor'], float)
    return o + d[0] * R[0] + d[1] * R[1]


def mirror_points(X, plane='x', c=0.0):
    """World points X (n x 3, any unit) mirrored in the plane through c (same unit) across axis_of(plane)."""
    X = np.array(X, float, copy=True)
    k = axis_of(plane)
    X[..., k] = 2.0 * c - X[..., k]
    return X


def mirror_diag(plane='x'):
    """The diagonal of the mirror (3 values): (-1, 1, 1) for 'x', (1, -1, 1) for 'y'."""
    d = [1.0, 1.0, 1.0]
    d[axis_of(plane)] = -1.0
    return d


def mirror_twins(spec, sources=None):
    """A twin goes where its pair's mirror image is (x -> -x): the same height up the mirrored guide, angle 180 - theta
    (every guide's frame is mirrored that way: torso, neck and legs on x = 0 with R0 = -x, the arms' frames mirror
    images), the anchor mirrored in the piece. sources: the pieces just placed (their twins follow them); otherwise
    the unmirrored copy of each pair leads. Placing a piece places its twin (user decision 2026-09-29: twins land
    on symmetric points without being asked). Returns (placement, report lines).

    Measured 2026-09-29 (placements read off the reference drape as test input, so each side came from an asymmetric
    drape before): jacket 13 pieces 587 -> 614 / 658 seams closed, 87 -> 49 vertices inside, p50 to the reference 6.5 -> 4.6 mm;
    shirt 744 / 797, 1 inside, 3.67 (approved 752, 0, 3.61); trousers 752-786 / 908 closed (830-836 without,
    look the same, the pockets now level), 0-1 inside, p50 3.75-4.10 (3.58-3.66)."""
    pl = {k: dict(v) for k, v in spec.get('placement', {}).items()}
    # outlines of the paired pieces only: every piece's was made on each call, 10 ms of a Place drag's 13.5 ms per
    # redraw on a 133-piece pattern (2026-11-03)
    pcs = {pc['name']: pc for pc in spec['pieces']}
    ol = {}

    def outline(n):
        if n not in ol:
            ol[n] = Outline(pcs[n])
        return ol[n]
    linked = {frozenset(p) for p in linked_twins(spec)}
    rep = []
    for a, b in placement_pairs(spec):
        if (sources and b in sources and a not in sources) or a not in pl:
            a, b = b, a
        if a not in pl:
            continue
        # a twin's hand move is the mirror image of its pair's (one object, a Mirror modifier); a Pair's other piece
        # keeps its own (user 2026-10-14 b-5: it follows only by Mirror Placement)
        pl[b] = mirrored_placement(pl[a], outline(a), outline(b),
                                   pose='mirror' if frozenset((a, b)) in linked else pl.get(b, {}).get('pose'),
                                   cx=mirror_center(spec, outline(a).plane, a))
        rep.append(f"  twin {b} <- mirror of {a}")
    return pl, rep


SIDE_KEYS = ('guide', 'theta', 'h', 'outside', 'flat')     # where a piece is placed (swap_sides exchanges these)


def swap_sides(spec, a, b):
    """The placement with pair (a, b)'s sides exchanged (Swap Sides, user 2026-10-26: a pocket landed on the side its
    partner should be on, and a pair's object picks both): each piece goes where the other was -- guide, angle,
    height -- its anchor mirrored in its own outline and its hand move mirrored, so a hand move stays with the place.
    (Taking each other's mirror image instead would change nothing: a twin is its pair's mirror image already. Fable
    review 2026-10-26.) The pair is still each other's mirror image, so mirror_twins does not undo it."""
    pl, _ = mirror_twins(spec)
    ol = {pc['name']: Outline(pc) for pc in spec['pieces'] if pc['name'] in (a, b)}

    def moved(x, y):
        p = {k: v for k, v in pl[x].items() if k not in SIDE_KEYS and k != 'pose'}
        p.update({k: pl[y][k] for k in SIDE_KEYS if k in pl[y]})
        c = 0.5 * (ol[x].B.min(0) + ol[x].B.max(0))
        d = np.asarray(pl[x]['anchor'], float) - c
        d[axis_of(ol[x].plane)] *= -1.0
        p['anchor'] = (c + d).tolist()
        q = mirror_pose(pl[x].get('pose'), ol[x].plane)
        if q:
            p['pose'] = q
        return p

    pl[a], pl[b] = moved(a, b), moved(b, a)
    return pl


def mirror_pose(pose, plane='x'):
    """The mirror image of a hand move: x -> -x (plane 'x'): loc x negated, the rotation S R S (S the mirror) the Euler
    XYZ angles (x, -y, -z); y -> -y (plane 'y', front / back): loc y negated, angles (-x, y, -z) (a turn about an axis
    in the mirror plane goes the other way, about the axis across it the same)."""
    if not pose:
        return None
    lx, ly, lz = pose['loc']
    rx, ry, rz = pose['rot']
    if plane == 'y':
        return {'loc': [lx, -ly, lz], 'rot': [-rx, ry, -rz]}
    return {'loc': [-lx, ly, lz], 'rot': [rx, -ry, -rz]}


FLAT = 'flat'      # placement 'guide' of a piece laid flat (user 2026-11-10): on a surface, the ground, in the air


def is_flat(p):
    """A placement laid flat: {'guide': FLAT, 'flat': {'o': [x, y, z] mm, 'R': [ex, ey, n]}, 'anchor': [x, y]} --
    the anchor at o, the pattern's +x along ex and +y along ey, its face along n. 'guide' keeps a value (FLAT, never a
    guide's name: Detect Guides makes none so called), so the code reading it finds no guide and no special case."""
    return bool(p) and p.get('guide') == FLAT and 'flat' in p


def center_x(spec):
    """The x of the mirror plane of a pair: the torso guide's axis, else 0."""
    t = spec.get('guides', {}).get('torso')
    return float(t['o'][0]) if t else 0.0


def mirror_flat(flat, cx=0.0, plane='x'):
    """A flat frame's mirror image in the plane x = cx ('x') or y = cx ('y'). The mirrored piece's pattern is mirrored
    across the same layout axis, so that axis of the frame turns over: for 'x' ex' = -S ex, ey' = S ey, for 'y' ex' =
    S ex, ey' = -S ey, and n' = S n (S the mirror): ex' x ey' = n' and the face stays the same side out."""
    o = list(map(float, flat['o']))
    ex, ey, n = (np.asarray(r, float) for r in flat['R'])
    S = np.array(mirror_diag(plane))
    o[axis_of(plane)] = 2.0 * cx - o[axis_of(plane)]
    R = (-S * ex, S * ey, S * n) if plane != 'y' else (S * ex, -S * ey, S * n)
    return {'o': [round(v, 3) for v in o], 'R': [[round(float(v), 6) + 0.0 for v in r] for r in R]}


def spun(pl, P):
    """Layout points P (mm) of a placed piece as its placement lays them: turned back about the anchor by the turn the
    piece got in the layout after it was placed (pl['spin'] degrees, core/draft.transform_piece), so turning a piece
    on the pattern does not turn it on the body (user 2026-11-11; Fable review: the layout is a work table). A
    placement without 'spin' (files before it): as before."""
    P = np.asarray(P, float)
    deg = float((pl or {}).get('spin', 0.0) or 0.0)
    if abs(deg) < 1e-9 or 'anchor' not in (pl or {}):
        return P
    a = np.radians(-deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    c = np.asarray(pl['anchor'], float)
    return (P.reshape(-1, 2) - c) @ R.T + c


def mirrored_placement(pa, ol_a, ol_b, pose='mirror', cx=0.0):
    """Piece b's placement as the mirror image of piece a's (pa), in the pair's plane (ol_a.plane; cx: where the plane
    is along its axis, mirror_center): left / right the mirrored guide, angle 180 - theta, the anchor mirrored in the
    piece; a flat one's frame mirrored in the plane. pose: 'mirror' = a's hand move mirrored, else b's
    own (None: none)."""
    plane = ol_a.plane
    ca = 0.5 * (ol_a.B.min(0) + ol_a.B.max(0))
    cb = 0.5 * (ol_b.B.min(0) + ol_b.B.max(0))
    d = np.asarray(pa['anchor'], float) - ca
    d[axis_of(plane)] *= -1.0
    if is_flat(pa):
        out = dict(pa, flat=mirror_flat(pa['flat'], cx, plane), anchor=(cb + d).tolist())
    elif plane == 'y':
        # front / back round a guide: the same guide, the angle turned over (the torso's, neck's and legs' frames
        # have R0 = -x, so the y mirror is theta -> -theta; an arm's frame is near enough, not exact: assumed)
        out = dict(pa, theta=-float(pa['theta']), anchor=(cb + d).tolist())
    else:
        out = dict(pa, guide=MIRROR_GUIDE.get(pa['guide'], pa['guide']), theta=180.0 - float(pa['theta']),
                   anchor=(cb + d).tolist())
    out.pop('pose', None)
    if out.get('spin'):                       # the mirror image turned the other way
        out['spin'] = -float(out['spin'])
    p = mirror_pose(pa.get('pose'), plane) if pose == 'mirror' else pose
    if p:
        out['pose'] = p
    return out
