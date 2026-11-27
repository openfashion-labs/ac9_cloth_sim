"""Native enum input -> shared-fold write -> native undo/redo -> private save/reopen.

Only event_simulate inside this isolated Blender window; no operating-system input.
The synthetic finite rectangles isolate data ownership, not physical fold quality.
"""
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
ARGS = sys.argv[sys.argv.index('--')+1:]
OUT = Path(ARGS[1]); OUT.parent.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, ARGS[0])
import bpy
import numpy as np
import ac9_cloth_sim as A
from mathutils import Matrix, Quaternion
from ac9_cloth_sim import store as ST, draft_tool as DT, pattern_view as PAT, placed_view as PV, place_fold as F
from ac9_cloth_sim.core import draft as D, spec as S

A.register()
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.edit.use_global_undo = True
bpy.context.preferences.filepaths.temporary_directory = str(OUT.parent)
R = dict(ok=False, events=[], source_hashes={})
for name in ('placed_view.py', 'place_fold.py', 'dress.py', 'place_seam.py'):
    R['source_hashes'][name] = hashlib.sha256((Path(PV.__file__).parent/name).read_bytes()).hexdigest()
phase = 0; started = time.monotonic()


def snap():
    objects = [bpy.data.objects[name] for name in ('NativeA', 'NativeB', 'NativeOutside')]
    return dict(co=np.array([[tuple(v.co) for v in ob.data.vertices] for ob in objects]).tolist(),
                mesh_ids=[int(ob.data.session_uid) for ob in objects],
                history=[bool(ob.data.attributes.get(F.BEFORE_ATTR+'0')) for ob in objects],
                spec=ST.text(bpy.context).as_string())


