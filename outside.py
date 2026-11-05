# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit outside corrections on current shapes, independent of their owner.

No placement reconstruction or persistent parent/layer relationship is involved.
Distances are world millimeters. Triangle winding defines a cloth's front.
"""
import json
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from . import selection as SEL


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


RING_REFUSALS = ("The cloth fronts point in opposing directions: select a smaller piece",
                 "The cloth front is ambiguous at a fold: move this piece by hand")


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
    """Clip a convex projected polygon by one linear half-plane."""
    if not len(poly):return []
    out=[];prev=poly[-1];dp=float(np.dot(prev,coefficient[:2])+coefficient[2]-threshold)
    for cur in poly:
        dc=float(np.dot(cur,coefficient[:2])+coefficient[2]-threshold)
        if (dc>=0)!=(dp>=0):out.append(prev+(cur-prev)*dp/(dp-dc))
        if dc>=0:out.append(cur)
        prev,dp=cur,dc
    return out


def _clip_triangle(poly, triangle):
    area=np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0])
    sign=1.0 if area>0 else -1.0
    for a,b in zip(triangle,np.roll(triangle,-1,axis=0)):
        edge=b-a
        normal=np.array([-edge[1],edge[0]])*sign
        poly=_clip_half(poly,np.r_[normal,-np.dot(normal,a)],-1e-9)
        if not len(poly):break
    return poly


def _area(poly):
    if len(poly)<3:return 0.0
    q=np.asarray(poly)
    return abs(float(np.sum(q[:,0]*np.roll(q[:,1],-1)-q[:,1]*np.roll(q[:,0],-1))))*.5


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
        raise ValueError("The cloth front is ambiguous at a fold: move this piece by hand")
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
    for a in A:
        mat=np.c_[a[:,:2],np.ones(3)]
        if abs(np.linalg.det(mat))<1e-9:
            continue  # Its zero-area projection cannot license another area.
        za=np.linalg.solve(mat,a[:,2])
        candidates=np.nonzero(((lo<=a[:,:2].max(0))&(hi>=a[:,:2].min(0))).all(1))[0]
        for j in candidates:
            db=heights[j]-za
            # Cheap interval bound before polygon operations.
            bound=np.c_[a[:,:2],np.ones(3)]@db
            if bound.max()<=tol or bound.min()>=amount-tol:continue
            poly=_clip_triangle(list(a[:,:2]),B[j,:,:2])
            poly=_clip_half(poly,db,tol)
            poly=_clip_half(poly,-db,-amount+tol)
            if _area(poly)<1e-8:continue
            if permitted is not None and not permitted[j]:
                raise ValueError("Another cloth layer blocks the outside correction: move this piece by hand")
            if determinant[j]<0:
                raise ValueError("Another cloth layer blocks the outside correction: move this piece by hand")
            p=np.asarray(poly);p_h=np.c_[p,np.ones(len(p))]
            near=np.nonzero(((lo<=p.max(0))&(hi>=p.min(0))).all(1))[0]
            for k in near:
                if k==j:continue
                if any((stitch,1-side) in face_tags[k] for stitch,side in face_tags[j]):
                    continue  # Existing opposing stitched-triangle contact exception, with no ring expansion.
                dc=heights[k]-za
                minus,plus=db-dc,db+dc
                if (p_h@minus).max()<=tol or (p_h@plus).max()<=tol:continue
                region=_clip_triangle(poly,B[k,:,:2])
                region=_clip_half(region,minus,tol)
                region=_clip_half(region,plus,tol)
                if _area(region)>1e-8:
                    raise ValueError("Another cloth layer blocks the outside correction: move this piece by hand")


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
        raise ValueError("The outside correction still crosses other cloth: move this piece by hand")
    inside=0
    for p in W:
        q,n,_,_=body_bv.find_nearest(Vector(p))
        if q is not None and (Vector(p)-q).dot(n)<-1e-4:inside+=1
    if inside:
        raise ValueError("The outside correction would enter the body: move this piece by hand")
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
            raise ValueError("The cloth front is ambiguous at a fold: move this piece by hand")
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
            raise ValueError("The body blocks the outside correction: move this piece by hand")
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
    for a in A:
        mat = np.c_[a[:, :2], np.ones(3)]
        if abs(np.linalg.det(mat)) < 1e-9:
            continue
        za = np.linalg.solve(mat, a[:, 2])
        ids=np.nonzero(((lo<=a[:,:2].max(0)+1e-8)&(hi>=a[:,:2].min(0)-1e-8)).all(1))[0]
        if not len(ids):continue
        b=B[ids,:,:2];aa=a[:,:2]
        ae=np.roll(aa,-1,axis=0)-aa;be=np.roll(b,-1,axis=1)-b
        sa=np.sign(cross(ae[0],ae[1]));sb=np.sign(cross(be[:,0],be[:,1]))
        ainb=(cross(be[:,:,None,:],aa[None,None,:,:]-b[:,:,None,:])*sb[:,None,None]>=-1e-8).all(1)
        bina=(cross(ae[None,:,None,:],b[:,None,:,:]-aa[None,:,None,:])*sa>=-1e-8).all(1)
        den=cross(ae[None,:,None,:],be[:,None,:,:]);nz=abs(den)>1e-10
        delta=b[:,None,:,:]-aa[None,:,None,:]
        ta=np.divide(cross(delta,be[:,None,:,:]),den,out=np.zeros_like(den),where=nz)
        tb=np.divide(cross(delta,ae[None,:,None,:]),den,out=np.zeros_like(den),where=nz)
        on=nz&(ta>=-1e-8)&(ta<=1+1e-8)&(tb>=-1e-8)&(tb<=1+1e-8)
        pts=np.concatenate([np.broadcast_to(aa,(len(b),3,2)),b,(aa[None,:,None,:]+ta[:,:,:,None]*ae[None,:,None,:]).reshape(-1,9,2)],axis=1)
        mask=np.concatenate([ainb,bina,on.reshape(-1,9)],axis=1)
        dz=bz[ids]-za
        z=np.einsum('nki,ni->nk',pts,dz[:,:2])+dz[:,2,None]
        if mask.any():
            zm=np.where(mask,z,-np.inf)
            if zm.max()>dist:
                dist=float(zm.max());furthest=int(np.nonzero(valid)[0][ids[np.unravel_index(zm.argmax(),zm.shape)[0]]])
            hits+=int(mask.any(1).sum())
    if not hits:
        raise ValueError("No overlapping cloth surface found")
    amount=max(float(gap),dist+float(gap))
    return Y+d*amount, dict(distance=amount, overlap_pairs=hits, direction=d.tolist(), furthest_triangle=furthest)


def snapshot(context, spec):
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
            X=X.copy(); X[:,0]=2*PV.center_x(spec)-X[:,0]; T=T[:,::-1]
            pcs={pc['name']:pc for pc in spec['pieces']}
            V=PV.mirror_flat(V,S.Outline(pcs[ob[PV.PROP]]).B,S.Outline(pcs[name]).B)
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
        res=D.prepare(context,context.scene.ac9_cloth_sim.mesh_size,shapes_only=True,placed_only=True)
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


def move_selected(context, names, ring_ok=False):
    """Plan every selected piece from one snapshot; commit only after validation.

    ring_ok: a piece the outside step cannot move in one direction (its partner wraps around it) is left where it
    is when it is already clear of the cloth and the body (Part to Seam, which bent it out first)."""
    from . import store, placed_view as PV, placement as PL, dress as D, piece_move as PM
    from . import place_seam as PS
    if context.mode != 'OBJECT':
        raise ValueError("Return to Object Mode before correcting the current shapes")
    spec = store.get(context)[0]
    selected=[ob.name for ob in SEL.objects(context)]
    active=context.view_layer.objects.active.name if context.view_layer.objects.active else None
    backup = PV.PreparedResetBackup(context, spec)
    try:
        shapes,g,raw = snapshot(context,spec)
        if not names or any(n not in shapes for n in names):
            raise ValueError("Select pieces with a current 3D shape first")
        tags={}
        if spec.get('seams'):
            res=D.prepare(context,context.scene.ac9_cloth_sim.mesh_size,shapes_only=True,placed_only=True)
            tags=seam_tags(shapes,res['pat'],spec)
        plans,seen=[],set()
        co,tr=PL.body_arrays(context,context.scene.ac9_cloth_sim.body)
        for n in names:
            row=shapes[n];ob=row['ob']
            key=ob.name if row['owner']=='placed' else n
            if key in seen:continue
            seen.add(key)
            excluded=set(names)
            if row['owner']=='placed':
                excluded|={ob.get(PV.PROP),ob.get(PV.TWIN_PROP)}
            others=[(r['X'],r['T']) for k,r in shapes.items() if k not in excluded]
            paired=False
            if row['owner']=='placed' and ob.get(PV.TWIN_PROP):
                other=ob[PV.TWIN_PROP] if n==ob[PV.PROP] else ob[PV.PROP]
                paired=other in shapes
            try:
                W,num=correct(row['X'],row['T'],others,(co,tr),8.0,tags=tags,validate=not paired)
            except ValueError as exc:
                # A partner that wraps around (Part to Seam has already bent the piece out of it): nothing to move.
                if not (ring_ok and not paired and str(exc) in RING_REFUSALS and _clear(row['X'],row['T'],others,co,tr)):
                    raise
                plans.append((n,row['X'].copy(),dict(distance=0.0,ring=True)))
                continue
            if row['owner']=='placed' and ob.get(PV.TWIN_PROP):
                twin=ob[PV.TWIN_PROP] if n==ob[PV.PROP] else ob[PV.PROP]
                if twin in shapes:
                    d=np.asarray(num['direction']);dm=d.copy();dm[0]*=-1
                    tw=shapes[twin]
                    _,tn=correct(tw['X'],tw['T'],others,(co,tr),8.0,direction=dm,tags=tags,validate=False)
                    amount=max(num['distance'],tn['distance'])
                    W,num=correct(row['X'],row['T'],others,(co,tr),8.0,direction=d,minimum=amount,tags=tags)
                    correct(tw['X'],tw['T'],others,(co,tr),8.0,direction=dm,minimum=amount,tags=tags)
            plans.append((n,W,num))
        final={n:r['X'] for n,r in shapes.items()}
        changed=set()
        for n,W,_ in plans:
            final[n]=W;changed.add(n)
            row=shapes[n]
            if row['owner']=='placed':
                ob=row['ob'];twin=ob.get(PV.TWIN_PROP)
                other=twin if n==ob[PV.PROP] else ob[PV.PROP]
                if twin and other in shapes:
                    X=W.copy();X[:,0]=2*PV.center_x(spec)-X[:,0]
                    final[other]=X;changed.add(other)
        from . import place_settle as PT
        body_bv=PT._bvh_mm(co,tr)
        for n in changed:
            if shapes[n]['owner']=='result':
                for k,r in shapes.items():
                    if k!=n and r['owner']=='result' and np.intersect1d(shapes[n]['idx'],r['idx']).size:
                        raise ValueError("The result shares vertices across pieces: separate them before moving")
            other=[(final[k],r['T']) for k,r in shapes.items() if k!=n]
            if PT._crossing_tris(final[n],shapes[n]['T'],other,np.zeros(len(final[n]),bool)).size:
                raise ValueError("The selected pieces would still cross cloth: move them separately")
            for p in final[n]:
                q,normal,_,_=body_bv.find_nearest(Vector(p))
                if q is not None and (Vector(p)-q).dot(normal)<-1e-4:
                    raise ValueError("The mirrored correction would enter the body: move this piece by hand")
        # No mutation until all plans exist. A refused second piece rolls back the first.
        if any(shapes[n]['owner']=='result' for n,_,_ in plans):
            for m in g.modifiers:
                if m.type=='MESH_CACHE':m.show_viewport=m.show_render=False
            D.set_positions(g,raw)
        proxy=[n for n,_,_ in plans if shapes[n]['owner']=='proxy']
        if proxy:
            spec=PS.objects_for(context,spec,proxy)
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
                if n!=ob[PV.PROP]:delta=delta.copy();delta[0]*=-1
                M=ob.matrix_world.copy();M.translation+=Vector(delta*.001);ob.matrix_world=M
        if raw is not None and any(shapes[n]['owner']=='result' for n,_,_ in plans):
            D.set_positions(g,raw)
        sp=PV.sync_pose(context,spec)
        if sp is not None:store.put(context,sp)
        PM.rebase_handles()
        PV.invalidate_shown()
        return {n:num for n,_,num in plans}
    except Exception:
        backup.rollback()
        PL.AC9_OT_SimMoveByHand._restore_selection(context,selected,active)
        raise
    finally:
        backup.close()
