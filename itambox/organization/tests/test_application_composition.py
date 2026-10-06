"""Issue #443 composition-root, lifecycle, and legacy-removal contracts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from core import identity_provisioning, restore_authority, tenant_access
from core.context import (
    get_current_all_accessible,
    get_current_membership,
    get_current_tenant,
    get_current_tenant_group,
)
from core.provider_slot import SingleProviderSlot
from organization.services.identity_provisioning import organization_identity_provisioner
from organization.services.restore_authority import organization_restore_authority
from organization.services.tenant_access import organization_tenant_access_policy

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fresh_subprocess(script: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "core.settings.dev",
        "ITAMBOX_ENV": "dev",
        "ITAMBOX_SECRET_KEY": "issue443-composition-test-secret",
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    return subprocess.run(
        (sys.executable, "-c", script),
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _context_snapshot() -> tuple[object, object, object, bool]:
    return (
        get_current_tenant(),
        get_current_membership(),
        get_current_tenant_group(),
        get_current_all_accessible(),
    )


class OrganizationReadyCompositionTests(SimpleTestCase):
    databases = {"default"}

    def test_ready_binds_all_three_preconstructed_singletons_and_repeats_without_queries(self):
        config = apps.get_app_config("organization")
        before = _context_snapshot()

        with CaptureQueriesContext(connection) as queries:
            config.ready()
            config.ready()

        self.assertEqual(list(queries), [])
        self.assertIs(
            tenant_access._tenant_access_policy._default,
            organization_tenant_access_policy,
        )
        self.assertIs(
            identity_provisioning._identity_provisioner._default,
            organization_identity_provisioner,
        )
        self.assertIs(
            restore_authority._restore_authority_validator._default,
            organization_restore_authority,
        )
        self.assertEqual(_context_snapshot(), before)

    def test_ready_retrieves_no_provider_and_runs_no_service_operation(self):
        config = apps.get_app_config("organization")
        before = _context_snapshot()

        with (
            mock.patch.object(
                SingleProviderSlot,
                "get",
                side_effect=AssertionError("ready retrieved a provider"),
            ),
            mock.patch.object(
                organization_identity_provisioner,
                "provision",
                side_effect=AssertionError("ready provisioned an identity"),
            ),
            mock.patch.object(
                organization_restore_authority,
                "validate",
                side_effect=AssertionError("ready validated restore authority"),
            ),
            CaptureQueriesContext(connection) as queries,
        ):
            config.ready()

        self.assertEqual(list(queries), [])
        self.assertEqual(_context_snapshot(), before)

    def test_post_setup_smoke_has_all_bindings_and_imports_backends(self):
        result = _fresh_subprocess(
            """
import django
django.setup()
from django.apps import apps
from core import identity_provisioning, restore_authority, tenant_access
from organization.services.identity_provisioning import organization_identity_provisioner
from organization.services.restore_authority import organization_restore_authority
from organization.services.tenant_access import organization_tenant_access_policy
from core.auth import ldap, oidc, saml
from itambox.views.generic import restore
assert apps.ready
assert tenant_access._tenant_access_policy._default is organization_tenant_access_policy
assert identity_provisioning._identity_provisioner._default is organization_identity_provisioner
assert restore_authority._restore_authority_validator._default is organization_restore_authority
assert ldap.MultiTenantLDAPBackend
assert oidc.TenantOIDCBackend
assert saml.TenantSaml2Backend
assert restore.ObjectRestoreView
print('issue443-post-setup-bindings-and-backends-ok')
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("issue443-post-setup-bindings-and-backends-ok", result.stdout)

    def test_post_setup_different_objects_conflict_with_exact_errors_for_all_three_slots(self):
        result = _fresh_subprocess(
            """
import django
django.setup()
from django.core.exceptions import ImproperlyConfigured
from core import identity_provisioning, restore_authority, tenant_access

class Identity:
    def provision(self, command):
        return None

class Restore:
    def validate(self, user, obj):
        return None

class Access:
    def accessible_tenant_ids(self, user):
        return set()
    def active_membership(self, user, tenant_id):
        return None
    def first_active_membership_in(self, user, authorized_tenant_ids):
        return None
    def shared_stock_read_allowed(self, obj, active_tenant, user, perm=None):
        return False

checks = (
    (
        identity_provisioning.configure_identity_provisioner,
        Identity(),
        'identity provisioner provider is already configured with a different object',
    ),
    (
        restore_authority.configure_restore_authority_validator,
        Restore(),
        'restore-authority validator provider is already configured with a different object',
    ),
    (
        tenant_access.configure_tenant_access_policy,
        Access(),
        'tenant access policy provider is already configured with a different object',
    ),
)
for configure, replacement, expected in checks:
    try:
        configure(replacement)
    except ImproperlyConfigured as exc:
        assert str(exc) == expected, str(exc)
    else:
        raise AssertionError('different startup object unexpectedly replaced a binding')
print('issue443-startup-conflicts-exact-ok')
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("issue443-startup-conflicts-exact-ok", result.stdout)

    def test_deleted_legacy_provisioning_module_is_refused_in_fresh_process(self):
        result = _fresh_subprocess(
            """
import importlib
import django
django.setup()
legacy_module = '.'.join(('core', 'auth', 'provisioning'))
try:
    importlib.import_module(legacy_module)
except ModuleNotFoundError as exc:
    assert exc.name == legacy_module, (exc.name, legacy_module)
else:
    raise AssertionError('deleted legacy provisioning module remains importable')
print('issue443-legacy-import-refused-ok')
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("issue443-legacy-import-refused-ok", result.stdout)
