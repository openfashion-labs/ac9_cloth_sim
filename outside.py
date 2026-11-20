# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit outside corrections on current shapes, independent of their owner.

No placement reconstruction or persistent parent/layer relationship is involved.
Distances are world millimeters. Triangle winding defines a cloth's front.
"""
from . import prepare_build as PB
import json
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from . import selection as SEL
from .i18n import tx


def seam_pattern(context, spec):
    """The meshed pattern (its stitches) the start shapes are made from: the same spec resolution and mesh build as
    dress.prepare, without making the start shapes (2 s on a coat, for seam_tags that read only the pattern)."""
    import bpy
    from . import dress as D
    props = context.scene.ac9_cloth_sim
    inputs = D.PP.resolve(spec, props, environ=None if bpy.app.background else {}, local=None)
    return D._build_cached(inputs.spec, props.mesh_size)[0]


def seam_tags(shapes, pattern, spec):
    """A strict subset of the solver's existing opposing stitched-triangle exception.

    No ring expansion: a face must contain the actual stitch endpoint. Also
    require the current endpoints to be within the solver check's 0.45 mm
    contact depth; an open, abstract seam does not join spatially separate layers.
    """
    from mathutils.kdtree import KDTree
    names=list(map(str,pattern['names']))
    out={id(r['X']):[set() for _ in r['T']] for r in shapes.values()}
    allowed={sm['id'] for sm in spec.get('seams',[]) if sm.get('enabled',True)}
    seam_ids=pattern.get('stitch_seam')
    if seam_ids is None or not allowed:return out
    maps={};incidence={}
    for i,name in enumerate(names):
        if name not in shapes or shapes[name].get('flat') is None:continue
        row=shapes[name];flat=np.asarray(row['flat'])
        kd=KDTree(len(flat))
        for k,p in enumerate(flat):kd.insert((float(p[0]),float(p[1]),0),k)
        kd.balance()
        mapping=[]
        for p in pattern[f'V{i}']:
            matches=kd.find_range((float(p[0]),float(p[1]),0),.001)
            mapping.append(int(matches[0][1]) if len(matches)==1 else -1)
        maps[i]=mapping
        incidence[i]=[[] for _ in flat]
        for j,face in enumerate(row['T']):
            for v in face:incidence[i][int(v)].append(j)
    for k,(i,v,j,w) in enumerate(np.asarray(pattern['stitch'],int)):
        if k>=len(seam_ids) or str(seam_ids[k]) not in allowed:continue
        if i not in maps or j not in maps:continue
        vi,wi=maps[i][v],maps[j][w]
        if vi<0 or wi<0:continue
        a,b=shapes[names[i]],shapes[names[j]]
        if np.linalg.norm(a['X'][vi]-b['X'][wi])>.45:continue
        for side,index,vertex,row in ((0,i,vi,a),(1,j,wi,b)):
            for face in incidence[index][vertex]:out[id(row['X'])][face].add((k,side))
    return out


def front_direction(Y, surfaces):
    """Area-independent front normal near Y, from the target cloth's winding.

    Reject surfaces with opposed normals rather than infer a side from where
    the moving piece happens to be. The nearest target is sampled per vertex.
    """
    bvhs = [BVHTree.FromPolygons(X.tolist(), np.asarray(T).tolist(), all_triangles=True)
            for X, T in surfaces if len(T)]
    normals = []
    for p in Y:
        hits = [bv.find_nearest(Vector(p)) for bv in bvhs]
        hits = [h for h in hits if h[0] is not None]
        if hits:
            h = min(hits, key=lambda h: h[3])
            normals.append(np.asarray(h[1]))
    if not normals:
        raise ValueError("No overlapping cloth surface found")
    normals = np.asarray(normals)
    v = normals.mean(0)
    if np.linalg.norm(v) < 0.25:
        raise ValueError(RING_REFUSALS[0])
    v /= np.linalg.norm(v)
    if np.mean(normals @ v > 0.1) < 0.9:
        raise ValueError(RING_REFUSALS[1])
    return v


PAIR_CROSS = ("{0} and {1} (a pair) cross each other: moved together, they cannot come apart. Unlink the pair "
              "(Tool tab > AC9 Piece > Pair), select the one on top and Move Outside again, or move it with Whole Pieces")
RING_REFUSALS = ("The cloth fronts point in opposing directions: select a smaller piece",
                 "The cloth front is ambiguous at a fold: move this piece with Whole Pieces")


def bend_out(Y, T, targets, gap, reach=3.0, rounds=8, iters=300):
    """A non-rigid outside correction, for a partner no single direction clears (a collar folded over its stand ring).

    Each vertex within reach*gap of a target face, and under gap in front of it, is pushed along that face's front
    to gap; the push is spread over the piece by averaging it over the edges, so the piece keeps its shape where
    nothing pushes it. Returns W (world mm). The caller validates it (crossings, body, seams, squashed).
    """
    Y, T = np.asarray(Y, float), np.asarray(T, int)
    E = np.unique(np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1), axis=0)
    deg = np.bincount(E.ravel(), minlength=len(Y)).astype(float)[:, None]
    bvhs = [BVHTree.FromPolygons(np.asarray(X).tolist(), np.asarray(F).tolist(), all_triangles=True)
            for X, F in targets if len(F)]
    D = np.zeros_like(Y)
    for _ in range(rounds):
        P = Y + D
        need, nrm = np.zeros(len(Y)), np.zeros_like(Y)
        for k, p in enumerate(P):
            hits = [h for h in (bv.find_nearest(Vector(p)) for bv in bvhs) if h[0] is not None]
            if not hits:
                continue
            q, n, _, d = min(hits, key=lambda h: h[3])
            s = float(np.dot(p - np.asarray(q), np.asarray(n)))
            if d < reach * gap and s < gap:
                need[k], nrm[k] = gap - s + 1e-3, np.asarray(n)
        on = need > 0
        if not on.any():
            break
        D0 = D.copy()
        for _ in range(iters):
            S = np.zeros_like(D)
            np.add.at(S, E[:, 0], D[E[:, 1]])
            np.add.at(S, E[:, 1], D[E[:, 0]])
            D = 0.5 * D + 0.5 * S / np.maximum(deg, 1)
            short = need[on] - np.einsum('ij,ij->i', D[on] - D0[on], nrm[on])
            D[on] += np.maximum(short, 0)[:, None] * nrm[on]
    return Y + D


def local_surfaces(Y, T, surfaces, gap):
    """Intersecting and nearest face patches; distant projected layers are not targets."""
    mine=BVHTree.FromPolygons(Y.tolist(),np.asarray(T).tolist(),all_triangles=True)
    rows=[]
    nearest=np.inf
    for X,F in surfaces:
        X,F=np.asarray(X,float),np.asarray(F,int)
        if not len(F):continue
        bv=BVHTree.FromPolygons(X.tolist(),F.tolist(),all_triangles=True)
        hits=[bv.find_nearest(Vector(p)) for p in Y]
        nearest=min(nearest,min((h[3] for h in hits if h[0] is not None),default=np.inf))
        rows.append((X,F,bv,hits))
    out=[]
    for X,F,bv,hits in rows:
        ids={int(j) for _,j in mine.overlap(bv)}
        ids|={int(h[2]) for h in hits if h[0] is not None and h[3]<=max(gap,nearest+gap)}
        if ids:
            # A full one-ring avoids cracks between the queried nearest faces.
            vertices=np.unique(F[list(ids)])
            keep=np.isin(F,vertices).any(1)
            out.append((X,F[keep]))
    return out


def _clip_half(poly, coefficient, threshold=0.0):
    """Clip a convex projected polygon by one linear half-plane.

    Plain floats: called tens of thousands of times on 3 to 6 points, where numpy's per-call cost was most of the
    outside correction's time (a pair unlinked over its other half: 17 s)."""
    if not len(poly):return []
    c0,c1,c2=float(coefficient[0]),float(coefficient[1]),float(coefficient[2])
    out=[];px,py=float(poly[-1][0]),float(poly[-1][1]);dp=px*c0+py*c1+c2-threshold
    for cur in poly:
        x,y=float(cur[0]),float(cur[1])
        dc=x*c0+y*c1+c2-threshold
        if (dc>=0)!=(dp>=0):out.append((px+(x-px)*dp/(dp-dc),py+(y-py)*dp/(dp-dc)))
        if dc>=0:out.append((x,y))
        px,py,dp=x,y,dc
    return out


