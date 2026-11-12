---
title: Placing around the body
sidebar_position: 7
---

# Placing around the body

What you do in this panel: specify the body and decide where each piece goes around it. Items without a body, such as bags, can be laid flat without choosing a body (see "Laying flat" below).

## Body, Detect Guides, and Edit Guides

- **Body**: Choose the avatar's body mesh. The garment is placed around it and drapes while colliding with this body.
- **Detect Guides**: Finds reference positions for placing pieces from the shape of the body. It fits cylinders (guides) over the torso, neck, arms, and legs. The avatar is assumed to stand upright and face -Y (otherwise you'll see "upright, facing -Y?"). The eye icon next to the button toggles whether the guides are shown on screen.
- **Edit Guides**: Adjust the cylinders by hand. When you press it, each cylinder becomes its own object, and you adjust them with G (move), R (tilt), and S (thickness; scaling only along the axis direction changes the length). The **L/R together** button appears only while you are editing; when it is on, moving one arm or leg guide makes the other follow to the mirrored position across the body's center. Press **Done** when you finish.
- When guides are found, you'll see `○ guides; 20 / 20 pieces placed` (number of guides, number of pieces placed / number of pieces that are on).

## Place and Back to Flat

- **Place** (a tool in the tool column on the left; the button in the panel switches to this tool):
  1. Click a pattern piece to select it (Shift to add).
  2. Move the cursor over a cylinder and that cylinder glows yellow. If no piece is selected, you'll see "Place: click pattern pieces first …".
  3. Press on a cylinder and the piece is placed there (green outline). If you keep holding and move, the piece slides around and along the cylinder, and it is set where you release. (If you press and drag a piece that is already placed, it moves only by the amount you move, without jumping. If you release it without moving, it is only selected and does not move. To change only the angle or only the height, or to tilt the piece, use Around (angle around the body), Height, and Tilt (tilt angle; at 90° the upper side goes out horizontally, and at 180° the piece is upside down, so the outer edge of a collar faces the neck side; the ↻ next to it adds +180°) in the Tool tab under "AC9 Piece".) Esc or right-click cancels.
  - If you drag a mirrored piece in 3D, the side you touched first becomes the one you operate on. That piece is placed on the cylinder you moved it to, and only its linked partner is placed on the opposite side.
  - If you have selected several pieces, they are placed side by side, keeping their arrangement from the pattern.
  - Pressing where there is no cylinder lays the pieces flat (see "Laying flat" below). Only a press on the pattern starts nothing (you'll see "Place: press on a guide, a surface or the ground …").
  - When you release, the shape the simulation will actually start from is shown. A band (such as a collar rib) is a ring of its own length, and it is widened and shown in red only where it has to go around the body. A piece that would start while overlapping something also turns red, and the reason is shown.
- **Back to Flat**: Lays the selected pieces and their linked mirrored pieces flat **where they are in 3D, keeping their place and turn**. The shape bent round a cylinder (or the garment) is unrolled into the plane under the piece's center; the center does not move. Hand moves and edits made on the placed shape are discarded. When nothing is selected, after a confirmation, every placed piece. A dressed piece goes back to a placed piece in its flat shape. To take pieces out of 3D, set their state to **Off**.
- Placement rules: a piece is placed by only bending it along one cylinder, or flat; it is not stretched to follow the bumps of the body. Nothing moves on its own from where you placed it (pushing overlaps outward, lifting a band, and so on happen only when you press one of the tools below yourself).

## Laying flat (items without a body, bags)

- Pressing with **Place** where there is no cylinder lays the selected pieces flat.
  - On a face of the body, a Collision object or another mesh: the piece faces along the face's normal, 2 mm off it. The piece's top is the world up (Z) laid on the face.
  - On nothing: on the ground (Z = 0), face up.
  - Where a flat piece meets a bumpy face it may go through it; this is not fixed for you. Look at it and lift it with G.
  - Press and drag a flat piece to slide it over the face (or the ground) under the cursor.
  - On the ground the pieces' center holds on X = 0 (the pair's mirror plane: the torso's center when there is a body) and on Y = 0 when it comes near, with a line and a white dot. A left / right pair holds on Y = 0 only. Hold Ctrl for no snapping (the torso's center front / back too).
  - **Orientation (deg)** in the Tool tab's "AC9 Piece": how far the piece is turned on the face it lies on. 0 = the pattern's top up the face (on the ground: toward +Y), 90 = turned counter-clockwise. The ↻ beside it adds 90. Where it lies stays; a pair follows as a mirror image.
  - A pair follows as a mirror image once placed (moving one moves the other). Its state and buttons are in the Pair box of "AC9 Piece" (see "Pairs" below). A front / back pair snaps to X = 0 only, not Y = 0.
  - Its location and rotation can be typed in the Tool tab under "AC9 Piece", **Location & Rotation** (once its placed shape is made: **Move by Hand**).
- With pieces laid flat, **Phase Sim** and **Live Sim** run without a body.
- **Floor (Z = 0)**: the switch under Gravity in Run. On, it puts a floor at Z = 0 the cloth lands on (nothing is added to the scene). With no body, Collision object or floor, a run sews without gravity and stops there, and says so.
- What keeps the shape is mostly the fabric's stiffness. For bags, the fabrics **Canvas**, **Leather** and **Synthetic Leather** suit (Canvas's bending is an estimate).

