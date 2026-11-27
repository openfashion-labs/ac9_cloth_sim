# SPDX-License-Identifier: GPL-3.0-or-later
"""Small text editing state independent of Blender data and its undo history."""
import ast
import math


def number(expression):
    """Arithmetic for dimensionless integer fields; no names, calls or exponentiation."""
    tree = ast.parse(expression, mode='eval')
    if len(list(ast.walk(tree))) > 100:
        raise ValueError('expression too long')
    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            out = float(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            out = value(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = value(node.left), value(node.right)
            if isinstance(node.op, ast.Add): out = a + b
            elif isinstance(node.op, ast.Sub): out = a - b
            elif isinstance(node.op, ast.Mult): out = a * b
            else: out = a / b
        else:
            raise ValueError('invalid numeric expression')
        if not math.isfinite(out): raise ValueError('non-finite value')
        return out
    return value(tree.body)


class Text:
    def __init__(self, text):
        self.text = str(text)
        self.cursor = len(self.text)
        self.anchor = 0
        self.undo = []
        self.redo = []

    @property
    def selection(self):
        return tuple(sorted((self.anchor, self.cursor)))

    @property
    def selected(self):
        a, b = self.selection
        return self.text[a:b]

    def snapshot(self):
        return self.text, self.cursor, self.anchor

    def replace(self, text):
        text = str(text).replace('\r', ' ').replace('\n', ' ')
        a, b = self.selection
        if len(self.text) - (b-a) + len(text) > 512:
            raise ValueError('expression too long')
        self.undo.append(self.snapshot())
        self.undo = self.undo[-100:]
        self.redo.clear()
        self.text = self.text[:a] + text + self.text[b:]
        self.cursor = self.anchor = a + len(text)

    def move(self, direction, shift=False, word=False):
        a, b = self.selection
        if not shift and a != b and not word:
            target = a if direction < 0 else b
        else:
            target = self.cursor
            if word:
                if direction < 0:
                    while target > 0 and self.text[target-1].isspace(): target -= 1
                    while target > 0 and not self.text[target-1].isspace(): target -= 1
                else:
                    while target < len(self.text) and not self.text[target].isspace(): target += 1
                    while target < len(self.text) and self.text[target].isspace(): target += 1
            else:
                target = max(0, min(len(self.text), target + direction))
        self.cursor = target
        if not shift: self.anchor = target

    def edge(self, end, shift=False):
        self.cursor = len(self.text) if end else 0
        if not shift: self.anchor = self.cursor

    def remove(self, back=True, word=False):
        if self.anchor == self.cursor:
            self.move(-1 if back else 1, shift=True, word=word)
        if self.anchor != self.cursor: self.replace('')

    def history(self, redo=False):
        source, target = (self.redo, self.undo) if redo else (self.undo, self.redo)
        if source:
            target.append(self.snapshot())
            self.text, self.cursor, self.anchor = source.pop()
