"""
FIX-03 security tests:
  Part A — upload/journal write-paths require per-object permission
  Part B / Family 2b — bulk model_name allowlist (only tenant-scoped models)
  Family 10 — global search respects tenant scoping
  Family B6 — REST list+detail cross-tenant probes (inventory, licenses, subscriptions)
"""
import io
import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetType, Manufacturer, StatusLabel
from inventory.models import Accessory
from licenses.models import License
from core.tests.mixins import grant
from organization.models import Tenant, Membership, Role, Site
from software.models import Software
from subscriptions.models import Subscription, Provider
from users.models import Token

User = get_user_model()


def _make_role(tenant, name, perms):
    return Role.objects.create(tenant=tenant, name=name, permissions=perms)


def _login(client, user, tenant):
    client.force_login(user)
    s = client.session
    s["active_tenant_id"] = tenant.pk
    s.save()


# ---------------------------------------------------------------------------
# Part A — upload / journal views require change permission on the target
# ---------------------------------------------------------------------------

class UploadJournalPermTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="S", slug="s")
        self.tenant_a = Tenant.objects.create(name="TA", slug="ta")
        self.tenant_b = Tenant.objects.create(name="TB", slug="tb")

        # User with change_asset on tenant_a
        self.user_chg = User.objects.create_user("chg", password="pw")
        role_chg = _make_role(self.tenant_a, "changer", ["assets.change_asset"])
        m_chg = grant(self.user_chg, self.tenant_a, role_chg).membership

        # User with view-only on tenant_a (no change)
        self.user_view = User.objects.create_user("view", password="pw")
        role_view = _make_role(self.tenant_a, "viewer", ["assets.view_asset"])
        m_view = grant(self.user_view, self.tenant_a, role_view).membership

        mfr = Manufacturer.objects.create(name="Dell", slug="dell")
        at = AssetType.objects.create(manufacturer=mfr, model="XPS 13")
        sl = StatusLabel.objects.create(name="Ready", slug="ready", type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_a = Asset.objects.create(
            name="Asset A", asset_tag="TAGA", asset_type=at, status=sl, tenant=self.tenant_a
        )
        self.asset_b = Asset.objects.create(
            name="Asset B", asset_tag="TAGB", asset_type=at, status=sl, tenant=self.tenant_b
        )

    # --- JournalEntryCreateView ---

    def _journal_url(self, asset):
        return reverse(
            "journal_entry_add",
            kwargs={"app_label": "assets", "model_name": "asset", "object_id": asset.pk},
        )

    def test_journal_zero_perm_returns_404(self):
        _login(self.client, self.user_view, self.tenant_a)
        resp = self.client.post(self._journal_url(self.asset_a), {"comment": "hi"})
        self.assertEqual(resp.status_code, 404)

    def test_journal_cross_tenant_returns_404(self):
        _login(self.client, self.user_chg, self.tenant_a)
        resp = self.client.post(self._journal_url(self.asset_b), {"comment": "hi"})
        self.assertEqual(resp.status_code, 404)

    def test_journal_authorized_succeeds(self):
        _login(self.client, self.user_chg, self.tenant_a)
        resp = self.client.post(
            self._journal_url(self.asset_a),
            {"comment": "legit"},
            follow=True,
        )
        self.assertNotEqual(resp.status_code, 404)
        from extras.models import JournalEntry
        self.assertTrue(
            JournalEntry.objects.filter(object_id=self.asset_a.pk, comment="legit").exists()
        )

    # --- ImageAttachmentUploadView ---

    def _img_url(self, asset):
        return reverse(
            "image_attachment_upload",
            kwargs={"app_label": "assets", "model_name": "asset", "object_id": asset.pk},
        )

    def test_image_upload_zero_perm_returns_404(self):
        _login(self.client, self.user_view, self.tenant_a)
        img = io.BytesIO(b"GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;")
        img.name = "t.gif"
        resp = self.client.post(self._img_url(self.asset_a), {"image": img})
        self.assertEqual(resp.status_code, 404)

    def test_image_upload_cross_tenant_returns_404(self):
        _login(self.client, self.user_chg, self.tenant_a)
        img = io.BytesIO(b"GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;")
        img.name = "t.gif"
        resp = self.client.post(self._img_url(self.asset_b), {"image": img})
        self.assertEqual(resp.status_code, 404)

    # --- FileAttachmentUploadView ---

    def _file_url(self, asset):
        return reverse(
            "file_attachment_upload",
            kwargs={"app_label": "assets", "model_name": "asset", "object_id": asset.pk},
        )

    def test_file_upload_zero_perm_returns_404(self):
        _login(self.client, self.user_view, self.tenant_a)
        f = io.BytesIO(b"data")
        f.name = "doc.pdf"
        resp = self.client.post(self._file_url(self.asset_a), {"file": f})
        self.assertEqual(resp.status_code, 404)

    def test_file_upload_cross_tenant_returns_404(self):
        _login(self.client, self.user_chg, self.tenant_a)
        f = io.BytesIO(b"data")
        f.name = "doc.pdf"
        resp = self.client.post(self._file_url(self.asset_b), {"file": f})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Part B / Family 2b — _get_model allowlist (non-tenant model rejected)
# ---------------------------------------------------------------------------

class BulkModelNameAllowlistTests(TestCase):
    """POST model_name=auth.User to the generic bulk views must return 404.

    core/urls.py registers ObjectBulkDeleteView and ObjectBulkEditView directly
    (without a queryset) at 'bulk_delete' and 'bulk_edit'. Those paths rely on
    the POST model_name param to resolve the model — these are the vulnerable
    surfaces the allowlist fix targets.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="T", slug="t")
        self.user = User.objects.create_superuser("su", password="pw")
        _login(self.client, self.user, self.tenant)

    def test_bulk_delete_rejects_non_tenant_model(self):
        url = reverse("bulk_delete")
        resp = self.client.post(url, {"model_name": "auth.User", "pk": ["1"]})
        self.assertEqual(resp.status_code, 404)

    def test_bulk_edit_rejects_non_tenant_model(self):
        url = reverse("bulk_edit")
        resp = self.client.post(url, {"model_name": "auth.User", "pk": ["1"]})
        self.assertEqual(resp.status_code, 404)

    def test_bulk_delete_rejects_invalid_model_name(self):
        url = reverse("bulk_delete")
        resp = self.client.post(url, {"model_name": "nonexistent.Foo", "pk": ["1"]})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Family 10 — global search respects tenant scoping
# ---------------------------------------------------------------------------

class SearchTenantScopingTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="SearchA", slug="search-a")
        self.tenant_b = Tenant.objects.create(name="SearchB", slug="search-b")

        self.user_a = User.objects.create_user("srch_a", password="pw")
        role_a = _make_role(self.tenant_a, "viewer", ["assets.view_asset"])
        m_a = grant(self.user_a, self.tenant_a, role_a).membership

        mfr = Manufacturer.objects.create(name="HP", slug="hp")
        at = AssetType.objects.create(manufacturer=mfr, model="EliteBook")
        sl = StatusLabel.objects.create(name="Active", slug="active-srch", type=StatusLabel.TYPE_DEPLOYABLE)

        # Unique token that only appears in tenant_b's asset
        self.secret_tag = "XZQHIDDENB999"
        Asset.objects.create(
            name="Hidden B Asset", asset_tag=self.secret_tag,
            asset_type=at, status=sl, tenant=self.tenant_b
        )
        Asset.objects.create(
            name="Visible A Asset", asset_tag="XZQVISIBLEA111",
            asset_type=at, status=sl, tenant=self.tenant_a
        )

    def test_search_does_not_leak_cross_tenant_data(self):
        _login(self.client, self.user_a, self.tenant_a)
        resp = self.client.get(reverse("search") + f"?q={self.secret_tag}")
        self.assertEqual(resp.status_code, 200)
        # The query term appears in the page title/form, so we can't assertNotContains on it.
        # Instead verify the search returned zero results (not the asset from tenant B).
        results = resp.context.get("results", {})
        for model, data in results.items():
            for obj in data.get("queryset", []):
                self.assertNotEqual(
                    getattr(obj, "asset_tag", None),
                    self.secret_tag,
                    f"Tenant B asset leaked into tenant A search: {obj}",
                )

    def test_search_returns_own_tenant_data(self):
        _login(self.client, self.user_a, self.tenant_a)
        resp = self.client.get(reverse("search") + "?q=XZQVISIBLEA111")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "XZQVISIBLEA111")


# ---------------------------------------------------------------------------
# Family B6 — REST list + detail cross-tenant (inventory, licenses, subscriptions)
# ---------------------------------------------------------------------------

class RESTCrossTenantTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="HQ", slug="hq")
        self.tenant_a = Tenant.objects.create(name="RestA", slug="rest-a")
        self.tenant_b = Tenant.objects.create(name="RestB", slug="rest-b")

        # User A (member of tenant_a only)
        self.user_a = User.objects.create_user("rest_a", password="pw")
        role_a = _make_role(
            self.tenant_a, "AllPerms",
            [
                "inventory.view_accessory",
                "licenses.view_license",
                "subscriptions.view_subscription",
            ],
        )
        m_a = grant(self.user_a, self.tenant_a, role_a).membership
        self.token_a = Token.objects.create(user=self.user_a)

        # Shared metadata
        self.mfr = Manufacturer.objects.create(name="Cisco", slug="cisco")
        sl = StatusLabel.objects.create(name="In-Use", slug="in-use-rest", type=StatusLabel.TYPE_DEPLOYED)
        at = AssetType.objects.create(manufacturer=self.mfr, model="Switch")
        sw = Software.objects.create(name="Office", manufacturer=self.mfr)

        # Tenant A objects
        self.acc_a = Accessory.objects.create(
            name="Acc A", slug="acc-a", manufacturer=self.mfr, tenant=self.tenant_a
        )
        self.lic_a = License.objects.create(
            name="Lic A", software=sw, tenant=self.tenant_a, seats=5
        )
        provider = Provider.objects.create(name="AWS", slug="aws")
        self.sub_a = Subscription.objects.create(
            name="Sub A", provider=provider, tenant=self.tenant_a
        )

        # Tenant B objects
        self.acc_b = Accessory.objects.create(
            name="Acc B", slug="acc-b", manufacturer=self.mfr, tenant=self.tenant_b
        )
        self.lic_b = License.objects.create(
            name="Lic B", software=sw, tenant=self.tenant_b, seats=10
        )
        self.sub_b = Subscription.objects.create(
            name="Sub B", provider=provider, tenant=self.tenant_b
        )

    def _headers(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token_a.key}"}

    def _switch_qs(self):
        return f"?switch_tenant={self.tenant_a.pk}"

    # Accessory
    def test_accessory_list_excludes_tenant_b(self):
        url = reverse("api:inventory_api:accessory-list") + self._switch_qs()
        resp = self.client.get(url, **self._headers())
        self.assertEqual(resp.status_code, 200)
        ids = [str(r["id"]) for r in resp.json().get("results", resp.json())]
        self.assertNotIn(str(self.acc_b.pk), ids)

    def test_accessory_detail_tenant_b_returns_404(self):
        url = reverse("api:inventory_api:accessory-detail", kwargs={"pk": self.acc_b.pk})
        url += self._switch_qs()
        resp = self.client.get(url, **self._headers())
        self.assertEqual(resp.status_code, 404)

    # License
    def test_license_list_excludes_tenant_b(self):
        url = reverse("api:licenses_api:license-list") + self._switch_qs()
        resp = self.client.get(url, **self._headers())
        self.assertEqual(resp.status_code, 200)
        ids = [str(r["id"]) for r in resp.json().get("results", resp.json())]
        self.assertNotIn(str(self.lic_b.pk), ids)

    def test_license_detail_tenant_b_returns_404(self):
        url = reverse("api:licenses_api:license-detail", kwargs={"pk": self.lic_b.pk})
        url += self._switch_qs()
        resp = self.client.get(url, **self._headers())
        self.assertEqual(resp.status_code, 404)

    # Subscription
    def test_subscription_list_excludes_tenant_b(self):
        url = reverse("api:subscriptions_api:subscription-list") + self._switch_qs()
        resp = self.client.get(url, **self._headers())
        self.assertEqual(resp.status_code, 200)
        ids = [str(r["id"]) for r in resp.json().get("results", resp.json())]
        self.assertNotIn(str(self.sub_b.pk), ids)

    def test_subscription_detail_tenant_b_returns_404(self):
        url = reverse("api:subscriptions_api:subscription-detail", kwargs={"pk": self.sub_b.pk})
        url += self._switch_qs()
        resp = self.client.get(url, **self._headers())
        self.assertEqual(resp.status_code, 404)
