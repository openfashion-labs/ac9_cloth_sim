# SPDX-License-Identifier: GPL-3.0-or-later
"""Piece roles: what each piece of a garment is (a front, a placket, a collar stand, a cuff ...), proposed from the
pattern's own shape and the source's hints, and what the stages make of it (no bpy).

Design: 2609_Apparel_CAD/DESIGN_piece_roles_2026-10-09.md (user OK 2026-10-09, DECISIONS cloth-sim-piece-roles):
- a person (or the AI companion) picks a piece's role from a closed vocabulary (VOCAB, about 20 words); where it is
  placed, how its seams are treated and in which stage it is sewn follow from the role and the pattern's shape
- the role is proposed without any AI: the pattern's shape (facts) and the source's hints (the arrangement point,
  piece and fabric names, the person's placement); the AI companion may add its own guess on top. Only pieces
  proposed with low confidence or with hints that disagree are asked about (ask)
- a role set by a person (role_by "user") is never overwritten by a proposal
- the simulation never calls an AI: it reads the roles written in the spec

Spec fields (core/spec.py docstring): spec['garment'] = {"kind", "by", "conf"}; per piece "role" (an id of VOCAB;
the old "auto" = "other", "top" = "trim"), "role_by" ("rule" | "source" | "ai" | "user"), "role_conf", "role_why".
"""
import re

import numpy as np

from . import spec as S

# id: (Japanese, unit). The unit is the group the stages sew it in (UNIT_GROUP).
VOCAB = {
    'front': ("前身頃", 'body'),
    'back': ("後ろ身頃", 'body'),
    'side': ("脇身頃", 'body'),
    'yoke': ("ヨーク", 'body'),
    'placket': ("前立て", 'body'),
    'facing': ("見返し", 'body'),
    'stand': ("台衿", 'neck'),
    'collar': ("衿", 'neck'),
    'neckband': ("衿リブ", 'neck'),
    'sleeve': ("袖", 'sleeve'),
    'sleeve_part': ("袖の切替", 'sleeve'),
    'sleeve_placket': ("剣ボロ", 'sleeve'),
    'cuff': ("カフス", 'sleeve'),
    'waistband': ("ウエスト帯", 'waist'),
    'leg_front': ("前パンツ", 'leg'),
    'leg_back': ("後ろパンツ", 'leg'),
    'hem_band': ("裾の別布", 'leg'),
    'skirt_panel': ("スカート", 'body'),
    'pocket': ("ポケット", 'trim'),
    'flap': ("フラップ", 'trim'),
    'tie': ("タイ", 'trim'),
    'loop': ("ベルト通し", 'trim'),
    'trim': ("付属", 'trim'),
    'other': ("その他", 'other'),
}
# the add-on's UI labels (English, as the rest of the panel); the Japanese names above are for people and the companion
EN = {'front': "Front", 'back': "Back", 'side': "Side Panel", 'yoke': "Yoke", 'placket': "Placket", 'facing': "Facing",
      'stand': "Collar Stand", 'collar': "Collar", 'neckband': "Neckband", 'sleeve': "Sleeve", 'sleeve_part': "Sleeve Panel",
      'sleeve_placket': "Sleeve Placket", 'cuff': "Cuff", 'waistband': "Waistband", 'leg_front': "Front Leg",
      'leg_back': "Back Leg", 'hem_band': "Hem Band", 'skirt_panel': "Skirt Panel", 'pocket': "Pocket", 'flap': "Flap",
      'tie': "Tie", 'loop': "Belt Loop", 'trim': "Accessory", 'other': "Other"}
GARMENTS = {'shirt': "Shirt", 'jacket': "Jacket", 'top': "Top", 'pants': "Pants", 'skirt': "Skirt",
            'other': "Other"}   # shown in English; the Japanese is i18n_ja
# the stages' groups (recipe.groups): body, collar, sleeves, top (laid on last); 'other' keeps the guide's group
UNIT_GROUP = {'body': 'body', 'waist': 'body', 'leg': 'body', 'neck': 'collar', 'sleeve': 'sleeves', 'trim': 'top'}
ASK_BELOW = 0.4          # asked under this (b2, user 2026-10-09): on 6 test garments every wrong proposal was <= 0.2 (roles_dev)
LEGACY = {'auto': 'other', 'top': 'trim', None: 'other', '': 'other'}


# the roles a person gives that change how a piece is placed (user 2026-11-02, DECISIONS
# cloth-sim-no-auto-judgment-default-rules-2026-11-02): nothing is told from a piece's shape; a piece with none of
# these roles is placed as any other piece
BAND_ROLES = ('waistband', 'cuff', 'hem_band', 'stand', 'collar', 'neckband')   # goes round as a ring of its own length
ARM_PANEL_ROLES = ('sleeve', 'sleeve_part')                         # round an arm side by side with the others


