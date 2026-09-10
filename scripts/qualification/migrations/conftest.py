"""Django test environment for explicit migration qualification.

Run explicitly:

    PYTHONPATH=itambox pytest scripts/qualification/migrations/

These rehearsals are deliberately NOT part of the normal serial_only lane;
they execute against their own database lifecycle via
core.tests.migration_harness.
"""
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_ITAMBOX = _REPO / 'itambox'

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
os.environ.setdefault('ITAMBOX_ENV', 'dev')
os.environ.setdefault('ITAMBOX_CACHE_BACKEND', 'locmem')

for _path in (_ITAMBOX, _REPO):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
