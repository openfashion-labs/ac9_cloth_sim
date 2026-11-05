# SPDX-License-Identifier: GPL-3.0-or-later
"""Read an SVG pattern into a new spec (no bpy): the way in for a pattern drawn in a vector drawing app or written
by a pattern generator (user 2026-10-31). The same shape as dxf_in.py: outlines, corners, internal lines, notches
and piece names; SVG has no seams, a garment read here has none.

The pieces are found by the drawing, not by its layers (a layer may hold one piece, several, or one piece's lines
split over many): every closed line not inside another is a piece, with what lies inside it.
  seam allowance  a piece drawn twice, the cut line round the sewing line (an offset of it, most of the way at
                  2..40 mm): the inner line is the outline, the outer one is left out (counted). A piece drawn with
                  one closed line is taken as its sewing line.
  internal lines  open lines inside the outline; closed ones inside it are closed internal shapes
  notches         a short line (<= NOTCH_MM) that touches the outline or the cut line, a small closed mark
                  (< MARK_AREA) on the outline, or a placed symbol whose id names a notch
  not read        lines with arrow heads (grain lines, fold marks; the spec keeps no grain), other placed symbols
                  and small marks inside the piece (buttons, holes), lines of the "help" class (construction lines
                  some generators write), lines outside every piece. Text names a piece only.
  the name        the nearest group or layer around the outline that holds no other piece (its label, data-name
                  or id), else the first text inside the outline, else "Piece n".
  corners         where two segments of the path meet at an angle >= TURN_DEG (a curve's flattened points are
                  never corners); fewer than 3: the sharpest turns, as in dxf_in.
Units: width / height with a unit (mm, cm, in, pt, pc) over the viewBox give the size. In px or with no unit the
size of a user unit is not in the file: a drawing app that works in points writes 1 unit = 1 pt (known by its
generator note or its data-name attributes), others 1 px = 1/96 in; `units` overrides this guess.
Curves (cubic, quadratic, arcs) are flattened to within FLAT_MM; y is turned up (SVG's y runs down).
"""
import math
import os
import re
import xml.etree.ElementTree as ET
from ._i18n import rpt

import numpy as np

from . import spec as S

TOL = 0.5          # mm: a point this close to a line is on it
TURN_DEG = 25.0    # a segment joint turning more than this is a corner
FLAT_MM = 0.05     # a flattened curve stays this close to the true one
MIN_AREA = 400.0   # mm²: a closed line smaller than this (2 x 2 cm) is a mark, not a piece
MARK_AREA = 400.0
NOTCH_MM = 15.0    # an open line up to this long touching the outline is a notch
SA_MIN, SA_MAX = 2.0, 40.0   # mm: the median width of a seam allowance

UNIT_MM = {'mm': 1.0, 'cm': 10.0, 'in': 25.4, 'pt': 25.4 / 72, 'pc': 25.4 / 6, 'px': 25.4 / 96, 'q': 0.25}
# units= for a file in px or with no unit: AUTO guesses, the others say what one user unit is
UNITS = {'PT': 25.4 / 72, 'PX': 25.4 / 96, 'MM': 1.0}

_SKIP = {'defs', 'symbol', 'clipPath', 'mask', 'marker', 'pattern', 'metadata', 'title', 'desc', 'style',
         'script', 'foreignObject', 'linearGradient', 'radialGradient', 'filter', 'font', 'font-face'}
_AUTO_ID = re.compile(r'^(g|path|layer|svg|rect|polygon|polyline|line|circle|ellipse|text|tspan|group|shape|use|'
                      r'[a-z]{0,3}\d+)([-_]?\d+)*$', re.I)          # ids a drawing app makes up: no name


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _attr(el, name):
    """An attribute by its local name (namespaced ones too: a layer's label, xlink:href)."""
    if name in el.attrib:
        return el.attrib[name]
    for k, v in el.attrib.items():
        if k.rsplit('}', 1)[-1] == name:
            return v
    return None


