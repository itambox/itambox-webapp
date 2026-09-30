"""Frozen V1 contract tests for the Stable scheduled-report scheduling surface.

The Stable promotion freezes the scheduling contract; these tests pin every
clause that operators and reviewers rely on:

* the nine supported frequencies map to the nine django-q schedule types;
* a (re)registration anchors the first run deterministically — start time
  (today if still ahead, else tomorrow) or due-immediately, with a plain cron
  schedule anchoring at its next cron occurrence;
* editing a schedule does not silently move its cadence unless the frequency,
  cron expression, or start time changes (or it was re-activated);
* the library-owned cadence is calendar-aware (month-end clamps) and preserves
  local wall time across DST transitions in a DST-observing cluster time zone;
* deactivating a schedule removes its registration and keeps the saved
  schedule and its records; deleting the schedule removes the registration.

The cadence pins intentionally exercise ``Schedule.calculate_next_run`` — the
exact code the qcluster scheduler calls — so a django-q upgrade that changed
any of these semantics would fail here before it could reach a deployment.
"""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone
from django_q.models import Schedule

from extras.models import ReportTemplate, ScheduledReport
from extras.views import _initial_next_run, handle_report_scheduling

BERLIN = ZoneInfo("Europe/Berlin")

FREQUENCY_TO_SCHEDULE_TYPE = {
    ScheduledReport.FREQUENCY_ONCE: Schedule.ONCE,
    ScheduledReport.FREQUENCY_HOURLY: Schedule.HOURLY,
    ScheduledReport.FREQUENCY_DAILY: Schedule.DAILY,
    ScheduledReport.FREQUENCY_WEEKLY: Schedule.WEEKLY,
    ScheduledReport.FREQUENCY_BIWEEKLY: Schedule.BIWEEKLY,
    ScheduledReport.FREQUENCY_MONTHLY: Schedule.MONTHLY,
    ScheduledReport.FREQUENCY_QUARTERLY: Schedule.QUARTERLY,
    ScheduledReport.FREQUENCY_YEARLY: Schedule.YEARLY,
    ScheduledReport.FREQUENCY_CRON: Schedule.CRON,
}


def _berlin_localtime(value=None):
    """Cluster-local conversion as the qcluster would do in Europe/Berlin."""
    return timezone.localtime(value, BERLIN)


