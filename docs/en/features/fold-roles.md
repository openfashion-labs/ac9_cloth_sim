---
title: Folds
sidebar_position: 6
---

# Folds

## Internal-line folds

Set folds along internal lines or across seams. Folding the adjusted shape and holding a crease during Simulate are different actions. Click an internal line on the pattern to select it (orange), and **AC9 Internal Line** appears in the Tool tab.

- **Fold Angle**: the angle to fold (0 to 180°; 0 means no fold). When the angle isn't 0, the following fields appear below it.
  - **Direction**: **Mountain** (a mountain fold) or **Valley** (a valley fold). This is the direction seen from the right side of the cloth. If you flip a piece over in 3D, the fold flips with the cloth.
  - **Turns**: **No Side** / **Side A** / **Side B**. With No Side, only the mountain or valley and the angle are set, and a whole side isn't folded over when placing. Use it for pleats, tucks, and the like. Side A and Side B are the side that turns in the adjusted shape. Labels A (orange, on the left as seen from where you started drawing) and B (blue) appear on both sides of the line. On a mirror pair (a **Mirror Copy**, the two lines of a piece with **Symmetric Edit**, a BOM's left and right pieces), A on the mirror line is the mirror image of A on the other line (it can be on the line's right there), and a fold is copied to the mirror line with the same letters. In a file from earlier versions, a line whose mirror has its A and B the other way round says "A / B named apart on its mirror"; **Match Mirror A/B** names them alike (the side that turns and the layer kept stay where they were). Typing the angle again names them alike too, and the mirror line then takes this line's fold settings (values changed on the mirror line alone are replaced). See the timing table below for changes during a run.
  - **Layers** (experimental; shown with **Experimental Features** enabled): **Both** (calculated as two layers, the default), **A only** / **B only** (only that side is calculated, and the other side follows along as a second layer; for a placket that is folded and sewn closed, and so on). It doesn't switch automatically.
- With Turns set to Side A or Side B, changing the fields folds the adjusted shape right away (angle 0 flattens it again). You place and sew in this shape.
- For a line that can't fold in the adjusted shape, a one-line reason appears. A line that stops inside a piece (a tuck, for example) folds in the simulation but has no A or B.
- The same fold goes into the partner's line of a left and right pair. Internal lines that a seam runs along also fold.
- A line with an angle is treated as an ironed crease in every step of the simulation (it keeps its angle with 10 times the stiffness of the cloth's bending).
- **Elastic**: the rate of shrinking along the line (100 % means no shrinking). Below it are the seams sewn to the line the distances to the surroundings and **Numeric Input...**. Cutting into two pieces (**Cut Along Line**) and deleting (X) are in the right-click menu.

A line you newly give an angle starts as "No Side." Folds in earlier files keep their turning side (if the side is missing from an old record, it is Side A as before). Even with "No Side," you can choose the layers to calculate separately.

When you switch from Side A or Side B to No Side, it returns to the saved adjusted shape from before folding. If it can't safely go back because of a fold added later, per-vertex hand edits, or a missing saved pre-fold shape, it keeps the settings and the shape and shows the reason.

## Folds across seams

Select a seam with **Edit Seams**, then set its angle, direction and **Turns** in **AC9 Seam** on the Tool tab. An internal line folds within one piece; a seam folds between two joined pieces. See [Seams](seams.md) for the controls.

## When settings apply {#when-settings-apply}

| Setting | Adjusted shape | When Simulate starts | Changes during Simulate |
|---|---|---|---|
| Internal-line angle and direction | Folds when Turns is Side A or Side B | Used as a crease | Only on lines whose angle was nonzero when the run started |
| Internal-line Turns | Chooses the side to turn; No Side does not turn a whole side | Does not determine the solver's fold | Cannot be changed |
| Internal-line Taper | No change | Softens only ends that stop inside the piece | Requires an interior end and nonzero angles both at the start and now |
| Seam angle and direction | Turns the chosen piece | Used as a crease between the pieces | Only on seams whose angle was nonzero when the run started |
| Seam Turns | Chooses the piece to turn | Uses the setting at the start | Cannot be changed; stop, set it and run again |

**To add a fold to a line or seam that was at 0° when the run started, stop, set its angle and start Simulate again.** The controls are gray during the run and show a reason. An internal line set to **No Side** still forms a crease in the simulation if its angle is set before starting. A flat adjusted shape does not necessarily mean the fold is disabled.

**Taper is a length, not an angle.** It sets how far the crease softens from an end that stops inside the piece. Ends on the outer boundary do not taper. If both ends reach the boundary, the field is hidden; if the current angle is 0°, the field is disabled. Check the unit shown in the field. A large value relative to the line's length weakens the fold along the whole line.

Internal-line **Layers** is experimental. It is read at the start and cannot be changed during a run. It is separate from the fold angle and Turns.
