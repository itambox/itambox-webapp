"""Regression tests for the M2 fix in ``MembershipForm.__init__`` (container
XOR / hidden-field-swap).

The form chooses a container — tenant XOR provider — via a hidden-field swap driven
by ``tenant=``/``provider=`` context kwargs. ``MembershipCreateView`` always passes
``kwargs['tenant'] = request.active_tenant`` (any active tenant in session) and
separately copies ``?user=``/``?tenant=``/``?provider=`` GET params verbatim into
``self.initial`` via ``get_initial()``. Before the fix, selecting one container via
context left the *other* field's stale ``self.initial`` entry in place: both hidden
inputs then rendered a value, and the tenant/provider XOR in ``clean()`` made the
form unsubmittable.

Covers:
  (a) provider context (``provider=`` kwarg) with a stale ``tenant`` key already in
      ``self.initial`` — the tenant initial must be cleared (field-level *and*
      ``self.initial``), and the form must be submittable.
  (b) the symmetric case: tenant context with a stale ``provider`` key in
      ``self.initial``.
  (c) the real view path: ``MembershipCreateView`` with ``?provider=<pk>`` in the URL
      and an active tenant in the session (mirrors ``get_initial()`` +
      ``get_form_kwargs()`` exactly) — end to end through the client.
  (d) the plain, context-free add flow is unaffected — both container pickers stay
      visible with no initial, and creating either kind of membership still works.
  (e) the edit flow (instance-bound) is unaffected — the container stays locked
      (disabled, correct initial, other field hidden) and saving still works.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, Provider, Membership
from organization.forms.membership_form import MembershipForm

User = get_user_model()


class MembershipFormProviderCtxClearsTenantInitialTests(TenantTestMixin, TestCase):
    """(a) provider context kwarg + a stale 'tenant' key in self.initial."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Corp", slug="corp-cinit-a")
        self.provider = Provider.objects.create(name="Acme MSP", slug="acme-cinit-a")
        self.superuser = User.objects.create_superuser(
            username="su_cinit_a", email="su_cinit_a@x.com", password="pw",
        )
        self.staff_user = User.objects.create_user(
            username="staff_cinit_a", email="staff_cinit_a@x.com", password="pw",
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_stale_tenant_initial_is_cleared(self):
        form = MembershipForm(
            provider=self.provider,
            initial={'tenant': str(self.tenant.pk)},
            user=self.superuser,
        )
        self.assertIsNone(form.fields['tenant'].initial)
        self.assertNotIn('tenant', form.initial)
        # BoundField.initial is what actually renders into the hidden input.
        self.assertIsNone(form['tenant'].initial)
        self.assertEqual(str(form['provider'].initial), str(self.provider.pk))

    def test_form_is_submittable_when_hidden_inputs_are_resubmitted(self):
        unbound = MembershipForm(
            provider=self.provider,
            initial={'tenant': str(self.tenant.pk)},
            user=self.superuser,
        )
        # Simulate the browser re-submitting exactly what the hidden inputs rendered.
        bound = MembershipForm(
            data={
                'user': self.staff_user.pk,
                'tenant': unbound['tenant'].initial or '',
                'provider': unbound['provider'].initial,
                'is_active': 'on',
            },
            provider=self.provider,
            initial={'tenant': str(self.tenant.pk)},
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.provider_id, self.provider.pk)
        self.assertIsNone(membership.tenant_id)


class MembershipFormTenantCtxClearsProviderInitialTests(TenantTestMixin, TestCase):
    """(b) symmetric: tenant context + a stale 'provider' key in self.initial."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Corp B", slug="corp-cinit-b")
        self.provider = Provider.objects.create(name="Acme MSP B", slug="acme-cinit-b")
        self.superuser = User.objects.create_superuser(
            username="su_cinit_b", email="su_cinit_b@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_cinit_b", email="member_cinit_b@x.com", password="pw",
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_stale_provider_initial_is_cleared(self):
        form = MembershipForm(
            tenant=self.tenant,
            initial={'provider': str(self.provider.pk)},
            user=self.superuser,
        )
        self.assertIsNone(form.fields['provider'].initial)
        self.assertNotIn('provider', form.initial)
        self.assertIsNone(form['provider'].initial)
        self.assertEqual(str(form['tenant'].initial), str(self.tenant.pk))

    def test_form_is_submittable_when_hidden_inputs_are_resubmitted(self):
        unbound = MembershipForm(
            tenant=self.tenant,
            initial={'provider': str(self.provider.pk)},
            user=self.superuser,
        )
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': unbound['tenant'].initial,
                'provider': unbound['provider'].initial or '',
                'is_active': 'on',
            },
            tenant=self.tenant,
            initial={'provider': str(self.provider.pk)},
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.tenant_id, self.tenant.pk)
        self.assertIsNone(membership.provider_id)


class MembershipCreateViewContainerInitialTests(TenantTestMixin, TestCase):
    """(c) End-to-end through the real view: ?provider=<pk> in the URL plus an active
    tenant in the session — exactly how ``MembershipCreateView.get_initial()`` /
    ``get_form_kwargs()`` populate the form in production."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="View Corp", slug="view-corp-cinit")
        self.provider = Provider.objects.create(name="View MSP", slug="view-msp-cinit")
        self.superuser = User.objects.create_superuser(
            username="su_view_cinit", email="su_view_cinit@x.com", password="pw",
        )
        self.staff_user = User.objects.create_user(
            username="staff_view_cinit", email="staff_view_cinit@x.com", password="pw",
        )
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_provider_get_param_with_active_tenant_session_renders_clean_initial(self):
        url = reverse('organization:membership_create') + f'?provider={self.provider.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        # The always-present active-tenant kwarg must not leak into the hidden tenant
        # input just because ?provider=<pk> populated self.initial.
        self.assertIsNone(form['tenant'].initial)
        self.assertEqual(str(form['provider'].initial), str(self.provider.pk))

    def test_provider_get_param_with_active_tenant_session_is_submittable(self):
        url = reverse('organization:membership_create') + f'?provider={self.provider.pk}'
        get_response = self.client.get(url)
        form = get_response.context['form']

        post_data = {
            'user': self.staff_user.pk,
            'tenant': form['tenant'].initial or '',
            'provider': form['provider'].initial,
            'is_active': 'on',
        }
        response = self.client.post(url, data=post_data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors.as_json())
        membership = Membership.objects.get(user=self.staff_user)
        self.assertEqual(membership.provider_id, self.provider.pk)
        self.assertIsNone(membership.tenant_id)


class MembershipFormPlainAddFlowTests(TenantTestMixin, TestCase):
    """(d) The context-free add flow (no tenant/provider context at all) is unaffected."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Plain Corp", slug="plain-corp-cinit")
        self.provider = Provider.objects.create(name="Plain MSP", slug="plain-msp-cinit")
        self.superuser = User.objects.create_superuser(
            username="su_plain_cinit", email="su_plain_cinit@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_plain_cinit", email="member_plain_cinit@x.com", password="pw",
        )
        self.staff_user = User.objects.create_user(
            username="staff_plain_cinit", email="staff_plain_cinit@x.com", password="pw",
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_both_container_pickers_stay_visible_with_no_initial(self):
        form = MembershipForm(user=self.superuser)
        self.assertNotIsInstance(form.fields['tenant'].widget, forms.HiddenInput)
        self.assertNotIsInstance(form.fields['provider'].widget, forms.HiddenInput)
        self.assertIsNone(form.fields['tenant'].initial)
        self.assertIsNone(form.fields['provider'].initial)

    def test_context_free_create_tenant_member_still_works(self):
        bound = MembershipForm(
            data={'user': self.member_user.pk, 'tenant': self.tenant.pk, 'is_active': 'on'},
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        saved = bound.save()
        self.assertEqual(saved.tenant_id, self.tenant.pk)
        self.assertIsNone(saved.provider_id)

    def test_context_free_create_provider_staff_still_works(self):
        bound = MembershipForm(
            data={'user': self.staff_user.pk, 'provider': self.provider.pk, 'is_active': 'on'},
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        saved = bound.save()
        self.assertEqual(saved.provider_id, self.provider.pk)
        self.assertIsNone(saved.tenant_id)


class MembershipFormEditFlowUnaffectedTests(TenantTestMixin, TestCase):
    """(e) The instance-bound edit flow keeps locking the container as before."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Edit Corp", slug="edit-corp-cinit")
        self.provider = Provider.objects.create(name="Edit MSP", slug="edit-msp-cinit")
        self.superuser = User.objects.create_superuser(
            username="su_edit_cinit", email="su_edit_cinit@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_edit_cinit", email="member_edit_cinit@x.com", password="pw",
        )
        self.staff_user = User.objects.create_user(
            username="staff_edit_cinit", email="staff_edit_cinit@x.com", password="pw",
        )
        self.tenant_membership = Membership.objects.create(
            user=self.member_user, tenant=self.tenant, is_active=True,
        )
        self.staff_membership = Membership.objects.create(
            user=self.staff_user, provider=self.provider, is_active=True,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_tenant_membership_edit_locks_container_and_saves(self):
        form = MembershipForm(instance=self.tenant_membership, user=self.superuser)
        self.assertEqual(form.fields['tenant'].initial, self.tenant.pk)
        self.assertTrue(form.fields['tenant'].disabled)
        self.assertIsInstance(form.fields['provider'].widget, forms.HiddenInput)

        bound = MembershipForm(
            data={'user': self.member_user.pk, 'tenant': self.tenant.pk, 'is_active': 'on'},
            instance=self.tenant_membership,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        saved = bound.save()
        self.assertEqual(saved.tenant_id, self.tenant.pk)
        self.assertIsNone(saved.provider_id)

    def test_provider_staff_edit_locks_container_and_saves(self):
        form = MembershipForm(instance=self.staff_membership, user=self.superuser)
        self.assertEqual(form.fields['provider'].initial, self.provider.pk)
        self.assertTrue(form.fields['provider'].disabled)
        self.assertIsInstance(form.fields['tenant'].widget, forms.HiddenInput)

        bound = MembershipForm(
            data={'user': self.staff_user.pk, 'provider': self.provider.pk, 'is_active': 'on'},
            instance=self.staff_membership,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        saved = bound.save()
        self.assertEqual(saved.provider_id, self.provider.pk)
        self.assertIsNone(saved.tenant_id)