# how a piece starts against the pieces it is sewn to (user 2026-11-02, DECISIONS cloth-sim-lay-by-role-2026-11-02;
# Fable docs/reviews/REVIEW_fable_start_pose_roles_2026-11-02.md): written on the piece, never decided while running.
# 'placed' as placed (a piece with no role always); 'edge' laid on the sewn edge by Phase Sim after the body is sewn
# (Band on Edge's laying; the placed shape stays a ring).
# 'seam' (carried onto its seams whenever placed) is gone (user 2026-11-03, DECISIONS cloth-sim-no-start-at-seam-2026-11-03):
# it moved pieces after every Place without a sign of it, only the dragged side of a pair, and nothing measured it
# better than placing them. Part to Seam is a button that acts once. A 'seam' an older file holds reads as 'placed'.
START = ('placed', 'edge')
START_EN = {'placed': "As Placed", 'edge': "On Edge"}
# what Propose Roles / Set Role write for a role (a person's choice, start_by 'user', is kept)
START_DEFAULT = {'neckband': 'edge'}


def start_of(pc):
    s = pc.get('start')
    return s if s in START else 'placed'


def default_start(pc):
    """The start the role's table gives the piece, unless a person chose one."""
    if pc.get('start_by') == 'user':
        return
    pc['start'] = START_DEFAULT.get(role_of(pc), 'placed') if pc.get('role') else 'placed'
    pc['start_by'] = 'rule'


def set_start(spec, piece, start, by='user'):
    """One piece's start, set by a person; its mirrored twin too."""
    if start not in START:
        raise ValueError(f"unknown start {start!r} (one of {', '.join(START)})")
    names = _with_family(spec, piece)
    pcs = S.piece_index(spec)
    for n in names:
        pcs[n].update(start=start, start_by=by)
    return names


def _with_family(spec, piece):
    pc = S.piece_index(spec)[piece]
    names = [piece]
    fam = (pc.get('bom') or {}).get('family') or pc.get('family')
    if fam:
        names += [p['name'] for p in spec['pieces'] if p['name'] != piece and p.get('enabled', True)
                  and ((p.get('bom') or {}).get('family') or p.get('family')) == fam]
    return names


def with_role(spec, roles):
    """The names of the pieces given one of `roles`."""
    return {pc['name'] for pc in spec.get('pieces', []) if pc.get('role') and role_of(pc) in roles}


def role_of(pc):
    r = pc.get('role')
    r = LEGACY.get(r, r)
    return r if r in VOCAB else 'other'


def unit_of(role):
    return VOCAB.get(role, VOCAB['other'])[1]


# ------------------------------------------------------------------------------------------------ facts

def _dir(P):
    d = np.asarray(P[-1], float) - np.asarray(P[0], float)
    n = float(np.linalg.norm(d))
    return d / n if n > 1e-9 else np.zeros(2)


def facts(spec):
    """Per enabled piece, what the pattern itself says (outline, seams; no meshing):
    w, h, area; sewn {partner: mm}; self_len (own outline sewn to itself, mm); double (a fold in two: the two long
    edges sewn to each other with a line halfway, the line id or ''); closed (sewn to itself end to end: a sleeve's
    underarm, a cuff, a band's ends); side_by_side {'L'/'R': partner} (a seam up the piece's side: the pieces of a
    ring round the body); twin (the other of a mirrored pair, by the BOM's copy family or spec family)."""
    on = {p['name']: p for p in spec['pieces'] if p.get('enabled', True)}
    ol = {n: S.Outline(p) for n, p in on.items()}
    F = {}
    for n, p in on.items():
        B = ol[n].B
        ar = abs(0.5 * float((B[:, 0] * np.roll(B[:, 1], -1) - np.roll(B[:, 0], -1) * B[:, 1]).sum()))
        F[n] = {'w': float(np.ptp(B[:, 0])), 'h': float(np.ptp(B[:, 1])), 'area': ar, 'perim': ol[n].L,
                'xm': float(0.5 * (B[:, 0].min() + B[:, 0].max())), 'sewn': {}, 'self_len': 0.0, 'self_pairs': [],
                'line_self': 0, 'double': '', 'closed': '', 'darts': 0, 'sides': {}, 'side_by_side': {}, 'across': {},
                'arrangement': (p.get('bom') or {}).get('arrangement', ''), 'name': n, 'fabric': p.get('fabric', ''),
                'family': (p.get('bom') or {}).get('family') or p.get('family') or '', 'twin': ''}
    for sm in spec.get('seams', []):
        if not sm.get('enabled', True):
            continue
        a, b = sm['a']['piece'], sm['b']['piece']
        if a not in on or b not in on:
            continue
        try:
            Pa = S.side_polyline(spec, sm['a'], ol)
            Pb = S.side_polyline(spec, sm['b'], ol)
        except Exception:
            continue
        La, Lb = S.polyline_len(Pa), S.polyline_len(Pb)
        if a == b:
            if 'line' in sm['a'] or 'line' in sm['b']:
                F[a]['line_self'] += 1                  # a dart / tuck / a fold over an internal line
                continue
            F[a]['self_len'] += 0.5 * (La + Lb)
            F[a]['self_pairs'].append((Pa, Pb))
            continue
        for x, y, P, L in ((a, b, Pa, La), (b, a, Pb, Lb)):
            F[x]['sewn'][y] = F[x]['sewn'].get(y, 0.0) + L
            F[x]['sides'].setdefault(y, []).append(P)
            if 'line' in (sm['a'] if x == a else sm['b']):
                continue
            fx = F[x]
            if np.ptp(P[:, 1]) >= 0.3 * fx['h'] and np.ptp(P[:, 0]) < np.ptp(P[:, 1]):
                fx['side_by_side'].setdefault('L' if P[:, 0].mean() < fx['xm'] else 'R', y)
            elif np.ptp(P[:, 0]) >= np.ptp(P[:, 1]):
                fx['across'][y] = fx['across'].get(y, 0.0) + L
    fam = {}
    for n, f in F.items():
        if f['family']:
            fam.setdefault(f['family'], []).append(n)
    for g in fam.values():
        if len(g) == 2:
            F[g[0]]['twin'], F[g[1]]['twin'] = g[1], g[0]
    for n, f in F.items():
        f['sewn_total'] = sum(f['sewn'].values())
        f['double'] = _double(on[n], f)
        f['closed'], f['darts'] = _closing(f) if not f['double'] else ('', 0)
    return F


