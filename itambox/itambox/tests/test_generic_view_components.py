"""Tests for the extracted generic-view components (issue #82).

``RelatedObjectProvider``, ``TableContextBuilder`` and the shared HTMX response
helpers were carved out of ``ObjectDetailView``/``ObjectListView``/the service
views. These tests pin the components' own contracts *and* assert that the views
still delegate to them, so the extraction cannot quietly grow a second copy.

The behaviour itself is locked by ``test_generic_view_foundations.py``; this
module is about the seams.
"""

import json
from unittest import mock

from django.http import Http404, HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.translation import gettext_lazy

from assets.models import Asset, AssetType, Manufacturer, StatusLabel
from assets.tables import AssetTable
from core.tests.mixins import TenantTestMixin
from itambox.views.generic.detail import ObjectDetailView
from itambox.views.generic.htmx_responses import (
    HX_CLOSE_MODAL,
    HX_SHOW_MESSAGE,
    HX_TABLE_REFRESH,
    error_response,
    is_htmx_request,
    success_response,
    trigger_response,
)
from itambox.views.generic.list_ import ObjectListView
from itambox.views.generic.related_objects import RelatedObjectProvider
from itambox.views.generic.table_context import TableContextBuilder
from organization.models import Tenant

# ---------------------------------------------------------------------------
# RelatedObjectProvider
# ---------------------------------------------------------------------------


class RelatedObjectProviderTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            name="Provider Tenant GVC",
            slug="provider-tenant-gvc",
            permissions=["assets.view_asset"],
        )
        self.tenant_b = Tenant.objects.create(name="Provider Tenant B GVC", slug="provider-tenant-b-gvc")
        self.manufacturer = Manufacturer.objects.create(name="Provider Mfr GVC", slug="provider-mfr-gvc")
        self.asset_type = AssetType.objects.create(
            model="Provider Type GVC",
            slug="provider-type-gvc",
            manufacturer=self.manufacturer,
        )
        self.status, _created = StatusLabel.objects.get_or_create(
            slug="available",
            defaults={"name": "Available", "type": "deployable", "color": "28a745"},
        )

    def _asset(self, name, tag, tenant):
        return Asset.objects.create(
            name=name, asset_tag=tag, tenant=tenant, status=self.status, asset_type=self.asset_type
        )

    def test_build_matches_the_detail_view_helper(self):
        """The view helper and the component must agree exactly — that identity
        is what makes the extraction safe."""
        self._asset("Prov A1 GVC", "PROV-A1-GVC", self.tenant)
        self._asset("Prov B1 GVC", "PROV-B1-GVC", self.tenant_b)

        with self.tenant_context(self.tenant, self.tenant_membership):
            from_component = RelatedObjectProvider(self.asset_type).build()
            from_view = ObjectDetailView()._build_related_objects_list(self.asset_type)

        self.assertEqual(from_component, from_view)

    def test_counts_respect_tenant_scoping(self):
        self._asset("Prov A2 GVC", "PROV-A2-GVC", self.tenant)
        self._asset("Prov A3 GVC", "PROV-A3-GVC", self.tenant)
        self._asset("Prov B2 GVC", "PROV-B2-GVC", self.tenant_b)

        with self.tenant_context(self.tenant, self.tenant_membership):
            rows = RelatedObjectProvider(self.asset_type).build()

        assets = [row for row in rows if row["label"] == "Assets"]
        self.assertEqual([row["count"] for row in assets], [2])

    def test_detail_view_delegates_to_the_provider(self):
        """``_build_related_objects_list`` survives as a thin, overridable wrapper."""
        sentinel = [{"label": "Sentinel", "count": 1, "url": "/x/"}]

        with mock.patch.object(RelatedObjectProvider, "build", return_value=sentinel) as build:
            result = ObjectDetailView()._build_related_objects_list(self.asset_type)

        build.assert_called_once_with()
        self.assertEqual(result, sentinel)

    def test_detail_view_excludes_sensitive_reverse_relations_before_counting(self):
        self._asset("Prov excluded GVC", "PROV-EXCLUDED-GVC", self.tenant)
        view = ObjectDetailView()
        view.related_object_exclusions = ("assets.asset",)

        with self.tenant_context(self.tenant, self.tenant_membership):
            rows = view._build_related_objects_list(self.asset_type)

        self.assertFalse(any(row["label"] == "Assets" for row in rows))

    def test_distinct_detection_is_shared_with_the_view(self):
        """Models carrying a ``filter_tenants`` M2M keep the legacy per-relation
        ``.count()`` because a plain FK subquery would count the join fan-out."""
        self.assertFalse(RelatedObjectProvider.count_uses_distinct(Asset))
        self.assertEqual(
            ObjectDetailView._related_count_uses_distinct(Asset),
            RelatedObjectProvider.count_uses_distinct(Asset),
        )


# ---------------------------------------------------------------------------
# TableContextBuilder
# ---------------------------------------------------------------------------


class TableContextBuilderTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/assets/")

    def test_explicit_table_class_wins(self):
        builder = TableContextBuilder(Asset, table_class=AssetTable)
        self.assertIs(builder.resolve_table_class(), AssetTable)

    def test_falls_back_to_the_model_table_registry(self):
        builder = TableContextBuilder(Asset)
        with mock.patch("itambox.views.generic.table_context.get_table_for_model", return_value=AssetTable) as lookup:
            self.assertIs(builder.resolve_table_class(), AssetTable)
        lookup.assert_called_once_with(Asset)

    def test_missing_table_is_a_404(self):
        builder = TableContextBuilder(Asset)
        with mock.patch("itambox.views.generic.table_context.get_table_for_model", return_value=None):
            with self.assertRaises(Http404):
                builder.resolve_table_class()

    def test_build_returns_a_request_bound_table(self):
        builder = TableContextBuilder(Asset, table_class=AssetTable)
        table = builder.build([], self.request)
        self.assertIsInstance(table, AssetTable)

    def test_config_key_pairs_app_label_with_table_class_name(self):
        table = TableContextBuilder(Asset, table_class=AssetTable).build([], self.request)
        self.assertEqual(TableContextBuilder.config_key(Asset, table), "assets.AssetTable")


class ListViewUsesTableContextBuilderTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            name="Builder List Tenant GVC",
            slug="builder-list-tenant-gvc",
            permissions=["assets.view_asset"],
        )
        self.client_login_to_tenant(self.tenant_user, self.tenant)

    def test_list_view_get_table_delegates_to_the_builder(self):
        with mock.patch.object(TableContextBuilder, "build", wraps=TableContextBuilder.build, autospec=True) as build:
            response = self.client.get(reverse("assets:asset_list"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(build.called, "ObjectListView.get_table() no longer uses TableContextBuilder")

    def test_table_config_key_still_reaches_the_template_context(self):
        response = self.client.get(reverse("assets:asset_list"))
        self.assertEqual(
            response.context["table_config_key"],
            TableContextBuilder.config_key(Asset, response.context["table"]),
        )

    def test_get_table_raises_404_when_no_table_is_configured(self):
        class _NoTableView(ObjectListView):
            table = None
            model = Asset

        view = _NoTableView()
        view.request = RequestFactory().get("/x/")
        view.object_list = Asset.objects.none()
        with mock.patch("itambox.views.generic.table_context.get_table_for_model", return_value=None):
            with self.assertRaises(Http404):
                view.get_table()


# ---------------------------------------------------------------------------
# Shared HTMX response helpers
# ---------------------------------------------------------------------------


class HtmxResponseHelperTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_trigger_names_are_the_documented_client_events(self):
        self.assertEqual(HX_CLOSE_MODAL, "closeModalEvent")
        self.assertEqual(HX_TABLE_REFRESH, "tableRefreshRequired")
        self.assertEqual(HX_SHOW_MESSAGE, "showMessage")

    def test_trigger_response_is_204_with_a_json_payload(self):
        response = trigger_response({"a": None, "b": {"x": 1}})

        self.assertIsInstance(response, HttpResponse)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(json.loads(response["HX-Trigger"]), {"a": None, "b": {"x": 1}})

    def test_success_response_closes_the_modal_and_refreshes_by_default(self):
        response = success_response("Done.")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            json.loads(response["HX-Trigger"]),
            {
                "closeModalEvent": None,
                "tableRefreshRequired": None,
                "showMessage": {"message": "Done.", "level": "success"},
            },
        )

    def test_success_response_honours_a_custom_trigger(self):
        payload = json.loads(success_response("Done.", trigger="assetRefresh")["HX-Trigger"])

        self.assertIn("assetRefresh", payload)
        self.assertNotIn("tableRefreshRequired", payload)

    def test_success_response_can_skip_the_modal_close(self):
        """The restore/purge flows are triggered from a page, not a modal."""
        payload = json.loads(success_response("Restored.", close_modal=False)["HX-Trigger"])

        self.assertNotIn("closeModalEvent", payload)
        self.assertEqual(
            payload,
            {"tableRefreshRequired": None, "showMessage": {"message": "Restored.", "level": "success"}},
        )

    def test_success_response_coerces_lazy_messages(self):
        payload = json.loads(success_response(gettext_lazy("Done."))["HX-Trigger"])
        self.assertIsInstance(payload["showMessage"]["message"], str)

    def test_error_response_is_a_danger_toast_with_no_refresh(self):
        response = error_response("Nope.")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            json.loads(response["HX-Trigger"]),
            {"showMessage": {"message": "Nope.", "level": "danger"}},
        )

    def test_is_htmx_request_accepts_the_middleware_attribute(self):
        request = self.factory.post("/x/")
        request.htmx = True
        self.assertTrue(is_htmx_request(request))

    def test_is_htmx_request_accepts_the_raw_header(self):
        request = self.factory.post("/x/", HTTP_HX_REQUEST="true")
        self.assertTrue(is_htmx_request(request))

    def test_is_htmx_request_is_false_for_a_plain_request(self):
        request = self.factory.post("/x/")
        self.assertFalse(is_htmx_request(request))

        request.htmx = False
        self.assertFalse(is_htmx_request(request))
