"""Rendering, delivery, security, and form behaviour for ExportTemplate.

The CRUD permission gate is covered separately in test_export_template_perms.py;
this module exercises the parts that previously did not actually work — the
whole-queryset Jinja render, as_attachment delivery, the hardened sandbox, and the
authoring form (curated content types + template validation).
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetType, Manufacturer, StatusLabel
from extras.forms import ExportTemplateForm, exportable_content_types
from extras.models import ExportTemplate

User = get_user_model()


def _make_template(model, **overrides):
    ct = ContentType.objects.get_for_model(model)
    defaults = dict(
        name='Tmpl',
        content_type=ct,
        template_code='{% for obj in queryset %}{{ obj.name }}\n{% endfor %}',
        mime_type='text/csv',
        file_extension='csv',
        as_attachment=True,
    )
    defaults.update(overrides)
    return ExportTemplate.objects.create(**defaults)


class ExportTemplateEndpointTests(TestCase):
    """End-to-end through the object_export view (template_id == pk)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='pw', is_staff=True, is_superuser=True
        )
        self.client.login(username='admin', password='pw')
        self.status = StatusLabel.objects.create(name='Active', slug='active')
        mfr = Manufacturer.objects.create(name='Dell', slug='dell')
        atype = AssetType.objects.create(manufacturer=mfr, model='Laptop', slug='laptop')
        self.a1 = Asset.objects.create(name='Alpha', asset_tag='TAG-001', status=self.status, asset_type=atype)
        self.a2 = Asset.objects.create(name='Beta', asset_tag='TAG-002', status=self.status, asset_type=atype)

    def _export_url(self, template):
        return reverse('object_export', kwargs={
            'app_label': 'assets', 'model_name': 'asset', 'template_id': template.pk,
        })

    def test_csv_template_renders_header_and_all_rows(self):
        tmpl = _make_template(
            Asset,
            name='Asset CSV',
            template_code=(
                'Asset Tag,Name\n'
                '{% for obj in queryset %}{{ obj.asset_tag|csv_safe }},{{ obj.name|csv_safe }}\n{% endfor %}'
            ),
        )
        resp = self.client.get(self._export_url(tmpl))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(resp['Content-Disposition'], 'attachment; filename="asset_export.csv"')
        body = resp.content.decode()
        lines = [ln for ln in body.splitlines() if ln]
        self.assertEqual(lines[0], 'Asset Tag,Name')
        self.assertIn('TAG-001,Alpha', lines)
        self.assertIn('TAG-002,Beta', lines)

    def test_json_template_with_tojson_is_valid_json(self):
        tmpl = _make_template(
            Asset,
            name='Asset JSON',
            mime_type='application/json',
            file_extension='json',
            template_code=(
                '[\n{% for obj in queryset %}'
                '  {"tag": {{ obj.asset_tag|tojson }}, "name": {{ obj.name|tojson }}}'
                '{% if not loop.last %},{% endif %}\n{% endfor %}]\n'
            ),
        )
        resp = self.client.get(self._export_url(tmpl))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/json')
        data = json.loads(resp.content.decode())
        self.assertEqual({d['name'] for d in data}, {'Alpha', 'Beta'})

    def test_as_attachment_false_serves_inline(self):
        tmpl = _make_template(Asset, name='Inline', as_attachment=False)
        resp = self.client.get(self._export_url(tmpl))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.has_header('Content-Disposition'))
        # nosniff is set regardless of delivery mode.
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    def test_empty_queryset_still_emits_header(self):
        tmpl = _make_template(
            Asset,
            name='Header CSV',
            template_code='Asset Tag,Name\n{% for obj in queryset %}{{ obj.asset_tag }},{{ obj.name }}\n{% endfor %}',
        )
        # ?export_scope=filtered with a non-matching search → empty queryset.
        resp = self.client.get(f"{self._export_url(tmpl)}?export_scope=filtered&q=NoSuchThing")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Asset Tag,Name', body)
        self.assertNotIn('TAG-001', body)
        self.assertNotIn('TAG-002', body)

    def test_blank_mime_falls_back_to_default(self):
        tmpl = _make_template(Asset, name='No Mime', mime_type='')
        resp = self.client.get(self._export_url(tmpl))
        self.assertEqual(resp['Content-Type'], ExportTemplate.DEFAULT_MIME_TYPE)


