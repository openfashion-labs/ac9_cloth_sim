"""Saved handle membership distinguishes an ID from an ID collection."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import piece_table as PT


class HandleNames(unittest.TestCase):
    def test_explicit_comma_unicode_and_order(self):
        names = ['A,B', '袖,右', 'C', 'A,B']
        props = {PT.HANDLE_IDS: PT.encode_handle_names(names), PT.HANDLE_NAMES: ','.join(names), 'ac9_piece': 'A,B'}
        self.assertEqual(PT.handle_names(props), ('A,B', '袖,右', 'C'))

    def test_explicit_array_takes_precedence(self):
        self.assertEqual(PT.handle_names({PT.HANDLE_IDS: '["B"]', PT.HANDLE_NAMES: 'A,C'}), ('B',))

    def test_empty_explicit_collection_stays_empty(self):
        self.assertEqual(PT.handle_names({PT.HANDLE_IDS: '[]', PT.HANDLE_NAMES: 'A'}), ())

    def test_corrupt_explicit_value_does_not_select_legacy_members(self):
        for value in ('bad json', '{}', '[1]', '[null]', '[""]', 3):
            with self.subTest(value=value):
                self.assertEqual(PT.handle_names({PT.HANDLE_IDS: value, PT.HANDLE_NAMES: 'A,B'}), ())

    def test_legacy_first_id_is_known_exactly(self):
        self.assertEqual(PT.handle_names({PT.HANDLE_NAMES:'A,B', 'ac9_piece':'A,B'}), ('A,B',))
        self.assertEqual(PT.handle_names({PT.HANDLE_NAMES:'A,B,C', 'ac9_piece':'A,B'}), ('A,B', 'C'))

    def test_legacy_delimiter_behavior_remains_for_untyped_suffix(self):
        self.assertEqual(PT.handle_names({PT.HANDLE_NAMES:'A,B,C', 'ac9_piece':'A'}), ('A', 'B', 'C'))
        self.assertEqual(PT.handle_names({PT.HANDLE_NAMES:'A,B'}), ('A','B'))

    def test_no_members(self):
        self.assertEqual(PT.handle_names({}), ())


if __name__=='__main__':
    unittest.main()
