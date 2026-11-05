# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker-owned CPU tasks for independent float64 hinge angles.

Native's float32 chart and activity mask stay on the calling thread. No Warp API
is called here, and the caller retains the existing global energy reductions.
"""
from concurrent.futures import ThreadPoolExecutor, wait
import os
import threading
import numpy as np

PARALLEL_THRESHOLD = 32768


def _angles(q, edge, chart):
    a, b, c, d = [np.asarray(q, float)[edge[:, i]] for i in range(4)]
    n1 = np.cross(c-a, d-a)
    n2 = np.cross(d-b, c-b)
    direction = d-c
    n1 /= np.linalg.norm(n1, axis=1)[:, None]
    n2 /= np.linalg.norm(n2, axis=1)[:, None]
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    value = np.arctan2(np.sum(np.cross(n1, n2)*direction, axis=1),
                       np.sum(n1*n2, axis=1))
    value += 2*np.pi*np.rint((chart.astype(float)-value)/(2*np.pi))
    return value


def _task(q, edge, chart, errors):
    # NumPy error settings are thread-local. Match the owning worker's policy.
    with np.errstate(**errors):
        return _angles(q, edge, chart)


class CpuHingeGeometry:
    def __init__(self):
        self.workers = min(4, os.cpu_count() or 1)
        self._executor = None
        self._closed = False
        self._evaluation = threading.Lock()

    def angles(self, q, edge, chart):
        if self._closed:
            raise RuntimeError('Fast Live CPU geometry owner is closed')
        if not self._evaluation.acquire(blocking=False):
            raise RuntimeError('Fast Live CPU geometry cannot be reentered')
        try:
            count = len(edge)
            if count < PARALLEL_THRESHOLD or self.workers == 1:
                return _angles(q, edge, chart)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.workers, thread_name_prefix='AC9 Fast Angles')
            chunk = max(2048, ((count+self.workers*8-1)//(self.workers*8))*8)
            errors = np.geterr()
            futures = []
            try:
                for start in range(0, count, chunk):
                    stop = min(start+chunk, count)
                    futures.append(self._executor.submit(
                        _task, q, edge[start:stop], chart[start:stop], errors))
                parts = [future.result() for future in futures]
            finally:
                # A failed task must not let the caller reuse q while siblings run.
                wait(futures)
            return np.concatenate(parts)
        finally:
            self._evaluation.release()

    def close(self):
        self._closed = True
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
