# SPDX-License-Identifier: GPL-3.0-or-later
"""Each piece's fabric and interlining (no bpy). Design: 2609_Apparel_CAD/docs/design/DESIGN_fabric_model_2026-10-20.md
(user 2026-10-20, DECISIONS cloth-sim-fabric-model-2026-10-20), on DESIGN_fabric_per_piece_2026-10-14.md.

Every piece has one fabric: its own, its twin's, else the garment's default -- one entry of the same list, never a
second path (the Dress panel's Weight / Bending are gone). A new garment starts with the default preset (Cotton
Jersey, user 2026-11-12; Cotton Poplin from 2026-10-20); a garment saved before has its default made from the Dress panel's old values by migrate(),
so its solver input stays what it was. The spec holds the numbers, not the preset's name: editing the preset table
(fabric_presets.json) never changes a saved garment.

    spec["fabrics"]:           [{"id", "name", "preset", "source", "weight_gsm", "bending_uNm" (or {"warp", "weft"}:
                                 the mean is used), "stretch_N_m" (or {"warp", "weft"}), "shear_N_m",
                                 "bend_hyst_per_m", "thickness_mm"}]   every value set (migrate() fills the old
                                 nulls with what the solver used), thickness_mm may be None (no display thickness)
    spec["default_fabric_id"]: the garment's default, an id of spec["fabrics"]
    pieces[].fabric_id:        an id of spec["fabrics"]; absent = the default (it follows a changed default)
    spec["interlinings"]:      [{"id", "name", "preset", "source", "weight_gsm", "bending_uNm"}]   (2026-10-25) the
                                 garment's interlinings (bonds): an edit reaches every piece on it
    pieces[].bond_id:          an id of spec["interlinings"]; absent = none (its twin's, if the twin has one)
    pieces[].bond:             before 2026-10-25 the interlining held whole on each piece; migrate() moves it into
                                 the list (one entry per preset / name / weight / bending) and sets bond_id

The three boxes (design section 2): the fabric holds what a fabric test measures (weight, bending, stretch, shear,
the bending hysteresis kappa_y = 2HB / 2B, the display thickness); the piece holds which fabric, the interlining, the
elastic, the ease; the solver keeps the rest fixed (the body friction, friction_epsilon, the stitch cap, the
hysteresis' stick ratio and start -- core/recipe.py).

The default's values are the garment's (core/recipe.physical: density, bending, the membrane tri_ke / tri_ka /
tri_ks, kappa_y). Pieces on another fabric get cfg "fabric_pieces" {piece: {"D": N m, "rho": kg/m2, "kappa_y": 1/m,
"ku", "kv", "ks": N/m}}: an interlining adds its weight and its bending to the fabric's (the bending sum is a lower
bound: a fused pair is stiffer, uncalibrated) and leaves the membrane. "ku" runs along the flat pattern's x (warp),
"kv" along y (weft); the grain line is not read. Tested on a T-shirt rib, 2026-10-17 (APPROVED "T シャツの衿リブの生地":
stretch 30, shear 29, bending 12).
"""
import json
import os
from ._i18n import rpt

from . import spec as S

HELD = ('stretch_N_m', 'shear_N_m', 'thickness_mm')     # copied from a preset with the weight and bending
DEFAULT_PRESET = 'jersey'                                 # a new garment's default fabric (user 2026-11-12; poplin before)
# what the solver used for every garment before 2026-10-20 where a fabric named nothing: the membrane of
# core/recipe.py (BASE tri_ke / tri_ka 3000, physical() tri_ks 29) and the table's kappa_y (migrate() writes them in)
OLD_STRETCH_N_M, OLD_SHEAR_N_M = 3000.0, 29.0
OLD_PANEL = (276.0, 18.0)                                 # the Dress panel's Weight / Bending defaults (old blends)
_PRESETS = None


def presets():
    """{'fabrics': [...], 'interlinings': [...]} from fabric_presets.json (read once)."""
    global _PRESETS
    if _PRESETS is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fabric_presets.json')
        _PRESETS = json.load(open(p, encoding='utf-8'))
    return _PRESETS


def bend_hyst(f=None):
    """kappa_y (1/m) of a fabric entry or preset: its own bend_hyst_per_m, else the table's (the garment's fabric,
    given by the Dress panel's Weight / Bending only, takes the table's)."""
    v = (f or {}).get('bend_hyst_per_m')
    return float(v) if v is not None else float(presets()['bend_hyst_per_m'])


