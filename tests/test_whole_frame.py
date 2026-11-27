"""Selection frame ownership and explicit outline reconnect. Synthetic finite patterns; no solver."""
import copy,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import draft_tool as T,store as ST,pattern_view as PV
from ac9_cloth_sim.core import draft as D,iline as IL,spec as S,point_connection as PC,tool_policy as TP,snap as SN


class WholeFrame(unittest.TestCase):
    def setUp(self):
        self.sp=S.new_spec('WholeFrameContract')
        D.add_polygon(self.sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
        self.lid=D.add_internal(self.sp,'P',[[50,60],[100,100],[150,60]],mirror=False)
        self.pc=D.piece(self.sp,'P')
        self.assertFalse(D.problems(self.sp,['P']))
        self.assertEqual(abs(D.signed_area(S.Outline(self.pc).B)),40000)
        self.assertTrue(np.isfinite(D.corners(self.pc)).all())
    def frame(self,mode='TRANSFORM',zoom=1.,selection=None,visible=None):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for owner,key,value in [(T,'tool_mode',mode),(T.VV,'allows_pattern',True),
                                     (T.VV,'pattern_names',{'P'} if visible is None else visible),
                                     (T,'picked_pieces',[]),(T,'picked_lines',selection or [('iline','P',self.lid)]),
                                     (T.store,'get',(self.sp,{'P':S.Outline(self.pc)})),(T.PV,'layout_matrix',None)]:
                stack.enter_context(patch.object(owner,key,return_value=value))
            stack.enter_context(patch.object(T,'to_world',side_effect=lambda m,p:p))
            stack.enter_context(patch.object(T,'location_3d_to_region_2d',side_effect=lambda r,rv,w:np.asarray(w)*zoom))
            return T.whole_frame(NS(region_data=object(),region=object()),self.sp)
    def test_frame_shares_bounds_with_hit_and_constant_screen_padding(self):
        for z in (.25,1.,4.):
            box=self.frame(zoom=z);pad=TP.WHOLE_FRAME_PADDING_PX
            np.testing.assert_allclose(box,[[50*z-pad,60*z-pad],[150*z+pad,60*z-pad],[150*z+pad,100*z+pad],[50*z-pad,100*z+pad]])
            with patch.object(T,'whole_frame',return_value=box):
                self.assertTrue(T.whole_frame_hit(None,self.sp,*box[0]))
                self.assertFalse(T.whole_frame_hit(None,self.sp,box[0][0]-.01,box[0][1]))
    def test_no_frame_in_edit_other_tools_or_hidden_selection(self):
        self.assertIsNone(self.frame(mode='EDIT'))
        self.assertIsNone(self.frame(mode=None))
        self.assertIsNone(self.frame(visible=set()))
    def test_frame_prioritizes_selected_line_over_piece_but_outside_keeps_pick(self):
        with patch.object(T.VV,'pattern_names',return_value={'P'}),patch.object(T,'picked_pieces',return_value=[]),patch.object(T,'picked_lines',return_value=[('iline','P',self.lid)]):
            self.assertEqual(T.transform_targets(None,self.sp,('piece','P'),selection_first=True),([],[('P',self.lid)]))
            self.assertEqual(T.transform_targets(None,self.sp,('piece','P')),(['P'],[]))
    def test_mixed_selection_moves_piece_once(self):
        with patch.object(T.VV,'pattern_names',return_value={'P'}),patch.object(T,'picked_pieces',return_value=['P']),patch.object(T,'picked_lines',return_value=[('iline','P',self.lid)]):
            self.assertEqual(T.whole_selection(None,self.sp),(['P'],[]))
    def test_move_rotate_scale_keep_frame_selection_over_parent_other_piece_or_empty(self):
        from contextlib import ExitStack
        class Context:
            window_manager=NS(modal_handler_add=lambda op:None)
            def __getattr__(self,k):return getattr(bpy.context,k)
        D.add_polygon(self.sp,[[300,0],[500,0],[500,200],[300,200]],name='Other')
        self.assertFalse(D.problems(self.sp,['P','Other']))
        for mode,hit in ((m,h) for m in ('MOVE','ROTATE','SCALE') for h in (('piece','P'),('piece','Other'),None)):
            op=NS(pieces='',mode=mode,_update=lambda *a:None,as_pointer=lambda:987654)
            event=NS(mouse_region_x=900,mouse_region_y=800)
            with ExitStack() as stack:
                for owner,key,value in [(T.VV,'allows_pattern',True),(T,'tool_mode','TRANSFORM'),
                                        (T.store,'begin_edit',(self.sp,{'P':S.Outline(self.pc)},object())),
                                        (T.PV,'layout_matrix',None),(T,'pick',hit),
                                        (T,'whole_frame',(30,40,170,120)),(T.VV,'pattern_names',{'P','Other'}),
                                        (T,'picked_pieces',[]),(T,'picked_lines',[('iline','P',self.lid)]),
                                        (T,'mouse_mm',np.array([100.,80.]))]:
                    stack.enter_context(patch.object(owner,key,return_value=value))
                stack.enter_context(patch.object(T.PV,'require_integrity'))
                stack.enter_context(patch.object(bpy.types.WindowManager,'modal_handler_add'))
                try:
                    self.assertEqual(T.AC9_OT_SimPatternTransform.invoke(op,Context(),event),{'RUNNING_MODAL'})
                    self.assertEqual(op.P,[]);self.assertEqual(op.L,[('P',self.lid)])
                finally:
                    T._state['drag']=None
                    from ac9_cloth_sim import undo_guard
                    undo_guard._modal.pop(987654,None)


    def test_zoomed_frame_generates_only_visible_dashes_with_original_phase(self):
        for w in (10000,100000,10000000):
            self.assertEqual(T._whole_frame_segments((-w,-w,w,w),500,300),[])
            pts=T._whole_frame_segments((-w,20,w,w),500,300)
            self.assertLessEqual(len(pts),102)
            self.assertTrue(all(0<=x<=500 and y==20 for x,y in pts))
        pts=T._whole_frame_segments((-3,20,10000,10000),500,300)
        self.assertEqual(pts[:2],[(0.,20.),(3.,20.)])


class Reconnect(unittest.TestCase):
    def fixture(self,q=(80,0),polygon=None):
        sp=S.new_spec('EdgeReconnect')
        D.add_polygon(sp,polygon or [[0,0],[200,0],[200,200],[0,200]],name='P')
        lid=D.add_internal(sp,'P',[q,[100,5] if polygon and len(polygon)==3 else [100,100]],mirror=False);pc=D.piece(sp,'P')
        self.assertFalse(D.problems(sp,['P']));self.assertTrue(np.isfinite(D.corners(pc)).all())
        self.assertFalse(D.internal_problems(sp,'P',[lid]))
        PC.detach_pair(sp,'P',lid,0)
        return sp,pc,lid
    def test_explicit_edge_restores_follow_after_save(self):
        sp,pc,lid=self.fixture()
        self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',np.array([80.,0.]))))
        sp=S.loads(S.dumps(sp));pc=D.piece(sp,'P')
        D.move_corners(sp,'P',{0:[0,-20],1:[0,-20]},twins=False)
        np.testing.assert_allclose(IL.shape(D.internal(pc,lid))[0][0],[80,-20])
    def test_nearby_internal_snap_other_piece_closed_and_no_witness_keep_detach(self):
        for witness in [None,('iline','P',[80,0]),('iend','P',[80,0]),('edge','other',[80,0]),('edge','P',[80,.1])]:
            sp,pc,lid=self.fixture()
            self.assertFalse(PC.bind_pair(sp,'P',lid,0,boundary_snap=witness))
            self.assertIs(D.internal(pc,lid)['boundary_nodes']['0'],False)
        sp,pc,lid=self.fixture((80,.01))
        self.assertFalse(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',[80,0])))
    def test_saved_rounding_on_slanted_edge_does_not_reject_snap(self):
        sp,pc,lid=self.fixture((50,20),[[0,0],[200,77],[200,200],[0,200]])
        q=np.array([37.1234567,37.1234567*77/200]);it=D.internal(pc,lid)
        IL.move_node(sp,'P',lid,0,q-IL.shape(it)[0][0])
        self.assertGreater(np.linalg.norm(IL.shape(it)[0][0]-q),PC.EPS)
        self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',q)))
    def test_stationary_nearest_edge_is_not_dragged_by_adjacent_edge(self):
        sp,pc,lid=self.fixture((.1,.01),[[0,0],[200,0],[200,20]])
        # E3 lies on y=x/10, within 1mm of E1. E3's endpoints stay fixed.
        self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',[.1,.01])))
        before=IL.shape(D.internal(pc,lid))[0][0].copy()
        D.move_corners(sp,'P',{1:[0,-20]},twins=False)
        np.testing.assert_array_equal(IL.shape(D.internal(pc,lid))[0][0],before)
    def test_self_symmetric_line_detaches_and_reconnects_both_ends(self):
        sp,pc,lid=self.fixture((0,45));it=D.internal(pc,lid)
        IL.set_shape(it,[[0,45],[200,45]],[None]);pc['mirror_edit']=True
        self.assertEqual(D.self_mirror_line(pc,lid)[0],lid)
        PC.detach_pair(sp,'P',lid,0)
        self.assertEqual(it['boundary_nodes'],{'0':False,'1':False})
        IL.move_node(sp,'P',lid,0,[.2,0]);before=IL.shape(it)[0].copy()
        D.move_edge(sp,'P','E4',[-20,0]);np.testing.assert_array_equal(IL.shape(it)[0],before)
        # Return the actual two symmetric nodes to the new outline.
        IL.move_node(sp,'P',lid,0,[-20.2,0])
        self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',[-20,45])))
        self.assertNotIn('boundary_nodes',it)
        before=IL.shape(it)[0].copy();D.move_edge(sp,'P','E4',[-10,0])
        np.testing.assert_allclose(IL.shape(it)[0]-before,[[-10,0],[10,0]],atol=1e-5)
    def test_mirror_refusal_does_not_reconnect_counterpart(self):
        sp,pc,lid=self.fixture();nm=D.mirror_copy(sp,'P')
        PC.detach_pair(sp,'P',lid,0)
        before=S.dumps(sp)
        self.assertFalse(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','other',[80,0])))
        self.assertEqual(S.dumps(sp),before)
    def test_self_symmetric_twins_detach_save_and_reconnect_all_four_ends(self):
        sp,pc,lid=self.fixture((0,45));IL.set_shape(D.internal(pc,lid),[[0,45],[200,45]],[None]);pc['mirror_edit']=True
        nm=D.mirror_copy(sp,'P');qid=D.mirror_lines(sp,'P',lid)[0][1]
        PC.detach_pair(sp,'P',lid,0)
        def its():return [D.internal(D.piece(sp,n),l) for n,l in [('P',lid),(nm,qid)]]
        self.assertEqual([it.get('boundary_nodes') for it in its()],[{'0':False,'1':False}]*2)
        IL.move_node(sp,'P',lid,0,[.2,0]);before=[IL.shape(it)[0].copy() for it in its()]
        D.move_edge(sp,'P','E4',[-20,0])
        for it,old in zip(its(),before):np.testing.assert_array_equal(IL.shape(it)[0],old)
        IL.move_node(sp,'P',lid,0,[-20.2,0]);sp=S.loads(S.dumps(sp))
        self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',[-20,45])))
        self.assertTrue(all(not it.get('boundary_nodes') for it in its()))
        before=[IL.shape(it)[0].copy() for it in its()];D.move_edge(sp,'P','E4',[-10,0])
        for it,old in zip(its(),before):
            delta=IL.shape(it)[0]-old;np.testing.assert_allclose(np.abs(delta[:,0]),10,atol=1e-4);np.testing.assert_allclose(delta[:,1],0,atol=1e-4)
        self.assertFalse(D.problems(sp,['P',nm]))
    def test_rotated_self_mirror_snap_accepts_only_storage_rounding(self):
        for angle in (3,11,17,29,41,52,68):
            sp,pc,lid=self.fixture((0,45));it=D.internal(pc,lid)
            IL.set_shape(it,[[0,45],[200,45]],[None]);pc['mirror_edit']=True
            D.transform_piece(sp,'P',deg=angle);PC.detach_pair(sp,'P',lid,0)
            n=IL.shape(it)[0];a,b=np.asarray(pc['edges'][3]['pts']);v=b-a
            q=a+v*np.clip((n[0]-a)@v/(v@v),0,1)
            self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',q)))
            self.assertFalse(it.get('boundary_nodes'),(angle,it.get('boundary_nodes')))
            PC.detach_pair(sp,'P',lid,0);old=S.dumps(sp)
            self.assertFalse(PC.bind_pair(sp,'P',lid,0,boundary_snap=('edge','P',q+[.01,.01])))
            self.assertEqual(S.dumps(sp),old)
    def test_curve_and_midpoint_witness_restore_initial_similarity(self):
        sp,pc,lid=self.fixture((80,20));D.make_curve(sp,'P','E1',twins=False)
        D.set_handle(sp,'P','E1',0,[40,60],twins=False)
        targets=SN.Targets(dict(pieces=[dict(pc,internals=[])]))
        q,kind,owner=SN.snap([75,40],100.,targets,('edge',))
        it=D.internal(pc,lid);IL.move_node(sp,'P',lid,0,q-IL.shape(it)[0][0])
        self.assertTrue(PC.bind_pair(sp,'P',lid,0,boundary_snap=(kind,owner,q)))
        before=IL.shape(it)[0][0].copy()
        D.move_corners(sp,'P',{0:[0,-20],1:[0,-20]},twins=False)
        np.testing.assert_allclose(IL.shape(it)[0][0]-before,[0,-20],atol=1e-4)
