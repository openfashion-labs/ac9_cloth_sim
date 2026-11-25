# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic six-piece selection fixture. No cloth solver or user data."""
import sys, os, importlib, json
from pathlib import Path
import bpy
import numpy as np
from mathutils import Matrix

BASE=Path(__file__).resolve().parent
sys.path.insert(0,sys.argv[sys.argv.index('--')+1])
PKG=os.environ.get('FOCUS_PKG','ac9_cloth_sim')
addon=importlib.import_module(PKG)
if PKG.startswith('bl_ext.'):
    import addon_utils
    addon_utils.enable(PKG,default_set=False,persistent=False)
else:addon.register()
SEL,ST,TT,DT,TB,SET,PV,PLV,PM,D,BX,BU,MEE,OP=[importlib.import_module(PKG+'.'+n) for n in
    ('selection','store','tool_tab','draft_tool','toolbar','seam_edit_tool','pattern_view','placed_view','piece_move',
     'dress','box_pieces','button_ui','measure_edit','operators')]
S,DR,FA,BT=[importlib.import_module(PKG+'.core.'+n) for n in ('spec','draft','fabric','buttons')]
ctx=bpy.context
NAMES=('A','B','C','D','E','F')

class View:
    region=type('Region',(),dict(width=1200,height=1000,type='WINDOW'))()
    region_data=type('RV',(),dict(perspective_matrix=Matrix(((2/1.2,0,0,-1),(0,2,0,-1),(0,0,-1,0),(0,0,0,1)))))()
    space_data=None
    def __getattr__(self,key):return getattr(bpy.context,key)

def build(route='pattern',wide=False):
    sp=S.new_spec('BoxReview')
    for i,n in enumerate(NAMES):
        x=50+200*i
        q=[[x,50],[x+100,50],[x+100,150],[x,150]]
        if wide and n in ('D','E'):
            x+=200;q=[[x,50],[x+100,50],[x+100,150],[x,150]]
        if wide and n=='F':q=[[650,0],[800,0],[800,400],[650,400]]
        DR.add_polygon(sp,q,name=n)
        if wide and n=='F':DR.add_internal(sp,n,[[690,80],[720,80]],mirror=False)
    fids=[FA.add_custom(sp,'Fabric '+str(i)) for i in range(2)]
    bond=FA.add_custom_interlining(sp,'Bond')
    FA.set_pieces(sp,['A'],fabric_id=fids[0]);FA.set_pieces(sp,['B'],fabric_id=fids[1])
    FA.set_pieces(sp,['C'],bond=bond,what='bond')
    lid=DR.add_internal(sp,'F',[[1060,80],[1090,80]] if not wide else [[700,250],[730,250]],mirror=False)
    def side(n):
        e=S.piece_index(sp)[n]['edges'][0]['id']
        return dict(piece=n,**{'from':[e,0.0],'to':[e,1.0]},dir=1)
    sp['seams']=[dict(id='U1',a=side('A'),b=side('B'),source='user',check='')]
    button=BT.add(sp,'A',[75,100],ctx.scene.ac9_button_style)['id']
    sp['measure']={'pattern':[dict(name='M',a={'piece':'A','edge':side('A')['from'][0],'t':0.},
                                  b={'piece':'A','edge':side('A')['from'][0],'t':1.})]}
    assert S.check(sp)==[]
    ST.new_text(ctx,sp['name']).write(S.dumps(sp));ST.invalidate()
    spec,ol=ST.get(ctx)
    PV.rebuild(ctx,spec,ol);PV.layout_empty(ctx).matrix_world=Matrix.Identity(4)
    ST.sync_seam_list(ctx,spec)
    pats=PV.piece_objects()
    ctx.view_layer.update()
    assert len(pats)==6 and all(ob.users_collection for ob in pats.values())
    assert all(len(ob.data.polygons)==1 and ob.data.polygons[0].area>0 for ob in pats.values())
    assert all(S.side_length(spec,sm[k],ol)>0 for sm in sp['seams'] for k in ('a','b'))
    if route!='pattern':PV.layout_empty(ctx).location.x=10.
    placed_names=NAMES if route=='placed' else NAMES[:3] if route=='mixed' else ()
    worn_names=NAMES if route=='worn' else NAMES[3:] if route=='mixed' else ()
    for n in placed_names:
        pc=S.piece_index(spec)[n];B=S.Outline(pc).B
        me=bpy.data.meshes.new('Placed_'+n);me.from_pydata([(float(x)*.001,float(y)*.001,.3) for x,y in B],[],[(0,1,2,3)])
        ob=bpy.data.objects.new('Placed_'+n,me);PLV._collection(ctx).objects.link(ob)
        ob[PLV.PROP]=n;ob[PLV.GARMENT_PROP]=spec['name'];ob[PLV.ORG_PROP]=[0,0,0]
    if worn_names:
        verts=[];faces=[]
        for n in worn_names:
            B=S.Outline(S.piece_index(spec)[n]).B
            k=len(verts);verts.extend((float(x)*.001,float(y)*.001,.3) for x,y in B);faces.append(tuple(range(k,k+4)))
        me=bpy.data.meshes.new('Worn');me.from_pydata(verts,[],faces)
        g=bpy.data.objects.new(D.garment_name(spec),me);ctx.scene.collection.objects.link(g)
        g['ac9_piece_names']=json.dumps(list(worn_names));g[D.JOB_MISSING_PROP]=True
        me.attributes.new(name='ac9_piece',type='INT',domain='FACE').data.foreach_set('value',list(range(len(worn_names))))
    foreign=bpy.data.objects.new('Other',None);ctx.scene.collection.objects.link(foreign)
    ctx.view_layer.update()
    return dict(spec=spec,route=route,wide=wide,fids=fids,bond=bond,lid=lid,button=button,foreign=foreign,
                fixture=dict(status='ok',pieces=6,spec_problems=[],positive_polygon_areas=6,seam_positive_sides=2,
                             placed=len(placed_names),worn=len(worn_names),start='factory synthetic polygons; UI selection only'))

