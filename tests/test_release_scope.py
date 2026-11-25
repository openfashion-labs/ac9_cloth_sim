# SPDX-License-Identifier: GPL-3.0-or-later
"""A release must fail when required distribution work or artifacts are omitted."""
import importlib.util,unittest
from pathlib import Path
S=importlib.util.spec_from_file_location('release_scope',Path(__file__).resolve().parents[1]/'tools/check_release_scope.py')
M=importlib.util.module_from_spec(S);S.loader.exec_module(M)

class ReleaseScope(unittest.TestCase):
    def test_prepared_steps_are_accepted(self):
        M.validate_workflow('\n'.join('- name: '+n for n in M.REQUIRED)+'\n  name: split-distribution-preview\n')
    def test_legacy_full_only_workflow_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Required distribution CI omitted'):M.validate_workflow('- name: Build ZIP\n  name: extension-zip\n')
    def test_commented_steps_do_not_count(self):
        with self.assertRaises(ValueError):M.validate_workflow('\n'.join('# - name: '+n for n in M.REQUIRED)+'\n# name: split-distribution-preview')
    def test_each_prepared_requirement_is_required(self):
        for omitted in (*M.REQUIRED,'split-distribution-preview'):
            lines=['- name: '+n for n in M.REQUIRED if n!=omitted]
            if omitted!='split-distribution-preview':lines.append('  name: split-distribution-preview')
            with self.subTest(omitted=omitted),self.assertRaises(ValueError):M.validate_workflow('\n'.join(lines))
    def test_full_zip_alone_does_not_pass(self):
        with self.assertRaisesRegex(ValueError,'full ZIP alone'):M.validate_packages({'packages':{'full':{}}},'0.4.1')
    def test_pending_publication_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Publication incomplete'):M.require_publish_ready({'publication':{'ready':False,'remaining':['connection']}})
    def test_flag_alone_does_not_override_remaining_work(self):
        with self.assertRaises(ValueError):M.require_publish_ready({'publication':{'ready':True,'remaining':['connection']}})
    def test_explicit_completed_publication_is_accepted(self):
        M.require_publish_ready({'publication':{'ready':True,'remaining':[]}})

if __name__=='__main__':unittest.main()