def _clip_triangle(poly, triangle):
    t=[(float(q[0]),float(q[1])) for q in triangle]
    area=(t[1][0]-t[0][0])*(t[2][1]-t[0][1])-(t[1][1]-t[0][1])*(t[2][0]-t[0][0])
    sign=1.0 if area>0 else -1.0
    for (ax,ay),(bx,by) in zip(t,t[1:]+t[:1]):
        nx,ny=-(by-ay)*sign,(bx-ax)*sign
        poly=_clip_half(poly,(nx,ny,-(nx*ax+ny*ay)),-1e-9)
        if not len(poly):break
    return poly


def _area(poly):
    if len(poly)<3:return 0.0
    s_=0.0
    for (x0,y0),(x1,y1) in zip(poly,list(poly[1:])+[poly[0]]):
        s_+=x0*y1-y0*x1
    return abs(s_)*.5


def _box_pairs(alo, ahi, blo, bhi):
    """Every (i, j) with box i of (alo, ahi) and box j of (blo, bhi) overlapping in 2D (closed), sorted by i then j.

    The boxes are dropped into a uniform grid (cell about twice the usual box); pairs sharing a cell are the
    candidates, then tested exactly as `(blo <= ahi) & (bhi >= alo)` on both axes."""
    none = np.zeros(0, np.int64)
    if not len(alo) or not len(blo):
        return none, none
    ext = np.r_[(ahi - alo).max(1), (bhi - blo).max(1)]
    cell = max(2.0 * float(np.median(ext)), 1e-6)
    org = np.minimum(alo.min(0), blo.min(0))
    top = np.floor((np.maximum(ahi.max(0), bhi.max(0)) - org) / cell).astype(np.int64) + 1

    def cells(lo, hi):
        i0 = np.floor((lo - org) / cell).astype(np.int64)
        i1 = np.floor((hi - org) / cell).astype(np.int64)
        nx, ny = i1[:, 0] - i0[:, 0] + 1, i1[:, 1] - i0[:, 1] + 1
        cnt = nx * ny
        owner = np.repeat(np.arange(len(lo)), cnt)
        k = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        nxr = np.repeat(nx, cnt)
        cx, cy = np.repeat(i0[:, 0], cnt) + k % nxr, np.repeat(i0[:, 1], cnt) + k // nxr
        return owner, cx * top[1] + cy

    ia, ka = cells(alo, ahi)
    jb, kb = cells(blo, bhi)
    o = np.argsort(kb, kind='stable')
    jb, kb = jb[o], kb[o]
    left, right = np.searchsorted(kb, ka, 'left'), np.searchsorted(kb, ka, 'right')
    cnt = right - left
    pa = np.repeat(ia, cnt)
    pos = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt) + np.repeat(left, cnt)
    pj = jb[pos]
    key = np.unique(pa * len(blo) + pj)
    pa, pj = key // len(blo), key % len(blo)
    ok = ((blo[pj] <= ahi[pa]) & (bhi[pj] >= alo[pa])).all(1)
    return pa[ok], pj[ok]


