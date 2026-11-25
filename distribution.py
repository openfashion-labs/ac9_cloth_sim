# SPDX-License-Identifier: GPL-3.0-or-later
"""Split-release integration. No downloader; Blender owns package installation."""
import importlib
import importlib.util
import json
import subprocess
import sys
import platform
import time
from pathlib import Path

RUNTIME_ID = 'ac9_cloth_sim_runtime'
_REPAIR = globals().get('_REPAIR')
_VERIFY_PENDING = globals().get('_VERIFY_PENDING',False)
_INSTALLING = globals().get('_INSTALLING',False)
_INSTALL_TICK = globals().get('_INSTALL_TICK')
_REPAIR_TICK = globals().get('_REPAIR_TICK')


def contract():
    path = Path(__file__).with_name('runtime_contract.json')
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else None


def dependencies_present():
    # A cached ModuleSpec can outlive files removed by Blender's wheel sync.
    for name in ('warp', 'newton'):
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin or not Path(spec.origin).is_file():
            return False
    return True


def own_runtime(context):
    """Only the runtime in this main extension's repository is eligible."""
    parts = __package__.split('.')
    if len(parts) != 3 or parts[0] != 'bl_ext':
        raise RuntimeError('Split installation requires an extension repository')
    module = '.'.join(parts[:2] + [RUNTIME_ID])
    # Standard installer indices exclude disabled and invalid repositories.
    from bl_pkg import repo_paths_or_none
    eligible = [repo for repo in context.preferences.extensions.repos
                if repo.enabled and repo_paths_or_none(repo)[0] is not None]
    matches = [(i, repo) for i, repo in enumerate(eligible) if repo.module == parts[1]]
    if len(matches) != 1:
        raise RuntimeError('The extension repository is unavailable')
    return module, matches[0][0]


def runtime_enabled(context):
    module, _ = own_runtime(context)
    return module in context.preferences.addons


def request_install(context):
    import bpy
    global _REPAIR, _VERIFY_PENDING, _INSTALLING, _INSTALL_TICK
    if not contract():
        raise RuntimeError('This build does not use a separate runtime')
    if not bpy.app.online_access:
        raise RuntimeError('Allow Online Access in Blender Preferences to install the runtime')
    if not _workers_released():
        raise RuntimeError('Finish simulation work before changing the runtime')
    if _REPAIR is not None:
        if _REPAIR.busy:
            raise RuntimeError('Runtime repair is already in progress')
        cleaned=_REPAIR.cleanup()
        if not cleaned and not (_REPAIR.published and _REPAIR.process_poll() is not None):
            raise RuntimeError('Runtime repair is already in progress')
        _VERIFY_PENDING=_VERIFY_PENDING or (_REPAIR.published and _REPAIR.state!='ready')
        _REPAIR = None
    _, index = own_runtime(context)
    _INSTALLING=True
    try:
        result=bpy.ops.extensions.package_install('INVOKE_DEFAULT', repo_index=index,
                                               pkg_id=RUNTIME_ID, enable_on_install=True)
    except BaseException:
        _INSTALLING=False
        raise
    if result=={'CANCELLED'}:
        _INSTALLING=False
        return result
    started=[False]
    deadline=time.monotonic()+300
    def monitor():
        global _INSTALLING
        from bl_pkg.bl_extension_utils import repo_lock_directory_query
        if not _INSTALLING:return None
        if started[0]:
            if _REPAIR_TICK is not None:_REPAIR_TICK()
            if _REPAIR is not None and _REPAIR.busy:return .2
            _INSTALLING=False
            return None
        if any(repo_lock_directory_query(r.directory,bpy.app.tempdir) is not None
               for r in bpy.context.preferences.extensions.repos if r.enabled and (r.use_remote_url or r.source!='SYSTEM')):
            if time.monotonic()<deadline:return .2
        try:
            if not dependencies_present():raise RuntimeError('Runtime repair failed')
            begin_repair(bpy.context,check_only=True)
            started[0]=True
            return .2
        except (OSError,RuntimeError,ValueError):
            _INSTALLING=False
            globals()['_VERIFY_PENDING']=True
            return None
    _INSTALL_TICK=monitor
    bpy.app.timers.register(monitor,first_interval=.2,persistent=True)
    return result


