# SPDX-License-Identifier: GPL-3.0-or-later
"""Every English text the add-on shows comes out in Japanese when Blender is set to Japanese.

The dictionary (i18n_ja.py) is kept by hand apart from the code, and a text without an entry silently stays English.
So the Japanese was finished three times and new buttons brought English back each time. This test is the gate that
was missing. It fails on any text a user would read in English:

1. registered: every panel, menu, operator and tool label and tooltip, every property name and tooltip, every enum
   item, looked up the way Blender does (in its translation context)
2. source: the literal first argument of tx() / rpt() / poll_message_set() / self.report(), every literal text= of a
   layout call, every literal description=; an f-string in one of those places (it can never be in the dictionary:
   put the English in tx("... {0}", value) instead)
3. drawn: every panel and menu drawn to the end with a recording layout, over a set of synthetic scenes (no garment;
   a drawn garment with pieces, an internal line, a seam, a button and a tape, with each kind of thing selected), and
   every text drawn is looked up the way Blender would show it. A literal handed down to a helper (a field label) is
   caught here. Every tx() / rpt() that found no Japanese while drawing is a miss too (i18n.MISSES).

Translated once, while a release is prepared (user 2026-11-23): during development a text may stay English, and this
test writes every one to docs/translation_ledger.md (English, the date it was first seen, where, the previous
translation when the English was rewritten, a note column kept across runs) and fails only on a button's name.
With --release (the release check) it fails on every text.

What is not English: names the user gives (pieces, garments, fabrics), numbers and units, key names, the brand and
file formats, and any English word the Japanese itself keeps (from the dictionary's own values, e.g. "Sim").

Run: blender -b --factory-startup --python tests/test_i18n_coverage.py -- <parent of ac9_cloth_sim> [out.json] [--release]
"""
import ast
import importlib
import json
import os
import re
import sys
import time
import traceback
import unittest
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
FLAGS = {a for a in ARGS if a.startswith('--')}
ARGS = [a for a in ARGS if not a.startswith('--')]
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))
# --release (the release check): every English text fails. Without it (development) only a button's name fails and the
# rest goes to the translation ledger, translated once while a release is prepared (user 2026-11-23)
RELEASE = '--release' in FLAGS or bool(os.environ.get('AC9_I18N_RELEASE'))

import bpy                                                   # noqa: E402
from bpy.app import translations as T                        # noqa: E402

import ac9_cloth_sim                                         # noqa: E402
from ac9_cloth_sim import i18n as I, i18n_ja as JA           # noqa: E402

ROOT = Path(ac9_cloth_sim.__file__).resolve().parent
PKG = ac9_cloth_sim.__name__
RESULT = {}
PENDING = []                    # (message, English, is a button's name) of every text still English
BUTTON_KINDS = {'op', 'menu', 'popover'}      # drawn as a button the user presses


def _gate(case, key, items):
    """Record the English left (the ledger); fail on all of it at a release, on the buttons' names otherwise."""
    PENDING.extend(items)
    RESULT[key] = [m for m, _en, _b in items]
    case.assertEqual([m for m, _en, b in items if RELEASE or b], [])
HELPER_PROPS = {'filepath', 'filter_glob', 'check_existing', 'directory', 'files'}   # Blender's own file browser
FIXTURE_NAMES = ('PieceA', 'PieceB', 'PieceC', 'Garment', 'Fabric', 'Bond', 'Tape')

# words that are the same in Japanese: the brand and product names, file formats, keys, units
KEEP = r'''AC9|Cloth\s?Sim|Cloth\s?Retopo|Newton|NVIDIA|CUDA|GPU|CPU|BOM(\.xml)?|DXF(-AAMA)?|JSON|KES|OBJ|FBX|UV|
    Blender|Python|python\.exe|warp(-lang)?|Ctrl|Shift|Alt|Enter|Esc|Tab|Backspace|Del|Space|Home|LMB|RMB|MMB|
    mm|cm|deg|N/m|g/m²|µN·m|MB|GB|ms|px|blend|jsonl|x|y|z'''


