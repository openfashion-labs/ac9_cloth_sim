# SPDX-License-Identifier: GPL-3.0-or-later
"""The places that can leave memory different from the last undo step are counted (no Blender: a file scan).

Why (2026-11-23): Blender's undo keeps in memory every datablock that is the same in two steps and frees the ones
the steps do not know. Code that puts a new datablock under an old one (ob.data = a copy), frees one, or calls
another operator from Python (which writes no undo step) and then ends without a step leaves an old datablock
pointing at freed memory: the next undo crashed after a refused or canceled operation (four operators on two test
garments; with a failure injected into the placed pieces' preparation, 29 of 40 rows). undo_guard writes the
missing step at the operator's end; this test keeps the number of such places from growing unseen. The counts are
today's. A new place fails here: do it inside an operator (the guard covers it), prefer writing in place over
installing a copy, call an operator from Python with undo=True when the caller writes no step of its own -- or
raise the count with the reason in the same change. A count going down is fine: lower it here.

Run: python tests/test_undo_sites_guard.py (from the add-on folder; no Blender)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = ('tests', 'solver', '__pycache__', 'LLM', 'docs', 'core')

# a datablock put under an object: ob.data = ...
SWAP = re.compile(r"(?<![\w.])\w+(?:\.\w+)*\.data\s*=(?!=)")
# placed_view.py 6: write_local_result (a Local Sim's result kept on the placed object) is called only from the
# Simulate operator's execute and modal (dress.AC9_OT_SimDress._to_placed via _finish), which undo_guard wraps
SWAP_ALLOWED = {'button_view.py': 2, 'dress.py': 2, 'measure_tool.py': 2, 'pattern_view.py': 1, 'placed_view.py': 8,
                'prepare_build.py': 1, 'zipper_view.py': 1}
# a datablock freed: bpy.data.<kind>.remove(...)
FREE = re.compile(r"bpy\.data\.\w+\.(?:remove|batch_remove)\(")
FREE_ALLOWED = {'button_view.py': 10, 'draft_tool.py': 2, 'dress.py': 5, 'export_retopo.py': 2, 'guide_edit.py': 3,
                'operators.py': 6, 'pattern_view.py': 6, 'piece_move.py': 4, 'place_seam.py': 3, 'placed_view.py': 30,
                'prepare_build.py': 2, 'work_screen.py': 2, 'zipper_view.py': 4}
# one of the add-on's operators called from Python with no undo step asked for (the second argument True)
NESTED = re.compile(r"bpy\.ops\.ac9_cloth_sim\.(?!\w+\.poll\b)\w+\((?!\s*'(?:INVOKE|EXEC)_DEFAULT',\s*True\b)")
NESTED_ALLOWED = {'draft_tool.py': 10, 'live_key.py': 1, 'measure_edit.py': 2, 'placement.py': 2, 'seam_edit_tool.py': 1,
                  'shape_tool.py': 1, 'toolbar.py': 2}
# an undo step written by hand: through undo_guard.push, so the guard knows memory is a step again
PUSH = re.compile(r"bpy\.ops\.ed\.undo_push\(")
PUSH_ALLOWED = {'undo_guard.py': 2}
# pattern_view.py's three additional frees retire only staged restore objects/meshes and a newly created
# recovery collection on failure, inside the UNDO Restore Pieces operator. Existing display objects survive.
# placed_view.py +2 swaps/+2 frees: deb957c's shared-mesh isolation and unchanged-result
# reconnection in PreparedResetBackup, reached through the guarded Part to Seam execute.
# Independently checked by test_move_state_snapshot and actual Part to Seam two-step undo/redo;
# the expected count still rejects any further unreviewed site. No handler/timer site was added.


def _sources():
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith('.py'):
                p = os.path.join(root, f)
                text = open(p, encoding='utf-8').read()
                code = '\n'.join(line for line in text.split('\n') if not line.lstrip().startswith('#'))
                yield os.path.relpath(p, HERE).replace(os.sep, '/'), code


def counts(pattern):
    return {rel: n for rel, n in ((rel, len(pattern.findall(code))) for rel, code in _sources()) if n}


def _over(pattern, allowed):
    return [f"{rel}: {n} (allowed {allowed.get(rel, 0)})" for rel, n in sorted(counts(pattern).items())
            if n > allowed.get(rel, 0)]


class UndoSitesGuard(unittest.TestCase):
    def test_datablock_swaps_in_known_places_only(self):
        self.assertEqual(_over(SWAP, SWAP_ALLOWED), [],
                         "a new place installs a datablock under an object: write in place when the vertex count "
                         "is the same, and do it inside an operator (undo_guard writes the step a cancel needs)")

    def test_datablock_frees_in_known_places_only(self):
        self.assertEqual(_over(FREE, FREE_ALLOWED), [],
                         "a new place frees a datablock: inside an operator only, never from a handler or a timer")

    def test_operators_called_from_python_in_known_places_only(self):
        self.assertEqual(_over(NESTED, NESTED_ALLOWED), [],
                         "an operator called from Python writes no undo step: the caller must have UNDO and end "
                         "FINISHED, or pass ('EXEC_DEFAULT', True)")

    def test_undo_steps_written_through_the_guard(self):
        self.assertEqual(_over(PUSH, PUSH_ALLOWED), [], "use undo_guard.push(message)")

    def test_guard_installed_before_registration(self):
        text = open(os.path.join(HERE, '_addon.py'), encoding='utf-8').read()
        body = text.split('def register()', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('_undo_guard.install(', body)
        self.assertLess(body.index('_undo_guard.install('), body.index('_properties.register()'))

    def test_delete_key_writes_its_step(self):
        text = open(os.path.join(HERE, 'draft_tool.py'), encoding='utf-8').read()
        body = text.split('class AC9_OT_SimDeleteSelected', 1)[1].split('\nclass ', 1)[0]
        self.assertRegex(body, r"bl_options\s*=\s*\{[^}]*'UNDO'")

    def test_run_takes_the_undo_keys(self):
        text = open(os.path.join(HERE, 'dress.py'), encoding='utf-8').read()
        body = text.split('    def _modal(self, context, event):', 1)[1].split('\n    def ', 1)[0]
        self.assertIn('UG.is_undo_event(context, event)', body)
        self.assertLess(body.index('UG.is_undo_event'), body.index('_pending_stop'))

    def test_allow_lists_name_files_that_exist(self):
        for allowed in (SWAP_ALLOWED, FREE_ALLOWED, NESTED_ALLOWED, PUSH_ALLOWED):
            for rel in allowed:
                self.assertTrue(os.path.exists(os.path.join(HERE, rel)), rel)


if __name__ == '__main__':
    import sys
    if '--print' in sys.argv:           # today's counts, to paste into the tables above
        for name, pat in (('SWAP', SWAP), ('FREE', FREE), ('NESTED', NESTED), ('PUSH', PUSH)):
            print(name, dict(sorted(counts(pat).items())), sum(counts(pat).values()))
        sys.exit(0)
    unittest.main()
