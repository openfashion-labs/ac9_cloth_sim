import unittest
import bpy
from ac9_cloth_sim.view_state import VisibilityBackup,_paths,_resolve,reveal_collection

class VisibilityLifetime(unittest.TestCase):
    def setUp(self):
        self.collections=[]
        self.scene=bpy.context.scene;self.layer=bpy.context.view_layer
        self.a=self.coll('VisibilityParentA');self.b=self.coll('VisibilityParentB')
        self.child=self.coll('VisibilityShared',self.a);self.b.children.link(self.child)
        bpy.context.view_layer.update()
    def tearDown(self):
        if bpy.context.window is not None:bpy.context.window.view_layer=self.layer
        for c in reversed(self.collections):
            try:bpy.data.collections.remove(c)
            except ReferenceError:pass
    def coll(self,name,parent=None):
        c=bpy.data.collections.new(name);(parent or self.scene.collection).children.link(c);self.collections.append(c);return c
    def path(self,parent):
        return (int(self.scene.collection.session_uid),int(parent.session_uid),int(self.child.session_uid))
    def test_multiply_linked_paths_restore_independently(self):
        pa,pb=self.path(self.a),self.path(self.b)
        _resolve(bpy.context,pa).exclude=True;_resolve(bpy.context,pb).hide_viewport=True
        saved=VisibilityBackup(bpy.context)
        _resolve(bpy.context,pa).exclude=False;_resolve(bpy.context,pb).hide_viewport=False
        self.child.hide_viewport=True
        saved.restore(bpy.context)
        self.assertTrue(_resolve(bpy.context,pa).exclude);self.assertFalse(_resolve(bpy.context,pb).exclude)
        self.assertFalse(_resolve(bpy.context,pa).hide_viewport);self.assertTrue(_resolve(bpy.context,pb).hide_viewport)
        self.assertFalse(self.child.hide_viewport)
    def test_removed_path_and_same_name_replacement_are_skipped(self):
        pa,pb=self.path(self.a),self.path(self.b)
        _resolve(bpy.context,pa).exclude=True
        saved=VisibilityBackup(bpy.context)
        self.a.children.unlink(self.child)
        replacement=self.coll(self.child.name,self.a)
        replacement.name='replacement';self.child.name='renamed-shared'
        saved.restore(bpy.context)
        self.assertIsNone(_resolve(bpy.context,pa));self.assertIsNotNone(_resolve(bpy.context,pb))
        newpath=(int(self.scene.collection.session_uid),int(self.a.session_uid),int(replacement.session_uid))
        self.assertFalse(_resolve(bpy.context,newpath).exclude)
    def test_saved_values_do_not_retain_layer_rna(self):
        saved=VisibilityBackup(bpy.context)
        for path,*flags in saved.values:
            self.assertTrue(all(type(x) is int for x in path));self.assertTrue(all(type(x) is bool for x in flags))
    def test_foreign_viewlayer_receives_no_writes(self):
        saved=VisibilityBackup(bpy.context)
        other=self.scene.view_layers.new('OtherVisibilityLayer')
        try:
            bpy.context.window.view_layer=other
            path=self.path(self.a);_resolve(bpy.context,path).exclude=True
            saved.restore(bpy.context)
            self.assertTrue(_resolve(bpy.context,path).exclude)
        finally:
            bpy.context.window.view_layer=self.layer;self.scene.view_layers.remove(other)
    def test_reveal_reacquires_each_hidden_ancestor(self):
        pa=self.path(self.a)
        _resolve(bpy.context,pa[:-1]).exclude=True
        _resolve(bpy.context,pa).hide_viewport=True;self.child.hide_viewport=True
        opened=reveal_collection(bpy.context,int(self.child.session_uid))
        self.assertIn(self.a.name,opened);self.assertIn(self.child.name,opened)
        for prefix in (pa[:-1],pa):
            n=_resolve(bpy.context,prefix)
            self.assertFalse(n.exclude or n.hide_viewport or n.collection.hide_viewport)
    def test_unrelated_collection_changes_do_not_block_restore(self):
        pa=self.path(self.a);_resolve(bpy.context,pa).exclude=True
        saved=VisibilityBackup(bpy.context)
        extra=self.coll('Transient');bpy.data.collections.remove(extra)
        _resolve(bpy.context,pa).exclude=False
        self.child.name='SameIdentityNewLabel'
        saved.restore(bpy.context);self.assertTrue(_resolve(bpy.context,pa).exclude)
    def test_recreated_same_named_viewlayer_receives_no_old_values(self):
        for index in range(3):
            original=self.scene.view_layers.new('ReusedVisibilityLayer')
            bpy.context.window.view_layer=original
            saved=VisibilityBackup(bpy.context)
            bpy.context.window.view_layer=self.layer
            self.scene.view_layers.remove(original)
            replacement=self.scene.view_layers.new('ReusedVisibilityLayer')
            try:
                bpy.context.window.view_layer=replacement
                path=self.path(self.a);_resolve(bpy.context,path).exclude=True
                saved.restore(bpy.context)
                self.assertTrue(_resolve(bpy.context,path).exclude)
            finally:
                bpy.context.window.view_layer=self.layer;self.scene.view_layers.remove(replacement)
    def test_collection_flag_survives_reparenting_without_moving_path_flags(self):
        self.child.hide_viewport=True
        saved=VisibilityBackup(bpy.context)
        self.a.children.unlink(self.child);self.b.children.unlink(self.child)
        self.scene.collection.children.link(self.child)
        self.child.hide_viewport=False
        newpath=(int(self.scene.collection.session_uid),int(self.child.session_uid))
        _resolve(bpy.context,newpath).hide_viewport=True
        saved.restore(bpy.context)
        self.assertTrue(self.child.hide_viewport)
        self.assertTrue(_resolve(bpy.context,newpath).hide_viewport)

