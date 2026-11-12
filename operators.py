# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
from .i18n import CTX, rpt, tx

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import overlay
from . import pattern_view as PV
from . import store
from .core import bom_to_spec
from .core import elastic as EL
from .core import fabric as FA
from .core import roles as RO
from .core import spec as S
from . import selection as SEL


def _tag_redraw(context):
    overlay.invalidate()
    for area in context.screen.areas if context.screen else []:
        if area.type in ('VIEW_3D', 'PROPERTIES'):
            area.tag_redraw()


def _need_spec(context):
    spec, outlines = store.get(context)
    return spec, outlines


def _rebuild_bom_seams(spec, garments=None):
    """Remake the BOM seams of the garments `garments` (ids; None = all), each between its own pieces; the other
    garments' seams and a person's seams stay (core/bom_to_spec.rebuild_seams, DECISIONS
    cloth-sim-bom-seams-per-garment-2026-10-25: switching pieces on and off no longer drops or remakes a seam)."""
    if not S.from_bom(spec):
        return ["not a BOM spec"]
    path = spec['source'].get('path', '')
    if not os.path.exists(path):
        return [tx("BOM not found: {0}", path)]
    return bom_to_spec.rebuild_seams(spec, garments, path)


def _selected_names(context, spec):
    """The selected pattern pieces of this garment (flat or placed objects), with their other halves."""
    from . import placement as PL
    mine = {pc['name'] for pc in spec['pieces']}
    sel = set(PL.selected_pieces(context)) & mine
    return sel | (_other_halves(spec, sel) & mine)


def _garment_from(spec, names):
    """Make the pieces `names` one garment of their own (a new id); the garments they came from keep the rest, an
    emptied one goes. Returns (its id, the garments whose BOM seams are to be remade: None = all, empty = none)."""
    remake_all = False
    if not spec.get('garments'):                  # a spec never split: a BOM's is split now, a drawn one is 1 garment
        if S.from_bom(spec):
            bom_to_spec.assign_garments(spec)
            remake_all = True
        else:
            spec['garments'] = [{'id': 'G1', 'name': tx("Garment {0}", 1), 'source': 'user', 'check': ''}]
            for pc in spec['pieces']:
                pc['garment'] = 'G1'
    for g, ns in bom_to_spec.garment_pieces(spec).items():
        if set(ns) == set(names):
            return g, (None if remake_all else set())
    old = {pc.get('garment', '') for pc in spec['pieces'] if pc['name'] in names}
    ids = {g['id'] for g in spec['garments']}
    k = 1
    while f"G{k}" in ids:
        k += 1
    gid = f"G{k}"
    spec['garments'].append({'id': gid, 'name': tx("Garment {0}", k), 'source': 'user', 'check': ''})
    for pc in spec['pieces']:
        if pc['name'] in names:
            pc['garment'] = gid
    left = {pc.get('garment', '') for pc in spec['pieces']}
    spec['garments'] = [g for g in spec['garments'] if g['id'] in left]
    return gid, (None if remake_all else (old & left) | {gid})


def _replace_bom(context, spec):
    """Stage a new pattern before retiring the active result. Failed display creation restores the old IDs.

    Same-name Text versions may share one result, but must describe the same BOM pieces. External jobs are history,
    never deletion targets. A different or ambiguous owner is refused before anything is unlinked.
    """
    import json
    from . import dress as D, placed_view as PLV, button_view as BV, zipper_view as ZV
    old, _ = store.get(context)
    if old is not None and not S.from_bom(old):
        old = None  # A manually drawn or mesh-derived garment is a separate garment, not a BOM version.
    peers, targets, kept = [], set(), []
    pattern = bpy.data.collections.get(PV.COLL_NAME)
    targets.update(ob for ob in pattern.objects if PV.PIECE_PROP in ob) if pattern else None
    if old:
        def identity(sp):
            return {p['name']: (p.get('bom'), p['edges'], p.get('internals', [])) for p in sp['pieces']}
        ident = identity(old)
        for text in bpy.data.texts:
            try:
                sp = S.loads(text.as_string())
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if sp.get('name') != old['name']:
                continue
            if (not S.from_bom(sp) or sp.get('source') != old.get('source') or identity(sp) != ident):
                raise ValueError(tx('The current 3D result has ambiguous owners; no data was changed'))
            peers.append((text, text.as_string(), sp))
        peer_texts = {text for text, _, _ in peers}
        if any(sc != context.scene and sc.ac9_cloth_sim.spec_text in peer_texts for sc in bpy.data.scenes):
            raise ValueError(tx('A BOM version is used by another scene; no data was changed'))
        g = context.scene.objects.get(D.garment_name(old))
        if g:
            names = json.loads(g.get('ac9_piece_names', '[]'))
            if (g.type != 'MESH' or not names or not set(names) <= set(ident) or
                    g.data.attributes.get('ac9_piece') is None):
                raise ValueError(tx('The current 3D result has ambiguous owners; no data was changed'))
            targets.add(g)
            for name in (BV.points_name(old['name']), ZV.points_name(old['name'])):
                ob = context.scene.objects.get(name)
                if ob:
                    if not any(m.type == 'NODES' and m.node_group for m in ob.modifiers):
                        raise ValueError(tx('The current 3D result has ambiguous owners; no data was changed'))
                    targets.add(ob)
        targets.update(PLV.objects(old).values())
        for me in bpy.data.meshes:
            for prop in (PLV.KEEP_PROP, PLV.BEFORE_SEAM_PROP):
                if me.get(prop) and json.loads(me[prop]).get('garment') == old['name']:
                    if me.users > int(me.use_fake_user):
                        raise ValueError(tx('A saved placed shape is shared; no data was changed'))
                    kept.append(me)
    for ob in targets:
        if any(ob.name in sc.objects and sc != context.scene for sc in bpy.data.scenes):
            raise ValueError(tx('A generated object is shared by scenes; no data was changed'))
    # Retired versions get their own names and no placement, so selecting one cannot recover the discarded result.
    used = {sp.get('name') for _, _, sp in peers}
    for t in bpy.data.texts:
        try:
            used.add(S.loads(t.as_string()).get('name'))
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    base, k = spec['name'], 1
    # Same active BOM may reuse its result name; unrelated existing specs never share a new result.
    if old and base == old['name']:
        used.discard(base)
    while spec['name'] in used or (bpy.data.objects.get(D.garment_name(spec)) not in targets
                                  and bpy.data.objects.get(D.garment_name(spec)) is not None):
        spec['name'] = f'{base}_{k}'
        k += 1
    selected = SEL.objects(context)
    active = context.view_layer.objects.active
    old_text = context.scene.ac9_cloth_sim.spec_text
    props = context.scene.ac9_cloth_sim
    ui_state = {k: getattr(props, k) for k in ('pattern_tools', 'pattern_tools_set', 'dress_status', 'log_tail', 'sync_pending')}
    before_objects = set(bpy.data.objects)
    records = [(ob, ob.name, list(ob.users_collection)) for ob in targets]
    t = None
    try:
        for i, (ob, name, colls) in enumerate(records):
            ob.name = f'__AC9_BOM_previous_{i}'
            for coll in colls:
                coll.objects.unlink(ob)
        t = store.new_text(context, spec['name'])
        store.put(context, spec)
        PV.rebuild(context, spec, store.get(context)[1])
        for text, raw, prior in peers:
            prior['name'] = f"{prior['name']} (previous: {text.name})"
            prior['placement'], prior['guides'] = {}, {}
            text.clear()
            text.write(S.dumps(prior))
    except Exception:
        for text, raw, _ in peers:
            text.clear()
            text.write(raw)
        for ob in set(bpy.data.objects) - before_objects:
            me = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if isinstance(me, bpy.types.Mesh) and me.users == 0:
                bpy.data.meshes.remove(me)
        for ob, name, colls in records:
            ob.name = name
            for coll in colls:
                if ob.name not in coll.objects:
                    coll.objects.link(ob)
        context.scene.ac9_cloth_sim.spec_text = old_text
        if t is not None:
            bpy.data.texts.remove(t)
        store.invalidate()
        store.sync_seam_list(context, old)
        for key, value in ui_state.items():
            setattr(props, key, value)
        for ob in selected:
            ob.select_set(True)
        context.view_layer.objects.active = active
        raise
    # Everything that can parse, build or draw has succeeded. Retire only this result's stored shapes.
    for ob, _, _ in records:
        me = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if isinstance(me, bpy.types.Mesh) and me.users == 0:
            bpy.data.meshes.remove(me)
    for me in set(kept):
        bpy.data.meshes.remove(me)
    props.dress_status = props.log_tail = ''
    props.sync_pending = False
    store.invalidate()
    PLV.invalidate_shown()


