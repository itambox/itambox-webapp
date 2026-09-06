from unittest.mock import patch

from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from assets.forms import AssetForm, AssetTypeForm
from assets.models import Asset, AssetType, Manufacturer, StatusLabel
from assets.services.specifications.commands import set_asset_type_composition
from assets.tests.test_asset_form_value_preservation import _minimal_asset_form_data
from core.tests.mixins import TenantTestMixin
from extras.models import CustomFieldset


class SpecificationFormBoundaryTests(TenantTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.setup_tenant_context(
            name="Form command boundary",
            slug="form-command-boundary",
            permissions=["assets.add_asset", "assets.view_asset", "assets.change_asset"],
        )
        manufacturer = Manufacturer.objects.create(name="Form Boundary", slug="form-boundary")
        self.source = AssetType.objects.create(manufacturer=manufacturer, model="Source", slug="boundary-source")
        self.target = AssetType.objects.create(manufacturer=manufacturer, model="Target", slug="boundary-target")
        self.status = StatusLabel.objects.create(
            name="Boundary available", slug="boundary-available", type="deployable"
        )
        with self.tenant_context(self.tenant):
            self.asset = Asset.objects.create(
                name="Form boundary asset",
                asset_tag="FORM-BOUNDARY-ASSET",
                asset_type=self.source,
                status=self.status,
                tenant=self.tenant,
            )
        self.request = RequestFactory().post("/assets/")
        self.request.user = self.tenant_user
        self.request.tenant = self.tenant

    def test_mixed_form_updates_lock_catalogue_before_native_owner_write(self):
        admin_request = RequestFactory().post("/asset-types/")
        admin_request.user = self.tenant_admin
        with self.tenant_context(self.tenant):
            forms = (
                (
                    AssetForm(
                        data=_minimal_asset_form_data(
                            self.asset, self.status, self.target, tenant=self.tenant.pk, name="Changed native name"
                        ),
                        instance=self.asset,
                        request=self.request,
                    ),
                    "assets_asset",
                ),
                (
                    AssetTypeForm(
                        data={
                            "manufacturer": self.source.manufacturer_id,
                            "model": "Changed native model",
                            "slug": self.source.slug,
                        },
                        instance=self.source,
                        request=admin_request,
                    ),
                    "assets_assettype",
                ),
            )
            for form, table in forms:
                with self.subTest(form=type(form).__name__):
                    self.assertTrue(form.is_valid(), form.errors.as_json())
                    with CaptureQueriesContext(connection) as queries:
                        form.save()
                    sql = [entry["sql"] for entry in queries.captured_queries]
                    locks = [i for i, query in enumerate(sql) if "pg_advisory_xact_lock" in query]
                    writes = [
                        i
                        for i, query in enumerate(sql)
                        if query.startswith(f"UPDATE {connection.ops.quote_name(table)}")
                    ]
                    self.assertTrue(locks, sql)
                    self.assertTrue(writes, sql)
                    self.assertLess(locks[0], writes[0], "Form wrote native fields before catalogue locking")

    def test_deferred_type_m2m_does_not_mutate_composition_before_command(self):
        fieldset = CustomFieldset.objects.create(
            namespace="local", slug="deferred-composition", label="Deferred composition"
        )
        request = RequestFactory().post("/asset-types/")
        request.user = self.tenant_admin
        form = AssetTypeForm(
            data={
                "manufacturer": self.source.manufacturer_id,
                "model": self.source.model,
                "slug": self.source.slug,
                "custom_fieldsets": [fieldset.pk],
            },
            instance=self.source,
            request=request,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        pending = form.save(commit=False)
        pending.save()

        def observe_real_command(**kwargs):
            self.assertFalse(self.source.fieldset_memberships.exists(), "Native save_m2m bypassed composition command")
            return set_asset_type_composition(**kwargs)

        with patch(
            "assets.forms.assettype_form.set_asset_type_composition", side_effect=observe_real_command
        ) as command:
            form.save_m2m()
        command.assert_called_once()
        self.assertEqual(list(self.source.fieldset_memberships.values_list("fieldset_id", flat=True)), [fieldset.pk])

    def test_asset_form_status_edit_persists_native_disposal_side_effects(self):
        archived = StatusLabel.objects.create(name="Form archived", slug="form-boundary-archived", type="archived")
        previous_timestamp = self.asset.updated_at
        with self.tenant_context(self.tenant):
            form = AssetForm(
                data=_minimal_asset_form_data(self.asset, archived, self.source, tenant=self.tenant.pk),
                instance=self.asset,
                request=self.request,
            )
            self.assertTrue(form.is_valid(), form.errors.as_json())
            saved = form.save()
            saved.refresh_from_db()
            self.assertIsNotNone(saved.disposed_at)
            self.assertIsNotNone(saved.disposal_value)
            self.assertNotEqual(saved.updated_at, previous_timestamp)

    def test_type_form_native_edit_advances_timestamp(self):
        request = RequestFactory().post("/asset-types/")
        request.user = self.tenant_admin
        previous_timestamp = self.source.updated_at
        form = AssetTypeForm(
            data={
                "manufacturer": self.source.manufacturer_id,
                "model": "Updated native Type",
                "slug": self.source.slug,
            },
            instance=self.source,
            request=request,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        saved = form.save()
        saved.refresh_from_db()
        self.assertNotEqual(saved.updated_at, previous_timestamp)

    def test_native_asset_form_create_does_not_require_change_permission(self):
        self.tenant_role.permissions = ["assets.add_asset", "assets.view_asset"]
        self.tenant_role.save(update_fields=["permissions"])
        unsaved = Asset(name="Native form creation", asset_tag="FORM-NATIVE-CREATE")
        with self.tenant_context(self.tenant):
            form = AssetForm(
                data=_minimal_asset_form_data(unsaved, self.status, self.source, tenant=self.tenant.pk),
                request=self.request,
            )
            self.assertTrue(form.is_valid(), form.errors)
            with patch("assets.forms.asset_form.update_asset_specifications") as command:
                created = form.save()
            command.assert_not_called()
        created.refresh_from_db()
        self.assertEqual(created.tenant_id, self.tenant.pk)
        self.assertEqual(created.asset_type_id, self.source.pk)
        self.assertEqual(created.custom_field_data, {})

    def test_commit_false_does_not_expose_type_switch_to_native_save(self):
        with self.tenant_context(self.tenant):
            form = AssetForm(
                data=_minimal_asset_form_data(self.asset, self.status, self.target, tenant=self.tenant.pk),
                instance=self.asset,
                request=self.request,
            )
            self.assertTrue(form.is_valid(), form.errors.as_json())
            pending = form.save(commit=False)
            self.assertEqual(pending.asset_type_id, self.source.pk)
            pending.save()
            self.asset.refresh_from_db()
            self.assertEqual(self.asset.asset_type_id, self.source.pk)
            form.save_m2m()
            self.asset.refresh_from_db()
            self.assertEqual(self.asset.asset_type_id, self.target.pk)
