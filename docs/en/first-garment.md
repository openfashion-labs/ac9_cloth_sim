---
title: Your first garment
sidebar_position: 4
---

# Your first garment

This walks you through everything from loading a pattern to dressing it on a body and saving.
Button names are the ones shown in the English interface. If you are new to Blender, please read [Blender basics](blender-basics.md) first.

<!-- video: Your first garment (1-2 minutes per chapter) -->

## What you need

- Blender 5.2 with AC9 Cloth Sim installed (see "Installation" in the README for how to install it)
- **A body (avatar) mesh**: standing straight, with the front facing **−Y**. An A-pose or a T-pose is fine.
- **A pattern**: any one of the following
  - a CLO BOM (`BOM.xml`) (it also contains the seams, so this is the quickest way to dress a garment)
  - DXF (DXF-AAMA format. It does not contain seams, so you add them yourself)
  - A garment mesh made in Blender (divided into patterns by UV seams)

<!-- When public samples (a body and patterns) are ready, add the download instructions here -->

## 1. Save, and open the work screen

1. Open the .blend that contains the body and **first save it under a different name** (**Ctrl+Shift+S**). The simulation's progress is kept in a folder created next to the saved .blend.
2. Press **N** in the 3D viewport to show the sidebar, and open the "AC9 Cloth Sim" tab.
3. In the **Setup** panel, press **Work Screen**. You get a screen with the 3D view on the left, the pattern in the middle, and the Tool tab on the right.

## 2. Load the pattern

In the **Setup** panel, press the one button that matches your pattern.

- **Import BOM**: choose a `BOM.xml`. The patterns are laid out flat on the floor, and the seams are loaded with them. The 3D shape and placement of the current BOM garment are discarded. To keep the dressed shape, use **Update BOM Seams**.
- **Import DXF**: choose a DXF. It contains no seams, so you add them later in "3. Check the seams".
- **Import SVG**: choose an SVG drawn in a drawing app or similar. One closed line is one piece. It contains no seams, so you add them later, the same as with DXF.
- **From Mesh**: do "4. Choose a body" first, then select the garment mesh and press it. It starts in the state of being worn on the body, with the shape you modeled (skip "5. Place" and go to "6. Dress").

After loading, the panel shows "○ pieces, ○ seams" (the number of pieces and the number of seams).

**When one BOM contains several garments**, the pieces are divided into a group per garment and listed one garment per row under the **Garment** field. We recommend dressing just one garment first.

