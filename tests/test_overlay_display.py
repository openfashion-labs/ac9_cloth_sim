# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real Scene/RNA bindings and UI control availability in Blender."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
import bpy
from ac9_cloth_sim import display_settings as UI, silhouette
from ac9_cloth_sim.core import overlay_policy as P


class Layout:
    def __init__(self, log, parent=None):
        self.log,self.parent=log,parent
        self.enabled=self.active=True
    def available(self):
        return self.enabled and self.active and (self.parent is None or self.parent.available())
    def sub(self, *a, **k): return Layout(self.log,self)
    row=column=split=sub
    def separator(self, *a, **k): pass
    def label(self, text='', **k): self.log.append(('label',text,self.available()))
    def prop(self, owner, key, **kwargs):
        self.log.append(('prop',key,self.available(),owner.as_pointer(),kwargs.get('text')))
    def operator(self, key, **kwargs): self.log.append(('operator',key,self.available()))


class OverlayDisplay(unittest.TestCase):
    def setUp(self):
        self.p=bpy.context.scene.ac9_cloth_sim
        self.m=bpy.context.scene.ac9_cloth_sim_measure
        self.saved={k:getattr(self.p,k) for k in ('show_overlays','show_piece_colors','show_overlay','show_thickness')}
        self.saved_m=(self.m.show_body,self.m.show)
    def tearDown(self):
        for k,v in self.saved.items():setattr(self.p,k,v)
        self.m.show_body,self.m.show=self.saved_m
    def draw(self, pattern=True):
        log=[]
        with patch.object(silhouette,'pattern_view',return_value=pattern):
            UI.draw(Layout(log),bpy.context)
        return log
    def test_both_entrances_have_identical_controls(self):
        a=self.draw(True);b=self.draw(False)
        self.assertEqual([r[1] for r in a if r[0]=='prop'],[r[1] for r in b if r[0]=='prop'])
        self.assertEqual(len([r for r in a if r[0]=='prop']),17)
        self.assertTrue(any(r[:2]==('label',P.PATTERN_ACTION) for r in b))
    def test_master_off_disables_decoration_settings_but_not_thickness(self):
        self.p.show_overlays=False;self.p.show_thickness=True
        states={r[1]:r[2] for r in self.draw() if r[0]=='prop'}
        self.assertTrue(states['show_overlays'])
        self.assertTrue(states['show_thickness'])
        self.assertTrue(states['thickness_side'])
        self.assertFalse(any(v for k,v in states.items() if k not in ('show_overlays','show_thickness','thickness_side')))
    def test_dependent_controls_follow_their_own_parent(self):
        self.p.show_overlays=True;self.p.show_piece_colors=False;self.p.show_overlay=False;self.p.show_thickness=False
        states={r[1]:r[2] for r in self.draw() if r[0]=='prop'}
        for k in ('show_fabric_colors','thickness_side'):
            self.assertFalse(states[k],k)
        self.assertTrue(states['show_pattern_seams_always'])
        self.assertTrue(states['show_notches'])
    def test_measurement_entrances_share_real_rna(self):
        log=[];UI.draw_measure_visibility(Layout(log),bpy.context)
        side={r[1]:(r[3],r[4]) for r in log if r[0]=='prop'}
        menu={r[1]:(r[3],r[4]) for r in self.draw() if r[0]=='prop' and r[1] in ('show_body','show')}
        self.assertEqual(side,menu)
        for b in (False,True):
            for t in (False,True):
                self.m.show_body=b;self.m.show=t
                self.assertEqual((self.m.show_body,self.m.show),(b,t))
    def test_every_property_has_its_canonical_name(self):
        for e in P.ENTRIES:
            if e['kind']=='OPERATOR':continue
            owner=self.m if e['owner']=='measure' else self.p
            self.assertEqual(owner.bl_rna.properties[e['key']].name,e['label'])
    def test_collision_silhouette_uses_the_actual_sidebar_collision_term(self):
        from ac9_cloth_sim.i18n_ja import JA
        name=self.p.bl_rna.properties['list_tab'].enum_items['OBSTACLES'].name
        self.assertEqual(JA[P.label('show_silhouette_collisions')],JA[name]+'の'+JA['Silhouette'])


if __name__=='__main__':unittest.main()
