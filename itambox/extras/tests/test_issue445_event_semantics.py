"""Issue #445 event terminal-attempt and logging contracts."""

import importlib
import inspect
import logging

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from assets.models import Manufacturer
from core.models import Notification
from extras.models import Event, EventRule


def _event_service():
    try:
        return importlib.import_module("extras.services.events")
    except ImportError:
        return importlib.import_module("core.events")


class Issue445EventSemanticsTests(TestCase):
    """Preserved attempts/isolation pass; replay and sanitized-error assertions are RED."""

    CANARY = "issue445-event-secret-canary"

    def setUp(self):
        self.content_type = ContentType.objects.get_for_model(Manufacturer)
        self.user = get_user_model().objects.create_user(username="issue445-event-user", password="pw")
        self.event = Event.objects.create(
            model=self.content_type,
            object_id=445,
            action=Event.ACTION_CREATE,
            data={"app_label": "assets", "model_name": "manufacturer"},
        )
        self.failing_rule = EventRule.objects.create(
            name="Issue445 failing rule",
            model=self.content_type,
            events=[self.event.action],
            action_type=EventRule.ACTION_NOTIFICATION,
            enabled=True,
        )
        self.success_rule = EventRule.objects.create(
            name="Issue445 success rule",
            model=self.content_type,
            events=[self.event.action],
            action_type=EventRule.ACTION_NOTIFICATION,
            enabled=True,
        )

    def _dispatch_with_canary(self):
        service = _event_service()
        attempts = []

        def execute(rule, event, _tenant_id=None):
            attempts.append(rule.pk)
            if rule.pk == self.failing_rule.pk:
                raise RuntimeError(self.CANARY)
            Notification.objects.create(user=self.user, subject="issue445-success", message="safe")

        return service, attempts, execute

    def test_each_eligible_rule_is_attempted_once_and_sibling_success_survives(self):
        service, attempts, execute = self._dispatch_with_canary()
        with self.assertLogs(service.__name__, level="ERROR"), self.subTest("isolated actions"):
            from unittest.mock import patch

            with patch.object(service, "_execute_event_action", side_effect=execute):
                service.process_event_rules(self.event)
        self.event.refresh_from_db()
        self.assertCountEqual(attempts, [self.failing_rule.pk, self.success_rule.pk])
        self.assertEqual(Notification.objects.filter(subject="issue445-success").count(), 1)
        self.assertTrue(self.event.processed)

    def test_no_eligible_rule_marks_event_processed(self):
        service = _event_service()
        self.failing_rule.enabled = False
        self.failing_rule.save(update_fields=["enabled"])
        self.success_rule.enabled = False
        self.success_rule.save(update_fields=["enabled"])
        service.process_event_rules(self.event)
        self.event.refresh_from_db()
        self.assertTrue(self.event.processed, "missing issue445 no-eligible-rule terminal event contract")

    def test_caught_failure_is_terminal_and_second_dispatch_does_not_replay(self):
        service, attempts, execute = self._dispatch_with_canary()
        from unittest.mock import patch

        with patch.object(service, "_execute_event_action", side_effect=execute):
            service.process_event_rules(self.event)
            service.process_event_rules(self.event)
        self.event.refresh_from_db()
        self.assertTrue(self.event.processed, "missing issue445 caught-failure terminal event contract")
        self.assertEqual(
            attempts.count(self.failing_rule.pk),
            1,
            "missing issue445 non-replayable caught-failure contract",
        )
        self.assertEqual(
            Notification.objects.filter(subject="issue445-success").count(),
            1,
            "missing issue445 processed-event no-duplicate-side-effect contract",
        )

    def test_rule_failure_log_has_only_safe_ids_and_exception_class(self):
        service, _attempts, execute = self._dispatch_with_canary()
        from unittest.mock import patch

        logger = logging.getLogger(service.__name__)
        with self.assertLogs(logger, level="ERROR") as captured:
            with patch.object(service, "_execute_event_action", side_effect=execute):
                service.process_event_rules(self.event)

        self.assertEqual(len(captured.records), 1, "missing issue445 one-record event failure audit contract")
        record = captured.records[0]
        rendered = captured.output[0]
        self.assertIn(str(self.event.pk), rendered)
        self.assertIn(str(self.failing_rule.pk), rendered)
        self.assertIn("RuntimeError", rendered, "missing issue445 exception-class-only event log contract")
        self.assertNotIn(self.CANARY, rendered, "missing issue445 canary-redaction event log contract")
        self.assertIsNone(record.exc_info, "missing issue445 traceback-free event log contract")
        self.assertIsNone(record.exc_text, "missing issue445 traceback-free event log contract")

    def test_rule_processing_boundary_never_calls_logger_exception(self):
        service = _event_service()
        source = inspect.getsource(service.process_event_rules)
        self.assertNotIn(
            "logger.exception",
            source,
            "missing issue445 logger.exception prohibition at the event-rule boundary",
        )
