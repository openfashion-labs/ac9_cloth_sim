# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory ownership of unlinked operation snapshots, never scene-generated objects."""
_owners = {}


def claim(data, owner):
    uid = int(data.session_uid)
    if uid in _owners and _owners[uid] is not owner:
        raise ValueError('A private snapshot already belongs to another operation')
    _owners[uid] = owner
    return uid


def contains(data):
    try:
        return int(data.session_uid) in _owners
    except (AttributeError, ReferenceError, TypeError):
        return False


def release(uid, owner):
    if _owners.get(uid) is not owner:
        return False
    del _owners[uid]
    return True
