#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive split previews from the canonical stable full ZIP; never rebuild its main source."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tomllib
import zipfile
from distribution_spec import (PINS, MAIN_ID, RUNTIME_ID, RUNTIME_VERSION, MODULE_VERSIONS,
                               RUNTIME_MAINTAINER, RUNTIME_BLENDER_VERSION_MIN)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main_notices(canonical):
    """Replace only the full-package introduction; retain every component notice."""
    introduction = (
        b'AC9 Cloth Sim is GPL-3.0-or-later (LICENSE). This is the split main component.\n'
        b'The matching AC9 Cloth Sim Runtime extension supplies the unmodified Python wheels;\n'
        b'this main ZIP does not contain wheels/. The following notices cover those dependencies\n'
        b'and the modified Newton sources included in this main component:\n\n'
    )
    notices, count = re.subn(
        rb'\AAC9 Cloth Sim is GPL-3.0-or-later \(LICENSE\)\. It bundles these Python packages '
        rb'as wheels \(wheels/\), each\r?\nunmodified from PyPI:\r?\n\r?\n',
        introduction, canonical, count=1,
    )
    if count != 1:
        raise ValueError('Canonical third-party notice introduction is missing or unrecognized')
    return notices


def verify_newton(source, wheels):
    tree = ast.parse(source['solver/newton_vbd.py'])
    table = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(getattr(target, 'id', '') == 'NEWTON_BASE_SHA256' for target in node.targets))
    with zipfile.ZipFile(io.BytesIO(wheels['newton-1.6.0-py3-none-any.whl'])) as archive:
        for name, accepted in table.items():
            if sha(archive.read('newton/_src/solvers/vbd/'+name)) not in accepted:
                raise ValueError('The canonical Newton wheel does not match the source patch')
            if 'solver/newton_patch/'+name not in source:
                raise ValueError('The canonical full ZIP lacks its Newton patch')


