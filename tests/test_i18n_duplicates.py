"""No English word is given twice in one translation table: Python keeps the last one silently, so an earlier
translation written for another place ("Direction" for a fold and for scaling) would never show."""
import ast
import collections
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'i18n_ja.py'


class NoDuplicateTranslations(unittest.TestCase):
    def test_each_table_names_a_word_once(self):
        tree = ast.parse(SRC.read_text(encoding='utf-8'))
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                keys = [ast.literal_eval(k) for k in node.value.keys if k is not None]
                found[node.targets[0].id] = [k for k, n in collections.Counter(keys).items() if n > 1]
        self.assertIn('JA', found)
        self.assertIn('JA_CTX', found)
        self.assertEqual({t: d for t, d in found.items() if d}, {},
                         'one English word, one translation per table: give the other place its own English')


if __name__ == '__main__':
    unittest.main()
