import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from ac9_cloth_sim.solver import prepared_live as P

class Metadata(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def test_manifest_does_not_read_record_and_still_detects_metadata_edits(self):
        metadata=self.root/'package.dist-info';metadata.mkdir()
        file=metadata/'METADATA';file.write_bytes(b'Name: package\nVersion: 1\n')
        class Distribution:
            _path=metadata;version='1'
            @property
            def files(self):raise AssertionError('RECORD must not be read')
        with patch.object(P.importlib.metadata,'distribution',return_value=Distribution()):
            manifest=P.dependency_manifest()
        self.assertEqual(manifest['numpy']['path'],str(file.resolve()))
        with patch.object(P,'code_id',return_value='code'):
            before=P.source_id(manifest)
            file.write_bytes(b'Name: changed\nVersion: 1\n')
            with self.assertRaisesRegex(ValueError,'metadata changed'):P.source_id(manifest)

    def test_owned_canonical_interpreter_avoids_reopening_but_aliases_are_resolved(self):
        canonical=str(Path('owned-python.exe').absolute())
        with patch.object(Path,'resolve',side_effect=AssertionError('must not reopen canonical file')):
            self.assertTrue(P.same_interpreter(canonical,canonical))
        with patch.object(Path,'resolve',return_value=Path(canonical)) as resolve:
            self.assertTrue(P.same_interpreter('alias-python.exe',canonical))
        resolve.assert_called_once()
        with patch.object(Path,'resolve',return_value=Path('different-python.exe').absolute()):
            self.assertFalse(P.same_interpreter('alias-python.exe',canonical))
