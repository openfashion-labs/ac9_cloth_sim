# AC9 Cloth Sim

[日本語](README.md)

A Blender add-on for importing and editing garment patterns, sewing them together and dressing an avatar.
Edit a pattern, dress the avatar again, and check how the cloth settles with a physics simulation.

**[User guide (English)](https://openfashion-labs.github.io/ac9_cloth_sim/) · [説明書（日本語）](https://openfashion-labs.github.io/ac9_cloth_sim/ja/)**

## Requirements and development status

Windows 64-bit / Blender 5.2 / NVIDIA GeForce RTX 20, 30, 40 or 50 series.
See the user guide for detailed requirements and the scope of GPU verification.

This is alpha software. Features and controls may change. Saved .blend files may not open in a later version.

## Installation

**Install through Extensions (0.4.1 — in preparation)**

The Extensions installation page is not available yet.
After publication, this section will link to that page. Drag its installation button into Blender to install the add-on.
Add Newton, needed for simulation, from the add-on preferences.
When the compatible Newton version stays the same, add-on updates reuse it without downloading the large components again.

For candidate inspection, see the [candidate repository guide](tools/CANDIDATE_REPOSITORY.md). See [0.4.1 preparation status](RELEASE_STATUS.md) for remaining work.

<details>
<summary>Current public version / manual ZIP installation</summary>

Currently, choose `ac9_cloth_sim-0.4.0.zip` from the Assets of the [0.4.0 Release](https://github.com/openfashion-labs/ac9_cloth_sim/releases/tag/v0.4.0) and use it without extracting it.
An all-in-one ZIP (`ac9_cloth_sim-0.4.1-full.zip`) is also planned for 0.4.1. It is not published yet.
See the user guide for installation steps.

</details>

## Help

See the user guide for usage and troubleshooting. Report bugs through [Issues](https://github.com/openfashion-labs/ac9_cloth_sim/issues).

## License

The add-on itself is free, including commercial use, under [GPL-3.0-or-later](LICENSE). Copyright (C) 2026 OpenFashion.
Modified Newton components use [Apache-2.0](solver/newton_patch/LICENSE) ([NOTICE](solver/newton_patch/NOTICE)).
Third-party components retain their respective terms; see [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
The bundled avatar is [CC0 1.0](avatar_LICENSE.txt).
