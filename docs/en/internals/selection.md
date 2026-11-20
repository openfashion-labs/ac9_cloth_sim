---
title: Selection and the Gizmo
sidebar_position: 3
---

# Selection and the Gizmo

## Where the selection is kept

Which pieces are picked is kept as Blender's own selection (the objects' selected flag); AC9 keeps no separate copy.
So Undo (Ctrl+Z) and the Outliner change it too.

When a piece is picked, AC9 marks **every form** of that piece:

- the flat pattern object `AC9_Piece_<piece>`
- the placed object `AC9_Placed_<piece>` (when there is one)
- that piece on the dressed garment (the "handle" below)

That is why a piece picked in the 2D pattern is also picked in 3D, and the other way round.

## Tools that pick a dressed piece

The dressed garment is one mesh for all pieces ([Data Model](data-model.md)). Blender's own click and box select
pick only the garment as a whole.

When an AC9 tool (**Whole Pieces**, **Place** and others) clicks the dressed garment, AC9 finds which piece the
clicked face belongs to and puts a **handle** in the middle of that piece. The handle is an Empty,
`AC9_Move_<piece>` (collection `AC9_ClothSim_Move`). Moving the handle with G / R moves that piece's part of the
garment mesh with it. The move and rotate gizmo shows up on the piece because the handle is what is selected.

| To pick | Blender's own click and box select | Outliner | AC9 tools |
|---|---|---|---|
| a flat pattern piece | no (not picked) | yes | yes |
| a placed piece | no (not picked) | yes | yes |
| a dressed piece | no (the whole garment) | — | yes (a handle is made) |
| other objects (collision objects, the body) | yes | yes | — |

The line is drawn this way: **AC9 tools pick pieces, Blender's tools select objects.** Blender's own click, box,
lasso and circle select do not select pieces' objects. Pieces you had picked are dropped, and other objects are
selected as before. With Shift (adding), the pieces you had picked stay. The Outliner is the exception: clicking a
piece's object there picks that piece.

Clicking the dressed garment with Blender's own tools selects the garment object. Only then, the Tool tab shows
"Whole garment selected" and a **Whole Pieces** button above "AC9 Garment".

**Whole Pieces** is a tool with Blender's move and rotate gizmo. With such a tool, Blender also runs its own select
on a click off the gizmo. AC9 notices it and puts back what Whole Pieces picked.

## Where the gizmo shows

When you pick a piece with **Whole Pieces**, the move and rotate gizmo shows on a placed piece's object, and on the
handle of a dressed piece. It does not show on the flat pattern (it is locked). It shows only where the view's Object
Gizmos are on. They are off by default in Blender; the **Work Screen** turns them on in its two views, move or
rotate as the tool's **Gizmo** switch says (W switches it).

## When the gizmo is not where you expect

Blender's move and rotate gizmo sits at the average of the origins of the objects that are **selected and shown in
that view** (with the default Median Point pivot).

AC9 marks the flat pattern object of every picked piece. A flat pattern object's origin is not on its piece: it is
one point shared by every piece. So **when one 3D view shows both the flat pattern and the placed shapes**, the gizmo
shows up in the empty space between the placed piece and the pattern.

To avoid it, work in the **Work Screen** (**Setup**). Its 3D view does not show the flat pattern, and its pattern view
does not show the placed shapes or the dressed garment. Objects a view does not show are not in the average, so the
gizmo sits on the piece you are looking at.

Pairs differ in one more way:

- A placed pair is one object, the original side with a Mirror modifier. Its origin is in the middle of the
  original side, so the gizmo sits on one side of the pair (not between the two).
- Turned, the mirrored side turns the other way across the mirror plane.
