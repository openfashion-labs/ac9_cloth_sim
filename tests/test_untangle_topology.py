"""Topology reuse cannot retain a changed mesh or a run's dynamic history."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np

spec=importlib.util.spec_from_file_location('untangle_tables',Path(__file__).resolve().parents[1]/'solver/untangle.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class Topology(unittest.TestCase):
    def test_cached_tables_equal_fresh_and_reject_changed_faces(self):
        cache={};F=np.array([[0,1,2],[2,1,3]])
        for faces,count in ((F,4),(F,4),(F[::-1],4),(F[:,::-1],4),(F,5)):
            nb,vt=M.Untangler.topology(faces,count,cache)
            expected_nb,expected_vt=M.Untangler.topology(faces,count)
            self.assertEqual(vt,expected_vt)
            for a,b in zip(nb,expected_nb):np.testing.assert_array_equal(a,b)
        self.assertEqual(set(cache),{'untangle'})

    def test_key_keeps_a_copy_of_mutable_input(self):
        cache={};F=np.array([[0,1,2]])
        before=M.Untangler.topology(F,4,cache)
        F[0,2]=3
        after=M.Untangler.topology(F,4,cache)
        self.assertEqual(before[1],[[0],[0],[0],[]])
        self.assertEqual(after[1],[[0],[0],[],[0]])
