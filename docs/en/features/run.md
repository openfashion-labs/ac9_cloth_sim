---
title: Dress (run)
sidebar_position: 10
---

# Dress (run)

The run buttons are grouped in **AC9 Run** at the top of the Tool tab. From the top: **Phase Sim**, **Live Sim** and **Local Sim** (each with a **Gravity** checkbox below it), **3D Follows Edits** and **Update 3D** ("Reflecting pattern edits on the dressed garment"), and **Fix by Hand**. The Smooth and Strength settings of the live brush are inside the collapsed **Live Options**, and **Delete Cache** and **Keep Cache** are inside the collapsed **Details**. **Back to Placed**, which returns to the placed shape, is in the **Placement** panel together with the other "back" buttons. Normally pressing **Phase Sim** alone is enough.

## Phase Sim

- Purpose: sew and dress, or settle again, continuing from an edited pattern or a shape you fixed by hand.
- Prerequisite: a Body is selected and guides exist from **Detect Guides** (otherwise the button is grayed out). Only pieces already placed in 3D are calculated. Unplaced pieces and the seams connected to them are left out of that run. The pattern on/off settings do not change. If zero pieces are placed, it tells you there is nothing to calculate.
- Steps: press **Phase Sim**. The first time, it divides the pattern into a mesh, arranges the placed pieces around the body, and sews and drapes stage by stage. From the second time on, it continues from the frame shown on screen (pieces set to **Freeze** stay as they are, the others drape again). If you have edited the pattern, the edits are first carried onto the garment before it runs.
- Expected result: while it runs, the garment moves on screen, and below it you see a progress bar (`41 % of the run`) and the current stage and frame. The first 10 seconds or so after you press it is the solver starting up (`starting the solver`). When it finishes, "done: …" appears and the progress goes onto the timeline (every other frame of all stages; the last frame is the result).
- How to stop: the button changes to **Stop**. Press it again or press **Esc**. The frames up to that point remain, and pressing **Phase Sim** continues from there. While it runs, other buttons are grayed out.
- Limits: if it does not start, `… did not start: <reason>` stays in the status display. If it fails partway, the frames up to that point remain and the reason it stopped is shown.

## Live Sim

