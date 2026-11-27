# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import unittest
import numpy as np
from ac9_cloth_sim.core import draft as D, spec as S, iline as IL, extend as EX, point_connection as PC


class Extension(unittest.TestCase):
    def fixture(self, points=None):
        sp=S.new_spec('extension')
        D.add_polygon(sp,points or [[0,0],[100,0],[100,80],[0,80]],name='P')
        self.assertEqual(D.problems(sp,['P']),[])
        return sp

    def test_single_and_save(self):
        sp=self.fixture();before=S.dumps(sp)
        out,names,lines=EX.plan(sp,{'P':['E1']},[0,-10],twins=False)
        self.assertEqual(S.dumps(sp),before)
        pc=D.piece(out,'P');self.assertEqual(len(pc['edges']),6)
        self.assertEqual(D.signed_area(S.Outline(pc).B),9000)
        it=D.internal(pc,lines['P'][0]);np.testing.assert_allclose(it['pts'],[[0,0],[100,0]])
        self.assertEqual(PC.corner(pc,it,0),0)
        reread=S.loads(S.dumps(out));self.assertEqual(S.dumps(reread),S.dumps(out))

    def test_disjoint_and_wrap(self):
        sp=self.fixture([[0,0],[35,0],[35,5],[65,5],[65,0],[100,0],[100,80],[0,80]])
        out,_,lines=EX.plan(sp,{'P':['E1','E5']},[0,-10],twins=False)
        self.assertEqual(len(D.piece(out,'P')['edges']),12)
        self.assertEqual(len(lines['P']),2)
        self.assertEqual(D.signed_area(S.Outline(D.piece(out,'P')).B),8550)
        out,_,_=EX.plan(self.fixture(),{'P':['E4','E1']},[-10,-10],twins=False)
        self.assertEqual(len(D.piece(out,'P')['edges']),6)
        self.assertEqual(D.signed_area(S.Outline(D.piece(out,'P')).B),9800)

    def test_curve_translation_and_old_shape(self):
        sp=self.fixture();edge=D.piece(sp,'P')['edges'][0]
        edge['ctrl']=[[25,-10],[75,-10]];D.refresh_curve(edge)
        old=copy.deepcopy(edge)
        out,_,lines=EX.plan(sp,{'P':['E1']},[3,-15],twins=False)
        new=D.piece(out,'P')['edges'][1]
        np.testing.assert_allclose(np.asarray(new['ctrl'])-old['ctrl'],[[3,-15],[3,-15]])
        it=D.internal(D.piece(out,'P'),lines['P'][0])
        np.testing.assert_allclose(IL.shape(it)[1][0],old['ctrl'])
        np.testing.assert_allclose(it['pts'],old['pts'])

    def seam(self):
        sp=self.fixture();D.add_polygon(sp,[[200,0],[300,0],[300,80],[200,80]],name='Q')
        sp['seams']=[{'id':'S1','a':{'piece':'P','from':['E1',.8],'to':['E2',.2],'dir':1},
                      'b':{'piece':'Q','from':['E1',0],'to':['E1',.72],'dir':1},'enabled':True}]
        return sp

    def test_seam_on_moves_and_adds_side_length(self):
        sp=self.seam();out,_,_=EX.plan(sp,{'P':['E1']},[0,-10],True,twins=False)
        self.assertEqual(len(out['seams']),1)
        self.assertAlmostEqual(S.side_length(out,out['seams'][0]['a']),46,places=5)

    def test_seam_off_keeps_material_and_partner_ratio(self):
        sp=self.seam();sp['folds']=[{'seam':'S1','deg':45}]
        out,_,_=EX.plan(sp,{'P':['E1']},[0,-10],False,twins=False)
        self.assertEqual(len(out['seams']),2)
        self.assertEqual(len(out['folds']),2)
        self.assertIn('line',out['seams'][0]['a'])
        lengths=[(S.side_length(out,s['a']),S.side_length(out,s['b'])) for s in out['seams']]
        np.testing.assert_allclose(lengths,[[20,40],[16,32]],atol=.0002)
        P=np.vstack([S.side_polyline(out,s['a']) for s in out['seams']])
        self.assertGreaterEqual(P[:,1].min(),0)

    def test_existing_internal_nodes_stay_and_detach_only_removed_corner(self):
        sp=self.fixture([[0,0],[50,0],[100,0],[100,80],[0,80]])
        lid=IL.add_shape(sp,'P',[[0,0],[50,0],[50,30]],[None,None],closed=False)
        it=D.internal(D.piece(sp,'P'),lid);PC.bind(D.piece(sp,'P'),lid,0);PC.bind(D.piece(sp,'P'),lid,1)
        old=copy.deepcopy(it['pts'])
        out,_,_=EX.plan(sp,{'P':['E1','E2']},[0,-10],twins=False)
        pc=D.piece(out,'P');it=D.internal(pc,lid)
        self.assertEqual(it['pts'],old)
        self.assertIsNotNone(PC.corner(pc,it,0));self.assertIsNone(PC.corner(pc,it,1))
        self.assertIs(it['boundary_nodes']['1'],False)

    def test_invalid_and_multiple_pieces_are_atomic(self):
        sp=self.fixture();D.add_polygon(sp,[[200,0],[300,0],[300,80],[200,80]],name='Q')
        before=S.dumps(sp)
        for selected,d in [({'P':['E1']},[0,10]),({'P':['E1','E2','E3','E4']},[0,-10]),
                           ({'P':['E1'],'Q':['E3']},[0,-10]),({'P':['E1']},[0,0]),
                           ({'P':['E1','E3']},[0,-10])]:
            with self.assertRaises(ValueError):EX.plan(sp,selected,d,twins=False)
            self.assertEqual(S.dumps(sp),before)

    def test_twins_and_explicit_direction_conflict(self):
        sp=self.fixture();other=D.mirror_copy(sp,'P')
        before=S.dumps(sp)
        out,names,_=EX.plan(sp,{'P':['E1']},[0,-10])
        self.assertEqual(set(names),{'P',other})
        self.assertEqual(S.dumps(sp),before)
        self.assertEqual(len(D.piece(out,other)['internals']),1)
        with self.assertRaises(ValueError):
            EX.plan(sp,{'P':['E1'],other:[D.piece(sp,other)['edges'][-1]['id']]},[5,-10])

    def test_notches_measures_elastic_and_zipper_follow_boundary(self):
        sp=self.seam();pc=D.piece(sp,'P')
        pc['notches']=[{'id':'N1','edge':'E1','t':.5,'kind':'slit'}]
        sp['measure']={'pattern':[{'name':'m','a':{'piece':'P','edge':'E1','t':.5},'b':{'piece':'P','edge':'E3','t':.5}}]}
        sp['elastic']=[{'piece':'P','side':copy.deepcopy(sp['seams'][0]['a']),'ratio':.8}]
        sp['zippers']=[{'id':'Z1','a':{'piece':'P','edges':['E2','E1']},'b':{'piece':'Q','edges':['E1']}}]
        out,_,_=EX.plan(sp,{'P':['E1']},[0,-10],twins=False)
        self.assertEqual(D.piece(out,'P')['notches'],pc['notches'])
        self.assertEqual(out['measure'],sp['measure'])
        self.assertEqual(len(out['zippers'][0]['a']['edges']),3)
        self.assertAlmostEqual(S.side_length(out,out['elastic'][0]['side']),46,places=4)

    def test_two_sides_and_reverse_seams_preserve_their_correspondence(self):
        sp=self.seam()
        sd=sp['seams'][0]['a'];sd['from'],sd['to']=sd['to'],sd['from'];sd['dir']=-1
        out,_,_=EX.plan(sp,{'P':['E1'],'Q':['E1']},[0,-10],twins=False)
        self.assertEqual(len(out['seams']),2)
        self.assertAlmostEqual(sum(S.side_length(out,s['a']) for s in out['seams']),36,places=4)
        self.assertAlmostEqual(sum(S.side_length(out,s['b']) for s in out['seams']),72,places=4)
        self.assertTrue(all('line' in s['b'] for s in out['seams']))

    def test_unrelated_seam_is_byte_identical(self):
        sp=self.seam();sp['seams'][0]['a']['piece']='Q'
        before=copy.deepcopy(sp['seams'])
        out,_,_=EX.plan(sp,{'P':['E1']},[0,-10],twins=False)
        self.assertEqual(out['seams'],before)

    def test_retained_segment_crossing_is_detected_between_midpoints(self):
        sp=self.fixture([[0,0],[80,0],[84,10],[88,0],[120,0],[120,80],[0,80]])
        before=S.dumps(sp)
        with self.assertRaises(ValueError):
            EX.plan(sp,{'P':['E1','E2','E3','E4']},[-20,-4],twins=False)
        self.assertEqual(before,S.dumps(sp))

    def test_new_mirror_line_identity_when_old_line_coincides(self):
        from ac9_cloth_sim.core import internal_sync as IS
        sp=self.fixture();D.add_internal(sp,'P',[[0,0],[100,0]],mirror=False)
        other=D.mirror_copy(sp,'P')
        out,_,lines=EX.plan(sp,{'P':['E1']},[0,-10])
        self.assertEqual(IS.match(out,'P',lines['P'][0]),(other,lines[other][0]))
        self.assertIn('mirror_id',D.internal(D.piece(out,'P'),lines['P'][0]))

    def test_legacy_mirror_identity_survives_new_coincident_line(self):
        from ac9_cloth_sim.core import internal_sync as IS
        sp=self.fixture();old=D.add_internal(sp,'P',[[0,0],[100,0]],mirror=False)
        other=D.mirror_copy(sp,'P')
        for pc in sp['pieces']:
            for it in pc['internals']:it.pop('mirror_id',None)
        peer=IS.match(sp,'P',old)
        out,_,lines=EX.plan(sp,{'P':['E1']},[0,-10])
        out=S.loads(S.dumps(out))
        self.assertEqual(IS.match(out,'P',old),peer)
        self.assertEqual(IS.match(out,*peer),('P',old))
        self.assertEqual(IS.match(out,'P',lines['P'][0]),(other,lines[other][0]))


if __name__=='__main__':unittest.main()
