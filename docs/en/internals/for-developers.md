---
title: For Developers
sidebar_position: 5
---

# For Developers

A map for automating with scripts or changing the add-on. The source is GPL-3.0-or-later (some parts, such as
`solver/newton_patch/`, are Apache-2.0; see each folder's `LICENSE`).

## Three layers

| Layer | Where | Uses Blender (bpy) | What it does |
|---|---|---|---|
| Pattern logic | `core/` | no | reads and edits the pattern data (a JSON dict): drafting, seams, fabrics, sewing order. Testable without Blender |
| Blender side | the `.py` files at the top | yes | makes the objects; tools, panels, overlays, selection |
| Cloth simulation | `solver/` | no | runs in a separate process, moves the cloth with NVIDIA Newton, returns the result as files |

## Main modules

| Module | What it holds |
|---|---|
| `store.py` | the one way in and out of the pattern data (text `AC9_ClothSim_Spec_<garment>`) |
| `core/spec.py` | the pattern data's layout and the functions that edit it; the table of piece state names `STATE_NAMES` |
| `core/draft.py` | drafting edits (move corners and edges, draw a piece, mirrored copy), keeping the seams valid |
| `pattern_view.py` | makes the flat pattern objects |
| `placed_view.py` | makes the placed objects: pairs' mirroring, origins, reading a piece moved by hand |
| `dress.py` | dressing: meshes the pieces, starts the simulation process, brings the result back to the garment |
| `selection.py` | reads and writes the piece selection ([Selection and the Gizmo](selection.md)) |
| `piece_move.py` | handles on the dressed garment's pieces |
| `toolbar.py`, `draft_tool.py` | the tools in the left toolbar |
| `i18n_ja.py` | the Japanese interface |

## Rules the code keeps

- **The pattern data is the truth.** Do not change flat pattern objects directly: edit the pattern data with the
  functions in `core/`, then write it back with `store.py`. The objects are made again.
- **Placed objects may be moved directly.** The simulation starts from the placed shapes on screen at the time.
- **Selection is written through `selection.py`.** Calling `select_set` elsewhere puts the 2D and 3D marks out of
  step. The places that write it directly, for a reason, are listed with the reason in
  `tests/test_selection_writers.py`.
- **A name shown on screen is read from one table.** The same string is not written twice (for example, piece states
  come from `STATE_NAMES` in `core/spec.py`). Renaming one changes its tests and the manual in the same change.
- **Names kept in a .blend** (object names, property names, JSON keys): when changing one, make sure older files still
  open.

## Tests

The tests in `tests/` run Blender in the background:

```
blender -b --factory-startup --python tests/test_<name>.py -- <folder holding ac9_cloth_sim>
```

## The manual for AI assistants

`LLM/` holds the manual an AI assistant reads. `LLM/README.md` is made from part of this manual (the Japanese one),
so do not edit it; edit `docs/ja/` instead.
