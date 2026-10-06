"""All-or-nothing historical resubmission guard over the django-q ORM broker."""

import uuid
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase

RESOLVABLE = "extras.tasks.alerts.evaluate_alert_rules_task"
UNRELATED = "core.tasks.retention.prune_changelog_task"
MISSING = "core.tasks.evaluate_alert_rules_task"
MISSING_MODULE = "core.tasks.no_such_module.some_task"
CANONICAL_SUCCESSORS = (
    "extras.tasks.alerts.evaluate_alert_rules_task",
    "extras.tasks.reports.generate_scheduled_report_task",
    "extras.tasks.webhooks.send_webhook_task",
    "assets.tasks.requests.notify_new_request_task",
    "assets.tasks.checkin.bulk_checkin_task",
    "assets.tasks.labels.generate_label_batch_task",
)


class TaskResubmissionGuardTests(TransactionTestCase):
    """All-or-nothing historical resubmission guard over the ORM broker."""

    def setUp(self):
        super().setUp()
        sync_patcher = mock.patch("django_q.conf.Conf.SYNC", False)
        sync_patcher.start()
        self.addCleanup(sync_patcher.stop)

    def _task_row(self, func, *, args=(), kwargs=None, hook=None):
        from django.utils import timezone
        from django_q.models import Failure

        now = timezone.now()
        return Failure.objects.create(
            id=uuid.uuid4().hex[:32],
            name="historical failure",
            func=func,
            hook=hook,
            args=args,
            kwargs={} if kwargs is None else kwargs,
            success=False,
            started=now,
            stopped=now,
        )

    def _raw_payload(self, task_id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT args, kwargs FROM django_q_task WHERE id = %s", [task_id])
            return cursor.fetchone()

    def test_unresolvable_paths_are_all_or_nothing(self):
        from core.django_q_task_resubmission import is_unresolvable_task_path, resubmit_task_guarded

        assert is_unresolvable_task_path(MISSING)
        assert is_unresolvable_task_path(MISSING_MODULE)
        assert not is_unresolvable_task_path(RESOLVABLE)
        assert not is_unresolvable_task_path(UNRELATED)
        assert not is_unresolvable_task_path(None)

        rows = [self._task_row(MISSING), self._task_row(UNRELATED)]
        before = {row.pk: self._raw_payload(row.pk) for row in rows}
        model_admin = mock.Mock(model=rows[0].__class__)
        request = mock.Mock()
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, request, rows)
            enqueue.assert_not_called()
        message = model_admin.message_user.call_args.args[1]
        assert "task_resubmission.unresolvable_path" in message
        assert MISSING in message
        assert "[]" not in message  # payloads never appear in rejection output
        self.assertEqual({task_id: self._raw_payload(task_id) for task_id in before}, before)

    def test_native_q2_args_and_kwargs_resubmit_and_failure_rows_are_deleted(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(UNRELATED, args=("asset", 7), kwargs={"notify": True})
        model_admin = mock.Mock(model=good.__class__)
        request = mock.Mock()
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, request, [good])
            enqueue.assert_called_once_with(
                UNRELATED,
                "asset",
                7,
                hook=None,
                group=None,
                cluster=None,
                notify=True,
            )
        model_admin.message_user.assert_not_called()
        self.assertFalse(good.__class__.objects.filter(pk=good.pk).exists())

    def test_enqueue_failure_rolls_back_queue_and_keeps_all_failure_rows(self):
        from django_q.brokers.orm import ORM
        from django_q.models import OrmQ

        from core.django_q_task_resubmission import resubmit_task_guarded

        rows = [self._task_row(UNRELATED), self._task_row(RESOLVABLE)]
        model_admin = mock.Mock(model=rows[0].__class__)
        request = mock.Mock()
        original_enqueue = ORM.enqueue
        calls = 0

        def fail_second_enqueue(broker, package):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("issue445 broker secret")
            return original_enqueue(broker, package)

        with mock.patch.object(ORM, "enqueue", new=fail_second_enqueue):
            resubmit_task_guarded(model_admin, request, rows)

        self.assertEqual(calls, 2)
        self.assertEqual(OrmQ.objects.count(), 0)
        self.assertEqual(rows[0].__class__.objects.filter(pk__in=[row.pk for row in rows]).count(), 2)
        message = model_admin.message_user.call_args.args[1]
        self.assertIn("task_resubmission.enqueue_failed", message)
        self.assertNotIn("issue445 broker secret", message)

    def test_unsupported_broker_fails_closed_without_queue_or_deletion(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        row = self._task_row(RESOLVABLE)
        model_admin = mock.Mock(model=row.__class__)
        with (
            mock.patch("core.django_q_task_resubmission.Conf.ORM", None),
            mock.patch("core.django_q_task_resubmission.async_task") as enqueue,
        ):
            resubmit_task_guarded(model_admin, mock.Mock(), [row])

        enqueue.assert_not_called()
        self.assertTrue(row.__class__.objects.filter(pk=row.pk).exists())
        self.assertIn("task_resubmission.unsupported_broker", model_admin.message_user.call_args.args[1])

    def test_native_list_args_and_cutover_path_are_allowed(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(RESOLVABLE, args=["asset"])
        model_admin = mock.Mock(model=good.__class__)
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, mock.Mock(), [good])
        enqueue.assert_called_once()

    def test_every_canonical_successor_neighborhood_is_allowed(self):
        from core.django_q_task_resubmission import is_unresolvable_task_path, resubmit_task_guarded

        for path in CANONICAL_SUCCESSORS:
            with self.subTest(path=path):
                self.assertFalse(is_unresolvable_task_path(path))
                row = self._task_row(path)
                model_admin = mock.Mock(model=row.__class__)
                with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
                    resubmit_task_guarded(model_admin, mock.Mock(), [row])
                enqueue.assert_called_once()

    def test_every_unresolvable_path_is_flagged(self):
        from core.django_q_task_resubmission import is_unresolvable_task_path

        for path in (MISSING, MISSING_MODULE):
            with self.subTest(path=path):
                self.assertTrue(is_unresolvable_task_path(path))

    def test_stale_hook_blocks_whole_selection(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        rows = [self._task_row(RESOLVABLE, hook=MISSING), self._task_row(UNRELATED)]
        model_admin = mock.Mock(model=rows[0].__class__)
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, mock.Mock(), rows)
        enqueue.assert_not_called()
        self.assertEqual(rows[0].__class__.objects.filter(pk__in=[row.pk for row in rows]).count(), 2)
        message = model_admin.message_user.call_args.args[1]
        self.assertIn(MISSING, message)

    def test_native_empty_tuple_args_are_allowed(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(UNRELATED, args=(), kwargs={})
        model_admin = mock.Mock(model=good.__class__)
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, mock.Mock(), [good])
        enqueue.assert_called_once_with(UNRELATED, hook=None, group=None, cluster=None)

    def test_legacy_json_payload_form_is_allowed(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(UNRELATED, args='["asset"]', kwargs='{"notify": true}')
        model_admin = mock.Mock(model=good.__class__)
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, mock.Mock(), [good])
        enqueue.assert_called_once()

    def test_malformed_mixed_selection_is_rejected_before_enqueue_or_delete(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(UNRELATED)
        malformed = self._task_row(RESOLVABLE, args={"not": "positional"})
        before = {row.pk: self._raw_payload(row.pk) for row in (good, malformed)}
        model_admin = mock.Mock(model=good.__class__)
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, mock.Mock(), [good, malformed])
        enqueue.assert_not_called()
        self.assertEqual(good.__class__.objects.filter(pk__in=before).count(), 2)
        self.assertEqual({task_id: self._raw_payload(task_id) for task_id in before}, before)

    def test_reserved_or_non_string_kwargs_are_rejected_all_or_nothing(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        for kwargs in ({"hook": "other.path"}, {1: "not-expandable"}):
            with self.subTest(kwargs=kwargs):
                row = self._task_row(UNRELATED, kwargs=kwargs)
                model_admin = mock.Mock(model=row.__class__)
                with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
                    resubmit_task_guarded(model_admin, mock.Mock(), [row])
                enqueue.assert_not_called()
                self.assertTrue(row.__class__.objects.filter(pk=row.pk).exists())