class AC9_OT_SimImportBOM(bpy.types.Operator, ImportHelper):
    """Read a new BOM pattern and discard the current garment's 3D result and placement. Use Update BOM Seams to keep the dressed shape"""
    bl_idname = "ac9_cloth_sim.import_bom"
    bl_label = "Import BOM"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".xml"
    filter_glob: StringProperty(default="*.xml", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        from .dress import AC9_OT_SimDress
        return AC9_OT_SimDress.running() is None and context.mode == 'OBJECT'

    def draw(self, context):
        self.layout.label(text='Discards the current 3D and placement', icon='ERROR')
        self.layout.label(text='Keep the drape: use Update BOM Seams')

    def execute(self, context):
        try:
            spec, rep = bom_to_spec.convert(self.filepath)
            if not spec['pieces']:
                raise ValueError(tx('No pattern pieces were found in this BOM'))
            rep += _rebuild_bom_seams(spec)
            _replace_bom(context, spec)
        except Exception as exc:
            self.report({'ERROR'}, rpt("Could not read the BOM: {0}", exc))
            return {'CANCELLED'}
        from . import shape_tool as ST
        ST.set_mm_if_default(context)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0} pieces, {1} seams", len(spec['pieces']), len(spec['seams'])))
        return {'FINISHED'}


class AC9_OT_SimGarmentFromSelection(bpy.types.Operator):
    """Make the selected pattern pieces one garment. Its BOM seams, and those of the garments the pieces came from, are found again within each garment; other garments are not touched"""
    bl_idname = "ac9_cloth_sim.garment_from_selection"
    bl_label = "Garment from Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        sel = _selected_names(context, spec)
        if not sel:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        gid, changed = _garment_from(spec, sel)
        next(g for g in spec['garments'] if g['id'] == gid)['source'] = 'user'
        rep = _rebuild_bom_seams(spec, changed) if S.from_bom(spec) and changed != set() else []
        store.put(context, spec)
        _tag_redraw(context)
        name = next((g['name'] for g in spec['garments'] if g['id'] == gid), gid)
        self.report({'INFO'}, rpt("{0}: {1} pieces", name, len(sel)) + ("; " + rep[-1 if len(rep) == 1 else 0]
                                                                         if rep else ""))
        return {'FINISHED'}


class AC9_OT_SimUseSelectedPieces(bpy.types.Operator):
    """Make the selected pattern pieces one garment and switch the other pieces off (Garment from Selection, then the selected on and the rest off; no other garment's seams change)"""
    bl_idname = "ac9_cloth_sim.use_selected_pieces"
    bl_label = "Use Selected Pieces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        # out of the panel since garments (2026-10-25): it switched every other piece off and dropped their BOM
        # seams; kept for scripts, now without dropping anything
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        sel = _selected_names(context, spec)
        if not sel:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        _gid, changed = _garment_from(spec, sel)
        rep = _rebuild_bom_seams(spec, changed) if S.from_bom(spec) and changed != set() else []
        for pc in spec['pieces']:
            pc['enabled'] = pc['name'] in sel
        store.put(context, spec)
        PV.refresh_enabled(spec)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0} pieces; ", len(sel)) + (rep[0] if rep else ''))
        return {'FINISHED'}


class AC9_OT_SimSelectGarment(bpy.types.Operator):
    """Select this garment's pattern pieces (Shift: add them to the selection), e.g. then switch them on with the state buttons and Invert Selection to switch the rest off"""
    bl_idname = "ac9_cloth_sim.select_garment"
    bl_label = "Select Garment"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    garment: StringProperty(options={'SKIP_SAVE'})
    extend: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        # user 2026-10-25: a garment as a selection set (3ds Max's named selections): pick it, switch it on, invert,
        # switch the rest off. The flat pieces and the placed objects of its pieces, as Invert Selection selects
        from . import placed_view as PLV
        spec, _ = store.get(context)
        names = set(bom_to_spec.garment_pieces(spec, self.garment))
        if not self.extend:
            SEL.deselect_all(context)
        got = []
        for ob in context.view_layer.objects:
            if any(ob.get(k) in names for k in (PV.PIECE_PROP, PLV.PROP, PLV.TWIN_PROP)) and ob.visible_get():
                ob.select_set(True)
                got.append(ob)
        if got:
            context.view_layer.objects.active = got[0]
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0} piece(s) selected", len(names)))
        return {'FINISHED'}


