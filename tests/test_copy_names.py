"""Duplicate and Mirror Copy give the copy a name shown apart from the original's, numbered as Blender numbers
copies (Front -> Front.001 -> Front.002). Before: a piece with a label (the name shown on screen) gave its copy the
same label, so the copy and the original read alike."""
import sys
import unittest

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import draft as D


def spec_with(label=None):
    q = [[0, 0], [300, 0], [300, 280], [0, 280]]
    pc = {'name': 'a', 'edges': [{'id': f'a_e{i}', 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}
    if label:
        pc['label'] = label
    return {'version': 1, 'name': 't', 'seams': [], 'pieces': [pc]}


def shown(spec):
    return [p.get('label') or p['name'] for p in spec['pieces']]


class CopyNames(unittest.TestCase):
    def test_duplicate_labeled(self):
        spec = spec_with('Front')
        D.duplicate_group(spec, ['a'])
        D.duplicate_group(spec, ['a'])
        self.assertEqual(shown(spec), ['Front', 'Front.001', 'Front.002'])
        self.assertEqual(len(set(p['name'] for p in spec['pieces'])), 3)

    def test_duplicate_of_a_copy(self):
        spec = spec_with('Front')
        made, _ = D.duplicate_group(spec, ['a'])
        D.duplicate_group(spec, [made['a']])
        self.assertEqual(shown(spec), ['Front', 'Front.001', 'Front.002'])

    def test_duplicate_unlabeled(self):
        spec = spec_with()
        D.duplicate_group(spec, ['a'])
        self.assertEqual(shown(spec), ['a', 'a.001'])

    def test_mirror_labeled(self):
        for plane in ('x', 'y'):
            spec = spec_with('Front')
            D.mirror_copy(spec, 'a', plane=plane)
            self.assertEqual(shown(spec), ['Front', 'Front.001'])

    def test_mirror_unlabeled(self):
        spec = spec_with()
        D.mirror_copy(spec, 'a')
        self.assertEqual(shown(spec), ['a', 'a_R'])


if __name__ == '__main__':
    unittest.main()