def _clip_many(poly, cnt, coef, threshold):
    """_clip_half for many convex polygons at once: poly (N, M, 2) with cnt (N,) points each, coef (N, 3) or (3,).
    The same arithmetic in the same order, so the same points. Returns (poly, cnt)."""
    N, M = poly.shape[:2]
    coef = np.broadcast_to(np.asarray(coef, float), (N, 3))
    idx = np.arange(M)[None, :]
    on = idx < cnt[:, None]
    prev = np.where(idx == 0, np.maximum(cnt[:, None] - 1, 0), idx - 1)
    cur, prv = poly, np.take_along_axis(poly, prev[..., None], 1)
    c0, c1, c2 = coef[:, None, 0], coef[:, None, 1], coef[:, None, 2]
    dc = cur[..., 0] * c0 + cur[..., 1] * c1 + c2 - threshold
    dp = prv[..., 0] * c0 + prv[..., 1] * c1 + c2 - threshold
    cross = on & ((dc >= 0) != (dp >= 0))
    den = np.where(cross, dp - dc, 1.0)
    hit = np.stack([prv[..., 0] + (cur[..., 0] - prv[..., 0]) * dp / den,
                    prv[..., 1] + (cur[..., 1] - prv[..., 1]) * dp / den], -1)
    pts = np.stack([hit, cur], 2).reshape(N, 2 * M, 2)
    keep = np.stack([cross, on & (dc >= 0)], 2).reshape(N, 2 * M)
    order = np.argsort(~keep, axis=1, kind='stable')
    pts = np.take_along_axis(pts, order[..., None], 1)
    cnt = keep.sum(1)
    width = max(int(cnt.max()) if N else 0, 1)
    return pts[:, :width], cnt


def _clip_many_triangle(poly, cnt, tri):
    """_clip_triangle for many: each polygon clipped to its triangle tri (N, 3, 2), its edges in order."""
    t = np.asarray(tri, float)
    area = (t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0])
    sign = np.where(area > 0, 1.0, -1.0)
    poly = np.asarray(poly, float)
    for e in range(3):
        a_, b_ = t[:, e], t[:, (e + 1) % 3]
        nx, ny = -(b_[:, 1] - a_[:, 1]) * sign, (b_[:, 0] - a_[:, 0]) * sign
        poly, cnt = _clip_many(poly, cnt, np.stack([nx, ny, -(nx * a_[:, 0] + ny * a_[:, 1])], 1), -1e-9)
    return poly, cnt


def _area_many(poly, cnt):
    """_area for many: 0 under 3 points."""
    N, M = poly.shape[:2]
    idx = np.arange(M)[None, :]
    on = idx < cnt[:, None]
    nxt = np.where(idx + 1 < cnt[:, None], idx + 1, 0)
    q = np.take_along_axis(poly, nxt[..., None], 1)
    s_ = np.where(on, poly[..., 0] * q[..., 1] - poly[..., 1] * q[..., 0], 0.0).sum(1)
    return np.where(cnt >= 3, abs(s_) * .5, 0.0)


