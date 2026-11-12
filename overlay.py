# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport overlay on the flat pattern: seams (both sides in one color, an arrow for the
direction, a tick at the start), the active seam highlighted and joined across, flagged seams
in orange, and piece names. In 3D, the seams: both sides in the seam's color joined by rungs,
on the placed pieces before a Dress and on the garment (its current frame) after one."""
import colorsys
import json
import os

import blf
import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_origin_3d
from mathutils import Vector
from gpu_extras.batch import batch_for_shader

from . import pattern_view as PV
from . import placed_view as PLV
from . import store
from . import view_visibility as VV
from .core import body_axes as BA
from .core import seam_edit as SE
from .core import seam_path as SPATH
from .core import spec as S

_handles = []
_geo = {'key': None}
_geo_views = {}             # bounded caches by logical viewport membership, never saved to the scene
_jobseg = {'key': None}
_gpos = {'key': None}        # the garment's seam points, kept while the frame, the shape and the transform stay
_batch3 = {'key': None}      # the 3D seam batch, kept while the points and the view stay
_mesh_epochs = {}
_mesh_epoch = 0
_scene_index_cache = {'key': None}


@bpy.app.handlers.persistent
def _geometry_changed(_scene, depsgraph):
    """Only invalidate derived mesh caches; never write scene data in a handler."""
    global _mesh_epoch
    for update in depsgraph.updates:
        if update.is_updated_geometry and isinstance(update.id, (bpy.types.Mesh, bpy.types.Object)):
            _mesh_epoch += 1
            _mesh_epochs[update.id.original.as_pointer()] = _mesh_epoch

RUNG_MM = 10.0       # one rung about every 10 mm of seam (a ladder, not a wall)
LIFT_MM = 5.0        # 3D seam lines drawn this much towards the eye, so the cloth they lie on does not hide them
#   (a tint on the cloth itself lost the depth test at 0.3 mm and showed at 3 mm, KNOWLEDGE reference_post_view_tint_depth)

FLAG = (1.0, 0.55, 0.1, 1.0)
ACTIVE = (1.0, 1.0, 1.0, 1.0)
OFF = (0.5, 0.5, 0.5, 0.6)          # a seam turned off (as on the pattern)


def _palette(i):
    r, g, b = colorsys.hsv_to_rgb((i * 0.61803398875) % 1.0, 0.65, 0.95)
    return (r, g, b, 1.0)


def _arrow(P, frac=0.6, size=0.012):
    """Two short segments forming an arrowhead at frac along the polyline P (world, n x 3)."""
    if len(P) < 2:
        return []
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    cum = np.r_[0, np.cumsum(seg)]
    if cum[-1] <= 0:
        return []
    s = frac * cum[-1]
    k = min(int(np.searchsorted(cum, s)) - 1, len(P) - 2)
    k = max(k, 0)
    t = (s - cum[k]) / max(seg[k], 1e-12)
    tip = P[k] + t * (P[k + 1] - P[k])
    d = (P[k + 1] - P[k]) / max(seg[k], 1e-12)
    n = np.array([-d[1], d[0], 0.0])
    return [tip, tip - size * d + 0.6 * size * n, tip, tip - size * d - 0.6 * size * n]


def _dashes(u, v, dash=0.006, gap=0.004):
    """A dotted line from u to v (world) as LINES pairs."""
    d = np.asarray(v) - np.asarray(u)
    L = float(np.linalg.norm(d))
    if L <= 1e-9:
        return []
    out, t = [], 0.0
    while t < L:
        a, b = t, min(t + dash, L)
        out += [np.asarray(u) + d * (a / L), np.asarray(u) + d * (b / L)]
        t += dash + gap
    return out


def _tick(P, size=0.008):
    if len(P) < 2:
        return []
    d = P[1] - P[0]
    L = np.linalg.norm(d)
    if L <= 0:
        return []
    d = d / L
    n = np.array([-d[1], d[0], 0.0])
    return [P[0] - size * n, P[0] + size * n]


def wrap_radii(spec, outlines, pls=None):
    """{piece: radius mm} the placed pieces are wrapped at (core/body_axes.wrap_radii, as Dress wraps them), with
    the placements pls {piece: placement} put in first (a Place being previewed)."""
    pl = dict(spec.get('placement', {}), **(pls or {}))
    on = {pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)} | set(pls or ())
    pieces = {}
    for n, p in pl.items():
        if n in on and n in outlines:
            w, hgt = np.ptp(S.spun(p, outlines[n].B), axis=0)        # as placed (core/spec.spun)
            pieces[n] = (p['guide'], float(w), float(hgt))
    from .core import roles as RO
    return BA.wrap_radii(spec.get('guides', {}), pieces, RO.with_role(spec, RO.ARM_PANEL_ROLES))


WRAP_STEP_MM = 10.0


def laid_mm(spec, pl, Q, r=None):
    """Layout points Q (mm) of a placed piece in the world (mm): laid flat (core/spec.is_flat, user 2026-11-10), or
    wrapped round its guide at radius r; None when its guide is gone."""
    if not pl:
        return None
    Q = S.spun(pl, Q)                            # a turn on the pattern after placing does not turn it on the body
    if S.is_flat(pl):
        return BA.lay_flat(pl['flat'], np.asarray(Q, float), pl['anchor'])
    g = spec.get('guides', {}).get(pl['guide'])
    if g is None:
        return None
    return BA.wrap(g, np.asarray(Q, float), pl['anchor'], pl['theta'], pl['h'], r, tilt=pl.get('tilt', 0.0))


def wrapped_outline(spec, outlines, name, pl=None, radii=None):
    """World (m) outline of a placed piece laid round its guide (a plain cylinder wrap, for display), at the radius
    Dress starts it at so it does not lie over itself or the other pieces round an arm or leg."""
    pl = pl or spec.get('placement', {}).get(name)
    if not pl or (pl['guide'] not in spec.get('guides', {}) and not S.is_flat(pl)):
        return None
    B = outlines[name].B
    r = (radii if radii is not None else wrap_radii(spec, outlines, {name: pl})).get(name)
    Q = [B[0]]
    for a, b in zip(B, np.vstack([B[1:], B[:1]])):   # long sides cut: a drawn rectangle has 4 points only, its
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / WRAP_STEP_MM)))   # sides went straight through the body
        Q += [a + (b - a) * (k / n) for k in range(1, n + 1)]
    return laid_mm(spec, pl, Q, r) * PV.MM


FILL_MM = 20.0      # the fill's inner points this far apart, so its triangles bend round the guide


def _inside(P, B):
    """P (n x 2) inside the closed polygon B (even-odd)."""
    x, y = P[:, 0][:, None], P[:, 1][:, None]
    ax, ay = B[:, 0][None], B[:, 1][None]
    bx, by = np.roll(B[:, 0], -1)[None], np.roll(B[:, 1], -1)[None]
    cross = ((ay > y) != (by > y)) & (x < ax + (y - ay) * (bx - ax) / np.where(by == ay, 1e-12, by - ay))
    return cross.sum(1) % 2 == 1


def wrapped_fill(spec, outlines, name, radii=None):
    """World (m) triangles (n*3 x 3) filling a placed piece laid round its guide, as wrapped_outline (user 2026-10-22:
    the outline alone gave no depth, a piece in front of the body and one behind it looked the same)."""
    from mathutils.geometry import delaunay_2d_cdt
    pl = spec.get('placement', {}).get(name)
    if not pl or (pl['guide'] not in spec.get('guides', {}) and not S.is_flat(pl)):
        return None
    O = outlines[name]
    # the outline every WRAP_STEP_MM (its own points are dense: cut as they are, a front panel was 80,000 triangles)
    # and a grid inside it (an ear-clipped outline alone is long slivers, flat through the body round a guide)
    B = O.at(np.linspace(0.0, O.L, max(8, int(np.ceil(O.L / WRAP_STEP_MM))), endpoint=False))
    lo, hi = B.min(0), B.max(0)
    gx, gy = np.meshgrid(np.arange(lo[0] + FILL_MM / 2, hi[0], FILL_MM), np.arange(lo[1] + FILL_MM / 2, hi[1], FILL_MM))
    G = np.c_[gx.ravel(), gy.ravel()]
    G = G[_inside(G, B)] if len(G) else G.reshape(0, 2)
    n = len(B)
    # holes cut out (core/spec.holes): their rings as constraints, no grid point and no triangle inside
    hs = [R for _, R in S.holes(next(p for p in spec['pieces'] if p['name'] == name))]
    E = [(i, (i + 1) % n) for i in range(n)]
    if hs:
        G = G[~S.in_holes(G, hs)]
        k = n + len(G)
        for R in hs:
            E += [(k + j, k + (j + 1) % len(R)) for j in range(len(R))]
            k += len(R)
    V, _, F = delaunay_2d_cdt([Vector(p) for p in np.vstack([B, G] + hs)], E, [list(range(n))], 1, 1e-6)[:3]
    if not F:
        return None
    V = np.array([tuple(v) for v in V], float)
    T = np.array([f for f in F if len(f) == 3], int)
    if hs:
        from .core import pattern_build as PB
        T = PB.drop_hole_tris(V, T, hs)
    r = (radii if radii is not None else wrap_radii(spec, outlines, {name: pl})).get(name)
    return laid_mm(spec, pl, V[T.ravel()], r) * PV.MM


def guide_lines(g, n=48):
    """World (m) line segments drawing a guide: rings at both ends and the middle, and 8 lines along it."""
    th = np.linspace(0, 360, n + 1)
    segs = []
    for h in (g['h0'], 0.5 * (g['h0'] + g['h1']), g['h1']):
        P = np.array([BA.point(g, t, h) for t in th]) * PV.MM
        segs += [p for u, v in zip(P[:-1], P[1:]) for p in (u, v)]
    for t in range(0, 360, 45):
        segs += [BA.point(g, t, g['h0']) * PV.MM, BA.point(g, t, g['h1']) * PV.MM]
    return segs



_gedit = {'key': None}       # Edit Guides: the guide lines drawn from the objects
_pflat = {'key': None}       # the placed seams' layout polylines and their guide wraps, per spec


def _placed_flat(spec, outlines, enabled):
    """Per seam on placed pieces: (id, piece a, piece b, side a, side b (layout mm, sample k of a sewn to sample k of
    b, as the pattern build stitches them), color, side a / b wrapped round its guide (world m, or None))."""
    key = (store._cache['key'], frozenset(enabled))
    if _pflat['key'] == key:
        return _pflat['rows']
    pl = spec.get('placement', {})
    gd = spec.get('guides', {})
    rad = wrap_radii(spec, outlines)
    rows = []
    for sm in spec['seams']:
        a, b = sm['a']['piece'], sm['b']['piece']
        if a not in enabled or b not in enabled:
            continue
        try:
            fa = S.side_polyline(spec, sm['a'], outlines, step=2.0)
            fb = S.side_polyline(spec, sm['b'], outlines, step=2.0)
        except Exception:
            continue
        n = max(2, int(max(S.polyline_len(fa), S.polyline_len(fb)) / 2.0) + 1)
        fa, fb = S.resample(fa, n), S.resample(fb, n)
        pa, pb = pl.get(a), pl.get(b)
        wa = laid_mm(spec, pa, fa, rad.get(a))
        wa = wa * PV.MM if wa is not None else None
        wb = laid_mm(spec, pb, fb, rad.get(b))
        wb = wb * PV.MM if wb is not None else None
        c = OFF if not sm.get('enabled', True) else (FLAG if sm.get('check') else SE.color(sm))
        rows.append((sm['id'], a, b, fa, fb, c, wa, wb))
    _pflat.update(key=key, rows=rows)
    return rows


_plad = {}          # seam id -> (key, points, colors, ids): a ladder made again only when one of its sides moved


def _ladder_np(A, B, fa, c, arc=None):
    """_ladder as arrays: both sides' segments and a rung about every RUNG_MM (flat arc length of fa). arc(A, B) ->
    (k, n+1, 3): the rungs drawn as those polylines (a seam pulled round the body) instead of straight."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(fa, axis=0), axis=1))]
    r = (cum // RUNG_MM).astype(int)
    pick = np.r_[True, r[1:] != r[:-1]]
    pick[-1] = True
    segs = [np.stack([P[:-1], P[1:]], 1).reshape(-1, 3) for P in (A, B)]
    if arc is None:
        segs.append(np.stack([A[pick], B[pick]], 1).reshape(-1, 3))
    else:
        Q = arc(A[pick], B[pick])
        segs.append(np.stack([Q[:, :-1], Q[:, 1:]], 2).reshape(-1, 3))
    pos = np.vstack(segs)
    return pos, np.tile(np.asarray(c, np.float32), (len(pos), 1)), len(pos) // 2


def _arc_of(spec, outlines, sid, a, shown):
    """For a seam pulled round the body (spec seam 'path'): the rung drawing, the arcs round its part's axis on the
    side it is set to go (auto: away from its piece a's cloth, as the solver chooses); None for a straight seam."""
    sm = next((x for x in spec['seams'] if x['id'] == sid), None)
    if sm is None or not sm.get('path'):
        return None
    _gn, g = SPATH.guide_of(spec, sm)
    if g is None:
        return None
    side = sm['path'].get('side', 'auto')
    if side in SPATH.DIRS:
        sd = np.asarray(SPATH.DIRS[side], float)
    else:
        Bo = outlines[a].B
        C = PLV.to_world(shown[a], Bo) if a in shown else wrapped_outline(spec, outlines, a)
        if C is None:
            return None
        R = np.asarray(g['R'], float)
        pc = np.asarray(C, float).mean(0) / PV.MM - np.asarray(g['o'], float)
        sd = -(pc - (pc @ R[2]) * R[2])
    return lambda P, Q: SPATH.arc_points(g, P / PV.MM, Q / PV.MM, sd, n=10) * PV.MM


def placed_seams(spec, outlines, enabled, shown=None):
    """World (m) seam ladders on the placed pieces: on a piece's placed object as it is now (moved, rotated, edited;
    shown = placed_view.shown), else on the cylinder wrap the placed outlines are drawn with. Sample k of side a
    joined to sample k of side b, as the pattern build stitches them. (points (2k, 3), colors (2k, 4), seam id per
    line). A seam's ladder is made again only when one of its sides moved (a drag redraws every frame)."""
    shown = shown or {}
    pos, col, ids = [], [], []
    sk = store._cache['key']
    # a piece whose placed object is hidden (a Dress hid them, a Reset of some pieces shows only those) is not
    # drawn on its cylinder wrap instead: its seams are left out
    hidden = set(PLV.objects(spec)) - set(shown)
    for sid, a, b, fa, fb, c, wa, wb in _placed_flat(spec, outlines, enabled):
        if a in hidden or b in hidden:
            continue
        key = (sk, a, b, shown[a][4] if a in shown else None, shown[b][4] if b in shown else None, c)
        hit = _plad.get(sid)
        if hit is None or hit[0] != key:
            A = PLV.to_world(shown[a], fa) if a in shown else wa
            B = PLV.to_world(shown[b], fb) if b in shown else wb
            hit = _plad[sid] = (key, None) if A is None or B is None else (
                key, _ladder_np(A, B, fa, c, _arc_of(spec, outlines, sid, a, shown)))
        if hit[1] is None:
            continue
        P, C, k = hit[1]
        pos.append(P)
        col.append(C)
        ids += [sid] * k
    if not pos:
        return np.zeros((0, 3)), np.zeros((0, 4)), []
    return np.vstack(pos), np.vstack(col), ids


def _side_verts(spec, outlines, side, flat, lo, hi, n):
    """Garment vertex indices of a seam side: n samples of its pattern polyline (layout mm), each the nearest vertex of
    its piece by the vertices' pattern position (flat, the piece's vertices lo:hi)."""
    P = S.resample(S.side_polyline(spec, side, outlines, step=2.0), n)
    V = flat[lo:hi]
    d = ((P[:, None, :] - V[None, :, :]) ** 2).sum(2)
    return lo + d.argmin(1), P


def _scene_seam_index(ob, n_verts, spec, outlines):
    """Read the garment's existing piece/UV correspondence; never make a job."""
    if ob is None or len(ob.data.vertices) != n_verts:
        return None
    me = ob.data
    attr = me.attributes.get('ac9_piece')
    try:
        names = json.loads(ob.get('ac9_piece_names', '[]'))
    except (ValueError, TypeError):
        return None
    if (not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names) or
            len(set(names)) != len(names) or attr is None or attr.domain != 'FACE' or
            attr.data_type != 'INT' or len(attr.data) != len(me.polygons) or me.uv_layers.get('UVMap') is None):
        return None
    sizes = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get('loop_total', sizes)
    if not len(sizes) or (sizes != 3).any():
        return None
    owner = np.empty(len(me.polygons), np.int32)
    attr.data.foreach_get('value', owner)
    if (owner < 0).any() or (owner >= len(names)).any():
        return None
    loops = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get('vertex_index', loops)
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers['UVMap'].data.foreach_get('uv', uv)
    points = uv.reshape(-1, 2).astype(np.float64) * 1000.0
    if not np.isfinite(points).all():
        return None
    low, high = np.full(n_verts, len(names), np.int32), np.full(n_verts, -1, np.int32)
    np.minimum.at(low, loops, np.repeat(owner, 3))
    np.maximum.at(high, loops, np.repeat(owner, 3))
    if (low != high).any() or (high < 0).any() or (np.diff(high) < 0).any():
        return None                         # shared/loose vertices or noncontiguous piece ranges
    flat = np.zeros((n_verts, 2))
    flat[loops] = points
    if np.max(np.abs(flat[loops] - points)) > 0.001:
        return None                         # one vertex has conflicting pattern UVs
    offs = np.searchsorted(high, np.arange(len(names)))
    cache_key = (ob.as_pointer(), me.as_pointer(), store._cache['key'], tuple(names),
                 hash(loops.tobytes()), hash(owner.tobytes()), hash(flat.tobytes()),
                 float(bpy.context.scene.ac9_cloth_sim.mesh_size))
    if _scene_index_cache['key'] == cache_key:
        return _scene_index_cache['index']
    if outlines is None:
        return None
    # The existing shape-transfer tolerance is also the acceptance limit here:
    # UV presence alone is not evidence of current pattern correspondence.
    from .core import transfer as TR
    from .core.pattern_build import dist_to_ring, dist_to_segments
    from mathutils.bvhtree import BVHTree
    tol = TR.SAME_SHAPE_MM
    # Match shape_changed's existing reverse-boundary allowance: the mesher
    # can merge a pattern corner by up to half its requested spacing.
    coverage_tol = max(tol, 0.5 * float(bpy.context.scene.ac9_cloth_sim.mesh_size))
    T = loops.reshape(-1, 3)
    ends = list(offs) + [n_verts]
    pieces = {pc['name']: pc for pc in spec['pieces']}
    for i, name in enumerate(names):
        if name not in outlines or ends[i] == ends[i + 1]:
            return None
        V = flat[ends[i]:ends[i + 1]]
        tri = T[owner == i] - ends[i]
        rings = [outlines[name].B] + [ring for _ids, ring in S.holes(pieces[name])]
        a, b, c = V[tri[:, 0]], V[tri[:, 1]], V[tri[:, 2]]
        area = (b[:, 0]-a[:, 0])*(c[:, 1]-a[:, 1])-(b[:, 1]-a[:, 1])*(c[:, 0]-a[:, 0])
        if not len(area) or (abs(area) <= 1e-12).any() or not ((area > 0).all() or (area < 0).all()):
            return None
        edges = np.sort(np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
        edges, counts = np.unique(edges, axis=0, return_counts=True)
        boundary = edges[counts == 1]
        if (counts > 2).any() or not len(boundary):
            return None
        points = V[np.unique(boundary)]
        if np.min(np.stack([dist_to_ring(points, ring) for ring in rings]), axis=0).max() > tol:
            return None
        A, B = V[boundary[:, 0]], V[boundary[:, 1]]
        if any(dist_to_segments(ring, A, B).max() > coverage_tol for ring in rings):
            return None
        def ring_area(ring):
            return abs(np.sum(ring[:, 0]*np.roll(ring[:, 1], -1)-ring[:, 1]*np.roll(ring[:, 0], -1)))*0.5
        expected = ring_area(rings[0]) - sum(ring_area(r) for r in rings[1:])
        perimeter = sum(np.linalg.norm(np.roll(r, -1, axis=0)-r, axis=1).sum() for r in rings)
        if abs(abs(area).sum()*0.5-expected) > tol*perimeter:
            return None
        tree = BVHTree.FromPolygons([(float(x), float(y), 0.) for x, y in V], tri.tolist(), all_triangles=True)
        for sm in spec['seams']:
            for side in ('a', 'b'):
                if sm[side]['piece'] == name:
                    Q = S.side_polyline(spec, sm[side], outlines, step=2.)
                    if any(tree.find_nearest(Vector((float(x), float(y), 0.)))[3] > coverage_tol for x, y in Q):
                        return None
    index = (flat, offs, np.empty((0, 2), np.int64), np.asarray([], str), names, 'mesh')
    _scene_index_cache.update(key=cache_key, index=index)
    return index


def _seam_index(job, ob, n_verts, spec, outlines):
    if job is not None:
        try:
            with np.load(os.path.join(job, 'scene.idx.npz')) as z:
                flat, offs = z['flat'], z['offs']
                E = z['E_st'] if 'E_st' in z.files else np.empty((0, 2), np.int64)
                sid = z['E_seam'].astype(str) if 'E_seam' in z.files else np.full(len(E), '')
                triangles = z['F'] if 'F' in z.files else None
                owners = z['fp'] if 'fp' in z.files else None
            with open(os.path.join(job, 'job.json'), encoding='utf-8') as f:
                names = json.load(f)['names']
            if (not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names) or
                    len(set(names)) != len(names)):
                raise ValueError('Invalid result piece names')
            raw = ob.get('ac9_piece_names') if ob is not None else None
            matches = True
            if ob is not None and raw is not None:
                me = ob.data
                actual = np.empty(len(me.loops), np.int32)
                me.loops.foreach_get('vertex_index', actual)
                pa = me.attributes.get('ac9_piece')
                values = np.empty(len(me.polygons), np.int32)
                if pa is not None and pa.domain == 'FACE' and len(pa.data) == len(values):
                    pa.data.foreach_get('value', values)
                    matches = (triangles is not None and owners is not None and owners.dtype.kind in 'iu' and
                               (owners >= 0).all() and (owners < len(names)).all() and actual.size == triangles.size and
                               np.array_equal(actual, triangles.ravel()) and np.array_equal(values, owners))
                    uv_map = me.uv_layers.get('UVMap')
                    if matches and flat.shape == (n_verts, 2) and uv_map is not None:
                        uv = np.empty(len(me.loops) * 2, np.float32)
                        uv_map.data.foreach_get('uv', uv)
                        matches = bool(np.isfinite(uv).all() and
                                       np.max(abs(flat[actual]-uv.reshape(-1, 2)*1000.0)) <= 0.001)
                    else:
                        matches = False
                    if matches:
                        low = np.full(n_verts, len(names), np.int32)
                        high = np.full(n_verts, -1, np.int32)
                        np.minimum.at(low, actual, np.repeat(values, 3))
                        np.maximum.at(high, actual, np.repeat(values, 3))
                        matches = bool((low == high).all() and (high >= 0).all() and
                                       (np.diff(high) >= 0).all() and
                                       np.array_equal(offs, np.searchsorted(high, np.arange(len(names)))))
                        if matches and E.dtype.kind in 'iu' and E.ndim == 2 and E.shape[1] == 2 and (E >= 0).all() and (E < n_verts).all() and len(sid) == len(E):
                            by_id = {sm['id']: sm for sm in spec['seams']}
                            for seam_id in dict.fromkeys(sid.tolist()):
                                sm = by_id.get(seam_id)
                                if sm is None:       # deleted seams are ignored as before
                                    continue
                                if sm['a']['piece'] not in names or sm['b']['piece'] not in names:
                                    matches = False
                                    break
                                a, b = names.index(sm['a']['piece']), names.index(sm['b']['piece'])
                                owners_a, owners_b = high[E[sid == seam_id, 0]], high[E[sid == seam_id, 1]]
                                if not (((owners_a == a) & (owners_b == b)).all() or
                                        ((owners_a == b) & (owners_b == a)).all()):
                                    matches = False
                                    break
                else:
                    matches = False
            if (matches and E.dtype.kind in 'iu' and offs.dtype.kind in 'iu' and
                    flat.shape == (n_verts, 2) and np.isfinite(flat).all() and len(names) == len(offs) and
                    len(offs) and offs[0] == 0 and (np.diff(offs) >= 0).all() and
                    (offs >= 0).all() and (offs <= n_verts).all() and E.ndim == 2 and E.shape[1] == 2 and
                    len(sid) == len(E) and (E >= 0).all() and (E < n_verts).all() and
                    (raw is None or json.loads(raw) == names)):
                return flat, offs, E, sid, names, 'job'
        except (OSError, KeyError, ValueError, TypeError):
            pass
    return _scene_seam_index(ob, n_verts, spec, outlines)


