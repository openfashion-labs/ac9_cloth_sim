# SPDX-License-Identifier: GPL-3.0-or-later
"""Staging recipes: which pieces the solver sews in which order (no bpy). Returns per-stage configs for
solver/newton_vbd.py, the same shape as the experiment's run_generic.sh / run_sleeved.sh.

generic  (no piece on an arm guide): the closed bands alone at zero gravity -> everything with gravity,
         bands held -> close the seams (pants 2.4-2.7 mm from the reference drape, 12 s; skirt, legwear, measured 2026-09-28)
sleeved  (pieces on arm guides): body + collar at zero g with the arms taken out of the body -> gravity,
         arms back -> sleeves at zero g -> everything with gravity -> close the seams (the shirt / jacket recipe;
         a shirt fell to the hips with the generic one). Frames at half the experiment's count (as fast, same shape).
Groups come from the pieces' roles (core/roles.py: each role's unit -- sleeve, neck, trim, the rest body) and, for a
piece without a role, from its placement as before: on an arm guide sleeves, on the neck guide collar, the rest body.
Accessories (role "trim", the old "top") come on last, and spec['closures'] ([[piece, piece], ...]; absent, the two
plackets sewn to each other) are sewn shut only then.
"""
import hashlib
import json

import numpy as np

from . import fabric as FA
from . import roles as RO
from . import spec as S

BASE = {"fps": 60, "substeps": 10, "density": 0.14, "tri_ke": 3000.0, "tri_ka": 3000.0, "tri_kd": 0.001,
        "edge_ke": 1.0, "edge_kd": 0.0001, "stitch_kd": 0.0, "particle_radius": 0.0015, "body_gap": 0.002,
        "filter_rings": 2, "rest_from_flat": True, "internal_filter_rings": 0,
        "seam_bend": {"pairs": "all", "ke": 1.0, "rest_deg": 0}}


# Physical fabric (user 2026-10-07, DECISIONS cloth-sim-fabric-physical-default-2026-10-07): the cloth's stiffness
# from the fabric's measured quantities instead of numbers matched to the reference's look, measured in
# foundation_2026-09-30/calib (RESULT_calibration / AUDIT_pre_calibration_values 2026-10-07):
# - bending: on this mesh (hex grid of spacing h) the effective flexural rigidity is D = C_HEX x edge_ke x h
#   (cantilever strips against the heavy elastica, +-10 %), so edge_ke = D / (C_HEX h); the bending damping in
#   proportion (v2's 5e-7 at 0.005); a seam's hinge 2.5 x the cloth (2-3 layers of seam allowance; 10 x left more
#   cloth crossings with the measured values, 2 -> 27); a fold line as stiff as the cloth (only its rest angle differs)
# - the solver of v2: the iterations start from x + v dt (init_xv), contact margin / gap 0.3 / 0.15 mm in every stage
# - the closing stage as the 10/01 best jacket (AIRS3): stitches rest at 0.3 mm (the contact margin, not 0), air-like
#   damping 0.9, until still (p99 < 0.1 mm a frame for 10 frames, at most 300 frames); the gravity stage before it 4/3
#   longer. Without these the closing stage was cut while still moving 0.9-1.5 mm a frame.
# Jacket bodice, wool (276 g/m2, 18 uN m), 6 mm, 3 runs: hems -7..+14 mm from the reference, 2 cloth crossings.
C_HEX = 0.75
FOLD_PRESS = 10.0     # a set-angle fold line's hinges x the cloth's bending (physical() below)


