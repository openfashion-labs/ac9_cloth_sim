---
title: Fabrics and Garment Settings
sidebar_position: 9
---

# Fabrics and Garment Settings

## AC9 Garment in the Tool tab

It appears in the Tool tab when nothing is selected.

![AC9 Garment in the Tool tab](../img/d_tool_tab.png)

- **Garment**: the garment you are working with.
- **Particle Distance (mm)**: the spacing of the points when the cloth is divided into fine triangles (in mm, from 2 to 30, default 6). The smaller it is, the finer the wrinkles that show up, but the slower it runs. 4 mm takes about twice as long as 6 mm.
- **Default Fabric**: the fabric used by pieces that don't have their own fabric. The default for a new garment is **Cotton Jersey**.
- **Watch**: the problem watcher (see below).
- **Default Look**: the default look of pieces (the material used for rendering).
- **Result**: the results box (see the "Run" section).

## AC9 Piece in the Tool tab

It appears in the Tool tab when you've selected a piece. If you select several, the fields show the value of one of the selected pieces; if the values differ, the field name turns red and shows "(mixed)", and changing it affects everything selected.

![AC9 Piece in the Tool tab](../img/d_tool_piece.png)

- The top field: the piece's name (for display; you can also set it with F2). Below it are the number, width x height and the outline length, and the garment group it belongs to.
- **Symmetric Edit**: editing one piece of a symmetric pair (a "left and right pair").
- **State**: the four states ("Piece States and Layering") and **Invert Selection**.
- **Role**: the role.
- **Fabric** and **Interlining**: the fabric and interlining. The pencil on the right opens a screen for fixing that fabric's or interlining's numbers (the same screen as **Edit...** in the **Fabrics** tab of **Lists**).
- **Look**: the appearance (Blender's material field). If empty, the garment's **Default Look** is used.
- **Thickness**: whether to show the placed pieces and the dressed garment with thickness (appearance only). A fold over an internal line shows as two layers.
- **Layer** (when Experimental Features is on, or when a piece has a layer): 0 to 9. Between pieces with different numbers, the higher one is kept outside the lower one. When things have settled, set it back to 0 (the arrow on the right does it for all pieces). It doesn't go back automatically. Near a seam joining a piece to another layer, the layer does not apply and the usual contact holds; a narrow piece sewn along its edge can have every vertex there, and then its Layer has no effect. A run says so in the log and a warning when it starts, and Problems shows that piece in red.
- **Distance (mm)**: point spacing per piece (finer for only the small parts). The arrow on the left returns it to the garment's value.
- **Elastic**: the rate of shrinking along the piece's long axis (100 means no shrinking).
- **Closes Onto**: the partner piece it is fastened to with buttons ("Buttons (Fasteners)").
- Whether it has been placed ("Placed" or "Not placed yet") and the number of internal lines.
- Changes take effect from the next time you press **Live Sim** (for what takes effect when changed while it runs, see the "Live Sim" section of "Dress (run)").

## The fabrics and interlinings list (Fabrics)

The **Fabrics** tab in **Lists** shows that garment's fabrics. One of them is the default (marked with a star), and every piece always has exactly one fabric.

![the Fabrics tab of Lists](../img/d_lists_fabrics.png)

- One row is one fabric: the color, the name, and the number of pieces using it. Press a row and the pieces of that fabric are selected, and **AC9 Fabric** appears in the Tool tab. **New** at the bottom adds a new fabric with the same numbers as the default fabric.
- **AC9 Fabric**: the name, **Preset** (pick from the prepared fabrics), the numbers (weight, bending, thickness, stretch, shear, hysteresis), **Edit...** (fix the numbers), **Make Default**, **Select Pieces**, and **Delete** (the default fabric can't be deleted).
- The lower half is the interlining list; press a row to get **AC9 Interlining** (**Preset**, **Edit...**, **Select Pieces**, **Delete**). Fixing it affects every piece with that interlining. Add one with **New** and attach it with **Interlining** in **AC9 Piece**.
- **Show Fabric Colors** at the very bottom shows each fabric's color on the pattern.

## What the fabric numbers mean (Edit Fabric)

The numbers in **Edit Fabric** (opened with **Edit...**) are measured values of real cloth. Start from the closest preset and change only what is missing, a little at a time.

| Field | What it is | Higher | Lower | Scale (presets) |
|---|---|---|---|---|
| **Weight** g/m² | How heavy the cloth is (grams per square meter) | Heavier, sags under its own weight | Lighter, keeps its shape more easily | Jersey about 125, poplin 140, wool suiting 276, canvas 390, leather 706 |
| **Bending** µN·m | How hard it is to bend. **The main stiffness** | Stiffer, fewer and rounder folds | Softer, fine wrinkles | Jersey 0.65, poplin 6, wool suiting 18, canvas 245, leather 262 |
| **Stretch warp** N/m | How hard it is to stretch along the pattern's x (side to side) | Barely stretches | Stretches like a knit | Woven cloth about 3000, jersey 280, rib knit 15, leather 11900 |
| **Stretch weft** N/m | The same along the pattern's y (up and down) | Same | Same | Same |
| **Shear** N/m | How hard it is to skew diagonally (a square pulled into a diamond) | Keeps its weave square, stands away from curves | Follows round shapes, more diagonal wrinkles | Most cloth 15-30, canvas 150, leather 704 |
| **Hysteresis** /m | How much a bend stays once the pull is gone | Creases and the draped shape stay, comes to rest sooner | Springs back, keeps moving longer | Most cloth about 40, jersey 61 |
| **Thickness** mm | How thick the cloth is drawn | Display only (the simulation does not use it) | Same | 0.6-1.2 |

### The fabrics provided (presets)

| Fabric | Weight g/m² | Bending µN·m | Where the numbers come from |
|---|---|---|---|
| Cotton Poplin | 140 | 6 | Measured (published) |
| Wool Suiting | 276 | 18 | Measured (published) |
| Denim | 354 | 27 | Measured (published; a washed commercial denim, raw denim is stiffer) |
| Satin | 120 | 4 | Estimate |
| Chiffon | 50 | 2 | Estimate |
| Nylon Taffeta | 82 | 3.3 | Weight a measured mean, bending an estimate (a windbreaker's cloth) |
| Cotton Jersey | 125.5 | 0.65 | Measured (published) |
| French Terry | 250 | 2.5 | Estimate |
| Rib Knit | 220 | 6 | Estimate |
| Leather | 706 | 262 | Measured (published) |
| Synthetic Leather | 440 | 112 | Measured (published) |
| Canvas | 390 | 245 | Bending an estimate |

An estimated fabric is a starting point: change it in Edit Fabric to match a real cloth.

### To make a cloth stiffer, or to make it drape

- **Whether a cloth stands or droops depends on Bending against Weight.** Raising Bending alone does little if the cloth is heavy.
  - To make it stand: raise Bending and lower Weight.
  - To make it drape: lower Bending (raise Weight too if it should feel heavy).
- Bending acts gently: doubling it changes little, and ten times makes the length a cloth can stick out about twice as long. Change it by tens, not a little at a time (for example 6 → 60 → 600).
- Bending goes up to 10000, about as stiff as thick paper. Sheets of metal or plastic are beyond it.
- To stiffen just one piece, an **Interlining** (it adds weight and bending, like a real one) or **Strengthen** (below) is easier than changing the fabric.
- Stretch and Shear do little to how a cloth drapes. Change them when the cloth should stretch, or follow the body's curves.

## The problem watcher (Watch)

When it is on, it records problems that happen while the garment is being dressed (a piece drifting away, seams pulling against each other, a seam being sewn inside out) and paints the place of the first one in red on the garment. It doesn't stop the simulation.

- It is off by default. Turn it on before pressing **Live Sim**.
- Red marks a "candidate for a problem," and it can show up even on a good garment.
- The red is drawn by **Diagnose** > **Problems** under **Overlays** (on by default). If you can't see it, check the overlay eye button in the header and the "Problems" checkbox.

## Strengthen

Select pieces, open Properties → Tool → **AC9 Piece**, and enable **Strengthen**. **Strength Factor** defaults to 5 and ranges from 1 to 100. It multiplies stretch, shear, and bending stiffness equally; 1 leaves stiffness unchanged. Mass, thickness, and rest shape stay the same. Selected pieces and their pairs change together.

A purple tint marks strengthened patterns and 3D pieces; Freeze and Off state colors take priority. Toggle Strengthen or edit the factor during Live Sim: the worker applies it at the next frame that receives the change. Disabling it retains the chosen factor. The setting is saved in the spec and .blend and remains effective until disabled.

Interlining adds the mass and bending of a physical material and is separate from Strengthen. You choose whether to retain the strengthened shape or disable it and let the cloth relax. Large multipliers resist sewing and bending more strongly.

The tint is visible when Overlays → Piece Colors is enabled. If Strengthen changes during a Live run using End Taper, the next Live rebuilds from the shown shape. The visible shape and Strengthen settings are still saved.
