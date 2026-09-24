"""#504 — repair/replacement episodes: model, timeline builder, write paths, CRUD.

An episode is the optional hub that groups the records of one repair or
replacement story (maintenance, reservation, disposal) around the asset and,
when one exists, its loaner/substitute. The timeline builder renders that story
on the asset detail page; records without an episode stay in the flat
chronological fallback, which is what every record written before the feature
existed renders as.
"""

import datetime
import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from model_bakery import baker

from assets.filters import RepairEpisodeFilterSet
from assets.forms import RepairEpisodeForm
from assets.forms.disposal_form import AssetDisposalForm
from assets.forms.fields import selectable_episodes
from assets.forms.reservation_form import AssetReservationForm
from assets.models import (
    Asset,
    AssetMaintenance,
    AssetReservation,
    AssetType,
    RepairEpisode,
    StatusLabel,
    Warranty,
)
from assets.models.lifecycle import AssetDisposal
from assets.services import disposal_service_payload, dispose_asset, update_asset_disposal
from assets.services.timeline import build_asset_timeline
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant

User = get_user_model()


def _asset(name, tenant=None, **kwargs):
    status = baker.make(StatusLabel, type="deployable")
    # A realistic asset carries a type (and thereby a manufacturer); the detail
    # page assumes one when rendering its support panel for staff users.
    asset_type = baker.make(AssetType)
    return baker.make(Asset, name=name, status=status, tenant=tenant, asset_type=asset_type, **kwargs)


def _episode(asset, **kwargs):
    kwargs.setdefault("notes", "")
    return baker.make(RepairEpisode, asset=asset, **kwargs)


def _maintenance(asset, episode=None, start="2026-01-10", **kwargs):
    return baker.make(
        AssetMaintenance,
        asset=asset,
        episode=episode,
        start_date=datetime.date.fromisoformat(start),
        completion_date=None,
        **kwargs,
    )


def _reservation(asset, episode=None, start="2026-01-20", holder=None, **kwargs):
    return baker.make(
        AssetReservation,
        asset=asset,
        episode=episode,
        reserved_for=holder,
        start_date=datetime.date.fromisoformat(start),
        end_date=datetime.date.fromisoformat(start) + datetime.timedelta(days=7),
        **kwargs,
    )