def _accepted():
    """English words the Japanese keeps (Edit Mode names ...): any word that appears in a translation."""
    words = set()
    for ja in list(JA.JA.values()) + list(JA.JA_CTX.values()):
        words.update(re.findall(r'[A-Za-z]{2,}', re.sub(r'\{[^}]*\}', '', ja)))
    return words


ACCEPTED = set()


def english_left(s):
    """The English words a user would read in s (after Blender's lookup)."""
    if not s or not isinstance(s, str):
        return []
    s = re.sub(r'\{[^}]*\}', ' ', s)
    s = re.sub(r'\b(' + KEEP.replace('\n', '').replace(' ', '') + r')\b', ' ', s)
    for n in FIXTURE_NAMES:
        s = s.replace(n, ' ')
    s = re.sub(r'\b[A-Z0-9]*[_0-9][A-Z0-9_]*\b|\b[A-Z]\b', ' ', s)   # identifiers (EDIT_PATTERN, F9), side letters
    return [w for w in re.findall(r'[A-Za-z]{2,}', s) if w not in ACCEPTED]


def setUpModule():
    pv = bpy.context.preferences.view
    pv.language = 'ja_JP'
    pv.use_translate_interface = pv.use_translate_tooltips = pv.use_translate_reports = True
    pv.use_translate_new_dataname = False
    ac9_cloth_sim.register()
    ACCEPTED.update(_accepted())


def tearDownModule():
    try:
        ac9_cloth_sim.unregister()
    except Exception:                                       # noqa: BLE001 -- teardown only
        pass


def _classes():
    out = []
    for name in dir(bpy.types):
        c = getattr(bpy.types, name, None)
        if isinstance(c, type) and getattr(c, '__module__', '').startswith(PKG):
            out.append(c)
    tb = sys.modules[PKG + '.toolbar']
    out += [c for c in vars(tb).values() if isinstance(c, type) and issubclass(c, bpy.types.WorkSpaceTool)
            and c is not bpy.types.WorkSpaceTool]
    return out


def _iface(s, ctx):
    return T.pgettext_iface(s, ctx) if ctx else T.pgettext_iface(s)


def _tip(s):
    return T.pgettext_tip(s)


class Registered(unittest.TestCase):
    def test_labels_and_tooltips(self):
        import inspect
        miss = []
        base = {p.identifier for t in (bpy.types.Operator, bpy.types.Panel, bpy.types.PropertyGroup, bpy.types.Menu,
                                       bpy.types.UIList, bpy.types.AddonPreferences) for p in t.bl_rna.properties}
        for c in _classes():
            is_tool = issubclass(c, bpy.types.WorkSpaceTool)
            ctx = 'Operator' if is_tool else getattr(c, 'bl_translation_context', None)
            label = getattr(c, 'bl_label', '') or ''
            if english_left(_iface(label, ctx)):
                miss.append((f"{c.__name__} label: {label}", label, issubclass(c, (bpy.types.Operator, bpy.types.Menu)) or is_tool))
            internal = 'INTERNAL' in getattr(c, 'bl_options', set())          # never on a button or in a menu
            if (issubclass(c, (bpy.types.Operator, bpy.types.Menu)) or is_tool) and not internal:   # a panel's docstring is no tooltip
                desc = getattr(c, 'bl_description', '') or inspect.cleandoc(c.__doc__ or '')
                if desc and not callable(desc) and english_left(_tip(desc)):
                    miss.append((f"{c.__name__} tooltip: {desc[:90]}", desc, False))
            rna = getattr(c, 'bl_rna', None)
            props = list(rna.properties) if rna is not None and not is_tool else []
            if issubclass(c, bpy.types.Operator):
                try:
                    g, f = c.bl_idname.split('.')
                    props = list(getattr(getattr(bpy.ops, g), f).get_rna_type().properties)
                except Exception:                           # noqa: BLE001
                    pass
            for p in props:
                if (p.identifier in base or p.identifier in HELPER_PROPS or p.identifier == 'rna_type'
                        or p.is_hidden):                    # HIDDEN: never drawn (Redo panel included)
                    continue
                if english_left(_iface(p.name, p.translation_context)):
                    miss.append((f"{c.__name__}.{p.identifier} name: {p.name}", p.name, False))
                if english_left(_tip(p.description)):
                    miss.append((f"{c.__name__}.{p.identifier} tooltip: {p.description[:90]}", p.description, False))
                if p.type == 'ENUM' and not p.is_enum_flag:
                    for it in p.enum_items:
                        if english_left(_iface(it.name, p.translation_context)):
                            miss.append((f"{c.__name__}.{p.identifier}[{it.identifier}]: {it.name}", it.name, False))
                        if english_left(_tip(it.description)):
                            miss.append((f"{c.__name__}.{p.identifier}[{it.identifier}] tooltip: {it.description[:90]}", it.description, False))
        for owner in (bpy.types.Scene, bpy.types.Object, bpy.types.WindowManager, bpy.types.Mesh):
            for p in owner.bl_rna.properties:
                if not p.identifier.startswith('ac9') or p.type == 'POINTER':   # a settings group, no field
                    continue
                for s, look in ((p.name, lambda s: _iface(s, p.translation_context)), (p.description, _tip)):
                    if english_left(look(s)):
                        miss.append((f"{owner.__name__}.{p.identifier}: {s[:90]}", s, False))
        _gate(self, 'registered', miss)


