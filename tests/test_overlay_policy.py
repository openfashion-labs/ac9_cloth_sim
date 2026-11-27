# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch missing controls, stale generated names and reintroduced view-only menus."""
import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = load(ROOT/'core/overlay_policy.py', 'overlay_policy_test')
G = load(ROOT/'tests/generate_overlay_reference.py', 'overlay_generator_test')


class DisplayPolicy(unittest.TestCase):
    def test_user_terms_match_the_sidebar_and_do_not_translate_tool(self):
        ja=G.load(ROOT/'i18n_ja.py','overlay_terms_ja')
        names={**ja.JA,**ja.JA_CTX}
        self.assertEqual(P.TITLE,'Overlays')
        self.assertEqual(P.MASTER,P.TITLE)
        self.assertEqual(P.destination('show_overlay'),'3D')
        self.assertIsNone(P.BY_KEY['show_pattern_seams_always']['parent'])
        self.assertEqual(names[P.label('show_silhouette_avatar')],names['Avatar']+'の'+names['Silhouette'])
        self.assertEqual(names[P.label('show_silhouette_collisions')],names['Collision']+'の'+names['Silhouette'])
        self.assertEqual(names['Tool'],'ツール')
        bad=[k for k,v in names.items() if '道具' in v]
        self.assertEqual(bad,[])
        for file in (ROOT/'docs/ja').rglob('*.md'):
            self.assertNotIn('道具',file.read_text(encoding='utf-8'),str(file))
    def test_every_stored_control_and_action_is_classified_once(self):
        self.assertEqual(len(P.ENTRIES), len(P.BY_KEY))
        old = {'show_overlay', 'show_pattern_seams_always', 'show_lengths', 'show_notches',
               'show_piece_colors', 'show_fabric_colors', 'show_names', 'show_internal_3d',
               'diagnosis_display', 'show_guides', 'show', 'show_body', 'show_silhouette_avatar',
               'show_silhouette_collisions', 'silhouette_position', 'show_thickness', 'thickness_side'}
        self.assertEqual(set(P.BY_KEY), old)
        self.assertEqual(len(P.GROUPS), 6)
        for e in P.ENTRIES:
            self.assertIn(e['group'], dict(P.GROUPS))
            if e['parent']:
                self.assertIn(e['parent'], P.BY_KEY)

    def test_diagnosis_destination_is_mode_specific(self):
        self.assertEqual([P.destination('diagnosis_display', m) for m in ('NONE','PROBLEMS','STRETCH','SIDE')],
                         ['', 'Pattern & 3D', '3D', '3D'])

    def test_generated_modes_use_the_same_destination_definition(self):
        with patch.dict(P.DIAGNOSIS_DOMAINS, {'STRETCH':'BOTH'}):
            self.assertEqual(P.destination('diagnosis_display','STRETCH'),'Pattern & 3D')
            self.assertIn('STRETCH: Pattern & 3D',P.reference())
        self.assertEqual(P.group_label('diagnosis'),P.label('diagnosis_display'))

    def test_documented_ui_names_and_other_diagnosis_entrance(self):
        ja=G.load(ROOT/'i18n_ja.py','overlay_docs_ja')
        for key,paths in P.DOC_NAMES.items():
            name=P.TITLE if key=='title' else P.label(key)
            for path in paths:
                text=(ROOT/path).read_text(encoding='utf-8')
                expected=ja.JA_CTX.get(name,ja.JA.get(name,name)) if '/ja/' in path else name
                self.assertIn(expected,text,path)
                if key=='title':
                    self.assertNotIn('AC9 Display Settings',text,path)
                    self.assertNotIn('AC9の表示設定',text,path)
                if key=='diagnosis_display':
                    for old in ('「表示」で', '診断パネルの「表示」', '**Display** selects', "panel's Display setting"):
                        self.assertNotIn(old,text,path)
        tree=ast.parse((ROOT/'diagnosis.py').read_text(encoding='utf-8'))
        bindings=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                  and n.func.attr in {'prop','prop_enum'} and len(n.args)>1
                  and isinstance(n.args[1],ast.Constant) and n.args[1].value=='diagnosis_display']
        self.assertEqual(len(bindings),4)
        self.assertEqual({n.args[2].value for n in bindings},{'NONE','PROBLEMS','STRETCH','SIDE'})
        self.assertTrue(all(n.func.attr=='prop_enum' for n in bindings))
        headings=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                  and n.func.attr=='label' and any(k.arg=='text' and ast.unparse(k.value)=='tx(DISPLAY_NAME)'
                                                  for k in n.keywords)]
        self.assertEqual(len(headings),1)

    def test_shared_measurement_and_thickness_scopes_are_not_misclassified(self):
        self.assertEqual(P.destination('show'), 'Pattern & 3D')
        self.assertEqual(P.destination('show_body'), '3D')
        self.assertEqual(P.destination('show_notches'), 'Pattern')
        self.assertEqual(P.BY_KEY['show_thickness']['group'], 'garment')

    def test_generated_reference_and_translations_match(self):
        self.assertEqual((ROOT/'docs/overlay_display.md').read_text(encoding='utf-8'), G.reference())
        ja = G.load(ROOT/'i18n_ja.py', 'overlay_ja_test')
        for e in P.ENTRIES:
            for text in (e['label'], e['note']):
                self.assertTrue(text in {**ja.JA, **ja.JA_CTX}, text)

    def test_every_name_binding_uses_the_same_definition(self):
        for file, clsname, owner in [('properties.py','AC9ClothSimProps','cloth'),
                                     ('measure_tool.py','AC9MeasureProps','measure')]:
            t = ast.parse((ROOT/file).read_text(encoding='utf-8'))
            cls = next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name == clsname)
            definitions = {n.target.id: n.annotation for n in cls.body if isinstance(n,ast.AnnAssign)}
            for e in P.ENTRIES:
                if e['owner'] != owner or e['kind']=='OPERATOR' or e['key']=='diagnosis_display':
                    continue
                name = next(k.value for k in definitions[e['key']].keywords if k.arg=='name')
                self.assertEqual(ast.unparse(name), f"OP.label({e['key']!r})")

    def test_recorded_ui_entrances_exist(self):
        entrances = set(P.MASTER_ENTRANCES)
        for e in P.ENTRIES:
            entrances.update(e['entrances'])
        for entrance in entrances:
            module, name = entrance.split('.')
            tree = ast.parse((ROOT/(module+'.py')).read_text(encoding='utf-8'))
            self.assertTrue(any(getattr(n,'name',None)==name for n in tree.body), entrance)

    def test_sidebar_uses_shared_presenter_and_menu_never_filters_by_open_view(self):
        measure = ast.parse((ROOT/'measure_tool.py').read_text(encoding='utf-8'))
        cls = next(n for n in measure.body if isinstance(n,ast.ClassDef) and n.name=='AC9_PT_SimMeasure')
        self.assertIn('draw_measure_visibility(layout, context)', ast.unparse(cls))
        display = ast.parse((ROOT/'display_settings.py').read_text(encoding='utf-8'))
        fn = next(n for n in display.body if isinstance(n,ast.FunctionDef) and n.name=='draw')
        # View checks are only allowed to explain where the position operation runs.
        checks = [n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                  and n.func.attr=='pattern_view']
        self.assertEqual(len(checks),1)
        self.assertIn('row.enabled = False', ast.unparse(fn))


if __name__ == '__main__':
    unittest.main()
