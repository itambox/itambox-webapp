import datetime

from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, Manufacturer, StatusLabel, Supplier
from core.managers import Scope, set_current_tenant
from core.tasks.context import TaskContext
from licenses.models import License, LicenseSeatAssignment
from organization.models import Tenant
from software.models import InstalledSoftware, Software
from subscriptions.models import Subscription, SubscriptionStatusChoices
from subscriptions.tasks import check_subscription_expiries_and_reminders


class SoftwareLicenseSubscriptionScopingTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Vendor", slug="vendor")
        self.supplier = Supplier.objects.create(name="Reseller", is_active=True)
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.sw_a = Software.objects.create(name="SW A", manufacturer=self.manufacturer, tenant=self.tenant_a)
        self.sw_b = Software.objects.create(name="SW B", manufacturer=self.manufacturer, tenant=self.tenant_b)
        self.sw_global = Software.objects.create(name="SW Global", manufacturer=self.manufacturer, tenant=None)
        self.lic_a = License.objects.create(name="Lic A", software=self.sw_a, seats=3, tenant=self.tenant_a)
        self.lic_b = License.objects.create(name="Lic B", software=self.sw_b, seats=3, tenant=self.tenant_b)
        today = datetime.date.today()
        self.sub_a = Subscription.objects.create(
            name="Sub A",
            supplier=self.supplier,
            tenant=self.tenant_a,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=today - datetime.timedelta(days=5),
        )
        self.sub_b = Subscription.objects.create(
            name="Sub B",
            supplier=self.supplier,
            tenant=self.tenant_b,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=today + datetime.timedelta(days=300),
        )

    def tearDown(self):
        set_current_tenant(None)

    def test_scoped_reads_follow_the_explicit_scope(self):
        set_current_tenant(self.tenant_a)
        scope = Scope.current()
        software = list(Software.objects.for_scope(scope))
        self.assertIn(self.sw_a, software)
        self.assertIn(self.sw_global, software)
        self.assertNotIn(self.sw_b, software)
        self.assertEqual(list(License.objects.for_scope(scope)), [self.lic_a])
        self.assertEqual(list(Subscription.objects.for_scope(scope)), [self.sub_a])

    def test_installs_and_seats_scope_through_parent(self):
        status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        asset_a = baker.make(Asset, asset_tag="A1", status=status, tenant=self.tenant_a)
        asset_b = baker.make(Asset, asset_tag="B1", status=status, tenant=self.tenant_b)
        inst_a = InstalledSoftware.objects.create(asset=asset_a, software=self.sw_a)
        inst_b = InstalledSoftware.objects.create(asset=asset_b, software=self.sw_b)
        seat_a = LicenseSeatAssignment.objects.create(license=self.lic_a, asset=asset_a)
        seat_b = LicenseSeatAssignment.objects.create(license=self.lic_b, asset=asset_b)
        set_current_tenant(self.tenant_a)
        scope = Scope.current()
        self.assertEqual(list(InstalledSoftware.objects.for_scope(scope)), [inst_a])
        self.assertEqual(list(LicenseSeatAssignment.objects.for_scope(scope)), [seat_a])
        self.assertNotIn(inst_b, InstalledSoftware.objects.for_scope(scope))
        self.assertNotIn(seat_b, LicenseSeatAssignment.objects.for_scope(scope))
        self.assertEqual(self.sw_a.installed_count, 1)

    def test_expiry_task_enumerates_every_tenant_under_a_bound_scope(self):
        set_current_tenant(self.tenant_b)
        check_subscription_expiries_and_reminders()
        set_current_tenant(None)
        self.sub_a.refresh_from_db()
        self.sub_b.refresh_from_db()
        self.assertEqual(self.sub_a.status, SubscriptionStatusChoices.EXPIRED)
        self.assertEqual(self.sub_b.status, SubscriptionStatusChoices.ACTIVE)


