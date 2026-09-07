"""Public HTTP coverage for the final specification REST adapters."""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Asset, AssetType, AssetTypeFieldset, Category, CategoryDefaultFieldset, Manufacturer
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField, CustomFieldset, CustomFieldsetField
from organization.models import Tenant


class T12PublicSpecificationHTTPTests(TenantTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.setup_tenant_context(
            name="T12 HTTP tenant",
            slug="t12-http-tenant",
            permissions=["assets.view_asset", "assets.add_asset", "assets.change_asset"],
        )
        self.manufacturer = Manufacturer.objects.create(name="T12 HTTP maker", slug="t12-http-maker")
        self.category = Category.objects.create(name="T12 HTTP category", slug="t12-http-category")
        self.type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="T12 source type",
            slug="t12-source-type",
            category=self.category,
        )
        self.first_field = self._field("t12_first_note")
        self.second_field = self._field("t12_second_note")
        self.first = self._fieldset("t12-first", self.first_field)
        self.second = self._fieldset("t12-second", self.second_field)
        AssetTypeFieldset.objects.create(asset_type=self.type, fieldset=self.first, position=1)
        CategoryDefaultFieldset.objects.create(category=self.category, fieldset=self.first, position=1)

        with self.tenant_context(self.tenant):
            self.asset = Asset.objects.create(
                name="T12 source asset",
                asset_tag="T12-SOURCE-ASSET",
                tenant=self.tenant,
                asset_type=self.type,
            )
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def _field(self, name, *, target=AssetType):
        field = CustomField.objects.create(
            namespace="local",
            name=name,
            label=name.replace("_", " ").title(),
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        field.object_types.add(ContentType.objects.get_for_model(target))
        return field

    def _fieldset(self, slug, field):
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug=slug,
            label=slug.replace("-", " ").title(),
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)
        return fieldset

    def _type_detail_url(self, asset_type=None):
        return reverse("api:assets_api:assettype-detail", args=[(asset_type or self.type).pk])

    def _asset_detail_url(self, asset=None):
        return reverse("api:assets_api:asset-detail", args=[(asset or self.asset).pk])

    def test_definition_and_composition_preview_are_ordered_and_read_only(self):
        definition_url = reverse("api:assets_api:assettype-specification-definition", args=[self.type.pk])
        response = self.client.get(definition_url, {"target": "asset_type"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual([row["identity"] for row in response.data["fieldsets"]], ["local/t12-first"])
        self.assertEqual(response.data["target"], "asset_type")
        self.assertIn("revision", response.data)

        preview_url = reverse("api:assets_api:assettype-composition-preview", args=[self.type.pk])
        response = self.client.post(
            preview_url,
            {
                "fieldsets": ["local/t12-second", "local/t12-first"],
                "specification_patch": {"set": {}, "clear": []},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["fieldsets"], ["local/t12-second", "local/t12-first"])
        self.assertEqual(
            [row["identity"] for row in response.data["definition"]["fieldsets"]],
            ["local/t12-second", "local/t12-first"],
        )
        self.assertEqual(response.data["impact"]["asset_count"], 1)
        self.assertEqual(
            list(self.type.fieldset_memberships.order_by("position").values_list("fieldset__slug", flat=True)),
            ["t12-first"],
        )

    def test_composition_requires_if_match_and_definition_preconditions(self):
        composition_url = reverse("api:assets_api:assettype-composition", args=[self.type.pk])
        detail = self.client.get(self._type_detail_url())
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        current_definition = detail.data["definition_revision"]

        missing_if_match = self.client.put(
            composition_url,
            {
                "fieldsets": ["local/t12-second"],
                "expected_definition_revision": current_definition,
            },
            format="json",
        )
        self.assertEqual(missing_if_match.status_code, status.HTTP_428_PRECONDITION_REQUIRED, missing_if_match.data)
        self.assertEqual(missing_if_match.data["error"]["issues"][0]["path"], ["If-Match"])

        missing_definition = self.client.put(
            composition_url,
            {"fieldsets": ["local/t12-second"]},
            format="json",
            HTTP_IF_MATCH=detail["ETag"],
        )
        self.assertEqual(missing_definition.status_code, status.HTTP_428_PRECONDITION_REQUIRED, missing_definition.data)
        self.assertEqual(missing_definition.data["error"]["issues"][0]["path"], ["expected_definition_revision"])

    def test_stale_resource_and_definition_win_before_invalid_fieldset_reference(self):
        composition_url = reverse("api:assets_api:assettype-composition", args=[self.type.pk])
        response = self.client.put(
            composition_url,
            {
                "fieldsets": ["local/does-not-exist"],
                "expected_definition_revision": "sha256:stale-definition",
            },
            format="json",
            HTTP_IF_MATCH='"sha256:stale-resource"',
        )
        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED, response.data)
        self.assertEqual(
            [(item["code"], item["path"]) for item in response.data["error"]["issues"]],
            [
                ("STALE_RESOURCE", ["expected_resource_revision"]),
                ("STALE_DEFINITION", ["expected_definition_revision"]),
            ],
        )
        self.assertEqual(
            list(self.type.fieldset_memberships.order_by("position").values_list("fieldset__slug", flat=True)),
            ["t12-first"],
        )

    def test_composition_write_and_native_type_update_use_public_paths(self):
        detail = self.client.get(self._type_detail_url())
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        preview_url = reverse("api:assets_api:assettype-composition-preview", args=[self.type.pk])
        preview = self.client.post(
            preview_url,
            {
                "fieldsets": ["local/t12-second", "local/t12-first"],
                "specification_patch": {"set": {}, "clear": []},
            },
            format="json",
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)

        composition_url = reverse("api:assets_api:assettype-composition", args=[self.type.pk])
        response = self.client.put(
            composition_url,
            {
                "fieldsets": preview.data["fieldsets"],
                "expected_definition_revision": preview.data["expected_definition_revision"],
                "specification_patch": {"set": {}, "clear": []},
            },
            format="json",
            HTTP_IF_MATCH=f'"{preview.data["expected_resource_revision"]}"',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["outcome"], "changed")
        self.assertEqual(
            list(self.type.fieldset_memberships.order_by("position").values_list("fieldset__slug", flat=True)),
            ["t12-second", "t12-first"],
        )

        detail = self.client.get(self._type_detail_url())
        native = self.client.patch(
            self._type_detail_url(),
            {"model": "T12 native edit"},
            format="json",
            HTTP_IF_MATCH=detail["ETag"],
        )
        self.assertEqual(native.status_code, status.HTTP_200_OK, native.data)
        self.type.refresh_from_db()
        self.assertEqual(self.type.model, "T12 native edit")

    def test_asset_type_switch_uses_destination_definition_and_if_match(self):
        destination = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="T12 destination type",
            slug="t12-destination-type",
            category=self.category,
        )
        destination_definition_url = reverse(
            "api:assets_api:assettype-specification-definition",
            args=[destination.pk],
        )
        destination_definition = self.client.get(destination_definition_url, {"target": "asset"})
        self.assertEqual(destination_definition.status_code, status.HTTP_200_OK, destination_definition.data)

        self.client_login_to_tenant(self.tenant_user, self.tenant)
        asset_url = self._asset_detail_url()
        current = self.client.get(asset_url)
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
        response = self.client.patch(
            asset_url,
            {
                "asset_type_id": destination.pk,
                "specification_patch": {"set": {}, "clear": []},
                "expected_definition_revision": destination_definition.data["revision"],
            },
            format="json",
            HTTP_IF_MATCH=current["ETag"],
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.asset_type_id, destination.pk)

    def test_native_asset_update_without_specification_gate_remains_available(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        asset_url = self._asset_detail_url()
        current = self.client.get(asset_url)
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
        response = self.client.patch(
            asset_url,
            {"name": "T12 native asset edit", "asset_type_id": self.type.pk},
            format="json",
            HTTP_IF_MATCH=current["ETag"],
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.name, "T12 native asset edit")

    def test_category_defaults_read_and_replace_are_atomic_public_operations(self):
        defaults_url = reverse("api:assets_api:category-default-fieldsets", args=[self.category.pk])
        current = self.client.get(defaults_url)
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
        self.assertEqual(current.data["fieldsets"], ["local/t12-first"])
        response = self.client.put(
            defaults_url,
            {"fieldsets": ["local/t12-second"]},
            format="json",
            HTTP_IF_MATCH=current["ETag"],
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["owner"], {"kind": "category", "id": self.category.pk})
        self.assertEqual(
            list(
                self.category.default_fieldset_memberships.order_by("position").values_list("fieldset__slug", flat=True)
            ),
            ["t12-second"],
        )

    def test_default_consuming_type_create_reports_all_missing_preconditions_in_order(self):
        response = self.client.post(
            reverse("api:assets_api:assettype-list"),
            {
                "manufacturer_id": self.manufacturer.pk,
                "model": "T12 missing preconditions",
                "slug": "t12-missing-preconditions",
                "category_id": self.category.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_428_PRECONDITION_REQUIRED, response.data)
        self.assertEqual(
            [(item["code"], item["path"]) for item in response.data["error"]["issues"]],
            [
                ("MISSING_PRECONDITION", ["preview_token"]),
                ("MISSING_PRECONDITION", ["expected_category_default_snapshot_revision"]),
                ("MISSING_PRECONDITION", ["expected_definition_revision"]),
            ],
        )
        self.assertFalse(AssetType.all_objects.filter(slug="t12-missing-preconditions").exists())

    def test_denied_asset_is_indistinguishable_from_missing_before_stale_or_reference_details(self):
        foreign_tenant = Tenant.objects.create(name="T12 foreign tenant", slug="t12-foreign-tenant")
        with self.tenant_context(foreign_tenant):
            foreign_asset = Asset.objects.create(
                name="T12 foreign asset",
                asset_tag="T12-FOREIGN-ASSET",
                tenant=foreign_tenant,
                asset_type=self.type,
            )

        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.patch(
            self._asset_detail_url(foreign_asset),
            {
                "specification_patch": {
                    "set": {"local/does-not-exist": "hidden"},
                    "clear": [],
                },
                "expected_definition_revision": "sha256:stale-definition",
            },
            format="json",
            HTTP_IF_MATCH='"sha256:stale-resource"',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.data)
        self.assertNotIn("STALE_RESOURCE", str(response.data))
        self.assertNotIn("REFERENCE_CONFLICT", str(response.data))

    def test_history_preview_and_cleanup_use_token_and_revision_gates(self):
        self.type.custom_field_data = {"t12_removed_history": "retain until cleanup"}
        self.type.save(update_fields=["custom_field_data"])
        detail = self.client.get(self._type_detail_url())
        history_preview_url = reverse(
            "api:assets_api:assettype-specification-history-cleanup-preview",
            args=[self.type.pk],
        )
        preview = self.client.post(
            history_preview_url,
            {
                "keys": ["t12_removed_history"],
                "expected_definition_revision": detail.data["definition_revision"],
            },
            format="json",
            HTTP_IF_MATCH=detail["ETag"],
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.data)
        self.assertEqual(preview.data["keys"], ["t12_removed_history"])
        self.assertTrue(preview.data["preview_token"])

        history_cleanup_url = reverse(
            "api:assets_api:assettype-specification-history-cleanup",
            args=[self.type.pk],
        )
        cleanup = self.client.post(
            history_cleanup_url,
            {
                "keys": ["t12_removed_history"],
                "preview_token": preview.data["preview_token"],
                "expected_definition_revision": detail.data["definition_revision"],
            },
            format="json",
            HTTP_IF_MATCH=detail["ETag"],
        )
        self.assertEqual(cleanup.status_code, status.HTTP_200_OK, cleanup.data)
        self.type.refresh_from_db()
        self.assertNotIn("t12_removed_history", self.type.custom_field_data)
