# SPDX-License-Identifier: GPL-3.0-or-later
"""Only selection.py writes the selection of pieces' objects.

A pick of pieces goes through selection.apply (or deselect_all / select_placed / refresh / borrow), which marks the
pieces in every view. A tool that sets object flags itself marks one view only, and the views drift apart again: the
picked pieces showed in one view and not the other three times before this guard (one selection, user 2026-11-14).

Every other `.select_set(...)` and `<view layer>.objects.active = ...` in the add-on is listed here with why it is
not a pick of pieces. A new one fails this test: route it through selection, or add it here with its reason.
Plain Python, no Blender: python tests/test_selection_writers.py
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (file, function): why it may write object selection itself
ALLOWED = {
    ('piece_move.py', 'restore'): 'transaction rollback restores object flags after logical projection, not a new pick',
    ('dress.py', 'execute'): "Edit Frame: the dressed garment object itself (not a piece) made active for its mode",
    ('drape_fbx.py', 'read_meshes'): "an FBX imported only for its meshes and removed: the selection the import "
                                     "changed put back exactly as it was",
    ('operators.py', '_replace_bom'): "a failed BOM replace: the selection put back exactly as it was before",
    ('operators.py', 'execute'): "collision object list: a collision object (not a piece); the pieces are dropped "
                                 "through selection.deselect_all",
    ('piece_move.py', '_remove_handles'): "a handle deselected in every view layer before its object is removed",
    ('placed_view.py', 'rollback'): "a failed placed-shape rebuild: the selection put back exactly as it was before",
    ('placement.py', '_select_center'): "Select Center: the garment's mirror center empty (not a piece) added",
    ('placement.py', 'restore_selection'): "a cancelled operation that made placed objects: the selection put back exactly as it was",
    ('toolbar.py', 'select_object'): "a click on an object that is not a piece's (pieces go through selection.apply)",
    ('work_screen.py', 'execute'): "Add Avatar: the body object (not a piece) selected",
}


def writers():
    out = []
    for path in sorted(ROOT.rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if rel == 'selection.py' or rel.startswith(('tests/', 'solver/')):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        stack = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, n):
                stack.append(n.name)
                self.generic_visit(n)
                stack.pop()
            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, n):
                if isinstance(n.func, ast.Attribute) and n.func.attr == 'select_set':
                    out.append((rel, stack[-1] if stack else '<module>', n.lineno, 'select_set'))
                self.generic_visit(n)

            def visit_Assign(self, n):
                for t in n.targets:
                    if isinstance(t, ast.Attribute) and t.attr == 'active' and isinstance(t.value, ast.Attribute) \
                            and t.value.attr == 'objects':
                        out.append((rel, stack[-1] if stack else '<module>', n.lineno, 'objects.active ='))
                self.generic_visit(n)
        V().visit(tree)
    return out


# (file, function): why it reads a piece's name off the active / selected objects itself. Which pieces are picked is
# read through selection (pieces, lead, pattern_pieces): read off one object it was the first piece of the garment's
# handle only, and a Shift-click on the garment showed one piece picked (2026-11-15).
READ_ALLOWED = {
    ('piece_move.py', '__init__'): 'transaction snapshots the active control and view for rollback; pieces come from SEL.piece_selection',
    ('placement.py', 'active_placed'): "the active placed object itself (its pose fields), not which pieces are picked",
    ('ui.py', 'draw'): "the active placed object's pose fields (an object, not the pick)",
    ('operators.py', '_replace_bom'): "a BOM replace keeps the selection to put it back exactly on a failure",
    ('outside.py', 'move_selected'): "keeps the selection to put it back exactly on a failure",
}
_SEL_READS = {'active_object', 'selected_objects'}
_PIECE_KEYS = {'PIECE_PROP', 'PROP', 'TWIN_PROP'}


def readers():
    """(file, function, line) of functions that read the active or selected objects and a piece key together."""
    out = []
    for path in sorted(ROOT.rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if rel == 'selection.py' or rel.startswith(('tests/', 'solver/', 'core/')):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            reads = keys = False
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute):
                    if n.attr in _SEL_READS and not isinstance(n.ctx, ast.Store):
                        reads = True
                    if n.attr in ('active', 'selected') and isinstance(n.value, ast.Attribute) \
                            and n.value.attr == 'objects' and isinstance(n.ctx, ast.Load):
                        reads = True
                    if n.attr in _PIECE_KEYS and isinstance(n.value, ast.Name) and n.value.id in ('PV', 'PLV'):
                        keys = True
                if isinstance(n, ast.Constant) and n.value in ('ac9_piece', 'ac9_placed', 'ac9_placed_twin'):
                    keys = True
            if reads and keys:
                out.append((rel, fn.name, fn.lineno))
    return out


class SelectionWriters(unittest.TestCase):
    def test_only_selection_reads_which_pieces_are_picked(self):
        bad = [r for r in readers() if (r[0], r[1]) not in READ_ALLOWED]
        self.assertEqual(bad, [], "a piece name read off the active / selected objects (read selection.pieces / "
                                  "lead instead, or list it in READ_ALLOWED with its reason):\n"
                                  + "\n".join(f"{f}:{ln} {fn}()" for f, fn, ln in bad))

    def test_only_selection_writes_piece_selection(self):
        bad = [w for w in writers() if (w[0], w[1]) not in ALLOWED]
        self.assertEqual(bad, [], "selection written outside selection.py (route it through selection.apply, or "
                                  "list it in ALLOWED with its reason):\n" + "\n".join(f"{f}:{ln} {fn}() {k}"
                                                                                       for f, fn, ln, k in bad))

    def test_allowed_entries_are_still_used(self):
        used = {(w[0], w[1]) for w in writers()}
        self.assertEqual(sorted(set(ALLOWED) - used), [], "remove the entries no longer needed")
        used = {(r[0], r[1]) for r in readers()}
        self.assertEqual(sorted(set(READ_ALLOWED) - used), [], "remove the read entries no longer needed")


if __name__ == '__main__':
    unittest.main()
