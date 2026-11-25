# SPDX-License-Identifier: GPL-3.0-or-later
"""core/gpu_failure.py: a run that failed building its kernels on an old NVIDIA driver asks for a driver update.
Run: python tests/test_gpu_failure.py (no Blender, no GPU)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core'))
import gpu_failure  # noqa: E402

START_OLD = ('Warp 1.17.0 initialized:\n   CUDA Toolkit 12.9, Driver 12.2\n   Devices:\n'
             '     "cuda:0"   : "NVIDIA GeForce RTX 3080 Laptop GPU" (16 GiB, sm_86, mempool enabled)\n')
START_NEW = 'Warp 1.17.0 initialized:\n   CUDA Toolkit 12.9, Driver 13.3\n'
BUILD = ("ptxas error   : Entry function 'k_cuda_kernel_forward' uses too much shared data (0x10400 bytes, 0xc000 max)\n"
         'Traceback (most recent call last):\nException: CUDA kernel build failed with error code 6\n')
PYTHON_ERROR = 'Traceback (most recent call last):\nValueError: the pattern has no pieces\n'


def write(d, name, text):
    p = os.path.join(d, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


def main():
    with tempfile.TemporaryDirectory() as d:
        # the tester's case: start line in the stage's stdout, the ptxas line in the job's console
        r = gpu_failure.diagnose([write(d, 'a.txt', START_OLD + BUILD[-80:]), write(d, 'b.txt', BUILD)])
        assert r == {'driver': (12, 2), 'toolkit': (12, 9), 'old_driver': True, 'no_gpu': False}, r
        assert gpu_failure.version_text(r['driver']) == '12.2'
        # a build failure on a driver as new as the toolkit: GPU, but not an old driver
        r = gpu_failure.diagnose([write(d, 'c.txt', START_NEW + BUILD)])
        assert r is not None and r['old_driver'] is False, r
        # a build failure without Warp's start line: GPU, versions unknown
        r = gpu_failure.diagnose([write(d, 'd.txt', BUILD)])
        assert r == {'driver': None, 'toolkit': None, 'old_driver': False, 'no_gpu': False}, r
        # the tester's second case: a new driver whose PTX JIT fails to load the module
        jit = ('Warp CUDA error 218: a PTX JIT compilation failed\n'
               "Exception: Failed to load CUDA module 'm' (device='cuda:0', block_dim=256)\n")
        start_jit = 'Warp 1.17.0 initialized:\n   CUDA Toolkit 12.9, Driver 13.5\n'
        r = gpu_failure.diagnose([write(d, 'g.txt', start_jit + jit)])
        assert r == {'driver': (13, 5), 'toolkit': (12, 9), 'old_driver': False, 'no_gpu': False}, r
        # no CUDA device (no driver, or too old for Warp): the solver stops instead of running on the CPU
        nogpu = ('Warp 1.17.0 initialized:\n   CUDA Toolkit 12.9, CUDA driver not available (NVRTC compilation '
                 'available)\n   Devices:\n     "cpu" : "x"\nAC9: no CUDA device: the solver does not run on the CPU\n')
        r = gpu_failure.diagnose([write(d, 'h.txt', nogpu)])
        assert r == {'driver': None, 'toolkit': (12, 9), 'old_driver': False, 'no_gpu': True}, r
        # Warp's start line alone (no stop line) is not a failure
        assert gpu_failure.diagnose([write(d, 'i.txt', nogpu.split('AC9:')[0])]) is None
        # an old driver but a failure that is not the GPU's: no GPU message
        assert gpu_failure.diagnose([write(d, 'e.txt', START_OLD + PYTHON_ERROR)]) is None
        # missing files and None are skipped
        assert gpu_failure.diagnose([os.path.join(d, 'missing.txt'), None]) is None
        # a long console: the start line at the top is still read
        r = gpu_failure.diagnose([write(d, 'f.txt', START_OLD + 'x' * (gpu_failure.TAIL + 10) + '\n' + BUILD)])
        assert r is not None and r['old_driver'] is True, r
        # the GPU has no memory left: Warp's allocation error, or CUDA's own words
        oom = START_NEW + "Traceback (most recent call last):\nRuntimeError: Failed to allocate 536870912 bytes on device 'cuda:0'\n"
        assert gpu_failure.out_of_memory([write(d, 'm.txt', oom)])
        assert gpu_failure.out_of_memory([write(d, 'n.txt', 'Warp CUDA error 2: out of memory\n')])
        # not out of memory: a build failure, a Python error, the graph-capture pool message, missing files
        assert not gpu_failure.out_of_memory([write(d, 'o.txt', START_OLD + BUILD), write(d, 'p.txt', PYTHON_ERROR)])
        assert not gpu_failure.out_of_memory([write(d, 'q.txt', 'Failed to allocate memory during graph capture '
                                                               'because memory pools are not enabled\n')])
        assert not gpu_failure.out_of_memory([os.path.join(d, 'missing.txt'), None])
    print('ok test_gpu_failure')


if __name__ == '__main__':
    main()
