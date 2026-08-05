"""Regression tests for worker-local filesystem state."""

import os
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class MediaIsolationTests(SimpleTestCase):
    def test_media_root_is_scoped_to_the_current_pytest_worker(self):
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
        media_root = Path(settings.MEDIA_ROOT).resolve()

        self.assertTrue(media_root.is_dir())
        self.assertNotEqual(media_root, (Path(settings.BASE_DIR) / "media").resolve())
        self.assertIn(f"itambox-media-{worker_id}", str(media_root))
