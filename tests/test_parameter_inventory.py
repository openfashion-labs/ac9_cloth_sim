# SPDX-License-Identifier: GPL-3.0-or-later
"""The maintained window inventory must agree with the operators actually connected to the shared UI."""
import ast
from pathlib import Path
import unittest
from ac9_cloth_sim.core import tool_policy as TP

class ParameterInventory(unittest.TestCase):
    def test_actual_modal_callers_match_the_maintained_inventory(self):
        root=Path(__file__).resolve().parents[1];actual=set()
        for path in root.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            for cls in (n for n in tree.body if isinstance(n,ast.ClassDef)):
                ids=[n.value.value for n in cls.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant)
                     and any(isinstance(t,ast.Name) and t.id=='bl_idname' for t in n.targets)]
                if not ids:continue
                calls=[n for n in ast.walk(cls) if isinstance(n,ast.Call)]
                names={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else '' for n in calls}
                if 'parameter_open' in names:actual.add(ids[0])
                self.assertFalse({'ghost_open','invoke_props_dialog'}<=names,ids[0]+' still has a transient preview dialog')
        self.assertEqual(actual,set(TP.PARAMETER_OPERATORS))
        self.assertTrue(set(TP.PARAMETER_OPERATORS.values())<=set(TP.PARAMETER_FIELDS))

if __name__=='__main__':unittest.main()
