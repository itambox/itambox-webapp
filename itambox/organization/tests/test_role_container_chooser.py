"""Coverage for the /roles/add/ tenant-vs-provider container chooser.

Before this, ``RoleEditView`` force-bound a new role to the active tenant, so a
*provider* role could not be created from the UI at all. The plain add page now
renders both container pickers (``allow_container_choice``); the user picks exactly
one and the role's scope is DERIVED from that choice in ``RoleForm.clean()``.

Split into form-level tests (scope derivation / exactly-one enforcement) and
view-level tests (the chooser renders on /roles/add/, deep-links still bind, edit
stays locked).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin
from organization.forms import RoleForm
from organization.models import Provider, Role, Tenant

User = get_user_model()


class RoleChooserFormTests(TenantTestMixin, TestCase):
    """The form derives scope from the picked container and requires exactly one."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin  # a superuser, per the mixin
        self.provider = Provider.objects.create(name="Northwind MSP", slug="northwind-msp")

    def tearDown(self):
        self.clear_tenant_context()

    def _chooser_form(self, data):
        return RoleForm(data=data, user=self.superuser, allow_container_choice=True)

    def test_chooser_is_active_only_with_the_flag(self):
        # No flag → not a chooser (backward compatible with direct construction).
        self.assertFalse(RoleForm(user=self.superuser, tenant=self.tenant).is_container_chooser)
        self.assertFalse(RoleForm(user=self.superuser).is_container_chooser)
        # Flag + no bound container → chooser.
        self.assertTrue(RoleForm(user=self.superuser, allow_container_choice=True).is_container_chooser)

    def test_pick_provider_yields_a_provider_role(self):
        form = self._chooser_form({'name': 'MSP Operations', 'provider': self.provider.pk})
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.scope, Role.SCOPE_PROVIDER)
        self.assertEqual(role.provider_id, self.provider.pk)
        self.assertIsNone(role.tenant_id)

    def test_pick_tenant_yields_a_tenant_role(self):
        form = self._chooser_form({'name': 'Tenant Ops', 'tenant': self.tenant.pk})
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.scope, Role.SCOPE_TENANT)
        self.assertEqual(role.tenant_id, self.tenant.pk)
        self.assertIsNone(role.provider_id)

    def test_provider_role_can_carry_a_provider_capability(self):
        form = self._chooser_form({
            'name': 'MSP Admin', 'provider': self.provider.pk, 'cap_manage_staff': 'on',
        })
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.scope, Role.SCOPE_PROVIDER)
        self.assertIn('organization.manage_staff', role.permissions)

    def test_both_containers_is_rejected(self):
        form = self._chooser_form({
            'name': 'Ambiguous', 'tenant': self.tenant.pk, 'provider': self.provider.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('not both', ' '.join(form.non_field_errors()).lower())

    def test_neither_container_is_rejected(self):
        form = self._chooser_form({'name': 'Homeless'})
        self.assertFalse(form.is_valid())
        errs = ' '.join(form.non_field_errors()).lower()
        self.assertTrue('tenant' in errs and 'provider' in errs)


class RoleChooserViewTests(TenantTestMixin, TestCase):
    """The plain add page shows the chooser; deep-links bind; edit stays locked."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin
        self.provider = Provider.objects.create(name="Contoso MSP", slug="contoso-msp")
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def tearDown(self):
        self.clear_tenant_context()

    def test_add_page_renders_the_container_chooser(self):
        resp = self.client.get(reverse('organization:role_create'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('role-container-chooser', html)
        self.assertIn('name="tenant"', html)
        self.assertIn('name="provider"', html)

    def test_add_page_creates_a_provider_role_end_to_end(self):
        resp = self.client.post(
            reverse('organization:role_create'),
            {'name': 'Provider Role via Chooser', 'provider': self.provider.pk},
        )
        self.assertIn(resp.status_code, (301, 302))
        role = Role._base_manager.get(name='Provider Role via Chooser')
        self.assertEqual(role.scope, Role.SCOPE_PROVIDER)
        self.assertEqual(role.provider_id, self.provider.pk)

    def test_provider_deep_link_binds_provider_and_hides_chooser(self):
        url = reverse('organization:role_create') + f'?provider={self.provider.pk}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # Bound to the provider → not the chooser; provider carried as a hidden field.
        self.assertNotIn('role-container-chooser', html)

    def test_edit_page_is_not_a_chooser(self):
        role = Role.objects.create(tenant=self.tenant, name='Editable', permissions=[])
        resp = self.client.get(reverse('organization:role_update', kwargs={'pk': role.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('role-container-chooser', resp.content.decode())
