# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic wrong-side volume over the actual projected overlap of two triangles.

Not a quality replacement: report both oriented directions, triangle intersections,
body clearance and strain separately. Projection can degenerate at a right angle.
"""
import numpy as np


def clip_polygon(poly, value):
    if len(poly)==0:return poly
    result=[];previous=poly[-1];before=value(previous)
    for current in poly:
        after=value(current)
        if (before>=0)!=(after>=0):
            t=before/(before-after);result.append(previous+t*(current-previous))
        if after>=0:result.append(current)
        previous=current;before=after
    return np.asarray(result).reshape(-1,3)


def triangle_wrong_volume(source,target,direction_sign):
    a,b,c=np.asarray(target,float);n=np.cross(b-a,c-a);length=np.linalg.norm(n)
    if length<1.e-14:return {'volume':0.,'area':0.,'degenerate':True}
    n=n/length*direction_sign;e=(b-a)/np.linalg.norm(b-a);f=np.cross(n,e)
    local=lambda points:np.stack([(points-a)@e,(points-a)@f,(points-a)@n],axis=-1)
    polygon=local(np.asarray(source,float));boundary=local(np.asarray(target,float))
    cross=lambda p,q:p[0]*q[1]-p[1]*q[0]
    orientation=1. if cross(boundary[1,:2]-boundary[0,:2],boundary[2,:2]-boundary[0,:2])>=0 else -1.
    for i,j in ((0,1),(1,2),(2,0)):
        origin=boundary[i,:2];edge=boundary[j,:2]-origin
        polygon=clip_polygon(polygon,lambda point:orientation*cross(edge,point[:2]-origin))
        if not len(polygon):break
    polygon=clip_polygon(polygon,lambda point:-point[2]);volume=area=0.
    for i in range(1,len(polygon)-1):
        tri=polygon[[0,i,i+1]];weight=abs(cross(tri[1,:2]-tri[0,:2],tri[2,:2]-tri[0,:2]))*.5
        area+=weight;volume+=weight*max(0.,-tri[:,2].mean())
    return {'volume':float(volume),'area':float(area),'degenerate':False}


def pair_wrong_volumes(x,F,a,b,layers,signs):
    high,low=(a,b)if layers[F[a,0]]>layers[F[b,0]]else(b,a)
    if layers[F[high,0]]==layers[F[low,0]]:return [0.,0.]
    forward=triangle_wrong_volume(x[F[high]],x[F[low]],signs[low])
    reverse=triangle_wrong_volume(x[F[low]],x[F[high]],-signs[high])
    return [forward['volume']*1.e9,reverse['volume']*1.e9]
