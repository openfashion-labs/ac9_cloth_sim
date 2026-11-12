# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable per-variant preload inputs; never rewrite a saved job config."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

MATERIAL_FIELDS = ('membrane', 'bw_comp_ratio', 'bw_comp_width', 'bw_comp_scale', 'bw_hess_reg')
LAYER_FIELDS = ('layers_recovery_friction', 'layers_crossing_cache')
COMPILE_FIELDS = MATERIAL_FIELDS + LAYER_FIELDS


def variant_key(config):
    return (tuple((key, config.get(key)) for key in MATERIAL_FIELDS)
            + (('layers_recovery_friction', float(config.get('layers_recovery_friction', 1.0))),
               ('layers_crossing_cache', bool(config.get('layers_crossing_cache', True))),
               ('native_three', bool(config.get('native_three')))))


def snapshot(config):
    result = {key: config[key] for key in COMPILE_FIELDS if key in config}
    if config.get('native_three'):
        result.update(native_three=True, live=True)
    return result


def choose(previous, physical_fabric, native_three):
    if (previous.get('membrane') == 'bw') == bool(physical_fabric):
        config = {key: previous[key] for key in MATERIAL_FIELDS if key in previous}
    else:
        config = {'membrane': 'bw', 'bw_hess_reg': 1.0} if physical_fabric else {}
    if not previous and physical_fabric:
        config = {'membrane': 'bw', 'bw_hess_reg': 1.0}
    config.update({key: previous[key] for key in LAYER_FIELDS if key in previous})
    if native_three:
        config.update(native_three=True, live=True)
    return config


def materialize(config):
    raw = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')
    if len(raw) > 4096:
        raise ValueError('Invalid preload configuration size')
    root = Path(tempfile.gettempdir()) / 'ac9_solver_preload'
    root.mkdir(exist_ok=True)
    path = root / (str(os.getpid()) + '_' + hashlib.sha256(raw).hexdigest() + '.json')
    try:
        if path.read_bytes() == raw:
            return str(path)
    except FileNotFoundError:
        pass
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)