def preset(kind, pid):
    """The preset `pid` of kind 'fabrics' / 'interlinings', or None."""
    return next((p for p in presets()[kind] if p['preset'] == pid), None)


def bending(f):
    """bending_uNm of a fabric or bond: warp and weft averaged."""
    b = f['bending_uNm']
    return 0.5 * (float(b['warp']) + float(b['weft'])) if isinstance(b, dict) else float(b)


def stretch(f):
    """(warp, weft) N/m of a fabric's stretch_N_m (one number = both), or None = the garment's."""
    s = f.get('stretch_N_m')
    if s is None:
        return None
    if isinstance(s, dict):
        return float(s['warp']), float(s['weft'])
    return float(s), float(s)


def thickness(f):
    """thickness_mm of a fabric (display only), or None."""
    t = f.get('thickness_mm') if f else None
    return None if t is None else float(t)


def fabric_index(spec):
    return {f['id']: f for f in spec.get('fabrics', [])}


def add_from_preset(spec, pid):
    """Add the preset `pid` to spec['fabrics'] (in place; the one already added is reused). Returns its id."""
    p = preset('fabrics', pid)
    if p is None:
        raise KeyError(pid)
    fl = spec.setdefault('fabrics', [])
    for f in fl:
        if f.get('preset') == pid:
            return f['id']
    ids = {f['id'] for f in fl}
    fid, i = pid, 2
    while fid in ids:
        fid, i = f"{pid}_{i}", i + 1
    fl.append({'id': fid, 'name': p['name'], 'preset': pid, 'source': p.get('source', ''),
               'weight_gsm': float(p['weight_gsm']), 'bending_uNm': p['bending_uNm'],
               'bend_hyst_per_m': p.get('bend_hyst_per_m'), **{k: p.get(k) for k in HELD}})
    return fid


def add_custom(spec, name="New Fabric"):
    """Add a fabric with the default's numbers and no preset (Lists > Fabrics > New, user 2026-10-25: a fabric always
    holds every value, so a new one starts from what the garment runs with). In place; returns its id."""
    d = default_fabric(spec) or preset('fabrics', DEFAULT_PRESET)
    fl = spec.setdefault('fabrics', [])
    ids, names = {f['id'] for f in fl}, {f['name'] for f in fl}
    nm, i = name, 2
    while nm in names:
        nm, i = f"{name} {i}", i + 1
    i = 1
    while f"fabric_{i}" in ids:
        i += 1
    keys = ('weight_gsm', 'bending_uNm', 'bend_hyst_per_m') + HELD
    fl.append({'id': f"fabric_{i}", 'name': nm, 'preset': None,
               'source': f"made from {d['name']}'s numbers", **{k: d.get(k) for k in keys}})
    return f"fabric_{i}"


def apply_preset(spec, fid, pid):
    """Fabric `fid` takes the preset `pid`'s name and every number (in place): the Preset menu of AC9 Fabric."""
    p, f = preset('fabrics', pid), fabric_index(spec).get(fid)
    if p is None or f is None:
        raise KeyError(pid if p is None else fid)
    f.update(name=p['name'], preset=pid, source=p.get('source', ''), weight_gsm=float(p['weight_gsm']),
             bending_uNm=p['bending_uNm'], bend_hyst_per_m=p.get('bend_hyst_per_m'), **{k: p.get(k) for k in HELD})


def default_fabric(spec):
    """The garment's default fabric entry, or None (a spec saved before 2026-10-20 that migrate() has not seen)."""
    return fabric_index(spec).get(spec.get('default_fabric_id'))


def add_default(spec, pid=DEFAULT_PRESET):
    """A new garment's default fabric: the preset `pid` in the list and named the default (in place)."""
    spec['default_fabric_id'] = add_from_preset(spec, pid)
    return spec['default_fabric_id']


def _same(f, p):
    keys = ('weight_gsm', 'bending_uNm', 'stretch_N_m', 'shear_N_m', 'bend_hyst_per_m')
    return all(f.get(k) == p.get(k) for k in keys)


def needs_migrate(spec):
    """Would migrate() change the spec (a fabric with an empty stretch / shear / kappa_y, no default, or an
    interlining held on a piece)? Read only: the callers copy the spec for migrate() only then."""
    return (any(f.get(k) is None for f in spec.get('fabrics', []) for k in ('stretch_N_m', 'shear_N_m', 'bend_hyst_per_m'))
            or default_fabric(spec) is None or any(pc.get('bond') for pc in spec['pieces']))


