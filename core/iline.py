# SPDX-License-Identifier: GPL-3.0-or-later
"""The drafting shape of an internal line: vertices and curved spans (user 2026-10-28).

An internal line's 'pts' stays the line everything else reads (the mesh, seams, folds, snapping, the 3D). A line
shaped here may also hold 'nodes' (indices into pts: the vertices one drags) and 'ctrl' (one per span between two
vertices: None for straight, or [h0, h1], its Bezier handles); pts is then sampled from them as a curved edge is.
An explicitly authored sampled_spans line stores its exact polyline in pts and only its logical corners in nodes;
None controls on that line retain the raw span rather than substituting a chord. Whole affine transforms map every
sample; endpoint edits carry each raw span by its endpoints' similarity. Without metadata every point is a vertex
and every span straight. An edit that moves pts without them (slash & spread, a
cut) leaves them stale: shape() then reads every point as a vertex, and the next edit here writes them afresh.

Each edit gives the line's mirror lines (the twin's, across a piece's center line: draft.mirror_lines) the mirror
image; a line that is its own mirror image (across or on the center line) is edited at both mirrored places."""
import numpy as np
from ._i18n import rpt

from . import draft as D

TOL = 0.05         # mm: pts sampled again from nodes and ctrl must match the stored pts within this
DENSE = 12         # a line without vertices of its own and more points than this (a curve read from a BOM, an old
                   # ellipse) offers no vertices to drag: K or C first gives it vertices and handles
NEAR = 1.0         # mm: a vertex this close to the mirror image of another is its partner
MIN_GAP = 0.5      # mm: a new vertex at least this far from the span's ends


def _spans(n, closed):
    return n if closed else n - 1


def sample(N, C, closed):
    """(pts (m, 2), indices of the vertices in pts) of the line through vertices N with span handles C."""
    N = np.asarray(N, float)
    n = len(N)
    out, idx = [], []
    for s in range(_spans(n, closed)):
        a, b = N[s], N[(s + 1) % n]
        idx.append(len(out))
        seg = D.bezier_pts(a, C[s][0], C[s][1], b) if C[s] is not None else np.array([a, b])
        out.extend(seg[:-1])
    if closed:
        out.append(N[0])
    else:
        idx.append(len(out))
        out.append(N[-1])
    return np.asarray(out, float), idx


def shape(it):
    """(vertices (n, 2), span handles [None | (2, 2)], closed, own) of internal line it; own: the line holds its
    vertices (False: every point read as one)."""
    P = np.asarray(it['pts'], float)
    closed = bool(it.get('closed'))
    nodes, ctrl = it.get('nodes'), it.get('ctrl')
    if nodes is not None and ctrl is not None:
        try:
            N = P[np.asarray(nodes, int)]
            C = [None if c is None else np.asarray(c, float).reshape(2, 2) for c in ctrl]
            if (it.get('sampled_spans') is True and len(N) >= (3 if closed else 2)
                    and len(C) == _spans(len(N), closed) and int(nodes[0]) == 0
                    and all(int(a) < int(b) for a,b in zip(nodes,nodes[1:]))
                    and (closed or int(nodes[-1]) == len(P)-1)
                    and (not closed or (int(nodes[-1]) < len(P)-1 and np.linalg.norm(P[0]-P[-1]) < 1e-6))):
                valid=True
                for k,c in enumerate(C):
                    if c is None:continue
                    raw=P[int(nodes[k]):(int(nodes[k+1]) if k+1<len(nodes) else len(P)-1)+1]
                    q=D.bezier_pts(N[k],c[0],c[1],N[(k+1)%len(N)])
                    if len(q)!=len(raw) or np.abs(q-raw).max()>=TOL:valid=False;break
                if valid:return N.copy(),C,closed,True
            if len(C) == _spans(len(N), closed):
                Q, idx = sample(N, C, closed)
                if len(Q) == len(P) and list(idx) == [int(i) for i in nodes] and np.abs(Q - P).max() < TOL:
                    return N.copy(), C, closed, True
        except (IndexError, TypeError, ValueError):
            pass
    N = P[:-1] if closed and len(P) > 1 and np.linalg.norm(P[0] - P[-1]) < 1e-6 else P
    return N.copy(), [None] * _spans(len(N), closed), closed, False


