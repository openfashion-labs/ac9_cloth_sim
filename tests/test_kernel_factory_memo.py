"""The collision narrow phase's kernel factories are wrapped so the same arguments give the kernel made the first time
in the process (solver/kernel_factory_memo.py)."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
import kernel_factory_memo as KFM


class KernelFactoryMemo(unittest.TestCase):
    def module(self):
        m = types.ModuleType('narrow_phase_stub'); m.calls = []

        def create_narrow_phase_primitive_kernel(*args, **kwargs):
            m.calls.append((args, kwargs)); return object()
        m.create_narrow_phase_primitive_kernel = create_narrow_phase_primitive_kernel
        return m

    def test_same_arguments_make_one_kernel(self):
        m = self.module()
        self.assertEqual(KFM.install(m), ['create_narrow_phase_primitive_kernel'])
        a = m.create_narrow_phase_primitive_kernel(1, flag=True)
        self.assertIs(m.create_narrow_phase_primitive_kernel(1, flag=True), a)
        self.assertIsNot(m.create_narrow_phase_primitive_kernel(2, flag=True), a)
        self.assertEqual(len(m.calls), 2)

    def test_unhashable_arguments_pass_through(self):
        m = self.module(); KFM.install(m)
        m.create_narrow_phase_primitive_kernel([1]); m.create_narrow_phase_primitive_kernel([1])
        self.assertEqual(len(m.calls), 2)

    def test_installed_once(self):
        m = self.module(); KFM.install(m)
        self.assertEqual(KFM.install(m), [])


if __name__ == '__main__':
    unittest.main()
