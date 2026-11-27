"""Selection-first input boundaries, deletion scope, attribute references and reversible frame history."""
import copy,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import selection_frame as F,draft_tool as T,store as ST,pattern_view as PV,selection as SEL,tool_tab as TT,properties as PR
from ac9_cloth_sim.core import spec as S,draft as D,iline as IL


class FrameMemory(unittest.TestCase):
    def test_rotation_keeps_pivot_and_round_trips_geometry_history(self):
        p=np.array([[20,60],[180,60],[140,100]],float);mem=F.Memory();key=(('P',),())
        center,angle=mem.get('doc',key,p);np.testing.assert_allclose(center,[100,80])
        q=D.group_map(30,center=center)(p);mem.put('doc',key,q,(center,30))
        c,a=mem.get('doc',key,q);np.testing.assert_array_equal(c,center);self.assertEqual(a,30)
        self.assertEqual(mem.get('doc',key,p)[1],0);self.assertEqual(mem.get('doc',key,q)[1],30)
        polygon=F.corners(q,(c,a));edge=polygon[1]-polygon[0]
        self.assertAlmostEqual(np.degrees(np.arctan2(edge[1],edge[0])),30)
        self.assertTrue(F.contains(polygon,center));self.assertFalse(F.contains(polygon,polygon.min(0)))
        self.assertEqual(mem.get('other',key,q)[1],0)
        mem.get('doc',(('Q',),()),p);self.assertEqual(mem.get('doc',key,q)[1],0)
    def test_clipping_is_bounded_for_oriented_offscreen_frames(self):
        p=np.array([[-1e6,-1e6],[1e6,-1e6],[1e6,1e6],[-1e6,1e6]]) @ F.axes(30).T
        self.assertEqual(F.dashes(p,500,300),[])
        p=np.array([[-100000,30],[100000,30],[100000,100000],[-100000,100000]])
        self.assertLessEqual(len(F.dashes(p,500,300)),102)