# ---- 2. source
CALLS = {'tx', 'rpt', 'poll_message_set', 'header_text_set', 'status_text_set'}
TEXT_CALLS = {'label', 'operator', 'prop', 'menu', 'popover', 'operator_menu_enum', 'prop_enum', 'prop_search',
              'progress', 'operator_enum', 'prop_menu_enum', 'template_ID'}


def _ui_files():
    for p in sorted(ROOT.rglob('*.py')):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(('tests/', 'solver/')) or rel in ('i18n_ja.py', 'i18n.py'):
            continue
        yield rel, p


def _shown_literals():
    """(file, line, where, text or None for an f-string) for every literal that reaches the screen as it is."""
    out = []
    for rel, p in _ui_files():
        text = p.read_text(encoding='utf-8')
        lines = text.splitlines()
        tree = ast.parse(text)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')
            cand = []
            if name in CALLS and n.args:
                cand.append((name, n.args[0]))
            if name == 'report' and len(n.args) > 1:
                cand.append(('report', n.args[1]))
            for kw in n.keywords:
                if kw.arg == 'text' and name in TEXT_CALLS:
                    cand.append((name + ' text=', kw.value))
                if kw.arg == 'description':
                    cand.append(('description=', kw.value))
            data = any(kw.arg == 'translate' and isinstance(kw.value, ast.Constant) and kw.value.value is False
                       for kw in n.keywords)            # translate=False: the text is data (names, numbers)
            for where, v in cand:
                vals = [v.body, v.orelse] if isinstance(v, ast.IfExp) else [v]
                for v2 in vals:
                    if isinstance(v2, ast.Constant) and isinstance(v2.value, str):
                        out.append((rel, n.lineno, where, v2.value))
                    elif isinstance(v2, ast.JoinedStr) and not data:
                        out.append((rel, n.lineno, where, None))
        # a text put together with + (a header line, a hint) in a function that shows text: every English piece
        # must be translated on its own
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {getattr(c.func, 'attr', getattr(c.func, 'id', '')) for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if not calls & (TEXT_CALLS | CALLS | {'report', 'set_note'}):
                continue
            parents = {c: n for n in ast.walk(fn) for c in ast.iter_child_nodes(n)}
            for n in ast.walk(fn):
                parts = _plus_parts(n)
                if not parts or _in_run_log(n, parents) or SKIP_MARK in lines[n.lineno - 1]:
                    continue
                for v in parts:
                    if (isinstance(v, ast.Constant) and isinstance(v.value, str) and not rel.startswith('core/')
                            and re.search(r'[A-Za-z]{2,}\W*\s\W*[A-Za-z]{2,}', v.value)):   # a phrase, not '.tmp'
                        out.append((rel, n.lineno, '+', v.value))
    return list(dict.fromkeys(out))


# the run log (log()) and the problem record (diagnose mark()) stay English: they are read in bug reports and by the
# AI companion, the panel shows them folded. A line marked "# i18n-skip: <reason>" is left out (keep them few).
RUN_LOG_CALLS = {'log', 'mark'}
SKIP_MARK = '# i18n-skip:'


def _in_run_log(n, parents):
    while n in parents:
        n = parents[n]
        if isinstance(n, ast.Call) and getattr(n.func, 'attr', getattr(n.func, 'id', '')) in RUN_LOG_CALLS:
            return True
    return False


def _plus_parts(n):
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        return [n.left, n.right]
    if isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add):
        return [n.value]
    return []