class AC9_OT_SimRenameGarment(bpy.types.Operator):
    """Rename this garment"""
    bl_idname = "ac9_cloth_sim.rename_garment"
    bl_label = "Rename Garment"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    garment: StringProperty(options={'SKIP_SAVE'})
    name: StringProperty(name="Name")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, _ = store.get(context)
        self.name = next((g['name'] for g in spec.get('garments', []) if g['id'] == self.garment), "")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        for g in spec.get('garments', []):
            if g['id'] == self.garment and self.name.strip():
                g['name'] = self.name.strip()
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimEnableAllPieces(bpy.types.Operator):
    """Switch every pattern piece on"""
    bl_idname = "ac9_cloth_sim.enable_all_pieces"
    bl_label = "All Pieces"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        for pc in spec['pieces']:
            pc['enabled'] = True
        store.put(context, spec)
        PV.refresh_enabled(spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimSeamsFromBOM(bpy.types.Operator):
    """Update BOM seams and automatic garment groups; keep the dressed shape, placement and manual settings. Changed patterns are refused"""
    bl_idname = "ac9_cloth_sim.seams_from_bom"
    bl_label = "Update BOM Seams"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        from .dress import AC9_OT_SimDress
        return spec is not None and S.from_bom(spec) and AC9_OT_SimDress.running() is None

    def execute(self, context):
        old, _ = store.get(context)
        text = store.text(context)
        raw = text.as_string()
        try:
            spec, rep = bom_to_spec.refresh_seams(old)
            # Seams alone do not change the mesh or display state. Avoid store.put's display reconciliation.
            text.clear()
            text.write(S.dumps(spec))
            store.invalidate()
            store.sync_seam_list(context, spec)
        except Exception as exc:
            text.clear()
            text.write(raw)
            store.invalidate()
            store.sync_seam_list(context, old)
            self.report({'ERROR'}, rpt('BOM seams were not updated: {0}', exc))
            return {'CANCELLED'}
        _tag_redraw(context)
        self.report({'INFO'}, rep[0] if rep else "done")
        return {'FINISHED'}


class AC9_OT_SimSeamEdit(bpy.types.Operator):
    """Change the active seam"""
    bl_idname = "ac9_cloth_sim.seam_edit"
    bl_label = "Edit Seam"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    action: EnumProperty(items=[('FLIP_A', "Flip A", "Run side A the other way (its start becomes its end)"),
                                ('FLIP_B', "Flip B", "Run side B the other way (its start becomes its end)"),
                                ('CONFIRM', "Confirm", "Mark the seam as checked"),
                                ('TOGGLE', "On/Off", "Sew this seam or not"),
                                ('DELETE', "Delete", "Remove the seam")])

    @classmethod
    def description(cls, context, props):
        return {'FLIP_A': "Run side A the other way (its start becomes its end)",
                'FLIP_B': "Run side B the other way (its start becomes its end)",
                'CONFIRM': "Mark the seam as checked (clears the orange flag)",
                'TOGGLE': "Switch the seam on or off (an off seam is kept but not sewn)",
                'DELETE': "Remove the seam"}[props.action]

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return store.active_seam(context, spec) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        act = store.active_seam(context, spec)
        if self.action in ('FLIP_A', 'FLIP_B'):
            sd = act['a' if self.action == 'FLIP_A' else 'b']
            sd['from'], sd['to'] = sd['to'], sd['from']
            if 'dir' in sd:
                sd['dir'] = -sd['dir']
            if act.get('source') == 'bom':
                act['source'] = 'user'           # a person changed it: keep it on "Seams from BOM"
        elif self.action == 'CONFIRM':
            act['check'] = ''
            if act.get('source') == 'bom':
                act['source'] = 'user'
        elif self.action == 'TOGGLE':                # every picked seam to the active one's other state
            on = not act.get('enabled', True)
            for sm in store.selected_seams(context, spec):
                sm['enabled'] = on
                if sm.get('source') == 'bom':
                    sm['source'] = 'user'
        elif self.action == 'DELETE':
            gone = {sm['id'] for sm in store.selected_seams(context, spec)}
            spec['seams'] = [sm for sm in spec['seams'] if sm['id'] not in gone]
            store.seam_more.clear()
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


PATH_SIDES = [('STRAIGHT', "Straight", "Pull the two sides straight to each other (as before)"),
              ('auto', "Round: Auto", "Pull them round the body part, on the side away from the seam's own cloth "
                                      "(the open side of a wrapped sleeve)"),
              ('under', "Round: Under", "Pull them round the body part, passing under it"),
              ('top', "Round: Top", "Pull them round the body part, passing over it"),
              ('front', "Round: Front", "Pull them round the body part, passing in front of it"),
              ('back', "Round: Back", "Pull them round the body part, passing behind it"),
              ('left', "Round: Left", "Pull them round the body part, passing on the avatar's left of it"),
              ('right', "Round: Right", "Pull them round the body part, passing on the avatar's right of it")]


def _path_guides(self, context):
    spec = store.get(context)[0]
    gs = sorted((spec or {}).get('guides') or {})
    return _keep([('PLACED', "Part: Placed", "The body part the seam's piece is placed on")] +
                 [(g, tx("Part: {0}", g), tx("Go round the {0} guide's axis", g)) for g in gs], owner='path_guides')


class _SeamPathBase:
    """The two operators below (a subclass of a registered operator lost its Python class in Blender 5.2)"""
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    what = 'SIDE'

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return store.active_seam(context, spec) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        act = store.active_seam(context, spec)
        if self.what == 'SIDE':
            if self.side == 'STRAIGHT':
                act.pop('path', None)
            else:
                act['path'] = dict(act.get('path') or {}, side=self.side)
        else:
            if not act.get('path'):
                act['path'] = {'side': 'auto'}
            if self.guide == 'PLACED':
                act['path'].pop('guide', None)
            else:
                act['path']['guide'] = self.guide
        if act.get('source') == 'bom':
            act['source'] = 'user'               # a person changed it: keep it on "Seams from BOM"
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimSeamPath(_SeamPathBase, bpy.types.Operator):
    """How the seam is pulled together while it is sewn: straight, or round a body part on the side chosen (a test
    feature). Round: closed in the stage its pieces come in, not held open for a later one"""
    bl_idname = "ac9_cloth_sim.seam_path"
    bl_label = "Seam Pull"
    side: EnumProperty(name="Pull", items=PATH_SIDES, translation_context=CTX, default='STRAIGHT')
    what = 'SIDE'


class AC9_OT_SimSeamPathPart(_SeamPathBase, bpy.types.Operator):
    """The body part whose axis the seam is pulled round (a test feature)"""
    bl_idname = "ac9_cloth_sim.seam_path_part"
    bl_label = "Seam Pull Part"
    guide: EnumProperty(name="Part", items=_path_guides)
    what = 'GUIDE'


_ATTACH_ITEMS = []


def _attach_tapes(self, context):
    """The tapes on the body (Measure: Tape Around / Along, and the measured girths' lines) and None. Read only when
    the menu opens (the body's lines are a JSON of every point)."""
    import json
    from .core import attach as AT
    ob = context.scene.ac9_cloth_sim.body
    geo = {}
    if ob is not None:
        try:
            geo = json.loads(ob.get('ac9_measure_geo', '{}'))
        except (ValueError, TypeError):
            geo = {}
    from .measure_tool import LABEL
    _ATTACH_ITEMS[:] = [('NONE', "None", "Not attached")] + [
        (n, LABEL.get(n, n), tx("A tape round the body: {0}" if cl else "A tape along the body: {0}", tx(LABEL.get(n, n))))
        for n, cl in AT.tape_names(geo)]
    return _keep(list(_ATTACH_ITEMS), owner='attach_tapes')


class AC9_OT_SimSeamAttach(bpy.types.Operator):
    """Hold the seam to a tape on the body while it is simulated: it is drawn towards the
    tape and can slide along it. Lay the tape with Measure (Tape Around / Along) first"""
    bl_idname = "ac9_cloth_sim.seam_attach"
    bl_label = "Attach to Tape"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    tape: EnumProperty(name="Tape", items=_attach_tapes, translation_context=CTX)

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return store.active_seam(context, spec) is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        act = store.active_seam(context, spec)
        att = [a for a in spec.get('attach', []) if str(a.get('seam')) != str(act['id'])]
        if self.tape != 'NONE':
            old = next((a for a in spec.get('attach', []) if str(a.get('seam')) == str(act['id'])), {})
            att.append({'id': f"A{act['id']}", 'seam': act['id'], 'tape': self.tape, 'until': old.get('until', 'end'),
                        'enabled': True})
        spec['attach'] = att
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimSeamAttachUntil(bpy.types.Operator):
    """How long the seam is held to its tape: to the end, or only while it is sewn (released when gravity comes on)"""
    bl_idname = "ac9_cloth_sim.seam_attach_until"
    bl_label = "Hold to Tape"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    until: EnumProperty(name="Hold", translation_context=CTX, items=[('end', "To the end", "Held in every stage"),
                                            ('sewn', "While sewing", "Released when gravity comes on")])

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        act = store.active_seam(context, spec)
        return act is not None and any(str(a.get('seam')) == str(act['id']) for a in spec.get('attach', []))

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        act = store.active_seam(context, spec)
        for a in spec.get('attach', []):
            if str(a.get('seam')) == str(act['id']):
                a['until'] = self.until
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimSetFold(bpy.types.Operator):
    """Fold angle on an internal line: + folds it as a mountain seen from the pattern's face, - as a valley, 0 removes it"""
    bl_idname = "ac9_cloth_sim.set_fold"
    bl_label = "Fold Angle"
    bl_options = {'REGISTER', 'UNDO'}

    piece: StringProperty(options={'HIDDEN'})
    line: StringProperty(options={'HIDDEN'})
    deg: bpy.props.FloatProperty(name="Angle", default=0.0, min=-180.0, max=180.0, subtype='NONE',
                                 description="+ mountain, - valley, seen from the pattern's face; 0 = no fold")

    def invoke(self, context, event):
        spec, _ = store.get(context)
        f = next((f for f in spec.get('folds', []) if f.get('piece') == self.piece and f.get('line') == self.line), None)
        self.deg = f['deg'] if f else 0.0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        folds = [f for f in spec.get('folds', []) if not (f.get('piece') == self.piece and f.get('line') == self.line)]
        if abs(self.deg) > 1e-6:
            folds.append({'piece': self.piece, 'line': self.line, 'deg': round(self.deg, 2)})
        spec['folds'] = folds
        store.put(context, spec)
        from . import place_fold as PF
        PF.sync_twin(context, self.piece, self.line)          # the twin's line the same (user 2026-10-25)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimPiecesOnOff(bpy.types.Operator):
    """Switch the selected pattern pieces on or off, leaving the others as they are (an off piece is not sewn: add an accessory later and Phase Sim)"""
    bl_idname = "ac9_cloth_sim.pieces_on_off"
    bl_label = "Pieces On / Off"
    bl_options = {'REGISTER', 'UNDO'}

    on: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and bool(SEL.pieces(context))

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        sel = set(SEL.pieces(context))
        for pc in spec['pieces']:
            if pc['name'] in sel:
                pc['enabled'] = bool(self.on)
        store.put(context, spec)
        PV.refresh_enabled(spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimFreeze(bpy.types.Operator):
    """Freeze the selected pattern pieces (they stay where they are while the rest drapes), or unfreeze them"""
    bl_idname = "ac9_cloth_sim.freeze"
    bl_label = "Freeze"
    bl_options = {'REGISTER', 'UNDO'}

    freeze: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None and bool(SEL.pieces(context))

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        sel = set(SEL.pieces(context))
        for pc in spec['pieces']:
            if pc['name'] in sel and pc.get('enabled', True):
                bom_to_spec.S.set_piece_state(pc, 'freeze' if self.freeze else 'active')
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


STATE_ITEMS = [('active', "Active", "Simulated: moves and is touched"),
               ('freeze', "Freeze", "Stays where it is; still touched and sewn"),
               ('off_pattern', "Off (Pattern)", "Stays where it is; nothing touches it (the cloth goes through), its "
                                                "seams still pull"),
               ('off_all', "Off (All)", "Left out with its seams (not meshed)")]


class AC9_OT_SimPieceState(bpy.types.Operator):
    """Set the pieces' state (one of four): the selected pattern pieces, one piece, or a whole garment"""
    bl_idname = "ac9_cloth_sim.piece_state"
    bl_label = "Piece State"
    bl_options = {'REGISTER', 'UNDO'}

    state: bpy.props.EnumProperty(items=STATE_ITEMS, options={'SKIP_SAVE'})
    scope: bpy.props.EnumProperty(items=[('SELECTED', "Selected", ""), ('PIECE', "Piece", ""),
                                         ('GARMENT', "Garment", "")], default='SELECTED', options={'SKIP_SAVE'})
    piece: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    text: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # the garment's spec Text (empty: current)
    solo: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})      # Ctrl: these pieces only, not their pairs

    @classmethod
    def description(cls, context, props):
        d = dict((k, v) for k, _n, v in STATE_ITEMS).get(props.state, '')
        return {'SELECTED': tx("The selected pieces with their other halves (Ctrl: these only): {0}", d),
                'PIECE': tx("This piece with its other half (Ctrl: this one only): {0}", d),
                'GARMENT': tx("Every piece of this garment that is not Off (All): {0}", d)}[props.scope]

    def invoke(self, context, event):
        self.solo = event.ctrl
        return self.execute(context)

    def execute(self, context):
        t = bpy.data.texts.get(self.text) if self.text else store.text(context)
        if t is None:
            return {'CANCELLED'}
        current = t == store.text(context)
        spec = bom_to_spec.S.loads(t.as_string())
        if self.scope == 'GARMENT':
            # a garment's pieces that are Off (All) stay so: a BOM keeps every piece of the file, and a garment's
            # Freeze brought the 125 unused pieces of a jacket back (GUI test 2026-10-13)
            names = {pc['name'] for pc in spec['pieces'] if bom_to_spec.S.piece_state(pc) != 'off_all'}
        else:
            names = ({self.piece} if self.scope == 'PIECE' else
                     set(SEL.pieces(context)))
            if not names:
                self.report({'WARNING'}, "Select pattern pieces in the viewport first")
                return {'CANCELLED'}
            if not self.solo:
                names |= _other_halves(spec, names)
        was_off = {pc['name'] for pc in spec['pieces'] if bom_to_spec.S.piece_state(pc) == 'off_all'}
        n = 0
        for pc in spec['pieces']:
            if pc['name'] in names and bom_to_spec.S.piece_state(pc) != self.state:
                bom_to_spec.S.set_piece_state(pc, self.state)
                n += 1
        matched = _match_back(spec, names & was_off) if self.state != 'off_all' else []
        if current:
            store.put(context, spec)
            PV.refresh_enabled(spec)
            # Geometry ownership and visibility are reconciled together below.
            # A state toggle must not rebuild (or discard) an existing hand shape.
        else:
            t.clear()
            t.write(bom_to_spec.S.dumps(spec))
            store.invalidate()
            from . import display_state
            display_state.apply(context, spec)
        _tag_redraw(context)
        msg = rpt("{0} piece(s) now {1}", n, dict((k, v) for k, v, _d in STATE_ITEMS)[self.state])
        from . import display_state
        previous_text = store.text(context)
        try:
            if not current:
                context.scene.ac9_cloth_sim.spec_text = t
                store.invalidate()
            needed = {n for n, v in display_state.snapshot(store.get(context)[0]).items() if v['missing']}
            for line in display_state.apply(context, store.get(context)[0], create_missing=True):
                self.report({'WARNING'}, line)
            remaining = {n for n, v in display_state.snapshot(store.get(context)[0]).items() if v['missing']}
            created = len(needed - remaining)
            if created:
                msg += rpt("; {0} placed shape(s) created", created)
        finally:
            if not current:
                context.scene.ac9_cloth_sim.spec_text = previous_text
                store.invalidate()
        if matched:
            msg += rpt("; {0} made the mirror image of the other half again", ", ".join(matched))
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_SimInvertPieceSelection(bpy.types.Operator):
    """Select the other pattern pieces of this garment instead (a pair counts as one: a piece selected on one side leaves both halves out), e.g. to turn all the rest off with the state buttons"""
    bl_idname = "ac9_cloth_sim.invert_piece_selection"
    bl_label = "Invert Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        # user 2026-10-25: a button, not Blender's Ctrl+I (that inverts the body and the guides too). The pieces of
        # this garment only; the pattern pieces end up selected wherever the selection was (the flat pieces or the
        # placed objects), as the state buttons act on them; Off (All) pieces are pieces too
        from . import placement as PL
        spec, _ = store.get(context)
        mine = [pc['name'] for pc in spec['pieces']]
        sel = set(PL.selected_pieces(context)) & set(mine)
        sel |= _other_halves(spec, sel)
        obs = PV.piece_objects()
        want = [n for n in mine if n not in sel and n in obs and obs[n].visible_get()]
        SEL.deselect_all(context)
        for n in want:
            obs[n].select_set(True)
        if want:
            context.view_layer.objects.active = obs[want[0]]
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0} piece(s) selected", len(want)))
        return {'FINISHED'}


