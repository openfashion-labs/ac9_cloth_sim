# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

from .i18n import CTX
from .units import LEN_STEP
from bpy.types import PropertyGroup


def _fast_live_choice(self, context):
    if self.fast_live and self.native_three:
        self.native_three = False


def _native_three_choice(self, context):
    if self.native_three and self.fast_live:
        self.fast_live = False


def _pattern_tools(self, context):
    self.pattern_tools_set = True       # decided (by the user or the first garment): not turned on by itself again
    from . import toolbar
    toolbar.sync(context)


def _redraw(self, context):
    from . import overlay
    overlay.invalidate()
    # the Properties editor too: its Tool tab shows the selected seam (tool_tab.py)
    for area in context.screen.areas if context.screen else []:
        if area.type in ('VIEW_3D', 'PROPERTIES'):
            area.tag_redraw()


def _follow_edits_on(context):
    # 3D Follows Edits switched back on: the edits made while it was off go to the 3D now (draft_tool.sync_3d)
    from . import draft_tool
    draft_tool.sync_3d(context)


def _seam_picked(self, context):
    # a seam picked in the list or on the pattern: the Properties editor shows it (the last thing touched)
    from . import store
    if not store._seam_keep[0]:
        store.seam_more.clear()
    from . import tool_tab
    tool_tab.set_focus('SEAM')
    _redraw(self, context)


def _keep_color(self, context):
    # the swatch shows the seam's color on the pattern; picking another one there would not change the pattern
    if tuple(self.color) != tuple(self.color_ref):
        self.color = self.color_ref


class AC9ClothSimSeamItem(PropertyGroup):
    """A row of the seam list (a mirror of the spec; the spec text is the source of truth)."""
    seam_id: StringProperty()
    label: StringProperty()
    len_a: FloatProperty(unit='NONE')
    len_b: FloatProperty(unit='NONE')
    check: StringProperty()
    source: StringProperty()
    enabled: BoolProperty(default=True)
    # the seam's color on the pattern (core/seam_edit.color), drawn as a swatch in the list; its group (an M:N
    # seam's stretches share one): the list indents the stretches after the first
    color: FloatVectorProperty(name="Seam Color", subtype='COLOR', size=4, min=0.0, max=1.0, default=(1, 1, 1, 1),
                               update=_keep_color, description="The seam's color on the pattern")
    color_ref: FloatVectorProperty(size=4, default=(1, 1, 1, 1))
    group: StringProperty()
    in_group: BoolProperty(default=False)


# The Tool tab's AC9 Piece fields (user 2026-10-20: direct input, not Set... dialogs): each reads the active piece
# and writes every selected piece through the writers the dialogs use (operators.set_piece_*). Not stored here.
def _ops():
    from . import operators
    return operators


def _act():
    """(spec, the active piece's name, the selected pieces)."""
    from . import store
    ctx = bpy.context
    spec = store.get(ctx)[0]
    O = _ops()
    sel = O._sel_pieces(ctx)
    return spec, (O._active_piece(ctx, sel) if spec is not None else None), sel


def _get_dist(self):
    spec, n, _ = _act()
    pc = next((p for p in (spec or {}).get('pieces', []) if p['name'] == n), None)
    return float((pc or {}).get('mesh_mm') or self.mesh_size)


def _set_dist(self, v):
    spec, n, sel = _act()
    if spec is not None and abs(v - _get_dist(self)) > 1e-6:
        _ops().set_piece_mesh(bpy.context, sel, max(2.0, v))


def _get_elastic(self):
    from .core import elastic as EL
    spec, n, _ = _act()
    r = EL.piece_ratio(spec, n) if spec is not None and n else None
    return 100.0 if r is None else r * 100.0


def _get_thick(self):
    spec, n, _ = _act()
    pc = next((p for p in (spec or {}).get('pieces', []) if p['name'] == n), None)
    return (pc or {}).get('thickness', True) is not False


