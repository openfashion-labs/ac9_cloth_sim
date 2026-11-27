"""The mesh prewarm after a pattern edit (dress.prewarm_build): one background thread at a time. A newer edit stops
the older thread and waits for it to end (it is counted as running meanwhile, so build_ahead does not make the mesh on
the main thread); a stopping thread's key is not "started". Runs without Blender: the prewarm functions are taken
from dress.py and run against a stand-in mesher."""
import ast
from pathlib import Path
import threading
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('_PREWARM', '_PENDING_S', '_prewarm_running', 'prewarm_give_up', 'prewarm_build', '_prewarmed')


class Stopped(Exception):
    pass


def load(build):
    tree = ast.parse((ROOT / 'dress.py').read_text(encoding='utf8'))
    keep = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in NAMES:
            keep.append(n)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NAMES for t in n.targets):
            keep.append(n)
    ns = dict(
        _BUILD_CACHE=[], _build_key=lambda spec, h: repr((spec, h)), mesh_cache_dir=lambda c: None,
        bpy=types.SimpleNamespace(context=None),
        pattern_build=types.SimpleNamespace(build=build, CACHE_DIR=None, Stopped=Stopped))
    exec(compile(ast.Module(body=keep, type_ignores=[]), 'dress.py', 'exec'), ns)
    assert all(k in ns for k in NAMES), [k for k in NAMES if k not in ns]
    return ns


class SlowMesher:
    """A build that takes `steps` x 10 ms and looks at its stop each step; counts the threads in it at once."""

    def __init__(self, steps=30):
        self.steps, self.inside, self.most, self.ended = steps, 0, 0, []
        self.lock = threading.Lock()

    def __call__(self, spec, h, stop=None):
        with self.lock:
            self.inside += 1; self.most = max(self.most, self.inside)
        try:
            for _ in range(self.steps):
                if stop is not None and stop.is_set():
                    self.ended.append(('stopped', spec)); raise Stopped()
                time.sleep(0.01)
            self.ended.append(('made', spec))
            return ('mesh', spec)
        finally:
            with self.lock:
                self.inside -= 1


class PrewarmOneThread(unittest.TestCase):
    def setUp(self):
        self.M = SlowMesher()
        self.D = load(self.M)

    def tearDown(self):
        t = self.D['_PREWARM']['thread']
        if t is not None:
            t.join(2)

    def test_a_newer_edit_waits_for_the_older_thread_to_stop(self):
        D = self.D
        self.assertTrue(D['prewarm_build']('a', 4.0))
        time.sleep(0.03)
        self.assertFalse(D['prewarm_build']('b', 4.0))          # a is told to stop; b not started beside it
        self.assertTrue(D['_prewarm_running']())                # counted as running while b waits
        t0 = time.monotonic()
        while not D['prewarm_build']('b', 4.0):
            self.assertLess(time.monotonic() - t0, 1.0)
            time.sleep(0.005)
        self.assertEqual(D['_prewarmed'](repr(('b', 4.0))), ('mesh', 'b'))
        self.assertEqual(self.M.most, 1)
        self.assertEqual(self.M.ended, [('stopped', 'a'), ('made', 'b')])
        self.assertFalse(D['_prewarm_running']())

    def test_a_stopping_threads_key_is_not_started(self):
        D = self.D
        D['prewarm_build']('a', 4.0); time.sleep(0.03)
        D['prewarm_build']('b', 4.0)                            # stops a
        t0 = time.monotonic()
        while not D['prewarm_build']('a', 4.0):                 # back to a (an undo): made again, not taken as started
            self.assertLess(time.monotonic() - t0, 1.0)
            time.sleep(0.005)
        self.assertEqual(D['_prewarmed'](repr(('a', 4.0))), ('mesh', 'a'))

    def test_the_waiting_mark_ends(self):
        D = self.D
        D['prewarm_build']('a', 4.0); time.sleep(0.03)
        D['prewarm_build']('b', 4.0)
        self.assertIsNone(D['_prewarmed'](repr(('b', 4.0))))    # the press came first: it makes b itself
        self.assertIsNone(D['_PREWARM']['pending'])
        D['prewarm_build']('c', 4.0)
        D['prewarm_give_up']()                                  # the timer stopped trying
        self.assertIsNone(D['_PREWARM']['pending'])
        D['_PREWARM']['pending'] = time.monotonic() - D['_PENDING_S'] - 1   # a timer lost on a file load
        D['_PREWARM']['thread'].join(2)
        self.assertFalse(D['_prewarm_running']())


if __name__ == '__main__':
    unittest.main()
