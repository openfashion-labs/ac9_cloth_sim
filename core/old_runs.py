# SPDX-License-Identifier: GPL-3.0-or-later
"""Which of a garment's result folders are old, and deleting them -- without Blender.

blend_record takes a snapshot on Blender's main thread (strings only: no call to the file system there) and the
judgement below runs on the result archive's thread (job_archive), the one thread that reads, writes and deletes
beside the .blend. The folder beside a .blend can be on a slow or busy disk: a folder renamed there (the Live
result's copy) held a path check on the same folder for 4-21 s, and the main thread waited that long when it made
the check (Live's Stop froze for 6-11 s, measured on a USB hard disk). The Clean Up button and the disk usage call
the same functions from where waiting is fine (a press, a thread of their own).

A snapshot (dict, all paths as the garments store them):
  this_file   the .blend open now, as blend_record._this_file() spells it ('' when unsaved)
  roots       the AC9_ClothSim_jobs folders this file's runs write to (beside it, Live's working and kept copies,
              the system temp folder)
  temp_roots  the ones of no .blend (Live's working and kept copies, the system temp folder)
  saved       the folders the saved .blend on disk reads
  readers     the folders any object reads (its result, its Mesh Cache's folder)
  busy        the folders being written or copied now
  garments    [{'name', 'entries': [{'path', 'file'}], 'current': [paths]}]
"""
import json
import os

from . import job_workspace as W

JOBS_DIR = 'AC9_ClothSim_jobs'
from .result_names import JOB, INDEX  # noqa: E402


def canon(path):
    """The one spelling of a folder, resolving links: reads the disk (never on Blender's main thread; path_wait lets
    the main thread run while it waits)."""
    try:
        return os.path.normcase(str(W.PW.resolve(str(path))))
    except (OSError, ValueError):
        return ''


def spelled(path):
    """The same spelling as canon for a path without links, from the string alone (safe on the main thread)."""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))
    except (OSError, ValueError):
        return ''


def simulated_root(job):
    """The simulated result a chain of carried edits (job.json 'from', 'root') started from, or None
    (draft_tool._simulated_root, reading job.json only)."""
    for _ in range(50):
        try:
            with open(os.path.join(job, JOB), encoding='utf-8') as f:
                info = json.load(f)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(info, dict) or not str(info.get('kind', '')).startswith('pattern edit carried'):
            return job
        job = info.get('root') if info.get('root') and os.path.isdir(info['root']) else info.get('from')
        if not job:
            return None
    return None


def _mine(entry, this_file, temp):
    """Written while this .blend was open, or while unsaved into a temporary folder (no other .blend names it)."""
    f = entry.get('file')
    if f is None:
        return False
    if f == this_file:
        return True
    return f == '' and canon(os.path.dirname(os.path.normpath(entry['path']))) in temp


def is_job_folder(path, roots):
    """Only a folder a run or carry made is ever deleted: in an AC9_ClothSim_jobs folder this file's runs write to,
    with a result in it."""
    parent = os.path.dirname(os.path.normpath(path))
    return (os.path.basename(parent) == JOBS_DIR and os.path.isdir(path) and canon(parent) in roots
            and any(os.path.exists(os.path.join(path, f)) for f in (JOB, INDEX)))


def judge(snap):
    """[(garment name, folder)] -- the old folders: written by a garment while this .blend was open, not its latest
    result (nor the one that was carried from), read by no object, not named by the saved .blend, not being
    written."""
    roots = {canon(p) for p in snap.get('roots', ())}
    temp = {canon(p) for p in snap.get('temp_roots', ())}
    skip = {canon(p) for key in ('saved', 'readers', 'busy') for p in snap.get(key, ())}
    this_file = snap.get('this_file', '')
    out = []
    for g in snap.get('garments', ()):
        keep = set()
        for cur in g.get('current', ()):
            if cur:
                keep.add(canon(cur))
                r = simulated_root(cur)
                if r and os.path.isdir(r):
                    keep.add(canon(r))
        seen = set()
        for e in g.get('entries', ()):
            p = e['path']
            c = canon(p)
            if (not c or c in seen or c in keep or c in skip or not _mine(e, this_file, temp)
                    or not is_job_folder(p, roots)):
                continue
            seen.add(c)
            out.append((g['name'], p))
    return out


def size(path):
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


def sweep(snap, still_free=None):
    """Delete the old folders. still_free(path) is asked right before each deletion (the .blend was not undone or
    reloaded since the snapshot, the folder is not being written now); False keeps it. Returns
    {'gone': {garment: [paths]}, 'missing': {garment: [paths no longer there]}, 'deleted': n, 'freed': bytes}."""
    gone, deleted, freed = {}, 0, 0
    for name, p in judge(snap):
        if still_free is not None and not still_free(p):
            continue
        n = size(p)
        # one file at a time, never a rename of the folder: renaming a folder of files on a slow disk stopped every
        # other access to that disk for seconds, deleting single files did not (job_workspace.PARTIAL). The arrays a
        # reader requires go first and job.json last, so the folder reads as incomplete from the first deletion.
        try:
            W.remove_files(p, first=W.REQUIRED[1:], last=(JOB,))
        except OSError as exc:
            print("AC9 Cloth Sim: an old run's folder was not fully deleted:", exc)
            if os.path.isfile(os.path.join(p, INDEX)):
                continue                              # nothing gone yet: still a whole result
            # past its first deletion it reads as incomplete and is never adopted again: gone from the garment
        deleted += 1
        freed += n
        gone.setdefault(name, []).append(p)
    missing = {}
    for g in snap.get('garments', ()):
        lost = [e['path'] for e in g.get('entries', ()) if e['path'] not in gone.get(g['name'], ())
                and not os.path.isdir(e['path'])]
        if lost:
            missing[g['name']] = lost
    return dict(gone=gone, missing=missing, deleted=deleted, freed=freed)


def usage(snap, folders):
    """(bytes of these folders that exist, bytes of the old ones among them) -- for the panel, on a thread."""
    old = {canon(p) for _g, p in judge(snap)}
    seen, total, old_b = set(), 0, 0
    for p in folders:
        c = canon(p) if p else ''
        if not c or c in seen or not os.path.isdir(p):
            continue
        seen.add(c)
        n = size(p)
        total += n
        if c in old:
            old_b += n
    return total, old_b