def sweep_gate(Y, T, surfaces, direction, amount, tags=None, permitted=None):
    """Only the closest sheet at each projected location may be exited.

    For moving triangle A, traversed face B, and possible nearer face C,
    depths db=zb-za and dc=zc-za are linear in projection coordinates.
    A forbidden region satisfies 0<db<amount, db-dc>0, db+dc>0.
    Convex clipping checks that region rather than granting a connected
    sheet permission: a spiral can have many layers in one connected sheet.
    """
    d=np.asarray(direction,float)
    ref=np.eye(3)[int(np.argmin(abs(d)))];u=np.cross(d,ref);u/=np.linalg.norm(u)
    basis=np.stack([u,np.cross(d,u),d],1)
    A=(np.asarray(Y)@basis)[T]
    a_mats=np.concatenate([A[:,:,:2],np.ones((*A.shape[:2],1))],axis=2)
    if (abs(np.linalg.det(a_mats))<1e-9).any():
        raise ValueError("The cloth front is ambiguous at a fold: move this piece with Whole Pieces")
    B=np.concatenate([(np.asarray(X)@basis)[F] for X,F in surfaces if len(F)])
    face_tags=[s for X,F in surfaces if len(F) for s in (tags.get(id(X),[set() for _ in F]) if tags else [set() for _ in F])]
    mats=np.concatenate([B[:,:,:2],np.ones((*B.shape[:2],1))],axis=2)
    determinant=np.linalg.det(mats);valid=abs(determinant)>1e-9
    B,mats,determinant=B[valid],mats[valid],determinant[valid]
    face_tags=[face_tags[k] for k in np.nonzero(valid)[0]]
    if permitted is not None:permitted=np.asarray(permitted,bool)[valid]
    heights=np.linalg.solve(mats,B[:,:,2,None])[:,:,0]
    lo,hi=B[:,:,:2].min(1),B[:,:,:2].max(1)
    tol=1e-4
    # Faces outside the moving piece's projected box cannot be traversed or license anything: dropped once, so the
    # per-triangle searches below run over the neighborhood only.
    near_all=np.nonzero(((lo<=A[:,:,:2].max((0,1)))&(hi>=A[:,:,:2].min((0,1)))).all(1))[0]
    B,mats,determinant,heights,lo,hi=B[near_all],mats[near_all],determinant[near_all],heights[near_all],lo[near_all],hi[near_all]
    face_tags=[face_tags[k] for k in near_all]
    if permitted is not None:permitted=permitted[near_all]
    za_all=np.linalg.solve(a_mats,A[:,:,2,None])[:,:,0]
    # Every (moving triangle, face) pair whose projected boxes overlap, found through a grid instead of testing each
    # triangle against every face (11003 triangles x 110k faces: 13 s of a 16.5 s Move Outside, measured 2026-11-16).
    # Sorted by triangle then face, the same pairs in the same order as the per-triangle search.
    alo,ahi=A[:,:,:2].min(1),A[:,:,:2].max(1)
    pa,pj=_box_pairs(alo,ahi,lo,hi)
    starts=np.searchsorted(pa,np.arange(len(A)+1))
    # Cheap interval bound before polygon operations, for every pair at once (the depth of face j over triangle a is
    # linear, so its extremes are at the triangle's corners).
    dbs=heights[pj]-za_all[pa]
    bound=np.einsum('pkc,pc->pk',a_mats[pa],dbs)
    keep=np.nonzero((bound.max(1)>tol)&(bound.min(1)<amount-tol))[0]
    BLOCK="Another cloth layer blocks the outside correction: move this piece with Whole Pieces"
    # Each kept pair's region (triangle a within face j, depth 0..amount), clipped for all pairs at once: the same
    # half-planes in the same order as _clip_triangle / _clip_half (one pair at a time in Python: 1.3 s of a coat's
    # Move Outside, measured 2026-11-16)
    cand_j=pj                       # every pair's face, by triangle (starts): the nearer faces are among them
    pa,pj,dbs=pa[keep],pj[keep],dbs[keep]
    poly,cnt=_clip_many_triangle(A[pa,:,:2],np.full(len(pa),3),B[pj,:,:2])
    poly,cnt=_clip_many(poly,cnt,dbs,tol)
    poly,cnt=_clip_many(poly,cnt,-dbs,-amount+tol)
    live=np.nonzero(_area_many(poly,cnt)>=1e-8)[0]
    pa,pj,dbs,poly,cnt=pa[live],pj[live],dbs[live],poly[live],cnt[live]
    if permitted is not None and not permitted[pj].all():
        raise ValueError(BLOCK)
    if (determinant[pj]<0).any():
        raise ValueError(BLOCK)
    if not len(pa):
        return
    # a nearer face k over the region (the region lies inside triangle a, so k is among a's pairs): every (region,
    # k) at once for the box and the open test, the few left clipped one at a time as before
    on=np.arange(poly.shape[1])[None,:]<cnt[:,None]
    pmax=np.where(on[...,None],poly,-np.inf).max(1);pmin=np.where(on[...,None],poly,np.inf).min(1)
    L=starts[pa+1]-starts[pa]
    r=np.repeat(np.arange(len(pa)),L)
    k=cand_j[np.arange(int(L.sum()))-np.repeat(np.cumsum(L)-L,L)+np.repeat(starts[pa],L)]
    ok=((lo[k]<=pmax[r])&(hi[k]>=pmin[r])).all(1)&(k!=pj[r])
    r,k=r[ok],k[ok]
    for s0 in range(0,len(r),200000):
        rr,kk=r[s0:s0+200000],k[s0:s0+200000]
        dcs=heights[kk]-za_all[pa[rr]]
        minus,plus=dbs[rr]-dcs,dbs[rr]+dcs
        P_=poly[rr];o_=on[rr]
        vm=np.where(o_,P_[:,:,0]*minus[:,None,0]+P_[:,:,1]*minus[:,None,1]+minus[:,None,2],-np.inf).max(1)
        vp=np.where(o_,P_[:,:,0]*plus[:,None,0]+P_[:,:,1]*plus[:,None,1]+plus[:,None,2],-np.inf).max(1)
        q=np.nonzero((vm>tol)&(vp>tol))[0]
        # Existing opposing stitched-triangle contact exception, with no ring expansion.
        q=q[[not (face_tags[int(kk[x])] and any((stitch,1-side) in face_tags[int(kk[x])]
                                                for stitch,side in face_tags[int(pj[rr[x]])])) for x in q]] if len(q) else q
        if not len(q):
            continue
        reg,rc=_clip_many_triangle(poly[rr[q]],cnt[rr[q]],B[kk[q],:,:2])
        reg,rc=_clip_many(reg,rc,minus[q],tol)
        reg,rc=_clip_many(reg,rc,plus[q],tol)
        if (_area_many(reg,rc)>1e-8).any():
            raise ValueError(BLOCK)


