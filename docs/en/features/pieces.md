---
title: Piece States and Layering
sidebar_position: 8
---

# Piece States and Layering

Each piece has four states (in the **Pieces** tab of **Lists**, or in **State** of **AC9 Piece** when you've selected a piece).

![the Pieces tab of Lists](../img/d_lists_pieces.png)

| State | Icon | Moves | Other cloth collides with it | Seams |
|---|---|---|---|---|
| **Active** | Play | Yes | Yes | Yes |
| **Freeze** | Snowflake | No (stays where it is) | Yes | Yes |
| **Off Pattern** | Ghost | No (stays where it is) | No (passes through) | Yes (the seams pull) |
| **Off All** | Closed eye | Not part of the mesh | — | No (the seams go away with it) |

- **Freeze** and **Off Pattern** hold the piece still in every step, including the sewing steps.
- The state buttons switch a left and right pair together (a mirrored pair, and a pair you made with **Pair**). To change just one side, Ctrl+click. A piece brought back from **Off All** is lined up with the mirrored shape of the side that stayed.
- **Invert Selection** in the **State** row reselects the other pieces of this garment (the steps for "garment groups").
- Pieces set to **Off All** show only as a count in the list. To bring them back, select the gray piece with **Edit Pieces** and set it to **Active**.
- The garment row in the **Pieces** tab: the filled circle marks the current garment, `10 on 2 fz 1 off` is the count per state, and the three icons on the right switch them all at once. A piece row is the name (press it to select) and four state buttons. A red row is a piece whose object alone was deleted with Blender's X, and you can bring it back with **Restore N Deleted Object(s)**.

## Layering

- Switch garments with **Garment** and run **Live Sim**, and other garments that have results automatically become "immovable obstacles". The order is: dress the inner one, then switch **Garment** to the outer one and run **Live Sim**.
- What becomes an obstacle is the other garment's last shape. Pieces you set to **Off Pattern** / **Off All** in the other garment are removed from the obstacles.
- When you simulate another garment, the earlier garments' history (the timeline frames) is dropped and only the last shape stays (the Result field becomes `Frames: dropped (shape kept)`).
- In the **Collision** tab, you can make things that have no pattern (props, or garments that are ordinary meshes) into obstacles. Select the object and press **Add Selected**; press **Remove** to stop.
- Right now, only the current garment moves in the simulation.