def write_zip(path, members):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in sorted(members.items()):
            item = zipfile.ZipInfo(name, date_time=(2026,1,1,0,0,0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.create_system = 3
            item.external_attr = 0o644 << 16
            archive.writestr(item, data)


def split(full, out):
    with zipfile.ZipFile(full) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 2_000_000_000:
            raise ValueError('Unexpected canonical archive layout')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe path in the canonical archive')
        if archive.testzip() is not None:
            raise ValueError('The canonical full ZIP has an invalid CRC')
        source = {name:archive.read(name) for name in names if not name.endswith('/')}
    if not source.get('LICENSE', b'').strip():
        raise ValueError('The canonical full ZIP lacks its main LICENSE')
    manifest = tomllib.loads(source['blender_manifest.toml'].decode())
    version = manifest['version']
    if manifest['id'] != MAIN_ID or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?', version):
        raise ValueError('Unexpected canonical add-on ID or version')
    expected_paths = {'./wheels/'+name for name in PINS}
    if set(manifest.get('wheels',[])) != expected_paths:
        raise ValueError('Canonical manifest wheels differ from the single pin table')
    wheel_paths = {name for name in source if name.startswith('wheels/')}
    if wheel_paths != {p.removeprefix('./') for p in expected_paths}:
        raise ValueError('Canonical archive wheels differ from the manifest')
    wheels = {name:source['wheels/'+name] for name in PINS}
    if any(sha(data) != PINS[name] for name,data in wheels.items()):
        raise ValueError('Canonical wheel hash mismatch')
    verify_newton(source, wheels)
    if 'runtime_contract.json' in source:
        raise ValueError('The canonical input must be the standalone full distribution')
    contract = json.dumps({'runtime_id':RUNTIME_ID,'runtime_version':RUNTIME_VERSION,
                           'modules':MODULE_VERSIONS,'wheels':PINS}, indent=2).encode()
    main = {name:data for name,data in source.items() if not name.startswith('wheels/')}
    text, count = re.subn(r'(?m)^wheels\s*=\s*\[[^\]]*\][ \t]*(?:#[^\n]*)?(?:\r?\n)?', '',
                         source['blender_manifest.toml'].decode(), count=1)
    if count != 1 or 'wheels' in tomllib.loads(text):
        raise ValueError('Cannot remove the canonical wheel declaration')
    main['blender_manifest.toml'] = text.encode()
    main['runtime_contract.json'] = contract
    main['THIRD_PARTY_NOTICES.txt'] = main_notices(source.get('THIRD_PARTY_NOTICES.txt', b''))
    runtime = {'__init__.py':b'# SPDX-License-Identifier: GPL-3.0-or-later\ndef register(): pass\ndef unregister(): pass\n',
               'LICENSE':Path(__file__).with_name('runtime_LICENSE.txt').read_bytes(),'runtime_contract.json':contract}
    for name,data in wheels.items():
        runtime['wheels/'+name] = data
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                if '/licenses/' in member and not member.endswith('/'):
                    runtime['licenses/'+name.removesuffix('.whl')+'/'+member.split('/licenses/',1)[1]] = archive.read(member)
    runtime['THIRD_PARTY_NOTICES.txt'] = (
        'AC9 Cloth Sim runtime installation component; not a standalone SDK.\n'
        'Newton 1.6.0 and Warp 1.17.0 wheels are unmodified; see licenses/ and wheel metadata.\n'
        'Warp includes CUDA Runtime, NVRTC and libmathdx under NVIDIA licenses.\n'
        'The GPL section 7 additional permission is in LICENSE.\n'
        'AC9 modified Newton sources remain in the main application.\n'
        'Not made or endorsed by NVIDIA.\n').encode()
    runtime['blender_manifest.toml'] = (
        f'schema_version = "1.0.0"\nid = "{RUNTIME_ID}"\nversion = "{RUNTIME_VERSION}"\n'
        'name = "AC9 Cloth Sim Runtime"\ntagline = "Install the simulation components for AC9 Cloth Sim"\n'
        f'maintainer = {json.dumps(RUNTIME_MAINTAINER)}\ntype = "add-on"\n'
        f'blender_version_min = {json.dumps(RUNTIME_BLENDER_VERSION_MIN)}\n'
        'license = ["SPDX:GPL-3.0-or-later"]\nplatforms = ["windows-x64"]\n'
        'wheels = ['+', '.join(json.dumps('./wheels/'+n) for n in PINS)+']\n').encode()
    out.mkdir(parents=True, exist_ok=False)
    (out/'full').mkdir();(out/'repository').mkdir()
    paths = {'full':out/'full'/f'{MAIN_ID}-{version}-full.zip',
             'main':out/'repository'/f'{MAIN_ID}-{version}.zip',
             'runtime':out/'repository'/f'{RUNTIME_ID}-{RUNTIME_VERSION}.zip'}
    shutil.copyfile(full, paths['full'])  # Byte-for-byte canonical full ZIP, including tests, seals and assets.
    write_zip(paths['main'],main);write_zip(paths['runtime'],runtime)
    common = [name for name in main if name not in {'blender_manifest.toml','runtime_contract.json','THIRD_PARTY_NOTICES.txt'}]
    if not all(main[name] == source[name] for name in common):
        raise ValueError('Main source differs from the canonical full ZIP')
    result = {'canonical_full_sha256':sha(full.read_bytes()),'full_copy_sha256':sha(paths['full'].read_bytes()),
              'main_version':version,'runtime_version':RUNTIME_VERSION,'wheels':PINS,
              'common_members_identical':len(common),'code_members_identical':sum(n.endswith('.py') for n in common),
              'preserved_test_members':sum(n.startswith('tests/') for n in main),'packages':{}}
    for label,path in paths.items():
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:raise ValueError('Generated archive CRC mismatch')
        result['packages'][label] = {'path':str(path.resolve()),'bytes':path.stat().st_size,'sha256':sha(path.read_bytes())}
    (out/'build.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(split(args.full,args.out),indent=2))


if __name__ == '__main__':
    main()
