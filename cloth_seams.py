# SPDX-License-Identifier: GPL-3.0-or-later
"""Read sewing correspondences for the actual displayed meshes, pair by pair.

Result-folder indices are used only after topology, names and material coordinates
match. Legacy/placed meshes may use an exact remesh correspondence. Missing data
leaves just that seam pair uncertain; it never means zero intersections.
"""
import json
import zipfile
import numpy as np


def key(a, b):
    return tuple(sorted((a, b)))


def canonical(t):
    t = np.sort(np.asarray(t, np.int64), axis=1)
    return t[np.lexsort((t[:, 2], t[:, 1], t[:, 0]))]


def contracts(spec):
    """All inputs that determine correspondence, including notch pairing/shape."""
    if not isinstance(spec, dict) or not isinstance(spec.get('pieces'), list) or not isinstance(spec.get('seams'), list):
        raise ValueError('Invalid saved sewing specification')
    pieces = {}
    for p in spec['pieces']:
        if not isinstance(p, dict) or not isinstance(p.get('name'), str):
            raise ValueError('Invalid saved sewing piece')
        if p['name'] in pieces:
            raise ValueError('Ambiguous saved sewing piece')
        pieces[p['name']] = {k: p.get(k) or [] for k in ('edges', 'internals', 'notches')}
        pieces[p['name']].update({k: p.get(k) for k in ('plane', 'layout')})
    out, seen = {}, set()
    for s in spec['seams']:
        if not isinstance(s, dict) or 'id' not in s or not all(isinstance(s.get(k), dict) and isinstance(s[k].get('piece'), str) for k in ('a', 'b')):
            raise ValueError('Invalid saved sewing side')
        sid = str(s['id'])
        if sid in seen:
            raise ValueError('Ambiguous sewing identity')
        seen.add(sid)
        if s.get('enabled', True):
            ns = (s['a']['piece'], s['b']['piece'])
            out[sid] = (key(*ns), json.dumps([s['a'], s['b'], [pieces[n] for n in ns]], sort_keys=True))
    return out