def _closing(f):
    """How the piece's own outline is sewn to itself, other than a fold in two: ('tube', n) -- two long edges far
    apart (a sleeve's underarm, a trouser leg's inseam), ('ends', n) -- the two short ends of a strip (a cuff, a
    band); n darts (two edges meeting at a point, sewn to each other) are not a closing."""
    long_, short = max(f['w'], f['h']), min(f['w'], f['h'])
    kind, darts = '', 0
    for Pa, Pb in f['self_pairs']:
        ends = min(float(np.linalg.norm(Pa[i] - Pb[j])) for i in (0, -1) for j in (0, -1))
        d = float(np.linalg.norm(Pa.mean(0) - Pb.mean(0)))
        if ends < 3.0 and d < 0.3 * short:
            darts += 1
            continue
        L = 0.5 * (S.polyline_len(Pa) + S.polyline_len(Pb))
        if L >= 0.4 * long_:
            kind = 'tube'
        elif kind != 'tube' and d >= 0.6 * long_:
            kind = 'ends'
    return kind, darts


def _double(pc, f):
    """A piece folded in two along its length (a placket, a waistband): its own long edges sewn to each other,
    parallel and facing, over >= 60 % of its long side, with an internal line halfway between them (the fold).
    Returns the line id ('' if not)."""
    if not f['self_pairs']:
        return ''
    long_, short = max(f['w'], f['h']), min(f['w'], f['h'])
    ok, mids_a, mids_b = 0.0, [], []
    for Pa, Pb in f['self_pairs']:
        da, db = _dir(Pa), _dir(Pb)
        d = Pb.mean(0) - Pa.mean(0)
        dist = float(np.linalg.norm(d))
        par = abs(float(da @ db)) > np.cos(np.radians(20)) if len(Pa) > 1 and len(Pb) > 1 else True
        perp = dist > 1e-6 and abs(float(da @ (d / dist))) < 0.5
        if dist >= 0.6 * short and par and perp:
            ok += 0.5 * (S.polyline_len(Pa) + S.polyline_len(Pb)) if len(Pa) > 1 else 0.0
            mids_a.append(Pa.mean(0))
            mids_b.append(Pb.mean(0))
    if ok < 0.6 * long_ or not mids_a:
        # many short self seams (a band sewn in 6 mm bits): count the stretch they cover along the long axis
        if not mids_a:
            return ''
        A = np.array(mids_a)
        ax = 0 if f['w'] >= f['h'] else 1
        if float(np.ptp(A[:, ax])) < 0.6 * long_:
            return ''
    ma, mb = np.mean(mids_a, 0), np.mean(mids_b, 0)
    mid, half = 0.5 * (ma + mb), 0.5 * float(np.linalg.norm(mb - ma))
    nrm = (mb - ma) / max(2 * half, 1e-9)
    best = ('', 1e9)
    for ln in pc.get('internals', []):
        P = np.asarray(ln['pts'], float)
        if len(P) < 2:
            continue
        dl = _dir(P)
        if abs(float(dl @ nrm)) > 0.35:                 # the fold runs along the edges, not across them
            continue
        off = abs(float((P.mean(0) - mid) @ nrm))
        if off <= 0.15 * 2 * half and off < best[1]:
            best = (ln['id'], off)
    return best[0] or 'mid'                             # 'mid': no line drawn, fold halfway


# ------------------------------------------------------------------------------------------------ hints

