import datetime
import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from assets.customfields import resolve_asset_type_custom_fields
from assets.models import (
    Asset,
    AssetAssignment,
    AssetMaintenance,
    AssetRequest,
    AssetRole,
    AssetType,
    Category,
    Manufacturer,
    RepairEpisode,
    StatusLabel,
    Supplier,
)
from core.management.commands._seed.access import check_seed_access_invariants
from core.management.commands._seed.consistency import check_seed_operational_invariants
from core.management.commands.seed_data import Command as SeedDataCommand
from core.management.commands.sync_tenant_ldap import Command as SyncTenantLDAPCommand
from core.models import Job, ObjectChange
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField
from licenses.models import License
from organization.models import AssetHolder, Location, Membership, Site, Tenant
from procurement.models import PurchaseOrder, PurchaseOrderLine

User = get_user_model()


class ManagementCommandsTestCase(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def test_purge_deleted_command(self):
        call_command("purge_deleted", days=30, dry_run=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Total objects that would be purged", self.stdout.getvalue())

    def test_rotate_encryption_keys_command(self):
        from assets.models import Manufacturer
        from software.models import Software

        mfr = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        software = Software.objects.create(name="Office 365", manufacturer=mfr)
        License.objects.create(name="Office 365", software=software, product_key="abc")
        call_command("rotate_encryption_keys", dry_run=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Scanning for encrypted fields", self.stdout.getvalue())

    def test_run_jobs_command(self):
        Job.objects.create(name="Script: my_script.py", status=Job.STATUS_PENDING)
        call_command("run_jobs", stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Job processing complete", self.stdout.getvalue())

    def test_seed_data_command(self):
        call_command("seed_data", production=True, force=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Database seeding complete", self.stdout.getvalue())

    def test_seed_catalog_writes_normative_compute_fieldset_memberships(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        fieldset = CustomFieldset.objects.get(namespace="itambox", slug="compute-memory")
        self.assertEqual(fieldset.label, "Compute and Memory")
        self.assertEqual(fieldset.management_kind, CustomFieldset.MANAGEMENT_CORE)
        self.assertEqual(
            list(fieldset.field_memberships.values_list("custom_field__name", "position")),
            [("processor_model", 10), ("core_count", 20), ("memory_capacity", 30), ("memory_type", 40)],
        )

    def test_seed_catalog_writes_complete_normative_composition(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        canonical = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "scripts"
                / "tests"
                / "fixtures"
                / "specification_vocabulary"
                / "canonical-target.json"
            ).read_text(encoding="utf-8")
        )
        expected_fields = {row["key"]: row for row in canonical["active_fields"] + canonical["reserved_retired_fields"]}
        expected_choice_sets = {row["slug"]: row for row in canonical["choice_sets"]}
        expected_fieldsets = {row["slug"]: row for row in canonical["sections"]}
        choice_ids = dict(CustomFieldChoiceSet.objects.filter(namespace="itambox").values_list("slug", "pk"))
        command._seed_catalog()
        self.assertEqual(
            dict(CustomFieldChoiceSet.objects.filter(namespace="itambox").values_list("slug", "pk")), choice_ids
        )
        self.assertEqual(
            CustomField.objects.filter(namespace="itambox", management_kind=CustomField.MANAGEMENT_CORE).count(),
            len(expected_fields),
        )
        self.assertEqual(CustomFieldChoiceSet.objects.filter(namespace="itambox").count(), len(expected_choice_sets))
        self.assertEqual(CustomFieldset.objects.filter(namespace="itambox").count(), len(expected_fieldsets))
        for field in CustomField.objects.filter(namespace="itambox", management_kind=CustomField.MANAGEMENT_CORE):
            expected = expected_fields[field.name]
            self.assertFalse(field.required)
            self.assertEqual(field.activation, expected["activation"])
            self.assertEqual(field.lifecycle, expected["lifecycle"])
            expected_models = {"assettype" if target == "asset_type" else target for target in expected["targets"]}
            self.assertEqual(set(field.object_types.values_list("model", flat=True)), expected_models)
            expected_choice_slug = (
                expected["choice_set"].rsplit("/", 1)[1] if expected["choice_set"] is not None else None
            )
            expected_choice_id = (
                CustomFieldChoiceSet.objects.get(namespace="itambox", slug=expected_choice_slug).pk
                if expected_choice_slug is not None
                else None
            )
            self.assertEqual(field.choice_set_id, expected_choice_id)
            if field.field_type == CustomField.FIELD_TYPE_SINGLE_SELECT:
                self.assertEqual(field.max_values, 1)
        asset_type = AssetType.objects.get(slug="dell-latitude-5550")
        self.assertFalse(hasattr(asset_type, "custom_fieldset"))
        self.assertGreater(asset_type.fieldset_memberships.count(), 1)
        expected_asset_type_fieldsets = [
            (slug, index) for index, slug in enumerate(command._category_fieldsets["laptops"], start=1)
        ]
        self.assertEqual(
            list(asset_type.fieldset_memberships.values_list("fieldset__slug", "position")),
            expected_asset_type_fieldsets,
        )
        expected_category_fieldsets = [
            (item["fieldset"].rsplit("/", 1)[1], item["position"])
            for item in next((row for row in canonical["categories"] if row["slug"] == "laptops"))["default_fieldsets"]
        ]
        self.assertEqual(
            list(
                Category.objects.get(slug="laptops").default_fieldset_memberships.values_list(
                    "fieldset__slug", "position"
                )
            ),
            expected_category_fieldsets,
        )
        stored_keys = set(asset_type.custom_field_data)
        resolved_keys = {item.definition.name for item in resolve_asset_type_custom_fields(asset_type)}
        self.assertTrue(stored_keys)
        self.assertTrue(stored_keys.issubset(resolved_keys))
        self.assertFalse(stored_keys & {"cpu", "ram_gb", "storage_gb", "storage_type", "os_version"})

    def test_seed_catalog_validates_field_before_reconciling_object_types(self):

        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        field = CustomField.objects.get(name="form_factor")
        asset_ct = ContentType.objects.get(app_label="assets", model="asset")
        field.object_types.set([asset_ct])
        CustomField.objects.filter(pk=field.pk).update(field_type=CustomField.FIELD_TYPE_TEXT)
        with self.assertRaisesRegex(ValueError, "Core field semantics differ for identity: form_factor"):
            command._seed_catalog()
        field.refresh_from_db()
        self.assertEqual(set(field.object_types.values_list("model", flat=True)), {"asset"})

    def test_seed_catalog_refuses_local_choice_set_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        choice_set = CustomFieldChoiceSet.objects.get(namespace="itambox", slug="form-factor")
        choice_set.management_kind = CustomFieldChoiceSet.MANAGEMENT_LOCAL
        choice_set.save(update_fields=["management_kind"])
        with self.assertRaisesRegex(ValueError, "management"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_allows_local_choice_set_same_slug(self):
        CustomFieldChoiceSet.objects.create(
            namespace="local",
            slug="form-factor",
            label="Local form factor",
            management_kind=CustomFieldChoiceSet.MANAGEMENT_LOCAL,
            lifecycle=CustomFieldChoiceSet.LIFECYCLE_ACTIVE,
        )
        SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()
        self.assertTrue(CustomFieldChoiceSet.objects.filter(namespace="local", slug="form-factor").exists())
        self.assertTrue(CustomFieldChoiceSet.objects.filter(namespace="itambox", slug="form-factor").exists())

    def test_seed_catalog_refuses_local_field_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        field = CustomField.objects.get(name="processor_model")
        field.management_kind = CustomField.MANAGEMENT_LOCAL
        field.save(update_fields=["namespace", "management_kind"])
        with self.assertRaisesRegex(ValueError, "management"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_refuses_local_fieldset_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        fieldset = CustomFieldset.objects.get(namespace="itambox", slug="compute-memory")
        fieldset.management_kind = CustomFieldset.MANAGEMENT_LOCAL
        fieldset.save(update_fields=["management_kind"])
        with self.assertRaisesRegex(ValueError, "management"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_allows_local_fieldset_same_slug(self):
        CustomFieldset.objects.create(
            namespace="local",
            slug="compute-memory",
            label="Local compute memory",
            management_kind=CustomFieldset.MANAGEMENT_LOCAL,
            lifecycle=CustomFieldset.LIFECYCLE_ACTIVE,
        )
        SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()
        self.assertTrue(CustomFieldset.objects.filter(namespace="local", slug="compute-memory").exists())
        self.assertTrue(CustomFieldset.objects.filter(namespace="itambox", slug="compute-memory").exists())

    def test_seed_catalog_refuses_inactive_core_field_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        field = CustomField.objects.get(name="processor_model")
        field.lifecycle = CustomField.LIFECYCLE_DEPRECATED
        field.save(update_fields=["lifecycle"])
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_refuses_inactive_core_choice_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        choice_set = CustomFieldChoiceSet.objects.get(namespace="itambox", slug="form-factor")
        choice = choice_set.choices.get(key="notebook")
        choice.lifecycle = CustomFieldChoice.LIFECYCLE_DEPRECATED
        choice.save(update_fields=["lifecycle"])
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_refuses_unexpected_choice_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        choice_set = CustomFieldChoiceSet.objects.get(namespace="itambox", slug="form-factor")
        CustomFieldChoice.objects.create(
            choice_set=choice_set,
            key="unexpected-core",
            label="Unexpected local",
            position=999,
            version=1,
            lifecycle=CustomFieldChoice.LIFECYCLE_ACTIVE,
        )
        with self.assertRaisesRegex(ValueError, "Choice identity"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_refuses_unexpected_fieldset_membership(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        fieldset = CustomFieldset.objects.get(namespace="itambox", slug="compute-memory")
        unexpected = CustomField.objects.create(
            name="unexpected_fieldset_child",
            label="Unexpected fieldset child",
            field_type="text",
            activation=CustomField.ACTIVATION_COMPOSED,
            namespace="itambox",
            management_kind=CustomField.MANAGEMENT_CORE,
            version=1,
            lifecycle=CustomField.LIFECYCLE_ACTIVE,
        )
        CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=unexpected, position=999)
        with self.assertRaisesRegex(ValueError, "unexpected membership"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_catalog_refuses_inactive_core_fieldset_identity(self):
        command = SeedDataCommand(stdout=self.stdout, stderr=self.stderr)
        command._seed_catalog()
        fieldset = CustomFieldset.objects.get(namespace="itambox", slug="compute-memory")
        fieldset.lifecycle = CustomFieldset.LIFECYCLE_DEPRECATED
        fieldset.save(update_fields=["lifecycle"])
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            SeedDataCommand(stdout=self.stdout, stderr=self.stderr)._seed_catalog()

    def test_seed_access_invariant_requires_admin_memberships_but_exempts_admin_holders(self):
        tenant = Tenant.objects.create(name="Seed Invariant Tenant", slug="seed-invariant-tenant")
        admin = User.objects.create_user(username="admin", password="password")
        org_admin = User.objects.create_user(username="admin@example.com", password="password")
        for user in (admin, org_admin):
            with self.subTest(user=user.username), pytest.raises(CommandError, match="no active membership"):
                check_seed_access_invariants([user])
            Membership._base_manager.create(user=user, tenant=tenant, is_active=True)
        check_seed_access_invariants([admin, org_admin])
        passwordless = User.objects.create(username="passwordless@example.com")
        passwordless.set_unusable_password()
        passwordless.save(update_fields=["password"])
        check_seed_access_invariants([passwordless])
        named_person = User.objects.create_user(
            username="named.person@example.com",
            email="named.person@example.com",
            first_name="Named",
            last_name="Person",
            password="password",
        )
        membership = Membership._base_manager.create(user=named_person, tenant=tenant, is_active=True)
        with pytest.raises(CommandError, match="named.person@example.com: 0 active AssetHolder profiles"):
            check_seed_access_invariants([named_person])
        membership.delete()
        AssetHolder._base_manager.create(
            tenant=tenant,
            user=named_person,
            first_name="Named",
            last_name="Person",
            upn=named_person.username,
            email=named_person.email,
        )
        with pytest.raises(CommandError, match="named.person@example.com: no active membership"):
            check_seed_access_invariants([named_person])
        Membership._base_manager.create(user=named_person, tenant=tenant, is_active=True)
        check_seed_access_invariants([named_person])

    def test_seed_data_refuses_to_wipe_without_force_when_not_debug(self):
        from django.test import override_settings

        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_data", production=True, stdout=self.stdout, stderr=self.stderr)

    def test_sync_tenant_ldap_command_invalid(self):
        with self.assertRaises(CommandError):
            call_command("sync_tenant_ldap", tenant="non-existent-tenant")


class SeedOperationalInvariantTestCase(TransactionTestCase):
    """The #506 self-check must fail closed on each operational-story contradiction.

    Each test builds the minimal row that violates one acceptance criterion, then
    asserts the check rejects it. They double as the sabotage runs: with the matching
    seed fix reverted, the seeded row is exactly what the check must refuse.
    """

    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="Invariant Tenant", slug="invariant-tenant")
        self.manufacturer = Manufacturer.objects.create(name="Invariant Vendor", slug="invariant-vendor")
        self.category = Category.objects.create(name="Invariant Category", slug="invariant-category")
        self.asset_role = AssetRole.objects.create(name="Invariant Endpoint", slug="invariant-endpoint")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            category=self.category,
            model="Invariant Laptop",
            slug="invariant-laptop",
            asset_role=self.asset_role,
            requestable=True,
        )
        self.site = Site.objects.create(name="Invariant Site", slug="invariant-site")
        self.location = Location.objects.create(
            name="Invariant HQ", slug="invariant-hq", tenant=self.tenant, site=self.site
        )
        self.holder = AssetHolder._base_manager.create(
            tenant=self.tenant,
            first_name="Invariant",
            last_name="Holder",
            email="invariant.holder@example.com",
            upn="invariant.holder@example.com",
        )
        # The status labels are migration-seeded reference data (assets.0003), so
        # fetch them by slug instead of creating them: re-creating them violates
        # unique_statuslabel_name_active on any already-migrated database.
        self.available = StatusLabel._base_manager.get(slug="available")
        self.in_use = StatusLabel._base_manager.get(slug="in-use")
        self.pending_repair = StatusLabel._base_manager.get(slug="pending-repair")
        self.requester = User.objects.create_user(username="requester@example.com", password="password")
        Membership._base_manager.create(user=self.requester, tenant=self.tenant, is_active=True)

    def _asset(self, **kwargs):
        defaults = dict(
            name="Invariant Laptop",
            asset_type=self.asset_type,
            tenant=self.tenant,
            location=self.location,
            status=self.available,
            purchase_date=datetime.date.today() - datetime.timedelta(days=400),
        )
        defaults.update(kwargs)
        return Asset._base_manager.create(**defaults)

    def test_coherent_rows_pass(self):
        check_seed_operational_invariants()

    def _received_line(self, order_number, qty):
        supplier = Supplier.objects.create(name=f"Supplier {order_number}", slug=f"supplier-{order_number.lower()}")
        po = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number=order_number,
            supplier=supplier,
            status="received",
            order_date=datetime.date.today() - datetime.timedelta(days=30),
            destination_location=self.location,
        )
        return PurchaseOrderLine.objects.create(
            purchase_order=po,
            tenant=self.tenant,
            asset_type=self.asset_type,
            qty_ordered=qty,
            qty_received=qty,
            unit_price=1000,
        )

    def test_po_line_under_materialised_assets_fails(self):
        # The #506 bug: a line received with 10 units but only 3 assets in the ledger.
        line = self._received_line("INV-PO-1", 10)
        for _ in range(3):
            self._asset(purchase_order_line=line)
        with self.assertRaisesRegex(CommandError, "reports 10 received but only 3 asset"):
            check_seed_operational_invariants()

    def test_po_line_fully_materialised_passes(self):
        line = self._received_line("INV-PO-2", 4)
        for _ in range(4):
            self._asset(purchase_order_line=line)
        check_seed_operational_invariants()

    def test_repair_history_on_held_asset_fails(self):
        """A repair recorded in the change log with no preceding check-in is refused."""
        asset = self._asset(status=self.in_use)
        AssetAssignment.objects.create(
            asset=asset,
            assigned_user=self.holder,
            is_active=True,
            notes="Provisioned.",
        )
        # The #506 bug: history walks the unit into repair and back while the same
        # person holds it throughout (the assignment never gets a checked_in_at).
        _log_status_change(asset, self.pending_repair, "update", days_ago=60)
        _log_status_change(asset, self.available, "update", days_ago=30)
        with self.assertRaisesRegex(CommandError, "without a preceding check-in"):
            check_seed_operational_invariants()

    def test_repair_history_with_checkin_passes(self):
        """The #506 fix: the unit is checked in before repair and handed back after."""
        asset = self._asset(status=self.in_use)
        assignment = AssetAssignment.objects.create(
            asset=asset,
            assigned_user=self.holder,
            is_active=True,
            notes="Provisioned.",
        )
        AssetAssignment._base_manager.filter(pk=assignment.pk).update(
            is_active=False, checked_in_at=timezone.now() - datetime.timedelta(days=70)
        )
        AssetAssignment.objects.create(
            asset=asset,
            assigned_user=self.holder,
            is_active=True,
            notes="Returned after repair.",
        )
        _log_status_change(asset, self.pending_repair, "update", days_ago=60)
        _log_status_change(asset, self.available, "update", days_ago=30)
        _log_status_change(asset, self.in_use, "update", days_ago=29)
        check_seed_operational_invariants()

    def test_incomplete_maintenance_fails(self):
        """A completed record with no completion date is the #506 maintenance bug."""
        asset = self._asset()
        AssetMaintenance._base_manager.create(
            asset=asset,
            maintenance_type="upgrade",
            status="completed",
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            completion_date=None,
        )
        with self.assertRaisesRegex(CommandError, "has no completion date"):
            check_seed_operational_invariants()

    def test_out_of_service_maintenance_without_episode_fails(self):
        """A repair record that belongs to no episode cannot show in the timeline."""
        asset = self._asset()
        AssetMaintenance._base_manager.create(
            asset=asset,
            maintenance_type="repair",
            status="completed",
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            completion_date=datetime.date.today() - datetime.timedelta(days=8),
        )
        with self.assertRaisesRegex(CommandError, "belongs to no repair episode"):
            check_seed_operational_invariants()

    def test_grouped_maintenance_passes(self):
        asset = self._asset()
        episode = RepairEpisode.objects.create(asset=asset, notes="Invariant repair story.")
        AssetMaintenance._base_manager.create(
            asset=asset,
            maintenance_type="repair",
            status="completed",
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            completion_date=datetime.date.today() - datetime.timedelta(days=8),
            episode=episode,
        )
        check_seed_operational_invariants()

    def test_approved_request_without_asset_fails(self):
        """An approved request with no allocation can never be claimed (#506)."""
        AssetRequest._base_manager.create(
            tenant=self.tenant,
            requester=self.requester,
            asset_type=self.asset_type,
            status="approved",
        )
        with self.assertRaisesRegex(CommandError, "has no allocated asset"):
            check_seed_operational_invariants()

    def test_approved_request_with_deployable_asset_passes(self):
        asset = self._asset()
        AssetRequest._base_manager.create(
            tenant=self.tenant,
            requester=self.requester,
            asset_type=self.asset_type,
            asset=asset,
            status="approved",
        )
        check_seed_operational_invariants()

    def test_pending_request_must_stay_unallocated(self):
        asset = self._asset()
        AssetRequest._base_manager.create(
            tenant=self.tenant,
            requester=self.requester,
            asset_type=self.asset_type,
            asset=asset,
            status="pending",
        )
        with self.assertRaisesRegex(CommandError, "already carries an allocated asset"):
            check_seed_operational_invariants()


def _log_status_change(asset, status_label, action, days_ago=0):
    """Write one change-log entry recording ``asset`` as being in ``status_label``."""
    content_type = ContentType.objects.get_for_model(Asset)
    ObjectChange._base_manager.create(
        tenant=asset.tenant,
        time=timezone.now() - datetime.timedelta(days=days_ago),
        action=action,
        changed_object_type=content_type,
        changed_object_id=asset.pk,
        object_repr=str(asset)[:200],
        object_type_repr=f"{content_type.app_label} | {content_type.model}",
        prechange_data={},
        # serialize_object stores relations as bare pks, so the recorded status is
        # the StatusLabel primary key.
        postchange_data={"status": status_label.pk},
        request_id=uuid.uuid4(),
    )


class SyncTenantLDAPDependencyTest(SimpleTestCase):
    @override_settings(
        ITAMBOX_TENANT_LDAP_CONFIGS={
            "test": {
                "SERVER_URI": "ldap://127.0.0.1",
                "BIND_DN": "cn=bind,dc=example,dc=test",
                "BIND_PASSWORD": "test",
                "USER_SEARCH_BASE": "ou=users,dc=example,dc=test",
                "USER_SEARCH_FILTER": "(uid=%(user)s)",
            }
        }
    )
    @patch("core.management.commands.sync_tenant_ldap.django_auth_ldap_installed", False)
    @patch("core.management.commands.sync_tenant_ldap.ldap.initialize")
    def test_sync_tenant_ldap_requires_locked_native_dependencies(self, mock_ldap_init):
        stdout = io.StringIO()
        command = SyncTenantLDAPCommand(stdout=stdout)
        with self.assertRaisesRegex(CommandError, "locked Linux/WSL or Docker environment"):
            command._run_sync(SimpleNamespace(pk=1, slug="test", name="Test"))
        self.assertNotIn("Connecting to LDAP server", stdout.getvalue())
        mock_ldap_init.assert_not_called()
