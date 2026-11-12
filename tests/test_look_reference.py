"""Pure material queries, observed saved owners, and explicit idempotent legacy repair."""
import copy
import unittest
from unittest.mock import patch
import bpy
from ac9_cloth_sim import look_apply as LA, look_reference as LR, store as ST, dress as DR
from ac9_cloth_sim.core import spec as S, draft as D, look as LK


class LookReferenceContracts(unittest.TestCase):
    def setUp(self):
        LR._load_post();self.texts=[];self.materials=[]

    def tearDown(self):
        for text in self.texts:
            try:
                if bpy.data.texts.get(text.name)==text:bpy.data.texts.remove(text)
            except ReferenceError:pass
        for mat in self.materials:
            try:
                if bpy.data.materials.get(mat.name)==mat:bpy.data.materials.remove(mat)
            except ReferenceError:pass
        LR._load_post();ST.invalidate()

    def material(self,name='LegacyLook',uid=None):
        mat=bpy.data.materials.new(name);self.materials.append(mat)
        if uid is not None:mat[LA.UID_PROP]=uid
        return mat

    def owner(self,mat,uid='legacy',name='LookOwner',piece=False):
        sp=S.new_spec(name)
        D.add_polygon(sp,[[0,0],[100,0],[0,100]],name='A')
        ref=dict(material=mat.name,uid=uid,unknown_ref_field={'keep':7})
        if piece:sp['pieces'][0]['look']=ref
        else:sp['default_look']=ref
        sp['unknown_test_field']={'keep':[1,2,3]}
        text=ST.new_text(bpy.context,name);self.texts.append(text);ST.write(bpy.context,sp,keep=True)
        return text,sp,ref

    def test_query_does_not_write_mat_or_text_and_follows_rename(self):
        mat=self.material();text,sp,ref=self.owner(mat);body=text.as_string();props=dict(mat.items())
        self.assertIs(LA.material(ref),mat);self.assertEqual(dict(mat.items()),props);self.assertEqual(text.as_string(),body)
        mat.name='LegacyRenamed';self.assertIs(LA.material(ref),mat)
        self.assertEqual(text.as_string(),body);self.assertEqual(dict(mat.items()),props)

    def test_explicit_repair_is_idempotent_and_keeps_unknown_fields(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);mat.name='Renamed'
        result=LR.repair_observed();saved=S.loads(text.as_string())
        self.assertEqual(result['texts_changed'],1);self.assertEqual(result['material_uids_assigned'],1)
        self.assertEqual(mat[LA.UID_PROP],'legacy');self.assertEqual(saved['default_look']['material'],mat.name)
        self.assertEqual(saved['default_look']['unknown_ref_field'],ref['unknown_ref_field'])
        self.assertEqual(saved['unknown_test_field'],sp['unknown_test_field'])
        body=text.as_string();again=LR.repair_observed()
        self.assertEqual((again['texts_changed'],again['material_uids_assigned']),(0,0));self.assertEqual(text.as_string(),body)

    def test_nonempty_material_uid_is_never_replaced(self):
        mat=self.material(uid='existing');text,sp,ref=self.owner(mat);LA.material(ref);mat.name='RenamedExisting'
        result=LR.repair_observed()
        self.assertEqual(mat[LA.UID_PROP],'existing');self.assertEqual(result['material_uids_assigned'],0)
        saved=S.loads(text.as_string())['default_look']
        self.assertEqual(saved['uid'],'existing');self.assertIs(LA.material(saved),mat)

    def test_conflicting_uid_claims_are_preserved(self):
        mat=self.material(uid='existing')
        ta,sa,ra=self.owner(mat,uid='old-a',name='OwnerA');LA.material(ra)
        tb,sb,rb=self.owner(mat,uid='old-b',name='OwnerB');LA.material(rb);mat.name='RenamedShared'
        result=LR.repair_observed();self.assertEqual(result['conflicting_materials'],1)
        self.assertEqual(mat[LA.UID_PROP],'existing')
        for text,uid in ((ta,'old-a'),(tb,'old-b')):
            saved=S.loads(text.as_string())['default_look']
            self.assertEqual(saved['uid'],uid);self.assertEqual(saved['material'],mat.name)
            self.assertIs(LA.material(saved),mat)

    def test_new_uid_authority_after_query_invalidates_old_pending(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);body=text.as_string();mat.name='OldTargetRenamed'
        current=self.material('NewUIDAuthority','legacy')
        result=LR.repair_observed();self.assertEqual(result['texts_changed'],0)
        self.assertEqual(text.as_string(),body);self.assertIs(LA.material(ref),current)
        self.assertNotIn(LA.UID_PROP,mat)

    def test_name_reuse_after_query_does_not_apply_old_pending(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);old_name=mat.name;mat.name='PreviousRenamed'
        current=self.material(old_name);body=text.as_string();result=LR.repair_observed()
        self.assertEqual(result['texts_changed'],0);self.assertEqual(text.as_string(),body)
        self.assertIs(LA.material(ref),current);self.assertNotIn(LA.UID_PROP,mat)

    def test_deleted_material_is_not_a_binding_to_same_name_replacement(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);name=mat.name
        bpy.data.materials.remove(mat);current=self.material(name)
        body=text.as_string();result=LR.repair_observed()
        self.assertEqual(result['texts_changed'],0);self.assertEqual(text.as_string(),body)
        self.assertNotIn(LA.UID_PROP,current);self.assertIs(LA.material(ref),current)

    def test_unqueried_text_with_same_reference_is_not_repaired(self):
        mat=self.material();ta,sa,ra=self.owner(mat,name='Queried');LA.material(ra)
        tb,sb,rb=self.owner(mat,name='NotQueried');body=tb.as_string();mat.name='RenamedObserved'
        LR.repair_observed();self.assertEqual(tb.as_string(),body)
        self.assertEqual(S.loads(ta.as_string())['default_look']['material'],mat.name)

    def test_deleted_owner_is_not_same_name_new_text(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);name=text.name
        bpy.data.texts.remove(text);new=bpy.data.texts.new(name);self.texts.append(new);new.from_string(S.dumps(sp))
        mat.name='RenamedAfterOwnerRemoval';body=new.as_string();result=LR.repair_observed()
        self.assertEqual(result['texts_changed'],0);self.assertEqual(new.as_string(),body)

    def test_default_and_twin_update_only_actual_saved_source(self):
        mat=self.material();text,sp,ref=self.owner(mat,piece=True)
        twin=D.mirror_copy(sp,'A');D.piece(sp,twin).pop('look',None);ST.write(bpy.context,sp,keep=True)
        inherited=LK.piece_look(sp,twin);self.assertIs(inherited,ref);LA.material(inherited);mat.name='TwinRenamed'
        LR.repair_observed();saved=S.loads(text.as_string())
        self.assertNotIn('look',D.piece(saved,twin));self.assertEqual(D.piece(saved,'A')['look']['material'],mat.name)
        sp['default_look']=sp['pieces'][0].pop('look');ST.write(bpy.context,sp,keep=True)
        LA.material(LK.resolved(sp,twin));mat.name='DefaultRenamed';LR.repair_observed()
        saved=S.loads(text.as_string());self.assertEqual(saved['default_look']['material'],mat.name)
        self.assertTrue(all('look' not in pc for pc in saved['pieces']))

    def test_duplicate_uid_keeps_name_disambiguation(self):
        mat=self.material(uid='shared');other=self.material('SecondShared','shared')
        text,sp,ref=self.owner(mat,uid='shared');self.assertIs(LA.material(ref),mat)
        mat.name='FirstSharedRenamed';self.assertIs(LA.material(ref),mat)
        LR.repair_observed();saved=S.loads(text.as_string())['default_look']
        self.assertEqual(saved['uid'],'shared');self.assertEqual([mat[LA.UID_PROP],other[LA.UID_PROP]],['shared','shared'])
        self.assertIs(LA.material(saved),mat)

    def test_changed_owner_body_aborts_prepared_writes(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);mat.name='OwnerCASRenamed'
        original=text.as_string();dumps=S.dumps
        def changed_dump(spec):
            text.from_string(original+'\n ')
            return dumps(spec)
        with patch.object(S,'dumps',changed_dump):result=LR.repair_observed()
        self.assertTrue(result['owner_changed']);self.assertEqual(text.as_string(),original+'\n ')
        self.assertNotIn(LA.UID_PROP,mat)

    def test_partial_text_failure_restores_metadata_and_material_uid(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);mat.name='RollbackRenamed';original=text.as_string()
        def fail(target,body):
            target.from_string('partial')
            raise RuntimeError('injected text replacement failure')
        with patch.object(LR,'_replace_text',fail):result=LR.repair_observed()
        self.assertIsNotNone(result['error']);self.assertEqual(text.as_string(),original)
        self.assertNotIn(LA.UID_PROP,mat)

    def test_same_material_uid_changed_after_query_is_not_repaired(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);body=text.as_string()
        mat[LA.UID_PROP]='changed-after-query';mat.name='ChangedUIDRenamed'
        result=LR.repair_observed()
        self.assertEqual(result['texts_changed'],0);self.assertEqual(text.as_string(),body)
        self.assertEqual(mat[LA.UID_PROP],'changed-after-query')

    def test_save_without_rename_preserves_uid_rescue_after_new_main(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref)
        result=LR.repair_observed();self.assertEqual(result['material_uids_assigned'],1)
        LR._load_post();saved=S.loads(text.as_string());mat.name='RenamedBeforeNextQuery'
        self.assertIs(LA.material(saved['default_look']),mat)

    def test_cancel_save_then_retry_is_idempotent(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);mat.name='CanceledSaveRenamed'
        LR._save_pre();body=text.as_string();props=dict(mat.items())
        # A canceled file save does not roll back save_pre; retry must preserve its completed metadata.
        LR._save_pre();self.assertEqual(text.as_string(),body);self.assertEqual(dict(mat.items()),props)
        self.assertEqual(LR._last_repair['texts_changed'],0)

    def test_two_scenes_preserve_actual_text_owners(self):
        mat=self.material();ta,sa,ra=self.owner(mat,name='SceneA');LA.material(ra)
        scene=bpy.data.scenes.new('MaterialReferenceSceneB')
        try:
            with bpy.context.temp_override(scene=scene):
                tb,sb,rb=self.owner(mat,name='SceneB');LA.material(rb)
            mat.name='SharedAcrossScenes';result=LR.repair_observed()
            self.assertEqual(result['texts_changed'],2)
            for text in (ta,tb):self.assertEqual(S.loads(text.as_string())['default_look']['material'],mat.name)
        finally:bpy.data.scenes.remove(scene)

    def test_serialization_failure_writes_nothing(self):
        mat=self.material();text,sp,ref=self.owner(mat);LA.material(ref);mat.name='SerializeFailure';body=text.as_string()
        with patch.object(S,'dumps',side_effect=ValueError('injected serialization')):LR._save_pre()
        self.assertEqual(text.as_string(),body);self.assertNotIn(LA.UID_PROP,mat)
        self.assertIn('injected serialization',LR._last_repair['error'])

    def test_second_text_failure_restores_both_texts(self):
        mat=self.material();ta,sa,ra=self.owner(mat,name='RollbackA');LA.material(ra)
        tb,sb,rb=self.owner(mat,name='RollbackB');LA.material(rb);mat.name='MultiTextRollback'
        bodies={t.session_uid:t.as_string() for t in (ta,tb)};calls=[]
        def fail_second(target,body):
            calls.append(target.session_uid);target.from_string(body)
            if len(calls)==2:raise RuntimeError('second text failed')
        with patch.object(LR,'_replace_text',fail_second):result=LR.repair_observed()
        self.assertIsNotNone(result['error']);self.assertEqual(len(calls),2)
        for text in (ta,tb):self.assertEqual(text.as_string(),bodies[text.session_uid])
        self.assertNotIn(LA.UID_PROP,mat)

    def test_readonly_query_does_not_repair_unowned_legacy_dict(self):
        mat=self.material();ref=dict(material=mat.name,uid='unowned')
        self.assertIs(LA.material(ref),mat);mat.name='UnownedRenamed';self.assertIs(LA.material(ref),mat)
        result=LR.repair_observed();self.assertEqual(result['texts_changed'],0);self.assertNotIn(LA.UID_PROP,mat)

    def test_store_and_garment_readers_preserve_each_others_current_owner(self):
        for first in ('store','garment'):
            with self.subTest(first=first):
                LR._load_post();DR._SPECS_CACHE.clear();ST._discard_cache()
                mat=self.material('Readers'+first);text,sp,ref=self.owner(mat,name='Readers'+first)
                # Force distinct parses, then hit the first reader's cache after the other reader parsed.
                ST._discard_cache()
                def store():return ST.get(bpy.context)[0]
                def garment():return DR.garment_specs()[DR.garment_name(sp)]
                a,b=(store,garment) if first=='store' else (garment,store)
                parsed=a();b();self.assertIs(a(),parsed);current=parsed['default_look']
                self.assertEqual(len(LR._sources(current)),1);body=text.as_string()
                self.assertIs(LA.material(current),mat);self.assertEqual(text.as_string(),body)
                mat.name='ReadersRenamed'+first;result=LR.repair_observed()
                self.assertEqual(result['texts_changed'],1);self.assertEqual(result['material_uids_assigned'],1)
                LR._load_post();saved=S.loads(text.as_string());self.assertIs(LA.material(saved['default_look']),mat)
                bpy.data.materials.remove(mat);bpy.data.texts.remove(text)

    def test_reparse_replaces_only_its_reader_and_does_not_accumulate_dicts(self):
        mat=self.material();text,sp,ref=self.owner(mat)
        stable=S.loads(text.as_string());LR.bind_owner(text,stable,reader='garment_specs')
        for _ in range(1000):LR.bind_owner(text,S.loads(text.as_string()),reader='store')
        self.assertEqual(len(LR._owners),2)
        self.assertEqual(len(LR._sources(stable['default_look'])),1)


if __name__=='__main__':unittest.main()