# (pattern, role, weight) on the piece name and the fabric name, English and Japanese; first match per text wins
KEYWORDS = [
    (r'sleeve[s _-]*placket|剣ボロ|けんぼろ|短冊', 'sleeve_placket', 0.75),
    # a shirt's "neckband" is its collar stand; a rib by name is weak (cuffs and hems are ribbed too: the unit decides)
    (r'collar[ _-]*stand|neck[ _-]*band|stand[ _-]*collar|台衿|台襟', 'stand', 0.8),
    (r'placket|前立', 'placket', 0.75),
    (r'yoke|ヨーク', 'yoke', 0.8),
    (r'facing|見返', 'facing', 0.8),
    (r'cuff|カフス|カフ|袖口', 'cuff', 0.75),
    (r'neck[ _-]*rib|ネックリブ|衿リブ|襟リブ|\bribbing\b|\brib\b|リブ', 'neckband', 0.6),
    (r'collar|衿|襟|カラー', 'collar', 0.75),
    (r'waist[ _-]*band|waistband|ベルト|腰帯|ウエスト|帯', 'waistband', 0.75),
    (r'pocket|ポケット', 'pocket', 0.75),
    (r'flap|フラップ|雨蓋', 'flap', 0.75),
    (r'\btie\b|リボン|タイ', 'tie', 0.6),
    (r'loop|ループ|ベルト通し', 'loop', 0.7),
    (r'sleeve[s]?[ _-]*panel|under[ _-]*sleeve|外袖|内袖|袖切替', 'sleeve_part', 0.7),
    (r'sleeve|袖', 'sleeve', 0.7),
    (r'skirt|スカート', 'skirt_panel', 0.7),
    (r'side|脇', 'side', 0.45),
    (r'front|前身|前', 'front', 0.45),
    (r'back|後身|後ろ|後', 'back', 0.45),
]


def _keyword(text):
    t = (text or '').lower()
    for pat, role, w in KEYWORDS:
        if re.search(pat, t):
            return role, w
    return None, 0.0


def _arrangement(a):
    """The source's arrangement point name -> (unit, role or None, weight). The point says where on the avatar the piece
    was placed, so it is strong on the unit and the side, weak on the role within it."""
    a = a or ''
    if a.startswith('Neck_Collar'):
        return 'neck', 'collar', 0.7
    if a.startswith('Neck'):
        return 'neck', None, 0.8
    if a.startswith('Wrist'):
        return 'sleeve', 'cuff', 0.8
    if a.startswith(('Arm', 'Shoulder')):
        return 'sleeve', None, 0.8
    if a.startswith('Leg_Front'):
        return 'leg', 'leg_front', 0.8
    if a.startswith('Leg_Back'):
        return 'leg', 'leg_back', 0.8
    if a.startswith('Leg'):
        return 'leg', None, 0.8
    if 'Waist' in a:
        return 'waist', 'waistband', 0.6
    if a.startswith('Body_Front_Center'):
        return 'body', 'placket', 0.5
    if a.startswith('Body_Front'):
        return 'body', 'front', 0.5
    if a.startswith('Body_Back'):
        return 'body', 'back', 0.4
    if a.startswith('Body_Side'):
        return 'body', 'side', 0.5
    if a.startswith('Body'):
        return 'body', None, 0.6
    return None, None, 0.0


GUIDE_UNIT = {'neck': 'neck', 'arm_L': 'sleeve', 'arm_R': 'sleeve', 'leg_L': 'leg', 'leg_R': 'leg', 'torso': 'body'}


# ------------------------------------------------------------------------------------------------ propose