def _other_halves(spec, names):
    """The other halves of the pieces `names`: their mirrored twins and the pieces paired with them (Pair), whatever
    their state -- a pair switches together, back from Off (All) too (user 2026-10-24: the twin came along to Off
    but not back on, as twins() leaves out the pieces that are off)."""
    S = bom_to_spec.S
    out = set()
    for a, b in S.twins(spec, any_state=True) + [tuple(p[:2]) for p in spec.get('pairs', [])]:
        if a in names or b in names:
            out |= {a, b}
    return out - set(names)


def _match_back(spec, back):
    """Pieces back from Off (All) whose mirrored twin stayed on and was edited meanwhile (no longer its mirror
    image): made the twin's mirror image again, as an edit made on one twin makes on the other. Left as it is when
    both came back (which one is right is not known). Returns the pieces made so."""
    from .core import draft as D
    S = bom_to_spec.S
    out = []
    for a, b in S.twins(spec):
        if (a in back) == (b in back):
            continue
        lead, gone = (b, a) if a in back else (a, b)
        try:
            if D.twin_corner_map(spec, lead)[1] is None:
                D.match_twin(spec, lead)
                out.append(gone)
        except (ValueError, KeyError, IndexError):
            pass
    return out


class AC9_OT_SimSelectPiece(bpy.types.Operator):
    """Select this piece in the viewport (Shift: add it to the selection); its settings show in the Properties
    editor's Tool tab"""
    bl_idname = "ac9_cloth_sim.select_piece"
    bl_label = "Select Piece"
    bl_options = {'REGISTER', 'UNDO'}

    piece: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        ob = next((o for o in context.view_layer.objects if o.get(PV.PIECE_PROP) == self.piece), None)
        if ob is None:
            self.report({'WARNING'}, rpt("{0}: not in the viewport", self.piece))
            return {'CANCELLED'}
        if not self.extend:
            for o in context.view_layer.objects.selected:
                o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimSetUnits(bpy.types.Operator):
    """Show every length in this unit: the pattern, the measurements and the fields (the particle distance stays mm). Only how lengths are shown; sizes stay"""
    bl_idname = "ac9_cloth_sim.set_units"
    bl_label = "Units"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    unit: bpy.props.EnumProperty(items=[('MM', "mm", ""), ('CM', "cm", ""), ('M', "m", ""), ('IN', "in", "")],
                                 options={'SKIP_SAVE'})

    def execute(self, context):
        from . import units
        units.set_unit(context, self.unit)
        for a in context.screen.areas if context.screen else ():
            a.tag_redraw()
        return {'FINISHED'}


class AC9_OT_SimRefresh(bpy.types.Operator):
    """Re-read the spec text (after editing the JSON by hand) and rebuild the pattern objects"""
    bl_idname = "ac9_cloth_sim.refresh"
    bl_label = "Reload Spec"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.text(context) is not None

    def execute(self, context):
        spec, outlines = store.get(context)
        if spec is None:
            self.report({'ERROR'}, rpt("The spec text is not valid: {0}", store.error()))
            return {'CANCELLED'}
        store.sync_seam_list(context, spec)
        PV.rebuild(context, spec, outlines)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimPieceMesh(bpy.types.Operator):
    """Mesh the selected pattern pieces at their own size (finer for a small part, a particle distance per
    piece); 0 gives them back the Mesh Size. A mirrored pair takes the finer of its two. Used at the next Dress or
    Continue, and written in the simulation log"""
    bl_idname = "ac9_cloth_sim.piece_mesh"
    bl_label = "Piece Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    mesh_mm: FloatProperty(name="Mesh mm", default=0.0, min=0.0, max=30.0,
                           description="Particle distance of the selected pieces in mm (0: the garment's)")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, _ = store.get(context)
        sel = set(SEL.pieces(context))
        cur = [float(pc.get('mesh_mm') or 0.0) for pc in spec['pieces'] if pc['name'] in sel]
        if cur:
            self.mesh_mm = cur[0]
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        sel = SEL.pieces(context)
        self.layout.label(text=tx("{0} selected piece(s)", len(sel)))
        self.layout.prop(self, "mesh_mm")

    def execute(self, context):
        msg = set_piece_mesh(context, _sel_pieces(context), self.mesh_mm)
        if msg is None:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AC9_OT_SimPieceElastic(bpy.types.Operator):
    """Make the selected pattern pieces elastic: the cloth's rest length along the piece's long axis becomes this
    share of the pattern's (a waistband, a cuff, a hem band); 100 % takes it off. A mirrored pair takes it together.
    Used at the next Phase Sim (spec elastic, core/elastic.py)"""
    bl_idname = "ac9_cloth_sim.piece_elastic"
    bl_label = "Piece Elastic"
    bl_options = {'REGISTER', 'UNDO'}

    percent: FloatProperty(name="Elastic %", default=100.0, min=30.0, max=100.0, precision=0,
                           description="Rest length of the selected pieces along their long axis, as a share of the "
                                       "pattern's (75 = shortened to 75 %; 100 = not elastic)")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, _ = store.get(context)
        sel = set(SEL.pieces(context))
        # what the panel shows for the active piece (its own setting, else its twin's)
        act = context.active_object
        name = act[PV.PIECE_PROP] if act is not None and PV.PIECE_PROP in act else (sorted(sel)[0] if sel else None)
        cur = EL.piece_ratio(spec, name) if name else None
        self.percent = round(cur * 100.0, 1) if cur is not None else 75.0
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        sel = SEL.pieces(context)
        self.layout.label(text=tx("{0} selected piece(s)", len(sel)))
        self.layout.prop(self, "percent")

    def execute(self, context):
        msg = set_piece_elastic(context, _sel_pieces(context), self.percent)
        if msg is None:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def set_piece_mesh(context, sel, mm):
    """The particle distance of pieces `sel` (mm; 0: back to the garment's). The message, None when nothing is
    selected. Shared by Piece Mesh and the Tool tab's Distance field (properties.py)."""
    if not sel:
        return None
    spec, _ = store.get(context)
    spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
    for pc in spec['pieces']:
        if pc['name'] in sel:
            if mm > 0:
                pc['mesh_mm'] = round(float(mm), 3)
            else:
                pc.pop('mesh_mm', None)
    store.put(context, spec)
    _tag_redraw(context)
    return tx("{0} piece(s): ", len(sel)) + (tx("distance {0:g} mm", mm) if mm > 0 else tx("the garment's distance"))