def correct(Y, T, others, body, gap, targets=None, direction=None, minimum=0.0, tags=None, validate=True):
    """Resolve the local front, move rigidly, then check all cloth and body."""
    from . import place_settle as PT
    Y,T=np.asarray(Y,float),np.asarray(T,int)
    patches=local_surfaces(Y,T,others if targets is None else targets,gap)
    front=front_direction(Y,patches)
    d=front if direction is None else np.asarray(direction,float)
    if np.dot(d,front)<0.1:
        raise ValueError("The linked pieces need opposing outside directions: unlink them first")
    constraints=local_surfaces(Y,T,others+[body],gap)
    W,num=translation(Y,T,constraints,d,gap)
    if minimum>num['distance']:
        num['distance']=float(minimum)
        W=Y+d*minimum
    if not validate:
        return W,num  # A provisional amount only; callers validate the joint amount before committing.
    num.update(validate_motion(Y,T,W,others,body,tags,targets))
    return W,num


def validate_motion(Y,T,W,others,body,tags=None,targets=None):
    """Validate the exact supplied rigid displacement, including its entire path."""
    from . import place_settle as PT
    Y,T,W=np.asarray(Y,float),np.asarray(T,int),np.asarray(W,float)
    shift=W-Y
    if not np.allclose(shift,shift[0],atol=1e-6,rtol=0):
        raise ValueError("The outside correction is not a rigid translation")
    amount=float(np.linalg.norm(shift[0]))
    d=shift[0]/amount if amount>1e-9 else np.array([0.,0.,1.])
    body_bv=PT._bvh_mm(*body)
    cloth_bv=[PT._bvh_mm(X,F) for X,F in others]
    def crossing(X):
        bv=PT._bvh_mm(X,T)
        return {(j,int(a),int(b)) for j,other in enumerate(cloth_bv) for a,b in bv.overlap(other)}
    before=crossing(Y);after=crossing(W)
    if after:
        raise ValueError("The outside correction still crosses other cloth: move this piece with Whole Pieces")
    inside=0
    for p in W:
        q,n,_,_=body_bv.find_nearest(Vector(p))
        if q is not None and (Vector(p)-q).dot(n)<-1e-4:inside+=1
    if inside:
        raise ValueError("The outside correction would enter the body: move this piece with Whole Pieces")
    # A previously separate layer must not be crossed en route to the target.
    # Test the swept triangles against each obstacle, excluding the initial contacts.
    n=len(Y);swept=np.vstack([Y,W])
    edges=np.unique(np.sort(np.vstack([T[:,[0,1]],T[:,[1,2]],T[:,[2,0]]]),axis=1),axis=0)
    walls=np.vstack([np.c_[edges[:,0],edges[:,1],edges[:,1]+n],np.c_[edges[:,0],edges[:,1]+n,edges[:,0]+n]])
    sweep=PT._bvh_mm(swept,np.vstack([T,T+n,walls]))
    for j,((X,F),bv) in enumerate(zip(others,cloth_bv)):
        tri=np.asarray(X)[F]
        normal=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
        tangent=abs(normal@d)<=1e-9*np.linalg.norm(normal,axis=1)
        old={b for surface,_,b in before if surface==j}
        if any(tangent[b] and b not in old for _,b in sweep.overlap(bv)):
            raise ValueError("The cloth front is ambiguous at a fold: move this piece with Whole Pieces")
    permitted=None
    if targets is not None:
        wanted={id(X) for X,_ in targets}
        permitted=[id(X) in wanted or face in {b for surface,_,b in before if surface==j}
                   for j,(X,F) in enumerate(others) for face in range(len(F))]
    if amount>1e-9:sweep_gate(Y,T,others,d,amount,tags,permitted)
    body_hits=sweep.overlap(body_bv)
    if body_hits:
        began_inside=any((Vector(p)-h[0]).dot(h[1])< -1e-4 for p in Y if (h:=body_bv.find_nearest(Vector(p)))[0] is not None)
        if not began_inside:
            raise ValueError("The body blocks the outside correction: move this piece with Whole Pieces")
        if amount>1e-9:sweep_gate(Y,T,[body],d,amount)
    return dict(cross_before=len({a for _,a,_ in before}),cross_after=0,inside_after=0)