def _migrate_bonds(spec, notes):
    """The interlinings held on the pieces (before 2026-10-25) into spec["interlinings"], in place: the same
    preset, name, weight and bending make one entry; each piece gets its bond_id. The numbers are copied as they
    were, so the solver input is unchanged. True if changed."""
    old = [pc for pc in spec['pieces'] if pc.get('bond')]
    if not old:
        return False
    il = spec.setdefault('interlinings', [])
    for pc in old:
        b = pc.pop('bond')
        nm = b.get('name') or b.get('preset') or 'Interlining'
        w, d = float(b['weight_gsm']), float(b['bending_uNm'])
        hit = next((i for i in il if (i.get('preset'), i['name'], float(i['weight_gsm']), float(i['bending_uNm']))
                    == (b.get('preset'), nm, w, d)), None)
        if hit is None:
            hit = {'id': _new_id(il, b.get('preset') or 'interlining'), 'name': nm, 'preset': b.get('preset'),
                   'source': b.get('source', ''), 'weight_gsm': w, 'bending_uNm': d}
            il.append(hit)
            notes.append(f"interlining: {nm} moved from the pieces into the garment's list")
        pc['bond_id'] = hit['id']
    return True


def migrate(spec, weight_gsm=None, bending_uNm=None, notes=None):
    """A spec from before 2026-10-20 into this shape, in place, its solver input unchanged; True if changed.
    Every fabric's empty stretch / shear / kappa_y gets what the solver used for it (the garment's membrane, the
    table's kappa_y). No default: one made from the Dress panel's old Weight / Bending (weight_gsm / bending_uNm,
    None = the panel's defaults) with that membrane -- a fabric already in the list with exactly these numbers, else
    a preset with them, else "Garment (Dress panel)". A default that is not in the list is made again the same way."""
    notes = [] if notes is None else notes
    changed = _migrate_bonds(spec, notes)
    for f in spec.get('fabrics', []):
        for k, v in (('stretch_N_m', OLD_STRETCH_N_M), ('shear_N_m', OLD_SHEAR_N_M),
                     ('bend_hyst_per_m', float(presets()['bend_hyst_per_m']))):
            if f.get(k) is None:
                f[k] = v
                changed = True
                notes.append(f"fabric: {f['name']} had no {k}: {v:g}, what the solver used")
    if default_fabric(spec) is not None:
        return changed
    w = float(OLD_PANEL[0] if weight_gsm is None else weight_gsm)
    d = float(OLD_PANEL[1] if bending_uNm is None else bending_uNm)
    new = {'weight_gsm': w, 'bending_uNm': d, 'stretch_N_m': OLD_STRETCH_N_M, 'shear_N_m': OLD_SHEAR_N_M,
           'bend_hyst_per_m': float(presets()['bend_hyst_per_m'])}
    hit = next((f for f in spec.get('fabrics', []) if _same(f, new)), None)
    if hit is None:
        p = next((p for p in presets()['fabrics'] if _same(p, new)), None)
        fl = spec.setdefault('fabrics', [])
        ids = {f['id'] for f in fl}
        base = p['preset'] if p else 'garment'
        fid, i = base, 2
        while fid in ids:
            fid, i = f"{base}_{i}", i + 1
        hit = dict(new, id=fid, name=p['name'] if p else "Garment (Dress panel)", preset=p['preset'] if p else None,
                   source=(p.get('source', '') if p else
                           "the Dress panel's Weight / Bending of a garment saved before 2026-10-20, with the membrane "
                           "and the bending hysteresis the solver used then"),
                   thickness_mm=None)          # it had no display thickness: none now either
        fl.append(hit)
    spec['default_fabric_id'] = hit['id']
    notes.append(f"fabric: the default made from the old Dress panel: {hit['name']} ({w:g} g/m2, {d:g} uN m)")
    return True


def set_default(spec, fid):
    """Make `fid` the garment's default (in place). The pieces following the default follow it."""
    if fid not in fabric_index(spec):
        raise KeyError(fid)
    spec['default_fabric_id'] = fid


def remove(spec, fid):
    """Take `fid` off the list (in place); its pieces go to the default. The default cannot be taken off."""
    if fid == spec.get('default_fabric_id'):
        raise ValueError(rpt("the default fabric: make another one the default first"))
    spec['fabrics'] = [f for f in spec.get('fabrics', []) if f['id'] != fid]
    for pc in spec['pieces']:
        if pc.get('fabric_id') == fid:
            pc.pop('fabric_id')


