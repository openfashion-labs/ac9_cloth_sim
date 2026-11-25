#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CI dependency preparation; one pin table, no imports of the solver or GPU libraries."""
import argparse
import ast
import hashlib
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile
from distribution_spec import PINS


def verify(source, directory, prepare_manifest=False):
    paths = sorted(p.name for p in directory.glob('*.whl'))
    if paths != sorted(PINS):
        raise ValueError('Expected exactly the two pinned wheels')
    for name,pin in PINS.items():
        with (directory/name).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=pin:
                raise ValueError('Wheel hash mismatch: '+name)
    manifest_path = source/'blender_manifest.toml'
    text = manifest_path.read_text(encoding='utf8')
    data = tomllib.loads(text)
    wheels = ['./wheels/'+name for name in PINS]
    if 'wheels' not in data and prepare_manifest:
        import json
        insert = '\nwheels = ['+', '.join(json.dumps(p) for p in wheels)+']\n'
        if 'platforms' not in data:
            insert += 'platforms = ["windows-x64"]\n'
        table = text.find('\n[')
        text = text[:table]+insert+text[table:] if table>=0 else text+insert
        data = tomllib.loads(text)
        manifest_path.write_text(text,encoding='utf8')
    if set(data.get('wheels',[]))!=set(wheels) or data.get('platforms')!=['windows-x64']:
        raise ValueError('The source manifest and pinned wheels disagree')
    table = next(ast.literal_eval(n.value) for n in ast.parse((source/'solver/newton_vbd.py').read_text(encoding='utf8')).body
                 if isinstance(n,ast.Assign) and any(getattr(t,'id',None)=='NEWTON_BASE_SHA256' for t in n.targets))
    with zipfile.ZipFile(directory/'newton-1.6.0-py3-none-any.whl') as archive:
        for name,accepted in table.items():
            if hashlib.sha256(archive.read('newton/_src/solvers/vbd/'+name)).hexdigest() not in accepted:
                raise ValueError('Newton does not match the source patch: '+name)
            if not (source/'solver/newton_patch'/name).is_file():
                raise ValueError('A patched Newton source file is missing')
    return {'wheels_checked':len(PINS),'newton_originals_checked':len(table)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--source',type=Path,default=Path('.'))
    p.add_argument('--check-only',action='store_true')
    p.add_argument('--prepare-manifest',action='store_true',help='Prepare missing wheel fields in a disposable CI checkout')
    a=p.parse_args()
    if not a.check_only:
        a.out.mkdir(parents=True,exist_ok=True)
        packages=[name.split('-')[0].replace('_','-')+'=='+name.split('-')[1] for name in PINS]
        subprocess.run([sys.executable,'-m','pip','download','--no-deps','--only-binary',':all:',
                        '--platform','win_amd64','--python-version','3.13','--implementation','cp',
                        '-d',str(a.out),*packages],check=True)
    print(verify(a.source,a.out,a.prepare_manifest))


if __name__=='__main__':main()
