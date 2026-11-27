# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender integration: element scope, coupled moves and track-local seam targets."""
import copy
import inspect
import unittest
from unittest.mock import patch
import bpy
import numpy as np
from ac9_cloth_sim import draft_tool as DT, store as ST, pattern_view as PV, toolbar as TB, seam_tool
from ac9_cloth_sim.core import draft as D, spec as S, iline as IL, seam_edit as SE, point_connection as PC


class ToolEditing(unittest.TestCase):
    def test_copy_menu_uses_transactional_placement(self):
        sp=S.new_spec('MenuCopyContract')
        D.add_polygon(sp,[[0,0],[100,0],[100,100],[0,100]],name='P')
        for tool in ('EDIT_PATTERN','TRANSFORM_PATTERN'):
            rows=[r for r in DT.menu_items(sp,('piece','P'),tool) if r is not None]
            for operator in ('ac9_cloth_sim.duplicate_pieces','ac9_cloth_sim.mirror_copy'):
                row=next(r for r in rows if r[1]==operator)
                self.assertEqual(row[2],'')
                self.assertNotIn('follow',row[3])

    def test_header_keeps_hover_keys_and_actual_edit_permissions(self):
        from ac9_cloth_sim.core import tool_policy as TP
        from ac9_cloth_sim.i18n_ja import JA
        class Layout:
            def __init__(self):self.labels=[]
            def label(self,**kw):self.labels.append(kw.get('text',''))
        before=DT._state.get('hover');props=bpy.context.scene.ac9_cloth_sim
        pending=props.sync_pending
        try:
            props.sync_pending=True
            rendered=[]
            for kind in (None,'edge','corner','iline','iend','inode','piece'):
                DT._state['hover']=(kind,'P','L',0) if kind else None
                layout=Layout();DT.AC9_WT_EditPattern.draw_settings(bpy.context,layout,None)
                self.assertIn('3D not updated',layout.labels)
                self.assertIn(TP.EDIT_MENU_HINT,layout.labels)
                self.assertIn(TP.EDIT_HINTS[kind],layout.labels)
                rendered.append(tuple(layout.labels))
            self.assertEqual(len(set(rendered)),7)
            self.assertIs(DT.HOVER_KEYS,TP.EDIT_HINTS)
            for kind in ('iline','iend'):self.assertNotIn('X:',TP.EDIT_HINTS[kind])
            for key in ('K:','C:','N:'):self.assertIn(key,TP.EDIT_HINTS['edge'])
            self.assertIn('box select',TP.EDIT_HINTS[None])
            for text in (*TP.EDIT_HINTS.values(),TP.EDIT_HEADER,TP.EDIT_MENU_HINT):
                self.assertIn(text,JA);self.assertNotIn('：',JA[text])
        finally:
            DT._state['hover']=before;props.sync_pending=pending

    def setUp(self):
        # This suite runs only inside its dedicated factory-startup process.
        for ob in list(bpy.data.objects): bpy.data.objects.remove(ob, do_unlink=True)
        DT._span_sel.clear(); DT._psel.clear(); DT._csel.clear(); DT._esel.clear(); DT._imore.clear()
        DT._isel.update(piece=None,line=None)
        self.sp=S.new_spec('editing')
        D.add_polygon(self.sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
        self.lid=D.add_internal(self.sp,'P',[[30,40],[80,40],[80,100],[160,100]],mirror=False)
        self.pc=D.piece(self.sp,'P')
        self.assertFalse(D.problems(self.sp,['P']))
        ST.new_text(bpy.context,'Tool integration');ST.text(bpy.context).write(S.dumps(self.sp));ST.invalidate()
        PV.rebuild(bpy.context,*ST.get(bpy.context))

    def test_click_selects_one_span_and_shift_adds(self):
        DT.select_span(bpy.context,self.sp,'P',self.lid,1)
        self.assertEqual(set(DT.picked_lines(self.sp)),set(DT.span_points(self.sp,'P',self.lid,1)))
        DT.select_span(bpy.context,self.sp,'P',self.lid,0,extend=True)
        self.assertEqual(len(DT.picked_lines(self.sp)),3)
        self.assertNotIn(('iline','P',self.lid),DT.picked_lines(self.sp))

    def test_tool_switch_preserves_line_owner_without_whole_line_edit_permission(self):
        DT.select_internal(bpy.context,'P',self.lid)
        with patch.object(DT,'tool_mode',return_value='EDIT'):
            self.assertEqual(DT.picked_lines(self.sp),[])
            self.assertEqual(DT.selected_internal(bpy.context,self.sp)[:2],('P',self.lid))
        with patch.object(DT,'tool_mode',return_value='TRANSFORM'):
            self.assertIn(('iline','P',self.lid),DT.picked_lines(self.sp))

    def test_inactive_span_has_no_whole_mode_selection_decoration(self):
        from unittest.mock import MagicMock
        DT.select_span(bpy.context,self.sp,'P',self.lid,1)
        with patch.object(DT,'tool_mode',return_value='TRANSFORM'), \
             patch.object(DT,'batch_for_shader',return_value=MagicMock()) as batch:
            DT._draw_isel(bpy.context)
            self.assertEqual(batch.call_count,0)
            self.assertEqual(DT._sel_dims(bpy.context,self.sp)[0],[])
        with patch.object(DT,'tool_mode',return_value='EDIT'):
            self.assertEqual(len(DT._sel_dims(bpy.context,self.sp)[0]),1)

    def test_middle_span_moves_two_nodes_only(self):
        before=IL.shape(D.internal(self.pc,self.lid))[0].copy()
        DT.move_lines(self.sp,DT.span_points(self.sp,'P',self.lid,1),[10,0])
        delta=IL.shape(D.internal(self.pc,self.lid))[0]-before
        np.testing.assert_allclose(delta,[[0,0],[10,0],[10,0],[0,0]],atol=1e-5)

    def test_shift_remove_last_span_does_not_select_the_parent(self):
        DT.select_span(bpy.context,self.sp,'P',self.lid,1)
        DT.select_span(bpy.context,self.sp,'P',self.lid,1,extend=True)
        self.assertEqual(DT.picked_lines(self.sp),[])

    def test_adjacent_span_is_not_selected_via_one_shared_node(self):
        DT.select_span(bpy.context,self.sp,'P',self.lid,0)
        self.assertFalse(DT.hit_selected(self.sp,('iline','P',self.lid,1),DT.picked_lines(self.sp)))
        self.assertTrue(DT.hit_selected(self.sp,('iline','P',self.lid,0),DT.picked_lines(self.sp)))

    def test_partial_box_selects_one_span_not_two_isolated_points(self):
        mask=lambda P: np.logical_and(np.asarray(P)[:,0]>=75,np.asarray(P)[:,0]<=85)
        with patch.object(DT,'_in_rect',return_value=mask):
            DT.box_points(bpy.context,(0,0,100,100))
        self.assertEqual(DT._span_sel,[('P',self.lid,1)])
        self.assertEqual(DT._psel,[])

    def test_grs_confirmation_has_no_drag_only_property(self):
        from types import SimpleNamespace as NS
        for cls,attrs in [(DT.AC9_OT_SimPatternTransform,dict(amount=(0,[1,1],np.array([10,0])),rigid=None)),
                          (DT.AC9_OT_SimPatternScale,dict(factor=1.2,piece='P'))]:
            fn=inspect.unwrap(cls.modal)
            op=NS(**attrs,_end=lambda c:None,_owner=object(),spec={},report=lambda *_:None)
            DT._state['drag']=dict(bad=[],preview={},names=['P'])
            try:
                with patch.object(DT,'_edit_current',return_value=True),patch.object(DT,'commit',return_value=None):
                    self.assertEqual(fn(op,NS(area=NS(tag_redraw=lambda:None)),NS(type='RET',value='PRESS')),{'FINISHED'})
            finally:DT._state['drag']=None

    def test_numeric_input_keeps_span_scope(self):
        DT.select_span(bpy.context,self.sp,'P',self.lid,1)
        with patch.object(DT,'tool_mode',return_value='EDIT'):
            row=DT.numeric_row(self.sp,('iline','P',self.lid,1))
        import json
        self.assertEqual(row[3]['kind'],'MULTI')
        self.assertEqual(len(json.loads(row[3]['items'])),2)

    def test_joint_and_outline_in_multi_selection_move_once(self):
        lid=D.add_internal(self.sp,'P',[[0,0],[60,80]],mirror=False)
        items=[('corner','P',self.pc['edges'][0]['id']),('ipoint','P',DT.ipoint_key(lid,0))]
        DT.move_lines(self.sp,items,[20,0])
        np.testing.assert_allclose(D.corners(self.pc)[0],[20,0],atol=1e-5)
        np.testing.assert_allclose(IL.shape(D.internal(self.pc,lid))[0][0],[20,0],atol=1e-5)

    def test_closed_last_span(self):
        lid=IL.add_shape(self.sp,'P',[[20,20],[60,20],[60,60],[20,60]],[None]*4,closed=True)
        DT.move_lines(self.sp,DT.span_points(self.sp,'P',lid,3),[5,0])
        np.testing.assert_allclose(IL.shape(D.internal(self.pc,lid))[0],[[25,20],[60,20],[60,60],[25,60]],atol=1e-5)

    def test_creation_snap_uses_center_internal_and_seam_ends(self):
        self.pc['mirror_edit']=True
        D.add_internal(self.sp,'P',[[60,0],[60,150]],mirror=False)
        eid=self.pc['edges'][0]['id']
        self.sp['seams'].append(dict(id='S1',a=dict(piece='P',**{'from':[eid,.2],'to':[eid,.4],'dir':1}),
                                            b=dict(piece='P',**{'from':[eid,.6],'to':[eid,.8],'dir':1})))
        outlines={'P':S.Outline(self.pc)};idx=seam_tool.OutlineIndex(self.sp,outlines)
        for wanted in (40,60,80,100,120,160):
            value,ok=idx.snap_corner('P',wanted+.1,.5)
            self.assertTrue(ok,wanted);self.assertAlmostEqual(value,wanted,places=5)
        # A mark on the edge never splits the One Edge selection.
        self.assertEqual(seam_tool.whole_edge(outlines['P'],60),(0.,200.,1))

    def test_detach_menu_is_contextual(self):
        lid=D.add_internal(self.sp,'P',[[0,0],[100,100]],mirror=False)
        rows=DT.menu_items(self.sp,('corner','P',0),tool='EDIT_PATTERN')
        labels=[r[0] for r in rows if r]
        self.assertIn('Separate Vertex and Move',labels)
        row=next(r for r in rows if r and r[0]=='Separate Vertex and Move')
        self.assertEqual(row[1],'ac9_cloth_sim.separate_vertex')
        self.assertIn(lid,[r[1] for r in __import__('ac9_cloth_sim.boundary_picker',fromlist=['nearby']).nearby(D.piece(self.sp,'P'),('corner','E1')) if r[0]=='internal'])
        rows=DT.menu_items(self.sp,('corner','P',1),tool='EDIT_PATTERN')
        self.assertNotIn('Separate Vertex and Move',[r[0] for r in rows if r])

    def test_span_menu_does_not_delete_whole_line(self):
        with patch.object(DT,'tool_mode',return_value='EDIT'):
            rows=DT.menu_items(self.sp,('iline','P',self.lid,1),tool='EDIT_PATTERN')
        self.assertFalse(any(r and r[1]=='ac9_cloth_sim.delete_internal' for r in rows))

    def test_numeric_span_scope_does_not_depend_on_hover_context(self):
        with patch.object(DT,'tool_mode',return_value=None):
            row=DT.numeric_row(self.sp,('iline','P',self.lid,1))
        self.assertEqual(row[3]['kind'],'MULTI')

    def test_drawing_shape_numeric_input_keeps_whole_shape_size(self):
        lid=IL.add_shape(self.sp,'P',[[20,20],[60,20],[60,60],[20,60]],[None]*4,closed=True)
        rows=DT.menu_items(self.sp,('iline','P',lid,1),tool='DRAW')
        row=next(r for r in rows if r and r[1]=='ac9_cloth_sim.set_gap')
        self.assertEqual(row[3]['kind'],'ILINE')
        self.assertEqual(DT.as_mode(('iline','P',lid,1),None),('iline','P',lid))

    def test_whole_line_menu_keeps_transform_without_geometry_commands(self):
        rows=DT.menu_items(self.sp,('iline','P',self.lid),tool='TRANSFORM_PATTERN')
        ops={r[1] for r in rows if r}
        self.assertFalse({'ac9_cloth_sim.cut_line','ac9_cloth_sim.spread','ac9_cloth_sim.parallel_line'} & ops)
        self.assertIn('ac9_cloth_sim.pattern_transform',ops)
        self.assertIn('ac9_cloth_sim.delete_internal',ops)
        row=next(r for r in rows if r and r[1]=='ac9_cloth_sim.delete_internal')
        self.assertEqual(row[3],{'piece':'P','line':self.lid})

    def test_hidden_seam_does_not_draw_selected_correspondence(self):
        from contextlib import ExitStack
        from types import SimpleNamespace as NS
        from unittest.mock import MagicMock
        from ac9_cloth_sim import overlay as O
        props=NS(show_overlay=True,show_lengths=True,show_guides=False,show_internal_3d=False)
        ctx=NS(scene=NS(ac9_cloth_sim=props),region=NS(width=100,height=100))
        geometry=dict(pos=[1],col=[1],apos=[1],acol=[1],guides=[],placed=[],join=[(0,0,0),(1,1,0)])
        with ExitStack() as st:
            st.enter_context(patch.object(O,'bpy',NS(context=ctx)))
            st.enter_context(patch.object(O,'gpu',MagicMock()))
            for obj,key,val in ((O,'shows',True),(O,'_draw_gone_view',None),(O.store,'get',({'id':1},{})),
                                (O.PV,'layout_empty',True),(O,'_build',geometry),(O.VV,'allows_world',False),(TB,'pattern_seams',False)):
                st.enter_context(patch.object(obj,key,return_value=val))
            batch=st.enter_context(patch.object(O,'_batch',return_value=MagicMock()))
            O._draw_view()
            self.assertEqual(batch.call_count,0)

    def test_hover_does_not_draw_the_handle_overlay_again(self):
        from unittest.mock import MagicMock
        from types import SimpleNamespace as NS
        from mathutils import Matrix
        lid=IL.add_shape(self.sp,'P',[[30,110],[100,115],[160,110]],
                         [[[45,135],[75,135]],None],closed=False)
        DT.select_span(bpy.context,self.sp,'P',lid,0)
        gpu=MagicMock()
        with patch.object(DT,'_view_state',return_value=(self.sp,['P'],('piece','P'),False,False)), \
             patch.object(DT,'gpu',gpu),patch.object(DT,'batch_for_shader',return_value=MagicMock()), \
             patch.object(DT,'_draw_gaps'),patch.object(DT,'tool_mode',return_value='TRANSFORM'), \
             patch.object(PV,'layout_matrix',return_value=Matrix.Identity(4)):
            DT._draw_hover(NS(region=NS(width=200,height=200)))
        helper_color=(0.6,0.85,1.0,0.9)
        self.assertFalse(any(c.args==('color',helper_color) for c in gpu.shader.from_builtin.return_value.uniform_float.call_args_list))


if __name__=='__main__': unittest.main()
