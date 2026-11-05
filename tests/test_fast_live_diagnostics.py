"""Long Live runs keep bounded evidence and preserve complete numeric totals."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solver.fast_live.diagnostics import Diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_long_run_is_bounded_and_totals_are_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            diagnostics = Diagnostics(folder)
            for index in range(1000):
                diagnostics.record_query({'frame': index})
                diagnostics.record_native({'frame': index, 'native_post_safe': True})
                diagnostics.record_correction({'frame': index, 'alpha': 1.,
                    'energy_before_J': 1., 'energy_after_J': .5,
                    'linear_solve': {'true_within_tolerance': True, 'iterations': index},
                    'near_negative_count': 0, 'near_ambiguous_count': 0})
            summary = diagnostics.flush()
            self.assertEqual(summary['corrections'], 1000)
            self.assertEqual(summary['native_substeps'], 1000)
            self.assertEqual(summary['queries'], 1000)
            self.assertEqual(summary['cg_iterations_max'], 999)
            for name in ('correction', 'warmstarts', 'query_reach'):
                rows = json.loads((Path(folder) / (name + '.json')).read_text())
                self.assertEqual(len(rows), Diagnostics.KEEP)
                self.assertEqual((rows[0]['frame'], rows[-1]['frame']), (744, 999))

    def test_diagnostic_write_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            diagnostics = Diagnostics(Path(folder) / 'missing')
            self.assertIn('write_error', diagnostics.flush())


if __name__ == '__main__':
    unittest.main()
