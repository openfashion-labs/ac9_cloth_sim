"""Headless operation/identity regressions, no physics or source-file writes."""
import bpy, importlib.util, sys, json, traceback, time, tempfile
from pathlib import Path
import numpy as np
from types import SimpleNamespace
HERE=Path(__file__).resolve().parent
args=sys.argv[sys.argv.index('--')+1:]
root=Path(args[0]) if args else HERE.parent
mode='synthetic'
OUTPUT=Path(args[1]) if len(args)>1 else Path(tempfile.gettempdir())/'ac9_pattern_integrity.json'
mod=importlib.util.spec_from_file_location('ac9_cloth_sim',root/'__init__.py',submodule_search_locations=[str(root)])
addon=importlib.util.module_from_spec(mod); sys.modules['ac9_cloth_sim']=addon; mod.loader.exec_module(addon)
from ac9_cloth_sim import store, pattern_view as PV, draft_tool as DT, selection as SEL, ui
from ac9_cloth_sim.core import draft as D, spec as S
out={'source':str(root),'mode':mode,'checks':[],'errors':[]}
ctx=bpy.context
def test(name, fn):
    try:
        value=fn(); out['checks'].append({'name':name,'result':value})
    except Exception:
        out['errors'].append({'name':name,'error':traceback.format_exc()})
def reset():
    for name in (PV.COLL_NAME,):
        col=bpy.data.collections.get(name)
        if col:
            for ob in list(col.objects):bpy.data.objects.remove(ob,do_unlink=True)
    ob=PV.layout_empty(ctx)
    if ob:bpy.data.objects.remove(ob,do_unlink=True)
    ctx.scene.ac9_cloth_sim.spec_text=None
    store.invalidate()
    sp=S.new_spec('Integrity regression'); store.new_text(ctx,sp['name'])
    for i in range(3):D.add_polygon(sp,D.rect_points(np.array([i*400.,0.]),np.array([i*400.+340.,245.])), 'Rectangle')
    note=DT.commit(ctx,sp,[p['name'] for p in sp['pieces']])
    assert not note,note
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    assert len(PV.piece_objects())==3
    return sp
def duplicate(pid='Rectangle.002'):
    ctx.view_layer.update()
    ob=PV.piece_objects()[pid]
    for o in ctx.selected_objects:o.select_set(False)
    ob.select_set(True);ctx.view_layer.objects.active=ob
    ctx.view_layer.update()
    assert bpy.ops.object.duplicate(linked=False)=={'FINISHED'}
    return next(o for o in PV._groups()[pid] if o != ob)
def frozen():
    return (store.text(ctx).as_string(),tuple((o.session_uid,o.data.session_uid if o.data else 0,
            tuple(tuple(v.co) for v in o.data.vertices) if o.type=='MESH' else ())
            for o in bpy.data.collections[PV.COLL_NAME].objects))
def refused(fn):
    before=frozen()
    try:fn()
    except PV.PatternIntegrityError as e:message=str(e)
    else:raise AssertionError('ambiguous operation succeeded')
    assert before==frozen(),'refusal changed document or geometry'
    return message
def regular():
    reset()
    SEL.apply(ctx,['Rectangle.002'],'SET',active='Rectangle.002',interaction=True)
    assert bpy.ops.ac9_cloth_sim.duplicate_pieces()=={'RUNNING_MODAL'}
    from ac9_cloth_sim import copy_place as CP
    op=CP._active
    sp=CP.move_candidate(op._copy_candidate,op._copy_names,op._copy_anchor,[1500,300])
    op._copy_end(ctx)
    assert op._copy_finish(ctx,sp)=={'FINISHED'}  # Integrity only; native click/Undo are exercised by GUI tests.
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    sp,_,owner=store.begin_edit(ctx);D.move_piece(sp,'Rectangle',shift=(30,40))
    assert not DT.commit(ctx,sp,['Rectangle'],owner=owner)
    SEL.apply(ctx,['Rectangle.003'],'SET',active='Rectangle.003',interaction=True)
    assert bpy.ops.ac9_cloth_sim.delete_piece()=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    return {'pieces':3,'objects':len(PV.piece_objects())}