def set_piece_thickness(context, sel, on):
    """Pieces `sel` drawn with their fabric's thickness or without it (spec pieces[].thickness False; user
    2026-10-23: a piece folded flat on itself, chosen by hand -- finding them by themselves comes later). Display
    only. The message, None when nothing is selected."""
    if not sel:
        return None
    spec, _ = store.get(context)
    spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
    for pc in spec['pieces']:
        if pc['name'] in sel:
            if on:
                pc.pop('thickness', None)
            else:
                pc['thickness'] = False
    store.put(context, spec)
    from . import dress as D
    g = bpy.data.objects.get(D.garment_name(spec))
    if g is not None:
        D.refresh_thickness(context, g, spec)
    _tag_redraw(context)
    return tx("{0} piece(s): ", len(sel)) + (tx("thickness on") if on else tx("no thickness"))


def set_piece_strengthen(context, sel, *, on=None, factor=None):
    """Set selected pieces and their pairs; retain the multiplier when switched off."""
    if not sel:
        return
    import math
    S = bom_to_spec.S
    if factor is not None and (not math.isfinite(float(factor)) or not 1.0 <= float(factor) <= S.STRENGTHEN_MAX):
        raise ValueError('AC9 Strengthen: multiplier must be finite and between 1 and 100')
    spec, _ = store.get(context)
    spec = S.loads(S.dumps(spec))
    both = set(sel) | _other_halves(spec, sel)
    for pc in spec['pieces']:
        if pc['name'] in both:
            if on is not None:
                pc['strengthen'] = bool(on)
            if factor is not None:
                pc['strengthen_factor'] = S.piece_strengthen_factor({'strengthen_factor': factor})
    store.put(context, spec, seams=False)
    _tag_redraw(context)


def set_piece_layer(context, sel, k):
    """Pieces `sel` and their pairs on layer `k` (spec pieces[].layer, 0 = not stored; user 2026-10-25,
    DECISIONS on layers 2026-10-25). The message, None when nothing is selected."""
    if not sel:
        return None
    spec, _ = store.get(context)
    spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
    tw = {}
    for a_, b_ in bom_to_spec.S.twins(spec):
        tw[a_], tw[b_] = b_, a_
    both = set(sel) | {tw[n] for n in sel if n in tw}
    for pc in spec['pieces']:
        if pc['name'] in both:
            if k:
                pc['layer'] = int(k)
            else:
                pc.pop('layer', None)
    store.put(context, spec, seams=False)
    _tag_redraw(context)
    return tx("{0} piece(s): layer {1}", len(both), int(k))


class AC9_OT_SimLayersToZero(bpy.types.Operator):
    """Set every piece's layer back to 0 (once the garment has settled; nothing turns the layers off by itself)"""
    bl_idname = "ac9_cloth_sim.layers_to_zero"
    bl_label = "All Layers to 0"
    bl_translation_context = CTX
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        spec = store.get(context)[0]
        return spec is not None and any(bom_to_spec.S.piece_layer(p) for p in spec.get('pieces', []))

    def execute(self, context):
        spec, _ = store.get(context)
        names = [p['name'] for p in spec['pieces'] if bom_to_spec.S.piece_layer(p)]
        set_piece_layer(context, set(names), 0)
        self.report({'INFO'}, tx("{0} piece(s): layer 0", len(names)))
        return {'FINISHED'}


def set_piece_elastic(context, sel, percent):
    """Pieces `sel` elastic at `percent` of the pattern's length (100: not elastic). The message, None when nothing
    is selected. Shared by Piece Elastic and the Tool tab's Elastic field."""
    if not sel:
        return None
    spec, _ = store.get(context)
    spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
    # a mirrored pair is one setting: drop the whole-piece elastic of the selected pieces and of their twins
    tw = {}
    for a_, b_ in bom_to_spec.S.twins(spec):
        tw[a_], tw[b_] = b_, a_
    both = sel | {tw[n] for n in sel if n in tw}
    el = [e for e in spec.get('elastic', []) if not (e.get('piece') in both and not e.get('line')
                                                      and not e.get('side'))]
    r = round(float(percent) / 100.0, 4)
    if r < 1.0:
        done = set()
        for n in sorted(sel):
            if n in done:
                continue
            el.append({'piece': n, 'ratio': r})
            done |= {n, tw.get(n)}
    if el:
        spec['elastic'] = el
    else:
        spec.pop('elastic', None)
    store.put(context, spec)
    _tag_redraw(context)
    return tx("{0} piece(s): ", len(sel)) + (tx("elastic {0:g} %", percent) if r < 1.0 else tx("not elastic"))


class AC9_OT_SimProposeRoles(bpy.types.Operator):
    """Propose every piece's role (front, placket, collar stand, cuff ...) from the pattern's shape and the source's hints. Roles you set yourself are kept; the uncertain ones are marked to check"""
    bl_idname = "ac9_cloth_sim.propose_roles"
    bl_label = "Propose Roles"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        prop = RO.propose(spec)
        done = RO.apply(spec, prop)
        store.put(context, spec)
        _tag_redraw(context)
        ask = [n for n in done if prop['pieces'][n]['ask']]
        kept = len([n for n in prop['pieces'] if n not in done])
        self.report({'INFO'}, rpt("{0} roles proposed, {1} to check", len(done), len(ask)) + (rpt(", {0} of yours kept", kept) if kept else ""))
        return {'FINISHED'}


def _role_items(self, context):
    return [(k, RO.EN[k], RO.VOCAB[k][0]) for k in RO.VOCAB]


class AC9_OT_SimSetRole(bpy.types.Operator):
    """Set the role of the selected pattern pieces (and their mirrored twins)"""
    bl_idname = "ac9_cloth_sim.set_role"
    bl_label = "Set Role"
    bl_options = {'REGISTER', 'UNDO'}

    role: EnumProperty(name="Role", items=_role_items, translation_context=CTX)
    piece: StringProperty(options={'HIDDEN'}, description="one piece by name instead of the selection")
    by: EnumProperty(items=[('user', "Person", ""), ('ai', "AI companion", "")], default='user', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        names = {p['name'] for p in spec['pieces']}
        sel = [self.piece] if self.piece else SEL.pieces(context)
        sel = [n for n in sel if n in names]
        if not sel:
            self.report({'WARNING'}, "Select pattern pieces first")
            return {'CANCELLED'}
        done = []
        for n in sel:
            done += RO.set_role(spec, n, self.role, by=self.by)
        store.put(context, spec)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0}: {1}", tx(RO.EN[self.role]), ', '.join(dict.fromkeys(done))))
        return {'FINISHED'}


class AC9_OT_SimSetStart(bpy.types.Operator):
    """How the selected pattern pieces (and their mirrored twins) start against the pieces they are sewn to. As Placed: from where they were placed. On Edge: Phase Sim lays them on the sewn edge after the body is sewn (placed, they stay a ring)"""
    bl_idname = "ac9_cloth_sim.set_start"
    bl_label = "Set Start"
    bl_options = {'REGISTER', 'UNDO'}

    start: EnumProperty(name="Start Shape", items=[(k, RO.START_EN[k], "") for k in RO.START], translation_context=CTX)
    piece: StringProperty(options={'HIDDEN'}, description="one piece by name instead of the selection")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec, _ = store.get(context)
        spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
        names = {p['name'] for p in spec['pieces']}
        sel = [self.piece] if self.piece else SEL.pieces(context)
        sel = [n for n in sel if n in names]
        if not sel:
            self.report({'WARNING'}, "Select pattern pieces first")
            return {'CANCELLED'}
        done = []
        for n in sel:
            done += RO.set_start(spec, n, self.start)
        store.put(context, spec)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0}: {1}", tx(RO.START_EN[self.start]), ', '.join(dict.fromkeys(done))))
        return {'FINISHED'}


_enum_properties = sys.modules.get(__package__ + '.properties')
_enum_users_live = any(getattr(cls, 'is_registered', False) for cls in
                       (*globals().get('CLASSES', ()), *getattr(_enum_properties, 'CLASSES', ())))
_previous_enum_lists = globals().get('_ENUM_KEEP', {})
_ENUM_KEEP = ({key: value for key, value in _previous_enum_lists.items() if isinstance(key, str)}
              if _enum_users_live else {})  # one current list per logical RNA field
