"""WS4-2: the report-template edit form must not splice ``included_columns``
into an HTML attribute via ``|safe`` (latent attribute breakout).

The saved column sequence is now emitted through Django's ``{% json_script %}``
(``|json_script``), which autoescapes ``<``/``>``/``&`` and confines the value
to a ``<script type="application/json">`` element instead of a ``data-*``
attribute. ``included_columns`` is constrained to ``COLUMN_CHOICES`` by the
form today, so these tests write a hostile value straight onto the model
(bypassing the form) to prove the template is robust regardless of the writer.
"""
from django.test import TestCase
from django.urls import reverse

from extras.models import ReportTemplate
from core.tests.mixins import TenantTestMixin


class ReportTemplateFormSavedSequenceXSSTests(TenantTestMixin, TestCase):
    # A column value crafted to break out of an HTML attribute and inject markup.
    HOSTILE_COL = 'name"><img src=x onerror=alert(1)>'

    def setUp(self):
        self.setup_tenant_context(name='XSS Tenant', slug='xss-rep')
        self.set_active_tenant(self.tenant)
        self.template = ReportTemplate.objects.create(
            name='Hostile Cols',
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=['asset_tag', self.HOSTILE_COL, 'status'],
        )

    def _login_superuser(self):
        # Superuser + active_tenant_id in session: TenantMiddleware resolves the
        # tenant, the membership backend short-circuits the change perm, and the
        # tenant-scoped queryset finds the object.
        self.client.force_login(self.tenant_admin)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def test_saved_sequence_uses_json_script_not_safe_attribute(self):
        self._login_superuser()
        url = reverse('extras:reporttemplate_update', kwargs={'pk': self.template.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        # The json_script element is present and carries the expected id.
        self.assertIn(
            '<script id="report-template-saved-sequence" type="application/json">',
            body,
        )
        # The legacy |safe data-attribute sink is gone.
        self.assertNotIn('data-saved-sequence', body)
        self.assertNotIn('id="report-template-metadata"', body)

    def test_hostile_column_value_cannot_break_out(self):
        self._login_superuser()
        url = reverse('extras:reporttemplate_update', kwargs={'pk': self.template.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        # The raw payload must never appear verbatim: json_script escapes the
        # angle brackets, so no real <img ...> tag is emitted into the document.
        self.assertNotIn('<img src=x onerror=alert(1)>', body)
        # The double-quote in the value must not survive as a literal " that
        # could terminate an HTML attribute; angle brackets are unicode-escaped.
        self.assertIn('\\u003C', body)  # < escaped
        self.assertIn('\\u003E', body)  # > escaped
