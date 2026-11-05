"""Material-coordinate fold helpers.

Terminal targets are used by preparation and Live; they are not a stress-free
geometric reference. The straight-profile geometry helpers remain experimental.
No placed or simulated positions define the terminal target angles.
"""
import numpy as np
import hashlib
import json


def profile_breaks(deg, radius_mm=2.0, segments=4):
    if not np.isfinite(deg) or not 0 < abs(deg) <= 180:
        raise ValueError('A finite nonzero angle of at most 180 degrees is required')
    if not np.isfinite(radius_mm) or radius_mm <= 0 or int(segments) != segments or segments < 2:
        raise ValueError('A positive radius and at least two segments are required')
    return np.linspace(0.0, radius_mm * np.radians(abs(deg)), int(segments)+1)


def straight_profile(flat, origin, axis, deg, radius_mm=2.0, segments=4):
    """Reference in millimeters; the moving side is left of the directed axis.

    Mesh/endpoint/support validation is the caller's responsibility in this
    unconnected experiment. Faces crossing a breakpoint must be rejected.
    """
    flat=np.asarray(flat,dtype=float);origin=np.asarray(origin,dtype=float);u=np.asarray(axis,dtype=float)
    if flat.ndim!=2 or flat.shape[1]!=2 or origin.shape!=(2,) or u.shape!=(2,) or not np.isfinite(flat).all() or not np.isfinite(origin).all() or not np.isfinite(u).all() or np.linalg.norm(u)<1e-12:
        raise ValueError('Finite 2D coordinates, origin, and nonzero axis are required')
    u=u/np.linalg.norm(u);v=np.array([-u[1],u[0]])
    breaks=profile_breaks(deg,radius_mm,segments);step=breaks[1];a=np.radians(abs(deg))
    side=(flat-origin)@v;along=(flat-origin)@u;depth=np.maximum(side,0)
    y=np.minimum(side,0);z=np.zeros(len(flat));sign=np.sign(deg)
    for j in range(int(segments)):
        distance=np.clip(depth-j*step,0,step);phi=(j+.5)*a/segments
        y+=distance*np.cos(phi);z-=sign*distance*np.sin(phi)
    tail=np.maximum(depth-breaks[-1],0);y+=tail*np.cos(a);z-=sign*tail*np.sin(a)
    return np.c_[origin+along[:,None]*u+y[:,None]*v,z]


def terminal_angles(points, polyline, deg, taper_mm, terminal_ends):
    """Smooth authored-angle taper by material arclength to explicit endpoints.

    ``terminal_ends`` names 0 and/or 1 of the authored polyline. Boundary
    classification and final appearance remain caller responsibilities.
    """
    p=np.asarray(points,float);line=np.asarray(polyline,float)
    if p.ndim!=2 or p.shape[1]!=2 or line.ndim!=2 or line.shape[1]!=2 or len(line)<2 or not np.isfinite(p).all() or not np.isfinite(line).all():
        raise ValueError('Finite material-space points and a polyline are required')
    if not np.isfinite(deg) or abs(deg)>180 or not np.isfinite(taper_mm) or taper_mm<=0 or not terminal_ends or any(e not in (0,1) for e in terminal_ends):
        raise ValueError('A finite angle, positive taper, and explicit terminal ends are required')
    a=line[:-1];e=np.diff(line,axis=0);length=np.linalg.norm(e,axis=1)
    if np.any(length<1e-10):raise ValueError('Degenerate polyline segment')
    t=np.clip(((p[:,None,:]-a)*e).sum(2)/(length*length),0.,1.)
    delta=p[:,None,:]-(a+t[:,:,None]*e);nearest=np.argmin((delta*delta).sum(2),axis=1)
    arc=np.r_[0.,np.cumsum(length)];s=arc[nearest]+t[np.arange(len(p)),nearest]*length[nearest]
    distance=np.full(len(p),np.inf)
    if 0 in terminal_ends:distance=np.minimum(distance,s)
    if 1 in terminal_ends:distance=np.minimum(distance,arc[-1]-s)
    u=np.clip(distance/taper_mm,0.,1.);weight=u*u*(3.-2.*u)
    return float(deg)*weight


