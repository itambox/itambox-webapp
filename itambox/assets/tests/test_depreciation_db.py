"""
DB-backed integration tests for depreciation v2:
  - archive freezes disposal_value; further time doesn't change it
  - un-archive clears disposal_value
  - in_service_date shifts the depreciation clock
  - resolution chain (override → tenant → type) via real FK objects
"""
import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from assets.depreciation import compute_book_value, resolve_policy
from assets.models import Asset, AssetType, Depreciation, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin
from django.test import TestCase


pytestmark = pytest.mark.django_db


def _make_policy(name, months=36, method='straight_line',
                 convention='exclude_purchase_month', threshold=None):
    return Depreciation.objects.create(
        name=name, months=months, method=method,
        convention=convention, immediate_expense_threshold=threshold,
    )


def _make_asset(name, asset_type, status, purchase_cost=1200,
                salvage_value=0, purchase_date=None, in_service_date=None,
                tenant=None, depreciation_override=None):
    if purchase_date is None:
        purchase_date = datetime.date(2022, 1, 1)
    return Asset.objects.create(
        name=name,
        asset_tag=f'TAG-{name[:5].upper()}',
        asset_type=asset_type,
        status=status,
        purchase_cost=Decimal(str(purchase_cost)),
        salvage_value=Decimal(str(salvage_value)),
        purchase_date=purchase_date,
        in_service_date=in_service_date,
        tenant=tenant,
        depreciation_override=depreciation_override,
    )


class TestDisposalFreeze(TenantTestMixin, TestCase):

    def setUp(self):
        self.setup_tenant_context()
        self.set_active_tenant(self.tenant)

        self.mfg = Manufacturer.objects.create(name='Acme', slug='acme')
        self.policy = _make_policy('Test 36M', months=36)
        self.asset_type = AssetType.objects.create(
            manufacturer=self.mfg, model='WidgetPro', slug='acme-widgetpro',
            depreciation=self.policy,
        )
        self.status_deployable, _ = StatusLabel.objects.get_or_create(
            slug='available',
            defaults={'name': 'Available', 'type': 'deployable', 'color': '28a745'},
        )
        self.status_archived, _ = StatusLabel.objects.get_or_create(
            slug='retired',
            defaults={'name': 'Retired', 'type': 'archived', 'color': 'dc3545'},
        )

    def test_archive_freezes_disposal_value(self):
        asset = _make_asset('Widget 1', self.asset_type, self.status_deployable,
                            purchase_cost=1200, salvage_value=0,
                            purchase_date=datetime.date(2022, 1, 1),
                            tenant=self.tenant)
        # Verify it has no disposal value yet.
        self.assertIsNone(asset.disposed_at)
        self.assertIsNone(asset.disposal_value)

        # Transition to archived.
        asset.status = self.status_archived
        asset.save()
        asset.refresh_from_db()

        self.assertIsNotNone(asset.disposed_at)
        self.assertIsNotNone(asset.disposal_value)
        # Value should be non-None and a valid decimal.
        self.assertIsInstance(asset.disposal_value, Decimal)

    def test_further_time_does_not_change_frozen_value(self):
        asset = _make_asset('Widget 2', self.asset_type, self.status_deployable,
                            purchase_cost=1200, salvage_value=0,
                            purchase_date=datetime.date(2022, 1, 1),
                            tenant=self.tenant)
        asset.status = self.status_archived
        asset.save()
        asset.refresh_from_db()

        frozen_value = asset.disposal_value
        # compute_book_value far in the future should still return frozen value.
        future_date = datetime.date(2050, 1, 1)
        result = compute_book_value(asset, on_date=future_date)
        self.assertEqual(result, frozen_value)

    def test_unarchive_clears_disposal_value(self):
        asset = _make_asset('Widget 3', self.asset_type, self.status_deployable,
                            purchase_cost=1200, salvage_value=0,
                            purchase_date=datetime.date(2022, 1, 1),
                            tenant=self.tenant)
        asset.status = self.status_archived
        asset.save()

        # Un-archive (archived → pending, allowed by state machine)
        status_pending, _ = StatusLabel.objects.get_or_create(
            slug='in-transit',
            defaults={'name': 'In Transit', 'type': 'pending', 'color': '6f42c1'},
        )
        asset.status = status_pending
        asset.save()
        asset.refresh_from_db()

        self.assertIsNone(asset.disposed_at)
        self.assertIsNone(asset.disposal_value)


