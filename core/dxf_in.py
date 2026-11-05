# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a DXF-AAMA / ASTM pattern into a new spec (no bpy): the way in for a pattern made in a 2D pattern CAD
(user 2026-10-21; VISION_gap P1).

What is read, by the AAMA layers (as dxf_out.py writes them, and as measured in another tool's export,
2609_Apparel_CAD/FINDINGS_dxf_2026-09-23.md):
  layer 1   the outline (closed POLYLINE / LWPOLYLINE) and the notes (PIECE NAME, SIZE, "AC9 PIECE", "# AC9 <edge>")
  layer 84  the outline with its curve points (preferred to layer 1 when there: layer 1 may hold the corners only)
  layer 2   turn points: the corners, where one edge ends and the next begins
  layer 4   notches (POINT; "# AC9 <id> <kind>" by each, from dxf_out)
  layer 8   internal lines (open or closed), "# AC9 <id>" at the first point
  layer 15  the piece name (when no PIECE NAME note)
Not read yet (counted in the report): grain lines (layer 7, the spec keeps no grain), internal cut-outs / darts
(layer 11), seam allowance lines (layer 14). Seams are not in DXF; a garment read here has none.

A piece is a BLOCK placed by an INSERT (its move, scale and turn applied), or, in a file without blocks, each
closed outline on layer 1 with what lies inside it. Graded files: only the sample size's pieces ("SAMPLE SIZE:").
Text is read line by line as UTF-8, else cp932 (both are found in one file, FINDINGS §1.1). Units: $INSUNITS or
"UNITS: ENGLISH"; mm by default.
"""
import math
import os
from ._i18n import rpt

import numpy as np

from . import spec as S

TOL = 0.5          # mm: a turn point or an AC9 mark this close to an outline point is that point
TURN_DEG = 25.0    # no turn points in the file: an outline point turning more than this is a corner


# ---------------------------------------------------------------- reading the file

def _lines(path):
    raw = open(path, 'rb').read().splitlines()
    out = []
    for b in raw:
        for enc in ('utf-8', 'cp932', 'latin-1'):
            try:
                out.append(b.decode(enc).strip())
                break
            except UnicodeDecodeError:
                continue
    return out


def _pairs(path):
    L = _lines(path)
    out = []
    for i in range(0, len(L) - 1, 2):
        try:
            out.append((int(L[i]), L[i + 1]))
        except ValueError:
            continue
    return out


def _entities(pairs):
    """[(section, block name or None, type, [(code, value), ...])] in file order, with the header's variables."""
    ents, header = [], {}
    sec, blk, cur, var = None, None, None, None
    for code, val in pairs:
        if code == 0:
            if cur is not None:
                ents.append((sec, blk, cur[0], cur[1]))
                cur = None
            if val == 'SECTION':
                sec = 'pending'
            elif val == 'ENDSEC':
                sec, blk = None, None
            elif val == 'BLOCK':
                cur = ('BLOCK', [])
            elif val == 'ENDBLK':
                blk = None
            elif val == 'EOF':
                break
            else:
                cur = (val, [])
            continue
        if sec == 'pending' and code == 2:
            sec = val
            continue
        if sec == 'HEADER':
            if code == 9:
                var = val
            elif var is not None:
                header.setdefault(var, val)
            continue
        if cur is not None:
            cur[1].append((code, val))
            if cur[0] == 'BLOCK' and code == 2:
                blk = val
    if cur is not None:
        ents.append((sec, blk, cur[0], cur[1]))
    return ents, header


def _get(d, code, default=None, cast=str):
    for c, v in d:
        if c == code:
            try:
                return cast(v)
            except ValueError:
                return default
    return default


def _xy_all(d):
    xs = [float(v) for c, v in d if c == 10]
    ys = [float(v) for c, v in d if c == 20]
    return list(zip(xs, ys))


