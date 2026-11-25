"""The mesher run on a background thread (a pattern edit's mesh made before the press) gives the same arrays as on
the main thread, also while both threads build at once through the shared cache."""
import importlib, sys, threading, types, unittest
from pathlib import Path
import numpy as np

package = types.ModuleType('_pattern_threads_core')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'core')]
sys.modules[package.__name__] = package
PB = importlib.import_module(package.__name__ + '.pattern_build')


def rect(name, x, w=180., height=240.):
    q = [[x, 0.], [x + w, 0.], [x + w, height], [x, height]]
    return {'name': name, 'edges': [{'id': str(i), 'pts': [q[i], q[(i + 1) % 4]]} for i in range(4)]}


def spec(w=180.):
    side = lambda n: {'piece': n, 'from': ['0', 0.], 'to': ['0', 1.], 'dir': 1}
    return {'name': 'threads', 'pieces': [rect('A', 0, w), rect('B', 300, w)],
            'seams': [{'id': 's', 'a': side('A'), 'b': side('B'), 'enabled': True}]}


def arrays(pat):
    return {k: v for k, v in pat.items() if isinstance(v, np.ndarray)}


class ThreadedBuildTests(unittest.TestCase):
    def setUp(self):
        PB.CACHE_DIR = None
        PB._INTERIOR.clear()

    def assertSame(self, a, b):
        a, b = arrays(a), arrays(b)
        self.assertEqual(sorted(a), sorted(b))
        for k in a:
            self.assertTrue(np.array_equal(a[k], b[k]), k)

    def test_background_build_equals_main(self):
        main, _ = PB.build(spec(), 6.)
        PB._INTERIOR.clear()
        out = {}
        t = threading.Thread(target=lambda: out.setdefault('pat', PB.build(spec(), 6.)[0]))
        t.start(); t.join()
        self.assertSame(main, out['pat'])

    def test_two_threads_at_once(self):
        ref = {w: PB.build(spec(w), 6.)[0] for w in (170., 190.)}
        PB._INTERIOR.clear()
        out = {}
        ts = [threading.Thread(target=lambda w=w: out.setdefault(w, PB.build(spec(w), 6.)[0])) for w in (170., 190.)]
        ts.append(threading.Thread(target=lambda: out.setdefault('again', PB.build(spec(170.), 6.)[0])))
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertSame(ref[170.], out[170.])
        self.assertSame(ref[190.], out[190.])
        self.assertSame(ref[170.], out['again'])


if __name__ == '__main__':
    unittest.main()