def _garment_segments(job, spec, n_verts, outlines=None, ob=None):
    """Vertex index pairs (M x 2), colors and seam ids of the garment's seam ladders (cached per job and spec).
    A seam the job sewed: its stitched vertices on side a and on side b chained in stitch order (broken where two
    neighbors lie far apart on the pattern), and a rung about every RUNG_MM. A seam added since (in the spec, not
    in the job): its sides found on the garment by their pattern position (the vertices' flat coordinates), sample k
    of side a joined to sample k of side b -- across the body, as it is not sewn yet. A seam deleted since is not
    drawn; a seam turned off is gray (user 2026-10-14). A missing/stale index is recovered read-only from verified
    current mesh metadata; None when neither correspondence is valid."""
    try:
        stat = os.stat(os.path.join(job, 'scene.idx.npz')) if job is not None else None
        if stat is None:
            raise OSError('No result folder')
        generation = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        generation = None
    owner_key = ((ob.as_pointer(), ob.data.as_pointer(),
                  _mesh_epochs.get(ob.data.as_pointer(), 0), str(ob.get('ac9_piece_names', '')),
                  float(bpy.context.scene.ac9_cloth_sim.mesh_size))
                 if ob is not None else None)
    key = (job, store._cache['key'], n_verts, generation, owner_key)
    if _jobseg['key'] == key:
        return _jobseg['seg'], _jobseg['col']
    seg, col, sids, rung, rng, flat = None, None, [], [], {}, None
    source = None
    try:
        index = _seam_index(job, ob, n_verts, spec, outlines)
        if index is None:
            raise ValueError('No verified seam correspondence')
        flat, offs, E, sid, names, source = index
        if len(flat) == n_verts:
            by_id = {sm['id']: sm for sm in spec['seams']}
            ends = list(offs) + [len(flat)]
            rng = {nm: (int(ends[i]), int(ends[i + 1])) for i, nm in enumerate(names)}
            segs, cols = [], []

            def color(sm):
                return OFF if not sm.get('enabled', True) else (FLAG if sm.get('check') else SE.color(sm))
            for s_ in dict.fromkeys(sid.tolist()):
                sm = by_id.get(s_)
                if sm is None:
                    continue                        # deleted since the Dress
                rows = E[sid == s_]
                c = color(sm)
                for side in (0, 1):
                    d = np.linalg.norm(np.diff(flat[rows[:, side]], axis=0), axis=1)
                    if not len(d):
                        continue
                    ok = d <= 4.0 * max(float(np.median(d)), 1e-6)
                    segs += [(int(a), int(b)) for a, b, o in zip(rows[:-1, side], rows[1:, side], ok) if o]
                    cols += [c] * int(ok.sum())
                    sids += [s_] * int(ok.sum())
                    rung += [False] * int(ok.sum())
                fa = flat[rows[:, 0]]
                cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(fa, axis=0), axis=1))]
                pick = np.r_[True, np.diff((cum // RUNG_MM).astype(int)) != 0]
                pick[-1] = True
                segs += [(int(a), int(b)) for a, b in rows[pick]]
                cols += [c] * int(pick.sum())
                sids += [s_] * int(pick.sum())
                rung += [True] * int(pick.sum())
            done = set(sid.tolist())
            for sm in spec['seams'] if outlines is not None else []:
                if sm['id'] in done or sm['a']['piece'] not in names or sm['b']['piece'] not in names:
                    continue
                try:
                    ia, ib = names.index(sm['a']['piece']), names.index(sm['b']['piece'])
                    n = max(2, int(max(S.polyline_len(S.side_polyline(spec, sm[k], outlines, step=2.0))
                                       for k in ('a', 'b')) / 2.0) + 1)
                    va, fa = _side_verts(spec, outlines, sm['a'], flat, ends[ia], ends[ia + 1], n)
                    vb, _fb = _side_verts(spec, outlines, sm['b'], flat, ends[ib], ends[ib + 1], n)
                except Exception:
                    continue
                c = color(sm)
                for v in (va, vb):
                    pr = [(int(a), int(b)) for a, b in zip(v[:-1], v[1:]) if a != b]
                    segs += pr
                    cols += [c] * len(pr)
                    sids += [sm['id']] * len(pr)
                    rung += [False] * len(pr)
                cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(fa, axis=0), axis=1))]
                pick = np.r_[True, np.diff((cum // RUNG_MM).astype(int)) != 0]
                pick[-1] = True
                segs += [(int(a), int(b)) for a, b in zip(va[pick], vb[pick])]
                cols += [c] * int(pick.sum())
                sids += [sm['id']] * int(pick.sum())
                rung += [True] * int(pick.sum())
            seg, col = np.array(segs, np.int64).reshape(-1, 2), cols
    except (OSError, KeyError, ValueError):
        seg, col, sids, rung = None, None, [], []
    _jobseg.update(key=key, seg=seg, col=col, ids=sids, rung=np.asarray(rung, bool), rng=rng,
                   flat=flat if seg is not None else None, index_source=source)
    return seg, col


def _mixed_ladders(spec, outlines, plan, visible, shown, ob, co):
    """Join visible placed/result sides on their current owners, never on an old guide wrap."""
    flat, ranges = _jobseg.get('flat'), _jobseg.get('rng', {})
    pos, colors, ids = [], [], []
    entries = {n: e for n, e in shown.items() if plan.get(n, {}).get('source') == 'placed'}
    if flat is None:
        return np.zeros((0, 3)), [], []
    for sm in spec['seams']:
        names = [sm[k]['piece'] for k in ('a', 'b')]
        states = [plan.get(n, {}) for n in names]
        if (any(n not in visible or not v.get('seams') for n, v in zip(names, states)) or
                {v.get('source') for v in states} != {'placed', 'result'}):
            continue
        for name, state in zip(names, states):
            if state['source'] != 'result' or name in entries:
                continue
            lo, hi = ranges.get(name, (0, 0))
            if not 0 <= lo < hi <= len(co):
                continue
            tris = _garment_tris(ob, lo, hi)
            if len(tris):
                entries[name] = (flat[lo:hi], tris, co[lo:hi],
                                 ('result-seam', _jobseg['key'], _gtri['key'], name))
        if any(n not in entries for n in names):
            continue
        try:
            sides = [S.side_polyline(spec, sm[k], outlines, step=2.0) for k in ('a', 'b')]
            count = max(2, int(max(S.polyline_len(p) for p in sides) / 2.0) + 1)
            fa, fb = [S.resample(p, count) for p in sides]
            A, B = [PLV.to_world(entries[n], p) for n, p in zip(names, (fa, fb))]
        except (KeyError, ValueError, IndexError):
            continue
        if A is None or B is None:
            continue
        color = OFF if not sm.get('enabled', True) else (FLAG if sm.get('check') else SE.color(sm))
        P, C, size = _ladder_np(A, B, fa, color, _arc_of(spec, outlines, sm['id'], names[0], entries))
        pos.append(P)
        colors.extend(C)
        ids.extend([sm['id']] * size)
    return (np.vstack(pos) if pos else np.zeros((0, 3))), colors, ids


def garment_seams(context, spec):
    """Current result seams plus placed and mixed-owner ladders; None without a visible result owner."""
    from . import dress as DR                   # imported here: dress -> placement -> this module
    ob = bpy.data.objects.get(DR.garment_name(spec))
    if ob is None or ob.type != 'MESH':
        return None
    if not VV.object_decoration_allowed(context, ob):
        return None                         # visible placed owners can still supply their ladders
    from . import display_state as DS
    plan = DS.snapshot(spec, ob)
    job = DS.job(ob)
    # a run going: its own folder (written before the first frame), so the lines follow the shape as it moves -- a new
    # Dress links the garment to its result only at the end, and the lines stayed where the pieces were placed until
    # then (user 2026-10-14)
    rj = DR.AC9_OT_SimDress._running_job if DR.AC9_OT_SimDress.running() else None
    if rj and rj[0] == ob.name and os.path.exists(os.path.join(rj[1], 'job.json')):
        job = rj[1]
    if job is None and not DS.result_names(ob):
        return None
    # pieces Reset on the garment and shown as placed objects again (dress.reset_to_placed, 2026-10-20): the garment's
    # seams without theirs, and the ladders of seams between two of them on the objects
    # (dress.shown_as_placed decides; with a result the garment's seams are always drawn, Fable review 2026-10-24:
    # read from the Mask, a Part to Seam that did not add it drew every piece wrapped on its cylinder)
    # (the pieces the result lacks count too: their placed objects' seams among themselves are drawn)
    visible = VV.world_names(context, spec, display=plan)
    hidden = sorted(n for n, v in plan.items() if v['source'] != 'result' or not v['seams'] or n not in visible)
    # hidden pieces: the garment is frozen (its mesh is its shape) and masked, so its own vertices are read
    # (dress.garment_co: the solver's vertices whatever modifiers a person put on the garment)
    gco = DR.garment_co(context, ob)
    n = len(gco)
    seg, col = _garment_segments(job, spec, n, store.get(context)[1], ob=ob)
    if seg is None:
        return [], []
    # garment_co has already read the coordinates. Include every vertex so local
    # edits and same-frame Live updates cannot leave the seam on an old shape.
    sample = hash(gco.tobytes())
    _garment_tris(ob, 0, n)
    key = (_jobseg['key'], _gtri['key'], bpy.context.scene.frame_current,
           np.array(ob.matrix_world).tobytes(), sample)
    if _gpos['key'] != key:
        co = gco.astype(np.float64)
        Mw = np.array(ob.matrix_world)
        co = co @ Mw[:3, :3].T + Mw[:3, 3]
        pos, cl, ids = _round_rungs(spec, co, seg, col, _jobseg['ids'], _jobseg.get('rung'), _jobseg.get('rng') or {})
        _gpos.update(key=key, pos=pos, col=[c for c in cl for _ in (0, 1)], ids=ids, mixed=None, co=co)
    if not hidden:
        return _gpos['pos'], _gpos['col']
    outlines = store.get(context)[1]
    sh, sig = PLV.shown(context, spec, outlines)
    sh = {n: v for n, v in sh.items() if n in visible}
    owners = tuple(sorted((n, v['source'], v['seams']) for n, v in plan.items() if n in visible))
    mk = (_gpos['key'], tuple(hidden), sig, owners)
    if _gpos.get('mixed') is None or _gpos['mixed'][0] != mk:
        hs = set(hidden)
        side = {sm['id']: (sm['a']['piece'], sm['b']['piece']) for sm in spec['seams']}
        P = np.asarray(_gpos['pos'], float).reshape(-1, 2, 3)
        keep = [i for i, s_ in enumerate(_gpos['ids']) if not (set(side.get(s_, ())) & hs)]
        pos = P[keep].reshape(-1, 3)
        col = [_gpos['col'][2 * i + k] for i in keep for k in (0, 1)]
        ids = [_gpos['ids'][i] for i in keep]
        enabled = {n for n, v in plan.items() if v['seams'] and v['source'] == 'placed' and n in visible}
        Pp, Cp, Ip = placed_seams(spec, outlines, enabled, sh)
        if len(Ip):
            both = [i for i, s_ in enumerate(Ip) if set(side.get(s_, ('', ''))) <= hs]
            if both:
                Pp = np.asarray(Pp, float).reshape(-1, 2, 3)[both].reshape(-1, 3)
                Cp = np.asarray(Cp, float).reshape(-1, 2, 4)[both].reshape(-1, 4)
                pos = np.vstack([pos, Pp])
                col = col + [tuple(c) for c in Cp]
                ids = ids + [Ip[i] for i in both]
        Pm, Cm, Im = _mixed_ladders(spec, outlines, plan, visible, sh, ob, _gpos['co'])
        if Im:
            pos = np.vstack([pos, Pm])
            col = col + [tuple(c) for c in Cm]
            ids = ids + Im
        _gpos['mixed'] = (mk, pos, col, ids)
    _gpos['ids_shown'] = _gpos['mixed'][3]
    return _gpos['mixed'][1], _gpos['mixed'][2]


def _round_rungs(spec, co, seg, col, ids, rung, rng, open_mm=15.0, n=10):
    """The garment's seam lines (points (2M, 3) m, color and seam id per line) with the rungs of a seam pulled round
    the body (spec seam 'path') that are still open -- longer than open_mm, as a seam turned round after the result was
    made -- drawn as arcs round its part's axis on its side (auto: away from its piece a's cloth), as the solver will
    pull them; a sewn rung is short and stays straight (user 2026-10-14: a round seam showed straight on the result)."""
    pos = co[seg.ravel()]
    if rung is None or len(rung) != len(seg) or not len(seg):
        return pos, col, ids
    paths = {sm['id']: sm for sm in spec.get('seams', []) if sm.get('path') and sm.get('enabled', True)}
    if not paths:
        return pos, col, ids
    sid = np.asarray(ids, dtype=object)
    A, B = co[seg[:, 0]], co[seg[:, 1]]
    L = np.linalg.norm(A - B, axis=1) / PV.MM
    out_p, out_c, out_i = [pos.reshape(-1, 2, 3)], list(col), list(ids)
    keep = np.ones(len(seg), bool)
    for s_, sm in paths.items():
        m = rung & (sid == s_) & (L > open_mm)
        if not m.any():
            continue
        _gn, g = SPATH.guide_of(spec, sm)
        if g is None:
            continue
        side = sm['path'].get('side', 'auto')
        if side in SPATH.DIRS:
            sd = np.asarray(SPATH.DIRS[side], float)
        else:
            lo, hi = rng.get(sm['a']['piece'], (0, 0))
            if hi <= lo:
                continue
            R = np.asarray(g['R'], float)
            pc = co[lo:hi].mean(0) / PV.MM - np.asarray(g['o'], float)
            sd = -(pc - (pc @ R[2]) * R[2])
        Q = SPATH.arc_points(g, A[m] / PV.MM, B[m] / PV.MM, sd, n=n) * PV.MM
        keep &= ~m
        out_p.append(np.stack([Q[:, :-1], Q[:, 1:]], 2).reshape(-1, 2, 3))
        c = col[int(np.flatnonzero(m)[0])]
        out_c += [c] * (int(m.sum()) * n)
        out_i += [s_] * (int(m.sum()) * n)
    if len(out_p) == 1:
        return pos, col, ids
    k = np.flatnonzero(keep)
    P = np.vstack([out_p[0][k]] + out_p[1:]).reshape(-1, 3)
    return P, [col[i] for i in k] + out_c[len(col):], [ids[i] for i in k] + out_i[len(ids):]


_notch3 = {'key': None}


def notches3d(context, spec, outlines, act, garment):
    """World (m) points of the selected seam's notches in 3D (user 2026-10-21: only the selected seam's, no switch):
    on the garment's result (garment: the 3D seams drawn are the garment's), each at the vertex nearest its pattern
    point (the vertices' flat coordinates); on a piece shown as a placed object, carried by its layout triangles;
    else on the cylinder wrap. (n, 3), maybe empty."""
    from .core import walk as W
    visible = VV.world_names(context, spec)
    pts = {}
    for k in ('a', 'b'):
        sd = act[k]
        if sd['piece'] not in visible:
            continue
        nts = W.side_notches(spec, sd, outlines)
        if nts:
            ol = outlines[sd['piece']]
            pts.setdefault(sd['piece'], []).extend(
                np.asarray(ol.at(np.array([W.notch_s(ol, n)]))).reshape(2) for _u, n in nts)
    if not pts:
        return np.zeros((0, 3))
    sh, sig = PLV.shown(context, spec, outlines)
    gk = _gpos['key'] if garment else None
    key = (store._cache['key'], act['id'], gk, sig, visible)
    if _notch3['key'] == key:
        return _notch3['P']
    from . import dress as DR
    ob = bpy.data.objects.get(DR.garment_name(spec)) if garment else None
    hidden = set(DR.hidden_pieces(ob, spec)) if ob is not None else set()
    out = []
    gco = None
    for name, Q in pts.items():
        Q = np.asarray(Q, float)
        P = None
        if ob is not None and name not in hidden and _jobseg.get('rng') and name in _jobseg['rng']:
            try:
                if gco is None:
                    z = np.load(os.path.join(DR.job_of(ob), 'scene.idx.npz'))
                    flat = z['flat']
                    gco = DR.garment_co(context, ob).astype(np.float64)
                    Mw = np.array(ob.matrix_world)
                    gco = gco @ Mw[:3, :3].T + Mw[:3, 3]
                lo, hi = _jobseg['rng'][name]
                d = ((Q[:, None, :] - flat[lo:hi][None, :, :]) ** 2).sum(2)
                P = gco[lo + d.argmin(1)]
            except Exception:
                P = None
        elif name in sh:
            P = PLV.to_world(sh[name], Q)
        else:
            pl = spec.get('placement', {}).get(name)
            P = laid_mm(spec, pl, Q, wrap_radii(spec, outlines).get(name))
            P = P * PV.MM if P is not None else None
        if P is not None:
            out.append(np.asarray(P, float).reshape(-1, 3))
    P = np.vstack(out) if out else np.zeros((0, 3))
    _notch3.update(key=key, P=P)
    return P


_iline3 = {'key': None}
_gtri = {'key': None}
ILINE_STEP_MM = 3.0


def _garment_tris(ob, lo, hi):
    """The garment mesh's triangles among the vertices lo..hi (a piece), indexed from lo."""
    me = ob.data
    owner = (ob.as_pointer(), me.as_pointer(), len(me.vertices), len(me.polygons), len(me.loops),
             _mesh_epochs.get(ob.as_pointer(), 0), _mesh_epochs.get(me.as_pointer(), 0))
    watched = _geometry_changed in bpy.app.handlers.depsgraph_update_post
    if watched and _gtri.get('owner') == owner:
        T = _gtri['T']
        if lo == 0 and hi == len(me.vertices):
            return T
        return T[(T >= lo).all(1) & (T < hi).all(1)] - lo
    connectivity, ngons = PLV._connectivity_key(me)
    co_key = None
    if ngons:
        coordinates = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get('co', coordinates)
        co_key = hash(coordinates.tobytes())
    key = (ob.as_pointer(), me.as_pointer(), connectivity, co_key)
    if _gtri['key'] != key:
        me.calc_loop_triangles()
        T = np.empty(len(me.loop_triangles) * 3, np.int32)
        me.loop_triangles.foreach_get('vertices', T)
        _gtri.update(key=key, T=T.reshape(-1, 3))
    _gtri['owner'] = owner
    T = _gtri['T']
    if lo == 0 and hi == len(me.vertices):
        return T
    T = T[(T >= lo).all(1) & (T < hi).all(1)]
    return T - lo


def iline3d(context, spec, outlines, garment):
    """World (m) points (a polyline, (n, 3)) of the internal line picked in Edit Pieces, in 3D (user 2026-10-21): on
    the garment's result inside its piece's triangles (barycentric on the flat coordinates, so the line follows the
    cloth between vertices), on a placed piece's object, else on the cylinder wrap -- as the 3D notches.
    Empty when no line is picked or its piece is not in 3D."""
    from . import draft_tool as DT
    name, lid = DT._isel['piece'], DT._isel['line']
    if not name or name not in outlines or name not in VV.world_names(context, spec):
        return np.zeros((0, 3))
    pc = next((x for x in spec['pieces'] if x['name'] == name), None)
    it = next((x for x in (pc or {}).get('internals', []) if x.get('id') == lid), None)
    if it is None or len(it.get('pts', [])) < 2:
        return np.zeros((0, 3))
    sh, sig = PLV.shown(context, spec, outlines)
    gk = _gpos['key'] if garment else None
    key = (store._cache['key'], name, lid, gk, sig)
    if _iline3['key'] == key:
        return _iline3['P']
    P0 = np.asarray(it['pts'], float)
    Q = [P0[0]]
    for a, b in zip(P0[:-1], P0[1:]):               # every ILINE_STEP_MM, so it bends with the cloth
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / ILINE_STEP_MM)))
        Q += [a + (b - a) * (k / n) for k in range(1, n + 1)]
    Q = np.asarray(Q, float)
    P = None
    from . import dress as DR
    ob = bpy.data.objects.get(DR.garment_name(spec)) if garment else None
    if (ob is not None and name not in set(DR.hidden_pieces(ob, spec)) and _jobseg.get('rng')
            and name in _jobseg['rng']):
        try:
            z = np.load(os.path.join(DR.job_of(ob), 'scene.idx.npz'))
            lo, hi = _jobseg['rng'][name]
            gco = DR.garment_co(context, ob).astype(np.float64)
            Mw = np.array(ob.matrix_world)
            X = gco[lo:hi] @ Mw[:3, :3].T + Mw[:3, 3]
            T = _garment_tris(ob, lo, hi)
            if len(T):
                P = DR._bary_map(Q, z['flat'][lo:hi].astype(np.float64), T, X)
        except Exception:
            P = None
    elif name in sh:
        P = PLV.to_world(sh[name], Q)
    else:
        pl = spec.get('placement', {}).get(name)
        P = laid_mm(spec, pl, Q, wrap_radii(spec, outlines).get(name))
        P = P * PV.MM if P is not None else None
    P = np.zeros((0, 3)) if P is None else np.asarray(P, float).reshape(-1, 3)
    _iline3.update(key=key, P=P)
    return P


