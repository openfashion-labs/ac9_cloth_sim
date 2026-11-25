# SPDX-License-Identifier: GPL-3.0-or-later
"""The five right-click counterexamples, including both kinds of mirror together.
Run in Blender: --python this.py -- <package parent> <out.json>.
Synthetic geometry is necessary to give each creation/cut/move a known count and shape.
"""
import copy,json,sys,unittest,traceback,io
from unittest.mock import patch
from pathlib import Path
ARGS=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from ac9_cloth_sim.core import draft as D, spec as S, cut as CU, spread as SP, dart as DA
from symmetry_fixture import fixture,MODES,outline_error,assert_valid,area

ROWS=[]


class CommandSymmetry(unittest.TestCase):
    def test_clone_and_sew_every_counterpart(self):
        for mode in MODES:
          for reverse in (False,True):
           for centered in (False,True):
            for curved in (False,True):
             for sew in (False,True):
              with self.subTest(mode=mode,reverse=reverse,centered=centered,curved=curved,sew=sew):
                sp,n,l,targets=fixture(mode,True,reverse,centered,curved)
                old=len(sp['pieces']);before=copy.deepcopy(sp)
                primary=D.clone_shape(sp,n,l,sew=sew)
                created=sp['pieces'][old:]
                self.assertEqual(len(created),len(targets))
                self.assertIn(primary,[p['name'] for p in created])
                self.assertEqual(len(sp['seams']),4*len(targets) if sew else 0)
                if sew:
                    sewn={(s[k]['piece'],s[k]['line']) for s in sp['seams'] for k in ('a','b') if 'line' in s[k]}
                    self.assertEqual(sewn,set(targets))
                    for s in sp['seams']:
                        self.assertAlmostEqual(S.side_length(sp,s['a']),S.side_length(sp,s['b']),delta=.002)
                for p in created:
                    self.assertEqual(p['fabric'],D.piece(before,n)['fabric'])
                assert_valid(sp)
                ROWS.append(dict(command='clone_sew' if sew else 'clone',mode=mode,reverse=reverse,centered=centered,curved=curved,created=len(created)))

    def test_cut_all_four_lines_and_return_all_halves(self):
        for mode in MODES:
          for reverse in (False,True):
           with self.subTest(mode=mode,reverse=reverse):
            sp,n,l,targets=fixture(mode,reverse=reverse)
            count=len(sp['pieces']);a0=area(sp)
            out=CU.cut(sp,n,l)
            self.assertEqual(len(sp['pieces'])-count,len(targets))
            self.assertAlmostEqual(area(sp),a0,delta=.01)
            self.assertFalse([it for p in sp['pieces'] for it in p['internals']])
            returned={v['new'] for v in out.values()} | {v['new'] for x in out.values() for _,v in x.get('more',[])}
            self.assertEqual(len(returned),len(targets))
            assert_valid(sp)
            for p in sp['pieces']:
                self.assertLess(outline_error(sp,p['name']),.002)
            ROWS.append(dict(command='cut',mode=mode,reverse=reverse,cuts=len(returned)))

    def test_spread_keeps_both_mirrors(self):
        for mode in MODES:
         for reverse in (False,True):
          for method in ('parallel','hinge'):
           for hinge in ('start','end'):
            with self.subTest(mode=mode,reverse=reverse,method=method,hinge=hinge):
                sp,n,l,targets=fixture(mode,reverse=reverse)
                SP.spread(sp,n,l,20,mode=method,hinge=hinge)
                assert_valid(sp)
                self.assertFalse([it for p in sp['pieces'] for it in p['internals']])
                for p in sp['pieces']:
                    if p.get('mirror_edit'):self.assertIsNotNone(D.self_mirror_map(p))
                    self.assertLess(outline_error(sp,p['name']),.002)
                ROWS.append(dict(command='spread',mode=mode,reverse=reverse,method=method,hinge=hinge,error_mm=outline_error(sp,n)))

    def test_central_cut_uses_the_parent_reflection(self):
        for mode in ('pair_x','pair_y','both_x','both_y','self'):
          for reverse in (False,True):
           with self.subTest(mode=mode,reverse=reverse):
            sp,n,l,_=fixture(mode,reverse=reverse,centered=True)
            tw=D.twin_of(sp,n)
            old_M=D.mirror_fn(copy.deepcopy(D.piece(sp,n)),copy.deepcopy(D.piece(sp,tw))) if tw else None
            before_names={p['name'] for p in sp['pieces']}
            out=CU.cut(sp,n,l)
            self.assertEqual(len(sp['pieces']),4 if tw else 2)
            self.assertEqual(len(list(S.twins(sp))),2 if tw else 0)
            if tw:
                source=[n,out[n]['new']];dest=[tw,out[tw]['new']]
                for a in source:
                    b=D.twin_of(sp,a);self.assertIn(b,dest)
                    self.assertLess(float(D._dist_to_polyline(old_M(S.Outline(D.piece(sp,a)).B),S.Outline(D.piece(sp,b)).B).max()),.001)

    def test_reversed_resampled_spread_uses_geometric_direction(self):
        for mode in ('pair_x','pair_y','both_x','both_y'):
         for method in ('parallel','hinge'):
          with self.subTest(mode=mode,method=method):
            sp,n,l,_=fixture(mode)
            peer,tl=D.twin_line(sp,n,l);it=D.internal(D.piece(sp,peer),tl)
            p=np.asarray(it['pts']);it['pts']=[p[-1].tolist(),p.mean(0).tolist(),p[0].tolist()]
            SP.spread(sp,n,l,20,mode=method,hinge='start',side='a')
            self.assertLess(outline_error(sp,n),.002)

    def test_bom_parent_pair_survives_cut_at_fixed_material_positions(self):
        for mode in ('pair_x','pair_y','both_x','both_y'):
         for reverse in (False,True):
          for centered in (False,True):
           with self.subTest(mode=mode,reverse=reverse,centered=centered):
            sp,n,l,targets=fixture(mode,reverse=reverse,centered=centered)
            for p in sp['pieces']:p['bom']={'family':'imported-family','idx':p['name']}
            peer=D.twin_of(sp,n);M=D.mirror_fn(copy.deepcopy(D.piece(sp,n)),copy.deepcopy(D.piece(sp,peer)))
            CU.cut(sp,n,l)
            expected=2 if centered or mode.startswith('pair') else 3
            self.assertEqual(len(S.twins(sp,any_state=True)),expected)
            self.assertEqual(D.twin_of(sp,n),peer)
            self.assertLess(float(D._dist_to_polyline(M(S.Outline(D.piece(sp,n)).B),S.Outline(D.piece(sp,peer)).B).max()),.001)

    def test_later_sewn_line_refuses_without_dangling_seams(self):
        for action in ('cut','spread'):
         for which in (1,3):
          with self.subTest(action=action,which=which):
            sp,n,l,targets=fixture('both_x')
            owner,line=targets[which]
            sp['seams']=[{'id':'blocking','enabled':True,
                'a':{'piece':owner,'line':line,'from':0.,'to':1.},
                'b':{'piece':owner,'from':['E2',0.],'to':['E2',1.],'dir':1}}]
            assert_valid(sp);before=S.dumps(sp)
            with self.assertRaises(ValueError):
                CU.cut(sp,n,l) if action=='cut' else SP.spread(sp,n,l,20)
            self.assertEqual(S.dumps(sp),before)
            assert_valid(sp)

    def test_cut_accepts_existing_pair_tolerance(self):
        for delta in (.0005,.008,.05):
          with self.subTest(delta=delta):
            sp,n,l,_=fixture('pair_x');peer=D.twin_of(sp,n)
            D.move_corners(sp,peer,{0:(delta,0)},twins=False)
            self.assertIsNotNone(D.twin_corner_map(sp,n)[1]);assert_valid(sp)
            CU.cut(sp,n,l)
            self.assertEqual(len(sp['pieces']),4);self.assertEqual(len(S.twins(sp)),2)
            self.assertLess(outline_error(sp,n),D.TWIN_TOL)

    def test_cut_family_does_not_absorb_an_unrelated_pair(self):
        for bom in (False,True):
          with self.subTest(bom=bom):
            sp,n,l,_=fixture('both_x')
            other=D.add_polygon(sp,[[1300,0],[1400,0],[1400,150],[1300,150]],'Other')
            made,_=D.mirror_group(sp,[other]);peer=made[other]
            for name in (other,peer):
                pc=D.piece(sp,name)
                if bom:pc['bom']={'family':'Panel_2'}
                else:pc['family']='Panel_2'
            assert_valid(sp);self.assertEqual(len(S.twins(sp)),2)
            CU.cut(sp,n,l)
            self.assertEqual(len(S.twins(sp)),4)
            self.assertEqual(D.twin_of(sp,other),peer)
            self.assertEqual(len({D.family_of(D.piece(sp,a)) for a,b in S.twins(sp)}),4)

    def test_dart_inverse_maps_both_moving_sectors_and_fixed_material(self):
        for mode in ('both_x','both_y'):
         for true in (False,True):
          with self.subTest(mode=mode,true=true):
            sp,n,_,_=fixture(mode)
            for p in sp['pieces']:p['internals']=[]
            ids,_=DA.add_dart(sp,n,'E2',.3,width=20,length=40)
            markers={}
            for p in sp['pieces']:
                B=S.Outline(p).B;center=D.self_axis(p)[0]
                for x in np.linspace(center[0]-170,center[0]+170,7):
                 for y in np.linspace(B[:,1].min()+30,B[:,1].max()-30,7):
                    Q=np.array([[x,y],[x+.001,y]])
                    if not DA._inside(B,Q).all():continue
                    lid=D.add_internal(sp,p['name'],Q,mirror=False)
                    markers[p['name'],lid]=Q
            _,_,_,maps=DA.move_dart(sp,n,ids[n],'E3',.25,true=true)
            counts={p['name']:[0,0,0] for p in sp['pieces']}
            for (name,lid),oldQ in markers.items():
                newQ=np.asarray(D.internal(D.piece(sp,name),lid)['pts'])
                self.assertLess(float(np.linalg.norm(maps[name](newQ)-oldQ,axis=1).max()),.001)
                changed=np.linalg.norm(newQ-oldQ,axis=1).max()>.01
                k=0 if not changed else 1 if oldQ[0,0]<D.self_axis(D.piece(sp,name))[0][0] else 2
                counts[name][k]+=1
            self.assertTrue(all(all(c>0 for c in row) for row in counts.values()),counts)

    def test_move_dart_keeps_both_mirrors_and_maps(self):
        for mode in MODES:
         for reverse in (False,True):
          for method in ('pivot','shift'):
           with self.subTest(mode=mode,reverse=reverse,method=method):
            sp,n,_,_=fixture(mode,reverse=reverse)
            for p in sp['pieces']:p['internals']=[]
            ids,_=DA.add_dart(sp,'Panel','E2',.3,width=20,length=40)
            assert_valid(sp);self.assertLess(outline_error(sp,n),.002)
            eid,t=('E2',.6) if method=='shift' else ('E3',.25)
            if n!='Panel':
                tw,cmap,_=D.twin_corner_map(sp,'Panel')
                pc,pb=D.piece(sp,'Panel'),D.piece(sp,n)
                k,rev=D._twin_edge(pc,pb,cmap,D.edge_index(pc,eid))
                eid,t=pb['edges'][k]['id'],1-t if rev else t
            out,side,_,maps=DA.move_dart(sp,n,ids[n],eid,t,mode=method)
            assert_valid(sp)
            self.assertEqual(set(maps),set(out))
            for p in sp['pieces']:
                if p.get('mirror_edit'):self.assertIsNotNone(D.self_mirror_map(p))
                self.assertLess(outline_error(sp,p['name']),.002)
            for nm,fn in maps.items():
                Q=np.asarray(fn(S.Outline(D.piece(sp,nm)).B))
                self.assertTrue(np.isfinite(Q).all())
            ROWS.append(dict(command='move_dart',mode=mode,reverse=reverse,method=method,error_mm=outline_error(sp,n)))

    def test_refused_batches_leave_original_unchanged(self):
        for action in ('clone','cut','spread','dart'):
          with self.subTest(action=action):
            sp,n,l,_=fixture('both_x',closed=action=='clone')
            if action=='dart':
                for p in sp['pieces']:p['internals']=[]
                ids,_=DA.add_dart(sp,n,'E2',.3,width=20,length=40);sid=ids[n]
            module,primitive={'clone':(D,'_clone_shape_one'),'cut':(CU,'_cut_one'),
                              'spread':(SP,'_spread_one'),'dart':(DA,'_move_one')}[action]
            original=getattr(module,primitive);calls=[]
            def fail_later(*args,**kwargs):
                calls.append(1)
                if len(calls)==2:raise ValueError('Injected second target refusal')
                return original(*args,**kwargs)
            before=S.dumps(sp)
            with patch.object(module,primitive,fail_later):
                with self.assertRaises(ValueError):
                    {'clone':lambda:D.clone_shape(sp,n,l), 'cut':lambda:CU.cut(sp,n,l),
                     'spread':lambda:SP.spread(sp,n,l,20),'dart':lambda:DA.move_dart(sp,n,sid,'E3',.25)}[action]()
            self.assertEqual(len(calls),2)
            self.assertEqual(S.dumps(sp),before)


if __name__=='__main__':
    import test_edit_symmetry
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(CommandSymmetry)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(test_edit_symmetry))
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    result={'ok':r.wasSuccessful(),'tests':r.testsRun,'conditions':len(ROWS),'rows':ROWS,'log':stream.getvalue()}
    if len(ARGS)>1:Path(ARGS[1]).write_text(json.dumps(result,indent=2),encoding='utf-8')
    sys.exit(0 if r.wasSuccessful() else 1)