class Source(unittest.TestCase):
    def test_literals_have_japanese(self):
        have = set(JA.JA) | set(JA.JA_CTX)
        miss, fstr = [], []
        for rel, line, where, s in _shown_literals():
            if s is None:
                fstr.append(f"{rel}:{line} {where}")
            elif s not in have and english_left(s):
                miss.append((f"{rel}:{line} {where} {s[:90]}", s, where == 'operator text='))
        RESULT['source_fstrings'] = fstr
        _gate(self, 'source_missing', miss)

    def test_no_fstrings_where_text_is_shown(self):
        fstr = [f"{rel}:{line} {where}" for rel, line, where, s in _shown_literals() if s is None]
        self.assertEqual(fstr, [])

    def test_placeholders_match(self):
        """A translation with other {} fields than its English shows the English (i18n._fill) or the wrong number."""
        field = re.compile(r'\{[^{}]*\}')
        bad = [en[:80] for d in (JA.JA, JA.JA_CTX) for en, ja in d.items()
               if sorted(field.findall(en)) != sorted(field.findall(ja))]
        self.assertEqual(bad, [])

    def test_skip_marks_few(self):
        """'# i18n-skip: <reason>' leaves a line English on purpose: each says why, and they stay few."""
        marks = [(rel, i + 1, ln.split(SKIP_MARK, 1)[1].strip())
                 for rel, p in _ui_files() for i, ln in enumerate(p.read_text(encoding='utf-8').splitlines())
                 if SKIP_MARK in ln]
        self.assertEqual([m for m in marks if not m[2]], [])
        self.assertLessEqual(len(marks), 10, marks)


# ---- 3. drawn
class Area:
    width = 270
    height = 800
    type = 'VIEW_3D'
    ui_type = 'VIEW_3D'
    spaces = ()
    x = y = 0

    def tag_redraw(self):
        pass