def seams3d(g, gs):
    """The 3D seam lines drawn now: (points, colors, seam id per line, cache key) -- the garment's when it has a
    result (gs from garment_seams), else the placed pieces' ladders."""
    if gs is None:
        return g['p3'], g['p3c'], g['p3id'], g['k3']
    ids = _gpos.get('ids_shown') if _gpos.get('mixed') is not None and gs[0] is _gpos['mixed'][1] else _gpos.get('ids', [])
    return gs[0], gs[1], (ids if len(gs[0]) else []), (_gpos['key'], id(gs[0]))


def pick3d(context, spec, mx, my, px=8.0, current=None):
    """The seam id of the 3D seam line under region pixel (mx, my), within px pixels and not hidden behind the body
    or the cloth (the line's point is no more than 10 mm behind what the view ray hits first): the nearest, or -- the
    nearest being `current`, the selected one -- the next nearest. 3D lines lie close on screen, so the selected seam
    is often within reach and the pattern's rule (cycle whenever it is among them) picked a neighbor 2 of 8 times.
    None when there is none."""
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    outlines = store.get(context)[1]
    g = _build(context, spec, outlines)
    p3, _c, ids3, _k = seams3d(g, garment_seams(context, spec))
    if not len(p3):
        return None
    P = np.asarray(p3, float)
    H = np.c_[P, np.ones(len(P))] @ np.array(rv3d.perspective_matrix).T
    front = H[:, 3] > 1e-6
    S2 = (H[:, :2] / np.where(front, H[:, 3], 1.0)[:, None] * 0.5 + 0.5) * [region.width, region.height]
    a, b = S2[0::2], S2[1::2]
    ab = b - a
    q = np.array([mx, my], float)
    t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
    d = np.linalg.norm(a + t[:, None] * ab - q, axis=1)
    ok = front[0::2] & front[1::2] & (d <= px)
    if not ok.any():
        return None
    o = region_2d_to_origin_3d(region, rv3d, (mx, my))
    dg = context.evaluated_depsgraph_get()
    found = []
    for i in np.argsort(np.where(ok, d, np.inf))[:int(ok.sum())]:
        X = P[2 * i] + t[i] * (P[2 * i + 1] - P[2 * i])
        v = Vector(X) - o
        dist = v.length
        hit, loc = context.scene.ray_cast(dg, o, v.normalized(), distance=dist + 1.0)[:2]
        if hit and (loc - o).length < dist - 0.010:
            continue                                # behind the body or another layer of cloth
        if ids3[i] not in found:
            found.append(ids3[i])
    if not found:
        return None
    if found[0] == current and len(found) > 1:     # clicked on the selected one: the next there
        return found[1]
    return found[0]


