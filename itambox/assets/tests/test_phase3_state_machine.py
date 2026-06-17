"""Phase 3 (E1) — AssetStateMachine enforcement on the SAVE path.

These tests exercise state-machine enforcement on the save path. Transition
validation lives in Asset.clean(), which the global
`validate_custom_validators_on_save` pre_save signal (core/signals.py) runs on
every ChangeLoggingMixin save — so a plain Asset.save() is enough to trigger it
(no separate validation block in save() is required). They also confirm the
existing service flows (checkout/checkin/dispose) still pass through the state
machine cleanly.

Note: bulk QuerySet.update() bypasses both clean() and the pre_save signal, so
mass status flips are NOT validated — a pre-existing gap, not covered here.

Run with:
    pytest assets/tests/test_phase3_state_machine.py
"""
import pytest
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

from model_bakery import baker

from core.tests.mixins import TenantTestMixin
from assets.models import Asset, StatusLabel
from assets.models import DisposalMethodChoices
from assets.services import checkout_asset, checkin_asset, dispose_asset

User = get_user_model()


class AssetStateMachineSavePathTest(TenantTestMixin, TestCase):
    """Save-level state-machine enforcement and service-flow integration."""

    def setUp(self):
        self.setup_tenant_context()
        self.user = baker.make(User, is_superuser=True, is_staff=True)
        # Unique status-label names per type (suffix '-sm3') to avoid colliding
        # with sibling tests in the order-dependent full run.
        self.pending = baker.make(StatusLabel, type='pending', name='Pending-sm3')
        self.deployable = baker.make(StatusLabel, type='deployable', name='Deployable-sm3')
        self.deployed = baker.make(StatusLabel, type='deployed', name='Deployed-sm3')
        self.archived = baker.make(StatusLabel, type='archived', name='Archived-sm3')
        self.asset = baker.make(
            Asset,
            name='State Machine Laptop',
            asset_tag='SM3-0001',
            status=self.pending,
            tenant=self.tenant,
        )

    def test_legal_transition_via_save_succeeds(self):
        """pending -> deployable is allowed and must save without raising."""
        self.asset.status = self.deployable
        self.asset.save()  # must not raise
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, 'deployable')

    def test_illegal_transition_via_save_raises(self):
        """archived -> deployable is illegal and must raise on save()."""
        # pending -> archived is legal; perform it first.
        self.asset.status = self.archived
        self.asset.save()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, 'archived')

        # archived only allows -> pending, so -> deployable must raise.
        self.asset.status = self.deployable
        with self.assertRaises(ValidationError) as ctx:
            self.asset.save()
        self.assertIn('Illegal state transition', str(ctx.exception))

    def test_checked_out_asset_cannot_be_archived_via_save(self):
        """An asset with an active assignment must not be archived on save()."""
        from organization.models import AssetHolder
        holder = baker.make(AssetHolder, tenant=self.tenant)
        # Reach a checked-out 'deployed' state via the LEGAL path
        # (pending -> deployable -> deployed through checkout); a direct
        # pending -> deployed save is itself an illegal transition.
        self.asset.status = self.deployable
        self.asset.save()
        checkout_asset(asset=self.asset, holder=holder, user=self.user)
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.assignments.filter(is_active=True).exists())

        self.asset.status = self.archived
        with self.assertRaises(ValidationError) as ctx:
            self.asset.save()
        self.assertIn('actively checked-out', str(ctx.exception))

    def test_checkout_archived_asset_is_blocked(self):
        """checkout_asset must reject an archived asset with a clear message."""
        from organization.models import AssetHolder
        holder = baker.make(AssetHolder, tenant=self.tenant)
        self.asset.status = self.archived
        self.asset.save()

        with self.assertRaises(ValidationError) as ctx:
            checkout_asset(asset=self.asset, holder=holder, user=self.user)
        self.assertIn('Cannot check out', str(ctx.exception))

    def test_service_flows_still_pass(self):
        """checkout -> checkin -> dispose must all complete without raising."""
        from organization.models import AssetHolder
        holder = baker.make(AssetHolder, tenant=self.tenant)

        # Start deployable so checkout (deployable -> deployed) is legal.
        self.asset.status = self.deployable
        self.asset.save()

        # checkout (deployable -> deployed)
        checkout_asset(asset=self.asset, holder=holder, user=self.user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, 'deployed')

        # checkin (deployed -> reverts to pre-checkout deployable)
        checkin_asset(asset=self.asset, user=self.user)
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.assignments.filter(is_active=True).exists())

        # dispose (-> archived)
        dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date='2026-06-16',
            user=self.user,
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, 'archived')
