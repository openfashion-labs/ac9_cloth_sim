# SPDX-License-Identifier: GPL-3.0-or-later
"""Selection, saved interval and mesh consumers must agree on the sewn path."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from ac9_cloth_sim.core import draft as D, spec as S, seam_edit as SE, seam_twin as TW, iline as IL
from ac9_cloth_sim.core.line_track import LineTrack, side_span, split_side, covered_intervals, reverse_side


def fixture():
    sp = S.new_spec('Sewing selection')
    name = D.add_polygon(sp, [[0, 0], [240, 0], [240, 160], [0, 160]], 'Panel')
    D.piece(sp, name)['mirror_edit'] = False
    lid = D.add_internal(sp, name, [[50, 40], [170, 40], [170, 100], [50, 100]], closed=True, mirror=False)
    assert not D.problems(sp, [name]), 'fixture must be healthy before use'
    return sp, name, lid


class SavedPaths(unittest.TestCase):
    def setUp(self):
        self.sp, self.n, self.lid = fixture()
        self.key = self.n + SE.SEP + self.lid
        self.trk = SE.tracks(self.sp)
        self.tr = self.trk[self.key]

    def test_four_authored_edges(self):
        self.assertEqual([abs(b-a) for a,b,d in [self.tr.whole_edge(s) for s in (60,150,240,330)]], [120,60,120,60])
        self.assertEqual(len(self.tr.vertices), 4)

    def test_short_and_long_paths_and_reverse(self):
        for a,b,d,want in [(342,18,1,36),(342,18,-1,324),(18,342,-1,36),(18,342,1,324)]:
            with self.subTest(a=a,b=b,d=d):
                sd = SE.make_side(self.tr,self.key,a,b,d)
                self.assertAlmostEqual(abs(SE.span(sd,self.tr)[2]),want,places=4)
                P = S.side_polyline(self.sp,sd)
                self.assertAlmostEqual(S.polyline_len(P),want,places=4)
                loaded = S.loads(S.dumps(dict(self.sp,seams=[{'id':'U1','a':sd,'b':sd}])))
                np.testing.assert_allclose(S.side_polyline(loaded,loaded['seams'][0]['a']),P)
                reverse_side(sd)
                np.testing.assert_allclose(S.side_polyline(self.sp,sd),P[::-1],atol=1e-8)

    def test_legacy_whole_loop(self):
        for a,b in [(0,1),(1,0)]:
            sd={'piece':self.n,'line':self.lid,'from':a,'to':b}
            self.assertAlmostEqual(S.side_length(self.sp,sd),360)
            self.assertEqual(covered_intervals(sd),[(0,1)])

    def test_split_and_reedit_origin(self):
        sd=SE.make_side(self.tr,self.key,342,18,1)
        a,b=split_side(sd,.25)
        self.assertAlmostEqual(S.side_length(self.sp,a),9,places=3)
        self.assertAlmostEqual(S.side_length(self.sp,b),27,places=3)
        self.sp['seams']=[{'id':'U1','a':sd,'b':dict(sd)}]
        SE.move_end(self.sp,'U1','a','to',5,self.trk,with_group=False)
        out=self.sp['seams'][0]['a']
        self.assertEqual(out['line'],self.lid)
        self.assertAlmostEqual(S.side_length(self.sp,out),41,places=3)
        targets=SE.snap_targets(self.sp,self.key,self.trk)
        expected={round(float(v),8) for v in self.tr.vertices}
        for side in self.sp['seams'][0].values():
            if isinstance(side,dict) and 'piece' in side:
                s0,s1,_=SE.span(side,self.tr)
                expected.update(round(float(v % self.tr.L),8) for v in (s0,s1))
        self.assertEqual(set(targets),expected)
        self.assertEqual(len(targets),len(set(targets)))

    def test_opposite_arcs_not_duplicates(self):
        short=SE.make_side(self.tr,self.key,342,18,1)
        long=SE.make_side(self.tr,self.key,342,18,-1)
        self.assertNotEqual(SE._side_key(short),SE._side_key(long))
        self.assertFalse(TW._same_side(self.sp,{},short,long))
        rev=dict(short); reverse_side(rev)
        self.assertEqual(SE._side_key(short),SE._side_key(rev))
        self.assertAlmostEqual(sum(b-a for a,b in covered_intervals(short)),.1)

    def test_mn_cuts_follow_short_path(self):
        runs=[(self.key,342,18,1)]
        pairs=SE.mn_seams(runs,[(self.key,0,12,1),(self.key,12,36,1)],self.trk,merge_mm=.01)
        self.assertEqual(len(pairs),2)
        self.assertAlmostEqual(sum(S.side_length(self.sp,a) for a,b in pairs),36,places=3)
        np.testing.assert_allclose(S.side_polyline(self.sp,pairs[0][0])[-1], S.side_polyline(self.sp,pairs[1][0])[0],atol=.001)

    def test_curve_samples_are_not_vertices(self):
        it={'pts':[[float(x),float(x*x/10)] for x in range(30)]}
        tr=LineTrack(it['pts'],False,it)
        self.assertEqual(len(tr.vertices),2)
        self.assertAlmostEqual(abs(tr.whole_edge(tr.L/2)[1]-tr.whole_edge(tr.L/2)[0]),tr.L)

    def test_thirteen_authored_vertices_and_unknown_sampled_loop(self):
        angles=np.arange(13)*2*np.pi/13
        P=np.column_stack([100+40*np.cos(angles),80+40*np.sin(angles)])
        lid=D.add_internal(self.sp,self.n,P,kind='shape',closed=True,mirror=False,authored=True)
        it=D.internal(D.piece(self.sp,self.n),lid)
        tr=LineTrack(it['pts'],True,it)
        self.assertEqual(len(tr.vertices),13)
        self.assertAlmostEqual(abs(tr.whole_edge(5)[1]-tr.whole_edge(5)[0]),np.linalg.norm(P[1]-P[0]),places=3)
        unknown=dict(it);unknown.pop('seam_authored_vertices')
        self.assertIsNone(LineTrack(unknown['pts'],True,unknown).whole_edge(5))

    def test_authored_vertices_survive_automatic_peer_and_geometry_copy(self):
        from ac9_cloth_sim.core import internal_sync as IS
        twin=D.mirror_copy(self.sp,self.n,plane='x')
        angles=np.arange(13)*2*np.pi/13
        P=np.column_stack([100+40*np.cos(angles),80+40*np.sin(angles)])
        lid=D.add_internal(self.sp,self.n,P,kind='shape',closed=True,authored=True)
        a=D.internal(D.piece(self.sp,self.n),lid)
        peer=IS.match(self.sp,self.n,lid)
        self.assertIsNotNone(peer)
        b=D.internal(D.piece(self.sp,peer[0]),peer[1])
        self.assertEqual(len(LineTrack(b['pts'],True,b).vertices),13)
        b.pop('seam_authored_vertices',None)
        IS.copy_geometry(a,b,lambda q:np.asarray(q))
        self.assertEqual(len(LineTrack(b['pts'],True,b).vertices),13)

    def test_unknown_curve_copy_does_not_author_sample_points(self):
        angles=np.linspace(0,2*np.pi,33)
        src={'pts':np.column_stack([100+40*np.cos(angles),80+25*np.sin(angles)]).tolist(),'closed':True}
        dst=dict(src)
        IL._copy_to(src,dst,lambda P:np.asarray(P)+[2,0],False)
        self.assertFalse(LineTrack(src['pts'],True,src).edges_known)
        self.assertFalse(LineTrack(dst['pts'],True,dst).edges_known)

    def test_mesh_receives_short_arc_and_leaves_long_arc(self):
        from ac9_cloth_sim.core import pattern_build as PB
        sd=SE.make_side(self.tr,self.key,342,18,1)
        other=D.add_polygon(self.sp,[[300,0],[336,0],[336,40],[300,40]],'Partner')
        pc=D.piece(self.sp,other);pc['mirror_edit']=False
        self.sp['seams']=[{'id':'U1','a':sd,'b':{'piece':other,'from':[pc['edges'][0]['id'],0],
                                               'to':[pc['edges'][0]['id'],1],'dir':1}}]
        self.assertFalse(D.problems(self.sp,[self.n,other]))
        pat,report=PB.build(self.sp,h=10)
        self.assertGreater(len(pat['stitch']),0)
        self.assertAlmostEqual(sum(b-a for a,b,_,_ in PB._unsewn(covered_intervals(sd)))*360,324)

    def test_dart_splits_partner_across_origin(self):
        from ac9_cloth_sim.core import dart
        partner=D.add_polygon(self.sp,[[300,0],[318,0],[318,18],[300,18]],'Partner')
        pc=D.piece(self.sp,partner);pc['mirror_edit']=False
        sd=SE.make_side(self.tr,self.key,342,18,1)
        self.sp['seams']=[{'id':'U1','a':{'piece':partner,'from':[pc['edges'][0]['id'],0],
                                        'to':[pc['edges'][1]['id'],1],'dir':1},'b':sd}]
        made=dart._split_seams_at(self.sp,partner,pc['edges'][0]['id'],pc['edges'][1]['id'])
        self.assertEqual(len(made),1)
        self.assertAlmostEqual(sum(S.side_length(self.sp,x['b']) for x in self.sp['seams']),36,places=3)

    def test_reversed_mirror_keeps_short_arc(self):
        from ac9_cloth_sim.core import seam_twin as TW
        partner=D.add_polygon(self.sp,[[300,0],[540,0],[540,160],[300,160]],'Partner')
        pc=D.piece(self.sp,partner);pc['mirror_edit']=False
        it=D.internal(D.piece(self.sp,self.n),self.lid)
        Q=np.asarray(it['pts'])[::-1]+[300,0]
        D.add_internal(self.sp,partner,Q,closed=True,mirror=False)
        sd=SE.make_side(self.tr,self.key,342,18,1)
        out,why=TW._mirror_side_by(self.sp,sd,self.n,partner,lambda P:np.asarray(P)+[300,0],{},set())
        self.assertIsNone(why)
        self.assertEqual(out['wrap'],-1)
        np.testing.assert_allclose(S.side_polyline(self.sp,out),S.side_polyline(self.sp,sd)+[300,0],atol=1e-8)


try:
    import bpy
    from ac9_cloth_sim import seam_tool as ST
except ImportError:
    ST = None


@unittest.skipIf(ST is None,'Blender modal test')
class ModalSelection(unittest.TestCase):
    def test_native_first_ctrl_binding_exists_for_both_tools(self):
        from bl_keymap_utils.io import keymap_init_from_data
        from ac9_cloth_sim import toolbar as TB
        cfg=bpy.context.window_manager.keyconfigs.new('Sewing regression')
        try:
            ref=cfg.keymaps.new('Reference',space_type='VIEW_3D')
            controls=[ref.keymap_items.new('wm.search_menu','LEFTMOUSE','PRESS',ctrl=x) for x in (False,True)]
            for cls in (TB.AC9_WT_AddSeam,TB.AC9_WT_AddSeamMN):
                km=cfg.keymaps.new(cls.bl_label,space_type='VIEW_3D')
                keymap_init_from_data(km,[x for x in cls.bl_keymap if x[0]=='ac9_cloth_sim.tool_click'])
                self.assertEqual([sum(i.compare(c) for i in km.keymap_items) for c in controls],[1,1])
        finally:bpy.context.window_manager.keyconfigs.remove(cfg)

    def test_same_contract_for_both_tools_and_sides(self):
        for mn in (False,True):
            for mode in ('EDGE','POINTS'):
                for snap,ctrl in ((True,False),(True,True),(False,False),(False,True)):
                    with self.subTest(mn=mn,mode=mode,snap=snap,ctrl=ctrl):
                        sp,n,lid=fixture(); ol={n:S.Outline(D.piece(sp,n))}
                        base=ST.AC9_OT_SimAddSeamMN if mn else ST._AddSeamBase
                        # Use Python methods on a plain instance, independent of RNA UI state.
                        class Probe(ST._AddSeamBase):
                            def _mouse_mm(self,context,x,y): return np.array([x,y],float)
                            def _status(self,context): pass
                            def _finish(self,context,canceled): return {'CANCELLED'} if canceled else {'FINISHED'}
                        if mn:
                            Probe._placing_end=base._placing_end
                            Probe._click=base._click
                            Probe._sync=base._sync
                            Probe._back=base._back
                        op=Probe(); op.spec=sp; op.source_outlines=ol; op._visible=frozenset(ol)
                        op.idx=ST.OutlineIndex(sp,ol); op.outlines=op.idx.tracks
                        op.step=0; op.sides=[]; op.cur_piece=None; op.cur_start=0; op.cur_dir=1; op.dir_forced=False
                        op.ctrl=False; op.hover=op.hover_edge=None
                        if mn: op.side=0; op.half=0; op.runs=[[],[]]
                        wm=SimpleNamespace(ac9_seam_pick_mode=mode,ac9_pattern_snap=snap)
                        ctx=SimpleNamespace(window_manager=wm,area=SimpleNamespace(tag_redraw=lambda:None))
                        with patch.object(ST.VV,'pattern_names',return_value=op._visible),patch.object(op,'_px_to_mm',return_value=.1):
                            def click(x,y):
                                ev=SimpleNamespace(type='LEFTMOUSE',value='PRESS',ctrl=ctrl,mouse_region_x=x,mouse_region_y=y)
                                # first event includes Ctrl: no preceding modifier event is assumed
                                ST._AddSeamBase.modal(op,ctx,ev)
                            click(110,40)
                            if mode=='POINTS': click(160,40)
                            self.assertEqual(len(op.sides),1)
                            self.assertAlmostEqual(abs(SE.signed_len(op.outlines[op.sides[0][0]],*op.sides[0][1:])),120 if mode=='EDGE' else 50)
                            if mn: op.side=1
                            click(110,100)
                            if mode=='POINTS': click(160,100)
                            self.assertEqual(len(op.sides),2)
                            self.assertEqual(len(sp['seams']),0)
                            back=SimpleNamespace(type='BACK_SPACE',value='PRESS',ctrl=False,mouse_region_x=240,mouse_region_y=80)
                            (base.modal if mn else ST._AddSeamBase.modal)(op,ctx,back)
                            if mode=='EDGE':
                                self.assertEqual(op.half if mn else op.step,0 if mn else 2)
                                click(240,80)
                                self.assertAlmostEqual(abs(SE.signed_len(op.outlines[op.sides[-1][0]],*op.sides[-1][1:])),160)
                            else:
                                self.assertEqual(op.half if mn else op.step,1 if mn else 3)

    def test_snap_and_ctrl_do_not_change_mode(self):
        sp,n,lid=fixture(); idx=ST.OutlineIndex(sp,{n:S.Outline(D.piece(sp,n))})
        key=n+ST.SEP+lid
        self.assertEqual(idx.snap_corner(key,119.5,1),(120.0,True))
        self.assertEqual(idx.snap_corner(key,110,1),(110,False))
        hit=idx.nearest([169.5,40],key)
        snapped,_=ST.pick_start(idx,hit,'POINTS',True,1)
        free,_=ST.pick_start(idx,hit,'POINTS',False,1)
        self.assertEqual(snapped[1],120)
        self.assertAlmostEqual(free[1],119.5)


if __name__=='__main__':
    unittest.main()
