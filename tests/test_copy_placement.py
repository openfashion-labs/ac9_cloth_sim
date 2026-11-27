"""Blender headless transaction regressions. Plane hit injection is separate from real GUI input checks."""
import bpy, sys, json, traceback, importlib.util, copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

args=sys.argv[sys.argv.index('--')+1:]
root=Path(args[0]);output=Path(args[1])
module=importlib.util.spec_from_file_location('ac9_cloth_sim',root/'__init__.py',submodule_search_locations=[str(root)])
addon=importlib.util.module_from_spec(module);sys.modules['ac9_cloth_sim']=addon;module.loader.exec_module(addon)
addon.register()
from ac9_cloth_sim import store,pattern_view as PV,draft_tool as DT,selection as SEL,copy_place as CP
from ac9_cloth_sim.core import spec as S,draft as D
ctx=bpy.context
area=next(a for a in ctx.screen.areas if a.type=='VIEW_3D')
region=next(r for r in area.regions if r.type=='WINDOW')
out={'checks':[],'errors':[],'fixture':'Artificial rectangles: standard gate rejected broken/retired source; coordinate/transaction tests only'}

def reset():
    if CP._active is not None: CP._active.cancel(ctx)
    for ob in list(bpy.data.objects): bpy.data.objects.remove(ob,do_unlink=True)
    sp=S.new_spec('Copy placement')
    a=D.add_polygon(sp,[[0,0],[400,0],[400,600],[0,600]],'Front')
    b=D.add_polygon(sp,[[700,0],[900,0],[900,300],[700,300]],'Side')
    D.piece(sp,a)['internals']=[{'id':'Pocket','closed':True,'pts':[[100,200],[200,200],[200,300],[100,300],[100,200]]}]
    sp['seams']=[{'id':'S1','a':{'piece':a,'from':[D.piece(sp,a)['edges'][0]['id'],0.0],'to':[D.piece(sp,a)['edges'][0]['id'],1.0],'dir':1},
                  'b':{'piece':b,'from':[D.piece(sp,b)['edges'][0]['id'],0.0],'to':[D.piece(sp,b)['edges'][0]['id'],1.0],'dir':1}}]
    assert not D.problems(sp,[a,b])
    assert all(np.isfinite(S.Outline(D.piece(sp,side['piece'])).span(side)) for side in sp['seams'][0].values() if isinstance(side,dict))
    store.new_text(ctx,sp['name']);DT.commit(ctx,sp,[a,b]);assert PV.integrity(ctx,sp)['ok']
    SEL.apply(ctx,[a,b],'SET',active=a,interaction=True)
    ctx.view_layer.update()
    return sp,a,b

def body():return store.text(ctx).as_string()
def event(kind='MOUSEMOVE',value='NOTHING'):
    return SimpleNamespace(type=kind,value=value,mouse_x=350,mouse_y=400,mouse_region_x=350,mouse_region_y=400)
def frozen():
    return (body(),SEL.pattern_pieces(ctx),tuple((o.name,o.session_uid,o.data.session_uid if o.data else 0) for o in bpy.data.objects))
def run(name,fn):
    try: out['checks'].append({'name':name,'result':fn()})
    except Exception: out['errors'].append({'name':name,'error':traceback.format_exc()})
    finally:
        if CP._active is not None: CP._active.cancel(ctx)

def transaction(kind,sew=False,cancel=False,plane='x'):
    sp,a,b=reset();before=frozen()
    kw={'piece':a,'line':'Pocket','sew':sew} if kind=='clone_shape' else {'plane':plane} if kind=='mirror_copy' else {}
    if kind=='clone_shape': SEL.apply(ctx,[a],'SET',active=a,interaction=True);before=frozen()
    assert getattr(bpy.ops.ac9_cloth_sim,kind)('EXEC_DEFAULT',**kw)=={'RUNNING_MODAL'}
    op=CP._active
    assert frozen()==before,'preview creation published a copy'
    point=np.array([1200.,800.])
    with patch.object(CP,'viewport',return_value=(area,region)),patch.object(DT,'mouse_mm',return_value=point):
        op.modal(ctx,event())
        assert frozen()==before,'mousemove changed document/objects/selection'
        anchor=D.piece_center(D.piece(op._copy_preview,op._copy_names[0]))
        np.testing.assert_allclose(anchor,point,atol=1e-4)
        if cancel:
            assert op.modal(ctx,event('ESC','PRESS'))=={'CANCELLED'}
            assert frozen()==before,'cancel left copies, seams, pairs, placement or selection'
            return {'cancel':'unchanged','source_pieces':2,'preview_pieces':len(op._copy_names)}
        names=list(op._copy_names)
        assert op.modal(ctx,event('LEFTMOUSE','PRESS'))=={'FINISHED'}
    current=store.get(ctx)[0];assert PV.integrity(ctx,current)['ok']
    assert len(current['pieces'])==2+len(names)
    for n in (a,b):
        np.testing.assert_array_equal(D.corners_all(D.piece(current,n)),D.corners_all(D.piece(sp,n)))
    assert CP._active is None and op._copy_handle is None
    if kind=='duplicate_pieces':
        assert len(current['seams'])==2 and all(n not in current.get('placement',{}) for n in names)
        np.testing.assert_allclose(D.piece_center(D.piece(current,names[1]))-D.piece_center(D.piece(current,names[0])),[600,-150])
    if kind=='mirror_copy':
        assert len(current['seams'])==2
        assert all(D.twin_of(current,n) in names for n in (a,b))
    if kind=='clone_shape':
        assert len(current['seams'])==(5 if sew else 1)
    return {'copies':len(names),'seams':len(current['seams']),'anchor_mm':point.tolist(),'original_geometry_difference_mm':0}

