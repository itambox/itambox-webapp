"""Issue #443 concrete port and lifecycle contracts."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path
from typing import get_args
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core import identity_provisioning, restore_authority
from core.provider_slot import SingleProviderSlot

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _User:
    pk = 7
    username = "user"
    email = "user@example.invalid"


class _Tenant:
    pk = 11
    slug = "tenant"
    is_provider = False
    managed_by_id = None


class _IdentityProvider:
    def __init__(self, marker: object) -> None:
        self.marker = marker

    def provision(self, command: object) -> object:
        return self.marker


class _RestoreProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def validate(self, user: object, obj: object) -> None:
        self.calls.append((user, obj))


def _command() -> identity_provisioning.ExternalIdentityProvisioningCommand:
    return identity_provisioning.ExternalIdentityProvisioningCommand(
        user=_User(),
        customer_tenant=_Tenant(),
        profile=identity_provisioning.ExternalIdentityProfile(
            source="OIDC",
            email=None,
            upn=None,
            first_name="First",
            last_name="Last",
        ),
        customer_role_name="Member",
    )


def _identity_operation() -> object:
    return identity_provisioning.provision_external_identity(_command())


def _restore_operation() -> None:
    restore_authority.validate_restore_grant_authority(_User(), object())


def _missing_message(operation) -> str:
    try:
        operation()
    except ImproperlyConfigured as exc:
        return str(exc)
    raise AssertionError("operation unexpectedly succeeded")


def _run_fresh_subprocess(script: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(_PROJECT_ROOT), environment.get("PYTHONPATH", "")) if part
    )
    return subprocess.run(
        (sys.executable, "-c", script),
        cwd=_PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class Issue443PortSurfaceTests(SimpleTestCase):
    def test_fresh_import_is_pre_setup_safe_and_missing_errors_are_exact(self):
        result = _run_fresh_subprocess(
            """
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from core.provider_slot import SingleProviderSlot

get_calls = []
original_get = SingleProviderSlot.get
def forbidden_get(self):
    get_calls.append(type(self).__name__)
    raise AssertionError("provider retrieved during import")
SingleProviderSlot.get = forbidden_get
from core import identity_provisioning, restore_authority
SingleProviderSlot.get = original_get
assert not apps.ready
assert not get_calls

try:
    identity_provisioning.provision_external_identity(None)
except ImproperlyConfigured as exc:
    assert str(exc) == "identity provisioner provider is not configured", str(exc)
else:
    raise AssertionError("identity operation unexpectedly succeeded")

try:
    restore_authority.validate_restore_grant_authority(None, None)
except ImproperlyConfigured as exc:
    assert str(exc) == "restore-authority validator provider is not configured", str(exc)
else:
    raise AssertionError("restore operation unexpectedly succeeded")

