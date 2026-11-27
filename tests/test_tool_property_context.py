# SPDX-License-Identifier: GPL-3.0-or-later
"""Real selection and RNA contracts. Blender --python this.py -- package_parent output.json."""
import sys,json,runpy,traceback,importlib,inspect
from pathlib import Path
ARGS=sys.argv[sys.argv.index('--')+1:]
globals().update(runpy.run_path(str(Path(__file__).with_name('fixture_tool_selection.py'))))
R={'cases':[],'errors':[]}
def check(name,fn):
    try:
        value=fn();R['cases'].append(dict(name=name,passed=True,result=value))
    except Exception:R['cases'].append(dict(name=name,passed=False,error=traceback.format_exc()))
def eq(a,b):assert a==b,(a,b)
def read():return ST.get(ctx)[0]
def pcs():return S.piece_index(read())
def select(names):SEL.apply(ctx,names,active=names[0] if names else None,interaction=True)
try:
    data=build(wide=True);R['fixture']=data['fixture'];base=S.dumps(read())
    view=View();props=ctx.scene.ac9_cloth_sim
    def box():
        seed(data,'SET','none','SEAM')
        DT.box_transform(view,(20,20,730,160))
        eq(set(SEL.ids(ctx)),{'A','B','C'});eq(TT.resolve(ctx,read()),'PIECE')
        eq(DT.transform_targets(ctx,read(),('piece','A'))[0],['A','B','C'])
        return snapshot()
    check('whole_mixed_box_keeps_piece_attributes',box)
    def line_only():
        DT.box_transform(view,(680,70,730,90))
        eq(SEL.ids(ctx),[]);eq(TT.resolve(ctx,read()),'GARMENT')
        return snapshot()
    check('whole_line_only_has_no_piece_write_target',line_only)
    area=next(a for a in ctx.screen.areas if a.type=='VIEW_3D')
    region=next(r for r in area.regions if r.type=='WINDOW')
    def switching():
        ctx.scene.ac9_cloth_sim.pattern_tools=True
        with ctx.temp_override(area=area,region=region):
            bpy.ops.wm.tool_set_by_id(name='ac9_cloth_sim.edit_pattern_tool')
            R.setdefault('tool_debug',[]).append([TB.active_tool(ctx),TT.snapshot(ctx)])
            DT.select_internal(ctx,'F',data['lid']);eq(TT.resolve(ctx,read()),'INTERNAL')
            bpy.ops.wm.tool_set_by_id(name='ac9_cloth_sim.transform_pattern_tool')
            R.setdefault('tool_debug',[]).append([TB.active_tool(ctx),TT.snapshot(ctx)])
            eq(TT.resolve(ctx,read()),'GARMENT')
            bpy.ops.wm.tool_set_by_id(name='ac9_cloth_sim.edit_pattern_tool')
            eq(TT.resolve(ctx,read()),'INTERNAL')
        return snapshot()
    check('edit_whole_edit_restores_only_valid_tool_target',switching)
    for prop,key,values,target,default in [('piece_distance','mesh_mm',[10.,20.],10.,props.mesh_size),
        ('piece_thickness','thickness',[True,False],True,True),('piece_layer','layer',[0,2],0,0),
        ('piece_strengthen_factor','strengthen_factor',[5.,8.],5.,5.)]:
        def same(prop=prop,key=key,values=values,target=target,default=default):
            sp=S.loads(base);idx=S.piece_index(sp)
            for n,v in zip(('A','B'),values):idx[n][key]=v
            ST.put(ctx,sp,seams=False);select(['A','B'])
            setattr(props,prop,target)
            actual=[pcs()[n].get(key,default) for n in ('A','B')];eq(actual,[target,target])
            eq(S.check(read()),[])
            return actual
        check('same_representative_value_'+prop,same)
    def mirror():
        sp=S.loads(base);twin=DR.mirror_copy(sp,'E');ST.put(ctx,sp,seams=False)
        PV.rebuild(ctx,read(),ST.get(ctx)[1]);ob=bpy.data.objects.new('SharedPair',None)
        ctx.scene.collection.objects.link(ob);ob[PLV.PROP]='E';ob[PLV.TWIN_PROP]=twin;ob[PLV.GARMENT_PROP]=read()['name']
        select([twin]);eq(SEL.ids(ctx),[twin]);eq(TT._selected_piece(ctx,read()),twin)
        SEL.apply(ctx,[twin],'SUB',active='KEEP',interaction=True);eq(SEL.ids(ctx),[])
        return {'selected_after_sub':SEL.ids(ctx)}
    check('shared_object_does_not_add_unselected_mirror_side',mirror)
    def foreign():
        select(['A']);ob=bpy.data.objects.new('OtherDocument_B',None);ctx.scene.collection.objects.link(ob)
        ob[PLV.PROP]='B';ob[PLV.GARMENT_PROP]='DifferentDocument';ob.select_set(True);ctx.view_layer.objects.active=ob
        eq(SEL.ids(ctx),['A']);eq(TB.select_object(ctx,ob),False)
        before=S.piece_strengthen_factor(pcs()['B']);props.piece_strengthen_factor=11.
        eq(S.piece_strengthen_factor(pcs()['B']),before)
        return {'selected':SEL.ids(ctx),'unchanged_B':before}
    check('foreign_same_id_cannot_enter_panel_or_write_target',foreign)
    def excluded():
        coll=ctx.view_layer.layer_collection.children[PV.COLL_NAME];coll.exclude=True;ctx.view_layer.update()
        select(['B']);eq(SEL.ids(ctx),['B']);eq(TT._selected_piece(ctx,read()),'B')
        coll.exclude=False;ctx.view_layer.update();SEL.refresh(ctx)
        eq(SEL.pattern_pieces(ctx),['B'])
        return snapshot()
    check('excluded_patterns_keep_saved_logical_selection',excluded)
    def measure():
        MEE.select(ctx,'pattern:M');TB.select_piece(ctx,'A')
        bpy.ops.ac9_cloth_sim.measure_select(key='pattern:M')
        eq(TT.resolve(ctx,read()),'MEASURE')
        ctx.scene.ac9_msel_name='Renamed';eq(TT.resolve(ctx,read()),'MEASURE')
        eq(MEE.sel(ctx),'pattern:Renamed')
        return snapshot()
    check('measurement_list_return_and_rename_keep_detail',measure)
    def edges():
        with ctx.temp_override(area=area,region=region):
            bpy.ops.wm.tool_set_by_id(name='ac9_cloth_sim.edit_pattern_tool')
            DT.select_edge(ctx);DT.select_corner(ctx);DT.select_ipoint(ctx)
            e=pcs()['A']['edges'][0]['id'];DT.select_edge(ctx,'A',e);DT.select_corner(ctx,'A',e,extend=True)
            DT.select_edge(ctx,'A',e,extend=True);eq(TT.resolve(ctx,read()),'EDGES')
            return snapshot()
    check('mixed_corner_survives_edge_removal',edges)
    def group_drag():
        from types import SimpleNamespace
        DT.select_internal(ctx);DT.select_edge(ctx);DT.select_corner(ctx);DT.select_ipoint(ctx)
        for ob in set(PLV.objects(read()).values()):bpy.data.objects.remove(ob,do_unlink=True)
        sp=S.loads(S.dumps(read()));moved=S.loads(S.dumps(sp));line_ids=[x['id'] for x in pcs()['F']['internals']]
        with ctx.temp_override(area=area,region=region):
            bpy.ops.wm.tool_set_by_id(name='ac9_cloth_sim.edit_pattern_tool')
            for i,lid in enumerate(line_ids):DT.select_internal(ctx,'F',lid,extend=i>0)
            SET.select_seam(ctx,'U1')
            for lid in line_ids:DR.move_internal(moved,'F',lid,'all',(5.,0.))
            owner=ST.edit_owner(ctx)
            op=SimpleNamespace(_owner=owner,box=None,dragging=True,grab=('iline','F',line_ids[0]),
                grab_mm=np.array([690.,80.]),spec=sp,group=[('iline','F',lid) for lid in line_ids],tgroup=None,
                delta=np.array([5.,0.]),rigid=None,ipoint=None,mode='EDIT',report=lambda *a:None)
            op._end=lambda c:DT.AC9_OT_SimPatternDrag._end(op,c)
            DT._state['drag']={'owner':owner,'preview':moved,'names':['F'],'bad':[],'grab':op.grab}
            inspect.unwrap(DT.AC9_OT_SimPatternDrag.modal)(op,ctx,SimpleNamespace(type='LEFTMOUSE',value='RELEASE'))
            eq(TT.resolve(ctx,read()),'INTERNAL');eq(S.check(read()),[])
            return snapshot()
    check('group_drag_commit_reactivates_current_tool_detail',group_drag)
    def blank():
        from types import SimpleNamespace
        cases={}
        actions={'FABRIC':lambda:TT.focus_item(ctx,'FABRIC',data['fids'][0]),
                 'MEASURE':lambda:MEE.select(ctx,'pattern:Renamed'),
                 'SEAM':lambda:SET.select_seam(ctx,'U1'),
                 'INTERNAL':lambda:DT.select_internal(ctx,'F',data['lid'])}
        for kind,action in actions.items():
            select(['A']);action()
            op=SimpleNamespace(p={'event':SimpleNamespace(shift=False),'kind':'NONE','idx':-1},_ob=lambda:None)
            TB.AC9_OT_SimPlacePress._click(op,ctx)
            eq(TT.resolve(ctx,read()),'GARMENT');eq(SEL.ids(ctx),[]);cases[kind]=TT.snapshot(ctx)
        return cases
    check('empty_click_clears_other_detail_kinds',blank)
    def layer_identity():
        layer=ctx.view_layer;old=layer.name;SET.select_seam(ctx,'U1')
        layer.name='RenamedLayer';eq(TT.resolve(ctx,read()),'SEAM')
        other=ctx.scene.view_layers.new(old)
        ctx.window.view_layer=other
        assert TT.resolve(ctx,read())!='SEAM'
        ctx.window.view_layer=layer;eq(TT.resolve(ctx,read()),'SEAM')
        return TT.snapshot(ctx)
    check('view_layer_identity_survives_rename_not_name_reuse',layer_identity)
    def saved_selection():
        select(['B']);coll=ctx.view_layer.layer_collection.children[PV.COLL_NAME]
        coll.exclude=True;ctx.view_layer.update();select(['B'])
        target=Path(ARGS[1]).with_suffix('.owned.blend')
        bpy.ops.wm.save_as_mainfile(filepath=str(target))
        bpy.ops.wm.open_mainfile(filepath=str(target))
        eq(SEL.ids(ctx),['B']);eq(TT._selected_piece(ctx,read()),'B')
        return {'selection':SEL.ids(ctx),'excluded':ctx.view_layer.layer_collection.children[PV.COLL_NAME].exclude}
    check('saved_logical_selection_with_excluded_pattern',saved_selection)
    def document_identity():
        original=ST.text(ctx);select(['B']);results={}
        for kind in ('fresh','copy'):
            if kind=='copy':
                target=original.copy()
                ctx.scene.ac9_cloth_sim.spec_text=target
            else:
                sp=S.loads(original.as_string());sp['name']='OtherDocument'
                target=ST.new_text(ctx,sp['name']);target.write(S.dumps(sp))
            ST.invalidate();eq(SEL.ids(ctx),[])
            SEL.watch(ctx.scene,None);eq(SEL.ids(ctx),[])
            before=S.piece_strengthen_factor(S.piece_index(read())['B'])
            props.piece_strengthen_factor=18.
            eq(S.piece_strengthen_factor(S.piece_index(read())['B']),before)
            eq(TB.select_piece(ctx,'B'),False)
            results[kind]={'logical':SEL.ids(ctx),'factor':before}
            ctx.scene.ac9_cloth_sim.spec_text=original;ST.invalidate();select(['B'])
        return results
    check('new_and_copied_text_cannot_adopt_old_piece_flags',document_identity)
    R['complete']=True
except Exception:R['errors'].append(traceback.format_exc())
R['passed']=sum(c['passed'] for c in R['cases']);R['failed']=len(R['cases'])-R['passed']
Path(ARGS[1]).write_text(json.dumps(R,ensure_ascii=False,indent=2),'utf-8')
if R['errors'] or R['failed']:raise RuntimeError('See tool property contract receipt')
