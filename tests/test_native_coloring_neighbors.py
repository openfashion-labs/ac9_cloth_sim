"""Regression for legacy set insertion history and host-only coloring/search contracts."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'solver'))
from native_coloring_neighbors import ColoringNeighbors


class Array:
    def __init__(self, values, **kwargs): self.values = np.asarray(values).copy()
    def numpy(self): return self.values.copy()
    def assign(self, values): self.values[:] = values
    @property
    def size(self): return self.values.size


class ColoringNeighborTests(unittest.TestCase):
    def test_compact_rollback_keeps_legacy_path_after_nested_failures(self):
        with patch.dict(sys.modules, {'warp': NS(array=Array)}):
            module=importlib.import_module('native_experimental_coloring')
        graph=[{1,2,3},{0,2,3},{0,1,3},{0,1,2},set()]
        initial=np.arange(5,dtype=np.int32)
        colors,path=module.compact(graph,initial)
        self.assertEqual(colors.tolist(),[0,3,2,1,0])
        self.assertEqual(path,[dict(from_colors=5,to_colors=4,steps=1,unresolved=0),
                               dict(from_colors=4,to_colors=4,steps=21,unresolved=1)])
        self.assertEqual(initial.tolist(),list(range(5)))

    def test_order_replays_duplicate_updates_and_hash_collisions(self):
        for stride in (1, 8, 32, 64):
            count = 15*stride+1
            rows = [np.array([[0, stride, 2*stride], [0, 3*stride, 4*stride], [0, stride, 2*stride]]),
                    np.array([[-1, 0, 6*stride, 7*stride], [0, 8*stride, 9*stride, 10*stride]]),
                    np.array([[0, 14*stride], [14*stride, 13*stride]])]
            followers = np.array([14*stride]);supports = np.array([[11*stride, 12*stride, 13*stride]])
            active = np.setdiff1d(np.arange(count), followers)
            support = {int(followers[0]): supports[0].tolist()}
            reference = [set() for _ in range(count)]
            for block in rows:
                for row in block:
                    ids = set()
                    for value in row:
                        if value >= 0: ids.update(support.get(int(value), [int(value)]))
                    ids.intersection_update(set(map(int, active)))
                    for value in ids: reference[value].update(ids-{value})
            graph = ColoringNeighbors(rows, count, active, followers, supports)
            self.assertEqual(len(graph._ordered), 0)
            for v in range(count):
                self.assertEqual(len(graph[v]), len(reference[v]))
                self.assertEqual(graph.fast(v), sorted(reference[v]))
            self.assertEqual(len(graph._ordered), 0)
            for v in range(count): self.assertEqual(list(graph[v]), list(reference[v]))

    def test_candidate_colors_and_failed_search_path_match_legacy_branch(self):
        with patch.dict(sys.modules, {'warp': NS(array=Array)}):
            module = importlib.import_module('native_experimental_coloring')
        model = NS(particle_count=7, tri_count=4, edge_count=2, spring_count=3, tet_count=0,
            device='cpu-adapter',particle_colors=Array([0,1,2,3,4,5,-1]),
            particle_color_groups=[Array([i]) for i in range(6)],
            tri_indices=Array([[0,1,2],[1,2,3],[3,4,5],[6,0,1]]),
            edge_indices=Array([[0,3,1,2],[1,4,2,3]]),spring_indices=Array([[0,5],[2,4],[3,5]]))
        bound=NS(vertices=Array([6]),triangles=Array([[0,1,2]]))
        old=module.ColoringPlan(model,bound,array_neighbors=False,reduce_steps=200)
        new=module.ColoringPlan(model,bound,array_neighbors=True,reduce_steps=200)
        self.assertEqual(old.colors.tobytes(),new.colors.tobytes())
        self.assertEqual({k:v for k,v in old.summary.items() if k!='build_s'},
                         {k:v for k,v in new.summary.items() if k!='build_s'})

    def test_search_input_errors_keep_type_and_text(self):
        with patch.dict(sys.modules, {'warp': NS(array=Array)}):
            module=importlib.import_module('native_three_automatic')
        for colors,message in ((np.zeros(3,np.float64),'Expected int32 proper colors'),
            (np.zeros(2,np.int32),'Automatic three-batch diagnostic exceeds its planning range'),
            (np.array([0,1,12],np.int32),'Automatic three-batch diagnostic exceeds its planning range'),
            (np.zeros(120001,np.int32),'Automatic three-batch diagnostic exceeds its planning range')):
            with self.assertRaises(ValueError) as caught:module.find_three(np.array([[0,1]],np.int32),colors)
            self.assertEqual(str(caught.exception),message)


if __name__=='__main__':unittest.main()
