# SPDX-License-Identifier: GPL-3.0-or-later
"""File-system calls that wait on a slow disk without stopping Blender.

Every Python thread in Blender shares one interpreter lock with the main thread (the UI). Most file calls let go of
it while Windows waits on the disk, but os.path.realpath / Path.resolve and os.listdir did not: on a USB hard disk
busy renaming a folder, a background thread waiting in either one held the UI for as long as the disk took (up to
15 s, measured), while os.stat, os.scandir, open/read/write, os.replace, mkdir, remove and rmdir waited just as long
and left the UI free. Code that may touch the folder beside the .blend from a background thread uses these instead.
No Blender calls.
"""
import os
from pathlib import Path

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)      # a foreign call lets go of the interpreter lock
    _create = _k32.CreateFileW
    _create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                        wintypes.DWORD, wintypes.HANDLE)
    _create.restype = wintypes.HANDLE
    _final = _k32.GetFinalPathNameByHandleW
    _final.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    _final.restype = wintypes.DWORD
    _close = _k32.CloseHandle
    _close.argtypes = (wintypes.HANDLE,)
    _INVALID = wintypes.HANDLE(-1).value
    _BACKUP_SEMANTICS = 0x02000000                                # needed to open a folder

    def _final_path(path):
        """The final path of an existing file or folder (links followed), or None."""
        handle = _create(path, 0, 7, None, 3, _BACKUP_SEMANTICS, None)   # any access, share all, OPEN_EXISTING
        if handle in (None, _INVALID):
            return None
        try:
            size = 512
            while True:
                buffer = ctypes.create_unicode_buffer(size)
                n = _final(handle, buffer, size, 0)
                if n == 0:
                    return None
                if n < size:
                    break
                size = n + 1
        finally:
            _close(handle)
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'):
            return '\\\\' + value[8:]
        if value.startswith('\\\\?\\'):
            return value[4:]
        return value
else:
    def _final_path(path):
        try:
            return os.path.realpath(path, strict=True)
        except (OSError, ValueError):
            return None


def resolve(path):
    """Path(path).resolve() (non-strict: the part that does not exist yet is kept as written), letting other
    threads run while the disk is slow."""
    path = os.path.abspath(os.fspath(path))
    rest = []
    while True:
        found = _final_path(path)
        if found is not None:
            return Path(os.path.join(found, *reversed(rest)) if rest else found)
        parent, name = os.path.split(path)
        if not name or parent == path:
            return Path(os.path.join(path, *reversed(rest)))
        rest.append(name)
        path = parent


def names(folder):
    """os.listdir(folder), through os.scandir (which lets other threads run while the disk is slow)."""
    with os.scandir(folder) as entries:
        return [entry.name for entry in entries]
