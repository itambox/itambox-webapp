"""Regression tests for race-safe django-q2 schedule registration (WS5-9).

``Schedule`` has no unique constraint on ``func``, so the old
``get_or_create(func=...)`` was not idempotent: repeated/concurrent
registration could leave duplicate schedule rows that fire a task more than
once. ``core.schedules.register_schedule`` (and the app-config post_migrate
handlers that use it) must collapse to exactly one row per func.
"""
from django.apps import apps
from django.test import TestCase
from django_q.models import Schedule

from core.schedules import register_schedule

CORE_FUNC = 'core.tasks.evaluate_alert_rules_task'
SUBSCRIPTION_FUNC = 'subscriptions.tasks.check_subscription_expiries_and_reminders'


class RegisterScheduleHelperTests(TestCase):
    """The shared helper itself is idempotent and self-healing."""

    def setUp(self):
        Schedule.objects.all().delete()

    def test_repeated_registration_creates_one_row(self):
        for _ in range(3):
            register_schedule(
                CORE_FUNC,
                defaults={
                    'name': 'Daily Alert Rule Evaluation',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,
                },
            )
        self.assertEqual(Schedule.objects.filter(func=CORE_FUNC).count(), 1)

    def test_existing_duplicates_are_collapsed(self):
        # Simulate rows left behind by the old racy get_or_create.
        Schedule.objects.create(func=CORE_FUNC, name='dup-1', schedule_type=Schedule.DAILY)
        Schedule.objects.create(func=CORE_FUNC, name='dup-2', schedule_type=Schedule.DAILY)
        Schedule.objects.create(func=CORE_FUNC, name='dup-3', schedule_type=Schedule.DAILY)
        self.assertEqual(Schedule.objects.filter(func=CORE_FUNC).count(), 3)

        register_schedule(
            CORE_FUNC,
            defaults={
                'name': 'Daily Alert Rule Evaluation',
                'schedule_type': Schedule.DAILY,
                'repeats': -1,
            },
        )

        rows = Schedule.objects.filter(func=CORE_FUNC)
        self.assertEqual(rows.count(), 1)
        # The survivor keeps the earliest row and gets its defaults refreshed.
        survivor = rows.get()
        self.assertEqual(survivor.name, 'Daily Alert Rule Evaluation')
        self.assertEqual(survivor.repeats, -1)

    def test_returns_surviving_instance(self):
        first = register_schedule(CORE_FUNC, defaults={'schedule_type': Schedule.DAILY})
        second = register_schedule(CORE_FUNC, defaults={'schedule_type': Schedule.DAILY})
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)


class AppConfigScheduleRegistrationTests(TestCase):
    """Invoking the post_migrate handlers twice leaves one row per func."""

    def setUp(self):
        Schedule.objects.all().delete()

    def _run_handler(self, app_label, method_name):
        config = apps.get_app_config(app_label)
        handler = getattr(config, method_name)
        # post_migrate handlers receive (sender, **kwargs).
        handler(sender=config)

    def test_core_handler_is_idempotent(self):
        self._run_handler('core', '_register_alert_schedule')
        self._run_handler('core', '_register_alert_schedule')
        self.assertEqual(Schedule.objects.filter(func=CORE_FUNC).count(), 1)

    def test_subscriptions_handler_is_idempotent(self):
        self._run_handler('subscriptions', '_register_subscription_tasks')
        self._run_handler('subscriptions', '_register_subscription_tasks')
        self.assertEqual(Schedule.objects.filter(func=SUBSCRIPTION_FUNC).count(), 1)

    def test_no_duplicate_schedules_across_funcs(self):
        # Run every registering handler twice; each func must end with one row.
        self._run_handler('core', '_register_alert_schedule')
        self._run_handler('subscriptions', '_register_subscription_tasks')
        self._run_handler('core', '_register_alert_schedule')
        self._run_handler('subscriptions', '_register_subscription_tasks')

        for func in (CORE_FUNC, SUBSCRIPTION_FUNC):
            self.assertEqual(
                Schedule.objects.filter(func=func).count(), 1,
                msg=f"expected exactly one schedule row for {func}",
            )
