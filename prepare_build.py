# SPDX-License-Identifier: GPL-3.0-or-later
"""Preparation-owned resource lifetime. Unknown/existing directories are never cleanup targets."""
import os
from pathlib import Path
import shutil
import uuid
import functools
import inspect
import stat
from collections import OrderedDict

_ROOTS = OrderedDict()


def _directory_identity(path):
    value=path.stat()
    if not stat.S_ISDIR(value.st_mode) or value.st_ino<=0 or value.st_dev<=0:
        raise ValueError('Directory identity is not usable for caching')
    return value.st_dev,value.st_ino


def _allocation_root(root):
    """Cache canonical allocation roots only; deletion always resolves afresh."""
    requested=Path(os.path.abspath(root))
    key=os.path.normcase(str(requested))
    cached=_ROOTS.get(key)
    if cached is not None:
        canonical,identity=cached
        try:
            if _directory_identity(requested)==identity and _directory_identity(canonical)==identity:
                _ROOTS.move_to_end(key)
                return canonical
        except (OSError,ValueError):pass
        _ROOTS.pop(key,None)
    canonical=requested.resolve()
    canonical.mkdir(parents=True,exist_ok=True)
    try:
        identity=_directory_identity(canonical)
        if _directory_identity(requested)==identity:
            _ROOTS[key]=(canonical,identity)
            while len(_ROOTS)>16:_ROOTS.popitem(last=False)
    except (OSError,ValueError):pass
    return canonical


def prepared_consumer(function):
    """A standalone consumer owns a scope; a child receives the caller's explicit preparation owner."""
    context_index = list(inspect.signature(function).parameters).index('context')
    @functools.wraps(function)
    def call(*args, **kwargs):
        if kwargs.get('preparation') is not None:
            return function(*args, **kwargs)
        context = kwargs.get('context', args[context_index] if len(args) > context_index else None)
        from . import dress
        with dress.preparation(context) as owner:
            result = function(*args, **dict(kwargs, preparation=owner))
            if args:
                import bpy
                if isinstance(args[0], bpy.types.Operator) and result == {'CANCELLED'}:owner.cancel()
        if owner.canceled and args:
            import bpy
            if isinstance(args[0], bpy.types.Operator):return {'CANCELLED'}
        return result
    if function.__name__ == 'execute' and context_index == 1:
        @functools.wraps(function)
        def operator_call(self, context, **kwargs):
            return call(self, context, **kwargs)
        return operator_call  # Blender validates co_argcount == 2, not inspect.signature/wraps metadata.
    return call


def replan_source(owner):
    if owner is not None:owner.replan_source()


def cancel(owner):
    if owner is not None:owner.cancel()


def refuse(owner, reason):
    """A normal domain refusal is as terminal as an exception for the operation's pending preparation."""
    return owner.refuse(reason) if owner is not None else (False, reason)


def operation(context, *, owner=None, consumer_backup=None):
    from . import dress
    return dress.preparation(context, owner=owner, consumer_backup=consumer_backup)


def modifier_values(modifier):
    """Writable Mesh Cache RNA values; copied arrays do not retain borrowed RNA views."""
    return {prop.identifier: (tuple(getattr(modifier, prop.identifier)) if getattr(prop, 'is_array', False)
                              else getattr(modifier, prop.identifier))
            for prop in modifier.bl_rna.properties if not prop.is_readonly
            and prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}}


