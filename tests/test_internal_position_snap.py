"""Position snapping never creates internal connection state or following."""
import copy,unittest
from types import SimpleNamespace as NS,MethodType
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import draft_tool as T,shape_tool as H,store as ST,pattern_view as PV,pre_click as PRE
from ac9_cloth_sim.core import draft as D,iline as IL,spec as S,snap as SN,boundary_connection as BC

class PositionSnap(unittest.TestCase):
    def setUp(self):
        self.sp=S.new_spec('PositionOnly');D.add_polygon(self.sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
        self.a=D.add_internal(self.sp,'P',[[40,60],[100,60],[160,100]],authored=True,mirror=False)
        self.b=D.add_internal(self.sp,'P',[[60,140],[150,150]],authored=True,mirror=False)
        self.assertEqual(D.problems(self.sp,['P'])+D.internal_problems(self.sp,'P'),[])
        self.old_text=ST.text(bpy.context);self.old_spec=ST.get(bpy.context)[0]
        self.old_snap=bpy.context.window_manager.ac9_pattern_snap
        ST.new_text(bpy.context,self.sp['name']);ST.write(bpy.context,copy.deepcopy(self.sp),keep=True);PV.rebuild(bpy.context,*ST.get(bpy.context))
        self.area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');self.region=next(r for r in self.area.regions if r.type=='WINDOW')
    def tearDown(self):
        T._state['drag']=None
        for ob in list(PV.piece_objects().values()):bpy.data.objects.remove(ob,do_unlink=True)
        bpy.context.scene.ac9_cloth_sim.spec_text=self.old_text;ST.invalidate()
        bpy.context.window_manager.ac9_pattern_snap=self.old_snap
        if self.old_spec is not None:PV.rebuild(bpy.context,*ST.get(bpy.context))
    def test_draw_points_edges_ctrl_and_no_implicit_binding(self):
        with bpy.context.temp_override(area=self.area,region=self.region),patch.object(H,'snap_settings',return_value=(True,False)):
            snapper=H.Snapper(bpy.context,('corner','notch','iend','mid','edge','iline'),outline_only=True,internal_positions=True)
            for query,target,kind in [([100.3,60.2],[100,60],'iend'),([70,60.3],[70,60],'iline')]:
                old=S.dumps(ST.get(bpy.context)[0]);p,k,_=snapper.point(bpy.context,query,.1,piece='P')
                np.testing.assert_allclose(p,target);self.assertEqual(k,kind);self.assertEqual(S.dumps(ST.get(bpy.context)[0]),old)
                np.testing.assert_allclose(snapper.point(bpy.context,query,.1,ctrl=True,piece='P')[0],query)
                sp=copy.deepcopy(self.sp);lid=D.add_internal(sp,'P',[p,[100,140]],mirror=False,authored=True)
                self.assertNotIn('vertex_groups',D.internal(D.piece(sp,'P'),lid));self.assertEqual(BC.groups(D.piece(sp,'P')),[])
                IL.move_node(sp,'P',self.a,1,[0,10]);np.testing.assert_allclose(IL.shape(D.internal(D.piece(sp,'P'),lid))[0][0],target,atol=1e-4)
            self.assertEqual(len(snapper._piece('P',[80,80])[0]),4)
    def test_logical_vertices_closed_and_dense_samples(self):
        sp=copy.deepcopy(self.sp);c=D.add_internal(sp,'P',[[30,120],[70,120],[50,160]],closed=True,authored=True,mirror=False)
        dense=D.add_internal(sp,'P',np.linspace([20,20],[180,20],25),mirror=False)
        pc=D.piece(sp,'P');targets=SN.Targets(sp,internal_vertices=True);pts=targets.arr['iend'][0]
        self.assertTrue(any(np.allclose(p,[100,60]) for p in pts))
        for p in IL.shape(D.internal(pc,c))[0]:self.assertTrue(any(np.allclose(q,p) for q in pts))
        self.assertTrue(any(np.allclose(p,[20,20]) for p in pts));self.assertFalse(any(np.allclose(p,[100,20]) for p in pts))
    def test_drag_commits_position_then_target_deformation_does_not_follow(self):
        class Proxy:
            def __init__(self,source,**kw):self.source=source;self.__dict__.update(kw)
            def __getattr__(self,k):return getattr(self.source,k)
        class Drag:
            move_only='BOTH'
            def as_pointer(self):return id(self)
            def __getattr__(self,k):
                v=getattr(T.AC9_OT_SimPatternDrag,k);return MethodType(v,self) if callable(v) else v
            def report(self,*args):pass
        def ev(p,t='MOUSEMOVE',v='NOTHING',ctrl=False):return NS(mouse_region_x=p[0],mouse_region_y=p[1],type=t,value=v,ctrl=ctrl,shift=False,alt=False,unicode='')
        with bpy.context.temp_override(area=self.area,region=self.region):
            ctx=Proxy(bpy.context,window_manager=Proxy(bpy.context.window_manager,modal_handler_add=lambda op:None))
            for query,target in [([100.3,60.2],[100,60]),([70,60.3],[70,60])]:
                ST.write(bpy.context,copy.deepcopy(self.sp),keep=True);PV.rebuild(bpy.context,*ST.get(bpy.context))
                with patch.object(T,'px_to_mm',return_value=.1),patch.object(T,'mouse_mm',side_effect=lambda c,m,x,y:np.array([x,y],float)),patch.object(T.TB,'under',return_value=('PATTERN',None,None)),patch.object(T,'tool_mode',return_value='EDIT'),patch.object(T,'pick',return_value=('inode','P',self.b,0)):
                    bpy.context.window_manager.ac9_pattern_snap=True
                    op=Drag();self.assertEqual(op.invoke(ctx,ev([60,140],'LEFTMOUSE','PRESS')),{'RUNNING_MODAL'})
                    op.modal(ctx,ev(query));self.assertEqual(op.modal(ctx,ev(query,'LEFTMOUSE','RELEASE')),{'FINISHED'})
                sp=ST.get(bpy.context)[0];pc=D.piece(sp,'P');np.testing.assert_allclose(IL.shape(D.internal(pc,self.b))[0][0],target,atol=1e-4)
                self.assertEqual(BC.groups(pc),[]);self.assertNotIn('vertex_groups',D.internal(pc,self.b))
                IL.move_node(sp,'P',self.a,1,[0,10]);np.testing.assert_allclose(IL.shape(D.internal(pc,self.b))[0][0],target,atol=1e-4)
    def test_tool_and_hover_use_the_same_position_only_policy(self):
        self.assertTrue(T.AC9_OT_SimDrawLine.INTERNAL_POSITIONS)
        import inspect
        self.assertIn('internal_positions',inspect.signature(PRE.snap_point).parameters)

    def test_other_selected_lines_are_not_stationary_targets(self):
        targets=T.internal_position_targets(self.sp,('inode','P',self.b,0),[('ipoint','P',T.ipoint_key(self.a,1))])
        self.assertFalse(np.any(targets.seg_kind=='iline'))
        self.assertEqual(len(targets.arr['iend'][0]),0)

    def test_outline_carry_and_adjacent_edge_lines_are_not_fixed_targets(self):
        sp=copy.deepcopy(self.sp)
        for points in ([[0,0],[75,75],[120,90]],[[200,50],[120,130]],[[100,0],[90,100]]):
            D.add_internal(sp,'P',points,authored=True,mirror=False)
        before=S.dumps(sp)
        group=[('edge','P','E1'),('ipoint','P',T.ipoint_key(self.b,0))]
        targets=T.internal_position_targets(sp,('inode','P',self.b,0),group)
        pts=targets.arr['iend'][0]
        self.assertFalse(any(np.allclose(p,[75,75]) for p in pts))
        self.assertFalse(any(np.allclose(p,[120,130]) for p in pts))
        self.assertFalse(any(np.allclose(p,[90,100]) for p in pts))
        self.assertTrue(any(np.allclose(p,[100,60]) for p in pts))
        self.assertEqual(S.dumps(sp),before)

    def test_actual_self_mirror_moves_are_excluded(self):
        sp=S.new_spec('Mirror');D.add_polygon(sp,[[0,0],[400,0],[400,500],[0,500]],name='P')
        pc=D.piece(sp,'P');pc['mirror_edit']=True
        lid=IL.add_shape(sp,'P',np.array([[80,100],[120,180]],float),[None],closed=False)
        self.assertEqual(len(pc['internals']),2)
        self.assertEqual(D.problems(sp,['P'])+D.internal_problems(sp,'P'),[])
        targets=T.internal_position_targets(sp,('inode','P',lid,0))
        self.assertFalse(np.any(targets.seg_kind=='iline'))

if __name__=='__main__':unittest.main()
