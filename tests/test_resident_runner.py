# SPDX-License-Identifier: GPL-3.0-or-later
"""Resident Live (solver/resident_runner.py) without a GPU: which changes keep the model (the structure key), which
statements of newton_vbd.py are the heavy ones a begin skips, and the shapes that are never kept."""
import os, sys, tempfile, types, unittest
from pathlib import Path
import numpy as np

SOLVER = Path(__file__).resolve().parent.parent / 'solver'
sys.path.insert(0, str(SOLVER))
import resident_runner as RR


def K(name):
    return '{"idx": "%s", "avatar": "a", "cfg": {}}' % name


def _npz(path, **arrays):
    np.savez(path, **arrays); return path


class StructureKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = Path(self.tmp.name)
        F = np.array([[0, 1, 2]], np.int32)
        self.idx = str(_npz(d / 'a.idx.npz', F=F, Xinit=np.zeros((3, 3)), Xclo=np.zeros((3, 3))))
        self.idx_moved = str(_npz(d / 'b.idx.npz', F=F, Xinit=np.ones((3, 3)), Xclo=np.ones((3, 3))))
        self.idx_other = str(_npz(d / 'c.idx.npz', F=F[:, ::-1].copy(), Xinit=np.zeros((3, 3)), Xclo=np.zeros((3, 3))))
        self.avatar = str(_npz(d / 'av.npz', V=np.zeros((3, 3))))
        self.cfg = dict(edge_ke=1e-4, edge_kd=1e-8, density=0.14, tri_ke=280.0, tri_ka=280.0, tri_kd=1e-3, tri_ks=26.0,
                        seam_bend={'pairs': 'all', 'ke': 3e-4, 'rest_deg': 0}, grab_file=str(d / 'g1.json'),
                        fabric_pieces={'P': {'D': 5e-5, 'rho': 0.17}}, mi={'D': 6.5e-7, 'ref_mm': 6.0})

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, idx=None, **change):
        c = dict(self.cfg); c.update(change)
        return RR.structure_key(idx or self.idx, self.avatar, c)

    def test_values_and_start_shape_keep_the_model(self):
        base = self.key()
        self.assertEqual(base, self.key(idx=self.idx_moved))                       # another start shape
        self.assertEqual(base, self.key(edge_ke=2e-4, tri_ke=300.0, density=0.2))  # fabric numbers
        self.assertEqual(base, self.key(seam_bend={'pairs': 'all', 'ke': 5e-4, 'rest_deg': 0}))
        self.assertEqual(base, self.key(fabric_pieces={'P': {'D': 6e-5, 'rho': 0.2}}, mi={'D': 7e-7, 'ref_mm': 6.0}))
        self.assertEqual(base, self.key(grab_file='elsewhere.json'))              # where a job's files are

    def test_structure_builds_fresh(self):
        base = self.key()
        self.assertNotEqual(base, self.key(idx=self.idx_other))                    # another mesh
        self.assertNotEqual(base, self.key(tri_ks=30.0))                           # read outside finalize_values
        self.assertNotEqual(base, self.key(fabric_pieces={'P': {'D': 5e-5, 'rho': 0.17, 'ku': 300.0}}))
        self.assertNotEqual(base, self.key(fabric_pieces={'Q': {'D': 5e-5, 'rho': 0.17}}))
        self.assertNotEqual(base, self.key(mi={'D': 0.0, 'ref_mm': 6.0}))            # the block on / off
        self.assertNotEqual(base, self.key(seam_bend={'pairs': 'all', 'ke': 3e-4, 'rest_deg': 10}))

    def test_literal_method_names_do_not_probe_cwd_and_unknown_files_still_invalidate(self):
        from unittest.mock import patch
        with patch.object(RR.os.path,'isfile',side_effect=AssertionError('literal lookup forbidden')):
            self.key(membrane='bw',stitch_ramp_shape='ease_out')
        asset=Path(self.tmp.name)/'input.dat';asset.write_bytes(b'first')
        first=self.key(**{'membrane[plugin]':str(asset)})
        asset.write_bytes(b'second')
        self.assertNotEqual(first,self.key(**{'membrane[plugin]':str(asset)}))


