# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU assembly of the existing reference GN membrane/hinge matrix.

Float64 arithmetic, no new constitutive law. Contact diagonal blocks are an
input; contact generation and springs are outside this prototype.
"""
import warp as wp
mat43d=wp.types.matrix(shape=(4,3),dtype=wp.float64)

@wp.kernel
def membrane(q:wp.array[wp.vec3d],mass:wp.array[wp.float64],tri:wp.array2d[int],poses:wp.array[wp.mat22d],materials:wp.array2d[wp.float64],areas:wp.array[wp.float64],dt:wp.float64,rows:wp.array[int],cols:wp.array[int],values:wp.array[wp.mat33d]):
    t=wp.tid();i0=tri[t,0];i1=tri[t,1];i2=tri[t,2];p=poses[t]
    u=(q[i1]-q[i0])*p[0,0]+(q[i2]-q[i0])*p[1,0]
    v=(q[i1]-q[i0])*p[0,1]+(q[i2]-q[i0])*p[1,1]
    a=wp.vec3d(-p[0,0]-p[1,0],p[0,0],p[1,0]);b=wp.vec3d(-p[0,1]-p[1,1],p[0,1],p[1,1])
    ku=materials[t,0]/wp.dot(u,u)+wp.float64(4)*materials[t,2]/dt
    kv=materials[t,1]/wp.dot(v,v)+wp.float64(4)*materials[t,2]/dt
    ks=materials[t,3]+wp.float64(2)*materials[t,2]/dt
    for i in range(3):
        for j in range(3):
            r=tri[t,i];c=tri[t,j];at=t*9+i*3+j;rows[at]=r;cols[at]=c
            h=wp.mat33d(wp.float64(0))
            if mass[r]>0 and mass[c]>0:
                h=areas[t]*(ku*wp.outer(a[i]*u,a[j]*u)+kv*wp.outer(b[i]*v,b[j]*v)+ks*wp.outer(a[i]*v+b[i]*u,a[j]*v+b[j]*u))
            values[at]=h

@wp.kernel
def hinges(q:wp.array[wp.vec3d],mass:wp.array[wp.float64],edge:wp.array2d[int],length:wp.array[wp.float64],properties:wp.array2d[wp.float64],bf:wp.array2d[wp.float64],dt:wp.float64,offset:int,rows:wp.array[int],cols:wp.array[int],values:wp.array[wp.mat33d]):
    t=wp.tid()
    for i in range(16):
        rows[offset+t*16+i]=0;cols[offset+t*16+i]=0;values[offset+t*16+i]=wp.mat33d(wp.float64(0))
    if edge[t,0]<0 or edge[t,1]<0 or edge[t,2]<0 or edge[t,3]<0 or properties[t,0]<=0:return
    x0=q[edge[t,0]];x1=q[edge[t,1]];x2=q[edge[t,2]];x3=q[edge[t,3]]
    x02=x2-x0;x03=x3-x0;x13=x3-x1;x12=x2-x1;e=x3-x2
    n1=wp.cross(x02,x03);n2=wp.cross(x13,x12)
    l1=wp.length(n1);l2=wp.length(n2);le=wp.length(e)
    if l1<wp.float64(1e-6) or l2<wp.float64(1e-6) or le<wp.float64(1e-6):return
    n1=n1/l1;n2=n2/l2;e=e/le
    ss=wp.dot(wp.cross(n1,n2),e);cc=wp.dot(n1,n2);theta=wp.atan2(ss,cc)
    identity=wp.identity(n=3,dtype=wp.float64)
    p1=(identity-wp.outer(n1,n1))/l1;p2=(identity-wp.outer(n2,n2))/l2
    kh=properties[t,0]*length[t]
    if bf[t,1]>0:
        ms=bf[t,2]*kh*(theta-bf[t,0]);cap=kh*bf[t,1]
        if wp.abs(ms)<=cap:kh=kh*(wp.float64(1)+bf[t,2])
    kh=kh+properties[t,1]*length[t]/dt
    gg=mat43d(wp.float64(0))
    for i in range(4):
        d1=wp.mat33d(wp.float64(0));d2=wp.mat33d(wp.float64(0))
        if i==0:d1=p1*wp.skew(x3-x2)
        elif i==1:d2=p2*(-wp.skew(x3-x2))
        elif i==2:
            d1=p1*(-wp.skew(x03));d2=p2*wp.skew(x13)
        else:
            d1=p1*wp.skew(x02);d2=p2*(-wp.skew(x12))
        ds=wp.transpose(wp.skew(n1)*d2-wp.skew(n2)*d1)*e
        dc=wp.transpose(d1)*n2+wp.transpose(d2)*n1
        gi=ds*cc-dc*ss
        gg[i,0]=gi[0];gg[i,1]=gi[1];gg[i,2]=gi[2]
    for i in range(4):
        for j in range(4):
            r=edge[t,i];c=edge[t,j];at=offset+t*16+i*4+j;rows[at]=r;cols[at]=c
            if mass[r]>0 and mass[c]>0:
                gi=wp.vec3d(gg[i,0],gg[i,1],gg[i,2]);gj=wp.vec3d(gg[j,0],gg[j,1],gg[j,2])
                values[at]=kh*wp.outer(gi,gj)

@wp.kernel
def inertia(mass:wp.array[wp.float64],contact:wp.array[wp.mat33d],dt:wp.float64,offset:int,rows:wp.array[int],cols:wp.array[int],values:wp.array[wp.mat33d]):
    i=wp.tid();rows[offset+i]=i;cols[offset+i]=i
    identity=wp.identity(n=3,dtype=wp.float64)
    if mass[i]>0:values[offset+i]=identity*(mass[i]/(dt*dt))+contact[i]
    else:values[offset+i]=identity
