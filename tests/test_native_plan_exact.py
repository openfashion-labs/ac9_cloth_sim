"""Frozen old-plan paths, insertion order, range guards, independent occupancy."""
import importlib,json,os,sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np

class Array:
    def __init__(self,values,**kwargs):self.values=np.asarray(values).copy()
    def numpy(self):return self.values.copy()
    def assign(self,values):self.values[:]=values
    @property
    def size(self):return self.values.size

def fixture_model(data):
    colors=np.asarray(data['initial_colors'],np.int32);followers=np.asarray(data['followers'],np.int32)
    arrays={k:np.asarray(data[k],np.int32).reshape(-1,w)for k,w in [('tri_indices',3),('edge_indices',4),('spring_indices',2)]}
    m=NS(particle_count=data['particle_count'],tri_count=len(arrays['tri_indices']),edge_count=len(arrays['edge_indices']),spring_count=len(arrays['spring_indices']),tet_count=0,
        device='cpu-adapter',particle_colors=Array(colors),particle_color_groups=[Array(np.flatnonzero(colors==i).astype(np.int32))for i in range(int(colors.max()+1))],
        **{k:Array(v)for k,v in arrays.items()})
    tri=np.ones((m.tri_count,2));tri[np.isin(arrays['tri_indices'],followers).any(1)]=0
    edge=np.ones((m.edge_count,2));edge[np.isin(arrays['edge_indices'],followers).any(1)]=0
    m.tri_materials=Array(tri);m.edge_bending_properties=Array(edge)
    return m,NS(vertices=Array(followers),triangles=Array(np.asarray(data['supports'],np.int32)))

class ExactPlanRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'solver'))
        with patch.dict(sys.modules,{'warp':NS(array=Array)}):
            cls.A=importlib.import_module('native_three_automatic');cls.C=importlib.import_module('native_experimental_coloring')
        cls.reference=json.loads((Path(__file__).parent/'data/native_plan_exact_reference.json').read_text())

    def test_frozen_legacy_outputs_neighbor_order_and_search_paths(self):
        self.assertEqual(self.reference['source_ref'],'7b58424')
        for data in self.reference['fixtures']:
            with self.subTest(name=data['name']),patch.dict(os.environ,{'AC9_NATIVE_PLAN_CACHE':'0'}):
                model,bound=fixture_model(data);observed={}
                compact=self.C.compact;proper=self.A.ColoringPlan;search=self.A.find_three
                def capture_compact(neighbors,initial,*args,**kwargs):
                    observed['neighbors']=[list(row)for row in neighbors];observed['greedy']=initial.tolist()
                    return compact(neighbors,initial,*args,**kwargs)
                def capture_proper(*args,**kwargs):
                    plan=proper(*args,**kwargs);observed['proper_colors']=plan.colors.tolist();observed['proper_path']={k:v for k,v in plan.summary.items()if k!='build_s'};return plan
                def capture_search(*args,**kwargs):
                    output,report=search(*args,**kwargs);observed['search_path']={k:v for k,v in report.items()if k!='search_s'};return output,report
                with patch.object(self.C,'compact',capture_compact),patch.object(self.A,'ColoringPlan',capture_proper),patch.object(self.A,'find_three',capture_search):
                    plan=self.A.AutomaticThreeBatchPlan(model,bound)
                observed.update(final_colors=plan.colors.tolist(),batches=[g.numpy().tolist()for g in plan.groups],excluded_tri=plan.excluded_tri.tolist(),excluded_edge=plan.excluded_edge.tolist())
                self.assertEqual(observed,data['expected'])
                self.A.TwoPerStencilBatch(model,bound,plan.colors)
                # Separate set/count oracle, not the search's counts or array helper.
                supports=dict(zip(data['followers'],data['supports']))
                for kind in ('tri_indices','edge_indices','spring_indices'):
                    for row in data[kind]:
                        if kind!='spring_indices'and set(row)&set(data['followers']):continue
                        nodes={u for v in row if v>=0 for u in supports.get(v,[v])if plan.colors[u]>=0}
                        counts=[sum(plan.colors[v]==color for v in nodes)for color in range(3)]
                        self.assertLessEqual(max(counts),2)

    def test_compact_success_traverses_two_conflicting_neighbors(self):
        neighbors=[{1,2,3,4},{0},{0},{0},{0}]
        colors,path=self.C.compact(neighbors,np.array([2,0,0,1,1],np.int32))
        self.assertEqual(colors.tolist(),[1,0,0,0,0])
        self.assertEqual(path,[dict(from_colors=3,to_colors=2,steps=3,unresolved=0),dict(from_colors=2,to_colors=2,steps=1,unresolved=1)])

    def test_original_planning_range_edges(self):
        colors,report=self.A.find_three(np.array([[0,1,2]],np.int32),np.arange(120000,dtype=np.int32)%6,max_steps=0)
        self.assertTrue(report['success']);self.assertEqual(report['sizes'],[40000,40000,40000])
        colors,report=self.A.find_three([[0,1,2]],np.array([0,1,2,11],np.int32))
        self.assertTrue(report['success'])
        for proper in (np.arange(120001,dtype=np.int32)%6,np.array([0,1],np.int32),np.array([0,1,2,12],np.int32)):
            with self.subTest(count=len(proper)),self.assertRaises(ValueError)as raised:
                self.A.find_three([[0,1,2]],proper)
            self.assertEqual(str(raised.exception),'Automatic three-batch diagnostic exceeds its planning range')

    def test_original_stitched_surface_and_material_refusals(self):
        data=self.reference['fixtures'][-1]
        for field in ('spring_count','tet_count'):
            model,bound=fixture_model(data);setattr(model,field,0 if field=='spring_count'else 1)
            with self.assertRaises(ValueError)as raised:self.A.AutomaticThreeBatchPlan(model,bound)
            self.assertEqual(str(raised.exception),'Automatic three-batch diagnostic is audited only for stitched surfaces')
        for field,message in [('tri_materials','The masked bound triangle stencil would become active'),('edge_bending_properties','The masked bound edge stencil would become active')]:
            model,bound=fixture_model(data);getattr(model,field).values[0,0]=1
            with patch.dict(os.environ,{'AC9_NATIVE_PLAN_CACHE':'0'}),self.assertRaises(ValueError)as raised:self.A.AutomaticThreeBatchPlan(model,bound)
            self.assertEqual(str(raised.exception),message)

if __name__=='__main__':unittest.main()