def _shapes(ents):
    """{block (None: ENTITIES): [shape]}, a shape {'kind': poly | line | point | text | insert, 'layer', ...}."""
    out = {}
    poly = None
    for sec, blk, t, d in ents:
        if sec not in ('BLOCKS', 'ENTITIES') or t == 'BLOCK':
            continue
        key = blk if sec == 'BLOCKS' else None
        lst = out.setdefault(key, [])
        lay = _get(d, 8, '0')
        if t == 'POLYLINE':
            poly = {'kind': 'poly', 'layer': lay, 'closed': bool(_get(d, 70, 0, int) & 1), 'pts': []}
            lst.append(poly)
        elif t == 'VERTEX' and poly is not None:
            poly['pts'].append((_get(d, 10, 0.0, float), _get(d, 20, 0.0, float)))
        elif t == 'SEQEND':
            poly = None
        elif t == 'LWPOLYLINE':
            lst.append({'kind': 'poly', 'layer': lay, 'closed': bool(_get(d, 70, 0, int) & 1), 'pts': _xy_all(d)})
        elif t == 'LINE':
            lst.append({'kind': 'poly', 'layer': lay, 'closed': False,
                        'pts': [(_get(d, 10, 0.0, float), _get(d, 20, 0.0, float)),
                                (_get(d, 11, 0.0, float), _get(d, 21, 0.0, float))]})
        elif t == 'POINT':
            lst.append({'kind': 'point', 'layer': lay, 'xy': (_get(d, 10, 0.0, float), _get(d, 20, 0.0, float))})
        elif t in ('TEXT', 'MTEXT'):
            lst.append({'kind': 'text', 'layer': lay, 'xy': (_get(d, 10, 0.0, float), _get(d, 20, 0.0, float)),
                        'text': _get(d, 1, '')})
        elif t == 'INSERT':
            lst.append({'kind': 'insert', 'layer': lay, 'name': _get(d, 2, ''),
                        'xy': (_get(d, 10, 0.0, float), _get(d, 20, 0.0, float)),
                        'scale': (_get(d, 41, 1.0, float), _get(d, 42, 1.0, float)),
                        'rot': _get(d, 50, 0.0, float)})
    return out


def _placed(shapes, ins):
    """The block's shapes moved as the INSERT places them (scale, then turn, then move)."""
    sx, sy = ins['scale']
    c, s = math.cos(math.radians(ins['rot'])), math.sin(math.radians(ins['rot']))
    ox, oy = ins['xy']

    def f(p):
        x, y = p[0] * sx, p[1] * sy
        return (ox + c * x - s * y, oy + s * x + c * y)
    out = []
    for sh in shapes:
        sh = dict(sh)
        if 'pts' in sh:
            sh['pts'] = [f(p) for p in sh['pts']]
        if 'xy' in sh:
            sh['xy'] = f(sh['xy'])
        out.append(sh)
    return out


