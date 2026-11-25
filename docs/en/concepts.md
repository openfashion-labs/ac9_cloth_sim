---
title: Key Ideas
sidebar_position: 5
---

# Key Ideas

What trips people up in AC9 Cloth Sim is less where the buttons are and more "which shape am I looking at right now, and what am I fixing?" This chapter lays out those ideas.

## The pattern data is the source of truth (the flat pattern on screen is just a display)

Everything about one garment (the pattern shapes, seams, placement, fold lines, piece roles, and fabrics) is kept together in a single set of "pattern data" (a Blender text named `AC9_ClothSim_Spec_<garment name>`, whose contents are JSON).

- The flat patterns you see on screen are a **display** rebuilt from this data.
- That means **you can't fix a pattern with Blender's ordinary editing (Edit Mode, G/R/S).** Use AC9's tools instead.
  - Move or rotate a whole piece: **Whole Pieces**
  - Fix corners, edges, or points on internal lines: **Edit Pieces**
- Every time you load a pattern (Import BOM, Import DXF, From Mesh), one more set of pattern data (= one garment) is added. What the panels show belongs to the one garment you picked in the **Garment** field.

<!-- figure: Figure 2: the pattern data and what it creates in the scene -->

### What ends up in the scene

| Name | What it is |
|---|---|
| Collection `AC9_ClothSim_Pattern` | The flat patterns. One piece is one object (`AC9_Piece_<piece name>`) |
| `AC9_Garment_<garment name>` | The garment in its worn state. The last shape is the shape of this object itself |
| `AC9_Guide_<garment name>` | The garment you pass to Cloth Retopo (made with "Send to AC9 Cloth Retopo") |
| Text `AC9_ClothSim_Log` | The simulation log |

## A piece's four shapes, and the three "go back" buttons

We distinguish four stages of a piece. Without adjustments, its Placed Shape and Adjusted Shape coincide.

| Shape | Where it is | What you did to get there |
|---|---|---|
| **Flat pattern** | On the floor (the middle view of the Work Screen) | Right after loading or drawing |
| **Placed Shape** | On a guide, or laid flat at the placement target | The shape made by **Place**, before free hand moves or vertex edits |
| **Adjusted Shape** | At its adjusted position and shape | The placed shape after hand moves, vertex edits, folds, and other preparation |
| **Dressed garment** | On the body | After dressing it with **Simulate** or similar |

There are three buttons for going back to an earlier shape, and they differ in how far back they go (they are lined up in the **Placement** panel, nearest first).

| Button | Goes back to | What it throws away |
|---|---|---|
| **Back to Adjusted Shape** | After simulation: the adjusted shape kept at its start. With no result yet: the adjusted shape before pulling toward seams | The dressed result; with no result yet, the seam-fitting effect too. Hand moves, vertex edits, and folds are kept |
| **Back to Placed Shape** | -> the placed shape rebuilt from the current placement settings, including flat placement | The dressed result and anything you moved by hand |
| **Back to Flat** | -> flat where it is | The hand edits and the bent shape of the target pieces (their place and turn stay). Patterns and seams are kept |

Select pieces and press it to affect only those pieces; press it with nothing selected to affect the whole garment (a confirmation appears for the whole garment).
Linked mirrored pieces go back together. Even with Back to Flat, the dressed shape of unselected pieces stays.

<!-- figure: Figure 3: a piece's four forms and the three "go back" buttons -->

When you fix a pattern, by default the adjusted shape and the dressed garment do not change right away (so that you don't wait at each fix). The next time you press **Simulate** or another run, the fixes are **carried over to the adjusted shape and the dressed garment together** (no physics is calculated for that) and the run settles them. To see the 3D shape after every fix, turn on **3D Follows Edits** under **AC9 Run** in the Tool tab (each fix then takes a moment). **Update 3D** carries everything over at once.

## Garments and pieces

- You can put any number of garments in one scene. Switch between them one at a time with the **Garment** field.
- When one BOM holds several garments, they are split into one group per garment as it is loaded. A seam's partner is decided inside that group.
- Layering: a garment that has finished being dressed becomes an "immovable obstacle" when you dress another garment. Dress from the innermost garment outward. When you dress another garment, the earlier garments' history (frames) is dropped and only their last shape stays.

## A piece's four states

| State | Moves | Other cloth collides with it | Seams |
|---|---|---|---|
| **Active** | Yes | Yes | Yes |
| **Freeze** | No (stays where it is) | Yes | Yes |
| **Inactive** | No (stays where it is) | No (passes through) | Yes (the seams pull) |
| **Off** | Not part of the calculation | — | No (the seams go away with it) |

- For pieces you aren't using, don't hide them (**H**); set them to **Off**. They show as translucent gray.
- States switch for a left and right pair together. To change only one side, **Ctrl**+click.

<!-- figure: Figure 4: a piece's four states -->

## The run button

| Button | What it does | History frames | How to stop |
|---|---|---|---|
| **Simulate** | Sews from the adjusted shape (from the current shape once dressed) and keeps running until you stop it. You can pinch the cloth with **Ctrl+drag** | Depends on recording settings and range ([Recording progress](features/run.md#recording-progress)) | Press the button again or **Esc** |

**Simulate** is both how you dress the garment and how you settle it after fixing. It doesn't stop by itself, so stop it once the garment has settled. **Gravity** can be switched while it runs. To close the seams first, turn gravity off and run, then turn gravity on once they are closed.

**Phase Sim**, which sews step by step and stops by itself, and the piece roles appear only when **Experimental Features** is turned on in the add-on preferences (see "Dress (run)").

### Continuing from the current shape

- The second and later **Simulate** runs, "Send to AC9 Cloth Retopo", and measuring the garment all use the garment's current shape. While the history frames are being played back, they use **the frame shown on screen**. Before you press, check that it is the last frame (**Shift+Right Arrow** jumps to the last frame).
- To fix the shape of a middle frame by hand, press **Fix by Hand** on that frame. That frame becomes the garment's shape itself, and you can fix it in Edit Mode or Sculpt Mode. After fixing, press **Simulate** and it continues from the fixed shape.

<!-- figure: Figure 6: the flow of the calculation -->

## What gets saved

| What | Where it is saved |
|---|---|
| Pattern data, placement, and settings | Inside the .blend |
| The last shape of the dressed garment | Inside the .blend |
| History frames (what you can play on the timeline) | The `AC9_ClothSim_jobs` folder next to the saved .blend |

- **Save the .blend before simulating.** If you haven't saved, the history is made in a temporary folder and can't be played back later.
- If you hand just the .blend to another computer, the last shape is still visible and you can continue from it (only playing back the history isn't possible).
- History deleted with **Delete Cache** (a button in AC9 Run) can't be brought back.

## Reading the results

- **Even with the same settings, the result changes a little each time you calculate.** Don't judge good or bad from a single result.
- Distinguish confirmed red crossings from yellow candidates. No red marks alone do not prove a good result.
- Run **Diagnose** for the current shape. Read its target, counts and unchecked items, and diagnose again after editing. Current diagnosis and past run records are separate; see [Diagnosis](features/placement.md).
