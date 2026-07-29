"""Characterization tests for the generic CBV foundations (issue #82).

These tests pin down the *existing* behaviour of the generic view layer before
any component is extracted out of it, so the extraction can be proven to be
behaviour-preserving rather than merely "still green somewhere else":

* ``ObjectDetailView``  — context construction, permission-driven action URLs,
  related-object counts (tenant-scoped, soft-delete aware), HTMX vs non-HTMX.
* ``ObjectListView``    — context construction, HTMX partial economics
  (export/label catalogues skipped, saved filters kept), tenant scoping.
* ``GenericTransactionView`` / ``SimplePostView`` — the fail-closed
  authorization contract, the ``permission_required = ()`` self-authorization
  opt-out, object-scoped permission checks, cross-tenant 404s, and the
  HTMX/non-HTMX response shapes.

Everything asserted here is behaviour that already exists on ``main``; the file
is the safety net for the refactor, so please treat a failure as a regression in
the view layer rather than a test that needs adjusting.
"""

import json
from unittest.mock import patch

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse

from assets.filters import StatusLabelFilterSet
from assets.forms.filter_forms import AssetFilterForm
from assets.models import Asset, AssetType, Manufacturer, StatusLabel
from assets.views.asset_views import AssetListView
from core.tests.mixins import TenantTestMixin
from extras.models import SavedFilter
from itambox.views.generic.detail import ObjectDetailView
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView
from organization.models import Tenant

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


class _CatalogFixtureMixin:
    """Create the AssetType/Manufacturer/StatusLabel catalogue rows the asset
    fixtures below need. Slugs carry a '-gvf' suffix so they never collide with
    the other view suites that build their own catalogue."""

    def setup_catalog(self):
        self.manufacturer = Manufacturer.objects.create(name="Foundations Mfr GVF", slug="foundations-mfr-gvf")
        self.asset_type = AssetType.objects.create(
            model="Foundations Type GVF",
            slug="foundations-type-gvf",
            manufacturer=self.manufacturer,
        )
        self.status, _created = StatusLabel.objects.get_or_create(
            slug="available",
            defaults={"name": "Available", "type": "deployable", "color": "28a745"},
        )

    def make_asset(self, name, tag, tenant, asset_type=None):
        return Asset.objects.create(
            name=name,
            asset_tag=tag,
            tenant=tenant,
            status=self.status,
            asset_type=asset_type if asset_type is not None else self.asset_type,
        )


# ---------------------------------------------------------------------------
# 1. ObjectDetailView — context construction
# ---------------------------------------------------------------------------


