# AC9 Cloth Sim

[日本語](README.md)

A Blender add-on that imports garment patterns, sews them together, dresses an avatar with them and lets you check how the cloth settles with a physics simulation.
Fixing a pattern and dressing the avatar again to check it all happens inside Blender.

<!-- screenshot: pattern → sewing → dressed garment, as 3 images or a short animation (GIF) -->

- Import patterns: patterns written by 3D garment software (a CLO BOM, `BOM.xml`), DXF, and 3D meshes with UV seams. You can also draw a pattern from scratch.
- Fix and draw patterns: drag corners and edges, darts, slash-and-spread, notches, symmetric editing and more.
- Sew and dress: lay the pattern pieces round the body, then sew them together in stages to dress the avatar. In the mode that runs until you stop it, you can grab the cloth by hand and fix it.
- Fabrics: choose a fabric (stiffness, weight) for each piece. Interlining, elastic and buttons are supported too.
- On to the next step: the dressed garment can be handed to [AC9 Cloth Retopo](https://github.com/openfashion-labs/ac9_cloth_retopo) as its Guide.

Free to use, commercial use included (GPL-3.0-or-later).

## About the alpha

This is an **alpha (0.1)**. Please use it knowing that:

- Features are still missing.
- The screens and steps will keep changing.
- A saved file (.blend) may not open in the next version.
- Even with the same settings, the result differs a little from run to run. Don't judge a setting from a single run.

## Requirements

| Item | What you need |
|---|---|
| Computer | Windows (64-bit). **It does not run on a Mac.** |
| GPU | **NVIDIA** (GeForce RTX and so on). Keep the driver up to date. |
| Blender | **5.2** |
| Free disk space | About 1 GB |

Dressing the garment is computed on an NVIDIA GPU. Without one, the run buttons do not work.
To check your GPU: open Task Manager and see whether "GPU" under "Performance" says "NVIDIA".

## Installation

1. Keep the release ZIP file (`ac9_cloth_sim-…zip`, about 190 MB) as it is, **without extracting it**.
2. Start Blender 5.2 and open Edit → Preferences.
3. Choose "Add-ons" in the list on the left, then "Install from Disk..." from the small "∨" at the top right.
4. Select the ZIP file and press "Install from Disk" at the bottom right. It is a large file, so wait a little until it finishes.
5. You are done when "AC9 Cloth Sim" appears in the add-on list with its check box on.

To check that it is installed, press **N** in the 3D viewport and look for the "**AC9 Cloth Sim**" tab in the sidebar.

- After installing a new version, **close Blender and start it again.** If you keep using it without restarting, parts of the previous version can remain and cause errors.
- To use it in Japanese, choose Japanese under Preferences → Interface → Translation. This add-on's buttons switch to Japanese too (some stay in English).
- The first time you dress a garment, the GPU is prepared before the run starts, so it takes a while (some tens of seconds; it depends on the computer). Later runs start faster.

## Your first garment (outline)

The button names are the ones shown in the English interface. The detailed steps are in the [user guide](#user-guide).

1. **Save the .blend first.** The run's frames are written to a folder next to the saved .blend (without saving, they go to a temporary folder and cannot be played back later).
2. Import the avatar (the body). It is expected to stand straight, facing −Y.
3. In the sidebar's "AC9 Cloth Sim" tab, press **Work Screen** under **Setup** to switch to a screen laid out for the work.
4. Import the pattern: **Import BOM**, **Import DXF**, **Import SVG**, or **From Mesh** for a 3D mesh. DXF and SVG files carry no seams, so add them with **Add Seam** after importing.
5. Under **Placement**, pick the body mesh as **Body** and press **Detect Guides**. Guides for placing the pieces appear round the body's torso, neck, arms and legs.
6. With the **Place** tool, select pattern pieces, press on a guide, drag to set the position and release.
7. In the **Tool** tab of the Properties editor at the bottom right (AC9 Run, at the top), press **Phase Sim**. The pieces are sewn together in stages and the garment settles on the body. Press **Esc** to stop.
8. Where you don't like the result, fix the pattern and press Phase Sim again (it goes on from the current shape). To dress the avatar again from the start, press **Back to Placed** with nothing selected, then Phase Sim.
9. The dressed garment is saved in the .blend (**Ctrl+S**).

<!-- screenshot: the whole Work Screen (sidebar, pattern view, Tool tab) -->

### Common stumbling points

- **The run buttons are not in the sidebar but in the Tool tab of the Properties editor at the bottom right.** If the Properties editor shows another tab (Render and so on), you won't find them.
- **Patterns cannot be fixed with Blender's usual editing (Edit Mode, G/R/S).** Use the **Whole Pieces** tool to move whole pieces and the **Edit Pieces** tool to fix corners and edges.
- **A piece's role** (front, placket, collar stand, sleeve, cuff ...) decides the order it is sewn in, how it is placed (bands as a ring, sleeve panels side by side round the arm) and its start shape. After checking the seams and before placing, press **Propose Roles** in the **Piece Roles** panel and look again at the pieces marked for checking. See [Piece Roles and What Gets Decided Automatically](docs/en/features/roles.md) in the user guide.
- **Hover over a button that cannot be pressed (gray) to see why.**
- On a **laptop** without a middle button or numpad, turning on "Emulate 3 Button Mouse" and "Emulate Numpad" under Preferences → Input makes moving the view easier.
- **Keyboard shortcuts don't work while a Japanese (or other) input method (IME) is on.**

## Troubleshooting

| Symptom | Check first |
|---|---|
| The run buttons are gray and cannot be pressed | Did you pick the **Body** and press **Detect Guides**? Hover over the button to see why. |
| "○ piece(s) not placed yet" appears in red | Some pieces are not placed. Place them with **Place**, or turn off the pieces you don't use. |
| "Set the Newton Python in the add-on preferences" appears | Did you install from the release ZIP? A development setup needs that preference field filled in. |
| The screen differs from the guide, or "cannot import name …" appears | Did you restart Blender after installing a new version? |
| The result differs a little every time | In the alpha the result varies even with the same settings. Run it a few times and compare. |

For other symptoms, see [Troubleshooting](docs/en/troubleshooting.md) in the user guide.

## User guide

- [User guide (English)](docs/en/index.md): getting started, Blender basics, your first garment, concepts, each feature, troubleshooting, glossary. The same content as the documentation site.
- [User guide (Japanese)](docs/ja/index.md)
- Documentation site: in preparation.

Notes for AI assistants (operation IDs, how to read the state) are in [LLM/procedures.md](LLM/procedures.md) (`LLM/README.md` is a copy of part of the user guide).

## Reporting problems

Please post them in [Issues](https://github.com/openfashion-labs/ac9_cloth_sim/issues). It helps if you include:

- A screenshot of the screen (with the error text readable)
- The add-on's version number (open "AC9 Cloth Sim" under Preferences → Add-ons)
- The GPU's name (Task Manager → "Performance" → "GPU")
- The buttons you pressed and what was shown then

## License

[GPL-3.0-or-later](LICENSE). The simulation uses NVIDIA Newton.

Copyright (C) 2026 OpenFashion. This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. `solver/newton_patch/` is a modified copy of NVIDIA Newton under Apache-2.0 (its `LICENSE` and `NOTICE` in the same folder).
