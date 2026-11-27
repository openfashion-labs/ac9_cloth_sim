# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import math
import unittest
import numpy as np
from ac9_cloth_sim.core import spec as S,draft as D,extend as E,iline as IL,internal_sync as IS


class WidthExtension(unittest.TestCase):
    def fixture(self,points=None):
        sp=S.new_spec('width extension')
        D.add_polygon(sp,points or [[0,0],[100,0],[100,80],[0,80]],name='P')
        self.assertFalse(D.problems(sp,['P']))
        return sp

    def edge(self,sp,eid):return D.piece(sp,'P')['edges'][D.edge_index(D.piece(sp,'P'),eid)]

    def test_connected_right_and_top_get_their_own_normals_and_one_corner(self):
        sp=self.fixture();before=S.dumps(sp)
        out,_,lines=E.plan_width(sp,{'P':['E2','E3']},10,twins=False)
        np.testing.assert_allclose(self.edge(out,'E2')['pts'],[[110,0],[110,90]])
        np.testing.assert_allclose(self.edge(out,'E3')['pts'],[[110,90],[0,90]])
        np.testing.assert_allclose(D.internal(D.piece(out,'P'),lines['P'][0])['pts'],[[100,0],[100,80],[0,80]])
        self.assertEqual(S.dumps(sp),before);self.assertFalse(D.problems(out,['P']))

    def test_disconnected_top_and_bottom(self):
        sp=self.fixture();out,_,lines=E.plan_width(sp,{'P':['E1','E3']},10,twins=False)
        np.testing.assert_allclose(np.asarray(self.edge(out,'E1')['pts'])[:,1],-10)
        np.testing.assert_allclose(np.asarray(self.edge(out,'E3')['pts'])[:,1],90)
        self.assertEqual(len(lines['P']),2)

    def test_slanted_edges_are_ten_mm_from_their_source_lines(self):
        sp=self.fixture([[-60,0],[0,-60],[100,0],[80,120],[0,150]])
        out,_,_=E.plan_width(sp,{'P':['E2','E3']},10,twins=False)
        for eid in ['E2','E3']:
            a,b=np.asarray(self.edge(sp,eid)['pts']);t=b-a;n=np.array([t[1],-t[0]])/np.linalg.norm(t)
            np.testing.assert_allclose((np.asarray(self.edge(out,eid)['pts'])-a)@n,10,atol=.0001)

    def test_whole_outline_is_a_closed_retained_line(self):
        sp=self.fixture();out,_,lines=E.plan_width(sp,{'P':['E1','E2','E3','E4']},10,twins=False)
        pc=D.piece(out,'P');self.assertEqual(len(pc['edges']),4)
        np.testing.assert_allclose(D.corners(pc).min(0),[-10,-10]);np.testing.assert_allclose(D.corners(pc).max(0),[110,90])
        self.assertTrue(D.internal(pc,lines['P'][0])['closed'])
        self.assertEqual(len(IL.shape(D.internal(pc,lines['P'][0]))[0]),4)

    def test_acute_corner_keeps_the_approved_spike(self):
        h=50/math.tan(math.radians(10));sp=self.fixture([[-50,0],[50,0],[0,h]])
        out,_,_=E.plan_width(sp,{'P':['E1','E2','E3']},10,twins=False)
        self.assertAlmostEqual(float(D.corners(D.piece(out,'P'))[:,1].max())-h,10/math.sin(math.radians(10)),delta=.0002)

    def test_curved_outline_and_original_controls(self):
        sp=self.fixture();edge=self.edge(sp,'E1');edge['ctrl']=[[25,-10],[75,-10]];D.refresh_curve(edge)
        old=copy.deepcopy(edge);out,_,lines=E.plan_width(sp,{'P':['E1']},10,twins=False)
        retained=D.internal(D.piece(out,'P'),lines['P'][0])
        np.testing.assert_allclose(IL.shape(retained)[1][0],old['ctrl'])
        np.testing.assert_allclose(retained['pts'],old['pts'])
        self.assertNotIn('ctrl',self.edge(out,'E1'));self.assertGreater(len(self.edge(out,'E1')['pts']),2)
        original=E._edge_points(old);shifted=np.asarray(self.edge(out,'E1')['pts'])
        np.testing.assert_allclose(D._dist_to_polyline(shifted,original),10,atol=.005)
        self.assertFalse(D.problems(out,['P']))

    def test_sewing_off_keeps_both_material_paths_and_on_keeps_edge_fractions(self):
        sp=self.fixture();D.add_polygon(sp,[[200,0],[300,0],[300,80],[200,80]],name='Q')
        sp['seams']=[dict(id='S1',a=dict(piece='P',**{'from':['E2',.2],'to':['E3',.7],'dir':1}),
                         b=dict(piece='Q',**{'from':['E1',.1],'to':['E2',.3],'dir':1}),enabled=True)]
        for on in [False,True]:
            out,_,_=E.plan_width(sp,{'P':['E2','E3'],'Q':['E1']},10,on,twins=False)
            for key in ['a','b']:
                if on:self.assertEqual(out['seams'][0][key],sp['seams'][0][key])
                else:
                    old=S.side_polyline(sp,sp['seams'][0][key]);new=np.vstack([S.side_polyline(out,row[key]) for row in out['seams']])
                    def resample(P):
                        s=np.r_[0,np.cumsum(np.linalg.norm(np.diff(P,axis=0),axis=1))]
                        return np.column_stack([np.interp(np.linspace(0,s[-1],301),s,P[:,j]) for j in range(2)])
                    np.testing.assert_allclose(resample(new),resample(old),atol=.0001)

    def test_full_outline_seam_can_stay_on_the_closed_original(self):
        sp=self.fixture();sp['seams']=[dict(id='S1',a=dict(piece='P',**{'from':['E3',.5],'to':['E2',.5],'dir':1}),
             b=dict(piece='P',**{'from':['E1',0.],'to':['E1',1.],'dir':1}),enabled=True)]
        out,_,_=E.plan_width(sp,{'P':['E1','E2','E3','E4']},10,False,twins=False)
        self.assertAlmostEqual(sum(S.side_length(out,row['a']) for row in out['seams']),S.side_length(sp,sp['seams'][0]['a']),places=4)
        self.assertTrue(all('line' in row['a'] and 'line' in row['b'] for row in out['seams']))

    def test_combined_mirrors_grow_all_corresponding_edges(self):
        sp=self.fixture();D.piece(sp,'P')['mirror_edit']=True;other=D.mirror_copy(sp,'P')
        out,names,lines=E.plan_width(sp,{'P':['E2']},10)
        self.assertEqual(set(names),{'P',other})
        for name in names:
            pc=D.piece(out,name);self.assertAlmostEqual(float(np.ptp(D.corners(pc),axis=0)[0]),120)
            self.assertEqual(len(lines[name]),2)
            for lid in lines[name]:self.assertIsNotNone(IS.match(out,name,lid))
        self.assertFalse(D.problems(out,names))

    def test_concave_edge_cannot_disappear_then_reverse(self):
        sp=self.fixture([[50,-10],[30,30],[10,10],[0,20],[-20,20]]);before=S.dumps(sp)
        out,_,_=E.plan_width(sp,{'P':['E2','E3']},10,twins=False)
        old=np.diff(np.asarray(self.edge(sp,'E3')['pts']),axis=0)[0]
        new=np.diff(np.asarray(self.edge(out,'E3')['pts']),axis=0)[0]
        self.assertGreater(float(old@new),0)
        with self.assertRaisesRegex(ValueError,'erase or reverse'):
            E.plan_width(sp,{'P':['E2','E3']},20,twins=False)
        self.assertEqual(S.dumps(sp),before)

    def test_closed_mirror_sewing_stations_survive_the_next_line_edit(self):
        for curve in [False,True]:
            sp=self.fixture()
            if curve:
                edge=self.edge(sp,'E1');edge['ctrl']=[[25,-10],[75,-10]];D.refresh_curve(edge)
            other=D.mirror_copy(sp,'P')
            sp['seams']=[dict(id='S1',a=dict(piece='P',**{'from':['E1',.2],'to':['E2',.4],'dir':1}),
                             b=dict(piece=other,**{'from':['E2',.3],'to':['E4',.2],'dir':1}),enabled=True)]
            out,_,lines=E.plan_width(sp,{'P':[e['id'] for e in D.piece(sp,'P')['edges']]},10,False)
            def resample(P):
                s=np.r_[0,np.cumsum(np.linalg.norm(np.diff(P,axis=0),axis=1))]
                return np.column_stack([np.interp(np.linspace(0,s[-1],501),s,P[:,j]) for j in range(2)])
            for key in ['a','b']:
                # The legacy outline reader's default 1 mm sampling can cut
                # across corners; compare the material tracks at finer steps.
                old=S.side_polyline(sp,sp['seams'][0][key],step=.001)
                new=np.vstack([S.side_polyline(out,row[key],step=.001) for row in out['seams']])
                np.testing.assert_allclose(resample(new),resample(old),atol=.0002)
            def endpoints(s):return np.asarray([S.side_polyline(s,row[k])[j] for row in s['seams'] for k in ['a','b'] for j in [0,-1]])
            baseline=endpoints(out)
            # A zero edit in either direction must not change any existing station.
            for name in ['P',other]:
                clone=copy.deepcopy(out)
                IL.move_node(clone,name,lines[name][0],1,[0,0])
                np.testing.assert_allclose(endpoints(clone),baseline,atol=.0001)
                self.assertFalse(D.problems(clone,['P',other]))
            clone=copy.deepcopy(out);IL.move_node(clone,'P',lines['P'][0],1,[1,0])
            self.assertLess(float(np.linalg.norm(endpoints(clone)-baseline,axis=1).max()),2.1)

    def test_save_read_and_invalid_inputs_are_atomic(self):
        sp=self.fixture();before=S.dumps(sp)
        out,_,_=E.plan_width(sp,{'P':['E2','E3']},10,twins=False)
        self.assertEqual(S.dumps(S.loads(S.dumps(out))),S.dumps(out))
        for selected,width in [({},10),({'P':['E1']},0),({'P':['E1']},float('nan')),({'P':['missing']},10)]:
            with self.assertRaises((ValueError,StopIteration)):E.plan_width(sp,selected,width,twins=False)
            self.assertEqual(S.dumps(sp),before)

    def test_old_corner_connections_remain_independent_of_retained_internal_vertex(self):
        from ac9_cloth_sim.core import boundary_connection as VC,point_connection as PC
        for explicit in [False,True]:
            for mode in ['WIDTH','XY','WHOLE']:
                sp=self.fixture();pc=D.piece(sp,'P')
                old=[D.add_internal(sp,'P',[[100,80],end],mirror=False) for end in [[50,40],[70,20]]]
                detached=D.add_internal(sp,'P',[[100,80],[10,10]],mirror=False)
                D.internal(pc,detached)['boundary_nodes']={'0':False}
                D.internal(pc,detached)['vertex_groups']={'0':False}
                if explicit:
                    for lid in old:self.assertTrue(PC.bind(pc,lid,0))
                selected={'P':['E1','E2','E3','E4'] if mode=='WHOLE' else ['E2','E3']}
                if mode=='XY':out,_,lines=E.plan(sp,selected,[10,10],twins=False)
                else:out,_,lines=E.plan_width(sp,selected,10,twins=False)
                pc=D.piece(out,'P');lid=lines['P'][0];N=IL.shape(D.internal(pc,lid))[0]
                k=int(np.linalg.norm(N-[100,80],axis=1).argmin())
                members=VC.members(pc,('internal',lid,k))
                for old_id in old:self.assertNotIn(('internal',old_id,0),members,(mode,explicit,members))
                before=D.corners(pc).copy();IL.move_node(out,'P',lid,k,[1,0])
                for old_id in old:np.testing.assert_allclose(IL.shape(D.internal(pc,old_id))[0][0],[100,80])
                np.testing.assert_allclose(IL.shape(D.internal(pc,detached))[0][0],[100,80])
                np.testing.assert_allclose(D.corners(pc),before)

    def test_more_than_twelve_original_corners_remain_authored_after_save_and_repeated_edits(self):
        from ac9_cloth_sim.core import boundary_connection as VC,point_connection as PC
        for count in [12,13,20]:
            angles=np.linspace(0,2*np.pi,count,endpoint=False)
            sp=self.fixture(np.column_stack([50*np.cos(angles),50*np.sin(angles)]).tolist());pc=D.piece(sp,'P')
            old=D.add_internal(sp,'P',[[50,0],[0,0]],mirror=False)
            self.assertTrue(PC.bind(pc,old,0))
            out,_,lines=E.plan_width(sp,{'P':[e['id'] for e in pc['edges']]},5,twins=False)
            out=S.loads(S.dumps(out));pc=D.piece(out,'P');lid=lines['P'][0]
            self.assertTrue(IL.editable(D.internal(pc,lid)))
            k=int(np.linalg.norm(IL.shape(D.internal(pc,lid))[0]-[50,0],axis=1).argmin())
            for step in [1,2]:
                IL.move_node(out,'P',lid,k,[1,0]);out=S.loads(S.dumps(out));pc=D.piece(out,'P')
                self.assertTrue(IL.editable(D.internal(pc,lid)))
                self.assertNotIn(('internal',old,0),VC.members(pc,('internal',lid,k)))
                np.testing.assert_allclose(IL.shape(D.internal(pc,old))[0][0],[50,0])

    def test_curve_then_whole_extension_keeps_logical_corners_and_connections(self):
        from ac9_cloth_sim.core import boundary_connection as VC,point_connection as PC
        sp=self.fixture();e=self.edge(sp,'E1');e['ctrl']=[[25,-10],[75,-10]];D.refresh_curve(e)
        first,_,_=E.plan_width(sp,{'P':['E1']},10,twins=False)
        pc=D.piece(first,'P');original=np.vstack([e['pts'][:-1] for e in pc['edges']]+[pc['edges'][-1]['pts'][-1:]])
        out,_,lines=E.plan_width(first,{'P':[e['id'] for e in pc['edges']]},5,twins=False)
        out=S.loads(S.dumps(out));pc=D.piece(out,'P');it=D.internal(pc,lines['P'][0])
        self.assertEqual(len(IL.shape(it)[0]),6);self.assertTrue(IL.editable(it))
        np.testing.assert_allclose(it['pts'],original)
        self.assertGreater(len(it['pts']),100)
        N=IL.shape(it)[0];k=int(np.linalg.norm(N-[0,0],axis=1).argmin())
        members=VC.members(pc,('internal',it['id'],k));self.assertEqual(len(members),1)
        outer=D.corners(pc).copy();IL.move_node(out,'P',it['id'],k,[1,0])
        np.testing.assert_allclose(D.corners(pc),outer)
        self.assertTrue(IL.editable(S.loads(S.dumps(out))['pieces'][0]['internals'][-1]))

    def test_sampled_spans_edit_insert_cut_clone_and_affine_preserve_material(self):
        from ac9_cloth_sim.core import cut
        sp=self.fixture();lid=D.add_internal(sp,'P',[[0,0],[50,20],[100,0]],mirror=False)
        it=D.internal(D.piece(sp,'P'),lid);IL.set_spans(it,[[[0,0],[50,20],[100,0]]],[None])
        self.assertEqual(len(IL.shape(it)[0]),2)
        self.assertEqual(IL.locate(it,[50,20]),(0,.5,0.))
        IL.insert_node(sp,'P',lid,[50,20]);it=D.internal(D.piece(sp,'P'),lid)
        np.testing.assert_allclose(IL.shape(it)[0],[[0,0],[50,20],[100,0]])
        self.assertAlmostEqual(S.polyline_len(it['pts']),107.703296,places=5)
        chain=cut._cut_edges(it,np.asarray(it['pts']),set())
        self.assertAlmostEqual(sum(S.polyline_len(e['pts']) for e in chain),107.703296,places=5)
        # Non-uniform transforms and reflected copies operate on every sample.
        raw=dict(id='raw',closed=False);IL.set_spans(raw,[[[0,0],[50,20],[100,0]]],[None])
        scaled=copy.deepcopy(raw);D.xform_internal(scaled,lambda p:np.asarray(p)*[1,2])
        np.testing.assert_allclose(scaled['pts'],[[0,0],[50,40],[100,0]])
        dst=copy.deepcopy(raw);IL._copy_to(raw,dst,lambda p:np.asarray(p)*[1,-1],True)
        np.testing.assert_allclose(dst['pts'],[[100,0],[50,-20],[0,0]])
        # A closed clone preserves the raw span, with the same logical corner count.
        IL.set_spans(it,[[[0,0],[50,20],[100,0]],[[100,0],[100,70]],[[100,70],[0,70]],[[0,70],[0,0]]],[None]*4)
        it['closed']=True;it['nodes']=it['nodes'][:-1]
        nm=D.clone_shape(sp,'P',lid)
        self.assertEqual(len(D.piece(sp,nm)['edges']),4)
        self.assertAlmostEqual(sum(S.polyline_len(e['pts']) for e in D.piece(sp,nm)['edges']),347.703296,places=5)


    def test_mixed_raw_and_bezier_insert_and_nonuniform_scale(self):
        sp=self.fixture()
        for eid,ctrl in [('E1',[[25,-10],[75,-10]]),('E3',[[75,130],[25,130]])]:
            edge=self.edge(sp,eid);edge['ctrl']=ctrl;D.refresh_curve(edge)
        first,_,_=E.plan_width(sp,{'P':['E1']},10,twins=False)
        out,_,lines=E.plan_width(first,{'P':[e['id'] for e in D.piece(first,'P')['edges']]},5,twins=False)
        pc=D.piece(out,'P');it=D.internal(pc,lines['P'][0]);N,C,closed,own=IL.shape(it)
        self.assertTrue(own);k=next(k for k,c in enumerate(C) if c is not None)
        raw=IL.span_points(it);q=np.asarray(raw[k][len(raw[k])//3]);s,t,d=IL.locate(it,q)
        self.assertEqual(s,k);self.assertLess(d,.3)
        IL.insert_node(out,'P',it['id'],q)
        self.assertLess(float(np.linalg.norm(IL.shape(it)[0]-q,axis=1).min()),.3)
        transformed=copy.deepcopy(it);D.xform_internal(transformed,lambda p:np.asarray(p)*[1,2])
        self.assertTrue(IL.shape(transformed)[3]);self.assertTrue(IL.editable(transformed))
        np.testing.assert_allclose(IL.shape(transformed)[0],IL.shape(it)[0]*[1,2],atol=.0001)
        for before,after,c in zip(IL.span_points(it),IL.span_points(transformed),IL.shape(it)[1]):
            if c is None:np.testing.assert_allclose(after,before*[1,2],atol=.0001)


    def test_individual_three_bands_leave_the_two_bottom_corners_empty(self):
        sp=self.fixture();before=S.dumps(sp)
        out,_,lines=E.plan_width(sp,{'P':['E1','E2','E4']},10,twins=False,individual=True)
        pc=D.piece(out,'P');self.assertAlmostEqual(abs(D.signed_area(E._ring(pc))),10600)
        self.assertEqual(len(lines['P']),3)
        for lid in lines['P']:
            self.assertFalse(D.internal(pc,lid).get('closed'));self.assertEqual(len(IL.span_points(D.internal(pc,lid))),1)
        self.assertFalse(D.problems(out,['P']));self.assertEqual(S.dumps(sp),before)
        for q in [[0,0],[100,0]]:self.assertEqual(int((np.linalg.norm(D.corners(pc)-q,axis=1)<1e-5).sum()),1)

    def test_individual_full_outline_has_four_open_references_and_connected_notches(self):
        from ac9_cloth_sim.core import point_connection as PC,boundary_connection as VC
        sp=self.fixture();old=D.add_internal(sp,'P',[[0,0],[30,30]],mirror=False)
        detached=D.add_internal(sp,'P',[[0,0],[50,40]],mirror=False)
        D.internal(D.piece(sp,'P'),detached).update(boundary_nodes={'0':False},vertex_groups={'0':False})
        out,_,lines=E.plan_width(sp,{'P':['E1','E2','E3','E4']},10,twins=False,individual=True)
        pc=D.piece(out,'P');it=D.internal(pc,lines['P'][0]);self.assertEqual(len(lines['P']),4)
        self.assertAlmostEqual(abs(D.signed_area(E._ring(pc))),11600)
        for lid in lines['P']:
            ref=D.internal(pc,lid);self.assertFalse(ref.get('closed'))
            for k in range(2):self.assertIsNotNone(PC.corner(pc,ref,k))
        IL.move_node(out,'P',it['id'],0,[1,0])
        np.testing.assert_allclose(IL.shape(D.internal(pc,old))[0][0],[1,0])
        np.testing.assert_allclose(IL.shape(D.internal(pc,detached))[0][0],[0,0])
        self.assertTrue(any(ref[0]=='corner' for ref in VC.members(pc,('internal',it['id'],0))))

    def test_individual_sewing_off_stays_on_material_and_on_runs_around_the_notch(self):
        sp=self.fixture();sp['seams']=[dict(id='S1',a=dict(piece='P',**{'from':['E1',.8],'to':['E2',.2],'dir':1}),
             b=dict(piece='P',**{'from':['E4',0.],'to':['E4',1.],'dir':1}),enabled=True)]
        for on,length in [(False,36),(True,56)]:
            out,_,_=E.plan_width(sp,{'P':['E1','E2']},10,on,twins=False,individual=True)
            self.assertAlmostEqual(sum(S.side_length(out,s['a']) for s in out['seams']),length,places=5)
            if not on:
                self.assertEqual(len(out['seams']),2)
                actual=np.vstack([S.side_polyline(out,s['a']) for s in out['seams']])
                np.testing.assert_allclose(actual[[0,-1]],S.side_polyline(sp,sp['seams'][0]['a'])[[0,-1]],atol=.0001)
            self.assertFalse(D.problems(out,['P']))

    def test_individual_mirror_closed_stations_survive_saved_zero_edit(self):
        sp=self.fixture();other=D.mirror_copy(sp,'P');sp['seams']=[dict(id='S1',a=dict(piece='P',**{'from':['E4',.8],'to':['E2',.2],'dir':1}),
             b=dict(piece=other,**{'from':['E1',.2],'to':['E3',.4],'dir':1}),enabled=True)]
        out,_,lines=E.plan_width(sp,{'P':['E1','E2','E3','E4']},10,False,individual=True)
        out=S.loads(S.dumps(out))
        before=[S.side_polyline(out,s[k])[[0,-1]] for s in out['seams'] for k in ['a','b']]
        IL.move_node(out,'P',lines['P'][0],1,[0,0])
        after=[S.side_polyline(out,s[k])[[0,-1]] for s in out['seams'] for k in ['a','b']]
        np.testing.assert_allclose(after,before,atol=.0001);self.assertFalse(D.problems(out,['P',other]))

    def test_individual_curved_span_preserves_its_old_handles(self):
        sp=self.fixture();edge=self.edge(sp,'E1');edge['ctrl']=[[25,-10],[75,-10]];D.refresh_curve(edge)
        out,_,lines=E.plan_width(sp,{'P':['E1','E2']},10,twins=False,individual=True)
        it=D.internal(D.piece(out,'P'),lines['P'][0]);self.assertTrue(IL.editable(it))
        np.testing.assert_allclose(IL.shape(it)[1][0],edge['ctrl']);self.assertFalse(D.problems(out,['P']))


    def test_individual_exact_bezier_split_keeps_the_smooth_join_inside(self):
        sp=self.fixture();edge=self.edge(sp,'E1');edge['ctrl']=[[0,-40],[100,-40]];D.refresh_curve(edge)
        # Exact de Casteljau halves of the same cubic, tangent continuous at (50,-30).
        pc=D.piece(sp,'P');left=dict(id='E1',pts=[[0,0],[50,-30]],ctrl=[[0,-20],[25,-30]])
        right=dict(id='E5',pts=[[50,-30],[100,0]],ctrl=[[75,-30],[100,-20]])
        D.refresh_curve(left);D.refresh_curve(right);pc['edges']=[left,right]+pc['edges'][1:]
        out,_,lines=E.plan_width(sp,{'P':['E1','E5']},10,twins=False,individual=True)
        pc=D.piece(out,'P');self.assertFalse(np.any(np.linalg.norm(D.corners(pc)-[50,-30],axis=1)<1e-5))
        self.assertEqual(len(lines['P']),2)
        self.assertEqual(len(pc['edges']),7);self.assertFalse(D.problems(out,['P']))
        np.testing.assert_allclose(self.edge(out,'E1')['pts'][-1],self.edge(out,'E5')['pts'][0])
        np.testing.assert_allclose(self.edge(out,'E1')['pts'][-1],[50,-40])


    def test_stale_curve_metadata_does_not_author_drawing_samples(self):
        x=np.linspace(0,100,30);P=np.column_stack([x,10*np.sin(x/30)])
        it=dict(id='sampled',pts=P.tolist(),nodes=[0,29],ctrl=[None])
        self.assertFalse(IL.shape(it)[3])
        IL.set_shape(it,P,[None]*29)
        self.assertFalse(IL.editable(it));self.assertNotIn('nodes',it)


if __name__=='__main__':unittest.main()