def translation(Y, T, surfaces, direction, gap):
    """Rigid translation beyond every projected overlapping triangle.

    The maximum separation is at a vertex of the projected intersection
    polygon. Including edge/edge intersections catches crossings without a
    moving vertex above the obstacle. Vectorized bounding boxes bound the pair search.
    """
    Y, T = np.asarray(Y, float), np.asarray(T, int)
    d = np.array(direction, float, copy=True)
    d /= np.linalg.norm(d)
    ref = np.eye(3)[int(np.argmin(abs(d)))]
    u = np.cross(d, ref); u /= np.linalg.norm(u)
    basis = np.stack([u, np.cross(d, u), d], 1)
    A = (Y @ basis)[T]
    B = np.concatenate([(np.asarray(X) @ basis)[np.asarray(F, int)] for X, F in surfaces if len(F)])
    mb = np.concatenate([B[:,:,:2], np.ones((*B.shape[:2],1))],axis=2)
    valid = abs(np.linalg.det(mb)) > 1e-9
    B,mb = B[valid],mb[valid]
    bz = np.linalg.solve(mb,B[:,:,2,None])[:,:,0]
    lo, hi = B[:,:,:2].min(1), B[:,:,:2].max(1)
    def cross(a,b):
        return a[...,0]*b[...,1]-a[...,1]*b[...,0]
    dist, hits, furthest = -np.inf, 0, None
    # every (triangle, face) pair at once, the pairs from a grid (one triangle at a time against every face: 1.2 s of
    # a coat's Move Outside, measured 2026-11-16); sorted by triangle then face, so the furthest is the first found
    ma = np.concatenate([A[:, :, :2], np.ones((len(A), 3, 1))], axis=2)
    ok_a = np.nonzero(abs(np.linalg.det(ma)) >= 1e-9)[0] if len(A) else np.zeros(0, np.int64)
    za_all = np.zeros((len(A), 3))
    if len(ok_a):
        za_all[ok_a] = np.linalg.solve(ma[ok_a], A[ok_a, :, 2, None])[:, :, 0]
    qa, qj = _box_pairs(A[ok_a, :, :2].min(1) - 1e-8, A[ok_a, :, :2].max(1) + 1e-8, lo, hi)
    qa = ok_a[qa]
    for s0 in range(0, len(qa), 100000):
        pa, pj = qa[s0:s0 + 100000], qj[s0:s0 + 100000]
        aa, b = A[pa, :, :2], B[pj, :, :2]
        ae = np.roll(aa, -1, axis=1) - aa; be = np.roll(b, -1, axis=1) - b
        sa = np.sign(cross(ae[:, 0], ae[:, 1])); sb = np.sign(cross(be[:, 0], be[:, 1]))
        ainb = (cross(be[:, :, None, :], aa[:, None, :, :] - b[:, :, None, :]) * sb[:, None, None] >= -1e-8).all(1)
        bina = (cross(ae[:, :, None, :], b[:, None, :, :] - aa[:, :, None, :]) * sa[:, None, None] >= -1e-8).all(1)
        den = cross(ae[:, :, None, :], be[:, None, :, :]); nz = abs(den) > 1e-10
        delta = b[:, None, :, :] - aa[:, :, None, :]
        ta = np.divide(cross(delta, be[:, None, :, :]), den, out=np.zeros_like(den), where=nz)
        tb = np.divide(cross(delta, ae[:, :, None, :]), den, out=np.zeros_like(den), where=nz)
        on = nz & (ta >= -1e-8) & (ta <= 1 + 1e-8) & (tb >= -1e-8) & (tb <= 1 + 1e-8)
        pts = np.concatenate([aa, b, (aa[:, :, None, :] + ta[..., None] * ae[:, :, None, :]).reshape(-1, 9, 2)], axis=1)
        mask = np.concatenate([ainb, bina, on.reshape(-1, 9)], axis=1)
        dz = bz[pj] - za_all[pa]
        z = np.einsum('nki,ni->nk', pts, dz[:, :2]) + dz[:, 2, None]
        if mask.any():
            zm = np.where(mask, z, -np.inf)
            if zm.max() > dist:
                dist = float(zm.max()); furthest = int(np.nonzero(valid)[0][pj[np.unravel_index(zm.argmax(), zm.shape)[0]]])
            hits += int(mask.any(1).sum())
    if not hits:
        raise ValueError("No overlapping cloth surface found")
    amount=max(float(gap),dist+float(gap))
    # clearance: how far in front of everything it overlaps (projected) the piece already is
    return Y+d*amount, dict(distance=amount, overlap_pairs=hits, direction=d.tolist(), furthest_triangle=furthest,
                            clearance=float(-dist))


