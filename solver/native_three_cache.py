# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded numeric plan cache. No model, material, state or GPU resource is saved."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from functools import lru_cache
import numpy as np

VERSION = 1
MAX_POINTS = 120000
MAX_BYTES = 1048576
MAX_ENTRIES = 32
PREFIX = 'ac9_three_'


def enabled():
    return os.environ.get('AC9_NATIVE_PLAN_CACHE', '1') != '0'


def directory():
    return Path(os.environ.get('AC9_NATIVE_PLAN_CACHE_DIR',
                str(Path(tempfile.gettempdir()) / 'ac9_native_three_plans' / 'v1'))).resolve()


@lru_cache(maxsize=1)
def algorithm_id():
    digest = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in ('native_three_automatic.py', 'native_three_cache.py',
                 'native_experimental_coloring.py', 'native_jacobi_diagnostic.py'):
        digest.update(name.encode('ascii'))
        digest.update((here / name).read_bytes().replace(b'\r\n', b'\n'))
    for name in ('numpy', 'warp-lang', 'newton'):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = 'not-installed'
        digest.update((name + ':' + version).encode('utf8'))
    return digest.hexdigest()


def input_key(model, bound=None):
    """Include ordered structure and corresponding supports, never object names."""
    count = int(model.particle_count)
    if count < 3 or count > MAX_POINTS:
        raise ValueError('Native three plan cache exceeds its input range')
    digest = hashlib.sha256()
    digest.update(json.dumps([VERSION, algorithm_id(), count, int(model.tri_count),
                             int(model.edge_count), int(model.spring_count),
                             int(model.tet_count)], separators=(',', ':')).encode('ascii'))

    def array(name, values):
        values = np.asarray(values)
        if not np.issubdtype(values.dtype, np.integer):
            raise ValueError('Native three structure must contain integer indices')
        values = np.ascontiguousarray(values.astype(values.dtype.newbyteorder('<'), copy=False))
        header = json.dumps([name, values.dtype.str, list(values.shape)], separators=(',', ':')).encode('ascii')
        digest.update(len(header).to_bytes(4, 'little')); digest.update(header)
        digest.update(values.nbytes.to_bytes(8, 'little')); digest.update(values.tobytes())
        return values

    groups = [np.asarray(group.numpy()) for group in model.particle_color_groups]
    active = np.sort(np.concatenate(groups)) if groups else np.empty(0, np.int32)
    if (len(active) < 3 or np.any(active < 0) or np.any(active >= count)
            or len(np.unique(active)) != len(active)):
        raise ValueError('Invalid Native three independent point membership')
    array('independent', active)
    followers = np.asarray(bound.vertices.numpy()) if bound is not None else np.empty(0, np.int32)
    supports = np.asarray(bound.triangles.numpy()) if bound is not None else np.empty((0, 3), np.int32)
    if (followers.ndim != 1 or supports.shape != (len(followers), 3)
            or len(np.unique(followers)) != len(followers)
            or np.any(followers < 0) or np.any(followers >= count)
            or np.any(supports < 0) or np.any(supports >= count)
            or np.isin(active, followers).any()):
        raise ValueError('Invalid Native three dependent point membership')
    array('followers', followers); array('supports', supports)
    for name, width, length in [('tri_indices', 3, model.tri_count),
                                ('edge_indices', 4, model.edge_count),
                                ('spring_indices', 2, model.spring_count),
                                ('tet_indices', 4, model.tet_count)]:
        source = getattr(model, name, None)
        if source is None and length:
            raise ValueError('Missing Native three structural indices')
        values = np.asarray(source.numpy()).reshape(-1, width) if source is not None else np.empty((0, width), np.int32)
        # The plan constructors consume all triangle/edge rows, but only the
        # declared spring prefix. Hash exactly those same ranges.
        used = values[:length] if name == 'spring_indices' else values
        if np.any(used >= count) or np.any(used < -1):
            raise ValueError('Invalid Native three structural indices')
        array(name, used)
    return digest.hexdigest()


def membership_hash(colors):
    return hashlib.sha256(np.asarray(colors, dtype='<i4').tobytes()).hexdigest()


def _path(key):
    if re.fullmatch('[0-9a-f]{64}', key) is None:
        raise ValueError('Invalid Native three cache key')
    return directory() / (PREFIX + key + '.json')


def load(key, count):
    """Unreadable, obsolete or corrupted cache data is a miss, not valid input."""
    try:
        with _path(key).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get('version') != VERSION
                or record.get('key') != key or record.get('count') != count):
            return None
        values = record.get('colors')
        if (not isinstance(values, list) or len(values) != count
                or any(type(v) is not int or v < -1 or v > 2 for v in values)):
            return None
        colors = np.asarray(values, dtype=np.int32)
        return colors if membership_hash(colors) == record.get('membership_sha256') else None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return None


def save(key, colors):
    colors = np.asarray(colors)
    if (colors.dtype != np.int32 or colors.ndim != 1 or not 3 <= len(colors) <= MAX_POINTS
            or np.any(colors < -1) or np.any(colors > 2)):
        raise ValueError('Invalid Native three cached membership')
    record = dict(version=VERSION, key=key, count=len(colors), colors=colors.tolist(),
                  membership_sha256=membership_hash(colors))
    raw = json.dumps(record, separators=(',', ':')).encode('ascii')
    if len(raw) > MAX_BYTES:
        raise ValueError('Native three plan cache record is too large')
    target = _path(key); temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('xb') as stream:
            stream.write(raw)
        os.replace(temporary, target)
        # Only files in this cache's own namespace; never recursive removal.
        entries = [p for p in target.parent.iterdir()
                   if re.fullmatch(PREFIX + '[0-9a-f]{64}\\.json', p.name) and p.is_file()]
        entries = [p for p in entries if p != target]
        entries.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in entries[MAX_ENTRIES - 1:]:
            path.unlink()
        return True
    except OSError:
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