class Rec:
    """A UILayout stand-in: records every text with the translation context Blender would use."""

    def __init__(self, log):
        self.__dict__.update(_log=log, active=True, enabled=True, alert=False, scale_x=1.0, scale_y=1.0,
                             use_property_split=False, use_property_decorate=False, alignment='EXPAND',
                             operator_context='INVOKE_DEFAULT', emboss='NORMAL', ui_units_x=0, ui_units_y=0,
                             activate_init=False)

    def __setattr__(self, k, v):
        self.__dict__[k] = v

    def __bool__(self):
        return True

    def _add(self, kind, text, ctx, src, translate=True):
        if text and translate:
            self._log.append((kind, text, ctx, src))

    def _sub(self, *a, **k):
        return Rec(self._log)
    row = column = column_flow = grid_flow = box = split = _sub

    def panel(self, *a, **k):
        return Rec(self._log), Rec(self._log)
    panel_prop = panel

    def label(self, text='', text_ctxt='', translate=True, **k):
        self._add('label', text, text_ctxt or None, 'label', translate)

    def operator(self, idname, text=None, text_ctxt='', translate=True, **k):
        rna = None
        try:
            g, f = idname.split('.')
            rna = getattr(getattr(bpy.ops, g), f).get_rna_type()
        except Exception:                                   # noqa: BLE001
            pass
        if text is None:
            if rna is not None:
                self._add('op', rna.name, rna.translation_context, idname)
        else:
            self._add('op', text, text_ctxt or (rna.translation_context if rna else 'Operator'), idname, translate)

        class P:
            def __setattr__(s, a, b):
                pass
        return P()

    def operator_menu_enum(self, idname, prop, text=None, **k):
        return self.operator(idname, text=text, **{x: y for x, y in k.items() if x in ('text_ctxt', 'translate')})

    def prop(self, data, prop, text=None, text_ctxt='', translate=True, **k):
        try:
            p = data.bl_rna.properties[prop]
        except Exception:                                   # noqa: BLE001
            p = None
        if text is None:
            if p is not None and not k.get('icon_only'):
                if p.type == 'ENUM' and k.get('expand'):
                    for it in p.enum_items:
                        self._add('enum', it.name, p.translation_context, prop)
                else:
                    self._add('prop', p.name, p.translation_context, prop)
        else:
            self._add('prop', text, text_ctxt or (p.translation_context if p else None), prop, translate)

    def prop_enum(self, data, prop, value, text=None, text_ctxt='', **k):
        try:
            p = data.bl_rna.properties[prop]
        except Exception:                                   # noqa: BLE001
            p = None
        if text is None and p is not None:
            try:
                self._add('enum', p.enum_items[value].name, p.translation_context, prop)
            except Exception:                               # noqa: BLE001
                pass
        elif text:
            self._add('enum', text, text_ctxt or (p.translation_context if p else None), prop)

    def prop_search(self, data, prop, *a, text=None, **k):
        return self.prop(data, prop, text=text)

    def progress(self, text='', text_ctxt='', **k):
        self._add('label', text, text_ctxt or None, 'progress')

    def separator(self, *a, **k):
        pass
    separator_spacer = template_list = separator

    def __getattr__(self, k):                               # menu, popover, template_*, context_pointer_set ...
        if k.startswith('_'):
            raise AttributeError(k)

        def f(*a, **kw):
            if kw.get('text'):
                self._add(k, kw['text'], kw.get('text_ctxt') or None, k, kw.get('translate', True))
            return Rec(self._log)
        return f


class Ctx:
    def __init__(self, real, area_type):
        self._real = real
        self.area = Area()
        self.area.type = self.area.ui_type = area_type
        self.region = Area()

        class Prefs:                                        # ui_scale is 0 without a window
            system = type('Sys', (), {'ui_scale': 1.0})()
            view = real.preferences.view
            addons = real.preferences.addons
            edit = real.preferences.edit
            inputs = real.preferences.inputs
        self.preferences = Prefs()

    def __getattr__(self, k):
        return getattr(self._real, k)


def _draw_all(state):
    """Draw every panel and menu (in the 3D view's sidebar, and in the Properties editor's Tool tab where the AC9
    tool panels live); returns (drawn misses, draw errors)."""
    miss, errors = [], []
    for c, area_type in [(c, a) for c in _classes() if issubclass(c, (bpy.types.Panel, bpy.types.Menu))
                         for a in ('VIEW_3D', 'PROPERTIES')]:
        ctx = Ctx(bpy.context, area_type)
        try:
            if hasattr(c, 'poll') and not c.poll(ctx):
                continue
        except Exception:                                   # noqa: BLE001
            errors.append(f"{state} {c.__name__} poll: {traceback.format_exc().splitlines()[-1]}")
            continue
        log = []
        s = type('Self', (), {})()
        s.layout = Rec(log)
        for k in dir(c):
            if not k.startswith('__') and not hasattr(s, k):
                try:
                    setattr(s, k, getattr(c, k))
                except Exception:                           # noqa: BLE001
                    pass
        try:
            if hasattr(c, 'draw_header'):
                c.draw_header(s, ctx)
            c.draw(s, ctx)
        except Exception:                                   # noqa: BLE001
            tb = traceback.format_exc().splitlines()
            errors.append(f"{state} {c.__name__}: {tb[-1]} @ {[x for x in tb if PKG in x][-1:]}")
        for kind, text, tctx, src in log:
            shown = _iface(text, tctx) if text.isascii() else text   # already Japanese: no entry to look up
            if english_left(shown):
                miss.append((f"{c.__name__} {kind} {src}: {text[:90]}", text, kind in BUTTON_KINDS))
    return miss, errors


