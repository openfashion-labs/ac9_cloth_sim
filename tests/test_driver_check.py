"""core/driver_check.py: the line said at the press when the NVIDIA driver is missing or older than the bundled CUDA.

  python tests/test_driver_check.py     (no Blender, no Warp; also prints this PC's driver)
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('driver_check', ROOT / 'core' / 'driver_check.py')
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

# an old driver: the versions in the message's arguments
msg, args = D.warning(version=(12, 2), bundled=(12, 9))
assert msg == D.OLD_DRIVER and args == ('12.2', '12.9'), (msg, args)
assert '{0}' in msg and '{1}' in msg
# as new as the bundled CUDA, or newer: nothing
assert D.warning(version=(12, 9), bundled=(12, 9)) is None
assert D.warning(version=(13, 5), bundled=(12, 9)) is None
# a minor version compares as a number (12.10 is newer than 12.9)
assert D.warning(version=(12, 10), bundled=(12, 9)) is None
# no driver
D._CACHE['v'] = None
assert D.warning() == (D.NO_DRIVER, ())
D._CACHE.clear()
print('ok test_driver_check; this PC:', D.driver_cuda())
