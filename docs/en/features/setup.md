---
title: Setup (loading a pattern)
sidebar_position: 3
---

# Setup (loading a pattern)

What you do in this panel: bring a pattern into Blender, split garments apart and choose the pieces to use, and open the work screen.

## Units

- **Units** (**mm**, **cm**, **m**, **in**): the unit every length is shown in: the labels on the pattern, the measurement tables, and the fields such as a dart's width or a line's distance. You can type a length in any of these fields with its unit (for example `5cm`), and a number typed while dragging is read in this unit. Only **Particle Distance (mm)** stays in mm.
- This only changes how lengths are shown. The pattern's sizes do not change.
- **New Garment** and the imports switch a new scene to mm.

## Import BOM

- Purpose: Load a pattern from a `BOM.xml` written by 3D garment software.
- Prerequisite: You have a `BOM.xml` file.
- Steps: Press **Import BOM** and choose a `BOM.xml`. The 3D garment, placement, and saved adjusted shapes of the current BOM are discarded. The body, other garments, and calculation results on disk are not deleted. If it fails, the original state is kept.
- Expected result: The pattern is laid out flat on the floor. The seams contained in the BOM are also loaded and appear in the **Seams** list. The screen shows "○ pieces, ○ seams".
- When one BOM contains several garments, the pieces are split into groups per garment at import, and the partners of seams are decided within each group (a piece is never sewn to a piece of another garment). Groups whose classification is uncertain are shown in orange.
- Hole shapes in the BOM (such as the diamond of a dart) are cut out of the piece (there is no cloth there).
- False seams that sew a piece to itself using a shared line are not imported. Seams that close the two ends of a band into a ring are kept.
- **3D Drape (FBX)**: on the right of the file browser you can give the garment's FBX exported with the BOM. When it is empty, the one FBX in the BOM's folder is used, if there is exactly one. With an FBX, the seams' partners and the garment groups are decided by where the pieces are sewn together in 3D (see "Seams from 3D Drape (FBX)" below).
- If a piece was flipped front-to-back in the 3D garment software, its 2D pattern is also mirrored left-to-right in the exported `BOM.xml`, and its seams can come in crossed (right end to left end instead of right to right). Flip the piece back before you export. If you can't, delete the crossed seams in **Edit Seams** after import and sew them again with **Add Seam**.

![the Setup panel (Work Screen and Import BOM numbered)](../img/d_setup_panel.png)

## Update BOM Seams

Use this when you want to re-read the seams while keeping the dressed shape. Press **Update BOM Seams** under the **Garment** field. It updates the seams and automatic classification from the BOM that was loaded.

- Kept: the dressed shape, placement, piece On/Off and Freeze, fabrics, roles, folds, and manually made seams, garment classification, and names.
- It updates only when the shapes and references of all pieces match. A parallel shift of the BOM's 2D layout is tolerated, and the current pattern arrangement is kept.
- If the IDs of pieces, shapes, edges, or internal lines changed, or if a seam that you set by hand or that has references would disappear, nothing is changed and the reason is shown.
- To settle the updated seams, run Simulate next.
- With a fresh import, other versions of settings that shared the old 3D are also retired under the name "previous". Even if you choose an old version, the old placement and results do not come back.

## Seams from 3D Drape (FBX)

- Purpose: keep BOM seams from landing on another piece or on a piece of another garment.
- What happens: a `BOM.xml` names a seam's partner by a shape number and a line number. In a project that gathers several garments, or uses parts brought in from other files, several pieces can carry the same numbers, and the BOM alone cannot tell which one is meant.
- Prerequisite: export the garment as FBX from the 3D garment software in the same state as the BOM (dressed as it is, with the UV left as the pattern layout). Put it in the BOM's folder and **Import BOM** uses it by itself.
- Steps: for a garment already imported, press **Seams from 3D Drape (FBX)** under the **Garment** field and choose the FBX. The dressed shape and placement are kept.
- Expected result: pieces sewn together in 3D become the seams' partners, and pieces sewn to each other become one garment group. The panel shows "3D drape: ○ / ○ BOM seams found".
- Seams of pieces that are not in the FBX (other garments, when the FBX holds only one) are decided from the BOM's numbers as before.
- A seam of pieces in the FBX that is not found in 3D comes in marked "not found on the 3D drape". Check it.
- If you choose the FBX of another garment, nothing changes and "The FBX does not hold this pattern" is shown.

## Import DXF and Export DXF

- **Import DXF**: Loads a pattern in DXF-AAMA format as a new garment. The outline, corners (turn points), internal lines, notches, and piece names come in.
  - Seams are not included in DXF, so a loaded garment has no seams. Sew them together with **Edit Seams** and **Add Seam**.
  - Not loaded (the counts appear in the report after loading): grainlines, inner cutouts (such as dart holes), and other sizes (for a graded DXF, only the base size).
  - For now this only loads as a new garment. There is no way to swap in the pieces of the current garment while keeping its seams.
- **Export DXF**: Writes the pattern in DXF-AAMA. The outline, corners, internal lines, notches, and piece names are included. Seams are not written to DXF; they stay on the AC9 side. If you read back a DXF written by AC9, the piece names, edge numbers, and notch numbers come back unchanged.

## Import SVG

