# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the spec's pattern as DXF-AAMA (no bpy): what 2D pattern CADs read.

Laid out as a 3D garment tool's export (measured, 2609_Apparel_CAD/FINDINGS_dxf_2026-09-23.md, and the ASTM D6673 layers): R12, one
BLOCK "<piece>_Master" per piece and an INSERT of each at the origin; in a block:
  layer 1  the outline (closed POLYLINE) and the piece's notes (PIECE NAME / SIZE / QUANTITY)
  layer 2  turn points (the corners: where one AC9 edge ends and the next begins)
  layer 3  curve points (the outline's other points)
  layer 4  notches (POINT, "# AC9 <id> <kind>" by each)
  layer 7  grain line (a 180 mm LINE; AC9 keeps no grain, so the layout's y)
  layer 8  internal lines
  layer 14 sew line (= the outline: AC9 patterns have no seam allowance)
  layer 15 the piece name
Round-trip marks (so that a piece coming back from the 2D CAD can be matched to its seams again, DECISIONS
cloth-sim-pattern-scope-roundtrip-2026-10-03): on layer 1, "AC9 PIECE: <name>" with the piece's index, and at every
turn point "# AC9 <edge id>" (the export measured puts its own point ids there the same way, "# 1798"); after the blocks "AC9 SPEC:
<garment name>". Seams are not written: DXF-AAMA has no place for them; they stay in the spec.
Units mm. Text is UTF-8 (the export measured had block names UTF-8 and texts cp932; which one a given CAD reads is not checked).
"""
import numpy as np

from . import spec as S


def _f(x):
    return f"{float(x):.6f}"


class _W:
    def __init__(self):
        self.out = []

    def g(self, code, value):
        self.out.append(f"{code:>3}")
        self.out.append(str(value))

    def text(self, layer, x, y, s, h=0.25, ang=0.0):
        self.g(0, 'TEXT'); self.g(8, layer); self.g(10, _f(x)); self.g(20, _f(y)); self.g(40, _f(h))
        self.g(50, _f(ang)); self.g(1, s); self.g(7, 'STANDARD')

    def point(self, layer, x, y):
        self.g(0, 'POINT'); self.g(8, layer); self.g(10, _f(x)); self.g(20, _f(y))

    def polyline(self, layer, P, closed):
        self.g(0, 'POLYLINE'); self.g(8, layer); self.g(66, 1); self.g(70, 1 if closed else 0)
        for x, y in P:
            self.g(0, 'VERTEX'); self.g(8, layer); self.g(10, _f(x)); self.g(20, _f(y))
        self.g(0, 'SEQEND')

    def line(self, layer, a, b):
        self.g(0, 'LINE'); self.g(8, layer); self.g(10, _f(a[0])); self.g(20, _f(a[1])); self.g(11, _f(b[0]))
        self.g(21, _f(b[1]))


def block_name(name):
    return f"{name}_Master"


def write(spec, path, pieces=None):
    """Write the enabled pieces (or the named ones) to path. Returns a summary dict."""
    pcs = [p for p in spec['pieces'] if (pieces is None and p.get('enabled', True)) or (pieces and p['name'] in pieces)]
    w = _W()
    w.g(0, 'SECTION'); w.g(2, 'HEADER'); w.g(9, '$ACADVER'); w.g(1, 'AC1009'); w.g(0, 'ENDSEC')
    w.g(0, 'SECTION'); w.g(2, 'BLOCKS')
    summary = {'pieces': 0, 'turn_points': 0, 'internal_lines': 0}
    for idx, pc in enumerate(pcs):
        ol = S.Outline(pc)
        B = ol.B
        w.g(0, 'BLOCK'); w.g(8, 1); w.g(2, block_name(pc['name'])); w.g(70, 64); w.g(10, 0); w.g(20, 0)
        w.polyline(1, B, True)
        corners = [np.asarray(e['pts'][0], float) for e in pc['edges']]
        for e, c in zip(pc['edges'], corners):
            w.text(1, c[0], c[1], f"# AC9 {e['id']}")
        for c in corners:
            w.point(2, c[0], c[1])
        cset = {(round(float(c[0]), 6), round(float(c[1]), 6)) for c in corners}
        for x, y in B:
            if (round(float(x), 6), round(float(y), 6)) not in cset:
                w.point(3, x, y)
        for n in pc.get('notches') or []:                # notches, with their id and kind to read back (dxf_in.py)
            if n['edge'] in ol.edges:
                p = ol.at(ol.s_of(n['edge'], float(n['t'])))
                w.point(4, p[0], p[1])
                w.text(4, p[0], p[1], f"# AC9 {n['id']} {n.get('kind', 'slit')}")
        lo, hi = B.min(0), B.max(0)
        cx, cy = 0.5 * (lo + hi)
        w.line(7, (cx, cy - 90.0), (cx, cy + 90.0))
        for it in pc.get('internals', []):
            if it.get('kind') == 'baseline' or len(it['pts']) < 2:
                continue
            w.polyline(8, it['pts'], False)
            w.text(8, it['pts'][0][0], it['pts'][0][1], f"# AC9 {it['id']}")
            summary['internal_lines'] += 1
        w.polyline(14, B, True)
        w.text(15, cx, cy, pc['name'], h=8.0)
        w.text(1, cx, cy + 5.0, f"PIECE NAME: {pc['name']}")
        w.text(1, cx, cy, "SIZE: Master")
        w.text(1, cx, cy - 10.0, "QUANTITY: 1")
        w.text(1, cx, cy - 15.0, f"AC9 PIECE: {pc['name']} #{idx}")
        w.g(0, 'ENDBLK'); w.g(8, 1)
        summary['pieces'] += 1
        summary['turn_points'] += len(corners)
    w.g(0, 'ENDSEC')
    w.g(0, 'SECTION'); w.g(2, 'ENTITIES')
    for pc in pcs:
        w.g(0, 'INSERT'); w.g(8, 1); w.g(2, block_name(pc['name'])); w.g(10, _f(0)); w.g(20, _f(0))
    for s in ("SAMPLE SIZE: Master", "UNITS: METRIC", f"AC9 SPEC: {spec.get('name', '')}"):
        w.text(1, 0, 0, s)
    w.g(0, 'ENDSEC'); w.g(0, 'EOF')
    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(w.out) + '\n')
    return summary


def read_back(path):
    """Minimal reader for checking a written file: {block name: {'outline': (n, 2), 'corners': [(x, y, edge id)],
    'internals': [(n, 2)], 'notes': [str]}}."""
    L = [x.strip() for x in open(path, encoding='utf-8').read().splitlines()]
    ents = []                                        # (type, {code: value}) in file order
    for c, v in zip(L[0::2], L[1::2]):
        if c == '0':
            ents.append((v, {}))
        elif ents:
            ents[-1][1][c] = v
    blocks, cur, poly = {}, None, None
    for t, d in ents:
        if t == 'BLOCK':
            cur = blocks[d['2']] = {'outline': None, 'corners': [], 'internals': [], 'notes': []}
        elif t == 'ENDBLK':
            cur = None
        elif cur is None:
            continue
        elif t == 'POLYLINE':
            poly = (d.get('8'), [])
        elif t == 'VERTEX' and poly is not None:
            poly[1].append((float(d['10']), float(d['20'])))
        elif t == 'SEQEND' and poly is not None:
            P = np.array(poly[1], float)
            if poly[0] == '1':
                cur['outline'] = P
            elif poly[0] == '8':
                cur['internals'].append(P)
            poly = None
        elif t == 'TEXT' and d.get('8') == '1':
            if d.get('1', '').startswith('# AC9 '):
                cur['corners'].append((float(d['10']), float(d['20']), d['1'][6:]))
            else:
                cur['notes'].append(d.get('1', ''))
    return blocks
