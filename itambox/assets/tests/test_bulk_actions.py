from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel
from core.models import Job
from core.tasks.labels import generate_label_batch_task, generate_label_pdf_batch_task
from core.tasks.utils import TaskStatus
from extras.models import LabelTemplate
from organization.models import Tenant

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