def editable(it):
    """The line offers its vertices to drag (its own, or a hand-drawn polyline of few points)."""
    N, _, _, own = shape(it)
    return own or it.get('seam_authored_vertices') is True or len(N) <= DENSE


def span_points(it):
    """Exact stored points per logical span, including both endpoints."""
    N,C,closed,own=shape(it)
    if own:
        P=np.asarray(it['pts'],float);ids=list(it['nodes'])+[len(P)-1]
        return [P[int(ids[k]):int(ids[k+1])+1].copy() for k in range(len(C))]
    return [np.array([N[k],N[(k+1)%len(N)]]) for k in range(len(C))]


def set_spans(it, parts, controls):
    """Author logical corners without promoting the raw drawing samples to vertices."""
    parts=[np.asarray(D._r(p),float) for p in parts]
    controls=[None if c is None else np.asarray(D._r(c),float) for c in controls]
    out=[];ids=[]
    for part,c in zip(parts,controls):
        if c is not None:part=D.bezier_pts(part[0],c[0],c[1],part[-1])
        ids.append(len(out));out.extend(part[:-1])
    out.append(np.asarray(parts[-1],float)[-1])
    if not it.get('closed'):ids.append(len(out)-1)
    it.update(pts=D._r(out),nodes=ids,ctrl=[None if c is None else D._r(c) for c in controls],sampled_spans=True)
    it.pop('seam_authored_vertices',None)


def _project(P,q):
    P=np.asarray(P,float);q=np.asarray(q,float);v=np.diff(P,axis=0)
    lengths=np.linalg.norm(v,axis=1);cum=np.r_[0,np.cumsum(lengths)]
    t=np.clip(np.einsum('ij,ij->i',q-P[:-1],v)/np.maximum(lengths**2,1e-20),0,1)
    dist=np.linalg.norm(P[:-1]+v*t[:,None]-q,axis=1);k=int(dist.argmin())
    return float(cum[k]+t[k]*lengths[k]),float(dist[k])


def _section(P,a,b):
    P=np.asarray(P,float);cum=np.r_[0,np.cumsum(np.linalg.norm(np.diff(P,axis=0),axis=1))]
    def at(t):return np.array([np.interp(t,cum,P[:,j]) for j in range(2)])
    return np.vstack([at(a),P[(cum>a+1e-8)&(cum<b-1e-8)],at(b)])


def _raw_edit(it,N,C,indices):
    old,oldc,closed,_=shape(it);parts=span_points(it);n=len(old)
    order=list(range(n)) if indices is None and len(N)==n else list(indices or [None]*len(N))
    # A new point belongs to its actual raw span; existing points use identity,
    # so deliberately detached coincident points are never matched by proximity.
    locations=[]
    for k,q in enumerate(N):
        if order[k] is not None:
            locations.append((int(order[k]),0.));continue
        hits=[_project(p,q) for p in parts];j=min(range(len(hits)),key=lambda j:hits[j][1])
        locations.append((j,hits[j][0]))
    result=[]
    for k,c in enumerate(C):
        j=(k+1)%len(N);a,sa=locations[k];b,sb=locations[j]
        if c is not None:
            result.append(D.bezier_pts(N[k],c[0],c[1],N[j]));continue
        if oldc[a] is None and (b==a or (b==(a+1)%n and sb==0)):
            end=sb if b==a else float(np.linalg.norm(np.diff(parts[a],axis=0),axis=1).sum())
            if end>sa+1e-10:
                raw=_section(parts[a],sa,end)
                result.append(D._similarity(raw[0],raw[-1],N[k],N[j])(raw));continue
        result.append(np.array([N[k],N[j]]))
    return result


