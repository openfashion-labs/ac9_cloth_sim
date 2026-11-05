# SPDX-License-Identifier: GPL-3.0-or-later
"""The button library (no bpy): each style a set of values for one shape maker (button_view.py's Geometry Nodes group
AC9_ButtonShape), not a mesh of its own -- so a later Button Creator edits the same values (user 2026-10-15: the
section, the number of holes, the thickness chosen by the user).

A style also keeps the notes every asset carries (DECISIONS cloth-sim-assets-2026-10-15): name, kind, author, url,
license; the url as the asset's custom property ac9_url when it becomes a library asset. A style with "mesh" set is
the user's own mesh (an object's name): drawn as it is, the values unused.

Sizes in mm. The shape's origin is the middle of its back, +z out of the cloth.
"""

SHAPE_KEYS = ('diameter', 'thickness', 'holes', 'hole_d', 'hole_spacing', 'rim', 'dome', 'shank')

STYLES = {
    'round2': {'name': "Round, 2 holes", 'kind': 'button', 'diameter': 11.5, 'thickness': 1.8, 'holes': 2,
               'hole_d': 1.6, 'hole_spacing': 3.2, 'rim': 1.2, 'dome': 0.0, 'shank': False},
    'round4': {'name': "Round, 4 holes", 'kind': 'button', 'diameter': 11.5, 'thickness': 1.8, 'holes': 4,
               'hole_d': 1.5, 'hole_spacing': 3.6, 'rim': 1.2, 'dome': 0.0, 'shank': False},
    'shank': {'name': "Shank", 'kind': 'button', 'diameter': 15.0, 'thickness': 1.0, 'holes': 0, 'hole_d': 0.0,
              'hole_spacing': 0.0, 'rim': 0.0, 'dome': 3.0, 'shank': True},
    'snap': {'name': "Snap", 'kind': 'button', 'diameter': 9.0, 'thickness': 1.2, 'holes': 0, 'hole_d': 0.0,
             'hole_spacing': 0.0, 'rim': 0.9, 'dome': 0.8, 'shank': False},
}
NOTES = ('author', 'url', 'license')
DEFAULT = 'round4'

# the buttonhole as drawn on the cloth (a slit): its length = the button's diameter + HOLE_EXTRA, along the piece's
# layout y (a shirt's placket; a jacket's runs across, set per button later)
HOLE_EXTRA = 2.0
HOLE_W = 1.6


def style(sid, user_styles=None):
    """The style `sid` (a copy): the spec's own styles (spec "button_styles", a user's mesh or edited values) first,
    then the library; an unknown id gives the default."""
    for s in user_styles or []:
        if s.get('id') == sid:
            out = dict(STYLES.get(s.get('base', DEFAULT), STYLES[DEFAULT]))
            out.update(s)
            return out
    out = dict(STYLES.get(sid) or STYLES[DEFAULT])
    out['id'] = sid if sid in STYLES else DEFAULT
    for k in NOTES:
        out.setdefault(k, '')
    return out


def ids(user_styles=None):
    """Every style id: the library's, then the spec's own."""
    return list(STYLES) + [s['id'] for s in user_styles or [] if s.get('id') and s['id'] not in STYLES]
