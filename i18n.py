"""The add-on's Japanese UI (user 2026-10-22; the words are docs/design/GLOSSARY_ja_ui_2026-10-21.md of the project).

Blender translates a literal label, button, tooltip, property name, enum item and report by itself once the English
text is a key of the dictionary registered here (bpy.app.translations); Preferences > Interface > Translation
picks the language. Text put together at run time (an f-string) goes through tx() / rpt() instead, with the
English as a format string: tx("Pair: {0}", name). Its Japanese must have the same {} fields.

A word Blender itself translates (Delete, Name, Settings ...) is left out unless its Japanese differs from ours:
the dictionary is global, so a key here also changes Blender's own UI where the same English appears.
No new English text needs an entry to work; one without an entry stays English (check: ja_ui check_missing.py).
"""
import re
import unicodedata

try:
    import bpy
    from bpy.app import translations as _bt
except ImportError:                       # core/ and the tests import without Blender
    bpy = _bt = None

from .i18n_ja import JA, JA_CTX

# the add-on's own translation context: every entry is there too, and JA_CTX only there (bl_translation_context /
# translation_context=CTX on a panel, operator or property whose words would clash with Blender's)
CTX = "ac9_cloth_sim"


def _fill(s, msg, args):
    if not args:
        return s
    try:
        return s.format(*args)
    except (IndexError, KeyError, ValueError):
        return msg.format(*args)          # a broken translation shows the English rather than failing a draw


def _look(get, msg):
    s = get(msg, CTX)
    return get(msg) if s == msg else s    # a word only Blender translates


# the end of the report of an operator whose numbers are adjusted afterwards in the Redo panel (user 2026-10-23,
# Fable review REVIEW_fable_settings_places_2026-10-23: the panel is folded in the corner and goes at the next action)
F9 = "   (F9: adjust)"


def tx(msg, *args):
    """msg translated for the interface (a label, a button, a header line), then filled with args."""
    return _fill(_look(_bt.pgettext_iface, msg) if _bt else msg, msg, args)


def rpt(msg, *args):
    """msg translated as a report (self.report), then filled with args."""
    return _fill(_look(_bt.pgettext_rpt, msg) if _bt else msg, msg, args)


def _width(ch):
    return 2 if unicodedata.east_asian_width(ch) in 'WF' else 1


_NO_START = set("、。，．）」』】・ー々ぁぃぅぇぉっゃゅょァィゥェォッャュョ%)]:,.")


def wrap(text, width):
    """textwrap.wrap by the shown width: a Japanese character counts 2, and Japanese breaks between characters
    (not only at spaces), keeping 、。）and the like off the start of a line."""
    if text.isascii():
        import textwrap
        return textwrap.wrap(text, width)
    toks = re.findall(r'[\x21-\x7e]+|\s+|.', text)
    lines, cur, w = [], '', 0
    for t in toks:
        tw = sum(_width(c) for c in t)
        if t.isspace():
            if cur:
                cur, w = cur + ' ', w + 1
            continue
        if cur and w + tw > width and t[0] not in _NO_START:
            lines.append(cur.rstrip())
            cur, w = '', 0
        cur, w = cur + t, w + tw
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _dictionary():
    d = {}
    for en, ja in JA.items():
        for ctx in ('*', 'Operator', CTX):
            d[(ctx, en)] = ja
    for en, ja in JA_CTX.items():
        d[(CTX, en)] = ja
    return {'ja_JP': d}


def register():
    try:
        _bt.register(__name__, _dictionary())
    except ValueError:                    # already there after a reload
        _bt.unregister(__name__)
        _bt.register(__name__, _dictionary())


def unregister():
    try:
        _bt.unregister(__name__)
    except Exception:
        pass