def _inside(P, q):
    x, y = q
    n, hit = len(P), False
    for i in range(n):
        (x1, y1), (x2, y2) = P[i], P[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


# ---------------------------------------------------------------- one piece

def _clean(P, closed):
    P = np.asarray(P, float).reshape(-1, 2)
    if len(P) == 0:
        return P
    keep = [0] + [i for i in range(1, len(P)) if np.linalg.norm(P[i] - P[i - 1]) > 1e-6]
    P = P[keep]
    if closed and len(P) > 1 and np.linalg.norm(P[0] - P[-1]) <= 1e-6:
        P = P[:-1]
    return P


def _outline(shapes):
    """The piece's outline points (closed, without the repeated first point): layer 84 if there, else layer 1; the
    largest closed one."""
    for layer in ('84', '1'):
        cand = [_clean(sh['pts'], True) for sh in shapes if sh['kind'] == 'poly' and sh['layer'] == layer
                and sh['closed'] and len(sh['pts']) >= 3]
        cand = [P for P in cand if len(P) >= 3]
        if cand:
            return max(cand, key=lambda P: abs(S_area(P)))
    return None


def S_area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _snap_in(B, q):
    """Index of outline point q (within TOL), inserting it on the nearest segment if it is not one. B is a list."""
    A = np.asarray(B)
    d = np.linalg.norm(A - q, axis=1)
    k = int(d.argmin())
    if d[k] <= TOL:
        return k, B
    n = len(A)
    best = None
    for i in range(n):
        a, b = A[i], A[(i + 1) % n]
        ab = b - a
        t = float(np.clip(np.dot(q - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1))
        dd = float(np.linalg.norm(a + t * ab - q))
        if best is None or dd < best[0]:
            best = (dd, i, a + t * ab)
    if best[0] > 5 * TOL:
        return None, B
    B = list(B)
    B.insert(best[1] + 1, tuple(best[2]))
    return best[1] + 1, B


def _turning(B):
    A = np.asarray(B)
    u = A - np.roll(A, 1, 0)
    v = np.roll(A, -1, 0) - A
    nu, nv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
    c = (u * v).sum(1) / np.maximum(nu * nv, 1e-12)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def _ac9_marks(shapes, layer):
    return [(np.asarray(sh['xy']), sh['text'][6:].strip()) for sh in shapes
            if sh['kind'] == 'text' and sh['layer'] == layer and sh['text'].startswith('# AC9 ')]


def _piece(shapes, name, rep):
    B = _outline(shapes)
    if B is None:
        return None
    if S_area(B) < 0:
        B = B[::-1]
    B = [tuple(p) for p in B]
    # the corners: the turn points (layer 2) on the outline, else the points that turn sharply
    turns = [np.asarray(sh['xy']) for sh in shapes if sh['kind'] == 'point' and sh['layer'] == '2']
    ks = set()
    for q in turns:
        k, B2 = _snap_in(B, q)
        if k is None:
            continue
        if len(B2) != len(B):                        # a point put in: the indices after it move on by one
            ks = {i + 1 if i >= k else i for i in ks}
            B = B2
        ks.add(k)
    if len(ks) < 3:
        ang = _turning(B)
        ks |= {int(i) for i in np.nonzero(ang >= TURN_DEG)[0]}
        if len(ks) < 3:
            ks |= {int(i) for i in np.argsort(-ang)[:3]}
        rep['corners_by_angle'] += 1
    ks = sorted(ks)
    A = np.asarray(B, float)
    # the edges' ids: an AC9 mark at a corner names the edge that starts there (a piece written by dxf_out)
    ids = {}
    for q, eid in _ac9_marks(shapes, '1'):
        d = np.linalg.norm(A[ks] - q, axis=1)
        j = int(d.argmin())
        if d[j] <= TOL:
            ids[ks[j]] = eid
    if len(set(ids.values())) != len(ids) or (ids and len(ids) != len(ks)):
        ids = {}
    edges = []
    n = len(A)
    for i, k0 in enumerate(ks):
        k1 = ks[(i + 1) % len(ks)]
        idx = list(range(k0, k1 + 1)) if k1 > k0 else list(range(k0, n)) + list(range(0, k1 + 1))
        edges.append({'id': ids.get(k0, f"E{i + 1}"), 'pts': _r(A[idx])})
    pc = {'name': name, 'edges': edges, 'enabled': True, 'internals': []}
    # internal lines
    marks = _ac9_marks(shapes, '8')
    used = set()
    for sh in shapes:
        if sh['kind'] != 'poly' or sh['layer'] != '8':
            continue
        P = _clean(sh['pts'], False)
        if len(P) < 2 or S.polyline_len(P) < TOL:
            continue
        shut = sh['closed'] or (len(P) >= 4 and np.linalg.norm(P[0] - P[-1]) <= 1e-6)   # dxf_out repeats the start
        if shut and np.linalg.norm(P[0] - P[-1]) <= 1e-6:
            P = P[:-1]
        lid = next((m for q, m in marks if np.linalg.norm(P[0] - q) <= TOL and m not in used), None)
        if lid is None:
            k = len(pc['internals']) + 1
            while f"I{k}" in used:
                k += 1
            lid = f"I{k}"
        used.add(lid)
        it = {'id': lid, 'pts': _r(P), 'kind': 'line'}
        if shut and len(P) >= 3:
            it.update(pts=_r(np.vstack([P, P[:1]])), kind='shape', closed=True)
        pc['internals'].append(it)
    if not pc['internals']:
        pc.pop('internals')
    # notches
    nts = [np.asarray(sh['xy']) for sh in shapes if sh['kind'] == 'point' and sh['layer'] == '4']
    if nts:
        ol = S.Outline(pc)
        Bc = np.vstack([ol.B, ol.B[:1]])
        out = []
        nmarks = _ac9_marks(shapes, '4')
        for q in nts:
            seg_a, seg_d = Bc[:-1], np.diff(Bc, axis=0)
            ll = (seg_d * seg_d).sum(1)
            t = np.clip(((q - seg_a) * seg_d).sum(1) / np.maximum(ll, 1e-12), 0, 1)
            d = np.linalg.norm(seg_a + t[:, None] * seg_d - q, axis=1)
            j = int(d.argmin())
            if d[j] > 5 * TOL:
                continue
            eid, tt = ol.edge_t(ol.cum[j] + t[j] * np.sqrt(ll[j]))
            mk = next((m.split() for p, m in nmarks if np.linalg.norm(p - q) <= TOL), None)
            nid = mk[0] if mk and mk[0] not in {o['id'] for o in out} else f"N{len(out) + 1}"
            out.append({'id': nid, 'edge': eid, 't': round(float(tt), 6),
                        'kind': mk[1] if mk and len(mk) > 1 else 'slit'})
        if out:
            pc['notches'] = out
    for layer, key in (('7', 'grain_lines'), ('11', 'cutouts')):
        rep[key] += sum(1 for sh in shapes if sh['kind'] == 'poly' and sh['layer'] == layer)
    return pc


def _r(P):
    return [[round(float(x), 4), round(float(y), 4)] for x, y in np.asarray(P, float)]


def _note(shapes, prefix):
    for sh in shapes:
        if sh['kind'] == 'text' and sh['text'].upper().startswith(prefix):
            return sh['text'][len(prefix):].strip()
    return None


def _name(shapes, block):
    nm = _note(shapes, 'AC9 PIECE:')
    if nm and ' #' in nm:
        nm = nm.rsplit(' #', 1)[0]
    nm = nm or _note(shapes, 'PIECE NAME:')
    if not nm:
        nm = next((sh['text'].strip() for sh in shapes if sh['kind'] == 'text' and sh['layer'] == '15'
                   and sh['text'].strip()), None)
    if not nm and block:
        nm = block[:-7] if block.endswith('_Master') else block
    return nm or "Piece"


# ---------------------------------------------------------------- the file

def read(path):
    """(spec, report) for the DXF at path. Raises ValueError when no piece outline is found."""
    ents, header = _entities(_pairs(path))
    shp = _shapes(ents)
    top = shp.get(None, [])
    groups = []                                       # (shapes, block name)
    inserts = [sh for sh in top if sh['kind'] == 'insert']
    for ins in inserts:
        b = shp.get(ins['name'])
        if b and not ins['name'].startswith('*'):
            groups.append((_placed(b, ins), ins['name']))
    placed = {ins['name'] for ins in inserts}
    for b, lst in shp.items():                       # blocks never inserted that hold an outline
        if b is not None and b not in placed and not b.startswith('*') and _outline(lst) is not None:
            groups.append((lst, b))
    if not groups:                                    # no blocks: each closed layer-1 outline and what is in it
        outl = [sh for sh in top if sh['kind'] == 'poly' and sh['layer'] in ('1', '84') and sh['closed']]
        for o in [o for o in outl if o['layer'] == '1'] or outl:
            P = _clean(o['pts'], True)
            if len(P) < 3:
                continue
            mine = [o] + [sh for sh in top if sh is not o and sh['kind'] != 'insert'
                          and all(_inside(P, q) or np.min(np.linalg.norm(P - np.asarray(q), axis=1)) <= TOL
                                  for q in (sh['pts'] if 'pts' in sh else [sh['xy']]))]
            groups.append((mine, None))
    # units
    u = str(header.get('$INSUNITS', '')).strip()
    scale = {'1': 25.4, '4': 1.0, '5': 10.0, '6': 1000.0}.get(u, 1.0)
    notes = [sh['text'].upper() for sh in top if sh['kind'] == 'text']
    if any(t.startswith('UNITS:') and 'ENGLISH' in t for t in notes):
        scale = 25.4
    # a graded file: the sample size only
    sample = next((sh['text'].split(':', 1)[1].strip() for sh in top
                   if sh['kind'] == 'text' and sh['text'].upper().startswith('SAMPLE SIZE:')), None)
    rep = {'pieces': 0, 'corners': 0, 'internals': 0, 'notches': 0, 'corners_by_angle': 0, 'grain_lines': 0,
           'cutouts': 0, 'skipped_sizes': 0, 'no_outline': 0, 'units_mm_per': scale}
    name = _note(top, 'AC9 SPEC:') or os.path.splitext(os.path.basename(path))[0]
    spec = S.new_spec(name, source={'kind': 'dxf', 'path': path})
    taken = set()
    for shapes, block in groups:
        size = _note(shapes, 'SIZE:')
        if sample and size and size != sample:
            rep['skipped_sizes'] += 1
            continue
        if scale != 1.0:
            shapes = _placed(shapes, {'scale': (scale, scale), 'rot': 0.0, 'xy': (0.0, 0.0)})
        nm = _name(shapes, block)
        base, k = nm, 2
        while nm in taken:
            nm = f"{base}_{k}"
            k += 1
        pc = _piece(shapes, nm, rep)
        if pc is None:
            rep['no_outline'] += 1
            continue
        taken.add(nm)
        spec['pieces'].append(pc)
        rep['pieces'] += 1
        rep['corners'] += len(pc['edges'])
        rep['internals'] += len(pc.get('internals', []))
        rep['notches'] += len(pc.get('notches', []))
    if not spec['pieces']:
        raise ValueError(rpt("no piece outline found (a closed polyline on layer 1 or 84)"))
    return spec, rep