with ctx.temp_override(area=area,region=region):
    for kind in ('duplicate_pieces','mirror_copy','clone_shape'):
        run(kind+'_commit',lambda kind=kind:transaction(kind))
        run(kind+'_cancel',lambda kind=kind:transaction(kind,cancel=True))
    run('clone_sew_commit',lambda:transaction('clone_shape',sew=True))
    run('clone_sew_cancel',lambda:transaction('clone_shape',sew=True,cancel=True))
    run('front_back_mirror',lambda:transaction('mirror_copy',plane='y'))
    def before_view():
        reset();before=frozen();assert bpy.ops.ac9_cloth_sim.duplicate_pieces()=={'RUNNING_MODAL'}
        op=CP._active
        with patch.object(CP,'viewport',return_value=None):
            assert op.modal(ctx,event('LEFTMOUSE','PRESS'))=={'RUNNING_MODAL'}
            assert not op._copy_ready and frozen()==before
            assert op.modal(ctx,event('RIGHTMOUSE','PRESS'))=={'CANCELLED'}
        assert frozen()==before
        return 'click outside pattern ignored; cancel before entering leaves original unchanged'
    run('wait_for_pattern_view',before_view)
    def stale():
        reset();assert bpy.ops.ac9_cloth_sim.duplicate_pieces()=={'RUNNING_MODAL'};op=CP._active
        sp=store.get(ctx)[0];sp=copy.deepcopy(sp);sp['name']='Edited externally';store.write(ctx,sp)
        external=body()
        with patch.object(CP,'viewport',return_value=(area,region)),patch.object(DT,'mouse_mm',return_value=[1000,900]):
            assert op.modal(ctx,event('LEFTMOUSE','PRESS'))=={'CANCELLED'}
        assert body()==external and len(store.get(ctx)[0]['pieces'])==2
        return 'stale owner refuses publication'
    run('stale_document',stale)
    def mirror_3d():
        sp,a,b=reset();sp['placement']={a:{'guide':'torso','theta':0.,'h':0.,'anchor':[200.,300.]}}
        DT.commit(ctx,sp,[a,b]);before=copy.deepcopy(sp['placement'][a])
        assert bpy.ops.ac9_cloth_sim.mirror_copy()=={'RUNNING_MODAL'};op=CP._active
        sample=copy.deepcopy(op._copy_candidate['placement'])
        with patch.object(CP,'viewport',return_value=(area,region)),patch.object(DT,'mouse_mm',return_value=[1200,800]):
            op.modal(ctx,event());preview=op._copy_preview
            assert preview['placement'][a]==before
            for n in op._copy_names:
                if n not in sample:continue
                delta=D.piece_center(D.piece(preview,n))-D.piece_center(D.piece(op._copy_candidate,n))
                np.testing.assert_allclose(np.array(preview['placement'][n]['anchor'])-sample[n]['anchor'],delta)
            op.modal(ctx,event('ESC','PRESS'))
        return 'original placement unchanged; copy anchor translated with pattern'
    run('mirror_3d_anchors',mirror_3d)
    def active_anchor(kind):
        sp,a,b=reset();SEL.apply(ctx,[a,b],'SET',active=b,interaction=True)
        assert getattr(bpy.ops.ac9_cloth_sim,kind)()=={'RUNNING_MODAL'};op=CP._active
        expected='Side.001' if kind=='duplicate_pieces' else 'Side_R'
        assert op._copy_anchor_name==expected,(op._copy_anchor_name,expected)
        with patch.object(CP,'viewport',return_value=(area,region)),patch.object(DT,'mouse_mm',return_value=[1200,800]):
            op.modal(ctx,event());np.testing.assert_allclose(D.piece_center(D.piece(op._copy_preview,expected)),[1200,800])
            op.modal(ctx,event('LEFTMOUSE','PRESS'))
        assert SEL.lead(ctx)==expected
        return expected
    run('active_duplicate_anchor',lambda:active_anchor('duplicate_pieces'))
    run('active_mirror_anchor',lambda:active_anchor('mirror_copy'))
    def clone_group():
        sys.path.insert(0,str(root/'tests'))
        from symmetry_fixture import fixture,assert_valid
        results=[]
        for witness in (False,True):
            reset();bpy.ops.ac9_cloth_sim.new_pattern()
            sp,n,l,_=fixture('both_x',closed=True)
            if witness:D.add_polygon(sp,[[1000,-1000],[2010,-1000],[2010,1000],[1000,1000]],'Unrelated')
            assert_valid(sp);DT.commit(ctx,sp,[p['name'] for p in sp['pieces']]);assert PV.integrity(ctx,sp)['ok']
            assert bpy.ops.ac9_cloth_sim.clone_shape(piece=n,line=l,sew=True)=={'RUNNING_MODAL'};op=CP._active
            centers=[D.piece_center(D.piece(op._copy_candidate,nm)) for nm in op._copy_names]
            results.append(np.array(centers));op.modal(ctx,event('ESC','PRESS'))
        np.testing.assert_allclose(results[0],results[1],atol=1e-4)
        return {'unrelated_piece_position_effect_mm':float(np.max(np.abs(results[0]-results[1]))),'copies':len(results[0])}
    run('clone_group_ignores_unrelated_pieces',clone_group)
output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
if out['errors']:raise SystemExit(1)
