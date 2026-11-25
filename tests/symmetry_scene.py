# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Text, meshes and nonplanar known material coordinates for operator/undo/carry checks."""
import json,copy
from pathlib import Path
import bpy,numpy as np
from ac9_cloth_sim import draft_tool as T,store,pattern_view as PV,dress as DR,blend_record as BR,result_reference as RR
from ac9_cloth_sim.core import spec as S,draft as D,pattern_build as PB
from symmetry_fixture import fixture,assert_valid


def inputs(sp,h=20):
    pat,_=PB.build(sp,h);tris,bad=DR.triangulate(pat);assert not bad,bad
    flat=np.vstack([pat[f'V{i}'] for i in range(len(pat['names']))])
    offs=np.cumsum([0]+[len(pat[f'V{i}']) for i in range(len(pat['names'])-1)])
    F=np.vstack([t+o for t,o in zip(tris,offs)])
    fp=np.concatenate([np.full(len(t),i) for i,t in enumerate(tris)])
    idx={'flat':flat,'offs':offs,'F':F,'fp':fp}
    X=np.c_[flat,40+0.0004*flat[:,0]**2+0.0002*flat[:,1]**2]
    assert np.isfinite(X).all()
    assert (DR.__dict__.get('np',np).linalg.norm(np.cross(X[F[:,1]]-X[F[:,0]],X[F[:,2]]-X[F[:,0]]),axis=1)>1e-8).all()
    return pat,tris,idx,X


def setup(out,action,with_result=True):
    bpy.ops.ac9_cloth_sim.new_pattern('EXEC_DEFAULT')
    sp,n,l,targets=fixture('both_x',closed=action in ('clone','clone_sew'))
    sp['name']='Symmetry '+action
    if action=='dart':
        from ac9_cloth_sim.core import dart as DA
        for p in sp['pieces']:p['internals']=[]
        ids,_=DA.add_dart(sp,n,'E2',.3,width=20,length=40);l=ids[n]
    D.add_polygon(sp,[[1100,0],[1200,0],[1200,150],[1100,150]],'Witness')
    for pc in sp['pieces']:
        sp['placement'][pc['name']]={'guide':S.FLAT,'anchor':[0,0],
            'flat':{'o':[0,0,40],'R':[[1,0,0],[0,1,0],[0,0,1]]}}
    assert_valid(sp)
    T.commit(bpy.context,sp,[p['name'] for p in sp['pieces']])
    bpy.context.scene.ac9_cloth_sim.mesh_size=20
    bpy.context.scene.ac9_cloth_sim.follow_edits=True
    pat,tris,idx,X=inputs(sp)
    if with_result:
        folder=Path(out)/('result_'+action);folder.mkdir(parents=True,exist_ok=True)
        RR.write_result(str(folder),'carried',pat['names'],idx['offs'],idx['F'],idx['fp'],idx['flat'],X,S.dumps(sp),pattern=pat,
                        info={'mesh':{'h_mm':20,'mesher':PB.MESHER_VERSION}})
        ob=DR.garment_object(bpy.context,DR.garment_name(sp),idx['flat'],idx['F'],idx['fp'],X,[str(n) for n in pat['names']])
        ob['ac9_job']=str(folder);BR.remember_spec(ob,S.dumps(sp),str(folder));BR.note(ob,str(folder))
    return sp,n,l,targets,(pat,tris,idx,X)


def snapshot():
    from ac9_cloth_sim import placed_view as PLV
    store.invalidate();sp,_=store.get(bpy.context)
    obs={}
    for n,o in PV.piece_objects().items():
        obs[n]={'vertices':[list(v.co) for v in o.data.vertices],'selected':o.select_get(),
                'owner':str(o.get(PV.TEXT_PROP,'')) if hasattr(PV,'TEXT_PROP') else str(o.get('ac9_spec',''))}
    g=DR.garment(sp)
    placed={n:{'xyz':[[float(x) for x in o.matrix_world@v.co] for v in o.data.vertices],
               'faces':[list(p.vertices) for p in o.data.polygons]} for n,o in PLV.objects(sp).items()}
    # Readers may refresh the in-memory mirror axis's origin along its own line.
    # Undo's persisted source of truth is the Text, not that mutable drawing cache.
    stored=S.loads(store.text(bpy.context).as_string())
    return {'spec':stored,'pieces':obs,'placed':placed,'garment':None if g is None else {
        'xyz':[[float(x) for x in g.matrix_world@v.co] for v in g.data.vertices],
        'faces':[list(p.vertices) for p in g.data.polygons],'job':DR.job_of(g)},
        'selected':sorted(o.name for o in bpy.context.selected_objects),
        'active':bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None}