def _scenes():
    """Synthetic garments, each yielded with the selection set up (factory polygons; no solver, no user data)."""
    ctx = bpy.context
    m = {n: importlib.import_module(PKG + '.' + n) for n in
         ('selection', 'store', 'tool_tab', 'draft_tool', 'pattern_view', 'measure_edit', 'seam_edit_tool')}
    S, DR, FA, BT = [importlib.import_module(PKG + '.core.' + n) for n in ('spec', 'draft', 'fabric', 'buttons')]
    yield 'no garment'
    sp = S.new_spec('Garment')
    for i, n in enumerate(FIXTURE_NAMES[:3]):
        x = 50 + 200 * i
        DR.add_polygon(sp, [[x, 50], [x + 100, 50], [x + 100, 150], [x, 150]], name=n)
    fid = FA.add_custom(sp, 'Fabric')
    bond = FA.add_custom_interlining(sp, 'Bond')
    FA.set_pieces(sp, ['PieceA'], fabric_id=fid)
    FA.set_pieces(sp, ['PieceB'], bond=bond, what='bond')
    lid = DR.add_internal(sp, 'PieceC', [[470, 80], [520, 80]], mirror=False)

    def side(n):
        e = S.piece_index(sp)[n]['edges'][0]['id']
        return {'piece': n, 'from': [e, 0.0], 'to': [e, 1.0], 'dir': 1}
    sp['seams'] = [dict(id='U1', a=side('PieceA'), b=side('PieceB'), source='user', check='')]
    button = BT.add(sp, 'PieceA', [75, 100], ctx.scene.ac9_button_style)['id']
    e0 = side('PieceA')['from'][0]
    sp['measure'] = {'pattern': [dict(name='Tape', a={'piece': 'PieceA', 'edge': e0, 't': 0.},
                                      b={'piece': 'PieceA', 'edge': e0, 't': 1.})]}
    m['store'].new_text(ctx, sp['name']).write(S.dumps(sp))
    m['store'].invalidate()
    spec, ol = m['store'].get(ctx)
    m['pattern_view'].rebuild(ctx, spec, ol)
    m['store'].sync_seam_list(ctx, spec)
    ctx.view_layer.update()
    SEL, DT, TT = m['selection'], m['draft_tool'], m['tool_tab']

    def clear():
        SEL._cancel_pending()
        DT.select_internal(ctx)
        DT.select_edge(ctx)
        DT.select_corner(ctx)
        m['measure_edit'].select(ctx, '')
        m['seam_edit_tool'].unselect_seam(ctx)
        SEL.deselect_all(ctx)
    yield 'garment, nothing selected'
    for what, names in (('one piece', ['PieceA']), ('piece with an internal line', ['PieceC']),
                        ('two pieces', ['PieceA', 'PieceB'])):
        clear()
        SEL.apply(ctx, names, active=names[0])
        TT.piece_clicked(ctx)
        ctx.view_layer.update()
        yield 'garment, ' + what
    for what, act in (('seam', lambda: m['seam_edit_tool'].select_seam(ctx, 'U1')),
                      ('internal line', lambda: DT.select_internal(ctx, 'PieceC', lid)),
                      ('button', lambda: bpy.ops.ac9_cloth_sim.button_select(button=button)),
                      ('tape', lambda: m['measure_edit'].select(ctx, 'pattern:Tape')),
                      ('fabric', lambda: TT.focus_item(ctx, 'FABRIC', fid)),
                      ('interlining', lambda: TT.focus_item(ctx, 'BOND', bond)),
                      ('edge', lambda: DT.select_corner(ctx, 'PieceA', S.piece_index(spec)['PieceA']['edges'][0]['id']))):
        clear()
        act()
        ctx.view_layer.update()
        yield 'garment, ' + what + ' selected'


