# SPDX-License-Identifier: GPL-3.0-or-later
"""Only outline corners connect drafting entities. Internal-only links are inactive."""
import numpy as np
from . import draft as D,iline as IL
EPS=1e-5
TOL=1.1e-4

def points(pc):
    out = {('corner',e['id']): np.asarray(e['pts'][0],float) for e in pc['edges']}
    for it in pc.get('internals',[]):
        if it.get('kind')!='baseline':
            N=IL.shape(it)[0]
            indices=range(len(N)) if IL.editable(it) else (() if it.get('closed') else (0,len(N)-1))
            out.update({('internal',it['id'],k):N[k].copy() for k in indices})
    return out

def position(pc,ref):
    if ref[0]=='corner':
        return next((np.asarray(e['pts'][0],float) for e in pc['edges'] if e['id']==ref[1]),None)
    it=next((it for it in pc.get('internals',[]) if it['id']==ref[1]),None)
    if it is None:return None
    N=IL.shape(it)[0]
    return N[ref[2]] if 0<=ref[2]<len(N) else None

def groups(pc,positions=None):
    from . import point_connection as PC
    pos=points(pc) if positions is None else positions
    per={}
    for ref in pos:
        if ref[0]!='internal':continue
        it=D.internal(pc,ref[1]);j=PC.corner(pc,it,ref[2])
        if j is not None:per.setdefault(j,[]).append(ref)
    return [(('corner',pc['edges'][j]['id']),*refs) for j,refs in per.items()]

def detach(pc,ref):
    from . import point_connection as PC
    if ref[0]=='internal':PC.detach(pc,ref[1],ref[2])
    else:
        for r in members(pc,ref):
            if r[0]=='internal':PC.detach(pc,r[1],r[2])

def members(pc,ref):
    return next((g for g in groups(pc) if ref in g),(ref,))

def _copies(spec,name,ref):
    """Actual mirrored authored nodes, including a self-reflected node."""
    from . import point_connection as PC
    pc=D.piece(spec,name);q=points(pc)[ref];out=[(name,ref)]
    if ref[0]=='internal':
        for nm,lid,M in PC._targets(spec,name,ref[1]):
            dst=D.piece(spec,nm);N=IL.shape(D.internal(dst,lid))[0]
            hits=np.flatnonzero(np.linalg.norm(N-M([q])[0],axis=1)<TOL)
            if len(hits)==1:out.append((nm,('internal',lid,int(hits[0]))))
    else:
        j=D.edge_index(pc,ref[1]);tw,cmap,_=D.twin_corner_map(spec,name)
        if cmap:
            out.append((tw,('corner',D.piece(spec,tw)['edges'][cmap[j]]['id'])))
        for nm,r in list(out):
            dst=D.piece(spec,nm);smap=D.symmetric_edit(dst)
            if smap:
                k=D.edge_index(dst,r[1]);out.append((nm,('corner',dst['edges'][smap[k]]['id'])))
    return list(dict.fromkeys(out))

def detach_pair(spec,name,ref):
    copies=_copies(spec,name,ref)
    for nm,r in copies:detach(D.piece(spec,nm),r)

def bind_pair(spec,name,a,b):
    """Explicit outline-to-internal join; never connect two internal vertices."""
    from . import point_connection as PC
    if a[0]!='corner' or b[0]!='internal':return False
    pc=D.piece(spec,name);pos=points(pc)
    if a not in pos or b not in pos or np.linalg.norm(pos[a]-pos[b])>TOL:return False
    return PC.bind_pair(spec,name,b[1],b[2])

def snapshot(spec):
    gs=[];pos={}
    for pc in spec['pieces']:
        nm=pc['name'];ps=points(pc)
        gs.extend([tuple((nm,r) for r in g) for g in groups(pc,ps)])
        pos.update({(nm,r):q for r,q in ps.items()})
    return gs,pos

def propagate(spec,before):
    """Absolute targets then residuals: a shared point moves once, not once per selected line."""
    from . import point_connection as PC
    gs,old=before;changed=[];forced={};pcs={pc['name']:pc for pc in spec['pieces']}
    for _ in range(min(64,len(gs)+4)):
        now={(pc['name'],r):q for pc in spec['pieces'] for r,q in points(pc).items()}
        jobs=[]
        for i,g in enumerate(gs):
            live=[r for r in g if r in now]
            moved=[r for r in live if np.linalg.norm(now[r]-old[r])>EPS]
            if not moved:continue
            anchors=[r for r in moved if r[1][0]=='corner']
            target=forced.get(i,np.mean([now[r] for r in anchors or moved],axis=0))
            if all(np.linalg.norm(now[r]-target)<=EPS for r in live):continue
            jobs.append((i,live,target))
        if not jobs:return list(dict.fromkeys(changed))
        with PC.raw():
            # Corners can project a request onto the piece's symmetry axis.
            for i,g,target in jobs:
                for nm,r in g:
                    if r[0]!='corner':continue
                    pc=pcs[nm];q=position(pc,r)
                    if q is None:continue
                    changed+=D.move_corners(spec,nm,{D.edge_index(pc,r[1]):tuple(target-q)})
                    target=position(pcs[nm],r);forced[i]=target.copy()
            per={};owners={}
            for i,g,target in jobs:
                target=forced.get(i,target)
                for nm,r in g:
                    if r[0]!='internal':continue
                    q=position(pcs[nm],r)
                    if q is None:continue
                    per.setdefault((nm,r[1]),{})[r[2]]=target.copy()
                    owners[(nm,r)]=(i,target)
            for (nm,lid),targets in per.items():
                N=IL.shape(D.internal(D.piece(spec,nm),lid))[0]
                changed+=IL.move_nodes(spec,nm,lid,{k:tuple(q-N[k]) for k,q in targets.items()})
            for (nm,r),(i,target) in owners.items():
                q=position(pcs[nm],r)
                if q is not None and np.linalg.norm(q-target)>EPS:forced[i]=q.copy()
    raise ValueError('Connected vertices could not keep a consistent position')

def move(spec,name,lid,deltas,which=None,twins=True):
    from . import point_connection as PC
    before=snapshot(spec)
    # Endpoint similarities and self reflection may move a connected node that was not grabbed.
    if not before[0]:return None
    # Keep the established outline carry, including open-line endpoint similarity.
    changed=PC._move_boundary(spec,name,lid,deltas)
    if changed is None:
        with PC.raw():
            changed=(D.move_internal(spec,name,lid,which,next(iter(deltas.values())),twins=twins)
                     if which is not None else IL.move_nodes(spec,name,lid,deltas))
    changed+=propagate(spec,before)
    return list(dict.fromkeys(changed))

def remap_outline(spec,before):
    """An intentional corner merge retains anchors on the surviving geometric corner."""
    old=before[1]
    for pc in spec['pieces']:
        ids={e['id'] for e in pc['edges']}
        removed={r[1] for (nm,r) in old if nm==pc['name'] and r[0]=='corner' and r[1] not in ids}
        if not removed:continue
        C=D.corners(pc)
        for it in pc.get('internals',[]):
            for key,anchor in list(it.get('boundary_nodes',{}).items()):
                if anchor not in removed:continue
                N=IL.shape(it)[0];k=int(key)
                hits=np.flatnonzero(np.linalg.norm(C-N[k],axis=1)<TOL) if 0<=k<len(N) else []
                it['boundary_nodes'][key]=pc['edges'][int(hits[0])]['id'] if len(hits)==1 else False
