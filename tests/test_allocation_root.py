from pathlib import Path
import os,stat,subprocess,tempfile,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import prepare_build as PB

class AllocationRootTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='ac9_root_identity_');self.base=Path(self.temp.name);PB._ROOTS.clear()
 def tearDown(self):self.temp.cleanup();PB._ROOTS.clear()
 def test_repeated_root_resolves_once_and_cleanup_still_checks(self):
  original=Path.resolve;calls=[]
  def resolve(path,*args,**kwargs):calls.append(str(path));return original(path,*args,**kwargs)
  with patch.object(Path,'resolve',resolve):
   first=PB.OwnedJobDirectory.create(self.base/'jobs','a');second=PB.OwnedJobDirectory.create(self.base/'jobs','b')
   self.assertEqual(len(calls),1)
   self.assertTrue(first.matches());self.assertGreater(len(calls),1)
  first.remove();second.remove();self.assertFalse(Path(first).exists())
 def test_deleted_recreated_root_misses_and_never_removes_replacement(self):
  root=self.base/'jobs';owned=PB.OwnedJobDirectory.create(root,'a');owned.remove();root.rmdir();root.mkdir()
  replacement=root/'a';replacement.mkdir();(replacement/'keep').write_text('keep')
  owned.remove();self.assertTrue((replacement/'keep').exists())
  fresh=PB.OwnedJobDirectory.create(root,'b');self.assertTrue(fresh.matches());fresh.remove()
 def test_marker_change_refuses_removal(self):
  owned=PB.OwnedJobDirectory.create(self.base/'jobs','a');owned.marker.write_text('changed')
  owned.remove();self.assertTrue(Path(owned).exists())
 def test_zero_identity_is_not_cached(self):
  root=self.base/'jobs';root.mkdir()
  with patch.object(Path,'stat',return_value=NS(st_mode=stat.S_IFDIR,st_dev=0,st_ino=0)):
   PB._allocation_root(root)
  self.assertFalse(PB._ROOTS)
 @unittest.skipUnless(os.name=='nt','junction')
 def test_junction_retarget_invalidates_both_sides(self):
  first=self.base/'first';second=self.base/'second';first.mkdir();second.mkdir();link=self.base/'junction'
  def junction(target):
   subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],check=True,capture_output=True)
  junction(first)
  owned=PB.OwnedJobDirectory.create(link,'a');self.assertEqual(owned.root,first.resolve())
  os.rmdir(link);junction(second)
  fresh=PB.OwnedJobDirectory.create(link,'b');self.assertEqual(fresh.root,second.resolve())
  keep=second/'a';keep.mkdir();(keep/'keep').write_text('keep')
  owned.remove();self.assertTrue((keep/'keep').exists());fresh.remove();os.rmdir(link)

if __name__=='__main__':unittest.main()
