# SPDX-License-Identifier: GPL-3.0-or-later
"""Which vertices a piece's Layer reaches. Pure numpy: the solver (newton_vbd.py) uses it to leave vertices out of the
layer's side, and the add-on (dress.py) uses the same function before a run to say which pieces' Layer reaches few
or none of their vertices.

Left out: the vertices of a stitch joining two layers (a seam or a button between a layer-1 placket and a layer-0
front) and those within `rings` triangle rings of them. The stitch pulls them through each other's side, so a side
held there kept them wrong for good (a shirt's placket: 40 stuck vertices, 24 of 47 on a stitch, crossings
23 -> 105). They keep the usual two-sided contact. A narrow piece sewn along its edge to a piece of another layer can
lose every vertex this way (a 23 mm placket sewn to its front: all 242 left out at 3 rings), and then its Layer does
nothing: said before the run, not left silent."""
import numpy as np

SEAM_RINGS = 3          # the solver's default (cfg layers_seam_rings)
FEW = 0.5               # a piece whose Layer reaches under this share of its vertices is said too


def left_out(tris, stitches, lay, rings=SEAM_RINGS):
    """Bool per vertex: left out of the layer's side. tris (m, 3) and stitches (k, 2) index the same vertices as
    lay (n,), each vertex's layer."""
    lay = np.asarray(lay)
    ex = np.zeros(len(lay), bool)
    st = np.asarray(stitches, np.int64).reshape(-1, 2)
    st = st[lay[st[:, 0]] != lay[st[:, 1]]] if len(st) else st
    if len(st):
        ex[st.ravel()] = True
        tl = np.asarray(tris, np.int64).reshape(-1, 3)
        for _ in range(int(rings)):
            ex[tl[ex[tl].any(1)].ravel()] = True
    return ex


def exact(cfg):
    """True when a stage's config makes the solver leave out exactly left_out() over the active pieces' stitches (the
    scene's E_st between active pieces; seams sewn late are in it from the start, Live's switched-off seams in
    neither). False -- nothing is said before the run -- when the solver may do otherwise: layers_pair_exclusions
    (on by default with bound coupling) decides by pairs with layer_pairs instead; a fold solved as one layer or
    bound vertices (one_layer, bind_verts) drop or move stitches first; weld renumbers them."""
    if cfg.get('layers_pair_exclusions') or cfg.get('weld') or cfg.get('bind_verts'):
        return False
    return not any(len(g.get('verts', ())) for g in (cfg.get('one_layer') or []))


def per_piece(ex, v_piece, names, layers):
    """{piece: [reached, vertices]} for the pieces with a Layer other than 0 (layers: {piece: layer})."""
    v_piece = np.asarray(v_piece)
    out = {}
    for i, n in enumerate(names):
        if int(layers.get(n, 0) or 0):
            m = v_piece == i
            out[n] = [int((m & ~ex).sum()), int(m.sum())]
    return out


def short(reach):
    """The pieces whose Layer reaches none or few of their vertices: [(piece, reached, vertices)]."""
    return [(n, r, t) for n, (r, t) in sorted(reach.items()) if t and r < FEW * t]