class DetailContextCharacterizationTests(_CatalogFixtureMixin, TenantTestMixin, TestCase):
    """The keys ``generic/object_detail.html`` and every app detail template read
    out of the context, and the permission inputs that decide them."""

    def setUp(self):
        self.setup_tenant_context(
            name="Detail Ctx Tenant GVF",
            slug="detail-ctx-tenant-gvf",
            permissions=["assets.view_asset", "assets.change_asset", "assets.delete_asset"],
        )
        self.setup_catalog()
        self.asset = self.make_asset("Detail Asset GVF", "DET-001-GVF", self.tenant)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        self.url = reverse("assets:asset_detail", kwargs={"pk": self.asset.pk})

    def test_detail_context_exposes_the_documented_keys(self):
        """Every key existing detail templates depend on is present."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        context = response.context

        for key in (
            "model",
            "layout",
            "can_change",
            "can_delete",
            "edit_url",
            "delete_url",
            "clone_url",
            "title",
            "breadcrumbs",
            "page_actions",
            "action_urls",
            "content_template_name",
            "attachment_app_label",
            "attachment_model_name",
            "related_objects_list",
            "help_url",
        ):
            self.assertIn(key, context, f"detail context lost the {key!r} key")

        self.assertIs(context["model"], Asset)
        self.assertEqual(context["title"], str(self.asset))
        self.assertEqual(context["attachment_app_label"], "assets")
        self.assertEqual(context["attachment_model_name"], "asset")

    def test_page_actions_and_action_urls_mirror_the_resolved_urls(self):
        """``page_actions``/``action_urls`` are derived views onto edit/delete/clone."""
        response = self.client.get(self.url)
        context = response.context

        self.assertEqual(
            context["page_actions"],
            {"edit_url": context["edit_url"], "delete_url": context["delete_url"]},
        )
        self.assertEqual(
            context["action_urls"],
            {
                "edit": context["edit_url"],
                "delete": context["delete_url"],
                "clone": context["clone_url"],
            },
        )

    def test_change_and_delete_permissions_drive_the_action_urls(self):
        """Holding change/delete yields resolvable edit and delete URLs."""
        response = self.client.get(self.url)
        context = response.context

        self.assertTrue(context["can_change"])
        self.assertTrue(context["can_delete"])
        self.assertEqual(
            context["edit_url"],
            reverse("assets:asset_update", kwargs={"pk": self.asset.pk}),
        )
        self.assertEqual(
            context["delete_url"],
            reverse("assets:asset_delete", kwargs={"pk": self.asset.pk}),
        )

    def test_view_only_user_gets_no_edit_or_delete_url(self):
        """Without change/delete the action URLs are None, not merely hidden."""
        self.tenant_role.permissions = ["assets.view_asset"]
        self.tenant_role.save(update_fields=["permissions"])

        response = self.client.get(self.url)
        context = response.context

        self.assertFalse(context["can_change"])
        self.assertFalse(context["can_delete"])
        self.assertIsNone(context["edit_url"])
        self.assertIsNone(context["delete_url"])
        self.assertEqual(context["page_actions"], {"edit_url": None, "delete_url": None})

    def test_breadcrumbs_end_at_the_object(self):
        response = self.client.get(self.url)
        breadcrumbs = response.context["breadcrumbs"]

        self.assertEqual(breadcrumbs[0][0], reverse("dashboard"))
        self.assertEqual(breadcrumbs[-1], (None, str(self.asset)))

    def test_missing_view_permission_is_denied(self):
        self.tenant_role.permissions = []
        self.tenant_role.save(update_fields=["permissions"])

        response = self.client.get(self.url)
        # Authenticated + unauthorized -> PermissionDenied -> 403 (not a login redirect).
        self.assertEqual(response.status_code, 403)


# Detail-view tenant scoping (foreign pk -> 404, own pk -> 200) is already
# covered by test_generic_cbv.py::TenantScopingTests; the scoping cases below are
# the ones it does not reach — related-object counts, lists, and action views.


class DetailHtmxCharacterizationTests(_CatalogFixtureMixin, TenantTestMixin, TestCase):
    """``BaseHTMXView`` picks the response shape from the HTMX request flavour."""

    def setUp(self):
        self.setup_tenant_context(
            name="Detail Htmx Tenant GVF",
            slug="detail-htmx-tenant-gvf",
            permissions=["assets.view_asset"],
        )
        self.setup_catalog()
        self.asset = self.make_asset("Htmx Detail Asset GVF", "HTMX-DET-GVF", self.tenant)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        self.url = reverse("assets:asset_detail", kwargs={"pk": self.asset.pk})

    def test_plain_request_renders_the_full_page(self):
        """No HTMX headers: the view leaves ``base_template`` at whatever the
        context processor supplied rather than overriding it with the HTMX base."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.context["base_template"], "base_htmx.html")

    def test_boosted_request_swaps_in_the_htmx_base_template(self):
        """A boosted navigation renders the page against ``base_htmx.html``."""
        response = self.client.get(self.url, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["base_template"], "base_htmx.html")

    def test_detail_view_has_no_content_partial_so_htmx_still_renders_the_page(self):
        """``ObjectDetailView`` declares no ``content_partial_name``: a bare HTMX
        GET therefore still renders the detail template (200), not a fragment."""
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn("related_objects_list", response.context)


