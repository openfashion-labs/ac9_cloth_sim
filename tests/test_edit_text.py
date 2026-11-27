# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from ac9_cloth_sim.core.edit_text import Text, number


class Editing(unittest.TestCase):
    def test_replace_selection_move_cursor_and_undo_without_touching_the_document(self):
        t=Text('10 mm');t.replace('2 cm');t.edge(False);t.move(1);t.replace('0')
        self.assertEqual(t.text,'20 cm')
        t.history();self.assertEqual(t.text,'2 cm')
        t.history(redo=True);self.assertEqual(t.text,'20 cm')

    def test_shift_selection_and_forward_backward_delete(self):
        t=Text('12.5 mm');t.anchor=t.cursor=2;t.move(1,shift=True);t.move(1,shift=True)
        self.assertEqual(t.selected,'.5');t.replace('');self.assertEqual(t.text,'12 mm')
        t.edge(False);t.remove(back=False);self.assertEqual(t.text,'2 mm')
        t.edge(True);t.remove();self.assertEqual(t.text,'2 m')

    def test_word_navigation_and_unicode_paste(self):
        t=Text('10 cm + 2 mm');t.anchor=t.cursor=len(t.text)
        t.move(-1,word=True,shift=True);self.assertEqual(t.selected,'mm')
        t.replace('µm\r\n');self.assertEqual(t.text,'10 cm + 2 µm  ')

    def test_paste_limit_is_atomic(self):
        t=Text('2');before=t.snapshot()
        with self.assertRaises(ValueError):t.replace('1'*513)
        self.assertEqual(t.snapshot(),before)

    def test_dimensionless_fields_accept_arithmetic_but_never_code(self):
        self.assertEqual(number('(4+2)/3'),2)
        self.assertEqual(number('-2*3+7'),1)
        for s in ["__import__('os')",'x','9**9','1/0','1e999']:
            with self.assertRaises((ValueError,ZeroDivisionError)):number(s)


if __name__=='__main__':unittest.main()
