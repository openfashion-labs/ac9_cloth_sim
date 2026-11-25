# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, language-independent support metadata. No scene, log or user strings."""
import json
import re

SCHEMA = 'ac9_support_v1'
MAX_BYTES = 1024
LABEL = 'Copy Diagnostics'
UNKNOWN = 'unknown'
_VERSION = re.compile(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}')


def version(value):
    if type(value) is tuple and len(value) == 3:
        if all(type(v) is int and 0 <= v <= 9999 for v in value):
            return '.'.join(str(v) for v in value)
    if type(value) is str and len(value) <= 14 and _VERSION.fullmatch(value):
        return value
    return UNKNOWN


def report(addon_version, blender_version, platform):
    """Only validated values reach the fixed four-field report."""
    os_family = {'win32': 'Windows', 'linux': 'Linux', 'darwin': 'macOS'}.get(
        platform if type(platform) is str else '', UNKNOWN)
    payload = json.dumps({'schema': SCHEMA, 'addon_version': version(addon_version),
                          'blender_version': version(blender_version), 'os': os_family},
                         ensure_ascii=True, indent=2)
    if len(payload.encode('utf-8')) > MAX_BYTES:
        raise ValueError('support metadata size')
    return payload
