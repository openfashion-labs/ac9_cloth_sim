# SPDX-License-Identifier: GPL-3.0-or-later
"""The file names of a result folder, in one place (no Blender import, so core modules read them too).
result_reference re-exports them; every other module reads and writes a result folder through result_reference or
these names (tests/test_result_contract.py counts the literal names elsewhere)."""
JOB = 'job.json'
INDEX = 'scene.idx.npz'
PATTERN = 'pattern.npz'
AVATAR = 'avatar.npz'
SPEC = 'ac9_spec.json'
FINAL = 'final.npy'
FRAMES = 'frames.npy'
