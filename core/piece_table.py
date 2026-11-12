"""The pieces as one table (no bpy): which pieces are selected, which one leads, and the pair (unit) each belongs to.

Every tool that asks "which pieces are selected" reads this table instead of walking Blender objects itself. The facts
come from the scene (selection.facts): one ObjFact per AC9 object that shows a piece (a flat pattern piece, a placed
piece's object -- one object may show both pieces of a pair --, a handle picked on the dressed garment), with its
Blender selection flag read over the whole view layer, not through one viewport's visible objects (a 2D view and a
3D view show different objects: a pattern piece picked in 2D was missing from the 3D view's selection)."""
from collections import namedtuple
import json

from . import spec as S

KINDS = ('pattern', 'placed', 'handle')
HANDLE_NAMES = 'ac9_move_pieces'          # legacy comma-separated value, retained for older files/readers
HANDLE_IDS = 'ac9_move_piece_ids'        # JSON array: a single ID may itself contain a comma


def handle_names(props):
    """Read a handle's piece collection without changing saved data.

    New handles carry an explicit JSON array. Legacy handles keep their old
    delimiter interpretation, except that the separately saved first ID is
    known exactly. No automatic migration/rename of ambiguous old suffixes.
    A corrupt explicit array is not reinterpreted as the legacy string.
    """
    if HANDLE_IDS in props:
        try:
            names = json.loads(props[HANDLE_IDS])
        except (TypeError, ValueError):
            return ()
        if not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names):
            return ()
        return tuple(dict.fromkeys(names))
    raw = str(props.get(HANDLE_NAMES, ''))
    first = props.get('ac9_piece')
    if first and raw == first:
        return (first,)
    if first and raw.startswith(first + ','):
        return tuple(dict.fromkeys([first] + [n for n in raw[len(first)+1:].split(',') if n]))
    return tuple(dict.fromkeys(n for n in raw.split(',') if n))


def encode_handle_names(names):
    """The lossless saved representation; does not change the IDs or their order."""
    return json.dumps(list(dict.fromkeys(names)), ensure_ascii=False)

# kind: one of KINDS; names: the pieces the object shows, its own first (a pair's placed object: (piece, twin));
# selected / active: Blender's flags for the view layer
ObjFact = namedtuple('ObjFact', 'kind names selected active')


def selected_names(facts):
    """Piece names of the selected objects, the active object's first, then in the view layer's order (as the old
    selected_pieces read context.selected_objects); a pair's placed object gives both of its pieces."""
    sel = [f for f in facts if f.selected]
    sel.sort(key=lambda f: not f.active)            # stable: only the active object moves to the front
    out = []
    for f in sel:
        for n in f.names:
            if n and n not in out:
                out.append(n)
    return out


def lead_name(facts, names=None):
    """The piece the selection is shown and edited by (the Tool tab's AC9 Piece): the active object's own piece when
    it is selected, else the first selected pattern piece by name, else the first selected placed piece or handle by
    name (a box select keeps the active object outside it). names: only pieces in it."""
    ok = lambda n: n and (names is None or n in names)
    for f in facts:
        if f.active and f.selected and ok(f.names[0] if f.names else None):
            return f.names[0]
    for kinds in (('pattern',), ('placed', 'handle')):
        cand = sorted(f.names[0] for f in facts if f.selected and f.kind in kinds and f.names and ok(f.names[0]))
        if cand:
            return cand[0]
    return None


def units(spec):
    """{piece: unit id}: a pair placed as mirror images is one unit (its first piece's name), every other piece its
    own. The unit's lead is its id: the piece whose placement the partner mirrors (placement.lead_only keeps it)."""
    out = {pc['name']: pc['name'] for pc in spec.get('pieces', [])}
    for a, b in S.placement_pairs(spec):
        out[a] = out[b] = a
    return out


def piece_table(spec, facts):
    """{piece: row}, one row per piece of the spec: unit, lead, state, selected, active, owners (the kinds of the
    objects showing it). Pure: the same spec and facts give the same table."""
    unit = units(spec)
    rows = {}
    for pc in spec.get('pieces', []):
        n = pc['name']
        rows[n] = {'name': n, 'unit': unit[n], 'lead': unit[n] == n, 'state': S.piece_state(pc),
                   'selected': False, 'active': False, 'owners': []}
    for f in facts:
        for i, n in enumerate(f.names):
            r = rows.get(n)
            if r is None:
                continue
            if f.kind not in r['owners']:
                r['owners'].append(f.kind)
            if f.selected:
                r['selected'] = True
                if f.active and i == 0:
                    r['active'] = True
    return rows


def selected_units(spec, facts):
    """The units with a selected piece, in selection order: [(unit id, [its selected pieces])]."""
    unit = units(spec)
    out = {}
    for n in selected_names(facts):
        if n in unit:
            out.setdefault(unit[n], []).append(n)
    return list(out.items())
