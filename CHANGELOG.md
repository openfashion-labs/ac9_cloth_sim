# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version number is the one in `blender_manifest.toml`; each release is tagged
`vX.Y.Z` and published on the GitHub Releases page.

## [Unreleased]

## [0.1.1] - 2026-11-12

Still an alpha: a .blend saved with this version may not open in an earlier one (pieces laid flat, pairs with
a front / back mirror plane).

The ZIP no longer bundles SciPy: the simulation's Python packages are newton 1.6.0 and warp-lang 1.17.0 (Windows
x64), and the ZIP is about 37 MB smaller.

### Added

- **Strengthen** in AC9 Piece: multiplies the stretch, shear and bending stiffness of the selected pieces by a
  factor (1-100, 5 by default). It stays on the result, can be switched or changed during Live Sim, and is
  shown with a purple tint. It is separate from Interlining.
- Bags and other items without a body: **Place** off the guides lays pieces flat on the face under the cursor,
  or on the ground (Z = 0); Phase Sim and Live Sim run without a body when the pieces are laid flat.
- **Floor (Z = 0)** in Run: a floor the cloth lands on, nothing added to the scene.
- Fabrics **Leather** and **Synthetic Leather** (measured values) and **Canvas** (bending estimated).
- **Units** in Setup: one length unit (mm, cm, m, inch) for every shown or typed length, including the
  measurements. Length fields accept a unit ("5cm"). Particle Distance stays in mm.
- Pairs can mirror **front to back** as well as left to right: **Mirror Copy**'s F9 has **Mirror Plane**, and it
  can be changed later. A front / back pair laid flat mirrors about its own plane.
- AC9 Piece has a **Pair** box (partner, linked or not, mirror plane, and every pair button), a placed piece's
  location and rotation (folded), and a flat piece's **Orientation** on the face it lies on.
- Pattern editing: **Move by Number**, a dotted guide with Horizontal / Vertical / 45 deg while Shift is held,
  internal lines drawn with any tool, notches snapping to an edge's middle and to the partner's notch.

### Changed

- The solver no longer uses SciPy: point queries, mesh diagnostics and the local contact repair use NumPy and
  Warp CPU helpers instead.
- Pattern editing: the right-click menu holds every action, and keys are shortcuts to it. S is Scale only;
  Ctrl turns snapping off and Shift holds the angle everywhere. Parallel Line and Slash and Spread ask for their
  numbers first.
- **Part to Seam** and **Band on Edge** ask for their targets: press the button, then click the targets
  (Shift adds or removes, A all, Enter applies, Esc cancels). The choice lasts for this operation only.
- **Back to Flat** lays pieces flat where they are (their place and turn kept) instead of taking them out of 3D.
- **Mirror 3D to Pair** moved to AC9 Piece and is pressable only when both sides are on the simulated garment.
- Messages and seam lengths are shown in the scene's unit.
- Measure takes everything again when the body is reshaped, scaled or turned; tapes laid by hand follow the skin.

### Fixed

- Moving or turning a whole piece in the pattern kept its placed shape's points where the piece had been.
- With Place, clicking a selected placed piece without moving the mouse placed it again (a stood-up piece could
  slide away); it now only selects it.
- A turn on the pattern no longer turns the placed piece.
- Seams of a front / back Mirror Copy landed on the original's pieces.
- Deleting a piece left its placed object behind (a later Mirror Copy under the same name took it over).
- The Redo of Add Dart failed; Ctrl+Z after a right-click menu edit left the pattern edited.
- Live Sim / Phase Sim stopped before starting when every piece of the last result was Off and the pieces On
  had never been simulated.
- Folded layers and seam fitting: folds marked No Side, and sewn targets across overlapping folded layers.
- With the Japanese interface, some tooltips (such as Duplicate), the piece states' and seam path's items and
  several messages were shown in English.
- The solver rejects non-finite values before they reach the cloth, and handles a piece without spring
  constraints.

## [0.1.0] - 2026-11-05

First public release. Features are missing, the screens and steps will change, and a .blend saved with
this version may not open in the next one. Windows and an NVIDIA GPU, Blender 5.2.

The ZIP bundles the simulation's Python packages unmodified from PyPI: newton 1.6.0, warp-lang 1.17.0 and
scipy 1.18.1 (Windows x64). Their licenses are in `THIRD_PARTY_NOTICES.txt` and `licenses/`.

[Unreleased]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/openfashion-labs/ac9_cloth_sim/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/openfashion-labs/ac9_cloth_sim/releases/tag/v0.1.0
