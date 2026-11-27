# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate the tool table, or fail when its checked-in rendering has drifted."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tool_policy', ROOT/'core/tool_policy.py')
policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = ROOT/'docs/tool_behavior.md'
    expected = policy.reference()
    if args.check:
        if not target.exists() or target.read_text(encoding='utf-8') != expected:
            raise SystemExit('Tool table is stale. Run tests/generate_tool_reference.py')
    else:
        target.write_text(expected, encoding='utf-8')


if __name__ == '__main__': main()
