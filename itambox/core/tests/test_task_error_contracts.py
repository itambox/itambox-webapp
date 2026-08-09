from unittest.mock import patch

from django.db import InterfaceError, OperationalError
from django.test import SimpleTestCase

from core.tasks.utils import (
    TaskResult,
    TaskStatus,
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

    def test_classifier_has_a_narrow_transient_allowlist(self):
        for error in (OperationalError("secret"), InterfaceError("secret"), TimeoutError("secret"), ConnectionError("secret")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify_task_error(error), TaskStatus.RETRYABLE)

        self.assertEqual(classify_task_error(ValueError("secret")), TaskStatus.TERMINAL)

    @patch("core.tasks.utils.reverse", side_effect=RuntimeError("unexpected"))
    def test_reverse_job_detail_does_not_hide_unexpected_errors(self, _reverse):
        from core.tasks.utils import reverse_job_detail

        with self.assertRaises(RuntimeError):
            reverse_job_detail(7)