def _style(el):
    out = {}
    for part in (el.attrib.get('style') or '').split(';'):
        if ':' in part:
            k, v = part.split(':', 1)
            out[k.strip()] = v.strip()
    for k in ('display', 'visibility', 'marker-start', 'marker-mid', 'marker-end', 'marker', 'stroke-dasharray'):
        v = el.attrib.get(k)
        if v is not None and k not in out:
            out[k] = v.strip()
    return out


def _hidden(st):
    return st.get('display') == 'none' or st.get('visibility') in ('hidden', 'collapse')


def _has_marker(st):
    return any(st.get(k, 'none') not in ('none', '') for k in ('marker-start', 'marker-mid', 'marker-end', 'marker'))


# ---------------------------------------------------------------- numbers, lengths, transforms

_NUM = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


def _nums(s):
    return [float(x) for x in _NUM.findall(s or '')]


def _length(s, default=None):
    """(value, unit) of an SVG length; unit '' for none."""
    if s is None:
        return default
    m = re.match(r'\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)', s)
    if not m:
        return default
    return float(m.group(1)), m.group(2).lower()


def _f(el, name, default=0.0):
    v = _length(_attr(el, name))
    return v[0] if v else default


def _transform(s):
    M = np.eye(3)
    for name, args in re.findall(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)', s or ''):
        a = _nums(args)
        T = np.eye(3)
        if name == 'matrix' and len(a) == 6:
            T = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1]])
        elif name == 'translate' and a:
            T[0, 2], T[1, 2] = a[0], (a[1] if len(a) > 1 else 0.0)
        elif name == 'scale' and a:
            T[0, 0], T[1, 1] = a[0], (a[1] if len(a) > 1 else a[0])
        elif name == 'rotate' and a:
            c, s_ = math.cos(math.radians(a[0])), math.sin(math.radians(a[0]))
            R = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])
            if len(a) >= 3:
                A, B = np.eye(3), np.eye(3)
                A[:2, 2], B[:2, 2] = (a[1], a[2]), (-a[1], -a[2])
                R = A @ R @ B
            T = R
        elif name == 'skewX' and a:
            T[0, 1] = math.tan(math.radians(a[0]))
        elif name == 'skewY' and a:
            T[1, 0] = math.tan(math.radians(a[0]))
        M = M @ T
    return M


def _apply(M, P):
    P = np.asarray(P, float).reshape(-1, 2)
    return P @ M[:2, :2].T + M[:2, 2]


# ---------------------------------------------------------------- path data to polylines

class _Scan:
    """Reads the path data one command letter, number or arc flag at a time."""

    def __init__(self, d):
        self.d, self.i = d or '', 0

    def _ws(self):
        while self.i < len(self.d) and self.d[self.i] in ' \t\r\n,':
            self.i += 1

    def done(self):
        self._ws()
        return self.i >= len(self.d)

    def cmd(self):
        self._ws()
        if self.i < len(self.d) and self.d[self.i].isalpha():
            self.i += 1
            return self.d[self.i - 1]
        return None

    def num(self):
        self._ws()
        m = _NUM.match(self.d, self.i)
        if not m:
            raise ValueError(rpt("bad path data near: {0}", self.d[self.i:self.i + 20]))
        self.i = m.end()
        return float(m.group(0))

    def flag(self):
        self._ws()
        c = self.d[self.i:self.i + 1]
        if c not in ('0', '1'):
            raise ValueError(rpt("bad path data near: {0}", self.d[self.i:self.i + 20]))
        self.i += 1
        return c == '1'

    def more(self):
        self._ws()
        return self.i < len(self.d) and not self.d[self.i].isalpha()


def _cubic_n(P, flat):
    M = max(np.linalg.norm(P[0] - 2 * P[1] + P[2]), np.linalg.norm(P[1] - 2 * P[2] + P[3]))
    return int(min(256, max(1, math.ceil(math.sqrt(6 * M / (8 * flat))))))


def _quad_n(P, flat):
    M = np.linalg.norm(P[0] - 2 * P[1] + P[2])
    return int(min(256, max(1, math.ceil(math.sqrt(2 * M / (8 * flat))))))


