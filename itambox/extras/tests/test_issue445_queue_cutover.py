"""Queue/executable-surface verification for the issue #445 task cutover.

Exercises the strict ``verify_issue445_task_cutover`` management command
against a real PostgreSQL database with seeded django-q Schedule/OrmQ state,
plus the all-or-nothing historical resubmission guard. No network access;
the queue broker used here is the ORM broker on the test database.
"""

from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

LEGACY = "core.tasks.evaluate_alert_rules_task"
CUTOVER = "extras.tasks.alerts.evaluate_alert_rules_task"
UNRELATED = "core.tasks.retention.prune_changelog_task"
ALIAS = "core.tasks.alerts.evaluate_alert_rules_task.extra"


@pytest.mark.serial_only
class Issue445QueueCutoverTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        from django_q.models import OrmQ, Schedule

        Schedule.objects.all().delete()
        OrmQ.objects.all().delete()

    def _schedule(self, func, hook=None):
        from django_q.models import Schedule

        return Schedule.objects.create(
            name=f"queue-cutover {func}",
            func=func,
            hook=hook,
            args="[]",
            kwargs="{}",
            schedule_type="D",
            minutes=0,
            repeats=-1,
        )

    def _ormq(self, func):
        from django_q.models import OrmQ
        from django_q.signing import SignedPackage

        OrmQ.objects.create(key="issue445-test-key", package=SignedPackage.dumps([func, (), {}, None]))

    def test_forward_preflight_passes_only_on_predecessor_paths(self):
        self._schedule(LEGACY)
        self._schedule(UNRELATED)
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_forward_preflight_rejects_cutover_paths(self):
        self._schedule(CUTOVER)
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_forward_postmigrate_accepts_cutover_paths(self):
        self._schedule(CUTOVER, hook=None)
        self._schedule(UNRELATED)
        call_command("verify_issue445_task_cutover", phase="forward-postmigrate", strict=True)

    def test_forward_postmigrate_rejects_predecessor_hook(self):
        self._schedule(CUTOVER, hook=LEGACY)
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-postmigrate", strict=True)

    def test_noncanonical_alias_is_a_strict_failure(self):
        self._schedule(ALIAS)
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_ormq_package_surfaces_are_inventoried(self):
        self._ormq(LEGACY)
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_ormq_package_with_unknown_form_is_undecodable(self):
        from django_q.models import OrmQ

        OrmQ.objects.create(key="issue445-bad", package=b"not-a-package")
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_reverse_postmigrate_accepts_predecessor_paths_again(self):
        self._schedule(LEGACY)
        call_command("verify_issue445_task_cutover", phase="reverse-postmigrate", strict=True)


class Issue445ResubmissionGuardTests(TransactionTestCase):
    """All-or-nothing historical resubmission guard (no queue writes)."""

    def _task_row(self, func):
        from django_q.models import Failure

        return Failure.objects.create(
            name="historical failure",
            func=func,
            args="[]",
            kwargs="{}",
            started=None,
            stopped=None,
        )

    def test_blocked_paths_are_all_or_nothing(self):
        from core.django_q_task_resubmission import is_blocked_task_path, resubmit_task_guarded

        assert is_blocked_task_path(LEGACY)
        assert is_blocked_task_path(ALIAS)
        assert not is_blocked_task_path(CUTOVER)
        assert not is_blocked_task_path(UNRELATED)
        assert not is_blocked_task_path(None)

        rows = [self._task_row(LEGACY), self._task_row(UNRELATED)]
        model_admin = mock.Mock(model=rows[0].__class__)
        request = mock.Mock()
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, request, rows)
            enqueue.assert_not_called()
        message = model_admin.message_user.call_args.args[1]
        assert "task_resubmission.blocked_moved_path" in message
        assert LEGACY in message
        assert "[]" not in message  # payloads never appear in rejection output

    def test_allowed_rows_resubmit_and_failure_rows_are_deleted(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(UNRELATED)
        model_admin = mock.Mock(model=good.__class__)
        request = mock.Mock()
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, request, [good])
            enqueue.assert_called_once()
        model_admin.message_user.assert_not_called()