def _lift(P, rv3d):
    """Points moved LIFT_MM towards the eye (drawn with the depth test, they sit just in front of their cloth)."""
    P = np.asarray(P, float)
    Vi = np.array(rv3d.view_matrix.inverted())
    if rv3d.is_perspective:
        d = Vi[:3, 3][None] - P
        d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    else:
        d = np.broadcast_to(Vi[:3, 2], P.shape)
    return P + d * (LIFT_MM * PV.MM)


def _build(context, spec, outlines):
    global _geo
    visible = VV.pattern_names(context, spec)
    from . import display_state as DS
    display = DS.snapshot(spec)
    world_names = VV.world_names(context, spec, display=display)
    world_visible = VV.allows_world(context, spec)
    body = getattr(context.scene.ac9_cloth_sim, 'body', None)
    guides_visible = VV.object_decoration_allowed(context, body) if body is not None else world_visible
    visibility = (visible, world_names, world_visible, guides_visible)
    if visibility not in _geo_views:
        if len(_geo_views) >= 16:
            _geo_views.clear()
        _geo_views[visibility] = {'key': None}
    _geo = _geo_views[visibility]
    props = context.scene.ac9_cloth_sim
    M = np.array(PV.layout_matrix(context))
    act = store.active_seam(context, spec)
    shown = frozenset(n for n, v in display.items() if v['source'] == 'placed')
    from . import dress as DR
    gob = bpy.data.objects.get(DR.garment_name(spec))
    on_g = DS.result_names(gob)
    more = frozenset(store.seam_more)
    key = (store._cache['key'], M.tobytes(), act['id'] if act else None, props.seam_index, props.show_guides, shown,
           on_g, more, visibility, context.scene.unit_settings.length_unit)   # the labels are in the unit
    if _geo['key'] == key:
        return _placed3d(context, spec, outlines) if world_visible else _geo
    enabled = {pc['name'] for pc in spec['pieces'] if pc.get('enabled', True)}

    def world(P2):
        P2 = np.asarray(P2, float) * PV.MM
        H = np.c_[P2, np.zeros(len(P2)), np.ones(len(P2))]
        return (H @ M.T)[:, :3]

    pos, col, apos, acol = [], [], [], []
    for i, sm in enumerate(spec['seams']):
        if sm['a']['piece'] not in enabled or sm['b']['piece'] not in enabled:
            continue
        # the color from the seam's group / id (core/seam_edit.py): the same in the seam list, and kept when other
        # seams are added or deleted
        c = (0.5, 0.5, 0.5, 0.6) if not sm.get('enabled', True) else (FLAG if sm.get('check') else SE.color(sm))
        is_act = act is not None and (sm['id'] == act['id'] or sm['id'] in more)
        # a dart switched off (marked, not sewn; user 2026-10-21): its legs dashed, 4 mm on / 4 mm off
        dashed = not sm.get('enabled', True) and sm['a']['piece'] == sm['b']['piece']
        for k in ('a', 'b'):
            if sm[k]['piece'] not in visible:
                continue
            try:
                P = world(S.side_polyline(spec, sm[k], outlines, step=2.0))
            except Exception:
                continue
            tgt_p, tgt_c = (apos, acol) if is_act else (pos, col)
            for j, (u, v) in enumerate(zip(P[:-1], P[1:])):
                if dashed and j % 4 >= 2:
                    continue
                tgt_p += [u, v]
                tgt_c += [c, c]
            for q in _arrow(P) + _tick(P):
                tgt_p.append(q)
                tgt_c.append(c)
    join, alab = [], []
    if act is not None and all(act[k]['piece'] in visible for k in ('a', 'b')):
        # the selected seam: its two ends joined by dotted lines, each side's length at its middle and
        # the difference (a / b) on the dotted line between the two sides' middles
        try:
            pa = S.side_polyline(spec, act['a'], outlines, step=2.0)
            pb = S.side_polyline(spec, act['b'], outlines, step=2.0)
            ma, mb = world(pa), world(pb)
            for u, v in ((ma[0], mb[0]), (ma[-1], mb[-1])):
                join += _dashes(u, v)
            la, lb = S.polyline_len(pa), S.polyline_len(pb)
            ca, cb = ma[len(ma) // 2], mb[len(mb) // 2]
            # A / B named on the pattern (user 2026-10-20: which side is A was not shown anywhere)
            from . import units as U                    # the scene's length unit (user 2026-11-11)
            n = lambda v, sg=False: U.num(context, v, signed=sg)
            alab = [(ca, f"A  {n(la)}"), (cb, f"B  {n(lb)}"),
                    (0.5 * (ma[0] + mb[0]), f"{n(la - lb, True)}  ({n(la)} / {n(lb)})")]
        except Exception:
            join, alab = [], []
    # the guides' named points are not drawn (user 2026-10-15: they were spread along the arms and legs by fixed
    # fractions, not from the body, and nothing snaps to them any more; the measures still read their heights)
    guides = []
    if props.show_guides and guides_visible:
        for g in spec.get('guides', {}).values():
            guides += guide_lines(g)
    placed, pcol, pnew, pncol, fills = [], [], [], [], {}
    rad = wrap_radii(spec, outlines)
    for i, pc in enumerate(spec['pieces']):
        if not world_visible:
            break
        state = display[pc['name']]
        if pc['name'] not in world_names or state['source'] != 'proxy' or not state['solid']:
            continue
        P = wrapped_outline(spec, outlines, pc['name'], radii=rad)
        if P is None:
            continue
        c = _palette(i * 3 + 1)
        seg = [p for u, v in zip(P[:-1], P[1:]) for p in (u, v)]
        placed += seg
        pcol += [c] * len(seg)
        if pc['name'] not in on_g:                 # not on the dressed garment (added after it): drawn over it too
            pnew += seg
            pncol += [c] * len(seg)
        F = wrapped_fill(spec, outlines, pc['name'], radii=rad)
        if F is not None:
            fills[pc['name']] = (F.astype(np.float32), c, pc['name'] not in on_g)
    # a piece edited symmetrically (user 2026-10-21): its center line dashed, so the switch is seen on the pattern
    from .core import draft as DR
    center = []
    for pc in spec['pieces']:
        if pc['name'] in visible and pc['name'] in enabled and DR.symmetric_edit(pc) is not None:
            c, ax = DR.self_axis(pc)                # turned with the piece (user 2026-10-27)
            s = (DR.corners_all(pc) - c) @ ax
            u, v = world([c + (s.min() - 10.0) * ax, c + (s.max() + 10.0) * ax])
            center += _dashes(u, v)
    labels = []
    for pc in spec['pieces']:
        if pc['name'] not in visible:
            continue
        B = outlines[pc['name']].B
        labels.append((world([B.mean(0)])[0], pc.get('label') or pc['name'], pc['name'] in enabled))
    _geo.update(key=key, pos=pos, col=col, apos=apos, acol=acol, join=join, labels=labels, alab=alab,
                guides=guides, placed=placed, pcol=pcol, pnew=pnew, pncol=pncol, enabled=enabled, k3=None,
                center=center, fills=fills, p3=(), p3c=(), p3id=(), world_names=world_names)
    return _placed3d(context, spec, outlines) if world_visible else _geo


def _placed3d(context, spec, outlines):
    """The placed seam ladders in _geo, made again only when the spec or a placed object (placed_view.shown) moved."""
    sh, sig = PLV.shown(context, spec, outlines)
    visible = _geo['world_names']
    sh = {n: v for n, v in sh.items() if n in visible}
    k3 = (_geo['key'], sig)
    if _geo.get('k3') != k3:
        p3, p3c, p3id = placed_seams(spec, outlines, _geo['enabled'] & visible, sh)
        _geo.update(p3=p3, p3c=p3c, p3id=p3id, k3=k3)
    return _geo


def _batch(g, name, sh, pos, col):
    """A line batch of _geo made once per its key (it was made on every redraw: 4.4 ms of the 4.7 ms the overlay took
    with a shirt's pattern shown, measured 2026-10-14)."""
    k = 'b_' + name
    if g.get(k + '_key') != g['key']:
        g[k] = batch_for_shader(sh, 'LINES', {'pos': np.asarray(pos, np.float32).reshape(-1, 3),
                                              'color': np.asarray(col, np.float32).reshape(-1, 4)})
        g[k + '_key'] = g['key']
    return g[k]


_fill = {'key': None}
FILL_FRONT, FILL_BEHIND, FILL_SEL = 0.22, 0.07, 0.45    # alpha: in front of the body, seen through it, selected


def _draw_fills(context, g, only_new=False):
    """The placed pieces filled: once seen through everything (dim), once with the depth test (in front of the body,
    brighter); the selected pieces brighter (user 2026-10-22)."""
    from . import selection as SEL
    sel = set(SEL.pieces(context))
    names = [n for n, f in g['fills'].items() if f[2] or not only_new]
    key = (g['key'], only_new, frozenset(sel & set(names)))
    sh = gpu.shader.from_builtin('SMOOTH_COLOR')
    if _fill['key'] != key:
        b = {}
        for which, a_front in (('front', FILL_FRONT), ('behind', FILL_BEHIND)):
            P, C = [], []
            for n in names:
                F, c, _ = g['fills'][n]
                a = FILL_SEL if n in sel else a_front
                if which == 'behind':
                    a = a * 0.5 if n in sel else a
                P.append(F)
                C.append(np.tile(np.array([c[0], c[1], c[2], a], np.float32), (len(F), 1)))
            b[which] = batch_for_shader(sh, 'TRIS', {'pos': np.vstack(P), 'color': np.vstack(C)}) if P else None
        _fill.update(key=key, **b)
    if _fill['behind'] is None:
        return
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set('NONE')
    _fill['behind'].draw(sh)
    gpu.state.depth_test_set('LESS_EQUAL')
    _fill['front'].draw(sh)
    gpu.state.depth_test_set('NONE')


def shows(props, kind):
    """One kind of overlay is drawn: the Overlays master switch and that kind's (properties.py)."""
    return props is not None and props.show_overlays and getattr(props, kind)


GONE = (1.0, 0.25, 0.2, 1.0)        # a piece whose flat object was deleted with Blender's X


def _gone(context):
    """[(name, world outline closed)] of the pieces whose flat objects are gone (Blender's own X / Del; the pattern
    still has them). Drawn whatever the overlay switches say (user 2026-10-27: a tester lost a piece's face and
    could not edit it, the warning was only in the Pieces list)."""
    spec, outlines = store.get(context)
    if not spec:
        return []
    from . import draft_tool as DT
    names = DT.missing_pieces(context, spec)
    if not names or not VV.allows_pattern(context):
        return []
    M = PV.layout_matrix(context)
    out = []
    for nm in names:
        ol = outlines.get(nm) if outlines else None
        if ol is not None and len(ol.B) > 2:
            W = PV.to_world(M, ol.B)
            out.append((nm, W + W[:1]))
    return out


def _draw_gone_view(context):
    gone = _gone(context)
    if not gone:
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (context.region.width, context.region.height))
    seg = [tuple(p) for _, W in gone for u, v in zip(W[:-1], W[1:]) for p in (u, v)]
    for w, c in ((7.0, (0.0, 0.0, 0.0, 0.8)), (3.5, GONE)):
        sh.uniform_float('lineWidth', w)
        batch_for_shader(sh, 'LINES', {'pos': seg, 'color': [c] * len(seg)}).draw(sh)
    gpu.state.blend_set('NONE')


