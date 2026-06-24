"""B8 regression: the new-request notifier must notify only same-tenant staff.

notify_new_request_task previously ran with no TaskContext and notified every
platform-wide is_staff user, leaking one tenant's asset requests to operators of
every other tenant. It now runs under the request's tenant context and scopes
recipients to staff who are members of that tenant.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from model_bakery import baker

from organization.models import Tenant, TenantRole, TenantMembership
from assets.models import AssetRequest
from core.models import Notification
from core.tests.mixins import TenantTestMixin

User = get_user_model()


class NewRequestNotifyRecipientTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='req-tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='req-tenant-b')

        self.staff_a = User.objects.create_user(username='req_staff_a', password='x', is_staff=True)
        _m_a = TenantMembership.objects.create(user=self.staff_a, tenant=self.tenant)
        _m_a.roles.add(self.tenant_role)
        self.staff_b = User.objects.create_user(username='req_staff_b', password='x', is_staff=True)
        role_b = TenantRole.objects.create(tenant=self.tenant_b, name='B role', permissions=[])
        _m_b = TenantMembership.objects.create(user=self.staff_b, tenant=self.tenant_b)
        _m_b.roles.add(role_b)

        self.set_active_tenant(self.tenant)
        # AssetRequest requires exactly one requested item category.
        self.request_obj = baker.make(
            AssetRequest,
            tenant=self.tenant,
            requester=self.tenant_user,
            asset_type=baker.make('assets.AssetType', requestable=True),
            parent=None,
        )

    def test_only_same_tenant_staff_notified(self):
        self.clear_tenant_context()
        from assets.tasks import notify_new_request_task
        notify_new_request_task(self.request_obj.pk)

        self.assertTrue(Notification.objects.filter(user=self.staff_a).exists())
        self.assertFalse(Notification.objects.filter(user=self.staff_b).exists())
