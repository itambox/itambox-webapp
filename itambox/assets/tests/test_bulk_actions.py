import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel
from core.models import Job, Notification
from core.tasks.labels import generate_label_batch_task, generate_label_pdf_batch_task
from core.tasks.utils import TaskStatus
from core.tests.mixins import grant
from extras.models import FileAttachment, LabelTemplate
from organization.models import Membership, Role, Tenant, TenantGroup

User = get_user_model()


class BulkActionsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin", password="testpassword", is_staff=True, is_superuser=True
        )
        self.client.login(username="testadmin", password="testpassword")

        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant")
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="ThinkPad T14", slug="lenovo-thinkpad-t14"
        )
        self.status, _ = StatusLabel.objects.get_or_create(
            slug="available", defaults={"name": "Available", "type": "deployable"}
        )

        self.asset1 = Asset.objects.create(
            name="Asset 1",
            asset_tag="AST-001",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status,
            tenant=self.tenant,
        )
        self.asset2 = Asset.objects.create(
            name="Asset 2",
            asset_tag="AST-002",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status,
            tenant=self.tenant,
        )

        self.label_template = LabelTemplate.objects.create(
            name="Standard QR",
            description="Standard QR label",
            barcode_format="qr",
            template_code="<div>{{ asset.name }}</div>",
        )

    @patch("django_q.tasks.async_task")
    def test_bulk_print_labels(self, mock_async):
        url = reverse("assets:asset_bulk_print_labels")
        post_data = {
            "pk": [self.asset1.pk, self.asset2.pk],
            "template_id": self.label_template.pk,
            "layout_mode": "roll",
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify Job was created
        job = Job.objects.filter(name__contains="Label Batch Generation").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, Job.STATUS_PENDING)

        # Verify async_task was called
        mock_async.assert_called_once()
        args = mock_async.call_args[0]
        self.assertEqual(args[0], "core.tasks.labels.generate_label_pdf_batch_task")
        self.assertEqual(args[1], job.pk)
        self.assertEqual(args[2], [str(self.asset1.pk), str(self.asset2.pk)])
        self.assertEqual(args[3], self.label_template.pk)
        self.assertEqual(args[4], "roll")

    def _job(self):
        return Job.objects.create(name="labels", tenant=self.tenant, status=Job.STATUS_PENDING)

    def test_label_tasks_classify_missing_and_non_pending_jobs(self):
        missing_zip = generate_label_batch_task(999999, [], "qr", self.user.pk, self.tenant.pk)
        missing_pdf = generate_label_pdf_batch_task(
            999999, [], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
        )
        zip_job = self._job()
        zip_job.status = Job.STATUS_FAILED
        zip_job.save(update_fields=["status"])
        pdf_job = self._job()
        pdf_job.status = Job.STATUS_FAILED
        pdf_job.save(update_fields=["status"])

        skipped_zip = generate_label_batch_task(zip_job.pk, [], "qr", self.user.pk, self.tenant.pk)
        skipped_pdf = generate_label_pdf_batch_task(
            pdf_job.pk, [], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
        )

        self.assertEqual((missing_zip.status, missing_zip.code), (TaskStatus.TERMINAL, "labels.job_not_found"))
        self.assertEqual((missing_pdf.status, missing_pdf.code), (TaskStatus.TERMINAL, "labels.job_not_found"))
        self.assertEqual((skipped_zip.status, skipped_zip.code), (TaskStatus.SKIPPED, "labels.job_not_pending"))
        self.assertEqual((skipped_pdf.status, skipped_pdf.code), (TaskStatus.SKIPPED, "labels.job_not_pending"))

    def test_pdf_task_classifies_missing_template_and_assets(self):
        missing_template_job = self._job()
        missing_template = generate_label_pdf_batch_task(
            missing_template_job.pk, [], 999999, "roll", self.user.pk, self.tenant.pk
        )
        no_assets_job = self._job()
        no_assets = generate_label_pdf_batch_task(
            no_assets_job.pk, [], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
        )

        missing_template_job.refresh_from_db()
        no_assets_job.refresh_from_db()
        self.assertEqual(
            (missing_template.status, missing_template.code), (TaskStatus.TERMINAL, "labels.template_not_found")
        )
        self.assertEqual(missing_template_job.status, Job.STATUS_FAILED)
        self.assertEqual((no_assets.status, no_assets.code), (TaskStatus.SKIPPED, "labels.no_assets"))
        self.assertEqual(no_assets_job.result, {"status": "no_assets"})

    @patch("core.tasks.labels.generate_base64_barcode", side_effect=RuntimeError("secret-asset-label"))
    def test_pdf_task_all_render_failures_are_terminal_and_redacted(self, _barcode):
        job = self._job()

        with self.assertLogs("core.tasks.labels", level="WARNING") as captured:
            result = generate_label_pdf_batch_task(
                job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
            )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.TERMINAL, "labels.no_labels_rendered"))
        self.assertNotIn("secret-asset-label", " ".join(captured.output) + " " + job.logs)
        self.assertNotIn(self.asset1.asset_tag, job.logs)
        self.assertNotIn(self.asset1.name, job.logs)

    @patch("core.tasks.labels._html_to_pdf_bytes", return_value=b"%PDF-test")
    @patch("core.tasks.labels.render_label_html", return_value="<div>safe</div>")
    @patch(
        "core.tasks.labels.generate_base64_barcode", side_effect=["data:image/png;base64,AAAA", ValueError("secret")]
    )
    def test_pdf_task_returns_partial_without_exposing_asset_labels(self, _barcode, _render, _pdf):
        job = self._job()

        result = generate_label_pdf_batch_task(
            job.pk,
            [self.asset1.pk, self.asset2.pk],
            self.label_template.pk,
            "roll",
            self.user.pk,
            self.tenant.pk,
        )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.PARTIAL, "labels.pdf_partial"))
        self.assertEqual(dict(result.counts), {"rendered": 1, "failed": 1})
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertNotIn(self.asset1.asset_tag, job.logs)
        self.assertNotIn(self.asset1.name, job.logs)

    @patch("core.tasks.labels._html_to_pdf_bytes", side_effect=OperationalError("secret-pdf-payload"))
    @patch("core.tasks.labels.render_label_html", return_value="<div>safe</div>")
    @patch("core.tasks.labels.generate_base64_barcode", return_value="data:image/png;base64,AAAA")
    def test_pdf_boundary_database_failure_is_retryable_and_redacted(self, _barcode, _render, _pdf):
        job = self._job()

        result = generate_label_pdf_batch_task(
            job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
        )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.RETRYABLE, "labels.pdf_failed"))
        self.assertNotIn("secret-pdf-payload", job.logs)

    def test_pdf_task_completes_with_real_renderer_and_valid_attachment(self):
        job = self._job()

        result = generate_label_pdf_batch_task(
            job.pk, [self.asset1.pk, self.asset2.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
        )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.SUCCESS, "labels.pdf_completed"))
        self.assertEqual(dict(result.counts), {"rendered": 2, "failed": 0})
        self.assertEqual(job.status, Job.STATUS_COMPLETED)

        ct = ContentType.objects.get_for_model(Job)
        attachments = FileAttachment.objects.filter(model=ct, object_id=job.pk)
        self.assertEqual(attachments.count(), 1)
        attachment = attachments.first()
        self.assertEqual(attachment.mime_type, "application/pdf")
        data = attachment.file.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        reader = PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages), 2)

        self.assertIn("download_url", job.result)
        self.assertTrue(job.result["download_url"].startswith("/"))
        self.assertTrue(
            Notification.objects.filter(
                user=self.user, level=Notification.LEVEL_SUCCESS, target_url__startswith="/"
            ).exists()
        )

    @patch("core.tasks.labels.Notification.objects.create", side_effect=OperationalError("secret-notify"))
    def test_pdf_task_success_notification_failure_does_not_fail_completed_job(self, _notify):
        job = self._job()

        with self.assertLogs("core.tasks.labels", level="ERROR") as captured:
            result = generate_label_pdf_batch_task(
                job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
            )

        job.refresh_from_db()
        ct = ContentType.objects.get_for_model(Job)
        attachment = FileAttachment.objects.filter(model=ct, object_id=job.pk).first()
        self.assertEqual((result.status, result.code), (TaskStatus.SUCCESS, "labels.pdf_completed"))
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertIsNotNone(attachment)
        data = attachment.file.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        PdfReader(io.BytesIO(data))
        self.assertIn("(phase=notification)", job.logs)
        self.assertNotIn("secret-notify", " ".join(captured.output) + " " + job.logs)

    @patch("core.tasks.labels._html_to_pdf_bytes", side_effect=RuntimeError("secret-render-payload"))
    def test_pdf_task_render_failure_is_terminal_phased_and_redacted(self, _render):
        job = self._job()

        with self.assertLogs("core.tasks.labels", level="ERROR") as captured:
            result = generate_label_pdf_batch_task(
                job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
            )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.TERMINAL, "labels.pdf_failed"))
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn("phase=pdf_render", job.logs)
        self.assertNotIn("secret-render-payload", " ".join(captured.output) + " " + job.logs)
        ct = ContentType.objects.get_for_model(Job)
        self.assertEqual(FileAttachment.objects.filter(model=ct, object_id=job.pk).count(), 0)
        notification = Notification.objects.filter(user=self.user, level=Notification.LEVEL_DANGER).first()
        self.assertIsNotNone(notification)
        self.assertNotIn("secret-render-payload", notification.message)

    @patch("core.tasks.labels.Job.objects.get", side_effect=OperationalError("secret-database"))
    def test_pdf_entry_database_failure_is_retryable_and_not_masked(self, _get):
        # The PDF task's single task boundary also covers Job resolution: a
        # transient failure there must be classified, with the boundary state
        # (phase/attachment) bound — never replaced by an UnboundLocalError.
        with self.assertLogs("core.tasks.labels", level="ERROR") as captured:
            result = generate_label_pdf_batch_task(17, [], self.label_template.pk, "roll", self.user.pk, self.tenant.pk)

        self.assertEqual((result.status, result.code), (TaskStatus.RETRYABLE, "labels.pdf_failed"))
        self.assertNotIn("secret-database", " ".join(captured.output))
        self.assertTrue(any(getattr(record, "phase", None) == "job_resolve" for record in captured.records))

    @patch("core.tasks.labels.Notification.objects.create", side_effect=OperationalError("secret-notify"))
    @patch("core.tasks.labels._html_to_pdf_bytes", side_effect=RuntimeError("secret-render"))
    def test_pdf_task_failure_notification_failure_does_not_mask_task_outcome(self, _render, _notify):
        # The failure notification is best-effort: even if BOTH the render and
        # the notification delivery fail, the original task outcome and job
        # state must survive, redacted.
        job = self._job()

        with self.assertLogs("core.tasks.labels", level="ERROR") as captured:
            result = generate_label_pdf_batch_task(
                job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
            )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.TERMINAL, "labels.pdf_failed"))
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn("phase=pdf_render", job.logs)
        self.assertIn("(phase=notification)", job.logs)
        self.assertNotIn("secret-render", " ".join(captured.output) + " " + job.logs)
        self.assertNotIn("secret-notify", " ".join(captured.output) + " " + job.logs)
        ct = ContentType.objects.get_for_model(Job)
        self.assertEqual(FileAttachment.objects.filter(model=ct, object_id=job.pk).count(), 0)

    def test_pdf_task_persist_failure_cleans_up_partial_attachment(self):
        job = self._job()

        real_save = FileAttachment.save
        calls = {"n": 0}

        def flaky_save(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # allow the create() insert; the explicit save() afterwards fails
                return real_save(self, *args, **kwargs)
            raise OperationalError("secret-storage")

        with (
            self.assertLogs("core.tasks.labels", level="ERROR") as captured,
            patch.object(FileAttachment, "save", new=flaky_save),
        ):
            result = generate_label_pdf_batch_task(
                job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
            )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.RETRYABLE, "labels.pdf_failed"))
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn("phase=attachment_persist", job.logs)
        self.assertNotIn("secret-storage", " ".join(captured.output) + " " + job.logs)
        ct = ContentType.objects.get_for_model(Job)
        self.assertEqual(FileAttachment.objects.filter(model=ct, object_id=job.pk).count(), 0)
        self.assertEqual(
            [name for name in default_storage.listdir("attachments/files")[1] if name == f"labels_batch_{job.pk}.pdf"],
            [],
        )

    @patch("extras.models.FileAttachment.delete", side_effect=OperationalError("secret-cleanup"))
    def test_pdf_task_persist_failure_reports_cleanup_failure_without_leaking(self, _delete):
        job = self._job()

        real_save = FileAttachment.save
        calls = {"n": 0}

        def flaky_save(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_save(self, *args, **kwargs)
            raise OperationalError("secret-storage")

        with (
            self.assertLogs("core.tasks.labels", level="ERROR") as captured,
            patch.object(FileAttachment, "save", new=flaky_save),
        ):
            result = generate_label_pdf_batch_task(
                job.pk, [self.asset1.pk], self.label_template.pk, "roll", self.user.pk, self.tenant.pk
            )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.RETRYABLE, "labels.pdf_failed"))
        self.assertEqual(job.status, Job.STATUS_FAILED)
        # cleanup failed, so the orphan row remains — but nothing sensitive leaked
        ct = ContentType.objects.get_for_model(Job)
        self.assertEqual(FileAttachment.objects.filter(model=ct, object_id=job.pk).count(), 1)
        self.assertNotIn("secret-cleanup", " ".join(captured.output) + " " + job.logs)
        self.assertTrue(any(record.phase == "attachment_cleanup" for record in captured.records))

    @patch("core.tasks.labels.generate_single_label_graphic", side_effect=RuntimeError("secret-zip-label"))
    def test_zip_item_failure_is_isolated_and_redacted(self, _graphic):
        job = self._job()

        with self.assertLogs("core.tasks.labels", level="WARNING") as captured:
            result = generate_label_batch_task(job.pk, [self.asset1.pk], "qr", self.user.pk, self.tenant.pk)

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.TERMINAL, "labels.no_labels_rendered"))
        self.assertNotIn("secret-zip-label", " ".join(captured.output) + " " + job.logs)
        self.assertNotIn(self.asset1.asset_tag, job.logs)
        self.assertNotIn(self.asset1.name, job.logs)

    @patch("core.tasks.labels.generate_single_label_graphic", return_value=b"safe-image")
    def test_zip_success_returns_counts_without_exposing_asset_labels(self, _graphic):
        job = self._job()

        result = generate_label_batch_task(job.pk, [self.asset1.pk], "qr", self.user.pk, self.tenant.pk)

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.SUCCESS, "labels.zip_completed"))
        self.assertEqual(dict(result.counts), {"rendered": 1, "failed": 0})
        self.assertNotIn(self.asset1.asset_tag, job.logs)
        self.assertNotIn(self.asset1.name, job.logs)

    @patch("core.tasks.labels.Notification.objects.create", side_effect=OperationalError("secret-zip-notify"))
    @patch("core.tasks.labels.generate_single_label_graphic", return_value=b"safe-image")
    def test_zip_success_notification_failure_does_not_fail_completed_job(self, _graphic, _notify):
        job = self._job()

        with self.assertLogs("core.tasks.labels", level="ERROR") as captured:
            result = generate_label_batch_task(job.pk, [self.asset1.pk], "qr", self.user.pk, self.tenant.pk)

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.SUCCESS, "labels.zip_completed"))
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertIn("(phase=notification)", job.logs)
        self.assertNotIn("secret-zip-notify", " ".join(captured.output) + " " + job.logs)

    @patch(
        "core.tasks.labels.generate_single_label_graphic",
        side_effect=[b"safe-image", RuntimeError("secret-second-label")],
    )
    def test_zip_item_failure_after_success_returns_partial(self, _graphic):
        job = self._job()

        result = generate_label_batch_task(job.pk, [self.asset1.pk, self.asset2.pk], "qr", self.user.pk, self.tenant.pk)

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.PARTIAL, "labels.zip_partial"))
        self.assertEqual(dict(result.counts), {"rendered": 1, "failed": 1})
        self.assertNotIn("secret-second-label", job.logs)

    @patch("assets.models.Asset.objects.filter", side_effect=OperationalError("secret-assets-query"))
    def test_zip_boundary_database_failure_is_retryable_and_redacted(self, _filter):
        job = self._job()

        result = generate_label_batch_task(job.pk, [self.asset1.pk], "qr", self.user.pk, self.tenant.pk)

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.RETRYABLE, "labels.zip_failed"))
        self.assertNotIn("secret-assets-query", job.logs)

    @patch("core.tasks.labels.Job.objects.get", side_effect=OperationalError("secret-database"))
    def test_zip_entry_database_failure_is_retryable(self, _get):
        result = generate_label_batch_task(17, [], "qr", self.user.pk, self.tenant.pk)

        self.assertEqual((result.status, result.code), (TaskStatus.RETRYABLE, "labels.entry_failed"))

    def test_bulk_delete_assets_get(self):
        url = reverse("assets:asset_bulk_delete")
        post_data = {
            "pk": [self.asset1.pk, self.asset2.pk],
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "generic/object_confirm_bulk_delete.html")

    def test_bulk_delete_assets_confirm(self):
        url = reverse("assets:asset_bulk_delete")
        post_data = {
            "pk": [self.asset1.pk, self.asset2.pk],
            "_confirm": "Confirm",
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        # Check that assets were deleted
        self.assertFalse(Asset.objects.filter(pk=self.asset1.pk).exists())
        self.assertFalse(Asset.objects.filter(pk=self.asset2.pk).exists())

    def test_bulk_edit_assets_get(self):
        url = reverse("assets:asset_bulk_edit")
        post_data = {
            "pk": [self.asset1.pk, self.asset2.pk],
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "generic/object_bulk_edit.html")

    def test_bulk_edit_assets_apply(self):
        status2 = StatusLabel.objects.create(name="Archived", slug="archived", type="archived")
        url = reverse("assets:asset_bulk_edit")
        post_data = {
            "pk": [self.asset1.pk, self.asset2.pk],
            "_selected_fields": ["status"],
            "status": status2.pk,
            "_apply": "Apply",
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        # Check that assets were updated
        self.asset1.refresh_from_db()
        self.asset2.refresh_from_db()
        self.assertEqual(self.asset1.status, status2)
        self.assertEqual(self.asset2.status, status2)

    def test_bulk_edit_assets_apply_tags(self):
        from extras.models import Tag

        tag1 = Tag.objects.create(name="Tag 1", slug="tag-1")
        tag2 = Tag.objects.create(name="Tag 2", slug="tag-2")

        # Add tag1 initially to asset1
        self.asset1.tags.add(tag1)

        url = reverse("assets:asset_bulk_edit")
        post_data = {
            "pk": [self.asset1.pk, self.asset2.pk],
            "_selected_fields": ["add_tags", "remove_tags"],
            "add_tags": [tag2.pk],
            "remove_tags": [tag1.pk],
            "_apply": "Apply",
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify tag changes
        self.asset1.refresh_from_db()
        self.asset2.refresh_from_db()

        # asset1 should have tag2 but not tag1
        self.assertIn(tag2, self.asset1.tags.all())
        self.assertNotIn(tag1, self.asset1.tags.all())

        # asset2 should have tag2 but not tag1
        self.assertIn(tag2, self.asset2.tags.all())
        self.assertNotIn(tag1, self.asset2.tags.all())


class LabelBulkScopeTests(TestCase):
    def setUp(self):
        self.group = TenantGroup.objects.create(name="Label Group", slug="label-group")
        self.tenant_a = Tenant.objects.create(name="Label Tenant A", slug="label-tenant-a", group=self.group)
        self.tenant_b = Tenant.objects.create(name="Label Tenant B", slug="label-tenant-b", group=self.group)
        self.outside_tenant = Tenant.objects.create(name="Outside Tenant", slug="outside-label-tenant")
        self.unreachable_tenant = Tenant.objects.create(name="Unreachable Tenant", slug="unreachable-label-tenant")
        self.user = User.objects.create_user(username="label-scope-user", password="pw")
        for tenant in (self.tenant_a, self.tenant_b, self.outside_tenant):
            role = Role.objects.create(
                tenant=tenant,
                name=f"Label viewer {tenant.pk}",
                permissions=["assets.view_asset", "core.view_job"],
            )
            grant(self.user, tenant, role)

        status = StatusLabel.objects.create(name="Available label", slug="available-label", type="deployable")
        self.asset_a = Asset.objects.create(name="Asset A", asset_tag="LABEL-A", status=status, tenant=self.tenant_a)
        self.asset_b = Asset.objects.create(name="Asset B", asset_tag="LABEL-B", status=status, tenant=self.tenant_b)
        self.unreachable_asset = Asset.objects.create(
            name="Unreachable asset",
            asset_tag="LABEL-X",
            status=status,
            tenant=self.unreachable_tenant,
        )
        self.outside_job = Job.objects.create(name="Outside group job", tenant=self.outside_tenant)
        self.template = LabelTemplate.objects.create(name="Scope label template")
        self.url = reverse("assets:asset_bulk_print_labels")

    def _login_with_scope(self, *, all_accessible=False, group=None):
        self.client.force_login(self.user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session.pop("active_tenant_group_id", None)
        session.pop("active_all_accessible", None)
        if all_accessible:
            session["active_all_accessible"] = True
        elif group is not None:
            session["active_tenant_group_id"] = group.pk
        session.save()

    def _login_with_tenant(self, tenant):
        self.client.force_login(self.user)
        session = self.client.session
        session.pop("active_tenant_group_id", None)
        session.pop("active_all_accessible", None)
        session["active_tenant_id"] = tenant.pk
        session.save()

    def _post(self, pks):
        return self.client.post(
            self.url,
            {
                "pk": [str(pk) for pk in pks],
                "template_id": self.template.pk,
                "layout_mode": "roll",
            },
        )

    @patch("django_q.tasks.async_task")
    def test_all_accessible_scope_prints_assets_from_multiple_tenants(self, mock_async):
        self._login_with_scope(all_accessible=True)

        response = self._post([self.asset_a.pk, self.asset_b.pk])

        self.assertEqual(response.status_code, 302)
        job = Job.objects.latest("created")
        self.assertEqual(job.tenant_id, self.tenant_a.pk)
        self.assertEqual(job.data["scope_tenant_ids"], [self.tenant_a.pk, self.tenant_b.pk])
        self.assertEqual(mock_async.call_args.args[5], self.user.pk)
        self.assertEqual(mock_async.call_args.args[6], self.tenant_a.pk)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    @patch("django_q.tasks.async_task")
    def test_group_scope_prints_assets_from_multiple_tenants(self, mock_async):
        self._login_with_scope(group=self.group)

        response = self._post([self.asset_a.pk, self.asset_b.pk])

        self.assertEqual(response.status_code, 302)
        job = Job.objects.latest("created")
        self.assertEqual(job.tenant_id, self.tenant_a.pk)
        self.assertEqual(job.data["scope_tenant_ids"], [self.tenant_a.pk, self.tenant_b.pk])
        self.assertEqual(mock_async.call_args.args[6], self.tenant_a.pk)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    @patch("core.tasks.labels._html_to_pdf_bytes", return_value=b"%PDF-scope-test")
    @patch("core.tasks.labels.render_label_html", return_value="<div>safe</div>")
    @patch("core.tasks.labels.generate_base64_barcode", return_value="data:image/png;base64,AAAA")
    def test_pdf_task_uses_persisted_multi_tenant_scope(self, _barcode, _render, _pdf):
        job = Job.objects.create(
            name="multi-tenant labels",
            tenant=self.tenant_a,
            status=Job.STATUS_PENDING,
            data={"scope_tenant_ids": [self.tenant_a.pk, self.tenant_b.pk]},
        )

        result = generate_label_pdf_batch_task(
            job.pk,
            [self.asset_a.pk, self.asset_b.pk],
            self.template.pk,
            "roll",
            self.user.pk,
            self.tenant_a.pk,
        )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.SUCCESS, "labels.pdf_completed"))
        self.assertEqual(job.status, Job.STATUS_COMPLETED)
        self.assertEqual(FileAttachment.objects.filter(object_id=job.pk).count(), 1)

    @patch("core.tasks.labels._html_to_pdf_bytes", return_value=b"%PDF-scope-test")
    @patch("core.tasks.labels.render_label_html", return_value="<div>safe</div>")
    @patch("core.tasks.labels.generate_base64_barcode", return_value="data:image/png;base64,AAAA")
    def test_pdf_task_rejects_assets_outside_persisted_scope(self, _barcode, _render, _pdf):
        job = Job.objects.create(
            name="scoped labels",
            tenant=self.tenant_a,
            status=Job.STATUS_PENDING,
            data={"scope_tenant_ids": [self.tenant_a.pk]},
        )

        result = generate_label_pdf_batch_task(
            job.pk,
            [self.asset_a.pk, self.asset_b.pk],
            self.template.pk,
            "roll",
            self.user.pk,
            self.tenant_a.pk,
        )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.TERMINAL, "labels.assets_not_accessible"))
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertEqual(FileAttachment.objects.filter(object_id=job.pk).count(), 0)

    @patch("core.tasks.labels._html_to_pdf_bytes", return_value=b"%PDF-scope-test")
    @patch("core.tasks.labels.render_label_html", return_value="<div>safe</div>")
    @patch("core.tasks.labels.generate_base64_barcode", return_value="data:image/png;base64,AAAA")
    def test_pdf_task_rejects_asset_after_view_access_is_revoked(self, _barcode, _render, _pdf):
        Role.objects.filter(tenant=self.tenant_b).update(permissions=["core.view_job"])
        job = Job.objects.create(
            name="revoked labels",
            tenant=self.tenant_a,
            status=Job.STATUS_PENDING,
            data={"scope_tenant_ids": [self.tenant_a.pk, self.tenant_b.pk]},
        )

        result = generate_label_pdf_batch_task(
            job.pk,
            [self.asset_a.pk, self.asset_b.pk],
            self.template.pk,
            "roll",
            self.user.pk,
            self.tenant_a.pk,
        )

        job.refresh_from_db()
        self.assertEqual((result.status, result.code), (TaskStatus.TERMINAL, "labels.assets_not_accessible"))
        self.assertEqual(job.status, Job.STATUS_FAILED)

    @patch("django_q.tasks.async_task")
    def test_bulk_print_labels_rejects_invalid_asset_ids_before_job_creation(self, mock_async):
        self._login_with_scope(all_accessible=True)
        before = Job.objects.count()

        for pks in (("not-an-id",), ("1", "1"), ("0",)):
            with self.subTest(pks=pks):
                response = self._post(pks)
                self.assertEqual(response.status_code, 302)

        self.assertEqual(Job.objects.count(), before)
        mock_async.assert_not_called()

    @patch("django_q.tasks.async_task")
    def test_bulk_print_labels_rejects_empty_selection_before_job_creation(self, mock_async):
        self._login_with_scope(all_accessible=True)
        before = Job.objects.count()

        response = self.client.post(
            self.url,
            {"template_id": self.template.pk, "layout_mode": "roll"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), before)
        mock_async.assert_not_called()

    @patch("django_q.tasks.async_task")
    def test_bulk_print_labels_rejects_invalid_template_before_job_creation(self, mock_async):
        self._login_with_scope(all_accessible=True)
        before = Job.objects.count()

        response = self.client.post(
            self.url,
            {"pk": [str(self.asset_a.pk)], "template_id": "not-a-template", "layout_mode": "roll"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), before)
        mock_async.assert_not_called()

    @patch("django_q.tasks.async_task")
    def test_aggregate_scope_rejects_inaccessible_selection_before_job_creation(self, mock_async):
        self._login_with_scope(all_accessible=True)
        before = Job.objects.count()

        response = self._post([self.asset_a.pk, self.unreachable_asset.pk])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), before)
        mock_async.assert_not_called()

    def test_single_tenant_scope_hides_multi_tenant_job_anchored_to_that_tenant(self):
        job = Job.objects.create(
            name="multi-tenant labels",
            tenant=self.tenant_a,
            status=Job.STATUS_COMPLETED,
            data={"scope_tenant_ids": [self.tenant_a.pk, self.tenant_b.pk]},
        )
        self._login_with_tenant(self.tenant_a)

        response = self.client.get(reverse("job_detail", kwargs={"pk": job.pk}))

        self.assertEqual(response.status_code, 404)

    def test_all_accessible_scope_hides_multi_tenant_job_after_access_revoked(self):
        job = Job.objects.create(
            name="multi-tenant labels",
            tenant=self.tenant_a,
            status=Job.STATUS_COMPLETED,
            data={"scope_tenant_ids": [self.tenant_a.pk, self.tenant_b.pk]},
        )
        Membership.objects.filter(user=self.user, tenant=self.tenant_b).delete()
        self._login_with_scope(all_accessible=True)

        response = self.client.get(reverse("job_detail", kwargs={"pk": job.pk}))

        self.assertEqual(response.status_code, 404)

    def test_group_scope_hides_jobs_outside_selected_group(self):
        self._login_with_scope(group=self.group)

        response = self.client.get(reverse("job_detail", kwargs={"pk": self.outside_job.pk}))

        self.assertEqual(response.status_code, 404)