_ENUM_STRINGS = globals().get('_ENUM_STRINGS', {}) if _enum_users_live else {}
if _enum_users_live:
    # Upgrade the older value-keyed list table without freeing strings still borrowed by registered old RNA.
    for _enum_items in _previous_enum_lists.values():
        for _enum_item in _enum_items:
            for _enum_value in _enum_item or ():
                if isinstance(_enum_value, str):_ENUM_STRINGS.setdefault(_enum_value, _enum_value)
del _previous_enum_lists, _enum_properties


def _keep(items, owner='compat'):
    """Keep RNA's strings alive across list changes; retain only each field's current list.

    Historical strings can still be referenced by Blender, so they are released only after both the operator and
    property RNA classes are gone. List generations are not themselves lifetime owners of those strings.
    """
    stable = [tuple(_ENUM_STRINGS.setdefault(value, value) if isinstance(value, str) else value
                    for value in item) if item is not None else None for item in items]
    previous = _ENUM_KEEP.get(owner)
    if previous != stable:
        _ENUM_KEEP[owner] = stable
    return _ENUM_KEEP[owner]


def release_enum_strings():
    """Called only after every RNA class consuming these callbacks has been unregistered."""
    _ENUM_KEEP.clear()
    _ENUM_STRINGS.clear()


def _sel_pieces(context):
    return set(SEL.pieces(context))


def _active_piece(context, sel):
    # the piece the Tool tab shows (pattern_view.lead_piece): an active object left outside a box select is not it
    return PV.lead_piece(context, sel) if sel else None


def _fabric_spec(context):
    """A copy of the scene's spec to change, in the 2026-10-20 shape (core/fabric.migrate: a garment saved before
    gets its default from the old Dress panel's Weight / Bending)."""
    spec, _ = store.get(context)
    spec = bom_to_spec.S.loads(bom_to_spec.S.dumps(spec))
    props = context.scene.ac9_cloth_sim
    FA.migrate(spec, props.fabric_weight, props.fabric_bending)
    return spec


def _fabric_items(self, context):
    spec, _ = store.get(context)
    d = FA.default_fabric(spec or {})
    items = [('DEFAULT', tx("Default ({0})", d['name']) if d else "Default",
              "Follow the garment's default fabric (it follows when the default changes)")]
    have = set()
    for f in (spec or {}).get('fabrics', []):
        items.append((f"id:{f['id']}", f['name'], f"{f['weight_gsm']:g} g/m², {FA.bending(f):g} µN·m"))
        if f.get('preset'):
            have.add(f['preset'])
    for p in FA.presets()['fabrics']:
        if p['preset'] not in have:
            items.append((f"preset:{p['preset']}", tx("{0} (preset)", p['name']),
                          f"{p['weight_gsm']:g} g/m², {FA.bending(p):g} µN·m. {p.get('source', '')}"))
    return _keep(items, owner='fabric')


def _bond_items(self, context):
    """Off, this garment's interlinings, the presets not in its list yet (picked: added to it), as _fabric_items."""
    spec, _ = store.get(context)
    items = [('OFF', "Off", "No interlining")]
    have = set()
    for i in (spec or {}).get('interlinings', []):
        items.append((f"id:{i['id']}", i['name'], f"+{i['weight_gsm']:g} g/m², +{i['bending_uNm']:g} µN·m"))
        if i.get('preset'):
            have.add(i['preset'])
    for p in FA.presets()['interlinings']:
        if p['preset'] not in have:
            items.append((f"preset:{p['preset']}", tx("{0} (preset)", p['name']),
                          f"+{p['weight_gsm']:g} g/m², +{p['bending_uNm']:g} µN·m. {p.get('source', '')}"))
    return _keep(items, owner='bond')


class AC9_OT_SimPieceFabric(bpy.types.Operator):
    """The fabric of the selected pattern pieces (a mirrored pair takes it together): one of this garment's fabrics,
    a preset (added to the garment's list), or the garment's default. Used at the next Phase Sim"""
    bl_idname = "ac9_cloth_sim.piece_fabric"
    bl_label = "Piece Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    fabric: EnumProperty(name="Fabric", items=_fabric_items)

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, _ = store.get(context)
        name = _active_piece(context, _sel_pieces(context))
        f = FA.piece_fabric(spec, name) if name else None
        self.fabric = f"id:{f['id']}" if f else 'DEFAULT'
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.label(text=tx("{0} selected piece(s)", len(_sel_pieces(context))))
        self.layout.prop(self, "fabric")

    def execute(self, context):
        msg = set_piece_fabric(context, _sel_pieces(context), self.fabric)
        if msg is None:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def set_piece_fabric(context, sel, v):
    """The fabric of pieces `sel`: v an item of _fabric_items ('DEFAULT', 'id:<id>', 'preset:<preset>', the preset
    then added to the list). The message, None when nothing is selected. Shared by Piece Fabric and the Tool tab's
    Fabric field."""
    if not sel:
        return None
    spec = _fabric_spec(context)
    fid = None if v == 'DEFAULT' else (FA.add_from_preset(spec, v[7:]) if v.startswith('preset:') else v[3:])
    done = FA.set_pieces(spec, sel, fabric_id=fid)
    store.put(context, spec)
    refresh_display(context, spec)
    _tag_redraw(context)
    nm = FA.fabric_index(spec)[fid]['name'] if fid else tx("the default ({0})", FA.default_fabric(spec)['name'])
    return tx("{0} piece(s): {1}", len(done), nm)


def refresh_display(context, spec=None):
    """After a fabric or a look changed: the garment's drawn thickness and every object's looks (dress.py /
    look_apply.py), so what is shown follows at once."""
    from . import dress as D
    from . import look_apply as LA
    spec = spec if spec is not None else store.get(context)[0]
    if spec is None:
        return
    g = bpy.data.objects.get(D.garment_name(spec))
    if D.has_result(g):
        D.refresh_thickness(context, g, spec)
    LA.apply_all(context, spec)


def set_piece_look(context, sel, mat):
    """The look of pieces `sel` (and their twins): material `mat`, None = the garment's default. The message, None
    when nothing is selected. The Tool tab's Look field."""
    from . import look_apply as LA
    from .core import look as LK
    if not sel:
        return None
    spec = _fabric_spec(context)
    done = LK.set_pieces(spec, sel, LA.ref(mat))
    store.put(context, spec)
    refresh_display(context, spec)
    _tag_redraw(context)
    return tx("{0} piece(s): ", len(done)) + (tx("look {0}", mat.name) if mat else tx("the default look"))


def set_garment_look(context, mat):
    """The garment's default look (None = none). A default no object may hold (every piece has its own) is kept
    with a fake user (Fable UI review 2026-10-21)."""
    from . import look_apply as LA
    from .core import look as LK
    spec = _fabric_spec(context)
    LK.set_default(spec, LA.ref(mat))
    if mat is not None:
        mat.use_fake_user = True
    store.put(context, spec)
    refresh_display(context, spec)
    _tag_redraw(context)


def _garment_fabric_items(self, context):
    """_fabric_items without 'DEFAULT': the choices of the garment's default fabric."""
    items = [it for it in _fabric_items(self, context) if it[0] != 'DEFAULT']
    return _keep(items, owner='garment_fabric')


def set_garment_fabric(context, v):
    """The garment's default fabric: v an item of _garment_fabric_items ('id:<id>', or 'preset:<preset>' added to
    the list first). Shared by the AC9 Garment field and the list's Default button."""
    spec = _fabric_spec(context)
    fid = FA.add_from_preset(spec, v[7:]) if v.startswith('preset:') else v[3:]
    FA.set_default(spec, fid)
    store.put(context, spec)
    refresh_display(context, spec)
    _tag_redraw(context)
    return tx("Default: {0}", FA.default_fabric(spec)['name'])


def piece_fabric_item(spec, name):
    """The _fabric_items identifier piece `name` shows: its own fabric, else 'DEFAULT'."""
    f = FA.piece_fabric(spec, name) if name else None
    d = FA.default_fabric(spec)
    return f"id:{f['id']}" if f and not (d and f['id'] == d['id']) else 'DEFAULT'


class AC9_OT_SimPieceInterlining(bpy.types.Operator):
    """The interlining of the selected pattern pieces (a collar, a stand, a cuff): its weight and bending are added
    to the piece's fabric. A mirrored pair takes it together. Used at the next Phase Sim"""
    bl_idname = "ac9_cloth_sim.piece_interlining"
    bl_label = "Piece Interlining"
    bl_options = {'REGISTER', 'UNDO'}

    bond: EnumProperty(name="Interlining", items=_bond_items)

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec, _ = store.get(context)
        name = _active_piece(context, _sel_pieces(context))
        self.bond = piece_bond_item(spec, name)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.label(text=tx("{0} selected piece(s)", len(_sel_pieces(context))))
        self.layout.prop(self, "bond")

    def execute(self, context):
        msg = set_piece_bond(context, _sel_pieces(context), self.bond)
        if msg is None:
            self.report({'WARNING'}, "Select pattern pieces in the viewport first")
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def set_piece_bond(context, sel, v):
    """The interlining of pieces `sel`: v an item of _bond_items ('OFF', 'id:<id>', 'preset:<preset>', the preset
    then added to the garment's list). The message, None when nothing is selected. Shared by Piece Interlining and
    the Tool tab's Interlining field."""
    if not sel:
        return None
    spec = _fabric_spec(context)
    iid = None if v == 'OFF' else (FA.add_interlining_from_preset(spec, v[7:]) if v.startswith('preset:') else v[3:])
    done = FA.set_pieces(spec, sel, bond=iid, what='bond')
    store.put(context, spec)
    _tag_redraw(context)
    nm = FA.interlining_index(spec)[iid]['name'] if iid else None
    return tx("{0} piece(s): ", len(done)) + (tx("interlining {0}", nm) if nm else tx("no interlining"))


