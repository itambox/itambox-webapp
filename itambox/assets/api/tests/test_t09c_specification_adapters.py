from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.test import APITestCase

from assets.api.serializers import AssetSerializer, AssetTypeSerializer
from assets.models import Asset, AssetType, AssetTypeFieldset, Manufacturer, StatusLabel
from assets.services.specifications.commands import create_asset_type
from core.mixins import suppress_custom_field_data_validation
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from extras.models import CustomField, CustomFieldset, CustomFieldsetField


def test_public_asset_serializers_collect_specification_patch_field():
    asset_type_fields = AssetTypeSerializer().fields
    asset_fields = AssetSerializer().fields

    assert "specification_patch" in asset_type_fields
    assert "specification_patch" in asset_fields
    assert "custom_fieldsets" not in list(asset_type_fields)  # Composition REST input belongs to T12.
    assert asset_type_fields["specification_patch"].write_only is True
    assert asset_fields["specification_patch"].write_only is True


class TestAssetSpecificationAPI(TenantTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.setup_tenant_context(
            name="T09-C denied tenant",
            slug="t09-c-denied",
            permissions=["assets.add_asset"],
        )
        manufacturer = Manufacturer.objects.create(name="T09-C Maker", slug="t09-c-maker")
        self.asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="T09-C Device",
            slug="t09-c-device",
        )
        self.client_login_to_tenant(self.tenant_user, self.tenant)

    def test_direct_asset_type_update_rolls_back_native_change_on_specification_rejection(self):
        original_model = self.asset_type.model
        serializer = AssetTypeSerializer(
            instance=self.asset_type,
            data={
                "model": "T09-C rejected native update",
                "specification_patch": {"set": {"unknown_key": "must reject"}, "clear": []},
            },
            partial=True,
            context={"request": SimpleNamespace(user=self.tenant_admin)},
        )
        assert serializer.is_valid(), serializer.errors

        with self.assertRaises(drf_serializers.ValidationError):
            serializer.save()

        self.asset_type.refresh_from_db()
        assert self.asset_type.model == original_model

    def test_direct_asset_update_rolls_back_native_change_when_specification_is_denied(self):
        with self.tenant_context(self.tenant):
            asset = Asset(
                name="T09-C denied direct asset",
                asset_tag="T09-C-DIRECT-DENIED",
                tenant=self.tenant,
                asset_type=self.asset_type,
            )
            with suppress_custom_field_data_validation(asset):
                asset.save()

            serializer = AssetSerializer(
                instance=asset,
                data={
                    "name": "T09-C leaked native name",
                    "specification_patch": {"set": {}, "clear": []},
                },
                partial=True,
                context={"request": SimpleNamespace(user=self.tenant_user)},
            )
            assert serializer.is_valid(), serializer.errors
            with self.assertRaises(DRFPermissionDenied):
                serializer.save()

        asset.refresh_from_db()
        assert asset.name == "T09-C denied direct asset"

    def test_mixed_updates_acquire_catalogue_lock_before_owner_write(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset", "assets.change_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        with self.tenant_context(self.tenant):
            asset = Asset.objects.create(
                name="Lock-order asset",
                asset_tag="T09-C-LOCK-ORDER",
                tenant=self.tenant,
                asset_type=self.asset_type,
            )
            cases = (
                (AssetSerializer, asset, {"name": "Native asset edit"}, "assets_asset"),
                (AssetTypeSerializer, self.asset_type, {"model": "Native type edit"}, "assets_assettype"),
            )
            for serializer_type, owner, native, table in cases:
                with self.subTest(serializer=serializer_type.__name__):
                    actor = self.tenant_user if serializer_type is AssetSerializer else self.tenant_admin
                    serializer = serializer_type(
                        instance=owner,
                        data={**native, "specification_patch": {"set": {}, "clear": []}},
                        partial=True,
                        context={"request": SimpleNamespace(user=actor)},
                    )
                    self.assertTrue(serializer.is_valid(), serializer.errors)
                    with CaptureQueriesContext(connection) as queries:
                        serializer.save()
                    sql = [entry["sql"] for entry in queries.captured_queries]
                    locks = [i for i, query in enumerate(sql) if "pg_advisory_xact_lock" in query]
                    writes = [
                        i
                        for i, query in enumerate(sql)
                        if query.startswith(f"UPDATE {connection.ops.quote_name(table)}")
                    ]
                    self.assertTrue(locks, sql)
                    self.assertTrue(writes, sql)
                    self.assertLess(
                        locks[0], writes[0], "Native UPDATE acquired the owner lock before catalogue locking"
                    )

    def test_native_only_edit_does_not_enforce_later_specification_requirement(self):
        with self.tenant_context(self.tenant):
            asset = Asset.objects.create(
                name="Before native edit",
                asset_tag="T09-C-NATIVE-ONLY",
                tenant=self.tenant,
                asset_type=self.asset_type,
            )
            fieldset = self._asset_fieldset("late-required", "late_required")
            field = CustomField.objects.get(name="late_required")
            field.required = True
            field.save(update_fields=["required"])
            AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=fieldset, position=1)
            serializer = AssetSerializer(
                instance=asset,
                data={"name": "After native edit"},
                partial=True,
                context={"request": SimpleNamespace(user=self.tenant_user)},
            )
            self.assertTrue(serializer.is_valid(), serializer.errors)
            with CaptureQueriesContext(connection) as queries:
                serializer.save()
            self.assertFalse(any("pg_advisory_xact_lock" in entry["sql"] for entry in queries.captured_queries))
            asset.refresh_from_db()
            self.assertEqual(asset.name, "After native edit")
            self.assertEqual(asset.custom_field_data, {})

    def test_public_asset_type_create_uses_canonical_create(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        with patch("assets.api.serializers.create_asset_type", wraps=create_asset_type) as command:
            response = self.client.post(
                reverse("api:assets_api:assettype-list"),
                {
                    "manufacturer_id": self.asset_type.manufacturer_id,
                    "model": "Created via canonical command",
                    "slug": "created-via-canonical-command",
                    "specification_patch": {"set": {}, "clear": []},
                },
                format="json",
            )
        command.assert_called_once()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        created = AssetType.all_objects.get(pk=response.data["id"])
        self.assertEqual(created.model, "Created via canonical command")
        self.assertEqual(created.custom_field_data, {})

    def test_public_type_image_is_staged_before_real_create_command(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        image = BytesIO()
        Image.new("RGB", (2, 2), color="red").save(image, format="PNG")
        upload = SimpleUploadedFile("type.png", image.getvalue(), content_type="image/png")
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with self.captureOnCommitCallbacks(execute=True):
                with patch("assets.api.serializers.create_asset_type", wraps=create_asset_type) as command:
                    response = self.client.post(
                        reverse("api:assets_api:assettype-list"),
                        {
                            "manufacturer_id": self.asset_type.manufacturer_id,
                            "model": "Staged image type",
                            "slug": "staged-image-type",
                            "image": upload,
                        },
                        format="multipart",
                    )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
            command.assert_called_once()
            self.assertTrue(command.call_args.kwargs["native"].staged_image_id)
            created = AssetType.all_objects.get(pk=response.data["id"])
            self.assertTrue(created.image.name)
            self.assertTrue(created.image.storage.exists(created.image.name))

    def test_public_value_set_clear_and_noop_preserve_unknown_history(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset", "assets.change_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        fieldset = self._asset_fieldset("editable-observation", "editable_note")
        AssetTypeFieldset.objects.create(asset_type=self.asset_type, fieldset=fieldset, position=1)
        with self.tenant_context(self.tenant):
            asset = Asset.objects.create(
                name="Editable observed values",
                asset_tag="T09-C-PATCH-ROUNDTRIP",
                tenant=self.tenant,
                asset_type=self.asset_type,
                custom_field_data={"unknown_history": "retain"},
            )
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("api:assets_api:asset-detail", kwargs={"pk": asset.pk})
        cases = (
            ({"set": {"editable_note": "observed"}, "clear": []}, {"editable_note": "observed"}),
            ({"set": {}, "clear": ["editable_note"]}, {}),
            ({"set": {}, "clear": []}, {}),
        )
        for value_patch, expected in cases:
            with self.subTest(patch=value_patch):
                current = self.client.get(url)
                self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
                asset.refresh_from_db()
                timestamp = asset.updated_at
                changes = ObjectChange._base_manager.filter(
                    changed_object_type=ContentType.objects.get_for_model(Asset), changed_object_id=asset.pk
                )
                change_count = changes.count()
                response = self.client.patch(
                    url, {"specification_patch": value_patch}, format="json", HTTP_IF_MATCH=current["ETag"]
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
                asset.refresh_from_db()
                self.assertEqual(asset.custom_field_data, {"unknown_history": "retain", **expected})
                if not value_patch["set"] and not value_patch["clear"]:
                    self.assertEqual(asset.updated_at, timestamp)
                    self.assertEqual(changes.count(), change_count)

    def test_untyped_asset_uses_global_definition_through_canonical_command(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset", "assets.change_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        field = CustomField.objects.create(
            namespace="local",
            name="untyped_observation",
            label="Untyped observation",
            activation=CustomField.ACTIVATION_GLOBAL,
        )
        field.object_types.add(ContentType.objects.get_for_model(Asset))
        with self.tenant_context(self.tenant):
            asset = Asset.objects.create(
                name="Untyped observed asset", asset_tag="UNTYPED-OBSERVATION", tenant=self.tenant
            )
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("api:assets_api:asset-detail", kwargs={"pk": asset.pk})
        current = self.client.get(url)
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
        response = self.client.patch(
            url,
            {"specification_patch": {"set": {field.name: "observed"}, "clear": []}},
            format="json",
            HTTP_IF_MATCH=current["ETag"],
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        asset.refresh_from_db()
        self.assertIsNone(asset.asset_type_id)
        self.assertEqual(asset.custom_field_data, {field.name: "observed"})

    def test_mixed_status_update_preserves_disposal_and_timestamp_side_effects(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset", "assets.change_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        archived = StatusLabel.objects.create(name="Adapter archived", slug="adapter-archived", type="archived")
        with self.tenant_context(self.tenant):
            asset = Asset.objects.create(
                name="Archive mixed update", asset_tag="ARCHIVE-MIXED", asset_type=self.asset_type, tenant=self.tenant
            )
        previous_timestamp = asset.updated_at
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("api:assets_api:asset-detail", kwargs={"pk": asset.pk})
        current = self.client.get(url)
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
        response = self.client.patch(
            url,
            {"status_id": archived.pk, "specification_patch": {"set": {}, "clear": []}},
            format="json",
            HTTP_IF_MATCH=current["ETag"],
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        asset.refresh_from_db()
        self.assertEqual(asset.status_id, archived.pk)
        self.assertIsNotNone(asset.disposed_at)
        self.assertIsNotNone(asset.disposal_value)
        self.assertNotEqual(asset.updated_at, previous_timestamp)

    def test_public_update_locks_catalogue_before_generic_owner_lock(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset", "assets.change_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        with self.tenant_context(self.tenant):
            asset = Asset.objects.create(name="Public lock order", tenant=self.tenant, asset_type=self.asset_type)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("api:assets_api:asset-detail", args=[asset.pk])
        etag = self.client.get(url)["ETag"]
        with CaptureQueriesContext(connection) as captured:
            response = self.client.patch(
                url,
                {"name": "Updated public lock order", "specification_patch": {"set": {}, "clear": []}},
                format="json",
                HTTP_IF_MATCH=etag,
            )
        self.assertEqual(response.status_code, 200, response.data)
        sql = [query["sql"] for query in captured.captured_queries]
        catalogue_lock = next(index for index, query in enumerate(sql) if "pg_advisory_xact_lock" in query)
        owner_lock = next(
            index for index, query in enumerate(sql) if 'FROM "assets_asset"' in query and "FOR UPDATE" in query
        )
        self.assertLess(catalogue_lock, owner_lock)
        self.assertIn("pg_advisory_xact_lock_shared", sql[catalogue_lock])

    def test_public_native_only_edit_does_not_acquire_specification_locks(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        url = reverse("api:assets_api:assettype-detail", args=[self.asset_type.pk])
        etag = self.client.get(url)["ETag"]
        with CaptureQueriesContext(connection) as captured:
            response = self.client.patch(url, {"model": "Native only"}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(any("pg_advisory_xact_lock" in query["sql"] for query in captured.captured_queries))
        self.asset_type.refresh_from_db()
        self.assertEqual(self.asset_type.model, "Native only")

    def test_native_asset_create_does_not_require_specification_change_permission(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        with patch("assets.api.serializers.update_asset_specifications") as command:
            response = self.client.post(
                reverse("api:assets_api:asset-list"),
                {
                    "name": "Native creation only",
                    "asset_tag": "NATIVE-CREATE-1",
                    "asset_type_id": self.asset_type.pk,
                    "tenant_id": self.tenant.pk,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.data)
        command.assert_not_called()
        created = Asset._base_manager.get(pk=response.data["id"])
        self.assertEqual(created.tenant_id, self.tenant.pk)
        self.assertEqual(created.asset_type_id, self.asset_type.pk)
        self.assertEqual(created.custom_field_data, {})

    def _asset_fieldset(self, slug, field_name):
        field = CustomField.objects.create(
            name=field_name,
            namespace="local",
            label=field_name.replace("_", " ").title(),
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
        )
        field.object_types.add(ContentType.objects.get_for_model(Asset))
        fieldset = CustomFieldset.objects.create(
            namespace="local",
            slug=slug,
            label=slug.replace("-", " ").title(),
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=field, position=1)
        return fieldset

    def test_asset_type_switch_uses_destination_definition_revision(self):
        type_a = AssetType.objects.create(
            manufacturer=self.asset_type.manufacturer,
            model="T09-C Source Device",
            slug="t09-c-source-device",
        )
        type_b = AssetType.objects.create(
            manufacturer=self.asset_type.manufacturer,
            model="T09-C Destination Device",
            slug="t09-c-destination-device",
        )
        source_fieldset = self._asset_fieldset("source-definition", "source_note")
        destination_fieldset = self._asset_fieldset("destination-definition", "destination_note")
        AssetTypeFieldset.objects.create(asset_type=type_a, fieldset=source_fieldset, position=1)
        AssetTypeFieldset.objects.create(asset_type=type_b, fieldset=destination_fieldset, position=1)
        with self.tenant_context(self.tenant):
            asset = Asset(
                name="T09-C switchable asset",
                asset_tag="T09-C-SWITCH",
                tenant=self.tenant,
                asset_type=type_a,
                custom_field_data={"source_note": "retained history"},
            )
            with suppress_custom_field_data_validation(asset):
                asset.save()

        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset", "assets.change_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        detail_url = reverse("api:assets_api:asset-detail", kwargs={"pk": asset.pk})
        current_response = self.client.get(detail_url)
        assert current_response.status_code == status.HTTP_200_OK, current_response.data
        response = self.client.patch(
            detail_url,
            {
                "asset_type_id": type_b.pk,
                "specification_patch": {"set": {}, "clear": []},
            },
            format="json",
            HTTP_IF_MATCH=current_response["ETag"],
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        asset.refresh_from_db()
        assert asset.asset_type_id == type_b.pk
        assert asset.custom_field_data == {"source_note": "retained history"}

    def test_asset_create_denied_by_specification_authority_returns_403(self):
        response = self.client.post(
            reverse("api:assets_api:asset-list"),
            {
                "name": "Denied asset",
                "asset_tag": "T09-C-DENIED",
                "asset_type_id": self.asset_type.pk,
                "tenant_id": self.tenant.pk,
                "specification_patch": {"set": {}, "clear": []},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        assert not Asset._base_manager.filter(asset_tag="T09-C-DENIED").exists()
