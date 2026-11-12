# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture repeated Native buffer pairs without changing their operation order."""
import warp as wp


@wp.kernel
def _decrement(counter: wp.array[int]):
    counter[0] = counter[0] - 1


def capture(substep_a, substep_b, substeps):
    counter = wp.zeros(1, dtype=int)

    def pair():
        for _ in range(2):
            substep_a()
            substep_b()
        wp.launch(_decrement, dim=1, inputs=[counter])

    with wp.ScopedCapture() as frame:
        counter.fill_(substeps // 2)
        wp.capture_while(counter, pair)
    # Keep the control buffer alive for every replay of the captured loop.
    return frame.graph, counter


