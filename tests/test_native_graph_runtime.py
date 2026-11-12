"""Buffer orientation, history order, and control reset must survive graph reuse."""
import pathlib
import sys
import unittest
import numpy as np
import warp as wp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'solver'))
import native_graph


@wp.kernel
def step(a: wp.array[float], b: wp.array[float], history: wp.array[int], sequence: wp.array[int], orientation: int):
    index = history[0]
    sequence[index] = orientation
    history[0] = index + 1
    b[0] = a[0] + 1.0


class NativeGraphTests(unittest.TestCase):
    def test_pair_replay_preserves_sequence_and_resets_counter(self):
        wp.init()
        if not wp.is_cuda_available() or not getattr(wp, 'is_conditional_graph_supported', lambda: False)():
            self.skipTest('Conditional CUDA graphs unavailable')
        for substeps in (2, 10, 40):
            with wp.ScopedDevice('cuda:0'):
                state = dict(a=wp.zeros(1), b=wp.zeros(1), history=wp.zeros(1, dtype=int),
                             sequence=wp.zeros(substeps * 3, dtype=int), orientation=0)

                def first():
                    wp.launch(step, dim=1, inputs=[state['a'], state['b'], state['history'],
                                                  state['sequence'], state['orientation']])

                def last():
                    state['a'], state['b'] = state['b'], state['a']
                    state['orientation'] = 1 - state['orientation']

                graph, counter = native_graph.capture(first, last, substeps)
                self.assertEqual(int(state['history'].numpy()[0]), 0)
                for _ in range(3):
                    wp.capture_launch(graph)
                self.assertEqual(int(state['history'].numpy()[0]), substeps * 3)
                self.assertEqual(float(state['a'].numpy()[0]), substeps * 3)
                self.assertEqual(int(counter.numpy()[0]), 0)
                np.testing.assert_array_equal(state['sequence'].numpy(), np.arange(substeps * 3) % 2)


if __name__ == '__main__':
    unittest.main()
