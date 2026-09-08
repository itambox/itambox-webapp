import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from assets.customfields import resolve_asset_custom_fields, resolve_asset_type_custom_fields
from assets.forms.asset_form import AssetForm
from assets.forms.assettype_form import AssetTypeForm
from assets.models import Asset, AssetType, Category
from core.management.commands._seed.access import check_seed_access_invariants
from core.management.commands._seed.catalog import (
    _get_core_fieldset,
    _reconcile_core_choice_rows,
    _reconcile_core_fields,
    _reconcile_core_fieldsets,
)
from core.management.commands._seed.inventory import check_seed_inventory_invariants
from core.management.commands.seed_data import Command as SeedDataCommand
from core.management.commands.sync_tenant_ldap import Command as SyncTenantLDAPCommand
from core.models import EmailSettings, Job
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentAllocation,
    ComponentStock,
    Consumable,
    ConsumableAssignment,
    ConsumableStock,
)
from licenses.models import License
from organization.models import AssetHolder, Membership, Tenant
from subscriptions.models import SubscriptionAssignment

User = get_user_model()


class CatalogConflictValidationTestCase(TestCase):
    """Exercise catalog conflict contracts without seeding the full demo catalog."""

    def setUp(self):
        self.choice_set = CustomFieldChoiceSet.objects.create(
            namespace="itambox",
            slug="fixture-choice-set",
            label="Fixture Choice Set",
            management_kind=CustomFieldChoiceSet.MANAGEMENT_CORE,
            lifecycle=CustomFieldChoiceSet.LIFECYCLE_ACTIVE,
        )
        self.choice = CustomFieldChoice.objects.create(
            choice_set=self.choice_set,
            key="fixture_choice",
            label="Fixture choice",
            position=10,
            version=1,
            lifecycle=CustomFieldChoice.LIFECYCLE_DEPRECATED,
        )
        self.field = CustomField.objects.create(
            name="fixture_field",
            namespace="itambox",
            label="Fixture field",
            help_text="",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_CORE,
            version=1,
            lifecycle=CustomField.LIFECYCLE_DEPRECATED,
        )
        self.fieldset = CustomFieldset.objects.create(
            namespace="itambox",
            slug="fixture-fieldset",
            label="Fixture fieldset",
            description="",
            management_kind=CustomFieldset.MANAGEMENT_CORE,
            version=1,
            lifecycle=CustomFieldset.LIFECYCLE_DEPRECATED,
        )

    def test_active_choice_identity_rejects_deprecated_existing_choice(self):
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            _reconcile_core_choice_rows(
                self.choice_set,
                "fixture-choice-set",
                [
                    {
                        "key": self.choice.key,
                        "label": self.choice.label,
                        "position": 10,
                        "lifecycle": CustomFieldChoice.LIFECYCLE_ACTIVE,
                    }
                ],
            )

        self.choice.refresh_from_db()
        self.assertEqual(self.choice.lifecycle, CustomFieldChoice.LIFECYCLE_DEPRECATED)

    def test_deprecated_choice_identity_remains_reconcilable(self):
        self.choice.lifecycle = CustomFieldChoice.LIFECYCLE_ACTIVE
        self.choice.save(update_fields=["lifecycle"])
        _reconcile_core_choice_rows(
            self.choice_set,
            "fixture-choice-set",
            [
                {
                    "key": self.choice.key,
                    "label": self.choice.label,
                    "position": 10,
                    "lifecycle": CustomFieldChoice.LIFECYCLE_DEPRECATED,
                }
            ],
        )

        self.choice.refresh_from_db()
        self.assertEqual(self.choice.lifecycle, CustomFieldChoice.LIFECYCLE_DEPRECATED)

    def _field_row(self, lifecycle):
        return {
            "identity": "itambox/fixture_field",
            "namespace": "itambox",
            "key": "fixture_field",
            "label": "Fixture field",
            "help_text": "",
            "targets": ["asset"],
            "activation": CustomField.ACTIVATION_COMPOSED,
            "field_type": CustomField.FIELD_TYPE_TEXT,
            "quantity_kind": None,
            "canonical_unit": None,
            "validation": {},
            "required": False,
            "nullable": False,
            "lifecycle": lifecycle,
            "choice_set": None,
        }

    def test_active_field_identity_rejects_deprecated_existing_field(self):
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            _reconcile_core_fields(
                [self._field_row(CustomField.LIFECYCLE_ACTIVE)],
                {},
                ContentType.objects.get_for_model(Asset),
                ContentType.objects.get_for_model(AssetType),
                1,
            )

        self.field.refresh_from_db()
        self.assertEqual(self.field.lifecycle, CustomField.LIFECYCLE_DEPRECATED)

    def test_deprecated_field_identity_remains_reconcilable(self):
        # Model save validates applicability; update represents an existing legacy row.
        CustomField.objects.filter(pk=self.field.pk).update(lifecycle=CustomField.LIFECYCLE_ACTIVE)
        _reconcile_core_fields(
            [self._field_row(CustomField.LIFECYCLE_DEPRECATED)],
            {},
            ContentType.objects.get_for_model(Asset),
            ContentType.objects.get_for_model(AssetType),
            1,
        )

        self.field.refresh_from_db()
        self.assertEqual(self.field.lifecycle, CustomField.LIFECYCLE_DEPRECATED)

    def test_active_fieldset_identity_rejects_deprecated_existing_fieldset(self):
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            _get_core_fieldset(
                "fixture-fieldset",
                "Fixture fieldset",
                "",
                CustomFieldset.LIFECYCLE_ACTIVE,
                1,
                "itambox",
            )

        self.fieldset.refresh_from_db()
        self.assertEqual(self.fieldset.lifecycle, CustomFieldset.LIFECYCLE_DEPRECATED)

    def test_local_fieldset_membership_raises_ownership_collision_before_reconcile(self):
        fieldset = CustomFieldset.objects.create(
            namespace="itambox",
            slug="ownership-fieldset",
            label="Ownership fieldset",
            description="",
            management_kind=CustomFieldset.MANAGEMENT_CORE,
            version=1,
            lifecycle=CustomFieldset.LIFECYCLE_ACTIVE,
        )
        local_field = CustomField.objects.create(
            name="local_membership_field",
            namespace="local",
            label="Local membership field",
            field_type=CustomField.FIELD_TYPE_TEXT,
            activation=CustomField.ACTIVATION_COMPOSED,
            management_kind=CustomField.MANAGEMENT_LOCAL,
            version=1,
            lifecycle=CustomField.LIFECYCLE_ACTIVE,
        )
        membership = CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=local_field, position=999)

        with self.assertRaisesRegex(ValueError, "ownership collision"):
            _reconcile_core_fieldsets(
                [
                    {
                        "namespace": "itambox",
                        "slug": "ownership-fieldset",
                        "label": "Ownership fieldset",
                        "description": "",
                        "lifecycle": CustomFieldset.LIFECYCLE_ACTIVE,
                        "memberships": [],
                    }
                ],
                {},
                1,
            )

        self.assertTrue(CustomFieldsetField.objects.filter(pk=membership.pk).exists())