class ExportTemplateRenderModelTests(TestCase):
    """Direct render()/environment behaviour, independent of the HTTP layer."""

    def setUp(self):
        self.status = StatusLabel.objects.create(name='Active', slug='active')
        mfr = Manufacturer.objects.create(name='Dell', slug='dell')
        atype = AssetType.objects.create(manufacturer=mfr, model='Laptop', slug='laptop')
        self.asset = Asset.objects.create(
            name='<b>x</b>', asset_tag='T1', status=self.status, asset_type=atype,
        )

    def test_render_is_single_pass_over_queryset(self):
        tmpl = _make_template(
            Asset, template_code='{{ queryset|length }} rows',
        )
        self.assertEqual(tmpl.render(Asset.objects.all()), '1 rows')

    def test_html_mime_autoescapes_tenant_data(self):
        tmpl = _make_template(
            Asset, mime_type='text/html', file_extension='html',
            template_code='{% for obj in queryset %}{{ obj.name }}{% endfor %}',
        )
        self.assertIn('&lt;b&gt;', tmpl.render(Asset.objects.all()))

    def test_csv_mime_does_not_autoescape(self):
        tmpl = _make_template(
            Asset, template_code='{% for obj in queryset %}{{ obj.name }}{% endfor %}',
        )
        self.assertEqual(tmpl.render(Asset.objects.all()), '<b>x</b>')

    def test_sandbox_neutralises_dunder_access(self):
        # The sandbox intercepts access to dunder attributes: ''.__class__ resolves
        # to an (empty-rendering) Undefined instead of leaking <class 'str'>, which a
        # plain Jinja Environment would print. That neutralisation is the guarantee.
        tmpl = _make_template(Asset, template_code="X{{ ''.__class__ }}Y")
        out = tmpl.render(Asset.objects.all())
        self.assertEqual(out, 'XY')
        self.assertNotIn('class', out)

    def test_ssti_escape_filters_removed(self):
        env = ExportTemplate.get_jinja_environment()
        for unsafe in ('attr', 'format', 'format_map', 'map', 'pprint', 'xmlattr'):
            self.assertNotIn(unsafe, env.filters)
        for unsafe in ('cycler', 'joiner', 'namespace', 'lipsum'):
            self.assertNotIn(unsafe, env.globals)

    def test_csv_safe_filter_neutralises_formula_injection(self):
        self.asset.name = '=cmd'
        self.asset.save()
        tmpl = _make_template(
            Asset, template_code='{% for obj in queryset %}{{ obj.name|csv_safe }}{% endfor %}',
        )
        self.assertEqual(tmpl.render(Asset.objects.all()), "'=cmd")


class ExportTemplateFormTests(TestCase):
    def setUp(self):
        self.asset_ct = ContentType.objects.get_for_model(Asset)

    def _valid_data(self, **overrides):
        data = dict(
            name='My Template',
            content_type=self.asset_ct.pk,
            description='',
            template_code='{% for obj in queryset %}{{ obj.name }}\n{% endfor %}',
            mime_type='text/csv',
            file_extension='csv',
            as_attachment=True,
        )
        data.update(overrides)
        return data

    def test_valid_form_saves(self):
        form = ExportTemplateForm(data=self._valid_data())
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.content_type, self.asset_ct)

    def test_invalid_template_syntax_rejected(self):
        form = ExportTemplateForm(data=self._valid_data(template_code='{% for obj in %}'))
        self.assertFalse(form.is_valid())
        self.assertIn('template_code', form.errors)

    def test_file_extension_normalised(self):
        form = ExportTemplateForm(data=self._valid_data(file_extension='.JSON'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['file_extension'], 'json')

    def test_content_type_choices_curated(self):
        choices = exportable_content_types()
        self.assertIn(self.asset_ct, choices)
        # A generated-log / non-exportable model must be absent from the picker.
        objectchange_ct = ContentType.objects.get(app_label='core', model='objectchange')
        self.assertNotIn(objectchange_ct, choices)
        # The form's field is restricted to the curated set.
        form = ExportTemplateForm()
        self.assertNotIn(objectchange_ct, form.fields['content_type'].queryset)


class ExportTemplateTenantIsolationTests(TestCase):
    """A custom-template export must only render the active tenant's rows — the
    template path shares ObjectExportView's tenant-scoped queryset, but exercise it
    explicitly so a future regression in scoping is caught here too."""

    def setUp(self):
        from organization.models import Tenant, TenantRole, TenantMembership
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='ten-a-exp')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='ten-b-exp')
        role = TenantRole.objects.create(
            tenant=self.tenant_a, name='Viewer',
            permissions=['assets.view_asset', 'extras.view_exporttemplate'],
        )
        self.member = User.objects.create_user(username='iso-member', password='pw')
        TenantMembership.objects.create(user=self.member, tenant=self.tenant_a, role=role)

        status = StatusLabel.objects.create(name='Active', slug='active')
        mfr = Manufacturer.objects.create(name='Dell', slug='dell')
        atype = AssetType.objects.create(manufacturer=mfr, model='Laptop', slug='laptop')
        Asset.objects.create(name='AlphaInTenantA', asset_tag='ISO-A', tenant=self.tenant_a, status=status, asset_type=atype)
        Asset.objects.create(name='BetaInTenantB', asset_tag='ISO-B', tenant=self.tenant_b, status=status, asset_type=atype)

        self.tmpl = _make_template(
            Asset, name='Iso CSV',
            template_code='{% for obj in queryset %}{{ obj.name }}\n{% endfor %}',
        )

    def test_export_only_returns_active_tenant_rows(self):
        self.client.force_login(self.member)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_a.pk
        session.save()
        url = reverse('object_export', kwargs={
            'app_label': 'assets', 'model_name': 'asset', 'template_id': self.tmpl.pk,
        })
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('AlphaInTenantA', body)
        self.assertNotIn('BetaInTenantB', body)
