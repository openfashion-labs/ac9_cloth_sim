# SPDX-License-Identifier: GPL-3.0-or-later
"""Executable symmetry contracts for the remaining Edit Pieces menu commands."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from symmetry_fixture import fixture,MODES,outline_error,assert_valid
from ac9_cloth_sim.core import draft as D,iline as IL


class EditSymmetry(unittest.TestCase):
    def test_outline_edits(self):
        for mode in MODES:
          for action in ('split','join','curve','straight','smooth','sharp','notch','delete_notch'):
           with self.subTest(mode=mode,action=action):
            sp,n,_,_=fixture(mode)
            for p in sp['pieces']:p['internals']=[]
            if action in ('split','join'):
                D.split_edge(sp,n,'E2',.3)
                if action=='join':
                    pc=D.piece(sp,n);k=next(i for i,e in enumerate(pc['edges']) if e['pts'][0]==[400.,150.])
                    D.join_at_corner(sp,n,k)
            elif action in ('curve','straight'):
                D.make_curve(sp,n,'E2')
                if action=='straight':D.make_straight(sp,n,'E2')
            elif action in ('smooth','sharp'):
                D.set_smooth(sp,n,1,True)
                if action=='sharp':D.set_smooth(sp,n,1,False)
            else:
                ids=D.add_notch(sp,n,'E2',.3)
                if action=='delete_notch':D.remove_notch(sp,n,ids[n])
                expected=(2 if mode in ('self','both_x','both_y') else 1) if action=='notch' else 0
                self.assertTrue(all(len(p.get('notches',[]))==expected for p in sp['pieces']))
            assert_valid(sp);self.assertLess(outline_error(sp,n),.002)
            for p in sp['pieces']:
                if p.get('mirror_edit'):self.assertIsNotNone(D.self_mirror_map(p))

    def test_internal_edits(self):
        for mode in MODES:
          for action in ('split','delete_node','curve','straight','smooth','sharp','move','rotate','scale','hole','fill','delete'):
           with self.subTest(mode=mode,action=action):
            sp,n,l,targets=fixture(mode,closed=True)
            if action=='split':IL.insert_node(sp,n,l,[85,100])
            elif action=='delete_node':IL.delete_node(sp,n,l,1)
            elif action in ('curve','straight'):
                IL.toggle_curve(sp,n,l,[85,100])
                if action=='straight':IL.toggle_curve(sp,n,l,[85,100])
            elif action in ('smooth','sharp'):
                IL.set_smooth(sp,n,l,1,True)
                if action=='sharp':IL.set_smooth(sp,n,l,1,False)
            elif action=='move':D.move_internal(sp,n,l,'all',[10,5])
            elif action=='rotate':D.transform_group(sp,[],[(n,l)],deg=17)
            elif action=='scale':D.transform_group(sp,[],[(n,l)],k=(1.1,1.1))
            elif action in ('hole','fill'):
                D.set_hole(sp,n,l,True)
                if action=='fill':D.set_hole(sp,n,l,False)
                self.assertTrue(all(bool(D.internal(D.piece(sp,p),ll).get('hole'))==(action=='hole') for p,ll in targets))
            else:
                D.delete_internal(sp,n,l)
                self.assertFalse([it for p in sp['pieces'] for it in p['internals']]);continue
            mirrored=D.mirror_lines(sp,n,l)
            self.assertEqual(len(mirrored)+1,len(targets))
            assert_valid(sp)

    def test_parallel_and_whole_transforms(self):
        for mode in MODES:
          for action in ('parallel','move','rotate','scale','symmetrize_LEFT','symmetrize_RIGHT'):
           with self.subTest(mode=mode,action=action):
            sp,n,l,targets=fixture(mode)
            if action=='parallel':
                P=D.offset_internal(D.piece(sp,n),l,10)
                new=D.add_internal(sp,n,P)
                self.assertEqual(len(D.mirror_lines(sp,n,new))+1,len(targets))
            else:
                for p in sp['pieces']:p['internals']=[]
                if action=='move':D.move_piece(sp,n,shift=(11,8))
                elif action=='rotate':D.move_piece(sp,n,deg=17)
                elif action=='scale':D.scale_piece(sp,n,1.1)
                else:D.symmetrize(sp,n,keep=action.split('_')[1])
            self.assertLess(outline_error(sp,n),.002)


if __name__=='__main__':unittest.main(argv=['test_edit_symmetry'])
