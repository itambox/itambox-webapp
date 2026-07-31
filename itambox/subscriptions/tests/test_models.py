import datetime
import threading

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from model_bakery import baker

from assets.models import Asset
from core.managers import set_current_tenant
from core.models import Notification
from licenses.models import License, LicenseSeatAssignment
from organization.models import AssetHolder, CostCenter, Location, Site, Tenant, TenantGroup
from software.models import Software
from subscriptions.models import (
    BillingCycleChoices,
    Provider,
    Subscription,
    SubscriptionAssignment,
    SubscriptionStatusChoices,
    SubscriptionTypeChoices,
)

User = get_user_model()


class SubscriptionSeatRollupTests(TestCase):
    """Seats are tracked on Licenses; a Subscription rolls them up across the
    licenses it funds (Subscription -> License -> Software)."""

    def test_seats_roll_up_from_funded_licenses(self):
        sub = baker.make(Subscription, tenant=None)
        software = baker.make(Software, manufacturer__name="Acme", manufacturer__slug="acme", tenant=None)
        baker.make(License, software=software, subscription=sub, seats=10, tenant=None)
        l2 = baker.make(License, software=software, subscription=sub, seats=5, tenant=None)
        # A license NOT funded by this subscription must not be counted.
        baker.make(License, software=software, subscription=None, seats=99, tenant=None)

        self.assertEqual(sub.total_seats, 15)
        self.assertEqual(sub.assigned_seats, 0)
        self.assertEqual(sub.available_seats, 15)

        holder = baker.make(AssetHolder, tenant=None)
        baker.make(LicenseSeatAssignment, license=l2, assigned_holder=holder, asset=None)
        self.assertEqual(sub.assigned_seats, 1)
        self.assertEqual(sub.available_seats, 14)

    def test_license_rejects_cross_tenant_subscription(self):
        t_a = baker.make(Tenant, name="A", slug="a")
        t_b = baker.make(Tenant, name="B", slug="b")
        sub_b = baker.make(Subscription, tenant=t_b)
        software = baker.make(Software, manufacturer__name="Acme2", manufacturer__slug="acme2", tenant=None)
        lic = baker.prepare(License, software=software, subscription=sub_b, seats=1, tenant=t_a)
        with self.assertRaises(ValidationError):
            lic.clean()

    def test_seat_accounting_ignores_stale_targets_and_is_stable_while_suspended(self):
        tenant = baker.make(Tenant, name="Seat tenant", slug="seat-tenant")
        other_tenant = baker.make(Tenant, name="Former tenant", slug="former-tenant")
        sub = baker.make(Subscription, tenant=tenant)
        software = baker.make(Software, manufacturer__name="Seat Co", manufacturer__slug="seat-co", tenant=None)
        license_obj = baker.make(License, software=software, subscription=sub, seats=4, tenant=tenant)
        holder = baker.make(AssetHolder, tenant=tenant)
        asset = baker.make(Asset, tenant=tenant)
        baker.make(LicenseSeatAssignment, license=license_obj, assigned_holder=holder, asset=None)
        baker.make(LicenseSeatAssignment, license=license_obj, assigned_holder=None, asset=asset)

        self.assertEqual(sub.assigned_seats, 2)
        set_current_tenant(other_tenant)
        try:
            self.assertEqual((sub.total_seats, sub.assigned_seats, sub.available_seats), (4, 2, 2))
        finally:
            set_current_tenant(None)

        sub.suspend()
        self.assertEqual((sub.total_seats, sub.assigned_seats, sub.available_seats), (4, 2, 2))

        asset.delete()
        self.assertEqual(sub.assigned_seats, 1)

        holder.tenant = other_tenant
        holder.save(update_fields=["tenant"])
        self.assertEqual(sub.assigned_seats, 0)


class ProviderModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            name="AWS",
            account_id="aws-12345",
            portal_url="https://aws.amazon.com/console",
            is_active=True,
        )

    def test_provider_creation(self):
        self.assertEqual(str(self.provider), "AWS")
        self.assertTrue(self.provider.is_active)

    def test_provider_contact_resolution(self):
        from organization.models import Contact, ContactAssignment, ContactRole

        role, _ = ContactRole.objects.get_or_create(
            slug="primary-contact", defaults={"name": "Primary Contact", "description": "Primary Contact"}
        )
        contact = Contact.objects.create(
            name="AWS Account Manager", email="manager@aws.example.com", phone="+1-800-555-0199"
        )
        ContactAssignment.objects.create(
            contact=contact,
            role=role,
            content_type=ContentType.objects.get_for_model(Provider),
            object_id=self.provider.pk,
            priority="primary",
        )
        self.assertEqual(self.provider.primary_contact, contact)

    def test_provider_absolute_url(self):
        url = self.provider.get_absolute_url()
        self.assertIn(str(self.provider.pk), url)

    def test_provider_slug_auto_generation(self):
        provider = Provider.objects.create(name="Google Cloud Platform")
        self.assertEqual(provider.slug, "google-cloud-platform")

    def test_provider_inactive_does_not_filter_out(self):
        provider = Provider.objects.create(name="Old Vendor", is_active=False)
        self.assertFalse(Provider.objects.filter(is_active=True).filter(pk=provider.pk).exists())


class SubscriptionModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Adobe Inc.", account_id="adobe-001")
        self.today = timezone.now().date()

    def test_subscription_creation(self):
        sub = Subscription.objects.create(
            name="Adobe Creative Cloud",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            start_date=self.today - datetime.timedelta(days=90),
            renewal_date=self.today + datetime.timedelta(days=275),
            renewal_cost=599.99,
            currency="USD",
            billing_cycle=BillingCycleChoices.ANNUAL,
            term_months=12,
            auto_renewal=True,
            licensed_quantity=25,
            contract_reference="PO-2026-0042",
            cost_center=None,
        )
        self.assertEqual(str(sub), "Adobe Inc. - Adobe Creative Cloud")
        self.assertEqual(sub.days_until_renewal, 275)
        self.assertFalse(sub.is_expired)
        self.assertEqual(sub.annual_cost, 599.99)

    def test_subscription_cost_center_fk(self):
        """cost_center is a FK to organization.CostCenter; null is the default."""
        sub_no_cc = Subscription.objects.create(
            name="No Cost Center Sub",
            provider=self.provider,
        )
        self.assertIsNone(sub_no_cc.cost_center)

        # Use baker so TenantScopingSoftDeleteManager's slug/unique constraints
        # are handled automatically for the CostCenter.
        cc = baker.make(CostCenter, name="Engineering", code="ENG-001", tenant=None)
        sub_with_cc = Subscription.objects.create(
            name="Engineering Tools",
            provider=self.provider,
            cost_center=cc,
        )
        self.assertEqual(sub_with_cc.cost_center, cc)
        self.assertEqual(sub_with_cc.cost_center.code, "ENG-001")
        # Reverse relation: the subscription should appear in cc.subscriptions
        self.assertIn(sub_with_cc, CostCenter.all_objects.get(pk=cc.pk).subscriptions.all())

    def test_subscription_expired(self):
        sub = Subscription.objects.create(
            name="Expired SaaS",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today - datetime.timedelta(days=1),
            renewal_cost=100,
        )
        self.assertTrue(sub.is_expired)
        self.assertEqual(sub.days_until_renewal, -1)

    def test_subscription_renewing_today(self):
        sub = Subscription.objects.create(
            name="Renewing Today",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today,
        )
        self.assertEqual(sub.days_until_renewal, 0)

    def test_subscription_annual_cost_monthly(self):
        sub = Subscription.objects.create(
            name="Monthly Plan",
            provider=self.provider,
            renewal_cost=49.99,
            billing_cycle=BillingCycleChoices.MONTHLY,
        )
        self.assertEqual(sub.annual_cost, 49.99 * 12)

    def test_subscription_annual_cost_quarterly(self):
        sub = Subscription.objects.create(
            name="Quarterly Plan",
            provider=self.provider,
            renewal_cost=299.99,
            billing_cycle=BillingCycleChoices.QUARTERLY,
        )
        self.assertEqual(sub.annual_cost, 299.99 * 4)

    def test_subscription_annual_cost_biannual(self):
        sub = Subscription.objects.create(
            name="Biannual Plan",
            provider=self.provider,
            renewal_cost=1199.99,
            billing_cycle=BillingCycleChoices.BIANNUAL,
        )
        self.assertEqual(sub.annual_cost, 1199.99 * 2)

    def test_subscription_annual_cost_none_when_no_cost(self):
        sub = Subscription.objects.create(
            name="Free Plan",
            provider=self.provider,
        )
        self.assertIsNone(sub.annual_cost)

    def test_subscription_days_until_renewal_none(self):
        sub = Subscription.objects.create(
            name="No Renewal",
            provider=self.provider,
        )
        self.assertIsNone(sub.days_until_renewal)

    def test_subscription_slug_auto_generation(self):
        sub = Subscription.objects.create(
            name="Adobe Creative Cloud - All Apps",
            provider=self.provider,
        )
        self.assertEqual(sub.slug, "adobe-creative-cloud-all-apps")

    def test_subscription_status_choices(self):
        self.assertEqual(
            list(SubscriptionStatusChoices.values),
            ["active", "suspended", "cancelled", "expired"],
        )

    def test_vendor_contract_auto_renews_is_canonical_with_legacy_model_alias(self):
        field_names = {field.name for field in Subscription._meta.fields}
        self.assertIn("vendor_contract_auto_renews", field_names)
        self.assertNotIn("auto_renewal", field_names)

        sub = Subscription.objects.create(
            name="Declarative Renewal Policy",
            provider=self.provider,
            vendor_contract_auto_renews=False,
        )
        self.assertIs(sub.vendor_contract_auto_renews, False)
        self.assertIs(sub.auto_renewal, False)

        sub.auto_renewal = True
        self.assertIs(sub.vendor_contract_auto_renews, True)

    def test_cancelled_subscription_cannot_be_renewed_back_to_active(self):
        sub = Subscription.objects.create(
            name="Cancelled Contract",
            provider=self.provider,
            status=SubscriptionStatusChoices.CANCELLED,
        )

        with self.assertRaisesMessage(ValidationError, "cancelled to active"):
            sub.renew(timezone.now().date() + datetime.timedelta(days=365))

        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatusChoices.CANCELLED)

    def test_lifecycle_action_retries_are_idempotent(self):
        sub = Subscription.objects.create(name="Retry Contract", provider=self.provider)
        sub.suspend()
        suspended_at = sub.updated_at
        sub.suspend()
        sub.refresh_from_db()
        self.assertEqual(sub.updated_at, suspended_at)

        sub.resume()
        sub.cancel(reason="Retired")
        cancelled_at = sub.updated_at
        notes = sub.notes
        sub.cancel(reason="Retired")
        sub.refresh_from_db()
        self.assertEqual(sub.updated_at, cancelled_at)
        self.assertEqual(sub.notes, notes)

    def test_clean_enforces_transition_matrix_but_allows_same_state_updates(self):
        sub = Subscription.objects.create(name="Clean Contract", provider=self.provider)
        sub.description = "Ordinary update"
        sub.full_clean()

        sub.status = SubscriptionStatusChoices.CANCELLED
        sub.full_clean()
        sub.save(update_fields=["status"])

        sub.status = SubscriptionStatusChoices.ACTIVE
        with self.assertRaisesMessage(ValidationError, "cancelled to active"):
            sub.full_clean()

    def test_clean_covers_every_source_target_pair(self):
        allowed = {
            SubscriptionStatusChoices.ACTIVE: {
                SubscriptionStatusChoices.ACTIVE,
                SubscriptionStatusChoices.SUSPENDED,
                SubscriptionStatusChoices.CANCELLED,
                SubscriptionStatusChoices.EXPIRED,
            },
            SubscriptionStatusChoices.SUSPENDED: {
                SubscriptionStatusChoices.SUSPENDED,
                SubscriptionStatusChoices.ACTIVE,
                SubscriptionStatusChoices.CANCELLED,
                SubscriptionStatusChoices.EXPIRED,
            },
            SubscriptionStatusChoices.EXPIRED: {
                SubscriptionStatusChoices.EXPIRED,
                SubscriptionStatusChoices.ACTIVE,
                SubscriptionStatusChoices.CANCELLED,
            },
            SubscriptionStatusChoices.CANCELLED: {SubscriptionStatusChoices.CANCELLED},
        }
        for source in SubscriptionStatusChoices.values:
            for target in SubscriptionStatusChoices.values:
                with self.subTest(source=source, target=target):
                    sub = Subscription.objects.create(
                        name=f"Matrix {source} {target}", provider=self.provider, status=source
                    )
                    sub.status = target
                    if target in allowed[source]:
                        sub.full_clean()
                    else:
                        with self.assertRaises(ValidationError):
                            sub.full_clean()

    def test_clean_enforces_transition_under_a_foreign_ambient_tenant(self):
        tenant_a = baker.make(Tenant, name="Lifecycle A", slug="lifecycle-a")
        tenant_b = baker.make(Tenant, name="Lifecycle B", slug="lifecycle-b")
        sub = Subscription.objects.create(
            name="Ambient Contract",
            provider=self.provider,
            tenant=tenant_a,
            status=SubscriptionStatusChoices.CANCELLED,
        )
        set_current_tenant(tenant_b)
        try:
            sub.status = SubscriptionStatusChoices.ACTIVE
            with self.assertRaises(ValidationError):
                sub.full_clean()
        finally:
            set_current_tenant(None)

    def test_subscription_billing_cycle_choices(self):
        self.assertEqual(BillingCycleChoices.MONTHLY, "monthly")
        self.assertEqual(BillingCycleChoices.ANNUAL, "annual")
        self.assertEqual(BillingCycleChoices.BIANNUAL, "biannual")

    def test_subscription_tenant(self):
        tg = TenantGroup.objects.create(name="Group", slug="group")
        tenant = Tenant.objects.create(name="Tenant Inc.", slug="tenant-inc", group=tg)
        sub = Subscription.objects.create(
            name="Tenant Sub",
            provider=self.provider,
            tenant=tenant,
        )
        self.assertEqual(sub.tenant, tenant)


class SubscriptionAssignmentModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Microsoft", account_id="ms-001")
        self.subscription = Subscription.objects.create(
            name="M365 E5",
            provider=self.provider,
            licensed_quantity=100,
        )
        self.tg = TenantGroup.objects.create(name="G", slug="g")
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", group=self.tg)
        self.subscription.tenant = self.tenant
        self.subscription.save(update_fields=["tenant"])
        self.site = Site.objects.create(name="Office", slug="office", tenant=self.tenant)
        self.location = Location.objects.create(name="Room 101", slug="room-101", site=self.site, tenant=self.tenant)
        self.asset = Asset.objects.create(
            name="Test Asset",
            asset_tag="TAG-001",
            serial_number="SN-001",
            purchase_cost=100,
            location=self.location,
            tenant=self.tenant,
        )
        self.user = get_user_model().objects.create_user(username="assigner", password="testpass123")

    def test_assignment_to_asset(self):
        ct = ContentType.objects.get_for_model(Asset)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=self.asset.pk,
            assigned_by=self.user,
        )
        self.assertEqual(str(assignment), f"Subscription {self.subscription} -> {self.asset}")

    def test_assignment_unique_constraint(self):
        ct = ContentType.objects.get_for_model(Asset)
        SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=self.asset.pk,
        )
        with self.assertRaises(IntegrityError):
            SubscriptionAssignment.objects.create(
                subscription=self.subscription,
                content_type=ct,
                object_id=self.asset.pk,
            )

    def test_assignment_to_asset_holder(self):
        holder = AssetHolder.objects.create(
            first_name="John", last_name="Doe", upn="john.doe", email="john@test.com", tenant=self.tenant
        )
        ct = ContentType.objects.get_for_model(AssetHolder)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=holder.pk,
        )
        self.assertIn("John Doe", str(assignment))

    def test_assignment_absolute_url_falls_back_to_subscription(self):
        ct = ContentType.objects.get_for_model(Asset)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=self.asset.pk,
        )
        url = assignment.get_absolute_url()
        self.assertIn(str(self.subscription.pk), url)

    def test_assignment_model_rejects_cross_tenant_gfk_target(self):
        foreign_tenant = Tenant.objects.create(name="Foreign", slug="foreign", group=self.tg)
        foreign_asset = baker.make(Asset, tenant=foreign_tenant)
        assignment = SubscriptionAssignment(
            subscription=self.subscription,
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=foreign_asset.pk,
        )

        with self.assertRaisesMessage(ValidationError, "subscription tenant"):
            assignment.full_clean()

    def test_assignment_hides_target_that_later_moves_to_another_tenant(self):
        holder = baker.make(AssetHolder, tenant=self.tenant)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ContentType.objects.get_for_model(AssetHolder),
            object_id=holder.pk,
        )
        self.assertEqual(assignment.assigned_object, holder)
        foreign = Tenant.objects.create(name="Moved", slug="moved", group=self.tg)
        AssetHolder._base_manager.filter(pk=holder.pk).update(tenant=foreign)

        self.assertIsNone(assignment.tenant_safe_assigned_object)
        self.assertIn("unlinked", str(assignment))

    def test_assignment_rejects_and_hides_a_soft_deleted_target(self):
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=self.asset.pk,
        )

        self.asset.delete()

        self.assertIsNone(assignment.tenant_safe_assigned_object)
        self.assertIn("unlinked", str(assignment))
        with self.assertRaisesMessage(ValidationError, "does not exist"):
            assignment.full_clean()


class SubscriptionExplicitExpiryTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Test Provider")
        self.yesterday = timezone.now().date() - datetime.timedelta(days=1)

    def test_save_does_not_silently_expire_and_explicit_action_does(self):
        sub = Subscription.objects.create(
            name="Should Expire",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.yesterday,
        )
        sub.save()
        self.assertEqual(sub.status, SubscriptionStatusChoices.ACTIVE)
        sub.expire()
        self.assertEqual(sub.status, SubscriptionStatusChoices.EXPIRED)

    def test_explicit_action_is_not_required_for_future_renewal(self):
        future = timezone.now().date() + datetime.timedelta(days=30)
        sub = Subscription.objects.create(
            name="Future Renewal",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=future,
        )
        sub.save()
        self.assertEqual(sub.status, SubscriptionStatusChoices.ACTIVE)


class SubscriptionConcurrencyTests(TransactionTestCase):
    def _post_teardown(self):
        super()._post_teardown()
        ContentType.objects.clear_cache()

    def test_stale_direct_writer_cannot_overwrite_a_terminal_cancellation(self):
        provider = Provider.objects.create(name="Race Provider")
        subscription = Subscription.objects.create(
            name="Race Contract", provider=provider, status=SubscriptionStatusChoices.ACTIVE
        )
        stale_loaded = threading.Event()
        allow_stale_save = threading.Event()
        stale_errors = []

        def stale_writer():
            close_old_connections()
            try:
                stale = Subscription._base_manager.get(pk=subscription.pk)
                stale_loaded.set()
                allow_stale_save.wait(timeout=10)
                stale.status = SubscriptionStatusChoices.SUSPENDED
                stale.save()
            except ValidationError as exc:
                stale_errors.append(exc)
            finally:
                close_old_connections()

        thread = threading.Thread(target=stale_writer)
        thread.start()
        self.assertTrue(stale_loaded.wait(timeout=10))

        current = Subscription._base_manager.get(pk=subscription.pk)
        current.cancel(timezone.now().date(), reason="terminal winner")
        allow_stale_save.set()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(stale_errors), 1)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, SubscriptionStatusChoices.CANCELLED)

    def test_stale_retried_cancellation_cannot_replace_the_winning_effects(self):
        provider = Provider.objects.create(name="Cancellation Race Provider")
        subscription = Subscription.objects.create(name="Cancellation Race", provider=provider)
        stale_loaded = threading.Event()
        winner_committed = threading.Event()
        errors = []

        def stale_cancellation():
            close_old_connections()
            stale = Subscription._base_manager.get(pk=subscription.pk)
            stale_loaded.set()
            winner_committed.wait(timeout=10)
            try:
                stale.cancel(datetime.date(2026, 2, 2), "loser")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        thread = threading.Thread(target=stale_cancellation)
        thread.start()
        self.assertTrue(stale_loaded.wait(timeout=10))

        winner = Subscription._base_manager.get(pk=subscription.pk)
        winner.cancel(datetime.date(2026, 1, 1), "winner")
        winner_committed.set()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        subscription.refresh_from_db()
        self.assertEqual(subscription.cancellation_date, datetime.date(2026, 1, 1))
        self.assertIn("winner", subscription.notes)
        self.assertNotIn("loser", subscription.notes)

    def test_stale_direct_same_status_writer_cannot_replace_the_winning_effects(self):
        provider = Provider.objects.create(name="Direct cancellation race provider")
        subscription = Subscription.objects.create(name="Direct cancellation race", provider=provider)
        loaded = threading.Event()
        allow_stale_save = threading.Event()
        errors = []

        def stale_writer():
            close_old_connections()
            stale = Subscription._base_manager.get(pk=subscription.pk)
            stale.status = SubscriptionStatusChoices.CANCELLED
            stale.cancellation_date = datetime.date(2026, 2, 2)
            stale.notes = "loser"
            loaded.set()
            allow_stale_save.wait(timeout=10)
            try:
                stale.save()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        thread = threading.Thread(target=stale_writer)
        thread.start()
        self.assertTrue(loaded.wait(timeout=10))

        winner = Subscription._base_manager.get(pk=subscription.pk)
        winner.cancel(cancellation_date=datetime.date(2026, 1, 1), reason="winner")
        allow_stale_save.set()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, SubscriptionStatusChoices.CANCELLED)
        self.assertEqual(subscription.cancellation_date, datetime.date(2026, 1, 1))
        self.assertIn("winner", subscription.notes)
        self.assertNotIn("loser", subscription.notes)

    def test_concurrent_reminder_delivery_is_idempotent(self):
        from subscriptions.tasks import _notify_once

        user = User.objects.create_user(username="notification-race")
        barrier = threading.Barrier(2)
        errors = []

        def notify():
            close_old_connections()
            thread_user = User._base_manager.get(pk=user.pk)
            try:
                barrier.wait(timeout=10)
                _notify_once(
                    user=thread_user,
                    subject="Renewal reminder",
                    message="Same daily effect",
                    target_url="/subscriptions/1/",
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=notify) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(Notification.objects.filter(user=user, subject="Renewal reminder").count(), 1)
