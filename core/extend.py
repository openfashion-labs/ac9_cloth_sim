# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate selected boundary runs, retaining their material paths as internal lines.

The input document is never mutated. All geometry and references are planned on
one private copy, including mirrors, before the caller commits a single edit.
"""
import copy
import numpy as np
from . import draft as D, spec as S, iline as IL, point_connection as PC
from ._i18n import rpt

TOL = 0.001  # mm; geometric subdivision error, above saved coordinate rounding


def _curve(P, depth=0):
    P = np.asarray(P, float)
    chord = P[-1] - P[0]
    L = np.linalg.norm(chord)
    flat = (np.abs(chord[0] * (P[1:3, 1]-P[0, 1]) - chord[1] * (P[1:3, 0]-P[0, 0])).max() / L
            if L > 1e-12 else np.linalg.norm(P[1:3]-P[0], axis=1).max())
    # Also bound tangential excursions (collinear handles can double back).
    poly = np.linalg.norm(np.diff(P, axis=0), axis=1).sum()
    if flat <= TOL and poly-L <= TOL:
        return [P[0], P[-1]]
    if depth >= 22:
        raise ValueError(rpt("The curve cannot be checked at the saved precision"))
    a=(P[:-1]+P[1:])/2; b=(a[:-1]+a[1:])/2; c=(b[0]+b[1])/2
    return _curve([P[0],a[0],b[0],c],depth+1)[:-1] + _curve([c,b[1],a[2],P[-1]],depth+1)


def _edge_points(edge):
    if edge.get('ctrl'):
        return np.asarray(_curve([edge['pts'][0], *edge['ctrl'], edge['pts'][-1]]))
    return np.asarray(edge['pts'], float)


def _ring(pc):
    return np.vstack([_edge_points(e)[:-1] for e in pc['edges']])


def _simple(P):
    """Reject crossings, nonadjacent touches and overlapping segments."""
    Q=np.roll(P,-1,axis=0); n=len(P)
    for i in range(n):
        js=np.arange(i+1,n)
        js=js[(js!=i+1) & ~((i==0)&(js==n-1))]
        if not len(js):continue
        a,b=P[i],Q[i]; c,d=P[js],Q[js]
        ab=b-a; cd=d-c
        cross=lambda x,y:x[...,0]*y[...,1]-x[...,1]*y[...,0]
        den=cross(ab,cd); ca=c-a
        parallel=np.abs(den)<1e-12
        t=cross(ca,cd)/np.where(parallel,1,den)
        u=cross(ca,ab)/np.where(parallel,1,den)
        if np.any(~parallel & (t>=-1e-9)&(t<=1+1e-9)&(u>=-1e-9)&(u<=1+1e-9)):
            return False
        col=parallel & (np.abs(cross(ca,ab))<1e-9)
        axis=int(np.argmax(np.abs(ab)))
        if np.any(col & (np.maximum(np.minimum(a[axis],b[axis]),np.minimum(c[:,axis],d[:,axis]))
                         <=np.minimum(np.maximum(a[axis],b[axis]),np.maximum(c[:,axis],d[:,axis]))+1e-9)):
            return False
    # Adjacent segments must not double back along one another.
    U=P-np.roll(P,1,axis=0); V=Q-P
    cr=U[:,0]*V[:,1]-U[:,1]*V[:,0]
    return not np.any((np.abs(cr)<1e-9)&((U*V).sum(1)<-1e-9))


def _where(P, ring):
    from .dart import _inside
    boundary=np.vstack([ring,ring[:1]])
    dist=np.concatenate([D._dist_to_polyline(P[i:i+128],boundary) for i in range(0,len(P),128)])
    inside=np.concatenate([_inside(ring,P[i:i+128]) for i in range(0,len(P),128)])
    return inside,dist


def _crosses_boundary(P, ring):
    """The open retained path may touch the new boundary only at its two ends."""
    C=ring;E=np.roll(ring,-1,axis=0);cd=E-C
    cross=lambda x,y:x[...,0]*y[...,1]-x[...,1]*y[...,0]
    for a,b in zip(P[:-1],P[1:]):
        ab=b-a;ca=C-a;den=cross(ab,cd);parallel=np.abs(den)<1e-12
        t=cross(ca,cd)/np.where(parallel,1,den);u=cross(ca,ab)/np.where(parallel,1,den)
        hits=np.flatnonzero(~parallel&(t>=-1e-9)&(t<=1+1e-9)&(u>=-1e-9)&(u<=1+1e-9))
        for j in hits:
            q=a+t[j]*ab
            if min(np.linalg.norm(q-P[0]),np.linalg.norm(q-P[-1]))>TOL*2:return True
        col=np.flatnonzero(parallel&(np.abs(cross(ca,ab))<1e-9))
        ll=float(ab@ab)
        if ll<1e-15:continue
        for j in col:
            ts=sorted([float((C[j]-a)@ab)/ll,float((E[j]-a)@ab)/ll])
            lo,hi=max(0.,ts[0]),min(1.,ts[1])
            if lo>hi+1e-9:continue
            if (hi-lo)*np.sqrt(ll)>TOL*2:return True
            q=a+(lo+hi)*.5*ab
            if min(np.linalg.norm(q-P[0]),np.linalg.norm(q-P[-1]))>TOL*2:return True
    return False


def _edge_id(used):
    k=len(used)+1
    while 'E'+str(k) in used:k+=1
    value='E'+str(k);used.add(value);return value


def _targets(spec, selected_vectors, twins):
    plans={};pending=[]
    def add(nm,eid,d):
        pc=D.piece(spec,nm);D.edge_index(pc,eid)
        old=plans.setdefault(nm,{}).get(eid)
        if old is not None:
            if not np.allclose(old,d,atol=1e-7,rtol=0):
                raise ValueError(rpt("The selected direction conflicts with mirrored editing: {0}",nm))
            return
        plans[nm][eid]=np.asarray(d,float).copy()
        pending.append((nm,eid,plans[nm][eid]))
    for nm,ds in selected_vectors.items():
        for eid,d in ds.items():add(nm,eid,d)
    maps={};cursor=0
    while twins and cursor<len(pending):
        nm,eid,delta=pending[cursor];cursor+=1
        if nm not in maps:
            pc=D.piece(spec,nm)
            tw,cmap,_=D.twin_corner_map(spec,nm)
            if tw and cmap is None:
                raise ValueError(rpt("The mirrored outline no longer matches: {0}",nm))
            maps[nm]=(pc,tw,cmap,D.symmetric_edit(pc))
        pc,tw,cmap,selfmap=maps[nm]
        if tw:
            dst=D.piece(spec,tw)
            j,_=D._twin_edge(pc,dst,cmap,D.edge_index(pc,eid))
            if j is None:raise ValueError(rpt("The mirrored edge cannot be found: {0}",eid))
            add(tw,dst['edges'][j]['id'],D.twin_vec(pc,dst,delta))
        if selfmap is not None:
            j,_=D._twin_edge(pc,pc,selfmap,D.edge_index(pc,eid))
            if j is None:raise ValueError(rpt("The mirrored edge cannot be found: {0}",eid))
            add(nm,pc['edges'][j]['id'],D.reflect_vec(pc,delta))
    return plans


def _width_edges(pc, ids, distance, individual=False):
    """Parallel segment intersections without a miter cap; retain one ID per source edge."""
    result={};sign=np.sign(D.signed_area(_ring(pc)))
    runs=([e['id']] for e in pc['edges'] if e['id'] in ids) if individual else D.edge_chains(pc,ids)
    for run in runs:
        parts=[_edge_points(pc['edges'][D.edge_index(pc,eid)]) for eid in run]
        closed=len(run)==len(pc['edges'])
        P=np.vstack([part[:-1] for part in parts]+[parts[-1][-1:]])
        T=np.diff(P,axis=0);lengths=np.linalg.norm(T,axis=1)
        if np.any(lengths<1e-8):raise ValueError(rpt('The outline has a segment too short to extend'))
        T/=lengths[:,None]
        normals=sign*np.column_stack([T[:,1],-T[:,0]])
        if closed:
            N=normals+np.roll(normals,1,axis=0)
            den=1+(normals*np.roll(normals,1,axis=0)).sum(1)
            if np.any(den<1e-12):raise ValueError(rpt('The outline turns back too tightly to extend'))
            Q=P[:-1]+distance*N/den[:,None];Q=np.vstack([Q,Q[:1]])
        else:
            N=normals[:-1]+normals[1:];den=1+(normals[:-1]*normals[1:]).sum(1)
            if np.any(den<1e-12):raise ValueError(rpt('The outline turns back too tightly to extend'))
            Q=np.vstack([P[:1]+distance*normals[:1],P[1:-1]+distance*N/den[:,None],P[-1:]+distance*normals[-1:]])
        if individual:
            edge=pc['edges'][D.edge_index(pc,run[0])]
            if edge.get('ctrl'):
                a,b=np.asarray(edge['pts'][0]),np.asarray(edge['pts'][-1]);h0,h1=np.asarray(edge['ctrl'])
                start=next((v/np.linalg.norm(v) for v in (h0-a,h1-a,b-a) if np.linalg.norm(v)>1e-12),None)
                end=next((v/np.linalg.norm(v) for v in (b-h1,b-h0,b-a) if np.linalg.norm(v)>1e-12),None)
                if start is not None:Q[0]=a+distance*sign*np.array([start[1],-start[0]])
                if end is not None:Q[-1]=b+distance*sign*np.array([end[1],-end[0]])
        Q=np.asarray(D._r(Q))
        if np.any(np.einsum('ij,ij->i',np.diff(Q,axis=0),T)<=1e-8):
            raise ValueError(rpt('This width would erase or reverse an outline segment'))
        offset=0
        for eid,part in zip(run,parts):
            result[eid]=Q[offset:offset+len(part)].copy();offset+=len(part)-1
    return result




def _piece(spec, name, vectors, width=None, closed_start=None, individual=False):
    pc=D.piece(spec,name);old=copy.deepcopy(pc);ol=S.Outline(old)
    runs=D.edge_chains(old,vectors)
    closed=width is not None and len(vectors)==len(old['edges'])
    offsets=_width_edges(old,vectors,width,individual) if width is not None else None
    if len(vectors)==len(old['edges']) and not closed:
        raise ValueError(rpt("{0}: the whole outline cannot be retained inside its translated copy",name))
    links=[]
    for it in pc.get('internals',[]):
        for k in range(len(IL.shape(it)[0])):
            j=PC.corner(old,it,k)
            if j is not None:links.append((it['id'],k,D.corners(old)[j].copy()))
    used={e['id'] for e in old['edges']};starts={};ends={};paths=[]
    for run in runs:
        edges=[old['edges'][D.edge_index(old,eid)] for eid in run]
        d=vectors[run[0]]
        if offsets is None and any(not np.allclose(d,vectors[eid],atol=1e-7,rtol=0) for eid in run):
            raise ValueError(rpt("Connected mirrored edges require different directions: {0}",name))
        parts=[np.asarray(e['pts'],float) for e in edges]
        C=[np.asarray(e['ctrl'],float) if e.get('ctrl') else None for e in edges]
        N=[p[0] for p in parts]+[parts[-1][-1]]
        origin=0.;direction=1
        if closed and closed_start is not None:
            base=np.asarray(N[:-1]);corner=D.corners(old)[closed_start]
            k=int(np.linalg.norm(base-corner,axis=1).argmin());n=len(base)
            order=[(k-j)%n for j in range(n)]
            N=[base[j] for j in order];N.append(N[0])
            parts=[parts[(j-1)%n][::-1] for j in order]
            C=[None if C[(j-1)%n] is None else C[(j-1)%n][::-1] for j in order]
            start=ol.s_of(run[0],0)
            origin=((ol.s_of(old['edges'][closed_start]['id'],0)-start)%ol.L)/ol.L
            direction=-1
        lid=D.add_internal(spec,name,N[:-1] if closed else N,closed=closed,mirror=False)
        it=D.internal(pc,lid);authored=N[:-1] if closed else N
        if any(c is None and len(p)>2 for p,c in zip(parts,C)):
            IL.set_spans(it,parts,C)
        else:
            if len(authored)>IL.DENSE:
                it.update(nodes=list(range(len(authored))),ctrl=[None]*len(C))
            IL.set_shape(it,authored,C)
        if not closed:
            first=offsets[run[0]][0] if offsets is not None else N[0]+d
            last=offsets[run[-1]][-1] if offsets is not None else N[-1]+d
            starts[run[0]]={'id':_edge_id(used),'pts':D._r([N[0],first])}
            ends[run[-1]]={'id':_edge_id(used),'pts':D._r([last,N[-1]])}
        paths.append(dict(ids=run,line=lid,start=ol.s_of(run[0],0),closed=closed,station_origin=origin,station_direction=direction,
                          length=sum(ol.edges[eid][1] for eid in run)))
    contacts=[]
    if individual:
        for i,edge in enumerate(old['edges']):
            previous=old['edges'][(i-1)%len(old['edges'])]
            if edge['id'] not in vectors or previous['id'] not in vectors:continue
            left,right=offsets[previous['id']][-1],offsets[edge['id']][0]
            if np.linalg.norm(left-right)<=TOL:
                # Tangent-continuous logical spans share a side, never a slit.
                q=np.asarray(D._r([(left+right)/2]))[0]
                offsets[previous['id']][-1]=q;offsets[edge['id']][0]=q
                continue
            q=np.asarray(edge['pts'][0],float);contacts.append(q)
            ends[previous['id']]={'id':_edge_id(used),'pts':D._r([left,q])}
            starts[edge['id']]={'id':_edge_id(used),'pts':D._r([q,right])}
    new=[]
    for i,edge in enumerate(old['edges']):
        eid=edge['id']
        if eid in starts:new.append(starts[eid])
        e=copy.deepcopy(edge)
        if eid in vectors:
            if offsets is not None:
                e['pts']=D._r(offsets[eid]);e.pop('ctrl',None)
            else:
                e['pts']=D._r(np.asarray(e['pts'])+vectors[eid])
                if e.get('ctrl'):e['ctrl']=D._r(np.asarray(e['ctrl'])+vectors[eid])
        if eid in starts or old['edges'][(i-1)%len(old['edges'])]['id'] in ends:e.pop('smooth',None)
        new.append(e)
        if eid in ends:new.append(ends[eid])
    pc['edges']=new
    B=_ring(pc)
    if not np.isfinite(B).all() or not _simple(B) or D.signed_area(B)<=D.signed_area(_ring(old))+1e-7:
        raise ValueError(rpt("{0}: the extension overlaps or does not add fabric",name))
    oldB=_ring(old);samples=np.vstack([oldB,(oldB+np.roll(oldB,-1,axis=0))/2])
    inside,dist=_where(samples,B)
    if not np.all(inside|(dist<=TOL*2)):
        raise ValueError(rpt("{0}: the original boundary would leave the piece",name))
    for path in paths:
        P=np.vstack([_edge_points(old['edges'][D.edge_index(old,e)])[:-1] for e in path['ids']]
                    +[np.asarray(old['edges'][D.edge_index(old,path['ids'][-1])]['pts'][-1:])])
        segments=[P]
        if contacts:
            cuts=[k for k,q in enumerate(P[:-1] if path['closed'] else P)
                  if any(np.linalg.norm(q-c)<PC.EPS for c in contacts)]
            if cuts and path['closed']:
                k=cuts[0];P=np.vstack([P[k:-1],P[:k+1]])
                cuts=[j for j,q in enumerate(P[:-1]) if any(np.linalg.norm(q-c)<PC.EPS for c in contacts)]
            cuts=sorted(set([0,len(P)-1]+cuts))
            segments=[P[a:z+1] for a,z in zip(cuts[:-1],cuts[1:])]
        for segment in segments:
            samples=np.vstack([segment[1:-1],(segment[:-1]+segment[1:])/2])
            inside,dist=_where(samples,B)
            if _crosses_boundary(segment,B) or not np.all(inside & (dist>TOL*2)):
                raise ValueError(rpt("{0}: the retained line must lie inside the extended piece",name))
    if individual:
        # Bands keep their original logical edges as independent editing/fold objects.
        # Outline connectors and their validation above still use the original runs.
        independent=[]
        for path in paths:
            if len(path['ids'])==1:
                independent.append(path);continue
            pc['internals']=[it for it in pc.get('internals',[]) if it['id']!=path['line']]
            for eid in path['ids']:
                edge=old['edges'][D.edge_index(old,eid)]
                P=np.asarray(edge['pts'],float)
                C=np.asarray(edge['ctrl'],float) if edge.get('ctrl') else None
                lid=D.add_internal(spec,name,[P[0],P[-1]],mirror=False)
                it=D.internal(pc,lid)
                if C is None and len(P)>2:IL.set_spans(it,[P],[None])
                else:IL.set_shape(it,[P[0],P[-1]],[C])
                independent.append(dict(ids=[eid],line=lid,start=ol.s_of(eid,0),closed=False,
                                        station_origin=0.,station_direction=1,length=ol.edges[eid][1]))
        paths=independent
    corners=D.corners(pc)
    for lid,k,q in links:
        hits=np.flatnonzero(np.linalg.norm(corners-q,axis=1)<PC.EPS)
        D.internal(pc,lid).setdefault('boundary_nodes',{})[str(k)]=pc['edges'][int(hits[0])]['id'] if len(hits)==1 else False
    for path in paths:
        it=D.internal(pc,path['line'])
        for k,q in enumerate(IL.shape(it)[0]):
            if np.any(np.linalg.norm(corners-q,axis=1)<PC.EPS):PC.bind(pc,it['id'],k)
    # A zipper's whole-edge list must also include any newly intervening connectors.
    for zipper in spec.get('zippers',[]):
        for key in ('a','b'):
            side=zipper[key]
            if side['piece']!=name:continue
            members=set(side['edges']);added=set()
            for i,e in enumerate(old['edges']):
                eid=e['id']
                if eid in members and old['edges'][(i-1)%len(old['edges'])]['id'] in members and eid in starts:
                    added.add(starts[eid]['id'])
                if eid in members and old['edges'][(i+1)%len(old['edges'])]['id'] in members and eid in ends:
                    added.add(ends[eid]['id'])
            side['edges']=[e['id'] for e in new if e['id'] in members|added]
    return dict(old=old,outline=ol,paths=paths)


def _boundaries(info,side):
    ol=info['outline'];s0=ol.s_of(*side['from']);span=ol.span(side)
    cuts=[]
    for path in info['paths']:
        for q in (path['start'],path['start']+path['length']):
            u=((q-s0)%ol.L if span>0 else (s0-q)%ol.L)/abs(span) if abs(span)>1e-10 else 0
            if 1e-8<u<1-1e-8:cuts.append(u)
    return cuts


def _outline_end(ol,s,direction):
    # Resolve a corner using the segment *inside* the sliced interval.
    eid,_=ol.edge_t(s+direction*1e-7)
    start,L,_=ol.edges[eid]
    t=((s-start)%ol.L)/L
    if t>1 and abs(t-ol.L/L)<1e-6:t=0
    return [eid,round(float(np.clip(t,0,1)),6)]


def _slice(spec,side,u,v,info):
    if not info and u == 0 and v == 1:
        return copy.deepcopy(side)
    if 'line' in side:
        from .line_track import split_side
        part=split_side(side,v)[0]
        return split_side(part,u/v)[1] if u else part
    ol=info['outline'] if info else S.Outline(D.piece(spec,side['piece']))
    s0=ol.s_of(*side['from']);span=ol.span(side)
    a,b=s0+span*u,s0+span*v;mid=(a+b)/2
    out=copy.deepcopy(side)
    if info:
        eid,_=ol.edge_t(mid)
        path=next((p for p in info['paths'] if eid in p['ids']),None)
        if path:
            m=(mid-path['start'])%ol.L
            out.pop('dir',None)
            out.update(line=path['line'],wrap=0)
            out['from']=round((m+(a-mid))/path['length'],8)
            out['to']=round((m+(b-mid))/path['length'],8)
            if path.get('closed'):
                va=(out['from']-path['station_origin'])*path['station_direction']
                vb=(out['to']-path['station_origin'])*path['station_direction']
                f,t=round(va%1,8),round(vb%1,8)
                out.update({'from':f,'to':t,'wrap':int(round((vb-va)-(t-f)))})
            return out
    out['from']=_outline_end(ol,a,span)
    out['to']=_outline_end(ol,b,-span)
    return out


def _seams(before,out,infos):
    from .dart import _seam_id
    out['seams']=[];children={}
    # Allocate against every original ID as well as new children.
    reserved=copy.deepcopy(before.get('seams',[]))
    for sm in before.get('seams',[]):
        cuts=[0.,1.]
        for key in ('a','b'):
            side=sm[key];info=infos.get(side['piece'])
            if info and 'line' not in side:cuts.extend(_boundaries(info,side))
        cuts=sorted(cuts);cuts=[x for i,x in enumerate(cuts) if not i or x-cuts[i-1]>1e-8]
        for j,(u,v) in enumerate(zip(cuts[:-1],cuts[1:])):
            child=copy.deepcopy(sm)
            if j:
                sid=_seam_id({'seams':reserved});reserved.append({'id':sid});child['id']=sid
                children.setdefault(sm['id'],[]).append(sid)
            for key in ('a','b'):
                sd=sm[key]
                child[key]=_slice(before,sd,u,v,infos.get(sd['piece']))
            out['seams'].append(child)
    for field in ('folds','attach'):
        rows=out.get(field,[])
        for row in list(rows):
            for sid in children.get(row.get('seam'),[]):
                child=copy.deepcopy(row);child['seam']=sid
                if field=='attach' and 'id' in child:
                    used={x.get('id') for x in rows}
                    while child['id'] in used:child['id']+='c'
                rows.append(child)


def plan(spec, selected, delta, move_seams=False, twins=True):
    """Return (private document, changed pieces, created line IDs), or change nothing."""
    d=np.asarray(delta,float)
    if d.shape!=(2,) or not np.isfinite(d).all() or np.linalg.norm(d)<1e-6:
        raise ValueError(rpt("Enter an extension in X or Y"))
    if not selected or not any(selected.values()):raise ValueError(rpt("Select an outline edge to extend"))
    vectors={nm:{eid:d for eid in ids} for nm,ids in selected.items()}
    return _plan(spec,vectors,move_seams,twins)


def plan_axis(spec, selected, axis, distance, move_seams=False, twins=True):
    """Translate each connected selected run outward along the chosen pattern axis."""
    if axis not in ('X','Y'):
        raise ValueError(rpt("Choose X axis or Y axis"))
    if not np.isfinite(distance) or distance<1e-6:
        raise ValueError(rpt("Enter a positive extension distance"))
    vectors={}
    j=0 if axis=='X' else 1
    for name,ids in selected.items():
        if not ids:continue
        pc=D.piece(spec,name)
        for eid in ids:D.edge_index(pc,eid)
        if len(set(ids))==len(pc['edges']):
            raise ValueError(rpt("{0}: the whole outline cannot be retained inside its translated copy",name))
        sign=np.sign(D.signed_area(_ring(pc)))
        for run in D.edge_chains(pc,ids):
            first=pc['edges'][D.edge_index(pc,run[0])]
            last=pc['edges'][D.edge_index(pc,run[-1])]
            t=np.asarray(last['pts'][-1],float)-first['pts'][0]
            projection=sign*(t[1] if j==0 else -t[0])
            if abs(projection)<=TOL:
                raise ValueError(rpt("{0} / {1}: outward is undefined on this axis; choose another axis or X/Y displacement",name,run[0]))
            d=np.zeros(2);d[j]=np.sign(projection)*distance
            vectors.setdefault(name,{}).update({eid:d for eid in run})
    return _plan(spec,vectors,move_seams,twins)


def plan_width(spec, selected, distance, move_seams=False, twins=True, individual=False):
    """Grow separate boundary runs by a perpendicular width, joining corners at their intersections."""
    if not np.isfinite(distance) or distance<1e-6:
        raise ValueError(rpt('Enter a positive extension distance'))
    vectors={name:{eid:np.zeros(2) for eid in ids} for name,ids in selected.items() if ids}
    return _plan(spec,vectors,move_seams,twins,width=float(distance),individual=individual)


def _plan(spec, selected_vectors, move_seams, twins, width=None, individual=False):
    if not selected_vectors or not any(selected_vectors.values()):raise ValueError(rpt("Select an outline edge to extend"))
    out=copy.deepcopy(spec)
    vectors=_targets(out,selected_vectors,twins)
    from . import internal_sync as IS
    # Persist unambiguous legacy correspondences before coincident retained
    # paths are added. Otherwise the old, previously unique pair can become
    # ambiguous even though the new pair itself has an explicit identity.
    for name in vectors:
        for it in D.piece(spec,name).get('internals',[]):
            try:peer=IS.match(spec,name,it['id'])
            except ValueError:continue  # Preserve a pre-existing ambiguity.
            if peer:IS.bind(out,name,it['id'],*peer)
    closed_starts={}
    if width is not None:
        for name,ds in vectors.items():
            other=IS.partner(spec,name)
            if other not in vectors or name<other:continue
            pc,peer=D.piece(spec,name),D.piece(spec,other)
            if len(ds)!=len(pc['edges']) or len(vectors[other])!=len(peer['edges']):continue
            _,cmap,_=D.twin_corner_map(spec,other)
            if cmap is None:raise ValueError(rpt('The mirrored outline no longer matches: {0}',name))
            run=D.edge_chains(peer,vectors[other])[0]
            closed_starts[name]=cmap[D.edge_index(peer,run[0])]
    infos={name:_piece(out,name,ds,width=width,closed_start=closed_starts.get(name),individual=individual) for name,ds in vectors.items()}
    # New reference lines can coincide with pre-existing drafting lines. Bind
    # known run counterparts by the original edge mapping, never proximity.
    for name,info in infos.items():
        other=IS.partner(spec,name)
        if other not in infos or name>=other:continue
        pa,pb=D.piece(spec,name),D.piece(spec,other)
        _,cmap,_=D.twin_corner_map(spec,name)
        if cmap is None:continue
        for path in info['paths']:
            mirrored=set()
            for eid in path['ids']:
                j,_=D._twin_edge(pa,pb,cmap,D.edge_index(pa,eid));mirrored.add(pb['edges'][j]['id'])
            peer=next((p for p in infos[other]['paths'] if set(p['ids'])==mirrored),None)
            if peer:IS.bind(out,name,path['line'],other,peer['line'])
    if not move_seams:_seams(spec,out,infos)
    bad=D.problems(out,list(infos))
    if bad:raise ValueError(bad[0])
    return out,sorted(infos),{nm:[p['line'] for p in i['paths']] for nm,i in infos.items()}
