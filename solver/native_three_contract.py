# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed, non-physical configuration for the optional Native three-batch path."""

SHAPE_ONLY_CHECKPOINT = 'AC9 Native 3 saves the shape; full-state resume is not supported'


def expected_shape_restart(checkpoint):
    return (checkpoint.get('status') == 'unavailable'
            and checkpoint.get('reason') == SHAPE_ONLY_CHECKPOINT)


def selected(props):
    return bool(getattr(props, 'native_three', False))


def _check(values):
    for key, value in (values or {}).items():
        if key.startswith('s') and key[1:].isdigit() and isinstance(value, dict):
            _check(value)
        elif key == 'fast_live' and value:
            raise ValueError('AC9 Native 3 and Accurate cannot run together')
        elif key in ('graph', 'self_contact') and value is not None and not value:
            raise ValueError('AC9 Native 3 requires ' + key + '; turn off AC9 Native 3')
        elif key in ('layer', 'layers', 'layer_order', 'layer_all', 'body_motion', 'drive', 'cloth_drive') and value:
            raise ValueError('AC9 Native 3 does not support ' + key + '; turn off AC9 Native 3')
        elif key == 'checkpoint_in' and value:
            raise ValueError('AC9 Native 3 cannot restore a full-state checkpoint; restart from the shown shape')
        elif key in ('native_experimental', 'native_experimental_variant', 'native_batch_file') and value:
            raise ValueError('AC9 Native 3 does not accept a diagnostic mode or external partition')
        elif key == 'native_three' and value is not True:
            raise ValueError('AC9 Native 3 cannot be overridden')


def preflight(spec, settings):
    """Run before any result, handle, placed shape or preparation is changed."""
    _check(settings)
    _check(settings.get('cfg_override'))
    layered = [p.get('name', '?') for p in (spec or {}).get('pieces', [])
               if p.get('enabled', True) and p.get('layer', 0)]
    if layered:
        raise ValueError('AC9 Native 3 does not support Piece Layer above 0: '
                         + ', '.join(layered[:3]) + '; turn off AC9 Native 3')


def validate_config(cfg):
    if cfg.get('native_three') is not True:
        raise ValueError('Invalid AC9 Native 3 mode')
    _check(cfg)
    if not cfg.get('live'):
        raise ValueError('AC9 Native 3 is available only in Live Sim')
    if not cfg.get('graph', True) or not cfg.get('self_contact', True):
        raise ValueError('AC9 Native 3 requires graph execution and self contact')


def configure(cfg):
    """Preserve all effective material, timestep, iteration and feature settings."""
    result = dict(cfg, native_three=True)
    validate_config(result)
    return result