def terminal_ends(piece, line, tolerance_mm=.01):
    """Authored endpoints inside a piece; boundary endpoints never taper."""
    pts=np.asarray(line['pts'],float);result=[]
    if line.get('closed') or line.get('hole') or len(pts)<2:return result
    boundary=[np.asarray(e['pts'],float) for e in piece['edges']]
    for end,point in enumerate((pts[0],pts[-1])):
        distance=np.inf
        for edge in boundary:
            a=edge[:-1];v=np.diff(edge,axis=0);t=np.clip(((point-a)*v).sum(1)/np.maximum((v*v).sum(1),1e-20),0,1)
            distance=min(distance,float(np.linalg.norm(point-(a+t[:,None]*v),axis=1).min()))
        if distance>tolerance_mm:result.append(end)
    return result


def terminal_field(spec, names):
    """Per-line simulation taper, opt-in and zero on existing files."""
    options=dict(spec.get('experimental_fold_terminal') or {})
    pieces={p['name']:p for p in spec['pieces']}
    for fold in spec.get('folds',[]):
        length=float(fold.get('end_taper_mm',0.))
        if not np.isfinite(length) or length<0:raise ValueError('Invalid end taper length')
        if not length or not fold.get('line'):continue
        piece=pieces.get(fold['piece']);line=next((l for l in piece['internals'] if l['id']==fold['line']),None) if piece else None
        if line is None:raise ValueError('End taper needs an authored internal line')
        ends=terminal_ends(piece,line)
        if ends:options[fold['piece']+'|'+fold['line']]={'taper_mm':length,'terminal_ends':ends}
    if not options:return None
    folds={f"{f['piece']}|{f['line']}":float(f['deg']) for f in spec.get('folds',[]) if f.get('line') and f.get('deg') is not None}
    lines={}
    for key,option in options.items():
        name,lid=key.split('|')
        if key not in folds or name not in pieces:raise ValueError('Unknown terminal fold: '+key)
        piece=pieces[name];line=next((l for l in piece['internals'] if l['id']==lid),None)
        if line is None:raise ValueError('Missing authored internal: '+key)
        pts=np.asarray(line['pts'],float);ends=option['terminal_ends'];length=float(option['taper_mm'])
        # points snapped onto each other (a drawn line): one of them, or every press stopped on the degenerate segment
        if len(pts)>1:pts=pts[np.r_[True,np.linalg.norm(np.diff(pts,axis=0),axis=1)>1e-6]]
        terminal_angles(pts,pts,folds[key],length,ends)
        boundary=[np.asarray(e['pts'],float) for e in piece['edges']]
        for end in ends:
            point=pts[0 if end==0 else -1];distance=np.inf
            for edge in boundary:
                a=edge[:-1];v=np.diff(edge,axis=0);t=np.clip(((point-a)*v).sum(1)/np.maximum((v*v).sum(1),1e-20),0,1)
                distance=min(distance,float(np.linalg.norm(point-(a+t[:,None]*v),axis=1).min()))
            if distance<=.01:raise ValueError('Selected endpoint is on the boundary: '+key)
        if name in names:lines[key]={'polyline_mm':pts.tolist(),'deg':folds[key],'taper_mm':length,'terminal_ends':list(ends)}
    field={'schema':1,'lines':lines}
    field['sha256']=hashlib.sha256(json.dumps(field,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return field if lines else None


def apply_terminal_field(field, flat, hinges, rest, groups, degrees):
    """Audit and apply a field to existing hinge IDs without reordering arrays."""
    content={k:v for k,v in field.items() if k!='sha256'}
    digest=hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if set(field)-{'schema','lines','sha256','excluded'} or not {'schema','lines','sha256'}<=set(field) or field['schema']!=1 or digest!=field['sha256']:raise ValueError('Invalid terminal field version or hash')
    out=np.asarray(rest).copy();audit={}
    for key,line in field['lines'].items():
        if key not in degrees:raise ValueError('Terminal field is missing fold_deg: '+key)
        if float(degrees[key])!=float(line['deg']):raise ValueError('Terminal angle conflicts with fold_deg: '+key)
        ids=np.asarray(groups.get(key,([],0))[0],int)
        if not len(ids):raise ValueError('Terminal field has no tagged hinges: '+key)
        if (hinges[ids]<0).any():raise ValueError('Invalid terminal hinge')
        points=np.asarray(flat)[hinges[ids,2:]].mean(1)
        poly=np.asarray(line['polyline_mm']);edge=np.diff(poly,axis=0);t=np.clip(((points[:,None,:]-poly[:-1])*edge).sum(2)/np.maximum((edge*edge).sum(1),1e-20),0,1)
        if np.max(np.linalg.norm(points[:,None,:]-(poly[:-1]+t[:,:,None]*edge),axis=2).min(1))>.01:raise ValueError('Terminal hinges do not match the authored polyline: '+key)
        target=terminal_angles(points,line['polyline_mm'],line['deg'],line['taper_mm'],line['terminal_ends'])
        out[ids]=np.radians(target)
        audit[key]={'hinges':ids.tolist(),'midpoint_material_mm':points.tolist(),'target_deg':target.tolist()}
    return out,{'schema':1,'sha256':digest,'lines':audit,'excluded':field.get('excluded',{}),'scope':'Pose-only target field; not an independent natural geometry.'}


def scope_terminal_field(field, active_pieces, degrees):
    """Recipe-side exclusion is explicit; backend never silently drops a key."""
    content={k:v for k,v in field.items() if k!='sha256'};content['lines']=dict(field['lines']);excluded=dict(field.get('excluded',{}))
    for key in list(content['lines']):
        reason=('piece_not_active' if active_pieces and key.split('|')[0] not in active_pieces else 'fold_not_in_stage' if key not in degrees else None)
        if reason:excluded[key]=reason;del content['lines'][key]
    if excluded:content['excluded']=excluded
    content['sha256']=hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return content


def field_manifest(field, flat, faces, hinges, rest, audit, metric, pattern_sha, generator_sha):
    """Experimental persistence definition only; does not enable checkpoints."""
    def array_signature(value):
        a=np.ascontiguousarray(value)
        if not np.isfinite(a).all():raise ValueError('Nonfinite field persistence input')
        return {'dtype':a.dtype.str,'shape':list(a.shape),'sha256':hashlib.sha256(a.tobytes()).hexdigest()}
    if audit['sha256']!=field['sha256']:raise ValueError('Field and applied audit disagree')
    if set(audit['lines'])!=set(field['lines']):raise ValueError('Incomplete applied field mapping')
    for value in audit['lines'].values():
        if not np.allclose(np.asarray(rest)[value['hinges']],np.radians(value['target_deg']),rtol=0,atol=1e-6):raise ValueError('Applied rest does not match the target audit')
    lines={key:{'hinges':v['hinges'],'target_rad':np.asarray(rest)[v['hinges']].tolist()} for key,v in audit['lines'].items()}
    value={'schema':1,'definition':field,'pattern_sha256':pattern_sha,'generator_sha256':generator_sha,
           'flat':array_signature(flat),'faces':array_signature(faces),'hinges':array_signature(hinges),
           'applied_rest':array_signature(rest),'metric':{k:array_signature(v) for k,v in metric.items()},'applied_lines':lines}
    value['sha256']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return value


def validate_field_manifest(saved, fresh, saved_base_rest, expected_base_rest):
    """Pure preflight of base targets, not BF2's effective history-dependent rest.

    This does not audit a dynamic checkpoint or enable its acceptance.
    """
    for value in (saved,fresh):
        if not isinstance(value,dict):raise ValueError('Missing field persistence manifest')
        content={k:v for k,v in value.items() if k!='sha256'}
        digest=hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if value.get('schema')!=1 or value.get('sha256')!=digest:raise ValueError('Invalid field persistence manifest')
    if saved!=fresh:raise ValueError('Field definition, mapping, metric, or generator changed')
    a=np.asarray(saved_base_rest);b=np.asarray(expected_base_rest)
    if a.shape!=b.shape or a.dtype!=b.dtype or not np.isfinite(a).all() or not np.array_equal(a,b):raise ValueError('Saved base rest differs from the regenerated field')
