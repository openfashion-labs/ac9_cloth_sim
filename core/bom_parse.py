# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a BOM.xml (Pacx v3): pieces, their outline / internal lines, and the seam line pairs.

Pure numpy + ElementTree (no bpy), so it runs in Blender and in a plain Python alike.

Coordinates are mm. A piece's own points are piece-local; the 2D layout position is
``layout = R @ local + T`` (the Instance). R is row-major and can include a reflection
(det < 0) and a scale (non-orthogonal): only this order reproduces the UV extent of scaled
instances.

Line types: 0 = straight segment, 3 = cubic Bezier (4 points), 2 = a curve through its points,
evaluated as a chordal Catmull-Rom (the best match to the file's own outline, mean RMS 0.02 mm).
"""
import xml.etree.ElementTree as ET

import numpy as np


# ---------------------------------------------------------------- XML

def _pts(el):
    pl = el.find('PointList')
    if pl is None:
        return np.zeros((0, 2))
    return np.array([[float(p.get('X')), float(p.get('Y'))] for p in pl.findall('Point')]).reshape(-1, 2)


def _rt(el):
    # Row-major: x' = R0 x + R1 y, y' = R2 x + R3 y
    R = np.array([[float(el.get('R0', 1)), float(el.get('R1', 0))],
                  [float(el.get('R2', 0)), float(el.get('R3', 1))]])
    T = np.array([float(el.get('X', 0)), float(el.get('Y', 0))])
    return R, T


def _lines(el, kind, shape_gid, piece_idx):
    out = []
    for ln in el.findall('Line'):
        out.append({'gid': int(ln.get('GID')), 'pgid': int(ln.get('PatternGID')), 'type': int(ln.get('Type')),
                    'idx': [int(p.get('Index')) for p in ln.findall('PointIndex')],
                    'shape': kind, 'shape_gid': shape_gid, 'piece': piece_idx})
    return out


def load_bom(path):
    """Parsed BOM as plain dicts. ``lines[gid]`` is a LIST: copies of a piece share line GIDs."""
    root = ET.parse(path).getroot()
    pieces, lines = [], {}
    for pi, pat in enumerate(root.findall('Pattern')):
        inst = pat.find('Instance')
        R, T = _rt(inst)
        b = pat.find('BoundaryShape')
        arr = pat.find('.//Arrangement')         # the arrangement point (where on the avatar it was placed)
        piece = {'idx': pi, 'name': inst.get('PatternName'), 'inst_gid': int(inst.get('GID')),
                 'arrangement': arr.get('Name', '') if arr is not None else '',
                 'R': R, 'T': T,
                 'boundary': {'points': _pts(b), 'lines': _lines(b, 'boundary', None, pi)},
                 'internals': [], 'baselines': []}
        for tag, key in (('InternalShape', 'internals'), ('BaseLine', 'baselines')):
            for s in pat.findall(tag):
                sR, sT = _rt(s)
                g = int(s.get('GID')) if s.get('GID') is not None else None
                kind = 'internal' if tag == 'InternalShape' else 'baseline'
                piece[key].append({'gid': g, 'hole': s.get('Hole') == 'true', 'R': sR, 'T': sT,
                                   'points': _pts(s), 'lines': _lines(s, kind, g, pi)})
        for L in piece['boundary']['lines'] + [l for s in piece['internals'] + piece['baselines']
                                               for l in s['lines']]:
            lines.setdefault(L['gid'], []).append(L)
        pieces.append(piece)

    # ColorWay 0 pattern list: same order as the Pattern elements; it de-duplicates repeated names
    # (PARTS1, PARTS1_1, ...), so its name is the unique one.
    cw = root.find('.//ColorWay')
    plist = cw.find('PatternList').findall('Pattern') if cw is not None else []
    for k, p in enumerate(pieces):
        c = plist[k] if k < len(plist) else None
        p['uname'] = c.get('Name') if c is not None else p['name']
        p['fabric'] = c.get('FabricName') if c is not None else ''

    seams = []
    for gi, g in enumerate(root.findall('SeamLinePairGroup')):      # a group has no GID: its order in the file
        for sp in g.findall('SeamLinePair'):
            sides = []
            for sl in sp.findall('SeamLine'):
                a, e = sl.find('SeamStartPoint'), sl.find('SeamEndPoint')
                sides.append({'line_start': int(a.get('RefLineGID')), 'r0': float(a.get('Ratio')),
                              'line_end': int(e.get('RefLineGID')), 'r1': float(e.get('Ratio')),
                              'reverse': sl.get('Reverse') == 'true',
                              'ref_pattern_gid': int(sl.get('RefPatternGID'))})
            seams.append({'gid': int(sp.get('GID')), 'fold': float(sp.get('FoldAngle', 0.0)), 'sides': sides,
                          'group': gi})     # M:N sewing: several pairs, one group
    return {'pieces': pieces, 'lines': lines, 'seams': seams}


# ---------------------------------------------------------------- curves

def _bezier(P, n):
    t = np.linspace(0, 1, n)[:, None]
    return ((1 - t) ** 3) * P[0] + 3 * ((1 - t) ** 2) * t * P[1] + 3 * (1 - t) * t * t * P[2] + t ** 3 * P[3]


def _catmull(P, n_per, alpha=1.0):
    # Catmull-Rom through all points, end tangents by reflection of the neighbor
    P = np.asarray(P, float)
    ext = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]

        def tj(ti, a, b):
            return ti + max(np.linalg.norm(b - a), 1e-9) ** alpha
        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        t = np.linspace(t1, t2, n_per, endpoint=(i == len(ext) - 3))[:, None]
        A1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
        A2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
        A3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
        B1 = (t2 - t) / (t2 - t0) * A1 + (t - t0) / (t2 - t0) * A2
        B2 = (t3 - t) / (t3 - t1) * A2 + (t - t1) / (t3 - t1) * A3
        out.append((t2 - t) / (t2 - t1) * B1 + (t - t1) / (t2 - t1) * B2)
    return np.vstack(out)


def eval_line(line, pts, n=64):
    """A BOM line as a polyline, in the coordinates of ``pts`` (its own shape)."""
    P = pts[line['idx']]
    if line['type'] == 0 or len(P) == 2:
        return P.copy()
    if line['type'] == 3:
        return _bezier(P, n)
    return _catmull(P, max(4, n // (len(P) - 1)))


def to_layout(piece, q):
    return q @ piece['R'].T + piece['T']


def shape_to_piece(shape, q):
    """InternalShape / BaseLine local -> piece local."""
    return q @ shape['R'].T + shape['T']


def polygon_area(q):
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum())


def owners(bom, side):
    """Piece indices that carry a seam side's line (copies of a piece share line GIDs)."""
    ls = bom['lines'].get(side['line_start'], [])
    return sorted(set(l['piece'] for l in ([l for l in ls if l['pgid'] == side['ref_pattern_gid']] or ls)))