def _arc_pts(p0, rx, ry, phi, large, sweep, p1, M, flat):
    """Points after p0 up to p1 of an SVG elliptical arc, in local coordinates (the endpoint form, SVG 1.1 F.6)."""
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12 or np.allclose(p0, p1):
        return [p1]
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0 - p1) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    k = math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
    if large == sweep:
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2, s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t0 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    r = max(rx, ry) * math.sqrt(abs(np.linalg.det(M[:2, :2])))   # the radius in mm, roughly
    step = 2 * math.acos(max(-1.0, 1 - flat / r)) if r > flat else math.pi / 2
    n = int(min(512, max(2, math.ceil(abs(dt) / max(step, 1e-3)))))
    out = []
    for j in range(1, n + 1):
        t = t0 + dt * j / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append(np.array([c * ex - s * ey + cx, s * ex + c * ey + cy]))
    out[-1] = np.asarray(p1, float)
    return out


def _path(d, M, flat):
    """[(points (n, 2) in mm, closed, joints)] of the path data d under the transform M (to mm). joints: the indices
    of the points where one segment ends and the next begins (the start of a closed one too)."""
    sc = _Scan(d)
    subs = []
    cur = start = np.zeros(2)
    pts, joints, closed = None, [], False
    last_ctrl, last_cmd = None, ''

    def to(P):
        return _apply(M, P)

    def finish():
        if pts is not None and len(pts) >= 2:
            subs.append((np.vstack(pts), closed, joints))

    cmd = None
    while not sc.done():
        c = sc.cmd()
        if c is None:
            if cmd is None:
                raise ValueError(rpt("bad path data near: {0}", sc.d[sc.i:sc.i + 20]))
            c = {'M': 'L', 'm': 'l'}.get(cmd, cmd)
        cmd = c
        rel = c.islower()
        C = c.upper()
        if C == 'Z':
            if pts is not None:
                closed = True
                finish()
            pts, joints, closed = None, [], False
            cur = start
            last_ctrl, last_cmd = None, C
            continue
        if C == 'M':
            p = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            finish()
            pts, joints, closed = [to(p)], [0], False
            cur = start = p
            last_ctrl, last_cmd = None, C
            continue
        if pts is None:                                   # a drawing command after Z starts at the last start
            pts, joints, closed = [to(cur)], [0], False
        if C == 'L':
            p = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            new = [p]
        elif C == 'H':
            x = sc.num() + (cur[0] if rel else 0)
            p = np.array([x, cur[1]])
            new = [p]
        elif C == 'V':
            y = sc.num() + (cur[1] if rel else 0)
            p = np.array([cur[0], y])
            new = [p]
        elif C in ('C', 'S'):
            if C == 'C':
                c1 = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            else:
                c1 = 2 * cur - last_ctrl if last_cmd in ('C', 'S') and last_ctrl is not None else cur.copy()
            c2 = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            p = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            Q = to(np.vstack([cur, c1, c2, p]))
            n = _cubic_n(Q, flat)
            t = np.linspace(0, 1, n + 1)[1:, None]
            seg = ((1 - t) ** 3) * Q[0] + 3 * ((1 - t) ** 2) * t * Q[1] + 3 * (1 - t) * t * t * Q[2] + t ** 3 * Q[3]
            pts.extend(seg)
            joints.append(len(pts) - 1)
            cur, last_ctrl, last_cmd = p, c2, C
            continue
        elif C in ('Q', 'T'):
            if C == 'Q':
                c1 = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            else:
                c1 = 2 * cur - last_ctrl if last_cmd in ('Q', 'T') and last_ctrl is not None else cur.copy()
            p = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            Q = to(np.vstack([cur, c1, p]))
            n = _quad_n(Q, flat)
            t = np.linspace(0, 1, n + 1)[1:, None]
            seg = ((1 - t) ** 2) * Q[0] + 2 * (1 - t) * t * Q[1] + t * t * Q[2]
            pts.extend(seg)
            joints.append(len(pts) - 1)
            cur, last_ctrl, last_cmd = p, c1, C
            continue
        elif C == 'A':
            rx, ry, phi = sc.num(), sc.num(), sc.num()
            large, sweep = sc.flag(), sc.flag()
            p = np.array([sc.num(), sc.num()]) + (cur if rel else 0)
            seg = _arc_pts(cur, rx, ry, phi, large, sweep, p, M, flat)
            pts.extend(to(np.vstack(seg)))
            joints.append(len(pts) - 1)
            cur, last_ctrl, last_cmd = p, None, C
            continue
        else:
            raise ValueError(rpt("bad path data near: {0}", c))
        pts.append(to(new[0])[0])
        joints.append(len(pts) - 1)
        cur, last_ctrl, last_cmd = p, None, C
    finish()
    return subs


