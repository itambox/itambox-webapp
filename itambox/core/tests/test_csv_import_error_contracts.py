import logging
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError, transaction

from assets.models import Manufacturer
from core.forms.import_forms import BulkImportForm, ImportResult
from core.models import Job, Notification
from core.tasks.csv_import import import_csv_task
from core.tests.mixins import TenantTestMixin

SECRET = "customer@example.test bearer-secret secret_idx_42"


class _ConditionalImportForm(BulkImportForm):
    model = Manufacturer
    required_fields = ["name"]
    optional_fields = ["slug"]

    def _create_instance(self, mapped_data):
        if mapped_data["name"] == "explode":
            instance = MagicMock()
            instance.full_clean.return_value = None
            instance.save.side_effect = IntegrityError(SECRET)
            return instance
        return super()._create_instance(mapped_data)


@pytest.mark.django_db
class TestImportRowContract(TenantTestMixin):
    def test_unexpected_row_rolls_back_to_savepoint_and_later_rows_continue(self, caplog):
        self.setup_tenant_context(name="Import Tenant", slug="import-tenant")
        self.set_active_tenant(self.tenant, self.tenant_membership)
        form = _ConditionalImportForm()
        form._rows_data = [{"name": "explode", "slug": "explode"}, {"name": "safe manufacturer", "slug": "safe"}]

        with caplog.at_level(logging.ERROR), transaction.atomic():
            result = form.import_data()

        assert isinstance(result, ImportResult)
        assert result.imported_count == 1
        assert result.errors == ["Row 2: could not be imported due to an unexpected error."]
        assert Manufacturer.objects.filter(name="safe manufacturer").exists()
        record = next(record for record in caplog.records if hasattr(record, "import_context"))
        assert record.import_context["operation"] == "row.persist"
        assert record.import_context["row_number"] == 2
        assert record.import_context["exception_type"] == "IntegrityError"
        assert record.import_context["tenant_id"] == self.tenant.pk
        assert SECRET not in caplog.text


@pytest.mark.django_db
class TestImportTaskAbortContract(TenantTestMixin):
    @patch("itambox.views.generic.ObjectImportView.get_form_class")
    def test_abort_persists_and_logs_only_safe_contract_fields(self, get_form_class, caplog):
        self.setup_tenant_context(name="Task Tenant", slug="task-tenant")
        user = self.tenant_user
        job = Job.objects.create(name="Import Job", tenant=self.tenant, status=Job.STATUS_PENDING)
        form = MagicMock()
        form.import_data.side_effect = RuntimeError(SECRET)
        get_form_class.return_value = lambda: form

        with caplog.at_level(logging.ERROR):
            import_csv_task(
                job_id=job.pk,
                rows_data=[{"email": "customer@example.test"}],
                app_label="assets",
                model_name="manufacturer",
                user_id=user.pk,
                tenant_id=self.tenant.pk,
            )

        job.refresh_from_db()
        assert job.status == Job.STATUS_FAILED
        assert job.logs.endswith("The import could not be completed due to an unexpected error.")
        notification = Notification.objects.get(user=user, level=Notification.LEVEL_DANGER)
        assert "A system error occurred during the import" in notification.message
        record = next(record for record in caplog.records if hasattr(record, "import_context"))
        assert record.import_context["operation"] == "task.run"
        assert record.import_context["tenant_id"] == self.tenant.pk
        assert record.import_context["actor_id"] == user.pk
        assert record.import_context["exception_type"] == "RuntimeError"
        assert SECRET not in caplog.text
        assert SECRET not in job.logs
