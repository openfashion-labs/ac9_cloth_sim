# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from ac9_cloth_sim.core.unit_expression import length


class Lengths(unittest.TestCase):
    def test_blender_units_and_arithmetic(self):
        for text,value in [('1cm+2mm',.012),('2*3cm',.06),('(2+3)*1cm',.05),('1m2cm',1.02),
                           ('1/2cm',.005),('1e3mm',1),('2cm/2',.01),('1cm+2',.03),("5'2\"",1.5748)]:
            self.assertAlmostEqual(length(text),value,msg=text)
        self.assertAlmostEqual(length('2',.01),.02)
        self.assertAlmostEqual(length('2',.0254),.0508)

    def test_unicode_and_locale_independent_units(self):
        self.assertAlmostEqual(length('１ｃｍ＋２ｍｍ'),.012)
        self.assertAlmostEqual(length('3 µm'),.000003)
        self.assertAlmostEqual(length('2 IN'),.0508)

    def test_mixed_system_basis_and_compound_negative_match_native_fields(self):
        for text,value in [('1mm+1cm+2',.031),('1cm+1mm+2',.031),('-1m20cm',-1.2),
                           ('-1m20cm+5mm',-1.195),('2*1m20cm',2.2),('1m20cm/2',1.1),
                           ('1ft1in+2',.3322),('1in1ft+2',.3322)]:
            self.assertAlmostEqual(length(text),value,msg=text)
        self.assertAlmostEqual(length('1ft1in+2',.0254,'IMPERIAL'),.9398)


    def test_only_the_numeric_grammar_is_accepted(self):
        for text in ['sqrt(9) cm','f(1)','1e309 cm','1e308*100 km','nan','inf','True','1**1000','1_000mm','1/0mm','1,2mm']:
            with self.assertRaises((ValueError,SyntaxError,ZeroDivisionError),msg=text):length(text)


if __name__=='__main__':unittest.main()
