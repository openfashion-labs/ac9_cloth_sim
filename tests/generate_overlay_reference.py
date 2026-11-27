# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the display contract from the definitions used by the UI."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference(root=ROOT):
    policy = load(root/'core/overlay_policy.py', 'display_policy_reference')
    ja = load(root/'i18n_ja.py', 'display_policy_ja')
    def translate(s):
        return ja.JA_CTX.get(s, ja.JA.get(s, s))
    return policy.reference(translate)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    p.add_argument('--parent', type=Path, help='Optional parent management document; never required by the add-on')
    args = p.parse_args()
    target = args.parent or ROOT/'docs/overlay_display.md'
    expected = reference()
    if args.parent:
        start, end = '<!-- GENERATED_OVERLAY_POLICY -->', '<!-- END_GENERATED_OVERLAY_POLICY -->'
        current = target.read_text(encoding='utf-8-sig')
        before, rest = current.split(start, 1)
        _, after = rest.split(end, 1)
        expected = before + start + '\n\n' + expected + '\n' + end + after
    if args.check:
        if not target.is_file() or target.read_text(encoding='utf-8') != expected:
            raise SystemExit('Display table is stale. Run tests/generate_overlay_reference.py')
    else:
        target.write_text(expected, encoding='utf-8')


if __name__ == '__main__':
    main()
