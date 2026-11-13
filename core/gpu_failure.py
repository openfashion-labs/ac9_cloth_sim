# SPDX-License-Identifier: GPL-3.0-or-later
"""Why a solver run that left no run log failed, when the GPU is the cause: read the run's console output.

A CUDA driver older than the CUDA toolkit Warp is built with makes Warp build the kernels itself (CUBIN) instead of
handing them to the driver, and fall back from parts of graph capture; the add-on is tested on drivers at or above
the toolkit. A kernel build that fails there says only "stage 1 failed", which tells the user nothing to do. Warp
prints its toolkit and the driver at start ("CUDA Toolkit 12.9, Driver 12.2"), so the message can ask for a driver
update and say which version is needed. A driver as new as the toolkit builds the kernels itself (PTX JIT) and can
fail too: that is the add-on's fault, and a driver update will not help. No Blender import: tested on its own (tests/test_gpu_failure.py).
"""
import os
import re

VERSIONS = re.compile(r'CUDA Toolkit (\d+)\.(\d+), Driver (\d+)\.(\d+)')
# what Warp writes when the GPU side cannot build or load a kernel, or cannot use the driver at all
BUILD_FAILED = ('CUDA kernel build failed', 'ptxas error', 'NVRTC_ERROR', 'insufficient CUDA driver version',
                'CUDA driver not found', 'PTX JIT compilation failed', 'Failed to load CUDA module')
TAIL = 400_000          # the last bytes of each file are enough: Warp's start line and the traceback


def _read_tail(path):
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            n = f.tell()
            f.seek(max(0, n - TAIL))
            head = b''
            if n > TAIL:            # Warp's start line is at the very top of a long file
                f.seek(0)
                head = f.read(4096)
                f.seek(n - TAIL)
            return (head + f.read()).decode('utf-8', 'replace')
    except OSError:
        return ''


def diagnose(paths):
    """{'driver': (major, minor) or None, 'toolkit': ... or None, 'old_driver': bool} when the output shows the GPU
    could not build or load the simulation's kernels, else None."""
    text = '\n'.join(_read_tail(p) for p in paths if p)
    if not any(m in text for m in BUILD_FAILED):
        return None
    found = VERSIONS.search(text)
    toolkit = driver = None
    if found:
        toolkit = (int(found.group(1)), int(found.group(2)))
        driver = (int(found.group(3)), int(found.group(4)))
    old = driver is not None and toolkit is not None and driver < toolkit
    return {'driver': driver, 'toolkit': toolkit, 'old_driver': old or 'insufficient CUDA driver version' in text}


def version_text(v):
    return f"{v[0]}.{v[1]}" if v else "?"
