import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from itambox.middleware import _current_user, _request_id
from core.choices import ObjectChangeActionChoices
from core.models import ObjectChange

from organization.models import Tenant, TenantRole, TenantMembership, Site, Location
from users.models import Token
from assets.models import StatusLabel, AssetRole, Asset
from compliance.models import AssetAudit

User = get_user_model()


class Phase3ChangeLoggingModelsTestCase(TestCase):
    """C4 — TenantMembership, Token and AssetAudit gained ChangeLoggingMixin.

    Saving each model inside an active request context must record an
    ObjectChange with action 'create' referencing that model.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='changelog-c4',
            password='password123',
            is_superuser=True,
        )
        self.tenant = Tenant.objects.create(name='Changelog C4 Tenant', slug='changelog-c4')
        self.role = TenantRole.objects.create(tenant=self.tenant, name='Member C4')

        # Establish an active request context so ChangeLoggingMixin logs.
        _current_user.set(self.user)
        self.request_id = uuid.uuid4()
        _request_id.set(self.request_id)

    def tearDown(self):
        # Be explicit even though the autouse conftest fixture also clears these.
        _request_id.set(None)
        _current_user.set(None)

    def _assert_create_logged(self, instance):
        ct_model = instance.__class__._meta.model_name
        change = ObjectChange.objects.filter(
            request_id=self.request_id,
            action=ObjectChangeActionChoices.ACTION_CREATE,
            changed_object_id=instance.pk,
        ).latest('time')
        self.assertEqual(change.action, ObjectChangeActionChoices.ACTION_CREATE)
        self.assertEqual(change.changed_object_type.model, ct_model)
        self.assertEqual(change.changed_object_id, instance.pk)

    def test_tenant_membership_create_is_logged(self):
        before = ObjectChange.objects.count()
        membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role=self.role,
        )
        self.assertGreater(ObjectChange.objects.count(), before)
        self._assert_create_logged(membership)

    def test_token_create_is_logged(self):
        before = ObjectChange.objects.count()
        token = Token.objects.create(user=self.user, tenant=self.tenant)
        self.assertGreater(ObjectChange.objects.count(), before)
        self._assert_create_logged(token)

    def test_asset_audit_create_is_logged(self):
        status = StatusLabel.objects.create(
            name='Audited C4', slug='audited-c4', type='deployable', color='28a745'
        )
        role = AssetRole.objects.create(name='Audit Role C4', slug='audit-role-c4')
        asset = Asset.objects.create(
            name='Audit Laptop C4',
            asset_tag='TAG-AUDIT-C4',
            status=status,
            asset_role=role,
        )
        site = Site.objects.create(name='Audit Site C4', slug='audit-site-c4')
        location = Location.objects.create(
            name='Audit Location C4', slug='audit-location-c4', site=site
        )

        before = ObjectChange.objects.count()
        audit = AssetAudit.objects.create(
            asset=asset,
            auditor=self.user,
            location=location,
            status=status,
        )
        self.assertGreater(ObjectChange.objects.count(), before)
        self._assert_create_logged(audit)
