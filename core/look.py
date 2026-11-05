"""The look of a piece: the render material it is drawn with, apart from its fabric (the physics). User 2026-10-20 /
10-21: the look is the piece's, as the fabric is; the garment has a default look as it has a default fabric; the
dressed garment, the placed pieces and the flat pattern all show it. No Blender here (look_apply.py puts it on).

spec keys (optional, spec version unchanged: absent = no look):
    pieces[].look:      {"material": name, "uid": str}   its own; absent = the garment's default
    spec["default_look"]: {"material": name, "uid": str}  the garment's default; absent = none (an empty slot)

A material is named by the Blender data name and a uid kept on the material (custom property ac9_look_uid): the name
follows a rename through the uid, a duplicated material (Shift+D, append: the uid copied) falls back to the name. A
tool outside Blender (MCP) cannot resolve it: the look is Blender's only (Fable review 2026-10-20 §2)."""

from . import fabric as FA
from . import spec as S


def piece_look(spec, name):
    """The look set on piece `name` (its own, else its twin's), None = it follows the default."""
    pcs = S.piece_index(spec)
    for n in (name, FA._twin_map(spec).get(name)):
        lk = pcs.get(n, {}).get('look') if n else None
        if lk and lk.get('material'):
            return lk
    return None


def default_look(spec):
    lk = spec.get('default_look')
    return lk if lk and lk.get('material') else None


def resolved(spec, name):
    """The look piece `name` is drawn with: its own or its twin's, else the default, else None."""
    return piece_look(spec, name) or default_look(spec)


def any_look(spec):
    """True when a piece or the garment has a look (the material slots are then the add-on's)."""
    return default_look(spec) is not None or any(pc.get('look') for pc in spec.get('pieces', []))


def set_pieces(spec, names, look):
    """Set `look` ({"material", "uid"}, None = follow the default) on the pieces `names` and their twins, in place.
    Returns the names set."""
    tw = FA._twin_map(spec)
    both = set(names) | {tw[n] for n in names if n in tw}
    for pc in spec['pieces']:
        if pc['name'] not in both:
            continue
        if look is None:
            pc.pop('look', None)
        else:
            pc['look'] = dict(look)
    return sorted(both)


def set_default(spec, look):
    """The garment's default look (None = none), in place."""
    if look is None:
        spec.pop('default_look', None)
    else:
        spec['default_look'] = dict(look)
