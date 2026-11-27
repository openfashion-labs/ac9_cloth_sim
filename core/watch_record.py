# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime watch records, separate from guard records sharing anomaly.json."""
import json
from pathlib import Path


def is_watch(record):
    return (isinstance(record, dict) and
            ('drift_mm_per_frame' in record or record.get('kind') in ('tug of war', 'seam turned over')))


def configured(settings):
    from . import presets
    override = settings.get('cfg_override') or {}
    base = {'watch': {'drift_mm': .4, 'tug': {}}} if settings.get('watch') else {}
    global_override = {k:v for k,v in override.items() if not (k.startswith('s') and k[1:].isdigit())}
    presets._override(base, 0, global_override)
    if base.get('watch'):
        return True
    for key, value in override.items():
        if key.startswith('s') and key[1:].isdigit() and isinstance(value, dict):
            cfg = dict(base);presets._override(cfg, 0, value)
            if cfg.get('watch'):
                return True
    return False


def panel_state(value, panel):
    """Describe effective research overrides without mutating the saved checkbox."""
    from . import presets
    try:
        named = presets.named(value)[1]
        settings = presets.resolve(value, panel, environ={})
        override = settings.get('cfg_override') or {}
        staged = any(k.startswith('s') and k[1:].isdigit() and isinstance(v,dict) and 'watch' in v
                     for k,v in override.items())
        explicit = named.get('watch') is not None or 'watch' in override or staged
        return dict(mode='STAGED' if staged else 'ON' if configured(settings) else 'OFF',
                    editable=not explicit, explicit=explicit)
    except (ValueError, TypeError, AttributeError):
        return dict(mode='INVALID', editable=False, explicit=True)


def summarize(stage_dirs):
    enabled, known, records, all_records, errors = False, 0, [], [], []
    for index, directory in enumerate(stage_dirs, 1):
        directory = Path(directory)
        stage = int(directory.name[1:]) if directory.name.startswith('s') and directory.name[1:].isdecimal() else index
        try:
            cfg = json.loads((directory/'config.json').read_text(encoding='utf-8'))
            enabled = enabled or bool(cfg.get('watch'))
            known += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        path = directory/'anomaly.json'
        if not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding='utf-8'))
            items = items if isinstance(items, list) else [items]
            all_records.extend(dict(r, stage=stage) for r in items if isinstance(r,dict))
            records.extend(dict(r, stage=stage) for r in items if is_watch(r))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    return dict(enabled=True if enabled or records else False if known and not errors else None,
                count=len(records), first=records[0] if records else None, errors=errors,
                all_count=len(all_records), first_any=all_records[0] if all_records else None)


def status(record):
    if not record or record.get('enabled') is None or record.get('errors'):
        return 'Run anomaly recording: state unavailable; see the run log', ()
    if record['enabled'] is False:
        return 'Run anomaly recording: off', ()
    if not record['count']:
        return 'Run anomaly recording: on, no candidates recorded', ()
    return 'Run anomaly recording: {0} candidate(s); see the run log', (record['count'],)
