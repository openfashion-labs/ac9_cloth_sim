# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU preview contracts for the three dialogs moved to the shared modal window.
The official runner registers this checkout; actual mouse/Undo is checked in the isolated GUI.
Synthetic drafting outlines are validated before every preview; no cloth physics is evaluated.
"""
import copy
import unittest
from types import MethodType
import bpy
from ac9_cloth_sim import draft_tool as T,store as ST,dart_ui as DA,spread_ui as SP
from ac9_cloth_sim.core import draft as D,spec as S


class ParameterPreviews(unittest.TestCase):
    def fixture(self,kind):
        sp=S.new_spec('preview contract')
        D.add_polygon(sp,[[0,0],[100,0],[100,80],[0,80]],name='Panel')
        lid=D.add_internal(sp,'Panel',[[50,0],[50,80]],mirror=False) if kind=='spread' else None
        if kind=='match':
            D.add_polygon(sp,[[160,0],[240,0],[240,80],[160,80]],name='Other')
            sp['seams']=[dict(id=sid,source='user',enabled=True,
                             a=dict(piece='Panel',**{'from':[eid,0.0],'to':[eid,1.0],'dir':1}),
                             b=dict(piece='Other',**{'from':[eid,0.0],'to':[eid,1.0],'dir':1}))
                         for sid,eid in [('S1','E1'),('S2','E3')]]
        self.assertEqual(D.problems(sp,[p['name'] for p in sp['pieces']]),[])
        self.assertEqual(S.check(sp),[])
        ST.new_text(bpy.context,'preview fixture');ST.text(bpy.context).write(S.dumps(sp));ST.invalidate()
        ST.sync_seam_list(bpy.context,sp)
        if kind=='match':bpy.context.scene.ac9_cloth_sim.seam_index=0
        return sp,lid

    def probe(self,cls,**values):
        q=type('PreviewProbe',(),{})()
        q.__dict__.update(values);q.as_pointer=lambda:id(q)
        for name in ('parameter_preview','_make','_spread','_seam','cancel'):
            if hasattr(cls,name):setattr(q,name,MethodType(getattr(cls,name),q))
        return q

    def check_preview(self,q,expected):
        before=ST.text(bpy.context).as_string();T.ghost_open(q)
        T._parameter_refresh(q,bpy.context)
        self.assertEqual(T._ghost['why'],'')
        self.assertEqual(S.dumps(T._ghost['spec']),S.dumps(expected))
        self.assertEqual(ST.text(bpy.context).as_string(),before)
        q.cancel(bpy.context)
        self.assertIsNone(T._ghost['live']);self.assertIsNone(T._ghost['spec'])

    def test_dart_keeps_existing_plan_and_cancel_does_not_write(self):
        sp,_=self.fixture('dart')
        q=self.probe(DA.AC9_OT_SimAddDart,piece='Panel',edge='E1',t=.5,width=.02,length=.03,true_mouth=True)
        self.check_preview(q,q._make(sp)[0])

    def test_spread_keeps_existing_plan_and_cancel_does_not_write(self):
        sp,lid=self.fixture('spread')
        q=self.probe(SP.AC9_OT_SimSpread,piece='Panel',line=lid,amount=.01,mode='hinge',hinge='start',side='auto')
        self.check_preview(q,q._spread(sp)[0])

    def test_match_keeps_the_opened_seam_when_list_selection_changes(self):
        sp,_=self.fixture('match')
        q=self.probe(T.AC9_OT_SimMatchLength,_parameter_seam_id='S1',side='a',ease=0.,end='to')
        bpy.context.scene.ac9_cloth_sim.seam_index=1
        self.assertEqual(ST.active_seam(bpy.context,sp)['id'],'S2')
        result=q._make(bpy.context,sp)
        self.assertEqual(result[2]['id'],'S1');self.assertAlmostEqual(result[4],80.)
        self.check_preview(q,result[0])

    def test_invalid_spread_preview_blocks_without_writing(self):
        sp,lid=self.fixture('spread');q=self.probe(SP.AC9_OT_SimSpread,piece='Panel',line=lid,amount=-.01,mode='hinge',hinge='start',side='auto')
        before=ST.text(bpy.context).as_string();T.ghost_open(q);T._parameter_refresh(q,bpy.context)
        self.assertTrue(T._ghost['why']);self.assertIsNone(T._ghost['spec'])
        self.assertEqual(ST.text(bpy.context).as_string(),before);q.cancel(bpy.context)


if __name__=='__main__':unittest.main()
