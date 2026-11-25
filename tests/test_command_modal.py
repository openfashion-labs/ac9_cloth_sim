# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Move Dart modal -> click -> automatic undo step, on a nonplanar carried result.
Run in an isolated GUI: blender --factory-startup --enable-event-simulate --python this.py -- <parent> <out.json>.
The timer operates only this Blender window. It never sends operating-system input.
"""
import sys,os,json,traceback,time
from pathlib import Path
ARGS=sys.argv[sys.argv.index('--')+1:];OUT=Path(ARGS[1]);OUT.parent.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,ARGS[0]);sys.path.insert(0,str(Path(__file__).resolve().parent))
import bpy,numpy as np,ac9_cloth_sim as A
from mathutils import Vector,Quaternion
from bpy_extras.view3d_utils import location_3d_to_region_2d
from ac9_cloth_sim import draft_tool as T,dart_ui as DU,store,pattern_view as PV,dress as DR,selection as SEL
from ac9_cloth_sim.core import spec as S,draft as D,dart as DA,transfer as TR
from symmetry_scene import setup,snapshot
A.register();bpy.context.preferences.use_preferences_save=False
bpy.context.preferences.filepaths.temporary_directory=str(OUT.parent)
bpy.context.preferences.edit.use_global_undo=True
R={'ok':False,'phase':0,'events':[]};phase=0;captured={};start=time.monotonic()
native=DA.move_dart


def move_spy(*a,**k):
    out=native(*a,**k);captured['maps']=out[3];return out
DA.move_dart=move_spy


def write():OUT.write_text(json.dumps(R,indent=2),encoding='utf-8')


def tick():
 global phase,area,region,win,before,after,old,n,l,initial,hitxy,target
 try:
    assert time.monotonic()-start<120,'modal test timed out'
    win=bpy.context.window
    if phase==0:
        for kind,env in [('CONFIG','BLENDER_USER_CONFIG'),('SCRIPTS','BLENDER_USER_SCRIPTS'),('EXTENSIONS','BLENDER_USER_EXTENSIONS')]:
            if env in os.environ:assert Path(bpy.utils.user_resource(kind)).resolve()==Path(os.environ[env]).resolve()
        area=next(a for a in win.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(window=win,area=area,region=region):
            old,n,l,_,initial=setup(OUT.parent/'modal_results','dart')
            SEL.apply(bpy.context,[n],active=n,view='pattern')
            DU._select_seam(bpy.context,l)
            bpy.ops.wm.tool_set_by_id(name=T.TOOL_ID)
            space=area.spaces.active;space.region_3d.view_rotation=Quaternion((1,0,0,0))
            space.region_3d.view_perspective='ORTHO';space.region_3d.view_distance=1.1
            space.region_3d.view_location=PV.to_world(PV.layout_matrix(bpy.context),[[200,250]])[0]
            space.region_3d.lock_rotation=True
        phase=1
    elif phase==1:
        with bpy.context.temp_override(window=win,area=area,region=region):
            target=S.Outline(D.piece(old,n)).at(S.Outline(D.piece(old,n)).s_of('E3',.25))
            xy=location_3d_to_region_2d(region,area.spaces.active.region_3d,PV.to_world(PV.layout_matrix(bpy.context),[target])[0])
            assert xy is not None
            hitxy=(int(xy.x),int(xy.y))
            hit=T.pick(bpy.context,*store.get(bpy.context),PV.layout_matrix(bpy.context),*hitxy)
            assert hit and hit[:3]==('edge',n,'E3'),hit
            R['target_hit']=list(hit)
            bpy.ops.ed.undo_push(message='before real Move Dart modal')
            before=snapshot()
            bpy.ops.screen.screenshot(filepath=str(OUT.parent/'dart_before.png'))
            result=bpy.ops.ac9_cloth_sim.move_dart('INVOKE_DEFAULT',True,mode='pivot',side='auto',true_mouth=True)
            assert result=={'RUNNING_MODAL'},result
            R['invoke']=list(result)
        phase=2
    elif phase==2:
        x,y=hitxy;x+=region.x;y+=region.y
        win.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
        phase=3
    elif phase==3:
        x,y=hitxy;x+=region.x;y+=region.y
        win.event_simulate(type='LEFTMOUSE',value='PRESS',x=x,y=y)
        win.event_simulate(type='LEFTMOUSE',value='RELEASE',x=x,y=y)
        phase=4
    elif phase==4:
        with bpy.context.temp_override(window=win,area=area,region=region):
            after=snapshot();assert after['spec']!=before['spec'],'modal click did not apply'
            sp=store.get(bpy.context)[0];assert not D.problems(sp,[p['name'] for p in sp['pieces']])
            from symmetry_fixture import outline_error
            R['mirror_error_mm']=outline_error(sp,n);assert R['mirror_error_mm']<.002
            g=DR.garment(sp);last=T._last_result(DR.job_of(g),DR.shown_shape(bpy.context,g))
            pat,idx,X,_=last;op,ot,oi,OX=initial
            oldnames=list(map(str,op['names']));names=list(map(str,pat['names']))
            oo=list(oi['offs'])+[len(OX)];offs=list(idx['offs'])+[len(X)]
            errors={}
            for i,nm in enumerate(names):
                j=oldnames.index(nm);V=pat[f'V{i}']
                Q=captured['maps'][nm](V) if nm in captured['maps'] else V
                expected=TR.bind(Q,op[f'V{j}'],ot[j],OX[oo[j]:oo[j+1]])
                errors[nm]=float(np.linalg.norm(X[offs[i]:offs[i+1]]-expected,axis=1).max())
            R['material_error_mm']=errors;assert max(errors.values())<.001,errors
            R['status']=bpy.context.scene.ac9_cloth_sim.dress_status
            bpy.ops.screen.screenshot(filepath=str(OUT.parent/'dart_after.png'))
            assert bpy.ops.ed.undo()=={'FINISHED'}
            undone=snapshot();R['undo_equal']=undone==before
            if not R['undo_equal']:(OUT.parent/'modal_undo_diff.json').write_text(json.dumps({'expected':before,'actual':undone}),encoding='utf-8')
            assert R['undo_equal'],'Undo mismatch'
            assert bpy.ops.ed.redo()=={'FINISHED'}
            R['redo_equal']=snapshot()==after;assert R['redo_equal'],'Redo mismatch'
        R['ok']=True;write();bpy.ops.wm.quit_blender();return None
    R['phase']=phase;write()
 except Exception:
    R['error']=traceback.format_exc();write();bpy.ops.wm.quit_blender();return None
 return .6

bpy.app.timers.register(tick,first_interval=2.)
