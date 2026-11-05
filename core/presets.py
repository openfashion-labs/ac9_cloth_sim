# SPDX-License-Identifier: GPL-3.0-or-later
"""The one place the Dress / Continue / Live settings come from (no bpy). 2026-10-08.

Before, the screen's Dress, its Continue and the headless tests each got their settings by another way: the panel,
the stage the result came from, and 19 environment variables read here and there in dress.py, plus a JSON override
applied after physical(). A test that worked did not say the screen would (2026-10-07: the shirt crumpled on the
screen's Dress, the jacket's ring placement was only in a test's environment). Now:

- a *settings set* is a dict of the keys in DEFAULTS. PRESETS names the sets; 'default' is what the screen runs.
- the scene's ac9_cloth_sim.preset names the set (empty = 'default'); the screen never sets it, a test sets it
  (or saves a blend with it set, and then the screen runs that set too). It may also hold JSON
  {"base": "<set name>", <key>: <value>, ...} for a one-off variant of a named set.
- resolve() turns that into the full settings of a job: the set over DEFAULTS, then (for now) the old environment
  variables (from_env, recorded in 'from_env' so a job says what it took from there), then the Dress panel for the
  keys left None (Physical Fabric, Weight, Bending, Watch).
- build_stages() assembles every stage's solver config from the recipe and the settings, in one order:
  recipe stages -> (Continue) the cloth of the stage the shape came from -> physical fabric -> per-stage extras
  (the caller's, which need the meshed pattern) -> watch -> cfg_override. The job keeps the settings (job.json
  'settings', with the solver process's environment in 'solver_env').

A key's default is the behavior before this module; the screen's Dress is unchanged by it (regression
cloth_sim_dev/regress, 2026-10-08).
"""
import json
import os

from . import lay as LAY
from . import recipe
from . import roles

DEFAULTS = {
    # the fabric (core/recipe.py physical). None = the garment's default fabric (core/fabric.py, 2026-10-20: the
    # Dress panel's Weight / Bending / Physical rows are gone); a set fixes them for a test only
    'physical': None,                # Physical Fabric (False: the cloth before 2026-10-07, the test set legacy_cloth)
    'weight_gsm': None,              # Weight g/m2
    'bending_uNm': None,             # Bending uN m
    'watch': None,                   # Watch for Anomalies
    # cloth taken into a seam and closed seams reopening, counted every 10 frames and recorded (never stopped). On by
    # default (user 2026-10-09, DECISIONS cloth-sim-guard-on-by-default-2026-10-09); a dict overrides its thresholds
    'guard': True,
    # vertices that keep moving differently from their neighbors (a collar end jittering to the last frame), smoothed
    # every frame and recorded at each stage's end (never stopped). On by default (user 2026-10-15, chats/jitter-detector.md);
    # a dict overrides its thresholds (rel_mm, flick_deg, alpha, skip, tail)
    'jitter': True,
    # the cloth defined per length / area so the same fabric is the same cloth at any mesh size (Physical only;
    # Fable REVIEW_fable_mesh_integration_r3 S1). On since the regression check 2026-10-08 (regress runs/mi_on)
    'mesh_independent': True,
    # a narrow band sewn on along one edge (a T-shirt's neck rib) is laid on its partners' sewn edge where it joins,
    # not started where it was placed (core/lay.py; user 2026-10-21: on, may become a switch after the tests)
    'lay': True,
    # garment spec keys laid over the spec for this job only (not written to the spec text): recipe,
    # recipe_frames, collar_after, collar_first, collar_prefold, symmetry, ... (see core/recipe.py, dress.py)
    'spec': {},
    # TEST options (were environment variables AC9_SIM_*; their effect is described where they are used)
    'ring_pieces': [],               # pieces laid side by side as a ring, when the spec names none (AC9_SIM_RING)
    'no_stack': [],                  # pieces not stacked outside the pieces they overlap (AC9_SIM_NO_STACK)
    'arm_legacy': False,             # the older arm cut |x| > 1.4 x half width (AC9_SIM_ARM_LEGACY)
    'arm_profile_pad_mm': 5.0,       # pad of the torso's half-width profile (AC9_SIM_ARM_PROFILE_PAD)
    'prefold_torso': None,           # None, 'all' or ["piece|line", ...]: torso folds folded at placement (AC9_SIM_PREFOLD)
    'twin_swap': False,              # mesh the mirrored twin, mirror into the other (AC9_SIM_TWIN_SWAP)
    'fold_free': [],                 # ["piece|line"]: pressed folds left free after placement (AC9_SIM_FOLD_FREE)
    'fold_bake': [],                 # ["piece|line"]: folds taken out of the simulation (AC9_SIM_FOLD_BAKE)
    'stack_under': [],               # ["piece|line>piece|line"] (AC9_SIM_STACK_UNDER)
    'stack_depth_mm': 3.5,           # (AC9_SIM_STACK_DEPTH)
    'layer': [],                     # ["LOWER<UPPER"] (AC9_SIM_LAYER)
    'layer_free': False,             # (AC9_SIM_LAYER_FREE)
    'layer_mode': 'contact',         # (AC9_SIM_LAYER_MODE)
    'layer_opts': {},                # (AC9_SIM_LAYER_OPTS)
    'layer_all': False,              # keep the layer order on every stage (AC9_SIM_LAYER_ALL)
    'ease_w_mm': None,               # ease every seam with sides 2-43 % apart over w mm (AC9_SIM_EASE_W)
    'cfg_override': {},              # merged into every stage's config last; "s<k>" for stage k only (AC9_SIM_CFG_OVERRIDE)
    # the solver process: Newton's compression scale (read when newton is imported, one value for the whole job).
    # None = 0.3 for the sleeved recipe (the experiment's jacket and shirt), the solver's own otherwise
    'compression_scale': None,       # (NEWTON_VBD_COMPRESSION_SCALE)
}

