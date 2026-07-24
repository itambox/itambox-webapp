"""Issue #134 regression suite — Extras forms.

Several Extras forms historically resolved a missing single-tenant context by
choosing ``user.asset_holder_profiles.first()``. That predates the tenant-group
and *All accessible tenants* scopes, where ``get_current_tenant()`` is
intentionally ``None`` even though a precise authorized tenant set is active,
and it silently bound new records to an arbitrary (or missing) tenant.

AssetHolder is a domain profile, not an authorization source. The canonical
write contract these tests pin:

* a non-admin create under a single active tenant binds that tenant — even when
  the actor has NO AssetHolder profile (never leaves a global ``tenant=None``
  row);
* a non-admin create under a multi-tenant scope (tenant group / All accessible)
  has no unambiguous tenant, so it fails validation and persists nothing — it
  never picks the first (or any) AssetHolder profile, regardless of how many
  profiles exist or their ordering;
* editing an existing record never silently reassigns its tenant;
* ``ScheduledReportForm`` report/channel choices follow the active canonical
  read scope, not an arbitrary AssetHolder tenant.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from extras.forms import (
    AlertRuleForm,
    NotificationChannelForm,
    ReportTemplateForm,
    ScheduledReportForm,
)
from extras.models import (
    AlertRule,
    NotificationChannel,
    ReportTemplate,
    ScheduledReport,
)
from itambox.middleware import _current_user
from organization.models import AssetHolder, Role, Tenant

User = get_user_model()


class _ExtrasScopeFormTestBase(TestCase):
    """A genuine non-admin member with direct access to tenants X and Y."""

    def setUp(self):
        self._reset_scope()
        self.tenant_x = Tenant.objects.create(name='Scope X', slug='i134-x')
        self.tenant_y = Tenant.objects.create(name='Scope Y', slug='i134-y')
        self.role_x = Role.objects.create(tenant=self.tenant_x, name='Rx', permissions=[])
        self.role_y = Role.objects.create(tenant=self.tenant_y, name='Ry', permissions=[])
        self.member = User.objects.create_user(username='i134-member', password='pw')
        # Direct memberships => accessible_tenant_ids == {X, Y}. Not is_staff /
        # is_superuser, so the forms drop the tenant field and take the
        # non-admin write path under test.
        grant(self.member, self.tenant_x, self.role_x)
        grant(self.member, self.tenant_y, self.role_y)
        # A global (tenant=None) report template — always a valid ScheduledReport
        # `report` FK in any scope (ReportTemplate.allow_global_tenant=True).
        self.global_report = ReportTemplate.objects.create(
            name='i134 Global Report',
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
        )
        _current_user.set(self.member)

    def tearDown(self):
        self._reset_scope()

    @staticmethod
    def _reset_scope():
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_all_accessible(False)
        _current_user.set(None)

    def _single_tenant(self, tenant):
        set_current_tenant(tenant)
        set_current_tenant_group(None)
        set_current_all_accessible(False)

    def _all_accessible(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_all_accessible(True)

    def _make_profile(self, tenant, last_name, upn):
        return AssetHolder.objects.create(
            user=self.member, tenant=tenant,
            first_name='P', last_name=last_name, upn=upn,
        )

    def _cases(self):
        return [
            (ReportTemplateForm, self._report_data, ReportTemplate),
            (ScheduledReportForm, self._schedule_data, ScheduledReport),
            (AlertRuleForm, self._alert_data, AlertRule),
            (NotificationChannelForm, self._channel_data, NotificationChannel),
        ]

    def _report_data(self, name):
        return {
            'name': name,
            'report_type': ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            'style_preset': 'default',
        }

    def _schedule_data(self, name):
        return {
            'name': name,
            'report': self.global_report.pk,
            'frequency': ScheduledReport.FREQUENCY_DAILY,
            'format': ScheduledReport.FORMAT_HTML,
        }

    def _alert_data(self, name):
        return {
            'name': name,
            'alert_type': AlertRule.ALERT_TYPE_LOW_STOCK,
            'threshold_value': '5',
            'severity': AlertRule.SEVERITY_WARNING,
            'renotify_interval_days': '0',
        }

    def _channel_data(self, name):
        return {
            'name': name,
            'channel_type': NotificationChannel.TYPE_IN_APP,
            'enabled': True,
        }

    def _existing_in_x(self, model):
        name = f'{model.__name__} X'
        if model is ScheduledReport:
            return model.objects.create(
                name=name, report=self.global_report, tenant=self.tenant_x,
                frequency=ScheduledReport.FREQUENCY_DAILY,
                format=ScheduledReport.FORMAT_HTML,
            )
        if model is AlertRule:
            return model.objects.create(
                name=name, alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
                threshold_value=5, severity=AlertRule.SEVERITY_WARNING,
                tenant=self.tenant_x,
            )
        if model is NotificationChannel:
            return model.objects.create(
                name=name, channel_type=NotificationChannel.TYPE_IN_APP,
                tenant=self.tenant_x,
            )
        return model.objects.create(
            name=name, report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            tenant=self.tenant_x,
        )


class SingleTenantCreateBindsActiveTenantTests(_ExtrasScopeFormTestBase):
    def test_create_without_profile_binds_active_tenant(self):
        # No AssetHolder profile at all: a single-tenant scope must still bind
        # the active tenant, never leave a global (tenant=None) row.
        self.assertEqual(
            AssetHolder._base_manager.filter(user=self.member).count(), 0
        )
        self._single_tenant(self.tenant_x)
        for form_cls, data_builder, _model in self._cases():
            with self.subTest(form=form_cls.__name__):
                form = form_cls(data=data_builder(f'{form_cls.__name__}-single'))
                self.assertTrue(form.is_valid(), form.errors)
                obj = form.save()
                self.assertEqual(obj.tenant, self.tenant_x)


class MultiTenantCreateFailsClosedTests(_ExtrasScopeFormTestBase):
    def test_all_accessible_create_with_reversed_profiles_is_invalid_no_persist(self):
        # Two AssetHolder profiles in deliberately reversed creation order — the
        # old code's `.first()` picked one arbitrarily (order-dependent). The
        # canonical fix refuses the ambiguous multi-tenant create and persists
        # nothing.
        self._make_profile(self.tenant_y, last_name='Aaa', upn='y@i134.example.com')
        self._make_profile(self.tenant_x, last_name='Zzz', upn='x@i134.example.com')
        self._all_accessible()
        for form_cls, data_builder, model in self._cases():
            with self.subTest(form=form_cls.__name__):
                before = model._base_manager.count()
                form = form_cls(data=data_builder(f'{form_cls.__name__}-multi'))
                valid = form.is_valid()
                if valid:
                    form.save()
                self.assertFalse(
                    valid,
                    f'{form_cls.__name__} accepted an ambiguous multi-tenant create',
                )
                self.assertEqual(model._base_manager.count(), before)

    def test_all_accessible_create_without_profile_is_invalid_no_global_row(self):
        # No profile: the old code left tenant unset and minted a GLOBAL row.
        self.assertEqual(
            AssetHolder._base_manager.filter(user=self.member).count(), 0
        )
        self._all_accessible()
        for form_cls, data_builder, model in self._cases():
            with self.subTest(form=form_cls.__name__):
                before_total = model._base_manager.count()
                before_global = model._base_manager.filter(tenant__isnull=True).count()
                form = form_cls(data=data_builder(f'{form_cls.__name__}-noprofile'))
                valid = form.is_valid()
                if valid:
                    form.save()
                self.assertFalse(valid)
                self.assertEqual(model._base_manager.count(), before_total)
                self.assertEqual(
                    model._base_manager.filter(tenant__isnull=True).count(),
                    before_global,
                )


class EditRetainsTenantTests(_ExtrasScopeFormTestBase):
    def test_edit_under_all_accessible_retains_object_tenant(self):
        # Member holds a profile in Y; the old code reassigned an X-owned object
        # to Y on any save under a multi-tenant scope. The tenant must be
        # retained since no explicit, authorized change was made.
        self._make_profile(self.tenant_y, last_name='Aaa', upn='y2@i134.example.com')
        for form_cls, data_builder, model in self._cases():
            with self.subTest(form=form_cls.__name__):
                obj = self._existing_in_x(model)
                self._all_accessible()
                form = form_cls(data=data_builder(obj.name), instance=obj)
                self.assertTrue(form.is_valid(), form.errors)
                form.save()
                reloaded = model._base_manager.get(pk=obj.pk)
                self.assertEqual(reloaded.tenant, self.tenant_x)


class ScheduledReportChoicesFollowScopeTests(_ExtrasScopeFormTestBase):
    def test_choices_span_all_accessible_scope_not_profile_tenant(self):
        # Member has a profile ONLY in X; the old code filtered ScheduledReport
        # choices to that single profile tenant. Under All accessible ({X, Y})
        # the choices must include Y-owned records the member can reach.
        self._make_profile(self.tenant_x, last_name='Zzz', upn='x3@i134.example.com')
        report_y = ReportTemplate.objects.create(
            name='i134 Y Report',
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            tenant=self.tenant_y,
        )
        channel_y = NotificationChannel.objects.create(
            name='i134 Y Channel',
            channel_type=NotificationChannel.TYPE_IN_APP,
            tenant=self.tenant_y,
        )
        self._all_accessible()
        form = ScheduledReportForm()
        report_ids = set(form.fields['report'].queryset.values_list('pk', flat=True))
        channel_ids = set(form.fields['channels'].queryset.values_list('pk', flat=True))
        self.assertIn(report_y.pk, report_ids)
        self.assertIn(channel_y.pk, channel_ids)
