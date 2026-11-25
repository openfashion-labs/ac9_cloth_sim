# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated GUI evidence. Timed captures, actual GPU masks and F3 lifecycle."""
import importlib
import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
import bpy
import gpu
import numpy as np
from mathutils import Quaternion, Vector

sys.path.insert(0, str(Path(__file__).parent))
from silhouette_fixture import enable, setup, preflight, PACKAGE
OUT = Path(sys.argv[sys.argv.index('--') + 1])
OUT.mkdir(parents=True, exist_ok=True)
enable()
bpy.context.preferences.view.show_splash = False
SI = importlib.import_module(PACKAGE + '.silhouette')
PV = importlib.import_module(PACKAGE + '.pattern_view')
U = importlib.import_module(PACKAGE + '.ui')
body, accessory = setup()
bpy.context.scene.ac9_cloth_sim.body = None
bpy.data.objects.remove(body, do_unlink=True)
bpy.ops.ac9_cloth_sim.add_avatar()
p = bpy.context.scene.ac9_cloth_sim
body = p.body
assert body is not None
preflight((body,), OUT / 'avatar_fixture.json', 'bundled CC0 avatar; appended; current evaluated display; no simulation')
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
sp = area.spaces.active
sp.show_region_ui = False
sp.shading.type = 'SOLID'
sp.overlay.show_floor = True
sp.overlay.show_axis_x = False
sp.overlay.show_axis_y = False
sp.region_3d.view_perspective = 'ORTHO'
sp.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
sp.region_3d.view_distance = 4.2
p.silhouette_position = (-.05, -.95)
p.silhouette_positioned = True
p.show_names = False
p.show_guides = False
p.show_silhouette_avatar = True
p.show_silhouette_collisions = True
bpy.context.view_layer.update()
sp.region_3d.view_location = PV.layout_matrix(bpy.context) @ Vector((0, -.05, 0))
# Hide physical sources in this viewport with local collections, as the real work screen does.
VS = importlib.import_module(PACKAGE + '.view_setup')
root = bpy.context.scene.collection
physical = bpy.data.collections.new('DisplayWorld')
root.children.link(physical)
for ob in list(root.objects):
    if ob != PV.layout_empty(bpy.context):
        physical.objects.link(ob)
        root.objects.unlink(ob)
sp.use_local_collections = True
from types import SimpleNamespace
# Use the product's actual view-domain setup for the sole pattern view.
VS.configure(bpy.context, bpy.context.window, area, area)
region = next(r for r in area.regions if r.type == 'WINDOW')
results = {'blender': bpy.app.version_string, 'source': str(Path(importlib.import_module(PACKAGE).__file__))}
phase = 0
old_position = None
expected_position = None