def propose(spec, F=None):
    """{'pieces': {piece: {'role', 'conf', 'why', 'ask', 'alts', 'unit'}}, 'garment': {...}} from the shape (F =
    facts) and the hints in the spec (arrangement point, names, placement). Deterministic; no AI.
    First each piece's unit (body / neck / sleeve / waist / leg / trim), then its role within the unit."""
    F = F or facts(spec)
    pl = spec.get('placement', {})
    ev = {n: {} for n in F}                       # piece -> role -> [(weight, why)]
    uev = {n: {} for n in F}                      # piece -> unit -> [(weight, why)]

    def add(d, n, k, w, why):
        d[n].setdefault(k, []).append((w, why))
    big = max([f['area'] for f in F.values()] or [1.0])
    # ---- hints: the source's arrangement point, names, the person's placement
    for n, f in F.items():
        u, r, w = _arrangement(f['arrangement'])
        if u:
            add(uev, n, u, w, f"元データの配置点 {f['arrangement']}")
        if r:
            add(ev, n, r, w, f"元データの配置点 {f['arrangement']}")
        for txt, lab, scale in ((n, "名前", 1.0), (f['fabric'], "生地名", 0.8)):
            r, w = _keyword(txt)
            if r:
                add(ev, n, r, w * scale, f"{lab}「{txt}」")
                add(uev, n, unit_of(r), 0.5 * w * scale, f"{lab}「{txt}」")
        g = pl.get(n, {}).get('guide')
        if g in GUIDE_UNIT:
            add(uev, n, GUIDE_UNIT[g], 0.6, f"置いたガイド {g}")
    # ---- the shape: units (b1: these work with no names, no hints, no AI)
    rings = _rings(F)
    # mirrored pairs of rings: round the two legs (the left one sewn to the right one at the crotch) or the two arms
    # (each sewn to the body)
    arm_r, leg_r, paired = [], [], set()
    for i, ra in enumerate(rings):
        for rb in rings[i + 1:]:
            if id(ra) in paired or id(rb) in paired or len(ra) != len(rb):
                continue
            if sorted(F[n]['twin'] for n in ra) != sorted(rb):
                continue
            paired |= {id(ra), id(rb)}
            if any(m in rb for n in ra for m in F[n]['sewn']):
                leg_r += [ra, rb]
            else:
                arm_r += [ra, rb]
    seq = next((r for r in rings if id(r) not in paired), [])
    ring = set(seq)
    legs_ring = set(seq) if _crotch(F, seq) else set()
    for r in leg_r:
        for n in r:
            add(uev, n, 'leg', 0.8, "左右対の輪で、左右が股で縫われる（脚）")
    for r in arm_r:
        top = max(r, key=lambda n: F[n]['area'])
        for n in r:
            add(uev, n, 'sleeve', 0.7, "左右対の輪で、それぞれ身頃に縫われる（腕）")
            add(ev, n, 'sleeve' if n == top else 'sleeve_part', 0.5 if n == top else 0.45,
                "腕の輪で一番大きい（袖）" if n == top else "腕の輪の一部（袖の切替）")
    for n in ring:
        if n in legs_ring:
            add(uev, n, 'leg', 0.8, "左右の対を股ぐりの曲線で縫う輪（ズボン）")
        else:
            add(uev, n, 'body', 0.7, "縦の縫い目で横に並び胴を回る")
    for n, f in F.items():
        strip = f['w'] >= 5 * f['h'] or f['h'] >= 5 * f['w']
        if (f['double'] or f['closed']) and f['w'] >= 6 * f['h'] and any(m in r for r in rings for m in f['across']):
            add(uev, n, 'waist', 0.8, "横に長い輪の帯で、胴の輪の上に縫われる")
        if (f['area'] < 0.1 * big and not strip and not f['closed'] and not f['double'] and n not in ring
                and len(f['sewn']) <= 3):
            add(uev, n, 'trim', 0.65, "小さく、縫われる相手が少ない（付属）")
    U = {}
    for n in F:
        u, _ = _best(uev[n])
        if u is None:
            r, _ = _best(ev[n])
            u = unit_of(r) if r else None
        U[n] = u
    # a small piece sewn only to the pieces of one unit other than the body (a sleeve placket, a cuff on a sleeve)
    # belongs to that unit, not to the accessories
    for n, f in F.items():
        if U[n] in ('trim', None):
            us = {U.get(m) for m in f['sewn']} - {None, 'trim'}
            if len(us) == 1 and next(iter(us)) in ('sleeve', 'neck'):
                U[n] = next(iter(us))
                add(uev, n, U[n], 0.6, "縫われる相手が全部" + VOCAB['sleeve' if U[n] == 'sleeve' else 'stand'][0][:1] + "の組")
    # ---- the shape: roles within a unit
    for n, f in F.items():
        if f['double']:
            add(ev, n, 'placket' if f['h'] >= f['w'] else 'waistband', 0.5, "長い辺どうしを縫い、真ん中に折れ線（二つ折り）")
    if len(seq) >= 2 and not _ring_closed(F, seq):          # an open ring: it opens at the front
        for k, n in enumerate(seq):
            end = k in (0, len(seq) - 1)
            nxt = k in (1, len(seq) - 2) and len(seq) >= 6
            r = 'front' if end else 'side' if nxt else 'back'
            add(ev, n, r, 0.55, "胴を回る輪の" + ("端（前で開く）" if end else "前の隣" if nxt else "真ん中"))
    neck = [n for n in F if U[n] == 'neck']
    if neck:
        to_body = {n: sum(v for m, v in F[n]['sewn'].items() if U.get(m) != 'neck') for n in neck}
        stand = max(neck, key=lambda n: to_body[n])
        if len(neck) > 1 and to_body[stand] > 0:
            add(ev, stand, 'stand', 0.6, "首のピースで身頃に最も長く縫われる")
            for n in neck:
                if n != stand and F[n]['sewn'].get(stand, 0) > 0.5 * F[n]['sewn_total']:
                    add(ev, n, 'collar', 0.6, "台衿に縫われる")
        else:
            f = F[stand]
            narrow = max(f['w'], f['h']) >= 4.0 * min(f['w'], f['h'])
            add(ev, stand, 'neckband' if narrow and to_body[stand] > 0 else 'collar', 0.4,
                "首のピースが 1 枚" + ("で身頃に縫われる細い帯" if narrow and to_body[stand] > 0 else ""))
    sleeve = [n for n in F if U[n] == 'sleeve']
    if sleeve:
        sb = max(F[n]['area'] for n in sleeve)
        for n in sleeve:
            f = F[n]
            if ((f['closed'] or f['line_self']) and (f['w'] >= 2.5 * f['h'] or f['h'] >= 2.5 * f['w'])
                    and f['area'] < 0.5 * sb):
                add(ev, n, 'cuff', 0.55, "袖の先で輪になる細長いピース")
            elif f['closed'] and f['area'] > 0.5 * sb:
                add(ev, n, 'sleeve', 0.6, "袖下で筒になる大きいピース")
            elif f['area'] < 0.12 * sb:
                add(ev, n, 'sleeve_placket', 0.4, "袖に縫う小さいピース")
            elif f['area'] >= 0.12 * sb and any(U.get(m) == 'sleeve' for m in f['sewn']):
                add(ev, n, 'sleeve_part', 0.45, "袖に縫われる袖の一部（筒ではない）")
    legs = [n for n in F if U[n] == 'leg']
    if legs:
        lb = max(F[n]['area'] for n in legs)
        main = [n for n in legs if F[n]['area'] > 0.4 * lb]
        for n in legs:
            f = F[n]
            if n not in main and f['w'] > 2.5 * f['h']:
                add(ev, n, 'hem_band', 0.5, "脚の裾に回る細長いピース")
        # the bigger pair of the legs is the back (the back crotch reaches further)
        pairs = sorted({tuple(sorted((n, F[n]['twin'] or n))) for n in main}, key=lambda t: -F[t[0]]['area'])
        if len(pairs) == 2:
            for k, pr in enumerate(pairs):
                for n in set(pr):
                    add(ev, n, 'leg_back' if k == 0 else 'leg_front', 0.35, "脚のピースで" + ("大きい方（後ろ）" if k == 0 else "小さい方（前）"))
    for n, f in F.items():
        if U[n] == 'body' and n not in ring and not f['double']:
            acr = f['across']
            to_ring = sum(v for m, v in acr.items() if m in ring)
            if len(acr) >= 2 and to_ring >= 0.6 * f['w'] and f['area'] >= 0.04 * big and f['w'] < 5 * f['h']:
                add(ev, n, 'yoke', 0.6, "身頃の輪の上に幅いっぱい横に縫われる（ヨーク）")
            if f['h'] >= 5 * f['w'] and any(m in ring for m in f['side_by_side'].values()):
                add(ev, n, 'placket', 0.55, "身頃の縁に縦に縫われる細長いピース（前立て）")
        if U[n] == 'waist' and (f['double'] or f['closed']):
            add(ev, n, 'waistband', 0.5, "腰で輪になる帯")
        if U[n] == 'trim':
            add(ev, n, 'pocket' if f['w'] < 3 * f['h'] and f['h'] < 3 * f['w'] else 'loop', 0.35,
                "小さい付属の形")
    # ---- decide, twins, the shape's veto
    out = {}
    for n in F:
        role, conf, why, alts = _decide(ev[n], U[n])
        out[n] = {'role': role, 'conf': conf, 'why': why, 'alts': alts, 'unit': U[n]}
    for n, f in F.items():
        t = f['twin']
        if t and t in out and out[t]['conf'] > out[n]['conf']:
            same = out[t]['role'] == out[n]['role']
            out[n] = dict(out[t], why=out[t]['why'] + ("" if same else f"（左右の対 {t} に合わせた）"))
    for n, f in F.items():
        exp = _expect(out[n]['role'], f)
        if exp:
            out[n]['conf'] = round(min(out[n]['conf'], 0.5), 2)
            out[n]['why'] += f"／食い違い: {exp}"
            out[n]['conflict'] = exp
        out[n]['ask'] = out[n]['conf'] < ASK_BELOW or bool(out[n].get('conflict'))
        out[n]['by'] = 'source' if any('配置点' in w or '名前' in w or '生地名' in w for w in [out[n]['why']]) else 'rule'
    kinds = {o['unit'] for o in out.values()}
    if 'leg' in kinds:
        kind = 'pants'
    elif 'sleeve' in kinds or 'neck' in kinds:
        kind = 'top'
    elif 'waist' in kinds or any(o['role'] == 'skirt_panel' for o in out.values()):
        kind = 'skirt'
    else:
        kind = 'other'
    return {'pieces': out, 'garment': {'kind': kind, 'by': 'rule', 'conf': 0.6}}


