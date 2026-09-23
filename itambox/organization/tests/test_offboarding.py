"""Offboarding readiness report — the read-only composition service.

``get_offboarding_report`` composes a departing person's outstanding
obligations from every existing capability. These tests pin the service's
contract:

- a clean holder is ``is_clear`` (no outstanding items);
- each obligation class contributes exactly its own item, keyed by a stable
  ``kind`` slug, with a resolvable ``url`` (``reverse()`` only — no request);
- terminal/excluded states (inactive assignments, accepted custody, terminal
  requests, expired reservations, deactivated memberships) are NOT surfaced;
- the report composes only the given holder's obligations (per-holder filter);
- the report never mutates (``user_is_active`` is surfaced, not flipped).

Tenant scoping itself is enforced by the tenant-scoped managers at request
time (the surrounding detail view sets the tenant context); the service's own
filters are per-holder, so they hold regardless of context. The view that
renders the report is covered by the detail-view template tests.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from assets.choices import RequestStatusChoices
from assets.models import (
    Asset,
    AssetAssignment,
    AssetRequest,
    AssetReservation,
    AssetType,
    Category,
    Manufacturer,
    StatusLabel,
)
from assets.models.choices import ReservationStatusChoices
from compliance.models import CustodyReceipt, CustodyTemplate
from core.tests.mixins import TenantTestMixin
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    Component,
    ComponentAllocation,
    Consumable,
    ConsumableAssignment,
)
from inventory.models_assignment_write import authorized_assignment_write
from licenses.models import License, LicenseSeatAssignment, Software
from organization.models import AssetHolder, Membership, Tenant
from organization.services.offboarding import OffboardingReport, get_offboarding_report
from subscriptions.models import (
    BillingCycleChoices,
    Provider,
    Subscription,
    SubscriptionAssignment,
    SubscriptionStatusChoices,
    SubscriptionTypeChoices,
)

User = get_user_model()


class OffboardingReportTests(TestCase):
    """Composition, per-holder filtering, and no-mutation of the report."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="OB Tenant", slug="ob-tenant")
        self.manufacturer = Manufacturer.objects.create(name="OB Mfg", slug="ob-mfg")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="OB ThinkPad",
            slug="ob-thinkpad",
            requestable=True,
        )
        self.status = StatusLabel.objects.create(
            name="OB Deployed",
            slug="ob-deployed",
            type="deployable",
        )
        self.asset = Asset.objects.create(
            name="OB Asset",
            asset_tag="OB-ASSET-001",
            status=self.status,
            tenant=self.tenant,
        )
        self.user = User.objects.create_user(
            username="ob-user",
            email="ob.user@example.com",
            password="password",
        )
        self.holder = AssetHolder.objects.create(
            first_name="Ob",
            last_name="Ringer",
            upn="ob.ringer@ob.example.com",
            tenant=self.tenant,
        )
        # Link the holder to their login user (the report surfaces, not flips,
        # the login state).
        self.holder.user = self.user
        self.holder.save()

    # ------------------------------------------------------------- clean holder
    def test_clean_holder_is_clear(self):
        report = get_offboarding_report(self.holder)
        assert isinstance(report, OffboardingReport)
        assert report.items == []
        assert report.is_clear
        assert report.counts() == {}
        # Login state is surfaced, not mutated.
        assert report.user_is_active is True

    def test_clean_holder_does_not_mutate(self):
        membership = Membership.objects.create(user=self.user, tenant=self.tenant)
        before_user_active = self.user.is_active
        before_membership_active = membership.is_active
        get_offboarding_report(self.holder)
        self.user.refresh_from_db()
        membership.refresh_from_db()
        assert self.user.is_active == before_user_active
        assert membership.is_active == before_membership_active

    # ------------------------------------------------------------- asset assignment
    def test_includes_active_asset_assignment(self):
        AssetAssignment.objects.create(asset=self.asset, assigned_user=self.holder, is_active=True)
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("asset_assignment")
        assert len(items) == 1
        assert items[0].label == "Asset assignment"
        assert items[0].model_label == "assets.AssetAssignment"
        # URL is resolvable without a live request (reverse() only).
        assert items[0].url.startswith("/")

    def test_inactive_asset_assignment_excluded(self):
        AssetAssignment.objects.create(asset=self.asset, assigned_user=self.holder, is_active=False)
        report = get_offboarding_report(self.holder)
        assert report.is_clear

    # ------------------------------------------------------------- accessory
    def test_includes_accessory_assignment(self):
        accessory = Accessory.objects.create(
            name="OB Dock",
            slug="ob-dock",
            manufacturer=self.manufacturer,
            tenant=self.tenant,
        )
        # Inventory assignments are sanctioned-write-only: a bare ORM create is
        # refused at the model layer ("use the authorized checkout service").
        # The checkout service seeds them through the internal
        # ``authorized_assignment_write`` seam, so the test does the same.
        assignment = AccessoryAssignment(accessory=accessory, assigned_holder=self.holder)
        with authorized_assignment_write(assignment):
            assignment.save()
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("accessory_assignment")
        assert len(items) == 1
        assert items[0].model_label == "inventory.AccessoryAssignment"

    # ------------------------------------------------------------- component
    def test_includes_component_allocation(self):
        component = Component.objects.create(
            name="OB RAM",
            manufacturer=self.manufacturer,
            tenant=self.tenant,
        )
        allocation = ComponentAllocation(component=component, assigned_holder=self.holder)
        with authorized_assignment_write(allocation):
            allocation.save()
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("component_allocation")
        assert len(items) == 1
        assert items[0].model_label == "inventory.ComponentAllocation"

    # ------------------------------------------------------------- consumable
    def test_includes_consumable_assignment(self):
        consumable = Consumable.objects.create(
            name="OB Cable",
            manufacturer=self.manufacturer,
            tenant=self.tenant,
        )
        assignment = ConsumableAssignment(consumable=consumable, assigned_holder=self.holder)
        with authorized_assignment_write(assignment):
            assignment.save()
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("consumable_assignment")
        assert len(items) == 1
        assert items[0].model_label == "inventory.ConsumableAssignment"

    # ------------------------------------------------------------- license seat
    def test_includes_license_seat(self):
        software = Software.objects.create(
            name="OB Software",
            manufacturer=self.manufacturer,
            tenant=self.tenant,
        )
        license_ = License.objects.create(
            name="OB License",
            software=software,
            seats=10,
            tenant=self.tenant,
        )
        LicenseSeatAssignment.objects.create(license=license_, assigned_holder=self.holder)
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("license_seat")
        assert len(items) == 1
        assert items[0].model_label == "licenses.LicenseSeatAssignment"

    # ------------------------------------------------------------- custody
    def _make_custody_template(self, suffix):
        category = Category.objects.create(name=f"OB Cat {suffix}", slug=f"ob-cat-{suffix}")
        return CustodyTemplate.objects.create(
            tenant=self.tenant,
            category=category,
            is_active=True,
            require_acceptance=True,
            email_signature_request=True,
            signature_provider="local",
            name=f"OB EULA {suffix}",
            eula_text=f"OB EULA {suffix} terms.",
            disclaimer="Sign here.",
        )

    def test_includes_pending_custody_receipt(self):
        template = self._make_custody_template("a")
        CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            custody_template=template,
            eula_text="OB custody terms.",
        )
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("custody_receipt")
        assert len(items) == 1
        assert items[0].model_label == "compliance.CustodyReceipt"

    def test_accepted_custody_receipt_excluded(self):
        template = self._make_custody_template("b")
        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            custody_template=template,
            eula_text="OB custody 2 terms.",
        )
        # Accept the receipt: no longer an outstanding obligation.
        receipt.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
        receipt.save()
        report = get_offboarding_report(self.holder)
        assert report.is_clear

    def test_custody_items_hidden_for_viewers_without_receipt_permission(self):
        template = self._make_custody_template("c")
        CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            custody_template=template,
            eula_text="OB custody c terms.",
        )
        # The view passes the viewer's compliance.view_custodyreceipt result;
        # without it the custody items are hidden exactly like the custody
        # surfaces on the same page, while the full report keeps them.
        restricted = get_offboarding_report(self.holder, include_custody=False)
        assert restricted.for_kind("custody_receipt") == []
        assert restricted.is_clear
        full = get_offboarding_report(self.holder)
        assert len(full.for_kind("custody_receipt")) == 1

    # ------------------------------------------------------------- asset request
    def test_includes_open_request_as_requester(self):
        AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.APPROVED,
        )
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("asset_request")
        assert len(items) == 1
        assert items[0].model_label == "assets.AssetRequest"
        assert "requester" in items[0].description

    def test_includes_open_request_as_assigned_user(self):
        # A request the person is the *assignee* of (requester is someone else).
        other_user = User.objects.create_user(username="ob-other", password="x")
        AssetRequest.objects.create(
            tenant=self.tenant,
            requester=other_user,
            assigned_user=self.holder,
            asset_type=self.asset_type,
            status=RequestStatusChoices.PENDING,
        )
        report = get_offboarding_report(self.holder)
        items = report.for_kind("asset_request")
        assert len(items) == 1
        assert "assigned user" in items[0].description

    def test_terminal_request_excluded(self):
        AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.FULFILLED,
        )
        report = get_offboarding_report(self.holder)
        assert report.is_clear

    # ------------------------------------------------------------- reservation
    def test_includes_open_reservation(self):
        today = date.today()
        AssetReservation.objects.create(
            asset=self.asset,
            reserved_for=self.holder,
            start_date=today,
            end_date=today + timedelta(days=7),
            status=ReservationStatusChoices.ACTIVE,
        )
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("asset_reservation")
        assert len(items) == 1
        assert items[0].model_label == "assets.AssetReservation"

    def test_past_reservation_excluded(self):
        today = date.today()
        AssetReservation.objects.create(
            asset=self.asset,
            reserved_for=self.holder,
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1),
            status=ReservationStatusChoices.ACTIVE,
        )
        report = get_offboarding_report(self.holder)
        assert report.is_clear

    # ------------------------------------------------------------- subscription
    def test_includes_subscription_assignment(self):
        provider = Provider.objects.create(name="OB Provider", slug="ob-provider")
        subscription = Subscription.objects.create(
            name="OB Subscription",
            provider=provider,
            tenant=self.tenant,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=99.99,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
            licensed_quantity=10,
        )
        holder_ct = ContentType.objects.get(app_label="organization", model="assetholder")
        SubscriptionAssignment.objects.create(
            subscription=subscription,
            content_type=holder_ct,
            object_id=self.holder.pk,
        )
        report = get_offboarding_report(self.holder)
        assert not report.is_clear
        items = report.for_kind("subscription")
        assert len(items) == 1
        assert items[0].model_label == "subscriptions.SubscriptionAssignment"

    # ------------------------------------------------------------- membership
    def test_includes_active_membership(self):
        Membership.objects.create(user=self.user, tenant=self.tenant)
        report = get_offboarding_report(self.holder)
        items = report.for_kind("membership")
        assert len(items) == 1
        assert items[0].model_label == "organization.Membership"

    def test_deactivated_membership_excluded(self):
        Membership.objects.create(user=self.user, tenant=self.tenant, is_active=False)
        report = get_offboarding_report(self.holder)
        assert report.is_clear

    # ------------------------------------------------------------- login state
    def test_user_is_active_reflects_login_state(self):
        self.user.is_active = False
        self.user.save()
        report = get_offboarding_report(self.holder)
        assert report.user_is_active is False

    # ------------------------------------------------------------- completeness
    EXPECTED_KINDS = (
        "asset_assignment",
        "accessory_assignment",
        "component_allocation",
        "consumable_assignment",
        "license_seat",
        "custody_receipt",
        "asset_request",
        "asset_reservation",
        "subscription",
        "membership",
    )

    def test_fixture_person_carrying_one_obligation_of_each_class(self):
        # One departing person who holds an outstanding obligation of EVERY
        # supported class. The report must surface exactly one item per kind —
        # a completeness assertion that the composition reaches all classes.
        self._seed_one_obligation_of_each_class()
        report = get_offboarding_report(self.holder)

        counts = report.counts()
        # Exactly one of each class, no more, no fewer.
        assert counts == {kind: 1 for kind in self.EXPECTED_KINDS}
        assert not report.is_clear
        assert len(report.items) == len(self.EXPECTED_KINDS)
        for kind in self.EXPECTED_KINDS:
            items = report.for_kind(kind)
            assert len(items) == 1
            assert items[0].model_label
            # Every item resolves a real URL (reverse() only, no live request).
            assert items[0].url.startswith("/")
        # Surfacing the login state is read-only: the person is still active.
        assert report.user_is_active is True

    def _seed_one_obligation_of_each_class(self):
        # asset assignment
        AssetAssignment.objects.create(asset=self.asset, assigned_user=self.holder, is_active=True)
        # accessory assignment (sanctioned-write-only inventory model)
        accessory = Accessory.objects.create(
            name="CB Dock", slug="cb-dock", manufacturer=self.manufacturer, tenant=self.tenant
        )
        acc = AccessoryAssignment(accessory=accessory, assigned_holder=self.holder)
        with authorized_assignment_write(acc):
            acc.save()
        # component allocation
        component = Component.objects.create(name="CB RAM", manufacturer=self.manufacturer, tenant=self.tenant)
        comp = ComponentAllocation(component=component, assigned_holder=self.holder)
        with authorized_assignment_write(comp):
            comp.save()
        # consumable assignment
        consumable = Consumable.objects.create(name="CB Cable", manufacturer=self.manufacturer, tenant=self.tenant)
        cons = ConsumableAssignment(consumable=consumable, assigned_holder=self.holder)
        with authorized_assignment_write(cons):
            cons.save()
        # license seat
        software = Software.objects.create(name="CB Software", manufacturer=self.manufacturer, tenant=self.tenant)
        license_ = License.objects.create(name="CB License", software=software, seats=10, tenant=self.tenant)
        LicenseSeatAssignment.objects.create(license=license_, assigned_holder=self.holder)
        # custody receipt (pending)
        template = self._make_custody_template("cb")
        CustodyReceipt.objects.create(
            asset=self.asset,
            holder=self.holder,
            custody_template=template,
            eula_text="CB custody terms.",
        )
        # asset request (the person is the requester)
        AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.APPROVED,
        )
        # reservation (open / future)
        today = date.today()
        AssetReservation.objects.create(
            asset=self.asset,
            reserved_for=self.holder,
            start_date=today,
            end_date=today + timedelta(days=7),
            status=ReservationStatusChoices.ACTIVE,
        )
        # subscription assignment
        provider = Provider.objects.create(name="CB Provider", slug="cb-provider")
        subscription = Subscription.objects.create(
            name="CB Subscription",
            provider=provider,
            tenant=self.tenant,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=49.99,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
            licensed_quantity=5,
        )
        holder_ct = ContentType.objects.get(app_label="organization", model="assetholder")
        SubscriptionAssignment.objects.create(
            subscription=subscription,
            content_type=holder_ct,
            object_id=self.holder.pk,
        )
        # membership (active)
        Membership.objects.create(user=self.user, tenant=self.tenant)

    def test_counts_aggregate_by_kind(self):
        AssetAssignment.objects.create(asset=self.asset, assigned_user=self.holder, is_active=True)
        Membership.objects.create(user=self.user, tenant=self.tenant)
        report = get_offboarding_report(self.holder)
        assert report.counts() == {"asset_assignment": 1, "membership": 1}

    # ------------------------------------------------------------- per-holder filter
    def test_only_this_holders_obligations_composed(self):
        # A different holder's active assignment must not leak into this
        # holder's report — the service composes per concrete holder.
        other_holder = AssetHolder.objects.create(
            first_name="Other",
            last_name="Holder",
            upn="other@other.example.com",
            tenant=self.tenant,
        )
        AssetAssignment.objects.create(asset=self.asset, assigned_user=other_holder, is_active=True)
        report = get_offboarding_report(self.holder)
        assert report.is_clear


