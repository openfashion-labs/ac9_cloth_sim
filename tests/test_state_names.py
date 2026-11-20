# SPDX-License-Identifier: GPL-3.0-or-later
"""The piece states' screen names come from one table (core/spec.py STATE_NAMES) and every display agrees with it.

The Tool tab's state buttons, the right-click menu's state rows, the operator's enum, the Japanese translation and the
manuals are read here and compared with the table; the names the states had before (two spellings of each) are found
nowhere in the code, the translation, docs/ or LLM/. (A rename that changes one place only fails here.)

Run: blender -b --factory-startup --python tests/test_state_names.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import re
import sys
import types
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import ui as UI, operators as OP, draft_tool as DT, i18n_ja as JA  # noqa: E402
from ac9_cloth_sim.core import spec as S                     # noqa: E402

ROOT = Path(ac9_cloth_sim.__file__).resolve().parent
OLD = ("Off (All)", "Off (Pattern)", "Off All", "Off Pattern", "型紙から外す", "全部オフ", "オフ（全部）", "オフ（型紙）")
JA_NAMES = {'active': "アクティブ", 'freeze': "フリーズ", 'off_pattern': "非アクティブ", 'off_all': "オフ"}


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class _Layout:
    """Records the operator buttons drawn (text, state)."""
    def __init__(self, out):
        self.out = out
        self.enabled = True

    def label(self, **_k):
        pass

    def grid_flow(self, **_k):
        return self

    def row(self, **_k):
        return self

    def operator(self, _idname, text="", **_k):
        op = types.SimpleNamespace()
        self.out.append((text, op))
        return op


class StateNames(unittest.TestCase):
    def test_table_covers_every_state(self):
        self.assertEqual(set(S.STATE_NAMES), set(S.PIECE_STATES))
        self.assertEqual([S.state_name(k) for k in S.PIECE_STATES], ["Active", "Freeze", "Inactive", "Off"])

    def test_operator_enum_reads_the_table(self):
        self.assertEqual([(k, n, d) for k, n, d in OP.STATE_ITEMS],
                         [(k, *S.STATE_NAMES[k]) for k in S.PIECE_STATES])

    def test_tool_tab_buttons_read_the_table(self):
        out = []
        UI.draw_selected_states(_Layout(out), current='active', title=None)
        self.assertEqual([(t, op.state) for t, op in out], [(S.state_name(k), k) for k in S.PIECE_STATES])

    def test_right_click_states_by_the_table(self):
        # the right-click menu of a placed or a dressed piece opens the states' submenu: all four, by the table, the
        # current one marked
        spec = {'pieces': [{'name': 'A'}]}
        for kind in ('placed', 'garment'):
            rows = [r for r in DT.menu_items(spec, (kind, 'A')) if r is not None]
            self.assertIn(("State", DT.STATE_MENU), [(r[0], r[1]) for r in rows], kind)
        for cur in S.PIECE_STATES:
            pc = spec['pieces'][0]
            pc.clear()
            pc['name'] = 'A'
            S.set_piece_state(pc, cur)
            self.assertEqual(DT.state_rows(spec, 'A'), [(S.state_name(k), k, k == cur) for k in S.PIECE_STATES])

    def test_japanese_names_and_tooltips(self):
        for k in S.PIECE_STATES:
            name, tip = S.STATE_NAMES[k]
            self.assertEqual(_ja(name), JA_NAMES[k])
            self.assertTrue(_ja(tip), tip)

    def test_old_names_are_gone(self):
        hits = []
        files = [p for p in ROOT.rglob('*') if p.suffix in ('.py', '.md') and p.name != 'CHANGELOG.md'
                 and '__pycache__' not in p.parts and p.name != Path(__file__).name]
        for p in files:
            s = p.read_text('utf-8', errors='replace')
            hits += [f"{p.relative_to(ROOT)}: {o}" for o in OLD if o in s]
        self.assertEqual(hits, [])

    def test_manuals_name_each_state_as_the_table(self):
        # every 「日本語」（English） pair in docs/ja that names a state has the table's English and its translation
        bad = []
        for p in (ROOT / 'docs' / 'ja').rglob('*.md'):
            for ja, en in re.findall(r"「\*{0,2}([^」*]+)\*{0,2}」（([^）]+)）", p.read_text('utf-8')):
                for k, ja_name in JA_NAMES.items():
                    if ja == ja_name and en != S.state_name(k):
                        bad.append(f"{p.name}: 「{ja}」（{en}）")
        self.assertEqual(bad, [])


def _ja(key):
    for name in dir(JA):
        d = getattr(JA, name)
        if isinstance(d, dict) and key in d:
            return d[key]
    return None


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[(str(t[0]), t[1][-400:]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-400:]) for t in res.errors])), 'utf-8')