JITTER_DEFAULT = {"rel_mm": 0.5, "flick_deg": 4.0, "alpha": 0.1, "skip": 10, "tail": 30}
GUARD_DEFAULT = {"every": 10, "far_mm": 15.0, "near_mm": 3.0, "min": 30, "share": 0.03, "reopen_n": 5}

JACKET6_RING = ['PARTS1', 'PARTS19', 'PARTS4', 'PARTS14', 'PARTS9', 'PARTS17']

# the keys only a test may set (the rest: the fabric, the spec overlay, the solver's compression). A user set
# naming one of them is an error (check_sets), so what the add-on ships never runs a test switch
TEST_KEYS = ('ring_pieces', 'no_stack', 'arm_legacy', 'arm_profile_pad_mm', 'prefold_torso', 'twin_swap', 'fold_free',
             'fold_bake', 'stack_under', 'stack_depth_mm', 'layer', 'layer_free', 'layer_mode', 'layer_opts',
             'layer_all', 'ease_w_mm', 'cfg_override')

# sets for use (the screen runs 'default')
USER_PRESETS = {
    # what the screen's Dress runs (the panel decides the fabric)
    'default': {},
    # the cloth before 2026-10-07 (look-matched to the reference drape, bending 1.0): what the approved shirt / pants were made with
    'legacy_cloth': {'physical': False},
}

# TEST sets: each states only how it differs from 'default' (user 2026-10-08: tests stay apart from what ships, and
# the difference stays visible). Names start with 'test_'
TEST_PRESETS = {
    # the jacket bodice laid as a ring (APPROVED v2 jacket bodice, RESULT_G3 note 14; the calibration's
    # "Dress path, hems -7..+11 from the reference" was measured with it)
    'test_jacket_ring': {'ring_pieces': JACKET6_RING},
    # the solver's per-frame diagnostics on (diag.npz, hinges.npz) and the watch
    'test_measure': {'watch': True, 'cfg_override': {'diag': True}},
    # the cloth before the mesh-independent definitions (2026-10-08), to compare with
    'test_mesh_dependent': {'mesh_independent': False},
    # undo small cloth-through-cloth crossings and vertices deep in the body (solver deep_push + untangle_free,
    # 2026-10-19). Kept to call on garments harder than the T-shirt (user 2026-10-19); a normal T-shirt never
    # triggers it, shape p99 0.5 mm, time +32 %. Not for cloth laid on the wrong side of its seam.
    # docs/reviews/INVENTORY_buried_features_2026-10-19.md section 9
    # the neck rib started where it was placed, as before 2026-10-21 (core/lay.py off)
    'test_no_lay': {'lay': False},
    'test_recover': {'cfg_override': {'deep_push': {'cap_mm': 5},
                                      'untangle_free': {'rings': 2, 'seams': True, 'idle': 3}}},
}

PRESETS = {**USER_PRESETS, **TEST_PRESETS}