def piece_bond_item(spec, name):
    """The _bond_items identifier piece `name` shows: 'id:<id>', 'OFF'; a spec not yet migrated (the interlining
    held on the piece) shows its preset item."""
    b = FA.piece_bond(spec, name) if name else None
    if b is None:
        return 'OFF'
    if b.get('id'):
        return f"id:{b['id']}"
    return f"preset:{b['preset']}" if b.get('preset') and FA.preset('interlinings', b['preset']) else 'OFF'


class AC9_OT_SimFabricAdd(bpy.types.Operator):
    """Add a preset fabric to this garment's list (its numbers are copied in: a later preset change leaves it as it is)"""
    bl_idname = "ac9_cloth_sim.fabric_add"
    bl_label = "Add Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    preset: StringProperty()

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec = _fabric_spec(context)
        try:
            FA.add_from_preset(spec, self.preset)
        except KeyError:
            self.report({'WARNING'}, rpt("No preset {0!r}", self.preset))
            return {'CANCELLED'}
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimFabricRemove(bpy.types.Operator):
    """Take this fabric off the garment's list; its pieces go to the default. The default stays (make another one
    the default first)"""
    bl_idname = "ac9_cloth_sim.fabric_remove"
    bl_label = "Remove Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    fabric_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # set by the button

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec = _fabric_spec(context)
        try:
            FA.remove(spec, self.fabric_id)
        except ValueError as e:
            self.report({'WARNING'}, rpt("Not removed: {0}", e))
            return {'CANCELLED'}
        store.put(context, spec)
        refresh_display(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimFabricDefault(bpy.types.Operator):
    """Make this fabric the garment's default: every piece without a fabric of its own takes it at the next
    Phase Sim"""
    bl_idname = "ac9_cloth_sim.fabric_default"
    bl_label = "Default Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    fabric_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # set by the button

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec = _fabric_spec(context)
        try:
            FA.set_default(spec, self.fabric_id)
        except KeyError:
            self.report({'WARNING'}, rpt("No fabric {0!r}", self.fabric_id))
            return {'CANCELLED'}
        store.put(context, spec)
        refresh_display(context, spec)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("Default: {0}", FA.default_fabric(spec)['name']))
        return {'FINISHED'}


def _select_pieces(context, names, extend):
    """Select the piece objects of `names` (extend: add to the selection), the first one active. How many, None
    when none is in the viewport (the selection then left as it was)."""
    from . import properties as P
    obs = [o for o in context.view_layer.objects if o.get(PV.PIECE_PROP) in names]
    if not obs:
        return None
    if not extend:
        for o in context.view_layer.objects.selected:
            o.select_set(False)
    n = 0
    for o in obs:
        try:
            o.select_set(True)
            n += 1
        except RuntimeError:                          # hidden from the view layer
            pass
    context.view_layer.objects.active = obs[0]
    try:
        P.sync_looks()                                # what tool_tab._on_depsgraph does on a selection change
    except Exception:
        pass
    return n


class AC9_OT_SimFabricSelect(bpy.types.Operator):
    """Select the pieces on this fabric in the viewport and show the fabric in Properties > Tool (Shift: add them to
    the selection)"""
    bl_idname = "ac9_cloth_sim.fabric_select"
    bl_label = "Select Pieces on Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    fabric_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        from . import tool_tab as TT
        spec, _ = store.get(context)
        if self.fabric_id not in FA.fabric_index(spec):
            return {'CANCELLED'}
        names = set(FA.users(spec, self.fabric_id))
        n = _select_pieces(context, names, self.extend)
        # the Tool tab shows the fabric, not the piece just selected (REVIEW_fable_lists_ui_2026-10-25 §3.1)
        TT.focus_item(context, 'FABRIC', self.fabric_id)
        _tag_redraw(context)
        if n is None:                                 # a new fabric: nothing to select, its settings still shown
            self.report({'INFO'}, "No piece on it in the viewport" if names else "No piece on this fabric yet")
            return {'FINISHED'}
        self.report({'INFO'}, rpt("{0} piece(s) on {1}", n, FA.fabric_index(spec)[self.fabric_id]['name']))
        return {'FINISHED'}


class AC9_OT_SimColliderSelect(bpy.types.Operator):
    """Select this collision object in the viewport (Shift: add it to the selection)"""
    bl_idname = "ac9_cloth_sim.collider_select"
    bl_label = "Select Collision Object"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        ob = context.view_layer.objects.get(self.name)
        if ob is None:
            return {'CANCELLED'}
        if not self.extend:
            for o in context.view_layer.objects.selected:
                o.select_set(False)
        try:
            ob.select_set(not ob.select_get() if self.extend else True)   # Shift takes it out again
        except RuntimeError:                          # hidden from the view layer
            self.report({'INFO'}, "The object is hidden")
            return {'CANCELLED'}
        if ob.select_get():
            context.view_layer.objects.active = ob
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimFabricNew(bpy.types.Operator):
    """A new fabric with the default fabric's numbers (pick a preset or edit it in Properties > Tool)"""
    bl_idname = "ac9_cloth_sim.fabric_new"
    bl_label = "New Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        from . import tool_tab as TT
        spec = _fabric_spec(context)
        fid = FA.add_custom(spec, tx("New Fabric"))
        store.put(context, spec)
        TT.focus_item(context, 'FABRIC', fid)
        _tag_redraw(context)
        return {'FINISHED'}


def _preset_items(self, context):
    return _keep([(p['preset'], p['name'], f"{p['weight_gsm']:g} g/m², {FA.bending(p):g} µN·m. {p.get('source', '')}")
                  for p in FA.presets()['fabrics']], owner='fabric_preset')


