# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure configuration contract for the opt-in static-body Fast Live mode."""
import os
import math

# 2 (Accurate): the fabric's bending hysteresis (bend_fric2, solved 'after' the substep) is carried along; cloth-cloth
# contact (self_contact) stays on; the piece Layer and the fold Layers (one layer, dependent points in the correction)
# run; Off (Pattern), Attach and seam paths run; fold / elastic changes, grab through cloth and the smooth brush work
# while Live runs; the correction's CG relative tolerance is 1e-3 (linear_tol). A version 1 job's saved state no longer
# matches the contract, so Continue / Live rebuilds from the shown shape with one line (live_resume.prepare).
VERSION = 2
MODE = 's3_static_v1'
FIXED = {'substeps': 2, 'iterations': 20, 'graph': False,
         'self_contact': False, 'init_xv': True,
         'membrane': 'bw', 'fric_anchor': True, 'friction_epsilon': .001}
UNSUPPORTED = '''layers layer_all bound dependent seam_paths attach drive cloth_drive
untangle untangle_free one_layer mirror_map mirror_x0 deep_push body_motion
bw_comp_ratio bw_comp_scale stitch_cap stitch_cap_N lock_seams binding bind_verts fold_bake
ghost_pieces weld'''.split()


def _test_supported():
    # Supported on the Accurate path, each on by default (AC9_TEST_<name>=0 turns it off, for tests only):
    # FAST_LAYERS the piece Layer and the fold Layers (one layer); FAST_E5 Off (Pattern), Attach and seam paths
    keys = ('layers', 'one_layer') if (os.environ.get('AC9_TEST_FAST_LAYERS', '1') != '0') else ()
    if (os.environ.get('AC9_TEST_FAST_E5', '1') != '0'):
        keys += ('ghost_pieces', 'attach', 'seam_paths')
    return keys


def selected(props):
    return bool(getattr(props, 'fast_live', False))


def _check_override(override):
    for name, value in (override or {}).items():
        if name.startswith('s') and name[1:].isdigit() and isinstance(value, dict):
            _check_override(value)
        elif name in FIXED and value != FIXED[name]:
            raise ValueError('AC9 Fast Live: override conflicts with '+name)
        elif name in UNSUPPORTED and value:
            raise ValueError('AC9 Fast Live does not support '+name)
        elif name == 'fast_live' and value != MODE:
            raise ValueError('AC9 Fast Live mode cannot be overridden')
        elif name == 'fast_live_contract':
            raise ValueError('AC9 Fast Live contract is generated from effective settings')
        elif name == 'grab_mode' and value != 'spring':
            raise ValueError('AC9 Fast Live supports spring grab only')


def _names(items):
    return ', '.join(items[:3]) + (f' (+{len(items) - 3})' if len(items) > 3 else '')


def preflight(spec, settings):
    """Must run before clearing handles, carrying edits or replacing any result."""
    # Each message names the field as the Tool tab shows it: the piece's Layer (layer order, AC9 Piece) and the
    # fold's Layers (AC9 Internal Line) were mistaken for each other (user 2026-11-02).
    off = 'or turn off AC9 Fast Live'
    _check_override(settings.get('cfg_override'))
    layers = 'layers' in _test_supported()   # the piece Layer and the fold Layers run on the Accurate path
    layered = [p.get('name', '?') for p in spec.get('pieces', [])
               if not layers and p.get('enabled', True) and p.get('layer', 0)]
    if layered:
        raise ValueError(f'AC9 Fast Live: Layer (layer order, AC9 Piece) is above 0 on {_names(layered)}. '
                         f'Set it to 0, {off}')
    if settings.get('layer') or settings.get('layer_all'):
        raise ValueError(f'AC9 Fast Live: the preset sets a layer order (layer / layer_all). '
                         f'Use a preset without it, {off}')
    one = [f"{f.get('piece', '?')} {f.get('line', '?')}" for f in spec.get('folds', [])
           if not layers and f.get('layers') in ('A', 'B')]
    if one:
        raise ValueError(f'AC9 Fast Live: fold Layers (AC9 Internal Line) is A only / B only on {_names(one)}. '
                         f'Set it to Both, {off}')
    if settings.get('fold_bake'):
        baked = [str(pl).replace('|', ' ') for pl in settings['fold_bake']]
        raise ValueError(f'AC9 Fast Live: the preset takes folds out of the simulation (fold_bake: '
                         f'{_names(baked)}). Use a preset without it, {off}')