class OffboardingReportTenantScopingTests(TenantTestMixin, TestCase):
    """Tenant scoping: a report never composes another tenant's obligations.

    The surrounding detail view runs inside the active tenant context; the
    report inherits that scoping from the tenant-scoped managers (the same
    contextvar the view set), so obligations that belong to another tenant
    are never surfaced, and the active tenant's own obligations are listed
    in full.
    """

    def setUp(self):
        self.setup_tenant_context(name="OB Alpha", slug="ob-alpha")
        self.tenant_b = Tenant.objects.create(name="OB Beta", slug="ob-beta")

        self.status = StatusLabel.objects.create(name="OB TS Deployed", slug="ob-ts-deployed", type="deployable")
        self.asset_a = Asset.objects.create(
            name="OB Alpha Asset", asset_tag="OB-ALPHA-1", status=self.status, tenant=self.tenant
        )
        self.holder_a = AssetHolder.objects.create(
            first_name="Alpha", last_name="Holder", upn="alpha.holder@ob.example.com", tenant=self.tenant
        )
        self.assignment_a = AssetAssignment.objects.create(
            asset=self.asset_a, assigned_user=self.holder_a, is_active=True
        )

        self.asset_b = Asset.objects.create(
            name="OB Beta Asset", asset_tag="OB-BETA-1", status=self.status, tenant=self.tenant_b
        )
        self.holder_b = AssetHolder.objects.create(
            first_name="Beta", last_name="Holder", upn="beta.holder@ob.example.com", tenant=self.tenant_b
        )
        self.assignment_b = AssetAssignment.objects.create(
            asset=self.asset_b, assigned_user=self.holder_b, is_active=True
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_single_tenant_scope_lists_all_of_this_tenants_obligations(self):
        self.set_active_tenant(self.tenant)
        report = get_offboarding_report(self.holder_a)
        items = report.for_kind("asset_assignment")
        assert len(items) == 1
        assert items[0].object_pk == self.assignment_a.pk
        assert not report.is_clear

    def test_other_tenants_obligations_do_not_leak_into_the_report(self):
        self.set_active_tenant(self.tenant)
        report = get_offboarding_report(self.holder_b)
        assert report.for_kind("asset_assignment") == []

    def test_each_tenant_context_sees_only_its_own_holders_obligations(self):
        self.set_active_tenant(self.tenant_b)
        report = get_offboarding_report(self.holder_b)
        items = report.for_kind("asset_assignment")
        assert len(items) == 1
        assert items[0].object_pk == self.assignment_b.pk