def physical(stages, weight_gsm, bending_uNm, h_mm, mesh_independent=False, membrane=None, kappa_y=None):
    """The stages' configs with the fabric's density / bending and the v2 solver settings (in place; returns them).
    The fabric is the garment's default (core/fabric.garment_values, user 2026-10-20): membrane (ku, kv, ks) N/m --
    tri_ke along the flat x, tri_ka along y, tri_ks the shear -- and kappa_y (1/m) its bending hysteresis; None = the
    values every garment had before (BASE's 3000 / 3000, shear 29, the fabric table's kappa_y).
    mesh_independent: the solver's cfg 'mi' too -- stitches per length and self contact per area (the per-stitch /
    per-pair values were tuned on 6 mm), every cloth hinge from the rigidity and its own triangles, and the coarse-mesh
    correction C(a/c) (calib/RESULT_calibration_2026-10-07, 4 / 6 / 10 mm hems within -3..+13 mm of the reference)."""
    ke = bending_uNm * 1e-6 / (C_HEX * h_mm * 1e-3)
    ku, kv, ks = membrane if membrane is not None else (BASE['tri_ke'], BASE['tri_ka'], 29.0)
    kap = FA.bend_hyst() if kappa_y is None else float(kappa_y)
    for _label, cfg in stages:
        cfg.update(tri_ke=float(ku), tri_ka=float(kv))   # the fabric's stretch (the stitch cap below reads them)
        cfg.update(density=weight_gsm / 1000.0, edge_ke=ke, edge_kd=1e-4 * ke, init_xv=True,
                   self_margin=0.0003, self_gap=0.00015)
        if mesh_independent:
            cfg['mi'] = {'D': bending_uNm * 1e-6, 'ref_mm': 6.0, 'ac_correct': True}
        sb = dict(cfg.get('seam_bend') or BASE['seam_bend'])
        sb['ke'] = 2.5 * ke
        if 'fold_ke' in sb:
            sb['fold_ke'] = ke
        cfg['seam_bend'] = sb
        # Coulomb friction in every stage, and a cap on each pattern stitch's pull (2026-10-16, DECISIONS
        # cloth-sim-all-stage-coulomb-2026-10-16). With Newton's friction_epsilon 1e-2 the friction below 10 mm/s is
        # a drag and the cloth creeps up under the seams' pull (never settles, hem +13 mm / 600 frames from the reference
        # drape); 1e-3 still lets it creep 0.4 mm / 100 frames, 1e-4 0.04 (experiments/settle_A_2026-10-16, T-shirt
        # drape, 600 frames; 2026-10-16 1e-3 -> 1e-4 under gravity). Alone it left the shirt points in the
        # body; the cap -- the force that stretches the cloth 10 % over one mesh edge, (lambda + 2 mu) x 0.1 x h --
        # reels far seams in gently. Only in the stages without gravity: under gravity the seams carry the garment
        # and a capped jacket hung open (p90 24 -> 27-40 mm, up to 27 points in the body).
        # Under gravity 1e-4; the stages without gravity keep 1e-3: there 1e-4 with the cap put 38 jacket points in
        # the body in 1 of 2 runs (0 of 4 with 1e-3, cloth_sim_dev/regress runs e4cs03_1016 / split_1016)
        cfg['friction_epsilon'] = 1e-4
        if cfg.get('gravity_z', -9.81) == 0.0:
            cfg['friction_epsilon'] = 1e-3
            cfg['stitch_cap_N'] = (cfg.get('tri_ka', BASE['tri_ka']) + 2.0 * cfg.get('tri_ke', BASE['tri_ke'])) * 0.1 * h_mm * 1e-3
        # Orthotropic membrane (user 2026-10-17, DECISIONS cloth-sim-aniso-membrane-first-2026-10-17): stretch along
        # the flat x / y (tri_ke / tri_ka) and the shear apart, after Baraff-Witkin. Shear 29 N/m = cotton shirting,
        # KES G 0.52 gf/(cm deg) x 56.2 (a physical value, not a matched one). The isotropic membrane held shear as stiff as
        # stretch, so the cloth folded where fabric would shear (fine back wrinkles; experiments/aniso_membrane_2026-10-17).
        # bw_hess_reg 1: the exact Hessian trembles; the regularization leaves the forces as they are.
        # Regression on every garment: experiments/aniso_regress_2026-10-17 (pants / shirt fewer crossings, more seams
        # closed; skirt the same; the jacket front can slide off the shoulder, 2 of 4 runs).
        cfg.update(membrane='bw', tri_ks=float(ks), bw_hess_reg=1.0)
        # Where the cloth stops (user 2026-10-18, DECISIONS cloth-sim-bend-hysteresis-stick-slip-2026-10-18), three
        # together (experiments/converge_2026-10-18, jitter_where_2026-10-18): the fabric's bending hysteresis as
        # stick-slip (bend_fric2: kappa_y = 2HB / 2B from the fabric table, on once the seams are closed, ratio 100);
        # the edge-edge contacts across a stitch taken out like its vertex-triangle ones (stitch_edge_filter: they
        # lifted a T-shirt 2 cm after its seams closed); the body friction's slip measured from an anchor kept across
        # substeps (fric_anchor: the garment crept on the body below friction_epsilon). T-shirt (poplin 140 g/m2,
        # 6 uN m): the experiment's 2 of 2 stops were luck -- the one-stage T-shirt stops in about half the runs
        # (experiments/bend_hyst_2026-10-18, docs/reviews/REVIEW_fable_bend_hyst_stop_2026-10-18.md).
        # With the anchor the body friction's epsilon is 1e-3 in every stage (user 2026-10-18; it held the garment
        # without creeping, experiments/jitter_where_2026-10-18 cv18).
        cfg.update(stitch_edge_filter=True, fric_anchor=True, friction_epsilon=1e-3,
                   bend_fric2={'kappa_per_m': kap, 'ratio': 100.0, 'start_mm': 2.0})
        # A fold line with a set angle is a pressed crease, FOLD_PRESS x the cloth's bending (user 2026-10-29, DECISIONS
        # cloth-sim-fold-strength-per-line-2026-10-23). At x1 a sleeve's tucks lay flat at 20 mm (|angle| 90 / 72 for
        # 180 / 120 after 20 -> 8 mm) where the reference folds them at 20 mm and at 8; x10: 161-168 / 122-133, the hem
        # 12 / 1 mm, crossings 2 / 0. A placket at 180 does not flare; the jacket (36 lines) no worse in 2 runs each
        # (experiments/fold_to_hem_2026-10-28). Only fold lines with an angle (solver FOLD_SET_EDGES); cfg_override
        # {'fold_ke_scale': null} takes it off
        cfg['fold_ke_scale'] = FOLD_PRESS
    if stages and stages[-1][0].startswith('close the seams'):
        stages[-1][1].update(stitch_rest_mm=0.3, air_damp=0.9, settle={'mm': 0.1, 'frames': 10}, frames=300)
        if len(stages) > 1 and stages[-2][1].get('gravity_z', -9.81) != 0.0:
            stages[-2][1]['frames'] = int(round(stages[-2][1]['frames'] * 4 / 3))
    return stages


