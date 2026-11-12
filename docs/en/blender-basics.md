---
title: Blender basics
sidebar_position: 3
---

# Blender basics

This page gathers only the Blender views and operations you need to use AC9 Cloth Sim. For Blender as a whole, see [the official Blender manual](https://docs.blender.org/manual/en/latest/) and similar resources.

Blender's menus and buttons appear in Japanese if you switch Blender to Japanese. On this page, Blender's own menus are written with their English names.

<!-- video: Blender basics (3 to 5 minutes, recorded in plain Blender with the default screen) -->

## Reading the screen

<!-- screenshot: the whole work screen, with numbers marking the 4 places below -->

AC9 Cloth Sim is operated from these 4 places.

| Place | Where it is | What it is for |
|---|---|---|
| **3D Viewport** | The large part of the screen | Where you see the body and garment in 3D. The pattern is also laid out flat here |
| **Sidebar** | The right edge of the 3D Viewport. Show or hide it with the **N** key | The vertical "AC9 Cloth Sim" tab holds the panels for loading, pattern, lists, placement, and measurement |
| **Toolbar (tool column)** | The left edge of the 3D Viewport. Show or hide it with the **T** key | Where you switch "tools" for fixing the pattern, adding seams, placing, and so on |
| **Tool tab of the Properties editor** | Lower right of the screen. Among the vertical icons, the tab shaped like a screwdriver and wrench | Shows the **run buttons** (AC9 Run) and the settings of the selected item |

- **The run buttons (such as "Phase Sim") are in the Tool tab, not in the sidebar.** You won't find them if the Properties editor is on another tab (Render, Object, and so on).
- The tabs lined up at the top of the screen (Layout, Modeling, and so on) are "workspaces", which switch how the screen is arranged. Pressing **Work Screen** in the sidebar creates a workspace for AC9 Cloth Sim.

### Work Screen

| Position | What is there |
|---|---|
| Left | 3D Viewport (see the body and garment in 3D) |
| Middle | Pattern view (the flat pattern seen from directly above. **It cannot be rotated**) |
| Right | Tool tab of the Properties editor |

The work screen has no Outliner (the list of things in the scene) and no Timeline (the strip of frames). To move through frames, see "Frames and the Timeline" below.

## Moving the view

| Action | Mouse / keys |
|---|---|
| Rotate | Drag while holding the middle button (wheel) |
| Pan | Drag with **Shift** + middle button |
| Zoom in/out | Turn the wheel |
| View from front, side, top | **1** / **3** / **7** on the numpad |
| Zoom to the selected item | **.** (period) on the numpad |
| See everything | **Home** |
| Maximize/restore the current view | **Ctrl+Space** |

- The pattern view in the middle of the work screen does not rotate. Only the 3D Viewport on the left rotates.
- You can also rotate by dragging the colored axis circle at the upper right of the 3D Viewport (the navigation gizmo). Clicking an axis circle shows the view from that direction.

### When you have no middle button or numpad (laptops, etc.)

In "Edit" > "Preferences" > "Input", turn on these two options.

- **Emulate 3 Button Mouse**: **Alt** + left drag acts as a middle-button drag.
- **Emulate Numpad**: The number keys in the top row of the keyboard act as the numpad.

## Selecting

| Action | Mouse / keys |
|---|---|
| Select | Left click |
| Add / remove | **Shift** + left click |
| Select all | **A** |
| Deselect all | **Alt+A** |

- The item you selected last is called the "active" one, and it is drawn in a brighter color than the other selected items. **The Tool tab shows the settings of the active item.** If the setting you expect doesn't appear, check what is active.
- For a button that can't be pressed (gray), put the cursor on it and the reason it can't be pressed appears.

## Moving and undoing

| Action | Keys |
|---|---|
| Move / rotate / scale | **G** / **R** / **S**. Then **X**, **Y**, or **Z** to fix the axis, and type a number for the value |
| Confirm | Left click or **Enter** |
| Cancel (in the middle of an operation) | Right click or **Esc** |
| Undo / redo | **Ctrl+Z** / **Ctrl+Shift+Z** |
| Change the values of the last operation | **F9** (or the small panel at the lower left of the screen) |
| Delete | **X** or **Delete** |
| Search for an operation by name and run it | **F3** |

**Pattern pieces do not move with Blender's G / R / S.** The pieces are locked. To move a whole piece, use the **Whole Pieces** tool, and to fix corners or edges, use the **Edit Pieces** tool.

Right-click has two meanings. In the middle of an operation it cancels, and when you aren't doing anything it shows a menu of what can be used on the item under the cursor (while you hold an AC9 tool).

## Object Mode and Edit Mode

Blender has "modes". You switch them at the upper left of the 3D Viewport, and you can also go back and forth with the **Tab** key.

- **Object Mode**: The mode that handles things (objects) as a whole. **AC9 Cloth Sim is operated in this mode.**
- **Edit Mode**: The mode for directly fixing the vertices, edges, and faces of one object.

Notes:

- **Even if you fix a pattern in Edit Mode, it doesn't go into the pattern data.** The pattern is a display that is rebuilt from the pattern data (the information of one garment). Fix it with AC9's tools.
- AC9's tools are only in the Object Mode tool column. **When you enter Edit Mode, the AC9 tools disappear.** If the tools vanish, return to Object Mode with **Tab**.
- You use Edit Mode only when fixing the shape of the dressed garment by hand (after **Fix by Hand**).

## Hiding and showing

- **H** hides the selected item, and **Alt+H** shows everything. A hidden item has not been deleted.
- When you don't want to use a pattern piece, don't hide it with **H**; set the piece's state to "Off" (see "The 4 states of a piece" in [Concepts](concepts.md)).
- If you turn off the button of two overlapping circles at the upper right of the 3D Viewport (Overlays), the lines and colors that AC9 Cloth Sim draws on screen also disappear.

## Frames and the Timeline

The progress of the simulation is kept as an animation, frame by frame.

- **Space** plays and stops.
- **←** / **→** move one frame at a time, and **Shift+←** / **Shift+→** jump to the first and last frame.
- **Some operations depend on which frame is shown.** The second and later **Phase Sim** continues from the frame shown on screen. When you send to Cloth Retopo or measure the garment, the frame shown on screen is used too. Before pressing, check that you are on the last frame.
- To show the Timeline (the strip of frames), choose "Timeline" from the editor type menu at the upper left of the area.

## Saving files

- Save with **Ctrl+S**. The first time you save, you are asked for a location and name.
- **Save once before simulating.** The frames of the progress are created in the `AC9_ClothSim_jobs` folder next to the saved .blend. If you haven't saved, they are created in a temporary folder and can't be played back later.
- The final shape of the dressed garment is saved inside the .blend. Even if you hand only the .blend to another computer, the final shape is visible and you can continue from it (only playback of the progress is not possible).
- If you move or rename the .blend after saving, the progress folder can no longer be found (the final shape remains).

## Installing the add-on and language

- For how to install the add-on, see "Installation" in the [README](https://github.com/openfashion-labs/ac9_cloth_sim/blob/main/README.en.md#installation). After installing a new version, restart Blender.
- To use it in Japanese or another language, set "Translation" to that language under "Interface" in "Edit" > "Preferences". The sidebar tab name "AC9 Cloth Sim" and some text and error messages stay in English.

## Common stumbling points

- **While a Japanese or other input method (IME) is on, keyboard shortcuts don't work.** If keys don't work, first turn the input method off.
- Keys act on the area the cursor is over. For operations in the 3D Viewport, put the cursor over the 3D Viewport first.
- If you pressed something, the screen changed, and you think you can't undo it, try **Ctrl+Z** first.