class TestInServiceDate(TenantTestMixin, TestCase):

    def setUp(self):
        self.setup_tenant_context()
        self.set_active_tenant(self.tenant)

        self.mfg = Manufacturer.objects.create(name='Acme2', slug='acme2')
        self.policy = _make_policy('Test 12M', months=12, convention='exclude_purchase_month')
        self.asset_type = AssetType.objects.create(
            manufacturer=self.mfg, model='WidgetLite', slug='acme2-widgetlite',
            depreciation=self.policy,
        )
        self.status, _ = StatusLabel.objects.get_or_create(
            slug='available',
            defaults={'name': 'Available', 'type': 'deployable', 'color': '28a745'},
        )

    def test_in_service_date_shifts_depreciation_clock(self):
        # Purchase Jan 2024, in_service Jul 2024 (6 months later).
        # On Jan 2025 → 6 months from in_service_date (exclude_purchase_month).
        asset = _make_asset(
            'InServ Widget', self.asset_type, self.status,
            purchase_cost=1200, salvage_value=0,
            purchase_date=datetime.date(2024, 1, 1),
            in_service_date=datetime.date(2024, 7, 1),
            tenant=self.tenant,
        )
        on_date = datetime.date(2025, 1, 1)  # 6 months from in_service_date
        result = compute_book_value(asset, on_date=on_date)
        # (1200/12) * 6 = 600 depreciation → value = 600
        self.assertEqual(result, Decimal('600.00'))


class TestResolutionChainDB(TenantTestMixin, TestCase):

    def setUp(self):
        self.setup_tenant_context()
        self.set_active_tenant(self.tenant)

        self.mfg = Manufacturer.objects.create(name='Acme3', slug='acme3')
        self.status, _ = StatusLabel.objects.get_or_create(
            slug='available',
            defaults={'name': 'Available', 'type': 'deployable', 'color': '28a745'},
        )
        self.policy_type = _make_policy('Type Policy', months=36)
        self.policy_tenant = _make_policy('Tenant Policy', months=24)
        self.policy_override = _make_policy('Override Policy', months=12)

        self.asset_type = AssetType.objects.create(
            manufacturer=self.mfg, model='ResWidget', slug='acme3-reswidget',
            depreciation=self.policy_type,
        )

    def test_type_rung_used_when_no_override_or_tenant_default(self):
        asset = _make_asset('R1', self.asset_type, self.status,
                            purchase_date=datetime.date(2024, 1, 1), tenant=self.tenant)
        policy, rung = resolve_policy(asset)
        self.assertEqual(rung, 'type')
        self.assertEqual(policy.months, 36)

    def test_tenant_default_beats_type(self):
        self.tenant.default_depreciation = self.policy_tenant
        self.tenant.save()
        asset = _make_asset('R2', self.asset_type, self.status,
                            purchase_date=datetime.date(2024, 1, 1), tenant=self.tenant)
        asset.refresh_from_db()
        asset.tenant.refresh_from_db()
        policy, rung = resolve_policy(asset)
        self.assertEqual(rung, 'tenant')
        self.assertEqual(policy.months, 24)

    def test_override_beats_tenant_and_type(self):
        self.tenant.default_depreciation = self.policy_tenant
        self.tenant.save()
        asset = _make_asset('R3', self.asset_type, self.status,
                            purchase_date=datetime.date(2024, 1, 1), tenant=self.tenant,
                            depreciation_override=self.policy_override)
        policy, rung = resolve_policy(asset)
        self.assertEqual(rung, 'override')
        self.assertEqual(policy.months, 12)
