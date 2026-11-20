# SPDX-License-Identifier: GPL-3.0-or-later
"""Operation-local physical shape records and absolute layout identity (millimeters)."""
from dataclasses import dataclass
import numpy as np


def same_layout(a, b, *, atol=1e-6):
    """Coordinate identity has an absolute tolerance; changing the layout origin cannot widen it."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a.shape == b.shape and bool(np.allclose(a, b, atol=atol, rtol=0))


CARRY_ATOL = 1e-3       # mm: two layouts this close point by point are one mesh (ac9_flat is float32)


def carry(dst_flat, src_flat, src_tris, src_world, mapper, *, atol=CARRY_ATOL):
    """(positions (n, 3) for the layout points dst_flat, taken vertex by vertex) -- the one way a shape is put on
    another mesh of the same piece. The shape is (src_flat layout points, src_tris, src_world); mapper(Q, V, T, X)
    gives the positions of layout points Q on it (dress._bary_map).

    Vertex k of the source is taken as vertex k of the target only when the two layouts are the same, point by point.
    Nothing else proves it: a mesh made again can keep its vertex count and number its vertices otherwise (a
    neighbor piece switched Off re-samples a seam's outline: 2986 of 3092 vertices renumbered, up to 606 mm apart),
    and a mark written when the mesh was made (a mirrored half, a vertex identity flag, a layout key) does not know
    the mesh was made again. Taken vertex by vertex there, the cloth started torn (edges to 623 mm). So the shortcut
    is decided here, from the layouts, and a failed shortcut falls back to the mapping (still right, only slower)."""
    dst = np.asarray(dst_flat, float)
    W = np.asarray(src_world, float)
    if len(W) == len(dst) and same_layout(src_flat, dst, atol=atol):
        return W.copy(), True
    return mapper(dst, np.asarray(src_flat, float), np.asarray(src_tris, np.int64), W), False


@dataclass(frozen=True)
class PlacedShape:
    piece: str
    object_uid: int
    mesh_uid: int
    provenance: tuple
    flat: np.ndarray
    triangles: np.ndarray
    world_mm: np.ndarray
    made_vertex_count: int | None
    layout_key: str | None
