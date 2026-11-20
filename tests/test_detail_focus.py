# SPDX-License-Identifier: GPL-3.0-or-later
"""Interaction contracts for details: all target pairs, identity, ownership and internal projections.

Run with -- <package parent> <output.json>. Synthetic data only, no simulation or user file.
"""
import importlib
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

ARGS = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, ARGS[0])
import bpy
MOD = ARGS[2] if len(ARGS) > 2 else 'ac9_cloth_sim'
addon = importlib.import_module(MOD)
if MOD.startswith('bl_ext.'):
    import addon_utils
    addon_utils.enable(MOD, default_set=False, persistent=False)
else:
    addon.register()
TT, ST, TB, SEL, DT, SET, MEE, MT, BU, PV, PLV = [importlib.import_module(MOD+'.'+n) for n in
    ('tool_tab', 'store', 'toolbar', 'selection', 'draft_tool', 'seam_edit_tool', 'measure_edit', 'measure_tool',
     'button_ui', 'pattern_view', 'placed_view')]
S, DR, FA, BT = [importlib.import_module(MOD+'.core.'+n) for n in ('spec', 'draft', 'fabric', 'buttons')]
R = dict(cases=[], fixture={}, module=MOD, loaded_from=addon.__file__, blender=bpy.app.version_string)
ctx = bpy.context


def check(name, fn):
    try:
        fn()
        R['cases'].append(dict(name=name, passed=True))
    except Exception:
        R['cases'].append(dict(name=name, passed=False, error=traceback.format_exc()))


def equal(a, b):
    assert a == b, (a, b)


def shown():
    ctx.view_layer.update()
    return TT.resolve(ctx, ST.get(ctx)[0])


def current():
    return ST.get(ctx)[0]


def write(sp):
    t = ST.text(ctx)
    t.clear()
    t.write(S.dumps(sp))
    ST.invalidate()
    ST.sync_seam_list(ctx, ST.get(ctx)[0])


def target():
    return TT._focus['item']