def save():
    (OUT / 'gui.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

def capture(name):
    bpy.ops.screen.screenshot(filepath=str(OUT / name))

def run():
    global phase, SI, U, old_position, expected_position
    try:
        with bpy.context.temp_override(area=area, region=region):
            p = bpy.context.scene.ac9_cloth_sim
            assert SI.pattern_view(bpy.context), 'not pattern view'
            assert SI.stats['error'] is None, SI.stats
            if phase == 0:
                data = SI.snapshot(bpy.context)
                masks = SI.masks(bpy.context, data)
                assert len(masks) == 3
                alpha = {}
                for direction, (off, bounds) in masks.items():
                    a = np.asarray(off.texture_color.read()).reshape(off.height, off.width, 4)[:, :, 0]
                    unique = np.unique(a).tolist()
                    if not (len(unique) == 2 and a.max() > 0 and np.count_nonzero(a) < a.size * .9):
                        results['mask_diagnostic'] = dict(SI.stats)
                        raise AssertionError((direction, unique))
                    alpha[direction] = dict(values=unique, occupied=int(np.count_nonzero(a)), pixels=int(a.size))
                results['masks'] = alpha
                results['source_vertices'] = sum(len(v) for v, t in data['meshes'].values())
                results['source_triangles'] = sum(len(t) for v, t in data['meshes'].values())
                results['first_build_ms'] = SI.stats['build_ms']
                # Warm GPU / Python caches then measure the actual POST_VIEW function.
                SI.draw()
                times = []
                builds = SI.stats['builds'], SI.stats['mask_builds']
                for _ in range(100):
                    start = time.perf_counter()
                    SI.draw()
                    times.append((time.perf_counter() - start) * 1000)
                assert SI.stats['error'] is None, SI.stats
                assert builds == (SI.stats['builds'], SI.stats['mask_builds'])
                results['draw_p95_ms'] = float(np.percentile(times, 95))
                results['no_rebuild_on_redraw'] = True
                p.show_silhouette_avatar = False
                p.show_silhouette_collisions = False
            elif phase == 1:
                capture('before.png')
                p.show_silhouette_avatar = True
                p.show_silhouette_collisions = True
            elif phase == 2:
                capture('after.png')
                results['draw_stats'] = dict(SI.stats)
                # The real event loop below exercises the registered operator,
                # including the current undo guard, rather than a fake RNA owner.
            elif phase == 3:
                old_position = tuple(p.silhouette_position), p.silhouette_positioned
                assert bpy.ops.ac9_cloth_sim.silhouette_position('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
                bpy.context.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=region.x+400, y=region.y+350)
                bpy.context.window.event_simulate(type='ESC', value='PRESS', x=region.x+400, y=region.y+350)
            elif phase == 4:
                assert SI._active is None
                assert (tuple(p.silhouette_position),p.silhouette_positioned) == old_position
                results['native_modal_cancel'] = True
                expected_position = SI.hit_position(bpy.context, region, (400,350))
                bpy.ops.ed.undo_push(message='Before reference placement')
                assert bpy.ops.ac9_cloth_sim.silhouette_position('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
                bpy.context.window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=region.x+400, y=region.y+350)
                bpy.context.window.event_simulate(type='LEFTMOUSE', value='PRESS', x=region.x+400, y=region.y+350)
                bpy.context.window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=region.x+400, y=region.y+350)
            elif phase == 5:
                assert SI._active is None
                results['native_modal_click_error_m'] = max(abs(a-b) for a,b in zip(p.silhouette_position,expected_position))
                assert results['native_modal_click_error_m'] < 1e-6
                assert bpy.ops.ed.undo() == {'FINISHED'}
                p = bpy.context.scene.ac9_cloth_sim
                assert (tuple(p.silhouette_position),p.silhouette_positioned) == old_position
                results['native_undo'] = True
                assert bpy.ops.ed.redo() == {'FINISHED'}
                p = bpy.context.scene.ac9_cloth_sim
                assert max(abs(a-b) for a,b in zip(p.silhouette_position,expected_position)) < 1e-6
                results['native_redo'] = True
                p.silhouette_position,p.silhouette_positioned=old_position
                bpy.context.window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=area.x+int(area.width*.7),y=area.y+int(area.height*.8))
            elif phase == 6:
                bpy.context.preferences.view.language = 'ja_JP'
                bpy.context.preferences.view.use_translate_interface = True
                bpy.ops.wm.call_panel(name='AC9_PT_SimOverlays', keep_open=True)
            elif phase == 7:
                capture('overlay_ui.png')
                save()
                # Actual F3 lifecycle, not manual unregister/importlib.reload.
                results['old_operator_id'] = id(SI.AC9_OT_SilhouettePosition)
                results['old_draw_id'] = id(SI.draw)
                bpy.utils.load_scripts(reload_scripts=True)
                SI = importlib.import_module(PACKAGE + '.silhouette')
                U = importlib.import_module(PACKAGE + '.ui')
                assert results['old_operator_id'] != id(SI.AC9_OT_SilhouettePosition)
                assert results['old_draw_id'] != id(SI.draw)
                results['f3_classes_replaced'] = True
                results['f3_functions_replaced'] = True
                results['f3_handle_count'] = len(SI._handles)
            elif phase == 8:
                SI.draw()
                assert SI.stats['error'] is None, SI.stats
                results['after_f3_stats'] = dict(SI.stats)
                capture('after_f3.png')
                save()
                bpy.ops.wm.quit_blender()
                return None
        phase += 1
        area.tag_redraw()
        save()
        return 1.5
    except Exception:
        results['error'] = traceback.format_exc()
        save()
        bpy.ops.wm.quit_blender()
        return None

bpy.app.timers.register(run, first_interval=3)
