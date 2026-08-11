import datetime
import logging
from io import StringIO
from types import SimpleNamespace

from django.core.exceptions import FieldError

from core.errors import IntegrationUnexpectedError
from core.importers.snipeit.common import _clean_field_name, _parse_date, _unique_slug, tenant_for
from core.importers.snipeit.contracts import ImportCounts, StageIssue, StageReporter, StageResult


class TestHelpers:
    def test_clean_field_name_strips_prefix_and_id(self):
        assert _clean_field_name("_snipeit_cpu_model_3") == "cpu_model"

    def test_clean_field_name_no_prefix(self):
        assert _clean_field_name("hostname") == "hostname"

    def test_clean_field_name_trailing_large_id(self):
        assert _clean_field_name("_snipeit_department_123") == "department"

    def test_unique_slug_uses_base_manager_and_field_error_fallback(self):
        class Manager:
            def __init__(self):
                self.slug = None

            def filter(self, **kwargs):
                if "deleted_at__isnull" in kwargs:
                    raise FieldError
                self.slug = kwargs["slug"]
                return self

            def exists(self):
                return self.slug == "imported-name"

        class Model:
            _base_manager = Manager()

        assert _unique_slug(Model, "Imported Name") == "imported-name-1"

    def test_parse_date_accepts_supported_formats_and_rejects_invalid_values(self):
        expected = datetime.date(2024, 1, 2)
        assert _parse_date("2024-01-02") == expected
        assert _parse_date("01/02/2024") == expected
        assert _parse_date("02/01/2024") == datetime.date(2024, 2, 1)
        assert _parse_date("not-a-date") is None
        assert _parse_date(None) is None


def test_import_counts_record_and_as_dict_have_exact_keys():
    counts = ImportCounts()
    counts.record("created")
    counts.record("updated")
    counts.record("skipped")
    counts.failed = 4

    assert counts.as_dict() == {"created": 1, "updated": 1, "skipped": 1, "failed": 4}


def test_stage_result_warning_count_aggregates_only_warning_issues():
    result = StageResult("assets")
    result.issues[StageIssue("warning", "assets.checkout", "RuntimeError", "terminal")] = 2
    result.issues[StageIssue("failure", "assets.persist", "ValueError", "terminal")] = 3

    assert result.warning_count == 2


class JobLog:
    def __init__(self):
        self.messages = []

    def append_log(self, message):
        self.messages.append(message)


def _reporter(stdout, job):
    return StageReporter(
        stdout,
        job,
        default_tenant=SimpleNamespace(pk=7),
        user=SimpleNamespace(pk=11),
    )


def test_stage_reporter_row_failure_is_safe_and_increments_failed(caplog):
    stdout = StringIO()
    job = JobLog()
    result = StageResult("assets")
    exception_text = "asset 424242 https://snipe.example/item customer@example.test {payload: secret}"

    with caplog.at_level(logging.WARNING, logger="core.importers.snipeit"):
        error = _reporter(stdout, job).row_failure(result, "assets.persist", RuntimeError(exception_text))

    assert isinstance(error, IntegrationUnexpectedError)
    assert error.cause_type == "RuntimeError"
    assert result.counts.failed == 1
    assert result.issues[StageIssue("failure", "assets.persist", "RuntimeError", "terminal")] == 1
    assert stdout.getvalue() == "  ! assets.persist: one item could not be imported"
    assert job.messages == ["  ! assets.persist: one item could not be imported"]
    record = next(record for record in caplog.records if record.name == "core.importers.snipeit")
    assert record.integration["tenant_id"] == 7
    assert record.integration["actor_id"] == 11
    combined = caplog.text + repr(record.__dict__) + stdout.getvalue() + repr(job.messages)
    assert "424242" not in combined
    assert "snipe.example" not in combined
    assert "customer@example.test" not in combined
    assert "payload" not in combined
    assert "secret" not in combined
    assert exception_text not in combined


def test_stage_reporter_warning_does_not_increment_failed():
    stdout = StringIO()
    job = JobLog()
    result = StageResult("assets")

    _reporter(stdout, job).warning(result, "assets.checkout", RuntimeError("private detail"))

    assert result.counts.failed == 0
    assert result.issues[StageIssue("warning", "assets.checkout", "RuntimeError", "terminal")] == 1
    assert stdout.getvalue() == "  ! assets.checkout: one item could not be imported"
    assert job.messages == ["  ! assets.checkout: one item could not be imported"]


def test_stage_reporter_start_and_finish_output_shape_including_warnings():
    stdout = StringIO()
    job = JobLog()
    result = StageResult("assets", counts=ImportCounts(created=1, updated=2, skipped=3, failed=4))
    result.issues[StageIssue("warning", "assets.checkout", "RuntimeError", "terminal")] = 2
    reporter = _reporter(stdout, job)

    reporter.start(result)
    reporter.finish(result)

    assert stdout.getvalue() == "\n[assets]  assets: 1 created, 2 updated, 3 skipped, 4 failed, 2 warnings"
    assert job.messages == [
        "\n[assets]",
        "  assets: 1 created, 2 updated, 3 skipped, 4 failed, 2 warnings",
    ]


def test_tenant_for_uses_default_when_company_mapping_is_disabled():
    default = object()
    mapped = object()

    assert (
        tenant_for({"company": {"id": 4}}, default_tenant=default, map_companies=False, tenants={4: mapped}) is default
    )


def test_tenant_for_uses_mapped_company_tenant():
    default = object()
    mapped = object()

    assert tenant_for({"company": {"id": 4}}, default_tenant=default, map_companies=True, tenants={4: mapped}) is mapped


def test_tenant_for_falls_back_when_company_is_missing():
    default = object()

    assert tenant_for({}, default_tenant=default, map_companies=True, tenants={}) is default