def _girth_hint(F):
    """A rough body girth from the ring pieces' widths (mm), 700 if there is no ring."""
    seq = _ring_seq(F)
    return sum(F[n]['w'] for n in seq) if len(seq) >= 2 else 700.0


def _best(d):
    if not d:
        return None, 0.0
    sc = {k: 1.0 - float(np.prod([1.0 - w for w, _ in v])) for k, v in d.items()}
    k = max(sc, key=sc.get)
    return k, sc[k]


UNIT_DEFAULT = {'body': ('front', "身頃（前後は分からない）"), 'neck': ('collar', "首のピース"),
                'sleeve': ('sleeve', "腕のピース"), 'leg': ('leg_front', "脚のピース（前後は分からない）"),
                'waist': ('waistband', "腰のピース"), 'trim': ('trim', "小さい付属")}


def _decide(evd, unit):
    """The role with the most evidence within the piece's unit (a role outside it only on a strong name, >= 0.7);
    confidence = its score less half the runner-up's."""
    sc = {k: 1.0 - float(np.prod([1.0 - w for w, _ in v])) for k, v in evd.items()}
    if unit:
        sc = {k: s for k, s in sc.items() if unit_of(k) == unit or s >= 0.7}
    if not sc:
        r, why = UNIT_DEFAULT.get(unit, ('other', "手がかりなし"))
        return r, 0.2, why, []
    order = sorted(sc, key=sc.get, reverse=True)
    k = order[0]
    second = sc[order[1]] if len(order) > 1 else 0.0
    conf = round(max(0.0, sc[k] - 0.5 * second), 2)
    why = "、".join(dict.fromkeys(w for _, w in evd[k]))
    return k, conf, why, [(o, round(sc[o], 2)) for o in order[1:3]]