def _draw_gone_px(context):
    from .i18n import tx
    gone = _gone(context)
    if not gone:
        return
    region, rv3d = context.region, context.region_data
    fid = 0
    blf.size(fid, 14)
    lines = [tx("{0} piece object(s) deleted", len(gone)), tx("Restore: the AC9 Run panel or the sidebar"),
             tx("Delete pieces with X in an AC9 tool"), tx("(Transform Pattern, Edit Pattern, ...)"), tx("X in Select Box, Move, ... deletes"), tx("only the object")]      # how to delete one (user 2026-11-12)
    for i, txt in enumerate(lines):                  # a banner at the top middle (the left has the toolbar and the view's name)
        w, _ = blf.dimensions(fid, txt)
        x, y = (region.width - w) / 2, region.height - 100 - i * 20
        blf.color(fid, 0.0, 0.0, 0.0, 0.9)
        blf.position(fid, x + 1, y - 1, 0)
        blf.draw(fid, txt)
        blf.color(fid, *GONE)
        blf.position(fid, x, y, 0)
        blf.draw(fid, txt)
    blf.size(fid, 13)
    for nm, W in gone:                               # on the piece itself
        c = sum(W[:-1], Vector()) / (len(W) - 1)
        xy = location_3d_to_region_2d(region, rv3d, c)
        if xy is None:
            continue
        txt = tx("{0}: object deleted", nm)
        w, _ = blf.dimensions(fid, txt)
        blf.color(fid, 0.0, 0.0, 0.0, 0.9)
        blf.position(fid, xy[0] - w / 2 + 1, xy[1] - 19, 0)
        blf.draw(fid, txt)
        blf.color(fid, *GONE)
        blf.position(fid, xy[0] - w / 2, xy[1] - 18, 0)
        blf.draw(fid, txt)


