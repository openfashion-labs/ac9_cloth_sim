"""The bundled avatar must keep its adopted mannequin material across shape updates.

Run with Blender --factory-startup --python this_file -- [addon_folder] result.json.
"""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import bpy

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ac9_cloth_sim',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
addon=importlib.util.module_from_spec(spec);sys.modules['ac9_cloth_sim']=addon;spec.loader.exec_module(addon)
addon.register()
from ac9_cloth_sim import measure_tool as M

def check_material(test, ob):
    test.assertEqual(len(ob.data.materials),1)
    mat=ob.data.materials[0]
    test.assertIsNotNone(mat)
    test.assertTrue(mat.name.startswith('AC9 Mannequin'))
    expected=[((c+.055)/1.055)**2.4 for c in (.64,.60,.56)]+[1.0]
    test.assertTrue(mat.use_nodes)
    bsdf=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    for actual,wanted in zip(mat.diffuse_color,expected):test.assertAlmostEqual(actual,wanted,places=6)
    for actual,wanted in zip(bsdf.inputs['Base Color'].default_value,expected):test.assertAlmostEqual(actual,wanted,places=6)
    test.assertAlmostEqual(bsdf.inputs['Roughness'].default_value,.5)
    test.assertTrue(all(p.material_index==0 for p in ob.data.polygons))

class AvatarAsset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert bpy.ops.ac9_cloth_sim.add_avatar()=={'FINISHED'}
        cls.body=bpy.context.scene.ac9_cloth_sim.body
        cls.collection=next(c for c in cls.body.users_collection if c.name.startswith('AC9 Avatar'))

    def test_body_and_eyes_have_adopted_material(self):
        for ob in self.collection.all_objects:
            if ob.type=='MESH':check_material(self,ob)

    def test_empty_material_slots_are_rejected(self):
        probe=self.body.copy();probe.data=self.body.data.copy()
        try:
            probe.data.materials.clear()
            with self.assertRaises(AssertionError):check_material(self,probe)
        finally:
            mesh=probe.data;bpy.data.objects.remove(probe);bpy.data.meshes.remove(mesh)

    def test_shape_rig_and_unmeasured_entry(self):
        self.assertEqual(len(self.body.data.vertices),13380)
        self.assertEqual(len(self.body.data.polygons),13378)
        self.assertEqual(len(self.collection.all_objects),3)
        self.assertEqual(self.body.parent.type,'ARMATURE')
        self.assertTrue(any(m.type=='ARMATURE' for m in self.body.modifiers))
        self.assertFalse(M.read(self.body).get('items'))

    def test_second_add_keeps_body_and_material(self):
        self.assertEqual(bpy.ops.ac9_cloth_sim.add_avatar(),{'FINISHED'})
        self.assertEqual(bpy.context.scene.ac9_cloth_sim.body,self.body)
        check_material(self,bpy.context.active_object)

run=unittest.main(argv=['avatar'],exit=False)
if '--' in sys.argv:
    Path(sys.argv[-1]).write_text(json.dumps(dict(run=run.result.testsRun,ok=run.result.wasSuccessful())),encoding='utf-8')
if not run.result.wasSuccessful():raise SystemExit(1)
