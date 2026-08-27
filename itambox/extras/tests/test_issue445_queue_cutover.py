"""Queue/executable-surface verification for the issue #445 task cutover.

Exercises the strict ``verify_issue445_task_cutover`` management command
against a real PostgreSQL database with seeded django-q Schedule/OrmQ state,
plus the all-or-nothing historical resubmission guard. No network access;
the queue broker used here is the ORM broker on the test database.
"""

import uuid
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings

LEGACY = "core.tasks.evaluate_alert_rules_task"
CUTOVER = "extras.tasks.alerts.evaluate_alert_rules_task"
UNRELATED = "core.tasks.retention.prune_changelog_task"
ALIAS = "core.tasks.alerts.evaluate_alert_rules_task.extra"


@pytest.mark.serial_only
class Issue445QueueCutoverTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self._clear_queue_state()

    def _clear_queue_state(self):
        from django_q.models import OrmQ, Schedule

        Schedule.objects.all().delete()
        OrmQ.objects.all().delete()

    def _schedule(self, func, hook=None, *, args="", kwargs=""):
        from django_q.models import Schedule

        return Schedule.objects.create(
            name=f"queue-cutover {func}",
            func=func,
            hook=hook,
            args=args,
            kwargs=kwargs,
            schedule_type="D",
            minutes=0,
            repeats=-1,
        )

    def _ormq(self, func, *, hook=None, chain=None, kwargs=None, key="ITAMbox-Cluster"):
        from django_q.models import OrmQ
        from django_q.tasks import async_task

        class RecordingOrmBroker:
            list_key = key

            def enqueue(self, package):
                return OrmQ.objects.create(key=self.list_key, payload=package).pk

        q_options = {"broker": RecordingOrmBroker(), "sync": False}
        if hook is not None:
            q_options["hook"] = hook
        if chain is not None:
            q_options["chain"] = chain
        async_task(func, q_options=q_options, **(kwargs or {}))
        return OrmQ.objects.latest("pk")

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
        from django_q.signing import SignedPackage

        row = self._ormq(LEGACY)
        decoded = SignedPackage.loads(row.payload)
        self.assertIsInstance(decoded, dict)
        self.assertEqual(decoded["func"], LEGACY)
        self.assertIsInstance(decoded["args"], tuple)
        self.assertIsInstance(decoded["kwargs"], dict)
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_ormq_hook_and_tuple_chain_are_recursively_inventoried(self):
        nested = (LEGACY, (), {"q_options": {"hook": LEGACY, "chain": [(LEGACY, (), {})]}})
        self._ormq(LEGACY, hook=LEGACY, chain=[nested])
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_ormq_nested_chain_phase_mismatch_is_rejected(self):
        self._ormq(CUTOVER, chain=[(CUTOVER, (), {"q_options": {"hook": LEGACY}})])
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-postmigrate", strict=True)

    def test_schedule_kwargs_q_options_and_tuple_chain_are_inventoried(self):
        nested = "[('core.tasks.evaluate_alert_rules_task', (), {'q_options': {'hook': 'core.tasks.evaluate_alert_rules_task'}})]"
        self._schedule(LEGACY, kwargs="{'q_options': {'hook': %r, 'chain': %s}}" % (LEGACY, nested))
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_schedule_kwargs_keyword_syntax_is_inventoried(self):
        self._schedule(LEGACY, kwargs=f"q_options={{'hook': {LEGACY!r}}}")
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_schedule_nested_q_options_phase_mismatch_is_rejected(self):
        self._schedule(CUTOVER, kwargs=f"{{'q_options': {{'hook': {LEGACY!r}}}}}")
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-postmigrate", strict=True)

    def test_schedule_invalid_kwargs_q_options_or_chain_shape_fails_closed(self):
        bad_values = (
            "not valid Python",
            "[]",
            "{'q_options': []}",
            "{'q_options': {'chain': [('core.tasks.evaluate_alert_rules_task',)]}}",
        )
        for kwargs in bad_values:
            with self.subTest(kwargs=kwargs):
                self._schedule(LEGACY, kwargs=kwargs)
                with self.assertRaises(CommandError):
                    call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)
                self._clear_queue_state()

    def test_ormq_invalid_decoded_shape_fails_closed(self):
        from django_q.signing import SignedPackage

        mutations = (
            lambda package: [package["func"], (), {}, None],
            lambda package: {**package, "args": {}},
            lambda package: {**package, "kwargs": []},
            lambda package: {**package, "chain": [(LEGACY,)]},
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                row = self._ormq(LEGACY)
                package = SignedPackage.loads(row.payload)
                row.payload = SignedPackage.dumps(mutate(package))
                row.save(update_fields=["payload"])
                with self.assertRaises(CommandError):
                    call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)
                self._clear_queue_state()

    @override_settings(
        Q_CLUSTER={
            "name": "ITAMbox-Cluster",
            "orm": "default",
            "ALT_CLUSTERS": {"issue445-secondary": {"orm": "default"}},
        }
    )
    def test_every_configured_orm_cluster_is_inventoried(self):
        self._ormq(LEGACY, key="issue445-secondary")
        call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    @override_settings(Q_CLUSTER={"name": "unsupported", "redis": {"host": "127.0.0.1"}})
    def test_unsupported_broker_fails_before_inventory(self):
        with self.assertRaises(CommandError):
            call_command("verify_issue445_task_cutover", phase="forward-preflight", strict=True)

    def test_ormq_undeclared_cluster_fails_closed_without_printing_key(self):
        secret_key = "private-cluster-identifier"
        self._ormq(LEGACY, key=secret_key)
        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command(
                "verify_issue445_task_cutover",
                phase="forward-preflight",
                strict=True,
                stdout=stdout,
                stderr=stderr,
            )
        self.assertNotIn(secret_key, str(raised.exception))
        self.assertNotIn(secret_key, stdout.getvalue())
        self.assertNotIn(secret_key, stderr.getvalue())

    def test_ormq_package_with_unknown_form_is_undecodable(self):
        from django_q.models import OrmQ

        secret_payload = "not-a-package-with-private-material"
        OrmQ.objects.create(key="ITAMbox-Cluster", payload=secret_payload)
        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command(
                "verify_issue445_task_cutover",
                phase="forward-preflight",
                strict=True,
                stdout=stdout,
                stderr=stderr,
            )
        self.assertNotIn(secret_payload, str(raised.exception))
        self.assertNotIn(secret_payload, stdout.getvalue())
        self.assertNotIn(secret_payload, stderr.getvalue())

    def test_reverse_postmigrate_accepts_predecessor_paths_again(self):
        self._schedule(LEGACY)
        call_command("verify_issue445_task_cutover", phase="reverse-postmigrate", strict=True)


