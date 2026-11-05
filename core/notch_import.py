# SPDX-License-Identifier: GPL-3.0-or-later
"""Notches from a BOM.xml onto a spec made from it (no bpy).

A BOM notch is Pattern/BoundaryShape/Notch {RefLineGID, X, Y, Alpha}: the outline line it is on (the GID is looked
up within its own Pattern, line GIDs repeat across pieces), Alpha the fraction along that line, X, Y the point in
the 2D layout. The line + fraction go to the spec's (edge id, t) the way a seam's ends do (bom_to_spec: the edge
`L<gid>`, the fraction through the line's ratio map, flipped when the line runs against the outline); X, Y checks it.
A notch on an internal line is skipped (notches are outline marks here)."""
import xml.etree.ElementTree as ET
from ._i18n import rpt

import numpy as np

from . import bom_parse as B
from . import bom_to_spec as BS
from . import spec as S

CHECK_MM = 1.0        # the point from line + fraction must be this close to the BOM's X, Y, else the nearest
                      # outline point to X, Y is used (and counted)
SAME_MM = 0.5         # a notch already this close is not added again


def _nearest(ol, q):
    Bc = np.vstack([ol.B, ol.B[:1]])
    A, Dd = Bc[:-1], np.diff(Bc, axis=0)
    ll = (Dd * Dd).sum(1)
    t = np.clip(((q - A) * Dd).sum(1) / np.maximum(ll, 1e-12), 0, 1)
    d = np.linalg.norm(A + t[:, None] * Dd - q, axis=1)
    j = int(d.argmin())
    return ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j])), float(d[j])


def bom_notches(path):
    """{pattern index: [(line gid, alpha, (x, y))]} read from the BOM."""
    root = ET.parse(path).getroot()
    out = {}
    for pi, pat in enumerate(root.findall('Pattern')):
        b = pat.find('BoundaryShape')
        if b is None:
            continue
        for n in b.findall('Notch'):
            out.setdefault(pi, []).append((int(n.get('RefLineGID')), float(n.get('Alpha', 0)),
                                           (float(n.get('X', 0)), float(n.get('Y', 0)))))
    return out


def import_notches(spec, path=None, names=None):
    """Add the BOM's notches to the pieces of spec (all, or `names`). Returns a report dict: added, already there,
    on internal lines (skipped), placed by X, Y because line + fraction missed it, the largest distance between a
    placed notch and the BOM's X, Y (mm)."""
    path = path or spec.get('source', {}).get('path')
    if not path:
        raise ValueError(rpt("the pattern was not read from a BOM"))
    bom = B.load_bom(path)
    nts = bom_notches(path)
    rep = {'added': 0, 'already': 0, 'internal': 0, 'by_position': 0, 'gone_edge': 0, 'max_mm': 0.0, 'pieces': 0}
    for pc in spec['pieces']:
        if names is not None and pc['name'] not in names:
            continue
        pi = (pc.get('bom') or {}).get('idx')
        if pi is None or pi not in nts:
            continue
        _, flip, _ = BS._piece_outline(bom['pieces'][pi])
        ol = S.Outline(pc)
        have = [ol.at(ol.s_of(n['edge'], float(n['t']))) for n in pc.get('notches') or [] if n['edge'] in ol.edges]
        added = 0
        for gid, alpha, xy in nts[pi]:
            if gid not in flip:
                rep['internal'] += 1
                continue
            eid, flipped, rmap = flip[gid]
            q = np.asarray(xy, float)
            if eid in ol.edges:
                t = BS._to_layout_ratio(rmap, alpha)
                t = 1.0 - t if flipped else t
                p = ol.at(ol.s_of(eid, t))
                d = float(np.linalg.norm(p - q))
            else:                                    # the edge was split or joined since: go by position
                rep['gone_edge'] += 1
                d = np.inf
            if d > CHECK_MM:
                (eid, t), d = _nearest(ol, q)
                rep['by_position'] += 1
                p = ol.at(ol.s_of(eid, t))
            if any(float(np.linalg.norm(p - h)) < SAME_MM for h in have):
                rep['already'] += 1
                continue
            from . import draft as D
            D.add_notch(spec, pc['name'], eid, t, twins=False)
            have.append(p)
            added += 1
            rep['max_mm'] = max(rep['max_mm'], round(d, 4))
        rep['added'] += added
        rep['pieces'] += bool(added)
    return rep
