---
title: About This Chapter
sidebar_position: 1
---

# How It Works

This chapter is not about how to use the add-on. It explains how AC9 Cloth Sim keeps its data behind the scenes and
when it makes things again.

You do not need it to dress a garment. It helps when:

- the same piece seems to be handled differently ("Blender's own tools moved it a moment ago, now they do not")
- Blender behaves unexpectedly: the gizmo shows up in an odd place, or something you picked is not picked
- you want to automate with a script or change the add-on

| Page | What it covers |
|---|---|
| [Data Model](data-model.md) | One set of pattern data, and the three kinds of objects made from it. Which ones Blender's own tools can touch |
| [Selection and the Gizmo](selection.md) | Where the piece selection is kept. Why a dressed piece is picked only with the AC9 tools. When the gizmo is off |
| [Simulation and Saving](simulation.md) | From pressing the button to the garment's shape coming back. What goes into the .blend and what stays outside |
| [For Developers](for-developers.md) | Where things are in the source, and the rules the code keeps |
| [UI timing recorder (experimental)](ui-timing.md) | Opt-in local timing logs, their location, and attribution limits |

The user's side of the same ideas is in [Concepts](../concepts.md); this chapter is what lies behind it.
