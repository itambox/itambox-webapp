"""UI tests for the global Journal Entries list view (Monitoring › Activity).

Mirrors the Changelog list: tenant-scoped, permission-gated, searchable. The
list reuses the generic ObjectListView machinery, so coverage focuses on the
behaviour specific to journaling — tenant isolation, the comment search filter,
and the view permission gate.
"""
# Import the view module at the top so JournalEntryListView's class-level
# ``queryset = JournalEntry.objects...`` is evaluated now, at collection time,
# with NO active tenant — baking an *unscoped* base queryset (exactly as it is
# in production, where the URLconf imports at startup). If the module were first
# imported lazily during the first reverse()/request below — after
# client_login_to_tenant() has set a tenant — the base queryset would bake that
# tenant, and every later test under a different tenant would see an empty list.
import itambox.views.features  # noqa: F401

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetType, StatusLabel, Manufacturer
from core.tests.mixins import TenantTestMixin
from extras.models import JournalEntry

User = get_user_model()


class JournalEntryListViewTests(TenantTestMixin, TestCase):
    def setUp(self):
        # Tenant A (the acting user's tenant) + a foreign tenant B.
        self.setup_tenant_context(
            name="JE List A", slug="je-list-a",
            permissions=['extras.view_journalentry'],
        )
        from organization.models import Tenant
        self.tenant_b = Tenant.objects.create(name="JE List B", slug="je-list-b")

        self.mfr = Manufacturer.objects.create(name="JE-List-Mfr", slug="je-list-mfr")
        self.atype = AssetType.objects.create(manufacturer=self.mfr, model="JE-List-Model")
        self.status = StatusLabel.objects.create(
            name="JE-List-Ready", slug="je-list-ready", type=StatusLabel.TYPE_DEPLOYABLE,
        )

        self.asset_a = Asset.objects.create(
            name="JE List Asset A", asset_tag="JEL-A-1", asset_type=self.atype,
            status=self.status, tenant=self.tenant,
        )
        self.asset_b = Asset.objects.create(
            name="JE List Asset B", asset_tag="JEL-B-1", asset_type=self.atype,
            status=self.status, tenant=self.tenant_b,
        )

        # tenant is derived from the journaled object on save().
        self.entry_a = JournalEntry.objects.create(
            content_object=self.asset_a, user=self.tenant_user,
            comment="Alpha journal note marker",
        )
        self.entry_b = JournalEntry.objects.create(
            content_object=self.asset_b, user=self.tenant_admin,
            comment="Bravo journal note marker",
        )

    def _list_url(self, **params):
        # switch_tenant pins the request to this tenant via the middleware,
        # independent of any ambient context bled from a prior test.
        params.setdefault('switch_tenant', self.tenant.pk)
        return reverse('journalentry_list') + '?' + '&'.join(f'{k}={v}' for k, v in params.items())

    def test_list_scoped_to_active_tenant(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        resp = self.client.get(self._list_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alpha journal note marker")
        self.assertNotContains(resp, "Bravo journal note marker")

    def test_comment_search_filter(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        resp = self.client.get(self._list_url(q='Alpha'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alpha journal note marker")

        resp = self.client.get(self._list_url(q='no-such-comment'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Alpha journal note marker")

    def test_object_column_links_to_journaled_object(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        resp = self.client.get(self._list_url())
        self.assertContains(resp, self.asset_a.get_absolute_url())

    def test_view_requires_permission(self):
        # A member of the tenant without extras.view_journalentry is denied.
        denied_user = User.objects.create_user(username="je-denied", password="pw")
        self.client_login_to_tenant(denied_user, self.tenant, role_permissions=[])
        resp = self.client.get(self._list_url())
        self.assertIn(resp.status_code, (302, 403))