def _expect(role, f):
    """What the shape should show for a role; a short reason when it does not (the shape's veto, design 4-2)."""
    if role in ('cuff', 'waistband') and not (f['closed'] or f['double'] or f['line_self']):
        return "輪になる縫い目が無い"
    if role == 'sleeve' and f['closed'] != 'tube' and f['line_self'] == 0 and f['sewn_total'] < 0.5 * f['perim']:
        return "袖下の縫い目が無い"
    return ''


def _ring_seq(F):
    """The pieces sewn side by side round the body, in order (the spec-level twin of dress._auto_ring's walk,
    without its girth test): from an end of an open ring (a jacket's fronts), or from any piece of a closed one."""
    rings = _rings(F)
    return rings[0] if rings else []


def _rings(F):
    """Every chain of pieces sewn side by side (biggest total width first): round the body, round an arm (a two-piece
    sleeve), round a leg. Strips (8 : 1) and pieces folded in two are not links: a placket sewn up a front's edge
    would otherwise sit in the ring between the fronts."""
    def link(n):
        f = F[n]
        return not f['double'] and f['w'] < 8 * f['h'] and f['h'] < 8 * f['w']
    nb = {}
    for n, f in F.items():
        if not link(n):
            continue
        for m in f['side_by_side'].values():
            if m in F and link(m) and n in F[m]['side_by_side'].values():
                nb.setdefault(n, set()).add(m)
    out, seen = [], set()
    while True:
        rest = [n for n in nb if n not in seen]
        if not rest:
            break
        seq = _walk(F, nb, rest)
        seen |= set(seq)
        # the whole component, to leave none of it for a later ring
        comp, st = set(seq), list(seq)
        while st:
            x = st.pop()
            for y in nb.get(x, ()):
                if y not in comp:
                    comp.add(y)
                    st.append(y)
        seen |= comp
        if len(seq) >= 2:
            out.append(seq)
    return sorted(out, key=lambda q: -sum(F[n]['w'] for n in q))


def _walk(F, nb, cand):
    best = []
    for start in sorted(cand, key=lambda n: (len(nb[n]), -F[n]['w'])):
        seq, cur, prev = [start], start, None
        while True:
            nxt = [m for m in nb[cur] if m != prev and m not in seq]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seq.append(cur)
        if len(seq) > len(best):
            best = seq
    return best


def _crotch(F, seq):
    """Trousers: in the ring, a mirrored pair sewn to each other along a seam that curves out sideways (the crotch
    curve: its sideways reach >= 30 % of its height) -- a skirt's or a jacket's center seam runs nearly straight."""
    for n in seq:
        t = F[n]['twin']
        if not t or t not in F[n]['side_by_side'].values():
            continue
        for P in F[n]['sides'].get(t, []):
            if np.ptp(P[:, 1]) >= 0.3 * F[n]['h'] and np.ptp(P[:, 0]) >= 0.3 * np.ptp(P[:, 1]):
                return True
    return False


def _ring_closed(F, seq):
    return len(seq) >= 2 and seq[0] in F[seq[-1]]['side_by_side'].values() and seq[-1] in F[seq[0]]['side_by_side'].values()


def apply(spec, proposal, overwrite_user=False):
    """Write the proposal into the spec (pieces' role / role_by / role_conf / role_why, spec['garment']). A role a
    person set (role_by 'user') is kept. Returns the names written."""
    done = []
    for pc in spec['pieces']:
        p = proposal['pieces'].get(pc['name'])
        if p is None or (pc.get('role_by') == 'user' and not overwrite_user):
            continue
        pc.update(role=p['role'], role_by=p.get('by', 'rule'), role_conf=p['conf'], role_why=p['why'],
                  role_ask=bool(p['ask']))
        default_start(pc)
        done.append(pc['name'])
    g = spec.get('garment') or {}
    if g.get('by') != 'user':
        spec['garment'] = dict(proposal['garment'])
    return done


def set_role(spec, piece, role, by='user', why=''):
    """One piece's role, set by a person (or the AI companion with by='ai'); its mirrored twin too."""
    if role not in VOCAB:
        raise ValueError(f"unknown role {role!r} (one of {', '.join(VOCAB)})")
    pcs = S.piece_index(spec)
    names = _with_family(spec, piece)
    for n in names:
        pcs[n].update(role=role, role_by=by, role_conf=1.0 if by == 'user' else pcs[n].get('role_conf', 0.5),
                      role_why=why or ("人が選んだ" if by == 'user' else ''), role_ask=False)
        default_start(pcs[n])       # the role's start comes with it (user 2026-11-02 b-2); a person's start is kept
    return names