def restore_local(context):
    """Explicit repair only; caller must first release every owned worker."""
    import bpy
    if not contract():
        raise RuntimeError('This build does not use a separate runtime')
    from . import dress
    if (dress.AC9_OT_SimDress.running() or dress._LIVE_SESSION is not None
            or dress._STANDBY.get('proc') is not None or dress._PREPARED.candidate is not None
            or dress.retired_running()):
        raise RuntimeError('Release the owned simulation workers before repairing the runtime')
    module, _ = own_runtime(context)
    if module not in context.preferences.addons:
        raise RuntimeError('The runtime is disabled; enable it in Blender Preferences')
    result = bpy.ops.preferences.addon_disable(module=module)
    if result != {'FINISHED'}:
        raise RuntimeError('Blender could not disable the runtime')
    result = bpy.ops.preferences.addon_enable(module=module)
    importlib.invalidate_caches()
    if result != {'FINISHED'} or not dependencies_present():
        raise RuntimeError('Runtime repair failed')
    return validate_child(context)


def _workers_released():
    from . import dress
    return not (dress.AC9_OT_SimDress.running() or dress._LIVE_SESSION is not None
                or dress._STANDBY.get('proc') is not None or dress._PREPARED.candidate is not None
                or dress.retired_running())


def _repair_snapshot(context):
    import addon_utils
    import bpy
    from _bpy_internal.extensions import wheel_manager
    expected = contract()
    module, _ = own_runtime(context)
    if module not in context.preferences.addons:
        raise RuntimeError('The runtime is disabled; enable it in Blender Preferences')
    if not _workers_released():
        raise RuntimeError('Release the owned simulation workers before repairing the runtime')
    carrier = importlib.import_module(module)
    installed = json.loads(Path(carrier.__file__).with_name('runtime_contract.json').read_text(encoding='utf8'))
    if installed != expected:
        raise RuntimeError('The installed runtime does not match this build')
    enabled = sorted(a.module for a in context.preferences.addons if a.module.startswith('bl_ext.'))
    ids = {tuple(name.removeprefix('bl_ext.').split('.')) for name in enabled}
    if any(len(pair) != 2 for pair in ids):
        raise RuntimeError('Unexpected extension module identifier')
    wheels = []
    data = addon_utils._extension_compat_cache_create(
        (bpy.app.version, platform.system(), platform.machine(), sys.version_info[:2], 0), ids, wheels, None)
    if data['incompatible'] or any(not row[2] for row in data['filesystem']):
        raise RuntimeError('An enabled extension manifest is unavailable or incompatible')
    extension_root = Path(bpy.utils.user_resource('EXTENSIONS')).resolve()
    site = extension_root/'.local'/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    stamps = sorted((str(Path(p).resolve()), Path(p).stat().st_size, Path(p).stat().st_mtime_ns)
                    for _key,paths in wheels for p in paths)
    wheels = sorted((key, sorted(paths)) for key,paths in wheels)
    return {'extensions':str(extension_root), 'site':str(site), 'wheel_list':wheels,
            'wheel_pins':expected['wheels'], 'expected_modules':expected['modules'],
            'enabled':enabled, 'filesystem':sorted(data['filesystem']), 'wheel_stamps':stamps,
            'blender_modules':str(Path(wheel_manager.__file__).resolve().parents[2]),
            'repos':sorted(str(Path(r.directory).resolve()) for r in context.preferences.extensions.repos
                           if r.enabled and Path(r.directory).is_dir()
                           and (r.use_remote_url or r.source != 'SYSTEM'))}