try:
    sp = S.new_spec('Details')
    for i, n in enumerate(('A', 'B', 'C')):
        DR.add_polygon(sp, [[i*200,0],[i*200+100,0],[i*200+100,100],[i*200,100]], name=n)
    lines = [DR.add_internal(sp, 'A', [[10,10],[90,20]], mirror=False),
             DR.add_internal(sp, 'A', [[10,30],[90,40]], mirror=False)]
    edges = [e['id'] for e in S.piece_index(sp)['A']['edges']]
    def side(n, i):
        e = S.piece_index(sp)[n]['edges'][i]['id']
        return dict(piece=n, **{'from':[e,0.0], 'to':[e,1.0]}, dir=1)
    sp['seams'] = [dict(id='U'+str(i+1), a=side('A',i), b=side('B',i), source='user', check='') for i in range(2)]
    fabrics = [FA.add_custom(sp, 'Fabric '+str(i)) for i in range(2)]
    bonds = [FA.add_custom_interlining(sp, 'Bond '+str(i)) for i in range(2)]
    for i,n in enumerate(('A','B')):
        FA.set_pieces(sp, [n], fabrics[i])
        FA.set_pieces(sp, [n], bond=bonds[i], what='bond')
    buttons = [BT.add(sp,'A',[20+i*30,50],ctx.scene.ac9_button_style)['id'] for i in range(2)]
    sp['measure'] = {'pattern':[dict(name='M'+str(i), a={'piece':'A','edge':edges[0],'t':0.0},
                                    b={'piece':'A','edge':edges[0],'t':1.0}) for i in range(2)]}
    problems=S.check(sp)
    equal(problems, [])
    equal([S.piece_index(sp)[n]['bond_id'] for n in ('A','B')],bonds)
    ST.new_text(ctx, sp['name']).write(S.dumps(sp))
    ST.invalidate()
    spec, ol = ST.get(ctx)
    PV.rebuild(ctx,spec,ol)
    ST.sync_seam_list(ctx,spec)
    me=bpy.data.meshes.new('placed')
    me.from_pydata([(0,0,0),(.1,0,0),(0,0,.1)],[],[(0,1,2)])
    placed=bpy.data.objects.new('placed',me)
    PLV._collection(ctx).objects.link(placed)
    placed[PLV.PROP]='A'
    placed[PLV.GARMENT_PROP]=sp['name']
    placed[PLV.ORG_PROP]=[0,0,0]
    ctx.view_layer.update()
    equal(len(PV.piece_objects()),3)
    assert all(S.side_length(spec,sm[k],ol)>0 for sm in spec['seams'] for k in ('a','b'))
    R['fixture']=dict(status='ok', spec_problems=problems, pieces=3, seams=2,
                       positive_lengths=4, bodies='synthetic measurement records only', simulation=False)
    base=S.dumps(spec)
    def reset():
        write(S.loads(base))
        DT.select_internal(ctx)
        DT.select_edge(ctx)
        DT.select_corner(ctx)
        DT.select_ipoint(ctx)
        MEE.select(ctx,'')
        ctx.scene.ac9_button_active=''
        ctx.scene.ac9_button_sel=''
        SEL.deselect_all(ctx)
        TB.select_piece(ctx,'A')
        ctx.view_layer.update()
    actions={
        'PIECE':lambda i:bpy.ops.ac9_cloth_sim.select_piece(piece=('A','B')[i]),
        'SEAM':lambda i:bpy.ops.ac9_cloth_sim.pick_seam(seam=('U1','U2')[i]),
        'FABRIC':lambda i:bpy.ops.ac9_cloth_sim.fabric_select(fabric_id=fabrics[i]),
        'BOND':lambda i:bpy.ops.ac9_cloth_sim.interlining_select(interlining_id=bonds[i]),
        'BUTTON':lambda i:bpy.ops.ac9_cloth_sim.button_select(button=buttons[i]),
        'MEASURE':lambda i:MEE.select(ctx,'pattern:M'+str(i)),
        'INTERNAL':lambda i:DT.select_internal(ctx,'A',lines[i]),
        'EDGES':lambda i:DT.select_edge(ctx,'A',edges[i]),
    }
    expected_targets={'PIECE':'B','SEAM':'U2','FABRIC':fabrics[1],'BOND':bonds[1],'BUTTON':buttons[1],
                      'MEASURE':('pattern:M1',0),'INTERNAL':('A',lines[1]),'EDGES':(('edge','A',edges[1]),)}
    for src in actions:
        for dst in actions:
            def pair(src=src,dst=dst):
                reset()
                actions[src](0)
                equal(shown(),src)
                actions[dst](1)
                equal(shown(),dst)
                first=target()
                if hasattr(TT,'publish'):equal(first,expected_targets[dst])
                actions[dst](1)
                equal(shown(),dst)
                equal(target(),first)
                if dst != 'PIECE':
                    SEL.apply(ctx,['C'],active='C')
                    equal(shown(),dst)
                    equal(target(),first)
            check('pair_'+src+'_to_'+dst, pair)
    def projections():
        reset(); SET.select_seam(ctx,'U1')
        before=target()
        SEL.apply(ctx,['A'],active='A',view='world')
        equal(shown(),'SEAM'); equal(target(),before)
        SEL.apply(ctx,['B'],active='B')  # Even an internal logical selection change is not a fresh click.
        equal(shown(),'SEAM')
        with SEL.borrow(ctx,[PV.piece_objects()['C']]):
            ctx.view_layer.update()
        equal(shown(),'SEAM')
    check('internal_projection_and_borrow',projections)
    def pending():
        reset()
        for ob in SEL.objects(ctx): ob.select_set(False)
        ob=PV.piece_objects()['B'];ob.select_set(True);ctx.view_layer.objects.active=ob
        SET.select_seam(ctx,'U1')
        SEL.watch(ctx.scene,None)
        equal(shown(),'SEAM')
    check('old_selection_notification_after_seam',pending)
    def external():
        reset(); SET.select_seam(ctx,'U1')
        for ob in SEL.objects(ctx): ob.select_set(False)
        ob=PV.piece_objects()['B'];ob.select_set(True);ctx.view_layer.objects.active=ob
        SEL.watch(ctx.scene,None)
        equal(shown(),'PIECE');equal(SEL.lead(ctx),'B')
    check('external_piece_change',external)
    def shift():
        reset(); SET.select_seam(ctx,'U1');TB.select_piece(ctx,'A')
        SET.select_seam(ctx,'U1',extend=True)
        equal(shown(),'SEAM')
    check('shift_same_seam',shift)
    def derived():
        reset(); SET.select_seam(ctx,'U1')
        seq=TT._focus['serial']
        altered=S.loads(base);altered['seams'].reverse();write(altered)
        equal(shown(),'SEAM');equal(ST.active_seam(ctx,current())['id'],'U1')
        equal(TT._focus['serial'],seq)
        TB.select_piece(ctx,'A');seq=TT._focus['serial']
        write(S.loads(base));equal(shown(),'PIECE');equal(TT._focus['serial'],seq)
        ctx.scene.ac9_cloth_sim.seam_index=1  # Actual UIList RNA input, not internal rebuild.
        equal(shown(),'SEAM');equal(target(),'U2')
    check('list_rebuild_identity_and_RNA_input',derived)
    def remove():
        reset();SET.select_seam(ctx,'U1')
        altered=S.loads(base);altered['seams']=altered['seams'][1:];write(altered)
        equal(shown(),'PIECE');equal(ST.active_seam(ctx,current()),None)
        reset();actions['BUTTON'](0)
        altered=S.loads(base);altered['buttons']=altered['buttons'][1:];write(altered)
        equal(shown(),'PIECE')
    check('deleted_target_never_rebinds',remove)
    def menus():
        reset();SET.select_seam(ctx,'U1');SET.select_seam(ctx,'U2',extend=True)
        TB.select_piece(ctx,'A');DT._menu_select(ctx,('seam','U1'))
        equal(shown(),'SEAM');equal(target(),'U1')
        equal({s['id'] for s in ST.selected_seams(ctx,current())},{'U1','U2'})
        SEL.apply(ctx,['A','B'],active='A');SET.select_seam(ctx,'U1')
        DT._menu_select(ctx,('piece','B'))
        equal(shown(),'PIECE');equal(target(),'B');equal(set(SEL.ids(ctx)),{'A','B'})
        actions['MEASURE'](0);SET.select_seam(ctx,'U1');DT._menu_select(ctx,('measure','pattern:M0'))
        equal(shown(),'MEASURE')
    check('right_click_reactivation_keeps_sets',menus)
    def points():
        reset();DT.select_corner(ctx,'A',edges[0]);equal(shown(),'EDGES')
        SET.select_seam(ctx,'U1');DT.select_corner(ctx,'A',edges[0]);equal(shown(),'EDGES')
        SET.select_seam(ctx,'U1');DT.select_ipoint(ctx,'A',lines[0],0);equal(shown(),'EDGES')
    check('corner_and_internal_point_reclick',points)
    def menu_elements():
        for hit,kind in [(('edge','A',edges[1]),'EDGES'),(('corner','A',0),'EDGES'),
                         (('iline','A',lines[0]),'INTERNAL'),(('iend','A',lines[0],'start'),'EDGES')]:
            reset();SET.select_seam(ctx,'U1');DT._menu_select(ctx,hit);equal(shown(),kind)
    check('right_click_each_drafting_kind',menu_elements)
    def explicit_sets():
        reset();SET.select_seam(ctx,'U1');bpy.ops.ac9_cloth_sim.invert_piece_selection();equal(shown(),'PIECE')
        SET.select_seam(ctx,'U1');bpy.ops.ac9_cloth_sim.select_garment(garment='');equal(shown(),'PIECE')
        SET.select_seam(ctx,'U1')
        import numpy as np
        with patch.object(DT,'_in_rect',return_value=lambda q:np.ones(len(q),bool)):
            DT.box_transform(ctx,(0,0,100,100),lines=False)
        equal(shown(),'PIECE')
    check('explicit_invert_garment_and_box',explicit_sets)
    def list_multiselect():
        reset();SET.select_seam(ctx,'U1');SET.select_seam(ctx,'U2',extend=True)
        ST.sync_seam_list(ctx,current())
        equal({s['id'] for s in ST.selected_seams(ctx,current())},{'U1','U2'})
        reset();SET.select_seam(ctx,'U1');TB.select_piece(ctx,'A')
        bpy.ops.ac9_cloth_sim.seam_list_pick(seam='U1');equal(shown(),'SEAM')
        bpy.ops.ac9_cloth_sim.seam_list_pick(seam='U1');equal(ST.active_seam(ctx,current()),None)
    check('list_multiselect_and_contextual_reclick',list_multiselect)
    def old_native():
        reset()
        for ob in SEL.objects(ctx):ob.select_set(False)
        SET.select_seam(ctx,'U1')
        with patch.object(SEL,'_native',return_value=(True,True)),patch.object(SEL,'_ac9_tool',return_value=False):
            SEL.watch(ctx.scene,None)
        equal(shown(),'SEAM');equal(ST.active_seam(ctx,current())['id'],'U1')
    check('acknowledged_native_cannot_clear_new_focus',old_native)
    def native_order():
        for ac9_tool in (False,True):
            results=[]
            for delayed in (False,True):
                reset()
                for ob in SEL.objects(ctx):ob.select_set(False)
                ob=PV.piece_objects()['B'];ob.select_set(True);ctx.view_layer.objects.active=ob
                with patch.object(SEL,'_native',return_value=(True,True)),patch.object(SEL,'_ac9_tool',return_value=ac9_tool):
                    if not delayed:SEL.watch(ctx.scene,None)
                    SET.select_seam(ctx,'U1')
                    if delayed:SEL.watch(ctx.scene,None)
                results.append((shown(),SEL.ids(ctx),[s['id'] for s in ST.selected_seams(ctx,current())]))
            equal(results[0],results[1]);equal(results[0],('SEAM',['A'] if ac9_tool else [],['U1']))
    check('native_notification_order_preserves_selection_contract',native_order)
    def menu_groups():
        for kind in ('edge','corner','ipoint','iline'):
            reset()
            if kind=='edge':
                DT.select_edge(ctx,'A',edges[0]);DT.select_edge(ctx,'A',edges[1],extend=True)
                hit=('edge','A',edges[0])
            elif kind=='corner':
                DT.select_corner(ctx,'A',edges[0]);DT.select_corner(ctx,'A',edges[1],extend=True)
                hit=('corner','A',0)
            elif kind=='ipoint':
                DT.select_ipoint(ctx,'A',lines[0],0);DT.select_ipoint(ctx,'A',lines[0],1,extend=True)
                hit=('iend','A',lines[0],'start')
            else:
                DT.select_internal(ctx,'A',lines[0]);DT.select_internal(ctx,'A',lines[1],extend=True)
                hit=('iline','A',lines[0])
            picked=set(DT.picked_lines(current()))
            SET.select_seam(ctx,'U1');DT._menu_select(ctx,hit)
            equal(set(DT.picked_lines(current())),picked)
            equal(shown(),'INTERNAL' if kind=='iline' else 'EDGES')
    check('right_click_drafting_groups_preserved',menu_groups)
    def look_fields():
        reset()
        LA=importlib.import_module(MOD+'.look_apply')
        LK=importlib.import_module(MOD+'.core.look')
        mats=[bpy.data.materials.new('Look'+n) for n in ('A','B')]
        altered=S.loads(base)
        for n,mat in zip(('A','B'),mats):LK.set_pieces(altered,[n],LA.ref(mat))
        write(altered)
        TB.select_piece(ctx,'A');equal(ctx.scene.ac9_cloth_sim.piece_look,mats[0])
        bpy.ops.ac9_cloth_sim.select_piece(piece='B');equal(ctx.scene.ac9_cloth_sim.piece_look,mats[1])
        SET.select_seam(ctx,'U1');SEL.apply(ctx,['A'],active='A')
        equal(shown(),'SEAM');equal(ctx.scene.ac9_cloth_sim.piece_look,mats[0])
    check('piece_look_RNA_matches_final_lead',look_fields)
    def other_object():
        reset();SET.select_seam(ctx,'U1')
        ob=bpy.data.objects.new('Other',None);ctx.scene.collection.objects.link(ob);ctx.view_layer.update()
        TB.select_object(ctx,ob);equal(shown(),'GARMENT')
    check('explicit_other_object_drops_piece_details',other_object)
    def owner():
        reset();SET.select_seam(ctx,'U1');original=ST.text(ctx)
        other=ST.new_text(ctx,'Other');other.write(base);ST.invalidate()
        assert shown() != 'SEAM'
        ctx.scene.ac9_cloth_sim.spec_text=original;ST.invalidate()
        equal(shown(),'SEAM')
        TT._on_load(None);assert shown() != 'SEAM'
    check('spec_owner_and_load',owner)
    def scene_owner():
        reset();SET.select_seam(ctx,'U1')
        scene=ctx.scene;other=bpy.data.scenes.new('OtherScene')
        other.ac9_cloth_sim.spec_text=ST.text(ctx)
        try:
            ctx.window.scene=other
            assert shown()!='SEAM'
            SEL.watch(ctx.scene,None)
        finally:ctx.window.scene=scene
        equal(shown(),'SEAM')
    check('scene_owner_with_same_spec_text',scene_owner)
    def history():
        reset();SET.select_seam(ctx,'U1');TT._on_history(None)
        equal(shown(),'SEAM')
        SEL._cancel_pending();SEL.watch(ctx.scene,None)
        equal(shown(),'SEAM')
    check('history_restore_keeps_valid_focus',history)
    def body_measure():
        reset();original=ST.text(ctx)
        bodies=[]
        for i in range(2):
            body=bpy.data.objects.new('MeasureBody'+str(i),None);ctx.scene.collection.objects.link(body)
            body['ac9_measure']=json.dumps({'items':{'bust':{'kind':'girth','value':900+i*100}},'tapes':[]})
            bodies.append(body)
        try:
            ctx.scene.ac9_cloth_sim.body=bodies[0]
            ctx.scene.ac9_cloth_sim.spec_text=None;ST.invalidate()
            MEE.select(ctx,'item:bust');equal(shown(),'MEASURE')
            ctx.scene.ac9_cloth_sim.body=bodies[1];assert shown() != 'MEASURE'
            MEE.select(ctx,'item:bust');equal(shown(),'MEASURE')
        finally:
            ctx.scene.ac9_cloth_sim.spec_text=original;ST.invalidate()
    check('body_measure_without_spec_and_body_owner',body_measure)
    reset()
    R['complete']=True
except Exception:
    R['fatal']=traceback.format_exc()
R['passed']=sum(c['passed'] for c in R['cases'])
R['failed']=len(R['cases'])-R['passed']
Path(ARGS[1]).write_text(json.dumps(R,ensure_ascii=False,indent=2),'utf-8')
if R.get('fatal') or R['failed']:
    raise RuntimeError('Detail focus contracts failed; read output JSON')
