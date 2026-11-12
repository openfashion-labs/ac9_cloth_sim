"""Read capabilities and explicit compatibility repair on owned Blender objects."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bpy
from ac9_cloth_sim import dress as D, display_state as DS, result_reference as RR


class ResultReferenceContracts(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='result_refs_',dir=os.environ.get('AC9_CONTRACT_OUTPUT')))
        self.objects=[]

    def tearDown(self):
        for ob in self.objects:
            try:
                if bpy.data.objects.get(ob.name)==ob:bpy.data.objects.remove(ob,do_unlink=True)
            except ReferenceError:pass
        DS.invalidate()

    def folder(self,name='job',metadata=True,index=True):
        folder=self.root/name;folder.mkdir()
        if metadata:(folder/'job.json').write_text('{}',encoding='utf-8')
        if index:(folder/'scene.idx.npz').write_bytes(b'owned index marker')
        return folder

    def object(self,folder,stored=False):
        mesh=bpy.data.meshes.new('ResultRefMesh');mesh.from_pydata([[0,0,0]],[],[])
        ob=bpy.data.objects.new('AC9_Garment_ResultRef',mesh);bpy.context.collection.objects.link(ob);self.objects.append(ob)
        if stored:ob['ac9_job']=str(folder)
        mod=ob.modifiers.new('Owned cache','MESH_CACHE');mod.filepath=str(folder/'drape.pc2');mod.show_viewport=False
        return ob

    def test_repeated_queries_do_not_materialize_legacy_property(self):
        folder=self.folder();ob=self.object(folder);original=dict(ob.items())
        for _ in range(5):
            self.assertEqual(D.job_of(ob),str(folder));self.assertTrue(D.has_result(ob))
            self.assertEqual(DS.job(ob),str(folder))
        self.assertEqual(dict(ob.items()),original)

    def test_explicit_repair_is_idempotent(self):
        folder=self.folder();ob=self.object(folder)
        self.assertTrue(D.repair_job_reference(ob));self.assertEqual(ob['ac9_job'],str(folder))
        self.assertFalse(D.repair_job_reference(ob));self.assertEqual(RR.resolve(ob).source,'property')

    def test_run_entry_repairs_without_reconstructing_valid_job(self):
        folder=self.folder();ob=self.object(folder)
        self.assertEqual(D.job_or_from_mesh(bpy.context,ob),str(folder))
        self.assertEqual(ob['ac9_job'],str(folder))
        self.assertEqual(sorted(p.name for p in folder.iterdir()),['job.json','scene.idx.npz'])

    def test_partial_capabilities_are_distinct(self):
        folder=self.folder(metadata=False);ob=self.object(folder)
        ref=RR.resolve(ob);self.assertTrue(ref.has_index);self.assertFalse(ref.has_metadata)
        self.assertTrue(D.has_result(ob));self.assertFalse(D.job_alive(str(folder)))
        self.assertTrue(D.repair_job_reference(ob));self.assertFalse(D.repair_job_reference(ob))

    def test_display_retains_empty_folder_without_claiming_result(self):
        folder=self.folder(metadata=False,index=False);ob=self.object(folder,stored=True)
        self.assertEqual(DS.job(ob),str(folder));self.assertIsNone(D.job_of(ob))
        self.assertFalse(D.has_result(ob));self.assertTrue(RR.resolve(ob,display=True).is_directory)

    def test_complete_property_wins_over_different_cache(self):
        folder=self.folder();other=self.folder('other');ob=self.object(other)
        ob['ac9_job']=str(folder)
        self.assertEqual(D.job_of(ob),str(folder));self.assertFalse(D.repair_job_reference(ob))

    def test_stale_property_can_be_read_without_overwriting_until_repair(self):
        folder=self.folder();ob=self.object(folder);ob['ac9_job']=str(self.root/'missing')
        original=ob['ac9_job'];self.assertEqual(D.job_of(ob),str(folder));self.assertEqual(ob['ac9_job'],original)
        self.assertTrue(D.repair_job_reference(ob));self.assertEqual(ob['ac9_job'],str(folder))

    def test_library_relative_cache_resolves_without_writing_linked_data(self):
        folder=self.folder('linked_job');ob=self.object(folder)
        ob.modifiers[0].filepath='//linked_job/drape.pc2'
        library=self.root/'reader.blend';original_name=ob.name
        bpy.data.libraries.write(str(library),{ob},path_remap='NONE')
        bpy.data.objects.remove(ob,do_unlink=True)
        with bpy.data.libraries.load(str(library),link=True) as (source,target):target.objects=[original_name]
        linked=target.objects[0];bpy.context.collection.objects.link(linked);self.objects.append(linked)
        self.assertFalse(linked.is_editable)
        self.assertEqual(D.job_of(linked),str(folder));self.assertEqual(DS.job(linked),str(folder))
        self.assertFalse(D.repair_job_reference(linked));self.assertNotIn('ac9_job',linked)

    def test_none_is_a_query_not_a_repair(self):
        self.assertIsNone(D.job_of(None));self.assertIsNone(RR.resolve(None));self.assertFalse(D.repair_job_reference(None))

    def test_log_prefers_active_legacy_cache_without_query_repair(self):
        folder=self.folder('a_active');active=self.object(folder)
        other=self.object(self.folder('z_other'),stored=True)
        active.select_set(True);bpy.context.view_layer.objects.active=active
        self.assertEqual(D.job_of(active),str(folder))
        self.assertEqual(D.AC9_OT_SimLog._job(None,bpy.context),str(folder))
        self.assertNotIn('ac9_job',active)

    def test_log_keeps_stored_folder_only_capability(self):
        folder=self.folder(metadata=False,index=False);active=self.object(folder,stored=True)
        active.select_set(True);bpy.context.view_layer.objects.active=active
        self.assertEqual(D.AC9_OT_SimLog._job(None,bpy.context),str(folder))
        self.assertIsNone(D.job_of(active))

    def test_log_reads_index_only_cache_and_rejects_missing_folder(self):
        folder=self.folder(metadata=False);active=self.object(folder)
        active.select_set(True);bpy.context.view_layer.objects.active=active
        self.assertEqual(D.AC9_OT_SimLog._job(None,bpy.context),str(folder))
        self.assertNotIn('ac9_job',active)
        (folder/'scene.idx.npz').unlink();folder.rmdir()
        self.assertIsNone(D.AC9_OT_SimLog._job(None,bpy.context))

    def test_load_removes_broken_cache_before_deciding_mesh_fallback(self):
        folder=self.folder();ob=self.object(folder);ob['ac9_piece_names']='["A"]'
        D._relink_jobs()
        self.assertEqual(len(ob.modifiers),0);self.assertNotIn('ac9_job',ob)
        self.assertEqual(ob[D.JOB_MISSING_PROP],'-')

    def test_load_repairs_valid_surviving_cache_idempotently(self):
        folder=self.folder();(folder/'drape.pc2').write_bytes(b'owned cache marker')
        ob=self.object(folder);D._relink_jobs()
        self.assertEqual(ob['ac9_job'],str(folder));before=dict(ob.items())
        D._relink_jobs();self.assertEqual(dict(ob.items()),before)

    def test_load_keeps_existing_relocated_folder_precedence(self):
        folder=self.folder('cache');(folder/'drape.pc2').write_bytes(b'owned cache marker')
        relocated=self.root/'AC9_ClothSim_jobs'/'old_job';relocated.mkdir(parents=True)
        (relocated/'job.json').write_text('{}',encoding='utf-8');(relocated/'scene.idx.npz').write_bytes(b'owned')
        ob=self.object(folder);ob['ac9_job']=str(self.root/'missing'/'old_job')
        class Proxy:
            def __init__(self,target,**values):self.target,self.values=target,values
            def __getattr__(self,key):return self.values[key] if key in self.values else getattr(self.target,key)
        with patch.object(D,'bpy',Proxy(bpy,data=Proxy(bpy.data,filepath=str(self.root/'scene.blend')))):
            D._relink_jobs()
        self.assertEqual(ob['ac9_job'],str(relocated))


if __name__=='__main__':unittest.main()
