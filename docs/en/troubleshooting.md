---
title: Troubleshooting
sidebar_position: 7
---

# Troubleshooting

## Look here first (common stumbling points)

| Problem | Cause and fix |
|---|---|
| I can't find the run buttons (such as **Live Sim**) | They are in the **Tool** tab of the Properties editor at the lower right, not in the sidebar. Check that Properties isn't on another tab such as Render. |
| Keyboard shortcuts don't work | Is a Japanese or other input method (IME) on? Is the cursor over the area you want to operate in? |
| The AC9 tools vanished from the tool column | Did you enter Edit Mode (return to Object Mode with **Tab**)? Is **Pattern Tools** in **Setup** turned off? |
| Pattern pieces don't move with **G** / **R** / **S** | The pieces are locked. Move them with the **Whole Pieces** tool. |
| A button is gray and can't be pressed | Put the cursor on the button and the reason it can't be pressed appears. |
| The pattern view in the middle doesn't rotate | The pattern view only looks from directly above and doesn't rotate. Rotate in the 3D Viewport on the left. |
| I can't rotate the view (no middle button) | Turn on "Emulate 3 Button Mouse" under "Edit" > "Preferences" > "Input", and you can rotate with **Alt** + left drag. |
| Pieces are invisible or faint | Gray translucent means a piece that is **Off All**. A red outline and a warning mean a piece whose object alone was deleted with Blender's **X** (you can restore it from the **Pieces** tab of the list). If you hid it with **H**, use **Alt+H**. If the overlays at the upper right of the 3D Viewport are off, the lines and colors AC9 draws also disappear. |
| I can't grab the cloth in **Live Sim** | The cloth doesn't move for a little while after you press it (it depends on the computer). Grab it after it starts moving. You can't grab pieces that are **Freeze** or Off. |
| The Result field shows `folder gone` | You moved or renamed the .blend after saving. The final shape remains, but the progress can't be played back. |
| After installing a new version, "cannot import name …" appears, or the screen differs from the description | Close Blender once and start it again. |

## Operations that can't be undone, and cautions

- **Back to Placed** (with nothing selected): Removes the result of the whole garment. A confirmation appears. You can undo with Ctrl+Z.
- **Live Sim**: It doesn't don't stop until you press **Stop** or **Esc**. The frames of the progress are not kept.
- **Delete Cache**: The progress frames you deleted can't be restored.
- **Live Sim** (from the second time), **To AC9 Cloth Retopo**, and **Measure Garment**: While the progress is being played back, all of them use the frame shown on screen. Check the Timeline position before pressing.
- **Fix by Hand**: Playback of the progress stops. To go back, use Ctrl+Z.
- **Watch**: Turn it on before you press **Live Sim**.
- Save a test .blend under another name before using it, and don't overwrite it.
- Red paint is an "anomaly candidate" and is not necessarily a defect as it is. Don't judge good or bad from a single result.


## When it doesn't work

| Symptom | What to check first | Next step |
|---|---|---|
| **Live Sim** doesn't start and shows "Set the Newton Python in the add-on preferences" | The **Newton Python** setting in Preferences | Fix the setting and press again. |
| **Live Sim** is gray | Is **Body** selected? Did you run **Detect Guides**? | Select the body and run **Detect Guides**. |
| "○ unplaced pieces are excluded from the calculation" appears | There are pieces not yet placed in 3D. | It can calculate with only the placed pieces. To include the unplaced pieces in the calculation, place them with **Place**. |
| **Detect Guides** shows "upright, facing -Y?" | The orientation and pose of the avatar | Use a body that stands upright and faces -Y. |
| The **From Mesh** button can't be pressed | Is a body selected in **Body**? Is the garment mesh active? | Select the body and make the garment mesh active. |
| **Fix by Hand** doesn't appear | Is the progress being played back? | When it is not being played back (for example after **Live Sim**), you can sculpt directly without pressing it. If there is no result, dress it with **Live Sim**. |
| BOM seams are attached to the piece itself or to pieces of another garment | The garment group lines in **Setup** (orange means an uncertain split). | **Update BOM Seams**. It keeps the garment classification you split by hand. For a needed classification correction, select pieces and use "Garment from Selection" in F3. |
| **Part to Seam**, **Band on Edge**, and **Settle** are gray | Are pieces selected? Are they placed? Are they part of a dressed garment? | Place them and then press. For pieces of a dressed garment, press **Back to Placed** first and then press. |
| Cloth stays passed through other cloth and doesn't come back | Are you in **Live Sim**? | In **Live Sim**, press **Alt** during a Ctrl+drag to let it pass through, and bring it back to the correct side. |
| No red appears | Are the eye button of **Overlays** and **Problems** on? Did you turn on **Watch** before **Live Sim**? | Turn them on again and run **Live Sim**. |
| After fixing the pattern, an error such as "changed outside the drafting tools" appears | If you change the pattern data directly as JSON, it can't be carried to the garment. | Fix it with the pattern tools, or do **Back to Placed** and dress again. |
| After swapping a seam, the cloth was pulled in | A swapped spot is treated as newly sewn. | This is normal. Run **Live Sim** next. |
| The collar flips over, or the joints twist | Roles and sewing order (**Piece Roles**), and the placement | Check **Propose Roles**. To fix it by hand, press **Fix by Hand** and then sculpt. |
| The garment collapses from the start (falls to the waist) | Stop with **Esc** and check which phase it is | Check the direction of the seams (**Flip the direction of**) and the placement. |
| The result differs from run to run | It varies even with the same settings. | Don't decide from a single run; run it several times and look. |
| The shape of the result isn't saved | The `Shape:` line of **Result** in **AC9 Garment** | Save the .blend (Ctrl+S). |
| Pulling the inside of a piece with **Edit Pieces** doesn't move the piece | Moving a whole piece is a different tool. | Switch to **Whole Pieces**. |
| I fixed the pattern but the dressed garment or placed shape didn't change. **Update 3D** is red | **3D Follows Edits** in **AC9 Run** is off. | Press **Update 3D**, or press **3D Follows Edits** again. |
| I can't make a shape into a hole (**Convert to Hole** refuses) | Is the shape closed? Does it touch the outline? Does it overlap another hole? Does it have a fold or elastic? | Move the shape away from the outline, and remove any fold or elastic. |
| The wording on screen differs from this guide | The add-on has been loaded in an old state. | Restart Blender. |

When you report a problem, please attach the button you pressed, the values you entered, the `done:` line of the status display (or `stopped` and the error text), the content of **Log**, and the saved .blend.
