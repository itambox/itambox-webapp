"""Generic export contract: fail-closed default, declared scopes, denied surfaces.

The route under test is ``ObjectExportView`` (``/export/<app>/<model>/<template>/``).
Its gate is the declared policy in ``core.data_transfer``:

* an undeclared or denied model answers 404 on every path — CSV, YAML, template
  render, ``export_scope=all`` / ``filtered`` and ``pk=`` lists;
* ``export_scope=all`` means every row the requester may see under the model's
  declared scope, never every row in the database;
* a personal model is narrowed to the requesting user, so a member never
  receives another user's rows.

The contract test for the inventory itself (every installed model declared, no
stale declaration) lives in ``core/tests/test_data_transfer_policy.py``; these
are the DB-backed regressions for the route.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import grant
from extras.models import Bookmark
from organization.models import Role, Tenant

User = get_user_model()

EXPORT_URL_NAME = "object_export"


def export_url(app_label, model_name, template_id=0):
    return reverse(
        EXPORT_URL_NAME,
        kwargs={"app_label": app_label, "model_name": model_name, "template_id": template_id},
    )


class GenericExportPolicyTestCase(TestCase):
    """Shared fixtures: one tenant, a member holding the view permission, rows."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Export Tenant", slug="export-tenant")
        self.superuser = User.objects.create_superuser(
            username="export-superuser", password="pw", email="export-superuser@example.test"
        )
        self.member = User.objects.create_user(username="export-member", password="pw")
        self.role = Role.objects.create(
            tenant=self.tenant,
            name="Export Viewer",
            permissions=[
                "assets.view_asset",
                "assets.view_assettype",
                "extras.view_bookmark",
            ],
        )
        grant(self.member, self.tenant, self.role)

        self.status = StatusLabel.objects.create(name="Active", slug="active")
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model="Laptop", slug="laptop")
        self.asset = Asset.objects.create(
            name="Export Alpha",
            asset_tag="EXP-A",
            tenant=self.tenant,
            status=self.status,
            asset_type=self.asset_type,
        )

    def _login(self, user, tenant=None):
        self.client.force_login(user)
        if tenant is not None:
            session = self.client.session
            session["active_tenant_id"] = tenant.pk
            session.save()

    def _assert_all_paths_404(self, app_label, model_name):
        template_id = 0
        for query in (
            "?format=csv",
            "?format=yaml",
            "?format=csv&export_scope=all",
            "?format=yaml&export_scope=all",
            "?format=csv&export_scope=filtered",
            f"?format=csv&pk={self.asset.pk}",
        ):
            with self.subTest(query=query):
                response = self.client.get(export_url(app_label, model_name, template_id) + query)
                self.assertEqual(response.status_code, 404)
        response = self.client.get(export_url(app_label, model_name, template_id=999999))
        self.assertEqual(response.status_code, 404)


class UndeclaredModelExportTests(GenericExportPolicyTestCase):
    """A model without a declaration is denied — the inverted default."""

    def test_undeclared_model_is_denied_on_every_export_path(self):
        self._login(self.superuser, self.tenant)
        # An empty declaration table is exactly the "no policy exists" state: the
        # gate must fail closed instead of falling back to default-allow.
        with mock.patch.dict("core.data_transfer.DECLARATIONS", {}, clear=True):
            self._assert_all_paths_404("assets", "asset")


class DeniedModelExportTests(GenericExportPolicyTestCase):
    """A model the inventory denies is unreachable for every principal."""

    def test_denied_system_model_is_404_for_superuser(self):
        self._login(self.superuser, self.tenant)
        self._assert_all_paths_404("extras", "dashboard")

    def test_denied_model_is_404_for_a_member_holding_the_view_permission(self):
        from extras.models import Dashboard

        Dashboard.objects.create(user=self.member, name="Export Dashboard Probe", layout=[])
        role = Role.objects.create(
            tenant=self.tenant,
            name="Dashboard Viewer",
            permissions=["assets.view_asset", "extras.view_dashboard"],
        )
        grant(self.member, self.tenant, role)
        self._login(self.member, self.tenant)

        for query in ("?format=csv", "?format=yaml", "?format=csv&export_scope=all"):
            with self.subTest(query=query):
                response = self.client.get(export_url("extras", "dashboard") + query)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b"Export Dashboard Probe", response.content)


class OwnerScopedExportTests(GenericExportPolicyTestCase):
    """A personal model exports only the requesting user's rows."""

    def test_personal_export_excludes_other_users_rows(self):
        other = User.objects.create_user(username="export-other", password="pw")
        content_type = ContentType.objects.get_for_model(Asset)
        Bookmark.objects.create(user=self.member, model=content_type, object_id=self.asset.pk)
        Bookmark.objects.create(user=other, model=content_type, object_id=self.asset.pk + 5000)

        self._login(self.member, self.tenant)
        response = self.client.get(export_url("extras", "bookmark") + "?format=csv&export_scope=all")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(self.member.username, body)
        self.assertNotIn(other.username, body)
        rows = body.strip().splitlines()
        self.assertEqual(len(rows), 2, "header plus the member's own bookmark")

    def test_personal_export_ignores_a_foreign_pk_request(self):
        other = User.objects.create_user(username="export-other-pk", password="pw")
        content_type = ContentType.objects.get_for_model(Asset)
        Bookmark.objects.create(user=self.member, model=content_type, object_id=self.asset.pk)
        foreign = Bookmark.objects.create(user=other, model=content_type, object_id=self.asset.pk + 7000)

        self._login(self.member, self.tenant)
        url = export_url("extras", "bookmark")
        own_rows = self.client.get(url + "?format=csv&export_scope=all").content.decode("utf-8").strip().splitlines()
        self.assertEqual(len(own_rows), 2, "header plus the member's own bookmark")

        # The declared owner scope applies before the requested ids, so a foreign
        # row id resolves to an empty row set (header only) instead of leaking it.
        foreign_rows = self.client.get(url + f"?format=csv&pk={foreign.pk}")
        self.assertEqual(foreign_rows.status_code, 200)
        self.assertEqual(len(foreign_rows.content.decode("utf-8").strip().splitlines()), 1)


class UnresolvedScopeExportTests(GenericExportPolicyTestCase):
    """Without a resolved tenant scope the gate fails closed."""

    def test_member_without_any_membership_gets_404_not_unfiltered_rows(self):
        outsider = User.objects.create_user(username="export-outsider", password="pw")
        self._login(outsider)
        # The rows exist and the default manager would hand them over with no
        # scope bound; the permission gate — not the manager — is the boundary.
        self.assertEqual(Asset._base_manager.count(), 1)
        self._assert_all_paths_404("assets", "asset")
