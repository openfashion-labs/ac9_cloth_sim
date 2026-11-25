# SPDX-License-Identifier: GPL-3.0-or-later
"""The dressed garment as shown, and whether its result folder still describes it.

The rule: the garment's mesh in the .blend is what the user sees, so it is the truth. Its result folder (ac9_job) is a
cache of how that mesh was made (the pattern layout, the stitches, the frames). The two are written apart -- a run
swaps the garment's mesh when it starts and points ac9_job at its own folder only when it ends -- so a .blend saved in
between keeps a mesh that its ac9_job does not describe (2026-11-23: a coat saved 12 s after a Live started kept the
run's mesh with two more pieces; the next run took the old folder, did not find the two pieces in it, made them again
from the placement rule and started a collar 114 mm from where it was shown, while the 3D internal lines were drawn
through another piece's vertices).

Whoever reads the garment's result asks here first (fits / ensure_fits) instead of trusting ac9_job: when the folder
does not describe the mesh, the run goes on from the mesh (dress.job_from_mesh), as for a .blend whose folder is lost.
"""
_fit_cache = {}
NOTES = []           # lines for the press that made a folder from the mesh (dress.AC9_OT_SimDress reports them)


def fits(g, job):
    """True when the result folder `job` describes g's mesh (the same pieces with the same vertex counts,
    blend_record's key), False when it does not, None when it cannot be told (no folder, or a mesh without piece
    metadata -- not one Dress made)."""
    if g is None or not job:
        return None
    from . import blend_record as BR
    me = g.data
    # no call to the disk to make the key: it is asked in draws, and a result folder is written once under its own
    # name (a run or a carry makes a new one)
    key = (g.as_pointer(), me.as_pointer(), len(me.vertices), len(me.polygons), str(g.get('ac9_piece_names', '')),
           str(job))
    if key not in _fit_cache:
        if len(_fit_cache) > 32:
            _fit_cache.clear()
        mk, rk = BR.mesh_key(g), BR.result_key(job)
        _fit_cache[key] = None if mk is None or rk is None else mk == rk
    return _fit_cache[key]


def ensure_fits(context, g):
    """At a press (never while a run holds the garment, never in a draw): when g's result folder does not describe
    its mesh, make a folder from the mesh (dress.job_from_mesh) so the run goes on from what is shown. Returns a line
    saying so, or ''."""
    from . import dress as DR
    if g is None or DR.AC9_OT_SimDress.running() is not None:
        return ''
    j = DR.job_of(g)
    if fits(g, j) is not False:
        return ''
    made = DR.job_from_mesh(context, g, str(j))
    if made is None:
        return ''
    DR.log(context, made, 'shape_source', "the garment's result folder did not describe its mesh; "
           "a folder was made from the mesh shown", was=str(j))
    return ("The garment's saved run did not match what is shown: it goes on from the garment as shown")


def invalidate():
    _fit_cache.clear()


def mismatched(context, spec):
    """The dressed garment of spec when its result folder does not describe its mesh (and no run holds it), else
    None. Cheap after the first call per garment and folder (fits is cached): read in the run panel's draw."""
    from . import dress as DR
    if spec is None or DR.AC9_OT_SimDress.running() is not None:
        return None
    g = DR.garment(spec)
    if g is None:
        return None
    return g if fits(g, DR.job_of(g)) is False else None


def draw_mismatch(layout, context, spec):
    """A red box at the top of AC9 Run when the garment's saved run is not what is shown (a .blend saved while a run
    held the garment): what it means and the button that makes the shown garment the start. A run pressed without
    it does the same and says so; the box is there so the user sees it before pressing, and knows the way out."""
    from .i18n import tx
    if mismatched(context, spec) is None:
        return
    box = layout.box()
    box.alert = True
    box.label(text=tx("The saved run does not match the garment shown"), icon='ERROR')
    box.label(text=tx("The next run starts from the garment as shown"))
    box.operator("ac9_cloth_sim.use_shown_garment", icon='FILE_REFRESH')


