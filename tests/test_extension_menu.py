# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import unittest
from types import SimpleNamespace as NS
import bpy
from ac9_cloth_sim import draft_tool as T, store, pattern_view as PV
from ac9_cloth_sim.core import draft as D, spec as S, tool_policy as TP


class ExtensionMenu(unittest.TestCase):
    def setUp(self):
        for ob in list(bpy.data.objects):bpy.data.objects.remove(ob,do_unlink=True)
        T._esel.clear();T._csel.clear();T._psel.clear();T._span_sel.clear()
        T._isel.update(piece=None,line=None);T._imore.clear()
        self.sp=S.new_spec('extension menu')
        for name,x in [('P',0),('Q',200)]:D.add_polygon(self.sp,[[x,0],[x+100,0],[x+100,80],[x,80]],name=name)
        self.lid=D.add_internal(self.sp,'P',[[20,20],[50,20]],mirror=False)

    def test_multi_edge_hit_and_unselected_hit(self):
        T._esel[:]=[('P','E1'),('Q','E1')]
        T._isel.update(piece='P',line=self.lid)
        row=T.extension_row(self.sp,('edge','P','E1',.2),'EDIT_PATTERN')
        self.assertEqual(row[1],TP.EXTENSION['operator'])
        self.assertEqual(json.loads(row[3]['items']),[['edge','P','E1'],['edge','Q','E1']])
        self.assertEqual(row[3]['ignored'],1)
        row=T.extension_row(self.sp,('edge','P','E3',.2),'EDIT_PATTERN')
        self.assertEqual(json.loads(row[3]['items']),[['edge','P','E3']])

    def test_other_tools_and_targets_have_no_extension(self):
        for hit in [('piece','P'),('corner','P',0),('iline','P',self.lid,0)]:
            self.assertIsNone(T.extension_row(self.sp,hit,'EDIT_PATTERN'))
        for tool in ['DRAW','TRANSFORM_PATTERN','EDIT_SEAMS']:
            self.assertIsNone(T.extension_row(self.sp,('edge','P','E1',.3),tool))
        prop=bpy.ops.ac9_cloth_sim.extend_outline.get_rna_type().properties['move_seams']
        self.assertFalse(prop.default)

    def test_axis_execution_and_existing_defaults(self):
        props=bpy.ops.ac9_cloth_sim.extend_outline.get_rna_type().properties
        self.assertEqual(props['method'].default,'XY');self.assertEqual(props['axis'].default,'Y')
        store.new_text(bpy.context,'axis RNA test')
        store.text(bpy.context).write(S.dumps(self.sp));store.invalidate()
        PV.rebuild(bpy.context,*store.get(bpy.context))
        result=bpy.ops.ac9_cloth_sim.extend_outline(items=json.dumps([['edge','P','E1'],['edge','P','E3']]),method='AXIS',axis='Y',distance=.01)
        self.assertEqual(result,{'FINISHED'})
        pc=D.piece(store.get(bpy.context)[0],'P')
        self.assertEqual(len(pc['edges']),8);self.assertEqual(len(pc['internals']),3)
        self.assertEqual(min(p[1] for p in D.corners(pc)),-10)
        self.assertEqual(max(p[1] for p in D.corners(pc)),90)

    def test_changed_document_rejects_stale_preview(self):
        store.new_text(bpy.context,'extension owner')
        store.text(bpy.context).write(S.dumps(self.sp));store.invalidate()
        PV.rebuild(bpy.context,*store.get(bpy.context))
        _,_,owner=store.begin_edit(bpy.context)
        changed=copy.deepcopy(self.sp);D.piece(changed,'P')['label']='changed'
        store.text(bpy.context).clear();store.text(bpy.context).write(S.dumps(changed));store.invalidate()
        op=NS(_owner=owner,_before=self.sp,move_x=0.,move_y=-.01,move_seams=False,
              _selection=lambda:{'P':['E1']})
        with self.assertRaises(store.EditChanged):T.AC9_OT_SimExtendOutline._result(op,bpy.context)
        self.assertEqual(store.text(bpy.context).as_string(),S.dumps(changed))


if __name__=='__main__':unittest.main()