def _clean(P, closed, joints):
    """Repeated points out (the joints kept on), the closing point dropped; (points, joints)."""
    keep, J, js = [0], set(), set(joints)
    for i in range(1, len(P)):
        if np.linalg.norm(P[i] - P[keep[-1]]) > 1e-6:
            keep.append(i)
        if i in js:
            J.add(len(keep) - 1)
    if 0 in js:
        J.add(0)
    P = P[keep]
    if closed or (len(P) >= 4 and np.linalg.norm(P[0] - P[-1]) <= 1e-6):
        if len(P) > 1 and np.linalg.norm(P[0] - P[-1]) <= 1e-6:
            P = P[:-1]
            J = {0 if j >= len(P) else j for j in J}
        closed = True
        J.add(0)
    return P, closed, sorted(J)


# ---------------------------------------------------------------- walking the document

def _shape_d(el, tag):
    """Path data for a basic shape."""
    if tag == 'rect':
        x, y, w, h = _f(el, 'x'), _f(el, 'y'), _f(el, 'width'), _f(el, 'height')
        if w <= 0 or h <= 0:
            return None
        return f"M{x},{y} H{x + w} V{y + h} H{x} Z"
    if tag in ('circle', 'ellipse'):
        cx, cy = _f(el, 'cx'), _f(el, 'cy')
        rx = _f(el, 'r') if tag == 'circle' else _f(el, 'rx')
        ry = _f(el, 'r') if tag == 'circle' else _f(el, 'ry')
        if rx <= 0 or ry <= 0:
            return None
        return (f"M{cx + rx},{cy} A{rx},{ry} 0 0 1 {cx},{cy + ry} A{rx},{ry} 0 0 1 {cx - rx},{cy} "
                f"A{rx},{ry} 0 0 1 {cx},{cy - ry} A{rx},{ry} 0 0 1 {cx + rx},{cy} Z")
    if tag == 'line':
        return f"M{_f(el, 'x1')},{_f(el, 'y1')} L{_f(el, 'x2')},{_f(el, 'y2')}"
    if tag in ('polyline', 'polygon'):
        a = _nums(_attr(el, 'points'))
        if len(a) < 4:
            return None
        d = "M" + " L".join(f"{a[i]},{a[i + 1]}" for i in range(0, len(a) - 1, 2))
        return d + (" Z" if tag == 'polygon' else "")
    return None


def _label(el):
    for k in ('label', 'data-name'):
        v = _attr(el, k)
        if v and v.strip():
            return v.strip()
    v = el.attrib.get('id')
    if not v:
        return None
    v = re.sub(r'_x([0-9A-Fa-f]{2})_', lambda m: chr(int(m.group(1), 16)), v)   # a drawing app's escaped id
    if _AUTO_ID.match(v):
        return None
    return v.strip()


def _text(el):
    return ' '.join(t.strip() for t in el.itertext() if t and t.strip())


