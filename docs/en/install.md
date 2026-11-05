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
- If it isn't specified enough, pressing **Phase Sim** shows "Set the Newton Python in the add-on preferences" and it doesn't start.
- **Keep the Solver Ready** in the same Preferences (on by default): gets the calculation ready ahead of time and keeps it waiting, so the next run starts 3 to 5 seconds sooner. While it waits it uses about 200 MB of GPU memory, and it ends after 15 minutes of not being used. If you turn it off, it ends right away and every run starts from preparation.
- You need Windows and an NVIDIA GPU (as in the installation guide).
- Even with the same settings, the result can vary by several centimeters from one calculation to the next. Don't judge good or bad from a single result.

## What to do first

- With a test .blend, first save it under a different name after opening it. The simulation history (the timeline frames) is made in the `AC9_ClothSim_jobs` folder next to the saved .blend (if you haven't saved, it goes to a temporary folder, and you can't play the history back later).
- The last shape of the dressed garment stays inside the .blend. If you hand just the .blend to another PC, the last shape is visible when you open it, and you can continue from it with **Phase Sim** (only playing back the history isn't possible).
- It assumes the avatar is standing straight and facing the -Y direction (the front).