1. Press the row of the garment you will use (that garment's pieces are selected).
2. In **AC9 Piece** on the Tool tab on the right, press **Active** under **State**.
3. In the **State** row, use **Invert Selection** to invert the selection, then press **Off (All)**. The pieces of the other garments become gray and translucent.

## 3. Check the seams

- In the **Seams** tab of **Lists** in the sidebar, the seams are listed one per row. You see the length of both sides (A and B) and, if there is a difference, the difference (mm).
- Rows with a warning icon, and seams that are orange on the pattern, are seams that you should check. Press a row and **AC9 Seam** appears in the Tool tab on the right, where you can see that seam's settings.
- When you loaded from DXF, you add the seams. Press **Add Seam** in the **Pattern** panel, and click the start and end points on the outline of one piece, then the start and end points on the other piece (**both from the same end**). If you click in the middle of an edge, it takes that whole edge. With 4 points, the length of both sides and the difference appear; review them and press **Enter** to add. If a section was taken in the reverse direction, press **F** to reverse it.

For a detailed explanation of how to add seams, see "Seams" in the feature descriptions.

## 4. Choose a body and make placement guides

1. In the **Placement** panel in the sidebar, choose the avatar's body mesh in the **Body** field. The body mesh is a single mesh. Do not choose hair or accessories.
2. Press **Detect Guides**. Cylinders (guides) that serve as placement guides for the pieces are created around the torso, neck, arms, and legs.
   - If "upright, facing -Y?" appears, check that the body is standing straight and facing −Y.
   - If the cylinders are off, you can fix them with **Edit Guides**.

## 5. Place the pieces around the body

1. In the **Placement** panel, press **Place** to pick up the tool.
2. Click a piece in the pattern view to select it (**Shift** adds). For a left/right pair, when you place one, its partner follows to the mirrored position.
3. In the 3D viewport on the left, move the cursor over the cylinder where you want to place it (the cylinder glows yellow).
4. Press on the cylinder and the piece is placed there. If you **hold and move**, it slides around and along the cylinder. It is set where you release (if you press and drag a piece that is already placed, it does not jump, and moves only by the amount you move. To change only the angle, only the height, or to tilt the piece, use Around (angle around), Height, and Tilt (tilt angle; at 90° the upper side is horizontal and outward, at 180° it is upside down = the outline of the collar faces the neck side; the ↻ next to it adds +180°) in "AC9 Piece" on the Tool tab).
5. Repeat until all the pieces are placed. The panel shows "○ / ○ pieces placed" (number placed / number to use).

The shape you see when you release is the shape the simulation starts from. Pieces that turn red are pieces that are inside the body or overlap other pieces (the reason is shown). You can start as is, but it starts more cleanly if you place them again or use the tidying tools in the **Placement** panel.

To place a piece again, select it and place it again with **Place**. **Back to Flat** lays it flat where it is.

<!-- screenshot: right after placing around the body -->

## 6. Dress (Phase Sim)

1. Press **Phase Sim** at the top of the Tool tab on the right (**AC9 Run**).
2. The first 10 seconds or so is preparation for the calculation ("starting the solver" appears). The first time you dress, it takes a little longer.
3. When the calculation starts, the garment moves on screen. Below the button you see a progress bar and the current stage and frame. Following the stages, the seams close and the garment settles onto the body.
4. When it finishes, "done: …" appears. To stop it, press the button (it has changed to **Stop**) again or press **Esc**. The progress up to the point where you stopped is kept.

The time the calculation takes depends on the size of the garment and the computer.

<!-- screenshot: the dressed garment and the Result field of AC9 Garment -->

## 7. Look at the result

- The progress remains as frames. Put the cursor in the 3D viewport on the left and press **Space** to play, or **Shift+→** to jump to the last frame.
- With nothing selected, look at the Tool tab and the result (Result) appears in **AC9 Garment**.
- Red fill means "possible problem". It does not necessarily mean it is bad.
- Even with the same settings, the result changes slightly every time you calculate. Do not judge good or bad from one run; run it a few times and look.

## 8. Fix it and dress again

- **Fix the pattern**: drag corners and edges with **Edit Pieces** in the **Pattern** panel. The edited shape is carried onto the dressed garment right away (the cloth has not settled yet). If you then press Phase Sim, it calculates **continuing from the current shape** and settles into the edited pattern.
- **Pinch and fix the cloth by hand**: press **Live Sim** and the calculation keeps running until you stop it. When the cloth starts to move (after 10-15 seconds), pinch and pull the cloth with **Ctrl + left drag**. If cloth has pushed through other cloth, press **Alt** during the drag and the cloth you hold passes through other cloth. Press **Esc** and the shape where you stopped remains (the progress frames are not kept).
- **Dress again from the start**: with nothing selected, press **Back to Placed** in the **Placement** panel (a confirmation appears), then press Phase Sim.

## 9. Save

Save with **Ctrl+S**. The final shape of the dressed garment is saved inside the .blend, and the progress frames are saved in the folder next to the .blend.

## What to do next

- Layering clothes: switch to another garment in the **Garment** field and press Phase Sim, and the garment you have finished dressing becomes an immovable obstacle. Dress from the inner garment outward.
- Change the fabric: when you select a piece, you can choose the fabric in **AC9 Piece** on the Tool tab.
- Send to Cloth Retopo: with **To AC9 Cloth Retopo** in Details of **AC9 Run** on the Tool tab, you make the dressed garment the Guide of [AC9 Cloth Retopo](https://github.com/openfashion-labs/ac9_cloth_retopo).

If things do not work, see "Troubleshooting" in the README and the [Troubleshooting](troubleshooting.md) page.
