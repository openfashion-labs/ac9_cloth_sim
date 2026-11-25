# SPDX-License-Identifier: GPL-3.0-or-later
"""An operation that is refused, canceled or fails half way leaves an undo history that can be walked (Blender).

Why (2026-11-23): a refused operation rolled back by installing copies of the placed pieces' meshes and wrote no
undo step; the next undo freed the copies under objects still pointing at them and Blender crashed. undo_guard
writes the missing step. Checked here, each followed by undo, undo, redo, redo with every object's data, parent,
constraint target and collection looked up in bpy.data after every step (a crash ends the process: no result file):

  1. the guard itself, on a made-up scene: a canceled operator that swapped a mesh, one that raised after linking a
     new object, one that touched nothing (no step), one with UNDO that finished (Blender's step, none of ours);
  2. From Mesh refuses a pattern piece as its source;
  3. the run's modal takes the undo / redo keys;
  4. with a .blend of a dressed garment (the release gate passes its standard test garment): the placement
     operators as they are refused or canceled there, some with a failure injected into the preparation, and the
     Delete key's delete of a seam as its own step.

Run: blender -b --factory-startup --python tests/test_undo_safety.py -- <parent of ac9_cloth_sim> [garment.blend]
     [out.json]     (exit code 0 = passed; the .blend is opened, never saved)
"""
import json
import os
import sys
import traceback
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))
BLEND = next((a for a in ARGS[1:] if a.endswith('.blend')), None)
OUT = next((a for a in ARGS[1:] if a.endswith('.json')), None)

import bpy                                                   # noqa: E402

import ac9_cloth_sim                                         # noqa: E402

if not hasattr(bpy.types, 'AC9_OT_SimDress'):
    ac9_cloth_sim.register()
from ac9_cloth_sim import undo_guard as UG, dress as D, store, selection as SEL, mesh_import as MI   # noqa: E402
from ac9_cloth_sim import draft_tool as DT, tool_tab as TT                                           # noqa: E402

RESULT = {'cases': [], 'failed': []}
KINDS = ('objects', 'meshes', 'collections', 'materials', 'node_groups', 'curves', 'texts', 'images', 'scenes',
         'cameras', 'lights', 'armatures')


def save():
    if OUT:
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(RESULT, f, indent=1)


def dangling():
    """What points at a datablock that is not in bpy.data any more."""
    known = {i.as_pointer() for kind in KINDS for i in getattr(bpy.data, kind, ())}
    bad = []
    for o in bpy.data.objects:
        if o.data is not None and o.data.as_pointer() not in known:
            bad.append(o.name + '.data')
            continue
        if o.parent is not None and o.parent.as_pointer() not in known:
            bad.append(o.name + '.parent')
        for c in o.constraints:
            t = getattr(c, 'target', None)
            if t is not None and t.as_pointer() not in known:
                bad.append(o.name + '.constraint')
    for c in [s.collection for s in bpy.data.scenes] + list(bpy.data.collections):
        bad += [c.name + '.objects' for o in c.objects if o.as_pointer() not in known]
        bad += [c.name + '.children' for ch in c.children if ch.as_pointer() not in known]
    return bad


def fail(name, why):
    RESULT['failed'].append(f"{name}: {why}")
    save()


def walk(name, row):
    """undo, undo, redo, redo; the process ends at the first dangling pointer (the next read would crash)."""
    for i, step in enumerate(('undo', 'undo', 'redo', 'redo')):
        row['at'] = f"{step} {i + 1}"
        save()
        try:
            getattr(bpy.ops.ed, step)()
        except RuntimeError:
            pass
        bad = dangling()
        if bad:
            row['dangling'] = bad[:6]
            fail(name, f"dangling after {step} {i + 1}: {bad[:3]}")
            os._exit(1)
    row['at'] = 'done'
    save()


def history():
    """Two steps that differ only by a probe: every other datablock is identical in both, the case in which
    Blender's undo keeps the datablock in memory instead of reading it again."""
    probe = bpy.data.objects.new('undo_test_probe', None)
    bpy.context.scene.collection.objects.link(probe)
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message='A')
    probe.location.x = 1.0
    bpy.ops.ed.undo_push(message='B')


def call(name, fn, undo_flag, **kw):
    """The operator as a key or a button would end it: Blender's step only when it has UNDO and FINISHED."""
    row = {'case': name, 'guard_steps': 0}
    RESULT['cases'].append(row)
    before = UG.pushed[0]
    try:
        row['end'] = '/'.join(sorted(fn('EXEC_DEFAULT', **kw)))
    except RuntimeError as exc:
        text = str(exc)
        row['end'] = 'POLL' if 'poll() failed' in text else 'EXCEPTION' if 'Traceback' in text else 'ERROR'
    row['guard_steps'] = UG.pushed[0] - before
    if row['end'] == 'FINISHED' and undo_flag:
        bpy.ops.ed.undo_push(message=name)
    bad = dangling()
    if bad:
        fail(name, f"dangling after the operator: {bad[:3]}")
        os._exit(1)
    return row


# ---- 1. the guard on a made-up scene