def garment_values(f):
    """The default fabric's numbers for core/recipe.physical: weight g/m2, bending uN m, (ku, kv, ks) N/m, kappa_y."""
    st = stretch(f) or (OLD_STRETCH_N_M, OLD_STRETCH_N_M)
    ks = f.get('shear_N_m')
    return {'weight_gsm': float(f['weight_gsm']), 'bending_uNm': bending(f),
            'membrane': (st[0], st[1], OLD_SHEAR_N_M if ks is None else float(ks)), 'kappa_y': bend_hyst(f)}


def _new_id(items, base):
    ids = {x['id'] for x in items}
    iid, i = base, 2
    while iid in ids:
        iid, i = f"{base}_{i}", i + 1
    return iid


def interlining_index(spec):
    return {i['id']: i for i in spec.get('interlinings', [])}


def add_interlining_from_preset(spec, pid):
    """Add the interlining preset `pid` to spec['interlinings'] (in place; the one already added is reused). Its id."""
    il = spec.setdefault('interlinings', [])
    for i in il:
        if i.get('preset') == pid:
            return i['id']
    il.append(dict(bond_from_preset(pid), id=_new_id(il, pid)))
    return il[-1]['id']


def add_custom_interlining(spec, name="New Interlining"):
    """Add an interlining with the first preset's numbers and no preset (Lists > Fabrics > Interlinings > New).
    In place; its id."""
    il = spec.setdefault('interlinings', [])
    b = bond_from_preset(presets()['interlinings'][0]['preset'])
    names = {i['name'] for i in il}
    nm, k = name, 2
    while nm in names:
        nm, k = f"{name} {k}", k + 1
    il.append(dict(b, id=_new_id(il, 'interlining'), name=nm, preset=None, source=f"made from {b['name']}'s numbers"))
    return il[-1]['id']


def apply_interlining_preset(spec, iid, pid):
    """Interlining `iid` takes the preset `pid`'s name and numbers (in place)."""
    i = interlining_index(spec).get(iid)
    if i is None:
        raise KeyError(iid)
    i.update(bond_from_preset(pid))


def remove_interlining(spec, iid):
    """Take `iid` off the list (in place); its pieces have no interlining then. Returns the pieces it was on."""
    spec['interlinings'] = [i for i in spec.get('interlinings', []) if i['id'] != iid]
    out = []
    for pc in spec['pieces']:
        if pc.get('bond_id') == iid:
            pc.pop('bond_id')
            out.append(pc['name'])
    return out


def users_by_interlining(spec):
    """{interlining id: the pieces with it (own or through the twin, as piece_bond)} in one pass."""
    out = {i['id']: [] for i in spec.get('interlinings', [])}
    pcs, tw = S.piece_index(spec), _twin_map(spec)
    for pc in spec['pieces']:
        iid = next((pcs[n].get('bond_id') for n in (pc['name'], tw.get(pc['name']))
                    if n in pcs and pcs[n].get('bond_id') in out), None)
        if iid is not None:
            out[iid].append(pc['name'])
    return out


def bond_from_preset(pid):
    p = preset('interlinings', pid)
    if p is None:
        raise KeyError(pid)
    return {'preset': pid, 'name': p['name'], 'weight_gsm': float(p['weight_gsm']),
            'bending_uNm': float(p['bending_uNm']), 'source': p.get('source', '')}


def _twin_map(spec):
    tw = {}
    for a, b in S.twins(spec):
        tw[a], tw[b] = b, a
    return tw


def piece_fabric(spec, name):
    """The fabric entry set on piece `name` (its own, else its twin's), None = it follows the default."""
    idx, pcs = fabric_index(spec), S.piece_index(spec)
    for n in (name, _twin_map(spec).get(name)):
        fid = pcs.get(n, {}).get('fabric_id') if n else None
        if fid in idx:
            return idx[fid]
    return None


def piece_bond(spec, name):
    """The interlining of piece `name` (its own, else its twin's): the list's entry, or the one a spec not yet
    migrated holds on the piece; None = none."""
    pcs, idx = S.piece_index(spec), interlining_index(spec)
    for n in (name, _twin_map(spec).get(name)):
        pc = pcs.get(n, {}) if n else {}
        if pc.get('bond_id') in idx:
            return idx[pc['bond_id']]
        if pc.get('bond'):
            return pc['bond']
    return None


