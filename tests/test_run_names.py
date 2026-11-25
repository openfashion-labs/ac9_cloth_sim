# SPDX-License-Identifier: GPL-3.0-or-later
"""The runs' screen names come from one table (core/spec.py RUN_NAMES) and every display agrees with it.

The Run panel's button, the operator's modes, the play key's name in the preferences, the Japanese translation and the
manuals are read here and compared with the table. Sentences that name the main run (a capitalized "Simulate" after
the first word) carry its Japanese name in the translation, and the name it had before (Live Sim) is found nowhere in
the code, the translation, docs/ or LLM/. (A rename that changes one place only fails here.)

Run: blender -b --factory-startup --python tests/test_run_names.py -- <parent of ac9_cloth_sim> [out.json]
"""
import json
import re
import sys
import types
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import ui as UI, dress as D, live_key as LK, i18n_ja as JA  # noqa: E402
from ac9_cloth_sim.core import spec as S                     # noqa: E402

ROOT = Path(ac9_cloth_sim.__file__).resolve().parent
OLD = re.compile(r"Live Sim|ライブ ?(?:シム|Sim)")
NAME, NAME_JA = "Simulate", "シミュレート"
KEY_JA = "シミュレート（開始・停止）"


def setUpModule():
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


class _Layout:
    """Records the operator buttons drawn (idname, text, op) and stands in for every other layout call."""
    def __init__(self, out):
        self.out = out

    def operator(self, idname, text="", **_k):
        op = types.SimpleNamespace()
        self.out.append((idname, text, op))
        return op

    def panel(self, *_a, **_k):
        return self, None

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


def _ja(key):
    for name in dir(JA):
        d = getattr(JA, name)
        if isinstance(d, dict) and key in d:
            return d[key]
    return None


def _entries():
    for name in dir(JA):
        d = getattr(JA, name)
        if isinstance(d, dict):
            yield from ((k, v) for k, v in d.items() if isinstance(k, str) and isinstance(v, str))


class RunNames(unittest.TestCase):
    def test_table(self):
        self.assertEqual(S.run_name('LIVE'), NAME)
        self.assertEqual(S.PLAY_KEY_NAME, NAME + " (Start / Stop)")

    def test_operator_modes_read_the_table(self):
        rna = bpy.ops.ac9_cloth_sim.dress.get_rna_type()
        items = {i.identifier: i.name for i in rna.properties['mode'].enum_items}
        self.assertEqual(items, {k: S.run_name(k) for k in ('START', 'CONTINUE', 'LIVE', 'LOCAL')})

    def test_run_panel_button_reads_the_table(self):
        out = []
        UI.draw_run(_Layout(out), bpy.context, status=False)
        live = [t for i, t, op in out if i == "ac9_cloth_sim.dress" and getattr(op, 'mode', None) == 'LIVE']
        self.assertEqual(live, [NAME])

    def test_play_key_reads_the_table(self):
        self.assertEqual(LK.LIVE_NAME, S.PLAY_KEY_NAME)
        self.assertEqual(LK.AC9_OT_LivePlayKey.bl_label, S.PLAY_KEY_NAME)

    def test_japanese_names(self):
        self.assertEqual(_ja(NAME), NAME_JA)
        self.assertEqual(_ja(S.PLAY_KEY_NAME), KEY_JA)

    def test_sentences_naming_the_run_say_it_in_japanese(self):
        # a capitalized Simulate after a sentence's first word is the button's name: its translation names it too
        bad = [k for k, v in _entries() if re.search(r"(?<=\S)[ (]Simulate\b", k) and NAME_JA not in v]
        self.assertEqual(bad, [])

    def test_old_name_is_gone(self):
        hits = []
        for p in ROOT.rglob('*'):
            if (p.suffix not in ('.py', '.md', '.toml', '.json', '.svg') or p.name == 'CHANGELOG.md'
                    or '__pycache__' in p.parts or '.git' in p.parts or p.name == Path(__file__).name):
                continue
            for i, ln in enumerate(p.read_text('utf-8', errors='replace').splitlines(), 1):
                if OLD.search(ln):
                    hits.append(f"{p.relative_to(ROOT)}:{i}")
        self.assertEqual(hits, [])

    def test_manuals_pair_the_names(self):
        # every 「日本語」（English） pair in docs/ja and LLM/ that names the run has the table's two names
        bad = []
        for p in list((ROOT / 'docs' / 'ja').rglob('*.md')) + list((ROOT / 'LLM').rglob('*.md')) + [ROOT / 'README.md']:
            for ja, en in re.findall(r"「\*{0,2}([^」*]+)\*{0,2}」（([^）]+)）", p.read_text('utf-8')):
                if (ja == NAME_JA) != (en == NAME):
                    bad.append(f"{p.relative_to(ROOT)}: 「{ja}」（{en}）")
        self.assertEqual(bad, [])


if __name__ == '__main__':
    out = ARGS[1] if len(ARGS) > 1 else None
    res = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if out:
        Path(out).write_text(json.dumps(dict(run=res.testsRun, failures=[(str(t[0]), t[1][-600:]) for t in res.failures],
                                             errors=[(str(t[0]), t[1][-600:]) for t in res.errors])), 'utf-8')