def check_sets():
    """The rules above, checked when the module loads (Fable review 2026-10-08)."""
    for n, st in USER_PRESETS.items():
        bad = sorted(set(st) & set(TEST_KEYS))
        if bad or n.startswith('test'):
            raise ValueError(f"settings set '{n}' is not a test set but sets {bad or 'a test name'}")
    for n in TEST_PRESETS:
        if not n.startswith('test_'):
            raise ValueError(f"test settings set '{n}' must be named test_...")


check_sets()

# the old environment variables -> keys (from_env), read by headless runs only (never the screen's Dress). Kept
# while the scripts written with them are in use; remove once the calibration's run_*.sh name sets instead
_ENV = {
    'AC9_SIM_RING': ('ring_pieces', 'list'),
    'AC9_SIM_NO_STACK': ('no_stack', 'list'),
    'AC9_SIM_ARM_LEGACY': ('arm_legacy', 'flag'),
    'AC9_SIM_ARM_PROFILE_PAD': ('arm_profile_pad_mm', 'float'),
    'AC9_SIM_PREFOLD': ('prefold_torso', 'prefold'),
    'AC9_SIM_TWIN_SWAP': ('twin_swap', 'flag'),
    'AC9_SIM_FOLD_FREE': ('fold_free', 'list'),
    'AC9_SIM_FOLD_BAKE': ('fold_bake', 'list'),
    'AC9_SIM_STACK_UNDER': ('stack_under', 'semi'),
    'AC9_SIM_STACK_DEPTH': ('stack_depth_mm', 'float'),
    'AC9_SIM_LAYER': ('layer', 'semi'),
    'AC9_SIM_LAYER_FREE': ('layer_free', 'flag'),
    'AC9_SIM_LAYER_MODE': ('layer_mode', 'str'),
    'AC9_SIM_LAYER_OPTS': ('layer_opts', 'json'),
    'AC9_SIM_LAYER_ALL': ('layer_all', 'flag'),
    'AC9_SIM_EASE_W': ('ease_w_mm', 'float'),
    'AC9_SIM_CFG_OVERRIDE': ('cfg_override', 'json'),
    'NEWTON_VBD_COMPRESSION_SCALE': ('compression_scale', 'str'),
}


def from_env(environ=None):
    """{key: value} for the old environment variables that are set (non-empty), as the code read them."""
    environ = os.environ if environ is None else environ
    out = {}
    for var, (key, kind) in _ENV.items():
        raw = environ.get(var)
        if not raw:
            continue
        if kind == 'list':
            v = [x for x in raw.split(',') if x]
        elif kind == 'semi':
            v = [x.strip() for x in raw.split(';') if x.strip()]
        elif kind == 'flag':
            v = True
        elif kind == 'float':
            v = float(raw)
        elif kind == 'json':
            v = json.loads(raw)
        elif kind == 'prefold':
            v = 'all' if raw == '1' else [x for x in raw.split(',') if x]
        else:
            v = raw
        out[key] = v
    return out


def named(value):
    """(name, set) for the scene's preset value: '' / a set name / JSON {"base": name, key: value, ...}."""
    value = (value or '').strip() or 'default'
    if value.startswith('{'):
        d = json.loads(value)
        base = d.pop('base', 'default')
        if base not in PRESETS:
            raise ValueError(f"Unknown settings set '{base}' (known: {', '.join(PRESETS)})")
        # a one-off variant is a test, whatever its base: its name says so
        name = f"{base}+" + ",".join(sorted(d))
        return (name if name.startswith('test_') else 'test:' + name), _merge(PRESETS[base], d)
    if value not in PRESETS:
        raise ValueError(f"Unknown settings set '{value}' (known: {', '.join(PRESETS)})")
    return value, dict(PRESETS[value])


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = dict(out[k], **v) if k in ('spec', 'cfg_override') and isinstance(out.get(k), dict) else v
    return out


def is_test(name):
    """A test set, a one-off variant or old environment variables: what the screen flags."""
    return name.startswith('test')


def fixed_fabric(value):
    """The fabric keys the scene's set fixes, over the panel's rows: {'physical': False, ...}; {} if the value
    does not parse (the panel must still draw)."""
    try:
        return {k: v for k, v in named(value)[1].items()
                if k in ('physical', 'weight_gsm', 'bending_uNm', 'watch') and v is not None}
    except ValueError:
        return {}