class AC9_OT_SimFabricPreset(bpy.types.Operator):
    """This fabric takes a preset's name and numbers (every piece on it takes them at the next Phase Sim)"""
    bl_idname = "ac9_cloth_sim.fabric_preset"
    bl_label = "Fabric Preset"
    bl_options = {'REGISTER', 'UNDO'}

    fabric_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    preset: EnumProperty(name="Preset", items=_preset_items)

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        from . import tool_tab as TT
        spec = _fabric_spec(context)
        fid = self.fabric_id or TT.focused_item(spec, 'FABRIC')
        try:
            FA.apply_preset(spec, fid, self.preset)
        except KeyError:
            return {'CANCELLED'}
        store.put(context, spec)
        refresh_display(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimFabricEdit(bpy.types.Operator):
    """Change this fabric's numbers (every piece on it takes them at the next Phase Sim). An edited preset is
    no longer the preset: its name gets (edited)"""
    bl_idname = "ac9_cloth_sim.fabric_edit"
    bl_label = "Edit Fabric"
    bl_options = {'REGISTER', 'UNDO'}

    fabric_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # set by the button
    fabric_name: StringProperty(name="Name")
    weight_gsm: FloatProperty(name="Weight g/m²", min=1.0, max=2000.0)
    bending_uNm: FloatProperty(name="Bending µN·m", min=0.01, max=10000.0,
                               description="Flexural rigidity (KES / FAST B), warp and weft averaged")
    stretch_warp: FloatProperty(name="Stretch warp N/m", min=0.1, max=1e6, description="Along the pattern's x")
    stretch_weft: FloatProperty(name="Stretch weft N/m", min=0.1, max=1e6, description="Along the pattern's y")
    shear_N_m: FloatProperty(name="Shear N/m", min=0.1, max=1e5, description="KES G x 56.2")
    bend_hyst_per_m: FloatProperty(name="Hysteresis /m", min=0.0, max=1000.0,
                                   description="Bending hysteresis 2HB / 2B: the curvature up to which a bend stays")
    thickness_mm: FloatProperty(name="Thickness mm", min=0.0, max=10.0, precision=2,
                                description="Drawn thickness of the cloth (display only, the simulation does not use "
                                            "it). 0: none")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec = _fabric_spec(context)
        f = FA.fabric_index(spec).get(self.fabric_id)
        if f is None:
            return {'CANCELLED'}
        st = FA.stretch(f) or (FA.OLD_STRETCH_N_M, FA.OLD_STRETCH_N_M)
        self.fabric_name = f['name']
        self.weight_gsm, self.bending_uNm = float(f['weight_gsm']), FA.bending(f)
        self.stretch_warp, self.stretch_weft = st
        self.shear_N_m, self.bend_hyst_per_m = float(f['shear_N_m']), FA.bend_hyst(f)
        self.thickness_mm = FA.thickness(f) or 0.0
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        for k in ('fabric_name', 'weight_gsm', 'bending_uNm', 'stretch_warp', 'stretch_weft', 'shear_N_m', 'bend_hyst_per_m',
                  'thickness_mm'):
            col.prop(self, k)

    def execute(self, context):
        spec = _fabric_spec(context)
        f = FA.fabric_index(spec).get(self.fabric_id)
        if f is None:
            return {'CANCELLED'}
        r = lambda v: round(float(v), 6)
        f.update(weight_gsm=r(self.weight_gsm), bending_uNm=r(self.bending_uNm), shear_N_m=r(self.shear_N_m),
                 bend_hyst_per_m=r(self.bend_hyst_per_m),
                 thickness_mm=(r(self.thickness_mm) if self.thickness_mm > 0 else None),
                 stretch_N_m=(r(self.stretch_warp) if r(self.stretch_warp) == r(self.stretch_weft)
                              else {'warp': r(self.stretch_warp), 'weft': r(self.stretch_weft)}))
        p = FA.preset('fabrics', f.get('preset') or '')
        if p is not None and not FA._same(f, p):
            f['preset'] = None
            f['source'] = tx("edited from the preset {0}: ", p['name']) + f.get('source', '')
            if not f['name'].endswith('(edited)'):
                f['name'] += ' (edited)'
        nm = self.fabric_name.strip()
        if nm and nm != f['name'] and not (p is not None and nm == p['name']):
            f['name'] = nm                            # a name typed in the dialog wins over "(edited)"
        store.put(context, spec)
        refresh_display(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


def _interlining_preset_items(self, context):
    return _keep([(p['preset'], p['name'], f"+{p['weight_gsm']:g} g/m², +{p['bending_uNm']:g} µN·m. {p.get('source', '')}")
                  for p in FA.presets()['interlinings']], owner='interlining_preset')


class AC9_OT_SimInterliningNew(bpy.types.Operator):
    """A new interlining for this garment (pick a preset or edit it in Properties > Tool); give it to pieces in
    their Interlining field"""
    bl_idname = "ac9_cloth_sim.interlining_new"
    bl_label = "New Interlining"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        from . import tool_tab as TT
        spec = _fabric_spec(context)
        iid = FA.add_custom_interlining(spec, tx("New Interlining"))
        store.put(context, spec)
        TT.focus_item(context, 'BOND', iid)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimInterliningPreset(bpy.types.Operator):
    """This interlining takes a preset's name and numbers (every piece with it takes them at the next Phase Sim)"""
    bl_idname = "ac9_cloth_sim.interlining_preset"
    bl_label = "Interlining Preset"
    bl_options = {'REGISTER', 'UNDO'}

    interlining_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    preset: EnumProperty(name="Preset", items=_interlining_preset_items)

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        from . import tool_tab as TT
        spec = _fabric_spec(context)
        try:
            FA.apply_interlining_preset(spec, self.interlining_id or TT.focused_item(spec, 'BOND'), self.preset)
        except KeyError:
            return {'CANCELLED'}
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimInterliningEdit(bpy.types.Operator):
    """Change this interlining's numbers (every piece with it takes them at the next Phase Sim). An edited preset
    is no longer the preset: its name gets (edited)"""
    bl_idname = "ac9_cloth_sim.interlining_edit"
    bl_label = "Edit Interlining"
    bl_options = {'REGISTER', 'UNDO'}

    interlining_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    piece: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})          # the pencil: the piece's (its id may be new)
    interlining_name: StringProperty(name="Name")
    weight_gsm: FloatProperty(name="Weight +g/m²", min=0.0, max=2000.0, description="Added to the fabric's weight")
    bending_uNm: FloatProperty(name="Bending +µN·m", min=0.0, max=10000.0,
                               description="Added to the fabric's bending (the sum is a lower bound: a fused pair is "
                                           "stiffer)")

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        spec = _fabric_spec(context)
        if not self.interlining_id and self.piece:    # a garment from before the list: migrate() gives the id
            self.interlining_id = (FA.piece_bond(spec, self.piece) or {}).get('id') or ''
        i = FA.interlining_index(spec).get(self.interlining_id)
        if i is None:
            return {'CANCELLED'}
        self.interlining_name = i['name']
        self.weight_gsm, self.bending_uNm = float(i['weight_gsm']), float(i['bending_uNm'])
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        for k in ('interlining_name', 'weight_gsm', 'bending_uNm'):
            col.prop(self, k)

    def execute(self, context):
        spec = _fabric_spec(context)
        i = FA.interlining_index(spec).get(self.interlining_id)
        if i is None:
            return {'CANCELLED'}
        r = lambda v: round(float(v), 6)
        i.update(weight_gsm=r(self.weight_gsm), bending_uNm=r(self.bending_uNm))
        p = FA.preset('interlinings', i.get('preset') or '')
        if p is not None and (float(p['weight_gsm']), float(p['bending_uNm'])) != (i['weight_gsm'], i['bending_uNm']):
            i['preset'] = None
            i['source'] = tx("edited from the preset {0}: ", p['name']) + i.get('source', '')
            if not i['name'].endswith('(edited)'):
                i['name'] += ' (edited)'
        nm = self.interlining_name.strip()
        if nm and nm != i['name'] and not (p is not None and nm == p['name']):
            i['name'] = nm
        store.put(context, spec)
        _tag_redraw(context)
        return {'FINISHED'}


class AC9_OT_SimInterliningRemove(bpy.types.Operator):
    """Take this interlining off the garment's list; its pieces have none then"""
    bl_idname = "ac9_cloth_sim.interlining_remove"
    bl_label = "Remove Interlining"
    bl_options = {'REGISTER', 'UNDO'}

    interlining_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def execute(self, context):
        spec = _fabric_spec(context)
        if self.interlining_id not in FA.interlining_index(spec):
            return {'CANCELLED'}
        n = FA.remove_interlining(spec, self.interlining_id)
        store.put(context, spec)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0} piece(s) without an interlining now", len(n)))
        return {'FINISHED'}


class AC9_OT_SimInterliningSelect(bpy.types.Operator):
    """Select the pieces with this interlining in the viewport and show it in Properties > Tool (Shift: add them
    to the selection)"""
    bl_idname = "ac9_cloth_sim.interlining_select"
    bl_label = "Select Pieces with Interlining"
    bl_options = {'REGISTER', 'UNDO'}

    interlining_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    extend: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return store.get(context)[0] is not None

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        from . import tool_tab as TT
        spec, _ = store.get(context)
        if FA.needs_migrate(spec):                    # a garment from before 2026-10-25: the list is made now
            spec = _fabric_spec(context)
            store.put(context, spec)
        if self.interlining_id not in FA.interlining_index(spec):
            return {'CANCELLED'}
        names = set(FA.users_by_interlining(spec)[self.interlining_id])
        _select_pieces(context, names, self.extend)
        TT.focus_item(context, 'BOND', self.interlining_id)
        _tag_redraw(context)
        self.report({'INFO'}, rpt("{0} piece(s) with {1}", len(names),
                                  FA.interlining_index(spec)[self.interlining_id]['name']))
        return {'FINISHED'}


CLASSES = (AC9_OT_SimSetUnits, AC9_OT_SimLayersToZero, AC9_OT_SimProposeRoles,AC9_OT_SimSetRole, AC9_OT_SimSetStart, AC9_OT_SimImportBOM, AC9_OT_SimUseSelectedPieces, AC9_OT_SimGarmentFromSelection,
           AC9_OT_SimSelectGarment, AC9_OT_SimRenameGarment, AC9_OT_SimEnableAllPieces,
           AC9_OT_SimSeamsFromBOM, AC9_OT_SimSeamEdit, AC9_OT_SimSeamPath, AC9_OT_SimSeamPathPart, AC9_OT_SimSeamAttach, AC9_OT_SimSeamAttachUntil, AC9_OT_SimSetFold, AC9_OT_SimPiecesOnOff, AC9_OT_SimFreeze, AC9_OT_SimPieceState, AC9_OT_SimInvertPieceSelection, AC9_OT_SimSelectPiece, AC9_OT_SimRefresh,
           AC9_OT_SimPieceMesh, AC9_OT_SimPieceElastic, AC9_OT_SimPieceFabric, AC9_OT_SimPieceInterlining,
           AC9_OT_SimFabricAdd, AC9_OT_SimFabricRemove, AC9_OT_SimFabricDefault, AC9_OT_SimFabricSelect, AC9_OT_SimColliderSelect, AC9_OT_SimFabricNew,
           AC9_OT_SimFabricPreset, AC9_OT_SimFabricEdit, AC9_OT_SimInterliningNew, AC9_OT_SimInterliningPreset,
           AC9_OT_SimInterliningEdit, AC9_OT_SimInterliningRemove, AC9_OT_SimInterliningSelect)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