assert not apps.ready
print("fresh-import-and-missing-contract-ok")
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fresh-import-and-missing-contract-ok", result.stdout)

    def test_registration_is_idempotent_and_conflicts_are_exact_in_fresh_process(self):
        result = _run_fresh_subprocess(
            """
from django.core.exceptions import ImproperlyConfigured
from core import identity_provisioning, restore_authority

class Identity:
    def __init__(self, marker):
        self.marker = marker
    def provision(self, command):
        return self.marker

class Restore:
    def __init__(self):
        self.calls = 0
    def validate(self, user, obj):
        self.calls += 1

identity_first = Identity("identity-first")
identity_other = Identity("identity-other")
identity_provisioning.configure_identity_provisioner(identity_first)
identity_provisioning.configure_identity_provisioner(identity_first)
assert identity_provisioning.provision_external_identity(None) == "identity-first"
try:
    identity_provisioning.configure_identity_provisioner(identity_other)
except ImproperlyConfigured as exc:
    assert str(exc) == "identity provisioner provider is already configured with a different object", str(exc)
else:
    raise AssertionError("identity conflict unexpectedly succeeded")
assert identity_provisioning.provision_external_identity(None) == "identity-first"

restore_first = Restore()
restore_other = Restore()
restore_authority.configure_restore_authority_validator(restore_first)
restore_authority.configure_restore_authority_validator(restore_first)
restore_authority.validate_restore_grant_authority(None, None)
assert restore_first.calls == 1
try:
    restore_authority.configure_restore_authority_validator(restore_other)
except ImproperlyConfigured as exc:
    assert str(exc) == "restore-authority validator provider is already configured with a different object", str(exc)
else:
    raise AssertionError("restore conflict unexpectedly succeeded")
restore_authority.validate_restore_grant_authority(None, None)
assert restore_first.calls == 2
print("registration-contract-ok")
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("registration-contract-ok", result.stdout)

    def test_two_concrete_slots_are_distinct_and_overrides_are_independent(self):
        identity_provider = _IdentityProvider("identity")
        restore_provider = _RestoreProvider()
        user = _User()
        obj = object()

        self.assertIsInstance(identity_provisioning._identity_provisioner, SingleProviderSlot)
        self.assertIsInstance(restore_authority._restore_authority_validator, SingleProviderSlot)
        self.assertIsNot(
            identity_provisioning._identity_provisioner,
            restore_authority._restore_authority_validator,
        )

        with identity_provisioning.override_identity_provisioner(identity_provider):
            self.assertEqual(_identity_operation(), "identity")
            self.assertEqual(
                _missing_message(_restore_operation),
                "restore-authority validator provider is not configured",
            )

        with restore_authority.override_restore_authority_validator(restore_provider):
            restore_authority.validate_restore_grant_authority(user, obj)
            self.assertEqual(restore_provider.calls, [(user, obj)])
            self.assertEqual(
                _missing_message(_identity_operation),
                "identity provisioner provider is not configured",
            )

        self.assertEqual(_missing_message(_identity_operation), "identity provisioner provider is not configured")
        self.assertEqual(
            _missing_message(_restore_operation),
            "restore-authority validator provider is not configured",
        )

    def test_identity_override_is_nested_exception_safe_thread_local_and_copyable(self):
        outer = _IdentityProvider("outer")
        inner = _IdentityProvider("inner")

        with identity_provisioning.override_identity_provisioner(outer):
            self.assertEqual(_identity_operation(), "outer")
            with identity_provisioning.override_identity_provisioner(inner):
                self.assertEqual(_identity_operation(), "inner")
            self.assertEqual(_identity_operation(), "outer")

            with self.assertRaisesRegex(RuntimeError, "boom"):
                with identity_provisioning.override_identity_provisioner(inner):
                    raise RuntimeError("boom")
            self.assertEqual(_identity_operation(), "outer")

            copied_context = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                plain_result = executor.submit(_missing_message, _identity_operation).result()
                copied_result = executor.submit(copied_context.run, _identity_operation).result()

            self.assertEqual(plain_result, "identity provisioner provider is not configured")
            self.assertEqual(copied_result, "outer")

        self.assertEqual(_missing_message(_identity_operation), "identity provisioner provider is not configured")

    def test_restore_override_is_nested_and_exception_safe(self):
        outer = _RestoreProvider()
        inner = _RestoreProvider()
        user = _User()
        obj = object()

        with restore_authority.override_restore_authority_validator(outer):
            restore_authority.validate_restore_grant_authority(user, obj)
            self.assertEqual(len(outer.calls), 1)
            with restore_authority.override_restore_authority_validator(inner):
                restore_authority.validate_restore_grant_authority(user, obj)
            self.assertEqual(len(inner.calls), 1)
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with restore_authority.override_restore_authority_validator(inner):
                    raise RuntimeError("boom")
            restore_authority.validate_restore_grant_authority(user, obj)

        self.assertEqual(outer.calls, [(user, obj), (user, obj)])
        self.assertEqual(
            _missing_message(_restore_operation),
            "restore-authority validator provider is not configured",
        )

    def test_operation_lookup_uses_module_owner_at_call_time(self):
        command = _command()
        identity_result = object()
        restore_result = object()

        def identity_adapter_call():
            from core import identity_provisioning as port

            return port.provision_external_identity(command)

        def restore_adapter_call():
            from core import restore_authority as port

            return port.validate_restore_grant_authority(_User(), object())

        with mock.patch.object(
            identity_provisioning,
            "provision_external_identity",
            return_value=identity_result,
        ) as identity_operation:
            self.assertIs(identity_adapter_call(), identity_result)
            identity_operation.assert_called_once_with(command)

        with mock.patch.object(
            restore_authority,
            "validate_restore_grant_authority",
            return_value=restore_result,
        ) as restore_operation:
            self.assertIs(restore_adapter_call(), restore_result)
            restore_operation.assert_called_once()

    def test_sdk_free_dto_and_literal_surface_is_exact(self):
        self.assertEqual(get_args(identity_provisioning.IdentitySource), ("LDAP", "OIDC", "SAML"))
        self.assertEqual(
            get_args(identity_provisioning.CustomerRoleName),
            ("Admin", "Manager", "Member"),
        )
        self.assertEqual(
            get_args(identity_provisioning.ProvisioningMode),
            ("customer", "provider_staff", "provider_mapping_rejected"),
        )
        for protocol in (
            identity_provisioning.UserRef,
            identity_provisioning.TenantRef,
            identity_provisioning.IdentityProvisioner,
            restore_authority.PrincipalRef,
            restore_authority.RestoreAuthorityValidator,
        ):
            self.assertTrue(getattr(protocol, "_is_protocol", False))

        self.assertEqual(
            [field.name for field in fields(identity_provisioning.ExternalIdentityProfile)],
            ["source", "email", "upn", "first_name", "last_name"],
        )
        self.assertEqual(
            [field.name for field in fields(identity_provisioning.ProviderStaffIntent)],
            ["provider_tenant", "role_name"],
        )
        self.assertEqual(
            [field.name for field in fields(identity_provisioning.ExternalIdentityProvisioningCommand)],
            ["user", "customer_tenant", "profile", "customer_role_name", "provider_staff"],
        )
        result_fields = fields(identity_provisioning.ExternalIdentityProvisioningResult)
        self.assertEqual(
            [field.name for field in result_fields],
            ["mode", "holder_id", "membership_id", "role_id"],
        )
        self.assertEqual([field.default for field in result_fields[1:]], [None, None, None])
        self.assertEqual(
            tuple(inspect.signature(identity_provisioning.IdentityProvisioner.provision).parameters),
            ("self", "command"),
        )
        self.assertEqual(
            tuple(inspect.signature(restore_authority.RestoreAuthorityValidator.validate).parameters),
            ("self", "user", "obj"),
        )

        for dto in (
            identity_provisioning.ExternalIdentityProfile,
            identity_provisioning.ProviderStaffIntent,
            identity_provisioning.ExternalIdentityProvisioningCommand,
            identity_provisioning.ExternalIdentityProvisioningResult,
        ):
            for field in fields(dto):
                self.assertNotIn(
                    field.name,
                    {"groups", "claims", "tokens", "access_token", "id_token", "settings"},
                )

        for source in (inspect.getsource(identity_provisioning), inspect.getsource(restore_authority)):
            for forbidden in (
                "mozilla_django_oidc",
                "django.conf",
                "organization.models",
                "users.models",
                "claims",
                "tokens",
                "settings",
            ):
                self.assertNotIn(forbidden, source)

    def test_public_surface_has_only_named_types_and_wrappers(self):
        identity_public = {name for name in vars(identity_provisioning) if not name.startswith("_")}
        restore_public = {name for name in vars(restore_authority) if not name.startswith("_")}
        self.assertEqual(identity_public, set(identity_provisioning.__all__))
        self.assertEqual(restore_public, set(restore_authority.__all__))

        self.assertEqual(
            set(identity_provisioning.__all__),
            {
                "IdentitySource",
                "CustomerRoleName",
                "ProvisioningMode",
                "UserRef",
                "TenantRef",
                "ExternalIdentityProfile",
                "ProviderStaffIntent",
                "ExternalIdentityProvisioningCommand",
                "ExternalIdentityProvisioningResult",
                "IdentityProvisioner",
                "configure_identity_provisioner",
                "provision_external_identity",
                "override_identity_provisioner",
            },
        )
        self.assertEqual(
            set(restore_authority.__all__),
            {
                "PrincipalRef",
                "RestoreAuthorityValidator",
                "configure_restore_authority_validator",
                "validate_restore_grant_authority",
                "override_restore_authority_validator",
            },
        )

        forbidden = (
            "get_identity_provisioner",
            "get_restore_authority_validator",
            "reset_identity_provisioner",
            "reset_restore_authority_validator",
            "clear_identity_provisioner",
            "clear_restore_authority_validator",
            "discover_provider",
            "provider_registry",
            "providers",
            "keys",
            "registry",
        )
        for name in forbidden:
            self.assertFalse(hasattr(identity_provisioning, name), name)
            self.assertFalse(hasattr(restore_authority, name), name)


class Issue443AsyncPortTests(SimpleTestCase):
    def test_identity_child_task_captures_override_at_creation(self):
        outer = _IdentityProvider("outer")
        inner = _IdentityProvider("inner")

        async def child_operation() -> object:
            await asyncio.sleep(0)
            return _identity_operation()

        async def exercise() -> None:
            with identity_provisioning.override_identity_provisioner(outer):
                task = asyncio.create_task(child_operation())
                with identity_provisioning.override_identity_provisioner(inner):
                    self.assertEqual(_identity_operation(), "inner")
                self.assertEqual(await task, "outer")
            self.assertEqual(
                _missing_message(_identity_operation),
                "identity provisioner provider is not configured",
            )

        asyncio.run(exercise())