## Tools for tidying the placed shape (no physics calculation)

Select the pieces first, then press any of these. They are tools for tidying the starting shape before the simulation.

- **Part to Seam**: Reflects the fold settings, and puts the sewing points of the selected pieces **on top of their sewing points on the targets you choose for this operation**. After that, it keeps the sewing points as they are, moves the piece out of the body, places it 2 mm on its own side of its partner, and restores the edge lengths (the same as "Settle"). The partner can be in its placed shape or its dressed shape. The shape may stretch. Stretching is shown by the colors of "Overlays > Stretch" and the one-line result; places that go into the body or through other cloth are shown in red. It refuses only when a triangle would collapse (a shape the simulation cannot start from). If moving the points on top would make the stretch exceed red (1.5 times), for example when the partners are placed far apart, it does not put them on top; it keeps the shape and moves it near the chosen targets, and tells you about the gap in one line. That seam is closed by the simulation. Press the button, then click a target. Shift-click adds or removes targets, A chooses all targets, Enter applies, and Esc cancels. This scope lasts for this operation only; other enabled seams remain enabled for the next simulation. If you want to float the piece further outward, press **Move Outside** separately. Hand placement, vertex edits, and folds from before the move are kept separately, so repeating the operation does not overwrite the prepared shape. If you change Fold Angle, press it again to redo the move from the new fold.
- **Band on Edge**: Lays a thin band (such as a neck rib) along the chosen targets' sewn edge and extends its width from that edge. Choose targets the same way as Part to Seam. Both tools show the moving pieces and any following pair, and keep the original selection and active object while you pick targets.
- **Move Outside**: Keeps the current shape of the selected pieces and moves them to the outer side of nearby overlapping cloth. It works for both the piece itself and its partner, whether in the placed shape or the shape after simulation. No sewing is required. It moves the piece as much as needed to be 8 mm away from the partner, and if it is already apart, it moves it a further 8 mm. After the correction, you can simulate again from that shape. If the outer side can't be determined, for example at a fold-back, or if the piece passes through another layer or the body, it shows the reason and keeps the shape.
- **Settle**: Tidies without a physics calculation (moves the piece out of the body and restores edge lengths). Places that remain are shown in red.
- For some pieces of a dressed garment, press **Back to Placed** first and then use these (you'll see "Back to Placed it first").

## Fixing the placed shape by hand: Move by Hand and Back to Guide

- **Move by Hand**: Makes the selected pieces movable by hand in 3D and switches to the move tool.
  - Before dressing: The placed shapes can be selected as one object per piece. You can fix them with G, R, or Edit Mode, and **Phase Sim** starts from that shape.
  - After dressing: A handle is attached to that piece of the dressed garment (see "Operations after dressing" below).
- **Back to Guide**: Discards the hand edits of the selected pieces (all pieces if none is selected; a confirmation appears) and returns them to the placement on the guides (the cylinders around the body). For a piece of a dressed garment, first return it to its placed shape (go through Back to Placed) and then do this.
- There are three "back" buttons, in order from nearest to farthest: **Back to Placed** (dressed garment or the shape moved to the seam, back to the prepared shape before the move; hand placement, vertex edits, and folds remain), **Back to Guide** (back onto the guides, discarding hand edits too), and **Back to Flat** (flat where it is, discarding hand edits too).
- If an old file does not have the shape from before the move, or if the pattern's mesh or pairs changed after the move, it shows the reason instead of guessing at a restoration. Undo the change, or place again with "Back to Guide".
- When you select one placed shape, the movement from the guide (Move and Turn) is shown. **Type Move** moves it by numbers.
- **Move by Hand** on a linked pair says in one line that the partner moves too, as its mirror image. To move one side only, unlink it in the Pair box below.

## Pairs (the Pair box in Tool tab > AC9 Piece)

When you select a piece, the **Pair** box in Tool tab > "AC9 Piece" shows its partner, the state, and the pair's buttons in one place (the pair rows at the bottom of the Placement panel moved here; the right-click menus keep theirs).

- "With ○○" and "Linked: moves as its mirror image": a pair drawn with **Mirror Copy**. If you place one, the other follows to the mirrored position.
- **Mirror Plane**: **Left / Right** or **Front / Back**. With Front / Back, the mirrored piece turns upside down in the pattern (edits then follow as the up-down mirror), and in 3D the pair is placed as mirror images across Y. The plane is the torso's center for a pair on the body (Y = 0 without one); for a pair laid flat it is the middle of the two when you switch to Front / Back, Link, or Mirror Placement (a back panel placed apart keeps its depth and is turned to the mirror image). Seams mirrored automatically use the same plane. Use it for things paired front and back, such as a bag's sides.
- **Unlink**: unlinks the pair so the two sides are placed separately (the pattern stays mirrored). **Link** restores it. An unlinked pair shows **Mirror Placement**, which copies one side's place and hand move to the other once.
- "Paired with ○○": a pair made with **Pair**. When you place them, the partner follows to the mirrored position, but movement you made by hand does not follow, so copy it with **Mirror Placement**. **Unpair** undoes the pair.
- "No pair": no partner. Select the two pieces you want to pair and press **Pair**.
- **Swap Sides**: swaps the sides of a placed pair (hand movement and folds stay on the location side, and the shape is rebuilt the same way as with **Place**).
- **Mirror 3D to Pair**: copies one side's simulated shape to the other. It is shown only when both sides of the pair are on the simulated garment.
- **Symmetric Edit** (one piece's two halves kept symmetric) is not a pair setting and stays outside the box.

## Operations after dressing

After dressing, you work on the one dressed garment.

- When you click a piece of the dressed garment with the **Place** tool or the **Edit Pieces** tool, a handle (an arrow Empty) appears at the center of that piece and it is selected. Shift+click adds more. The same happens if you select pieces in the pattern and then press **Move by Hand**.
- A piece newly placed after dressing appears in its placed shape (it is sewn on by the next **Phase Sim**).
- If you move the handle with G or R, that piece follows inside the dressed garment. Fix any places where it sinks in, and then run **Phase Sim**. If you move a piece far while it is sewn, it is pulled back at the start of **Phase Sim**.
- If you press **Back to Placed** with the handle selected, that piece returns to its placed shape.
- A spot where you clicked the dressed garment also appears as a red dot at the same position on the pattern (it follows even when you move through frames).