def ambiguous():
    reset();duplicate()
    sp=S.loads(store.text(ctx).as_string());D.move_piece(sp,'Rectangle.002',shift=(759.748,119.526))
    c=PV.integrity(ctx,store.get(ctx)[0]);assert len(c['duplicates'])==1
    assert 'Rectangle.002' not in PV.piece_objects()
    assert DT.missing_pieces(ctx,store.get(ctx)[0])==[]
    messages=[refused(lambda:store.begin_edit(ctx)),refused(lambda:DT.commit(ctx,sp,['Rectangle.002'])),
              refused(lambda:store.put(ctx,sp)),refused(lambda:PV.update_pieces(ctx,sp,{p['name']:S.Outline(p) for p in sp['pieces']},['Rectangle.002']))]
    # Use an unambiguous selected piece: deleting it must not commit amid a different duplicate either.
    SEL.apply(ctx,['Rectangle'],'SET',active='Rectangle',interaction=True)
    before=frozen();r=bpy.ops.ac9_cloth_sim.delete_piece();assert r=={'CANCELLED'},r;assert before==frozen()
    # Restore now archives both ambiguous displays before recreating the logical piece.
    body=store.text(ctx).as_string();r=bpy.ops.ac9_cloth_sim.restore_pieces();assert r=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok'] and body==store.text(ctx).as_string()
    return {'lookup':len(PV.piece_objects()),'check':c,'refusals':len(messages)+1,'restored':True,'message':messages[0]}
def legacy():
    reset()
    for o in PV.piece_objects().values():del o[PV.OWNER_PROP]
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    sp,_,owner=store.begin_edit(ctx);D.move_piece(sp,'Rectangle',shift=(10,10));assert not DT.commit(ctx,sp,['Rectangle'],owner=owner)
    return True
def missing():
    reset();bpy.data.objects.remove(PV.piece_objects()['Rectangle.002'],do_unlink=True)
    c=PV.integrity(ctx,store.get(ctx)[0]);assert c['missing']==('Rectangle.002',)
    refused(lambda:store.begin_edit(ctx))
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    assert PV.piece_objects()['Rectangle.002'][PV.OWNER_PROP]==store.text(ctx)
    return True
def equal_counts():
    reset();bpy.data.objects.remove(PV.piece_objects()['Rectangle'],do_unlink=True);duplicate()
    c=PV.integrity(ctx,store.get(ctx)[0]);assert c['missing'] and c['duplicates'];assert len(PV._groups())==2
    assert sum(PV.PIECE_PROP in o for o in bpy.data.collections[PV.COLL_NAME].objects)==3
    refused(lambda:store.begin_edit(ctx));return c
def foreign():
    reset();other=bpy.data.texts.new('Other document');PV.piece_objects()['Rectangle'][PV.OWNER_PROP]=other
    c=PV.integrity(ctx,store.get(ctx)[0]);assert c['foreign'];refused(lambda:store.begin_edit(ctx));return c
def unexpected():
    reset();ob=duplicate();ob[PV.PIECE_PROP]='NotInDocument'
    c=PV.integrity(ctx,store.get(ctx)[0]);assert c['unexpected']==('NotInDocument',);refused(lambda:store.begin_edit(ctx));return c
def invalid():
    reset();ob=bpy.data.objects.new('Wrong type',None);ob[PV.PIECE_PROP]='Rectangle';bpy.data.collections[PV.COLL_NAME].objects.link(ob)
    c=PV.integrity(ctx,store.get(ctx)[0]);assert c['invalid'];refused(lambda:store.begin_edit(ctx));return c
class Layout:
    def __init__(self):self.lines=[]
    def box(self):return self
    def label(self,**kw):self.lines.append(kw.get('text',''))
    def operator(self,*a,**kw):
        assert a[0]=='ac9_cloth_sim.restore_pieces'
        return SimpleNamespace()
def panel():
    reset();duplicate();sp=store.get(ctx)[0];lay=Layout();assert ui.AC9_PT_SimMissing.poll(ctx)
    t=time.perf_counter();ui.draw_missing_pieces(lay,ctx,sp);ms=(time.perf_counter()-t)*1000
    assert len(lay.lines)>2;return {'ms':ms,'lines':lay.lines}
def history():
    reset();bpy.ops.ed.undo_push(message='healthy');duplicate();bpy.ops.ed.undo_push(message='duplicated')
    bpy.ops.ed.undo();assert PV.integrity(ctx,store.get(ctx)[0])['ok'];assert PV._history_checks[ctx.scene.session_uid]['ok']
    bpy.ops.ed.redo();assert not PV.integrity(ctx,store.get(ctx)[0])['ok'];assert PV._history_checks[ctx.scene.session_uid]['duplicates']
    refused(lambda:store.begin_edit(ctx));return True
def roundtrip():
    reset();duplicate()
    with tempfile.TemporaryDirectory() as tmp:
        path=str(Path(tmp)/'duplicate.blend');bpy.ops.wm.save_as_mainfile(filepath=path,copy=True);bpy.ops.wm.open_mainfile(filepath=path)
        c=PV._history_checks[ctx.scene.session_uid];assert c['duplicates'];refused(lambda:store.begin_edit(ctx))
    return c