def read(context, spec, shapes):
    from . import dress as D, result_reference as RR, display_state as DS
    from .core import spec as S
    active = [s for s in spec.get('seams', []) if s.get('enabled', True)]
    expected = {key(s['a']['piece'], s['b']['piece']) for s in active
                if s['a']['piece'] in shapes and s['b']['piece'] in shapes}
    out = {'pairs': {}, 'unknown': set(expected)}
    try:
        current_contract = contracts(spec)
    except (ValueError, KeyError, TypeError):
        return out
    g = D.garment(spec)
    result = {n: f for n, f in shapes.items() if f['source'] == 'result' and f['owner'] == g}
    if result:
        try:
            path = DS.job(g)
            if not path:
                raise ValueError('No saved sewing reference')
            ref = RR.open_result(path)
            idx = ref.index
            names, flat, faces, fp = ref.names, np.asarray(idx['flat']), np.asarray(idx['F']), np.asarray(idx['fp'])
            if len(set(names)) != len(names):
                raise ValueError('Ambiguous saved piece names')
            if flat.ndim != 2 or flat.shape[1] != 2 or not np.isfinite(flat).all():
                raise ValueError('Saved material coordinates invalid')
            if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in 'iu' or (len(faces) and (faces.min() < 0 or faces.max() >= len(flat))):
                raise ValueError('Saved triangles invalid')
            edges = ref.stitches()
            if edges is None or edges.ndim != 2 or edges.shape[1] != 2 or edges.dtype.kind not in 'iu':
                raise ValueError('No valid saved stitches')
            if len(edges) and (edges.min() < 0 or edges.max() >= len(flat)):
                raise ValueError('Saved stitch index out of range')
            if fp.ndim != 1 or len(fp) != len(faces) or fp.dtype.kind not in 'iu' or (len(fp) and (fp.min() < 0 or fp.max() >= len(names))):
                raise ValueError('Saved face ownership invalid')
            old = json.loads(ref.dressed_spec_text() or '{}')
            saved_contract = contracts(old)
            if old.get('name') != spec.get('name'):
                raise ValueError('Saved sewing specification belongs to another garment')
            current = {str(s['id']): s for s in active}
            same = {sid for sid in current_contract if saved_contract.get(sid) == current_contract[sid]}
            valid, vertices = set(), {}
            for n, f in result.items():
                ids = f['ids']
                if n not in names or not f['flat_checked'] or not len(ids) or ids.min() < 0 or ids.max() >= len(flat):
                    continue
                if not np.allclose(flat[ids], f['V'], atol=.001, rtol=0):
                    continue
                if not np.array_equal(canonical(faces[fp == names.index(n)]), canonical(ids[f['T']])):
                    continue
                valid.add(n)
                for local, vertex in enumerate(ids):
                    vertices.setdefault(int(vertex), []).append((n, local))
            seam_ids = ref.seams()
            if seam_ids is not None and len(seam_ids) != len(edges):
                raise ValueError('Saved seam ids invalid')
            proven = set()
            for i, (a, b) in enumerate(edges):
                aa, bb = vertices.get(int(a), ()), vertices.get(int(b), ())
                if len(aa) != 1 or len(bb) != 1:
                    continue
                (an, av), (bn, bv) = aa[0], bb[0]
                pair = key(an, bn)
                if pair not in expected:
                    continue
                needed = {sid for sid, s in current.items() if key(s['a']['piece'], s['b']['piece']) == pair}
                if not needed <= same:
                    continue
                if seam_ids is not None:
                    sid = str(seam_ids[i])
                    if sid not in needed:
                        continue
                    proven.add(sid)
                else:
                    saved_pair = {sid: contract for sid, contract in saved_contract.items() if contract[0] == pair}
                    current_pair = {sid: contract for sid, contract in current_contract.items() if contract[0] == pair}
                    if not needed or saved_pair != current_pair:
                        continue
                edge = (av, bv) if an <= bn else (bv, av)
                if an == bn:
                    edge = tuple(sorted(edge))
                out['pairs'].setdefault(pair, set()).add(edge)
            for pair in list(out['pairs']):
                needed = {sid for sid, s in current.items() if key(s['a']['piece'], s['b']['piece']) == pair}
                if needed <= same and (seam_ids is None or needed <= proven):
                    out['unknown'].discard(pair)
        except (OSError, ValueError, KeyError, TypeError, IndexError, EOFError, zipfile.BadZipFile, RR.ResultUnreadable):
            pass
    # Only remaining pairs need the legacy mesher. Validate each pair separately.
    if out['unknown']:
        try:
            sp = S.loads(S.dumps(spec))
            for pc in sp['pieces']:
                if pc['name'] in shapes:
                    pc['enabled'] = True
            pat, _report = D._build_cached(sp, context.scene.ac9_cloth_sim.mesh_size)
            names = [str(n) for n in pat['names']]
            groups = {}
            grouped_ids = {}
            rows = np.asarray(pat['stitch'], np.int64).reshape(-1, 4)
            ids = np.asarray(pat['stitch_seam'])
            if len(ids) != len(rows):
                raise ValueError('Generated sewing identities missing')
            for si, (ai, av, bi, bv) in enumerate(rows):
                an, bn = names[ai], names[bi]
                pair = key(an, bn)
                if pair in out['unknown']:
                    groups.setdefault(pair, []).append((an, int(av), bn, int(bv)))
                    grouped_ids.setdefault(pair, set()).add(str(ids[si]))
            for pair in list(out['unknown']):
                rows = groups.get(pair, [])
                needed = {str(s['id']) for s in active if key(s['a']['piece'], s['b']['piece']) == pair}
                if not rows or not needed <= grouped_ids.get(pair, set()) or not all(shapes[n]['flat_checked'] for n in pair):
                    continue
                mapping, good = {}, True
                for n in set(pair):
                    used = sorted({a for an, a, bn, b in rows if an == n} | {b for an, a, bn, b in rows if bn == n})
                    q, v = np.asarray(pat[f'V{names.index(n)}'])[used], shapes[n]['V']
                    for offset in range(0, len(q), 128):
                        dist = np.linalg.norm(q[offset:offset + 128, None] - v[None], axis=2)
                        near, vi = dist.min(1), dist.argmin(1)
                        if (near > .05).any() or ((np.abs(dist - near[:, None]) < 1e-7).sum(1) != 1).any():
                            good = False
                            break
                        mapping.update({(n, used[offset + j]): int(k) for j, k in enumerate(vi)})
                    if good and len({mapping[n, k] for k in used}) != len(used):
                        good = False
                if not good:
                    continue
                links = set()
                for an, a, bn, b in rows:
                    edge = (mapping[an, a], mapping[bn, b])
                    if an > bn:
                        edge = edge[::-1]
                    if an == bn:
                        edge = tuple(sorted(edge))
                    links.add(edge)
                out['pairs'][pair] = links
                out['unknown'].discard(pair)
        except (ValueError, KeyError, IndexError, RuntimeError, StopIteration):
            pass
    return out
