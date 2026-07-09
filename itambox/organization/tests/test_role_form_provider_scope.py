"""Regression coverage for ``RoleForm.is_provider_scoped`` precedence (M3).

``Role.scope`` defaults to ``SCOPE_TENANT`` at the model-field level, so a bare
``Role()`` instance (what Django's ``ModelForm`` builds when no ``instance=`` is
passed) always carries ``scope == 'tenant'`` before the form has a chance to derive
anything. ``is_provider_scoped`` used to consult ``self.instance.scope`` for this
pk-less case, which meant a fresh ``RoleForm(provider=<Provider>)`` — exactly what
the role-less onboarding deep-link (``role_create?provider=<pk>``) constructs via
``RoleEditView.get_form_kwargs`` — misclassified as tenant-scoped: the template hid
the provider-capability checkboxes, and the role still saved with ``scope='provider'``
(``clean()`` derives scope correctly), producing a capability-less provider role with
no way for the user to grant capabilities through the UI.

The fix makes the pk-less branch follow ``fields['scope'].initial`` unconditionally,
which ``__init__`` already derives with the right precedence: bound ``initial['scope']``
(this is also how a cloned instance's real scope flows through — Django folds the
instance's field values into ``self.initial`` before ``__init__`` runs) over the
explicit ``provider``/``tenant`` kwarg over the model default.
"""
from django.test import TestCase

from core.tests.mixins import TenantTestMixin
from organization.forms import RoleForm
from organization.models import Provider, Role


class RoleFormProviderScopeTests(TenantTestMixin, TestCase):

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin  # a superuser, per the mixin
        self.provider = Provider.objects.create(name="Northwind MSP", slug="northwind-msp")

    def tearDown(self):
        self.clear_tenant_context()

    # ------------------------------------------------------------------ (a)
    def test_fresh_create_with_provider_kwarg_is_provider_scoped(self):
        # Mirrors RoleEditView.get_form_kwargs on the role-less onboarding deep-link
        # (role_create?provider=<pk>): no instance, no bound data, just the kwarg.
        form = RoleForm(user=self.superuser, provider=self.provider)
        self.assertTrue(form.is_provider_scoped)
        # The provider-capability checkbox fields must actually be present so the
        # template's capability strip has something to render.
        self.assertIn('cap_manage_staff', form.fields)

    def test_fresh_create_with_provider_kwarg_saves_role_with_capabilities(self):
        data = {
            'name': 'MSP Admin', 'description': '',
            'perm_asset_read': 'on', 'cap_manage_staff': 'on',
        }
        form = RoleForm(data=data, user=self.superuser, provider=self.provider)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.scope, Role.SCOPE_PROVIDER)
        self.assertEqual(role.provider_id, self.provider.pk)
        self.assertIsNone(role.tenant_id)
        self.assertIn('organization.manage_staff', role.permissions)

    # ------------------------------------------------------------------ (b)
    def test_editing_existing_tenant_role_is_tenant_scoped(self):
        role = Role.objects.create(tenant=self.tenant, name='Tenant Ops', permissions=[])
        form = RoleForm(instance=role, user=self.superuser, tenant=self.tenant)
        self.assertFalse(form.is_provider_scoped)

    # ------------------------------------------------------------------ (c)
    def test_editing_existing_provider_role_is_provider_scoped(self):
        role = Role.objects.create(
            provider=self.provider, scope=Role.SCOPE_PROVIDER,
            name='MSP Operations', permissions=['organization.manage_staff'],
        )
        form = RoleForm(instance=role, user=self.superuser)
        self.assertTrue(form.is_provider_scoped)
        self.assertIn('cap_manage_staff', form.fields)
        self.assertTrue(form['cap_manage_staff'].value())

    # ------------------------------------------------------------------ (d)
    def test_bound_data_cannot_override_the_derived_scope(self):
        # The ``scope`` field is disabled — Django ignores submitted data for
        # disabled fields and always uses the field's ``initial`` instead. A
        # malicious/naive POST that tries to smuggle scope='tenant' alongside the
        # explicit provider kwarg must not flip the form (or the saved role) to
        # tenant-scoped.
        data = {
            'name': 'MSP Admin', 'description': '', 'scope': Role.SCOPE_TENANT,
            'cap_manage_staff': 'on',
        }
        form = RoleForm(data=data, user=self.superuser, provider=self.provider)
        self.assertTrue(form.is_provider_scoped)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.scope, Role.SCOPE_PROVIDER)
        self.assertEqual(role.provider_id, self.provider.pk)
        self.assertIn('organization.manage_staff', role.permissions)

    def test_editing_existing_role_ignores_provider_kwarg_and_stays_locked(self):
        # Edit locks scope to the saved instance regardless of any (nonsensical)
        # provider/tenant kwarg passed alongside it.
        role = Role.objects.create(tenant=self.tenant, name='Tenant Ops 2', permissions=[])
        form = RoleForm(instance=role, user=self.superuser, provider=self.provider)
        self.assertFalse(form.is_provider_scoped)