def groups(spec, names):
    """(body, collar, sleeves, top). By the pieces' roles (core/roles.py: the unit of each role) where they are set;
    a piece without one goes by its placement's guide as before roles existed (arm -> sleeves, neck -> collar, the
    rest body) and by role 'top' (now the role 'trim') to the accessories laid on last."""
    pl = spec.get('placement', {})
    sleeves = [n for n in names if pl.get(n, {}).get('guide', '').startswith('arm')]
    collar = [n for n in names if pl.get(n, {}).get('guide') == 'neck']
    body = [n for n in names if n not in sleeves and n not in collar]
    return RO.groups(spec, names, (body, collar, sleeves))


def drop_arms(spec):
    """Arm-mask specs for the solver ([o, direction, radius, |x| beyond]), from the arm guides (mm)."""
    # |x| beyond 1.4 x the torso's half width under the arm, and nothing above the armpit: the shoulder tops stay,
    # or the body has nothing to hang on (with 1.04 x and no height limit the shirt sank into the shoulders, 125
    # vertices inside; the experiment's jacket used 125 mm and a height limit)
    out = []
    body = spec.get('body', {})
    half = float(body.get('torso_half_width_armpit', 120.0))
    zmax = float(body.get('z_armpit', 1e9))
    for k in ('arm_L', 'arm_R'):
        g = spec.get('guides', {}).get(k)
        if g is None:
            continue
        o = np.asarray(g['o'], float)
        ez = np.asarray(g['R'], float)[2]
        out.append([*np.round(o, 2).tolist(), *np.round(-ez, 4).tolist(), round(1.35 * g['r'], 1),
                    round(1.4 * half, 1), round(zmax, 1)] + ([body['torso_profile']] if body.get('torso_profile') else []))
    return out


def fold_deg(spec, names):
    """{"piece|line": deg} for the folds set on internal lines of the given pieces."""
    return {f"{f['piece']}|{f['line']}": float(f['deg']) for f in spec.get('folds', [])
            if f.get('line') and f.get('piece') in names and f.get('deg') is not None}


def _with_terminal_field(base, spec, names):
    if spec.get('experimental_fold_terminal') or any(f.get('end_taper_mm') for f in spec.get('folds',[])):
        from .fold_reference import terminal_field
        field=terminal_field(spec,names)
        if field:base['experimental_fold_terminal']=field


def _scope_terminal_stages(stages):
    for _,cfg in stages:
        if cfg.get('experimental_fold_terminal'):
            from .fold_reference import scope_terminal_field
            cfg['experimental_fold_terminal']=scope_terminal_field(cfg['experimental_fold_terminal'],cfg.get('active_pieces'),cfg.get('fold_deg',{}))
    return stages


def fit_terminal(cfg, local=False, meshed=None):
    """The end tapers (cfg experimental_fold_terminal) the solver would stop on, left out before it runs (user
    2026-11-01: "silently drop what the solver does not take, said in one line"; Fable review the same day). Scoped
    again to the stage as it is now (a band laid, Local Sim's selection), then a line is left out on a weld, under a
    fold profile or a free fold, on a piece turned (fold_turn), held in one layer (one_layer) or bound (bind_verts).
    A piece under fold_ramp keeps its taper in Local Sim (the ramp, Local's own easing, goes for that piece) and
    loses it otherwise (the recipe's ramp folds the collar). meshed: in a running Live, the lines it was started with
    (a line drawn since has no hinges in it). Returns {key: why} of the lines left out here."""
    tf = cfg.get('experimental_fold_terminal')
    if not tf:
        return {}
    from .fold_reference import scope_terminal_field
    tf = scope_terminal_field(tf, cfg.get('active_pieces'), cfg.get('fold_deg', {}))
    turn = set((cfg.get('fold_turn') or {}).get('pieces', []))
    ramp = dict(cfg.get('fold_ramp') or {})          # a copy: a stage's dict may be shared with another stage
    ol = {r['piece'] for r in cfg.get('one_layer') or []}
    bound = {b['piece'] for b in cfg.get('bind_verts') or []}
    prof, free = set(cfg.get('fold_profile') or {}), set(cfg.get('fold_free') or [])
    out = {}
    for key in list(tf['lines']):
        p = key.split('|')[0]
        why = ('not in this run' if meshed is not None and key not in meshed else 'weld' if cfg.get('weld') else 'fold profile' if key in prof else 'free fold' if key in free
               else 'turned' if p in turn else 'one layer' if p in ol else 'bound' if p in bound else None)
        if why is None and p in ramp.get('pieces', []):
            if local:
                ramp['pieces'] = [n for n in ramp['pieces'] if n != p]
            else:
                why = 'folded by the stage'
        if why:
            out[key] = why
    if cfg.get('fold_ramp'):
        if ramp.get('pieces'):
            cfg['fold_ramp'] = ramp
        else:
            cfg.pop('fold_ramp', None)
    if out:
        content = {k: v for k, v in tf.items() if k != 'sha256'}
        content['lines'] = {k: v for k, v in tf['lines'].items() if k not in out}
        content['excluded'] = dict(tf.get('excluded', {}), **out)
        tf = dict(content, sha256=hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'),
                                                            allow_nan=False).encode()).hexdigest())
    if tf['lines']:
        cfg['experimental_fold_terminal'] = tf
    else:
        cfg.pop('experimental_fold_terminal', None)
    return out


