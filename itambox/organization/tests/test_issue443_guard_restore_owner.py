"""Issue #443 guard-owner and restore-port contracts."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from core import tenant_scope
from itambox.views.generic import restore as generic_restore
from organization.services import role_grant_validation
from organization.services.restore_authority import (
    OrganizationRestoreAuthority,
    organization_restore_authority,
)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class GuardOwnerSurfaceTests(SimpleTestCase):
    def test_public_signatures_are_moved_without_compatibility_changes(self):
        expected = {
            "validate_permission_grant": "(granting_user, permissions, tenant)",
            "validate_role_grant": "(granting_user, role, principal_tenant, *, scope_type='own', requested_tenant_ids=None)",
            "validate_role_reactivation_grants": "(granting_user, role)",
            "validate_group_membership_grant": "(granting_user, group)",
            "_live_role_grant_scope_request": "(grant, *, restoring_role_id=None, restoring_user_group_id=None)",
        }
        for name, signature in expected.items():
            self.assertEqual(str(inspect.signature(getattr(role_grant_validation, name))), signature)

    def test_permission_grant_uses_canonical_tenant_scope_and_never_membership_backend(self):
        actor = SimpleNamespace(is_superuser=False)
        tenant = object()
        with mock.patch.object(
            tenant_scope,
            "resolve_effective_permissions_with_expiry",
            return_value=(frozenset({"assets.view_asset"}), object()),
        ) as resolve:
            with mock.patch.object(role_grant_validation, "MembershipBackend", create=True) as backend:
                role_grant_validation.validate_permission_grant(actor, ["assets.view_asset"], tenant)

        resolve.assert_called_once_with(actor, tenant)
        backend.assert_not_called()

    def test_permission_grant_preserves_bypass_and_exact_message(self):
        tenant = object()
        with mock.patch.object(tenant_scope, "resolve_effective_permissions_with_expiry") as resolve:
            role_grant_validation.validate_permission_grant(None, ["assets.delete_asset"], tenant)
            role_grant_validation.validate_permission_grant(
                SimpleNamespace(is_superuser=True), ["assets.delete_asset"], tenant
            )
        resolve.assert_not_called()

        actor = SimpleNamespace(is_superuser=False)
        with mock.patch.object(
            tenant_scope,
            "resolve_effective_permissions_with_expiry",
            return_value=(frozenset(), None),
        ):
            with self.assertRaises(ValidationError) as caught:
                role_grant_validation.validate_permission_grant(actor, ["assets.delete_asset"], tenant)
        self.assertEqual(
            caught.exception.messages,
            ["Privilege escalation detected: you cannot grant permissions you do not hold: assets.delete_asset"],
        )

    def test_owner_does_not_import_integration_guard_or_membership_backend(self):
        source = inspect.getsource(role_grant_validation)
        self.assertNotIn("core.auth.guards", source)
        self.assertNotIn("MembershipBackend", source)
        self.assertNotIn("from core.auth", source)


class RestoreAuthorityDispatchTests(SimpleTestCase):
    @staticmethod
    def _obj(label, **kwargs):
        return SimpleNamespace(_meta=SimpleNamespace(label_lower=label), **kwargs)

    def test_role_label_dispatches_only_role_reactivation(self):
        user = object()
        obj = self._obj("organization.role")
        with mock.patch.object(role_grant_validation, "validate_role_reactivation_grants") as role_guard:
            with mock.patch.object(role_grant_validation, "validate_group_membership_grant") as group_guard:
                OrganizationRestoreAuthority().validate(user, obj)
        role_guard.assert_called_once_with(user, obj)
        group_guard.assert_not_called()

    def test_active_group_dispatches_only_group_membership(self):
        user = object()
        obj = self._obj("users.usergroup", is_active=True)
        with mock.patch.object(role_grant_validation, "validate_role_reactivation_grants") as role_guard:
            with mock.patch.object(role_grant_validation, "validate_group_membership_grant") as group_guard:
                OrganizationRestoreAuthority().validate(user, obj)
        role_guard.assert_not_called()
        group_guard.assert_called_once_with(user, obj)

    def test_inactive_group_and_safe_labels_are_exact_no_ops(self):
        user = object()
        inactive = self._obj("users.usergroup", is_active=False)
        safe = self._obj("assets.site")
        with mock.patch.object(role_grant_validation, "validate_role_reactivation_grants") as role_guard:
            with mock.patch.object(role_grant_validation, "validate_group_membership_grant") as group_guard:
                validator = OrganizationRestoreAuthority()
                validator.validate(user, inactive)
                validator.validate(user, safe)
        role_guard.assert_not_called()
        group_guard.assert_not_called()

    def test_singleton_is_preconstructed_and_annotated(self):
        self.assertIsInstance(organization_restore_authority, OrganizationRestoreAuthority)
        source = inspect.getsource(__import__("organization.services.restore_authority", fromlist=["*"]))
        self.assertIn("organization_restore_authority: RestoreAuthorityValidator", source)


class GenericRestorePortTests(TestCase):
    def test_generic_restore_has_only_the_named_port_import(self):
        source = inspect.getsource(generic_restore)
        tree = ast.parse(source)
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertTrue(
            any(
                isinstance(node, ast.ImportFrom)
                and node.module == "core"
                and [alias.name for alias in node.names] == ["restore_authority"]
                for node in imports
            )
        )
        self.assertFalse(
            any(isinstance(node, ast.ImportFrom) and node.module == "core.auth.guards" for node in imports)
        )
        self.assertFalse(
            any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("organization") for node in imports)
        )
        self.assertNotIn("importlib", source)
        self.assertNotIn("_validate_restore_grant_authority", source)

    def test_single_restore_calls_port_wrapper_at_operation_time(self):
        obj = SimpleNamespace(
            pk=7,
            _meta=SimpleNamespace(label_lower="assets.site"),
            restore=mock.Mock(),
        )
        model = SimpleNamespace(
            _meta=SimpleNamespace(verbose_name="site", app_label="assets", model_name="site"),
        )
        request = SimpleNamespace(user=SimpleNamespace(pk=11), META={})
        view = generic_restore.ObjectRestoreView()
        view.object = obj
        view.model = model
        view._htmx_or_redirect = mock.Mock(return_value="ok")
        with mock.patch.object(generic_restore.restore_authority, "validate_restore_grant_authority") as validate:
            self.assertEqual(view.post(request), "ok")
        validate.assert_called_once_with(request.user, obj)
        obj.restore.assert_called_once_with()

    def test_bulk_restore_calls_port_wrapper_for_every_selected_row_at_operation_time(self):
        first = SimpleNamespace(pk=1, restore=mock.Mock())
        second = SimpleNamespace(pk=2, restore=mock.Mock())
        model = SimpleNamespace(
            _meta=SimpleNamespace(verbose_name_plural="sites"),
            _base_manager=SimpleNamespace(filter=mock.Mock(return_value=object())),
        )
        request = SimpleNamespace(
            user=SimpleNamespace(pk=11),
            META={},
            POST=SimpleNamespace(getlist=mock.Mock(return_value=["1", "2"])),
        )
        view = generic_restore.ObjectBulkRestoreView()
        view.model = model
        view._htmx_or_redirect = mock.Mock(return_value="ok")
        with mock.patch.object(generic_restore, "filter_permitted_rows", return_value=([first, second], 0)):
            with mock.patch.object(generic_restore.restore_authority, "validate_restore_grant_authority") as validate:
                self.assertEqual(view.post(request), "ok")
        self.assertEqual(validate.call_args_list, [mock.call(request.user, first), mock.call(request.user, second)])
        first.restore.assert_called_once_with()
        second.restore.assert_called_once_with()

    def test_missing_binding_and_old_import_refusal_are_exact(self):
        script = """
import django
django.setup()
from django.core.exceptions import ImproperlyConfigured
from core import restore_authority
try:
    restore_authority.validate_restore_grant_authority(None, None)
except ImproperlyConfigured as exc:
    assert str(exc) == 'restore-authority validator provider is not configured', str(exc)
else:
    raise AssertionError('restore operation unexpectedly succeeded')
print('missing-restore-binding-ok')
"""
        result = subprocess.run(
            (sys.executable, "-c", script),
            cwd=_PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": _PROJECT_ROOT},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("missing-restore-binding-ok", result.stdout)

        refusal = subprocess.run(
            (
                sys.executable,
                "-c",
                "import django; django.setup(); "
                "import importlib; importlib.import_module('core.auth.guards'); "
                "raise AssertionError('old guard module imported')",
            ),
            cwd=_PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": _PROJECT_ROOT},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(refusal.returncode, 0)
        self.assertIn("ModuleNotFoundError", refusal.stderr)