def set_shape(it, N, C, indices=None):
    """Write vertices N and span handles C into internal line it (pts sampled from them)."""
    closed = bool(it.get('closed'))
    authored = it.get('nodes') is not None and it.get('ctrl') is not None and shape(it)[3]
    logical_vertices=editable(it)
    # rounded before sampling, as stored: shape() samples the stored values again, and a curve's point count can
    # change with the rounding (31 -> 32 points seen), which read the line as stale
    N = np.asarray(D._r(np.asarray(N, float)), float)
    C = [None if c is None else np.asarray(D._r(np.asarray(c, float).reshape(2, 2)), float) for c in C]
    if it.get('sampled_spans') is True and authored:
        set_spans(it,_raw_edit(it,N,C,indices),C)
        return
    it.pop('sampled_spans',None)
    P, idx = sample(N, C, closed)
    it['pts'] = D._r(P)
    curved=any(c is not None for c in C)
    if curved:it.pop('seam_authored_vertices', None)
    elif logical_vertices:it['seam_authored_vertices'] = True
    else:it.pop('seam_authored_vertices',None)
    if curved or authored:
        it['nodes'] = [int(i) for i in idx]
        it['ctrl'] = [None if c is None else D._r(c) for c in C]
    else:
        it.pop('nodes', None)
        it.pop('ctrl', None)


def _targets(spec, name, lid):
    """[(piece, line id, map, reversed)]: the mirror lines of line lid (before the edit) and whether each runs the
    other way along the mirror image."""
    P = np.asarray(D.internal(D.piece(spec, name), lid)['pts'], float)
    out = []
    from . import internal_sync as IS
    targets = D.mirror_lines(spec, name, lid)
    IS.bind_targets(spec, name, lid, targets)
    for nm, l2, M in targets:
        Q = np.asarray(D.internal(D.piece(spec, nm), l2)['pts'], float)
        MP = np.asarray(M(P), float)
        rev = (not D.internal(D.piece(spec, nm), l2).get('closed')
               and np.linalg.norm(Q[0] - MP[-1]) < np.linalg.norm(Q[0] - MP[0]))
        out.append((nm, l2, M, rev))
    return out


def _copy_to(src, dst, M, rev, indices=None):
    N, C, _, own = shape(src)
    N2 = np.asarray(M(N), float)
    C2 = [None if c is None else np.asarray(M(c), float) for c in C]
    if rev:
        N2 = N2[::-1]
        C2 = [None if c is None else c[::-1] for c in C2[::-1]]
    from . import point_connection as PC
    PC.remap(dst, shape(dst)[0], N2, indices=indices)
    if src.get('sampled_spans') is True and own:
        parts=[np.asarray(M(p),float) for p in span_points(src)]
        if rev:parts=[p[::-1] for p in parts[::-1]]
        set_spans(dst,parts,C2)
    else:
        dst.pop('sampled_spans',None)
        set_shape(dst, N2, C2)
    if src.get('seam_authored_vertices') is True:
        dst['seam_authored_vertices']=True
    elif not own:
        # Moving an imported sampled curve does not author new sewing corners.
        dst.pop('seam_authored_vertices', None)