def check_lines(context, spec):
    """[(level, English line with {0}..., args)] -- what may make a run start other than as shown, each with what to
    do. Empty: nothing found. Read-only."""
    from . import dress as DR, placed_view as PV, display_state as DS
    out = []
    if spec is None:
        return out
    g = DR.garment(spec)
    if g is not None and DR.has_result(g):
        job = DR.job_of(g)
        ok = fits(g, job)
        if job is None:
            out.append(('INFO', "The garment's run folder is not here: the next run starts from the garment as shown", ()))
        elif ok is False:
            out.append(('WARNING', "The saved run does not match the garment shown: press Use the Shown Garment "
                                   "(or any run, which does the same)", ()))
        elif ok is None:
            out.append(('WARNING', "The garment's run could not be compared with its mesh", ()))
        if job:
            import json, os
            try:
                with open(os.path.join(job, 'start_check.json'), encoding='utf-8') as f:
                    report = json.load(f)
                if not isinstance(report, dict) or not isinstance(report.get('pieces'), dict):
                    raise ValueError('Invalid start record')
                last = report['pieces']
                if any(not isinstance(n, str) or not isinstance(r, dict) for n, r in last.items()):
                    raise ValueError('Invalid start record pieces')
                off = sorted(n for n, r in last.items() if r.get('checked') and r.get('over'))
                if off:
                    out.append(('WARNING', "The last run did not start as shown: {0}. Undo it (Ctrl+Z) and press "
                                           "again; see the recorded run checks", (", ".join(off),)))
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                out.append(('WARNING', "The previous start-shape record could not be read; current-shape diagnosis is unaffected", ()))
    for n, v in DS.snapshot(spec, g).items():
        if v['solid'] and v['source'] == 'placed':
            ob = PV.objects(spec).get(n)
            if ob is not None and PV.hand_shape(ob) is None:
                out.append(('WARNING', "{0}: its placed object has no layout: Place it again", (n,)))
    # A missing display mesh can still have a placement rule and join Sim.
    # Display availability belongs to Diagnose's scope, not this start-shape check.
    return out


import bpy  # noqa: E402


class AC9_OT_UseShownGarment(bpy.types.Operator):
    """Make the garment as shown the start of the next run: its saved run folder describes another shape (the file
    was saved while a run was going)"""
    bl_idname = "ac9_cloth_sim.use_shown_garment"
    bl_label = "Use the Shown Garment"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import store
        return mismatched(context, store.get(context)[0]) is not None

    def execute(self, context):
        from . import store, overlay
        from .i18n import rpt
        g = mismatched(context, store.get(context)[0])
        line = ensure_fits(context, g)
        NOTES.clear()
        invalidate()
        overlay.invalidate()
        if not line:
            self.report({'WARNING'}, rpt("The shown garment could not be read as a result"))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt("The next run starts from the garment as shown"))
        return {'FINISHED'}


class AC9_OT_CheckShapes(bpy.types.Operator):
    """List what may make the next run start other than as shown, and what to do for each"""
    bl_idname = "ac9_cloth_sim.check_shapes"
    bl_label = "Check Start Shapes"
    bl_options = {'REGISTER', 'INTERNAL'}  # legacy callable; Diagnose is the visible entry point

    def execute(self, context):
        from . import store
        from .i18n import rpt
        lines = check_lines(context, store.get(context)[0])
        if not lines:
            self.report({'INFO'}, rpt("No start-shape warnings found"))
        for level, ln, args in lines:
            self.report({level}, rpt(ln, *args))
        return {'FINISHED'}


class AC9_OT_RecheckGarmentOwner(bpy.types.Operator):
    """Compare unowned garment results with their saved patterns again; only a unique verified match is linked"""
    bl_idname = "ac9_cloth_sim.recheck_garment_owner"
    bl_label = "Recheck Garment Ownership"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import dress, store
        return store.text(context) is not None and not dress.AC9_OT_SimDress.running()

    def execute(self, context):
        from . import display_state, legacy_garment
        from .i18n import rpt
        bound = legacy_garment.reconcile(context)
        display_state._sync_all()
        count = len(legacy_garment.notices(context))
        if count:
            self.report({'WARNING'}, rpt("Ownership is still unclear for {0} garment result(s)", count))
        else:
            self.report({'INFO'}, rpt("Garment ownership checked; {0} result(s) linked", len(bound)))
        return {'FINISHED'}


_classes = (AC9_OT_UseShownGarment, AC9_OT_CheckShapes, AC9_OT_RecheckGarmentOwner)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
