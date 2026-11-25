# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender 5.2 / isolated extension; run --python this.py -- <evidence folder>."""
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from silhouette_fixture import enable, setup, preflight, PACKAGE

OUT = Path(sys.argv[sys.argv.index('--') + 1])
OUT.mkdir(parents=True, exist_ok=True)
enable()
SI = importlib.import_module(PACKAGE + '.silhouette')
C = importlib.import_module(PACKAGE + '.core.silhouette')
PV = importlib.import_module(PACKAGE + '.pattern_view')
U = importlib.import_module(PACKAGE + '.ui')
body, accessory = setup()
fixture = preflight((body, accessory), OUT.parent / 'fixture_registry.json', 'analytic boxes; direct display, no simulation')
results = {}
def check(name, value):
    assert value, name
    results[name] = True

p = bpy.context.scene.ac9_cloth_sim
check('both_default_on', p.show_silhouette_avatar and p.show_silhouette_collisions)
inventory = len(bpy.data.objects), len(bpy.data.meshes), p.spec_text.as_string()
D = importlib.import_module(PACKAGE + '.dress')
physics = tuple(D.collider_objects(bpy.context))
data = SI.snapshot(bpy.context)
v, t = data['meshes'][body.session_uid]
origin = C.frame(v)
f, r, l = (C.project(v, origin, d) for d in ('FRONT', 'RIGHT', 'LEFT'))
errors = dict(height_m=abs(np.ptp(f[:, 1]) - 1.8), front_width_m=abs(np.ptp(f[:, 0]) - .6),
              side_width_m=abs(np.ptp(r[:, 0]) - .3), mirror_m=float(np.max(abs(r[:, 0] + l[:, 0]))))
check('metric_projection', max(errors.values()) < 1e-6)
before = body.matrix_world.copy(), accessory.matrix_world.copy(), [tuple(x.co) for x in body.data.vertices]
ids = []
for a, c in ((False, False), (True, False), (False, True), (True, True)):
    p.show_silhouette_avatar, p.show_silhouette_collisions = a, c
    got = SI.enabled_ids(bpy.context, data)
    check('toggle_' + str((a, c)), len(got) == int(a) + int(c))
    ids.append(list(got))
check('source_unchanged', before == (body.matrix_world, accessory.matrix_world, [tuple(x.co) for x in body.data.vertices]))
check('no_generated_scene_data', inventory == (len(bpy.data.objects), len(bpy.data.meshes), p.spec_text.as_string()))
check('physics_registration_unchanged', physics == tuple(D.collider_objects(bpy.context)))
accessory['ac9_collision_body_duplicate_test'] = False
D = importlib.import_module(PACKAGE + '.dress')
body[D.COLLIDER_PROP] = True
check('body_not_duplicated', SI.sources(bpy.context) == (body, (accessory,)))
check('stable_cache', SI.snapshot(bpy.context) is SI.snapshot(bpy.context))
count = SI.stats['builds']
for _ in range(20):
    SI.snapshot(bpy.context)
check('no_geometry_read_on_redraw', count == SI.stats['builds'])
old = SI.snapshot(bpy.context)['meshes'][accessory.session_uid][0].copy()
accessory.location.z += .25
bpy.context.view_layer.update()
new = SI.snapshot(bpy.context)['meshes'][accessory.session_uid][0]
check('object_transform_invalidates', np.max(abs(new - old - (0, 0, .25))) < 1e-6)
old = new.copy()
accessory.data.vertices[0].co.z += .02
accessory.data.update()
bpy.context.view_layer.update()
new = SI.snapshot(bpy.context)['meshes'][accessory.session_uid][0]
check('geometry_change_invalidates', abs(new[0, 2] - old[0, 2] - .02) < 1e-6)
modifier = accessory.modifiers.new('Display scale test', 'SOLIDIFY')
modifier.thickness = .03
bpy.context.view_layer.update()
modified = SI.snapshot(bpy.context)['meshes'][accessory.session_uid][0]
check('evaluated_modifier', len(modified) > len(new))
accessory.modifiers.remove(modifier)
bpy.context.view_layer.update()
# Losing the Body pointer still leaves explicit registered collision sources.
p.body = None
bodyless = SI.snapshot(bpy.context)
check('no_avatar_with_collisions', bodyless['body'] is None and len(bodyless['objects']) == 2)
p.body = body
bpy.context.view_layer.update()
# A rotated/translated layout preserves the chosen metric point.
m = Matrix.Translation((4, 2, .1)) @ Matrix.Rotation(.4, 4, 'Z')
q = C.quad(((-.3, 0), (.3, 1.8)), (2, -1))
world = [m @ Vector(x) for x in q]
check('layout_metric', abs((world[2] - world[1]).length - 1.8) < 1e-6)
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
area.spaces.active.region_3d.view_perspective = 'ORTHO'
area.spaces.active.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
region = next(r for r in area.regions if r.type == 'WINDOW')
class Layout:
    def __init__(self): self.calls = []
    def __getattr__(self, key):
        def call(*args, **kw):
            self.calls.append((key, args, kw))
            return self
        return call
with bpy.context.temp_override(area=area, region=region):
    check('pattern_view_allowed', SI.pattern_view(bpy.context))
    layout = Layout()
    U.AC9_PT_SimOverlays.draw(SimpleNamespace(layout=layout), bpy.context)
    check('panel_reaches_silhouette', any(a == 'operator' and b[0] == 'ac9_cloth_sim.silhouette_position' for a, b, c in layout.calls))
    check('panel_reaches_end', any(a == 'prop' and b[1] == 'thickness_side' for a, b, c in layout.calls))
    times = []
    for _ in range(30):
        start = time.perf_counter()
        U.AC9_PT_SimOverlays.draw(SimpleNamespace(layout=Layout()), bpy.context)
        SI.AC9_OT_SilhouettePosition.poll(bpy.context)
        times.append((time.perf_counter() - start) * 1000)
    results['panel_poll_p95_ms'] = float(np.percentile(times, 95))
    area.spaces.active.region_3d.view_perspective = 'PERSP'
    check('perspective_excluded', not SI.pattern_view(bpy.context))
    area.spaces.active.region_3d.view_perspective = 'ORTHO'
p.silhouette_position = (1.25, -2.5)
p.silhouette_positioned = True
p.show_silhouette_avatar = False
saved = OUT / 'saved_state.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(saved))
bpy.ops.wm.open_mainfile(filepath=str(saved))
p = bpy.context.scene.ac9_cloth_sim
check('saved_position', tuple(p.silhouette_position) == (1.25, -2.5) and p.silhouette_positioned)
check('saved_switches', not p.show_silhouette_avatar and p.show_silhouette_collisions)
data = SI.snapshot(bpy.context)
accessory = bpy.data.objects['DisplayAccessory']
bpy.data.objects.remove(accessory, do_unlink=True)
bpy.context.view_layer.update()
check('delete_source', len(SI.snapshot(bpy.context)['objects']) == 1)
p.body = None
body = bpy.data.objects['DisplayBody']
del body[D.COLLIDER_PROP]
bpy.context.view_layer.update()
check('empty_sources', SI.snapshot(bpy.context)['empty'])
results['projection_errors'] = errors
results['extension_file'] = importlib.import_module(PACKAGE).__file__
results['blender'] = bpy.app.version_string
(OUT / 'headless.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