def seed(data,mode,active,prior):
    SEL._cancel_pending()
    DT.select_internal(ctx);DT.select_edge(ctx);DT.select_corner(ctx);DT.select_ipoint(ctx)
    MEE.select(ctx,'');SET.unselect_seam(ctx);SEL.deselect_all(ctx)
    base=list(NAMES) if mode=='SUB' else ['F'] if mode=='ADD' else []
    SEL.apply(ctx,base,active=base[0] if base else None)
    pats=PV.piece_objects()
    ctx.view_layer.objects.active={'none':None,'inside':pats['A'],'outside':pats['F'],'foreign':data['foreign']}[active]
    SEL._remember(ctx)
    if prior=='SEAM':SET.select_seam(ctx,'U1')
    elif prior=='FABRIC':TT.focus_item(ctx,prior,data['fids'][0])
    elif prior=='BOND':TT.focus_item(ctx,prior,data['bond'])
    elif prior=='BUTTON':bpy.ops.ac9_cloth_sim.button_select(button=data['button'])
    elif prior=='MEASURE':MEE.select(ctx,'pattern:M')
    elif prior=='INTERNAL':DT.select_internal(ctx,'F',data['lid'])
    elif prior=='EDGES':DT.select_corner(ctx,'F',S.piece_index(data['spec'])['F']['edges'][0]['id'])
    else:TT.piece_clicked(ctx)
    ctx.view_layer.update()
    return base

def snapshot():
    return dict(selected=SEL.ids(ctx),pattern_selected=SEL.pattern_pieces(ctx),lead=SEL.lead(ctx),
                panel_piece=TT._selected_piece(ctx,ST.get(ctx)[0]),editing_piece=OP._active_piece(ctx,OP._sel_pieces(ctx)),
                details=TT.snapshot(ctx),active=ctx.view_layer.objects.active.name if ctx.view_layer.objects.active else None,
                drafting=[list(x) for x in DT.picked_lines(ST.get(ctx)[0])])
