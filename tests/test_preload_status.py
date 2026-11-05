"""Atomic preload messages must never turn missing/invalid output into completion."""
import json
from pathlib import Path
import tempfile
import unittest
from solver import preload_status as status


class PreloadStatusTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / 'preload.json')

    def tearDown(self):
        self.folder.cleanup()

    def test_actual_completion_sequence(self):
        for done, phase in enumerate(status.PHASES):
            status.write(self.path, done, phase)
            self.assertEqual(status.read(self.path)['completed'], done)
        status.write(self.path, status.TOTAL, 'ready', 'ready')
        self.assertEqual(status.read(self.path)['state'], 'ready')

    def test_absent_truncated_and_oversized_messages(self):
        self.assertIsNone(status.read(self.path))
        for value in ('{', '[]', 'null', 'x' * 5000):
            Path(self.path).write_text(value)
            self.assertIsNone(status.read(self.path))

    def test_ready_requires_complete_valid_message(self):
        status.write(self.path, 4, 'ready', 'ready')
        row = status.read(self.path)
        for key, value in [('completed', 3), ('completed', True), ('total', 99),
                           ('version', 2), ('phase', 'model'), ('state', 'unknown')]:
            with self.subTest(key=key, value=value):
                Path(self.path).write_text(json.dumps(dict(row, **{key: value})))
                self.assertIsNone(status.read(self.path))

    def test_failure_is_not_completion_and_does_not_leak_to_new_session(self):
        status.write(self.path, 3, 'kernels', 'failed', 'compile failed')
        row = status.read(self.path)
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['completed'], 3)
        self.assertIsNone(status.read(str(Path(self.folder.name) / 'another-session.json')))


if __name__ == '__main__':
    unittest.main()
