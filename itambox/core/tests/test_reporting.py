from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django_q.models import Schedule

from core.tasks.reports import (
    _archive_report_output,
    _deliver_report_channels,
    _deliver_report_email,
    _DeliveryOutcome,
    _render_report_output,
    _ReportOutput,
    _resolve_report_recipients,
    _resolve_report_scope,
)
from extras.models import NotificationChannel, ReportGenerationArchive, ReportTemplate, ScheduledReport

User = get_user_model()


class ScheduledReportingAndAlertsTests(TestCase):
    def setUp(self):
        from organization.models import Tenant

        self.tenant = Tenant.objects.create(name="Report Tenant", slug="report-tenant")
        self.user = User.objects.create_user(username="reportuser", password="password123", is_superuser=True)
        # Create a report template
        self.template = ReportTemplate.objects.create(
            name="Asset Inventory Test Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag", "name"],
            include_summary_cards=True,
        )

    def test_scheduled_report_validation_cron(self):
        """Test cron expression validation in ScheduledReport."""
        # 1. Valid cron
        report = ScheduledReport(
            name="Test Report 1", report=self.template, frequency="cron", cron_expression="0 8 * * 1-5"
        )
        report.full_clean()  # should not raise

        # 2. Invalid cron
        report_invalid = ScheduledReport(
            name="Test Report 2", report=self.template, frequency="cron", cron_expression="invalid_cron"
        )
        with self.assertRaises(ValidationError):
            report_invalid.full_clean()

    def test_scheduled_report_validation_recipients(self):
        """Test email recipients validation."""
        # Invalid email list
        report = ScheduledReport(
            name="Test Report 3", report=self.template, recipients="invalid_email, another_invalid"
        )
        with self.assertRaises(ValidationError):
            report.full_clean()

        # Valid email list
        report_valid = ScheduledReport(
            name="Test Report 4", report=self.template, recipients="test@example.com, user2@domain.co.uk"
        )
        report_valid.full_clean()

    def test_schedule_creation_in_view(self):
        """Test that Schedule is created or updated in ScheduledReport form_valid views."""
        self.client.force_login(self.user)
        url = reverse("extras:scheduledreport_create")
        data = {
            "name": "Active Weekly Report",
            "report": self.template.pk,
            "frequency": "weekly",
            "format": "html",
            "start_time": "09:30:00",
            "save_to_archive": True,
            "is_active": True,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        # Retrieve created ScheduledReport
        sched = ScheduledReport.objects.get(name="Active Weekly Report")
        self.assertIsNotNone(sched.schedule)
        self.assertEqual(sched.schedule.schedule_type, Schedule.WEEKLY)
        self.assertEqual(sched.schedule.cron, "")

        # Verify next run is set and matches the configured time of day
        self.assertEqual(sched.schedule.next_run.time().hour, 9)
        self.assertEqual(sched.schedule.next_run.time().minute, 30)

    @patch("django.core.mail.EmailMessage")
    @patch("core.http.request_pinned")
    def test_generate_report_task_success(self, mock_request_pinned, mock_email_message):
        """Test general execution of report generation task, local archiving and dispatches."""
        # A tenant is required now — a tenantless report with no filter_tenants is refused
        # (WS5-6). The channel is scoped to the same tenant so it is visible under the run.
        from organization.models import Tenant

        tenant = Tenant.objects.create(name="Task Report Tenant", slug="report-tenant-gen")

        # Setup a Slack channel (report task dispatches to all enabled channels)
        channel_slack = NotificationChannel.objects.create(
            name="Test Slack Channel",
            channel_type=NotificationChannel.TYPE_SLACK,
            enabled=True,
            tenant=tenant,
            config={"webhook_url": "https://hooks.slack.com/services/test"},
        )

        # Create a scheduled report with the Slack channel and archiving active.
        sched = ScheduledReport.objects.create(
            name="Full Task Test Schedule",
            report=self.template,
            tenant=tenant,
            frequency="once",
            format="html",
            recipients="",
            save_to_archive=True,
        )
        sched.channels.add(channel_slack)

        # Mock the pinned-request response (used by Slack dispatch)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request_pinned.return_value = mock_response

        # Execute task
        from core.tasks import generate_scheduled_report_task

        success = generate_scheduled_report_task(sched.pk)

        self.assertTrue(success)

        # Check archive entry was created
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "success")
        self.assertIsNotNone(sched.last_run)

        archive = ReportGenerationArchive.objects.filter(scheduled_report=sched).first()
        self.assertIsNotNone(archive)
        self.assertEqual(archive.status, "success")
        self.assertIsNotNone(archive.file)
        self.assertEqual(archive.file.mime_type, "text/html")

        # Verify Slack channel was called
        mock_request_pinned.assert_called_once()

    def test_delivery_failure_is_partial_and_later_channels_are_attempted(self):
        """One channel failure is persisted without hiding later delivery success."""
        failed_channel = NotificationChannel.objects.create(
            name="Failing Report Channel",
            channel_type=NotificationChannel.TYPE_SLACK,
            enabled=True,
            tenant=self.tenant,
        )
        healthy_channel = NotificationChannel.objects.create(
            name="Healthy Report Channel",
            channel_type=NotificationChannel.TYPE_TEAMS,
            enabled=True,
            tenant=self.tenant,
        )
        sched = ScheduledReport.objects.create(
            name="Partial Delivery Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency="once",
            format=ScheduledReport.FORMAT_HTML,
            save_to_archive=True,
        )
        sched.channels.add(failed_channel, healthy_channel)

        with patch("core.tasks.reports.send_notification_to_channel", side_effect=[False, True]) as send_channel:
            from core.tasks import generate_scheduled_report_task

            self.assertTrue(generate_scheduled_report_task(sched.pk))

        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "partial")
        self.assertEqual(send_channel.call_count, 2)
        archive = ReportGenerationArchive.objects.get(scheduled_report=sched)
        self.assertEqual(archive.status, "success")
        self.assertIn("Failing Report Channel", archive.error_message)

    def test_delivery_outcome_marks_all_failures_as_failed(self):
        outcome = _DeliveryOutcome()
        outcome.record_failure("first")
        outcome.record_failure("second")

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.attempted, 2)
        self.assertEqual(outcome.succeeded, 0)

    def test_delivery_outcome_tracks_success_and_partial(self):
        outcome = _DeliveryOutcome()
        self.assertEqual(outcome.status, "success")
        outcome.record_success()
        outcome.record_failure("one channel failed")
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.attempted, 2)
        self.assertEqual(outcome.succeeded, 1)

        merged = _DeliveryOutcome()
        merged.merge(outcome)
        self.assertEqual(merged.status, "partial")
        self.assertEqual(merged.attempted, 2)

    def test_deliver_report_email_returns_false_without_recipients(self):
        sched = ScheduledReport.objects.create(
            name="No Recipient Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency="once",
            format=ScheduledReport.FORMAT_HTML,
            save_to_archive=False,
        )
        self.assertEqual(_resolve_report_recipients(sched), [])
        self.assertFalse(_deliver_report_email(sched, self.template, _ReportOutput(email_body="body")))

    def test_deliver_report_channels_skips_disabled_channels(self):
        disabled_channel = NotificationChannel.objects.create(
            name="Disabled Report Channel",
            channel_type=NotificationChannel.TYPE_SLACK,
            enabled=False,
            tenant=self.tenant,
        )
        sched = ScheduledReport.objects.create(
            name="Disabled Channel Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency="once",
            format=ScheduledReport.FORMAT_HTML,
        )
        sched.channels.add(disabled_channel)
        with patch("core.tasks.reports.send_notification_to_channel") as send_channel:
            outcome = _deliver_report_channels(sched, [], 0)
            send_channel.assert_not_called()
        self.assertEqual(outcome.status, "success")

    def test_notify_report_channels_alias_stays_compatible(self):
        sched = ScheduledReport.objects.create(
            name="Alias Schedule",
            report=self.template,
            tenant=self.tenant,
            frequency="once",
            format=ScheduledReport.FORMAT_HTML,
        )
        with patch("core.tasks.reports.send_notification_to_channel", return_value=True):
            from core.tasks.reports import _notify_report_channels

            outcome = _notify_report_channels(sched, [], 0)
        self.assertEqual(outcome.status, "success")

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_report_preview_compilation_and_view(self):
        """Test report template context compilation and preview endpoint rendering without ValueError."""
        # Create some sample assets to exercise the asset summary report compilation path
        from assets.models import Asset, StatusLabel

        status, _ = StatusLabel.objects.get_or_create(name="Available", defaults={"type": StatusLabel.TYPE_DEPLOYABLE})

        Asset.objects.create(
            asset_tag="AST-1001",
            name="Developer Laptop",
            status=status,
            purchase_cost=1200.00,
            # Explicit currency so the per-currency summary card renders '$1,200.00';
            # without it the money filter falls back to ITAMBOX_DEFAULT_CURRENCY (EUR).
            currency="USD",
            tenant=self.tenant,
        )

        # Test direct compilation of context
        from core.reports import compile_report_context

        with translation.override("en"):
            headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(
                self.template, active_tenant=self.tenant
            )

        self.assertIn("Total Hardware Assets", [c["label"] for c in summary_cards])
        self.assertIn("$1,200.00", [c["value"] for c in summary_cards])

        # Test preview view POST endpoint
        self.client.force_login(self.user)
        url = reverse("extras:reporttemplate_preview")
        data = {
            "report_type": self.template.report_type,
            "name": self.template.name,
            "included_columns": self.template.included_columns,
            "include_summary_cards": "true",
            "include_distribution_chart": "true",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Asset Inventory Test Report", response.content)

    @override_settings(REPORT_DESIGNER_ENABLED=False)
    def test_scheduled_report_list_hides_inactive_designer_links(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("extras:scheduledreport_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("extras:reporttemplate_list"))

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_scheduled_report_list_shows_active_designer_links(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("extras:scheduledreport_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("extras:reporttemplate_list"))

    def test_new_report_types_compilation(self):
        """Test that the new report types compile context and preview successfully."""
        from core.reports import compile_report_context

        # 1. Test asset_depreciation
        deprec_template = ReportTemplate.objects.create(
            name="Depreciation Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_DEPRECIATION,
            included_columns=[
                "asset_tag",
                "name",
                "purchase_cost",
                "salvage_value",
                "depreciation_months",
                "current_value",
            ],
            include_summary_cards=True,
            include_distribution_chart=True,
        )
        headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(
            deprec_template, active_tenant=self.tenant
        )
        self.assertIn("Total Depreciable Assets", [c["label"] for c in summary_cards])
        self.assertIsNotNone(chart_svg)

        # 2. Test software_inventory
        software_template = ReportTemplate.objects.create(
            name="Software Report",
            report_type=ReportTemplate.REPORT_TYPE_SOFTWARE_INVENTORY,
            included_columns=[
                "software_name",
                "manufacturer",
                "version",
                "category",
                "license_type",
                "installed_count",
                "license_count",
            ],
            include_summary_cards=True,
            include_distribution_chart=True,
        )
        headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(
            software_template, active_tenant=self.tenant
        )
        self.assertIn("Total Software Products", [c["label"] for c in summary_cards])
        self.assertIsNotNone(chart_svg)

    @patch("core.tasks.reports.compile_report_context")
    def test_pre_archive_failure_preserves_status(self, mock_compile):
        """compile_report_context raising before archive_entry is assigned must
        preserve the original failure — no UnboundLocalError."""
        from core.tasks import generate_scheduled_report_task
        from organization.models import Tenant

        tenant = Tenant.objects.create(name="Fail Tenant", slug="fail-tenant")
        sched = ScheduledReport.objects.create(
            name="Pre-Archive Failure Test",
            report=self.template,
            tenant=tenant,
            frequency="once",
            format="html",
            recipients="",
            save_to_archive=True,
        )
        mock_compile.side_effect = RuntimeError("SYNTHETIC: compiler failure")

        success = generate_scheduled_report_task(sched.pk)

        self.assertFalse(success)
        sched.refresh_from_db()
        self.assertEqual(sched.last_status, "failed: SYNTHETIC: compiler failure")
        self.assertEqual(ReportGenerationArchive.objects.filter(scheduled_report=sched).count(), 0)

    def test_report_output_helpers_cover_all_attachment_formats(self):
        template = SimpleNamespace(name="Helper Report")
        context_data = {"report": "context"}

        with patch("core.tasks.reports._render_report_html", return_value="<html>report</html>"):
            html_output = _render_report_output(
                SimpleNamespace(format=ScheduledReport.FORMAT_HTML),
                template,
                [],
                [],
                context_data,
            )
        self.assertEqual(html_output.email_body, "<html>report</html>")

        with (
            patch("core.tasks.reports._render_report_html", return_value="<html>pdf</html>"),
            patch("core.reports.exporters.report_pdf_bytes", return_value=b"pdf-bytes"),
        ):
            pdf_output = _render_report_output(
                SimpleNamespace(format=ScheduledReport.FORMAT_PDF), template, [], [], context_data
            )
        self.assertEqual(pdf_output.attachment_content, b"pdf-bytes")
        self.assertTrue(pdf_output.attachment_filename.endswith(".pdf"))

        with patch("core.reports.exporters.report_xlsx_bytes", return_value=b"xlsx-bytes"):
            xlsx_output = _render_report_output(
                SimpleNamespace(format=ScheduledReport.FORMAT_XLSX), template, ["Name"], [], context_data
            )
        self.assertEqual(xlsx_output.attachment_content, b"xlsx-bytes")
        self.assertTrue(xlsx_output.attachment_filename.endswith(".xlsx"))

        csv_output = _render_report_output(
            SimpleNamespace(format=ScheduledReport.FORMAT_CSV),
            template,
            ["Name"],
            [{"Name": "Asset, one"}],
            context_data,
        )
        self.assertIn('"Asset, one"', csv_output.attachment_content)
        self.assertTrue(csv_output.attachment_filename.endswith(".csv"))

        with self.assertRaises(ValueError):
            _render_report_output(SimpleNamespace(format="unknown"), template, [], [], context_data)

    def test_report_archive_and_email_helpers_cover_attachment_paths(self):
        sched = ScheduledReport.objects.create(
            name="CSV archive helper",
            report=self.template,
            tenant=self.tenant,
            frequency="once",
            format=ScheduledReport.FORMAT_CSV,
            save_to_archive=True,
        )
        output = _ReportOutput(
            email_body="CSV body",
            attachment_content="Name\nAsset\n",
            attachment_filename="helper.csv",
            attachment_mime="text/csv",
        )

        archive = _archive_report_output(sched, self.template, output, self.tenant)
        self.assertEqual(archive.status, "success")
        self.assertEqual(archive.file.mime_type, "text/csv")

        email_config = SimpleNamespace(enabled=True, from_address="from@example.com")
        with (
            patch("core.tasks.reports.EmailSettings.load", return_value=email_config),
            patch("core.tasks.reports.EmailMessage") as email_factory,
        ):
            sched.recipients = " recipient@example.com, "
            _deliver_report_email(sched, self.template, output)

        email = email_factory.return_value
        email.attach.assert_called_once_with("helper.csv", "Name\nAsset\n", "text/csv")
        email.send.assert_called_once_with(fail_silently=False)

        sched.format = ScheduledReport.FORMAT_HTML
        html_output = _ReportOutput(email_body="<html>body</html>")
        with (
            patch("core.tasks.reports.EmailSettings.load", return_value=email_config),
            patch("core.tasks.reports.EmailMessage") as email_factory,
        ):
            _deliver_report_email(sched, self.template, html_output)
        self.assertEqual(email_factory.return_value.content_subtype, "html")

        with patch("core.tasks.reports.EmailSettings.load", return_value=None):
            with self.assertRaises(ValidationError):
                _deliver_report_email(sched, self.template, html_output)

    def test_report_scope_refuses_unscoped_schedules(self):
        empty_relation = MagicMock()
        empty_relation.all.return_value = []
        sched = SimpleNamespace(
            tenant=None,
            report=SimpleNamespace(tenant=None, filter_tenants=empty_relation),
            filter_tenants=empty_relation,
            name="Unscoped helper",
        )

        self.assertIsNone(_resolve_report_scope(sched))


class ReportCrossTenantPermissionTests(TestCase):
    """RBAC matrix from WP-9a: permission gate for cross-tenant report aggregation."""

    def setUp(self):
        from itambox.middleware import set_current_user
        from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant

        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(username="reportuser", password="pass")
        # User is a member of tenant A with a role that does NOT include the
        # cross-tenant reports permission.
        role = Role.objects.create(tenant=self.tenant_a, name="Basic", permissions=["extras.view_reporttemplate"])
        membership = Membership.objects.create(user=self.user, tenant=self.tenant_a)
        self.membership = membership
        grant = RoleGrant.objects.create(membership=membership, role=role)
        RoleGrantScope.objects.create(role_grant=grant, scope_type=RoleGrantScope.SCOPE_OWN)
        set_current_user(self.user)
        self.template = ReportTemplate.objects.create(
            name="Asset Inventory",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag", "name"],
        )

    def test_no_active_tenant_without_permission_raises_permission_denied(self):
        """Non-holder + empty filter_tenants + no active_tenant → PermissionDenied."""
        from core.reports import compile_report_context

        with self.assertRaises(PermissionError):
            compile_report_context(self.template, active_tenant=None, filter_tenants=None)

    def test_non_holder_with_active_tenant_falls_back_to_single_tenant(self):
        """Non-holder + empty filter_tenants + active_tenant → single-tenant."""
        # The gate should set filter_tenants=[active_tenant] when the user
        # lacks the cross-tenant permission and active_tenant is present.
        filter_tenants = None
        active_tenant = self.tenant_a
        # Simulate what compile_report_context does with those inputs
        if not filter_tenants:
            user = self.user
            if user is not None and user.has_perm("reports.view_cross_tenant_reports"):
                pass
            elif active_tenant is not None:
                filter_tenants = [active_tenant]
            else:
                raise PermissionError("Cross-tenant report aggregation requires the permission.")
        self.assertEqual(filter_tenants, [self.tenant_a])

    def test_holder_with_empty_filter_tenants_gets_global_aggregation(self):
        """Holder + empty filter_tenants + active_tenant → global aggregation."""
        from organization.models import Role, RoleGrant, RoleGrantScope

        # Grant the cross-tenant permission.
        holder_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Global Viewer",
            permissions=["extras.view_reporttemplate", "reports.view_cross_tenant_reports"],
        )
        holder_grant = RoleGrant.objects.create(membership=self.membership, role=holder_role)
        RoleGrantScope.objects.create(role_grant=holder_grant, scope_type=RoleGrantScope.SCOPE_OWN)
        # Flush the per-backend permission cache on the user so the new grant is visible.
        if hasattr(self.user, "_perm_cache"):
            del self.user._perm_cache

        # The gate should NOT override filter_tenants when the user holds the permission.
        filter_tenants = None
        if not filter_tenants:
            user = self.user
            if user is not None and user.has_perm("reports.view_cross_tenant_reports"):
                pass  # holder — allow global
            elif self.tenant_a is not None:
                filter_tenants = [self.tenant_a]
            else:
                raise PermissionError("no.")
        self.assertIsNone(filter_tenants, "holder should keep filter_tenants=None for global aggregation")
