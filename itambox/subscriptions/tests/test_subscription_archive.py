"""Per-aggregate archive for Subscription (#619 step 3).

Deleting a subscription is an archive of an aggregate root: the assignments that
say who it covers move with it, and the licenses it funds are unlinked. The
former generic cascade physically deleted the assignments (``CASCADE`` with no
``deleted_at``) and silently left the licenses pointing at the archived
subscription (``SET_NULL`` was ignored by the collector). These tests pin:

- the archive moves the assignments with the subscription, each with its own
  audited delete and the operation marker, and detaches every funding license
  with its own audited update;
- idempotent re-archive;
- restore brings the assignments an operation archived back and never re-attaches
  a detached license or resurrects an assignment that was ended on its own;
- a restore refused by the active-slug unique slot, or by a target that is
  covered again, leaves everything archived (no partial restore);
- atomicity: a failure after the child writes rolls the whole archive back;
- tenant isolation of the service;
- that the UI delete, the bulk delete, the REST API delete and the recycle-bin
  restore all reach the service instead of the generic single-row soft delete.
"""

from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from assets.models import Manufacturer, Supplier
from core.archive_handlers import ArchiveBlocked
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from itambox.middleware import _current_user, _request_id
from licenses.models import License, Software
from organization.models import AssetHolder, Tenant
from subscriptions.archive_services import archive_subscription, restore_subscription
from subscriptions.models import Subscription, SubscriptionAssignment


@contextmanager
def acting_as(user=None):
    """Enter the request audit context the archive service writes into.

    Outside a request/task context ``save()`` silently skips the audit row, so
    assertions about ``ObjectChange`` need this explicitly.
    """
    request_token = _request_id.set(uuid4())
    user_token = _current_user.set(user)
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _current_user.reset(user_token)


