# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental panel values, explicit research settings and truthful runtime records."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import bpy
import numpy as np
from ac9_cloth_sim import properties, dress
from ac9_cloth_sim.core import presets as P, watch_record as W


class WatchRecord(unittest.TestCase):
    def test_hidden_old_checkbox_is_ignored_without_changing_saved_value(self):
        panel=NS(watch=True,watch_panel_enabled=False)
        st=P.resolve('',panel,environ={})
        self.assertFalse(st['watch']);self.assertTrue(panel.watch);self.assertTrue(st['guard'])
        panel.watch_panel_enabled=True
        self.assertTrue(P.resolve('',panel,environ={})['watch'])

    def test_explicit_settings_keep_precedence_and_guard(self):
        panel=NS(watch=False,watch_panel_enabled=False)
        st=P.resolve('test_measure',panel,environ={})
        self.assertTrue(st['watch']);self.assertTrue(st['guard'])
        panel.watch=True;panel.watch_panel_enabled=True
        self.assertFalse(P.resolve('{"watch":false}',panel,environ={})['watch'])
        st=P.resolve('{"cfg_override":{"s2":{"watch":{"drift_mm":0.4}}}}',panel,environ={})
        self.assertTrue(W.configured(st))
        cfg={};P._override(cfg,1,st['cfg_override']);self.assertEqual(cfg['watch'],{'drift_mm':.4})

    def test_gui_gate_and_explicit_headless_use(self):
        with patch.object(dress,'experimental',return_value=False):
            self.assertFalse(properties._watch_panel_enabled(None,background=False))
            self.assertTrue(properties._watch_panel_enabled(None,background=True))
        with patch.object(dress,'experimental',return_value=True):
            self.assertTrue(properties._watch_panel_enabled(None,background=False))

    def test_effective_override_display_including_deletion_and_stage_settings(self):
        panel=NS(watch=False,watch_panel_enabled=True)
        state=W.panel_state('test_measure',panel)
        self.assertEqual(state['mode'],'ON');self.assertFalse(state['editable'])
        state=W.panel_state('{"base":"test_measure","cfg_override":{"watch":null}}',panel)
        self.assertEqual(state['mode'],'OFF');self.assertFalse(state['editable'])
        state=W.panel_state('{"cfg_override":{"s2":{"watch":true}}}',panel)
        self.assertEqual(state['mode'],'STAGED')
        self.assertFalse(W.configured(P.resolve('{"base":"test_measure","cfg_override":{"watch":null}}',panel,environ={})))

    def test_invalid_saved_preset_has_a_nonthrowing_display_state(self):
        for value in ('missing_old_preset','{'):
            self.assertEqual(W.panel_state(value,NS(watch=True))['mode'],'INVALID')

    def summary(self,watch,records=None):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);(d/'config.json').write_text(json.dumps({'watch':watch}),encoding='utf-8')
            if records is not None:(d/'anomaly.json').write_text(json.dumps(records),encoding='utf-8')
            return W.summarize([d])

    def test_off_and_on_without_candidates_are_distinct(self):
        off=self.summary(False);on=self.summary({'tug':{}})
        self.assertEqual(W.status(off)[0],'Run anomaly recording: off')
        self.assertEqual(W.status(on)[0],'Run anomaly recording: on, no candidates recorded')

    def test_watch_candidates_are_not_guard_records(self):
        records=[{'kind':'crossing','frame':10},{'kind':'unfolded','frame':20},
                 {'drift_mm_per_frame':.9,'frame':30,'action':'record'},
                 {'kind':'tug of war','frame':40,'action':'record'},
                 {'kind':'seam turned over','frame':50,'action':'record'}]
        result=self.summary({'tug':{}},records)
        self.assertEqual(result['count'],3);self.assertEqual(result['first']['frame'],30)
        self.assertEqual(result['all_count'],5);self.assertEqual(result['first_any']['kind'],'crossing')
        self.assertEqual(W.status(result)[1],(3,))
        self.assertNotIn('red',W.status(result)[0]);self.assertNotIn('stop',W.status(result)[0])

    def test_guard_only_does_not_claim_watch_ran(self):
        result=self.summary(False,[{'kind':'crossing','frame':10}])
        self.assertEqual(result['count'],0);self.assertFalse(result['enabled'])

    def test_missing_or_corrupt_records_are_unknown(self):
        self.assertIn('unavailable',W.status(W.summarize([]))[0])
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);(d/'config.json').write_text('{',encoding='utf-8')
            result=W.summarize([d]);self.assertTrue(result['errors'])
            self.assertIn('unavailable',W.status(result)[0])

    def test_history_uses_classified_watch_record_without_reading_files_again(self):
        mesh=bpy.data.meshes.new('WatchHistory');mesh.from_pydata([(0,0,0),(.001,0,0)],[],[])
        ob=bpy.data.objects.new('WatchHistory',mesh);bpy.context.scene.collection.objects.link(ob)
        try:
            with tempfile.TemporaryDirectory() as temp:
                d=Path(temp);(d/'config.json').write_text(json.dumps({'watch':{'tug':{}}}),encoding='utf-8')
                items=[{'kind':'crossing','frame':10},{'kind':'tug of war','frame':20,
                       'top_stitches_full_verts':[[0,1,1.,2.]]}]
                (d/'anomaly.json').write_text(json.dumps(items),encoding='utf-8')
                record=W.summarize([d])
                with patch.object(W,'summarize',side_effect=AssertionError('second read')):
                    al=dress.mark_anomaly(ob,str(d),np.array([[0.,0.,0.],[.001,0.,0.]]),record=record)
                self.assertEqual(al['frame'],20);self.assertTrue(al['painted'])
                (d/'anomaly.json').write_text('{',encoding='utf-8')
                record=W.summarize([d]);self.assertTrue(record['errors'])
                self.assertIsNone(dress.mark_anomaly(ob,str(d),np.zeros((2,3)),record=record))
        finally:
            bpy.data.objects.remove(ob,do_unlink=True);bpy.data.meshes.remove(mesh)


if __name__=='__main__':unittest.main()