def _edit(spec, name, lid, fn, keep=None, flags=None, indices=None):
    """fn(N, C, closed, R) -> (N, C) on line lid of piece name, its mirror lines given the mirror image. R: the
    piece's reflection across its center line when the line is its own mirror image, else None. The smooth vertices
    keep their handles on one line afterwards (keep: {vertex: 'in' | 'out'}, the handle that keeps its direction);
    flags(smooth set, N, R) -> the new smooth set (set_smooth). Returns the pieces changed."""
    from . import point_connection as PC,boundary_connection as BC
    connection_before=BC.snapshot(spec) if not PC._busy and indices is None else None
    pc = D.piece(spec, name)
    it = D.internal(pc, lid)
    targets = _targets(spec, name, lid)
    ms, _ = D.self_mirror_line(pc, lid)
    R = D.self_reflect(pc) if ms == lid else None
    N, C, closed, _ = shape(it)
    sm = smooth_nodes(it, len(N))
    N0 = N.copy()
    N, C = fn(N, C, closed, R)
    from . import point_connection as PC
    mapping={old:new for new,old in enumerate(indices) if old is not None} if indices is not None else None
    PC.remap(it, N0, N, indices=mapping)
    if len(N) != len(N0):                         # a vertex added or removed: the others are where they were
        sm = {int(np.linalg.norm(N - N0[k], axis=1).argmin()) for k in sm
              if np.linalg.norm(N - N0[k], axis=1).min() < 1e-6}
    if flags is not None:
        sm = set(flags(set(sm), N, R))
    C = list(C)
    for k in sorted(sm):
        _align(N, C, closed, k, (keep or {}).get(k))
    set_shape(it, N, C, indices=indices)
    _set_smooth_nodes(it, sm, len(N))
    for nm, l2, M, rev in targets:
        dst = D.internal(D.piece(spec, nm), l2)
        dest_map=({len(N0)-1-old:len(N)-1-new for old,new in mapping.items()} if rev else mapping) if mapping is not None and len(shape(dst)[0])==len(N0) else None
        _copy_to(it, dst, M, rev, indices=dest_map)
        n = len(N)
        _set_smooth_nodes(dst, {n - 1 - k for k in sm} if rev else sm, n)
        from . import fold_hand as FH
        FH.carry(it, dst, M)
    changed=[name]+[t[0] for t in targets]
    if connection_before is not None and len(N)==len(N0):
        changed+=BC.propagate(spec,connection_before)
    return list(dict.fromkeys(changed))


# ---------------------------------------------------------------- smooth vertices (user 2026-11-17)
# As the outline's smooth corners: a vertex of an internal line may be smooth ('smooth_nodes': its index among the
# line's vertices), the curve handles either side of it then stay on one line (their lengths free). Sharp by default.
# An open line's two ends have one side only and are never smooth.

def smooth_nodes(it, n=None):
    """The smooth vertices of internal line it (indices among its vertices), those still there."""
    if n is None:
        n = len(shape(it)[0])
    closed = bool(it.get('closed'))
    return {int(k) for k in it.get('smooth_nodes') or () if 0 <= int(k) < n and (closed or 0 < int(k) < n - 1)}


def _set_smooth_nodes(it, sm, n):
    closed = bool(it.get('closed'))
    sm = sorted(int(k) for k in sm if 0 <= k < n and (closed or 0 < k < n - 1))
    if sm:
        it['smooth_nodes'] = sm
    else:
        it.pop('smooth_nodes', None)


def _align(N, C, closed, k, keep=None):
    """Put the handles either side of vertex k on one line (D.align_corner on a line's vertex): keep 'in' / 'out' the
    handle before / after it keeps its direction, None both turn to the mean; a straight side keeps its direction."""
    n = len(N)
    sa = (k - 1) % n if closed or k > 0 else None
    sb = k if closed or k < n - 1 else None
    if sa is None or sb is None:
        return
    ca, cb = C[sa] is not None, C[sb] is not None
    if not (ca or cb):
        return
    P = N[k]
    ta = P - (C[sa][1] if ca else N[sa])
    tb = (C[sb][0] if cb else N[(k + 1) % n]) - P
    ua, ub = D._unit(ta), D._unit(tb)
    if not ca:
        d = ua
    elif not cb:
        d = ub
    elif keep == 'in':
        d = ua if ua is not None else ub
    elif keep == 'out':
        d = ub if ub is not None else ua
    else:
        d = D._unit(ua + ub) if ua is not None and ub is not None else (ua if ua is not None else ub)
        if d is None:
            d = ub
    if d is None:
        return
    la, lb = float(np.linalg.norm(ta)), float(np.linalg.norm(tb))
    if ca:
        C[sa] = np.array([C[sa][0], P - d * max(la, D.SMOOTH_MIN)])
    if cb:
        C[sb] = np.array([P + d * max(lb, D.SMOOTH_MIN), C[sb][1]])


def is_smooth(it, k):
    return int(k) in smooth_nodes(it)