class Drawn(unittest.TestCase):
    def test_every_drawn_text_is_japanese(self):
        I.MISSES.clear()
        miss, errors, states = [], [], []
        for state in _scenes():
            states.append(state)
            mm, ee = _draw_all(state)
            miss += [(f"[{state}] {m}", en, b) for m, en, b in mm]
            errors += ee
        # one line per text: the first state it was seen in
        seen, uniq = set(), []
        for x in miss:
            key = x[0].split('] ', 1)[1]
            if key not in seen:
                seen.add(key)
                uniq.append(x)
        RESULT['states'] = states
        RESULT['draw_errors'] = errors
        self.assertEqual(errors, [])
        _gate(self, 'drawn_missing', uniq)
        _gate(self, 'tx_missing', [(f"tx: {s[:90]}", s, False) for s in sorted(I.MISSES) if english_left(s)])


# ---- 4. the translation ledger: what is still English, for the one translation pass before a release
LEDGER = ROOT / 'docs' / 'translation_ledger.md'
LEDGER_HEAD = """# 翻訳の台帳（自動で作る）

`tests/test_i18n_coverage.py` を回すたびに作り直す表。日本語の訳がまだ無い英語の一覧で、公開の準備のときに
まとめて訳す（開発中は英語のままでよい。ボタンの名前だけは開発中も試験が止める）。

- 訳し終えた行（`i18n_ja.py` に入った英語）は次に回したときに消える。消えた行の記録は git の履歴に残る。
- 「訳し直し」は、英語が書き換えられて前の訳が使われなくなった行。前の英語と前の訳を「前の訳」に出す。
- 「メモ」の欄は人が書いてよい（どの機能で足したか、用語集のどれに合わせるか）。作り直しても残す。
- 公開前の関門（配布の検査）は、この表が空でないと不合格にする。

"""
COLUMNS = ('状態', '英語', '日本語', '見つけた日', '場所', '前の訳', 'メモ')


def _cells(line):
    return [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', line.strip())[1:-1]]


def _cell(s):
    return ' '.join(str(s).split()).replace('|', '\\|')


def _old_rows():
    rows = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding='utf-8').splitlines():
            c = _cells(line) if line.startswith('|') else []
            if len(c) == len(COLUMNS) and c[0] in ('未訳', '訳し直し'):
                rows[c[1]] = dict(zip(COLUMNS, c))
    return rows


def _unused_entries():
    """Dictionary entries no code or data names any more: a text rewritten since it was translated."""
    used = set()
    for p in ROOT.rglob('*'):
        if (p.suffix in ('.py', '.json') and not {'tests', 'docs'} & set(p.relative_to(ROOT).parts)
                and p.name != 'i18n_ja.py'):
            try:
                tree = ast.parse(p.read_text(encoding='utf-8')) if p.suffix == '.py' else None
            except (SyntaxError, UnicodeDecodeError):
                continue
            if tree is not None:
                used.update(I.one_line(n.value) for n in ast.walk(tree)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str))
            else:
                used.update(I.one_line(s) for s in re.findall(r'"((?:[^"\\]|\\.)*)"', p.read_text(encoding='utf-8')))
    return {I.one_line(en): ja for d in (JA.JA, JA.JA_CTX) for en, ja in d.items() if I.one_line(en) not in used}


