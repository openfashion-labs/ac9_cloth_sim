# SPDX-License-Identifier: GPL-3.0-or-later
"""Working seams survive display switches; 3D and reference seams keep their own gates."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import patch
import bpy
import numpy as np
from ac9_cloth_sim import overlay as O, toolbar as TB, text_band


class Shader:
    def bind(self):pass
    def uniform_float(self,*a):pass


class SewingVisibility(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.draws=[];self.labels=[]
        self.p=NS(show_overlays=True,show_overlay=True,show_pattern_seams_always=False,
                  show_lengths=False,show_names=False,show_guides=False,show_internal_3d=False)
        self.ctx=NS(scene=NS(ac9_cloth_sim=self.p),region=NS(width=500,height=500),
                    region_data=NS(view_matrix=np.eye(4),is_perspective=False))
        self.g=dict(pos=[],col=[],apos=[],guides=[],placed=[],join=[],fills={})
        def bind(obj,key,value):self.stack.enter_context(patch.object(obj,key,value))
        bind(O,'bpy',NS(context=self.ctx))
        bind(O,'gpu',NS(state=NS(blend_set=lambda *a:None,depth_test_set=lambda *a:None,point_size_set=lambda *a:None),
                        shader=NS(from_builtin=lambda *a:Shader())))
        for name in ('_draw_gone_view','_draw_gone_px','_draw_hidden_px','_draw_frames_px'):
            bind(O,name,lambda *a:None)
        bind(O.store,'get',lambda *a:({'pieces':[]},{}));bind(O.PV,'layout_empty',lambda *a:True)
        bind(O.store,'active_seam',lambda *a:{'id':'S1'})
        bind(O,'garment_seams',lambda *a:None);bind(O,'notches3d',lambda *a:())
        bind(O,'ilines_picked3d',lambda *a:())
        bind(O,'seams3d',lambda *a:(np.array([[0.,0.,0.],[.1,0.,0.]]),[(1.,1.,1.,1.)]*2,['S1'],'k'))
        bind(O,'_lift',lambda points,*a:np.array(points))
        def batch(key):return NS(draw=lambda *a:self.draws.append(key))
        bind(O,'batch_for_shader',lambda *a,**kw:batch('world'))
        bind(O,'_batch',lambda g,key,*a:batch(key));bind(O,'_build',lambda *a:self.g)
        bind(O,'_geo',dict(alab=[((0.,0.,0.),'A 100'),((0.,0.,0.),'B 100'),((0.,0.,0.),'diff')],labels=[],checks=[]))
        bind(O,'blf',NS(size=lambda *a:None,dimensions=lambda *a:(10.,10.)))
        bind(O,'location_3d_to_region_2d',lambda *a:(30.,30.))
        bind(text_band,'draw',lambda fid,items:self.labels.extend([i[2] for i in items]))
        bind(O,'_batch3',dict(key=None))
    def draw(self,tool,pattern):
        self.draws.clear();self.labels.clear();O._batch3['key']=None
        self.g['pos']=[(0.,0.,0.),(.1,0.,0.)] if pattern else []
        with patch.object(TB,'active_tool',return_value=tool), \
             patch.object(O.VV,'allows_world',return_value=not pattern), \
             patch.object(O.VV,'pattern_names',return_value=frozenset({'P'}) if pattern else frozenset()):
            O._draw_view();O._draw_px()
    def test_edit_and_both_add_tools_keep_working_lines_and_AB_in_all_switch_states(self):
        for tool in ('EDIT_SEAMS','ADD_SEAM','ADD_SEAM_MN'):
            for master in (False,True):
                for seams in (False,True):
                    for always in (False,True):
                        with self.subTest(tool=tool,master=master,seams=seams,always=always):
                            self.p.show_overlays,self.p.show_overlay,self.p.show_pattern_seams_always=master,seams,always
                            self.draw(tool,True)
                            self.assertIn('pos',self.draws)
                            self.assertEqual(self.labels,['A','B'])
    def test_reference_and_world_seams_still_obey_display_switches(self):
        for master in (False,True):
            for seams in (False,True):
                for always in (False,True):
                    self.p.show_overlays,self.p.show_overlay,self.p.show_pattern_seams_always=master,seams,always
                    self.draw('EDIT_PATTERN',True)
                    self.assertEqual('pos' in self.draws,master and always)
                    for tool in ('EDIT_PATTERN','EDIT_SEAMS','ADD_SEAM','ADD_SEAM_MN'):
                        self.draw(tool,False)
                        self.assertEqual('world' in self.draws,master and seams)
    def test_required_pattern_lines_do_not_reenable_hidden_world_guides_or_internal_lines(self):
        self.p.show_overlays=False;self.p.show_guides=True;self.p.show_internal_3d=True
        self.g['guides']=[(0.,0.,0.),(.1,0.,0.)]
        with patch.object(O,'ilines3d',side_effect=AssertionError('hidden world lines requested')) as ilines:
            self.draw('EDIT_SEAMS',False)
            self.assertEqual(self.draws,[])
            ilines.assert_not_called()
    def test_required_seams_do_not_reenable_optional_symmetry_centerlines(self):
        self.g['center']=[(0.,0.,0.),(.1,0.,0.)]
        for master in (False,True):
            self.p.show_overlays=master;self.p.show_overlay=False
            self.draw('EDIT_SEAMS',True)
            self.assertIn('pos',self.draws)
            self.assertNotIn('center',self.draws)


if __name__=='__main__':unittest.main()
