# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression inputs captured from Blender 5.2's overlapping native regions."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

TREE=ast.parse((Path(__file__).resolve().parents[1]/'draft_tool.py').read_text(encoding='utf-8'))


def function(name,env=None):
    ns={} if env is None else env
    node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'draft_tool.py','exec'),ns)
    return ns[name]


class ParameterSession(unittest.TestCase):
    def test_header_enter_is_field_input_even_when_the_canvas_extends_behind_it(self):
        # Actual regions in native_field_fixed.json: canvas reaches y=774,
        # but tool settings occupy y=722..748 and the main header y=748..774.
        regions=[NS(type=t,x=x,y=y,width=w,height=h) for t,x,y,w,h in
                 [('WINDOW',2,79,982,695),('TOOL_HEADER',2,722,982,26),
                  ('HEADER',2,748,982,26),('TOOLS',2,79,56,643),('UI',750,79,234,643)]]
        canvas=function('parameter_canvas');area=NS(regions=regions)
        self.assertTrue(canvas(area,600,350))
        for point in [(380,735),(380,760),(30,350),(800,350),(1200,350)]:
            self.assertFalse(canvas(area,*point),point)

    def test_non_viewport_invocation_is_rejected_before_allocating_any_resource(self):
        for kind in (None,'PROPERTIES','OUTLINER'):
            calls=[];state={'op':None};op=NS(report=lambda *args:calls.append('report'))
            ctx=NS(area=NS(type=kind) if kind else None,window=object(),space_data=NS(type=kind))
            def forbidden(*args,**kwargs):raise AssertionError('resource allocated for non-viewport')
            begin=function('parameter_open',dict(_parameter=state,rpt=lambda s:s,store=NS(edit_owner=forbidden)))
            self.assertEqual(begin(op,ctx),{'CANCELLED'})
            self.assertEqual(calls,['report']);self.assertEqual(state,{'op':None})

    def test_file_load_cleanup_does_not_dereference_the_removed_operator(self):
        class Removed:
            def __getattr__(self,key):raise ReferenceError('RNA was removed')
        op=Removed();timer=object();removed=[];cleared=[]
        space=NS(show_region_tool_header=True)
        state=dict(op=op,action='APPLY',timer=timer,space=space,show_header=False)
        close=function('_parameter_close',dict(_parameter=state,clear_ghost=lambda:cleared.append(True),_redraw_views=lambda:None))
        close(op,NS(window_manager=NS(event_timer_remove=removed.append)))
        self.assertTrue(space.show_region_tool_header)
        close(op,NS(window_manager=NS(event_timer_remove=removed.append)))
        self.assertEqual(removed,[timer]);self.assertEqual(cleared,[True])
        self.assertEqual(state,dict(op=None,action=None,timer=None,space=None,show_header=None,ui=None))


if __name__=='__main__':unittest.main()