# ---------------------------------------------------------------------------
# 2. ObjectDetailView — related-object counts
# ---------------------------------------------------------------------------


class RelatedObjectCountCharacterizationTests(_CatalogFixtureMixin, TenantTestMixin, TestCase):
    """The "Related Objects" sidebar counts every reverse relation through the
    related model's *default* manager, so they inherit tenant scoping and
    soft-delete filtering. Relations with a zero count are dropped entirely and
    the surviving rows are sorted by label."""

    def setUp(self):
        self.setup_tenant_context(
            name="RelCount Tenant A GVF",
            slug="relcount-tenant-a-gvf",
            permissions=["assets.view_asset"],
        )
        self.tenant_b = Tenant.objects.create(name="RelCount Tenant B GVF", slug="relcount-tenant-b-gvf")
        self.setup_catalog()

    def _build(self, obj):
        """Invoke the detail view's related-object builder outside of a request."""
        return ObjectDetailView()._build_related_objects_list(obj)

    def _assets_row(self, rows):
        matches = [row for row in rows if row["label"] == "Assets"]
        self.assertEqual(len(matches), 1, f"expected exactly one 'Assets' row, got {rows}")
        return matches[0]

    def test_counts_are_scoped_to_the_active_tenant(self):
        """Two tenants' assets share one AssetType; only the active tenant counts."""
        self.make_asset("Rel A1 GVF", "REL-A1-GVF", self.tenant)
        self.make_asset("Rel A2 GVF", "REL-A2-GVF", self.tenant)
        self.make_asset("Rel B1 GVF", "REL-B1-GVF", self.tenant_b)

        with self.tenant_context(self.tenant, self.tenant_membership):
            rows = self._build(self.asset_type)

        self.assertEqual(self._assets_row(rows)["count"], 2)

    def test_counts_exclude_soft_deleted_rows(self):
        asset_1 = self.make_asset("Rel Soft 1 GVF", "REL-S1-GVF", self.tenant)
        self.make_asset("Rel Soft 2 GVF", "REL-S2-GVF", self.tenant)
        asset_1.delete()

        with self.tenant_context(self.tenant, self.tenant_membership):
            rows = self._build(self.asset_type)

        self.assertEqual(self._assets_row(rows)["count"], 1)

    def test_zero_count_relations_are_omitted(self):
        """An AssetType with no assets contributes no 'Assets' row at all."""
        with self.tenant_context(self.tenant, self.tenant_membership):
            rows = self._build(self.asset_type)

        self.assertEqual([row for row in rows if row["label"] == "Assets"], [])
        self.assertTrue(all(row["count"] > 0 for row in rows))

    def test_row_shape_and_filter_url(self):
        """Each row is {label, count, url}; the URL deep-links the related list
        filtered by this object's slug (pk when the model has no slug)."""
        self.make_asset("Rel URL GVF", "REL-URL-GVF", self.tenant)

        with self.tenant_context(self.tenant, self.tenant_membership):
            row = self._assets_row(self._build(self.asset_type))

        self.assertEqual(set(row), {"label", "count", "url"})
        self.assertEqual(
            row["url"],
            f"{reverse('assets:asset_list')}?asset_type={self.asset_type.slug}",
        )

    def test_disable_flag_short_circuits_the_whole_list(self):
        """``disable_related_objects_list`` is the opt-out escape hatch: the
        context key stays present but empty, and no counting query runs."""
        self.make_asset("Rel Disabled GVF", "REL-DIS-GVF", self.tenant)
        request = RequestFactory().get("/x/")
        request.user = self.tenant_user

        class _DisabledDetailView(ObjectDetailView):
            queryset = AssetType.objects.all()
            disable_related_objects_list = True

            def _build_related_objects_list(self, obj):
                raise AssertionError("related objects were built despite the opt-out")

        view = _DisabledDetailView()
        view.request = request
        view.kwargs = {"pk": self.asset_type.pk}
        view.object = self.asset_type

        with self.tenant_context(self.tenant, self.tenant_membership):
            context = view.get_context_data(object=self.asset_type)

        self.assertEqual(context["related_objects_list"], [])

    def test_default_keeps_related_objects_enabled(self):
        """The opt-out is opt-in: every existing detail view still counts."""
        self.assertFalse(ObjectDetailView.disable_related_objects_list)

    def test_detail_response_carries_the_related_objects_list(self):
        """End to end: the key reaches the template context of a real detail view."""
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        asset = self.make_asset("Rel Ctx GVF", "REL-CTX-GVF", self.tenant)

        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": asset.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["related_objects_list"], list)


