# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual operator undo/redo, carried nonplanar meshes and real Part to Seam.
Run: Blender -b --factory-startup --python this.py -- <package parent> <out.json>.
No physics claim: the synthetic result is an injective nonplanar graph with known material coordinates.
"""
import sys,json,traceback,copy,inspect
from pathlib import Path
ARGS=sys.argv[sys.argv.index('--')+1:];OUT=Path(ARGS[1]);OUT.parent.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,ARGS[0]);sys.path.insert(0,str(Path(__file__).resolve().parent))
import bpy,numpy as np,ac9_cloth_sim as A
from ac9_cloth_sim import draft_tool as T,store,pattern_view as PV,dress as DR,placed_view as PLV,place_seam as PS
from ac9_cloth_sim.core import spec as S,draft as D,transfer as TR
from symmetry_scene import setup,snapshot
RESULT={'ok':False,'cases':[]}


def save():OUT.write_text(json.dumps(RESULT,indent=2),encoding='utf-8')


def history(before,after,row):
    assert bpy.ops.ed.undo()=={'FINISHED'}
    actual=snapshot();row['undo_equal']=actual==before
    assert row['undo_equal'],'undo differs from pre-operation state'
    assert bpy.ops.ed.redo()=={'FINISHED'}
    redone=snapshot();row['redo_equal']=redone==after
    if not row['redo_equal']:(OUT.parent/('redo_diff_'+row['action']+'.json')).write_text(json.dumps({'expected':after,'actual':redone}),encoding='utf-8')
    assert row['redo_equal'],'redo differs from post-operation state'


def expected_cut(sp,initial):
    pat,tri,idx,X=initial
    expected={}
    new_pat,_=DR._build_cached(sp,bpy.context.scene.ac9_cloth_sim.mesh_size)
    oldnames=list(pat['names']);offs=list(idx['offs'])+[len(X)]
    for i,n in enumerate(new_pat['names']):
        Q=new_pat[f'V{i}']
        # Fixture's three parents occupy disjoint, known rectangles in material space.
        candidates=[]
        for j,parent in enumerate(oldnames):
            V=pat[f'V{j}'];lo=V.min(0)-.001;hi=V.max(0)+.001
            if np.all((Q>=lo)&(Q<=hi)):candidates.append(j)
        assert len(candidates)==1,(n,candidates)
        j=candidates[0]
        expected[str(n)]=TR.bind(Q,pat[f'V{j}'],tri[j],X[offs[j]:offs[j+1]])
    return new_pat,expected


def carried_values(sp):
    g=DR.garment(sp);job=DR.job_of(g)
    last=T._last_result(job,DR.shown_shape(bpy.context,g))
    assert last is not None
    pat,idx,X,_=last;names=[str(n) for n in pat['names']];offs=list(idx['offs'])+[len(X)]
    return {n:np.asarray(X[offs[i]:offs[i+1]]) for i,n in enumerate(names)}


try:
 A.register();bpy.context.preferences.edit.use_global_undo=True
 for action in ('clone','clone_sew','cut','spread'):
    row={'action':action};RESULT['cases'].append(row);save()
    old,n,l,targets,initial=setup(OUT.parent/'scene_results',action)
    bpy.ops.ed.undo_push(message='pre-operation witness')
    before=snapshot()
    if action.startswith('clone'):
        result=bpy.ops.ac9_cloth_sim.clone_shape('EXEC_DEFAULT',True,piece=n,line=l,sew=action=='clone_sew')
        assert result=={'RUNNING_MODAL'} and snapshot()==before
        from ac9_cloth_sim import copy_place as CP
        op=CP._active
        candidate=CP.move_candidate(op._copy_candidate,op._copy_names,op._copy_anchor,[1500,300])
        op._copy_end(bpy.context)
        result=op._copy_finish(bpy.context,candidate)
        # Headless history witness. Native copy click/automatic undo is verified in test_clone_modal.py.
        bpy.ops.ed.undo_push(message='Confirm copied pattern')
    elif action=='cut':result=bpy.ops.ac9_cloth_sim.cut_line('EXEC_DEFAULT',True,piece=n,line=l)
    else:result=bpy.ops.ac9_cloth_sim.spread('EXEC_DEFAULT',True,piece=n,line=l,amount=.020,mode='parallel',side='auto',hinge='start')
    assert result=={'FINISHED'},result
    after=snapshot();row['piece_count']=len(after['spec']['pieces']);row['operator_result']=list(result)
    row['status']=bpy.context.scene.ac9_cloth_sim.dress_status
    if action.startswith('clone'):
        new={p['name'] for p in after['spec']['pieces']}-{p['name'] for p in old['pieces']}
        self_selected={PV.piece_objects()[nm].name for nm in new}
        assert self_selected.issubset(set(after['selected']))
        assert after['active']==PV.piece_objects()[n+'_'+l].name
        row['garment_unchanged']=after['garment']==before['garment'];assert row['garment_unchanged']
    else:
        actual=carried_values(after['spec']);row['carried_pieces']=sorted(actual)
        assert set(actual)=={p['name'] for p in after['spec']['pieces']}
        if action=='cut':
            _,expected=expected_cut(after['spec'],initial)
            row['max_material_error_mm']=max(float(np.linalg.norm(actual[k]-v,axis=1).max()) for k,v in expected.items())
            assert row['max_material_error_mm']<.001,row
        else:
            # With no seams the approved carry rule leaves new material where drawn,
            # extending the old nonplanar face. Both symmetric cuts add 20 mm width.
            pat,tris,idx,X=initial;oldnames=list(map(str,pat['names']));offs=list(idx['offs'])+[len(X)]
            new_pat,_=DR._build_cached(after['spec'],bpy.context.scene.ac9_cloth_sim.mesh_size)
            errors={};widths={}
            for i,nm in enumerate(map(str,new_pat['names'])):
                j=oldnames.index(nm);Q=new_pat[f'V{i}'];oldX=X[offs[j]:offs[j+1]]
                expected=TR.bind(Q,pat[f'V{j}'],tris[j],oldX)
                errors[nm]=float(np.linalg.norm(actual[nm]-expected,axis=1).max())
                if nm!='Witness':
                    widths[nm]=[float(np.ptp(oldX[:,0])),float(np.ptp(actual[nm][:,0]))]
                    assert abs(widths[nm][1]-widths[nm][0]-40)<.001,widths
            row['max_material_error_mm']=max(errors.values());assert row['max_material_error_mm']<.001,errors
            row['world_width_before_after_mm']=widths
        # Both affected parents must have a changed layout in the carried result, while the witness stays exact.
        p0,_,i0,X0=initial;j=list(p0['names']).index('Witness');a=int(i0['offs'][j]);b=a+len(p0[f'V{j}'])
        row['witness_error_mm']=float(np.linalg.norm(actual['Witness']-X0[a:b],axis=1).max())
        assert row['witness_error_mm']<.001
    history(before,after,row);save()
    if action=='clone_sew':
        # Placement is deliberately a separate real operator/history step, exactly as the target-selection tool.
        new=sorted(new);before_place=snapshot()
        result=bpy.ops.ac9_cloth_sim.move_to_seam('EXEC_DEFAULT',True,moving_pieces=json.dumps(new),target_pieces=json.dumps(['Panel','Panel_R']))
        row['seam_placement_result']=list(result);assert result=={'FINISHED'}
        sp=store.get(bpy.context)[0];obs=PLV.objects(sp)
        row['placed_new_pieces']=sorted(set(new)&set(obs))
        assert row['placed_new_pieces']==new
        row['placed_vertex_counts']={k:len(obs[k].data.vertices) for k in new}
        got=PS.fresh_shapes(bpy.context);pat,_,xyz=got;pn=list(map(str,pat['names']))
        gaps={}
        for nm in new:
            k=pn.index(nm);st=np.asarray(pat['stitch']).reshape(-1,4)
            pairs=st[(st[:,0]==k)|(st[:,2]==k)]
            assert len(pairs)
            gaps[nm]=max(float(np.linalg.norm(xyz[int(a)][int(ai)]-xyz[int(b)][int(bi)])) for a,ai,b,bi in pairs)
        row['max_sewn_gap_mm']=gaps;assert max(gaps.values())<.001,gaps
        row['placement_status']=bpy.context.scene.ac9_cloth_sim.dress_status
        placed=snapshot()
        # Include actual placed mesh coordinates in addition to the dressed garment's untouched source.
        row['placed_xyz']={k:[list(v.co) for v in obs[k].data.vertices] for k in new}
        history(before_place,placed,row)
        assert bpy.ops.ed.undo()=={'FINISHED'};assert snapshot()==before_place
        assert bpy.ops.ed.undo()=={'FINISHED'};assert snapshot()==before
        assert bpy.ops.ed.redo()=={'FINISHED'};assert snapshot()==after
        assert bpy.ops.ed.redo()=={'FINISHED'};assert snapshot()==placed
        row['two_step_history']=True;save()
 for action in ('cut','dart'):
    row={'action':action,'deferred':True};RESULT['cases'].append(row);save()
    old,n,l,targets,initial=setup(OUT.parent/'deferred_results',action)
    bpy.context.scene.ac9_cloth_sim.follow_edits=False
    before=snapshot()
    maps=None
    if action=='cut':
        result=bpy.ops.ac9_cloth_sim.cut_line('EXEC_DEFAULT',True,piece=n,line=l)
    else:
        from ac9_cloth_sim import dart_ui as DU
        from ac9_cloth_sim.core import dart as DA
        expected_sp=copy.deepcopy(old);_,_,_,maps=DA.move_dart(expected_sp,n,l,'E3',.25)
        Proxy=type('DeferredDart',(),{k:v for k,v in DU.AC9_OT_SimMoveDart.__dict__.items() if inspect.isfunction(v)})
        op=Proxy();op.piece=n;op.seam=l;op.side='auto';op.mode='pivot';op.true_mouth=True;op.apex_follows=False;op.report=lambda *a:None
        result=op.apply(bpy.context,'E3',.25)
    assert result=={'FINISHED'}
    self_pending=bpy.context.scene.ac9_cloth_sim.sync_pending;assert self_pending
    assert snapshot()['garment']==before['garment']
    result=bpy.ops.ac9_cloth_sim.sync_3d('EXEC_DEFAULT',True)
    assert result=={'FINISHED'} and not bpy.context.scene.ac9_cloth_sim.sync_pending
    sp=store.get(bpy.context)[0];actual=carried_values(sp)
    if action=='cut':_,expected=expected_cut(sp,initial)
    else:
        npat,_=DR._build_cached(sp,bpy.context.scene.ac9_cloth_sim.mesh_size)
        pat,tris,idx,X=initial;names=list(map(str,pat['names']));offs=list(idx['offs'])+[len(X)]
        expected={}
        for i,nm in enumerate(npat['names']):
            j=names.index(str(nm));V=npat[f'V{i}'];Q=maps[str(nm)](V) if str(nm) in maps else V
            expected[str(nm)]=TR.bind(Q,pat[f'V{j}'],tris[j],X[offs[j]:offs[j+1]])
    row['max_material_error_mm']=max(float(np.linalg.norm(actual[k]-v,axis=1).max()) for k,v in expected.items())
    assert row['max_material_error_mm']<.001,row
    row['status']=bpy.context.scene.ac9_cloth_sim.dress_status;row['pending_cleared']=True;save()
 RESULT['ok']=True
except Exception:RESULT['error']=traceback.format_exc()
save();sys.exit(0 if RESULT['ok'] else 1)
