# SPDX-License-Identifier: GPL-3.0-or-later
"""The picked internal line's two sides, filled on the flat pattern and on the placed shape (user 2026-10-25, after
"the A / B of Fold and Layers, I can't tell what they are"): A orange, B blue; the side that moves (Moves) darker;
the side taken out by Layers (Keep A / Keep B) hatched; the letters A and B at the middle of their sides, away
from the line's dimension boxes.

The sides are the solver's own (place_fold.split_ab on the garment's mesh, as dress.one_layer_rows reads them), so
what is filled A is what Keep A keeps -- on a mirrored piece of a pair too, where "left of the line" is not what
split_ab calls A. Only for a line running from the outline to the outline (place_fold.cuts_apart): a line that
stops inside the piece has no two sides (a tuck's fold line; its angle still works while simulating).

Worked out in a timer when the line or the pattern changes (the mesh is kept, dress._build_cached), never while
drawing: drawing reads the kept triangles and only the fold's side and layers from the spec."""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import pattern_view as PV
from . import store
from .core import spec as S

A_COL = (1.0, 0.55, 0.15)
B_COL = (0.25, 0.55, 1.0)
MOVE_ALPHA = 0.45           # the side Moves turns
STAY_ALPHA = 0.18
HATCH_MM = 6.0              # the side taken out (Layers): 45 degree lines this far apart on the flat pattern
HATCH_ALPHA = 0.9
TEXT_PX = 20
LETTER_GAP_PX = 22         # the two letters closer than this on screen are set this far apart

_data = {}                  # (piece, line) -> {'skey', 'bkey', 'n', 'V', 'tri': [A, B], 'hatch': [A, B], 'lab': [A, B]}
_pending = set()
_world = {}                 # (piece, line) -> (key, [tris A, tris B], [hatch A, hatch B]) on the flat pattern


def _skey(context):
    props = context.scene.ac9_cloth_sim
    return store._cache['key'], float(props.mesh_size), getattr(props, 'preset', '')


def _hatch(V, T):
    """45 degree segments (k, 2, 2) mm HATCH_MM apart, clipped to the triangles T of V."""
    out = []
    f = (V[:, 0] + V[:, 1]) / np.sqrt(2.0)
    for t in T:
        p, fv = V[t], f[t]
        for c in np.arange(np.ceil(fv.min() / HATCH_MM), np.floor(fv.max() / HATCH_MM) + 1) * HATCH_MM:
            q = []
            for a, b in ((0, 1), (1, 2), (2, 0)):
                if (fv[a] - c) * (fv[b] - c) < 0 or (fv[a] == c and fv[b] != c):
                    s = (c - fv[a]) / (fv[b] - fv[a])
                    q.append(p[a] + (p[b] - p[a]) * s)
            if len(q) >= 2:
                out.append((q[0], q[1]))
    return np.asarray(out, float).reshape(-1, 2, 2)


