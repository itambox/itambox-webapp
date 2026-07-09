from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset
from extras.models import ExportTemplate
from organization.models import Tenant, Role, Membership

User = get_user_model()


class ExportTemplateSuperuserGateTests(TestCase):
    """WS1-1: ExportTemplate is a global, admin-managed resource whose template_code is
    server-rendered for every tenant. A tenant member holding only the model perms must NOT
    be able to create/edit/delete the shared templates (cross-tenant integrity / SSTI).
    Authoring is restricted to superusers; members keep read/render access."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Tenant', slug='t-exp')
        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Full Extras Role',
            permissions=[
                'extras.view_exporttemplate', 'extras.add_exporttemplate',
                'extras.change_exporttemplate', 'extras.delete_exporttemplate',
            ],
        )
        self.member = User.objects.create_user(username='member', password='pw')
        m = Membership.objects.create(user=self.member, tenant=self.tenant)
        m.roles.add(self.role)
        self.superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='pw'
        )
        ct = ContentType.objects.get_for_model(Asset)
        self.template = ExportTemplate.objects.create(
            name='Asset CSV', content_type=ct, template_code='{{ obj.name }}'
        )

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_member_cannot_edit_export_template(self):
        self._login(self.member)
        url = reverse('extras:exporttemplate_update', kwargs={'pk': self.template.pk})
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_member_cannot_create_export_template(self):
        self._login(self.member)
        self.assertEqual(self.client.get(reverse('extras:exporttemplate_create')).status_code, 403)

    def test_member_cannot_delete_export_template(self):
        self._login(self.member)
        url = reverse('extras:exporttemplate_delete', kwargs={'pk': self.template.pk})
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(ExportTemplate.objects.filter(pk=self.template.pk).exists())

    def test_superuser_can_edit_export_template(self):
        self._login(self.superuser)
        url = reverse('extras:exporttemplate_update', kwargs={'pk': self.template.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