def _walk(root, scale_M, flat):
    """The drawn things: [{'kind': 'path' | 'use' | 'text', ...}] with points in mm (y still down)."""
    ids = {el.attrib['id']: el for el in root.iter() if 'id' in el.attrib}
    out = []

    def go(el, M, classes, st_in, groups, depth):
        if depth > 64:
            return
        tag = _local(el.tag)
        if tag in _SKIP:
            return
        st = dict(st_in)
        st.update({k: v for k, v in _style(el).items()})
        if _hidden(st):
            return
        M = M @ _transform(el.attrib.get('transform'))
        cls = classes | set((el.attrib.get('class') or '').split())
        if tag in ('g', 'svg', 'a', 'switch'):
            g = groups + [(id(el), _label(el))] if tag != 'svg' or depth > 0 else groups
            if tag == 'svg' and depth > 0:                  # a nested svg: placed at x, y
                T = np.eye(3)
                T[0, 2], T[1, 2] = _f(el, 'x'), _f(el, 'y')
                M = M @ T
            for ch in el:
                go(ch, M, cls, st, g, depth + 1)
            return
        if tag == 'use':
            ref = (_attr(el, 'href') or '').lstrip('#')
            T = np.eye(3)
            T[0, 2], T[1, 2] = _f(el, 'x'), _f(el, 'y')
            target = ids.get(ref)
            own = M @ T
            if target is None:
                return
            if _local(target.tag) == 'symbol' or _in_defs(root, target):
                xy = _apply(own, [0.0, 0.0])[0]
                out.append({'kind': 'use', 'ref': ref, 'xy': xy, 'groups': groups})
                return
            go(target, own, cls, st, groups, depth + 1)
            return
        if tag == 'text':
            x, y = _nums(_attr(el, 'x'))[:1] or [0.0], _nums(_attr(el, 'y'))[:1] or [0.0]
            if not el.attrib.get('x'):                      # the first tspan's place
                ts = next((t for t in el.iter() if _local(t.tag) == 'tspan' and t.attrib.get('x')), None)
                if ts is not None:
                    x, y = _nums(ts.attrib.get('x'))[:1] or [0.0], _nums(ts.attrib.get('y'))[:1] or [0.0]
            s = _text(el)
            if s:
                out.append({'kind': 'text', 'xy': _apply(M, [x[0], y[0]])[0], 'text': s, 'groups': groups})
            return
        d = _attr(el, 'd') if tag == 'path' else _shape_d(el, tag)
        if not d:
            return
        for P, closed, joints in _path(d, M, flat):
            P, closed, joints = _clean(P, closed, joints)
            if len(P) < 2:
                continue
            out.append({'kind': 'path', 'pts': P, 'closed': closed and len(P) >= 3, 'joints': joints,
                        'classes': cls, 'marker': _has_marker(st), 'groups': groups,
                        'label': _label(el)})

    defs_cache = {}

    def _in_defs(rt, target):
        if not defs_cache:
            for dd in rt.iter():
                if _local(dd.tag) == 'defs':
                    for x in dd.iter():
                        defs_cache[id(x)] = True
            defs_cache.setdefault(None, False)
        return defs_cache.get(id(target), False)

    go(root, scale_M, set(), {}, [], 0)
    return out


def _units(root, raw, units):
    """(mm per user unit, M: user units to mm, how it was decided)."""
    vb = _nums(root.attrib.get('viewBox'))
    W, H = _length(root.attrib.get('width')), _length(root.attrib.get('height'))
    ox, oy = (vb[0], vb[1]) if len(vb) == 4 else (0.0, 0.0)
    absu = W is not None and W[1] in UNIT_MM and W[1] != 'px'
    ratio = W[0] / vb[2] if W and W[1] in ('', 'px') and W[0] > 0 and len(vb) == 4 and vb[2] > 0 else 1.0
    if absu:
        size = W[0] * UNIT_MM[W[1]]
        k = size / vb[2] if len(vb) == 4 and vb[2] > 0 else UNIT_MM['px']
        how = W[1]
    elif units in UNITS:
        k, how = UNITS[units] * ratio, units.lower()
    else:
        # the points app's marks: its generator note ("SVG Export Plug-In"), its entities, data-name on layers
        pt = b'SVG Export Plug-In' in raw[:4000] or b'&ns_extend;' in raw[:4000] or b'data-name=' in raw
        base = UNITS['PT'] if pt else UNITS['PX']
        k = base * ratio
        how = 'pt' if pt else 'px'
    M = np.diag([k, k, 1.0])
    M[0, 2], M[1, 2] = -ox * k, -oy * k
    return k, M, how