def snapshot(context, spec, *, preparation=None):
    """Current world shapes with their exact write-back indices, before modifiers."""
    import bpy
    from . import display_state as DS, placed_view as PV, dress as D
    from .core import spec as S
    g = context.scene.objects.get(D.garment_name(spec))
    states = DS.snapshot(spec, g)
    out, raw = {}, None
    obs = PV.objects(spec)
    for name, state in states.items():
        if not state['solid'] or state['source'] != 'placed':
            continue
        ob = obs[name]
        hs = PV.hand_shape(ob)
        if hs is None:
            raise ValueError("The placed shape has no pattern correspondence: " + name)
        V, T, X = hs
        if np.linalg.det(np.asarray(ob.matrix_world)[:3,:3]) < 0:
            T=T[:,::-1]
        if name != ob[PV.PROP]:
            pl_=S.pair_plane(spec,name)
            X=S.mirror_points(X,pl_,S.mirror_center(spec,pl_,name)); T=T[:,::-1]
            pcs={pc['name']:pc for pc in spec['pieces']}
            V=PV.mirror_flat(V,S.Outline(pcs[ob[PV.PROP]]).B,S.Outline(pcs[name]).B,pl_)
        out[name] = dict(X=X,T=T,flat=V,owner='placed',ob=ob)
    if g is not None and any(s['source']=='result' and s['solid'] for s in states.values()):
        raw = D.garment_co(context,g).astype(float)
        M = np.asarray(g.matrix_world)
        W = (raw @ M[:3,:3].T+M[:3,3])*1000
        me = g.data; me.calc_loop_triangles()
        tri = np.asarray([tuple(t.vertices) for t in me.loop_triangles],int)
        faces = np.asarray([t.polygon_index for t in me.loop_triangles],int)
        pa = np.asarray([a.value for a in me.attributes['ac9_piece'].data],int)
        names = json.loads(g['ac9_piece_names'])
        flat=None
        if me.uv_layers:
            flat=np.zeros((len(raw),2))
            loops=np.asarray([loop.vertex_index for loop in me.loops],int)
            uv=np.empty(len(me.loops)*2,np.float32)
            me.uv_layers[0].data.foreach_get('uv',uv)
            flat[loops]=uv.reshape(-1,2).astype(float)*1000
        for j,name in enumerate(names):
            if name not in states or not states[name]['solid'] or states[name]['source']!='result':continue
            T=tri[pa[faces]==j]
            if not len(T):continue
            idx=np.unique(T);inv=np.full(len(raw),-1,int);inv[idx]=np.arange(len(idx))
            T=inv[T]
            if np.linalg.det(M[:3,:3])<0:T=T[:,::-1]
            out[name]=dict(X=W[idx],T=T,idx=idx,flat=flat[idx] if flat is not None else None,owner='result',ob=g)
    # Shapes not materialized yet remain pure guide input until commit.
    missing=[n for n,s in states.items() if s['solid'] and s['source']=='proxy']
    if missing:
        res=D.prepare(context,context.scene.ac9_cloth_sim.mesh_size,shapes_only=True,placed_only=True, preparation=preparation)
        for i,name in enumerate(map(str,res['pat']['names'])):
            if name in missing:
                out[name]=dict(X=np.asarray(res['X'][i],float),T=np.asarray(res['tris'][i]),flat=np.asarray(res['pat'][f'V{i}']),owner='proxy',ob=None)
    return out, g, raw


def _clear(Y, T, others, co, tr):
    """No crossing with the other cloth and no point inside the body."""
    from . import place_settle as PT
    if PT._crossing_tris(Y,T,others,np.zeros(len(Y),bool)).size:return False
    body_bv=PT._bvh_mm(co,tr)
    for p in Y:
        q,n,_,_=body_bv.find_nearest(Vector(p))
        if q is not None and (Vector(p)-q).dot(n)<-1e-4:return False
    return True


