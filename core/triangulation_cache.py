# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded copies of CDT results, keyed by every input consumed by triangulate."""
import hashlib,json
from collections import OrderedDict
import numpy as np

def input_key(pattern):
    keys=set(pattern.files if hasattr(pattern,'files') else pattern)
    names=np.asarray(pattern['names'])
    selected=['names','nb','twin_src']
    selected += [f'{prefix}{i}' for i in range(len(names)) for prefix in ('V','C','H','Hn')]
    digest=hashlib.sha256()
    for key in selected:
        digest.update(json.dumps([key,key in keys]).encode())
        if key in keys:
            value=np.asarray(pattern[key])
            if value.dtype.hasobject:raise ValueError('Object data cannot key triangulation')
            digest.update(json.dumps([value.dtype.str,value.shape]).encode())
            digest.update(np.ascontiguousarray(value).tobytes())
    return digest.hexdigest()

class Cache:
    def __init__(self,limit=2):self.limit=limit;self.entries=OrderedDict()
    def get(self,key):
        if key not in self.entries:return None
        self.entries.move_to_end(key)
        faces,bad=self.entries[key]
        return [a.copy() for a in faces],list(bad)
    def put(self,key,faces,bad):
        self.entries[key]=([np.array(a,copy=True) for a in faces],tuple(bad))
        self.entries.move_to_end(key)
        while len(self.entries)>self.limit:self.entries.popitem(last=False)