def set_smooth(spec, name, lid, i, on=True):
    """Make vertex i of internal line lid smooth (its handles turned onto one line at once) or sharp again; its mirror
    vertex too. Returns the pieces changed."""
    def flags(sm, N, R):
        j = _partner(N, i, R)
        ks = {i} | ({j} if j is not None else set())
        return (sm | ks) if on else (sm - ks)
    return _edit(spec, name, lid, lambda N, C, closed, R: (N, C), flags=flags)


def _partner(N, i, R):
    """The vertex at the mirror image of vertex i (i itself on the center line), or None."""
    if R is None:
        return None
    d = np.linalg.norm(N - R([N[i]])[0], axis=1)
    j = int(d.argmin())
    return j if d[j] < NEAR else None


def _span_of(n, closed, i, j):
    """(span, reversed) running between vertices i and j, or (None, False)."""
    for s in range(_spans(n, closed)):
        a, b = s, (s + 1) % n
        if (a, b) == (i, j):
            return s, False
        if (a, b) == (j, i):
            return s, True
    return None, False


def _move(N, C, closed, k, m):
    n = len(N)
    N[k] = N[k] + m
    for s, w in (((k - 1) % n if closed or k > 0 else None, 1), (k if closed or k < n - 1 else None, 0)):
        if s is not None and C[s] is not None:
            C[s] = C[s].copy()
            C[s][w] = C[s][w] + m


def move_node(spec, name, lid, i, d):
    """Move vertex i of internal line lid by d mm (its handles go along). Returns the pieces changed."""
    return move_nodes(spec, name, lid, {int(i): d})


def move_nodes(spec, name, lid, deltas):
    """Move vertices of internal line lid by deltas {vertex: (dx, dy) mm} at once (their handles go along). On a line
    that is its own mirror image each vertex also gives its mirror vertex the mirror-image move, and a vertex that gets
    two moves (it and its mirror both picked, or one on the center line) takes their mean (as draft._sym_deltas).
    Returns the pieces changed."""
    from . import point_connection as PC
    connected = PC.move(spec, name, lid, deltas)
    if connected is not None:
        return connected
    deltas = {int(k): np.asarray(v, float) for k, v in deltas.items()}

    def fn(N, C, closed, R):
        acc = {}
        for i, d in deltas.items():
            acc.setdefault(i, []).append(d)
            j = _partner(N, i, R)
            if j is not None:
                acc.setdefault(j, []).append(R([N[i] + d])[0] - N[j])
        for k, ds in acc.items():
            _move(N, C, closed, k, np.mean(ds, axis=0))
        return N, C
    return _edit(spec, name, lid, fn)


def set_handle(spec, name, lid, s, w, pos):
    """Put handle w (0: by the span's first vertex) of span s of internal line lid at pos (layout mm)."""
    pos = np.asarray(pos, float)

    def fn(N, C, closed, R):
        if C[s] is None:
            raise ValueError(rpt("that part of the line is straight: C curves it"))
        n = len(N)
        C[s] = C[s].copy()
        C[s][w] = pos
        a, b = _partner(N, s, R), _partner(N, (s + 1) % n, R)
        if a is not None and b is not None:
            t, rev = _span_of(n, closed, a, b)
            if t is not None and C[t] is not None:
                w2 = 1 - w if rev else w
                if (t, w2) != (s, w):
                    C[t] = C[t].copy()
                    C[t][w2] = R([pos])[0]
        return N, C
    # a smooth vertex beside the handle: the handle dragged keeps its direction, the one across the vertex turns
    it = D.internal(D.piece(spec, name), lid)
    n = len(shape(it)[0])
    keep = {s: 'out'} if w == 0 else {(s + 1) % n: 'in'}
    return _edit(spec, name, lid, fn, keep=keep)


def locate(it, q):
    """(span, t, distance mm) of the point of internal line it nearest q."""
    N, C, closed, _ = shape(it)
    if it.get('sampled_spans') is True and shape(it)[3]:
        hits=[]
        for k,p in enumerate(span_points(it)):
            if C[k] is not None:
                _,t,d=_locate(N[[k,(k+1)%len(N)]],[C[k]],False,q)
            else:
                station,d=_project(p,q)
                length=float(np.linalg.norm(np.diff(p,axis=0),axis=1).sum())
                t=station/max(length,1e-12)
            hits.append((k,t,d))
        return min(hits,key=lambda hit:hit[2])
    return _locate(N, C, closed, q)