def destructive_entries():
    results={}
    for name in ('refresh','new_pattern'):
        reset();duplicate();before=frozen()
        result=getattr(bpy.ops.ac9_cloth_sim,name)()
        assert result=={'CANCELLED'} and before==frozen(),name
        results[name]=sorted(result)
    reset();assert bpy.ops.ac9_cloth_sim.refresh()=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    assert bpy.ops.ac9_cloth_sim.new_pattern()=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    return results
def proposal_ids():
    reset();sp,_,owner=store.begin_edit(ctx);sp['pieces'][1]['name']=sp['pieces'][0]['name']
    return refused(lambda:DT.commit(ctx,sp,['Rectangle'],owner=owner))
def scene_legacy():
    reset();atlas=PV.piece_objects()
    for ob in atlas.values():del ob[PV.OWNER_PROP]
    other=bpy.data.scenes.new('Separate identity test')
    text=bpy.data.texts.new('Other scene document');text.from_string(store.text(ctx).as_string())
    other.ac9_cloth_sim.spec_text=text
    before=frozen();doc=text.as_string()
    with ctx.temp_override(scene=other,view_layer=other.view_layers[0]):
        assert not PV.piece_objects()
        c=PV.integrity(ctx,store.get(ctx)[0]);assert c['other_scene']
        try:store.begin_edit(ctx)
        except PV.PatternIntegrityError:pass
        else:raise AssertionError('another scene accepted legacy objects')
    assert before==frozen() and doc==text.as_string()
    bpy.data.scenes.remove(other)
    return c
def modal_cleanup():
    reset();sp,_,owner=store.begin_edit(ctx);D.move_piece(sp,'Rectangle',shift=(10,0));duplicate()
    area=next(a for a in ctx.screen.areas if a.type=='VIEW_3D')
    reports=[]
    op=SimpleNamespace(_owner=owner,box=None,dragging=True,delta=np.array([10.,0.]),grab=('piece','Rectangle'),
        rigid=None,spec=store.get(ctx)[0],tgroup=None,as_pointer=lambda:987654,report=lambda *r:reports.append(str(r)))
    op._end=lambda c:DT.AC9_OT_SimPatternDrag._end(op,c)
    DT._state['drag']=dict(owner=owner,bad=[],preview=sp,names=['Rectangle'])
    before=frozen()
    with ctx.temp_override(area=area):r=DT.AC9_OT_SimPatternDrag.modal(op,ctx,SimpleNamespace(type='LEFTMOUSE',value='RELEASE'))
    assert r=={'CANCELLED'} and DT._state['drag'] is None and before==frozen() and reports
    return {'result':sorted(r),'preview_retained':False}
def material():
    from ac9_cloth_sim import operators
    reset();duplicate();mat=bpy.data.materials.new('Refused material')
    assert not mat.use_fake_user
    refused(lambda:operators.set_garment_look(ctx,mat))
    assert not mat.use_fake_user
    return True
def placed_preserved():
    from ac9_cloth_sim import placed_view as PLV, garment_owner as GO
    sp=reset();sp['placement']={'Rectangle':{'anchor':'flat'}};store.put(ctx,sp)
    ob=bpy.data.objects.new('Placed refusal check',bpy.data.meshes.new('Placed refusal mesh'))
    PLV._collection(ctx).objects.link(ob);ob[PLV.PROP]='Rectangle';ob[GO.PLACED]=store.text(ctx)
    uid=ob.session_uid;assert PLV.objects(sp)['Rectangle']==ob
    duplicate();before=frozen()
    r=bpy.ops.ac9_cloth_sim.reset_placed()
    assert r=={'CANCELLED'} and before==frozen() and any(o.session_uid==uid for o in bpy.data.objects)
    return {'result':sorted(r),'placed_preserved':True}
def reload_manual():
    results=[]
    for add in (True,False):
        sp=reset()
        if add:D.add_polygon(sp,D.rect_points(np.array([1400.,0.]),np.array([1740.,245.])), 'Rectangle')
        else:D.delete_piece(sp,'Rectangle.002')
        store.text(ctx).from_string(S.dumps(sp))
        # No explicit cache invalidation: Reload itself must read the new Text immediately.
        r=bpy.ops.ac9_cloth_sim.refresh()
        assert r=={'FINISHED'}
        assert PV.integrity(ctx,store.get(ctx)[0])['ok']
        results.append(len(PV.piece_objects()))
    assert results==[4,2]
    return results