class ObstacleBackup:
    """Short-lived commit rollback, not another user history. Original ID and modifier order are restored."""
    def __init__(self, plan):
        import bpy
        self.plan, self.original_mesh = plan, plan.object.data
        self.edit_frame = ('ac9_edit_frame' in plan.object, plan.object.get('ac9_edit_frame'))
        self.files = {}
        if not plan.shared:
            for name in ('final.npy', 'final.json'):
                path = Path(plan.job) / name
                self.files[path] = path.read_bytes() if path.exists() else None
        self.modifiers = [(index, modifier_values(m)) for index, m in enumerate(plan.object.modifiers)
                          if m.type == 'MESH_CACHE']
        self.active_modifier = next((m.name for m in plan.object.modifiers if m.is_active), None)
        self.mesh = plan.object.data.copy()
        self.mesh.use_fake_user = False
        from . import placed_view
        for key in (placed_view.KEEP_PROP, placed_view.BEFORE_SEAM_PROP):
            self.mesh.pop(key, None)

    def rollback(self):
        import bpy
        import numpy as np
        ob = self.plan.object
        if ob.session_uid != self.plan.object_uid:
            raise RuntimeError('The obstacle owner changed during preparation rollback')
        changed_mesh = ob.data
        ob.data = self.original_mesh
        coordinates = np.empty(len(self.mesh.vertices) * 3, np.float32)
        self.mesh.vertices.foreach_get('co', coordinates)
        ob.data.vertices.foreach_set('co', coordinates)
        ob.data.update()
        for m in list(ob.modifiers):
            if m.type == 'MESH_CACHE':
                ob.modifiers.remove(m)
        pinned = []
        for index, values in self.modifiers:
            modifier = ob.modifiers.new(values['name'], 'MESH_CACHE')
            for key, value in values.items():
                if key not in {'is_active', 'use_pin_to_last'}:
                    setattr(modifier, key, value)
            ob.modifiers.move(len(ob.modifiers) - 1, index)
            pinned.append((modifier, values.get('use_pin_to_last', False)))
        for modifier, value in pinned:
            modifier.use_pin_to_last = value
        active = ob.modifiers.get(self.active_modifier) if self.active_modifier else None
        if active is not None:
            active.is_active = True
        had, value = self.edit_frame
        if had:ob['ac9_edit_frame'] = value
        else:ob.pop('ac9_edit_frame', None)
        for path, content in self.files.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                temporary = path.with_name(path.name + '.restore_' + uuid.uuid4().hex)
                try:
                    temporary.write_bytes(content)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        if changed_mesh != self.original_mesh and changed_mesh.users == 0:
            bpy.data.meshes.remove(changed_mesh)

    def close(self):
        import bpy
        if self.mesh.users == 0:
            bpy.data.meshes.remove(self.mesh)


class OwnedJobDirectory(str):
    """A newly, exclusively allocated job directory and its live filesystem identity."""
    @classmethod
    def create(cls, root, name):
        root = _allocation_root(root)
        candidate = root / name
        while True:
            try:
                candidate.mkdir()
                break
            except FileExistsError:
                candidate = root / (name + '_' + uuid.uuid4().hex[:12])
        own = cls(str(candidate))
        own.root = root
        own.identity = (candidate.stat().st_dev, candidate.stat().st_ino)
        own.token = uuid.uuid4().hex
        own.marker = candidate / '.ac9_prepare_owner'
        claimed = False
        try:
            with own.marker.open('x', encoding='ascii') as marker:
                claimed = True
                marker.write(own.token)
        except Exception:
            if claimed:
                own.marker.unlink(missing_ok=True)
            try:candidate.rmdir()
            except OSError:pass
            raise
        return own

    def matches(self):
        path = Path(self)
        try:
            resolved=path.resolve()
            value=path.stat()
            return (resolved.parent == self.root and resolved == self.root / path.name
                    and (value.st_dev, value.st_ino) == self.identity
                    and self.marker.read_text(encoding='ascii') == self.token)
        except (OSError, UnicodeError):
            return False

    def remove(self):
        # Check the absolute resolved target, its named job root, identity, and ownership before recursive deletion.
        if self.matches():
            shutil.rmtree(str(Path(self).resolve()))

    def release(self):
        if self.matches():
            self.marker.unlink()