def _draw_frames_px(context):
    """A banner while the garment is in Sculpt / Edit Mode with its frames playing: the hand edits do not show
    (REVIEW_fable_edit_frame_ux_2026-11-02 B: told, the frames not switched off by itself)."""
    from . import dress as D
    from .i18n import tx
    if not D.frames_hide_edits(context):
        return
    region = context.region
    fid = 0
    blf.size(fid, 14)
    lines = [tx("Frames are playing: hand edits will not show"),
             tx("Press Fix by Hand in AC9 Run first (Ctrl+Z puts the frames back)")]
    for i, txt in enumerate(lines):
        w, _ = blf.dimensions(fid, txt)
        x, y = (region.width - w) / 2, region.height - 140 - i * 20
        blf.color(fid, 0.0, 0.0, 0.0, 0.9)
        blf.position(fid, x + 1, y - 1, 0)
        blf.draw(fid, txt)
        blf.color(fid, *GONE)
        blf.position(fid, x, y, 0)
        blf.draw(fid, txt)


def _draw_view():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if props is not None:
        _draw_gone_view(context)
    if not (shows(props, 'show_overlay') or shows(props, 'show_guides') or shows(props, 'show_lengths')):
        return
    seams = props.show_overlay
    from . import guide_edit as GE                  # (guide_edit imports this module)
    spec, outlines = store.get(context)
    if not spec or PV.layout_empty(context) is None:
        return
    g = _build(context, spec, outlines)
    world_visible = VV.allows_world(context, spec)
    region = context.region
    vs = (region.width, region.height)
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    sh = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', vs)
    if g['pos'] and seams:
        sh.uniform_float('lineWidth', 3.0)
        _batch(g, 'pos', sh, g['pos'], g['col']).draw(sh)
    if g.get('center'):
        sh.uniform_float('lineWidth', 1.5)
        _batch(g, 'center', sh, g['center'], [(1.0, 0.8, 0.3, 0.9)] * len(g['center'])).draw(sh)
    if g['apos'] and seams:
        # the selected seam: white under, its own color on top (both sides read as one seam)
        sh.uniform_float('lineWidth', 8.0)
        _batch(g, 'apos_w', sh, g['apos'], [ACTIVE] * len(g['apos'])).draw(sh)
        sh.uniform_float('lineWidth', 4.0)
        _batch(g, 'apos', sh, g['apos'], g['acol']).draw(sh)
    if world_visible and GE.editing() and props.show_guides:
        # Edit Guides: drawn from the objects as they are moved, brighter
        owners = {n: ob for n, ob in GE.objects().items() if VV.object_decoration_allowed(context, ob)}
        key = tuple((n, tuple(map(tuple, ob.matrix_world)), ob.get('ac9_g0')) for n, ob in sorted(owners.items()))
        if _gedit['key'] != key:
            lv = GE.live()
            L = [q for name, gg in lv.items() if name in owners for q in guide_lines(gg)]
            _gedit.update(key=key, lines=batch_for_shader(sh, 'LINES', {'pos': L, 'color': [(0.45, 0.75, 1.0, 0.9)] * len(L)})
                          if L else None)
        if _gedit['lines'] is not None:
            sh.uniform_float('lineWidth', 2.0)
            _gedit['lines'].draw(sh)
    elif g['guides']:
        sh.uniform_float('lineWidth', 1.0)
        _batch(g, 'guides', sh, g['guides'], [(0.7, 0.8, 1.0, 0.35)] * len(g['guides'])).draw(sh)
    gs = garment_seams(context, spec) if seams and world_visible else None
    if g.get('fills') and seams:
        _draw_fills(context, g, only_new=gs is not None)
        sh.bind()
    if g['placed'] and gs is None and seams:              # once a result is shown the placed outlines only cover it
        sh.uniform_float('lineWidth', 2.5)
        _batch(g, 'placed', sh, g['placed'], g['pcol']).draw(sh)
    elif g.get('pnew') and seams:
        # pieces placed that the dressed garment does not have (drawn after it, user 2026-10-21: a new rectangle
        # placed on the torso was drawn nowhere)
        sh.uniform_float('lineWidth', 2.5)
        _batch(g, 'pnew', sh, g['pnew'], g['pncol']).draw(sh)
    p3, c3, ids3, k3 = seams3d(g, gs) if seams and world_visible else ((), (), (), None)
    if len(p3):
        # the 3D seams: hidden behind the body and the cloth, lifted towards the eye off the cloth they lie on
        gpu.state.depth_test_set('LESS_EQUAL')
        sh.uniform_float('lineWidth', 2.0)
        rv = context.region_data
        act = store.active_seam(context, spec)
        bkey = (id(p3), k3, np.array(rv.view_matrix).tobytes(), rv.is_perspective, act['id'] if act else None)
        if _batch3['key'] != bkey:
            L = _lift(p3, rv).astype(np.float32)
            on = np.repeat(np.asarray(ids3) == (act['id'] if act else None), 2) if act else np.zeros(len(L), bool)
            ca = [c for c, o in zip(c3, on) if o]
            _batch3.update(key=bkey, batch=batch_for_shader(sh, 'LINES', {'pos': L, 'color': c3}),
                           act=batch_for_shader(sh, 'LINES', {'pos': L[on], 'color': ca}) if on.any() else None,
                           actw=batch_for_shader(sh, 'LINES', {'pos': L[on], 'color': [ACTIVE] * int(on.sum())})
                           if on.any() else None)
        _batch3['batch'].draw(sh)
        gpu.state.depth_test_set('NONE')
        if _batch3['act'] is not None:
            # the seam selected on the pattern, as there (white under, its color on top), seen through the body and
            # the cloth so it is found from any side
            sh.uniform_float('lineWidth', 6.0)
            _batch3['actw'].draw(sh)
            sh.uniform_float('lineWidth', 3.0)
            _batch3['act'].draw(sh)
    act = store.active_seam(context, spec) if seams and world_visible else None
    if act is not None:
        try:
            N3 = notches3d(context, spec, outlines, act, gs is not None)
        except Exception:
            N3 = ()
        if len(N3):
            # the selected seam's notches in 3D: a dot, dark under white, seen through the body like the seam
            gpu.state.depth_test_set('NONE')
            psh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
            bt = batch_for_shader(psh, 'POINTS', {'pos': np.asarray(N3, np.float32)})
            psh.bind()
            for size, c in ((11.0, (0.0, 0.0, 0.0, 0.9)), (7.0, (1.0, 1.0, 1.0, 1.0))):
                gpu.state.point_size_set(size)
                psh.uniform_float('color', c)
                bt.draw(psh)
            gpu.state.point_size_set(1.0)
            sh.bind()
    if seams and world_visible:
        try:
            L3 = iline3d(context, spec, outlines, gs is not None)
        except Exception:
            L3 = ()
        if len(L3) > 1:
            # the internal line picked on the pattern, in 3D: dark under its orange, seen through like the seam
            from . import draft_tool as DT
            gpu.state.depth_test_set('NONE')
            Lp = _lift(L3, context.region_data).astype(np.float32)
            seg = np.stack([Lp[:-1], Lp[1:]], 1).reshape(-1, 3)
            for w, c in ((6.0, (0.0, 0.0, 0.0, 0.8)), (3.0, DT.ISEL)):
                sh.uniform_float('lineWidth', w)
                batch_for_shader(sh, 'LINES', {'pos': seg, 'color': [c] * len(seg)}).draw(sh)
    if g['join'] and props.show_lengths:
        sh.uniform_float('lineWidth', 2.0)
        _batch(g, 'join', sh, g['join'], [(1, 1, 1, 0.85)] * len(g['join'])).draw(sh)
    gpu.state.blend_set('NONE')


