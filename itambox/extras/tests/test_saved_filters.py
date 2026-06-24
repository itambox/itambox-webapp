"""Tenant-isolation + scoping tests for the SavedFilter feature (audit L7).

Covers the security-critical behaviour:
- get_visible_saved_filters excludes other tenants' filters and other members'
  private filters, includes global (tenant=None) and own/shared ones.
- the content_type gate (a filter for model A is not offered on model B's list).
- SavedFilterSaveView assigns tenant correctly and only superusers create global.
"""
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, RequestFactory

from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, TenantMembership, TenantRole
from extras.models import SavedFilter, Tag, CustomField
from extras.views import SavedFilterSaveView
from itambox.views.generic import ObjectListView

User = get_user_model()


class SavedFilterVisibilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        # Tenant A (+ self.tenant_user, self.tenant_admin) and a second member.
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")
        self.user_a2 = User.objects.create_user(
            username="user_a2", email="a2@example.com", password="password"
        )
        m = TenantMembership.objects.create(user=self.user_a2, tenant=self.tenant)
        m.roles.add(self.tenant_role)
        # Tenant B + a member.
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.user_b = User.objects.create_user(
            username="user_b", email="b@example.com", password="password"
        )

        self.ct_tag = ContentType.objects.get_for_model(Tag)
        self.ct_cf = ContentType.objects.get_for_model(CustomField)

        # Filters for the Tag list, varying tenant / shared / owner / content_type.
        self.sf_a_shared = SavedFilter.objects.create(
            name="A shared", content_type=self.ct_tag, tenant=self.tenant,
            shared=True, created_by=self.user_a2, parameters={"q": "x"},
        )
        self.sf_a_mine_private = SavedFilter.objects.create(
            name="A mine private", content_type=self.ct_tag, tenant=self.tenant,
            shared=False, created_by=self.tenant_user, parameters={"q": "y"},
        )
        self.sf_a_other_private = SavedFilter.objects.create(
            name="A other private", content_type=self.ct_tag, tenant=self.tenant,
            shared=False, created_by=self.user_a2, parameters={"q": "z"},
        )
        self.sf_b = SavedFilter.objects.create(
            name="B shared", content_type=self.ct_tag, tenant=self.tenant_b,
            shared=True, created_by=self.user_b, parameters={"q": "b"},
        )
        self.sf_global = SavedFilter.objects.create(
            name="Global", content_type=self.ct_tag, tenant=None,
            shared=True, created_by=self.tenant_admin, parameters={"q": "g"},
        )
        self.sf_a_other_model = SavedFilter.objects.create(
            name="A custom-field filter", content_type=self.ct_cf, tenant=self.tenant,
            shared=True, created_by=self.tenant_user, parameters={"q": "cf"},
        )

    def _visible_pks(self, user, model):
        view = ObjectListView()
        view.request = self.factory.get("/")
        view.request.user = user
        with self.tenant_context(self.tenant, self.tenant_membership):
            return set(view.get_visible_saved_filters(model).values_list("pk", flat=True))

    def test_visibility_excludes_other_tenant_and_others_private(self):
        visible = self._visible_pks(self.tenant_user, Tag)
        # Own private, tenant-shared, and global are visible.
        self.assertIn(self.sf_a_mine_private.pk, visible)
        self.assertIn(self.sf_a_shared.pk, visible)
        self.assertIn(self.sf_global.pk, visible)
        # Another tenant's filter and another member's private filter are NOT.
        self.assertNotIn(self.sf_b.pk, visible)
        self.assertNotIn(self.sf_a_other_private.pk, visible)

    def test_content_type_gate(self):
        # Querying the Tag list must not surface a CustomField-scoped filter.
        visible = self._visible_pks(self.tenant_user, Tag)
        self.assertNotIn(self.sf_a_other_model.pk, visible)
        # ...but it IS visible when listing the model it belongs to.
        visible_cf = self._visible_pks(self.tenant_user, CustomField)
        self.assertIn(self.sf_a_other_model.pk, visible_cf)

    def test_other_member_sees_shared_not_my_private(self):
        visible = self._visible_pks(self.user_a2, Tag)
        self.assertIn(self.sf_a_shared.pk, visible)        # shared by a2 -> a2 sees it
        self.assertIn(self.sf_a_other_private.pk, visible)  # a2's own private
        self.assertIn(self.sf_global.pk, visible)
        self.assertNotIn(self.sf_a_mine_private.pk, visible)  # tenant_user's private


class SavedFilterSaveViewTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.setup_tenant_context(name="Tenant A", slug="tenant-a")

    def _post(self, user, data):
        req = self.factory.post("/extras/saved-filters/save/", data)
        req.user = user
        with self.tenant_context(self.tenant, self.tenant_membership):
            SavedFilterSaveView().post(req)

    def test_non_superuser_cannot_create_global(self):
        # is_global is requested but the user is not a superuser -> scoped to the active tenant.
        # Filter fields arrive directly in POST (hx-include of the filter form), not as a blob.
        self._post(self.tenant_user, {
            "sf_name": "Scoped", "model": "extras.tag",
            "q": "foo", "page": "2", "sort": "name",
            "sf_shared": "on", "sf_is_global": "on",
        })
        sf = SavedFilter.all_objects.get(name="Scoped")
        self.assertEqual(sf.tenant_id, self.tenant.pk)
        self.assertEqual(sf.created_by_id, self.tenant_user.pk)
        # page/sort (chrome) stripped; the filter field q kept.
        self.assertEqual(sf.parameters, {"q": "foo"})
        self.assertEqual(sf.content_type, ContentType.objects.get_for_model(Tag))

    def test_empty_filter_fields_are_dropped(self):
        # An unfilled filter field posts an empty value; it must not be stored.
        self._post(self.tenant_user, {
            "sf_name": "Empties", "model": "extras.tag",
            "q": "", "status": "", "sf_shared": "on",
        })
        sf = SavedFilter.all_objects.get(name="Empties")
        self.assertEqual(sf.parameters, {})

    def test_superuser_creates_global_when_requested(self):
        self._post(self.tenant_admin, {
            "sf_name": "Global one", "model": "extras.tag",
            "q": "bar", "sf_shared": "on", "sf_is_global": "on",
        })
        sf = SavedFilter.all_objects.get(name="Global one")
        self.assertIsNone(sf.tenant_id)

    def test_superuser_without_global_flag_stays_tenant_scoped(self):
        self._post(self.tenant_admin, {
            "sf_name": "Admin scoped", "model": "extras.tag",
            "q": "baz", "sf_shared": "on",
        })
        sf = SavedFilter.all_objects.get(name="Admin scoped")
        self.assertEqual(sf.tenant_id, self.tenant.pk)