def _locate(N, C, closed, q):
    n = len(N)
    q = np.asarray(q, float)
    best = None
    for s in range(_spans(n, closed)):
        a, b = N[s], N[(s + 1) % n]
        if C[s] is None:
            ab = b - a
            t = float(np.clip((q - a) @ ab / max(ab @ ab, 1e-12), 0.0, 1.0))
            dist = float(np.linalg.norm(a + t * ab - q))
        else:
            B = D.bezier(a, C[s][0], C[s][1], b, 400)
            j = int(np.linalg.norm(B - q, axis=1).argmin())
            t, dist = j / 399.0, float(np.linalg.norm(B[j] - q))
        if best is None or dist < best[2]:
            best = (s, t, dist)
    return best


def _split(N, C, closed, s, t, raw=None):
    """Insert a vertex in span s at parameter t (a curve split so its shape stays). Returns its index."""
    n = len(N)
    a, b = N[s], N[(s + 1) % n]
    if C[s] is None:
        if raw is None:m = a + t * (b - a)
        else:
            cum=np.r_[0,np.cumsum(np.linalg.norm(np.diff(raw,axis=0),axis=1))]
            m=np.array([np.interp(t*cum[-1],cum,np.asarray(raw)[:,j]) for j in range(2)])
        left = right = None
    else:
        h0, h1 = C[s]
        p01, p12, p23 = a + t * (h0 - a), h0 + t * (h1 - h0), h1 + t * (b - h1)
        p012, p123 = p01 + t * (p12 - p01), p12 + t * (p23 - p12)
        m = p012 + t * (p123 - p012)
        left, right = np.array([p01, p012]), np.array([p123, p23])
    if min(np.linalg.norm(m - a), np.linalg.norm(m - b)) < MIN_GAP:
        raise ValueError(rpt("too close to a point of the line"))
    N2 = np.insert(N, s + 1, m, axis=0)
    C[s] = left
    C.insert(s + 1, right)
    return N2, s + 1


def insert_node(spec, name, lid, q):
    """Add a vertex on internal line lid at its point nearest q (layout mm); a line read as one long curve (more than
    DENSE points of its own) is fitted with a curve each side of the new vertex. Returns the pieces changed."""
    it = D.internal(D.piece(spec, name), lid)
    if not editable(it):
        return _fit_split(spec, name, lid, q)

    order=[]
    def fn(N, C, closed, R):
        order[:]=range(len(N));old_len=len(N)
        raw_mode=it.get('sampled_spans') is True and shape(it)[3]
        s,t,_=locate(it,q) if raw_mode else _locate(N,C,closed,q)
        N,k=_split(N,C,closed,s,t,span_points(it)[s] if raw_mode else None)
        if len(N)>old_len:order.insert(k,None)
        if R is not None:
            qm = R([N[k]])[0]
            if np.linalg.norm(qm - N[k]) > NEAR:
                if raw_mode:
                    import copy
                    tmp=copy.deepcopy(it);set_shape(tmp,N,C,indices=order)
                    s2,t2,_=locate(tmp,qm);raw=span_points(tmp)[s2]
                else:s2,t2,_=_locate(N,C,closed,qm);raw=None
                old_len=len(N);N,k2=_split(N,C,closed,s2,t2,raw)
                if len(N)>old_len:order.insert(k2,None)
        return N, C
    return _edit(spec, name, lid, fn, indices=order)


def _fit_split(spec, name, lid, q):
    """A dense line (no vertices of its own): two curves fitted to it, either side of its point nearest q."""
    it = D.internal(D.piece(spec, name), lid)
    if it.get('closed'):
        raise ValueError(rpt("this closed line has no points to edit: draw it again with the Ellipse or Rectangle tool"))
    P = np.asarray(it['pts'], float)
    j = int(np.linalg.norm(P - np.asarray(q, float), axis=1).argmin())
    j = min(max(j, 1), len(P) - 2)

    def fn(N, C, closed, R):
        h0, _ = D.fit_bezier(P[:j + 1])
        h1, _ = D.fit_bezier(P[j:])
        return np.array([P[0], P[j], P[-1]]), [np.asarray(h0, float), np.asarray(h1, float)]
    return _edit(spec, name, lid, fn)