class PreparationBuild:
    def __init__(self, context, allocator):
        self.context, self.allocator = context, allocator
        self.directories = []
        self.source = self.migration = self.owner = None
        self.obstacles = {}
        self.cleanup_errors = []
        self.accepted = None
        self.committed_owner = None

    def job_dir(self):
        path = self.allocator(self.context)
        if isinstance(path, OwnedJobDirectory):
            self.directories.append(path)
        return path

    def sibling(self, path, suffix):
        path = Path(path)
        own = OwnedJobDirectory.create(path.parent, path.name + '_' + suffix)
        self.directories.append(own)
        return own

    def stage_migration(self, spec, owner):
        if self.migration is not None:
            from .core import spec as S
            if S.dumps(self.migration) != S.dumps(spec):
                raise ValueError('Preparation has conflicting compatibility plans; replan the operation')
        self.migration, self.owner = spec, owner

    def bind_source(self, spec, owner):
        if self.owner is not None:
            self.owner.require(self.context)
            return
        self.source, self.owner = spec, owner

    def stage_obstacle(self, plan):
        if plan.object_uid in self.obstacles:
            import numpy as np
            old = self.obstacles[plan.object_uid]
            old.require_current()
            if not np.array_equal(old.positions, plan.positions):
                raise ValueError('An obstacle result changed between preparation plans')
        self.obstacles.setdefault(plan.object_uid, plan)

    def source_spec(self):
        return self.migration if self.migration is not None else self.source

    def finish(self, result):
        from . import store, dress
        accepted = os.path.normcase(os.path.abspath(result[0]['dir'])) if isinstance(result, tuple) else None
        for directory in self.directories:
            if accepted == os.path.normcase(os.path.abspath(directory)) and not directory.matches():
                raise ValueError('The new job directory changed during preparation; existing results were kept')
        if self.owner is not None:
            self.owner.require(self.context)
        for plan in self.obstacles.values():
            plan.require_current()
        backups, applied, queue = [], [], list(dress._DROP_LATER)
        result_cache = dict(dress._RESULT_CACHE)
        try:
            # Capture every owner before the first retirement. A backup failure has changed no existing owner.
            for plan in self.obstacles.values():
                backups.append(ObstacleBackup(plan))
            for plan, backup in zip(self.obstacles.values(), backups):
                applied.append(backup)
                dress.apply_frame_drop(plan, later=True)
            if self.migration is not None:
                # Preserve store's saved-versus-display-failed contract; successful persistence is not rolled back.
                self.committed_owner, _ = store.commit(self.context, self.migration, owner=self.owner)
            self.accepted = accepted
        except Exception as original:
            rollback_errors = []
            for backup in reversed(applied):
                try:backup.rollback()
                except Exception as exc:rollback_errors.append(str(exc))
            dress._DROP_LATER[:] = queue
            dress._RESULT_CACHE.clear()
            dress._RESULT_CACHE.update(result_cache)
            if rollback_errors:
                raise RuntimeError('Preparation failed; obstacle restoration failed: ' + '; '.join(rollback_errors)) from original
            raise
        finally:
            for backup in backups:
                try:backup.close()
                except Exception as exc:self.cleanup_errors.append(str(exc))
        if isinstance(result, tuple):
            result[0]['dir'] = str(result[0]['dir'])  # Handoff exposes a path, not the preparation's private ownership.
        for directory in self.directories:
            if self.accepted == os.path.normcase(os.path.abspath(directory)):
                try:directory.release()
                except OSError as exc:self.cleanup_errors.append(str(exc))
            else:
                self._remove(directory)
        if self.cleanup_errors:
            note = 'Prepared; temporary resource cleanup failed: ' + '; '.join(self.cleanup_errors)
            info = result[0] if isinstance(result, tuple) else result
            info.setdefault('report' if isinstance(result, tuple) else 'rep', []).append(note)
            try:self.context.scene.ac9_cloth_sim.dress_status = note
            except Exception:pass  # The returned result still carries the cleanup diagnosis.
        return result

    def abort(self):
        for directory in reversed(self.directories):
            if self.accepted != os.path.normcase(os.path.abspath(directory)):
                self._remove(directory)

    def _remove(self, directory):
        try:
            directory.remove()
        except OSError as exc:
            # A locked owned temporary file must not replace the original prepare failure or undo a saved spec.
            self.cleanup_errors.append(str(exc))
