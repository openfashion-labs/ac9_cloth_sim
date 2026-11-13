"""A starting run says it builds the compute programs only for modules written into warp's cache since the press."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from core import kernel_cache as KC


class KernelCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def _module(self, version, name, mtime):
        d = self.root / version / name
        d.mkdir(parents=True)
        os.utime(d, (mtime, mtime))
        return d

    def test_cache_root(self):
        self.assertEqual(KC.cache_root({'WARP_CACHE_PATH': 'X:/cache'}), 'X:/cache')
        self.assertTrue(KC.cache_root({'LOCALAPPDATA': 'C:/L'}).replace('\\', '/').endswith('warp/Cache')
                        or 'warp' in KC.cache_root({}))

    def test_counts_only_new_modules(self):
        now = time.time()
        self._module('1.17.0', 'wp_old_1234567', now - 3600)
        self._module('1.17.0', 'wp_a_1111111', now + 1)
        self._module('1.17.0', 'wp_b_2222222', now + 2)
        (self.root / '1.17.0' / 'wp_c_3333333.tmp').write_text('')      # a file, not a module folder
        os.utime(self.root / '1.17.0', (now + 2, now + 2))
        self.assertEqual(KC.built_since(now, str(self.root)), 2)

    def test_untouched_version_is_skipped(self):
        now = time.time()
        self._module('1.16.0', 'wp_a_1111111', now + 1)
        os.utime(self.root / '1.16.0', (now - 60, now - 60))           # the folder itself not touched since
        self.assertEqual(KC.built_since(now, str(self.root)), 0)

    def test_missing_cache(self):
        self.assertEqual(KC.built_since(time.time(), str(self.root / 'absent')), 0)


if __name__ == '__main__':
    unittest.main()
