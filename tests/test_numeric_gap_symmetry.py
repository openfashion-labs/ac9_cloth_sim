# SPDX-License-Identifier: GPL-3.0-or-later
"""Absolute gaps use the response of both sides of a symmetric edit.

Run: blender -b --factory-startup --python tests/test_numeric_gap_symmetry.py -- <package parent> <out.json>
Artificial 2D fixtures: each is checked before editing; no cloth solver is involved.
"""
import inspect
import json
import sys
import time
import unittest
from pathlib import Path
import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, ARGS[0])
import ac9_cloth_sim
from ac9_cloth_sim import draft_tool as DT, store as ST, pattern_view as PV
from ac9_cloth_sim.core import draft as D, spec as S

RESULTS = []
Proxy = type('GapProbe', (), {k:v for k,v in DT.AC9_OT_SimSetGap.__dict__.items() if inspect.isfunction(v)})

def setUpModule():
    ac9_cloth_sim.register()

def rectangle(sym=True):
    sp = S.new_spec('Known gap fixture')
    nm = D.add_polygon(sp, [[0,0],[125.2,0],[125.2,150],[0,150]], 'Rectangle')
    D.piece(sp,nm)['mirror_edit'] = sym
    return sp,nm

def setup(sp,nm,kind,thing,ray,target):
    pc = D.piece(sp,nm)
    assert np.isfinite(D.corners_all(pc)).all() and not D.problems(sp,[nm])
    assert not pc.get('mirror_edit') or D.symmetric_edit(pc) is not None
    op = Proxy()
    op.kind,op.piece,op.thing = kind,nm,thing
    op.move_x=op.move_y=0.0
    op.size0=[0,0]
    op.width=op.height=0.0
    gl=DT.gap_list(sp,op._g(sp))
    op.rays=[-1]*DT.GAP_MAX
    op.gaps=[0.0]*DT.GAP_MAX
    for k,(_,_,h,i) in enumerate(gl):
        op.rays[k],op.gaps[k]=i,h/1000
    op.gaps0=op.gaps.copy()
    k=op.rays.index(ray)
    op.gaps[k]=target/1000
    return op,k

def actual(sp,op):
    if ST.text(bpy.context) is None:
        ST.new_text(bpy.context,'Gap fixture')
    ST.text(bpy.context).clear()
    ST.text(bpy.context).write(S.dumps(sp))
    ST.invalidate()
    ss,oo=ST.get(bpy.context)
    PV.rebuild(bpy.context,ss,oo)
    ret=bpy.ops.ac9_cloth_sim.set_gap(kind=op.kind,piece=op.piece,thing=op.thing,
        gaps=op.gaps,gaps0=op.gaps0,rays=op.rays,move_x=op.move_x,move_y=op.move_y)
    ST.invalidate()
    return ret,ST.get(bpy.context)[0]

