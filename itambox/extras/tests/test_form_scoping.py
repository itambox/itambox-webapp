"""Explicit scoping declarations of the extras forms (#584, WP3).

The extras forms declare tenant scoping, tenant requiredness and TomSelect
behaviour through ``TenantScopedFormMixin`` instead of relying on the global
form patches. These assertions pin each declaration directly.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from extras.dashboard.forms import DashboardWidgetAddForm, DashboardWidgetConfigForm
from extras.dashboard.widgets import WidgetConfigForm
from extras.forms import (
    AlertRuleForm,
    CustomFieldForm,
    CustomFieldsetForm,
    EventRuleForm,
    ExportTemplateForm,
    LabelTemplateForm,
    NotificationChannelForm,
    ReportTemplateForm,
    SavedFilterForm,
    ScheduledReportForm,
    WebhookEndpointForm,
)
from itambox.middleware import set_current_user
from organization.models import Tenant

FORMS = (
    CustomFieldForm,
    CustomFieldsetForm,
    SavedFilterForm,
    WebhookEndpointForm,
    EventRuleForm,
    LabelTemplateForm,
    ExportTemplateForm,
    ReportTemplateForm,
    ScheduledReportForm,
    AlertRuleForm,
    NotificationChannelForm,
    DashboardWidgetAddForm,
    DashboardWidgetConfigForm,
    WidgetConfigForm,
)
REQUIRED_TENANT_FORMS = (WebhookEndpointForm, EventRuleForm, AlertRuleForm, NotificationChannelForm)


class ExtrasFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="ext-fs-a")
        Tenant.objects.create(name="Tenant B", slug="ext-fs-b")
        self.admin = get_user_model().objects.create_superuser("ext-fs-admin", "ext-fs@example.com", "pw")
        set_current_user(self.admin)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    def test_forms_use_the_explicit_mixin(self):
        for form_class in FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_tenant_requiredness_is_declared(self):
        for form_class in REQUIRED_TENANT_FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertIs(form_class.tenant_required, True)
                self.assertIs(form_class().fields["tenant"].required, True)

    def test_report_forms_keep_optional_tenant(self):
        for form_class in (ReportTemplateForm, ScheduledReportForm):
            with self.subTest(form=form_class.__name__):
                self.assertIs(form_class.tenant_required, False)
                self.assertIs(form_class().fields["tenant"].required, False)

    def test_model_choice_fields_are_scoped_explicitly(self):
        expected = (
            (ReportTemplateForm, ("tenant", "filter_tenants")),
            (ScheduledReportForm, ("tenant", "filter_tenants", "report", "channels")),
            (EventRuleForm, ("tenant", "webhook")),
            (AlertRuleForm, ("tenant", "channels")),
            (WebhookEndpointForm, ("tenant",)),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_tom_select_is_declared_on_selects(self):
        form = AlertRuleForm()
        for name in ("alert_type", "severity", "tenant"):
            with self.subTest(field=name):
                self.assertIn("data-tom-select", form.fields[name].widget.attrs)
        self.assertIn("data-tom-select", DashboardWidgetAddForm().fields["widget"].widget.attrs)
        self.assertIn("data-tom-select", DashboardWidgetConfigForm().fields["style"].widget.attrs)