def resolve(value, panel=None, environ=None):
    """The full settings of a job: DEFAULTS < the named set < the old environment variables, then the panel's
    values for the fabric keys left None. panel: an object with physical_fabric, fabric_weight, fabric_bending,
    watch (the scene's ac9_cloth_sim) or None. environ: None reads os.environ; the screen passes {} (a Blender
    started from a shell with test variables set must not run them unseen, Fable review 2026-10-08)."""
    name, st = named(value)
    bad = sorted(set(st) - set(DEFAULTS))
    if bad:
        raise ValueError(f"Unknown settings key(s): {', '.join(bad)}")
    out = dict(DEFAULTS, **st)
    env = from_env(environ)
    out = _merge(out, env)
    if env and not name.startswith('test'):
        name = f"test:{name}+env"
    # the fabric keys a set left None: physical on (the panel's checkbox is gone, 2026-10-20 -- a hidden False kept
    # in an old file must not switch the fabrics off), weight / bending the panel's old values here (the caller lays
    # the garment's default fabric over them, dress.py; they are what core/fabric.migrate reads for an old garment)
    out['fixed_fabric'] = sorted(k for k in ('weight_gsm', 'bending_uNm') if out[k] is not None)
    if out['physical'] is None:
        out['physical'] = True
    for key, attr, fallback in (('weight_gsm', 'fabric_weight', 276.0), ('bending_uNm', 'fabric_bending', 18.0),
                                ('watch', 'watch', False)):
        if out[key] is None:
            out[key] = getattr(panel, attr, fallback) if panel is not None else fallback
    out['physical'], out['watch'] = bool(out['physical']), bool(out['watch'])
    out['weight_gsm'], out['bending_uNm'] = float(out['weight_gsm']), float(out['bending_uNm'])
    if panel is not None and getattr(panel, 'fast_live', False):
        out['fast_live'] = True
    out['name'] = name
    out['from_env'] = sorted(env)
    return out


def spec_for_job(spec, st):
    """The garment spec with the set's spec keys laid over it (a copy)."""
    sp = dict(spec, **(st.get('spec') or {}))
    if st.get('twin_swap'):
        sp['twin_swap'] = True
    return sp


def solver_env(st, kind):
    """Environment variables of the solver process."""
    cs = st.get('compression_scale')
    # until 2026-10-16 a sleeved garment ran at 0.3 (the spike's jacket and shirt). At 0.3 the T-shirt's rib trembles
    # without end (p99 0.15 mm/frame after 600 more frames of the last stage, 1.0: 0.03) and the hem stands 2.5 mm
    # further from the reference drape (experiments/seam_open_B_2026-10-16, settle_A_2026-10-16). None = Newton's own 1.0.
    return {} if cs is None else {'NEWTON_VBD_COMPRESSION_SCALE': str(cs)}


# the cloth Continue takes over from the stage its shape came from (10/06: Continue ran on the recipe's base cloth,
# 200 times stiffer than the dress it went on from; a collar fold line jumped 6 -> 109 degrees in 20 frames)
CONTINUE_INHERIT = ('edge_ke', 'edge_kd', 'seam_bend', 'init_xv', 'stitch_rest_mm')


