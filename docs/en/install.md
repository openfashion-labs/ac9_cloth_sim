---
title: Installation and Requirements
sidebar_position: 2
---

# Installation and Requirements

## Installation

1. Use Blender **5.2** (the distributed version won't install on Blender versions earlier than 5.2).
2. Install the distributed add-on from Blender's Preferences under Add-ons, and turn on **AC9 Cloth Sim**.
3. After you replace the add-on, the safest thing is to quit Blender once and start it again (old modules can stay loaded). If it stalls at F3 Reload Scripts, restart as well.

## Requirements for the calculation

- The simulation is calculated by a separate program, NVIDIA's Newton. Open **AC9 Cloth Sim** in Preferences under Add-ons. The distributed version uses the bundled one, so you can leave the **Newton Python** field empty. In a development environment, put a Python that has Newton installed in this field.
- If it isn't specified enough, pressing **Simulate** shows "Set the Newton Python in the add-on preferences" and it doesn't start.
- **Fast Simulation Start** in the same Preferences (on by default): gets the calculation ready ahead of time and keeps it waiting, keeps the garment of the last Simulate run, and after a pattern edit prepares the next Simulate run in the background, so runs start sooner. It uses more GPU memory (more for a larger garment), and Blender may pause briefly after an edit. If the GPU runs out of memory, turn it off: every run then starts from preparation.
- You need Windows and an NVIDIA GPU (GeForce RTX 20, 30, 40 or 50 series). Keep the driver up to date ("About GPU support" in the README).
- Even with the same settings, the result can vary by several centimeters from one calculation to the next. Don't judge good or bad from a single result.

## What to do first

- With a test .blend, first save it under a different name after opening it. The simulation history (the timeline frames) is made in the `AC9_ClothSim_jobs` folder next to the saved .blend (if you haven't saved, it goes to a temporary folder, and you can't play the history back later).
- The last shape of the dressed garment stays inside the .blend. If you hand just the .blend to another PC, the last shape is visible when you open it, and you can continue from it with **Simulate** (only playing back the history isn't possible).
- It assumes the avatar is standing straight and facing the -Y direction (the front).
