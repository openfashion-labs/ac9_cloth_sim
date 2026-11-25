# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only staging with Blender's wheel manager. Never writes the live site-packages."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
import os
import shutil
import time


def cleanup_owned(root, spec):
    """Delete only a verified private job; keep its owner until deletion succeeds."""
    expected=Path(spec['extensions']).resolve()/'.ac9_runtime_repair'
    if (root.parent!=expected or root.name!=spec['token'] or len(root.name)!=32
            or any(c not in '0123456789abcdef' for c in root.name)
            or root.is_symlink() or root.is_junction()
            or (root/'session.cookie').exists()
            or json.loads((root/'owner.json').read_text())!={'token':root.name}):
        raise ValueError('Unexpected runtime cleanup owner')
    journal=root/'publication.json'
    if journal.exists() and json.loads(journal.read_text())['state'] not in {'verified','rolled_back'}:
        raise ValueError('Unverified runtime publication must be retained')
    for attempt in range(8):
        try:
            stage=root/'stage'
            if stage.exists():
                if stage.is_symlink() or stage.is_junction() or any(p.is_symlink() or p.is_junction() for p in stage.rglob('*')):
                    raise ValueError('Unexpected links in private runtime staging')
                shutil.rmtree(stage)
            for p in root.iterdir():
                if p.name=='owner.json':continue
                if p.is_dir() or p.is_symlink() or p.is_junction():
                    raise ValueError('Unexpected private runtime cleanup entry')
                p.unlink()
            # Preserve the owner if rmdir encounters a transient sharing error.
            owner=root/'owner.json';owner.unlink()
            try:root.rmdir()
            except OSError:
                owner.write_text(json.dumps({'token':root.name}),encoding='utf8')
                raise
            return
        except OSError as error:
            if getattr(error,'winerror',None) not in {5,32,33,145} or attempt==7:raise
            time.sleep(.1*(attempt+1))


