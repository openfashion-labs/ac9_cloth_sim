"""A drag edits a fresh copy of the pattern on every redraw: the original's placement must not move (no Blender).
Before the fix, 50 redraws of a 500 mm move put the anchor 12750 mm off."""
import importlib.util
import json
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = '_ac9_edit_copy_placement_test'
definition = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(definition)
sys.modules[PACKAGE] = package
definition.loader.exec_module(package)
D = __import__(PACKAGE + '.core.draft', fromlist=['draft'])


class EditCopyPlacement(unittest.TestCase):
    def test_redrawn_move_keeps_the_original(self):
        spec = {"version": 1, "name": "T", "units": "mm", "pieces": [], "seams": [], "placement": {}}
        a = D.add_polygon(spec, D.rect_points(np.array([0., 0.]), np.array([400., 600.])), "Rectangle")
        spec['placement'][a] = {'guide': 'torso', 'theta': 0.0, 'h': 0.0, 'anchor': [200.0, 300.0]}
        before = json.dumps(spec['placement'])
        for i in range(1, 51):
            sp = D.edit_copy(spec, [a])
            D.transform_group(sp, [a], shift=(10.0 * i, 0.0))
        self.assertEqual(json.dumps(spec['placement']), before)
        np.testing.assert_allclose(sp['placement'][a]['anchor'], [700.0, 300.0])


if __name__ == '__main__':
    unittest.main()