def move_selected(context, names, ring_ok=False, *, preparation=None):
    """Plan every selected piece from one snapshot; commit only after validation.

    ring_ok: a piece the outside step cannot move in one direction (its partner wraps around it) is left where it
    is when it is already clear of the cloth and the body (Part to Seam, which bent it out first)."""
    from . import store, placed_view as PV, placement as PL, dress as D, piece_move as PM
    from . import place_seam as PS
    from .core import spec as S
    if context.mode != 'OBJECT':
        raise ValueError("Return to Object Mode before correcting the current shapes")
    spec = store.get(context)[0]
    selected=[ob.name for ob in SEL.objects(context)]
    active=context.view_layer.objects.active.name if context.view_layer.objects.active else None
    backup = PV.PreparedResetBackup(context, spec)
    try:
        with PB.operation(context, owner=preparation, consumer_backup=backup) as preparation:
            shapes,g,raw = snapshot(context,spec, preparation=preparation)
            if not names or any(n not in shapes for n in names):
                raise ValueError("Select pieces with a current 3D shape first")
            tags={}
            if spec.get('seams'):
                tags=seam_tags(shapes,seam_pattern(context,spec),spec)
            plans,seen,already=[],set(),{}
            co,tr=PL.body_arrays(context,context.scene.ac9_cloth_sim.body)
            from . import place_settle as PT
            for n in names:
                row=shapes[n];ob=row['ob']
                key=ob.name if row['owner']=='placed' else n
                if key in seen:continue
                seen.add(key)
                excluded=set(names)
                if row['owner']=='placed':
                    excluded|={ob.get(PV.PROP),ob.get(PV.TWIN_PROP)}
                others=[(r['X'],r['T']) for k,r in shapes.items() if k not in excluded]
                twin=None
                if row['owner']=='placed' and ob.get(PV.TWIN_PROP):
                    other=ob[PV.TWIN_PROP] if n==ob[PV.PROP] else ob[PV.PROP]
                    twin=other if other in shapes else None
                try:
                    # The amount first, without the path check (the expensive part): what is already outside, or a
                    # pair that cannot come apart, is said before any path is checked.
                    W,num=correct(row['X'],row['T'],others,(co,tr),8.0,tags=tags,validate=False)
                    if twin is None:
                        if num['clearance']>=8.0:
                            already[n]=num['clearance'];continue
                        num.update(validate_motion(row['X'],row['T'],W,others,(co,tr),tags))
                except ValueError as exc:
                    # A partner that wraps around (Part to Seam has already bent the piece out of it): nothing to move.
                    if not (ring_ok and twin is None and str(exc) in RING_REFUSALS and _clear(row['X'],row['T'],others,co,tr)):
                        raise
                    plans.append((n,row['X'].copy(),dict(distance=0.0,ring=True)))
                    continue
                if twin is not None:
                    pl_=S.pair_plane(spec,twin);k_=S.axis_of(pl_)
                    d=np.asarray(num['direction']);dm=d.copy();dm[k_]*=-1
                    tw=shapes[twin]
                    _,tn=correct(tw['X'],tw['T'],others,(co,tr),8.0,direction=dm,tags=tags,validate=False)
                    if num['clearance']>=8.0 and tn['clearance']>=8.0:
                        # clear of the other cloth, but maybe not of each other (fronts crossing at the center)
                        if PT._crossing_tris(row['X'],row['T'],[(tw['X'],tw['T'])],np.zeros(len(row['X']),bool)).size:
                            raise ValueError(tx(PAIR_CROSS,n,twin))
                        already[n]=min(num['clearance'],tn['clearance']);continue
                    amount=max(num['distance'],tn['distance'])
                    # Moved together, a pair's halves only move apart across their mirror plane (2 x amount x the
                    # direction's part across it): halves that cross each other there cannot be parted by this.
                    Wp=row['X']+d*amount
                    Xt=S.mirror_points(Wp,pl_,S.mirror_center(spec,pl_,twin))
                    if PT._crossing_tris(Wp,row['T'],[(Xt,tw['T'])],np.zeros(len(Wp),bool)).size:
                        raise ValueError(tx(PAIR_CROSS,n,twin))
                    W,num=correct(row['X'],row['T'],others,(co,tr),8.0,direction=d,minimum=amount,tags=tags)
                    correct(tw['X'],tw['T'],others,(co,tr),8.0,direction=dm,minimum=amount,tags=tags)
                plans.append((n,W,num))
            if not plans:
                return {}, already
            final={n:r['X'] for n,r in shapes.items()}
            changed=set()
            for n,W,_ in plans:
                final[n]=W;changed.add(n)
                row=shapes[n]
                if row['owner']=='placed':
                    ob=row['ob'];twin=ob.get(PV.TWIN_PROP)
                    other=twin if n==ob[PV.PROP] else ob[PV.PROP]
                    if twin and other in shapes:
                        pl_=S.pair_plane(spec,other);X=S.mirror_points(W,pl_,S.mirror_center(spec,pl_,other))
                        final[other]=X;changed.add(other)
            body_bv=PT._bvh_mm(co,tr)
            for n in changed:
                if shapes[n]['owner']=='result':
                    for k,r in shapes.items():
                        if k!=n and r['owner']=='result' and np.intersect1d(shapes[n]['idx'],r['idx']).size:
                            raise ValueError("The result shares vertices across pieces: separate them before moving")
                other=[(final[k],r['T']) for k,r in shapes.items() if k!=n]
                if PT._crossing_tris(final[n],shapes[n]['T'],other,np.zeros(len(final[n]),bool)).size:
                    raise ValueError("The selected pieces would still cross each other: press it for one piece at a time")
                for p in final[n]:
                    q,normal,_,_=body_bv.find_nearest(Vector(p))
                    if q is not None and (Vector(p)-q).dot(normal)<-1e-4:
                        raise ValueError("The mirrored correction would enter the body: move this piece with Whole Pieces")
            # No mutation until all plans exist. A refused second piece rolls back the first.
            if any(shapes[n]['owner']=='result' for n,_,_ in plans):
                for m in g.modifiers:
                    if m.type=='MESH_CACHE':m.show_viewport=m.show_render=False
                D.set_positions(g,raw)
            proxy=[n for n,_,_ in plans if shapes[n]['owner']=='proxy']
            if proxy:
                spec=PS.objects_for(context,spec,proxy, preparation=preparation)
                for n in proxy:
                    ob=PV.objects(spec).get(n)
                    hs=PV.hand_shape(ob) if ob is not None else None
                    if hs is None or hs[2].shape!=shapes[n]['X'].shape or not np.allclose(hs[2],shapes[n]['X'],atol=1e-3):
                        raise ValueError("The saved placed shape differs from the guide: show it first")
            for n,W,num in plans:
                row=shapes[n];delta=W[0]-row['X'][0]
                if row['owner']=='result':
                    M=np.asarray(g.matrix_world);loc=(W*.001-M[:3,3])@np.linalg.inv(M[:3,:3]).T
                    raw[row['idx']]=loc
                else:
                    ob=row['ob'] if row['owner']=='placed' else PV.objects(spec)[n]
                    if n!=ob[PV.PROP]:delta=delta.copy();delta[S.axis_of(S.pair_plane(spec,n))]*=-1
                    M=ob.matrix_world.copy();M.translation+=Vector(delta*.001);ob.matrix_world=M
            if raw is not None and any(shapes[n]['owner']=='result' for n,_,_ in plans):
                D.set_positions(g,raw)
            sp=PV.sync_pose(context,spec)
            if sp is not None:store.put(context,sp); PB.replan_source(preparation)
            PM.rebase_handles()
            PV.invalidate_shown()
            return {n:num for n,_,num in plans}, already
    except Exception:
        if backup.can_restore():
            backup.rollback()
            PL.restore_selection(context,selected,active)
        raise
    finally:
        backup.close()
