---
title: Glossary
sidebar_position: 8
---

# Glossary

These are the terms used in this guide and on screen. Each term is listed under its English UI name, with the Japanese UI name in parentheses where one exists (you can switch Blender to Japanese).

## Patterns and seams

| Term | Meaning |
|---|---|
| Pattern data (型紙のデータ) | The information for one garment (pattern shapes, seams, placement, folds, roles, and fabrics). It is stored in the Blender text `AC9_ClothSim_Spec_<garment name>`. The pattern on screen is a display built from this. |
| **Garment** (服) | One piece of pattern data. You can have any number of garments in one scene, and you switch between them with the **Garment** field. |
| **Pieces** (ピース) | One sheet of the pattern. It corresponds to a part in CAD. |
| Outline, corner, edge (外周・角・辺) | The outline of a piece (the finished line), its corners, and the lines between corners. |
| **Internal Line** (内部線) | A line inside a piece. It is used for fold lines, dart legs, pocket positions, and so on. It can also be a closed shape. |
| **Seams** (縫い目) | A specification that sews two sections (side A and side B) together. A and B start from the same end. |
| **Notches** (合印) | Marks (notches) for the positions to be sewn together. |
| Dart (ダーツ) | A triangular cut running from the outline toward the inside. The legs are sewn together. |
| **Slash and Spread** (切り開き) | An operation that cuts a piece open along an internal line and spreads it (flare, gathers). |
| **Pair** (ペア) | Two pieces that are left-right symmetric. When you fix one, the other is fixed in a mirrored shape. |
| **Fold** (折り) | Making the cloth into a folded shape at an internal line. You set mountain or valley fold and the angle. |
| **Piece Roles** (ピースの役割) | Roles such as body, collar, and sleeve. They are used for the sewing order. |

## Placing and dressing

| Term | Meaning |
|---|---|
| **Body** (体) | The avatar body mesh the garment is dressed on. |
| Guide (**Detect Guides**, ガイド検出) | Cylinders fitted over the torso, neck, arms, and legs of the body. They are the reference for placing pieces. |
| Flat pattern (平らな型紙) | The pieces laid out on the floor. The shape right after loading or drawing. |
| Placed shape (**Place**, 配置) | Pieces placed on the guides. The simulation starts from this shape. |
| Dressed garment (結果の「**AC9 Garment**」, AC9 服) | The garment after dressing (the object `AC9_Garment_<garment name>`). It appears in the result of **AC9 Garment**. |
| Simulation (シミュレーション) | The physics calculation of cloth motion. It is computed with NVIDIA's Newton. |
| Phase (段) | One section within **Live Sim**. It goes through the phases in order, such as a phase that closes the seams and a phase that applies gravity to drape the cloth. |
| Frame (コマ) | One image of the progress. You can play them back in the Timeline. |
| Progress (cache) (経過、**Delete Cache**) | The record of the simulation's frames. It is stored in a folder next to the saved .blend. |
| Piece state (ピースの状態) | **Active**, **Freeze**, **Off Pattern**, **Off All**. They decide whether a piece moves, whether it collides with other cloth, and whether its seams are used. |
| Obstacle (**Collision**, 障害物) | Something the cloth collides with but that doesn't move. Other garments that have finished dressing automatically become obstacles. |

## Fabric

| Term | Meaning |
|---|---|
| **Fabrics** (生地) | The physical properties of the cloth, such as stiffness and weight. You choose one for each piece. |
| **Interlining** (芯地) | Interfacing attached to a piece. It makes that piece stiffer. |
| **Elastic** (ゴム) | The rate at which a piece or internal line is shrunk. |
| **Particle Distance (mm)** (粒子間隔) | The spacing of points when the cloth is divided into fine triangles. The smaller it is, the finer the mesh and the heavier the calculation. |

## Blender terms

| Term | Meaning |
|---|---|
| Object Mode / Edit Mode (オブジェクトモード／編集モード) | The mode that handles things as a whole, and the mode that fixes vertices, edges, and faces. AC9 Cloth Sim is used in Object Mode. |
| Active (アクティブ) | The item you selected last. The Tool tab shows the settings of the active item. |
| Sidebar (サイドバー) | The panel at the right edge of the 3D Viewport (**N** key). |
| Tool tab (Tool タブ) | One of the tabs of the Properties editor at the lower right of the screen. It shows the run buttons and the settings of the selected item. |
| Workspace (ワークスペース) | How the screen is arranged. **Work Screen** of AC9 Cloth Sim is one of them. |
| Overlays (オーバーレイ) | Lines and marks drawn over the 3D Viewport. You toggle them with the button at the upper right. |
