import pickle
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import InterfaceError, OperationalError
from django.test import SimpleTestCase

from core.tasks.utils import (
    RetryableTaskError,
    TaskResult,
    TaskStatus,
    TerminalTaskError,
    classify_task_error,
)


class TaskResultContractTests(SimpleTestCase):
    def test_result_is_immutable_and_preserves_boolean_compatibility(self):
        success = TaskResult(status=TaskStatus.SUCCESS, code="task.completed")
        partial = TaskResult(status=TaskStatus.PARTIAL, code="task.partial")
        failure = TaskResult(status=TaskStatus.TERMINAL, code="task.failed")

        self.assertTrue(success)
        self.assertTrue(partial)
        self.assertFalse(failure)
        with self.assertRaises(AttributeError):
            success.code = "changed"

    def test_counts_are_copied_into_an_immutable_mapping(self):
        source = {"processed": 2}
        result = TaskResult(status=TaskStatus.SUCCESS, code="task.completed", counts=source)
        source["processed"] = 99

        self.assertEqual(result.counts["processed"], 2)
        with self.assertRaises(TypeError):
            result.counts["processed"] = 3

    def test_result_is_serializable_by_a_task_backend(self):
        result = TaskResult(status=TaskStatus.PARTIAL, code="task.partial", counts={"processed": 2})

        restored = pickle.loads(pickle.dumps(result))

        self.assertEqual(restored, result)

    def test_classifier_has_a_narrow_transient_allowlist(self):
        for error in (
            OperationalError("secret"),
            InterfaceError("secret"),
            TimeoutError("secret"),
            ConnectionError("secret"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify_task_error(error), TaskStatus.RETRYABLE)

        self.assertEqual(classify_task_error(ValueError("secret")), TaskStatus.TERMINAL)

    @patch("core.tasks.utils.reverse", side_effect=RuntimeError("unexpected"))
    def test_reverse_job_detail_does_not_hide_unexpected_errors(self, _reverse):
        from core.tasks.utils import reverse_job_detail

        with self.assertRaises(RuntimeError):
            reverse_job_detail(7)


class DepreciationTaskContractTests(SimpleTestCase):
    @patch("core.tasks.depreciation.Asset.objects.bulk_update")
    @patch("core.tasks.depreciation.Asset.objects.select_related")
    @patch("core.tasks.depreciation.compute_book_value")
    def test_updates_only_changed_computable_values(self, compute, select_related, bulk_update):
        unchanged = SimpleNamespace(current_book_value=100, depreciation_updated_at=None)
        changed = SimpleNamespace(current_book_value=50, depreciation_updated_at=None)
        unavailable = SimpleNamespace(current_book_value=None, depreciation_updated_at=None)
        queryset = MagicMock()
        queryset.filter.return_value = [unchanged, changed, unavailable]
        select_related.return_value = queryset
        compute.side_effect = [100, 25, None]

        from core.tasks.depreciation import calculate_depreciation

        updated = calculate_depreciation()

        self.assertEqual(updated, 1)
        self.assertEqual(changed.current_book_value, 25)
        self.assertIsNotNone(changed.depreciation_updated_at)
        self.assertIsNone(unavailable.depreciation_updated_at)
        bulk_update.assert_called_once_with(
            [changed], ["current_book_value", "depreciation_updated_at"], batch_size=1000
        )

    @patch("core.tasks.depreciation.Asset.objects.bulk_update")
    @patch("core.tasks.depreciation.Asset.objects.select_related")
    @patch("core.tasks.depreciation.compute_book_value", return_value=100)
    def test_does_not_write_when_values_are_unchanged(self, _compute, select_related, bulk_update):
        queryset = MagicMock()
        queryset.filter.return_value = [SimpleNamespace(current_book_value=100)]
        select_related.return_value = queryset

        from core.tasks.depreciation import calculate_depreciation

        self.assertEqual(calculate_depreciation(), 0)
        bulk_update.assert_not_called()

    @patch("core.tasks.depreciation.Asset.objects.select_related", side_effect=OperationalError("database-secret"))
    def test_database_failure_becomes_redacted_retryable_error(self, _select_related):
        from core.tasks.depreciation import calculate_depreciation

        with self.assertLogs("core.tasks.depreciation", level="ERROR") as captured:
            with self.assertRaises(RetryableTaskError) as raised:
                calculate_depreciation()

        self.assertEqual(raised.exception.code, "depreciation.failed")
        self.assertNotIn("database-secret", str(raised.exception))
        self.assertNotIn("database-secret", " ".join(captured.output))

    @patch("core.tasks.depreciation.compute_book_value", side_effect=ValueError("asset-secret"))
    @patch("core.tasks.depreciation.Asset.objects.select_related")
    def test_data_failure_becomes_redacted_terminal_error(self, select_related, _compute):
        queryset = MagicMock()
        queryset.filter.return_value = [SimpleNamespace(current_book_value=100)]
        select_related.return_value = queryset

        from core.tasks.depreciation import calculate_depreciation

        with self.assertRaises(TerminalTaskError) as raised:
            calculate_depreciation()

        self.assertEqual(raised.exception.code, "depreciation.failed")
        self.assertNotIn("asset-secret", str(raised.exception))
