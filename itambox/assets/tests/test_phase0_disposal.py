"""Phase-0 regression test for dispose_asset() book-value freeze.

When an asset is disposed with proceeds=None, dispose_asset() must freeze the
depreciated residual into disposal_value BEFORE stamping disposed_at. Otherwise
compute_book_value short-circuits on disposed_at and the residual is lost
(disposal_value would default to 0.00 instead of the depreciated value).

Run with:
    pytest assets/tests/test_phase0_disposal.py
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from model_bakery import baker

from core.tests.mixins import TenantTestMixin
from assets.depreciation import compute_book_value
from assets.models import (
    Asset,
    AssetType,
    Depreciation,
    DisposalMethodChoices,
    Manufacturer,
    StatusLabel,
)
from assets.services import dispose_asset

User = get_user_model()

pytestmark = pytest.mark.django_db


class DisposeAssetFreezesResidualTest(TenantTestMixin, TestCase):
    """Disposing with proceeds=None must freeze the depreciated residual."""

    def setUp(self):
        self.setup_tenant_context()
        self.set_active_tenant(self.tenant)

        self.user = baker.make(User, is_superuser=True, is_staff=True)
        self.mfg = Manufacturer.objects.create(name='Acme', slug='acme-p0')
        # 36-month straight-line policy, exclude purchase month.
        self.policy = Depreciation.objects.create(
            name='P0 36M', months=36, method='straight_line',
            convention='exclude_purchase_month',
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.mfg, model='WidgetPro', slug='acme-p0-widgetpro',
            depreciation=self.policy,
        )
        # Use names unique to this test — default seed data already ships an
        # "Available"/"Retired" label and StatusLabel.name is uniquely constrained
        # among active rows.
        self.status_deployable, _ = StatusLabel.objects.get_or_create(
            slug='available-p0',
            defaults={'name': 'P0 Deployable', 'type': 'deployable', 'color': '28a745'},
        )
        self.status_archived, _ = StatusLabel.objects.get_or_create(
            slug='retired-p0',
            defaults={'name': 'P0 Archived', 'type': 'archived', 'color': 'dc3545'},
        )

    def test_dispose_with_no_proceeds_freezes_depreciated_residual(self):
        # Purchase 2024-01-01, dispose 2024-07-01 → 6 months held
        # (exclude_purchase_month). monthly = 1200/36 = 33.33...;
        # value = 1200 - 33.33... * 6 = 1000.00.
        purchase_date = datetime.date(2024, 1, 1)
        disposal_date = datetime.date(2024, 7, 1)
        asset = Asset.objects.create(
            name='Widget P0',
            asset_tag='TAG-P0-1',
            asset_type=self.asset_type,
            status=self.status_deployable,
            purchase_cost=Decimal('1200.00'),
            salvage_value=Decimal('0.00'),
            purchase_date=purchase_date,
            tenant=self.tenant,
        )

        # Expected residual computed BEFORE disposed_at is set.
        expected_residual = compute_book_value(asset, on_date=disposal_date)
        self.assertEqual(expected_residual, Decimal('1000.00'))
        self.assertTrue(expected_residual > 0)

        dispose_asset(
            asset=asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=disposal_date,
            proceeds=None,
            user=self.user,
        )

        asset.refresh_from_db()
        self.assertIsNotNone(asset.disposed_at)
        self.assertEqual(asset.disposal_value, expected_residual)
        self.assertTrue(asset.disposal_value > 0)