def seam_fold_deg(spec, names):
    """{seam id: deg} for the folds set on seams between the given pieces: the rest angle of the hinges across that
    seam only (+ = mountain seen from outside, as on internal lines). Per seam, not per pair of pieces (DECISIONS
    cloth-sim-seam-fold-2026-10-23): two pieces can share several seams (a back vent's top and the side seam), and
    by the pair the seam not folded got the angle too and a second angle on the same two pieces was dropped."""
    # the solver's seam hinges have the opposite sign (+ = the turning piece out, the collar turned down): placed
    # inwards at +30 the shirt's yoke started at -36.5 there and ended at -1.7..-4.3 (2026-10-23)
    return {str(sm['id']): -deg for sm, deg, _ in S.seam_folds(spec)
            if sm['a']['piece'] in names and sm['b']['piece'] in names}


def _with_seam_folds(base, spec, names):
    sf = seam_fold_deg(spec, names)
    if sf:
        # the fold hinges at 0.3 x the seam hinges' stiffness: at 1.0 the turned-down collar pulled the shirt's stand
        # into the neck (6 vertices inside; 0, 0, 0 in three runs at 0.3, the collar down at the back in all, 2026-09-29)
        base['seam_bend'] = dict(base.get('seam_bend') or BASE['seam_bend'], rest_deg_seams=sf, fold_ke=0.3)


def frozen(spec, names):
    return [p['name'] for p in spec['pieces'] if p.get('frozen') and p['name'] in names]


def hold_user_pieces(spec, stg):
    """The pieces a person stopped (Freeze, Off (Pattern)) are held in EVERY stage they are in, the no-gravity sewing
    stages included (the sewing stages used to hold only their own rings, so a frozen piece moved there; Fable review
    2026-10-13), and the Off (Pattern) ones are ghosts there too (solver ghost_pieces: nothing touches them)."""
    held = [p['name'] for p in spec['pieces'] if S.piece_state(p) in ('freeze', 'off_pattern')]
    ghost = [p['name'] for p in spec['pieces'] if S.piece_state(p) == 'off_pattern']
    for _label, cfg in stg:
        act = cfg.get('active_pieces')
        h = [n for n in held if act is None or n in act]
        if h:
            cfg['frozen_pieces'] = list(dict.fromkeys(list(cfg.get('frozen_pieces') or []) + h))
        g = [n for n in ghost if act is None or n in act]
        if g:
            cfg['ghost_pieces'] = g
    return stg


LIVE_FRAMES = 0                   # Live has no frame cap (user 2026-10-23): the solver runs it until stopped
SEW_RAMP_FRAMES = 60              # Ordinary sewing from the current shape (user 2026-10-30).


def live_params(spec, names, gravity, active_pieces=None):
    """What a Live stage takes while it runs (user 2026-10-23, solver live_params): the spec's values the person can
    change then -- the folds' angles, the seams switched off, the pieces held (Freeze), the whole-piece elastics --
    and gravity on / off. The add-on writes it to the job's params.json whenever it changes; the solver eases each
    change in. Changed in the spec, so they stay as the pieces' settings after the run."""
    from . import elastic as EL
    held = [p['name'] for p in spec['pieces'] if p['name'] in names
            and (p.get('frozen') or S.piece_state(p) == 'freeze')]
    result = {'gravity': bool(gravity), 'fold': fold_deg(spec, names),
            'seams_off': sorted(str(sm['id']) for sm in spec.get('seams', []) if not sm.get('enabled', True)),
            'frozen': sorted(held),
            'strengthen_pieces': S.strengthen_pieces(spec, names),
            'elastic': {e['piece']: e['ratio'] for e in EL.solver_entries(spec, names, []) if 'line' not in e}}
    from .fold_reference import terminal_field
    result['fold_terminal']=terminal_field(spec,active_pieces if active_pieces is not None else names)
    return result


