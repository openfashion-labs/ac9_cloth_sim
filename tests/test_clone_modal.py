# SPDX-License-Identifier: GPL-3.0-or-later
"""Clone (+Sew)'s native target-selection modal and its two automatic undo steps."""
import sys,json,time,traceback
from pathlib import Path
ARGS=sys.argv[sys.argv.index('--')+1:];OUT=Path(ARGS[1]);OUT.parent.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,ARGS[0]);sys.path.insert(0,str(Path(__file__).resolve().parent))
import bpy,ac9_cloth_sim as A,numpy as np
from mathutils import Quaternion
from ac9_cloth_sim import draft_tool as T,store,pattern_view as PV,place_targets as PT,place_seam as PS,selection as SEL
from symmetry_scene import setup,snapshot
A.register();bpy.context.preferences.use_preferences_save=False;bpy.context.preferences.edit.use_global_undo=True
bpy.context.preferences.filepaths.temporary_directory=str(OUT.parent)
R={'ok':False,'phase':0};phase=0;start=time.monotonic()


def tick():
 global phase,win,area,region,before,cloned,new,n,l,placed
 try:
    assert time.monotonic()-start<120,'clone target modal timed out'
    win=bpy.context.window
    if phase==0:
        area=next(a for a in win.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(window=win,area=area,region=region):
            sp,n,l,_,_=setup(OUT.parent/'clone_modal_results','clone_sew')
            SEL.apply(bpy.context,[n],active=n,view='pattern')
            space=area.spaces.active;space.region_3d.view_rotation=Quaternion((1,0,0,0))
            space.region_3d.view_perspective='ORTHO';space.region_3d.view_distance=2.
            space.region_3d.view_location=PV.to_world(PV.layout_matrix(bpy.context),[[450,250]])[0]
            bpy.ops.ed.undo_push(message='before Clone (+Sew)')
            before=snapshot()
            assert bpy.ops.ac9_cloth_sim.clone_shape('EXEC_DEFAULT',True,piece=n,line=l,sew=True)=={'RUNNING_MODAL'}
            assert snapshot()==before,'copy preview changed the original'
        phase=10
    elif phase==10:
        win.event_simulate(type='MOUSEMOVE',value='NOTHING',x=region.x+300,y=region.y+300)
        win.event_simulate(type='LEFTMOUSE',value='PRESS',x=region.x+300,y=region.y+300)
        win.event_simulate(type='LEFTMOUSE',value='RELEASE',x=region.x+300,y=region.y+300)
        phase=11
    elif phase==11:
        with bpy.context.temp_override(window=win,area=area,region=region):
            cloned=snapshot();new=sorted({p['name'] for p in cloned['spec']['pieces']}-{p['name'] for p in before['spec']['pieces']})
            assert len(new)==4
            assert PT._active is not None,'Part to Seam did not open'
            state=PT._active._pick_state
            R['movers']=state['movers'];R['candidates']=state['candidates']
            assert set(state['movers'])==set(new)
            assert state['candidates']==['Panel','Panel_R']
        phase=1
    elif phase==1:
        win.event_simulate(type='A',value='PRESS');win.event_simulate(type='A',value='RELEASE');phase=2
    elif phase==2:
        R['selected_targets']=sorted(PT._active._pick_state['targets'])
        assert R['selected_targets']==['Panel','Panel_R']
        win.event_simulate(type='RET',value='PRESS');win.event_simulate(type='RET',value='RELEASE');phase=3
    elif phase==3:
        with bpy.context.temp_override(window=win,area=area,region=region):
            assert PT._active is None,'target modal still running'
            placed=snapshot();assert set(new).issubset(placed['placed'])
            got=PS.fresh_shapes(bpy.context);pat,_,xyz=got;names=list(map(str,pat['names']));st=np.asarray(pat['stitch']).reshape(-1,4)
            gaps={}
            for nm in new:
                k=names.index(nm);pairs=st[(st[:,0]==k)|(st[:,2]==k)];assert len(pairs)
                gaps[nm]=max(float(np.linalg.norm(xyz[int(a)][int(ai)]-xyz[int(b)][int(bi)])) for a,ai,b,bi in pairs)
            R['max_sewn_gap_mm']=gaps;assert max(gaps.values())<.001,gaps
            assert bpy.ops.ed.undo()=={'FINISHED'};R['undo_placement_equal']=snapshot()==cloned;assert R['undo_placement_equal']
            assert bpy.ops.ed.undo()=={'FINISHED'};R['undo_clone_equal']=snapshot()==before;assert R['undo_clone_equal']
            assert bpy.ops.ed.redo()=={'FINISHED'};R['redo_clone_equal']=snapshot()==cloned;assert R['redo_clone_equal']
            assert bpy.ops.ed.redo()=={'FINISHED'};R['redo_placement_equal']=snapshot()==placed;assert R['redo_placement_equal']
        R['ok']=True;OUT.write_text(json.dumps(R,indent=2),encoding='utf-8');bpy.ops.wm.quit_blender();return None
    R['phase']=phase;OUT.write_text(json.dumps(R,indent=2),encoding='utf-8')
 except Exception:
    R['error']=traceback.format_exc();OUT.write_text(json.dumps(R,indent=2),encoding='utf-8');bpy.ops.wm.quit_blender();return None
 return .7
bpy.app.timers.register(tick,first_interval=2.)
