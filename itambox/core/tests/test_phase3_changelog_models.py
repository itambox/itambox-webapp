import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from itambox.middleware import _current_user, _request_id
from core.choices import ObjectChangeActionChoices
from core.managers import set_current_tenant
from core.models import ObjectChange

from organization.models import Tenant, Role, Membership, Site, Location
from users.models import Token
from assets.models import StatusLabel, AssetRole, Asset
from compliance.models import AssetAudit

User = get_user_model()


class Phase3ChangeLoggingModelsTestCase(TestCase):
    """C4 — Membership, Token and AssetAudit gained ChangeLoggingMixin.

    Saving each model inside an active request context must record an
    ObjectChange with action 'create' referencing that model, attributed to the
    correct tenant and user (M2 / L4).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='changelog-c4',
            password='password123',
            is_superuser=True,
        )
        self.tenant = Tenant.objects.create(name='Changelog C4 Tenant', slug='changelog-c4')
        self.role = Role.objects.create(tenant=self.tenant, name='Member C4')

        # Establish an active request context so ChangeLoggingMixin logs.
        _current_user.set(self.user)
        self.request_id = uuid.uuid4()
        _request_id.set(self.request_id)
        # An active tenant for the direct-FK assertions; M2 tests override it.
        set_current_tenant(self.tenant)

    def tearDown(self):
        # Be explicit even though the autouse conftest fixture also clears these.
        _request_id.set(None)
        _current_user.set(None)
        set_current_tenant(None)

    def _latest_create(self, instance):
        # Query via the unfiltered base manager so ambient tenant scoping never
        # masks the change we are asserting on.
        return ObjectChange._base_manager.filter(
            request_id=self.request_id,
            action=ObjectChangeActionChoices.ACTION_CREATE,
            changed_object_id=instance.pk,
            changed_object_type__model=instance.__class__._meta.model_name,
        ).latest('time')

    def _assert_create_logged(self, instance, expected_tenant):
        change = self._latest_create(instance)
        self.assertEqual(change.action, ObjectChangeActionChoices.ACTION_CREATE)
        self.assertEqual(change.changed_object_type.model, instance.__class__._meta.model_name)
        self.assertEqual(change.changed_object_id, instance.pk)
        self.assertEqual(change.user, self.user)
        self.assertEqual(change.tenant, expected_tenant)
        return change

    def test_tenant_membership_create_is_logged(self):
        before = ObjectChange._base_manager.count()
        membership = Membership.objects.create(user=self.user,
            tenant=self.tenant,
        )
        membership.roles.add(self.role)
        self.assertGreater(ObjectChange._base_manager.count(), before)
        # Membership has a direct tenant FK -> attributed to that tenant.
        self._assert_create_logged(membership, self.tenant)

    def test_token_create_is_logged(self):
        before = ObjectChange._base_manager.count()
        token = Token.objects.create(user=self.user, tenant=self.tenant)
        self.assertGreater(ObjectChange._base_manager.count(), before)
        # Token has a direct tenant FK -> attributed to that tenant.
        self._assert_create_logged(token, self.tenant)

    def _make_audit_fixtures(self, asset_tenant):
        status = StatusLabel.objects.create(
            name='Audited C4', slug='audited-c4', type='deployable', color='28a745'
        )
        role = AssetRole.objects.create(name='Audit Role C4', slug='audit-role-c4')
        asset = Asset.objects.create(
            name='Audit Laptop C4',
            asset_tag='TAG-AUDIT-C4',
            status=status,
            asset_role=role,
            tenant=asset_tenant,
        )
        site = Site.objects.create(name='Audit Site C4', slug='audit-site-c4')
        location = Location.objects.create(
            name='Audit Location C4', slug='audit-location-c4', site=site, tenant=asset_tenant
        )
        return asset, location, status

    def test_asset_audit_create_is_logged(self):
        asset, location, status = self._make_audit_fixtures(self.tenant)

        before = ObjectChange._base_manager.count()
        audit = AssetAudit.objects.create(
            asset=asset,
            auditor=self.user,
            location=location,
            status=status,
        )
        self.assertGreater(ObjectChange._base_manager.count(), before)
        # AssetAudit has no tenant of its own -> derived from asset.tenant.
        self._assert_create_logged(audit, self.tenant)

    def test_asset_audit_attributed_to_asset_tenant_when_ambient_differs(self):
        """M2 — the change is attributed to the audited asset's tenant even when
        the ambient request tenant is a *different* tenant."""
        owner_tenant = Tenant.objects.create(name='Owner Tenant', slug='owner-tenant')
        asset, location, status = self._make_audit_fixtures(owner_tenant)

        # Ambient tenant is the unrelated setUp tenant, not the asset's owner.
        set_current_tenant(self.tenant)
        audit = AssetAudit.objects.create(
            asset=asset,
            auditor=self.user,
            location=location,
            status=status,
        )
        change = self._assert_create_logged(audit, owner_tenant)
        self.assertNotEqual(change.tenant, self.tenant)

    def test_asset_audit_attributed_to_asset_tenant_when_ambient_none(self):
        """M2 — with no ambient tenant (superuser global session / service flow)
        the change is still attributed to the asset's owning tenant rather than
        being written tenant=None and lost to the owner's changelog."""
        owner_tenant = Tenant.objects.create(name='Owner Tenant', slug='owner-tenant')
        asset, location, status = self._make_audit_fixtures(owner_tenant)

        # No ambient tenant context.
        set_current_tenant(None)
        audit = AssetAudit.objects.create(
            asset=asset,
            auditor=self.user,
            location=location,
            status=status,
        )
        change = self._assert_create_logged(audit, owner_tenant)
        self.assertIsNotNone(change.tenant)
