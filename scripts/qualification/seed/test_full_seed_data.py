"""Full demo-seed qualification (scripts/qualification/seed/). Run explicitly:

PYTHONPATH=itambox pytest scripts/qualification/seed/
"""

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


class FullSeedDataQualificationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()


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
            ComponentAllocation._base_manager.filter(component=component, deleted_at__isnull=True).values_list(
                "qty", flat=True
            )
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
    self.assertTrue({item.definition.name for item in resolve_asset_custom_fields(asset_type, asset.custom_field_data)})
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
            total_stock = sum(stock_model._base_manager.filter(**{field: inventory_item}).values_list("qty", flat=True))
            assignments = assignment_model._base_manager.filter(**{field: inventory_item, "deleted_at__isnull": True})
            target_only = sum(assignments.filter(from_location__isnull=True).values_list("qty", flat=True))
            self.assertGreaterEqual(total_stock, target_only, inventory_item.pk)
            self.assertEqual(inventory_item.available, max(0, total_stock - target_only), inventory_item.pk)
    admin_accounts = User.objects.filter(username="admin") | User.objects.filter(username__startswith="admin@")
    self.assertGreater(admin_accounts.count(), 0)
    for user in admin_accounts:
        self.assertTrue(Membership._base_manager.filter(user=user, is_active=True).exists(), user.username)
        self.assertFalse(AssetHolder._base_manager.filter(user=user, deleted_at__isnull=True).exists(), user.username)
    allocation = ComponentAllocation._base_manager.filter(deleted_at__isnull=True).first()
    self.assertIsNotNone(allocation)
    ComponentStock._base_manager.filter(component_id=allocation.component_id).update(qty=0)
    with self.assertRaisesRegex(CommandError, "allocates"):
        check_seed_inventory_invariants()
    ComponentStock._base_manager.filter(component_id=allocation.component_id).update(qty=-1)
    with self.assertRaisesRegex(CommandError, "negative stock"):
        check_seed_inventory_invariants()
