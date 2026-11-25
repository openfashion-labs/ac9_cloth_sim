# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU preflight in the actual solver interpreter, before importing GPU libraries."""
import ast
import hashlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import re


def pending_journals(extensions):
    base=Path(extensions).resolve()/'.ac9_runtime_repair'
    if not base.is_dir():return []
    site=Path(extensions).resolve()/'.local'/'lib'/f'python{__import__("sys").version_info.major}.{__import__("sys").version_info.minor}'/'site-packages'
    result=[]
    for root in base.iterdir():
        if not re.fullmatch('[0-9a-f]{32}',root.name) or not root.is_dir() or root.is_symlink() or root.is_junction():continue
        journal=root/'publication.json'
        if not journal.exists():continue
        if json.loads((root/'owner.json').read_text(encoding='utf8'))!={'token':root.name}:
            raise RuntimeError('Unverified runtime repair journal')
        row=json.loads(journal.read_text(encoding='utf8'))
        if row.get('token')!=root.name or Path(row.get('site','')).resolve()!=site:
            raise RuntimeError('Unverified runtime repair journal')
        if row.get('state') not in ('verified','rolled_back'):result.append(root)
    return result


def before_launch(environment):
    if environment.get('AC9_RUNTIME_GUARD')!='1':return
    if any(Path(p).exists() for p in json.loads(environment.get('AC9_RUNTIME_LOCKS','[]'))):
        raise RuntimeError('Another extension operation is in progress')
    if pending_journals(environment['AC9_RUNTIME_EXTENSION_ROOT']):
        raise RuntimeError('Runtime repair must be verified before starting simulation')


def verify_actual_environment():
    root=Path(__file__).resolve().parents[1]
    path=root/'runtime_contract.json'
    if not path.is_file():return None
    before_launch(os.environ)
    expected=json.loads(path.read_text(encoding='utf8'))
    paths={}
    for name,dist in (('warp','warp-lang'),('newton','newton')):
        spec=util.find_spec(name)
        if spec is None or not spec.origin or not Path(spec.origin).is_file():
            raise RuntimeError('Simulation runtime is unavailable in the selected Python')
        origin=Path(spec.origin).resolve();base=origin.parent.parent
        matches=[d for d in metadata.distributions(path=[str(base)])
                 if d.metadata['Name'].lower().replace('_','-')==dist]
        if len(matches)!=1 or matches[0].version!=expected['modules'][name]:
            raise RuntimeError('The selected Python has a different runtime version')
        required=os.environ.get('AC9_RUNTIME_SITE')
        if required and base!=Path(required).resolve():
            raise RuntimeError('The selected Python imports a different runtime location')
        paths[name]=str(origin)
    tree=ast.parse((root/'solver/newton_vbd.py').read_text(encoding='utf8'))
    table=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
               and any(getattr(t,'id','')=='NEWTON_BASE_SHA256' for t in n.targets))
    newton=Path(paths['newton']).parent/'_src/solvers/vbd'
    for name,accepted in table.items():
        if hashlib.sha256((newton/name).read_bytes()).hexdigest() not in accepted:
            raise RuntimeError('The selected Newton does not match the application patch')
    return {'python':__import__('sys').executable,'modules':expected['modules'],'paths':paths}