# ---------------------------------------------------------------- geometry helpers

def _area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _inside(P, Q):
    """Which of the points Q lie inside the closed polygon P (even-odd)."""
    Q = np.asarray(Q, float).reshape(-1, 2)
    x, y = Q[:, 0][:, None], Q[:, 1][:, None]
    a, b = P[None, :, :], np.roll(P, -1, 0)[None, :, :]
    y1, y2, x1, x2 = a[..., 1], b[..., 1], a[..., 0], b[..., 0]
    cross = (y1 > y) != (y2 > y)
    with np.errstate(divide='ignore', invalid='ignore'):
        xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    return ((cross & (x < xi)).sum(1) % 2) == 1


def _dist(P, Q, closed=True):
    """Distance of each point of Q to the polyline P."""
    Q = np.asarray(Q, float).reshape(-1, 2)
    A = P if not closed else np.vstack([P, P[:1]])
    a, d = A[:-1], np.diff(A, axis=0)
    ll = np.maximum((d * d).sum(1), 1e-12)
    out = np.empty(len(Q))
    for i0 in range(0, len(Q), 256):                  # a block of points at a time: (block, segments)
        q = Q[i0:i0 + 256][:, None, :]
        t = np.clip(((q - a) * d).sum(2) / ll, 0, 1)
        out[i0:i0 + 256] = np.linalg.norm(a + t[..., None] * d - q, axis=2).min(1)
    return out


def _within(P, Q):
    """All of Q inside P or on its line."""
    Q = np.asarray(Q, float).reshape(-1, 2)
    ins = _inside(P, Q)
    if ins.all():
        return True
    return bool((_dist(P, Q[~ins]) <= TOL).all())


def _sample(Q, n=64):
    return Q if len(Q) <= n else Q[np.linspace(0, len(Q) - 1, n).astype(int)]


def _turning(A):
    u = A - np.roll(A, 1, 0)
    v = np.roll(A, -1, 0) - A
    nu, nv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
    c = (u * v).sum(1) / np.maximum(nu * nv, 1e-12)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def _r(P):
    return [[round(float(x), 4), round(float(y), 4)] for x, y in np.asarray(P, float)]


def _up(P):
    P = np.array(P, float).reshape(-1, 2)
    P[:, 1] *= -1.0
    return P


# ---------------------------------------------------------------- one piece

def _edges(B, joints, rep):
    """The outline split at its corners: [{'id', 'pts'}]. B counter-clockwise, y up; joints its segment joints."""
    ang = _turning(B)
    ks = {j for j in joints if ang[j] >= TURN_DEG}
    if len(ks) < 3:
        ks |= {int(i) for i in np.nonzero(ang >= TURN_DEG)[0]}
        if len(ks) < 3:
            ks |= {int(i) for i in np.argsort(-ang)[:3]}
        rep['corners_by_angle'] += 1
    ks = sorted(ks)
    n = len(B)
    out = []
    for i, k0 in enumerate(ks):
        k1 = ks[(i + 1) % len(ks)]
        idx = list(range(k0, k1 + 1)) if k1 > k0 else list(range(k0, n)) + list(range(0, k1 + 1))
        out.append({'id': f"E{i + 1}", 'pts': _r(B[idx])})
    return out


def _is_allowance(P, Q):
    """Q (inside P) is the sewing line of which P is the cut line: an offset of it most of the way round."""
    if abs(_area(Q)) < 0.5 * abs(_area(P)):
        return False
    d = _dist(P, _sample(Q, 256))
    return SA_MIN <= float(np.median(d)) <= SA_MAX and float(d.max()) <= 1.5 * SA_MAX


