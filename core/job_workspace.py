# SPDX-License-Identifier: GPL-3.0-or-later
"""Local workspaces and immutable, transactional copies of finished jobs.

No Blender calls. A failed/incomplete export never replaces the working result.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import uuid

from . import path_wait as PW
from . import result_names as RN

LINK = '.ac9_archive.json'
RECEIPT = '.ac9_archive_receipt.json'
SEAL = '.ac9_job_seal.json'
PLAN = '.ac9_export_plan.json'
# Marks a saved job being written in place (under its own name, no staging folder renamed into it): who writes it,
# so a retry finishes it and a cancel deletes only its own. Renaming a folder of files on a slow disk can stop every
# other access to that disk for seconds (measured on a USB hard disk under load: 1-12 s); writing and deleting single
# files did not (under 0.1 s).
PARTIAL = '.ac9_archive_partial.json'
REQUIRED = (RN.JOB, RN.INDEX, RN.PATTERN)       # job.json first: written last, deleted last
PATH_FIELDS = frozenset(('dir', 'pattern', 'init_from', 'checkpoint_in', 'checkpoint_out',
                         'grab_file', 'params_file', 'native_runtime_dir', 'stage'))


def work_root(preference, blender_temp):
    value = str(preference or '').strip() or str(blender_temp or '').strip()
    if not value:
        raise ValueError('No simulation working directory is available')
    return Path(value).expanduser().resolve() / 'AC9_ClothSim_work'


def _write(path, value):
    temporary = PW.io_path(path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp'))
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
        os.replace(temporary, PW.io_path(path))
    finally:
        temporary.unlink(missing_ok=True)


def _pick_sha256():
    """The faster of the two SHA-256 implementations Python carries; both give the same digest. Blender's bundled
    OpenSSL hashed at about 185 MB/s against 490 MB/s for Python's own (_sha2), while a system Python's OpenSSL did
    2400 MB/s (2026-11-25, 18 MB): the Stop of a Live that kept its frames spent most of its time here."""
    try:
        import _sha2
    except ImportError:
        return hashlib.sha256
    import time
    data = bytes(range(256)) * 4096
    standard = hashlib.sha256(data).hexdigest()
    best, best_s = hashlib.sha256, None
    for make in (hashlib.sha256, _sha2.sha256):
        try:
            t = time.perf_counter()
            same = make(data).hexdigest() == standard
            spent = time.perf_counter() - t
            if not same:
                continue
        except (TypeError, ValueError):
            continue
        except (TypeError, ValueError):
            continue
        if best_s is None or spent < best_s:
            best, best_s = make, spent
    return best


_SHA256 = None


def _sha(path):
    global _SHA256
    if _SHA256 is None:
        _SHA256 = _pick_sha256()
    digest = _SHA256()
    with PW.io_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _names(root):
    """The relative names _inventory would hash, without reading them."""
    root = PW.io_path(root, force=True) # descendants may be long even when the root is short
    out = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError('A job export must not follow directory links')
        if path.is_file() and path.name not in (LINK, RECEIPT, SEAL, PARTIAL):
            out.append(path.relative_to(root).as_posix())
    return sorted(out)          # string order, as the seal's and before's keys are compared (Path order differs)


def _stamps(root, names):
    """Size and modification time of each file: a change after it was hashed shows here without reading it again."""
    out = {}
    for rel in names:
        st = PW.io_path(root / rel).stat()
        out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def _inventory(root):
    root = PW.io_path(root, force=True)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError('A job export must not follow directory links')
        if path.is_file() and path.name not in (LINK, RECEIPT, SEAL, PARTIAL):
            result[path.relative_to(root).as_posix()] = _sha(path)
    if any(name not in result for name in REQUIRED):
        raise ValueError('The finished job is missing required result files')
    return result


def require_released(source, lease):
    source = PW.resolve(source)
    if (lease.get('job') != str(source) or not lease.get('writer_released')
            or not lease.get('gui_complete') or not lease.get('token')):
        raise ValueError('The completed job has no released writer lease')
    return source


def seal_finished(source, lease):
    """Seal only a particular completed solver lease and its GUI consumer."""
    source = require_released(source, lease)
    files = _inventory(source)
    if PW.io_path(source / SEAL).exists():
        old = json.loads(PW.io_path(source / SEAL).read_text(encoding='utf8'))
        if old.get('token') != lease['token'] or old.get('files') != files:
            raise ValueError('A sealed job was changed')
        return old['token']
    _write(source / SEAL, dict(version=1, token=lease['token'], files=files))
    return lease['token']


def _remap(value, source, target, field=None):
    if isinstance(value, dict):
        return {key: _remap(child, source, target, key) for key, child in value.items()}
    if isinstance(value, list):
        return [_remap(child, source, target, field) for child in value]
    if isinstance(value, str) and field in PATH_FIELDS:
        # JSON fields can contain paths in either Windows or portable spelling.
        for old, new in ((str(source), str(target)), (source.as_posix(), target.as_posix())):
            if value == old or value.startswith(old + '/') or value.startswith(old + '\\'):
                return new + value[len(old):]
    return value


def archived_path(source):
    """Return a committed copy only when its receipt matches this working job."""
    source = PW.resolve(source)
    try:
        link = json.loads(PW.io_path(source / LINK).read_text(encoding='utf8'))
        target = PW.resolve(link['target'])
        receipt = json.loads(PW.io_path(target / RECEIPT).read_text(encoding='utf8'))
        if (link.get('version') == receipt.get('version') == 1
                and link.get('source') == receipt.get('source') == str(source)
                and link.get('token') == receipt.get('token')
                and receipt.get('target') == str(target)
                and all(PW.io_path(target / name).is_file() for name in REQUIRED)):
            return str(target)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def saved_generation(target, generation):
    """Full readback at file-open/recovery boundaries, not during panel drawing."""
    target = PW.resolve(target)
    try:
        receipt = json.loads(PW.io_path(target / RECEIPT).read_text(encoding='utf8'))
        seal = json.loads(PW.io_path(target / SEAL).read_text(encoding='utf8'))
        return (bool(generation) and receipt.get('generation') == seal.get('token') == generation
                and receipt.get('target') == str(target)
                and _inventory(target) == receipt.get('saved_files') == seal.get('files'))
    except (OSError, ValueError, KeyError, TypeError):
        return False


class ExportCancelled(Exception):
    """A newer result of the same garment was accepted while this one was being copied: not needed any more."""


def remove_files(folder, first=(), last=()):
    """Delete a folder one file at a time, then its folders: never a rename of the whole folder (see PARTIAL).
    first are deleted before the rest and last after it (relative posix paths), so a reader that checks for a
    required file sees the folder as incomplete from the first deletion until the last."""
    folder = PW.io_path(folder, force=True)
    files = sorted(p for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
    rel = {p: p.relative_to(folder).as_posix() for p in files}
    order = ([p for p in files if rel[p] in first]
             + [p for p in files if rel[p] not in first and rel[p] not in last]
             + [p for p in files if rel[p] in last and rel[p] not in first])
    for p in order:
        p.unlink(missing_ok=True)
    for d in sorted((p for p in folder.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        d.rmdir()
    folder.rmdir()


def archive_finished(source, target, cancelled=None):
    """Publish a verified copy under its final name; retain source until readers are retired.

    The copy is written straight into target (no staging folder renamed into place, see PARTIAL): target carries
    PARTIAL while it is being written, and RECEIPT, written last, is what makes it a saved job. Readers adopt a saved
    job only by its RECEIPT. Each file arrives through a temporary name in its own folder, so no file is ever seen
    half-written under its final name, and job.json with the required arrays arrive last. A retry after a crash
    finishes a target whose PARTIAL names this source and generation.

    cancelled(), when given, is asked between the files and before publication: True stops the export, deletes the
    target only while it still carries this call's PARTIAL, and raises ExportCancelled.

    The caller owns the destination policy and must wait for solver/runtime writers
    to finish. Inventory checks also reject a source that changes during copying.
    A saved destination is never overwritten, even after an earlier failure.
    """
    # on the archive's thread: a plain resolve() would hold Blender's UI while the disk is slow (path_wait)
    source, target = PW.resolve(source), PW.resolve(target)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Working and saved jobs must be separate directories')
    if source.name != target.name or target.parent.name != 'AC9_ClothSim_jobs':
        raise ValueError('The archive must keep the unique job id')
    seal = json.loads(PW.io_path(source / SEAL).read_text(encoding='utf8')) if PW.io_path(source / SEAL).exists() else None
    names = _names(source)
    stamps = _stamps(source, names)
    if seal and seal.get('version') == 1 and isinstance(seal.get('files'), dict) and sorted(seal['files']) == names:
        # A sealed job is not hashed twice: each file's copy is hashed against the seal below, so a file that differs
        # from it is refused there, and _stamps shows one changed after its copy was read (Stop of a Live that kept
        # its frames: five full hashes of the job were most of its 1.2 s)
        before = dict(seal['files'])
    else:
        before = _inventory(source)
        if seal and (seal.get('version') != 1 or seal.get('files') != before):
            raise ValueError('A sealed job was changed')
    generation = seal['token'] if seal else None
    mark = dict(version=1, source=str(source), target=str(target), generation=generation)
    token = None
    if PW.io_path(target).exists():
        # A crash may occur after the receipt and before the source LINK. The target receipt, not the optional
        # LINK, proves this retry.
        try:
            receipt = json.loads(PW.io_path(target / RECEIPT).read_text(encoding='utf8'))
            old = receipt.get('token', '')
            if (receipt.get('version') == 1 and re.fullmatch('[0-9a-f]{32}', old)
                    and receipt.get('source') == str(source) and receipt.get('target') == str(target)
                    and receipt.get('generation') == generation
                    and receipt.get('source_files') == before
                    and receipt.get('saved_files') == _inventory(target)
                    and (not seal or saved_generation(target, seal['token']))):
                _write(source / LINK, dict(version=1, token=old, source=str(source), target=str(target)))
                PW.io_path(target / PARTIAL).unlink(missing_ok=True)
                return str(target)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # Not saved yet: only a copy this same export started (its PARTIAL names this source and generation) is
        # finished; anything else under the name is never touched.
        try:
            partial = json.loads(PW.io_path(target / PARTIAL).read_text(encoding='utf8'))
            if (not PW.io_path(target / RECEIPT).exists() and re.fullmatch('[0-9a-f]{32}', partial.get('token', ''))
                    and {key: partial.get(key) for key in mark} == mark):
                token = partial['token']
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        if token is None:
            raise FileExistsError('The saved job already exists or changed: ' + str(target))
        scan_root = PW.io_path(target, force=True)
        for path in [p for p in scan_root.rglob('*') if p.is_file()]:
            if path.relative_to(scan_root).as_posix() not in before and path.name not in (PARTIAL, SEAL):
                path.unlink()                         # leftovers of the interrupted copy (temporary files)
    else:
        PW.io_path(target.parent).mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        PW.io_path(target).mkdir()                                # refuses an existing folder
        _write(target / PARTIAL, dict(mark, token=token))

    def discard_own():
        try:
            if json.loads(PW.io_path(target / PARTIAL).read_text(encoding='utf8')).get('token') == token:
                remove_files(target, first=REQUIRED[1:], last=(REQUIRED[0], PARTIAL))
        except (OSError, ValueError, AttributeError):
            pass

    def stop_if_cancelled():
        if cancelled is not None and cancelled():
            discard_own()
            raise ExportCancelled(str(target))
    try:
        hashes = _copy_checked(source, target, before, stamps, stop_if_cancelled)
    except ValueError:
        # a working job that differs from its seal (or changed while it was read) leaves no copy of its own behind
        discard_own()
        raise
    if seal:
        if json.loads(PW.io_path(source / SEAL).read_text(encoding='utf8')) != seal:
            raise ValueError('The job seal changed during export')
        _write(target / SEAL, dict(seal, files=hashes))
    stop_if_cancelled()
    receipt = dict(version=1, token=token, source=str(source), target=str(target),
                   generation=generation, source_files=before, saved_files=hashes)
    _write(target / RECEIPT, receipt)                 # the moment it becomes a saved job
    PW.io_path(target / PARTIAL).unlink(missing_ok=True)
    _write(source / LINK, dict(version=1, token=token, source=str(source), target=str(target)))
    return str(target)


def _copy_checked(source, target, before, stamps, stop_if_cancelled):
    """Copy each file through a temporary name, checked against its hash in before; returns the saved files' hashes
    (the copy's for a plain file, read again only for the small JSON whose paths are rewritten)."""
    order = [rel for rel in before if rel not in REQUIRED] + list(REQUIRED[1:]) + [REQUIRED[0]]
    hashes = {}
    for rel in order:
        stop_if_cancelled()
        src, dst = PW.io_path(source / rel), PW.io_path(target / rel)
        remap = bool(rel in (RN.JOB, 'config.json', 'resume_gate.json')
                     or re.fullmatch(r's[1-9][0-9]*/(?:config|native_runtime)\.json', rel))
        if not remap and dst.is_file() and _sha(dst) == before[rel]:
            hashes[rel] = before[rel]
            continue                                  # already there from an interrupted copy
        dst.parent.mkdir(parents=True, exist_ok=True)
        temporary = PW.io_path(dst.with_name(dst.name + '.' + uuid.uuid4().hex[:12] + '.tmp'))
        try:
            shutil.copyfile(src, temporary)
            if _sha(temporary) != before[rel]:
                raise ValueError('The working job changed during export: ' + rel)
            if remap:
                value = json.loads(temporary.read_text(encoding='utf8'))
                temporary.write_text(json.dumps(_remap(value, source, target), ensure_ascii=False, indent=1),
                                     encoding='utf8')
            hashes[rel] = _sha(temporary) if remap else before[rel]    # only the small remapped JSON is read again
            os.replace(temporary, dst)
        finally:
            temporary.unlink(missing_ok=True)
    if _names(source) != sorted(before) or _stamps(source, sorted(before)) != stamps:
        raise ValueError('The working job changed during export')
    if _names(target) != sorted(before) or set(hashes) != set(before):
        raise ValueError("The saved job does not hold exactly the working job's files")
    return hashes


class ArchiveQueue:
    """One background writer; results are consumed by Blender's main thread."""
    def __init__(self):
        self.requests, self.results = queue.Queue(), queue.Queue()
        self.pending = set()
        self._destinations = {}
        self.thread = None
        self.lock = threading.Lock()
        # cancelled(source) -> True: an export not started or under way is no longer wanted (job_archive sets it)
        self.cancelled = None

    def submit(self, source, target):
        key = (str(PW.resolve(source)), str(PW.resolve(target)))
        return self._enqueue(key, key[0], key[1], None)

    def submit_accepted(self, source, target, generation):
        """Queue a sealed PLAN's exact logical destination without filesystem IO.

        Only finish/resume may call this with their verified canonical recovery
        string and generation. The worker revalidates the sealed PLAN, resolves
        its destination at publication, and reports the original recovery string.
        Legacy submit retains its synchronous canonical-path deduplication.
        """
        if (not isinstance(source,str) or not isinstance(target,str)
                or not Path(source).is_absolute() or not Path(target).is_absolute()
                or not isinstance(generation,str) or not generation):
            raise ValueError('A sealed recovery and absolute destination are required')
        return self._enqueue(('accepted',source,generation),source,target,generation)

    def submit_task(self, kind, work):
        """Run work() on this same thread, in order with the exports, and report {'task': kind, 'value': ...}
        (or 'error'). For the file work beside the .blend that must not run on Blender's main thread (the old runs'
        clean-up): one thread at a time touches that folder, so a check never waits behind a copy being renamed."""
        if not callable(work) or not isinstance(kind, str) or not kind:
            raise ValueError('A task needs a kind and a callable')
        return self._enqueue(('task', kind, uuid.uuid4().hex), work, None, None)

    def _enqueue(self, key, source, target, generation):
        with self.lock:
            if key in self.pending:
                if self._destinations[key] != target:
                    raise ValueError('An accepted generation is already bound to another destination')
                return False
            self.pending.add(key)
            self._destinations[key]=target
            self.requests.put((key,source,target,generation))
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name='AC9 result archive', daemon=True)
                self.thread.start()
        return True

    def _run(self):
        while True:
            try:
                key,source,target,generation = self.requests.get_nowait()
            except queue.Empty:
                # Synchronize the empty check with submit, so no request can be
                # enqueued just as the only worker is leaving.
                with self.lock:
                    if self.requests.empty():
                        self.thread = None
                        return
                continue
            if key[0] == 'task':
                try:
                    self.results.put(dict(task=key[1], value=source(), error=None))
                except BaseException as exc:
                    self.results.put(dict(task=key[1], value=None, error=str(exc)))
                finally:
                    with self.lock:
                        self.pending.discard(key)
                        self._destinations.pop(key,None)
                    self.requests.task_done()
                continue
            try:
                if generation is not None:
                    plan=json.loads(PW.io_path(Path(source)/PLAN).read_text(encoding='utf8'))
                    if (plan.get('version')!=1 or plan.get('generation')!=generation
                            or plan.get('recovery')!=source or plan.get('target')!=target
                            or not saved_generation(source,generation)):
                        raise ValueError('The queued export does not match its sealed recovery plan')
                check = self.cancelled
                path = archive_finished(source, target,
                                        cancelled=(lambda s=source, c=check: c(s)) if check is not None else None)
                self.results.put(dict(source=source, target=path, error=None))
            except ExportCancelled:
                self.results.put(dict(source=source, target=target, error=None, cancelled=True))
            except BaseException as exc:
                self.results.put(dict(source=source, target=target, error=str(exc)))
            finally:
                with self.lock:
                    self.pending.discard(key)
                    self._destinations.pop(key,None)
                self.requests.task_done()

    def collect(self):
        rows = []
        while True:
            try:
                rows.append(self.results.get_nowait())
            except queue.Empty:
                return rows