- Purpose: starting from the shape on screen, keep running until you stop it (when you want to pinch and fix the cloth by hand, or watch it while changing settings).
- Steps: press **Live Sim**. The button changes to **Stop**; press it again or press **Esc** to stop. The shape where it stopped is the result. There is no frame limit. The frames along the way are not kept; only the final shape remains.
- While it runs, you can pinch and pull the cloth with **Ctrl + left drag** (radius 2 cm). If cloth has pushed through other cloth, press **Alt** during the drag and the cloth you hold passes through other cloth (only while you hold it). If you start grabbing while holding Alt, you grab only the single piece under the mouse.
- **Ctrl + Shift + left drag** is the **Smooth** brush. It holds down the floating cloth inside the brush circle and carries it in the direction you drag. Wrinkles are carried along and leave through the hem or loose areas (the cloth length stays the same; this differs from Sculpt's Smooth, which erases wrinkles in place). Where a piece inside the circle has pushed through itself and flipped over, it is returned to its original orientation as you stroke (areas near fold lines, such as overlapping box pleats, are overlapped on purpose, so they are left alone. Fix crossings with a different piece using Ctrl + Alt pass-through). You can change the radius and strength with **Smooth** and **Strength** below Live Sim. It cannot be used with AC9 Fast Live. Pieces set to **Freeze** and pieces set to **Off** cannot be grabbed (if you try, it tells you so).
- Things that take effect while it runs: fold angles, **On**/**Off** of seams, **Freeze**, a piece's **Elastic**, and **Gravity**. The values remain after you stop. Everything else takes effect the next time you press it.
- To fix the inside and outside order of a garment with **Layer**, set the larger number on the outside and run, then stop once you have checked the order. Release it with **All Layers to 0**, then settle the shape with Live Sim again. Layer is not released automatically, and changes take effect from the start of the next simulation. The extra processing that pulls back deep penetrations works only while different Layers exist, and does not run when you recalculate with all of them at 0.
- You can press it even if you have never dressed the garment (it sews in one stage with gravity from the placed shape; for a rough look at the shape).
- Limits: it takes a while after you press it before the cloth starts to move. Pinch the cloth after it starts moving.

## Local Sim

- Purpose: move only the selected pieces to look at or adjust their shape (folds that do not fold in the placed shape, shapes of small parts, and so on).
- Steps: select pieces (you can select them on the pattern, the placed shape, or the dressed garment) and press **Local Sim**. It runs until you press **Esc** or **Stop**, and the shape where you stopped remains.
- Pieces you have not selected are treated the same as **Off (All)** in the meantime (they do not move and do not collide), and only seams between selected pieces pull. **Gravity** is off by default.
- If there is a dressed garment, the result stays in that garment's shape; before dressing, it stays in the placed shape. If no pieces are selected, "select the pieces to simulate first" appears and it does not start.

## Back to Placed (Placement panel)

- Returns to the placed shape (the amount you moved by hand is kept). It does not run a simulation. To dress again, press **Phase Sim** after **Back to Placed**.
- If you select pieces and then press it, at that moment only those pieces in the dressed garment (both sides of a left/right pair) return to the placed shape. The panel shows "Next: 2 piece(s) from placed". The next **Phase Sim** sews those pieces in from their current shape and continues the others from the result. You may also move the returned pieces with the handle before pressing **Phase Sim**.
- If you press it with nothing selected, it discards the result for the whole garment and returns to the placed shape (a confirmation appears). The next **Phase Sim** dresses from the start.
- How to undo: Ctrl+Z undoes it (the job folder remains).

## Fix by Hand

- **Press this before fixing in Sculpt or Edit Mode.** It makes the frame shown on screen the garment's actual shape and goes straight into Sculpt Mode (the small button on the right goes to Edit Mode). After you fix it, press **Phase Sim** or **Live Sim** and it continues from the shape you fixed.
- When to use it: when a collar flipped over in the simulation, cloth sank into the body, or cloth got tangled, and fixing by hand is faster. Choose the frame you want to fix on the timeline.
- Pressing it stops the playback of the progress (Ctrl+Z undoes it).
- It appears only while the progress is playing. When it is not playing, such as after **Live Sim**, the garment's shape is the mesh itself, so you can sculpt without pressing it.
- If you forget to press it and enter Sculpt or Edit Mode while it is playing, a red message appears in the viewport and this button also turns red (while it is playing, hand edits do not show in the shape).
- Even the shape you fixed gradually moves, in the continued simulation, toward the shape that the seams, the body, and the fabric forces settle into. If you want to keep the hand-fixed shape, stop the continued run early.

## Delete Cache and Keep Cache

- **Delete Cache**: deletes the progress frames and their files. The garment keeps its current shape and you can continue from it. Deleted frames cannot be restored.
- **Keep Cache** (add-on preference): when turned off, only the last shape is kept each time and the progress frames are deleted.

## Result and status display

When **Phase Sim** finishes, **Result** in **AC9 Garment** on the Tool tab shows what the result is (if you stopped it, `(stopped)`), the date and time, the frame count, and the time taken.

- `Shape: Ctrl+S to keep it` is a sign that the result shape has not yet been saved in the .blend (after saving it becomes `Shape: saved in the blend`). For a file that has never been saved, `Shape: file never saved`.
- `Frames: on the timeline` is a sign that the progress can be played back. After **Fix by Hand**, it is `Frames: off (shape kept)`; after the frames were deleted (after **Live Sim**, **Delete Cache**, and so on), it is `Frames: dropped (shape kept)`.
- The last line is the job folder name; open it with **Open**. If it cannot be found (for example, you passed along only the .blend), `Result: folder gone` and `Shape stays in garment` appear. The shape remains in the garment and you can continue from it.
- Opening **Details** below the status display shows the last few lines of the log, **Log** (the whole previous log), and **To AC9 Cloth Retopo**. If something looks wrong, please include the contents of **Log**.
