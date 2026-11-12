# SPDX-License-Identifier: GPL-3.0-or-later
"""Operation-local physical shape records and absolute layout identity (millimeters)."""
from dataclasses import dataclass
import numpy as np


def same_layout(a, b, *, atol=1e-6):
    """Coordinate identity has an absolute tolerance; changing the layout origin cannot widen it."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a.shape == b.shape and bool(np.allclose(a, b, atol=atol, rtol=0))


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
    vertex_identity: bool