def _set_thick(self, v):
    spec, n, sel = _act()
    if spec is not None and bool(v) != _get_thick(self):
        _ops().set_piece_thickness(bpy.context, sel, bool(v))


def _get_strengthen(self):
    spec, n, _ = _act()
    return bool(next((p for p in (spec or {}).get('pieces', []) if p['name'] == n), {}).get('strengthen'))


def _set_strengthen(self, v):
    spec, n, sel = _act()
    if spec is not None:
        _ops().set_piece_strengthen(bpy.context, sel, on=bool(v))


def _get_strengthen_factor(self):
    from .core import spec as S
    spec, n, _ = _act()
    return S.piece_strengthen_factor(next((p for p in (spec or {}).get('pieces', []) if p['name'] == n), {}))


def _set_strengthen_factor(self, v):
    spec, n, sel = _act()
    if spec is not None:
        _ops().set_piece_strengthen(bpy.context, sel, factor=v)


def _get_layer(self):
    from .core import spec as S
    spec, n, _ = _act()
    pc = next((p for p in (spec or {}).get('pieces', []) if p['name'] == n), None)
    return S.piece_layer(pc) if pc else 0


def _set_layer(self, v):
    spec, n, sel = _act()
    if spec is not None and int(v) != _get_layer(self):
        _ops().set_piece_layer(bpy.context, sel, int(v))


def _placed(key):
    spec, n, _ = _act()
    return float(((spec or {}).get('placement', {}).get(n) or {}).get(key, 0.0))


def _get_around(self):
    return _placed('theta')


def _set_around(self, v):
    from . import placement as PL
    spec, n, sel = _act()
    if spec is not None and abs(v - _get_around(self)) > 1e-6:
        PL.move_placed(bpy.context, sel, theta=v)


def _ring_of(n, spec):
    """(a band's ring as a share of its own length's, set by hand?): the one set, else the last made (Auto)."""
    from . import dress as D
    k = ((spec or {}).get('placement', {}).get(n) or {}).get('ring')
    return (float(k), True) if k else (float(D.RING_USED.get(n, (1.0, True))[0]), False)


def _get_ring(self):
    spec, n, _ = _act()
    return _ring_of(n, spec)[0]


def _set_ring(self, v):
    from . import placement as PL
    spec, n, sel = _act()
    k, by_hand = _ring_of(n, spec)
    if spec is not None and (not by_hand or abs(v - k) > 1e-6):
        PL.set_ring(bpy.context, sel, k=v)


def _get_closure_over(self):
    from . import store
    spec = store.get(bpy.context)[0]
    return {'left': 1, 'right': 2}.get((spec or {}).get('closure_over'), 0)


def _set_closure_over(self, v):
    # the garment's front closure: which front is on top (closure.py; user 2026-11-02)
    from . import store
    spec = store.get(bpy.context)[0]
    if spec is None or v == _get_closure_over(self):
        return
    sp = {k: x for k, x in spec.items() if k != 'closure_over'}
    if v:
        sp['closure_over'] = ('left', 'right')[v - 1]
    store.put(bpy.context, sp, seams=False)


def _get_ring_auto(self):
    spec, n, _ = _act()
    return not _ring_of(n, spec)[1]


def _set_ring_auto(self, v):
    # on: measured again where the band is now; off: the ring it has now is kept as set by hand
    from . import placement as PL
    spec, n, sel = _act()
    if spec is not None:
        PL.set_ring(bpy.context, sel, k=None if v else _ring_of(n, spec)[0])


def _get_tilt(self):
    return _placed('tilt')


def _set_tilt(self, v):
    from . import placement as PL
    spec, n, sel = _act()
    if spec is not None and abs(PL.norm_tilt(v) - PL.norm_tilt(_get_tilt(self))) > 1e-6:
        PL.set_tilt(bpy.context, sel, deg=v)