# ------------------------------------------------------------------------------------------------ stages

def has_roles(spec, names):
    return any(role_of(p) != 'other' for p in spec['pieces'] if p['name'] in names)


def groups(spec, names, guide_groups):
    """(body, collar, sleeves, top) for the stages: a piece with a role goes to its unit's group; a piece without one
    ('other') keeps the group its guide gives (guide_groups = (body, collar, sleeves) as recipe found them), as
    before roles existed. Only the sleeved stages use these groups (accessories, 'top', on in the last stage); the
    generic stages (trousers, skirts) sew every piece from the start whatever its role, an accessory too, as before
    (checked: every piece is in some stage's active pieces, roles_dev/stages_dump.py)."""
    gb, gc, gs = guide_groups
    by_guide = {**{n: 'body' for n in gb}, **{n: 'collar' for n in gc}, **{n: 'sleeves' for n in gs}}
    pcs = S.piece_index(spec)
    out = {'body': [], 'collar': [], 'sleeves': [], 'top': []}
    for n in names:
        r = role_of(pcs.get(n, {}))
        g = UNIT_GROUP.get(unit_of(r)) if r != 'other' else None
        if g is None:
            g = 'top' if (pcs.get(n, {}).get('role') == 'top') else by_guide.get(n, 'body')
        out[g].append(n)
    return out['body'], out['collar'], out['sleeves'], out['top']


def closes_onto_pairs(spec, names=None):
    """The pieces' Closes onto (pieces[].closes_onto, set by hand, user 2026-10-22) whose two pieces are both placed
    on the torso guide: a front closure (Fable buttons pairing review §1-1). A strap onto a shoulder or a cuff onto a
    sleeve is only its buttons' hole partner, kept out of the closure's stages and the ring."""
    pl = spec.get('placement') or {}
    out = []
    for pc in spec.get('pieces', []):
        a, b = pc['name'], pc.get('closes_onto')
        if not b or a == b or (names is not None and not (a in names and b in names)):
            continue
        if (pl.get(a) or {}).get('guide') == 'torso' and (pl.get(b) or {}).get('guide') == 'torso':
            c = sorted([a, b])
            if c not in out:
                out.append(c)
    return out


def declared_closures(spec, names):
    """The front closures the spec says: spec['closures'] (From BOM before 2026-10-22, tests) and the torso pieces'
    Closes onto. Without a Closes onto this is spec['closures'] as before."""
    out = [list(c) for c in spec.get('closures') or [] if all(x in names for x in c)]
    for c in closes_onto_pairs(spec, names):
        if sorted(c) not in [sorted(x) for x in out]:
            out.append(c)
    return out


def closures(spec, names):
    """The declared closures (spec['closures'], the torso pieces' Closes onto) if any is set; else the front closure
    the roles give: two plackets sewn to each other."""
    if spec.get('closures') is not None or closes_onto_pairs(spec, names):
        return declared_closures(spec, names)
    pcs = S.piece_index(spec)
    pk = [n for n in names if role_of(pcs.get(n, {})) == 'placket']
    if len(pk) == 2 and any({sm['a']['piece'], sm['b']['piece']} == set(pk) for sm in spec.get('seams', [])
                            if sm.get('enabled', True)):
        return [pk]
    return []


def stand(spec, names, neck):
    """The collar stand among the neck pieces: the one with role 'stand', else None (the caller's length rule)."""
    pcs = S.piece_index(spec)
    st = [n for n in neck if role_of(pcs.get(n, {})) == 'stand']
    return st[0] if len(st) == 1 else None


def plan(spec, names, stages):
    """What the job says about the roles (job.json settings['plan'], the log): every piece's role and who set it,
    and per stage the pieces it sews. stages = [(label, cfg)]."""
    pcs = S.piece_index(spec)
    pieces = {n: {'role': role_of(pcs.get(n, {})), 'by': pcs.get(n, {}).get('role_by', ''),
                  'conf': pcs.get(n, {}).get('role_conf')} for n in names}
    st = [{'stage': lab, 'pieces': list(cfg.get('active_pieces') or []), 'frozen': list(cfg.get('frozen_pieces') or [])}
          for lab, cfg in stages]
    return {'garment': spec.get('garment'), 'pieces': pieces, 'stages': st,
            'ask': [n for n in names if pcs.get(n, {}).get('role_ask')]}


def plan_line(plan):
    """The simulation log's line about the roles: how many pieces have one, who set them, which are still to check."""
    P = plan.get('pieces', {})
    set_ = [n for n, p in P.items() if p['role'] != 'other' or p['by']]
    if not set_:
        return "roles: none set (stages by the placement's guides)"
    by = {}
    for n in set_:
        by[P[n]['by'] or 'spec'] = by.get(P[n]['by'] or 'spec', 0) + 1
    s = f"roles: {len(set_)} / {len(P)} pieces (" + ", ".join(f"{k} {v}" for k, v in sorted(by.items())) + ")"
    if plan.get('ask'):
        s += f"; to check: {', '.join(plan['ask'][:6])}" + (" ..." if len(plan['ask']) > 6 else "")
    return s
