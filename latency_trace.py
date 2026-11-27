# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in local responsiveness trace. Observer threads never call Blender APIs.

Input means entry to an AC9 callback, not the physical mouse timestamp. A draw
receipt is entry to POST_PIXEL, not a GPU fence or proof of display presentation.
"""
import functools
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import uuid

_recorder = None
_last_error = None
_draw_handle = None
_installed = []
_pending = []
_modal_noise = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER', 'TIMER_REPORT'}


class Recorder:
    def __init__(self, folder, *, stall_s=2.0, limit=8*1024*1024):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stall_s, self.limit = stall_s, limit
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.closed = False
        self.stream = None
        self.part = 0
        self.error = None
        self.main_thread = threading.get_ident()
        self.last_pulse = time.perf_counter()
        self.active = {}
        self.write('session_start', python=sys.version.split()[0])
        self.thread = threading.Thread(target=self._watch, name='AC9 latency observer', daemon=True)
        self.thread.start()

    def write(self, event, **fields):
        try:
            with self.lock:
                if self.closed:
                    return
                if self.stream is None or self.stream.tell() >= self.limit:
                    if self.stream is not None:
                        self.stream.close()
                        self.part += 1
                    # Only this session's four owned files rotate. Earlier sessions are untouched.
                    self.stream = (self.folder / f'events_{self.part % 4}.jsonl').open('w', encoding='utf-8')
                row = dict(event=event, wall_ns=time.time_ns(), monotonic_ns=time.perf_counter_ns(),
                           pid=os.getpid(), part=self.part, **fields)
                self.stream.write(json.dumps(row, ensure_ascii=True, default=str) + '\n')
                self.stream.flush()
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)

    def begin(self, label, *, durable=False, **fields):
        token = uuid.uuid4().hex
        started = time.perf_counter()
        with self.lock:
            self.active[token] = (label, started)
        if durable:
            self.write('begin', token=token, label=label, **fields)
        return token, label, started, durable, time.process_time()

    def end(self, span, *, error=None, result=None):
        token, label, started, durable, cpu_started = span
        elapsed = time.perf_counter() - started
        with self.lock:
            self.active.pop(token, None)
        if durable or elapsed >= .1 or error:
            self.write('end', token=token, label=label, seconds=elapsed,
                       process_cpu_s=time.process_time()-cpu_started, error=error, result=result)

    def pulse(self):
        now = time.perf_counter()
        gap = now - self.last_pulse
        self.last_pulse = now
        if gap >= .5:
            self.write('heartbeat_gap', seconds=gap)

    def _watch(self):
        reported = None
        while not self.stop_event.wait(.25):
            pulse = self.last_pulse
            gap = time.perf_counter() - pulse
            if gap < self.stall_s or reported == pulse:
                continue
            reported = pulse
            with self.lock:
                active = [dict(label=label, seconds=time.perf_counter()-started)
                          for label, started in self.active.values()]
            frame = sys._current_frames().get(self.main_thread)
            stack = ''.join(traceback.format_stack(frame, limit=20))[-12000:] if frame else None
            self.write('stall_sample', gap_s=gap, active=active, python_stack=stack,
                       limitation='Python stack only; observer needs GIL; not native cause proof')

    def close(self):
        self.stop_event.set()
        self.write('session_end')
        with self.lock:
            self.closed = True
            if self.stream is not None:
                try:
                    self.stream.close()
                except (OSError, ValueError) as exc:
                    self.error = str(exc)


def root():
    import bpy
    return Path(bpy.utils.user_resource('CONFIG')) / 'ac9_latency'


def allowed(context=None):
    import bpy
    from . import dress
    prefs = dress.addon_prefs(context or bpy.context)
    return bool(prefs and prefs.experimental and getattr(prefs, 'latency_recording', False))


def sync(_self=None, context=None):
    global _recorder, _draw_handle, _last_error
    if allowed(context):
        if _recorder is None:
            try:
                _last_error = None
                folder = root() / (time.strftime('%Y%m%d_%H%M%S') + '_' + str(os.getpid()) + '_' + uuid.uuid4().hex[:8])
                _recorder = Recorder(folder)
                import bpy
                _recorder.write('context', blender=bpy.app.version_string, filepath=bpy.data.filepath)
                from .solver import lifecycle_trace
                _recorder.write('related_log', lifecycle_directory=lifecycle_trace.directory())
                if not bpy.app.background:
                    if _draw_handle is None:
                        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
                    if not bpy.app.timers.is_registered(_pulse):
                        bpy.app.timers.register(_pulse, first_interval=.2, persistent=True)
            except (OSError, RuntimeError) as exc:
                _last_error = str(exc)
                stop()
    else:
        stop()


def stop():
    global _recorder, _draw_handle, _last_error
    recorder, _recorder = _recorder, None
    _pending.clear()
    try:
        if recorder is not None:
            recorder.close()
    except (OSError, ValueError) as exc:
        _last_error = str(exc)
    finally:
        import bpy
        handle, _draw_handle = _draw_handle, None
        try:
            if bpy.app.timers.is_registered(_pulse):
                bpy.app.timers.unregister(_pulse)
        finally:
            if handle is not None:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')


def wrap(fn, label, *, method=False):
    if getattr(fn, '_ac9_latency', False):
        return fn

    @functools.wraps(fn)
    def measured(*args, **kwargs):
        recorder = _recorder
        if recorder is None:
            return fn(*args, **kwargs)
        event = args[2] if method and len(args) > 2 else None
        kind = getattr(event, 'type', None)
        durable = method and kind not in _modal_noise
        span = recorder.begin(label, durable=durable, event_type=kind,
                              event_value=getattr(event, 'value', None))
        result, error = None, None
        try:
            result = fn(*args, **kwargs)
            return result
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            recorder.end(span, error=error, result=sorted(result) if isinstance(result, set) else None)
            if durable and recorder is _recorder:
                _pending.append((span[0], span[2], time.perf_counter()))
                del _pending[:-32]
    measured._ac9_latency = True
    if method:
        # Blender validates Python co_argcount when registering an Operator.
        if label.endswith('.execute'):
            @functools.wraps(fn)
            def execute(self, context):
                return measured(self, context)
            execute._ac9_latency = True
            return execute
        @functools.wraps(fn)
        def event_method(self, context, event):
            return measured(self, context, event)
        event_method._ac9_latency = True
        return event_method
    return measured


def install(modules):
    """Outside undo_guard: include preflight and existing click diagnostics in measured time."""
    import bpy
    for mod in modules:
        if mod.__name__ == __name__:
            continue
        for cls in list(vars(mod).values()):
            if not (isinstance(cls, type) and cls.__module__ == mod.__name__ and issubclass(cls, bpy.types.Operator)):
                continue
            for name in ('execute', 'invoke', 'modal'):
                fn = getattr(cls, name, None)
                if fn is not None and not getattr(fn, '_ac9_latency', False):
                    setattr(cls, name, wrap(fn, cls.bl_idname + '.' + name, method=True))
        for name in ('_draw_view', '_tick', '_start', '_prewarm_tick', '_preload_tick', '_wake_now',
                     '_startup', '_sync_all', '_wake_on_load', '_relink_jobs', '_stop_on_load',
                     'watch', '_moved'):
            fn = getattr(mod, name, None)
            if callable(fn) and getattr(fn, '__module__', None) == mod.__name__ and not getattr(fn, '_ac9_latency', False):
                new = wrap(fn, mod.__name__.rsplit('.', 1)[-1] + '.' + name)
                setattr(mod, name, new)
                _installed.append((mod, name, fn, new))


def _pulse():
    recorder = _recorder
    if recorder is not None:
        recorder.pulse()
    return .2


def _draw():
    recorder = _recorder
    if recorder is not None and _pending:
        now = time.perf_counter()
        pending, _pending[:] = list(_pending), []
        for token, start, end in pending:
            recorder.write('draw_callback', token=token, from_entry_s=now-start, from_return_s=now-end,
                           limitation='first 3D POST_PIXEL callback, not physical display presentation')


def _load_pre(*_args):
    if _recorder is not None:
        _recorder.write('load_begin')
    _pending.clear()


def _load_post(*_args):
    if _recorder is not None:
        import bpy
        _recorder.write('load_end', filepath=bpy.data.filepath)


def _startup():
    sync()
    return None


def register():
    global _draw_handle
    import bpy
    from bpy.app.handlers import persistent
    for name, fn in (('load_pre', _load_pre), ('load_post', _load_post)):
        persistent(fn)
        if fn not in getattr(bpy.app.handlers, name):
            if name == 'load_pre':
                getattr(bpy.app.handlers, name).insert(0, fn)
            else:
                getattr(bpy.app.handlers, name).append(fn)
    if not bpy.app.background:
        bpy.app.timers.register(_startup, first_interval=0)


def unregister():
    global _draw_handle
    import bpy
    stop()
    for fn in (_pulse, _startup):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    for name, fn in (('load_pre', _load_pre), ('load_post', _load_post)):
        if fn in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).remove(fn)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    for mod, name, original, wrapped in reversed(_installed):
        if getattr(mod, name, None) is wrapped:
            setattr(mod, name, original)
    _installed.clear()


def draw_settings(layout, prefs):
    if not prefs.experimental:
        return
    from .i18n import tx
    box = layout.box()
    box.prop(prefs, 'latency_recording')
    if prefs.latency_recording:
        box.label(text=tx('Local timing logs; no automatic upload'))
        if _recorder is not None:
            box.label(text=str(_recorder.folder))
            if _recorder.error:
                box.label(text=tx('Timing log write failed'), icon='ERROR')
        elif _last_error:
            box.label(text=tx('Timing log write failed'), icon='ERROR')
    box.operator('ac9_cloth_sim.open_latency_logs', icon='FILE_FOLDER')
