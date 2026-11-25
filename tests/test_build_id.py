"""Which build wrote a run (core/build_id.py): the released ZIP's BUILD_INFO.txt, else a checkout's HEAD."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import build_id as BID

SHA = '0123456789abcdef0123456789abcdef01234567'


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8').write(text)


class BuildId(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _write(os.path.join(self.root, 'blender_manifest.toml'), 'schema_version = "1.0.0"\nversion = "9.8.7"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_release_build_info(self):
        _write(os.path.join(self.root, 'BUILD_INFO.txt'), f"AC9 Cloth Sim 9.8.7\ncommit {SHA} (main)\nbuilt 2026\n")
        self.assertEqual(BID.info(self.root), {'version': '9.8.7', 'commit': SHA, 'source': 'release'})
        self.assertEqual(BID.line(self.root), 'add-on 9.8.7 (0123456)')

    def test_release_build_info_without_its_version_line(self):
        # the build made like the release workflow writes the commit and the time only
        _write(os.path.join(self.root, 'BUILD_INFO.txt'), f"commit {SHA}\nbuilt (local) 2026\n")
        self.assertEqual(BID.info(self.root), {'version': '9.8.7', 'commit': SHA, 'source': 'release'})

    def test_checkout_branch(self):
        _write(os.path.join(self.root, '.git', 'HEAD'), 'ref: refs/heads/master\n')
        _write(os.path.join(self.root, '.git', 'refs', 'heads', 'master'), SHA + '\n')
        self.assertEqual(BID.info(self.root)['commit'], SHA)
        self.assertEqual(BID.line(self.root), 'add-on 9.8.7 (checkout 0123456)')

    def test_checkout_packed_ref(self):
        _write(os.path.join(self.root, '.git', 'HEAD'), 'ref: refs/heads/master\n')
        _write(os.path.join(self.root, '.git', 'packed-refs'), f"# pack-refs\n{SHA} refs/heads/master\n")
        self.assertEqual(BID.info(self.root)['commit'], SHA)

    def test_worktree(self):
        common = os.path.join(self.root, 'repo.git')
        wt = os.path.join(common, 'worktrees', 'one')
        _write(os.path.join(self.root, '.git'), f"gitdir: {wt}\n")
        _write(os.path.join(wt, 'HEAD'), 'ref: refs/heads/topic\n')
        _write(os.path.join(wt, 'commondir'), '../..\n')
        _write(os.path.join(common, 'refs', 'heads', 'topic'), SHA + '\n')
        self.assertEqual(BID.info(self.root)['commit'], SHA)

    def test_nothing_says_it(self):
        self.assertEqual(BID.info(self.root), {'version': '9.8.7', 'commit': None, 'source': 'unknown'})
        self.assertEqual(BID.line(self.root), 'add-on 9.8.7 (build unknown)')


if __name__ == '__main__':
    unittest.main()