def current_view():
    win = bpy.context.window
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def tick():
    global phase, before, after, line, case_path
    try:
        assert time.monotonic()-started < 100, 'native shared fold test timed out'
        win, area, region = current_view()
        with bpy.context.temp_override(window=win, area=area, region=region):
            if phase == 0:
                for kind, env in [('CONFIG', 'BLENDER_USER_CONFIG'), ('EXTENSIONS', 'BLENDER_USER_EXTENSIONS')]:
                    assert Path(bpy.utils.user_resource(kind)).resolve() == Path(os.environ[env]).resolve()
                for ob in list(bpy.data.objects):
                    bpy.data.objects.remove(ob, do_unlink=True)
                sp = S.new_spec('NativeSharedFold')
                V = np.array([[0.,0.],[100.,0.],[100.,100.],[0.,100.]])
                T = np.array([[0,1,2],[0,2,3]])
                for name in ('A', 'B'):
                    D.add_polygon(sp, V.tolist(), name=name)
                    sp.setdefault('placement', {})[name] = dict(guide=S.FLAT,
                        flat=dict(o=[0,0,0],R=np.eye(3).tolist()),anchor=[50,50])
                line = D.add_internal(sp, 'A', [[0,50],[100,50]], mirror=False)
                assert D.problems(sp,['A','B']) == []
                ST.new_text(bpy.context, sp['name']); ST.write(bpy.context,sp,keep=True)
                PAT.rebuild(bpy.context,*ST.get(bpy.context)); PAT.require_integrity(bpy.context,sp)
                mesh = PV._mesh('NativeSharedInput',V,T,np.c_[V,np.zeros(4)],np.zeros(3))
                obs = []
                for i, name in enumerate(('A','B','Outside')):
                    ob = bpy.data.objects.new('Native'+name,mesh); PV._collection(bpy.context).objects.link(ob)
                    if name != 'Outside':
                        PV._tag(ob,sp,name,V)
                    ob.matrix_world = Matrix.Translation((i*.16,0,0))
                    ob.color = ((.2,.45,1.,1.) if i == 0 else (.25,.7,.4,1.))
                    obs.append(ob)
                F.write_angle(bpy.context,'A',line,45.,'A')
                co = np.array([tuple(v.co) for v in mesh.vertices],np.float32)
                mesh.attributes.new(F.BEFORE_ATTR+'0','FLOAT_VECTOR','POINT').data.foreach_set('vector',co.ravel())
                co[0,2] = .025; mesh.vertices.foreach_set('co',co.ravel())
                mesh.attributes[PV.GEN_ATTR].data.foreach_set('vector',PV.generated_co(obs[0],co).astype(np.float32).ravel())
                mesh.update(); obs[0][F.FOLD_PROP] = json.dumps([dict(line=line,side=0,deg=45.)])
                assert not PV.has_edits(obs[0])
                DT.select_internal(bpy.context,'A',line)
                PAT._collection(bpy.context).hide_viewport = True
                space = area.spaces.active
                space.shading.type = 'SOLID'; space.shading.color_type = 'OBJECT'
                space.overlay.show_floor = False
                space.region_3d.view_rotation = Quaternion((.9238795,.3826834,0,0))
                space.region_3d.view_perspective = 'ORTHO'; space.region_3d.view_distance = .65
                space.region_3d.view_location = (.21,.05,0)
                km = bpy.context.window_manager.keyconfigs.addon.keymaps.new(name='3D View',space_type='VIEW_3D')
                key = km.keymap_items.new('wm.context_set_enum',type='F8',value='PRESS')
                key.properties.data_path = 'scene.ac9_iline_fold_side'; key.properties.value = 'NONE'
                bpy.context.view_layer.update()
                assert bpy.context.scene.ac9_iline_fold_side == 'A'
                bpy.ops.ed.undo_push(message='native shared fold input')
                before = snap(); R['before'] = before
                phase = 10
            elif phase == 10:
                win.event_simulate(type='ESC',value='PRESS')
                win.event_simulate(type='ESC',value='RELEASE')
                phase = 15
            elif phase == 15:
                bpy.ops.screen.screenshot(filepath=str(OUT.parent/'shared_native_before.png'))
                win.event_simulate(type='MOUSEMOVE',value='NOTHING',x=region.x+region.width//2,y=region.y+region.height//2)
                win.event_simulate(type='F8',value='PRESS',x=region.x+region.width//2,y=region.y+region.height//2)
                win.event_simulate(type='F8',value='RELEASE',x=region.x+region.width//2,y=region.y+region.height//2)
                R['events'].append('native context enum NONE'); phase = 11
            elif phase == 11:
                if bpy.context.scene.ac9_iline_fold_side != 'NONE':
                    return .2
                after = snap(); R['after'] = after
                bpy.ops.screen.screenshot(filepath=str(OUT.parent/'shared_native_after.png'))
                np.testing.assert_array_equal(np.array(after['co'])[1:],np.array(before['co'])[1:])
                assert abs(after['co'][0][0][2]) < 1e-8
                assert after['history'] == [False,True,True]
                assert after['mesh_ids'][0] != after['mesh_ids'][1] == after['mesh_ids'][2]
                bpy.ops.ed.undo('INVOKE_DEFAULT'); phase = 20
            elif phase == 20:
                undone = snap(); R['undo'] = undone
                np.testing.assert_array_equal(undone['co'],before['co'])
                assert len(set(undone['mesh_ids'])) == 1
                assert undone['spec'] == before['spec']
                bpy.ops.ed.redo('INVOKE_DEFAULT'); phase = 30
            elif phase == 30:
                redone = snap(); R['redo'] = redone
                np.testing.assert_array_equal(redone['co'],after['co'])
                assert redone['history'] == after['history']
                bpy.ops.screen.screenshot(filepath=str(OUT.parent/'shared_native_after.png'))
                case_path = str(OUT.parent/'native_shared.blend')
                bpy.context.preferences.filepaths.save_version = 0
                bpy.context.preferences.filepaths.file_preview_type = 'NONE'
                bpy.ops.wm.save_as_mainfile(filepath=case_path)
                bpy.ops.wm.open_mainfile(filepath=case_path); phase = 40
            elif phase == 40:
                loaded = snap(); R['reopened'] = loaded
                np.testing.assert_array_equal(loaded['co'],after['co'])
                assert loaded['history'] == after['history']
                assert loaded['mesh_ids'][0] != loaded['mesh_ids'][1] == loaded['mesh_ids'][2]
                assert not Path(case_path+'1').exists()
                R['ok'] = True
                OUT.write_text(json.dumps(R,indent=2),encoding='utf-8')
                bpy.ops.wm.quit_blender(); return None
        R['phase'] = phase
        OUT.write_text(json.dumps(R,indent=2),encoding='utf-8')
        return .3
    except Exception:
        R['error'] = traceback.format_exc(); R['phase'] = phase
        OUT.write_text(json.dumps(R,indent=2),encoding='utf-8')
        bpy.ops.wm.quit_blender(); return None


bpy.app.timers.register(tick,first_interval=.7,persistent=True)