def _piece(out_line, sa_line, items, name, rep):
    """A piece from its outline (y down), its cut line or None, and the drawn things inside it."""
    B, J = out_line['pts'], out_line['joints']
    B = _up(B)
    if _area(B) < 0:                                  # counter-clockwise; the joints follow the points
        n = len(B)
        B = B[::-1]
        J = [(n - 1 - j) % n for j in J]
    pc = {'name': name, 'edges': _edges(B, J, rep), 'enabled': True}
    ol = S.Outline(pc)
    Bc = np.vstack([ol.B, ol.B[:1]])
    cut = _up(sa_line['pts']) if sa_line is not None else None
    internals, notches = [], []

    def notch_at(q):
        """(edge, t) of the outline point nearest q, None when more than an allowance away."""
        a, d = Bc[:-1], np.diff(Bc, axis=0)
        ll = np.maximum((d * d).sum(1), 1e-12)
        t = np.clip(((q - a) * d).sum(1) / ll, 0, 1)
        dist = np.linalg.norm(a + t[:, None] * d - q, axis=1)
        j = int(dist.argmin())
        if dist[j] > (SA_MAX if cut is not None else 5 * TOL):
            return None
        return ol.edge_t(ol.cum[j] + t[j] * math.sqrt(ll[j]))

    def add_notch(q):
        et = notch_at(np.asarray(q, float))
        if et is None:
            rep['marks'] += 1
            return
        notches.append({'id': f"N{len(notches) + 1}", 'edge': et[0], 't': round(float(et[1]), 6), 'kind': 'slit'})

    for it in items:
        if it['kind'] == 'text':
            continue
        if it['kind'] == 'use':
            if 'notch' in it['ref'].lower():
                add_notch(_up(it['xy'])[0])
            else:
                rep['marks'] += 1
            continue
        P = _up(it['pts'])
        if cut is not None and (_dist(cut, _sample(P)) <= TOL).all():   # the cut line drawn again
            continue
        if 'help' in it['classes']:
            rep['help'] += 1
            continue
        if it['marker']:
            rep['grain_lines'] += 1
            continue
        if it['closed']:
            if abs(_area(P)) < MARK_AREA:              # a small mark: a notch on the line, else a button, a hole
                c = P.mean(0)
                if float(_dist(ol.B, [c])[0]) <= 3.0 or (cut is not None and float(_dist(cut, [c])[0]) <= 3.0):
                    add_notch(c)
                else:
                    rep['marks'] += 1
                continue
            if not _within(ol.B, _sample(P)):
                rep['outside'] += 1
                continue
            if (_dist(ol.B, _sample(P)) <= TOL).all():  # the outline drawn again
                continue
            internals.append({'pts': _r(np.vstack([P, P[:1]])), 'kind': 'shape', 'closed': True})
            continue
        L = S.polyline_len(P)
        if L < TOL:
            continue
        if (_dist(ol.B, P) <= TOL).all():              # a piece of the outline drawn again
            continue
        if L <= NOTCH_MM:                              # a slit: where it crosses or touches the line
            Pd = S.resample(P, int(math.ceil(L / (TOL / 2))) + 1)
            on_out = _dist(ol.B, Pd)
            on_cut = _dist(cut, Pd) if cut is not None else np.full(len(Pd), np.inf)
            if on_out.min() <= TOL or on_cut.min() <= TOL:
                add_notch(Pd[int(on_out.argmin())])
                continue
        if not _within(ol.B, _sample(P)):
            rep['outside'] += 1
            continue
        internals.append({'pts': _r(P), 'kind': 'line'})
    for k, it in enumerate(internals):
        it['id'] = f"I{k + 1}"
    if internals:
        pc['internals'] = [{'id': it['id'], **{k: v for k, v in it.items() if k != 'id'}} for it in internals]
    if notches:
        pc['notches'] = notches
    return pc


# ---------------------------------------------------------------- the file

