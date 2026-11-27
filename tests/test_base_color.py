# SPDX-License-Identifier: GPL-3.0-or-later
"""Color editing keeps node links, alpha, material sharing and mesh assignment intact."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
import bpy
from ac9_cloth_sim import base_color as B
from ac9_cloth_sim.core import look, spec, draft


class BaseColor(unittest.TestCase):
    def setUp(self):
        self.mat=bpy.data.materials.new('BaseColorTest');self.mat.use_nodes=True
        self.ctx=NS(window_manager=NS(windows=[]))

    def tearDown(self):
        bpy.data.materials.remove(self.mat)

    def edit(self,value):
        with patch.object(B,'target',return_value=(self.mat,'')):
            B.set(self.ctx,value,True)

    def test_node_and_solid_color_change_together_and_alpha_survives(self):
        color=B.socket(self.mat);color.default_value=(.8,.8,.8,.4)
        self.mat.diffuse_color=(.8,.8,.8,.6);self.edit((.1,.2,.3))
        for actual,expected in zip(color.default_value,(.1,.2,.3,.4)):self.assertAlmostEqual(actual,expected)
        for actual,expected in zip(self.mat.diffuse_color,(.1,.2,.3,.6)):self.assertAlmostEqual(actual,expected)

    def test_linked_color_is_unchanged(self):
        color=B.socket(self.mat);tree=self.mat.node_tree
        source=tree.nodes.new('ShaderNodeRGB');tree.links.new(source.outputs[0],color)
        before=tuple(color.default_value);self.edit((1,0,0))
        self.assertTrue(color.is_linked);self.assertEqual(tuple(color.default_value),before)

    def test_unconnected_principled_is_not_edited(self):
        tree=self.mat.node_tree;tree.links.remove(tree.get_output_node('ALL').inputs['Surface'].links[0])
        self.assertIsNone(B.socket(self.mat));before=tuple(self.mat.diffuse_color)
        self.edit((1,0,0));self.assertEqual(tuple(self.mat.diffuse_color),before)

    def test_non_node_material(self):
        self.mat.use_nodes=False;self.edit((.2,.4,.6))
        for actual,expected in zip(self.mat.diffuse_color[:3],(.2,.4,.6)):self.assertAlmostEqual(actual,expected)

    def test_missing_or_mixed_target_is_not_written(self):
        before=tuple(self.mat.diffuse_color)
        with patch.object(B,'target',return_value=(None,'Select pieces with the same material')):
            B.set(self.ctx,(1,0,0),True)
        self.assertEqual(tuple(self.mat.diffuse_color),before)

    def test_read_does_not_mutate(self):
        before=tuple(self.mat.diffuse_color);nodes=len(self.mat.node_tree.nodes)
        with patch.object(B,'target',return_value=(self.mat,'')):
            B.get(self.ctx,True)
        self.assertEqual(tuple(self.mat.diffuse_color),before);self.assertEqual(len(self.mat.node_tree.nodes),nodes)

    def test_bulk_resolution_matches_own_twin_and_default(self):
        sp=spec.new_spec('ColorInheritance')
        draft.add_polygon(sp,[[0,0],[100,0],[0,100]],name='A')
        twin=draft.mirror_copy(sp,'A')
        draft.add_polygon(sp,[[200,0],[300,0],[200,100]],name='B')
        look.set_default(sp,{'material':'Default','uid':'default'})
        look.set_pieces(sp,['A'],{'material':'Own','uid':'own'})
        next(p for p in sp['pieces'] if p['name']==twin).pop('look',None)
        self.assertEqual(look.resolved_all(sp),{p['name']:look.resolved(sp,p['name']) for p in sp['pieces']})


if __name__=='__main__':unittest.main()
