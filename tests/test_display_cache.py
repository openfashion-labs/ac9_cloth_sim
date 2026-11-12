"""Exact derived shape ownership and RNA string lifetime, on real Blender data."""
import gc,json,unittest
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import piece_look as PL, dress as DR, operators as OP


class DisplayCacheContracts(unittest.TestCase):
    def setUp(self):self.objects=[];self.meshes=[];PL.invalidate()
    def tearDown(self):
        for ob in self.objects:
            try:bpy.data.objects.remove(ob,do_unlink=True)
            except ReferenceError:pass
        for me in self.meshes:
            try:bpy.data.meshes.remove(me)
            except ReferenceError:pass
        PL.invalidate()
    def mesh(self,name='CacheTest',faces=()):
        me=bpy.data.meshes.new(name);self.meshes.append(me)
        coords=[(0,0,0),(1,0,0),(1,1,0),(0,1,0)]+[(i*.001,0,0) for i in range(4,48)]
        me.from_pydata(coords,[],faces);ob=bpy.data.objects.new(name,me);self.objects.append(ob)
        bpy.context.scene.collection.objects.link(ob);return ob
    def assert_raw(self,ob):
        co,key=PL._garment_co(bpy.context,ob,'same-job');M=np.array(ob.matrix_world)
        expected=DR.garment_co(bpy.context,ob)@M[:3,:3].T+M[:3,3]
        np.testing.assert_array_equal(co,expected);return co.copy(),key
    def test_unsampled_and_submicrometer_edits_update_exactly(self):
        ob=self.mesh();before,key=self.assert_raw(ob)
        for offset in (.02,.0200001,0):
            ob.data.vertices[1].co.z=offset;ob.data.update();bpy.context.view_layer.update()
            co,new=self.assert_raw(ob);self.assertNotEqual(new,key);key=new
        np.testing.assert_array_equal(co,before)
    def test_unchanged_shape_keeps_generation(self):
        ob=self.mesh();co,key=self.assert_raw(ob)
        for _ in range(5):self.assertEqual(self.assert_raw(ob)[1],key)
    def test_transform_frame_and_data_owner_are_part_of_identity(self):
        ob=self.mesh();co,key=self.assert_raw(ob);ob.location.z=.25;bpy.context.view_layer.update()
        moved,k2=self.assert_raw(ob);self.assertNotEqual(key,k2)
        np.testing.assert_allclose(moved-co,np.broadcast_to([0,0,.25],co.shape))
        bpy.context.scene.frame_set(bpy.context.scene.frame_current+1);k3=self.assert_raw(ob)[1];self.assertNotEqual(k2,k3)
        other=self.mesh('OtherMesh');ob.data=other.data;k4=self.assert_raw(ob)[1];self.assertNotEqual(k3,k4)
    def test_same_name_replacement_is_a_new_owner(self):
        ob=self.mesh();co,key=self.assert_raw(ob);name=ob.name;bpy.data.objects.remove(ob,do_unlink=True)
        other=self.mesh(name);other.data.vertices[1].co.z=.02;other.data.update()
        self.assertNotEqual(self.assert_raw(other)[1],key)
    def test_same_count_connectivity_attribute_and_names_refresh_topology(self):
        ob=self.mesh(faces=[(0,1,2),(0,2,3)]);ob['ac9_piece_names']=json.dumps(['A','B'])
        pa=ob.data.attributes.new('ac9_piece','INT','FACE');pa.data[0].value=0;pa.data[1].value=1
        initial=PL._garment_topo(ob,'topo');g=PL._topo['generation']
        self.assertEqual(set(initial),{'A','B'})
        # Same loop/face counts, different vertex connectivity.
        ob.data.loops[1].vertex_index=3;ob.data.update();bpy.context.view_layer.update()
        changed=PL._garment_topo(ob,'topo');self.assertGreater(PL._topo['generation'],g)
        self.assertFalse(np.array_equal(initial['A']['tri'],changed['A']['tri']))
        pa=ob.data.attributes['ac9_piece'];pa.data[1].value=0;ob.data.update();bpy.context.view_layer.update()
        self.assertEqual(set(PL._garment_topo(ob,'topo')),{'A'})
        ob['ac9_piece_names']=json.dumps(['Renamed','B'])
        self.assertEqual(set(PL._garment_topo(ob,'topo')),{'Renamed'})
    def test_ngon_shape_change_refreshes_triangulation_generation(self):
        ob=self.mesh(faces=[(0,1,2,3)]);ob['ac9_piece_names']='["A"]'
        ob.data.attributes.new('ac9_piece','INT','FACE')
        PL._garment_topo(ob,'topo');g=PL._topo['generation'];ob.data.vertices[1].co.z=.4;ob.data.update();bpy.context.view_layer.update()
        PL._garment_topo(ob,'topo');self.assertGreater(PL._topo['generation'],g)
    def test_topology_unchanged_reuses_piece_arrays(self):
        ob=self.mesh(faces=[(0,1,2)]);ob['ac9_piece_names']='["A"]';ob.data.attributes.new('ac9_piece','INT','FACE')
        first=PL._garment_topo(ob,'topo');self.assertIs(PL._garment_topo(ob,'topo'),first)
    def test_warm_watched_topology_does_not_scan_the_mesh(self):
        ob=self.mesh(faces=[(0,1,2)]);ob['ac9_piece_names']='["A"]';ob.data.attributes.new('ac9_piece','INT','FACE')
        bpy.context.view_layer.update();first=PL._garment_topo(ob,'topo')
        with patch.object(PL.np,'empty',side_effect=AssertionError('warm topology must not allocate bulk reads')):
            self.assertIs(PL._garment_topo(ob,'topo'),first)


