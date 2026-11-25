# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in submitted-file controls: no phantom fold from either shared control side."""
import os,unittest,json
import bpy,numpy as np
from ac9_cloth_sim import store as ST,dress as D,placed_view as PV,placement as PL,place_fold as PF,draft_tool as DT
from ac9_cloth_sim.core import spec as S


@unittest.skipUnless(os.environ.get('AC9_MISSING_LINE_BLEND'),'External submitted fixture required')
class MissingLineFold(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.open_mainfile(filepath=os.environ['AC9_MISSING_LINE_BLEND'],use_scripts=False)
        D.standby_wake=lambda *a,**kw:None
        self.p,self.q,self.l='Pattern_578148','Pattern_578149','I4'
        DT.select_internal(bpy.context,self.p,self.l)
    def points(self,ob):return PV._world_co(ob)*1000.
    def refuse(self):
        sp=ST.get(bpy.context)[0];ob=PV.objects(sp)[self.p];before=self.points(ob)
        self.assertTrue(np.isfinite(before).all());text=ST.text(bpy.context).as_string()
        ids={o.session_uid for o in bpy.data.objects}
        for side in ('A','B'):
            bpy.context.scene.ac9_iline_fold_side=side
            self.assertEqual(bpy.context.scene.ac9_iline_fold_side,'NONE')
            self.assertIn('cannot match',PF.why_not(self.p,self.l))
            self.assertEqual(ST.text(bpy.context).as_string(),text)
            np.testing.assert_array_equal(self.points(PV.objects(ST.get(bpy.context)[0])[self.p]),before)
            self.assertEqual({o.session_uid for o in bpy.data.objects},ids)
    def test_missing_with_149_as_control(self):
        self.assertEqual(PV.objects(ST.get(bpy.context)[0])[self.p][PV.PROP],self.q);self.refuse()
    def test_missing_with_148_as_control(self):
        PV.activate_side(bpy.context,ST.get(bpy.context)[0],self.p)
        self.assertEqual(PV.objects(ST.get(bpy.context)[0])[self.p][PV.PROP],self.p);self.refuse()
    def test_legacy_derived_history_is_not_silently_accepted(self):
        ob=PV.objects(ST.get(bpy.context)[0])[self.p]
        ob[PV.FOLD_PROP]=json.dumps([dict(line='__ac9_mirror_fold__legacy',deg=90.,side=0)])
        self.refuse()
    def test_rebuild_keeps_authored_asymmetry_as_separate_shapes(self):
        sp=ST.get(bpy.context)[0];pieces=S.dumps({'pieces':sp['pieces']})
        PL.rebuild_placement(bpy.context,sp,[self.p,self.q])
        bpy.context.view_layer.update()
        obs=PV.objects(ST.get(bpy.context)[0]);self.assertIsNot(obs[self.p],obs[self.q])
        other=self.points(obs[self.q]).copy();mine=self.points(obs[self.p]).copy()
        other_local=np.array([v.co[:] for v in obs[self.q].data.vertices])
        bpy.context.scene.ac9_iline_fold_side='A'
        self.assertEqual(bpy.context.scene.ac9_iline_fold_side,'A')
        obs=PV.objects(ST.get(bpy.context)[0])
        self.assertGreater(np.linalg.norm(self.points(obs[self.p])-mine,axis=1).max(),1.)
        np.testing.assert_array_equal(np.array([v.co[:] for v in obs[self.q].data.vertices]),other_local)
        np.testing.assert_allclose(self.points(obs[self.q]),other,atol=.0001,rtol=0)
        self.assertEqual(S.dumps({'pieces':ST.get(bpy.context)[0]['pieces']}),pieces)