def _get_turn(self):
    from .core import body_axes as BA
    from .core import spec as S
    spec, n, _ = _act()
    p = ((spec or {}).get('placement', {}).get(n) or {})
    return BA.flat_turn(p['flat']) if S.is_flat(p) else 0.0


def _set_turn(self, v):
    from . import placement as PL
    spec, n, sel = _act()
    if spec is not None and abs((v - _get_turn(self) + 180.0) % 360.0 - 180.0) > 1e-6:
        PL.set_turn(bpy.context, sel, deg=v)


def _get_height(self):
    return _placed('h') / 1000.0          # a length field (m); the placement's h is mm


def _set_height(self, v):
    from . import placement as PL
    spec, n, sel = _act()
    if spec is not None and abs(v - _get_height(self)) > 1e-9:
        PL.move_placed(bpy.context, sel, h=v * 1000.0)


def _set_elastic(self, v):
    spec, n, sel = _act()
    if spec is not None and abs(v - _get_elastic(self)) > 1e-6:
        _ops().set_piece_elastic(bpy.context, sel, v)


def _fabric_field_items(self, context):
    return _ops()._fabric_items(self, context)


def _bond_field_items(self, context):
    return _ops()._bond_items(self, context)


def _index(items, ident):
    return next((i for i, it in enumerate(items) if it[0] == ident), 0)


def _get_fabric(self):
    spec, n, _ = _act()
    if spec is None:
        return 0
    return _index(_ops()._fabric_items(self, bpy.context), _ops().piece_fabric_item(spec, n))


def _set_fabric(self, i):
    spec, n, sel = _act()
    items = _ops()._fabric_items(self, bpy.context)
    if spec is not None and 0 <= i < len(items) and i != _get_fabric(self):
        _ops().set_piece_fabric(bpy.context, sel, items[i][0])


def _garment_fabric_field_items(self, context):
    return _ops()._garment_fabric_items(self, context)


def _get_garment_fabric(self):
    from .core import fabric as FA
    from . import store
    spec = store.get(bpy.context)[0]
    d = FA.default_fabric(spec) if spec is not None else None
    if d is None:
        return 0
    return _index(_ops()._garment_fabric_items(self, bpy.context), f"id:{d['id']}")


def _set_garment_fabric(self, i):
    from . import store
    items = _ops()._garment_fabric_items(self, bpy.context)
    if store.get(bpy.context)[0] is not None and 0 <= i < len(items) and i != _get_garment_fabric(self):
        _ops().set_garment_fabric(bpy.context, items[i][0])


# the Look fields (Pointer properties have no get / set): written from the spec when the selection changes
# (sync_looks, tool_tab._on_depsgraph); their update writes the spec unless it is that sync writing them
_LOOK_SYNC = {'on': False}


def _set_piece_look(self, context):
    if _LOOK_SYNC['on']:
        return
    spec, n, sel = _act()
    if spec is not None:
        _ops().set_piece_look(context, sel, self.piece_look)


def _set_garment_look(self, context):
    if _LOOK_SYNC['on'] or store_spec() is None:
        return
    _ops().set_garment_look(context, self.garment_look)


def store_spec():
    from . import store
    return store.get(bpy.context)[0]


def sync_looks(context=None):
    """The Look fields from the spec: the active piece's own look, the garment's default."""
    from . import look_apply as LA
    from .core import look as LK
    context = context or bpy.context
    props = getattr(context.scene, 'ac9_cloth_sim', None) if context.scene else None
    if props is None:
        return
    spec, n, _ = _act()
    pm = LA.material(LK.piece_look(spec, n)) if spec is not None and n else None
    dm = LA.material(LK.default_look(spec)) if spec is not None else None
    _LOOK_SYNC['on'] = True
    try:
        if props.piece_look != pm:
            props.piece_look = pm
        if props.garment_look != dm:
            props.garment_look = dm
    finally:
        _LOOK_SYNC['on'] = False