def _filled_by(formats):
    """A drawn text that is a format of a tx() miss filled in ("Pair shown: PieceA"): the format is the row."""
    pats = [re.compile(re.sub(r'\\\{[^}]*\\\}', '.*', re.escape(f)) + r'\Z', re.S) for f in formats if '{' in f]
    return lambda s: any(p.match(s) for p in pats)


def write_ledger():
    import difflib
    pending, formats = {}, {I.one_line(en) for m, en, _b in PENDING if m.startswith('tx:')}
    filled = _filled_by(formats)
    for msg, en, _b in PENDING:
        en = I.one_line(en)
        if en not in formats and filled(en):
            continue
        where = re.sub(r'^\[[^\]]*\] ', '', msg).split(' ', 1)[0].split(':', 1)[0]
        pending.setdefault(en, [])
        if where not in pending[en]:
            pending[en].append(where)
    # the previous translation of a rewritten text: a dictionary entry that became unused since the last run (the
    # entries unused at the last run are remembered beside the ledger; the first run only remembers them)
    old, unused = _old_rows(), _unused_entries()
    seen_file = LEDGER.with_name(LEDGER.stem + '.unused.json')
    before = set(json.loads(seen_file.read_text(encoding='utf-8'))) if seen_file.exists() else set(unused)
    fresh = {en: ja for en, ja in unused.items() if en not in before}
    today = time.strftime('%Y-%m-%d')
    rows = []
    for en, wheres in sorted(pending.items(), key=lambda kv: (old.get(kv[0], {}).get('見つけた日', today), kv[0])):
        prev = old.get(en, {})
        best = max(fresh, key=lambda u: difflib.SequenceMatcher(None, u, en).ratio(), default=None)
        was = prev.get('前の訳', '')                     # found at an earlier run: kept until translated
        if not was and best is not None and difflib.SequenceMatcher(None, best, en).ratio() >= 0.5:
            was = f"{best} → {fresh[best]}"
        rows.append({'状態': '訳し直し' if was else '未訳', '英語': en, '日本語': '',
                     '見つけた日': prev.get('見つけた日') or today, '場所': ' / '.join(wheres[:3]),
                     '前の訳': was, 'メモ': prev.get('メモ', '')})
    out = [LEDGER_HEAD, f"未訳 {sum(r['状態'] == '未訳' for r in rows)} 件・訳し直し "
                        f"{sum(r['状態'] == '訳し直し' for r in rows)} 件", '',
           '| ' + ' | '.join(COLUMNS) + ' |', '|' + '---|' * len(COLUMNS)]
    out += ['| ' + ' | '.join(_cell(r[k]) for k in COLUMNS) + ' |' for r in rows]
    text = '\n'.join(out) + '\n'
    if not LEDGER.exists() or LEDGER.read_text(encoding='utf-8') != text:
        LEDGER.write_text(text, encoding='utf-8', newline='\n')
    seen = json.dumps(sorted(unused), ensure_ascii=False, indent=0) + '\n'
    if not seen_file.exists() or seen_file.read_text(encoding='utf-8') != seen:
        seen_file.write_text(seen, encoding='utf-8', newline='\n')
    return len(rows)

if __name__ == '__main__':
    prog = unittest.main(argv=['x'], exit=False, verbosity=1)
    RESULT['ok'] = prog.result.wasSuccessful()
    RESULT['release'] = RELEASE
    RESULT['buttons'] = sorted({m for m, _en, b in PENDING if b})
    if not RELEASE and LEDGER.parent.is_dir():        # the repository (docs/ is not in the ZIP)
        RESULT['ledger_rows'] = write_ledger()
    RESULT['counts'] = {k: len(v) for k, v in RESULT.items() if isinstance(v, list)}
    if len(ARGS) > 1:
        Path(ARGS[1]).write_text(json.dumps(RESULT, ensure_ascii=False, indent=1), encoding='utf-8')
    sys.exit(0 if RESULT['ok'] else 1)