def _disposal(asset, episode=None, day="2026-02-01", **kwargs):
    return baker.make(
        AssetDisposal,
        asset=asset,
        episode=episode,
        disposal_date=datetime.date.fromisoformat(day),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


class RepairEpisodeModelTests(TestCase):
    def setUp(self):
        self.asset = _asset("Main Laptop")
        self.loaner = _asset("Loaner Laptop")

    def test_episode_links_the_asset_and_its_substitute(self):
        episode = _episode(self.asset, substitute_asset=self.loaner)
        episode.full_clean()
        self.assertEqual(episode.asset_id, self.asset.pk)
        self.assertEqual(episode.substitute_asset_id, self.loaner.pk)

    def test_substitute_cannot_be_the_asset_itself(self):
        episode = RepairEpisode(asset=self.asset, substitute_asset=self.asset)
        with self.assertRaises(ValidationError):
            episode.full_clean()

    def test_substitute_must_belong_to_the_same_tenant(self):
        tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        asset_a = _asset("Asset A", tenant=tenant_a)
        asset_b = _asset("Asset B", tenant=tenant_b)
        sister = _asset("Asset A2", tenant=tenant_a)

        with self.assertRaises(ValidationError):
            RepairEpisode(asset=asset_a, substitute_asset=asset_b).full_clean()
        RepairEpisode(asset=asset_a, substitute_asset=sister).full_clean()

    def test_tenant_property_follows_the_asset(self):
        tenant = Tenant.objects.create(name="Tenant T", slug="tenant-t")
        asset = _asset("Tenant Asset", tenant=tenant)
        self.assertEqual(_episode(asset).tenant, tenant)
        self.assertIsNone(_episode(self.asset).tenant)

    def test_str_and_absolute_url(self):
        episode = _episode(self.asset)
        self.assertIn("Main Laptop", str(episode))
        self.assertEqual(episode.get_absolute_url(), reverse("assets:repairepisode_detail", kwargs={"pk": episode.pk}))

    def test_records_stay_valid_without_an_episode(self):
        maintenance = _maintenance(self.asset)
        reservation = _reservation(self.asset)
        disposal = _disposal(self.asset)
        for record in (maintenance, reservation, disposal):
            self.assertIsNone(record.episode_id)

    def test_records_link_to_the_episode_through_the_optional_fk(self):
        episode = _episode(self.asset)
        maintenance = _maintenance(self.asset, episode=episode)
        reservation = _reservation(self.asset, episode=episode)
        disposal = _disposal(self.asset, episode=episode)
        self.assertEqual(maintenance.episode_id, episode.pk)
        self.assertEqual(reservation.episode_id, episode.pk)
        self.assertEqual(disposal.episode_id, episode.pk)


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


class RepairEpisodeTimelineTests(TestCase):
    def setUp(self):
        self.asset = _asset("Main Laptop")
        self.loaner = _asset("Loaner Laptop")

    def test_empty_timeline_has_no_events(self):
        timeline = build_asset_timeline(self.asset)
        self.assertEqual(timeline.total, 0)
        self.assertFalse(timeline.has_episodes)
        self.assertEqual(timeline.ungrouped, [])

    def test_records_without_episode_stay_chronological(self):
        _maintenance(self.asset, start="2026-01-10")
        baker.make(
            Warranty,
            asset=self.asset,
            start_date=datetime.date(2026, 1, 5),
            end_date=datetime.date(2027, 1, 5),
        )
        _reservation(self.asset, start="2026-01-20")

        timeline = build_asset_timeline(self.asset)
        self.assertFalse(timeline.has_episodes)
        self.assertEqual(
            [event.date for event in timeline.ungrouped], sorted(event.date for event in timeline.ungrouped)
        )
        self.assertEqual({event.kind for event in timeline.ungrouped}, {"maintenance", "warranty", "reservation"})
        self.assertEqual(timeline.total, 3)

    def test_linked_records_are_grouped_under_the_episode(self):
        episode = _episode(self.asset, substitute_asset=self.loaner)
        _maintenance(self.asset, episode=episode)
        _reservation(self.asset, episode=episode)

        timeline = build_asset_timeline(self.asset)
        self.assertTrue(timeline.has_episodes)
        self.assertEqual(len(timeline.groups), 1)
        group = timeline.groups[0]
        self.assertEqual(group.episode.pk, episode.pk)
        self.assertEqual({event.kind for event in group.events}, {"maintenance", "reservation"})
        self.assertEqual(timeline.ungrouped, [])
        self.assertEqual(timeline.total, 2)

    def test_unlinked_records_fall_back_to_the_flat_list(self):
        episode = _episode(self.asset)
        _maintenance(self.asset, episode=episode)
        baker.make(
            Warranty,
            asset=self.asset,
            start_date=datetime.date(2026, 1, 5),
            end_date=datetime.date(2027, 1, 5),
        )

        timeline = build_asset_timeline(self.asset)
        self.assertEqual(len(timeline.groups), 1)
        self.assertEqual([event.kind for event in timeline.ungrouped], ["warranty"])
        self.assertEqual(timeline.total, 2)

    def test_story_spans_the_substitute_asset_on_both_pages(self):
        episode = _episode(self.asset, substitute_asset=self.loaner)
        _maintenance(self.asset, episode=episode)
        _reservation(self.loaner, episode=episode)

        main_timeline = build_asset_timeline(self.asset)
        loaner_timeline = build_asset_timeline(self.loaner)
        for timeline in (main_timeline, loaner_timeline):
            self.assertEqual(len(timeline.groups), 1)
            group = timeline.groups[0]
            self.assertEqual({event.kind for event in group.events}, {"maintenance", "reservation"})
            keys = [(event.kind, event.url) for event in group.events]
            self.assertEqual(len(keys), len(set(keys)), "the two views must not duplicate an event")

        # The borrowed record names the asset it really belongs to, on both pages.
        main_reservation = [event for event in main_timeline.groups[0].events if event.kind == "reservation"][0]
        self.assertTrue(any("Loaner Laptop" in part for part in main_reservation.parts))
        loaner_maintenance = [event for event in loaner_timeline.groups[0].events if event.kind == "maintenance"][0]
        self.assertTrue(any("Main Laptop" in part for part in loaner_maintenance.parts))

    def test_orphaned_episode_link_falls_back_to_ungrouped(self):
        other = _asset("Unrelated Server")
        foreign_episode = _episode(other)
        _maintenance(self.asset, episode=foreign_episode)

        timeline = build_asset_timeline(self.asset)
        self.assertFalse(timeline.has_episodes)
        self.assertEqual([event.kind for event in timeline.ungrouped], ["maintenance"])

    def test_episode_without_records_keeps_an_empty_group(self):
        episode = _episode(self.asset)
        timeline = build_asset_timeline(self.asset)
        self.assertTrue(timeline.has_episodes)
        self.assertEqual(timeline.groups[0].episode.pk, episode.pk)
        self.assertEqual(timeline.groups[0].events, [])
        self.assertEqual(timeline.total, 0)

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
        episode = _episode(self.asset)
        maintenance = _maintenance(self.asset, episode=episode)
        maintenance.completion_date = maintenance.start_date + datetime.timedelta(days=2)
        maintenance.save()
        _reservation(self.asset, episode=episode, start="2026-01-22")
        disposal = _disposal(self.asset, episode=episode, recipient="Recycler GmbH")
        cancellation = baker.make(User, username="canceller")
        disposal.cancelled_at = timezone.now()
        disposal.cancelled_by = cancellation
        disposal.cancellation_reason = "Wrong asset"
        disposal.save()
        # Records the pre-guard era left in the recycle bin still render.
        AssetDisposal.all_objects.filter(pk=disposal.pk).update(deleted_at=timezone.now())

        timeline = build_asset_timeline(self.asset)
        events = {event.kind: event for event in timeline.groups[0].events}
        window = maintenance.completion_date.isoformat()
        self.assertIn(_("Completed %(date)s") % {"date": window}, events["maintenance"].parts)
        self.assertIn(_("Recipient: %(recipient)s") % {"recipient": "Recycler GmbH"}, events["disposal"].parts)
        self.assertIn(_("Cancelled"), events["disposal"].parts)
        self.assertIn(_("Previously deleted (recycle bin)"), events["disposal"].parts)
        self.assertIn(_("(no holder)"), events["reservation"].title)


# ---------------------------------------------------------------------------
# Write paths: disposal service, forms and the episode picker
# ---------------------------------------------------------------------------


class RepairEpisodeWritePathTests(TestCase):
    def setUp(self):
        self.asset = _asset("Main Laptop")
        self.episode = _episode(self.asset)

    def test_disposal_service_payload_carries_the_episode(self):
        payload = disposal_service_payload(
            {"disposal_method": "recycle", "disposal_date": datetime.date(2026, 2, 1), "episode": self.episode}
        )
        self.assertEqual(payload["episode"], self.episode)

    def test_dispose_asset_links_the_episode(self):
        baker.make(StatusLabel, type="archived")
        user = baker.make(User, username="technician")
        disposal = dispose_asset(
            self.asset,
            disposal_method="recycle",
            disposal_date=datetime.date(2026, 2, 1),
            episode=self.episode,
            user=user,
        )
        self.assertEqual(disposal.episode_id, self.episode.pk)

    def test_update_asset_disposal_can_amend_the_episode(self):
        baker.make(StatusLabel, type="archived")
        other = _episode(self.asset, notes="second")
        disposal = _disposal(self.asset, episode=self.episode)

        update_asset_disposal(disposal, user=None, data={"episode": other})
        disposal.refresh_from_db()
        self.assertEqual(disposal.episode_id, other.pk)

    def test_selectable_episodes_keeps_a_deleted_current_link(self):
        self.episode.delete()
        self.assertNotIn(self.episode.pk, set(selectable_episodes().values_list("pk", flat=True)))
        self.assertIn(self.episode.pk, set(selectable_episodes(self.episode.pk).values_list("pk", flat=True)))

    def test_disposal_form_keeps_the_linked_deleted_episode_selectable(self):
        disposal = _disposal(self.asset, episode=self.episode)
        form = AssetDisposalForm(instance=disposal)
        self.assertIn("episode", form.fields)
        self.episode.delete()
        form = AssetDisposalForm(instance=disposal)
        self.assertIn(self.episode.pk, set(form.fields["episode"].queryset.values_list("pk", flat=True)))

    def test_reservation_form_exposes_the_episode_picker(self):
        form = AssetReservationForm()
        self.assertIn("episode", form.fields)
        self.assertIn(self.episode.pk, set(form.fields["episode"].queryset.values_list("pk", flat=True)))

    def test_episode_form_rejects_the_asset_as_its_own_substitute(self):
        form = RepairEpisodeForm(data={"asset": self.asset.pk, "substitute_asset": self.asset.pk, "notes": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("substitute_asset", form.errors)


# ---------------------------------------------------------------------------
# CRUD surface + filter
# ---------------------------------------------------------------------------


class RepairEpisodeViewTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context()
        self.user = baker.make(User, is_superuser=True, is_staff=True)
        self.asset = _asset("View Laptop", tenant=self.tenant)
        self.episode = _episode(self.asset)

    def test_list_view_requires_login(self):
        response = self.client.get(reverse("assets:repairepisode_list"))
        self.assertEqual(response.status_code, 302)

    def test_list_view_renders_the_episode(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("assets:repairepisode_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Laptop")

    def test_detail_view_renders_the_episode_panel(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("assets:repairepisode_detail", kwargs={"pk": self.episode.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Laptop")

    def test_asset_detail_page_renders_the_timeline_tab(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tab=timeline")

    def test_admin_changelist_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:assets_repairepisode_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Laptop")

    def test_create_view_prefills_the_asset_from_the_query_string(self):
        self.client.force_login(self.user)
        response = self.client.get(f"{reverse('assets:repairepisode_create')}?asset={self.asset.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.context["form"].initial["asset"]), str(self.asset.pk))

    def test_delete_view_asks_for_confirmation(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("assets:repairepisode_delete", kwargs={"pk": self.episode.pk}))
        self.assertEqual(response.status_code, 200)

    def test_filter_searches_asset_name_and_notes(self):
        other_asset = _asset("Other Server", tenant=self.tenant)
        other_episode = _episode(other_asset, notes="fan rattle")
        queryset = RepairEpisode.objects.all()

        by_name = RepairEpisodeFilterSet({"q": "View Laptop"}, queryset=queryset)
        self.assertEqual(set(by_name.qs.values_list("pk", flat=True)), {self.episode.pk})

        by_notes = RepairEpisodeFilterSet({"q": "fan rattle"}, queryset=queryset)
        self.assertEqual(set(by_notes.qs.values_list("pk", flat=True)), {other_episode.pk})