class UNDOTEST_OT_swap_cancel(bpy.types.Operator):
    bl_idname = "undotest.swap_cancel"
    bl_label = "swap, cancel"

    def execute(self, context):
        ob = bpy.data.objects['undo_test_cube']
        old = ob.data
        ob.data = old.copy()
        bpy.data.meshes.remove(old)
        return {'CANCELLED'}


class UNDOTEST_OT_link_raise(bpy.types.Operator):
    bl_idname = "undotest.link_raise"
    bl_label = "link, raise"

    def execute(self, context):
        context.scene.collection.objects.link(bpy.data.objects.new('undo_test_new', None))
        raise ValueError("half way")


class UNDOTEST_OT_nothing(bpy.types.Operator):
    bl_idname = "undotest.nothing"
    bl_label = "nothing, cancel"

    def execute(self, context):
        return {'CANCELLED'}


class UNDOTEST_OT_finished(bpy.types.Operator):
    bl_idname = "undotest.finished"
    bl_label = "swap, finished"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ob = bpy.data.objects['undo_test_cube']
        old = ob.data
        ob.data = old.copy()
        bpy.data.meshes.remove(old)
        return {'FINISHED'}


OWN = (UNDOTEST_OT_swap_cancel, UNDOTEST_OT_link_raise, UNDOTEST_OT_nothing, UNDOTEST_OT_finished)


def made_up_scene():
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    me = bpy.data.meshes.new('undo_test_cube')
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    bpy.context.scene.collection.objects.link(bpy.data.objects.new('undo_test_cube', me))
    history()


def test_guard():
    UG.install([sys.modules[__name__]])
    for cls in OWN:
        bpy.utils.register_class(cls)
    for name, want in (('swap_cancel', 1), ('link_raise', 1), ('nothing', 0), ('finished', 0)):
        made_up_scene()
        cls = getattr(bpy.types, 'UNDOTEST_OT_' + name)
        row = call('guard ' + name, getattr(bpy.ops.undotest, name), 'UNDO' in getattr(cls, 'bl_options', ()))
        if row['guard_steps'] != want:
            fail(row['case'], f"the guard wrote {row['guard_steps']} step(s), expected {want}")
        walk(row['case'], row)
    for cls in OWN:
        bpy.utils.unregister_class(cls)


# ---- 2. From Mesh

def test_from_mesh():
    made_up_scene()
    scene = bpy.context.scene
    body = bpy.data.objects.new('undo_test_body', bpy.data.meshes.new('undo_test_body'))
    scene.collection.objects.link(body)
    scene.ac9_cloth_sim.body = body
    cube = bpy.data.objects['undo_test_cube']
    row = {'case': 'from mesh source', 'own_mesh': MI.can_be_source(cube, body)}
    cube['ac9_piece'] = 'P1'
    row['piece'] = MI.can_be_source(cube, body)
    bpy.context.view_layer.objects.active = cube
    row['poll_with_piece_active'] = bool(bpy.ops.ac9_cloth_sim.from_mesh.poll())
    RESULT['cases'].append(row)
    if not row['own_mesh'] or row['piece'] or row['poll_with_piece_active']:
        fail(row['case'], f"{row}")


# ---- 3. the run's modal takes the undo keys

class _Event:
    def __init__(self, type, value='PRESS', ctrl=False, shift=False, alt=False, oskey=False):
        self.type, self.value, self.ctrl, self.shift, self.alt, self.oskey = type, value, ctrl, shift, alt, oskey


class _Run:
    """What _modal reads before the undo check."""
    _abandoned = False

    def __init__(self):
        self.said = []

    def report(self, level, text):
        self.said.append(text)


def test_undo_keys():
    c = bpy.context
    row = {'case': 'undo keys',
           'ctrl_z': UG.is_undo_event(c, _Event('Z', ctrl=True)),
           'ctrl_shift_z': UG.is_undo_event(c, _Event('Z', ctrl=True, shift=True)),
           'z': UG.is_undo_event(c, _Event('Z')),
           'release': UG.is_undo_event(c, _Event('Z', value='RELEASE', ctrl=True)),
           'mouse': UG.is_undo_event(c, _Event('MOUSEMOVE', value='NOTHING')),
           'esc': UG.is_undo_event(c, _Event('ESC'))}
    run = _Run()
    plain = getattr(D.AC9_OT_SimDress, '_modal')
    row['modal_ctrl_z'] = sorted(plain(run, c, _Event('Z', ctrl=True)))
    row['modal_ctrl_shift_z'] = sorted(plain(run, c, _Event('Z', ctrl=True, shift=True)))
    row['said'] = len(run.said)
    RESULT['cases'].append(row)
    if not (row['ctrl_z'] and row['ctrl_shift_z'] and not row['z'] and not row['release'] and not row['mouse']
            and not row['esc'] and row['modal_ctrl_z'] == ['RUNNING_MODAL']
            and row['modal_ctrl_shift_z'] == ['RUNNING_MODAL'] and row['said'] == 2):
        fail(row['case'], f"{row}")


# ---- 4. a dressed garment

