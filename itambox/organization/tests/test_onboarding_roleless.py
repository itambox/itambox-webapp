"""Compatibility-route coverage for Stage 3 technician onboarding.

The separate technician form no longer exists. ``onboard/technician/`` remains
as a GET-only bookmark/navigation target and redirects to the unified membership
form with the provider tenant and technician preset selected.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, Membership, Role, RoleAssignment

User = get_user_model()


class TechnicianPresetRedirectTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(
            name="Acme MSP", slug="acme-msp-preset", is_provider=True,
        )
        self.admin_role = Role.objects.create(
            tenant=self.msp,
            name="Provider Admin",
            permissions=["organization.add_membership"],
        )
        self.staff_admin = User.objects.create_user(
            username="staff_admin_preset",
            email="staff_admin_preset@example.com",
            password="pw",
        )
        self.grant(
            self.staff_admin,
            self.msp,
            self.admin_role,
            reach=RoleAssignment.REACH_OWN,
        )
        self.url = reverse('organization:technician_quick_add')

    def tearDown(self):
        self.clear_tenant_context()

    def test_get_redirects_to_unified_technician_preset(self):
        self.client.force_login(self.staff_admin)

        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            reverse('organization:membership_create')
            + f'?preset=technician&tenant={self.msp.pk}',
            fetch_redirect_response=False,
        )

    def test_post_is_not_allowed_and_creates_nothing(self):
        self.client.force_login(self.staff_admin)

        response = self.client.post(self.url, {'email': 'newtech@example.com'})

        self.assertEqual(response.status_code, 405)
        self.assertFalse(User.objects.filter(email='newtech@example.com').exists())
        self.assertEqual(Membership.objects.count(), 1)

    def test_user_without_an_eligible_provider_is_denied(self):
        outsider = User.objects.create_user(
            username='preset_outsider', email='preset_outsider@example.com', password='pw',
        )
        self.client.force_login(outsider)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)


class TechnicianPresetInitialTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(
            name="Preset MSP", slug="preset-msp", is_provider=True,
        )
        self.technician = Role.objects.create(
            tenant=self.msp,
            name="Technician",
            permissions=[],
            shared_with_managed=True,
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_preset_selects_new_user_managed_all_and_technician_role(self):
        from organization.forms import MembershipForm

        form = MembershipForm(
            tenant=self.msp,
            preset=MembershipForm.PRESET_TECHNICIAN,
            user=User.objects.create_superuser(
                username='preset_root', email='preset_root@example.com', password='pw',
            ),
        )

        self.assertEqual(form.fields['who'].initial, MembershipForm.WHO_NEW)
        self.assertFalse(form.fields['reach_own'].initial)
        self.assertTrue(form.fields['reach_managed'].initial)
        self.assertEqual(
            form.fields['managed_scope'].initial,
            RoleAssignment.SCOPE_ALL,
        )
        self.assertEqual(form.fields['roles'].initial, [self.technician.pk])
