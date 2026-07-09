"""The extras config lists (alert rules, alert channels, report templates, report
schedules) gained a bulk-select checkbox column + search/filter wiring. Verify each
list view declares its filterset + filter form, the table carries the ToggleColumn
checkbox, the search narrows results, and the bulk-delete routes are wired.

Introspection (not live Client requests) is deliberate: a list-view GET as a
superuser bakes/caches process-global state that pollutes later tests in the run.
The live rendering is covered by manual browser verification.
"""
from django.test import TestCase
from django.urls import resolve, reverse

from core.tests.mixins import TenantTestMixin
from extras.models import (
    AlertRule, NotificationChannel, ReportTemplate, ScheduledReport,
)


class ExtrasListWiringTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Wiring Tenant', slug='wiring-tenant')

    def tearDown(self):
        self.clear_tenant_context()

    def test_list_views_declare_filter_and_checkbox(self):
        from extras import views
        from extras.filters import (
            AlertRuleFilterSet, NotificationChannelFilterSet,
            ReportTemplateFilterSet, ScheduledReportFilterSet,
        )
        from extras.forms import (
            AlertRuleFilterForm, NotificationChannelFilterForm,
            ReportTemplateFilterForm, ScheduledReportFilterForm,
        )
        cases = [
            (views.AlertRuleListView, AlertRuleFilterSet, AlertRuleFilterForm,
             ['alert_type', 'severity', 'is_active']),
            (views.NotificationChannelListView, NotificationChannelFilterSet,
             NotificationChannelFilterForm, ['channel_type', 'enabled']),
            (views.ReportTemplateListView, ReportTemplateFilterSet,
             ReportTemplateFilterForm, ['report_type', 'style_preset']),
            (views.ScheduledReportListView, ScheduledReportFilterSet,
             ScheduledReportFilterForm, ['report', 'frequency', 'format', 'is_active']),
        ]
        for view_cls, filterset, filterform, fields in cases:
            label = view_cls.__name__
            self.assertIs(view_cls.filterset, filterset, label)
            self.assertIs(view_cls.filterset_form, filterform, label)
            # ToggleColumn renders the bulk-select checkbox column.
            self.assertIn('pk', view_cls.table.base_columns, label)
            # The filter form drives the quick-search box (`q`) + the filter panel.
            form_fields = filterform().fields
            self.assertIn('q', form_fields, label)
            for field in fields:
                self.assertIn(field, form_fields, f"{label}: {field}")

    def test_report_template_filterset_search_narrows(self):
        from extras.filters import ReportTemplateFilterSet
        with self.tenant_context(self.tenant):
            ReportTemplate.objects.create(
                name='Alpha Report', report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
                tenant=self.tenant)
            ReportTemplate.objects.create(
                name='Beta Report', report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
                tenant=self.tenant)
            matched = ReportTemplateFilterSet(
                {'q': 'Alpha'}, queryset=ReportTemplate.objects.all()).qs
            names = set(matched.values_list('name', flat=True))
        self.assertEqual(names, {'Alpha Report'})

    def test_bulk_delete_routes_wired_to_each_model(self):
        from itambox.views.generic import ObjectBulkDeleteView
        cases = [
            ('alertrule_bulk_delete', AlertRule),
            ('notificationchannel_bulk_delete', NotificationChannel),
            ('reporttemplate_bulk_delete', ReportTemplate),
            ('scheduledreport_bulk_delete', ScheduledReport),
        ]
        for url_name, model in cases:
            view_cls = resolve(reverse(f'extras:{url_name}')).func.view_class
            self.assertTrue(issubclass(view_cls, ObjectBulkDeleteView), url_name)
            self.assertEqual(view_cls.queryset.model, model, url_name)