# Database names, caches, media roots, and queue execution are isolated per xdist
# process by itambox/conftest.py. Only the seed-data methods below retain the
# serial-only marker because seed_data.handle() resets process-global random state.
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
        # Let the model produce a valid Fernet ciphertext. A literal ``enc$abc``
        # is malformed encrypted data and the rotation command now correctly
        # fails closed instead of reporting a false-success dry run.
        License.objects.create(name="Office 365", software=software, product_key="abc")
        call_command("rotate_encryption_keys", dry_run=True, stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Scanning for encrypted fields", self.stdout.getvalue())

    def test_run_jobs_command(self):
        # Create a pending job
        Job.objects.create(name="Script: my_script.py", status=Job.STATUS_PENDING)
        call_command("run_jobs", stdout=self.stdout, stderr=self.stderr)
        self.assertIn("Job processing complete", self.stdout.getvalue())

    @pytest.mark.serial_only
    def test_seed_data_command(self):
        # Run seed data with --production option to verify minimal bootstrap execution paths.
        # --force is required because seed_data refuses to clear data when DEBUG is off
        # (the guard that prevents an accidental production wipe).
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
            [
                ("processor_model", 10),
                ("core_count", 20),
                ("memory_capacity", 30),
                ("memory_type", 40),
            ],
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
            dict(CustomFieldChoiceSet.objects.filter(namespace="itambox").values_list("slug", "pk")),
            choice_ids,
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
            for item in next(row for row in canonical["categories"] if row["slug"] == "laptops")["default_fieldsets"]
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
        from django.contrib.contenttypes.models import ContentType

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

    @pytest.mark.serial_only
    def test_full_seed_data_keeps_subscription_assignments_within_tenant(self):
        with override_settings(SEED_PASSWORD="configured-seed-password"):
            call_command("seed_data", force=True, stdout=self.stdout, stderr=self.stderr)

        assignments = list(SubscriptionAssignment._base_manager.select_related("subscription"))
        self.assertGreater(len(assignments), 0)
        for assignment in assignments:
            target = assignment._resolve_assigned_object_unscoped()
            self.assertIsNotNone(target)
            self.assertEqual(target.tenant_id, assignment.subscription.tenant_id)

        lars = User.objects.get(username="lars.eklund")
        self.assertTrue(lars.check_password("configured-seed-password"))
        check_seed_access_invariants()

        seeded_people = User.objects.exclude(username="admin").exclude(username__startswith="admin@")
        self.assertGreater(seeded_people.count(), 0)
        for user in seeded_people:
            self.assertTrue(user.has_usable_password())
            memberships = Membership._base_manager.filter(user=user, is_active=True)
            holders = AssetHolder._base_manager.filter(user=user, deleted_at__isnull=True)
            self.assertEqual(memberships.count(), 1, user.username)
            self.assertEqual(holders.count(), 1, user.username)
            self.assertEqual(holders.get().tenant_id, memberships.get().tenant_id, user.username)

        check_seed_inventory_invariants()
        self.assertGreater(Component._base_manager.count(), 0)
        self.assertGreater(Accessory._base_manager.count(), 0)
        self.assertGreater(Consumable._base_manager.count(), 0)

        for component in Component._base_manager.all():
            total_stock = sum(ComponentStock._base_manager.filter(component=component).values_list("qty", flat=True))
            allocated = sum(
                ComponentAllocation._base_manager.filter(
                    component=component,
                    deleted_at__isnull=True,
                ).values_list("qty", flat=True)
            )
            self.assertGreaterEqual(total_stock, allocated, component.pk)
            self.assertEqual(component.available, total_stock - allocated)

        asset_type = AssetType._base_manager.get(slug="dell-latitude-5550")
        asset = Asset._base_manager.filter(asset_type=asset_type).first()
        self.assertIsNotNone(asset)
        asset_type_form = AssetTypeForm(instance=asset_type)
        asset_form = AssetForm(instance=asset)
        self.assertIn("cf_processor_model", asset_type_form.fields)
        self.assertIn("cf_memory_capacity", asset_type_form.fields)
        self.assertIn("cf_hostname", asset_form.fields)
        self.assertIn("cf_operating_system_family", asset_form.fields)
        self.assertTrue({item.definition.name for item in resolve_asset_type_custom_fields(asset_type)})
        self.assertTrue(
            {item.definition.name for item in resolve_asset_custom_fields(asset_type, asset.custom_field_data)}
        )
        self.assertTrue(
            set(asset.custom_field_data).issubset(
                {item.definition.name for item in resolve_asset_custom_fields(asset_type, asset.custom_field_data)}
            )
        )

        for item, assignment_model, stock_model, field in (
            (Accessory, AccessoryAssignment, AccessoryStock, "accessory"),
            (Consumable, ConsumableAssignment, ConsumableStock, "consumable"),
        ):
            for inventory_item in item._base_manager.all():
                total_stock = sum(
                    stock_model._base_manager.filter(**{field: inventory_item}).values_list("qty", flat=True)
                )
                assignments = assignment_model._base_manager.filter(
                    **{field: inventory_item, "deleted_at__isnull": True}
                )
                target_only = sum(assignments.filter(from_location__isnull=True).values_list("qty", flat=True))
                self.assertGreaterEqual(total_stock, target_only, inventory_item.pk)
                self.assertEqual(
                    inventory_item.available,
                    max(0, total_stock - target_only),
                    inventory_item.pk,
                )

        admin_accounts = User.objects.filter(username="admin") | User.objects.filter(username__startswith="admin@")
        self.assertGreater(admin_accounts.count(), 0)
        for user in admin_accounts:
            self.assertTrue(
                Membership._base_manager.filter(user=user, is_active=True).exists(),
                user.username,
            )
            self.assertFalse(
                AssetHolder._base_manager.filter(user=user, deleted_at__isnull=True).exists(),
                user.username,
            )

        allocation = ComponentAllocation._base_manager.filter(deleted_at__isnull=True).first()
        self.assertIsNotNone(allocation)
        ComponentStock._base_manager.filter(component_id=allocation.component_id).update(qty=0)
        with self.assertRaisesRegex(CommandError, "allocates"):
            check_seed_inventory_invariants()

        ComponentStock._base_manager.filter(component_id=allocation.component_id).update(qty=-1)
        with self.assertRaisesRegex(CommandError, "negative stock"):
            check_seed_inventory_invariants()

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

    @pytest.mark.serial_only
    def test_seed_data_refuses_to_wipe_without_force_when_not_debug(self):
        # The destructive clear must be blocked outside DEBUG unless --force is passed.
        from django.test import override_settings

        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_data", production=True, stdout=self.stdout, stderr=self.stderr)

    def test_sync_tenant_ldap_command_invalid(self):
        with self.assertRaises(CommandError):
            call_command("sync_tenant_ldap", tenant="non-existent-tenant")


class SyncTenantLDAPDependencyTest(SimpleTestCase):
    @override_settings(
        ITAMBOX_TENANT_LDAP_CONFIGS={
            "test": {
                "SERVER_URI": "ldap://127.0.0.1",
                "BIND_DN": "cn=bind,dc=example,dc=test",
                "BIND_PASSWORD": "test",
                "USER_SEARCH_BASE": "ou=users,dc=example,dc=test",
                "USER_SEARCH_FILTER": "(uid=%(user)s)",
            },
        }
    )
    @patch(
        "core.management.commands.sync_tenant_ldap.django_auth_ldap_installed",
        False,
    )
    @patch("core.management.commands.sync_tenant_ldap.ldap.initialize")
    def test_sync_tenant_ldap_requires_locked_native_dependencies(self, mock_ldap_init):
        stdout = io.StringIO()
        command = SyncTenantLDAPCommand(stdout=stdout)

        with self.assertRaisesRegex(
            CommandError,
            "locked Linux/WSL or Docker environment",
        ):
            command._run_sync(SimpleNamespace(pk=1, slug="test", name="Test"))

        self.assertNotIn("Connecting to LDAP server", stdout.getvalue())
        mock_ldap_init.assert_not_called()
