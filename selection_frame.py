"""Ephemeral 2D selection frames. Geometry remains the document's only source of truth."""
from collections import OrderedDict
import hashlib
import math
import numpy as np


class Memory:
    def __init__(self):
        self.owners = OrderedDict()

    @staticmethod
    def signature(points):
        p = np.round(np.asarray(points, float), 4)
        return p.shape, hashlib.blake2b(p.tobytes(), digest_size=16).digest()

    def history(self, owner, selection):
        state = self.owners.get(owner)
        if state is None or state[0] != selection:
            state = (selection, OrderedDict())
            self.owners[owner] = state
        self.owners.move_to_end(owner)
        while len(self.owners) > 16:
            self.owners.popitem(last=False)
        return state[1]

    def get(self, owner, selection, points):
        history = self.history(owner, selection)
        sig = self.signature(points)
        if sig not in history:
            p = np.asarray(points, float)
            self.put(owner, selection, points, ((p.min(0)+p.max(0))/2, 0.))
        center, angle = history[sig]
        return np.array(center, float), float(angle)

    def put(self, owner, selection, points, frame):
        history = self.history(owner, selection)
        sig = self.signature(points)
        history[sig] = (np.array(frame[0], float).copy(), float(frame[1]))
        history.move_to_end(sig)
        while len(history) > 64:
            history.popitem(last=False)


def axes(angle):
    a = math.radians(angle)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def corners(points, frame):
    center, angle = frame
    basis = axes(angle)
    half = np.max(np.abs((np.asarray(points)-center) @ basis), axis=0)
    return center + np.array([[-1,-1],[1,-1],[1,1],[-1,1]])*half @ basis.T


def contains(polygon, point):
    p = np.asarray(polygon, float); v = np.roll(p,-1,axis=0)-p; q = np.asarray(point)-p
    if p.shape!=(4,2) or not np.isfinite(p).all():return False
    if abs(np.sum(p[:,0]*np.roll(p[:,1],-1)-p[:,1]*np.roll(p[:,0],-1)))<1e-6:return False
    cross = v[:,0]*q[:,1]-v[:,1]*q[:,0]
    return bool(np.all(cross >= -1e-6) or np.all(cross <= 1e-6))


def dashes(polygon, width, height):
    """Liang-Barsky clipping before enumeration; dash origin survives clipping."""
    p = np.asarray(polygon, float)
    if p.shape == (4,):
        x0,y0,x1,y1 = p; p = np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])
    segments = []
    for a,b in zip(p,np.roll(p,-1,axis=0)):
        length = float(np.linalg.norm(b-a))
        if length <= 1e-9:
            continue
        u = (b-a)/length; lo,hi = 0.,length
        for ax,limit in enumerate((width,height)):
            if abs(u[ax]) < 1e-12:
                if a[ax] < 0 or a[ax] > limit:
                    hi = -1.; break
            else:
                x,y = sorted((-a[ax]/u[ax], (limit-a[ax])/u[ax]))
                lo,hi = max(lo,x),min(hi,y)
        if hi <= lo:
            continue
        for d in np.arange(math.floor(lo/10.)*10.,hi,10.):
            start,end = max(d,lo),min(d+6.,hi)
            if end > start:
                segments.extend((tuple(a+u*start),tuple(a+u*end)))
    return segments
