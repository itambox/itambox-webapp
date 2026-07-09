from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, StatusLabel
from extras.models import LabelTemplate
from organization.models import Tenant, Role, Membership

User = get_user_model()


class LabelPrintPermissionTests(TestCase):
    """WS1-2: printing a label exposes an asset's name/tag/serial, so LabelPrintView must
    require view_asset — not just login."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Tenant', slug='t-label')
        self.status = StatusLabel.objects.create(name='Dep', slug='dep-label', type='deployable')
        self.asset = Asset.objects.create(
            name='Secret Laptop', asset_tag='LBL-1', status=self.status, tenant=self.tenant,
        )
        self.template = LabelTemplate.objects.create(name='Default Label')

    def _login(self, username, perms):
        user = User.objects.create_user(username=username, password='pw')
        role = Role.objects.create(tenant=self.tenant, name=f'role-{username}', permissions=perms)
        membership = Membership.objects.create(user=user, tenant=self.tenant)
        membership.roles.add(role)
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def _print_url(self):
        return reverse('label_print', kwargs={'template_id': self.template.pk, 'object_id': self.asset.pk})

    def test_member_without_view_asset_is_denied(self):
        self._login('noview', ['extras.view_dashboard'])  # member, but no view_asset
        self.assertEqual(self.client.get(self._print_url()).status_code, 403)

    def test_member_with_view_asset_is_allowed(self):
        self._login('canview', ['assets.view_asset'])
        self.assertNotEqual(self.client.get(self._print_url()).status_code, 403)