class InputScopes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=S.new_spec('SelectionUXTests')
        for nm,x in [('P',0),('Q',300)]:D.add_polygon(cls.base,[[x,0],[x+200,0],[x+200,200],[x,200]],name=nm)
        cls.lid=D.add_internal(cls.base,'P',[[20,60],[100,100],[180,60]],mirror=False)
        cls.closed=IL.add_shape(cls.base,'P',[[30,130],[100,160],[160,130]],[None]*3,closed=True)
        assert not D.problems(cls.base,['P','Q']) and not D.internal_problems(cls.base,'P')
        assert all(abs(D.signed_area(S.Outline(p).B))==40000 for p in cls.base['pieces'])
        ST.new_text(bpy.context,cls.base['name'])
    def setUp(self):
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
        self.scope=bpy.context.temp_override(area=area,region=region);self.scope.__enter__()
        bpy.context.scene.ac9_cloth_sim.pattern_tools=True
        ST.write(bpy.context,copy.deepcopy(self.base),keep=True);PV.rebuild(bpy.context,*ST.get(bpy.context))
        T.select_internal(bpy.context);T.select_edge(bpy.context);T.select_corner(bpy.context);T._psel.clear();SEL.deselect_all(bpy.context)
        self.tool('TRANSFORM');T._frame_memory.owners.clear()
    def tearDown(self):
        T._state['drag']=None;self.scope.__exit__(None,None,None)
    def tool(self,mode):bpy.ops.wm.tool_set_by_id(name=T.TRANSFORM_ID if mode=='TRANSFORM' else T.TOOL_ID)
    def data(self):return ST.get(bpy.context)[0]
    def ids(self):return [i['id'] for i in D.piece(self.data(),'P')['internals']]
    def test_whole_key_deletes_open_or_closed_despite_garment_attributes(self):
        for lid in (self.lid,self.closed):
            T.select_internal(bpy.context,'P',lid)
            self.assertEqual(TT.resolve(bpy.context,self.data()),'GARMENT')
            op=NS(report=lambda *a:None,as_pointer=lambda:987654)
            self.assertEqual(T.AC9_OT_SimDeleteSelected.invoke(op,bpy.context,NS(mouse_region_x=10,mouse_region_y=10)),{'FINISHED'})
            self.assertNotIn(lid,self.ids());self.assertEqual(len(self.data()['pieces']),2)
    def test_line_menu_explicit_id_preserves_mixed_piece_selection(self):
        SEL.apply(bpy.context,['Q'],'SET',active='Q',view='pattern');T.select_internal(bpy.context,'P',self.lid)
        row=next(r for r in T.menu_items(self.data(),('iline','P',self.lid),'TRANSFORM_PATTERN') if r and r[0]=='Delete')
        self.assertEqual(row[1],'ac9_cloth_sim.delete_internal')
        self.assertEqual(bpy.ops.ac9_cloth_sim.delete_internal(**row[3]),{'FINISHED'})
        self.assertEqual(len(self.data()['pieces']),2);self.assertNotIn(self.lid,self.ids());self.assertIn(self.closed,self.ids())
    def test_edit_does_not_allow_whole_line_operator_or_endpoint_delete(self):
        self.tool('EDIT');T.select_internal(bpy.context,'P',self.lid)
        self.assertFalse(bpy.ops.ac9_cloth_sim.delete_internal.poll())
        old=S.dumps(self.data())
        op=NS(piece='P',line=self.lid,report=lambda *a:None)
        self.assertEqual(T.AC9_OT_SimDeleteInternal.execute(op,bpy.context),{'CANCELLED'})
        self.assertEqual(S.dumps(self.data()),old)
    def test_edit_middle_point_key_deletes_one_node(self):
        self.tool('EDIT');T.select_internal(bpy.context,'P',self.lid)
        event=NS(mouse_region_x=10,mouse_region_y=10);calls=[]
        def delegate(*args,**kwargs):calls.append((args,kwargs));return {'FINISHED'}
        with patch.object(T,'pick',return_value=('inode','P',self.lid,1)):
            fake=NS(ops=NS(ac9_cloth_sim=NS(pattern_edit=delegate)))
            with patch.object(T,'bpy',fake):
                op=NS(report=lambda *a:None,as_pointer=lambda:987654)
                self.assertEqual(T.AC9_OT_SimDeleteSelected.invoke(op,bpy.context,event),{'FINISHED'})
            self.assertEqual(calls,[(('INVOKE_DEFAULT',),{'action':'REMOVE'})])
            op=NS(action='REMOVE',report=lambda *a:None,as_pointer=lambda:987654)
            self.assertEqual(T.AC9_OT_SimPatternEdit.invoke(op,bpy.context,event),{'FINISHED'})
        self.assertEqual(len(IL.shape(D.internal(D.piece(self.data(),'P'),self.lid))[0]),2)
        self.assertEqual(len(self.ids()),2)
    def test_edit_face_reference_writes_attributes_without_whole_selection(self):
        self.tool('EDIT');self.assertTrue(TT.reference_piece(bpy.context,'P'))
        self.assertEqual(SEL.pattern_pieces(bpy.context),[])
        self.assertEqual(TT.resolve(bpy.context,self.data()),'PIECE')
        self.assertEqual(PR._act()[1:],('P',('P',)))
        bpy.context.scene.ac9_piece_name='Referenced P'
        self.assertEqual(D.piece(self.data(),'P')['label'],'Referenced P')
        self.assertNotIn('label',D.piece(self.data(),'Q'))
    def test_reference_buttons_target_attributes_without_operation_selection(self):
        self.tool('EDIT');TT.reference_piece(bpy.context,'P')
        self.assertEqual(bpy.ops.ac9_cloth_sim.piece_state(state='freeze',scope='PIECE',piece='P'),{'FINISHED'})
        self.assertEqual(S.piece_state(D.piece(self.data(),'P')),'freeze')
        self.assertEqual(S.piece_state(D.piece(self.data(),'Q')),'active')
        self.assertTrue(bpy.ops.ac9_cloth_sim.symmetric_edit.poll())
        self.assertEqual(bpy.ops.ac9_cloth_sim.symmetric_edit(),{'FINISHED'})
        self.assertTrue(D.piece(self.data(),'P').get('mirror_edit'))
        self.assertFalse(D.piece(self.data(),'Q').get('mirror_edit'))
        self.assertEqual(bpy.ops.ac9_cloth_sim.piece_mesh(piece='P',mesh_mm=7),{'FINISHED'})
        self.assertEqual(D.piece(self.data(),'P')['mesh_mm'],7)
        self.assertEqual(bpy.ops.ac9_cloth_sim.piece_mesh(piece='P',mesh_mm=0),{'FINISHED'})
        self.assertFalse(D.piece(self.data(),'P').get('mesh_mm'))
        self.assertEqual(SEL.pattern_pieces(bpy.context),[])
    def test_mixed_own_parent_key_cannot_delete_piece(self):
        SEL.apply(bpy.context,['P'],'SET',active='P',view='pattern');T.select_internal(bpy.context,'P',self.lid)
        old=S.dumps(self.data());op=NS(report=lambda *a:None,as_pointer=lambda:987654)
        self.assertEqual(T.AC9_OT_SimDeleteSelected.invoke(op,bpy.context,NS(mouse_region_x=10,mouse_region_y=10)),{'CANCELLED'})
        self.assertEqual(S.dumps(self.data()),old)
    def test_partial_explicit_delete_never_falls_back_to_selection(self):
        T.select_internal(bpy.context,'P',self.lid);old=S.dumps(self.data())
        self.assertEqual(bpy.ops.ac9_cloth_sim.delete_internal(piece='Q'),{'CANCELLED'})
        self.assertEqual(bpy.ops.ac9_cloth_sim.delete_internal(line=self.lid),{'CANCELLED'})
        self.assertEqual(S.dumps(self.data()),old)
    def test_confirmed_delete_uses_frozen_ids_and_document(self):
        SEL.apply(bpy.context,['P','Q'],'SET',active='P',view='pattern')
        calls=[]
        def delegate(*args,**kwargs):calls.append(kwargs);return {'RUNNING_MODAL'}
        with patch.object(T.VV,'pattern_names',return_value={'P'}),patch.object(T,'bpy',NS(ops=NS(ac9_cloth_sim=NS(delete_piece=delegate)))):
            op=NS(report=lambda *a:None,as_pointer=lambda:987654)
            self.assertEqual(T.AC9_OT_SimDeleteSelected.invoke(op,bpy.context,NS(mouse_region_x=10,mouse_region_y=10)),{'RUNNING_MODAL'})
        self.assertEqual(calls,[{'names':'["P"]'}])
        SEL.apply(bpy.context,['Q'],'SET',active='Q',view='pattern')
        self.assertEqual(bpy.ops.ac9_cloth_sim.delete_piece('EXEC_DEFAULT',**calls[0]),{'FINISHED'})
        self.assertEqual([p['name'] for p in self.data()['pieces']],['Q'])
        owner=ST.edit_owner(bpy.context);original=ST.text(bpy.context);ST.new_text(bpy.context,'OtherUXDocument')
        op=NS(_delete_owner=owner,names='["Q"]')
        with self.assertRaises(ST.EditChanged):T.AC9_OT_SimDeletePiece.execute(op,bpy.context)
        bpy.context.scene.ac9_cloth_sim.spec_text=original
    def test_projection_visibility_does_not_reset_rotation(self):
        T.select_internal(bpy.context,'P',self.lid);sp=self.data();P,L=T.whole_selection(bpy.context,sp)
        center=D.group_center(sp,P,L);T._remember_frame(bpy.context,sp,P,L,(center,30))
        with patch.object(T.VV,'pattern_names',return_value={'Q'}):self.assertIsNone(T.whole_frame(bpy.context,sp))
        self.assertEqual(T._frame_state(bpy.context,sp,P,L)[1],30)
        T.select_internal(bpy.context)
        T.whole_frame(bpy.context,sp)
        T.select_internal(bpy.context,'P',self.lid)
        self.assertEqual(T._frame_state(bpy.context,sp,P,L)[1],0)
    def test_tool_notification_does_not_change_world_selection(self):
        self.tool('EDIT');TT._view(bpy.context)
        SEL.apply(bpy.context,['Q'],'SET',active='Q',view='world');old=SEL.piece_selection(bpy.context,self.data())
        TT._tools_changed();TT._tools_changed()
        self.assertEqual(SEL.piece_selection(bpy.context,self.data()),old)
    def test_edit_world_multiselection_is_not_a_single_face_reference(self):
        self.tool('EDIT');TT._view(bpy.context).pop('reference',None)
        SEL.apply(bpy.context,['P','Q'],'SET',active='Q',view='world')
        self.assertIsNone(TT.attribute_reference(bpy.context,self.data()))
        self.assertEqual(set(TT.attribute_selection(bpy.context,self.data())[0]),{'P','Q'})
        self.assertEqual(bpy.ops.ac9_cloth_sim.piece_state(state='freeze',scope='SELECTED'),{'FINISHED'})
        self.assertEqual([S.piece_state(D.piece(self.data(),n)) for n in ('P','Q')],['freeze','freeze'])
        TT.reference_piece(bpy.context,'P')
        self.assertEqual(TT.attribute_reference(bpy.context,self.data()),'P')
        self.assertEqual(TT.attribute_selection(bpy.context,self.data()),(('P',),'P'))
    def test_attribute_panel_buttons_and_label_follow_actual_reference(self):
        self.tool('EDIT')
        class Layout:
            def __init__(self):self.calls=[];self.labels=[]
            def __getattr__(self,key):return lambda *a,**kw:self
            def panel(self,*a,**kw):return self,None
            def operator(self,name,**kw):
                op=NS();self.calls.append((name,op));return op
            def label(self,**kw):self.labels.append(kw.get('text',''))
        def draw():
            layout=Layout();TT.AC9_PT_ToolPiece.draw(NS(layout=layout),bpy.context)
            op=next(op for name,op in layout.calls if name=='ac9_cloth_sim.piece_state' and op.state=='freeze')
            return layout,op
        TT._view(bpy.context).pop('reference',None)
        SEL.apply(bpy.context,['P','Q'],'SET',active='Q',view='world');TT.piece_clicked(bpy.context,origin='external')
        layout,op=draw();self.assertEqual(op.scope,'SELECTED');self.assertFalse(hasattr(op,'piece'))
        self.assertNotIn('Reference piece',layout.labels)
        self.assertEqual(bpy.ops.ac9_cloth_sim.piece_state(**vars(op)),{'FINISHED'})
        self.assertEqual([S.piece_state(D.piece(self.data(),n)) for n in ('P','Q')],['freeze','freeze'])
        TT.reference_piece(bpy.context,'P');layout,op=draw()
        self.assertEqual((op.scope,op.piece),('PIECE','P'));self.assertIn('Reference piece',layout.labels)
    def test_vertical_and_horizontal_frames_keep_area_at_right_angles(self):
        for nodes in ([[100,40],[100,160]],[[40,100],[160,100]]):
            for deg in (0,30,90,-90):
                sp=copy.deepcopy(self.base);lid=D.add_internal(sp,'P',nodes,mirror=False)
                D.transform_group(sp,[],[('P',lid)],deg=deg,center=[100,100]);ST.write(bpy.context,sp,keep=True)
                sp=self.data();T.select_internal(bpy.context,'P',lid);center=np.array([100.,100.])
                with patch.object(T,'to_world',side_effect=lambda m,q:q),patch.object(T,'location_3d_to_region_2d',side_effect=lambda r,rv,w:np.asarray(w)[:2]):
                    T._remember_frame(bpy.context,sp,[],[('P',lid)],(center,deg));frame=T.whole_frame(bpy.context,sp)
                # Stored draft coordinates are float32; allow its subpixel projection error.
                np.testing.assert_allclose(abs(D.signed_area(frame)),6400,rtol=1e-6,atol=0)
                self.assertTrue(F.contains(frame,center+[0,10]));self.assertFalse(F.contains(frame,center+[1000,0]))
                edge=frame[1]-frame[0]
                self.assertAlmostEqual(np.degrees(np.arctan2(edge[1],edge[0])),deg,places=4)
        self.assertFalse(F.contains(np.zeros((4,2)),[1000,0]))
    def test_whole_grs_without_selection_never_adopts_hover(self):
        for mode in ('MOVE','ROTATE','SCALE'):
            op=NS(pieces='',mode=mode,report=lambda *a:None)
            with patch.object(T,'pick',return_value=('piece','P')):
                self.assertEqual(T.AC9_OT_SimPatternTransform.invoke(op,bpy.context,NS(mouse_region_x=100,mouse_region_y=100)),{'CANCELLED'})
            self.assertIsNone(T._state['drag'])

