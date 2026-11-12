"""Read result-file capabilities without repairing Blender data; explicit operations own repair."""
from dataclasses import dataclass
import os

import bpy


@dataclass(frozen=True)
class JobReference:
    path: str
    source: str
    has_metadata: bool
    has_index: bool
    is_directory: bool

    @property
    def has_reference_files(self):
        # This describes files, not a promise that a particular solver can resume them.
        return self.has_metadata and self.has_index


def inspect(path, source='property'):
    path = str(path or '')
    metadata = bool(path) and os.path.isfile(os.path.join(path, 'job.json'))
    index = bool(path) and os.path.isfile(os.path.join(path, 'scene.idx.npz'))
    directory = metadata or index or (bool(path) and os.path.isdir(path))
    return JobReference(path, source, metadata, index, directory)


def _absolute(g, path):
    return bpy.path.abspath(str(path), library=g.library) if str(path).startswith('//') else str(path)


def resolve(g, display=False):
    """Preserve the two existing read contracts: display accepts a stored folder, result needs both files.

    Both accept a cache-derived index-only job. Missing metadata is retained in the descriptor rather than
    converted into either 'fully resumable' or 'no visible result'. No object, Text, or file is written.
    """
    if g is None:
        return None
    stored = g.get('ac9_job')
    if stored:
        ref = inspect(_absolute(g, stored))
        if (ref.is_directory if display else ref.has_reference_files):
            return ref
    for modifier in g.modifiers:
        if modifier.type == 'MESH_CACHE' and modifier.filepath:
            path = bpy.path.abspath(modifier.filepath, library=g.library)
            ref = inspect(os.path.dirname(path), 'cache')
            if ref.has_index:
                return ref
    return None


def repair(g):
    """Persist a resolved legacy cache reference only inside an explicit operation/load boundary. Idempotent."""
    ref = resolve(g)
    if ref is None or ref.source != 'cache' or str(g.get('ac9_job') or '') == ref.path:
        return False
    if not getattr(g, 'is_editable', g.library is None):
        return False
    g['ac9_job'] = ref.path
    return True