class AuxiliaryLifetime(unittest.TestCase):
    def test_suffix_and_foreign_replacement_keep_document_ownership(self):
        from ac9_cloth_sim import placed_view as PLV,garment_owner as GO,store as ST
        from ac9_cloth_sim.core import spec as S
        from mathutils import Matrix
        for replaced in (False,True):
            with self.subTest(replaced=replaced):
                sp=S.new_spec('AuxLifetime')
                text=ST.new_text(bpy.context,sp['name']);ST.write(bpy.context,sp,keep=True)
                foreign=bpy.data.objects.new(PLV.CENTER_NAME,None);bpy.context.scene.collection.objects.link(foreign)
                own=bpy.data.objects.new(PLV.CENTER_NAME,None);bpy.context.scene.collection.objects.link(own)
                own[GO.PLACED]=text;own.matrix_world=Matrix.Translation((.03,0,0))
                me=bpy.data.meshes.new('AuxMirrorMesh');me.from_pydata([(.1,0,0),(.2,0,0),(.1,.1,0)],[],[(0,1,2)])
                placed=bpy.data.objects.new('AuxPlaced',me);PLV._collection(bpy.context).objects.link(placed)
                placed[PLV.PROP]='A';placed[GO.PLACED]=text;placed.parent=own
                mirror=placed.modifiers.new('AuxMirror','MIRROR');mirror.mirror_object=own
                constraint=placed.constraints.new('COPY_LOCATION');constraint.target=own
                def evaluated():
                    bpy.context.view_layer.update()
                    ob=placed.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=ob.to_mesh()
                    values=[tuple(v.co) for v in mesh.vertices];ob.to_mesh_clear();return values
                before=evaluated()
                backup=PLV.PreparedResetBackup(bpy.context,sp)
                foreign_replacement=None
                try:
                    if replaced:
                        oldname=own.name;bpy.data.objects.remove(own,do_unlink=True)
                        foreign_replacement=bpy.data.objects.new(oldname,None);bpy.context.scene.collection.objects.link(foreign_replacement)
                        foreign_replacement.matrix_world=Matrix.Translation((.085,0,0))
                    else:own.matrix_world=Matrix.Translation((.075,0,0))
                    backup.rollback();backup.close()
                    restored=GO.auxiliary(PLV.CENTER_NAME,sp,bpy.context,owner=text)
                    self.assertIsNotNone(restored);self.assertAlmostEqual(restored.matrix_world.translation.x,.03,places=7)
                    if replaced:self.assertAlmostEqual(foreign_replacement.matrix_world.translation.x,.085,places=7)
                    self.assertIs(placed.parent,restored);self.assertIs(placed.constraints[0].target,restored)
                    self.assertIs(placed.modifiers[0].mirror_object,restored)
                    self.assertEqual(evaluated(),before)
                finally:
                    backup.close()
                    for ob in list(bpy.data.objects):
                        if ob.get(GO.PLACED)==text or ob==foreign or ob==foreign_replacement:
                            bpy.data.objects.remove(ob,do_unlink=True)
                    bpy.data.texts.remove(text);ST._discard_cache()

