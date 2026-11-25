"""The add-on's Japanese UI (user 2026-10-22; the words are docs/design/GLOSSARY_ja_ui_2026-10-21.md of the project).

Blender translates a literal label, button, tooltip, property name, enum item and report by itself once the English
text is a key of the dictionary registered here (bpy.app.translations); Preferences > Interface > Translation
picks the language. Text put together at run time (an f-string) goes through tx() / rpt() instead, with the
English as a format string: tx("Pair: {0}", name). Its Japanese must have the same {} fields.

The dictionary is shared by Blender and every add-on, and a word is looked up in Blender's own translation first,
then in the add-ons' dictionaries in the order they were registered: the first one that has the word wins. So a
plain word in the default context ("Setup", "Fold", "Count") can come out as Blender's word or as another add-on's
(one that maps "Setup" to "Setup" kept our panel English). Our own interface is therefore looked up in our own
context CTX, where nobody else has entries: own_context() gives it to every panel, menu, operator and property of
the add-on before they are registered, and a layout.label / menu / popover with English text passes
text_ctxt=CTX (tx() looks in CTX first). The entries also stay in the default context for what Blender always
looks up there (tooltips, reports, labels built from a variable). tests/test_i18n_context.py checks it.
An operator's or menu's docstring becomes its tooltip as one line (own_context), so the entry is the one-line form.
English without an entry stays English on screen, without an error: tests/test_i18n_coverage.py finds it (every
registered label and tooltip, every literal shown in the source, every text drawn in sample states, and what tx() /
rpt() missed). The Japanese is added once, while a release is prepared: during development the test fails only on a
button's name and lists the rest in docs/translation_ledger.md; the release check (--release) fails on any of it.
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


# texts tx() / rpt() found no translation for (bounded): tests/test_i18n_coverage.py fails on the English ones
MISSES = set()


def _look(get, msg):
    s = get(msg, CTX)
    if s == msg:
        s = get(msg)                      # a word only Blender translates
        if s == msg and len(MISSES) < 2000:
            MISSES.add(msg)
    return s


# the end of the report of an operator whose numbers are adjusted afterwards in the Redo panel (user 2026-10-23,
# Fable review REVIEW_fable_settings_places_2026-10-23: the panel is folded in the corner and goes at the next action)


def tx(msg, *args):
    """msg translated for the interface (a label, a button, a header line), then filled with args."""
    return _fill(_look(_bt.pgettext_iface, msg) if _bt else msg, msg, args)


def rpt(msg, *args):
    """msg translated as a report (self.report), then filled with args."""
    return _fill(_look(_bt.pgettext_rpt, msg) if _bt else msg, msg, args)


EDITED = " (edited)"


def fabric_name(name):
    """A fabric's or interlining's name as shown: a preset's name (and "<preset> (edited)") in the interface
    language, a name the user typed as it is. The stored name stays the preset's English one, so a garment reads the
    same in any language, and Blender's own words never come in (it turns Leather into a word for skin)."""
    from .core import fabric as _fa
    names = {p['name'] for k in ('fabrics', 'interlinings') for p in _fa.presets().get(k, [])}
    base, tail = (name[:-len(EDITED)], EDITED) if name.endswith(EDITED) else (name, '')
    if base not in names:
        return name
    return tx(base) + (tx(tail) if tail else '')


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


def one_line(text):
    """A docstring as one line: the form an operator's or menu's tooltip and its dictionary entry take."""
    return " ".join(text.split())


def _dictionary():
    d = {}
    for en, ja in JA.items():
        en = one_line(en) if '\n' in en else en          # an entry copied from a wrapped docstring
        for ctx in ('*', 'Operator', CTX):
            d[(ctx, en)] = ja
    for en, ja in JA_CTX.items():
        d[(CTX, one_line(en) if '\n' in en else en)] = ja
    return {'ja_JP': d}


def _own_kinds():
    t = bpy.types
    return (t.Panel, t.Menu, t.Operator)


def own_context(modules):
    """Give CTX to the add-on's panels, menus and operators (bl_translation_context) and to the properties they and
    the property groups declare (translation_context), unless one sets its own. Run before the classes are
    registered: Blender reads both at registration."""
    kinds = _own_kinds()
    for mod in modules:
        for cls in list(vars(mod).values()):
            if not (isinstance(cls, type) and cls.__module__ == mod.__name__ and issubclass(cls, bpy.types.bpy_struct)):
                continue
            if issubclass(cls, kinds) and not getattr(cls, 'bl_translation_context', None):
                cls.bl_translation_context = CTX
            doc = cls.__dict__.get('__doc__')
            if (issubclass(cls, (bpy.types.Operator, bpy.types.Menu)) and doc
                    and not cls.__dict__.get('bl_description')):
                # Blender keeps a docstring's line breaks in the tooltip, so a tooltip wrapped over two lines
                # never met its dictionary entry (re-wrapping the docstring dropped the Japanese without a word)
                cls.bl_description = one_line(doc)
            for value in cls.__dict__.get('__annotations__', {}).values():
                keywords = getattr(value, 'keywords', None)     # bpy.props' deferred property
                if isinstance(keywords, dict) and 'translation_context' not in keywords:
                    keywords['translation_context'] = CTX


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
