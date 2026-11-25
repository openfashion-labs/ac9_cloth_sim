"""Submitted failure + isolated material-side contracts; not a solver-quality trial."""
import os, sys, json, traceback, importlib.util, time
from pathlib import Path
import bpy, numpy as np
from mathutils import Matrix
ARGS=sys.argv[sys.argv.index('--')+1:]
# Args: source package directory, JSON result, optional submitted fixture copy.
src=Path(ARGS[0]);OUT=Path(ARGS[1]);P=OUT.parent;P.mkdir(parents=True,exist_ok=True)
USER_BLEND=Path(ARGS[2]) if len(ARGS)>2 else None
ms=importlib.util.spec_from_file_location('ac9_cloth_sim',src/'__init__.py',submodule_search_locations=[str(src)])
A=importlib.util.module_from_spec(ms);sys.modules['ac9_cloth_sim']=A;ms.loader.exec_module(A);A.register()
from ac9_cloth_sim import store as ST,dress as D,placed_view as PV,placement as PL,place_fold as PF,draft_tool as DT,place_candidate as PC,private_ids
from ac9_cloth_sim.core import spec as S,draft as DR,fold_resolve as FR,internal_sync as IS,fold_hand as FH
D.mesh_cache_dir=lambda c:str(P/'mesh_cache');D.standby_wake=lambda *a,**k:None
R={'cases':{},'health':'Each generated fixture: finite, nondegenerate triangles, dividing fold; submitted reproduction only, not physical garment quality.'}
ctx=bpy.context
def points(ob):return PV._world_co(ob)*1000.
def run(name,setup):
    if os.environ.get('ONLY_CASE') and name!=os.environ['ONLY_CASE']:return
    row={};R['cases'][name]=row
    try:
        p,l=setup();DT.select_internal(ctx,p,l)
        authored=ST.get(ctx)[0];internals=S.dumps({'pieces':[FH.shape_only(pc) for pc in authored['pieces']]})
        eff=PF._effective(ctx,authored);targets=PF._physical_targets(authored,p,l)
        row['targets']=targets;row['health']=[]
        row['prepared_internals']={n:[it['id'] for it in S.piece_index(eff)[n].get('internals',[])] for n,l in targets}
        # Material-corresponding baseline after the same read-only preparation/remesh as the first UI turn.
        infos={};base={};states={}
        for n,line in targets:
            info,why=PF._info(ctx,eff,n,line);assert info is not None,why
            ob=info['ob'];X=points(ob);T=np.asarray(info['tris'][info['i']],int)
            area=np.linalg.norm(np.cross(X[T[:,1]]-X[T[:,0]],X[T[:,2]]-X[T[:,0]]),axis=1)
            assert np.isfinite(X).all() and (area>1e-9).all()
            row['health'].append({'piece':n,'vertices':len(X),'min_area2_mm2':float(area.min()),'side_counts':[len(s) for s in info['sides']]})
            assert info['a_side'] is not None and all(len(s) for s in info['sides']),'fixture fold must separate two nonempty material sides'
            infos[n,line]=info;base[n]=X
        all_before={n:points(o).copy() for n,o in PV.objects(authored).items()}
        row['sides']={}
        for side in ('A','B','NONE'):
            if name=='central_offaxis' and side=='B':DT.select_internal(ctx,p,targets[-1][1])
            started=time.perf_counter();ctx.scene.ac9_iline_fold_side=side
            assert ctx.scene.ac9_iline_fold_side==side,(side,PF.why_not(p,l))
            assert PF.why_not(p,l) is None,PF.why_not(p,l)
            metrics={};sp=ST.get(ctx)[0]
            assert S.dumps({'pieces':[FH.shape_only(pc) for pc in sp['pieces']]})==internals,'authored internals changed'
            assert not sp.get(FR.MARKER) and not any(str(f.get('line','')).startswith(FR.PREFIX) for f in sp.get('folds',[]))
            for n,line in targets:
                ob=PV.objects(sp)[n];X=points(ob);delta=np.linalg.norm(X-base[n],axis=1)
                info=infos[n,line];k=info['a_side'] if side=='A' else 1-info['a_side']
                met={'max_mm':float(delta.max()),'axis_mm':float(delta[info['roll']].max()),'folds':PF.folded(ob)}
                if len(targets)==1:
                    if side=='NONE':assert delta.max()<.0001
                    else:
                        met['moving_mm']=float(delta[info['sides'][k]].max());met['fixed_mm']=float(delta[info['sides'][1-k]].max())
                        assert met['moving_mm']>1. and met['fixed_mm']<.001 and met['axis_mm']<.001,met
                        if info.get('flat'):
                            current=PF._to_guide(info,PV.generated_co(ob,np.array([v.co[:] for v in ob.data.vertices]))*1000.)
                            moved=info['sides'][k]
                            met['face_displacement_mm']=float(np.median(current[moved,2]-info['Xid'][moved,2]))
                            deg=next(f['deg'] for f in sp['folds'] if f.get('piece')==p and f.get('line')==l)
                            assert met['face_displacement_mm']*deg<0,met
                else:assert delta.max()>1. if side!='NONE' else delta.max()<.0001
                assert np.isfinite(X).all();metrics[n]=met
                if name=='central_offaxis' and side!='NONE':assert len(PF.folded(ob))==2,PF.folded(ob)
                if name=='central_axis' and side!='NONE':assert len(PF.folded(ob))==1,PF.folded(ob)
            for n,X in all_before.items():
                if n not in base and PV.objects(sp)[n].get(PV.PROP) not in base:np.testing.assert_array_equal(points(PV.objects(sp)[n]),X)
            assert len(private_ids._owners)==0,private_ids._owners
            states[side]={n:points(PV.objects(sp)[n]).copy() for n in base}
            row['sides'][side]={'elapsed_ms':(time.perf_counter()-started)*1000.,'metrics':metrics}
            if name=='actual_pocket':
                from ac9_cloth_sim import start_check as SC
                from ac9_cloth_sim.core import start_plan as SP
                sources={};screen=SC.read_screen(ctx,sp,D.garment(sp),'START',sources)
                ready=D.prepare(ctx,ctx.scene.ac9_cloth_sim.mesh_size,shapes_only=True,placed_only=True)
                names=list(map(str,ready['pat']['names']));plan=SP.plan(screen,names,sources)
                initial,_,taken=SP.take(plan,screen,sources,[ready['pat'][f'V{i}'] for i in range(len(names))],ready['X'],D._bary_map_extend)
                errors={n:float(np.linalg.norm(initial[names.index(n)]-screen[n][1][2],axis=1).max()) for n in ('Pattern_578148','Pattern_578149')}
                assert all(e<.0001 for e in errors.values()),errors
                row['sides'][side]['start_input_error_mm']=errors
        # Preview -> commit uses the same constraints and persists authored data only.
        PF.write_angle(ctx,p,l,90.,'A')
        cand=PC.build(ctx,ST.get(ctx)[0],PL.with_pairs(ST.get(ctx)[0],[p]));PC.commit(ctx,cand);ctx.view_layer.update()
        row['preview_error_mm']={}
        for n in base:
            ob=PV.objects(ST.get(ctx)[0])[n];i=list(cand['res']['pat']['names']).index(n)
            err=float(np.linalg.norm(points(ob)-cand['X'][i],axis=1).max());assert err<.0001
            row['preview_error_mm'][n]=err
        if name=='actual_pocket':
            np.savez(P/'pocket_shapes.npz',**{side:states[side][targets[0][0]] for side in states},T=np.asarray(infos[targets[0]]['tris'][infos[targets[0]]['i']]),V=infos[targets[0]]['V'])
        if name in ('actual_pocket','central_axis','central_offaxis','flat_53_90','pair_missing_other'):
            reference={n:points(o).copy() for n,o in PV.objects(ST.get(ctx)[0]).items() if o.get(PV.PROP)==n and n in base}
            PL.rebuild_placement(ctx,ST.get(ctx)[0],PL.with_pairs(ST.get(ctx)[0],[p]))
            row['rebuild_error_mm']={n:float(np.linalg.norm(points(PV.objects(ST.get(ctx)[0])[n])-X,axis=1).max()) for n,X in reference.items()}
            assert all(e<.001 for e in row['rebuild_error_mm'].values()),row['rebuild_error_mm']
            path=P/('roundtrip_'+name+'.blend');bpy.ops.wm.save_as_mainfile(filepath=str(path))
            bpy.ops.wm.open_mainfile(filepath=str(path),use_scripts=False)
            row['save_load_error_mm']={n:float(np.linalg.norm(points(PV.objects(ST.get(ctx)[0])[n])-X,axis=1).max()) for n,X in reference.items()}
            assert all(e<.001 for e in row['save_load_error_mm'].values())
            DT.select_internal(ctx,p,l)
            for side in ('B','NONE'):
                ctx.scene.ac9_iline_fold_side=side;assert ctx.scene.ac9_iline_fold_side==side,PF.why_not(p,l)
        row['ok']=True
    except Exception:row['error']=traceback.format_exc();row['ok']=False
    OUT.write_text(json.dumps(R,ensure_ascii=False,indent=2),encoding='utf8')

