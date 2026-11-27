# SPDX-License-Identifier: GPL-3.0-or-later
"""Input boundaries and drawing state do not depend on a particular font/theme."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import time
import unittest

from ac9_cloth_sim.core.edit_text import Text

TREE=ast.parse((Path(__file__).resolve().parents[1]/'edit_window.py').read_text(encoding='utf-8'))
NODES=[n for n in TREE.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ('hit','intersect','Window')]


class Boundaries(unittest.TestCase):
    def model(self):
        state={'blend':'ADDITIVE','depth':'LESS_EQUAL'}
        gpu=NS(state=NS(blend_get=lambda:state['blend'],depth_test_get=lambda:state['depth'],
                        blend_set=lambda v:state.update(blend=v),depth_test_set=lambda v:state.update(depth=v)))
        context=NS(window=object(),area=object(),region=NS(x=0,y=0))
        ns=dict(bpy=NS(context=context),gpu=gpu,time=time,tx=lambda s:s,
                blf=NS(dimensions=lambda font,s:(len(s),1),size=lambda *a:None),Text=Text,canvas=lambda a,r:(0,0,400,500))
        exec(compile(ast.Module(body=NODES,type_ignores=[]),'edit_window.py','exec'),ns)
        w=ns['Window'].__new__(ns['Window'])
        w.window=context.window;w.area=context.area;w.region=context.region
        w.geometry=lambda c:None;w.bounds=(10,10,100,100);w.body=(10,30,100,60)
        w.title=(10,90,100,20);w.apply=(10,10,30,20);w.cancel=(60,10,30,20)
        w.rows=[];w.drag=w.selecting=w.edit=None;w.edit_key=None;w.blocked=True
        w.scale=1;w.width=100;w.height=100;w.closed=False;w.shader=object();w.font=1
        w.draw_ms=[];w.draw_error='';w.op=NS(bl_label='test');w.commit=lambda c:True
        w.box=lambda *a,**kw:None;w.text=lambda *a:None
        w._colors=lambda c:{k:(.1,.1,.1,1) for k in ('bg','field','selected','text','outline','radio','radio_sel','toggle','toggle_sel','field_sel','field_text','field_text_sel','button','button_text','button_text_sel','radio_text','radio_text_sel')}
        return w,context,state

    def event(self,typ,x=200,y=200):
        return NS(type=typ,value='PRESS',mouse_x=x,mouse_y=y,ctrl=False,oskey=False,shift=False,unicode='')

    def test_header_sidebar_and_other_area_keys_do_not_apply_or_cancel(self):
        w,c,_=self.model()
        for typ in ['RET','ESC']:
            self.assertIsNone(w.event(c,self.event(typ),canvas_event=False))
            other=NS(window=c.window,area=object(),region=c.region)
            self.assertIsNone(w.event(other,self.event(typ),canvas_event=True))

    def test_valid_commit_on_apply_does_not_use_the_old_preview_block(self):
        w,c,_=self.model();self.assertTrue(w.blocked)
        self.assertEqual(w.event(c,self.event('LEFTMOUSE',20,20),canvas_event=True),'APPLY')

    def test_first_escape_leaves_the_field_second_cancels_the_operation(self):
        w,c,_=self.model();w.edit=Text('2cm');w.edit_key=('distance',-1)
        self.assertEqual(w.event(c,self.event('ESC'),canvas_event=True),'EDIT')
        self.assertIsNone(w.edit)
        self.assertEqual(w.event(c,self.event('ESC'),canvas_event=True),'CANCEL')

    def test_scrolled_number_and_edit_clip_stop_at_body_edge(self):
        for editing in (False,True):
            w,c,_=self.model();w.body=(10,30,100,55)
            f=NS(key=('distance',-1),prop=NS(type='FLOAT'),label='Width',display=lambda c:'10.0 mm')
            w.rows=[dict(kind='FIELD',field=f,rect=(10,80,100,20),enabled=True)]
            if editing:w.edit=Text('10.0 mm');w.edit_key=f.key;w.text_offset=0
            calls=[];w.text=lambda x,y,value,color,clip:calls.append((value,clip))
            w.draw()
            values=[clip for value,clip in calls if value in ('Width','10.0 mm')]
            self.assertTrue(values)
            for a,b,width,height in values:self.assertLessEqual(b+height,85)

    def test_radio_uses_its_theme_text_instead_of_regular_label_text(self):
        w,c,_=self.model();f=NS(get=lambda:'WIDTH',key=('method',-1))
        w.rows=[dict(kind='ENUM',field=f,extra=('XY','X/Y'),rect=(10,40,100,20),enabled=True,lines=['X/Y'])]
        colors=w._colors(c);colors['text']=(.1,.1,.1,1);colors['radio_text']=(.85,.85,.85,1)
        w._colors=lambda c:colors;calls=[]
        w.text=lambda x,y,value,color,clip:calls.append((value,color))
        w.draw()
        self.assertEqual(next(color for value,color in calls if value=='X/Y'),colors['radio_text'])

    def test_wrapped_enum_choices_share_a_row_height_and_use_user_font_size(self):
        w,c,_=self.model();del w.geometry
        c.preferences=NS(system=NS(ui_scale=1),ui_styles=[NS(widget=NS(points=11))])
        w.error='';w.position=None;w.scroll=0
        field=NS(key=('side',-1),prop=NS(type='ENUM'),label='')
        layout=NS(active=lambda:True)
        w.items=[('ENUM',layout,field,('A','short')),('ENUM',layout,field,('B','long '*100))]
        w.geometry(c)
        self.assertEqual(w.font_size,11)
        self.assertEqual(w.rows[0]['rect'][1],w.rows[1]['rect'][1])
        self.assertEqual(w.rows[0]['h'],w.rows[1]['h'])
        self.assertGreater(w.rows[0]['h'],w.line)

    def test_drawing_restores_blend_and_depth_in_success_and_failure(self):
        for fail in [False,True]:
            w,c,state=self.model();before=state.copy()
            if fail:
                def broken(*args,**kwargs):raise RuntimeError('GPU probe failure')
                w.box=broken
            w.draw()
            self.assertEqual(state,before)
            self.assertEqual(bool(w.draw_error),fail)


if __name__=='__main__':unittest.main()
