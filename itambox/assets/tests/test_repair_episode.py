"""Repair timeline and the retired episode surface (#504, #644).

The repair maintenance is the anchor of a repair story: its loan and its disposal
are grouped under it on the timeline of the failed asset *and* of the loaner, and
assignments and loans appear as timeline events. The beta-era RepairEpisode, its
CRUD surface and its navigation entry are gone.
"""

import datetime
import uuid
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from model_bakery import baker

from assets.forms.disposal_form import AssetDisposalForm
from assets.forms.fields import selectable_repair_maintenances
from assets.models import Asset, AssetAssignment, AssetMaintenance, AssetReservation, AssetType, StatusLabel, Warranty
from assets.models.lifecycle import AssetDisposal
from assets.services import checkout_asset, disposal_service_payload, dispose_asset, update_asset_disposal
from assets.services.timeline import AssetTimeline, build_asset_timeline
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from organization.models import AssetHolder

User = get_user_model()


def _asset(name, tenant=None, **kwargs):
    status = baker.make(StatusLabel, type="deployable")
    # A realistic asset carries a type (and thereby a manufacturer); the detail
    # page assumes one when rendering its support panel for staff users.
    asset_type = baker.make(AssetType)
    return baker.make(Asset, name=name, status=status, tenant=tenant, asset_type=asset_type, **kwargs)


def _maintenance(asset, start="2026-01-10", maintenance_type="repair", **kwargs):
    kwargs.setdefault("completion_date", None)
    return baker.make(
        AssetMaintenance,
        asset=asset,
        maintenance_type=maintenance_type,
        start_date=datetime.date.fromisoformat(start),
        **kwargs,
    )


def _deployed_status():
    """A deployed-type label: an active assignment requires the asset to wear one."""
    return StatusLabel.objects.filter(type="deployed").first() or baker.make(
        StatusLabel, type="deployed", name="Deployed"
    )


def _holder(**kwargs):
    return baker.make(AssetHolder, **kwargs)


def _loan(asset, holder, maintenance=None, due_date=None, **kwargs):
    """Issue a real loan: the checkout service owns the deployed status (#644)."""
    _deployed_status()
    checkout_asset(
        asset,
        holder=holder,
        is_loan=True,
        due_date=due_date,
        maintenance=maintenance,
        **kwargs,
    )
    return AssetAssignment.objects.get(asset=asset, is_active=True)


def _handover(asset, holder, **kwargs):
    """A plain checkout, so the timeline has a non-loan assignment to show."""
    _deployed_status()
    checkout_asset(asset, holder=holder, **kwargs)
    return AssetAssignment.objects.get(asset=asset, is_active=True)


def _reservation(asset, start="2026-01-20", holder=None, **kwargs):
    return baker.make(
        AssetReservation,
        asset=asset,
        reserved_for=holder,
        start_date=datetime.date.fromisoformat(start),
        end_date=datetime.date.fromisoformat(start) + datetime.timedelta(days=7),
        **kwargs,
    )