class TestFrequencyContract(TestCase):
    """Every supported frequency registers with the documented schedule type."""

    def setUp(self):
        self.template = ReportTemplate.objects.create(
            name="Contract Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )

    def _schedule_for(self, frequency, **overrides):
        sched = ScheduledReport.objects.create(
            name=f"{frequency} report",
            report=self.template,
            frequency=frequency,
            format=ScheduledReport.FORMAT_HTML,
            is_active=True,
            **overrides,
        )
        handle_report_scheduling(sched)
        sched.refresh_from_db()
        return sched

    def test_all_nine_frequencies_map_to_their_django_q_types(self):
        for frequency, expected_type in FREQUENCY_TO_SCHEDULE_TYPE.items():
            with self.subTest(frequency=frequency):
                overrides = {}
                if frequency == ScheduledReport.FREQUENCY_CRON:
                    overrides["cron_expression"] = "0 8 * * 1-5"
                sched = self._schedule_for(frequency, **overrides)

                self.assertIsNotNone(sched.schedule)
                self.assertEqual(sched.schedule.schedule_type, expected_type)
                self.assertEqual(sched.schedule.name, f"scheduled_report_{sched.pk}")
                self.assertEqual(sched.schedule.args, str(sched.pk))
                self.assertEqual(sched.schedule.repeats, -1)
                self.assertEqual(sched.schedule.intended_date_kwarg, "intended_fire_at")
                # The registration row must be owned by the schedule itself.
                self.assertEqual(sched.schedule.func, "extras.tasks.reports.generate_scheduled_report_task")

    def test_non_cron_frequencies_never_carry_a_cron_expression(self):
        sched = self._schedule_for(ScheduledReport.FREQUENCY_DAILY)
        self.assertEqual(sched.schedule.cron, "")

    def test_cron_frequency_carries_the_validated_expression(self):
        sched = self._schedule_for(ScheduledReport.FREQUENCY_CRON, cron_expression="15 6 * * 1")
        self.assertEqual(sched.schedule.cron, "15 6 * * 1")

    def test_once_frequency_still_uses_django_q_once_semantics(self):
        sched = self._schedule_for(ScheduledReport.FREQUENCY_ONCE)
        self.assertEqual(sched.schedule.schedule_type, Schedule.ONCE)
        # ONCE rows are deleted by the library after firing; repeats=-1 marks
        # the row as non-repeating from the registration side.
        self.assertEqual(sched.schedule.repeats, -1)


class TestFirstRunAnchoring(TestCase):
    """Registration anchors the first run deterministically."""

    def _report(self, *, start_time=None, frequency=ScheduledReport.FREQUENCY_DAILY, cron=""):
        return SimpleNamespace(start_time=start_time, frequency=frequency, cron_expression=cron)

    def test_start_time_later_today_anchors_today(self):
        now = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
        with timezone.override(UTC):
            next_run = _initial_next_run(self._report(start_time=time(8, 0)), now=now)
        self.assertEqual(timezone.localtime(next_run, UTC), datetime(2026, 9, 29, 8, 0, tzinfo=UTC))

    def test_start_time_already_passed_today_anchors_tomorrow(self):
        now = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
        with timezone.override(UTC):
            next_run = _initial_next_run(self._report(start_time=time(8, 0)), now=now)
        self.assertEqual(timezone.localtime(next_run, UTC), datetime(2026, 9, 30, 8, 0, tzinfo=UTC))

    def test_start_time_uses_the_local_calendar_day(self):
        # 22:30 UTC on the 29th is already the 30th in Berlin; a 08:00 Berlin
        # start time must anchor on the 30th, not the 29th.
        now = datetime(2026, 9, 29, 22, 30, tzinfo=UTC)
        with timezone.override(BERLIN):
            next_run = _initial_next_run(self._report(start_time=time(8, 0)), now=now)
        self.assertEqual(timezone.localtime(next_run, BERLIN), datetime(2026, 9, 30, 8, 0, tzinfo=BERLIN))

    def test_without_a_start_time_the_schedule_is_due_immediately(self):
        now = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
        next_run = _initial_next_run(self._report(start_time=None), now=now)
        self.assertEqual(next_run, now)

    def test_cron_without_a_start_time_anchors_at_the_next_local_occurrence(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=UTC)
        with timezone.override(UTC):
            next_run = _initial_next_run(
                self._report(start_time=None, frequency=ScheduledReport.FREQUENCY_CRON, cron="0 9 * * *"),
                now=now,
            )
        self.assertEqual(timezone.localtime(next_run, UTC), datetime(2026, 9, 29, 9, 0, tzinfo=UTC))

    def test_cron_candidates_advance_in_wall_clock_time(self):
        now = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
        with timezone.override(BERLIN):
            next_run = _initial_next_run(
                self._report(start_time=None, frequency=ScheduledReport.FREQUENCY_CRON, cron="0 8 * * *"),
                now=now,
            )
        # 23:30 UTC is 01:30 Berlin on the 30th; the next 08:00 wall-clock
        # occurrence is on the same local day.
        self.assertEqual(timezone.localtime(next_run, BERLIN), datetime(2026, 9, 30, 8, 0, tzinfo=BERLIN))

    def test_an_invalid_cron_expression_falls_back_to_due_immediately(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=UTC)
        next_run = _initial_next_run(
            self._report(start_time=None, frequency=ScheduledReport.FREQUENCY_CRON, cron="not a cron"),
            now=now,
        )
        self.assertEqual(next_run, now)


class TestCalendarCadence(TestCase):
    """The library-owned cadence is calendar-aware; these pins freeze it."""

    def _advance(self, schedule_type, next_run):
        schedule = Schedule(schedule_type=schedule_type)
        return schedule.calculate_next_run(next_run)

    def test_monthly_clamps_to_the_shorter_month(self):
        next_run = datetime(2026, 1, 31, 8, 0, tzinfo=UTC)
        advanced = self._advance(Schedule.MONTHLY, next_run)
        self.assertEqual(advanced, datetime(2026, 2, 28, 8, 0, tzinfo=UTC))

    def test_quarterly_clamps_to_thirty_day_months(self):
        next_run = datetime(2026, 1, 31, 8, 0, tzinfo=UTC)
        advanced = self._advance(Schedule.QUARTERLY, next_run)
        self.assertEqual(advanced, datetime(2026, 4, 30, 8, 0, tzinfo=UTC))

    def test_yearly_moves_february_29_to_february_28(self):
        next_run = datetime(2028, 2, 29, 8, 0, tzinfo=UTC)
        advanced = self._advance(Schedule.YEARLY, next_run)
        self.assertEqual(advanced, datetime(2029, 2, 28, 8, 0, tzinfo=UTC))

    def test_weekly_and_biweekly_advance_by_calendar_days(self):
        next_run = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
        self.assertEqual(self._advance(Schedule.WEEKLY, next_run), datetime(2026, 10, 6, 8, 0, tzinfo=UTC))
        self.assertEqual(self._advance(Schedule.BIWEEKLY, next_run), datetime(2026, 10, 13, 8, 0, tzinfo=UTC))

    def test_daily_keeps_local_wall_time_across_the_spring_dst_switch(self):
        next_run = datetime(2026, 3, 28, 8, 0, tzinfo=BERLIN)
        with patch("django_q.models.localtime", _berlin_localtime):
            advanced = self._advance(Schedule.DAILY, next_run)
        self.assertEqual(timezone.localtime(advanced, BERLIN), datetime(2026, 3, 29, 8, 0, tzinfo=BERLIN))

    def test_daily_keeps_local_wall_time_across_the_autumn_dst_switch(self):
        next_run = datetime(2026, 10, 24, 8, 0, tzinfo=BERLIN)
        with patch("django_q.models.localtime", _berlin_localtime):
            advanced = self._advance(Schedule.DAILY, next_run)
        self.assertEqual(timezone.localtime(advanced, BERLIN), datetime(2026, 10, 25, 8, 0, tzinfo=BERLIN))

    def test_cron_advances_to_the_next_wall_clock_occurrence(self):
        schedule = Schedule(schedule_type=Schedule.CRON, cron="0 8 * * *")
        frozen_now = datetime(2026, 3, 28, 9, 0, tzinfo=BERLIN)

        def _frozen_localtime(value=None):
            if value is None:
                return frozen_now
            return timezone.localtime(value, BERLIN)

        # The library computes the next cron occurrence from 'now'; freezing it
        # just after 08:00 on the day before the spring switch pins that the
        # next occurrence keeps the 08:00 wall-clock time across the DST edge.
        with patch("django_q.models.localtime", _frozen_localtime):
            advanced = schedule.calculate_next_run(datetime(2026, 3, 28, 8, 0, tzinfo=BERLIN))
        self.assertEqual(timezone.localtime(advanced, BERLIN), datetime(2026, 3, 29, 8, 0, tzinfo=BERLIN))

    def test_hourly_advances_by_absolute_hours(self):
        next_run = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
        self.assertEqual(self._advance(Schedule.HOURLY, next_run), datetime(2026, 3, 29, 1, 30, tzinfo=UTC))


class TestRegistrationLifecycle(TestCase):
    """Registration is idempotent, self-healing, and removal-safe."""

    def setUp(self):
        self.template = ReportTemplate.objects.create(
            name="Lifecycle Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )

    def _scheduled_report(self, **overrides):
        data = {
            "name": "Lifecycle",
            "report": self.template,
            "frequency": ScheduledReport.FREQUENCY_DAILY,
            "format": ScheduledReport.FORMAT_HTML,
            "start_time": time(8, 0),
            "is_active": True,
        }
        data.update(overrides)
        return ScheduledReport.objects.create(**data)

    def test_repeated_registration_keeps_exactly_one_row(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        handle_report_scheduling(sched)

        rows = Schedule.objects.filter(name=f"scheduled_report_{sched.pk}")
        self.assertEqual(rows.count(), 1)

    def test_registration_heals_duplicate_rows_and_keeps_the_referenced_row(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        keeper = sched.schedule
        duplicate = Schedule.objects.create(
            name=f"scheduled_report_{sched.pk}",
            func="extras.tasks.reports.generate_scheduled_report_task",
            args=str(sched.pk),
            schedule_type=Schedule.DAILY,
            repeats=-1,
        )
        # Point the schedule at the duplicate; registration must collapse onto
        # the surviving row and re-point the schedule.
        sched.schedule = duplicate
        sched.save(update_fields=["schedule"])

        handle_report_scheduling(sched)
        sched.refresh_from_db()

        rows = Schedule.objects.filter(name=f"scheduled_report_{sched.pk}")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().pk, keeper.pk)
        self.assertEqual(sched.schedule_id, keeper.pk)

    def test_reanchor_false_preserves_the_live_next_run(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        pinned = datetime(2026, 12, 24, 8, 0, tzinfo=UTC)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=pinned)

        handle_report_scheduling(sched, reanchor=False)

        sched.refresh_from_db()
        self.assertEqual(sched.schedule.next_run, pinned)

    def test_reanchor_true_moves_the_next_run_to_the_start_time_anchor(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=datetime(2026, 1, 1, 3, 0, tzinfo=UTC))

        handle_report_scheduling(sched, reanchor=True)

        sched.refresh_from_db()
        anchored = timezone.localtime(sched.schedule.next_run)
        self.assertEqual(anchored.time(), time(8, 0))

    def test_reanchor_false_recreates_a_missing_row_as_due(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        Schedule.objects.filter(name=f"scheduled_report_{sched.pk}").delete()

        handle_report_scheduling(sched, reanchor=False)

        sched.refresh_from_db()
        self.assertIsNotNone(sched.schedule)
        # A missing registration is recreated due-immediately, never silently
        # lost; the library advances the cadence from there.
        self.assertLessEqual(sched.schedule.next_run, timezone.now() + timedelta(seconds=5))

    def test_deactivation_removes_the_registration_and_keeps_the_records(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        sched.is_active = False
        sched.save(update_fields=["is_active"])

        handle_report_scheduling(sched)
        sched.refresh_from_db()

        self.assertFalse(Schedule.objects.filter(name=f"scheduled_report_{sched.pk}").exists())
        self.assertIsNone(sched.schedule_id)
        self.assertTrue(ScheduledReport.objects.filter(pk=sched.pk).exists())

    def test_deletion_removes_the_registration(self):
        sched = self._scheduled_report()
        handle_report_scheduling(sched)
        key = sched.pk

        sched.delete()

        self.assertFalse(Schedule.objects.filter(name=f"scheduled_report_{key}").exists())


class TestEditSemantics(TestCase):
    """Editing a schedule preserves its cadence unless the shape changed."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.tenant = None
        self.user = get_user_model().objects.create_superuser(
            username="schedule-editor", password="password123", email="editor@example.com"
        )
        self.template = ReportTemplate.objects.create(
            name="Edited Report",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
        )

    def _create_schedule(self):
        sched = ScheduledReport.objects.create(
            name="Editable",
            report=self.template,
            frequency=ScheduledReport.FREQUENCY_DAILY,
            format=ScheduledReport.FORMAT_HTML,
            start_time=time(8, 0),
            is_active=True,
            recipients="ops@example.com",
        )
        handle_report_scheduling(sched)
        sched.refresh_from_db()
        return sched

    def test_editing_recipients_keeps_the_live_next_run(self):
        self.client.force_login(self.user)
        sched = self._create_schedule()
        pinned = datetime(2026, 12, 24, 8, 0, tzinfo=UTC)
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=pinned)

        response = self.client.post(
            f"/extras/reports/schedules/{sched.pk}/edit/",
            {
                "name": sched.name,
                "report": self.template.pk,
                "frequency": ScheduledReport.FREQUENCY_DAILY,
                "format": ScheduledReport.FORMAT_HTML,
                "start_time": "08:00:00",
                "recipients": "new-ops@example.com",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        sched.refresh_from_db()
        self.assertEqual(sched.schedule.next_run, pinned)

    def test_changing_the_start_time_reanchors(self):
        self.client.force_login(self.user)
        sched = self._create_schedule()
        Schedule.objects.filter(pk=sched.schedule_id).update(next_run=datetime(2026, 1, 1, 3, 0, tzinfo=UTC))

        response = self.client.post(
            f"/extras/reports/schedules/{sched.pk}/edit/",
            {
                "name": sched.name,
                "report": self.template.pk,
                "frequency": ScheduledReport.FREQUENCY_DAILY,
                "format": ScheduledReport.FORMAT_HTML,
                "start_time": "10:15:00",
                "recipients": "ops@example.com",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        sched.refresh_from_db()
        anchored = timezone.localtime(sched.schedule.next_run)
        self.assertEqual(anchored.time(), time(10, 15))