def continue_stages(spec, names, pattern_path, crop, new=(), live=False):
    """From the last result: gravity with the frozen pieces held, then close the seams (freeze and
    simulate again). live (user 2026-10-07): one stage that runs until it is stopped (Esc), with
    no frame cap and only its last shape kept (user 2026-10-23), gravity on, the frozen pieces held; the cloth can be grabbed all the while."""
    base = dict(BASE, pattern=pattern_path, crop=crop, iterations=20, stitch_ke=1000, self_margin=0.001,
                self_gap=0.0005, body_mu=1.0)
    fd = fold_deg(spec, names)
    if fd:
        base['fold_deg'] = fd
        fp_ = {f"{f['piece']}|{f['line']}": f['profile'] for f in spec.get('folds', [])
               if f.get('profile') and f"{f.get('piece')}|{f.get('line')}" in fd}
        if fp_:
            base['fold_profile'] = fp_          # TEST (2026-10-10): the set angle along the line (solver fold_profile)
    _with_seam_folds(base, spec, names)
    _with_terminal_field(base, spec, names)
    fz = frozen(spec, names)
    if not any(pl.get('guide', '').startswith('arm') for pl in spec.get('placement', {}).values()):
        base['drop_arms'] = drop_arms(spec)
    # a seam switched on since, or a piece added since (an accessory, the front closure: the usual way of sewing them
    # on by hand after the body has settled) starts apart: close it over the ordinary sewing ramp
    if live:
        return 'live', [("live (until stopped)",
                         dict(base, frames=LIVE_FRAMES, live=True, active_pieces=names, frozen_pieces=fz,
                              stitch_ramp_frames=SEW_RAMP_FRAMES, live_every=2))]
    # Pieces added since are sewn on without gravity first, as a first Dress sews (user 2026-11-04): under gravity a
    # whole garment added over another (a jacket over a shirt, its shoulders 160-205 mm apart on the placement
    # cylinder) fell 14 mm while the ramp was weak and was then reeled in through the inner garment (21 cloth edges
    # through it, the shirt dragged up 20 mm). Without gravity -- which also brings the stitch cap of physical() --
    # 0 edges through, the shirt moved p90 5 mm, the same 60-frame ramp (experiments/claude_continue_nograv_2026-11-04).
    # The closing stage after it has gravity and runs until still.
    first = (("sew the new pieces (no gravity)",
              dict(base, frames=90, gravity_z=0.0, active_pieces=names, frozen_pieces=fz,
                   stitch_ramp_frames=SEW_RAMP_FRAMES, live_every=2))
             if new else
             ("continue (gravity)",
              dict(base, frames=90, active_pieces=names, frozen_pieces=fz, stitch_ramp_frames=SEW_RAMP_FRAMES,
                   live_every=2)))
    return 'continue', [
        first,
        ("close the seams", dict(base, frames=30, active_pieces=names, init_from_pieces=names, frozen_pieces=fz,
                                 stitch_ramp_frames=10, stitch_ke=5000, self_margin=0.0005, self_gap=0.0002,
                                 stitch_rest_mm=0.0)),
    ]


