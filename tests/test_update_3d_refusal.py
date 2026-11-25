# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase Sim (update_3d) with no placed pieces says the run's refusal in one line, not as a Python error.

Why (2026-11-24): update_3d calls the run (dress) from Python; the run's refusal reaches it as a RuntimeError, which
went on up as a Python traceback. Found by the automatic tests' route A (every operator in three states).

Run: blender -b --factory-startup --python tests/test_update_3d_refusal.py -- <parent of ac9_cloth_sim> [out.json]"""
import json
import sys
from pathlib import Path

ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
sys.path.insert(0, ARGS[0] if ARGS else str(Path(__file__).resolve().parents[2]))
OUT = next((a for a in ARGS[1:] if a.endswith('.json')), None)

import bpy  # noqa: E402
import ac9_cloth_sim  # noqa: E402

if not hasattr(bpy.types, 'AC9_OT_SimDress'):
    ac9_cloth_sim.register()
from ac9_cloth_sim import store, pattern_view as PV  # noqa: E402
from ac9_cloth_sim.core import spec as S, draft as D  # noqa: E402

result = {'passed': False}
try:
    bpy.ops.wm.read_homefile(use_empty=True)
    import tempfile
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(tempfile.mkdtemp(prefix='ac9_u3d_')) / 'nothing_placed.blend'))
    bpy.ops.ac9_cloth_sim.add_avatar()            # a body: the run's poll asks for one
    ctx = bpy.context
    sp = S.new_spec('NothingPlaced')
    D.add_polygon(sp, [[0, 0], [100, 0], [100, 100], [0, 100]], name='A')
    store.new_text(ctx, sp['name'])
    store.put(ctx, sp)
    bpy.ops.ac9_cloth_sim.detect_guides()          # guides and a body: the run's poll passes, nothing is placed
    sp, outlines = store.get(ctx)
    PV.rebuild(ctx, sp, outlines)
    try:
        end = bpy.ops.ac9_cloth_sim.update_3d('EXEC_DEFAULT')
        text = ''
    except RuntimeError as exc:                  # an ERROR report reaches a Python caller as a RuntimeError
        end, text = {'CANCELLED'}, str(exc)
    result.update(end=sorted(end), text=text[-400:])
    # the refusal must come from the run itself (a poll refusal would not reach the code this test is for)
    result['passed'] = ('FINISHED' not in end and 'poll() failed' not in text and 'placed' in text
                        and 'Traceback' not in text and 'Error: Python' not in text)
except Exception:                                 # noqa: BLE001
    import traceback
    result['error'] = traceback.format_exc()[-800:]
if OUT:
    json.dump(result, open(OUT, 'w', encoding='utf-8'), indent=1)
print('UPDATE 3D REFUSAL', 'OK' if result['passed'] else 'FAILED', result)
sys.stdout.flush()
import os  # noqa: E402
os._exit(0 if result['passed'] else 1)