def actual():
    bpy.ops.wm.open_mainfile(filepath=str(USER_BLEND),use_scripts=False)
    # Explicit, visible repair of this submitted legacy mismatch, only in the test copy.
    sp=S.loads(S.dumps(ST.get(ctx)[0]));p,q='Pattern_578148','Pattern_578149';lid='I4'
    M=DR.mirror_fn(DR.piece(sp,p),DR.piece(sp,q));line=DR.internal(DR.piece(sp,p),lid)
    tid=DR.add_internal(sp,q,M(line['pts']),mirror=False)
    FH.carry(line,DR.internal(DR.piece(sp,q),tid),M);IS.bind(sp,p,lid,q,tid)
    ST.put(ctx,sp);PF.sync_twin(ctx,p,lid)
    if not (P/'PT-4_internal_lines_repaired.blend').exists():
        bpy.ops.wm.save_as_mainfile(filepath=str(P/'PT-4_internal_lines_repaired.blend'))
    return 'Pattern_578148','I4'
if USER_BLEND is not None:run('actual_pocket',actual)

def actual_other(name):
    actual();sp=S.loads(S.dumps(ST.get(ctx)[0]));B=S.Outline(DR.piece(sp,name)).B
    vertical=name=='Pattern_1981972'
    if vertical:B=B[:,::-1]
    y=float(np.mean([B[:,1].min(),B[:,1].max()]));N=np.roll(B,-1,axis=0)
    crossing=(B[:,1]>y)!=(N[:,1]>y);a=B[crossing];b=N[crossing]
    xs=np.sort(a[:,0]+(y-a[:,1])*(b[:,0]-a[:,0])/(b[:,1]-a[:,1]))
    segments=list(zip(xs[::2],xs[1::2]));lo,hi=max(segments,key=lambda s:s[1]-s[0])
    cut=np.array([[lo,y],[hi,y]])
    if vertical:cut=cut[:,::-1]
    lid=DR.add_internal(sp,name,cut,mirror=True)
    sp['folds'].append(dict(piece=name,line=lid,deg=90.,side='NONE'));ST.put(ctx,sp);PF.sync_twin(ctx,name,lid)
    PL.rebuild_placement(ctx,ST.get(ctx)[0],PL.with_pairs(ST.get(ctx)[0],[name]))
    return name,lid
