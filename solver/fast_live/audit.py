# SPDX-License-Identifier: GPL-3.0-or-later
"""Research audits of the Fast Live correction (signatures, invariants, diagnostic re-evaluations).

Off by default (Accurate stage B, docs/design/DESIGN_S3_accurate_rebuild_2026-11-03.md): they cost about 30 ms per
correction and never change the step. AC9_TEST_FAST_AUDIT=1 turns them all back on for a test run.
"""
import os
ON = bool(os.environ.get('AC9_TEST_FAST_AUDIT'))
