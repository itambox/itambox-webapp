"""Unit tests for core.forms.scope_tenant_field — the owning-tenant picker
scoping used across edit forms.

Covers the three behaviours, including tenant-group mode (a user with roles in
tenant-a1 and tenant-a2, active on their group, must see both — and only both).
"""
from django import forms
from django.test import TestCase
from django.contrib.auth import get_user_model

from organization.models import Tenant, TenantGroup, TenantRole, TenantMembership
from core.forms import scope_tenant_field
from core.managers import set_current_tenant, set_current_tenant_group
from itambox.middleware import _current_user

User = get_user_model()


class _TenantPickerForm(forms.Form):
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.all(), required=False)


class ScopeTenantFieldTests(TestCase):
    def setUp(self):
        self.group = TenantGroup.objects.create(name='Group A', slug='grp-a')
        self.a1 = Tenant.objects.create(name='A1', slug='t-a1', group=self.group)
        self.a2 = Tenant.objects.create(name='A2', slug='t-a2', group=self.group)
        self.other = Tenant.objects.create(name='Other', slug='t-other')

        self.member = User.objects.create_user(username='member', password='pw')
        self.superuser = User.objects.create_superuser(username='root', email='r@x.com', password='pw')
        for t in (self.a1, self.a2):
            role = TenantRole.objects.create(tenant=t, name='R', permissions=[])
            TenantMembership.objects.create(user=self.member, tenant=t, role=role)

        # Load the URLconf now, under a clean (no-tenant) context, so view
        # `queryset = Model.objects.all()` class attributes bake UNSCOPED. The
        # real-form test below instantiates a form whose __init__ calls reverse()
        # under a tenant context; without this that could be the first URLconf
        # load and would freeze view querysets to the wrong tenant for later
        # tests (see memory: import-baked view querysets).
        from django.urls import reverse
        reverse('organization:location_list')

    def _pks(self, form):
        return set(form.fields['tenant'].queryset.values_list('pk', flat=True))

    def test_superuser_keeps_full_picker(self):
        _current_user.set(self.superuser)
        form = _TenantPickerForm()
        scope_tenant_field(form)
        # Left untouched — not hidden, not disabled.
        self.assertFalse(form.fields['tenant'].disabled)
        self.assertNotIsInstance(form.fields['tenant'].widget, forms.HiddenInput)

    def test_single_tenant_member_autoset_and_hidden(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = _TenantPickerForm()
        scope_tenant_field(form)
        self.assertTrue(form.fields['tenant'].disabled)
        self.assertIsInstance(form.fields['tenant'].widget, forms.HiddenInput)
        self.assertEqual(form.initial['tenant'], self.a1.pk)

    def test_group_member_sees_only_their_group_tenants(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        form = _TenantPickerForm()
        scope_tenant_field(form)
        # Both group memberships visible, nothing else; picker stays usable.
        self.assertEqual(self._pks(form), {self.a1.pk, self.a2.pk})
        self.assertNotIn(self.other.pk, self._pks(form))
        self.assertFalse(form.fields['tenant'].disabled)

    def test_wired_into_a_real_form(self):
        # End-to-end: a real edit form hides+locks its tenant picker for a
        # single-tenant member (confirms the helper is actually called).
        from organization.forms import LocationForm
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = LocationForm()
        self.assertTrue(form.fields['tenant'].disabled)
        self.assertIsInstance(form.fields['tenant'].widget, forms.HiddenInput)
