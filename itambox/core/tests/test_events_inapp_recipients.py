"""Audit fix A: a tenant-scoped in-app NotificationChannel resolves its recipients
via TenantMembership (``memberships``), not via the old AssetHolder-profile join.

The old join silently dropped tenant members who had no AssetHolder profile (e.g.
an admin who never holds hardware), so they never received in-app alerts for their
own tenant. Membership is the correct source of truth for "who belongs to a tenant".
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.events import send_notification_to_channel
from core.models import Notification
from extras.models import NotificationChannel
from organization.models import Tenant, TenantRole, TenantMembership

User = get_user_model()


class InAppChannelRecipientTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Acme', slug='acme')
        self.other_tenant = Tenant.objects.create(name='Globex', slug='globex')
        role = TenantRole.objects.create(tenant=self.tenant, name='R', permissions=[])

        # A member of `tenant` with NO AssetHolder profile — the case the old
        # asset_holder_profiles join dropped.
        self.member = User.objects.create_user(
            username='member', password='pw', is_active=True
        )
        TenantMembership.objects.create(user=self.member, tenant=self.tenant, role=role)

        # A member of a DIFFERENT tenant — must NOT receive this channel's notice.
        other_role = TenantRole.objects.create(tenant=self.other_tenant, name='R', permissions=[])
        self.outsider = User.objects.create_user(
            username='outsider', password='pw', is_active=True
        )
        TenantMembership.objects.create(user=self.outsider, tenant=self.other_tenant, role=other_role)

        self.channel = NotificationChannel.objects.create(
            name='Acme Feed',
            channel_type=NotificationChannel.TYPE_IN_APP,
            tenant=self.tenant,
        )

    def test_tenant_member_without_holder_profile_receives_notification(self):
        ok = send_notification_to_channel(self.channel, "Subj", "Body")
        self.assertTrue(ok)
        self.assertTrue(
            Notification.objects.filter(user=self.member, subject="Subj").exists()
        )

    def test_member_of_other_tenant_is_not_notified(self):
        send_notification_to_channel(self.channel, "Subj", "Body")
        self.assertFalse(
            Notification.objects.filter(user=self.outsider).exists()
        )