for name in (('Pattern_1981972','Pattern_3840996','Pattern_4815714','Waistband_3') if USER_BLEND is not None else ()):
    run('actual_other_'+name,lambda name=name:actual_other(name))

def generated(kind='single',flat=False,tilt=0.,deg=90.,lead=False):
    # Delete this test scene's data, retaining registered addon classes.
    for ob in list(bpy.data.objects):bpy.data.objects.remove(ob,do_unlink=True)
    for text in list(bpy.data.texts):bpy.data.texts.remove(text)
    ST.invalidate()
    bpy.ops.mesh.primitive_cube_add(size=.1,location=(10,0,0));body=ctx.object;ctx.scene.ac9_cloth_sim.mesh_size=12.
    sp=S.new_spec('FoldContract_'+kind);DR.add_polygon(sp,[[0,0],[200,0],[200,200],[0,200]],name='P')
    central=kind.startswith('central')
    if central:DR.piece(sp,'P')['mirror_edit']=True
    x=100 if kind=='central_axis' else 60
    lid=DR.add_internal(sp,'P',[[x,0],[x,200]],mirror=central)
    target='P'
    if kind.startswith('pair'):
        made,_=DR.mirror_group(sp,['P']);q=made['P']
        if kind in ('pair_missing','pair_missing_separate'):
            DR.piece(sp,q)['internals']=[]
            for it in DR.piece(sp,'P')['internals']:it.pop(IS.KEY,None)
        if kind=='pair_missing_separate':
            pc=DR.piece(sp,q)
            for edge in pc['edges']:
                for point in edge['pts']:point[0]*=1.1
        if lead and kind=='pair_missing':
            it=DR.internal(DR.piece(sp,'P'),lid);pts=DR.mirror_fn(DR.piece(sp,'P'),DR.piece(sp,q))(it['pts'])
            DR.piece(sp,'P')['internals']=[];DR.add_internal(sp,q,pts,mirror=False);target=q
            lid=DR.piece(sp,q)['internals'][0]['id']
        if kind=='pair_off':sp['symmetry']='off'
    sp['guides']={'torso':dict(o=[0,0,0],R=np.eye(3).tolist(),r=100,h0=-200,h1=200)}
    sp['placement']={'P':dict(guide='torso',theta=90.,h=0.,anchor=[100,100])}
    if flat:
        Rm=np.asarray(Matrix.Rotation(np.radians(tilt),3,'X'))
        sp['placement']['P']=dict(guide='flat',flat=dict(o=[0,0,20],R=Rm.tolist()),anchor=[100,100])
    sp['placement'],_=S.mirror_twins(sp)
    if kind=='pair_off':
        q=made['P'];sp['placement'][q]=dict(guide='torso',theta=140.,h=0.,anchor=np.mean(S.Outline(DR.piece(sp,q)).B,axis=0).tolist())
    sp['folds']=[dict(piece=target,line=lid,deg=deg,side='NONE')]
    ST.new_text(ctx,sp['name']);ST.write(ctx,sp,keep=True);ctx.scene.ac9_cloth_sim.body=body;PF.sync_twin(ctx,target,lid)
    r=D.prepare(ctx,12.,shapes_only=True,placed_only=True);PV.build(ctx,ST.get(ctx)[0],r['pat'],r['tris'],r['X'],r['pairs'])
    return target,lid