def read(path, units='AUTO'):
    """(spec, report) for the SVG at path. units: 'AUTO', 'PT', 'PX' or 'MM', what one user unit is in a file whose
    size is in px or has no unit. Raises ValueError when no piece outline is found."""
    raw = open(path, 'rb').read()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(rpt("not an SVG file: {0}", exc))
    if _local(root.tag) != 'svg':
        raise ValueError(rpt("not an SVG file: {0}", _local(root.tag)))
    k, M, how = _units(root, raw, units)
    things = _walk(root, M, FLAT_MM)
    rep = {'pieces': 0, 'corners': 0, 'internals': 0, 'notches': 0, 'corners_by_angle': 0, 'grain_lines': 0,
           'seam_allowance': 0, 'marks': 0, 'help': 0, 'outside': 0, 'units_mm_per': k, 'units_from': how}
    # the closed lines big enough to be a piece, the same line drawn twice once, each with the one it lies in
    cl = [t for t in things if t['kind'] == 'path' and t['closed'] and 'help' not in t['classes']
          and not t['marker'] and abs(_area(t['pts'])) >= MIN_AREA]
    cl.sort(key=lambda t: -abs(_area(t['pts'])))
    uniq = []
    for t in cl:
        a = abs(_area(t['pts']))
        if any(abs(abs(_area(u['pts'])) - a) <= 1e-3 * a and (_dist(u['pts'], _sample(t['pts'])) <= TOL).all()
               for u in uniq):
            continue
        uniq.append(t)
    parent = {}
    for i, t in enumerate(uniq):                       # the smallest bigger one it lies in
        for j in range(i - 1, -1, -1):
            if _within(uniq[j]['pts'], _sample(t['pts'])):
                parent[i] = j
                break
    tops = [i for i in range(len(uniq)) if i not in parent]
    if not tops:
        raise ValueError(rpt("no piece outline found (a closed line of at least 2 x 2 cm)"))
    pieces = []                                        # (outline, cut line or None, top)
    for i in tops:
        kids = [j for j in range(len(uniq)) if parent.get(j) == i]
        sa = next((j for j in kids if _is_allowance(uniq[i]['pts'], uniq[j]['pts'])), None)
        if sa is not None:
            pieces.append((uniq[sa], uniq[i], i))
            rep['seam_allowance'] += 1
        else:
            pieces.append((uniq[i], None, i))
    owner = {}                                         # which piece each drawn thing lies in
    used = {id(o) for o, c, _ in pieces} | {id(c) for o, c, _ in pieces if c is not None}
    for t in things:
        if id(t) in used:
            continue
        Q = t['pts'] if t['kind'] == 'path' else np.asarray([t['xy']])
        small = t['kind'] != 'path' or (abs(_area(Q)) < MARK_AREA if t['closed'] else S.polyline_len(Q) <= NOTCH_MM)
        hit = None
        for n, (o, c, i) in enumerate(pieces):
            P = uniq[i]['pts']
            q = _sample(Q)
            on = _inside(P, q) | (_dist(P, q) <= TOL)
            if on.all() or (small and on.any()):      # a mark or a slit may stick out past the line it is on
                hit = n
                break
        if hit is None:
            if t['kind'] != 'text':
                rep['outside'] += 1
            continue
        owner.setdefault(hit, []).append(t)
    name = os.path.splitext(os.path.basename(path))[0]
    spec = S.new_spec(name, source={'kind': 'svg', 'path': path})
    taken = set()
    for n, (o, c, i) in enumerate(pieces):
        nm = _piece_name(o, c, pieces, owner.get(n, [])) or f"Piece {n + 1}"
        base, kk = nm, 2
        while nm in taken:
            nm = f"{base}_{kk}"
            kk += 1
        taken.add(nm)
        pc = _piece(o, c, owner.get(n, []), nm, rep)
        spec['pieces'].append(pc)
        rep['pieces'] += 1
        rep['corners'] += len(pc['edges'])
        rep['internals'] += len(pc.get('internals', []))
        rep['notches'] += len(pc.get('notches', []))
    return spec, rep


def _piece_name(o, c, pieces, items):
    """The nearest group or layer round the outline that holds no other piece, else the outline's own label, else
    the first text inside it."""
    mine = {id(o)} | ({id(c)} if c is not None else set())
    others = [p for p in pieces if id(p[0]) not in mine]
    for gid, lab in reversed(o['groups']):
        if any(gid in {g for g, _ in p[0]['groups']} for p in others):
            break
        if lab:
            return lab
    if o.get('label'):
        return o['label']
    txt = next((t['text'] for t in items if t['kind'] == 'text'), None)
    return txt[:40].strip() if txt else None
