---
title: Piece Roles and Folds
sidebar_position: 6
---

# Piece Roles and Folds

## Piece Roles

A role decides which step a piece is sewn in (body, collar, sleeve, then accessories). If a role is wrong, the sewing order goes off and things can fall apart. Besides the sewing order, the way a piece is placed and its starting shape are also decided by its role. For details, see "[Piece Roles and What Gets Decided Automatically](roles.md)".

- The top row shows the garment type, the number of pieces whose role is decided, and the number to check.
- **Propose Roles**: proposes roles for all pieces from the pattern shapes and clues in the source data (placement points, piece names, fabric names, and so on). It doesn't overwrite roles you decided yourself. Low-confidence ones get a warning.
- To decide a role yourself, select the piece, press **Role** in **AC9 Piece** in the Tool tab, and pick from the list (the partner of a left and right pair becomes the same).
- The list shows each piece's role. A warning means "please check," and a human mark means "decided by you."

## Fold

This feature places cloth folded along an internal line (for example, turning a collar over). Click an internal line on the pattern to select it (orange), and **AC9 Internal Line** appears in the Tool tab.

- **Fold Angle**: the angle to fold (0 to 180°; 0 means no fold). When the angle isn't 0, the following fields appear below it.
  - **Direction**: **Mountain** (a mountain fold) or **Valley** (a valley fold). This is the direction seen from the right side of the cloth. If you flip a piece over in 3D, the fold flips with the cloth.
  - **Turns**: **No Side** / **Side A** / **Side B**. With No Side, only the mountain or valley and the angle are set, and a whole side isn't folded over when placing. Use it for pleats, tucks, and the like. Side A and Side B are the side that turns in the placed shape. Labels A (orange, on the left as seen from where you started drawing) and B (blue) appear on both sides of the line. Only the placed shape changes.
  - **Layers**: **Both** (calculated as two layers, the default), **A only** / **B only** (only that side is calculated, and the other side follows along as a second layer; for a placket that is folded and sewn closed, and so on). It doesn't switch automatically.
- With Turns set to Side A or Side B, changing the fields folds the placed shape right away (angle 0 flattens it again). You place and sew in this shape.
- For a line that can't fold in the placed shape, a one-line reason and **Local Sim** appear. A line that stops inside a piece (a tuck, for example) folds in the simulation but has no A or B.
- The same fold goes into the partner's line of a left and right pair. Internal lines that a seam runs along also fold.
- A line with an angle is treated as an ironed crease in every step of the simulation (it keeps its angle with 10 times the stiffness of the cloth's bending).
- **Elastic**: the rate of shrinking along the line (100 % means no shrinking). Below it are the seams sewn to the line the distances to the surroundings and **Move by Number...**. Cutting into two pieces (**Cut Along Line**) and deleting (X) are in the right-click menu.

A line you newly give an angle starts as "No Side." Folds in earlier files keep their turning side (if the side is missing from an old record, it is Side A as before). Even with "No Side," you can choose the layers to calculate separately.

When you switch from Side A or Side B to No Side, it returns to the saved placed shape from before folding. If it can't safely go back because of a fold added later, per-vertex hand edits, or a missing saved pre-fold shape, it keeps the settings and the shape and shows the reason.