def begin_repair(context,*,check_only=False):
    import bpy
    from bl_pkg.bl_extension_utils import RepoLockContext
    from .runtime_repair import RepairJob
    from .solver.runtime_guard import pending_journals
    global _REPAIR, _VERIFY_PENDING, _REPAIR_TICK
    if _REPAIR is not None and _REPAIR.busy:
        raise RuntimeError('Runtime repair is already in progress')
    snapshot = _repair_snapshot(context)
    pending=pending_journals(snapshot['extensions'])
    pending_records={str(root):(root/'publication.json').read_text(encoding='utf8') for root in pending}
    if any((root/'session.cookie').exists() for root in pending):
        raise RuntimeError('Another extension operation is in progress')
    def guard(before):
        if _repair_snapshot(bpy.context) != before:
            raise RuntimeError('Extension settings changed while repair was preparing')
    def lock_factory(cookie):
        return RepoLockContext(repo_directories=snapshot['repos'], cookie=str(cookie/'session.cookie'))
    _VERIFY_PENDING=bool(pending)
    job = RepairJob(snapshot, guard, lock_factory,check_only=check_only or bool(pending),
                    cleanup_roots=[str(root) for root in pending])
    _REPAIR = job
    finished=[False]
    handled=[False]
    def tick():
        if finished[0]:return None
        job.poll()
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'PREFERENCES':
                    area.tag_redraw()
        if job.busy:
            return .2
        if not handled[0] and job.published and job.state!='ready':globals()['_VERIFY_PENDING']=True
        if not handled[0] and job.state=='ready':
            try:
                with lock_factory(job.root) as locks:
                    if any(error is not None for error in locks.values()):raise RuntimeError('Another extension operation is in progress')
                    guard(snapshot)
                    for root in pending_journals(snapshot['extensions']):
                        if (root/'session.cookie').exists():raise RuntimeError('Another extension operation is in progress')
                        if pending_records.get(str(root))!=(root/'publication.json').read_text(encoding='utf8'):
                            raise RuntimeError('Interrupted runtime records changed during verification')
                        path=root/'publication.json';row=json.loads(path.read_text(encoding='utf8'))
                        if not set(row.get('identities',{})).issubset(job.verified_entries):
                            raise RuntimeError('Interrupted runtime files have not all been verified')
                        row['state']='verified'
                        temp=root/'publication.tmp';temp.write_text(json.dumps(row),encoding='utf8');temp.replace(path)
                        # The verification child already removed the large owned staging tree.
                        # Keep main-thread cleanup bounded to this job's small receipt files.
                        if any(p.is_dir() or p.is_symlink() for p in root.iterdir()):
                            raise RuntimeError('Temporary runtime files could not be cleaned up')
                        for p in root.iterdir():p.unlink()
                        root.rmdir()
                globals()['_VERIFY_PENDING']=False
            except (OSError,ValueError,RuntimeError) as error:
                job.state='failed';job.message=str(error)
        handled[0]=True
        try:
            if not job.cleanup():return .2
        except OSError as error:
            job.cleanup_error={'message':str(error),'errno':error.errno,
                               'winerror':getattr(error,'winerror',None),'root':str(job.root)}
            job.cleanup_attempts=getattr(job,'cleanup_attempts',0)+1
            if job.cleanup_attempts<8:return min(.2*job.cleanup_attempts,1.0)
        except ValueError as error:
            # Refuse deletion without changing an already completed cancellation.
            job.cleanup_error={'message':str(error),'root':str(job.root)}
        finished[0]=True
        return None
    _REPAIR_TICK=tick
    bpy.app.timers.register(tick, first_interval=.2, persistent=True)
    return job


def worker_blocked():
    return _CONTRACT_ENABLED and (_INSTALLING or _VERIFY_PENDING or (_REPAIR is not None and
        (_REPAIR.busy or _REPAIR.state=='failed' or (_REPAIR.published and _REPAIR.validation is None))))