def configure(cfg):
    """Apply the disclosed Fast preset after ordinary construction and overrides."""
    result = dict(cfg)
    for key in UNSUPPORTED:
        if key in _test_supported():
            continue
        if result.get(key):
            raise ValueError('AC9 Fast Live does not support '+key+'; use Native Live')
    if result.get('grab_mode', 'spring') != 'spring':
        raise ValueError('AC9 Fast Live supports spring grab only')
    result.update(FIXED)
    if (os.environ.get('AC9_TEST_FAST_SELF_CONTACT', '1') != '0'):   # cloth-cloth contact on the Accurate path (=0 turns it off, tests only)
        result['self_contact'] = cfg.get('self_contact', True)
    result.setdefault('body_kd', 0.)
    result['fast_live'] = MODE
    # These are versioned solver semantics, rather than user tuning controls.
    result['fast_live_contract'] = contract(result)
    return result


def contract(cfg):
    fps = float(cfg.get('fps', 60))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('AC9 Fast Live needs positive finite FPS')
    coefficients = {key: float(cfg.get(key, default)) for key, default in
                    [('body_mu', .5), ('soft_contact_mu', .5),
                     ('soft_contact_kd', .01), ('body_kd', 0.)]}
    if any(not math.isfinite(value) or value < 0 for value in coefficients.values()):
        raise ValueError('AC9 Fast Live requires finite nonnegative contact coefficients')
    return {'version': VERSION, 'mode': MODE, 'fps': fps,
            'dt': 1. / fps / 2, 'substeps': 2, 'native_iterations': 20,
            'corrections': 1, 'linear_tol': 1e-3, 'linear_atol': 1e-12,
            'linear_maxiter': 3000, 'linear_check_every': 10,
            'fric_anchor': True, 'friction_epsilon': .001,
            **coefficients, 'grab_mode': 'spring',
            'graph': 'native_particle_iteration', 'scope': 'static_mesh_vertex_bw',
            'self_contact': bool(cfg.get('self_contact', True)), 'bend_fric2': bool(cfg.get('bend_fric2'))}


def validate_config(cfg):
    if cfg.get('fast_live') != MODE:
        raise ValueError('Unsupported AC9 Fast Live version')
    if not cfg.get('live'):
        raise ValueError('AC9 Fast Live is available only in Live Sim')
    for key, value in FIXED.items():
        if key == 'substeps' and os.environ.get('AC9_TEST_FAST_SUBSTEPS'):   # test switch: other substep counts
            continue
        if key == 'self_contact' and (os.environ.get('AC9_TEST_FAST_SELF_CONTACT', '1') != '0'):   # cloth-cloth contact (as in configure)
            continue
        if cfg.get(key) != value:
            raise ValueError('AC9 Fast Live configuration changed: '+key)
    for key in UNSUPPORTED:
        if key in _test_supported():
            continue
        if cfg.get(key):
            raise ValueError('AC9 Fast Live does not support '+key)
    if cfg.get('grab_mode', 'spring') != 'spring':
        raise ValueError('AC9 Fast Live supports spring grab only')
    if cfg.get('fast_live_contract') != contract(cfg):
        raise ValueError('AC9 Fast Live contract does not match effective settings')


# (params.json key, the solver's LIVEP key, value a key missing from the dict means, the field as the Tool tab shows it)
_HELD = [('fold', 'fold', 0., 'Fold (AC9 Internal Line)'),
         ('fold_terminal', 'terminal', None, 'End Taper (AC9 Internal Line)'),
         ('elastic', 'elastic', 1., 'Elastic (AC9 Piece)')]


def _numbers_differ(new, old, missing):
    new, old = new or {}, old or {}
    return any(abs(float(new.get(k, missing)) - float(old.get(k, missing))) > 1e-6 for k in set(new) | set(old))


def validate_live_update(update, current):
    if (os.environ.get('AC9_TEST_FAST_E6', '1') != '0'):   # fold / elastic changes while Live runs (=0 holds them, tests only)
        return
    # Stitch values, gravity and frozen masses are synchronized at substep entry.
    # Fold/elastic controls are conservatively held for the initial release.
    # current is the solver's LIVEP, which keeps the End Taper field as 'terminal': compared against a missing
    # 'fold_terminal' every run with an End Taper stopped on its first params.json, nothing changed (user 2026-11-02).
    # Angles and ratios are compared as the solver applies them (1e-6, a missing key = no fold / no elastic).
    for key, live_key, missing, field in _HELD:
        if key not in update:
            continue
        old = current.get(live_key)
        changed = (_numbers_differ(update[key], old, missing) if missing is not None
                   else (update[key] or None) != (old or None))
        if changed:
            raise ValueError(f'AC9 Fast Live: {field} cannot change while Live runs. '
                             f'Stop and start Live again, or turn off AC9 Fast Live')


def validate_grab(grab):
    if grab and grab.get('through') and not (os.environ.get('AC9_TEST_FAST_E6', '1') != '0'):   # grab through cloth on the Accurate path
        raise ValueError('AC9 Fast Live does not support grab through cloth')
