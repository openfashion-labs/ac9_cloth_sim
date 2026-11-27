# SPDX-License-Identifier: GPL-3.0-or-later
"""Length arithmetic with explicit unit tokens; never execute a pasted expression."""
import ast
import math
import re
import unicodedata

UNITS = {}
for factor, aliases in [
    (1e-6, ('um','µm','μm','micrometer','micrometers')),
    (.001, ('mm','millimeter','millimeters')),
    (.01, ('cm','centimeter','centimeters')),
    (1., ('m','meter','meters')),
    (1000., ('km','kilometer','kilometers')),
    (.0254, ('in','inch','inches','"')),
    (.3048, ('ft','foot','feet',"'")),
    (.0000254, ('thou','mil','mils')),
    (1609.344, ('mi','mile','miles')),
]:
    UNITS.update({name: factor for name in aliases})
NUM = re.compile(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
UNIT = re.compile(r'[a-zA-Zµμ]+|[\'\"]')


def length(expression, default_scale=.001, system='METRIC'):
    """Meters, matching Blender's selected-system unit basis and compound signs."""
    source = unicodedata.normalize('NFKC', str(expression)).strip()
    source = source.replace('−','-').replace('×','*').replace('÷','/')
    if len(source)>512: raise ValueError('expression too long')
    names, tokens, i, previous_unit, negative_chain = {}, [], 0, False, False
    while i < len(source):
        if source[i].isspace(): i+=1; continue
        match = NUM.match(source,i)
        if match:
            value = float(match.group()); i=match.end()
            if not math.isfinite(value): raise ValueError('non-finite value')
            while i<len(source) and source[i].isspace(): i+=1
            unit=UNIT.match(source,i)
            explicit=unit is not None
            if unit:
                label=unit.group().lower()
                if label not in UNITS: raise ValueError('unknown unit')
                factor=UNITS[label];i=unit.end()
            if tokens and tokens[-1] in names:
                if not (previous_unit and explicit): raise ValueError('missing operator')
                tokens.append('-' if negative_chain else '+')
            else:
                negative_chain=bool(tokens and tokens[-1]=='-')
            key='q'+str(len(names));names[key]=(value,factor if explicit else None);tokens.append(key)
            previous_unit=explicit
        elif source[i] in '+-*/()':
            tokens.append(source[i]);i+=1;previous_unit=False
        else:
            raise ValueError('invalid length expression')
    tree=ast.parse(' '.join(tokens),mode='eval')
    if len(list(ast.walk(tree)))>100: raise ValueError('expression too long')
    # The largest explicitly present unit of the selected system supplies the
    # basis. Foreign-system units convert their literals without changing it.
    metric={1e-6,.001,.01,1.,1000.}
    own=metric if system!='IMPERIAL' else set(UNITS.values())-metric
    basis=max((factor for _,factor in names.values() if factor in own),default=default_scale)
    def value(node):
        if isinstance(node,ast.Name) and node.id in names:
            numeric,factor=names[node.id]
            out=numeric*(factor/basis if factor is not None else 1)
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            out=value(node.operand)*(-1 if isinstance(node.op,ast.USub) else 1)
        elif isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=value(node.left),value(node.right)
            if isinstance(node.op,ast.Add):out=a+b
            elif isinstance(node.op,ast.Sub):out=a-b
            elif isinstance(node.op,ast.Mult):out=a*b
            else:out=a/b
        else:raise ValueError('invalid arithmetic')
        if not math.isfinite(out):raise ValueError('non-finite result')
        return out
    out=value(tree.body)*basis
    if not math.isfinite(out):raise ValueError('non-finite length')
    return out