def inspection_selection():
    reset();duplicate()
    ob=bpy.data.objects.new('Unaffected collider',bpy.data.meshes.new('Unaffected collider mesh'))
    ctx.scene.collection.objects.link(ob);ctx.view_layer.update()
    before=frozen();r=bpy.ops.ac9_cloth_sim.collider_select(name=ob.name)
    assert r=={'FINISHED'} and ob.select_get() and before==frozen()
    return True
def all_geometry():
    return {o.session_uid:(o.name,o.data.session_uid if o.data else 0,
        tuple(tuple(v.co) for v in o.data.vertices) if o.type=='MESH' else ()) for o in bpy.data.objects}
def restore_history():
    reset();duplicate();body=store.text(ctx).as_string();before=all_geometry()
    bpy.ops.ed.undo_push(message='restore input')
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok'] and store.text(ctx).as_string()==body
    after=all_geometry()
    assert all(after[k]==v for k,v in before.items())
    assert sum(PV.RECOVERY_PROP in o for o in bpy.data.objects)>=2
    bpy.ops.ed.undo_push(message='restored display')
    bpy.ops.ed.undo();assert not PV.integrity(ctx,store.get(ctx)[0])['ok']
    assert all_geometry()==before
    bpy.ops.ed.redo();assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    assert all_geometry()==after
    count=len(bpy.data.objects)
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'CANCELLED'}
    assert count==len(bpy.data.objects)
    return {'undo_mismatch':True,'redo_ok':True,'idempotent':True}
def restore_mixed():
    reset();bpy.data.objects.remove(PV.piece_objects()['Rectangle'],do_unlink=True);duplicate()
    body=store.text(ctx).as_string();old=all_geometry()
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    assert len(PV.piece_objects())==3 and PV.integrity(ctx,store.get(ctx)[0])['ok']
    assert body==store.text(ctx).as_string()
    assert all(all_geometry()[k]==v for k,v in old.items())
    return True
def restore_unexpected():
    reset();o=duplicate();o[PV.PIECE_PROP]='Unexpected';uid=o.session_uid
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    retired=next(o for o in bpy.data.objects if o.session_uid==uid)
    assert retired.get(PV.RECOVERY_PROP)=='Unexpected' and PV.PIECE_PROP not in retired
    return True
def restore_failure():
    reset();bpy.data.objects.remove(PV.piece_objects()['Rectangle'],do_unlink=True);duplicate()
    before=all_geometry();body=store.text(ctx).as_string();meshes={m.session_uid for m in bpy.data.meshes}
    real=PV._piece_mesh;calls=[]
    def fault(*a):
        calls.append(1)
        if len(calls)==2:raise RuntimeError('injected allocation failure')
        return real(*a)
    PV._piece_mesh=fault
    try:assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'CANCELLED'}
    finally:PV._piece_mesh=real
    assert all_geometry()==before and body==store.text(ctx).as_string()
    assert {m.session_uid for m in bpy.data.meshes}==meshes
    return True
def restore_publish_failure():
    reset();o=duplicate();o.select_set(True);ctx.view_layer.objects.active=o
    active=o.session_uid;selected={ob.session_uid for ob in ctx.view_layer.objects.selected}
    before=all_geometry();body=store.text(ctx).as_string()
    groups={c.session_uid:tuple(o.session_uid for o in c.objects) for c in bpy.data.collections}
    real=PV.require_integrity
    def fault(*a,**kw):raise RuntimeError('injected final restore validation failure')
    PV.require_integrity=fault
    try:
        # Direct transaction test: Blender's operator entry can first normalize an unowned duplicate selection.
        try:PV.restore(ctx,*store.get(ctx))
        except RuntimeError:pass
        else:raise AssertionError('injected publication failure did not propagate')
    finally:PV.require_integrity=real
    assert all_geometry()==before and body==store.text(ctx).as_string()
    assert groups=={c.session_uid:tuple(o.session_uid for o in c.objects) for c in bpy.data.collections}
    assert ctx.view_layer.objects.active.session_uid==active
    assert selected=={ob.session_uid for ob in ctx.view_layer.objects.selected}
    return True
def restore_shared_archive():
    reset();duplicate();assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    owner=store.text(ctx)
    archive=next(c for c in ctx.scene.collection.children if c.get(PV.RECOVERY_COLLECTION)==owner)
    other=bpy.data.scenes.new('Shared recovery scene');other.collection.children.link(archive)
    external={o.session_uid for o in other.objects}
    duplicate();assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    assert external=={o.session_uid for o in other.objects}
    assert PV.integrity(ctx,store.get(ctx)[0])['ok']
    bpy.data.scenes.remove(other)
    return True
