# SPDX-License-Identifier: GPL-3.0-or-later
"""Delayed export completion must not change a different Blender job/object."""
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

from ac9_cloth_sim.core import job_workspace as W


class Object(dict):
    session_uid = 1
    modifiers = ()


class Objects(dict):
    def __iter__(self):return iter(self.values())


class Writer:
    def __init__(self):
        import threading
        self.pending=set();self.results=queue.Queue();self.requests=[];self.lock=threading.Lock();self._destinations={}
    def submit(self,source,target):self.requests.append((source,target));return True
    def submit_accepted(self,source,target,generation):return self.submit(source,target)
    def collect(self):
        out=[]
        while not self.results.empty():out.append(self.results.get_nowait())
        return out


class JobArchive(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.work=self.root/'work'/'job_unique'
        self.work.mkdir(parents=True)
        for name in W.REQUIRED:(self.work/name).write_text('{}')
        (self.work/'final.npy').write_bytes(b'real-result-bytes')
        self.obj=Object(ac9_job=str(self.work))
        self.objects=Objects(garment=self.obj)
        registered=set()
        self.context=NS(preferences=NS(addons={}),scene=NS(ac9_cloth_sim=NS(dress_status='')))
        fake=NS(data=NS(filepath=str(self.root/'saved'/'scene.blend'),objects=self.objects),context=self.context,
            app=NS(tempdir=str(self.root/'volatile'),handlers=NS(persistent=lambda fn:fn),
                timers=NS(is_registered=lambda fn:fn in registered,
                    register=lambda fn,**kw:registered.add(fn),unregister=lambda fn:registered.discard(fn))),
            path=NS(abspath=lambda p:p))
        name='_job_archive_isolated'
        pkg=ModuleType(name);pkg.__path__=[str(Path(__file__).resolve().parents[1])]
        core=ModuleType(name+'.core');core.job_workspace=W
        solver=ModuleType(name+'.solver');trace=ModuleType(name+'.solver.lifecycle_trace');trace.mark=lambda *a,**k:None
        i18n=ModuleType(name+'.i18n');i18n.tx=lambda s,*a:s.format(*a)
        # the garment's record of its folders (blend_record): what was noted and cleaned, without the rest of the add-on
        self.noted=[];self.cleaned=[]
        br=ModuleType(name+'.blend_record');br.note=lambda obj,*p:self.noted.extend(p)
        br.auto_clean=lambda obj,context=None:self.cleaned.append(obj)
        modules={name:pkg,name+'.blend_record':br,name+'.core':core,name+'.core.job_workspace':W,name+'.solver':solver,
                 name+'.solver.lifecycle_trace':trace,name+'.i18n':i18n,'bpy':fake}
        self.patcher=patch.dict(sys.modules,modules);self.patcher.start();self.addCleanup(self.patcher.stop)
        spec=importlib.util.spec_from_file_location(name+'.job_archive',Path(__file__).resolve().parents[1]/'job_archive.py')
        self.J=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.J)
        self.J._WRITER=Writer()
        self.J.QUIET_S=0.0                  # the copies wait for a quiet moment: here at once (test_quiet_* below)
        recovery=self.root/'durable'/'AC9_ClothSim_jobs'/self.work.name
        target=self.root/'saved'/'AC9_ClothSim_jobs'/self.work.name
        self.J._TARGETS[str(self.work)]=dict(recovery=str(recovery),target=str(target),blend=fake.data.filepath)
        self.lease=dict(job=str(self.work),token='bound-writer-generation',writer_released=True,gui_complete=True)
        self.fake=fake

    def finish(self):return self.J.finish(self.context,str(self.work),'garment',self.lease)
    def publish(self):
        source,target=self.J._WRITER.requests[-1]
        W.archive_finished(source,target)
        self.J._WRITER.results.put(dict(source=source,target=target,error=None))
        return source,target

    def test_durable_reference_exists_before_background_export(self):
        recovery=self.finish()
        self.assertEqual(self.obj['ac9_job'],recovery)
        self.assertEqual(self.obj['ac9_job_recovery'],recovery)
        self.assertEqual(self.obj['ac9_job_generation'],self.lease['token'])
        self.assertTrue(W.saved_generation(recovery,self.lease['token']))
        self.assertFalse(Path(self.J._WRITER.requests[-1][1]).exists())

    def test_save_accepted_result_before_release_recovers_after_seal(self):
        self.obj['ac9_job_generation']='previous-generation'
        self.obj['ac9_job_recovery']='previous-recovery'
        self.J.accept_result(self.obj,str(self.work),self.lease)
        saved=dict(self.obj)
        recovery=self.finish()
        target=self.J._WRITER.requests[-1][1]
        import shutil
        shutil.rmtree(self.work)
        self.obj.clear();self.obj.update(saved)
        self.J._WRITER=Writer();self.J.resume(self.context)
        self.assertEqual(self.obj['ac9_job'],recovery)
        self.assertEqual(self.J._WRITER.requests,[(recovery,target)])

    def test_unsealed_accepted_promise_cannot_resume(self):
        self.J.accept_result(self.obj,str(self.work),self.lease)
        self.J.resume(self.context)
        self.assertEqual(self.obj['ac9_job'],str(self.work))
        self.assertEqual(self.J._WRITER.requests,[])

    def old_writer(self):
        current=Writer()
        return NS(pending=current.pending,results=current.results,collect=current.collect)

    def test_empty_writer_from_before_f3_is_upgraded_before_submission(self):
        old=self.old_writer();self.J._WRITER=old
        recovery=self.finish();self.assertIsNot(self.J._WRITER,old)
        self.J._WRITER.requests.join();self.J.collect()
        self.assertNotEqual(self.obj['ac9_job'],recovery)
        self.assertTrue(W.saved_generation(self.obj['ac9_job'],self.lease['token']))

    def test_busy_writer_from_before_f3_drains_results_before_upgrade(self):
        old=self.old_writer();old.pending.add(('old','target'));self.J._WRITER=old
        recovery=self.finish();self.assertIs(self.J._WRITER,old)
        self.assertEqual(self.obj['ac9_job'],recovery)
        old.pending.clear();old.results.put(dict(source='old',target='target',error=None))
        self.J.collect();self.assertIsNot(self.J._WRITER,old)
        self.J._WRITER.requests.join();self.J.collect()
        self.assertNotEqual(self.obj['ac9_job'],recovery)
        self.assertEqual(self.J._DEFERRED_EXPORTS,{})

    def test_normal_notification_switches_exact_generation(self):
        self.finish();source,target=self.publish();self.J.collect()
        self.assertEqual(self.obj['ac9_job'],target)
        self.assertIn(target,self.noted);self.assertEqual(self.cleaned,[self.obj])   # the older copies are cleaned

    def test_new_job_is_not_replaced_by_old_notification(self):
        self.finish();self.obj['ac9_job']='next-job';self.publish();self.J.collect()
        self.assertEqual(self.obj['ac9_job'],'next-job')

    def test_undo_epoch_rejects_even_same_object_and_path(self):
        recovery=self.finish();self.J.invalidate();self.publish();self.J.collect()
        self.assertEqual(self.obj['ac9_job'],recovery)

    def test_reloaded_same_name_different_object_is_not_changed(self):
        recovery=self.finish();other=Object(self.obj);other.session_uid=2
        self.objects['garment']=other;self.publish();self.J.collect()
        self.assertEqual(other['ac9_job'],recovery)

    def test_save_as_does_not_change_captured_destination(self):
        self.finish();expected=self.J._WRITER.requests[-1][1]
        self.fake.data.filepath=str(self.root/'other'/'new.blend')
        self.publish();self.J.collect()
        self.assertEqual(self.obj['ac9_job'],expected)

    def test_pending_export_resumes_after_working_temp_is_deleted(self):
        import shutil
        recovery=self.finish();expected=self.J._WRITER.requests[-1][1]
        shutil.rmtree(self.work)
        self.J.invalidate();self.J._NOTICES.clear();self.J._WRITER=Writer()
        self.J.resume(self.context)
        self.assertEqual(self.J._WRITER.requests,[(recovery,expected)])
        self.publish();self.J.collect()
        self.assertEqual(self.obj['ac9_job'],expected)

    def test_foreign_generation_is_not_resumed(self):
        self.finish();self.obj['ac9_job_generation']='foreign'
        self.J._WRITER=Writer();self.J.resume(self.context)
        self.assertEqual(self.J._WRITER.requests,[])

    def test_valid_old_recovery_cannot_roll_back_a_different_current_job(self):
        recovery=self.finish()
        self.obj['ac9_job_recovery']=recovery
        self.obj['ac9_job']='another-valid-job'
        self.J._WRITER=Writer();self.J.resume(self.context)
        self.assertEqual(self.obj['ac9_job'],'another-valid-job')
        self.assertEqual(self.J._WRITER.requests,[])

    def test_published_reference_is_not_rolled_back_to_recovery(self):
        recovery=self.finish();_,target=self.publish();self.J.collect()
        self.obj['ac9_job_recovery']=recovery
        self.J._WRITER=Writer();self.J.resume(self.context)
        self.assertEqual(self.obj['ac9_job'],target)
        self.assertEqual(self.J._WRITER.requests,[])

    def test_original_working_reference_can_recover_by_same_generation(self):
        recovery=self.finish();target=self.J._WRITER.requests[-1][1]
        self.obj['ac9_job_recovery']=recovery;self.obj['ac9_job']=str(self.work)
        self.J._WRITER=Writer();self.J.resume(self.context)
        self.assertEqual(self.obj['ac9_job'],recovery)
        self.assertEqual(self.J._WRITER.requests,[(recovery,target)])

    def test_accepted_old_job_is_preserved_after_next_job_or_undo(self):
        for invalidate in (False,True):
            with self.subTest(undo=invalidate):
                request=self.J.capture(str(self.work),'garment')
                self.obj['ac9_job']='next-job'
                if invalidate:self.J.invalidate()
                recovery=self.J.finish(self.context,str(self.work),'garment',self.lease,request=request)
                self.assertTrue(W.saved_generation(recovery,self.lease['token']))
                self.assertEqual(self.obj['ac9_job'],'next-job')
                self.assertEqual(self.J._NOTICES,{})
                # Reuse this fixture only for the second ownership boundary.
                self.obj['ac9_job']=str(self.work)
                self.J._TARGETS[str(self.work)]=request['plan']

    def test_background_failure_keeps_local_result(self):
        recovery=self.finish()
        self.J._WRITER.results.put(dict(source=recovery,target='unavailable',error='disk disconnected'))
        self.J.collect()
        self.assertEqual(self.obj['ac9_job'],recovery)
        self.assertTrue(W.saved_generation(recovery,self.lease['token']))
        self.assertIn('disk disconnected',self.context.scene.ac9_cloth_sim.dress_status)

    def test_open_writer_cannot_publish_or_change_reference(self):
        self.lease['writer_released']=False
        with self.assertRaises(ValueError):self.finish()
        self.assertEqual(self.obj['ac9_job'],str(self.work))
        self.assertEqual(self.J._WRITER.requests,[])
        self.assertFalse((self.work/W.PLAN).exists())

    def test_restored_previous_result_is_not_archived_as_new_result(self):
        self.obj['ac9_job']='previous-job'
        self.assertIsNone(self.finish())
        self.assertEqual(self.obj['ac9_job'],'previous-job')

    # ---- the newest result only, after a quiet moment (Fable 2026-11-23 step 2) ------------------------------------
    def second_job(self):
        """A second Live of the same garment, finished: its working folder and lease."""
        work=self.root/'work'/'job_second';work.mkdir(parents=True)
        for name in W.REQUIRED:(work/name).write_text('{}')
        self.obj['ac9_job']=str(work)
        self.J._TARGETS[str(work)]=dict(recovery=str(self.root/'durable'/'AC9_ClothSim_jobs'/work.name),
                                       target=str(self.root/'saved'/'AC9_ClothSim_jobs'/work.name),blend='')
        lease=dict(job=str(work),token='second-generation',writer_released=True,gui_complete=True)
        return work,lease

    def test_quiet_only_the_newest_result_is_copied(self):
        self.J.QUIET_S=60.0
        first=self.finish()
        self.assertEqual(self.J._WRITER.requests,[])          # waits for a quiet moment
        work,lease=self.second_job()
        second=self.J.finish(self.context,str(work),'garment',lease)
        self.assertNotIn(first,self.J._NOTICES)               # the first one's copy is never made
        self.J._QUIET['since']-=61;self.J.collect()
        self.assertEqual([r[0] for r in self.J._WRITER.requests],[second])
        self.assertNotIn(first,self.J.busy_paths())           # free for the clean-up (it is in the history)

    def test_quiet_saving_copies_at_once(self):
        self.J.QUIET_S=60.0
        recovery=self.finish()
        self.J.flush_on_save()
        self.assertEqual([r[0] for r in self.J._WRITER.requests],[recovery])

    def test_quiet_waits_while_a_live_runs(self):
        self.J.QUIET_S=0.0
        self.J._live_running=lambda:True
        self.finish()
        self.assertEqual(self.J._WRITER.requests,[])

    def test_a_copy_under_way_is_stopped_by_a_newer_result(self):
        first=self.finish()                                  # queued (the fake writer holds it)
        work,lease=self.second_job()
        self.J.finish(self.context,str(work),'garment',lease)
        self.assertTrue(self.J._cancelled(first))
        self.J._WRITER.results.put(dict(source=first,target='t',error=None,cancelled=True))
        self.J.collect()
        self.assertFalse(self.J._cancelled(first))
        self.assertNotIn('t',self.noted)
        self.assertEqual(self.context.scene.ac9_cloth_sim.dress_status,'')

    def test_a_copy_made_too_late_goes_into_the_history(self):
        self.finish();self.obj['ac9_job']='next-job';_,target=self.publish();self.J.collect()
        self.assertIn(target,self.noted);self.assertEqual(self.cleaned,[self.obj])

    def test_a_cancelled_export_leaves_nothing_and_the_source_intact(self):
        recovery=self.finish();target=self.J._WRITER.requests[-1][1]
        before=W._inventory(Path(recovery))
        calls=[]
        with self.assertRaises(W.ExportCancelled):
            W.archive_finished(recovery,target,cancelled=lambda:calls.append(1) or len(calls)>2)
        self.assertFalse(Path(target).exists())
        self.assertEqual(list(Path(target).parent.iterdir()),[])
        self.assertEqual(W._inventory(Path(recovery)),before)
        self.assertEqual(W.archive_finished(recovery,target),str(Path(target).resolve()))   # a later copy works

    def test_the_real_writer_reports_a_cancelled_export(self):
        recovery=self.finish();target=self.J._WRITER.requests[-1][1]
        q=W.ArchiveQueue();q.cancelled=lambda source:True
        q.submit_accepted(recovery,target,self.lease['token']);q.requests.join()
        rows=q.collect()
        self.assertEqual(rows,[dict(source=recovery,target=target,error=None,cancelled=True)])
        self.assertFalse(Path(target).exists())


if __name__=='__main__':unittest.main()