class Statements(unittest.TestCase):
    def test_begin_failure_after_claim_never_retries_fresh(self):
        from unittest.mock import patch,Mock
        runtime=types.SimpleNamespace(claimed=True,closed=False,close=Mock())
        runner=object.__new__(RR.Resident);runner.g={'_NATIVE_RUNTIME':runtime};runner.key='key';runner.last=None;runner.snap={}
        runner._begin=Mock(side_effect=OSError('phase write failed after claim'))
        with patch.object(RR,'structure_key',return_value='key'),patch.object(runner,'_fresh',side_effect=AssertionError('must not retry')) as fresh:
            with self.assertRaisesRegex(OSError,'after claim'):runner.run('idx','body',{}, {})
        fresh.assert_not_called();self.assertIsNone(runner.g)

    def _builder(self, kept_key):
        from unittest.mock import Mock
        R=object.__new__(RR.Resident);R.g={'model':'kept'} if kept_key else None;R.key=kept_key;R.last=None;R.snap={}
        R.runtime=None;R.codes=[];R.loop_i=0;R.vram=lambda:{}
        R._begin=Mock(side_effect=AssertionError('a build never begins'))
        def fresh(init,mark):
            R.g={'model':K('new')};return dict(setup_s=1.0,keepable=R.keepable,why_not=None)
        R._fresh=Mock(side_effect=fresh);R.keepable=True
        return R

    def test_build_only_keeps_the_new_model_and_runs_no_frame(self):
        from unittest.mock import patch
        R=self._builder(K('old'));R.codes=[compile('raise AssertionError("the loop ran")','<loop>','exec')]
        with patch.object(RR,'structure_key',return_value=K('new')):info=R.run('idx','body',{},{},build_only=True)
        self.assertEqual((info['mode'],R.key,R.g),('build',K('new'),{'model':K('new')}))
        R._fresh.assert_called_once()

    def test_build_only_of_the_kept_structure_does_nothing(self):
        from unittest.mock import patch
        R=self._builder(K('same'))
        with patch.object(RR,'structure_key',return_value=K('same')):info=R.run('idx','body',{},{},build_only=True)
        self.assertEqual((info['mode'],R.key,R.g),('kept',K('same'),{'model':'kept'}))
        R._fresh.assert_not_called()

    def test_build_only_of_an_unkeepable_garment_keeps_nothing(self):
        from unittest.mock import patch
        R=self._builder(K('old'));R.keepable=False
        with patch.object(RR,'structure_key',return_value=K('new')):info=R.run('idx','body',{},{},build_only=True)
        self.assertEqual((info['mode'],R.key,R.g),('build',None,None))

    def test_unknown_builder_keeps_its_post_edge_spring_positions(self):
        from unittest.mock import patch
        from types import SimpleNamespace as NS
        class Builder:
            def add_triangles(self,*args,**kw):
                self.tri_areas=[.75];self.tri_poses=[np.eye(2)];self.tri_materials=[[1.,1.,1.,0.,0.]]
                return [.5]
            def add_edges(self,*args):
                self.edge_rest_angle=[0.];self.edge_rest_length=[1.]
                self.particle_q[1]=[2.,0.,0.]
        R=object.__new__(RR.Resident);R.wp=NS()
        st=dict(n_particles=3,tri=np.array([[0,1,2]]),edges=np.array([[-1,-1,0,1]]),springs=np.array([[0,1]]),e0=1,rest_s=0.,hstar=[],dihedral=None,tri_cfg=(1.,1.,1.))
        cfg=dict(tri_ke=1.,tri_ka=1.,tri_kd=1.,edge_ke=1.,edge_kd=1.)
        positions=np.array([[0,0,0],[1,0,0],[0,1,0]],np.float32)
        with patch.dict(sys.modules,{'newton':NS(ModelBuilder=Builder)}):values,_=R._finalize_values(positions,positions,st,cfg)
        self.assertEqual(values['spring_rest_length'].tolist(),[2.])
        self.assertEqual(values['tri_areas'].tolist(),[.75])

    def test_setup_cancellation_drops_model_and_closes_input_archive(self):
        class Cancelled(Exception):pass
        R=object.__new__(RR.Resident)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.npz';np.savez(path,x=np.zeros(1))
            archive=np.load(path)
            def fail(*a):
                R.g=dict(model=object(),input=archive);R.key='old';R.snap={}
                raise Cancelled()
            R._run=fail
            with self.assertRaises(Cancelled):R.run('idx','body',{}, {})
            self.assertIsNone(R.g);self.assertIsNone(R.key);self.assertIsNone(R.snap)
            self.assertIsNone(archive.zip)

    def test_lifecycle_modules_are_not_restored_with_physics_module_state(self):
        from unittest.mock import patch
        R=object.__new__(RR.Resident);R.source=str(SOLVER.resolve())
        modules={n:types.SimpleNamespace(__file__=str(SOLVER/(n+'.py'))) for n in ('job_cancel','lifecycle_trace','some_physics')}
        with patch.dict(sys.modules,modules):
            selected=R._mods()
        self.assertNotIn(modules['job_cancel'],selected)
        self.assertNotIn(modules['lifecycle_trace'],selected)
        self.assertIn(modules['some_physics'],selected)

    def test_newton_vbd_split(self):
        fake = types.ModuleType('warp')
        old = sys.modules.get('warp'); sys.modules['warp'] = fake
        try:
            R = RR.Resident(SOLVER / 'newton_vbd.py', SOLVER)
        finally:
            if old is None: sys.modules.pop('warp', None)
            else: sys.modules['warp'] = old
        self.assertLess(R.i_builder, R.i_final); self.assertLess(R.i_final, R.loop_i)
        heads = sorted(R.heads[i].split('(')[0].split(chr(10))[0] for i in R.i_heavy)
        # the solver, the collision pipeline and the Native 3 block only (not the checkpoint branch)
        self.assertEqual(heads, ['if NATIVE_THREE:', 'pipe = newton.CollisionPipeline', 'solver = newton.solvers.SolverVBD'])

    def test_restored_filter_maps_do_not_accumulate_layer_exclusions(self):
        state={k:None for k in RR.FILTER_OUTPUTS}
        state.update(vfilter={1:{2,3}},efilter={4:{5}})
        saved=dict(stitch_filters=state,stitch_filter_log={'edge_keys':1})
        g=dict(cfg={'layer_order':[{}]},OL_KIN=[],log={})
        RR.Resident._restore_stitch_filters(g,saved)
        g['vfilter'][1].add(999);g['efilter'][4].add(888)
        RR.Resident._restore_stitch_filters(g,saved)
        self.assertEqual(g['vfilter'],{1:{2,3}})
        self.assertEqual(g['efilter'],{4:{5}})

    def test_batched_hinge_test_matches_scalar_at_threshold_and_random_shapes(self):
        def dih(a,b,k,l,P):
            n1=np.cross(P[k]-P[a],P[l]-P[a]);n2=np.cross(P[l]-P[b],P[k]-P[b])
            return np.degrees(np.arccos(np.clip(n1@n2/(np.linalg.norm(n1)*np.linalg.norm(n2)+1e-18),-1,1)))
        random=np.random.default_rng(42)
        for points in [random.normal(size=(80,3)) for _ in range(4)]:
            hinges=[tuple(q) for q in random.integers(0,len(points),size=(200,4))]
            rows=[('F',i,i,q) for i,q in enumerate(hinges)]
            on,seen,added,skipped,_=RR.Resident._hstar_switch(rows,points,points,dih)
            expected={i:True for i,q in enumerate(hinges) if dih(*q,points)<=120}
            self.assertEqual(on,expected)
            self.assertEqual((len(seen),added+skipped),(200,200))
        for degrees in (119.99999999,120.,120.00000001):
            theta=np.radians(degrees)
            P=np.array([[0,1,0],[0,-np.cos(theta),np.sin(theta)],[0,0,0],[1,0,0]],float)
            on,*_=RR.Resident._hstar_switch([('F',0,0,(0,1,2,3))],P,P,dih)
            self.assertEqual(bool(on),bool(dih(0,1,2,3,P)<=120))

    def test_unmade_hinge_uses_its_key_and_is_dropped(self):
        # a hinge newton_vbd never made (not four different points) has row None: it uses up its key as the fresh
        # build does, is never switched on, and is counted as dropped, not skipped
        dih=lambda *a:0.0
        P=np.zeros((6,3))
        rows=[('F','k',None,(0,0,1,2)),('F','k',5,(0,3,1,2)),('N','m',0,1,[(1,None,(0,1,1,2))])]
        on,seen,added,skipped,dropped=RR.Resident._hstar_switch(rows,P,P,dih)
        self.assertEqual((on,added,skipped,dropped),({},0,0,2))
        self.assertEqual(seen,{'k','m'})


class NotKept(unittest.TestCase):
    def test_reasons(self):
        plain = dict(cfg={}, BOUND_COUPLING=False, WELD=None)
        self.assertEqual([w for _, t, w in RR.NOT_KEPT if t(plain)], [])
        for g in (dict(plain, BOUND_COUPLING=True), dict(plain, WELD=object()), dict(plain, cfg={'anchor_ke': 1.0}),
                  dict(plain, cfg={'seam_paths': [[1, 2]]}), dict(plain, cfg={'sym_color': True, 'mirror_map': 'm.npz'}),
                  dict(plain, cfg={'ghost_pieces': ['P']})):
            self.assertEqual(len([w for _, t, w in RR.NOT_KEPT if t(g)]), 1, g)


if __name__ == '__main__':
    unittest.main()
