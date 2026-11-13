# SPDX-License-Identifier: GPL-3.0-or-later
"""Whether a starting run is building the solver's compute programs (warp's kernel cache), read from Blender's side
without importing warp: the modules warp wrote into its cache since the press.

A first run on a PC, and the first after an update of the add-on, builds every kernel module for this GPU; later
runs load them from the cache. On the development PC a first run waited 84-107 s for its first frame, a later one
15-21 s (2026-11-13), and the screen showed one unchanging step all that time. Counting the new module folders tells
the person what is happening and moves the bar."""
import os
import sys

# Kernel modules a Live run builds on an empty cache (22 on 2026-11-13). Only the bar's scale: a different count
# leaves the bar short of or held at its end, never wrong in what it says.
EXPECTED_MODULES = 22


def cache_root(environ=None):
    """warp's kernel cache folder (the solver process inherits Blender's environment): WARP_CACHE_PATH, else warp's
    default per-user cache. Version subfolders are below it."""
    env = os.environ if environ is None else environ
    path = env.get('WARP_CACHE_PATH')
    if path:
        return path
    if sys.platform == 'win32':
        base = env.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        return os.path.join(base, 'NVIDIA', 'warp', 'Cache')
    if sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'Library', 'Caches', 'warp')
    return os.path.join(env.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache'), 'warp')


def built_since(since, root=None):
    """The kernel modules written into the cache at or after `since` (a time.time()): warp makes one 'wp_<module>_
    <hash>' folder per module it builds. 0 when nothing was built or the cache cannot be read."""
    root = cache_root() if root is None else root
    n = 0
    try:
        versions = [e for e in os.scandir(root) if e.is_dir()]
    except OSError:
        return 0
    for v in versions:
        try:
            # only the version folders touched since: an old version's thousands of entries are not walked
            if v.stat().st_mtime < since:
                continue
            for e in os.scandir(v.path):
                if e.name.startswith('wp_') and e.is_dir() and e.stat().st_mtime >= since:
                    n += 1
        except OSError:
            continue
    return n
