# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned, optional runtime I/O. The job directory remains the result authority."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager

DIR_KEY = 'native_runtime_dir'
TOKEN_KEY = 'native_runtime_token'
DESCRIPTOR = 'native_runtime.json'
MARKER = 'owner.json'
KNOWN_FILES = frozenset((MARKER, 'worker.lock', 'console.txt', 'progress.txt', 'progress_tmp.txt',
                         'live.npy', 'live_tmp.npy', 'grab.json', 'params.json'))


@contextmanager
def _stage_claim(stage):
    """OS-owned lock: released even when the acquiring process is killed."""
    with open(Path(stage) / 'native_runtime.lock', 'a+b') as claim:
        if os.name == 'nt':
            import msvcrt
            claim.seek(0, os.SEEK_END)
            if claim.tell() == 0:
                claim.write(b'\0'); claim.flush()
            claim.seek(0)
            msvcrt.locking(claim.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                claim.seek(0)
                msvcrt.locking(claim.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(claim.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(claim.fileno(), fcntl.LOCK_UN)


def _base():
    return Path(tempfile.gettempdir()).resolve() / 'ac9_native_runtime'


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _read(path):
    with open(path, encoding='utf8') as stream:
        return json.load(stream)


def _write(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(temporary, 'x', encoding='utf8') as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate(stage, directory, token, *, require_current=True):
    stage = Path(stage).resolve()
    directory = Path(directory).resolve()
    if not isinstance(token, str) or re.fullmatch(r'[0-9a-f]{32}', token) is None:
        raise ValueError('Invalid Native runtime token')
    if directory.parent != _base() or directory.name != token:
        raise ValueError('Native runtime directory is outside its owned cache')
    marker = _read(directory / MARKER)
    if marker.get('version') != 1 or marker.get('token') != token or marker.get('stage') != _canonical(stage):
        raise ValueError('Native runtime owner does not match this stage')
    if require_current:
        descriptor = _read(stage / DESCRIPTOR)
        if descriptor.get('token') != token or descriptor.get('stage') != _canonical(stage):
            raise ValueError('Native runtime owner was retired')
    return marker


def _status(stage, directory, token, **fields):
    with _stage_claim(stage):
        return _status_claimed(stage, directory, token, **fields)


def _status_claimed(stage, directory, token, **fields):
    marker = _validate(stage, directory, token, require_current=False)
    marker.update(fields)
    _write(directory / MARKER, marker)
    descriptor = _read(stage / DESCRIPTOR) if (stage / DESCRIPTOR).is_file() else {}
    if descriptor.get('token') == token and descriptor.get('stage') == _canonical(stage):
        descriptor.update(fields)
        _write(stage / DESCRIPTOR, descriptor)


class RuntimeOwner:
    """Created by run entry, after Prepare; never by a read query."""
    def __init__(self, stage, config):
        if config.get('native_three') is not True or config.get('fast_live'):
            raise ValueError('Native runtime I/O requires the isolated Native route')
        self.stage = Path(stage).resolve()
        self.stage.mkdir(exist_ok=True)
        self.token = uuid.uuid4().hex
        self.directory = _base() / self.token
        self.controls = {}
        try:
            with _stage_claim(self.stage):
                if (self.stage / DESCRIPTOR).exists():
                    previous = _read(self.stage / DESCRIPTOR)
                    if previous.get('version') != 1 or previous.get('stage') != _canonical(self.stage):
                        raise ValueError('Unrecognized Native runtime descriptor')
                    if previous.get('phase') in ('allocated', 'running', 'cleanup_pending'):
                        raise ValueError('This stage already has a Native runtime owner')
                self.directory.mkdir(parents=True, exist_ok=False)
                data = dict(version=1, token=self.token, stage=_canonical(self.stage),
                            directory=str(self.directory), owner_pid=os.getpid(), created_ns=time.time_ns(), phase='allocated')
                _write(self.directory / MARKER, data)
                for key, name in [('grab_file', 'grab.json'), ('params_file', 'params.json')]:
                    if config.get(key) is not None and config.get(key) != '':
                        source = Path(config[key])
                        target = self.directory / name
                        if source.is_file():
                            shutil.copy2(source, target)
                        self.controls[key] = str(target)
                _write(self.stage / DESCRIPTOR, data)
        except Exception:
            if (self.directory / MARKER).exists():
                self.abort_before_launch()
            elif self.directory.exists() and not any(self.directory.iterdir()):
                self.directory.rmdir()
            raise

    def settings(self):
        return {DIR_KEY: str(self.directory), TOKEN_KEY: self.token, **self.controls}

    def abort_before_launch(self):
        """Caller must have not launched a worker; no reader has been published."""
        marker = _validate(self.stage, self.directory, self.token, require_current=False)
        if marker.get('phase') not in ('allocated', 'retained') or marker.get('worker_pid') or (self.directory / 'worker.lock').exists():
            raise ValueError('A started Native runtime cannot be aborted as unlaunched')
        _status(self.stage, self.directory, self.token, phase='aborted')
        return self.release(readers_released=True)

    def release(self, *, readers_released=False, preserve=False):
        if not readers_released:
            raise ValueError('Runtime readers must be released before cleanup')
        with _stage_claim(self.stage):
            return self._release_claimed(preserve)

    def _release_claimed(self, preserve):
        if self.directory.parent != _base() or self.directory.name != self.token:
            raise ValueError('Unsafe Native runtime cleanup target')
        descriptor = _read(self.stage / DESCRIPTOR) if (self.stage / DESCRIPTOR).is_file() else {}
        current = (descriptor.get('token') == self.token and descriptor.get('stage') == _canonical(self.stage)
                   and descriptor.get('directory') == str(self.directory))
        credential = self.stage / ('native_runtime_cleanup_' + self.token + '.json')
        backup = _read(credential) if credential.is_file() else None
        if backup is not None and (backup.get('version') != 1 or backup.get('token') != self.token or
                backup.get('stage') != _canonical(self.stage) or backup.get('directory') != str(self.directory) or
                backup.get('phase') != 'cleanup_pending'):
            raise ValueError('Invalid Native runtime cleanup credential')
        if not self.directory.exists():
            if backup is not None:
                try:
                    credential.unlink()
                except OSError:
                    return False
            if current and descriptor.get('phase') == 'cleanup_pending':
                descriptor.update(phase='retired'); _write(self.stage / DESCRIPTOR, descriptor)
            return True
        if (self.directory / MARKER).exists():
            marker = _validate(self.stage, self.directory, self.token, require_current=False)
        elif backup is not None:
            marker = backup
        elif current and descriptor.get('phase') == 'cleanup_pending':
            # The current descriptor remains the cleanup credential if rmdir
            # failed after the marker was removed. Allocation rejects this phase.
            marker = dict(descriptor)
        else:
            raise ValueError('Missing Native runtime cleanup credential')
        if preserve or marker.get('phase') not in ('complete', 'aborted', 'cleanup_pending'):
            return False
        files = list(self.directory.iterdir())
        if any(not path.is_file() or path.name not in KNOWN_FILES for path in files):
            if (self.directory / MARKER).exists():
                _status_claimed(self.stage, self.directory, self.token, phase='retained', reason='Unexpected cache file')
            return False
        marker.update(phase='cleanup_pending')
        _write(self.directory / MARKER, marker)
        # Keep a per-token credential until rmdir completes, even if a newer
        # run has already become the stage's current descriptor.
        _write(credential, marker)
        if current:
            descriptor.update(phase='cleanup_pending'); _write(self.stage / DESCRIPTOR, descriptor)
        try:
            for path in files:
                if path.name != MARKER:
                    path.unlink()
            (self.directory / MARKER).unlink()
            self.directory.rmdir()
            credential.unlink()
        except OSError as error:
            marker.update(reason=str(error))
            try:
                _write(self.directory / MARKER, marker)
            except OSError:
                pass  # The current cleanup_pending descriptor remains valid.
            return False
        if current:
            descriptor.update(phase='retired'); _write(self.stage / DESCRIPTOR, descriptor)
        return True


class WorkerRuntime:
    def __init__(self, config, stage):
        self.stage = Path(stage).resolve()
        self.directory = Path(config[DIR_KEY]).resolve()
        self.token = config[TOKEN_KEY]
        marker = _validate(self.stage, self.directory, self.token)
        if marker.get('phase') != 'allocated' or (self.directory / 'worker.lock').exists():
            raise ValueError('Native runtime has already been started')
        self.expected_points = None
        self.closed = False
        self.published = False
        self.success = False
        self.stream = None
        self.claimed = False

    def start(self):
        if self.closed or self.stream is not None:
            raise ValueError('Native runtime console cannot be started twice')
        self.old_stdout, self.old_stderr = sys.stdout, sys.stderr
        with _stage_claim(self.stage):
            marker = _validate(self.stage, self.directory, self.token)
            if marker.get('phase') != 'allocated':
                raise ValueError('Native runtime is no longer allocated')
            with open(self.directory / 'worker.lock', 'x', encoding='utf8') as lock:
                lock.write(str(os.getpid()))
            self.claimed = True
            _status_claimed(self.stage, self.directory, self.token, worker_pid=os.getpid(), phase='running')
        self.old_stdout.flush()
        if self.old_stderr is not self.old_stdout:
            self.old_stderr.flush()
        prefix = ''
        name = getattr(self.old_stdout, 'name', None)
        if isinstance(name, (str, os.PathLike)) and Path(name).is_file() and self.old_stdout.seekable():
            prefix = Path(name).read_text(encoding='utf8')
        self.stream = open(self.directory / 'console.txt', 'w', encoding='utf8')
        self.stream.write(prefix)
        sys.stdout = sys.stderr = self.stream

    def set_expected_points(self, count):
        if self.expected_points is None:
            self.expected_points = int(count)
            marker = _validate(self.stage, self.directory, self.token)
            marker['points'] = self.expected_points
            # This startup-only metadata write stays in the owned runtime dir.
            _write(self.directory / MARKER, marker)
        elif self.expected_points != count:
            raise ValueError('Native runtime output point count changed')

    def publish(self):
        if self.published:
            return
        _validate(self.stage, self.directory, self.token)
        live = self.directory / 'live.npy'
        if live.exists():
            import numpy as np
            q = np.load(live, allow_pickle=False)
            if (not np.issubdtype(q.dtype, np.floating) or self.expected_points is None or q.shape != (self.expected_points, 3) or
                    not np.isfinite(q).all()):
                raise ValueError('Invalid Native runtime live output')
        for name in ['live.npy', 'progress.txt']:
            source = self.directory / name
            if source.is_file():
                target = self.stage / (name + '.' + self.token + '.tmp')
                try:
                    shutil.copy2(source, target)
                    os.replace(target, self.stage / name)
                finally:
                    if target.exists():
                        target.unlink()
        self.published = True

    def close(self):
        if self.closed:
            return
        self.closed = True
        if not self.claimed:
            return
        if sys.stdout is self.stream:
            sys.stdout = self.old_stdout
        if sys.stderr is self.stream:
            sys.stderr = self.old_stderr
        try:
            if self.stream is not None:
                self.stream.flush()
                self.stream.close()
            self.publish()
            content = (self.directory / 'console.txt').read_text(encoding='utf8')
            if self.old_stdout.seekable():
                self.old_stdout.seek(0)
                self.old_stdout.write(content)
                self.old_stdout.truncate()
            else:
                self.old_stdout.write(content)
            self.old_stdout.flush()
            _status(self.stage, self.directory, self.token, phase='complete' if self.success else 'failed')
        except Exception:
            _status(self.stage, self.directory, self.token, phase='failed')
            raise
