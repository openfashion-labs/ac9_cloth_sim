# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only preparation plans. The caller owns persistence and Blender resource changes."""
from dataclasses import dataclass
import numpy as np
from . import fabric as FA, presets, spec as S
from ._i18n import rpt


@dataclass(frozen=True)
class Inputs:
    spec: dict
    settings: dict
    migrated: dict | None
    fabric_notes: list
    twin_notes: list
    center_x: float


def resolve(spec, panel, *, environ=None, local=None):
    """Resolve compatibility/settings/placement on copies; do not write the input or the scene."""
    migrated, notes = None, []
    if FA.needs_migrate(spec):
        candidate = S.loads(S.dumps(spec))
        if FA.migrate(candidate, panel.fabric_weight, panel.fabric_bending, notes):
            spec = migrated = candidate
    settings = presets.resolve(getattr(panel, 'preset', ''), panel, environ=environ)
    default = FA.default_fabric(spec)
    values = FA.garment_values(default)
    for key in ('weight_gsm', 'bending_uNm'):
        if key not in settings['fixed_fabric']:
            settings[key] = values[key]
    settings['membrane'], settings['kappa_y'], settings['default_fabric'] = (
        list(values['membrane']), values['kappa_y'], default['name'])
    effective = presets.spec_for_job(spec, settings)
    effective['placement'], twins = S.mirror_twins(effective)
    torso = effective.get('guides', {}).get('torso')
    center_x = float(torso['o'][0]) if torso else 0.0
    # no body is needed for pieces laid flat (user 2026-11-10); a piece on, and placed round, a guide needs the body
    # (its girth, the arms left out, the push out of it: refused here, before a run starts) and that guide
    pl_ = effective.get('placement', {})
    if panel.body is None and any(p_.get('enabled', True) and p_['name'] in pl_ and not S.is_flat(pl_[p_['name']])
                                  for p_ in effective['pieces']):
        raise ValueError('Pick the body object (Placement panel)')
    guides = effective.get('guides') or {}
    if any(not S.is_flat(p) and p.get('guide') not in guides for p in effective.get('placement', {}).values()):
        raise ValueError('Detect Guides first')
    missing = {p['name'] for p in effective['pieces'] if p.get('enabled', True)
               and p['name'] not in effective.get('placement', {})}
    if missing:
        effective = dict(effective, pieces=[dict(p, enabled=False) if p['name'] in missing else p
                                           for p in effective['pieces']])
    placed = {p['name'] for p in effective['pieces'] if p.get('enabled', True)}
    if not placed or (local is not None and not placed.intersection(local)):
        raise ValueError(rpt('No placed pieces to simulate'))
    if settings.get('native_three'):
        # Native 3 is the default Live (user 2026-11-12); a garment it does not take (a piece on a Layer above 0,
        # kept in an older file) runs the usual Live instead of stopping, the reason said by the caller
        from ..solver.native_three_contract import preflight as n3_preflight
        try:
            n3_preflight(effective, settings)
        except ValueError as exc:
            settings['native_three'] = False
            settings['native_three_off'] = str(exc)
    return Inputs(effective, settings, migrated, notes, twins, center_x)


@dataclass(frozen=True)
class MeshArrays:
    names: list
    offsets: list
    triangles: np.ndarray
    face_pieces: np.ndarray
    flat: np.ndarray
    initial: np.ndarray
    stitches: np.ndarray


def assemble(pattern, triangles, initial):
    """Assemble solver indices/coordinates without creating a job or changing source arrays."""
    names = [str(n) for n in pattern['names']]
    offsets, faces, face_pieces, flat = [], [], [], []
    offset = 0
    for i in range(len(names)):
        offsets.append(offset)
        faces.append(triangles[i] + offset)
        face_pieces.append(np.full(len(triangles[i]), i))
        flat.append(pattern[f'V{i}'])
        offset += len(pattern[f'V{i}'])
    faces, face_pieces = np.vstack(faces), np.concatenate(face_pieces)
    flat, initial = np.vstack(flat), np.vstack(initial)
    stitch = pattern['stitch']
    stitches = np.stack([np.array(offsets)[stitch[:, 0]] + stitch[:, 1],
                         np.array(offsets)[stitch[:, 2]] + stitch[:, 3]], 1)
    return MeshArrays(names, offsets, faces, face_pieces, flat, initial, stitches)
