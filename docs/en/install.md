---
title: Installation and Requirements
sidebar_position: 2
---

# Installation and Requirements

## Installation

> This guide describes the 0.4.1 candidate. Until split distribution is published, use the all-in-one ZIP from the [0.4.0 Release](https://github.com/openfashion-labs/ac9_cloth_sim/releases/tag/v0.4.0).

### Install through Extensions (0.4.1 — in preparation)

After publication, this section will link to the Extensions installation page. It is not available yet.

1. Open Blender 5.2 and allow online access in Preferences.
2. Drag the main installation button from that page into Blender.
3. If the repository is unknown, confirm adding it, then drag the same button again.
4. Install the add-on. In its preferences, use **Install Runtime** to add the compatible Runtime (Newton/Warp). Both are required on the first installation.
5. Add-on updates reuse the Runtime if its compatible version has not changed. Existing all-in-one installations are not migrated automatically.

### Install manually from a ZIP

1. Use Blender **5.2**; the distributed version does not install on earlier Blender versions.
2. Currently download `ac9_cloth_sim-0.4.0.zip` from the 0.4.0 Release. After 0.4.1 publication, its all-in-one file will be `ac9_cloth_sim-0.4.1-full.zip`. **Do not extract it**.
3. Open Edit → Preferences → Add-ons.
4. Choose **Install from Disk…** from the **∨** menu at the top right, select the ZIP, and install it.
5. Check that **AC9 Cloth Sim** is enabled.

### After installation and updates

- Press **N** in the 3D viewport and check for the **AC9 Cloth Sim** sidebar tab.
- After updating, **quit Blender and start it again**; old modules may otherwise remain loaded. Restart if F3 Reload Scripts stalls too.
- For Japanese, select Japanese under Preferences → Interface → Translation → Language. Some labels remain in English.
- The first simulation takes time to prepare the GPU; the wait depends on the computer.

## System requirements and GPU verification

- Windows 64-bit, Blender 5.2, about 1 GB free disk space. Mac is not supported.
- GPU programs have been checked to build for NVIDIA GeForce RTX 20, 30, 40 and 50 series. Actual hardware testing used RTX 50 series.
- Non-RTX NVIDIA GPUs such as GTX 10 and 16 series, and non-NVIDIA GPUs, are outside the supported scope. Non-RTX operation is untested.
- Use current drivers. An old driver triggers an update notice at simulation start; update through NVIDIA App or NVIDIA's official site.
- Check the GPU name in Task Manager → Performance → GPU.
- This is alpha software; saved .blend files may not open in a later version.

## Requirements for the calculation

- The simulation is calculated by a separate program, NVIDIA's Newton. Open **AC9 Cloth Sim** in Preferences under Add-ons. Split installations use the Runtime added through preferences; all-in-one ZIPs use bundled components, so you can leave the **Newton Python** field empty. In a development environment, put a Python that has Newton installed in this field.
- If it isn't specified enough, pressing **Simulate** shows "Set the Newton Python in the add-on preferences" and it doesn't start.
- **Fast Simulation Start** in the same Preferences (on by default): gets the calculation ready ahead of time and keeps it waiting, keeps the garment of the last Simulate run, and after a pattern edit prepares the next Simulate run in the background, so runs start sooner. It uses more GPU memory (more for a larger garment), and Blender may pause briefly after an edit. If the GPU runs out of memory, turn it off: every run then starts from preparation.
- You need Windows and an NVIDIA GPU (GeForce RTX 20, 30, 40 or 50 series). Keep the driver up to date (see "System requirements and GPU verification" above).
- Even with the same settings, the result can vary by several centimeters from one calculation to the next. Don't judge good or bad from a single result.

## What to do first

- With a test .blend, first save it under a different name after opening it. The simulation history (the timeline frames) is made in the `AC9_ClothSim_jobs` folder next to the saved .blend (if you haven't saved, it goes to a temporary folder, and you can't play the history back later).
- The last shape of the dressed garment stays inside the .blend. If you hand just the .blend to another PC, the last shape is visible when you open it, and you can continue from it with **Simulate** (only playing back the history isn't possible).
- It assumes the avatar is standing straight and facing the -Y direction (the front).
