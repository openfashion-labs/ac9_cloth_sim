# SPDX-License-Identifier: GPL-3.0-or-later
"""Fold order and real authored correspondences; legacy IDs are read only."""
import base64
import json
import numpy as np
from . import draft as D, spec as S, fold_hand as FH
from ._i18n import rpt

PREFIX = '__ac9_mirror_fold__'
MARKER = '_ac9_prepared_folds'
ORIGIN = '_ac9_fold_origin'


def ordered_folds(spec):
    """Keep independent fold order; a self-mirrored batch has one click-independent order."""
    rows = list(spec.get('folds', []))
    emitted, out = set(), []
    for f in rows:
        key = f.get('piece'), f.get('line')
        if key in emitted:
            continue
        group = {key}
        if key[0] and key[1] and not str(key[1]).startswith(PREFIX):
            group.update((p, l) for p, l, _ in D.mirror_lines(spec, *key) if p == key[0])
        batch = [r for r in rows if (r.get('piece'), r.get('line')) in group]
        out.extend(sorted(batch, key=lambda r: str(r.get('line'))))
        emitted.update(group)
    return out


def origin(spec, piece, line):
    if str(line).startswith(PREFIX):
        try:
            token = str(line)[len(PREFIX):]
            src, lid = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
            if D.twin_of(spec, src) == piece and D.internal(D.piece(spec, src), lid) is not None:
                return src, lid
        except (ValueError, TypeError, KeyError, StopIteration):
            pass
    return piece, line


def physical_line(spec, piece, line, owner):
    if owner == piece:
        return line
    if not any({piece, owner} == set(pair) for pair in S.linked_twins(spec)):
        return None
    match = D.twin_line(spec, piece, line)
    return match[1] if match and match[0] == owner else None