def view3d():
    win = bpy.context.window or bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            return {'window': win, 'screen': win.screen, 'area': area,
                    'region': next(r for r in area.regions if r.type == 'WINDOW')}
    return {'window': win}


def pieces(spec, n):
    g = bpy.data.objects.get(D.garment_name(spec))
    try:
        reset = json.loads(g.get('ac9_reset_pieces', '[]')) if g else []
    except ValueError:
        reset = []
    return (reset + [p['name'] for p in spec['pieces'] if p['name'] not in reset])[:n]


def garment_case(op, select=0, inject=None):
    name = f"{op}{' selected ' + str(select) if select else ''}{' +' + inject if inject else ''}"
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    over = view3d()
    spec = store.get(bpy.context)[0]
    if select:
        with bpy.context.temp_override(**over):
            SEL.apply(bpy.context, pieces(spec, select))
    history()
    finish, prepare = D.Preparation.finish, D._prepare
    if inject == 'commit':
        def boom(self):
            raise ValueError("injected failure at the commit")
        D.Preparation.finish = boom
    elif inject == 'build':
        def boom2(*a, **k):
            prepare(*a, **k)
            raise ValueError("injected failure after the shapes were built")
        D._prepare = boom2
    try:
        cls = getattr(bpy.types, getattr(bpy.ops.ac9_cloth_sim, op).get_rna_type().identifier)
        with bpy.context.temp_override(**over):
            row = call(name, getattr(bpy.ops.ac9_cloth_sim, op), 'UNDO' in getattr(cls, 'bl_options', ()))
    finally:
        D.Preparation.finish, D._prepare = finish, prepare
    walk(name, row)
    return row


def delete_key_case():
    """The X / Del operator on a picked seam. -b has no event, so its invoke is run by a stand-in execute; the call
    asks for the step as a key press does (written only when the operator has UNDO and ended FINISHED)."""
    cls = DT.AC9_OT_SimDeleteSelected
    pick = DT.pick
    bpy.utils.unregister_class(cls)
    cls.execute = lambda self, context: self.invoke(context, _Event('X'))
    _Event.mouse_region_x = _Event.mouse_region_y = 0
    bpy.utils.register_class(cls)
    DT.pick = lambda *a, **k: None                      # nothing under the cursor: the picked thing goes
    row = {'case': 'delete key on a seam'}
    RESULT['cases'].append(row)

    def seams():
        store.invalidate()
        return len(store.get(bpy.context)[0].get('seams', []))
    try:
        bpy.ops.wm.open_mainfile(filepath=BLEND)
        with bpy.context.temp_override(**view3d()):
            c = bpy.context
            bpy.ops.ed.undo_push(message='A')
            c.scene['undo_test_probe'] = 1
            bpy.ops.ed.undo_push(message='B')
            props = c.scene.ac9_cloth_sim
            for p in props.bl_rna.properties.keys():
                if 'seam' in p and 'index' in p:
                    try:
                        setattr(props, p, 0)
                    except (AttributeError, TypeError):
                        pass
            TT.publish(c, 'SEAM', origin='seam-list')
            row['before'] = seams()
            row['end'] = sorted(bpy.ops.ac9_cloth_sim.delete_selected('EXEC_DEFAULT', True))
            row['after'] = seams()
            bpy.ops.ed.undo()
            row['undone'], row['probe_after_undo'] = seams(), c.scene.get('undo_test_probe', 0)
            bpy.ops.ed.redo()
            row['redone'] = seams()
    finally:
        DT.pick = pick
        bpy.utils.unregister_class(cls)
        del cls.execute
        bpy.utils.register_class(cls)
    if not (row.get('after', 0) < row.get('before', 0) and row.get('undone') == row['before']
            and row.get('probe_after_undo') == 1 and row.get('redone') == row['after']):
        fail(row['case'], f"{row}")


def test_garment():
    rows = [garment_case('move_to_seam'), garment_case('lay_on_edge'),
            garment_case('clear_placement', select=1), garment_case('move_outside', select=1),
            garment_case('move_outside', select=2), garment_case('from_mesh', select=1),
            garment_case('reset_placed', inject='commit'), garment_case('show_placed', inject='build'),
            garment_case('clear_placement', inject='commit'), garment_case('mirror_copy', select=1, inject='commit')]
    # the test must have met what it is for: an ending without Blender's step that the guard had to seal
    if not any(r['guard_steps'] for r in rows):
        fail('garment', "no case needed the guard's step on this .blend: the test proved nothing")
    delete_key_case()


def main():
    try:
        test_guard()
        test_from_mesh()
        test_undo_keys()
        if BLEND:
            test_garment()
        else:
            RESULT['skipped'] = "no .blend given: the dressed garment's cases were not run"
    except Exception:                                    # noqa: BLE001
        fail('test', traceback.format_exc()[-800:])
    RESULT['ok'] = not RESULT['failed']
    save()
    print('UNDO SAFETY', 'OK' if RESULT['ok'] else 'FAILED', len(RESULT['cases']), 'cases')
    for line in RESULT['failed']:
        print('  ', line)
    sys.stdout.flush()
    os._exit(0 if RESULT['ok'] else 1)


main()