def stages(spec, names, rings, pattern_path, crop, kind=None):
    """[(label, config)] for the pieces `names`; rings = names of closed bands (settled first, then held)."""
    body, collar, sleeves, top = groups(spec, names)
    kind = kind or ('sleeved' if sleeves else 'generic')
    base = dict(BASE, pattern=pattern_path, crop=crop)
    fd = fold_deg(spec, names)
    if fd:
        base['fold_deg'] = fd
        fp_ = {f"{f['piece']}|{f['line']}": f['profile'] for f in spec.get('folds', [])
               if f.get('profile') and f"{f.get('piece')}|{f.get('line')}" in fd}
        if fp_:
            base['fold_profile'] = fp_          # TEST (2026-10-10): the set angle along the line (solver fold_profile)
    _with_seam_folds(base, spec, names)
    _with_terminal_field(base, spec, names)
    user_frozen = frozen(spec, names)
    out = []
    if kind == 'generic':
        # no sleeves: the arms are not in the collision body. With them in, an A-pose avatar's forearms reach down
        # beside the hips and the trousers ended with ~900 vertices inside the body even from the experiment's
        # clean start (the experiment cropped the body to |x| < 200 mm, which also dropped the arms)
        base['drop_arms'] = drop_arms(spec)
        first = rings or names
        if spec.get('tuck_first') and not rings and spec.get('tuck_pieces'):
            # TEST (opt-in, user + Fable r3 2026-10-09): a bodice whose back has pleats -- the other pieces sewn first
            # (no gravity, arms out), then the pleats folded with the rest held and nothing sewn between pieces, then
            # the pleated piece sewn to its partner (the yoke) only, with the rest held, then the other seams, then
            # gravity, then the closure (spec closures) and everything closed. Sewn elsewhere first, the side seams
            # pulled the back's top edge and the pleats collapsed into a tangle (user 2026-10-09)
            tk = [n for n in spec['tuck_pieces'] if n in names]
            partners = [n for n in spec.get('tuck_partners', []) if n in names]
            rest = [n for n in names if n not in tk]
            closures = RO.declared_closures(spec, names)
            shut = {tuple(sorted(c)) for c in closures}
            pairs = sorted({tuple(sorted((sm['a']['piece'], sm['b']['piece']))) for sm in spec.get('seams', [])
                            if sm.get('enabled', True) and sm['a']['piece'] != sm['b']['piece']
                            and sm['a']['piece'] in names and sm['b']['piece'] in names})

            def opn(keep):
                return [{"pieces": list(pr), "start": 100000, "frames": 1} for pr in pairs if not keep(pr)]
            ctl = dict(iterations=20, stitch_ke=1000, self_margin=0.001, self_gap=0.0005)
            out.append(("sew the rest (no gravity)",
                        dict(base, frames=70, gravity_z=0.0, active_pieces=rest, stitch_ramp_frames=20,
                             stitch_delay=opn(lambda pr: not (set(pr) & set(tk)) and pr not in shut), **ctl)))
            out.append(("fold the tucks (no gravity)",
                        dict(base, frames=60, gravity_z=0.0, active_pieces=names, init_from_pieces=rest,
                             frozen_pieces=rest, stitch_ramp_frames=20, stitch_delay=opn(lambda pr: False),
                             fold_ramp={"pieces": tk, "frames": 30}, carry_to_seam={"pieces": tk, "to": partners},
                             **ctl)))
            out.append(("sew the tucks to their partners (no gravity)",
                        dict(base, frames=80, gravity_z=0.0, active_pieces=names, init_from_pieces=names,
                             frozen_pieces=rest, stitch_ramp_frames=30,
                             stitch_delay=opn(lambda pr: bool(set(pr) & set(tk)) and bool(set(pr) & set(partners))),
                             **ctl)))
            out.append(("sew the other seams (no gravity)",
                        dict(base, frames=80, gravity_z=0.0, active_pieces=names, init_from_pieces=names,
                             stitch_ramp_frames=30, stitch_delay=opn(lambda pr: pr not in shut), **ctl)))
            out.append(("drape (gravity)", dict(base, frames=120, active_pieces=names, init_from_pieces=names,
                                                frozen_pieces=user_frozen, stitch_ramp_frames=0, body_mu=1.0,
                                                stitch_delay=opn(lambda pr: pr not in shut), **ctl)))
            out.append(("close the seams", dict(base, frames=60, iterations=20, stitch_ke=5000, self_margin=0.0005,
                                                self_gap=0.0002, active_pieces=names, init_from_pieces=names,
                                                frozen_pieces=user_frozen, stitch_ramp_frames=20, stitch_rest_mm=0.0,
                                                body_mu=1.0)))
            return kind, _scope_terminal_stages(out)
        if spec.get('tuck_first') and rings:
            # TEST (opt-in, user + Fable r3 2026-10-09, DECISIONS cloth-sim-tuck-sew-order-2026-10-09): the band, then
            # the tucks folded with the band held and nothing sewn between pieces, then the legs sewn to the held band,
            # then gravity and the legs' own seams, then the band let go. Sewn before they fold, the tucks' longer edge
            # is gathered evenly by the stitches and the tucks collapse; with the band's seams and the legs' seams
            # starting together (p4: frame 80) the waist took the legs' cloth in (19 -> 195)
            legs = [n for n in names if n not in rings]
            pairs = sorted({tuple(sorted((sm['a']['piece'], sm['b']['piece']))) for sm in spec.get('seams', [])
                            if sm.get('enabled', True) and sm['a']['piece'] != sm['b']['piece']
                            and sm['a']['piece'] in names and sm['b']['piece'] in names})
            OPEN_ALL = [{"pieces": list(pr), "start": 100000, "frames": 1} for pr in pairs]
            OPEN_LEGS = [{"pieces": list(pr), "start": 100000, "frames": 1} for pr in pairs
                         if not (set(pr) & set(rings))]
            out.append(("sew the bands (no gravity)",
                        dict(base, frames=60, iterations=10, stitch_ke=1000, self_margin=0.001, self_gap=0.0005,
                             gravity_z=0.0, active_pieces=rings)))
            out.append(("fold the tucks (no gravity)",
                        dict(base, frames=60, iterations=20, stitch_ke=1000, self_margin=0.001, self_gap=0.0005,
                             gravity_z=0.0, active_pieces=names, init_from_pieces=rings, frozen_pieces=rings,
                             stitch_ramp_frames=20, stitch_delay=OPEN_ALL,
                             fold_ramp={"pieces": legs, "frames": 30})))
            out.append(("sew the legs to the band (no gravity)",
                        dict(base, frames=80, iterations=20, stitch_ke=1000, self_margin=0.001, self_gap=0.0005,
                             gravity_z=0.0, active_pieces=names, init_from_pieces=names, frozen_pieces=rings,
                             stitch_ramp_frames=30, stitch_delay=OPEN_LEGS)))
            out.append(("drape (gravity)", dict(base, frames=120, iterations=10, stitch_ke=1000, self_margin=0.001,
                                                self_gap=0.0005, active_pieces=names, init_from_pieces=names,
                                                frozen_pieces=rings + user_frozen, stitch_ramp_frames=40, body_mu=1.0)))
            out.append(("close the seams", dict(base, frames=60, iterations=20, stitch_ke=5000, self_margin=0.0005,
                                                self_gap=0.0002, active_pieces=names, init_from_pieces=names,
                                                frozen_pieces=user_frozen, stitch_ramp_frames=20, stitch_rest_mm=0.0,
                                                body_mu=1.0)))
            return kind, _scope_terminal_stages(out)
        out.append(("sew the bands (no gravity)" if rings else "sew (no gravity)",
                    dict(base, frames=60 if rings else 140, iterations=10 if rings else 20, stitch_ke=1000,
                         self_margin=0.001, self_gap=0.0005, gravity_z=0.0, active_pieces=first,
                         **({} if rings else {"stitch_ramp_frames": 40}))))
        out.append(("drape (gravity)", dict(base, frames=120, iterations=10, stitch_ke=1000, self_margin=0.001,
                                            self_gap=0.0005, active_pieces=names, init_from_pieces=first,
                                            frozen_pieces=rings + user_frozen, stitch_ramp_frames=40, body_mu=1.0)))
        out.append(("close the seams", dict(base, frames=60, iterations=20, stitch_ke=5000, self_margin=0.0005,
                                            self_gap=0.0002, active_pieces=names, init_from_pieces=names,
                                            frozen_pieces=rings + user_frozen, stitch_ramp_frames=20, stitch_rest_mm=0.0,
                                            body_mu=1.0)))
    else:
        # the experiment's six stages (newton/run_sleeved.sh) at half its frames: the front closure stays open
        # until everything hangs, accessories ("top" pieces: a tie, a pocket flap) come on last
        base.update(iterations=20, stitch_ke=1000, self_margin=0.001, self_gap=0.0005, stitch_rest_mm=1.0)
        closures = RO.closures(spec, names)
        # a sleeve's own seam (the underarm, closing it into a tube) waits like the front closure. Placed open round
        # the arm, the seam's two edges are 84 mm apart across the arm; sewn with the sleeve's cap it pulled the
        # armhole into the arm and the shirt ended with 114 vertices in the shoulders (the experiment's sleeves
        # started with no seam across the arm and ended at 0, 2026-09-29). Once the sleeve hangs from the armhole
        # its edges meet under the arm. Cuffs and other closed bands are placed closed and keep theirs.
        tubes = sorted({sm['a']['piece'] for sm in spec.get('seams', []) if sm.get('enabled', True)
                        and sm['a']['piece'] == sm['b']['piece'] and sm['a']['piece'] in sleeves
                        and sm['a']['piece'] not in rings})
        closures = closures + [[n, n] for n in tubes]
        OPEN = [{"pieces": list(c), "start": 100000, "frames": 1} for c in closures]
        # spec['collar_delay']: the collar's seams close only once the neckline has come in (FINDINGS 4j-7, the
        # approved jacket's first stage): stand x body and collar x stand from frame 50, collar x fronts from 80, each
        # over 40 frames. Closed at once, the collar is pulled over the top of the bodice and lies inside it at the back
        COL = []
        if spec.get('collar_delay'):
            pl_ = spec.get('placement', {})
            nk = [n for n in names if pl_.get(n, {}).get('guide') == 'neck']
            prs = {}
            for sm in spec.get('seams', []):
                a_, b_ = sm['a']['piece'], sm['b']['piece']
                if sm.get('enabled', True) and a_ != b_ and a_ in names and b_ in names and (a_ in nk or b_ in nk):
                    k_ = tuple(sorted((a_, b_)))
                    prs[k_] = prs.get(k_, 0.0) + S.side_length(spec, sm['a'])
            # the stand is the neck piece sewn to the bodice along the most length (the neckline); the collar is sewn
            # to the bodice only at the notches (by the number of seams the collar came first, 2026-09-29)
            to_body = {n: sum(v for k_, v in prs.items() if n in k_ and not all(x in nk for x in k_)) for n in nk}
            stand = (RO.stand(spec, names, nk) or max(nk, key=lambda n: to_body[n])) if nk else None
            for (a_, b_) in sorted(prs):
                late = stand not in (a_, b_) and not (a_ in nk and b_ in nk)
                COL.append({"pieces": [a_, b_], "start": 80 if late else 50, "frames": 40})
        SHUT = [{"pieces": list(c), "start": 0, "frames": 20} for c in closures]
        bc = body + collar
        nts = bc + sleeves
        allp = nts + top
        arms = drop_arms(spec)

        # spec['recipe_frames'] = 'full': the experiment's frame counts (twice these; the stitches shortened over 40
        # frames) -- to compare a stage with the approved run before trying the halved counts
        FULL = 2 if spec.get('recipe_frames') == 'full' else 1

        def st(**kw):
            c = dict(base, **kw)
            c['frames'] = int(c['frames']) * FULL
            if c.get('stitch_ramp_frames'):
                c['stitch_ramp_frames'] = int(c['stitch_ramp_frames']) * FULL
            if not c.get('stitch_delay'):
                c.pop('stitch_delay', None)
            return c
        # TEST (user 2026-10-07: sew the collar flat, then fold it, as a tailor does): spec['collar_fold_after'] -- the
        # collar's fold lines are flat until the collar is on, then a stage of its own folds them to their set angle
        # (cfg fold_ramp) with the bodice held (and the stand too with spec['collar_fold_hold_stand']);
        # spec['collar_iron'] -- from the stage after that, the collar's shape then is its rest shape (cfg iron)
        FOLD_AFTER = bool(spec.get('collar_fold_after')) and bool(collar) and bool(spec.get('collar_after'))
        if FOLD_AFTER and base.get('fold_deg'):
            _fd_all = dict(base['fold_deg'])
            base['fold_deg'] = {k_: v_ for k_, v_ in _fd_all.items() if k_.split('|')[0] not in collar}
        if spec.get('collar_after') and collar:
            # TEST (user 2026-10-03: sew in a tailor's order, as the shirt's sleeves after its body): the bodice
            # alone first; then, the bodice held, the collar pieces sewn to each other at zero gravity, and only
            # then onto the neckline; then everything hangs. Sewn all at once, the collar floated unsewn for 50
            # frames while the neckline came in and was thrown about (5 of 5 runs broken, 2026-10-03)
            NB = []
            for sm in spec.get('seams', []):
                a_, b_ = sm['a']['piece'], sm['b']['piece']
                if sm.get('enabled', True) and a_ != b_ and ((a_ in collar) != (b_ in collar)) \
                        and a_ in names and b_ in names:
                    NB.append({"pieces": [a_, b_], "start": 40, "frames": 40})
            out += [
                ("body, arms out (no gravity)",
                 st(frames=70, gravity_z=0.0, active_pieces=body, drop_arms=arms, stitch_ramp_frames=20,
                    stitch_delay=OPEN)),
                ("body (gravity)", st(frames=75, active_pieces=body, init_from_pieces=body, stitch_ramp_frames=20,
                                      body_mu=1.0, stitch_delay=OPEN)),
            ]
            if spec.get('collar_first'):
                # TEST (user 2026-10-03): the collar pieces sewn to each other first, in a stage of their own, and
                # only then onto the neckline -- in one stage the two closings overlapped and the unit was not whole
                # when it started to be pulled to the body
                OFFB = [dict(x, start=100000, frames=1) for x in NB]
                out += [("collar pieces to each other (no gravity)",
                         st(frames=40, gravity_z=0.0, active_pieces=bc, init_from_pieces=body, frozen_pieces=body,
                            stitch_ramp_frames=20, body_mu=1.0, stitch_delay=OPEN + OFFB)),
                        ("collar on the held body (no gravity)",
                         st(frames=60, gravity_z=0.0, active_pieces=bc, init_from_pieces=bc, frozen_pieces=body,
                            stitch_ramp_frames=30, body_mu=1.0, stitch_delay=OPEN))]
            else:
                out += [("collar on the held body (no gravity)",
                         st(frames=60, gravity_z=0.0, active_pieces=bc, init_from_pieces=body, frozen_pieces=body,
                            stitch_ramp_frames=30, body_mu=1.0, stitch_delay=OPEN + NB))]
            if FOLD_AFTER:
                hold = list(body)
                if spec.get('collar_fold_hold_stand'):
                    # the stand: the neck piece sewn to the bodice along the most length (as collar_delay finds it)
                    _tb = {n: sum(S.side_length(spec, sm['a']) for sm in spec.get('seams', [])
                                  if sm.get('enabled', True) and n in (sm['a']['piece'], sm['b']['piece'])
                                  and ({sm['a']['piece'], sm['b']['piece']} - {n}) <= set(body)
                                  and sm['a']['piece'] != sm['b']['piece']) for n in collar}
                    hold = body + [max(collar, key=lambda n: _tb[n])]
                # spec['collar_fold_how'] (TEST 2026-10-07, RESEARCH_fold_and_grab): 'ramp' (default) the fold lines'
                # target deepened over 40 frames; 'turn' / 'cyl' the far side turned at the stage's start, not
                # simulated (each vertex round its nearest bit of the fold line / prefold's cylinder round the neck);
                # 'carry' the same turn carried out over 20 frames with the far side held (solver/fold_turn.py)
                how = spec.get('collar_fold_how', 'ramp')
                fk = (dict(fold_ramp={"pieces": collar, "frames": 40}) if how == 'ramp' else
                      dict(fold_turn={"pieces": collar, "mode": how, "frames": 20,
                                      "axis_xy": list(spec.get('guides', {}).get('neck', {}).get('o', [0, 0]))[:2]}))
                out += [("fold the collar (no gravity)",
                         st(frames=60, gravity_z=0.0, active_pieces=bc, init_from_pieces=bc, frozen_pieces=hold,
                            stitch_ramp_frames=0, body_mu=1.0, stitch_delay=OPEN, fold_deg=_fd_all, **fk))]
                _late = dict(fold_deg=_fd_all)
                if spec.get('collar_iron'):
                    _late['iron'] = {"pieces": collar, "seams": True}
                base.update(_late)
            out += [
                ("body and collar (gravity)", st(frames=75, active_pieces=bc, init_from_pieces=bc,
                                                 stitch_ramp_frames=0, body_mu=1.0, stitch_delay=OPEN)),
            ]
        else:
            out += [
                ("body and collar, arms out (no gravity)",
                 st(frames=70, gravity_z=0.0, active_pieces=bc, drop_arms=arms, stitch_ramp_frames=20,
                    stitch_delay=OPEN + COL)),
                ("body and collar (gravity)", st(frames=75, active_pieces=bc, init_from_pieces=bc, stitch_ramp_frames=20,
                                                 body_mu=1.0, stitch_delay=OPEN)),
            ]
        if sleeves:
            # no sleeves (a bodice tried on its own first, the order the jacket was built in): skip the sleeve stages
            out += [
                ("sleeves (no gravity)", st(frames=60, gravity_z=0.0, active_pieces=nts, init_from_pieces=bc,
                                            stitch_ramp_frames=30, body_mu=1.0, stitch_delay=OPEN)),
                ("everything (gravity)", st(frames=75, active_pieces=nts, init_from_pieces=nts, stitch_ramp_frames=0,
                                            body_mu=1.0, stitch_delay=OPEN)),
            ]
        if top or closures:
            out.append(("accessories on, front closed", st(frames=75, active_pieces=allp, init_from_pieces=nts,
                                                             stitch_ramp_frames=20, body_mu=1.0, stitch_delay=SHUT)))
        out.append(("close the seams", st(frames=30, active_pieces=allp, init_from_pieces=allp, stitch_ramp_frames=10,
                                          body_mu=1.0, stitch_ke=5000, self_margin=0.0005, self_gap=0.0002,
                                          stitch_rest_mm=0.0)))
    return kind, _scope_terminal_stages(out)
