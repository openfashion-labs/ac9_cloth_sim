"""Actual edit/menu/snap boundaries for common vertex groups (Blender)."""
import unittest,json,copy
from unittest.mock import Mock
from types import SimpleNamespace as NS
from unittest.mock import patch
import bpy,numpy as np
from ac9_cloth_sim import draft_tool as T,boundary_picker as VP
from ac9_cloth_sim.core import spec as S,draft as D,iline as IL,boundary_connection as V


class VertexPicker(unittest.TestCase):
    def test_internal_only_overlap_has_no_outline_separation_menu(self):
        pc=D.piece(self.sp,'P')
        for lid in self.ls:D.internal(pc,lid).update(pts=[[80,80],[150,140]],vertex_groups={'0':'legacy'})
        for lid in self.ls:
            self.assertEqual(VP.nearby(pc,('internal',lid,0)),[])
            self.assertFalse(any(row and row[1]=='ac9_cloth_sim.separate_vertex' for row in T.menu_items(self.sp,('inode','P',lid,0),'EDIT_PATTERN')))
    def test_outline_corner_can_rejoin_a_deliberately_detached_internal_endpoint(self):
        from ac9_cloth_sim.core import point_connection as PC
        lid=D.add_internal(self.sp,'P',[[50,45],[80,120]],mirror=False)
        PC.detach(D.piece(self.sp,'P'),lid,0);target=('internal',lid,0)
        op=NS(grab=('corner','P',0),M=None,move_only='BOTH',grab_pt=np.array([0.,0.]),outline_vertex_targets=[(target,np.array([50.,45.]))])
        with patch.object(T,'px_to_mm',return_value=1.):
            delta=T.AC9_OT_SimPatternDrag._snap(op,None,NS(mouse_region_x=0,mouse_region_y=0),np.array([50.3,45.2]),None)
        np.testing.assert_allclose(delta,[50,45]);self.assertEqual(op._outline_vertex_snap,target)
        preview=copy.deepcopy(self.sp);D.move_corners(preview,'P',{0:delta})
        self.assertTrue(T.bind_drag_outline(preview,self.sp,('corner','P',0),target))
        self.assertEqual(D.internal(D.piece(preview,'P'),lid)['boundary_nodes']['0'],'E1')
    def test_internal_draw_snapper_excludes_other_lines_but_default_consumers_keep_them(self):
        from ac9_cloth_sim import shape_tool as H,store as ST,pattern_view as PV
        ctx=bpy.context;old_text=ST.text(ctx);old_spec=ST.get(ctx)[0]
        snap,grid=ctx.window_manager.ac9_pattern_snap,ctx.window_manager.ac9_grid_snap
        area=next(a for a in ctx.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
        try:
            ST.new_text(ctx,self.sp['name']);ST.write(ctx,copy.deepcopy(self.sp),keep=True);PV.rebuild(ctx,*ST.get(ctx))
            ctx.window_manager.ac9_pattern_snap=True;ctx.window_manager.ac9_grid_snap=False
            with ctx.temp_override(area=area,region=region):
                kinds=('corner','notch','iend','mid','edge','iline')
                q=np.array([150.8,121.2])
                outline=H.Snapper(ctx,kinds,outline_only=True);regular=H.Snapper(ctx,kinds)
                np.testing.assert_allclose(outline.point(ctx,q,.5,piece='P')[0],q,atol=1e-6)
                np.testing.assert_allclose(regular.point(ctx,q,.5,piece='P')[0],[150,120],atol=1e-6)
                np.testing.assert_allclose(outline.point(ctx,[199,87],.5,piece='P')[0],[200,87],atol=1e-6)
        finally:
            for obj in list(PV.piece_objects().values()):bpy.data.objects.remove(obj,do_unlink=True)
            ctx.scene.ac9_cloth_sim.spec_text=old_text;ST.invalidate()
            ctx.window_manager.ac9_pattern_snap=snap;ctx.window_manager.ac9_grid_snap=grid
            if old_spec is not None:PV.rebuild(ctx,*ST.get(ctx))
    def test_drag_alignment_and_gap_targets_exclude_other_internal_lines(self):
        hit=('inode','P',self.ls[1],0)
        points=T.ialign_points(self.sp,hit)
        self.assertFalse(np.any(np.linalg.norm(points-[150,120],axis=1)<1e-6))
        self.assertTrue(np.any(np.linalg.norm(points-[40,140],axis=1)<1e-6))
        self.assertEqual(len(T.gap_probe(self.sp,hit,outline_only=True)[1]),4)
        self.assertGreater(len(T.gap_probe(self.sp,hit)[1]),4)
    def test_outline_candidates_keep_exact_ids(self):
        refs=VP.nearby(D.piece(self.sp,'P'),('corner','E1'))
        self.assertEqual(refs,[('corner','E1')]+[('internal',lid,0) for lid in self.ls])
        for lid in self.ls:
            rows=T.menu_items(self.sp,('inode','P',lid,0),'EDIT_PATTERN')
            row=next(r for r in rows if r and r[0]=='Separate Vertex and Move')
            self.assertEqual(tuple(json.loads(row[3]['vertex'])),('internal',lid,0))
    def test_forced_separation_ignores_popup_underlay(self):
        from types import MethodType
        from ac9_cloth_sim import store as ST,pattern_view as PV
        class Proxy:
            def __init__(self,source,**values):self.source=source;self.__dict__.update(values)
            def __getattr__(self,key):return getattr(self.source,key)
        class Op:
            move_only='INTERNAL';target_piece='P';target_line='I2';target_node=0
            def as_pointer(self):return id(self)
            def __getattr__(self,key):
                value=getattr(T.AC9_OT_SimPatternDrag,key)
                return MethodType(value,self) if callable(value) else value
        old_text=ST.text(bpy.context);old_spec=ST.get(bpy.context)[0]
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        region=next(r for r in area.regions if r.type=='WINDOW')
        try:
            ST.new_text(bpy.context,self.sp['name']);ST.write(bpy.context,copy.deepcopy(self.sp),keep=True)
            PV.rebuild(bpy.context,*ST.get(bpy.context))
            with bpy.context.temp_override(area=area,region=region):
                ctx=Proxy(bpy.context,window_manager=Proxy(bpy.context.window_manager,modal_handler_add=lambda op:None))
                with patch.object(T.TB,'under',side_effect=AssertionError('picked popup underlay')) as under,patch.object(T,'tool_mode',return_value='EDIT'),patch.object(T,'mouse_mm',return_value=np.array([80.,80.])):
                    op=Op();self.assertEqual(op.invoke(ctx,NS(mouse_region_x=160,mouse_region_y=160,shift=False,ctrl=False)),{'RUNNING_MODAL'})
                    under.assert_not_called();self.assertEqual(op.grab,('inode','P','I2',0));op.cancel(ctx)
        finally:
            T._state['drag']=None
            for obj in list(PV.piece_objects().values()):bpy.data.objects.remove(obj,do_unlink=True)
            bpy.context.scene.ac9_cloth_sim.spec_text=old_text;ST.invalidate()
            if old_spec is not None:PV.rebuild(bpy.context,*ST.get(bpy.context))
    def test_popup_does_not_overlap_visible_toolbar_or_sidebar(self):
        regions=[NS(type='TOOLS',x=100,width=180),NS(type='UI',x=800,width=300)]
        ctx=NS(region=NS(x=100,width=1000,height=700),area=NS(regions=regions),
               space_data=NS(show_region_toolbar=True,show_region_ui=True))
        for q in (NS(x=900),NS(x=100),None):
            (x,y,w,h),visible=VP.popup_rect(ctx,q,3)
            self.assertGreaterEqual(x,180);self.assertLessEqual(x+w,700)
            self.assertGreaterEqual(y-h,0);self.assertEqual(visible,3)
    def setUp(self):
        self.sp=S.new_spec('VertexPicker')
        D.add_polygon(self.sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
        self.ls=[D.add_internal(self.sp,'P',[[0,0],p],mirror=False) for p in [[150,120],[40,140],[150,40]]]
        self.assertEqual(D.problems(self.sp,['P'])+D.internal_problems(self.sp,'P'),[])
    def test_multiselection_moves_shared_point_once(self):
        items=[('ipoint','P',T.ipoint_key(lid,0)) for lid in self.ls]
        T.move_ipoints(self.sp,items,set(),lambda p:np.array([10,0]))
        for lid in self.ls:np.testing.assert_allclose(IL.shape(D.internal(D.piece(self.sp,'P'),lid))[0][0],[10,0])
    def test_preview_single_vertex_separation_is_not_written_until_commit(self):
        old=S.dumps(self.sp);preview=copy.deepcopy(self.sp)
        op=NS(grab=('inode','P',self.ls[1],0),move_only='INTERNAL',tgroup=None,group=[],mode='EDIT')
        T.AC9_OT_SimPatternDrag._edit(op,preview,np.array([10,0]))
        for i,lid in enumerate(self.ls):
            np.testing.assert_allclose(IL.shape(D.internal(D.piece(preview,'P'),lid))[0][0],[10 if i==1 else 0,0])
        self.assertEqual(S.dumps(self.sp),old)
    def test_merged_corner_does_not_reinterpret_its_old_index(self):
        preview=copy.deepcopy(self.sp)
        lid=D.add_internal(preview,'P',[[0,200],[40,140]],mirror=False)
        from ac9_cloth_sim.core import point_connection as PC
        self.assertTrue(PC.bind(D.piece(preview,'P'),lid,0))
        D.move_corners(preview,'P',{3:[0,-200]},merge=True)
        self.assertEqual(len(D.piece(preview,'P')['edges']),3)
        self.assertEqual(D.problems(preview,['P']),[])
        pc=D.piece(preview,'P')
        self.assertNotIn(('corner','E4'),V.points(pc))
        self.assertEqual(D.internal(pc,lid)['boundary_nodes']['0'],'E1')
        self.assertFalse(T.bind_drag_outline(preview,self.sp,('corner','P',3),('internal',lid,0)))
    def test_picker_edges_and_press_drag_release_are_safe(self):
        def run(events):
            calls=Mock();finish=Mock()
            op=NS(rect=(0,200,200,148),refs=[('internal',lid,0) for lid in self.ls],piece='P',
                  owner=NS(matches=lambda c,**kw:True),first=0,index=0,visible=3,pressed=None,
                  area=NS(tag_redraw=lambda:None),finish=finish,as_pointer=lambda:987654321)
            with patch.object(VP,'bpy',NS(ops=NS(ac9_cloth_sim=NS(pattern_drag=calls)))):
                for kind,value,y in events:
                    result=VP.AC9_OT_SimSeparateVertex.modal(op,None,NS(type=kind,value=value,mouse_region_x=50,mouse_region_y=y,shift=False))
                    if result=={'CANCELLED'}:break
            return op,calls,finish,result
        op,calls,finish,result=run([('LEFTMOUSE','PRESS',83),('LEFTMOUSE','RELEASE',83)])
        self.assertEqual(result,{'CANCELLED'});calls.assert_not_called();finish.assert_called_once()
        op,calls,finish,result=run([('MOUSEMOVE','NOTHING',150),('LEFTMOUSE','PRESS',150),('MOUSEMOVE','NOTHING',125),('LEFTMOUSE','RELEASE',125)])
        self.assertEqual(op.index,0);self.assertEqual(result,{'CANCELLED'});calls.assert_not_called()
        op,calls,finish,result=run([('MOUSEMOVE','NOTHING',125),('LEFTMOUSE','PRESS',125),('LEFTMOUSE','RELEASE',125)])
        self.assertEqual(calls.call_args.args,('INVOKE_DEFAULT',True))
        self.assertEqual(calls.call_args.kwargs['target_line'],self.ls[1])
    def test_picker_rejects_changed_document_before_resolving_node_index(self):
        from ac9_cloth_sim import store as ST,pattern_view as PV
        # The scene keeps its Text identity while the authored node index changes.
        original_text=ST.text(bpy.context)
        original_spec=ST.get(bpy.context)[0]
        for ob in list(PV.piece_objects().values()):bpy.data.objects.remove(ob,do_unlink=True)
        try:
            D.internal(D.piece(self.sp,'P'),self.ls[1])['pts'].reverse()
            refs=VP.nearby(D.piece(self.sp,'P'),('internal',self.ls[0],0))
            target_index=refs.index(('internal',self.ls[1],1))
            ST.new_text(bpy.context,'PickerOwnerTest');ST.write(bpy.context,copy.deepcopy(self.sp),keep=True)
            PV.rebuild(bpy.context,*ST.get(bpy.context));owner=ST.edit_owner(bpy.context)
            changed=copy.deepcopy(self.sp);IL.insert_node(changed,'P',self.ls[1],[60,110])
            self.assertEqual(D.internal_problems(changed,'P'),[])
            ST.write(bpy.context,changed,keep=True);PV.rebuild(bpy.context,*ST.get(bpy.context))
            self.assertTrue(owner.matches(bpy.context));self.assertFalse(owner.matches(bpy.context,content=True))
            self.assertGreater(np.linalg.norm(V.position(D.piece(changed,'P'),refs[target_index])-V.position(D.piece(self.sp,'P'),refs[target_index])),1)
            calls=Mock();finish=Mock()
            op=NS(rect=(0,200,200,148),refs=refs,piece='P',owner=owner,
                  first=0,index=target_index,visible=3,pressed=target_index,area=NS(tag_redraw=lambda:None),finish=finish,as_pointer=lambda:987654322)
            with patch.object(VP,'bpy',NS(ops=NS(ac9_cloth_sim=NS(pattern_drag=calls)))):
                result=VP.AC9_OT_SimSeparateVertex.modal(op,bpy.context,NS(type='LEFTMOUSE',value='RELEASE',mouse_region_x=50,mouse_region_y=200-32-target_index*28-14,shift=False))
            self.assertEqual(result,{'CANCELLED'});calls.assert_not_called();finish.assert_called_once()
        finally:
            for ob in list(PV.piece_objects().values()):bpy.data.objects.remove(ob,do_unlink=True)
            bpy.context.scene.ac9_cloth_sim.spec_text=original_text;ST.invalidate()
            if original_spec is not None:PV.rebuild(bpy.context,*ST.get(bpy.context))

if __name__=='__main__':unittest.main()