def _along(L, f):
    """The point at the share f of the polyline L's length."""
    seg = np.linalg.norm(np.diff(L, axis=0), axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    j = int(min(np.searchsorted(s, s[-1] * f, side='right') - 1, len(seg) - 1))
    return L[j] + (L[j + 1] - L[j]) * ((s[-1] * f - s[j]) / max(seg[j], 1e-9))


def job_spec(context, spec):
    """The spec as a job meshes it (the settings set's spec keys laid over)."""
    from .core import presets
    props = context.scene.ac9_cloth_sim
    cs = presets.resolve(getattr(props, 'preset', ''), props, environ=None if bpy.app.background else {})
    return presets.spec_for_job(spec, cs)


def piece_mesh(context, js, piece):
    """(pat, names, i, T) of the garment's mesh as Simulate makes it (kept: dress._build_cached), T piece i's
    triangles alone (the others are not triangulated), or None when the piece is not meshed."""
    from . import dress as D
    pat, _rep = D._build_cached(js, context.scene.ac9_cloth_sim.mesh_size)
    names = [str(n) for n in pat['names']]
    if piece not in names:
        return None
    i = names.index(piece)
    one = {'names': [piece], 'V0': pat[f"V{i}"], 'nb': [pat['nb'][i]], 'C0': pat[f"C{i}"]}
    if f"H{i}" in pat:                                      # its holes too (core/spec.holes)
        one.update(H0=pat[f"H{i}"], Hn0=pat[f"Hn{i}"])
    tl, bad = D.triangulate(one)
    if bad:
        return None
    return pat, names, i, np.asarray(tl[0])


def compute(context, spec, piece, line):
    """The kept sides of piece's line (None: no two sides -- not meshed, not a fold line, or it stops inside)."""
    from . import dress as D
    from . import place_fold as PF
    props = context.scene.ac9_cloth_sim
    js = job_spec(context, spec)
    pc = S.piece_index(js).get(piece, {})
    il = next((l for l in pc.get('internals', []) if str(l.get('id')) == str(line)), None)
    L = np.asarray((il or {}).get('pts', ()), float)
    bkey = (D._build_key(js, props.mesh_size), piece, str(line), L.round(4).tobytes())
    old = _data.get((piece, line))
    if old is not None and old.get('bkey') == bkey:
        return old
    out = {'bkey': bkey, 'n': 0}
    try:
        pm = piece_mesh(context, js, piece)
        if pm is None or len(L) < 2:
            return out
        pat, names, i, T = pm
        fid = D._fold_fid(pat, piece, str(line))
        if fid is None:
            return out
        V = np.asarray(pat[f"V{i}"], float)
        ab = PF.split_ab(pat, i, fid, T, L)
        if ab is None:
            return out
        roll, sides, a, _ = ab
        if not PF.cuts_apart(len(V), T, roll, sides[0], sides[1]):
            out['partial'] = True
            return out
    except Exception:
        return out
    ia = 0 if a is None else a
    tri, hatch, lab = [], [], []
    for k in (ia, 1 - ia):                      # A, then B
        own = np.zeros(len(V), bool)
        own[sides[k]] = True
        on = own.copy()
        on[roll] = True
        Tk = T[on[T].all(1) & own[T].any(1)]
        tri.append(Tk)
        hatch.append(_hatch(V, Tk))
        if len(Tk):
            P = V[Tk]
            ar = 0.5 * np.abs((P[:, 1, 0] - P[:, 0, 0]) * (P[:, 2, 1] - P[:, 0, 1])
                              - (P[:, 1, 1] - P[:, 0, 1]) * (P[:, 2, 0] - P[:, 0, 0]))
            cen = P.mean(1)
            c = (cen * ar[:, None]).sum(0) / max(ar.sum(), 1e-12)
            # a quarter of the way along the line from its middle: the line's length and seams are shown at its
            # middle (a placket's two sides are long strips, their middles beside the line's)
            c = c + (_along(L, 0.25) - _along(L, 0.5))
            lab.append(Tk[int(np.linalg.norm(cen - c, axis=1).argmin())])     # inside the side
        else:
            lab.append(None)
    out.update(n=len(V), V=V, tri=tri, hatch=hatch, lab=lab, arrow=[_arrow(V, t, L) if t is not None else None
                                                                     for t in lab])
    return out


ARROW_MM = 18.0             # the arrow over the line: this far each side of it on the pattern
ARROW_HEAD_MM = 5.0


def _arrow(V, t, L):
    """The arrow (k, 2) mm on the pattern from the side whose label triangle is t over the line to the other side,
    bowed along the line, and its head: the side that turns, where it goes (user 2026-10-25)."""
    p = V[t].mean(0)
    a, b = L[:-1], L[1:]
    ab = b - a
    tt = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
    q = a + ab * tt[:, None]
    j = int(np.linalg.norm(q - p, axis=1).argmin())
    foot, along = q[j], ab[j] / max(np.linalg.norm(ab[j]), 1e-12)
    away = p - foot
    away = away / max(np.linalg.norm(away), 1e-12)
    p0, p1, c = foot + away * ARROW_MM, foot - away * ARROW_MM, foot + along * ARROW_MM * 0.8
    s = np.linspace(0.0, 1.0, 13)[:, None]
    arc = (1 - s) ** 2 * p0 + 2 * (1 - s) * s * c + s ** 2 * p1
    d = arc[-1] - arc[-2]
    d = d / max(np.linalg.norm(d), 1e-12)
    n = np.array([-d[1], d[0]])
    head = [arc[-1] - d * ARROW_HEAD_MM + n * ARROW_HEAD_MM * 0.5, arc[-1], arc[-1] - d * ARROW_HEAD_MM - n * ARROW_HEAD_MM * 0.5]
    return np.vstack([arc, head])


def _run(piece, line):
    _pending.discard((piece, line))
    context = bpy.context
    try:
        spec = store.get(context)[0]
        if spec is not None:
            d = compute(context, spec, piece, line)
            d['skey'] = _skey(context)
            _data[(piece, line)] = d
            _world.pop((piece, line), None)
    except Exception:
        pass
    for w in getattr(context.window_manager, 'windows', ()):
        for area in w.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return None


def _entry(context, piece, line):
    d = _data.get((piece, line))
    if (d is None or d.get('skey') != _skey(context)) and (piece, line) not in _pending:
        _pending.add((piece, line))
        bpy.app.timers.register(lambda: _run(piece, line), first_interval=0.0)
    return d


def get(context, piece, line):
    """The kept sides (as last worked out: a newer one is asked for when the spec changed), or None."""
    d = _entry(context, piece, line)
    return d if d is not None and d.get('n') else None


def partial(context, piece, line):
    """True when the line stops inside the piece (no two sides: Moves and Layers mean nothing there), as last
    worked out; False until known."""
    d = _entry(context, piece, line)
    return bool(d is not None and d.get('partial'))


def _fold(spec, piece, line):
    return next((f for f in spec.get('folds', []) if f.get('piece') == piece and f.get('line') == line), {})


def folded(spec, piece, line):
    """The line has a fold angle. At 0 nothing is filled (user 2026-10-25: Moves and Layers are hidden then too)."""
    try:
        return abs(float(_fold(spec, piece, line).get('deg') or 0.0)) > 1e-6
    except (TypeError, ValueError):
        return False


def looks(spec, piece, line):
    """(fill alpha A, fill alpha B, hatched index or None): the side Moves turns darker, the side Layers takes out
    hatched."""
    f = _fold(spec, piece, line)
    folded_ = folded(spec, piece, line)
    mv = (None if f.get('side') == 'NONE' else f.get('side', 'A')) if folded_ else None
    al = [MOVE_ALPHA if mv == ab else STAY_ALPHA for ab in 'AB']
    keep = f.get('layers') if folded_ else None
    return al[0], al[1], ({'A': 1, 'B': 0}.get(keep))


def _to3(Mn, P):
    P = np.asarray(P, float).reshape(-1, 2) * PV.MM
    return (np.c_[P, np.zeros(len(P)), np.ones(len(P))] @ Mn.T)[:, :3]


def _batches(sh_t, sh_l, tris, hatch, al, hidx, vs):
    for k, col in enumerate((A_COL, B_COL)):
        if len(tris[k]):
            sh_t.bind()
            sh_t.uniform_float('color', (*col, al[k]))
            batch_for_shader(sh_t, 'TRIS', {'pos': np.ascontiguousarray(tris[k], np.float32)}).draw(sh_t)
    if hidx is not None and len(hatch[hidx]):
        sh_l.bind()
        sh_l.uniform_float('viewportSize', vs)
        sh_l.uniform_float('lineWidth', 1.5)
        sh_l.uniform_float('color', (*(A_COL, B_COL)[hidx], HATCH_ALPHA))
        batch_for_shader(sh_l, 'LINES', {'pos': np.ascontiguousarray(hatch[hidx], np.float32)}).draw(sh_l)


def placed_positions(spec, piece, n, context=None):
    """World positions (n, 3) of the piece's placed object, its vertices one to one with the mesh, or None."""
    from . import place_fold as PF
    ob = PF._object(spec, piece)
    from . import view_visibility as VV
    allowed = (VV.object_decoration_allowed(context, ob) if context is not None
               else ob is not None and ob.visible_get())
    if ob is None or ob.type != 'MESH' or len(ob.data.vertices) != n or not allowed:
        return None
    co = np.empty(n * 3)
    ob.data.vertices.foreach_get('co', co)
    Mw = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ Mw[:3, :3].T + Mw[:3, 3]


def draw_view(context, spec, piece, line):
    """The fills and the hatching (POST_VIEW): on the flat pattern over everything, on the placed shape lifted
    towards the eye and depth tested (piece_look's tints)."""
    from . import piece_look as LK
    from . import view_visibility as VV
    flat_visible = piece in VV.pattern_names(context, spec)
    world_visible = piece in VV.world_names(context, spec)
    if not flat_visible and not world_visible:
        return
    if not folded(spec, piece, line):
        return
    d = get(context, piece, line)
    if d is None or PV.layout_empty(context) is None:
        return
    rv3d = context.region_data
    vs = (context.region.width, context.region.height)
    al_a, al_b, hidx = looks(spec, piece, line)
    al = (al_a, al_b)
    Mn = np.array(PV.layout_matrix(context))
    wk = (Mn.tobytes(), d['skey'])
    w = _world.get((piece, line))
    if w is None or w[0] != wk:
        V3 = _to3(Mn, d['V'])
        w = _world[(piece, line)] = (wk, [V3[t].reshape(-1, 3) for t in d['tri']],
                                     [_to3(Mn, h.reshape(-1, 2)) for h in d['hatch']])
    sh_t = gpu.shader.from_builtin('UNIFORM_COLOR')
    sh_l = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    if flat_visible:
        _batches(sh_t, sh_l, w[1], w[2], al, hidx, vs)
    # the side that turns, an arrow over the line on the pattern (the placed shape shows it folded)
    k = 0 if al_a == MOVE_ALPHA else (1 if al_b == MOVE_ALPHA else None)
    arw = (d.get('arrow') or [None, None])[k] if k is not None else None
    if arw is not None and flat_visible:
        A3 = _to3(Mn, arw)
        n_arc = len(arw) - 3
        pairs = [p for u, v in zip(A3[:n_arc - 1], A3[1:n_arc]) for p in (u, v)]
        pairs += [A3[n_arc], A3[n_arc + 1], A3[n_arc + 1], A3[n_arc + 2]]
        sh_l.bind()
        sh_l.uniform_float('viewportSize', vs)
        sh_l.uniform_float('lineWidth', 2.5)
        sh_l.uniform_float('color', (1.0, 1.0, 1.0, 0.95))
        batch_for_shader(sh_l, 'LINES', {'pos': np.ascontiguousarray(pairs, np.float32)}).draw(sh_l)
    X = placed_positions(spec, piece, d['n'], context=context) if world_visible else None
    if X is not None:
        gpu.state.depth_test_set('LESS_EQUAL')
        tris = [LK._lift(X[t].reshape(-1, 3), rv3d) for t in d['tri']]
        hatch = [np.zeros((0, 3))] * 2
        if hidx is not None and len(d['hatch'][hidx]):
            hatch[hidx] = LK._lift(_hatch3(d['V'], X, d['tri'][hidx], d['hatch'][hidx]), rv3d)
        _batches(sh_t, sh_l, tris, hatch, al, hidx, vs)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def _hatch3(V, X, T, H):
    """The flat hatching H (k, 2, 2) carried onto the placed positions X through the triangle each end lies in."""
    P = H.reshape(-1, 2)
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    out = np.empty((len(P), 3))
    for j, p in enumerate(P):
        v0, v1, v2 = B - A, C - A, p[None] - A
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        den = np.maximum(d00 * d11 - d01 * d01, 1e-12)
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        u = 1.0 - v - w
        k = int(np.minimum(np.minimum(u, v), w).argmax())
        out[j] = u[k] * X[T[k, 0]] + v[k] * X[T[k, 1]] + w[k] * X[T[k, 2]]
    return out


def draw_px(context, spec, piece, line):
    """The letters A and B at the middle of their sides, on the flat pattern and on the placed shape (POST_PIXEL)."""
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector
    from . import view_visibility as VV
    flat_visible = piece in VV.pattern_names(context, spec)
    world_visible = piece in VV.world_names(context, spec)
    if not flat_visible and not world_visible:
        return
    if not folded(spec, piece, line):
        return
    d = get(context, piece, line)
    region, rv3d = context.region, context.region_data
    if d is None or region is None or rv3d is None or PV.layout_empty(context) is None:
        return
    al_a, al_b, _h = looks(spec, piece, line)
    Mn = np.array(PV.layout_matrix(context))
    X = placed_positions(spec, piece, d['n'], context=context) if world_visible else None
    fid = 0
    blf.size(fid, TEXT_PX)
    spots = [[], []]                            # per surface (pattern, placed): [(letter, k, screen point)]
    for k, ab in enumerate('AB'):
        t = d['lab'][k]
        if t is None:
            continue
        pts = [(0, _to3(Mn, d['V'][t].mean(0))[0])] if flat_visible else []
        if X is not None:
            pts.append((1, X[t].mean(0)))
        for j, p3 in pts:
            p = location_3d_to_region_2d(region, rv3d, Vector(p3))
            if p is not None:
                spots[j].append((ab, k, p))
    for sp in spots:
        if len(sp) == 2 and (sp[0][2] - sp[1][2]).length < LETTER_GAP_PX:
            # one side folded over the other (the placed shape at 149 degrees): side by side, not on top
            sp = [(ab, k, p + Vector(((k - 0.5) * LETTER_GAP_PX, 0.0))) for ab, k, p in sp]
        items = []
        for ab, k, p in sp:
            w, h = blf.dimensions(fid, ab)
            items.append((p.x - w / 2.0, p.y - h / 2.0, ab,
                          (1.0, 1.0, 1.0, 1.0 if (al_a, al_b)[k] == MOVE_ALPHA else 0.75)))
        from . import text_band
        text_band.draw(fid, items)


def invalidate():
    _data.clear()
    _world.clear()
