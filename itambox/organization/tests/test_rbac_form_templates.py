"""Regression tests for the bespoke RBAC create/edit form templates (fix #12, §6).

The three RBAC create/edit flows — Role, Membership, UserGroup — should read as
one system. Role already has a bespoke screen (``organization/role_form.html``).
This fix adds bespoke templates for Membership and UserGroup:

    * ``organization/memberships/membership_form.html``
    * ``users/usergroups/usergroup_form.html``

``MembershipCreateView`` / ``MembershipEditView`` (organization/views/membership_views.py)
and ``UserGroupEditView`` (users/views.py) now set ``template_name`` to these bespoke
templates, so the three RBAC create/edit flows render as one system.
"""
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin
from organization.forms import MembershipForm
from users.forms import UserGroupForm

User = get_user_model()


class RBACFormTemplateTests(TenantTestMixin, TestCase):
    """The Membership and UserGroup create pages render, and the new bespoke
    templates are valid against the real forms."""

    def setUp(self):
        super().setup_tenant_context()
        self.admin = User.objects.create_user(
            username='rbac-form-admin',
            password='password123',
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.admin)

    def test_membership_create_page_renders(self):
        """Membership create page renders with the bespoke RBAC form template."""
        url = reverse('organization:membership_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'organization/memberships/membership_form.html')

    def test_usergroup_create_page_renders(self):
        """UserGroup create page renders with the bespoke RBAC form template."""
        url = reverse('users:usergroup_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'users/usergroups/usergroup_form.html')

    def test_bespoke_templates_exist_and_compile(self):
        """Both new bespoke templates load and compile (no template-syntax errors).

        A broken template here would only surface once the deferred view wiring
        lands, so guard the syntax now.
        """
        # get_template() raises TemplateSyntaxError / TemplateDoesNotExist on failure.
        self.assertIsNotNone(get_template('organization/memberships/membership_form.html'))
        self.assertIsNotNone(get_template('users/usergroups/usergroup_form.html'))

    def test_bespoke_templates_render_form(self):
        """The bespoke templates render the real forms' crispy output.

        Rendered against a minimal base (``base_template`` is overridable via the
        ``{% extends base_template|default:"layout.html" %}`` hook) so the test
        exercises the template's own ``content`` block — the crispy ``<form>``,
        its fields, and buttons — without the full page chrome that needs a live
        request. This proves the deferred one-line view switch is safe.
        """
        minimal_base = Template('{% block content %}{% endblock %}')

        membership_ctx = Context({
            'base_template': minimal_base,
            'form': MembershipForm(user=self.admin, tenant=self.tenant),
            'title': 'Create membership',
            'is_editing': False,
            'cancel_url': reverse('organization:membership_list'),
        })
        membership_html = get_template(
            'organization/memberships/membership_form.html'
        ).template.render(membership_ctx)
        self.assertIn('<form', membership_html)
        self.assertIn('name="user"', membership_html)

        usergroup_ctx = Context({
            'base_template': minimal_base,
            'form': UserGroupForm(user=self.admin),
            'title': 'Create user group',
            'is_editing': False,
            'cancel_url': reverse('users:usergroup_list'),
        })
        usergroup_html = get_template(
            'users/usergroups/usergroup_form.html'
        ).template.render(usergroup_ctx)
        self.assertIn('<form', usergroup_html)
        self.assertIn('name="name"', usergroup_html)