def fit_error(it):
    """Largest distance (mm) between a dense line and one curve fitted to it (what C would make of it)."""
    return float(D.fit_bezier(np.asarray(it['pts'], float))[1])


def delete_node(spec, name, lid, i):
    """Remove vertex i of internal line lid: its two spans become one (a curve if either was). Returns the pieces
    changed."""
    def drop(N, C, closed, k):
        n = len(N)
        if n - 1 < (3 if closed else 2):
            raise ValueError(rpt("a line keeps at least 3 points" if closed else "a line keeps at least 2 points: X on the line removes it"))
        if not closed and k in (0, n - 1):
            return np.delete(N, k, axis=0), (C[1:] if k == 0 else C[:-1])
        sl, sr = (k - 1) % n, k
        a, b = N[(k - 1) % n], N[(k + 1) % n]
        if C[sl] is None and C[sr] is None:
            merged = None
        else:
            merged = np.array([C[sl][0] if C[sl] is not None else a + (b - a) / 3.0,
                               C[sr][1] if C[sr] is not None else a + 2.0 * (b - a) / 3.0])
        C = list(C)
        C[sl] = merged
        del C[sr]
        N = np.delete(N, k, axis=0)
        return N, C

    order=[]
    def fn(N, C, closed, R):
        order[:]=range(len(N))
        j = _partner(N, i, R)
        for k in sorted({i} | ({j} if j is not None else set()), reverse=True):
            N, C = drop(N, C, closed, k)
            order.pop(k)
        return N, C
    return _edit(spec, name, lid, fn, indices=order)


def toggle_curve(spec, name, lid, q, span=None):
    """C on internal line lid near q: its span there curved (handles at a third, so the shape stays) or straight
    again; a dense line (a curve read from a BOM) becomes one curve fitted to it. Returns the pieces changed."""
    it = D.internal(D.piece(spec, name), lid)
    if not editable(it):
        if it.get('closed'):
            raise ValueError(rpt("this closed line has no points to edit: draw it again with the Ellipse or Rectangle tool"))
        P = np.asarray(it['pts'], float)

        def fit(N, C, closed, R):
            h, _ = D.fit_bezier(P)
            return np.array([P[0], P[-1]]), [np.asarray(h, float)]
        return _edit(spec, name, lid, fit)
    s = span if span is not None else locate(it, q)[0]

    def fn(N, C, closed, R):
        n = len(N)
        curve = C[s] is None

        def flip(t):
            a, b = N[t], N[(t + 1) % n]
            if curve and it.get('sampled_spans') is True and shape(it)[3]:
                C[t]=np.asarray(D.fit_bezier(span_points(it)[t])[0],float)
            else:
                C[t] = np.array([a + (b - a) / 3.0, a + 2.0 * (b - a) / 3.0]) if curve else None
        flip(s)
        a, b = _partner(N, s, R), _partner(N, (s + 1) % n, R)
        if a is not None and b is not None:
            t, _ = _span_of(n, closed, a, b)
            if t is not None and t != s:
                flip(t)
        return N, C
    return _edit(spec, name, lid, fn)


def add_shape(spec, name, N, C, kind='shape', closed=True):
    """A new internal line with vertices N and span handles C (the Ellipse tool's four quarters). Returns its id."""
    P, _ = sample(N, C, closed)
    lid = D.add_internal(spec, name, P, kind=kind, closed=closed)
    it = D.internal(D.piece(spec, name), lid)
    targets = _targets(spec, name, lid)
    set_shape(it, N, C)
    for nm, l2, M, rev in targets:
        _copy_to(it, D.internal(D.piece(spec, nm), l2), M, rev)
    return lid