def _thickness_changed(self, context):
    from . import store
    from . import dress as D
    spec = store.get(context)[0]
    if spec is not None:
        g = bpy.data.objects.get(D.garment_name(spec))
        if D.has_result(g):
            D.refresh_thickness(context, g, spec)
    _redraw(self, context)


def _get_bond(self):
    spec, n, _ = _act()
    if spec is None:
        return 0
    return _index(_ops()._bond_items(self, bpy.context), _ops().piece_bond_item(spec, n))


def _set_bond(self, i):
    spec, n, sel = _act()
    items = _ops()._bond_items(self, bpy.context)
    if spec is not None and 0 <= i < len(items) and i != _get_bond(self):
        _ops().set_piece_bond(bpy.context, sel, items[i][0])


class AC9ClothSimProps(PropertyGroup):
    spec_text: PointerProperty(type=bpy.types.Text, name="Spec",
                               description="The garment spec (JSON) this scene uses")
    seams: CollectionProperty(type=AC9ClothSimSeamItem)
    seam_index: IntProperty(name="Seam", default=0, translation_context=CTX, update=_seam_picked)
    seams_selected_only: BoolProperty(name="Selected Pieces Only", default=False,
                                      description="List only the seams on the selected pieces")
    # what the add-on draws over the viewports (the pattern and 3D alike): the master switch and one per kind, in the
    # header's Overlays popover (ui.py, user 2026-10-14; "Sim Overlays" until 2026-11-02). show_overlay is the seams
    # (its old name kept for files)
    show_overlays: BoolProperty(name="Overlays", default=True, update=_redraw,
                                description="Draw the add-on's overlays (seams, piece colors, names, ...)")
    show_overlay: BoolProperty(name="Seams", default=True, translation_context=CTX, update=_redraw,
                               description="The seams, on the pattern and in 3D")
    show_lengths: BoolProperty(name="Seam Lengths", default=True, update=_redraw,
                               description="The selected seam's two lengths and their difference")
    show_notches: BoolProperty(name="Notches", default=True, update=_redraw,
                               description="The selected seam's notches, paired side to side")
    show_piece_colors: BoolProperty(name="Piece Colors", default=True, update=_redraw,
                                    description="The pieces tinted by their state, and the selected pieces' rims")
    # each piece tinted by its fabric (user 2026-10-20: as the seams have their colors; the Lists' Fabrics tab shows
    # each fabric's color). Drawn by the piece colors (piece_look.py), so under their switch
    show_fabric_colors: BoolProperty(name="Fabric Colors", default=False, update=_redraw,
                                     description="Tint each piece by its fabric (the colors are in Lists > "
                                                 "Fabrics)")
    show_names: BoolProperty(name="Piece Names", default=True, update=_redraw)
    # what the add-on found wrong, red on the cloth (diagnose.py; on by default, Fable review b-1, user 2026-11-02)
    show_problems: BoolProperty(name="Problems", default=True, update=_redraw,
                                description="Red where the add-on found something wrong: left inside the body or "
                                            "through other cloth, a run's anomaly, cloth still moving at the end")
    # the cloth colored by its stretch from the pattern (diagnose.py, Fable review 2026-11-02, user 2026-11-02)
    show_stretch: BoolProperty(name="Stretch", default=False, update=_redraw,
                               description="Color the cloth by how far it is stretched from its pattern: yellow "
                                           "from 1.05, red at 1.5, blue where squashed")
    show_guides: BoolProperty(name="Guides", default=True, update=_redraw,
                              description="The body's guide cylinders and placement points")
    # Edit Guides (guide_edit.py): a left / right pair follows the one moved as its mirror image (user 2026-10-14)
    guide_mirror: BoolProperty(name="Mirror", default=True,
                               description="Moving one arm or leg guide moves the other as its mirror image about "
                                           "the body's center")
    # the pattern tools in the left toolbar (user 2026-10-13: a switch at the top of the sidebar, toolbar.py)
    pattern_tools: BoolProperty(name="Pattern Tools", default=False, update=_pattern_tools,
                                description="Show the pattern tools (Edit Pieces, Draw, Edit Seams) in the left "
                                            "toolbar")
    # on by itself the first time the file has a garment (a pattern read in or drawn), user 2026-10-22: from the start
    # in a file that uses Cloth Sim, not in one that only has the add-on installed; turned off, it stays off
    pattern_tools_set: BoolProperty(default=False, options={'HIDDEN'})
    list_tab: EnumProperty(name="List", default='SEAMS', translation_context=CTX,
                           items=[('SEAMS', "Seams", "The seams"), ('PIECES', "Pieces", "The garments and pieces"),
                                  ('FABRICS', "Fabrics", "The garment's fabrics and interlinings"),
                                  ('OBSTACLES', "Collision", "What the garment drapes over besides the body"),
                                  ('TAPES', "Tapes", "The tapes on the body"),
                                  ('FASTENERS', "Fasteners", "The buttons and zippers")])
    show_piece_list: BoolProperty(name="Piece List", default=False,
                                  description="List the current garment's pieces with their states")
    # the usual name (user 2026-10-20; the prop keeps its name: files and tests set mesh_size)
    mesh_size: FloatProperty(name="Particle Distance (mm)", default=6.0, min=2.0, max=30.0,
                             description="Distance between the cloth's points in mm, the triangle size (smaller: "
                                         "finer, slower)")
    # gravity in Live Sim (user 2026-10-23, DECISIONS cloth-sim-phase-live-local-sim-2026-10-23): switched while it
    # runs too (core/recipe.live_params)
    fast_live: BoolProperty(name="AC9 Fast Live (Experimental)", default=False, update=_fast_live_choice,
        description="Live Sim with 2 substeps and a correction in each (a test feature). Static body only, with lower contact damping. Native remains the default")
    # Native 3 is the default Live (user 2026-11-12, 0.2.0); its switch is drawn under Experimental Features only. A
    # garment it does not take runs the usual Live (core/prepare_plan.resolve). Fast Live is hidden everywhere and
    # not used by a run (core/presets.resolve); its name stays (kept in files)
    native_three: BoolProperty(name="Native 3", default=True, update=_native_three_choice,
        description="The faster Live Sim (on by default). Keeps the fabric and time step settings; the body does not move, and Live starts again from the shape on screen. A garment with a piece on a Layer above 0 runs the usual Live")
    # a floor at Z = 0 for every run (user 2026-11-10, DECISIONS cloth-sim-place-beyond-garments-2026-11-10): a
    # static box under Z = 0 added to the obstacles, no object in the scene. A box, not a plane: a one-sided plane
    # let all the cloth through when its normal faced down, a 20 mm box let none through (experiments/
    # placement_beyond_garments_2026-11-10 M3)
    floor: BoolProperty(name="Floor", default=False,
                        description="A floor at Z = 0 the cloth lands on, in every run (nothing is added to the "
                                    "scene). For bags and other items without an avatar")
    live_gravity: BoolProperty(name="Gravity", default=True,
                               description="Live Sim pulls the cloth down by gravity (can be switched while it runs)")
    # the stroke brush in Live / Local Sim (Ctrl+Shift+drag, user 2026-11-02, DECISIONS cloth-sim-live-stroke-2026-11-02):
    # read when a stroke starts and as it moves, so they can be changed while it runs
    stroke_radius: FloatProperty(name="Smooth Radius (mm)", default=40.0, min=5.0, max=150.0,
                                 description="Radius of the smooth brush (Ctrl+Shift+drag while Live Sim runs)")
    # the same in the scene's length unit (a length field over stroke_radius, which stays mm in the file)
    stroke_radius_len: FloatProperty(name="Smooth Radius", min=0.005, max=0.15, precision=1, step=LEN_STEP, subtype='DISTANCE',
                                     unit='LENGTH', get=lambda s: s.stroke_radius / 1000.0,
                                     set=lambda s, v: setattr(s, 'stroke_radius', v * 1000.0), options=set(),
                                     description="Radius of the smooth brush (Ctrl+Shift+drag while Live Sim runs)")
    stroke_strength: FloatProperty(name="Smooth Strength", default=1.0, min=0.05, max=1.0,
                                   description="How hard the smooth brush presses the cloth down and carries it "
                                               "along (the cloth's physics stays on)")
    # ... and in Local Sim, off by default there (the selected pieces folded or shaped in the air)
    local_gravity: BoolProperty(name="Gravity", default=False,
                                description="Local Sim pulls the selected pieces down by gravity (can be switched "
                                            "while it runs)")
    # a pattern edit carried to the dressed garment and the placed shapes at once (on), or left until Update 3D, the
    # switch back on or the next Phase / Live / Local Sim (off: the pattern edits return in 0.1 s instead of up to
    # 1.8 s). User 2026-10-27, DECISIONS cloth-sim-work-screen-layout-2026-10-27 (TASKS 85)
    # off by default (user 2026-11-13): a carry took 1-2 s per pattern edit and a small edit is hardly seen in the
    # 3D before a run anyway; the edits wait for Update 3D or the next run
    follow_edits: BoolProperty(name="3D Follows Edits", default=False,
                               update=lambda self, context: _follow_edits_on(context) if self.follow_edits else None,
                               description="Each pattern edit changes the dressed garment and the placed shapes at "
                                           "once. Off: they wait until Update 3D or the next simulation, and the "
                                           "pattern can be edited without waiting")
    sync_pending: BoolProperty(name="3D Not Updated", default=False,
                               description="Pattern edits made with 3D Follows Edits off that the 3D has not taken")
    # the fabric as measured quantities (user 2026-10-07, core/recipe.py physical): on by default; off = the older
    # look-matched cloth (bending 1.0), kept to compare with what was approved with it
    physical_fabric: BoolProperty(name="Physical Fabric", default=True,
                                  description="Stiffness and weight from the fabric's measured values (off: the "
                                              "older cloth settings)")
    fabric_weight: FloatProperty(name="Weight g/m²", default=276.0, min=20.0, max=1500.0,
                                 description="Fabric weight in g/m² (wool suiting about 250-280, shirting 100-140)")
    fabric_bending: FloatProperty(name="Bending µN·m", default=18.0, min=0.5, max=2000.0,
                                  description="Flexural rigidity in µN·m, as a cantilever (FAST B) measures it "
                                              "(shirting 2.5-13, wool suiting 11-25, fused collars far more). "
                                              "Below about 12 the 6 mm mesh is too coarse: use 4 mm")
    # an option, off by default (user 2026-10-07): the solver watches for a piece drifting, two closed seams pulling
    # at each other and a seam sewn turned over, records each one and marks the first red
    watch: BoolProperty(name="Watch for Anomalies", default=False,
                        description="While dressing, record anomalies (a piece drifting, seams pulling at each other "
                                    "or sewn turned over) and mark the first one red")
    # the settings set Dress / Continue / Live run with (core/presets.py): empty = 'default', what the screen runs.
    # Not on the panel: a test sets it (a set name, or JSON {"base": name, key: value}); the panel shows it when set
    preset: StringProperty(name="Settings", default="",
                           description="Named settings set of a simulation (empty: the default set)")
    # the selected pieces' settings as fields (the functions above)
    # drag steps (2026-11-12, a value jumped under the mouse): degrees 50 (read as 0.5 deg a pixel), shares 1 (0.01);
    # lengths units.LEN_STEP (1 mm a pixel, measured)
    piece_distance: FloatProperty(name="Particle Distance (mm)", min=2.0, max=30.0, precision=1, get=_get_dist,
                                  set=_set_dist, options=set(),
                                  description="Particle distance of the selected pieces in mm (finer for a small "
                                              "part); the arrow beside it gives them back the garment's")
    piece_thickness: BoolProperty(name="Thickness", translation_context=CTX, get=_get_thick, set=_set_thick,
                                  options=set(),
                                  description="Draw the selected pieces with their fabric's thickness (display only). "
                                              "Off for a piece folded flat on itself, where the two layers' "
                                              "thickness runs into each other")
    piece_around: FloatProperty(name="Around", translation_context=CTX, precision=1, step=50,
                                get=_get_around, set=_set_around, options=set(),
                                description="Where the selected placed pieces are round their guide, in degrees "
                                            "(their height kept). Several selected move by as much. Their pairs follow")
    piece_turn: FloatProperty(name="Orientation", translation_context=CTX, min=-180.0, max=180.0, precision=0, step=50,
                              get=_get_turn, set=_set_turn, options=set(),
                              description="How far the selected pieces laid flat are turned on the face they lie on, "
                                          "in degrees: 0 = the pattern's top up the face (on the ground: toward +Y), "
                                          "90 = turned counter-clockwise. Where they lie stays. Their pairs follow")
    piece_tilt: FloatProperty(name="Tilt", translation_context=CTX, min=-180.0, max=180.0, precision=0, step=50,
                              get=_get_tilt, set=_set_tilt, options=set(),
                              description="How far the selected placed pieces are tilted about the line round their "
                                          "guide through their center, in degrees: 90 lays the top flat out, 180 "
                                          "turns them upside down (a collar's outer edge next to the neck). The part "
                                          "tilted out is stretched round. Their pairs follow")
    piece_ring: FloatProperty(name="Ring Size", translation_context=CTX, min=0.5, max=3.0, precision=2, step=1,
                              get=_get_ring, set=_set_ring, options=set(),
                              description="The selected bands' ring as a share of their own length's (1.00: their "
                                          "own length, not stretched). Set by hand it is kept, what is in the body "
                                          "red; Auto widens it until it clears the body where it is now")
    closure_over: EnumProperty(name="Front Over", translation_context=CTX, get=_get_closure_over,
                               set=_set_closure_over, options=set(),
                               items=[('NONE', "None", "Not set: from the buttonholes, else a band's closure is placed as before"),
                                      ('LEFT', "Left", "The wearer's left front on top: a band's end on the left goes "
                                                       "over the other on its placed ring"),
                                      ('RIGHT', "Right", "The wearer's right front on top: a band's end on the right "
                                                         "goes over the other on its placed ring")],
                               description="Which front is on top where the garment closes: a band closed over itself "
                                           "(a collar stand) is placed with that side's end over the other")
    piece_ring_auto: BoolProperty(name="Auto", translation_context=CTX, get=_get_ring_auto, set=_set_ring_auto,
                                  options=set(),
                                  description="Widen the selected bands until they clear the body where they are now "
                                              "(measured again when turned on and when placed)")
    piece_height: FloatProperty(name="Height", translation_context=CTX, precision=1, step=LEN_STEP, subtype='DISTANCE', unit='LENGTH',
                                get=_get_height, set=_set_height, options=set(),
                                description="How high the selected placed pieces are along their guide (their "
                                            "angle kept). Several selected move by as much. Their pairs follow")
    piece_strengthen: BoolProperty(name="Strengthen", translation_context=CTX,
        get=_get_strengthen, set=_set_strengthen,
        description="Multiply the piece's stretch, shear, and bending stiffness until switched off")
    piece_strengthen_factor: FloatProperty(name="Strength Factor", translation_context=CTX,
        min=1.0, max=100.0, get=_get_strengthen_factor, set=_set_strengthen_factor,
        description="Stiffness multiplier; mass and rest shape stay the same")
    piece_layer: IntProperty(name="Layer", translation_context=CTX, min=0, max=9, get=_get_layer, set=_set_layer,
                             options=set(),
                             description="The selected pieces' layer (and their pairs'): while pieces have different "
                                         "layers the simulation keeps the higher one outside the lower one. Set them "
                                         "back to 0 once the garment has settled")
    piece_elastic: FloatProperty(name="Elastic", translation_context=CTX, min=30.0, max=100.0, precision=0, subtype='PERCENTAGE',
                                 get=_get_elastic, set=_set_elastic, options=set(),
                                 description="Rest length of the selected pieces along their long axis, as a share "
                                             "of the pattern's (75 = shortened to 75 %; 100 = not elastic)")
    piece_fabric: EnumProperty(name="Fabric", items=_fabric_field_items, get=_get_fabric, set=_set_fabric,
                               options=set(),
                               description="The selected pieces' fabric (a preset is added to the garment's list)")
    piece_bond: EnumProperty(name="Interlining", items=_bond_field_items, get=_get_bond, set=_set_bond,
                             options=set(),
                             description="The selected pieces' interlining, added to their fabric")
    # the garment's default fabric as a field (Fable UI review 2026-10-21: the AC9 Garment label became a setting)
    garment_fabric: EnumProperty(name="Default Fabric", items=_garment_fabric_field_items, get=_get_garment_fabric,
                                 set=_set_garment_fabric, options=set(),
                                 description="The fabric of every piece without one of its own (a preset is added "
                                             "to the garment's list)")
    # the look (render material) of the selected pieces and the garment's default (user 2026-10-20 / 10-21; core/look.py)
    piece_look: PointerProperty(type=bpy.types.Material, name="Look", update=_set_piece_look, options=set(),
                                description="The selected pieces' look (render material); empty: the garment's "
                                            "default look")
    garment_look: PointerProperty(type=bpy.types.Material, name="Default Look", update=_set_garment_look,
                                  options=set(),
                                  description="The look of every piece without one of its own")
    # the cloth drawn with its fabrics' thickness (a Solidify, display only; user 2026-10-21: one switch for the
    # scene in Overlays, inside by default as Blender's Solidify)
    show_thickness: BoolProperty(name="Thickness", default=True, translation_context=CTX, update=_thickness_changed,
                                 description="Draw the dressed garments with their fabrics' thickness (display only, "
                                             "the simulation does not use it; off takes it away, the values stay)")
    thickness_side: EnumProperty(name="Side", default='INSIDE', update=_thickness_changed,
                                 items=[('INSIDE', "Inside", "The thickness goes inward from the cloth (no grooves "
                                                            "at the seams)"),
                                        ('CENTER', "Center", "Half inward, half outward"),
                                        ('OUTSIDE', "Outside", "The thickness goes outward")],
                                 description="Which side of the cloth the thickness goes")
    dress_status: StringProperty(name="Status")
    # the simulation log's last lines (dress.log, core/simlog.py), drawn under the status
    log_tail: StringProperty(name="Log")
    body: PointerProperty(type=bpy.types.Object, name="Body",
                          description="The avatar's body mesh the garment is placed round and sewn onto",
                          poll=lambda self, ob: ob.type == 'MESH')


CLASSES = (AC9ClothSimSeamItem, AC9ClothSimProps)


@bpy.app.handlers.persistent
def _fast_live_off(*_args):
    """Fast Live is hidden everywhere (user 2026-11-12): a file saved with it on opens with it off and with Native 3
    on (the two were one or the other: the faster Live asked for). Not used by a run either way (core/presets)."""
    for sc in bpy.data.scenes:
        p = getattr(sc, 'ac9_cloth_sim', None)
        if p is not None and p.fast_live:
            p.fast_live = False
            p.native_three = True


def _drop_handler():
    for h in [h for h in bpy.app.handlers.load_post if getattr(h, '__name__', '') == '_fast_live_off']:
        bpy.app.handlers.load_post.remove(h)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ac9_cloth_sim = PointerProperty(type=AC9ClothSimProps)
    _drop_handler()                     # by name: the one of an add-on reloaded with F3 too
    bpy.app.handlers.load_post.append(_fast_live_off)


def unregister():
    _drop_handler()
    del bpy.types.Scene.ac9_cloth_sim
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
