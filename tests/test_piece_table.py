"""The piece table (core/piece_table.py): which pieces are selected and which one leads, the same whichever kind of
object shows the piece (flat pattern, placed object, handle) and whichever view it was picked in."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ac9_cloth_sim.core import piece_table as PT
from ac9_cloth_sim.core import spec as S

F = PT.ObjFact


def _spec(pair=True):
    pcs = [{'name': n} for n in ('Back', 'SleeveL', 'SleeveR', 'Collar')]
    return {'pieces': pcs, 'pairs': [['SleeveL', 'SleeveR']] if pair else []}


class SelectedNames(unittest.TestCase):
    def test_active_first_then_layer_order(self):
        facts = [F('pattern', ('Back',), True, False), F('pattern', ('Collar',), True, True)]
        self.assertEqual(PT.selected_names(facts), ['Collar', 'Back'])

    def test_pair_object_gives_both(self):
        facts = [F('placed', ('SleeveL', 'SleeveR'), True, True)]
        self.assertEqual(PT.selected_names(facts), ['SleeveL', 'SleeveR'])

    def test_every_kind_alike(self):
        for kind in PT.KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(PT.selected_names([F(kind, ('Back',), True, False)]), ['Back'])

    def test_no_duplicates(self):
        facts = [F('pattern', ('Back',), True, False), F('placed', ('Back',), True, False)]
        self.assertEqual(PT.selected_names(facts), ['Back'])


class LeadName(unittest.TestCase):
    def test_active_selected_wins(self):
        for kind in PT.KINDS:
            with self.subTest(kind=kind):
                facts = [F('pattern', ('Back',), True, False), F(kind, ('Collar',), True, True)]
                self.assertEqual(PT.lead_name(facts), 'Collar')

    def test_box_select_keeps_active_outside(self):
        # the active object is not among the selected (a box select): the first selected pattern piece by name
        facts = [F('pattern', ('Collar',), True, False), F('pattern', ('Back',), True, False)]
        self.assertEqual(PT.lead_name(facts), 'Back')

    def test_pattern_before_placed(self):
        facts = [F('placed', ('Back',), True, False), F('pattern', ('Collar',), True, False)]
        self.assertEqual(PT.lead_name(facts), 'Collar')

    def test_placed_alone_leads(self):
        # a placed piece's object selected alone: the Tool tab shows the piece (it showed the garment)
        self.assertEqual(PT.lead_name([F('placed', ('SleeveL', 'SleeveR'), True, False)]), 'SleeveL')

    def test_names_filter(self):
        facts = [F('pattern', ('Back',), True, True), F('pattern', ('Collar',), True, False)]
        self.assertEqual(PT.lead_name(facts, {'Collar'}), 'Collar')
        self.assertIsNone(PT.lead_name(facts, {'Front'}))

    def test_nothing(self):
        self.assertIsNone(PT.lead_name([]))


class Table(unittest.TestCase):
    def test_units_and_lead(self):
        for pair in (True, False):
            with self.subTest(pair=pair):
                t = PT.piece_table(_spec(pair), [])
                self.assertEqual(t['SleeveR']['unit'], 'SleeveL' if pair else 'SleeveR')
                self.assertEqual(t['SleeveR']['lead'], not pair)
                self.assertTrue(t['SleeveL']['lead'])

    def test_states(self):
        for state in S.PIECE_STATES:
            with self.subTest(state=state):
                sp = _spec()
                S.set_piece_state(sp['pieces'][0], state)
                self.assertEqual(PT.piece_table(sp, [])['Back']['state'], state)

    def test_selection_and_owners(self):
        facts = [F('pattern', ('Back',), True, False), F('placed', ('SleeveL', 'SleeveR'), True, True),
                 F('handle', ('Collar',), False, False)]
        t = PT.piece_table(_spec(), facts)
        self.assertTrue(t['Back']['selected'] and not t['Back']['active'])
        self.assertTrue(t['SleeveL']['active'] and not t['SleeveR']['active'])
        self.assertTrue(t['SleeveR']['selected'])
        self.assertFalse(t['Collar']['selected'])
        self.assertEqual(t['Collar']['owners'], ['handle'])

    def test_selected_units(self):
        facts = [F('placed', ('SleeveL', 'SleeveR'), True, True), F('pattern', ('Back',), True, False)]
        self.assertEqual(PT.selected_units(_spec(), facts), [('SleeveL', ['SleeveL', 'SleeveR']), ('Back', ['Back'])])

    def test_unknown_names_ignored(self):
        t = PT.piece_table(_spec(), [F('pattern', ('Gone',), True, True)])
        self.assertFalse(any(r['selected'] for r in t.values()))


if __name__ == '__main__':
    unittest.main()