def _disposal(asset, maintenance=None, day="2026-02-01", **kwargs):
    return baker.make(
        AssetDisposal,
        asset=asset,
        maintenance=maintenance,
        disposal_date=datetime.date.fromisoformat(day),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


class RepairTimelineTests(TestCase):
    def setUp(self):
        self.asset = _asset("Main Laptop")
        self.loaner = _asset("Loaner Laptop")
        self.holder = _holder()

    def test_empty_timeline_has_no_events(self):
        timeline = build_asset_timeline(self.asset)
        self.assertEqual(timeline.total, 0)
        self.assertFalse(timeline.has_groups)
        self.assertEqual(timeline.ungrouped, [])

    def test_records_without_a_repair_stay_chronological(self):
        _maintenance(self.asset, start="2026-01-10", maintenance_type="upgrade")
        baker.make(
            Warranty,
            asset=self.asset,
            start_date=datetime.date(2026, 1, 5),
            end_date=datetime.date(2027, 1, 5),
        )
        _reservation(self.asset, start="2026-01-20")
        _handover(self.asset, self.holder)

        timeline = build_asset_timeline(self.asset)
        self.assertFalse(timeline.has_groups)
        self.assertEqual(
            [event.date for event in timeline.ungrouped], sorted(event.date for event in timeline.ungrouped)
        )
        self.assertEqual(
            {event.kind for event in timeline.ungrouped}, {"maintenance", "warranty", "reservation", "assignment"}
        )
        self.assertEqual(timeline.total, 4)

    def test_repair_groups_its_loan_and_disposal(self):
        maintenance = _maintenance(self.asset)
        _loan(self.loaner, self.holder, maintenance=maintenance)
        _disposal(self.asset, maintenance=maintenance)

        timeline = build_asset_timeline(self.asset)
        self.assertTrue(timeline.has_groups)
        self.assertEqual(len(timeline.groups), 1)
        group = timeline.groups[0]
        self.assertEqual(group.maintenance.pk, maintenance.pk)
        self.assertEqual({event.kind for event in group.events}, {"maintenance", "loan", "disposal"})
        self.assertEqual([asset.pk for asset in group.substitute_assets], [self.loaner.pk])
        self.assertEqual(timeline.ungrouped, [])
        self.assertEqual(timeline.total, 3)

    def test_story_spans_the_loaner_asset_on_both_pages(self):
        maintenance = _maintenance(self.asset)
        _loan(self.loaner, self.holder, maintenance=maintenance)
        _disposal(self.asset, maintenance=maintenance)

        main_timeline = build_asset_timeline(self.asset)
        loaner_timeline = build_asset_timeline(self.loaner)
        for timeline in (main_timeline, loaner_timeline):
            self.assertEqual(len(timeline.groups), 1)
            group = timeline.groups[0]
            self.assertEqual({event.kind for event in group.events}, {"maintenance", "loan", "disposal"})
            keys = [(event.kind, event.url, event.date) for event in group.events]
            self.assertEqual(len(keys), len(set(keys)), "the two views must not duplicate an event")

        # The borrowed record names the asset it really belongs to, on both pages.
        loaner_maintenance = [event for event in loaner_timeline.groups[0].events if event.kind == "maintenance"][0]
        self.assertTrue(any("Main Laptop" in part for part in loaner_maintenance.parts))
        main_disposal = [event for event in main_timeline.groups[0].events if event.kind == "disposal"][0]
        self.assertEqual(main_disposal.parts, [])
        main_loan = [event for event in main_timeline.groups[0].events if event.kind == "loan"][0]
        self.assertTrue(any("Loaner Laptop" in part for part in main_loan.parts))

    def test_a_loan_for_another_asset_is_a_plain_event(self):
        """A loan that belongs to no repair is still an event, in chronological order."""
        due_date = datetime.date.today() + datetime.timedelta(days=1)
        _loan(self.loaner, self.holder, due_date=due_date)

        timeline = build_asset_timeline(self.loaner)
        self.assertFalse(timeline.has_groups)
        self.assertEqual([event.kind for event in timeline.ungrouped], ["loan"])
        event = timeline.ungrouped[0]
        self.assertIn(_("Due %(date)s") % {"date": due_date.isoformat()}, event.parts)
        self.assertIn(_("Active"), event.parts)

    def test_an_ended_overdue_loan_reports_its_state(self):
        assignment = _loan(self.loaner, self.holder)
        assignment.is_active = False
        assignment.due_date = datetime.date.today() - datetime.timedelta(days=5)
        assignment.returned_at = datetime.date.today() - datetime.timedelta(days=4)
        assignment.save()

        timeline = build_asset_timeline(self.loaner)
        event = timeline.ungrouped[0]
        self.assertEqual(event.kind, "loan")
        self.assertIn(_("Ended"), event.parts)
        self.assertIn(_("Returned %(date)s") % {"date": assignment.returned_at.isoformat()}, event.parts)

    def test_non_repair_work_stays_ungrouped(self):
        _maintenance(self.asset, maintenance_type="calibration")

        timeline = build_asset_timeline(self.asset)
        self.assertFalse(timeline.has_groups)
        self.assertEqual([event.kind for event in timeline.ungrouped], ["maintenance"])

    def test_status_transitions_read_from_the_changelog(self):
        deployable = self.asset.status
        repair = baker.make(StatusLabel, type="maintenance", name="In Repair")
        content_type = ContentType.objects.get_for_model(Asset)

        def change(prechange, postchange, when):
            return baker.make(
                ObjectChange,
                tenant=None,
                time=when,
                user_name="tester",
                request_id=uuid.uuid4(),
                object_repr=str(self.asset),
                action="update",
                changed_object_type=content_type,
                changed_object_id=self.asset.pk,
                prechange_data=prechange,
                postchange_data=postchange,
            )

        change({"status": deployable.pk}, {"status": repair.pk}, timezone.now())
        # A same-value save and a row without a status key produce no event.
        change({"status": repair.pk}, {"status": repair.pk}, timezone.now())
        change({"name": "x"}, {"name": "y"}, timezone.now())
        # A history entry whose old label no longer exists stays renderable.
        change({"status": 99999999}, {"status": repair.pk}, timezone.now())
        # Clearing the status renders without a label instead of breaking.
        change({"status": repair.pk}, {"status": None}, timezone.now())

        timeline = build_asset_timeline(self.asset)
        status_events = [event for event in timeline.ungrouped if event.kind == "status"]
        self.assertEqual(len(status_events), 3)
        self.assertTrue(all(event.url.endswith("?tab=changelog") for event in status_events))
        self.assertTrue(
            any(event.title == _("Status changed to %(status)s") % {"status": "In Repair"} for event in status_events)
        )
        self.assertTrue(
            any(
                event.title == _("Status changed to %(status)s") % {"status": _("(no status)")}
                for event in status_events
            )
        )
        self.assertTrue(any(_("Unknown status") in part for event in status_events for part in event.parts))

    def test_record_details_are_part_of_the_event(self):
        maintenance = _maintenance(self.asset, completion_date=datetime.date.fromisoformat("2026-01-12"))
        disposal = _disposal(self.asset, maintenance=maintenance, recipient="Recycler GmbH")
        cancellation = baker.make(User, username="canceller")
        disposal.cancelled_at = timezone.now()
        disposal.cancelled_by = cancellation
        disposal.cancellation_reason = "Wrong asset"
        disposal.save()
        # Records the pre-guard era left in the recycle bin still render.
        AssetDisposal.all_objects.filter(pk=disposal.pk).update(deleted_at=timezone.now())
        _reservation(self.asset, start="2026-01-22")

        timeline = build_asset_timeline(self.asset)
        events = {event.kind: event for event in timeline.groups[0].events}
        window = maintenance.completion_date.isoformat()
        self.assertIn(_("Completed %(date)s") % {"date": window}, events["maintenance"].parts)
        self.assertIn(_("Recipient: %(recipient)s") % {"recipient": "Recycler GmbH"}, events["disposal"].parts)
        self.assertIn(_("Cancelled"), events["disposal"].parts)
        self.assertIn(_("Previously deleted (recycle bin)"), events["disposal"].parts)
        reservation_event = [event for event in timeline.ungrouped if event.kind == "reservation"][0]
        self.assertIn(_("(no holder)"), reservation_event.title)


# ---------------------------------------------------------------------------
# Retired surface
# ---------------------------------------------------------------------------


class RepairEpisodeSurfaceTests(TestCase):
    def test_the_model_is_gone(self):
        with self.assertRaises(ImportError):
            from assets.models import RepairEpisode  # noqa: F401  # expected ImportError asserts removal

    def test_the_routes_are_gone(self):
        for name in (
            "assets:repairepisode_list",
            "assets:repairepisode_create",
            "assets:repairepisode_detail",
            "assets:repairepisode_update",
            "assets:repairepisode_delete",
        ):
            with self.assertRaises(NoReverseMatch):
                reverse(name, kwargs={"pk": 1} if name.endswith(("detail", "update", "delete")) else {})

    def test_the_generic_export_declaration_is_gone(self):
        from core.data_transfer import DECLARATIONS

        self.assertNotIn("assets.repairepisode", DECLARATIONS)

    def test_reservations_no_longer_carry_a_repair_link(self):
        field_names = {field.name for field in AssetReservation._meta.get_fields()}
        self.assertNotIn("episode", field_names)

    def test_assignments_and_disposals_anchor_on_the_maintenance(self):
        self.assertIn("maintenance", {field.name for field in AssetAssignment._meta.get_fields()})
        self.assertIn("maintenance", {field.name for field in AssetDisposal._meta.get_fields()})


# ---------------------------------------------------------------------------
# Disposal write path and the maintenance picker
# ---------------------------------------------------------------------------


class DisposalRepairLinkTests(TestCase):
    def setUp(self):
        self.asset = _asset("Main Laptop")
        self.maintenance = _maintenance(self.asset)

    def test_disposal_service_payload_carries_the_maintenance(self):
        payload = disposal_service_payload(
            {
                "disposal_method": "recycle",
                "disposal_date": datetime.date(2026, 2, 1),
                "maintenance": self.maintenance,
            }
        )
        self.assertEqual(payload["maintenance"], self.maintenance)

    def test_dispose_asset_links_the_maintenance(self):
        baker.make(StatusLabel, type="archived")
        user = baker.make(User, username="technician")
        disposal = dispose_asset(
            self.asset,
            disposal_method="recycle",
            disposal_date=datetime.date(2026, 2, 1),
            maintenance=self.maintenance,
            user=user,
        )
        self.assertEqual(disposal.maintenance_id, self.maintenance.pk)

    def test_update_asset_disposal_can_amend_the_maintenance(self):
        baker.make(StatusLabel, type="archived")
        other = _maintenance(self.asset, start="2026-03-01")
        disposal = _disposal(self.asset, maintenance=self.maintenance)

        update_asset_disposal(disposal, user=None, data={"maintenance": other})
        disposal.refresh_from_db()
        self.assertEqual(disposal.maintenance_id, other.pk)

    def test_selectable_maintenances_keeps_a_deleted_current_link(self):
        self.maintenance.delete()
        listed = set(selectable_repair_maintenances().values_list("pk", flat=True))
        self.assertNotIn(self.maintenance.pk, listed)
        kept = set(selectable_repair_maintenances(self.maintenance.pk).values_list("pk", flat=True))
        self.assertIn(self.maintenance.pk, kept)

    def test_disposal_form_keeps_the_linked_deleted_maintenance_selectable(self):
        disposal = _disposal(self.asset, maintenance=self.maintenance)
        form = AssetDisposalForm(instance=disposal)
        self.assertIn("maintenance", form.fields)
        self.maintenance.delete()
        form = AssetDisposalForm(instance=disposal)
        self.assertIn(self.maintenance.pk, set(form.fields["maintenance"].queryset.values_list("pk", flat=True)))


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


class RepairTimelineDetailViewTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context()
        self.user = baker.make(User, is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        self.asset = _asset("View Laptop", tenant=self.tenant)
        self.loaner = _asset("View Loaner", tenant=self.tenant)
        self.holder = _holder(tenant=self.tenant)
        self.maintenance = _maintenance(self.asset)
        _loan(self.loaner, self.holder, maintenance=self.maintenance)
        _disposal(self.asset, maintenance=self.maintenance)

    def test_the_asset_page_offers_log_repair(self):
        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Log repair")
        self.assertNotContains(response, "Add Repair Episode")

    def test_the_asset_page_groups_the_whole_repair_story(self):
        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Repair on View Laptop")
        self.assertContains(response, "View Loaner")

    def test_the_loaner_page_groups_the_story_and_names_the_failed_asset(self):
        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.loaner.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This asset stood in for")
        self.assertContains(response, "View Laptop")


# ---------------------------------------------------------------------------
# Recent activity card (#645)
# ---------------------------------------------------------------------------


class RecentActivityCardTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context()
        self.user = baker.make(User, is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        self.asset = _asset("Card Laptop", tenant=self.tenant)

    def test_asset_overview_lists_the_five_newest_events_flattened(self):
        records = [_maintenance(self.asset, start=f"2026-02-{index:02d}") for index in range(1, 7)]

        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))

        self.assertEqual(response.status_code, 200)
        recent_activity = response.context["recent_activity"]
        expected_records = list(reversed(records[1:]))
        self.assertEqual(len(recent_activity), 5)
        self.assertEqual(
            [event.url for event in recent_activity], [record.get_absolute_url() for record in expected_records]
        )
        self.assertContains(response, "Recent activity")
        self.assertContains(response, "Show full timeline")
        self.assertContains(response, 'href="?tab=timeline"')

        rendered = response.content.decode()
        self.assertEqual(rendered.count("asset-recent-activity-kind"), 5)
        for event in recent_activity:
            expected_date = '<span class="text-secondary small text-nowrap">' + event.date.isoformat() + "</span>"
            expected_badge = (
                '<span class="badge bg-' + event.color + '-lt asset-recent-activity-kind">' + event.label + "</span>"
            )
            expected_link = '<a href="' + event.url + '" class="flex-fill text-truncate">' + event.title + "</a>"
            self.assertIn(expected_date, rendered)
            self.assertIn(expected_badge, rendered)
            self.assertIn(expected_link, rendered)

    def test_asset_overview_shows_the_timeline_empty_state(self):
        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recent_activity"], [])
        self.assertContains(response, 'id="asset-recent-activity"')
        self.assertContains(response, "Recent activity")
        self.assertContains(response, "No lifecycle records exist for this asset yet.")
        self.assertContains(response, "Show full timeline")

    def test_asset_detail_recent_activity_does_not_add_database_queries(self):
        _maintenance(self.asset, start="2026-02-12")
        url = reverse("assets:asset_detail", kwargs={"pk": self.asset.pk})

        # Warm process-level caches (content types, templates, translations) so
        # the comparison measures the request itself, not first-hit setup work.
        self.client.get(url)

        with CaptureQueriesContext(connection) as baseline_queries:
            with patch.object(AssetTimeline, "recent_events", new_callable=PropertyMock, return_value=[]):
                baseline_response = self.client.get(url)
        with CaptureQueriesContext(connection) as recent_queries:
            response = self.client.get(url)

        self.assertEqual(baseline_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(recent_queries), len(baseline_queries))
        self.assertEqual(len(response.context["recent_activity"]), 1)