for kind in ('single','pair_existing','pair_missing','pair_missing_separate','pair_off','central_axis','central_offaxis'):
    run(kind,lambda kind=kind:generated(kind))
run('pair_missing_other',lambda:generated('pair_missing',lead=True))
for tilt in (0.,53.,180.):
    for deg in (90.,179.,-90.):run('flat_%g_%g'%(tilt,deg),lambda tilt=tilt,deg=deg:generated(flat=True,tilt=tilt,deg=deg))

def central_click_independent():
    results=[]
    for which in (0,1):
        p,l=generated('central_offaxis',flat=True)
        lines=sorted(it['id'] for it in DR.piece(ST.get(ctx)[0],p)['internals'])
        DT.select_internal(ctx,p,lines[which]);ctx.scene.ac9_iline_fold_side='B'
        assert ctx.scene.ac9_iline_fold_side=='B';ob=PV.objects(ST.get(ctx)[0])[p]
        assert len(PF.folded(ob))==2;results.append(points(ob))
        ctx.scene.ac9_iline_fold_side='NONE';assert not PF.folded(ob)
    error=float(np.linalg.norm(results[0]-results[1],axis=1).max());assert error<.0001,error
    R['central_click_independent_mm']=error
central_click_independent()
R['passed']=sum(r['ok'] for r in R['cases'].values());R['count']=len(R['cases'])
OUT.write_text(json.dumps(R,ensure_ascii=False,indent=2),encoding='utf8')

assert R['passed']==R['count'], [(n,r.get('error')) for n,r in R['cases'].items() if not r['ok']]