class Issue445ResubmissionGuardTests(TransactionTestCase):
    """All-or-nothing historical resubmission guard (no queue writes)."""

    def _task_row(self, func, *, args=(), kwargs=None):
        from django.utils import timezone
        from django_q.models import Failure

        now = timezone.now()
        return Failure.objects.create(
            id=uuid.uuid4().hex[:32],
            name="historical failure",
            func=func,
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

    def test_blocked_paths_are_all_or_nothing(self):
        from core.django_q_task_resubmission import is_blocked_task_path, resubmit_task_guarded

        assert is_blocked_task_path(LEGACY)
        assert is_blocked_task_path(ALIAS)
        assert not is_blocked_task_path(CUTOVER)
        assert not is_blocked_task_path(UNRELATED)
        assert not is_blocked_task_path(None)

        rows = [self._task_row(LEGACY), self._task_row(UNRELATED)]
        before = {row.pk: self._raw_payload(row.pk) for row in rows}
        model_admin = mock.Mock(model=rows[0].__class__)
        request = mock.Mock()
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, request, rows)
            enqueue.assert_not_called()
        message = model_admin.message_user.call_args.args[1]
        assert "task_resubmission.blocked_moved_path" in message
        assert LEGACY in message
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

    def test_native_list_args_and_cutover_path_are_allowed(self):
        from core.django_q_task_resubmission import resubmit_task_guarded

        good = self._task_row(CUTOVER, args=["asset"])
        model_admin = mock.Mock(model=good.__class__)
        with mock.patch("core.django_q_task_resubmission.async_task") as enqueue:
            resubmit_task_guarded(model_admin, mock.Mock(), [good])
        enqueue.assert_called_once()

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
        malformed = self._task_row(CUTOVER, args={"not": "positional"})
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
