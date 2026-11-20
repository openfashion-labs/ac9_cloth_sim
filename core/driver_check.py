# SPDX-License-Identifier: GPL-3.0-or-later
"""Before a run: is the NVIDIA driver new enough for the bundled solver? Read from the driver itself (nvcuda.dll's
cuDriverGetVersion), without importing Warp into Blender.

The bundled Warp is built with CUDA 12.9. A driver older than that makes Warp build the GPU programs itself and
fails on some GPUs; a person used to wait for minutes of building before the run said so. This says it at the
press, in one line, and does not stop the run. No Blender import: tested on its own (tests/test_driver_check.py)."""
import ctypes
import sys

# the CUDA toolkit the bundled warp-lang is built with: wp.get_cuda_toolkit_version() of the bundled wheel
# (tests/check_gpu_kernel_budget.py fails when they differ, so a new wheel updates this too)
BUNDLED_CUDA = (12, 9)
_CACHE = {}


def driver_cuda():
    """The CUDA version the installed NVIDIA driver supports, as (major, minor); None when there is no NVIDIA driver
    (or it cannot be asked). Read once per Blender session."""
    if 'v' in _CACHE:
        return _CACHE['v']
    v = None
    try:
        lib = ctypes.WinDLL('nvcuda.dll') if sys.platform == 'win32' else ctypes.CDLL('libcuda.so.1')
        n = ctypes.c_int(0)
        if lib.cuDriverGetVersion(ctypes.byref(n)) == 0 and n.value > 0:
            v = (n.value // 1000, (n.value % 1000) // 10)
    except (OSError, AttributeError):
        v = None
    _CACHE['v'] = v
    return v


NO_DRIVER = ("No NVIDIA driver was found: the simulation needs an NVIDIA GPU and its driver. "
             "Install the NVIDIA driver")
OLD_DRIVER = ("The NVIDIA driver is older than this add-on needs (CUDA {0}, needs {1} or newer): the run may fail. "
              "Update the NVIDIA driver with the NVIDIA App")


def warning(version=None, bundled=BUNDLED_CUDA):
    """(message, args) for the person when the driver is missing or older than the bundled CUDA, else None; the
    message is translated by the caller (tx). `version`: the driver's (major, minor) for tests, read when omitted."""
    v = driver_cuda() if version is None else version
    if v is None:
        return NO_DRIVER, ()
    if tuple(v) < tuple(bundled):
        return OLD_DRIVER, (f"{v[0]}.{v[1]}", f"{bundled[0]}.{bundled[1]}")
    return None