def runtime_environment(python, environment):
    if not _CONTRACT_ENABLED:return environment
    if worker_blocked():raise RuntimeError('Runtime repair must be verified before starting simulation')
    import bpy
    from bl_pkg.bl_extension_utils import REPO_LOCAL_PRIVATE_DIR,REPO_LOCAL_PRIVATE_LOCK
    locks=[str(Path(r.directory)/REPO_LOCAL_PRIVATE_DIR/REPO_LOCAL_PRIVATE_LOCK)
           for r in bpy.context.preferences.extensions.repos if r.enabled and (r.use_remote_url or r.source!='SYSTEM')]
    env=dict(environment,AC9_RUNTIME_GUARD='1',AC9_RUNTIME_EXTENSION_ROOT=bpy.utils.user_resource('EXTENSIONS'),
             AC9_RUNTIME_LOCKS=json.dumps(locks))
    if __import__('os').path.normcase(__import__('os').path.abspath(python))==__import__('os').path.normcase(__import__('os').path.abspath(sys.executable)):
        env['AC9_RUNTIME_SITE']=str(Path(bpy.utils.user_resource('EXTENSIONS')).resolve()/'.local'/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages')
    else:
        env.pop('AC9_RUNTIME_SITE',None)
    return env


def launch_error():
    if not _CONTRACT_ENABLED:
        return None
    if _INSTALLING:return 'The runtime is being prepared; please wait'
    if _VERIFY_PENDING:
        return 'Runtime repair must be verified before starting simulation'
    if _REPAIR is not None and _REPAIR.busy:
        return 'The runtime is being prepared; please wait'
    if _REPAIR is not None and _REPAIR.state == 'failed':
        return 'Runtime repair failed; use Blender\'s installer'
    if not dependencies_present():
        return 'Simulation runtime is unavailable; open add-on preferences'
    # Press-time validation only. UI draw/poll never imports packages or scans metadata.
    try:
        import bpy
        from importlib.metadata import version
        from bl_pkg.bl_extension_utils import repo_lock_directory_query
        for repo in bpy.context.preferences.extensions.repos:
            if repo.enabled and (repo.use_remote_url or repo.source != 'SYSTEM'):
                if repo_lock_directory_query(repo.directory, bpy.app.tempdir) is not None:
                    return 'Another extension operation is in progress'
        module, _ = own_runtime(bpy.context)
        if module not in bpy.context.preferences.addons:
            return 'The runtime is disabled; enable it in Blender Preferences'
        carrier = importlib.import_module(module)
        installed = json.loads(Path(carrier.__file__).with_name('runtime_contract.json').read_text(encoding='utf8'))
        expected = contract()
        if installed != expected or {'warp':version('warp-lang'),'newton':version('newton')} != expected['modules']:
            return 'The installed runtime does not match this build'
        root = Path(bpy.utils.user_resource('EXTENSIONS')).resolve()/'.local'/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
        if any(Path(importlib.util.find_spec(n).origin).resolve().parent.parent != root for n in expected['modules']):
            return 'A different runtime location was imported'
    except (ImportError, OSError, ValueError, RuntimeError):
        return 'Simulation runtime is unavailable; open add-on preferences'
    return None


def validate_child(context):
    """Check the installed carrier and imports in a new interpreter, never in UI draw/poll."""
    expected = contract()
    module, _ = own_runtime(context)
    carrier = importlib.import_module(module)
    installed = json.loads(Path(carrier.__file__).with_name('runtime_contract.json').read_text(encoding='utf8'))
    if installed != expected:
        raise RuntimeError('The installed runtime does not match this build')
    roots = list(dict.fromkeys(str(Path(importlib.util.find_spec(name).origin).parent.parent)
                              for name in expected['modules']))
    code = ('import sys,json,importlib,importlib.metadata as metadata;'
            'sys.path[:0]=json.loads(sys.argv[1]);'
            'modules={name:importlib.import_module(name) for name in ("warp","newton")};'
            'print(json.dumps({"modules":{"warp":metadata.version("warp-lang"),'
            '"newton":metadata.version("newton")},"paths":{name:m.__file__ for name,m in modules.items()}}))')
    run = subprocess.run([sys.executable, '-I', '-B', '-c', code, json.dumps(roots)],
                         capture_output=True, text=True, timeout=60,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if run.returncode:
        raise RuntimeError('The repaired runtime could not be imported by a new worker')
    receipt = json.loads(run.stdout.strip().splitlines()[-1])
    if receipt['modules'] != expected['modules']:
        raise RuntimeError('The repaired runtime versions do not match this build')
    if any(str(Path(path).parent.parent) not in roots for path in receipt['paths'].values()):
        raise RuntimeError('The worker imported a different runtime location')
    return receipt


def draw(layout, context):
    from .i18n import CTX
    if not _CONTRACT_ENABLED:
        return
    box = layout.box()
    box.label(text='Simulation Runtime', text_ctxt=CTX)
    box.label(text='Runtime setup', text_ctxt=CTX)
    if _VERIFY_PENDING:
        from .i18n import tx,wrap
        for line in wrap(tx('Runtime repair must be verified before starting simulation'),42):box.label(text=line,translate=False)
    if _REPAIR is not None:
        messages = {'staging':'Preparing runtime...', 'validating':'Checking runtime...',
                    'cancelling':'Cancelling runtime repair...', 'cancelled':'Runtime repair cancelled',
                    'failed':"Runtime repair failed; use Blender's installer", 'ready':'Runtime ready'}
        from .i18n import tx, wrap
        for line in wrap(tx(messages[_REPAIR.state]), 42):
            box.label(text=line, translate=False)
    if _INSTALLING and _REPAIR is None:
        box.label(text='Preparing runtime...',text_ctxt=CTX)
    if _INSTALLING or (_REPAIR is not None and _REPAIR.busy):
        if _REPAIR is not None and _REPAIR.cancellable:box.operator('ac9_cloth.runtime_cancel')
    else:
        box.operator('ac9_cloth.runtime_install')
        box.operator('ac9_cloth.runtime_repair')


import bpy


def _report_failure(operator, error):
    from .i18n import rpt
    from .i18n_ja import JA, JA_CTX
    message = str(error)
    if message not in JA and message not in JA_CTX:
        message = 'Runtime operation could not start; check add-on preferences'
    operator.report({'ERROR'}, rpt(message))


class AC9_OT_RuntimeInstall(bpy.types.Operator):
    """Install the simulation runtime using Blender's extension installer"""
    bl_idname = 'ac9_cloth.runtime_install'
    bl_label = 'Install Runtime'

    @classmethod
    def poll(cls, context):
        # No disk scans, imports, network or dependency repair during UI polling.
        return contract_enabled() and not _INSTALLING and not (_REPAIR is not None and _REPAIR.busy)

    def execute(self, context):
        from .i18n import rpt
        try:
            result = request_install(context)
            return {'CANCELLED'} if result == {'CANCELLED'} else {'FINISHED'}
        except (RuntimeError, ValueError) as error:
            _report_failure(self, error)
            return {'CANCELLED'}


class AC9_OT_RuntimeRepair(bpy.types.Operator):
    """Restore missing runtime files from the installed wheels without downloading"""
    bl_idname = 'ac9_cloth.runtime_repair'
    bl_label = 'Repair Runtime'

    @classmethod
    def poll(cls, context):
        return contract_enabled() and not _INSTALLING and not (_REPAIR is not None and _REPAIR.busy)

    def execute(self, context):
        from .i18n import rpt
        try:
            begin_repair(context)
            return {'FINISHED'}
        except (OSError, RuntimeError, ValueError) as error:
            _report_failure(self, error)
            return {'CANCELLED'}


class AC9_OT_RuntimeCancel(bpy.types.Operator):
    """Cancel runtime preparation before it replaces any live files"""
    bl_idname = 'ac9_cloth.runtime_cancel'
    bl_label = 'Cancel Repair'

    @classmethod
    def poll(cls, context):
        return _REPAIR is not None and _REPAIR.cancellable

    def execute(self, context):
        _REPAIR.cancel()
        return {'FINISHED'}


_CONTRACT_ENABLED = False


def contract_enabled():
    return _CONTRACT_ENABLED


def register():
    global _CONTRACT_ENABLED, _VERIFY_PENDING
    _CONTRACT_ENABLED = contract() is not None
    if _CONTRACT_ENABLED:
        from .solver.runtime_guard import pending_journals
        try:_VERIFY_PENDING=bool(pending_journals(bpy.utils.user_resource('EXTENSIONS')))
        except (OSError,ValueError,RuntimeError):_VERIFY_PENDING=True
    from .i18n import CTX
    for cls in (AC9_OT_RuntimeInstall, AC9_OT_RuntimeRepair, AC9_OT_RuntimeCancel):
        cls.bl_translation_context = CTX
    bpy.utils.register_class(AC9_OT_RuntimeInstall)
    bpy.utils.register_class(AC9_OT_RuntimeRepair)
    bpy.utils.register_class(AC9_OT_RuntimeCancel)


def unregister():
    # Staging writes only the owned scratch directory. The existing timer retains
    # this job through disable/reload until its process tree exits and cleanup is safe.
    if _REPAIR is not None and _REPAIR.busy:
        _REPAIR.cancel()
    bpy.utils.unregister_class(AC9_OT_RuntimeCancel)
    bpy.utils.unregister_class(AC9_OT_RuntimeRepair)
    bpy.utils.unregister_class(AC9_OT_RuntimeInstall)
