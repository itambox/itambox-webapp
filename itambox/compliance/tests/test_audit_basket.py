from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.models import AuditSession, AssetAudit
from organization.models import Location, Tenant
from core.tests.mixins import TenantTestMixin

User = get_user_model()


class AuditBasketTests(TenantTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.setup_tenant_context(name='TenantA', slug='tenant-a')
        self.tenant_role.permissions = [
            'compliance.view_auditsession',
            'compliance.add_assetaudit',
            'compliance.change_auditsession',
        ]
        self.tenant_role.save()

        # Locations
        self.loc_berlin = baker.make(Location, name='Berlin', tenant=self.tenant)
        self.loc_munich = baker.make(Location, name='Munich', tenant=self.tenant)

        # Statuses
        self.status_deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name='Deployable')
        self.status_archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED, name='Archived')

        # Active Session scoped to Berlin
        self.session = AuditSession.objects.create(
            name='Berlin Audit',
            status='active',
            location=self.loc_berlin,
            created_by=self.tenant_admin,
            tenant=self.tenant,
        )

        # Assets
        # 1. Matching (in expected list, i.e., in Berlin)
        self.asset_matching = baker.make(
            Asset,
            asset_tag='AST-MATCH',
            location=self.loc_berlin,
            status=self.status_deployable,
            tenant=self.tenant
        )
        # 2. Surprise (not in expected list, e.g., in Munich)
        self.asset_surprise = baker.make(
            Asset,
            asset_tag='AST-SURPRISE',
            location=self.loc_munich,
            status=self.status_deployable,
            tenant=self.tenant
        )
        # 3. Archived (ineligible)
        self.asset_archived = baker.make(
            Asset,
            asset_tag='AST-ARCHIVED',
            location=self.loc_berlin,
            status=self.status_archived,
            tenant=self.tenant
        )

    def test_validate_matching_asset(self):
        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_validate', kwargs={'pk': self.session.pk})
        response = self.client.get(url, {'code': 'AST-MATCH'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['found'])
        self.assertEqual(data['classification'], 'matched')
        self.assertTrue(data['eligible'])
        self.assertIsNone(data['warning'])

    def test_validate_surprise_asset(self):
        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_validate', kwargs={'pk': self.session.pk})
        response = self.client.get(url, {'code': 'AST-SURPRISE'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['found'])
        self.assertEqual(data['classification'], 'surprise')
        self.assertTrue(data['eligible'])
        self.assertIsNone(data['warning'])

    def test_validate_archived_asset_is_ineligible(self):
        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_validate', kwargs={'pk': self.session.pk})
        response = self.client.get(url, {'code': 'AST-ARCHIVED'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['found'])
        self.assertFalse(data['eligible'])
        self.assertIn('Archived assets cannot be audited', data['warning'])

    def test_validate_already_audited_asset(self):
        # Audit the matching asset first
        AssetAudit.objects.create(
            session=self.session,
            asset=self.asset_matching,
            auditor=self.tenant_user,
            location=self.loc_berlin,
            status=self.status_deployable,
            verification_method='barcode'
        )

        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_validate', kwargs={'pk': self.session.pk})
        response = self.client.get(url, {'code': 'AST-MATCH'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['found'])
        self.assertFalse(data['eligible'])
        self.assertIn('already been verified', data['warning'])

    def test_commit_basket_success(self):
        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_commit', kwargs={'pk': self.session.pk})
        
        # Post the basket containing matching and surprise assets
        response = self.client.post(url, {
            'pk': [self.asset_matching.pk, self.asset_surprise.pk]
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('auditCommitSuccess', response['HX-Trigger'])

        # Verify audits created
        audits = AssetAudit.objects.filter(session=self.session)
        self.assertEqual(audits.count(), 2)

        audit_matching = audits.get(asset=self.asset_matching)
        self.assertEqual(audit_matching.location, self.loc_berlin)
        self.assertEqual(audit_matching.auditor, self.tenant_user)

        audit_surprise = audits.get(asset=self.asset_surprise)
        # Observed location should be session.location (Berlin) or asset.location (Munich if session had no location)
        self.assertEqual(audit_surprise.location, self.loc_berlin)
        self.assertEqual(audit_surprise.auditor, self.tenant_user)

        # Verify change log created
        # AssetAudit inherits from ChangeLoggingMixin, so creating one logs a change.
        self.asset_matching.refresh_from_db()
        self.assertEqual(self.asset_matching.last_audited_by, self.tenant_user)

    def test_commit_basket_idempotency(self):
        # Pre-audit the matching asset
        AssetAudit.objects.create(
            session=self.session,
            asset=self.asset_matching,
            auditor=self.tenant_user,
            location=self.loc_berlin,
            status=self.status_deployable,
            verification_method='barcode'
        )

        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_commit', kwargs={'pk': self.session.pk})
        
        # Post a basket with already-audited asset + a new surprise asset
        response = self.client.post(url, {
            'pk': [self.asset_matching.pk, self.asset_surprise.pk]
        })
        self.assertEqual(response.status_code, 200)
        
        # Only surprise asset audit should have been created in this commit, no duplicate or error
        self.assertEqual(AssetAudit.objects.filter(session=self.session).count(), 2)

    def test_commit_basket_with_archived_asset_fails_atomic(self):
        self.client.force_login(self.tenant_user)
        url = reverse('compliance:auditsession_commit', kwargs={'pk': self.session.pk})
        
        # Post a basket containing a valid matching asset and an archived asset
        response = self.client.post(url, {
            'pk': [self.asset_matching.pk, self.asset_archived.pk]
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('Archived assets cannot be audited', response.content.decode('utf-8'))

        # Verify transaction atomicity: matching asset audit was NOT created
        self.assertEqual(AssetAudit.objects.filter(session=self.session).count(), 0)