class GapSymmetry(unittest.TestCase):
    def check_gap(self,sp,op,ray,target):
        frozen=S.dumps(sp)
        t=time.perf_counter()
        delta=op._delta(sp)
        elapsed=(time.perf_counter()-t)*1000
        self.assertEqual(frozen,S.dumps(sp))
        preview,_=op._apply(sp,delta,light=True)
        ret,after=actual(sp,op)
        self.assertEqual(ret,{'FINISHED'})
        h=op._hit(after,ray)[0]
        self.assertAlmostEqual(h,target,delta=.001)
        np.testing.assert_allclose(D.corners_all(D.piece(preview,op.piece)),
                                   D.corners_all(D.piece(after,op.piece)),atol=.00011)
        self.assertFalse(D.problems(after,[op.piece]))
        if D.piece(sp,op.piece).get('mirror_edit'):
            self.assertIsNotNone(D.symmetric_edit(D.piece(after,op.piece)))
        RESULTS.append({'case':self.id(),'target_mm':target,'actual_mm':h,'delta_ms':elapsed,'fixture':'ok'})

    def test_rectangle_expand_shrink_decimal_and_off(self):
        for sym in (False,True):
            for target in (180,180.35,100,30):
                with self.subTest(sym=sym,target=target):
                    sp,nm=rectangle(sym)
                    op,k=setup(sp,nm,'EDGE',D.piece(sp,nm)['edges'][1]['id'],0,target)
                    self.check_gap(sp,op,0,target)

    def test_rotated_axis(self):
        for angle in (27,63,90):
            sp,nm=rectangle()
            D.transform_group(sp,[nm],[],float(np.radians(angle)),(1.,1.),(0.,0.))
            op,k=setup(sp,nm,'EDGE',D.piece(sp,nm)['edges'][1]['id'],0,180)
            self.check_gap(sp,op,0,180)

    def test_internal_reference_fixed_and_mirrored(self):
        for reference in ('outline','paired_line','center_line'):
            sp,nm=rectangle()
            # Add without auto mirroring to explicitly control the known references.
            lid=D.add_internal(sp,nm,[[90,30],[90,120]],mirror=False)
            if reference=='paired_line':
                D.add_internal(sp,nm,[[35.2,30],[35.2,120]],mirror=False)
            elif reference=='center_line':
                D.add_internal(sp,nm,[[62.6,20],[62.6,130]],mirror=False)
            # Open vertical line: normal ray 0 points left.
            op,k=setup(sp,nm,'ILINE',lid,0,20)
            self.check_gap(sp,op,0,20)

    def test_unchanged_and_xy_only(self):
        sp,nm=rectangle()
        op,k=setup(sp,nm,'EDGE',D.piece(sp,nm)['edges'][1]['id'],0,125.2)
        np.testing.assert_allclose(op._delta(sp),[0,0],atol=1e-8)
        op.move_x=.01
        np.testing.assert_allclose(op._delta(sp),[10,0],atol=1e-8)

    def test_native_length_precision(self):
        p=bpy.ops.ac9_cloth_sim.set_gap.get_rna_type().properties['gaps']
        self.assertEqual(p.precision,4)
        self.assertEqual(p.unit,'LENGTH')

    def test_inconsistent_gaps_are_not_applied(self):
        sp,nm=rectangle()
        lid=D.add_internal(sp,nm,[[90,30],[90,120]],mirror=False)
        op,k=setup(sp,nm,'ILINE',lid,0,20)
        op.gaps[op.rays.index(1)]=.020
        with self.assertRaises(ValueError):
            op._delta(sp)

    def test_dialog_cached_draw_cost(self):
        sp,nm=rectangle()
        op,k=setup(sp,nm,'EDGE',D.piece(sp,nm)['edges'][1]['id'],0,180)
        if ST.text(bpy.context) is None:
            ST.new_text(bpy.context,'Gap fixture')
        ST.text(bpy.context).clear()
        ST.text(bpy.context).write(S.dumps(sp))
        ST.invalidate()
        ST.get(bpy.context)
        class Layout:
            def column(self,**kwargs): return self
            def label(self,**kwargs): pass
            def prop(self,*args,**kwargs): pass
        op.layout=Layout()
        op.items=''
        op.as_pointer=lambda:123456
        DT._ghost['live']=123456
        DT._ghost['key']=None
        t=time.perf_counter()
        op.draw(bpy.context)
        cold=(time.perf_counter()-t)*1000
        samples=[]
        for _ in range(30):
            t=time.perf_counter()
            op.draw(bpy.context)
            samples.append((time.perf_counter()-t)*1000)
        self.assertFalse(DT._ghost['why'])
        RESULTS.append({'case':self.id(),'cold_draw_ms':cold,
                        'warm_draw_median_ms':float(np.median(samples)),
                        'warm_draw_max_ms':max(samples)})
        DT.clear_ghost()

if __name__=='__main__':
    res=unittest.main(argv=['x'],exit=False,verbosity=2).result
    Path(ARGS[1]).write_text(json.dumps({'ok':res.wasSuccessful(),'tests':res.testsRun,'results':RESULTS,
        'failures':[x[1] for x in res.failures],'errors':[x[1] for x in res.errors]},indent=2),encoding='utf-8')
    sys.exit(0 if res.wasSuccessful() else 1)