# ---------------------------------------------------------------------------
# 3. ObjectListView — context construction and HTMX behaviour
# ---------------------------------------------------------------------------


class ListContextCharacterizationTests(_CatalogFixtureMixin, TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            name="List Ctx Tenant GVF",
            slug="list-ctx-tenant-gvf",
            permissions=["assets.view_asset", "assets.add_asset"],
        )
        self.setup_catalog()
        self.asset = self.make_asset("List Asset GVF", "LIST-001-GVF", self.tenant)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        self.url = reverse("assets:asset_list")

    def test_list_context_exposes_the_documented_keys(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        context = response.context

        for key in (
            "table",
            "filter_form",
            "model",
            "verbose_name_plural",
            "model_name_str",
            "table_config_key",
            "app_label",
            "model_name",
            "object_type",
            "title",
            "export_templates",
            "label_templates",
            "saved_filters",
            "active_saved_filter_id",
            "create_url_name",
            "can_export",
            "import_url",
            "bulk_delete_url",
            "bulk_edit_url",
            "can_add",
            "action_buttons",
            "has_soft_delete",
            "is_deleted_view",
            "breadcrumbs",
            "help_url",
            "is_beta_module",
        ):
            self.assertIn(key, context, f"list context lost the {key!r} key")

    def test_model_identity_keys(self):
        response = self.client.get(self.url)
        context = response.context

        self.assertIs(context["model"], Asset)
        self.assertEqual(context["app_label"], "assets")
        self.assertEqual(context["model_name"], "asset")
        self.assertEqual(context["model_name_str"], "assets.asset")
        self.assertEqual(context["object_type"], Asset._meta.verbose_name)
        self.assertEqual(context["verbose_name_plural"], Asset._meta.verbose_name_plural)

    def test_table_config_key_pairs_app_label_with_table_class(self):
        response = self.client.get(self.url)
        context = response.context

        self.assertEqual(
            context["table_config_key"],
            f"assets.{context['table'].__class__.__name__}",
        )

    def test_add_permission_drives_can_add(self):
        response = self.client.get(self.url)
        self.assertTrue(response.context["can_add"])

        self.tenant_role.permissions = ["assets.view_asset"]
        self.tenant_role.save(update_fields=["permissions"])

        response = self.client.get(self.url)
        self.assertFalse(response.context["can_add"])

    def test_active_saved_filter_id_is_none_without_a_filter_param(self):
        response = self.client.get(self.url)
        self.assertIsNone(response.context["active_saved_filter_id"])

    def test_visible_saved_filter_parameters_replace_the_query_string(self):
        saved_filter = SavedFilter.objects.create(
            name="Asset name filter GVF",
            content_type=ContentType.objects.get_for_model(Asset),
            tenant=self.tenant,
            shared=False,
            created_by=self.tenant_user,
            parameters={"q": "List Asset GVF"},
        )

        response = self.client.get(self.url, {"filter": saved_filter.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_saved_filter_id"], saved_filter.pk)
        self.assertEqual({row.pk for row in response.context["table"].data.data}, {self.asset.pk})

    def test_invalid_saved_filter_pk_falls_back_to_the_request_query(self):
        response = self.client.get(self.url, {"filter": "not-a-pk"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["active_saved_filter_id"])

    def test_invisible_saved_filter_falls_back_to_the_request_query(self):
        foreign_tenant = Tenant.objects.create(name="Filter Scope B GVF", slug="filter-scope-b-gvf")
        saved_filter = SavedFilter.objects.create(
            name="Foreign asset filter GVF",
            content_type=ContentType.objects.get_for_model(Asset),
            tenant=foreign_tenant,
            shared=True,
            created_by=self.tenant_user,
            parameters={"q": "does-not-matter"},
        )

        response = self.client.get(self.url, {"filter": saved_filter.pk})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["active_saved_filter_id"])

    def test_invalid_model_choice_filter_fails_closed_and_preserves_errors(self):
        response = self.client.get(self.url, {"status": "999999999"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertTrue(response.context["filter_form"].is_bound)
        self.assertIn("status", response.context["filter_form"].errors)

    def test_invalid_saved_filter_fails_closed_and_binds_its_errors(self):
        saved_filter = SavedFilter.objects.create(
            name="Invalid asset status GVF",
            content_type=ContentType.objects.get_for_model(Asset),
            tenant=self.tenant,
            shared=False,
            created_by=self.tenant_user,
            parameters={"status": "999999999"},
        )

        response = self.client.get(self.url, {"filter": saved_filter.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertEqual(response.context["active_saved_filter_id"], saved_filter.pk)
        self.assertEqual(response.context["filter_form"].data.get("status"), "999999999")
        self.assertIn("status", response.context["filter_form"].errors)

    def test_invalid_model_choice_filter_fails_closed_for_htmx(self):
        response = self.client.get(self.url, {"status": "999999999"}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertIn("status", response.context["filter_form"].errors)
        self.assertIn("htmx/list_page_wrapper.html", [template.name for template in response.templates])

    def test_mixed_valid_and_invalid_filters_fail_closed(self):
        response = self.client.get(
            self.url,
            {"q": self.asset.name, "status": "999999999"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertEqual(response.context["filter_form"].data.get("q"), self.asset.name)
        self.assertIn("status", response.context["filter_form"].errors)

    def test_valid_filter_still_returns_matching_rows(self):
        response = self.client.get(self.url, {"status": self.status.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual({row.pk for row in response.context["table"].data.data}, {self.asset.pk})
        self.assertEqual(response.context["filter_form"].errors, {})

    def test_valid_filter_with_no_matches_remains_empty_without_errors(self):
        response = self.client.get(self.url, {"q": "definitely-no-match-gvf"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertEqual(response.context["filter_form"].errors, {})

    def test_invalid_choice_filter_fails_closed_on_a_concrete_list_view(self):
        self.tenant_role.permissions = ["assets.view_asset", "assets.view_statuslabel"]
        self.tenant_role.save(update_fields=["permissions"])

        response = self.client.get(reverse("assets:statuslabel_list"), {"type": "not-a-status-type"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertIn("type", response.context["filter_form"].errors)

    def test_configured_display_form_validation_also_fails_closed(self):
        instances = []

        class RejectingAssetFilterForm(AssetFilterForm):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                instances.append(self)

            def clean_q(self):
                raise forms.ValidationError("Rejected only by the configured display form.")

        with patch.object(AssetListView, "filterset_form", RejectingAssetFilterForm):
            response = self.client.get(self.url, {"q": self.asset.name})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertEqual(len(instances), 1)
        self.assertIs(response.context["filter_form"], instances[0])
        self.assertIn("q", response.context["filter_form"].errors)

    def test_filterset_errors_are_preserved_on_a_divergent_display_form(self):
        class LenientAssetFilterForm(AssetFilterForm):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fields["status"] = forms.CharField(required=False)

        with patch.object(AssetListView, "filterset_form", LenientAssetFilterForm):
            response = self.client.get(self.url, {"status": "999999999"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["table"].data.data), [])
        self.assertIn("status", response.context["filter_form"].errors)

    def test_custom_field_filtering_cannot_reopen_an_invalid_standard_filter(self):
        self.asset.custom_field_data = {"hostname": "list-asset-gvf"}
        self.asset.save(update_fields=["custom_field_data"])
        invalid_params = {"status": "999999999", "cf_hostname": "list-asset-gvf"}
        saved_filter = SavedFilter.objects.create(
            name="Invalid status with custom field GVF",
            content_type=ContentType.objects.get_for_model(Asset),
            tenant=self.tenant,
            shared=False,
            created_by=self.tenant_user,
            parameters=invalid_params,
        )
        cases = (
            (invalid_params, {}),
            (invalid_params, {"HTTP_HX_REQUEST": "true"}),
            ({"filter": saved_filter.pk}, {}),
        )

        for params, extra in cases:
            with self.subTest(params=params, extra=extra):
                response = self.client.get(self.url, params, **extra)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context["table"].data.data), [])
                self.assertIn("status", response.context["filter_form"].errors)

    def test_mismatched_filterset_form_configuration_fails_loud(self):
        class MismatchedAssetFilterForm(AssetFilterForm):
            filterset_class = StatusLabelFilterSet

        with patch.object(AssetListView, "filterset_form", MismatchedAssetFilterForm):
            with self.assertRaisesRegex(
                ImproperlyConfigured,
                "AssetListView.filterset_form must use AssetFilterSet as its filterset_class",
            ):
                self.client.get(self.url)

    def test_list_is_scoped_to_the_active_tenant(self):
        tenant_b = Tenant.objects.create(name="List Scope B GVF", slug="list-scope-b-gvf")
        self.make_asset("List Foreign GVF", "LIST-FOREIGN-GVF", tenant_b)

        response = self.client.get(self.url)

        pks = {row.pk for row in response.context["table"].data.data}
        self.assertEqual(pks, {self.asset.pk})

    def test_missing_view_permission_is_denied(self):
        self.tenant_role.permissions = []
        self.tenant_role.save(update_fields=["permissions"])

        response = self.client.get(self.url)
        # Authenticated + unauthorized -> PermissionDenied -> 403 (not a login redirect).
        self.assertEqual(response.status_code, 403)


class ListHtmxCharacterizationTests(_CatalogFixtureMixin, TenantTestMixin, TestCase):
    """The HTMX partial path deliberately skips the export/label catalogue
    queries (they only render on the full page) but must keep populating the
    saved-filter list, which IS re-rendered by the offcanvas OOB swap."""

    def setUp(self):
        self.setup_tenant_context(
            name="List Htmx Tenant GVF",
            slug="list-htmx-tenant-gvf",
            permissions=["assets.view_asset"],
        )
        self.setup_catalog()
        self.make_asset("List Htmx Asset GVF", "LIST-HTMX-GVF", self.tenant)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        self.url = reverse("assets:asset_list")

    def test_partial_request_renders_the_content_partial(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        template_names = [t.name for t in response.templates]
        self.assertIn("htmx/list_page_wrapper.html", template_names)
        self.assertNotIn("base.html", template_names)

    def test_partial_request_skips_export_and_label_catalogues(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.context["export_templates"], [])
        self.assertEqual(response.context["label_templates"], [])

    def test_partial_request_still_populates_saved_filters(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertIsInstance(response.context["saved_filters"], list)

    def test_full_page_request_does_not_render_the_content_partial(self):
        response = self.client.get(self.url)

        template_names = [t.name for t in response.templates]
        self.assertNotIn("htmx/list_page_wrapper.html", template_names)

    def test_boosted_request_uses_the_htmx_base_template(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["base_template"], "base_htmx.html")
        template_names = [t.name for t in response.templates]
        self.assertNotIn("htmx/list_page_wrapper.html", template_names)


# ---------------------------------------------------------------------------
# 4. Secured action views — authorization contract
# ---------------------------------------------------------------------------


class _AssetNameForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = ("name",)


def _rename_asset(asset, user=None, request=None, name=None, **kwargs):
    asset.name = name
    asset.save(update_fields=["name"])
    return {"renamed": name}


class _RenameAssetView(GenericTransactionView):
    permission_required = ("assets.change_asset",)
    queryset = Asset.objects.all()
    model_form = _AssetNameForm
    service_callable = _rename_asset
    success_message = "Renamed."


class _TouchAssetView(SimplePostView):
    permission_required = ("assets.change_asset",)
    queryset = Asset.objects.all()

    def perform_action(self, obj, request):
        return {"message": "Touched."}


class _SelfAuthorizedAssetView(SimplePostView):
    """Mirrors the request-workflow views: no declarative permission, the
    per-object rule lives in ``perform_action``."""

    permission_required = ()
    queryset = Asset.objects.all()

    def perform_action(self, obj, request):
        if obj.name != "OWNED":
            raise PermissionDenied("not yours")
        return {"message": "Self-authorized."}


class _RejectingAssetView(SimplePostView):
    permission_required = ("assets.change_asset",)
    queryset = Asset.objects.all()

    def perform_action(self, obj, request):
        raise ValidationError("nope")


class _ActionViewTestBase(_CatalogFixtureMixin, TenantTestMixin, TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.setup_tenant_context(
            name=self.tenant_name,
            slug=self.tenant_slug,
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        self.setup_catalog()
        self.asset = self.make_asset("Action Asset GVF", f"ACT-{self.tenant_slug}", self.tenant)

    def make_request(self, method="post", data=None, htmx=False, user=None):
        request = getattr(self.factory, method)("/action/", data or {})
        request.user = user or self.tenant_user
        request.htmx = htmx
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request


class SecuredActionAuthorizationTests(_ActionViewTestBase):
    """The declarative contract itself (fail-closed, ``()`` opt-out, string
    normalisation) is unit-tested in test_generic_view_authorization.py; these
    cases drive it through a real request against a tenant-scoped object."""

    tenant_name = "Action Authz Tenant GVF"
    tenant_slug = "action-authz-gvf"

    def test_self_authorizing_view_runs_its_own_object_check(self):
        """``permission_required = ()`` skips the declarative gate but the action's
        own check still denies — the opt-out is not an authorization bypass."""
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=False)
            with self.assertRaises(PermissionDenied):
                _SelfAuthorizedAssetView.as_view()(request, pk=self.asset.pk)

    def test_self_authorizing_view_allows_when_its_own_check_passes(self):
        self.asset.name = "OWNED"
        self.asset.save(update_fields=["name"])

        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=True)
            response = _SelfAuthorizedAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            json.loads(response["HX-Trigger"])["showMessage"]["message"],
            "Self-authorized.",
        )

    def test_declarative_permission_is_enforced_per_object(self):
        """Dropping ``assets.change_asset`` denies the action for this object.

        ``AccessMixin.handle_no_permission()`` raises ``PermissionDenied`` for an
        authenticated user (the 403 page is produced later by Django's exception
        middleware, which ``RequestFactory`` does not run)."""
        self.tenant_role.permissions = ["assets.view_asset"]
        self.tenant_role.save(update_fields=["permissions"])

        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=False)
            with self.assertRaises(PermissionDenied):
                _TouchAssetView.as_view()(request, pk=self.asset.pk)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.name, "Action Asset GVF")

    def test_foreign_tenant_object_raises_404_for_authenticated_users(self):
        """The tenant boundary answers 404, not 403 — the pk's existence in
        another tenant must not leak."""
        tenant_b = Tenant.objects.create(name="Action Foreign GVF", slug="action-foreign-gvf")
        foreign = self.make_asset("Foreign Action GVF", "ACT-FOREIGN-GVF", tenant_b)

        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=False)
            with self.assertRaises(Http404):
                _TouchAssetView.as_view()(request, pk=foreign.pk)

    def test_get_object_is_cached_within_one_request(self):
        """``get_object`` is called from has_permission, form kwargs, the action
        and the context; it must hit the database once."""
        with self.tenant_context(self.tenant, self.tenant_membership):
            view = _TouchAssetView()
            view.request = self.make_request()
            view.kwargs = {"pk": self.asset.pk}
            first = view.get_object()
            with self.assertNumQueries(0):
                second = view.get_object()

        self.assertIs(first, second)


class SecuredActionResponseShapeTests(_ActionViewTestBase):
    """The HTMX contract: 204 + an ``HX-Trigger`` JSON payload carrying
    ``closeModalEvent``, the view's refresh trigger and a ``showMessage`` toast.
    Plain HTTP callers get a message plus a redirect instead."""

    tenant_name = "Action Shape Tenant GVF"
    tenant_slug = "action-shape-gvf"

    def test_simple_post_htmx_success_payload(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=True)
            response = _TouchAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 204)
        trigger = json.loads(response["HX-Trigger"])
        self.assertIn("closeModalEvent", trigger)
        self.assertIn("tableRefreshRequired", trigger)
        self.assertEqual(trigger["showMessage"], {"message": "Touched.", "level": "success"})

    def test_simple_post_non_htmx_success_redirects(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=False)
            response = _TouchAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.asset.get_absolute_url())

    def test_simple_post_htmx_validation_error_is_a_danger_toast(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=True)
            response = _RejectingAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 204)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["showMessage"]["level"], "danger")
        self.assertIn("nope", trigger["showMessage"]["message"])
        self.assertNotIn("tableRefreshRequired", trigger)

    def test_simple_post_non_htmx_validation_error_redirects_with_a_message(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=False)
            response = _RejectingAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.asset.get_absolute_url())

    def test_simple_post_htmx_permission_denied_is_a_danger_toast(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(htmx=True)
            response = _SelfAuthorizedAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 204)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["showMessage"]["level"], "danger")
        self.assertIn("not yours", trigger["showMessage"]["message"])

    def test_transaction_view_htmx_success_payload(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(data={"name": "Renamed GVF"}, htmx=True)
            response = _RenameAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 204)
        trigger = json.loads(response["HX-Trigger"])
        self.assertIn("closeModalEvent", trigger)
        self.assertIn("tableRefreshRequired", trigger)
        self.assertEqual(trigger["showMessage"], {"message": "Renamed.", "level": "success"})

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.name, "Renamed GVF")

    def test_transaction_view_non_htmx_success_redirects_to_the_object(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(data={"name": "Renamed Plain GVF"}, htmx=False)
            response = _RenameAssetView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.asset.get_absolute_url())

    def test_transaction_view_hx_redirect_on_success_opt_in(self):
        class _RedirectingRenameView(_RenameAssetView):
            hx_redirect_on_success = True

        with self.tenant_context(self.tenant, self.tenant_membership):
            request = self.make_request(data={"name": "Renamed Redirect GVF"}, htmx=True)
            response = _RedirectingRenameView.as_view()(request, pk=self.asset.pk)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], self.asset.get_absolute_url())
        self.assertNotIn("HX-Trigger", response)

    def test_transaction_view_puts_the_object_in_the_context(self):
        with self.tenant_context(self.tenant, self.tenant_membership):
            view = _RenameAssetView()
            view.request = self.make_request(method="get")
            view.kwargs = {"pk": self.asset.pk}
            context = view.get_context_data(form=None)

        self.assertEqual(context["object"], self.asset)