def restore_operator_active():
    reset();duplicate();SEL.apply(ctx,['Rectangle.001'],'SET',active='Rectangle.001',interaction=True)
    active=ctx.view_layer.objects.active.session_uid
    before=all_geometry();body=store.text(ctx).as_string();real=PV.require_integrity
    def fault(*a,**kw):raise RuntimeError('injected operator publication failure')
    PV.require_integrity=fault
    try:assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'CANCELLED'}
    finally:PV.require_integrity=real
    assert all_geometry()==before and body==store.text(ctx).as_string()
    assert ctx.view_layer.objects.active.session_uid==active
    return True
def restore_legacy_ambiguity():
    reset();duplicate()
    for o in PV._groups()['Rectangle.002']:del o[PV.OWNER_PROP]
    other=bpy.data.texts.new(store.TEXT_NAME+'_other_owner');other.from_string(store.text(ctx).as_string())
    old=all_geometry();body=store.text(ctx).as_string()
    assert PV.restore_plan(ctx,store.get(ctx)[0])['reason']
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'CANCELLED'}
    assert old==all_geometry() and body==store.text(ctx).as_string()
    bpy.data.texts.remove(other)
    return True
def restore_renamed_document():
    reset();duplicate()
    for o in PV._groups()['Rectangle.002']:del o[PV.OWNER_PROP]
    other=bpy.data.texts.new('User named garment document');other.from_string(store.text(ctx).as_string())
    scene=bpy.data.scenes.new('Other document owner');scene.ac9_cloth_sim.spec_text=other
    old=all_geometry();body=store.text(ctx).as_string()
    assert PV.restore_plan(ctx,store.get(ctx)[0])['reason']
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'CANCELLED'}
    assert old==all_geometry() and body==store.text(ctx).as_string()
    bpy.data.scenes.remove(scene);bpy.data.texts.remove(other)
    return True
def restore_shared_refusal():
    reset();o=duplicate();other=bpy.data.collections.new('User membership')
    ctx.scene.collection.children.link(other);other.objects.link(o)
    before=all_geometry();body=store.text(ctx).as_string()
    assert PV.restore_plan(ctx,store.get(ctx)[0])['reason']
    assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'CANCELLED'}
    assert before==all_geometry() and body==store.text(ctx).as_string() and o.name in other.objects
    return True
def restore_saved():
    reset();duplicate();assert bpy.ops.ac9_cloth_sim.restore_pieces()=={'FINISHED'}
    body=store.text(ctx).as_string()
    with tempfile.TemporaryDirectory() as tmp:
        p=str(Path(tmp)/'restored.blend');bpy.ops.wm.save_as_mainfile(filepath=p,copy=True);bpy.ops.wm.open_mainfile(filepath=p)
        assert PV.integrity(ctx,store.get(ctx)[0])['ok'] and body==store.text(ctx).as_string()
        archived=[o for o in bpy.data.objects if PV.RECOVERY_PROP in o]
        assert archived and all(any(c.hide_viewport and c.hide_render for c in o.users_collection) for o in archived)
        sp,_,owner=store.begin_edit(ctx);D.move_piece(sp,'Rectangle.002',shift=(1,0))
        assert not DT.commit(ctx,sp,['Rectangle.002'],owner=owner)
    return True
try:
    addon.register()
    assert hasattr(bpy.types.Scene,'ac9_cloth_sim')
    for name,fn in [('normal',regular),('ambiguous',ambiguous),('legacy',legacy),('missing_restore',missing),
                    ('same_total',equal_counts),('foreign_owner',foreign),('unexpected_id',unexpected),
                    ('invalid_type',invalid),('warning_panel',panel),('history',history),('roundtrip',roundtrip),
                    ('destructive_entries',destructive_entries),('proposal_ids',proposal_ids),
                    ('scene_legacy',scene_legacy),('modal_cleanup',modal_cleanup),('material',material),
                    ('placed_preserved',placed_preserved),('reload_manual',reload_manual),
                    ('inspection_selection',inspection_selection),('restore_history',restore_history),
                    ('restore_mixed',restore_mixed),('restore_unexpected',restore_unexpected),
                    ('restore_failure',restore_failure),('restore_shared_refusal',restore_shared_refusal),
                    ('restore_saved',restore_saved),('restore_publish_failure',restore_publish_failure),
                    ('restore_shared_archive',restore_shared_archive),('restore_legacy_ambiguity',restore_legacy_ambiguity),
                    ('restore_operator_active',restore_operator_active),('restore_renamed_document',restore_renamed_document)]:test(name,fn)
except Exception:out['errors'].append({'error':traceback.format_exc()})
out['ok']=not out['errors']
OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