def _override(cfg, k, ov):
    """cfg_override on stage k (0-based): a dict value merges one level down, None deletes a key, "s<k>" holds
    overrides for stage k only."""
    changed = []
    if not ov:
        return changed
    ovd = dict({k_: v_ for k_, v_ in ov.items() if not (k_[:1] == 's' and k_[1:].isdigit())},
               **ov.get(f"s{k + 1}", {}))
    for key, v in ovd.items():
        before = cfg.get(key)
        if v is None:
            cfg.pop(key, None)
        elif isinstance(v, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = dict(cfg[key], **v)
        else:
            cfg[key] = v
        if cfg.get(key) != before:
            changed.append(key)
    return changed


def build_stages(spec, names, rings, pattern_path, crop, h, st, extras=None, prev_cfg=None, continue_=False,
                 new=(), live=False, hand=()):
    """(kind, [(label, cfg)]) for a job: the recipe's stages (or Continue / Live's), the settings applied in the
    order the module docstring gives. extras(k, cfg): the caller's per-stage additions that need the meshed
    pattern (init_from, the grab file, mirror map, test options' vertex lists), applied before the watch and the
    override."""
    if live and not continue_:
        # Live before any result (user 2026-10-22): Live's one stage from the placement, gravity on from the start
        # and every seam closing over the ordinary sewing ramp; no stages, nothing inherited
        kind, stg = recipe.continue_stages(spec, names, pattern_path, crop, new=names, live=True)
    elif continue_:
        kind, stg = recipe.continue_stages(spec, names, pattern_path, crop, new=new, live=live)
        prev_cfg = prev_cfg or {}
        for _label, cfg in stg:
            for key in CONTINUE_INHERIT:
                if key in prev_cfg:
                    if key == 'seam_bend':
                        # the cloth's seam hinges from the stage it went on from, the seams' folds as they are set
                        # now: taken over whole, a fold set since was dropped (user 2026-10-23: a seam set to 90
                        # deg, then Live, did not fold) and a fold set to nothing kept its old angle
                        sb = {k_: v_ for k_, v_ in prev_cfg[key].items() if k_ not in ('rest_deg_seams', 'fold_ke')}
                        sb.update({k_: v_ for k_, v_ in (cfg.get(key) or {}).items()
                                   if k_ in ('rest_deg_seams', 'fold_ke')})
                        cfg[key] = sb
                    else:
                        cfg[key] = prev_cfg[key]
                elif key in cfg and key in ('init_xv',):
                    del cfg[key]
            for key in ('self_margin', 'self_gap'):          # the closing stage's tighter contact stays tighter
                if key in prev_cfg and key in cfg:
                    cfg[key] = min(cfg[key], prev_cfg[key])
    else:
        kind, stg = recipe.stages(spec, names, rings, pattern_path, crop, kind=spec.get('recipe'))
        # a narrow band sewn on along one edge (a T-shirt's neck rib) laid on its partners' sewn edge where it joins
        # (core/lay.py, user 2026-10-21); hand: the pieces the user moved by hand start where they were put
        st['laid'] = []
        body_, collar_, _sl, _top = recipe.groups(spec, names)
        if st.get('lay', True) and kind == 'sleeved':
            st['laid'] = LAY.apply(stg, LAY.candidates(spec, names, body_, collar_, hand=hand))
        # an On Edge piece not laid starts as placed, said in one line (user 2026-11-02: never a silent rule)
        sk = LAY.skipped(spec, names, body_, collar_, hand=hand)
        if not st.get('lay', True):
            sk = {n: "laying is off in this settings set" for n in LAY._edge_pieces(spec, names)}
        elif kind != 'sleeved':
            sk = {n: "On Edge needs a garment with sleeves for now: placed as it is"
                  for n in LAY._edge_pieces(spec, names)}
        sk.update({n: "On Edge could not find its stage: placed as it is"
                   for n in LAY.candidates(spec, names, body_, collar_, hand=hand)
                   if st.get('lay', True) and kind == 'sleeved' and n not in st['laid']})
        if sk:
            st['lay_skipped'] = sk
    recipe.hold_user_pieces(spec, stg)
    # the pieces' roles and what each stage sews (job.json settings['plan'], core/roles.py)
    st['plan'] = roles.plan(spec, names, stg)
    if st['physical']:
        recipe.physical(stg, st['weight_gsm'], st['bending_uNm'], h, mesh_independent=bool(st.get('mesh_independent')),
                        membrane=st.get('membrane'), kappa_y=st.get('kappa_y'))
    # what the override changed, per stage (it comes after physical: the job then says which measured values it
    # replaced, Fable review 2026-10-08)
    st['overridden'] = {}
    for k, (_label, cfg) in enumerate(stg):
        if extras is not None:
            extras(k, cfg)
        # the panel's "Watch for Anomalies" (off by default, user 2026-10-07): the solver's watch with the thresholds
        # the tests ran with (drift 0.4 mm/frame, tug of war, seam turned over); records, never stops
        if st['watch']:
            cfg['watch'] = {"drift_mm": 0.4, "tug": {}}
        if st.get('guard'):
            cfg['guard'] = dict(GUARD_DEFAULT, **(st['guard'] if isinstance(st['guard'], dict) else {}))
        if st.get('jitter'):
            cfg['jitter'] = dict(JITTER_DEFAULT, **(st['jitter'] if isinstance(st['jitter'], dict) else {}))
        ch = _override(cfg, k, st.get('cfg_override'))
        if ch:
            st['overridden'][f"s{k + 1}"] = sorted(ch)
    return kind, stg
