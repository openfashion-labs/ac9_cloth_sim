#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private prototype: generate full/main/runtime from one committed source and pin table."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib
import zipfile

from distribution_spec import PINS, RUNTIME_VERSION, MODULE_VERSIONS, RUNTIME_MAINTAINER, RUNTIME_BLENDER_VERSION_MIN


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ref', default='HEAD')
    ap.add_argument('--version', required=True)
    ap.add_argument('--wheels', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', a.version):
        ap.error('version must have three integer components')
    root = Path(__file__).resolve().parents[1]
    commit = git(root, 'rev-parse', a.ref).decode().strip()
    source = {}
    with tarfile.open(fileobj=io.BytesIO(git(root, 'archive', commit))) as archive:
        for member in archive:
            if not member.isfile() or member.name.split('/')[0] in {'tests', 'docs', 'tools', '.github'}:
                continue
            if any(part.startswith('.') for part in Path(member.name).parts):
                continue
            source[member.name] = archive.extractfile(member).read()
    if not source.get('LICENSE', b'').strip():
        raise ValueError('The source commit lacks its main LICENSE')
    source['BUILD_INFO.txt'] = f'AC9 Cloth Sim {a.version}\ncommit {commit}\nPrivate distribution prototype; not for publication.\n'.encode()
    wheels = {name: (a.wheels / name).read_bytes() for name in PINS}
    assert all(sha(data) == PINS[name] for name, data in wheels.items()), 'wheel pin mismatch'
    tree = ast.parse(source['solver/newton_vbd.py'])
    table = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, 'id', '') == 'NEWTON_BASE_SHA256' for t in n.targets))
    with zipfile.ZipFile(io.BytesIO(wheels['newton-1.6.0-py3-none-any.whl'])) as archive:
        for name, accepted in table.items():
            assert sha(archive.read('newton/_src/solvers/vbd/' + name)) in accepted
            assert 'solver/newton_patch/' + name in source
    manifest = source.pop('blender_manifest.toml').decode()
    manifest = re.sub(r'^version = ".*"$', f'version = "{a.version}"', manifest, flags=re.M)
    base = manifest.split('\n[build]')[0]
    # All license files are copied verbatim, including NVIDIA component conditions.
    licenses = {}
    for name, data in wheels.items():
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                if '/licenses/' in member and not member.endswith('/'):
                    licenses['licenses/' + name.removesuffix('.whl') + '/' + member.split('/licenses/', 1)[1]] = archive.read(member)
    notices = ('Private AC9 Cloth Sim distribution prototype; not a standalone SDK.\n'
               'The runtime is an installation component of AC9 Cloth Sim.\n'
               'Newton 1.6.0 and Warp 1.17.0 wheels are unmodified; see licenses/.\n'
               'Warp includes NVIDIA CUDA Runtime, NVRTC and libmathdx under their own licenses.\n'
               'AC9 includes modified Apache-2.0 Newton copies in solver/newton_patch/.\n'
               'They remain in the main/full add-on, not in the runtime.\n'
               'The GPL section 7 additional permission is in LICENSE.\n'
               'Not made or endorsed by NVIDIA.\n').encode()
    contract = {'runtime_id': 'ac9_cloth_sim_runtime', 'runtime_version': RUNTIME_VERSION,
                'modules': MODULE_VERSIONS, 'wheels': PINS}
    full = dict(source)
    main_files = dict(source, **{'runtime_contract.json': json.dumps(contract, indent=2).encode()})
    runtime_manifest = (f'schema_version = "1.0.0"\nid = "ac9_cloth_sim_runtime"\nversion = "{RUNTIME_VERSION}"\n'
                        'name = "AC9 Cloth Sim Runtime"\ntagline = "Install the simulation components for AC9 Cloth Sim"\n'
                        f'maintainer = {json.dumps(RUNTIME_MAINTAINER)}\ntype = "add-on"\n'
                        f'blender_version_min = {json.dumps(RUNTIME_BLENDER_VERSION_MIN)}\nlicense = ["SPDX:GPL-3.0-or-later"]\n')
    runtime = {'__init__.py': b'# SPDX-License-Identifier: GPL-3.0-or-later\ndef register(): pass\ndef unregister(): pass\n',
               'LICENSE': Path(__file__).with_name('runtime_LICENSE.txt').read_bytes(), 'runtime_contract.json': json.dumps(contract, indent=2).encode()}
    extra = '\nplatforms = ["windows-x64"]\n'
    wheel_lines = 'wheels = [' + ', '.join(json.dumps('./wheels/' + n) for n in wheels) + ']\n'
    full['blender_manifest.toml'] = (base + extra + wheel_lines).encode()
    main_files['blender_manifest.toml'] = (base + extra).encode()
    runtime['blender_manifest.toml'] = (runtime_manifest + extra + wheel_lines).encode()
    for files in (full, runtime):
        files.update({'wheels/' + n: d for n, d in wheels.items()})
    for files in (full, main_files, runtime):
        files.update(licenses)
        files['THIRD_PARTY_NOTICES.txt'] = notices
    a.out.mkdir(parents=True, exist_ok=True)
    names = {'full': f'ac9_cloth_sim-{a.version}-full.zip', 'main': f'ac9_cloth_sim-{a.version}-main.zip',
             'runtime': f'ac9_cloth_sim_runtime-{RUNTIME_VERSION}.zip'}
    assert all(not (a.out / name).exists() for name in names.values()), 'output already exists'
    result = {'source_commit': commit, 'private_prototype': True, 'wheels': PINS, 'packages': {}}
    for label, files in (('full', full), ('main', main_files), ('runtime', runtime)):
        path = a.out / names[label]
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.create_system = 3
                item.external_attr = 0o644 << 16
                archive.writestr(item, data)
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            tomllib.loads(archive.read('blender_manifest.toml').decode())
        result['packages'][label] = {'path': str(path.resolve()), 'bytes': path.stat().st_size,
                                    'sha256': sha(path.read_bytes()), 'members': len(files)}
    same = [n for n in source if full[n] == main_files[n]]
    assert len(same) == len(source)
    result['identical_main_source_members'] = len(same)
    result['identical_license_members'] = len(licenses)
    (a.out / 'build.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