def check_modules(roots, expected):
    code = ('import sys,json,importlib,importlib.metadata as m;sys.path[:0]=json.loads(sys.argv[1]);'
            'modules={n:importlib.import_module(n) for n in ("warp","newton")};'
            'print(json.dumps({"modules":{"warp":m.version("warp-lang"),"newton":m.version("newton")},'
            '"paths":{n:x.__file__ for n,x in modules.items()}}))')
    run = subprocess.run([sys.executable, '-I', '-B', '-c', code, json.dumps(roots)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise RuntimeError('The runtime could not be loaded by a new process')
    row = json.loads(run.stdout.strip().splitlines()[-1])
    if row['modules'] != expected:
        raise RuntimeError('The runtime versions do not match this build')
    allowed = {Path(p).resolve() for p in roots}
    if any(Path(p).resolve().parent.parent not in allowed for p in row['paths'].values()):
        raise RuntimeError('A different runtime location was imported')
    return row


def main(path):
    request = Path(path)
    spec = json.loads(request.read_text(encoding='utf8'))
    root = request.parent.resolve()
    if json.loads((root/'owner.json').read_text()) != {'token': spec['token']}:
        raise ValueError('Invalid repair owner')
    output = root/'receipt.json'
    row = {'token': spec['token'], 'ok': False}
    def save():
        temp = root/'receipt.tmp'
        temp.write_text(json.dumps(row), encoding='utf8')
        temp.replace(output)
    try:
        # The worker and its read-only validation child belong to the parent process tree.
        sys.path.insert(0, str(Path(__file__).parent/'solver'))
        from process_tree import join_owned_job
        join_owned_job()
        if spec['mode']=='cleanup':
            cleanup_owned(root,spec)
            return 0
        live_site = Path(spec['site']).resolve()
        if spec['mode'] == 'check':
            sys.path.insert(0,spec['blender_modules'])
            from _bpy_internal.extensions import wheel_manager as wm
            skipped=wm.wheel_list_deduplicate_as_skip_set(spec['wheel_list'])
            seen=set();verified_entries=set()
            # A fresh full content check also makes interrupted publication recoverable
            # through Blender's standard installer, without trusting a leftover version string.
            for _key,paths in spec['wheel_list']:
                for p in paths:
                    name=Path(p).name
                    if p in skipped:continue
                    if name in spec['wheel_pins']:
                        with open(p,'rb') as stream:
                            if hashlib.file_digest(stream,'sha256').hexdigest()!=spec['wheel_pins'][name]:
                                raise RuntimeError('The runtime wheel hash does not match this build')
                        seen.add(name)
                    with __import__('zipfile').ZipFile(p) as archive:
                        errors=[]
                        data_map=wm._wheel_zipfile_normalize(archive,error_fn=lambda e:errors.append(str(e))) or {}
                        if errors:raise RuntimeError('An enabled extension has an unreadable wheel')
                        for info in archive.infolist():
                            member=info.filename
                            if member.endswith('/'):continue
                            target=(live_site/member).resolve()
                            if not target.is_relative_to(live_site) or not target.is_file():
                                raise RuntimeError('Installed runtime files are incomplete')
                            with target.open('rb') as stream:
                                if hashlib.file_digest(stream,'sha256').digest()!=hashlib.sha256(data_map.get(member,archive.read(info))).digest():
                                    raise RuntimeError('Installed runtime files do not match the pinned wheels')
                            verified_entries.add(Path(member).parts[0])
            if seen!=set(spec['wheel_pins']):
                raise RuntimeError('The enabled wheel set does not contain every pinned runtime wheel')
            row['verified_entries']=sorted(verified_entries)
            row['validation'] = check_modules([str(live_site)], spec['expected_modules'])
            # Recover only owned, dead-session work. Keep its pending journal until
            # the parent rechecks the extension snapshot under repository locks.
            from runtime_guard import pending_journals
            allowed={str(p):p for p in pending_journals(spec['extensions'])}
            for directory in spec.get('cleanup_roots',[]):
                old=allowed.get(directory)
                if old is None or (old/'session.cookie').exists():
                    raise RuntimeError('Another extension operation is in progress')
                journal=json.loads((old/'publication.json').read_text(encoding='utf8'))
                if not set(journal.get('identities',{})).issubset(verified_entries):
                    raise RuntimeError('Interrupted runtime files have not all been verified')
                stage=old/'stage'
                if stage.exists():
                    if stage.is_symlink() or stage.is_junction() or any(p.is_symlink() or p.is_junction() for p in stage.rglob('*')):
                        raise RuntimeError('Unexpected links in the staged runtime')
                    __import__('shutil').rmtree(stage)
        elif spec['mode'] == 'stage':
            sys.path.insert(0, spec['blender_modules'])
            from _bpy_internal.extensions import wheel_manager as wm
            wheel_list = spec['wheel_list']
            skipped = wm.wheel_list_deduplicate_as_skip_set(wheel_list)
            installed, _unknown = wm._wheels_from_dir(str(live_site))
            needed = []
            for key, paths in wheel_list:
                selected = []
                for p in paths:
                    if p in skipped:
                        continue
                    info = wm._wheel_info_dir_from_zip(p)
                    if info is None:
                        raise RuntimeError('An enabled extension has an unreadable wheel')
                    if info[0] not in installed:
                        selected.append(p)
                if selected:
                    needed.append((key, selected))
            # Pin verification runs off the UI thread, before any extraction.
            pins = spec['wheel_pins']
            seen = set()
            for _key, paths in wheel_list:
                for p in paths:
                    name = Path(p).name
                    if name in pins:
                        with open(p, 'rb') as stream:
                            if hashlib.file_digest(stream, 'sha256').hexdigest() != pins[name]:
                                raise RuntimeError('The runtime wheel hash does not match this build')
                        seen.add(name)
            if seen != set(pins):
                raise RuntimeError('The runtime wheels are unavailable locally')
            stage_local = root/'stage'/'.local'
            stage_site = stage_local/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
            if stage_local.exists():
                raise RuntimeError('Repair staging directory already exists')
            stage_site.mkdir(parents=True)
            errors = []
            wm.apply_action(local_dir=str(stage_local), local_dir_site_packages=str(stage_site),
                            wheel_list=needed, error_fn=lambda e: errors.append(str(e)),
                            remove_error_fn=lambda p,e: errors.append(str(e)), debug=False)
            if errors:
                raise RuntimeError('Blender could not stage the runtime wheels')
            # Wheels with files outside site-packages need the standard installer instead.
            if any(p.is_file() and not p.is_relative_to(stage_site) for p in stage_local.rglob('*')):
                raise RuntimeError('This wheel layout requires the standard installer')
            row['validation'] = check_modules([str(stage_site), str(live_site)], spec['expected_modules'])
            entries = sorted(p.name for p in stage_site.iterdir())
            if any(p.is_symlink() or p.is_junction() for p in stage_site.rglob('*')):
                raise RuntimeError('Unexpected links in the staged runtime')
            row.update(stage_site=str(stage_site), entries=entries,
                       staged_wheels=sum(len(paths) for _key,paths in needed))
        else:
            raise ValueError('Unknown repair operation')
        row['ok'] = True
    except Exception as error:
        row.update(message=str(error), detail=traceback.format_exc())
        if spec['mode']=='cleanup':
            row.update(errno=getattr(error,'errno',None),winerror=getattr(error,'winerror',None))
            (root/'cleanup_error.json').write_text(json.dumps(row),encoding='utf8')
            return 1
    save()
    return 0 if row['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
