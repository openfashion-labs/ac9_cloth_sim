# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu triggers and behavior-bearing arguments must map to executable tests or an explicit scope contract."""
import sys,json,unittest,importlib.util,subprocess,tempfile,io
from pathlib import Path
ARGS=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,ARGS[0] if ARGS else str(ROOT.parent));sys.path.insert(0,str(ROOT/'tests'))
from ac9_cloth_sim import draft_tool as T
from ac9_cloth_sim.core import draft as D,iline as IL,dart as DA
from symmetry_fixture import fixture
load=importlib.util.spec_from_file_location('menu_checker',ROOT/'tests/context_contract_check.py')
C=importlib.util.module_from_spec(load);load.loader.exec_module(C)
CONTRACTS=json.loads((ROOT/'tests/context_contracts.json').read_text(encoding='utf-8'))


def key(tool,hit,row):
    props={k:v for k,v in row[3].items() if k not in ('piece','line','corner','vertex','thing','items')}
    return json.dumps([tool,hit[0],row[1],row[2],props],sort_keys=True)


def inventory():
    found={}
    for closed in (False,True):
     for smooth in (False,True):
      for hole in ((False,True) if closed else (False,)):
        sp,n,l,_=fixture('self',closed=closed)
        if smooth:
            IL.set_smooth(sp,n,l,1,True);D.set_smooth(sp,n,1,True)
        if hole:D.set_hole(sp,n,l,True)
        hits=[('piece',n),('edge',n,'E2',.3),('corner',n,1),('handle',n,'E2',0),
              ('iline',n,l,.3),('inode',n,l,1),('ihandle',n,l,0,0),('iend',n,l,'start'),('notch',n,'N1')]
        for tool in ('EDIT_PATTERN','TRANSFORM_PATTERN','DRAW'):
         for hit in hits:
          for row in T.menu_items(sp,hit,tool):
            if row is not None:found[key(tool,hit,row)]=row[1]
    sp,n,l,_=fixture('self')
    for p in sp['pieces']:p['internals']=[]
    DA.add_dart(sp,n,'E2',.3,width=20,length=40)
    dt=DA.find_darts(sp,n)[0]
    for hit in [('edge',n,dt['leg_edges'][0],.2),('corner',n,dt['apex'])]:
      for row in T.menu_items(sp,hit,'EDIT_PATTERN'):
        if row is not None:found[key('EDIT_PATTERN',hit,row)]=row[1]
    return found


class ContextContracts(unittest.TestCase):
    def test_all_declarations_including_unreached_branches_have_contracts(self):
        self.assertEqual(C.check((ROOT/'draft_tool.py').read_text(encoding='utf-8'),CONTRACTS),{'unreviewed':[],'stale':[]})

    def test_runtime_triggers_and_arguments_have_executable_contracts(self):
        actual=inventory();self.assertEqual(set(actual),set(CONTRACTS['runtime']))
        for k,contract in CONTRACTS['runtime'].items():
            self.assertTrue(contract['tests'] or contract.get('outside_symmetry'),k)
            for test in contract['tests']:
                file,_,method=test.partition(':')
                source=(ROOT/'tests'/file).read_text(encoding='utf-8')
                self.assertIn('def '+method+'(',source,test)

    def test_new_row_hidden_in_unreached_branch_blocks_release(self):
        source=(ROOT/'draft_tool.py').read_text(encoding='utf-8')
        source=source.replace('def menu_items(spec, hit, tool=None):',
            'def menu_items(spec, hit, tool=None):\n    if tool == "NEW_UNREACHED_TOOL":\n        return [("New command", "ac9_cloth_sim.unreviewed", "", {"sew": True})]')
        self.assertTrue(C.check(source,CONTRACTS)['unreviewed'])
        # Run the same fail-closed checker used before packaging, on the injected source.
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'draft_tool.py';p.write_text(source,encoding='utf-8')
            import bpy
            python=Path(sys.prefix)/'bin'/('python.exe' if sys.platform=='win32' else 'python3')
            if not python.exists():python=Path(sys.prefix)/'bin'/f'python{sys.version_info.major}.{sys.version_info.minor}'
            self.assertTrue(python.exists(),str(python))
            result=subprocess.run([str(python),str(ROOT/'tests/context_contract_check.py'),str(p)],capture_output=True)
            self.assertEqual(result.returncode,1,result.stderr.decode(errors='replace'))

    def test_constant_operator_and_new_hit_table_also_block_release(self):
        original=(ROOT/'draft_tool.py').read_text(encoding='utf-8')
        variants=[
            'NEW_MENU_OP = "ac9_cloth_sim.unreviewed"\n'+original.replace('def menu_items(spec, hit, tool=None):',
                'def menu_items(spec, hit, tool=None):\n    if hit[0] == "never_seen":\n        return [("New", NEW_MENU_OP, "", {})]'),
            original+'\n_MENU_ITEMS["never_seen"] = [("New", "ac9_cloth_sim.pattern_edit", "REMOVE")]\n',
            original+'\n_MENU_ITEMS.update({"new_hit": [("New command", "ac9_cloth_sim.future_command", "")]})\n',
            original+'\nalias = _MENU_ITEMS\nalias["edge"].append(("New", "ac9_cloth_sim.future_command", ""))\n',
            original+'\n_MENU_ITEMS["edge"].extend([("New", "ac9_cloth_sim.future_command", "")])\n',
            original.replace("'inode': [(\"Delete Point\"", "'new_hit': [(\"New\", \"ac9_cloth_sim.pattern_edit\", 'REMOVE')],\n    'inode': [(\"Delete Point\"")]
        for source in variants:
          with self.subTest(variant=variants.index(source)):
            self.assertNotEqual(source,original)
            self.assertTrue(C.check(source,CONTRACTS)['unreviewed'])
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'draft_tool.py';p.write_text(source,encoding='utf-8')
                python=Path(sys.prefix)/'bin'/('python.exe' if sys.platform=='win32' else f'python{sys.version_info.major}.{sys.version_info.minor}')
                result=subprocess.run([str(python),str(ROOT/'tests/context_contract_check.py'),str(p)],capture_output=True)
                self.assertEqual(result.returncode,1,result.stderr.decode(errors='replace'))


if __name__=='__main__':
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ContextContracts))
    if len(ARGS)>1:Path(ARGS[1]).write_text(json.dumps({'ok':r.wasSuccessful(),'tests':r.testsRun,'runtime':len(inventory()),'log':stream.getvalue()},indent=2),encoding='utf-8')
    sys.exit(0 if r.wasSuccessful() else 1)