class ScopedCallSiteCoverageTests(TestCase):
    """Exercise the call sites migrated to explicit scoping (alerts, services, form, GraphQL, counts)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from core.tests.mixins import grant
        from organization.models import Role

        self.manufacturer = Manufacturer.objects.create(name="Vendor2", slug="vendor2")
        self.supplier = Supplier.objects.create(name="Reseller2", is_active=True)
        self.tenant = Tenant.objects.create(name="Tenant C", slug="tenant-c")
        self.other = Tenant.objects.create(name="Tenant D", slug="tenant-d")
        self.today = datetime.date.today()
        self.software = Software.objects.create(name="SW C", manufacturer=self.manufacturer, tenant=self.tenant)
        self.lic = License.objects.create(
            name="Lic C",
            software=self.software,
            seats=1,
            tenant=self.tenant,
            expiration_date=self.today + datetime.timedelta(days=5),
        )
        self.lic_other = License.objects.create(
            name="Lic D",
            software=self.software,
            seats=1,
            tenant=self.tenant,
            expiration_date=self.today + datetime.timedelta(days=5),
        )
        self.sub = Subscription.objects.create(
            name="Sub C",
            supplier=self.supplier,
            tenant=self.tenant,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today + datetime.timedelta(days=5),
        )
        self.user = get_user_model().objects.create_user(username="cov", email="cov@example.com", password="pw")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Cov Role",
            permissions=[
                "software.view_software",
                "licenses.view_license",
                "subscriptions.view_subscription",
            ],
        )
        grant(self.user, self.tenant, role)
        set_current_tenant(self.tenant)

    def tearDown(self):
        set_current_tenant(None)

    def test_alert_matchers_respect_scope(self):
        from types import SimpleNamespace

        from extras.tasks.alerts import _match_license_expiry, _match_renewal_due

        rule = SimpleNamespace(threshold_value=10, tenant=self.tenant)
        self.assertEqual({m["obj"] for m in _match_license_expiry(rule, self.today)}, {self.lic, self.lic_other})
        self.assertEqual([m["obj"] for m in _match_renewal_due(rule, self.today)], [self.sub])
        set_current_tenant(self.other)
        self.assertEqual(_match_license_expiry(rule, self.today), [])
        self.assertEqual(_match_renewal_due(rule, self.today), [])

    def test_transfer_seat_checks_capacity_and_moves(self):
        from django.core.exceptions import ValidationError

        from licenses.services import transfer_license_seat

        status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        a1 = baker.make(Asset, asset_tag="C1", status=status, tenant=self.tenant)
        a2 = baker.make(Asset, asset_tag="C2", status=status, tenant=self.tenant)
        seat = LicenseSeatAssignment.objects.create(license=self.lic, asset=a1)
        LicenseSeatAssignment.objects.create(license=self.lic_other, asset=a2)
        with self.assertRaises(ValidationError):
            transfer_license_seat(seat, self.lic_other)
        free = License.objects.create(name="Free", software=self.software, seats=2, tenant=self.tenant)
        with TaskContext(operation="test.transfer_license_seat"):
            moved = transfer_license_seat(seat, free)
        self.assertEqual(moved.license_id, free.pk)

    def test_seat_form_rejects_duplicate_asset(self):
        from licenses.forms import LicenseSeatAssignmentForm

        status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        asset = baker.make(Asset, asset_tag="C3", status=status, tenant=self.tenant)
        LicenseSeatAssignment.objects.create(license=self.lic, asset=asset)
        form = LicenseSeatAssignmentForm(data={"license": self.lic.pk, "asset": asset.pk})
        self.assertFalse(form.is_valid())

    def test_license_count_is_scoped(self):
        self.assertEqual(self.software.license_count, 2)
        set_current_tenant(self.other)
        self.assertEqual(self.software.license_count, 0)

    def test_graphql_reads_are_scoped(self):
        from django.test import RequestFactory

        from core.schema import schema

        request = RequestFactory().post("/graphql")
        request.user = self.user
        request.active_tenant = self.tenant
        queries = (
            "{ softwareList { id } }",
            '{ software(id: "' + str(self.software.pk) + '") { id } }',
            "{ licenses { id } }",
            '{ license(id: "' + str(self.lic.pk) + '") { id } }',
            '{ subscription(id: "' + str(self.sub.pk) + '") { id } }',
        )
        for query in queries:
            result = schema.execute_sync(query, context_value=request)
            self.assertIsNone(result.errors, query)
            self.assertTrue(any(v for v in result.data.values()), query)