def object_changes(instance, action=None):
    """Audit rows written for one instance."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    rows = ObjectChange._base_manager.filter(
        changed_object_type=content_type,
        changed_object_id=instance.pk,
    )
    return rows.filter(action=action) if action else rows


class SubscriptionArchiveFixtureMixin:
    """Fixtures shared by the service-level and surface-level suites."""

    def make_subscription(self, name="P619 Subscription", **extra):
        if "supplier" not in extra:
            extra["supplier"] = Supplier.objects.create(name=f"P619 Supplier {name}", tenant=self.tenant)
        values = {"name": name, "tenant": self.tenant}
        values.update(extra)
        return Subscription.objects.create(**values)

    def make_holder(self, upn=None):
        return AssetHolder.objects.create(
            first_name="P619",
            last_name="Covered",
            upn=upn or f"p619.covered.{uuid4().hex[:8]}@example.test",
            tenant=self.tenant,
        )

    def make_assignment(self, subscription, target=None):
        target = target or self.make_holder()
        return SubscriptionAssignment.objects.create(
            subscription=subscription,
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
        )

    def make_license(self, subscription, name="P619 License"):
        software = Software.objects.create(
            name=f"P619 Software {name}",
            manufacturer=Manufacturer.objects.create(
                name=f"P619 Software Mfg {name}", slug=f"p619-sw-mfg-{uuid4().hex[:8]}"
            ),
            tenant=self.tenant,
        )
        return License.objects.create(
            name=name,
            software=software,
            seats=10,
            tenant=self.tenant,
            subscription=subscription,
        )


class SubscriptionArchiveServiceTests(SubscriptionArchiveFixtureMixin, TenantTestMixin, TestCase):
    """`archive_subscription` / `restore_subscription` on their own."""

    def setUp(self):
        self.setup_tenant_context(name="P619 Sub Tenant", slug="p619-sub-tenant")
        self.subscription = self.make_subscription()
        self.first_assignment = self.make_assignment(self.subscription)
        self.second_assignment = self.make_assignment(self.subscription)
        self.license = self.make_license(self.subscription)

    def archive(self, subscription=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return archive_subscription(subscription or self.subscription)

    def restore(self, subscription=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return restore_subscription(subscription or self.subscription)

    # ------------------------------------------------------------- happy path
    def test_archive_moves_the_assignments_and_detaches_the_licenses(self):
        result = self.archive()

        # One archived root plus both assignments; one detached license.
        self.assertEqual(result.archived, 3)
        self.assertEqual(result.detached, 1)
        self.assertIsNotNone(result.operation_id)

        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.deleted_at)
        self.assertFalse(Subscription.objects.filter(pk=self.subscription.pk).exists())
        self.assertTrue(Subscription.all_objects.filter(pk=self.subscription.pk).exists())
        self.assertEqual(object_changes(self.subscription, action="delete").count(), 1)

        for assignment in (self.first_assignment, self.second_assignment):
            assignment.refresh_from_db()
            self.assertIsNotNone(assignment.deleted_at)
            self.assertEqual(assignment.archive_operation_id, result.operation_id)
            self.assertEqual(object_changes(assignment, action="delete").count(), 1)
        self.assertEqual(SubscriptionAssignment.objects.filter(subscription=self.subscription).count(), 0)

        self.license.refresh_from_db()
        self.assertIsNone(self.license.subscription_id)
        # The unlink is its own audited update, one per license.
        self.assertEqual(object_changes(self.license, action="update").count(), 1)

    def test_archive_keeps_the_journal_as_evidence(self):
        self.subscription.journal_entries.create(comment="P619 journal note", user=self.tenant_admin)

        result = self.archive()

        self.assertEqual(result.kept, 1)
        self.assertEqual(self.subscription.journal_entries.count(), 1)

    def test_archive_is_idempotent_and_audits_once(self):
        self.archive()
        second = self.archive()

        self.assertEqual(second.archived, 0)
        self.assertEqual(second.detached, 0)
        self.assertEqual(object_changes(self.subscription, action="delete").count(), 1)
        self.assertEqual(object_changes(self.first_assignment, action="delete").count(), 1)
        self.assertEqual(object_changes(self.license, action="update").count(), 1)

    def test_restore_brings_back_the_archived_assignments_only(self):
        self.archive()
        self.restore()

        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.deleted_at)
        self.assertTrue(Subscription.objects.filter(pk=self.subscription.pk).exists())
        self.assertEqual(object_changes(self.subscription, action="update").count(), 1)

        for assignment in (self.first_assignment, self.second_assignment):
            assignment.refresh_from_db()
            self.assertIsNone(assignment.deleted_at)
            self.assertIsNone(assignment.archive_operation_id)
            self.assertEqual(object_changes(assignment, action="update").count(), 1)
        self.assertEqual(self.subscription.assignments.count(), 2)

        # A detached license is never re-attached by a restore.
        self.license.refresh_from_db()
        self.assertIsNone(self.license.subscription_id)

    def test_restore_leaves_an_ended_assignment_deleted(self):
        """Only rows an archive operation moved come back: a holder detach has no marker."""
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            self.second_assignment.delete()
        self.second_assignment.refresh_from_db()
        self.assertIsNotNone(self.second_assignment.deleted_at)
        self.assertIsNone(self.second_assignment.archive_operation_id)

        self.archive()
        self.restore()

        self.first_assignment.refresh_from_db()
        self.assertIsNone(self.first_assignment.deleted_at)
        self.second_assignment.refresh_from_db()
        self.assertIsNotNone(self.second_assignment.deleted_at)
        self.assertEqual(self.subscription.assignments.count(), 1)

    # ------------------------------------------------------------- refusals
    def test_restore_refuses_when_another_active_subscription_uses_the_slug(self):
        self.archive()
        self.make_subscription(name="P619 Reusing Subscription", slug=self.subscription.slug)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.restore()

        self.assertIn(self.subscription.slug, caught.exception.headline)
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.deleted_at)
        self.assertEqual(object_changes(self.subscription, action="update").count(), 0)
        self.first_assignment.refresh_from_db()
        self.assertIsNotNone(self.first_assignment.deleted_at)

    def test_restore_refuses_when_a_target_is_covered_again(self):
        """The conditional unique slot allows a re-assignment while the archive stands."""
        self.archive()
        replacement = self.make_assignment(self.subscription, target=self.first_assignment.assigned_object)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.restore()

        self.assertIn("live subscription assignment", caught.exception.headline)
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.deleted_at)
        self.first_assignment.refresh_from_db()
        self.assertIsNotNone(self.first_assignment.deleted_at)
        self.assertIsNone(replacement.deleted_at)

    # ------------------------------------------------------------- atomicity
    def test_archive_is_atomic_when_a_later_step_fails(self):
        with patch(
            "subscriptions.archive_services._kept_evidence_count",
            side_effect=RuntimeError("archive failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.archive()

        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.deleted_at)
        for assignment in (self.first_assignment, self.second_assignment):
            assignment.refresh_from_db()
            self.assertIsNone(assignment.deleted_at)
            self.assertIsNone(assignment.archive_operation_id)
        self.license.refresh_from_db()
        self.assertEqual(self.license.subscription_id, self.subscription.pk)
        self.assertEqual(object_changes(self.subscription).count(), 0)
        self.assertEqual(object_changes(self.license).count(), 0)

    # ------------------------------------------------------------- tenant boundary
    def test_archive_fails_closed_for_a_subscription_outside_the_active_tenant(self):
        other_tenant = Tenant.objects.create(name="P619 Sub Other", slug="p619-sub-other")
        foreign = Subscription.objects.create(
            name="P619 Foreign Subscription",
            supplier=Supplier.objects.create(name="P619 Foreign Supplier", tenant=other_tenant),
            tenant=other_tenant,
        )
        foreign_assignment = SubscriptionAssignment.objects.create(
            subscription=foreign,
            content_type=ContentType.objects.get_for_model(foreign),
            object_id=foreign.pk,
        )

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                archive_subscription(foreign)

        foreign.refresh_from_db()
        foreign_assignment.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)
        self.assertIsNone(foreign_assignment.deleted_at)


class SubscriptionArchiveSurfaceTests(SubscriptionArchiveFixtureMixin, TenantTestMixin, TestCase):
    """Every delete/restore surface reaches the aggregate service."""

    SUBSCRIPTION_PERMISSIONS = [
        "subscriptions.view_subscription",
        "subscriptions.change_subscription",
        "subscriptions.delete_subscription",
        "core.view_recyclebin",
        "core.change_recyclebin",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="P619 Sub Surface Tenant",
            slug="p619-sub-surface",
            permissions=list(self.SUBSCRIPTION_PERMISSIONS),
        )
        self.subscription = self.make_subscription()
        self.assignment = self.make_assignment(self.subscription)
        self.client_login_to_tenant(self.tenant_user, self.tenant)

    def _messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def _archive_subscription_row(self, subscription):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            return archive_subscription(subscription)

    def _archived_assignment(self, assignment):
        return SubscriptionAssignment.all_objects.get(pk=assignment.pk)

    # ------------------------------------------------------------- UI delete
    def test_ui_delete_archives_the_subscription_and_its_assignments(self):
        response = self.client.post(reverse("subscriptions:subscription_delete", kwargs={"pk": self.subscription.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Subscription.all_objects.get(pk=self.subscription.pk).deleted_at)
        self.assertIsNotNone(self._archived_assignment(self.assignment).deleted_at)

    # ------------------------------------------------------------- bulk delete
    def test_bulk_delete_archives_through_the_service(self):
        response = self.client.post(
            reverse("subscriptions:subscription_bulk_delete"),
            {
                "_confirm": "1",
                "pk": [self.subscription.pk],
                "return_url": reverse("subscriptions:subscription_list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Subscription.all_objects.get(pk=self.subscription.pk).deleted_at)
        self.assertIsNotNone(self._archived_assignment(self.assignment).deleted_at)

    # ------------------------------------------------------------- API delete
    def _api_detail_url(self, subscription):
        return reverse("api:subscriptions_api:subscription-detail", kwargs={"pk": subscription.pk})

    def test_api_delete_archives_the_subscription_and_its_assignments(self):
        detail_url = self._api_detail_url(self.subscription)
        current = self.client.get(detail_url)

        response = self.client.delete(detail_url, HTTP_IF_MATCH=current["ETag"])

        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(Subscription.all_objects.get(pk=self.subscription.pk).deleted_at)
        self.assertIsNotNone(self._archived_assignment(self.assignment).deleted_at)

    def test_api_delete_of_another_tenants_subscription_is_404(self):
        other = Tenant.objects.create(name="P619 Sub API Other", slug="p619-sub-api-other")
        foreign = Subscription.objects.create(
            name="P619 Foreign API Subscription",
            supplier=Supplier.objects.create(name="P619 Foreign API Supplier", tenant=other),
            tenant=other,
        )

        response = self.client.delete(self._api_detail_url(foreign))

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(Subscription.all_objects.get(pk=foreign.pk).deleted_at)

    # ------------------------------------------------------------- recycle bin
    def test_recycle_bin_restore_uses_the_service(self):
        self._archive_subscription_row(self.subscription)
        content_type = ContentType.objects.get_for_model(Subscription)

        response = self.client.post(
            reverse(
                "object_restore",
                kwargs={"content_type_id": content_type.pk, "object_id": self.subscription.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Subscription.all_objects.get(pk=self.subscription.pk).deleted_at)
        self.assertIsNone(self._archived_assignment(self.assignment).deleted_at)
        self.assertEqual(self.subscription.assignments.count(), 1)

    def test_recycle_bin_restore_refusal_keeps_everything_archived(self):
        self._archive_subscription_row(self.subscription)
        self.make_subscription(name="P619 Reusing Subscription", slug=self.subscription.slug)
        content_type = ContentType.objects.get_for_model(Subscription)

        response = self.client.post(
            reverse(
                "object_restore",
                kwargs={"content_type_id": content_type.pk, "object_id": self.subscription.pk},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("another active subscription" in message for message in self._messages(response)))
        self.assertIsNotNone(Subscription.all_objects.get(pk=self.subscription.pk).deleted_at)
        self.assertIsNotNone(self._archived_assignment(self.assignment).deleted_at)