def _draw_px():
    context = bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None)
    if props is not None:
        _draw_gone_px(context)
        _draw_frames_px(context)
    if not (shows(props, 'show_lengths') or shows(props, 'show_names') or shows(props, 'show_overlay')):
        return
    spec, outlines = store.get(context)
    if not spec:
        return
    if not VV.pattern_names(context, spec):
        return                             # no flat labels; avoid rebuilding the 3D owner plan a second time
    _build(context, spec, outlines)          # select this viewport's cache; another viewport may have drawn last
    region, rv3d = context.region, context.region_data
    fid = 0
    blf.size(fid, 13)
    labs = _geo.get('alab', [])
    if not props.show_lengths:                      # the lengths off: the sides still read A and B
        labs = [(p, t.split()[0]) for p, t in labs[:2]] if props.show_overlay else []
    for p, txt in labs:                             # the selected seam's sides and lengths
        xy = location_3d_to_region_2d(region, rv3d, p)
        if xy is None:
            continue
        w, h = blf.dimensions(fid, txt)
        blf.color(fid, 0.0, 0.0, 0.0, 0.8)
        blf.position(fid, xy[0] - w / 2 + 1, xy[1] + 6 - 1, 0)
        blf.draw(fid, txt)
        blf.color(fid, 1.0, 1.0, 1.0, 1.0)
        blf.position(fid, xy[0] - w / 2, xy[1] + 6, 0)
        blf.draw(fid, txt)
    if not props.show_names:
        return
    blf.size(fid, 12)
    from .core import roles as RO
    from .i18n import tx
    starts = {pc['name']: RO.start_of(pc) for pc in spec.get('pieces', [])}
    for p, name, on in _geo.get('labels', []):
        xy = location_3d_to_region_2d(region, rv3d, p)
        if xy is None:
            continue
        w, _ = blf.dimensions(fid, name)
        blf.position(fid, xy[0] - w / 2, xy[1], 0)
        blf.color(fid, *((1, 1, 1, 0.9) if on else (0.6, 0.6, 0.6, 0.6)))
        blf.draw(fid, name)
        st_ = starts.get(name, 'placed')
        if st_ != 'placed' and on:                  # the piece's Start under its name (user 2026-11-02)
            t = tx(RO.START_EN[st_])
            w, _ = blf.dimensions(fid, t)
            blf.position(fid, xy[0] - w / 2, xy[1] - 14, 0)
            blf.color(fid, *((0.45, 0.85, 1.0, 0.9) if st_ == 'edge' else (1.0, 0.85, 0.35, 0.9)))
            blf.draw(fid, t)


def invalidate():
    _geo['key'] = None
    _geo_views.clear()
    _batch3['key'] = None
    _pflat['key'] = None
    _plad.clear()
    _gpos['key'] = None
    _gtri['key'] = None
    _gtri['owner'] = None
    _jobseg['key'] = None
    _scene_index_cache['key'] = None


def register():
    handlers = bpy.app.handlers.depsgraph_update_post
    for old in [f for f in handlers if getattr(f, '__module__', '') == __name__]:
        handlers.remove(old)
    handlers.append(_geometry_changed)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_px, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    handlers = bpy.app.handlers.depsgraph_update_post
    for old in [f for f in handlers if getattr(f, '__module__', '') == __name__]:
        handlers.remove(old)
    for h in _handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except Exception:
            pass
    _handles.clear()
    invalidate()
