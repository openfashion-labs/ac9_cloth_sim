# SPDX-License-Identifier: GPL-3.0-or-later
"""Lengths on the screen in one unit: the scene's Length unit (Setup > Units: mm, cm, m or in). The pattern, the
measurements and the spec stay in mm; this module turns them into the shown unit and back. Everything that shows or
takes a length goes through here (user 2026-11-11: one unit everywhere, inch too). Not converted: the particle
distance (always mm), fabric values (g/m², N/m), angles, factors, and the units DXF / SVG files are written in.

Fields typed in the panels use Blender's own length fields (subtype 'DISTANCE', unit 'LENGTH': the value in meters,
shown and typed in the scene's unit, "5cm" and "2in" accepted); their getters and setters turn them into mm."""

# (mm per unit, symbol, digits on the pattern); the body's measurements show one digit fewer
_METRIC = {'MILLIMETERS': (1.0, "mm", 1), 'CENTIMETERS': (10.0, "cm", 2), 'METERS': (1000.0, "m", 4),
           'MICROMETERS': (0.001, "µm", 0), 'KILOMETERS': (1e6, "km", 6), 'ADAPTIVE': (1.0, "mm", 1)}
_IMPERIAL = {'INCHES': (25.4, "in", 3), 'FEET': (304.8, "ft", 4), 'THOU': (0.0254, "thou", 1),
             'MILES': (1609344.0, "mi", 7), 'ADAPTIVE': (25.4, "in", 3)}
_MM = _METRIC['MILLIMETERS']

# The step of a length field typed in a dialog or the Tool tab (Blender's 'step', in 1/100 of a meter). Blender
# turns a length field's step into the unit nearest it: the default step (3) drags 10 mm per pixel (1 in per pixel in
# inches), so a value jumped by centimeters. Below 0.1 the step is a millimeter: 1 mm per pixel (measured in Blender
# 5.2 with simulated drags; inches then drag 0.001 in per pixel, there is no step between that and 1 in).
LEN_STEP = 0.05

# the Setup row: (key, label, unit system, Length unit)
CHOICES = (('MM', "mm", 'METRIC', 'MILLIMETERS'), ('CM', "cm", 'METRIC', 'CENTIMETERS'),
           ('M', "m", 'METRIC', 'METERS'), ('IN', "in", 'IMPERIAL', 'INCHES'))


def _row(context):
    try:
        us = context.scene.unit_settings
        if us.system == 'METRIC':
            return _METRIC.get(us.length_unit, _MM)
        if us.system == 'IMPERIAL':
            return _IMPERIAL.get(us.length_unit, _IMPERIAL['INCHES'])
    except Exception:
        pass
    return _MM


def unit(context):
    """(mm per unit, symbol) of the scene's Length unit; no unit system shows mm."""
    r = _row(context)
    return r[0], r[1]


def num(context, mm, signed=False, body=False):
    """mm in the scene's Length unit, the number only (the boxes on the pattern). body: a body measurement, one digit
    fewer (mm 0, cm 1, in 2)."""
    f, _s, d = _row(context)
    d = max(0, d - 1) if body else d
    return f"{mm / f:{'+' if signed else ''}.{d}f}"


def fmt(context, mm, signed=False, body=False):
    """mm in the scene's Length unit with its symbol: "123.4 mm", "12.34 cm", "4.858 in". None: a dash."""
    if mm is None:
        return "—"
    return f"{num(context, mm, signed, body)} {unit(context)[1]}"


def typed_mm(context, txt):
    """A typed number in the scene's unit as mm, else None."""
    try:
        return float(txt) * unit(context)[0]
    except (TypeError, ValueError):
        return None


def current(context):
    """The Setup row's key the scene shows now (MM / CM / M / IN), else None (another unit)."""
    try:
        us = context.scene.unit_settings
    except Exception:
        return None
    return next((k for k, _l, sy, lu in CHOICES if us.system == sy and us.length_unit == lu), None)


def set_unit(context, key):
    """The scene's unit system and Length unit to the Setup row's `key`. Only how lengths are shown: sizes and the Unit
    Scale stay."""
    us = context.scene.unit_settings
    _k, _l, system, length = next(c for c in CHOICES if c[0] == key)
    us.system = system
    us.length_unit = length


def set_mm_if_default(context):
    """A new or imported pattern: the scene's Length unit to Millimeters when it is still Blender's default (Meters).
    Only how lengths are shown: sizes and the Unit Scale stay (user 2026-10-20; 5.2: a cube stays 2 m, scale 1.0)."""
    try:
        us = context.scene.unit_settings
        if us.system == 'METRIC' and us.length_unit == 'METERS' and abs(us.scale_length - 1.0) < 1e-9:
            us.length_unit = 'MILLIMETERS'
    except Exception:
        pass


# ---------------------------------------------------------------- Blender length fields over mm values

def m(mm):
    """mm -> the meters a length field holds."""
    return float(mm) / 1000.0


def mm(meters):
    """A length field's meters -> mm."""
    return float(meters) * 1000.0
