# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite analytic display fixture; no simulation result is used as a test bed."""
import importlib
import json
from pathlib import Path
import bpy
import numpy as np

PACKAGE = 'bl_ext.user_default.ac9_cloth_sim'


def enable():
    import addon_utils
    bpy.ops.preferences.addon_refresh()
    module = addon_utils.enable(PACKAGE, default_set=True, persistent=True)
    if module is None:
        raise RuntimeError('Isolated extension was not enabled')
    return module


def box(name, lo, hi):
    v = [(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
    f = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    m = bpy.data.meshes.new(name)
    m.from_pydata(v, [], f)
    m.update()
    ob = bpy.data.objects.new(name, m)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def preflight(objects, out, condition):
    records = []
    for ob in objects:
        v = np.array([tuple(p.co) for p in ob.data.vertices])
        ob.data.calc_loop_triangles()
        t = np.array([tuple(p.vertices) for p in ob.data.loop_triangles])
        assert len(v) and len(t) and np.isfinite(v).all()
        assert t.min() >= 0 and t.max() < len(v)
        areas = np.linalg.norm(np.cross(v[t[:, 1]]-v[t[:, 0]], v[t[:, 2]]-v[t[:, 0]]), axis=1) / 2
        assert (areas > 0).all()
        records.append(dict(name=ob.name, vertices=len(v), triangles=len(t),
                            dimensions=np.ptp(v, axis=0).tolist(), finite=True, min_area=float(areas.min())))
    data = dict(verdict='ok', scope='display geometry only; no simulation', condition=condition, objects=records)
    Path(out).write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


def setup():
    mod = importlib.import_module(PACKAGE)
    PV = importlib.import_module(PACKAGE + '.pattern_view')
    S = importlib.import_module(PACKAGE + '.core.spec')
    ST = importlib.import_module(PACKAGE + '.store')
    for ob in list(bpy.context.scene.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    body = box('DisplayBody', (-0.3, -0.12, 0), (0.3, 0.18, 1.8))
    accessory = box('DisplayAccessory', (-0.4, -0.26, 0.25), (-0.1, -0.1, 0.7))
    accessory['ac9_collision'] = True  # replaced below with the product's exact key
    D = importlib.import_module(PACKAGE + '.dress')
    del accessory['ac9_collision']
    accessory[D.COLLIDER_PROP] = True
    p = bpy.context.scene.ac9_cloth_sim
    p.body = body
    # A plain planar garment for overlay/order testing, not a simulation fixture.
    loops = [[(-420, -100), (-420, 500), (-220, 610), (-140, 500), (0, 500), (80, 610), (280, 500), (280, -100)],
             [(480, -100), (480, 500), (680, 610), (760, 580), (900, 580), (980, 610), (1180, 500), (1180, -100)]]
    pcs = []
    for i, ring in enumerate(loops):
        edges = [{'id': 'E' + str(k), 'pts': [a, ring[(k + 1) % len(ring)]]} for k, a in enumerate(ring)]
        pcs.append(dict(name='DisplayPanel' + str(i), enabled=True, edges=edges, internal_lines=[]))
    spec = dict(version=1, name='DisplayFixture', units='mm', pieces=pcs, seams=[], guides={}, placement={}, twins=[])
    text = bpy.data.texts.new('DisplaySpec')
    text.write(json.dumps(spec))
    p.spec_text = text
    outlines = {pc['name']: S.Outline(pc) for pc in pcs}
    PV.rebuild(bpy.context, spec, outlines)
    bpy.context.view_layer.update()
    return body, accessory
