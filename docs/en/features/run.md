---
title: Dress (run)
sidebar_position: 10
---

# Dress (run)

The run buttons are grouped in **AC9 Run** at the top of the Tool tab. Always shown: **Simulate** with the **Gravity** checkbox beside it, **3D Follows Edits** and **Update 3D** ("Reflecting pattern edits on the dressed garment"), and one line of status. **Fix by Hand** appears when progress playback is enabled for the garment. **Floor (Z = 0)**, the Smooth and Strength settings of the live brush, the whole status, the log, **Delete Cache** and **Keep Cache** are inside the collapsed **Settings**. **Back to Adjusted Shape**, which returns to the adjusted shape, is in the **Placement** panel together with the other "back" buttons. **Simulate** is both how you dress the garment and how you settle it after fixing.

## Simulate

- Purpose: sew and dress. Settle again, continuing from an edited pattern or a shape you fixed by hand. Pinch and fix the cloth by hand.
- Prerequisite: a Body is selected and guides exist from **Detect Guides** (otherwise the button is grayed out). Only pieces already placed in 3D are calculated. Unplaced pieces and the seams connected to them are left out of that run. The pattern on/off settings do not change. Pieces laid flat run even without a body.
- Steps: press **Simulate**. The first time you dress, it pulls the seams together from the adjusted shape and lets the cloth hang. Once dressed, it continues from the shape on screen (pieces set to **Freeze** stay as they are). If you have edited the pattern, the edits are first carried onto the garment before it runs.
- How to stop: it does not stop by itself. When the garment has settled, press the button again (it has changed to **Stop**) or press **Esc**. The shape where it stopped is the result. There is no frame limit on running time. Retained progress has separate settings and a recording limit; see Recording progress below.
- **Gravity**: on by default. You can switch it while it runs. When you want to close the seams first (to sew a collar or sleeves on before they fall), turn gravity off and run, then turn gravity on once the seams have closed.
- **Floor (Z = 0)**: when on, the cloth rests on a floor at height 0 (nothing is added to the scene). Decide this before you run. It is inside **Settings**.
- **Freeze Garment While Sewing**: on by default, inside **Settings**. When you place one piece again (a collar, a band) on a garment already sewn and run, the garment stays still until that piece is sewn on, so the stitches bring the piece to the garment instead of lifting the garment. Meanwhile the garment is tinted like **Freeze** and cannot be grabbed, and a line under the run buttons says so; once the piece is sewn on, the garment moves and can be grabbed again. If the piece cannot reach its seams, the garment is let go after a while and comes to meet it. Off: the garment and the piece pull on each other, and the garment rises toward the piece. Decide this before you run.
- While it runs, you can pinch and pull the cloth with **Ctrl + left drag** (radius 2 cm). If cloth has pushed through other cloth, press **Alt** during the drag and the cloth you hold passes through other cloth (only while you hold it). If you start grabbing while holding Alt, you grab only the single piece under the mouse.
- **Ctrl + Shift + left drag** is the **Smooth** brush. It holds down the floating cloth inside the brush circle and carries it in the direction you drag. Wrinkles are carried along and leave through the hem or loose areas (the cloth length stays the same; this differs from Sculpt's Smooth, which erases wrinkles in place). Where a piece inside the circle has pushed through itself and flipped over, it is returned to its original orientation as you stroke (areas near fold lines, such as overlapping box pleats, are overlapped on purpose, so they are left alone. Fix crossings with a different piece using Ctrl + Alt pass-through). You can change the radius and strength with **Smooth** and **Strength** in **Settings**. Pieces set to **Freeze** and pieces set to **Off** cannot be grabbed (if you try, it tells you so).
- The line below the run button lists what takes effect now: gravity, seam on/off, active/freeze, piece elastic, Strengthen on/off and multiplier, and eligible fold angles, directions and Taper. See [fold timing](fold-roles.md#when-settings-apply) and [Strengthen](fabric.md). Material properties and particle distance take effect on the next run.
- Expected result: while it runs, the garment moves on screen and the status appears below the button. When you stop it, "done: …" appears (one line; the whole of it is inside **Settings**).
- Limits: after you press it, wait a little before the cloth starts to move (it depends on the computer). Pinch the cloth after it starts moving. If it does not start, `… did not start: <reason>` stays in the status display.

![the AC9 Run panel](../img/d_tool_tab.png)

## Experimental Features

When you turn on **Experimental Features** in the add-on preferences (Edit -> Preferences -> Add-ons -> AC9 Cloth Sim), features that are still being tried appear. It is off by default, and this manual is written for the screen with it off.

- **Phase Sim**: sews step by step and stops by itself once all the set steps are done. To retain progress after successful completion, turn **Keep Cache** on. When off, only the last shape remains.
- How the seams pull together, and a piece's **Layer**.

[Piece Roles (Experimental)](roles.md) explains roles and Phase Sim. Roles are not required for normal Simulate.

## Back to Adjusted Shape (Placement panel)

- Returns to the adjusted shape (the amount you moved by hand is kept). It does not run a simulation. To dress again, press **Simulate** after **Back to Adjusted Shape**.
- If you select pieces and then press it, at that moment only those pieces in the dressed garment (both sides of a left/right pair) return to the adjusted shape. The panel shows "Next: 2 piece(s) from placed". The next **Simulate** sews those pieces in from their current shape and continues the others from the result. You may also move the returned pieces with the handle before pressing **Simulate**.
- If you press it with nothing selected, it discards the result for the whole garment and returns to the adjusted shape (a confirmation appears). The next **Simulate** dresses from the start.
- How to undo: Ctrl+Z undoes it (the job folder remains).

## Fix by Hand

- **Press this before fixing in Sculpt or Edit Mode.** It makes the frame shown on screen the garment's actual shape and goes straight into Sculpt Mode (the small button on the right goes to Edit Mode). After you fix it, press **Simulate** and it continues from the shape you fixed.
- When to use it: when a collar flipped over in the simulation, cloth sank into the body, or cloth got tangled, and fixing by hand is faster. Choose the frame you want to fix on the timeline.
- Pressing it stops the playback of the progress (Ctrl+Z undoes it).
- It appears only while the progress is playing. When progress is not playing, the garment's shape is the mesh itself, so you can sculpt without pressing it.
- If you forget to press it and enter Sculpt or Edit Mode while it is playing, a red message appears in the viewport and this button also turns red (while it is playing, hand edits do not show in the shape).
- Even the shape you fixed gradually moves, in the continued simulation, toward the shape that the seams, the body, and the fabric forces settle into. If you want to keep the hand-fixed shape, stop the continued run early.

## Recording progress {#recording-progress}

- **Delete Cache**: deletes the progress frames and their files. The garment keeps its current shape and you can continue from it. Deleted frames cannot be restored.
- **Keep Cache**, in **AC9 Run > Settings** or the add-on preferences, is on by default. Normal Simulate retains the sewing interval and a short period after it. When off, a successful or user-stopped run keeps only the last shape. Diagnostic records from a failed run are handled separately.
- **Keep All Simulate Frames** is available when Keep Cache is on, and is off by default. It records beyond sewing, up to 1,500 intermediate frames. Calculation continues after the recording limit and the final shape is kept. It does not guarantee continuous playback of the entire run.
- With sewing-only recording, playback jumps from the end of that interval to the last shape. If only the start and last shape were recorded, progress frames are discarded. Check Result after stopping.
- Save the shape in the .blend with Ctrl+S. To move progress to another PC, also keep the matching `AC9_ClothSim_jobs` folder beside the .blend. Saving the .blend under another name alone does not back up progress; back up the result folder separately before trying deletion.

## Result and status display

When you stop the simulation, **Result** in **AC9 Garment** on the Tool tab shows what the result is (if you stopped it, `(stopped)`), the date and time, the frame count, and the time taken.

![the dressed garment and the Result of AC9 Garment](../img/fg_dressed.png)

- `Shape: Ctrl+S to keep it` is a sign that the result shape has not yet been saved in the .blend (after saving it becomes `Shape: saved in the blend`). For a file that has never been saved, `Shape: file never saved`.
- `Frames: on the timeline` is a sign that the progress can be played back. After **Fix by Hand**, it is `Frames: off (shape kept)`; after the frames were deleted (after a run that did not retain progress, or after **Delete Cache**), it is `Frames: dropped (shape kept)`.
- The last line is the job folder name; open it with **Open**. If it cannot be found (for example, you passed along only the .blend), `Result: folder gone` and `Shape stays in garment` appear. The shape remains in the garment and you can continue from it.
- Opening **Settings** below the status display shows the whole status of the last run, the last few lines of the log, and **Log** (the whole previous log). If something looks wrong, please include the contents of **Log**.
