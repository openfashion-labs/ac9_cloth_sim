# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail closed when a context-menu declaration has no reviewed test contract.
AST inspection also sees rows inside branches the runtime fixtures cannot reach.
"""
import ast,json,sys,hashlib
from pathlib import Path


def declarations(source):
    tree=ast.parse(source);found=set()
    bindings={}
    for node in tree.body:
        if isinstance(node,ast.FunctionDef):bindings[node.name]=node
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name):bindings[target.id]=node
                elif isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id in ('_MENU_ITEMS','_MENU_ITEMS_T'):
                    found.add('TABLE_MUTATION:'+ast.dump(node,include_attributes=False))
    queue=['_MENU_ITEMS','_MENU_ITEMS_T','menu_items','numeric_row','_corner_rows','_ipoint_smooth_rows']
    seen=set()
    while queue:
        name=queue.pop()
        if name in seen or name not in bindings:continue
        seen.add(name);node=bindings[name]
        canonical=ast.dump(node,include_attributes=False)
        found.add('REVIEWED_AST:'+name+':'+hashlib.sha256(canonical.encode()).hexdigest())
        queue.extend(n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id in bindings)
    for node in tree.body:
        declared=any(node is bindings.get(name) for name in seen)
        if not declared and any(isinstance(n,ast.Name) and n.id in ('_MENU_ITEMS','_MENU_ITEMS_T') for n in ast.walk(node)):
            canonical=ast.dump(node,include_attributes=False)
            found.add('EXTERNAL_TABLE_REFERENCE:'+hashlib.sha256(canonical.encode()).hexdigest())
    for node in ast.walk(tree):
        if not isinstance(node,(ast.FunctionDef,ast.Assign)):continue
        if isinstance(node,ast.Assign):
            names=[getattr(x,'id','') for x in node.targets]
            if not any(x in ('_MENU_ITEMS','_MENU_ITEMS_T') for x in names):continue
            owner=names[0]
        else:
            if node.name not in ('menu_items','numeric_row','_corner_rows','_ipoint_smooth_rows'):continue
            owner=node.name
            # A new row-producing helper or computed operator must be reviewed even
            # when its branch is not visited by any current fixture.
            for call in ast.walk(node):
                if isinstance(call,ast.Call):
                    found.add(owner+':CALL:'+ast.dump(call.func,include_attributes=False))
                if isinstance(call,ast.Constant) and isinstance(call.value,str) and call.value.startswith(('ac9_cloth_sim.','transform.')):
                    found.add(owner+':OP:'+call.value)
        for row in ast.walk(node):
            if not isinstance(row,ast.Tuple) or len(row.elts) not in (3,4):continue
            op=row.elts[1]
            # Literal operators are visible even in unvisited conditions. Nonliteral operators
            # in these row constructors require a deliberate extension of this extractor.
            is_op=isinstance(op,ast.Constant) and isinstance(op.value,str) and ('.' in op.value or op.value.startswith('MENU:'))
            if is_op or isinstance(op,ast.Name) and op.id=='STATE_MENU':
                descriptor=ast.Tuple(elts=row.elts[1:],ctx=ast.Load())
                found.add(owner+':'+ast.dump(descriptor,include_attributes=False))
    return sorted(found)


def check(source,contracts):
    actual=set(declarations(source));expected=set(contracts['declarations'])
    return {'unreviewed':sorted(actual-expected),'stale':sorted(expected-actual)}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    path=Path(sys.argv[1]) if len(sys.argv)>1 else root/'draft_tool.py'
    result=check(path.read_text(encoding='utf-8'),json.loads((root/'tests/context_contracts.json').read_text(encoding='utf-8')))
    print(json.dumps(result))
    sys.exit(1 if any(result.values()) else 0)
