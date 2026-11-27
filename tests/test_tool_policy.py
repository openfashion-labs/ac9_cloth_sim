# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import unittest
from pathlib import Path
from ac9_cloth_sim.core import tool_policy as TP

ROOT = Path(__file__).resolve().parents[1]


class ToolPolicy(unittest.TestCase):
    def test_every_registered_tool_has_a_policy(self):
        # The actual registration functions enumerate classes, including shape factories.
        sources = {p.name: ast.parse(p.read_text(encoding='utf-8-sig')) for p in
                   [ROOT/'toolbar.py', ROOT/'draft_tool.py', ROOT/'seam_edit_tool.py', ROOT/'shape_tool.py']}
        ids, names = {}, {}
        for tree in sources.values():
            constants = {n.targets[0].id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                         and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
            for n in tree.body:
                if isinstance(n, ast.ClassDef):
                    for x in n.body:
                        if isinstance(x, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_idname' for t in x.targets):
                            v = x.value.value if isinstance(x.value, ast.Constant) else constants.get(getattr(x.value,'id',''))
                            if v: ids[n.name] = v
                elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func,'id','') == '_tool':
                    ids[n.targets[0].id] = n.value.args[0].value
        registered = set()
        for f in sources['toolbar.py'].body:
            if isinstance(f, ast.FunctionDef) and f.name in ('_pattern_tools','_always_tools'):
                for ret in ast.walk(f):
                    if isinstance(ret, ast.Return):
                        for row in ret.value.elts:
                            v = row.elts[0]; name = v.id if isinstance(v, ast.Name) else v.attr
                            registered.add(ids[name])
        self.assertEqual(set(TP.BY_ID), registered)

    def test_external_fallback_and_unclassified_addon(self):
        self.assertEqual(TP.policy('builtin.annotate')['domain'], 'external')
        with self.assertRaises(KeyError): TP.policy('ac9_cloth_sim.new_tool')

    def test_seam_reference_does_not_grant_editing(self):
        self.assertTrue(TP.pattern_seams('EDIT_PATTERN', True))
        self.assertFalse(TP.editable('EDIT_PATTERN', 'seam'))
        self.assertFalse(TP.pattern_seams('EDIT_PATTERN'))
        self.assertTrue(TP.pattern_seams('ADD_SEAM_MN'))

    def test_generated_reference_is_current(self):
        self.assertEqual((ROOT/'docs/tool_behavior.md').read_text(encoding='utf-8'), TP.reference())

    def test_tool_labels_match_the_actual_ui(self):
        from ac9_cloth_sim.i18n_ja import JA
        tree=ast.parse((ROOT/'toolbar.py').read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.Assign) and
                  any(isinstance(t,ast.Name) and t.id=='NAMES' for t in n.targets))
        names=ast.literal_eval(node.value)
        for row in TP.TOOLS:
            label=names[row['key']]
            self.assertEqual(row['name'],JA.get(label,label),row['key'])


if __name__ == '__main__': unittest.main()
