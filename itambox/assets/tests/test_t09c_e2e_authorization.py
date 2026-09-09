"""Regression coverage for the CI E2E principal's tenant authorization graph."""

import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from assets.models import AssetType, Manufacturer, StatusLabel
from assets.specification_adapters import authorization_for_asset
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.access_scope import ResolvedAccessAuthorizationDTO

User = get_user_model()
REPO_ROOT = Path(__file__).resolve().parents[3]
E2E_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "e2e.yml"


def _workflow_provisioning_source() -> str:
    workflow = E2E_WORKFLOW_PATH.read_text(encoding="utf-8")
    marker = "python manage.py shell <<'PY'\n"
    start = workflow.index(marker) + len(marker)
    end = workflow.index("\n          PY\n", start)
    lines = workflow[start:end].splitlines()
    source = "\n".join(line[10:] if line.startswith("          ") else line for line in lines)
    if not source.strip():
        raise AssertionError("the E2E principal provisioning block is empty")
    return f"{source}\n"


def _run_workflow_provisioning(tenant: Tenant, secondary_tenant: Tenant) -> None:
    """Execute the checked-in disposable provisioning block without exposing tokens."""
    with tempfile.TemporaryDirectory(prefix="t09c-e2e-env-") as temporary_directory:
        github_env = Path(temporary_directory) / "github.env"
        environment = {
            "E2E_USERNAME": "e2e-admin",
            "DJANGO_SUPERUSER_EMAIL": "e2e-admin@example.test",
            "E2E_PASSWORD": "test-password",
            "E2E_TENANT_SLUG": tenant.slug,
            "E2E_AGGREGATE_SECOND_TENANT_SLUG": secondary_tenant.slug,
            "E2E_ISOLATION_TENANT_SLUG": "e2e-isolation-tenant",
            "E2E_OPERATOR_USERNAME": "e2e-operator",
            "E2E_VIEWER_USERNAME": "e2e-viewer",
            "E2E_TENANT_GROUP_NAME": "E2E Test Tenant Group",
            "E2E_OIDC_EMAIL": "oidc-fixture@example.test",
            "GITHUB_ENV": str(github_env),
        }
        printed = []

        def safe_print(*args, **kwargs):
            rendered = " ".join(str(arg) for arg in args)
            if not rendered.startswith("::add-mask::"):
                printed.append(rendered)

        with patch.dict(os.environ, environment, clear=False):
            exec(
                compile(_workflow_provisioning_source(), str(E2E_WORKFLOW_PATH), "exec"),
                {"__name__": "__t09c_workflow_provisioning__", "print": safe_print},
            )


class E2CE2EPrincipalAuthorizationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="E2E Contract Tenant", slug="e2e-contract-tenant")
        self.secondary_tenant = Tenant.objects.create(name="E2E Secondary Tenant", slug="e2e-secondary-tenant")
        self.administrator = Role.objects.create(
            tenant=self.tenant,
            name="Administrator",
            permissions=["assets.add_asset", "assets.change_asset"],
        )
        Role.objects.create(
            tenant=self.tenant,
            name="Asset Manager",
            permissions=["assets.add_asset", "assets.change_asset"],
        )
        Role.objects.create(
            tenant=self.tenant,
            name="Read-Only",
            permissions=["assets.view_asset"],
        )

    def _provision_own_scope(self, user, tenant, role):
        membership = Membership.objects.create(user=user, tenant=tenant)
        grant = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason="T09-C authorization regression",
            valid_until=timezone.now() + timedelta(hours=4),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
            tenant=None,
            tenant_group=None,
        )

    def test_workflow_provisions_e2e_admin_for_explicit_asset_scope(self):
        manufacturer = Manufacturer.objects.create(name="E2E workflow manufacturer", slug="e2e-workflow-manufacturer")
        for slug in ("dell-latitude-5550", "cisco-catalyst-9300"):
            AssetType.objects.create(manufacturer=manufacturer, model=slug, slug=slug)
        StatusLabel.objects.create(name="E2E workflow deployable", slug="e2e-workflow-deployable", type="deployable")
        _run_workflow_provisioning(self.tenant, self.secondary_tenant)

        e2e_admin = User._base_manager.get(username="e2e-admin")
        authorization = authorization_for_asset(user=e2e_admin, tenant_id=self.tenant.pk)

        self.assertIsInstance(authorization, ResolvedAccessAuthorizationDTO)
        self.assertEqual(authorization.initial_scope.authorized_tenant_ids, frozenset({self.tenant.pk}))

    def test_unprovisioned_superuser_remains_denied(self):
        actor = User.objects.create_user(username="e2e-unprovisioned", password="test-password")
        actor.is_staff = True
        actor.is_superuser = True
        actor.save(update_fields=["is_staff", "is_superuser"])

        with self.assertRaises(PermissionDenied):
            authorization_for_asset(user=actor, tenant_id=self.tenant.pk)

    def test_actor_granted_only_in_another_tenant_remains_denied(self):
        actor = User.objects.create_user(username="e2e-secondary-only", password="test-password")
        actor.is_staff = True
        actor.is_superuser = True
        actor.save(update_fields=["is_staff", "is_superuser"])
        secondary_role = Role.objects.create(
            tenant=self.secondary_tenant,
            name="Secondary Administrator",
            permissions=["assets.change_asset"],
        )
        self._provision_own_scope(actor, self.secondary_tenant, secondary_role)

        with self.assertRaises(PermissionDenied):
            authorization_for_asset(user=actor, tenant_id=self.tenant.pk)