def set_pieces(spec, names, fabric_id=None, bond=None, what='fabric'):
    """Set (what='fabric': fabric_id, None = the garment's; what='bond': bond, an id of the interlinings, None =
    off) on the pieces `names` and their twins, in place."""
    tw = _twin_map(spec)
    both = set(names) | {tw[n] for n in names if n in tw}
    for pc in spec['pieces']:
        if pc['name'] not in both:
            continue
        key, val = ('fabric_id', fabric_id) if what == 'fabric' else ('bond_id', bond)
        if what == 'bond':
            pc.pop('bond', None)                     # held on the piece before 2026-10-25
        if val is None:
            pc.pop(key, None)
        else:
            pc[key] = val
    return sorted(both)


def resolved(spec, name):
    """The fabric piece `name` is simulated with: its own or its twin's, else the default (None only for a spec
    migrate() has not seen and no fabric set)."""
    return piece_fabric(spec, name) or default_fabric(spec)


def users(spec, fid):
    """The pieces simulated with `fid` (own, through the twin, or following the default)."""
    return [pc['name'] for pc in spec['pieces'] if (resolved(spec, pc['name']) or {}).get('id') == fid]


def users_by_fabric(spec):
    """{fabric id: users()} for every fabric of the list in one pass (users() per fabric makes the twins again for
    each piece: a jacket's 133 pieces x the fabrics on every sidebar redraw)."""
    idx, pcs, tw = fabric_index(spec), S.piece_index(spec), _twin_map(spec)
    d = spec.get('default_fabric_id')
    out = {fid: [] for fid in idx}
    for pc in spec['pieces']:
        fid = next((pcs.get(n, {}).get('fabric_id') for n in (pc['name'], tw.get(pc['name']))
                    if n and pcs.get(n, {}).get('fabric_id') in idx), d)
        if fid in out:
            out[fid].append(pc['name'])
    return out


def solver_pieces(spec, names, weight_gsm, bending_uNm, notes=None):
    """cfg 'fabric_pieces' {piece: {"D": N m, "rho": kg/m2, "kappa_y": 1/m[, "ku", "kv", "ks": N/m]}} for the pieces
    in `names` on a fabric other than the default or with an interlining ({} when none: the default is the garment's,
    core/recipe.physical), and the job report's lines in `notes`. weight_gsm / bending_uNm: the garment's (the
    default's, or a settings set's test values)."""
    notes = [] if notes is None else notes
    idx = fabric_index(spec)
    dflt = spec.get('default_fabric_id')
    for pc in spec['pieces']:
        if pc['name'] in names and pc.get('fabric_id') and pc['fabric_id'] not in idx:
            notes.append(f"fabric: {pc['name']} names fabric {pc['fabric_id']!r}, not in the list: the default used")
    out, by, used = {}, {}, {}
    for n in names:
        f, b = piece_fabric(spec, n), piece_bond(spec, n)
        if f is not None and f['id'] == dflt:
            f = None                                       # set to the default by name: the garment's values
        if f is None and b is None:
            continue
        w = float(f['weight_gsm']) if f else float(weight_gsm)
        d = bending(f) if f else float(bending_uNm)
        tag = f['name'] if f else 'the default'
        if b:
            w, d = w + float(b['weight_gsm']), d + float(b['bending_uNm'])
            tag += f" + {b.get('name') or b.get('preset') or 'interlining'}"
        out[n] = {'D': round(d * 1e-6, 12), 'rho': round(w / 1000.0, 9)}
        if f:
            out[n]['kappa_y'] = bend_hyst(f)
        mem = ''
        st = stretch(f) if f else None
        if st is not None:
            out[n]['ku'], out[n]['kv'] = st
            mem += f", stretch {st[0]:g}" + (f"/{st[1]:g}" if st[1] != st[0] else '') + " N/m"
        if f and f.get('shear_N_m') is not None:
            out[n]['ks'] = float(f['shear_N_m'])
            mem += f", shear {out[n]['ks']:g} N/m"
        by.setdefault(f"{tag} ({w:g} g/m2, {d:g} uN m{mem})", []).append(n)
        if f:
            used[f['id']] = f
    for k, v in by.items():
        notes.append(f"fabric: {k}: {', '.join(v)}")
    if out:
        notes.append(f"fabric: the other pieces the default ({float(weight_gsm):g} g/m2, {float(bending_uNm):g} uN m)")
    for f in used.values():
        if f.get('thickness_mm') is not None:
            notes.append(f"fabric: {f['name']} thickness {float(f['thickness_mm']):g} mm: the display only")
    return out
