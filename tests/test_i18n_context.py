# SPDX-License-Identifier: GPL-3.0-or-later
"""The add-on's Japanese is looked up in its own translation context, so another add-on's dictionary cannot change it.

A rival dictionary is registered first that maps every word of ours to "RIVAL" in Blender's default and operator
contexts (the first dictionary that has a word wins there: one add-on mapping "Setup" to "Setup" kept a panel title
English). Then every panel, menu and operator of the add-on, every property and enum item they declare, and the
words of the layout.label / menu / popover calls must still come out in our Japanese. A label call with English text
and no text_ctxt=CTX fails here too, read from the source.

Run: blender -b --factory-startup --python tests/test_i18n_context.py -- <parent of ac9_cloth_sim> [out.json]
"""
import ast
import json
import sys
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))

import bpy                                                   # noqa: E402
from bpy.app import translations as T                        # noqa: E402

import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import i18n as I, i18n_ja as JA           # noqa: E402

ROOT = Path(ac9_cloth_sim.__file__).resolve().parent
PKG = ac9_cloth_sim.__name__
RIVAL = "RIVAL"
RESULT = {}
# a file browser's properties come from bpy_extras' ImportHelper / ExportHelper (Blender's own words)
HELPER_PROPS = {'filepath', 'filter_glob', 'check_existing', 'directory', 'files'}


def _words():
    return {**JA.JA, **JA.JA_CTX}


def setUpModule():
    prefs = bpy.context.preferences.view
    prefs.language = 'ja_JP'
    prefs.use_translate_interface = True
    prefs.use_translate_tooltips = True
    rival = {}
    for en in JA.JA:
        rival[('*', en)] = RIVAL
        rival[('Operator', en)] = RIVAL
    T.register("ac9_test_rival_dictionary", {'ja_JP': rival})
    ac9_cloth_sim.register()


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass
    T.unregister("ac9_test_rival_dictionary")


def _classes():
    kinds = (bpy.types.Panel, bpy.types.Menu, bpy.types.Operator, bpy.types.PropertyGroup)
    seen = []
    for name, mod in list(sys.modules.items()):
        if not (name == PKG or name.startswith(PKG + '.')) or mod is None:
            continue
        for cls in list(vars(mod).values()):
            if (isinstance(cls, type) and cls.__module__ == name and issubclass(cls, kinds)
                    and getattr(cls, 'is_registered', False) and cls not in seen):
                seen.append(cls)
    return seen


def _own_properties(cls):
    """The properties the class declares (not the ones every operator or property group has)."""
    if issubclass(cls, bpy.types.Operator):
        mod, fn = cls.bl_idname.split('.')
        rna = getattr(getattr(bpy.ops, mod), fn).get_rna_type()
        base = set()
    elif issubclass(cls, bpy.types.PropertyGroup):
        rna = cls.bl_rna
        base = {p.identifier for p in bpy.types.PropertyGroup.bl_rna.properties}
    else:
        return []
    base |= HELPER_PROPS
    return [p for p in rna.properties if p.identifier not in base and p.identifier != 'rna_type']


def _ui(text, ctx):
    return T.pgettext_iface(text, ctx)


class TestOwnContext(unittest.TestCase):

    def test_rival_wins_the_default_context(self):
        """The test's rival really is in the way (else the checks below prove nothing)."""
        self.assertEqual(_ui("Setup", '*'), RIVAL)

    def test_classes_and_properties_use_ctx(self):
        words = _words()
        wrong_ctx, wrong_word, checked = [], [], 0
        for cls in _classes():
            rna = cls.bl_rna
            if not issubclass(cls, bpy.types.PropertyGroup):
                ctx = rna.translation_context
                if ctx != I.CTX:
                    wrong_ctx.append(f"{cls.__name__}: {ctx}")
                label = getattr(cls, 'bl_label', '')
                if label in words:
                    checked += 1
                    if _ui(label, ctx) != words[label]:
                        wrong_word.append(f"{cls.__name__} {label!r} -> {_ui(label, ctx)}")
            for prop in _own_properties(cls):
                if prop.translation_context != I.CTX:
                    wrong_ctx.append(f"{cls.__name__}.{prop.identifier}: {prop.translation_context}")
                texts = [prop.name] + [item.name for item in getattr(prop, 'enum_items', [])]
                for text in texts:
                    if text in words:
                        checked += 1
                        got = _ui(text, prop.translation_context)
                        if got != words[text]:
                            wrong_word.append(f"{cls.__name__}.{prop.identifier} {text!r} -> {got}")
        RESULT['classes_and_properties'] = {'checked': checked, 'wrong_context': wrong_ctx, 'wrong_word': wrong_word}
        self.assertGreater(checked, 300)
        self.assertEqual(wrong_ctx, [])
        self.assertEqual(wrong_word, [])

    def test_known_clashes(self):
        """The words that showed another add-on's or Blender's translation in a user's Blender."""
        words = _words()
        names = ("Setup", "Fold", "Fold Lines", "Seam Lines", "Import BOM", "Overlays: ON", "Overlays: OFF",
                 "Lines", "Count", "Replace", "Smooth Radius", "Scale", "Stop", "Orientation", "Transform")
        bad = {n: _ui(n, I.CTX) for n in names if n in words and _ui(n, I.CTX) != words[n]}
        RESULT['known_clashes'] = bad
        self.assertEqual(bad, {})

    def test_tx_reads_ctx(self):
        self.assertEqual(I.tx("Setup"), JA.JA["Setup"])

    def test_label_calls_pass_ctx(self):
        """layout.label / menu / popover with English text in the source pass text_ctxt (labels use the default
        context otherwise). Text built only by tx() is already translated and needs none."""
        missing = []
        for path in sorted(ROOT.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ('label', 'menu', 'popover')):
                    continue
                kws = {k.arg: k.value for k in node.keywords}
                if 'text' not in kws or 'text_ctxt' in kws:
                    continue
                v = kws['text']
                if isinstance(v, ast.Call) and getattr(v.func, 'id', '') in ('tx', 'fabric_name'):
                    continue
                if isinstance(v, ast.JoinedStr):
                    continue
                if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and any(c.isalpha() for c in n.value)
                       for n in ast.walk(v)):
                    missing.append(f"{path.name}:{node.lineno}")
        RESULT['label_calls_without_ctxt'] = missing
        self.assertEqual(missing, [])


if __name__ == '__main__':
    prog = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2)
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps({'ok': prog.result.wasSuccessful(), **RESULT}, ensure_ascii=False,
                                            indent=1), encoding='utf-8')
