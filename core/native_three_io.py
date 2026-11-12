# SPDX-License-Identifier: GPL-3.0-or-later
"""One run's temporary live I/O; job references and final results stay canonical."""
import json
import os
from pathlib import Path
import time
from ..solver.native_runtime_io import RuntimeOwner


class FrontendRuntime:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        config = json.loads(self.config_path.read_text(encoding='utf8'))
        self.owner = RuntimeOwner(self.config_path.parent, config)
        self.proc = None
        self.retry_since = None
        self.error = None
        temporary = self.config_path.with_name('native_config_' + self.owner.token + '.tmp')
        try:
            effective = dict(config, **self.owner.settings())
            temporary.write_text(json.dumps(effective, ensure_ascii=False), encoding='utf8')
            os.replace(temporary, self.config_path)
        except Exception:
            self.owner.abort_before_launch()
            raise
        finally:
            if temporary.exists():
                temporary.unlink()

    def path(self, name):
        if name not in ('live.npy', 'progress.txt', 'grab.json', 'params.json'):
            raise ValueError('Unknown Native live I/O file')
        return str(self.owner.directory / name)

    def launched(self, proc):
        self.proc = proc

    def close(self):
        """True: collected; None: retry after process exit; False: retain diagnostics."""
        now = time.monotonic()
        if self.retry_since is None:
            self.retry_since = now
        if self.proc is not None and self.proc.poll() is None:
            return None if now - self.retry_since < 60. else False
        try:
            if self.owner.release(readers_released=True):
                if getattr(self.proc,'is_session_job',False):self.proc.release()
                return True
            # A failed spawn/request may have ended without claiming the runtime.
            # The owner's own guard still rejects worker.pid/lock/started phases.
            try:
                if self.owner.abort_before_launch():
                    if getattr(self.proc,'is_session_job',False):self.proc.release()
                    return True
            except ValueError:
                pass
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        return None if now - self.retry_since < 5. else False