class EnumLifetimeContracts(unittest.TestCase):
    def test_old_strings_survive_new_list_gc_and_fields_do_not_alias(self):
        old=OP._keep([(''.join(['legacy','_identifier']),''.join(['Old',' label']),'Old tooltip')],owner='test_a')
        identity=[id(s) for s in old[0]]
        for i in range(1000):OP._keep([(f'next_{i}',f'Label {i}','Tooltip')],owner='test_a')
        gc.collect();again=OP._keep([('legacy_identifier','Old label','Old tooltip')],owner='test_a')
        self.assertEqual(identity,[id(s) for s in again[0]])
        other=OP._keep([('other','Other field','')],owner='test_b')
        self.assertIs(OP._keep(list(again),owner='test_a'),again);self.assertIsNot(other,again)
        self.assertEqual(OP._ENUM_KEEP['test_b'][0][0],'other')
    def test_dynamic_enum_real_rna_value_survives_callback_generations(self):
        state={'generation':0}
        def items(self,context):
            n=state['generation']
            return OP._keep([('FIRST',f'First {n}',f'First tooltip {n}',0),('SECOND',f'Second {n}','Second tooltip',1)],owner='test_rna')
        class AC9TestEnumOwner(bpy.types.PropertyGroup):
            value:bpy.props.EnumProperty(items=items)
        bpy.utils.register_class(AC9TestEnumOwner);bpy.types.Scene.ac9_enum_test=bpy.props.PointerProperty(type=AC9TestEnumOwner)
        try:
            owner=bpy.context.scene.ac9_enum_test;owner.value='SECOND'
            for i in range(1000):
                state['generation']=i;items(owner,bpy.context)
                if i%100==0:gc.collect()
                self.assertEqual(owner.value,'SECOND')
            self.assertEqual(len([k for k in OP._ENUM_KEEP if k=='test_rna']),1)
        finally:
            del bpy.types.Scene.ac9_enum_test;bpy.utils.unregister_class(AC9TestEnumOwner)


if __name__=='__main__':unittest.main()
