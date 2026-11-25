# SPDX-License-Identifier: GPL-3.0-or-later
"""One-operation stitch scope. Never change the spec, meshing, or collision geometry."""
import numpy as np


def filter_stitches(got, piece, targets):
    """Keep other pieces' stitches and self-closures; restrict this piece's external stitches."""
    if targets is None:
        return got
    pat, tris, xyz = got
    names = list(map(str, pat['names']))
    if piece not in names:
        return got
    i = names.index(piece)
    allowed = {j for j, name in enumerate(names) if name in targets}
    st = np.asarray(pat['stitch']).reshape(-1, 4)
    external = (st[:, 0] == i) ^ (st[:, 2] == i)
    other = np.where(st[:, 0] == i, st[:, 2], st[:, 0])
    keep = ~external | np.isin(other, list(allowed))
    scoped = dict(pat, stitch=st[keep].copy())
    if 'stitch_seam' in pat:
        scoped['stitch_seam'] = np.asarray(pat['stitch_seam'])[keep].copy()
    return scoped, tris, xyz


def partner_names(got, piece, excluded=(), spec=None):
    pat = got[0]
    names = list(map(str, pat['names']))
    if piece not in names:
        # A freshly cloned pocket is deliberately unplaced and absent from prepare's
        # mesh. Its authored enabled seams still identify the placed target to pick.
        if spec is not None and piece not in spec.get('placement', {}):
            pc = next((p for p in spec.get('pieces', []) if p['name'] == piece), None)
            if pc is None or not pc.get('enabled', True):return []
            partners = set()
            for sm in spec.get('seams', []):
                if not sm.get('enabled', True):continue
                a, b = sm['a']['piece'], sm['b']['piece']
                if a == piece and b in names:partners.add(b)
                if b == piece and a in names:partners.add(a)
            return sorted(partners - set(excluded) - {piece})
        return []
    i = names.index(piece)
    st = np.asarray(pat['stitch']).reshape(-1, 4)
    rows = np.r_[st[(st[:, 0] == i) & (st[:, 2] != i), 2],
                 st[(st[:, 2] == i) & (st[:, 0] != i), 0]]
    return sorted({names[int(j)] for j in rows} - set(excluded))


def filter_spec(spec, piece, targets):
    """A read-only eligibility view, not a spec to store or remesh."""
    seams = []
    for sm in spec.get('seams', []):
        a, b = sm['a']['piece'], sm['b']['piece']
        if a == piece and b != piece and b not in targets:
            continue
        if b == piece and a != piece and a not in targets:
            continue
        seams.append(sm)
    return dict(spec, seams=seams)
