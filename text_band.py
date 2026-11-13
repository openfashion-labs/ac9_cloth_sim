# SPDX-License-Identifier: GPL-3.0-or-later
"""Text the add-on draws in a 3D view, on a dark band (white text over the white cloth, the body and the light pattern
pieces was hard to read; the numbers on the pattern already sat on such a box, draft_tool.draw_labels). One batch for
all the bands, then the texts."""
import blf
import gpu
from gpu_extras.batch import batch_for_shader

BAND = (0.08, 0.08, 0.08, 0.72)
PAD = 3


def draw(fid, items):
    """items: [(x, y, text, rgba)] at the text's baseline-left in region pixels (blf's size already set)."""
    if not items:
        return
    tris = []
    _w, h = blf.dimensions(fid, "Ag")      # one height for every band (a text's own height follows its glyphs)
    for x, y, txt, _c in items:
        w, _h = blf.dimensions(fid, txt)
        x0, y0, x1, y1 = x - PAD, y - PAD - 1, x + w + PAD, y + h + PAD
        tris += [(x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)]
    gpu.state.blend_set('ALPHA')
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('color', BAND)
    batch_for_shader(sh, 'TRIS', {'pos': tris}).draw(sh)
    for x, y, txt, c in items:
        blf.color(fid, *c)
        blf.position(fid, x, y, 0)
        blf.draw(fid, txt)
    gpu.state.blend_set('NONE')
