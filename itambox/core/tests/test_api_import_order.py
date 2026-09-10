"""Exercise DRF startup without imports cached by other test modules."""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


class APIImportOrderTests(SimpleTestCase):
    def test_drf_views_first_resolves_normal_permissions_and_exception_handler(self):
        source = """
import sys
import django

django.setup()
assert 'rest_framework.views' not in sys.modules
assert 'itambox.api' not in sys.modules

from rest_framework.views import APIView
from rest_framework.settings import api_settings
from django.core.exceptions import ValidationError
from itambox.api.permissions import StrictTenantPermission, TokenPermissions
from itambox.api.exceptions import itambox_exception_handler

assert APIView.permission_classes == [TokenPermissions, StrictTenantPermission]
assert api_settings.EXCEPTION_HANDLER is itambox_exception_handler
response = api_settings.EXCEPTION_HANDLER(ValidationError({'name': ['Invalid value.']}), {})
assert response.status_code == 400
assert response.data == {'name': ['Invalid value.']}
assert api_settings.EXCEPTION_HANDLER(RuntimeError('unhandled'), {}) is None
"""
        env = os.environ.copy()
        env["DJANGO_SETTINGS_MODULE"] = "core.settings.dev"
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
