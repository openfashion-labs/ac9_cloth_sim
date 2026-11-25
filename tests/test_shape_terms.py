"""The two reset stages must never be swapped during a display-only rename."""
import ast
from pathlib import Path
import re
import runpy
import unittest

ROOT=Path(__file__).resolve().parents[1]
NAMES={'ac9_cloth_sim.reset':('Back to Adjusted Shape','調整した形に戻す'),
       'ac9_cloth_sim.reset_placed':('Back to Placed Shape','配置した形に戻す')}

class ShapeTerms(unittest.TestCase):
    def test_operator_ids_keep_the_correct_stage(self):
        got={}
        for f in ('dress.py','placement.py'):
            for cls in ast.walk(ast.parse((ROOT/f).read_text('utf-8'))):
                if not isinstance(cls,ast.ClassDef):continue
                fields={n.targets[0].id:n.value.value for n in cls.body if isinstance(n,ast.Assign) and
                        isinstance(n.targets[0],ast.Name) and isinstance(n.value,ast.Constant)}
                if fields.get('bl_idname') in NAMES:got[fields['bl_idname']]=fields['bl_label']
        self.assertEqual(got,{k:v[0] for k,v in NAMES.items()})

    def test_both_japanese_translations_match_the_stage(self):
        ja=runpy.run_path(str(ROOT/'i18n_ja.py'))['JA']
        for en,jp in NAMES.values():self.assertEqual(ja[en],jp)

    def test_old_terms_not_used_in_current_ui_or_manuals(self):
        bad=[]
        paths=[*ROOT.glob('*.py'),*ROOT.glob('README*.md'),*(ROOT/'docs/ja').rglob('*.md'),
               *(ROOT/'docs/en').rglob('*.md'),*(ROOT/'LLM').glob('*')]
        old=re.compile(r'置いた形|ガイドに戻(?:す|します)|Back to Guide\b|Back to Placed\b(?! Shape)')
        for p in paths:
            if p.suffix not in ('.py','.md','.toml'):continue
            for i,line in enumerate(p.read_text('utf-8').splitlines(),1):
                if old.search(line):bad.append(f'{p.relative_to(ROOT)}:{i}')
            if p.suffix=='.py':
                for n in ast.walk(ast.parse(p.read_text('utf-8'))):
                    if isinstance(n,ast.Constant) and isinstance(n.value,str) and old.search(n.value):
                        bad.append(f'{p.relative_to(ROOT)}:{n.lineno}: joined string')
        self.assertEqual(bad,[])

    def test_both_concepts_defined_in_both_languages(self):
        for lang,names in [('ja',('配置した形','調整した形')),('en',('Placed Shape','Adjusted Shape'))]:
            for f in ('concepts.md','glossary.md'):
                text=(ROOT/f'docs/{lang}/{f}').read_text('utf-8')
                for name in names:self.assertIn(name,text)

if __name__=='__main__':unittest.main()
