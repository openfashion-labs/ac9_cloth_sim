import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from ac9_cloth_sim.solver import prepared_live as P

class FingerprintLiterals(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.idx=self.root/'original.npz';self.avatar=self.root/'body.npz'
        np.savez(self.idx,Xinit=np.zeros((4,3)),Xclo=np.zeros((4,3)))
        np.savez(self.avatar,V=np.zeros((3,3)))
        self.cfg=dict(native_three=True,live=True)
    def test_known_labels_never_probe_files_but_still_change_identity(self):
        self.cfg.update(membrane='bw',stitch_ramp_shape='ease_out',seam_bend=dict(pairs='all'),
            active_pieces=['Rectangle','Rectangle.002','Piece','Piece_R','Rectangle.003','Rectangle.003_R'])
        with patch.object(Path,'is_file',side_effect=AssertionError('literal field must not query cwd')):
            first=P.fingerprint(self.idx,self.avatar,self.cfg,'env',implementation='fixed')
            self.cfg['membrane']='nh'
            second=P.fingerprint(self.idx,self.avatar,self.cfg,'env',implementation='fixed')
        self.assertNotEqual(first,second)

    def test_lookalike_unknown_keys_keep_complete_file_validation(self):
        asset=self.root/'asset.dat';asset.write_bytes(b'before')
        for key in ('active_pieces[0]','seam_bend.pairs','membrane[plugin]','extension'):
            with self.subTest(key=key):
                self.cfg[key]=str(asset)
                first=P.fingerprint(self.idx,self.avatar,self.cfg,'env',implementation='fixed')
                asset.write_bytes(b'changed')
                second=P.fingerprint(self.idx,self.avatar,self.cfg,'env',implementation='fixed')
                self.assertNotEqual(first,second)
                self.cfg.pop(key);asset.write_bytes(b'before')

    def test_nested_and_dotted_unknown_fields_cannot_hide_either_file(self):
        a=self.root/'a.dat';b=self.root/'b.dat';a.write_bytes(b'a');b.write_bytes(b'b')
        self.cfg.update(extension=dict(file=str(a)),**{'extension.file':str(b)})
        def identity():return P.fingerprint(self.idx,self.avatar,self.cfg,'env',implementation='fixed')
        first=identity();a.write_bytes(b'a2');second=identity();b.write_bytes(b'b2');third=identity()
        self.assertNotEqual(first,second);self.assertNotEqual(second,third)