- **Import SVG**: Loads a pattern drawn in a drawing app, or an SVG written by a pattern generator, as a new garment. The outline, corners, internal lines, notches, and piece names come in.
  - Pieces are separated by the drawn lines, not by layers. A closed line that is not inside any other line is one piece, and the lines and marks inside it belong to that piece. The piece name is the name of the layer or group that contains only that one piece; if there is none, it is the text inside the piece.
  - If there is a cutting line that surrounds the finished line at the width of a seam allowance (roughly 2 to 40 mm), the finished line becomes the outline and the cutting line is not loaded. For a piece that has only one closed line, that line is read as the finished line.
  - Notches are short lines of 15 mm or less that touch the outline or the cutting line, small circles on the outline, and symbols with "notch" in their name.
  - Not loaded (the counts appear in the report after loading): lines with arrowheads (such as grainlines), small marks such as buttons and holes, construction lines, and lines that do not belong to any piece.
  - Units: if the width and height have mm, cm, in, or pt, they are used as they are. For px or no units, the file is treated as 1 unit = 1 pt if it came from a drawing app that draws in points, and as 1/96 inch otherwise. If that is wrong, choose again with "Units" on the screen where you pick the file. The report shows "1 unit = ○ mm".
  - Seams are not included in SVG, so sew them together with **Edit Seams** and **Add Seam**.

## From Mesh

- Purpose: When you modeled a garment directly in Blender (a garment without a BOM), turn it into a sewable pattern and seams so you can simulate it.
- Prerequisites:
  - Make the garment mesh from a single sheet of faces with no thickness, and leave the cuffs and hem open. For modifiers, the visible shape is used as it is.
  - Put **UV seams** where you want to split the pattern (sides, shoulders, and so on) and unwrap. One UV island becomes one pattern piece, and an edge where two islands touch becomes a seam.
  - Choose the body in **Body** of the **Placement** panel beforehand (the button cannot be pressed if no body is chosen).
  - Put the garment mesh in the **Mesh** field (an object separate from the body): click it in a 3D view with the eyedropper at the right of the field, or pick it by name. With the field empty, the active object is used.
- Steps: Put the garment mesh in the **Mesh** field and press **From Mesh**.
- Expected result: Flat patterns are laid out on the floor and a dressed garment `AC9_Garment_<name>` is created, starting dressed on the body in the shape you modeled. The original mesh is hidden. The screen shows "○ pieces, ○ seams" and the number of points that are inside the body. The places that are inside are painted red.
- Then press **Simulate** and the cloth drapes and settles.
- Limitations: This is the "as built" state; the cloth has not settled. If the UVs have not been unwrapped, it stops with an error saying there are no UVs.

## Add Avatar

Find it at the top of **Measure**. See [adding and measuring an avatar](measure.md#add-avatar).

## New Garment

Starts a new garment with no pieces at all. Then draw with **Draw Piece**.

## The Garment field and the pieces to use

- **Garment**: The field for choosing the garment (pattern data) you are working with. When you switch it, the panel display changes to that garment's pattern, seam list, placement, and so on. For layered garments, you switch one garment at a time here.
- `<garment name>: 13 / 20 pieces on` is the number of pieces that are used (on) and the total number.
- **All On**: Turns all pieces on (the function name is All Pieces).
- Only pieces that are on are sewn in the simulation. Pieces that are off are shown gray and translucent.
- **Reload Spec**: Re-reads the pattern data (text JSON) and rebuilds the pattern on screen. Press it when you edited the JSON by hand. If the JSON is broken, the panel shows "The spec text is not valid JSON" and where the problem is.

## Garment groups (when one BOM contains several garments)

When there are two or more groups, they are listed one group per line under the **Garment** field. Each line shows the group's name and `(number on / number of pieces)`, and you can rename it with the pencil on the right. An orange line is a group that was split from the placement, so it is not certain. Pieces that are alone are gathered into one line, "Pieces alone (○)".

Steps to turn on only the one garment you want to use:

1. Press the line of the group you want to use (the pieces of that group are selected; Shift adds).
2. With the pieces selected, press **Active** under **State** in **AC9 Piece** on the Tool tab.
3. In the **State** row, press **Invert Selection** to invert the selection, then press **Off**.

To re-read the seams, use "Update BOM Seams". Only when you need to correct the classification by hand, you can select pieces and use "Garment from Selection" in F3. It is not in the regular panel.

When seams land on another garment or another piece, first read the FBX exported with the BOM with **Seams from 3D Drape (FBX)**. When several pieces share the BOM's numbers, fixing the groups by hand ("Garment from Selection") does not decide the seams' partners.

## Display

- **Overlays** (the eye button and list): Toggles seam lines, lengths, notches, piece colors, fabric colors, names, guides, and measurements. **Diagnosis Display** selects one of None, Diagnosis Locations, Stretch, and Face Side. It shares the diagnosis panel's Diagnosis Display setting; changing it does not run diagnosis. Diagnosis Locations shows a current valid diagnosis, while Stretch and Face Side show the displayed shape. Stretch is clear up to 1.05, yellow at 1.2, red at 1.5, and blue when compressed. Face Side is blue on the face and red on the back, including with thickness visible. **Show Fabric Thickness** and its side (**Inside**, **Center**, **Outside**) affect appearance only, not simulation.
